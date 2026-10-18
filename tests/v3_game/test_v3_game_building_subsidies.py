"""Focused CPU subsidy seams over actual SDK/owner/Session wire paths.

The byte-memory and callback transport are fixtures from the existing mature
Building policy production seams. No native/game execution. The new helper,
packaged mature owner ledger, builders/decoders, NativeCommand and real core
pending-query continuation run here; fixture eligibility is not game proof.
"""
import asyncio
from decimal import Decimal
import struct
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from tests.fixtures.building_policy import PolicyMemory, PolicyTransport, WORLD, ACTOR, BUILDING, STATE, UNIQUE, SHARED
from pdx_runtime import v3_data as data, v3_commands as commands, v3_player_control as player
from pdx_runtime import native_call, v3_session
from pdx_runtime.v3_game._core import GameContext, PendingOperation
from pdx_runtime.v3_game._run import command_field
from pdx_runtime.v3_game.economy import building_subsidies as helper


class SubsidyTransport(PolicyTransport):
    def __init__(self,process):
        super().__init__(process)
        self.allowed={True:True,False:False};self.reason={True:b"",False:b"Original policy rejects disabling"}
        self.explicit_enabled=None;self.current_cost=123456;self.cost_kind=data.Kind.FIXED
        self.cost_error=0;self.has_cost=True;self.flip_after_submit=True
        self.wrong_mode=False;self.hold_submit=False;self.held_event=asyncio.Event()
        self.loop=asyncio.get_running_loop()
        self.fail_after_submit=False;self.drift_after_validate=False;self.native_action_count=0
        self.paginate_metadata=False;self.metadata_pages=[];self.missing_on=False
        self.destroy_error=0;self.release_error=0
    def _execute(self,symbol,address,request_id):
        if symbol=="PdxV3ConsolePlayerStateInterface":
            fields=list(player.STATE.unpack(self.process.read(address,player.STATE.size)))
            assert fields[2]==1  # OBSERVE ONLY. No pause/speed/tag/control mutation.
            fields[1],fields[10],fields[11]=0,0,1  # RUNNING, started.
            fields[8],fields[12],fields[13]=self.observer,self.player_actor,0
            fields[20],fields[21],fields[26]=0xABC01,self.world,0xABC02
            self.process.store(address,player.STATE.pack(*fields))
        else:super()._execute(symbol,address,request_id)
    def queue_v3_interface_callback(self,callback,address):
        symbol=next(name for name,value in self.process.exports.items() if value==callback)
        if symbol=="PdxNativeCallInterface" and self.hold_submit:
            outer=native_call.REQUEST.unpack(self.process.read(address,native_call.REQUEST.size))
            _,opaque,_=struct.unpack("<3Q",self.process.read(outer[4],24))
            packed=commands.RESULT.unpack(self.process.read(opaque,commands.RESULT.size))
            if packed[2]==commands.Action.SUBMIT:
                self.hold=True
                result=super().queue_v3_interface_callback(callback,address)
                return result
        return super().queue_v3_interface_callback(callback,address)
    def v3_world_status(self):
        status=super().v3_world_status()
        if self.held is not None:
            # This read observes the original held query after executor adoption.
            self.loop.call_soon_threadsafe(self.held_event.set)
        return status
    def _data(self,address):
        fields=list(data.REQUEST.unpack(self.process.read(address,data.REQUEST.size)))
        action=data.Action(fields[2]);snapshot=b""
        fields[1],fields[10]=0,33
        if action==data.Action.TYPES:
            rows=((134,"Building"),(36,"Command"),(5,"bool"),(82,"CFixedPoint"))
            snapshot=b"".join(data.TYPE_INFO.pack(t,len(n),0,1,0x111,0x222,0x333,n.encode(),b"") for t,n in rows)
            fields[7],fields[8],fields[9]=len(rows),len(rows),len(rows)
        elif action==data.Action.FUNCTIONS:
            assert fields[4]==134
            rows=[("IsSubsidized",5),("ToggleSubsidiesOn",36),("ToggleSubsidiesOff",36)]
            if self.missing_on:rows=[row for row in rows if row[0]!="ToggleSubsidiesOn"]
            if self.has_cost:rows.append(("GetSubsidies",82))
            total=len(rows);start=fields[5];self.metadata_pages.append(start)
            if self.paginate_metadata:rows=rows[start:start+2]
            snapshot=b"".join(data.FUNCTION_INFO.pack(134,t,0,1,len(n),0,0x111,0x222,0,0,0,n.encode(),b"") for n,t in rows)
            fields[7],fields[8],fields[9]=len(rows),start+len(rows),total
        elif action==data.Action.EVALUATE:
            expression=self.process.read(address+fields[20],fields[21]).decode()
            bindings=tuple(data.BINDING_INPUT.unpack(self.process.read(address+fields[22]+i*data.BINDING_INPUT.size,data.BINDING_INPUT.size)) for i in range(fields[23]))
            assert bindings==((134,6,BUILDING),)
            self.evaluations.append(expression);self.bindings.append(bindings)
            fields[14]=self.next_handle;self.next_handle+=1
            if expression=="Building.IsSubsidized":
                fields[11],fields[26]=self.bool_kind,int(self.enabled["subsidies"])
            elif expression=="Building.GetSubsidies":
                fields[1],fields[11],fields[26]=self.cost_error,self.cost_kind,self.current_cost&0xffffffffffffffff
                fields[18]=8;snapshot=struct.pack("<Q",fields[26])
            elif expression in ("Building.ToggleSubsidiesOn","Building.ToggleSubsidiesOff"):
                self.explicit_enabled=expression.endswith("On")
                self.selected=("subsidies",expression,"Building.IsSubsidized",20250)
                self.generated_handle=fields[14]
                fields[11],fields[18],fields[28]=data.Kind.COMMAND,24,b"CDataCommand"
                snapshot=struct.pack("<3Q",SHARED,0xC1000,SHARED+0x18)
            else:raise AssertionError("Unexpected expression; no toggle fallback, no estimate fiction")
        elif action==data.Action.COMMAND_VALUE:
            snapshot=struct.pack("<4Q",SHARED,0xC1000,SHARED+0x18,self.carrier_world)
            fields[9]=32
        elif action==data.Action.RELEASE:
            handle=fields[14]
            assert handle and handle not in self.releases
            self.releases.append(handle);fields[14]=0;fields[1]=self.release_error
        else:raise AssertionError(action)
        if snapshot:
            assert len(snapshot)<=fields[25]
            self.process.store(address+fields[24],snapshot);fields[19]=len(snapshot)
        self.process.store(address,data.REQUEST.pack(*fields))
    def _command(self,address):
        fields=commands.RESULT.unpack(self.process.read(address,commands.RESULT.size))
        action=commands.Action(fields[2])
        if action==commands.Action.CLONE:self.command_live=True
        if action==commands.Action.SERIALIZE:
            self.mode_wire=commands.Token(10760 if self.wrong_mode else 20254 if self.explicit_enabled else 20255)
        if action==commands.Action.VALIDATE:
            self.validation_bool=int(self.allowed[self.explicit_enabled]);self.validation_reason=self.reason[self.explicit_enabled]
        super()._command(address)
        if action==commands.Action.DESTROY and self.destroy_error:
            # Fixture-native output before the real SDK decodes it. No sidecar
            # receipt sampler, header reconstruction, or extra witness gate.
            result=list(commands.RESULT.unpack(self.process.read(address,commands.RESULT.size)))
            result[1]=self.destroy_error
            raw=commands.RESULT.pack(*result)
            self.process.store(address,raw)
            self.command_results[-1]=(address,raw)
        if action==commands.Action.VALIDATE and self.drift_after_validate:
            self.enabled["subsidies"]=self.explicit_enabled
        if action==commands.Action.SUBMIT:
            self.native_action_count+=1
            if self.accepted and not self.submit_error and self.flip_after_submit:
                self.enabled["subsidies"]=self.explicit_enabled
            if self.fail_after_submit:self.state_owner=ACTOR+1


