"""CPU-only read-only selected release-window matrix via the actual Action22 factory.

The original callback factory/DataQuery/_Run/snapshot paths run on deterministic
storage and copied page fixtures. No game process or native effect is invoked.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.query_executor import PendingOperation, SerializedQueryExecutor
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game.politics import national
from tests.v3_game import test_v3_game_national_release as release_tests


class OptionsTests(unittest.IsolatedAsyncioTestCase):
    formation_native_source = release_tests.ReleaseTests.formation_native_source
    setUp = release_tests.ReleaseTests.setUp
    patch = release_tests.ReleaseTests.patch
    catalog = release_tests.ReleaseTests.catalog
    events = release_tests.ReleaseTests.events

    def runs(self):
        return [run for run in self.fx.ctx.diagnostics if isinstance(run, run_mod.Run)
                and run.label == "release_country_options"]

    async def read(self, choice):
        # Options discovery may construct only its isolated context. Not even
        # candidate command generation, property invocation or Validate is legal.
        with patch.object(data, "evaluate", side_effect=AssertionError("options generated or showed a command")), \
             patch.object(data, "invoke_property_action", side_effect=AssertionError("options toggled a flag")), \
             patch.object(data, "command_value", side_effect=AssertionError("options extracted a command")), \
             patch.object(commands, "request", side_effect=AssertionError("options validated or submitted")):
            result = await self.fx.api.release_country_options(choice)
            if isinstance(result, PendingOperation):
                if hasattr(self, "pending_ready"):
                    self.original_pending = result
                    self.pending_ready.set()
                result = await result.resume(1)
            return result

    async def test_raw_header_owned_context_uses_original_dataresult_type_handle_and_bytes(self):
        catalog = await self.catalog()
        original_request = self.fx.ctx.native.request
        payload, raw_any = b"\x00original-native-RAW\xff", bytes(range(40))
        def raw_request(command):
            response = original_request(command)
            if command.startswith("interface-callback "):
                address = max(self.fx.memory)
                raw = bytearray(self.fx.memory[address])
                header = list(data.REQUEST.unpack_from(raw))
                header[11], header[13] = data.Kind.RAW, 0
                header[9], header[18], header[19] = len(payload), len(payload), len(payload)
                header[27] = raw_any
                raw[header[24]:header[24] + len(payload)] = payload
                data.REQUEST.pack_into(raw, 0, *header)
                self.fx.memory[address] = bytes(raw)
            return response
        self.fx.ctx.native.request = raw_request
        self.fx.ctx.adapters["data"] = "later-other-data.dll"
        result = await self.read(catalog[0])
        made = result["context_result"]
        self.assertIsInstance(made, data.DataResult)
        self.assertEqual(made.kind, data.Kind.RAW)
        self.assertEqual(made.value, payload)
        self.assertEqual(made.snapshot, payload)
        self.assertEqual(made.raw_any, raw_any)
        self.assertEqual(made.object, 0)
        self.assertEqual((made.type_id, made.value_handle), (self.fx.context_type, 901))
        self.assertEqual(made.native_type_name,
            ".?AU?$SAnyType@V?$CPdxPtr@VCReleaseCountryWindow@@@@@PdxAnyImpl@@")
        self.assertTrue(result["complete"])
        self.assertEqual(result["status"], "observed")
        self.assertIs(result["native_result"], made)
        bound, = self.fx.calls[-1][1][0]
        self.assertEqual((bound.type_id, bound.object, bound.retained), (made.type_id, made.value_handle, True))
        self.assertEqual(self.fx.calls[-1][2]["adapter"], self.fx.data_base)
        self.assertEqual(self.fx.dispatches, 1)
        self.assertEqual([h for h, _ in self.fx.backend.released], [901])
        self.assertEqual(self.events("produce"), [])
        await catalog.close()

    async def test_raw_header_borrowed_context_is_rejected_without_object_conversion(self):
        catalog = await self.catalog()
        self.fx.context_rtti = ".?AU?$SAnyType@PEAVCReleaseCountryWindow@@@PdxAnyImpl@@"
        original_request = self.fx.ctx.native.request
        payload = b"original-borrowed-RAW"
        def raw_request(command):
            response = original_request(command)
            if command.startswith("interface-callback "):
                address = max(self.fx.memory)
                raw = bytearray(self.fx.memory[address])
                header = list(data.REQUEST.unpack_from(raw))
                header[11], header[13] = data.Kind.RAW, 0
                header[9], header[18], header[19] = len(payload), len(payload), len(payload)
                raw[header[24]:header[24] + len(payload)] = payload
                data.REQUEST.pack_into(raw, 0, *header)
                self.fx.memory[address] = bytes(raw)
            return response
        self.fx.ctx.native.request = raw_request
        result = await self.read(catalog[0])
        made = result["context_result"]
        self.assertIsInstance(made, data.DataResult)
        self.assertEqual(made.kind, data.Kind.RAW)
        self.assertEqual(made.value, payload)
        self.assertEqual(made.object, 0)
        self.assertEqual(made.native_type_name, self.fx.context_rtti)
        self.assertIs(result["native_result"], made)
        self.assertEqual(result["status"], "native_result")
        self.assertIn("ownership is not established", result["reason"])
        self.assertFalse(result["context_available"])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["window"])
        self.assertEqual(self.fx.window_reads, 0)
        self.assertEqual(self.fx.dispatches, 1)
        self.assertEqual([h for h, _ in self.fx.backend.released], [901])
        self.assertEqual(catalog.active_leases, 0)
        await catalog.close()

    async def test_actual_factory_original_handle_dynamic_zero_type_and_pinned_snapshot_owner(self):
        catalog = await self.catalog([{}, {}])
        source_calls = list(self.fx.backend.calls)
        self.fx.ctx.adapters["data"] = "different-later-data.dll"
        self.fx.context_type = 0
        self.fx.defaults["GetPlayer.GetID"] = 0
        self.fx.options.update({name: False for name in self.fx.options})
        for field in national._RELEASE_WINDOW_FIELDS:
            if field.kind == data.Kind.BOOL: self.fx.defaults[field.expression] = False
        result = await self.read(catalog[1])
        self.assertTrue(result["complete"])
        self.assertEqual(result["status"], "observed")
        self.assertTrue(result["context_available"])
        self.assertFalse(result["command_validation_performed"])
        self.assertNotIn("eligible", result)
        self.assertEqual(result["catalog_country_id"], self.fx.country_id)
        window = result["window"]
        self.assertEqual(window["actor_country_id"], 0)
        self.assertEqual({field.expression for field in national._RELEASE_WINDOW_FIELDS if field.kind == data.Kind.BOOL}, {
            "ReleaseCountryWindow.WillMakeIndependent", "ReleaseCountryWindow.WillMakeOwnSubject",
            "ReleaseCountryWindow.WillPlayAsReleased", "ReleaseCountryWindow.WillKeepHomelandStates",
            "ReleaseCountryWindow.CanMakeIndependent", "ReleaseCountryWindow.CanMakeOwnSubject",
            "ReleaseCountryWindow.CanPlayAsReleased", "ReleaseCountryWindow.CanReleaseWithKeepHomelandStates",
            "ReleaseCountryWindow.SubjectTypeToMakeOwnSubjectExists",
            "And(ReleaseCountryWindow.CanMakeIndependent,ReleaseCountryWindow.CanMakeOwnSubject)"})
        for field in national._RELEASE_WINDOW_FIELDS:
            self.assertIn(field.name, window)
            if field.kind == data.Kind.BOOL: self.assertIs(window[field.name], False)
        self.assertEqual(window["reasons"], {"fields": {}, "pages": []})
        self.assertIs(result["native_pages"], window["native_pages"])
        self.assertEqual(self.fx.headers[0][2:4], (22, 1))
        self.assertEqual(self.fx.headers[0][14], 201)
        self.assertEqual(result["context_result"].type_id, 0)
        self.assertIs(result["native_result"], result["context_result"])
        self.assertEqual(self.fx.exports, [(self.fx.data_base, "PdxV3DataCreateOwnedContextInterface")])
        self.assertEqual(self.fx.dispatches, 1)
        self.assertEqual([event[0] for event in self.fx.events], ["context", "flags"])
        self.assertEqual([h for h, _ in self.fx.backend.released], [901])
        self.assertEqual(self.fx.backend.calls, source_calls)
        self.assertEqual(self.fx.calls[-1][1][0][0].type_id, 0)
        self.assertEqual(self.fx.calls[-1][2]["adapter"], self.fx.data_base)
        run, = self.runs()
        self.assertEqual(run.status, "complete")
        self.assertEqual(run.owners, [])
        self.assertEqual([stage for stage, _ in result["native_results"]],
                         ["release.context", "politics.release_country_options.window"])
        self.assertEqual(catalog.active_leases, 0)
        self.assertEqual(self.fx.performs, ["politics.release_choices", "politics.release_country_options"])
        await catalog.close()
        self.assertEqual(self.fx.backend.live, set())

    async def test_missing_error_and_wrong_kind_current_flags_and_predicates_stay_distinct(self):
        catalog = await self.catalog()
        self.fx.options.update({name: False for name in self.fx.options})
        for field in national._RELEASE_WINDOW_FIELDS:
            if field.kind == data.Kind.BOOL: self.fx.defaults[field.expression] = False
        for index, field in enumerate(national._RELEASE_WINDOW_FIELDS):
            if field.kind != data.Kind.BOOL: continue
            for mode in ("missing", "122", "kind"):
                with self.subTest(field=field.name, mode=mode):
                    def partial(page):
                        cells = tuple(c for c in page.cells if not (mode == "missing" and c.field == index))
                        return replace(page, cells=tuple(replace(c, error=122) if mode == "122" and c.field == index
                            else replace(c, kind=data.Kind.EMPTY) if mode == "kind" and c.field == index else c for c in cells))
                    self.fx.window_transform = partial
                    result = await self.read(catalog[0])
                    self.assertFalse(result["complete"])
                    self.assertEqual(result["status"], "partial")
                    row = result["window"]
                    self.assertIs(row[field.name], False if mode == "122" else None)
                    reason = row["reasons"]["fields"][field.name]
                    self.assertIn("122" if mode == "122" else "did not copy" if mode == "missing" else "native kind", reason)
                    if mode == "122":
                        original = result["native_pages"][0].cells[index]
                        self.assertEqual(original.error, 122)
                        self.assertIs(original.value, False)
        self.assertEqual(self.events("toggle"), [])
        self.assertEqual(self.events("produce"), [])
        await catalog.close()

    async def test_native_page_error122_retains_original_false_zero_and_tooltip_prefix(self):
        catalog = await self.catalog()
        self.fx.defaults["GetPlayer.GetID"] = 0
        index = next(i for i, f in enumerate(national._RELEASE_WINDOW_FIELDS) if f.name == "subject_tooltip")
        def partial(page):
            return replace(page, error=122, cells=tuple(replace(c, total=c.total + 100) if c.field == index else c for c in page.cells))
        self.fx.window_transform = partial
        result = await self.read(catalog[0])
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "partial")
        self.assertIs(result["window"]["make_own_subject"], False)
        self.assertEqual(result["window"]["actor_country_id"], 0)
        self.assertEqual(result["window"]["subject_tooltip"], "original text")
        self.assertIn("incomplete", result["window"]["reasons"]["fields"]["subject_tooltip"])
        self.assertEqual(result["native_pages"][0].error, 122)
        self.assertIs(result["native_pages"][0], result["native_results"][1][1])
        await catalog.close()

    async def test_no_native_window_row_is_unavailable_not_false_or_empty_matrix(self):
        catalog = await self.catalog()
        self.fx.window_transform = lambda page: replace(page, error=50, cells=(), row_count=0, count_valid=False)
        result = await self.read(catalog[0])
        self.assertTrue(result["context_available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "unavailable")
        for field in national._RELEASE_WINDOW_FIELDS:
            self.assertIsNone(result["window"][field.name])
            self.assertIn("no row", result["window"]["reasons"]["fields"][field.name])
        self.assertEqual(result["native_pages"][0].error, 50)
        self.assertEqual(self.fx.window_reads, 1)
        await catalog.close()

    async def test_missing_context_output_keeps_original_native_result_without_window_read(self):
        catalog = await self.catalog()
        self.fx.context_output = 0
        result = await self.read(catalog[0])
        self.assertFalse(result["context_available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "native_result")
        self.assertIsNone(result["window"])
        self.assertEqual(result["context_result"].error, 0)
        self.assertEqual(result["context_result"].value_handle, 0)
        self.assertIs(result["native_result"], result["context_result"])
        self.assertEqual(result["native_pages"], [])
        self.assertEqual(self.fx.window_reads, 0)
        self.assertEqual(self.fx.backend.released, [])
        await catalog.close()


    async def test_partial_factory234_keeps_complete_window_copy_but_overall_partial_status(self):
        catalog = await self.catalog()
        self.fx.context_error = 234
        result = await self.read(catalog[0])
        self.assertTrue(result["context_available"])
        self.assertTrue(result["window"]["complete"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["context_result"].error, 234)
        self.assertEqual(result["native_error"], 234)
        self.assertEqual(result["reasons"]["context_error"], 234)
        self.assertIs(result["native_result"], result["context_result"])
        self.assertEqual([h for h, _ in self.fx.backend.released], [901])
        self.assertEqual([e[0] for e in self.fx.events], ["context", "flags"])
        await catalog.close()


    async def test_pending_window_copy_keeps_original_query_partial_cells_without_factory_replay(self):
        catalog = await self.catalog()
        executor = SerializedQueryExecutor(timeout=.01, poll_interval=.001, diagnostics=[], owners=self.fx.ctx.owner_pool)
        self.fx.ctx.query, self.fx.ctx.perform, self.fx.ctx._remember = executor.query, executor.perform, executor._remember
        captured, started, loop = [], asyncio.Event(), asyncio.get_running_loop()
        class Delayed(release_tests.Query):
            def __init__(self, page):
                super().__init__(page)
                self.ready = False
                self.partial_result = replace(page, cells=page.cells[:2])
                captured.append(self)
            def read(self):
                loop.call_soon_threadsafe(started.set)
                return self.result if self.ready else None
        self.fx.query_factory = Delayed
        task = asyncio.create_task(self.read(catalog[0]))
        await asyncio.wait_for(started.wait(), 1)
        query, = executor.pending_queries
        self.assertIs(query, captured[0])
        self.assertTrue(query.partial_result.cells[0].value)
        run, = self.runs()
        self.assertEqual(run.values, [901])
        self.assertEqual(catalog.active_leases, 1)
        self.fx.options["make_independent"] = False
        self.fx.query_factory = release_tests.Query
        captured[0].ready = True
        result = await asyncio.wait_for(task, 1)
        self.assertTrue(result["window"]["make_independent"])
        self.assertEqual(self.fx.window_reads, 1)
        self.assertEqual(self.fx.dispatches, 1)
        await catalog.close()


if __name__ == "__main__": unittest.main()
