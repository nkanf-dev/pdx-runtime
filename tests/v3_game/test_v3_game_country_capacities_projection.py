"""Same15 native capacity fields in one copied page. CPU-only call counts."""
from decimal import Decimal, localcontext
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch, AsyncMock

from pdx_runtime import v3_data as D, v3_commands as C
from pdx_runtime.v3_game.country import CountryAPI

RESOURCES = ("bureaucracy", "authority", "influence")
RESOURCE_GETTERS = ("GetPowerDelta", "GetPowerIncome", "GetDeficitPowerRatio", "GetExcessPowerRatio")
INSTITUTION_GETTERS = ("GetInstitutionInvestmentBureaucracyCost",
    "GetInstitutionInvestmentBureaucracyFactor", "GetInstitutionInvestment")
EXPRESSIONS = tuple("Country." + getter + "('" + resource + "')"
    for resource in RESOURCES for getter in RESOURCE_GETTERS) + tuple(
    "Country." + getter for getter in INSTITUTION_GETTERS)


def cell(index, signed=0, *, kind=D.Kind.FIXED, error=0, binding_error=0):
    bits = signed & ((1 << 64)-1)
    return D.ProjectionCell(0, index, error, kind, 80, bits,
        bits.to_bytes(8, "little"), 8, binding_error=binding_error)


def copied(cells=None, *, error=0, count_valid=True, world=0x7000):
    return D.ProjectionPage(error, world, 15, 0, 17, 1, count_valid, 1,
        tuple(cell(i, i*100000) for i in range(15)) if cells is None else tuple(cells))


class Context:
    def __init__(self):
        self.native = object(); self.world = 0x7000; self.adapters = {"data": 7}
        self.resolves = []; self.process = NS(resolve_export=self.resolve)
        self.refreshes = 0; self.country_calls = 0; self.types = []
        self.calls = []; self.labels = []; self.diagnostics = []; self.remembered = []
        self.failure = None
    def resolve(self, module, symbol, **kwargs):
        self.resolves.append((module, symbol)); return 0x180010000
    async def blocking(self, function, *args, **kwargs): return function(*args, **kwargs)
    async def refresh(self): self.refreshes += 1
    async def country_id(self): self.country_calls += 1; return 0x300009c
    async def type_id(self, name): self.types.append(name); return 83
    async def perform(self, label, work): self.labels.append(label); return await work()
    async def query(self, label, factory):
        self.calls.append(label)
        original = factory()
        if self.failure is not None: raise self.failure
        return original.result
    def _remember(self, query): self.remembered.append(query)


