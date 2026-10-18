"""Owned V3 warship, shipyard, naval-area, transport, and invasion sources.

Every collection in this module comes from an original DataFactory provider.
Source ordinals are diagnostic only and never key a mutation.  Ship and
ShipTemplate actions select genuine full native IDs from owned wrappers; other
commands use stable Country or MilitaryFormation IDs.  UI selection state and
guessed province-set wire encodings are deliberately not used.
"""
from __future__ import annotations

from ... import v3_commands as commands, v3_data as data
from .._text import display_field
from ._warfare import (
    SourceUnavailable,
    _command_result,
    _copied,
    _formation,
    _produced_owner,
    _related,
    _owner_unsettled,
    _source_count,
    _token,
    _validate_submit,
    _war,
    owned_read,
)
from .._run import KnownResultError, SourceUnavailable, command_field, rejected
from .. import _run


__all__ = (
    "flagship_state",
    "fleet_repair",
    "fleet_repair_status",
    "invasion_action",
    "invasions",
    "naval_area_sources",
    "ship_action",
    "ship_construction_action",
    "ship_construction_control",
    "ship_constructions",
    "ship_state",
    "ship_template_action",
    "ship_templates",
    "transport_options",
)


_SCALAR_ERRORS = (SourceUnavailable, KnownResultError, LookupError)


def _has_unavailable(value):
    if isinstance(value, dict):
        if value.get("unavailable"):
            return True
        return any(_has_unavailable(child) for key, child in value.items()
                   if key != "unavailable")
    if isinstance(value, list):
        return any(_has_unavailable(child) for child in value)
    return False


def _merge_fields(row, observed):
    row.setdefault("unavailable", {}).update(observed.get("unavailable", {}))
    if "native_pages" in observed:
        row.setdefault("native_pages", []).extend(observed["native_pages"])
    row.update((key, value) for key, value in observed.items()
               if key not in ("unavailable", "native_pages"))
    return row


async def _ship_list(run):
    """Own Country.GetShipList before invoking a ShipList Creation provider."""
    from ._warfare import _source_country
    country = await _source_country(run)
    await run.provider("Country", "GetShipList")
    source = await run.evaluate("Country.GetShipList", (country,))
    run.wrapper(source, "ShipList")
    return data.ObjectBinding(
        await run.type_id("ShipList"), source.value_handle, retained=True)


async def _ships(run):
    ship_list = await _ship_list(run)
    await run.provider("ShipList", "GetShips")
    async for index, element, binding in run.elements(
            "ShipList.GetShips", (ship_list,), "Ship"):
        # CDataShip+0x10 is the complete native Ship ID.  The held ShipList and
        # Creation remain owned by WarfareRead until all projections complete.
        yield index, await run.origin(element, "Ship"), binding


async def _ship(run, wanted):
    if type(wanted) is not int or not 0 <= wanted < 0xFFFFFFFF:
        raise ValueError("ship_id must be a complete uint32 Ship ID")
    async for index, identity, binding in _ships(run):
        if identity == wanted:
            return index, binding
    raise SourceUnavailable(f"Ship {wanted} was not present in ShipList.GetShips")


async def _templates(run):
    ship_list = await _ship_list(run)
    await run.provider("ShipList", "GetShipTemplates")
    async for index, element, binding in run.elements(
            "ShipList.GetShipTemplates", (ship_list,), "ShipTemplate"):
        # Static ABI proof closes CDataShipTemplate+0x10 as the complete
        # ShipTemplate reference.  Wrapper+0x14 is a second composite-key word,
        # not a Ship ID and not part of the public identity.
        yield index, await run.origin(element, "ShipTemplate"), binding


async def _template(run, wanted):
    if type(wanted) is not int or not 0 <= wanted < 0xFFFFFFFF:
        raise ValueError("template_id must be a complete uint32 ShipTemplate ID")
    async for _, identity, binding in _templates(run):
        if identity == wanted:
            return binding
    raise SourceUnavailable(f"ShipTemplate {wanted} was not present in ShipList.GetShipTemplates")


async def _constructions(run):
    ship_list = await _ship_list(run)
    await run.provider("ShipList", "GetShipConstructions")
    async for source_index, element, binding in run.elements(
            "ShipList.GetShipConstructions", (ship_list,), "ShipConstruction"):
        # Static factory/consumer proof closes wrapper+0x10 as the complete
        # CShipConstruction reference.  The raw GetIndex field is only the
        # queue position, never the command identity.
        yield source_index, await run.origin(element, "ShipConstruction"), binding


async def _construction(run, wanted):
    if type(wanted) is not int or not 0 <= wanted < 0xFFFFFFFF:
        raise ValueError("construction_id must be a complete uint32 ShipConstruction ID")
    async for _, identity, binding in _constructions(run):
        if identity == wanted:
            return binding
    raise SourceUnavailable(
        f"ShipConstruction {wanted} was not present in ShipList.GetShipConstructions")


async def _chain_fields(run, binding, receiver, getter, target, specs):
    """Project a promoted child without exporting or retagging its wrapper."""
    return await run.fields(
        target, (binding,), specs, expression_prefix=f"{receiver}.{getter}")


async def _formation_relation(run, binding, receiver, getter):
    relation, reason = await _related(
        run, binding, receiver, getter, "MilitaryFormation")
    if relation is None:
        return None, reason
    return relation, reason


async def _modifications(run, binding, receiver, getter):
    expression = receiver + "." + getter
    rows, details = [], []
    try:
        async for index, _, modification in run.elements(
                expression, (binding,), "ShipModificationType"):
            row = {
                "source_index": index,
                "source": expression,
                "unavailable": {},
            }
            for method, key, kinds in (
                    ("GetID", "id", (data.Kind.I32, data.Kind.U32)),
                    ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
                    ("GetDescription", "description", (data.Kind.STRING,)),
                    ("GetTooltipTag", "tooltip_tag", (data.Kind.STRING,)),
                    ("IsUtility", "utility", (data.Kind.BOOL,)),
                    ("HasRequiredTech", "has_required_tech", (data.Kind.BOOL,))):
                row[key], reason = await run.field(
                    "ShipModificationType", method, (modification,), kinds)
                if reason:
                    row["unavailable"][key] = reason
            row.update(display_field(row.pop("name")))
            rows.append(row)
    except _SCALAR_ERRORS as error:
        details.append(str(error))
    return rows, details


