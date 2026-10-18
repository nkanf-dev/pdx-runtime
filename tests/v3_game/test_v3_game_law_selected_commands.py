"""CPU-only selected enact/stop commands; no serialization/actor receipt gates.

The real original-law selection, _Run generated_owner/validate_submit, command
clone/adoption, executor and cleanup paths execute on native-shaped fixtures.
Native actor IDs below belong to the fixture's original commands, not SDK reads.
No process/DLL/game/native gameplay effect is invoked.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.query_executor import PendingOperation
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from tests.v3_game import test_v3_game_politics as legacy


class ControlledQuery(legacy.Query):
    def __init__(self, result, fixture):
        super().__init__(result)
        self.ready = False
        self.partial_result = result
        fixture.delayed.append(self)
        self.fixture = fixture
    def read(self):
        self.fixture.loop.call_soon_threadsafe(self.fixture.started.set)
        return super().read() if self.ready else None


class LawCommand(legacy.Command):
    def __init__(self, fixture, token, params, command):
        super().__init__(fixture, token, params, command)
        self.world = fixture.command_world
        self.native_actor_id = fixture.native_actor_id
        self.adapter = fixture.command_adapter
    def enqueue(self, action, result, delay=0):
        assert self.pending is None
        self.operation = action
        self.pending = (ControlledQuery(result, self.f) if self.f.pending_stage == action
                        else legacy.Query(result, delay))
        return self.pending
    def complete(self):
        value = self.pending.read()
        if value is not None:
            self.pending = None
            if self.operation == "submit":
                self.command = value.command
                self.attempt = NS(outcome=self.f.submit_outcome)
            elif self.operation == "destroy":
                self.command, self.attempt = 0, NS(outcome="destroyed")
            else:
                self.attempt = NS(outcome="owned")
        return value
    def serialize(self): raise AssertionError("selected law command must not serialize")
    def validate(self, capacity):
        self.f.validations.append(self)
        self.attempt = NS(outcome="owned")
        result = NS(error=self.f.validation_error, action=commands.Action.VALIDATE,
                    validated=self.f.validation_value, reason=self.f.validation_reason,
                    reason_truncated=False)
        self.f.validation_results.append(result)
        return self.enqueue("validate", result)
    def submit(self, flags):
        assert flags == 4
        self.f.submitted.append((self.type_token, self.params))
        self.f.current_operation_submitted = True
        result = NS(error=self.f.submission_error, action=commands.Action.SUBMIT,
                    command=self.f.submission_command, accepted=self.f.submission_accepted)
        self.f.submission_results.append(result)
        if result.accepted:
            self.f.enacting = self.params[11018].value if self.type_token == 22275 else None
        return self.enqueue("submit", result)


class Fixture(legacy.Fixture):
    def __init__(self):
        super().__init__()
        self.native_actor_id = 0x99000009
        self.command_world = self.world + 0x100
        self.command_adapter, self.data_adapter = "original-command-module", 0xA000
        self.validations, self.validation_results, self.submission_results = [], [], []
        self.validation_error, self.validation_value, self.validation_reason = 0, True, ""
        self.submission_error, self.submission_accepted, self.submission_command = 0, True, 0
        self.submit_outcome = "consumed"
        self.clones, self.producer_bindings, self.release_calls = [], [], []
        self.pre_observation = None
        self.current_operation_submitted = False
        self.readback_failure, self.readback_native_error = None, 0
        self.producer_error, self.clone_error, self.clone_nonzero = 0, 0, False
        self.mutate_adapters_after_clone = False
        self.pending_stage, self.delayed, self.started, self.loop = None, [], None, None
    def producer(self, token, params):
        query = super().producer(token, params)
        value = query.result.value
        carrier = data.NativeDataCommand(value.primary_pointer, value.shared_control,
            value.game_command_pointer, value.value_handle, value.native_type_name, self.command_world)
        query.result.value = carrier
        self.handles[query.result.value_handle] = carrier
        query.result.error = self.producer_error
        return query
    def created(self, token, params, command=0xD0000):
        owner = LawCommand(self, token, params, command)
        self.owners.append(owner)
        return legacy.Query(NS(error=0, owner=owner, action=commands.Action.DESERIALIZE))
    def evaluate(self, native, expression, bindings, **kwargs):
        if expression in ("Law.StartEnacting", "Country.GetLawBeingEnacted.StopEnacting"):
            self.producer_bindings.append((expression, tuple(bindings)))
        if expression == "Country.IsEnactingLaw":
            if self.current_operation_submitted and self.readback_failure is not None:
                raise self.readback_failure
            if not self.current_operation_submitted and self.pre_observation is not None:
                self.events.append(expression)
                return legacy.Query(self.pre_observation)
            query = super().evaluate(native, expression, bindings, **kwargs)
            if self.current_operation_submitted:
                query.result.error = self.readback_native_error
                if self.pending_stage == "readback": return ControlledQuery(query.result, self)
            return query
        return super().evaluate(native, expression, bindings, **kwargs)
    def request(self, native, action, command, world, **kwargs):
        self.clones.append((action, command, world, kwargs.get("adapter")))
        assert action == commands.Action.CLONE
        assert world == self.command_world
        query = super().request(native, action, command, world, **kwargs)
        query.result.error = self.clone_error
        if self.clone_error and not self.clone_nonzero: query.result.cloned_command = 0
        if self.mutate_adapters_after_clone:
            self.ctx.adapters.update(data="later-other-data", commands="later-other-command")
        return query
    def release(self, native, handle, **kwargs):
        self.release_calls.append((handle, kwargs.get("adapter")))
        return super().release(native, handle, **kwargs)


class LawSelectedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fx = Fixture()
        self.ctx = legacy.Context(self.fx)
        self.ctx.adapters.update(data=self.fx.data_adapter, commands=self.fx.command_adapter)
        self.api = politics.PoliticsAPI(self.ctx)
        self.patchers = self.fx.patches()
        for patcher in self.patchers: patcher.start()
    async def asyncTearDown(self):
        for patcher in reversed(self.patchers): patcher.stop()
    def cleaned(self):
        self.assertEqual(self.fx.handles, {})
        self.assertEqual(self.fx.memory.blocks, {})
        self.assertTrue(all(not owner.command for owner in self.fx.owners))
        self.assertEqual(self.api._retained, [])
    async def invoke(self, action):
        self.fx.current_operation_submitted = False
        if action == "enact": return await self.api.enact("law_workers_protections")
        return await self.api.stop_enactment()
    def run_for(self, action):
        return next(run for run in reversed(self.ctx.owner_pool.snapshot())
                    if isinstance(run, run_mod.Run) and run.label == action)

    async def test_enact_original_full_id_key_source_one_clone_validate_submit_without_serialize(self):
        result = await self.api.enact(self.fx.law_ids[1])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertIsNone(result["completed"])
        self.assertEqual(result["law"]["id"], self.fx.law_ids[1])
        self.assertEqual(result["law"]["key"], "law_workers_protections")
        self.assertEqual(result["current"]["id"], self.fx.law_ids[1])
        self.assertEqual(result["source_country_id"], self.fx.actor)
        self.assertIsNone(result["native_actor_id"])
        self.assertIn("not a native actor receipt", result["actor_scope"])
        self.assertIn("not command actor", result["readback_scope"])
        self.assertEqual(self.fx.events.count("Law.StartEnacting"), 1)
        self.assertEqual(len(self.fx.clones), 1)
        self.assertEqual(self.fx.clones[0][2], self.fx.command_world)
        self.assertNotEqual(self.fx.clones[0][2], self.ctx.world)
        self.assertEqual(len(self.fx.validations), 1)
        self.assertEqual(len(self.fx.submitted), 1)
        owner, = self.fx.validations
        self.assertEqual(owner.native_actor_id, self.fx.native_actor_id)
        self.assertNotEqual(owner.native_actor_id, result["source_country_id"])
        expression, bindings = self.fx.producer_bindings[0]
        self.assertEqual(expression, "Law.StartEnacting")
        self.assertEqual(len(bindings), 1)
        self.assertTrue(bindings[0].retained)
        self.assertEqual(self.fx.bound[bindings[0].object], ("Law", self.fx.law_ids[1]))
        self.assertIs(result["validation"], self.fx.validation_results[0])
        self.assertIs(result["submission"], self.fx.submission_results[0])
        self.assertIs(result["native_result"], result["submission"])
        self.assertIsNotNone(result["native_results"])
        self.assertNotIn("Country.GetLawBeingEnacted.StopEnacting", self.fx.events)
        self.cleaned()

    async def test_stop_original_current_chain_one_clone_validate_submit_without_target_receipt(self):
        self.fx.enacting = self.fx.law_ids[1]
        result = await self.api.stop_enactment()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertIsNone(result["completed"])
        self.assertTrue(result["enacting_observed"])
        self.assertFalse(result["enacting_after_observed"])
        self.assertIs(result["submission"], self.fx.submission_results[0])
        self.assertEqual(len(self.fx.clones), 1)
        self.assertEqual(len(self.fx.validations), 1)
        self.assertEqual(len(self.fx.submitted), 1)
        expression, bindings = self.fx.producer_bindings[0]
        self.assertEqual(expression, "Country.GetLawBeingEnacted.StopEnacting")
        self.assertEqual(bindings[0].type_id, await self.ctx.type_id("Country"))
        self.assertEqual(bindings[0].object, self.fx.actor)
        self.assertTrue(bindings[0].country_id)
        self.assertFalse(bindings[0].retained)
        self.assertNotIn("law_id", result)
        self.assertIsNone(result["native_actor_id"])
        self.cleaned()

    async def test_native_validator_rejection_for_each_selected_action_never_submits(self):
        for action in ("enact", "stop_enactment"):
            with self.subTest(action=action):
                self.fx.enacting = None if action == "enact" else self.fx.law_ids[1]
                self.fx.validation_value, self.fx.validation_reason = False, "original native law rule"
                count = len(self.fx.validations)
                result = await self.invoke(action)
                self.assertFalse(result["accepted"])
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(result["reason"], "original native law rule")
                self.assertIs(result["validation"], self.fx.validation_results[-1])
                self.assertEqual(len(self.fx.validations), count + 1)
                self.assertEqual(self.fx.submitted, [])
                self.cleaned()

    async def test_native_validation_error_preserves_observed_true_without_submit(self):
        self.fx.validation_error = 50
        result = await self.api.enact("law_workers_protections")
        self.assertEqual(result["status"], "native_result")
        self.assertIsNone(result["accepted"])
        self.assertTrue(result["validation"].validated)
        self.assertEqual(result["native_error"], 50)
        self.assertIs(result["native_result"], self.fx.validation_results[0])
        self.assertEqual(self.fx.submitted, [])
        self.cleaned()

    async def test_native_submission_error_and_partial_acceptance_are_not_rewritten(self):
        for action in ("enact", "stop_enactment"):
            for accepted in (False, True):
                with self.subTest(action=action, accepted=accepted):
                    self.fx.enacting = None if action == "enact" else self.fx.law_ids[1]
                    self.fx.submission_error, self.fx.submission_accepted = 50, accepted
                    result = await self.invoke(action)
                    self.assertIs(result["accepted"], True if accepted else None)
                    self.assertEqual(result["status"], "accepted" if accepted else "native_result")
                    self.assertEqual(result["native_error"], 50)
                    self.assertIs(result["native_result"], self.fx.submission_results[-1])
                    self.assertIs(result["submission"], self.fx.submission_results[-1])
                    self.assertIsNone(result["completed"])
                    self.cleaned()

    async def test_original_producer_known_error_keeps_same_result_and_finishes_known_scope(self):
        self.fx.producer_error = 50
        with self.assertRaises(run_mod.KnownResultError) as raised:
            await self.api.enact("law_workers_protections")
        error = raised.exception
        self.assertEqual(error.result.error, 50)
        self.assertIsNone(error.observed_result["accepted"])
        self.assertTrue(any(native is error.result for _, native in error.observed_result["native_results"]))
        self.assertEqual(error.cleanup["status"], "complete")
        self.assertEqual(self.fx.clones, [])
        self.assertEqual(self.fx.submitted, [])
        self.cleaned()

    async def test_nonzero_clone_error_is_preserved_unadopted_and_never_submitted(self):
        self.fx.clone_error, self.fx.clone_nonzero = 50, True
        with self.assertRaises(run_mod.KnownResultError) as raised:
            await self.api.enact("law_workers_protections")
        run = self.run_for("enact")
        self.assertIs(run.unadopted_command, raised.exception.result)
        self.assertTrue(run.unadopted_command.cloned_command)
        self.assertEqual(run.status, "retained_pending_or_unknown")
        self.assertEqual(self.fx.submitted, [])
        self.assertEqual(self.fx.validations, [])
        self.assertEqual(self.fx.released, [])
        self.assertIsNone(raised.exception.observed_result["accepted"])

    async def test_accepted_with_unsettled_owner_keeps_receipt_and_skips_all_readback(self):
        for action in ("enact", "stop_enactment"):
            with self.subTest(action=action):
                self.fx.enacting = None if action == "enact" else self.fx.law_ids[1]
                self.fx.submission_command, self.fx.submit_outcome = 0xD0000, "unknown"
                result = await self.invoke(action)
                self.assertTrue(result["accepted"])
                self.assertIn("unsettled", result["readback_skipped"])
                self.assertIs(result["submission"], self.fx.submission_results[-1])
                self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
                run = self.run_for(action)
                self.assertTrue(run.values)
                self.assertTrue(run.owners[0].command)
                if action == "enact": self.assertIsNone(result["current"])
                else: self.assertIsNone(result["after"])

    async def test_accepted_readback_transport_error_keeps_original_query_and_partial(self):
        for action in ("enact", "stop_enactment"):
            with self.subTest(action=action):
                self.fx.enacting = None if action == "enact" else self.fx.law_ids[1]
                original = legacy.Query(NS(error=122, value_handle=0, value=False, kind=data.Kind.BOOL))
                original.partial_result = original.result
                failure = OSError("original law readback unknown")
                failure.query = original
                failure.partial_result = original.partial_result
                self.fx.readback_failure = failure
                result = await self.invoke(action)
                self.assertTrue(result["accepted"])
                self.assertIs(result["readback_error"], failure)
                self.assertIs(result["native_result"], self.fx.submission_results[-1])
                run = self.run_for(action)
                self.assertIs(run.uncertain_query, original)
                self.assertTrue(any(value is original.partial_result for _, value in run.operations))
                self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
                self.fx.readback_failure = None

    async def test_accepted_stop_readback_native_error_keeps_raw_false_as_unknown(self):
        self.fx.enacting = self.fx.law_ids[1]
        self.fx.readback_native_error = 122
        result = await self.api.stop_enactment()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["after"].error, 122)
        self.assertIs(result["after"].value, False)
        self.assertIsNone(result["enacting_after_observed"])
        self.assertIs(result["native_result"], result["submission"])
        self.cleaned()

    async def test_pending_submit_resumes_same_query_once_for_both_actions(self):
        for action in ("enact", "stop_enactment"):
            with self.subTest(action=action):
                self.fx.enacting = None if action == "enact" else self.fx.law_ids[1]
                self.fx.pending_stage = "submit"
                self.fx.loop, self.fx.started = asyncio.get_running_loop(), asyncio.Event()
                self.ctx.timeout = .01
                before = len(self.fx.submitted)
                task = asyncio.create_task(self.invoke(action))
                await asyncio.wait_for(self.fx.started.wait(), 1)
                pending = await task
                self.assertIsInstance(pending, PendingOperation)
                query = self.fx.delayed[-1]
                self.assertIs(pending.pending_queries[0], query)
                self.assertTrue(self.api._mutation_lock.locked())
                self.assertEqual(len(self.fx.submitted), before + 1)
                query.ready = True
                result = await pending.resume(1)
                self.assertTrue(result["accepted"])
                self.assertIs(result["submission"], query.result)
                self.assertEqual(len(self.fx.submitted), before + 1)
                self.fx.pending_stage = None
                self.assertFalse(self.api._mutation_lock.locked())
                self.cleaned()

    async def test_pending_readback_keeps_accepted_primary_receipt_without_resubmitting(self):
        self.fx.pending_stage = "readback"
        self.fx.loop, self.fx.started = asyncio.get_running_loop(), asyncio.Event()
        self.ctx.timeout = .01
        task = asyncio.create_task(self.api.enact("law_workers_protections"))
        await asyncio.wait_for(self.fx.started.wait(), 1)
        pending = await task
        self.assertIsInstance(pending, PendingOperation)
        run = self.run_for("enact")
        self.assertTrue(run.public_result["accepted"])
        self.assertIs(run.public_result["submission"], self.fx.submission_results[0])
        self.fx.pending_stage = None
        self.fx.delayed[-1].ready = True
        result = await pending.resume(1)
        self.assertTrue(result["accepted"])
        self.assertEqual(len(self.fx.submitted), 1)
        self.assertEqual(self.fx.events.count("Law.StartEnacting"), 1)
        self.cleaned()

    async def test_original_adapter_and_native_actor_are_not_retargeted_by_later_context_defaults(self):
        self.fx.mutate_adapters_after_clone = True
        result = await self.api.enact("law_workers_protections")
        self.assertTrue(result["accepted"])
        self.assertEqual(self.fx.clones[0][3], self.fx.command_adapter)
        self.assertTrue(all(adapter == self.fx.data_adapter for _, adapter in self.fx.release_calls))
        self.assertEqual(self.fx.owners[0].adapter, self.fx.command_adapter)
        self.assertEqual(self.fx.owners[0].native_actor_id, self.fx.native_actor_id)
        self.assertIsNone(result["native_actor_id"])
        self.cleaned()

    async def test_same_original_law_already_enacting_is_positive_noop_not_native_acceptance(self):
        self.fx.enacting = self.fx.law_ids[1]
        self.fx.active_law_ids.add(self.fx.law_ids[1])
        result = await self.api.enact("law_workers_protections")
        self.assertEqual(result["status"], "unchanged")
        self.assertIsNone(result["accepted"])
        self.assertIsNone(result["validation"])
        self.assertIsNone(result["submission"])
        self.assertTrue(result["active_observed"])
        self.assertTrue(result["enacting_observed"])
        self.assertEqual(self.fx.producer_bindings, [])
        self.assertEqual(self.fx.clones, [])
        self.assertEqual(self.fx.submitted, [])
        self.cleaned()

    async def test_different_current_law_does_not_host_block_explicit_new_target(self):
        self.fx.enacting = self.fx.law_ids[0]
        result = await self.api.enact("law_workers_protections")
        self.assertTrue(result["accepted"])
        self.assertEqual(len(self.fx.validations), 1)
        self.assertEqual(self.fx.enacting, self.fx.law_ids[1])
        self.assertNotIn("Country.GetLawBeingEnacted.StopEnacting", self.fx.events)
        self.cleaned()

    async def test_active_law_observation_is_not_an_invented_host_eligibility_gate(self):
        self.fx.validation_value, self.fx.validation_reason = False, "native already-active rule"
        result = await self.api.enact("law_serfdom")
        self.assertTrue(result["active_observed"])
        self.assertFalse(result["enacting_observed"])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "native already-active rule")
        self.assertEqual(len(self.fx.validations), 1)
        self.assertEqual(self.fx.submitted, [])
        self.cleaned()

    async def test_stop_clean_false_is_noop_but_error_none_and_nonbool_are_not_false(self):
        noop = await self.api.stop_enactment()
        self.assertEqual(noop["status"], "unchanged")
        self.assertIsNone(noop["accepted"])
        self.assertFalse(noop["enacting_observed"])
        self.assertEqual(self.fx.producer_bindings, [])
        self.cleaned()
        for error, value, kind in ((50, False, data.Kind.BOOL), (0, None, data.Kind.EMPTY), (0, 0, data.Kind.I32)):
            with self.subTest(error=error, value=value, kind=kind):
                self.fx.enacting = self.fx.law_ids[1]
                self.fx.current_operation_submitted = False
                self.fx.pre_observation = NS(error=error, value=value, kind=kind, value_handle=0)
                count = len(self.fx.validations)
                result = await self.api.stop_enactment()
                self.assertIsNone(result["enacting_observed"])
                self.assertIs(result["before"], self.fx.pre_observation)
                self.assertTrue(result["accepted"])
                self.assertEqual(len(self.fx.validations), count + 1)
                self.cleaned()
        self.fx.pre_observation = None

    async def test_errored_or_missing_selected_law_enacting_flag_does_not_authorize_noop(self):
        original = self.fx.evaluate
        for error, value, kind in ((50, True, data.Kind.BOOL), (0, None, data.Kind.EMPTY)):
            with self.subTest(error=error, value=value, kind=kind):
                self.fx.enacting = None
                def evaluate(native, expression, bindings, **kwargs):
                    if expression == "Law.IsBeingEnacted":
                        query = original(native, expression, bindings, **kwargs)
                        query.result.error, query.result.value, query.result.kind = error, value, kind
                        return query
                    return original(native, expression, bindings, **kwargs)
                count = len(self.fx.validations)
                with patch.object(data, "evaluate", evaluate):
                    result = await self.api.enact("law_workers_protections")
                self.assertIsNone(result["enacting_observed"])
                self.assertTrue(result["accepted"])
                self.assertEqual(len(self.fx.validations), count + 1)
                self.assertIn("enacting", result["law"]["state_reasons"])
                self.cleaned()

    async def test_known_unavailable_no_command_or_null_carrier_finishes_original_scope_once(self):
        for action in ("enact", "stop_enactment"):
            for mode in ("no_command", "null_carrier"):
                with self.subTest(action=action, mode=mode):
                    self.fx.enacting = None if action == "enact" else self.fx.law_ids[1]
                    self.fx.current_operation_submitted = False
                    original_evaluate = self.fx.evaluate
                    original_carrier = self.fx.command_value
                    observed, raised, original_queries = [], [], []
                    partial = {"original_partial": object()}
                    def evaluate(native, expression, bindings, **kwargs):
                        query = original_evaluate(native, expression, bindings, **kwargs)
                        if expression in ("Law.StartEnacting", "Country.GetLawBeingEnacted.StopEnacting"):
                            original_queries.append(query)
                            if mode == "no_command":
                                query.result.value = b"original non-command payload"
                                self.fx.handles[query.result.value_handle] = query.result.value
                                query.result.partial_result = partial
                                observed.append(query.result)
                        return query
                    def carrier(native, handle, **kwargs):
                        query = original_carrier(native, handle, **kwargs)
                        if mode == "null_carrier":
                            query.result.value = data.NativeDataCommand(0, 0, 0, handle,
                                "original incomplete carrier", self.fx.command_world)
                            query.result.partial_result = partial
                            observed.append(query.result)
                        return query
                    original_generated = run_mod.Run.generated_owner
                    async def generated(run, expression, bindings):
                        try:
                            return await original_generated(run, expression, bindings)
                        except run_mod.SourceUnavailable as error:
                            error.partial_result = partial
                            error.query = original_queries[-1]
                            raised.append((run, error))
                            raise
                    released_before = len(self.fx.release_calls)
                    with patch.object(data, "evaluate", evaluate), \
                         patch.object(data, "command_value", carrier), \
                         patch.object(run_mod.Run, "generated_owner", generated):
                        result = await self.invoke(action)
                    run, error = raised[0]
                    self.assertIs(result, run.public_result)
                    self.assertEqual(result["status"], "unavailable")
                    self.assertIsNone(result["accepted"])
                    self.assertIsNone(result["validation"])
                    self.assertIsNone(result["submission"])
                    self.assertIs(result["source_unavailable"], error)
                    self.assertIs(error.partial_result, partial)
                    self.assertIs(error.query, original_queries[-1])
                    self.assertIs(result["native_result"], observed[0])
                    self.assertIs(result["native_result"].partial_result, partial)
                    self.assertEqual(result["native_error"], 0)
                    self.assertEqual(run.status, "complete")
                    self.assertEqual(run.values, [])
                    self.assertEqual(self.fx.clones, [])
                    self.assertEqual(self.fx.validations, [])
                    self.assertEqual(self.fx.submitted, [])
                    released = self.fx.release_calls[released_before:]
                    self.assertTrue(released)
                    self.assertEqual(len({handle for handle, _ in released}), len(released))
                    self.assertTrue(all(adapter == self.fx.data_adapter for _, adapter in released))
                    for stage, native in run.operations:
                        if stage in ("Law.StartEnacting", "Country.GetLawBeingEnacted.StopEnacting"):
                            self.assertEqual(sum(handle == native.value_handle for handle, _ in released), 1)
                    self.cleaned()

    async def test_known_unavailable_keeps_unknown_or_unsettled_original_graph_without_release(self):
        for action in ("enact", "stop_enactment"):
            for mode in ("retain_unknown", "unsettled_owner"):
                with self.subTest(action=action, mode=mode):
                    self.fx.enacting = None if action == "enact" else self.fx.law_ids[1]
                    self.fx.current_operation_submitted = False
                    original_evaluate = self.fx.evaluate
                    original_generated = run_mod.Run.generated_owner
                    observed, raised, original_queries = [], [], []
                    partial = {"original_partial": object()}
                    uncertain = legacy.Query(None)
                    owner = NS(command=0xD1000, pending=uncertain, attempt=NS(outcome="pending"))
                    def evaluate(native, expression, bindings, **kwargs):
                        query = original_evaluate(native, expression, bindings, **kwargs)
                        if expression in ("Law.StartEnacting", "Country.GetLawBeingEnacted.StopEnacting"):
                            query.result.value = b"original no-command payload"
                            self.fx.handles[query.result.value_handle] = query.result.value
                            query.result.partial_result = partial
                            observed.append(query.result)
                            original_queries.append(query)
                        return query
                    async def generated(run, expression, bindings):
                        if mode == "retain_unknown":
                            run.retain_unknown, run.uncertain_query = True, uncertain
                        else:
                            run.owners.append(owner)
                        try:
                            return await original_generated(run, expression, bindings)
                        except run_mod.SourceUnavailable as error:
                            error.partial_result, error.query = partial, original_queries[-1]
                            raised.append((run, error))
                            raise
                    released_before = len(self.fx.release_calls)
                    with patch.object(data, "evaluate", evaluate), \
                         patch.object(run_mod.Run, "generated_owner", generated):
                        result = await self.invoke(action)
                    run, error = raised[0]
                    self.assertIs(result, run.public_result)
                    self.assertEqual(result["status"], "unavailable")
                    self.assertIsNone(result["accepted"])
                    self.assertIs(result["source_unavailable"], error)
                    self.assertIs(error.partial_result, partial)
                    self.assertIs(error.query, original_queries[-1])
                    self.assertIs(result["native_result"], observed[0])
                    self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
                    self.assertEqual(len(self.fx.release_calls), released_before)
                    self.assertTrue(run.values)
                    self.assertIn(run, self.ctx.owner_pool.snapshot())
                    if mode == "retain_unknown": self.assertIs(run.uncertain_query, uncertain)
                    else: self.assertIs(run.owners[0].pending, uncertain)
                    self.assertEqual(self.fx.submitted, [])
                    self.assertEqual(self.fx.clones, [])

    async def test_display_name_is_not_a_replacement_for_original_native_id_or_key(self):
        self.fx.law_names = {value: "Same display label" for value in self.fx.law_ids}
        result = await self.api.enact("Same display label")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["accepted"])
        self.assertEqual(self.fx.producer_bindings, [])
        self.assertEqual(self.fx.submitted, [])
        self.cleaned()


if __name__ == "__main__": unittest.main()
