"""POST-CUT4 partial law catalogs and original source identity joins.

CPU fixtures execute the public consumer, canonical original-owner helpers and
copied native origins. Native key/group errors are independent fixture inputs,
not game validation rules. No game, process, DLL or new native producer runs.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest

from pdx_runtime import v3_data as data, v3_objects as objects
from pdx_runtime.query_executor import PendingOperation
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from tests.v3_game import test_v3_game_politics as legacy
from tests.v3_game import test_v3_game_law_selected_commands as selected


def definition(pointer, key=b"law_native", *, error=0, key_error=0,
               group_error=0, group_key_error=0, group=b"lawgroup_native", index=0):
    if error:
        key_error = group_error = group_key_error = error
    if group_error: group_key_error = group_error
    key = b"" if key_error else key
    group = b"" if group_key_error else group
    return objects.LawDefinition(index, -1 if error else index, error, key_error,
        pointer, -1 if group_error else 0, group_error, 0 if group_error else 0xB0000,
        0, len(key), len(key), 0, len(group), len(group), group_key_error, 0, key, group)


class FailedQuery(legacy.Query):
    def __init__(self, result, failure):
        super().__init__(result)
        self.failure, self.partial_result = failure, result
    def read(self): raise self.failure


class Fixture(selected.Fixture):
    def __init__(self):
        super().__init__()
        self.definitions = [definition(row.pointer, row.key.encode(), index=index)
            for index, row in enumerate(self.definitions)]
        self.catalog_records = list(self.definitions)
        self.catalog_queries, self.type_queries = [], []
        self.catalog_error, self.catalog_world, self.catalog_stall = 0, self.world, False
        self.catalog_failure, self.catalog_pending = None, False
        self.type_errors, self.type_partials = {}, {}
        self.missing_once, self.type_seen = set(), set()
        self.borrowed_type = False
    def set_definition(self, index, **changes):
        old = self.definitions[index]
        values = dict(pointer=old.pointer, key=old.key_bytes, group=old.group_key_bytes, index=index)
        values.update(changes)
        row = definition(**values)
        self.definitions[index] = row
        self.catalog_records[self.catalog_records.index(old)] = row
        return row
    def enumerate_law_definitions(self, native, start_index, **kwargs):
        page = NS(error=self.catalog_error, world=self.catalog_world,
            records=(self.catalog_records[start_index],), fallback=0xDEAD,
            next_index=start_index if self.catalog_stall else start_index + 1,
            slot_bound=len(self.catalog_records))
        if self.catalog_failure is not None:
            query = FailedQuery(page, self.catalog_failure)
        elif self.catalog_pending and start_index == 0:
            self.catalog_pending = False
            query = selected.ControlledQuery(page, self)
        else:
            query = legacy.Query(page)
            query.partial_result = page if self.catalog_error else None
        self.catalog_queries.append(query)
        return query
    def evaluate(self, native, expression, bindings, **kwargs):
        query = super().evaluate(native, expression, bindings, **kwargs)
        if expression == "Law.GetLawType":
            full_id = self.bound[bindings[0].object][1]
            self.type_queries.append((full_id, query))
            query.result.error = self.type_errors.get(full_id, 0)
            query.partial_result = self.type_partials.get(full_id)
            if full_id in self.missing_once and full_id not in self.type_seen:
                self.origins[query.result.value.pointer + 0x10] = 0xBAD
            self.type_seen.add(full_id)
            if self.borrowed_type:
                value = query.result.value
                name = ".?AU?$SAnyType@PEBVCDataLawType@@@PdxAnyImpl@@"
                query.result.native_type_name = name
                query.result.value = data.NativeObject(value.pointer, value.type_id, value.value_handle, name)
        return query


class PartialLawDefinitionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self): self.patchers = []
    def tearDown(self):
        for patcher in reversed(self.patchers): patcher.stop()
    def fixture(self):
        fx = Fixture()
        ctx = legacy.Context(fx)
        ctx.adapters.update(data=fx.data_adapter, commands=fx.command_adapter)
        fx.started, fx.loop = asyncio.Event(), asyncio.get_running_loop()
        fx.api = politics.PoliticsAPI(ctx)
        for patcher in fx.patches(): patcher.start(); self.patchers.append(patcher)
        return fx
    def cleaned(self, fx):
        self.assertEqual(fx.handles, {})
        self.assertEqual(fx.memory.blocks, {})
        self.assertEqual(fx.api._retained, [])
        self.assertTrue(all(not owner.command for owner in fx.owners))
    def no_command(self, fx):
        self.assertEqual(fx.producer_bindings, [])
        self.assertEqual(fx.clones, [])
        self.assertEqual(fx.validations, [])
        self.assertEqual(fx.submitted, [])
    async def invoke(self, fx, action, target):
        return await getattr(fx.api, action)(target)
    def selected_once(self, fx, result, action):
        self.assertEqual(fx.events.count("Law.StartEnacting"), 1)
        self.assertEqual(len(fx.producer_bindings), 1)
        self.assertEqual(len(fx.clones), 1)
        self.assertEqual(len(fx.validations), 1)
        self.assertEqual(len(fx.submitted), 1 if action == "enact" else 0)
        self.assertIs(result["validation"], fx.validation_results[0])
        _, bindings = fx.producer_bindings[0]
        self.assertTrue(bindings[0].retained)
        self.assertEqual(fx.bound[bindings[0].object], ("Law", result["law"]["id"]))
        self.assertIs(bindings[0], result["law"]["source_binding"])
        type_read = next(read for read in result["law"]["native_reads"] if read["stage"] == "Law.GetLawType")
        self.assertIs(type_read["native_result"], type_read["query"].result)
        self.assertTrue(any(query is type_read["query"] for _, query in fx.type_queries))
        self.assertIsNone(type_read["error"])
        self.assertTrue(any(stage == "Law.origin" for stage, _ in result["law"]["native_results"]))
        self.assertTrue(any(stage == "LawType.origin" for stage, _ in result["law"]["native_results"]))
        self.assertTrue(all(fx.ctx.created_queries.count(query) == 1 for query in fx.ctx.created_queries))
        if action == "enact":
            self.assertTrue(result["accepted"])
            self.assertIs(result["native_result"], fx.submission_results[0])
            self.assertIs(result["submission"], fx.submission_results[0])
        else:
            self.assertIsNone(result["accepted"])
            self.assertTrue(result["eligible"])
        self.cleaned(fx)

    async def test_unrelated_bad_catalog_and_source_rows_do_not_block_later_clean_target(self):
        for action in ("enact", "validate_enactment"):
            with self.subTest(action=action):
                fx = self.fixture()
                bad_key = fx.set_definition(0, key_error=13)
                unrelated = definition(0xDEAD, error=1168, index=20)
                fx.catalog_records.insert(0, unrelated)
                result = await self.invoke(fx, action, "law_workers_protections")
                self.assertEqual(result["selection_status"], "matched")
                self.assertEqual(result["law"]["key"], "law_workers_protections")
                self.assertEqual(result["law"]["source_index"], 0)
                self.assertEqual(result["law"]["source_expression"], "Country.AccessLawsWithoutGovernmentOrMovementSupport")
                entries = result["definition_records"]
                self.assertEqual(len(entries), 3)
                for entry, query in zip(entries, fx.catalog_queries[:3]):
                    self.assertIs(entry["query"], query)
                    self.assertIs(entry["native_result"], query.result)
                    self.assertIs(entry["record"], query.result.records[0])
                self.assertIs(result["definition_issues"][0]["record"], unrelated)
                issue, = result["selection_issues"]
                self.assertEqual(issue["id"], fx.law_ids[0])
                self.assertIsNone(issue["key"])
                self.assertIs(issue["definition"]["record"], bad_key)
                self.assertFalse(issue["identity_complete"])
                self.selected_once(fx, result, action)

    async def test_clean_key_with_group_errors_or_strict_group_decode_failure_remains_selectable(self):
        for action in ("enact", "validate_enactment"):
            for changes in ({"group_error": 21}, {"group_key_error": 13}, {"group": b"\xff"}):
                with self.subTest(action=action, changes=changes):
                    fx = self.fixture()
                    original = fx.set_definition(1, **changes)
                    result = await self.invoke(fx, action, "law_workers_protections")
                    row = result["law"]
                    self.assertTrue(row["identity_complete"])
                    self.assertEqual(row["key"], "law_workers_protections")
                    self.assertIsNone(row["group"])
                    self.assertIn("group_reason", row)
                    self.assertIs(row["definition"]["record"], original)
                    if changes.get("group") == b"\xff":
                        self.assertIsInstance(row["definition"]["decode_errors"]["group"], UnicodeDecodeError)
                        self.assertEqual(original.group_key_bytes, b"\xff")
                    self.selected_once(fx, result, action)

    async def test_selected_full_id_never_bypasses_missing_or_errored_native_key_join(self):
        for action in ("enact", "validate_enactment"):
            for mode in ("key_error", "record_error", "missing_origin"):
                with self.subTest(action=action, mode=mode):
                    fx = self.fixture()
                    if mode == "key_error": fx.set_definition(1, key_error=13)
                    elif mode == "record_error": fx.set_definition(1, error=1168)
                    else: fx.bad_definition = True
                    result = await self.invoke(fx, action, fx.law_ids[1])
                    self.assertEqual(result["status"], "unavailable")
                    self.assertEqual(result["selection_status"], "identity_unavailable")
                    self.assertEqual(result["law"]["id"], fx.law_ids[1])
                    self.assertIsNone(result["law"]["key"])
                    self.assertFalse(result["law"]["identity_complete"])
                    self.assertIsNone(result["accepted"])
                    self.no_command(fx)
                    self.cleaned(fx)

    async def test_clean_absence_unresolved_key_and_known_nonmatching_full_ids_are_distinct(self):
        for action in ("enact", "validate_enactment"):
            for mode, expected in (("clean", "absent"), ("unknown_key", "unknown"), ("known_other_ids", "absent")):
                with self.subTest(action=action, mode=mode):
                    fx = self.fixture()
                    if mode != "clean":
                        fx.set_definition(0, key_error=13)
                        fx.set_definition(1, key_error=13)
                    target = 0xCC000006 if mode == "known_other_ids" else "law_absent"
                    result = await self.invoke(fx, action, target)
                    self.assertEqual(result["status"], "unavailable")
                    self.assertEqual(result["selection_status"], expected)
                    self.assertIsNone(result["accepted"])
                    self.assertEqual(len(result["selection_issues"]), 0 if mode == "clean" else 2)
                    self.no_command(fx)
                    self.cleaned(fx)

    async def test_native_full_id_zero_and_complete_empty_key_are_not_missing_values(self):
        fx = self.fixture()
        old = fx.law_ids[1]
        fx.law_ids[1] = 0
        fx.law_names[0] = fx.law_names.pop(old)
        fx.law_detail_fields[0] = fx.law_detail_fields.pop(old)
        for expression, records in fx.lists.items():
            fx.lists[expression] = [(name, 0 if name == "Law" and value == old else value) for name, value in records]
        fx.set_definition(1, key=b"")
        result = await fx.api.enact(0)
        self.assertEqual(result["law"]["id"], 0)
        self.assertEqual(result["law"]["id_observed"], 0)
        self.assertEqual(result["law"]["key"], "")
        self.assertTrue(result["law"]["identity_complete"])
        self.assertEqual(result["current"]["id"], 0)
        self.assertEqual(result["current"]["key"], "")
        self.selected_once(fx, result, "enact")

    async def test_completed_source_native_error_preserves_query_result_error_and_partial_before_target(self):
        for action in ("enact", "validate_enactment"):
            with self.subTest(action=action):
                fx = self.fixture()
                partial = NS(error=122, snapshot=b"original partial identity", value=0)
                fx.type_errors[fx.law_ids[0]], fx.type_partials[fx.law_ids[0]] = 50, partial
                result = await self.invoke(fx, action, "law_workers_protections")
                row, = result["selection_issues"]
                issue = row["native_issues"][0]
                query = fx.type_queries[0][1]
                self.assertIs(issue["query"], query)
                self.assertIs(issue["native_result"], query.result)
                self.assertIs(issue["error"].result, query.result)
                self.assertEqual(issue["native_result"].error, 50)
                self.assertIs(issue["partial_result"], partial)
                self.assertIs(issue["source_result"], row["source_result"])
                self.assertIs(issue["binding"], row["source_binding"])
                self.assertEqual(issue["source_index"], 0)
                self.selected_once(fx, result, action)

    async def test_strict_native_key_bytes_and_display_names_never_supply_missing_identity(self):
        for action in ("enact", "validate_enactment"):
            for mode in ("invalid_utf8", "display_name"):
                with self.subTest(action=action, mode=mode):
                    fx = self.fixture()
                    original = fx.set_definition(1, **({"key": b"\xff"} if mode == "invalid_utf8" else {"key_error": 13}))
                    target = fx.law_ids[1] if mode == "invalid_utf8" else "Workers' Protections"
                    result = await self.invoke(fx, action, target)
                    self.assertEqual(result["status"], "unavailable")
                    self.assertEqual(result["selection_status"], "identity_unavailable" if mode == "invalid_utf8" else "unknown")
                    self.assertIsNone(result["accepted"])
                    if mode == "invalid_utf8":
                        self.assertEqual(original.key_bytes, b"\xff")
                        self.assertIsInstance(result["law"]["definition"]["decode_errors"]["key"], UnicodeDecodeError)
                        self.assertIsNone(result["law"]["key"])
                    self.no_command(fx)
                    self.cleaned(fx)

    async def test_partial_read_rows_do_not_deduplicate_a_later_clean_original_occurrence(self):
        for method in ("laws", "law_options"):
            with self.subTest(method=method):
                fx = self.fixture()
                expression = "Country.AccessActivePowerStructureLaws" if method == "laws" else "Country.AccessLawsWithGovernmentOrMovementSupport"
                fx.lists[expression] = [("Law", fx.law_ids[0]), ("Law", fx.law_ids[0])]
                fx.missing_once.add(fx.law_ids[0])
                rows = await getattr(fx.api, method)()
                first, clean = rows[:2]
                self.assertEqual(first["id"], clean["id"])
                self.assertIsNone(first["key"])
                self.assertFalse(first["identity_complete"])
                self.assertEqual(clean["key"], "law_serfdom")
                self.assertTrue(clean["identity_complete"])
                self.assertEqual((first["source_index"], clean["source_index"]), (0, 1))
                self.no_command(fx)
                self.cleaned(fx)

    async def test_current_enactment_fallback_preserves_partial_key_group_without_request_fill(self):
        fx = self.fixture()
        fx.enacting = fx.law_ids[1]
        original = fx.set_definition(1, key_error=13, group_error=21)
        for getter in (*politics._ACTIVE_LAWS, *politics._LAW_OPTIONS): fx.lists["Country." + getter] = []
        result = await fx.api.enactment()
        self.assertEqual(result["status"], "enacting")
        self.assertIsNone(result["id"])
        self.assertIsNone(result["key"])
        self.assertIsNone(result["group"])
        self.assertFalse(result["identity_complete"])
        self.assertIs(result["definition"]["record"], original)
        self.assertIn("key error", result["key_reason"])
        self.no_command(fx)
        self.cleaned(fx)

    async def test_page_error_world_advance_and_original_pointer_guards_are_not_weakened(self):
        for mode in ("page_error", "world", "advance", "borrowed_type"):
            with self.subTest(mode=mode):
                fx = self.fixture()
                if mode == "page_error": fx.catalog_error = 234
                elif mode == "world": fx.catalog_world += 1
                elif mode == "advance": fx.catalog_stall = True
                else: fx.borrowed_type = True
                expected = run_mod.KnownResultError if mode == "page_error" else RuntimeError
                with self.assertRaises(expected) as raised: await fx.api.enact("law_workers_protections")
                if mode == "page_error":
                    query, = fx.catalog_queries
                    self.assertIs(raised.exception.result, query.result)
                    self.assertIs(raised.exception.query, query)
                    self.assertIs(raised.exception.partial_result, query.partial_result)
                    self.assertIs(query.result.records[0], fx.catalog_records[0])
                    self.cleaned(fx)
                elif mode == "borrowed_type":
                    self.assertTrue(fx.api._retained)
                else:
                    # No Any/owner/buffer was acquired before these completed
                    # page guards. Canonical _forget_if_empty removes the scope.
                    self.cleaned(fx)
                self.no_command(fx)

    async def test_unknown_runtime_and_cancel_keep_original_query_and_never_continue_to_action(self):
        for failure in (RuntimeError("catalog outcome uncertain"), asyncio.CancelledError("original catalog canceled")):
            with self.subTest(failure=type(failure).__name__):
                fx = self.fixture()
                fx.catalog_failure = failure
                with self.assertRaises(type(failure)) as raised: await fx.api.enact("law_workers_protections")
                self.assertIs(raised.exception, failure)
                run, = fx.api._retained
                query, = fx.catalog_queries
                self.assertIs(run.uncertain_query, query)
                self.assertTrue(run.retain_unknown)
                self.assertEqual(fx.released, [])
                self.no_command(fx)

    async def test_native_rejection_and_pending_resume_keep_original_single_command(self):
        fx = self.fixture()
        fx.set_definition(0, key_error=13)
        fx.validation_value, fx.validation_reason = False, "independent original native rejection"
        result = await fx.api.enact("law_workers_protections")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], fx.validation_reason)
        self.assertIs(result["validation"], fx.validation_results[0])
        self.assertEqual(len(fx.clones), 1)
        self.assertEqual(len(fx.validations), 1)
        self.assertEqual(fx.submitted, [])
        self.cleaned(fx)
        for phase in ("catalog", "submit"):
            with self.subTest(phase=phase):
                fx = self.fixture()
                fx.set_definition(0, key_error=13)
                fx.ctx.timeout = .01
                fx.catalog_pending = phase == "catalog"
                fx.pending_stage = "submit" if phase == "submit" else None
                task = asyncio.create_task(fx.api.enact("law_workers_protections"))
                await asyncio.wait_for(fx.started.wait(), 1)
                pending = await task
                self.assertIsInstance(pending, PendingOperation)
                original_task, original_query = pending.task, fx.delayed[0]
                self.assertIs(pending.pending_queries[0], original_query)
                self.assertTrue(fx.api._mutation_lock.locked())
                original_query.ready = True
                result = await pending.resume(1)
                self.assertIs(pending.task, original_task)
                self.assertTrue(original_task.done())
                self.assertEqual(fx.ctx.created_queries.count(original_query), 1)
                self.selected_once(fx, result, "enact")


if __name__ == "__main__": unittest.main()
