"""Owned native warfare observations used by the high-level military API.

These are original DataFactory providers, not UI selection state. A front in a
war is an observed front, not proof that a particular army can deploy to it.
"""
from __future__ import annotations

from ... import v3_data as data, v3_commands as commands
from .._text import display_field
from .._values import fixed_decimal
from .._run import KnownResultError, SourceUnavailable, command_field
from .. import _run


class WarfareRead(_run.Run):
    """One owning source graph. Unknown child work never releases its parents."""
    def __init__(self, api, label):
        super().__init__(api._ctx, label, namespace="military")
        self.api = api
        self.types = {}
        self.providers = {}
        self.public_result = None

    async def type_id(self, name):
        if name not in self.types:
            known = getattr(self.ctx, "_types", {})
            self.types[name] = known[name] if name in known else await self.ctx.type_id(name)
        return self.types[name]

    async def functions(self, named_type):
        if named_type not in self.metadata:
            type_id = await self.type_id(named_type)
            if not hasattr(self.api, "_warfare_functions"):
                self.api._warfare_functions = {}
            key = (self.world, type_id)
            if key not in self.api._warfare_functions:
                self.api._warfare_functions[key] = await super().functions(type_id)
            self.metadata[named_type] = self.api._warfare_functions[key]
        return self.metadata[named_type]

    async def country(self):
        if self.actor is None:
            self.actor = await self.ctx.country_id()
        return data.ObjectBinding.country(await self.type_id("Country"), self.actor)

    async def page_available(self, *, typed_steps=False, native_fields=False):
        from .._projection import _projection_supported
        cache = getattr(self.ctx, "_projection_capabilities", None)
        if cache is None:
            self.ctx._projection_capabilities = cache = {}
        self._projection_capabilities = cache
        return await _projection_supported(self, typed_steps=typed_steps,
                                           native_fields=native_fields)

    async def page(self, fields, bindings=(), *, source_expression=None, start=0,
                   limit=64, steps=(), source_arguments=()):
        from .._projection import _projection_page
        page = await _projection_page(self, self.namespace + ".page", tuple(fields), (tuple(bindings),),
            source_expression=source_expression, start=start, limit=limit,
            steps=steps, source_arguments=source_arguments)
        return page

    @staticmethod
    def cell_value(cell):
        from .._projection import _projection_value
        return _projection_value(cell)

    async def provider(self, named_type, method, arguments=0):
        key = (named_type, method, arguments)
        if key not in self.providers:
            method_row = (await self.functions(named_type)).get(method)
            if not self.api._pure_read_function(method_row, arguments):
                raise SourceUnavailable(f"{named_type}.{method}: current metadata has no pure {arguments}-argument provider")
            self.providers[key] = method_row
        return self.providers[key]

    async def field(self, named_type, method, bindings, kinds, *, arguments=0, expression=None):
        expression = expression or named_type + "." + method
        try:
            await self.provider(named_type, method, arguments)
            result = await self.evaluate(expression, bindings)
            kind = result.kind
            # DataResult.value deliberately rejects incomplete strings. A read
            # model still exposes the actual copied prefix beside its reason.
            value = result.snapshot.decode("utf-8", "replace") if kind == data.Kind.STRING else result.value
            if kind not in kinds:
                return (fixed_decimal(value) if isinstance(value, data.FixedPoint) else value,
                        expression + ": native result did not return the requested scalar kind")
            valid = (type(value) is bool if kind == data.Kind.BOOL else
                     type(value) is int if kind in (data.Kind.I32, data.Kind.U32, data.Kind.I64) else
                     isinstance(value, str) if kind == data.Kind.STRING else
                     isinstance(value, data.FixedPoint))
            if not valid:
                raise SourceUnavailable(expression + ": decoded value differs from its native scalar kind")
            if kind == data.Kind.STRING and (len(result.snapshot) != result.total
                    or result.snapshot_bytes != result.total):
                return value, expression + ": native text snapshot is partial"
            return fixed_decimal(value) if kind == data.Kind.FIXED else value, None
        except (SourceUnavailable, KnownResultError, LookupError) as error:
            if isinstance(error, KnownResultError):
                partial = error.result
                if data.Kind.EMPTY <= partial.kind <= data.Kind.STRING:
                    try:
                        value = (partial.snapshot.decode("utf-8", "replace")
                                 if partial.kind == data.Kind.STRING else partial.value)
                    except (ValueError, TypeError):
                        return None, str(error)
                    reason = str(error)
                    if partial.kind == data.Kind.STRING and (
                            len(partial.snapshot) != partial.total
                            or partial.snapshot_bytes != partial.total):
                        reason += "; native text snapshot is partial"
                    return fixed_decimal(value) if isinstance(value, data.FixedPoint) else value, reason
            return None, str(error)

    async def fields(self, named_type, bindings, specs, *, expression_prefix=None):
        """Copy scalar fields in native pages; keep cell-level partials and errors."""
        specs = tuple((method, key, kinds if isinstance(kinds, tuple) else (kinds,))
                      for method, key, kinds in specs)
        row = {"unavailable": {}}
        prefix = expression_prefix or named_type
        if not await self.page_available():
            for method, key, kinds in specs:
                row[key], reason = await self.field(named_type, method, bindings, kinds,
                    expression=prefix + "." + method)
                if reason:
                    row["unavailable"][key] = reason
            return row
        row["native_pages"] = []
        for start in range(0, len(specs), 32):
            chunk = specs[start:start + 32]
            page = await self.page(tuple(prefix + "." + method for method, _, _ in chunk),
                                   bindings, limit=1)
            row["native_pages"].append(page)
            cells = {cell.field: cell for cell in page.cells if cell.row == 0}
            for column, (method, key, kinds) in enumerate(chunk):
                cell = cells.get(column)
                value, reason = self.cell_value(cell)
                if cell is not None and cell.kind not in kinds:
                    reason = (reason + "; " if reason else "") + "native result did not return the requested scalar kind"
                if isinstance(value, data.FixedPoint):
                    value = fixed_decimal(value)
                if page.error:
                    reason = (reason + "; " if reason else "") + f"native page error {page.error}"
                row[key] = value
                if reason:
                    row["unavailable"][key] = reason
        return row

    async def elements(self, expression, bindings, named_type, *, wrapper_type=None):
        source = await self.evaluate(expression, bindings)
        if source.kind != data.Kind.LIST or not isinstance(source.value, data.NativeList) or not source.value_handle:
            raise SourceUnavailable(expression + ": original source did not return a retained native list")
        count = await self.call(expression + ".count", lambda: data.list_count(self.native,
            source.value_handle, adapter=self.adapters["data"], process=self.process))
        if type(count.total) is not int or count.total < 0:
            raise SourceUnavailable(expression + ": native list count is unavailable or invalid")
        for index in range(count.total):
            child = await self.call(expression + ".element", lambda index=index: data.list_element(
                self.native, source.value_handle, index,
                adapter=self.adapters["data"], process=self.process))
            try:
                binding = self.owned_binding(child, wrapper_type or named_type)
            except RuntimeError as error:
                # This check is local. It does not catch an unknown query result.
                raise SourceUnavailable(expression + ": " + str(error)) from error
            yield index, child, binding

    async def front(self, binding, country_binding):
        unavailable = {}
        valid, reason = await self.field("Front", "IsValid", (binding,), (data.Kind.BOOL,))
        if reason:
            raise SourceUnavailable(reason)
        if not valid:
            return None
        identity, reason = await self.field("Front", "GetID", (binding,), (data.Kind.U32, data.Kind.I32))
        identity = self.api._native_full_id(identity)
        if identity is None:
            unavailable["id"] = reason or "Front.GetID did not return a complete native ID"
        fields = await self.functions("Front")
        name_getter = next((name for name in ("GetNameNoFormatting", "GetName") if name in fields), "GetName")
        name, reason = await self.field("Front", name_getter, (binding,), (data.Kind.STRING,))
        if reason:
            unavailable["name"] = reason
        row = {"id": identity, **display_field(name), "source_country_id": self.actor,
               "coverage": "country_war_fronts", "atomic_snapshot": False,
               "id_unavailable_reason": unavailable.get("id"),
               "name_unavailable_reason": unavailable.get("name"), "unavailable": unavailable,
               "deployment_eligible": None,
               "deployment_eligible_unavailable_reason": "War front observation does not validate a particular army deployment"}
        for method, key, kinds in (
                ("GetPlayerAdvantageRaw", "player_advantage", (data.Kind.I32,)),
                ("GetPlayerAdvantageDesc", "player_advantage_description", (data.Kind.STRING,)),
                ("GetOccupationProgressDesc", "occupation_progress", (data.Kind.STRING,)),
                ("GetPlayerSideNumUnitsAtFront", "player_units_at_front", (data.Kind.I32,)),
                ("GetPlayerSideNumUnitsTraveling", "player_units_travelling", (data.Kind.I32,))):
            row[key], reason = await self.field("Front", method, (binding,), kinds)
            if reason:
                unavailable[key] = reason
        for method, key in (("HasCountry", "country_in_front"), ("IsVulnerable", "country_front_vulnerable")):
            row[key], reason = await self.field("Front", method, (binding, country_binding), (data.Kind.BOOL,),
                arguments=1, expression="Front." + method + "(Country.Self)")
            if reason:
                unavailable[key] = reason
        return row


