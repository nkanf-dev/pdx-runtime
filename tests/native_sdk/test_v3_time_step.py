"""Explicit one-effect/one-observation queue composition, not game simulation."""
import unittest
from unittest.mock import Mock
from dataclasses import replace
from pdx_runtime import v3_time_step as step
from pdx_runtime.native_bridge import BridgeError

class TimeStepTests(unittest.TestCase):
    def _wire_request_fixture(self, command):
        """CPU transport: preserve raw responses through exact V3 wire commands."""
        if command == "world-status":
            return self.world_status()
        words = command.split()
        if len(words) == 3 and words[0] == "interface-callback":
            callback, address = int(words[1], 0), int(words[2], 0)
            self.assertEqual(command, f"interface-callback {callback:#x} {address:#x}")
            return self.interface_callback(callback, address)
        raise AssertionError(f"unexpected V3 fixture wire command: {command!r}")

    def setUp(self):
        self.client=Mock(spec_set=["pid", "request", "status"], pid=123);self.process=Mock(pid=123)
        self.world_status = Mock()
        self.interface_callback = Mock()
        self.client.request.side_effect = self._wire_request_fixture
        self.interface_callback.return_value={"status":"ok", "error": 0,"request_id":7}
        self.process.resolve_export.return_value=0x10000;self.process.allocate.return_value=0x20000
    def snapshot(self,**options):
        fields=dict(size=160,error=0,action=1,accepted=1,execution_thread_id=123,
            paused=1,game_started=1,transition_pending=0,initialized=1,thread_stop=0,
            world_updating=0,queues_settled=1,outgoing_active=0,outgoing_pending=0,
            incoming_active=0,incoming_pending=0,session_buffered=0,logic_tick_pending=0,
            raw_counter_5964f10=0,raw_counter_5964f14=0,application=1,world=2,idler=3,
            logic=4,session=5,dispatcher=6,thread_handle=7,active_command=0,date_raw=43800000,frame=100)
        fields.update(options);return step.Snapshot(**fields)
    def test_exact6hours_and_settled_followups_required(self):
        before=self.snapshot()
        after=replace(before,action=0,accepted=0,date_raw=43800006)
        self.assertTrue(step.compare(before,after).completed)
        self.assertTrue(step.compare(before,replace(after,queues_settled=0,incoming_pending=1)).pending)
        self.assertTrue(step.compare(before,replace(after,date_raw=43800000)).pending)
        self.assertFalse(step.compare(before,replace(after,date_raw=43800012)).completed)
        self.assertFalse(step.compare(before,replace(after,world=99)).completed)
        self.assertFalse(step.compare(before,replace(after,paused=0)).completed)
        self.assertFalse(step.compare(before,replace(after,error=170)).completed)
    def test_raw_df_and_accounting_not_completion_identity(self):
        before=self.snapshot()
        after=replace(before,action=0,date_raw=43800006,logic_tick_pending=1,
                      raw_counter_5964f10=99,raw_counter_5964f14=42)
        self.assertTrue(step.compare(before,after).completed)
    def test_pending_unknown_preserves_shared_callback_without_replay(self):
        self.interface_callback.side_effect=BridgeError("ReadFile",109)
        with self.assertRaises(BridgeError) as caught:step.submit(self.client,process=self.process)
        self.assertEqual(caught.exception.query.result_address,0x20000)
        self.process.free.assert_not_called()
        self.interface_callback.assert_called_once()
    def test_observe_only_action_and_public_rawdate_decode(self):
        query=step.observe(self.client,process=self.process)
        written=self.process.write.call_args.args[1]
        self.assertEqual(step.RESULT.unpack(written)[:3],(160,997,0))
        snapshot=self.snapshot(date_raw=(0xABC<<32)|43800006)
        self.assertEqual(snapshot.date_hours,43800006)
        with self.assertRaises(ValueError):step.Snapshot.from_bytes(bytes(160))
if __name__=="__main__":unittest.main()
