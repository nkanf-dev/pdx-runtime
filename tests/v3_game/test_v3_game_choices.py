"""Owned native choice graph/borrow contracts on genuine-value-shaped CPU fixtures."""
from types import SimpleNamespace as NS
import unittest
from pdx_runtime import v3_data as data
from pdx_runtime.v3_game._choices import OwnedChoices,ChoicesBusyError
from pdx_runtime.v3_game._run import Run as _Run

class Owner:
    wrapper=_Run.wrapper
    def __init__(self):
        self.ctx=NS(native=object(),process=object(),adapters={"data":123})
        self.native=self.ctx.native;self.process=self.ctx.process;self.adapters=dict(self.ctx.adapters)
        self.values=[8];self.value_adapters={8:self.adapters["data"]}
        self.status="working";self.calls=0;self.fail=False
    async def finish(self,result):
        self.calls+=1
        if self.fail:self.status="retained_pending_or_unknown";result["cleanup"]={"status":self.status}
        else:self.values.clear();self.status="complete"
        return result

def made(owner,*,rtti=None):
    obj=data.NativeObject(0x1000,5,8,rtti or ".?AU?$SAnyType@V?$CPdxPtr@VCDataParty@@@@@PdxAnyImpl@@")
    result=NS(value=obj,value_handle=8,error=0,type_id=5,native_type_name=obj.native_type_name)
    catalog=OwnedChoices(owner)
    return catalog,catalog.add(result,"Party",data.ObjectBinding(5,8,retained=True),{"name":"same","id":0}),result

