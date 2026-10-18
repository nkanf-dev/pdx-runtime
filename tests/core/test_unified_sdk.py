"""CPU-only one-DLL SDK resolution/ownership; no PID, DLL execution or build."""
import struct
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from pdx_runtime import native_bridge as native, v3_runtime as runtime, v3_data as data
from pdx_runtime.session import RuntimeSession


def image(exports):
    names=list(exports); raw=bytearray(0x20000)
    raw[:2]=b"MZ";struct.pack_into("<I",raw,60,64)
    raw[64:68]=b"PE\0\0";struct.pack_into("<H",raw,68,0x8664)
    struct.pack_into("<H",raw,88,0x20B)
    start=0x400;functions=0x440;strings_table=functions+len(names)*4
    ordinals=strings_table+len(names)*4;cursor=ordinals+len(names)*2
    struct.pack_into("<6I",raw,start+16,1,len(names),len(names),functions,strings_table,ordinals)
    addresses={}
    for i,name in enumerate(names):
        encoded=name.encode()+b"\0"
        struct.pack_into("<I",raw,strings_table+i*4,cursor)
        struct.pack_into("<H",raw,ordinals+i*2,i)
        raw[cursor:cursor+len(encoded)]=encoded;cursor+=len(encoded)
    for i,name in enumerate(names):
        target=exports[name]
        if isinstance(target,str):
            encoded=target.encode()+b"\0";rva=cursor
            raw[cursor:cursor+len(encoded)]=encoded;cursor+=len(encoded)
        else:rva=0x2000+i*0x200
        addresses[name]=rva;struct.pack_into("<I",raw,functions+i*4,rva)
    struct.pack_into("<II",raw,64+24+112,start,cursor-start+8)
    return raw,addresses


class Thread:
    def __init__(self,action):self.action=action;self.closes=0;self.waits=0
    def wait(self,timeout_ms=None):self.waits+=1;self.action();return 0
    def poll(self):return 0
    def close(self):self.closes+=1


class Process(native.RemoteProcess):
    def __init__(self):
        self.pid=123;self.loaded=[];self.memory={};self.addresses={};self.buffers={}
        self.frees=[];self.loads=[];self.threads=[];self.next_buffer=0x10000000
        self.bound=False;self.session=0xA00000;self.base=0x180000000
    def add(self,name,base,exports):
        raw,rvas=image(exports)
        self.loaded.append({"name":name,"path":"C:/owned/"+name,"base":base,"size":len(raw)})
        self.memory[base]=raw
        for symbol,rva in rvas.items():self.addresses[(base,symbol)]=base+rva
    def modules(self):return list(self.loaded)
    def read(self,address,size):
        for base,raw in {**self.memory,**self.buffers}.items():
            if base<=address and address+size<=base+len(raw):return bytes(raw[address-base:address-base+size])
        raise native.BridgeError("ReadProcessMemory",299,address=address)
    def allocate(self,size):
        address=self.next_buffer;self.next_buffer+=0x10000;self.buffers[address]=bytearray(size);return address
    def write(self,address,raw):
        for base,buffer in {**self.memory,**self.buffers}.items():
            if base<=address and address+len(raw)<=base+len(buffer):
                buffer[address-base:address-base+len(raw)]=raw;return len(raw)
        raise AssertionError("unmapped fixture write")
    def free(self,address):self.frees.append(address);self.buffers.pop(address,None)
    def load_library(self,*args,**kwargs):self.loads.append((args,kwargs));raise AssertionError("resident monolith must not reload")
    def session_state(self,bound):
        self.bound=bound;session=self.session if bound else 0
        state=runtime.SESSION_HOOK_PREFIX.pack(152,int(bound),0,0,session+0xB8 if bound else 0,
            0x7FF103386F10 if bound else 0,self.base+0x10000 if bound else 0,session,
            self.addresses[(self.base,"PdxV3DrainConstruction")] if bound else 0,0,0,0,0,
            0x7FF100000000 if bound else 0,self.base if bound else 0)
        self.write(self.addresses[(self.base,"PdxV3SessionHookState")],state)
        memory=self.buffers.setdefault(self.session,bytearray(0x400))
        struct.pack_into("<Q",memory,0xB8,self.base+0x10000 if bound else 0x7FF103386F10)
        struct.pack_into("<Q",memory,0xC0,self.session)
        struct.pack_into("<Q",memory,0x3D8,0xA10000)
        struct.pack_into("<Q",memory,0xD0,0xA20000)
        memory[0x3F0]=1;memory[0xC8]=0
    def start_thread(self,address,argument=0):
        self.assertion=(address,argument)
        thread=Thread(lambda:self.session_state(True));self.threads.append(thread);return thread


