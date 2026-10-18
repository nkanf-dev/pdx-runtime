"""CPU-only original-thread ownership/continuation fixtures (IDA Python 3.14).

Run directly with the project's IDA Python interpreter. No process is launched,
opened, injected, or inspected: all native APIs are deterministic fakes.
"""
from __future__ import annotations

import ctypes
import importlib.util
import sys
import types
import unittest
from pathlib import Path

from pdx_runtime import native_bridge as native

HERE = Path(native.__file__).resolve().parent


class FakeAPI:
    def __init__(self, waits=(0,), exits=(42,), module_results=None):
        self.waits = list(waits)
        self.exits = list(exits)
        self.module_results = list(module_results or [])
        self.events = []
        self.free_failures = 0
        self.thread_close_failures = 0
        self.create_error = None
        self.write_error = None
        self.next_thread = 0x200
        self.next_argument = 0x1000

    def open_process(self, access, inherit, pid):
        self.events.append(("open", pid))
        return 0x100

    def allocate(self, process, unused, size, kind, protection):
        address = self.next_argument
        self.next_argument += 0x1000
        self.events.append(("allocate", process, address, size))
        return address

    def write_process_memory(self, process, address, data, size, count):
        self.events.append(("write", process, address, bytes(data)))
        if self.write_error:
            raise self.write_error
        count._obj.value = size
        return True

    def free(self, process, address, size, kind):
        self.events.append(("free", process, address))
        if self.free_failures:
            self.free_failures -= 1
            return False
        return True

    def create_thread(self, process, unused, size, loader, argument, flags, tid):
        self.events.append(("create", process, loader, argument))
        if self.create_error:
            raise self.create_error
        tid._obj.value = self.next_thread + 1
        handle = self.next_thread
        self.next_thread += 0x10
        return handle

    def wait(self, handle, timeout):
        self.events.append(("wait", handle, timeout))
        value = self.waits.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def thread_exit(self, handle, code):
        self.events.append(("exit", handle))
        value = self.exits.pop(0)
        if value is False:
            return False
        if isinstance(value, Exception):
            raise value
        code._obj.value = value
        return True

    def modules(self, pid):
        self.events.append(("modules", pid))
        value = self.module_results.pop(0) if self.module_results else [MODULE]
        if isinstance(value, Exception):
            raise value
        return value

    def close_handle(self, handle):
        self.events.append(("close", handle))
        if handle != 0x100 and self.thread_close_failures:
            self.thread_close_failures -= 1
            return False
        return True

    @staticmethod
    def failure(operation):
        return native.BridgeError(operation, 5)


DLL_PATH = Path(__file__).resolve()  # Existing fixture input, never executed remotely.
MODULE = {"name": DLL_PATH.name, "path": str(DLL_PATH), "base": 0x7FF000001000, "size": 4096}
WAIT_ERROR = native.BridgeError("WaitForSingleObject", 5)


