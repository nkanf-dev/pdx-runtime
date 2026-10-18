"""Native State economy and society observations (one explicit full State ID).

Frozen eco120 rows: eco.state.pollution, eco.state.urbanization,
eco.state.turmoil, eco.state.obstinance, eco.migration.flows,
eco.population.demographics. Return original fixed values without converting
to display percentages. A StateRegion
pollution field is read through the game's State.GetStateRegion promotion,
not by reconstructing a region ID from State.GetID.
"""
from __future__ import annotations

from pdx_runtime.v3_game._read import Field, read_state_fields

# Historical data_types_uncategorized.txt signatures: zero arguments; fixed =
# CFixedPoint, integer = int32/int64, text = CString, boolean = bool.
# The installed GUI map_list_panel.gui uses the same State.GetStateRegion chain.
# Units on ambiguous native fixed-point values are intentionally unspecified.
POLLUTION = (
    Field("pollution.generation", "State.GetPollutionGeneration", "fixed"),
    Field("pollution.level", "State.GetStateRegion.GetPollutionLevel", "fixed"),
    Field("pollution.impact", "State.GetStateRegion.GetPollutionImpact", "fixed"),
    Field("pollution.impact_daily_change", "State.GetStateRegion.GetPollutionImpactDailyChange", "fixed"),
    Field("pollution.health_effects_reduction", "State.GetPollutionHealthEffectsReduction", "fixed"),
)

URBANIZATION = (
    Field("urbanization.amount", "State.GetUrbanization", "integer", "native urbanization units"),
    Field("urbanization.needed_per_level", "State.GetUrbanizationNeededPerLevelRaw", "integer", "native urbanization units/level"),
    Field("urbanization.buildings_max_level_description", "State.GetUrbanizationBuildingsMaxLevelDesc", "text"),
)

TURMOIL = (
    Field("turmoil.amount", "State.CalcTurmoil", "fixed"),
    Field("turmoil.effects_description", "State.GetTurmoilEffects", "text"),
    Field("turmoil.has_effects", "State.HasTurmoilEffects", "boolean"),
)

OBSTINANCE = (
    Field("obstinance.amount", "State.GetObstinance", "fixed"),
    Field("obstinance.supporters", "State.GetNumObstinanceSupporters", "integer", "people"),
    Field("obstinance.effect_scale", "State.GetObstinanceEffectScale", "fixed"),
    Field("obstinance.effects_description", "State.GetObstinanceEffectsDesc", "text"),
)

MIGRATION = (
    Field("migration.weekly_immigration", "State.GetWeeklyImmigration", "integer", "people/week"),
    Field("migration.weekly_emigration", "State.GetWeeklyEmigration", "integer", "people/week"),
    Field("migration.weekly_net", "State.GetWeeklyPopNetMigration", "integer", "people/week"),
    Field("migration.annual_projected_net", "State.GetAnnualPopNetMigration", "integer", "people/year"),
    Field("migration.flow_description", "State.GetMigrationDesc", "text"),
    Field("migration.flow_and_slave_trade_description", "State.GetMigrationAndSlaveTradeDetailsDesc", "text"),
    Field("migration.details_description", "State.GetMigrationDetailsDesc", "text"),
)

# The installed English TOOLTIP_STATE_POPULATION formats birthrate and mortality
# as percentages "yearly". Keep the native fixed ratios, not multiplied by 100.
# STATE_POP_GROWTH displays GetPopNetGrowth as an integer; the source does not
# establish the integer count's period. GetPopTotalChange only drives an arrow.
DEMOGRAPHICS = (
    Field("demographics.birthrate", "State.GetPopBirthrate", "fixed", "ratio/year"),
    Field("demographics.mortality", "State.GetPopMortality", "fixed", "ratio/year"),
    Field("demographics.net_growth", "State.GetPopNetGrowth", "integer", "people (timeframe unproven)"),
    Field("demographics.total_change", "State.GetPopTotalChange", "integer", "people (timeframe unproven)"),
    Field("demographics.growth_description", "State.GetPopGrowthDesc", "text"),
)

GROUPS = {"pollution": POLLUTION, "urbanization": URBANIZATION, "turmoil": TURMOIL,
    "obstinance": OBSTINANCE, "migration": MIGRATION, "demographics": DEMOGRAPHICS}
FIELDS = tuple(field for group in GROUPS.values() for field in group)

