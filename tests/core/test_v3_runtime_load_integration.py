"""Real loader lifecycle + real install continuation, using only fake OS APIs."""
import ctypes
from pathlib import Path
import unittest
from pdx_runtime import native_bridge as native, v3_runtime as runtime
from tests.core import test_v3_runtime as fixtures

class API:
    def __init__(self):
        self.events=[];self.memory={};self.threads={};self.next_argument=0xA00000;self.next_thread=0x200
        self.first_handle=None;self.first_ready=False;self.owner=None
    def open_process(self,*args):return 0x100
    def allocate(self,process,unused,size,kind,protection):
        address=self.next_argument;self.next_argument+=0x1000;self.memory[address]=None
        self.events.append(('allocate',address));return address
    def write_process_memory(self,process,address,data,size,count):
        self.memory[address]=bytes(data)[:size];count._obj.value=size;return True
    def create_thread(self,process,unused,size,loader,argument,flags,tid):
        handle=self.next_thread;self.next_thread+=0x10;tid._obj.value=handle+1
        path=Path(self.memory[argument].decode('utf-16-le').rstrip('\0'))
        module=self.owner.add(path.name,0x30000+len(self.threads)*0x10000,self.owner.definitions[path.name],path)
        self.threads[handle]={'module':module,'argument':argument}
        if self.first_handle is None:self.first_handle=handle
        self.events.append(('create',handle,path.name));return handle
    def wait(self,handle,timeout):
        self.events.append(('wait',handle,timeout))
        return 258 if handle==self.first_handle and not self.first_ready else 0
    def thread_exit(self,handle,code):
        code._obj.value=self.threads[handle]['module']['base'];return True
    def free(self,process,address,size,kind):
        self.events.append(('free',address));return True
    def close_handle(self,handle):self.events.append(('close',handle));return True
    def failure(self,operation):return native.BridgeError(operation,5)

class CombinedProcess(fixtures.Process,native.RemoteProcess):
    def __init__(self,directory,definitions):
        fixtures.Process.__init__(self,directory,definitions)
        api=API();native.RemoteProcess.__init__(self,123,api=api);api.owner=self
    load_library=native.RemoteProcess.load_library
    allocate=native.RemoteProcess.allocate
    write=native.RemoteProcess.write
    free=native.RemoteProcess.free
    def resolve_export(self,base,symbol,*,aliases=None):
        assert aliases is None, "raw loader and selected installer bases do not choose V3 aliases"
        if base=='kernel32.dll' and symbol=='LoadLibraryW':return 0xABC000
        return fixtures.Process.resolve_export(self,base,symbol,aliases=aliases)
    def start_thread(self,address,argument=0):
        if address==0xABC000:return native.RemoteProcess.start_thread(self,address,argument)
        return fixtures.Process.start_thread(self,address,argument)

class CombinedRecovery(unittest.TestCase):
    def test_slow_real_loader_keeps_other_roles_working_then_continues_once(self):
        setup=fixtures.RecoveryTests('test_all_in_menu_keeps_interface_then_resumes_session_in_world')
        setup.setUp();self.addCleanup(setup.doCleanups)
        process=CombinedProcess(setup.directory,setup.definitions)
        self.addCleanup(process.close)
        first=runtime.install(setup.client,process,setup.directory,stage='interface')
        self.assertEqual(first['stages']['interface'],'ready')
        self.assertEqual(first['status'],'pending')
        self.assertEqual(len(process.pending_library_loads),1)
        attempt=process.pending_library_loads[0]
        self.assertIsInstance(attempt,native.LibraryLoadAttempt)
        self.assertEqual(len([e for e in process.api.events if e[0]=='create']),5)
        self.assertEqual(len([e for e in process.api.events if e[0]=='free']),4)
        self.assertEqual(len([role for role in runtime.ARTIFACTS if role in first['modules']]),4)
        self.assertNotIn(attempt.thread.handle,[e[1] for e in process.api.events if e[0]=='close'])
        # The module was already visible, but it was not used before signal.
        self.assertTrue(any(m['path']==str(attempt.path) for m in process.loaded))
        process.api.first_ready=True
        second=runtime.install(setup.client,process,setup.directory,stage='interface')
        self.assertEqual(second['status'],'ready')
        self.assertFalse(second['pending']);self.assertFalse(process.pending_library_loads)
        self.assertEqual(len([e for e in process.api.events if e[0]=='create']),5)
        self.assertEqual(len([e for e in process.api.events if e[0]=='free']),5)
        self.assertEqual(setup.install_interface_hook.call_count,1)
        self.assertEqual(len([role for role in runtime.ARTIFACTS if role in second['modules']]),5)
        self.assertTrue(all(e[2]==0 for e in process.api.events if e[0]=='wait'))

if __name__=='__main__':unittest.main(verbosity=2)