class Fixture:
    def __init__(self):
        self.process=PolicyMemory();self.native=SubsidyTransport(self.process)
        self.ctx=GameContext(NS(native=self.native,namespace={}),self.process,timeout=1,poll_interval=0)
        self.ctx.world=WORLD;self.ctx.image_base=0x140000000
        self.ctx.refresh=AsyncMock(return_value=NS(world=WORLD))
        self.ctx.country_id=AsyncMock(return_value=ACTOR)
    async def read(self):return await self.ctx.perform("subsidy.read",lambda:helper.read_subsidy(self.ctx,BUILDING))
    async def set(self,enabled=True):return await self.ctx.perform("subsidy.set",lambda:helper.set_subsidy(self.ctx,BUILDING,enabled))


class SubsidyTests(unittest.IsolatedAsyncioTestCase):
    def cleaned(self,fixture):
        self.assertFalse(fixture.process.active_callback_buffers())
        self.assertFalse(fixture.ctx.pending_queries)
        self.assertFalse(getattr(fixture.ctx,"_building_subsidy_runs",[]))
        self.assertFalse(fixture.ctx._query_lock.locked())
        self.assertFalse(fixture.process.closed)
    async def test_read_native_state_on_off_constraints_current_cost_not_forecast_while_running(self):
        fixture=Fixture();result=await fixture.read()
        self.assertEqual(result["status"],"observed",result)
        self.assertFalse(result["current"])
        self.assertTrue(result["can_enable"]);self.assertFalse(result["can_disable"])
        self.assertEqual(result["disable_reason"],"Original policy rejects disabling")
        self.assertEqual(result["current_cost"],Decimal("1.23456"))
        self.assertEqual(result["current_cost_period"],"week")
        self.assertIsNone(result["estimated_cost"]);self.assertIn("not a forecast",result["estimate_reason"])
        self.assertIn("per-type override",result["scope"])
        self.assertEqual(fixture.native.commands.count(commands.Action.DESTROY),2)
        self.assertNotIn(commands.Action.SUBMIT,fixture.native.commands)
        self.assertNotIn("Building.ToggleSubsidies",fixture.native.evaluations)
        self.cleaned(fixture)
    async def test_enable_uses_explicit_native_on_once_and_fresh_effect_without_pause(self):
        fixture=Fixture();result=await fixture.set(True)
        self.assertEqual(result["status"],"observed")
        self.assertTrue(result["accepted"]);self.assertTrue(result["effect_observed"]);self.assertTrue(result["current"])
        self.assertEqual(fixture.native.native_action_count,1)
        self.assertEqual(fixture.native.evaluations.count("Building.ToggleSubsidiesOn"),1)
        self.assertNotIn("Building.ToggleSubsidiesOff",fixture.native.evaluations)
        tree=commands.decode_command(fixture.native.serialized_wire)
        self.assertEqual(command_field(tree,10232),(commands.UInt32(BUILDING),))
        self.assertEqual(command_field(tree,1012),commands.Token(20254))
        self.assertGreaterEqual(fixture.native.evaluations.count("Building.IsSubsidized"),3)
        self.cleaned(fixture)
    async def test_disable_explicit_native_off_and_scope_warning_are_preserved(self):
        fixture=Fixture();fixture.native.enabled["subsidies"]=True;fixture.native.allowed[False]=True
        result=await fixture.set(False)
        self.assertEqual(result["status"],"observed");self.assertFalse(result["current"])
        self.assertIn("Country",result["scope"]);self.assertIn("per-type override",result["scope"])
        self.assertEqual(fixture.native.evaluations.count("Building.ToggleSubsidiesOff"),1)
        self.assertEqual(command_field(commands.decode_command(fixture.native.serialized_wire),1012),commands.Token(20255))
        self.cleaned(fixture)
    async def test_already_requested_state_is_a_real_read_no_command(self):
        fixture=Fixture();fixture.native.enabled["subsidies"]=True
        result=await fixture.set(True)
        self.assertEqual(result["status"],"unchanged");self.assertFalse(result["changed"])
        self.assertIsNone(result["accepted"]);self.assertFalse(result["submitted"])
        self.assertTrue(result["effect_observed"])
        self.assertTrue(result["current"]);self.assertFalse(fixture.native.commands)
        self.assertEqual(fixture.native.evaluations,["Building.IsSubsidized"])
        self.cleaned(fixture)
    async def test_native_rejection_reason_returns_without_submit_and_cleans_unique_owner(self):
        fixture=Fixture();fixture.native.allowed[True]=False;fixture.native.reason[True]=b"Original government building restriction"
        result=await fixture.set(True)
        self.assertIsNone(result["accepted"]);self.assertFalse(result["submitted"]);self.assertEqual(result["status"],"rejected")
        self.assertEqual(result["reason"],"Original government building restriction")
        self.assertNotIn(commands.Action.SUBMIT,fixture.native.commands)
        self.assertEqual(fixture.native.commands.count(commands.Action.DESTROY),1)
        self.cleaned(fixture)
    async def test_accepted_without_fresh_effect_is_not_claimed_or_replayed(self):
        fixture=Fixture();fixture.native.flip_after_submit=False
        result=await fixture.set(True)
        self.assertTrue(result["accepted"]);self.assertEqual(result["status"],"accepted")
        self.assertFalse(result["effect_observed"]);self.assertFalse(result["current"])
        self.assertEqual(fixture.native.native_action_count,1)
        self.cleaned(fixture)
    async def test_receiver_rejection_is_consumed_once_not_effect_or_destroy(self):
        fixture=Fixture();fixture.native.accepted=0
        result=await fixture.set(True)
        self.assertEqual(result["status"],"rejected");self.assertFalse(result["accepted"])
        self.assertEqual(fixture.native.native_action_count,1)
        self.assertNotIn(commands.Action.DESTROY,fixture.native.commands)
        self.cleaned(fixture)
    async def test_current_cost_missing_wrong_kind_error_never_becomes_zero_or_forecast(self):
        for issue in ("missing","kind","error"):
            with self.subTest(issue=issue):
                fixture=Fixture()
                if issue=="missing":fixture.native.has_cost=False
                if issue=="kind":fixture.native.cost_kind=data.Kind.I64
                if issue=="error":fixture.native.cost_error=1168
                result=await fixture.read()
                self.assertIsNone(result["current_cost"]);self.assertTrue(result["current_cost_reason"])
                self.assertIsNone(result["estimated_cost"])
                self.assertTrue(result["can_enable"])
                self.cleaned(fixture)
    async def test_metadata_paging_and_missing_explicit_on_do_not_fall_back_to_toggle(self):
        fixture=Fixture();fixture.native.paginate_metadata=True
        result=await fixture.read()
        self.assertTrue(result["can_enable"])
        self.assertEqual(fixture.native.metadata_pages,[0,2])
        self.cleaned(fixture)
        fixture=Fixture();fixture.native.missing_on=True
        result=await fixture.read()
        self.assertIsNone(result["can_enable"])
        self.assertIn("ToggleSubsidiesOn",result["enable_reason"])
        self.assertNotIn("Building.ToggleSubsidies",fixture.native.evaluations)
        self.assertNotIn(commands.Action.SUBMIT,fixture.native.commands)
        self.cleaned(fixture)

    async def test_current_cost_preserves_signed_native_amount_exactly(self):
        fixture=Fixture();fixture.native.current_cost=-123456
        result=await fixture.read()
        self.assertEqual(result["current_cost"],Decimal("-1.23456"))
        self.assertEqual(result["current_cost_period"],"week")
        self.cleaned(fixture)
    async def test_control_or_ownership_mismatch_never_generates_or_submits(self):
        for issue in ("actor","owner"):
            with self.subTest(issue=issue):
                fixture=Fixture()
                if issue=="actor":fixture.native.player_actor=ACTOR+1
                else:fixture.native.state_owner=ACTOR+1
                result=await fixture.set(True)
                self.assertEqual(result["status"],"unavailable");self.assertIsNone(result["accepted"]);self.assertFalse(result["submitted"])
                self.assertFalse(fixture.native.commands)
                self.cleaned(fixture)
    async def test_serialized_toggle_mode_or_wrong_actor_is_not_sent_as_set(self):
        for issue in ("mode","actor"):
            with self.subTest(issue=issue):
                fixture=Fixture()
                if issue=="mode":fixture.native.wrong_mode=True
                else:fixture.native.actor_wire=ACTOR+1
                result=await fixture.set(True)
                self.assertEqual(result["status"],"unavailable")
                self.assertNotIn(commands.Action.SUBMIT,fixture.native.commands)
                self.assertEqual(fixture.native.commands.count(commands.Action.DESTROY),1)
                self.cleaned(fixture)
    async def test_fresh_precheck_already_desired_destroys_unsubmitted_owner_no_reverse_toggle(self):
        fixture=Fixture();fixture.native.drift_after_validate=True
        result=await fixture.set(True)
        self.assertEqual(result["status"],"unchanged");self.assertTrue(result["current"])
        self.assertIsNone(result["accepted"]);self.assertFalse(result["submitted"])
        self.assertTrue(result["effect_observed"])
        self.assertNotIn(commands.Action.SUBMIT,fixture.native.commands)
        self.assertEqual(fixture.native.commands.count(commands.Action.DESTROY),1)
        self.cleaned(fixture)
    async def test_accepted_readback_failure_preserves_real_receipt_without_replay(self):
        fixture=Fixture();fixture.native.fail_after_submit=True
        result=await fixture.set(True)
        self.assertTrue(result["accepted"]);self.assertEqual(result["status"],"accepted")
        self.assertIsNone(result["current"]);self.assertIn("observation_reason",result)
        self.assertEqual(fixture.native.native_action_count,1)
        self.cleaned(fixture)
    async def test_real_pending_submit_resumes_same_query_and_releases_core_slot(self):
        fixture=Fixture();fixture.ctx.timeout=0;fixture.native.hold_submit=True
        pending=None
        try:
            pending=await fixture.set(True)
            self.assertIsInstance(pending,PendingOperation)
            await asyncio.wait_for(fixture.native.held_event.wait(),1)
            original=fixture.ctx.pending_queries[0]
            self.assertEqual(fixture.native.native_action_count,0)
            commands_before=list(fixture.native.commands)
            duplicate=await helper.set_subsidy(fixture.ctx,BUILDING,True)
            reverse=await helper.set_subsidy(fixture.ctx,BUILDING,False)
            self.assertEqual(duplicate["status"],"original_handoff_retained")
            self.assertEqual(reverse["status"],"original_handoff_retained")
            self.assertEqual(fixture.native.commands,commands_before)
            self.assertIs(fixture.ctx.pending_queries[0],original)
            fixture.native.hold=False;fixture.native.finish_held()
            result=await pending.resume(timeout=1)
            self.assertTrue(result["effect_observed"])
            self.assertEqual(fixture.native.native_action_count,1)
            self.assertEqual(fixture.native.commands.count(commands.Action.SUBMIT),1)
            self.assertIsNotNone(original._result)
            self.cleaned(fixture)
        finally:
            # Settle the same callback/task even if an earlier assertion fails.
            fixture.native.hold_submit=False;fixture.native.hold=False
            if fixture.native.held is not None:fixture.native.finish_held()
            if isinstance(pending,PendingOperation):await pending.task
    async def test_unknown_handoff_exposes_native_result_without_readback_cleanup_or_replay(self):
        fixture=Fixture();fixture.native.submit_error=574;fixture.native.accepted=0
        result=await fixture.set(True)
        self.assertEqual(result["status"],"native_result")
        self.assertIsNone(result["accepted"]);self.assertIsNone(result["submitted"])
        self.assertTrue(result["submission_attempted"])
        self.assertIsNone(result["effect_observed"]);self.assertIsNone(result["current"])
        self.assertEqual(result["native_error"],574);self.assertEqual(result["native_outcome"],"unknown")
        self.assertEqual(fixture.native.native_action_count,1)
        self.assertEqual(fixture.native.commands.count(commands.Action.SUBMIT),1)
        self.assertNotIn(commands.Action.DESTROY,fixture.native.commands)
        retained=fixture.ctx._building_subsidy_runs
        self.assertEqual(len(retained),1)
        plan=retained[0]
        self.assertTrue(plan["pending"]["callback_completed"])
        self.assertTrue(plan["uncertain_operations"])
        self.assertTrue(plan["command_wire"])
        consumption=plan["consumptions"][-1]
        native_result=consumption["completed_result"]
        self.assertIs(result["native_result"],native_result)
        self.assertIs(native_result,plan["pending"]["result"])
        self.assertIs(native_result,consumption["original_query"]._result)
        self.assertEqual((native_result.error,native_result.accepted,native_result.command),(574,0,0))
        self.assertTrue(consumption["handoff_uncertain"])
        self.assertEqual(result["cleanup"]["status"],"retained_pending_or_unknown")
        self.assertIs(result["cleanup"],plan["cleanup"])
        self.assertEqual(fixture.native.evaluations.count("Building.IsSubsidized"),2)
        before=(len(fixture.native.queued),list(fixture.native.commands),list(fixture.native.evaluations),
                len(fixture.native.releases),native_result,plan["owner"],plan["producer_result"] if "producer_result" in plan else plan["values"][:])
        original_query=consumption["original_query"]
        for requested in (True,False):
            retained_result=await helper.set_subsidy(fixture.ctx,BUILDING,requested)
            self.assertEqual(retained_result["status"],"original_handoff_retained")
            self.assertIsNone(retained_result["accepted"])
            self.assertEqual(retained_result["original_requested"],True)
            self.assertEqual(retained_result["requested"],requested)
            self.assertIs(retained_result["native_result"],native_result)
            self.assertIs(retained_result["original_result"],result)
        read_again=await helper.read_subsidy(fixture.ctx,BUILDING)
        self.assertEqual(read_again["status"],"original_handoff_retained")
        self.assertIs(read_again["native_result"],native_result)
        after=(len(fixture.native.queued),list(fixture.native.commands),list(fixture.native.evaluations),
               len(fixture.native.releases),consumption["completed_result"],plan["owner"],plan["producer_result"] if "producer_result" in plan else plan["values"][:])
        self.assertEqual(after,before)
        self.assertIs(consumption["original_query"],original_query)
        self.assertIs(fixture.ctx._building_subsidy_runs[0],plan)

    async def test_no_submit_reports_no_native_acceptance_for_both_satisfied_intents(self):
        for desired in (True,False):
            fixture=Fixture();fixture.native.enabled["subsidies"]=desired
            result=await fixture.set(desired)
            self.assertEqual(result["status"],"unchanged")
            self.assertFalse(result["submitted"]);self.assertIsNone(result["accepted"])
            self.assertTrue(result["effect_observed"])
            self.assertFalse(fixture.native.commands)
            self.cleaned(fixture)

    async def test_unknown574_second_same_and_reverse_set_return_original_without_new_work(self):
        fixture=Fixture();fixture.native.submit_error=574;fixture.native.accepted=0
        result=await fixture.set(True)
        self.assertEqual(result["status"],"native_result")
        self.assertIsNone(result["accepted"])
        plan=fixture.ctx._building_subsidy_runs[0]
        original=plan["consumptions"][-1]
        native_result=original["completed_result"]
        self.assertIs(result["native_result"],native_result)
        owner=plan["owner"];source_handles=tuple(plan["values"])
        before=(len(fixture.native.queued),len(fixture.native.commands),len(fixture.native.evaluations),len(fixture.native.releases))
        for desired in (True,False):
            retained_result=await helper.set_subsidy(fixture.ctx,BUILDING,desired)
            self.assertEqual(retained_result["status"],"original_handoff_retained")
            self.assertIsNone(retained_result["accepted"])
            self.assertEqual(retained_result["original_requested"],True)
            self.assertIs(retained_result["native_result"],native_result)
        self.assertEqual(before,(len(fixture.native.queued),len(fixture.native.commands),len(fixture.native.evaluations),len(fixture.native.releases)))
        self.assertIs(plan["owner"],owner)
        self.assertEqual(tuple(plan["values"]),source_handles)
        self.assertIs(plan["consumptions"][-1],original)
        self.assertIs(original["completed_result"],native_result)
        self.assertIs(native_result,original["original_query"]._result)
        self.assertEqual((native_result.error,native_result.accepted,native_result.command),(574,0,0))
        self.assertEqual(fixture.native.commands.count(commands.Action.SUBMIT),1)
        self.assertEqual(fixture.native.commands.count(commands.Action.CLONE),1)

    async def test_cleanup_unknown_preserves_observed_eligibility_with_original_native_error(self):
        fixture=Fixture();fixture.native.destroy_error=574
        result=await fixture.read()
        self.assertEqual(result["status"],"observed")
        self.assertFalse(result["current"])
        self.assertTrue(result["can_enable"])
        self.assertIsNone(result["can_disable"])
        self.assertTrue(result["disable_reason"])
        self.assertEqual(result["current_cost"],Decimal("1.23456"))
        plan=fixture.ctx._building_subsidy_runs[0]
        self.assertIs(result["enable_validation"],plan["validation"])
        self.assertEqual(result["enable_validation"].validated,1)
        self.assertEqual(result["cleanup"]["status"],"retained_pending_or_unknown")
        self.assertTrue(result["cleanup_warning"])
        original=plan["consumptions"][-1]
        native_result=original["completed_result"]
        self.assertEqual((native_result.error,native_result.action),(574,commands.Action.DESTROY))
        self.assertIs(native_result,original["original_query"]._result)
        self.assertIn(native_result,result["cleanup"]["results"])
        self.assertEqual(original["native_outcome"],"unknown")
        self.assertEqual(fixture.native.commands.count(commands.Action.DESTROY),1)
        self.assertEqual(fixture.native.commands.count(commands.Action.CLONE),1)
        self.assertNotIn(commands.Action.SUBMIT,fixture.native.commands)
        self.assertNotIn("Building.ToggleSubsidiesOff",fixture.native.evaluations)
        self.assertFalse(fixture.native.releases)
        before=(len(fixture.native.queued),tuple(fixture.native.commands),tuple(fixture.native.evaluations))
        duplicate=await helper.read_subsidy(fixture.ctx,BUILDING)
        self.assertEqual(duplicate["status"],"original_handoff_retained")
        self.assertIs(duplicate["original_result"],result)
        self.assertEqual(before,(len(fixture.native.queued),tuple(fixture.native.commands),tuple(fixture.native.evaluations)))
        self.assertIs(fixture.ctx._building_subsidy_runs[0],plan)

    async def test_cleanup_native_error_does_not_mask_successful_submit_or_replay_release(self):
        fixture=Fixture();fixture.native.release_error=1168
        result=await fixture.set(True)
        self.assertEqual(result["status"],"observed")
        self.assertTrue(result["accepted"]);self.assertTrue(result["effect_observed"])
        self.assertTrue(result["current"])
        plan=fixture.ctx._building_subsidy_runs[0]
        self.assertIs(result["native_result"],plan["submission"])
        self.assertEqual(result["native_error"],0)
        self.assertEqual(result["cleanup"]["status"],"release_native_error")
        self.assertEqual(result["cleanup"]["results"][-1].error,1168)
        self.assertTrue(result["cleanup_warning"])
        self.assertTrue(plan["values"]);self.assertFalse(plan["safe_to_discard"])
        self.assertEqual(fixture.native.commands.count(commands.Action.SUBMIT),1)
        self.assertEqual(len(fixture.native.releases),1)
        before=(len(fixture.native.queued),tuple(fixture.native.commands),tuple(fixture.native.releases))
        duplicate=await helper.set_subsidy(fixture.ctx,BUILDING,False)
        self.assertEqual(duplicate["status"],"original_handoff_retained")
        self.assertIs(duplicate["original_result"],result)
        self.assertIs(duplicate["native_result"],plan["submission"])
        self.assertEqual(before,(len(fixture.native.queued),tuple(fixture.native.commands),tuple(fixture.native.releases)))

    async def test_public_ids_and_boolean_are_checked_before_native_work(self):
        fixture=Fixture()
        for value in (True,-1,0xffffffff,"7259"):
            with self.assertRaises(ValueError):await helper.read_subsidy(fixture.ctx,value)
        with self.assertRaises(ValueError):await helper.set_subsidy(fixture.ctx,BUILDING,1)
        self.assertFalse(fixture.native.queued)


if __name__=="__main__":unittest.main()
