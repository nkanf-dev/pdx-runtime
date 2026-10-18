"""Original country/formation military logistics observations and allocation.

Construction level controls are the five original construction-panel producers.
No capacity ratio is reverse-labelled as a selected policy without a getter.
"""
from ._warfare import (owned_read, _formation, _produced_owner, _validate_submit)
from ... import v3_data as data


_NETWORK = (
    ("GetNumSupplyShips", "supply_ships", data.Kind.I32),
    ("GetTotalSupplyShipCount", "total_supply_ships", data.Kind.I32),
    ("GetNumAvailableSupplyShips", "available_supply_ships", data.Kind.I32),
    ("GetNumAssignedSupplyShips", "assigned_supply_ships", data.Kind.I32),
    ("GetNumRequiredSupplyShips", "required_supply_ships", data.Kind.I32),
    ("GetNumAssignedSupplyShipsToArmies", "army_assigned_supply_ships", data.Kind.I32),
    ("GetNumAssignedSupplyShipsToFleets", "fleet_assigned_supply_ships", data.Kind.I32),
    ("GetNumRequiredSupplyShipsToArmies", "army_required_supply_ships", data.Kind.I32),
    ("GetNumRequiredSupplyShipsToFleets", "fleet_required_supply_ships", data.Kind.I32),
    ("GetSupplyShipsArmiesBreakdown", "army_supply_description", data.Kind.STRING),
    ("GetSupplyShipsFleetsBreakdown", "fleet_supply_description", data.Kind.STRING),
    ("GetSupplyNetworkStrength", "network_strength", data.Kind.FIXED),
    ("GetSupplyNetworkStrengthTarget", "network_strength_target", data.Kind.FIXED),
    ("GetSupplyNetworkStrengthWeeklyChange", "network_strength_weekly_change", data.Kind.FIXED),
    ("GetSupplyNetworkNumSeaNodes", "sea_node_count", data.Kind.I32),
    ("GetSupplyNetworkNumSeaNodesDesc", "sea_node_description", data.Kind.STRING),
    ("GetDeficitSupplyShipRatioForProgressBar", "deficit_supply_ship_ratio", data.Kind.FIXED),
)
_COSTS = (
    ("GetSupplyConstructionCapacity", "supply_construction_capacity", data.Kind.FIXED),
    ("GetFreeSupplyConstructionCapacity", "free_supply_construction_capacity", data.Kind.FIXED),
    ("GetSupplyConstructionQueueUsage", "supply_queue_usage", data.Kind.FIXED),
    ("GetSupplyConstructionUsageTotal", "supply_construction_usage", data.Kind.FIXED),
    ("GetSupplyConstructionQueueCount", "supply_queue_count", data.Kind.I32),
    ("GetMilitaryConstructionCapacity", "military_construction_capacity", data.Kind.FIXED),
    ("GetMilitaryConstructionUsageTotal", "military_construction_usage", data.Kind.FIXED),
    ("GetMilitaryConstructionQueueUsage", "military_queue_usage", data.Kind.FIXED),
    ("GetSupplyMaintenanceUsage", "supply_maintenance_usage", data.Kind.FIXED),
    ("GetSupplyMaintenanceFulfillment", "supply_maintenance_fulfillment", data.Kind.FIXED),
    ("GetSupplyMaintenanceDemand", "supply_maintenance_demand", data.Kind.FIXED),
    ("GetCurrentSailors", "current_sailors", data.Kind.I32),
    ("GetNeededSailors", "needed_sailors", data.Kind.I32),
    ("HasInsufficientMaxSailors", "insufficient_max_sailors", data.Kind.BOOL),
    ("GetSupplyShipConstructionGoodsExpenses", "supply_ship_construction_goods_expenses", data.Kind.FIXED),
    ("GetSupplyShipMaintenanceExpenses", "supply_ship_maintenance_expenses", data.Kind.FIXED),
    ("GetMilitaryGoodsExpenses", "military_goods_expenses", data.Kind.FIXED),
    ("GetMilitaryGoodsExpensesByGoodsDesc", "military_goods_cost_description", data.Kind.STRING),
)
_LEVELS = {"very_low": "VeryLow", "low": "Low", "medium": "Medium", "high": "High", "very_high": "VeryHigh"}


