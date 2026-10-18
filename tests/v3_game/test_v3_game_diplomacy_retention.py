"""CPU retention checks using the real GameContext, Diagnostics and owner pool."""
from __future__ import annotations

from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pdx_runtime.query_executor import Diagnostics, RetainedOwners
from pdx_runtime.v3_game._core import GameContext
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI, PreparedTreaty
from pdx_runtime.v3_game.diplomacy import _treaty_proposals as backend


WORLD, ACTOR, TARGET = 0x50000, 0, 0x02000004


def released_query(*, released=True, result=None):
    return NS(_released=released, _result=result, read=Mock(), _release=Mock())


def consumed_owner(query):
    return NS(command=0, pending=None, attempt=NS(outcome="consumed", query=query),
        destroy=Mock(), submit=Mock())


class DiplomacyRetentionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = NS(native=NS(pid=123), namespace={})
        self.ctx = GameContext(self.session, process=NS(pid=123), country_id=ACTOR)
        self.ctx.world, self.ctx.image_base = WORLD, 0x140000000
        self.api = DiplomacyAPI(self.ctx)
        self.assertIsInstance(self.ctx.diagnostics, Diagnostics)
        self.assertIsInstance(self.ctx.owner_pool, RetainedOwners)
        self.assertIs(self.ctx.owner_pool, self.session.owner_pool)
        self.assertIs(self.ctx.owner_pool, self.ctx._executor.owner_pool)

    def records(self):
        diagnostics = self.ctx.diagnostics
        return diagnostics.get("operations", ()) if isinstance(diagnostics, dict) else diagnostics

    def assertRetained(self, state):
        self.assertTrue(any(item is state for item in self.ctx.owner_pool.snapshot()))

    def assertNotRetained(self, state):
        self.assertFalse(any(item is state for item in self.ctx.owner_pool.snapshot()))

    def test_remember_uses_actual_diagnostic_sink_and_keeps_original_result(self):
        native_result = NS(error=122, value_handle=37, snapshot=b"partial native bytes")
        self.assertIs(self.api._remember("partial_read", native_result), native_result)
        record = self.records()[-1]
        self.assertEqual(record["label"], "partial_read")
        self.assertIs(record["state"], native_result)
        self.assertEqual(self.ctx.owner_pool.snapshot(), ())

    def test_read_and_proposal_states_share_one_identity_pool_without_ledgers(self):
        read, proposal = {"pending": {}}, {"pending": {}}
        self.assertEqual(read, proposal)
        self.assertIs(self.api._retain_state(read, "read", "read"), read)
        self.assertIs(self.api._retain_state(proposal, "proposal", "proposal"), proposal)
        self.api._retain_state(read, "proposal", "same_graph")
        retained = self.ctx.owner_pool.snapshot()
        self.assertEqual(len(retained), 2)
        self.assertIs(retained[0], read)
        self.assertIs(retained[1], proposal)
        self.assertNotIn("_read_states", vars(self.api))
        self.assertNotIn("_proposal_states", vars(self.api))

    def test_bounded_diagnostics_and_clear_do_not_abandon_any_original_graph(self):
        states = []
        for index in range(80):
            query = released_query(released=False)
            state = {"pending": {"query": query}, "owned_buffers": [index + 1]}
            states.append(state)
            self.api._retain_state(state, "read" if index % 2 else "proposal", str(index))
        self.assertEqual(len(self.records()), 64)
        self.assertIs(self.records()[0]["state"], states[16])
        self.ctx.diagnostics.clear()
        self.assertEqual(len(self.records()), 0)
        self.assertEqual(len(self.ctx.owner_pool.snapshot()), 80)
        for state in states:
            self.assertRetained(state)
            self.assertFalse(self.api._retire_state(state))
            state["pending"]["query"].read.assert_not_called()
            state["pending"]["query"]._release.assert_not_called()

    def test_read_state_retains_backend_identity_and_explicit_world_image(self):
        query = released_query(released=False)
        state = {"pending": {"query": query}, "world": WORLD}
        source = NS(state_for=Mock(return_value=state))
        returned = self.api._read_state(source, ACTOR, "original_read", world=17, image=23)
        self.assertIs(returned, state)
        self.assertIs(state["pending"]["query"], query)
        self.assertEqual((state["world"], state["image_base"]), (17, 23))
        source.state_for.assert_called_once_with(self.ctx, ACTOR)
        self.assertRetained(state)

    def test_known_complete_graph_discards_only_exact_state_and_mature_entries(self):
        query = released_query(result=NS(error=0))
        state = {"pending": None, "owned_values": [], "owned_buffers": [],
            "queries": [query], "last_command_query": query,
            "unique_owner": consumed_owner(query), "staging_owner": consumed_owner(query),
            "clone_owner": consumed_owner(query)}
        equal_but_distinct = dict(state)
        unrelated = {"pending": {"query": released_query(released=False)}}
        for item in (state, equal_but_distinct, unrelated):
            self.api._retain_state(item, "proposal", "graph")
        proposals = [state, equal_but_distinct, state, unrelated]
        accepts = [equal_but_distinct, state, unrelated]
        with patch.object(backend.mature, "ATTEMPTS", proposals), \
             patch.object(backend.mature.accept, "ATTEMPTS", accepts):
            self.ctx.diagnostics.clear()
            self.assertTrue(self.api._retire_state(state))
            self.assertEqual(len(proposals), 2)
            self.assertIs(proposals[0], equal_but_distinct)
            self.assertIs(proposals[1], unrelated)
            self.assertEqual(len(accepts), 2)
            self.assertIs(accepts[0], equal_but_distinct)
            self.assertIs(accepts[1], unrelated)
        self.assertNotRetained(state)
        self.assertRetained(equal_but_distinct)
        self.assertRetained(unrelated)
        query.read.assert_not_called()
        query._release.assert_not_called()
        for key in ("unique_owner", "staging_owner", "clone_owner"):
            state[key].destroy.assert_not_called()
            state[key].submit.assert_not_called()

    def test_uncertain_or_owned_edges_prevent_retirement_and_mature_removal(self):
        cases = [
            {"pending": {}}, {"owned_buffers": [1]}, {"owned_values": [2]},
            {"unadopted_clone": object()}, {"unsettled_clone": object()},
            {"handoff_uncertain": True}, {"cleanup_uncertain": True},
            {"queries": [released_query(released=False)]},
            {"last_command_query": released_query(released=False)},
            {"queries": [object()]},
        ]
        for name in ("unique_owner", "staging_owner", "owner", "clone_owner"):
            cases.extend([
                {name: NS(command=1, pending=None, attempt=None)},
                {name: NS(command=0, pending=object(), attempt=None)},
                {name: NS(command=0, pending=None, attempt=NS(outcome="unknown", query=None))},
                {name: NS(command=0, pending=None, attempt=NS(outcome="pending", query=None))},
                {name: consumed_owner(released_query(released=False))},
            ])
        for state in cases:
            with self.subTest(state=state):
                self.api._retain_state(state, "proposal", "unsettled")
                attempts = [state]
                with patch.object(backend.mature, "ATTEMPTS", attempts), \
                     patch.object(backend.mature.accept, "ATTEMPTS", []):
                    self.assertFalse(self.api._retire_state(state))
                    self.assertEqual(len(attempts), 1)
                    self.assertIs(attempts[0], state)
                self.assertRetained(state)

    def test_cached_result_is_not_release_proof_and_retry_does_no_native_io(self):
        native_result = NS(error=0)
        query = released_query(released=False, result=native_result)
        state = {"pending": None, "last_command_query": query}
        self.api._retain_state(state, "read", "cached")
        self.ctx.diagnostics.clear()
        self.assertFalse(self.api._retire_state(state))
        self.assertRetained(state)
        self.assertIs(query._result, native_result)
        query._released = True
        self.assertTrue(self.api._retire_state(state))
        self.assertNotRetained(state)
        query.read.assert_not_called()
        query._release.assert_not_called()

    async def test_cleanup_success_retires_only_after_known_full_release(self):
        query = released_query(released=False, result=NS(error=0))
        state = {"pending": None, "owned_buffers": [11], "owned_values": [37],
            "last_command_query": query}
        self.api._retain_state(state, "proposal", "owned")
        async def cleanup(original):
            self.assertIs(original, state)
            original["owned_buffers"].clear()
            original["owned_values"].clear()
            query._released = True
            return True
        source = NS(cleanup=AsyncMock(side_effect=cleanup))
        self.assertTrue(await self.api._cleanup_proposal_state(source, state, "cleanup"))
        self.assertNotRetained(state)
        source.cleanup.assert_awaited_once_with(state)
        query.read.assert_not_called()
        query._release.assert_not_called()

    async def test_success_claim_does_not_drop_original_pending_query_or_owner(self):
        query = released_query(released=False)
        owner = NS(command=123, pending=query, attempt=NS(outcome="unknown", query=query),
            destroy=Mock(), submit=Mock())
        pending = {"query": query, "owner": owner}
        state = {"pending": pending, "unique_owner": owner, "owned_buffers": [11]}
        source = NS(cleanup=AsyncMock(return_value=True))
        self.assertFalse(await self.api._cleanup_proposal_state(source, state, "pending"))
        self.ctx.diagnostics.clear()
        self.assertRetained(state)
        self.assertIs(state["pending"], pending)
        self.assertIs(state["pending"]["query"], query)
        self.assertIs(state["unique_owner"], owner)
        self.assertEqual(owner.command, 123)
        source.cleanup.assert_awaited_once_with(state)
        query.read.assert_not_called()
        query._release.assert_not_called()
        owner.destroy.assert_not_called()
        owner.submit.assert_not_called()

    async def test_cleanup_false_keeps_partial_result_and_warning_originals(self):
        partial = NS(error=122, value_handle=37, snapshot=b"native partial")
        query = released_query(released=False, result=partial)
        warning = RuntimeError("original query storage release warning")
        state = {"pending": None, "last_error_result": partial, "last_command_query": query,
            "owned_values": [37], "cleanup_failure": warning}
        source = NS(cleanup=AsyncMock(return_value=False))
        self.assertFalse(await self.api._cleanup_proposal_state(source, state, "warning"))
        self.assertIs(self.records()[-1]["state"], state)
        self.ctx.diagnostics.clear()
        self.assertRetained(state)
        self.assertIs(state["last_error_result"], partial)
        self.assertIs(state["cleanup_failure"], warning)
        self.assertIs(state["last_command_query"], query)
        self.assertEqual(state["owned_values"], [37])
        self.assertFalse(self.api._retire_state(state))

    async def test_cleanup_exception_is_retained_without_masking_native_preview(self):
        receipt = NS(error=0, validated=True, reason="")
        warning = RuntimeError("cleanup storage remains owned")
        state = {"world": WORLD, "validation": receipt, "binding_days": 3650,
            "binding_days_source": "native_game_default", "cost_source": {"value": 10000000},
            "pending": None, "owned_buffers": [11], "owned_values": []}
        async def prepare(ctx, *args, on_state, **kwargs):
            self.assertIs(ctx, self.ctx)
            on_state(state)
            return state
        source = NS(prepare=AsyncMock(side_effect=prepare), cleanup=AsyncMock(side_effect=warning))
        with patch.object(self.ctx, "refresh", AsyncMock()), \
             patch.object(self.api, "_resolve", AsyncMock(return_value=TARGET)), \
             patch.object(self.api, "_treaty_backend", Mock(return_value=source)):
            terms = await self.api._treaty_prepare(TARGET, ["defensive_pact"], None)
        self.assertIsInstance(terms, PreparedTreaty)
        self.assertTrue(terms["can_propose"])
        self.assertFalse(terms["submitted"])
        self.assertEqual(terms["target_id"], TARGET)
        self.assertIs(state["validation"], receipt)
        self.assertIs(state["cleanup_failure"], warning)
        self.assertEqual(state["owned_buffers"], [11])
        self.assertIs(self.records()[-1]["state"], state)
        self.ctx.diagnostics.clear()
        self.assertRetained(state)
        source.prepare.assert_awaited_once()
        source.cleanup.assert_awaited_once_with(state)

    async def test_cleanup_warning_does_not_replace_original_main_failure(self):
        original = RuntimeError("native prepare failed with original state")
        warning = RuntimeError("cleanup also failed")
        partial = NS(error=5, value_handle=37)
        query = released_query(released=False, result=partial)
        state = {"pending": {"query": query}, "owned_values": [37], "last_error_result": partial}
        async def prepare(ctx, *args, on_state, **kwargs):
            on_state(state)
            raise original
        source = NS(prepare=AsyncMock(side_effect=prepare), cleanup=AsyncMock(side_effect=warning))
        with patch.object(self.ctx, "refresh", AsyncMock()), \
             patch.object(self.api, "_resolve", AsyncMock(return_value=TARGET)), \
             patch.object(self.api, "_treaty_backend", Mock(return_value=source)):
            with self.assertRaises(RuntimeError) as caught:
                await self.api._treaty_prepare(TARGET, ["defensive_pact"], None)
        self.assertIs(caught.exception, original)
        self.assertIs(state["cleanup_failure"], warning)
        self.assertIs(state["last_error_result"], partial)
        self.ctx.diagnostics.clear()
        self.assertRetained(state)
        self.assertIs(state["pending"]["query"], query)
        query.read.assert_not_called()
        query._release.assert_not_called()



if __name__ == "__main__":
    unittest.main()
