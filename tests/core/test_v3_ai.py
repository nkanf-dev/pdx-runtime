from __future__ import annotations
import unittest
import struct
from unittest.mock import Mock, patch
from pdx_runtime import v3_ai as ai
from pdx_runtime import v3_player_control as player
from pdx_runtime import v3_console as console
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES

class AiControlTests(unittest.TestCase):
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
        self.interface_callback.return_value={"status":"ok", "error": 0,"request_id":9}
        self.process.resolve_export.return_value=0x10000;self.process.allocate.return_value=0x20000

    def snapshot(self,**overrides):
        values=dict(size=144,error=0,country_full_id=0x02000004,country_error=0,
            actor_full_id=0x03000007,actor_error=0,owner_country_full_id=0x02000004,actor_enabled_raw=1,
            manager_override_raw=0,manager_error=0,role_index=4,role_raw=0,role_count=8,role_error=0,
            observed_predicate=1,observed_predicate_valid=1,paused=1,simulation_started=1,execution_thread_id=7372,reserved=0,
            application=0x10,world=0x20,idler=0x30,country_manager=0x40,ai_manager=0x50,country=0x60,actor=0x70,role_data=0x80)
        values.update(overrides);return ai.STATE.pack(*values.values())

    def complete(self,query,data):
        self.world_status.return_value={"status":"ok","completed_request_id":9}
        self.process.read.return_value=data
        return query.read()

    def test_query_wire_and_copied_lifecycle_no_replay(self):
        query=ai.observe_country_ai(self.client,0x02000004,process=self.process)
        self.assertEqual(ai.STATE.size,144);self.assertEqual(player.STATE.size,160);self.assertEqual(console.REQUEST.size,96)
        self.process.resolve_export.assert_called_once_with("pdx_v3_console.dll","PdxV3ConsoleAiStateInterface", aliases=V3_EXPORT_ALIASES)
        values=ai.STATE.unpack(self.process.write.call_args.args[1]);self.assertEqual(values[:3],(144,997,0x02000004))
        self.world_status.return_value={"status":"ok","completed_request_id":8}
        self.assertIsNone(query.read());self.process.free.assert_not_called()
        result=self.complete(query,self.snapshot());self.assertEqual(result.actor_full_id,0x03000007)
        self.assertEqual(result.actor_enabled_raw,1);self.assertEqual(result.observed_predicate_valid,1)
        self.assertIs(query.read(),result);self.process.free.assert_called_once_with(0x20000)
        self.interface_callback.assert_called_once()

    def test_independent_actor_and_role_errors_preserved(self):
        query=ai.observe_country_ai(self.client,4,process=self.process)
        result=self.complete(query,self.snapshot(actor_full_id=0xFFFFFFFF,actor_error=1168,actor_enabled_raw=0,
            actor=0,manager_override_raw=3,role_error=0,observed_predicate=0,observed_predicate_valid=0))
        self.assertEqual(result.error,0);self.assertEqual(result.actor_error,1168)
        self.assertEqual(result.manager_error,0);self.assertEqual(result.manager_override_raw,3);self.assertEqual(result.role_error,0)
        self.assertEqual(result.observed_predicate_valid,0)
        data=self.snapshot(role_count=4,role_raw=0xFFFFFFFF,role_error=1168,manager_override_raw=3,observed_predicate=1)
        bounds=ai.AiState.from_bytes(data);self.assertEqual(bounds.role_error,1168);self.assertEqual(bounds.observed_predicate_valid,1)
        self.assertEqual(bounds.role_raw,0xFFFFFFFF);self.assertEqual(bounds.observed_predicate,1)

    def test_raw_nonboolean_bytes_and_native_exception_status(self):
        state=ai.AiState.from_bytes(self.snapshot(actor_enabled_raw=2,manager_override_raw=3,role_raw=99))
        self.assertEqual((state.actor_enabled_raw,state.manager_override_raw,state.role_raw),(2,3,99))
        query=ai.observe_country_ai(self.client,4,process=self.process)
        result=self.complete(query,self.snapshot(error=574,actor_error=21,observed_predicate_valid=0))
        self.assertEqual(result.error,574);self.assertEqual(result.actor_error,21)

    def test_full_country_id_validation_no_mask_or_allocation(self):
        for value in [True,False,"FRA",4.0,None]:
            with self.assertRaises(TypeError):ai.observe_country_ai(self.client,value,process=self.process)
        for value in [-1,0x100000000]:
            with self.assertRaises(ValueError):ai.observe_country_ai(self.client,value,process=self.process)
        self.process.allocate.assert_not_called()
        ai.observe_country_ai(self.client,0xFFFFFFFF,process=self.process)
        self.assertEqual(ai.STATE.unpack(self.process.write.call_args.args[1])[2],0xFFFFFFFF)

    def test_original_commands_forward_one_each_no_hidden_queries(self):
        with patch.object(ai.v3_console,"execute_arguments",return_value=object()) as submit:
            disable=ai.disable_ai(self.client,"FRA",output_capacity=12,adapter="fresh.dll",process=self.process)
            submit.assert_called_once_with(self.client,("disable_ai","FRA"),output_capacity=12,adapter="fresh.dll",process=self.process)
            enable=ai.enable_ai(self.client,"FRA",process=self.process)
            self.assertEqual(submit.call_count,2);self.assertEqual(submit.call_args.args,(self.client,("enable_ai","FRA")))
            self.assertIs(disable,enable)
            self.world_status.assert_not_called();self.interface_callback.assert_not_called()

    def test_selector_scopes_and_strings_are_not_rewritten(self):
        with patch.object(ai.v3_console,"execute_arguments") as submit:
            ai.disable_ai(self.client,"all",process=self.process);self.assertEqual(submit.call_args.args[1],("disable_ai","all"))
            ai.enable_ai(self.client,"self",process=self.process);self.assertEqual(submit.call_args.args[1],("enable_ai","self"))
            ai.enable_ai(self.client,"fRa",process=self.process);self.assertEqual(submit.call_args.args[1],("enable_ai","fRa"))
            ai.disable_ai(self.client,"",process=self.process);self.assertEqual(submit.call_args.args[1],("disable_ai",""))
            with self.assertRaises(TypeError):ai.enable_ai(self.client,4,process=self.process)
            self.assertEqual(submit.call_count,4)

    def test_world_and_owned_payload_are_distinct_wire_values(self):
        state=ai.AiState.from_bytes(self.snapshot(world=0x2000,country_manager=0x8000,ai_manager=0xA680))
        self.assertEqual(state.world,0x2000)
        self.assertEqual(state.country_manager,0x8000)
        self.assertNotEqual(state.world,state.country_manager)
        self.assertEqual(state.ai_manager,state.country_manager+0x2680)
        values=ai.STATE.unpack(self.snapshot(world=0x2000,country_manager=0x8000,ai_manager=0xA680))
        self.assertEqual(values[21],0x2000);self.assertEqual(values[23],0x8000);self.assertEqual(values[24],0xA680)

    def test_layout_mismatch_rejected(self):
        with self.assertRaises(ValueError):ai.AiState.from_bytes(self.snapshot(size=160))
        with self.assertRaises(struct.error):ai.AiState.from_bytes(bytes(160))

if __name__=="__main__":unittest.main()
