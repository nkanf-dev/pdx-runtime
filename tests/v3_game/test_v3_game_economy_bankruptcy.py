"""Bankruptcy native-command composition, ownership and preview CPU tests."""
from decimal import Decimal
import unittest
from unittest.mock import patch, AsyncMock

from pdx_runtime import v3_data as D
from pdx_runtime.v3_game.economy import EconomyAPI
from pdx_runtime.v3_game.economy.observations import BANKRUPTCY
from tests.v3_game.test_v3_game_economy import Fixture, q


def facade(f):
    api = EconomyAPI(f.ctx)
    original = f.eval
    def evaluate(client, expression, *args, **kwargs):
        if expression == "Country.DeclareBankruptcy":
            generated = original(client, "Country.SetTaxLevelHigh", *args, **kwargs)
            f.action = expression
            return generated
        return original(client, expression, *args, **kwargs)
    f.eval = evaluate
    api._finance_observations = AsyncMock(return_value={"country_id": 156, "complete": True,
        "reasons": {}, "credit": {"principal": Decimal("123"), "self_held_debt": Decimal("1")},
        "balance": {"predicted_after_bankruptcy": Decimal("-12")},
        "consequences": {"radicalization": 5000, "description": "原生破产后果"}})
    return api


class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_reads_consequences_validates_once_without_submitting(self):
        f = Fixture(); api = facade(f)
        with f.patches(): answer = await api.bankruptcy_preview()
        self.assertTrue(answer["allowed"]); self.assertTrue(answer["eligibility_observed"])
        self.assertEqual(answer["credit"]["principal"], Decimal("123")); self.assertEqual(answer["consequences"]["radicalization"], 5000)
        self.assertEqual(f.submits, 0); self.assertEqual(f.destroyed, 1); self.assertEqual(f.released, [80])
        self.assertEqual(f.ctx.calls.count("economy.validate"), 1)
        api._finance_observations.assert_awaited_once_with(BANKRUPTCY, "economy.bankruptcy_preview")

    async def test_preview_native_rejection_preserves_description_and_reason(self):
        f = Fixture(); f.allowed = False; api = facade(f)
        with f.patches(): answer = await api.bankruptcy_preview()
        self.assertFalse(answer["allowed"]); self.assertTrue(answer["eligibility_observed"])
        self.assertEqual(answer["eligibility_reason"], "游戏需要更多行政力")
        self.assertEqual(answer["consequences"]["description"], "原生破产后果"); self.assertEqual(f.submits, 0)

    async def test_action_uses_original_producer_and_exposes_actual_receipt(self):
        f = Fixture(); api = facade(f)
        with f.patches(): answer = await api.declare_bankruptcy()
        self.assertTrue(answer["accepted"]); self.assertEqual(answer["native_accepted"], 1)
        self.assertEqual(answer["native_error"], 0); self.assertEqual(answer["action"], "declare_bankruptcy")
        self.assertEqual(f.action, "Country.DeclareBankruptcy"); self.assertEqual(f.submits, 1)
        self.assertNotIn("level", answer); self.assertNotIn("sector", answer)
        self.assertNotIn("effect_observed", answer)
        self.assertEqual(f.released, [80]); self.assertEqual(f.destroyed, 0)

    async def test_uncontrolled_actor_is_not_submitted(self):
        f = Fixture(); f.controlled = 4; api = facade(f)
        with f.patches(): answer = await api.declare_bankruptcy()
        self.assertFalse(answer["accepted"]); self.assertEqual(f.submits, 0); self.assertFalse(f.evaluated)


    async def test_preview_real_batch_preserves_int64_radicalization(self):
        from pdx_runtime.v3_game import _read as observations
        f = Fixture(); api = facade(f)
        api._finance_observations = EconomyAPI._finance_observations.__get__(api)
        cells = []
        for index, field in enumerate(BANKRUPTCY):
            kind = D.Kind.STRING if field.kind == "text" else D.Kind.I64 if field.kind == "integer" else D.Kind.FIXED
            value = (1 << 40 if field.kind == "integer" else
                7394033038000 if field.name == "credit.principal" else
                88728396000 if field.name == "credit.self_held_debt" else
                -1200000 if field.name == "balance.predicted_after_bankruptcy" else 0)
            bits = value & ((1 << 64) - 1)
            raw = "原生后果".encode() if field.kind == "text" else bits.to_bytes(8,"little")
            cells.append(D.ProjectionCell(0,index,0,kind,115,bits,raw,len(raw)))
        copied = D.ProjectionPage(0,f.ctx.world,len(BANKRUPTCY),0,77,1,True,1,tuple(cells))
        with f.patches(), patch.object(observations,"_projection_supported",return_value=True), \
             patch.object(D,"project_page",return_value=q(copied)) as batch:
            answer = await api.bankruptcy_preview()
        self.assertEqual(answer["consequences"]["radicalization"],1 << 40)
        self.assertEqual(answer["credit"]["principal"], Decimal("73940330.38"))
        self.assertEqual(answer["credit"]["self_held_debt"], Decimal("887283.96"))
        self.assertEqual(answer["balance"]["predicted_after_bankruptcy"], Decimal("-12"))
        self.assertTrue(answer["predictions"]["balance.predicted_after_bankruptcy"])
        self.assertEqual(answer["units"]["balance.predicted_after_bankruptcy"], "pounds/week")
        self.assertEqual(batch.call_count,1)
        self.assertEqual(batch.call_args.args[1],tuple(field.expression for field in BANKRUPTCY))
        self.assertTrue(answer["complete"]); self.assertEqual(f.submits,0)

    async def test_unknown_eligibility_is_not_complete(self):
        f = Fixture(); api = facade(f)
        api._national_command = AsyncMock(return_value={"country_id": 156, "accepted": False,
            "native_error": 13, "reason": "native producer error 13"})
        answer = await api.bankruptcy_preview()
        self.assertIsNone(answer["allowed"]); self.assertFalse(answer["eligibility_observed"])
        self.assertFalse(answer["complete"]); self.assertEqual(answer["credit"]["principal"], Decimal("123"))


if __name__ == "__main__": unittest.main()
