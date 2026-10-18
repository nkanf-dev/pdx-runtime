"""Player-facing country reads built from the existing native SDK.

Importing this module does not connect or open a process. Full IDs remain native
IDs. Each list is assembled from separate live observations, not an atomic save.
"""
from __future__ import annotations

import asyncio

from pdx_runtime import v3_data as data, v3_objects as objects
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_wire import resolve_v3_export
from pdx_runtime.v3_game._text import plain_text as _plain_name
from pdx_runtime.v3_game import _text as localization
from pdx_runtime.v3_game._values import fixed_decimal

_OBJECT_PAGE_ROWS = 4096  # Fixed-size records: <=164 KiB/page, not a total-row limit.


from pdx_runtime.v3_game._projection import _projection_supported, _projection_page, _projection_value

class _Read:
    """Keep completed Any owners private, including an uncertain dependent read."""

    def __init__(self, api):
        self.api = api
        self.ctx = api._ctx
        self.world = self.ctx.world
        self.context_world = self.ctx.world
        self.owners = []
        self.functions = {}
        self.function_names = {}
        self.type_ids = {}
        self.uncertain = False
        self.label = ""

    async def query(self, label, factory):
        self.label = label
        try:
            result = await self.ctx.query(label, factory)
        except BaseException:
            # A dependent callback may still borrow one of these values. The
            # context retains the exact query; never release or replay it here.
            self.uncertain = True
            raise
        # Observe provenance already supplied by this result, never issue a
        # second native request just to certify copied data.
        await self.check_world()
        observed_world = getattr(result, "world", None)
        if observed_world is not None:
            self.observe_world(observed_world, label)
        handle = getattr(result, "value_handle", 0)
        if handle and handle not in self.owners:
            self.owners.append(handle)
        return result

    def observe_world(self, world, source):
        if world == self.world:
            return
        previous = self.world
        self.world = world
        self.functions.clear()
        self.function_names.clear()
        self.type_ids.clear()
        evidence = {"domain": "country", "event": "world_changed", "source": source,
            "previous_world": previous, "world": world,
            "reason": "World changed during separate native observations; copied values are preserved."}
        diagnostics = getattr(self.ctx, "diagnostics", None)
        if isinstance(diagnostics, list):
            diagnostics.append(evidence)
        elif isinstance(diagnostics, dict):
            diagnostics.setdefault("country", []).append(evidence)

    async def type_id(self, name):
        # Per-read metadata, not an object/pointer lease or a cross-read cache.
        await self.check_world()
        if name not in self.type_ids:
            type_id = await self.ctx.type_id(name)
            await self.check_world()
            self.type_ids[name] = type_id
        return self.type_ids[name]

    async def check_world(self):
        # Compatibility for country helpers: only consume a context update that
        # another requested operation has already observed. Reads are not a
        # cross-world transaction; repeated refreshes neither lease IDs nor make
        # separate getter values atomic.
        if self.ctx.world != self.context_world:
            self.context_world = self.ctx.world
            self.observe_world(self.ctx.world, self.label or "context")

    async def close(self):
        if self.uncertain:
            self.api._retain(self)
            return
        try:
            while self.owners:
                handle = self.owners[-1]
                result = await self.query("country.release", lambda handle=handle: data.release(
                    self.ctx.native, handle, **self.api._options("data")))
                if result.error or result.value_handle:
                    self.uncertain = True  # known failed release must not be replayed
                    raise RuntimeError(f"country value cleanup: native error {result.error}; owner retained internally")
                self.owners.remove(handle)
        except BaseException:
            self.api._retain(self)
            raise


