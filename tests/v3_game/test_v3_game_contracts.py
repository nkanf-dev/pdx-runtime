"""Offline shape and lifecycle checks for public research typing contracts."""
from __future__ import annotations

import asyncio
from decimal import Decimal
import inspect
import json
from threading import Event
from types import SimpleNamespace, UnionType
import typing
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime.v3_game import PendingOperation, V3Game
from pdx_runtime.v3_game._core import GameContext
from pdx_runtime.v3_game import contracts as c
from pdx_runtime.v3_game.research import ResearchAPI
from tests.v3_game import test_v3_game_research as research_fixtures


def matches(value, contract):
    """Check exact returned keys and values, including unions and nested lists."""
    origin, args = typing.get_origin(contract), typing.get_args(contract)
    if origin in (typing.Union, UnionType):
        return any(matches(value, member) for member in args)
    if origin is typing.Literal:
        return any(type(value) is type(item) and value == item for item in args)
    if typing.is_typeddict(contract):
        if not isinstance(value, dict):
            return False
        fields = typing.get_type_hints(contract)
        return (contract.__required_keys__ <= value.keys() <= fields.keys()
                and all(matches(item, fields[key]) for key, item in value.items()))
    if origin is list:
        return isinstance(value, list) and all(matches(item, args[0]) for item in value)
    if origin is dict:
        return (isinstance(value, dict) and all(matches(key, args[0]) and matches(item, args[1])
                                                for key, item in value.items()))
    if contract in (int, float, str, bool, bytes):
        return type(value) is contract
    return isinstance(value, contract)


