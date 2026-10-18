"""Minimal native bridge unit test plus opt-in real Windows host tests.

Set PDX_NATIVE_TEST_PID/DLL/SAMPLE to a self-owned bridge_host instance.
No test starts or targets a game process.
"""
from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from pdx_runtime.native_bridge import (NativeBridge, RemoteProcess, BridgeError,
                                       MAX_RAW)


class NativeBridgeTests(unittest.TestCase):
    def test_raw_methods_compose_public_request_without_layout_gate(self):
        bridge = NativeBridge.__new__(NativeBridge)
        with patch.object(bridge, "request", return_value={"status": "ok", "hex": "504458", "bytes": 3}) as request:
            self.assertEqual(bridge.read(0xFEDCBA9876543210, 3), b"PDX")
            request.assert_called_once_with("read 0xfedcba9876543210 3")
        with patch.object(bridge, "request", return_value={"status": "ok", "bytes": 3}) as request:
            self.assertEqual(bridge.write(0xFEDCBA9876543210, b"PDX"), 3)
            request.assert_called_once_with("write 0xfedcba9876543210 504458")


class ProcessObservationTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.open_process.return_value = 0x1234
        self.process = RemoteProcess(42, api=self.api)

    def test_poll_uses_retained_handle_without_waiting_or_pid_lookup(self):
        self.api.wait.return_value = 258
        self.assertIsNone(self.process.poll())
        self.api.wait.assert_called_once_with(0x1234, 0)
        self.api.process_exit.assert_not_called()
        self.api.open_process.assert_called_once()
        self.assertTrue(self.api.open_process.call_args.args[0] & 0x00100000)

    def test_signaled_exit_259_is_not_confused_with_running(self):
        self.api.wait.return_value = 0
        def exit_code(handle, output):
            self.assertEqual(handle, 0x1234)
            output._obj.value = 259
            return True
        self.api.process_exit.side_effect = exit_code
        self.assertEqual(self.process.poll(), 259)
        self.api.close_handle.assert_not_called()
        self.process.close()
        with self.assertRaises(RuntimeError):
            self.process.poll()
        self.api.close_handle.assert_called_once_with(0x1234)

    def test_native_observation_errors_are_not_process_completion(self):
        self.api.wait.return_value = 0xffffffff
        self.api.failure.return_value = BridgeError("WaitForSingleObject(process)", 6)
        with self.assertRaises(BridgeError) as caught:
            self.process.poll()
        self.assertEqual(caught.exception.winerror, 6)
        self.api.wait.return_value = 0
        self.api.process_exit.return_value = False
        self.api.failure.return_value = BridgeError("GetExitCodeProcess", 5)
        with self.assertRaises(BridgeError) as caught:
            self.process.poll()
        self.assertEqual(caught.exception.winerror, 5)


@unittest.skipUnless(os.name == "nt" and os.getenv("PDX_NATIVE_TEST_PID"), "requires an explicit self-owned native host")
class NativeHostTests(unittest.TestCase):
    def test_remote_load_status_raw_memory_and_reconnect(self):
        pid = int(os.environ["PDX_NATIVE_TEST_PID"])
        sample = int(os.environ["PDX_NATIVE_TEST_SAMPLE"], 0)
        dll = Path(os.environ["PDX_NATIVE_TEST_DLL"])
        bridge = NativeBridge.load(pid, dll)
        with bridge:
            status = bridge.status()
            self.assertEqual(status["pid"], pid)
            self.assertEqual(status["protocol"], 1)
            self.assertEqual(status["error"], 0)
            self.assertEqual(status["max_raw"], MAX_RAW)
            self.assertEqual(int(status["dll_base"], 16), bridge.load_info["base"])
            self.assertGreater(bridge.load_info["base"], 0xFFFFFFFF)
            self.assertEqual(bridge.load_info["loader_exit_dword"], bridge.load_info["base"] & 0xFFFFFFFF)
            self.assertEqual(bridge.read(int(status["main_module_base"], 16), 2), b"MZ")
            self.assertEqual(bridge.read(sample, 8), bytes.fromhex("50445800783634ff"))
            self.assertEqual(bridge.write(sample, b"TEST"), 4)
            self.assertEqual(bridge.read(sample, 4), b"TEST")
            bridge.write(sample, b"PDX\0")
            with self.assertRaises(BridgeError) as invalid_read:
                bridge.read(1, 8)
            self.assertEqual(bridge.status()["status"], "ok")
            # The shipped composition uses this same multi-client transport.
            # A second short attach must reuse it while this client stays open.
            with NativeBridge.attach(pid, dll, timeout_ms=5000) as short:
                self.assertIsNone(short.load_info)
                self.assertEqual(short.read(sample, 8), bytes.fromhex("50445800783634ff"))
                self.assertEqual(short.status()["dll_base"], status["dll_base"])
            self.assertEqual(bridge.status()["pid"], pid)
            with RemoteProcess(pid) as process:
                module = next(m for m in process.modules() if m["name"].casefold() == dll.name.casefold())
                initialize = process.resolve_export(module["base"], "PdxRuntimeInitialize")
                self.assertEqual(initialize, bridge.load_info["initialize_address"])
                self.assertTrue(module["base"] <= initialize < module["base"] + module["size"])
                allocation = process.allocate(32)
                try:
                    process.write(allocation, b"external raw")
                    self.assertEqual(bridge.read(allocation, 12), b"external raw")
                    bridge.write(allocation, b"in-process")
                    self.assertEqual(process.read(allocation, 10), b"in-process")
                finally:
                    process.free(allocation)
            print("HOST_STATUS", status)
            print("HOST_LOAD", bridge.load_info)
            print("HOST_READ_WRITE", {"sample_address": hex(sample), "initial": "50445800783634ff", "round_trip": "TEST", "invalid_read_error": invalid_read.exception.winerror})
        with NativeBridge.connect(pid) as again:
            self.assertEqual(again.read(sample, 8), bytes.fromhex("50445800783634ff"))


if __name__ == "__main__":
    unittest.main()
