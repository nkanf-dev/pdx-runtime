"""Ordinary research selection and queue operations in one existing V3 session.

``start(key)`` uses the game's enqueue command when no research is selected.
It does not preempt a different current technology. ``enqueue`` appends through
native queue rules; it does not grant technology or add research progress.
"""
from __future__ import annotations

from types import SimpleNamespace

from ... import v3_data as data, v3_objects as objects
from .._values import fixed_decimal
from ..country import _plain_name
from ..contracts import (
    OperationResult, ResearchCategory, ResearchChange, ResearchCurrent,
    ResearchDetails, ResearchOption, ResearchQueueEntry, ResearchSubmittedChange,
    ResearchNativeResult,
)
from .._run import rejected


# These are registered native Country Creation sources, not guessed joins.
_SOURCES = {
    "production": "Country.AccessResearchableTechnologiesProduction",
    "military": "Country.AccessResearchableTechnologiesMilitary",
    "society": "Country.AccessResearchableTechnologiesSociety",
    "other": "Country.AccessResearchableTechnologiesOther",
}


class ResearchAPI:
    """Read real technologies and submit ordinary native queue commands.

    All methods accept game keys. Definition origins, current-world metadata,
    source indices, retained values and command ownership stay private.
    """

    def __init__(self, ctx):
        self._ctx = ctx
        self._name_results = {}  # Copied strings/failures only, never native owners.

    async def current(self) -> OperationResult[ResearchCurrent]:
        """Return actual current identity and native progress/cost in points.

        Current research need not occur in any researchable-candidate list.
        The fresh original receiver is consumed inside one Access callback;
        only identity and native progress/cost PODs cross back to Python.
        """
        return await self._ctx.perform("research.current", self._current)

    async def options(self, category: ResearchCategory | None = None) -> OperationResult[list[ResearchOption]]:
        """List real native candidates with cost, progress and eligibility.

        Categories are production, military, society and other. A candidate list
        alone is not eligibility. ``researchable`` preserves the native pure
        flag. ``can_start`` validates selection for the target Country using
        the original GUI command rule, including prerequisites and current state.
        Queuing a locked technology can be valid without selecting it.
        """
        categories = self._categories(category)
        return await self._ctx.perform("research.options", lambda: self._options(categories))

    async def details(self, key: str) -> OperationResult[ResearchDetails]:
        """Read a real technology by definition key, including researched ones.

        Completion comes from native HasResearchedTech for the selected Country,
        not candidate membership or progress. This read never selects research,
        changes the queue, or evaluates a research action's eligibility.
        """
        key = self._key(key)
        return await self._ctx.perform("research.details", lambda: self._key_details(key))

    async def queue(self) -> OperationResult[list[ResearchQueueEntry]]:
        """Return complete native planned order, excluding current research.

        V3's Top and Bottom queue views exclude current selection. Their order is
        preserved, not sorted or deduplicated. This is not an atomic snapshot.
        """
        return await self._ctx.perform("research.queue", self._queue)

    async def innovation(self) -> OperationResult[dict[str, object]]:
        """Read native weekly raw/usable/max innovation and research speed.

        Values are exact V3 fixed-point Decimals, not percentages derived from
        technology cost. The native innovation/cap/speed descriptions keep their
        original rich text so modifier explanations are not silently discarded.
        Field observations and reasons preserve partial or failed native cells.
        """
        return await self._ctx.perform("research.innovation", self._innovation)

    async def spread(self) -> OperationResult[dict[str, object]]:
        """Read observed passive-spread descriptions and country-wide rates.

        The original localized text is *not* a list of verified technology keys
        across all three trees. Country-wide base/min/max values are not per-tech
        rates. This partial read does not claim to identify every spreading tech.
        """
        return await self._ctx.perform("research.spread", self._spread)

    async def start(self, key: str) -> OperationResult[ResearchChange]:
        """Select eligible research using native enqueue when none is current.

        If another technology is current, raise a meaningful error; use enqueue
        to plan work after it. This method never pretends append means preempt.
        An accepted command and an independently observed selection are separate.
        """
        key = self._key(key)
        return await self._ctx.perform("research.start", lambda: self._change("start", key))

    async def enqueue(self, key: str) -> OperationResult[ResearchSubmittedChange | ResearchNativeResult]:
        """Append one real technology through ordinary native queue rules.

        The engine can select it if current research is empty. Native validation
        rejects researched/duplicate entries, not every prerequisite rule.
        """
        key = self._key(key)
        return await self._ctx.perform("research.enqueue", lambda: self._change("enqueue", key))

    async def enqueue_prerequisites(self, key: str) -> OperationResult[ResearchSubmittedChange | ResearchNativeResult]:
        """Queue a technology with the game's required prerequisite unlocks.

        This is the original AddToQueueWithUnlocks intent, not front insertion,
        forced completion or a dependency graph built by Python. The game can
        select a prerequisite when current research is empty. Native validation,
        acceptance and independently observed queue/selection stay separate.
        """
        key = self._key(key)
        return await self._ctx.perform("research.enqueue_prerequisites",
            lambda: self._change("enqueue", key, with_unlocks=True))

    async def dequeue(self, key: str) -> OperationResult[ResearchSubmittedChange | ResearchNativeResult]:
        """Remove a queued/current technology through the native removal rule.

        Native removal can also prune dependent entries and select a replacement.
        It is not an inverse operation or a progress/completion effect.
        """
        key = self._key(key)
        return await self._ctx.perform("research.dequeue", lambda: self._change("dequeue", key))

    @staticmethod
    async def _country_metrics(ctx, fields, label):
        # Import at use-time: the country observations support is shared by
        # other domains and need not be loaded by ordinary queue operations.
        from .._read import Field, read_fields
        native_fields = tuple(Field(name, "Country." + getter, kind, unit) for name, getter, kind, unit in fields)
        observed = await read_fields(ctx, native_fields, label=label)
        # Values/reasons/snapshot come from one native projection page (or the
        # support's original-owner fallback), never a second ordinary action.
        return {**observed, **observed["values"]}

    async def _innovation(self):
        fields = (
            ("raw_innovation", "GetWeeklyRawInnovation", "fixed", "research_points_per_week"),
            ("innovation", "GetWeeklyInnovation", "fixed", "research_points_per_week"),
            ("max_innovation", "GetWeeklyMaxInnovation", "fixed", "research_points_per_week"),
            ("research_speed", "GetWeeklyResearchSpeed", "fixed", "research_points_per_week"),
            ("innovation_description", "GetWeeklyInnovationDesc", "text", None),
            ("max_innovation_description", "GetWeeklyMaxInnovationDesc", "text", None),
            ("research_speed_description", "GetWeeklyResearchSpeedDesc", "text", None),
        )
        return await self._country_metrics(self._ctx, fields, "research.innovation")

    async def _spread(self):
        fields = (
            ("currently_spreading_description", "GetCurrentlySpreadingTechsDesc", "text", None),
            ("base_weekly_spread", "GetBaseWeeklyTechnologySpread", "fixed", "research_points_per_week"),
            ("min_weekly_spread", "GetMinWeeklyTechnologySpread", "fixed", "research_points_per_week"),
            ("max_weekly_spread", "GetMaxWeeklyTechnologySpread", "fixed", "research_points_per_week"),
            ("weekly_spread_description", "GetWeeklyTechnologySpreadDesc", "text", None),
            ("weekly_spread_breakdown", "GetWeeklyTechnologySpreadBreakdown", "text", None),
        )
        observed = await self._country_metrics(self._ctx, fields, "research.spread")
        return {**observed, "observation_complete": observed["complete"],
            "complete": False, "tech_identification_complete": False,
            "tech_identification_reason": (
                "Country.GetCurrentlySpreadingTechsDesc is localized text, not a "
                "complete category/technology-key list. The per-technology spread "
                "getters require a Technology argument that this read does not bind."
            )}

    @staticmethod
    def _key(key):
        if not isinstance(key, str) or not key or key != key.strip() or "\0" in key:
            raise ValueError("technology must be a nonempty genuine technology key")
        return key

    @staticmethod
    def _categories(category):
        if category is None:
            return tuple(_SOURCES)
        if not isinstance(category, str) or category not in _SOURCES:
            raise ValueError("research category must be production, military, society or other")
        return (category,)

    def _remember(self, value):
        # Evidence belongs to internal context, never the ordinary return value.
        self._ctx.diagnostics.append({"domain": "research", "evidence": value})

    async def _new_plan(self):
        ctx = self._ctx
        await ctx.refresh()
        actor = await ctx.country_id()
        from . import _queue as helper
        plan = helper.new_plan(ctx, actor)
        self._remember(plan)
        plan["types"] = {name: await ctx.type_id(name) for name in ("Country", "Technology")}
        return helper, plan

    async def _cleanup(self, helper, plan):
        await helper.cleanup_known(plan)

    async def _definitions(self, helper, plan):
        handoff = helper
        records, start, text_capacity = [], 0, 65536
        while True:
            page = await handoff._once(plan, "research.definition-page", lambda: objects.enumerate_technology_definitions(
                self._ctx.native, start_index=start, max_items=256, text_capacity=text_capacity,
                adapter=self._ctx.adapters["objects"], process=self._ctx.process))
            if page.world != plan["world"] or page.error not in (0, 234):
                raise RuntimeError("technology definitions are unavailable in the current world")
            records.extend(page.records)
            if page.error == 234 and page.next_index <= start:
                options = page.definition_options
                required = getattr(options, "text_bytes_required", 0)
                if required <= text_capacity or page.records:
                    raise RuntimeError("technology definition key read did not advance")
                text_capacity = required
                continue
            if page.next_index >= page.slot_bound and not page.truncated:
                break
            if page.next_index <= start:
                raise RuntimeError("technology definition enumeration did not advance")
            start = page.next_index
        plan["definitions"] = SimpleNamespace(records=tuple(records))

    async def _functions(self, helper, plan):
        """Confirm native metadata instead of assuming old runtime type IDs."""
        functions, start = {}, 0
        while True:
            page = helper._ok(await helper._once(plan,
                "research.technology-functions", lambda: data.enumerate_functions(self._ctx.native,
                    plan["types"]["Technology"], start=start, capacity=64,
                    adapter=self._ctx.adapters["data"], process=self._ctx.process)),
                "technology function metadata")
            for row in page.rows:
                if row.name in functions:
                    raise RuntimeError("Technology function metadata has duplicate names")
                functions[row.name] = row
            if page.next >= page.total:
                return functions
            if page.next <= start:
                raise RuntimeError("Technology function metadata did not advance")
            start = page.next

    async def _value(self, helper, plan, expression, bindings, expected):
        result = await helper._eval(plan, expression, expression, bindings)
        value = result.value
        if not isinstance(value, expected):
            raise RuntimeError(expression + ": native result has an unexpected type")
        return value

    async def _eligibility(self, helper, plan, bindings, functions):
        """Preserve the native pure flag and validate selection for this Country.

        Actual V3 metadata registers IsResearchable with zero arguments. That
        flag is not silently treated as another country's complete eligibility.
        The installed GUI uses IsValid(Technology.Research(GetPlayer.Self)) for
        country-specific selection. Country.Self here is our genuine target.
        Both reads only produce/query shared values; neither submits a command.
        """
        predicate = functions.get("IsResearchable")
        researchable, predicate_has_country = None, False
        expressions = plan.setdefault("eligibility_expressions", [])
        if predicate is not None and predicate.argument_count == 0:
            # With an explicit foreign actor the zero-argument getter's implicit
            # actor semantics have not been proved. The explicit Country command
            # validation below still returns useful selection eligibility.
            if getattr(self._ctx, "explicit_country_id", None) is None:
                expression = "Technology.IsResearchable"
                expressions.append(expression)
                researchable = await self._value(helper, plan, expression, bindings, bool)
        elif predicate is not None and predicate.argument_count == 1:
            expression = "Technology.IsResearchable(Country.Self)"
            expressions.append(expression)
            researchable = await self._value(helper, plan, expression, bindings, bool)
            predicate_has_country = True
        research = functions.get("Research")
        if research is not None and research.argument_count == 1:
            expression = "IsValid(Technology.Research(Country.Self))"
            expressions.append(expression)
            can_start = await self._value(helper, plan, expression, bindings, bool)
            reason = None if can_start else "the game's native research selection validation rejects this technology"
        elif predicate_has_country:
            can_start = researchable
            reason = None if can_start else "native researchability predicate is false"
        else:
            can_start = None
            reason = "country-specific selection eligibility is not exposed by the current function signatures"
        return researchable, can_start, reason

    async def _details(self, helper, plan, technology, category, functions, *, receiver=None, include_eligibility=True):
        country = helper._country(plan)
        bindings = (technology["binding"], country) if receiver is None else (country,)
        prefix = "Technology" if receiver is None else receiver
        values = {}
        for name, expected in (("GetProgress", data.FixedPoint), ("GetCost", data.FixedPoint),
                               ("HasResearchedTech", bool), ("IsResearching", bool)):
            metadata = functions.get(name)
            if metadata is None or metadata.argument_count != 1:
                raise RuntimeError("native Technology." + name + "(Country) is unavailable")
            values[name] = await self._value(helper, plan, prefix + "." + name + "(Country.Self)",
                bindings, expected)
        if include_eligibility:
            researchable, can_start, eligibility_reason = await self._eligibility(helper, plan, bindings, functions)
        else:
            researchable, can_start = None, None
            eligibility_reason = "selection eligibility is not queried by this read"
        name = None
        name_getter = next((getter for getter in ("GetNameNoFormatting", "GetName")
            if getter in functions and functions[getter].argument_count == 0), None)
        if name_getter is not None:
            name_bindings = (technology["binding"],) if receiver is None else ()
            rich = await self._value(helper, plan, prefix + "." + name_getter, name_bindings, str)
            plan.setdefault("rich_names", []).append({"key": technology["key"], "getter": name_getter, "value": rich})
            name = _plain_name(rich)
        return {"key": technology["key"], "name": name, "category": category,
            "progress": fixed_decimal(values["GetProgress"]),
            "cost": fixed_decimal(values["GetCost"]),
            "unit": "research_points", "researched": values["HasResearchedTech"],
            "is_current": values["IsResearching"], "researchable": researchable, "can_start": can_start,
            "eligibility_reason": eligibility_reason}

    async def _key_details(self, key):
        helper, plan = await self._new_plan()
        try:
            await self._definitions(helper, plan)
            records = plan["definitions"].records
            matches = [record for record in records if record.key == key]
            if len(matches) > 1:
                raise RuntimeError("technology key is not unique in current native definitions: " + key)
            if not matches:
                if any(record.error or record.key_error for record in records):
                    raise RuntimeError("native technology definition keys are incomplete; the requested key is unknown")
                raise ValueError("technology key not found in current native definitions: " + key)
            if matches[0].error or matches[0].key_error:
                raise RuntimeError("the requested native technology definition could not be read completely")
            if any(char in key for char in ("'", "\\")) or any(ord(char) < 32 for char in key):
                raise ValueError("the native technology key needs literal escaping not provided by this factory query")
            functions = await self._functions(helper, plan)
            # Definition pointers are identity evidence, never CData receivers.
            # Each factory+getter stays inside one native expression; only its
            # owned scalar Any result survives until the existing cleanup.
            result = await self._details(helper, plan, {"key": key}, None, functions,
                receiver="GetTechnology('" + key + "')", include_eligibility=False)
            result["category_reason"] = "category is not queried by this definition-key read"
            if result["name"] is None:
                result["name_reason"] = "native technology metadata has no zero-argument name getter"
            return result
        finally:
            await self._cleanup(helper, plan)

    async def _candidates(self, helper, plan, categories, *, wanted=None):
        await self._definitions(helper, plan)
        functions = await self._functions(helper, plan)
        rows = []
        handoff = helper
        for category in categories:
            expression = _SOURCES[category]
            source = await handoff._eval(plan, expression, expression, (handoff._country(plan),))
            if not isinstance(source.value, data.NativeList):
                raise RuntimeError("native research candidate source is not an owned list")
            count = handoff._ok(await handoff._once(plan, "research.candidate-count", lambda:
                data.list_count(self._ctx.native, source.value_handle,
                    adapter=self._ctx.adapters["data"], process=self._ctx.process)), "research candidate count")
            for index in range(count.total):
                result = handoff._ok(await handoff._once(plan, "research.candidate", lambda i=index:
                    data.list_element(self._ctx.native, source.value_handle, i,
                        adapter=self._ctx.adapters["data"], process=self._ctx.process)), "research candidate")
                technology = await helper._technology(plan, result)
                if wanted is not None and technology["key"] != wanted:
                    continue
                details = await self._details(helper, plan, technology, category, functions)
                rows.append({"public": details, "expression": expression, "index": index})
            end = handoff._ok(await handoff._once(plan, "research.candidate-end-count", lambda:
                data.list_count(self._ctx.native, source.value_handle,
                    adapter=self._ctx.adapters["data"], process=self._ctx.process)), "research candidate end count")
            if end.total != count.total:
                raise RuntimeError("research candidates changed while reading; no stable selection")
        keys = [row["public"]["key"] for row in rows]
        if len(set(keys)) != len(keys):
            raise RuntimeError("technology has multiple native candidate identities; no guessed selection")
        return rows

    async def _options(self, categories):
        helper, plan = await self._new_plan()
        try:
            return [row["public"] for row in await self._candidates(helper, plan, categories)]
        finally:
            await self._cleanup(helper, plan)

    def _module_bases(self, roles):
        # The mature POD reader accepts observed module bases. Public context
        # adapters can also be packaged DLL aliases; resolve those internally.
        from ...v3_wire import V3_EXPORT_ALIASES
        loaded = None
        bases = {}
        for role in roles:
            adapter = self._ctx.adapters[role]
            if isinstance(adapter, int) and not isinstance(adapter, bool):
                bases[role] = adapter
                continue
            if not isinstance(adapter, str):
                raise ValueError("research adapter must be a loaded module name or base")
            normalized = adapter.casefold()
            if not normalized.endswith(".dll"):
                normalized += ".dll"
            if loaded is None:
                loaded = self._ctx.process.modules()
            module = next((row for row in loaded if row["name"].casefold() == normalized), None)
            if module is None and normalized in V3_EXPORT_ALIASES:
                target = V3_EXPORT_ALIASES[normalized]
                module = next((row for row in loaded if row["name"].casefold() == target), None)
            if module is None:
                raise RuntimeError("research adapter module is not loaded: " + adapter)
            bases[role] = module["base"]
        return bases

    async def _identity(self, *, include_progress=False):
        ctx = self._ctx
        await ctx.refresh()
        actor = await ctx.country_id()
        from . import _current as helper
        record = {}
        self._remember(record)
        roles = ("data", "objects", "access", "call", "carrier", "console")
        modules = self._module_bases(roles)
        intent = {"world": ctx.world, "actor_id": actor, "record": record,
            "with_progress": include_progress,
            "identity": {"pid": ctx.native.pid, "base": ctx.image_base,
                         "modules": {name: {"base": modules[name]} for name in roles}},
            "await_result": lambda label, query: ctx.wait(query), "submit_query": ctx.submit}
        result = await helper.read_current(ctx.session, ctx.process, intent)
        if not result.get("current_known"):
            raise RuntimeError("current research changed or could not be identified; observe again")
        if result.get("has_current_research") and not result.get("key_known"):
            raise RuntimeError("current research identity is unavailable")
        known = result.get("progress_known") is True
        return {"country_id": actor, "key": result.get("key"), "_world": ctx.world,
                "has_current_research": result["has_current_research"], "atomic_snapshot": False,
                "progress": fixed_decimal(data.FixedPoint(result["progress_bits"])) if known else None,
                "cost": fixed_decimal(data.FixedPoint(result["cost_bits"])) if known else None}

    async def _current_name(self, key, expected_world):
        """Copy the localized name in one original Data expression.

        The key was already read from the genuine current definition. The GUI's
        GetTechnology(key) factory and its name getter stay inside ONE native
        evaluation. Only the returned String is retained, not a borrowed/by-value
        Technology result rebound as an owned receiver in a later callback.
        """
        # A failure in the former split factory/receiver path does not reject a
        # newly supported compound expression after a normal source hot-update.
        cache_key = (expected_world, key, "compound_name")
        if cache_key in self._name_results:
            cached_name, reason = self._name_results[cache_key]
            return _plain_name(cached_name) if cached_name is not None else None, reason
        if any(char in key for char in ("'", "\\")) or any(ord(char) < 32 for char in key):
            return None, "the native key needs literal escaping not provided by the current factory"
        helper, plan = await self._new_plan()
        try:
            if plan["world"] != expected_world:
                raise RuntimeError("world changed while observing the current technology name")
            functions = await self._functions(helper, plan)
            getter = next((name for name in ("GetNameNoFormatting", "GetName")
                if name in functions and functions[name].argument_count == 0), None)
            if getter is None:
                value = (None, "native technology metadata has no localized name getter")
            else:
                expression = "GetTechnology('" + key + "')." + getter
                rich = await self._value(helper, plan, expression, (), str)
                plan.setdefault("rich_names", []).append({"key": key, "getter": getter, "value": rich})
                value = (_plain_name(rich), None)
        except (ValueError, RuntimeError) as error:
            if plan.get("pending") is not None or plan.get("buffers") or plan.get("uncertain_operations"):
                raise
            value = (None, "native localized technology name could not be read: " + str(error))
        finally:
            await self._cleanup(helper, plan)
        self._name_results[cache_key] = value
        if len(self._name_results) > 64:
            del self._name_results[next(iter(self._name_results))]
        return value

    async def _current(self):
        current = await self._identity(include_progress=True)
        world = current.pop("_world")
        if not current["has_current_research"]:
            return {**current, "name": None, "unit": "research_points", "reason": "no current research"}
        name, name_reason = await self._current_name(current["key"], world)
        return {**current, "name": name, "name_reason": name_reason,
                "is_current": True, "unit": "research_points"}

    @staticmethod
    def _queue_public(observation):
        return [{"key": row["key"], "position": index + 1}
                for index, row in enumerate(observation["entries"])]

    async def _queue(self):
        helper, plan = await self._new_plan()
        try:
            await self._definitions(helper, plan)
            observation = await helper.observe_queue(plan)
            return self._queue_public(observation)
        finally:
            await self._cleanup(helper, plan)

    @staticmethod
    def _reason(validation):
        # CommandResult.reason decodes the original Validate text buffer.
        reason = getattr(validation, "reason", None)
        if reason:
            return str(reason)
        text = getattr(validation, "text", None)
        return str(text) if text else "the game's native research queue validation rejected this technology"

    async def _change(self, kind, key, *, with_unlocks=False):
        identity = await self._identity() if kind == "start" else None
        if identity is not None and identity["has_current_research"]:
            if identity["key"] == key:
                return {"key": key, "accepted": None, "is_current": True,
                        "status": "already_current", "submitted": False}
            raise ValueError("research is already active: " + identity["key"]
                + "; enqueue('" + key + "') plans another technology without preempting it")
        helper, scan = await self._new_plan()
        selected = None
        try:
            if kind == "dequeue":
                await self._definitions(helper, scan)
                observed = await helper.observe_queue(scan)
                for part, expression in (("top", "Country.GetResearchQueueTop"),
                                         ("bottom", "Country.GetResearchQueueBottom")):
                    for index, row in enumerate(observed[part]["entries"]):
                        if row["key"] == key:
                            if selected is not None:
                                raise RuntimeError("technology occurs more than once in the native queue")
                            selected = {"expression": expression, "index": index}
                if selected is None:
                    # The native queue views omit current. A real candidate
                    # receiver is also a valid original removal-command source.
                    rows = await self._candidates(helper, scan, tuple(_SOURCES), wanted=key)
                    if rows and rows[0]["public"]["is_current"]:
                        selected = rows[0]
            else:
                rows = await self._candidates(helper, scan, tuple(_SOURCES), wanted=key)
                if rows:
                    selected = rows[0]
                    if kind == "start" and selected["public"].get("can_start", selected["public"]["researchable"]) is not True:
                        reason = selected["public"].get("eligibility_reason") or "native selection eligibility is unknown"
                        raise ValueError(key + " cannot be selected: " + reason)
            if selected is None:
                raise ValueError("technology key not found in the current native "
                    + ("research queue: " if kind == "dequeue" else "research candidates: ") + key)
            source, index = selected["expression"], selected["index"]
        finally:
            await self._cleanup(helper, scan)
        ctx = self._ctx
        # Preparation rechecks the actual source origin/key and queue state.
        # The selected key can never turn into another candidate after a reorder.
        helper, plan = await self._new_plan()
        native_result = None
        try:
            prepare = helper.prepare_dequeue if kind == "dequeue" else helper.prepare_enqueue
            await prepare(plan, index=index, expected_key=key, list_expression=source,
                **({"with_unlocks": True} if with_unlocks else {}))
            if kind == "start" and plan["before"]["current_after"]["present"]:
                raise RuntimeError("research became active before selection; no start command was submitted")
            if not plan["validation"].validated:
                raise ValueError(key + ": " + self._reason(plan["validation"]))
            submitted = await helper.submit_prepared(plan, **({"dequeue": True} if kind == "dequeue" else {"enqueue": True}))
            if submitted is None:
                raise RuntimeError("research queue changed before submission; no command was submitted")
            native_result = submitted
            native_outcome = getattr(getattr(plan.get("owner"), "attempt", None), "outcome", None)
            cleanup = plan.setdefault("cleanup", {})
            known_receiver = not submitted.error and native_outcome == "consumed"

            def native_answer(accepted, reason, observation_error=None):
                owner = plan.get("owner")
                attempt = getattr(owner, "attempt", None)
                query = getattr(owner, "pending", None)
                unresolved = getattr(attempt, "outcome", None) in ("pending", "unknown")
                if query is None and unresolved:
                    query = getattr(attempt, "query", None)
                pending = ({"query": query, "attempt": attempt,
                    "result": getattr(attempt, "result", None)} if query is not None or unresolved else None)
                answer = {"key": key, "status": "native_result", "submission_attempted": True,
                    "submitted": True if known_receiver else None, "accepted": accepted,
                    "native_result": submitted, "native_error": submitted.error,
                    "native_outcome": native_outcome, "before": plan.get("before"),
                    "submit_before": plan.get("submit_before"), "pending": pending,
                    "cleanup": cleanup, "is_current": None, "queue": None,
                    "queue_changed": None, "target_selection_changed": None,
                    "reason": reason, "atomic_snapshot": False}
                if observation_error is not None:
                    answer["observation_error"] = observation_error
                return answer

            if not known_receiver:
                return native_answer(None,
                    "Native submit returned error " + str(submitted.error) + " with ownership outcome "
                    + str(native_outcome) + "; receiver acceptance and post-state are not confirmed")
            if not submitted.accepted:
                return native_answer(False, "Native research queue receiver returned accepted=0; post-state was not queried")
            try:
                after = await helper.observe_result(plan)
                is_current = plan["target_is_current_after"]
                answer = {"key": key, "accepted": True, "submitted": True,
                    "status": "current" if is_current else "removed" if kind == "dequeue" and key not in after["keys"] else "queued" if key in after["keys"] else "accepted_not_observed",
                    "is_current": is_current, "queue": self._queue_public(after),
                    "queue_changed": plan["queue_changed"],
                    "target_selection_changed": plan["target_selection_changed"],
                    "atomic_snapshot": False, "native_result": submitted,
                    "native_error": submitted.error, "native_outcome": native_outcome, "cleanup": cleanup}
            except Exception as error:
                return native_answer(True, "Native receiver accepted; post-state observation did not complete: "
                    + str(error), error)
            return answer
        finally:
            if native_result is None:
                await self._cleanup(helper, plan)
            else:
                try:
                    await self._cleanup(helper, plan)
                except Exception as error:
                    cleanup = plan.setdefault("cleanup", {})
                    cleanup.setdefault("errors", []).append(error)
                    cleanup.update(status="failed")
