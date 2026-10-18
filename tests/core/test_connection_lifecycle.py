"""CPU-only connection lifecycle regressions. Every OS endpoint/process is fake."""
from pathlib import Path
from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch

from pdx_runtime import native_bridge as native
from pdx_runtime.session import RuntimeSession
from pdx_runtime.v3_game import lifecycle


def module(path="C:/old-install/pdx_runtime.dll", base=0x180000000):
    return {"name": Path(path).name, "path": path, "base": base, "size": 4096}


def process(loaded=()):
    p = Mock(pid=42, pending_library_loads=())
    p.poll.return_value = None
    p.modules.return_value = list(loaded)
    p.resolve_export.side_effect = lambda base, symbol: base + 16
    p.start_thread.return_value = Mock()
    p.start_thread.return_value.poll.return_value = 0
    return p


def client(handle=123):
    api = Mock()
    api.close_handle.return_value = True
    result = native.NativeBridge(42, api=api)
    result.handle = handle
    return result


class PipeDiagnosticsTests(unittest.TestCase):
    def test_zero_timeout_is_one_open_not_default_wait_and_no_status(self):
        api = Mock()
        api.create_file.return_value = 123
        api.pipe_mode.return_value = True
        with patch.object(native, "NativeWindowsAPI", return_value=api):
            bridge = native.NativeBridge.connect(42, timeout_ms=0)
        api.wait_pipe.assert_not_called()
        api.create_file.assert_called_once()
        api.write_file.assert_not_called()
        self.assertEqual(bridge.handle, 123)

    def test_competing_open_waits_only_within_original_acquisition_budget(self):
        api = Mock()
        api.wait_pipe.return_value = True
        api.create_file.side_effect = [native.INVALID_HANDLE_VALUE, 123]
        api.pipe_mode.return_value = True
        api.failure.return_value = native.BridgeError("CreateFileW(pipe)", 231)
        with patch.object(native, "NativeWindowsAPI", return_value=api), \
             patch.object(native, "monotonic", side_effect=[10, 10.003]):
            bridge = native.NativeBridge.connect(42, timeout_ms=10)
        self.assertEqual(bridge.handle, 123)
        self.assertEqual(api.create_file.call_count, 2)
        self.assertEqual(api.wait_pipe.call_count, 2)
        self.assertEqual(api.wait_pipe.call_args_list[0].args[1], 10)
        self.assertGreaterEqual(api.wait_pipe.call_args_list[1].args[1], 1)
        self.assertLess(api.wait_pipe.call_args_list[1].args[1], 10)
        api.write_file.assert_not_called()
        api.modules.assert_not_called()

    def test_competing_open_exhaustion_keeps_actual_error_without_new_budget(self):
        api = Mock()
        api.wait_pipe.return_value = True
        api.create_file.return_value = native.INVALID_HANDLE_VALUE
        original = native.BridgeError("CreateFileW(pipe)", 231)
        api.failure.return_value = original
        with patch.object(native, "NativeWindowsAPI", return_value=api), \
             patch.object(native, "monotonic", side_effect=[10, 10.020]):
            with self.assertRaises(native.BridgeError) as caught:
                native.NativeBridge.connect(42, timeout_ms=10)
        self.assertIs(caught.exception, original)
        self.assertEqual(original.details["delivery"], "not_sent")
        api.create_file.assert_called_once()
        api.wait_pipe.assert_called_once()
        api.write_file.assert_not_called()

    def test_competing_open_zero_timeout_is_one_attempt_and_not_sent(self):
        api = Mock()
        api.create_file.return_value = native.INVALID_HANDLE_VALUE
        original = native.BridgeError("CreateFileW(pipe)", 231)
        api.failure.return_value = original
        with patch.object(native, "NativeWindowsAPI", return_value=api):
            with self.assertRaises(native.BridgeError) as caught:
                native.NativeBridge.connect(42, timeout_ms=0)
        self.assertIs(caught.exception, original)
        self.assertEqual(original.details["delivery"], "not_sent")
        api.create_file.assert_called_once()
        api.wait_pipe.assert_not_called()
        api.write_file.assert_not_called()

    def test_busy_connect_reports_pipe_and_does_not_inspect_process(self):
        api = Mock()
        api.wait_pipe.return_value = False
        error = native.BridgeError("WaitNamedPipeW", 121)
        api.failure.return_value = error
        with patch.object(native, "NativeWindowsAPI", return_value=api):
            with self.assertRaises(native.BridgeError) as caught:
                native.NativeBridge.connect(42, timeout_ms=7)
        self.assertIs(caught.exception, error)
        self.assertEqual(error.details["pipe_name"], r"\\.\pipe\pdx-runtime-42")
        self.assertEqual(error.details["timeout_ms"], 7)
        self.assertIsNone(error.details["client_owner_pid"])
        api.diagnose_process_access.assert_not_called()
        api.modules.assert_not_called()
        json.dumps(error.as_dict())

    def test_openprocess_original_error_survives_diagnostic_exception(self):
        api = Mock()
        api.open_process.return_value = 0
        original = native.BridgeError("OpenProcess", 5)
        api.failure.return_value = original
        api.diagnose_process_access.side_effect = RuntimeError("token read unavailable")
        with self.assertRaises(native.BridgeError) as caught:
            native.RemoteProcess(42, api=api)
        self.assertIs(caught.exception, original)
        self.assertEqual(original.winerror, 5)
        self.assertEqual(original.details["requested_access"], native.PROCESS_NATIVE_ACCESS)
        api.diagnose_process_access.assert_called_once_with(42, native.PROCESS_NATIVE_ACCESS,
                                                           original_winerror=5)
        self.assertEqual(original.details["access_diagnostics"]["errors"][0]["type"], "RuntimeError")
        json.dumps(original.as_dict())

    def test_pipe_access_diagnostics_preserve_actual_pipe_rights(self):
        api = Mock()
        api.create_file.return_value = native.INVALID_HANDLE_VALUE
        original = native.BridgeError("CreateFileW(pipe)", 5)
        api.failure.return_value = original
        api.diagnose_process_access.return_value = {"current": {"integrity": "medium"},
                                                     "target": {"integrity": None}, "errors": []}
        with patch.object(native, "NativeWindowsAPI", return_value=api):
            with self.assertRaises(native.BridgeError) as caught:
                native.NativeBridge.connect(42, timeout_ms=0)
        self.assertIs(caught.exception, original)
        api.diagnose_process_access.assert_called_once_with(42, 0xC0000000, original_winerror=5)
        self.assertEqual(original.details["requested_access"], 0xC0000000)
        json.dumps(original.as_dict())

    def test_mode_failure_keeps_primary_error_and_cleanup_owner(self):
        api = Mock()
        api.create_file.return_value = 123
        api.pipe_mode.return_value = False
        api.close_handle.return_value = False
        original = native.BridgeError("SetNamedPipeHandleState", 87)
        errors = iter((original, native.BridgeError("CloseHandle(pipe)", 6)))
        api.failure.side_effect = lambda operation: next(errors)
        with patch.object(native, "NativeWindowsAPI", return_value=api):
            with self.assertRaises(native.BridgeError) as caught:
                native.NativeBridge.connect(42, timeout_ms=0)
        self.assertIs(caught.exception, original)
        self.assertEqual(original.winerror, 87)
        self.assertEqual(original.client.handle, 123)
        self.assertEqual(original.details["cleanup_errors"][0]["winerror"], 6)


