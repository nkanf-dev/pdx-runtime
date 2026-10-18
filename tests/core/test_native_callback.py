"""Shared callback CPU fixtures. No native process, game, or dispatcher is opened."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from tests.native_sdk.test_v3_query import bind_fixture_wire

from pdx_runtime import native_call, native_callback as shared, v3
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES, resolve_v3_export


def classify_fixture_submission(response):
    """This fixture protocol explicitly proves rejection, including its zero code."""
    if response["status"] == "ok":
        return shared.CallbackDisposition("accepted", request_id=response.get("request_id"))
    if response["status"] == "error":
        return shared.CallbackDisposition("rejected", error=response.get("error", 0))
    return shared.CallbackDisposition("unknown")


class SharedCallbackTests(unittest.TestCase):
    def setUp(self):
        # A plain object has no V3 dispatch/status methods and no PID.
        self.client = object()
        self.process = Mock()
        self.process.resolve_export.return_value = 0x100000001
        self.process.allocate.return_value = 0x200000002
        self.dispatch = Mock(return_value={"status": "ok", "request_id": 0x100000007})
        self.status = Mock(return_value={"status": "ok", "completed_request_id": 0x100000006})
        self.open_process = Mock(return_value=self.process)
        self.context = shared.CallbackContext(self.client, self.dispatch, self.status,
            self.open_process, lambda process: process is self.process, classify_fixture_submission)
        self.result = SimpleNamespace(error=0, value=0, enabled=False)
        self.decode = Mock(return_value=self.result)
        self.process.read.return_value = b"done"

    def submit(self, *, owned=False, **kwargs):
        return shared.submit_callback(self.client, b"wait", context=self.context,
            adapter="foreign.dll", callback_symbol="ForeignCallback", decode_result=self.decode,
            process=None if owned else self.process, **kwargs)


    def test_explicit_foreign_context_owns_dispatch_completion_and_zero_false_values(self):
        query = self.submit()
        self.assertIs(query.client, self.client)
        self.assertIs(query.context, self.context)
        self.assertIs(query.submission, self.dispatch.return_value)
        self.assertEqual(query.request_id, 0x100000007)
        self.dispatch.assert_called_once_with(0x100000001, 0x200000002)
        self.open_process.assert_not_called()
        self.assertIsNone(query.read())
        self.process.read.assert_not_called()
        self.process.free.assert_not_called()
        self.status.return_value = {"status": "ok", "completed_request_id": 0x100000007}
        result = query.read()
        self.assertEqual(result.value, 0)
        self.assertIs(result.enabled, False)
        self.assertIs(query.read(), result)
        query.close()
        self.process.read.assert_called_once_with(0x200000002, 4)
        self.decode.assert_called_once_with(b"done")
        self.process.free.assert_called_once_with(0x200000002)
        self.process.close.assert_not_called()
        self.dispatch.assert_called_once()


    def test_default_export_resolution_is_strict_without_alias_policy(self):
        query = self.submit()
        self.assertIsNone(query.context.export_resolver)
        self.process.resolve_export.assert_called_once_with("foreign.dll", "ForeignCallback")
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()

    def test_explicit_export_resolver_receives_original_process_and_full_adapter_id(self):
        resolver = Mock(return_value=0xABCDEF0123456789)
        context = replace(self.context, export_resolver=resolver)
        adapter = 0xFEDCBA9876543210
        query = shared.submit_callback(self.client, b"wait", context=context,
            adapter=adapter, callback_symbol="ForeignCallback", decode_result=self.decode,
            process=self.process)
        resolver.assert_called_once_with(self.process, adapter, "ForeignCallback")
        self.process.resolve_export.assert_not_called()
        self.assertIs(query.process, self.process)
        self.assertIs(query.client, self.client)
        self.assertIs(query.context.export_resolver, resolver)
        self.dispatch.assert_called_once_with(0xABCDEF0123456789, 0x200000002)
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()

    def test_export_resolver_failure_is_prequeue_and_releases_only_owned_process(self):
        original = BridgeError("fixture resolver", 126)
        resolver = Mock(side_effect=original)
        context = replace(self.context, export_resolver=resolver)
        with self.assertRaises(BridgeError) as caught:
            shared.submit_callback(self.client, b"wait", context=context,
                adapter="missing.dll", callback_symbol="Callback", decode_result=self.decode)
        self.assertIs(caught.exception, original)
        self.assertFalse(hasattr(caught.exception, "query"))
        resolver.assert_called_once_with(self.process, "missing.dll", "Callback")
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.process.free.assert_not_called()
        self.process.close.assert_called_once()
        self.dispatch.assert_not_called()

    def test_owned_process_uses_only_injected_factory_and_closes_once(self):
        query = self.submit(owned=True)
        self.open_process.assert_called_once_with()
        self.assertIsNone(query.read())
        with self.assertRaisesRegex(RuntimeError, "pending"):
            query.close()
        self.process.close.assert_not_called()
        self.status.return_value = {"status": "ok", "completed_request_id": query.request_id}
        self.assertIs(query.read(), self.result)
        query.close()
        self.process.free.assert_called_once()
        self.process.close.assert_called_once()

    def test_unknown_dispatch_retains_original_handle_and_recovers_without_replay(self):
        original = BridgeError("foreign transport", 109)
        self.dispatch.side_effect = original
        with self.assertRaises(BridgeError) as caught:
            self.submit(owned=True)
        self.assertIs(caught.exception, original)
        query = caught.exception.query
        self.assertIsNone(query.request_id)
        self.process.free.assert_not_called()
        self.process.close.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            query.close()
        for invalid in (0, -1):
            with self.assertRaises(ValueError):
                query.recover(invalid)
        self.status.return_value = {"status": "ok", "completed_request_id": 0x100000007}
        self.assertIs(query.recover(0x100000007).read(), self.result)
        with self.assertRaises(RuntimeError):
            query.recover(0x100000008)
        self.process.free.assert_called_once()
        self.process.close.assert_called_once()
        self.dispatch.assert_called_once()

    def test_malformed_unknown_acknowledgements_retain_exact_storage_until_recovery(self):
        responses = (None, [], {}, {"status": "pending", "unknown_operation": "future"},
            {"status": "ok"}, {"status": "ok", "request_id": 0},
            {"status": "ok", "request_id": False}, {"status": "ok", "request_id": "7"})
        for response in responses:
            with self.subTest(response=response):
                self.setUp()
                self.dispatch.return_value = response
                with self.assertRaises((BridgeError, TypeError, KeyError)) as caught:
                    self.submit(owned=True)
                query = caught.exception.query
                self.assertIs(query.submission, response)
                self.assertIs(query.process, self.process)
                self.assertIsNone(query.request_id)
                self.process.free.assert_not_called()
                self.process.close.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, "unknown"):
                    query.close()
                self.status.return_value = {"status": "ok", "completed_request_id": 0x100000007}
                self.assertIs(query.recover(0x100000007).read(), self.result)
                self.dispatch.assert_called_once()
                self.process.free.assert_called_once_with(0x200000002)
                self.process.close.assert_called_once()

    def test_classifier_exception_retains_original_exception_and_query(self):
        original = ValueError("foreign decoder failed")
        context = shared.CallbackContext(self.client, self.dispatch, self.status,
            self.open_process, lambda process: process is self.process, Mock(side_effect=original))
        with self.assertRaises(ValueError) as caught:
            shared.submit_callback(self.client, b"wait", context=context,
                adapter="foreign", callback_symbol="Callback", decode_result=self.decode)
        self.assertIs(caught.exception, original)
        self.assertIs(caught.exception.query.submission, self.dispatch.return_value)
        self.process.free.assert_not_called()
        self.process.close.assert_not_called()
        self.dispatch.assert_called_once()

    def test_dispatch_cancellation_retains_original_query_without_replay(self):
        original = asyncio.CancelledError("fixture cancellation")
        self.dispatch.side_effect = original
        with self.assertRaises(asyncio.CancelledError) as caught:
            self.submit(owned=True)
        self.assertIs(caught.exception, original)
        query = caught.exception.query
        self.process.free.assert_not_called()
        self.process.close.assert_not_called()
        self.status.return_value = {"status": "ok", "completed_request_id": 0x100000007}
        self.assertIs(query.recover(0x100000007).read(), self.result)
        self.dispatch.assert_called_once()
        self.process.free.assert_called_once()
        self.process.close.assert_called_once()

    def test_rejection_cleanup_failure_retains_owner_and_retries_only_cleanup(self):
        for fail_at in ("free", "close"):
            with self.subTest(fail_at=fail_at):
                self.setUp()
                self.dispatch.return_value = {"status": "error", "error": 170}
                original = BridgeError(fail_at, 5)
                getattr(self.process, fail_at).side_effect = [original, None]
                with self.assertRaises(BridgeError) as caught:
                    self.submit(owned=True)
                query = caught.exception.query
                self.assertIs(caught.exception, query.submission_error)
                self.assertIs(query.cleanup_error, original)
                self.assertIs(caught.exception.__cause__, original)
                self.assertEqual(query.submission_error.winerror, 170)
                self.assertIsNone(query.request_id)
                with self.assertRaises(RuntimeError):
                    query.recover(0x100000007)
                query.close()
                query.close()
                with self.assertRaises(BridgeError) as rejected:
                    query.read()
                self.assertIs(rejected.exception, query.submission_error)
                self.assertEqual(self.process.free.call_count, 2 if fail_at == "free" else 1)
                self.assertEqual(self.process.close.call_count, 1 if fail_at == "free" else 2)
                self.process.read.assert_not_called()
                self.decode.assert_not_called()
                self.status.assert_not_called()
                self.dispatch.assert_called_once()

    def test_v3_protocol_does_not_treat_inconsistent_or_unknown_status_as_rejection(self):
        responses = (None, {}, {"status": "error", "error": 0},
            {"status": "error", "error": False}, {"status": "ok", "request_id": 7},
            {"status": "ok", "error": 170, "request_id": 7},
            {"status": "pending", "error": 170}, {"status": "ok", "error": 0})
        for response in responses:
            with self.subTest(response=response):
                client = Mock(pid=123)
                bind_fixture_wire(client)
                process = Mock(pid=123)
                process.resolve_export.return_value = 1
                process.allocate.return_value = 2
                client.queue_v3_interface_callback.return_value = response
                with self.assertRaises(BridgeError) as caught:
                    v3.submit(client, 1, process=process)
                query = caught.exception.query
                self.assertIsInstance(query, v3.CountryQuery)
                self.assertIs(query.submission, response)
                process.free.assert_not_called()
                process.close.assert_not_called()
                client.queue_v3_interface_callback.assert_called_once()

    def test_explicit_rejection_releases_owned_process_without_replay(self):
        self.dispatch.return_value = {"status": "error", "error": 0}
        with self.assertRaises(BridgeError) as caught:
            self.submit(owned=True)
        self.assertEqual(caught.exception.winerror, 0)
        self.assertFalse(hasattr(caught.exception, "query"))
        self.process.free.assert_called_once()
        self.process.close.assert_called_once()
        self.dispatch.assert_called_once()

    def test_prequeue_factory_failure_releases_without_dispatch(self):
        original = BridgeError("prepare", 299)
        with self.assertRaises(BridgeError) as caught:
            self.submit(owned=True, handle_factory=Mock(side_effect=original))
        self.assertIs(caught.exception, original)
        self.process.free.assert_called_once()
        self.process.close.assert_called_once()
        self.dispatch.assert_not_called()

    def test_custom_handle_cannot_mix_another_process_or_result_allocation(self):
        foreign_process = Mock()
        foreign_context = shared.CallbackContext(self.client, self.dispatch, self.status,
            Mock(return_value=foreign_process), lambda process: process is foreign_process, classify_fixture_submission)
        def wrong_process(client, process, callback, address, owns):
            return shared.CallbackResult(client, foreign_process, callback, address,
                owns, 4, self.decode, context=foreign_context)
        with self.assertRaisesRegex(ValueError, "this submission"):
            self.submit(owned=True, handle_factory=wrong_process)
        self.process.free.assert_called_once_with(0x200000002)
        self.process.close.assert_called_once()
        foreign_process.free.assert_not_called()
        foreign_process.close.assert_not_called()
        self.dispatch.assert_not_called()

    def test_completion_transport_error_preserves_storage_and_can_retry_read(self):
        query = self.submit(owned=True)
        self.status.return_value = {"status": "error", "error": 0}
        with self.assertRaises(BridgeError) as caught:
            query.read()
        self.assertEqual(caught.exception.winerror, 0)
        self.process.read.assert_not_called()
        self.process.free.assert_not_called()
        self.process.close.assert_not_called()
        self.status.return_value = {"status": "ok", "completed_request_id": query.request_id}
        self.assertIs(query.read(), self.result)
        self.dispatch.assert_called_once()

    def test_unwritten_completion_matches_only_its_exact_request_error(self):
        for latest in (0x100000007, 0x100000008):
            with self.subTest(latest=latest):
                query = self.submit()
                pending = SimpleNamespace(error=shared.ERROR_IO_PENDING)
                self.decode.return_value = pending
                self.status.return_value = {"status": "ok", "request_id": latest,
                    "completed_request_id": latest, "callback_error": 574}
                with self.assertRaises(shared.CallbackExecutionError) as caught:
                    query.read()
                self.assertIs(caught.exception.result, pending)
                self.assertEqual(caught.exception.details["request_id"], 0x100000007)
                self.assertEqual(caught.exception.winerror, 574 if latest == query.request_id else 0)
                query.close()

    def test_decoder_error_retains_observed_completion_without_replacing_original_error(self):
        query = self.submit(owned=True)
        observed = {"status": "ok", "request_id": query.request_id,
                    "completed_request_id": query.request_id, "callback_error": 574,
                    "raw_zero": 0, "raw_false": False}
        self.status.return_value = observed
        original = BridgeError("opaque decoder", 997)
        self.decode.side_effect = original
        with self.assertRaises(BridgeError) as caught:
            query.read()
        self.assertIs(caught.exception, original)
        self.assertEqual(caught.exception.winerror, 997)
        self.assertIs(getattr(query, "completion_status", None), observed)
        self.assertIsNone(query._result)
        self.assertIsNone(query._execution_error)
        self.assertFalse(query._released)
        self.decode.assert_called_once_with(b"done")
        self.status.assert_called_once()
        self.process.read.assert_called_once_with(query.result_address, 4)
        self.process.free.assert_not_called()
        self.process.close.assert_not_called()
        self.dispatch.assert_called_once()

    def test_completion_status_is_absent_until_terminal_callback_is_observed(self):
        query = self.submit()
        self.assertIsNone(query.completion_status)
        self.assertIsNone(query.read())
        self.assertIsNone(query.completion_status)
        self.status.return_value = {"status": "error", "error": 5}
        with self.assertRaises(BridgeError):
            query.read()
        self.assertIsNone(query.completion_status)
        self.process.read.assert_not_called()
        self.decode.assert_not_called()
        self.process.free.assert_not_called()

    def test_completion_status_keeps_same_snapshot_during_cached_result_cleanup(self):
        query = self.submit(owned=True)
        observed = {"status": "ok", "request_id": query.request_id + 1,
                    "completed_request_id": query.request_id + 1, "callback_error": 574}
        self.status.return_value = observed
        self.process.free.side_effect = [BridgeError("free", 5), None]
        self.assertIs(query.read(), self.result)
        self.assertIs(query.completion_status, observed)
        self.assertIs(query.read(), self.result)
        self.assertIs(query.completion_status, observed)
        self.status.assert_called_once()
        self.decode.assert_called_once()
        self.dispatch.assert_called_once()
        self.assertTrue(query._released)

    def test_free_retry_uses_saved_result_without_repeating_consuming_decode(self):
        query = self.submit(owned=True)
        self.status.return_value = {"status": "ok", "completed_request_id": query.request_id}
        failure = BridgeError("free", 5)
        self.process.free.side_effect = [failure, None]
        self.assertIs(query.read(), self.result)
        self.assertIs(query.cleanup_error, failure)
        self.assertFalse(query._released)
        self.assertIs(query.read(), self.result)
        self.assertIsNone(query.cleanup_error)
        self.process.free.assert_called_with(query.result_address)
        self.assertEqual(self.process.free.call_count, 2)
        self.decode.assert_called_once()
        self.process.read.assert_called_once()
        self.status.assert_called_once()
        self.process.close.assert_called_once()
        self.dispatch.assert_called_once()

    def test_close_retry_does_not_free_or_decode_completed_result_twice(self):
        query = self.submit(owned=True)
        self.status.return_value = {"status": "ok", "completed_request_id": query.request_id}
        failure = BridgeError("close", 5)
        self.process.close.side_effect = [failure, None]
        self.assertIs(query.read(), self.result)
        self.assertIs(query.cleanup_error, failure)
        self.assertTrue(query._freed)
        self.assertFalse(query._released)
        self.assertIs(query.read(), self.result)
        self.assertIsNone(query.cleanup_error)
        self.process.free.assert_called_once()
        self.decode.assert_called_once()
        self.process.read.assert_called_once()
        self.status.assert_called_once()
        self.assertEqual(self.process.close.call_count, 2)
        self.dispatch.assert_called_once()

    def test_context_cannot_be_reused_for_another_client_before_allocation(self):
        wrong_client = object()
        with self.assertRaisesRegex(ValueError, "different client"):
            shared.submit_callback(wrong_client, b"wait", context=self.context,
                adapter="foreign.dll", callback_symbol="ForeignCallback", decode_result=self.decode)
        with self.assertRaisesRegex(ValueError, "different client"):
            native_call.submit(wrong_client, 0xABC, context=self.context, process=self.process)
        with self.assertRaisesRegex(ValueError, "different client"):
            shared.CallbackResult(wrong_client, self.process, 1, 2, False, 4,
                                  self.decode, context=self.context)
        self.open_process.assert_not_called()
        self.process.allocate.assert_not_called()
        self.dispatch.assert_not_called()

    def test_native_call_requires_context_before_any_allocation(self):
        for submit, value in ((native_call.submit, 0xABC), (native_call.submit_batch, ())):
            with self.subTest(submit=submit.__name__):
                with self.assertRaisesRegex(TypeError, "context"):
                    submit(self.client, value, process=self.process)
        self.process.allocate.assert_not_called()
        self.dispatch.assert_not_called()

    def test_raw_call_uses_foreign_context_without_client_game_methods(self):
        query = native_call.submit(self.client, 0xFFFFFFFFABCDEF01, (0, 0x100000001),
                                   context=self.context, process=self.process)
        self.assertIsInstance(query, shared.CallbackResult)
        self.assertNotIsInstance(query, v3.CallbackResult)
        self.assertIsNone(query.read())
        self.status.return_value = {"status": "ok", "completed_request_id": query.request_id}
        self.process.read.return_value = native_call.REQUEST.pack(56, 0,
            query.target, 2, query.arguments_address, 0, bytes(16)) + bytes(16)
        self.assertEqual(query.read().rax, 0)
        self.dispatch.assert_called_once_with(query.callback_address, query.result_address)

    def test_process_proof_must_be_exact_true_before_allocation_or_dispatch(self):
        for proof in (False, None, 1):
            with self.subTest(proof=proof):
                context = shared.CallbackContext(self.client, self.dispatch, self.status,
                    self.open_process, lambda process: proof, classify_fixture_submission)
                with self.assertRaisesRegex(ValueError, "identity is not confirmed"):
                    shared.submit_callback(self.client, b"wait", context=context,
                        adapter="foreign.dll", callback_symbol="Callback", decode_result=self.decode,
                        process=self.process)
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.process.close.assert_not_called()
        self.dispatch.assert_not_called()

    def test_invalid_factory_process_is_closed_without_lookup_or_allocation(self):
        context = shared.CallbackContext(self.client, self.dispatch, self.status,
            self.open_process, lambda process: False, classify_fixture_submission)
        with self.assertRaisesRegex(ValueError, "identity is not confirmed"):
            shared.submit_callback(self.client, b"wait", context=context,
                adapter="foreign.dll", callback_symbol="Callback", decode_result=self.decode)
        self.open_process.assert_called_once()
        self.process.resolve_export.assert_not_called()
        self.process.allocate.assert_not_called()
        self.process.free.assert_not_called()
        self.process.close.assert_called_once()
        self.dispatch.assert_not_called()

    def test_foreign_backend_explicit_opaque_target_proof_needs_no_os_pid(self):
        target_key = object()
        process = SimpleNamespace(target_key=target_key, resolve_export=Mock(return_value=1),
            allocate=Mock(return_value=2), write=Mock(), read=Mock(return_value=b"done"),
            free=Mock(), close=Mock())
        self.assertFalse(hasattr(process, "pid"))
        context = shared.CallbackContext(self.client, self.dispatch, self.status,
            lambda: process, lambda candidate: getattr(candidate, "target_key", None) is target_key, classify_fixture_submission)
        query = shared.submit_callback(self.client, b"wait", context=context,
            adapter="foreign", callback_symbol="Callback", decode_result=self.decode)
        self.assertIsNone(query.read())
        self.status.return_value = {"status": "ok", "completed_request_id": query.request_id}
        self.assertIs(query.read(), self.result)
        process.free.assert_called_once_with(2)
        process.close.assert_called_once()

    def test_v3_adapter_rejects_missing_invalid_or_different_pid_before_allocation(self):
        client = Mock(pid=123)
        bind_fixture_wire(client)
        for actual_pid in (None, False, 0, "123", 124):
            with self.subTest(actual_pid=actual_pid):
                process = Mock(pid=actual_pid)
                with self.assertRaisesRegex(ValueError, "identity is not confirmed"):
                    v3.submit(client, 1, process=process)
                process.resolve_export.assert_not_called()
                process.allocate.assert_not_called()
                process.close.assert_not_called()
        missing = SimpleNamespace()
        with self.assertRaisesRegex(ValueError, "identity is not confirmed"):
            v3.submit(client, 1, process=missing)
        client.queue_v3_interface_callback.assert_not_called()

    def test_v3_context_rejects_client_pid_mutation_after_binding(self):
        client = Mock(pid=123)
        bind_fixture_wire(client)
        context = v3.callback_context(client)
        process = Mock(pid=123)
        client.pid = 124
        with self.assertRaisesRegex(ValueError, "identity is not confirmed"):
            native_call.submit(client, 0xABC, context=context, process=process)
        process.resolve_export.assert_not_called()
        process.allocate.assert_not_called()
        client.queue_v3_interface_callback.assert_not_called()

    def test_v3_wire_context_uses_request_only_transport_and_keeps_original_client(self):
        request = Mock(side_effect=[
            {"status": "ok", "error": 0, "request_id": 7},
            {"status": "ok", "completed_request_id": 6},
            {"status": "ok", "request_id": 7, "completed_request_id": 7}])
        client = SimpleNamespace(pid=123, request=request)
        self.assertFalse(hasattr(client, "v3_world_status"))
        self.assertFalse(hasattr(client, "queue_v3_interface_callback"))
        process = Mock(pid=123)
        process.resolve_export.return_value = 0x100000001
        process.allocate.return_value = 0x200000002
        process.read.return_value = v3.COUNTRY_RESULT.pack(40, 0, 1, 1, 0, 0, 0)
        query = v3.submit(client, 1, process=process)
        self.assertIs(query.client, client)
        self.assertIs(query.context.client, client)
        self.assertIs(query.context.export_resolver, resolve_v3_export)
        self.assertIs(query.process, process)
        process.resolve_export.assert_called_once_with("pdx_v3_query.dll",
            "PdxV3QueryCountryInterface", aliases=V3_EXPORT_ALIASES)
        self.assertIsNone(query.read())
        process.free.assert_not_called()
        self.assertEqual(query.read().gdp_raw64, 0)
        self.assertEqual([call.args for call in request.call_args_list], [
            ("interface-callback 0x100000001 0x200000002",),
            ("world-status",), ("world-status",)])
        process.free.assert_called_once_with(0x200000002)
        process.close.assert_not_called()

    def test_v3_adapter_normalizes_error_without_mutating_native_snapshot(self):
        source = Mock(return_value={"status": "ok", "completed_request_id": 7,
                                    "world_error": 0, "unknown_native_field": False})
        client = Mock()
        bind_fixture_wire(client)
        context = v3.callback_context(client, status_getter=source)
        normalized = context.status_getter()
        self.assertEqual(normalized["callback_error"], 0)
        self.assertIs(normalized["unknown_native_field"], False)
        self.assertNotIn("callback_error", source.return_value)
        client.v3_world_status.assert_not_called()
        client.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