async def country_war_fronts(api, *, country_fields=None):
    """Read every native front of the country's wars, including empty fronts.

    All war fronts remain in the result. HasCountry is a separate observation,
    not a substitute for the native deployment predicate. There is no list-wide
    command generation or validation, and no UI action is invoked.
    """
    run = WarfareRead(api, "military.war_fronts")
    if country_fields is not None:
        run.metadata["Country"] = country_fields
    records, sources, details, seen = [], [], [], set()
    root_observed = False
    try:
        country = await run.country()
        await run.provider("Country", "AccessWars")
        await run.provider("War", "AccessFronts")
        await run.provider("Front", "IsValid")
        root_observed = True
        wars = 0
        async for _, _, war in run.elements("Country.AccessWars", (country,), "War"):
            wars += 1
            count = 0
            try:
                async for _, _, front in run.elements("War.AccessFronts", (war,), "Front"):
                    count += 1
                    row = await run.front(front, country)
                    if row is None:
                        continue
                    if row["id"] is not None:
                        if row["id"] in seen:
                            continue
                        seen.add(row["id"])
                    records.append(row)
                sources.append({"source": "Country.AccessWars", "front_getter": "War.AccessFronts",
                                "count": count, "coverage": "country_war_fronts"})
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                details.append(str(error))
        if wars == 0:
            sources.append({"source": "Country.AccessWars", "count": 0, "coverage": "country_war_fronts"})
    except (SourceUnavailable, KnownResultError, LookupError) as error:
        details.append(str(error))
    # An arbitrary exception above deliberately retains the owning graph. In
    # particular, no finally releases the parent behind uncertain child work.
    observed = root_observed and not details
    result = {"status": "observed" if observed else "partial" if records else "unavailable",
              "fronts": records if observed or records else None, "sources": sources,
              "coverage": "country_war_fronts" if observed or records else None,
              "reason": None if observed else "Some original war front providers could not be observed",
              "retryable": bool(details), "details": details}
    return await _finish_read(api, run, result)


async def _finish_read(api, run, result):
    return await run.finish(result)

async def owned_read(api, label, operation):
    run = WarfareRead(api, label)
    try:
        result = await operation(run)
    except (SourceUnavailable, KnownResultError, LookupError) as error:
        result = {"status": "unavailable", "reason": str(error), "retryable": True}
        if isinstance(error, KnownResultError):
            result.update(native_result=error.result, native_error=error.result.error)
    return await _finish_action(api, run, result)


async def _related(run, binding, receiver, getter, target, *, has_identity=True):
    """Project one relation inside its original native expression, not a pointer join."""
    # These relation getters are established original Promote expressions.
    # Promote-only relations need not appear in the function enumeration. The
    # engine resolves the complete scalar chain within one callback; no borrowed
    # intermediate wrapper escapes or is retagged as an owning Any.
    prefix = receiver + "." + getter
    valid, reason = await run.field(target, "IsValid", (binding,), (data.Kind.BOOL,),
        expression=prefix + ".IsValid")
    if reason or not valid:
        return None, reason
    fields = await run.functions(target)
    name_getter = next((name for name in ("GetNameNoFormatting", "GetNameNoFlagNoConcept", "GetName")
                       if name in fields), "GetName")
    name, name_reason = await run.field(target, name_getter, (binding,), (data.Kind.STRING,),
        expression=prefix + "." + name_getter)
    result = {**display_field(name), "unavailable": {}}
    if name_reason:
        result["unavailable"]["name"] = name_reason
    if has_identity:
        identity_getter = "GetID" if "GetID" in fields else "GetIDString"
        identity, identity_reason = await run.field(target, identity_getter, (binding,),
            (data.Kind.U32, data.Kind.I32) if identity_getter == "GetID" else (data.Kind.STRING,),
            expression=prefix + "." + identity_getter)
        result["id"] = run.api._native_full_id(identity)
        if result["id"] is None:
            result["unavailable"]["id"] = identity_reason or "Native relation did not return a complete ID"
    else:
        # HQ has no registered ID getter. Its tooltip is returned as original
        # text, not parsed into a guessed target ID.
        tag, tag_reason = await run.field(target, "GetTooltipTag", (binding,), (data.Kind.STRING,),
            expression=prefix + ".GetTooltipTag")
        result["tooltip_tag"] = tag
        if tag_reason:
            result["unavailable"]["tooltip_tag"] = tag_reason
    return result, None


def _source_country_id(run):
    selected = getattr(run.api, "_source_country_id", None)
    return run.actor if selected is None else selected


async def _source_country(run):
    country = await run.country()  # records actual acting player separately
    selected = getattr(run.api, "_source_country_id", None)
    if selected is not None:
        return data.ObjectBinding.country(await run.type_id("Country"), selected)
    return country


async def _formation(run, wanted, *, kind=None):
    country = await _source_country(run)
    fields = await run.functions("MilitaryFormation")
    identity_getter = next((name for name in ("GetID", "GetIDString") if name in fields), None)
    if identity_getter is None:
        raise SourceUnavailable("MilitaryFormation has no original identity getter")
    for source_name, source_kind in (("GetMilitaryFormationsArmy", "army"), ("GetMilitaryFormationsFleet", "fleet")):
        if kind is not None and source_kind != kind:
            continue
        await run.provider("Country", source_name)
        async for _, _, binding in run.elements("Country." + source_name, (country,), "MilitaryFormation"):
            identity, reason = await run.field("MilitaryFormation", identity_getter, (binding,),
                (data.Kind.I32, data.Kind.U32) if identity_getter == "GetID" else (data.Kind.STRING,))
            if reason:
                raise SourceUnavailable(reason)
            if run.api._native_full_id(identity) == wanted:
                return source_kind, binding
    return None, None


