"""Copied Goods definitions from the selected country's original Market source.

An ordinal in GetGoodsSortedByBalance is a search position, never a Goods ID.
The source is scope-local; its absence does not prove absence from game definitions.
"""
from __future__ import annotations

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game.country import CountryAPI, _Read
from pdx_runtime.v3_game._projection import _projection_source_pages, _projection_supported
from pdx_runtime.v3_game._read import Field, _record, _world, copy_fields, finish_read

SOURCE = "Country.GetMarket.GetGoodsSortedByBalance"
FIELDS = (
    Field("key", "Goods.GetKey", "text"),
    Field("name", "Goods.GetName", "text"),
    Field("base_price", "Goods.GetBasePrice", "fixed"),
    Field("category", "Goods.GetCategoryName", "text"),
    Field("is_local", "Goods.IsLocal", "boolean"),
    Field("is_tradeable", "Goods.IsTradeable", "boolean"),
    Field("demand_use_description", "Goods.GetNeedLevelsContainingThisDesc", "text"),
)


def _candidate(answer, index, *, source_index=0):
    row = {"source_index": source_index, "provider_index": index, "values": {},
        "observations": {}, "reasons": {}, "world": answer["world"],
        "world_consistent": answer["world_consistent"], "cleanup_warnings": [],
        "key": None, "complete": False}
    answer["candidates"].append(row)
    return row


def _binding_reason(cell):
    if cell is None:
        return "native source element binding was not copied"
    if cell.binding_error:
        return f"native source element binding error {cell.binding_error}"
    if cell.binding_mode not in (2, 3):
        return f"native source element binding mode {cell.binding_mode} is not a list element"
    if cell.binding_kind != data.Kind.OBJECT:
        return f"native source element kind {cell.binding_kind} is not an object"
    if not cell.binding_native_type_name or "CPdxPtr@VCDataGoods" not in cell.binding_native_type_name:
        return f"native source element type {cell.binding_native_type_name!r} is not Goods"
    return None


def _finish_candidate(row, answer):
    row["key"] = row["values"].get("key")
    row["complete"] = (row["world_consistent"] and all(
        row["observations"].get(field.name, {}).get("complete") for field in FIELDS)
        and not row["reasons"])
    if row["observations"].get("key", {}).get("complete") and not row["reasons"].get("_binding"):
        if row["key"] == answer["requested_key"]:
            answer["selected"] = row
            for field in FIELDS:
                answer[field.name] = row["values"].get(field.name)
                answer["values"][field.name] = row["values"].get(field.name)
                if field.name in row["observations"]:
                    answer["observations"][field.name] = row["observations"][field.name]
                if field.name in row["reasons"]:
                    answer["reasons"][field.name] = row["reasons"][field.name]
            answer["available"] = True


def _source_meta(source_index, source, page):
    # Ordinary ProjectionPage.range_complete belongs to the grouped wire format
    # and defaults to False. Its real source cursor is count_valid/next/has_more.
    grouped = page.grouped
    range_complete = (source.range_complete if grouped else
        source.error == 0 and page.error == 0 and source.count_valid)
    finished = source.finished if grouped else source.count_valid and not page.has_more
    return {"source_index": source_index, "source": SOURCE, "error": source.error,
        "source_produced": getattr(source, "source_produced", None),
        "count_valid": source.count_valid, "source_count": source.source_count,
        "start": source.start, "row_count": source.row_count,
        "next": source.next, "has_more": page.has_more,
        "finished": finished, "range_complete": range_complete,
        "world": page.world, "page_error": page.error,
        "execution_thread_id": page.execution_thread_id}


def _apply_page(answer, source_index, source, page, cells):
    meta = _source_meta(source_index, source, page)
    answer["source_observations"].append(meta)
    _world(answer, page.world)
    if source.error or page.error or not source.count_valid or not meta["range_complete"]:
        answer["reasons"].setdefault("_source", []).append(
            f"source/page error {source.error}/{page.error}; count_valid={source.count_valid}; "
            f"range_complete={meta['range_complete']}")
    by_row = {}
    for cell in cells:
        by_row.setdefault(cell.row, {})[cell.field] = cell
    # Cells copied before an interrupted row-count header are still evidence.
    for index in sorted(set(range(source.start, source.start + source.row_count)) | set(by_row)):
        row = _candidate(answer, index, source_index=source_index)
        row["world"] = page.world
        row["world_consistent"] = answer["world_consistent"]
        selected = by_row.get(index, {})
        # These columns are all from this one source element in this one callback.
        binding = _binding_reason(selected.get(0))
        if binding:
            row["reasons"]["_binding"] = binding
        for column, field in enumerate(FIELDS):
            _record(row, field, selected.get(column), page_error=page.error,
                count_valid=source.count_valid)
            if binding and column in selected:
                row["observations"][field.name]["source_binding_reason"] = binding
        _finish_candidate(row, answer)
        if answer["available"]:
            break
    if (not page.has_more and source.count_valid and source.error == 0
            and page.error == 0 and meta["range_complete"]):
        answer["source_complete"] = True


