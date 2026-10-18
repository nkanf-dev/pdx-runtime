"""Budget player tasks over copied native pages; no game or process access."""
from decimal import Decimal, localcontext
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as D, v3_commands as C
from pdx_runtime.v3_game.economy import EconomyAPI
from pdx_runtime.v3_game import _read as O


def q(result):
    return NS(result=result)


class Context:
    def __init__(self):
        self.native = object()
        self.process = NS(resolve_export=lambda *args, **kwargs: 0x180010000)
        self.world = 0x7000
        self.adapters = {"data": 7}
        self.diagnostics = []
        self.refreshes = 0
        self.country_calls = 0
        self.labels = []
    async def refresh(self): self.refreshes += 1
    async def country_id(self): self.country_calls += 1; return 0x300009c
    async def type_id(self, name): return 83
    async def perform(self, label, work): self.labels.append(label); return await work()
    async def query(self, label, factory): return factory().result
    async def blocking(self, function, *args, **kwargs): return function(*args, **kwargs)
    def _remember(self, query): pass


class Native:
    def __init__(self, ctx):
        self.ctx = ctx
        self.pages = []
        self.evaluated = []
        self.released = []
        self.errors = {}
        self.values = {
            "Country.GetWeeklyBalance": -25121100000,
            "Country.PredictWeeklyBalance": -41600000000,
            "Country.PredictBalanceWithoutTemporaryIncomeAndExpenses": -20712345678,
            "Country.GetPrincipal": 7394033038001,
            "Country.GetGovernmentSelfDebt": 88728396000,
            "Country.GetGovernmentSelfDebtFraction": 1200,
            "Country.GetMaxCredit": 17706625490000,
            "Country.GetRemainingCredit": 10312592452000,
            "Country.GetCreditRatio": 42000,
            "Country.GetInterestPayment": 41400000000,
            "Country.GetWeeklyInterestRate": 560,
            "Country.GetYearlyInterestRate": 29200,
            "Country.GetInvestmentIncome": 27876300000,
            "Country.GetPrivateConstructionGoodsExpenses": 27876300000,
            "Country.GetGovernmentConstructionGoodsExpenses": 12300000,
            "Country.GetConstructionGoodsExpenses": 99900000,
            "Country.PredictGovernmentShareDividends": 43200000,
            "Country.GetModifier.GetValueFor('country_tax_income_add')": 11100,
            "Country.GetModifier.GetValueFor('country_expenses_add')": 22200,
            "Country.GetIncomeBreakdown": "原生[Country.GetName]\x15! income",
            "Country.GetExpensesBreakdown": "原生 expenses",
            "Country.GetCreditTooltip": "原生 credit",
            "Country.GetInterestRateTooltip": "原生 interest",
            "Country.PredictWeeklyBalanceWithEconomyHealthHint": "原生 balance hint",
        }
        self.interrupt_page = None
        self.interrupt_expression = None
        self.failure = None
        self.cleanup_error = False
        self.page_world = ctx.world
    def cell(self, expression, index=0):
        value = self.values.get(expression, 100000)
        if isinstance(value, str):
            kind, bits, raw = D.Kind.STRING, 0, value.encode()
        else:
            kind, bits = D.Kind.FIXED, value & ((1 << 64) - 1)
            raw = bits.to_bytes(8, "little")
        return D.ProjectionCell(0, index, self.errors.get(expression, 0), kind, 82,
            bits, raw, len(raw))
    def project(self, client, fields, binding_rows, **kwargs):
        self.pages.append((tuple(fields), tuple(binding_rows)))
        if self.interrupt_page == len(self.pages): raise self.failure
        return q(D.ProjectionPage(0, self.page_world, len(fields), 0, 77, 1, True, 1,
            tuple(self.cell(expression, index) for index, expression in enumerate(fields))))
    def evaluate(self, client, expression, bindings, **kwargs):
        self.evaluated.append((expression, bindings))
        if expression == self.interrupt_expression: raise self.failure
        cell = self.cell(expression)
        return q(NS(error=cell.error, kind=cell.kind, type_id=cell.type_id,
            raw_bits=cell.raw_bits, value_handle=len(self.evaluated), snapshot=cell.snapshot,
            total=cell.total))
    def release(self, client, handle, **kwargs):
        self.released.append(handle)
        return q(NS(error=13 if self.cleanup_error else 0,
            value_handle=handle if self.cleanup_error else 0))
    def patches(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(O, "_projection_supported", return_value=True))
        stack.enter_context(patch.object(D, "project_page", side_effect=self.project))
        stack.enter_context(patch.object(D, "evaluate", side_effect=self.evaluate))
        stack.enter_context(patch.object(D, "release", side_effect=self.release))
        return stack