async def formation_details(api, formation_id):
    async def observe(run):
        kind, binding = await _formation(run, formation_id)
        if binding is None:
            return {"id": formation_id, "country_id": _source_country_id(run), "status": "not_found",
                    "reason": "Formation was not present in the selected country's current army or fleet sources"}
        fields = await run.functions("MilitaryFormation")
        row = {"id": formation_id, "country_id": _source_country_id(run), "kind": kind,
               "actor_country_id": run.actor,
               "status": "observed", "atomic_snapshot": False, "unavailable": {}}
        name_getter = "GetNameNoFormatting" if "GetNameNoFormatting" in fields else "GetName"
        name, reason = await run.field("MilitaryFormation", name_getter, (binding,), (data.Kind.STRING,))
        row.update(display_field(name))
        if reason:
            row["unavailable"]["name"] = reason
        specs = (
            ('GetLocationString', 'location', data.Kind.STRING),
            ('GetFormationStatusDesc', 'formation_status', data.Kind.STRING),
            ('IsMobilized', 'mobilized', data.Kind.BOOL),
            ('IsFullyMobilized', 'fully_mobilized', data.Kind.BOOL),
            ('GetMobilizationPercentage', 'mobilization_fraction', data.Kind.FIXED),
            ('HasRaisedConscripts', 'conscripts_raised', data.Kind.BOOL),
            ('IsTraveling', 'travelling', data.Kind.BOOL),
            ('GetTravelDaysLeft', 'travel_days_left', data.Kind.I32),
            ('IsInBattle', 'in_battle', data.Kind.BOOL),
            ('IsAdvancingInBattle', 'advancing_in_battle', data.Kind.BOOL),
            ('IsDefendingInBattle', 'defending_in_battle', data.Kind.BOOL),
            ('GetNumCombatUnits', 'combat_units', data.Kind.I32),
            ('GetNumConscripts', 'conscripts', data.Kind.I32),
            ('GetCurrentManpower', 'manpower', data.Kind.I64),
            ('GetMaxManpower', 'max_manpower', data.Kind.I64),
            ('GetOrganizationPercentage', 'organization_fraction', data.Kind.FIXED),
            ('GetCurrentSupplyAmount', 'supply', data.Kind.FIXED),
            ('GetSupplyRatio', 'supply_ratio', data.Kind.FIXED),
            ('GetSupplyConsumption', 'supply_consumption', data.Kind.FIXED),
            ('GetSupplyCapacity', 'supply_capacity', data.Kind.FIXED),
            ('HasSupplyRoute', 'has_supply_route', data.Kind.BOOL),
            ('HasFullSupplyPath', 'has_full_supply_path', data.Kind.BOOL),
            ('CanDeployToFront', 'can_deploy_to_front', data.Kind.BOOL),
        ) + (
    ("GetMorale", "morale", data.Kind.FIXED),
    ("GetMoraleBreakdown", "morale_description", data.Kind.STRING),
    ("GetAverageOffense", "average_offense", data.Kind.FIXED),
    ("GetAverageDefense", "average_defense", data.Kind.FIXED),
    ("GetAverageOffenseExcludeUnavailableManpower", "effective_average_offense", data.Kind.FIXED),
    ("GetAverageDefenseExcludeUnavailableManpower", "effective_average_defense", data.Kind.FIXED),
    ("GetOffenseCommanderBreakdown", "commander_offense_description", data.Kind.STRING),
    ("GetDefenseCommanderBreakdown", "commander_defense_description", data.Kind.STRING),
    ("GetOrganizationRaw", "organization", data.Kind.FIXED),
    ("GetOrganizationTargetPercentage", "organization_target_fraction", data.Kind.FIXED),
    ("GetOrganizationDailyChange", "organization_daily_change", data.Kind.FIXED),
    ("GetOrganizationTargetDesc", "organization_target_description", data.Kind.STRING),
    ("GetOrganizationDailyChangeDesc", "organization_daily_change_description", data.Kind.STRING),
    ("GetLowOrganizationDesc", "low_organization_description", data.Kind.STRING),
    ("GetSupplyShortageRatio", "supply_shortage_ratio", data.Kind.FIXED),
    ("HasSupplyShortageOrganizationPenalty", "supply_shortage_organization_penalty", data.Kind.BOOL),
    ("GetSupplyDelivered", "supply_delivered", data.Kind.FIXED),
    ("GetSupplyConsumptionBreakdown", "supply_consumption_description", data.Kind.STRING),
    ("GetSupplyCapacityBreakdown", "supply_capacity_description", data.Kind.STRING),
    ("GetLogisticsCenterSupplyAvailability", "logistics_center_supply_availability", data.Kind.FIXED),
)
        copied = await run.fields("MilitaryFormation", (binding,), specs)
        row["unavailable"].update(copied.pop("unavailable"))
        row.update(copied)
        for method, key, target, identity in (
                ("GetCurrentFront", "current_front", "Front", True),
                ("GetTargetFront", "target_front", "Front", True),
                ("GetCurrentHQ", "current_hq", "Hq", False),
                ("GetTargetHQ", "target_hq", "Hq", False),
                ("GetHomeHQ", "home_hq", "Hq", False),
                ("GetCommander", "commander", "Character", True)):
            row[key], reason = await _related(run, binding, "MilitaryFormation", method, target,
                has_identity=identity)
            if reason:
                row["unavailable"][key] = reason
        if kind == "army":
            from .formations import _strategic_objective
            row["strategic_objective"], reason = await _strategic_objective(run, binding)
            if reason:
                row["unavailable"]["strategic_objective"] = reason
        row["status"] = "partial" if row["unavailable"] else "observed"
        return row
    return await owned_read(api, "military.formation_details", observe)

async def _token(run, name):
    if not hasattr(run.api, "_warfare_tokens"):
        run.api._warfare_tokens = {}
    cache = run.api._warfare_tokens
    if name not in cache:
        result = await run.call("warfare.token." + name, lambda: commands.resolve_token(run.native,
            name, adapter=run.adapters["commands"], process=run.process))
        cache[name] = result.type_token
    return cache[name]


async def _command_result(run, owner, action, factory):
    return await run.command_result(action, factory, owner=owner)


def _owner_unsettled(owner):
    return _run.Run.owner_unsettled(owner)

async def _finish_action(api, run, result):
    return await _finish_read(api, run, result)

async def formation_command(api, command_name, formation_id, target_id, *, validate_only=False):
    """One explicit native deployment/station choice; never a target cross product.

    Canonical fields are closed by the original native readers/writers.
    Station's original resolver is State (same database as the Objects State
    resolver), not HQ. The native validator supplies target-specific eligibility.
    """
    target_field = {"deploy_military_formation_to_front": "front",
                    "station_military_formation": "state"}[command_name]
    run = WarfareRead(api, "military." + command_name)
    result = {"formation_id": formation_id, target_field + "_id": target_id,
              "action": command_name, "accepted": None}
    try:
        owner, created = await _id_owner(run, command_name,
            {"formation": formation_id, target_field: target_id})
        if owner is None:
            result.update(status="native_result", native_error=created.error, native_result=created)
            return await _finish_action(api, run, result)
        submitted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if not submitted:
            return await _finish_action(api, run, result)
        if result["accepted"]:
            # Observing a command is not another command. This does not refresh
            # the world again or replay a mutation on an unsettled observation.
            try:
                observation = await formation_details(api, formation_id)
                result["observation"] = observation
                if target_field == "front":
                    current_front = observation.get("current_front") or {}
                    result["observed_on_requested_front"] = (current_front.get("id") == target_id
                        if current_front.get("id") is not None else None)
                result["arrived"] = None
            except Exception as error:
                # The separate reader retains its own uncertain child graph.
                # The already observed submission result remains usable.
                result["observation_error"] = str(error)
                result["arrived"] = None
    except KnownResultError as error:
        result.update(status="native_result", native_error=error.result.error,
                      native_result=error.result, reason=str(error))
    return await _finish_action(api, run, result)


async def _source_count(run, source, bindings):
    result = await run.call(source + ".count", lambda: data.source_count(run.native, source, bindings,
        world=run.world, adapter=run.adapters["data"], process=run.process))
    if not result.count_valid:
        raise SourceUnavailable(source + ": original source count was not observed")
    return result.source_count


