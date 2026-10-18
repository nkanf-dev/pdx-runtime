"""CPU copied-reminder contracts. No process, RuntimeSession or game is opened."""
import asyncio
from copy import deepcopy
from threading import Event
from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pdx_runtime import v3_data as data, v3_objects as objects
from pdx_runtime.v3_game import PendingOperation
from pdx_runtime.v3_game._core import GameContext
from pdx_runtime.v3_game.events import _read as event_read
from pdx_runtime.v3_game.notifications import NotificationsAPI, UI_SOURCES


def copied(value):
    raw = value.encode() if isinstance(value, str) else b""
    return N(error=0, value=value, result=N(snapshot=raw, snapshot_bytes=len(raw), total=len(raw),
        kind=data.Kind.STRING if isinstance(value, str) else data.Kind.BOOL))


def event_record():
    return {"instance_tag_bytes": b"native-guard", "key": copied("actual.event"),
        "window_fields": {"title": copied("Raw \x15G title\x15!"), "description": copied("Body"), "flavor": copied("")},
        "field_diagnostics": {}, "options": [{"provider_index": 0, "text": copied("Choice"),
            "desc": copied("Description"), "enabled": copied(False), "default": copied(False)}]}


def war_directory(*rows, status="observed", source_count=None, enumeration_complete=True):
    return {"wars": list(rows), "country_id": 0, "source": "Country.AccessWars",
        "scope": "selected_country_wars", "world": 77, "status": status, "start": 0, "next": len(rows),
        "has_more": not enumeration_complete, "source_count": source_count,
        "enumeration_complete": enumeration_complete, "atomic_snapshot": False, "details": []}


class Context:
    def __init__(self):
        self.world=77; self.pending_queries=[]; self.diagnostics=[]
        self.native=object(); self.process=object(); self.adapters={"data":"data"}; self.interface_thread_id=8
        self.count=1; self.wars=AsyncMock(return_value=war_directory()); self.plays=AsyncMock(return_value=[])
    async def refresh(self):
        return N(world=self.world, error=0, transition_pending=False, date_parts=(1853,10,17,0))
    async def country_id(self):return 0
    async def perform(self,label,body):return await body()
    async def type_id(self,name):return 918
    async def query(self,label,factory):
        if label=="events.pending.count":return N(error=0,source_stages=3,root_count=self.count)
        raise AssertionError("No native callback may execute in this CPU fixture")
    async def read_options(self, *args):
        args[-1].update(event_record())
    def domain(self,name):return N(wars=self.wars) if name=="military" else N(plays=self.plays)


def native_result(*, receipts=(), status="available", sources=("message_feed",)):
    return {"sources":[{"source":source,"status":status,"reason":None if status=="available" else "partial read",
        "coverage":"current_source","count":len(receipts)} for source in sources], "receipts":deepcopy(list(receipts))}


def native_receipt(identity=None):
    return {"source":"message_feed","kind":"message","provenance":"native_ui_data",
        "native_identity":identity,"source_index":0,"fields":{"title":{"value":"Original \x15G text\x15!",
        "raw_bytes":b"copied","complete":True,"reason":None}},"visibility":{"on_screen":None},"actions":[]}


