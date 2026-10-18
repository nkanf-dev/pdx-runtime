"""Offline source-local Goods definition observations; no game or native process."""
import unittest
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import patch
from pdx_runtime import v3_data as D
from pdx_runtime.v3_game.economy import goods as G


class Context:
    def __init__(self):
        self.native = object(); self.process = object(); self.world = 77
        self.adapters = {"data": "data"}; self.diagnostics = []; self.calls = []
        self._projection_capabilities = {}
    async def refresh(self): self.calls.append("refresh")
    async def country_id(self): self.calls.append("country_id"); return 0x12340001
    async def type_id(self, name):
        self.calls.append("type:" + name); return {"Country": 31, "Goods": 32}[name]
    async def query(self, label, factory):
        self.calls.append(label)
        return factory().result


def cell(row, col, value=None, *, error=0, total_extra=0, kind=None,
         native_type="CPdxPtr@VCDataGoods@@"):
    field = G.FIELDS[col]
    kind = kind or {"text": D.Kind.STRING, "fixed": D.Kind.FIXED,
        "boolean": D.Kind.BOOL}[field.kind]
    if kind == D.Kind.STRING:
        snapshot = str(value).encode() if value is not None else b""
        bits = 0
    else:
        bits = value if isinstance(value, int) else 0
        snapshot = b""
    return D.ProjectionCell(row, col, error, kind, 32, bits, snapshot,
        len(snapshot) + total_extra, 2, D.Kind.OBJECT, native_type, 0)


def page(rows, *, error=0, count_valid=True, count=None, start=0, world=77,
         selected=None):
    cells = tuple(cell(i + start, j, value) for i, values in enumerate(rows)
        for j, value in enumerate(values)) if selected is None else selected
    length = len(rows); count = length + start if count is None else count
    return D.ProjectionPage(error, world, len(G.FIELDS), start, 123, count,
        count_valid, length, cells)


def native(result): return NS(result=result)


