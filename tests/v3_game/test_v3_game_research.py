"""Game research API CPU seams. No engine, DLL or live process is operated."""
from __future__ import annotations

import asyncio
import importlib
from threading import Event
from types import SimpleNamespace as R
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pdx_runtime import v3_data as data, v3_commands as commands
from pdx_runtime.v3_game.research import ResearchAPI
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES


class Query:
    def __init__(self, result):
        self.result = result
    def read(self):
        return self.result


class Context:
    def __init__(self, helper):
        self.native = Mock(spec=("pid", "request"), pid=123)
        world_status = {"status": "ok", "world": 0x50000, "transition_pending": 0}
        def request(command):
            if command != "world-status":
                raise AssertionError(f"unexpected wire command {command!r}")
            return world_status
        self.native.request.side_effect = request
        self.process = Mock(pid=123)
        self.session = R(native=self.native, namespace={})
        self.world, self.image_base, self.interface_thread_id = 0x50000, 0x140000000, 55
        self.adapters = {role: 0x100000 + index * 0x10000 for index, role in enumerate(
            ("data", "objects", "commands", "access", "call", "carrier", "console"))}
        self.diagnostics, self.labels = [], []
        self.helper = helper
        self.current_helper = R(read_current=AsyncMock(return_value={"current_known": True,
            "has_current_research": True, "key_known": True, "key": "atmospheric_engine",
            "progress_known": True, "progress_bits": 5000000, "cost_bits": 1500000000}))
    async def refresh(self):
        return None
    async def country_id(self):
        return 0x1000007
    async def type_id(self, name):
        return {"Country": 71, "Technology": 93}[name]
    async def wait(self, query):
        return query.read()
    async def submit(self, factory):
        return factory()
    async def perform(self, label, action):
        self.labels.append(label)
        return await action()
    async def query(self, label, factory, owner=None):
        query = factory()
        return await self.wait(query)


class ResearchReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.helper = importlib.import_module("pdx_runtime.v3_game.research._queue")
        self.ctx = Context(self.helper)
        self.api = ResearchAPI(self.ctx)
        current_patch = patch("pdx_runtime.v3_game.research._current.read_current", self.ctx.current_helper.read_current)
        current_patch.start()
        self.addCleanup(current_patch.stop)
        self.next_handle, self.selected_origin = 100, 0
        self.researched_keys = set()
        self.candidates = {"Production": [("atmospheric_engine", 0x70000, 0x90000)],
                           "Military": [("line_infantry", 0x70010, 0x90010)], "Society": [], "Other": []}
        self.sources = {}
        self.factory_objects = {key: (origin, wrapper) for rows in self.candidates.values()
                                for key, origin, wrapper in rows}
        self.definitions = [R(error=0, key_error=0, pointer=origin, key=key)
            for rows in self.candidates.values() for key, origin, wrapper in rows]
        self.ctx.process.allocate.return_value = 0x60000
        def resolve_export(module, symbol, *, aliases=None):
            if (module, symbol) == ("ntdll.dll", "memcpy"):
                self.assertIsNone(aliases)  # Generic OS export, not a V3 adapter alias.
            else:
                self.assertIs(aliases, V3_EXPORT_ALIASES)
            return 0x80000
        self.ctx.process.resolve_export.side_effect = resolve_export
        self.ctx.process.read.side_effect = lambda address, size: self.selected_origin.to_bytes(size, "little")
        self.release_calls = []
        self.patches = [
            patch.object(data, "enumerate_functions", side_effect=lambda *a, **k: Query(R(error=0,
                rows=tuple(R(name=name, argument_count=0 if name in ("GetName", "GetNameNoFormatting") else 1) for name in
                           ("GetProgress", "GetCost", "HasResearchedTech", "IsResearching", "IsResearchable", "GetName", "GetNameNoFormatting")), next=7, total=7))),
            patch.object(self.helper.objects, "enumerate_technology_definitions", side_effect=lambda *a, **k:
                Query(R(error=0, world=self.ctx.world, records=tuple(self.definitions),
                    next_index=len(self.definitions), slot_bound=len(self.definitions), truncated=False))),
            patch.object(data, "evaluate", side_effect=self.evaluate),
            patch.object(data, "list_count", side_effect=lambda client, handle, **k: Query(R(error=0,
                total=len(self.sources[handle])))),
            patch.object(data, "list_element", side_effect=self.element),
            patch.object(data, "release", side_effect=self.release),
            patch.object(self.helper.v3_access, "submit_call", side_effect=self.copy),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    @property
    def plans(self):
        return [item["evidence"] for item in self.ctx.diagnostics
            if isinstance(item, dict) and item.get("domain") == "research" and "run" in item.get("evidence", {})]

    def new_result(self, value):
        self.next_handle += 1
        return R(action=data.Action.EVALUATE, error=0, value_handle=self.next_handle, value=value)

    def evaluate(self, client, expression, bindings, **options):
        if expression.startswith("GetTechnology('") and "')." in expression:
            key, getter = expression[len("GetTechnology('"):].split("').",1)
            self.assertIn(key,self.factory_objects)
            if getter in ("GetNameNoFormatting","GetName"):
                self.assertEqual(bindings,())
                value={"atmospheric_engine":"Atmospheric Engine","line_infantry":"Line Infantry",
                    "bessemer_process":"Bessemer Process"}.get(key,key)
            else:
                self.assertEqual(bindings,(data.ObjectBinding.country(71,0x1000007),))
                values={"GetProgress(Country.Self)":data.FixedPoint(5000000),
                    "GetCost(Country.Self)":data.FixedPoint(1500000000),
                    "HasResearchedTech(Country.Self)":key in self.researched_keys,
                    "IsResearching(Country.Self)":key=="atmospheric_engine"}
                self.assertIn(getter,values)
                value=values[getter]
            return Query(self.new_result(value))
        if expression.startswith("GetTechnology('") and expression.endswith("').GetNameNoFormatting"):
            key = expression[len("GetTechnology('"):-len("').GetNameNoFormatting")]
            self.assertIn(key, self.factory_objects)
            self.assertEqual(bindings, ())
            return Query(self.new_result("Atmospheric Engine" if key == "atmospheric_engine" else "Line Infantry"))
        if expression.startswith("GetTechnology('") and expression.endswith("')"):
            key = expression[len("GetTechnology('"):-2]
            origin, wrapper = self.factory_objects[key]
            result = self.new_result(None)
            result.value = data.NativeObject(wrapper, 93, result.value_handle, ".?AV?$CPdxPtr@VCDataTechnology@@@@")
            self.handles[result.value_handle] = key
            return Query(result)
        if expression.startswith("Country.AccessResearchableTechnologies"):
            rows = self.candidates[expression.removeprefix("Country.AccessResearchableTechnologies")]
            result = self.new_result(None)
            self.sources[result.value_handle] = rows
            result.value = data.NativeList(result.value_handle, 0xA0000, b"", "owned-list")
            return Query(result)
        key = self.handles[bindings[0].object]
        if expression == "Technology.GetProgress(Country.Self)":
            value = data.FixedPoint(5000000)
        elif expression == "Technology.GetCost(Country.Self)":
            value = data.FixedPoint(1500000000)
        elif expression == "Technology.HasResearchedTech(Country.Self)":
            value = False
        elif expression == "Technology.IsResearching(Country.Self)":
            value = key == "atmospheric_engine"
        elif expression in ("Technology.IsResearchable", "Technology.IsResearchable(Country.Self)",
                            "IsValid(Technology.Research(Country.Self))"):
            value = key == "atmospheric_engine"
        elif expression in ("Technology.GetName", "Technology.GetNameNoFormatting"):
            value = "Atmospheric Engine" if key == "atmospheric_engine" else "Line Infantry"
        else:
            raise AssertionError("unproved expression: " + expression)
        return Query(self.new_result(value))

    @property
    def handles(self):
        if not hasattr(self, "_handles"):
            self._handles = {}
        return self._handles

    def element(self, client, source, index, **options):
        key, origin, pointer = self.sources[source][index]
        result = self.new_result(None)
        self.handles[result.value_handle] = key
        result.value = data.NativeObject(pointer, 93, result.value_handle,
            ".?AV?$CPdxPtr@VCDataTechnology@@@@")
        return Query(result)

    def copy(self, client, function, arguments, **options):
        buffer, source, size = arguments
        origins = {wrapper + 0x10: origin for origin, wrapper in self.factory_objects.values()}
        self.selected_origin = origins[source]
        return Query(R(error=0, callee_result=R(error=0, rax=buffer)))

    def release(self, client, handle, **options):
        self.release_calls.append(handle)
        return Query(R(error=0, value_handle=0))

    def add_completed_definition(self):
        key="bessemer_process"
        self.definitions.append(R(error=0,key_error=0,pointer=0x70100,key=key))
        self.factory_objects[key]=(0x70100,0x90100)
        self.researched_keys.add(key)
        return key

    async def test_details_finds_completed_definition_outside_candidates_without_any_action(self):
        key=self.add_completed_definition()
        with patch.object(self.api,"_candidates",AsyncMock(side_effect=AssertionError("not a candidate lookup"))), \
                patch.object(self.api,"_eligibility",AsyncMock(side_effect=AssertionError("no eligibility action"))), \
                patch.object(self.helper,"_technology",AsyncMock(side_effect=AssertionError("no factory retag"))), \
                patch.object(data,"list_count",side_effect=AssertionError("no candidate list")), \
                patch.object(data,"evaluate",side_effect=self.evaluate) as evaluated:
            result=await self.api.details(key)
        self.assertTrue(result["researched"])
        self.assertFalse(result["is_current"])
        self.assertEqual(result["key"],key)
        self.assertEqual(result["name"],"Bessemer Process")
        self.assertEqual((result["progress"],result["cost"],result["unit"]),(50,15000,"research_points"))
        self.assertIsNone(result["category"])
        self.assertIn("not queried",result["category_reason"])
        self.assertIsNone(result["can_start"])
        expressions=[call.args[1] for call in evaluated.call_args_list]
        self.assertEqual(len(expressions),5)
        self.assertTrue(all(expression.startswith("GetTechnology('bessemer_process').") for expression in expressions))
        self.assertIn("GetTechnology('bessemer_process').HasResearchedTech(Country.Self)",expressions)
        self.assertFalse(any("IsValid" in expression or "IsResearchable" in expression or ".Research(" in expression for expression in expressions))
        self.assertFalse(any(field in result for field in ("origin","pointer","binding","value_handle")))
        plan=self.plans[-1]
        self.assertEqual(plan["submit_count"],0)
        self.assertTrue(plan["run"].status == "complete")
        self.assertEqual(plan["run"].values,[])
        self.assertEqual(len(self.release_calls),len(set(self.release_calls)))
        self.assertEqual(self.ctx.labels,["research.details"])

    async def test_details_completion_is_actual_native_bool_not_progress_inference(self):
        original=self.evaluate
        def evaluate(client,expression,bindings,**options):
            query=original(client,expression,bindings,**options)
            if expression.endswith(".GetProgress(Country.Self)"):
                query.result.value=data.FixedPoint(1500000000)  # Equal cost is not completion proof.
            return query
        with patch.object(data,"evaluate",side_effect=evaluate):
            result=await self.api.details("line_infantry")
        self.assertEqual(result["progress"],result["cost"])
        self.assertFalse(result["researched"])

    async def test_details_unknown_duplicate_or_failed_key_never_uses_factory_fallback(self):
        with patch.object(data,"evaluate",side_effect=AssertionError("no unproved factory fallback")):
            with self.assertRaisesRegex(ValueError,"not found in current native definitions"):
                await self.api.details("not_a_native_technology")
            self.definitions.append(R(error=0,key_error=0,pointer=0x77700,key="line_infantry"))
            with self.assertRaisesRegex(RuntimeError,"not unique"):
                await self.api.details("line_infantry")
            self.definitions.pop()
            self.definitions[1].key_error=13
            with self.assertRaisesRegex(RuntimeError,"could not be read completely"):
                await self.api.details("line_infantry")
        self.assertTrue(all(plan["run"].status == "complete" for plan in self.plans))

    async def test_details_native_type_or_arity_failure_is_not_false_or_zero(self):
        original=self.evaluate
        for getter,value in (("HasResearchedTech",1),("GetProgress",0)):
            with self.subTest(getter=getter):
                def evaluate(client,expression,bindings,**options):
                    query=original(client,expression,bindings,**options)
                    if expression.endswith("."+getter+"(Country.Self)"):
                        query.result.value=value
                    return query
                with patch.object(data,"evaluate",side_effect=evaluate), self.assertRaisesRegex(RuntimeError,"unexpected type"):
                    await self.api.details("line_infantry")
                self.assertEqual(self.plans[-1]["run"].values,[])
        functions={name:R(name=name,argument_count=0 if name=="HasResearchedTech" else 1)
            for name in ("GetProgress","GetCost","HasResearchedTech","IsResearching")}
        with patch.object(self.api,"_functions",AsyncMock(return_value=functions)), self.assertRaisesRegex(RuntimeError,"HasResearchedTech"):
            await self.api.details("line_infantry")
        self.assertEqual(self.plans[-1]["run"].values,[])

    async def test_details_does_not_bind_definition_pointer_and_cleans_rich_name(self):
        key=self.add_completed_definition()
        self.definitions[-1].pointer=0xDEADBEEF  # Never a CData receiver input.
        rich="\x15tooltippable_name \x15tooltip:token,TechnologyTooltip Bessemer Process\x15!\x15!"
        original=self.evaluate
        def evaluate(client,expression,bindings,**options):
            self.assertTrue(all(binding.country_id for binding in bindings))
            query=original(client,expression,bindings,**options)
            if expression.endswith(".GetNameNoFormatting"):
                query.result.value=rich
            return query
        with patch.object(data,"evaluate",side_effect=evaluate):
            result=await self.api.details(key)
        self.assertEqual(result["name"],"Bessemer Process")
        self.assertEqual(self.plans[-1]["rich_names"][0]["value"],rich)
        self.assertNotIn("\x15",result["name"])
        self.assertEqual(self.plans[-1]["run"].values,[])

    async def test_details_invalid_input_is_rejected_before_work(self):
        for key in (None,"",17," line_infantry","line_infantry\0"):
            with self.subTest(key=key),self.assertRaises(ValueError):
                await self.api.details(key)
        self.assertEqual(self.ctx.labels,[])
        self.assertEqual(self.plans,[])

    async def test_details_pending_resumes_same_scalar_query_and_never_changes_queue(self):
        from pdx_runtime.v3_game._core import GameContext,PendingOperation
        key=self.add_completed_definition()
        ctx=GameContext(self.ctx.session,self.ctx.process,adapters=self.ctx.adapters,timeout=.001,poll_interval=.001)
        ctx.world,ctx.image_base=self.ctx.world,self.ctx.image_base
        ctx.refresh=AsyncMock()
        ctx.country_id=AsyncMock(return_value=0x1000007)
        ctx.type_id=AsyncMock(side_effect=lambda name:{"Country":71,"Technology":93}[name])
        originals=[]
        loop=asyncio.get_running_loop()
        started,proceed=asyncio.Event(),Event()
        def gate(client,expression,bindings,**options):
            query=self.evaluate(client,expression,bindings,**options)
            if expression.endswith(".HasResearchedTech(Country.Self)"):
                originals.append(query)
                original_read=query.read
                def read():
                    if not proceed.is_set():
                        loop.call_soon_threadsafe(started.set)
                        return None
                    return original_read()
                query.read=read
            return query
        pending=None
        with patch.object(data,"evaluate",side_effect=gate) as evaluated:
            try:
                pending=await ResearchAPI(ctx).details(key)
                self.assertIsInstance(pending,PendingOperation)
                await asyncio.wait_for(started.wait(),1)
                self.assertEqual(len(originals),1)
                self.assertIn(originals[0],ctx.pending_queries)
                proceed.set()
                details=await pending.resume(timeout=1)
            finally:
                proceed.set()
                if isinstance(pending,PendingOperation):
                    await pending.task
        self.assertTrue(details["researched"])
        self.assertEqual(sum(call.args[1].endswith(".HasResearchedTech(Country.Self)") for call in evaluated.call_args_list),1)
        self.assertEqual(ctx.pending_queries,[])
        self.assertFalse(ctx._query_lock.locked())
        self.assertEqual(ctx.owner_pool.snapshot(), ())

    async def test_options_discover_key_native_eligibility_points_and_cleanup(self):
        rows = await self.api.options()
        self.assertEqual([r["key"] for r in rows], ["atmospheric_engine", "line_infantry"])
        self.assertEqual(rows[0]["name"], "Atmospheric Engine")
        self.assertEqual(rows[0]["progress"], 50)
        self.assertEqual(rows[0]["cost"], 15000)
        self.assertEqual(rows[0]["unit"], "research_points")
        self.assertTrue(rows[0]["researchable"])
        self.assertFalse(rows[1]["researchable"])
        self.assertIn("native", rows[1]["eligibility_reason"])
        plan = self.plans[-1]
        self.assertEqual(plan["types"], {"Country": 71, "Technology": 93})
        self.assertTrue(plan["run"].status == "complete")
        self.assertEqual(plan["run"].values, [])
        self.assertEqual(len(self.release_calls), len(set(self.release_calls)))
        self.assertFalse(any("index" in row or "origin" in row or "binding" in row for row in rows))
        self.assertEqual(self.ctx.labels, ["research.options"])

    async def test_category_and_key_validation_happen_before_work(self):
        for value in ("navy", "Production", 17, []):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                await self.api.options(value)
        for value in (None, "", " atmospheric_engine", 7):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.api.start(value)
        self.assertEqual(self.ctx.labels, [])
        self.assertEqual(self.plans, [])

    async def test_current_reads_genuine_identity_plus_native_progress_not_pointer(self):
        result = await self.api.current()
        self.assertEqual(result["key"], "atmospheric_engine")
        self.assertEqual(result["country_id"], 0x1000007)
        self.assertEqual(result["progress"], 50)
        self.assertTrue(result["is_current"])
        args = self.ctx.current_helper.read_current.call_args.args
        self.assertIs(args[0], self.ctx.session)
        self.assertIs(args[1], self.ctx.process)
        self.assertEqual(args[2]["actor_id"], 0x1000007)
        self.assertNotIn("pointer", result)

    async def test_no_current_is_not_zero_progress(self):
        self.ctx.current_helper.read_current.return_value = {"current_known": True, "has_current_research": False,
                                                    "key_known": False, "key": None}
        result = await self.api.current()
        self.assertIsNone(result["key"])
        self.assertIsNone(result["progress"])
        self.assertEqual(result["reason"], "no current research")
        self.assertEqual(self.plans, [])

    async def test_origin_mismatch_rejects_instead_of_joining_by_index_and_cleans(self):
        self.definitions[0].pointer = 0xDEAD
        with self.assertRaisesRegex(RuntimeError, "unique complete native definition key"):
            await self.api.options("production")
        self.assertEqual(self.plans[-1]["run"].values, [])

    async def test_candidate_metadata_predicate_required_not_inferred_from_list(self):
        with patch.object(self.api, "_functions", AsyncMock(return_value={})), self.assertRaisesRegex(
                RuntimeError, "native Technology.GetProgress"):
            await self.api.options("production")
        self.assertEqual(self.plans[-1]["run"].values, [])

    async def test_foreign_country_does_not_inherit_zero_argument_player_eligibility(self):
        self.ctx.explicit_country_id = 0x1000022
        self.ctx.country_id = AsyncMock(return_value=0x1000022)
        functions = {name: R(name=name, argument_count=0 if name in ("GetName", "IsResearchable") else 1)
            for name in ("GetProgress", "GetCost", "HasResearchedTech", "IsResearching", "Research", "GetName", "IsResearchable")}
        with patch.object(self.api, "_functions", AsyncMock(return_value=functions)), \
                patch.object(data, "evaluate", side_effect=self.evaluate) as evaluated:
            rows = await self.api.options("production")
        self.assertIsNone(rows[0]["researchable"])
        self.assertTrue(rows[0]["can_start"])
        expressions = [call.args[1] for call in evaluated.call_args_list]
        self.assertNotIn("Technology.IsResearchable", expressions)
        self.assertIn("IsValid(Technology.Research(Country.Self))", expressions)
        validation = next(call for call in evaluated.call_args_list
                          if call.args[1] == "IsValid(Technology.Research(Country.Self))")
        self.assertEqual(validation.args[2][1], data.ObjectBinding.country(71, 0x1000022))

    async def test_current_progress_does_not_depend_on_candidate_membership(self):
        self.candidates = {category: [] for category in self.candidates}
        with patch.object(self.api, "_candidates", AsyncMock(side_effect=AssertionError("current is not a candidate"))):
            result = await self.api.current()
        self.assertEqual(result["key"], "atmospheric_engine")
        self.assertEqual(result["progress"], 50)
        self.assertEqual(result["cost"], 15000)
        self.assertTrue(result["is_current"])
        self.assertTrue(self.ctx.current_helper.read_current.call_args.args[2]["with_progress"])
        self.assertEqual(result["name"], "Atmospheric Engine")
        self.assertEqual(self.plans[-1]["run"].values, [])

    async def test_zero_argument_native_flag_does_not_override_country_selection_rejection(self):
        functions = {name: R(name=name, argument_count=0 if name in ("GetName", "IsResearchable") else 1)
            for name in ("GetProgress", "GetCost", "HasResearchedTech", "IsResearching", "Research", "GetName", "IsResearchable")}
        original = self.evaluate
        def evaluate(client, expression, bindings, **options):
            query = original(client, expression, bindings, **options)
            if expression == "IsValid(Technology.Research(Country.Self))":
                query.result.value = False
            return query
        with patch.object(self.api, "_functions", AsyncMock(return_value=functions)), \
                patch.object(data, "evaluate", side_effect=evaluate):
            rows = await self.api.options("production")
        self.assertTrue(rows[0]["researchable"])
        self.assertFalse(rows[0]["can_start"])
        self.assertIn("native research selection validation", rows[0]["eligibility_reason"])

    async def test_options_plain_display_retains_original_tooltip_text_in_diagnostics(self):
        rich = "\x15tooltippable_name \x15tooltip:dw_2693000601,wAaLtfQCAAA=,DATA_TECHNOLOGY_NAME_TOOLTIP,TechnologyTooltip 轧棉机\x15!\x15!"
        original = self.evaluate
        def evaluate(client, expression, bindings, **options):
            query = original(client, expression, bindings, **options)
            if expression in ("Technology.GetName", "Technology.GetNameNoFormatting"):
                query.result.value = rich
            return query
        functions = {name: R(name=name, argument_count=0 if name == "GetName" else 1)
            for name in ("GetProgress", "GetCost", "HasResearchedTech", "IsResearching", "IsResearchable", "GetName")}
        with patch.object(self.api, "_functions", AsyncMock(return_value=functions)), \
                patch.object(data, "evaluate", side_effect=evaluate):
            rows = await self.api.options("production")
        self.assertEqual(rows[0]["name"], "轧棉机")
        self.assertNotIn("\x15", rows[0]["name"])
        self.assertEqual(self.plans[-1]["rich_names"][0]["value"], rich)
        self.assertEqual(rows[0]["key"], "atmospheric_engine")

    async def test_current_compound_name_is_plain_and_rich_value_stays_private(self):
        rich = "\x15tooltippable_name \x15tooltip:token,EngineTooltip Atmospheric Engine\x15!\x15!"
        original = self.evaluate
        def evaluate(client, expression, bindings, **options):
            query = original(client, expression, bindings, **options)
            if expression.startswith("GetTechnology("):
                query.result.value = rich
            return query
        with patch.object(data, "evaluate", side_effect=evaluate):
            result = await self.api.current()
        self.assertEqual(result["name"], "Atmospheric Engine")
        self.assertEqual(self.plans[-1]["rich_names"][0]["value"], rich)
        self.assertNotIn("rich_names", result)

    async def test_cached_display_name_is_clean_after_plain_formatter_hot_update(self):
        rich = "\x15tooltippable_name \x15tooltip:token,TechnologyTooltip 轧棉机\x15!\x15!"
        self.api._name_results[(self.ctx.world, "atmospheric_engine", "compound_name")] = (rich, None)
        with patch.object(data, "evaluate", side_effect=AssertionError("cached string needs no native call")):
            name, reason = await self.api._current_name("atmospheric_engine", self.ctx.world)
        self.assertEqual(name, "轧棉机")
        self.assertIsNone(reason)

    async def test_current_name_consumes_byvalue_factory_only_inside_one_evaluate(self):
        self.api._name_results[(self.ctx.world, "atmospheric_engine")] = (None, "old split receiver guard")
        with patch.object(self.helper, "_technology", AsyncMock(side_effect=AssertionError("must not rebind factory result"))), \
                patch.object(self.api, "_definitions", AsyncMock(side_effect=AssertionError("current key is already genuine"))), \
                patch.object(data, "evaluate", side_effect=self.evaluate) as evaluated:
            result = await self.api.current()
        self.assertEqual(result["name"], "Atmospheric Engine")
        self.assertEqual(result["progress"], 50)
        self.assertEqual(result["cost"], 15000)
        calls = [call for call in evaluated.call_args_list if call.args[1].startswith("GetTechnology(")]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[1], "GetTechnology('atmospheric_engine').GetNameNoFormatting")
        self.assertEqual(calls[0].args[2], ())
        self.assertEqual(self.plans[-1]["run"].values, [])

    async def test_current_name_compound_expression_caches_known_failure(self):
        original = self.evaluate
        def error_factory(client, expression, bindings, **options):
            if expression.startswith("GetTechnology("):
                return Query(R(error=13, value_handle=0))
            return original(client, expression, bindings, **options)
        with patch.object(data, "evaluate", side_effect=error_factory) as evaluated:
            first = await self.api.current()
            second = await self.api.current()
        self.assertEqual(first["progress"], 50)
        self.assertEqual(second["cost"], 15000)
        self.assertIsNone(first["name"])
        self.assertIn("could not be read", first["name_reason"])
        self.assertEqual(sum(call.args[1].startswith("GetTechnology(") for call in evaluated.call_args_list), 1)

    async def test_non_country_predicate_signature_uses_real_gui_selection_validation(self):
        # Reproduce the live failure: IsResearchable exists but is NOT arity1.
        # No attempt is made to guess its additional arguments or implicit actor.
        for arity in (0, 2):
            with self.subTest(arity=arity):
                functions = {name: R(name=name, argument_count=0 if name == "GetName" else 1)
                    for name in ("GetProgress", "GetCost", "HasResearchedTech", "IsResearching", "Research", "GetName")}
                functions["IsResearchable"] = R(name="IsResearchable", argument_count=arity)
                with patch.object(self.api, "_functions", AsyncMock(return_value=functions)), \
                        patch.object(data, "evaluate", side_effect=self.evaluate) as evaluated:
                    rows = await self.api.options("production")
                self.assertEqual(rows[0]["key"], "atmospheric_engine")
                self.assertTrue(rows[0]["can_start"])
                self.assertIs(rows[0]["researchable"], True if arity == 0 else None)
                self.assertEqual(rows[0]["cost"], 15000)
                expressions = [call.args[1] for call in evaluated.call_args_list]
                self.assertIn("IsValid(Technology.Research(Country.Self))", expressions)
                self.assertNotIn("Technology.IsResearchable(Country.Self)", expressions)
                if arity == 0:
                    self.assertIn("Technology.IsResearchable", expressions)
                validation = next(call for call in evaluated.call_args_list
                                  if call.args[1] == "IsValid(Technology.Research(Country.Self))")
                self.assertEqual(validation.args[2][1], data.ObjectBinding.country(71, 0x1000007))
                self.assertTrue(validation.args[2][0].retained)
                self.assertEqual(self.plans[-1]["run"].values, [])
                self.assertEqual(self.plans[-1]["submit_count"], 0)

    async def test_gui_selection_rejection_stays_false_not_candidate_membership_guess(self):
        functions = {name: R(name=name, argument_count=0 if name == "GetName" else 1)
            for name in ("GetProgress", "GetCost", "HasResearchedTech", "IsResearching", "Research", "GetName")}
        functions["IsResearchable"] = R(name="IsResearchable", argument_count=2)
        with patch.object(self.api, "_functions", AsyncMock(return_value=functions)):
            rows = await self.api.options("military")
        self.assertEqual(rows[0]["key"], "line_infantry")
        self.assertFalse(rows[0]["can_start"])
        self.assertIsNone(rows[0]["researchable"])
        self.assertIn("native research selection validation", rows[0]["eligibility_reason"])
        self.assertEqual(self.plans[-1]["submit_count"], 0)

    async def test_unavailable_eligibility_signature_keeps_genuine_candidates_unknown(self):
        functions = {name: R(name=name, argument_count=0 if name == "GetName" else 1)
            for name in ("GetProgress", "GetCost", "HasResearchedTech", "IsResearching", "GetName")}
        functions["IsResearchable"] = R(name="IsResearchable", argument_count=2)
        with patch.object(self.api, "_functions", AsyncMock(return_value=functions)), \
                patch.object(data, "evaluate", side_effect=self.evaluate) as evaluated:
            rows = await self.api.options("production")
        self.assertEqual(rows[0]["key"], "atmospheric_engine")
        self.assertEqual(rows[0]["progress"], 50)
        self.assertIsNone(rows[0]["researchable"])
        self.assertIn("function signatures", rows[0]["eligibility_reason"])
        self.assertFalse(any("IsResearchable" in call.args[1] or "IsValid" in call.args[1]
                             for call in evaluated.call_args_list))

    async def test_definition_text_growth_and_paging_are_automatic(self):
        helper, plan = await self.api._new_plan()
        first, second = self.definitions
        pages = [R(error=234, world=self.ctx.world, records=(), next_index=0, slot_bound=2, truncated=True,
                   definition_options=R(text_bytes_required=70000)),
                 R(error=0, world=self.ctx.world, records=(first,), next_index=1, slot_bound=2, truncated=True),
                 R(error=0, world=self.ctx.world, records=(second,), next_index=2, slot_bound=2, truncated=False)]
        with patch.object(helper.objects, "enumerate_technology_definitions", side_effect=lambda *a, **k: Query(pages.pop(0))) as read:
            await self.api._definitions(helper, plan)
        self.assertEqual(plan["definitions"].records, (first, second))
        self.assertEqual([call.kwargs["start_index"] for call in read.call_args_list], [0, 0, 1])
        self.assertEqual([call.kwargs["text_capacity"] for call in read.call_args_list], [65536, 70000, 70000])
        await self.api._cleanup(helper, plan)

    async def test_function_metadata_pages_do_not_use_old_type_ids(self):
        helper, plan = await self.api._new_plan()
        pages = [R(error=0, rows=(R(name="GetCost", argument_count=1),), next=1, total=2),
                 R(error=0, rows=(R(name="IsResearchable", argument_count=1),), next=2, total=2)]
        with patch.object(data, "enumerate_functions", side_effect=lambda *a, **k: Query(pages.pop(0))) as read:
            functions = await self.api._functions(helper, plan)
        self.assertEqual(set(functions), {"GetCost", "IsResearchable"})
        self.assertEqual([call.args[1] for call in read.call_args_list], [93, 93])
        self.assertEqual([call.kwargs["start"] for call in read.call_args_list], [0, 1])
        await self.api._cleanup(helper, plan)

    async def test_game_points_are_exact_even_with_low_decimal_precision(self):
        from decimal import Decimal, Inexact, Rounded, localcontext
        def evaluate(client, expression, bindings, **options):
            query = self.evaluate(client, expression, bindings, **options)
            if expression == "Technology.GetProgress(Country.Self)":
                query.result.value = data.FixedPoint(9223372036854775807)
            elif expression == "Technology.GetCost(Country.Self)":
                query.result.value = data.FixedPoint(0xffffffffffffffff)
            return query
        with localcontext() as context, patch.object(data, "evaluate", side_effect=evaluate):
            context.prec = 2
            context.traps[Inexact] = context.traps[Rounded] = True
            rows = await self.api.options("production")
        self.assertEqual(rows[0]["progress"], Decimal("92233720368547.75807"))
        self.assertEqual(rows[0]["cost"], Decimal("-0.00001"))
        self.assertEqual(rows[0]["unit"], "research_points")

    async def test_packaged_module_aliases_resolve_real_loaded_base(self):
        self.ctx.adapters = {role: "pdx_v3_data.dll" for role in self.ctx.adapters}
        self.ctx.process.modules.return_value = [{"name": "pdx_runtime.dll", "base": 0xABC000}]
        self.assertEqual(self.api._module_bases(("data", "carrier")), {"data": 0xABC000, "carrier": 0xABC000})
        self.ctx.process.modules.assert_called_once()

    async def test_literal_module_bases_preserve_zero_without_scanning_or_aliasing(self):
        self.ctx.adapters = {"data": 0, "carrier": 0x1ABCDEF0000}
        self.assertEqual(self.api._module_bases(("data", "carrier")),
                         {"data": 0, "carrier": 0x1ABCDEF0000})
        self.ctx.process.modules.assert_not_called()
        self.ctx.process.resolve_export.assert_not_called()

    async def test_non_module_adapter_values_do_not_become_integer_bases(self):
        for adapter in (False, True, None, 17.0):
            with self.subTest(adapter=adapter):
                self.ctx.adapters = {"data": adapter}
                with self.assertRaisesRegex(ValueError, "loaded module name or base"):
                    self.api._module_bases(("data",))
        self.ctx.process.modules.assert_not_called()
        self.ctx.process.resolve_export.assert_not_called()

    async def test_loaded_split_module_precedes_explicit_packaged_alias(self):
        self.ctx.adapters = {"data": "PDX_V3_DATA", "carrier": "pdx_runtime.dll"}
        self.ctx.process.modules.return_value = [
            {"name": "pdx_runtime.dll", "base": 0xABC000},
            {"name": "PDX_V3_DATA.DLL", "base": 0xDEF000},
        ]
        self.assertEqual(self.api._module_bases(("data", "carrier")),
                         {"data": 0xDEF000, "carrier": 0xABC000})
        self.ctx.process.modules.assert_called_once()
        self.ctx.process.resolve_export.assert_not_called()

    async def test_unknown_module_name_never_uses_blanket_runtime_fallback(self):
        self.ctx.adapters = {"data": "unrelated.dll"}
        self.ctx.process.modules.return_value = [{"name": "pdx_runtime.dll", "base": 0xABC000}]
        with self.assertRaisesRegex(RuntimeError, "not loaded: unrelated.dll"):
            self.api._module_bases(("data",))
        self.ctx.process.modules.assert_called_once()
        self.ctx.process.resolve_export.assert_not_called()

    async def test_real_context_timeout_resumes_original_candidate_query_and_cleanup(self):
        from pdx_runtime.v3_game._core import GameContext, PendingOperation
        ctx = GameContext(self.ctx.session, self.ctx.process, adapters=self.ctx.adapters,
                          timeout=0.001, poll_interval=0.001)
        ctx.world, ctx.image_base = self.ctx.world, self.ctx.image_base
        ctx.refresh = AsyncMock()
        ctx.country_id = AsyncMock(return_value=0x1000007)
        ctx.type_id = AsyncMock(side_effect=lambda name: {"Country": 71, "Technology": 93}[name])
        originals = []
        loop = asyncio.get_running_loop()
        started, proceed = asyncio.Event(), Event()
        def gate(client, expression, bindings, **options):
            query = self.evaluate(client, expression, bindings, **options)
            if expression.startswith("Country.AccessResearchableTechnologies"):
                originals.append(query)
                original_read = query.read
                def read():
                    if not proceed.is_set():
                        loop.call_soon_threadsafe(started.set)
                        return None
                    return original_read()
                query.read = read
            return query
        pending = None
        with patch.object(data, "evaluate", side_effect=gate) as evaluated:
            try:
                pending = await ResearchAPI(ctx).options("production")
                self.assertIsInstance(pending, PendingOperation)
                await asyncio.wait_for(started.wait(), 1)
                self.assertEqual(len(originals), 1)
                self.assertIn(originals[0], ctx.pending_queries)
                proceed.set()
                rows = await pending.resume(timeout=1)
            finally:
                proceed.set()
                if isinstance(pending, PendingOperation):
                    await pending.task
        self.assertEqual(rows[0]["key"], "atmospheric_engine")
        self.assertEqual(sum(call.args[1].startswith("Country.AccessResearchableTechnologies")
                             for call in evaluated.call_args_list), 1)
        self.assertEqual(ctx.pending_queries, [])
        self.assertEqual(ctx.owner_pool.snapshot(), ())
        self.assertFalse(ctx._query_lock.locked())

    async def test_queue_command_uses_existing_run_for_native_clone_submit_and_cleanup(self):
        helper, plan = await self.api._new_plan()
        before = {"keys": (), "current_after": {"present": False, "target_is_current": False}}
        native_wire = commands.encode_command(23339, {10304: commands.UInt32(0x1000007),
            65: {12713: "atmospheric_engine", 23341: False}})
        calls = []
        class Owned:
            world, type_token, command = self.ctx.world, 23339, 0xB0000
            pending, attempt = None, R(outcome="owned")
            def serialize(inner):
                calls.append("serialize")
                return Query(R(error=0, data=native_wire))
            def validate(inner, capacity):
                calls.append("validate")
                return Query(R(error=0, validated=1, reason=""))
            def submit(inner, flags):
                calls.append(("submit", flags))
                inner.command, inner.attempt = 0, R(outcome="consumed")
                return Query(R(error=0, accepted=1, command=0))
            def destroy(inner):
                calls.append("destroy")
                inner.command, inner.attempt = 0, R(outcome="consumed")
                return Query(R(error=0, command=0))
        owner = Owned()
        original_evaluate = self.evaluate
        def evaluate(client, expression, bindings, **options):
            if expression == "Technology.AddToQueue(Country.Self)":
                return Query(self.new_result(data.NativeDataCommand(0xA0000, 0xA1000, 0xA0018,
                    self.next_handle + 1, "CDataCommand")))
            return original_evaluate(client, expression, bindings, **options)
        carrier = data.NativeDataCommand(0xA0000, 0xA1000, 0xA0018, 800,
            "CDataCommand", self.ctx.world)
        clone = R(error=0, action=commands.Action.CLONE, cloned_command=owner.command,
            command=0, world=self.ctx.world, type_token=23339)
        with patch.object(data, "evaluate", side_effect=evaluate), \
             patch.object(data, "command_value", return_value=Query(R(error=0, value=carrier, value_handle=0))), \
             patch.object(commands, "request", return_value=Query(clone)) as cloned, \
             patch.object(commands.NativeCommand, "from_result", return_value=owner), \
             patch.object(helper, "observe_queue", AsyncMock(return_value=before)):
            await helper.prepare_enqueue(plan, index=0, expected_key="atmospheric_engine")
            result = await helper.submit_prepared(plan, enqueue=True)
            await helper.cleanup_known(plan)
        self.assertEqual(cloned.call_args.kwargs["command"], 0xA0000)
        self.assertEqual(cloned.call_args.kwargs["world"], self.ctx.world)
        self.assertEqual(calls, ["serialize", "validate", ("submit", 4)])
        self.assertEqual(result.accepted, 1)
        self.assertEqual(plan["run"].status, "complete")
        self.assertEqual(plan["run"].values, [])
        self.assertEqual(self.ctx.owner_pool.snapshot(), ())

    async def test_queue_preserves_native_order_and_excludes_current(self):
        observed = {"entries": ({"key": "b"}, {"key": "a"}, {"key": "b"})}
        with patch.object(self.helper, "observe_queue", AsyncMock(return_value=observed)):
            rows = await self.api.queue()
        self.assertEqual(rows, [{"key": "b", "position": 1}, {"key": "a", "position": 2}, {"key": "b", "position": 3}])
        self.assertNotIn("atmospheric_engine", [r["key"] for r in rows])
        self.assertEqual(self.plans[-1]["run"].values, [])