class ChoicesTests(unittest.IsolatedAsyncioTestCase):
    async def test_choice_uses_actual_retained_binding_not_python_ordinal(self):
        owner=Owner();catalog,choice,result=made(owner)
        async with choice.borrow(owner.ctx) as lease:
            self.assertEqual(lease.binding.object,8);self.assertTrue(lease.binding.retained)
            self.assertIs(choice.native_result,result)
        await catalog.close();self.assertEqual(owner.calls,1)

    async def test_only_proven_ui_cpp_names_use_shared_exact_type_contract(self):
        cases=(("ReleaseCountryWindow","CReleaseCountryWindow"),
               ("PowerBlocCustomizationPopup","CPowerBlocCustomizationPopup"),
               ("MilitaryFormationChangeNamePopup","CMilitaryFormationNameChangePopup"))
        for registered,cpp_class in cases:
            owner=Owner();catalog=OwnedChoices(owner)
            rtti=".?AU?$SAnyType@V?$CPdxPtr@V"+cpp_class+"@@@@@PdxAnyImpl@@"
            result=NS(value=data.NativeObject(0x1000,5,8,rtti),value_handle=8,error=0,
                type_id=5,native_type_name=rtti)
            selected=catalog.add(result,registered,data.ObjectBinding(5,8,retained=True),{})
            self.assertEqual(selected.native_type,registered)
            await catalog.close()

    async def test_raw_game_class_spelling_is_not_an_owned_data_wrapper(self):
        owner=Owner();catalog=OwnedChoices(owner)
        rtti=".?AU?$SAnyType@V?$CPdxPtr@VCCountry@@@@@PdxAnyImpl@@"
        result=NS(value=data.NativeObject(0x1000,5,8,rtti),value_handle=8,error=0,
            type_id=5,native_type_name=rtti)
        with self.assertRaises(ValueError):catalog.add(result,"Country",data.ObjectBinding(5,8,retained=True),{})

    async def test_zero_visible_id_not_confused_with_handle(self):
        _,choice,_=made(Owner());self.assertEqual(choice["id"],0)
        self.assertEqual(choice._binding.object,8)

    async def test_visible_fields_are_readonly_and_not_identity_lookup(self):
        _,choice,_=made(Owner())
        with self.assertRaises(TypeError):choice["name"]="different"
        self.assertEqual(dict(choice),{"name":"same","id":0})

    async def test_raw_pointer_wrapper_rejected_even_with_nonzero_retained_handle(self):
        with self.assertRaises(ValueError):made(Owner(),rtti=".?AU?$SAnyType@PEAVCDataParty@@@PdxAnyImpl@@")

    async def test_const_borrowed_wrapper_not_retagged(self):
        with self.assertRaises(ValueError):made(Owner(),rtti=".?AU?$SAnyType@PEBVCDataParty@@@PdxAnyImpl@@")

    async def test_unowned_result_handle_rejected(self):
        owner=Owner();owner.values=[]
        with self.assertRaises(ValueError):made(owner)

    async def test_different_retained_binding_handle_rejected(self):
        owner=Owner();catalog,_,result=made(owner)
        with self.assertRaises(ValueError):catalog.add(result,"Party",data.ObjectBinding(5,9,retained=True),{})

    async def test_different_context_rejected_without_queries(self):
        owner=Owner();catalog,choice,_=made(owner)
        with self.assertRaises(ValueError):
            async with choice.borrow(NS()):pass
        self.assertEqual(owner.calls,0);self.assertEqual(catalog.active_leases,0)

    async def test_close_refuses_active_borrow_not_whole_workflow_lock(self):
        owner=Owner();catalog,choice,_=made(owner)
        async with choice.borrow(owner.ctx):
            with self.assertRaises(ChoicesBusyError):await catalog.close()
            self.assertEqual(owner.calls,0)
        await catalog.close();self.assertTrue(catalog.closed)

    async def test_pending_original_owner_holds_source_after_borrow_body_returns(self):
        owner=Owner();catalog,choice,_=made(owner);operation=NS(ctx=owner.ctx,status="working")
        async with choice.borrow(owner.ctx) as lease:lease.attach(operation)
        self.assertEqual(catalog.active_leases,1)
        with self.assertRaises(ChoicesBusyError):await catalog.close()
        operation.status="complete"
        self.assertTrue(lease.settle());await catalog.close();self.assertTrue(catalog.closed)

    async def test_unknown_exception_preserves_graph_until_original_owner_settles(self):
        owner=Owner();catalog,choice,_=made(owner);operation=NS(ctx=owner.ctx,status="working")
        with self.assertRaises(OSError):
            async with choice.borrow(owner.ctx) as lease:
                lease.attach(operation);raise OSError("original query unknown")
        with self.assertRaises(ChoicesBusyError):await catalog.close()
        operation.status="complete";await catalog.close();self.assertTrue(catalog.closed)

    async def test_cleanup_unknown_not_reported_closed_or_reborrowable(self):
        owner=Owner();catalog,choice,_=made(owner);owner.fail=True
        result=await catalog.close();self.assertFalse(result["closed"]);self.assertFalse(catalog.closed)
        with self.assertRaises(RuntimeError):
            async with choice.borrow(owner.ctx):pass
        self.assertEqual(owner.values,[8])

    async def test_close_idempotent_no_double_release(self):
        owner=Owner();catalog,choice,_=made(owner)
        first=await catalog.close();second=await catalog.close()
        self.assertIs(first,second);self.assertEqual(owner.calls,1)
        with self.assertRaises(RuntimeError):
            async with choice.borrow(owner.ctx):pass

    async def test_context_adapter_edits_do_not_change_catalog_resource_identity(self):
        owner=Owner();catalog,_,_=made(owner);owner.ctx.adapters["data"]=456
        self.assertEqual(catalog.adapters["data"],123)

    async def test_concurrent_close_joins_one_original_cleanup(self):
        import asyncio
        owner=Owner();catalog,_,_=made(owner);started=asyncio.Event();release=asyncio.Event()
        original=owner.finish
        async def delayed(result):
            started.set();await release.wait();return await original(result)
        owner.finish=delayed
        first=asyncio.create_task(catalog.close());await started.wait()
        second=asyncio.create_task(catalog.close());release.set()
        left,right=await asyncio.gather(first,second)
        self.assertIs(left,right);self.assertEqual(owner.calls,1)

    async def test_explicit_close_can_continue_known_cleanup_after_failure(self):
        owner=Owner();catalog,_,_=made(owner);owner.fail=True
        self.assertFalse((await catalog.close())["closed"])
        owner.fail=False
        self.assertTrue((await catalog.close())["closed"]);self.assertEqual(owner.calls,2)

    async def test_action_binds_actual_producer_adapter_not_changed_role_alias(self):
        owner=Owner();owner.value_adapters={8:0x1234000};catalog,choice,_=made(owner)
        owner.ctx.adapters["data"]="new.dll"
        operation=NS(ctx=owner.ctx,native=owner.native,process=owner.process,adapters={"data":"new.dll"},
                     value_adapters={},values=[],status="working")
        async with choice.borrow(owner.ctx) as lease:
            lease.attach(operation);self.assertEqual(operation.adapters["data"],0x1234000)
            operation.status="complete"
        await catalog.close()

    async def test_catalog_source_owner_cannot_consume_itself_as_action(self):
        owner=Owner();catalog,choice,_=made(owner)
        async with choice.borrow(owner.ctx) as lease:
            with self.assertRaises(ValueError):lease.attach(owner)
        await catalog.close()

if __name__=="__main__":unittest.main()