async def _fallback(ctx, answer):
    """One _Read owns the Country source, its elements and every scalar owner."""
    read = _Read(CountryAPI(ctx))
    options = {"adapter": ctx.adapters["data"], "process": ctx.process}
    try:
        country_type = await read.type_id("Country")
        goods_type = await read.type_id("Goods")
        binding = data.ObjectBinding.country(country_type, answer["country_id"])
        source = await read.query("economy.goods_definition.source", lambda: data.evaluate(
            ctx.native, SOURCE, (binding,), **options))
        answer["source_observations"].append({"source": SOURCE, "error": source.error,
            "kind": int(source.kind), "value_handle": source.value_handle,
            "world": read.world})
        _world(answer, read.world)
        if source.error:
            answer["reasons"].setdefault("_source", []).append(f"native source error {source.error}")
        if source.kind != data.Kind.LIST or not source.value_handle:
            answer["reasons"].setdefault("_source", []).append(
                "native Market Goods source has no retained list owner")
            return
        count = await read.query("economy.goods_definition.count", lambda: data.list_count(
            ctx.native, source.value_handle, **options))
        answer["source_observations"].append({"source": SOURCE, "count_error": count.error,
            "count": count.total if not count.error else None, "world": read.world})
        _world(answer, read.world)
        if count.error:
            answer["reasons"].setdefault("_source", []).append(f"native source count error {count.error}")
            return
        for index in range(count.total):
            element = await read.query("economy.goods_definition.element", lambda index=index:
                data.list_element(ctx.native, source.value_handle, index, **options))
            row = _candidate(answer, index)
            row["world"] = read.world
            _world(row, read.world)
            row["element"] = {"error": element.error, "kind": int(element.kind),
                "value_handle": element.value_handle,
                "native_type_name": element.native_type_name}
            if element.error:
                row["reasons"]["_element"] = f"native element error {element.error}"
            if (not element.value_handle or element.kind != data.Kind.OBJECT or
                    not isinstance(element.value, data.NativeObject) or
                    not element.value.pointer or
                    "CPdxPtr@VCDataGoods" not in element.native_type_name):
                row["reasons"]["_binding"] = "element does not carry a retained CPdxPtr@VCDataGoods owner"
                continue
            goods_binding = data.ObjectBinding(goods_type, element.value_handle, retained=True)
            try:
                await copy_fields(ctx, read, row, FIELDS, (goods_binding,),
                    label="economy.goods_definition")
            finally:
                # Even an interrupted later field leaves an already copied exact
                # key and its same-row prefix available on the original exception.
                _world(answer, read.world)
                _finish_candidate(row, answer)
            if answer["available"]:
                break
        else:
            answer["source_complete"] = source.error == 0
    finally:
        await finish_read(ctx, read, answer, label="economy.goods_definition.cleanup")


async def read_goods_definition(ctx, key):
    """Search exact native Goods.GetKey in the selected country's Market source.

    A missing result is scoped to this Market source, not the global catalog.
    An interrupted query carries goods_observations and its original query.
    """
    if type(key) is not str or not key or "\x00" in key:
        raise ValueError("key must be a nonempty native string without NUL")
    await ctx.refresh()
    country_id = await ctx.country_id()
    answer = {"requested_key": key, "key": None, "country_id": country_id,
        "world": ctx.world, "world_consistent": True, "source": SOURCE,
        "scope": "selected country's current Market.GetGoodsSortedByBalance",
        "source_complete": False, "available": False, "complete": False,
        "name": None, "base_price": None, "category": None,
        "is_local": None, "is_tradeable": None, "demand_use_description": None,
        "values": {}, "observations": {}, "reasons": {}, "sources": {
            field.name: field.expression for field in FIELDS},
        "candidates": [], "source_observations": [], "selected": None,
        "cleanup_warnings": [],
        "snapshot": "separate live source pages; no cross-page atomicity"}
    try:
        if await _projection_supported(ctx):
            binding = data.ObjectBinding.country(await ctx.type_id("Country"), country_id)
            iterator = _projection_source_pages(ctx, "economy.goods_definition",
                tuple(field.expression for field in FIELDS), ((binding,),), (SOURCE,))
            try:
                async for source_index, source, page, cells in iterator:
                    _apply_page(answer, source_index, source, page, cells)
                    if answer["available"]:
                        break
            except BaseException as error:
                partial = getattr(getattr(error, "query", None), "partial_result", None)
                if isinstance(partial, data.ProjectionPage):
                    _apply_page(answer, 0, partial, partial, partial.cells)
                raise
        else:
            await _fallback(ctx, answer)
    except BaseException as error:
        answer["complete"] = _complete(answer)
        error.goods_observations = answer
        raise
    answer["complete"] = _complete(answer)
    if not answer["available"]:
        answer["reason"] = ("No exact complete native Goods.GetKey was found in this source"
            if answer["source_complete"] else
            "Goods.GetKey was not proved in this source; the source was incomplete")
    return answer


def _complete(answer):
    if not answer["available"]:
        return bool(answer["source_complete"] and answer["world_consistent"]
            and not answer["reasons"] and
            all(not row["reasons"] for row in answer["candidates"]))
    return bool(answer["world_consistent"] and answer["selected"]["complete"]
        and not answer["reasons"].get("_source") and not answer["cleanup_warnings"])