async def _template_row(run, index, template_id, template):
    country = await run.country()
    row = {
        "id": template_id,
        "source_index": index,
        "atomic_snapshot": False,
        "unavailable": {},
    }
    _merge_fields(row, await run.fields(
        "ShipTemplate", (template,), (
            ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
            ("GetTooltipTag", "tooltip_tag", (data.Kind.STRING,)),
            ("IsBase", "base", (data.Kind.BOOL,)),
            ("IsCustom", "custom", (data.Kind.BOOL,)),
            ("GetConstructionCost", "construction_cost", (data.Kind.FIXED,)),
            ("GetConstructionGoodsCost", "construction_goods_cost", (data.Kind.FIXED,)),
            ("GetConstructionGoodsDesc", "construction_goods", (data.Kind.STRING,)),
            ("GetConstructionWeeks", "construction_weeks", (data.Kind.I32,)),
            ("GetTotalMaintenanceCost", "maintenance_cost", (data.Kind.FIXED,)),
            ("GetTotalMaintenanceCostDesc", "maintenance_cost_description", (data.Kind.STRING,)),
            ("GetArmor", "armor", (data.Kind.FIXED,)),
            ("GetAccuracy", "accuracy", (data.Kind.FIXED,)),
            ("GetScreening", "screening", (data.Kind.FIXED,)),
            ("GetMovementSpeed", "movement_speed", (data.Kind.FIXED,)),
            ("GetHitPointsMax", "hull_max", (data.Kind.FIXED,)),
            ("GetCrewMax", "crew_max", (data.Kind.FIXED,)),
            ("GetHullDamage", "hull_damage", (data.Kind.FIXED,)),
            ("GetCrewDamage", "crew_damage", (data.Kind.FIXED,)),
            ("GetSupplyCapacity", "supply_capacity", (data.Kind.FIXED,)),
            ("GetCarryingCapacity", "carrying_capacity", (data.Kind.FIXED,)),
            ("GetMarineCapacity", "marine_capacity", (data.Kind.FIXED,)),
            ("GetReadinessGain", "readiness_gain", (data.Kind.FIXED,)),
            ("GetDetection", "detection", (data.Kind.FIXED,)),
            ("GetVisibility", "visibility", (data.Kind.FIXED,)),
            ("GetVulnerability", "vulnerability", (data.Kind.FIXED,)),
            ("GetCriticalHitChance", "critical_hit_chance", (data.Kind.FIXED,)),
            ("GetCriticalHitMultiplier", "critical_hit_multiplier", (data.Kind.FIXED,)),
            ("GetSupplyGoodsCost", "supply_goods_cost", (data.Kind.FIXED,)))))
    row.update(display_field(row.pop("name")))
    row["ship_type"] = await _chain_fields(
        run, template, "ShipTemplate", "GetType", "ShipType", (
            ("GetID", "id", (data.Kind.I32, data.Kind.U32)),
            ("GetIDString", "key_raw", (data.Kind.STRING,)),
            ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
            ("GetDescription", "description", (data.Kind.STRING,)),
            ("HasRequiredTech", "has_required_tech", (data.Kind.BOOL,)),
            ("GetMaxDistanceToPort", "max_distance_to_port", (data.Kind.I32,)),
        ))
    if "name" in row["ship_type"]:
        row["ship_type"].update(display_field(row["ship_type"].pop("name")))
    requirement, reason = await run.field(
        "ShipType", "GetTechRequirements", (template, country), (data.Kind.STRING,),
        arguments=1,
        expression="ShipTemplate.GetType.GetTechRequirements(Country.Self)",
    )
    row["ship_type"]["tech_requirements"] = requirement
    if reason:
        row["ship_type"]["unavailable"]["tech_requirements"] = reason
    row["modifications"], failures = await _modifications(
        run, template, "ShipTemplate", "GetModifications")
    row["utility_modifications"], utility_failures = await _modifications(
        run, template, "ShipTemplate", "GetUtilityModifications")
    failures.extend(utility_failures)
    if failures:
        row["unavailable"]["modifications"] = failures
    # The ID is copied from the exact owned CDataShipTemplate+0x10 wrapper.
    # Source index remains diagnostic only and is never accepted by a mutation.
    row["status"] = "partial" if _has_unavailable(row) else "observed"
    return row


async def ship_templates(api):
    """Read current country ship templates, modifications, costs, and tech state."""
    async def observe(run):
        rows, failures = [], []
        try:
            async for index, template_id, template in _templates(run):
                rows.append(await _template_row(run, index, template_id, template))
        except _SCALAR_ERRORS as error:
            failures.append(str(error))
        return {
            "status": "partial" if failures or any(
                row.get("status") == "partial" for row in rows) else "observed",
            "templates": rows,
            "source": "Country.GetShipList -> ShipList.GetShipTemplates",
            "identity_scope": "native_full_ship_template_id",
            "designer_save_available": False,
            "designer_save_unavailable_reason": (
                "ShipDesignerPopup.CreateTemplate/EditTemplate require a popup-owned mutable design draft"),
            "details": failures,
        }
    return await owned_read(api, "military.ship_templates", observe)


