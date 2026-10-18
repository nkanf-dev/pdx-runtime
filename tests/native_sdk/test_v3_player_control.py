from __future__ import annotations
import unittest
from unittest.mock import Mock, patch
from pdx_runtime import v3_player_control as player
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES

class PlayerControlTests(unittest.TestCase):
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
        values=dict(size=160,error=0,action=1,tag_bytes=0,local_machine_id=3,application_player_id=3,
            player_machine_id=3,player_flags_raw=0xF7,observer_enabled=1,observer_global=1,paused=1,simulation_started=1,
            controlled_country_id=0xFFFFFFFF,controlled_country_error=1168,selected_country_id=0x02000002,
            selected_country_error=0,target_country_id=0xFFFFFFFF,target_error=21,target_control_eligible=0,
            execution_thread_id=7372,application=0x10,world=0x20,idler=0x30,player=0x40,controlled_ref_database=0,
            selected_ref_database=0x50,controlled_country=0,selected_country=0x60,target_country=0,tag_raw=bytes(8))
        values.update(overrides);return player.STATE.pack(*values.values())

    def test_copied_observation_lifecycle_and_separate_refs(self):
        query=player.observe_player(self.client,process=self.process)
        self.assertEqual(player.STATE.size,160)
        self.process.resolve_export.assert_called_once_with("pdx_v3_console.dll","PdxV3ConsolePlayerStateInterface", aliases=V3_EXPORT_ALIASES)
        self.world_status.return_value={"status":"ok","completed_request_id":8}
        self.assertIsNone(query.read());self.process.free.assert_not_called()
        self.world_status.return_value={"status":"ok","completed_request_id":9}
        self.process.read.return_value=self.snapshot()
        result=query.read();self.assertEqual(result.observer_enabled,1)
        self.assertEqual(result.controlled_country_error,1168);self.assertEqual(result.selected_country_id,0x02000002)
        self.assertEqual(result.error,0);self.assertIs(query.read(),result)
        self.process.free.assert_called_once_with(0x20000)

    def test_tag_resolution_bytes_and_native_error_preserved(self):
        query=player.resolve_tag(self.client,"swe",process=self.process)
        data=self.process.write.call_args.args[1];values=player.STATE.unpack(data)
        self.assertEqual(values[2:4],(2,3));self.assertEqual(values[-1],b"swe"+bytes(5))
        self.world_status.return_value={"status":"ok","completed_request_id":9}
        self.process.read.return_value=self.snapshot(action=2,tag_bytes=3,tag_raw=b"ZZZ"+bytes(5),target_error=1168)
        result=query.read();self.assertEqual(result.target_error,1168);self.assertEqual(result.error,0)
        self.assertEqual(result.tag,"ZZZ");self.interface_callback.assert_called_once()

    def test_controls_forward_exact_argv_once_no_automatic_toggle(self):
        with patch.object(player.v3_console,"execute_arguments",return_value=object()) as submit:
            player.switch_country(self.client,"swe",process=self.process)
            self.assertEqual(submit.call_args.args,(self.client,("tag","swe")))
            player.toggle_observer(self.client,process=self.process)
            self.assertEqual(submit.call_args.args,(self.client,("observe",)))
            self.assertEqual(submit.call_count,2)
            self.world_status.assert_not_called()

    def test_invalid_tag_shape_rejected_before_allocation(self):
        for tag in ["", "SW", "SWED", "S\0E", "中文"]:
            with self.assertRaises(ValueError):player.resolve_tag(self.client,tag,process=self.process)
        self.process.allocate.assert_not_called()
        with self.assertRaises(ValueError):player.PlayerState.from_bytes(self.snapshot(size=152))

if __name__=="__main__":unittest.main()
