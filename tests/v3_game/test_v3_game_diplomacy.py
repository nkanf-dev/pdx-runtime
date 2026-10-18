"""CPU orchestration contracts only. No process attachment or native execution."""
from __future__ import annotations
import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pdx_runtime import v3_commands as c, v3_data as data, v3_objects as objects
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from pdx_runtime.query_executor import Diagnostics, RetainedOwners

WORLD, ACTOR, TARGET, PROPOSAL, ARTICLE = 0x50000, 0, 0x02000004, 0x0100002B, 0x01000053


class Query:
    def __init__(self, value): self.value = value


class Context:
    def __init__(self):
        self.native, self.process, self.session = object(), object(), object()
        self.world, self.image_base = WORLD, 0x140000000
        self.adapters = {"data": "pdx_v3_data.dll", "commands": "pdx_v3_commands.dll"}
        self.diagnostics, self.helpers, self.calls = Diagnostics(), {}, []
        self.owner_pool = RetainedOwners()
    async def refresh(self): return NS(world=WORLD)
    async def country_id(self): return ACTOR
    async def type_id(self, name): return {"Country": 983, "int32": 777}[name]
    async def query(self, label, factory, **kw):
        self.calls.append(label)
        query = factory()
        return query.value if isinstance(query, Query) else query
    async def perform(self, label, operation):
        self.calls.append(label)
        return await operation()
    def support(self, name): return self.helpers[name]


def country(ref):
    return objects.Country(ref, ref, 0, ref & 0xFFFFFF, 0xABC000, 123)


def player_result(actor=ACTOR, observer=False):
    return NS(error=0, controlled_country_error=0, observer_enabled=observer,
        world=WORLD, controlled_country_id=actor)


class DiplomacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = Context()
        self.api = DiplomacyAPI(self.ctx)
        self.labels = []

    def labels_fixture(self, client, expression, bindings, **kw):
        self.labels.append((expression, bindings))
        ref = bindings[0].object
        value = ("ACT" if ref == ACTOR else "FRA") if expression.endswith("GetTagName") else "原生国家名"
        return Query(NS(action=data.Action.EVALUATE, error=0, kind=data.Kind.STRING, value=value, value_handle=len(self.labels)))

    async def test_resolve_explicit_full_id_has_no_world_or_objects_receipt_gate(self):
        with patch.object(objects, "read_ids", side_effect=AssertionError("extra ID receipt")), \
             patch.object(self.ctx, "refresh", side_effect=AssertionError("extra world receipt")):
            self.assertEqual(await self.api._resolve(0), 0)
            self.assertEqual(await self.api._resolve({"country_id": 0x80000002}), 0x80000002)
        self.assertFalse(self.ctx.calls)

    async def test_resolve_tag_keeps_original_resolution_despite_stale_context_world(self):
        original = NS(error=0, target_error=0, world=WORLD+1, target_country_id=0)
        with patch("pdx_runtime.v3_game.diplomacy.player.resolve_tag", return_value=Query(original)) as resolve:
            result = await self.api._resolve(" fra ")
        self.assertEqual(result, 0)
        self.assertEqual(resolve.call_args.args[1], "FRA")
        self.assertEqual(resolve.call_count, 1)

    async def test_resolve_native_error_preserves_actual_result(self):
        from pdx_runtime.v3_game._core import NativeRejection
        original = NS(error=0, target_error=1168, world=WORLD+1, target_country_id=0)
        with patch("pdx_runtime.v3_game.diplomacy.player.resolve_tag", return_value=Query(original)):
            with self.assertRaises(NativeRejection) as error:
                await self.api._resolve("FRA")
        self.assertIs(error.exception.result, original)

    async def test_resolve_invalid_selection_fails_locally_without_query(self):
        for target in (None, False, -1, 0xFFFFFFFF, "", "not-a-tag"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                await self.api._resolve(target)
        self.assertFalse(self.ctx.calls)

    async def test_countries_pages_all_ids_zero_valid_and_no_raw_pointers(self):
        pages = [NS(error=0, world=WORLD, records=(country(ACTOR),), next_index=1, slot_bound=2),
            NS(error=0, world=WORLD, records=(country(TARGET),), next_index=2, slot_bound=2)]
        with patch("pdx_runtime.v3_game.diplomacy.objects.enumerate_objects", side_effect=[Query(p) for p in pages]) as enum, \
             patch("pdx_runtime.v3_game.diplomacy.data.evaluate", side_effect=self.labels_fixture), \
             patch("pdx_runtime.v3_game.diplomacy.data.release", return_value=Query(NS(error=0,value_handle=0))) as release:
            rows = await self.api.countries()
        self.assertEqual([r["id"] for r in rows], [ACTOR, TARGET])
        self.assertEqual(rows[1]["tag"], "FRA")
        self.assertEqual(rows[1]["name"], "原生国家名")
        self.assertNotIn("pointer", rows[1])
        self.assertEqual(enum.call_count, 2)
        self.assertEqual(release.call_count, 4)
        self.assertTrue(all(b[0].type_id == 983 for _, b in self.labels))

    async def test_native_scalar_failure_is_none_with_reason_not_zero(self):
        with patch("pdx_runtime.v3_game.diplomacy.data.evaluate", return_value=Query(NS(error=1168,value_handle=0))):
            row = await self.api._country_label(TARGET)
        self.assertIsNone(row["tag"])
        self.assertIsNone(row["name"])
        self.assertIn("name", row["unavailable"])

    async def test_original_relation_getter_keeps_zero_negative_and_caches_only_metadata(self):
        function=NS(name="GetRelationsWith",type_id=983,argument_count=1,return_type_id=777,callbacks=(123,0,0,0))
        page=NS(error=0,rows=(function,),next=2061,total=2061)
        seen=[]
        def evaluate(client,expression,bindings,**kwargs):
            seen.append((expression,bindings))
            return Query(NS(action=data.Action.EVALUATE,error=0,kind=data.Kind.I32,type_id=777,value=0 if len(seen)==1 else -17,value_handle=40+len(seen)))
        with patch("pdx_runtime.v3_game.diplomacy.player.observe_player",return_value=Query(player_result())), \
             patch("pdx_runtime.v3_game.diplomacy.data.enumerate_functions",return_value=Query(page)) as metadata, \
             patch("pdx_runtime.v3_game.diplomacy.data.evaluate",side_effect=evaluate), \
             patch("pdx_runtime.v3_game.diplomacy.data.release",return_value=Query(NS(error=0,value_handle=0))) as release:
            first=await self.api._relation_points(ACTOR,TARGET)
            second=await self.api._relation_points(ACTOR,TARGET)
        self.assertEqual(first,(0,None))
        self.assertEqual(second,(-17,None))
        self.assertEqual(metadata.call_count,1)
        self.assertEqual(release.call_count,2)
        self.assertEqual([item[0] for item in seen],["Country.GetRelationsWith(GetPlayer)"]*2)
        self.assertTrue(all(len(bindings)==1 and bindings[0].object==TARGET and bindings[0].type_id==983 for _,bindings in seen))

    async def test_relation_getter_never_substitutes_player_for_explicit_foreign_source(self):
        with patch("pdx_runtime.v3_game.diplomacy.player.observe_player",return_value=Query(player_result(actor=TARGET))), \
             patch("pdx_runtime.v3_game.diplomacy.data.evaluate") as evaluate:
            score,reason=await self.api._relation_points(ACTOR,TARGET)
        self.assertIsNone(score)
        self.assertIn("current controlled country",reason)
        evaluate.assert_not_called()

    async def test_relation_signature_action_or_fixed_type_is_not_misread_as_int_score(self):
        function=NS(name="GetRelationsWith",type_id=983,argument_count=1,return_type_id=998,callbacks=(123,0,0,0))
        with patch("pdx_runtime.v3_game.diplomacy.player.observe_player",return_value=Query(player_result())), \
             patch.object(self.api,"_country_functions",new=AsyncMock(return_value=(function,))), \
             patch("pdx_runtime.v3_game.diplomacy.data.evaluate") as evaluate:
            score,reason=await self.api._relation_points(ACTOR,TARGET)
        self.assertIsNone(score)
        self.assertIn("int32",reason)
        evaluate.assert_not_called()

    async def test_country_label_uses_shared_plain_name_and_keeps_rich_original_private(self):
        from pdx_runtime.v3_game.country import _plain_name
        rich="\x15H Great Britain\x15! \x16flag \x15!"
        rich=rich.encode().decode("unicode_escape")
        with patch.object(self.api,"_scalar",new=AsyncMock(side_effect=["GBR",rich])):
            row=await self.api._country_label(TARGET)
        self.assertEqual(row["name"],_plain_name(rich))
        self.assertNotIn("\x15",row["name"])
        # No terminating '!': preserve this malformed icon rather than blindly
        # dropping its control byte and presenting 'flag' as visible prose.
        self.assertIn("\x16flag",row["name"])
        self.assertNotIn("raw_name",row)

    async def test_plays_reports_leaders_only_and_keeps_independent_bad_leader(self):
        a = objects.DiplomaticPlay(7,7,0,7,123,ACTOR,TARGET,0,1168,4,0)
        b = objects.DiplomaticPlay(8,8,0,8,124,2,3,0,0,5,6)
        page = NS(error=0,world=WORLD,records=(a,b),next_index=10,slot_bound=10)
        with patch("pdx_runtime.v3_game.diplomacy.objects.enumerate_objects", return_value=Query(page)), \
             patch.object(self.api,"_country_label",new=AsyncMock(side_effect=lambda i:{"id":i})):
            rows = await self.api.plays()
        self.assertEqual([r["id"] for r in rows],[7])
        self.assertEqual(rows[0]["attacker_id"],ACTOR)
        self.assertIsNone(rows[0]["defender_id"])
        self.assertEqual(rows[0]["participant_coverage"],"leaders only")

    async def test_bad_country_pagination_does_not_spin_or_invent_empty_success(self):
        page = NS(error=0,world=WORLD,records=(),next_index=0,slot_bound=10)
        with patch("pdx_runtime.v3_game.diplomacy.objects.enumerate_objects",return_value=Query(page)):
            with self.assertRaisesRegex(RuntimeError,"did not advance"):
                await self.api.countries()

    def action_helper(self, *, rejected=False):
        plan = {"status":"native_rejected_no_submission" if rejected else "prepared_not_submitted",
            "validation":NS(reason="外交影响力不足" if rejected else "",validated=not rejected),
            "before":{"pact_valid":True,"pact_id":178,"pending_valid":False,"action_id":0xFFFFFFFF}}
        async def submit(current, *, intent):
            current["submit_count"] = current.get("submit_count",0)+1
            current.update(status="observed_expected_effect",submission=NS(accepted=1),
                after={"pact_valid":True,"pact_id":178,"pending_valid":False,"action_id":0xFFFFFFFF})
        helper = NS(prepare=AsyncMock(return_value=plan),submit=AsyncMock(side_effect=submit),
            destroy_unsubmitted=AsyncMock())
        self.ctx.helpers["diplomacy._country_commands"] = helper
        return helper,plan

    async def action(self, method, helper, plan):
        with patch("pdx_runtime.v3_game.diplomacy.player.resolve_tag",return_value=Query(NS(error=0,target_error=0,world=WORLD,target_country_id=TARGET))) as resolve, \
             patch("pdx_runtime.v3_game.diplomacy.player.observe_player",return_value=Query(player_result())):
            result = await method("fra")
        self.assertIs(helper.prepare.call_args.args[0],self.ctx.native)
        self.assertIs(helper.prepare.call_args.args[1],self.ctx.process)
        self.assertEqual(helper.prepare.call_args.kwargs["first"],ACTOR)
        self.assertEqual(helper.prepare.call_args.kwargs["second"],TARGET)
        self.assertEqual(resolve.call_args.args[1],"FRA")
        return result

    async def test_improve_relations_hides_schema_submits_once_then_releases(self):
        helper,plan=self.action_helper()
        result=await self.action(self.api.improve_relations,helper,plan)
        self.assertTrue(result["accepted"] and result["completed"])
        self.assertEqual(result["pact_id"],178)
        self.assertEqual(helper.prepare.call_args.kwargs["action_key"],"increase_relations")
        self.assertEqual(helper.prepare.call_args.kwargs["intent"],"start_pact")
        self.assertEqual(helper.submit.await_count,1)
        helper.destroy_unsubmitted.assert_awaited_once_with(plan,keep_status=True)

    async def test_stop_is_separate_explicit_intent(self):
        helper,plan=self.action_helper()
        await self.action(self.api.stop_improving_relations,helper,plan)
        self.assertEqual(helper.prepare.call_args.kwargs["intent"],"break_pact")
        self.assertEqual(helper.submit.call_args.kwargs["intent"],"break_pact")

    async def test_native_rejection_returns_game_reason_no_submit_and_cleans(self):
        helper,plan=self.action_helper(rejected=True)
        result=await self.action(self.api.improve_relations,helper,plan)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"],"外交影响力不足")
        helper.submit.assert_not_awaited()
        helper.destroy_unsubmitted.assert_awaited_once()


    async def test_improve_relations_does_not_switch_a_different_controlled_actor(self):
        helper,plan=self.action_helper()
        with patch.object(self.api,"_resolve",new=AsyncMock(return_value=TARGET)), \
             patch("pdx_runtime.v3_game.diplomacy.player.observe_player",return_value=Query(player_result(actor=TARGET))):
            with self.assertRaisesRegex(RuntimeError,"actual controlled"):
                await self.api.improve_relations(TARGET)
        helper.prepare.assert_not_awaited()

    async def test_relations_does_not_fabricate_relation_points_from_pact(self):
        helper,plan=self.action_helper()
        with patch.object(self.api,"_resolve",new=AsyncMock(return_value=TARGET)), \
             patch.object(self.api,"_country_label",new=AsyncMock(return_value={"id":TARGET})), \
             patch.object(self.api,"_relation_points",new=AsyncMock(return_value=(None,"native getter unavailable"))):
            row=await self.api.relations(TARGET)
        self.assertTrue(row["improving_relations"])
        self.assertIsNone(row["relation_points"])
        self.assertIn("getter",row["relation_points_unavailable_reason"])
        helper.submit.assert_not_awaited()

    def treaty_query(self, *, clauses=True):
        source=NS(value_handle=10)
        element=NS(value_handle=11,full_ref=PROPOSAL)
        async def evaluate(state,label,expression,binding):
            if expression=="Country.GetDraftTreaties":return source
            if expression=="Treaty.DeclineTreatyProposal":return NS(value_handle=20)
            raise AssertionError(expression)
        async def complete(state,label,factory): return await self.ctx.query(label,factory)
        async def parties(state,row,binding):
            row["country_views"]=[{"full_country_id":i,"native_validity":{"valid":True}} for i in (ACTOR,TARGET)]
        async def articles(state,row,binding):
            state["articles"].append({"source_treaty_full_id":PROPOSAL,"full_article_id":ARTICLE,
                "view":"GetOffers","status":"owned_article_and_definition_origin_observed"})
        async def cleanup(state):
            state["owned_values"].clear();state["owned_buffers"].clear()
            return state
        state_factory=lambda *a: {"owned_values":[],"pending":None,"operations":[]}
        q=NS(new_state=state_factory,_types=AsyncMock(side_effect=lambda state,*a:state["types"].update(Country=983,Treaty=985)),
            _evaluate=AsyncMock(side_effect=evaluate),complete=complete,
            _owned_object=Mock(return_value=object()),_full_id=AsyncMock(return_value=PROPOSAL),
            _scope_valid=AsyncMock(return_value={"valid":True}),_parties=AsyncMock(side_effect=parties),
            _articles=AsyncMock(side_effect=articles),source_check=AsyncMock(),cleanup_completed=AsyncMock(side_effect=cleanup),TREATY_SCOPE=0x65)
        self.ctx.helpers["diplomacy.treaty.query"]=q
        return q,element

    async def test_proposals_uses_real_draft_source_full_id_not_index_zero_identity(self):
        q,element=self.treaty_query()
        with patch("pdx_runtime.v3_game.diplomacy.data.list_count",return_value=Query(NS(total=1))), \
             patch("pdx_runtime.v3_game.diplomacy.data.list_element",return_value=Query(element)):
            rows=await self.api.proposals()
        self.assertEqual(rows[0]["id"],PROPOSAL)
        self.assertEqual(rows[0]["parties"],[ACTOR,TARGET])
        self.assertIsNone(rows[0]["direction"])
        self.assertNotIn("source_index",rows[0])
        q.cleanup_completed.assert_awaited_once()
        self.assertIn("Country.GetDraftTreaties",[call.args[2] for call in q._evaluate.call_args_list])

    async def test_terms_returns_original_received_text_and_typed_parameters_without_input_substitution(self):
        q,element=self.treaty_query()
        original="收到的原文\n第二行🙂"
        parameters=((c.Assignment(c.Token(0x8F),original),),)
        body={"article_full_id":ARTICLE,"treaty_full_id":PROPOSAL,"parameters":parameters,
            "text":original,"text_bytes":original.encode(),"complete":True,"text_complete":True,"parameter_wire":b"original-native-wire"}
        reader=NS(read_article_parameters=AsyncMock(return_value={"result":body}),cleanup_completed=AsyncMock(return_value=True))
        self.ctx.helpers["diplomacy.treaty.article_parameters"]=reader
        with patch("pdx_runtime.v3_game.diplomacy.data.list_count",return_value=Query(NS(total=1))), \
             patch("pdx_runtime.v3_game.diplomacy.data.list_element",return_value=Query(element)):
            result=await self.api.terms(PROPOSAL)
        self.assertEqual(result["clauses"][0]["text"],original)
        self.assertEqual(result["clauses"][0]["parameters"],[{"type":"text","value":original}])
        self.assertNotIn("text_bytes",result["clauses"][0])
        self.assertNotIn("parameter_wire",result["clauses"][0])
        self.assertNotIn("text",reader.read_article_parameters.call_args.kwargs)
        self.assertEqual(reader.read_article_parameters.call_args.kwargs["article_full_id"],ARTICLE)
        reader.cleanup_completed.assert_awaited_once()
        q.cleanup_completed.assert_awaited_once()

    async def test_missing_exact_proposal_cleans_known_reads_before_reporting_absent(self):
        q,element=self.treaty_query()
        with patch("pdx_runtime.v3_game.diplomacy.data.list_count",return_value=Query(NS(total=0))):
            with self.assertRaisesRegex(ValueError,"not unique"):
                await self.api.terms(PROPOSAL)
        q.cleanup_completed.assert_awaited_once()

    def response_helper(self, *, valid=True, accepted=1, effect=False):
        query=NS(_released=True)
        owner=NS(command=0xD0000,pending=None,attempt=NS(outcome="owned",query=query))
        state={"validation":NS(error=0,validated=valid,reason="条约已失效" if not valid else ""),
            "prepared_wire_sha256":"internal-only","unique_owner":owner,"staging_owner":None,
            "pending":None,"owned_buffers":[],"owned_values":[],"queries":[],
            "handoff_uncertain":False,"cleanup_uncertain":False,"unadopted_clone":None}
        async def cleanup(actual):
            owner.command=0;owner.pending=None
            owner.attempt=NS(outcome="consumed",query=NS(_released=True))
            return True
        async def submit(*args,**kwargs):
            owner.command=0;owner.pending=None
            owner.attempt=NS(outcome="consumed",query=NS(_released=True))
            return NS(error=0,accepted=accepted)
        helper=NS(prepare_response=AsyncMock(return_value=state),
            submit_response=AsyncMock(side_effect=submit),
            observe_response_state=AsyncMock(return_value={"native_draft_to_inforce_transition":effect}),
            cleanup_completed=AsyncMock(side_effect=cleanup))
        self.ctx.helpers["diplomacy.treaty.proposal"]=helper
        return helper,state

    async def test_accept_is_once_with_internal_mature_helpers_not_accepted_as_effect(self):
        helper,state=self.response_helper(effect=False)
        with patch.object(self.api,"_drafts",new=AsyncMock(return_value=[{"id":PROPOSAL,"complete":True}])), \
             patch.object(self.api,"_controlled",new=AsyncMock()):
            result=await self.api.accept(PROPOSAL)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(helper.submit_response.await_count,1)
        self.assertNotIn("prepared_wire_sha256",result)
        helper.cleanup_completed.assert_awaited_once_with(state)

    async def test_rejected_accept_has_native_reason_and_no_submit(self):
        helper,state=self.response_helper(valid=False)
        with patch.object(self.api,"_drafts",new=AsyncMock(return_value=[{"id":PROPOSAL,"complete":True}])), \
             patch.object(self.api,"_controlled",new=AsyncMock()):
            result=await self.api.accept(PROPOSAL)
        self.assertEqual(result["reason"],"条约已失效")
        self.assertFalse(result["accepted"])
        helper.submit_response.assert_not_awaited()
        helper.cleanup_completed.assert_awaited_once()

    async def test_incomplete_terms_block_response_without_creating_command(self):
        helper,state=self.response_helper()
        with patch.object(self.api,"_drafts",new=AsyncMock(return_value=[{"id":PROPOSAL,"complete":False}])):
            with self.assertRaisesRegex(RuntimeError,"incomplete"):
                await self.api.accept(PROPOSAL)
        helper.prepare_response.assert_not_awaited()

    async def test_decline_uses_original_false_producer_and_serialized_actor_ref_before_one_submit(self):
        q,element=self.treaty_query()
        wire=c.encode_command(14275,{0x2840:c.UInt32(ACTOR),0x41:{0x240:c.UInt32(ACTOR),0x57D6:c.UInt32(PROPOSAL),0x37C5:False}})
        owner=NS(command=0xD0000,pending=None,attempt=NS(outcome="owned",query=NS(_released=True)),
            serialize=Mock(),validate=Mock(),submit=Mock(),destroy=Mock())
        async def op(state,label,make,**kw):
            state["last_command_query"]=NS(_released=True)
            owner.attempt.query=state["last_command_query"]
            if label=="clone_original_decline":return NS(error=0,type_token=14275,world=WORLD,cloned_command=0xD0000)
            if label=="serialize_original_decline":return NS(error=0,data=wire)
            if label=="validate_original_decline":return NS(error=0,validated=1,reason="")
            if label=="submit_original_decline_once":
                owner.command=0;owner.attempt.outcome="consumed"
                return NS(error=0,accepted=1)
            raise AssertionError(label)
        from pdx_runtime.v3_game.diplomacy.treaty import accept as mature
        accept_helper=NS(TOKEN=mature.TOKEN,_one=mature._one,_operation=AsyncMock(side_effect=op))
        self.ctx.helpers["diplomacy.treaty.accept"]=accept_helper
        carrier=data.NativeDataCommand(0xB0000,0xB1000,0xB0018,20,"CDataCommand",WORLD)
        with patch("pdx_runtime.v3_game.diplomacy.data.list_count",return_value=Query(NS(total=1))), \
             patch("pdx_runtime.v3_game.diplomacy.data.list_element",return_value=Query(element)), \
             patch("pdx_runtime.v3_game.diplomacy.data.command_value",return_value=Query(NS(value=carrier))), \
             patch("pdx_runtime.v3_game.diplomacy.commands.NativeCommand.from_result",return_value=owner), \
             patch.object(self.api,"_controlled",new=AsyncMock()), \
             patch.object(self.api,"_drafts",new=AsyncMock(side_effect=[[{"id":PROPOSAL,"complete":True}],[]])):
            result=await self.api.decline(PROPOSAL)
        self.assertTrue(result["accepted"] and result["completed"])
        self.assertEqual(result["decision"],"decline")
        labels=[call.args[1] for call in accept_helper._operation.call_args_list]
        self.assertEqual(labels.count("submit_original_decline_once"),1)
        self.assertIn("Treaty.DeclineTreatyProposal",[call.args[2] for call in q._evaluate.call_args_list])
        q.cleanup_completed.assert_awaited_once()


    async def test_real_core_pending_reentry_waits_original_task_and_never_builds_opposite_decision(self):
        from pdx_runtime.v3_game import GameContext, PendingOperation
        helper,state=self.response_helper(effect=True)
        ready=asyncio.Event()
        async def submit(*args,**kwargs):
            await ready.wait()
            state["unique_owner"].command=0
            state["unique_owner"].attempt=NS(outcome="consumed",query=NS(_released=True))
            return NS(error=0,accepted=1)
        helper.submit_response.side_effect=submit
        ctx=GameContext(NS(native=object(),namespace={}),process=object(),timeout=.001)
        ctx.world,ctx.image_base=WORLD,0x140000000
        ctx.refresh=AsyncMock()
        ctx.country_id=AsyncMock(return_value=ACTOR)
        ctx._support["diplomacy.treaty.proposal"]=helper
        api=DiplomacyAPI(ctx)
        with patch.object(api,"_drafts",new=AsyncMock(return_value=[{"id":PROPOSAL,"complete":True}])), \
             patch.object(api,"_controlled",new=AsyncMock()):
            first=await api.accept(PROPOSAL)
            self.assertIsInstance(first,PendingOperation)
            opposite=await api.decline(PROPOSAL)
            self.assertIsInstance(opposite,PendingOperation)
            ready.set()
            a=await first.resume(timeout=1)
            b=await opposite.resume(timeout=1)
        self.assertIs(a,b)
        self.assertEqual(a["decision"],"accept")
        self.assertEqual(helper.submit_response.await_count,1)
        self.assertEqual(helper.prepare_response.await_count,1)
        self.assertTrue(a["completed"])


    def test_parameter_values_preserve_native_integer_width_without_guessing_fixed_scale(self):
        values=((c.Assignment(c.Token(701),c.Int64(-9223372036854775808)),),)
        result=self.api._parameter_values(values)
        self.assertEqual(result,[{"type":"native_parameter","fields":[{"field_token":701,"value":{"type":"int64","value":-9223372036854775808}}]}])


if __name__=="__main__":unittest.main(verbosity=2)