async def _ship_row(run, ship_id, source_index, ship):
    from ._warfare import _source_country_id
    row = {
        "id": ship_id,
        "source_index": source_index,
        "country_id": _source_country_id(run),
        "atomic_snapshot": False,
        "unavailable": {},
    }
    _merge_fields(row, await run.fields(
        "Ship", (ship,), (
            ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
            ("GetTooltipTag", "tooltip_tag", (data.Kind.STRING,)),
            ("GetHitPoints", "hull_current", (data.Kind.FIXED,)),
            ("GetHitPointsMax", "hull_max", (data.Kind.FIXED,)),
            ("GetHullDamage", "hull_damage", (data.Kind.FIXED,)),
            ("GetTotalCrew", "crew_current", (data.Kind.I32,)),
            ("GetCrewMax", "crew_max", (data.Kind.FIXED,)),
            ("GetCrewDamage", "crew_damage", (data.Kind.FIXED,)),
            ("GetArmor", "armor", (data.Kind.FIXED,)),
            ("GetAccuracy", "accuracy", (data.Kind.FIXED,)),
            ("GetScreening", "screening", (data.Kind.FIXED,)),
            ("GetMovementSpeed", "movement_speed", (data.Kind.FIXED,)),
            ("GetSpeedPerDay", "speed_per_day", (data.Kind.FIXED,)),
            ("GetSupplyCapacity", "supply_capacity", (data.Kind.FIXED,)),
            ("GetSupplyEfficiency", "supply_efficiency", (data.Kind.FIXED,)),
            ("GetCarryingCapacity", "carrying_capacity", (data.Kind.FIXED,)),
            ("GetMarineCapacity", "marine_capacity", (data.Kind.FIXED,)),
            ("GetBlockadeStrength", "blockade_strength", (data.Kind.FIXED,)),
            ("GetDetection", "detection", (data.Kind.FIXED,)),
            ("GetVisibility", "visibility", (data.Kind.FIXED,)),
            ("GetVulnerability", "vulnerability", (data.Kind.FIXED,)),
            ("GetCriticalHitChance", "critical_hit_chance", (data.Kind.FIXED,)),
            ("GetCriticalHitMultiplier", "critical_hit_multiplier", (data.Kind.FIXED,)),
            ("GetAverageAttackDamage", "average_attack_damage", (data.Kind.FIXED,)),
            ("GetReadinessGain", "readiness_gain", (data.Kind.FIXED,)),
            ("GetVeterancyExperience", "veterancy_experience", (data.Kind.FIXED,)),
            ("GetVeterancyExperienceNeededForNextLevel", "veterancy_next_threshold", (data.Kind.FIXED,)),
            ("GetVeterancyExperienceRelativeToCurrentLevel", "veterancy_level_progress", (data.Kind.FIXED,)),
            ("GetVeterancyExperienceWeeklyGain", "veterancy_weekly_gain", (data.Kind.FIXED,)),
            ("GetVeterancyExperienceWeeklyDecay", "veterancy_weekly_decay", (data.Kind.FIXED,)),
            ("IsAtMaxVeterancy", "veterancy_max", (data.Kind.BOOL,)),
            ("IsGainingVeterancyExperience", "veterancy_gaining", (data.Kind.BOOL,)),
            ("IsDecayingVeterancyExperience", "veterancy_decaying", (data.Kind.BOOL,)),
            ("IsInBattle", "in_battle", (data.Kind.BOOL,)),
            ("IsDestroyed", "destroyed", (data.Kind.BOOL,)),
            ("IsOutdated", "outdated", (data.Kind.BOOL,)),
            ("IsToRetrofit", "marked_for_retrofit", (data.Kind.BOOL,)),
            ("GetRetrofitCost", "retrofit_cost", (data.Kind.FIXED,)),
            ("GetRetrofitProgress", "retrofit_progress", (data.Kind.FIXED,)),
            ("GetRetrofitWeeks", "retrofit_weeks", (data.Kind.I32,)),
            ("GetRetrofitWeeksLeft", "retrofit_weeks_left", (data.Kind.I32,)),
            ("CanBeFlagship", "can_be_flagship", (data.Kind.BOOL,)),
            ("IsFlagship", "flagship", (data.Kind.BOOL,)),
            ("GetFlagshipOrnamentName", "flagship_ornament", (data.Kind.STRING,)),
            ("HasFlagshipSignalFlags", "flagship_signal_flags", (data.Kind.BOOL,)))))
    row.update(display_field(row.pop("name")))
    for getter, key in (("GetFleet", "fleet"), ("GetTargetFleet", "target_fleet")):
        row[key], reason = await _formation_relation(run, ship, "Ship", getter)
        if reason:
            row["unavailable"][key] = reason
    row["template"] = await _chain_fields(
        run, ship, "Ship", "GetTemplate", "ShipTemplate", (
            ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
            ("GetTooltipTag", "tooltip_tag", (data.Kind.STRING,)),
            ("IsBase", "base", (data.Kind.BOOL,)),
            ("IsCustom", "custom", (data.Kind.BOOL,)),
        ))
    row["template"].update(display_field(row["template"].pop("name")))
    row["ship_type"] = await _chain_fields(
        run, ship, "Ship", "GetType", "ShipType", (
            ("GetID", "id", (data.Kind.I32, data.Kind.U32)),
            ("GetIDString", "key_raw", (data.Kind.STRING,)),
            ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
        ))
    row["ship_type"].update(display_field(row["ship_type"].pop("name")))
    row["veterancy"] = await _chain_fields(
        run, ship, "Ship", "GetVeterancyLevel", "ShipVeterancyLevel", (
            ("GetCurrentLevel", "level", (data.Kind.I32,)),
            ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
            ("GetTooltipTag", "tooltip_tag", (data.Kind.STRING,)),
            ("IsMaxLevel", "max_level", (data.Kind.BOOL,)),
        ))
    row["veterancy"].update(display_field(row["veterancy"].pop("name")))
    row["modifications"], failures = await _modifications(
        run, ship, "Ship", "GetModifications")
    row["utility_modifications"], utility_failures = await _modifications(
        run, ship, "Ship", "GetUtilityModifications")
    failures.extend(utility_failures)
    if failures:
        row["unavailable"]["modifications"] = failures
    row["status"] = "partial" if _has_unavailable(row) else "observed"
    return row


async def ship_state(api, ship_id):
    """Read one owned ship's detailed combat, damage, retrofit, and design state."""
    async def observe(run):
        source_index, ship = await _ship(run, ship_id)
        row = await _ship_row(run, ship_id, source_index, ship)
        row["status"] = "partial" if _has_unavailable(row) else "observed"
        row["rename_command_available"] = None
        row["rename_command_unavailable_reason"] = None
        row["rename_command_status"] = "not_checked"
        row["rename_command_entrypoint"] = "military.rename_ship"
        row["rename_command_source"] = "ShipChangeNamePopup.Confirm"
        return row
    return await owned_read(api, "military.ship_state", observe)


async def ship_action(api, ship_id, action, *, template_id=None, validate_only=False):
    """Run one original Ship producer; rename is excluded because it is popup-owned."""
    expressions = {
        "decommission": "Ship.Decommission",
        "retrofit": "Ship.Retrofit",
        "cancel_retrofit": "Ship.CancelRetrofit",
        "assign_flagship": "Ship.AssignAsFlagship",
        "unassign_flagship": "Ship.UnassignAsFlagship",
        "next_flagship_ornament": "Ship.SetNextFlagshipOrnament",
        "previous_flagship_ornament": "Ship.SetPrevFlagshipOrnament",
        "toggle_flagship_signal_flags": "Ship.ToggleFlagshipSignalFlags",
    }
    if action not in (*expressions, "change_template"):
        raise ValueError("unknown ship action")

    async def operation(run):
        _, ship = await _ship(run, ship_id)
        result = {"ship_id": ship_id, "action": action, "accepted": None}
        if action == "change_template":
            template = await _template(run, template_id)
            expression = "Ship.ChangeTemplate(ShipTemplate.AccessSelf)"
            bindings = (ship, template)
            result["template_id"] = template_id
        else:
            expression = expressions[action]
            bindings = (ship,)
        owner = await _produced_owner(run, expression, bindings)
        accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if accepted:
            try:
                result["observation"] = await ship_state(api, ship_id)
            except Exception as error:
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military.ship_" + action, operation)


_QUEUE_QUANTITY_TOKEN = 22805
_QUEUE_QUANTITY_NAME = "construction_increment_level"


def _replace_queue_quantity(tree, requested_count):
    """Replace exactly one proved int32 quantity field and nothing else."""
    replacements = 0

    def replace(item):
        nonlocal replacements
        if isinstance(item, commands.Assignment):
            if item.key == commands.Token(_QUEUE_QUANTITY_TOKEN):
                if type(item.value) is not int:
                    raise SourceUnavailable(
                        "Original ship queue quantity field is not a native int32")
                replacements += 1
                return commands.Assignment(item.key, requested_count)
            return commands.Assignment(item.key, replace(item.value))
        if isinstance(item, tuple):
            return tuple(replace(child) for child in item)
        return item

    replaced = replace(tree)
    if replacements != 1:
        raise SourceUnavailable(
            "Original ship queue command has missing or ambiguous construction_increment_level")
    return replaced


async def _queue_quantity(run, owner):
    """Read the original producer's serialized quantity field before submission."""
    serialized = await _command_result(run, owner, "ship_queue.serialize", owner.serialize)
    if serialized.error:
        raise KnownResultError("ship_queue.serialize", serialized)
    if _owner_unsettled(owner):
        raise SourceUnavailable(
            "Original ship queue command serialization ownership is unresolved; owner retained")
    try:
        tree = commands.decode_command(serialized.data)
        if (tree.key != commands.Token(owner.type_token)
                or commands.encode_tree(tree) != serialized.data):
            raise RuntimeError("native root type or lossless wire roundtrip differs")
        quantity = command_field(tree, _QUEUE_QUANTITY_TOKEN)
    except (RuntimeError, ValueError) as error:
        raise SourceUnavailable(
            "Original ship queue producer quantity could not be decoded: " + str(error)) from error
    if type(quantity) is not int or not 1 <= quantity < (1 << 31):
        raise SourceUnavailable("Original ship queue producer returned an invalid quantity field")
    return quantity, tree, serialized