class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_explain_deficit_has_ui_prediction_native_value_and_borrowing_cost_together(self):
        ctx = Context(); native = Native(ctx)
        with native.patches(), patch.object(C, "request") as command, localcontext() as decimal_context:
            decimal_context.prec = 2
            answer = await EconomyAPI(ctx).budget()
        self.assertEqual(answer["balance"]["predicted_weekly"], Decimal("-416000"))
        self.assertEqual(answer["balance"]["native_weekly"], Decimal("-251211"))
        self.assertEqual(answer["balance"]["predicted_without_temporary"], Decimal("-207123.45678"))
        self.assertEqual(answer["credit"]["principal"], Decimal("73940330.38001"))
        self.assertEqual(answer["credit"]["self_held_debt"], Decimal("887283.96"))
        self.assertEqual(answer["credit"]["used_fraction"], Decimal("0.42"))
        self.assertEqual(answer["credit"]["interest"]["weekly_payment"], Decimal("414000"))
        self.assertEqual(answer["credit"]["interest"]["weekly_rate"], Decimal("0.0056"))
        self.assertEqual(answer["credit"]["interest"]["yearly_rate"], Decimal("0.292"))
        self.assertEqual(answer["sources"]["credit.principal"], "Country.GetPrincipal")
        self.assertEqual(answer["units"]["credit.interest.weekly_payment"], "pounds/week")
        self.assertEqual(answer["units"]["credit.principal"], "pounds")
        self.assertTrue(answer["predictions"]["balance.predicted_weekly"])
        self.assertFalse(answer["predictions"]["balance.native_weekly"])
        self.assertTrue(answer["complete"])
        self.assertEqual((ctx.refreshes, ctx.country_calls), (1, 1))
        self.assertEqual(len(native.pages), 1)
        self.assertLessEqual(len(native.pages[0][0]), D.MAX_PAGE_FIELDS)
        self.assertFalse(any(isinstance(native.values.get(expression), str)
            for expression in native.pages[0][0]))
        self.assertFalse(native.evaluated)
        command.assert_not_called()

    async def test_construction_funding_keeps_transfer_private_spending_and_dividends_separate(self):
        ctx = Context(); native = Native(ctx)
        with native.patches(): answer = await EconomyAPI(ctx).budget(details=True)
        income = answer["income"]["categories"]
        construction = answer["expenses"]["categories"]["construction"]
        self.assertEqual(income["investment_pool_transfer"], Decimal("278763"))
        self.assertEqual(construction["private"], Decimal("278763"))
        self.assertEqual(construction["government"], Decimal("123"))
        self.assertEqual(construction["total"], Decimal("999"))
        self.assertEqual(income["government_dividends"], Decimal("432"))
        self.assertEqual(income["additional_modifier"], Decimal("0.111"))
        self.assertEqual(answer["expenses"]["categories"]["additional_modifier"], Decimal("0.222"))
        self.assertEqual(answer["sources"]["income.categories.investment_pool_transfer"], "Country.GetInvestmentIncome")
        self.assertEqual(answer["sources"]["income.categories.additional_modifier"],
            "Country.GetModifier.GetValueFor('country_tax_income_add')")
        self.assertEqual([expression for expression, _ in native.evaluated], [
            "Country.GetModifier.GetValueFor('country_tax_income_add')",
            "Country.GetModifier.GetValueFor('country_expenses_add')"])
        self.assertTrue(all(bindings[0].object == answer["country_id"] for _, bindings in native.evaluated))
        self.assertEqual((ctx.refreshes, ctx.country_calls), (1, 1))
        self.assertTrue(answer["complete"])
        self.assertFalse(any("Breakdown" in expression or "Tooltip" in expression
            for fields, _ in native.pages for expression in fields))

    async def test_explanations_are_opt_in_original_text_not_an_invented_balance_formula(self):
        ctx = Context(); native = Native(ctx)
        with native.patches(): answer = await EconomyAPI(ctx).budget(explanations=True)
        self.assertEqual(answer["explanations"]["income"], "原生[Country.GetName]\x15! income")
        self.assertEqual(answer["explanations"]["interest"], "原生 interest")
        self.assertEqual(answer["sources"]["explanations.balance"],
            "Country.PredictWeeklyBalanceWithEconomyHealthHint")
        self.assertEqual(answer["balance"]["native_weekly"], Decimal("-251211"))
        self.assertNotIn("categories", answer["income"])
        self.assertFalse(native.evaluated)

    async def test_native_error_keeps_observed_amount_and_independent_credit_values(self):
        ctx = Context(); native = Native(ctx)
        native.errors["Country.GetInterestPayment"] = 13
        with native.patches(): answer = await EconomyAPI(ctx).budget()
        self.assertEqual(answer["credit"]["interest"]["weekly_payment"], Decimal("414000"))
        self.assertEqual(answer["credit"]["principal"], Decimal("73940330.38001"))
        self.assertEqual(answer["observations"]["credit.interest.weekly_payment"]["error"], 13)
        self.assertIn("credit.interest.weekly_payment", answer["reasons"])
        self.assertFalse(answer["complete"])

    async def test_second_page_interruption_retains_original_query_and_grouped_budget_prefix(self):
        ctx = Context(); native = Native(ctx)
        native.interrupt_page = 2
        original = q(None); native.failure = OSError("original detail page pending")
        native.failure.query = original
        with native.patches(), self.assertRaises(OSError) as caught:
            await EconomyAPI(ctx).budget(details=True)
        self.assertIs(caught.exception, native.failure)
        self.assertIs(caught.exception.query, original)
        prefix = caught.exception.finance_observations
        self.assertEqual(prefix["balance"]["native_weekly"], Decimal("-251211"))
        self.assertEqual(prefix["credit"]["principal"], Decimal("73940330.38001"))
        self.assertIsNone(prefix["expenses"]["categories"]["construction"]["private"])
        self.assertFalse(prefix["complete"])
        self.assertEqual(len(native.pages), 2)
        self.assertFalse(native.evaluated)

    async def test_pending_modifier_keeps_successful_prefix_and_original_owner_without_replay(self):
        ctx = Context(); native = Native(ctx)
        native.interrupt_expression = "Country.GetModifier.GetValueFor('country_expenses_add')"
        native.failure = OSError("original modifier pending")
        original = q(None); native.failure.query = original
        with native.patches(), self.assertRaises(OSError) as caught:
            await EconomyAPI(ctx).budget(details=True)
        self.assertIs(caught.exception.query, original)
        prefix = caught.exception.finance_observations
        self.assertEqual(prefix["income"]["categories"]["additional_modifier"], Decimal("0.111"))
        self.assertIsNone(prefix["expenses"]["categories"]["additional_modifier"])
        self.assertFalse(prefix["complete"])
        self.assertEqual(len(native.evaluated), 2)
        self.assertFalse(native.released)
        self.assertEqual(ctx._country_observation_reads[0].owners, [1])

    async def test_cleanup_warning_does_not_swallow_modifier_or_budget_results(self):
        ctx = Context(); native = Native(ctx); native.cleanup_error = True
        with native.patches(): answer = await EconomyAPI(ctx).budget(details=True)
        self.assertEqual(answer["income"]["categories"]["additional_modifier"], Decimal("0.111"))
        self.assertEqual(answer["credit"]["principal"], Decimal("73940330.38001"))
        self.assertTrue(answer["complete"])
        self.assertTrue(answer["cleanup_warnings"])
        self.assertEqual(native.released, [2])

    async def test_detail_and_explanation_selection_preserves_both_without_worldwide_reads(self):
        ctx = Context(); native = Native(ctx)
        with native.patches():
            answer = await EconomyAPI(ctx).budget(details=True, explanations=True)
        self.assertEqual(answer["income"]["categories"]["investment_pool_transfer"], Decimal("278763"))
        self.assertEqual(answer["explanations"]["income"], "原生[Country.GetName]\x15! income")
        self.assertTrue(answer["predictions"]["income.categories.government_dividends"])
        self.assertFalse(answer["predictions"]["income.categories.investment_pool_transfer"])
        self.assertTrue(answer["complete"])
        self.assertEqual(len(native.pages), 2)
        self.assertTrue(all(len(fields) <= D.MAX_PAGE_FIELDS for fields, _ in native.pages))
        self.assertTrue(all(expression.startswith("Country.")
            for fields, _ in native.pages for expression in fields))
        self.assertEqual((ctx.refreshes, ctx.country_calls), (1, 1))

    async def test_cleanup_cancellation_keeps_completed_primary_and_same_error(self):
        import asyncio
        ctx = Context(); native = Native(ctx)
        failure = asyncio.CancelledError("original cleanup cancelled")
        original = q(None); failure.query = original
        def cancelled_release(client, handle, **kwargs):
            native.released.append(handle)
            raise failure
        with native.patches(), patch.object(D, "release", side_effect=cancelled_release), \
             self.assertRaises(asyncio.CancelledError) as caught:
            await EconomyAPI(ctx).budget(details=True)
        self.assertIs(caught.exception, failure)
        self.assertIs(caught.exception.query, original)
        primary = caught.exception.finance_observations
        self.assertEqual(primary["income"]["categories"]["additional_modifier"], Decimal("0.111"))
        self.assertEqual(primary["expenses"]["categories"]["additional_modifier"], Decimal("0.222"))
        self.assertTrue(primary["complete"])
        self.assertEqual(native.released, [2])
        self.assertEqual(len(native.evaluated), 2)
        self.assertEqual(ctx._country_observation_reads[0].owners, [1, 2])

    async def test_world_change_keeps_native_values_as_provenance_not_an_entry_guard(self):
        ctx = Context(); native = Native(ctx); native.page_world = 0x8000
        with native.patches(): answer = await EconomyAPI(ctx).budget()
        self.assertEqual(answer["balance"]["predicted_weekly"], Decimal("-416000"))
        self.assertEqual(answer["credit"]["principal"], Decimal("73940330.38001"))
        self.assertFalse(answer["world_consistent"])
        self.assertFalse(answer["complete"])
        self.assertIn("_world", answer["reasons"])
        self.assertEqual(len(native.pages), 1)
        self.assertEqual((ctx.refreshes, ctx.country_calls), (1, 1))

    async def test_ordinary_fallback_preserves_the_same_ui_and_native_metrics(self):
        ctx = Context(); native = Native(ctx)
        with native.patches(), patch.object(O, "_projection_supported", return_value=False):
            answer = await EconomyAPI(ctx).budget()
        self.assertEqual(answer["balance"]["predicted_weekly"], Decimal("-416000"))
        self.assertEqual(answer["balance"]["native_weekly"], Decimal("-251211"))
        self.assertEqual(answer["credit"]["principal"], Decimal("73940330.38001"))
        self.assertTrue(answer["complete"])
        self.assertFalse(native.pages)
        self.assertEqual(len(native.evaluated), len(native.released))
        self.assertEqual((ctx.refreshes, ctx.country_calls), (1, 1))


if __name__ == "__main__": unittest.main()
