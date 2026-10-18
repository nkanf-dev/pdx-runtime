"""Native Company and CompanyType observations with stable identities and partial results."""
from __future__ import annotations

from dataclasses import dataclass

from pdx_runtime import v3_data as data, v3_access, v3_objects as objects, native_call
from pdx_runtime.v3_wire import resolve_v3_export
from pdx_runtime.v3_game.country import CountryAPI, _Read
from pdx_runtime.v3_game._projection import _projection_page, _projection_supported
from pdx_runtime.v3_game._values import fixed_decimal


@dataclass(frozen=True)
class Field:
    name: str
    expression: str
    kind: str
    unit: str | None = None


# Zero-argument native methods in data_types_uncategorized.txt. Promote paths
# use the same original DataFactory traversal as Building.GetBuildingType.GetKey.
# Date.GetString (reference-types/data_types_common.txt) follows the promoted
# Company.GetEstablishmentDate (data_types_uncategorized.txt); no raw date cast.
# GetState is an actual native selector with Building-first AND region fallback;
# the physical Company+0x64 is a Building ID, never a State ID. GetBuilding/
# GetState.GetID semantics are not assumed; use only their native names here.
FIELDS = (
    Field("name", "Company.GetName", "text"),
    Field("name_no_formatting", "Company.GetNameNoFormatting", "text"),
    Field("establishment_date", "Company.GetEstablishmentDate.GetString", "text"),
    Field("type_key", "Company.GetCompanyType.GetKey", "text"),
    Field("type_name", "Company.GetCompanyType.GetNameNoFormatting", "text"),
    Field("country_name", "Company.GetCountry.GetNameNoFormatting", "text"),
    Field("building_name", "Company.GetBuilding.GetNameNoFormatting", "text"),
    Field("state_name", "Company.GetState.GetNameNoFormatting", "text"),
    Field("hq_profit_description", "Company.GetHQProfitDescription", "text"),
    Field("productivity", "Company.GetProductivity", "fixed"),
    Field("prosperity", "Company.GetProsperity", "fixed"),
    Field("prosperity_change", "Company.GetProsperityChange", "fixed"),
    Field("target_prosperity", "Company.GetTargetProsperity", "fixed"),
    Field("profit", "Company.GetProfit", "fixed"),
    Field("is_prosperous", "Company.IsProsperous", "boolean"),
    # Definition bonus text exists even when prosperity is below activation;
    # these are separate original observations, not an inferred active effect.
    Field("definition_bonus_description",
        "Company.GetCompanyType.GetProsperityModifier.GetDesc", "text"),
    Field("target_prosperity_breakdown",
        "Company.GetTargetProsperityBreakdown", "text"),
    Field("target_prosperity_description",
        "Company.GetTargetProsperityDesc", "text"),
    Field("prosperity_change_description",
        "Company.GetProsperityChangeDesc", "text"),
    Field("owned_building_levels", "Company.GetNumOwnedBuildingLevels", "integer", "levels"),
    Field("employed_building_levels", "Company.GetNumEmployedBuildingLevels", "integer", "levels"),
)

_KINDS = {"text": (data.Kind.STRING,), "integer": (data.Kind.I32, data.Kind.U32,
    data.Kind.I64, data.Kind.U64), "fixed": (data.Kind.FIXED,),
    "boolean": (data.Kind.BOOL,)}