async def _queue_owner_for_count(run, template_id, action, fleet_id, requested_count):
    """Clone an original populated command, replace its one proved count, and rebuild it."""
    expression, bindings = await _template_action_producer(
        run, template_id, action, fleet_id)
    original = await _produced_owner(run, expression, bindings)
    original_quantity, tree, original_serialization = await _queue_quantity(run, original)
    token = await _token(run, _QUEUE_QUANTITY_NAME)
    if token != _QUEUE_QUANTITY_TOKEN:
        raise SourceUnavailable(
            "Native construction_increment_level token differs from the proved queue reader/writer")
    edited_tree = _replace_queue_quantity(tree, requested_count)
    if (edited_tree.key != tree.key
            or commands.token_ids(edited_tree) != commands.token_ids(tree)):
        raise SourceUnavailable("Edited ship queue tree changed native class or field tokens")
    edited_wire = commands.encode_tree(edited_tree)
    original_type = original.type_token
    original_world = original.world
    await run.destroy(original)

    receipt = await run.ctx.query("military.ship_queue.deserialize", lambda: commands.request(
        run.ctx.native, commands.Action.DESERIALIZE, data=edited_wire, world=original_world,
        adapter=run.ctx.adapters["commands"], process=run.ctx.process))
    run.operations.append(("ship_queue.deserialize", receipt))
    if receipt.error or not receipt.command:
        if receipt.command:
            run.unadopted_command = receipt
        raise KnownResultError("ship_queue.deserialize", receipt)
    owner = run.adopt(receipt, expected_world=original_world)
    if receipt.action != commands.Action.DESERIALIZE or owner.type_token != original_type:
        raise SourceUnavailable("Deserialized ship queue owner changed native command class")
    return owner, {
        "original_native_quantity": original_quantity,
        "requested_native_quantity": requested_count,
        "original_serialization_result": original_serialization,
        "deserialize_result": receipt,
    }


async def _template_action_producer(run, template_id, action, fleet_id):
    template = await _template(run, template_id)
    expression = {
        "queue": "ShipTemplate.QueueConstruction",
        "cancel": "ShipTemplate.CancelConstruction",
        "delete": "ShipTemplate.DeleteTemplate",
        "default_country": "ShipTemplate.SetAsDefaultForCountry",
    }.get(action)
    if expression is not None:
        return expression, (template,)
    _, fleet = await _formation(run, fleet_id, kind="fleet")
    if fleet is None:
        raise SourceUnavailable("Fleet is not in Country.GetMilitaryFormationsFleet")
    expression = {
        "queue_for_fleet": "ShipTemplate.QueueConstructionForFleet(MilitaryFormation.Self)",
        "cancel_for_fleet": "ShipTemplate.CancelConstructionForFleet(MilitaryFormation.Self)",
        "default_fleet": "ShipTemplate.SetAsDefaultForFleet(MilitaryFormation.Self)",
    }[action]
    return expression, (template, fleet)


async def ship_template_action(api, template_id, action, *, fleet_id=None,
                               count=1, validate_only=False):
    """Queue a requested quantity or run one non-queue template action.

    The original populated producer supplies every native field.  Its proved
    construction_increment_level is replaced in that complete lossless tree,
    then the native reader builds one fresh owner.  That owner is validated and
    submitted at most once; no owner or unknown attempt is replayed.
    """
    allowed = {
        "queue", "queue_for_fleet", "cancel", "cancel_for_fleet",
        "delete", "default_country", "default_fleet",
    }
    if action not in allowed:
        raise ValueError("unknown ship template action")
    queue_action = action in ("queue", "queue_for_fleet")
    if type(count) is not int or (queue_action and not 1 <= count < (1 << 31)):
        raise ValueError("count must be a positive int32 for a ship queue action")
    if not queue_action and count != 1:
        raise ValueError("count applies only to queue and queue_for_fleet")
    if action.endswith("_for_fleet") or action == "default_fleet":
        if type(fleet_id) is not int:
            raise ValueError("fleet_id is required for this template action")

    async def operation(run):
        base = {
            "template_id": template_id,
            "fleet_id": fleet_id,
            "requested_count": count if queue_action else None,
            "action": action,
            "accepted": None,
        }
        if not queue_action:
            expression, bindings = await _template_action_producer(
                run, template_id, action, fleet_id)
            owner = await _produced_owner(run, expression, bindings)
            accepted = await _validate_submit(
                run, owner, base, validate_only=validate_only)
            if accepted:
                try:
                    base["templates"] = await ship_templates(api)
                    base["queue"] = await ship_constructions(api)
                except Exception as error:
                    base["observation_error"] = str(error)
            return base

        owner, rewrite = await _queue_owner_for_count(
            run, template_id, action, fleet_id, count)
        attempt = {
            "attempt": 0,
            **rewrite,
            "accepted": None,
        }
        submitted = await _validate_submit(
            run, owner, attempt, validate_only=validate_only)
        # The shared return value also reports settled ownership. It is not the
        # native acceptance flag: an accepted command can still need cleanup.
        native_accepted = attempt.get("accepted")
        accepted_count = (0 if validate_only else count if native_accepted is True
                          else 0 if native_accepted is False else None)
        validated_count = (count if attempt.get("status") == "available" else
                           0 if attempt.get("status") == "rejected" else None) if validate_only else 0
        accounted_count = validated_count if validate_only else accepted_count
        base.update(
            attempts=[attempt],
            native_attempt_count=1,
            accepted_count=accepted_count,
            validated_count=validated_count,
            remaining_count=(count - accounted_count if accounted_count is not None else None),
            accepted_count_basis=(
                "requested construction_increment_level on one accepted native command; "
                "not completed ships"),
            status=attempt.get("status", "native_result"),
            eligible=attempt.get("eligible"),
            accepted=None if validate_only else attempt.get("accepted"),
        )
        if submitted:
            try:
                base["templates"] = await ship_templates(api)
                base["queue"] = await ship_constructions(api)
            except Exception as error:
                base["observation_error"] = str(error)
        return base
    return await owned_read(api, "military.ship_template_" + action, operation)


