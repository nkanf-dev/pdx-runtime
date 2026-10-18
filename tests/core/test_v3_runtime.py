"""Focused CPU recovery paths; no process, DLL execution or native build."""
from pathlib import Path
import os
import struct
import tempfile
import unittest
from unittest.mock import Mock
from pdx_runtime import v3_runtime as runtime
from pdx_runtime.native_bridge import BridgeError


def dll(symbols, machine=0x8664, trailer=b""):
    data=bytearray(0xC00)
    data[:2]=b"MZ"; struct.pack_into("<I",data,60,0x80)
    data[0x80:0x84]=b"PE\0\0"
    struct.pack_into("<HH",data,0x84,machine,2)
    struct.pack_into("<H",data,0x94,240)
    struct.pack_into("<H",data,0x98,0x20B)
    struct.pack_into("<II",data,0x98+112,0x2000,0x700)
    for i,(rva,raw,size) in enumerate(((0x1000,0x200,0x200),(0x2000,0x400,0x800))):
        struct.pack_into("<IIII",data,0x188+i*40+8,size,rva,size,raw)
    struct.pack_into("<6I",data,0x410,1,len(symbols),len(symbols),0x2040,0x2080,0x20C0)
    cursor=0x2200
    for i,symbol in enumerate(symbols):
        struct.pack_into("<I",data,0x440+i*4,0x1100+i*16)
        struct.pack_into("<I",data,0x480+i*4,cursor)
        struct.pack_into("<H",data,0x4C0+i*2,i)
        raw=symbol.encode()+b"\0"; offset=cursor-0x2000+0x400
        data[offset:offset+len(raw)]=raw; cursor+=len(raw)
    return bytes(data)+trailer


class Thread:
    def __init__(self,action,error=None,code=0):
        self.action,self.error,self.code=action,error,code
        self.ready=error is None; self.completed=False; self.closes=0; self.waits=0; self.polls=0
    def finish(self):
        if not self.completed:
            self.action(); self.completed=True
        return self.code
    def wait(self,timeout_ms=None):
        self.waits+=1
        if self.error:raise self.error
        return self.finish()
    def poll(self):
        self.polls+=1
        return self.finish() if self.ready else None
    def close(self):self.closes+=1


class Process:
    pid=123
    def __init__(self,directory,definitions):
        self.directory,self.definitions=directory,definitions
        self.loaded=[]; self.exports={}; self.loads=[]; self.threads=[]; self.frees=[]
        self.installed=False; self.current=0x90000; self.install_error=0; self.wait_error=None
        self.load_unknown={}; self.read_failures=0; self.state_by_base={}
        self.add("pdx_runtime_bridge.dll",0x10000,["PdxV3DrainConstruction"],path="carrier.dll")
    def add(self,name,base,symbols,path=None):
        module={"name":name,"base":base,"path":str(path or self.directory/name)}
        self.loaded.append(module)
        self.exports[base]={symbol:base+(0x200 if symbol=="PdxV3SessionHookState" else 0x100) for symbol in symbols}
        return module
    def modules(self):return list(self.loaded)
    def resolve_export(self,base,symbol,*,aliases=None):
        assert aliases is None, "installer resolves already-selected bases without a default alias policy"
        try:return self.exports[base][symbol]
        except KeyError:raise BridgeError("resolve_export",reason="export not found",symbol=symbol)
    def load_library(self,path,*,timeout_ms=None):
        assert timeout_ms==0, "installer must not wait indefinitely for a slow DLL"
        self.loads.append(path)
        module=self.add(path.name,0x20000+len(self.loads)*0x10000,self.definitions[path.name],path)
        if path.name in self.load_unknown:
            error=self.load_unknown.pop(path.name)
            thread=Thread(lambda:None,error)
            process=self
            class Attempt:
                def __init__(self):
                    self.thread=thread; self.argument_address=0xF000+len(process.loads)
                    self.process=process; self.path=path
                def resume(self):
                    if self.thread.poll() is None:
                        raise error
                    self.thread.close(); process.free(self.argument_address)
                    return module
            error.attempt=Attempt()
            raise error
        return module
    def read(self,address,size):
        if self.read_failures:
            self.read_failures-=1
            raise BridgeError("ReadProcessMemory",299)
        if size==8:return self.current.to_bytes(8,"little")
        override=next((self.state_by_base[m["base"]] for m in self.loaded
                       if address==m["base"]+0x200 and m["base"] in self.state_by_base),self.installed)
        if isinstance(override,Exception):raise override
        installed=override.get("installed",self.installed) if isinstance(override,dict) else override
        values=[160,int(installed),0,0,0x80000,0x81000,0x90000,
                0x82000,0x10100,0,0,0,0,0x7FF100000000,0x10000]
        if isinstance(override,dict):
            for key,value in override.items():values[runtime.SESSION_HOOK_FIELDS.index(key)]=value
        return runtime.SESSION_HOOK_PREFIX.pack(*values)
    def start_thread(self,address,argument):
        def action():
            self.installed=self.install_error in (0,183)
            for base,exports in self.exports.items():
                if exports.get("PdxV3SessionHookInstall")==address and base in self.state_by_base:
                    self.state_by_base[base]=self.installed
        thread=Thread(action,self.wait_error,self.install_error)
        self.threads.append(thread)
        return thread
    def free(self,address):self.frees.append(address)


