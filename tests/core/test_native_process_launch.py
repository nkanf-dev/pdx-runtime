"""Focused CreateProcessW composition tests; no process or native API is started."""
from __future__ import annotations

import ctypes
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from pdx_runtime.native_bridge import (
    BridgeError, NativeBridge, PROCESS_INFORMATION, RemoteProcess, STARTUPINFOW,
)

PROCESS_HANDLE = 0x123456789ABC
THREAD_HANDLE = 0x23456789ABCD


class NativeProcessLaunchTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.calls = []
        self.api.create_process.side_effect = self.create_process

    def create_process(self, executable, command, process_security, thread_security,
                       inherit_handles, flags, environment, cwd, startup, information):
        self.calls.append((executable, command.value, process_security, thread_security,
                           inherit_handles, flags, environment, cwd))
        native_startup = ctypes.cast(startup, ctypes.POINTER(STARTUPINFOW)).contents
        self.assertEqual(native_startup.cb, ctypes.sizeof(STARTUPINFOW))
        self.assertEqual(ctypes.sizeof(STARTUPINFOW), 104)
        self.assertEqual(ctypes.sizeof(PROCESS_INFORMATION), 24)
        native_information = ctypes.cast(information, ctypes.POINTER(PROCESS_INFORMATION)).contents
        native_information.hProcess = PROCESS_HANDLE
        native_information.hThread = THREAD_HANDLE
        native_information.dwProcessId = 1234
        native_information.dwThreadId = 4321
        return True

    def test_explicit_unicode_application_cwd_and_crt_argument_quoting(self):
        executable = Path("工具目录/程序 file.exe")
        cwd = Path("工作目录 空格")
        arguments = ("", "plain", "two words", 'say"hello', "a b\\", "目录")
        process = RemoteProcess.launch(executable, iter(arguments), cwd=cwd,
                                       creation_flags=0x20, api=self.api)
        expected = ('"' + str(executable.resolve()) + '" '
                    + '"" plain "two words" say\\"hello "a b\\\\" 目录')
        self.assertEqual(self.calls, [(str(executable.resolve()), expected, None, None,
                                      False, 0x20, None, str(cwd.resolve()))])
        self.assertEqual(process.arguments, arguments)
        self.assertEqual(process.command_line, expected)
        self.assertEqual(process.executable, str(executable.resolve()))
        self.api.open_process.assert_not_called()

    def test_created_handle_is_retained_primary_thread_is_closed_and_no_actions_are_added(self):
        executable = Path("plain.exe")
        with patch.object(RemoteProcess, "load_library") as load_library, \
                patch.object(NativeBridge, "load") as load_bridge:
            process = RemoteProcess.launch(executable, api=self.api)
        self.assertIs(process.api, self.api)
        self.assertEqual((process.pid, process.handle, process.primary_thread_id),
                         (1234, PROCESS_HANDLE, 4321))
        self.assertEqual(self.calls[0][1], str(executable.resolve()))
        self.assertEqual(self.calls[0][4:8], (False, 0, None, None))
        self.assertEqual(process.arguments, ())
        self.api.close_handle.assert_called_once_with(THREAD_HANDLE)
        self.api.open_process.assert_not_called()
        self.api.create_thread.assert_not_called()
        self.api.create_file.assert_not_called()
        load_library.assert_not_called()
        load_bridge.assert_not_called()
        process.close()
        process.close()
        self.assertEqual([call.args[0] for call in self.api.close_handle.call_args_list],
                         [THREAD_HANDLE, PROCESS_HANDLE])
        self.assertIsNone(process.handle)

    def test_create_failure_raises_native_failure_without_fake_process(self):
        failure = BridgeError("CreateProcessW", 2)
        self.api.create_process.side_effect = None
        self.api.create_process.return_value = False
        self.api.failure.return_value = failure
        with self.assertRaises(BridgeError) as caught:
            RemoteProcess.launch("missing.exe", api=self.api)
        self.assertIs(caught.exception, failure)
        self.api.failure.assert_called_once_with("CreateProcessW")
        self.api.close_handle.assert_not_called()
        self.api.open_process.assert_not_called()

    def test_invalid_command_text_argument_types_and_nul_are_rejected_before_native_create(self):
        invalid_arguments = (("bare command", TypeError), (b"bytes", TypeError),
                             ((1,), TypeError), (("embedded\0nul",), ValueError))
        for arguments, error_type in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(error_type):
                    RemoteProcess.launch("app.exe", arguments, api=self.api)
        for value, kwargs in (("bad\0app.exe", {}), ("app.exe", {"cwd": "bad\0cwd"})):
            with self.subTest(value=value, kwargs=kwargs):
                with self.assertRaises(ValueError):
                    RemoteProcess.launch(value, api=self.api, **kwargs)
        self.api.create_process.assert_not_called()
        self.api.close_handle.assert_not_called()
        self.api.open_process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