class UnifiedSDKTests(unittest.TestCase):
    def _wire_request_fixture(self, command):
        """CPU transport: preserve raw responses through exact V3 wire commands."""
        if command == "world-status":
            return self.world_status()
        words = command.split()
        if len(words) == 3 and words[0] == "interface-callback":
            callback, address = int(words[1], 0), int(words[2], 0)
            self.assertEqual(command, f"interface-callback {callback:#x} {address:#x}")
            return self.interface_callback(callback, address)
        if command == "hook-install":
            return self.install_interface_hook()
        raise AssertionError(f"unexpected V3 fixture wire command: {command!r}")

    def setUp(self):
        self.process=Process()
        exports={symbol:None for filename,symbol in runtime.ARTIFACTS.values()}
        exports.update({name:None for name in ("SharedEntry","PdxRuntimeInitialize","PdxV3DrainConstruction",
                        "PdxV3SessionHookState","PdxV3DataInterface","PdxNativeCallInterface")})
        self.process.add(native.RUNTIME_DLL,self.process.base,exports)
        self.client=Mock(spec_set=["pid", "request", "status"], pid=123)
        self.world_status = Mock()
        self.interface_callback = Mock()
        self.install_interface_hook = Mock()
        self.client.request.side_effect = self._wire_request_fixture
        self.client.status.return_value={"status":"ok","dll_base":hex(self.process.base)}
        self.world_status.return_value={"status":"ok","installed":0,"world":"0x0","completed_request_id":7}
        self.interface_callback.return_value={"status":"ok", "error": 0,"request_id":7}
        def interface():
            self.world_status.return_value={"status":"ok","installed":1,"world":"0x0","completed_request_id":7}
            return self.world_status.return_value
        self.install_interface_hook.side_effect=interface
        self.process.session_state(False)


    def test_default_install_uses_one_resident_module_for_interface_session_reuse(self):
        first=runtime.install(self.client,self.process,stage="interface")
        self.assertEqual(first["status"],"ready")
        self.assertTrue(all(module["base"]==self.process.base for module in first["modules"].values()))
        self.world_status.return_value={"status":"ok","installed":1,"world":"0x800000","completed_request_id":7}
        second=runtime.install(self.client,self.process,stage="session")
        self.assertEqual(second["status"],"ready")
        third=runtime.install(self.client,self.process)
        self.assertEqual(third["status"],"ready")
        self.assertEqual(self.process.loads,[])
        self.assertEqual(len(self.process.threads),1)
        self.install_interface_hook.assert_called_once()

    def test_data_handle_release_keeps_actual_owner_if_split_appears_later(self):
        self.world_status.return_value={"status":"ok","completed_request_id":7}
        query=data.snapshot(self.client,42,process=self.process)
        self.assertEqual(query.adapter,self.process.base)
        initial=bytes(self.process.buffers[query.result_address])
        fields=list(data.REQUEST.unpack_from(initial));fields[1]=0
        self.process.write(query.result_address,data.REQUEST.pack(*fields)+initial[data.REQUEST.size:])
        self.assertEqual(query.read().value_handle,42)
        self.process.add("pdx_v3_data.dll",0x190000000,{"PdxV3DataInterface":None})
        released=query.release_value(process=self.process)
        self.assertEqual(released.adapter,self.process.base)
        self.assertEqual(released.callback_address,self.process.addresses[(self.process.base,"PdxV3DataInterface")])
        self.assertEqual(self.interface_callback.call_count,2)

    def test_unknown_query_preserves_original_handle_owner_and_storage(self):
        error=native.BridgeError("ReadFile(pipe)",109)
        self.interface_callback.side_effect=error
        with self.assertRaises(native.BridgeError) as caught:data.snapshot(self.client,42,process=self.process)
        self.assertIs(caught.exception,error)
        self.assertEqual(error.query.adapter,self.process.base)
        self.assertIs(error.query.process,self.process)
        self.assertIn(error.query.result_address,self.process.buffers)
        self.assertEqual(self.process.frees,[])
        self.interface_callback.assert_called_once()


class PackagedLoadTests(unittest.TestCase):
    def test_session_default_and_explicit_path_keep_native_client(self):
        client=Mock()
        with patch.object(native.NativeBridge,"load",return_value=client) as load:
            session=RuntimeSession.load(123)
            self.assertIs(session.native,client);load.assert_called_once_with(123)
        with patch.object(native.NativeBridge,"load",return_value=client) as load:
            RuntimeSession.load(123,"development.dll")
            load.assert_called_once_with(123,"development.dll")

    def test_bridge_default_path_is_package_native_dll_not_cwd(self):
        process=Mock();process.__enter__=Mock(return_value=process);process.__exit__=Mock(return_value=False)
        process.load_library.return_value={"name":"pdx_runtime.dll","base":0x180000000}
        thread=Mock();thread.__enter__=Mock(return_value=thread);thread.__exit__=Mock(return_value=False)
        thread.wait.return_value=0;process.start_thread.return_value=thread
        client=Mock()
        expected=Path(native.__file__).resolve().parent/"_native/pdx_runtime.dll"
        with patch.object(native,"RemoteProcess",return_value=process),patch.object(native.NativeBridge,"connect",return_value=client):
            self.assertIs(native.NativeBridge.load(123),client)
        process.load_library.assert_called_once_with(expected)
        self.assertEqual(client.load_info["base"],0x180000000)

    def test_default_load_pending_preserves_original_attempt_without_replay(self):
        attempt=Mock(path=Path("pdx_runtime.dll"),state="waiting",argument_address=0x1200,
                     loader_exit_dword=None,last_error=None)
        error=native.LibraryLoadPending(attempt)
        process=Mock();process.__enter__=Mock(return_value=process);process.__exit__=Mock(return_value=False)
        process.load_library.side_effect=error
        with patch.object(native,"RemoteProcess",return_value=process):
            with self.assertRaises(native.LibraryLoadPending) as caught:native.NativeBridge.load(123)
        self.assertIs(caught.exception,error);self.assertIs(error.attempt,attempt)
        process.load_library.assert_called_once();process.start_thread.assert_not_called()
        attempt.resume.assert_not_called()


if __name__=="__main__":unittest.main()