def _cell(field, cell, page):
    reasons = []
    row = {"source": field.expression, "unit": field.unit, "value": None,
        "native_value": None, "available": False, "complete": False,
        "error": None, "kind": None, "type_id": None, "raw_bits": None,
        "snapshot": b"", "total": None, "binding_error": None}
    if cell is None:
        reasons.append("native projection did not copy this field")
    else:
        row.update(error=cell.error, kind=int(cell.kind), type_id=cell.type_id,
            raw_bits=cell.raw_bits, snapshot=cell.snapshot, total=cell.total,
            binding_error=cell.binding_error, binding_mode=cell.binding_mode,
            binding_kind=cell.binding_kind,
            binding_native_type_name=cell.binding_native_type_name)
        if cell.error:
            reasons.append(f"native getter error {cell.error}")
        if cell.binding_error:
            reasons.append(f"native binding error {cell.binding_error}")
        if not cell.complete:
            reasons.append(f"incomplete native copy ({len(cell.snapshot)}/{cell.total} bytes)")
        if cell.kind not in _KINDS[field.kind]:
            reasons.append(f"native kind {int(cell.kind)} is not {field.kind}")
        else:
            try:
                if field.kind == "text":
                    try:
                        value = cell.snapshot.decode("utf-8", "strict")
                    except UnicodeDecodeError:
                        value = cell.snapshot.decode("utf-8", "replace")
                        reasons.append("native string contains invalid UTF-8")
                else:
                    value = cell.value
                row["native_value"] = value
                row["value"] = fixed_decimal(value) if field.kind == "fixed" else value
                row["available"] = True  # Zero/empty and short/error copies are real observations.
            except (ValueError, TypeError, OverflowError) as error:
                reasons.append(f"native value decoding failed: {error}")
    if page.error:
        reasons.append(f"native projection error {page.error}")
    if not page.count_valid:
        reasons.append("native source count is unavailable")
    row["complete"] = row["available"] and not reasons
    if reasons:
        row["reason"] = "; ".join(reasons)
    return row


def _page_meta(page):
    return {"world": page.world, "error": page.error, "count_valid": page.count_valid,
        "source_count": page.source_count, "start": page.start, "row_count": page.row_count,
        "next": page.next, "has_more": page.has_more,
        "execution_thread_id": page.execution_thread_id, "fields_count": page.fields_count,
        "steps": page.steps, "completed_steps": page.completed_steps}


def _data_options(ctx):
    return {"adapter": ctx.adapters["data"], "process": ctx.process}


def _apply_page(answer, page, batch):
    answer["pages"].append(_page_meta(page))
    if page.error:
        answer["reasons"].append(f"native retained Company projection error {page.error}")
    if page.world != answer["world"]:
        answer["reasons"].append("retained Company projection world differs from selected country world")
    if page.row_count != len(batch) or not page.count_valid:
        answer["reasons"].append("retained Company projection row count unavailable/incomplete")
    cells = {(cell.row, cell.field): cell for cell in page.cells}
    for local_index, record in enumerate(batch):
        for column, field in enumerate(FIELDS):
            observed = _cell(field, cells.get((local_index, column)), page)
            record["values"][field.name] = observed["value"]
            record["observations"][field.name] = observed
            record[field.name] = observed["value"]
            if "reason" in observed:
                record["reasons"][field.name] = observed["reason"]
        record["complete"] = (page.world == answer["world"] and
            page.row_count == len(batch) and page.count_valid and page.error == 0 and
            record.get("country_relation_complete", False) and
            all(row["complete"] for row in record["observations"].values()))


async def list_companies(ctx, *, max_pages=None):
    """Read real full Company IDs and fields from the SAME retained owner.

    Original Country.GetCompanies is evaluated once per call. Its list_count
    gives native total, with no redundant source preview or command validator.
    max_pages is an OPTIONAL explicit caller page budget. By default, visit
    every native element. Separate 64-row copied projection pages are not an
    atomic save. An error carries `company_observations` with copied prefixes.
    """
    if max_pages is not None and (type(max_pages) is not int or max_pages < 1):
        raise ValueError("max_pages must be a positive integer or None")
    await ctx.refresh()
    actor = await ctx.country_id()
    answer = {"country_id": actor, "world": ctx.world,
        "source": "Country.GetCompanies", "source_count": None,
        "native_count": None, "next": 0, "truncated": False,
        "companies": [], "pages": [], "complete": False,
        "contract_complete": False, "reasons": [],
        "snapshot": "live native list with retained elements; pages are not an atomic save",
        "bonus_semantics": "definition_bonus_description is the CompanyType modifier definition; "
            "is_prosperous is the separate native active flag; target/change descriptions "
            "explain prosperity, not guaranteed currently active bonus effects",
        "establishment_status": "Company.GetEstablishmentDate -> Date.GetString original CString (not a guessed raw date layout)"}
    binding = data.ObjectBinding.country(await ctx.type_id("Country"), actor)
    try:
        answer["projection_supported"] = await _projection_supported(ctx)
        await _retained_rows(ctx, answer, binding,
            max_rows=None if max_pages is None else max_pages * data.MAX_PAGE_ROWS)
    except BaseException as error:
        error.company_observations = answer
        raise
    answer["complete"] = (not answer["reasons"] and not answer["truncated"] and
        answer["native_count"] is not None and
        answer["native_count"] == len(answer["companies"]) and
        all(record["company_id"] is not None and record["complete"]
            for record in answer["companies"]))
    required = ("name", "type_key", "establishment_date")
    answer["contract_complete"] = (not answer["truncated"] and
        not answer["reasons"] and
        answer["native_count"] is not None and
        len(answer["companies"]) == answer["native_count"] and
        all(record["company_id"] is not None and record["country_id"] is not None and
            all(record["observations"].get(name, {}).get("complete") for name in required)
            for record in answer["companies"]))
    return answer


