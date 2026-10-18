"""Victoria 3 building construction in game terms, on the supplied session.

Native CReader resolves building keys. No file-order definition IDs or synthetic
BuildingType objects are used. A successful submit is admission, not completion.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
import struct
from types import SimpleNamespace

from .. import v3_commands as commands, v3_data as data, v3_objects as objects
from ._values import fixed_decimal
from .contracts import ConstructionQueueObservation, ConstructionIntentResult, OperationResult
from .country import _OBJECT_PAGE_ROWS
from ._projection import (_projection_supported, _projection_page, _projection_value,
                          _projection_source_pages)
from ._run import command_field, rejected
from . import _run

_QUEUE = 12490
_QUEUE_SOURCE = "Country.AccessGovernmentConstructionQueue"
_MISSING = object()


def _id(value, label):
    if isinstance(value, dict):
        value = value.get("id", value.get("state_id"))
    if type(value) is not int or not 0 <= value < 0xffffffff:
        raise ValueError(f"{label} must be a complete game ID")
    return value


def _levels(value):
    if type(value) is not int or not 1 <= value <= 0x7fffffff:
        raise ValueError("levels must be a positive integer")
    return value


def _key(value):
    if isinstance(value, dict):
        value = value.get("building_key", value.get("key"))
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("building must be a building key, such as building_iron_mine")
    return value


def command_field(block, token, default=_MISSING):
    if isinstance(block, commands.Assignment):
        block = block.value
    values = [item.value for item in block if isinstance(item, commands.Assignment)
              and item.key == commands.Token(token)]
    if len(values) == 1:
        return values[0]
    if not values and default is not _MISSING:
        return default
    raise RuntimeError(f"native construction command field {token} is missing or ambiguous")


def _number(value):
    if isinstance(value, data.FixedPoint):
        return fixed_decimal(value)
    if type(value) in (int, float, bool) or isinstance(value, str) or value is None:
        return value
    return None


class ConstructionAPI:
    """Read candidates and government orders, estimate, enqueue and cancel."""

    def __init__(self, ctx):
        self._ctx = ctx
        self._metadata = {}
        self._metadata_world = None
        self._operations = []
        self._source_failures = {}

    def _adapter(self, role):
        return self._ctx.adapters[role]

    def _record(self, operation):
        # Retain exact native owners even when a transport/completion is uncertain.
        self._operations.append(operation)
        self._ctx.diagnostics.append({"domain": "construction", "operation": operation})

    async def _query(self, label, factory, owner=None):
        return await self._ctx.query("construction." + label, factory, owner=owner)

    @staticmethod
    def _ok(result, label, *, truncated=False):
        if result.error and not (truncated and result.error == 122):
            raise RuntimeError(f"{label}: native error {result.error}")
        return result

    async def _functions(self, name):
        if self._metadata_world != self._ctx.world:
            self._metadata.clear()
            self._metadata_world = self._ctx.world
        if name not in self._metadata:
            type_id = await self._ctx.type_id(name)
            rows, start = [], 0
            while True:
                result = self._ok(await self._query("metadata." + name,
                    lambda start=start: data.enumerate_functions(self._ctx.native,
                        type_id, start=start, capacity=256, adapter=self._adapter("data"),
                        process=self._ctx.process)), "construction metadata")
                rows.extend(result.rows)
                if result.next >= result.total:
                    break
                if result.next <= start:
                    raise RuntimeError("native construction metadata did not advance")
                start = result.next
            self._metadata[name] = {row.name: row for row in rows}
        return self._metadata[name]

    async def _release(self, result):
        if getattr(result, "value_handle", 0):
            released = self._ok(await self._query("release", lambda: data.release(
                self._ctx.native, result.value_handle, adapter=self._adapter("data"),
                process=self._ctx.process)), "release construction value")
            if released.value_handle:
                raise RuntimeError("native construction value has not been released")

    async def _evaluate(self, expression, bindings, output_bytes=65536):
        return await self._query(expression, lambda: data.evaluate(self._ctx.native,
            expression, bindings, output_bytes=output_bytes, adapter=self._adapter("data"),
            process=self._ctx.process))

    async def _value(self, name, getter, bindings):
        functions = await self._functions(name)
        row = functions.get(getter)
        if row is None or row.argument_count != 0 or any(row.callbacks[2:]):
            return None, "native zero-argument getter is unavailable"
        result = await self._evaluate(name + "." + getter, bindings)
        try:
            if result.error:
                return None, f"native getter error {result.error}"
            value = _number(result.value)
            if value is None and result.value is not None:
                return None, "native result is not a copied scalar or text value"
            return value, None
        finally:
            await self._release(result)

    async def _binding(self, name, full_id):
        return data.ObjectBinding.full_id(name, await self._ctx.type_id(name), full_id)

    async def _objects(self, domain):
        rows, start, world = [], 0, self._ctx.world
        while True:
            result = self._ok(await self._query("objects." + domain.name,
                lambda start=start: objects.enumerate_objects(self._ctx.native, domain,
                    start_index=start, max_items=_OBJECT_PAGE_ROWS, adapter=self._adapter("objects"),
                    process=self._ctx.process)), "construction object list")
            if result.world != world:
                self._ctx.diagnostics.append({"domain": "construction", "event": "world_changed",
                    "previous_world": world, "world": result.world,
                    "reason": "Separate native object observations; copied rows are preserved."})
                world = result.world
            rows.extend(row for row in result.records if row.valid)
            if result.next_index >= result.slot_bound:
                return rows
            if result.next_index <= start:
                raise RuntimeError("native construction object list did not advance")
            start = result.next_index

    async def _generated_key(self, building, binding):
        """Observe a native key through a generation-only original command.

        Native shared source -> native unique clone -> Serialize. The unique
        clone is destroyed, never submitted. No borrowed wrapper is rebound.
        """
        generated = await self._evaluate("Building.QueueConstruction", (binding,))
        owner, carrier_result = None, None
        clone_started = clone_finished = False
        operation = {"kind": "candidate_key", "owner": None, "submitted": False}
        self._record(operation)
        operation["source"] = generated
        try:
            self._ok(generated, "construction candidate key")
            if not isinstance(generated.value, data.NativeDataCommand):
                raise RuntimeError("native Building did not produce a construction command")
            carrier_result = self._ok(await self._query("candidate.carrier", lambda: data.command_value(
                self._ctx.native, generated.value_handle, adapter=self._adapter("data"),
                process=self._ctx.process)), "construction candidate command")
            carrier = carrier_result.value
            if carrier.generation_world != self._ctx.world:
                raise RuntimeError("native construction candidate source changed world")
            clone_started = True
            cloned = await self._query("candidate.clone", lambda: commands.request(
                self._ctx.native, commands.Action.CLONE, command=carrier.primary_pointer,
                world=carrier.generation_world, capacity=8, adapter=self._adapter("commands"),
                process=self._ctx.process))
            operation["clone"] = cloned
            self._ok(cloned, "construction candidate clone")
            clone_finished = True
            owner = commands.NativeCommand.from_result(self._ctx.native, cloned,
                adapter=self._adapter("commands"), process=self._ctx.process)
            operation["owner"] = owner
            if owner.world != carrier.generation_world or owner.type_token != _QUEUE:
                raise RuntimeError("native Building producer returned a different command")
            serialized = self._ok(await self._query("candidate.serialize", owner.serialize, owner),
                                  "construction candidate key")
            tree = commands.decode_command(serialized.data)
            body = command_field(tree, 65)
            if (tree.key != commands.Token(_QUEUE) or commands.encode_tree(tree) != serialized.data
                    or command_field(body, 439) != commands.UInt32(building.state_id)):
                raise RuntimeError("native Building candidate command identifies another state")
            return _key(command_field(body, 10232))
        finally:
            if owner is not None:
                await self._cleanup(owner)
            # command_value retains the same Any handle; release its source once.
            # An uncertain Clone still borrows the source; preserve its exact Any.
            if not clone_started or clone_finished:
                await self._release(generated)

    async def _type_details(self, building):
        """Copy native VM chains; never rebind a borrowed BuildingType pointer."""
        binding = await self._binding("Building", building.resolved_id)
        return await self._bound_type_details(building, binding)

    async def _bound_type_details(self, building, binding, *, seen=()):
        # Only skip details for an already-emitted key in this observation.
        # Unknown keys still take the full native fallback path below.
        details = {"type_pointer": building.type_pointer}
        try:
            functions = await self._functions("BuildingType")
        except LookupError:
            functions = {}
        for field, getter in (("building_key", "GetKey"), ("name", "GetNameNoFormatting"),
                ("required_construction", "GetRequiredConstruction"),
                ("construction_costs_description", "GetConstructionCosts")):
            row = functions.get(getter)
            if row is None or row.argument_count != 0 or any(row.callbacks[2:]):
                details[field] = None
                details[field + "_reason"] = "native zero-argument getter is unavailable"
                continue
            result = await self._evaluate("Building.GetBuildingType." + getter, (binding,))
            try:
                details[field] = None if result.error else _number(result.value)
                if result.error:
                    details[field + "_reason"] = f"native getter error {result.error}"
            finally:
                await self._release(result)
            if (field == "building_key" and isinstance(details[field], str)
                    and details[field] and (building.state_id, details[field]) in seen):
                return details
        if not isinstance(details["building_key"], str) or not details["building_key"]:
            try:
                details["building_key"] = await self._generated_key(building, binding)
                details.pop("building_key_reason", None)
            except (RuntimeError, ValueError) as error:
                details["building_key"] = None
                details["building_key_reason"] = str(error)
        if details["name"] is None:
            details["name"], details["name_reason"] = await self._value(
                "Building", "GetNameNoFormatting", (binding,))
        # A queue/eligibility tooltip is not a construction cost. Render it only
        # through the original Building context, never as this optional fallback.
        return details

    async def _known_types(self, buildings):
        details = {}
        for row in buildings:
            if row.type_pointer and row.type_pointer not in details:
                details[row.type_pointer] = await self._type_details(row)
        return details

    async def _cleanup(self, owner):
        if owner.command and owner.pending is None and (
                owner.attempt is None or owner.attempt.outcome == "owned"):
            result = self._ok(await self._query("destroy", owner.destroy, owner),
                              "release construction command")
            if result.command or owner.command:
                raise RuntimeError("native construction command remains owned")

    @staticmethod
    def _explanation(operation):
        validation = operation["validation"]
        raw = validation.reason if operation.get("details") else None
        reason = raw or (None if validation.validated else "Native construction rules rejected this request.")
        result = {"reason": reason, "reason_complete": False,
            "reason_missing_context": "The native nested-localization context for the full construction explanation is not available.",
            "reason_details": {"eligible": bool(validation.validated),
                "state_id": operation.get("state_id"), "building_key": operation.get("building_key"),
                "levels_requested": operation.get("levels")}}
        if operation.get("details"):
            result["reason_raw"] = validation.reason or None
        return result

    def _option_validation(self, operation, state, building_key, *, requested, key_reason=None):
        if operation is not None:
            return {"eligible": bool(operation["validation"].validated),
                "validation_status": "validated", **self._explanation(operation)}
        reason = (key_reason if requested else
            "Native eligibility validation was not requested; use estimate(state, building) for a selected candidate or options(validate=True).")
        return {"eligible": None,
            "validation_status": "unavailable" if requested else "not_requested",
            "reason": reason or "The actual Building key is unavailable.",
            "reason_complete": False,
            "reason_missing_context": "Native eligibility has not been evaluated for this candidate.",
            "reason_details": {"eligible": None, "state_id": state,
                "building_key": building_key, "levels_requested": 1}}

    async def _prepare(self, state, building, levels, actor, *, details=False):
        token = _QUEUE
        parameters = {439: commands.UInt32(state), 10232: building,
            219: {10304: commands.UInt32(actor)}, 22805: levels, 22308: commands.Token(603)}
        operation = {"kind": "build", "owner": None,
            "state_id": state, "building_key": building, "levels": levels, "submitted": False,
            "details": details}
        self._record(operation)
        created = await self._query("create", lambda: commands.create(self._ctx.native,
            token, parameters=parameters, actor_id=actor, adapter=self._adapter("commands"),
            process=self._ctx.process))
        operation["created"] = created
        self._ok(created, "prepare construction")
        owner = commands.NativeCommand.from_result(self._ctx.native, created,
            adapter=self._adapter("commands"), process=self._ctx.process)
        operation["owner"] = owner
        try:
            if owner.world != self._ctx.world or owner.type_token != token:
                raise RuntimeError("native construction command belongs to a different world or type")
            serialized = self._ok(await self._query("serialize", owner.serialize, owner),
                                  "construction command")
            operation["serialized"] = serialized
            tree = commands.decode_command(serialized.data)
            body = command_field(tree, 65)
            if (commands.encode_tree(tree) != serialized.data or tree.key != commands.Token(token)
                    or command_field(tree, 10304) != commands.UInt32(actor)
                    or command_field(body, 439) != commands.UInt32(state)
                    or command_field(body, 10232) != building):
                raise ValueError("native command did not resolve the requested state/building key")
            if (command_field(body, 22805) != levels or command_field(body, 22308) != commands.Token(603)
                    or command_field(command_field(body, 219), 10304) != commands.UInt32(actor)):
                raise RuntimeError("native construction quantity, placement or actor differs")
            # Proved CQueueConstruction primary +0x30: the
            # CReader-resolved real CBuildingType*, not a fabricated key index.
            raw = await self._ctx.blocking(self._ctx.process.read, owner.command + 0x30, 8)
            if len(raw) != 8 or not (type_pointer := struct.unpack("<Q", raw)[0]):
                raise ValueError("native reader could not resolve the building key")
            operation["type_pointer"] = type_pointer
            validated = self._ok(await self._query("validate",
                lambda: owner.validate(capacity=65536 if details else 0), owner),
                "construction validation", truncated=details)
            operation["validation"] = validated
            return operation
        except BaseException:
            await self._cleanup(owner)
            raise


    async def _potential_options(self, state, actor, seen, details=False, *, validate=False, completed=None,
                                 selected=None):
        """Use the game's real construction menus, including potential buildings.

        These lists can publish actual owned CDataBuilding contexts without a
        persistent Building ID. Their original command producer supplies the key.
        """
        functions = await self._functions("State")
        state_binding = await self._binding("State", state)
        building_type = None
        result = []
        for getter in ("GetPotentialUrbanBuildings", "AccessCappedRuralBuildings",
                "AccessUncappedRuralBuildings", "AccessDevelopmentBuildings", "AccessCoastalBuildings"):
            row = functions.get(getter)
            if row is None or row.argument_count != 0 or any(row.callbacks[2:]):
                continue
            if selected is not None and getter not in selected:
                continue
            source_expression = "State." + getter
            source_key = self._source_context(actor, state, source_expression, "owned collection")
            if source_key in self._source_failures:
                continue
            source = await self._evaluate(source_expression, (state_binding,))
            try:
                if source.error or not isinstance(source.value, data.NativeList) or not source.value_handle:
                    reason = "native source did not provide a retained collection"
                    if source.error in (13, 50) or not source.error:
                        self._source_failures[source_key] = reason
                    self._ctx.diagnostics.append({"construction_candidate_source": getter,
                        "state_id": state, "native_error": source.error, "reason": reason})
                    continue
                if selected is None:
                    count = await self._query("candidates.count", lambda: data.list_count(
                        self._ctx.native, source.value_handle, adapter=self._adapter("data"),
                        process=self._ctx.process))
                    if count.error:
                        self._ctx.diagnostics.append({"construction_candidate_source": getter,
                            "state_id": state, "native_error": count.error})
                        continue
                    indices = range(count.total)
                else:
                    indices = selected[getter]
                complete = True
                for index in indices:
                    selected_item = await self._query("candidates.item", lambda index=index: data.list_element(
                        self._ctx.native, source.value_handle, index, adapter=self._adapter("data"),
                        process=self._ctx.process))
                    try:
                        if selected_item.error:
                            complete = False
                            self._ctx.diagnostics.append({"construction_candidate_source": getter,
                                "state_id": state, "native_error": selected_item.error})
                            if selected_item.error in (13, 50):
                                self._source_failures[source_key] = "native candidate requires same-callback BindingScope"
                                break
                            continue
                        value = selected_item.value
                        if (not isinstance(value, data.NativeObject) or "CPdxPtr" not in value.native_type_name
                                or "CDataBuilding@@" not in value.native_type_name):
                            complete = False
                            reason = "native candidate requires same-callback BindingScope"
                            self._source_failures[source_key] = reason
                            self._ctx.diagnostics.append({"construction_candidate_source": getter, "reason": reason})
                            break
                        if building_type is None:
                            building_type = await self._ctx.type_id("Building")
                        binding = data.ObjectBinding(building_type, selected_item.value_handle, retained=True)
                        candidate = SimpleNamespace(state_id=state, type_pointer=None)
                        detail = await self._bound_type_details(candidate, binding, seen=seen)
                        building_key = detail.get("building_key")
                        if building_key is None:
                            complete = False
                        if building_key is not None and (state, building_key) in seen:
                            continue
                        operation = None
                        try:
                            if building_key is not None:
                                if validate:
                                    operation = await self._prepare(state, building_key, 1, actor, details=details)
                                seen.add((state, building_key))
                            existing, existing_reason = await self._value("Building", "GetTotalLevels", (binding,))
                            maximum, max_reason = await self._value("Building", "GetMaxLevels", (binding,))
                            result.append({"state_id": state,
                                **{k: v for k, v in detail.items() if k != "type_pointer"},
                                "existing_levels": existing, "existing_levels_reason": existing_reason,
                                "maximum_levels": maximum, "maximum_levels_reason": max_reason,
                                **self._option_validation(operation, state, building_key,
                                    requested=validate, key_reason=detail.get("building_key_reason")),
                                "candidate_source": "native state construction menu: " + getter})
                        finally:
                            if operation is not None:
                                await self._cleanup(operation["owner"])
                    finally:
                        await self._release(selected_item)
                if complete and completed is not None:
                    completed.add((state, source_expression))
            finally:
                await self._release(source)
        return result

    def _source_context(self, actor, state, source, expression):
        # Failures belong to the real source context, not an installation-wide
        # blacklist. A world, country, State or adapter change permits recovery.
        return (self._ctx.world, self._adapter("data"), actor, state, source, expression)

    async def _source_element(self, source, index, expression, bindings, *, actor,
                              state=None):
        key = self._source_context(actor, state, source, expression)
        cached = self._source_failures.get(key)
        if cached is not None:
            return None, cached, None
        world = self._ctx.world
        result = await self._query("source." + expression, lambda: data.evaluate_element(
            self._ctx.native, source, index, expression, bindings, world=world,
            output_bytes=65536, adapter=self._adapter("data"),
            process=self._ctx.process))
        if result.error:
            reason = f"native source-context getter error {result.error}"
            # Invalid syntax/type promotion is unchanged within this context.
            # Busy/not-ready/source-count changes remain recoverable observations.
            if result.error in (13, 50):
                self._source_failures[key] = reason
            return None, reason, result
        return _number(result.value), None, result

    async def _all_type_options(self, states, actor, seen, details=False, *, validate=False, completed=()):
        """Discover potential candidates from genuine State menu contexts.

        BuildingBrowserPanel.Self is not a global root. It is never evaluated.
        Original State providers supply their own native BindingScope inside each
        callback, including providers whose elements cannot safely escape as Any.
        """
        result = []
        functions = await self._functions("State")
        for state in states:
            bindings = None
            for getter in ("GetPotentialUrbanBuildings", "AccessCappedRuralBuildings",
                    "AccessUncappedRuralBuildings", "AccessDevelopmentBuildings", "AccessCoastalBuildings"):
                row = functions.get(getter)
                if row is None or row.argument_count != 0 or any(row.callbacks[2:]):
                    continue
                source = "State." + getter
                if (state, source) in completed:
                    continue
                if bindings is None:
                    bindings = (await self._binding("State", state),)
                _, reason, first = await self._source_element(source, 0, "Building.GetTotalLevels",
                    bindings, actor=actor, state=state)
                if first is None or first.error or first.source_count <= 0:
                    if reason:
                        self._ctx.diagnostics.append({"construction_candidate_source": source,
                            "state_id": state, "native_error": getattr(first, "error", None), "reason": reason})
                    continue
                count = first.source_count
                for index in range(count):
                    building_key, key_reason, _ = await self._source_element(source, index,
                        "Building.GetBuildingType.GetKey", bindings, actor=actor, state=state)
                    if not isinstance(building_key, str) or not building_key or (state, building_key) in seen:
                        continue
                    detail = {"building_key": building_key}
                    for field, expression in (("name", "Building.GetNameNoFormatting"),
                            ("required_construction", "Building.GetBuildingType.GetRequiredConstruction"),
                            ("existing_levels", "Building.GetTotalLevels"),
                            ("maximum_levels", "Building.GetMaxLevels")):
                        detail[field], field_reason, _ = await self._source_element(source, index,
                            expression, bindings, actor=actor, state=state)
                        if field_reason:
                            detail[field + "_reason"] = field_reason
                    operation = (await self._prepare(state, building_key, 1, actor, details=details)
                                 if validate else None)
                    try:
                        result.append({"state_id": state, **detail,
                            **self._option_validation(operation, state, building_key, requested=validate),
                            "candidate_source": "native state construction menu: " + getter})
                        seen.add((state, building_key))
                    finally:
                        if operation is not None:
                            await self._cleanup(operation["owner"])
        return result

    async def options(self, *, state, details=False, validate=False):
        """Read the selected State's native building menus, not World buildings.

        Potential menu entries need no persistent Building instance. Discovery
        does not validate a command for every candidate unless requested.
        Use estimate(state, building) for one selection, then build() to enqueue.
        """
        state = _id(state, "state")
        return await self._ctx.perform("construction.options", lambda: self._options(state, details, validate))

    @staticmethod
    def _copied_detail(cells, fields, *, raw=False, page_error=0):
        detail = {}
        for field, index in fields:
            cell = cells.get(index)
            value, reason = _projection_value(cell)
            detail[field] = _number(value)
            if raw and reason and cell is not None and cell.error:
                reason = reason.replace("native getter error", "native source-context getter error")
            if page_error:
                reason = "; ".join(filter(None, (reason, f"native projection error {page_error}")))
            if reason:
                detail[field + "_reason"] = reason
        return detail


    async def _project_candidates(self, states, actor, seen, details, *, validate):
        # Both names are copied while the provider's actual Binding is in scope.
        # Its observed mode preserves the historical owned/raw name and order.
        fields = ("Building.GetBuildingType.GetKey", "Building.GetBuildingType.GetNameNoFormatting",
            "Building.GetBuildingType.GetRequiredConstruction", "Building.GetBuildingType.GetConstructionCosts",
            "Building.GetTotalLevels", "Building.GetMaxLevels", "Building.GetNameNoFormatting")
        functions = await self._functions("State")
        getters = [getter for getter in ("GetPotentialUrbanBuildings", "AccessCappedRuralBuildings",
            "AccessUncappedRuralBuildings", "AccessDevelopmentBuildings", "AccessCoastalBuildings")
            if (getter in functions and functions[getter].argument_count == 0
                and not any(functions[getter].callbacks[2:]))]
        state_type = await self._ctx.type_id("State")
        owned, raw = [], []
        for state in states:
            binding = data.ObjectBinding.state(state_type, state)
            owned_passes = {}
            sources = tuple("State." + getter for getter in getters)
            async for source_index, observation, page, source_cells in _projection_source_pages(
                    self._ctx, "construction.project", fields, ((binding,),), sources):
                getter, source = getters[source_index], sources[source_index]
                owned_pass = owned_passes.get(source_index, True)
                source_error = observation.error or page.error
                copied = {}
                for cell in source_cells:
                    copied.setdefault(cell.row, {})[cell.field] = cell
                for index in sorted(set(range(observation.start, observation.next)) | copied.keys()):
                    cells = copied.get(index, {})
                    provider = next(iter(cells.values()), None)
                    mode = getattr(provider, "binding_mode", 0xffffffff)
                    binding_kind = getattr(provider, "binding_kind", 0xffffffff)
                    binding_name = getattr(provider, "binding_native_type_name", None)
                    binding_error = getattr(provider, "binding_error", 0)
                    descriptor_known = not binding_error and (mode in (0, 1) or
                        (binding_kind != 0xffffffff and binding_name is not None))
                    owned_shape = (mode in (2, 3) and binding_kind == data.Kind.OBJECT
                        and isinstance(binding_name, str)
                        and "CPdxPtr" in binding_name and "CDataBuilding@@" in binding_name)
                    if ((descriptor_known and not owned_shape) or
                            (mode == 0xffffffff and any(cell.error in (13, 50) for cell in cells.values()))):
                        owned_pass = False
                    is_owned = owned_pass and descriptor_known and owned_shape
                    detail = self._copied_detail(cells, (("building_key", 0),
                        ("name", 1 if is_owned else 6), ("required_construction", 2),
                        ("existing_levels", 4), ("maximum_levels", 5)),
                        raw=not is_owned, page_error=source_error)
                    if is_owned:
                        detail.update(self._copied_detail(cells, (("construction_costs_description", 3),),
                            page_error=source_error))
                        detail.setdefault("existing_levels_reason", None)
                        detail.setdefault("maximum_levels_reason", None)
                        if detail["name"] is None:
                            fallback = self._copied_detail(cells, (("name", 6),), page_error=source_error)
                            detail.update(fallback)
                            detail["name_reason"] = fallback.get("name_reason")
                    else:
                        # The batch now copies this real text for raw providers
                        # too. Add it without changing any former raw field.
                        detail.update(self._copied_detail(cells, (("construction_costs_description", 3),),
                            raw=True, page_error=source_error))
                    item = {"state_id": state, **detail,
                        "candidate_source": "native state construction menu: " + getter}
                    if not descriptor_known:
                        # Unknown provider metadata never erases actual fields.
                        # Publish both observed names rather than asserting that
                        # the legacy owned/raw display choice was established.
                        item.update(self._copied_detail(cells, (("building_type_name", 1),), page_error=source_error))
                        item["provider_observation"] = {"binding_mode": mode, "binding_kind": binding_kind,
                            "binding_native_type_name": binding_name, "native_error": binding_error}
                    if (not observation.count_valid or source_error or
                            (observation is not page and not observation.range_complete)):
                        item["source_observation"] = {"count": observation.source_count,
                            "count_valid": observation.count_valid, "start": observation.start,
                            "row_count": observation.row_count, "native_error": observation.error}
                        if observation is not page:
                            item["source_observation"].update(source_produced=observation.source_produced,
                                range_complete=observation.range_complete)
                        if page.error and observation is not page:
                            item["source_observation"]["projection_error"] = page.error
                    if mode == 0xffffffff:
                        item["source_index"] = index
                        item["projection_reason"] = "Native provider row selection did not supply a Binding mode."
                    if is_owned and (not isinstance(detail["building_key"], str) or not detail["building_key"]):
                        # Read a complete original candidate for genuine key
                        # recovery, not a new key grafted onto the first scope.
                        self._ctx.diagnostics.append({"construction_candidate_source": source,
                            "state_id": state, "source_index": index, "projection_page": page})
                        recovered = await self._potential_options(state, actor, set(), details,
                            selected={getter: (index,)})
                        if recovered:
                            item = recovered[0]
                    (owned if is_owned else raw).append(item)
                owned_passes[source_index] = owned_pass
        result = []
        for item in owned + raw:
            state, building_key = item["state_id"], item.get("building_key")
            known_key = isinstance(building_key, str) and bool(building_key)
            if known_key and (state, building_key) in seen:
                continue
            operation = None
            try:
                if known_key:
                    if validate:
                        operation = await self._prepare(state, building_key, 1, actor, details=details)
                    seen.add((state, building_key))
                item.update(self._option_validation(operation, state, building_key,
                    requested=validate, key_reason=item.get("building_key_reason")))
                result.append(item)
            finally:
                if operation is not None:
                    await self._cleanup(operation["owner"])
        return result

    async def _options(self, state, details=False, validate=False):
        await self._ctx.refresh()
        actor = await self._ctx.country_id()
        seen = set()
        if await _projection_supported(self._ctx):
            options = await self._project_candidates([state], actor, seen, details, validate=validate)
        else:
            completed = set()
            options = await self._potential_options(state, actor, seen, details,
                validate=validate, completed=completed)
            options.extend(await self._all_type_options([state], actor, seen, details,
                validate=validate, completed=completed))
        for item in options:
            observed = item.setdefault("reason_details", {})
            for name in ("existing_levels", "maximum_levels", "required_construction"):
                observed[name] = item.get(name)
        return options

    async def costs(self):
        """Current national building-construction goods expenses, not project totals."""
        async def work():
            await self._ctx.refresh()
            return await self._costs(await self._ctx.country_id())
        return await self._ctx.perform("construction.costs", work)

    async def _costs(self, actor, *, run=None):
        binding = await run.country() if run is not None else await self._binding("Country", actor)
        result = {"country_id": actor, "scope": "current national building construction",
            "units": "pounds/week", "sampling": "separate native observations"}
        for field, getter in (("weekly_government_goods_expense", "GetGovernmentConstructionGoodsExpenses"),
                ("weekly_private_goods_expense", "GetPrivateConstructionGoodsExpenses"),
                ("weekly_total_goods_expense", "GetConstructionGoodsExpenses"),
                ("government_goods_description", "GetGovernmentConstructionGoodsExpensesByGoodsDesc"),
                ("private_goods_description", "GetPrivateConstructionGoodsExpensesByGoodsDesc")):
            if run is None:
                result[field], reason = await self._value("Country", getter, (binding,))
                if reason:
                    result[field + "_reason"] = reason
            else:
                native = await run.capture("costs." + getter, lambda getter=getter: data.evaluate(
                    run.native, "Country." + getter, (binding,), output_bytes=65536,
                    adapter=run.adapters["data"], process=run.process))
                copied = self._queue_field(native)
                result[field] = copied["value"] if copied["complete"] else None
                result.setdefault("native_fields", {})[field] = copied
                if native.error:
                    result[field + "_reason"] = "native getter error " + str(native.error)
        return result

    async def _sector_inputs(self, building):
        """Reuse the shared native Building IO composition for one real sector."""
        world = self._ctx.world
        io = await self._ctx.domain("economy")._building_io(building.resolved_id, world)
        result = {"building_id": building.resolved_id, "state_id": building.state_id,
            "building_key": "building_construction_sector", "levels": building.levels,
            "inputs": [], "complete": io.get("inputs") is not None,
            "has_input_shortage": None, "input_shortage_penalty": None}
        if io.get("reasons"):
            result["reasons"] = dict(io["reasons"])
            if any(key in io["reasons"] for key in ("inputs", "inputs_changes", "building", "world")):
                result["complete"] = False
        for row in io.get("inputs") or ():
            result["inputs"].append({"key": row["key"], "name": row["name"],
                "quantity": row["consumption"], "value": row["consumption_value"],
                "units": "goods orders", "value_units": "pounds",
                "quantity_scope": "current native sector consumption", "reasons": row.get("reasons", {})})
            if row["key"] is None or row["consumption"] is None:
                result["complete"] = False
        if self._ctx.world != world:
            result["complete"] = False
            result.setdefault("reasons", {})["world"] = "the world changed during construction input observation"
            return result
        binding = await self._binding("Building", building.resolved_id)
        for field, getter in (("has_input_shortage", "HasAnyInputShortage"),
                ("input_shortage_penalty", "GetInputShortagePenalty")):
            result[field], reason = await self._value("Building", getter, (binding,))
            if reason:
                result.setdefault("reasons", {})[field] = reason
        return result

    async def inputs(self, *, market=True):
        """Current construction-sector inputs and their actual national market.

        These are the sectors' current operating inputs, not the target
        building's production inputs or a fixed material bill per new level.
        A positive market order gap is not a native input-shortage penalty.
        """
        return await self._ctx.perform("construction.inputs", lambda: self._inputs(market))

    async def _inputs(self, market):
        await self._ctx.refresh()
        actor = await self._ctx.country_id()
        owned = {row.resolved_id for row in await self._objects(objects.Domain.STATE) if row.owner_id == actor}
        buildings = [row for row in await self._objects(objects.Domain.BUILDING)
                     if row.state_id in owned and row.levels > 0]
        known = await self._known_types(buildings)
        sectors, missing_keys = [], False
        for building in buildings:
            detail = known.get(building.type_pointer, {})
            if detail.get("building_key") is None:
                missing_keys = True
            elif detail["building_key"] == "building_construction_sector":
                sectors.append(await self._sector_inputs(building))
        aggregate = {}
        for sector in sectors:
            for item in sector["inputs"]:
                if not isinstance(item["key"], str) or not item["key"]:
                    continue
                if item["key"] not in aggregate:
                    aggregate[item["key"]] = {"key": item["key"], "name": item["name"],
                        "_quantities": [], "quantity": None, "units": "goods orders",
                        "market_price": None, "market_demand": None, "market_supply": None,
                        "market_order_gap": None, "native_shortage_penalty": None}
                good = aggregate[item["key"]]
                good["_quantities"].append(item["quantity"])
        for good in aggregate.values():
            quantities = good.pop("_quantities")
            if all(isinstance(value, Decimal) for value in quantities):
                # Each source is an exact signed64 fixed value. 50 digits also
                # cover every uint32-sized collection of such quantities.
                with localcontext() as context:
                    context.prec = 50
                    good["quantity"] = sum(quantities, Decimal(0))
        market_snapshot, market_reason = None, None
        if market and aggregate:
            market_snapshot = await self._ctx.domain("economy")._market()
            if market_snapshot.get("country_id") != actor or market_snapshot.get("id") is None:
                market_reason = "the actual country market is unavailable"
            else:
                for good in aggregate.values():
                    matches = [row for row in market_snapshot["goods"] if row["key"] == good["key"]]
                    if len(matches) != 1:
                        good["market_reason"] = "no unique current native Goods key match in this market"
                        continue
                    row = matches[0]
                    good.update(market_price=row.get("price"), market_demand=row.get("demand"),
                        market_supply=row.get("supply"), market_order_gap=row.get("shortage"))
        goods = list(aggregate.values())
        bottlenecks = [good for good in goods if good["market_order_gap"] is not None and good["market_order_gap"] > 0]
        complete = not missing_keys and all(sector["complete"] for sector in sectors)
        market_complete = bool(market and not market_reason and complete and
            all(good["market_order_gap"] is not None for good in goods))
        result = {"country_id": actor, "sectors": sectors, "goods": goods,
            "complete": complete, "market_complete": market_complete,
            "scope": "current construction-sector consumption, not per-project material totals",
            "market_id": market_snapshot.get("id") if market_snapshot else None,
            "market_bottlenecks": bottlenecks if market_complete else None,
            "bottleneck_definition": "current construction inputs with national buy orders greater than sell orders; not a native shortage penalty",
            "current_costs": await self._costs(actor)}
        if missing_keys:
            result["reason"] = "some domestic BuildingType keys could not be observed"
        if market_reason:
            result["market_reason"] = market_reason
        if not market:
            result["market_reason"] = "market observation was not requested"
        return result

    async def capacity(self):
        """Current building-construction use and native queue counts."""
        return await self._ctx.perform("construction.capacity", self._capacity)

    async def _capacity(self):
        await self._ctx.refresh()
        actor = await self._ctx.country_id()
        binding = await self._binding("Country", actor)
        result = {"country_id": actor, "units": "construction points", "sampling": "separate native observations"}
        for field, getter in (("base_speed", "CalcBaseConstructionSpeed"),
                ("used", "CalcUsedConstruction"), ("unused", "CalcUnusedConstruction"),
                ("government_used", "CalcUsedGovernmentConstruction"),
                ("private_used", "CalcUsedPrivateConstruction"),
                ("government_queue_count", "GetNumGovernmentConstructionsInQueue"),
                ("private_queue_count", "GetNumPrivateConstructionsInQueue"),
                ("construction_paused", "IsConstructionPaused")):
            result[field], reason = await self._value("Country", getter, (binding,))
            if reason:
                result[field + "_reason"] = reason
        return result

    @staticmethod
    def _queue_field(result):
        """Copy one original field without discarding its native prefix or error."""
        snapshot = result.snapshot
        copied = len(snapshot) == result.snapshot_bytes == result.total
        observed = {"native_result": result, "error": result.error, "kind": result.kind,
            "raw_bits": result.raw_bits, "snapshot": snapshot,
            "snapshot_bytes": result.snapshot_bytes, "total": result.total,
            "copy_complete": copied, "complete": False, "value": None}
        try:
            # A copied CString prefix is useful even when the full copy failed.
            # Invalid/incomplete UTF-8 remains in snapshot, never silently repaired.
            value = snapshot.decode("utf-8", "strict") if result.kind == data.Kind.STRING else result.value
            observed["value"] = _number(value)
            if value is not None and observed["value"] is None:
                observed["reason"] = "Native field is not a copied scalar or CString"
            observed["complete"] = (not result.error and copied and result.kind != data.Kind.EMPTY
                and "reason" not in observed)
        except Exception as error:
            observed["decode_error"] = error
        return observed

    async def queue(self, *, start=0, limit=None, details=False) -> OperationResult[ConstructionQueueObservation]:
        """Observe a selected range of the current government construction queue.

        Each row carries its transient native index, progress, speed, validity
        and original formatted names. ``source_position`` is the zero-based
        position accepted by cancel(position=...) for a NEW current selection.
        It does not identify this observation's order after the queue changes.
        details=True expands the same rows with original tooltip, display number,
        base speed, active-construction state and weeks left, without a World join.
        """
        if type(start) is not int or start < 0:
            raise ValueError("start must be a nonnegative source position")
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("limit must be None or a nonnegative item count")
        return await self._ctx.perform("construction.queue", lambda: self._queue(start, limit, details))

    async def _queue(self, start, limit, details=False):

        await self._ctx.refresh()
        run = _run.Run(self._ctx, "queue", namespace="construction")
        result = {"queue": "government", "country_id": None, "items": [],
            "source_expression": _QUEUE_SOURCE,
            "selection_scope": "current_unfiltered_government_source",
            "source_count": None, "requested_range": {"start": start, "limit": limit},
            "observed_range": {"start": start, "stop": start}, "next": None,
            "range_complete": False, "complete": False, "atomic_snapshot": False,
            "status": "observing", "source_result": None, "count_result": None,
            "work_scope": "Selected elements only; the native producer may refresh the whole source"}
        run.public_result = result
        retain_partial = False
        fields = (("index", "GetIndex"), ("progress", "CalcPercentComplete"),
            ("construction_speed", "GetConstructionSpeed"), ("valid_construction", "IsValidConstruction"),
            ("state_name", "GetBuilding.GetState.GetName"), ("building_name", "GetBuilding.GetName"))
        if details:
            fields += (("tooltip", "GetTooltip"), ("queue_number", "GetQueueNumber"),
                ("base_construction_speed", "GetBaseConstructionSpeed"),
                ("currently_under_construction", "IsCurrentlyUnderConstruction"),
                ("weeks_left", "GetWeeksLeft"))

        async def observe():
            nonlocal retain_partial
            country = await run.country()
            result["country_id"] = run.actor
            source = await run.capture("queue.source", lambda: data.evaluate(run.native,
                _QUEUE_SOURCE, (country,), output_bytes=65536,
                adapter=run.adapters["data"], process=run.process))
            result["source_result"] = source
            if source.value_handle and len(source.snapshot) != source.snapshot_bytes or (
                    source.value_handle and source.snapshot_bytes != source.total):
                retain_partial = True
            if source.error:
                result["status"] = "source_error"
                return
            if not isinstance(source.value, data.NativeList) or not source.value_handle:
                result["status"] = "source_unavailable"
                return
            count = await run.capture("queue.count", lambda: data.list_count(run.native,
                source.value_handle, adapter=run.adapters["data"], process=run.process))
            result["count_result"] = count
            if count.error or type(count.total) is not int or count.total < 0:
                result["status"] = "count_error"
                return
            result["source_count"] = count.total  # Elements, not copied byte length.
            stop = max(start, min(count.total, count.total if limit is None else start + limit))
            result["next"] = stop if stop < count.total else None
            for position in range(start, stop):
                row = {"source_position": position, "index": None, "progress": None,
                    "construction_speed": None, "valid_construction": None,
                    "state_name": None, "building_name": None, "fields": {}, "complete": False,
                    "progress_units": "fraction (0 to 1)", "text_format": "native_formatted",
                    "native_element": None}
                result["items"].append(row)
                selected = await run.capture("queue.element", lambda position=position: data.list_element(
                    run.native, source.value_handle, position, output_bytes=65536,
                    adapter=run.adapters["data"], process=run.process))
                row["native_element"] = selected
                result["observed_range"]["stop"] = position + 1
                if (selected.value_handle != source.value_handle and selected.value_handle
                        and not len(selected.snapshot) == selected.snapshot_bytes == selected.total):
                    retain_partial = True
                if selected.error:
                    row["reason"] = "Native queue element error " + str(selected.error)
                    continue
                try:
                    binding = run.owned_binding(selected, "ConstructionQueueElement")
                except RuntimeError as error:
                    row["reason"] = str(error)
                    continue
                for name, expression in fields:
                    native = await run.capture("queue." + name, lambda expression=expression: data.evaluate(
                        run.native, "ConstructionQueueElement." + expression, (binding,), output_bytes=65536,
                        adapter=run.adapters["data"], process=run.process))
                    observed = self._queue_field(native)
                    row["fields"][name] = observed
                    row[name] = observed["value"]
                    if native.value_handle and not observed["copy_complete"]:
                        retain_partial = True
                row["complete"] = all(field["complete"] for field in row["fields"].values())
            result["range_complete"] = True
            result["complete"] = all(row["complete"] for row in result["items"])
            result["status"] = "observed" if result["complete"] else "partial"

        try:
            await observe()
        except BaseException as error:
            result["error"] = error
            result["status"] = "original_error_graph_retained"
            # Use actual exception evidence without re-reading or replacing its
            # protocol fields. The same Run/pending query keeps the original graph.
            try:
                result["exception_native_result"] = getattr(error, "result", None)
                if not hasattr(error, "observed_result"):
                    error.observed_result = result
            except BaseException:
                pass
            raise
        if details:
            result["operations"] = run.operations
        if retain_partial:
            run.status = "partial_copy_retained"
            result["cleanup"] = {"status": run.status,
                "reason": "Original Any retained for a later snapshot, not getter replay"}
        else:
            try:
                await run.finish(result)
            except BaseException as error:
                try:
                    if not hasattr(error, "observed_result"):
                        error.observed_result = result
                except BaseException:
                    pass
                raise
        return result

    async def _fresh_candidate_details(self, state, building_key, actor, *, run):
        """Read this State's native menu, without world joins or key-generating commands."""
        functions = await self._functions("State")
        bindings = (await self._binding("State", state),)
        best = None
        specs = (("required_construction", "Building.GetBuildingType.GetRequiredConstruction"),
            ("name", "Building.GetNameNoFormatting"), ("existing_levels", "Building.GetTotalLevels"),
            ("maximum_levels", "Building.GetMaxLevels"),
            ("construction_costs_description", "Building.GetBuildingType.GetConstructionCosts"))

        async def owned_field(expression, binding):
            native = await run.capture("estimate." + expression, lambda: data.evaluate(run.native,
                expression, (binding,), output_bytes=65536,
                adapter=run.adapters["data"], process=run.process))
            copied = self._queue_field(native)
            return (copied["value"] if copied["complete"] else None), native

        async def scoped_field(source, index, expression):
            native = await run.capture("estimate.source." + expression, lambda: data.evaluate_element(
                run.native, source, index, expression, bindings, world=run.world,
                output_bytes=65536, adapter=run.adapters["data"], process=run.process))
            return (None if native.error else _number(native.value)), native

        for getter in ("GetPotentialUrbanBuildings", "AccessDevelopmentBuildings",
                "AccessCappedRuralBuildings", "AccessUncappedRuralBuildings", "AccessCoastalBuildings"):
            member = functions.get(getter)
            if member is None or member.argument_count or any(member.callbacks[2:]):
                continue
            expression = "State." + getter
            source = await run.capture("estimate.source", lambda expression=expression: data.evaluate(
                run.native, expression, bindings, output_bytes=65536,
                adapter=run.adapters["data"], process=run.process))
            count_value, owned_complete = None, False
            if source.error not in (0, 13, 50):
                return best
            if (source.value_handle and not len(source.snapshot) == source.snapshot_bytes == source.total):
                return best
            if not source.error and isinstance(source.value, data.NativeList) and source.value_handle:
                count = await run.capture("estimate.count", lambda: data.list_count(run.native,
                    source.value_handle, adapter=run.adapters["data"], process=run.process))
                if not count.error and type(count.total) is int and count.total >= 0:
                    count_value, owned_complete = count.total, True
                    for index in range(count.total):
                        selected = await run.capture("estimate.candidate", lambda index=index: data.list_element(
                            run.native, source.value_handle, index, output_bytes=65536,
                            adapter=run.adapters["data"], process=run.process))
                        if (selected.error not in (0, 13, 50) or (selected.value_handle
                                and not len(selected.snapshot) == selected.snapshot_bytes == selected.total)):
                            return best
                        try:
                            binding = run.owned_binding(selected, "Building") if not selected.error else None
                        except RuntimeError:
                            binding = None
                        if binding is None:
                            owned_complete = False
                            break
                        key, raw_key = await owned_field("Building.GetBuildingType.GetKey", binding)
                        if raw_key.error in (13, 50):
                            owned_complete = False
                            break
                        if raw_key.error or not len(raw_key.snapshot) == raw_key.snapshot_bytes == raw_key.total:
                            return best
                        if key != building_key:
                            continue
                        detail = {"building_key": key, "candidate_source": expression,
                            "native_fields": {"building_key": raw_key}}
                        for field, native_expression in specs:
                            value, raw = await owned_field(native_expression, binding)
                            detail[field] = value
                            detail["native_fields"][field] = raw
                            if raw.error:
                                detail[field + "_reason"] = "native getter error " + str(raw.error)
                        return detail
            if owned_complete or count_value == 0:
                continue
            # Binding0/1 stays inside the existing native source callback. No
            # borrowed pointer escapes, and no command is created to guess a key.
            _, first = await scoped_field(expression, 0, "Building.GetTotalLevels")
            if first.error or first.source_count <= 0:
                continue
            for index in range(first.source_count):
                key, raw_key = await scoped_field(expression, index, "Building.GetBuildingType.GetKey")
                if raw_key.error:
                    return best
                if key != building_key:
                    continue
                detail = {"building_key": key, "candidate_source": expression,
                    "native_fields": {"building_key": raw_key}}
                for field, native_expression in specs:
                    value, raw = await scoped_field(expression, index, native_expression)
                    detail[field] = value
                    detail["native_fields"][field] = raw
                    if raw.error:
                        detail[field + "_reason"] = "native source-context getter error " + str(raw.error)
                return detail
        return best


    async def estimate(self, state, building, levels=1, *, details=False) -> OperationResult[ConstructionIntentResult]:
        """Native eligibility and measured costs; details=True requests raw reason text.

        Full nested localization is not yet available in the headless context.
        The native boolean is independent of whether explanation text exists.
        """
        state, building, levels = _id(state, "state"), _key(building), _levels(levels)
        return await self._ctx.perform("construction.estimate", lambda: self._estimate(state, building, levels, details))

    async def _estimate(self, state, building, levels, details=False):
        return await self._construction_intent(state, building, levels, details, estimate=True)

    async def build(self, state, building, levels=1, *, details=False) -> OperationResult[ConstructionIntentResult]:
        """Enqueue the requested levels once. Acceptance is not completion."""
        state, building, levels = _id(state, "state"), _key(building), _levels(levels)
        return await self._ctx.perform("construction.build", lambda: self._build(state, building, levels, details))

    async def _build(self, state, building, levels, details=False):
        return await self._construction_intent(state, building, levels, details, estimate=False)

    async def _post_build(self, run):
        """Read the current national government queue count once."""
        binding = await run.country()
        native = await run.capture("build.queue_count", lambda: data.evaluate(run.native,
            "Country.GetNumGovernmentConstructionsInQueue", (binding,), output_bytes=65536,
            adapter=run.adapters["data"], process=run.process))
        return {"government_queue_count": self._queue_field(native), "atomic_snapshot": False,
            "scope": "current national government queue count"}

    async def _construction_intent(self, state, building, levels, details, *, estimate):

        await self._ctx.refresh()
        run = _run.Run(self._ctx, "estimate" if estimate else "build", namespace="construction")
        result = {"state_id": state, "building_key": building, "levels": levels,
            "eligible": None, "accepted": None, "status": "preparing", "reason": None,
            "reason_complete": False,
            "reason_details": {"state_id": state, "building_key": building, "levels_requested": levels}}
        if estimate:
            result.update(required_construction=None, required_construction_per_level=None,
                construction_units="construction points", current_costs=None,
                existing_levels=None, maximum_levels=None,
                work_scope="native eligibility, current national expenses and selected State menu construction points")
        run.public_result = result
        try:
            run.actor = await self._ctx.country_id()
            parameters = {439: commands.UInt32(state), 10232: building,
                219: {10304: commands.UInt32(run.actor)}, 22805: levels, 22308: commands.Token(603)}
            created = await run.capture("create", lambda: commands.create(run.native, _QUEUE,
                parameters=parameters, actor_id=run.actor,
                adapter=run.adapters["commands"], process=run.process))
            result["creation"] = created
            if created.command:
                run.unadopted_command = created
            if created.error or not created.command:
                result.update(status="native_result", native_result=created)
            else:
                owner = run.adopt(created, expected_world=created.world)
                run.unadopted_command = None
                validation = await run.capture("validate",
                    lambda: owner.validate(capacity=65536 if details else 0), owner=owner)
                eligible = (True if validation.validated == 1 else False if validation.validated == 0 else None)
                result.update(validation=validation, reason=validation.reason,
                    reason_raw=validation.reason, eligible=eligible if validation.error in (0, 122) else None)
                result["reason_details"]["eligible"] = result["eligible"]
                if run.owner_unsettled(owner) or validation.error not in (0, 122) or result["eligible"] is None:
                    result.update(status="native_result", native_result=validation)
                elif estimate:
                    result.update(status="estimated", required_construction=None,
                        required_construction_per_level=None, construction_units="construction points",
                        existing_levels=None, maximum_levels=None)
                    try:
                        result["current_costs"] = await self._costs(run.actor, run=run)
                        detail = await self._fresh_candidate_details(state, building, run.actor, run=run)
                        if detail is not None:
                            result.update({key: value for key, value in detail.items() if key != "building_key"})
                    except Exception as error:
                        result["enrichment_error"] = error
                    required = result["required_construction"]
                    result["required_construction_per_level"] = required
                    if isinstance(required, Decimal):
                        sign, digits, exponent = required.as_tuple()
                        total = int("".join(str(digit) for digit in digits)) * levels
                        result["required_construction"] = Decimal((sign,
                            tuple(int(char) for char in str(total)), exponent))
                    elif type(required) in (int, float):
                        result["required_construction"] = required * levels
                    else:
                        result.setdefault("required_construction_reason",
                            "No current source-owned State candidate returned this key's construction points")
                    for field in ("existing_levels", "maximum_levels", "required_construction"):
                        result["reason_details"][field] = result.get(field)
                elif result["eligible"] is False:
                    result.update(status="rejected", accepted=False)
                else:
                    submitted = await run.capture("submit", lambda: owner.submit(flags=4), owner=owner)
                    result.update(native_result=submitted,
                        accepted=True if submitted.accepted else False if not submitted.error else None,
                        status="accepted" if submitted.accepted else "receiver_rejected" if not submitted.error else "native_result")
                    # Enrichment can neither block the typed intent nor downgrade
                    # its actual native receipt. Never retry a failed post-read.
                    if submitted.accepted and not run.owner_unsettled(owner):
                        try:
                            result["post_observation"] = await self._post_build(run)
                        except Exception as error:
                            result["post_observation_error"] = error
        except BaseException as error:
            try:
                result["error"] = error
                result["status"] = "original_error_graph_retained"
                if not hasattr(error, "observed_result"):
                    error.observed_result = result
            except BaseException:
                pass
            raise
        if estimate:
            result["enrichment_operations"] = run.operations
        if details:
            result["operations"] = run.operations
        partials = [native for _, native in run.operations if getattr(native, "value_handle", 0)
            and hasattr(native, "snapshot_bytes")
            and not len(native.snapshot) == native.snapshot_bytes == native.total]
        if partials:
            run.status = "partial_copy_retained"
            result["partial_native_results"] = partials
            result["cleanup"] = {"status": run.status,
                "reason": "Original Any retained for a later snapshot, not getter replay"}
        else:
            try:
                await run.finish(result)
            except BaseException as error:
                try:
                    if not hasattr(error, "observed_result"):
                        error.observed_result = result
                except BaseException:
                    pass
                raise
        return result

    async def cancel(self, *, position, details=False):
        """Cancel the current government source element at a zero-based position.

        ``position`` selects the CURRENT unfiltered source used by ``queue()``.
        It is neither a stable order ID nor the native command ordinal. A position
        copied from an earlier observation can now select a different order.
        The selected element's original CancelConstruction supplies its actual
        State, BuildingType and command index; no display-name lookup is needed.
        """
        if type(position) is not int or not 0 <= position <= 0x7fffffff:
            raise ValueError("position must be a nonnegative signed32 source position")
        return await self._ctx.perform("construction.cancel",
            lambda: self._cancel_selection(position, details))

    async def _cancel_selection(self, position, details):
        await self._ctx.refresh()
        run = _run.Run(self._ctx, "cancel", namespace="construction")
        result = {"action": "cancel", "queue": "government", "country_id": None,
            "source_position": position, "source_expression": _QUEUE_SOURCE,
            "selection_scope": "current_unfiltered_government_source",
            "accepted": None, "status": "selecting",
            "source_result": None, "native_element": None, "operations": run.operations}
        run.public_result = result

        async def select_and_cancel():
            country = await run.country()
            result["country_id"] = run.actor
            # This original producer always acts as the local player. The GUI
            # hides it on foreign government rows. Explicit-country read views
            # remain useful, but cannot supply a foreign ordinal to this producer.
            if getattr(self._ctx, "explicit_country_id", None) is not None:
                local = await run.capture("cancel.country_scope", lambda: data.evaluate(run.native,
                    "Country.IsLocalPlayer", (country,), output_bytes=65536,
                    adapter=run.adapters["data"], process=run.process))
                result["country_action_scope"] = self._queue_field(local)
                if local.error or local.kind != data.Kind.BOOL:
                    result.update(status="native_result", native_result=local)
                    return
                if local.value is False:
                    result.update(status="unavailable_for_country", native_result=local,
                        reason="ConstructionQueueElement.CancelConstruction acts on the local player's government queue")
                    return
            source = await run.capture("cancel.source", lambda: data.evaluate(run.native,
                _QUEUE_SOURCE, (country,), output_bytes=65536,
                adapter=run.adapters["data"], process=run.process))
            result["source_result"] = source
            if source.error:
                result.update(status="native_result", native_result=source)
                return
            if not isinstance(source.value, data.NativeList) or not source.value_handle:
                result.update(status="source_unavailable", reason="Native government queue source is not a retained list")
                return
            selected = await run.capture("cancel.element", lambda: data.list_element(
                run.native, source.value_handle, position, output_bytes=65536,
                adapter=run.adapters["data"], process=run.process))
            result["native_element"] = selected
            if selected.error:
                result.update(status="native_result", native_result=selected)
                return
            try:
                binding = run.owned_binding(selected, "ConstructionQueueElement")
            except RuntimeError as error:
                result.update(status="source_unavailable", reason=str(error))
                return
            if details:
                tooltip = await run.capture("cancel.tooltip", lambda: data.evaluate(run.native,
                    "ConstructionQueueElement.GetTooltip", (binding,), output_bytes=65536,
                    adapter=run.adapters["data"], process=run.process))
                result["tooltip"] = self._queue_field(tooltip)
            owner = await run.generated_owner("ConstructionQueueElement.CancelConstruction", (binding,))
            await run.validate_submit(owner, result)
            if "validation" in result:
                # Preserve the original empty/native reason, not truthiness-normalized text.
                result["reason"] = result["validation"].reason
            if result.get("eligible") is False and result["status"] == "selecting":
                result.update(status="rejected", accepted=False, native_result=result["validation"])

        try:
            await select_and_cancel()
        except _run.KnownResultError as error:
            result.update(status="native_result", native_result=error.result, reason=str(error))
        except _run.SourceUnavailable as error:
            result.update(status="source_unavailable", reason=str(error))
        except BaseException as error:
            result.update(status="original_error_graph_retained", error=error)
            try:
                if not hasattr(error, "observed_result"):
                    error.observed_result = result
            except BaseException:
                pass
            raise
        # An incomplete copied Any keeps its original graph for snapshot recovery.
        # This never changes native admission or repeats the producer/submit.
        partials = [native for _, native in run.operations if getattr(native, "value_handle", 0)
            and hasattr(native, "snapshot_bytes")
            and not len(native.snapshot) == native.snapshot_bytes == native.total]
        if partials:
            run.status = "partial_copy_retained"
            result["partial_native_results"] = partials
            result["cleanup"] = {"status": run.status}
        else:
            await run.finish(result)
        return result
