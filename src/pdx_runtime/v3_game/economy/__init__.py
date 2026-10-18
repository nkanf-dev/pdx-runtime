"""Budget, market goods and ordinary national economic controls."""
from __future__ import annotations
from pdx_runtime.v3_wire import resolve_v3_export
import struct
import inspect
from pdx_runtime import v3_data as data, v3_objects as objects, v3_commands as commands
from pdx_runtime import native_call, v3_access, v3_player_control as player
from pdx_runtime.v3_game._values import fixed_decimal
from pdx_runtime.v3_game.country import CountryAPI, _Read, _plain_name
from pdx_runtime.v3_game._read import read_fields, copy_fields, finish_read
from pdx_runtime.v3_game.economy.observations import MINTING, INVESTMENT_POOL, BANKRUPTCY, BUDGET_OVERVIEW, BUDGET_CATEGORIES, BUDGET_CATEGORY_EXPRESSIONS, BUDGET_EXPLANATIONS
from pdx_runtime.v3_game._run import rejected
from pdx_runtime.v3_game import _run
from . import market_relation, market_goods

_LEVELS = {"very_low":"VeryLow", "low":"Low", "medium":"Medium", "high":"High", "very_high":"VeryHigh"}
# Original CBudgetLevelTrigger<0> getter252A950 and native tax enum converters:
# 21671 -> 0 very_low; 21600 -> 1 low; 21601 -> 2 medium;
# 21602 -> 3 high; 21672 -> 4 very_high. 5 is native invalid/fallback.
_TAX_LEVEL_BY_ENUM = {0:"very_low", 1:"low", 2:"medium", 3:"high", 4:"very_high"}

_WAGE_GETTERS = {"government": 0x252A8F0, "military": 0x252A920}
_TARIFF_POLICIES = {
    "subventions_max": "SubventionsMax", "subventions_high": "SubventionsHigh",
    "subventions_low": "SubventionsLow", "tariffs_none": "TariffsNone",
    "tariffs_low": "TariffsLow", "tariffs_high": "TariffsHigh", "tariffs_max": "TariffsMax",
}
_TARIFF_DIRECTIONS = {"import": "Import", "export": "Export"}


def _tariff_expression(direction, policy, *, query=False):
    """Keep the native Country/Goods relation and actual named policy."""
    if direction not in _TARIFF_DIRECTIONS or policy not in _TARIFF_POLICIES:
        raise ValueError("select an explicit native import/export policy name")
    return f"Country.{('Has' if query else 'Set')}{_TARIFF_DIRECTIONS[direction]}{_TARIFF_POLICIES[policy]}(Goods.Self)"


def _wage_getter_input(input_address, country_full_id):
    full_id = commands.UInt32(country_full_id).value
    if not 0 <= full_id <= 0xFFFFFFFF:
        raise ValueError("Country full ID does not fit uint32")
    return struct.pack("<QH3HQ", input_address + 8, 5, 0, 0, 0, full_id)


