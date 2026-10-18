"""Behavior of the small Python composition helper, without game execution."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from pdx_runtime import v3
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES
from pdx_runtime.native_bridge import BridgeError


def fixture_wire_request(client, command):
    """Explicit CPU transport for these fixtures, not a runtime compatibility path."""
    if command == "world-status":
        return client.v3_world_status()
    parts = command.split()
    if len(parts) == 3 and parts[0] == "interface-callback":
        return client.queue_v3_interface_callback(int(parts[1], 0), int(parts[2], 0))
    raise AssertionError(f"unexpected V3 fixture wire command: {command!r}")


def bind_fixture_wire(client):
    client.request.side_effect = lambda command: fixture_wire_request(client, command)


class V3QueryTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=123)
        bind_fixture_wire(self.client)
        self.client.queue_v3_interface_callback.return_value = {
            "status": "ok", "error": 0, "request_id": 7, "completed_request_id": 6}
        self.process = Mock(pid=123)
        self.process.resolve_export.return_value = 0x10000
        self.process.allocate.return_value = 0x20000
        self.process.read.return_value = v3.COUNTRY_RESULT.pack(40, 0, 1, 1, 0x30000, 0x40000, 2478004656764)

    def test_submit_and_pending_keep_native_storage_alive(self):
        query = v3.submit(self.client, 1, process=self.process)
        self.process.write.assert_called_once_with(0x20000, v3.COUNTRY_RESULT.pack(40, 997, 1, 0, 0, 0, 0))
        self.client.queue_v3_interface_callback.assert_called_once_with(0x10000, 0x20000)
        self.client.request.assert_called_once_with("interface-callback 0x10000 0x20000")
        self.client.v3_world_status.return_value = {"status": "ok", "completed_request_id": 6}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "pending"):
            query.close()
        self.process.free.assert_not_called()

    def test_completed_result_copies_bits_and_releases_once(self):
        query = v3.submit(self.client, 1, process=self.process, adapter=0x55000)
        self.client.v3_world_status.return_value = {"status": "ok", "completed_request_id": 7}
        result = query.read()
        self.assertTrue(result.valid)
        self.assertEqual(result.resolved_id, 1)
        self.assertEqual(result.gdp_raw64, 2478004656764)
        self.assertEqual(result.gdp_raw64_hex, "0x00000240f4965a7c")
        self.assertIs(query.read(), result)
        query.close()
        self.process.free.assert_called_once_with(0x20000)
        self.process.close.assert_not_called()
        self.process.resolve_export.assert_called_once_with(0x55000, "PdxV3QueryCountryInterface",
                                                            aliases=V3_EXPORT_ALIASES)

    def test_native_invalid_id_status_is_not_a_real_zero(self):
        query = v3.submit(self.client, 1, process=self.process)
        self.client.v3_world_status.return_value = {"status": "ok", "completed_request_id": 7}
        self.process.read.return_value = v3.COUNTRY_RESULT.pack(40, 1168, 1, 0, 0x40000, 0x40000, 0)
        result = query.read()
        self.assertFalse(result.valid)
        self.assertEqual(result.error, 1168)
        self.assertEqual(result.gdp_raw64, 0)

    def test_rejected_submission_releases_storage(self):
        self.client.queue_v3_interface_callback.return_value = {"status": "error", "error": 170}
        with self.assertRaises(BridgeError):
            v3.submit(self.client, 1, process=self.process)
        self.process.free.assert_called_once_with(0x20000)

    def test_unknown_delivery_preserves_recoverable_query(self):
        self.client.queue_v3_interface_callback.side_effect = BridgeError("ReadFile", 109)
        with self.assertRaises(BridgeError) as captured:
            v3.submit(self.client, 1, process=self.process)
        query = captured.exception.query
        self.assertEqual(query.result_address, 0x20000)
        self.process.free.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            query.read()
        self.client.v3_world_status.return_value = {"status": "ok", "completed_request_id": 7}
        self.assertTrue(query.recover(7).read().valid)


    def test_completed_unwritten_result_is_terminal_callback_error(self):
        query = v3.submit(self.client, 1, process=self.process)
        self.client.v3_world_status.return_value = {
            "status": "ok", "request_id": 7, "completed_request_id": 7, "world_error": 574}
        self.process.read.return_value = v3.COUNTRY_RESULT.pack(40, 997, 1, 0, 0, 0, 0)
        with self.assertRaises(v3.QueryExecutionError) as captured:
            query.read()
        self.assertEqual(captured.exception.winerror, 574)
        self.assertEqual(captured.exception.result.error, 997)
        self.process.free.assert_called_once_with(0x20000)
        query.close()

    def test_newer_native_error_is_not_attached_to_older_query(self):
        query = v3.submit(self.client, 1, process=self.process)
        self.client.v3_world_status.return_value = {
            "status": "ok", "request_id": 8, "completed_request_id": 8, "world_error": 574}
        self.process.read.return_value = v3.COUNTRY_RESULT.pack(40, 997, 1, 0, 0, 0, 0)
        with self.assertRaises(v3.QueryExecutionError) as captured:
            query.read()
        self.assertEqual(captured.exception.winerror, 0)


    def test_explicit_completion_source_keeps_actual_client_public(self):
        completion = Mock(return_value={"status": "ok", "completed_request_id": 6})
        query = v3.CallbackResult(self.client, self.process, 0x10000, 0x20000, False,
            40, v3.QueryResult.from_bytes, status_getter=completion)
        query.request_id = 7
        self.assertIs(query.client, self.client)
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        completion.return_value = {"status": "ok", "completed_request_id": 7}
        self.assertTrue(query.read().valid)
        self.client.v3_world_status.assert_not_called()
        self.process.free.assert_called_once_with(0x20000)


if __name__ == "__main__":
    unittest.main()
