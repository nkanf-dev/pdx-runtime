"""Focused retained-thread poll mocks; no OS thread or process is created."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import unittest
from unittest.mock import Mock, call

from pdx_runtime.native_bridge import BridgeError, INFINITE, RemoteThread

HANDLE = 0x123456789ABC


class NativeThreadPollTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.thread = RemoteThread(self.api, HANDLE, 123)

    def test_running_then_signaled_preserves_real_exit_259_without_wait_loop_or_close(self):
        self.api.wait.side_effect = [258, 0]

        def get_exit(handle, output):
            self.assertEqual(handle, HANDLE)
            ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = 259
            return True

        self.api.thread_exit.side_effect = get_exit
        self.assertIsNone(self.thread.poll())
        self.api.thread_exit.assert_not_called()
        self.api.wait.assert_called_once_with(HANDLE, 0)
        self.assertEqual(self.thread.poll(), 259)
        self.assertEqual(self.api.wait.call_args_list, [call(HANDLE, 0), call(HANDLE, 0)])
        self.api.thread_exit.assert_called_once()
        self.api.close_handle.assert_not_called()
        self.api.create_thread.assert_not_called()
        self.assertEqual(self.thread.handle, HANDLE)

    def test_explicit_close_is_idempotent_and_closed_poll_does_not_touch_native_api(self):
        self.thread.close()
        self.thread.close()
        self.api.close_handle.assert_called_once_with(HANDLE)
        self.assertIsNone(self.thread.handle)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.thread.poll()
        self.api.wait.assert_not_called()
        self.api.thread_exit.assert_not_called()

    def test_wait_failure_and_exit_query_failure_propagate_without_close_or_replay(self):
        wait_failure = BridgeError("WaitForSingleObject(thread)", 6)
        self.api.wait.return_value = INFINITE
        self.api.failure.return_value = wait_failure
        with self.assertRaises(BridgeError) as caught:
            self.thread.poll()
        self.assertIs(caught.exception, wait_failure)
        self.api.failure.assert_called_once_with("WaitForSingleObject(thread)")
        self.api.wait.assert_called_once_with(HANDLE, 0)
        self.api.thread_exit.assert_not_called()

        self.api.reset_mock()
        self.api.wait.return_value = 0
        self.api.thread_exit.return_value = False
        exit_failure = BridgeError("GetExitCodeThread", 5)
        self.api.failure.return_value = exit_failure
        with self.assertRaises(BridgeError) as caught:
            self.thread.poll()
        self.assertIs(caught.exception, exit_failure)
        self.api.failure.assert_called_once_with("GetExitCodeThread")
        self.api.wait.assert_called_once_with(HANDLE, 0)
        self.api.thread_exit.assert_called_once()
        self.api.close_handle.assert_not_called()
        self.api.create_thread.assert_not_called()
        self.assertEqual(self.thread.handle, HANDLE)


if __name__ == "__main__":
    unittest.main()