def submit_result(*, accepted=1, error=0):
    """Completed native-layout output fixture; no command or process is executed."""
    return commands.CommandResult(size=commands.RESULT.size, error=error,
        action=commands.Action.SUBMIT, type_token=0, command=0, buffer=0,
        buffer_capacity=0, buffer_size=0, count=0, submit_flags=4, validated=0,
        accepted=accepted, execution_thread_id=55, reserved=0,
        application=0x40000, world=0x50000, session=0x60000)


class ResearchActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.helper = R(prepare_enqueue=AsyncMock(), prepare_dequeue=AsyncMock(),
                        submit_prepared=AsyncMock(return_value=submit_result()),
                        observe_result=AsyncMock(), cleanup_known=AsyncMock())
        self.ctx = Context(self.helper)
        self.api = ResearchAPI(self.ctx)
        self.scan = {"world": self.ctx.world, "actor_id": 0x1000007, "image_base": self.ctx.image_base}
        self.plan = {"pending": None, "buffers": [], "uncertain_operations": [], "unadopted_clones": [],
                     "consumptions": [], "before": {"current_after": {"present": False}},
                     "validation": R(validated=True, reason=""), "target_is_current_after": True,
                     "owner": R(attempt=R(outcome="consumed")),
                     "queue_changed": False, "target_selection_changed": True}
        self.helper.prepare_enqueue.return_value = self.plan
        self.helper.prepare_dequeue.return_value = self.plan
        self.helper.observe_result.return_value = {"keys": (), "entries": ()}
        self.api._new_plan = AsyncMock(return_value=(self.helper, self.plan))
        self.api._cleanup = AsyncMock()
        self.api._candidates = AsyncMock(return_value=[{"public": {"key": "atmospheric_engine", "researchable": True,
            "is_current": False}, "expression": "Country.AccessResearchableTechnologiesProduction", "index": 3}])
        self.api._identity = AsyncMock(return_value={"key": None, "has_current_research": False})

    async def test_start_hides_prepare_validate_submit_observe_and_cleanup(self):
        result = await self.api.start("atmospheric_engine")
        call = self.helper.prepare_enqueue.call_args
        self.assertEqual(call.kwargs["expected_key"], "atmospheric_engine")
        self.assertEqual(call.kwargs["index"], 3)
        self.assertIs(call.args[0], self.plan)
        self.helper.submit_prepared.assert_awaited_once_with(self.plan, enqueue=True)
        self.helper.observe_result.assert_awaited_once_with(self.plan)
        self.assertEqual(result["status"], "current")
        self.assertTrue(result["accepted"])
        self.assertTrue(result["is_current"])
        self.assertEqual(self.api._cleanup.await_count, 2)
        self.assertNotIn("plan", result)
        self.assertNotIn("validation", result)

    async def test_start_does_not_preempt_existing_research_or_replay_current(self):
        self.api._identity.return_value = {"key": "other_technology", "has_current_research": True}
        with self.assertRaisesRegex(ValueError, "enqueue.*without preempting"):
            await self.api.start("atmospheric_engine")
        self.helper.prepare_enqueue.assert_not_awaited()
        self.api._identity.return_value = {"key": "atmospheric_engine", "has_current_research": True}
        result = await self.api.start("atmospheric_engine")
        self.assertEqual(result["status"], "already_current")
        self.assertFalse(result["submitted"])
        self.api._new_plan.assert_not_awaited()

    async def test_start_requires_actual_eligibility_but_enqueue_allows_native_queue_rules(self):
        self.api._candidates.return_value[0]["public"]["researchable"] = False
        self.api._candidates.return_value[0]["public"]["eligibility_reason"] = "native researchability predicate is false"
        with self.assertRaisesRegex(ValueError, "researchability predicate is false"):
            await self.api.start("atmospheric_engine")
        self.helper.prepare_enqueue.assert_not_awaited()
        result = await self.api.enqueue("atmospheric_engine")
        self.assertTrue(result["accepted"])
        self.helper.prepare_enqueue.assert_awaited_once()

    async def test_rejection_keeps_native_game_reason_and_never_submits(self):
        self.plan["validation"] = R(validated=False, reason="Technology is already researched")
        with self.assertRaisesRegex(ValueError, "Technology is already researched"):
            await self.api.enqueue("atmospheric_engine")
        self.helper.submit_prepared.assert_not_awaited()
        self.assertIs(self.api._cleanup.call_args.args[1], self.plan)

    async def test_accepted_is_not_falsely_reported_selected(self):
        self.plan["target_is_current_after"] = False
        self.plan["target_selection_changed"] = False
        self.helper.observe_result.return_value = {"keys": ("atmospheric_engine",),
            "entries": ({"key": "atmospheric_engine"},)}
        result = await self.api.start("atmospheric_engine")
        self.assertEqual(result["status"], "queued")
        self.assertFalse(result["is_current"])

    async def test_queue_change_does_not_submit_or_get_reported_as_acceptance(self):
        self.helper.submit_prepared.return_value = None
        with self.assertRaisesRegex(RuntimeError, "no command was submitted"):
            await self.api.enqueue("atmospheric_engine")
        self.helper.observe_result.assert_not_awaited()
        self.helper.submit_prepared.assert_awaited_once()

    async def test_dequeue_uses_actual_bottom_source_and_reports_native_pruning(self):
        self.api._definitions = AsyncMock()
        self.helper.observe_queue = AsyncMock(return_value={"top": {"entries": ({"key": "a"},)},
            "bottom": {"entries": ({"key": "atmospheric_engine"},)}})
        self.plan["target_is_current_after"] = False
        result = await self.api.dequeue("atmospheric_engine")
        self.assertEqual(self.helper.prepare_dequeue.call_args.kwargs["list_expression"], "Country.GetResearchQueueBottom")
        self.helper.submit_prepared.assert_awaited_once_with(self.plan, dequeue=True)
        self.assertEqual(result["status"], "removed")
        self.assertEqual(result["queue"], [])

    async def test_prepare_failure_keeps_original_caller_owned_run_for_cleanup(self):
        self.helper.prepare_enqueue.side_effect = ValueError("native source key changed")
        with self.assertRaisesRegex(ValueError, "native source key changed"):
            await self.api.enqueue("atmospheric_engine")
        self.assertIs(self.api._cleanup.call_args.args[1], self.plan)
        self.helper.submit_prepared.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
