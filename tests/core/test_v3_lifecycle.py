"""Offline ownership tests; no game, process, native callback or save file is opened."""
from __future__ import annotations

from dataclasses import fields
import unittest
from unittest.mock import Mock, patch

from pdx_runtime import v3_lifecycle as lifecycle
from pdx_runtime.native_bridge import BridgeError


def result(**changes):
    values = {field.name: ("" if field.name in ("path", "resolved_path", "date_text") else 0)
              for field in fields(lifecycle.LifecycleResult)}
    values.update(size=lifecycle.LIFECYCLE_RESULT.size, path="temporary/review",
                  resolved_path="native-root/temporary/review.v3", owned_task_handle=0x50000)
    values.update(changes)
    return lifecycle.LifecycleResult(**values)


class LifecycleTests(unittest.TestCase):
    def _wire_request_fixture(self, command):
        """CPU transport: preserve raw responses through exact V3 wire commands."""
        if command == "world-status":
            return self.world_status()
        words = command.split()
        if len(words) == 3 and words[0] == "interface-callback":
            callback, address = int(words[1], 0), int(words[2], 0)
            self.assertEqual(command, f"interface-callback {callback:#x} {address:#x}")
            return self.interface_callback(callback, address)
        raise AssertionError(f"unexpected V3 fixture wire command: {command!r}")

    def setUp(self):
        self.client = Mock(spec_set=["pid", "request", "status"], pid=123)
        self.world_status = Mock()
        self.interface_callback = Mock()
        self.client.request.side_effect = self._wire_request_fixture
        self.process = Mock(pid=123)
        self.operation = lifecycle.retain_save_result(result(), self.client, process=self.process)

    def test_queue_ack_does_not_prove_save_reference_was_consumed(self):
        query = Mock()
        with patch.object(lifecycle, "submit", return_value=query) as submit:
            self.assertIs(self.operation.release(), query)
        self.assertEqual(self.operation.owned_task_handle, 0x50000)
        self.assertIs(self.operation.pending_release, query)
        submit.assert_called_once()

    def test_unknown_release_cannot_enqueue_release_or_poll_again(self):
        query = Mock()
        error = BridgeError("ReadFile(pipe)", 109)
        error.query = query
        with patch.object(lifecycle, "submit", side_effect=error) as submit:
            with self.assertRaises(BridgeError) as caught:
                self.operation.release()
            self.assertIs(caught.exception.query, query)
            self.assertIs(self.operation.pending_release, query)
            self.assertEqual(self.operation.owned_task_handle, 0x50000)
            for action in (self.operation.release, self.operation.poll):
                try:
                    action()
                except (RuntimeError, BridgeError):
                    pass
            self.assertEqual(submit.call_count, 1, "unknown ownership must not release twice or poll freed storage")

    def test_acknowledged_pending_release_cannot_poll_owned_task(self):
        query = Mock()
        with patch.object(lifecycle, "submit", return_value=query) as submit:
            self.operation.release()
            try:
                self.operation.poll()
            except RuntimeError:
                pass
            self.assertEqual(submit.call_count, 1, "release callback may already have consumed the task")

    def test_release_settlement_retains_on_pending_and_clears_only_confirmed_consumption(self):
        query = Mock()
        released = result(error=0, owned_task_handle=0)
        query.read.side_effect = [None, released]
        with patch.object(lifecycle, "submit", return_value=query) as submit:
            self.operation.release()
            self.assertIsNone(self.operation.settle_release())
            self.assertIs(self.operation.pending_release, query)
            self.assertEqual(self.operation.owned_task_handle, 0x50000)
            self.assertEqual(self.operation.settle_release(), released)
            self.assertIsNone(self.operation.pending_release)
            self.assertEqual(self.operation.owned_task_handle, 0)
            with self.assertRaises(RuntimeError):
                self.operation.poll()
            with self.assertRaises(RuntimeError):
                self.operation.release()
            submit.assert_called_once()

    def test_callback_not_ready_retains_reference_and_explicit_retry_is_possible(self):
        query = Mock()
        query.read.return_value = result(error=21, owned_task_handle=0x50000)
        with patch.object(lifecycle, "submit", return_value=query) as submit:
            self.operation.release()
            settled = self.operation.settle_release()
            self.assertEqual(settled.error, 21)
            self.assertIsNone(self.operation.pending_release)
            self.assertEqual(self.operation.owned_task_handle, 0x50000)
            self.operation.release()
            self.assertEqual(submit.call_count, 2)

    def test_consumption_unknown_cannot_be_released_or_polled_again(self):
        query = Mock()
        query.read.return_value = result(error=574, owned_task_handle=0)
        with patch.object(lifecycle, "submit", return_value=query) as submit:
            self.operation.release()
            with self.assertRaisesRegex(RuntimeError, "ownership is unknown"):
                self.operation.settle_release()
            self.assertIs(self.operation.pending_release, query)
            for action in (self.operation.release, self.operation.poll):
                with self.assertRaises(RuntimeError):
                    action()
            submit.assert_called_once()

    def test_native_overwrite_requires_explicit_opt_in(self):
        with patch.object(lifecycle, "submit_callback", return_value=Mock()) as queue:
            lifecycle.save(self.client, "temporary/review", process=self.process)
            initial = lifecycle.LIFECYCLE_RESULT.unpack(queue.call_args.args[1])
            self.assertEqual(initial[3], 0)
            lifecycle.save(self.client, "temporary/review", overwrite=True, process=self.process)
            overwrite_initial = lifecycle.LIFECYCLE_RESULT.unpack(queue.call_args.args[1])
            self.assertEqual(overwrite_initial[3], 2)

    def test_native_date_text_parts_and_set_date_input(self):
        sample = result(date_text="1836.1.2.3")
        packed = [getattr(sample, field.name) for field in fields(sample)]
        packed[-3:] = [value.encode("utf-8") for value in packed[-3:]]
        decoded = lifecycle.LifecycleResult.from_bytes(lifecycle.LIFECYCLE_RESULT.pack(*packed))
        self.assertEqual(decoded.date_text, "1836.1.2.3")
        self.assertEqual(decoded.date_parts, (1836, 1, 2, 3))
        self.assertEqual(result(date_text="1836.1.2").date_parts, (1836, 1, 2, 0))
        self.assertIsNone(result().date_parts)
        with patch.object(lifecycle, "submit_callback", return_value=Mock()) as queue:
            lifecycle.set_date(self.client, 1836, 1, 2, 3, process=self.process)
        initial = lifecycle.LIFECYCLE_RESULT.unpack(queue.call_args.args[1])
        self.assertEqual(initial[2], lifecycle.Action.SET_DATE)
        self.assertEqual(initial[-3].split(b"\0", 1)[0], b"1836.1.2.3")

    def test_application_exit_requires_intent_and_explicit_native_save_flag(self):
        with patch.object(lifecycle, "submit_callback", return_value=Mock()) as queue:
            with self.assertRaises(TypeError):
                lifecycle.exit_application(self.client, process=self.process)
            queue.assert_not_called()
            for intent, expected_flag in ((lifecycle.ExitIntent.DISCARD, 0),
                                          (lifecycle.ExitIntent.SAVE_AT_NATIVE_DESTINATION, 8)):
                lifecycle.exit_application(self.client, intent=intent, process=self.process)
                initial = lifecycle.LIFECYCLE_RESULT.unpack(queue.call_args.args[1])
                self.assertEqual(initial[2], lifecycle.Action.EXIT_APPLICATION)
                self.assertEqual(initial[3], expected_flag)
                self.assertEqual(initial[-3].split(b"\0", 1)[0], b"")
            lifecycle.resolve_native_exit_save_path(self.client, process=self.process)
            target = lifecycle.LIFECYCLE_RESULT.unpack(queue.call_args.args[1])
            self.assertEqual(target[2], lifecycle.Action.RESOLVE_EXIT_SAVE_PATH)
            self.assertEqual(target[3], 0)
            self.assertEqual(target[-3].split(b"\0", 1)[0], b"")

    def test_application_exit_rejects_non_enum_intent_before_submission(self):
        with patch.object(lifecycle, "submit_callback", return_value=Mock()) as queue:
            for intent in (1, True, "save", object()):
                with self.subTest(intent=intent):
                    with self.assertRaises(TypeError):
                        lifecycle.exit_application(self.client, intent=intent, process=self.process)
            queue.assert_not_called()

    def test_unknown_application_exit_preserves_request_without_automatic_retry(self):
        self.process.resolve_export.return_value = 0x10000
        self.process.allocate.return_value = 0x20000
        self.interface_callback.side_effect = BridgeError("ReadFile(pipe)", 109)
        with self.assertRaises(BridgeError) as caught:
            lifecycle.exit_application(self.client, intent=lifecycle.ExitIntent.DISCARD,
                                       process=self.process)
        query = caught.exception.query
        self.assertIs(query.client, self.client)
        self.assertIs(query.process, self.process)
        self.assertEqual(query.result_address, 0x20000)
        self.process.free.assert_not_called()
        self.interface_callback.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            query.read()
        self.interface_callback.assert_called_once()
        self.process.free.assert_not_called()

    def test_completed_exit_callback_only_reports_quit_requested(self):
        self.interface_callback.return_value = {"status": "ok", "error": 0, "request_id": 7}
        self.world_status.return_value = {"status": "ok", "completed_request_id": 7}
        self.process.resolve_export.return_value = 0x10000
        self.process.allocate.return_value = 0x20000
        sample = result(error=0, action=lifecycle.Action.EXIT_APPLICATION, flags=4,
                        world=0x60000, active_idler=0x70000, frame=90)
        packed = [getattr(sample, field.name) for field in fields(sample)]
        packed[-3:] = [value.encode("utf-8") for value in packed[-3:]]
        self.process.read.return_value = lifecycle.LIFECYCLE_RESULT.pack(*packed)
        query = lifecycle.exit_application(self.client, intent=lifecycle.ExitIntent.DISCARD,
                                           process=self.process)
        completed = query.read()
        self.assertTrue(completed.quit_requested)
        self.assertEqual(completed.world, 0x60000)
        self.assertEqual(completed.active_idler, 0x70000)
        self.assertEqual(completed.frame, 90)
        self.assertFalse(result(flags=8).quit_requested)
        self.assertIs(query.read(), completed)
        self.interface_callback.assert_called_once()
        self.process.free.assert_called_once_with(0x20000)

    def test_completed_callback_can_report_save_task_still_pending(self):
        self.interface_callback.return_value = {"status": "ok", "error": 0, "request_id": 7}
        self.world_status.return_value = {"status": "ok", "completed_request_id": 7}
        self.process.resolve_export.return_value = 0x10000
        self.process.allocate.return_value = 0x20000
        sample = result(error=0, native_result=997, task_state=1, task_completed=0)
        packed = [getattr(sample, field.name) for field in fields(sample)]
        packed[-3:] = [value.encode("utf-8") for value in packed[-3:]]
        self.process.read.return_value = lifecycle.LIFECYCLE_RESULT.pack(*packed)
        query = lifecycle.save(self.client, sample.path, process=self.process)
        completed = query.read()
        self.assertEqual(lifecycle.LIFECYCLE_RESULT.size, 2208)
        self.assertEqual(completed.error, 0)
        self.assertEqual(completed.native_result, 997)
        self.assertFalse(completed.saved)
        self.assertEqual(completed.owned_task_handle, 0x50000)
        self.process.free.assert_called_once_with(0x20000)


if __name__ == "__main__":
    unittest.main()