class LibraryLoadTests(unittest.TestCase):
    def process(self, api):
        process = native.RemoteProcess(123, api)
        process.resolve_export = lambda module, symbol: 0x7FF010001000
        return process

    @staticmethod
    def events(api, name):
        return [event for event in api.events if event[0] == name]

    def test_initial_wait_timeout_allows_other_role_then_same_thread_finishes(self):
        other_path = HERE / "native_bridge.py"
        other_module = {**MODULE, "name": other_path.name, "path": str(other_path), "base": MODULE["base"] + 0x10000}
        api = FakeAPI((258, 258, 0, 0), (43, 42), ([other_module], [MODULE]))
        process = self.process(api)
        with self.assertRaises(native.LibraryLoadPending) as caught:
            process.load_library(DLL_PATH, timeout_ms=0)
        attempt = caught.exception.native_load
        self.assertEqual(attempt.thread.handle, 0x200)
        self.assertFalse(self.events(api, "free"))
        self.assertFalse(self.events(api, "close"))
        # The caller can keep working on another role while this one is pending.
        self.assertEqual(process.load_library(other_path, timeout_ms=0)["base"], other_module["base"])
        self.assertIs(process.pending_library_loads[0], attempt)
        self.assertEqual(attempt.resume()["base"], MODULE["base"])
        self.assertEqual(self.events(api, "wait"), [("wait", 0x200, 0), ("wait", 0x200, 0),
                                                   ("wait", 0x210, 0), ("wait", 0x200, 0)])
        self.assertEqual(len(self.events(api, "create")), 2)
        self.assertEqual(self.events(api, "free"), [("free", 0x100, 0x2000), ("free", 0x100, 0x1000)])
        self.assertFalse(process.pending_library_loads)
        process.close()

    def test_original_wait_error_recovers_with_original_poll(self):
        api = FakeAPI((WAIT_ERROR, 0))
        process = self.process(api)
        result = process.load_library(DLL_PATH)
        self.assertEqual(result, {**MODULE, "loader_address": 0x7FF010001000, "loader_exit_dword": 42})
        self.assertEqual(self.events(api, "wait"), [("wait", 0x200, native.INFINITE), ("wait", 0x200, 0)])
        self.assertEqual(len(self.events(api, "create")), 1)
        self.assertEqual(self.events(api, "free"), [("free", 0x100, 0x1000)])
        self.assertEqual(self.events(api, "close"), [("close", 0x200)])
        self.assertFalse(process.pending_library_loads)
        process.close()

    def test_get_exit_error_recovers_on_original_handle(self):
        api = FakeAPI((0, 0), (False, 73))
        process = self.process(api)
        self.assertEqual(process.load_library(DLL_PATH)["loader_exit_dword"], 73)
        self.assertEqual(self.events(api, "exit"), [("exit", 0x200), ("exit", 0x200)])
        self.assertEqual(len(self.events(api, "create")), 1)
        process.close()

    def test_pending_then_same_path_continuation_without_replay(self):
        api = FakeAPI((WAIT_ERROR, 258, 0), (259,))
        process = self.process(api)
        with self.assertRaises(native.LibraryLoadPending) as caught:
            process.load_library(DLL_PATH)
        attempt = caught.exception.native_load
        self.assertIs(attempt, caught.exception.attempt)
        self.assertIs(process.pending_library_loads[0], attempt)
        self.assertEqual(attempt.argument_address, 0x1000)
        self.assertEqual(attempt.thread.handle, 0x200)
        self.assertIsNone(attempt.loader_exit_dword)
        self.assertFalse(self.events(api, "free"))
        self.assertFalse(self.events(api, "close"))
        self.assertFalse(self.events(api, "modules"))
        # A real, signaled exit 259 is a completed DWORD, not STILL_ACTIVE.
        result = process.load_library(DLL_PATH)
        self.assertEqual(result["loader_exit_dword"], 259)
        self.assertIs(attempt.resume(), result)
        self.assertEqual(len(self.events(api, "create")), 1)
        self.assertEqual(len(self.events(api, "free")), 1)
        self.assertEqual(self.events(api, "close"), [("close", 0x200)])
        process.close()

    def test_observation_error_retains_thread_input_and_actionable_state(self):
        api = FakeAPI((WAIT_ERROR, WAIT_ERROR, 0))
        process = self.process(api)
        with self.assertRaises(native.LibraryLoadPending) as caught:
            process.load_library(DLL_PATH)
        attempt = caught.exception.native_load
        self.assertEqual(caught.exception.as_dict()["state"], "waiting")
        self.assertEqual(attempt.thread.handle, 0x200)
        self.assertEqual(attempt.argument_address, 0x1000)
        self.assertIs(attempt.last_error, WAIT_ERROR)
        self.assertFalse(self.events(api, "free"))
        self.assertFalse(self.events(api, "close"))
        self.assertEqual(attempt.resume()["base"], MODULE["base"])
        process.close()

    def test_context_close_keeps_original_process_for_later_cleanup(self):
        api = FakeAPI((WAIT_ERROR, 258, 258, 0))
        with self.assertRaises(native.LibraryLoadPending) as caught:
            with self.process(api) as process:
                process.load_library(DLL_PATH)
        attempt = caught.exception.native_load
        self.assertIs(attempt.process, process)
        self.assertEqual(process.handle, 0x100)
        self.assertFalse(self.events(api, "free"))
        self.assertFalse(self.events(api, "close"))
        # Repeated close must not discard ownership either.
        process.close()
        with self.assertRaises(native.LibraryLoadPending) as again:
            attempt.resume()
        self.assertIs(again.exception.native_load, attempt)
        self.assertFalse(self.events(api, "close"))
        result = attempt.resume()
        self.assertEqual(result["base"], MODULE["base"])
        self.assertIsNone(process.handle)
        self.assertIsNone(attempt.argument_address)
        self.assertIsNone(attempt.thread.handle)
        self.assertFalse(process.pending_library_loads)
        self.assertEqual(self.events(api, "free"), [("free", 0x100, 0x1000)])
        self.assertEqual(self.events(api, "close"), [("close", 0x200), ("close", 0x100)])

    def test_module_visibility_never_observed_until_thread_known_done(self):
        api = FakeAPI((WAIT_ERROR, 258, 258, 0))
        process = self.process(api)
        with self.assertRaises(native.LibraryLoadPending) as caught:
            process.load_library(DLL_PATH)
        with self.assertRaises(native.LibraryLoadPending):
            caught.exception.native_load.resume()
        self.assertFalse(self.events(api, "modules"))
        self.assertFalse(self.events(api, "free"))
        self.assertEqual(caught.exception.native_load.resume()["base"], MODULE["base"])
        process.close()

    def test_completed_module_enumeration_error_resumes_without_loader_replay(self):
        error = native.BridgeError("modules", 299)
        api = FakeAPI(module_results=(error, [MODULE]))
        process = self.process(api)
        with self.assertRaises(native.LibraryLoadPending) as caught:
            process.load_library(DLL_PATH)
        attempt = caught.exception.native_load
        self.assertEqual(attempt.state, "module")
        self.assertEqual(attempt.loader_exit_dword, 42)
        self.assertIsNone(attempt.thread.handle)
        self.assertIsNone(attempt.argument_address)
        self.assertEqual(attempt.resume()["base"], MODULE["base"])
        self.assertEqual(len(self.events(api, "wait")), 1)
        self.assertEqual(len(self.events(api, "create")), 1)
        self.assertEqual(len(self.events(api, "free")), 1)
        process.close()

    def test_completed_cleanup_error_keeps_original_process_until_retry(self):
        api = FakeAPI()
        api.free_failures = 1
        with self.assertRaises(native.LibraryLoadPending) as caught:
            with self.process(api) as process:
                process.load_library(DLL_PATH)
        attempt = caught.exception.native_load
        self.assertEqual(attempt.state, "cleanup")
        self.assertEqual(attempt.loader_exit_dword, 42)
        self.assertEqual(attempt.argument_address, 0x1000)
        self.assertEqual(attempt.thread.handle, 0x200)
        self.assertEqual(process.handle, 0x100)
        self.assertEqual(attempt.resume()["base"], MODULE["base"])
        self.assertIsNone(process.handle)
        self.assertEqual(len(self.events(api, "create")), 1)
        self.assertEqual(len(self.events(api, "wait")), 1)
        self.assertEqual(self.events(api, "free"), [("free", 0x100, 0x1000)] * 2)

    def test_completed_thread_close_error_retains_original_handle_for_retry(self):
        api = FakeAPI()
        api.thread_close_failures = 1
        with self.assertRaises(native.LibraryLoadPending) as caught:
            with self.process(api) as process:
                process.load_library(DLL_PATH)
        attempt = caught.exception.native_load
        self.assertEqual(attempt.state, "cleanup")
        self.assertEqual(attempt.loader_exit_dword, 42)
        self.assertIsNone(attempt.argument_address)
        self.assertEqual(attempt.thread.handle, 0x200)
        self.assertEqual(process.handle, 0x100)
        self.assertEqual(attempt.resume()["base"], MODULE["base"])
        self.assertIsNone(process.handle)
        self.assertEqual(self.events(api, "close"), [("close", 0x200), ("close", 0x200), ("close", 0x100)])
        self.assertEqual(len(self.events(api, "free")), 1)
        self.assertEqual(len(self.events(api, "wait")), 1)
        self.assertEqual(len(self.events(api, "create")), 1)

    def test_absent_module_after_known_completion_keeps_raw_failure(self):
        api = FakeAPI(module_results=([],))
        process = self.process(api)
        with self.assertRaises(native.BridgeError) as caught:
            process.load_library(DLL_PATH)
        self.assertNotIsInstance(caught.exception, native.LibraryLoadPending)
        self.assertEqual(caught.exception.details["loader_exit_dword"], 42)
        self.assertFalse(process.pending_library_loads)
        self.assertEqual(len(self.events(api, "free")), 1)
        self.assertEqual(self.events(api, "close"), [("close", 0x200)])
        process.close()

    def test_new_completed_request_still_calls_native_loader_again(self):
        api = FakeAPI((0, 0), (42, 43))
        process = self.process(api)
        self.assertEqual(process.load_library(DLL_PATH)["loader_exit_dword"], 42)
        self.assertEqual(process.load_library(DLL_PATH)["loader_exit_dword"], 43)
        self.assertEqual(len(self.events(api, "create")), 2)
        self.assertEqual(len(self.events(api, "free")), 2)
        self.assertEqual(self.events(api, "close"), [("close", 0x200), ("close", 0x210)])
        process.close()

    def test_registered_before_dispatch_and_predispatch_failure_cleans(self):
        api = FakeAPI()
        process = self.process(api)
        failure = native.BridgeError("CreateRemoteThread", 5)
        def create(*args):
            self.assertEqual(len(process.pending_library_loads), 1)
            self.assertEqual(process.pending_library_loads[0].argument_address, 0x1000)
            raise failure
        api.create_thread = create
        with self.assertRaises(native.BridgeError) as caught:
            process.load_library(DLL_PATH)
        self.assertIs(caught.exception, failure)
        self.assertFalse(process.pending_library_loads)
        self.assertFalse(self.events(api, "wait"))
        self.assertEqual(self.events(api, "free"), [("free", 0x100, 0x1000)])
        process.close()

    def test_all_pending_finish_before_deferred_process_close_even_last_failure(self):
        api = FakeAPI((WAIT_ERROR, 258, WAIT_ERROR, 258, 0, 0), (42, 43), ([MODULE], []))
        process = self.process(api)
        attempts = []
        for path in (DLL_PATH, HERE / "native_bridge.py"):
            with self.assertRaises(native.LibraryLoadPending) as caught:
                process.load_library(path)
            attempts.append(caught.exception.native_load)
        process.close()
        self.assertEqual(len(process.pending_library_loads), 2)
        self.assertEqual(attempts[0].resume()["base"], MODULE["base"])
        self.assertEqual(process.handle, 0x100)
        self.assertEqual(len(process.pending_library_loads), 1)
        with self.assertRaises(native.BridgeError) as failed:
            attempts[1].resume()
        self.assertNotIsInstance(failed.exception, native.LibraryLoadPending)
        self.assertIsNone(process.handle)
        self.assertFalse(process.pending_library_loads)
        self.assertEqual(api.events[-1], ("close", 0x100))
        self.assertEqual(self.events(api, "close"), [("close", 0x200), ("close", 0x210), ("close", 0x100)])
        with self.assertRaises(native.BridgeError) as failed_again:
            attempts[1].resume()
        self.assertIs(failed.exception, failed_again.exception)
        process.close()
        self.assertEqual(len(self.events(api, "free")), 2)
        self.assertEqual(len(self.events(api, "create")), 2)

    def test_context_close_waits_for_module_observation_retry_after_cleanup(self):
        api = FakeAPI(module_results=(native.BridgeError("modules", 299), [MODULE]))
        with self.assertRaises(native.LibraryLoadPending) as caught:
            with self.process(api) as process:
                process.load_library(DLL_PATH)
        attempt = caught.exception.native_load
        self.assertEqual(attempt.state, "module")
        self.assertEqual(process.handle, 0x100)
        self.assertIsNone(attempt.argument_address)
        self.assertIsNone(attempt.thread.handle)
        attempt.resume()
        self.assertIsNone(process.handle)
        self.assertEqual(api.events[-2:], [("modules", 123), ("close", 0x100)])
        process.close()
        attempt.resume()
        self.assertEqual(len(self.events(api, "free")), 1)
        self.assertEqual(len(self.events(api, "create")), 1)

    def test_predispatch_cleanup_retry_never_dispatches_or_claims_visible_module(self):
        api = FakeAPI()
        api.write_error = native.BridgeError("WriteProcessMemory", 299)
        api.free_failures = 1
        process = self.process(api)
        with self.assertRaises(native.LibraryLoadPending) as caught:
            process.load_library(DLL_PATH)
        attempt = caught.exception.native_load
        with self.assertRaises(native.BridgeError) as failed:
            attempt.resume()
        self.assertIs(failed.exception, api.write_error)
        self.assertEqual(attempt.state, "failed")
        self.assertFalse(process.pending_library_loads)
        self.assertFalse(self.events(api, "create"))
        self.assertFalse(self.events(api, "modules"))
        process.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