async def _construction_row(run, source_index, construction_id, construction):
    row = {
        "id": construction_id,
        "source_index": source_index,
        "identity_scope": "native_full_ship_construction_id",
        "atomic_snapshot": False,
        "unavailable": {},
    }
    for method, key, kinds in (
            ("GetIndex", "queue_index", (data.Kind.I32,)),
            ("GetPlaceInQueue", "place_in_queue", (data.Kind.I32,)),
            ("GetTooltipTag", "tooltip_tag", (data.Kind.STRING,)),
            ("IsRepair", "repair", (data.Kind.BOOL,)),
            ("IsRetrofit", "retrofit", (data.Kind.BOOL,)),
            ("IsInProgress", "in_progress", (data.Kind.BOOL,)),
            ("GetProgress", "progress", (data.Kind.FIXED,)),
            ("GetProgressPercent", "progress_fraction", (data.Kind.FIXED,)),
            ("GetConstructionCost", "construction_cost", (data.Kind.FIXED,)),
            ("GetConstructionSpeed", "construction_speed", (data.Kind.FIXED,)),
            ("GetConstructionUsage", "construction_usage", (data.Kind.FIXED,)),
            ("GetEfficiency", "efficiency", (data.Kind.FIXED,)),
            ("GetWeeksLeft", "weeks_left", (data.Kind.I32,))):
        row[key], reason = await run.field("ShipConstruction", method, (construction,), kinds)
        if reason:
            row["unavailable"][key] = reason
    if row.get("repair"):
        row["kind"] = "repair"
    elif row.get("retrofit"):
        row["kind"] = "retrofit"
    elif row.get("repair") is False and row.get("retrofit") is False:
        row["kind"] = "new_ship"
    else:
        row["kind"] = None
    row["fleet"], reason = await _formation_relation(
        run, construction, "ShipConstruction", "GetFleet")
    if reason:
        row["unavailable"]["fleet"] = reason
    row["ship"] = await _chain_fields(
        run, construction, "ShipConstruction", "GetShip", "Ship", (
            ("IsValid", "valid", (data.Kind.BOOL,)),
            ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
            ("GetTooltipTag", "tooltip_tag", (data.Kind.STRING,)),
            ("IsDestroyed", "destroyed", (data.Kind.BOOL,)),
        ))
    row["ship"].update(display_field(row["ship"].pop("name")))
    row["template"] = await _chain_fields(
        run, construction, "ShipConstruction", "GetShipTemplate", "ShipTemplate", (
            ("IsValid", "valid", (data.Kind.BOOL,)),
            ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
            ("GetTooltipTag", "tooltip_tag", (data.Kind.STRING,)),
        ))
    row["template"].update(display_field(row["template"].pop("name")))
    row["ship_type"] = await _chain_fields(
        run, construction, "ShipConstruction", "GetShipType", "ShipType", (
            ("GetID", "id", (data.Kind.I32, data.Kind.U32)),
            ("GetIDString", "key_raw", (data.Kind.STRING,)),
            ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
        ))
    row["ship_type"].update(display_field(row["ship_type"].pop("name")))
    row["status"] = "partial" if _has_unavailable(row) else "observed"
    return row


async def ship_constructions(api):
    """Read the native new-build, repair, and retrofit ship queue."""
    async def observe(run):
        rows, failures = [], []
        try:
            async for source_index, construction_id, construction in _constructions(run):
                rows.append(await _construction_row(
                    run, source_index, construction_id, construction))
        except _SCALAR_ERRORS as error:
            failures.append(str(error))
        return {
            "status": "partial" if failures or any(
                row.get("status") == "partial" for row in rows) else "observed",
            "constructions": rows,
            "source": "Country.GetShipList -> ShipList.GetShipConstructions",
            "identity_scope": "native_full_ship_construction_id",
            "mutation_available": True,
            "details": failures,
        }
    return await owned_read(api, "military.ship_constructions", observe)



def _int32_literal(value):
    if type(value) is not int or not 0 <= value < (1 << 31):
        raise ValueError("new_position must be a non-negative int32")
    return "'(int32)" + str(value) + "'"


async def ship_construction_action(api, construction_id, action, *, new_position=None,
                                   validate_only=False):
    """Cancel or reorder one exact native ShipConstruction full reference."""
    methods = {
        "cancel": "ShipConstruction.Cancel",
        "top": "ShipConstruction.MoveToTop",
        "bottom": "ShipConstruction.MoveToBottom",
        "up": "ShipConstruction.MoveUp",
        "down": "ShipConstruction.MoveDown",
    }
    if action == "move":
        expression = "ShipConstruction.MoveToIndex(" + _int32_literal(new_position) + ")"
    elif action in methods:
        expression = methods[action]
    else:
        raise ValueError("unknown ship construction action")

    async def operation(run):
        construction = await _construction(run, construction_id)
        result = {
            "construction_id": construction_id,
            "new_position": new_position,
            "action": action,
            "accepted": None,
        }
        owner = await _produced_owner(run, expression, (construction,))
        accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if accepted:
            try:
                result["observation"] = await ship_constructions(api)
            except Exception as error:
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military.ship_construction_" + action, operation)


async def ship_construction_control(api, *, paused=None, default_fleet_id=...,
                                    validate_only=False):
    """Observe or change national warship queue pause/default-fleet state."""
    if paused is not None and type(paused) is not bool:
        raise ValueError("paused must be bool or None")
    if default_fleet_id is not ... and default_fleet_id is not None and type(default_fleet_id) is not int:
        raise ValueError("default_fleet_id must be a fleet ID, None to clear, or omitted")

    async def status(run, country):
        row = {"unavailable": {}}
        row["queue_paused"], reason = await run.field(
            "Country", "IsShipConstructionPaused", (country,), (data.Kind.BOOL,))
        if reason:
            row["unavailable"]["queue_paused"] = reason
        row["default_fleet"], reason = await _related(
            run, country, "Country", "GetDefaultConstructionFleet", "MilitaryFormation")
        if reason:
            row["unavailable"]["default_fleet"] = reason
        return row

    async def operation(run):
        country = await run.country()
        before = await status(run, country)
        if paused is None and default_fleet_id is ...:
            return {"status": "observed", **before}
        if paused is not None and default_fleet_id is not ...:
            raise ValueError("change pause and default fleet with separate explicit intents")
        if paused is not None:
            expression = "Country.PauseShipConstruction" if paused else "Country.ResumeShipConstruction"
            bindings = (country,)
            result = {"action": "set_ship_construction_paused", "queue_paused": paused, "accepted": None}
        elif default_fleet_id is None:
            expression = "Country.ClearDefaultConstructionFleet"
            bindings = (country,)
            result = {"action": "clear_default_construction_fleet", "default_fleet_id": None,
                      "accepted": None}
        else:
            _, fleet = await _formation(run, default_fleet_id, kind="fleet")
            if fleet is None:
                raise SourceUnavailable("Fleet is not in Country.GetMilitaryFormationsFleet")
            expression = "Country.SetDefaultConstructionFleet(MilitaryFormation.Self)"
            bindings = (country, fleet)
            result = {"action": "set_default_construction_fleet",
                      "default_fleet_id": default_fleet_id, "accepted": None}
        result["before"] = before
        owner = await _produced_owner(run, expression, bindings)
        accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if accepted:
            try:
                result["observation"] = await ship_construction_control(api)
            except Exception as error:
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military.ship_construction_control", operation)


async def _fleet_repair_row(run, fleet_id, fleet):
    row = {"fleet_id": fleet_id, "unavailable": {}}
    for method, key, kinds in (
            ("IsRecalledForRepairs", "recalled_for_repairs", (data.Kind.BOOL,)),
            ("IsDocked", "docked", (data.Kind.BOOL,)),
            ("GetRepairProgress", "repair_progress", (data.Kind.FIXED,)),
            ("GetRetrofitProgress", "retrofit_progress", (data.Kind.FIXED,))):
        row[key], reason = await run.field("MilitaryFormation", method, (fleet,), kinds)
        if reason:
            row["unavailable"][key] = reason
    row["status"] = "partial" if _has_unavailable(row) else "observed"
    return row


