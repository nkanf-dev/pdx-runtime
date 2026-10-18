"""Formal treaty CPU tests. No process connection or native game call is made."""
from __future__ import annotations

import asyncio
from decimal import Decimal, Inexact, Rounded, localcontext
import struct
from types import SimpleNamespace as R
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pdx_runtime import v3_commands as c, native_call
from pdx_runtime.v3_game.diplomacy import _treaty_proposals as backend
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI, PreparedTreaty
from pdx_runtime.query_executor import RetainedOwners

WORLD, ACTOR, TARGET, TREATY = 0x50000, 0x01000001, 0x02000004, 0x03000022


class TreatyWireTests(unittest.TestCase):
    def state(self, actor=ACTOR):
        wire = backend.terms_wire(actor, ACTOR, TARGET, ("defensive_pact",), 1095)
        return {"input_wire": wire, "actor_full_country_id": actor, "cost_source": {"value": 10000000}}

    @staticmethod
    def change(wire, transform):
        tree = c.decode_command(wire)
        def visit(value):
            if isinstance(value, c.Assignment):
                return transform(c.Assignment(value.key, visit(value.value)))
            if isinstance(value, tuple):
                return tuple(visit(item) for item in value if not (isinstance(item, c.Assignment) and item.key == c.Token(0x81B)))
            return value
        return c.encode_tree(visit(tree))

    def test_mutual_uses_real_both_country_refs_and_explicit_commitment_not_journal_time(self):
        state = self.state()
        tree = backend.check_wire(state["input_wire"], state, staging=True)
        field = backend.mature._field
        payload = field(field(tree.value, 0x41), 0x37C4)
        article = field(payload, 0x60C0)[0]
        self.assertEqual(field(article, 0x2F11), "defensive_pact")
        self.assertEqual(field(article, 0x60C7), c.UInt32(ACTOR))
        self.assertEqual(field(article, 0x60C8), c.UInt32(TARGET))
        self.assertEqual(field(article, 0x81B), ())
        self.assertEqual(field(payload, 0x60CA), 1095)
        self.assertNotEqual(field(payload, 0x60CA), 1825)
        self.assertEqual(tree.key, c.Token(14273))

    def test_only_empty_input_vector_omission_is_restored(self):
        state = self.state()
        omitted = self.change(state["input_wire"], lambda item: item)
        backend.check_wire(omitted, state, staging=True)
        broken = self.change(state["input_wire"], lambda item:
            c.Assignment(item.key, c.UInt32(0xFFFFFFFF)) if item.key == c.Token(0x60C7) else item)
        with self.assertRaisesRegex(RuntimeError, "country refs"):
            backend.check_wire(broken, state, staging=True)

    def test_wrong_real_definition_or_actor_is_not_a_valid_roundtrip(self):
        state = self.state()
        wrong = self.change(state["input_wire"], lambda item:
            c.Assignment(item.key, "free_text") if item.key == c.Token(0x2F11) else item)
        with self.assertRaisesRegex(RuntimeError, "article key"):
            backend.check_wire(wrong, state, staging=True)
        other = self.change(state["input_wire"], lambda item:
            c.Assignment(item.key, c.UInt32(TARGET)) if item.key == c.Token(0x2840) else item)
        with self.assertRaisesRegex(RuntimeError, "actor mirrors"):
            backend.check_wire(other, state, staging=True)

    def test_actor_can_be_either_mutual_party_without_changing_free_text_contract(self):
        self.assertTrue(backend.terms_wire(ACTOR, ACTOR, TARGET, ("defensive_pact",), 0))
        self.assertTrue(backend.terms_wire(TARGET, ACTOR, TARGET, ("defensive_pact",), 0))
        with self.assertRaisesRegex(ValueError, "actor=target payer"):
            backend.mature.free_text_wire(actor_full_country_id=ACTOR, source_full_country_id=ACTOR,
                target_full_country_id=TARGET, text="text", binding_days=0)


class NativeCostTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_cost_forwards_all_three_resolver_results_inside_same_callback(self):
        memory, image, batch_address = 0x200000, 0x140000000, 0x300000
        process = Mock(pid=123)
        process.allocate.return_value = memory
        process.resolve_export.return_value = 0x7000
        size = 40
        process.read.side_effect = lambda address, count: struct.pack("<q", 10000000) if count == 8 else b"\x01"
        state = {"process": process, "image_base": image, "owned_buffers": [],
            "source_full_country_id": ACTOR, "target_full_country_id": TARGET, "actor_full_country_id": ACTOR}
        async def run(state, label, calls, *, prepare):
            raw = prepare(batch_address)
            batch = native_call.NativeCallBatchResult.from_bytes(raw)
            self.assertEqual(batch.calls[11].target, image + 0xD513B0)
            for index in range(3):
                args = struct.unpack_from("<3Q", raw, batch.calls[6+index].arguments_address - batch_address)
                self.assertEqual(args[0], batch.calls[11].arguments_address + (2+index) * 8)
                self.assertEqual(args[1], batch_address + 32 + index * 56 + 32)
            return R(calls=tuple(R(rax=memory+16 if i==11 else 0) for i in range(12)))
        with patch.object(backend.mature, "_batch", AsyncMock(side_effect=run)):
            value = await backend._mutual_cost(state, R(command=0x600000), 0)
        self.assertEqual(value, 10000000)
        self.assertEqual(state["owned_buffers"], [])
        process.free.assert_called_once_with(memory)

    async def test_actual_native_kind_and_declared_inputs_are_checked_before_cost(self):
        for kind, declared, actual_count, reason in ((1,0,0,None),(0,0,0,"not mutual"),
                (1,1,0,"requires native input"),(1,0,1,"empty native input")):
            with self.subTest(kind=kind,declared=declared,count=actual_count):
                memory, batch_address, size = 0x200000, 0x300000, 40
                process=Mock(pid=123)
                process.allocate.return_value=memory
                process.resolve_export.return_value=0x7000
                raw=bytearray(size+0xEC)
                struct.pack_into("<ii",raw,0x18,actual_count,actual_count)
                raw[size+0x40]=kind
                struct.pack_into("<i",raw,size+0xE8,declared)
                process.read.return_value=bytes(raw)
                state={"process":process,"owned_buffers":[]}
                async def execute(state,label,calls,*,prepare):
                    encoded=prepare(batch_address)
                    batch=native_call.NativeCallBatchResult.from_bytes(encoded)
                    self.assertEqual(len(batch.calls),4)
                    self.assertTrue(all(call.target==0x7000 for call in batch.calls))
                    args=[call.arguments_address for call in batch.calls]
                    self.assertEqual(struct.unpack_from("<Q",encoded,args[0]-batch_address)[0],args[1]+8)
                    self.assertEqual(struct.unpack_from("<Q",encoded,args[2]-batch_address)[0],args[3]+8)
                    self.assertEqual(struct.unpack_from("<3Q",encoded,args[3]-batch_address),(memory+size,0,0xEC))
                    return R()
                with patch.object(backend.mature,"_batch",AsyncMock(side_effect=execute)):
                    if reason:
                        with self.assertRaisesRegex(ValueError,reason):
                            await backend._mutual_contract(state,R(command=0x600000),0)
                    else:
                        result=await backend._mutual_contract(state,R(command=0x600000),0)
                        self.assertEqual((result["native_kind"],result["declared_input_count"],result["input_count"]),(1,0,0))
                self.assertEqual(state["owned_buffers"],[])
                process.free.assert_called_once_with(memory)

    async def test_native_default_is_read_from_original_define_not_journal_1825(self):
        process = Mock(pid=123)
        process.allocate.return_value = 0x200000
        process.read.return_value = struct.pack("<i", 10)
        state = {"process": process, "image_base": 0x140000000, "owned_buffers": []}
        with patch.object(backend.mature, "_batch", AsyncMock()) as native:
            days = await backend.default_binding_days(state)
        self.assertEqual(days, 3650)
        self.assertNotEqual(days, 1825)
        self.assertEqual(native.call_args.args[2][0][1][1], 0x140000000 + 0x58FEA58)
        self.assertEqual(state["owned_buffers"], [])


class NativePrepareLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, valid=True, fail_reader=False, fail_contract=False):
        backend.mature.ATTEMPTS.clear()
        ctx = R(native=R(pid=123), process=R(pid=123), world=WORLD, image_base=0x140000000,
                adapters={role:role for role in ("data","access","call","script","commands")})
        staging = R(command=0x600000, pending=None, attempt=R(outcome="owned"),
                    serialize=Mock(), clone=Mock(), destroy=Mock())
        unique = R(command=0x610000, pending=None, attempt=R(outcome="owned"),
                   serialize=Mock(), validate=Mock(), destroy=Mock())
        operations = []
        def write_result(state, final=False):
            tree = c.decode_command(state["input_wire"])
            if not final:
                return NativePrepareLifecycleTests.omit_empty(tree)
            fields = []
            for item in tree.value:
                if item.key != c.Token(0x41):
                    fields.append(item); continue
                domain = tuple(c.Assignment(part.key, (c.Assignment(c.Token(0x51D), ()),))
                    if part.key == c.Token(0x1B) else
                    c.Assignment(part.key, c.Int64(state["cost_source"]["value"]))
                    if part.key == c.Token(0x27FE) else part for part in item.value)
                fields.append(c.Assignment(item.key, domain))
            return NativePrepareLifecycleTests.omit_empty(c.Assignment(tree.key, tuple(fields)))
        async def operation(state, label, make, *, owner=None):
            operations.append(label)
            if label == "registered_native_treaty_factory":
                return R(error=0, action=c.Action.CREATE, type_token=backend.mature.TOKEN, world=WORLD, command=staging.command)
            if label == "serialize_mutual_staging":
                return R(error=0, data=write_result(state))
            if label == "clone_mutual_proposal":
                return R(error=0, action=c.Action.CLONE, type_token=backend.mature.TOKEN, world=WORLD,
                         cloned_command=unique.command)
            if label == "serialize_mutual_proposal":
                return R(error=0, data=write_result(state, final=True))
            if label == "validate_mutual_proposal":
                return R(error=0, validated=valid, reason="" if valid else "原生条件不满足")
            if label.startswith("destroy_"):
                owner.command = 0; owner.attempt.outcome = "consumed"
                return R(error=0)
            raise AssertionError("unexpected native lifecycle label: " + label)
        async def reader(state, owner, wire, label):
            if fail_reader:
                raise RuntimeError("original Reader rejected")
            state["reader_receipts"].append({"error_count":0,"failed":0,"native_destructors_returned":True})
        async def context(state, owner):
            state["context_source"] = {"source":"original native context producer"}
        async def cost(state, owner, index, *, country_id=None):
            return 10000000 if country_id == ACTOR else 8500000
        def owner_from(client, result, **kwargs):
            return staging if result.action == c.Action.CREATE else unique
        patches = [patch.object(backend.mature, "_no_prior_unknown"),
            patch.object(backend.mature, "_country_valid", AsyncMock()),
            patch.object(backend.mature.accept, "_operation", AsyncMock(side_effect=operation)),
            patch.object(backend.mature, "_read_owned", AsyncMock(side_effect=reader)),
            patch.object(backend.mature, "_native_context", AsyncMock(side_effect=context)),
            patch.object(backend, "_mutual_contract", AsyncMock(
                side_effect=ValueError("actual native definition is not mutual") if fail_contract else None,
                return_value={"native_kind":1,"input_count":0,"declared_input_count":0})),
            patch.object(backend, "_mutual_cost", AsyncMock(side_effect=cost)),
            patch.object(c.NativeCommand, "from_result", side_effect=owner_from)]
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        if fail_reader or fail_contract:
            with self.assertRaisesRegex(ValueError if fail_contract else RuntimeError,
                    "not mutual" if fail_contract else "Reader rejected") as raised:
                await backend.prepare(ctx, ACTOR, ACTOR, TARGET, ("defensive_pact",), binding_days=1095)
            state = raised.exception.query_state
        else:
            state = await backend.prepare(ctx, ACTOR, ACTOR, TARGET, ("defensive_pact",), binding_days=1095)
        return state, operations, staging, unique

    @staticmethod
    def omit_empty(tree):
        def visit(value):
            if isinstance(value,c.Assignment):
                return c.Assignment(value.key,visit(value.value))
            if isinstance(value,tuple):
                return tuple(visit(item) for item in value if not (isinstance(item,c.Assignment) and item.key==c.Token(0x81B) and item.value==()))
            return value
        return c.encode_tree(visit(tree))

    async def test_real_prepare_orchestration_checks_reader_context_both_costs_clone_validate_no_submit(self):
        state, labels, staging, unique = await self.exercise()
        self.assertEqual(state["status"],"prepared_native_proposal_valid")
        self.assertEqual(state["cost_source"]["value"],10000000)
        self.assertEqual(state["cost_source"]["party_totals"],{ACTOR:10000000,TARGET:8500000})
        self.assertEqual(state["binding_days"],1095)
        self.assertEqual(state["binding_days_source"],"explicit")
        self.assertEqual(len(state["reader_receipts"]),2)
        self.assertEqual(labels,["registered_native_treaty_factory","serialize_mutual_staging","clone_mutual_proposal",
            "serialize_mutual_proposal","validate_mutual_proposal"])
        self.assertFalse(state["submission_attempted"])
        self.assertTrue(await backend.cleanup(state))
        self.assertEqual((staging.command,unique.command),(0,0))
        self.assertEqual(labels[-2:],["destroy_unique_owner","destroy_staging_owner"])

    async def test_reader_rejection_stops_before_context_cost_clone_or_validation_and_cleans_known_owner(self):
        state, labels, staging, unique = await self.exercise(fail_reader=True)
        self.assertEqual(labels,["registered_native_treaty_factory"])
        self.assertIsNone(state["unique_owner"])
        self.assertTrue(await backend.cleanup(state))
        self.assertEqual(staging.command,0)

    async def test_nonmutual_or_input_contract_rejection_precedes_context_cost_clone(self):
        state, labels, staging, unique = await self.exercise(fail_contract=True)
        self.assertEqual(labels,["registered_native_treaty_factory","serialize_mutual_staging"])
        backend.mature._native_context.assert_not_awaited()
        backend._mutual_cost.assert_not_awaited()
        self.assertTrue(await backend.cleanup(state))

    async def test_native_validation_rejection_keeps_game_reason_and_remains_unsubmitted(self):
        state, labels, staging, unique = await self.exercise(valid=False)
        self.assertEqual(state["status"],"native_proposal_rejected_owned")
        self.assertEqual(state["validation"].reason,"原生条件不满足")
        self.assertFalse(state["submission_attempted"])
        self.assertTrue(await backend.cleanup(state))


