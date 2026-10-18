"""Country budget wiring and the other original V3 economy observations.

Budget groups follow budget_panel.gui and its money/credit tooltips. Native
values are never replaced by host sums, inferred debts or display rounding.
Names/kinds also have archived data_types_uncategorized.txt metadata evidence.
"""
from pdx_runtime.v3_game._read import Field, NativeExpression


# UI headline and the separate non-prediction remain visible together. Amounts
# are stocks (pounds) or weekly flows (pounds/week); rates stay native fractions.
BUDGET_OVERVIEW = (
    Field("balance.predicted_weekly", "Country.PredictWeeklyBalance", "fixed", "pounds/week"),
    Field("balance.native_weekly", "Country.GetWeeklyBalance", "fixed", "pounds/week"),
    Field("balance.predicted_without_temporary", "Country.PredictBalanceWithoutTemporaryIncomeAndExpenses", "fixed", "pounds/week"),
    Field("income.predicted_weekly", "Country.PredictWeeklyIncome", "fixed", "pounds/week"),
    Field("income.native_weekly", "Country.GetWeeklyIncome", "fixed", "pounds/week"),
    Field("income.predicted_fixed", "Country.PredictWeeklyFixedIncome", "fixed", "pounds/week"),
    Field("income.temporary", "Country.GetWeeklyTemporaryIncome", "fixed", "pounds/week"),
    Field("expenses.weekly", "Country.GetWeeklyExpenses", "fixed", "pounds/week"),
    Field("expenses.fixed", "Country.GetWeeklyFixedExpenses", "fixed", "pounds/week"),
    Field("expenses.temporary", "Country.GetWeeklyTemporaryExpenses", "fixed", "pounds/week"),
    Field("assets.gold_reserves", "Country.GetGoldReserves", "fixed", "pounds"),
    Field("assets.investment_pool", "Country.GetInvestmentPool", "fixed", "pounds"),
    Field("assets.investment_pool_weekly_change", "Country.GetInvestmentPoolChange", "fixed", "pounds/week"),
    Field("credit.principal", "Country.GetPrincipal", "fixed", "pounds"),
    Field("credit.self_held_debt", "Country.GetGovernmentSelfDebt", "fixed", "pounds"),
    Field("credit.self_held_fraction", "Country.GetGovernmentSelfDebtFraction", "fixed", "ratio"),
    Field("credit.limit", "Country.GetMaxCredit", "fixed", "pounds"),
    Field("credit.remaining", "Country.GetRemainingCredit", "fixed", "pounds"),
    Field("credit.used_fraction", "Country.GetCreditRatio", "fixed", "ratio"),
    Field("credit.interest.weekly_payment", "Country.GetInterestPayment", "fixed", "pounds/week"),
    Field("credit.interest.weekly_rate", "Country.GetWeeklyInterestRate", "fixed", "ratio/week"),
    Field("credit.interest.yearly_rate", "Country.GetYearlyInterestRate", "fixed", "ratio/year"),
)