async def fleet_repair_status(api, fleet_id):
    """Read docked, recalled, repair, and retrofit state without a command."""
    async def observe(run):
        _, fleet = await _formation(run, fleet_id, kind="fleet")
        if fleet is None:
            raise SourceUnavailable("Fleet is not in Country.GetMilitaryFormationsFleet")
        return await _fleet_repair_row(run, fleet_id, fleet)
    return await owned_read(api, "military.fleet_repair_status", observe)


async def fleet_repair(api, fleet_id, *, validate_only=False):
    """Submit one original MilitaryFormation.RecallForRepairs command."""
    async def operation(run):
        _, fleet = await _formation(run, fleet_id, kind="fleet")
        if fleet is None:
            raise SourceUnavailable("Fleet is not in Country.GetMilitaryFormationsFleet")
        result = await _fleet_repair_row(run, fleet_id, fleet)
        result.update(action="recall_for_repairs", accepted=None)
        owner = await _produced_owner(run, "MilitaryFormation.RecallForRepairs", (fleet,))
        accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if accepted:
            try:
                result["observation"] = await fleet_repair_status(api, fleet_id)
            except Exception as error:
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military.fleet_repair", operation)


async def flagship_state(api):
    """Find the national flagship through owned ships, preserving its full Ship ID."""
    async def observe(run):
        matches, failures = [], []
        try:
            async for source_index, ship_id, ship in _ships(run):
                selected, reason = await run.field("Ship", "IsFlagship", (ship,), (data.Kind.BOOL,))
                if reason:
                    failures.append(reason)
                elif selected:
                    matches.append(await _ship_row(run, ship_id, source_index, ship))
        except _SCALAR_ERRORS as error:
            failures.append(str(error))
        return {
            "status": "partial" if failures or any(
                row.get("status") == "partial" for row in matches) else "observed",
            "flagship": matches[0] if len(matches) == 1 else None,
            "flagships": matches,
            "invariant": "one" if len(matches) == 1 else "none" if not matches else "multiple_observed",
            "details": failures,
        }
    return await owned_read(api, "military.flagship_state", observe)


async def _province_relation(run, formation, getter):
    prefix = "MilitaryFormation." + getter
    row = {"unavailable": {}}
    for expression, key, kinds in (
            (prefix + ".GetID", "id_raw", (data.Kind.STRING,)),
            (prefix + ".GetIndex", "index", (data.Kind.I32,)),
            (prefix + ".GetSeaNodeName", "name", (data.Kind.STRING,)),
            (prefix + ".GetStateRegion.GetID", "state_region_id", (data.Kind.I32, data.Kind.U32)),
            (prefix + ".GetStateRegion.GetNameNoFormatting", "state_region_name", (data.Kind.STRING,))):
        target = "StateRegion" if ".GetStateRegion." in expression else "Province"
        method = expression.rsplit(".", 1)[-1]
        row[key], reason = await run.field(target, method, (formation,), kinds, expression=expression)
        if reason:
            row["unavailable"][key] = reason
    row.update(display_field(row.pop("name")))
    row.update(display_field(row.pop("state_region_name"), "state_region_name"))
    return row


async def naval_area_sources(api, fleet_id):
    """Read physical sea nodes and current assigned mission regions.

    This is not an all-target eligibility list.  GetNavalMissionSeaRegions is a
    StateRegion view of the current native CProvinceSet.  The compact two-UInt16
    records are never decoded or synthesized here.
    """
    async def observe(run):
        _, fleet = await _formation(run, fleet_id, kind="fleet")
        if fleet is None:
            raise SourceUnavailable("Fleet is not in Country.GetMilitaryFormationsFleet")
        row = {
            "fleet_id": fleet_id,
            "current_sea_node": await _province_relation(run, fleet, "GetCurrentSeaNode"),
            "target_sea_node": await _province_relation(run, fleet, "GetTargetSeaNode"),
            "assigned_sea_regions": [],
            "unavailable": {},
            "candidate_scope": None,
            "candidate_scope_unavailable_reason": (
                "No proved all/eligible sea-region source exists; current assigned regions are not "
                "a target denominator"),
            "area_wire": "native CProvinceSet retained; not flattened",
            "fleet_deploy_command_available": False,
            "fleet_deploy_command_unavailable_reason": (
                "Province.GetDeploySelectedFormationsCommand consumes engine selected-formation state; "
                "no explicit Fleet formal or all sea-node source is proved"),
            "area_edit_command_available": False,
            "area_edit_command_unavailable_reason": (
                "Province.AddSeaRegionToSelectedFleetMissions consumes selected fleets and no safe "
                "province identity-to-CProvinceSet range constructor is proved"),
        }
        for method, key, kinds in (
                ("CanDeployToSeaNode", "can_deploy_to_some_selected_sea_node", (data.Kind.BOOL,)),
                ("CanEditMissionSeaRegions", "can_edit_mission_regions", (data.Kind.BOOL,)),
                ("GetCanEditMissionSeaRegionsDesc", "edit_mission_regions_description", (data.Kind.STRING,)),
                ("GetSeaPresence", "sea_presence", (data.Kind.FIXED,)),
                ("GetSeaPresencePerDay", "sea_presence_per_day", (data.Kind.FIXED,)),
                ("GetMissionEfficiency", "mission_efficiency", (data.Kind.FIXED,)),
                ("GetBlockadeEfficiency", "blockade_efficiency", (data.Kind.FIXED,)),
                ("HasSupplyRaidTargets", "has_supply_raid_targets", (data.Kind.BOOL,))):
            row[key], reason = await run.field("MilitaryFormation", method, (fleet,), kinds)
            if reason:
                row["unavailable"][key] = reason
        try:
            async for index, _, region in run.elements(
                    "MilitaryFormation.GetNavalMissionSeaRegions", (fleet,), "StateRegion"):
                area = {"source_index": index, "unavailable": {}}
                for method, key, kinds in (
                        ("GetID", "id", (data.Kind.I32, data.Kind.U32)),
                        ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
                        ("GetTooltipTag", "tooltip_tag", (data.Kind.STRING,))):
                    area[key], reason = await run.field("StateRegion", method, (region,), kinds)
                    if reason:
                        area["unavailable"][key] = reason
                area.update(display_field(area.pop("name")))
                row["assigned_sea_regions"].append(area)
        except _SCALAR_ERRORS as error:
            row["unavailable"]["assigned_sea_regions"] = str(error)
        row["status"] = "partial" if _has_unavailable(row) else "observed"
        return row
    return await owned_read(api, "military.naval_area_sources", observe)