class ConnectionAttemptTests(unittest.TestCase):
    def test_busy_never_loads_or_initializes_and_inventory_is_not_polled(self):
        p = process([module()])
        operation = native.ConnectionAttempt(42, process=p)
        ready = client()
        replies = [native.BridgeError("WaitNamedPipeW", n) for n in (121, 231, 121, 231)] + [ready]
        with patch.object(native.NativeBridge, "connect", side_effect=replies):
            for _ in range(4):
                with self.assertRaises(native.ConnectionPending) as caught:
                    operation.resume()
                self.assertIs(caught.exception.connection_attempt, operation)
                self.assertEqual(caught.exception.details["candidate_modules"][0]["path"], module()["path"])
                self.assertIsNone(caught.exception.details["client_owner_pid"])
                json.dumps(caught.exception.as_dict())
            self.assertIs(operation.resume(), ready)
            self.assertIs(operation.resume(), ready)
        p.modules.assert_called_once()
        p.resolve_export.assert_not_called()
        p.load_library.assert_not_called()
        p.start_thread.assert_not_called()
        ready.api.write_file.assert_not_called()
        p.close.assert_not_called()

    def test_absent_endpoint_reuses_unique_different_path_runtime(self):
        old = module("C:/another-install/renamed-runtime.dll")
        p = process([old])
        ready = client()
        operation = native.ConnectionAttempt(42, "C:/new-package/pdx_runtime.dll", process=p)
        with patch.object(native.NativeBridge, "connect", side_effect=[native.BridgeError("open", 2), ready]):
            self.assertIs(operation.resume(), ready)
        p.load_library.assert_not_called()
        p.start_thread.assert_called_once_with(old["base"] + 16)
        self.assertEqual(operation.module["path"], old["path"])
        self.assertIsNone(ready.load_info)
        self.assertEqual(ready.connection_info["selected_module"]["base"], old["base"])
        ready.api.write_file.assert_not_called()

    def test_ambiguous_residents_exposed_without_guessing_or_rescanning(self):
        p = process([module(), module("C:/other/pdx_runtime.dll", 0x190000000)])
        operation = native.ConnectionAttempt(42, process=p)
        with patch.object(native.NativeBridge, "connect", side_effect=native.BridgeError("open", 2)):
            for _ in range(3):
                with self.assertRaises(native.ConnectionPending) as caught:
                    operation.resume()
                self.assertEqual(len(caught.exception.details["candidate_modules"]), 2)
                self.assertIn("serving image is unknown", caught.exception.details["ambiguity"])
        p.modules.assert_called_once()
        p.load_library.assert_not_called()
        p.start_thread.assert_not_called()

    def test_original_loader_and_initializer_survive_multiple_pending_observations(self):
        p = process()
        loaded = module("C:/new/pdx_runtime.dll")
        attempt = SimpleNamespace(path=Path(loaded["path"]), state="waiting", thread=None,
            argument_address=0xBEEF, loader_exit_dword=None, last_error=None)
        original_pending = native.LibraryLoadPending(attempt)
        attempt.resume = Mock(side_effect=[original_pending, loaded])
        p.load_library.side_effect = original_pending
        thread = p.start_thread.return_value
        thread.poll.side_effect = [None, native.BridgeError("WaitForSingleObject(thread)", 6), 0]
        operation = native.ConnectionAttempt(42, process=p)
        ready = client()
        with patch.object(native.NativeBridge, "connect", side_effect=[
                native.BridgeError("open", 2), native.BridgeError("open", 2), ready]):
            for _ in range(2):
                with self.assertRaises(native.LibraryLoadPending) as caught:
                    operation.resume()
                self.assertIs(caught.exception, original_pending)
                self.assertIs(caught.exception.attempt, attempt)
                self.assertIs(caught.exception.connection_attempt, operation)
            for _ in range(2):
                with self.assertRaises(native.ConnectionPending):
                    operation.resume()
                thread.close.assert_not_called()
                self.assertTrue(operation.pending_native)
            self.assertIs(operation.resume(), ready)
            self.assertIs(operation.resume(), ready)
        p.load_library.assert_called_once()
        self.assertEqual(attempt.resume.call_count, 2)
        p.start_thread.assert_called_once()
        self.assertEqual(thread.poll.call_count, 3)
        thread.close.assert_called_once()
        self.assertFalse(operation.pending_native)
        self.assertIs(ready.load_info, loaded)

    def test_existing_original_load_is_resumed_even_when_module_is_visible(self):
        loaded = module()
        p = process([loaded])
        attempt = SimpleNamespace(path=native.runtime_dll_path(), state="waiting",
                                  resume=Mock(return_value=loaded))
        p.pending_library_loads = (attempt,)
        ready = client()
        with patch.object(native.NativeBridge, "connect", side_effect=[native.BridgeError("open", 2), ready]):
            self.assertIs(native.ConnectionAttempt(42, process=p).resume(), ready)
        attempt.resume.assert_called_once()
        p.modules.assert_not_called()
        p.load_library.assert_not_called()

    def test_completed_original_load_is_never_reinitialized_while_pipe_is_absent(self):
        loaded = module()
        p = process([loaded])
        attempt = SimpleNamespace(path=native.runtime_dll_path(), state="waiting",
                                  resume=Mock(return_value=loaded))
        p.pending_library_loads = (attempt,)
        operation = native.ConnectionAttempt(42, process=p)
        ready = client()
        with patch.object(native.NativeBridge, "connect", side_effect=[
                native.BridgeError("open", 2), native.BridgeError("open", 2),
                native.BridgeError("open", 2), ready]):
            for _ in range(2):
                with self.assertRaises(native.ConnectionPending):
                    operation.resume()
            self.assertIs(operation.resume(), ready)
        attempt.resume.assert_called_once()
        p.load_library.assert_not_called()
        p.start_thread.assert_called_once()
        p.modules.assert_not_called()

    def test_initialize_collision_reconnects_without_reloading_and_keeps_code(self):
        p = process()
        p.load_library.return_value = module()
        p.start_thread.return_value.poll.return_value = 231
        operation = native.ConnectionAttempt(42, process=p)
        ready = client()
        with patch.object(native.NativeBridge, "connect", side_effect=[native.BridgeError("open", 2),
                native.BridgeError("open", 2), native.BridgeError("open", 231), ready]):
            with self.assertRaises(native.ConnectionPending) as caught:
                operation.resume()
            self.assertEqual(caught.exception.details["initialize_error"], 231)
            self.assertIs(operation.resume(), ready)
        p.load_library.assert_called_once()
        p.start_thread.assert_called_once()
        self.assertEqual(ready.load_info["initialize_error"], 231)

    def test_explicit_load_does_not_silently_reuse_and_keeps_init_failure(self):
        p = process([module()])
        p.load_library.return_value = module()
        p.start_thread.return_value.wait.return_value = 231
        with patch.object(native, "RemoteProcess", return_value=p),              patch.object(native.NativeBridge, "connect") as connect:
            with self.assertRaises(native.BridgeError) as caught:
                native.NativeBridge.load(42)
        self.assertEqual(caught.exception.operation, "PdxRuntimeInitialize")
        self.assertEqual(caught.exception.winerror, 231)
        self.assertEqual(caught.exception.details["selected_module"]["base"], module()["base"])
        self.assertEqual(caught.exception.details["pipe_name"], native.pipe_name(42))
        p.load_library.assert_called_once()
        p.modules.assert_not_called()
        connect.assert_not_called()
        p.close.assert_called_once()

    def test_explicit_load_pipe_pending_preserves_original_operation_and_continuation(self):
        p = process()
        p.load_library.return_value = module()
        p.start_thread.return_value.wait.return_value = 0
        ready = client()
        with patch.object(native, "RemoteProcess", return_value=p), \
             patch.object(native.NativeBridge, "connect", side_effect=[
                 native.BridgeError("WaitNamedPipeW", 121), ready]):
            with self.assertRaises(native.ConnectionPending) as caught:
                native.NativeBridge.load(42)
            self.assertEqual(caught.exception.operation, "WaitNamedPipeW")
            self.assertEqual(caught.exception.winerror, 121)
            operation = caught.exception.connection_attempt
            self.assertIs(operation.resume(), ready)
        p.load_library.assert_called_once()
        p.start_thread.assert_called_once()
        p.start_thread.return_value.wait.assert_called_once()
        p.close.assert_called_once()

    def test_session_attach_is_only_composition(self):
        ready = client()
        p = process()
        with patch.object(native.NativeBridge, "attach", return_value=ready) as attach:
            session = RuntimeSession.attach(42, process=p)
        self.assertIs(session.native, ready)
        attach.assert_called_once_with(42, None, process=p, timeout_ms=0)