async def _copied(run, source, index, count, expression, bindings, kinds):
    """Borrowed Creation rows stay inside one callback; only the scalar escapes."""
    try:
        result = await run.call(expression, lambda: data.evaluate_element(run.native, source, index,
            expression, bindings, world=run.world, expected_count=count, output_bytes=65536,
            adapter=run.adapters["data"], process=run.process))
        native = result.result
        value = native.snapshot.decode("utf-8", "replace") if native.kind == data.Kind.STRING else result.value
        if native.kind not in kinds:
            return (fixed_decimal(value) if isinstance(value, data.FixedPoint) else value,
                    expression + ": original getter did not return the requested scalar kind")
        if native.kind == data.Kind.STRING and (len(native.snapshot) != native.total
                or native.snapshot_bytes != native.total):
            return value, expression + ": original text snapshot is partial"
        return fixed_decimal(value) if isinstance(value, data.FixedPoint) else value, None
    except (SourceUnavailable, KnownResultError) as error:
        if isinstance(error, KnownResultError):
            partial = error.result
            native = partial.result
            if data.Kind.EMPTY <= native.kind <= data.Kind.STRING:
                try:
                    value = (native.snapshot.decode("utf-8", "replace")
                             if native.kind == data.Kind.STRING else partial.value)
                except (ValueError, TypeError):
                    return None, str(error)
                reason = str(error)
                if native.kind == data.Kind.STRING and (len(native.snapshot) != native.total
                        or native.snapshot_bytes != native.total):
                    reason += "; native text snapshot is partial"
                return fixed_decimal(value) if isinstance(value, data.FixedPoint) else value, reason
        return None, str(error)


_PARTICIPANT_SUPPORT_FIELDS = (
    ("WarParticipant.GetCountry.GetID", "country_id"),
    ("WarParticipant.GetWarSupportDesc", "war_support_description"),
    ("WarParticipant.GetWarSupportDescDelayed", "war_support_description_delayed"),
    ("WarParticipant.GetExhaustionDesc", "exhaustion_description"),
    ("WarParticipant.GetCanCapitulateDesc", "capitulation_description"),
)


async def _participant_support_rows(run, war_binding, source, side):
    """Copy five leaves from each genuine raw participant row in one Page.

    Source creation may refresh War arrays. No row escapes its callback, and no
    legacy getter runs between its fields. Native leaf transitive reentry is not
    a general purity or atomic-snapshot guarantee. Pages remain separate reads.
    """
    rows, pages, details, start = [], [], [], 0
    while True:
        page = await run.page(
            tuple(expression for expression, _ in _PARTICIPANT_SUPPORT_FIELDS),
            (war_binding,), source_expression=source, start=start, limit=64,
        )
        pages.append(page)
        if page.error or not page.count_valid:
            details.append({"start": page.start, "native_error": page.error,
                            "count_valid": page.count_valid, "source_count": page.source_count})
        for local_index, cells in enumerate(page.rows):
            columns = {cell.field: cell for cell in cells}
            cell = columns.get(0)
            raw_id, reason = run.cell_value(cell)
            id_kind = cell is not None and cell.kind in (data.Kind.I32, data.Kind.U32)
            if cell is not None and not id_kind:
                reason = (reason + "; " if reason else "") + "native Country ID kind is not int32/uint32"
            usable = (id_kind and type(raw_id) is int and not cell.error and cell.complete)
            country_id = run.api._native_full_id(raw_id) if usable else None
            if country_id is None and not reason:
                reason = "Original participant Country ID was not a complete valid native ID"
            row = {
                "country_id": country_id, "country_id_raw": raw_id,
                "country_id_cell": cell,
                "country_id_raw_bits": cell.raw_bits if cell is not None else None,
                "country_id_bytes": ((cell.raw_bits & 0xffffffff).to_bytes(4, "little")
                                     if id_kind else cell.snapshot if cell is not None else None),
                "country_id_error": cell.error if cell is not None else None,
                "side": side, "source": source, "source_index": page.start + local_index,
                "atomic_snapshot": False, "unavailable": {},
            }
            if reason:
                row["unavailable"]["country_id"] = reason
            support = {"source": source, "source_index": row["source_index"],
                       "atomic_snapshot": False, "native_cells": {}, "unavailable": {}}
            for column, (_, key) in enumerate(_PARTICIPANT_SUPPORT_FIELDS[1:], 1):
                leaf = columns.get(column)
                value, leaf_reason = run.cell_value(leaf)
                support.update(display_field(value, key))
                support["native_cells"][key] = leaf
                if leaf is not None and leaf.kind != data.Kind.STRING:
                    leaf_reason = ((leaf_reason + "; " if leaf_reason else "") +
                                   "native description kind is not CString")
                if leaf_reason:
                    support["unavailable"][key] = leaf_reason
            if page.error or not page.count_valid:
                support["unavailable"]["source"] = (
                    f"native source page error {page.error}; count_valid={page.count_valid}"
                )
            support["status"] = "partial" if support["unavailable"] else "observed"
            row["support_details"] = support
            rows.append(row)
        # A returned source error can carry a copied prefix. Do not rerun an
        # ambiguous suffix, or treat an unobserved count as a successful empty.
        if page.error or not page.count_valid or not page.has_more:
            break
        if page.next <= start:
            details.append({"start": page.start, "reason": "native participant page cursor did not advance"})
            break
        start = page.next
    membership_complete = (bool(pages) and not details and
                           all(page.count_valid and not page.error for page in pages))
    complete = membership_complete and not any(
        row["unavailable"] or row["support_details"]["unavailable"] for row in rows
    )
    return rows, {
        "source": source, "status": "observed" if complete else "partial",
        "membership_complete": membership_complete, "native_pages": pages,
        "details": details, "atomic_snapshot": False,
    }


async def _participants(run, war_binding, side, *, include_support_details=False):
    source = "War.Get" + ("Attackers" if side == "attacker" else "Defenders")
    await run.provider("War", source.split(".")[1])
    source_observation = None
    if include_support_details and await run.page_available():
        rows, source_observation = await _participant_support_rows(run, war_binding, source, side)
    else:
        count = await _source_count(run, source, (war_binding,))
        rows = []
        for index in range(count):
            raw_id, reason = await _copied(run, source, index, count,
                "WarParticipant.GetCountry.GetID", (war_binding,), (data.Kind.I32, data.Kind.U32))
            country_id = (None if include_support_details and reason else
                          run.api._native_full_id(raw_id))
            row = {"country_id": country_id, "country_id_raw": raw_id,
                   "side": side, "unavailable": {}}
            if reason or country_id is None:
                row["unavailable"]["country_id"] = reason or "Original participant Country ID was unavailable"
            if include_support_details:
                support = {"status": "unavailable", "atomic_snapshot": False,
                           "native_cells": {}, "unavailable": {
                               "source": "PdxV3DataProjectPageInterface is unavailable; no same-row detail page was submitted"
                           }}
                for _, key in _PARTICIPANT_SUPPORT_FIELDS[1:]:
                    support.update(display_field(None, key))
                row["support_details"] = support
            rows.append(row)
        if include_support_details:
            source_observation = {
                "source": source, "status": "unavailable", "membership_complete": True,
                "native_pages": [], "atomic_snapshot": False,
                "reason": "Page support is absent; separate single-leaf calls are not a same-row detail result",
            }
    # All source Page copies for this side have finished. These existing name
    # and numeric observations are independent legacy callbacks, not witnesses
    # for the five copied fields or an atomic multi-callback snapshot.
    for row in rows:
        country_id = row["country_id"]
        if country_id is None:
            continue
        country = data.ObjectBinding.country(await run.type_id("Country"), country_id)
        country_fields = await run.functions("Country")
        name_getter = "GetNameNoFormatting" if "GetNameNoFormatting" in country_fields else "GetName"
        name, reason = await run.field("Country", name_getter, (country,), (data.Kind.STRING,))
        row.update(display_field(name))
        if reason:
            row["unavailable"]["name"] = reason
        for method, key in (("GetWarSupport", "war_support"), ("GetCasualties", "casualties")):
            row[key], reason = await run.field("War", method, (war_binding, country), (data.Kind.FIXED,),
                arguments=1, expression="War." + method + "(Country.Self)")
            if reason:
                row["unavailable"][key] = reason
    return rows, source_observation


