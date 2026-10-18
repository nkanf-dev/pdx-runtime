"""CPU tests for shared copied country-field reads; no game process."""
from decimal import Decimal, localcontext
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as D
from pdx_runtime.v3_game import _read as O


def cell(index, value=0, *, kind=D.Kind.FIXED, error=0, total=None, binding_error=0):
    raw = value.encode("utf-8") if kind == D.Kind.STRING else (int(value) & ((1 << 64)-1)).to_bytes(8, "little")
    return D.ProjectionCell(0, index, error, kind, 4, int(value) & ((1 << 64)-1)
        if kind != D.Kind.STRING else 0, raw, len(raw) if total is None else total,
        binding_error=binding_error)


def page(cells, *, error=0, count_valid=True):
    return D.ProjectionPage(error, 0x7000, len(cells), 0, 17, 1, count_valid, 1, tuple(cells))


def result(value=0, *, kind=D.Kind.FIXED, error=0, handle=100, text=None, total=None):
    copied = cell(0, text if text is not None else value, kind=kind, error=error, total=total)
    return NS(error=error, kind=kind, type_id=4, raw_bits=copied.raw_bits,
        value_handle=handle, snapshot=copied.snapshot, snapshot_bytes=len(copied.snapshot), total=copied.total)


def query(value, **extra):
    return NS(result=value, **extra)


class Context:
    def __init__(self):
        self.native = object(); self.process = object(); self.adapters = {"data": 7}
        self.world = 0x7000; self.diagnostics = []; self.calls = []; self.refreshes = 0
        self.types = []; self.remembered = []
    async def refresh(self): self.refreshes += 1
    async def country_id(self): return 0x300009c
    async def type_id(self, name): self.types.append(name); return 83
    async def query(self, label, factory):
        self.calls.append(label)
        return factory().result
    def _remember(self, query): self.remembered.append(query)


FIELDS = (O.Field("rate", "Country.GetWeeklyInterestRate", "fixed", "ratio/week"),
    O.Field("description", "Country.GetInterestRateTooltip", "text"))


