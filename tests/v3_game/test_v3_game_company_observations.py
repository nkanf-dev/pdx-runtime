"""Offline native-ABI mock tests. Never attach to or launch a game process."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from decimal import Decimal
from pdx_runtime import v3_data as D
from pdx_runtime.v3_game.economy import company_observations as O


class Context:
    def __init__(self):
        self.native = object(); self.process = object(); self.world = 0x7000
        self.adapters = {"data": "data", "access": "access", "call": "call", "objects": "objects"}
        self.diagnostics = []; self.calls = []; self.types = []
    async def blocking(self, function, *args, **kwargs): return function(*args, **kwargs)
    async def refresh(self): self.calls.append("refresh")
    async def country_id(self): return 0x1000023
    async def type_id(self, name):
        self.types.append(name); return {"Country": 31, "Company": 44}[name]
    async def query(self, label, factory):
        self.calls.append(label)
        return factory().result


def result(*, error=0, handle=0, kind=D.Kind.EMPTY, value=None, total=0, native_type_name=""):
    return NS(error=error, value_handle=handle, kind=kind, value=value, total=total,
        native_type_name=native_type_name, world=0x7000)


def query(value): return NS(result=value)


def page(bindings=(), *, source=False, partial=False):
    if source:
        cells = (D.ProjectionCell(0, 0, 0, D.Kind.STRING, 44, 0, b"A", 1),) if bindings else ()
        return D.ProjectionPage(0, 0x7000, 1, 0, 17, len(bindings), True,
            len(bindings), cells)
    cells = []
    for row, binding in enumerate(bindings):
        for i, field in enumerate(O.FIELDS):
            if field.kind == "text":
                value = ("Alpha" if field.name == "name_no_formatting" else field.name).encode()
                kind = D.Kind.STRING; bits = 0
            else:
                kind = (D.Kind.FIXED if field.kind == "fixed" else
                    D.Kind.BOOL if field.kind == "boolean" else D.Kind.I32)
                bits = 0
                value = bits.to_bytes(8, "little")
            cells.append(D.ProjectionCell(row, i, 234 if partial and row == 0 and i == 0 else 0,
                kind, 44, bits, value, len(value) + int(partial and i == 0)))
    return D.ProjectionPage(0, 0x7000, len(O.FIELDS), 0, 17, len(bindings), True,
        len(bindings), tuple(cells))


def relations(*args, **kwargs):
    ids=args[2]
    records=tuple(NS(requested_id=full_id,resolved_id=full_id,error=0,
        country_id=0x1000023,country_error=0,company_type_pointer=0xABBA,
        company_type_error=0) for full_id in ids)
    return query(NS(error=0,world=0x7000,returned_count=len(ids),records=records,
        next_index=len(ids),truncated=False,value_handle=0))


class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_same_owner_bound_to_fields_not_source_ordinal(self):
        ctx = Context(); projected = []
        async def projected_page(ctx_, label, fields, bindings, **kw):
            projected.append((label, fields, bindings, kw))
            if kw.get("source_expression"):
                return D.ProjectionPage(0, 0x7000, 1, 0, 17, 1, True, 1,
                    (D.ProjectionCell(0, 0, 0, D.Kind.STRING, 44, 0, b"irrelevant", 10),))
            return page(bindings)
        native = D.NativeObject(0xAAA000, 44, 1002, "CPdxPtr@VCDataCompany@@")
        def evaluate(_, expression, bindings, **kw):
            self.assertEqual(expression, "Country.GetCompanies")
            return query(result(handle=1001, kind=D.Kind.LIST))
        def count(*args, **kw): return query(result(total=1))
        def element(*args, **kw): return query(result(handle=1002, kind=D.Kind.OBJECT,
            value=native, native_type_name=native.native_type_name))
        async def copied(*args): return 0xAA000002
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(O, "_projection_page", side_effect=projected_page), \
             patch.object(O, "_copy_owned_full_id", side_effect=copied), \
             patch.object(D, "evaluate", side_effect=evaluate), \
             patch.object(D, "list_count", side_effect=count), \
             patch.object(D, "list_element", side_effect=element), \
             patch.object(D, "release", side_effect=lambda *args, **kw: query(result())), \
             patch.object(O.objects,"read_ids", side_effect=relations):
            answer = await O.list_companies(ctx)
        row = answer["companies"][0]
        self.assertEqual(row["company_id"], 0xAA000002)
        self.assertEqual(row["country_id"], 0x1000023)
        self.assertEqual(row["profit"], Decimal(0))
        self.assertTrue(answer["complete"])
        self.assertTrue(answer["contract_complete"])
        self.assertEqual(row["establishment_date"], "establishment_date")
        self.assertEqual(ctx.types, ["Country", "Company"])
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0][1], tuple(field.expression for field in O.FIELDS))
        self.assertIn("Company.GetCompanyType.GetProsperityModifier.GetDesc",projected[0][1])
        self.assertIn("Company.GetTargetProsperityBreakdown",projected[0][1])
        self.assertIn("Company.GetTargetProsperityDesc",projected[0][1])
        self.assertIn("Company.GetProsperityChangeDesc",projected[0][1])
        self.assertIn("Company.IsProsperous",projected[0][1])
        self.assertEqual(projected[0][2], ((D.ObjectBinding(44, 1002, retained=True),),))
        self.assertNotIn("source_expression", projected[0][3])
        self.assertEqual(ctx.calls.count("economy.company.owned.element"), 1)
        self.assertEqual(ctx.calls.count("country.release"), 2)
        self.assertEqual(ctx.calls.count("economy.company.owned.source"), 1)

    async def test_zero_is_known_empty_not_an_error(self):
        ctx = Context()
        async def project(ctx_, label, fields, bindings, **kwargs):
            return D.ProjectionPage(0, ctx.world, 1, 0, 17, 0, True, 0, ())
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(O, "_projection_page", side_effect=project), \
             patch.object(D, "evaluate", return_value=query(result(handle=1001, kind=D.Kind.LIST))), \
             patch.object(D, "list_count", return_value=query(result(total=0))), \
             patch.object(D, "release", return_value=query(result())):
            answer = await O.list_companies(ctx)
        self.assertEqual(answer["native_count"], 0)
        self.assertEqual(answer["source_count"], 0)
        self.assertEqual(answer["next"], 0)
        self.assertTrue(answer["complete"])
        self.assertEqual(answer["companies"], [])
        self.assertEqual(ctx.types, ["Country"])

    async def test_original_source_index_does_not_join_identity_when_element_fails(self):
        ctx = Context()
        async def project(ctx_, label, fields, bindings, **kwargs):
            return D.ProjectionPage(0, ctx.world, 1, 0, 17, 1, True, 1,
                (D.ProjectionCell(0, 0, 0, D.Kind.STRING, 44, 0, b"A", 1),))
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(O, "_projection_page", side_effect=project), \
             patch.object(D, "evaluate", return_value=query(result(handle=1001, kind=D.Kind.LIST))), \
             patch.object(D, "list_count", return_value=query(result(total=1))), \
             patch.object(D, "list_element", return_value=query(result(error=234))), \
             patch.object(D, "release", return_value=query(result())):
            answer = await O.list_companies(ctx)
        self.assertFalse(answer["complete"])
        self.assertIsNone(answer["companies"][0]["company_id"])
        self.assertIn("retained", answer["companies"][0]["reasons"]["company_id"])

    async def test_bonus_field_sources_and_distinct_bool_definition_descriptions(self):
        expected={
            "is_prosperous": ("Company.IsProsperous", "boolean"),
            "definition_bonus_description":
                ("Company.GetCompanyType.GetProsperityModifier.GetDesc", "text"),
            "target_prosperity_breakdown":
                ("Company.GetTargetProsperityBreakdown", "text"),
            "target_prosperity_description": ("Company.GetTargetProsperityDesc", "text"),
            "prosperity_change_description": ("Company.GetProsperityChangeDesc", "text"),
        }
        self.assertEqual({f.name:(f.expression,f.kind) for f in O.FIELDS
            if f.name in expected},expected)
        self.assertEqual(len(O.FIELDS),21)
        self.assertFalse(any(f.name=="revenue_trend_value" for f in O.FIELDS))
        answer={"world":0x7000,"pages":[],"reasons":[]}
        owner=D.ObjectBinding(44,1002,retained=True)
        base=page(((owner,),))
        bool_index=next(i for i,f in enumerate(O.FIELDS) if f.name=="is_prosperous")
        from dataclasses import replace
        native_cells=tuple(replace(cell,raw_bits=1)
            if cell.field==bool_index else cell for cell in base.cells)
        row={"company_id":77,"country_id":6,"country_relation_complete":True,
            "values":{},"observations":{},"reasons":{},"complete":False}
        O._apply_page(answer,replace(base,cells=native_cells),[row])
        self.assertTrue(row["is_prosperous"])
        self.assertEqual(row["definition_bonus_description"],"definition_bonus_description")
        self.assertEqual(row["target_prosperity_breakdown"],"target_prosperity_breakdown")
        self.assertTrue(row["complete"])
        false_row={"company_id":77,"country_id":6,"country_relation_complete":True,
            "values":{},"observations":{},"reasons":{},"complete":False}
        # Native bool can carry its zero in raw_bits with no text snapshot.
        empty_bool_cells=tuple(replace(cell,snapshot=b"",total=0,raw_bits=0)
            if cell.field==bool_index else cell for cell in base.cells)
        O._apply_page(answer,replace(base,cells=empty_bool_cells),[false_row])
        self.assertFalse(false_row["is_prosperous"])
        self.assertEqual(false_row["definition_bonus_description"],"definition_bonus_description")
        self.assertTrue(false_row["complete"])

    async def test_native_bonus_error_keeps_flag_and_other_descriptions(self):
        from dataclasses import replace
        owner=D.ObjectBinding(44,1002,retained=True)
        base=page(((owner,),))
        bonus_index=next(i for i,f in enumerate(O.FIELDS) if f.name=="definition_bonus_description")
        cells=tuple(replace(cell,error=234) if cell.field==bonus_index else cell
            for cell in base.cells)
        answer={"world":0x7000,"pages":[],"reasons":[]}
        row={"company_id":77,"country_id":6,"country_relation_complete":True,
            "values":{},"observations":{},"reasons":{},"complete":False}
        O._apply_page(answer,replace(base,cells=cells),[row])
        self.assertFalse(row["is_prosperous"])
        self.assertEqual(row["definition_bonus_description"],"definition_bonus_description")
        self.assertTrue(row["observations"]["definition_bonus_description"]["available"])
        self.assertEqual(row["observations"]["definition_bonus_description"]["error"],234)
        self.assertEqual(row["target_prosperity_description"],"target_prosperity_description")
        self.assertFalse(row["complete"])

    async def test_partial_getter_keeps_real_id_zero_and_native_error(self):
        answer={"world":0x7000,"pages":[],"reasons":[]}
        row={"company_id":0xAA000004,"provider_index":0,"values":{},
            "observations":{},"reasons":{},"complete":False}
        O._apply_page(answer, page(((D.ObjectBinding(44,1002,retained=True),),),partial=True),[row])
        self.assertEqual(row["company_id"],0xAA000004)
        self.assertEqual(row["profit"],Decimal(0))
        self.assertFalse(row["complete"])
        self.assertEqual(row["observations"]["name"]["error"],234)
        self.assertTrue(row["observations"]["name"]["available"])
        self.assertIn("234",row["reasons"]["name"])

    async def test_uncertain_projection_keeps_original_owner_and_query(self):
        ctx=Context()
        native=D.NativeObject(0xAAA000,44,1002,"CPdxPtr@VCDataCompany@@")
        def evaluate(*args,**kwargs):return query(result(handle=1001,kind=D.Kind.LIST))
        def element(*args,**kwargs):return query(result(handle=1002,kind=D.Kind.OBJECT,
            value=native,native_type_name=native.native_type_name))
        original=NS(partial_result=None)
        async def failed(*args,**kwargs):
            error=RuntimeError("original projection query uncertain")
            error.query=original
            raise error
        async def copied(*args):return 0xAA000002
        with patch.object(O,"_projection_supported",return_value=True), \
             patch.object(O,"_projection_page",side_effect=failed), \
             patch.object(O,"_copy_owned_full_id",side_effect=copied), \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"list_count",return_value=query(result(total=1))), \
             patch.object(D,"list_element",side_effect=element), \
             patch.object(D,"release") as release, \
             patch.object(O.objects,"read_ids",side_effect=relations):
            with self.assertRaisesRegex(RuntimeError,"uncertain") as caught:
                await O.list_companies(ctx)
        self.assertIs(caught.exception.query,original)
        self.assertEqual(caught.exception.company_observations["companies"][0]["company_id"],0xAA000002)
        self.assertEqual(len(ctx._company_observation_reads),1)
        self.assertEqual(set(ctx._company_observation_reads[0].owners),{1001,1002})
        release.assert_not_called()

    async def test_default_reads_all_pages_explicit_budget_reports_truncation(self):
        async def run(max_pages):
            ctx=Context(); copied_pages=[]
            def element(_, handle, index, **opts):
                native=D.NativeObject(0xAAA000+index,44,2000+index,
                    "CPdxPtr@VCDataCompany@@")
                return query(result(handle=native.value_handle,kind=D.Kind.OBJECT,
                    value=native,native_type_name=native.native_type_name))
            async def project(ctx_, label, fields, bindings, **kw):
                copied_pages.append(len(bindings))
                return page(bindings)
            async def copied(ctx_, read, pointer): return pointer-0xAAA000+1
            with patch.object(O,"_projection_supported",return_value=True), \
                 patch.object(O,"_projection_page",side_effect=project), \
                 patch.object(O,"_copy_owned_full_id",side_effect=copied), \
                 patch.object(D,"evaluate",return_value=query(result(handle=1001,kind=D.Kind.LIST))), \
                 patch.object(D,"list_count",return_value=query(result(total=65))), \
                 patch.object(D,"list_element",side_effect=element), \
                 patch.object(D,"release",return_value=query(result())), \
                 patch.object(O.objects,"read_ids",side_effect=relations):
                answer=await O.list_companies(ctx,max_pages=max_pages)
            return answer,copied_pages
        full,pages=await run(None)
        self.assertEqual((full["native_count"],full["next"],pages),(65,65,[64,1]))
        self.assertTrue(full["complete"])
        short,pages=await run(1)
        self.assertEqual((short["native_count"],short["next"],pages),(65,64,[64]))
        self.assertTrue(short["truncated"])
        self.assertFalse(short["complete"])
        self.assertIn("next=64 total=65",short["reasons"][0])

    async def test_old_projection_export_reads_fields_from_same_retained_owner(self):
        ctx=Context()
        native=D.NativeObject(0xAAA000,44,1002,"CPdxPtr@VCDataCompany@@")
        inspected=[]
        def evaluate(_,expression,bindings,**opts):
            if expression=="Country.GetCompanies":
                return query(result(handle=1001,kind=D.Kind.LIST))
            inspected.append((expression,bindings[0]))
            field=next(field for field in O.FIELDS if field.expression==expression)
            value=("Original" if field.kind=="text" else 0)
            kind=(D.Kind.STRING if field.kind=="text" else
                D.Kind.FIXED if field.kind=="fixed" else
                D.Kind.BOOL if field.kind=="boolean" else D.Kind.I32)
            raw=value.encode() if field.kind=="text" else (0).to_bytes(8,"little")
            return query(NS(error=0,kind=kind,type_id=44,raw_bits=0,
                snapshot=raw,snapshot_bytes=len(raw),total=len(raw),value=value,value_handle=0))
        async def copied(*args):return 99
        with patch.object(O,"_projection_supported",return_value=False), \
             patch.object(O,"_projection_page") as page_call, \
             patch.object(O,"_copy_owned_full_id",side_effect=copied), \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"list_count",return_value=query(result(total=1))), \
             patch.object(D,"list_element",return_value=query(result(handle=1002,kind=D.Kind.OBJECT,
                 value=native,native_type_name=native.native_type_name))), \
             patch.object(D,"release",return_value=query(result())), \
             patch.object(O.objects,"read_ids",side_effect=relations):
            answer=await O.list_companies(ctx)
        page_call.assert_not_called()
        self.assertTrue(answer["complete"])
        self.assertFalse(answer["projection_supported"])
        self.assertEqual(answer["companies"][0]["company_id"],99)
        self.assertEqual(len(inspected),len(O.FIELDS))
        self.assertTrue(all(binding==D.ObjectBinding(44,1002,retained=True)
            for _,binding in inspected))

    async def test_native_copy_uses_one_inline_payload_and_proven_offset(self):
        import struct
        class Process:
            def resolve_export(self,dll,name):return 0xF0000
        ctx=Context();ctx.process=Process(); captured=[]
        class Read:
            async def query(self,label,factory):return factory().result
        def access(client,callback,initial,**kw):
            self.assertEqual(callback,0xE0000)
            offset=O.native_call.REQUEST.size+3*8
            prepared=kw["prepare_payload"](0xB100)
            args=struct.unpack_from("<3Q",prepared,O.native_call.REQUEST.size)
            captured.append((args,kw,prepared))
            self.assertEqual(args,(0xB100+offset,0xABC010,4))
            self.assertEqual(kw["decode_payload"](prepared).target,0xF0000)
            returned=prepared[:offset]+(0xAA000002).to_bytes(4,"little")
            return query(NS(error=0,opaque=0xB100,payload=returned,
                callee_result=NS(error=0,rax=0xB100+offset)))
        with patch.object(O,"resolve_v3_export",return_value=0xE0000), \
             patch.object(O.v3_access,"submit_callback",side_effect=access):
            read=Read()
            self.assertEqual(await O._copy_owned_full_id(ctx,read,0xABC000),0xAA000002)
            self.assertEqual(await O._copy_owned_full_id(ctx,read,0xABC000),0xAA000002)
        self.assertEqual(len(captured),2)
        self.assertEqual(captured[0][1]["dispatch"],"interface")
        self.assertEqual(ctx._company_observation_memcpy,0xF0000)
        self.assertEqual(ctx._company_observation_call,0xE0000)

    async def test_optional_performance_error_preserves_complete_core_list(self):
        from dataclasses import replace
        ctx=Context()
        native=D.NativeObject(0xAAA000,44,1002,"CPdxPtr@VCDataCompany@@")
        async def project(ctx_,label,fields,bindings,**kw):
            original=page(bindings)
            index=next(i for i,field in enumerate(O.FIELDS) if field.name=="profit")
            cells=tuple(replace(cell,error=234) if cell.field==index else cell
                for cell in original.cells)
            return replace(original,cells=cells)
        async def copied(*args):return 7
        with patch.object(O,"_projection_supported",return_value=True), \
             patch.object(O,"_projection_page",side_effect=project), \
             patch.object(O,"_copy_owned_full_id",side_effect=copied), \
             patch.object(D,"evaluate",return_value=query(result(handle=1001,kind=D.Kind.LIST))), \
             patch.object(D,"list_count",return_value=query(result(total=1))), \
             patch.object(D,"list_element",return_value=query(result(handle=1002,kind=D.Kind.OBJECT,
                 value=native,native_type_name=native.native_type_name))), \
             patch.object(D,"release",return_value=query(result())), \
             patch.object(O.objects,"read_ids",side_effect=relations):
            answer=await O.list_companies(ctx)
        self.assertTrue(answer["contract_complete"])
        self.assertFalse(answer["complete"])
        self.assertEqual(answer["companies"][0]["company_id"],7)
        self.assertEqual(answer["companies"][0]["observations"]["profit"]["error"],234)

    async def test_partial_typed_relation_page_preserves_first_of_two(self):
        ctx=Context()
        batch=[{"company_id":2,"reasons":{}},{"company_id":3,"reasons":{}}]
        answer={"world":ctx.world,"reasons":[]}
        first=NS(requested_id=2,resolved_id=2,error=0,country_id=42,
            country_error=0,company_type_pointer=0xABBA,company_type_error=0)
        async def fake_query(label,factory):return NS(
            error=0,world=ctx.world,truncated=True,returned_count=1,records=(first,),
            next_index=1,value_handle=0)
        with patch.object(O.objects,"read_ids"):
            await O._company_object_relations(ctx,answer,NS(query=fake_query),batch)
        self.assertEqual(batch[0]["country_id"],42)
        self.assertEqual(batch[0]["native_company"]["company_type_pointer"],0xABBA)
        self.assertFalse(batch[0]["country_relation_complete"])
        self.assertIsNone(batch[1].get("country_id"))
        self.assertIn("missing",batch[1]["reasons"]["country_id"])
        self.assertTrue(answer["reasons"])

    async def test_header_error_does_not_erase_valid_relation_rows(self):
        ctx=Context()
        batch=[{"company_id":2,"reasons":{}},{"company_id":3,"reasons":{}}]
        answer={"world":ctx.world,"reasons":[]}
        def native(full_id):return NS(requested_id=full_id,resolved_id=full_id,
            error=0,country_id=42+full_id,country_error=0,
            company_type_pointer=0xBA00+full_id,company_type_error=0)
        async def fake_query(label,factory):return NS(error=234,world=ctx.world,
            truncated=False,returned_count=2,records=(native(3),native(2)),
            next_index=2,value_handle=0)
        with patch.object(O.objects,"read_ids"):
            await O._company_object_relations(ctx,answer,NS(query=fake_query),batch)
        self.assertEqual([record["country_id"] for record in batch],[44,45])
        self.assertEqual(batch[0]["native_company"]["error"],0)
        self.assertEqual(answer["object_pages"][0]["error"],234)
        self.assertFalse(batch[1]["country_relation_complete"])

    async def test_world_change_keeps_copied_relation_but_stops_old_owner_projection(self):
        ctx=Context();batch=[{"company_id":2,"reasons":{}}]
        answer={"world":ctx.world,"reasons":[]}
        native=NS(requested_id=2,resolved_id=2,error=0,country_id=42,
            country_error=0,company_type_pointer=0xABBA,company_type_error=0)
        async def fake_query(label,factory):return NS(error=0,world=0x7001,
            truncated=False,returned_count=1,records=(native,),next_index=1,value_handle=0)
        reader=NS(query=fake_query,uncertain=False)
        with patch.object(O.objects,"read_ids"):
            with self.assertRaisesRegex(RuntimeError,"world changed"):
                await O._company_object_relations(ctx,answer,reader,batch)
        self.assertTrue(reader.uncertain)
        self.assertEqual(batch[0]["native_company"]["country_id"],42)
        self.assertEqual(batch[0]["country_id"],42)
        self.assertFalse(batch[0]["country_relation_complete"])

    async def test_source_error_with_usable_owned_list_still_reads_rows(self):
        ctx=Context()
        native=D.NativeObject(0xAAA000,44,1002,"CPdxPtr@VCDataCompany@@")
        async def project(ctx_,label,fields,bindings,**kw):return page(bindings)
        async def copied(*args):return 7
        with patch.object(O,"_projection_supported",return_value=True), \
             patch.object(O,"_projection_page",side_effect=project), \
             patch.object(O,"_copy_owned_full_id",side_effect=copied), \
             patch.object(D,"evaluate",return_value=query(result(error=234,handle=1001,kind=D.Kind.LIST))), \
             patch.object(D,"list_count",return_value=query(result(total=1))) as count, \
             patch.object(D,"list_element",return_value=query(result(handle=1002,kind=D.Kind.OBJECT,
                 value=native,native_type_name=native.native_type_name))) as element, \
             patch.object(D,"release",return_value=query(result())), \
             patch.object(O.objects,"read_ids",side_effect=relations):
            answer=await O.list_companies(ctx)
        count.assert_called_once(); element.assert_called_once()
        self.assertEqual(answer["companies"][0]["company_id"],7)
        self.assertEqual(answer["owned_source"]["error"],234)
        self.assertTrue(any("234" in reason for reason in answer["reasons"]))
        self.assertFalse(answer["contract_complete"])

    async def test_cleanup_warning_retains_core_values_and_completeness(self):
        ctx=Context()
        native=D.NativeObject(0xAAA000,44,1002,"CPdxPtr@VCDataCompany@@")
        async def project(ctx_,label,fields,bindings,**kw):return page(bindings)
        async def copied(*args):return 7
        with patch.object(O,"_projection_supported",return_value=True), \
             patch.object(O,"_projection_page",side_effect=project), \
             patch.object(O,"_copy_owned_full_id",side_effect=copied), \
             patch.object(D,"evaluate",return_value=query(result(handle=1001,kind=D.Kind.LIST))), \
             patch.object(D,"list_count",return_value=query(result(total=1))), \
             patch.object(D,"list_element",return_value=query(result(handle=1002,kind=D.Kind.OBJECT,
                 value=native,native_type_name=native.native_type_name))), \
             patch.object(D,"release",return_value=query(result(error=234))), \
             patch.object(O.objects,"read_ids",side_effect=relations):
            answer=await O.list_companies(ctx)
        self.assertTrue(answer["complete"])
        self.assertTrue(answer["contract_complete"])
        self.assertEqual(answer["companies"][0]["company_id"],7)
        self.assertIn("234",answer["cleanup_warning"])
        self.assertTrue(ctx._company_observation_reads)


    async def test_real_access_decoder_short_tail_raises_before_result_creation(self):
        import struct
        from pdx_runtime import v3_access
        offset=O.native_call.REQUEST.size+3*8
        output=0xB100+offset
        payload=bytearray(O.native_call.encode_request(0x9000,
            (output,0xABC010,4),request_address=0xB100)+bytes(2))
        struct.pack_into("<I",payload,4,0)
        outer=bytearray(v3_access.encode_request(0xE000,0xB100,0xC000,
            mode=1,flag=0))
        struct.pack_into("<I",outer,4,0)
        with self.assertRaisesRegex(ValueError,"short") as caught:
            v3_access.AccessResult.from_bytes(bytes(outer)+bytes(payload),
                payload_size=offset+4,decode_payload=O._decode_company_copy)
        self.assertEqual(caught.exception.partial_bytes,b"\x00\x00")
        self.assertIsNotNone(caught.exception.native_call_result)
        self.assertEqual(caught.exception.native_call_result.target,0x9000)

    async def test_short_inline_decoder_keeps_original_result_and_owner(self):
        class Process:
            def resolve_export(self,*args):return 0x9000
        ctx=Context();ctx.process=Process()
        class Read:
            uncertain=False
            async def query(self,label,factory):return factory().result
        offset=O.native_call.REQUEST.size+3*8
        original=NS(error=0,opaque=0xB100,payload=bytes(offset)+b"\x08\x07",
            callee_result=NS(error=0,rax=0xB100+offset))
        read=Read()
        with patch.object(O,"resolve_v3_export",return_value=0xE000), \
             patch.object(O.v3_access,"submit_callback",return_value=query(original)):
            with self.assertRaisesRegex(RuntimeError,"short"):
                await O._copy_owned_full_id(ctx,read,0xAABB)
        self.assertTrue(read.uncertain)
        self.assertIs(ctx.diagnostics[0]["native_result"],original)
        self.assertEqual(ctx.diagnostics[0]["partial_bytes"],b"\x08\x07")

    async def test_rax_mismatch_is_diagnostic_not_id_gate(self):
        class Process:
            def resolve_export(self,*args):return 0x9000
        ctx=Context();ctx.process=Process()
        class Read:
            async def query(self,label,factory):return factory().result
        offset=O.native_call.REQUEST.size+3*8
        original=NS(error=0,opaque=0xB100,payload=bytes(offset)+(123).to_bytes(4,"little"),
            callee_result=NS(error=0,rax=0))
        with patch.object(O,"resolve_v3_export",return_value=0xE000), \
             patch.object(O.v3_access,"submit_callback",return_value=query(original)):
            value=await O._copy_owned_full_id(ctx,Read(),0xAABB)
        self.assertEqual(value,123)
        self.assertIs(ctx.diagnostics[0]["native_result"],original)
        self.assertEqual(ctx.diagnostics[0]["copied_id"],123)


class CompanyTypeCopiedCatalog(unittest.TestCase):
    """Copied DTO tests only. No SourceKind2 enum/serializer/API is invoked."""
    def cell(self, index, text=b"", *, error=0, total=None, kind=D.Kind.STRING, bits=0):
        return D.ProjectionCell(index,0,error,kind,42,bits,text,
            len(text) if total is None else total,2,D.Kind.OBJECT,
            ".?AU?$SAnyType@V?$CPdxPtr@VCDataCompanyType@@@@@PdxAnyImpl@@",0)

    def page(self, count, cells=(), *, error=0, count_valid=True, rows=None, world=0):
        return D.ProjectionPage(error,world,1,0,17,count,count_valid,
            count if rows is None else rows,tuple(cells))

    def test_all_positions_over64_and_empty_native_key_are_not_clipped_or_filtered(self):
        cells=[self.cell(index,b"" if index==2 else ("company_"+str(index)).encode())
            for index in range(70)]
        native=self.page(70,cells)
        self.assertFalse(native.range_complete)  # Ordinary copied-Page default.
        answer=O.decode_company_type_catalog(native)
        self.assertTrue(answer["complete"]);self.assertTrue(answer["positions_complete"])
        self.assertEqual(answer["source_count"],70)
        self.assertEqual(len(answer["rows"]),70)
        self.assertEqual([row["source_index"] for row in answer["rows"]],list(range(70)))
        self.assertEqual(answer["rows"][2]["key"],"")
        self.assertTrue(answer["rows"][2]["observations"]["key"]["available"])
        self.assertEqual(answer["rows"][-1]["key"],"company_69")
        self.assertNotIn("id",answer["rows"][0])
        self.assertEqual(answer["missing_position_ranges"],())

    def test_real_empty_and_unavailable_invalid_count_are_distinct(self):
        empty=O.decode_company_type_catalog(self.page(0))
        self.assertTrue(empty["complete"]);self.assertEqual(empty["source_count"],0)
        self.assertEqual(empty["world"],0)
        for raw_count in (0,0xFFFFFFFF):
            failed=O.decode_company_type_catalog(self.page(raw_count,error=21,count_valid=False,rows=0))
            self.assertFalse(failed["complete"])
            self.assertIsNone(failed["source_count"])
            self.assertEqual(failed["source_count_raw"],raw_count)
            self.assertEqual(failed["native_error"],21)
            self.assertEqual(failed["rows"],[])
            self.assertIsNone(failed["missing_position_ranges"])

    def test_null_error_and_partial_key_prefix_keep_original_positions_and_bytes(self):
        native=self.page(5,(self.cell(0,b""),
            self.cell(1,error=87,kind=D.Kind.EMPTY),
            self.cell(3,b"prefix",error=234,total=20)))
        answer=O.decode_company_type_catalog(native)
        self.assertFalse(answer["complete"]);self.assertFalse(answer["positions_complete"])
        self.assertEqual([row["source_index"] for row in answer["rows"]],[0,1,3])
        self.assertTrue(answer["rows"][0]["complete"])
        self.assertIsNone(answer["rows"][1]["key"])
        self.assertEqual(answer["rows"][1]["observations"]["key"]["error"],87)
        self.assertEqual(answer["rows"][2]["key"],"prefix")
        self.assertEqual(answer["rows"][2]["key_bytes"],b"prefix")
        self.assertEqual(answer["rows"][2]["observations"]["key"]["total"],20)
        self.assertEqual(answer["missing_position_ranges"],((2,3),(4,5)))
        self.assertIs(answer["raw_cells"],native.cells)

    def test_duplicate_and_out_of_range_cells_are_not_silently_joined_or_erased(self):
        native=self.page(2,(self.cell(0,b"first"),self.cell(0,b"second"),self.cell(3,b"outside")))
        answer=O.decode_company_type_catalog(native)
        self.assertFalse(answer["complete"])
        self.assertEqual(answer["rows"][0]["key"],"first")
        self.assertEqual([cell.snapshot for cell in answer["rows"][0]["raw_cells"]],[b"first",b"second"])
        self.assertIn("_duplicate_key_cell",answer["rows"][0]["reasons"])
        self.assertEqual(answer["rows"][1]["key"],"outside")
        self.assertFalse(answer["rows"][1]["position_valid"])
        self.assertEqual(answer["missing_position_ranges"],((1,2),))

    def test_false_none_invalid_utf8_and_late_native_error_preserve_raw_values(self):
        native=self.page(3,(self.cell(0,b"\0",kind=D.Kind.BOOL,bits=0),
            self.cell(1,kind=D.Kind.EMPTY),self.cell(2,b"\xff")),error=5)
        answer=O.decode_company_type_catalog(native)
        self.assertFalse(answer["complete"])
        self.assertIs(answer["rows"][0]["observations"]["key"]["native_value"],False)
        self.assertIsNone(answer["rows"][1]["observations"]["key"]["native_value"])
        self.assertEqual(answer["rows"][2]["key_bytes"],b"\xff")
        self.assertEqual(answer["rows"][2]["key"],"\ufffd")
        self.assertEqual(answer["native_error"],5)
        self.assertIn("invalid UTF-8",answer["rows"][2]["reasons"]["key"])


class FullCompanyTypesConsumer(unittest.IsolatedAsyncioTestCase):
    def context(self):
        from pdx_runtime.query_executor import Diagnostics
        ctx=Context();ctx.queries=[];ctx.operations=[];ctx.remembered=[]
        ctx.diagnostics=Diagnostics()
        async def forbidden(*args,**kwargs):raise AssertionError("no prewarm/world/country/type gate")
        async def perform(label,work):ctx.operations.append(label);return await work()
        async def query(label,factory):ctx.queries.append(label);return factory().result
        ctx.refresh=forbidden;ctx.country_id=forbidden;ctx.type_id=forbidden
        ctx.perform=perform;ctx.query=query;ctx._remember=lambda q:ctx.remembered.append(q)
        return ctx

    async def test_direct_real_native_source_kind2_empty_rootrow_explicit_zero_limit_all70(self):
        from pdx_runtime.v3_game.economy import EconomyAPI
        ctx=self.context();factory=CompanyTypeCopiedCatalog()
        native=factory.page(70,[factory.cell(i,("company_"+str(i)).encode()) for i in range(70)])
        with patch.object(D,"project_page",return_value=NS(result=native)) as sent:
            result=await EconomyAPI(ctx).company_types()
        self.assertTrue(result["complete"]);self.assertEqual(len(result["rows"]),70)
        self.assertEqual(ctx.operations,["economy.company_types"])
        self.assertEqual(ctx.queries,["economy.company_types"])
        self.assertEqual(sent.call_args.args[1],("CompanyType.GetKey",))
        self.assertEqual(sent.call_args.args[2],((),))
        source=sent.call_args.kwargs["source_expression"]
        self.assertIsInstance(source,D.NativeSource)
        self.assertEqual(source.kind,D.NativeSourceKind.FULL_COMPANY_TYPES)
        self.assertEqual((sent.call_args.kwargs["start"],sent.call_args.kwargs["limit"]),(0,0))

    async def test_original_missing_transport_or_partial_copy_not_empty_success_or_replay(self):
        from pdx_runtime.v3_game.economy import EconomyAPI
        factory=CompanyTypeCopiedCatalog()
        original=NS(partial_result=factory.page(3,[factory.cell(0,b"copied")]))
        for partial in (None,original):
            ctx=self.context();failure=OSError("original full source unavailable or pending")
            if partial is not None:failure.query=partial
            with patch.object(D,"project_page",side_effect=failure) as sent:
                with self.assertRaises(OSError) as caught:await EconomyAPI(ctx).company_types()
            self.assertIs(caught.exception,failure);self.assertEqual(sent.call_count,1)
            result=failure.company_type_catalog
            self.assertFalse(result["complete"])
            if partial is None:self.assertIsNone(result["source_count"])
            else:
                self.assertIs(caught.exception.query,original)
                self.assertEqual(result["rows"][0]["key"],"copied")
                self.assertEqual(result["missing_position_ranges"],((1,3),))

    async def test_cleanup_warning_and_complete_native_key_are_separate(self):
        from pdx_runtime.v3_game.economy import EconomyAPI
        ctx=self.context();factory=CompanyTypeCopiedCatalog()
        native=factory.page(1,[factory.cell(0,b"company")]);warning=OSError("cleanup warning")
        original=NS(result=native,cleanup_error=warning)
        with patch.object(D,"project_page",return_value=original):
            result=await EconomyAPI(ctx).company_types()
        self.assertTrue(result["complete"])
        self.assertEqual(result["rows"][0]["key"],"company")
        self.assertEqual(ctx.remembered,[original])
        self.assertIs(result["cleanup_warnings"][0]["cleanup_error"],warning)


if __name__ == "__main__": unittest.main()