async def _war_goals(run, war_binding, side):
    method = "GetAttackersWarGoals" if side == "attacker" else "GetDefendersWarGoals"
    await run.provider("War", method)
    source = "War." + method
    count = await _source_count(run, source, (war_binding,))
    rows = []
    for index in range(count):
        row = {"source_index": index, "side": side, "atomic_snapshot": False, "unavailable": {}}
        for expression, key, kinds in (
                ("WarGoal.GetNameNoFormatting", "name", (data.Kind.STRING,)),
                ("WarGoal.GetDesc", "description", (data.Kind.STRING,)),
                ("WarGoal.GetStatusString", "goal_status", (data.Kind.STRING,)),
                ("WarGoal.GetHolder.GetID", "holder_country_id", (data.Kind.I32, data.Kind.U32)),
                ("WarGoal.GetTarget.GetID", "target_country_id", (data.Kind.I32, data.Kind.U32)),
                ("WarGoal.IsContested", "contested", (data.Kind.BOOL,)),
                ("WarGoal.IsExecuted", "executed", (data.Kind.BOOL,)),
                ("WarGoal.IsPressedInCurrentPeaceDeal", "pressed_in_current_peace", (data.Kind.BOOL,))):
            row[key], reason = await _copied(run, source, index, count, expression, (war_binding,), kinds)
            if key.endswith("_id"):
                row[key + "_raw"] = row[key]
                row[key] = run.api._native_full_id(row[key])
                if row[key] is None and not reason:
                    reason = expression + ": original getter did not return a complete ID"
            if reason:
                row["unavailable"][key] = reason
        row.update(display_field(row.pop("name")))
        rows.append(row)
    return rows


async def country_wars(api, wanted=None, *, include_support_details=False):
    async def observe(run):
        country = await run.country()
        await run.provider("Country", "AccessWars")
        rows, failures = [], []
        async for _, element, binding in run.elements("Country.AccessWars", (country,), "War"):
            # Existing static CDataWar reference ABI: owned wrapper+0x10 is its
            # full uint32 War ref. The same native resolver consumes this field.
            war_id = await run.origin(element, "War")
            if wanted is not None and war_id != wanted:
                continue
            row = {"id": war_id, "country_id": run.actor, "coverage": "country_wars",
                   "atomic_snapshot": False, "unavailable": {}}
            fields = await run.functions("War")
            name_getter = "GetNameNoFormatting" if "GetNameNoFormatting" in fields else "GetName"
            name, reason = await run.field("War", name_getter, (binding,), (data.Kind.STRING,))
            row.update(display_field(name))
            row["name_unavailable_reason"] = reason
            if reason:
                row["unavailable"]["name"] = reason
            for method, key in (("IsParticipant", "country_is_participant"),
                    ("IsAttacker", "country_is_attacker"), ("IsDefender", "country_is_defender"),
                    ("IsWarLeader", "country_is_leader")):
                row[key], reason = await run.field("War", method, (binding, country), (data.Kind.BOOL,),
                    arguments=1, expression="War." + method + "(Country.Self)")
                if reason:
                    row["unavailable"][key] = reason
            for method, key, kind in (
                    ("AllowsNegotiatedPeace", "allows_negotiated_peace", data.Kind.BOOL),
                    ("HasActivePeaceDeal", "has_active_peace_deal", data.Kind.BOOL),
                    ("HasActiveWhitePeaceDeal", "has_active_white_peace", data.Kind.BOOL),
                    ("IsPeaceDealValid", "peace_deal_valid", data.Kind.BOOL),
                    ("GetPeaceDealValidationDesc", "peace_validation_description", data.Kind.STRING),
                    ("GetPressedWarGoalsString", "pressed_war_goals_description", data.Kind.STRING)):
                row[key], reason = await run.field("War", method, (binding,), (kind,))
                if reason:
                    row["unavailable"][key] = reason
            participants, complete = [], True
            goals = {}
            support_sources = {}
            for side in ("attacker", "defender"):
                try:
                    side_rows, source_observation = await _participants(
                        run, binding, side, include_support_details=include_support_details
                    )
                    participants.extend(side_rows)
                    if source_observation is not None:
                        support_sources[side] = source_observation
                        if not source_observation["membership_complete"]:
                            complete = False
                            row["unavailable"][side + "_participants"] = (
                                "Original participant source page is partial; inspect copied pages and prefix"
                            )
                except (SourceUnavailable, KnownResultError, LookupError) as error:
                    complete = False
                    row["unavailable"][side + "_participants"] = str(error)
                    if include_support_details:
                        support_sources[side] = {"source": "War.Get" + (
                            "Attackers" if side == "attacker" else "Defenders"),
                            "status": "unavailable", "membership_complete": False,
                            "reason": str(error), "native_pages": [], "atomic_snapshot": False}
                        if isinstance(error, KnownResultError):
                            support_sources[side]["native_result"] = error.result
                try:
                    goals[side] = await _war_goals(run, binding, side)
                except (SourceUnavailable, KnownResultError, LookupError) as error:
                    goals[side] = None
                    row["unavailable"][side + "_war_goals"] = str(error)
            if include_support_details:
                row["participant_support_sources"] = support_sources
            row["participants"] = participants if complete or participants else None
            row["participant_coverage"] = "native_side_lists" if complete else "partial_native_side_lists"
            row["participants_unavailable_reason"] = None if complete else "Some original participant rows could not be read"
            row["war_goals"] = goals
            rows.append(row)
            if wanted is not None:
                break
        return {"status": "observed" if rows or wanted is None else "not_found", "wars": rows,
                "coverage": "country_wars", "details": failures}
    return await owned_read(api, "military.war_details", observe)


async def battles(api, *, war_id=None):
    async def observe(run):
        country = await run.country()
        await run.provider("Country", "AccessWars")
        await run.provider("War", "GetActiveBattles")
        rows, failures, matched_war = [], [], False
        async for _, element, binding in run.elements("Country.AccessWars", (country,), "War"):
            current_war = await run.origin(element, "War")
            if war_id is not None and current_war != war_id:
                continue
            matched_war = True
            source = "War.GetActiveBattles"
            try:
                count = await _source_count(run, source, (binding,))
            except (SourceUnavailable, KnownResultError) as error:
                failures.append(str(error))
                continue
            for index in range(count):
                row = {"war_id": current_war, "source_index": index, "atomic_snapshot": False,
                       "id": None, "id_unavailable_reason": "Current Battle API has no native ID getter",
                       "unavailable": {}}
                for expression, key, kinds in (
                        ("Battle.GetName", "name", (data.Kind.STRING,)),
                        ("Battle.GetTooltipTag", "tooltip_tag", (data.Kind.STRING,)),
                        ("Battle.GetStatusString", "battle_status", (data.Kind.STRING,)),
                        ("Battle.IsActive", "active", (data.Kind.BOOL,)),
                        ("Battle.HasEnded", "ended", (data.Kind.BOOL,)),
                        ("Battle.GetAttacker.GetID", "attacker_country_id", (data.Kind.I32, data.Kind.U32)),
                        ("Battle.GetDefender.GetID", "defender_country_id", (data.Kind.I32, data.Kind.U32)),
                        ("Battle.GetFront.GetID", "front_id", (data.Kind.I32, data.Kind.U32)),
                        ("Battle.GetDurationInDaysRaw", "duration_days", (data.Kind.I32,)),
                        ("Battle.CalcCurrentAttackerManpower", "attacker_manpower", (data.Kind.I64,)),
                        ("Battle.CalcCurrentDefenderManpower", "defender_manpower", (data.Kind.I64,)),
                        ("Battle.CalcCurrentAttackerMorale", "attacker_morale", (data.Kind.FIXED,)),
                        ("Battle.CalcCurrentDefenderMorale", "defender_morale", (data.Kind.FIXED,)),
                        ("Battle.GetAttackerTotalDead", "attacker_dead", (data.Kind.FIXED,)),
                        ("Battle.GetDefenderTotalDead", "defender_dead", (data.Kind.FIXED,)),
                        ("Battle.GetOccupationWon", "occupation_won", (data.Kind.STRING,))):
                    row[key], reason = await _copied(run, source, index, count, expression, (binding,), kinds)
                    if key.endswith("_id"):
                        row[key + "_raw"] = row[key]
                        row[key] = run.api._native_full_id(row[key])
                        if row[key] is None and not reason:
                            reason = expression + ": original getter did not return a complete ID"
                    if reason:
                        row["unavailable"][key] = reason
                row.update(display_field(row.pop("name")))
                rows.append(row)
        return {"status": "partial" if failures and rows else "unavailable" if failures else
                          "not_found" if war_id is not None and not matched_war else "observed",
                "battles": rows if rows or not failures else None, "coverage": "active_country_war_battles",
                "war_found": matched_war if war_id is not None else None, "details": failures}
    return await owned_read(api, "military.battles", observe)


