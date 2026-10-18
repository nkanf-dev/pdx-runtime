"""Company discovery, native actions and independent readback."""
from __future__ import annotations

from pdx_runtime import v3_data as data, v3_player_control as player
from pdx_runtime.v3_game.country import CountryAPI, _Read
from pdx_runtime.v3_game.economy.company_observations import list_companies, _copy_owned_full_id
from pdx_runtime.v3_game import _run

# These original Country methods are zero-argument native lists, historically
# return_type_id 70. Actual retained element type is checked for EACH element.
# GetPotential/Attainable are not asserted to be a global definition catalog.
_CREATE_SOURCES = ("Country.GetPotentialCompanies", "Country.GetAttainableCompanies",
    "Country.GetAvailableCompanies")
_REMOVAL_SOURCE = "Country.GetCompanies"


def _options(ctx):
    return {"adapter": ctx.adapters["data"], "process": ctx.process}


def _retained(ctx, read, reason):
    """Use the existing economy retention ledger, not a new command owner pool."""
    ledger = getattr(ctx, "_economy_retained", None)
    if ledger is None:
        ctx._economy_retained = ledger = []
    if not any(isinstance(row, dict) and row.get("company_action_read") is read
               for row in ledger):
        ledger.append({"company_action_read": read, "reason": reason})


async def _controlled(ctx, country):
    observed = await ctx.query("economy.company.player", lambda: player.observe_player(
        ctx.native, adapter=ctx.adapters["console"], process=ctx.process))
    return not (observed.error or observed.controlled_country_error or observed.observer_enabled
        or observed.world != ctx.world or observed.controlled_country_id != country)


async def _source(ctx, read, country_binding, expression):
    """Copy a native collection once; retain its original source for all elements."""
    result = await read.query("economy.company.action.source", lambda: data.evaluate(
        ctx.native, expression, (country_binding,), output_bytes=4096, **_options(ctx)))
    source_error = (f"{expression}: native source error {result.error}"
        if result.error else None)
    if result.kind != data.Kind.LIST or not result.value_handle:
        return None, None, (f"{expression}: no retained LIST owner; native error {result.error}, "
            f"kind {result.kind}, handle {result.value_handle}")
    # An error-bearing source can still carry an original owned LIST. Preserve
    # its error; count and check real typed elements rather than discard it.
    count = await read.query("economy.company.action.count", lambda: data.list_count(
        ctx.native, result.value_handle, **_options(ctx)))
    if count.error or type(count.total) is not int or count.total < 0:
        return result, None, (f"{expression}: native count error {count.error}; "
            f"copied count {count.total}; source error {result.error}")
    return result, count.total, source_error


async def _key(ctx, read, binding, expression, observations):
    # Caller installs this mutable recorder in public sources BEFORE querying.
    # A snapshot failure leaves the already-copied prefix in the same result.
    copied = await read.query("economy.company.action.key", lambda: data.evaluate(
        ctx.native, expression, (binding,), output_bytes=4096, **_options(ctx)))
    while True:
        observations.append({"source": expression, "error": copied.error,
            "kind": int(copied.kind), "value_handle": copied.value_handle,
            "snapshot": copied.snapshot, "total": copied.total})
        if (copied.error != data.ERROR_MORE_DATA or not copied.value_handle
                or copied.total <= len(copied.snapshot)):
            break
        # Enlarge the exact original Any, not its getter; a failed or stalled
        # copy preserves the earlier observation rather than replaying a query.
        previous = copied
        copied = await read.query("economy.company.action.key.snapshot", lambda:
            data.snapshot(ctx.native, previous.value_handle, output_bytes=previous.total,
                **_options(ctx)))
        if len(copied.snapshot) <= len(previous.snapshot):
            observations.append({"source": expression, "error": copied.error,
                "kind": int(copied.kind), "value_handle": copied.value_handle,
                "snapshot": copied.snapshot, "total": copied.total})
            break
    # Only a fully copied successful UTF-8 getter proves the requested key.
    value = None
    if (not copied.error and copied.kind == data.Kind.STRING
            and copied.total == len(copied.snapshot)):
        try:
            value = copied.snapshot.decode("utf-8", "strict")
        except UnicodeDecodeError:
            pass
    return value


