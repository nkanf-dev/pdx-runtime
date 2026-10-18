"""Source-scoped military tasks. These selectors hold copied IDs, never native owners."""
from __future__ import annotations

from . import (_warfare, _observations, battles, forces, formations, formation_names,
               logistics, peace, ships, naval_battles, naval_design,
               recruitment, commander_careers)


class CountrySource:
    """Binding context, not an executor or a claim that supplied IDs are related."""
    def __init__(self, api, country_id=None):
        if country_id is not None:
            api._id("country_id", country_id)
        self._api = api
        self._source_country_id = country_id

    def __getattr__(self, name):
        return getattr(self._api, name)


class _Task:
    def __init__(self, api, identity=None, country_id=None):
        self._api = api
        self.id = identity
        self._source = CountrySource(api, country_id)

    async def _call(self, module, method, *args, action=False, **kwargs):
        return await self._api._call(module, method, *args, action=action, source=self._source, **kwargs)

    async def _action(self, module, method, *args, validate_only=False, **kwargs):
        return await self._call(module, method, *args, validate_only=validate_only,
                                action=not validate_only, **kwargs)


def _one(report, key):
    rows = report.get(key)
    if not rows:
        return report
    row = rows[0]
    row["source_observation"] = {name: value for name, value in report.items() if name != key}
    if "cleanup" in report:
        row["cleanup"] = report["cleanup"]
    return row


def _formation_links(report):
    for front in report.get("fronts") or ():
        if front.get("id") is not None:
            front["selector"] = {"type": "front", "id": front["id"], "war_id": front["war_id"]}
        for side in front.get("sides", {}).values():
            for row in side.get("formations") or ():
                if row.get("formation_id") is not None and row.get("country_id") is not None:
                    row["selector"] = {"type": "formation", "id": row["formation_id"], "country_id": row["country_id"]}
    return report


class War(_Task):
    async def read(self, *, support=True):
        if type(support) is not bool:
            raise ValueError("support must be a boolean")
        result = await self._call(_warfare, "country_wars", self.id, include_support_details=support)
        return _one(result, "wars")

    async def fronts(self):
        return _formation_links(await self._call(battles, "front_situations", war_id=self.id))

    def front(self, front_id):
        return Front(self._api, self.id, self._api._id("front_id", front_id))

    async def battles(self, *, detail=False):
        if type(detail) is not bool:
            raise ValueError("detail must be a boolean")
        return await self._call(battles if detail else _warfare,
                                "battle_details" if detail else "battles", war_id=self.id)

    async def history(self):
        return await self._call(battles, "battle_history", war_id=self.id)

    async def exhaustion(self):
        return await self._call(battles, "war_exhaustion", war_id=self.id)

    async def invasions(self):
        return await self._call(ships, "invasions", war_id=self.id)

    def invasion(self, invasion_id):
        return Invasion(self._api, self.id, self._api._id("invasion_id", invasion_id))

    @property
    def peace(self):
        return Peace(self._api, self.id)


class Front(_Task):
    def __init__(self, api, war_id, front_id):
        super().__init__(api, front_id)
        self.war_id = war_id

    async def read(self):
        report = await self._call(battles, "front_situations", war_id=self.war_id, front_id=self.id)
        return _one(_formation_links(report), "fronts")


class Peace(_Task):
    async def read(self):
        return await self._call(peace, "peace_read", self.id)

    async def goals(self):
        return await self._call(peace, "war_goals", self.id)

    async def set_goal(self, goal_id, *, pressed=True):
        self._api._id("goal_id", goal_id)
        if type(pressed) is not bool:
            raise ValueError("pressed must be a boolean")
        return await self._call(peace, "set_goal_pressed", self.id, goal_id, pressed, action=True)

    async def propose(self):
        return await self._call(_warfare, "peace_action", self.id, "propose_peace", action=True)

    async def white_peace(self):
        return await self._call(_warfare, "peace_action", self.id, "set_white_peace", action=True)

    async def toggle_acceptance(self):
        return await self._call(_warfare, "peace_action", self.id, "toggle_peace_acceptance", action=True)

    async def capitulate(self):
        return await self._call(_warfare, "peace_action", self.id, "capitulate", action=True)


class Invasion(_Task):
    def __init__(self, api, war_id, invasion_id):
        super().__init__(api, invasion_id)
        self.war_id = war_id

    async def act(self, action, *, formation_id=None, validate_only=False):
        if formation_id is not None:
            self._api._id("formation_id", formation_id)
        return await self._action(ships, "invasion_action", self.war_id, self.id, action,
                                  formation_id=formation_id, validate_only=validate_only)