# Budget overview rows and the income/expenses tooltip categories. These are
# independent native observations, not a promise that every category adds to
# a total. Interest is already present at credit.interest.weekly_payment.
BUDGET_CATEGORIES = (
    Field("income.categories.income_tax", "Country.GetIncomeTaxIncome", "fixed", "pounds/week"),
    Field("income.categories.poll_tax", "Country.GetPollTaxIncome", "fixed", "pounds/week"),
    Field("income.categories.consumption_tax", "Country.PredictConsumptionTaxes", "fixed", "pounds/week"),
    Field("income.categories.dividend_tax", "Country.PredictDividendsTaxes", "fixed", "pounds/week"),
    Field("income.categories.tariffs", "Country.PredictTariffs", "fixed", "pounds/week"),
    Field("income.categories.minting", "Country.PredictMinting", "fixed", "pounds/week"),
    Field("income.categories.government_dividends", "Country.PredictGovernmentShareDividends", "fixed", "pounds/week"),
    Field("income.categories.investment_pool_transfer", "Country.GetInvestmentIncome", "fixed", "pounds/week"),
    Field("income.categories.diplomatic_pacts", "Country.PredictDiplomaticPactsIncome", "fixed", "pounds/week"),
    Field("income.categories.treaties", "Country.PredictTreatyIncome", "fixed", "pounds/week"),
    Field("income.categories.power_bloc", "Country.GetPowerBlocIncomeRaw", "fixed", "pounds/week"),
    Field("income.categories.piracy", "Country.GetPiracyIncome", "fixed", "pounds/week"),
    Field("income.categories.market_fees", "Country.GetMarketFeesIncome", "fixed", "pounds/week"),
    Field("expenses.categories.construction.total", "Country.GetConstructionGoodsExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.construction.government", "Country.GetGovernmentConstructionGoodsExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.construction.private", "Country.GetPrivateConstructionGoodsExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.military_ship_construction", "Country.GetMilitaryShipConstructionGoodsExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.supply_ship_construction", "Country.GetSupplyShipConstructionGoodsExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.government_wages", "Country.PredictGovernmentWagesExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.government_goods", "Country.GetGovernmentGoodsExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.government_slaves", "Country.GetGovernmentSlavesExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.military_wages", "Country.PredictMilitaryWagesExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.military_goods", "Country.GetMilitaryGoodsExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.military_slaves", "Country.GetMilitarySlavesExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.supply_ship_maintenance", "Country.GetSupplyShipMaintenanceExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.military_ship_maintenance", "Country.GetMilitaryShipMaintenanceExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.port_connections", "Country.GetPortConnectionExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.welfare_payments", "Country.GetWelfarePaymentsExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.subsidies", "Country.GetSubsidiesExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.subventions", "Country.GetSubventionsExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.government_share_losses", "Country.PredictGovernmentShareLosses", "fixed", "pounds/week"),
    Field("expenses.categories.diplomatic_pacts", "Country.PredictDiplomaticPactsExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.treaties", "Country.PredictTreatyExpenses", "fixed", "pounds/week"),
    Field("expenses.categories.power_bloc", "Country.GetPowerBlocExpenseRaw", "fixed", "pounds/week"),
    Field("taxation.unrealized", "Country.GetUnrealizedTaxes", "fixed", "pounds/week"),
    Field("taxation.average_collection_multiplier", "Country.GetAverageStateTaxCollectionMult", "fixed", "ratio"),
    Field("taxation.average_waste", "Country.GetAverageTaxWaste", "fixed", "ratio"),
)

# These two UI rows have real native string arguments. Keep the expressions
# intact and use ordinary evaluation, not invented zero-argument getters.
BUDGET_CATEGORY_EXPRESSIONS = (
    NativeExpression("income.categories.additional_modifier", "Country.GetModifier.GetValueFor('country_tax_income_add')", "fixed", "pounds/week"),
    NativeExpression("expenses.categories.additional_modifier", "Country.GetModifier.GetValueFor('country_expenses_add')", "fixed", "pounds/week"),
)

BUDGET_EXPLANATIONS = (
    Field("explanations.balance", "Country.PredictWeeklyBalanceWithEconomyHealthHint", "text"),
    Field("explanations.income", "Country.GetIncomeBreakdown", "text"),
    Field("explanations.expenses", "Country.GetExpensesBreakdown", "text"),
    Field("explanations.credit", "Country.GetCreditTooltip", "text"),
    Field("explanations.interest", "Country.GetInterestRateTooltip", "text"),
)


MINTING = (
    Field("predicted_weekly_income", "Country.PredictMinting", "fixed", "pounds/week"),
    Field("prediction_description", "Country.PredictMintingDesc", "text"),
    Field("reserve_limit", "Country.GetGoldReserveLimit", "fixed", "pounds"),
    Field("reserve_limit_description", "Country.GetGoldReserveLimitDesc", "text"),
    Field("reserves", "Country.GetGoldReserves", "fixed", "pounds"),
    Field("reserve_ratio", "Country.GetGoldReserveRatio", "fixed", "ratio"),
)