# These are separate explicit reads. They do not enlarge the 27-field default
# state_conditions page. Native fixed values retain their original scale.
WORLD_MARKET_ACCESS = (
    Field("is_world_market_hub", "State.IsWorldMarketHub", "boolean"),
    Field("world_market_access", "State.GetWorldMarketAccess", "fixed"),
    Field("access_description", "State.GetWorldMarketAccessDesc", "text"),
    Field("trade_potential", "State.GetTradePotential", "fixed"),
    Field("trade_potential_description", "State.GetTradePotentialDesc", "text"),
)

# State.GetFoodSecurity is the original FoodSecurity promotion. Installed
# map_list_panel.gui:5125/5155 uses its mild/severe ratio getters directly.
# This is the State aggregate, not an invented Pop.GetFoodSecurityRaw value.
FOOD_SECURITY = (
    Field("population_total", "State.GetFoodSecurity.GetNumPopsTotal", "integer"),
    Field("starving_population", "State.GetFoodSecurity.GetNumStarvingPops", "integer"),
    Field("mild_starving_population", "State.GetFoodSecurity.GetNumStarvingPopsMild", "integer"),
    Field("severe_starving_population", "State.GetFoodSecurity.GetNumStarvingPopsSevere", "integer"),
    Field("starving_fraction", "State.GetFoodSecurity.GetPercentageStarvingPops", "fixed", "ratio"),
    Field("mild_starving_fraction", "State.GetFoodSecurity.GetPercentageStarvingPopsMild", "fixed", "ratio"),
    Field("severe_starving_fraction", "State.GetFoodSecurity.GetPercentageStarvingPopsSevere", "fixed", "ratio"),
    Field("has_famine", "State.GetFoodSecurity.HasFamine", "boolean"),
    Field("states_with_famine", "State.GetFoodSecurity.GetNumStatesWithFamineRaw", "integer"),
    Field("famine_description", "State.GetFamineDesc", "text"),
)


POPULATION_METRICS = (
    Field("population", "State.GetPopulationSize", "integer", "people"),
    Field("literacy", "State.GetLiteracy", "fixed", "ratio"),
    Field("unemployed_working_adults", "State.GetNumUnemployedWorkingAdults", "integer", "people"),
    Field("jobseekers", "State.GetNumJobseekers", "integer", "people"),
    Field("standard_of_living", "State.GetAverageStandardOfLivingAll", "fixed"),
    Field("standard_of_living_poor", "State.GetAverageStandardOfLivingPoor", "fixed"),
    Field("standard_of_living_middle", "State.GetAverageStandardOfLivingMiddle", "fixed"),
    Field("standard_of_living_rich", "State.GetAverageStandardOfLivingRich", "fixed"),
    Field("expected_standard_of_living", "State.GetExpectedStandardOfLivingAll", "fixed"),
    Field("expected_standard_of_living_poor", "State.GetExpectedStandardOfLivingPoor", "fixed"),
    Field("expected_standard_of_living_middle", "State.GetExpectedStandardOfLivingMiddle", "fixed"),
    Field("expected_standard_of_living_rich", "State.GetExpectedStandardOfLivingRich", "fixed"),
    Field("below_expected_standard_of_living_fraction", "State.GetPopulationRatioBelowExpectedStandardOfLiving", "fixed", "ratio"),
    Field("expected_standard_of_living_description", "State.GetExpectedSOLFactorsDescAll", "text"),
)


MIGRATION_CONTEXT = (
    Field("migration_pull", "State.GetMigrationPull", "fixed"),
    Field("migration_pull_description", "State.GetMigrationPullDesc", "text"),
    Field("migration_and_slave_trade_description", "State.GetMigrationAndSlaveTradeDetailsDesc", "text"),
    Field("is_mass_migration_target", "State.IsMassMigrationTarget", "boolean"),
    Field("mass_migration_culture_native_id", "State.GetMassMigrationCulture.GetID", "integer"),
    Field("mass_migration_culture_name", "State.GetMassMigrationCulture.GetNameNoFormatting", "text"),
    Field("mass_migration_origin_country_native_id", "State.GetMassMigrationOriginCountry.GetID", "integer"),
    Field("mass_migration_origin_country_tag", "State.GetMassMigrationOriginCountry.GetTagName", "text"),
    Field("mass_migration_origin_country_name", "State.GetMassMigrationOriginCountry.GetNameNoFormatting", "text"),
    Field("mass_migration_end_date_text", "State.GetMassMigrationEndDate", "text"),
    Field("owning_country_mass_migration_attraction", "State.GetCountry.GetLimitedMassMigrationCountryAttraction", "fixed"),
)


