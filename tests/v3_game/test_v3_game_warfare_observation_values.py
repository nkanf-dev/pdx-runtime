"""CPU-only preservation of original native field values beside diagnostics."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_data as data
from pdx_runtime.query_executor import Diagnostics, RetainedOwners
from pdx_runtime.v3_game.military._warfare import WarfareRead, _copied, formation_details


def copied_result(*, kind=data.Kind.STRING, raw_bits=0, snapshot=b"", total=None, error=0):
    raw = bytearray(data.encode_action_request("Type.GetField", data.TypedInput.empty(),
        output_bytes=max(32, len(snapshot))))
    h = list(data.REQUEST.unpack_from(raw))
    h[1], h[2], h[9], h[11], h[19], h[26] = (error, data.Action.EVALUATE,
        len(snapshot) if total is None else total, kind, len(snapshot), raw_bits)
    raw[h[24]:h[24] + len(snapshot)] = snapshot
    data.REQUEST.pack_into(raw, 0, *h)
    return data.DataResult.from_bytes(bytes(raw))


class Context:
    def __init__(self):
        self.native, self.process = object(), object()
        self.adapters, self.world = {"data": 0xA000}, 77
        self.owner_pool, self.diagnostics = RetainedOwners(), Diagnostics()
    async def query(self, label, factory, **options):
        return factory().result


class ObservationValueTests(IsolatedAsyncioTestCase):
    def make_run(self):
        api = N(_ctx=Context())
        run = WarfareRead(api, "military.observation-values")
        run.provider = AsyncMock()
        return run

    async def test_page_kind_mismatch_keeps_actual_false_zero_none_and_raw_cells(self):
        run = self.make_run()
        cells = tuple(data.ProjectionCell(0, index, error, kind, 0, bits, b"", 0)
            for index, (kind, bits, error) in enumerate((
                (data.Kind.BOOL, 0, 21), (data.Kind.I32, 0, 0), (data.Kind.EMPTY, 0, 0))))
        page = data.ProjectionPage(574, 77, 3, 0, 395, 1, True, 1, cells)
        run.page_available = AsyncMock(return_value=True)
        run.page = AsyncMock(return_value=page)
        result = await run.fields("Type", (), (("GetBool", "flag", data.Kind.STRING),
            ("GetZero", "zero", data.Kind.STRING), ("GetNone", "empty", data.Kind.BOOL)))
        self.assertIs(result["flag"], False)
        self.assertEqual(result["zero"], 0)
        self.assertIsNone(result["empty"])
        self.assertIs(result["native_pages"][0], page)
        self.assertIn("native getter error 21", result["unavailable"]["flag"])
        self.assertIn("native page error 574", result["unavailable"]["flag"])
        self.assertIn("requested scalar kind", result["unavailable"]["zero"])
        self.assertEqual(page.cells, cells)

    async def test_fallback_preserves_native_false_and_zero_beside_error(self):
        run = self.make_run()
        for kind, bits, expected in ((data.Kind.BOOL, 0, False), (data.Kind.I32, 0, 0),
                                    (data.Kind.FIXED, 0, Decimal(0)), (data.Kind.EMPTY, 0, None)):
            with self.subTest(kind=kind):
                original = copied_result(kind=kind, raw_bits=bits, error=21)
                with patch.object(data, "evaluate", return_value=N(result=original)):
                    value, reason = await run.field("Type", "GetField", (), (kind,))
                self.assertEqual(value, expected)
                if kind == data.Kind.BOOL:
                    self.assertIs(value, False)
                self.assertIn("native error 21", reason)
                self.assertIs(run.operations[-1][1], original)

    async def test_fallback_native234_text_keeps_original_prefix_and_error(self):
        run = self.make_run()
        original = copied_result(snapshot=b"prefix", total=99, error=234)
        with patch.object(data, "evaluate", return_value=N(result=original)) as evaluate:
            value, reason = await run.field("Type", "GetField", (), (data.Kind.STRING,))
        self.assertEqual(value, "prefix")
        self.assertIn("native error 234", reason)
        self.assertIn("partial", reason)
        self.assertIs(run.operations[-1][1], original)
        evaluate.assert_called_once()

    async def test_fallback_kind_mismatch_returns_actual_copied_value(self):
        run = self.make_run()
        original = copied_result(kind=data.Kind.BOOL, raw_bits=0)
        with patch.object(data, "evaluate", return_value=N(result=original)):
            value, reason = await run.field("Type", "GetField", (), (data.Kind.I32,))
        self.assertIs(value, False)
        self.assertIn("requested scalar kind", reason)

    async def test_fallback_error0_short_actual_text_keeps_prefix_and_partial_reason(self):
        run = self.make_run()
        original = replace(copied_result(snapshot=b"full"), snapshot=b"p")
        with patch.object(data, "evaluate", return_value=N(result=original)):
            value, reason = await run.field("Type", "GetField", (), (data.Kind.STRING,))
        self.assertEqual(value, "p")
        self.assertIn("partial", reason)

    async def test_copied_creation_read_keeps_native234_prefix_and_false_on_error(self):
        run = self.make_run()
        for native, expected in ((copied_result(snapshot=b"prefix", total=99, error=234), "prefix"),
                (copied_result(kind=data.Kind.BOOL, raw_bits=0, error=21), False)):
            with self.subTest(kind=native.kind):
                original = data.ProjectionResult(native, 77, 1, 1, 1, 0)
                with patch.object(data, "evaluate_element", return_value=N(result=original)) as read:
                    value, reason = await _copied(run, "Type.GetRows", 0, 1,
                        "Type.GetField", (), (native.kind,))
                self.assertEqual(value, expected)
                self.assertIn("native error " + str(native.error), reason)
                self.assertIs(run.operations[-1][1], original)
                read.assert_called_once()

    async def test_formation_quality_uses_actual43_specs_in32plus11_pages(self):
        run = self.make_run()
        run.functions = AsyncMock(return_value={"GetNameNoFormatting": object()})
        run.field = AsyncMock(return_value=("name", None))
        run.page_available = AsyncMock(return_value=True)
        pages, requests = [], []
        async def page(fields, bindings, **options):
            requests.append(tuple(fields))
            cells = tuple(data.ProjectionCell(0, index, 0, data.Kind.I32, 0, 0, b"", 0)
                for index in range(len(fields)))
            observed = data.ProjectionPage(0, 77, len(fields), 0, 395, 1, True, 1, cells)
            pages.append(observed)
            return observed
        run.page = page
        with patch("pdx_runtime.v3_game.military._warfare.WarfareRead", return_value=run), \
                patch("pdx_runtime.v3_game.military._warfare._formation", new=AsyncMock(
                    return_value=("army", data.ObjectBinding(37, 101, retained=True)))), \
                patch("pdx_runtime.v3_game.military._warfare._related", new=AsyncMock(return_value=(None, None))), \
                patch("pdx_runtime.v3_game.military.formations._strategic_objective", new=AsyncMock(return_value=(None, None))):
            result = await formation_details(run.api, 367)
        self.assertEqual([len(fields) for fields in requests], [32, 11])
        self.assertEqual(result["native_pages"], pages)
        self.assertIn("MilitaryFormation.GetAverageOffense", requests[0])
        self.assertIn("MilitaryFormation.GetLogisticsCenterSupplyAvailability", requests[1])
        self.assertEqual(result["organization"], 0)
        self.assertEqual(result["organization_daily_change"], 0)
        self.assertFalse(run.values)
