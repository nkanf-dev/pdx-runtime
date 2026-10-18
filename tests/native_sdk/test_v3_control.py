"""Exact native result layouts and non-blocking callback storage behavior."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from pdx_runtime import v3_control as control
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES


class V3ControlTests(unittest.TestCase):
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
        self.client = Mock(spec_set=["pid", "request", "status"], pid=123)
        self.world_status = Mock()
        self.interface_callback = Mock()
        self.client.request.side_effect = self._wire_request_fixture
        self.interface_callback.return_value = {
            "status": "ok", "error": 0, "request_id": 9, "completed_request_id": 8}
        self.process = Mock(pid=123)
        self.process.resolve_export.return_value = 0x10000
        self.process.allocate.return_value = 0x20000

    def test_validation_exact_layout_and_pending_storage(self):
        query = control.validate_construction(self.client, 1, 5, 0x30000,
                                             carrier=0x40000, process=self.process)
        self.assertEqual(control.VALIDATION_RESULT.size, 64)
        self.process.write.assert_called_once_with(0x20000,
            control.VALIDATION_RESULT.pack(64, 997, 1, 5, 0x30000, 0x40000, 0, 0, 0, 0, 0, 0))
        self.world_status.return_value = {"status": "ok", "completed_request_id": 8}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        self.world_status.return_value = {"status": "ok", "completed_request_id": 9}
        self.process.read.return_value = control.VALIDATION_RESULT.pack(
            64, 0, 1, 5, 0x30000, 0x40000, 0, 0, 1, 7372, 0x50000, 0x60000)
        result = query.read()
        self.assertEqual(result.raw_low, 0)
        self.assertEqual(result.raw_high, 0)
        self.assertEqual(result.validated, 1)
        self.assertEqual(result.execution_thread_id, 7372)
        self.process.read.assert_called_once_with(0x20000, 64)
        self.process.free.assert_called_once_with(0x20000)

    def test_start_exact_layout_acceptance_is_not_started(self):
        query = control.start(self.client, control.StartAction.SELECT_AND_START_PAUSED,
                              1, process=self.process, adapter=0x70000)
        self.assertEqual(control.START_RESULT.size, 80)
        self.process.resolve_export.assert_called_once_with(0x70000, "PdxV3StartGameInterface", aliases=V3_EXPORT_ALIASES)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 9}
        self.process.read.return_value = control.START_RESULT.pack(
            80, 0, 3, 1, 2, 1, 1, 0, 1, 0, 0x100, 0x200, 0x300, 0x400, 0x500)
        result = query.read()
        self.assertEqual(result.start_accepted, 1)
        self.assertEqual(result.game_started, 0)
        self.assertEqual(result.selected_country_id, 1)
        self.assertEqual(result.player, 0x500)
        self.interface_callback.assert_called_once()

    def test_native_start_error_is_preserved_not_retried(self):
        query = control.start(self.client, control.StartAction.SELECT_COUNTRY, 0xFF000001,
                              process=self.process)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 9}
        self.process.read.return_value = control.START_RESULT.pack(
            80, 1168, 1, 0xFF000001, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        result = query.read()
        self.assertEqual(result.error, 1168)
        self.assertIs(query.read(), result)
        self.interface_callback.assert_called_once()

    def test_finalize_exact_layout_and_independent_observation(self):
        query = control.finalize_play(self.client, 1, observe=True, process=self.process)
        self.assertEqual(control.PLAY_FINALIZE_RESULT.size, 144)
        self.process.write.assert_called_once_with(0x20000,
            control.PLAY_FINALIZE_RESULT.pack(144, 997, 0, 1, *([0] * 22)))
        self.process.resolve_export.assert_called_once_with(
            "pdx_v3_play_finalize.dll", "PdxV3PlayFinalizeInterface", aliases=V3_EXPORT_ALIASES)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 9}
        self.process.read.return_value = control.PLAY_FINALIZE_RESULT.pack(
            144, 0, 0, 1, 2, 1, 1, 8, 0, 0, 0, 0,
            0x100, 0x200, 0x300, 0x400, 0x500, 0x600, 0x700, 0x800,
            1, 0xFFFFFFFF, 0, 1, 1, 0x800)
        result = query.read()
        self.assertEqual(result.gui_mode, 2)
        self.assertEqual(result.lobby_visible, 0)
        self.assertEqual(result.simulation_started, 1)
        self.assertEqual(result.player_country_id, 1)
        self.assertEqual(result.handler_country_id, 0xFFFFFFFF)
        self.assertEqual(result.handler_ref_database, 0)
        self.assertEqual(result.lobby_ref_database, 0x800)
        self.assertEqual(result.native_ready_called, 0)
        self.process.free.assert_called_once_with(0x20000)
        self.interface_callback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
