"""Copied Country, State and Pop observations for V3 economy and research facades.

Use the existing projection callback and serialized query owner. Each page is a
separate live observation, not an atomic save. The old-adapter path uses _Read
owners; it never repeats a getter to recover a larger string or pending query.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game.country import CountryAPI, _Read
from pdx_runtime.v3_game._projection import _projection_page, _projection_supported
from pdx_runtime.v3_game._values import fixed_decimal


@dataclass(frozen=True)
class Field:
    name: str
    expression: str
    kind: str
    unit: str | None = None

    def __post_init__(self):
        if not self.name or not re.fullmatch(
                r"[A-Za-z_][A-Za-z_0-9]*\.(?:[A-Za-z_][A-Za-z_0-9]*\.)*[A-Za-z_][A-Za-z_0-9]*",
                self.expression):
            raise ValueError("expected a named, zero-argument native observation chain")
        if self.kind not in {"fixed", "integer", "boolean", "text"}:
            raise ValueError("unsupported Country observation kind")


@dataclass(frozen=True)
class NativeExpression:
    """One opaque expression for existing ordinary native evaluation.

    The engine parses the expression. This descriptor is not a Page field,
    Python DSL or native formal-argument frame. Receivers stay in that original
    expression graph; no borrowed receiver is exported to a later callback.
    """
    name: str
    expression: str
    kind: str
    unit: str | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("native observation name must be nonempty")
        if not isinstance(self.expression, str) or not self.expression:
            raise ValueError("native observation expression must be nonempty")
        if self.kind not in {"fixed", "integer", "boolean", "text"}:
            raise ValueError("unsupported native observation kind")


_KINDS = {"fixed": (data.Kind.FIXED,), "integer": (data.Kind.I32, data.Kind.U32,
    data.Kind.I64, data.Kind.U64), "boolean": (data.Kind.BOOL,), "text": (data.Kind.STRING,)}


def _record(answer, field, cell, *, page_error=0, count_valid=True):
    reasons = []
    value = native_value = None
    available = False
    if cell is None:
        row = {"source": field.expression, "available": False, "complete": False,
            "error": None, "kind": None, "raw_bits": None, "snapshot": b"", "total": None}
        reasons.append("native observation did not copy this field")
    else:
        row = {"source": field.expression, "error": cell.error, "kind": int(cell.kind),
            "type_id": cell.type_id, "raw_bits": cell.raw_bits,
            "snapshot": cell.snapshot, "total": cell.total,
            "binding_error": cell.binding_error}
        if cell.error:
            reasons.append(f"native getter error {cell.error}")
        if cell.binding_error:
            reasons.append(f"native binding error {cell.binding_error}")
        if not cell.complete:
            reasons.append(f"incomplete native copy ({len(cell.snapshot)}/{cell.total} bytes)")
        try:
            if cell.kind == data.Kind.STRING:
                try:
                    native_value = cell.snapshot.decode("utf-8", "strict")
                except UnicodeDecodeError:
                    native_value = cell.snapshot.decode("utf-8", "replace")
                    reasons.append("native string contains invalid UTF-8")
            elif cell.kind in (data.Kind.EMPTY, data.Kind.BOOL, data.Kind.I32,
                    data.Kind.U32, data.Kind.I64, data.Kind.U64, data.Kind.FLOAT,
                    data.Kind.DOUBLE, data.Kind.FIXED):
                native_value = cell.value
            if cell.kind in _KINDS[field.kind]:
                value = fixed_decimal(native_value) if field.kind == "fixed" else native_value
                available = True  # Zero, false, empty text and partial copied data are observations.
            else:
                reasons.append(f"native kind {int(cell.kind)} is not {field.kind}")
        except (ValueError, TypeError, OverflowError) as error:
            reasons.append(f"native value decoding failed: {error}")
        row.update(available=available, complete=available and not reasons)
    if page_error:
        reasons.append(f"native projection error {page_error}")
    if not count_valid:
        reasons.append("native projection row count is unavailable")
    row.update(value=value, native_value=native_value, page_error=page_error,
        complete=available and not reasons)
    answer["values"][field.name] = value
    answer["observations"][field.name] = row
    if reasons:
        answer["reasons"][field.name] = "; ".join(reasons)
    else:
        answer["reasons"].pop(field.name, None)


def _world(answer, world):
    if world != answer["world"]:
        answer["world_consistent"] = False
        answer["reasons"]["_world"] = (
            "world changed between native observations; copied values are preserved")


def _page(answer, fields, page):
    cells = {(cell.row, cell.field): cell for cell in page.cells}
    answer["pages"].append({"world": page.world, "error": page.error,
        "count_valid": page.count_valid, "row_count": page.row_count})
    _world(answer, page.world)
    for index, field in enumerate(fields):
        _record(answer, field, cells.get((0, index)), page_error=page.error,
            count_valid=page.count_valid)


def _retain(ctx, read):
    if not read.owners and not read.uncertain:
        return
    retained = getattr(ctx, "_country_observation_reads", None)
    if retained is None:
        ctx._country_observation_reads = retained = []
    if read not in retained:
        retained.append(read)


async def copy_fields(ctx, read, answer, fields, bindings, *, label):
    """Copy native scalar/text expressions with the caller's retained source owner.

    The caller keeps that same _Read open until every borrowed binding settles.
    This function creates no owner/pool, refreshes no scope and issues no
    metadata or validation query. Snapshot continuation uses the original Any.
    """
    bindings = tuple(bindings)
    for field in fields:
        result = await read.query(label + "." + field.name, lambda field=field: data.evaluate(
            ctx.native, field.expression, bindings, output_bytes=4096,
            adapter=ctx.adapters["data"], process=ctx.process))
        def copy_result(observed):
            cell = data.ProjectionCell(0, 0, observed.error, observed.kind, observed.type_id,
                observed.raw_bits, observed.snapshot, observed.total)
            _record(answer, field, cell)
            _world(answer, read.world)  # Provenance already observed by _Read, no new query.
        copy_result(result)  # Keep this prefix even if the next snapshot is interrupted.
        copies = []
        # Enlarge the original Any's copied bytes. Never re-evaluate its getter.
        while result.error == data.ERROR_MORE_DATA and result.value_handle:
            if result.total <= len(result.snapshot):
                break
            previous = result
            previous_row = answer["observations"][field.name]
            previous_reason = answer["reasons"].get(field.name)
            result = await read.query(label + "." + field.name + ".snapshot",
                lambda previous=previous: data.snapshot(ctx.native, previous.value_handle,
                    output_bytes=previous.total, adapter=ctx.adapters["data"], process=ctx.process))
            copy_result(result)
            current_row = answer["observations"][field.name]
            copies.append({key: current_row[key] for key in
                ("error", "kind", "raw_bits", "snapshot", "total", "value", "available", "complete")})
            # A failed/shorter copy cannot erase a prefix already observed.
            if previous_row["available"] and (not current_row["available"] or
                    (field.kind == "text" and len(result.snapshot) < len(previous.snapshot))):
                reason = answer["reasons"].get(field.name) or "snapshot copy did not advance"
                answer["observations"][field.name] = previous_row
                answer["values"][field.name] = previous_row["value"]
                previous_row["complete"] = False
                answer["reasons"][field.name] = "; ".join(filter(None, (previous_reason, reason)))
            answer["observations"][field.name]["copies"] = copies
            if result.error == data.ERROR_MORE_DATA and len(result.snapshot) <= len(previous.snapshot):
                break


async def finish_read(ctx, read, answer, *, label):
    """Close the same _Read, keeping copied values and original cleanup owners."""
    try:
        await read.close()
    except Exception as error:
        answer["cleanup_warnings"].append(str(error))
        ctx.diagnostics.append({"label": label, "cleanup_error": error, "read": read})
    finally:
        _retain(ctx, read)


async def _fallback(ctx, answer, fields, binding, label):
    read = _Read(CountryAPI(ctx))
    try:
        await copy_fields(ctx, read, answer, fields, (binding,), label=label)
    finally:
        await finish_read(ctx, read, answer, label=label)


async def read_fields(ctx, fields, *, label):
    """Read Country fields with one selected-country lookup and no validators.

    Native errors and partial values coexist. On transport failure the original
    exception/query is kept, with copied observations in country_observations.
    """
    return await _read_fields(ctx, fields, label=label, domain="Country")


async def read_state_fields(ctx, state_id, fields, *, label):
    """Read an explicit full uint32 State ID without a selected-country query.

    A StateRegion promotion stays inside the original native expression. No
    region ID, raw pointer or guessed State.GetID value is used as the binding.
    On transport failure, copied values remain in error.state_observations.
    """
    if type(state_id) is not int or not 0 <= state_id <= 0xffffffff:
        raise ValueError("state_id must be a full uint32 native State ID")
    return await _read_fields(ctx, fields, label=label, domain="State", full_id=state_id)


async def read_pop_fields(ctx, pop_id, fields, *, label):
    """Read one original full uint32 Pop ID with the shared owner and decoder.

    No selected-country query, raw-pointer cast, metadata enumeration or second
    pool is needed. Original transport errors carry error.pop_observations.
    """
    if type(pop_id) is not int or not 0 <= pop_id <= 0xffffffff:
        raise ValueError("pop_id must be a full uint32 native Pop ID")
    return await _read_fields(ctx, fields, label=label, domain="Pop", full_id=pop_id)


async def read_pop_expressions(ctx, pop_id, expressions, *, label):
    """Copy ordinary native expressions with one original full-ID Pop root.

    Uses the existing engine compiler, _Read and decoder. No Page parameter
    codec or physical formal slot is created. Each callback evaluates its own
    original Pop-rooted expression; these observations are not atomic.
    """
    if type(pop_id) is not int or not 0 <= pop_id <= 0xffffffff:
        raise ValueError("pop_id must be a full uint32 native Pop ID")
    return await _read_fields(ctx, expressions, label=label, domain="Pop",
        full_id=pop_id, ordinary=True)


async def _read_fields(ctx, fields, *, label, domain, full_id=None, ordinary=False):
    fields = tuple(fields)
    descriptor = NativeExpression if ordinary else Field
    if not all(isinstance(field, descriptor) for field in fields):
        raise TypeError("fields must contain " + descriptor.__name__ + " descriptors")
    if len({field.name for field in fields}) != len(fields):
        raise ValueError("native observation field names must be unique")
    if any(not field.expression.startswith(domain + ".") for field in fields):
        raise ValueError("observation fields must use the bound " + domain + " root")
    await ctx.refresh()
    if domain == "Country":
        full_id = await ctx.country_id()
    answer = {domain.lower() + "_id": full_id, "world": ctx.world, "world_consistent": True, "values": {},
        "observations": {}, "reasons": {}, "complete": not fields,
        "units": {field.name: field.unit for field in fields if field.unit is not None},
        "sources": {field.name: field.expression for field in fields},
        "snapshot": "separate native observations", "pages": [], "cleanup_warnings": []}
    if not fields:
        return answer
    type_id = await ctx.type_id(domain)
    binding = (data.ObjectBinding.country(type_id, full_id) if domain == "Country"
        else data.ObjectBinding.full_id(domain, type_id, full_id))
    # Predeclare missing fields so a failed later page cannot imply completeness.
    for field in fields:
        _record(answer, field, None)
    try:
        if not ordinary and await _projection_supported(ctx):
            for start in range(0, len(fields), data.MAX_PAGE_FIELDS):
                selected = fields[start:start + data.MAX_PAGE_FIELDS]
                try:
                    page = await _projection_page(ctx, label,
                        tuple(field.expression for field in selected), ((binding,),),
                        cleanup_warnings=answer["cleanup_warnings"])
                except BaseException as error:
                    partial = getattr(getattr(error, "query", None), "partial_result", None)
                    if partial is not None:
                        _page(answer, selected, partial)
                    raise
                _page(answer, selected, page)
        else:
            await _fallback(ctx, answer, fields, binding, label)
    except BaseException as error:
        answer["complete"] = answer["world_consistent"] and all(
            row["complete"] for row in answer["observations"].values())
        setattr(error, domain.lower() + "_observations", answer)
        raise
    answer["complete"] = answer["world_consistent"] and all(
        row["complete"] for row in answer["observations"].values())
    return answer


def copy_source_page(answer, fields, source, page, cells, *, rows_key):
    """Copy one ordinary original-source Page with native values and receipts.

    This is scalar/text copying only. It creates no owner, query, source policy,
    registry or lease. The caller keeps the original page/copy query on failure.
    """
    fields = tuple(fields)
    if answer["world"] is None:
        answer["world"] = page.world  # Native0 is a value, not an absence test.
    _world(answer, page.world)
    answer["source_observations"].append({"error": source.error,
        "page_error": page.error, "world": page.world,
        "source_count": source.source_count, "count_valid": source.count_valid,
        "start": source.start, "row_count": source.row_count,
        "next": source.next, "has_more": page.has_more,
        "execution_thread_id": page.execution_thread_id})
    if source.error or page.error or not source.count_valid:
        answer["reasons"].setdefault("_source", []).append(
            f"native source/page error {source.error}/{page.error}; count_valid={source.count_valid}")
    if source.count_valid:
        previous = answer["source_count"]
        answer["source_count"] = source.source_count
        if previous is not None and previous != source.source_count:
            answer["reasons"]["_source_changed"] = "native source count changed between separate live pages"
    by_row, raw_rows, duplicates = {}, {}, {}
    for cell in cells:
        raw_rows.setdefault(cell.row, []).append(cell)
        selected = by_row.setdefault(cell.row, {})
        if cell.field in selected:
            duplicates.setdefault(cell.row, []).append(cell.field)
        else:
            selected[cell.field] = cell
    for index in sorted(by_row):
        row = {"source_index": index, "world": page.world,
            "world_consistent": answer["world_consistent"], "values": {},
            "observations": {}, "reasons": {}, "bindings": {},
            "raw_cells": tuple(raw_rows[index])}
        if index in duplicates:
            row["reasons"]["_duplicate_cells"] = tuple(duplicates[index])
        for column, field in enumerate(fields):
            cell = by_row[index].get(column)
            _record(row, field, cell, page_error=page.error,
                count_valid=source.count_valid)
            if cell is not None:
                row["bindings"][field.name] = {"mode": cell.binding_mode,
                    "kind": cell.binding_kind, "native_type_name": cell.binding_native_type_name,
                    "error": cell.binding_error}
        row.update(row["values"])
        row["complete"] = bool(row["world_consistent"] and not row["reasons"]
            and all(observed["complete"] for observed in row["observations"].values()))
        answer[rows_key].append(row)
    if (source.count_valid and not source.error and not page.error
            and not page.has_more):
        answer["source_complete"] = True

