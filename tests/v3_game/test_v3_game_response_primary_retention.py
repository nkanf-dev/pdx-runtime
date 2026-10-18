"""CPU response composition only; no process attachment, DLL, or game calls."""
from __future__ import annotations
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.query_executor import RetainedOwners
from pdx_runtime.v3_game import GameContext, PendingOperation
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.v3_game.diplomacy.treaty import accept as accept_module, proposal as proposal_module

WORLD, ACTOR, PROPOSAL = 0x50000, 0, 0x8000002B


class Query:
    def __init__(self, value=None, *, released=True):
        self.value, self._result, self._released = value, value, released


class Context:
    def __init__(self):
        self.native, self.process, self.session = object(), object(), object()
        self.world, self.image_base = WORLD, 0x140000000
        self.adapters, self.diagnostics, self.helpers = {}, [], {}
        self.owner_pool = RetainedOwners()
    async def refresh(self): return NS(world=self.world)
    async def country_id(self): return ACTOR
    async def perform(self, label, operation): return await operation()
    def support(self, name): return self.helpers[name]


def owner(command=0xD0000):
    return NS(command=command, pending=None,
        attempt=NS(outcome="owned", query=Query()),
        serialize=Mock(), validate=Mock(), submit=Mock(), destroy=Mock())


def native_state():
    return {"pending": None, "owned_buffers": [], "owned_values": [], "queries": [],
        "unadopted_clone": None, "unsettled_clone": None,
        "handoff_uncertain": False, "cleanup_uncertain": False,
        "unique_owner": owner(), "staging_owner": owner(0xE0000)}


def settle(state):
    # This models actual completed native cleanup, not a success flag alone.
    for name in ("unique_owner", "staging_owner", "owner", "clone_owner"):
        current = state.get(name)
        if current is not None:
            current.command, current.pending = 0, None
            current.attempt = NS(outcome="consumed", query=Query())
    state["owned_buffers"].clear()
    state["owned_values"].clear()


class ResponsePrimaryRetentionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = Context()
        self.api = DiplomacyAPI(self.ctx)
        self.key = (WORLD, PROPOSAL)

    def accept_fixture(self, *, accepted=True, error=0, outcome="consumed", valid=True,
                       effect=False, cleanup_result=True, after_error=None, cleanup_error=None):
        state = native_state()
        validation = NS(error=0, validated=valid, reason="native reject" if not valid else "")
        state.update(validation=validation, prepared_wire_sha256="native-wire")
        receipt = NS(error=error, accepted=accepted)
        async def submit(*args, **kwargs):
            current = state["unique_owner"]
            current.attempt = NS(outcome=outcome, query=Query(receipt))
            current.command = 0 if outcome == "consumed" else 0xD0000
            current.pending = current.attempt.query if outcome in ("pending", "unknown") else None
            state["handoff_uncertain"] = outcome in ("pending", "unknown")
            state["submit_result"] = receipt
            return receipt
        async def cleanup(current):
            if cleanup_error is not None: raise cleanup_error
            if cleanup_result: settle(current)
            return cleanup_result
        helper = NS(prepare_response=AsyncMock(return_value=state),
            submit_response=AsyncMock(side_effect=submit),
            observe_response_state=AsyncMock(side_effect=after_error,
                return_value={"native_draft_to_inforce_transition": effect}),
            cleanup_completed=AsyncMock(side_effect=cleanup))
        self.ctx.helpers["diplomacy.treaty.proposal"] = helper
        self.api._drafts = AsyncMock(return_value=[{"id": PROPOSAL, "complete": True}])
        self.api._controlled = AsyncMock()
        return helper, state, receipt

    async def test_accept_primary_is_in_original_record_before_after_exception(self):
        error = RuntimeError("after read failed")
        helper, state, receipt = self.accept_fixture(after_error=error)
        async def observe(current):
            self.assertIs(self.api._responses[self.key]["result"]["native_result"], receipt)
            self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
            raise error
        helper.observe_response_state.side_effect = observe
        result = await self.api.accept(PROPOSAL)
        self.assertIs(result["native_result"], receipt)
        self.assertIs(result["accepted"], True)
        self.assertIs(result["after_error"], error)
        self.assertFalse(result["completed"])
        self.assertIs(self.api._responses[self.key]["result"], result)
        self.assertFalse(any(item is state for item in self.ctx.owner_pool.snapshot()))
        helper.submit_response.assert_awaited_once()

    async def test_accept_known_submit_false_is_not_pending_and_settled_record_retires(self):
        helper, state, receipt = self.accept_fixture(accepted=False)
        result = await self.api.accept(PROPOSAL)
        self.assertIs(result["accepted"], False)
        self.assertIs(result["native_submit_accepted"], False)
        self.assertIs(result["native_result"], receipt)
        self.assertEqual(result["cleanup"]["status"], "completed")
        self.assertFalse(any(item is state for item in self.ctx.owner_pool.snapshot()))
        self.assertNotIn(self.key, self.api._responses)
        helper.observe_response_state.assert_not_awaited()

    async def test_real_integer_zero_submit_flag_retires_only_after_real_cleanup(self):
        helper, state, receipt = self.accept_fixture(accepted=0)
        result = await self.api.accept(PROPOSAL)
        self.assertEqual(result["accepted"], 0)
        self.assertIs(result["native_result"], receipt)
        self.assertNotIn(self.key, self.api._responses)
        self.assertFalse(any(item is state for item in self.ctx.owner_pool.snapshot()))

    async def test_raw_submit_flags_with_native_error_keep_original_and_unknown_owner(self):
        for flag in (True, False, None):
            with self.subTest(flag=flag):
                self.setUp()
                helper, state, receipt = self.accept_fixture(accepted=flag, error=5, outcome="unknown")
                result = await self.api.accept(PROPOSAL)
                self.assertIs(result["accepted"], flag)
                self.assertIs(result["native_submit_accepted"], flag)
                self.assertIs(result["native_result"], receipt)
                self.assertEqual(result["native_error"], 5)
                self.assertEqual(result["owner_outcome"], "unknown")
                self.assertFalse(result["completed"])
                self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
                self.assertIn(self.key, self.api._responses)
                helper.observe_response_state.assert_not_awaited()
                helper.cleanup_completed.assert_not_awaited()

    async def test_error_zero_unknown_owner_is_not_after_read_or_cleanup_permission(self):
        helper, state, receipt = self.accept_fixture(outcome="unknown")
        result = await self.api.accept(PROPOSAL)
        self.assertIs(result["accepted"], True)
        self.assertEqual(result["native_error"], 0)
        self.assertEqual(result["status"], "delivery_uncertain")
        self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
        helper.observe_response_state.assert_not_awaited()
        helper.cleanup_completed.assert_not_awaited()

    async def test_known_prequeue_error_keeps_raw_false_and_native_error_not_fake_pending(self):
        helper, state, receipt = self.accept_fixture(accepted=False, error=21, outcome="owned")
        result = await self.api.accept(PROPOSAL)
        self.assertIs(result["accepted"], False)
        self.assertIs(result["native_result"], receipt)
        self.assertEqual(result["native_error"], 21)
        self.assertEqual(result["owner_outcome"], "owned")
        helper.observe_response_state.assert_not_awaited()
        helper.cleanup_completed.assert_awaited_once()
        self.assertFalse(any(item is state for item in self.ctx.owner_pool.snapshot()))

    async def test_native_validation_reject_false_has_no_submit_receipt(self):
        helper, state, _ = self.accept_fixture(valid=False)
        result = await self.api.accept(PROPOSAL)
        self.assertIs(result["accepted"], False)
        self.assertIsNone(result["native_submit_accepted"])
        self.assertIsNone(result["native_result"])
        self.assertIs(result["validation"], state["validation"])
        self.assertEqual(result["reason"], "native reject")
        helper.submit_response.assert_not_awaited()
        self.assertNotIn(self.key, self.api._responses)

    async def test_cleanup_false_keeps_primary_actual_effect_and_response_cache(self):
        helper, state, receipt = self.accept_fixture(effect=True, cleanup_result=False)
        result = await self.api.accept(PROPOSAL)
        self.assertIs(result["accepted"], True)
        self.assertTrue(result["completed"])
        self.assertIs(result["native_result"], receipt)
        self.assertIs(result["cleanup"]["result"], False)
        self.assertIsInstance(result["cleanup"]["error"], RuntimeError)
        self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
        self.assertIn(self.key, self.api._responses)
        opposite = await self.api.decline(PROPOSAL)
        self.assertIs(opposite, result)
        helper.submit_response.assert_awaited_once()

    async def test_cleanup_exception_is_separate_original_error_with_false_primary(self):
        error = RuntimeError("destroy callback failed")
        helper, state, receipt = self.accept_fixture(accepted=False, cleanup_error=error)
        result = await self.api.accept(PROPOSAL)
        self.assertIs(result["accepted"], False)
        self.assertIs(result["native_result"], receipt)
        self.assertIs(result["cleanup"]["error"], error)
        self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
        self.assertIn(self.key, self.api._responses)

    async def test_after_cancel_keeps_same_object_primary_and_failed_task_for_opposite_call(self):
        error = asyncio.CancelledError("original after cancellation")
        helper, state, receipt = self.accept_fixture(after_error=error)
        task = asyncio.create_task(self.api.accept(PROPOSAL))
        with self.assertRaises(asyncio.CancelledError) as first:
            await task
        self.assertIs(first.exception, error)
        record = self.api._responses[self.key]
        self.assertIs(record["task"], task)
        self.assertIs(record["error"], error)
        self.assertIs(error.observed_result, record["result"])
        self.assertIs(record["result"]["native_result"], receipt)
        self.assertIs(record["result"]["after_error"], error)
        with self.assertRaises(asyncio.CancelledError) as second:
            await self.api.decline(PROPOSAL)
        self.assertIs(second.exception, error)
        self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
        helper.cleanup_completed.assert_not_awaited()
        helper.prepare_response.assert_awaited_once()
        helper.submit_response.assert_awaited_once()

    async def test_cancel_preserves_existing_original_fields_and_adds_current_response_facts(self):
        for original_state, original_result in ((object(), object()), (None, None)):
            with self.subTest(explicit_none=original_state is None):
                self.setUp()
                error = asyncio.CancelledError("original operation cancellation")
                error.query_state, error.observed_result = original_state, original_result
                helper, state, receipt = self.accept_fixture(after_error=error)
                task = asyncio.create_task(self.api.accept(PROPOSAL))
                with self.assertRaises(asyncio.CancelledError) as first:
                    await task
                self.assertIs(first.exception, error)
                record = self.api._responses[self.key]
                self.assertIs(record["task"], task)
                self.assertIs(record["error"], error)
                self.assertIs(error.query_state, original_state)
                self.assertIs(error.observed_result, original_result)
                self.assertIs(error.response_state, state)
                self.assertIs(error.response_result, record["result"])
                self.assertIs(error.response_result["native_result"], receipt)
                self.assertIs(error.response_result["accepted"], True)
                with self.assertRaises(asyncio.CancelledError) as second:
                    await self.api.decline(PROPOSAL)
                self.assertIs(second.exception, error)
                self.assertIs(self.api._responses[self.key]["task"], task)
                helper.prepare_response.assert_awaited_once()
                helper.submit_response.assert_awaited_once()
                helper.cleanup_completed.assert_not_awaited()

    async def test_cleanup_cancel_keeps_same_object_and_public_receipt(self):
        error = asyncio.CancelledError("original cleanup cancellation")
        helper, state, receipt = self.accept_fixture(cleanup_error=error)
        with self.assertRaises(asyncio.CancelledError) as caught:
            await self.api.accept(PROPOSAL)
        self.assertIs(caught.exception, error)
        self.assertIs(error.observed_result["native_result"], receipt)
        self.assertIs(error.observed_result["cleanup"]["error"], error)
        self.assertIs(self.api._responses[self.key]["error"], error)
        self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))

    async def test_prepare_error_state_joins_real_pool_and_valueerror_does_not_pop(self):
        helper, state, _ = self.accept_fixture()
        error = ValueError("native prepare terms changed")
        error.query_state = state
        helper.prepare_response.side_effect = error
        for operation in (self.api.accept, self.api.decline):
            with self.assertRaises(ValueError) as caught:
                await operation(PROPOSAL)
            self.assertIs(caught.exception, error)
        self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
        self.assertIs(self.api._responses[self.key]["state"], state)
        self.assertIsNone(self.api._responses[self.key]["result"])
        helper.prepare_response.assert_awaited_once()
        helper.submit_response.assert_not_awaited()

    async def test_submit_exception_remains_same_original_without_default_false_primary(self):
        helper, state, _ = self.accept_fixture()
        error = RuntimeError("original submit delivery unknown")
        helper.submit_response.side_effect = error
        for operation in (self.api.accept, self.api.decline):
            with self.assertRaises(RuntimeError) as caught:
                await operation(PROPOSAL)
            self.assertIs(caught.exception, error)
        self.assertIsNone(self.api._responses[self.key]["result"])
        self.assertIs(error.query_state, state)
        helper.prepare_response.assert_awaited_once()
        helper.submit_response.assert_awaited_once()

    async def test_completed_unreleased_query_stays_retained_even_if_helper_cleanup_true(self):
        helper, state, _ = self.accept_fixture(accepted=False)
        exact_query = Query(released=False)
        state["queries"].append(exact_query)
        result = await self.api.accept(PROPOSAL)
        self.assertEqual(state["unique_owner"].command, 0)
        self.assertEqual(state["staging_owner"].command, 0)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
        self.assertIn(self.key, self.api._responses)
        self.assertIs(state["queries"][0], exact_query)

    async def test_actual_settled_retirement_removes_only_same_attempt_identity(self):
        helper, state, _ = self.accept_fixture(accepted=False)
        other = native_state()
        proposal_module.ATTEMPTS.extend((state, other))
        accept_module.ATTEMPTS.extend((state, other))
        try:
            await self.api.accept(PROPOSAL)
            self.assertFalse(any(item is state for item in proposal_module.ATTEMPTS))
            self.assertFalse(any(item is state for item in accept_module.ATTEMPTS))
            self.assertTrue(any(item is other for item in proposal_module.ATTEMPTS))
            self.assertTrue(any(item is other for item in accept_module.ATTEMPTS))
        finally:
            proposal_module.ATTEMPTS[:] = [item for item in proposal_module.ATTEMPTS if item is not state and item is not other]
            accept_module.ATTEMPTS[:] = [item for item in accept_module.ATTEMPTS if item is not state and item is not other]

    async def test_real_core_pending_same_and_opposite_share_exact_task_query_and_receipt(self):
        helper, state, receipt = self.accept_fixture(effect=True)
        gate = asyncio.Event()
        class OriginalQuery:
            def __init__(self):
                self._released, self._result, self.reads = False, None, 0
            def read(self):
                self.reads += 1
                if not gate.is_set(): return None
                self._result, self._released = receipt, True
                return receipt
        query = OriginalQuery()
        factory = Mock(return_value=query)
        ctx = GameContext(NS(native=object(), namespace={}), process=object(), timeout=.001)
        ctx.world, ctx.image_base = WORLD, 0x140000000
        ctx.refresh, ctx.country_id = AsyncMock(), AsyncMock(return_value=ACTOR)
        ctx._support["diplomacy.treaty.proposal"] = helper
        api = DiplomacyAPI(ctx)
        api._drafts = AsyncMock(return_value=[{"id": PROPOSAL, "complete": True}])
        api._controlled = AsyncMock()
        async def submit(*args, **kwargs):
            state["pending"] = {"label": "original_submit", "query": query, "owner": state["unique_owner"]}
            value = await ctx.query("response.original_submit", factory)
            state["pending"] = None
            current = state["unique_owner"]
            current.command, current.pending = 0, None
            current.attempt = NS(outcome="consumed", query=query)
            state["submit_result"] = value
            return value
        helper.submit_response.side_effect = submit
        first = await api.accept(PROPOSAL)
        self.assertIsInstance(first, PendingOperation)
        exact_task = api._responses[self.key]["task"]
        opposite = await api.decline(PROPOSAL)
        self.assertIsInstance(opposite, PendingOperation)
        self.assertIs(api._responses[self.key]["task"], exact_task)
        self.assertIs(state["pending"]["query"], query)
        gate.set()
        a = await first.resume(timeout=1)
        b = await opposite.resume(timeout=1)
        self.assertIs(a, b)
        self.assertIs(a["native_result"], receipt)
        self.assertEqual(a["decision"], "accept")
        helper.submit_response.assert_awaited_once()
        helper.prepare_response.assert_awaited_once()
        factory.assert_called_once()

    def decline_fixture(self, *, accepted=True, error=0, outcome="consumed", valid=True,
                        after_error=None, cleanup_result=True, cleanup_error=None):
        state = native_state()
        state.pop("staging_owner")  # The genuine query state has no staging command.
        state.update(types={"Country": 983, "Treaty": 984}, owned_values=[11, 12])
        current = owner()
        receipt = NS(error=error, accepted=accepted)
        validation = NS(error=0, validated=valid, reason="decline rejected" if not valid else "")
        wire = commands.encode_command(14275, {0x2840: commands.UInt32(ACTOR),
            0x41: {0x240: commands.UInt32(ACTOR), 0x57D6: commands.UInt32(PROPOSAL), 0x37C5: False}})
        async def operation(actual, label, make, **kwargs):
            query = Query()
            actual["last_command_query"] = query
            if label == "clone_original_decline":
                return NS(error=0, type_token=14275, world=WORLD, cloned_command=0xD0000)
            current.attempt = NS(outcome="owned", query=query)
            if label == "serialize_original_decline": return NS(error=0, data=wire)
            if label == "validate_original_decline": return validation
            if label == "submit_original_decline_once":
                current.attempt = NS(outcome=outcome, query=query)
                current.command = 0 if outcome == "consumed" else 0xD0000
                current.pending = query if outcome in ("pending", "unknown") else None
                return receipt
            if label == "destroy_known_decline":
                current.command, current.pending = 0, None
                current.attempt = NS(outcome="consumed", query=query)
                return NS(error=0, command=0)
            raise AssertionError(label)
        async def complete(actual, label, make): return make().value
        async def evaluate(actual, label, expression, binding):
            return NS(value_handle=11 if expression == "Country.GetDraftTreaties" else 12)
        async def cleanup(actual):
            if cleanup_error is not None: raise cleanup_error
            if cleanup_result: settle(actual)
            return actual if cleanup_result else False
        async def types(actual, *args):
            actual["types"].update(Country=983, Treaty=984)
        q = NS(new_state=Mock(return_value=state), _types=AsyncMock(side_effect=types),
            _evaluate=AsyncMock(side_effect=evaluate), complete=AsyncMock(side_effect=complete),
            _owned_object=Mock(return_value=0xA0000), _full_id=AsyncMock(return_value=PROPOSAL),
            cleanup_completed=AsyncMock(side_effect=cleanup))
        helper = NS(TOKEN=14275, _one=accept_module._one, _operation=AsyncMock(side_effect=operation))
        self.ctx.helpers.update({"diplomacy.treaty.query": q, "diplomacy.treaty.accept": helper})
        self.api._controlled = AsyncMock()
        after = AsyncMock(side_effect=after_error, return_value=[])
        async def drafts(**kwargs):
            if kwargs: return [{"id": PROPOSAL, "complete": True}]
            return await after()
        self.api._drafts = AsyncMock(side_effect=drafts)
        carrier = data.NativeDataCommand(0xB0000, 0xB1000, 0xB0018, 20, "CDataCommand", WORLD)
        stack = ExitStack()
        stack.enter_context(patch("pdx_runtime.v3_game.diplomacy.data.list_count", return_value=Query(NS(total=1))))
        stack.enter_context(patch("pdx_runtime.v3_game.diplomacy.data.list_element", return_value=Query(NS(value_handle=13))))
        stack.enter_context(patch("pdx_runtime.v3_game.diplomacy.data.command_value", return_value=Query(NS(value=carrier))))
        stack.enter_context(patch("pdx_runtime.v3_game.diplomacy.commands.NativeCommand.from_result", return_value=current))
        self.addCleanup(stack.close)
        return helper, q, state, receipt, validation, after

    async def test_decline_after_exception_never_publishes_prefilled_false(self):
        error = RuntimeError("fresh drafts unavailable")
        helper, q, state, receipt, _, after = self.decline_fixture(after_error=error)
        result = await self.api.decline(PROPOSAL)
        self.assertIs(result["accepted"], True)
        self.assertIs(result["native_result"], receipt)
        self.assertIs(state["submission_receipt"], receipt)
        self.assertIs(result["after_error"], error)
        self.assertFalse(result["completed"])
        self.assertFalse(any(item is state for item in self.ctx.owner_pool.snapshot()))
        self.assertEqual([call.args[1] for call in helper._operation.call_args_list].count("submit_original_decline_once"), 1)

    async def test_decline_raw_error_flags_and_unknown_owner_skip_after_cleanup(self):
        for flag in (True, False, None):
            with self.subTest(flag=flag):
                self.setUp()
                helper, q, state, receipt, _, after = self.decline_fixture(accepted=flag, error=5, outcome="unknown")
                result = await self.api.decline(PROPOSAL)
                self.assertIs(result["accepted"], flag)
                self.assertIs(result["native_result"], receipt)
                self.assertEqual(result["native_error"], 5)
                self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
                self.assertIn(self.key, self.api._responses)
                after.assert_not_awaited()
                q.cleanup_completed.assert_not_awaited()

    async def test_decline_validation_rejection_keeps_false_but_no_submit_flag(self):
        helper, q, state, _, validation, after = self.decline_fixture(valid=False)
        result = await self.api.decline(PROPOSAL)
        self.assertIs(result["accepted"], False)
        self.assertIsNone(result["native_submit_accepted"])
        self.assertIsNone(result["native_result"])
        self.assertIs(result["validation"], validation)
        self.assertEqual(result["reason"], "decline rejected")
        self.assertNotIn("submit_original_decline_once", [call.args[1] for call in helper._operation.call_args_list])
        self.assertNotIn(self.key, self.api._responses)
        after.assert_not_awaited()

    async def test_decline_cleanup_false_retains_actual_primary_effect_and_cache(self):
        helper, q, state, receipt, _, after = self.decline_fixture(cleanup_result=False)
        result = await self.api.decline(PROPOSAL)
        self.assertIs(result["accepted"], True)
        self.assertTrue(result["completed"])
        self.assertIs(result["native_result"], receipt)
        self.assertIs(result["cleanup"]["result"], False)
        self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
        self.assertIn(self.key, self.api._responses)

    async def test_decline_cleanup_exception_keeps_original_error_and_false_submit(self):
        error = RuntimeError("Any cleanup failed")
        helper, q, state, receipt, _, after = self.decline_fixture(accepted=False, cleanup_error=error)
        result = await self.api.decline(PROPOSAL)
        self.assertIs(result["accepted"], False)
        self.assertIs(result["native_result"], receipt)
        self.assertIs(result["cleanup"]["error"], error)
        self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
        self.assertIn(self.key, self.api._responses)
        after.assert_not_awaited()

    async def test_decline_after_cancel_preserves_same_object_primary_and_actual_graph(self):
        error = asyncio.CancelledError("decline original cancellation")
        helper, q, state, receipt, _, after = self.decline_fixture(after_error=error)
        with self.assertRaises(asyncio.CancelledError) as caught:
            await self.api.decline(PROPOSAL)
        self.assertIs(caught.exception, error)
        self.assertIs(error.observed_result["native_result"], receipt)
        self.assertIs(error.observed_result["accepted"], True)
        self.assertIs(self.api._responses[self.key]["error"], error)
        self.assertIs(error.query_state, state)
        self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))
        q.cleanup_completed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