async def _validate_submit(run, owner, result, *, validate_only=False):
    return await run.validate_submit(owner, result, validate_only=validate_only)

async def _character(run, wanted):
    country = await _source_country(run)
    fields = await run.functions("Country")
    for method in ("AccessGenerals", "AccessAdmirals"):
        if method not in fields:
            continue
        await run.provider("Country", method)
        async for _, _, binding in run.elements("Country." + method, (country,), "Character"):
            identity, reason = await run.field("Character", "GetID", (binding,), (data.Kind.I32, data.Kind.U32))
            if reason:
                raise SourceUnavailable(reason)
            if run.api._native_full_id(identity) == wanted:
                return binding
    raise SourceUnavailable(f"Character {wanted} was not present in the country's current general/admiral sources")


async def _produced_owner(run, expression, bindings):
    return await run.generated_owner(expression, bindings)

async def _definition_key(run, owner, field_name):
    # Only serialize an original POPULATED SelectOrder result. Serializing the
    # empty default factory can dereference a null order definition in the game.
    serialized = await _command_result(run, owner, "definition.serialize", owner.serialize)
    if serialized.error:
        raise KnownResultError("definition.serialize", serialized)
    if _owner_unsettled(owner):
        raise SourceUnavailable("Original populated command serialization ownership is unresolved; its owner is retained")
    token = await _token(run, field_name)
    try:
        key = command_field(commands.decode_command(serialized.data), token)
    except (RuntimeError, ValueError) as error:
        raise SourceUnavailable("Original populated definition key could not be decoded: " + str(error)) from error
    if not isinstance(key, str):
        raise SourceUnavailable("Original populated command did not serialize its definition key as text")
    return key


async def _order_key(run, owner):
    return await _definition_key(run, owner, "commander_order_type")

async def _current_order(run, character):
    valid, reason = await run.field("CommanderOrderType", "IsValid", (character,), (data.Kind.BOOL,),
        expression="Character.GetOrderType.IsValid")
    if reason or not valid:
        return {"key": None, "reason": reason}
    owner = await _produced_owner(run, "Character.SelectOrder(Character.GetOrderType)", (character,))
    key = await _order_key(run, owner)
    await run.destroy(owner)
    return {"key": key}


async def _commander_order_state(run, character):
    """Read current/next orders from held native Anys with the original validity guard.

    The invalid next-order sentinel is not null and is not the current order.
    Its name is never requested. Separate native reads are not an atomic snapshot.
    This observation does not create a command or infer a definition key.
    """
    state = {"status": "observed", "atomic_snapshot": False, "in_battle": None,
             "current": None, "next": None, "unavailable": {}, "native_results": []}
    character_fields = await run.functions("Character")

    async def capture(expression, bindings):
        result = await run.capture("commander.order_state." + expression,
            lambda: data.evaluate(run.native, expression, tuple(bindings),
                output_bytes=65536, adapter=run.adapters["data"], process=run.process))
        state["native_results"].append((expression, result))
        return result

    def scalar(result, kind):
        reasons = []
        if result.error:
            reasons.append("native getter error " + str(result.error))
        if result.kind != kind:
            reasons.append("native getter did not return the requested scalar kind")
            return None, "; ".join(reasons)
        if kind == data.Kind.STRING:
            try:
                value = result.snapshot.decode("utf-8", "strict")
            except UnicodeDecodeError:
                value = result.snapshot.decode("utf-8", "replace")
                reasons.append("native string contains invalid UTF-8")
            if (result.snapshot_bytes != result.total
                    or len(result.snapshot) != result.snapshot_bytes):
                reasons.append("native text snapshot is partial")
        else:
            value = result.value
            if type(value) is not bool:
                reasons.append("native getter did not return a bool")
            if (result.snapshot_bytes != result.total
                    or len(result.snapshot) != result.snapshot_bytes):
                reasons.append("native scalar snapshot is partial")
        return value, "; ".join(reasons) or None

    for key, expression in (("current", "Character.GetOrderType"),
                            ("next", "Character.GetNextOrderType")):
        row = {"key": None, "valid": None, **display_field(None), "name_read": False,
               "name_status": "not_read_unknown", "unavailable": {},
               "source_result": None, "validity_result": None, "name_result": None}
        state[key] = row
        if expression.removeprefix("Character.") not in character_fields:
            row["unavailable"]["source"] = expression + ": current native metadata has no getter"
            continue
        source = row["source_result"] = await capture(expression, (character,))
        if source.error:
            row["unavailable"]["source"] = "native getter error " + str(source.error)
            continue
        try:
            binding = run.owned_binding(source, "CommanderOrderType")
        except RuntimeError as error:
            row["unavailable"]["source"] = str(error)
            continue
        validity = row["validity_result"] = await capture("CommanderOrderType.IsValid", (binding,))
        row["valid"], reason = scalar(validity, data.Kind.BOOL)
        if reason:
            row["unavailable"]["valid"] = reason
        if reason or row["valid"] is not True:
            if not reason and row["valid"] is False:
                row["name_status"] = "not_read_invalid"
            continue
        name = row["name_result"] = await capture("CommanderOrderType.GetNameNoFormatting", (binding,))
        value, reason = scalar(name, data.Kind.STRING)
        row.update(display_field(value))
        row["name_read"] = True
        row["name_status"] = "partial" if reason else "observed"
        if reason:
            row["unavailable"]["name"] = reason

    state["in_battle_result"] = None
    if "IsInBattle" in character_fields:
        battle = await capture("Character.IsInBattle", (character,))
        state["in_battle_result"] = battle
        state["in_battle"], reason = scalar(battle, data.Kind.BOOL)
        if reason:
            state["unavailable"]["in_battle"] = reason
    else:
        state["unavailable"]["in_battle"] = "Character.IsInBattle: current native metadata has no getter"
    for key in ("current", "next"):
        if state[key]["unavailable"]:
            state["unavailable"][key] = state[key]["unavailable"]
    if state["unavailable"]:
        state["status"] = "partial"
    state["native_results"] = tuple(state["native_results"])
    return state