async def read_state_migration(ctx, state_id):
    """Read State pull and original mass-migration context, not a target graph.

    Culture.GetID is native uint32 and Country.GetID native int32. Return those
    direct getter values and raw observations; do not mask/reinterpret them as
    independently proved full-generation IDs. Tags/names and native date text
    stay distinct. Absent target promotions may return EMPTY or real errors;
    the native target flag and every copied prefix remain available beside them.
    """
    answer = await read_state_fields(ctx, state_id, MIGRATION_CONTEXT,
        label="economy.state_migration")
    answer = {**answer, **answer["values"]}
    answer["identity_semantics"] = {
        "mass_migration_culture_native_id": "original Culture.GetID uint32; no definition-key or full-ID inference",
        "mass_migration_origin_country_native_id": "original Country.GetID int32 and raw bits; full-generation semantics not independently proved",
        "mass_migration_origin_country_tag": "original Country.GetTagName text, not a fabricated three-byte tag",
        "mass_migration_culture_name": "native display name, not an identifier",
        "mass_migration_origin_country_name": "native display name, not an identifier"}
    answer["scope"] = "one explicitly bound State; not discovery of all targets, origin States or culture/community eligibility"
    return answer


async def read_state_population_metrics(ctx, state_id):
    """Read original State population, literacy, employment and SoL metrics."""
    answer = await read_state_fields(ctx, state_id, POPULATION_METRICS,
        label="economy.state_population_metrics")
    return {**answer, **answer["values"]}


async def read_state_world_market_access(ctx, state_id):
    """Read the selected State's hub flag, access and trade potential."""
    answer = await read_state_fields(ctx, state_id, WORLD_MARKET_ACCESS,
        label="economy.state_world_market_access")
    return {**answer, **answer["values"]}


async def read_state_food_security(ctx, state_id):
    """Read original State FoodSecurity totals, ratios and famine observations.

    The native aggregate does not identify individual Pops or replace each
    Pop's raw food-security value. Ratios are not converted to percentages.
    """
    answer = await read_state_fields(ctx, state_id, FOOD_SECURITY,
        label="economy.state_food_security")
    return {**answer, **answer["values"]}


async def read_state_conditions(ctx, state_id):
    """Observe native state conditions, retaining errors and original text.

    Native projection pages are separate observations rather than an atomic
    state snapshot. A missing or failed getter makes its group and result
    incomplete; merely finding the getter does not establish availability.
    annual_projected_net is the native annual projection, not a historical
    annual total. annual_net remains an alias of that same copied value.
    """
    answer = await read_state_fields(ctx, state_id, FIELDS, label="economy.state_conditions")
    for name, fields in GROUPS.items():
        keys = {field.name: field.name.split(".", 1)[1] for field in fields}
        values = {short: answer["values"][qualified] for qualified, short in keys.items()}
        observations = {short: answer["observations"][qualified] for qualified, short in keys.items()}
        reasons = {short: answer["reasons"][qualified] for qualified, short in keys.items()
            if qualified in answer["reasons"]}
        answer[name] = {**values, "observations": observations, "reasons": reasons,
            "units": {short: answer["units"][qualified] for qualified, short in keys.items()
                if qualified in answer["units"]},
            "sources": {short: answer["sources"][qualified] for qualified, short in keys.items()},
            "complete": answer["world_consistent"] and
                all(row["complete"] for row in observations.values())}
    # One original getter/copy; the earlier short name remains compatible.
    old_name, canonical = "annual_net", "annual_projected_net"
    migration = answer["migration"]
    migration[old_name] = migration[canonical]
    for bucket in ("observations", "reasons", "units", "sources"):
        if canonical in migration[bucket]:
            migration[bucket][old_name] = migration[bucket][canonical]
    for bucket in ("values", "observations", "reasons", "units", "sources"):
        qualified = "migration." + canonical
        if qualified in answer[bucket]:
            answer[bucket]["migration." + old_name] = answer[bucket][qualified]
    answer["aliases"] = {"migration.annual_net": "migration.annual_projected_net"}
    migration["semantics"] = {"annual_projected_net":
        "Native annual projected net migration, not a historical annual total or annual gross flow."}
    return answer


