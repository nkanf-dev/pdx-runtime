"""CPU-only guarded current/next commander-order observations."""
from __future__ import annotations

from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_commands as commands, v3_data as data, v3_objects as objects
from pdx_runtime.v3_game.military import _warfare as warfare
from pdx_runtime.v3_game.military import MilitaryAPI
from pdx_runtime.v3_game._run import Run as _Run
from tests.v3_game.test_v3_game_warfare_orders import OrderFixture, Option, function, TYPES


ORDER_ANY = ".?AU?$SAnyType@V?$CPdxPtr@VCDataCommanderOrderType@@@@@PdxAnyImpl@@"


def scalar(value, *, kind=None, error=0, total=None, snapshot=None, snapshot_bytes=None):
    if kind is None:
        kind = data.Kind.BOOL if type(value) is bool else data.Kind.STRING
    if snapshot is None:
        snapshot = value.encode("utf-8") if isinstance(value, str) else bytes((int(value),)) if type(value) is bool else b""
    return NS(action=data.Action.EVALUATE, error=error, kind=kind, value=value,
              value_handle=0, snapshot=snapshot,
              snapshot_bytes=len(snapshot) if snapshot_bytes is None else snapshot_bytes,
              total=len(snapshot) if total is None else total)


class StateFixture(OrderFixture):
    def __init__(self):
        super().__init__()
        self.character_fields.update({name: function(name) for name in
            ("GetOrderType", "GetNextOrderType", "IsInBattle")})
        self.state_calls = []
        self.state_receipts = {}
        self.state_overrides = {}
        self.in_battle = True
        self.state_values = {"current": (True, "推进前线"), "next": (True, "防御前线")}
        self.raw_order_any = True
        self.unknown_state_expression = None
        self.capture_queries = []

    def evaluate(self, native, expression, bindings, **kwargs):
        if (expression.startswith("CommanderOrderType.") and bindings
                and self.handles.get(bindings[0].object, (None,))[0] != "held_order"):
            return super().evaluate(native, expression, bindings, **kwargs)
        if expression in {"Character.GetOrderType", "Character.GetNextOrderType",
                          "CommanderOrderType.IsValid", "CommanderOrderType.GetNameNoFormatting",
                          "Character.IsInBattle"}:
            self.state_calls.append((expression, tuple(bindings)))
            if expression == self.unknown_state_expression:
                query = NS(adapter="fake-data")
                self.capture_queries.append(query)
                error = RuntimeError("original order observation pending")
                error.query = query
                raise error
            if expression.startswith("Character.Get"):
                role = "next" if "Next" in expression else "current"
                handle = self.retain(("held_order", role, None))
                result = NS(action=data.Action.EVALUATE, error=0,
                    kind=data.Kind.RAW if self.raw_order_any else data.Kind.OBJECT,
                    type_id=TYPES["CommanderOrderType"], native_type_name=ORDER_ANY,
                    value=b"original owned payload" if self.raw_order_any else
                        data.NativeObject(0x4000 + handle, TYPES["CommanderOrderType"], handle, ORDER_ANY),
                    value_handle=handle, snapshot=b"original owned payload",
                    snapshot_bytes=22, total=22)
                result = self.state_overrides.get((role, "source"), result)
                self.state_receipts[(role, "source")] = result
                return result
            if expression == "Character.IsInBattle":
                result = self.state_overrides.get("in_battle", scalar(self.in_battle))
                self.state_receipts["in_battle"] = result
                return result
            handle = bindings[0].object
            assert handle in self.handles, "original order Any was released before its read"
            _, role, _ = self.handles[handle]
            valid, name = self.state_values[role]
            field = "valid" if expression.endswith("IsValid") else "name"
            if field == "name":
                assert valid is True, "unsafe name getter on invalid sentinel"
            result = self.state_overrides.get((role, field), scalar(valid if field == "valid" else name))
            self.state_receipts[(role, field)] = result
            return result
        return super().evaluate(native, expression, bindings, **kwargs)