async def transport_options(api, army_id):
    """Read genuine army transport state and the country's fleet capacity source.

    Pair eligibility remains unknown because CanTransportArmy has no Army formal
    and shipped transport execution belongs to TransportPlannerPopup state.
    """
    async def observe(run):
        _, army = await _formation(run, army_id, kind="army")
        if army is None:
            raise SourceUnavailable("Army is not in Country.GetMilitaryFormationsArmy")
        army_row = {"id": army_id, "unavailable": {}}
        for method, key, kinds in (
                ("CanEmbark", "can_embark", (data.Kind.BOOL,)),
                ("GetCanEmbarkDesc", "can_embark_description", (data.Kind.STRING,)),
                ("IsAttachedToFleet", "attached_to_fleet", (data.Kind.BOOL,)),
                ("HasTransportMission", "has_transport_mission", (data.Kind.BOOL,)),
                ("IsTransportingArmiesToTarget", "transporting_to_target", (data.Kind.BOOL,)),
                ("HasPendingPickup", "pending_pickup", (data.Kind.BOOL,)),
                ("GetNumTotalUnits", "required_units", (data.Kind.I32,)),
                ("GetLocationString", "source_location", (data.Kind.STRING,))):
            army_row[key], reason = await run.field("MilitaryFormation", method, (army,), kinds)
            if reason:
                army_row["unavailable"][key] = reason
        country = await run.country()
        fleets = []
        await run.provider("Country", "GetMilitaryFormationsFleet")
        async for _, element, fleet in run.elements(
                "Country.GetMilitaryFormationsFleet", (country,), "MilitaryFormation"):
            fleet_id = await run.origin(element, "MilitaryFormation")
            candidate = {"id": fleet_id, "unavailable": {}}
            for method, key, kinds in (
                    ("GetNameNoFormatting", "name", (data.Kind.STRING,)),
                    ("CanTransportArmy", "can_transport_an_army", (data.Kind.BOOL,)),
                    ("GetAvailableCarryingCapacity", "available_carrying_capacity", (data.Kind.I32,)),
                    ("GetCarryingCapacity", "carrying_capacity", (data.Kind.I32,)),
                    ("HasTransportedArmyOnboard", "has_army_onboard", (data.Kind.BOOL,)),
                    ("HasTransportMission", "has_transport_mission", (data.Kind.BOOL,)),
                    ("GetTransportMissionQueueCount", "transport_queue_count", (data.Kind.I32,)),
                    ("GetLocationString", "source_location", (data.Kind.STRING,))):
                candidate[key], reason = await run.field("MilitaryFormation", method, (fleet,), kinds)
                if reason:
                    candidate["unavailable"][key] = reason
            candidate.update(display_field(candidate.pop("name")))
            for source, key in (
                    ("MilitaryFormation.GetTransportedFormations", "transported_formations"),
                    ("MilitaryFormation.GetAttachedFormations", "attached_formations")):
                try:
                    candidate[key] = await _formation_source_rows(
                        run, fleet, source, "army")
                except _SCALAR_ERRORS as error:
                    candidate[key] = None
                    candidate["unavailable"][key] = str(error)
            candidate["eligible_for_army"] = None
            candidate["eligible_for_army_unavailable_reason"] = (
                "MilitaryFormation.CanTransportArmy has no Army argument and does not prove this pair")
            fleets.append(candidate)
        result = {
            "army": army_row,
            "fleets": fleets,
            "command_available": False,
            "command_unavailable_reason": (
                "TransportPlannerPopup.StartTransport needs popup-owned army, fleet, pickup and target state"),
            "marine_attach_command_available": False,
            "marine_attach_command_unavailable_reason": (
                "MilitaryFormationWrapped.EmbarkFormation(MilitaryFormation.Self) needs the "
                "right-click-manager wrapper that owns the selected army"),
        }
        result["status"] = "partial" if _has_unavailable(result) else "observed"
        return result
    return await owned_read(api, "military.transport_options", observe)


async def _formation_source_rows(run, invasion, source, kind):
    count = await _source_count(run, source, (invasion,))
    rows = []
    for index in range(count):
        raw_id, id_reason = await _copied(
            run, source, index, count, "MilitaryFormation.GetIDString", (invasion,),
            (data.Kind.STRING,))
        name, name_reason = await _copied(
            run, source, index, count, "MilitaryFormation.GetNameNoFormatting", (invasion,),
            (data.Kind.STRING,))
        identity = run.api._native_full_id(raw_id)
        row = {
            "source_index": index,
            "kind": kind,
            "id": identity,
            "id_raw": raw_id,
            "unavailable": {},
            **display_field(name),
        }
        if identity is None:
            row["unavailable"]["id"] = id_reason or "Formation ID string was not a complete native ID"
        if name_reason:
            row["unavailable"]["name"] = name_reason
        rows.append(row)
    return rows


async def _invasion_row(run, war_id, source_index, invasion_id, invasion):
    row = {
        "id": invasion_id,
        "war_id": war_id,
        "source_index": source_index,
        "identity_scope": "native_full_invasion_id_with_war_source",
        "atomic_snapshot": False,
        "unavailable": {},
    }
    _merge_fields(row, await run.fields(
        "Invasion", (invasion,), (
            ("GetName", "name", (data.Kind.STRING,)),
            ("GetTooltipTag", "tooltip_tag", (data.Kind.STRING,)),
            ("GetStatus", "invasion_status", (data.Kind.STRING,)),
            ("IsNavalInvasion", "naval", (data.Kind.BOOL,)),
            ("IsLandInvasion", "land", (data.Kind.BOOL,)),
            ("IsPreparing", "preparing", (data.Kind.BOOL,)),
            ("IsWaiting", "waiting", (data.Kind.BOOL,)),
            ("IsFightingLandBattle", "fighting_land_battle", (data.Kind.BOOL,)),
            ("IsFightingNavalBattle", "fighting_naval_battle", (data.Kind.BOOL,)),
            ("HasBlockingEnemyFleetOnSeaNode", "blocked_by_enemy_fleet", (data.Kind.BOOL,)),
            ("IsStalledDueToCommanderOrders", "stalled_by_commander_orders", (data.Kind.BOOL,)),
            ("GetPreparationDaysLeftRaw", "preparation_days_left", (data.Kind.I32,)),
            ("GetLandingProgress", "landing_progress", (data.Kind.FIXED,)),
            ("GetAdvancementProgress", "advancement_progress", (data.Kind.FIXED,)),
            ("GetProgress", "progress", (data.Kind.FIXED,)),
            ("GetNeededPowerProjection", "needed_power_projection", (data.Kind.FIXED,)),
            ("GetNeededPowerProjectionDesc", "needed_power_projection_description", (data.Kind.STRING,)),
            ("GetTotalPowerProjection", "total_power_projection", (data.Kind.FIXED,)),
            ("GetTargetStateFortificationUnitDefense", "target_fortification_defense", (data.Kind.FIXED,)),
            ("GetTargetStateFortificationBreakdown", "target_fortification_description", (data.Kind.STRING,)),
            ("GetNumBlockingFleets", "blocking_fleets", (data.Kind.I32,)),
            ("GetNumBlockingFleetsUnits", "blocking_fleet_units", (data.Kind.I32,)),
            ("GetNumInvadingArmies", "invading_armies_count", (data.Kind.I32,)),
            ("GetNumInvadingFleets", "invading_fleets_count", (data.Kind.I32,)),
            ("GetNumDefendingArmies", "defending_armies_count", (data.Kind.I32,)),
            ("GetNumDefendingFleets", "defending_fleets_count", (data.Kind.I32,)),
            ("HasEnded", "ended", (data.Kind.BOOL,)),
            ("HasSucceeded", "succeeded", (data.Kind.BOOL,)),
            ("HasFailed", "failed", (data.Kind.BOOL,)))))
    row.update(display_field(row.pop("name")))
    for getter, key, target, has_identity in (
            ("GetInvaderCountry", "invader_country", "Country", True),
            ("GetInvadedCountry", "invaded_country", "Country", True),
            ("GetSourceState", "source_state", "State", True),
            ("GetTargetState", "target_state", "State", True),
            ("GetFleet", "lead_fleet", "MilitaryFormation", True),
            ("GetDefenderHQ", "defender_hq", "Hq", False)):
        row[key], reason = await _related(
            run, invasion, "Invasion", getter, target, has_identity=has_identity)
        if reason:
            row["unavailable"][key] = reason
    row["sea_node"] = await _chain_fields(
        run, invasion, "Invasion", "GetSeaNode", "Province", (
            ("GetID", "id_raw", (data.Kind.STRING,)),
            ("GetIndex", "index", (data.Kind.I32,)),
            ("GetSeaNodeName", "name", (data.Kind.STRING,)),
        ))
    row["sea_node"].update(display_field(row["sea_node"].pop("name")))
    for source, key, kind in (
            ("Invasion.GetInvadingArmies", "invading_armies", "army"),
            ("Invasion.GetInvadingFleets", "invading_fleets", "fleet"),
            ("Invasion.GetDefendingArmies", "defending_armies", "army"),
            ("Invasion.GetDefendingFleets", "defending_fleets", "fleet")):
        try:
            row[key] = await _formation_source_rows(run, invasion, source, kind)
        except _SCALAR_ERRORS as error:
            row[key] = None
            row["unavailable"][key] = str(error)
    row["status"] = "partial" if _has_unavailable(row) else "observed"
    return row