class EconomyAPI:
    def __init__(self, ctx):self._ctx=ctx

    def _retain(self,record):
        # Uncertain native owners must outlive the bounded diagnostics ring and
        # a safe Python domain reload. They stay on the existing shared context.
        retained=getattr(self._ctx,"_economy_retained",None)
        if retained is None:
            retained=[];self._ctx._economy_retained=retained
        retained.append(record)
        self._ctx.diagnostics.append({"retained_economy_operation":len(retained)-1})

    async def _observations(self, fields, label):
        result = await read_fields(self._ctx, fields, label=label)
        return {**result, **result["values"]}

    async def minting(self):
        """Minting prediction and gold reserve limit, ratio and explanations.

        predicted_weekly_income is the native prediction, not current income.
        """
        return await self._ctx.perform("economy.minting",
            lambda: self._observations(MINTING, "economy.minting"))

    async def investment_pool(self):
        """Investment pool balance, flows and native autonomous-investment text.

        Contributions, construction spending, next investment and blockers keep
        their original descriptions. Text is not parsed into guessed building
        IDs or amounts. Private construction goods expenses are a separate value,
        not a replacement for the pool's construction-spending description.
        """
        return await self._ctx.perform("economy.investment_pool",
            lambda: self._observations(INVESTMENT_POOL, "economy.investment_pool"))

    async def state_conditions(self, state_id):
        """Read native State conditions, migration and demographic observations.

        state_id is the original full uint32 State ID, including generation bits.
        Native quantities and explanations are grouped by topic. Missing fields,
        native errors and mixed-world observations stay visible beside values.
        """
        from pdx_runtime.v3_game.economy.states import read_state_conditions
        return await self._ctx.perform("economy.state_conditions",
            lambda: read_state_conditions(self._ctx,state_id))

    async def population_metrics(self):
        """Read native national/incorporated literacy and total/strata SoL.

        Current and expected SoL remain separate original observations. Ratios
        keep their native scale; original factors and explanations remain text.
        """
        from pdx_runtime.v3_game.economy.observations import POPULATION_METRICS
        return await self._ctx.perform("economy.population_metrics",
            lambda: self._observations(POPULATION_METRICS, "economy.population_metrics"))

    async def state_population_metrics(self, state_id):
        """Read an explicit State's literacy, labor pool and current/expected SoL.

        The below-expected share is the original State getter. It is not derived
        from national averages, strata values or a fabricated population scan.
        """
        from pdx_runtime.v3_game.economy.states import read_state_population_metrics
        return await self._ctx.perform("economy.state_population_metrics",
            lambda: read_state_population_metrics(self._ctx, state_id))

    async def pop_acceptance(self, pop_id):
        """Read native acceptance and effects for this Pop's own Country.

        Culture/Religion acceptance integers and original descriptions use
        inline native Pop.GetCountry.Self arguments. Five callbacks are separate
        observations. Effects text is not a structured wage or rights amount.
        """
        from pdx_runtime.v3_game.economy.observations import read_pop_acceptance
        return await self._ctx.perform("economy.pop_acceptance",
            lambda: read_pop_acceptance(self._ctx, pop_id))

    async def pop_culture(self, pop_id):
        """Read a Pop's native culture, acceptance and cultural change values.

        Preserves signed counts, false flags, actual Culture.GetID values and
        original descriptions. Religion names are not IDs. Status ordinals are
        not keys. Native counts have no invented weekly/monthly time basis.
        """
        from pdx_runtime.v3_game.economy.observations import read_pop_culture
        return await self._ctx.perform("economy.pop_culture",
            lambda: read_pop_culture(self._ctx, pop_id))

    async def pop_needs(self, pop_id, *, top=False):
        """Read a Pop's original goods quantities, budget shares and native text.

        The default is the full original goods-consumption source. top=True uses
        the game's separate native top source, not host sorting/truncation. Goods
        keys, display names and descriptions stay distinct. Native cost/category
        and substitution gaps are not filled with derived or localized guesses.
        """
        from pdx_runtime.v3_game.economy.observations import read_pop_needs
        return await self._ctx.perform("economy.pop_needs",
            lambda: read_pop_needs(self._ctx, pop_id, top=top))

    async def pop_details(self, pop_id):
        """Read one full native Pop ID's economic and society observations.

        State/Country relations use the original explicit-ID PopDetails record.
        Literacy, job satisfaction, SoL, income, needs and food security retain
        their native types, descriptions, errors and separate-observation scope.
        """
        from pdx_runtime.v3_game.economy.observations import read_pop_economy
        return await self._ctx.perform("economy.pop_details",
            lambda: read_pop_economy(self._ctx, pop_id))

    async def state_world_market_access(self, state_id):
        """Read a full native State ID's hub flag, world access and potential.

        Native values, explanations and errors remain separate observations.
        This explicit read does not enlarge the default state_conditions page.
        """
        from pdx_runtime.v3_game.economy.states import read_state_world_market_access
        return await self._ctx.perform("economy.state_world_market_access",
            lambda: read_state_world_market_access(self._ctx, state_id))

    async def state_food_security(self, state_id):
        """Read a State's starvation totals/ratios and native famine details.

        These are native State aggregates. They do not stand in for individual
        Pop food-security values or invent identities of affected Pops.
        """
        from pdx_runtime.v3_game.economy.states import read_state_food_security
        return await self._ctx.perform("economy.state_food_security",
            lambda: read_state_food_security(self._ctx, state_id))

    async def state_harvest(self, state_id):
        """Read native harvest type/display, multiplier, days and effect text.

        Affected-State and Country values come from each original condition.
        Runtime days, rendered label/range, instance effects and type effects are
        distinct. No type key, instance ID or modifier vector is synthesized.
        """
        from pdx_runtime.v3_game.economy.states import read_state_harvest
        return await self._ctx.perform("economy.state_harvest",
            lambda: read_state_harvest(self._ctx, state_id))

    async def state_migration(self, state_id):
        """Read a State's native migration pull and mass-migration context.

        Original numeric getter values, country tag, display names and end-date
        text remain distinct. This does not enumerate all targets or reinterpret
        native ID values as independently proved full-generation identities.
        """
        from pdx_runtime.v3_game.economy.states import read_state_migration
        return await self._ctx.perform("economy.state_migration",
            lambda: read_state_migration(self._ctx, state_id))

    async def company_types(self):
        """Read all native CompanyType keys, including hidden definitions.

        The full snapshot uses the canonical source's explicit unlimited mode.
        Per-position errors and copied prefixes remain visible. These keys are
        not retained native objects for a later create command.
        """
        from pdx_runtime.v3_game.economy.company_observations import read_company_types
        return await self._ctx.perform("economy.company_types",
            lambda: read_company_types(self._ctx))

    async def companies(self, *, max_pages=None):
        """Read owned companies with real full IDs and original native values.

        By default, read the complete native list. An explicit max_pages budget
        uses 64-row pages and reports truncation and the next source index. IDs
        and projected fields share each retained Company owner; source ordinals
        are not identities. Individual field errors remain beside copied values.
        """
        from pdx_runtime.v3_game.economy.company_observations import list_companies
        return await self._ctx.perform("economy.company.list",
            lambda: list_companies(self._ctx,max_pages=max_pages))

    async def establish_company(self, type_key):
        """Establish a company from its original game CompanyType key.

        The game validates the request. Native acceptance and fresh membership
        are reported separately. Definition discovery uses the country's original
        CompanyType sources; an existing company is not reported as a submitted
        success without asking the native command.
        """
        from pdx_runtime.v3_game.economy.companies import change_company
        return await self._ctx.perform("economy.establish_company",
            lambda: change_company(self._ctx,type_key,"create",
                submit_generated=self._submit_generated))

    async def rename_company(self, company_id, new_name):
        """Rename one full native Company ID and independently read its name.

        The original native validator and submission decide legality. Accepted
        receipt, original nonformatted name, readback errors and cleanup remain
        separate. This does not require a successful full-list preflight.
        """
        from pdx_runtime.v3_game.economy.companies import rename_company
        return await self._ctx.perform("economy.rename_company",
            lambda: rename_company(self._ctx, company_id, new_name, run_factory=_run.Run))

    async def disband_company(self, type_key):
        """Disband an owned company identified by its original CompanyType key.

        The native removal command decides legality. Returned membership is an
        independent observation; copied readback errors do not erase acceptance.
        """
        from pdx_runtime.v3_game.economy.companies import change_company
        return await self._ctx.perform("economy.disband_company",
            lambda: change_company(self._ctx,type_key,"remove",
                submit_generated=self._submit_generated))

    async def bankruptcy_preview(self):
        """Read consequences and validate the original bankruptcy command once.

        This explicit preview does not submit. Principal and its self-held subset,
        the native weekly-balance prediction, radicalization and description stay
        separate observations. Validation does not promise later acceptance.
        """
        return await self._ctx.perform("economy.bankruptcy_preview", self._bankruptcy_preview)

    async def _bankruptcy_preview(self):
        answer = await self._finance_observations(BANKRUPTCY, "economy.bankruptcy_preview")
        eligibility = await self._national_command("DeclareBankruptcy", submit=False,
            requested={"action": "declare_bankruptcy"})
        answer["eligibility"] = eligibility
        answer["allowed"] = eligibility.get("allowed")
        answer["eligibility_reason"] = eligibility.get("reason")
        answer["eligibility_observed"] = (type(answer["allowed"]) is bool and
            eligibility.get("native_validation_error",0) in (0,122))
        if not answer["eligibility_observed"]:
            answer["complete"] = False
            answer["reasons"]["eligibility"] = eligibility.get("reason") or "native eligibility was not observed"
        if eligibility.get("country_id") != answer["country_id"]:
            answer["complete"] = False
            answer["reasons"]["eligibility"] = "selected country changed between observations"
        return answer

    async def declare_bankruptcy(self):
        """Submit the game's bankruptcy decision and return its native result.

        The original command is generated, validated and submitted once. Native
        acceptance is reported separately from later financial or political effects.
        """
        return await self._ctx.perform("economy.declare_bankruptcy",
            lambda: self._national_command("DeclareBankruptcy",
                requested={"action": "declare_bankruptcy"}))

    @staticmethod
    def _finance_result(observed, fields):
        """Group copied native fields without changing their values or receipts."""
        answer = {key: value for key, value in observed.items() if key != "values"}
        answer["sources"] = dict(observed["sources"])
        answer["units"] = dict(observed["units"])
        answer["reasons"] = dict(observed["reasons"])
        answer["predictions"] = {}
        for field in fields:
            answer["sources"][field.name] = field.expression
            if field.unit is not None:
                answer["units"][field.name] = field.unit
            if field.name not in observed["observations"]:
                answer["reasons"].setdefault(field.name, "native field not yet observed")
            parts = field.name.split(".")
            group = answer
            for part in parts[:-1]:
                group = group.setdefault(part, {})
            group[parts[-1]] = observed["values"].get(field.name)
            getter = field.expression.rsplit(".", 1)[-1]
            answer["predictions"][field.name] = getter.startswith("Predict") or getter.startswith("GetPredicted")
        return answer

    async def _finance_observations(self, fields, label, *, expressions=()):
        """Use the shared scalar reader and original ordinary-expression owner.

        Modifier arguments stay in their original native expressions. Their
        reads reuse the Country copied by the scalar reader; no second actor
        selection, host category arithmetic or native command is involved.
        """
        all_fields = tuple(fields) + tuple(expressions)
        observed = None
        try:
            observed = await read_fields(self._ctx, fields, label=label)
            if expressions:
                for field in expressions:
                    observed["values"][field.name] = None
                    observed["sources"][field.name] = field.expression
                    if field.unit is not None:
                        observed["units"][field.name] = field.unit
                    observed["reasons"][field.name] = "native expression not yet observed"
                binding = data.ObjectBinding.country(await self._ctx.type_id("Country"), observed["country_id"])
                read = _Read(CountryAPI(self._ctx))
                try:
                    await copy_fields(self._ctx, read, observed, expressions, (binding,), label=label)
                finally:
                    await finish_read(self._ctx, read, observed, label=label)
                observed["complete"] = observed["world_consistent"] and all(
                    observed["observations"].get(field.name, {}).get("complete", False)
                    for field in all_fields)
        except BaseException as error:
            if observed is None:
                observed = getattr(error, "country_observations", None)
            if isinstance(observed, dict):
                observed["complete"] = observed["world_consistent"] and all(
                    observed["observations"].get(field.name, {}).get("complete", False)
                    for field in all_fields)
                error.country_observations = observed
                error.finance_observations = self._finance_result(observed, all_fields)
            raise
        return self._finance_result(observed, all_fields)

    async def budget(self, *, details=False, explanations=False):
        """Read the native budget headline, funding, credit and interest context.

        balance.predicted_weekly is the UI balance. balance.native_weekly and
        income.native_weekly preserve GetWeeklyBalance/GetWeeklyIncome without
        claiming a historical accounting period. Credit principal is total
        loans; self_held_debt is only its government-held subset. Expense values
        keep their native signs, not the UI's added minus sign.

        details adds native income/expense categories, government/private
        construction and taxation diagnostics. Interest payment is already at
        credit.interest.weekly_payment; categories are not summed on the host.
        explanations separately adds the native money, income, expense, credit
        and interest text. Defaults read no long tooltip or State/Building list.
        Units, sources, predictions, native observations, errors and partial
        pages use dotted field paths. On transport failure the original error
        keeps country_observations and the grouped finance_observations prefix.
        """
        fields = BUDGET_OVERVIEW
        if details:
            fields += BUDGET_CATEGORIES
        if explanations:
            fields += BUDGET_EXPLANATIONS
        return await self._ctx.perform("economy.budget", lambda: self._finance_observations(
            fields, "economy.budget", expressions=BUDGET_CATEGORY_EXPRESSIONS if details else ()))

    async def market(self):
        """The selected country's actual market and its current goods."""
        return await self._ctx.perform("economy.market",self._market)

    async def goods(self, key=None):
        """All actual market goods, or one original game key such as 'iron'."""
        async def work():
            market=await self._market();rows=market["goods"]
            if key is None:return rows
            found=[r for r in rows if r["key"]==key or (type(key) is int and r["id"]==key)]
            if not found:return {"id":None,"key":key,"available":False,
                "reason":"This good was not found in the current native market"}
            return found[0]
        return await self._ctx.perform("economy.goods",work)

    async def goods_definition(self, key):
        """Read a Goods definition by its exact native key in the current market.

        Fields keep their original values, rich text and native errors. Absence
        applies to this country's observed market source, not a global catalog.
        A provider position is never reported as a Goods definition ID.
        """
        from pdx_runtime.v3_game.economy.goods import read_goods_definition
        return await self._ctx.perform("economy.goods_definition",
            lambda: read_goods_definition(self._ctx, key))

    async def _market(self):
        ctx=self._ctx;await ctx.refresh();country=await ctx.country_id()
        relation_record={}
        relation=await market_relation.observe_country_market(ctx.native,ctx.process,
            country_type_id=await ctx.type_id("Country"),country_id=country,world=ctx.world,
            adapter=ctx.adapters["data"],await_result=lambda label,q:ctx.wait(q),record=relation_record)
        ctx.diagnostics.append({"operation":"economy.market_relation","record":relation_record})
        if relation_record.get("state",{}).get("owned_buffers"):
            self._retain({"market_relation":relation_record})
        market_id=relation.get("market_id")
        if market_id is None:return {"id":None,"country_id":country,"name":None,"goods":[],
            "reason":"The country's original market relation is currently unavailable"}
        snapshot=await market_goods.read_market_goods(ctx.native,ctx.process,
            market_id=market_id,world=ctx.world,adapter=ctx.adapters["objects"],
            await_result=lambda label,q:ctx.wait(q))
        ctx.diagnostics.append({"operation":"economy.market_goods","state":snapshot})
        labels,name=await self._market_labels(market_id)
        goods=[]
        for row in snapshot["rows"]:
            label=labels.get(row["goods_index"],{})
            item={"id":row["goods_index"],"key":label.get("key"),"name":label.get("name"),
                "price":None,"demand":None,"supply":None,"imports":None,"exports":None,
                "shortage":None,"units":{"price":"pounds/unit","orders":"goods orders"},"reasons":{}}
            for target,source in (("price","price"),("demand","buy_orders"),("supply","sell_orders"),
                                  ("imports","imports"),("exports","exports")):
                field=row["fields"][source]
                if field["status"]=="ok":item[target]=fixed_decimal(field["raw_signed"])
                else:item["reasons"][target]="Native field error "+str(field["native_error"])
            if item["supply"] is not None and item["demand"] is not None:
                item["shortage"]=fixed_decimal(row["fields"]["buy_orders"]["raw_signed"]-
                    row["fields"]["sell_orders"]["raw_signed"])
            if not label:item["reasons"]["name"]="No original Goods label was available for this definition"
            goods.append(item)
        return {"id":market_id,"country_id":country,"name":name,"goods":goods,
            "goods_count":len(goods),"complete":snapshot["complete"],
            "shortage_definition":"buy orders minus sell orders; not shortage percentage",
            "snapshot":"separate native observations"}

    async def subsidy(self, building_id):
        """Read current subsidy state, native legal choices and observed cost.

        Observed spending is not a forecast of spending after enabling support.
        A missing native estimate is returned as unknown with its reason.
        """
        from . import building_subsidies as helper
        return await self._ctx.perform("economy.subsidy",
            lambda:helper.read_subsidy(self._ctx,building_id))

    async def set_subsidy(self, building_id, enabled=True):
        """Request the native on/off state once and independently read it back.

        The native off command can also clear the country's subsidy override
        for this building type.
        """
        from . import building_subsidies as helper
        return await self._ctx.perform("economy.set_subsidy",
            lambda:helper.set_subsidy(self._ctx,building_id,enabled=enabled))

    async def production_methods(self, building_id, *, details=True):
        """Read the building's native production choices and current selection.

        Candidate membership is distinct from current native legal constraints.
        Descriptions report game observations; they do not promise better output.
        """
        from . import production_methods as helper
        return await self._ctx.perform("economy.production_methods",
            lambda:helper.read_production_methods(self._ctx,building_id,details=details))

    async def set_production_method(self, building_id, method):
        """Select a native production method for one building and read it back.

        ``method`` is a game key or an observed candidate row. Acceptance and
        the later observed selection are separate from employment or output.
        """
        from . import production_methods as helper
        return await self._ctx.perform("economy.set_production_method",
            lambda:helper.set_production_method(self._ctx,building_id,method))

    async def building_status(self, building_id, *, details=True):
        """Read staffing, hiring, operating state and native production options.

        Descriptions are observations from the game, not an inferred cause of
        understaffing. This method never changes hiring, subsidies or methods.
        """
        from . import building_status as helper
        return await self._ctx.perform("economy.building_status",
            lambda:helper.read_building_status(self._ctx,building_id,details=details))

    async def building_io(self,building_id):
        """Actual input/output Goods and quantities for one real building."""
        if type(building_id) is not int or not 0<=building_id<0xffffffff:
            raise ValueError("building_id must identify a real building")
        return await self._ctx.perform("economy.building_io",lambda:self._building_io(building_id))

    def _building_io_submit(self,helper):
        submit=getattr(self._ctx,"submit",None)
        return ({"submit_query":submit} if callable(submit)
            and "submit_query" in inspect.signature(helper.read_all_building_io).parameters else {})

    @staticmethod
    def _building_io_rows(state):
        rows=[]
        for observed in state["rows"]:
            fields=observed["fields"];row={"key":None,"name":None,"consumption":None,
                "production":None,"consumption_value":None,"production_value":None,"reasons":{}}
            for native_name,public_name in (("GetKey","key"),("GetName","name"),
                    ("GetConsumption","consumption"),("GetProduction","production"),
                    ("GetConsumptionValue","consumption_value"),("GetProductionValue","production_value")):
                field=fields.get(native_name,{})
                if field.get("status")!="ok":
                    row["reasons"][public_name]=field.get("reason","Native value unavailable")
                    continue
                value=field.get("value")
                row[public_name]=(_plain_name(value) if public_name=="name" else value
                    if public_name=="key" else fixed_decimal(value))
            rows.append(row)
        return rows

    def _building_io_error(self,state):
        pending=state.get("pending") or {}
        cause=pending.get("submission_exception") or pending.get("completion_exception")
        detail=str(cause) if cause is not None else ("Unexpected native receipt" if "unexpected_receipt" in pending
            else "The original native observation is not complete")
        error=RuntimeError("Building "+state["source"]+" Goods read: "+detail)
        error.original_state=state;error.original_query=pending.get("query")
        retained=getattr(self._ctx,"_economy_retained",())
        if not any(isinstance(item,dict) and item.get("building_io") is state for item in retained):
            self._retain({"building_io":state,"cause":cause})
        return error,cause

    async def _finish_building_io_state(self,helper,state):
        ctx=self._ctx
        try:
            if state["pending"] is not None or not state.get("bulk_complete"):
                state=await helper.continue_all_building_io(state,
                    await_result=lambda label,q:ctx.wait(q),**self._building_io_submit(helper))
            if state["pending"] is not None or not state.get("bulk_complete"):
                error,cause=self._building_io_error(state)
                raise error from cause
            if state.get("bulk_initial_count") is None:
                return {"rows":None,"reason":"The native Goods provider is currently unavailable","changed":False}
            return {"rows":self._building_io_rows(state),"reason":None,
                "changed":bool(state.get("bulk_diagnostics"))}
        finally:
            if state["pending"] is None and state["phase"]=="done":
                if "release_order" not in state:
                    await helper.start_release_building_io(state,await_result=lambda label,q:ctx.wait(q))
                if state.get("owned_values") or state.get("release_errors"):
                    self._retain({"building_io":state})
                else:
                    retained=getattr(ctx,"_economy_retained",None)
                    if retained is not None:
                        retained[:]=[item for item in retained if not (isinstance(item,dict)
                            and item.get("building_io") is state)]

    @staticmethod
    def _merge_building_io(result,state,observed):
        target={"consumed":"inputs","produced":"outputs"}[state["source"]]
        result[target]=observed["rows"]
        if observed["reason"]:result["reasons"][target]=observed["reason"]
        if observed["changed"]:result["reasons"][target+"_changes"]="The Goods collection changed during observation"
        return result

    async def _resume_building_io_state(self,state,partial_result):
        """Continue one retained source internally, preserving the other copied source."""
        ctx=self._ctx
        async def work():
            if state["client"] is not ctx.native or state["process"] is not ctx.process:
                raise ValueError("The retained building observation belongs to a different runtime")
            if partial_result["building_id"]!=state["building_full_id"]:
                raise ValueError("The retained building observation has a different building")
            from . import building_io as helper
            observed=await self._finish_building_io_state(helper,state)
            return self._merge_building_io(partial_result,state,observed)
        return await ctx.perform("economy.building_io.resume",work)

    async def _building_io(self,building_id,expected_world=None):
        ctx=self._ctx;await ctx.refresh()
        result={"building_id":building_id,"inputs":None,"outputs":None,
            "units":{"quantity":"goods orders","value":"pounds"},"reasons":{},
            "snapshot":"separate native observations"}
        if expected_world is not None and ctx.world!=expected_world:
            result["reasons"]["world"]="The game world changed during the industry scan"
            return result
        valid=await ctx.query("economy.building",lambda:objects.read_ids(ctx.native,objects.Domain.BUILDING,
            (building_id,),adapter=ctx.adapters["objects"],process=ctx.process))
        if valid.error or len(valid.records)!=1 or valid.records[0].error:
            result["reasons"]["building"]="This building is no longer available"
            return result
        from . import building_io as helper
        for source,target in (("consumed","inputs"),("produced","outputs")):
            options=self._building_io_submit(helper)
            if "metadata_cache" in inspect.signature(helper.read_all_building_io).parameters:
                provider=getattr(ctx,"shared_cache",None)
                options["metadata_cache"]=provider("building_io") if callable(provider) else None
            state=await helper.read_all_building_io(ctx.native,building_full_id=building_id,
                source=source,process=ctx.process,adapter=ctx.adapters["data"],
                page_capacity=128,await_result=lambda label,q:ctx.wait(q),**options)
            state.setdefault("source",source)
            ctx.diagnostics.append({"operation":"economy.building_io","state":state})
            observed=await self._finish_building_io_state(helper,state)
            self._merge_building_io(result,state,observed)
        return result

    async def industries(self,state=None,*,include_io=True,on_row=None):
        """Country-wide industry panorama; optionally emit each real copied row.

        The default country summary/building list stays lightweight. This explicit
        panorama scans all native country buildings. on_row(row) can be a normal
        or async callback, providing useful rows before the whole scan finishes.
        """
        async def work():
            ctx=self._ctx;await ctx.refresh();world=ctx.world
            buildings=await ctx.domain("country").buildings(state=state)
            # Nested high-level work remains the original owned operation.
            while hasattr(buildings,"resume"):
                buildings=await buildings.resume()
            selected=state.get("id") if isinstance(state,dict) else state
            rows=[]
            self._industry_partial={"rows":rows,"complete":False,"country_id":await ctx.country_id()}
            for building in buildings:
                if selected is not None:
                    if isinstance(selected,str):
                        if (building.get("state_name") or "").casefold()!=selected.casefold():continue
                    elif building.get("state_id")!=selected:continue
                row=dict(building)
                if include_io:
                    row["io"]=await self._building_io(building["id"],world)
                    if "world" in row["io"]["reasons"]:
                        self._industry_partial["reason"]=row["io"]["reasons"]["world"]
                        break
                rows.append(row)
                if on_row is not None:
                    emitted=on_row(row)
                    if inspect.isawaitable(emitted):await emitted
            else:self._industry_partial["complete"]=True
            return rows
        return await self._ctx.perform("economy.industries",work)

    @property
    def industry_progress(self):
        """Copied rows produced by a still-running explicit panorama scan."""
        progress=getattr(self,"_industry_partial",{"rows":[],"complete":False})
        return {**progress,"rows":list(progress["rows"])}

    async def _release(self,handles):
        ctx=self._ctx;released=set();warnings=[]
        try:
            for handle in reversed(tuple(dict.fromkeys(handles))):
                result=await ctx.query("economy.release",lambda h=handle:data.release(ctx.native,h,
                    adapter=ctx.adapters["data"],process=ctx.process))
                if result.error or result.value_handle:
                    self._retain({"release_error":result,"handle":handle})
                    warnings.append("native value cleanup error "+str(result.error)+
                        "; retained handle "+str(handle))
                else:released.add(handle)
        finally:
            # An interrupted later release must not retain already-freed handles.
            handles[:]=[handle for handle in handles if handle not in released]
        return warnings

    async def _evaluate(self,expression,bindings,handles,capacity=4096):
        ctx=self._ctx
        result=await ctx.query(expression,lambda:data.evaluate(ctx.native,expression,bindings,
            output_bytes=capacity,adapter=ctx.adapters["data"],process=ctx.process))
        if result.value_handle:handles.append(result.value_handle)
        return result

    @staticmethod
    def _text(result):
        if (result.error or result.kind!=data.Kind.STRING or result.snapshot_bytes!=result.total
                or len(result.snapshot)!=result.snapshot_bytes):return None
        return result.snapshot.decode("utf-8","replace")

    async def _goods_index(self,wrapper):
        """Owned DataGoods -> fresh definition index; outputs live inside query."""
        ctx=self._ctx
        read=resolve_v3_export(ctx.process, ctx.adapters.get("runtime",ctx.adapters["data"]),"PdxRuntimeRead")
        callback=resolve_v3_export(ctx.process, ctx.adapters["call"],"PdxNativeCallBatchInterface")
        calls=((read,(wrapper,0,24,0)),(read,(0,0,8,0)),(read,(0,0,24,0)))
        output_offset=32+3*56+3*32
        def prepare(address):
            raw=bytearray(native_call.encode_batch(calls,request_address=address))+bytes(96)
            args=[struct.unpack_from("<Q",raw,32+i*56+24)[0] for i in range(3)]
            output=address+output_offset
            struct.pack_into("<4Q",raw,args[0]-address,wrapper,output,24,output+64)
            struct.pack_into("<4Q",raw,args[1]-address,output+16,args[2],8,output+68)
            struct.pack_into("<4Q",raw,args[2]-address,0,output+24,24,output+72)
            return bytes(raw)
        result=await ctx.query("economy.goods_definition",lambda:v3_access.submit_callback(ctx.native,
            callback,native_call.encode_batch(calls)+bytes(96),mode=1,flag=0,dispatch="interface",process=ctx.process,
            adapter=ctx.adapters["access"],call_adapter=ctx.adapters["call"],prepare_payload=prepare,
            decode_payload=native_call.NativeCallBatchResult.from_bytes))
        raw=result.payload[output_offset:output_offset+96];batch=result.callee_result
        if (result.error or not result.entered or not result.left or batch is None
            or batch.error or batch.completed!=3 or len(raw)!=96
            or any(c.error or c.rax&0xffffffff for c in batch.calls)
            or struct.unpack_from("<3I",raw,64)!=(24,8,24)):return None
        index=struct.unpack_from("<I",raw,40)[0]
        return None if index==0xffffffff else index

    async def _has_name_getter(self,receiver,name):
        ctx=self._ctx;cache=getattr(self,"_name_metadata",None)
        if cache is None or cache[0]!=ctx.world:
            self._name_metadata=(ctx.world,{})
        tables=self._name_metadata[1]
        if receiver not in tables:
            rows=[];cursor=0;type_id=await ctx.type_id(receiver)
            while True:
                page=await ctx.query("economy.name_metadata",lambda:data.enumerate_functions(ctx.native,
                    type_id,start=cursor,capacity=128,adapter=ctx.adapters["data"],process=ctx.process))
                if page.error:break
                rows.extend(page.rows)
                if page.next<=cursor or page.next>=page.total:break
                cursor=page.next
            tables[receiver]=rows
        rows=[row for row in tables[receiver] if row.name==name and row.argument_count==0]
        return bool(len(rows)==1 and not any(rows[0].callbacks[2:]) and
                    bool(rows[0].callbacks[0])!=bool(rows[0].callbacks[1]))

    def _visible_name(self,receiver,key,rich):
        if rich is None:return None
        plain=_plain_name(rich)
        self._ctx.diagnostics.setdefault("economy_names",{})[receiver+":"+str(key)]={
            "world":self._ctx.world,"name":plain,"rich_name":rich}
        return plain

    async def _market_labels(self,market_id, *, keep_key=None):
        ctx=self._ctx;handles=[];labels={};market_name=None;kept=None;uncertain=False
        market_binding=data.ObjectBinding.market(await ctx.type_id("Market"),market_id)
        goods_type=await ctx.type_id("Goods")
        try:
            goods_name_getter=None
            if keep_key is None:
                market_getter="GetNameNoFormatting" if await self._has_name_getter("Market","GetNameNoFormatting") else "GetName"
                named=await self._evaluate("Market."+market_getter,(market_binding,),handles)
                market_name=self._visible_name("Market",market_id,self._text(named))
                goods_name_getter="GetNameNoFormatting" if await self._has_name_getter("Goods","GetNameNoFormatting") else "GetName"
            source=await self._evaluate("Market.GetGoodsSortedByBalance",(market_binding,),handles,24)
            if source.error or source.kind!=data.Kind.LIST:return labels,market_name
            count=await ctx.query("economy.market_goods_count",lambda:data.list_count(ctx.native,source.value_handle,
                adapter=ctx.adapters["data"],process=ctx.process))
            if count.error:return labels,market_name
            for index in range(count.total):
                element=await ctx.query("economy.market_good",lambda i=index:data.list_element(ctx.native,
                    source.value_handle,i,adapter=ctx.adapters["data"],process=ctx.process))
                if element.value_handle:handles.append(element.value_handle)
                if element.error or "CPdxPtr@VCDataGoods" not in element.native_type_name:continue
                binding=data.ObjectBinding(goods_type,element.value_handle,retained=True)
                key=self._text(await self._evaluate("Goods.GetKey",(binding,),handles))
                if keep_key is not None and not key==keep_key:continue
                if goods_name_getter is None:
                    goods_name_getter="GetNameNoFormatting" if await self._has_name_getter("Goods","GetNameNoFormatting") else "GetName"
                named=await self._evaluate("Goods."+goods_name_getter,(binding,),handles)
                name=self._visible_name("Goods",key,self._text(named))
                if keep_key is not None:
                    kept={"key":key,"name":name,"binding":binding,"handles":handles,
                        "goods_handle":element.value_handle,"market_id":market_id}
                    return kept,market_name
                actual_index=await self._goods_index(element.object)
                if actual_index is not None:labels[actual_index]={"key":key,"name":name}
            return labels,market_name
        except BaseException:
            uncertain=True
            self._retain({"operation":"economy.goods","retained_handles":handles})
            raise
        finally:
            if kept is None and not uncertain:await self._release(handles)

    @staticmethod
    def _display_name(value):
        return _plain_name(value) if value is not None else None

    async def wages(self):
        """Current government and military wage bands, not percentages."""
        async def work():
            ctx=self._ctx;await ctx.refresh();country=await ctx.country_id()
            result={"country_id":country,"government":None,"military":None,"reasons":{}}
            for sector in ("government","military"):
                level,reason=await self._wage_level(country,sector)
                result[sector]=level
                if reason:result["reasons"][sector]=reason
            return result
        return await self._ctx.perform("economy.wages",work)

    async def _wage_level(self,country,sector):
        # Original getters used by the native accepted wage-band composition.
        # Validate the same original Scope16 in this callback, so a fallback
        # Country cannot masquerade as a genuine very_low band.
        ctx=self._ctx
        target=ctx.image_base+_WAGE_GETTERS[sector]
        valid=resolve_v3_export(ctx.process, ctx.adapters["script"],"PdxV3ScriptScopeValid")
        callback=resolve_v3_export(ctx.process, ctx.adapters["call"],"PdxNativeCallBatchInterface")
        calls=((valid,(0,0)),(target,(0,0)))
        offset=32+2*56+4*8
        def prepare(address):
            local=address+offset
            raw=bytearray(native_call.encode_batch(((valid,(local+8,local+24)),
                (target,(0,local))),request_address=address))
            raw+=_wage_getter_input(local,country)+bytes(8)
            return bytes(raw)
        result=await ctx.query("economy."+sector+"_wages",lambda:v3_access.submit_callback(ctx.native,
            callback,native_call.encode_batch(calls)+bytes(32),mode=1,flag=0,dispatch="interface",process=ctx.process,
            adapter=ctx.adapters["access"],call_adapter=ctx.adapters["call"],prepare_payload=prepare,
            decode_payload=native_call.NativeCallBatchResult.from_bytes))
        batch=result.callee_result
        if (result.error or not result.entered or not result.left or batch is None or batch.error
            or batch.completed!=2 or any(c.error for c in batch.calls)
            or batch.calls[0].rax&0xffffffff or len(result.payload)<offset+28
            or struct.unpack_from("<I",result.payload,offset+24)[0]!=1):
            return None,"Native wage-band observation unavailable for this country"
        index=batch.calls[1].rax&0xffffffff
        if index not in range(5):return None,"Native wage band is outside the original five levels"
        return tuple(_LEVELS)[index],None

    async def _controlled(self,country):
        ctx=self._ctx
        actor=await ctx.query("economy.current_player",lambda:player.observe_player(ctx.native,
            adapter=ctx.adapters["console"],process=ctx.process))
        return not (actor.error or actor.controlled_country_error or actor.observer_enabled
            or actor.world!=ctx.world or actor.controlled_country_id!=country)

    async def tax_level(self):
        """Read the current native national tax band without generating commands."""
        async def work():
            ctx=self._ctx;await ctx.refresh();country=await ctx.country_id()
            level,reason=await self._tax_level(country)
            return {"country_id":country,"level":level,"reason":reason}
        return await self._ctx.perform("economy.tax_level",work)

    async def _tax_level(self,country):
        # The real script budget-level getter reads the Country Scope's full ID,
        # not a DataFactory tax description or the setter's validation result.
        # Validate that original Scope in the SAME callback before consuming the
        # copied enum, so a missing Country's fallback cannot become very_low.
        ctx=self._ctx
        target=ctx.image_base+0x252A950
        valid=resolve_v3_export(ctx.process, ctx.adapters["script"],"PdxV3ScriptScopeValid")
        callback=resolve_v3_export(ctx.process, ctx.adapters["call"],"PdxNativeCallBatchInterface")
        calls=((valid,(0,0)),(target,(0,0)))
        offset=32+2*56+4*8
        def prepare(address):
            local=address+offset
            raw=bytearray(native_call.encode_batch(((valid,(local+8,local+24)),
                (target,(0,local))),request_address=address))
            raw+=struct.pack("<QH3HQ",local+8,5,0,0,0,country)+bytes(8)
            return bytes(raw)
        result=await ctx.query("economy.tax_level",lambda:v3_access.submit_callback(ctx.native,
            callback,native_call.encode_batch(calls)+bytes(32),mode=1,flag=0,dispatch="interface",process=ctx.process,
            adapter=ctx.adapters["access"],call_adapter=ctx.adapters["call"],prepare_payload=prepare,
            decode_payload=native_call.NativeCallBatchResult.from_bytes))
        batch=result.callee_result
        if result.error or not result.entered or not result.left:
            return None,"Native tax-band access did not complete"
        if (batch is None or batch.error or batch.count!=2 or batch.completed!=2
            or len(batch.calls)!=2 or any(c.error for c in batch.calls)
            or len(result.payload)<offset+28):
            return None,"Native tax-band callback results are incomplete"
        if batch.calls[0].rax&0xffffffff:
            return None,"Native Country scope validation returned an error"
        if struct.unpack_from("<I",result.payload,offset+24)[0]!=1:
            return None,"Selected country is not a valid native Country scope"
        index=batch.calls[1].rax&0xffffffff
        if index not in _TAX_LEVEL_BY_ENUM:
            return None,"Native tax band is invalid/fallback (5)" if index==5 else "Unknown native tax band "+str(index)
        return _TAX_LEVEL_BY_ENUM[index],None

    async def tax_options(self):
        """Native tax-band descriptions, legal constraints, and actual current band."""
        async def work():
            ctx=self._ctx;await ctx.refresh();country=await ctx.country_id();rows=[]
            if not await self._controlled(country):return []
            current,current_reason=await self._tax_level(country)
            binding=data.ObjectBinding.country(await ctx.type_id("Country"),country)
            for level,suffix in _LEVELS.items():
                handles=[];known=False
                try:
                    description=await self._evaluate("Country.GetTaxLevelDesc"+suffix,(binding,),handles)
                    generated=await self._evaluate("Country.SetTaxLevel"+suffix,(binding,),handles)
                    constraint=await self._submit_generated(generated,handles,{"level":level},submit=False)
                    known=not constraint.pop("_retain_inputs",False)
                    rows.append({"level":level,"description":_plain_name(self._text(description)) if self._text(description) is not None else None,
                        "allowed":constraint.get("allowed"),"reason":constraint.get("reason"),
                        "current":level==current if current is not None else None,
                        "current_reason":current_reason})
                finally:
                    if known:await self._release(handles)
                    elif handles:self._retain({"retained_tax_option_values":handles})
            return rows
        return await self._ctx.perform("economy.tax_options",work)

    @staticmethod
    def _tariff_policy_word(policy):
        if policy.startswith("tariffs_"):return policy[len("tariffs_"):]
        if policy.startswith("subventions_"):return "subsidy_"+policy[len("subventions_"):]
        return policy

    @classmethod
    def _canonical_tariff_policy(cls,policy):
        # Accept the native policy names and their player-facing readback words.
        if not isinstance(policy,str):
            raise ValueError("policy must be a named tariff or subsidy band")
        aliases={cls._tariff_policy_word(native):native for native in _TARIFF_POLICIES}
        if "tariffs_max" in _TARIFF_POLICIES:aliases["maximum"]="tariffs_max"
        canonical=aliases.get(policy,policy)
        if canonical not in _TARIFF_POLICIES:
            raise ValueError("use an original tariff/subvention policy name")
        return canonical

    async def tariffs(self,good):
        """Effective tariff bands, with canonical import_policy/export_policy names."""
        return await self._ctx.perform("economy.tariffs",lambda:self._good_observation(good,False))

    async def consumption_tax(self,good):
        """Read whether the current country actually taxes this good."""
        return await self._ctx.perform("economy.consumption_tax",lambda:self._good_observation(good,True))

    async def _good_observation(self,good,tax):
        ctx=self._ctx;await ctx.refresh();country=await ctx.country_id();handles=[];known=False
        relation=await market_relation.observe_country_market(ctx.native,ctx.process,
            country_type_id=await ctx.type_id("Country"),country_id=country,world=ctx.world,
            adapter=ctx.adapters["data"],await_result=lambda label,q:ctx.wait(q),record={})
        if relation.get("market_id") is None:return {"good":good,"reason":"Current market unavailable"}
        selected,_=await self._market_labels(relation["market_id"],keep_key=good)
        if "binding" not in selected:return {"good":good,"reason":"Good unavailable in the native market"}
        handles=selected["handles"]
        bindings=(data.ObjectBinding.country(await ctx.type_id("Country"),country),selected["binding"])
        try:
            if tax:
                value=await self._evaluate("Goods.IsTaxed(Country.Self)",bindings,handles)
                known=True
                usable=not value.error and value.kind==data.Kind.BOOL
                return {"country_id":country,"good":good,"name":selected["name"],
                    "taxed":value.value if usable else None,
                    "reason":None if usable else "Native membership observation unavailable"}
            result={"country_id":country,"good":good,"name":selected["name"],"reason":None}
            for direction in ("import","export"):
                active=[];complete=True
                for policy in _TARIFF_POLICIES:
                    value=await self._evaluate(_tariff_expression(direction,policy,query=True),bindings,handles)
                    if value.error or value.kind!=data.Kind.BOOL:complete=False
                    elif value.value:active.append(policy)
                if complete and len(active)==1:
                    policy=active[0]
                    result[direction]=self._tariff_policy_word(policy)
                    result[direction+"_policy"]=policy
                else:
                    result[direction]=None;result[direction+"_policy"]=None
                    result["reason"]="Effective policy observation incomplete"
            known=True;result["scope"]="effective policy; the native game may delegate to the market owner"
            return result
        finally:
            if known:await self._release(handles)
            elif handles:self._retain({"retained_goods_observation_values":handles})

    async def set_tax_level(self,level):
        """Set one native national tax band, not a fabricated effective rate."""
        if level not in _LEVELS:raise ValueError("level must be very_low, low, medium, high or very_high")
        return await self._ctx.perform("economy.set_tax_level",lambda:self._national_command("SetTaxLevel"+_LEVELS[level],level))

    async def set_wages(self,sector,level):
        """Set government or military wages to an original native band."""
        if sector not in ("government","military") or level not in _LEVELS:
            raise ValueError("sector must be government/military; use a named wage band")
        method="Set"+sector.title()+"WageLevel"+_LEVELS[level]
        return await self._ctx.perform("economy.set_wages",lambda:self._national_command(method,level,sector))

    async def _national_command(self,method,level=None,sector=None,*,submit=True,requested=None):
        ctx=self._ctx;await ctx.refresh();country=await ctx.country_id();handles=[];uncertain=False
        request={"country_id":country,**(requested if requested is not None else {"level":level,"sector":sector})}
        answer=None
        if not await self._controlled(country):
            return {"accepted":False,"allowed":False,**request,"reason":"You do not currently control this country"}
        try:
            if sector:
                before,_=await self._wage_level(country,sector)
                if before==level:
                    answer={"accepted":True,"changed":False,**request}
                    return answer
            binding=data.ObjectBinding.country(await ctx.type_id("Country"),country)
            generated=await self._evaluate("Country."+method,(binding,),handles)
            if generated.error:
                answer={"accepted":False,"native_error":generated.error,**request,
                    "reason":"Native producer error "+str(generated.error)}
                return answer
            answer=await self._submit_generated(generated,handles,request,submit=submit)
            uncertain=answer.pop("_retain_inputs",False)
            if sector and answer.get("accepted"):
                after,reason=await self._wage_level(country,sector)
                answer.update(observed_level=after,effect_observed=after==level)
                if reason:answer["observation_reason"]=reason
            return answer
        except BaseException:
            uncertain=True;self._retain({"retained_economy_values":handles});raise
        finally:
            if not uncertain:
                try:
                    warnings=await self._release(handles)
                    if warnings and answer is not None:
                        answer.setdefault("cleanup_warnings",[]).extend(warnings)
                except Exception as cleanup:
                    self._retain({"retained_economy_values":handles,"cleanup_error":cleanup})
                    if answer is not None:answer.setdefault("cleanup_warnings",[]).append(str(cleanup))
                    ctx.diagnostics.append({"operation":"economy."+method,"cleanup_error":cleanup})

    async def _submit_generated(self,generated,handles,requested,*,submit=True):
        ctx=self._ctx
        native_results={"generated":generated}
        requested={**requested,"native_results":native_results}
        async def observed(phase,label,factory,**options):
            try:
                result=await ctx.query(label,factory,**options)
            except BaseException as error:
                error.economy_native_results=native_results
                raise
            native_results[phase]=result
            return result
        if generated.error or not isinstance(generated.value,data.NativeDataCommand):
            return {"accepted":False,"native_error":generated.error,
                "reason":"The original game function did not produce a command",**requested}
        carrier=await observed("command","economy.command",lambda:data.command_value(ctx.native,generated.value_handle,
            adapter=ctx.adapters["data"],process=ctx.process))
        if carrier.error or carrier.value.generation_world!=ctx.world:
            return {"accepted":False,"native_error":carrier.error,
                "reason":"The command's game world changed before submission",**requested}
        clone=await observed("clone","economy.clone",lambda:commands.request(ctx.native,commands.Action.CLONE,
            command=carrier.value.primary_pointer,world=ctx.world,capacity=8,
            adapter=ctx.adapters["commands"],process=ctx.process))
        if clone.error or not clone.cloned_command:
            if clone.cloned_command:
                self._retain({"uncertain_clone":clone,"source":generated,"handles":handles})
                return {"accepted":None,"native_error":clone.error,
                    "reason":"Command clone ownership is unresolved","_retain_inputs":True,**requested}
            return {"accepted":False,"native_error":clone.error,"reason":"Native command clone failed",**requested}
        if clone.cloned_command==carrier.value.primary_pointer:
            self._retain({"uncertain_clone":clone,"source":generated,"handles":handles})
            return {"accepted":None,"reason":"The native clone was not independent","_retain_inputs":True,**requested}
        owner=commands.NativeCommand.from_result(ctx.native,clone,adapter=ctx.adapters["commands"],process=ctx.process)
        uncertain=False;answer=None
        def done(value):
            nonlocal answer
            answer=value
            return value
        try:
            serialized=await observed("serialize","economy.command_terms",owner.serialize,owner=owner)
            if serialized.error:return done({"accepted":False,"native_error":serialized.error,
                "reason":"The game could not serialize this command",**requested})
            valid=await observed("validation","economy.validate",lambda:owner.validate(capacity=65536),owner=owner)
            if valid.error not in (0,122) or not valid.validated:
                return done({"accepted":False,"allowed":False,"native_validation_error":valid.error,
                    "native_validated":valid.validated,"reason":valid.reason or "The game rejected this change",**requested})
            if not submit:return done({"allowed":True,"native_validation_error":valid.error,
                "native_validated":valid.validated,"reason":valid.reason or None,**requested})
            if "country_id" in requested and not await self._controlled(requested["country_id"]):
                return done({"accepted":False,"reason":"Country control changed before this action",**requested})
            submitted=await observed("submit","economy.submit",lambda:owner.submit(4),owner=owner)
            outcome=getattr(getattr(owner,"attempt",None),"outcome",None)
            if outcome in ("pending","unknown"):
                uncertain=True;self._retain({"retained_native_owner":owner,"receipt":submitted,"handles":handles})
                return done({"accepted":None,"reason":"The original command is still being reconciled",
                    "native_error":submitted.error,"native_accepted":submitted.accepted,
                    "_retain_inputs":True,**requested})
            return done({"accepted":bool(not submitted.error and submitted.accepted),
                "native_error":submitted.error,"native_accepted":submitted.accepted,
                "reason":submitted.reason or ("Native error "+str(submitted.error) if submitted.error else None),
                "effect":"submitted; fresh game observations report the resulting state",**requested})
        except BaseException as error:
            error.economy_native_results=native_results
            uncertain=True;self._retain({"retained_native_owner":owner,"handles":handles});raise
        finally:
            outcome=getattr(getattr(owner,"attempt",None),"outcome",None)
            if not uncertain and owner.command and owner.pending is None and outcome not in ("pending","unknown"):
                try:
                    destroyed=await ctx.query("economy.destroy",owner.destroy,owner=owner)
                    if answer is not None:answer.setdefault("cleanup_results",{})["destroy"]=destroyed
                    if destroyed.error:
                        raise RuntimeError("native command cleanup error "+str(destroyed.error))
                except BaseException as cleanup:
                    self._retain({"retained_native_owner":owner,"handles":handles,"cleanup_error":cleanup})
                    if answer is not None:
                        answer["_retain_inputs"]=True
                        answer.setdefault("cleanup_warnings",[]).append(str(cleanup))
                        answer.setdefault("cleanup_errors",[]).append(cleanup)
                    ctx.diagnostics.append({"operation":"economy.destroy","cleanup_error":cleanup,"owner":owner})
                    if not isinstance(cleanup,Exception):
                        cleanup.economy_native_results=native_results
                        if answer is not None:cleanup.economy_action_result=answer
                        raise

    async def set_tariff(self,good,direction,policy):
        """Set an original import/export tariff or subvention band for a game key."""
        policy=self._canonical_tariff_policy(policy)
        if direction not in _TARIFF_DIRECTIONS:
            raise ValueError("use import/export and an original tariff/subvention policy name")
        return await self._ctx.perform("economy.set_tariff",lambda:self._goods_control(good,direction,policy,None))

    async def set_consumption_tax(self,good,enabled=True):
        """Idempotent intent over the native tax toggle; never blindly toggles again."""
        if type(enabled) is not bool:raise ValueError("enabled must be True or False")
        return await self._ctx.perform("economy.set_consumption_tax",lambda:self._goods_control(good,None,None,enabled))

    async def _goods_control(self,good,direction,policy,enabled):
        ctx=self._ctx;await ctx.refresh();country=await ctx.country_id()
        if not isinstance(good,str) or not good:raise ValueError("good must be an original game key")
        if not await self._controlled(country):
            return {"accepted":False,"country_id":country,"reason":"You do not currently control this country"}
        relation=await market_relation.observe_country_market(ctx.native,ctx.process,
            country_type_id=await ctx.type_id("Country"),country_id=country,world=ctx.world,
            adapter=ctx.adapters["data"],await_result=lambda label,q:ctx.wait(q),record={})
        if relation.get("market_id") is None:return {"accepted":False,"reason":"Current market unavailable"}
        selected,_=await self._market_labels(relation["market_id"],keep_key=good)
        if not isinstance(selected,dict) or "binding" not in selected:
            return {"accepted":False,"reason":"Good not found in the original market provider","good":good}
        handles=selected["handles"];uncertain=False;answer=None;before=None
        bindings=(data.ObjectBinding.country(await ctx.type_id("Country"),country),selected["binding"])
        try:
            if enabled is not None:
                before=await self._evaluate("Goods.IsTaxed(Country.Self)",bindings,handles)
                if before.error or before.kind!=data.Kind.BOOL:
                    answer={"accepted":False,"reason":"Current consumption tax membership unavailable","before_result":before}
                    return answer
                if before.value==enabled:
                    answer={"accepted":True,"changed":False,"country_id":country,"good":good,"taxed":enabled,"before_result":before}
                    return answer
                expression="Goods.ToggleTaxation(Country.Self)"
            else:expression=_tariff_expression(direction,policy)
            generated=await self._evaluate(expression,bindings,handles)
            answer=await self._submit_generated(generated,handles,{"good":good,"direction":direction,"policy":policy,"country_id":country})
            uncertain=answer.pop("_retain_inputs",False)
            if before is not None:answer["before_result"]=before
            if answer["accepted"]:
                expression="Goods.IsTaxed(Country.Self)" if enabled is not None else _tariff_expression(direction,policy,query=True)
                answer["readback_expression"]=expression
                after=await self._evaluate(expression,bindings,handles)
                answer["readback_result"]=after
                answer["observed"]=after.value if not after.error and after.kind==data.Kind.BOOL else None
                answer["effect_observed"]=answer["observed"]==(enabled if enabled is not None else True)
            return answer
        except BaseException as error:
            if answer is None:answer=getattr(error,"economy_action_result",None)
            if answer is not None:error.goods_action_result=answer
            if before is not None:error.goods_before_result=before
            uncertain=True;self._retain({"retained_goods_control_values":handles});raise
        finally:
            if not uncertain:
                try:
                    warnings=await self._release(handles)
                    if warnings and answer is not None:answer.setdefault("cleanup_warnings",[]).extend(warnings)
                except BaseException as cleanup:
                    self._retain({"retained_goods_control_values":handles,"cleanup_error":cleanup})
                    if answer is not None:
                        answer.setdefault("cleanup_warnings",[]).append(str(cleanup))
                        answer.setdefault("cleanup_errors",[]).append(cleanup)
                    ctx.diagnostics.append({"operation":"economy.goods_control.release","cleanup_error":cleanup})
                    if not isinstance(cleanup,Exception):
                        if answer is not None:cleanup.goods_action_result=answer
                        raise
