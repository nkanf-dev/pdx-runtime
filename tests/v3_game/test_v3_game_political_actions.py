"""Once-only selected political command orchestration; CPU fixtures only."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from pdx_runtime.v3_game import politics, _political_actions as actions
from pdx_runtime.v3_game import _run as run_mod

class Owner:
    def __init__(self,ctx):self.ctx=ctx;self.pending=None;self.attempt=None;self.command=17;self.validations=0;self.submissions=0
    def validate(self,capacity=0):
        self.validations+=1;self.attempt=NS(outcome="unknown" if self.ctx.unknown_validation else "owned")
        return self.ctx.validation
    def submit(self,flags=4):
        self.submissions+=1
        if self.ctx.dispatch_error:raise OSError("dispatch uncertain")
        self.attempt=NS(outcome="unknown" if self.ctx.unknown_submit else "consumed");self.command=0
        return self.ctx.submission

class Run:
    made=[]
    owner_unsettled=staticmethod(run_mod.Run.owner_unsettled)
    command_result=run_mod.Run.command_result
    validate_submit=run_mod.Run.validate_submit
    finish=run_mod.Run.finish
    def __init__(self,ctx,label,namespace="politics"):
        self.ctx=ctx;self.namespace=namespace;self.actor=None;self.owners=[];self.operations=[];self.cleaned=0;self.status="working";Run.made.append(self)
        self.retained_scope=run_mod.scope_owner_pool(ctx);self.retained_scope.retain(self)
    async def generated_owner(self,expression,bindings):
        self.expression=expression;self.bindings=bindings
        if self.ctx.clone_error:
            self.unadopted_command=self.ctx.clone_error
            raise run_mod.KnownResultError("clone",self.ctx.clone_error)
        owner=Owner(self.ctx);self.owners.append(owner);return owner
    async def cleanup(self):
        self.cleaned+=1
        if self.ctx.cleanup_error:raise self.ctx.cleanup_error
        self.status="complete"
        self.retained_scope.discard(self)

class Context:
    def __init__(self,valid=True,accepted=True):
        self.validation=NS(error=0,validated=int(valid),reason="native reason")
        self.submission=NS(error=0,accepted=int(accepted));self.labels=[];self.performs=[]
        self.unknown_validation=False;self.unknown_submit=False;self.dispatch_error=False
        self.clone_error=None;self.cleanup_error=None
    async def query(self,label,factory,owner=None):self.labels.append(label);return factory()
    async def perform(self,label,fn):self.performs.append(label);return await fn()

class SelectedCommandTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self,ctx,**kw):
        api=NS(_ctx=ctx)
        with patch.object(run_mod,"Run",Run):
            result=await actions.selected_command(api,"chosen","State.AbandonColony",("native-binding",),**kw)
        return api,result,Run.made[-1]

    async def test_one_validation_one_submit_no_serialization(self):
        api,result,run=await self.invoke(Context())
        self.assertEqual((run.owners[0].validations,run.owners[0].submissions),(1,1))
        self.assertTrue(result["accepted"]);self.assertFalse(result["completed"])
        self.assertIs(result["validation"],api._ctx.validation)
        self.assertIs(result["submission"],api._ctx.submission)
        self.assertEqual(run.cleaned,1);self.assertEqual(list(api._ctx.owner_pool.snapshot()),[])

    async def test_native_rejection_never_submits(self):
        api,result,run=await self.invoke(Context(valid=False))
        self.assertEqual(run.owners[0].submissions,0);self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"],"native reason");self.assertEqual(run.cleaned,1)

    async def test_reason_truncation_does_not_repeat_validation(self):
        ctx=Context();ctx.validation.error=122
        _,result,run=await self.invoke(ctx)
        self.assertTrue(result["accepted"]);self.assertEqual(run.owners[0].validations,1)

    async def test_unknown_validation_preserves_result_and_owner_without_submit(self):
        ctx=Context();ctx.unknown_validation=True
        api,result,run=await self.invoke(ctx)
        self.assertIs(result["validation"],ctx.validation);self.assertEqual(run.owners[0].submissions,0)
        self.assertFalse(result["ownership_settled"]);self.assertEqual(run.cleaned,0)
        self.assertEqual(list(api._ctx.owner_pool.snapshot()),[run])

    async def test_accepted_native_result_survives_unknown_submit_ownership(self):
        ctx=Context();ctx.unknown_submit=True
        api,result,run=await self.invoke(ctx)
        self.assertTrue(result["accepted"]);self.assertIs(result["submission"],ctx.submission)
        self.assertFalse(result["ownership_settled"]);self.assertEqual(run.cleaned,0)

    async def test_unadopted_error_clone_retains_parent_graph(self):
        ctx=Context();ctx.clone_error=NS(error=8,cloned_command=42)
        api,result,run=await self.invoke(ctx)
        self.assertIs(result["native_result"],ctx.clone_error);self.assertFalse(result["ownership_settled"])
        self.assertEqual(run.cleaned,0)

    async def test_cleanup_warning_does_not_replace_accepted_result(self):
        ctx=Context();ctx.cleanup_error=OSError("cleanup")
        api,result,run=await self.invoke(ctx)
        self.assertTrue(result["accepted"]);self.assertEqual(result["cleanup"]["reason"],str(ctx.cleanup_error))
        self.assertEqual(list(api._ctx.owner_pool.snapshot()),[run])

    async def test_unknown_dispatch_not_replayed_or_cleaned(self):
        ctx=Context();ctx.dispatch_error=True;api=NS(_ctx=ctx)
        with patch.object(run_mod,"Run",Run),self.assertRaises(OSError):
            await actions.selected_command(api,"chosen","State.AbandonColony",("native-binding",))
        run=Run.made[-1];self.assertEqual(run.owners[0].submissions,1);self.assertEqual(run.cleaned,0)
        self.assertEqual(list(api._ctx.owner_pool.snapshot()),[run])

    async def test_perform_false_composes_under_caller_pending_owner(self):
        ctx=Context();_,_,_=await self.invoke(ctx,perform=False)
        self.assertEqual(ctx.performs,[])


class PropertyActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=NS(native=object(),process=object(),adapters={"data":"original-data"},world=0,diagnostics=[])
        self.results=[];self.calls=[];self.performs=[]
        async def query(label,factory,owner=None):
            self.calls.append(label);return factory()
        async def perform(label,fn):self.performs.append(label);return await fn()
        self.ctx.query=query;self.ctx.perform=perform
        self.api=NS(_ctx=self.ctx)

    async def test_selected_predicate_and_void_action_once(self):
        from pdx_runtime import v3_data as data
        condition=NS(error=0,kind=data.Kind.BOOL,value=True,action=data.Action.EVALUATE,value_handle=0)
        action=NS(error=0,invoked=1,accepted=1,value_handle=0,result=NS(action=data.Action.INVOKE_ACTION))
        with patch.object(data,"evaluate",return_value=condition) as evaluate,patch.object(data,"invoke_property_action",return_value=action) as invoke:
            result=await actions.selected_property_action(self.api,"character","CharacterInteraction.ExecuteCommand(Character.Self)",(),
                condition="CharacterInteraction.IsCommandValid(Character.Self)")
        self.assertEqual((evaluate.call_count,invoke.call_count),(1,1))
        self.assertTrue(result["invoked"]);self.assertTrue(result["accepted"]);self.assertFalse(result["completed"])
        self.assertIs(result["validation"],condition);self.assertIs(result["native_result"],action)

    async def test_selected_false_predicate_never_invokes(self):
        from pdx_runtime import v3_data as data
        condition=NS(error=0,kind=data.Kind.BOOL,value=False,action=data.Action.EVALUATE,value_handle=0)
        with patch.object(data,"evaluate",return_value=condition),patch.object(data,"invoke_property_action") as invoke:
            result=await actions.selected_property_action(self.api,"character","original.action",(),condition="original.condition")
        invoke.assert_not_called();self.assertFalse(result["accepted"])

    async def test_partial_invocation_error_preserves_invoked_and_acceptance(self):
        from pdx_runtime import v3_data as data
        condition=NS(error=0,kind=data.Kind.BOOL,value=True,action=data.Action.EVALUATE,value_handle=0)
        action=NS(error=122,invoked=1,accepted=1,value_handle=0,result=NS(action=data.Action.INVOKE_ACTION))
        with patch.object(data,"evaluate",return_value=condition),patch.object(data,"invoke_property_action",return_value=action):
            result=await actions.selected_property_action(self.api,"character","original.action",(),condition="original.condition")
        self.assertTrue(result["invoked"]);self.assertTrue(result["accepted"])
        self.assertEqual(result["native_error"],122);self.assertIs(result["native_result"],action)


class SelectedFactoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from pdx_runtime import v3_commands as commands
        self.commands=commands
        self.ctx=Context()
        self.ctx.native=object();self.ctx.process=object()
        self.ctx.adapters={"commands":"original-command-role","data":"original-data-role"}
        self.ctx.world=0;self.ctx.diagnostics=[]
        self.actor_reads=0
        async def country_id():self.actor_reads+=1;return 0x80000001
        self.ctx.country_id=country_id
        self.api=NS(_ctx=self.ctx)
        self.created=NS(error=0,command=17,world=0x4567,type_token=0x5a23,action=commands.Action.DESERIALIZE)
        self.owner=Owner(self.ctx);self.owner.world=self.created.world
        self.closed=[]
        async def cleanup(run):
            self.closed.append(run)
            run.owners.clear();run.values.clear();run.status="complete"
            run_mod.scope_owner_pool(run.ctx).discard(run)
        self.cleanup=cleanup

    async def invoke(self,parameters=None,**kwargs):
        parameters={0x30a6:(self.commands.UInt32(0),self.commands.UInt32(0x80000002))} if parameters is None else parameters
        with patch.object(self.commands,"create",return_value=self.created) as create,\
             patch.object(self.commands.NativeCommand,"from_result",return_value=self.owner) as adopt,\
             patch.object(run_mod.Run,"cleanup",self.cleanup):
            result=await actions.selected_factory(self.api,"reform",0x5a23,parameters,**kwargs)
        return result,create,adopt

    async def test_fresh_factory_once_actual_world_not_cached_world(self):
        result,create,adopt=await self.invoke()
        self.assertEqual(create.call_count,1);self.assertEqual(adopt.call_count,1)
        self.assertEqual((self.owner.validations,self.owner.submissions),(1,1))
        self.assertTrue(result["accepted"]);self.assertFalse(result["completed"])
        self.assertIs(result["creation"],self.created);self.assertEqual(result["actor_country_id"],0x80000001)
        self.assertEqual(create.call_args.kwargs["parameters"][0x30a6],(self.commands.UInt32(0),self.commands.UInt32(0x80000002)))
        self.assertEqual(self.actor_reads,1);self.assertEqual(len(self.closed),1)
        self.assertEqual(tuple(self.ctx.owner_pool.snapshot()),())

    async def test_selector_runs_in_same_graph_and_uses_explicit_zero_actor(self):
        seen=[]
        async def select(run):seen.append(run);return {0x30a6:()}
        result,create,_=await self.invoke(select,actor_id=0,perform=False)
        self.assertIs(self.closed[0],seen[0]);self.assertEqual(create.call_args.kwargs["actor_id"],0)
        self.assertEqual(self.actor_reads,0);self.assertEqual(self.ctx.performs,[])
        self.assertEqual(create.call_args.kwargs["parameters"],{0x30a6:()})

    async def test_native_create_error_preserves_partial_pointer_without_adoption(self):
        self.created.error=8
        result,create,adopt=await self.invoke(actor_id=0)
        adopt.assert_not_called();self.assertEqual((self.owner.validations,self.owner.submissions),(0,0))
        self.assertIs(result["native_result"],self.created);self.assertFalse(result["ownership_settled"])
        retained=tuple(self.ctx.owner_pool.snapshot());self.assertEqual(len(retained),1)
        self.assertIs(retained[0].unadopted_command,self.created);self.assertEqual(self.closed,[])

    async def test_native_create_error_without_pointer_can_finish_known_scope(self):
        self.created.error=87;self.created.command=0
        result,_,adopt=await self.invoke(actor_id=0)
        adopt.assert_not_called();self.assertTrue(result["ownership_settled"])
        self.assertEqual(result["native_error"],87);self.assertEqual(len(self.closed),1)

    async def test_validation_rejects_without_submit(self):
        self.ctx.validation.validated=0
        result,_,_=await self.invoke(actor_id=0)
        self.assertFalse(result["accepted"]);self.assertEqual((self.owner.validations,self.owner.submissions),(1,0))

    async def test_partial_acceptance_retains_original_pending_owner(self):
        self.ctx.unknown_submit=True;self.ctx.submission.error=122
        result,_,_=await self.invoke(actor_id=0)
        self.assertTrue(result["accepted"]);self.assertIs(result["submission"],self.ctx.submission)
        self.assertFalse(result["ownership_settled"]);self.assertEqual(self.closed,[])
        self.assertEqual(len(tuple(self.ctx.owner_pool.snapshot())),1)

    async def test_transport_error_retains_graph_and_does_not_recreate(self):
        with patch.object(self.commands,"create",side_effect=OSError("create uncertain")) as create,\
             patch.object(run_mod.Run,"cleanup",self.cleanup),self.assertRaises(OSError):
            await actions.selected_factory(self.api,"reform",0x5a23,{0x30a6:()},actor_id=0)
        self.assertEqual(create.call_count,1);self.assertEqual(self.closed,[])
        retained=tuple(self.ctx.owner_pool.snapshot());self.assertEqual(len(retained),1)
        self.assertTrue(retained[0].retain_unknown)

if __name__=="__main__":unittest.main()
