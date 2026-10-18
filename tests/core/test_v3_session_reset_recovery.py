"""CPU-only Session reset continuation; no game/native host/build is invoked."""
from pathlib import Path
import unittest
from tests.core import test_v3_runtime as fixture
from pdx_runtime import v3_runtime as runtime


class ResetProcess(fixture.Process):
    def __init__(self,*args):
        self.bound=False; self.native_session=0x82000; self.hook=0x90000
        self.original=0x7FF103386F10; self.current=self.original
        self.argument=self.native_session; self.dispatcher=0x83000; self.worker=0x84000
        self.initialized=1; self.stop=0; self.drain_address=0x10100
        self.install_calls=0; self.remove_calls=0; self.callback_calls=0
        self.queue=dict(zip(runtime.SESSION_CALLBACK_FIELDS,(56,0,0,0,0,0,0,0,0)))
        super().__init__(*args)
        self.current=self.original
    def add(self,*args,**kwargs):
        module=super().add(*args,**kwargs)
        if "PdxV3SessionHookInstall" in self.exports[module["base"]]:
            self.exports[module["base"]]["PdxV3SessionCallbackState"]=module["base"]+0x300
            self.exports[module["base"]]["PdxV3SessionHookRemove"]=module["base"]+0x400
        return module
    def read(self,address,size):
        if size==runtime.SESSION_HOOK_PREFIX.size:
            native=self.native_session if self.bound else 0
            return runtime.SESSION_HOOK_PREFIX.pack(152,int(self.installed),0,0,
                native+0xB8 if native else 0,self.original if native else 0,self.hook if native else 0,
                native,self.drain_address if native else 0,0,0,0,0,
                0x7FF100000000 if native else 0,0x10000 if native else 0)
        if size==runtime.SESSION_CALLBACK.size:
            return runtime.SESSION_CALLBACK.pack(*(self.queue[field] for field in runtime.SESSION_CALLBACK_FIELDS))
        if address==self.native_session+0xB8:return self.current.to_bytes(8,"little")
        for offset,value in ((0xC0,self.argument),(0x3D8,self.dispatcher),(0xD0,self.worker)):
            if address==self.native_session+offset:return value.to_bytes(8,"little")
        if address==self.native_session+0x3F0:return bytes([self.initialized])
        if address==self.native_session+0xC8:return bytes([self.stop])
        return super().read(address,size)
    def start_thread(self,address,argument):
        if any(exports.get("PdxV3SessionHookRemove")==address for exports in self.exports.values()):
            self.remove_calls+=1
            raise AssertionError("reset continuation must never Remove/cancel the queued callback")
        self.install_calls+=1
        def install():
            self.installed=True; self.bound=True; self.current=self.hook
        thread=fixture.Thread(install,self.wait_error,0)
        self.threads.append(thread)
        return thread
    def tick(self):
        if self.current==self.hook and self.queue["phase"]==1:
            self.callback_calls+=1
            self.queue.update(phase=3,completed_request_id=self.queue["request_id"])


