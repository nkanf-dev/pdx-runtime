"""Readable pending events and explicit original option choices.

No option policy is installed. All native pointers and opaque source guards stay
inside the existing SDK callbacks and this context's private diagnostic records.
"""
from __future__ import annotations

from pdx_runtime.v3_wire import resolve_v3_export
import asyncio
import re
from pdx_runtime import v3_data as data, v3_access as access
from pdx_runtime.v3_game._text import plain_text
from . import _read, journal
from ._read import _diagnostic, _copied, _text_info








class _ReaderData:
    """Defer the existing reader's submissions into the shared core queue."""
    def __getattr__(self, name):
        target = getattr(data, name)
        if name in ("evaluate_pointer_array", "evaluate_element"):
            return lambda *args, **kwargs: lambda: target(*args, **kwargs)
        return target


class EventsAPI:
    def __init__(self, ctx):
        self.ctx = ctx
        if not hasattr(ctx, "_events_state"):
            ctx._events_state = {"world": None, "serial": 0, "by_tag": {}, "by_id": {}, "history": {}, "operations": {}, "choice_locks": {}}
        self._state = ctx._events_state

    async def _prepare(self):
        await self.ctx.refresh()
        if self._state["world"] != self.ctx.world:
            # Old unknown attempts remain in diagnostics, never rebound to a new world.
            self._state.update(world=self.ctx.world, by_tag={}, by_id={}, history={})
        return await self.ctx.type_id("EventWindow")

    def _source(self, type_id, index=0, count=None, tag=None):
        return data.PointerArraySource("PopupManager.Self", 0x10, type_id,
            self.ctx.interface_thread_id, index=index, expected_count=count,
            guard_expression="EventWindow.GetEvent.GetTooltipTag" if tag is not None else "",
            guard_input=data.TypedInput.string(tag) if tag is not None else None)

    async def _count(self, type_id):
        result = await self.ctx.query("events.pending.count", lambda:
            data.pointer_array_count(self.ctx.native, self._source(type_id),
                world=self.ctx.world, adapter=self.ctx.adapters["data"], process=self.ctx.process))
        if result.error or result.source_stages != 3 or result.root_count is None:
            raise RuntimeError("Cannot read current event windows: native error " + str(result.error))
        return result.root_count

    def _public(self, entry):
        record = entry["record"]
        fields = record.get("window_fields", {})
        def optional(name, result, *, rich=False):
            diagnostic = record.get("field_diagnostics", {}).get(name)
            return None if diagnostic is not None and not diagnostic.get("ok") else _copied(result, rich=rich)
        options = []
        for row in record.get("options", ()):
            options.append({"index": row["provider_index"], "label": _copied(row.get("text"), rich=True),
                "description": optional("option." + str(row["provider_index"]) + ".desc", row.get("desc"), rich=True),
                "enabled": _copied(row.get("enabled")), "default": _copied(row.get("default")),
                "highlighted": optional("option." + str(row["provider_index"]) + ".highlighted", row.get("highlighted")),
                "text": {"label": _text_info(row.get("text")),
                    "description": _text_info(row.get("desc"), record.get("field_diagnostics", {}).get(
                        "option." + str(row["provider_index"]) + ".desc"))}})
        unavailable = {name: "The current game response did not provide this field (" + str(d.get("status", "unavailable")) + ")."
            for name, d in record.get("field_diagnostics", {}).items() if not d.get("ok")}
        return {"id": entry["id"], "key": _copied(record.get("key")),
            "title": optional("window.title", fields.get("title"), rich=True),
            "body": optional("window.description", fields.get("description"), rich=True),
            "flavor": optional("window.flavor", fields.get("flavor"), rich=True), "options": options,
            "text": {name: _text_info(fields.get(field), record.get("field_diagnostics", {}).get("window." + field))
                for name, field in (("title", "title"), ("body", "description"), ("flavor", "flavor"))},
            "unavailable": unavailable, "choice_submitted": entry.get("attempt") is not None}

    async def _pending(self):
        type_id = await self._prepare()
        count = await self._count(type_id)
        reader = _read
        found = []
        for index in range(count):
            record = {"world": self.ctx.world, "window_index": index, "window_count": count}
            _diagnostic(self.ctx, record)  # Original reader owners survive any exception.
            await reader.read_pending_options(_ReaderData(), self.ctx.native, self.ctx.process,
                self.ctx.adapters["data"], lambda label, factory: self.ctx.query(label, factory),
                self.ctx.world, self.ctx.interface_thread_id, type_id, index, count, record)
            tag = record["instance_tag_bytes"]
            entry = self._state["by_tag"].get(tag)
            if entry is None:
                self._state["serial"] += 1
                entry = {"id": "event-" + str(self._state["serial"]), "tag": tag, "record": record}
                self._state["by_tag"][tag] = entry
                self._state["by_id"][entry["id"]] = entry
            else:
                entry["record"] = record
            found.append(self._public(entry))
        current_ids = {item["id"] for item in found}
        self._state["current_ids"] = current_ids
        for old_id, old in list(self._state["by_id"].items()):
            if old_id in current_ids or (old.get("attempt") and
                    ("public" not in old["attempt"] or old["attempt"]["public"].get("accepted") and
                     not old["attempt"]["public"].get("resolved"))):
                continue
            if old.get("attempt"):
                self._state["history"][old_id] = old["attempt"]["public"]
                while len(self._state["history"]) > 64:
                    self._state["history"].pop(next(iter(self._state["history"])))
            self._state["by_id"].pop(old_id)
            self._state["by_tag"].pop(old["tag"], None)
            operation = self._state["operations"].get(old_id)
            if operation is None or operation.done:
                self._state["operations"].pop(old_id, None)
                self._state["choice_locks"].pop(old_id, None)
        return found

    async def journal_entries(self, *, details=False):
        """Read current visible journal entries, goals and native condition text.

        details adds native completion/failure/timeout descriptions. This reads
        existing journal state only; it cannot create an entry or sign a treaty.
        Text completeness refers to copied text, not hidden script semantics.
        """
        async def read_journal():
            helper = journal
            return await helper.read(self.ctx, details=details)
        return await self.ctx.perform("events.journal_entries", read_journal)

    async def pending(self):
        """Return readable current events. Reading never chooses any option.

        Pass the returned id and a displayed option index to choose(). IDs are
        local event-instance tokens, not native fullIDs or event-definition keys.
        Descriptions are visible native tooltip text, not all hidden consequences.
        """
        return await self.ctx.perform("events.pending", self._pending)

    async def _world_pending(self):
        helper = _read
        process = self.ctx.process
        read = resolve_v3_export(process, self.ctx.adapters.get("carrier", "pdx_runtime.dll"), "PdxRuntimeRead")
        batch = resolve_v3_export(process, self.ctx.adapters["call"], "PdxNativeCallBatchInterface")
        slots = 16
        while True:
            initial, prepare, decode = helper.compose(read, self.ctx.image_base + 0x592D128, slots)
            result = await self.ctx.query("events.world_pending", lambda:
                access.submit_callback(self.ctx.native, batch, initial, mode=1, flag=0,
                    dispatch="interface", adapter=self.ctx.adapters["access"],
                    call_adapter=self.ctx.adapters["call"], process=process,
                    prepare_payload=prepare, decode_payload=decode))
            if result.error or (result.entered, result.left, result.execution_thread_id) != (1, 1, self.ctx.interface_thread_id):
                raise RuntimeError("Current pending-event membership is unavailable")
            copied = result.callee_result
            if copied["complete_ids_copied"] and copied["borrowed_storage_scrubbed"]:
                return copied
            if copied["header_copied"] and copied["borrowed_storage_scrubbed"] and slots < copied["native_count"] <= 256:
                slots = max(slots * 2, copied["native_count"])
                continue
            raise RuntimeError("Current pending-event membership was not copied completely")

    async def _pending_evidence(self):
        try:
            return await self._world_pending()
        except Exception as error:
            if getattr(self.ctx, "pending_queries", ()):
                raise
            # This optional global diagnostic is not the source of option
            # identity or permission to choose a valid visible event.
            record = {"unavailable_reason": str(error)}
            _diagnostic(self.ctx, {"event_membership": record})
            return record

    async def _tag_present(self, type_id, wanted):
        previous = None
        for _ in range(2):
            count = await self._count(type_id)
            if count == 0:
                return False
            tags = set()
            for index in range(count):
                result = await self.ctx.query("events.choice.observe_instance", lambda index=index:
                    data.evaluate_pointer_array(self.ctx.native, self._source(type_id, index, count),
                        "EventWindow.GetEvent.GetTooltipTag", world=self.ctx.world,
                        adapter=self.ctx.adapters["data"], process=self.ctx.process))
                if result.error:
                    return None
                tag = result.result.snapshot
                if tag == wanted:
                    return True
                if tag in tags:
                    return None
                tags.add(tag)
            if await self._count(type_id) != count:
                return None
            if previous is not None and previous != tags:
                return None
            previous = tags
        return False

    async def _reconcile(self, entry, type_id):
        attempt = entry["attempt"]
        result = attempt["result"]
        attempt["world_pending_after"] = await self._pending_evidence()
        attempt["target_tag_present"] = await self._tag_present(type_id, entry["tag"])
        guarded = bool(result.guard_checked and result.guard_passed and
                       result.condition_checked and result.condition_passed)
        chosen = bool(result.invoked and result.accepted and guarded)
        resolved = bool(chosen and attempt["target_tag_present"] is False)
        public = {"event_id": entry["id"], "option_index": attempt["option_index"],
            "status": ("resolved" if resolved else "submitted") if chosen and not result.error
                else "unavailable" if not result.invoked else "receipt_error",
            "accepted": chosen, "resolved": resolved}
        if result.error:
            public["reason"] = ("The event or its available choices changed." if result.error == 1237 else
                "This option is currently unavailable in the game." if result.error == 1223 else
                "The game did not confirm this choice; its operation is kept internally.")
        attempt["public"] = public
        return dict(public)

    async def choice(self, event_id):
        """Observe an original choice's effect without choosing it again."""
        async def observe_choice():
            type_id = await self._prepare()
            if event_id in self._state["history"]:
                return dict(self._state["history"][event_id])
            entry = self._state["by_id"].get(event_id)
            if entry is None or not entry.get("attempt"):
                return {"event_id": event_id, "status": "not_submitted", "accepted": False}
            if "result" not in entry["attempt"]:
                operation = self._state["operations"].get(event_id)
                return operation if operation is not None else {"event_id": event_id, "status": "pending"}
            return await self._reconcile(entry, type_id)
        return await self.ctx.perform("events.choice", observe_choice)

    async def _detail_functions(self, type_id):
        cache = self._state.setdefault("detail_functions", {})
        key = (self.ctx.world, type_id)
        if key in cache:
            return cache[key]
        rows, start = [], 0
        while True:
            page = await self.ctx.query("events.details.metadata", lambda:
                data.enumerate_functions(self.ctx.native, type_id, start=start, capacity=64,
                    adapter=self.ctx.adapters["data"], process=self.ctx.process))
            if page.error:
                _diagnostic(self.ctx, {"event_detail_metadata": page})
                return []
            rows.extend(page.rows)
            if page.next >= page.total or page.next <= start:
                break
            start = page.next
        cache[key] = rows
        return rows

    @staticmethod
    def _field_name(getter):
        name = getter[3:] if getter.startswith("Get") else getter
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()

    async def details(self, event_id, *, option_index=None, fields=None):
        """Inspect current native event text, tooltips, timing and extra flags.

        Discover zero-argument pure getters on EventWindow, its Event and each
        original option. Text and scalar results are copied under the original
        instance guard. Object/argument-dependent fields get an individual
        reason, not an invented value. No action is invoked to inspect a field.
        Native descriptions are not a promise that hidden scripts are visible.

        fields optionally selects readable names such as tooltip or
        trigger_desc; omit it for all currently discoverable scalar getters.
        """
        if option_index is not None and (isinstance(option_index, bool) or not isinstance(option_index, int) or option_index < 0):
            raise ValueError("option_index must be a nonnegative displayed option index")
        selected_fields = None if fields is None else {fields} if isinstance(fields, str) else set(fields)
        async def inspect_event():
            type_id = await self._prepare()
            await self._pending()
            entry = self._state["by_id"].get(event_id)
            if entry is None:
                matches = [e for e in self._state["by_id"].values()
                    if e["id"] in self._state.get("current_ids", ()) and _copied(e["record"].get("key")) == event_id]
                entry = matches[0] if len(matches) == 1 else None
            if entry is None:
                return {"id": event_id, "available": False, "reason": "This event is no longer open."}
            record = entry["record"]
            count = await self._count(type_id)
            positions = []
            for index in range(count):
                result = await self.ctx.query("events.details.locate", lambda index=index:
                    data.evaluate_pointer_array(self.ctx.native, self._source(type_id, index, count),
                        "EventWindow.GetEvent.GetTooltipTag", world=self.ctx.world,
                        adapter=self.ctx.adapters["data"], process=self.ctx.process))
                if not result.error and result.result.snapshot == entry["tag"]:
                    positions.append(index)
            if len(positions) != 1:
                return {"id": entry["id"], "available": False, "reason": "The event window changed; read current events again."}
            source = self._source(type_id, positions[0], count, entry["tag"].decode("utf-8"))
            all_types = await self.ctx.all_types()
            type_names = {identifier: name for name, identifier in all_types.items()}
            window_functions = await self._detail_functions(type_id)
            event_function = next((f for f in window_functions if f.name == "GetEvent"), None)
            event_functions = await self._detail_functions(event_function.return_type_id) if event_function else []
            # Some factory reference aliases have no named function directory;
            # use the named Event metadata, with the real GetEvent VM context.
            if not event_functions and "Event" in all_types:
                event_functions = await self._detail_functions(all_types["Event"])
            option_functions = await self._detail_functions(all_types["EventOption"]) if "EventOption" in all_types else []
            raw = {"event_id": entry["id"], "window": {}, "event": {}, "options": {}}
            _diagnostic(self.ctx, raw)
            output = {"window": {}, "event": {}, "options": []}
            unavailable = {}
            primitives = {"cstring", "string", "bool", "int", "int32", "int64", "uint32", "uint64",
                "int32_t", "int64_t", "uint32_t", "uint64_t", "float", "double", "cfixedpoint", "fixedpoint"}
            seen_fields = set()

            async def group(owner, functions, prefix, destination, original_values, index=None):
                for function in functions:
                    getter = function.name
                    if not getter.startswith(("Get", "Is", "Has")):
                        continue  # Never evaluate Select, OpenInEditor or another action.
                    name = self._field_name(getter)
                    seen_fields.add(name)
                    if selected_fields is not None and name not in selected_fields and owner + "." + name not in selected_fields:
                        continue
                    key = owner + "." + name if index is None else "option." + str(index) + "." + name
                    if function.argument_count:
                        destination[name] = None
                        unavailable[key] = "This native getter needs an additional game argument."
                        continue
                    native_type = type_names.get(function.return_type_id, "")
                    if native_type.lower() not in primitives:
                        destination[name] = None
                        unavailable[key] = "The native getter returns " + (native_type or "an object/reference") + "; no copied scalar detail source is available for this field."
                        continue
                    expression = prefix + "." + getter
                    capacity = 65536
                    reads = []
                    while True:
                        result = await self.ctx.query("events.details.field", lambda expression=expression:
                            data.evaluate_pointer_array(self.ctx.native, source, expression, world=self.ctx.world,
                                output_bytes=capacity, adapter=self.ctx.adapters["data"], process=self.ctx.process)) if index is None else await self.ctx.query(
                            "events.details.option_field", lambda expression=expression, index=index:
                            data.evaluate_element(self.ctx.native, "EventWindow.GetOptions", index, expression,
                                world=self.ctx.world, expected_count=len(record["options"]), pointer_array=source,
                                output_bytes=capacity, adapter=self.ctx.adapters["data"], process=self.ctx.process))
                        reads.append(result)
                        if result.error != 234 or result.result.total <= capacity:
                            break
                        capacity = result.result.total  # Fresh readonly getter, never replay an action.
                    original_values[name] = {"result": result, "reads": reads}
                    if result.error:
                        destination[name] = None
                        unavailable[key] = "The game did not provide this detail (native error " + str(result.error) + ")."
                    elif not result.guard_checked or not result.guard_passed:
                        destination[name] = None
                        unavailable[key] = "The original event instance changed while reading this detail."
                    else:
                        destination[name] = _copied(result, rich=True)
                        if destination[name] is None:
                            unavailable[key] = "The original native value could not be copied completely."

            await group("window", window_functions, "EventWindow", output["window"], raw["window"])
            await group("event", event_functions, "EventWindow.GetEvent", output["event"], raw["event"])
            for row in record["options"]:
                index = row["provider_index"]
                if option_index is not None and index != option_index:
                    continue
                destination = {"index": index}
                raw["options"][index] = {}
                await group("option", option_functions, "EventOption", destination, raw["options"][index], index)
                output["options"].append(destination)
            if option_index is not None and not output["options"]:
                unavailable["option"] = "That option is not in the current original option list."
            if selected_fields is not None:
                for name in selected_fields:
                    if name.split(".")[-1] not in seen_fields:
                        unavailable[name] = "No native getter for this detail was found in the current event metadata."
            final = await self.ctx.query("events.details.instance", lambda:
                data.evaluate_pointer_array(self.ctx.native, source, "EventWindow.GetEvent.GetTooltipTag",
                    world=self.ctx.world, adapter=self.ctx.adapters["data"], process=self.ctx.process))
            raw["instance_after"] = final
            current = bool(not final.error and final.guard_checked and final.guard_passed and final.result.snapshot == entry["tag"])
            public = self._public(entry)
            public.update(available=True, current=current, details=output)
            public["unavailable"].update(unavailable)
            return public
        return await self.ctx.perform("events.details", inspect_event)

    async def inspect(self, event_id, **options):
        """Readable alias for details(); it never chooses or probes actions."""
        return await self.details(event_id, **options)

    async def choose(self, event_id, option_index):
        """Choose one displayed original option once, with fresh native guards.

        Repeating the same event choice returns the original receipt; it never
        submits another Select. Unknown delivery stays owned by the shared core.
        """
        if isinstance(option_index, bool) or not isinstance(option_index, int) or option_index < 0:
            raise ValueError("option_index must be a displayed nonnegative option index")
        pending_operation = self._state["operations"].get(event_id)
        if pending_operation is not None:
            entry = self._state["by_id"].get(event_id)
            if entry and entry.get("attempt", {}).get("option_index") != option_index:
                raise ValueError("This event already received a different choice")
            return await pending_operation.resume()
        async def choose_once():
            type_id = await self._prepare()
            previous = self._state["history"].get(event_id)
            if previous is not None:
                if previous["option_index"] != option_index:
                    raise ValueError("This event already received a different choice")
                return dict(previous)
            entry = self._state["by_id"].get(event_id)
            if entry is None:
                await self._pending()
                entry = self._state["by_id"].get(event_id)
                if entry is None:
                    matches = [e for e in self._state["by_id"].values() if e["id"] in self._state.get("current_ids", ())
                               and _copied(e["record"].get("key")) == event_id]
                    if len(matches) == 1:
                        entry = matches[0]
            if entry is None:
                raise ValueError("Event is no longer available; read game.events.pending()")
            attempt = entry.get("attempt")
            if attempt is not None:
                if attempt["option_index"] != option_index:
                    raise RuntimeError("This event already has an original choice in progress")
                if "result" in attempt:
                    return await self._reconcile(entry, type_id)
                operation = self._state["operations"].get(event_id)
                return operation if operation is not None else {"event_id": event_id, "status": "pending"}
            options = entry["record"].get("options", ())
            if not any(row["provider_index"] == option_index for row in options):
                raise ValueError("Option is not in the current original option list")
            count = await self._count(type_id)
            positions = []
            for index in range(count):
                tag = await self.ctx.query("events.choose.find", lambda index=index:
                    data.evaluate_pointer_array(self.ctx.native, self._source(type_id, index, count),
                        "EventWindow.GetEvent.GetTooltipTag", world=self.ctx.world,
                        adapter=self.ctx.adapters["data"], process=self.ctx.process))
                if tag.error:
                    raise RuntimeError("Event changed while locating its original instance")
                if tag.result.snapshot == entry["tag"]:
                    positions.append(index)
            if len(positions) != 1:
                raise ValueError("Event is no longer available or its instance is ambiguous")
            original_tag = entry["tag"].decode("utf-8")
            fresh = {"world": self.ctx.world, "window_index": positions[0], "window_count": count}
            _diagnostic(self.ctx, fresh)
            reader = _read
            await reader.read_pending_options(_ReaderData(), self.ctx.native, self.ctx.process,
                self.ctx.adapters["data"], lambda label, factory: self.ctx.query(label, factory), self.ctx.world,
                self.ctx.interface_thread_id, type_id, positions[0], count, fresh)
            if fresh["instance_tag_bytes"] != entry["tag"]:
                raise ValueError("This event changed while opening its current choices")
            old_choice = next(row for row in options if row["provider_index"] == option_index)
            new_choice = next((row for row in fresh["options"] if row["provider_index"] == option_index), None)
            entry["record"] = fresh
            if new_choice is None or _copied(new_choice.get("text")) != _copied(old_choice.get("text")):
                return {"event_id": entry["id"], "status": "options_changed", "accepted": False,
                        "reason": "The choices changed; use the current options.", "event": self._public(entry)}
            if _copied(new_choice.get("enabled")) is not True:
                return {"event_id": entry["id"], "option_index": option_index, "status": "unavailable",
                        "accepted": False, "reason": "This option is currently unavailable in the game.",
                        "event": self._public(entry)}
            options = fresh["options"]
            attempt = {"event_id": entry["id"], "option_index": option_index}
            entry["attempt"] = attempt
            _diagnostic(self.ctx, attempt)
            attempt["world_pending_before"] = await self._pending_evidence()
            attempt["select_attempted"] = True
            result = await self.ctx.query("events.choose.select", lambda:
                data.invoke_element_action(self.ctx.native, "EventWindow.GetOptions", option_index,
                    "EventOption.Select", data.TypedInput.empty(), world=self.ctx.world,
                    condition="EventOption.IsEnabled", expected_count=len(options),
                    pointer_array=self._source(type_id, positions[0], count, original_tag),
                    adapter=self.ctx.adapters["data"], process=self.ctx.process))
            attempt["result"] = result
            return await self._reconcile(entry, type_id)
        lock = self._state["choice_locks"].setdefault(event_id, asyncio.Lock())
        async def serialized():
            async with lock:
                return await choose_once()
        result = await self.ctx.perform("events.choose", serialized)
        if hasattr(result, "resume") and hasattr(result, "task"):
            self._state["operations"][event_id] = result
        return result