class Formation(_Task):
    async def read(self):
        return await self._call(_warfare, "formation_details", self.id)

    @property
    def units(self):
        return Units(self._api, self.id, self._source._source_country_id)

    @property
    def recruitment(self):
        return UnitRecruitment(self._api, self.id, self._source._source_country_id)

    @property
    def mobilization(self):
        return Mobilization(self._api, self.id, self._source._source_country_id)

    @property
    def naval(self):
        return Naval(self._api, self.id, self._source._source_country_id)

    async def logistics(self):
        # Separate original observations; do not claim an atomic combined snapshot.
        return {"formation_id": self.id, "atomic_snapshot": False,
                "attrition": await self._call(logistics, "attrition", self.id),
                "priority": await self._call(_warfare, "formation_supply_options", self.id)}

    async def set_supply_priority(self, priority):
        if priority not in ("low", "medium", "high"):
            raise ValueError("priority must be low, medium or high")
        return await self._call(_warfare, "formation_producer_action", self.id,
                                "set_supply_priority", priority=priority, action=True)

    async def rename(self, name, *, validate_only=False):
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        return await self._action(formation_names, "formation_rename", self.id, name,
                                  validate_only=validate_only)

    async def disband(self, *, validate_only=False):
        return await self._action(forces, "formation_disband", self.id, validate_only=validate_only)

    async def borrow_policies(self):
        return await self._call(formations, "formation_borrow_policies", self.id)

    async def set_borrow_policy(self, policy, *, validate_only=False):
        if not ((isinstance(policy, str) and policy.strip()) or
                (type(policy) is int and 0 <= policy <= 0xffff)):
            raise ValueError("policy must be a native name or uint16 token")
        return await self._action(formations, "set_formation_borrow_policy", self.id, policy,
                                  validate_only=validate_only)

    async def home_options(self):
        return await self._call(formations, "formation_home_options", self.id)

    async def rehome(self, hq_id, *, validate_only=False):
        self._api._id("hq_id", hq_id)
        return await self._action(formations, "set_formation_home", self.id, hq_id,
                                  validate_only=validate_only)

    async def set_objective(self, state_id, *, validate_only=False):
        if state_id is not None:
            self._api._id("state_id", state_id)
        return await self._action(formations, "strategic_objective", self.id, state_id,
                                  validate_only=validate_only)

    async def deployment(self, front_id):
        self._api._id("front_id", front_id)
        return await self._action(_warfare, "formation_command", "deploy_military_formation_to_front",
                                  self.id, front_id, validate_only=True)

    async def deploy(self, front_id):
        self._api._id("front_id", front_id)
        return await self._call(_warfare, "formation_command", "deploy_military_formation_to_front",
                                self.id, front_id, action=True)

    async def station(self, state_id):
        self._api._id("state_id", state_id)
        return await self._call(_warfare, "formation_command", "station_military_formation",
                                self.id, state_id, action=True)


class Units(_Task):
    async def read(self, *, individual=False):
        if type(individual) is not bool:
            raise ValueError("individual must be a boolean")
        return await self._call(forces, "formation_unit_composition", self.id, include_units=individual)

    async def choices(self, *, entry_id_string=None):
        return await self._call(forces, "combat_unit_choices", self.id,
                                entry_id_string=entry_id_string, perform=False)

    async def act(self, action, *, entry_id_string=None, group_id_string=None, validate_only=False):
        if action not in forces._UNIT_ACTIONS:
            raise ValueError("unknown formation unit action")
        selector = group_id_string if forces._UNIT_ACTIONS[action][0] == "group" else entry_id_string
        if not isinstance(selector, str) or not selector:
            raise ValueError("native entry/group ID string is required")
        return await self._action(forces, "formation_unit_action", self.id, action,
            entry_id_string=entry_id_string, group_id_string=group_id_string, validate_only=validate_only)

    async def disband(self, choice, *, validate_only=False):
        forces._choice_input(choice, "CombatUnit", self._api._ctx, "combat_units")
        return await self._action(forces, "disband_combat_unit", choice,
                                  validate_only=validate_only, perform=False)