async def _retained_rows(ctx, answer, binding, *, max_rows):
    read = _Read(CountryAPI(ctx))
    opts = _data_options(ctx)
    try:
        source = await read.query("economy.company.owned.source", lambda: data.evaluate(
            ctx.native, "Country.GetCompanies", (binding,), **opts))
        answer["owned_source"] = {"error": source.error, "kind": int(source.kind),
            "value_handle": source.value_handle}
        if source.error:
            answer["reasons"].append(f"native owned Company source error {source.error}; retained list owner preserved")
        if not source.value_handle or source.kind != data.Kind.LIST:
            answer["reasons"].append(f"native owned Company list owner unavailable: kind {source.kind}")
            return
        count = await read.query("economy.company.owned.count", lambda: data.list_count(
            ctx.native, source.value_handle, **opts))
        answer["native_count"] = count.total if not count.error else None
        answer["source_count"] = answer["native_count"]
        if count.error:
            answer["reasons"].append(f"native owned Company count error {count.error}")
            return
        if max_rows is not None and count.total > max_rows:
            answer["truncated"] = True
            answer["reasons"].append(f"explicit page budget reached: next={max_rows} total={count.total}")
        company_type_id = await read.type_id("Company") if count.total else None
        batch, bindings = [], []
        async def flush():
            if not batch:
                return
            await _company_object_relations(ctx, answer, read, batch)
            if not answer.get("projection_supported", True):
                for record, company_binding in zip(batch, bindings):
                    await _fallback_fields(ctx, answer, read, record, company_binding)
                batch.clear()
                bindings.clear()
                return
            try:
                page = await _projection_page(ctx, "economy.company.retained",
                    tuple(field.expression for field in FIELDS), tuple((row,) for row in bindings))
            except BaseException as error:
                # The original projection query keeps its owner; the read must
                # NOT release a referenced native Company on uncertain failure.
                read.uncertain = True
                partial = getattr(getattr(error, "query", None), "partial_result", None)
                if partial is not None:
                    _apply_page(answer, partial, batch)
                raise
            _apply_page(answer, page, batch)
            batch.clear()
            bindings.clear()

        for index in range(count.total if max_rows is None else min(count.total, max_rows)):
            result = await read.query("economy.company.owned.element", lambda index=index:
                data.list_element(ctx.native, source.value_handle, index, **opts))
            record = {"provider_index": index, "company_id": None,
                "identity_source": "owned CDataCompany+0x10 full uint32 ID",
                "identity_error": result.error, "kind": int(result.kind),
                "country_id": None, "country_error": None, "company_type_pointer": None,
                "native_type_name": result.native_type_name,
                "values": {}, "observations": {}, "reasons": {}, "complete": False}
            answer["companies"].append(record)
            answer["next"] = index + 1
            native = result.value if not result.error else None
            if (result.error or not isinstance(native, data.NativeObject)
                    or not native.pointer or not result.value_handle or
                    "CPdxPtr@VCDataCompany" not in native.native_type_name):
                record["reasons"]["company_id"] = "element does not carry a retained CDataCompany owner"
                continue
            observed_id = await _copy_owned_full_id(ctx, read, native.pointer)
            if observed_id == 0xffffffff:
                record["reasons"]["company_id"] = "retained wrapper reports invalid Company reference"
                continue
            record["company_id"] = observed_id
            batch.append(record)
            bindings.append(data.ObjectBinding(company_type_id, result.value_handle, retained=True))
            if len(batch) == data.MAX_PAGE_ROWS:
                await flush()
        await flush()
    finally:
        try:
            await read.close()
        except BaseException as error:
            answer["cleanup_warning"] = str(error)
            ctx.diagnostics.append({"label": "economy.company.cleanup", "error": error, "read": read})
        if read.owners or read.uncertain:
            retained = getattr(ctx, "_company_observation_reads", None)
            if retained is None:
                ctx._company_observation_reads = retained = []
            if read not in retained:
                retained.append(read)