class LocalRecoveryTests(unittest.TestCase):
    def fail(self, bridge):
        original = native.BridgeError("WriteFile(pipe)", 109)
        bridge.api.failure.return_value = original
        bridge.api.write_file.return_value = False
        with self.assertRaises(native.BridgeError) as caught:
            bridge.request("one-native-action")
        self.assertIs(caught.exception, original)
        return original

    def test_failed_stream_is_not_reused_and_recovery_preserves_client(self):
        bridge = client(100)
        self.fail(bridge)
        with self.assertRaises(native.BridgeError) as caught:
            bridge.request("another-action")
        self.assertEqual(caught.exception.details["delivery"], "not_sent")
        self.assertEqual(bridge.api.write_file.call_count, 1)
        replacement = client(200)
        def connect(*args, **kwargs):
            bridge.api.close_handle.assert_called_once_with(100)
            if connect.calls == 0:
                connect.calls += 1
                raise native.BridgeError("open", 231)
            return replacement
        connect.calls = 0
        with patch.object(native.NativeBridge, "connect", side_effect=connect) as opening:
            with self.assertRaises(native.BridgeError):
                bridge.recover()
            self.assertIsNone(bridge.handle)
            self.assertIs(bridge.recover(), bridge)
            self.assertIs(bridge.recover(), bridge)
            self.assertEqual(opening.call_count, 2)
        self.assertEqual(bridge.handle, 200)
        self.assertIsNone(replacement.handle)
        self.assertEqual(bridge.api.write_file.call_count, 1)  # No mutation replay or handshake.

    def test_old_failed_generation_cannot_close_replacement(self):
        bridge = client(200)
        bridge._generation = 2
        bridge._failed_connection = (100, 1)
        with patch.object(native.NativeBridge, "connect") as connect:
            self.assertIs(bridge.recover(), bridge)
        bridge.api.close_handle.assert_not_called()
        connect.assert_not_called()

    def test_concurrent_recovery_never_overwrites_other_success(self):
        bridge = client(100)
        self.fail(bridge)
        replacement = client(200)
        def another_won(*args, **kwargs):
            bridge.handle = 300
            bridge._generation += 1
            bridge._recovery_pending = False
            return replacement
        with patch.object(native.NativeBridge, "connect", side_effect=another_won):
            self.assertIs(bridge.recover(), bridge)
        self.assertEqual(bridge.handle, 300)
        bridge.api.close_handle.assert_called_once_with(100)
        replacement.api.close_handle.assert_called_once_with(200)

    def test_explicit_close_during_reconnect_does_not_reopen_client(self):
        bridge = client(100)
        self.fail(bridge)
        replacement = client(200)
        def closed(*args, **kwargs):
            bridge.close()
            return replacement
        with patch.object(native.NativeBridge, "connect", side_effect=closed):
            with self.assertRaises(native.BridgeError) as caught:
                bridge.recover()
        self.assertEqual(caught.exception.operation, "reconnect")
        self.assertIsNone(bridge.handle)
        replacement.api.close_handle.assert_called_once_with(200)

    def test_status_returns_same_native_result_without_receipt_gate(self):
        bridge = client()
        response = {"status": "ok", "dll_base": "0x123", "arbitrary_native_field": 99}
        with patch.object(bridge, "request", return_value=response):
            self.assertIs(bridge.status(), response)
        self.assertEqual(bridge.connection_info["status"], {"dll_base": "0x123"})