async def commander_orders(api, character_id, wanted=None, *, validate_only=False):
    """Read real native options or submit one actual populated option producer.

    Keys come from native populated serialization, never display names or tooltip
    parsing. Listing creates no validation/submission Cartesian product.
    """
    async def operation(run):
        character = await _character(run, character_id)
        await run.provider("Character", "SelectOrder", 1)
        character_fields = await run.functions("Character")
        options, seen, failures = [], set(), []
        for source_method, available in (("AccessOrders", True), ("AccessUnavailableOrders", False),
                ("AccessPossibleSpecialOrders", None)):
            if source_method not in character_fields:
                continue
            await run.provider("Character", source_method)
            try:
                async for _, _, option in run.elements("Character." + source_method, (character,), "CommanderOrderType"):
                    owner = await _produced_owner(run, "Character.SelectOrder(CommanderOrderType.Self)", (character, option))
                    key = await _order_key(run, owner)
                    if key in seen:
                        await run.destroy(owner)
                        continue
                    seen.add(key)
                    if wanted is not None:
                        if key != wanted:
                            await run.destroy(owner)
                            continue
                        result = {"character_id": character_id, "order_key": key,
                                  "action": "set_commander_order", "accepted": None}
                        submitted = await _validate_submit(run, owner, result, validate_only=validate_only)
                        if submitted:
                            try:
                                result["observation"] = await _current_order(run, character)
                                result["observed_order_matches"] = result["observation"]["key"] == key
                            except (SourceUnavailable, KnownResultError) as error:
                                result["observation_error"] = str(error)
                            except Exception as error:
                                run.retain_unknown = True
                                result["observation_error"] = str(error)
                        return result
                    row = {"key": key, "available": available, "source": "Character." + source_method,
                           "unavailable": {}}
                    for method, field, kind in (("GetNameNoFormatting", "name", data.Kind.STRING),
                            ("GetDesc", "description", data.Kind.STRING),
                            ("HasAdvanceBehavior", "advances", data.Kind.BOOL),
                            ("IsStandBy", "standby", data.Kind.BOOL)):
                        row[field], reason = await run.field("CommanderOrderType", method, (option,), (kind,))
                        if reason:
                            row["unavailable"][field] = reason
                    row.update(display_field(row.pop("name")))
                    options.append(row)
                    await run.destroy(owner)
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                failures.append(str(error))
                if any(_owner_unsettled(owner) for owner in run.owners):
                    break
        if wanted is not None:
            return {"character_id": character_id, "order_key": wanted, "status": "not_found",
                    "accepted": None, "observed_order_keys": sorted(seen), "details": failures,
                    "reason": "Requested key was not observed in the commander's original native order sources"}
        order_state = await _commander_order_state(run, character)
        return {"character_id": character_id, "status": "partial" if failures else "observed",
                "orders": options, "details": failures, "order_state": order_state}
    return await owned_read(api, "military.commander_orders", operation)


async def _id_owner(run, command_name, parameters):
    actor = await run.ctx.country_id()
    token = await _token(run, command_name)
    native_parameters = {await _token(run, key): commands.UInt32(value) for key, value in parameters.items()}
    created = await run.ctx.query("military.command.create", lambda: commands.create(run.native, token,
        actor_id=actor, world=run.world, parameters=native_parameters,
        adapter=run.adapters["commands"], process=run.process))
    run.operations.append(("create", created))
    if created.error or not created.command:
        if created.command:
            run.unadopted_command = created
        return None, created
    return run.adopt(created), created


async def commander_status(api, character_id, *, include_order_state=True):
    """Read commander facts and guarded current/next display state, not order effects."""
    async def observe(run):
        character = await _character(run, character_id)
        row = {"character_id": character_id, "country_id": _source_country_id(run),
               "status": "observed", "atomic_snapshot": False, "unavailable": {}}
        for method, key in (("HasMilitaryFormation", "has_formation"),
                ("IsTravellingToFormation", "travelling_to_formation"),
                ("HasAdvancingOrder", "has_advancing_order")):
            row[key], reason = await run.field("Character", method, (character,), (data.Kind.BOOL,))
            if reason:
                row["unavailable"][key] = reason
        if include_order_state:
            row["order_state"] = await _commander_order_state(run, character)
            row["in_battle"] = row["order_state"]["in_battle"]
            if "in_battle" in row["order_state"]["unavailable"]:
                row["unavailable"]["in_battle"] = row["order_state"]["unavailable"]["in_battle"]
        else:
            row["in_battle"], reason = await run.field("Character", "IsInBattle", (character,), (data.Kind.BOOL,))
            if reason:
                row["unavailable"]["in_battle"] = reason
        row["formation"], reason = await _related(run, character, "Character", "GetMilitaryFormation", "MilitaryFormation")
        if reason:
            row["unavailable"]["formation"] = reason
        # Character.GetFront is order/target-derived, not formation physical location.
        row["target_front"], reason = await _related(run, character, "Character", "GetFront", "Front")
        if reason:
            row["unavailable"]["target_front"] = reason
        try:
            row["order"] = await _current_order(run, character)
        except (SourceUnavailable, KnownResultError) as error:
            row["order"] = None
            row["unavailable"]["order"] = str(error)
        return row
    return await owned_read(api, "military.commander_status", observe)


async def commander_reference_command(api, command_name, character_id, formation_id=None):
    """Original Assign/Unassign/Reset schemas; no guessed memory writes."""
    parameters = {"character": character_id}
    if command_name == "assign_commander_to_formation":
        parameters["formation"] = formation_id
    elif command_name not in ("unassign_commander_from_formation", "reset_character_order_command"):
        raise ValueError("unknown commander reference command")
    async def operation(run):
        result = {"character_id": character_id, "action": command_name, "accepted": None}
        if formation_id is not None:
            result["formation_id"] = formation_id
        owner, created = await _id_owner(run, command_name, parameters)
        if owner is None:
            result.update(status="native_result", native_result=created, native_error=created.error)
            return result
        accepted = await _validate_submit(run, owner, result)
        if accepted:
            try:
                observation = await commander_status(api, character_id, include_order_state=False)
                result["observation"] = observation
                if command_name == "assign_commander_to_formation":
                    current_id = (observation.get("formation") or {}).get("id")
                    result["observed_assignment_matches"] = current_id == formation_id if current_id is not None else None
                elif command_name == "unassign_commander_from_formation":
                    has_formation = observation.get("has_formation")
                    result["observed_unassigned"] = not has_formation if type(has_formation) is bool else None
            except Exception as error:
                # The separate observation owns and retains its own source graph.
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military." + command_name, operation)


async def formation_supply_options(api, formation_id):
    async def observe(run):
        _, binding = await _formation(run, formation_id)
        if binding is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
        rows = []
        for priority in ("low", "medium", "high"):
            selected, reason = await run.field("MilitaryFormation", "IsSupplyPrioritySelected", (binding,),
                (data.Kind.BOOL,), arguments=1,
                expression="MilitaryFormation.IsSupplyPrioritySelected('" + priority + "')")
            rows.append({"priority": priority, "selected": selected, "eligible": None,
                         "unavailable_reason": reason})
        return {"formation_id": formation_id, "status": "observed", "priorities": rows}
    return await owned_read(api, "military.supply_options", observe)


async def formation_producer_action(api, formation_id, action, *, priority=None):
    expressions = {"remove_naval_mission": "MilitaryFormation.RemoveNavalMission",
                   "disembark": "MilitaryFormation.DisembarkFormation"}
    if action == "set_supply_priority":
        if priority not in ("low", "medium", "high"):
            raise ValueError("priority must be low, medium or high")
        expression = "MilitaryFormation.SelectSupplyPriority('" + priority + "')"
    else:
        expression = expressions[action]
    async def operation(run):
        _, formation = await _formation(run, formation_id)
        if formation is None:
            raise SourceUnavailable("Formation is not in the country's current army or fleet sources")
        result = {"formation_id": formation_id, "action": action, "accepted": None}
        if priority is not None:
            result["priority"] = priority
        owner = await _produced_owner(run, expression, (formation,))
        accepted = await _validate_submit(run, owner, result)
        if accepted:
            if action == "set_supply_priority":
                try:
                    selected, reason = await run.field("MilitaryFormation", "IsSupplyPrioritySelected", (formation,),
                        (data.Kind.BOOL,), arguments=1,
                        expression="MilitaryFormation.IsSupplyPrioritySelected('" + priority + "')")
                    result["observed_selected"] = selected
                    if reason:
                        result["observation_error"] = reason
                except Exception as error:
                    run.retain_unknown = True
                    result["observation_error"] = str(error)
            else:
                try:
                    result["observation"] = await formation_details(api, formation_id)
                except Exception as error:
                    result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military." + action, operation)


async def _war(run, wanted):
    country = await run.country()
    await run.provider("Country", "AccessWars")
    async for _, element, binding in run.elements("Country.AccessWars", (country,), "War"):
        if await run.origin(element, "War") == wanted:
            return country, binding
    raise SourceUnavailable(f"War {wanted} was not present in the country's original War source")