async def _read_fields(run, named_type, binding, specs):
    return {"atomic_snapshot": False, **await run.fields(named_type, (binding,), specs)}


async def supply_network(api):
    async def observe(run):
        country = await run.country()
        return {"country_id": run.actor, "status": "observed", **await _read_fields(run, "Country", country, _NETWORK)}
    return await owned_read(api, "military.supply_network", observe)


async def logistics_costs(api):
    async def observe(run):
        country = await run.country()
        return {"country_id": run.actor, "status": "observed", **await _read_fields(run, "Country", country, _COSTS)}
    return await owned_read(api, "military.logistics_costs", observe)


async def supply_allocation_options(api):
    async def observe(run):
        country = await run.country()
        rows = []
        for key, suffix in _LEVELS.items():
            description, reason = await run.field("Country", "GetSupplyShipConstructionLevelDesc" + suffix,
                (country,), (data.Kind.STRING,))
            rows.append({"level": key, "description": description, "description_unavailable_reason": reason,
                         "selected": None, "eligible": None})
        return {"country_id": run.actor, "status": "observed", "levels": rows,
                "selected_level": None, "selected_level_unavailable_reason": "The original Country API has no dedicated selected construction-level getter",
                "capacity_observation": await _read_fields(run, "Country", country, _COSTS[:8])}
    return await owned_read(api, "military.supply_allocation_options", observe)


async def set_supply_allocation(api, level):
    if level not in _LEVELS:
        raise ValueError("level must be very_low, low, medium, high or very_high")
    async def operation(run):
        country = await run.country()
        owner = await _produced_owner(run, "Country.SetSupplyShipConstructionLevel" + _LEVELS[level], (country,))
        result = {"country_id": run.actor, "level": level, "action": "set_supply_allocation", "accepted": None}
        accepted = await _validate_submit(run, owner, result)
        if accepted:
            try:
                result["observation"] = await _read_fields(run, "Country", country, _COSTS[:8])
                result["observed_selected_level"] = None
                result["selected_level_unavailable_reason"] = "Native capacity was observed; no dedicated selected-level getter is exposed"
            except Exception as error:
                run.retain_unknown = True
                result["observation_error"] = str(error)
        return result
    return await owned_read(api, "military.set_supply_allocation", operation)


async def attrition(api, formation_id):
    async def observe(run):
        _, formation = await _formation(run, formation_id)
        if formation is None:
            return {"formation_id": formation_id, "status": "not_found"}
        fields = (
            ("GetAttritionRaw", "attrition", data.Kind.FIXED),
            ("GetAverageAttritionPerWeek", "average_weekly_attrition", data.Kind.FIXED),
            ("GetAttritionRiskDesc", "attrition_risk", data.Kind.STRING),
            ("HasLogisticsCenterGoodsShortage", "logistics_goods_shortage", data.Kind.BOOL),
            ("GetSupplyShortageRatio", "supply_shortage_ratio", data.Kind.FIXED),
            ("GetEffectiveSupplyDistance", "effective_supply_distance", data.Kind.FIXED),
            ("HasSupplyShortageOrganizationPenalty", "supply_shortage_organization_penalty", data.Kind.BOOL),
            ("GetGoodsConsumptionCostRaw", "goods_consumption_cost", data.Kind.FIXED),
            ("GetGoodsConsumptionDesc", "goods_consumption_description", data.Kind.STRING),
        )
        return {"formation_id": formation_id, "status": "observed",
                **await _read_fields(run, "MilitaryFormation", formation, fields)}
    return await owned_read(api, "military.attrition", observe)