class ServingImageTests(unittest.TestCase):
    def fixture(self):
        from tests.core.test_v3_runtime import RecoveryTests
        setup = RecoveryTests("test_known_already_exists_result_keeps_original_native_code")
        setup.setUp()
        self.addCleanup(setup.doCleanups)
        exports = ["PdxV3DrainConstruction", "PdxV3SessionHookState"]
        exports += [pair[1] for pair in lifecycle.v3_runtime.ARTIFACTS.values()]
        setup.process.add("pdx_runtime.dll", 0x70000, exports, path="old/pdx_runtime.dll")
        setup.process.add("pdx_runtime.dll", 0x80000, exports, path="new/pdx_runtime.dll")
        setup.client.status.return_value = {"status": "ok", "dll_base": "0x80000"}
        return setup

    def test_already_read_serving_image_precedes_another_unified_image(self):
        setup = self.fixture()
        result = setup.install(stage="interface")
        self.assertEqual(result["modules"]["carrier"]["base"], 0x80000)
        for role in ("query", "validation", "start", "finalize"):
            self.assertEqual(result["modules"][role]["base"], 0x80000)
        setup.client.status.assert_called_once()
        self.assertEqual(result["resident_modules"], setup.process.loaded)

    def test_explicit_role_hint_and_real_split_module_still_win(self):
        setup = self.fixture()
        name, symbol = lifecycle.v3_runtime.ARTIFACTS["validation"]
        setup.process.add(name, 0x90000, [symbol])
        result = setup.install(stage="interface", modules={"query": 0x70000})
        self.assertEqual(result["modules"]["query"]["base"], 0x70000)
        self.assertEqual(result["modules"]["validation"]["base"], 0x90000)
        self.assertEqual(result["modules"]["start"]["base"], 0x80000)


