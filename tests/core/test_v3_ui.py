"""CPU-only provider contract tests; no session/process/native call is opened."""
from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pdx_runtime import v3_data as data, v3_ui as ui


def copied(value="native title", *, error=0, count=1, produced=1, count_valid=1,
           raw_bytes=None, total=None, pointer=0):
    if type(value) is bool:
        kind = data.Kind.BOOL
    elif type(value) is int:
        kind = data.Kind.U64
    elif value is None:
        kind = data.Kind.EMPTY
    else:
        kind = data.Kind.STRING
    raw = (str(value).encode() if kind == data.Kind.STRING else b"") if raw_bytes is None else raw_bytes
    result = N(kind=kind, snapshot=raw, snapshot_bytes=len(raw),
        total=len(raw) if total is None else total, value_handle=0, object=pointer)
    return N(error=error, value=value, result=result, source_count=count,
        source_produced=produced, count_valid=count_valid)


def dialog_snapshot(source, *, count=1, produced=1, count_valid=1, error=0, world=88, index=0):
    names = {
        "jomini_notifications": (("title", ""), ("text", "原始通知"), ("accept_label", "OK"),
            ("decline_label", ""), ("has_accept", False), ("has_decline", True)),
        "general_information_dialogs": (("title", "title"), ("text", "body")),
        "confirmations": (("description", "确认原文"),)}
    fields=[]
    for name,value in names[source]:
        raw=value.encode("utf-8") if isinstance(value,str) else b""
        fields.append(N(name=name,value=value,kind=data.Kind.STRING if isinstance(value,str) else data.Kind.BOOL,
            raw_bytes=raw,raw_text=value if isinstance(value,str) else None,error=0,complete=True,
            expression="original."+name,text_bytes_copied=len(raw),text_bytes_required=len(raw)))
    records=(N(source_index=index,error=0,metadata_valid=3,consumed=0,has_dialog=0,has_widget=0,
        fields=tuple(fields)),) if produced and count_valid and count else ()
    return N(source_produced=produced,count_valid=count_valid,error=error,source_world=world,
        source_count=count,returned_count=len(records),records=records,truncated=0,
        text_bytes_copied=sum(field.text_bytes_copied for field in fields),
        text_bytes_required=sum(field.text_bytes_required for field in fields))


class Context:
    def __init__(self):
        self.world = 88
        self._ui_type_cache = {"world":88, "types":{"PlayerMessageItem":991}}
        self.image_base = 0x100000
        self.interface_thread_id = 77
        self.native = object(); self.process = object()
        self.adapters = {"data": "original-data"}
        self.pending_queries = []
        self.labels = []
        self.failure = None

    async def type_id(self, name):
        assert name == "PlayerMessageItem"
        return 991

    async def query(self, label, factory):
        self.labels.append(label)
        if self.failure:
            raise self.failure
        return factory()