BANKRUPTCY = (
    Field("consequences.description", "Country.GetDeclareBankruptcyDesc", "text"),
    Field("balance.predicted_after_bankruptcy", "Country.GetPredictedBalanceFromBankruptcy", "fixed", "pounds/week"),
    Field("consequences.radicalization", "Country.GetRadicalizationFromBankruptcy", "integer", "people"),
    Field("credit.principal", "Country.GetPrincipal", "fixed", "pounds"),
    Field("credit.self_held_debt", "Country.GetGovernmentSelfDebt", "fixed", "pounds"),
    Field("credit.self_held_fraction", "Country.GetGovernmentSelfDebtFraction", "fixed", "ratio"),
)

INVESTMENT_POOL = (
    Field("balance", "Country.GetInvestmentPool", "fixed", "pounds"),
    Field("weekly_change", "Country.GetInvestmentPoolChange", "fixed", "pounds/week"),
    Field("contributions_description", "Country.GetInvestmentPoolChangeFromInvestmentDesc", "text"),
    Field("contributions_breakdown", "Country.GetInvestmentPoolChangeFromInvestmentBreakdown", "text"),
    Field("construction_spending_description", "Country.GetInvestmentPoolChangeFromConstructionDesc", "text"),
    Field("private_construction_goods_expenses", "Country.GetPrivateConstructionGoodsExpenses", "fixed", "pounds/week"),
    Field("next_investment_description", "Country.GetInvestmentPoolPredictedNextBuildingDesc", "text"),
    Field("show_next_investment", "Country.ShowInvestmentPoolPredictedNextBuilding", "boolean"),
    Field("blockers_description", "Country.GetLackOfAutonomousInvestmentReasonDesc", "text"),
    Field("previous_investments_description", "Country.GetPreviousAutonomousInvestmentsDesc", "text"),
    Field("previous_investment_count", "Country.GetNumPreviousAutonomousInvestments", "integer", "investments"),
)


# Original Pop functions, data_types_uncategorized.txt:60010-60934. These
# scalar reads keep quantities and descriptions, not localized-name identities.
# In particular, current/expected SoL and wealth are native int32, not fixed.
POP_ECONOMY = (
    Field("identity.name", "Pop.GetNameNoFormatting", "text"),
    Field("identity.profession_name", "Pop.GetPopType.GetNameNoFormatting", "text"),
    Field("size.total", "Pop.GetTotalSize", "integer", "people"),
    Field("size.workforce", "Pop.GetNumWorkforce", "integer", "people"),
    Field("size.dependents", "Pop.GetDependentsSize", "integer", "people"),
    Field("employment.is_employed", "Pop.IsEmployed", "boolean"),
    Field("employment.is_looking_for_job", "Pop.IsLookingForJob", "boolean"),
    Field("employment.workplace_description", "Pop.GetWorksAt", "text"),
    Field("employment.is_slave", "Pop.IsSlave", "boolean"),
    Field("job_satisfaction.current", "Pop.GetCurrentJobSatisfaction", "fixed"),
    Field("job_satisfaction.next_description", "Pop.GetNextJobSatisfactionDesc", "text"),
    Field("literacy.rate", "Pop.GetLiteracyRate", "fixed", "ratio"),
    Field("literacy.education_access", "Pop.GetEducationAccess", "fixed"),
    Field("literacy.growth", "Pop.GetLiteracyGrowth", "fixed"),
    Field("literacy.growth_description", "Pop.GetLiteracyGrowthDesc", "text"),
    Field("standard_of_living.current", "Pop.GetCurrentStandardOfLiving", "integer"),
    Field("standard_of_living.expected", "Pop.GetExpectedStandardOfLiving", "integer"),
    Field("standard_of_living.expected_description", "Pop.GetExpectedStandardOfLivingDesc", "text"),
    Field("wealth_income.wealth", "Pop.GetCurrentWealth", "integer"),
    Field("wealth_income.wealth_progression", "Pop.GetWealthProgression", "fixed"),
    Field("wealth_income.income", "Pop.GetIncome", "fixed"),
    Field("wealth_income.workforce_income", "Pop.GetWorkforceIncome", "fixed"),
    Field("wealth_income.dependent_income", "Pop.GetDependentIncome", "fixed"),
    Field("wealth_income.weekly_expenses", "Pop.GetWeeklyExpenses", "fixed"),
    Field("wealth_income.average_annual_wage", "Pop.GetAverageAnnualWage", "fixed"),
    Field("wealth_income.balance_description", "Pop.GetIncomeExpensesBalanceDesc", "text"),
    Field("wealth_income.shares_description", "Pop.GetSharesDesc", "text"),
    Field("needs.description", "Pop.GetNeedsDesc", "text"),
    Field("food_security.raw", "Pop.GetFoodSecurityRaw", "fixed"),
    Field("food_security.description", "Pop.GetFoodSecurityDesc", "text"),
    Field("food_security.is_starving", "Pop.IsInStarvation", "boolean"),
    Field("food_security.is_severely_starving", "Pop.IsInSevereStarvation", "boolean"),
)