async def _company_object_relations(ctx, answer, read, batch):
    """Typed raw Company source supplies full-ID Country and definition pointer.

    A separate read in the same world, not a second list/source or a validator.
    Preserve each per-ID native error rather than infer a Country from Data.GetID.
    """
    ids = tuple(row["company_id"] for row in batch)
    result = await read.query("economy.company.owned.relations", lambda: objects.read_ids(
        ctx.native, objects.Domain.COMPANY, ids, adapter=ctx.adapters["objects"],
        process=ctx.process))
    answer.setdefault("object_pages", []).append({"world": result.world,
        "error": result.error, "returned_count": result.returned_count,
        "requested_ids": ids, "next_index": result.next_index,
        "truncated": result.truncated})
    page_complete = (result.world == answer["world"] and
        not result.error and not result.truncated and
        result.returned_count == len(ids) and len(result.records) == len(ids))
    if not page_complete:
        answer["reasons"].append("native Company relation page header/count incomplete; copied valid records retained")
    # READ_IDS should use request order, but map by the actual copied full ID so
    # a short/error page cannot shift its remaining rows onto other Companies.
    by_id = {}
    for native in result.records:
        by_id.setdefault(native.requested_id, []).append(native)
    for record in batch:
        matches = by_id.get(record["company_id"], ())
        if len(matches) != 1:
            record["reasons"]["country_id"] = (
                "native Company relation record missing" if not matches else
                "duplicate native Company relation records for full ID")
            continue
        native = matches[0]
        record["native_company"] = {"requested_id": native.requested_id,
            "resolved_id": native.resolved_id, "error": native.error,
            "country_id": native.country_id, "country_error": native.country_error,
            "company_type_pointer": native.company_type_pointer,
            "company_type_error": native.company_type_error,
            "world": result.world}
        if native.error or native.resolved_id != record["company_id"]:
            record["reasons"]["country_id"] = "typed native Company full ID invalid/mismatched"
            continue
        if native.country_error:
            record["reasons"]["country_id"] = f"native Company Country relation error {native.country_error}"
        else:
            record["country_id"] = native.country_id
            record["country_relation_complete"] = page_complete
        if native.company_type_error:
            record["reasons"]["company_type_pointer"] = (
                f"native CompanyType relation error {native.company_type_error}")
        else:
            record["company_type_pointer"] = native.company_type_pointer
    if result.world != answer["world"]:
        # Keep every copied native row/error as historical evidence, but never
        # project old borrowed Company wrappers in a newer world.
        read.uncertain = True
        raise RuntimeError("Company relation world changed; original owned source retained")


async def _fallback_fields(ctx, answer, read, record, binding):
    """Old-adapter scalar fallback on this same retained element, never re-evaluate a source."""
    from types import SimpleNamespace
    opts = _data_options(ctx)
    page = SimpleNamespace(error=0, count_valid=True)
    for field in FIELDS:
        result = await read.query("economy.company.owned." + field.name,
            lambda field=field: data.evaluate(ctx.native, field.expression, (binding,),
                output_bytes=4096, **opts))
        copied = data.ProjectionCell(0, 0, result.error, result.kind, result.type_id,
            result.raw_bits, result.snapshot, result.total)
        observed = _cell(field, copied, page)
        record["observations"][field.name] = observed
        record["values"][field.name] = observed["value"]
        record[field.name] = observed["value"]
        if "reason" in observed:
            record["reasons"][field.name] = observed["reason"]
    record["complete"] = (record.get("country_relation_complete", False) and
        all(row["complete"] for row in record["observations"].values()))