async def _find_create(ctx, read, country_binding, key, sources):
    type_id = None
    found = None
    for expression in _CREATE_SOURCES:
        source, total, reason = await _source(ctx, read, country_binding, expression)
        entry = {"source": expression, "count": total, "reason": reason}
        sources.append(entry)
        if total is None:
            continue
        for index in range(total):
            element = await read.query("economy.company.action.element", lambda index=index:
                data.list_element(ctx.native, source.value_handle, index, **_options(ctx)))
            obj = element.value if not element.error else None
            if (not element.value_handle or not isinstance(obj, data.NativeObject)
                    or not obj.pointer or "CPdxPtr@VCDataCompanyType" not in obj.native_type_name):
                entry.setdefault("unverified_elements", []).append({"index": index,
                    "error": element.error, "kind": int(element.kind),
                    "value_handle": element.value_handle,
                    "native_type_name": element.native_type_name})
                continue
            if type_id is None:
                type_id = await read.type_id("CompanyType")
            binding = data.ObjectBinding(type_id, element.value_handle, retained=True)
            key_record = {"index": index, "observations": []}
            entry.setdefault("unverified_keys", []).append(key_record)
            observed = await _key(ctx, read, binding, "CompanyType.GetKey",
                key_record["observations"])
            if observed is not None:
                entry["unverified_keys"].remove(key_record)
            if observed == key:
                found = binding
                entry["matched_index"] = index
                break
        if found is not None:
            return found, expression
    return None, None


async def _find_owned(ctx, read, country_binding, key, sources, *, operation):
    """One Country.GetCompanies source and retained Company per actual typed key.

    For remove, same-owner +0x10 supplies the real selected full ID. Create
    can reuse the original Company.GetCompanyType promote as a definition
    argument if no candidate definition list provided that key; the game's
    own producer/validator decides whether another instance is allowed.
    """
    source, total, reason = await _source(ctx, read, country_binding, _REMOVAL_SOURCE)
    entry = {"source": _REMOVAL_SOURCE, "count": total, "reason": reason}
    sources.append(entry)
    if total is None:
        return None, None, None
    company_type_id = None
    matched = []
    for index in range(total):
        element = await read.query("economy.company.action.element", lambda index=index:
            data.list_element(ctx.native, source.value_handle, index, **_options(ctx)))
        obj = element.value if not element.error else None
        if (not element.value_handle or not isinstance(obj, data.NativeObject)
                or not obj.pointer or "CPdxPtr@VCDataCompany" not in obj.native_type_name
                or "CPdxPtr@VCDataCompanyType" in obj.native_type_name):
            entry.setdefault("unverified_elements", []).append({"index": index,
                "error": element.error, "kind": int(element.kind),
                "value_handle": element.value_handle,
                "native_type_name": element.native_type_name})
            continue
        if company_type_id is None:
            company_type_id = await read.type_id("Company")
        binding = data.ObjectBinding(company_type_id, element.value_handle, retained=True)
        key_record = {"index": index, "observations": []}
        entry.setdefault("unverified_keys", []).append(key_record)
        observed = await _key(ctx, read, binding, "Company.GetCompanyType.GetKey",
            key_record["observations"])
        if observed is not None:
            entry["unverified_keys"].remove(key_record)
        if observed != key:
            continue
        if operation == "create":
            # Only the original CompanyType parameter is needed to ask the
            # native CreateCompany validator. A second live-ID copy would be
            # an unrelated precondition and could hide a valid native action.
            matched.append((None, binding))
            entry["matched_index"] = index
            break
        try:
            original_id = await _copy_owned_full_id(ctx, read, obj.pointer)
        except Exception as error:
            if read.uncertain:
                raise  # Exact pending query must settle before touching this owner.
            entry.setdefault("unverified_ids", []).append({"index": index, "error": str(error)})
            continue
        if original_id == 0xffffffff:
            entry.setdefault("unverified_ids", []).append({"index": index,
                "error": "invalid original Company full ID"})
            continue
        matched.append((original_id, binding))
        entry.setdefault("matched_ids", []).append(original_id)
    if not matched:
        return None, None, None
    if operation == "remove" and len(matched) != 1:
        entry["reason"] = "More than one proved live Company has this type key; native removal target ambiguous"
        return None, None, None
    entry["source_partial"] = bool(entry.get("reason") or any(
        entry.get(name) for name in ("unverified_elements", "unverified_keys", "unverified_ids")))
    return matched[0][1], _REMOVAL_SOURCE, matched[0][0]