async def invasions(api, war_id=None):
    """Read current invasions from each owned War.GetInvasions source."""
    async def observe(run):
        country = await run.country()
        await run.provider("Country", "AccessWars")
        rows, failures = [], []
        async for _, war_element, war in run.elements("Country.AccessWars", (country,), "War"):
            observed_war_id = await run.origin(war_element, "War")
            if war_id is not None and observed_war_id != war_id:
                continue
            try:
                await run.provider("War", "GetInvasions")
                async for index, element, invasion in run.elements(
                        "War.GetInvasions", (war,), "Invasion"):
                    invasion_id = await run.origin(element, "Invasion")
                    rows.append(await _invasion_row(
                        run, observed_war_id, index, invasion_id, invasion))
            except _SCALAR_ERRORS as error:
                failures.append({"war_id": observed_war_id, "reason": str(error)})
        return {
            "status": "partial" if failures or any(
                row.get("status") == "partial" for row in rows) else "observed",
            "invasions": rows,
            "identity_scope": "native_full_invasion_id_with_war_source",
            "mutation_available": True,
            "start_command_available": False,
            "start_command_unavailable_reason": (
                "InvasionPlannerPopup.StartInvasion needs popup-owned selected forces, source state "
                "and target state; no explicit standalone producer is proved"),
            "details": failures,
        }
    return await owned_read(api, "military.invasions", observe)




async def _invasion(run, war_id, wanted):
    if type(wanted) is not int or not 0 <= wanted < 0xFFFFFFFF:
        raise ValueError("invasion_id must be a complete uint32 Invasion ID")
    _, war = await _war(run, war_id)
    await run.provider("War", "GetInvasions")
    async for _, element, invasion in run.elements("War.GetInvasions", (war,), "Invasion"):
        if await run.origin(element, "Invasion") != wanted:
            continue
        # The selected original producer and its one native validation decide
        # command validity. A second scalar witness cannot gate this full ID.
        return invasion
    raise SourceUnavailable(
        f"Invasion {wanted} was not present in War {war_id} GetInvasions source")


async def invasion_action(api, war_id, invasion_id, action, *, formation_id=None,
                          validate_only=False):
    """Cancel or defend one exact native Invasion full reference in its War source."""
    if action not in ("cancel", "defend_army", "defend_fleet"):
        raise ValueError("unknown invasion action")
    if action != "cancel" and type(formation_id) is not int:
        raise ValueError("formation_id is required for a defend action")

    async def operation(run):
        invasion = await _invasion(run, war_id, invasion_id)
        if action == "cancel":
            expression = "Invasion.Cancel"
            bindings = (invasion,)
        else:
            kind = "army" if action == "defend_army" else "fleet"
            _, formation = await _formation(run, formation_id, kind=kind)
            if formation is None:
                raise SourceUnavailable(f"Formation is not in the country's current {kind} source")
            method = "DeployArmyToDefend" if kind == "army" else "DeployFleetToDefend"
            expression = f"Invasion.{method}(MilitaryFormation.Self)"
            bindings = (invasion, formation)
        result = {
            "war_id": war_id,
            "invasion_id": invasion_id,
            "formation_id": formation_id,
            "action": action,
            "accepted": None,
        }
        owner = await _produced_owner(run, expression, bindings)
        accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if accepted:
            try:
                result["observation"] = await invasions(api, war_id=war_id)
            except Exception as error:
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military.invasion_" + action, operation)


async def transfer_ship(api, ship_id, fleet_id, *, validate_only=False):
    """Transfer one Ship using the game's existing typed command reader.

    The shipped 1.13.11 transfer reader uses command token 24660 and the
    source/destination/Ship-vector fields below. Shared Run owns the actual
    deserialized command, validates it once and submits with ordinary flags 4.
    Native validation is authoritative; no extra raw-memory/RVA gate is added.
    """
    api._id("ship_id", ship_id)
    api._id("fleet_id", fleet_id)

    async def operation(run):
        _, ship = await _ship(run, ship_id)
        source, reason = await _formation_relation(run, ship, "Ship", "GetFleet")
        result = {"ship_id": ship_id, "requested_fleet_id": fleet_id,
                  "action": "transfer_ship", "accepted": None,
                  "source_fleet": source, "atomic_snapshot": False}
        source_id = source.get("id") if source is not None else None
        if reason or source_id is None:
            result.update(status="unavailable", reason=reason or "Native Ship has no observed source fleet ID")
            return result
        if source_id == fleet_id:
            result.update(status="unchanged", submitted=False, assigned_to_requested_fleet=True)
            return result

        await run.country()
        created = await run.ctx.query("military.ship_transfer.create", lambda: commands.create(
            run.native, 24660, actor_id=run.actor, world=run.world, parameters={
                12911: commands.UInt32(source_id),
                107: commands.UInt32(fleet_id),
                24647: (commands.UInt32(ship_id),),
            }, adapter=run.adapters["commands"], process=run.process))
        run.operations.append(("ship_transfer.create", created))
        result["creation_result"] = created
        if created.error or not created.command:
            if created.command:
                run.unadopted_command = created
            result.update(status="native_result", native_result=created, native_error=created.error)
            return result
        owner = run.adopt(created, expected_world=run.world)
        accepted = await _validate_submit(run, owner, result, validate_only=validate_only)
        if accepted:
            try:
                observed, reason = await _formation_relation(run, ship, "Ship", "GetFleet")
                result["observed_fleet"] = observed
                result["assigned_to_requested_fleet"] = (
                    observed.get("id") == fleet_id if observed is not None and not reason else None)
                if reason:
                    result["observation_error"] = reason
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
                if getattr(error, "query", None) is not None:
                    result["observation_query"] = error.query
        return result

    return await owned_read(api, "military.transfer_ship", operation)