class EarlyEntryFailureTests(unittest.IsolatedAsyncioTestCase):

    async def test_entry_cleanup_failure_does_not_replace_original_error(self):
        from tests.v3_game.test_v3_game_lifecycle import FakeNative, FakeProcess
        from pdx_runtime.v3_game import V3Game
        bridge, p = FakeNative(), FakeProcess()
        primary = native.BridgeError("install", 87)
        bridge.close = Mock(side_effect=RuntimeError("local cleanup failed"))
        with patch.object(lifecycle, "RemoteProcess", return_value=p), \
             patch.object(native.NativeBridge, "connect", return_value=bridge), \
             patch.object(lifecycle, "_install", side_effect=primary):
            with self.assertRaises(native.BridgeError) as caught:
                await V3Game.attach(pid=42)
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.entry_context.diagnostics["cleanup_errors"][0]["resource"], "session")
        self.assertEqual(p.closed, 1)

    async def test_openprocess_failure_has_entry_context_and_no_resource_actions(self):
        original = native.BridgeError("OpenProcess", 5)
        with patch.object(lifecycle, "RemoteProcess", side_effect=original),              patch.object(native.NativeBridge, "connect") as connect:
            with self.assertRaises(native.BridgeError) as caught:
                await lifecycle.attach(Mock(), pid=42)
        self.assertIs(caught.exception, original)
        self.assertEqual(original.entry_context.pending_queries, [])
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