def _pop_record(answer, observed):
    """Expose the original explicit-ID record; never join rows by order."""
    answer["native_records"] = observed.records
    answer["native_record_world"] = observed.world
    answer["native_record_error"] = observed.error
    if observed.world != answer["world"]:
        answer["world_consistent"] = False
        answer["reasons"]["_world"] = "world changed between separate Pop observations"
    if observed.error:
        answer["reasons"]["_native_record"] = f"native Pop record error {observed.error}"
    matches = [record for record in observed.records if record.requested_id == answer["pop_id"]]
    if len(matches) != 1:
        answer["reasons"]["_native_record"] = "native Pop source did not copy one matching requested full ID"
        answer["complete"] = False
        return
    record = matches[0]
    answer["native_record"] = record
    answer["state_id"] = record.state_id
    answer["country_id"] = record.country_id
    answer["native_quantities"] = {"total": record.total_size,
        "workforce": record.workforce, "dependents": record.dependents}
    answer["native_errors"] = {"record": record.error, "quantities": record.quantities_error,
        "state": record.state_error, "country": record.country_error}
    if (record.error or record.quantities_error or record.state_error or record.country_error
            or record.resolved_id != answer["pop_id"]):
        answer["reasons"]["_native_record"] = "native Pop record carries errors or a different resolved full ID"
    answer["complete"] = bool(answer["complete"] and answer["world_consistent"]
        and not answer["reasons"].get("_native_record"))


async def read_pop_economy(ctx, pop_id):
    """Read one real Pop's economy/society values and explicit-ID relations.

    Scalars use one shared page on current adapters. The separate original
    PopDetails record supplies State/Country IDs and quantities, with its own
    errors. Neither source gates or replaces values already copied by the other.
    Native needs and shares remain descriptions, not fabricated structured goods
    quantities or dividends. No culture/acceptance strategy is applied here.
    """
    from pdx_runtime import v3_objects as objects
    from pdx_runtime.v3_game._read import read_pop_fields
    answer = await read_pop_fields(ctx, pop_id, POP_ECONOMY, label="economy.pop_details")
    for name in dict.fromkeys(field.name.split(".", 1)[0] for field in POP_ECONOMY):
        fields = [field for field in POP_ECONOMY if field.name.startswith(name + ".")]
        answer[name] = {field.name.split(".", 1)[1]: answer["values"][field.name] for field in fields}
    answer.update(state_id=None, country_id=None, native_record=None,
        native_records=(), native_errors={}, native_quantities=None)
    try:
        observed = await ctx.query("economy.pop_details.identity", lambda: objects.read_pop_details(
            ctx.native, (pop_id,), adapter=ctx.adapters["objects"], process=ctx.process))
    except BaseException as error:
        partial = getattr(getattr(error, "query", None), "partial_result", None)
        if isinstance(partial, objects.ObjectsResult):
            _pop_record(answer, partial)
        answer["complete"] = False
        answer["reasons"]["_native_record"] = f"Pop identity query interrupted: {error}"
        error.pop_observations = answer
        raise
    _pop_record(answer, observed)
    return answer


