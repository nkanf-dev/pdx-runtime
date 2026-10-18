"""CPU-only actual-slot/stopped-worker continuation, not native shutdown execution."""
import unittest
from unittest.mock import Mock
from tests.core import test_v3_runtime as runtime_fixture
from tests.core import test_v3_session_reset_recovery as reset_fixture
from pdx_runtime import v3_runtime as runtime
from pdx_runtime.native_bridge import BridgeError


class WorkerReadinessTests(unittest.TestCase):
    def setUp(self):
        runtime_fixture.RecoveryTests.setUp(self)
        self.process=reset_fixture.ResetProcess(self.directory,self.definitions)
        self.assertEqual(self.install()["status"],"ready")
        self.process.threads=[]; self.process.install_calls=0
    def install(self):return runtime.install(self.client,self.process,self.directory)
    def shutdown_worker(self):
        self.process.worker=0; self.process.initialized=0; self.process.stop=1
    def initialize_worker(self):
        self.process.current=self.process.original
        self.process.worker=0x84000; self.process.initialized=1; self.process.stop=0

    def test_same_hook_without_worker_defers_then_init_repairs_same_queued_request(self):
        self.process.queue.update(phase=1,request_id=81,completed_request_id=0,
                                  callbackfn=0xA100,opaque=0xB100,session=self.process.native_session)
        queued=self.process.queue.copy()
        hook=self.process.current
        self.shutdown_worker()
        for _ in range(2):
            stopped=self.install()
            self.assertEqual(stopped["stages"],{"interface":"ready","session":"deferred"})
            self.assertNotEqual(stopped["status"],"ready")
            self.assertEqual(self.process.current,hook)
            self.assertTrue(self.process.installed)
            self.assertEqual(self.process.queue,queued)
            self.assertEqual((self.process.worker,self.process.initialized,self.process.stop),(0,0,1))
            self.assertEqual(self.process.install_calls,0)
            self.assertEqual(self.process.remove_calls,0)
            self.assertEqual(self.process.frees,[])
        self.initialize_worker()
        recovered=self.install()
        self.assertEqual(recovered["status"],"ready")
        self.assertEqual(self.process.install_calls,1)
        self.assertEqual(self.process.remove_calls,0)
        self.assertEqual(self.process.queue,queued)
        self.process.tick(); self.process.tick()
        self.assertEqual(self.process.callback_calls,1)
        self.assertEqual(self.process.queue["completed_request_id"],81)
        self.assertEqual(self.process.queue["opaque"],0xB100)

    def test_installat_host_binding_has_no_game_worker_field_reads(self):
        state={"installed":1,"carrier":0,"drain":0x10100,"hook_callback":0x90000,
               "current_callback":0x90000,"session":0x12340000,"callback_slot":0x56780000,
               "main_module_base":0}
        host=Mock()
        host.read.side_effect=AssertionError("InstallAt storage is not a native game Session")
        self.assertTrue(runtime._session_service(host,state,0,0x10100,[]))
        host.read.assert_not_called()

    def test_stopped_worker_preserves_unknown_original_install_thread(self):
        self.process.current=self.process.original
        self.process.wait_error=BridgeError("WaitForSingleObject",258)
        self.assertEqual(self.install()["status"],"pending")
        original=self.process.threads[0]
        self.process.current=self.process.hook  # Effect visible; thread not signaled.
        self.shutdown_worker()
        stopped=self.install()
        self.assertEqual(stopped["stages"]["session"],"deferred")
        self.assertEqual(stopped["status"],"pending")
        self.assertIs(stopped["journal"]["session"]["thread"],original)
        self.assertEqual(original.closes,0)
        self.assertEqual(self.process.install_calls,1)
        original.ready=True; self.process.wait_error=None
        completed=self.install()
        self.assertEqual(completed["stages"]["session"],"deferred")
        self.assertEqual(original.waits,1)
        self.assertEqual(original.closes,1)
        self.assertEqual(self.process.install_calls,1)
        self.initialize_worker()
        self.assertEqual(self.install()["status"],"ready")
        self.assertEqual(self.process.install_calls,2)
        self.assertEqual(original.waits,1)
        self.assertEqual(original.closes,1)


if __name__=="__main__":unittest.main()
