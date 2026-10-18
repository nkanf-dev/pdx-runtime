"""CPU-only event facade tests. No RuntimeSession or process is opened."""
import asyncio
from threading import Event
from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from pdx_runtime.v3_game.events import EventsAPI, _read as event_read
from pdx_runtime.v3_game._text import plain_text
from pdx_runtime import v3_data


def value(text=None, boolean=None, error=0):
    kind=v3_data.Kind.BOOL if boolean is not None else v3_data.Kind.STRING
    raw=(text or "").encode()
    return N(error=error,value=boolean if boolean is not None else text,
        result=N(snapshot=raw,kind=kind,snapshot_bytes=len(raw),total=len(raw)),
        guard_checked=1,guard_passed=1,condition_checked=1,condition_passed=1)


def record(tag=b"original-tag",label="Choice"):
    return dict(key=value("natural.event"),tag=value(tag.decode()),instance_tag_bytes=tag,
        window_fields=dict(title=value("Actual title"),description=value("Actual body"),flavor=value("Actual flavor")),
        field_diagnostics={},options=[dict(provider_index=0,text=value(label),desc=value("\x15G +25%\x15! trade"),
            enabled=value(boolean=True),default=value(boolean=False),highlighted=value(boolean=True))])


class Context:
    def __init__(self):
        self.world=77;self.interface_thread_id=8;self.image_base=1000
        self.native=object();self.process=object();self.adapters=dict(data="data",call="call",access="access")
        self.diagnostics=[];self.session=N(namespace={});self.records={b"original-tag":record()}
        self.tags=[b"original-tag"];self.selects=[];self.error=0;self.unknown=False
        self.query_labels=[];self.reads=0;self.race_tags=False
    async def refresh(self):return N(world=self.world,error=0)
    async def type_id(self,name):return 918
    async def perform(self,label,body):return await body()
    async def wait(self,q):raise AssertionError("reader should use shared core query factory")
    async def query(self,label,factory):
        self.query_labels.append(label)
        if label.endswith("count"):
            return N(error=0,source_stages=3,root_count=len(self.tags))
        if label in ("events.choose.find","events.choice.observe_instance"):
            if self.race_tags:return value("duplicated")
            q=factory();return value(self.tags[q[1].index].decode())
        if label=="events.choose.select":
            self.selects.append(factory())
            if self.unknown:raise TimeoutError("original unknown query remains in core")
            if not self.error:self.tags.remove(b"original-tag")
            return N(error=self.error,invoked=int(not self.error),accepted=int(not self.error),
                guard_checked=1,guard_passed=1,condition_checked=1,condition_passed=int(not self.error))
        return factory()
    async def read_options(self,data,native,process,adapter,await_result,world,tid,typ,index,count,target):
        self.reads+=1
        target.update(self.records[self.tags[index]])


