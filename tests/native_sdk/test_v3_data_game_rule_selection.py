"""Copied typed game-rule selection wire/carrier tests. No game is attached.
SPDX-License-Identifier: MPL-2.0
"""
from dataclasses import fields
import struct
import unittest
from unittest.mock import Mock, patch

from pdx_runtime import v3_data as data, v3_access as access


HEADER_FIELDS = (
    "size error rule_input_offset rule_input_bytes setting_input_offset setting_input_bytes "
    "output_offset output_bytes stage outcome root_observed root_found row_observed row_found "
    "count_valid choice_count current_found target_found current_index target_index direction "
    "planned completed stopped receipts_offset receipts_count keys_offset keys_count "
    "final_observed final_selected execution_thread_id initial_key_valid initial_key_offset "
    "initial_key_bytes final_key_valid final_key_offset final_key_bytes receipts_bytes "
    "copied_output_bytes observation_error"
).split()


class GameRuleSelectionTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=1)
        self.status = {"status": "ok", "request_id": 7, "completed_request_id": 7}
        self.client.request.side_effect = lambda command: (
            {"status": "ok", "request_id": 7, "error": 0}
            if command.startswith("interface-callback ") else self.status)
        self.process = Mock(pid=1)
        self.process.resolve_export.side_effect = lambda adapter, symbol, **kwargs: (
            0xA100 if symbol == "PdxV3DataSelectGameRuleInterface" else 0xB100)
        self.process.export_owner.return_value = 0xA000
        self.process.allocate.return_value = 0x2000
        self.base = access.PAYLOAD_OFFSET

    def submit(self, **kwargs):
        query = data.select_game_rule(self.client, 'rule".C', 'setting\0tail',
                                     output_bytes=kwargs.pop("output_bytes", 4096),
                                     process=self.process, **kwargs)
        self.raw = bytearray(self.process.write.call_args.args[1])
        return query

    def header(self):
        return dict(zip(HEADER_FIELDS, data.GAME_RULE_SELECTION.unpack_from(self.raw, self.base)))

    def set_header(self, **values):
        h = self.header()
        h.update(values)
        data.GAME_RULE_SELECTION.pack_into(self.raw, self.base, *(h[name] for name in HEADER_FIELDS))

    def receipt(self, *, error=0, invoked=1, accepted=1, flags=1, compiled_mode=4):
        # This is the ACTUAL existing encoder. The native copied receipt is the
        # exact 416-byte action header + EMPTY TypedInput, with no expression.
        raw = bytearray(data.encode_action_request("", data.TypedInput.empty(), output_bytes=0))
        self.assertEqual(len(raw), 416)
        h = list(data.REQUEST.unpack_from(raw))
        h[1], h[3], h[10], h[11] = error, flags, 91, data.Kind.EMPTY
        data.REQUEST.pack_into(raw, 0, *h)
        a = list(data.ACTION_SUFFIX.unpack_from(raw, data.REQUEST.size))
        a[2:5] = compiled_mode, invoked, accepted
        data.ACTION_SUFFIX.pack_into(raw, data.REQUEST.size, *a)
        return bytes(raw)

    def returned(self, *, keys=(b"old", b"new"), initial=b"old", final=b"new",
                 receipts=(), scope_error=0, **values):
        h = list(access.REQUEST.unpack_from(self.raw))
        h[1], h[11], h[12], h[13] = scope_error, 91, 1, 1
        access.REQUEST.pack_into(self.raw, 0, *h)
        cursor = self.header()["output_offset"]
        keys_offset = cursor if keys else 0
        cursor += len(keys) * data.PAGE_SPAN.size
        for i, key in enumerate(keys):
            data.PAGE_SPAN.pack_into(self.raw, self.base + keys_offset + i * data.PAGE_SPAN.size,
                                     cursor, len(key))
            self.raw[self.base + cursor:self.base + cursor + len(key)] = key
            cursor += len(key)
        initial_offset = cursor if initial is not None else 0
        if initial is not None:
            self.raw[self.base + cursor:self.base + cursor + len(initial)] = initial
            cursor += len(initial)
        final_offset = cursor if final is not None else 0
        if final is not None:
            self.raw[self.base + cursor:self.base + cursor + len(final)] = final
            cursor += len(final)
        cursor = (cursor + 7) & ~7
        receipts_offset = cursor if receipts else 0
        for receipt in receipts:
            self.raw[self.base + cursor:self.base + cursor + len(receipt)] = receipt
            cursor += len(receipt)
        self.assertLessEqual(cursor, self.header()["size"])
        self.set_header(error=0, stage=9, outcome=6, root_observed=1, root_found=1,
                        row_observed=1, row_found=1, count_valid=1, choice_count=len(keys),
                        current_found=int(initial is not None), target_found=1,
                        current_index=0, target_index=1, direction=int(bool(receipts)),
                        planned=len(receipts), completed=len(receipts), stopped=0,
                        receipts_offset=receipts_offset, receipts_count=len(receipts),
                        receipts_bytes=sum(map(len, receipts)), keys_offset=keys_offset,
                        keys_count=len(keys), initial_key_valid=int(initial is not None),
                        initial_key_offset=initial_offset, initial_key_bytes=len(initial or b""),
                        final_key_valid=int(final is not None), final_key_offset=final_offset,
                        final_key_bytes=len(final or b""), final_observed=int(final is not None),
                        final_selected=int(final is not None), execution_thread_id=91,
                        copied_output_bytes=cursor - self.header()["output_offset"])
        self.set_header(**values)
        self.process.read.return_value = bytes(self.raw)

    def flush(self):
        self.process.read.return_value = bytes(self.raw)

    def direct(self):
        h = self.header()
        return data.GameRuleSelectionResult.from_bytes(bytes(self.raw[self.base:self.base + h["size"]]))

    def assert_retained(self, query, error):
        self.assertIs(error.query, query)
        self.assertEqual(error.copied_bytes, self.process.read.return_value)
        self.assertEqual(query.copied_bytes, error.copied_bytes)
        self.assertIs(error.completion_status, query.completion_status)
        self.assertIs(error.partial_result, query.partial_result)
        self.process.free.assert_not_called()
        self.process.close.assert_not_called()
        self.assertEqual(self.process.allocate.call_count, 1)
        self.assertEqual(self.process.read.call_count, 1)
        self.assertEqual(self.client.request.call_count, 2)

    def test_exact_header_and_cstring_bytes_without_dsl_policy(self):
        self.assertEqual(data.GAME_RULE_SELECTION.format, "<40I")
        self.assertEqual(data.GAME_RULE_SELECTION.size, 160)
        self.assertEqual([field.name for field in fields(data.GameRuleSelectionResult)][:40], HEADER_FIELDS)
        self.assertEqual(data.ACTION_HEADER_SIZE, 392)
        for rule, setting in (("", ""), ('rule"\0tail', '[Country.Self]\0"新设置'),
                              ("GetGameRules.SetPresetName('x')", 'converter-shaped:国家"')):
            with self.subTest(rule=rule):
                raw = data.encode_game_rule_selection(rule, setting, output_bytes=17)
                h = data.GAME_RULE_SELECTION.unpack_from(raw)
                self.assertEqual((h[0], h[1], h[7]), (len(raw), 997, 17))
                for text, offset, length in ((rule, h[2], h[3]), (setting, h[4], h[5])):
                    self.assertEqual(offset % 8, 0)
                    self.assertEqual(raw[offset:offset + length], data.TypedInput.string(text).pack())
                    self.assertEqual(data.ANY_INPUT.unpack_from(raw, offset),
                                     (data.Kind.STRING, len(text.encode("utf-8")), 0, 0))
                    self.assertEqual(raw[offset + 24:offset + length], text.encode("utf-8"))
                self.assertEqual(h[6] % 8, 0)
                self.assertEqual(raw[h[6]:], bytes(17))
        self.process.allocate.assert_not_called()

    def test_wrapper_uses_actual_typed_encoder_one_access_allocation_queue_and_data_dll(self):
        query = self.submit(adapter=0xA000, access_adapter=0xB000)
        self.assertIsInstance(query, data.CallbackResult)
        self.assertEqual(query.data_adapter, 0xA000)
        self.process.export_owner.assert_called_once_with(0xA100)
        resolutions = [(call.args[0], call.args[1]) for call in self.process.resolve_export.call_args_list]
        self.assertEqual(resolutions, [(0xA000, "PdxV3DataSelectGameRuleInterface"),
                                       (0xB000, "PdxV3AccessCallback")])
        h = access.REQUEST.unpack_from(self.raw)
        self.assertEqual((h[2], h[3], h[4]), (1, 0, 0xA100))
        self.assertEqual(h[5], query.callee_address)
        self.assertEqual(query.callee_address, query.result_address + access.PAYLOAD_OFFSET)
        payload = data.encode_game_rule_selection('rule".C', 'setting\0tail', output_bytes=4096)
        self.assertEqual(self.raw[self.base:self.base + len(payload)], payload)
        self.assertEqual(self.process.allocate.call_count, 1)
        self.assertEqual(self.client.request.call_count, 1)
        self.assertTrue(self.client.request.call_args.args[0].startswith("interface-callback "))

    def test_empty_and_nul_strings_reach_real_wrapper(self):
        for rule, setting in (("", ""), ('"\0', "[Country.Self]\0设置")):
            data.select_game_rule(self.client, rule, setting, output_bytes=0, process=self.process)
            raw = self.process.write.call_args.args[1][self.base:]
            h = data.GAME_RULE_SELECTION.unpack_from(raw)
            self.assertEqual(raw[h[2]:h[2] + h[3]], data.TypedInput.string(rule).pack())
            self.assertEqual(raw[h[4]:h[4] + h[5]], data.TypedInput.string(setting).pack())

    def test_no_action_outcomes_do_not_fabricate_accepted(self):
        cases = (
            dict(outcome=1, error=13, observation_error=13, root_found=0, row_observed=0,
                 row_found=0, count_valid=0, choice_count=0),
            dict(outcome=2, choice_count=0),
            dict(outcome=3, target_found=0),
            dict(outcome=4, current_found=0),
            dict(outcome=5, current_index=1, target_index=1),
        )
        for case in cases:
            with self.subTest(case=case):
                self.setUp()
                query = self.submit()
                absent = case["outcome"] in (1, 2)
                current = (None if absent else b"new" if case["outcome"] == 5
                           else b"nonmember" if case["outcome"] == 4 else b"old")
                self.returned(keys=() if absent else (b"old", b"new"),
                              initial=current, final=current if case["outcome"] == 5 else None,
                              receipts=(), **case)
                result = query.read()
                payload = result.callee_result
                self.assertIsInstance(result, access.AccessResult)
                self.assertEqual(payload.outcome, case["outcome"])
                self.assertEqual((payload.error, payload.receipts_count, payload.planned, payload.completed),
                                 (case.get("error", 0), 0, 0, 0))
                self.assertEqual(payload.observation_error, case.get("observation_error", 0))
                self.assertEqual(payload.native_results, ())
                self.assertFalse(hasattr(payload, "accepted"))
                self.assertTrue(payload.complete)
                self.process.free.assert_called_once_with(query.result_address)

    def test_actual_canonical_keys_and_observed_empty_values_are_not_input_echoes(self):
        query = self.submit()
        self.returned(keys=(b"native\0old", "新设置".encode(), b""), initial=b"", final=b"native\0old")
        payload = query.read().callee_result
        self.assertEqual(payload.key_bytes, (b"native\0old", "新设置".encode(), b""))
        self.assertEqual(payload.keys, ("native\0old", "新设置", ""))
        self.assertEqual((payload.initial_key_valid, payload.initial_key_value, payload.initial_key), (1, b"", ""))
        self.assertEqual((payload.final_key_value, payload.final_key), (b"native\0old", "native\0old"))
        self.assertNotEqual(payload.copied_bytes, payload.copied_output_bytes)
        self.assertEqual(payload.copied_bytes, payload.size)

    def test_unobserved_key_values_stay_none(self):
        query = self.submit()
        self.returned(keys=(), initial=None, final=None, final_observed=0)
        payload = query.read().callee_result
        self.assertEqual((payload.initial_key_valid, payload.initial_key, payload.initial_key_value), (0, None, None))
        self.assertEqual((payload.final_key_valid, payload.final_key, payload.final_key_value), (0, None, None))

    def test_original_action_flags_errors_and_order_are_preserved(self):
        query = self.submit()
        self.returned(receipts=(self.receipt(error=234, invoked=1, accepted=1, flags=0xA5, compiled_mode=3),
                                self.receipt(error=5, invoked=1, accepted=0, flags=0xB6, compiled_mode=4)),
                      error=234, stopped=1, outcome=7)
        payload = query.read().callee_result
        self.assertEqual((payload.error, payload.stopped, payload.outcome), (234, 1, 7))
        first, second = payload.native_results
        self.assertEqual((first.error, first.invoked, first.accepted, first.result.flags, first.compiled_mode),
                         (234, 1, 1, 0xA5, 3))
        self.assertEqual((second.error, second.invoked, second.accepted, second.result.flags), (5, 1, 0, 0xB6))
        self.assertEqual((first.result.size, first.result.action, first.input_kind), (416, 8, data.Kind.EMPTY))
        self.assertEqual((first.result.value_handle, second.result.value_handle), (0, 0))
        self.assertTrue(payload.complete)
        self.process.free.assert_called_once()

    def test_completed_output_partial_error234_keeps_real_short_prefix_and_frees_storage(self):
        query = self.submit()
        self.returned(keys=(b"only-copied",), initial=b"old", final=None,
                      receipts=(self.receipt(error=234),), error=234, outcome=8,
                      choice_count=80, planned=79, completed=1)
        result = query.read()
        payload = result.callee_result
        self.assertEqual((result.error, payload.error, payload.outcome), (0, 234, 8))
        self.assertEqual((payload.choice_count, payload.keys_count, payload.key_bytes), (80, 1, (b"only-copied",)))
        self.assertTrue(payload.complete)
        self.process.free.assert_called_once()
        self.assertEqual(self.client.request.call_count, 2)

    def test_later_observation_error_does_not_displace_original_action_error(self):
        query = self.submit()
        self.returned(receipts=(self.receipt(error=234, accepted=1),),
                      error=234, observation_error=13, outcome=9,
                      final_observed=1, final_selected=0, final=None)
        payload = query.read().callee_result
        self.assertEqual((payload.error, payload.observation_error, payload.outcome), (234, 13, 9))
        self.assertEqual((payload.native_results[0].error, payload.native_results[0].accepted), (234, 1))
        self.assertEqual((payload.final_key_valid, payload.final_key, payload.final_observed), (0, None, 1))
        self.assertTrue(payload.complete)
        self.process.free.assert_called_once()

    def test_malformed_allocation_preserves_header_and_is_not_complete(self):
        query = self.submit()
        self.returned(keys=(), initial=None, final=None, error=13, observation_error=13)
        self.set_header(output_offset=self.header()["size"] + 8)
        self.flush()
        with self.assertRaises(ValueError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        payload = caught.exception.partial_result.callee_result
        self.assertEqual((payload.error, payload.observation_error), (13, 13))
        self.assertFalse(payload.complete)

    def test_no_32_action_gameplay_cap(self):
        query = self.submit(output_bytes=65536)
        self.returned(receipts=tuple(self.receipt() for _ in range(40)), planned=40, completed=40, choice_count=81)
        payload = query.read().callee_result
        self.assertEqual(len(payload.native_results), 40)
        self.assertEqual(payload.planned, 40)
        self.assertTrue(payload.complete)

    def test_fault_preserves_current_entered_receipt_beyond_completed(self):
        query = self.submit()
        self.returned(receipts=(self.receipt(), self.receipt(error=997, accepted=0)),
                      completed=1, scope_error=574, error=997, stage=7)
        self.status["callback_error"] = 574
        with self.assertRaises(data.GameRuleSelectionReadError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        payload = caught.exception.partial_result.callee_result
        self.assertEqual((payload.error, payload.completed, len(payload.native_results)), (997, 1, 2))
        self.assertEqual(payload.native_results[1].error, 997)
        self.assertEqual(caught.exception.partial_result.error, 574)
        self.assertEqual(payload.keys, ("old", "new"))

    def test_outer_access_error_retains_even_nonpending_complete_payload(self):
        query = self.submit()
        self.returned(receipts=(self.receipt(),), scope_error=574)
        with self.assertRaises(data.GameRuleSelectionReadError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        self.assertTrue(caught.exception.partial_result.callee_result.complete)
        self.assertEqual(caught.exception.partial_result.error, 574)

    def test_pending_status_has_no_copy_free_or_replay(self):
        query = self.submit()
        self.status["completed_request_id"] = 6
        self.assertIsNone(query.read())
        self.assertIsNone(query.completion_status)
        self.process.read.assert_not_called()
        self.process.free.assert_not_called()
        self.assertEqual(self.client.request.call_count, 2)
        self.assertEqual(self.process.allocate.call_count, 1)

    def test_pending_payload_retains_raw_later_status_without_relabel_or_extra_rpc(self):
        query = self.submit()
        self.returned(error=997)
        self.status.update(request_id=9, completed_request_id=9, callback_error=574)
        with self.assertRaises(data.GameRuleSelectionReadError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        self.assertEqual(caught.exception.completion_status, self.status)
        self.assertEqual((caught.exception.partial_result.error,
                          caught.exception.partial_result.callee_result.error), (0, 997))
        self.assertEqual(query.request_id, 7)

    def recovered_unknown_sequence(self, *, owns_process=False):
        steps = (
            data.ActionStep("GetGameRules.SetPresetName", data.TypedInput.string("name"),
                            data.ActionContinuation.INVOKED_AND_ACCEPTED, output_bytes=0),
            data.ActionStep("GetGameRules.SavePreset", data.TypedInput.empty(),
                            data.ActionContinuation.ALWAYS, output_bytes=0),
        )
        fault = RuntimeError("original unknown sequence delivery")
        self.client.request.side_effect = fault
        with patch.object(data, "RemoteProcess", return_value=self.process):
            with self.assertRaises(RuntimeError) as caught:
                data.invoke_action_sequence(self.client, steps,
                    process=None if owns_process else self.process)
        self.assertIs(caught.exception, fault)
        query = fault.query
        self.assertIsNone(query.request_id)
        self.assertEqual(query.data_adapter, 0xA000)
        self.assertEqual(query._owns_process, owns_process)
        self.assertEqual(self.client.request.call_count, 1)
        self.raw = bytearray(self.process.write.call_args.args[1])
        scope = list(access.REQUEST.unpack_from(self.raw))
        scope[1], scope[12], scope[13] = 0, 1, 1
        access.REQUEST.pack_into(self.raw, 0, *scope)
        sequence = list(data.ACTION_SEQUENCE.unpack_from(self.raw, self.base))
        sequence[1], sequence[4] = 0, 2
        data.ACTION_SEQUENCE.pack_into(self.raw, self.base, *sequence)
        pointers = []
        for index in range(2):
            pointer, _ = data.ACTION_SEQUENCE_STEP.unpack_from(self.raw,
                self.base + sequence[3] + index * data.ACTION_SEQUENCE_STEP.size)
            pointers.append(self.base + pointer)
            result = list(data.REQUEST.unpack_from(self.raw, pointers[-1]))
            result[1], result[11], result[14] = 0, data.Kind.EMPTY, 100 + index
            data.REQUEST.pack_into(self.raw, pointers[-1], *result)
            action = list(data.ACTION_SUFFIX.unpack_from(self.raw, pointers[-1] + data.REQUEST.size))
            action[2:5] = 4, 1, 1
            data.ACTION_SUFFIX.pack_into(self.raw, pointers[-1] + data.REQUEST.size, *action)
        self.client.request.side_effect = lambda _: self.status
        self.assertIs(query.recover(7), query)
        return query, sequence, pointers

    def test_unknown_sequence_recovery_retains_native_pending_same_query(self):
        query, sequence, pointers = self.recovered_unknown_sequence()
        sequence[1], sequence[4] = 997, 1
        data.ACTION_SEQUENCE.pack_into(self.raw, self.base, *sequence)
        struct.pack_into("<I", self.raw, pointers[1] + 4, 997)
        self.flush()
        self.status.update(request_id=9, completed_request_id=9, callback_error=574)
        with self.assertRaises(data.ActionSequenceReadError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        payload = caught.exception.partial_result.callee_result
        self.assertEqual((caught.exception.partial_result.error, payload.error, payload.completed), (0, 997, 1))
        self.assertEqual(len(payload.native_results), 2)
        self.assertEqual((payload.native_results[1].error, payload.native_results[1].value_handle), (997, 101))
        self.assertEqual(query.request_id, 7)
        self.assertEqual(caught.exception.completion_status, self.status)

    def test_unknown_sequence_recovery_retains_shortcopy_and_owned_process(self):
        query, sequence, pointers = self.recovered_unknown_sequence(owns_process=True)
        self.process.read.return_value = bytes(self.raw[:pointers[1] + data.ACTION_HEADER_SIZE])
        with self.assertRaises(data.ActionSequenceReadError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        payload = caught.exception.partial_result.callee_result
        self.assertFalse(payload.complete)
        self.assertEqual((payload.error, payload.completed, len(payload.native_results)), (0, 2, 2))
        self.assertEqual(payload.native_results[1].value_handle, 101)
        self.assertTrue(query._owns_process)

    def test_unknown_sequence_recovery_retains_outer_fault_and_all_receipts(self):
        query, sequence, pointers = self.recovered_unknown_sequence()
        struct.pack_into("<I", self.raw, 4, 574)
        sequence[1], sequence[4] = 234, 0
        data.ACTION_SEQUENCE.pack_into(self.raw, self.base, *sequence)
        struct.pack_into("<I", self.raw, pointers[0] + 4, 234)
        self.flush()
        with self.assertRaises(data.ActionSequenceReadError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        payload = caught.exception.partial_result.callee_result
        self.assertEqual((caught.exception.partial_result.error, payload.error, payload.completed), (574, 234, 0))
        self.assertEqual(tuple(item.value_handle for item in payload.native_results), (100, 101))
        self.assertEqual((payload.native_results[0].error, payload.native_results[0].accepted), (234, 1))

    def test_unknown_sequence_recovery_preserves_exact_decoder_error_and_prefix(self):
        query, sequence, pointers = self.recovered_unknown_sequence()
        self.flush()
        first = data.ActionResult.from_bytes(bytes(self.raw[pointers[0]:pointers[1]]), allow_partial=True)
        fault = ValueError("original recovered second action decode error")
        with patch.object(data.ActionResult, "from_bytes", side_effect=(first, fault)) as decoder:
            with self.assertRaises(ValueError) as caught:
                query.read()
        self.assertIs(caught.exception, fault)
        self.assert_retained(query, fault)
        partial = fault.partial_result
        self.assertIsInstance(partial, access.AccessResult)
        self.assertIs(fault.partial_sequence, partial.callee_result)
        self.assertEqual(fault.partial_sequence.native_results, (first,))
        self.assertEqual((partial.error, partial.callee_result.error, partial.callee_result.completed), (0, 0, 2))
        self.assertEqual(first.value_handle, 100)
        self.assertEqual(decoder.call_count, 2)

    def test_sequence_short_outer_allocation_tail_retains_complete_access_payload(self):
        step = data.ActionStep("GetGameRules.SavePreset", data.TypedInput.empty(),
                               data.ActionContinuation.ALWAYS, output_bytes=0)
        query = data.invoke_action_sequence(self.client, (step,), process=self.process)
        self.raw = bytearray(self.process.write.call_args.args[1])
        scope = list(access.REQUEST.unpack_from(self.raw))
        scope[1], scope[12], scope[13] = 0, 1, 1
        access.REQUEST.pack_into(self.raw, 0, *scope)
        sequence = list(data.ACTION_SEQUENCE.unpack_from(self.raw, self.base))
        sequence[1], sequence[4] = 0, 1
        data.ACTION_SEQUENCE.pack_into(self.raw, self.base, *sequence)
        pointer, policy = data.ACTION_SEQUENCE_STEP.unpack_from(self.raw, self.base + sequence[3])
        self.assertEqual(policy, data.ActionContinuation.ALWAYS)
        at = self.base + pointer
        result = list(data.REQUEST.unpack_from(self.raw, at))
        result[1], result[11] = 0, data.Kind.EMPTY
        data.REQUEST.pack_into(self.raw, at, *result)
        action = list(data.ACTION_SUFFIX.unpack_from(self.raw, at + data.REQUEST.size))
        action[2:5] = 4, 1, 1
        data.ACTION_SUFFIX.pack_into(self.raw, at + data.REQUEST.size, *action)
        self.process.read.return_value = bytes(self.raw[:self.base + sequence[0]])
        with self.assertRaises(data.ActionSequenceReadError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        partial = caught.exception.partial_result
        self.assertIsInstance(partial, access.AccessResult)
        payload = partial.callee_result
        self.assertEqual((partial.error, partial.entered, partial.left), (0, 1, 1))
        self.assertTrue(payload.complete)
        self.assertEqual((payload.error, payload.completed, len(payload.native_results)), (0, 1, 1))
        self.assertEqual((payload.native_results[0].invoked, payload.native_results[0].accepted), (1, 1))
        self.assertEqual(payload.copied_bytes, payload.size)
        self.assertLess(len(caught.exception.copied_bytes), query.result_size)

    def test_short_outer_allocation_tail_retains_even_with_complete_payload(self):
        query = self.submit()
        self.returned()
        # Access also owns its source bytes. A complete callee is not proof
        # that the original allocation copy has completed.
        self.process.read.return_value = bytes(self.raw[:self.base + self.header()["size"]])
        with self.assertRaises(data.GameRuleSelectionReadError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        payload = caught.exception.partial_result.callee_result
        self.assertTrue(payload.complete)
        self.assertEqual(payload.copied_bytes, payload.size)
        self.assertLess(len(caught.exception.copied_bytes), query.result_size)

    def test_short_copy_preserves_last_action_header_and_never_slices_at_completed(self):
        query = self.submit()
        self.returned(receipts=(self.receipt(), self.receipt(error=234)), completed=0)
        h = self.header()
        cutoff = self.base + h["receipts_offset"] + 416 + data.ACTION_HEADER_SIZE
        self.process.read.return_value = bytes(self.raw[:cutoff])
        with self.assertRaises(data.GameRuleSelectionReadError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        payload = caught.exception.partial_result.callee_result
        self.assertEqual((payload.completed, len(payload.native_results)), (0, 2))
        self.assertEqual(payload.native_results[1].error, 234)
        self.assertEqual(payload.copied_bytes, cutoff - self.base)
        self.assertLess(payload.copied_bytes, payload.size)
        self.assertFalse(payload.complete)

    def test_short_copy_before_later_header_preserves_prior_receipt(self):
        query = self.submit()
        self.returned(receipts=(self.receipt(), self.receipt()))
        cutoff = self.base + self.header()["receipts_offset"] + 416 + 20
        self.process.read.return_value = bytes(self.raw[:cutoff])
        with self.assertRaises(data.GameRuleSelectionReadError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        payload = caught.exception.partial_result.callee_result
        self.assertEqual(len(payload.native_results), 1)
        self.assertEqual(payload.initial_key, "old")
        self.assertEqual(payload.final_key, "new")

    def test_short_copy_key_is_unknown_not_a_false_partial_identity(self):
        query = self.submit()
        self.returned(initial=b"original-current", final=None)
        cutoff = self.base + self.header()["initial_key_offset"] + 2
        self.process.read.return_value = bytes(self.raw[:cutoff])
        with self.assertRaises(data.GameRuleSelectionReadError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        payload = caught.exception.partial_result.callee_result
        self.assertEqual(payload.keys, ("old", "new"))
        self.assertEqual((payload.initial_key_valid, payload.initial_key_bytes), (1, 16))
        self.assertIsNone(payload.initial_key_value)

    def assert_later_malformed(self, defect):
        query = self.submit()
        self.returned(receipts=(self.receipt(error=234), self.receipt()), completed=0)
        h = self.header()
        second = self.base + h["receipts_offset"] + 416
        if defect == "size":
            struct.pack_into("<I", self.raw, second, 415)
        elif defect == "action":
            struct.pack_into("<I", self.raw, second + 8, data.Action.SNAPSHOT)
        elif defect == "data_result":
            r = list(data.REQUEST.unpack_from(self.raw, second))
            r[19], r[24] = 1, 417
            data.REQUEST.pack_into(self.raw, second, *r)
        elif defect == "input":
            data.ANY_INPUT.pack_into(self.raw, second + data.ACTION_HEADER_SIZE, data.Kind.BOOL, 0, 0, 0)
        else:
            self.set_header(receipts_bytes=416)
        self.flush()
        with self.assertRaises(ValueError) as caught:
            query.read()
        error = caught.exception
        self.assert_retained(query, error)
        payload = error.partial_result.callee_result
        self.assertIs(error.partial_game_rule_selection, payload)
        self.assertEqual((payload.error, payload.completed, len(payload.native_results)), (0, 0, 1))
        self.assertEqual(payload.native_results[0].error, 234)
        self.assertEqual((payload.keys, payload.initial_key, payload.final_key), (("old", "new"), "old", "new"))
        self.assertFalse(payload.complete)
        with self.assertRaises(ValueError) as direct:
            self.direct()
        self.assertEqual(str(direct.exception), str(error))
        self.assertEqual(direct.exception.partial_game_rule_selection.native_results, payload.native_results)

    def test_later_wrong_packet_size_preserves_prefix(self):
        self.assert_later_malformed("size")

    def test_later_wrong_action_discriminator_preserves_prefix(self):
        self.assert_later_malformed("action")

    def test_later_invalid_data_result_preserves_prefix(self):
        self.assert_later_malformed("data_result")

    def test_later_invalid_empty_input_preserves_prefix(self):
        self.assert_later_malformed("input")

    def test_later_receipt_span_preserves_prefix(self):
        self.assert_later_malformed("span")

    def test_later_key_span_error_preserves_original_header_and_canonical_prefix(self):
        query = self.submit()
        self.returned()
        h = self.header()
        data.PAGE_SPAN.pack_into(self.raw, self.base + h["keys_offset"] + 8, h["size"] + 8, 2)
        self.flush()
        with self.assertRaises(ValueError) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        payload = caught.exception.partial_result.callee_result
        self.assertEqual(payload.key_bytes, (b"old",))
        self.assertEqual((payload.keys_count, payload.choice_count, payload.final_selected), (2, 2, 1))
        self.assertFalse(payload.complete)

    def test_later_action_decoder_error_is_the_exact_original_exception(self):
        query = self.submit()
        self.returned(receipts=(self.receipt(error=234), self.receipt()))
        h = self.header()
        at = self.base + h["receipts_offset"]
        first = data.ActionResult.from_bytes(bytes(self.raw[at:at + 416]), allow_partial=True)
        fault = ValueError("original second action decode fault")
        with patch.object(data.ActionResult, "from_bytes", side_effect=(first, fault)) as decoder:
            with self.assertRaises(ValueError) as caught:
                query.read()
        self.assertIs(caught.exception, fault)
        self.assert_retained(query, fault)
        self.assertEqual(fault.partial_game_rule_selection.native_results, (first,))
        self.assertEqual(fault.partial_result.callee_result.initial_key, "old")
        self.assertEqual(decoder.call_count, 2)
        self.assertTrue(all(call.kwargs == {"allow_partial": True} for call in decoder.call_args_list))
        self.assertTrue(all(len(call.args[0]) == 416 for call in decoder.call_args_list))

    def test_undecodable_short_outer_copy_preserves_original_error_and_raw_snapshot(self):
        query = self.submit()
        self.process.read.return_value = b"\x01\x02"
        with self.assertRaises(struct.error) as caught:
            query.read()
        error = caught.exception
        self.assertIs(error.query, query)
        self.assertEqual((error.copied_bytes, query.copied_bytes), (b"\x01\x02", b"\x01\x02"))
        self.assertIs(error.completion_status, query.completion_status)
        self.process.free.assert_not_called()

    def test_short_payload_header_preserves_access_shaped_partial_and_original_error(self):
        query = self.submit()
        self.returned()
        self.process.read.return_value = bytes(self.raw[:self.base + 12])
        with self.assertRaises(struct.error) as caught:
            query.read()
        self.assert_retained(query, caught.exception)
        partial = caught.exception.partial_result
        self.assertIsInstance(partial, access.AccessResult)
        self.assertEqual(partial.error, 0)
        self.assertIsNone(partial.callee_result)
        self.assertEqual(partial.payload, bytes(self.raw[self.base:self.base + 12]))

    def test_unknown_submission_retains_original_query_and_decoder_on_recover(self):
        failure = RuntimeError("unknown delivery")
        self.client.request.side_effect = failure
        with self.assertRaises(RuntimeError) as caught:
            self.submit()
        self.assertIs(caught.exception, failure)
        query = failure.query
        self.assertIsNone(query.request_id)
        self.assertEqual(query.data_adapter, 0xA000)
        self.process.free.assert_not_called()
        self.raw = bytearray(self.process.write.call_args.args[1])
        self.returned(error=997)
        self.client.request.side_effect = lambda _: self.status
        self.assertIs(query.recover(7), query)
        with self.assertRaises(data.GameRuleSelectionReadError) as recovered:
            query.read()
        self.assert_retained(query, recovered.exception)
        self.assertIs(recovered.exception.query, query)
        self.assertEqual(query.request_id, 7)

    def test_same_query_can_copy_again_after_shortcopy_without_resubmitting(self):
        query = self.submit()
        self.returned(receipts=(self.receipt(),))
        full = bytes(self.raw)
        self.process.read.return_value = full[:self.base + self.header()["receipts_offset"] + 392]
        with self.assertRaises(data.GameRuleSelectionReadError):
            query.read()
        self.process.free.assert_not_called()
        self.process.read.return_value = full
        result = query.read()
        self.assertTrue(result.callee_result.complete)
        self.assertIs(query.read(), result)
        self.assertEqual(self.process.allocate.call_count, 1)
        self.assertEqual(self.process.read.call_count, 2)
        self.assertEqual(sum(call.args[0].startswith("interface-callback ") for call in self.client.request.call_args_list), 1)
        self.process.free.assert_called_once()

    def test_owned_process_remains_open_on_unknown_submission(self):
        fault = RuntimeError("unknown delivery")
        self.client.request.side_effect = fault
        with patch.object(data, "RemoteProcess", return_value=self.process):
            with self.assertRaises(RuntimeError) as caught:
                data.select_game_rule(self.client, "r", "s")
        self.assertIs(caught.exception, fault)
        self.assertTrue(fault.query._owns_process)
        self.process.free.assert_not_called()
        self.process.close.assert_not_called()

    def test_invalid_representability_rejected_before_allocation(self):
        for budget in (-1, 0x100000000, 0xffffffff, 1.5):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    data.select_game_rule(self.client, "r", "s", output_bytes=budget, process=self.process)
        with self.assertRaises(TypeError):
            data.select_game_rule(self.client, None, "s", process=self.process)
        with self.assertRaises(UnicodeEncodeError):
            data.select_game_rule(self.client, "\ud800", "s", process=self.process)
        self.process.allocate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