POPULATION_METRICS = (
    Field("population", "Country.GetTotalPopulation", "integer", "people"),
    Field("literacy_total", "Country.GetTotalLiteracyRate", "fixed", "ratio"),
    Field("literacy_incorporated", "Country.GetIncorporatedLiteracyRate", "fixed", "ratio"),
    Field("standard_of_living", "Country.GetAverageSoLByPopulation", "fixed"),
    Field("expected_standard_of_living", "Country.GetExpectedSoLByPopulation", "fixed"),
    Field("standard_of_living_poor", "Country.GetAverageStandardOfLivingPoor", "fixed"),
    Field("standard_of_living_middle", "Country.GetAverageStandardOfLivingMiddle", "fixed"),
    Field("standard_of_living_rich", "Country.GetAverageStandardOfLivingRich", "fixed"),
    Field("expected_standard_of_living_poor", "Country.GetExpectedStandardOfLivingPoor", "fixed"),
    Field("expected_standard_of_living_middle", "Country.GetExpectedStandardOfLivingMiddle", "fixed"),
    Field("expected_standard_of_living_rich", "Country.GetExpectedStandardOfLivingRich", "fixed"),
    Field("expected_standard_of_living_description", "Country.GetExpectedSOLFactorsDescAll", "text"),
    Field("additional_standard_of_living_description", "Country.GetAdditionalSOLFactorsDescAll", "text"),
    Field("standard_of_living_description", "Country.GetSOLDescForAllStratas", "text"),
)


POP_NEED_GOODS = (
    Field("goods_key", "PopConsumptionGoods.GetGoods.GetKey", "text"),
    Field("goods_name", "PopConsumptionGoods.GetGoods.GetNameNoFormatting", "text"),
    Field("consumption_amount", "PopConsumptionGoods.GetConsumptionAmount", "fixed"),
    Field("budget_share", "PopConsumptionGoods.GetConsumptionRatio", "fixed", "ratio"),
    Field("needs_description", "PopConsumptionGoods.GetPopNeedsDesc", "text"),
    Field("consumption_factor_description", "PopConsumptionGoods.GetPop.GetConsumptionFactorDesc", "text"),
    Field("state_price", "PopConsumptionGoods.GetGoods.GetStatePrice", "fixed"),
    Field("state_price_description", "PopConsumptionGoods.GetGoods.GetStatePriceDesc", "text"),
    Field("state_shortage_description", "PopConsumptionGoods.GetGoods.GetStateGoodsShortageDesc", "text"),
)


def _pop_needs_page(answer, source, page, cells):
    from pdx_runtime.v3_game._read import copy_source_page
    copy_source_page(answer, POP_NEED_GOODS, source, page, cells, rows_key="goods")

async def read_pop_needs(ctx, pop_id, *, top=False):
    """Copy original full/top Pop goods rows in each original Page callback.

    Row Binding0/1 stays scoped; genuine mode3 is consumed by the same native
    mechanism. Nothing is externally retained, retagged or joined by an index.
    Native rows/arrays remain mutable. This is not a transitive no-reentry,
    source-coherence or frozen-contract acceptance claim.
    """
    from pdx_runtime import v3_data as data
    from pdx_runtime.v3_game._projection import _projection_source_pages
    if type(pop_id) is not int or not 0 <= pop_id <= 0xffffffff:
        raise ValueError("pop_id must be a full uint32 native Pop ID")
    if type(top) is not bool:
        raise TypeError("top must be bool")
    source_name = "Pop.GetTopGoodsConsumption" if top else "Pop.GetGoodsConsumption"
    answer = {"pop_id": pop_id, "source": source_name, "top": top,
        "world": None, "world_consistent": True, "source_count": None,
        "source_complete": False, "complete": False, "contract_complete": False,
        "goods": [], "source_observations": [], "reasons": {}, "cleanup_warnings": [],
        "units": {field.name: field.unit for field in POP_NEED_GOODS if field.unit is not None},
        "sources": {field.name: field.expression for field in POP_NEED_GOODS},
        "snapshot": "fresh source per callback/page; native row owner does not freeze arrays/scalars/Pop/GoodsDB",
        "remaining": ("typed Pop-to-PopNeed and native need-category key",
            "structured native per-good cost and substitution multiplier",
            "consumption cycle/time unit and all-field coherence/runtime acceptance")}
    try:
        binding = data.ObjectBinding.pop(await ctx.type_id("Pop"), pop_id)
        iterator = _projection_source_pages(ctx, "economy.pop_needs",
            tuple(field.expression for field in POP_NEED_GOODS), ((binding,),), (source_name,))
        async for _, source, page, cells in iterator:
            _pop_needs_page(answer, source, page, cells)
            answer["cleanup_warnings"].extend(diagnostic for diagnostic in ctx.diagnostics.get("operations", ())
                if isinstance(diagnostic, dict) and diagnostic.get("result") is page
                and "cleanup_error" in diagnostic)
    except BaseException as error:
        partial = getattr(getattr(error, "query", None), "partial_result", None)
        if isinstance(partial, data.ProjectionPage):
            _pop_needs_page(answer, partial, partial, partial.cells)
        error.pop_needs_observations = answer
        raise  # Same original query and copied prefix; no capability veto/replay.
    count = answer["source_count"]
    positions = [row["source_index"] for row in answer["goods"]]
    coverage = (count is not None and len(positions) == count
        and len(set(positions)) == count and all(0 <= index < count for index in positions))
    answer["complete"] = bool(answer["source_complete"] and coverage
        and answer["world_consistent"] and not answer["reasons"]
        and all(row["complete"] for row in answer["goods"]))
    return answer