def _readback_membership(observed, country, world, current_world, key):
    """A proved matching row establishes presence without a complete list.

    Absence requires a complete trusted source and every row's ID/owner/key.
    Names, dates and unrelated partial rows never erase proved presence.
    The caller separately preserves the entire original native observation.
    """
    if (observed.get("world") != world or current_world != world
            or observed.get("country_id") != country):
        return None, [], "Company readback country/world changed"
    companies = observed.get("companies", ())
    proved = [row["company_id"] for row in companies
        if row.get("country_id") == country and row.get("country_relation_complete")
        and type(row.get("company_id")) is int
        and row.get("observations", {}).get("type_key", {}).get("complete")
        and row.get("type_key") == key]
    if proved:
        return True, proved, None  # Positive membership needs only its original row.
    if (observed.get("truncated") or observed.get("native_count") is None
            or observed["native_count"] != len(companies) or observed.get("reasons")):
        return None, [], "Company list incomplete; type-key absence not proved"
    if (any(row.get("country_id") != country or not row.get("country_relation_complete")
            or type(row.get("company_id")) is not int
            or not row.get("observations", {}).get("type_key", {}).get("complete")
            for row in companies)
            or len({row["company_id"] for row in companies}) != len(companies)):
        return None, [], "Original typed Company ID, owner or type-key absence not proved"
    return False, [], None


async def change_company(ctx, key, operation, *, submit_generated):
    """Change a named CompanyType only when its real source/owner is proven.

    submit_generated is the bound EconomyAPI._submit_generated method. It owns
    unique native clone, original serialize/validate, controlled flags4 submit,
    and pending-command retention. This caller keeps the source Any alive until
    that unique owner settles. There is no retry of unknown native operations.
    """
    if type(key) is not str or not key or "\x00" in key:
        raise ValueError("key must be a complete native CompanyType key")
    if operation not in ("create", "remove"):
        raise ValueError("operation must be create or remove")
    if not callable(submit_generated):
        raise TypeError("submit_generated must be the bound native command helper")
    await ctx.refresh()
    country = await ctx.country_id()
    world = ctx.world
    result = {"country_id": country, "world": world, "type_key": key,
        "operation": operation, "accepted": False, "effect_observed": None,
        "membership_observed": None,
        "source_coverage": "country-scoped native lists; not all CompanyType definitions",
        "sources": [], "cleanup_warnings": []}
    if not await _controlled(ctx, country):
        return {**result, "allowed": False, "reason": "You do not currently control this country"}
    # No separate all-fields owned-Company preflight. Native source/identity
    # and selected definition live in one retained read until command cloning.
    read = _Read(CountryAPI(ctx))
    uncertain = False
    try:
        country_binding = data.ObjectBinding.country(await read.type_id("Country"), country)
        if operation == "create":
            binding, source_name = await _find_create(ctx, read, country_binding, key, result["sources"])
            expression = "Country.CreateCompany(CompanyType.Self)"
            if binding is None:
                binding, source_name, _ = await _find_owned(ctx, read,
                    country_binding, key, result["sources"], operation="create")
                if binding is not None:
                    expression = "Country.CreateCompany(Company.GetCompanyType)"
        else:
            binding, source_name, selected_id = await _find_owned(ctx, read,
                country_binding, key, result["sources"], operation="remove")
            expression = "Country.RemoveCompany(Company.GetCompanyType)"
            if binding is not None:
                result["selected_company_id"] = selected_id
        if binding is None:
            result["reason"] = ("No complete matching retained CompanyType owner in these "
                "country-scoped sources; definition absence or permanent unavailability is not proved"
                if operation == "create" else "Original owned Company key/ID source did not prove a unique removal target")
            return result
        result["selected_source"] = source_name
        result["source_partial"] = any(entry.get("reason") or any(entry.get(label)
            for label in ("unverified_elements", "unverified_keys", "unverified_ids"))
            for entry in result["sources"])
        if ctx.world != world or read.world != world:
            result["reason"] = "Country/world changed before native Company command generation"
            return result
        # Keep source Any and exact retained element alive for command producer.
        generated = await read.query("economy.company.action.generate", lambda:
            data.evaluate(ctx.native, expression, (country_binding, binding),
                output_bytes=4096, **_options(ctx)))
        result["producer"] = {"source": expression, "error": generated.error,
            "kind": int(generated.kind), "value_handle": generated.value_handle}
        if generated.error or not isinstance(generated.value, data.NativeDataCommand):
            result["reason"] = "Original Company producer did not copy a native command"
            return result
        if ctx.world != world or read.world != world:
            result["reason"] = "Country/world changed before command clone"
            return result
        request = {"country_id": country, "type_key": key, "operation": operation}
        try:
            answer = await submit_generated(generated, read.owners, request)
        except BaseException:
            # _submit_generated may already retain a unique owner borrowing this
            # very list. Do not release its original source/element/command Any.
            read.uncertain = uncertain = True
            raise
        uncertain = bool(answer.pop("_retain_inputs", False))
        if uncertain:
            read.uncertain = True
        result.update(answer)
        result["ownership_pending"] = uncertain
    except BaseException as error:
        # Do not replace the original exception, query or pending owner. Attach
        # the exact public prefix accumulated before this failing query.
        try:
            error.company_action_result = result
        except (AttributeError, TypeError):
            pass
        if read.uncertain or uncertain:
            _retained(ctx, read, "Company action original query or unique command owner uncertain")
        raise
    finally:
        try:
            await read.close()
        except BaseException as cleanup:
            _retained(ctx, read, "Company action original owner cleanup failed")
            if not isinstance(cleanup, Exception):
                raise  # Do not turn cancellation into apparent action success.
            result["cleanup_warnings"].append(str(cleanup))
        if read.owners or read.uncertain:
            _retained(ctx, read, "Company action retained native inputs")
    if result.get("accepted") is True:
        try:
            observed = await list_companies(ctx)
            result["readback"] = observed  # Full native evidence, including partial/errors.
            present, matched_ids, readback_reason = _readback_membership(
                observed, country, world, ctx.world, key)
            result["membership_observed"] = present
            result["after_company_ids"] = matched_ids
            if present is None:
                result["readback_reason"] = readback_reason
            else:
                # Effect here means observed desired membership, not proof of
                # causal creation/removal by this particular accepted command.
                result["effect_observed"] = (present if operation == "create" else not present)
        except Exception as error:
            # Accepted receiver result is primary. The readback's copied prefix
            # and original error/query remain visible, never another submission.
            result["readback"] = getattr(error, "company_observations", None)
            result["readback_reason"] = f"Independent Company readback failed: {error}"
    return result