class Tests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context();self.api=EventsAPI(self.ctx)
        reader_patch=patch.object(event_read,"read_pending_options",side_effect=self.ctx.read_options)
        reader_patch.start();self.addCleanup(reader_patch.stop)
        self.native_get=patch("pdx_runtime.v3_game.events.data.evaluate_pointer_array",side_effect=lambda *a,**k:a)
        self.native_action=patch("pdx_runtime.v3_game.events.data.invoke_element_action",side_effect=lambda *a,**k:(a,k))
        self.native_get.start();self.native_action.start();self.addCleanup(self.native_get.stop);self.addCleanup(self.native_action.stop)
        self.api._world_pending=AsyncMock(side_effect=[{"full_ids":[10]},{"full_ids":[]}]*8)
    async def test_pending_is_readable_without_selection_or_native_ceremony(self):
        pending=await self.api.pending();event=pending[0]
        self.assertEqual(event["title"],"Actual title");self.assertEqual(event["body"],"Actual body")
        self.assertEqual(event["options"][0]["description"],"+25% trade")
        self.assertNotIn("tag",event);self.assertNotIn("world",event)
        self.assertFalse(event["options"][0]["default"]);self.assertEqual(self.ctx.selects,[])
    async def test_choose_one_guarded_original_select_and_repeated_call_no_replay(self):
        event=(await self.api.pending())[0]
        reply=await self.api.choose(event["id"],0)
        self.assertTrue(reply["resolved"]);self.assertEqual(reply["status"],"resolved")
        self.assertEqual(len(self.ctx.selects),1)
        args,kw=self.ctx.selects[0]
        self.assertEqual(args[1:4],("EventWindow.GetOptions",0,"EventOption.Select"))
        self.assertEqual(kw["condition"],"EventOption.IsEnabled")
        self.assertEqual(kw["pointer_array"].guard_input.text,"original-tag")
        self.assertEqual(kw["pointer_array"].type_id,918)
        await self.api.choose(event["id"],0);self.assertEqual(len(self.ctx.selects),1)
    async def test_other_popup_does_not_prevent_target_resolution_and_new_ids_allowed(self):
        self.ctx.tags.append(b"other");self.ctx.records[b"other"]=record(b"other")
        event=(await self.api.pending())[0]
        self.api._world_pending=AsyncMock(side_effect=[{"full_ids":[10,20]},{"full_ids":[20,30,40]}])
        result=await self.api.choose(event["id"],0)
        self.assertTrue(result["resolved"]);self.assertEqual(self.ctx.tags,[b"other"])
        self.assertEqual(len(self.ctx.selects),1)
    async def test_optional_global_membership_failure_does_not_block_visible_choice(self):
        event=(await self.api.pending())[0]
        self.api._world_pending=AsyncMock(side_effect=RuntimeError("unavailable optional membership"))
        result=await self.api.choose(event["id"],0)
        self.assertTrue(result["accepted"]);self.assertTrue(result["resolved"])
        self.assertEqual(len(self.ctx.selects),1)
    async def test_disabled_fresh_option_returns_game_reason_and_no_select(self):
        event=(await self.api.pending())[0]
        self.ctx.records[b"original-tag"]["options"][0]["enabled"]=value(boolean=False)
        result=await self.api.choose(event["id"],0)
        self.assertEqual(result["status"],"unavailable");self.assertIn("unavailable",result["reason"])
        self.assertFalse(self.ctx.selects)
    async def test_unknown_does_not_replay_original_select(self):
        event=(await self.api.pending())[0];self.ctx.unknown=True
        with self.assertRaises(TimeoutError):await self.api.choose(event["id"],0)
        await self.api.choose(event["id"],0)
        self.assertEqual(len(self.ctx.selects),1)
        self.assertIn("attempt",self.api._state["by_id"][event["id"]])
    async def test_reordered_source_does_not_claim_absence(self):
        self.ctx.tags=[b"other",b"third"];self.ctx.race_tags=True
        self.assertIsNone(await self.api._tag_present(918,b"missing"))
    async def test_world_change_expires_old_event_tokens(self):
        event=(await self.api.pending())[0];self.ctx.world=88;self.ctx.tags=[]
        with self.assertRaises(ValueError):await self.api.choose(event["id"],0)
        self.assertFalse(self.ctx.selects)
    async def test_raw_rich_text_preserved_inside_diagnostics(self):
        await self.api.pending()
        self.assertIn("\x15G",self.ctx.diagnostics[0]["options"][0]["desc"].value)
        self.assertNotIn("dw_",plain_text("\x15tooltip:dw_123,AAAA Native label\x15!"))
    async def test_pending_option_label_uses_display_seam_but_key_and_raw_are_exact(self):
        raw = "\x15tooltippable_name \x15tooltip:dw_option,AAAA 选择\x15G 改革\x15!\x15!\x15!"
        saved = record(label=raw)
        saved["key"] = value("#BOLD native.event.key#!")
        self.ctx.records[b"original-tag"] = saved
        event, = await self.api.pending()
        option, = event["options"]
        self.assertEqual(option["label"], "选择改革")
        self.assertEqual(option["text"]["label"]["raw_text"], raw)
        self.assertEqual(option["text"]["label"]["raw_bytes"], raw.encode())
        self.assertEqual(saved["options"][0]["text"].value, raw)
        self.assertEqual(event["key"], "#BOLD native.event.key#!")
        self.assertFalse(option["default"])
        self.assertEqual(self.ctx.selects, [])

    async def test_optional_invalid_native_field_is_none_with_reason(self):
        self.ctx.records[b"original-tag"]["window_fields"]["title"]=value(boolean=True)
        self.ctx.records[b"original-tag"]["field_diagnostics"]["window.title"]={"ok":False,"status":"unexpected_native_kind"}
        event=(await self.api.pending())[0]
        self.assertIsNone(event["title"]);self.assertIn("window.title",event["unavailable"])
        self.assertEqual(event["options"][0]["label"],"Choice")
    async def test_invalid_option_does_not_submit(self):
        event=(await self.api.pending())[0]
        with self.assertRaises(ValueError):await self.api.choose(event["id"],99)
        with self.assertRaises(ValueError):await self.api.choose(event["id"],True)
        self.assertFalse(self.ctx.selects)