class UIProviderTests(IsolatedAsyncioTestCase):
    async def test_actual_roots_and_zero_false_preserved_without_actions_or_identity(self):
        ctx = Context()
        def element(client, root, index, expression, **kwargs):
            self.assertEqual(root, "MessageFeedHandler.GetItems")
            self.assertEqual(kwargs["world"], 88)
            self.assertEqual(kwargs["expected_count"], 1)
            if expression.endswith("IsSetToDelete"):
                return copied(False)
            return copied("原生\x15B text\x15!")
        with patch.object(data, "source_count", return_value=copied(count=1)), \
             patch.object(data, "evaluate_element", side_effect=element), \
             patch.object(data, "invoke_action") as action:
            result = await ui.observe(ctx, sources="message_feed")
        source = result["sources"][0]; row = result["receipts"][0]
        self.assertEqual(source["status"], "available")
        self.assertIs(row["fields"]["is_set_to_delete"]["value"], False)
        self.assertEqual(row["raw_text"]["title"], "原生\x15B text\x15!")
        self.assertIsNone(row["native_identity"])
        self.assertFalse(row["observations_are_atomic"])
        self.assertIsNone(row["visibility"]["rendered"])
        self.assertNotIn("local_id", row)
        action.assert_not_called()

    async def test_proven_empty_list_is_available_not_missing(self):
        ctx = Context()
        with patch.object(data, "source_count", return_value=copied(count=0)), \
             patch.object(data, "evaluate_element") as element:
            result = await ui.observe(ctx, sources="alerts")
        self.assertEqual(result["sources"][0]["count"], 0)
        self.assertEqual(result["sources"][0]["status"], "available")
        self.assertEqual(result["receipts"], [])
        element.assert_not_called()

    async def test_older_dll_unavailable_never_falls_back_to_borrowed_evaluate(self):
        ctx = Context()
        with patch.object(data, "source_count", return_value=copied(error=87, count=0, produced=0, count_valid=0)), \
             patch.object(data, "evaluate") as retained:
            result = await ui.observe(ctx, sources="message_feed")
        self.assertEqual(result["sources"][0]["status"], "unavailable")
        self.assertIsNone(result["sources"][0]["count"])
        retained.assert_not_called()

    async def test_toast_absent_uses_copied_root_false_and_no_item_getters(self):
        ctx = Context()
        with patch.object(data, "evaluate_copied", return_value=copied(False)) as root:
            result = await ui.observe(ctx, sources="toast")
        self.assertEqual(root.call_count, 1)
        self.assertEqual(root.call_args.args[1], "ToastMessageHandler.HasMessage")
        self.assertEqual(result["sources"][0]["status"], "available")
        self.assertEqual(result["sources"][0]["count"], 0)
        self.assertEqual(result["receipts"], [])

    async def test_toast_title_body_copied_without_consuming_ui(self):
        ctx = Context()
        def root(client, expression, **kwargs):
            return copied(True if expression.endswith("HasMessage") else "native toast")
        with patch.object(data, "evaluate_copied", side_effect=root), \
             patch.object(data, "release") as release, patch.object(data, "invoke_action") as action:
            result = await ui.observe(ctx, sources="toast")
        self.assertEqual(result["sources"][0]["status"], "available", result)
        self.assertEqual(result["receipts"][0]["raw_text"]["description"], "native toast")
        release.assert_not_called(); action.assert_not_called()

    async def test_partial_text_and_invalid_utf8_keep_bytes_and_reason(self):
        ctx = Context()
        with patch.object(data, "source_count", return_value=copied(count=1)), \
             patch.object(data, "evaluate_element", return_value=copied("prefix", error=234, raw_bytes=b"a\xff", total=9)):
            result = await ui.observe(ctx, sources="alerts")
        self.assertEqual(result["sources"][0]["status"], "partial")
        field = result["receipts"][0]["fields"]["title"]
        self.assertIsNone(field["value"]); self.assertFalse(field["complete"])
        self.assertEqual(field["raw_bytes"], b"a\xff")
        self.assertIsNotNone(field["reason"])

    async def test_count_race_is_partial_no_retry_or_fake_identity(self):
        ctx = Context()
        with patch.object(data, "source_count", return_value=copied(count=2)), \
             patch.object(data, "evaluate_element", return_value=copied(count=3)) as element:
            result = await ui.observe(ctx, sources="message_feed")
        self.assertEqual(element.call_count, 1)
        self.assertEqual(result["sources"][0]["status"], "partial")
        self.assertIsNone(result["receipts"][0]["fields"]["title"]["value"])

    async def test_item_bound_and_gaps_never_claim_full_ui_coverage(self):
        ctx = Context()
        with patch.object(data, "source_count", return_value=copied(count=2)), \
             patch.object(data, "evaluate_element", return_value=copied(count=2)), \
             patch.object(ui, "_popup_profile", return_value={"supported":False, "reason":"unmatched executable"}):
            result = await ui.observe(ctx, sources=("message_feed", "message_popups", "map_notifications"), max_items=1)
        self.assertEqual([s["status"] for s in result["sources"]], ["partial", "unavailable", "unavailable"])
        self.assertFalse(result["history_available"])
        self.assertIsNone(result["native_cursor"])
        self.assertIn("Transient", result["sources"][0]["coverage"][1])

    async def test_unknown_query_owner_is_retained_and_error_not_swallowed(self):
        ctx = Context(); ctx.pending_queries.append(object()); ctx.failure = OSError("unresolved transport")
        with self.assertRaises(OSError):
            await ui.observe(ctx, sources="message_feed")
        self.assertEqual(len(ctx.labels), 1)
        ctx.pending_queries.clear()
        result = await ui.observe(ctx, sources="message_feed")
        self.assertEqual(result["sources"][0]["status"], "failed")

    async def test_invalid_input_has_no_submission_and_unknown_source_explicit(self):
        ctx = Context()
        for kwargs in ({"max_items":0}, {"max_items":True}, {"output_bytes":-1}):
            with self.assertRaises(ValueError):
                await ui.observe(ctx, **kwargs)
        self.assertEqual(ctx.labels, [])
        result = await ui.observe(ctx, sources="unknown")
        self.assertEqual(result["sources"][0]["status"], "unavailable")
        ctx.world = 0
        with self.assertRaises(ValueError):
            await ui.observe(ctx)

    async def test_popup_uses_actual_embedded_original_item_and_dynamic_type(self):
        ctx = Context()
        def element(client, source, expression, **kwargs):
            self.assertEqual(source.root_expression, "PopupManager.Self")
            self.assertEqual((source.byte_offset, source.element_subobject_offset), (0x40, 0x88))
            self.assertEqual(source.type_id, 991)
            self.assertEqual(source.expected_element_vtable, ctx.image_base + 0x45c54e8)
            self.assertEqual(source.interface_thread_id, 77)
            result = copied("native popup")
            result.root_count = 1
            return result
        with patch.object(ui, "_popup_profile", return_value={"supported":True}), \
             patch.object(data, "pointer_array_count", return_value=N(error=0, source_stages=3, root_count=1)), \
             patch.object(data, "evaluate_pointer_array", side_effect=element), \
             patch.object(data, "invoke_action") as action:
            result = await ui.observe(ctx, sources="message_popups")
        self.assertEqual(result["sources"][0]["status"], "available", result)
        self.assertEqual(result["receipts"][0]["raw_text"]["description"], "native popup")
        self.assertIsNone(result["receipts"][0]["native_identity"])
        action.assert_not_called()

    async def test_popup_profile_gate_precedes_ui_layout_or_native_query(self):
        ctx = Context()
        with patch.object(ui, "_popup_profile", return_value={"supported":False, "reason":"unsupported build"}), \
             patch.object(data, "pointer_array_count") as count:
            result = await ui.observe(ctx, sources="message_popups")
        self.assertEqual(result["sources"][0]["status"], "unavailable")
        count.assert_not_called()
        self.assertEqual(ctx.labels, [])

    async def test_popup_old_dll_subobject_flag_failure_never_bypassed(self):
        ctx = Context()
        failed = copied(None, error=87)
        failed.root_count = 0
        with patch.object(ui, "_popup_profile", return_value={"supported":True}), \
             patch.object(data, "pointer_array_count", return_value=N(error=0, source_stages=3, root_count=1)), \
             patch.object(data, "evaluate_pointer_array", return_value=failed) as field, \
             patch.object(data, "evaluate") as unsafe:
            result = await ui.observe(ctx, sources="message_popups")
        self.assertEqual(result["sources"][0]["status"], "partial")
        self.assertEqual(field.call_count, 1)
        unsafe.assert_not_called()


    async def test_outliner_army_navy_pact_roots_and_native_flags(self):
        ctx = Context()
        roots = []
        def element(client, root, index, expression, **kwargs):
            roots.append(root)
            return copied(False if expression.rsplit(".",1)[-1].startswith(("Is", "Has")) else "UI attention")
        with patch.object(data, "source_count", return_value=copied(count=1)), \
             patch.object(data, "evaluate_element", side_effect=element):
            result = await ui.observe(ctx, sources=("outliner_armies", "outliner_navies", "outliner_diplomatic_pacts"))
        self.assertEqual({s["status"] for s in result["sources"]}, {"available"})
        self.assertEqual(set(roots), {"Outliner.AccessCategory('army').GetSubEntries",
            "Outliner.AccessCategory('navy').GetSubEntries", "Outliner.AccessCategory('diplo_pacts').GetSubEntries"})
        self.assertIs(result["receipts"][0]["fields"]["is_mobilizing"]["value"], False)
        self.assertTrue(all(row["provenance"] == "native_ui_data" for row in result["receipts"]))
        self.assertTrue(all(row["native_identity"] is None for row in result["receipts"]))

    async def test_query_budget_prioritizes_popup_toast_and_keeps_partial_fields(self):
        ctx = Context()
        with patch.object(data, "evaluate_copied", side_effect=lambda client,expr,**kw:copied(True if expr.endswith("HasMessage") else "urgent")), \
             patch.object(data, "source_count") as feed:
            result = await ui.observe(ctx, sources=("message_feed", "toast"), max_queries=2)
        self.assertEqual(ctx.labels, ["ui.toast.presence", "ui.toast.0.title"])
        self.assertEqual(result["query_count"], 2)
        self.assertEqual(result["sources"][0]["status"], "partial")
        self.assertEqual(result["sources"][0]["sampling_status"], "not_sampled")
        self.assertIsNone(result["sources"][0]["count"])
        self.assertEqual(result["receipts"][0]["raw_text"]["title"], "urgent")
        feed.assert_not_called()

    async def test_async_stop_after_completed_query_preserves_owner_and_partial_row(self):
        ctx = Context()
        async def stop():
            return "player interrupted" if len(ctx.labels) >= 2 else None
        with patch.object(data, "source_count", return_value=copied(count=1)), \
             patch.object(data, "evaluate_element", return_value=copied("copied first field")):
            result = await ui.observe(ctx, sources="message_feed", should_stop=stop)
        self.assertEqual(ctx.labels, ["ui.message_feed.count", "ui.message_feed.0.title"])
        self.assertEqual(result["sources"][0]["status"], "partial")
        self.assertEqual(result["sources"][0]["reason"], "player interrupted")
        self.assertEqual(result["receipts"][0]["raw_text"]["title"], "copied first field")
        self.assertTrue(result["sampling_interrupted"])

    async def test_initial_interrupt_samples_nothing_and_never_proves_absence(self):
        ctx = Context()
        result = await ui.observe(ctx, sources=("message_popups", "toast"), should_stop=lambda:True)
        self.assertEqual(ctx.labels, [])
        self.assertEqual(result["query_count"], 0)
        self.assertTrue(all(s["status"] == "partial" and s["count"] is None for s in result["sources"]))
        self.assertEqual(result["receipts"], [])

    async def test_guarded_context_stop_is_partial_not_failed_and_never_replayed(self):
        ctx = Context()
        class Stopped(Exception):
            sampling_stopped = True
        original = ctx.query
        async def guarded(label, factory):
            if label.endswith("description"):
                raise Stopped("target date reached")
            return await original(label, factory)
        ctx.query = guarded
        with patch.object(data, "source_count", return_value=copied(count=1)), \
             patch.object(data, "evaluate_element", return_value=copied("settled title")):
            result = await ui.observe(ctx, sources="message_feed")
        self.assertEqual(result["sources"][0]["status"], "partial")
        self.assertEqual(result["query_count"], 3)  # Conservatively reserved before delegated gate stopped.
        self.assertIn("reserved", result["query_count_kind"])
        self.assertEqual(result["receipts"][0]["raw_text"]["title"], "settled title")
        self.assertEqual(result["sources"][0]["reason"], "target date reached")

    async def test_cold_metadata_is_bounded_and_cooperative_too(self):
        ctx = Context(); ctx._ui_type_cache = {}
        page = N(error=0, rows=[N(name="PlayerMessageItem",type_id=991)], next=10,total=10)
        with patch.object(ui, "_popup_profile", return_value={"supported":True}), \
             patch.object(data, "enumerate_types", return_value=page), \
             patch.object(data, "pointer_array_count") as count:
            result = await ui.observe(ctx, sources="message_popups", max_queries=1)
        self.assertEqual(ctx.labels, ["ui.metadata.types"])
        self.assertEqual(result["sources"][0]["status"], "partial")
        count.assert_not_called()

    async def test_native_map_bounded_snapshot_preserves_text_and_limits(self):
        ctx = Context()
        row = N(source_index=0,error=0,raw_bytes="原生地图".encode(),raw_text="原生地图",
                complete=1,text_bytes_copied=len("原生地图".encode()),text_bytes_required=len("原生地图".encode()))
        sample = N(source_produced=1,collection_valid=1,error=0,source_count=1,returned_count=1,
            text_capacity=100,text_bytes_copied=row.text_bytes_copied,text_bytes_required=row.text_bytes_required,
            max_items=64,start_index=0,records=(row,),truncated=0)
        with patch.object(ui,"_popup_profile",return_value={"supported":True}), \
             patch.object(ui.map_data,"snapshot",return_value=sample):
            result = await ui.observe(ctx,sources="map_notifications")
        self.assertEqual(result["sources"][0]["status"],"available")
        self.assertEqual(result["receipts"][0]["raw_text"]["text"],"原生地图")
        self.assertIsNone(result["receipts"][0]["native_identity"])
        self.assertTrue(result["sources"][0]["single_callback"])

    async def test_high_volume_source_cannot_starve_later_sources_across_rounds(self):
        ctx=Context(); visited=[]
        def counted(client,root,**kwargs):
            visited.append(root); return copied(count=100)
        def root(client,expr,**kwargs):
            return copied(False if expr.endswith("HasMessage") else "toast")
        with patch.object(data,"source_count",side_effect=counted), \
             patch.object(data,"evaluate_element",return_value=copied("field",count=100)), \
             patch.object(data,"evaluate_copied",side_effect=root):
            results=[await ui.observe(ctx,sources=("message_feed","alerts","toast"),max_queries=12) for _ in range(6)]
        self.assertIn("TopFrontend.AccessAlerts",visited)
        self.assertIn("MessageFeedHandler.GetItems",visited)
        self.assertTrue(any("ui.toast.presence" in label for label in ctx.labels))
        self.assertTrue(any(r["sampling_cursor"]["positions"].get("message_feed",0)>0 for r in results))
        self.assertTrue(all(r["query_count"]<=12 for r in results))

    async def test_cold_metadata_pages_resume_without_native_descriptor_or_restart(self):
        ctx=Context();ctx._ui_type_cache={};pages=[]
        def metadata(client,start,capacity,**kwargs):
            pages.append(start)
            return N(error=0,rows=[] if start<256 else [N(name="PlayerMessageItem",type_id=991)],
                     next=start+128,total=384)
        with patch.object(ui,"_popup_profile",return_value={"supported":True}), \
             patch.object(data,"enumerate_types",side_effect=metadata), \
             patch.object(data,"pointer_array_count",return_value=N(error=0,source_stages=3,root_count=0)), \
             patch.object(data,"evaluate_copied",return_value=copied(False)):
            results=[await ui.observe(ctx,sources=("message_popups","toast"),max_queries=2) for _ in range(4)]
        self.assertEqual(pages,[0,128,256])
        self.assertEqual(ctx._ui_type_cache["types"]["PlayerMessageItem"],991)
        self.assertTrue(any(s["source"]=="message_popups" and s["status"]=="available" for r in results for s in r["sources"]))
        self.assertTrue(all(r["query_count"]<=2 for r in results))

    async def test_two_query_quota_progresses_every_field_without_old_merges(self):
        ctx=Context(); expressions=[]
        def element(client,root,index,expr,**kwargs):
            expressions.append(expr); return copied(expr,count=1)
        with patch.object(data,"source_count",return_value=copied(count=1)), \
             patch.object(data,"evaluate_element",side_effect=element):
            results=[await ui.observe(ctx,sources="message_feed",max_queries=2) for _ in range(6)]
        self.assertEqual(len(set(expressions)),6)
        self.assertEqual(len(expressions),6)
        self.assertTrue(all(len(r["receipts"])==1 and len(r["receipts"][0]["fields"])==1 for r in results))
        self.assertTrue(all(r["sources"][0]["status"]=="partial" for r in results))
        self.assertEqual(results[-1]["sampling_cursor"]["positions"]["message_feed"],0)

    async def test_sync_signal_is_rechecked_after_dispatch_wait_before_factory(self):
        ctx=Context(); interrupted=False
        async def delayed_dispatch(label,factory):
            nonlocal interrupted
            interrupted=True
            return factory()
        ctx.query=delayed_dispatch
        with patch.object(data,"source_count") as native:
            result=await ui.observe(ctx,sources="message_feed",should_stop=lambda:interrupted)
        native.assert_not_called()
        self.assertEqual(result["sources"][0]["status"],"partial")
        self.assertEqual(result["receipts"],[])

    async def test_ordinary_callable_returning_awaitable_is_awaited_once(self):
        ctx=Context();calls=[]
        async def async_stop():
            calls.append("awaited")
            return "async policy stop"
        def make_awaitable():
            calls.append("created")
            return async_stop()
        result=await ui.observe(ctx,sources="message_feed",should_stop=make_awaitable)
        self.assertEqual(calls,["created","awaited"])
        self.assertEqual(ctx.labels,[])
        self.assertEqual(result["sources"][0]["reason"],"async policy stop")
        self.assertEqual(result["sources"][0]["status"],"partial")

    async def test_discovered_awaitable_is_not_lost_when_budget_just_exhausted(self):
        ctx=Context();created=[];awaited=[]
        async def stop_async():
            awaited.append(1)
            return "post-settlement async stop"
        def stop():
            if ctx.labels:
                created.append(1)
                return stop_async()
            return False
        with patch.object(data,"source_count",return_value=copied(count=1)):
            result=await ui.observe(ctx,sources="message_feed",max_queries=1,should_stop=stop)
        self.assertEqual(len(created),1)
        self.assertEqual(len(awaited),1)
        self.assertEqual(result["sources"][0]["status"],"partial")
        self.assertEqual(result["sources"][0]["reason"],"post-settlement async stop")

    async def test_declared_priority_is_pinned_without_implicit_source_dependency(self):
        ctx=Context();ctx._ui_sampling_progress=ui.RoundRobinProgress(88)
        ctx._ui_last_sample={}
        ctx._ui_sampling_progress.next_source="message_feed"
        with patch.object(data,"source_count",return_value=copied(count=100)), \
             patch.object(data,"evaluate_element",return_value=copied("field",count=100)), \
             patch.object(data,"evaluate_copied",return_value=copied(False)):
            result=await ui.observe(ctx,sources=("message_feed","toast"),max_queries=2,priority_sources=("toast",))
        self.assertEqual(ctx.labels[0],"ui.toast.presence")
        self.assertEqual([s["source"] for s in result["sources"]],["message_feed","toast"])
        self.assertEqual(result["sources"][0]["status"],"partial")
        with self.assertRaises(ValueError):
            await ui.observe(ctx,sources="message_feed",priority_sources="toast")

    async def test_diplomatic_proposal_original_visible_and_hidden_channels(self):
        ctx=Context();roots=[];gets=[]
        def count(client,expression,**kwargs):
            roots.append(expression)
            return copied(count=1)
        def element(client,root,index,expression,**kwargs):
            gets.append((root,index,expression))
            self.assertEqual(kwargs["expected_count"],1)
            return copied("原文 tooltip")
        with patch.object(data,"source_count",side_effect=count), \
             patch.object(data,"evaluate_element",side_effect=element):
            result=await ui.observe(ctx,sources=("diplomatic_proposals","hidden_diplomatic_proposals"))
        self.assertEqual(roots,["TopFrontend.AccessProposals","TopFrontend.AccessHiddenProposals"])
        self.assertEqual(len(gets),8)
        self.assertTrue(all("GetDraftTreaties" not in expr and "Execute(" not in expr for _,_,expr in gets))
        self.assertEqual([s["status"] for s in result["sources"]],["available","available"])
        self.assertEqual([r["kind"] for r in result["receipts"]],["diplomatic_proposal"]*2)
        self.assertEqual(result["receipts"][0]["fields"]["tooltip"]["value"],"原文 tooltip")
        self.assertIsNone(result["receipts"][0]["native_identity"])
        self.assertEqual(result["receipts"][1]["actions"][0]["name"],"open")

    async def test_grouped_alert_children_use_fresh_original_nested_scopes(self):
        ctx=Context();leaves=[]
        def leaf(client,steps,expression,**kwargs):
            leaves.append((steps,expression))
            result=copied(False if expression.endswith("IsRead") else "child text")
            result.steps=(N(source_produced=1,count_valid=1,source_count=1),)*2
            return result
        with patch.object(data,"evaluate_scoped",side_effect=leaf), \
             patch.object(data,"source_count",side_effect=AssertionError("no separate root reentry")), \
             patch.object(data,"scoped_source_count",side_effect=AssertionError("leaf includes both fresh counts")):
            result=await ui.observe(ctx,sources="grouped_important_alert_children")
        self.assertEqual(leaves[0][0],(data.CreationStep("TopFrontend.AccessImportantActions",0),
                                      data.CreationStep("ImportantAlert.GetAlerts",0)))
        self.assertEqual(leaves[1][0],(data.CreationStep("TopFrontend.AccessImportantActions",0,1),
                                      data.CreationStep("ImportantAlert.GetAlerts",0,1)))
        self.assertEqual(result["sources"][0]["status"],"available")
        self.assertEqual(result["sources"][0]["count"],1)
        self.assertEqual(result["query_count"],6)
        self.assertFalse(result["receipts"][0]["fields"]["is_read"]["value"])
        self.assertEqual(result["receipts"][0]["parent_source_index"],0)
        self.assertIsNone(result["receipts"][0]["native_identity"])

    async def test_grouped_alert_empty_parent_is_proven_without_leaf_or_false_error(self):
        ctx=Context();empty=copied(error=1168,produced=0,count_valid=0,count=0)
        empty.steps=(N(source_produced=1,count_valid=1,source_count=0),N(source_produced=0,count_valid=0,source_count=0))
        with patch.object(data,"evaluate_scoped",return_value=empty):
            result=await ui.observe(ctx,sources="grouped_important_alert_children")
        self.assertEqual(result["sources"][0]["status"],"available")
        self.assertEqual(result["sources"][0]["count"],0)
        self.assertEqual(result["receipts"],[])

    async def test_grouped_alert_one_query_quota_advances_fields_without_old_merges(self):
        ctx=Context();seen=[]
        def leaf(client,steps,expression,**kwargs):
            seen.append(expression)
            result=copied("fresh child")
            result.steps=(N(source_produced=1,count_valid=1,source_count=1),)*2
            return result
        with patch.object(data,"evaluate_scoped",side_effect=leaf):
            results=[await ui.observe(ctx,sources="grouped_important_alert_children",max_queries=1) for _ in range(6)]
        expected=[expression for _,expression in ui._BY_NAME["grouped_important_alert_children"].fields]
        self.assertEqual(seen,expected)
        self.assertTrue(all(len(result["receipts"][0]["fields"])==1 for result in results))
        self.assertTrue(all(result["sources"][0]["status"]=="partial" for result in results))
        self.assertEqual(results[-1]["sampling_cursor"]["positions"]["grouped_important_alert_children"],(0,0))

    async def test_grouped_alert_count_race_keeps_only_earlier_fresh_partial_fields(self):
        ctx=Context();first=copied("fresh title")
        first.steps=(N(source_produced=1,count_valid=1,source_count=1),)*2
        raced=copied("wrong ordinal",error=1237)
        raced.steps=(N(source_produced=1,count_valid=1,source_count=2),N(source_produced=0,count_valid=0,source_count=0))
        with patch.object(data,"evaluate_scoped",side_effect=[first,raced]):
            result=await ui.observe(ctx,sources="grouped_important_alert_children")
        self.assertEqual(result["sources"][0]["status"],"partial")
        self.assertEqual(set(result["receipts"][0]["fields"]),{"title"})
        self.assertEqual(result["receipts"][0]["fields"]["title"]["value"],"fresh title")
        self.assertEqual(result["query_count"],2)

    async def test_grouped_alert_world_change_never_proves_outer_or_inner_empty(self):
        for empty_parent in (False,True):
            ctx=Context()
            def leaf(*args,**kwargs):
                ctx.world=99
                result=copied(error=1168,produced=0,count_valid=0,count=0)
                result.steps=(N(source_produced=1,count_valid=1,source_count=0 if empty_parent else 1),
                              N(source_produced=0 if empty_parent else 1,count_valid=0 if empty_parent else 1,source_count=0))
                return result
            with patch.object(data,"evaluate_scoped",side_effect=leaf):
                result=await ui.observe(ctx,sources="grouped_important_alert_children")
            self.assertEqual(result["sources"][0]["status"],"partial")
            self.assertIsNone(result["sources"][0]["count"])
            self.assertIn("world changed",result["sources"][0]["reason"])
            self.assertEqual(result["receipts"],[])

    async def test_grouped_alert_all_zero_children_use_fresh_counts_without_getter_success(self):
        ctx=Context();paths=[]
        def leaf(client,steps,expression,**kwargs):
            paths.append(steps)
            result=copied(error=1168,produced=0,count_valid=0,count=0)
            result.steps=(N(source_produced=1,count_valid=1,source_count=2),N(source_produced=1,count_valid=1,source_count=0))
            return result
        with patch.object(data,"evaluate_scoped",side_effect=leaf):
            result=await ui.observe(ctx,sources="grouped_important_alert_children")
        self.assertEqual([path[0].index for path in paths],[0,1])
        self.assertEqual(result["sources"][0]["status"],"available")
        self.assertEqual(result["sources"][0]["count"],0)
        self.assertEqual(result["receipts"],[])

    async def test_native_dialog_sources_preserve_false_empty_text_and_metadata_unknown(self):
        names=("jomini_notifications","general_information_dialogs","confirmations")
        ctx=Context()
        with patch("pdx_runtime.v3_ui_dialogs.snapshot",side_effect=lambda client,**kw:dialog_snapshot(kw["source"])) as snapshot:
            result=await ui.observe(ctx,sources=names,max_items=2)
        self.assertEqual(snapshot.call_count,3)
        self.assertEqual([source["status"] for source in result["sources"]],["available"]*3)
        self.assertEqual(result["receipts"][0]["fields"]["title"]["value"],"")
        self.assertFalse(result["receipts"][0]["fields"]["has_accept"]["value"])
        self.assertIsNone(result["receipts"][0]["source_metadata"]["has_widget"])
        self.assertFalse(result["receipts"][0]["source_metadata"]["consumed"])
        self.assertIsNone(result["receipts"][0]["visibility"]["rendered"])
        self.assertIsNone(result["receipts"][0]["native_identity"])
        self.assertEqual(snapshot.call_args.kwargs["process"],ctx.process)
        self.assertEqual(snapshot.call_args.kwargs["interface_thread_id"],77)
        self.assertEqual(result["receipts"][2]["fields"]["description"]["value"],"确认原文")

    async def test_native_dialog_missing_production_never_becomes_empty(self):
        ctx=Context();unknown=dialog_snapshot("confirmations",produced=0,count_valid=0,error=50,count=0)
        with patch("pdx_runtime.v3_ui_dialogs.snapshot",return_value=unknown):
            result=await ui.observe(ctx,sources="confirmations")
        self.assertEqual(result["sources"][0]["status"],"unavailable")
        self.assertIsNone(result["sources"][0]["count"])
        self.assertEqual(result["receipts"],[])

    async def test_native_dialog_world_mismatch_rejects_even_proven_empty(self):
        ctx=Context();wrong=dialog_snapshot("confirmations",world=99,count=0)
        with patch("pdx_runtime.v3_ui_dialogs.snapshot",return_value=wrong):
            result=await ui.observe(ctx,sources="confirmations")
        self.assertEqual(result["sources"][0]["status"],"partial")
        self.assertIsNone(result["sources"][0]["count"])
        self.assertEqual(result["receipts"],[])

    async def test_native_dialog_snapshot_slice_progress_is_ordinal_not_old_field_merge(self):
        ctx=Context();ctx._ui_sampling_progress=ui.RoundRobinProgress(88);ctx._ui_last_sample={}
        ctx._ui_sampling_progress.positions["jomini_notifications"]=1
        with patch("pdx_runtime.v3_ui_dialogs.snapshot",side_effect=lambda client,**kw:
                dialog_snapshot(kw["source"],count=3,index=kw["start_index"])) as snapshot:
            first=await ui.observe(ctx,sources="jomini_notifications",max_items=1,max_queries=1)
            second=await ui.observe(ctx,sources="jomini_notifications",max_items=1,max_queries=1)
        self.assertEqual([call.kwargs["start_index"] for call in snapshot.call_args_list],[1,2])
        self.assertEqual(first["sources"][0]["status"],"partial")
        self.assertEqual(first["sources"][0]["count"],3)
        self.assertEqual(first["receipts"][0]["source_index"],1)
        self.assertEqual(second["receipts"][0]["source_index"],2)
        self.assertEqual(second["sampling_cursor"]["positions"]["jomini_notifications"],0)

    async def test_native_dialog_reader_caps_upper_bounds_without_disabling_source(self):
        from pdx_runtime import v3_ui_dialogs as dialogs
        ctx=Context()
        with patch("pdx_runtime.v3_ui_dialogs.snapshot",return_value=dialog_snapshot("confirmations")) as snapshot:
            result=await ui.observe(ctx,sources="confirmations",max_items=1000,output_bytes=2*dialogs.MAX_TEXT)
        self.assertEqual(snapshot.call_args.kwargs["max_items"],dialogs.MAX_ITEMS)
        self.assertEqual(snapshot.call_args.kwargs["text_capacity"],dialogs.MAX_TEXT)
        self.assertEqual(result["sources"][0]["status"],"available")
        self.assertEqual(result["sources"][0]["adapter_item_limit"],dialogs.MAX_ITEMS)

    async def test_native_dialog_unknown_profile_is_unavailable_not_false_empty(self):
        from pdx_runtime.target_descriptor import UnsupportedBuild
        ctx=Context()
        with patch("pdx_runtime.v3_ui_dialogs.snapshot",side_effect=UnsupportedBuild("no exact image profile")):
            result=await ui.observe(ctx,sources="confirmations")
        self.assertEqual(result["sources"][0]["status"],"unavailable")
        self.assertIsNone(result["sources"][0]["count"])
        self.assertIn("verified",result["sources"][0]["reason"])
        self.assertEqual(result["receipts"],[])