def _decode_company_copy(payload):
    """Reject short inline output BEFORE shared CallbackResult releases its owner.

    NativeCallResult only decodes its fixed header, so a short four-byte output
    would otherwise escape as a completed AccessResult and free the original
    callback allocation. Raising in this decoder retains that original query.
    """
    offset = native_call.REQUEST.size + 3 * 8
    if len(payload) < offset + 4:
        error = ValueError(f"Company inline full-ID copy short ({max(0, len(payload) - offset)}/4 bytes)")
        error.partial_bytes = payload[offset:]
        error.native_call_result = (native_call.NativeCallResult.from_bytes(payload)
            if len(payload) >= native_call.REQUEST.size else None)
        raise error
    return native_call.NativeCallResult.from_bytes(payload)


async def _copy_owned_full_id(ctx, read, pointer):
    """One owned Access payload: inline memcpy output + original query owner.

    CDataCompany+0x10 is a proved full uint32 instance ID. The Access callback
    owns NativeCall request, arguments and four-byte output in ONE allocation.
    No external output buffer can outlive its original callback by accident.
    """
    memcpy = getattr(ctx, "_company_observation_memcpy", None)
    if memcpy is None:
        memcpy = await ctx.blocking(ctx.process.resolve_export, "ntdll.dll", "memcpy")
        ctx._company_observation_memcpy = memcpy
    callback = getattr(ctx, "_company_observation_call", None)
    if callback is None:
        callback = await ctx.blocking(resolve_v3_export, ctx.process,
            ctx.adapters["call"], "PdxNativeCallInterface")
        ctx._company_observation_call = callback
    offset = native_call.REQUEST.size + 3 * 8
    initial = native_call.encode_request(memcpy, (0, pointer + 0x10, 4),
        request_address=0) + bytes(4)
    def prepare(address):
        output = address + offset
        return native_call.encode_request(memcpy, (output, pointer + 0x10, 4),
            request_address=address) + bytes(4)
    result = await read.query("economy.company.ids.copy", lambda: v3_access.submit_callback(
        ctx.native, callback, initial, mode=1, flag=0, dispatch="interface",
        adapter=ctx.adapters["access"], call_adapter=ctx.adapters["call"],
        process=ctx.process, prepare_payload=prepare,
        decode_payload=_decode_company_copy))
    callee = result.callee_result
    if result.error or callee is None or callee.error:
        ctx.diagnostics.append({"label": "economy.company.ids.copy",
            "native_result": result, "callee_result": callee})
        raise RuntimeError("retained Company full-ID native copy error; original query kept")
    copied = result.payload[offset:offset + 4]
    if len(copied) != 4:
        ctx.diagnostics.append({"label": "economy.company.ids.copy",
            "native_result": result, "partial_bytes": copied})
        read.uncertain = True  # Incomplete copy; do not release its dependent Company owner.
        raise RuntimeError(f"Company full-ID decoded copy short ({len(copied)}/4 bytes)")
    value = int.from_bytes(copied, "little")
    if callee.rax != result.opaque + offset:
        ctx.diagnostics.append({"label": "economy.company.ids.copy",
            "native_result": result, "callee_rax": callee.rax,
            "copied_id": value, "warning": "memcpy return register differs from inline output"})
    return value


COMPANY_TYPE_CATALOG_FIELDS = (Field("key", "CompanyType.GetKey", "text"),)