class Tests(unittest.IsolatedAsyncioTestCase):
    async def projected(self, ctx, pages, key="iron"):
        async def supported(_): return True
        async def source_pages(ctx_, label, fields, bindings, sources):
            self.assertEqual(sources, (G.SOURCE,))
            self.assertEqual(fields, tuple(f.expression for f in G.FIELDS))
            self.assertEqual(len(bindings), 1)
            self.assertEqual(bindings[0][0].domain, None)
            self.assertTrue(bindings[0][0].country_id)
            for current in pages:
                yield 0, current, current, current.cells
        with patch.object(G, "_projection_supported", supported), patch.object(
                G, "_projection_source_pages", source_pages):
            return await G.read_goods_definition(ctx, key)

    async def test_exact_same_row_and_false_zero_empty_rich_name(self):
        ctx = Context()
        p = page((("wood", "a", 99999, "wrong", True, False, "wrong"),
                  ("iron", "£Iron <b>rich</b>", 0, "industrial", False, False, "")),
                  count=2)
        result = await self.projected(ctx, (p,))
        self.assertTrue(result["available"])
        self.assertTrue(result["complete"])
        self.assertTrue(result["source_complete"])
        self.assertEqual(result["base_price"], Decimal("0.00000"))
        self.assertEqual(result["name"], "£Iron <b>rich</b>")
        self.assertEqual(result["category"], "industrial")
        self.assertIs(result["is_local"], False)
        self.assertIs(result["is_tradeable"], False)
        self.assertEqual(result["demand_use_description"], "")
        self.assertEqual(result["selected"]["provider_index"], 1)
        self.assertNotIn("id", result)

    async def test_partial_key_never_certifies_identity_and_no_cross_source(self):
        ctx = Context()
        first = page((), selected=(cell(0, 0, "iron", total_extra=5),),
            count=2, error=123)
        next_page = page((("iron", "second", 50000, "second-category", True, False, "ok"),),
            start=1, count=2)
        result = await self.projected(ctx, (first, next_page))
        self.assertEqual(result["selected"]["provider_index"], 1)
        self.assertEqual(result["name"], "second")
        self.assertFalse(result["complete"])
        self.assertEqual(result["candidates"][0]["observations"]["key"]["snapshot"], b"iron")
        self.assertIn("_source", result["reasons"])

    async def test_native_error_coexists_with_copied_value_but_key_error_not_identity(self):
        ctx = Context()
        p = page((("iron", "Iron", 99999, "basic", False, True, "used"),),
            selected=tuple(cell(0, i, value, error=93 if i == 2 else 0)
                for i, value in enumerate(("iron", "Iron", 99999, "basic", False, True, "used"))))
        result = await self.projected(ctx, (p,))
        self.assertTrue(result["available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["base_price"], Decimal("0.99999"))
        self.assertIn("native getter error 93", result["reasons"]["base_price"])
        bad = page((), selected=(cell(0, 0, "iron", error=93),), count=1)
        result = await self.projected(Context(), (bad,))
        self.assertFalse(result["available"])
        self.assertEqual(result["candidates"][0]["key"], "iron")
        self.assertFalse(result["complete"])

    async def test_empty_source_failure_world_and_binding_evidence(self):
        empty = await self.projected(Context(), (page((), count=0),))
        self.assertFalse(empty["available"])
        self.assertIsNone(empty["key"])
        self.assertEqual(empty["requested_key"], "iron")
        self.assertTrue(empty["source_complete"])
        self.assertTrue(empty["complete"])
        self.assertIn("this source", empty["reason"])
        failed = await self.projected(Context(), (page((), error=44, count_valid=False),))
        self.assertFalse(failed["source_complete"])
        self.assertFalse(failed["complete"])
        wrong = page((), selected=tuple(cell(0, i, val, native_type="CPdxPtr@VCDataOther")
            for i, val in enumerate(("iron", "bad", 100000, "basic", False, True, ""))),
            count=1, world=88)
        result = await self.projected(Context(), (wrong,))
        self.assertFalse(result["available"])
        self.assertFalse(result["world_consistent"])
        self.assertIn("Goods", result["candidates"][0]["reasons"]["_binding"])

    async def test_original_projection_query_partial_result_is_not_reissued(self):
        ctx = Context(); calls = []
        partial = page((), selected=tuple(cell(0, i, v) for i, v in enumerate(
            ("iron", "copied", 120000, "basic", False, True, ""))), count=1)
        original = NS(partial_result=partial)
        class Interrupted(RuntimeError): pass
        error = Interrupted("transport interrupted"); error.query = original
        async def supported(_): return True
        async def source_pages(*args):
            calls.append("submit")
            raise error
            yield
        with patch.object(G, "_projection_supported", supported), patch.object(
                G, "_projection_source_pages", source_pages):
            with self.assertRaises(Interrupted) as caught:
                await G.read_goods_definition(ctx, "iron")
        self.assertIs(caught.exception.query, original)
        self.assertEqual(calls, ["submit"])
        self.assertEqual(caught.exception.goods_observations["name"], "copied")

    async def test_old_adapter_one_read_source_elements_fields_and_cleanup(self):
        ctx = Context(); original_calls = []
        retained = D.NativeObject(0xAABB, 32, 303, "CPdxPtr@VCDataGoods@@")
        async def supported(_): return False
        def evaluate(_, expression, bindings, **opts):
            original_calls.append((expression, bindings))
            if expression == G.SOURCE:
                self.assertTrue(bindings[0].country_id)
                return native(NS(error=0, kind=D.Kind.LIST, value_handle=101,
                    world=77, total=0))
            self.assertEqual(bindings[0], D.ObjectBinding(32, 303, retained=True))
            field = next(f for f in G.FIELDS if f.expression == expression)
            position = G.FIELDS.index(field)
            value = ("iron", "Iron", 0, "basic", False, False, "")[position]
            c = cell(0, position, value)
            return native(NS(error=0, kind=c.kind, value_handle=0, type_id=32,
                raw_bits=c.raw_bits, snapshot=c.snapshot, total=c.total,
                world=77))
        def list_count(_, handle, **opts):
            self.assertEqual(handle, 101)
            return native(NS(error=0, total=1, value_handle=0, world=77))
        def list_element(_, handle, index, **opts):
            self.assertEqual((handle, index), (101, 0))
            return native(NS(error=0, kind=D.Kind.OBJECT, value_handle=303,
                value=retained, native_type_name=retained.native_type_name, world=77))
        def release(_, handle, **opts):
            ctx.calls.append("release:" + str(handle))
            return native(NS(error=0, value_handle=0, world=77))
        with patch.object(G, "_projection_supported", supported), patch.object(
                D, "evaluate", evaluate), patch.object(D, "list_count", list_count), patch.object(
                D, "list_element", list_element), patch.object(D, "release", release):
            result = await G.read_goods_definition(ctx, "iron")
        self.assertTrue(result["available"])
        self.assertEqual(result["base_price"], Decimal("0.00000"))
        self.assertEqual(original_calls[0][0], G.SOURCE)
        self.assertEqual(len(original_calls), 1 + len(G.FIELDS))
        self.assertEqual(ctx.calls.count("type:Country"), 1)
        self.assertEqual(ctx.calls.count("type:Goods"), 1)
        self.assertIn("release:101", ctx.calls)
        self.assertIn("release:303", ctx.calls)
        self.assertGreater(ctx.calls.index("release:303"), ctx.calls.index("economy.goods_definition.demand_use_description"))

    async def test_ordinary_page_cursor_not_grouped_range_complete(self):
        first = page((("iron", "Iron", 0, "basic", False, True, ""),), count=3)
        self.assertFalse(first.range_complete)  # Real ordinary wire default.
        result = await self.projected(Context(), (first,))
        self.assertTrue(result["available"])
        self.assertTrue(result["complete"])
        self.assertFalse(result["source_complete"])
        self.assertTrue(result["source_observations"][0]["has_more"])
        self.assertNotIn("_source", result["reasons"])
        self.assertEqual(result["values"]["key"], "iron")

    async def test_partial_fallback_keeps_exact_key_and_original_owner(self):
        ctx = Context(); original = NS(partial_result=None)
        failure = OSError("original field still pending"); failure.query = original
        retained = D.NativeObject(0xAABB, 32, 303, "CPdxPtr@VCDataGoods@@")
        def evaluate(_, expression, bindings, **opts):
            if expression == G.SOURCE:
                return native(NS(error=0, kind=D.Kind.LIST, value_handle=101, world=77))
            if expression == "Goods.GetKey":
                return native(NS(error=0, kind=D.Kind.STRING, value_handle=404,
                    type_id=32, raw_bits=0, snapshot=b"iron", total=4, world=77))
            raise failure
        with patch.object(G, "_projection_supported", return_value=False), \
             patch.object(D, "evaluate", side_effect=evaluate) as evaluated, \
             patch.object(D, "list_count", return_value=native(NS(error=0, total=1, value_handle=0, world=77))), \
             patch.object(D, "list_element", return_value=native(NS(error=0, kind=D.Kind.OBJECT,
                 value_handle=303, value=retained, native_type_name=retained.native_type_name, world=77))), \
             patch.object(D, "release") as released:
            with self.assertRaises(OSError) as caught:
                await G.read_goods_definition(ctx, "iron")
        self.assertIs(caught.exception, failure)
        self.assertIs(caught.exception.query, original)
        observed = caught.exception.goods_observations
        self.assertTrue(observed["available"])
        self.assertFalse(observed["complete"])
        self.assertEqual(observed["key"], "iron")
        self.assertEqual(observed["observations"]["key"]["snapshot"], b"iron")
        self.assertEqual(evaluated.call_count, 3)
        released.assert_not_called()
        self.assertEqual(ctx._country_observation_reads[0].owners, [101, 303, 404])


    async def test_invalid_keys_rejected_before_native(self):
        for bad in ("", "a\0b", 3, None, True):
            ctx = Context()
            with self.assertRaises(ValueError): await G.read_goods_definition(ctx, bad)
            self.assertEqual(ctx.calls, [])
        val = await self.projected(Context(), (page(((" İron ", "v", 1, "a", False, True, ""),)),), " İron ")
        self.assertTrue(val["available"])
        self.assertEqual(val["key"], " İron ")


if __name__ == "__main__":
    unittest.main()