async def rename_company(ctx, company_id, new_name, *, run_factory):
    """Rename one full Company ID through the existing shared command owner.

    The original factory uses the native Company-ID and name fields. Native
    rules decide legality. Independent name readback does not replace the
    command result or its cleanup information.
    """
    from pdx_runtime import v3_commands as commands
    if type(company_id) is not int or not 0 <= company_id <= 0xffffffff:
        raise ValueError("company_id must be a full uint32 native Company ID")
    if type(new_name) is not str:
        raise TypeError("new_name must be a native string")
    if not callable(run_factory):
        raise TypeError("run_factory must be the existing shared Run")
    await ctx.refresh()
    run = run_factory(ctx, "economy.rename_company", namespace="economy")
    answer = {"action": "rename_company", "company_id": company_id,
        "requested_name": new_name, "world": run.world, "accepted": None,
        "effect_observed": None, "name_no_formatting": None, "readback": None}
    try:
        actor = await ctx.country_id()
        run.actor = actor
        answer["country_id"] = actor
        created = await run.capture("command.create", lambda: commands.create(
            run.native, 24072, actor_id=actor, world=run.world,
            parameters={23675: commands.UInt32(company_id), 27: new_name},
            adapter=run.adapters["commands"], process=run.process))
        answer["creation"] = created
        if created.error or not created.command:
            # A partial original unique command is not adopted, fabricated or
            # discarded. finish retains this same scope for later settlement.
            if created.command:
                run.unadopted_command = created
            answer.update(status="native_result", native_result=created,
                native_error=created.error)
        else:
            owner = run.adopt(created)
            if await run.validate_submit(owner, answer):
                try:
                    observed = await list_companies(ctx)
                    answer["readback"] = observed
                    matches = [row for row in observed.get("companies", ())
                        if row.get("company_id") == company_id]
                    if len(matches) == 1:
                        row = matches[0]
                        name = row.get("observations", {}).get("name_no_formatting", {})
                        answer["name_observation"] = name
                        answer["name_no_formatting"] = name.get("value")
                        # Only this same-ID native name is needed. Unrelated
                        # list fields, dates, names and cleanup are not gates.
                        if (name.get("complete") and
                                observed.get("world") == answer["world"]):
                            answer["effect_observed"] = name.get("value") == new_name
                        else:
                            answer["readback_reason"] = "native name copy incomplete or readback world changed"
                    else:
                        answer["readback_reason"] = "fresh source did not prove one matching full Company ID"
                except Exception as error:
                    answer["readback"] = getattr(error, "company_observations", None)
                    answer["readback_error"] = error
                    answer["readback_reason"] = str(error)
    except BaseException as error:
        error.company_action_result = answer
        try:
            await run.finish(answer)
        except BaseException as cleanup:
            # Keep the original exception/query, not a replacement cleanup error.
            answer["cleanup_exception"] = cleanup
        raise
    try:
        return await run.finish(answer)
    except BaseException as error:
        # A directly cancelled owning task may stop known-owner cleanup. Keep
        # its existing public prefix on the same error; never run finish twice.
        error.company_action_result = answer
        raise
