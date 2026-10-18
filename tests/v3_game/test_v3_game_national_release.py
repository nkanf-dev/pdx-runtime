"""CPU-only original CountryCreation -> Action22 release workflow contracts.

The real context factory, DataQuery decode, _Run capture and generated selected
helper execute. Native callbacks/command receiver are deterministic fixtures;
these tests do not prove any game effect or target-build runtime behavior.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.query_executor import PendingOperation, SerializedQueryExecutor
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.politics import national
from pdx_runtime.v3_game._choices import ChoicesBusyError
from tests.v3_game import test_v3_game_national as national_tests

Query, cell = national_tests.Query, national_tests.cell


class Fixture(national_tests.Fixture):
    context_handle, context_type, data_base = 901, 1766, 0xA000
    def __init__(self):
        super().__init__()
        self.events, self.window_reads = [], 0
        self.options = {"make_independent": True, "make_own_subject": False,
                        "play_as_released": False, "keep_homeland_states": False}
        self.window_transform = lambda page: page
        self.readback_error = None
        for field in (*national._RELEASE_WINDOW_FIELDS, *national._RELEASE_STATES, *national._RELEASE_PLAYER):
            self.kinds[field.expression] = field.kind
            self.defaults.setdefault(field.expression,
                "original text" if field.kind == data.Kind.STRING else True if field.kind == data.Kind.BOOL else 0)
        self.defaults.update({"ReleaseCountryWindow.CanMakeIndependent": True,
            "ReleaseCountryWindow.CanMakeOwnSubject": True,
            "And(ReleaseCountryWindow.CanMakeIndependent,ReleaseCountryWindow.CanMakeOwnSubject)": True,
            "ReleaseCountryWindow.CanPlayAsReleased": True,
            "ReleaseCountryWindow.CanReleaseWithKeepHomelandStates": True})
        self.memory, self.freed, self.exports, self.headers = {}, [], [], []
        self.context_error, self.context_rtti = 0, ".?AU?$SAnyType@V?$CPdxPtr@VCReleaseCountryWindow@@@@@PdxAnyImpl@@"
        self.context_early_error = 0
        self.context_pointer, self.context_output = 0xABC0, self.context_handle
        self.callback_pending, self.dispatch_error, self.storage_cleanup_error = False, None, None
        self.dispatches, self.started, self.started_loop = 0, None, None
        self.ctx.native = NS(pid=1, request=self.request)
        self.ctx.process = NS(pid=1, allocate=self.allocate, write=self.write, read=self.read,
            free=self.free, resolve_export=self.resolve_export, export_owner=lambda callback: self.data_base)
        self.ctx.adapters.update(data=self.data_base, commands="original-commands")

    def allocate(self, size):
        address = 0x20000 + len(self.memory) * 0x10000
        self.memory[address] = bytes(size)
        return address
    def write(self, address, raw): self.memory[address] = raw
    def read(self, address, size): return self.memory[address][:size]
    def free(self, address):
        if self.storage_cleanup_error is not None:
            raise self.storage_cleanup_error
        self.freed.append(address)
    def resolve_export(self, adapter, symbol, **kwargs):
        self.exports.append((adapter, symbol))
        assert symbol == "PdxV3DataCreateOwnedContextInterface"
        return 0xA100
    def request(self, command):
        if command.startswith("interface-callback "):
            self.dispatches += 1
            self.events.append(("context", command))
            address = max(self.memory)
            raw = bytearray(self.memory[address])
            header = list(data.REQUEST.unpack_from(raw))
            self.headers.append(tuple(header))
            assert header[2] == data.Action.CREATE_OWNED_CONTEXT and header[3] == 1
            # Native revision2 takes the original input before every early
            # return. An unproduced context must never echo that catalog Any.
            header[14] = 0
            if self.context_early_error:
                header[1] = self.context_early_error
            else:
                header[1], header[4], header[11] = self.context_error, self.context_type, data.Kind.OBJECT
                header[13], header[14] = self.context_pointer, self.context_output
                header[28] = self.context_rtti.encode().ljust(192, b"\0")
            data.REQUEST.pack_into(raw, 0, *header)
            self.memory[address] = bytes(raw)
            if header[14]:
                self.backend.live.add(header[14])
            if self.started is not None:
                self.started_loop.call_soon_threadsafe(self.started.set)
            if self.dispatch_error is not None:
                raise self.dispatch_error
            return {"status": "ok", "request_id": 7, "error": 0}
        return {"status": "ok", "completed_request_id": 6 if self.callback_pending else 7}

    def project(self, native, fields, binding_rows, **kwargs):
        fields, rows = tuple(fields), tuple(binding_rows)
        if rows == ((),) and kwargs.get("source_expression") is None:
            if self.readback_error is not None:
                raise self.readback_error
            self.calls.append((fields, rows, kwargs))
            self.events.append(("player_read",))
            payload = {}
        elif rows and len(rows[0]) == 1 and rows[0][0].retained:
            self.calls.append((fields, rows, kwargs))
            window, = rows[0]
            assert (window.type_id, window.object, window.retained) == (self.context_type, self.context_handle, True)
            assert kwargs["adapter"] == self.data_base
            assert kwargs["process"] is self.ctx.process and native is self.ctx.native
            self.window_reads += 1
            self.events.append(("flags", dict(self.options)))
            payload = {"ReleaseCountryWindow." + {"make_independent": "WillMakeIndependent",
                "make_own_subject": "WillMakeOwnSubject", "play_as_released": "WillPlayAsReleased",
                "keep_homeland_states": "WillKeepHomelandStates"}[name]: value
                for name, value in self.options.items()}
        else:
            return super().project(native, fields, rows, **kwargs)
        cells = tuple(cell(payload.get(expression, self.defaults[expression]), self.kinds[expression], 0, i)
                      for i, expression in enumerate(fields))
        page = data.ProjectionPage(0, self.world, len(fields), 0, 45, 1, True, 1, cells)
        if rows[0]: page = self.window_transform(page)
        return self.query_factory(self.transform(page))


class ReleaseTests(unittest.IsolatedAsyncioTestCase):
    formation_native_source = national_tests.NationalTests.formation_native_source
    def setUp(self):
        self.fx = Fixture()
        self.patch(patch.object(data, "project_page", self.fx.project))
        self.owner = None
        async def query(label, factory, *, owner=None):
            query = factory()
            return owner.complete() if owner is not None else query.read()
        self.fx.ctx.query = query

    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    async def catalog(self, records=None):
        self.fx.backend = self.formation_native_source(records, named_type="CountryCreation")
        catalog = await self.fx.api.release_choices()
        source_evaluate = data.evaluate
        fx = self.fx
        self.validation = NS(error=0, validated=True, reason="")
        self.submission = NS(error=0, accepted=True, command=0)
        self.toggle_error, self.no_op, self.toggle_fail, self.submit_hook = 0, set(), None, None
        self.toggle_handle = 0
        self.pending_factory = Query
        outer = self
        class Owner:
            world, command, pending, attempt = fx.world, 0x3000, None, None
            def validate(self, capacity):
                fx.events.append(("validate", capacity))
                self.attempt = NS(outcome="owned")
                self.pending = outer.pending_factory(outer.validation)
                return self.pending
            def submit(self, flags=4):
                fx.events.append(("submit", flags))
                self.command, self.attempt = outer.submission.command, NS(outcome="consumed" if not outer.submission.command else "unknown")
                if outer.submit_hook is not None: outer.submit_hook()
                self.pending = outer.pending_factory(outer.submission)
                return self.pending
            def destroy(self):
                fx.events.append(("destroy",))
                self.command = 0
                self.pending = Query(NS(error=0, command=0))
                return self.pending
            def complete(self):
                if self.pending is None: return None
                result = self.pending.read()
                if result is not None: self.pending = None
                return result
            def serialize(self, *args, **kwargs): raise AssertionError("No serialization receipt")
        self.owner = Owner()
        carrier = data.NativeDataCommand(0x1000, 0x1100, 0x1018, 950, "fixture", fx.world)
        def evaluate(native, expression, bindings, **kwargs):
            if expression != "ReleaseCountryWindow.ReleaseCountry":
                return source_evaluate(native, expression, bindings, **kwargs)
            window, = bindings
            assert (window.type_id, window.object, window.retained) == (fx.context_type, fx.context_handle, True)
            assert kwargs["adapter"] == fx.data_base
            fx.events.append(("produce", expression, tuple(bindings)))
            fx.backend.live.add(950)
            return Query(NS(error=0, action=data.Action.EVALUATE, value_handle=950, value=carrier))
        def invoke(native, expression, bindings, **kwargs):
            window, = bindings
            assert (window.type_id, window.object, window.retained) == (fx.context_type, fx.context_handle, True)
            assert kwargs["adapter"] == fx.data_base
            name = next(name for name, _, expr in national._RELEASE_TOGGLES if expr == expression)
            fx.events.append(("toggle", name))
            if outer.toggle_fail is not None: raise outer.toggle_fail
            if name not in outer.no_op:
                fx.options[name] = not fx.options[name]
                if name == "make_independent": fx.options["make_own_subject"] = not fx.options[name]
                elif name == "make_own_subject": fx.options["make_independent"] = not fx.options[name]
            if outer.toggle_handle: fx.backend.live.add(outer.toggle_handle)
            return Query(NS(error=outer.toggle_error, action=data.Action.INVOKE_ACTION, value_handle=outer.toggle_handle, value=None))
        self.patch(patch.object(data, "evaluate", evaluate))
        self.patch(patch.object(data, "invoke_property_action", invoke))
        self.patch(patch.object(data, "command_value", return_value=Query(NS(error=0,
            action=data.Action.COMMAND_VALUE, value_handle=950, value=carrier))))
        self.clone = self.patch(patch.object(commands, "request", return_value=Query(NS(error=0,
            action=commands.Action.CLONE, cloned_command=0x3000, type_token=0x532c, world=fx.world))))
        def adopt(*args, **kwargs):
            self.owner.command, self.owner.attempt = 0x3000, None
            return self.owner
        self.patch(patch.object(commands.NativeCommand, "from_result", side_effect=adopt))
        return catalog

    def action_runs(self):
        return [run for run in self.fx.ctx.diagnostics if isinstance(run, run_mod.Run) and run.label == "release_country"]
    def events(self, name): return [e for e in self.fx.events if e[0] == name]

    async def test_real_factory_wire_dynamic_type_original_handle_and_one_canonical_run(self):
        catalog = await self.catalog([{}, {}])
        original_source = list(self.fx.backend.calls)
        # A later default Data DLL must not retarget the existing choice registry.
        self.fx.ctx.adapters["data"] = "other-data.dll"
        result = await self.fx.api.release_country(catalog[1])
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(self.fx.headers[0][14], 201)
        self.assertEqual(self.fx.headers[0][2:4], (22, 1))
        self.assertEqual(self.fx.exports, [(self.fx.data_base, "PdxV3DataCreateOwnedContextInterface")])
        self.assertEqual(result["context_result"].type_id, self.fx.context_type)
        self.assertEqual(result["context_result"].value.native_type_name,
            ".?AU?$SAnyType@V?$CPdxPtr@VCReleaseCountryWindow@@@@@PdxAnyImpl@@")
        self.assertEqual(self.fx.dispatches, 1)
        self.assertEqual(len(self.action_runs()), 1)
        self.assertEqual(self.action_runs()[0].status, "complete")
        self.assertEqual([h for h, _ in self.fx.backend.released], [950, 901])
        self.assertTrue(all(options["adapter"] == self.fx.data_base for _, options in self.fx.backend.released))
        self.assertEqual([e[0] for e in self.fx.events], ["context", "flags", "produce", "validate", "submit", "player_read"])
        self.assertIs(result["validation"], self.validation)
        self.assertIs(result["submission"], self.submission)
        self.assertEqual(result["catalog_country_id"], self.fx.country_id)
        self.assertEqual(result["initial_window"]["rows"][0]["actor_country_id"], 0x99000009)
        self.assertEqual(result["after"]["player"]["rows"][0]["id"], 0x99000009)
        self.assertEqual(catalog.active_leases, 0)
        self.assertEqual(self.fx.performs, ["politics.release_choices", "politics.release_country"])
        self.assertEqual([e for e in self.fx.backend.calls if e[0] != "release"], original_source)
        self.assertEqual(self.fx.type_calls, [])
        self.assertEqual(catalog[1].native_result.type_id, 41)
        self.assertEqual(catalog[1].native_result.value_handle, 201)
        self.assertEqual((catalog[1]._binding.type_id, catalog[1]._binding.object),
                         (catalog[1].native_result.type_id, catalog[1].native_result.value_handle))
        self.clone.assert_called_once()
        await catalog.close()
        self.assertEqual(self.fx.backend.live, set())


    async def test_coupled_radio_reread_avoids_stale_second_toggle(self):
        catalog = await self.catalog()
        self.fx.options.update(make_independent=False, make_own_subject=True)
        result = await self.fx.api.release_country(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertEqual(self.events("toggle"), [("toggle", "make_independent")])
        self.assertTrue(result["final_window"]["rows"][0]["make_independent"])
        self.assertFalse(result["final_window"]["rows"][0]["make_own_subject"])
        await catalog.close()

    async def test_explicit_false_play_as_is_applied_not_ignored(self):
        catalog = await self.catalog()
        self.fx.options["play_as_released"] = True
        result = await self.fx.api.release_country(catalog[0], play_as=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.events("toggle"), [("toggle", "play_as_released")])
        self.assertFalse(result["final_window"]["rows"][0]["play_as_released"])
        await catalog.close()

    async def test_missing_error_and_wrong_kind_flags_are_not_false(self):
        catalog = await self.catalog()
        for index in range(4):
            for mode in ("missing", "error", "kind"):
                with self.subTest(index=index, mode=mode):
                    def partial(page):
                        cells = tuple(c for c in page.cells if not (mode == "missing" and c.field == index))
                        return replace(page, cells=tuple(replace(c, error=50) if mode == "error" and c.field == index
                            else replace(c, kind=data.Kind.EMPTY) if mode == "kind" and c.field == index else c for c in cells))
                    self.fx.window_transform = partial
                    result = await self.fx.api.release_country(catalog[0])
                    self.assertEqual(result["status"], "options_unavailable")
                    self.assertIsNone(result["accepted"])
                    self.assertEqual(result["updates"], [])
        self.assertEqual(self.events("produce"), [])
        await catalog.close()

    async def test_each_original_ui_predicate_blocks_only_its_needed_toggle(self):
        catalog = await self.catalog()
        conditions = {field.name: field.expression for field in national._RELEASE_WINDOW_FIELDS}
        cases = (("can_toggle_independent", "make_independent", True),
                 ("can_make_own_subject", "make_own_subject", True),
                 ("can_play_as_released", "play_as_released", True),
                 ("can_keep_homeland_states", "keep_homeland_states", True))
        for condition, name, _ in cases:
            self.fx.options.update(make_independent=True, make_own_subject=False,
                                   play_as_released=False, keep_homeland_states=False)
            if name == "make_independent": self.fx.options.update(make_independent=False, make_own_subject=True)
            if name == "make_own_subject": self.fx.options.update(make_independent=False, make_own_subject=False)
            desired = {"independent": name != "make_own_subject", "play_as": name == "play_as_released",
                       "keep_homeland_states": name == "keep_homeland_states"}
            self.fx.defaults[conditions[condition]] = False
            result = await self.fx.api.release_country(catalog[0], **desired)
            self.assertEqual(result["status"], "option_rejected")
            self.assertEqual(result["blocked_option"], name)
            self.fx.defaults[conditions[condition]] = True
        self.assertEqual(self.events("produce"), [])
        self.assertEqual(conditions["can_toggle_independent"],
            "And(ReleaseCountryWindow.CanMakeIndependent,ReleaseCountryWindow.CanMakeOwnSubject)")
        await catalog.close()

    async def test_partial_toggle_output_any_is_captured_once_by_shared_owner(self):
        catalog = await self.catalog()
        self.toggle_error, self.toggle_handle = 234, 920
        result = await self.fx.api.release_country(catalog[0], play_as=True)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["updates"][0]["invocation"].value_handle, 920)
        self.assertEqual([h for h, _ in self.fx.backend.released], [950, 920, 901])
        self.assertEqual(self.action_runs()[0].values, [])
        await catalog.close()

    async def test_unknown_toggle_keeps_original_context_and_catalog_no_replay(self):
        catalog = await self.catalog()
        error = OSError("original toggle delivery unknown")
        self.toggle_fail = error
        with self.assertRaises(OSError) as raised:
            await self.fx.api.release_country(catalog[0], play_as=True)
        self.assertIs(raised.exception, error)
        run, = self.action_runs()
        self.assertTrue(run.retain_unknown)
        self.assertEqual(run.values, [901])
        self.assertEqual(self.fx.dispatches, 1)
        self.assertEqual(self.events("toggle"), [("toggle", "play_as_released")])
        self.assertEqual(self.events("produce"), [])
        self.assertEqual(self.fx.backend.released, [])
        with self.assertRaises(ChoicesBusyError): await catalog.close()

    async def test_missing_flag_after_toggle_retains_partial_copy_and_blocks_release(self):
        catalog = await self.catalog()
        def after_toggle(page):
            if self.fx.window_reads > 1:
                return replace(page, cells=tuple(c for c in page.cells if c.field != 2))
            return page
        self.fx.window_transform = after_toggle
        result = await self.fx.api.release_country(catalog[0], play_as=True)
        self.assertEqual(result["status"], "options_not_applied")
        self.assertEqual(result["option_mismatches"], {"play_as_released": None})
        self.assertIn("did not copy", result["final_window"]["rows"][0]["reasons"]["play_as_released"])
        self.assertTrue(result["updates"][0]["after"]["rows"][0]["make_independent"])
        self.assertEqual(self.events("toggle"), [("toggle", "play_as_released")])
        self.assertEqual(self.events("produce"), [])
        await catalog.close()

    async def test_own_subject_uses_its_allowed_radio_when_independent_control_is_disabled(self):
        catalog = await self.catalog()
        self.fx.defaults["ReleaseCountryWindow.CanMakeIndependent"] = False
        self.fx.defaults["And(ReleaseCountryWindow.CanMakeIndependent,ReleaseCountryWindow.CanMakeOwnSubject)"] = False
        result = await self.fx.api.release_country(catalog[0], independent=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.events("toggle"), [("toggle", "make_own_subject")])
        self.assertFalse(result["final_window"]["rows"][0]["make_independent"])
        self.assertTrue(result["final_window"]["rows"][0]["make_own_subject"])
        self.assertEqual(self.events("submit"), [("submit", 4)])
        await catalog.close()

    async def test_disabled_predicates_do_not_block_already_applied_requested_options(self):
        catalog = await self.catalog()
        for field in national._RELEASE_WINDOW_FIELDS:
            if field.name.startswith("can_"): self.fx.defaults[field.expression] = False
        result = await self.fx.api.release_country(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["updates"], [])
        self.assertEqual(self.events("toggle"), [])
        self.assertEqual(self.events("submit"), [("submit", 4)])
        await catalog.close()

    async def test_unknown_predicate_does_not_authorize_toggle(self):
        catalog = await self.catalog()
        self.fx.options["play_as_released"] = True
        index = next(i for i, f in enumerate(national._RELEASE_WINDOW_FIELDS) if f.name == "can_play_as_released")
        self.fx.window_transform = lambda page: replace(page, cells=tuple(replace(c, error=50) if c.field == index else c for c in page.cells))
        result = await self.fx.api.release_country(catalog[0])
        self.assertEqual(result["status"], "options_unavailable")
        self.assertEqual(self.events("toggle"), [])
        self.assertEqual(self.events("produce"), [])
        await catalog.close()

    async def test_no_op_toggle_never_authorizes_release_or_retries(self):
        catalog = await self.catalog()
        self.no_op.add("play_as_released")
        result = await self.fx.api.release_country(catalog[0], play_as=True)
        self.assertEqual(result["status"], "options_not_applied")
        self.assertEqual(result["option_mismatches"], {"play_as_released": False})
        self.assertEqual(self.events("toggle"), [("toggle", "play_as_released")])
        self.assertEqual(self.events("produce"), [])
        await catalog.close()

    async def test_known_toggle_error_retains_native_result_and_actual_flags(self):
        catalog = await self.catalog()
        self.toggle_error = 234
        result = await self.fx.api.release_country(catalog[0], play_as=True)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["updates"][0]["invocation"].error, 234)
        self.assertTrue(result["final_window"]["rows"][0]["play_as_released"])
        self.assertTrue(any(native is result["updates"][0]["invocation"] for _, native in result["native_results"]))
        await catalog.close()

    async def test_partial_context_error_keeps_owned_any_and_exact_original_result(self):
        catalog = await self.catalog()
        self.fx.context_error = 234
        result = await self.fx.api.release_country(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["context_result"].error, 234)
        self.assertEqual(result["context_result"].value_handle, 901)
        self.assertIs(result["native_results"][0][1], result["context_result"])
        self.assertEqual([h for h, _ in self.fx.backend.released], [950, 901])
        await catalog.close()

    async def test_early_factory_error21_clears_output_without_adopting_original_catalog_handle(self):
        catalog = await self.catalog([{}, {}])
        self.fx.context_early_error = 21
        original_source_values = tuple(catalog.owner.values)
        result = await self.fx.api.release_country(catalog[1])
        made = result["context_result"]
        self.assertIsInstance(made, data.DataResult)
        self.assertEqual(self.fx.headers[0][14], 201)
        self.assertEqual((made.action, made.error, made.value_handle), (data.Action.CREATE_OWNED_CONTEXT, 21, 0))
        self.assertEqual((made.type_id, made.native_type_name, made.object), (-1, "", 0))
        self.assertEqual(made.kind, data.Kind.RAW)
        self.assertEqual(made.value, b"")
        self.assertEqual(result["status"], "native_result")
        self.assertEqual(result["native_error"], 21)
        self.assertIsNone(result["accepted"])
        self.assertIsNone(result["validation"])
        self.assertIsNone(result["submission"])
        self.assertFalse(result["completed"])
        self.assertIs(result["native_result"], made)
        self.assertIs(result["native_results"][0][1], made)
        run, = self.action_runs()
        self.assertEqual(run.values, [])
        self.assertEqual(run.value_adapters, {})
        self.assertEqual(run.owners, [])
        self.assertEqual(run.status, "complete")
        self.assertNotIn(run, self.fx.ctx.owner_pool.snapshot())
        self.assertEqual(tuple(catalog.owner.values), original_source_values)
        self.assertIn(catalog.owner, self.fx.ctx.owner_pool.snapshot())
        self.assertEqual(self.fx.backend.live, {100, 200, 201})
        self.assertEqual(self.fx.backend.released, [])
        self.assertEqual(catalog.active_leases, 0)
        self.assertEqual(self.fx.window_reads, 0)
        self.assertEqual(self.events("toggle"), [])
        self.assertEqual(self.events("produce"), [])
        self.assertEqual(self.events("submit"), [])
        closed = await catalog.close()
        self.assertTrue(closed["closed"])
        self.assertEqual([handle for handle, _ in self.fx.backend.released], [201, 200, 100])
        self.assertEqual(self.fx.backend.live, set())

    async def test_other_factory_error_preserves_published_any_without_flag_or_submit(self):
        catalog = await self.catalog()
        self.fx.context_error = 50
        result = await self.fx.api.release_country(catalog[0])
        self.assertEqual(result["status"], "native_result")
        self.assertEqual(result["native_error"], 50)
        self.assertEqual(result["context_result"].value_handle, 901)
        self.assertEqual(self.fx.window_reads, 0)
        self.assertEqual(self.events("produce"), [])
        self.assertEqual([h for h, _ in self.fx.backend.released], [901])
        await catalog.close()

    async def test_raw_wrong_cdata_null_and_missing_context_outputs_are_not_retagged(self):
        catalog = await self.catalog()
        exact = self.fx.context_rtti
        for rtti, pointer, handle, type_id, error in (
            (".?AU?$SAnyType@PEAVCReleaseCountryWindow@@@PdxAnyImpl@@", 0xABC0, 901, 1766, 0),
            (".?AU?$SAnyType@V?$CPdxPtr@VCDataReleaseCountryWindow@@@@@PdxAnyImpl@@", 0xABC0, 901, 1766, 0),
            ("", 0, 0, -1, 0), ("", 0, 901, 1766, 0),
            (exact, 0xABC0, 0, 1766, 0), (exact, 0, 901, -1, 0),
            (exact, 0, 901, 1766, 50)):
            with self.subTest(rtti=rtti, pointer=pointer, handle=handle, type_id=type_id, error=error):
                self.fx.context_rtti, self.fx.context_pointer, self.fx.context_output = rtti, pointer, handle
                self.fx.context_type, self.fx.context_error = type_id, error
                result = await self.fx.api.release_country(catalog[0])
                self.assertEqual(result["status"], "native_result")
                self.assertIsNone(result["accepted"])
                self.assertIs(result["native_result"], result["context_result"])
                self.assertEqual(result["context_result"].error, error)
                self.assertEqual(result["context_result"].value_handle, handle)
        self.assertEqual(self.fx.window_reads, 0)
        self.assertEqual(self.events("produce"), [])
        # A host decoder's zero object is not a native null verdict when the
        # original owned header, dynamic type and held Any are all genuine.
        self.fx.context_rtti, self.fx.context_pointer, self.fx.context_output = exact, 0, 901
        self.fx.context_type, self.fx.context_error = 1766, 0
        observed = await self.fx.api.release_country(catalog[0])
        self.assertTrue(observed["accepted"])
        self.assertEqual(observed["context_result"].object, 0)
        self.assertEqual(self.events("submit"), [("submit", 4)])
        await catalog.close()

    async def test_native_validation_rejection_and_submit_error_remain_original(self):
        catalog = await self.catalog()
        self.validation.validated, self.validation.reason = False, "native release rule"
        rejected = await self.fx.api.release_country(catalog[0])
        self.assertIs(rejected["validation"], self.validation)
        self.assertEqual(rejected["status"], "rejected")
        self.assertFalse(rejected["accepted"])
        self.assertIsNone(rejected["after"])
        self.assertEqual(self.events("submit"), [])
        self.validation.validated, self.validation.reason = True, ""
        self.submission.error, self.submission.accepted = 50, False
        failed = await self.fx.api.release_country(catalog[0])
        self.assertIs(failed["submission"], self.submission)
        self.assertEqual(failed["native_error"], 50)
        self.assertIsNone(failed["accepted"])
        self.assertIsNone(failed["after"])
        await catalog.close()

    async def test_accepted_with_native_error_and_readback_exception_is_never_masked(self):
        catalog = await self.catalog()
        error = OSError("original post-acceptance read unavailable")
        self.submission.error = 50
        self.submit_hook = lambda: setattr(self.fx, "readback_error", error)
        result = await self.fx.api.release_country(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertIs(result["native_result"], self.submission)
        self.assertEqual(result["native_error"], 50)
        self.assertIs(result["readback_error"], error)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        run, = self.action_runs()
        self.assertTrue(run.retain_unknown)
        self.assertEqual(run.values, [901, 950])
        self.assertEqual(catalog.active_leases, 1)
        with self.assertRaises(ChoicesBusyError): await catalog.close()
        self.assertEqual(self.events("submit"), [("submit", 4)])
        self.assertEqual(self.fx.backend.released, [])

    async def test_accepted_partial_flag_pages_and_partial_readback_keep_all_values(self):
        catalog = await self.catalog()
        self.fx.transform = lambda page: replace(page, error=13)
        result = await self.fx.api.release_country(catalog[0])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertFalse(result["initial_window"]["complete"])
        self.assertEqual(result["initial_window"]["native_pages"][0].error, 13)
        self.assertEqual(result["after"]["player"]["rows"][0]["id"], 0x99000009)
        self.assertEqual(result["after"]["player"]["native_pages"][0].error, 13)
        self.assertFalse(result["completed"])
        await catalog.close()


    async def test_unknown_factory_dispatch_keeps_original_query_and_source_lease(self):
        catalog = await self.catalog()
        error = OSError("original context dispatch unknown")
        self.fx.dispatch_error = error
        with self.assertRaises(OSError) as raised:
            await self.fx.api.release_country(catalog[0])
        self.assertIs(raised.exception, error)
        self.assertIsInstance(error.query, data.DataQuery)
        self.assertIsNone(error.query.request_id)
        run, = self.action_runs()
        self.assertIs(run.uncertain_query, error.query)
        self.assertEqual(self.fx.dispatches, 1)
        self.assertEqual(catalog.active_leases, 1)
        self.assertEqual(self.fx.freed, [])
        self.assertEqual(self.fx.backend.released, [])
        with self.assertRaises(ChoicesBusyError): await catalog.close()

    async def test_pending_real_factory_resumes_original_callback_once(self):
        catalog = await self.catalog()
        executor = SerializedQueryExecutor(timeout=.01, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query, self.fx.ctx.perform, self.fx.ctx._remember = executor.query, executor.perform, executor._remember
        self.fx.callback_pending, self.fx.started = True, asyncio.Event()
        self.fx.started_loop = asyncio.get_running_loop()
        task = asyncio.create_task(self.fx.api.release_country(catalog[0]))
        await asyncio.wait_for(self.fx.started.wait(), 1)
        pending = await task
        self.assertIsInstance(pending, PendingOperation)
        query, = pending.pending_queries
        self.assertIsInstance(query, data.DataQuery)
        self.assertEqual(query.request_id, 7)
        self.assertEqual(query.adapter, self.fx.data_base)
        self.assertEqual(self.fx.freed, [])
        self.assertTrue(self.fx.api._mutation_lock.locked())
        self.assertEqual(catalog.active_leases, 1)
        with self.assertRaises(ChoicesBusyError): await catalog.close()
        self.fx.callback_pending = False
        result = await pending.resume(1)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.fx.dispatches, 1)
        self.assertEqual(self.events("submit"), [("submit", 4)])
        self.assertEqual(catalog.active_leases, 0)
        self.assertFalse(self.fx.api._mutation_lock.locked())
        await catalog.close()

    async def test_pending_toggle_reread_resumes_same_flags_without_replay(self):
        catalog = await self.catalog()
        executor = SerializedQueryExecutor(timeout=.01, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query, self.fx.ctx.perform, self.fx.ctx._remember = executor.query, executor.perform, executor._remember
        captured, started, loop = [], asyncio.Event(), asyncio.get_running_loop()
        class Delayed(Query):
            def __init__(self, page):
                super().__init__(page)
                self.ready = False
                self.partial_result = replace(page, cells=page.cells[:1])
                captured.append(self)
                loop.call_soon_threadsafe(started.set)
            def read(self): return self.result if self.ready else None
        self.fx.query_factory = lambda page: Delayed(page) if self.fx.window_reads == 2 else Query(page)
        task = asyncio.create_task(self.fx.api.release_country(catalog[0], play_as=True))
        await asyncio.wait_for(started.wait(), 1)
        pending = await task
        self.assertIsInstance(pending, PendingOperation)
        self.assertIs(pending.pending_queries[0], captured[0])
        self.assertEqual(self.events("toggle"), [("toggle", "play_as_released")])
        self.assertEqual(self.events("produce"), [])
        self.fx.query_factory = Query
        captured[0].ready = True
        result = await pending.resume(1)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.events("toggle"), [("toggle", "play_as_released")])
        self.assertEqual(self.fx.dispatches, 1)
        self.assertEqual(self.events("submit"), [("submit", 4)])
        await catalog.close()

    async def test_pending_accepted_readback_keeps_primary_result_no_second_submit(self):
        catalog = await self.catalog()
        executor = SerializedQueryExecutor(timeout=.01, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query, self.fx.ctx.perform, self.fx.ctx._remember = executor.query, executor.perform, executor._remember
        captured, started, loop = [], asyncio.Event(), asyncio.get_running_loop()
        class Delayed(Query):
            def __init__(self, page):
                super().__init__(page)
                self.ready = False
                captured.append(self)
                loop.call_soon_threadsafe(started.set)
            def read(self): return self.result if self.ready else None
        self.submit_hook = lambda: setattr(self.fx, "query_factory", Delayed)
        task = asyncio.create_task(self.fx.api.release_country(catalog[0]))
        await asyncio.wait_for(started.wait(), 1)
        pending = await task
        self.assertIsInstance(pending, PendingOperation)
        run, = self.action_runs()
        self.assertTrue(run.public_result["accepted"])
        self.assertIs(run.public_result["submission"], self.submission)
        self.assertEqual(run.values, [901, 950])
        self.fx.query_factory = Query
        captured[0].ready = True
        result = await pending.resume(1)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.events("submit"), [("submit", 4)])
        self.assertEqual(self.fx.dispatches, 1)
        await catalog.close()


    async def test_bad_closed_foreign_and_nonbool_inputs_do_not_dispatch_factory(self):
        catalog = await self.catalog()
        for choice in (None, 0, "Released Nation", dict(catalog[0]), {"source_index": 0}):
            with self.assertRaises(TypeError): await self.fx.api.release_country(choice)
        for name in ("independent", "play_as", "keep_homeland_states"):
            for value in (0, 1, None, "yes"):
                with self.assertRaises(ValueError): await self.fx.api.release_country(catalog[0], **{name: value})
        foreign = national_tests.API(NS(perform=self.fx.perform))
        with self.assertRaises(ValueError): await foreign.release_country(catalog[0])
        await catalog.close()
        with self.assertRaises(RuntimeError): await self.fx.api.release_country(catalog[0])
        self.assertEqual(self.fx.dispatches, 0)


if __name__ == "__main__": unittest.main()