class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_page_batches_fields_one_type_lookup_without_catalog(self):
        ctx = Context()
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=query(page((cell(0, -123456), cell(1, "原生说明", kind=D.Kind.STRING))))) as send, \
             patch.object(D, "enumerate_functions") as metadata:
            with localcontext() as decimal_context:
                decimal_context.prec = 2
                answer = await O.read_fields(ctx, FIELDS, label="finance")
        self.assertEqual(answer["values"], {"rate": Decimal("-1.23456"), "description": "原生说明"})
        self.assertTrue(answer["complete"]); self.assertEqual(answer["reasons"], {})
        self.assertEqual(ctx.refreshes, 1); self.assertEqual(ctx.types, ["Country"])
        self.assertEqual(send.call_count, 1); metadata.assert_not_called()
        fields, bindings = send.call_args.args[1:]
        self.assertEqual(fields, tuple(field.expression for field in FIELDS))
        self.assertEqual(bindings[0][0].object, 0x300009c)
        self.assertTrue(bindings[0][0].country_id)

    async def test_zero_false_empty_text_are_real_values(self):
        fields = (O.Field("zero", "Country.GetInvestmentPool", "fixed"),
            O.Field("false", "Country.ShowInvestmentPoolPredictedNextBuilding", "boolean"),
            O.Field("empty", "Country.GetLackOfAutonomousInvestmentReasonDesc", "text"))
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=query(page((cell(0), cell(1, 0, kind=D.Kind.BOOL), cell(2, "", kind=D.Kind.STRING))))):
            answer = await O.read_fields(Context(), fields, label="zero")
        self.assertEqual(answer["values"], {"zero": Decimal(0), "false": False, "empty": ""})
        self.assertTrue(answer["complete"])

    async def test_native_error_and_partial_text_keep_copied_values(self):
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=query(page((cell(0, 0, error=13),
                 cell(1, "prefix", kind=D.Kind.STRING, total=20)), error=5))):
            answer = await O.read_fields(Context(), FIELDS, label="partial")
        self.assertEqual(answer["values"], {"rate": Decimal(0), "description": "prefix"})
        self.assertFalse(answer["complete"])
        self.assertEqual(answer["observations"]["rate"]["error"], 13)
        self.assertEqual(answer["observations"]["description"]["page_error"], 5)
        self.assertEqual(answer["observations"]["description"]["total"], 20)

    async def test_missing_field_is_not_zero_and_wrong_kind_is_preserved(self):
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=query(page((cell(0, 42, kind=D.Kind.I64),)))):
            answer = await O.read_fields(Context(), FIELDS, label="missing")
        self.assertEqual(answer["values"], {"rate": None, "description": None})
        self.assertEqual(answer["observations"]["rate"]["native_value"], 42)
        self.assertFalse(answer["complete"])

    async def test_invalid_utf8_and_count_failure_do_not_erase_string(self):
        copied = D.ProjectionCell(0, 1, 0, D.Kind.STRING, 9, 0, b"a\xff", 2)
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=query(page((cell(0), copied), count_valid=False))):
            answer = await O.read_fields(Context(), FIELDS, label="utf8")
        self.assertEqual(answer["values"]["description"], "a\ufffd")
        self.assertFalse(answer["complete"])

    async def test_binding_error_is_visible_without_erasing_scalar(self):
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=query(page((cell(0, 20, binding_error=3), cell(1, "", kind=D.Kind.STRING))))):
            answer = await O.read_fields(Context(), FIELDS, label="binding")
        self.assertEqual(answer["observations"]["rate"]["binding_error"], 3)
        self.assertEqual(answer["values"]["rate"], Decimal("0.0002"))
        self.assertFalse(answer["complete"])

    async def test_more_than32_fields_page_not_truncate(self):
        ctx = Context()
        fields = tuple(O.Field(f"f{i}", "Country.GetInvestmentPool", "fixed") for i in range(35))
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", side_effect=(query(page(tuple(cell(i, i) for i in range(32)))), query(page(tuple(cell(i, i+32) for i in range(3)))))) as send:
            answer = await O.read_fields(ctx, fields, label="pages")
        self.assertEqual(send.call_count, 2); self.assertEqual(ctx.types, ["Country"])
        self.assertEqual(len(answer["values"]), 35)
        self.assertEqual(answer["values"]["f34"], Decimal("0.00034")); self.assertTrue(answer["complete"])

    async def test_transport_error_preserves_original_query_and_partial(self):
        original = query(None, partial_result=page((cell(0, 7),)))
        failure = OSError("copy interrupted"); failure.query = original
        ctx = Context()
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", side_effect=failure) as send:
            with self.assertRaises(OSError) as caught:
                await O.read_fields(ctx, FIELDS, label="interrupted")
        self.assertIs(caught.exception, failure); self.assertIs(failure.query, original)
        self.assertEqual(failure.country_observations["values"]["rate"], Decimal("0.00007"))
        self.assertFalse(failure.country_observations["complete"]); self.assertEqual(send.call_count, 1)


    async def test_fallback_snapshot_uses_original_any_not_repeated_getter(self):
        ctx = Context()
        with patch.object(O, "_projection_supported", return_value=False), \
             patch.object(D, "evaluate", side_effect=(query(result(-123456)), query(result(kind=D.Kind.STRING, text="ab", total=6, handle=101, error=D.ERROR_MORE_DATA)))) as evaluate, \
             patch.object(D, "snapshot", return_value=query(result(kind=D.Kind.STRING, text="abcdef", handle=101))) as snapshot, \
             patch.object(D, "release", return_value=query(NS(error=0, value_handle=0))) as release:
            answer = await O.read_fields(ctx, FIELDS, label="fallback")
        self.assertEqual(answer["values"]["description"], "abcdef"); self.assertTrue(answer["complete"])
        self.assertEqual(evaluate.call_count, 2); self.assertEqual(snapshot.call_args.args[1], 101)
        self.assertEqual([call.args[1] for call in release.call_args_list], [101, 100])

    async def test_fallback_error_and_cleanup_failure_keep_primary(self):
        ctx = Context()
        with patch.object(O, "_projection_supported", return_value=False), \
             patch.object(D, "evaluate", return_value=query(result(0, error=13))), \
             patch.object(D, "release", return_value=query(NS(error=5, value_handle=0))):
            answer = await O.read_fields(ctx, FIELDS[:1], label="fallback")
        self.assertEqual(answer["values"]["rate"], Decimal(0))
        self.assertEqual(answer["observations"]["rate"]["error"], 13)
        self.assertEqual(len(answer["cleanup_warnings"]), 1)
        self.assertEqual(ctx._country_observation_reads[0].owners, [100])

    async def test_fallback_unknown_query_does_not_release_borrowed_owners(self):
        ctx = Context(); failure = OSError("unknown original submission")
        with patch.object(O, "_projection_supported", return_value=False), \
             patch.object(D, "evaluate", side_effect=(query(result(12)), failure)) as evaluate, \
             patch.object(D, "release") as release:
            with self.assertRaises(OSError) as caught:
                await O.read_fields(ctx, FIELDS, label="fallback")
        self.assertIs(caught.exception, failure); release.assert_not_called(); self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(ctx._country_observation_reads[0].owners, [100])
        self.assertEqual(failure.country_observations["values"]["rate"], Decimal("0.00012"))

    async def test_fallback_snapshot_interruption_keeps_prefix_and_original_owner(self):
        ctx = Context(); failure = OSError("copy interrupted")
        with patch.object(O, "_projection_supported", return_value=False), \
             patch.object(D, "evaluate", return_value=query(result(kind=D.Kind.STRING, text="prefix", total=50, error=D.ERROR_MORE_DATA))) as evaluate, \
             patch.object(D, "snapshot", side_effect=failure), patch.object(D, "release") as release:
            with self.assertRaises(OSError):
                await O.read_fields(ctx, FIELDS[1:], label="fallback")
        self.assertEqual(failure.country_observations["values"]["description"], "prefix")
        self.assertEqual(evaluate.call_count, 1); release.assert_not_called()
        self.assertEqual(ctx._country_observation_reads[0].owners, [100])

    async def test_fallback_failed_shorter_snapshot_cannot_erase_prior_prefix(self):
        with patch.object(O, "_projection_supported", return_value=False), \
             patch.object(D, "evaluate", return_value=query(result(kind=D.Kind.STRING, text="prefix", total=50, error=D.ERROR_MORE_DATA))), \
             patch.object(D, "snapshot", return_value=query(result(kind=D.Kind.STRING, text="", total=50, error=5))), \
             patch.object(D, "release", return_value=query(NS(error=0, value_handle=0))):
            answer = await O.read_fields(Context(), FIELDS[1:], label="fallback")
        self.assertEqual(answer["values"]["description"], "prefix")
        self.assertEqual(answer["observations"]["description"]["copies"][0]["error"], 5)
        self.assertFalse(answer["complete"])

    async def test_observed_world_change_keeps_values_and_marks_mixed_scope(self):
        ctx = Context(); ctx.world = 0x1234
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=query(page((cell(0, 7), cell(1, "", kind=D.Kind.STRING))))):
            answer = await O.read_fields(ctx, FIELDS, label="world")
        self.assertEqual(answer["values"]["rate"], Decimal("0.00007"))
        self.assertEqual(answer["pages"][0]["world"], 0x7000)
        self.assertFalse(answer["world_consistent"]); self.assertFalse(answer["complete"])
        self.assertIn("_world", answer["reasons"])

    async def test_state_full_id_and_promoted_region_use_one_binding_without_country_lookup(self):
        ctx = Context()
        async def no_country(): raise AssertionError("State reads do not need a selected country")
        ctx.country_id = no_country
        fields = (O.Field("pollution.impact","State.GetStateRegion.GetPollutionImpact","fixed"),)
        with patch.object(O,"_projection_supported",return_value=True), \
             patch.object(D,"project_page",return_value=query(page((cell(0,123456),)))) as native:
            answer = await O.read_state_fields(ctx,0x3000004,fields,label="state")
        self.assertEqual(answer["state_id"],0x3000004); self.assertNotIn("country_id",answer)
        self.assertEqual(answer["values"]["pollution.impact"],Decimal("1.23456"))
        bound = native.call_args.args[2][0][0]
        self.assertEqual(bound.domain,"State"); self.assertEqual(bound.object,0x3000004)
        self.assertEqual(ctx.types,["State"]); self.assertTrue(answer["complete"])

    async def test_state_transport_error_keeps_original_query_and_partial(self):
        ctx = Context(); original = query(None,partial_result=page((cell(0,7),)))
        failure = OSError("State copy interrupted"); failure.query = original
        fields = (O.Field("turmoil","State.CalcTurmoil","fixed"),)
        with patch.object(O,"_projection_supported",return_value=True), patch.object(D,"project_page",side_effect=failure):
            with self.assertRaises(OSError):
                await O.read_state_fields(ctx,0x3000004,fields,label="state")
        self.assertIs(failure.query,original)
        self.assertEqual(failure.state_observations["values"]["turmoil"],Decimal("0.00007"))

    async def test_state_fallback_observed_world_change_keeps_values_but_not_completeness(self):
        ctx = Context(); calls = 0
        fields = (O.Field("turmoil","State.CalcTurmoil","fixed"),
            O.Field("obstinance","State.GetObstinance","fixed"))
        def evaluate(*args,**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: ctx.world = 0x9000
            return query(result(calls,handle=100+calls))
        with patch.object(O,"_projection_supported",return_value=False), \
             patch.object(D,"evaluate",side_effect=evaluate), \
             patch.object(D,"release",return_value=query(NS(error=0,value_handle=0))):
            answer = await O.read_state_fields(ctx,0x3000004,fields,label="state")
        self.assertEqual(answer["values"],{"turmoil":Decimal("0.00001"),"obstinance":Decimal("0.00002")})
        self.assertFalse(answer["world_consistent"]); self.assertFalse(answer["complete"])
        self.assertIn("_world",answer["reasons"]); self.assertEqual(calls,2)

    async def test_state_invalid_id_or_wrong_root_fails_before_native(self):
        ctx = Context(); fields = (O.Field("turmoil","State.CalcTurmoil","fixed"),)
        for bad in (-1,0x100000000,True,"4"):
            with self.assertRaises(ValueError):
                await O.read_state_fields(ctx,bad,fields,label="state")
        with self.assertRaises(ValueError):
            await O.read_state_fields(ctx,4,FIELDS,label="state")
        self.assertEqual(ctx.refreshes,0)

    async def test_duplicate_field_names_fail_before_native(self):
        ctx = Context()
        with self.assertRaises(ValueError):
            await O.read_fields(ctx, FIELDS[:1] * 2, label="duplicate")
        self.assertEqual(ctx.refreshes, 0)


    async def test_copy_fields_materializes_binding_generator_once_in_same_read(self):
        ctx = Context()
        read = O._Read(O.CountryAPI(ctx))
        answer = {"world": ctx.world, "world_consistent": True, "values": {},
            "observations": {}, "reasons": {}, "cleanup_warnings": []}
        binding = D.ObjectBinding.country(7, 0x3000004)
        bindings = (value for value in (binding,))
        with patch.object(D, "evaluate", side_effect=(query(result(0)),
                query(result(kind=D.Kind.STRING, text="")))) as evaluate, \
             patch.object(D, "release", return_value=query(NS(error=0, value_handle=0))):
            await O.copy_fields(ctx, read, answer, FIELDS, bindings, label="same.read")
            await O.finish_read(ctx, read, answer, label="same.read.cleanup")
        self.assertEqual([call.args[2] for call in evaluate.call_args_list], [(binding,), (binding,)])
        self.assertEqual(answer["values"], {"rate": Decimal(0), "description": ""})
        self.assertEqual(read.owners, [])

    async def test_generic_field_descriptor_keeps_country_and_state_root_guards(self):
        field = O.Field("base_price", "Goods.GetBasePrice", "fixed", "pounds/unit")
        for call in (lambda: O.read_fields(Context(), (field,), label="test.goods"),
                     lambda: O.read_state_fields(Context(), 4, (field,), label="test.goods")):
            with self.assertRaises(ValueError):
                await call()
        for expression in ("Goods.GetKey()", "Goods.GetKey;Country.SetTaxLevelHigh", "GetGoods('iron')"):
            with self.assertRaises(ValueError):
                O.Field("invalid", expression, "text")


class ProjectionCleanupSinkConsumers(unittest.IsolatedAsyncioTestCase):
    def context(self):
        from pdx_runtime.query_executor import Diagnostics
        ctx = Context()
        ctx.diagnostics = Diagnostics()
        return ctx

    async def test_country_same_record_query_page_and_primary_not_vetoed_by_cleanup(self):
        ctx = self.context()
        primary = page((cell(0), cell(1, "", kind=D.Kind.STRING)))
        cleanup = OSError("original current query cleanup")
        original = query(primary, cleanup_error=cleanup)
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=original) as native:
            answer = await O.read_fields(ctx, FIELDS, label="cleanup.same_scope")
        self.assertTrue(answer["complete"])
        self.assertEqual(answer["values"], {"rate": Decimal(0), "description": ""})
        warning, = answer["cleanup_warnings"]
        self.assertIs(warning, ctx.diagnostics["operations"][0])
        self.assertIs(warning["query"], original)
        self.assertIs(warning["cleanup_error"], cleanup)
        self.assertIs(warning["result"], primary)
        self.assertEqual(ctx.remembered, [original])
        self.assertNotIn("cleanup_warning_delivery_error", warning)
        native.assert_called_once()
        self.assertNotIn("cleanup_warnings", native.call_args.kwargs)

    async def test_stale_same_label_same_page_diagnostic_is_not_current_query_warning(self):
        ctx = self.context()
        primary = page((cell(0), cell(1, "", kind=D.Kind.STRING)))
        stale = {"label": "cleanup.reused", "query": query(primary),
            "cleanup_error": OSError("older query only"), "result": primary}
        ctx.diagnostics.append(stale)
        current = query(primary)
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=current) as native:
            answer = await O.read_fields(ctx, FIELDS, label="cleanup.reused")
        self.assertTrue(answer["complete"])
        self.assertEqual(answer["cleanup_warnings"], [])
        self.assertEqual(ctx.diagnostics["operations"], [stale])
        self.assertEqual(ctx.remembered, [])
        native.assert_called_once()

    async def test_state_partial_native_errors_and_values_coexist_with_same_query_cleanup(self):
        ctx = self.context()
        fields = (O.Field("native.zero", "State.CalcTurmoil", "fixed"),)
        primary = page((cell(0, 0, error=13),), error=5)
        original = query(primary, cleanup_error=OSError("release still pending"))
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=original) as native:
            answer = await O.read_state_fields(ctx, 0xAF000123, fields, label="cleanup.state")
        self.assertEqual(answer["state_id"], 0xAF000123)
        self.assertEqual(answer["values"]["native.zero"], Decimal(0))
        self.assertEqual(answer["observations"]["native.zero"]["error"], 13)
        self.assertEqual(answer["observations"]["native.zero"]["page_error"], 5)
        self.assertFalse(answer["complete"])
        self.assertIs(answer["cleanup_warnings"][0]["query"], original)
        self.assertIs(answer["cleanup_warnings"][0]["result"], primary)
        self.assertIs(answer["cleanup_warnings"][0], ctx.diagnostics["operations"][0])
        self.assertEqual(ctx.remembered, [original])
        native.assert_called_once()

    async def test_multi_page_warning_provenance_uses_each_original_query_not_label(self):
        ctx = self.context()
        fields = tuple(O.Field(f"f{i}", "Country.GetInvestmentPool", "fixed") for i in range(65))
        primaries = (page(tuple(cell(i, i) for i in range(32))),
            page(tuple(cell(i, i + 32) for i in range(32))), page((cell(0, 64),)))
        originals = (query(primaries[0], cleanup_error=OSError("first")),
            query(primaries[1]), query(primaries[2], cleanup_error=OSError("third")))
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", side_effect=originals) as native:
            answer = await O.read_fields(ctx, fields, label="cleanup.pages")
        self.assertTrue(answer["complete"])
        self.assertEqual(answer["values"]["f64"], Decimal("0.00064"))
        self.assertEqual(len(answer["pages"]), 3)
        self.assertEqual(len(answer["cleanup_warnings"]), 2)
        for warning, expected_query, expected_page in zip(answer["cleanup_warnings"],
                (originals[0], originals[2]), (primaries[0], primaries[2])):
            self.assertIs(warning["query"], expected_query)
            self.assertIs(warning["result"], expected_page)
        self.assertIs(answer["cleanup_warnings"][0], ctx.diagnostics["operations"][0])
        self.assertIs(answer["cleanup_warnings"][1], ctx.diagnostics["operations"][1])
        self.assertEqual(native.call_count, 3)
        self.assertEqual(ctx.remembered, [originals[0], originals[2]])

    async def test_original_exception_query_and_partial_survive_without_synthetic_warning(self):
        ctx = self.context()
        partial = page((cell(0, 7),))
        original = query(None, partial_result=partial, cleanup_error=OSError("query retains detail"))
        failure = OSError("original copy interruption")
        failure.query = original
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", side_effect=failure) as native:
            with self.assertRaises(OSError) as caught:
                await O.read_fields(ctx, FIELDS, label="cleanup.interrupted")
        self.assertIs(caught.exception, failure)
        self.assertIs(caught.exception.query, original)
        self.assertIs(caught.exception.query.partial_result, partial)
        self.assertEqual(failure.country_observations["values"]["rate"], Decimal("0.00007"))
        self.assertEqual(failure.country_observations["cleanup_warnings"], [])
        self.assertIsNotNone(original.cleanup_error)
        self.assertFalse(failure.country_observations["complete"])
        native.assert_called_once()

    async def test_pop_culture30_keeps_cleanup_record_and_observation_complete_separate_from_contract(self):
        from pdx_runtime.v3_game.economy import EconomyAPI
        from pdx_runtime.v3_game.economy.observations import POP_CULTURE
        ctx = self.context()
        async def perform(label, work):
            return await work()
        async def no_country():
            raise AssertionError("full-ID Pop read must not resolve selected Country")
        ctx.perform = perform
        ctx.country_id = no_country
        cells = []
        for index, field in enumerate(POP_CULTURE):
            kind = {"fixed": D.Kind.FIXED, "integer": D.Kind.I32,
                "boolean": D.Kind.BOOL, "text": D.Kind.STRING}[field.kind]
            if field.name in ("culture.native_id", "assimilation.target_native_culture_id"):
                kind = D.Kind.U32
            if field.name in ("assimilation.individual_count", "conversion.individual_count"):
                kind = D.Kind.I64
            cells.append(cell(index, "" if kind == D.Kind.STRING else 0, kind=kind))
        primary = page(cells)
        original = query(primary, cleanup_error=OSError("same Pop projection query"))
        with patch.object(O, "_projection_supported", return_value=True), \
             patch.object(D, "project_page", return_value=original) as native:
            answer = await EconomyAPI(ctx).pop_culture(0xAF000123)
        self.assertTrue(answer["complete"])
        self.assertFalse(answer["contract_complete"])
        self.assertEqual(answer["pop_id"], 0xAF000123)
        self.assertEqual(answer["culture"]["native_id"], 0)
        self.assertIs(answer["assimilation"]["is_assimilating"], False)
        self.assertEqual(answer["conversion"]["individual_count"], 0)
        self.assertEqual(answer["religion"]["name"], "")
        self.assertIs(answer["cleanup_warnings"][0], ctx.diagnostics["operations"][0])
        self.assertIs(answer["cleanup_warnings"][0]["query"], original)
        self.assertIs(answer["cleanup_warnings"][0]["result"], primary)
        self.assertEqual(ctx.remembered, [original])
        native.assert_called_once()


if __name__ == "__main__": unittest.main()