class CommanderOrderStateTests(unittest.IsolatedAsyncioTestCase):
    async def read_state(self, fixture):
        run = _Run(fixture, "test.commander_order_state", namespace="military")
        character = data.ObjectBinding(TYPES["Character"], 999, retained=True)
        with fixture.patches():
            state = await warfare._commander_order_state(run, character)
            await run.cleanup()
        return state, run

    async def test_current_advance_valid_next_defense_and_battle_are_distinct(self):
        fixture = StateFixture()
        with patch.object(commands, "create", side_effect=AssertionError("factory forbidden")), \
                patch.object(commands, "request", side_effect=AssertionError("clone forbidden")), \
                patch.object(data, "invoke_property_action", side_effect=AssertionError("action forbidden")), \
                patch.object(data, "project_page", side_effect=AssertionError("unguarded Page forbidden")):
            state, run = await self.read_state(fixture)
        self.assertEqual(state["status"], "observed")
        self.assertFalse(state["atomic_snapshot"])
        self.assertTrue(state["in_battle"])
        self.assertTrue(state["current"]["valid"])
        self.assertEqual(state["current"]["name_raw"], "推进前线")
        self.assertTrue(state["next"]["valid"])
        self.assertEqual(state["next"]["name_raw"], "防御前线")
        self.assertIsNone(state["current"]["key"])
        self.assertIsNone(state["next"]["key"])
        calls = fixture.state_calls
        self.assertEqual([expression for expression, _ in calls], [
            "Character.GetOrderType", "CommanderOrderType.IsValid", "CommanderOrderType.GetNameNoFormatting",
            "Character.GetNextOrderType", "CommanderOrderType.IsValid", "CommanderOrderType.GetNameNoFormatting",
            "Character.IsInBattle"])
        self.assertEqual(calls[1][1], calls[2][1])
        self.assertEqual(calls[4][1], calls[5][1])
        self.assertNotEqual(calls[1][1], calls[4][1])
        self.assertIs(state["next"]["source_result"], fixture.state_receipts[("next", "source")])
        self.assertEqual(run.status, "complete")
        self.assertFalse(fixture.handles)
        self.assertFalse(fixture.owners)
        self.assertFalse(fixture.clone_requests)

    async def test_false_sentinel_skips_name_and_does_not_fall_back_to_current(self):
        fixture = StateFixture()
        fixture.state_values = {"current": (False, "UNSAFE CURRENT"), "next": (False, "UNSAFE NEXT")}
        fixture.in_battle = False
        state, _ = await self.read_state(fixture)
        for key in ("current", "next"):
            self.assertIs(state[key]["valid"], False)
            self.assertIsNone(state[key]["name"])
            self.assertIsNone(state[key]["name_raw"])
            self.assertFalse(state[key]["name_read"])
            self.assertEqual(state[key]["name_status"], "not_read_invalid")
        self.assertIs(state["in_battle"], False)
        self.assertFalse(any(expression.endswith("GetNameNoFormatting")
                             for expression, _ in fixture.state_calls))

    async def test_unknown_errored_or_partial_validity_never_calls_name(self):
        invalid_receipts = (
            scalar(None, kind=data.Kind.EMPTY),
            scalar(False, error=21),
            scalar(True, error=21),
            scalar(True, total=2),
            scalar(True, snapshot_bytes=2, total=2),
            scalar(0, kind=data.Kind.I32),
        )
        for receipt in invalid_receipts:
            with self.subTest(receipt=receipt):
                fixture = StateFixture()
                fixture.state_overrides[("next", "valid")] = receipt
                state, _ = await self.read_state(fixture)
                self.assertEqual(state["status"], "partial")
                self.assertIs(state["next"]["validity_result"], receipt)
                self.assertIsNone(state["next"]["name_raw"])
                self.assertFalse(state["next"]["name_read"])
                self.assertIn("valid", state["next"]["unavailable"])
                next_handle = state["next"]["source_result"].value_handle
                self.assertFalse(any(expression.endswith("GetNameNoFormatting") and bindings[0].object == next_handle
                                     for expression, bindings in fixture.state_calls))

    async def test_actual_empty_and_errored_partial_text_preserve_original_receipts(self):
        fixture = StateFixture()
        fixture.state_values["current"] = (True, "")
        partial = scalar("unused", error=122, snapshot=b"#bold Def", total=40)
        fixture.state_overrides[("next", "name")] = partial
        state, _ = await self.read_state(fixture)
        self.assertEqual(state["current"]["name_raw"], "")
        self.assertTrue(state["current"]["name_read"])
        self.assertEqual(state["next"]["name_raw"], "#bold Def")
        self.assertTrue(state["next"]["name_read"])
        self.assertIs(state["next"]["name_result"], partial)
        self.assertIn("122", state["next"]["unavailable"]["name"])
        self.assertIn("partial", state["next"]["unavailable"]["name"])

    async def test_known_missing_sources_keep_other_detail_without_dispatch(self):
        fixture = StateFixture()
        for name in ("GetOrderType", "GetNextOrderType", "IsInBattle"):
            fixture.character_fields.pop(name)
        state, _ = await self.read_state(fixture)
        self.assertEqual(state["status"], "partial")
        self.assertEqual(fixture.state_calls, [])
        self.assertIsNone(state["next"]["valid"])
        self.assertIsNone(state["in_battle"])
        self.assertIn("metadata", state["next"]["unavailable"]["source"])

    async def test_error_or_unowned_source_preserves_header_and_never_reads_its_children(self):
        for error, name in ((21, ORDER_ANY), (0, ".?AU?$SAnyType@PEAVCDataCommanderOrderType@@@PdxAnyImpl@@")):
            with self.subTest(error=error, name=name):
                fixture = StateFixture()
                source = NS(action=data.Action.EVALUATE, error=error, kind=data.Kind.RAW,
                            type_id=TYPES["CommanderOrderType"], native_type_name=name,
                            value=b"raw", value_handle=901, snapshot=b"raw", snapshot_bytes=3, total=3)
                fixture.state_overrides[("next", "source")] = source
                fixture.handles[901] = ("held_order", "next", None)
                state, _ = await self.read_state(fixture)
                self.assertIs(state["next"]["source_result"], source)
                self.assertIsNone(state["next"]["valid"])
                self.assertIn("source", state["next"]["unavailable"])
                self.assertFalse(any(bindings and bindings[0].object == 901
                    for expression, bindings in fixture.state_calls if expression.startswith("CommanderOrderType.")))

    async def test_unknown_pending_read_retains_original_sources_and_never_replays(self):
        fixture = StateFixture()
        fixture.unknown_state_expression = "CommanderOrderType.GetNameNoFormatting"
        run = _Run(fixture, "test.unknown_order_state", namespace="military")
        with fixture.patches():
            with self.assertRaisesRegex(RuntimeError, "original order observation pending") as caught:
                await warfare._commander_order_state(run, data.ObjectBinding(TYPES["Character"], 999, retained=True))
        self.assertIs(caught.exception.query, fixture.capture_queries[0])
        self.assertTrue(run.retain_unknown)
        self.assertTrue(run.values)
        self.assertFalse(fixture.releases)
        self.assertEqual(sum(expression == "CommanderOrderType.GetNameNoFormatting"
                             for expression, _ in fixture.state_calls), 1)
        self.assertIn(run, fixture.owner_pool.snapshot())

    async def test_readonly_order_options_add_state_without_extra_key_producers(self):
        fixture = StateFixture(); api = MilitaryAPI(fixture)
        api._pause_for_action = AsyncMock()
        fixture.sources["AccessOrders"] = [Option("advance", "推进前线")]
        with fixture.patches():
            result = await api.commander(fixture.character_id).orders()
        self.assertEqual([row["key"] for row in result["orders"]], ["advance"])
        self.assertEqual(len(fixture.owners), 1)
        self.assertEqual(fixture.owners[0].calls, ["serialize", "destroy"])
        self.assertEqual(result["order_state"]["next"]["name_raw"], "防御前线")
        self.assertFalse(fixture.handles)
        api._pause_for_action.assert_not_awaited()

    async def test_set_order_existing_readback_does_not_call_new_state_helper(self):
        fixture = StateFixture(); api = MilitaryAPI(fixture)
        api._pause_for_action = AsyncMock()
        fixture.sources["AccessOrders"] = [Option("defend", "防御前线")]
        with fixture.patches(), patch.object(warfare, "_commander_order_state",
                side_effect=AssertionError("new automatic order readback forbidden")):
            result = await api.commander(fixture.character_id).set_order('defend')
        self.assertTrue(result["accepted"])
        self.assertEqual(sum(owner.calls.count("validate") for owner in fixture.owners), 1)
        self.assertEqual(sum(owner.calls.count("submit") for owner in fixture.owners), 1)
        self.assertNotIn("order_state", result)





if __name__ == "__main__":
    unittest.main()