class CountryAPI:
    """Read the selected country without handling wrappers or native owners."""

    def __init__(self, ctx):
        self._ctx = ctx
        self._retained_reads = []

    def _retain(self, read):
        if read not in self._retained_reads:
            self._retained_reads.append(read)
        evidence = {"operation": "country", "stage": read.label,
                    "retained_read": read, "reason": "original query or cleanup requires settlement"}
        diagnostics = getattr(self._ctx, "diagnostics", None)
        if isinstance(diagnostics, list):
            diagnostics.append(evidence)
        elif isinstance(diagnostics, dict):
            diagnostics.setdefault("country", []).append(evidence)

    def _options(self, role):
        return {"adapter": self._ctx.adapters[role], "process": self._ctx.process}

    async def _run(self, work):
        await self._ctx.refresh()
        country_id = await self._ctx.country_id()
        read = _Read(self)
        try:
            return await work(read, country_id)
        finally:
            await read.close()

    async def _functions(self, read, type_id):
        if type_id not in read.functions:
            rows, start = [], 0
            while True:
                await read.check_world()
                page = await read.query("country.functions", lambda start=start: data.enumerate_functions(
                    self._ctx.native, type_id, start=start, capacity=256, **self._options("data")))
                if page.error:
                    raise RuntimeError(f"country metadata: native error {page.error}")
                rows.extend(page.rows)
                if page.next >= page.total:
                    break
                if page.next <= start:
                    raise RuntimeError("country metadata pagination did not advance")
                start = page.next
            read.functions[type_id] = rows
        return read.functions[type_id]

    async def _function(self, read, receiver, type_id, getter, *, argument_count=0):
        rows = await self._functions(read, type_id)
        if type_id not in read.function_names:
            names = {}
            for row in rows:
                names.setdefault(row.name, []).append(row)
            read.function_names[type_id] = names
        matches = read.function_names[type_id].get(getter, ())
        expression = f"{receiver}.{getter}"
        if len(matches) != 1:
            return None, f"{expression}: current native getter missing or ambiguous"
        function = matches[0]
        direct, scoped, action_direct, action_scoped = function.callbacks
        if (function.type_id != type_id or function.argument_count != argument_count
                or action_direct or action_scoped or bool(direct) == bool(scoped)):
            signature = "zero-argument" if argument_count == 0 else f"{argument_count}-argument"
            return None, f"{expression}: current metadata is not a {signature} read getter"
        return function, None

    async def _getter(self, read, receiver, binding, getter, *, chain="", return_type_id=None, arguments=()):
        function, reason = await self._function(read, receiver, binding.type_id, getter,
            argument_count=len(arguments))
        if function is None:
            return None, reason
        call = f"({', '.join(arguments)})" if arguments else ""
        expression = f"{receiver}.{getter}{call}{chain}"
        result = await read.query(expression, lambda: data.evaluate(
            self._ctx.native, expression, (binding,), output_bytes=4096, **self._options("data")))
        # MORE_DATA retains the original Any. Enlarge its snapshot, not the getter.
        while result.error == data.ERROR_MORE_DATA and result.value_handle:
            required = result.total
            if required <= result.snapshot_bytes:
                return None, f"{expression}: native error {result.error}; incomplete snapshot size"
            previous = result.snapshot_bytes
            result = await read.query(f"{expression}.snapshot", lambda result=result, required=required: data.snapshot(
                self._ctx.native, result.value_handle, output_bytes=required, **self._options("data")))
            if result.error == data.ERROR_MORE_DATA and result.snapshot_bytes <= previous:
                return None, f"{expression}: native error {result.error}; snapshot did not advance"
        if result.error:
            return None, f"{expression}: native error {result.error}"
        return result, None

    async def _name(self, read, receiver, binding):
        reasons = []
        for getter in ("GetNameNoFormatting", "GetName"):
            # _getter checks the current receiver metadata and read-only, zero
            # argument signature before invoking either original native getter.
            result, reason = await self._getter(read, receiver, binding, getter)
            if result is None:
                reasons.append(reason)
                continue
            expression = f"{receiver}.{getter}"
            if result.kind != data.Kind.STRING or result.snapshot_bytes < result.total:
                reasons.append(f"{expression}: native result is not a complete string")
                continue
            try:
                rich = result.snapshot.decode("utf-8", "strict")
            except UnicodeDecodeError:
                reasons.append(f"{expression}: native string is not valid UTF-8")
                continue
            plain = _plain_name(rich)
            if plain != rich:
                evidence = {"domain": "country", "event": "display_name", "source": expression,
                            "id": binding.object, "name": plain, "rich_name": rich}
                diagnostics = getattr(self._ctx, "diagnostics", None)
                if callable(getattr(diagnostics, "append", None)):
                    diagnostics.append(evidence)
                elif isinstance(diagnostics, dict):
                    diagnostics.setdefault("display_names", []).append(evidence)
            if plain:
                return plain, None
            reasons.append(f"{expression}: native name contains no display text")
        return None, "; ".join(reasons)

    async def _scalar(self, read, binding, getter, return_name, *, arguments=(), observation=None, receiver="Country"):
        result, reason = await self._getter(read, receiver, binding, getter, arguments=arguments)
        if result is None:
            return None, reason
        value = result.value
        if return_name == "CFixedPoint" and isinstance(value, data.FixedPoint):
            quantity, signed, scale = fixed_decimal(value), value.signed, 100000
        elif return_name == "int64" and type(value) is int:
            quantity, signed, scale = value, value, 1
        else:
            return None, f"{receiver}.{getter}: native scalar kind is not {return_name}"
        if observation is not None:
            observation.update({"raw_signed": signed, "scale": scale})
        return quantity, None

    async def _records(self, read, domain):
        records, start = [], 0
        while True:
            await read.check_world()
            page = await read.query(f"country.{domain.name.lower()}.page", lambda start=start: objects.enumerate_objects(
                self._ctx.native, domain, start_index=start, max_items=_OBJECT_PAGE_ROWS, **self._options("objects")))
            if page.error:
                raise RuntimeError(f"country {domain.name.lower()} list: native error {page.error}")
            records.extend(record for record in page.records if not record.error)
            if not page.truncated:
                return records
            if page.next_index <= start:
                raise RuntimeError("country object pagination did not advance")
            start = page.next_index

    async def _project_records(self, read, receiver, type_id, ids, *, with_key=False,
                               with_taxation=False):
        """Copy complete ID pages; metadata only chooses an available name getter."""
        names, missing = [], []
        for getter in ("GetNameNoFormatting", "GetName"):
            function, reason = await self._function(read, receiver, type_id, getter)
            if function is None:
                missing.append(reason)
            else:
                names.append(receiver + "." + getter)
        fields = ([names[0]] if names else []) + (["Building.GetBuildingType.GetKey"] if with_key else [])
        taxation_offset = len(fields)
        if with_taxation:
            fields.extend(("State.CalcTaxCapacity", "State.CalcTaxCapacityUsage", "State.CalcTaxCapacityBalance"))
        answer, retry, pages = {}, [], []
        for full_id in ids:
            answer[full_id] = {"name": None, "name_reason": "; ".join(missing) or None}
        if not fields:
            return answer
        def name_value(full_id, cell, expression):
            value, reason = _projection_value(cell)
            if cell is not None and cell.kind == data.Kind.STRING:
                plain = _plain_name(value)
                if plain != value:
                    self._ctx.diagnostics.append({"domain": "country", "event": "display_name",
                        "source": expression, "id": full_id, "name": plain, "rich_name": value})
                if plain:
                    return plain, reason
                reason = reason or f"{expression}: native name contains no display text"
            elif cell is not None:
                reason = reason or f"{expression}: native result is not a complete string"
            return None, reason
        for start in range(0, len(ids), 64):
            selected = ids[start:start + 64]
            try:
                page = await _projection_page(self._ctx, "country.project." + receiver, fields,
                    tuple((data.ObjectBinding.full_id(receiver, type_id, full_id),) for full_id in selected))
            except BaseException as error:
                if with_taxation:
                    error.country_state_projection = {"records": answer, "native_pages": pages,
                        "pending_query": getattr(error, "query", None)}
                raise
            if with_taxation:
                pages.append(page)
            read.observe_world(page.world, "country.project." + receiver)
            copied = {(cell.row, cell.field): cell for cell in page.cells}
            for index, full_id in enumerate(selected):
                row = answer[full_id]
                if names:
                    row["name"], row["name_reason"] = name_value(full_id, copied.get((index, 0)), names[0])
                    if row["name"] is None and len(names) > 1:
                        retry.append(full_id)
                if with_key:
                    cell = copied.get((index, len(fields) - 1))
                    row["key"], row["key_reason"] = _projection_value(cell)
                if with_taxation:
                    row["taxation"] = await self._state_taxation(read,
                        data.ObjectBinding.state(type_id, full_id), page=page,
                        row_index=index, field_index=taxation_offset,
                        copied_cells={column: copied.get((index, column))
                            for column in range(taxation_offset, taxation_offset + 3)})
                if page.error:
                    for field in ("name", "key") if with_key else ("name",):
                        row[field + "_reason"] = "; ".join(filter(None,
                            (row.get(field + "_reason"), f"native projection error {page.error}")))
        for start in range(0, len(retry), 64):
            selected = retry[start:start + 64]
            try:
                page = await _projection_page(self._ctx, "country.project." + receiver + ".name",
                    (names[1],), tuple((data.ObjectBinding.full_id(receiver, type_id, full_id),) for full_id in selected))
            except BaseException as error:
                if with_taxation:
                    error.country_state_projection = {"records": answer, "native_pages": pages,
                        "pending_query": getattr(error, "query", None)}
                raise
            if with_taxation:
                pages.append(page)
            read.observe_world(page.world, "country.project." + receiver + ".name")
            copied = {cell.row: cell for cell in page.cells}
            for index, full_id in enumerate(selected):
                name, reason = name_value(full_id, copied.get(index), names[1])
                if name is not None:
                    answer[full_id]["name"], answer[full_id]["name_reason"] = name, reason
                elif reason:
                    answer[full_id]["name_reason"] = "; ".join(filter(None,
                        (answer[full_id]["name_reason"], reason)))
        return answer

    async def _state_rows(self, read, country_id, *, details=False):
        records = await self._records(read, objects.Domain.STATE)
        domestic = {record.resolved_id: record for record in records if record.owner_id == country_id}
        if not domestic:
            return []
        state_type = await read.type_id("State")
        projected = (await self._project_records(read, "State", state_type, list(domestic),
                                               with_taxation=details)
                     if await _projection_supported(self._ctx) else None)
        rows = []
        for state_id in domestic:
            if projected is None:
                name, reason = await self._name(read, "State", data.ObjectBinding.state(state_type, state_id))
            else:
                name, reason = projected[state_id]["name"], projected[state_id]["name_reason"]
            row = {"id": state_id, "name": name, "country_id": country_id,
                   "reasons": {"name": reason} if reason else {}}
            if details:
                row["taxation"] = (await self._state_taxation(read,
                    data.ObjectBinding.state(state_type, state_id)) if projected is None
                    else projected[state_id]["taxation"])
            rows.append(row)
            await read.close()  # copied row needs no native owner across the next row
        return rows

    async def capacities(self, *, details=False):
        """Read bureaucracy, authority and influence from the original top bar.

        Resource points are not money. Native signs are preserved. details=True
        also reads the game's expense breakdown text with a real Country scope.
        National institution cost is not a police-only cost or a tax-loss cause.
        """
        async def work(read, country_id):
            binding = data.ObjectBinding.country(await read.type_id("Country"), country_id)
            answer = {"country_id": country_id, "snapshot": "separate native observations"}
            sources = {}
            resources = ("bureaucracy", "authority", "influence")
            resource_fields = (("balance", "GetPowerDelta"), ("produced", "GetPowerIncome"),
                ("deficit_ratio", "GetDeficitPowerRatio"), ("excess_ratio", "GetExcessPowerRatio"))
            institution_fields = (("bureaucracy_cost", "GetInstitutionInvestmentBureaucracyCost"),
                ("cost_per_investment_level", "GetInstitutionInvestmentBureaucracyFactor"),
                ("investment_levels", "GetInstitutionInvestment"))
            projected = None
            if await _projection_supported(self._ctx):
                requested = [(resource, field, "Country." + getter + "('" + resource + "')")
                    for resource in resources for field, getter in resource_fields]
                requested.extend(("institutions", field, "Country." + getter)
                    for field, getter in institution_fields)
                page = await _projection_page(self._ctx, "country.capacities.page",
                    tuple(expression for _, _, expression in requested), ((binding,),), limit=1)
                read.observe_world(page.world, "country.capacities.page")
                answer["native_pages"] = [page]
                cells = {cell.field: cell for cell in page.cells if cell.row == 0}
                projected = {}
                for index, (resource, field, expression) in enumerate(requested):
                    cell = cells.get(index)
                    value, reason = _projection_value(cell)
                    raw = {}
                    if isinstance(value, data.FixedPoint):
                        raw = {"raw_signed": value.signed, "scale": 100000}
                        quantity = fixed_decimal(value)
                    else:
                        quantity = None
                        if cell is not None:
                            reason = "; ".join(filter(None, (reason,
                                expression + ": native scalar kind is not CFixedPoint")))
                    if cell is not None and not cell.complete and cell.kind != data.Kind.STRING:
                        reason = "; ".join(filter(None, (reason,
                            f"native scalar copy is incomplete ({len(cell.snapshot)}/{cell.total} bytes)")))
                    if cell is not None and cell.binding_error:
                        reason = "; ".join(filter(None, (reason,
                            "native provider binding error " + str(cell.binding_error))))
                    if page.error:
                        reason = "; ".join(filter(None, (reason,
                            "native projection error " + str(page.error))))
                    if not page.count_valid:
                        reason = "; ".join(filter(None, (reason,
                            "native projection row count is unavailable")))
                    projected[resource, field] = quantity, reason, raw
            for resource in resources:
                arguments = ("'" + resource + "'",)
                row = {"balance": None, "produced": None, "used": None,
                    "deficit_ratio": None, "excess_ratio": None, "expenses_text": None,
                    "expenses_raw": None,
                    "units": {"balance": resource + " points", "produced": resource + " points",
                        "used": resource + " points", "deficit_ratio": "fraction", "excess_ratio": "fraction"},
                    "used_observation": "production minus balance, from separate native getter observations",
                    "reasons": {}}
                raw_values = {}
                for field, getter in resource_fields:
                    raw = {}
                    if projected is None:
                        quantity, reason = await self._scalar(read, binding, getter, "CFixedPoint",
                            arguments=arguments, observation=raw)
                    else:
                        quantity, reason, raw = projected[resource, field]
                    if raw:raw_values[field] = raw
                    row[field] = quantity
                    sources[resource + "." + field] = "Country." + getter + "(" + arguments[0] + ")"
                    if reason:row["reasons"][field] = reason
                    await read.close()
                if "produced" in raw_values and "balance" in raw_values:
                    # Original C0EF80 -> C0F013 is native income - expenses.
                    # Reverse that same signed64 fixed-point arithmetic exactly;
                    # no formatted text parsing, float conversion, abs or clamp.
                    used_bits = (raw_values["produced"]["raw_signed"] - raw_values["balance"]["raw_signed"]) & 0xffffffffffffffff
                    used_raw = used_bits - (1 << 64) if used_bits & (1 << 63) else used_bits
                    row["used"] = fixed_decimal(used_raw)
                    sources[resource + ".used"] = sources[resource + ".produced"] + " - " + sources[resource + ".balance"]
                    dependency_reasons = [row["reasons"][field] for field in ("produced", "balance")
                        if field in row["reasons"]]
                    if dependency_reasons:
                        row["reasons"]["used"] = "; ".join(dependency_reasons)
                else:
                    row["reasons"]["used"] = "Current native resource production or balance is unavailable"
                if details:
                    observed, reason = await self._capacity_expenses(read, binding, resource)
                    if observed is not None:
                        row["expenses_text"] = observed.get("text")
                        row["expenses_raw"] = observed.get("raw_text")
                    if reason:row["reasons"]["expenses_text"] = reason
                    await read.close()
                answer[resource] = row
            institutions = {"bureaucracy_cost": None, "cost_per_investment_level": None,
                "investment_levels": None, "police_bureaucracy_cost": None,
                "units": {"bureaucracy_cost": "bureaucracy points",
                    "cost_per_investment_level": "bureaucracy points per investment level",
                    "investment_levels": "investment levels", "police_bureaucracy_cost": "bureaucracy points"},
                "scope": "all country institutions; native signs are preserved",
                "reasons": {"police_bureaucracy_cost": "This is the national institution total, not police-only; Country has no separate police institution cost getter"}}
            for field, getter in institution_fields:
                if projected is None:
                    quantity, reason = await self._scalar(read, binding, getter, "CFixedPoint")
                else:
                    quantity, reason, _ = projected["institutions", field]
                institutions[field] = quantity
                sources["institutions." + field] = "Country." + getter
                if reason:institutions["reasons"][field] = reason
                await read.close()
            answer["institutions"] = institutions
            evidence = {"domain": "country", "event": "capacities", "country_id": country_id,
                "sources": sources, "institution_ui_cost_multiplier": -1}
            diagnostics = getattr(self._ctx, "diagnostics", None)
            if callable(getattr(diagnostics, "append", None)):diagnostics.append(evidence)
            elif isinstance(diagnostics, dict):diagnostics.setdefault("country", []).append(evidence)
            return answer
        return await self._ctx.perform("country.capacities", lambda: self._run(work))

    async def _capacity_expenses(self, read, binding, resource):
        getter = "GetPowerExpensesString"
        function, reason = await self._function(read, "Country", binding.type_id, getter, argument_count=1)
        if function is None:return None, reason
        # Country.Self's ValueNode keeps its genuine owning full-ID Binding2.
        # A raw pointer Any by itself is never treated as ownership.
        await read.check_world()
        held = await read.query("Country.Self", lambda: data.evaluate(self._ctx.native, "Country.Self",
            (binding,), output_bytes=4096, **self._options("data")))
        if held.error or not held.object or "CDataCountry" not in held.native_type_name:
            return None, "The original Country receiver for the expense breakdown is unavailable"
        from pdx_runtime.v3_game import _text as helper
        expression = "Country." + getter + "('" + resource + "')"
        record = helper._retain(self._ctx, {"kind": "Country", "values": [], "queries": [],
            "releases": [], "storage": {}, "source_read": read})
        try:
            observed = await helper._format(self._ctx, record, held.object, binding.type_id, "Country", expression)
        except BaseException as error:
            if getattr(helper,"settled_error",lambda error:False)(error):
                helper._finish_record(self._ctx,record)
            else:read.uncertain = True
            raise
        helper._finish_record(self._ctx, record)
        complete = (observed.get("text_complete") and observed.get("context_supplied")
            and observed.get("native_temporaries_destroyed") and observed.get("decode_error") is None
            and observed.get("world", read.world) == read.world)
        return observed, None if complete else observed.get("reason") or "Native expense breakdown is incomplete"

    async def summary(self):
        """Return the native country ID, name, population and GDP.

        GDP uses the existing proven fixed-point decoder. Unavailable fields
        are None with a native or metadata reason, never invented zero values.
        """
        async def work(read, country_id):
            binding = data.ObjectBinding.country(await read.type_id("Country"), country_id)
            name, name_reason = await self._name(read, "Country", binding)
            population, population_reason = await self._scalar(read, binding, "GetTotalPopulation", "int64")
            gdp, gdp_reason = await self._scalar(read, binding, "GetGDP", "CFixedPoint")
            reasons = {key: reason for key, reason in (("name", name_reason),
                       ("population", population_reason), ("gdp", gdp_reason)) if reason}
            return {"id": country_id, "name": name, "population": population, "gdp": gdp,
                    "units": {"population": "people", "gdp": "currency"}, "reasons": reasons}
        return await self._ctx.perform("country.summary", lambda: self._run(work))

    async def _state_taxation(self, read, binding, *, page=None, row_index=0, field_index=0,
                              copied_cells=None):
        """Original State tax-capacity display; never national bureaucracy."""
        row = {"scope": "state", "capacity": None, "usage": None, "balance": None,
               "insufficient": None,
               "units": {"capacity": "taxation capacity points", "usage": "taxation capacity points",
                         "balance": "taxation capacity points"}, "reasons": {}}
        # Original STATE_TAX_CAPACITY_DESC uses these three State getters.
        # The condensed display's warning is exactly usage > capacity.
        cells = (copied_cells if copied_cells is not None else
                 {cell.field: cell for cell in page.cells if cell.row == row_index}
                 if page is not None else None)
        if page is not None:
            row.update(native_pages=[page], native_row=row_index)
        for index, (field, getter) in enumerate((("capacity", "CalcTaxCapacity"),
                ("usage", "CalcTaxCapacityUsage"), ("balance", "CalcTaxCapacityBalance"))):
            if cells is None:
                quantity, reason = await self._scalar(read, binding, getter, "CFixedPoint", receiver="State")
            else:
                cell = cells.get(field_index + index)
                value, reason = _projection_value(cell)
                quantity = fixed_decimal(value) if isinstance(value, data.FixedPoint) else None
                if cell is not None:
                    if quantity is None:
                        reason = "; ".join(filter(None, (reason,
                            "State." + getter + ": native scalar kind is not CFixedPoint")))
                    if not cell.complete and cell.kind != data.Kind.STRING:
                        reason = "; ".join(filter(None, (reason,
                            f"native scalar copy is incomplete ({len(cell.snapshot)}/{cell.total} bytes)")))
                    if cell.binding_error:
                        reason = "; ".join(filter(None, (reason,
                            "native provider binding error " + str(cell.binding_error))))
                if page.error:
                    reason = "; ".join(filter(None, (reason, "native projection error " + str(page.error))))
                if not page.count_valid:
                    reason = "; ".join(filter(None, (reason, "native projection row count is unavailable")))
            row[field] = quantity
            if reason:row["reasons"][field] = reason
        if row["usage"] is not None and row["capacity"] is not None:
            row["insufficient"] = row["usage"] > row["capacity"]
            dependency_reasons = [row["reasons"][field] for field in ("capacity", "usage")
                if field in row["reasons"]]
            if dependency_reasons:
                row["reasons"]["insufficient"] = "; ".join(dependency_reasons)
        else:
            row["reasons"]["insufficient"] = "Current state taxation capacity or usage is unavailable"
        return row

    async def state_infrastructure(self, state_id):
        """Read one owned state's infrastructure and market connection.

        Values are original scalar observations, not a diagnosis of isolation.
        Infrastructure points are not tax capacity, bureaucracy or money.
        Market access is a fraction; a real zero or False stays zero or False.
        Status text is copied native UTF-8, including any original markup.
        The separate getter observations are not an atomic state snapshot.
        """
        if type(state_id) is not int or not 0 <= state_id < 0xffffffff:
            raise ValueError("state_id must be a complete uint32 State ID; zero is valid")
        async def work(read, country_id):
            fields = ("capacity", "usage", "balance", "market_access", "isolated_from_market", "market_access_status_text")
            row = {"state_id": state_id, "country_id": country_id, "scope": "state", "available": False,
                **{field: None for field in fields},
                "units": {"capacity": "infrastructure points", "usage": "infrastructure points",
                    "balance": "infrastructure points", "market_access": "fraction"},
                "reasons": {}, "atomic_snapshot": False, "snapshot": "separate native observations"}
            await read.check_world()
            observed = await read.query("country.state_infrastructure.state", lambda: objects.read_ids(
                self._ctx.native, objects.Domain.STATE, (state_id,), **self._options("objects")))
            await read.check_world()
            reason = None
            if observed.error:
                reason = "Native State lookup failed: " + str(observed.error)
            elif (len(observed.records) != 1 or observed.records[0].error
                    or observed.records[0].resolved_id != state_id):
                reason = "The State is not available with this full ID in the current world"
            elif observed.records[0].owner_id != country_id:
                reason = "This State is not owned by the selected country"
            if reason:
                row["reasons"] = {"state": reason, **{field: reason for field in fields}}
                return row
            row["available"] = True
            binding = data.ObjectBinding.state(await read.type_id("State"), state_id)
            if await _projection_supported(self._ctx):
                specs = (("capacity", "GetInfrastructure", data.Kind.FIXED),
                    ("usage", "GetInfrastructureUsage", data.Kind.FIXED),
                    ("balance", "GetInfrastructureBalance", data.Kind.FIXED),
                    ("market_access", "GetMarketAccess", data.Kind.FIXED),
                    ("isolated_from_market", "IsIsolatedFromMarket", data.Kind.BOOL),
                    ("market_access_status_text", "GetMarketAccessStatus", data.Kind.STRING))
                page = await _projection_page(self._ctx, "country.state_infrastructure.page",
                    tuple("State." + getter for _, getter, _ in specs), ((binding,),), limit=1)
                read.observe_world(page.world, "country.state_infrastructure.page")
                row["native_pages"] = [page]
                cells = {cell.field: cell for cell in page.cells if cell.row == 0}
                for index, (field, getter, kind) in enumerate(specs):
                    cell = cells.get(index)
                    value, reason = _projection_value(cell)
                    if cell is not None:
                        if cell.kind != kind:
                            value = None
                            reason = "; ".join(filter(None, (reason,
                                "State." + getter + ": native kind " + str(cell.kind)
                                + " differs from expected " + str(int(kind)))))
                        elif kind == data.Kind.FIXED:
                            value = fixed_decimal(value)
                        if not cell.complete and cell.kind != data.Kind.STRING:
                            reason = "; ".join(filter(None, (reason,
                                f"native scalar copy is incomplete ({len(cell.snapshot)}/{cell.total} bytes)")))
                        if cell.binding_error:
                            reason = "; ".join(filter(None, (reason,
                                "native provider binding error " + str(cell.binding_error))))
                    if page.error:
                        reason = "; ".join(filter(None, (reason, "native projection error " + str(page.error))))
                    if not page.count_valid:
                        reason = "; ".join(filter(None, (reason, "native projection row count is unavailable")))
                    row[field] = value
                    if reason:
                        row["reasons"][field] = reason
                return row
            for field, getter in (("capacity", "GetInfrastructure"), ("usage", "GetInfrastructureUsage"),
                    ("balance", "GetInfrastructureBalance"), ("market_access", "GetMarketAccess")):
                row[field], reason = await self._scalar(read, binding, getter, "CFixedPoint", receiver="State")
                if reason:
                    row["reasons"][field] = reason
            result, reason = await self._getter(read, "State", binding, "IsIsolatedFromMarket")
            if result is not None:
                if result.kind != data.Kind.BOOL:
                    reason = "State.IsIsolatedFromMarket: current native result is not bool"
                else:
                    row["isolated_from_market"] = result.value
            if reason:
                row["reasons"]["isolated_from_market"] = reason
            result, reason = await self._getter(read, "State", binding, "GetMarketAccessStatus")
            if result is not None:
                if (result.kind != data.Kind.STRING or result.snapshot_bytes != result.total
                        or len(result.snapshot) != result.snapshot_bytes):
                    reason = "State.GetMarketAccessStatus: native result is not a complete string"
                else:
                    try:
                        row["market_access_status_text"] = result.snapshot.decode("utf-8", "strict")
                    except UnicodeDecodeError:
                        reason = "State.GetMarketAccessStatus: native string is not valid UTF-8"
            if reason:
                row["reasons"]["market_access_status_text"] = reason
            await read.check_world()
            return row
        return await self._ctx.perform("country.state_infrastructure", lambda: self._run(work))

    async def population(self, *, state, details=False):
        """Read one owned state's population and real lower/middle/upper sizes.

        State-wide radicals and loyalists remain separate from stratum counts.
        This does not predict how an event's scripted radicalism change applies.
        No population scan is added to states() or states(details=True).
        """
        state_id = state.get("id") if isinstance(state, dict) else state
        if type(state_id) is not int or not 0 <= state_id < 0xffffffff:
            raise ValueError("state must be a complete State ID or {id: full_id}")
        from . import population as helper
        return await self._ctx.perform("country.population", lambda: self._run(
            lambda read, country_id: helper.read_state(self, read, country_id, state_id, details=details)))

    async def states(self, *, details=False):
        """List owned states; details adds their local taxation capacity.

        The default rows stay lightweight and unchanged. State taxation uses
        native capacity, usage and balance; it is not country bureaucracy.
        """
        return await self._ctx.perform("country.states",
            lambda: self._run(lambda read, country_id: self._state_rows(read, country_id, details=details)))

    async def buildings(self, *, state=None, details=False):
        """List domestic buildings, optionally in one native state ID or name.

        state accepts a complete native State ID, an observed state name, or an
        {"id": full_id} row. Only owned states can match. None lists all states.
        Levels come from each native Building record. Names use the game's
        state/type aggregate wrapper; this does not change Building identity.
        details=True also includes the native building tooltip as readable text
        and exact rich text. The default list does not run tooltip formatting.
        """
        async def work(read, country_id):
            owned_states = await self._state_rows(read, country_id)
            if state is not None:
                selected = state.get("id") if isinstance(state, dict) else state
                if type(selected) is int and 0 <= selected < 0xffffffff:
                    owned_states = [row for row in owned_states if row["id"] == selected]
                elif isinstance(selected, str) and not isinstance(state, dict):
                    owned_states = [row for row in owned_states
                                    if row["name"] is not None and row["name"].casefold() == selected.casefold()]
                else:
                    raise ValueError("state must be a complete State ID, observed state name, or {id: full_id}")
            states = {row["id"]: row for row in owned_states}
            if not states:
                return []
            records = await self._records(read, objects.Domain.BUILDING)
            domestic = {record.resolved_id: record for record in records if record.state_id in states}
            if not domestic:
                return []
            building_type = await read.type_id("Building")
            projected = (await self._project_records(read, "Building", building_type, list(domestic), with_key=True)
                         if await _projection_supported(self._ctx) else None)
            rows = []
            for building_id, record in domestic.items():
                binding = data.ObjectBinding.building(building_type, building_id)
                if projected is None:
                    name, name_reason = await self._name(read, "Building", binding)
                    key, key_reason = await self._building_key(read, binding)
                else:
                    copied = projected[building_id]
                    name, name_reason = copied["name"], copied["name_reason"]
                    key, key_reason = copied["key"], copied["key_reason"]
                state_row = states[record.state_id]
                reasons = {key: reason for key, reason in (("name", name_reason),
                           ("key", key_reason), ("state_name", state_row["reasons"].get("name"))) if reason}
                row = {"id": building_id, "name": name, "key": key,
                       "state_id": record.state_id, "state_name": state_row["name"],
                       "country_id": country_id, "levels": record.levels, "reasons": reasons}
                if details:
                    await read.check_world()
                    localized = await localization.read_building_tooltip(
                        self._ctx, building_id)
                    row["tooltip"] = localized.get("text")
                    row["tooltip_raw"] = localized.get("raw_text")
                    row["tooltip_complete"] = bool(localized.get("text_complete")
                        and localized.get("context_supplied") and localized.get("native_temporaries_destroyed")
                        and localized.get("decode_error") is None
                        and localized.get("world", read.world) == read.world)
                    if not row["tooltip_complete"]:
                        row["reasons"]["tooltip"] = localized.get("reason") or "Native building description is incomplete"
                rows.append(row)
                await read.close()  # bound native ownership stays row-sized
            return rows
        return await self._ctx.perform("country.buildings", lambda: self._run(work))

    async def _building_key(self, read, binding):
        # The native parser consumes the borrowed BuildingType in this callback.
        # Return its copied key or actual error, not a second metadata verdict.
        result, reason = await self._getter(read, "Building", binding, "GetBuildingType",
            chain=".GetKey")
        if result is None:
            return None, reason
        if result.kind != data.Kind.STRING or result.snapshot_bytes < result.total:
            return None, "Building.GetBuildingType.GetKey: native result is not a complete string"
        try:
            return result.snapshot.decode("utf-8", "strict"), None
        except UnicodeDecodeError:
            return None, "Building.GetBuildingType.GetKey: native key is not valid UTF-8"
