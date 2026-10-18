"""CPU-only checks for the shared Win32 seam; no native or game calls."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pdx_runtime import ck3_live_query as ck3
from pdx_runtime import live_query as live
from pdx_runtime import windows


class SharedWindowsBoundaryTests(unittest.TestCase):

    def test_shared_api_requires_explicit_executable(self):
        api = windows.WindowsAPI.__new__(windows.WindowsAPI)
        with self.assertRaises(TypeError):
            api.find_processes()


class WindowsAPITests(unittest.TestCase):
    def make_api(self):
        api = windows.WindowsAPI.__new__(windows.WindowsAPI)
        api.create_snapshot = Mock(return_value=0x1234567800000010)
        api.close_handle = Mock(return_value=True)
        return api

    def test_api_binding_uses_only_mock_kernel_and_exact_pointer_types(self):
        kernel = Mock()
        with patch.object(windows.os, "name", "nt"), \
                patch.object(windows.ctypes, "WinDLL", return_value=kernel, create=True) as loader:
            api = windows.WindowsAPI()
        loader.assert_called_once_with("kernel32", use_last_error=True)
        self.assertIs(api.open_process, kernel.OpenProcess)
        self.assertEqual(api.open_process.argtypes,
                         [windows.wintypes.DWORD, windows.wintypes.BOOL, windows.wintypes.DWORD])
        self.assertIs(api.read_process_memory.argtypes[-1], ctypes.POINTER(ctypes.c_size_t))
        self.assertIs(api.process_first.argtypes[-1], ctypes.POINTER(windows.PROCESSENTRY32W))
        self.assertIs(api.module_first.argtypes[-1], ctypes.POINTER(windows.MODULEENTRY32W))

    def test_snapshot_failure_is_not_a_valid_handle(self):
        api = self.make_api()
        api.create_snapshot.return_value = windows.INVALID_HANDLE_VALUE
        with patch.object(windows.ctypes, "get_last_error", return_value=5, create=True):
            with self.assertRaises(windows.QueryFailure) as caught:
                api.snapshot(windows.TH32CS_SNAPPROCESS, 0)
        self.assertEqual(caught.exception.as_dict(), {
            "status": "windows_api_failed", "operation": "CreateToolhelp32Snapshot", "winerror": 5})
        api.close_handle.assert_not_called()

    def test_find_processes_matches_explicit_name_case_insensitively_and_closes_snapshot(self):
        api = self.make_api()
        rows = iter(((10, "other.exe"), (0xFEDCBA98, "OwNeD_HoSt.ExE"), (42, "owned_host.exe")))
        def advance(handle, output):
            self.assertEqual(handle, api.create_snapshot.return_value)
            self.assertEqual(output._obj.dwSize, ctypes.sizeof(windows.PROCESSENTRY32W))
            row = next(rows, None)
            if row is None:
                return False
            output._obj.th32ProcessID, output._obj.szExeFile = row
            return True
        api.process_first = Mock(side_effect=advance)
        api.process_next = Mock(side_effect=advance)
        with patch.object(windows.ctypes, "get_last_error", return_value=windows.ERROR_NO_MORE_FILES, create=True):
            self.assertEqual(api.find_processes("owned_host.exe"), [0xFEDCBA98, 42])
        api.create_snapshot.assert_called_once_with(windows.TH32CS_SNAPPROCESS, 0)
        api.close_handle.assert_called_once_with(api.create_snapshot.return_value)

    def test_enumeration_error_closes_snapshot_and_keeps_native_error(self):
        api = self.make_api()
        api.process_first = Mock(return_value=False)
        with patch.object(windows.ctypes, "get_last_error", return_value=299, create=True):
            with self.assertRaises(windows.QueryFailure) as caught:
                api.find_processes("owned_host.exe")
        self.assertEqual(caught.exception.details["winerror"], 299)
        api.close_handle.assert_called_once_with(api.create_snapshot.return_value)

    def test_main_module_keeps_full_base_path_size_and_closes_snapshot(self):
        api = self.make_api()
        base = 0xFEDCBA9876543210
        path = r"C:\owned host\测试\host.exe"
        def first(handle, output):
            entry = output._obj
            self.assertEqual(entry.dwSize, ctypes.sizeof(windows.MODULEENTRY32W))
            entry.szModule, entry.szExePath = "host.exe", path
            entry.modBaseAddr = ctypes.cast(ctypes.c_void_p(base), ctypes.POINTER(ctypes.c_ubyte))
            entry.modBaseSize = 0xFEDCBA98
            return True
        api.module_first = Mock(side_effect=first)
        self.assertEqual(api.main_module(0xF0000001), {
            "name": "host.exe", "path": path, "base": base, "size": 0xFEDCBA98})
        api.create_snapshot.assert_called_once_with(
            windows.TH32CS_SNAPMODULE | windows.TH32CS_SNAPMODULE32, 0xF0000001)
        api.close_handle.assert_called_once_with(api.create_snapshot.return_value)

    def test_main_module_error_still_closes_snapshot(self):
        api = self.make_api()
        api.module_first = Mock(return_value=False)
        with patch.object(windows.ctypes, "get_last_error", return_value=6, create=True):
            with self.assertRaises(windows.QueryFailure) as caught:
                api.main_module(42)
        self.assertEqual(caught.exception.details["operation"], "Module32FirstW")
        api.close_handle.assert_called_once_with(api.create_snapshot.return_value)


class WindowsProcessTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.open_process.return_value = 0xFEDCBA9876543210
        self.process = windows.WindowsProcess(self.api, 0xF0000001)

    def test_read_only_access_and_exact_original_handle_ownership(self):
        self.api.open_process.assert_called_once_with(
            windows.PROCESS_VM_READ | windows.PROCESS_QUERY_LIMITED_INFORMATION, False, 0xF0000001)
        self.assertEqual(self.process.handle, 0xFEDCBA9876543210)
        self.process.close()
        self.process.close()
        self.api.close_handle.assert_called_once_with(0xFEDCBA9876543210)
        self.assertIsNone(self.process.handle)

    def test_open_failure_is_not_constructed_as_success(self):
        self.api.open_process.return_value = 0
        self.api.failure.return_value = windows.QueryFailure("windows_api_failed", operation="OpenProcess")
        with self.assertRaises(windows.QueryFailure):
            windows.WindowsProcess(self.api, 42)
        self.api.failure.assert_called_once_with("OpenProcess")

    def test_image_path_is_observed_through_original_handle(self):
        expected = r"C:\owned host\测试\host.exe"
        def image(handle, flags, output, length):
            self.assertEqual(handle, self.process.handle)
            self.assertEqual(flags, 0)
            self.assertEqual(length._obj.value, 32768)
            output.value = expected
            length._obj.value = len(expected)
            return True
        self.api.query_image.side_effect = image
        self.assertEqual(self.process.image_path(), expected)
        self.api.open_process.assert_called_once()

    def test_read_returns_owned_bytes_preserving_zero_and_all_raw_bits(self):
        payload = bytes.fromhex("0000ff8076543210")
        def read(handle, address, output, size, count):
            self.assertEqual((handle, address, size), (self.process.handle, 0xFEDCBA9800000000, 8))
            ctypes.memmove(output, payload, size)
            count._obj.value = size
            return True
        self.api.read_process_memory.side_effect = read
        data = self.process.read(0xFEDCBA9800000000, 8)
        self.assertIsInstance(data, bytes)
        self.assertEqual(data, payload)
        self.api.close_handle.assert_not_called()

    def test_short_or_failed_reads_keep_error_and_byte_counts(self):
        for success, received in ((True, 3), (False, 0), (False, 8)):
            with self.subTest(success=success, received=received):
                def read(handle, address, output, size, count):
                    count._obj.value = received
                    return success
                self.api.read_process_memory.side_effect = read
                with patch.object(windows.ctypes, "get_last_error", return_value=299, create=True):
                    with self.assertRaises(windows.ReadFailure) as caught:
                        self.process.read(0x1000, 8)
                self.assertEqual(caught.exception.as_dict(), {
                    "status": "read_failed", "operation": "ReadProcessMemory", "winerror": 299,
                    "requested_bytes": 8, "received_bytes": received})


class SharedUtilitiesAndSamplerTests(unittest.TestCase):
    def test_file_hash_uses_all_binary_content(self):
        content = b"\0\xff" + b"owned-host" * 110000
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.exe"
            path.write_bytes(content)
            self.assertEqual(windows.file_sha256(str(path)), hashlib.sha256(content).hexdigest())

    def test_generic_cli_ranges_preserve_zero_address_and_full_width(self):
        self.assertEqual(windows.positive_pid("0xffffffff"), 0xFFFFFFFF)
        self.assertEqual(windows.address_argument("0"), 0)
        self.assertEqual(windows.address_argument("0xffffffffffffffff"), 0xFFFFFFFFFFFFFFFF)
        self.assertEqual(windows.size_argument(str(windows.MAX_RAW_BYTES)), windows.MAX_RAW_BYTES)
        for function, value in ((windows.positive_pid, "0"),
                                (windows.positive_pid, "0x100000000"),
                                (windows.address_argument, "-1"),
                                (windows.address_argument, "0x10000000000000000"),
                                (windows.size_argument, "0"),
                                (windows.size_argument, str(windows.MAX_RAW_BYTES + 1))):
            with self.subTest(function=function.__name__, value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    function(value)

    def test_sampler_discovery_stays_game_specific_and_raw_read_has_no_hash_gate(self):
        for sampler, executable in ((live, "victoria3.exe"), (ck3, "ck3.exe")):
            with self.subTest(sampler=sampler.__name__):
                api = Mock()
                api.find_processes.return_value = [0xF0000001]
                api.main_module.return_value = {
                    "name": executable, "path": "exact module path", "base": 0xFEDCBA9800000000, "size": 4096}
                process = Mock()
                process.image_path.return_value = "exact process path"
                process.read.return_value = b"\0\xff"
                with patch.object(sampler, "WindowsAPI", return_value=api), \
                        patch.object(sampler, "WindowsProcess", return_value=process) as constructor, \
                        patch.object(sampler, "file_sha256", side_effect=AssertionError("raw reads do not hash layouts")):
                    result = sampler.query(None, 20, None, read_rva=0, size=2)
                api.find_processes.assert_called_once_with(executable)
                constructor.assert_called_once_with(api, 0xF0000001)
                process.read.assert_called_once_with(0xFEDCBA9800000000, 2)
                process.close.assert_called_once_with()
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["process"], {"pid": 0xF0000001, "path": "exact process path"})
                self.assertEqual(result["raw_read"]["bytes_hex"], "00ff")
                self.assertIs(result["consistent_snapshot"], False)
                self.assertIs(result["native_lock_protected"], False)

    def test_build_mismatch_still_hashes_exact_module_path_and_closes_process(self):
        for sampler in (live, ck3):
            with self.subTest(sampler=sampler.__name__):
                api, process = Mock(), Mock()
                api.main_module.return_value = {
                    "name": "owned.exe", "path": "exact module path", "base": 0x100000000, "size": 4096}
                process.image_path.return_value = "exact process path"
                with patch.object(sampler, "WindowsAPI", return_value=api), \
                        patch.object(sampler, "WindowsProcess", return_value=process), \
                        patch.object(sampler, "file_sha256", return_value="wrong-build") as digest:
                    result = sampler.query(42, 20, None)
                digest.assert_called_once_with("exact module path")
                self.assertEqual(result["status"], "unsupported_build")
                process.read.assert_not_called()
                process.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
