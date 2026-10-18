"""Focused OS-memory primitive mocks; no process, page protection or cache is touched."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import unittest
from unittest.mock import Mock, call

from pdx_runtime.native_bridge import (
    BridgeError, MEMORY_BASIC_INFORMATION, MemoryRegion, RemoteProcess,
)

HANDLE = 0x123456789ABC


class NativeProcessMemoryTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.process = RemoteProcess.__new__(RemoteProcess)
        self.process.api, self.process.pid, self.process.handle = self.api, 123, HANDLE

    def test_query_copies_raw_x64_region_and_preserves_free_zero_values_and_failure(self):
        native_values = []

        def query(handle, address, output, size):
            self.assertEqual((handle, address, size), (HANDLE, 0x123456789000, 48))
            self.assertEqual(ctypes.sizeof(MEMORY_BASIC_INFORMATION), 48)
            info = ctypes.cast(output, ctypes.POINTER(MEMORY_BASIC_INFORMATION)).contents
            info.BaseAddress, info.AllocationBase = 0x123456780000, 0x123450000000
            info.AllocationProtect, info.PartitionId = 0x104, 7
            info.RegionSize, info.State, info.Protect, info.Type = 0x3000, 0x1000, 0x20, 0x20000
            native_values.append(info)
            return size

        self.api.query_memory.side_effect = query
        region = self.process.query_memory(0x123456789000)
        self.assertEqual(region, MemoryRegion(0x123456780000, 0x123450000000,
                                              0x104, 7, 0x3000, 0x1000, 0x20, 0x20000))
        native_values[0].Protect = 0x04
        self.assertEqual(region.protection, 0x20)

        def query_free(handle, address, output, size):
            info = ctypes.cast(output, ctypes.POINTER(MEMORY_BASIC_INFORMATION)).contents
            info.BaseAddress, info.AllocationBase = 0x60000000, None
            info.AllocationProtect, info.PartitionId = 0, 0
            info.RegionSize, info.State, info.Protect, info.Type = 0x10000, 0x10000, 0, 0
            return size

        self.api.query_memory.side_effect = query_free
        self.assertEqual(self.process.query_memory(0x60000000),
                         MemoryRegion(0x60000000, 0, 0, 0, 0x10000, 0x10000, 0, 0))
        failure = BridgeError("VirtualQueryEx", 87)
        self.api.query_memory.side_effect = None
        self.api.query_memory.return_value = 0
        self.api.failure.return_value = failure
        with self.assertRaises(BridgeError) as caught:
            self.process.query_memory(0x60000000)
        self.assertIs(caught.exception, failure)
        self.api.failure.assert_called_once_with("VirtualQueryEx")
        self.api.open_process.assert_not_called()

    def test_protect_passes_exact_range_flags_and_returns_first_page_old_flags_without_restore(self):
        observed = []

        def protect(handle, address, size, protection, old_output):
            observed.append((handle, address, size, protection))
            ctypes.cast(old_output, ctypes.POINTER(wintypes.DWORD)).contents.value = 0x104
            return True

        self.api.protect_memory.side_effect = protect
        self.assertEqual(self.process.protect(0x123456789123, 0x2345, 0x120), 0x104)
        self.assertEqual(observed, [(HANDLE, 0x123456789123, 0x2345, 0x120)])
        self.api.protect_memory.assert_called_once()
        self.api.query_memory.assert_not_called()
        self.api.flush_instruction_cache.assert_not_called()
        failure = BridgeError("VirtualProtectEx", 5)
        self.api.protect_memory.side_effect = None
        self.api.protect_memory.return_value = False
        self.api.failure.return_value = failure
        with self.assertRaises(BridgeError) as caught:
            self.process.protect(0x123456789123, 0x2345, 0x120)
        self.assertIs(caught.exception, failure)
        self.api.failure.assert_called_once_with("VirtualProtectEx")
        self.assertEqual(self.api.protect_memory.call_count, 2)

    def test_instruction_cache_flush_uses_exact_default_or_explicit_range_and_propagates_failure(self):
        self.api.flush_instruction_cache.return_value = True
        self.assertIsNone(self.process.flush_instruction_cache())
        self.assertIsNone(self.process.flush_instruction_cache(0x123456789123, 0x2345))
        self.assertEqual(self.api.flush_instruction_cache.call_args_list,
                         [call(HANDLE, 0, 0), call(HANDLE, 0x123456789123, 0x2345)])
        failure = BridgeError("FlushInstructionCache", 6)
        self.api.flush_instruction_cache.return_value = False
        self.api.failure.return_value = failure
        with self.assertRaises(BridgeError) as caught:
            self.process.flush_instruction_cache(0x123456789123, 0x2345)
        self.assertIs(caught.exception, failure)
        self.api.failure.assert_called_once_with("FlushInstructionCache")
        self.api.write_process_memory.assert_not_called()
        self.api.protect_memory.assert_not_called()
        self.api.create_thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