class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_same15_original_fields_one_page_without_metadata_or_scalar_calls(self):
        ctx = Context(); original = NS(result=copied())
        with patch.object(D, "project_page", return_value=original) as page, \
             patch.object(D, "evaluate") as evaluate, patch.object(D, "release") as release, \
             patch.object(D, "enumerate_functions") as metadata, patch.object(C, "request") as command:
            answer = await CountryAPI(ctx).capacities()
        self.assertEqual(page.call_count, 1)
        self.assertEqual(page.call_args.args[1], EXPRESSIONS)
        binding = page.call_args.args[2][0][0]
        self.assertEqual(binding.object, 0x300009c)
        self.assertTrue(binding.country_id); self.assertFalse(binding.retained)
        self.assertEqual(page.call_args.kwargs["limit"], 1)
        self.assertEqual((ctx.refreshes, ctx.country_calls, ctx.types), (1, 1, ["Country"]))
        self.assertEqual(ctx.labels, ["country.capacities"])
        self.assertEqual(ctx.calls, ["country.capacities.page"])
        self.assertEqual(answer["bureaucracy"]["balance"], Decimal(0))
        self.assertEqual(answer["bureaucracy"]["used"], Decimal(1))
        self.assertEqual(answer["institutions"]["investment_levels"], Decimal(14))
        self.assertEqual(answer["influence"]["units"]["balance"], "influence points")
        self.assertIs(answer["native_pages"][0], original.result)
        evaluate.assert_not_called(); release.assert_not_called()
        metadata.assert_not_called(); command.assert_not_called()

    async def test_signed64_used_wrap_is_exact_even_with_low_decimal_precision(self):
        cells = [cell(i) for i in range(15)]
        cells[0] = cell(0, -1); cells[1] = cell(1, (1 << 63)-1)
        with patch.object(D, "project_page", return_value=NS(result=copied(cells))):
            with localcontext() as context:
                context.prec = 2
                answer = await CountryAPI(Context()).capacities()
        self.assertEqual(answer["bureaucracy"]["balance"], Decimal("-0.00001"))
        self.assertEqual(answer["bureaucracy"]["produced"], Decimal("92233720368547.75807"))
        self.assertEqual(answer["bureaucracy"]["used"], Decimal("-92233720368547.75808"))
        self.assertEqual(answer["native_pages"][0].cells[0].raw_bits, (1 << 64)-1)

    async def test_native_error_keeps_zero_and_signed_values_and_used_reason(self):
        cells = [cell(i) for i in range(15)]
        cells[0] = cell(0, 0, error=13); cells[1] = cell(1, -123456)
        with patch.object(D, "project_page", return_value=NS(result=copied(cells))):
            answer = await CountryAPI(Context()).capacities()
        row = answer["bureaucracy"]
        self.assertEqual((row["balance"], row["produced"], row["used"]),
            (Decimal(0), Decimal("-1.23456"), Decimal("-1.23456")))
        self.assertIn("13", row["reasons"]["balance"])
        self.assertIn("13", row["reasons"]["used"])
        self.assertEqual(answer["native_pages"][0].cells[0].error, 13)


    async def test_details_keeps_three_existing_contextual_expense_calls(self):
        ctx = Context(); api = CountryAPI(ctx)
        expenses = AsyncMock(return_value=({"text": "native", "raw_text": "#native"}, None))
        with patch.object(D, "project_page", return_value=NS(result=copied())) as page, \
             patch.object(api, "_capacity_expenses", expenses):
            answer = await api.capacities(details=True)
        self.assertEqual(page.call_count, 1); self.assertEqual(expenses.await_count, 3)
        self.assertEqual([call.args[2] for call in expenses.await_args_list], list(RESOURCES))
        self.assertEqual(answer["influence"]["expenses_text"], "native")
        self.assertEqual(answer["influence"]["expenses_raw"], "#native")

    async def test_legacy_dispatch_remains15_scalars_and_no_page(self):
        ctx = Context(); ctx.process = object(); api = CountryAPI(ctx)
        async def scalar(read, binding, getter, return_name, *, arguments=(), observation=None):
            signed = 200000 if getter == "GetPowerIncome" else 100000
            if observation is not None: observation.update(raw_signed=signed, scale=100000)
            return Decimal(signed)/100000, None
        with patch.object(api, "_scalar", side_effect=scalar) as scalars, patch.object(D, "project_page") as page:
            answer = await api.capacities()
        self.assertEqual(scalars.await_count, 15); page.assert_not_called()
        self.assertEqual(answer["bureaucracy"]["used"], Decimal(1))
        self.assertNotIn("native_pages", answer)

    async def test_healthy_old_adapter_and_page_outputs_match_with31_to1_mocked_queries(self):
        old_ctx = Context(); old_ctx.process = object()
        rows = tuple(NS(type_id=83, name=getter, argument_count=1,
            callbacks=(1, 0, 0, 0)) for getter in RESOURCE_GETTERS) + tuple(
            NS(type_id=83, name=getter, argument_count=0, callbacks=(1, 0, 0, 0))
            for getter in INSTITUTION_GETTERS)
        handles = []
        def evaluate(native, expression, bindings, **kwargs):
            handles.append(len(handles)+1)
            bits = EXPRESSIONS.index(expression)*100000
            return NS(result=NS(error=0, value=D.FixedPoint(bits), kind=D.Kind.FIXED,
                value_handle=handles[-1], raw_bits=bits, snapshot=bits.to_bytes(8, "little"),
                snapshot_bytes=8, total=8))
        with patch.object(D, "enumerate_functions", return_value=NS(result=NS(
                error=0, rows=rows, next=len(rows), total=len(rows)))) as metadata, \
             patch.object(D, "evaluate", side_effect=evaluate) as scalar, \
             patch.object(D, "release", return_value=NS(result=NS(error=0, value_handle=0))) as release:
            old = await CountryAPI(old_ctx).capacities()
        new_ctx = Context()
        with patch.object(D, "project_page", return_value=NS(result=copied())) as page:
            new = await CountryAPI(new_ctx).capacities()
        self.assertEqual({key: value for key, value in new.items() if key != "native_pages"}, old)
        self.assertEqual((metadata.call_count, scalar.call_count, release.call_count), (1, 15, 15))
        self.assertEqual([call.args[1] for call in release.call_args_list], handles)
        self.assertEqual((len(old_ctx.calls), len(new_ctx.calls)), (31, 1))
        self.assertEqual(page.call_count, 1)

    async def test_optional_export_is_discovered_once_not_per_field_or_call(self):
        ctx = Context(); api = CountryAPI(ctx)
        with patch.object(D, "project_page", return_value=NS(result=copied())) as page:
            await api.capacities(); await api.capacities()
        self.assertEqual(len(ctx.resolves), 1); self.assertEqual(page.call_count, 2)


if __name__ == "__main__": unittest.main()