def decode_company_type_catalog(page):
    """Decode copied full-source rows only; this function performs no native I/O.

    Input must come from the canonical full CompanyType source API once that
    API is implemented. A generic/preset Page or this decoder's existence does
    not prove that producer is available. No new SourceKind/serializer is used
    here. The full source has no live next-page request; only an original copied
    query may finish a partial byte copy. Rows may be sliced after full copying.
    """
    from pdx_runtime.v3_game._read import _record
    count = page.source_count if page.count_valid else None
    answer = {"world": page.world, "source_count": count,
        "source_count_raw": page.source_count, "count_valid": page.count_valid,
        "native_error": page.error, "start": page.start,
        "row_count": page.row_count, "has_more": page.has_more,
        "rows": [], "raw_cells": page.cells, "reasons": [],
        "positions_complete": False, "complete": False,
        "missing_position_ranges": None,
        "source": "canonical full pre-filter CompanyType source required",
        "snapshot": "copied-only full-source result; no database/array lease",
        "continuation": "finish only the original copied query; never request a new live page"}
    if page.error:
        answer["reasons"].append(f"native full-source error {page.error}")
    if not page.count_valid:
        answer["reasons"].append("native full-source count unavailable; raw count bits preserved")
    if page.start != 0 or page.grouped:
        answer["reasons"].append("full CompanyType source is standalone, not live/grouped pagination")
    if page.fields_count != len(COMPANY_TYPE_CATALOG_FIELDS):
        answer["reasons"].append("copied field count differs from requested native key schema")
    by_row = {}
    for cell in page.cells:
        by_row.setdefault(cell.row, []).append(cell)
    valid_positions = []
    for index, cells in sorted(by_row.items()):
        row = {"source_index": index, "world": page.world,
            "world_consistent": True, "values": {}, "observations": {},
            "reasons": {}, "raw_cells": tuple(cells), "complete": False,
            "position_valid": count is not None and 0 <= index < count}
        keys = [cell for cell in cells if cell.field == 0]
        _record(row, COMPANY_TYPE_CATALOG_FIELDS[0], keys[0] if keys else None,
            page_error=page.error, count_valid=page.count_valid)
        if len(keys) > 1:
            row["reasons"]["_duplicate_key_cell"] = "multiple copied key cells for one original position"
        if any(cell.field != 0 for cell in cells):
            row["reasons"]["_unexpected_field"] = "copied columns outside the requested native key schema"
        if not row["position_valid"]:
            row["reasons"]["_position"] = "copied position outside the validated source range"
        else:
            valid_positions.append(index)
        row["key"] = row["values"].get("key")
        row["key_bytes"] = row["observations"]["key"]["snapshot"]
        row["complete"] = bool(row["position_valid"] and not row["reasons"]
            and row["observations"]["key"]["complete"])
        answer["rows"].append(row)
    if count is not None:
        # Compact gaps preserve missing positions without allocating N fake rows
        # for an invalid count or interrupted copied allocation. No healthy N is
        # clipped to the ordinary Page's64-row default budget.
        gaps, cursor = [], 0
        for index in valid_positions:
            if index > cursor:
                gaps.append((cursor, index))
            cursor = index + 1
        if cursor < count:
            gaps.append((cursor, count))
        answer["missing_position_ranges"] = tuple(gaps)
        answer["positions_complete"] = bool(page.start == 0 and not page.grouped
            and page.row_count == count and len(valid_positions) == count
            and len(by_row) == count and not page.has_more)
        if not answer["positions_complete"]:
            answer["reasons"].append("not every original source position was copied in this snapshot")
    answer["complete"] = bool(answer["positions_complete"] and not answer["reasons"]
        and all(row["complete"] for row in answer["rows"]))
    return answer


async def read_company_types(ctx):
    """Read the canonical full native definition snapshot, including hidden types.

    This uses the actually implemented per-kind source, not three Country views.
    It needs no Country/actor/type/world preflight. The native callback owns its
    current TLS/database frame and copies all positions before it returns.
    Copied keys do not supply a retained type binding for a later create action.
    """
    from pdx_runtime.v3_game._projection import _projection_page
    answer = {"world": None, "source_count": None, "source_count_raw": None,
        "count_valid": False, "rows": [], "complete": False,
        "positions_complete": False}
    try:
        source = data.NativeSource(data.NativeSourceKind.FULL_COMPANY_TYPES)
        page = await _projection_page(ctx, "economy.company_types",
            tuple(field.expression for field in COMPANY_TYPE_CATALOG_FIELDS), ((),),
            source_expression=source, start=0, limit=0)
        answer = decode_company_type_catalog(page)
        answer["cleanup_warnings"] = [diagnostic for diagnostic in ctx.diagnostics.get("operations", ())
            if isinstance(diagnostic, dict) and diagnostic.get("result") is page
            and "cleanup_error" in diagnostic]
        return answer
    except BaseException as error:
        partial = getattr(getattr(error, "query", None), "partial_result", None)
        if isinstance(partial, data.ProjectionPage):
            answer = decode_company_type_catalog(partial)
        # Missing exact export/transport/copy remains its original error. No
        # Boolean capability/prewarm veto, fake empty catalog, fallback or retry.
        error.company_type_catalog = answer
        raise