class SessionResetTests(unittest.TestCase):
    def setUp(self):
        fixture.RecoveryTests.setUp(self)
        self.process=ResetProcess(self.directory,self.definitions)
        result=self.install()
        self.assertEqual(result["status"],"ready")
        self.initial_loads=len(self.process.loads)
        self.process.threads=[]; self.process.install_calls=0
    def install(self,**kwargs):
        return runtime.install(self.client,self.process,self.directory,**kwargs)
    def reset(self):self.process.current=self.process.original
    def queue(self,phase=1,request=41,completed=0):
        self.process.queue.update(phase=phase,request_id=request,completed_request_id=completed,
                                  callbackfn=0xA100,opaque=0xB100,session=self.process.native_session)

    def test_queued_original_survives_reset_and_completes_once(self):
        self.queue(); self.reset()
        original=self.process.queue.copy()
        result=self.install()
        self.assertEqual(result["status"],"ready")
        self.assertEqual(self.process.queue,original)
        self.assertEqual(self.process.remove_calls,0)
        self.assertEqual(self.process.install_calls,1)
        self.process.tick(); self.process.tick()
        self.assertEqual(self.process.callback_calls,1)
        self.assertEqual(self.process.queue["completed_request_id"],41)
        self.assertEqual(self.process.queue["opaque"],0xB100)
        self.assertEqual(self.process.frees,[])
        self.assertEqual(len(self.process.loads),self.initial_loads)

    def test_executing_original_finishes_then_same_entry_repairs_without_replay(self):
        self.queue(phase=2,request=52,completed=51); self.reset()
        original=self.process.queue.copy()
        waiting=self.install()
        self.assertEqual(waiting["stages"]["session"],"pending")
        self.assertEqual(waiting["stages"]["interface"],"ready")
        self.assertEqual(self.process.queue,original)
        self.assertEqual(self.process.install_calls,0)
        self.process.callback_calls=1
        self.process.queue.update(phase=3,completed_request_id=52)
        repaired=self.install()
        self.assertEqual(repaired["status"],"ready")
        self.process.tick()
        self.assertEqual(self.process.callback_calls,1)
        self.assertEqual(self.process.queue["request_id"],52)
        self.assertEqual(self.process.queue["completed_request_id"],52)
        self.assertEqual(self.process.remove_calls,0)

    def test_reinitialization_readiness_defers_then_recovers_in_menu(self):
        self.reset(); self.process.initialized=0; self.process.worker=0; self.process.stop=1
        self.world_status.return_value={"status":"ok","installed":1,"world":"0x0"}
        first=self.install()
        self.assertEqual(first["stages"]["session"],"deferred")
        self.assertEqual(first["stages"]["interface"],"ready")
        self.assertEqual(self.process.install_calls,0)
        self.process.initialized=1; self.process.worker=0x84000; self.process.stop=0
        second=self.install()
        self.assertEqual(second["status"],"ready")
        self.assertEqual(self.process.install_calls,1)
        self.assertEqual(self.process.remove_calls,0)

    def test_foreign_active_pointer_is_not_overwritten(self):
        self.process.current=0xDEAD0000
        result=self.install()
        self.assertEqual(result["stages"]["interface"],"ready")
        self.assertEqual(self.process.current,0xDEAD0000)
        self.assertEqual(self.process.install_calls,0)
        self.assertEqual(self.process.remove_calls,0)

    def test_changed_owner_binding_is_not_reclaimed(self):
        self.reset(); self.process.drain_address=0xDEAD1000
        result=self.install()
        self.assertEqual(result["stages"]["interface"],"ready")
        self.assertEqual(self.process.current,self.process.original)
        self.assertEqual(self.process.install_calls,0)
        self.assertEqual(self.process.remove_calls,0)

    def test_completed_original_delivery_retires_before_fresh_reset_repair(self):
        from pdx_runtime.native_bridge import BridgeError
        for code in (0,183):
            with self.subTest(code=code):
                if code==183:
                    self.process=ResetProcess(self.directory,self.definitions)
                    self.assertEqual(self.install()["status"],"ready")
                    self.process.threads=[]; self.process.install_calls=0
                self.reset()
                self.process.wait_error=BridgeError("WaitForSingleObject",258)
                first=self.install()
                original=self.process.threads[0]
                self.assertEqual(first["status"],"pending")
                self.assertIs(first["journal"]["session"]["thread"],original)
                # Original Install completed, then native init restored its slot
                # before Python observed the original thread's completion.
                original.action=lambda: setattr(self.process,"current",self.process.original)
                original.code=code; original.ready=True
                self.process.wait_error=None
                second=self.install()
                self.assertEqual(second["status"],"ready")
                self.assertIsNone(second["journal"]["session"])
                self.assertEqual(second["journal"]["session_completion"]["completed"],code)
                self.assertEqual(original.waits,1)
                self.assertEqual(original.polls,1)
                self.assertEqual(original.closes,1)
                self.assertEqual(self.process.install_calls,2)
                self.assertIsNot(self.process.threads[1],original)
                self.assertEqual(self.install()["status"],"ready")
                self.assertEqual(self.process.install_calls,2)
                self.assertEqual(original.waits,1)
                self.assertEqual(original.closes,1)

    def test_original_request_terminal_retires_reset_observation_when_slot_is_foreign(self):
        self.queue(phase=2,request=71,completed=70); self.reset()
        first=self.install()
        original_module=first["journal"]["session_reset"]["module"]
        self.assertEqual(first["status"],"pending")
        self.assertEqual(self.process.install_calls,0)
        self.process.queue.update(phase=3,completed_request_id=71)
        self.process.current=0xDEAD0000
        second=self.install()
        self.assertEqual(second["status"],"partial")
        self.assertEqual(second["stages"]["session"],"unavailable")
        self.assertEqual(second["pending"],[])
        self.assertIsNone(second["journal"]["session_reset"])
        receipt=second["journal"]["session_reset_completion"]
        self.assertEqual(receipt["module"],original_module)
        self.assertEqual(receipt["callback"]["request_id"],71)
        self.assertEqual(receipt["callback"]["completed_request_id"],71)
        self.assertEqual(self.process.current,0xDEAD0000)
        self.assertEqual(self.process.queue["opaque"],0xB100)
        self.assertEqual(self.process.install_calls,0)
        self.assertEqual(self.process.remove_calls,0)
        self.assertEqual(self.process.frees,[])

    def test_healthy_hook_is_reused_without_repair(self):
        result=self.install()
        self.assertEqual(result["status"],"ready")
        self.assertEqual(self.process.install_calls,0)
        self.assertEqual(self.process.remove_calls,0)


if __name__=="__main__":unittest.main()
