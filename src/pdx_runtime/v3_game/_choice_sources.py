"""Original retained native choice sources and copied descriptor rows.

These helpers reuse the caller's existing Run and OwnedChoices. They neither
create a lifetime ledger nor choose UI/game policy. Raw/inline values remain
original rejected results; only the catalog admits actual owning native values.
"""
from __future__ import annotations

from .. import v3_data as data
from ._run import owned_binding, rejected
from . import _run


async def _choice_capture(run, catalog, label, factory):
    # Only an actual failed native submission/read marks transport unknown.
    # A returned native error is copied below, not changed into unknown work.
    query = None
    def submit():
        nonlocal query
        try:
            query = factory()
        except Exception as error:
            query = getattr(error, "query", None)
            raise
        return query
    try:
        return await run.capture(label, submit)
    except Exception as error:
        if query is not None or isinstance(error, (OSError, TimeoutError)):
            run.retain_unknown = True
        if query is not None:
            error.query = query
        error.choices = catalog
        raise


async def _choice_evaluate(run, catalog, expression, bindings):
    value = await _choice_capture(run, catalog, expression, lambda: data.evaluate(
        run.native, expression, tuple(bindings), output_bytes=65536,
        adapter=run.adapters["data"], process=run.process))
    if value.error:
        catalog.metadata["result"]["reasons"].append({"expression": expression, "native_result": value})
    return value


async def _choice_elements(run, catalog, expression, bindings):
    """Walk the actual owned source; raw elements are reported, never retagged."""
    source = await _choice_evaluate(run, catalog, expression, bindings)
    report = catalog.metadata["result"]
    if not isinstance(source.value, data.NativeList) or not source.value_handle:
        report["reasons"].append({"expression": expression, "reason": "Native source did not produce a retained list", "native_result": source})
        return
    adapter = run.value_adapters[source.value_handle]
    count = await _choice_capture(run, catalog, expression + ".count", lambda: data.list_count(
        run.native, source.value_handle, adapter=adapter, process=run.process))
    report["sources"].append({"expression": expression, "count_result": count})
    if count.error or type(count.total) is not int or count.total < 0:
        report["reasons"].append({"expression": expression, "reason": "Native list count is unavailable", "native_result": count})
        return
    for index in range(count.total):
        element = await _choice_capture(run, catalog, expression + ".element", lambda index=index: data.list_element(
            run.native, source.value_handle, index, adapter=adapter, process=run.process))
        if element.error:
            report["reasons"].append({"expression": expression, "source_index": index, "native_result": element})
        yield index, element


def _choice_binding(run, catalog, value, named_type, type_id, source, index=None):
    try:
        return owned_binding(run, value, named_type)
    except (RuntimeError, TypeError, ValueError) as error:
        catalog.metadata["result"]["rejected_rows"].append({"source": source, "source_index": index,
            "native_result": value, "reason": str(error)})
        return None


async def _copy_owned_rows(run, catalog, fields, candidates, roots=(), *, report=None):
    """Copy at most64 actual retained bindings per callback, grouped by adapter.

    The row number only identifies a fixed submitted binding-row table. It is
    never used to re-resolve a mutable native source for a selected action.
    """
    from ._political_data import _field_value
    from ._text import display_field
    report = catalog.metadata["result"] if report is None else report
    report.setdefault("native_pages", [])
    report.setdefault("reasons", [])
    grouped = {}
    for candidate in candidates:
        adapter = run.value_adapters.get(candidate[0].value_handle, run.adapters["data"])
        grouped.setdefault(adapter, []).append(candidate)
    copied = []
    for adapter, group in grouped.items():
        for start in range(0, len(group), 64):
            batch = group[start:start + 64]
            query = None
            def submit():
                nonlocal query
                query = data.project_page(run.native, tuple(field.expression for field in fields),
                    tuple((*roots, binding) for _, binding, _ in batch), limit=64,
                    adapter=adapter, process=run.process)
                return query
            page = await _choice_capture(run, catalog, "choice.descriptors", submit)
            report["native_pages"].append(page)
            if page.error or not page.count_valid or page.fields_count != len(fields) or page.row_count != len(batch):
                report["reasons"].append({"reason": "Native descriptor page is incomplete", "native_page": page})
            warning = getattr(query, "cleanup_error", None)
            if warning is not None:
                run.ctx._remember(query)
                run.ctx.diagnostics.append({"label": "choice.descriptors", "query": query,
                    "cleanup_error": warning, "result": page})
                report.setdefault("cleanup_warnings", []).append(warning)
            for index, candidate in enumerate(batch):
                cells = page.rows[index] if index < len(page.rows) else ()
                cells_by_field = {cell.field: cell for cell in cells}
                row = {"reasons": {}, "source": candidate[2]}
                for position, field in enumerate(fields):
                    value, reason = _field_value(field, cells_by_field.get(position))
                    if field.text:
                        row.update(display_field(value, field.name))
                    else:
                        row[field.name] = value
                    if reason:
                        row["reasons"][field.name] = reason
                row["complete"] = not row["reasons"] and not page.error
                copied.append((candidate, row))
    return copied


def _finish_choice_report(catalog):
    report = catalog.metadata["result"]
    report["native_results"] = tuple(catalog.owner.operations)
    report["admitted_count"] = len(catalog)
    report["complete"] = not report["reasons"] and not report["rejected_rows"] and all(choice.get("complete") is True for choice in catalog)
    report["status"] = "observed" if report["complete"] else "partial" if len(catalog) else "unavailable"
    return catalog