class ResearchContractTests(unittest.IsolatedAsyncioTestCase):
    def read_fixture(self):
        originals = {name: getattr(research_fixtures.data, name) for name in
                     ("enumerate_functions", "evaluate", "list_count", "list_element", "release")}
        def assert_restored():
            for name, original in originals.items():
                self.assertIs(getattr(research_fixtures.data, name), original, name)
        # Registered first, so this assertion runs after the fixture's cleanups.
        self.addCleanup(assert_restored)
        fixture = research_fixtures.ResearchReadTests()
        # This borrowed IsolatedAsyncioTestCase has no runner. Register every
        # patch cleanup on the running case before setUp can start any patches.
        fixture.addCleanup = self.addCleanup
        fixture.setUp()
        return fixture

    def action_fixture(self):
        from pdx_runtime.v3_game.research import _queue
        fixture = research_fixtures.ResearchActionTests()
        fixture.setUp()
        fields = {name: value for name, value in fixture.plan.items() if name not in
            {"pending", "buffers", "uncertain_operations", "unadopted_clones", "consumptions", "run"}}
        fixture.plan = _queue.new_plan(fixture.ctx, 0x1000007)
        fixture.plan.update(fields)
        fixture.api._new_plan = AsyncMock(return_value=(fixture.helper, fixture.plan))
        return fixture

    def assertShape(self, value, contract):
        self.assertTrue(matches(value, contract), (value, contract))

    async def test_current_present_missing_name_and_absent_have_distinct_shapes(self):
        fixture = self.read_fixture()
        current = await fixture.api.current()
        self.assertShape(current, c.ResearchCurrentTechnology)
        self.assertIsInstance(current["progress"], Decimal)
        with patch.object(fixture.api, "_current_name", AsyncMock(return_value=(None, "native name unavailable"))):
            unnamed = await fixture.api.current()
        self.assertShape(unnamed, c.ResearchCurrentTechnology)
        self.assertIsNone(unnamed["name"])
        fixture.ctx.current_helper.read_current.return_value = {
            "current_known": True, "has_current_research": False,
            "key_known": False, "key": None,
        }
        absent = await fixture.api.current()
        self.assertShape(absent, c.ResearchNoCurrent)
        self.assertNotIn("is_current", absent)
        self.assertNotIn("name_reason", absent)
        self.assertFalse(matches({**absent, "progress": Decimal(0)}, c.ResearchNoCurrent))

    async def test_options_keep_decimal_and_unknown_eligibility(self):
        fixture = self.read_fixture()
        rows = await fixture.api.options()
        self.assertShape(rows, list[c.ResearchOption])
        with patch.object(fixture.api, "_eligibility", AsyncMock(return_value=(None, None, "unknown native eligibility"))):
            rows = await fixture.api.options("production")
        self.assertShape(rows, list[c.ResearchOption])
        self.assertIsNone(rows[0]["can_start"])
        self.assertIsNone(rows[0]["researchable"])
        self.assertFalse(matches({**rows[0], "progress": 50.0}, c.ResearchOption))

    async def test_definition_details_optional_name_reason_is_not_nullable_required_field(self):
        fixture = self.read_fixture()
        details = await fixture.api.details(fixture.add_completed_definition())
        self.assertShape(details, c.ResearchDetails)
        self.assertNotIn("name_reason", details)
        functions = {name: SimpleNamespace(name=name, argument_count=1) for name in
                     ("GetProgress", "GetCost", "HasResearchedTech", "IsResearching")}
        with patch.object(fixture.api, "_functions", AsyncMock(return_value=functions)):
            details = await fixture.api.details("line_infantry")
        self.assertShape(details, c.ResearchDetails)
        self.assertIsNone(details["name"])
        self.assertIn("name_reason", details)
        self.assertFalse(matches({**details, "name_reason": None}, c.ResearchDetails))

    async def test_queue_preserves_duplicate_keys_and_one_based_positions(self):
        fixture = self.read_fixture()
        observation = {"entries": ({"key": "b"}, {"key": "a"}, {"key": "b"})}
        with patch.object(fixture.helper, "observe_queue", AsyncMock(return_value=observation)):
            rows = await fixture.api.queue()
        self.assertShape(rows, list[c.ResearchQueueEntry])
        self.assertEqual(rows, [{"key": "b", "position": 1}, {"key": "a", "position": 2}, {"key": "b", "position": 3}])

    async def test_submitted_statuses_keep_acceptance_separate_from_observation(self):
        fixture = self.action_fixture()
        for status in ("current", "queued", "accepted_not_observed", "removed"):
            with self.subTest(status=status):
                fixture.plan["target_is_current_after"] = status == "current"
                fixture.plan["target_selection_changed"] = status == "current"
                fixture.helper.observe_result.return_value = {
                    "keys": ("atmospheric_engine",) if status == "queued" else (),
                    "entries": ({"key": "atmospheric_engine"},) if status == "queued" else (),
                }
                if status == "removed":
                    fixture.api._definitions = AsyncMock()
                    fixture.helper.observe_queue = AsyncMock(return_value={
                        "top": {"entries": ({"key": "atmospheric_engine"},)},
                        "bottom": {"entries": ()},
                    })
                    result = await fixture.api.dequeue("atmospheric_engine")
                else:
                    result = await fixture.api.start("atmospheric_engine")
                self.assertShape(result, c.ResearchSubmittedChange)
                self.assertEqual(result["status"], status)
                self.assertIs(result["accepted"], True)
                self.assertIs(result["is_current"], status == "current")
                self.assertIs(result["atomic_snapshot"], False)
                self.assertFalse(matches({**result, "status": "succeeded"}, c.ResearchSubmittedChange))

    async def test_already_current_did_not_submit_and_has_no_acceptance_receipt(self):
        fixture = self.action_fixture()
        fixture.api._identity.return_value = {"key": "atmospheric_engine", "has_current_research": True}
        result = await fixture.api.start("atmospheric_engine")
        self.assertShape(result, c.ResearchAlreadyCurrent)
        self.assertIsNone(result["accepted"])
        self.assertNotIn("queue", result)
        fixture.helper.submit_prepared.assert_not_awaited()
        self.assertFalse(matches({**result, "accepted": True}, c.ResearchAlreadyCurrent))

    async def test_known_receiver_rejection_returns_exact_native_result_not_a_success_shape(self):
        fixture = self.action_fixture()
        native = research_fixtures.submit_result(accepted=0)
        fixture.helper.submit_prepared.return_value = native
        result = await fixture.api.enqueue("atmospheric_engine")
        self.assertShape(result, c.ResearchNativeResult)
        self.assertShape(result, c.ResearchChange)
        self.assertIs(result["native_result"], native)
        self.assertEqual(result["native_error"], 0)
        self.assertEqual(result["native_outcome"], "consumed")
        self.assertIs(result["accepted"], False)
        self.assertIs(result["submitted"], True)
        self.assertIsNone(result["queue"])
        self.assertIn("accepted=0", result["reason"])
        fixture.helper.observe_result.assert_not_awaited()
        self.assertFalse(matches(result, c.ResearchSubmittedChange))

    async def test_native_error_or_unknown_preserves_raw_result_before_data_and_none_post_state(self):
        for error, outcome in ((574, "unknown"), (0, "unknown"), (87, "owned")):
            with self.subTest(error=error, outcome=outcome):
                fixture = self.action_fixture()
                native = research_fixtures.submit_result(error=error, accepted=0)
                fixture.helper.submit_prepared.return_value = native
                fixture.plan["owner"].attempt.outcome = outcome
                original_query = object()
                attempt = SimpleNamespace(outcome=outcome, query=original_query, result=native)
                fixture.plan["owner"].attempt = attempt
                fixture.plan["owner"].pending = original_query if outcome == "unknown" else None
                before = fixture.plan["before"]
                submit_before = {"keys": ("existing",), "native_before": object()}
                fixture.plan["submit_before"] = submit_before
                result = await fixture.api.enqueue("atmospheric_engine")
                self.assertShape(result, c.ResearchNativeResult)
                self.assertShape(result, c.ResearchChange)
                self.assertIs(result["native_result"], native)
                self.assertIs(result["before"], before)
                self.assertIs(result["submit_before"], submit_before)
                if outcome == "unknown":
                    self.assertIs(result["pending"]["query"], original_query)
                    self.assertIs(result["pending"]["attempt"], attempt)
                    self.assertIs(result["pending"]["result"], native)
                else:
                    self.assertIsNone(result["pending"])
                self.assertEqual((result["native_error"], result["native_outcome"]), (error, outcome))
                self.assertIsNone(result["accepted"])
                self.assertIsNone(result["submitted"])
                for name in ("is_current", "queue", "queue_changed", "target_selection_changed"):
                    self.assertIsNone(result[name], name)
                fixture.helper.observe_result.assert_not_awaited()
                fixture.helper.submit_prepared.assert_awaited_once()
                self.assertFalse(matches(result, c.ResearchSubmittedChange))
                self.assertFalse(matches({**result, "status": "queued"}, c.ResearchNativeResult))
                self.assertFalse(matches({**result, "queue": []}, c.ResearchNativeResult))
                self.assertFalse(matches({**result, "is_current": False}, c.ResearchNativeResult))
                self.assertFalse(matches({**result, "native_error": False}, c.ResearchNativeResult))
                self.assertFalse(matches({**result, "native_result": object()}, c.ResearchNativeResult))

    async def test_accepted_native_result_survives_post_state_read_failure_without_repeat(self):
        fixture = self.action_fixture()
        native = fixture.helper.submit_prepared.return_value
        error = LookupError("native post-state source unavailable")
        fixture.helper.observe_result.side_effect = error
        result = await fixture.api.enqueue("atmospheric_engine")
        self.assertShape(result, c.ResearchNativeResult)
        self.assertIs(result["native_result"], native)
        self.assertIs(result["accepted"], True)
        self.assertIsNone(result["queue"])
        self.assertIs(result["observation_error"], error)
        self.assertIn("post-state", result["reason"])
        fixture.helper.submit_prepared.assert_awaited_once()
        fixture.helper.observe_result.assert_awaited_once()
        self.assertFalse(matches(result, c.ResearchSubmittedChange))

    async def test_cleanup_error_is_supplementary_to_completed_native_result(self):
        fixture = self.action_fixture()
        native = research_fixtures.submit_result(error=574, accepted=0)
        fixture.helper.submit_prepared.return_value = native
        fixture.plan["owner"].attempt.outcome = "unknown"
        cleanup_error = LookupError("cleanup retained original request")
        # The earlier scan cleanup completes; only the action-plan cleanup fails.
        fixture.api._cleanup.side_effect = [None, cleanup_error]
        result = await fixture.api.enqueue("atmospheric_engine")
        self.assertShape(result, c.ResearchNativeResult)
        self.assertIs(result["native_result"], native)
        self.assertEqual(result["cleanup"]["status"], "failed")
        self.assertIs(result["cleanup"]["errors"][0], cleanup_error)
        fixture.helper.submit_prepared.assert_awaited_once()
        fixture.helper.observe_result.assert_not_awaited()

    async def test_unfinished_submit_keeps_same_operation_and_returns_no_completed_variant(self):
        fixture = self.action_fixture()
        ready, started = asyncio.Event(), asyncio.Event()
        native = research_fixtures.submit_result(error=574)
        calls = []
        async def pending_submit(plan, **options):
            calls.append(plan)
            started.set()
            await ready.wait()
            return native
        fixture.helper.submit_prepared.side_effect = pending_submit
        fixture.plan["owner"].attempt.outcome = "unknown"
        task = asyncio.create_task(fixture.api.enqueue("atmospheric_engine"))
        try:
            await asyncio.wait_for(started.wait(), 1)
            self.assertFalse(task.done())
            self.assertEqual(calls, [fixture.plan])
            fixture.helper.observe_result.assert_not_awaited()
        finally:
            ready.set()
            result = await task
        self.assertShape(result, c.ResearchNativeResult)
        self.assertIs(result["native_result"], native)
        self.assertEqual(calls, [fixture.plan])
        fixture.helper.submit_prepared.assert_awaited_once()


class PendingContractTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return GameContext(SimpleNamespace(native=object(), namespace={}), object(),
                           timeout=.001, poll_interval=.001)

    async def test_research_timeout_resume_and_direct_await_keep_original_task(self):
        ctx, ready, submissions = self.context(), Event(), []
        loop, started = asyncio.get_running_loop(), asyncio.Event()
        result = {"key": "b", "position": 1}
        def read():
            if not ready.is_set():
                loop.call_soon_threadsafe(started.set)
                return None
            return [result]
        query = SimpleNamespace(read=read)
        api = ResearchAPI(ctx)
        async def read_queue():
            def submit():
                submissions.append(query)
                return query
            return await ctx.query("research.queue.read", submit)
        api._queue = read_queue
        pending = await api.queue()
        try:
            self.assertIsInstance(pending, PendingOperation)
            await asyncio.wait_for(started.wait(), 1)
            self.assertIsInstance(pending, c.PendingResult)
            self.assertFalse(pending.done)
            self.assertEqual(pending.pending_queries, (query,))
            task = pending.task
            self.assertIs(await pending.resume(timeout=.001), pending)
            self.assertIs(await pending, pending)
            self.assertIs(pending.task, task)
            ready.set()
            final = await pending.resume(timeout=1)
        finally:
            ready.set()
            if isinstance(pending, PendingOperation):
                await pending.task
        self.assertTrue(matches(final, list[c.ResearchQueueEntry]))
        self.assertIs(final[0], result)
        self.assertTrue(pending.done)
        self.assertEqual(pending.pending_queries, ())
        self.assertIs(await pending, final)
        self.assertEqual(submissions, [query])
        self.assertEqual(json.loads(json.dumps(pending)), {"status": "pending", "operation": "research.queue"})
        self.assertFalse(isinstance(json.loads(json.dumps(pending)), c.PendingResult))

    async def test_done_also_includes_failure_and_resume_reraises_it(self):
        ctx, ready, calls = self.context(), asyncio.Event(), []
        error = ValueError("native read failed")
        async def fail():
            calls.append(1)
            await ready.wait()
            raise error
        pending = PendingOperation(ctx, "research.details", asyncio.create_task(fail()))
        ready.set()
        for _ in range(2):
            with self.assertRaises(ValueError) as caught:
                await pending.resume(timeout=1)
            self.assertIs(caught.exception, error)
        self.assertTrue(pending.done)
        self.assertEqual(dict(pending), {"status": "pending", "operation": "research.details"})
        self.assertEqual(calls, [1])

    async def test_cancelling_a_waiter_does_not_cancel_the_owned_operation(self):
        ctx, ready = self.context(), asyncio.Event()
        owned_started = asyncio.Event()
        async def finish():
            owned_started.set()
            await ready.wait()
            return []
        pending = await ctx.perform("research.queue", finish)
        started = asyncio.Event()
        async def wait():
            started.set()
            return await pending.resume(timeout=1)
        waiter = None
        try:
            self.assertIsInstance(pending, PendingOperation)
            await asyncio.wait_for(owned_started.wait(), 1)
            waiter = asyncio.create_task(wait())
            await asyncio.wait_for(started.wait(), 1)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertFalse(pending.task.cancelled())
            ready.set()
            self.assertEqual(await pending.resume(timeout=1), [])
        finally:
            ready.set()
            if isinstance(pending, PendingOperation):
                await pending.task
            if waiter is not None and not waiter.done():
                await waiter