POP_CULTURE = (
    Field('culture.native_id', 'Pop.GetCulture.GetID', 'integer'),
    Field('culture.name', 'Pop.GetCulture.GetNameNoFormatting', 'text'),
    Field('culture.is_valid', 'Pop.GetCulture.IsValid', 'boolean'),
    Field('religion.name', 'Pop.GetReligion.GetNameNoFormatting', 'text'),
    Field('religion.is_valid', 'Pop.GetReligion.IsValid', 'boolean'),
    Field('religion.description', 'Pop.GetReligion.GetDesc', 'text'),
    Field('acceptance.value', 'Pop.GetAcceptanceValue', 'integer'),
    Field('acceptance.value_description', 'Pop.GetAcceptanceValueDesc', 'text'),
    Field('acceptance.is_status_increasing', 'Pop.IsAcceptanceStatusIncreasing', 'boolean'),
    Field('acceptance.status_name', 'Pop.GetAcceptanceStatus.GetNameNoFormatting', 'text'),
    Field('acceptance.status_description', 'Pop.GetAcceptanceStatus.GetDesc', 'text'),
    Field('acceptance.status_ordinal', 'Pop.GetAcceptanceStatus.GetOrdinal', 'integer'),
    Field('acceptance.status_lower_bound', 'Pop.GetAcceptanceStatus.GetLowerBound', 'integer'),
    Field('acceptance.status_upper_bound', 'Pop.GetAcceptanceStatus.GetUpperBound', 'integer'),
    Field('acceptance.status_has_higher', 'Pop.GetAcceptanceStatus.HasHigherStatus', 'boolean'),
    Field('acceptance.status_has_lower', 'Pop.GetAcceptanceStatus.HasLowerStatus', 'boolean'),
    Field('acceptance.wage_discrimination_description', 'Pop.GetWageDiscriminationDesc', 'text'),
    Field('assimilation.is_assimilating', 'Pop.IsAssimilating', 'boolean'),
    Field('assimilation.individual_count', 'Pop.GetNumAssimilatingIndividuals', 'integer', "people"),
    Field('assimilation.description', 'Pop.GetPopAssimilationDesc', 'text'),
    Field('assimilation.target_native_culture_id', 'Pop.GetAssimilatingCulture.GetID', 'integer'),
    Field('assimilation.target_culture_name', 'Pop.GetAssimilatingCulture.GetNameNoFormatting', 'text'),
    Field('assimilation.target_culture_is_valid', 'Pop.GetAssimilatingCulture.IsValid', 'boolean'),
    Field('conversion.is_converting', 'Pop.IsConverting', 'boolean'),
    Field('conversion.individual_count', 'Pop.GetNumConvertingIndividuals', 'integer', "people"),
    Field('conversion.description', 'Pop.GetPopConversionDesc', 'text'),
    Field('conversion.target_religion_name', 'Pop.GetConvertingReligion.GetNameNoFormatting', 'text'),
    Field('conversion.target_religion_is_valid', 'Pop.GetConvertingReligion.IsValid', 'boolean'),
    Field('obsessions.culture_description', 'Pop.GetCulture.GetObsessionsDesc', 'text'),
    Field('obsessions.religion_taboos_description', 'Pop.GetReligion.GetTaboosDesc', 'text'),
)


