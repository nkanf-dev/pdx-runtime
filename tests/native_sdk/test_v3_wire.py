"""CPU-only V3 wire encoding and original transport identity checks.

No Windows process, pipe, game or native dispatcher is opened by this suite.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from pdx_runtime.native_bridge import BridgeError, NativeBridge
from pdx_runtime.v3_wire import V3Wire
from pdx_runtime import v3


class V3WireTests(unittest.TestCase):

    def test_all_existing_commands_keep_exact_wire_and_full_width_values(self):
        response = {"status": "ok", "error": 0, "value": 0, "enabled": False}
        client = SimpleNamespace(request=Mock(return_value=response))
        wire = V3Wire(client)
        cases = (
            (wire.queue_interface_callback, (0x100000001, 0x200000002),
             "interface-callback 0x100000001 0x200000002"),
            (wire.queue_interface_callback, (0,), "interface-callback 0x0 0x0"),
            (wire.construction_status, (), "construction-status"),
            (wire.queue_construction, (0xffffffff, 0x80000001, 0xffffffffffffffff),
             "construction-submit 0xffffffff 0x80000001 0xffffffffffffffff"),
            (wire.queue_construction, (0, 0, 0), "construction-submit 0x0 0x0 0x0"),
            (wire.install_interface_hook, (), "hook-install"),
            (wire.remove_interface_hook, (), "hook-remove"),
            (wire.world_status, (), "world-status"),
            (wire.enter_world, (), "world-enter 0x0"),
            (wire.enter_world, (0x100000003,), "world-enter 0x100000003"),
            (wire.create_options, (), "options-create"),
            (wire.submit_options, (0,), "options-submit 0x0"),
            (wire.submit_options, (0x100000004,), "options-submit 0x100000004"),
            (wire.destroy_options, (0x100000005,), "options-destroy 0x100000005"),
        )
        for method, arguments, command in cases:
            with self.subTest(command=command):
                client.request.reset_mock()
                self.assertIs(method(*arguments), response)
                client.request.assert_called_once_with(command)
                self.assertIs(response["enabled"], False)
                self.assertEqual(response["value"], 0)

    def test_native_error_unknown_and_non_mapping_response_pass_through_without_retry(self):
        client = SimpleNamespace(request=Mock())
        wire = V3Wire(client)
        for response in ({"status": "error", "error": 87, "world_error": 0},
                         {"status": "ok", "error": 0, "request_id": None}, None, 0, False):
            with self.subTest(response=response):
                client.request.reset_mock()
                client.request.return_value = response
                self.assertIs(wire.world_status(), response)
                client.request.assert_called_once_with("world-status")

    def test_original_transport_exception_propagates_once_unchanged(self):
        original = BridgeError("ReadFile(pipe)", 109)
        client = SimpleNamespace(request=Mock(side_effect=original))
        with self.assertRaises(BridgeError) as caught:
            V3Wire(client).queue_interface_callback(0x100000001)
        self.assertIs(caught.exception, original)
        client.request.assert_called_once_with("interface-callback 0x100000001 0x0")

    def test_view_reuses_actual_request_lock_handle_and_pid(self):
        api = Mock()
        bridge = NativeBridge(42, api=api)
        bridge.handle = 0x1234
        original_lock = bridge._lock
        commands = []
        response = {"status": "ok", "error": 0, "request_id": 0x100000007}
        payload = json.dumps(response).encode("ascii")
        def write_file(handle, message, size, count, overlapped):
            self.assertEqual(handle, 0x1234)
            self.assertTrue(original_lock.locked())
            self.assertIsNone(overlapped)
            commands.append(message)
            count._obj.value = size
            return True
        def read_file(handle, buffer, size, count, overlapped):
            self.assertEqual(handle, 0x1234)
            self.assertTrue(original_lock.locked())
            buffer.raw = payload + bytes(size - len(payload))
            count._obj.value = len(payload)
            return True
        api.write_file.side_effect = write_file
        api.read_file.side_effect = read_file
        self.assertEqual(V3Wire(bridge).enter_world(), response)
        self.assertEqual(commands, [b"world-enter 0x0"])
        self.assertIs(bridge._lock, original_lock)
        self.assertEqual((bridge.pid, bridge.handle), (42, 0x1234))
        self.assertFalse(original_lock.locked())
        api.close_handle.assert_not_called()
        api.wait_pipe.assert_not_called()
        api.create_file.assert_not_called()


    def test_v3_callback_context_binds_original_transport_and_explicit_wire_only(self):
        ack = {"status": "ok", "error": 0, "request_id": 0x100000007}
        observed = {"status": "ok", "error": 0, "world_error": 0,
                    "request_id": 0x100000007, "completed_request_id": 0x100000006}
        commands = []
        def request(command):
            commands.append(command)
            if command == "world-status": return observed
            if command == "interface-callback 0x100000001 0x200000002": return ack
            raise AssertionError(command)
        client = SimpleNamespace(pid=42, request=request)
        context = v3.callback_context(client)
        self.assertIs(context.client, client)
        self.assertEqual(commands, [])
        self.assertIs(context.dispatch(0x100000001, 0x200000002), ack)
        self.assertEqual(context.status_getter(), {**observed, "callback_error": 0})
        self.assertEqual(commands, ["interface-callback 0x100000001 0x200000002", "world-status"])
        self.assertTrue(context.process_matches(SimpleNamespace(pid=42)))
        self.assertFalse(context.process_matches(SimpleNamespace(pid=True)))
        self.assertFalse(context.process_matches(SimpleNamespace(pid=43)))
        client.pid = 43
        self.assertFalse(context.process_matches(SimpleNamespace(pid=42)))

    def test_custom_session_status_source_keeps_precedence_without_world_wire_query(self):
        client = SimpleNamespace(pid=42, request=Mock())
        status = {"status": "ok", "request_id": 8, "completed_request_id": 7, "world_error": 0}
        source = Mock(return_value=status)
        context = v3.callback_context(client, status_getter=source)
        self.assertIs(context.client, client)
        self.assertEqual(context.status_getter(), {**status, "callback_error": 0})
        source.assert_called_once_with()
        client.request.assert_not_called()
        self.assertNotIn("callback_error", status)


if __name__ == "__main__":
    unittest.main()