HARVEST_CONDITIONS = (
    Field("name", "HarvestCondition.GetNameNoFormatting", "text"),
    Field("type_name", "HarvestCondition.GetType.GetNameNoFormatting", "text"),
    Field("type_description", "HarvestCondition.GetType.GetDescription", "text"),
    Field("intensity", "HarvestCondition.GetIntensity", "fixed", "native effect multiplier"),
    Field("duration_days", "HarvestCondition.GetDurationDays", "integer", "days"),
    Field("remaining_days", "HarvestCondition.GetRemainingDays", "integer", "days"),
    Field("remaining_duration_label", "HarvestCondition.GetRemainingDurationLabel", "text"),
    Field("remaining_duration_range", "HarvestCondition.GetRemainingDurationRange", "text"),
    Field("effects_description", "HarvestCondition.GetEffectDesc", "text"),
    Field("type_effects_description", "HarvestCondition.GetType.GetEffectDesc", "text"),
    Field("affected_state_native_id", "HarvestCondition.GetState.GetID", "integer"),
    Field("affected_state_name", "HarvestCondition.GetState.GetNameNoFormatting", "text"),
    Field("country_native_id", "HarvestCondition.GetState.GetCountry.GetID", "integer"),
    Field("country_tag", "HarvestCondition.GetState.GetCountry.GetTagName", "text"),
)


async def read_state_harvest(ctx, state_id):
    """Copy original State harvest rows in the same callback as their source.

    The native row owner pins its wrapper, not the array, active condition or
    definition. Original Binding0/1 stays scoped. Display/type relations remain
    separate from stable keys, and no instance-ID requirement is invented.
    """
    from pdx_runtime import v3_data as data
    from pdx_runtime.v3_game._projection import _projection_source_pages
    from pdx_runtime.v3_game._read import copy_source_page
    if type(state_id) is not int or not 0 <= state_id <= 0xffffffff:
        raise ValueError("state_id must be a full uint32 native State ID")
    answer = {"requested_state_id": state_id, "source": "State.GetHarvestConditions",
        "world": None, "world_consistent": True, "source_count": None,
        "source_complete": False, "complete": False, "conditions": [],
        "source_observations": [], "reasons": {}, "cleanup_warnings": [],
        "sources": {field.name: field.expression for field in HARVEST_CONDITIONS},
        "units": {field.name: field.unit for field in HARVEST_CONDITIONS if field.unit is not None},
        "snapshot": "fresh original source per callback/page; no array/condition/definition or all-leaf coherence lease",
        "identity_semantics": "native State/Country.GetID values and raw bits; names are display, no inferred type key/instance ID",
        "remaining": ("copied stable type-key native profile not implemented",
            "structured modifier vectors and transitive opaque-leaf/runtime acceptance")}
    try:
        binding = data.ObjectBinding.state(await ctx.type_id("State"), state_id)
        iterator = _projection_source_pages(ctx, "economy.state_harvest",
            tuple(field.expression for field in HARVEST_CONDITIONS), ((binding,),),
            ("State.GetHarvestConditions",))
        async for _, source, page, cells in iterator:
            copy_source_page(answer, HARVEST_CONDITIONS, source, page, cells, rows_key="conditions")
            answer["cleanup_warnings"].extend(diagnostic for diagnostic in ctx.diagnostics.get("operations", ())
                if isinstance(diagnostic, dict) and diagnostic.get("result") is page
                and "cleanup_error" in diagnostic)
    except BaseException as error:
        partial = getattr(getattr(error, "query", None), "partial_result", None)
        if isinstance(partial, data.ProjectionPage):
            copy_source_page(answer, HARVEST_CONDITIONS, partial, partial, partial.cells,
                rows_key="conditions")
        error.harvest_observations = answer
        raise
    count = answer["source_count"]
    positions = [row["source_index"] for row in answer["conditions"]]
    coverage = (count is not None and len(positions) == count
        and len(set(positions)) == count and all(0 <= index < count for index in positions))
    answer["complete"] = bool(answer["source_complete"] and coverage
        and answer["world_consistent"] and not answer["reasons"]
        and all(row["complete"] for row in answer["conditions"]))
    return answer