class ContractAnnotationTests(unittest.TestCase):

    def test_all_seven_public_research_methods_expose_final_or_pending_types(self):
        returns = {
            "current": c.OperationResult[c.ResearchCurrent],
            "options": c.OperationResult[list[c.ResearchOption]],
            "details": c.OperationResult[c.ResearchDetails],
            "queue": c.OperationResult[list[c.ResearchQueueEntry]],
            "start": c.OperationResult[c.ResearchChange],
            "enqueue": c.OperationResult[c.ResearchSubmittedChange | c.ResearchNativeResult],
            "dequeue": c.OperationResult[c.ResearchSubmittedChange | c.ResearchNativeResult],
        }
        for name, expected in returns.items():
            method = getattr(ResearchAPI, name)
            self.assertTrue(inspect.iscoroutinefunction(method))
            self.assertEqual(typing.get_type_hints(method)["return"], expected)
        self.assertEqual(typing.get_type_hints(ResearchAPI.options)["category"], c.ResearchCategory | None)
        for name in ("details", "start", "enqueue", "dequeue"):
            self.assertIs(typing.get_type_hints(getattr(ResearchAPI, name))["key"], str)

    def test_pending_protocol_has_only_actual_readable_lifecycle_members(self):
        hints = typing.get_type_hints(c.PendingResult.resume)
        self.assertEqual(hints["return"], c.OperationResult[c._T_co])
        self.assertEqual(hints["timeout"], float | None)
        self.assertEqual(c.ResearchDetails.__optional_keys__, {"name_reason"})
        self.assertNotIn("accepted", c.ResearchCurrentTechnology.__annotations__)


if __name__ == "__main__":
    unittest.main()