class Context:
    def __init__(self):
        self.native, self.process = R(pid=123), R(pid=123)
        self.session = R(native=self.native, namespace={})
        self.world, self.image_base = WORLD, 0x140000000
        self.adapters, self.diagnostics, self.helpers, self.calls = {}, {}, {}, []
        self.owner_pool = RetainedOwners()
    async def refresh(self): return R(world=self.world)
    async def country_id(self): return ACTOR
    async def perform(self, label, action):
        self.calls.append(label)
        return await action()
    def support(self, name): return self.helpers[name]


class TreatyDomainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx, self.api = Context(), None
        self.api = DiplomacyAPI(self.ctx)
        self.api._resolve = AsyncMock(return_value=TARGET)
        self.api._article_catalog = AsyncMock(return_value=({"key": "defensive_pact", "kind": "mutual"},
            {"key": "another_real_mutual", "kind": "mutual"}, {"key": "free_text", "kind": "directed"}))
        self.state = {"world":WORLD, "validation": R(validated=True, reason=""), "binding_days": 3650,
            "binding_days_source": "native_game_default", "cost_source": {"value": 10000000,
                "party_totals": {ACTOR:10000000, TARGET:8500000}},
            "handoff_uncertain": False}
        self.fake = R(prepare=AsyncMock(return_value=self.state), cleanup=AsyncMock(return_value=True),
            state_for=Mock(side_effect=lambda *args: {"pending":None,"owned_buffers":[],"owned_values":[],
                "world":WORLD,"image_base":0x140000000}),
            submit=AsyncMock(return_value=R(error=0, accepted=1)))
        self.api._treaty_backend = Mock(return_value=self.fake)
        self.api._treaty_snapshot = AsyncMock(side_effect=[{"all_treaties": [], "complete": True},
            {"all_treaties": [{"full_ref": TREATY, "is_draft": True, "party_full_refs": [ACTOR, TARGET]}], "complete": True}])
        self.api._has_articles = AsyncMock(return_value=True)

    async def preview(self, **kwargs):
        return await self.api.prepare_treaty("rus", articles=["defensive_pact"], **kwargs)

    async def test_preview_is_readonly_copied_terms_with_native_cost_source_and_unknown_ai(self):
        terms = await self.preview()
        self.assertIsInstance(terms, PreparedTreaty)
        self.assertEqual(terms["parties"], [ACTOR,TARGET])
        self.assertEqual(terms["articles"][0]["key"], "defensive_pact")
        self.assertEqual(terms["articles"][0]["obligation_scope"], "both_parties")
        self.assertEqual(terms["binding_days"], 3650)
        self.assertEqual(terms["binding_days_source"], "native_game_default")
        self.assertTrue(terms["binding_period_is_commitment"])
        self.assertEqual(terms["maintenance_cost"], Decimal("100.00000"))
        self.assertEqual(terms["cost_unit"], "influence")
        self.assertEqual(terms["party_costs"], [{"country_id":ACTOR,"maintenance_cost":Decimal("100.00000"),"unit":"influence"},
            {"country_id":TARGET,"maintenance_cost":Decimal("85.00000"),"unit":"influence"}])
        self.assertIsNone(terms["ai_acceptance"])
        self.assertFalse(terms["submitted"])
        self.assertNotIn("owner", terms)
        self.assertNotIn("prepared_wire_sha256", terms)
        self.fake.submit.assert_not_awaited()
        self.fake.cleanup.assert_awaited_once_with(self.state)
        self.assertEqual(await self.api.terms(terms), dict(terms))
        self.api._resolve.assert_awaited_once_with("rus")

    async def test_capacity_cost_model_is_not_a_periodic_charge_or_unknown_period(self):
        terms=await self.preview()
        self.assertEqual(terms["maintenance_cost"],Decimal("100.00000"))
        self.assertEqual(terms["cost_unit"],"influence")  # Compatible field.
        self.assertEqual(terms["cost_unit_label"],"influence points")
        self.assertEqual(terms["cost_model"],"capacity_allocation")
        self.assertIsNone(terms["cost_period"])
        self.assertEqual(terms["cost_period_status"],"not_applicable")
        self.assertIn("capacity",terms["cost_period_reason"])
        self.assertNotIn("cost_timing",terms)
        self.assertNotIn("draft_reservation",terms)
        result=await terms.propose()
        self.assertEqual(result["cost_model"],"capacity_allocation")
        self.assertEqual(result["cost_period_status"],"not_applicable")
        self.assertIsNone(result["cost_period"])
        self.assertEqual(result["cost_unit"],"influence")
        self.assertEqual(result["maintenance_cost"],Decimal("100.00000"))

    async def test_cost_metadata_view_preserves_existing_prepared_and_delivery_identity(self):
        terms=await self.preview()
        # Model an existing handle made before this metadata-only patch.
        for field in ("cost_model","cost_period","cost_period_status","cost_period_reason","cost_unit_label"):
            terms.pop(field,None)
        handle_type=type(terms)
        api,keys,world,days=terms._api,terms._keys,terms._world,terms._binding_days
        before_prepares=self.fake.prepare.await_count
        display=await self.api.terms(terms)
        self.assertEqual(display["cost_model"],"capacity_allocation")
        self.assertIs(type(terms),handle_type)
        self.assertIs(terms._api,api)
        self.assertIs(terms._keys,keys)
        self.assertEqual((terms._world,terms._binding_days),(world,days))
        self.assertEqual(self.fake.prepare.await_count,before_prepares)
        self.fake.submit.assert_not_awaited()
        result=await terms.propose()
        delivery,receipt,state=terms._delivery,terms._receipt,terms._proposal_state
        result_type=type(result)
        again=self.api._proposal_result(terms)
        self.assertIs(again,result)
        self.assertIs(type(again),result_type)
        self.assertIs(terms._delivery,delivery)
        self.assertIs(terms._receipt,receipt)
        self.assertIs(terms._proposal_state,state)
        self.assertEqual(again["cost_period_status"],"not_applicable")
        self.fake.submit.assert_awaited_once()

    async def test_explicit_period_passes_unchanged_not_a_lobby_countdown_rule(self):
        self.state.update(binding_days=1095, binding_days_source="explicit")
        terms = await self.preview(binding_days=1095)
        self.assertEqual(terms["binding_days"], 1095)
        self.assertEqual(terms["binding_days_source"], "explicit")
        self.assertEqual(self.fake.prepare.call_args.kwargs["binding_days"], 1095)

    async def test_native_rejection_is_game_reason_no_submission_and_known_cleanup(self):
        self.state["validation"] = R(validated=False, reason="双方关系必须达到友好")
        terms = await self.preview()
        self.assertFalse(terms["can_propose"])
        self.assertEqual(terms["reason"], "双方关系必须达到友好")
        result = await terms.propose()
        self.assertFalse(result["submitted"])
        self.assertIsNone(result["accepted"])
        self.assertFalse(result["submission_attempted"])
        self.fake.submit.assert_not_awaited()
        self.assertEqual(self.fake.cleanup.await_count, 2)

    async def test_explicit_propose_submits_once_and_draft_is_not_in_force(self):
        terms = await self.preview()
        first = await self.api.propose_treaty(terms)
        second = await self.api.propose_treaty(terms)
        self.assertIs(first, second)
        self.assertTrue(first["accepted"])
        self.assertTrue(first["proposal_created"])
        self.assertEqual(first["id"], TREATY)
        self.assertEqual(first["status"], "proposed")
        self.assertFalse(first["in_force"])
        self.fake.submit.assert_awaited_once_with(self.state)
        self.assertEqual(self.api._has_articles.await_args.args, (TREATY, ("defensive_pact",)))
        self.assertIsNotNone(self.api._has_articles.await_args.kwargs["read_state"])
        self.assertEqual(self.fake.cleanup.await_count, 2)

    async def test_native_inforce_readback_is_distinct_from_receipt_acceptance(self):
        terms = await self.preview()
        self.api._treaty_snapshot.side_effect = [{"all_treaties": [], "complete": True},
            {"all_treaties": [{"full_ref":TREATY,"is_draft":False,"party_full_refs":[TARGET,ACTOR]}], "complete":True}]
        result = await self.api.propose_treaty(terms)
        self.assertEqual(result["status"], "in_force")
        self.assertTrue(result["in_force"])

    async def test_accepted_receipt_without_matching_article_is_not_treaty_success(self):
        terms = await self.preview()
        self.api._has_articles.return_value = False
        result = await self.api.propose_treaty(terms)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["proposal_created"])
        self.assertFalse(result["in_force"])
        self.assertIsNone(result["id"])
        self.assertEqual(result["status"], "accepted_not_observed")

    async def test_reject_different_session_or_world_without_replay(self):
        terms = await self.preview()
        other = DiplomacyAPI(self.ctx)
        with self.assertRaisesRegex(ValueError, "another game session"):
            await other.propose_treaty(terms)
        self.ctx.world += 1
        with self.assertRaisesRegex(ValueError, "campaign or acting country changed"):
            await self.api.propose_treaty(terms)
        self.fake.submit.assert_not_awaited()

    async def test_any_real_mutual_key_and_native_usage_limits_not_strategy_allowlist(self):
        terms = await self.api.prepare_treaty(TARGET, articles=["another_real_mutual", "another_real_mutual"])
        self.assertEqual([row["key"] for row in terms["articles"]], ["another_real_mutual", "another_real_mutual"])
        self.assertEqual(self.fake.prepare.call_args.args[4], ("another_real_mutual", "another_real_mutual"))

    async def test_options_without_terms_lists_real_registry_does_not_choose_strategy(self):
        result = await self.api.treaty_options(TARGET)
        self.assertEqual([row["key"] for row in result["article_types"]], ["defensive_pact", "another_real_mutual", "free_text"])
        self.assertTrue(result["catalog_complete"])
        self.assertIsNone(result["can_propose"])
        self.fake.prepare.assert_not_awaited()
        self.fake.submit.assert_not_awaited()

    async def test_unknown_delivery_retains_original_state_and_never_replays(self):
        terms = await self.preview()
        error = RuntimeError("original native receipt unknown")
        self.fake.submit.side_effect = error
        self.fake.cleanup.return_value = False
        with self.assertRaises(RuntimeError) as first:
            await self.api.propose_treaty(terms)
        with self.assertRaises(RuntimeError) as second:
            await self.api.propose_treaty(terms)
        self.assertIs(first.exception, error)
        self.assertIs(second.exception, error)
        self.fake.submit.assert_awaited_once()
        self.assertIs(self.ctx.diagnostics["diplomacy"][-1]["state"], self.state)

    async def test_low_decimal_context_does_not_round_native_cost(self):
        self.state["cost_source"]["value"] = 9223372036854775807
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = context.traps[Rounded] = True
            terms = await self.preview()
        self.assertEqual(terms["maintenance_cost"], Decimal("92233720368547.75807"))

    async def test_known_proposal_history_survives_unlink_and_never_adopts_another_same_terms_ref(self):
        terms=await self.preview()
        other=TREATY+1
        self.api._treaty_snapshot.side_effect=[
            {"all_treaties":[],"complete":True},
            {"all_treaties":[{"full_ref":TREATY,"is_draft":True,"party_full_refs":[ACTOR,TARGET]}],"complete":True},
            {"all_treaties":[],"complete":True},
            {"all_treaties":[{"full_ref":other,"is_draft":False,"party_full_refs":[ACTOR,TARGET]}],"complete":True}]
        result=await terms.propose()
        result_identity=id(result)
        self.assertEqual(terms._observed_treaty_id,TREATY)
        self.assertTrue(result["proposal_created"])
        self.assertEqual(result["id"],TREATY)
        unlinked=await result.observe()
        self.assertEqual(id(unlinked),result_identity)
        self.assertEqual(unlinked["id"],TREATY)
        self.assertTrue(unlinked["proposal_created"])
        self.assertFalse(unlinked["current_country_membership"])
        self.assertFalse(unlinked["currently_observed"])
        self.assertIsNone(unlinked["in_force"])
        self.assertEqual(unlinked["status"],"not_in_current_country_treaties")
        self.assertIsNone(unlinked["response"])
        later=await result.observe()
        self.assertEqual(later["id"],TREATY)
        self.assertTrue(later["proposal_created"])
        self.assertFalse(later["current_country_membership"])
        self.assertIsNone(later["in_force"])
        self.assertEqual(later["matching_treaty_ids"],[])
        self.assertEqual(self.api._has_articles.await_count,1)
        self.assertTrue(all(call.args[0]==TREATY for call in self.api._has_articles.await_args_list))
        self.fake.submit.assert_awaited_once()

    async def test_known_history_survives_read_error_and_uses_lazy_original_handle_scalar(self):
        terms=await self.preview()
        result=await terms.propose()
        self.assertEqual(terms._observed_treaty_id,TREATY)
        self.api._treaty_snapshot.side_effect=RuntimeError("later read failed")
        again=await result.observe()
        self.assertEqual(again["id"],TREATY)
        self.assertTrue(again["proposal_created"])
        self.assertTrue(again["accepted"])
        self.assertTrue(again["submitted"])
        self.assertEqual(again["observation_status"],"unknown")
        self.assertIsNone(again["current_country_membership"])
        self.assertIsNone(again["in_force"])
        self.fake.submit.assert_awaited_once()

    async def test_true_snapshot_seed_restores_old_handle_history_without_rebuilding_or_dispatch(self):
        terms=await self.preview()
        self.api._treaty_snapshot.side_effect=[{"all_treaties":[],"complete":True},{"all_treaties":[],"complete":True}]
        result=await terms.propose()
        self.assertIsNone(result["id"])
        original_type,original_result,delivery,receipt=type(terms),result,terms._delivery,terms._receipt
        # Root may seed only this scalar from its saved original native snapshot.
        terms._observed_treaty_id=TREATY
        restored=self.api._proposal_result(terms)
        self.assertIs(restored,original_result)
        self.assertIs(type(terms),original_type)
        self.assertIs(terms._delivery,delivery)
        self.assertIs(terms._receipt,receipt)
        self.assertEqual(restored["id"],TREATY)
        self.assertTrue(restored["proposal_created"])
        self.fake.submit.assert_awaited_once()

    async def test_observe_after_accepted_missing_id_reads_effect_without_another_submit(self):
        terms = await self.preview()
        self.api._treaty_snapshot.side_effect = [{"all_treaties":[],"complete":True},
            {"all_treaties":[],"complete":True},
            {"all_treaties":[{"full_ref":TREATY,"is_draft":False,"party_full_refs":[ACTOR,TARGET]}],"complete":True}]
        result = await self.api.propose_treaty(terms)
        self.assertIsNone(result["id"])
        observed = await result.observe()
        self.assertTrue(observed["in_force"])
        self.assertEqual(observed["id"], TREATY)
        self.fake.submit.assert_awaited_once()

    async def test_multiple_matching_real_treaties_preserve_ids_not_fake_absence(self):
        terms = await self.preview()
        self.api._treaty_snapshot.side_effect = [{"all_treaties":[],"complete":True},
            {"all_treaties":[{"full_ref":TREATY,"is_draft":False,"party_full_refs":[ACTOR,TARGET]},
                              {"full_ref":TREATY+1,"is_draft":True,"party_full_refs":[ACTOR,TARGET]}],"complete":True}]
        result = await self.api.propose_treaty(terms)
        self.assertIsNone(result["id"])
        self.assertIsNone(result["in_force"])
        self.assertIsNone(result["proposal_created"])
        self.assertFalse(result["attribution_unique"])
        self.assertEqual(result["status"], "ambiguous_matching_treaties")
        self.assertEqual(result["matching_treaty_ids"], [TREATY,TREATY+1])
        self.assertEqual(result["matching_treaties"], [{"id":TREATY,"in_force":True},{"id":TREATY+1,"in_force":False}])

    async def test_accepted_delivery_is_saved_before_observation_failure_and_resumes_only_read(self):
        terms = await self.preview()
        self.api._observe_treaty_proposal = AsyncMock(side_effect=[RuntimeError("read transport failed"),
            {"id":TREATY,"proposal_created":True,"in_force":True,"status":"in_force","observation_status":"complete"}])
        result = await terms.propose()
        self.assertTrue(result["submitted"])
        self.assertTrue(result["accepted"])
        self.assertTrue(result["submission_attempted"])
        self.assertIs(terms._receipt, self.fake.submit.return_value)
        self.assertEqual(result["observation_status"],"unknown")
        self.assertIsNone(result["in_force"])
        self.assertIsNone(terms._failure)
        self.ctx.diagnostics.clear()
        later = await terms.status()
        self.assertTrue(later["accepted"])
        self.assertTrue(later["submitted"])
        self.assertTrue(later["in_force"])
        self.fake.submit.assert_awaited_once()
        self.assertEqual(self.fake.prepare.await_count,2)

    async def test_receiver_rejection_status_preserves_delivery_and_never_reports_not_submitted(self):
        terms = await self.preview()
        self.fake.submit.return_value = R(error=0,accepted=0)
        result = await terms.propose()
        self.assertTrue(result["submitted"])
        self.assertFalse(result["accepted"])
        self.ctx.diagnostics.clear()
        status = await terms.status()
        self.assertTrue(status["submitted"])
        self.assertFalse(status["accepted"])
        self.assertEqual(status["status"],"receiver_rejected")
        self.fake.submit.assert_awaited_once()

    async def test_semantic_changes_are_rejected_before_prepare_or_submit(self):
        changes = [lambda terms:terms.update(binding_days=30), lambda terms:terms.update(target_id=99),
            lambda terms:terms["parties"].__setitem__(0,99),
            lambda terms:terms["articles"][0].update(key="different_article"),
            lambda terms:terms["articles"][0]["parties"].__setitem__(1,99),
            lambda terms:terms["articles"].append({"key":"extra_article"})]
        for change in changes:
            with self.subTest(change=change):
                terms = await self.preview()
                self.fake.prepare.reset_mock()
                change(terms)
                with self.assertRaisesRegex(ValueError,"terms were edited"):
                    await terms.propose()
                self.fake.prepare.assert_not_awaited()
                self.fake.submit.assert_not_awaited()

    async def test_edit_while_final_prepare_is_pending_is_detected_before_submit(self):
        terms=await self.preview()
        async def prepare(*args,**kwargs):
            terms["articles"][0]["key"]="edited_while_waiting"
            return self.state
        self.fake.prepare.side_effect=prepare
        with self.assertRaisesRegex(ValueError,"terms were edited"):
            await terms.propose()
        self.fake.submit.assert_not_awaited()
        self.assertFalse(terms._delivery["submitted"])
        self.assertIsNone(terms._delivery["accepted"])

    async def test_receipt_terms_are_canonical_and_do_not_share_nested_public_lists(self):
        terms = await self.preview()
        result = await terms.propose()
        terms["articles"][0]["parties"][0] = 99
        terms["articles"][0]["key"] = "edited_after_send"
        self.assertEqual(result["articles"][0]["key"],"defensive_pact")
        self.assertEqual(result["articles"][0]["parties"],[ACTOR,TARGET])
        # Editing a returned copy does not rewrite the private receipt facts.
        result["accepted"] = False
        result["submitted"] = False
        self.api._observe_treaty_proposal = AsyncMock(return_value={"in_force":True,"status":"in_force"})
        status = await result.observe()
        self.assertTrue(status["submitted"])
        self.assertTrue(status["accepted"])
        self.assertEqual(status["articles"][0]["key"],"defensive_pact")
        self.fake.submit.assert_awaited_once()

    async def test_known_prequeue_owned_failure_is_not_receiver_rejection(self):
        terms = await self.preview()
        self.state["unique_owner"] = R(command=123,pending=None,attempt=R(outcome="owned"))
        self.fake.submit.return_value = R(error=5,accepted=0)
        self.fake.cleanup.return_value = False
        result = await terms.propose()
        self.assertFalse(result["submitted"])
        self.assertIsNone(result["accepted"])
        self.assertTrue(result["submission_attempted"])
        self.assertEqual(result["status"],"not_submitted")
        self.assertTrue(any(owner is self.state for owner in self.ctx.owner_pool.snapshot()))

    async def test_unknown_owner_graph_survives_diagnostics_clear_and_is_not_retired(self):
        terms = await self.preview()
        query = R(_result=None,_released=False)
        self.state.update(submission_attempted=True,handoff_uncertain=True,
            pending={"query":query},owned_buffers=[0xDEAD],
            unique_owner=R(command=123,pending=query,attempt=R(outcome="unknown")))
        self.fake.cleanup.return_value = False
        self.fake.submit.side_effect = RuntimeError("unknown native delivery")
        with self.assertRaisesRegex(RuntimeError,"unknown native delivery"):
            await terms.propose()
        self.ctx.diagnostics.clear()
        self.assertTrue(any(owner is self.state for owner in self.ctx.owner_pool.snapshot()))
        self.assertIs(self.state["pending"]["query"],query)
        self.assertFalse(self.api._retire_state(self.state))
        status = await terms.status()
        self.assertTrue(status["submission_attempted"])
        self.assertIsNone(status["submitted"])
        self.assertIsNone(status["accepted"])
        self.fake.submit.assert_awaited_once()

    async def test_failed_effect_read_resumes_its_exact_query_before_a_fresh_read(self):
        terms = await self.preview()
        query = R(_result=None,_released=False)
        captured={}
        async def observe(terms,state,before):
            if not captured:
                state["pending"]={"query":query}
                state["owned_buffers"]=[0xBEEF]
                captured["state"]=state
                raise RuntimeError("read continuation lost its current frame")
            return {"status":"in_force","in_force":True,"proposal_created":True,"id":TREATY}
        async def wait(original):
            self.assertIs(original,query)
            original._result=R(error=0)
            original._released=True
            return original._result
        self.ctx.wait=AsyncMock(side_effect=wait)
        self.api._observe_treaty_proposal=AsyncMock(side_effect=observe)
        first=await terms.propose()
        self.assertTrue(first["accepted"])
        self.ctx.diagnostics.clear()
        self.assertTrue(any(owner is captured["state"] for owner in self.ctx.owner_pool.snapshot()))
        def free(state,*addresses):
            self.assertEqual(addresses,(0xBEEF,))
            state["owned_buffers"].clear()
        with patch.object(backend.mature,"_free",side_effect=free):
            result=await terms.status()
        self.assertTrue(result["accepted"])
        self.assertTrue(result["in_force"])
        self.ctx.wait.assert_awaited_once_with(query)
        self.assertFalse(any(owner is captured["state"] for owner in self.ctx.owner_pool.snapshot()))
        self.fake.submit.assert_awaited_once()

    async def test_cached_unreleased_effect_read_never_dispatches_a_new_read_on_repeated_status(self):
        terms=await self.preview()
        query=R(_result=R(error=0),_released=False,read=Mock())
        captured={}
        async def observe(terms,state,before):
            state["pending"]={"query":query}
            state["owned_buffers"]=[0xBEEF]
            captured["state"]=state
            raise RuntimeError("query storage release failed after cached result")
        self.ctx.wait=AsyncMock()
        self.ctx._release_query_slot=Mock()
        self.ctx._forget=Mock()
        self.api._observe_treaty_proposal=AsyncMock(side_effect=observe)
        await terms.propose()
        self.ctx.diagnostics.clear()
        with patch.object(backend.mature,"_free") as free:
            first=await terms.status()
            second=await terms.status()
        self.assertTrue(second["accepted"])
        self.assertTrue(second["submitted"])
        self.assertEqual(second["observation_status"],"pending_original_read_settlement")
        self.assertIsNone(second["in_force"])
        self.assertTrue(any(owner is captured["state"] for owner in self.ctx.owner_pool.snapshot()))
        self.api._observe_treaty_proposal.assert_awaited_once()
        self.assertEqual(self.fake.state_for.call_count,2)  # baseline + first effect only
        query.read.assert_not_called()
        self.ctx.wait.assert_not_awaited()
        self.ctx._forget.assert_not_called()
        self.assertEqual(self.ctx._release_query_slot.call_args.args,(query,))
        free.assert_not_called()
        self.fake.submit.assert_awaited_once()

    async def test_read_ledger_retains_cached_but_unreleased_storage_and_retires_only_after_release(self):
        query = R(_result=R(error=0),_released=False)
        state = {"pending":None,"owned_buffers":[],"owned_values":[],"last_command_query":query}
        self.api._retain_state(state,"read","catalog_read")
        backend.mature.accept.ATTEMPTS.append(state)
        self.ctx.diagnostics.clear()
        self.assertFalse(self.api._retire_state(state))
        self.assertTrue(any(owner is state for owner in self.ctx.owner_pool.snapshot()))
        query._released=True
        self.assertTrue(self.api._retire_state(state))
        self.assertFalse(any(owner is state for owner in self.ctx.owner_pool.snapshot()))
        self.assertFalse(any(item is state for item in backend.mature.accept.ATTEMPTS))

    async def test_prepare_registers_unknown_state_before_return_or_first_native_failure(self):
        async def prepare(ctx,*args,on_state,**kwargs):
            state={"pending":{"query":R(_result=None,_released=False)},"owned_buffers":[0xCAFE],"world":WORLD}
            on_state(state)
            self.ctx.diagnostics.clear()
            self.assertTrue(any(owner is state for owner in self.ctx.owner_pool.snapshot()))
            raise RuntimeError("first native operation failed")
        self.fake.prepare.side_effect=prepare
        self.fake.cleanup.return_value=False
        with self.assertRaisesRegex(RuntimeError,"first native"):
            await self.preview()
        self.assertEqual(len(self.ctx.owner_pool.snapshot()),1)
        retained, = self.ctx.owner_pool.snapshot()
        self.assertEqual(retained["owned_buffers"],[0xCAFE])

    async def test_numeric_status_valid_but_unlinked_draft_or_inforce_is_not_current(self):
        for is_draft in (True,False):
            with self.subTest(raw_is_draft=is_draft):
                refs={"complete_refs_copied":True,"full_refs":[],"native_count":0,
                    "country_full_id":ACTOR,"observed_country_full_id":ACTOR}
                reader=R(read_country_refs=AsyncMock(return_value=refs))
                self.ctx.helpers["diplomacy.treaty.draft"]=reader
                snapshot=AsyncMock(return_value={"full_ref":TREATY,"is_draft":is_draft,
                    "party_full_refs":[ACTOR,TARGET],"world":WORLD})
                self.fake.mature=R(_proposal_snapshot=snapshot)
                result=await self.api.treaty_status(TREATY)
                self.assertTrue(result["object_present"])
                self.assertEqual(result["raw_is_draft"],is_draft)
                self.assertFalse(result["current_country_membership"])
                self.assertTrue(result["membership_complete"])
                self.assertEqual(result["current_country_id"],ACTOR)
                self.assertEqual(result["status"],"not_in_current_country_treaties")
                self.assertIsNone(result["in_force"])
                self.assertIsNone(result["response"])
                self.assertNotIn("declin",result["status"])
                self.assertFalse(result["atomic_snapshot"])
                self.assertEqual(reader.read_country_refs.await_args.kwargs["country_full_id"],ACTOR)
                snapshot.assert_awaited_once()
                self.fake.prepare.assert_not_awaited()
                self.fake.submit.assert_not_awaited()

    async def test_numeric_status_requires_current_membership_for_draft_and_inforce(self):
        for is_draft in (True,False):
            with self.subTest(raw_is_draft=is_draft):
                self.ctx.helpers["diplomacy.treaty.draft"]=R(read_country_refs=AsyncMock(return_value={
                    "complete_refs_copied":True,"full_refs":[TREATY],"native_count":1}))
                self.fake.mature=R(_proposal_snapshot=AsyncMock(return_value={"full_ref":TREATY,
                    "is_draft":is_draft,"party_full_refs":[ACTOR,TARGET],"world":WORLD}))
                result=await self.api.treaty_status(TREATY)
                self.assertTrue(result["current_country_membership"])
                self.assertEqual(result["status"],"proposed" if is_draft else "in_force")
                self.assertEqual(result["in_force"],not is_draft)
                self.assertIsNone(result["response"])

    async def test_numeric_status_incomplete_country_refs_is_unknown_not_absent(self):
        self.ctx.helpers["diplomacy.treaty.draft"]=R(read_country_refs=AsyncMock(return_value={
            "complete_refs_copied":False,"header_copied":False,"full_refs":[],"native_count":0}))
        self.fake.mature=R(_proposal_snapshot=AsyncMock(return_value={"full_ref":TREATY,
            "is_draft":False,"party_full_refs":[ACTOR,TARGET],"world":WORLD}))
        result=await self.api.treaty_status(TREATY)
        self.assertEqual(result["status"],"membership_unknown")
        self.assertIsNone(result["current_country_membership"])
        self.assertFalse(result["membership_complete"])
        self.assertFalse(result["raw_is_draft"])
        self.assertIsNone(result["in_force"])
        self.assertIsNone(result["response"])
        self.fake.submit.assert_not_awaited()

    async def test_numeric_status_uses_fresh_count_capacity_without_fake_absence(self):
        reader=R(read_country_refs=AsyncMock(side_effect=[
            {"complete_refs_copied":False,"header_copied":True,"owned_slots":64,"native_count":65,"full_refs":[]},
            {"complete_refs_copied":True,"header_copied":True,"owned_slots":65,"native_count":65,"full_refs":[TREATY]}]))
        self.ctx.helpers["diplomacy.treaty.draft"]=reader
        self.fake.mature=R(_proposal_snapshot=AsyncMock(return_value={"full_ref":TREATY,
            "is_draft":True,"party_full_refs":[ACTOR,TARGET],"world":WORLD}))
        result=await self.api.treaty_status(TREATY)
        self.assertEqual(reader.read_country_refs.await_count,2)
        self.assertEqual(reader.read_country_refs.await_args.kwargs["slots"],65)
        self.assertTrue(result["current_country_membership"])
        self.assertEqual(result["status"],"proposed")

    async def test_pending_operation_resumes_original_submit_without_preparing_again(self):
        from pdx_runtime.v3_game import GameContext, PendingOperation
        ready = asyncio.Event()
        async def submit(state):
            await ready.wait()
            return R(error=0, accepted=1)
        self.fake.submit.side_effect = submit
        ctx = GameContext(R(native=self.ctx.native, namespace={}), process=self.ctx.process, timeout=.001)
        ctx.world, ctx.image_base = WORLD, 0x140000000
        ctx.refresh = AsyncMock()
        ctx.country_id = AsyncMock(return_value=ACTOR)
        api = DiplomacyAPI(ctx)
        api._resolve, api._article_catalog = self.api._resolve, self.api._article_catalog
        api._treaty_backend = Mock(return_value=self.fake)
        api._treaty_snapshot, api._has_articles = self.api._treaty_snapshot, self.api._has_articles
        terms = PreparedTreaty(api, ACTOR, TARGET, WORLD, ("defensive_pact",), 1095,
            {"target_id":TARGET,"parties":[ACTOR,TARGET],"binding_days":1095,
             "articles":[{"key":"defensive_pact","kind":"mutual","parties":[ACTOR,TARGET],"obligation_scope":"both_parties"}]})
        first = await api.propose_treaty(terms)
        self.assertIsInstance(first, PendingOperation)
        second = await api.propose_treaty(terms)
        self.assertIsInstance(second, PendingOperation)
        ready.set()
        a, b = await first.resume(timeout=1), await second.resume(timeout=1)
        self.assertIs(a, b)
        self.fake.prepare.assert_awaited_once()
        self.fake.submit.assert_awaited_once()
        self.fake.cleanup.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
