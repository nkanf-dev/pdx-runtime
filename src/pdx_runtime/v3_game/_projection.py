"""Shared V3 copied-page discovery and ownership; no domain selection policy."""
import asyncio
from collections.abc import Iterable
from .. import v3_data as data
from ..native_bridge import BridgeError
from ..v3_wire import resolve_v3_export


async def _projection_supported(ctx, *, grouped=False, typed_steps=False, native_fields=False,
                                native_sources=False):
    """One optional-export discovery per connected context/adapter, not per field."""
    cache = getattr(ctx, "_projection_capabilities", None)
    if cache is None:
        ctx._projection_capabilities = cache = {}
    adapter = ctx.adapters["data"]
    symbol = ("PdxV3DataProjectOriginalSourcesInterface" if native_sources else
              "PdxV3DataProjectNativeFieldsInterface" if native_fields else
              "PdxV3DataProjectTypedStepsInterface" if typed_steps else
              "PdxV3DataProjectSourcesInterface" if grouped else "PdxV3DataProjectPageInterface")
    key = (adapter, symbol) if grouped or typed_steps or native_fields or native_sources else adapter
    if key not in cache:
        # Older injected contexts need not implement the optional export seam.
        if (not callable(getattr(ctx.process, "resolve_export", None))
                or not callable(getattr(ctx, "blocking", None))):
            cache[key] = False
        else:
            async def discover():
                try:
                    await ctx.blocking(resolve_v3_export, ctx.process, adapter,
                                       symbol)
                except BridgeError as error:
                    if (error.operation == "resolve_export" and
                            error.details.get("reason") in ("export not found", "empty export")):
                        return False
                    raise
                return True
            cache[key] = asyncio.create_task(discover())
    found = cache[key]
    if isinstance(found, bool):
        return found
    try:
        available = await asyncio.shield(found)
    except Exception:
        if cache.get(key) is found:
            cache.pop(key)  # An access/transport failure is not absence.
        raise
    cache[key] = available
    return available


async def _projection_page(ctx, label, fields: Iterable[str | data.NativeField], binding_rows, *,
                           source_expression: str | data.NativeSource | None = None, start=0,
                           limit=64, steps=(), source_arguments=(),
                           sources: Iterable[str | data.NativeSource] | None = None, source_index=0,
                           cleanup_warnings=None):
    """Keep the original copied owner; optionally expose this query's cleanup warning."""
    fields = tuple(fields)
    binding_rows = tuple(tuple(row) for row in binding_rows)
    steps, source_arguments = tuple(steps), tuple(source_arguments)
    sources = None if sources is None else tuple(sources)
    grouped = {} if sources is None else {"sources": sources, "source_index": source_index}
    query = None
    def submit():
        nonlocal query
        try:
            query = data.project_page(ctx.native, fields, binding_rows,
                source_expression=source_expression, start=start, limit=limit,
                steps=steps, source_arguments=source_arguments,
                adapter=ctx.adapters["data"], process=ctx.process, **grouped)
        except Exception as error:
            query = getattr(error, "query", None)
            raise
        return query
    try:
        page = await ctx.query(label, submit)
    except Exception as error:
        if query is not None:
            error.query = query  # Includes partial_result and original copy continuation.
            error.projection_context = {"fields": tuple(fields), "binding_rows": binding_rows,
                "source_expression": source_expression, "start": start,
                "steps": tuple(steps), "source_arguments": tuple(source_arguments),
                "limit": limit, **grouped}
        raise
    cleanup_error = getattr(query, "cleanup_error", None)
    if cleanup_error is not None:
        ctx._remember(query)
        record = {"label": label, "query": query, "cleanup_error": cleanup_error, "result": page}
        diagnostics = ctx.diagnostics
        diagnostics.append(record)
        if cleanup_warnings is not None and cleanup_warnings is not diagnostics:
            try:
                cleanup_warnings.append(record)
            except Exception as error:
                record["cleanup_warning_delivery_error"] = error
    incomplete = not page.count_valid if sources is None else not page.range_complete
    if page.error or incomplete or any(cell.error or not cell.complete for cell in page.cells):
        ctx.diagnostics.append({"label": label, "projection_page": page})
    return page


async def _projection_source_pages(ctx, label, fields: Iterable[str | data.NativeField], binding_rows,
                                   sources: Iterable[str | data.NativeSource], *,
                                   limit=64, steps=(), source_arguments=()):
    """Yield real source observations through the same serialized page owner.

    A yielded tuple is (source index, source observation, original page, cells).
    The iterator changes scheduling only. It does not merge source counts, retry
    a failed getter, or certify a cross-page snapshot.
    """
    fields, sources = tuple(fields), tuple(sources)
    binding_rows = tuple(tuple(row) for row in binding_rows)
    steps, source_arguments = tuple(steps), tuple(source_arguments)
    grouped = (len(sources) > 1 and not source_arguments
               and await _projection_supported(ctx, grouped=True,
                   typed_steps=any(getattr(step, "source_arguments", ()) for step in steps),
                   native_fields=any(isinstance(field, data.NativeField) for field in fields),
                   native_sources=any(isinstance(source, data.NativeSource) for source in sources)))
    if grouped:
        cursor = (0, 0)
        while cursor[0] < len(sources):
            page = await _projection_page(ctx, label, fields, binding_rows, sources=sources,
                source_index=cursor[0], start=cursor[1], limit=limit, steps=steps)
            for source in page.sources:
                offset = source.row_offset * page.fields_count
                cells = page.cells[offset:offset + source.row_count * page.fields_count]
                if source.error or not source.count_valid or not source.range_complete:
                    ctx.diagnostics.append({"label": label,
                        "source_expression": sources[source.source_index],
                        "source_observation": source, "projection_page": page})
                yield source.source_index, source, page, cells
            # A fatal page may stop inside a row. Its row cursor cannot tell
            # which fields already ran; keep the prefix without replaying them.
            if page.error or not page.has_more:
                break
            next_cursor = page.next_cursor
            if next_cursor is None or next_cursor <= cursor:
                ctx.diagnostics.append({"label": label, "projection_page": page,
                    "reason": "native projection pagination did not advance; copied rows are preserved"})
                break
            cursor = next_cursor
    else:
        for source_index, source in enumerate(sources):
            start = 0
            while True:
                source_label = label + "." + source if isinstance(source, str) else label + ".source"
                page = await _projection_page(ctx, source_label, fields, binding_rows,
                    source_expression=source, start=start, limit=limit,
                    steps=steps, source_arguments=source_arguments)
                yield source_index, page, page, page.cells
                # Ordinary pages also carry source-local errors here. Stop
                # this source's row continuation, not the independent sources.
                if page.error or not page.has_more:
                    break
                if page.next <= start:
                    ctx.diagnostics.append({"label": label, "source_expression": source,
                        "projection_page": page,
                        "reason": "native projection pagination did not advance; copied rows are preserved"})
                    break
                start = page.next


def _projection_value(cell):
    """Keep copied primitive/text values even beside a native error or partial."""
    if cell is None:
        return None, "native projection did not copy this field"
    reasons = [f"native getter error {cell.error}"] if cell.error else []
    if cell.kind == data.Kind.STRING:
        try:
            value = cell.snapshot.decode("utf-8", "strict")
        except UnicodeDecodeError:
            value = cell.snapshot.decode("utf-8", "replace")
            reasons.append("native string contains invalid UTF-8")
        if not cell.complete:
            reasons.append(f"native string copy is incomplete ({len(cell.snapshot)}/{cell.total} bytes)")
    else:
        value = cell.value
    return value, "; ".join(reasons) or None

