"""Actual RAW DataResult owned-Any admission; no fake NativeObject/pointer/retag."""
from dataclasses import replace
from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pdx_runtime import v3_data as data
from pdx_runtime.query_executor import Diagnostics, RetainedOwners
from pdx_runtime.v3_game._run import Run as _Run
from pdx_runtime.v3_game.military._warfare import WarfareRead
from pdx_runtime.v3_game._choices import OwnedChoices
from pdx_runtime.v3_game._choice_sources import _choice_binding
from pdx_runtime.v3_game.military.forces import _owned_choice_elements

ENTRY_RTTI = ".?AU?$SAnyType@V?$CPdxPtr@USDataCombatUnitEntry@@@@@PdxAnyImpl@@"


def raw_entry(*, handle=901, type_id=311, rtti=ENTRY_RTTI):
    packet = bytearray(data.encode_action_request("MilitaryFormation.GetCombatUnitEntries",
        data.TypedInput.empty(), output_bytes=32))
    h = list(data.REQUEST.unpack_from(packet))
    h[1], h[2], h[4], h[9], h[11], h[14] = 0, data.Action.LIST_ELEMENT, type_id, 8, data.Kind.RAW, handle
    h[13], h[19], h[-1] = 0, 8, rtti.encode().ljust(192, b"\0")
    packet[h[24]:h[24] + 8] = b"rawbytes"
    data.REQUEST.pack_into(packet, 0, *h)
    return data.DataResult.from_bytes(bytes(packet))


class Context:
    def __init__(self):
        self.native, self.process = object(), object()
        self.adapters = {"data": 0xA000}
        self.world = 77
        self.owner_pool, self.diagnostics = RetainedOwners(), Diagnostics()
    async def query(self, label, factory, **options):
        return factory().result
    async def type_id(self, name):
        raise AssertionError("actual source type_id must not be replaced by a named lookup")


def query(result):
    q = object.__new__(data.DataQuery)
    q.adapter, q.result = 0xA000, result
    return q


def source(handle=900):
    return N(action=data.Action.EVALUATE, error=0, kind=data.Kind.LIST, value_handle=handle,
        value=data.NativeList(handle, 0x1000, b"", "native-owned-list"))