class RecoveryTests(unittest.TestCase):
    def wire_request(self, command):
        if command == "world-status":
            return self.world_status()
        if command == "hook-install":
            return self.install_interface_hook()
        raise AssertionError(f"unexpected runtime wire command: {command!r}")

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.definitions={}
        for role,(filename,symbol) in runtime.ARTIFACTS.items():
            self.definitions[filename]=[symbol]+(["PdxV3SessionHookState"] if role=="session" else [])
            (self.directory/filename).write_bytes(dll(self.definitions[filename]))
        self.process=Process(self.directory,self.definitions)
        self.client=Mock(spec_set=("pid", "request", "status"))
        self.client.pid=123
        self.client.status.return_value={"status":"ok","dll_base":"0x10000"}
        self.world_status=Mock(return_value={"status":"ok","installed":0,"world":"0x800000"})
        self.install_interface_hook=Mock()
        self.client.request.side_effect=lambda command: RecoveryTests.wire_request(self, command)
        def install():
            self.world_status.return_value={"status":"ok","installed":1,"world":"0x800000"}
            return self.world_status.return_value
        self.install_interface_hook.side_effect=install
    def install(self,**kwargs):return runtime.install(self.client,self.process,self.directory,**kwargs)
    def alias(self,role,name="renamed.dll",**kwargs):
        filename,symbol=runtime.ARTIFACTS[role]
        symbols=self.definitions[filename]
        self.definitions[name]=symbols
        (self.directory/name).write_bytes(dll(symbols,**kwargs))
        return name

    def test_stale_hint_rediscovery_uses_actual_renamed_resident(self):
        name=self.alias("query")
        module=self.process.add(name,0xA0000,self.definitions[name])
        (self.directory/runtime.ARTIFACTS["query"][0]).unlink()
        result=self.install(modules={"query":0xDEADBEEF})
        self.assertEqual(result["status"],"ready")
        self.assertEqual(result["modules"]["query"]["base"],module["base"])
        self.assertEqual(len(self.process.loads),4)

    def test_missing_default_callback_finds_compatible_directory_alias(self):
        filename=runtime.ARTIFACTS["query"][0]
        (self.directory/filename).write_bytes(dll(["UnrelatedExport"]))
        self.alias("query","alternate-query.dll")
        result=self.install()
        self.assertEqual(result["status"],"ready")
        self.assertEqual(result["modules"]["query"]["name"],"alternate-query.dll")
        self.assertNotIn(filename,[path.name for path in self.process.loads])

    def test_wrong_machine_primary_recovers_using_compatible_alias(self):
        filename=runtime.ARTIFACTS["query"][0]
        (self.directory/filename).write_bytes(dll(self.definitions[filename],machine=0x14C))
        self.alias("query","compatible-query.dll")
        result=self.install()
        self.assertEqual(result["status"],"ready")
        self.assertEqual(result["modules"]["query"]["name"],"compatible-query.dll")

    def test_all_in_menu_keeps_interface_then_resumes_session_in_world(self):
        self.world_status.return_value={"status":"ok","installed":0,"world":"0x0"}
        self.install_interface_hook.side_effect=lambda:{"status":"ok","installed":1,"world":"0x0"}
        first=self.install()
        self.assertEqual(first["stages"],{"interface":"ready","session":"deferred"})
        self.assertEqual(self.process.threads,[])
        bases={role:module["base"] for role,module in first["modules"].items()}
        self.world_status.return_value={"status":"ok","installed":1,"world":"0x800000"}
        second=self.install()
        self.assertEqual(second["status"],"ready")
        self.assertEqual({role:module["base"] for role,module in second["modules"].items()},bases)
        self.assertEqual(len(self.process.loads),5)
        self.assertEqual(len(self.process.threads),1)
        self.install_interface_hook.assert_called_once()

    def test_transient_session_read_recovers_without_player_action(self):
        self.process.read_failures=1
        result=self.install()
        self.assertEqual(result["status"],"ready")
        self.assertTrue(any(isinstance(item.get("exception"),BridgeError) for item in result["diagnostics"]))
        self.assertEqual(len(self.process.threads),1)

    def test_missing_local_role_does_not_block_hooks_and_later_role_resumes(self):
        filename=runtime.ARTIFACTS["query"][0]
        (self.directory/filename).unlink()
        first=self.install()
        self.assertEqual(first["status"],"partial")
        self.assertEqual(first["stages"],{"interface":"ready","session":"ready"})
        (self.directory/filename).write_bytes(dll(self.definitions[filename]))
        second=self.install()
        self.assertEqual(second["status"],"ready")
        self.assertEqual(len(self.process.loads),5)
        self.assertEqual(len(self.process.threads),1)
        self.install_interface_hook.assert_called_once()

    def test_unknown_session_delivery_retains_original_thread_and_resumes_without_replay(self):
        error=BridgeError("WaitForSingleObject",258)
        self.process.wait_error=error
        first=self.install()
        self.assertEqual(first["stages"]["session"],"pending")
        original=self.process.threads[0]
        self.assertIs(first["journal"]["session"]["thread"],original)
        self.assertEqual(original.closes,0)
        original.ready=True
        second=self.install()
        self.assertEqual(second["status"],"ready")
        self.assertEqual(len(self.process.threads),1)
        self.assertEqual(original.waits,1)
        self.assertEqual(original.closes,1)
        self.assertFalse(second["pending"])

    def test_usable_hook_does_not_hide_original_thread_pending_lifecycle(self):
        self.process.wait_error=BridgeError("WaitForSingleObject",258)
        first=self.install()
        original=self.process.threads[0]
        self.assertEqual(first["status"],"pending")
        self.process.installed=True  # Hook effect is visible before thread signal.
        second=self.install()
        self.assertEqual(second["stages"]["session"],"ready")
        self.assertEqual(second["status"],"pending")
        self.assertIs(second["pending"][0]["thread"],original)
        self.assertEqual(original.closes,0)
        original.ready=True
        third=self.install()
        self.assertEqual(third["stages"]["session"],"ready")
        self.assertEqual(third["status"],"ready")
        self.assertEqual(third["pending"],[])
        self.assertEqual(len(self.process.threads),1)
        self.assertEqual(original.waits,1)
        self.assertEqual(original.closes,1)

    def test_unknown_loader_module_visibility_is_not_completion_and_other_roles_work(self):
        filename=runtime.ARTIFACTS["query"][0]
        error=BridgeError("WaitForSingleObject",258)
        self.process.load_unknown[filename]=error
        first=self.install()
        self.assertNotIn("query",first["modules"])
        self.assertEqual(first["stages"],{"interface":"ready","session":"ready"})
        owner=first["journal"]["loads"][runtime._path_key((self.directory/filename).resolve())]["attempt"]
        self.assertTrue(any(m["name"]==filename for m in self.process.loaded))
        second=self.install()
        self.assertNotIn("query",second["modules"])
        self.assertEqual([path.name for path in self.process.loads].count(filename),1)
        self.assertEqual(owner.thread.closes,0)
        owner.thread.ready=True
        third=self.install()
        self.assertEqual(third["status"],"ready")
        self.assertEqual([path.name for path in self.process.loads].count(filename),1)
        self.assertEqual(owner.thread.closes,1)
        self.assertEqual(self.process.frees,[0xF001])

    @unittest.skipUnless(os.name == "nt", "real Windows short/long path regression")
    def test_unknown_loader_short_and_long_paths_keep_the_original_attempt(self):
        import ctypes
        directory=self.directory/"Runtime Long Artifact Directory"
        directory.mkdir()
        for filename,symbols in self.definitions.items():
            (directory/filename).write_bytes(dll(symbols))
        directory=directory.resolve()
        get_short_path=ctypes.WinDLL("kernel32",use_last_error=True).GetShortPathNameW
        get_short_path.argtypes=(ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint32)
        get_short_path.restype=ctypes.c_uint32
        required=get_short_path(str(directory),None,0)
        self.assertGreater(required,0,ctypes.get_last_error())
        buffer=ctypes.create_unicode_buffer(required)
        length=get_short_path(str(directory),buffer,required)
        self.assertTrue(0<length<required,ctypes.get_last_error())
        short_directory=Path(buffer.value)
        if runtime._path_key(short_directory)==runtime._path_key(directory):
            self.skipTest("filesystem did not provide a distinct real 8.3 alias")
        self.assertEqual(short_directory.resolve(),directory)
        self.directory=short_directory
        self.process=Process(self.directory,self.definitions)
        filename=runtime.ARTIFACTS["query"][0]
        self.process.load_unknown[filename]=BridgeError("WaitForSingleObject",258)
        first=self.install()
        raw_key=runtime._path_key(short_directory/filename)
        canonical_key=runtime._path_key((short_directory/filename).resolve())
        self.assertNotEqual(raw_key,canonical_key)
        self.assertNotIn(raw_key,first["journal"]["loads"])
        owner=first["journal"]["loads"][canonical_key]["attempt"]
        self.assertNotIn("query",first["modules"])
        self.directory=directory
        second=self.install()
        self.assertIs(second["journal"]["loads"][canonical_key]["attempt"],owner)
        self.assertNotIn("query",second["modules"])
        self.assertEqual([path.name for path in self.process.loads].count(filename),1)
        self.assertEqual(owner.thread.closes,0)
        owner.thread.ready=True
        third=self.install()
        self.assertEqual(third["status"],"ready")
        self.assertEqual([path.name for path in self.process.loads].count(filename),1)
        self.assertEqual(owner.thread.closes,1)
        self.assertEqual(self.process.frees,[0xF001])

    def test_identity_free_trailer_and_healthy_hook_reuse_do_not_reject(self):
        for filename,symbols in self.definitions.items():
            (self.directory/filename).write_bytes(dll(symbols,trailer=b"changing inject library trailer"))
        first=self.install()
        self.assertEqual(first["status"],"ready")
        self.world_status.return_value={"status":"ok","installed":1,"world":"0x7FF900000000",
            "original_vtable":"0x7FF100000000","hook_vtable":"0xABCDEF000000"}
        second=self.install()
        self.assertEqual(second["status"],"ready")
        self.assertEqual(len(self.process.loads),5)
        self.assertEqual(len(self.process.threads),1)

    def test_healthy_historical_session_wins_over_uninstalled_current_hint(self):
        filename=runtime.ARTIFACTS["session"][0]
        symbols=self.definitions[filename]
        self.process.add(filename,0xB0000,symbols)
        self.process.add("old-session-name.dll",0xC0000,symbols,path="elsewhere/old-session-name.dll")
        self.process.state_by_base[0xB0000]=False
        self.process.state_by_base[0xC0000]=True
        result=self.install(modules={"session":0xB0000})
        self.assertEqual(result["status"],"ready")
        self.assertEqual(result["modules"]["session"]["base"],0xC0000)
        self.assertEqual(self.process.threads,[])

    def test_stale_installed_default_does_not_hide_live_alias_owner(self):
        filename=runtime.ARTIFACTS["session"][0]; symbols=self.definitions[filename]
        self.process.add(filename,0xB0000,symbols)
        self.process.add("live-alias.dll",0xC0000,symbols,path="elsewhere/live-alias.dll")
        self.process.state_by_base[0xB0000]={"installed":1,"hook_callback":0xA90000}
        self.process.state_by_base[0xC0000]=True
        result=self.install()
        self.assertEqual(result["modules"]["session"]["base"],0xC0000)
        self.assertEqual(result["status"],"ready")
        self.assertEqual(self.process.threads,[])
        self.assertEqual(self.process.current,0x90000)

    def test_unreadable_default_does_not_hide_readable_uninstalled_alias(self):
        filename=runtime.ARTIFACTS["session"][0]; symbols=self.definitions[filename]
        self.process.add(filename,0xB0000,symbols)
        self.process.add("readable-alias.dll",0xC0000,symbols,path="elsewhere/readable-alias.dll")
        self.process.state_by_base[0xB0000]=BridgeError("ReadProcessMemory",299)
        self.process.state_by_base[0xC0000]=False
        result=self.install()
        self.assertEqual(result["modules"]["session"]["base"],0xC0000)
        self.assertEqual(result["status"],"ready")
        self.assertEqual(len(self.process.threads),1)
        self.assertEqual(result["stages"]["interface"],"ready")

    def test_unknown_interface_delivery_reconciles_observed_hook_without_resend(self):
        error=BridgeError("ReadFile(pipe)",109)
        self.install_interface_hook.side_effect=error
        first=self.install()
        self.assertEqual(first["stages"]["interface"],"pending")
        self.assertEqual(first["stages"]["session"],"ready")
        self.assertIs(first["journal"]["interface"]["client"],self.client)
        self.world_status.return_value={"status":"ok","installed":1,"world":"0x800000"}
        second=self.install()
        self.assertEqual(second["status"],"ready")
        self.install_interface_hook.assert_called_once()
        self.assertEqual(len(self.process.threads),1)

    def test_conflicting_existing_session_is_kept_and_interface_still_works(self):
        self.process.installed=True; self.process.current=0xDEAD0000
        first=self.install()
        self.assertEqual(first["stages"]["interface"],"ready")
        self.assertEqual(first["stages"]["session"],"unavailable")
        self.assertEqual(self.process.threads,[])
        self.assertEqual(self.process.current,0xDEAD0000)
        self.assertTrue(any(item["operation"]=="Session binding" for item in first["diagnostics"]))


    def test_known_already_exists_result_keeps_original_native_code(self):
        self.process.install_error=183
        result=self.install()
        self.assertEqual(result["status"],"ready")
        self.assertEqual(result["session_hook_install_error"],183)
        self.assertEqual(len(self.process.threads),1)
        again=self.install()
        self.assertEqual(again["status"],"ready")
        self.assertEqual(len(self.process.threads),1)


if __name__=="__main__":unittest.main()