class CorePendingTests(IsolatedAsyncioTestCase):
    async def test_real_core_pending_operation_resumes_one_original_select(self):
        from pdx_runtime.v3_game._core import GameContext,PendingOperation
        class Query:
            def __init__(self,result,gate=None,finished=None):
                self.result=result;self.gate=gate;self.finished=finished
                self._result=None;self._released=False;self.completions=0
            def read(self):
                if self._result is not None:return self._result
                if self.gate is not None and not self.gate.is_set():
                    loop.call_soon_threadsafe(started.set)
                    return None
                self._result=self.result;self._released=True;self.completions+=1
                if self.finished:self.finished()
                return self.result
        ctx=GameContext(N(native=object(),namespace={}),process=object(),timeout=.002,poll_interval=.001)
        ctx.world=77;ctx.interface_thread_id=8;ctx.image_base=1000
        ctx.refresh=AsyncMock(return_value=N(error=0,world=77));ctx.type_id=AsyncMock(return_value=918)
        loop=asyncio.get_running_loop()
        started=asyncio.Event()
        tags=[b"original-tag"];saved=record();gate=Event();selected=[]
        async def read(*args,**kwargs):args[-1].update(saved)
        api=EventsAPI(ctx);api._world_pending=AsyncMock(side_effect=[{"full_ids":[10]},{"full_ids":[]}])
        def count(*args,**kwargs):return Query(N(error=0,source_stages=3,root_count=len(tags)))
        def tag(*args,**kwargs):return Query(value(tags[args[1].index].decode()))
        def select(*args,**kwargs):
            q=Query(N(error=0,invoked=1,accepted=1,guard_checked=1,guard_passed=1,
                condition_checked=1,condition_passed=1),gate,lambda:tags.clear())
            selected.append(q);return q
        with patch.object(event_read,"read_pending_options",side_effect=read), \
             patch("pdx_runtime.v3_game.events.data.pointer_array_count",side_effect=count), \
             patch("pdx_runtime.v3_game.events.data.evaluate_pointer_array",side_effect=tag), \
             patch("pdx_runtime.v3_game.events.data.invoke_element_action",side_effect=select):
            events=await api.pending()
            if isinstance(events,PendingOperation):
                events=await events.task  # Complete the original read-only setup.
            operation=None
            try:
                operation=await api.choose(events[0]["id"],0)
                self.assertIsInstance(operation,PendingOperation)
                await asyncio.wait_for(started.wait(),1)
                self.assertEqual(len(selected),1)
                self.assertIs(ctx.pending_queries[0],selected[0])
                gate.set()
                result=await operation.resume(timeout=1)
                self.assertTrue(result["resolved"]);self.assertEqual(selected[0].completions,1)
                again=await api.choose(events[0]["id"],0)
                if isinstance(again,PendingOperation):
                    again=await again.task
                self.assertTrue(again["resolved"]);self.assertEqual(len(selected),1)
                self.assertEqual(ctx.pending_queries,[])
            finally:
                gate.set()
                if isinstance(operation,PendingOperation):
                    await operation.task


class DetailTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx=Context();self.api=EventsAPI(self.ctx)
        reader_patch=patch.object(event_read,"read_pending_options",side_effect=self.ctx.read_options)
        reader_patch.start();self.addCleanup(reader_patch.stop)
        self.ctx.all_types=AsyncMock(return_value={"CString":0,"void":3,"int32":4,"bool":5,"float":64,
            "EventWindow":918,"EventOption":919,"Event":500,"Texture":32})
        async def type_id(name):return {"EventWindow":918,"EventOption":919,"Event":500}[name]
        self.ctx.type_id=type_id
        self.metadata={918:[N(name="GetTitle",return_type_id=0,argument_count=0),
            N(name="GetEvent",return_type_id=999,argument_count=0)],999:[],
            500:[N(name="GetTooltip",return_type_id=0,argument_count=0),
                 N(name="GetTriggerDesc",return_type_id=0,argument_count=0),
                 N(name="GetNumRemainingDays",return_type_id=4,argument_count=0),
                 N(name="HasVideo",return_type_id=5,argument_count=0),
                 N(name="GetTexture",return_type_id=32,argument_count=0),
                 N(name="HasCategory",return_type_id=5,argument_count=1),
                 N(name="OpenInEditor",return_type_id=3,argument_count=0)],
            919:[N(name="GetDesc",return_type_id=0,argument_count=0),
                 N(name="IsEnabled",return_type_id=5,argument_count=0),
                 N(name="Select",return_type_id=3,argument_count=0)]}
        self.expressions=[];self.truncate=False;self.counts={}
        def metadata(native,type_id,**kwargs):
            rows=self.metadata[type_id]
            return N(error=0,rows=rows,next=len(rows),total=len(rows))
        def getter(native,source,expression,**kwargs):
            self.expressions.append(expression)
            if expression.endswith("GetTooltipTag"):
                return value("original-tag")
            self.assertEqual(source.guard_input.text,"original-tag")
            if self.truncate and expression.endswith("GetTooltip"):
                self.counts[expression]=self.counts.get(expression,0)+1
                if self.counts[expression]==1:
                    result=value("cut",error=234);result.result.total=70000;return result
                self.assertEqual(kwargs["output_bytes"],70000)
            if expression.endswith("HasVideo"):return value(boolean=True)
            if expression.endswith("GetNumRemainingDays"):
                result=value("3");result.value=3;return result
            return value("\x15G Original "+expression+"\x15!")
        def element(native,source_expression,index,expression,**kwargs):
            return getter(native,kwargs["pointer_array"],expression,output_bytes=kwargs["output_bytes"])
        self.patches=[patch("pdx_runtime.v3_game.events.data.enumerate_functions",side_effect=metadata),
            patch("pdx_runtime.v3_game.events.data.evaluate_pointer_array",side_effect=getter),
            patch("pdx_runtime.v3_game.events.data.evaluate_element",side_effect=element),
            patch("pdx_runtime.v3_game.events.data.invoke_element_action",side_effect=AssertionError("No action inspection"))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        original=self.ctx.query
        async def query(label,factory):
            if label.startswith("events.details."):return factory()
            return await original(label,factory)
        self.ctx.query=query
    async def test_details_discovers_pure_event_getters_and_keeps_missing_reasons(self):
        event=(await self.api.pending())[0]
        details=await self.api.details(event["id"])
        self.assertTrue(details["available"]);self.assertTrue(details["current"])
        self.assertIn("EventWindow.GetEvent.GetTooltip",self.expressions)
        self.assertIn("EventWindow.GetEvent.GetTriggerDesc",self.expressions)
        self.assertEqual(details["details"]["event"]["num_remaining_days"],3)
        self.assertTrue(details["details"]["event"]["has_video"])
        self.assertIsNone(details["details"]["event"]["texture"])
        self.assertIn("event.texture",details["unavailable"])
        self.assertIn("event.has_category",details["unavailable"])
        self.assertFalse(any("Select" in x or "OpenInEditor" in x for x in self.expressions))
        self.assertEqual(details["details"]["options"][0]["index"],0)
    async def test_requested_unknown_hidden_field_has_local_reason_not_a_global_ban(self):
        event=(await self.api.pending())[0]
        details=await self.api.inspect(event["id"],fields=["tooltip","hidden_script_effects"])
        self.assertIn("tooltip",details["details"]["event"])
        self.assertIn("hidden_script_effects",details["unavailable"])
        self.assertNotIn("trigger_desc",details["details"]["event"])
        self.assertEqual(details["options"][0]["label"],"Choice")
    async def test_full_readonly_tooltip_reallocates_on_real_truncation_size(self):
        self.truncate=True
        event=(await self.api.pending())[0]
        details=await self.api.details(event["id"],fields="tooltip")
        self.assertIn("Original",details["details"]["event"]["tooltip"])
        self.assertEqual(self.counts["EventWindow.GetEvent.GetTooltip"],2)
        self.assertEqual(self.ctx.selects,[])


class TextRenderingRegressionTests(IsolatedAsyncioTestCase):
    async def test_flag_tokens_do_not_consume_following_chinese_without_spaces(self):
        raw=("地主内部与丝绸贸易有利益关系的群体已开始游说政府对"
             "\x15tooltippable_name \x15tooltip:dw_country,AAAA,DATA_COUNTRY_NAME_TOOLTIP,CountryTooltip "
             "\x16JAP!\x16flag_overlay!日本\x15!采取行动，理由是其存在敌对贸易行为。")
        rendered=plain_text(raw)
        self.assertIn("政府对日本采取行动",rendered)
        self.assertTrue(rendered.endswith("理由是其存在敌对贸易行为。"))
        self.assertNotIn("flag_overlay",rendered)
    async def test_lobby_effect_target_and_later_effects_are_preserved(self):
        raw=("\x15indent_newline:3 • 一个新; 反国家游说团成立，目标"
             "\x15tooltip:dw_country,AAAA,CountryTooltip \x16GBR!\x16flag_overlay!大不列颠\x15!。"
             "忽视贸易游说团，持续5年；政治力-10%，支持度-2。")
        rendered=plain_text(raw)
        self.assertIn("目标大不列颠",rendered)
        self.assertTrue(rendered.endswith("支持度-2。"))
    async def test_public_text_exposes_exact_raw_and_native_completeness(self):
        ctx=Context();api=EventsAPI(ctx)
        raw="政府对\x16GBR!\x16flag_overlay!大不列颠采取行动。"
        native=value(raw);saved=record();saved["window_fields"]["description"]=native
        public=api._public({"id":"event-15","record":saved})
        self.assertEqual(public["body"],"政府对大不列颠采取行动。")
        body=public["text"]["body"]
        self.assertEqual(body["raw_text"],raw);self.assertEqual(body["raw_bytes"],raw.encode())
        self.assertTrue(body["complete"]);self.assertIsNone(body["reason"])
        native.result.total+=100
        public=api._public({"id":"event-15","record":saved})
        self.assertFalse(public["text"]["body"]["complete"])
        self.assertIn("only part",public["text"]["body"]["reason"])

    async def test_cached_lobby_events_14_keeps_actual_ig_country_and_sentence(self):
        # Regression text copied from the zero-native cached event-15 receipt.
        raw_body = '\x15tooltippable_name \x15tooltip:dw_599014184,HAMAAA==,EMPTY_STRING,IGTooltip \x16landowners!士大夫\x15!\x15!内部与\x15tooltippable_name \x15tooltip:dw_241073271,MM2Ks/QCAAD//////////xAA3ZmTAQAA,DATA_GOODS_NAME_TOOLTIP,GoodsTooltip \x16silk!\xa0丝绸\x15!\x15!贸易有利益关系的群体已开始游说政府对\x15tooltippable_name \x15tooltip:dw_1696362872,EQAAAA==,DATA_COUNTRY_NAME_TOOLTIP,CountryTooltip \x16JAP17!\x16flag_overlay!德川幕府\x15!\x15!采取行动，理由是其存在敌对贸易行为。'
        raw_option = '\x15indent_newline:0 \x15!\x15indent_newline:3 • 一个新\x15v; 反国家游说团\x15!成立，目标\x15tooltippable_name \x15tooltip:dw_1696362872,EQAAAA==,DATA_COUNTRY_NAME_TOOLTIP,CountryTooltip \x16JAP17!\x16flag_overlay!德川幕府\x15!\x15!\x15!'
        body=plain_text(raw_body);option=plain_text(raw_option)
        self.assertTrue(body.startswith("士大夫内部与"))
        self.assertIn("丝绸贸易有利益关系",body)
        self.assertIn("政府对德川幕府采取行动",body)
        self.assertTrue(body.endswith("理由是其存在敌对贸易行为。"))
        self.assertTrue(option.endswith("目标德川幕府"))
        self.assertEqual(len(raw_body.encode()),473)
        ctx=Context();api=EventsAPI(ctx);saved=record()
        saved["window_fields"]["description"]=value(raw_body)
        public=api._public({"id":"event-15","record":saved})
        self.assertEqual(public["text"]["body"]["raw_text"],raw_body)
        self.assertTrue(public["text"]["body"]["complete"])
        self.assertIsNone(public["text"]["body"]["reason"])


class JournalTests(IsolatedAsyncioTestCase):
    def setUp(self):
        from pdx_runtime.v3_game.events import journal
        self.journal=journal;self.released=[];self.fields=[];self.owned=True;self.empty=False;self.raise_field=False
        self.alias=902;self.unconfirmed_release=False;self.bad_kind=False;self.original_unknown=None
        self.serial=300;self.ctx=N(native=object(),process=object(),world=177,
            adapters={"data":"data"},diagnostics=[],pending_queries=[])
        self.ctx.refresh=AsyncMock(return_value=N(error=0,world=177))
        self.ctx.country_id=AsyncMock(return_value=22)
        async def types(name):return {"Country":901,"JournalEntry":902,"JournalEntryType":903}[name]
        self.ctx.type_id=types
        self.metadata={901:[N(name="AccessVisibleJournalEntries",argument_count=0,return_type_id=70,callbacks=(0,1,0,0))],
            902:[N(name=n,argument_count=0,return_type_id=0,callbacks=(1,0,0,0))
                 for n in list(journal.BASIC.values())+list(journal.DETAILS.values())+["GetType"]],
            903:[N(name="GetKey",argument_count=0,return_type_id=0,callbacks=(1,0,0,0))]}
        def result(kind,handle=0,text="",boolean=None,error=0):
            raw=text.encode();return N(error=error,kind=kind,value_handle=handle,type_id=902,
                value=boolean if boolean is not None else text,snapshot=raw,snapshot_bytes=len(raw),total=len(raw))
        def evaluate(native,expression,bindings,**kw):
            if expression=="Country.AccessVisibleJournalEntries":
                self.assertEqual(bindings[0].type_id,901);self.assertEqual(bindings[0].object,22)
                return result(v3_data.Kind.LIST,100)
            self.fields.append(expression)
            if self.raise_field:
                error=RuntimeError("original unknown query")
                if self.original_unknown is not None:error.query=self.original_unknown
                raise error
            self.assertTrue(bindings[0].retained);self.assertEqual(bindings[0].object,200)
            self.assertEqual(bindings[0].type_id,902)
            self.serial+=1
            if expression.endswith("IsActive"):
                return result(v3_data.Kind.STRING,self.serial,"yes") if self.bad_kind else result(v3_data.Kind.BOOL,self.serial,boolean=False)
            if expression.endswith("IsValid"):return result(v3_data.Kind.BOOL,self.serial,boolean=True)
            if expression.endswith("GetType.GetKey"):return result(v3_data.Kind.STRING,self.serial,"je_lobby_opportunity")
            if expression.endswith("GetName"):
                return result(v3_data.Kind.BOOL,self.serial,boolean=True) if self.bad_kind else result(v3_data.Kind.STRING,self.serial,"大清-俄罗斯同盟提供机会")
            return result(v3_data.Kind.STRING,self.serial,"\x16RUS!\x16flag_overlay!俄罗斯的原生日志条件")
        def functions(native,typ,**kw):
            rows=self.metadata[typ];return N(error=0,rows=rows,next=len(rows),total=len(rows))
        def release(native,handle,**kw):
            self.released.append(handle)
            return result(v3_data.Kind.EMPTY,handle if self.unconfirmed_release else 0)
        def element(native,handle,index,**kw):
            r=result(v3_data.Kind.OBJECT,200) if self.owned else result(v3_data.Kind.RAW,error=50)
            r.type_id=self.alias;return r
        def copied(native,source,index,field,bindings,**kw):
            self.assertEqual(field,"JournalEntry.GetTooltip")
            r=result(v3_data.Kind.STRING,text="一次回调的俄罗斯同盟日志目标")
            return N(error=0,result=r)
        async def query(label,factory):return factory()
        self.ctx.query=query
        self.patches=[patch("pdx_runtime.v3_game.events.journal.data.evaluate",side_effect=evaluate),
            patch("pdx_runtime.v3_game.events.journal.data.enumerate_functions",side_effect=functions),
            patch("pdx_runtime.v3_game.events.journal.data.release",side_effect=release),
            patch("pdx_runtime.v3_game.events.journal.data.list_count",side_effect=lambda *a,**k:N(error=0,total=0 if self.empty else 1)),
            patch("pdx_runtime.v3_game.events.journal.data.list_element",side_effect=element),
            patch("pdx_runtime.v3_game.events.journal.data.evaluate_element",side_effect=copied)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    async def test_owned_entry_reads_same_owner_and_releases_every_value(self):
        view=await self.journal.read(self.ctx,details=True)
        self.assertTrue(view["available"]);entry=view["entries"][0]
        self.assertEqual(entry["name"],"大清-俄罗斯同盟提供机会")
        self.assertFalse(entry["active"])  # Visible is not substituted for active.
        self.assertEqual(entry["definition_key"],"je_lobby_opportunity");self.assertIsNone(entry["id"])
        self.assertIn("completion_conditions",entry);self.assertIn("俄罗斯",entry["goal"])
        self.assertEqual(entry["sampling"],"owned_entry")
        self.assertTrue(entry["text"]["goal"]["complete"])
        self.assertIn(100,self.released);self.assertIn(200,self.released)
        self.assertEqual(len(self.released),len(self.fields)+2)
        self.assertEqual(len(self.released),len(set(self.released)))
        self.assertEqual(self.ctx._journal_reads,{})
    async def test_borrowed_provider_only_returns_single_callback_tooltip(self):
        self.owned=False;view=await self.journal.read(self.ctx,details=True);entry=view["entries"][0]
        self.assertEqual(entry["sampling"],"single_callback_tooltip")
        self.assertIsNone(entry["name"]);self.assertIsNone(entry["goal"])
        self.assertIn("一次回调",entry["tooltip"]);self.assertEqual(self.fields,[])
        self.assertEqual(self.released,[100])
    async def test_missing_source_is_not_an_empty_journal_claim(self):
        self.metadata[901]=[];view=await self.journal.read(self.ctx)
        self.assertFalse(view["available"]);self.assertEqual(view["entries"],[])
        self.assertIn("journal",view["unavailable"]);self.assertEqual(self.released,[])
    async def test_actual_empty_source_has_available_true(self):
        self.empty=True;view=await self.journal.read(self.ctx)
        self.assertTrue(view["available"]);self.assertEqual(view["entries"],[])
        self.assertEqual(self.released,[100])
    async def test_unknown_field_keeps_owners_no_cleanup_behind_pending(self):
        self.raise_field=True;self.ctx.pending_queries=[object()]
        with self.assertRaises(RuntimeError):await self.journal.read(self.ctx)
        self.assertEqual(self.released,[])
        self.assertTrue(self.ctx.diagnostics[0]["source_result"].value_handle)

    async def test_retained_provider_alias_uses_named_receiver_without_retagging_any(self):
        self.alias=777
        view=await self.journal.read(self.ctx)
        self.assertTrue(view["available"])
        raw=self.ctx.diagnostics[0]["entries"][0]
        self.assertEqual(raw["owner_result"].type_id,777)
        self.assertEqual(raw["receiver_type_id"],902)
        self.assertEqual(raw["owner_result"].value_handle,200)
        self.assertEqual(self.ctx._journal_reads,{})
    async def test_unconfirmed_child_release_keeps_parent_graph_outside_diagnostics(self):
        self.unconfirmed_release=True
        view=await self.journal.read(self.ctx)
        self.assertFalse(view["available"])
        self.assertEqual(self.released,[301])
        state=next(iter(self.ctx._journal_reads.values()))
        self.assertFalse(state["owners"][301]["released"])
        self.assertEqual(state["owners"][301]["parent"],200)
        self.assertEqual(state["owners"][200]["parent"],100)
        self.assertFalse(state["owners"][200]["release_attempted"])
        self.assertFalse(state["owners"][100]["release_attempted"])
        self.assertEqual(state["owners"][301]["release_result"].value_handle,301)
        self.ctx.diagnostics.clear()
        self.assertIs(next(iter(self.ctx._journal_reads.values())),state)
        self.assertIsNotNone(state["owners"][301]["release_query"])
    async def test_unknown_original_query_is_strongly_retained_with_dependencies(self):
        self.raise_field=True;self.original_unknown=object()
        with self.assertRaises(RuntimeError):await self.journal.read(self.ctx)
        state=next(iter(self.ctx._journal_reads.values()))
        self.assertIs(state["pending"]["query"],self.original_unknown)
        self.assertEqual(state["pending"]["parents"],(200,))
        self.assertEqual(self.released,[])
        self.ctx.diagnostics.clear()
        self.assertIs(state["pending"]["query"],self.original_unknown)
        self.assertFalse(state["owners"][200]["released"])
        self.assertFalse(state["owners"][100]["released"])
    async def test_pure_requires_real_read_callback_and_no_actions(self):
        self.assertFalse(self.journal._pure(N(argument_count=0,callbacks=(0,0,0,0))))
        self.assertFalse(self.journal._pure(N(argument_count=0,callbacks=(1,0,1,0))))
        self.assertFalse(self.journal._pure(N(argument_count=1,callbacks=(1,0,0,0))))
        self.assertTrue(self.journal._pure(N(argument_count=0,callbacks=(0,1,0,0))))
    async def test_boolean_and_cstring_wrong_kinds_have_field_reasons(self):
        self.bad_kind=True
        view=await self.journal.read(self.ctx)
        entry=view["entries"][0]
        self.assertIsNone(entry["active"]);self.assertIsNone(entry["name"])
        self.assertIn("boolean",entry["unavailable"]["active"])
        self.assertIn("CString",entry["unavailable"]["name"])
        self.assertTrue(entry["valid"])
        self.assertEqual(self.ctx._journal_reads,{})