class RawOwnedBindingTests(IsolatedAsyncioTestCase):
    async def make_run(self, *, warfare=False):
        ctx = Context()
        run = WarfareRead(N(_ctx=ctx), "raw-entry") if warfare else _Run(ctx, "raw-entry")
        return ctx, run

    async def test_actual_raw_header_admits_original_handle_and_dynamic_type_without_object(self):
        ctx, run = await self.make_run()
        for handle, type_id in ((901, 311), (902, 427)):
            original = raw_entry(handle=handle, type_id=type_id)
            await run.capture("entry", lambda: query(original))
            self.assertEqual(original.kind, data.Kind.RAW)
            self.assertIsInstance(original.value, bytes)
            self.assertNotIsInstance(original.value, data.NativeObject)
            self.assertEqual(original.object, 0)
            self.assertEqual(run.owned_binding(original, "MilitaryFormationUnitEntry"),
                data.ObjectBinding(type_id, handle, retained=True))
            self.assertEqual(original.snapshot, b"rawbytes")
            self.assertEqual(original.object, 0)
            with self.assertRaisesRegex(RuntimeError, "wrapper ownership"):
                run.wrapper(original, "MilitaryFormationUnitEntry")

    async def test_shared_choice_forwarding_keeps_actual_raw_result_and_exact_binding(self):
        ctx, run = await self.make_run()
        original = raw_entry()
        await run.capture("entry", lambda: query(original))
        report = {"rejected_rows": []}
        catalog = OwnedChoices(run, metadata={"result": report})
        # 999 is a stale catalogue hint, never the actual returned dynamic ID.
        binding = _choice_binding(run, catalog, original, "MilitaryFormationUnitEntry", 999, "entry", 0)
        self.assertEqual(binding.type_id, original.type_id)
        choice = catalog.add(original, "MilitaryFormationUnitEntry", binding, {"units": 0})
        self.assertIs(choice.native_result, original)
        self.assertEqual(choice["units"], 0)
        async with choice.borrow(ctx) as lease:
            self.assertEqual(lease.binding, binding)
        with self.assertRaisesRegex(ValueError, "handle/type"):
            catalog.add(original, "MilitaryFormationUnitEntry",
                data.ObjectBinding(999, original.value_handle, retained=True), {})
        self.assertEqual(report["rejected_rows"], [])

    async def test_raw_pointer_old_cdata_alias_unowned_and_missing_adapter_remain_rejected(self):
        ctx, run = await self.make_run()
        original = raw_entry()
        await run.capture("entry", lambda: query(original))
        for bad in (replace(original, native_type_name=".?AU?$SAnyType@PEAUSDataCombatUnitEntry@@@PdxAnyImpl@@"),
                replace(original, native_type_name=".?AU?$SAnyType@V?$CPdxPtr@VCDataMilitaryFormationUnitEntry@@@@@PdxAnyImpl@@"),
                replace(original, value_handle=9999), replace(original, type_id=-1)):
            with self.subTest(bad=bad.native_type_name, handle=bad.value_handle, type_id=bad.type_id):
                with self.assertRaises(RuntimeError):
                    run.owned_binding(bad, "MilitaryFormationUnitEntry")
        run.value_adapters[original.value_handle] = None
        with self.assertRaises(RuntimeError):
            run.owned_binding(original, "MilitaryFormationUnitEntry")

    async def test_warfare_elements_uses_actual_raw_header_without_named_type_lookup(self):
        ctx, run = await self.make_run(warfare=True)
        original = raw_entry()
        with patch.object(data, "evaluate", return_value=query(source())), \
                patch.object(data, "list_count", return_value=query(N(error=0, total=1))), \
                patch.object(data, "list_element", return_value=query(original)):
            rows = [row async for row in run.elements("MilitaryFormation.GetCombatUnitEntries", (),
                "MilitaryFormationUnitEntry")]
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0][1], original)
        self.assertEqual(rows[0][2], data.ObjectBinding(original.type_id, original.value_handle, retained=True))
        self.assertIn(original.value_handle, run.values)

    async def test_force_owned_element_path_admits_raw_entry_and_keeps_real_native_result(self):
        ctx, run = await self.make_run(warfare=True)
        original = raw_entry()
        report = {"source_count": 0, "rejected_rows": []}
        catalog = OwnedChoices(run, metadata={"result": report})
        with patch.object(data, "evaluate", return_value=query(source())), \
                patch.object(data, "list_count", return_value=query(N(error=0, total=1))), \
                patch.object(data, "list_element", return_value=query(original)):
            rows = [row async for row in _owned_choice_elements(run, catalog,
                "MilitaryFormation.GetCombatUnitEntries", (), "MilitaryFormationUnitEntry")]
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0][1], original)
        self.assertEqual(rows[0][2].type_id, original.type_id)
        self.assertEqual(report["source_count"], 1)
        self.assertEqual(report["rejected_rows"], [])

    async def test_retained_admission_never_reads_value_or_guesses_missing_header(self):
        ctx, run = await self.make_run()
        original = raw_entry()
        await run.capture("entry", lambda: query(original))
        class Header:
            native_type_name = ENTRY_RTTI
            type_id = 311
            value_handle = original.value_handle
            @property
            def value(self):
                raise AssertionError("retained admission must not call the host value decoder")
        self.assertEqual(run.owned_binding(Header(), "MilitaryFormationUnitEntry"),
            data.ObjectBinding(311, original.value_handle, retained=True))
        class MissingHeader:
            value_handle = original.value_handle
            @property
            def value(self):
                raise AssertionError("missing header must not trigger decoded-object fallback")
        with self.assertRaises(RuntimeError):
            run.owned_binding(MissingHeader(), "MilitaryFormationUnitEntry")

    async def test_headerless_existing_nativeobject_remains_pointer_wrapper_only(self):
        ctx, run = await self.make_run()
        original = raw_entry()
        await run.capture("entry", lambda: query(original))
        existing = data.NativeObject(0xA100, 311, original.value_handle, ENTRY_RTTI)
        pointer_result = N(value=existing, value_handle=original.value_handle)
        self.assertIs(run.wrapper(pointer_result, "MilitaryFormationUnitEntry"), existing)
        with self.assertRaises(RuntimeError):
            run.owned_binding(pointer_result, "MilitaryFormationUnitEntry")

    async def test_missing_original_producer_does_not_use_changed_role_alias(self):
        ctx, run = await self.make_run()
        original = raw_entry()
        await run.capture("entry", lambda: query(original))
        run.value_adapters.pop(original.value_handle)
        run.adapters["data"] = 0xB000
        with self.assertRaises(RuntimeError):
            run.owned_binding(original, "MilitaryFormationUnitEntry")

    async def test_ship_popup_uses_exact_native_owned_class_alias_not_data_prefix_guess(self):
        ctx, run = await self.make_run()
        rtti = ".?AU?$SAnyType@V?$CPdxPtr@VCShipChangeNamePopup@@@@@PdxAnyImpl@@"
        original = raw_entry(handle=905, type_id=513, rtti=rtti)
        await run.capture("ship-popup", lambda: query(original))
        self.assertEqual(run.owned_binding(original, "ShipChangeNamePopup"),
            data.ObjectBinding(513, 905, retained=True))
        self.assertIsInstance(original.value, bytes)
        for bad_name in (
                ".?AU?$SAnyType@V?$CPdxPtr@VCDataShipChangeNamePopup@@@@@PdxAnyImpl@@",
                ".?AU?$SAnyType@PEAVCShipChangeNamePopup@@@PdxAnyImpl@@"):
            with self.assertRaises(RuntimeError):
                run.owned_binding(replace(original, native_type_name=bad_name), "ShipChangeNamePopup")