class Tests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context(); self.api=NotificationsAPI(self.ctx)
        reader_patch=patch.object(event_read,"read_pending_options",side_effect=self.ctx.read_options)
        reader_patch.start();self.addCleanup(reader_patch.stop)
        self.provider=N(observe=AsyncMock(return_value=native_result()))
        self.importer=patch("pdx_runtime.v3_game.notifications.importlib.import_module",return_value=self.provider)
        self.importer.start();self.addCleanup(self.importer.stop)


    async def test_real_event_adapter_preserves_raw_text_false_zero_and_local_identity(self):
        snapshot=await self.api.observe(sources=("event_windows",))
        receipt=snapshot["receipts"][0]
        self.assertEqual(receipt["raw_text"]["title"],"Raw \x15G title\x15!")
        self.assertEqual(receipt["fields"]["title"]["raw_bytes"],b"Raw \x15G title\x15!")
        self.assertIsNone(receipt["native_identity"])
        self.assertEqual(receipt["local_id"],"event-1")
        self.assertEqual(receipt["actor_country_id"],0)
        self.assertEqual(receipt["actions"][0]["option_index"],0)
        self.assertIs(receipt["actions"][0]["enabled"],False)
        self.assertIs(receipt["actions"][0]["default"],False)
        self.assertFalse(snapshot["atomic_snapshot"])
        self.assertTrue(snapshot["selected_sources_complete"])
        self.assertFalse(snapshot["coverage_complete"])
        self.provider.observe.assert_not_awaited()

    async def test_empty_modals_and_missing_sources_are_not_complete_or_peace(self):
        self.ctx.count=0
        self.provider.observe.return_value={"sources":[],"receipts":[]}
        snapshot=await self.api.observe()
        self.assertEqual(len(snapshot["sources"]),len(UI_SOURCES))
        self.assertFalse(snapshot["selected_sources_complete"])
        self.assertEqual(snapshot["receipts"],[])
        self.assertNotIn("peace",snapshot)
        self.assertTrue(any(s["status"]=="unavailable" for s in snapshot["sources"]))

    async def test_unknown_identity_never_deduplicates_text_or_index(self):
        self.provider.observe.return_value=native_result(receipts=[native_receipt()])
        first=await self.api.observe(sources="message_feed")
        second=await self.api.observe(sources="message_feed",cursor=first["cursor"])
        self.assertEqual(second["cursor_status"],"valid")
        self.assertEqual(second["changes"][0]["lifecycle"],"observed")
        self.assertEqual(second["receipts"][0]["deduplication"]["status"],"unavailable")
        self.assertIsNone(second["receipts"][0]["local_id"])
        self.assertEqual(second["receipts"][0]["raw_text"]["title"],"Original \x15G text\x15!")

    async def test_proven_full_identity_deduplicates_and_tracks_observed_absence(self):
        identity=2**62+9
        self.provider.observe.return_value=native_result(receipts=[native_receipt(identity)])
        first=await self.api.observe(sources="message_feed")
        second=await self.api.observe(sources="message_feed",cursor=first["cursor"])
        self.assertEqual(second["changes"],[])
        self.assertEqual(second["receipts"][0]["native_identity"],identity)
        self.provider.observe.return_value=native_result()
        third=await self.api.observe(sources="message_feed",cursor=second["cursor"])
        self.assertEqual(third["changes"][0]["lifecycle"],"no_longer_observed")
        self.assertIn("not proof",third["changes"][0]["reason"])

    async def test_partial_source_never_claims_known_reminder_disappeared(self):
        self.provider.observe.return_value=native_result(receipts=[native_receipt(0)])
        first=await self.api.observe(sources="message_feed")
        self.provider.observe.return_value=native_result(status="partial")
        second=await self.api.observe(sources="message_feed",cursor=first["cursor"])
        self.assertEqual(second["changes"],[])
        self.assertFalse(second["selected_sources_complete"])

    async def test_failed_source_is_explicit_but_unknown_query_propagates(self):
        self.provider.observe.side_effect=RuntimeError("native error 1168")
        failed=await self.api.observe(sources="message_feed")
        self.assertEqual(failed["sources"][0]["status"],"failed")
        self.assertIn("1168",failed["sources"][0]["reason"])
        self.ctx.pending_queries=[object()]
        with self.assertRaisesRegex(RuntimeError,"1168"):
            await self.api.observe(sources="message_feed",state_sources=("wars",))
        self.ctx.wars.assert_not_awaited()

    async def test_country_war_directory_preserves_actual_roles_scope_and_partial_errors(self):
        row = {"id": 0, "name": None, "name_raw": "", "country_is_participant": True,
            "country_is_attacker": False, "country_is_defender": None, "country_is_leader": False,
            "unavailable": {"country_is_defender": "native error 1168"}}
        directory = war_directory(row, status="partial", source_count=None, enumeration_complete=False)
        directory["details"] = [{"error": 1168, "native_result": {"value": None}}]
        directory["cleanup"] = {"status": "warning", "error": 5}
        self.ctx.wars.return_value = directory
        self.ctx.plays.return_value = [{"id": 117440518, "attacker_id": 167, "defender_id": 0}]
        snapshot = await self.api.observe(sources=(), state_sources=("wars", "plays"))
        self.ctx.wars.assert_awaited_once_with(start=0, limit=64)
        self.ctx.plays.assert_awaited_once_with(all_countries=True)
        war, play = snapshot["receipts"]
        self.assertEqual(war["fields"], row)
        self.assertIs(war["fields"]["country_is_attacker"], False)
        self.assertIsNone(war["fields"]["country_is_defender"])
        self.assertEqual(war["native_identity"], 0)
        self.assertEqual(war["scope"]["source_scope"], "selected_country_wars")
        self.assertEqual(war["scope"]["country_id"], 0)
        self.assertIs(war["scope"]["source_country_participation"], True)
        self.assertIs(war["scope"]["player_participation"], True)
        self.assertTrue(all(r["provenance"] == "state_observation" for r in snapshot["receipts"]))
        self.assertTrue(all(r["active"] is None for r in snapshot["receipts"]))
        self.assertEqual(play["fields"]["defender_id"], 0)
        source = snapshot["sources"][0]
        self.assertEqual(source["directory"], directory)
        self.assertIsNone(source["directory"]["source_count"])
        self.assertFalse(source["enumeration_complete"])
        self.assertEqual(source["count"], 1)
        source["directory"]["wars"][0]["unavailable"].clear()
        self.assertIn("country_is_defender", row["unavailable"])
        self.assertEqual(snapshot["changes"][0]["lifecycle"], "first_observed")

    async def test_unavailable_country_source_never_uses_world_wars_or_zeroes_unknown_count(self):
        self.ctx.wars.return_value = war_directory(status="unavailable", enumeration_complete=False)
        with patch.object(objects, "enumerate_objects") as world:
            snapshot = await self.api.observe(sources=(), state_sources="wars")
        world.assert_not_called()
        self.assertEqual(snapshot["receipts"], [])
        source = snapshot["sources"][0]
        self.assertEqual(source["status"], "unavailable")
        self.assertIsNone(source["directory"]["source_count"])
        self.assertFalse(source["enumeration_complete"])

    async def test_stopped_country_read_keeps_prefix_and_original_error(self):
        from pdx_runtime._sampling import SamplingStopped
        row = {"id": 0, "country_is_participant": None, "unavailable": {"name": "not read"}}
        error = SamplingStopped("query_budget")
        error.military_result = war_directory(row, status="partial", source_count=3, enumeration_complete=False)
        self.ctx.wars.side_effect = error
        snapshot = await self.api.observe(sources=(), state_sources="wars")
        source = snapshot["sources"][0]
        self.assertIs(source["error"], error)
        self.assertEqual(source["sampling_status"], "stopped")
        self.assertEqual(source["directory"], error.military_result)
        self.assertEqual(snapshot["receipts"][0]["fields"], row)
        self.assertFalse(source["enumeration_complete"])
        self.ctx.wars.assert_awaited_once_with(start=0, limit=64)

    async def test_failed_country_read_keeps_prefix_without_replacing_original_pending_owner(self):
        error = RuntimeError("original native failure")
        error.military_result = war_directory({"id": 0}, status="partial", enumeration_complete=False)
        self.ctx.wars.side_effect = error
        snapshot = await self.api.observe(sources=(), state_sources="wars")
        self.assertEqual(snapshot["sources"][0]["status"], "failed")
        self.assertIs(snapshot["sources"][0]["error"], error)
        self.assertEqual(snapshot["receipts"][0]["native_identity"], 0)
        owner = object()
        self.ctx.pending_queries.append(owner)
        with self.assertRaises(RuntimeError) as raised:
            await self.api.observe(sources=(), state_sources=("wars", "plays"))
        self.assertIs(raised.exception, error)
        self.assertIs(self.ctx.pending_queries[0], owner)
        self.ctx.plays.assert_not_awaited()

    async def test_real_country_directory_composes_without_world_snapshot_or_native_execution(self):
        self.importer.stop()
        try:
            from pdx_runtime.v3_game.military import MilitaryAPI, _observations
            calls = []
            class CopiedRead:
                def __init__(read, api, label):
                    read.actor, read.world = None, api._ctx.world
                    read.native, read.process, read.adapters = api._ctx.native, api._ctx.process, api._ctx.adapters
                    read.value_adapters, read.operations = {}, []
                async def country(read):
                    read.actor = 0
                    return "actual-country-binding"
                async def provider(read, named_type, method):
                    calls.append((named_type, method))
                async def evaluate(read, expression, bindings):
                    calls.append(expression)
                    self.assertEqual(expression, "Country.AccessWars")
                    self.assertEqual(bindings, ("actual-country-binding",))
                    return N(kind=data.Kind.LIST, value=data.NativeList(100, 0, b"", "WarList"), value_handle=100)
                async def call(read, label, factory):
                    calls.append(label)  # Do not execute a native factory.
                    return N(total=1) if label.endswith(".count") else N(value_handle=101)
                def owned_binding(read, value, named_type):
                    self.assertEqual(named_type, "War")
                    return "original-war-binding"
                async def origin(read, value, named_type):
                    return 0
                async def functions(read, named_type):
                    return {"GetNameNoFormatting": object()}
                async def field(read, named_type, method, bindings, kinds, **kwargs):
                    values = {"GetNameNoFormatting": "", "IsParticipant": True,
                        "IsAttacker": False, "IsDefender": None, "IsWarLeader": False}
                    return values[method], "native relation error 1168" if method == "IsDefender" else None
                async def finish(read, report):
                    calls.append("finish")
                    return report
            self.ctx.domain = lambda name: MilitaryAPI(self.ctx)
            with patch.object(_observations, "WarfareRead", CopiedRead), \
                 patch.object(objects, "enumerate_objects", side_effect=AssertionError("no world fallback")):
                snapshot = await self.api.observe(sources=(), state_sources="wars")
        finally:
            self.importer.start()
        source = snapshot["sources"][0]
        receipt = snapshot["receipts"][0]
        self.assertEqual(source["directory"]["source"], "Country.AccessWars")
        self.assertTrue(source["enumeration_complete"])
        self.assertEqual(source["directory"]["source_count"], 1)
        self.assertEqual(receipt["fields"]["selector"], {"type": "war", "id": 0})
        self.assertIs(receipt["fields"]["country_is_leader"], False)
        self.assertIsNone(receipt["fields"]["country_is_defender"])
        self.assertIn("1168", receipt["fields"]["unavailable"]["country_is_defender"])
        self.assertIs(receipt["scope"]["player_participation"], True)
        self.assertIsNone(receipt["active"])
        self.assertEqual(calls.count("Country.AccessWars"), 1)
        self.assertEqual(calls.count("Country.AccessWars.count"), 1)
        self.assertEqual(calls.count("Country.AccessWars.element"), 1)
        self.assertEqual(calls[-1], "finish")

    async def test_country_directory_world_labels_use_actual_source_not_later_context(self):
        row = {"id": 0, "country_is_participant": True}
        directory = war_directory(row)
        async def changed(*, start, limit):
            self.ctx.world = 88
            return directory
        self.ctx.wars.side_effect = changed
        snapshot = await self.api.observe(sources=(), state_sources="wars")
        self.assertTrue(snapshot["world_changed"])
        self.assertEqual(snapshot["world_after"], 88)
        self.assertEqual(snapshot["sources"][0]["world"], 77)
        self.assertEqual(snapshot["receipts"][0]["world"], 77)
        self.assertEqual(snapshot["receipts"][0]["actor_country_id"], 0)
        self.ctx.wars.assert_awaited_once_with(start=0, limit=64)

    async def test_different_source_world_does_not_claim_current_player_membership(self):
        directory = war_directory({"id": 0, "country_is_participant": True})
        directory["world"] = 88
        async def changed(*, start, limit):
            self.ctx.world = 88
            return directory
        self.ctx.wars.side_effect = changed
        snapshot = await self.api.observe(sources=(), state_sources="wars")
        receipt = snapshot["receipts"][0]
        self.assertEqual(receipt["world"], 88)
        self.assertIsNone(receipt["actor_country_id"])
        self.assertIsNone(receipt["scope"]["player_participation"])
        self.assertIs(receipt["scope"]["source_country_participation"], True)
        self.assertTrue(snapshot["world_changed"])

    async def test_cursor_gap_world_reset_and_future_cursor_are_explicit(self):
        self.ctx.count=0
        first=await self.api.observe(sources=())
        for _ in range(129):await self.api.observe(sources=())
        gap=await self.api.observe(sources=(),cursor=first["cursor"])
        self.assertEqual(gap["cursor_status"],"gap")
        future=await self.api.observe(sources=(),cursor={"world":77,"sequence":9999})
        self.assertEqual(future["cursor_status"],"invalid")
        self.ctx.world=88
        reset=await self.api.observe(sources=(),cursor=gap["cursor"])
        self.assertEqual(reset["cursor_status"],"world_changed")
        self.assertEqual(reset["cursor"]["world"],88)
        self.assertFalse(reset["history_complete"])

    async def test_world_change_mid_source_is_explicit_and_skips_later_reads(self):
        async def changed(*args,**kwargs):
            self.ctx.world=88
            return native_result(receipts=[native_receipt()])
        self.provider.observe.side_effect=changed
        snapshot=await self.api.observe(sources="message_feed",state_sources=("wars",))
        self.assertTrue(snapshot["world_changed"])
        self.assertFalse(snapshot["selected_sources_complete"])
        self.assertIsNone(snapshot["cursor"])
        self.ctx.wars.assert_not_awaited()

    async def test_invalid_sources_and_cursor_do_not_read_any_ui_provider(self):
        with self.assertRaises(ValueError):await self.api.observe(sources="made_up")
        with self.assertRaises(ValueError):await self.api.observe(sources=(),cursor={"world":77,"sequence":False})
        self.provider.observe.assert_not_awaited()

    async def test_real_native_ui_provider_composes_with_highlevel_without_native_execution(self):
        self.importer.stop()
        async def query(label,factory):
            # Never call factory: every native submission is replaced by copied
            # CPU evidence. The real source catalog/projection adapter still runs.
            if label=="ui.message_feed.count":
                return N(error=0,source_produced=True,count_valid=True,source_count=1)
            self.assertTrue(label.startswith("ui.message_feed.0."))
            value="Actual source text" if label.endswith(("title","description","message_group_tag")) else False
            result=copied(value);result.source_count=1
            return result
        self.ctx.query=query
        try:
            snapshot=await self.api.observe(sources="message_feed")
        finally:
            self.importer.start()
        self.assertEqual(snapshot["sources"][0]["status"],"available")
        self.assertEqual(snapshot["sources"][0]["count"],1)
        self.assertTrue(snapshot["selected_sources_complete"])
        receipt=snapshot["receipts"][0]
        self.assertEqual(receipt["provenance"],"native_ui_data")
        self.assertEqual(receipt["raw_text"]["title"],"Actual source text")
        self.assertIs(receipt["fields"]["is_expanded"]["value"],False)
        self.assertIsNone(receipt["native_identity"])
        self.assertEqual(receipt["deduplication"]["status"],"unavailable")
        self.assertFalse(snapshot["coverage_complete"])

    async def test_public_pending_ui_observation_keeps_exact_original_query_and_cancelled_waiter(self):
        await self._pending_observation("message_feed")

    async def test_public_pending_country_wars_keeps_exact_original_query_and_cancelled_waiter(self):
        await self._pending_observation("wars")

    async def _pending_observation(self, source):
        ctx=GameContext(N(native=object(),namespace={}),object(),timeout=.001,poll_interval=.001)
        ctx.world=77
        ctx.refresh=AsyncMock(return_value=N(world=77,error=0,transition_pending=False,date_parts=(1853,10,17,0)))
        ctx.country_id=AsyncMock(return_value=0)
        ready=Event();submissions=[]
        loop=asyncio.get_running_loop()
        read_started,waiter_started=asyncio.Event(),asyncio.Event()
        result=war_directory() if source == "wars" else native_result()
        def read():
            if not ready.is_set():
                loop.call_soon_threadsafe(read_started.set)
                return None
            return result
        query=N(read=read)
        async def observe(original,**kwargs):
            self.assertIs(original,ctx)
            def submit():submissions.append(query);return query
            return await ctx.query("ui.cpu_copied",submit)
        if source == "wars":
            async def wars(*, start, limit):
                self.assertEqual((start, limit), (0, 64))
                return await observe(ctx)
            war_provider = AsyncMock(side_effect=wars)
            ctx.domain=lambda name: N(wars=war_provider)
            pending=await NotificationsAPI(ctx).observe(sources=(), state_sources="wars")
        else:
            self.provider.observe.side_effect=observe
            pending=await NotificationsAPI(ctx).observe(sources="message_feed")
        waiter=None
        try:
            self.assertIsInstance(pending,PendingOperation)
            await asyncio.wait_for(read_started.wait(),1)
            self.assertEqual(pending.pending_queries,(query,))
            original_task=pending.task
            async def wait():
                waiter_started.set()
                return await pending.resume(timeout=1)
            waiter=asyncio.create_task(wait())
            await asyncio.wait_for(waiter_started.wait(),1)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):await waiter
            self.assertFalse(original_task.cancelled())
            ready.set();final=await pending.resume(timeout=1)
            self.assertEqual(final["receipts"],[])
            self.assertEqual(ctx.pending_queries,[])
            self.assertEqual(submissions,[query])
            if source == "wars":
                war_provider.assert_awaited_once_with(start=0, limit=64)
                self.assertEqual(final["sources"][0]["directory"], result)
            self.assertIs(pending.task,original_task)
            self.assertIs(await pending.resume(),final)
        finally:
            ready.set()
            if isinstance(pending,PendingOperation):
                await pending.task  # Settle before cleanup removes the provider patch.
            if waiter is not None and not waiter.done():
                await waiter

    async def test_observation_timestamps_do_not_fabricate_content_updates_for_proven_identity(self):
        receipt=native_receipt(0)
        receipt["fields"]["title"].update(read_sequence=1,read_started_monotonic_ns=100)
        self.provider.observe.return_value=native_result(receipts=[receipt])
        first=await self.api.observe(sources="message_feed")
        receipt["source_index"]=4
        receipt["fields"]["title"].update(read_sequence=99,read_started_monotonic_ns=900)
        self.provider.observe.return_value=native_result(receipts=[receipt])
        second=await self.api.observe(sources="message_feed",cursor=first["cursor"])
        self.assertEqual(second["changes"],[])
        self.assertEqual(second["receipts"][0]["native_identity"],0)

    async def test_native_proposal_catalog_channels_are_exposed_without_new_guard_policy(self):
        channels=("diplomatic_proposals","hidden_diplomatic_proposals")
        self.assertTrue(set(channels).issubset(UI_SOURCES))
        receipts=[]
        for index,source in enumerate(channels):
            receipt=native_receipt()
            receipt.update(source=source,kind="diplomatic_proposal")
            receipt["fields"]["hidden"]={"value":bool(index),"raw_bytes":None,"complete":True,"reason":None}
            receipts.append(receipt)
        output=native_result(receipts=receipts,sources=channels)
        for row in output["sources"]:row["family"]="diplomatic_proposals"
        self.provider.observe.return_value=output
        snapshot=await self.api.observe(sources=channels)
        self.assertEqual([row["source"] for row in snapshot["sources"]],list(channels))
        self.assertTrue(all(row["family"]=="diplomatic_proposals" for row in snapshot["sources"]))
        self.assertTrue(all(row["provenance"]=="native_ui_data" for row in snapshot["receipts"]))
        self.assertIs(snapshot["receipts"][0]["fields"]["hidden"]["value"],False)
        self.assertIs(snapshot["receipts"][1]["fields"]["hidden"]["value"],True)
        self.assertTrue(snapshot["selected_sources_complete"])
        self.assertFalse(snapshot["coverage_complete"])
        self.assertNotIn("guard_dependencies",snapshot)
        self.assertTrue(all(row["actions"]==[] for row in snapshot["receipts"]))

    async def test_grouped_child_parent_ordinals_and_paths_are_provenance_not_identity(self):
        source="grouped_important_alert_children"
        self.assertIn(source,UI_SOURCES)
        receipts=[]
        for parent in (0,1):
            receipt=native_receipt()
            receipt.update(source=source,kind="grouped_alert_child",source_index=0,parent_source_index=parent,
                source_path=[{"expression":"TopFrontend.AccessImportantActions","index":parent},
                             {"expression":"ImportantAlert.GetAlerts","index":0}])
            receipts.append(receipt)
        self.provider.observe.return_value=native_result(receipts=receipts,sources=(source,))
        first=await self.api.observe(sources=source)
        second=await self.api.observe(sources=source,cursor=first["cursor"])
        self.assertEqual([r["parent_source_index"] for r in second["receipts"]],[0,1])
        self.assertEqual(second["receipts"][0]["source_path"][0]["index"],0)
        self.assertEqual(second["receipts"][1]["source_path"][0]["index"],1)
        self.assertTrue(all(r["native_identity"] is None and r["local_id"] is None for r in second["receipts"]))
        self.assertTrue(all(r["deduplication"]["status"]=="unavailable" for r in second["receipts"]))
        self.assertEqual([row["lifecycle"] for row in second["changes"]],["observed","observed"])
        self.assertFalse(second["coverage_complete"])