class UnitRecruitment(_Task):
    async def read(self):
        return await self._call(forces, "formation_recruit_options", self.id)

    async def types(self, *, mode="regular"):
        if mode not in ("regular", "conscript"):
            raise ValueError("mode must be regular or conscript")
        return await self._call(recruitment, "combat_unit_type_choices", self.id, mode=mode, perform=False)

    async def locations(self, type_choice, *, state_id=None):
        if state_id is not None:
            self._api._id("state_id", state_id)
        recruitment._choice_input(type_choice, "CombatUnitType", self._api._ctx, "recruit_unit_types")
        return await self._call(recruitment, "regular_recruitment_options", type_choice,
                                state_id=state_id, perform=False)

    async def recruit(self, type_choice, building_id, *, validate_only=False):
        self._api._id("building_id", building_id)
        recruitment._choice_input(type_choice, "CombatUnitType", self._api._ctx, "recruit_unit_types")
        return await self._action(recruitment, "recruit_regular_unit", type_choice, building_id,
                                  validate_only=validate_only, perform=False)

    async def open_conscript_map(self, type_choice):
        recruitment._choice_input(type_choice, "CombatUnitType", self._api._ctx, "recruit_unit_types")
        return await self._call(recruitment, "open_conscript_recruitment_map", type_choice,
                                perform=False, action=True)


class Mobilization(_Task):
    async def read(self):
        return await self._call(_observations, "mobilization_state", self.id)

    async def set(self, enabled=True, *, validate_only=False):
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        return await self._action(_observations, "set_mobilization", self.id,
                                  enabled=enabled, validate_only=validate_only)

    async def raise_conscripts(self, *, validate_only=False):
        return await self._action(_observations, "set_mobilization", self.id,
                                  conscripts=True, validate_only=validate_only)

    async def options(self):
        return await self._call(forces, "mobilization_options", self.id)

    async def choices(self):
        return await self._call(forces, "mobilization_option_choices", self.id, perform=False)

    async def set_option(self, choice, activated, *, validate_only=False):
        if type(activated) is not bool:
            raise ValueError("activated must be a boolean")
        forces._choice_input(choice, "MobilizationOption", self._api._ctx, "mobilization_options")
        return await self._action(forces, "set_mobilization_option", choice, activated,
                                  validate_only=validate_only, perform=False)

    async def toggle_option(self, choice, *, validate_only=False):
        forces._choice_input(choice, "MobilizationOption", self._api._ctx, "mobilization_options")
        return await self._action(forces, "toggle_mobilization_option", choice,
                                  validate_only=validate_only, perform=False)


class Naval(_Task):
    async def area(self):
        return await self._call(ships, "naval_area_sources", self.id)

    async def mission(self):
        return await self._call(_warfare, "naval_mission_status", self.id)

    async def missions(self):
        return await self._call(_warfare, "naval_missions", self.id)

    async def set_mission(self, mission_key, *, preserve_area=True):
        if not isinstance(mission_key, str) or not mission_key:
            raise ValueError("mission_key must be an original mission key")
        if type(preserve_area) is not bool:
            raise ValueError("preserve_area must be a boolean")
        return await self._call(_warfare, "naval_missions", self.id, mission_key,
                                preserve_area=preserve_area, action=True)

    async def remove_mission(self):
        return await self._call(_warfare, "formation_producer_action", self.id, "remove_naval_mission", action=True)

    async def disembark(self):
        return await self._call(_warfare, "formation_producer_action", self.id, "disembark", action=True)

    async def battles(self, *, detail=False):
        return await self._call(naval_battles, "naval_battles", fleet_id=self.id,
                                include_ships=detail, perform=False)

    async def repair_state(self):
        return await self._call(ships, "fleet_repair_status", self.id)

    async def repair(self, *, validate_only=False):
        return await self._action(ships, "fleet_repair", self.id, validate_only=validate_only)

    async def transport(self):
        """Army transport state and actual fleet capacity, not pair eligibility."""
        return await self._call(ships, "transport_options", self.id)


class Commander(_Task):
    async def read(self, *, traits=False):
        if type(traits) is not bool:
            raise ValueError("traits must be a boolean")
        return await self._call(_observations, "commander_read", self.id, traits=traits)

    async def orders(self):
        return await self._call(_warfare, "commander_orders", self.id)

    async def set_order(self, order_key):
        if not isinstance(order_key, str) or not order_key:
            raise ValueError("order_key must be an original order key")
        return await self._call(_warfare, "commander_orders", self.id, order_key, action=True)

    async def assign(self, formation_id):
        self._api._id("formation_id", formation_id)
        return await self._call(_warfare, "commander_reference_command", "assign_commander_to_formation",
                                self.id, formation_id, action=True)

    async def unassign(self):
        return await self._call(_warfare, "commander_reference_command", "unassign_commander_from_formation", self.id, action=True)

    async def reset_order(self):
        return await self._call(_warfare, "commander_reference_command", "reset_character_order_command", self.id, action=True)

    async def promote(self, *, validate_only=False):
        return await self._action(forces, "commander_promotion", self.id, validate_only=validate_only)

    @property
    def career(self):
        return Career(self._api, self.id, self._source._source_country_id)