# The existing ordinary compiler consumes the receiver and Pop-country formal
# together. These are not Page fields or host-constructed native argument slots.
POP_ACCEPTANCE = (
    NativeExpression("culture.value", "Pop.GetCulture.GetAcceptanceIn(Pop.GetCountry.Self)", "integer"),
    NativeExpression("culture.description", "Pop.GetCulture.GetAcceptanceDescIn(Pop.GetCountry.Self)", "text"),
    NativeExpression("religion.value", "Pop.GetReligion.GetAcceptanceIn(Pop.GetCountry.Self)", "integer"),
    NativeExpression("religion.description", "Pop.GetReligion.GetAcceptanceDescIn(Pop.GetCountry.Self)", "text"),
    NativeExpression("status.effects_description", "Pop.GetAcceptanceStatus.GetEffectsDescIn(Pop.GetCountry.Self)", "text"),
)


def _pop_acceptance_groups(answer):
    for prefix in ("culture", "religion", "status"):
        answer[prefix] = {field.name.split(".", 1)[1]: answer["values"].get(field.name)
            for field in POP_ACCEPTANCE if field.name.startswith(prefix + ".")}
    answer["argument_source"] = "Pop.GetCountry.Self"
    answer["contract_complete"] = False
    answer["remaining"] = ("effective wage, qualification and legal-right consequences",
        "structured native effects and stable Religion/AcceptanceStatus identities")
    return answer


async def read_pop_acceptance(ctx, pop_id):
    """Copy acceptance/effects using each originating Pop's native Country.

    Native signed integers are not rescaled. Description bytes and actual
    errors remain separate from completeness. No localized effects text is
    parsed into wage, qualification or legal-right amounts.
    """
    from pdx_runtime.v3_game._read import read_pop_expressions
    try:
        answer = await read_pop_expressions(ctx, pop_id, POP_ACCEPTANCE,
            label="economy.pop_acceptance")
    except BaseException as error:
        partial = getattr(error, "pop_observations", None)
        if isinstance(partial, dict):
            _pop_acceptance_groups(partial)
        raise
    return _pop_acceptance_groups(answer)


def _pop_culture_groups(answer):
    for prefix in dict.fromkeys(field.name.split(".", 1)[0] for field in POP_CULTURE):
        answer[prefix] = {field.name.split(".", 1)[1]: answer["values"].get(field.name)
            for field in POP_CULTURE if field.name.startswith(prefix + ".")}
    answer["contract_complete"] = False
    answer["identity_semantics"] = ("Culture IDs are original uint32 observations, not Pop full IDs; "
        "Religion names are display only; AcceptanceStatus ordinal/bounds are signed scalars, not IDs")
    answer["remaining"] = ("country-argument acceptance/effects composition and stable Religion/status identities",
        "structured native assimilation modifiers and conversion target key/time basis",
        "keyed Goods obsessions/taboos and demand effects; actual native runtime acceptance")
    return answer


async def read_pop_culture(ctx, pop_id):
    """Copy native Pop culture/acceptance/assimilation/conversion observations.

    Zero-argument promotions stay in each original expression. The actual Pop
    religion supplies taboos, not its Culture's default religion. No flag/count,
    display name, ordinal, selected Country or wiki rate supplies missing data.
    """
    from pdx_runtime.v3_game._read import read_pop_fields
    try:
        answer = await read_pop_fields(ctx, pop_id, POP_CULTURE, label="economy.pop_culture")
    except BaseException as error:
        partial = getattr(error, "pop_observations", None)
        if isinstance(partial, dict):
            _pop_culture_groups(partial)
        raise
    return _pop_culture_groups(answer)