async def _peace_status(run, war, country):
    row = {"unavailable": {}, "atomic_snapshot": False}
    for method, key, kind, argument in (
            ("AllowsNegotiatedPeace", "allows_negotiated_peace", data.Kind.BOOL, False),
            ("HasActivePeaceDeal", "has_active_peace_deal", data.Kind.BOOL, False),
            ("HasActiveWhitePeaceDeal", "has_active_white_peace", data.Kind.BOOL, False),
            ("IsPeaceDealValid", "peace_deal_valid", data.Kind.BOOL, False),
            ("GetPeaceDealValidationDesc", "validation_description", data.Kind.STRING, False),
            ("IsPeaceNegotiator", "country_is_negotiator", data.Kind.BOOL, True),
            ("MustRatifyPeaceDeal", "country_must_ratify", data.Kind.BOOL, True),
            ("WouldAcceptActivePeaceDeal", "country_would_accept_active", data.Kind.BOOL, True),
            ("WouldWhitePeaceDealBeAccepted", "white_peace_would_be_accepted", data.Kind.BOOL, False),
            ("GetWhitePeaceDealAcceptanceTooltip", "white_peace_acceptance", data.Kind.STRING, True),
            ("GetCapitulationDesireDesc", "capitulation_desire", data.Kind.STRING, True)):
        row[key], reason = await run.field("War", method, (war, country) if argument else (war,), (kind,),
            arguments=1 if argument else 0, expression="War." + method + ("(Country.Self)" if argument else ""))
        if reason:
            row["unavailable"][key] = reason
    return row


async def peace_options(api, war_id):
    async def observe(run):
        country, war = await _war(run, war_id)
        return {"war_id": war_id, "status": "observed", **await _peace_status(run, war, country)}
    return await owned_read(api, "military.peace_options", observe)


async def peace_action(api, war_id, action):
    # Exact original war_panel.gui producers. In particular, ToggleAcceptPeace
    # is a toggle, not an idempotent "accept" method or a promise of settlement.
    method = {"propose_peace": "ProposePeaceDeal", "set_white_peace": "SetWhitePeace",
              "toggle_peace_acceptance": "ToggleAcceptPeace", "capitulate": "Capitulate"}[action]
    async def operation(run):
        country, war = await _war(run, war_id)
        owner = await _produced_owner(run, "War." + method + "(Country.Self)", (war, country))
        result = {"war_id": war_id, "action": action, "accepted": None}
        accepted = await _validate_submit(run, owner, result)
        if accepted:
            try:
                result["observation"] = await _peace_status(run, war, country)
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
            result["peace_settled"] = None
        return result
    return await owned_read(api, "military." + action, operation)


async def _naval_status(run, fleet):
    row = {"unavailable": {}, "atomic_snapshot": False, "sea_regions": None,
           "area_coverage": "current_mission_area_only"}
    has_mission, reason = await run.field("MilitaryFormation", "HasNavalMission", (fleet,), (data.Kind.BOOL,))
    row["has_mission"] = has_mission
    if reason:
        row["unavailable"]["has_mission"] = reason
    if has_mission is not True:
        return row
    for target, method, key, kind, prefix in (
            ("NavalMission", "GetNameNoFormatting", "name", data.Kind.STRING, "MilitaryFormation.GetNavalMission"),
            ("NavalMission", "GetNumSeaRegions", "sea_region_count", data.Kind.I32, "MilitaryFormation.GetNavalMission"),
            ("NavalMission", "GetMaxSeaRegions", "max_sea_regions", data.Kind.I32, "MilitaryFormation.GetNavalMission"),
            ("NavalMission", "GetSeaRegionBreakdown", "sea_region_description", data.Kind.STRING, "MilitaryFormation.GetNavalMission"),
            ("NavalMissionType", "GetNameNoFormatting", "type_name", data.Kind.STRING, "MilitaryFormation.GetNavalMission.GetType")):
        row[key], reason = await run.field(target, method, (fleet,), (kind,), expression=prefix + "." + method)
        if reason:
            row["unavailable"][key] = reason
    for name in ("name", "type_name"):
        row.update(display_field(row.get(name), name))
    source = "MilitaryFormation.GetNavalMissionSeaRegions"
    try:
        count = await _source_count(run, source, (fleet,))
        regions = []
        for index in range(count):
            region = {"unavailable": {}}
            for expression, key, kinds in (
                    ("StateRegion.GetID", "id", (data.Kind.I32, data.Kind.U32)),
                    ("StateRegion.GetNameNoFormatting", "name", (data.Kind.STRING,)),
                    ("StateRegion.GetTooltipTag", "tooltip_tag", (data.Kind.STRING,))):
                region[key], reason = await _copied(run, source, index, count, expression, (fleet,), kinds)
                if reason:
                    region["unavailable"][key] = reason
            region.update(display_field(region.pop("name")))
            regions.append(region)
        row["sea_regions"] = regions
    except (SourceUnavailable, KnownResultError) as error:
        row["unavailable"]["sea_regions"] = str(error)
    return row


async def naval_mission_status(api, fleet_id):
    async def observe(run):
        _, fleet = await _formation(run, fleet_id, kind="fleet")
        if fleet is None:
            raise SourceUnavailable("Fleet is not in the country's current fleet source")
        return {"fleet_id": fleet_id, "status": "observed", **await _naval_status(run, fleet)}
    return await owned_read(api, "military.naval_mission_status", observe)


async def naval_missions(api, fleet_id, wanted=None, *, preserve_area=True):
    """Use native mission options and populated producers; never synthesize ranges.

    The native sea_nodes field is a compressed CProvinceSet, not a list of IDs.
    Plain Create starts with an empty area. WithPreviousArea preserves the real
    existing area (or the game's empty fallback); no UI map selector is invoked.
    """
    async def operation(run):
        _, fleet = await _formation(run, fleet_id, kind="fleet")
        if fleet is None:
            raise SourceUnavailable("Fleet is not in the country's current fleet source")
        options, failures = [], []
        source = "MilitaryFormation.GetPotentialMissionsTypes"
        async for _, _, option in run.elements(source, (fleet,), "NavalMissionType"):
            try:
                method = "CreateNavalMissionWithPreviousArea" if wanted is not None and preserve_area else "CreateNavalMission"
                owner = await _produced_owner(run, "MilitaryFormation." + method + "(NavalMissionType.AccessSelf)", (fleet, option))
                key = await _definition_key(run, owner, "naval_mission_type")
                if wanted is not None:
                    if key != wanted:
                        await run.destroy(owner)
                        continue
                    result = {"fleet_id": fleet_id, "mission_key": key, "action": "set_naval_mission",
                              "accepted": None, "preserve_area": preserve_area}
                    accepted = await _validate_submit(run, owner, result)
                    if accepted:
                        try:
                            result["observation"] = await _naval_status(run, fleet)
                        except Exception as error:
                            run.retain_unknown = True
                            result["observation_error"] = str(error)
                        result["mission_effective"] = None
                    return result
                row = {"key": key, "eligible": None, "unavailable": {}}
                for method, field in (("GetNameNoFormatting", "name"), ("GetDesc", "description")):
                    row[field], reason = await run.field("NavalMissionType", method, (option,), (data.Kind.STRING,))
                    if reason:
                        row["unavailable"][field] = reason
                row["selected"], reason = await run.field("MilitaryFormation", "HasNavalMissionType", (fleet, option),
                    (data.Kind.BOOL,), arguments=1, expression="MilitaryFormation.HasNavalMissionType(NavalMissionType.AccessSelf)")
                if reason:
                    row["unavailable"]["selected"] = reason
                row.update(display_field(row.pop("name")))
                options.append(row)
                await run.destroy(owner)
            except (SourceUnavailable, KnownResultError, LookupError) as error:
                failures.append(str(error))
                if any(_owner_unsettled(owner) for owner in run.owners):
                    break
        if wanted is not None:
            return {"fleet_id": fleet_id, "mission_key": wanted, "status": "not_found", "accepted": None,
                    "reason": "Requested key was not observed in the fleet's original potential mission source"}
        return {"fleet_id": fleet_id, "status": "partial" if failures else "observed", "missions": options, "details": failures,
                "coverage": "native_potential_missions", "eligible": None}
    return await owned_read(api, "military.naval_missions", operation)