class Career(_Task):
    async def choices(self):
        return await self._call(commander_careers, "commander_choices", character_id=self.id, perform=False)

    async def interactions(self, commander_choice):
        commander_careers._choice_input(commander_choice, "Character", self._api._ctx, "commander_career")
        return await self._call(commander_careers, "commander_interaction_choices", commander_choice, perform=False)

    async def invoke(self, commander_choice, interaction_choice, *, readback=True):
        if type(readback) is not bool:
            raise ValueError("readback must be a boolean")
        commander_careers._choice_input(commander_choice, "Character", self._api._ctx, "commander_career")
        commander_careers._choice_input(interaction_choice, "CharacterInteraction", self._api._ctx, "commander_interactions")
        return await self._call(commander_careers, "invoke_commander_interaction", commander_choice,
                                interaction_choice, readback=readback, perform=False, action=True)


class CommanderRecruitment(_Task):
    async def candidates(self, *, role=None):
        return await self._call(forces, "commander_candidates", role=role)

    async def eligibility(self, candidate_id, *, formation_id=None):
        self._api._id("candidate_id", candidate_id)
        if formation_id is not None:
            self._api._id("formation_id", formation_id)
        return await self._call(forces, "commander_recruit_options", candidate_id, formation_id=formation_id)

    async def recruit(self, candidate_id, *, formation_id=None):
        self._api._id("candidate_id", candidate_id)
        if formation_id is not None:
            self._api._id("formation_id", formation_id)
        return await self._call(forces, "recruit_commander", candidate_id, formation_id=formation_id, action=True)


class Ship(_Task):
    async def read(self):
        return await self._call(ships, "ship_state", self.id)

    async def rename(self, name, *, validate_only=False):
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        return await self._action(naval_design, "ship_rename", self.id, name, validate_only=validate_only)

    async def act(self, action, *, template_id=None, validate_only=False):
        if template_id is not None:
            self._api._id("template_id", template_id)
        return await self._action(ships, "ship_action", self.id, action,
                                  template_id=template_id, validate_only=validate_only)

    async def transfer(self, fleet_id):
        self._api._id("fleet_id", fleet_id)
        return await self._call(ships, "transfer_ship", self.id, fleet_id, action=True)


class Template(_Task):
    async def act(self, action, *, fleet_id=None, count=1, validate_only=False):
        if fleet_id is not None:
            self._api._id("fleet_id", fleet_id)
        return await self._action(ships, "ship_template_action", self.id, action,
            fleet_id=fleet_id, count=count, validate_only=validate_only)


class Construction(_Task):
    async def act(self, action, *, new_position=None, validate_only=False):
        return await self._action(ships, "ship_construction_action", self.id, action,
                                  new_position=new_position, validate_only=validate_only)


class ShipQueue(_Task):
    async def read(self):
        return await self._call(ships, "ship_constructions")

    async def state(self):
        return await self._call(ships, "ship_construction_control")

    def entry(self, construction_id):
        return Construction(self._api, self._api._id("construction_id", construction_id))

    async def set_paused(self, paused, *, validate_only=False):
        if type(paused) is not bool:
            raise ValueError("paused must be a boolean")
        return await self._action(ships, "ship_construction_control", paused=paused, validate_only=validate_only)

    async def set_default_fleet(self, fleet_id, *, validate_only=False):
        if fleet_id is not None:
            self._api._id("fleet_id", fleet_id)
        return await self._action(ships, "ship_construction_control", default_fleet_id=fleet_id,
                                  validate_only=validate_only)


class NationalLogistics(_Task):
    async def read(self):
        return {"atomic_snapshot": False,
                "network": await self._call(logistics, "supply_network"),
                "costs": await self._call(logistics, "logistics_costs")}

    async def allocation(self):
        return await self._call(logistics, "supply_allocation_options")

    async def set_allocation(self, level):
        if level not in logistics._LEVELS:
            raise ValueError("unknown native construction allocation level")
        return await self._call(logistics, "set_supply_allocation", level, action=True)
