"""CPU-only formation-management identity, ownership, and one-shot tests."""
from __future__ import annotations

from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_commands as commands
from pdx_runtime.v3_game.military import formations as warformation
from pdx_runtime.v3_game._run import Run as OwnedRun


TOKENS = {
    "military_formation_borrowing": 900,
    "formation": 901,
    "borrow_setting": 902,
    "can_be_borrowed_by_any": 903,
    "can_only_be_borrowed_by_own_country": 904,
    "cannot_be_borrowed": 905,
    "designate_strategic_objective": 910,
    "formations": 911,
    "state": 912,
}


class API:
    def _native_full_id(self, value):
        return value if type(value) is int and 0 <= value < 0xFFFFFFFF else None


class Context:
    def __init__(self):
        self.native = object()
        self.process = object()
        self.adapters = {"data": "data", "commands": "commands"}
        self.actor = 42
        self.created = []

    async def country_id(self):
        return self.actor

    async def query(self, label, factory, **kwargs):
        return factory()


class Owner:
    def __init__(self, wire=b"", name="owner", world=77):
        self.wire = wire
        self.name = name
        self.world = world
        self.command = 100
        self.pending = None
        self.attempt = None
        self.calls = []

    def serialize(self):
        self.calls.append("serialize")
        return NS(error=0, data=self.wire)

    def destroy(self):
        self.calls.append("destroy")
        self.command = 0
        self.attempt = NS(outcome="consumed")
        return NS(error=0, command=0)


class Run:
    def __init__(self):
        self.api = API()
        self.ctx = Context()
        self.native = self.ctx.native
        self.process = self.ctx.process
        self.adapters = self.ctx.adapters
        self.namespace = "military"
        self.world = 77
        self.actor = 42
        self.owners = []
        self.operations = []
        self.providers = []
        self.elements_by_expression = {}
        self.element_calls = []
        self.values = {}
        self.destroyed = []
        self.retain_unknown = False

    async def country(self):
        return "country"

    async def provider(self, receiver, method, arguments=0):
        self.providers.append((receiver, method, arguments))
        return object()

    async def functions(self, receiver):
        values = {
            "MilitaryFormation": {"GetNameNoFormatting": object()},
            "Hq": {"GetNameNoFlagNoConcept": object()},
            "State": {"GetNameNoFormatting": object()},
        }
        return values.get(receiver, {})

    async def elements(self, expression, bindings, target, *, wrapper_type=None):
        self.element_calls.append((expression, bindings, target, wrapper_type))
        for index, binding in enumerate(self.elements_by_expression.get(expression, ())):
            yield index, NS(value_handle=1000 + index), binding

    async def field(self, receiver, method, bindings, kinds, *, arguments=0, expression=None):
        key = (expression or receiver + "." + method, bindings)
        value = self.values.get(key, self.values.get(key[0], (None, "unavailable: " + key[0])))
        return value() if callable(value) else value

    async def type_id(self, name):
        return {"Hq": 77}[name]

    async def evaluate(self, expression, bindings):
        return self.values[("evaluate", expression)]

    async def call(self, label, factory, **kwargs):
        return factory()

    command_result = OwnedRun.command_result

    def wrapper(self, result, named_type):
        self.values.setdefault("wrapper_calls", []).append((result, named_type))
        return result.value

    async def origin(self, result, named_type):
        self.values.setdefault("origin_calls", []).append((result, named_type))
        return self.values.get(("origin", result.value_handle), 0x81000003)

    async def destroy(self, owner):
        owner.destroy()
        self.destroyed.append(owner.name)

    adopt = OwnedRun.adopt


class FormationManagementTests(unittest.IsolatedAsyncioTestCase):
    def immediate(self, run):
        async def owned_read(api, label, operation):
            run.api = api
            return await operation(run)
        return owned_read

    async def test_borrow_identity_uses_native_token_name_and_caches_it(self):
        run = Run()
        wire = commands.encode_command(TOKENS["military_formation_borrowing"], {
            TOKENS["formation"]: commands.UInt32(17),
            TOKENS["borrow_setting"]: commands.Token(TOKENS["cannot_be_borrowed"]),
        })
        owner = Owner(wire)
        calls = []

        async def token(current, name):
            return TOKENS[name]

        def token_name(native, token_value, **kwargs):
            calls.append(token_value)
            return NS(error=0, token_name="cannot_be_borrowed",
                      token_name_truncated=False)

        with (patch.object(warformation, "_token", token),
              patch.object(warformation.commands, "token_name", token_name)):
            identity = await warformation._borrow_policy_identity(run, owner, 17)
            cached = await warformation._native_policy_identity(
                run, TOKENS["cannot_be_borrowed"])
        self.assertEqual(identity, {
            "policy": "cannot_be_borrowed",
            "policy_token": TOKENS["cannot_be_borrowed"],
        })
        self.assertEqual(cached, identity)
        self.assertEqual(calls, [TOKENS["cannot_be_borrowed"]])
        self.assertEqual(owner.calls, ["serialize"])

    async def test_unknown_native_policy_name_keeps_raw_token_and_error(self):
        run = Run()
        unknown_token = 999
        wire = commands.encode_command(TOKENS["military_formation_borrowing"], {
            TOKENS["formation"]: commands.UInt32(17),
            TOKENS["borrow_setting"]: commands.Token(unknown_token),
        })
        native_result = NS(error=1168, token_name="", token_name_truncated=False)

        async def token(current, name):
            return TOKENS[name]

        with (patch.object(warformation, "_token", token),
              patch.object(warformation.commands, "token_name",
                           lambda *args, **kwargs: native_result)):
            identity = await warformation._borrow_policy_identity(
                run, Owner(wire), 17)
        self.assertIsNone(identity["policy"])
        self.assertEqual(identity["policy_token"], unknown_token)
        self.assertEqual(identity["policy_name_native_error"], 1168)
        self.assertIs(identity["policy_name_native_result"], native_result)

    async def test_borrow_identity_rejects_a_command_for_another_formation(self):
        run = Run()
        wire = commands.encode_command(TOKENS["military_formation_borrowing"], {
            TOKENS["formation"]: commands.UInt32(99),
            TOKENS["borrow_setting"]: commands.Token(TOKENS["can_be_borrowed_by_any"]),
        })

        async def token(current, name):
            return TOKENS[name]

        with patch.object(warformation, "_token", token):
            with self.assertRaisesRegex(Exception, "different formation"):
                await warformation._borrow_policy_identity(run, Owner(wire), 17)

    async def test_borrow_listing_serializes_options_but_never_validates_or_submits(self):
        run = Run()
        run.elements_by_expression["MilitaryFormation.GetBorrowSettings"] = ["any", "none"]
        run.values.update({
            ("MilitaryFormationBorrowSetting.GetName", ("any",)): ("Anyone", None),
            ("MilitaryFormationBorrowSetting.GetDesc", ("any",)): ("Allies may borrow", None),
            ("MilitaryFormationBorrowSetting.IsSelected", ("any",)): (False, None),
            ("MilitaryFormationBorrowSetting.GetName", ("none",)): ("Nobody", None),
            ("MilitaryFormationBorrowSetting.GetDesc", ("none",)): ("No borrowing", None),
            ("MilitaryFormationBorrowSetting.IsSelected", ("none",)): (True, None),
        })
        owners = {name: Owner(name=name) for name in ("any", "none")}

        async def formation(current, wanted, **kwargs):
            return "army", "army"

        async def produced(current, expression, bindings):
            return owners[bindings[0]]

        async def policy_identity(current, owner, formation_id):
            owner.calls.append("serialize")
            values = {"any": ("can_be_borrowed_by_any", 903),
                      "none": ("cannot_be_borrowed", 905)}
            name, token_value = values[owner.name]
            return {"policy": name, "policy_token": token_value}

        with patch.multiple(warformation, owned_read=self.immediate(run), _formation=formation,
                _produced_owner=produced, _borrow_policy_identity=policy_identity):
            result = await warformation.formation_borrow_policies(API(), 17)
        self.assertEqual(result["selected_policy"], "cannot_be_borrowed")
        self.assertEqual([row["policy"] for row in result["policies"]],
                         ["can_be_borrowed_by_any", "cannot_be_borrowed"])
        self.assertEqual(owners["any"].calls, ["serialize", "destroy"])
        self.assertEqual(owners["none"].calls, ["serialize", "destroy"])

    async def test_borrow_action_validates_only_the_selected_native_policy(self):
        run = Run()
        run.elements_by_expression["MilitaryFormation.GetBorrowSettings"] = ["any", "own", "none"]
        run.values[("MilitaryFormationBorrowSetting.IsSelected", ("own",))] = (False, None)
        owners = {name: Owner(name=name) for name in ("any", "own", "none")}
        validated = []

        async def formation(current, wanted, **kwargs):
            return "army", "army"

        async def produced(current, expression, bindings):
            return owners[bindings[0]]

        async def policy_identity(current, owner, formation_id):
            values = {"any": ("can_be_borrowed_by_any", 903),
                      "own": ("can_only_be_borrowed_by_own_country", 904),
                      "none": ("cannot_be_borrowed", 905)}
            name, token_value = values[owner.name]
            return {"policy": name, "policy_token": token_value}

        async def current_policy(current, formation_binding, formation_id):
            return {"policy": "can_be_borrowed_by_any", "policy_token": 903}

        async def validate(current, owner, result, *, validate_only=False):
            validated.append(owner.name)
            result.update(status="available", eligible=True, accepted=None)
            return False

        with patch.multiple(warformation, owned_read=self.immediate(run), _formation=formation,
                _produced_owner=produced, _borrow_policy_identity=policy_identity,
                _current_borrow_policy=current_policy, _validate_submit=validate):
            result = await warformation.set_formation_borrow_policy(API(), 17,
                "can_only_be_borrowed_by_own_country", validate_only=True)
        self.assertEqual(validated, ["own"])
        self.assertEqual(result["allowed_country_or_policy"], {
            "policy": "can_only_be_borrowed_by_own_country", "policy_token": 904})
        self.assertEqual(owners["any"].calls, ["destroy"])
        self.assertEqual(owners["none"].calls, [])

    async def test_borrow_action_can_select_future_policy_by_raw_native_token(self):
        run = Run()
        run.elements_by_expression["MilitaryFormation.GetBorrowSettings"] = ["future"]
        run.values[("MilitaryFormationBorrowSetting.IsSelected", ("future",))] = (False, None)
        owner = Owner(name="future")
        validated = []

        async def formation(current, wanted, **kwargs):
            return "army", "army"

        async def produced(current, expression, bindings):
            return owner

        async def policy_identity(current, current_owner, formation_id):
            return {"policy": None, "policy_token": 999,
                    "policy_name_native_error": 1168,
                    "policy_name_error": "native token-name error 1168"}

        async def current_policy(current, formation_binding, formation_id):
            return None

        async def validate(current, current_owner, result, *, validate_only=False):
            validated.append(current_owner.name)
            result.update(status="available", eligible=True, accepted=None)
            return False

        with patch.multiple(warformation, owned_read=self.immediate(run), _formation=formation,
                _produced_owner=produced, _borrow_policy_identity=policy_identity,
                _current_borrow_policy=current_policy, _validate_submit=validate):
            result = await warformation.set_formation_borrow_policy(
                API(), 17, 999, validate_only=True)
        self.assertEqual(validated, ["future"])
        self.assertEqual(result["policy_token"], 999)
        self.assertIsNone(result["policy"])

    async def test_hq_source_uses_gui_hq_type_and_cpp_hq_wrapper_case(self):
        run = Run()
        run.elements_by_expression["Country.AccessLandHQs"] = ["hq"]

        async def summary(current, element, binding):
            return {"id": 0x81000003}

        with patch.object(warformation, "_hq_summary", summary):
            source, rows = await warformation._home_candidates(run, "army")
        self.assertEqual(source, "AccessLandHQs")
        self.assertEqual(rows, [{"id": 0x81000003}])
        self.assertEqual(run.element_calls, [
            ("Country.AccessLandHQs", ("country",), "Hq", "HQ")])

    async def test_set_home_uses_explicit_hq_id_without_catalog_eligibility_gate(self):
        run = Run()
        owners = []
        created = []
        reads = iter((({"name": "North Germany", "id": None}, None),
                      ({"name": "Rhine", "id": None}, None)))

        async def formation(current, wanted, **kwargs):
            return "army", "army"

        async def forbidden_catalog(*args, **kwargs):
            raise AssertionError("explicit action must not enumerate the HQ catalog")

        async def home_readback(current, binding):
            return next(reads)

        async def id_owner(current, command_name, parameters):
            created.append((command_name, parameters))
            owner = Owner(name="rehome")
            owners.append(owner)
            return owner, NS(error=0)

        async def validate(current, owner, result, *, validate_only=False):
            result.update(status="accepted", eligible=True, accepted=True)
            owner.command = 0
            owner.attempt = NS(outcome="consumed")
            return True

        with patch.multiple(warformation, owned_read=self.immediate(run), _formation=formation,
                _home_candidates=forbidden_catalog, _home_readback=home_readback,
                _id_owner=id_owner, _validate_submit=validate):
            result = await warformation.set_formation_home(API(), 17, 0x81000003)
        self.assertEqual(created, [("rehome_military_formation",
                                   {"formation": 17, "home_hq": 0x81000003})])
        self.assertEqual(result["selected_home_location"], {
            "id": 0x81000003, "identity": "complete_native_hq_ref"})
        self.assertEqual(result["new_home"]["name"], "Rhine")
        self.assertTrue(result["native_effect"]["accepted"])

    async def test_strategic_owner_encodes_one_formation_list_and_state_id(self):
        run = Run()
        owner = Owner(name="objective", world=88)
        created_result = NS(error=0, command=123, world=88)
        captured = []

        async def token(current, name):
            return TOKENS[name]

        def create(native, type_token, **kwargs):
            captured.append((type_token, kwargs))
            return created_result

        with (patch.object(warformation, "_token", token),
              patch.object(warformation.commands, "create", create),
              patch.object(warformation.commands.NativeCommand, "from_result",
                           lambda *args, **kwargs: owner)):
            actual, receipt = await warformation._strategic_owner(run, 17, 0x82000009)
        self.assertIs(actual, owner)
        self.assertIs(receipt, created_result)
        self.assertEqual(run.owners, [owner])
        self.assertEqual(owner.world, created_result.world)
        self.assertEqual(captured[0][0], TOKENS["designate_strategic_objective"])
        self.assertEqual(captured[0][1]["parameters"], {
            TOKENS["formations"]: (commands.UInt32(17),),
            TOKENS["state"]: commands.UInt32(0x82000009),
        })

    async def test_clear_objective_targets_the_observed_state_and_reads_back_none(self):
        run = Run()
        formation_calls = []
        objectives = iter((({"id": 0x82000009, "name": "Silesia"}, None),
                           (None, None)))
        command_targets = []

        async def formation(current, wanted, **kwargs):
            formation_calls.append((wanted, kwargs))
            return "army", "army"

        async def objective(current, binding):
            return next(objectives)

        async def owner(current, formation_id, state_id):
            command_targets.append((formation_id, state_id))
            return Owner(name="objective"), NS(error=0)

        async def validate(current, command, result, *, validate_only=False):
            result.update(status="accepted", eligible=True, accepted=True)
            command.command = 0
            command.attempt = NS(outcome="consumed")
            return True

        with patch.multiple(warformation, owned_read=self.immediate(run), _formation=formation,
                _strategic_objective=objective, _strategic_owner=owner,
                _validate_submit=validate):
            result = await warformation.strategic_objective(API(), 17, None)
        self.assertEqual(command_targets, [(17, 0x82000009)])
        self.assertEqual(result["target_state_or_clear"], "clear")
        self.assertIsNone(result["objective_readback"])
        self.assertTrue(result["observed_objective_matches"])
        self.assertTrue(result["native_effect"]["accepted"])
        self.assertEqual(formation_calls, [(17, {"kind": "army"}),
                                           (17, {"kind": "army"})])

    async def test_uint32_max_is_not_a_strategic_objective_clear_sentinel(self):
        with self.assertRaisesRegex(Exception, "complete native ID"):
            await warformation.strategic_objective(API(), 17, 0xFFFFFFFF)

    async def test_repeated_objective_set_is_noop_not_an_accidental_toggle(self):
        run = Run()

        async def formation(current, wanted, **kwargs):
            return "army", "army"

        async def objective(current, binding):
            return {"id": 0x82000009, "name": "Silesia"}, None

        async def forbidden(*args, **kwargs):
            raise AssertionError("repeated set must not materialize a toggle command")

        with patch.multiple(warformation, owned_read=self.immediate(run), _formation=formation,
                _strategic_objective=objective, _strategic_owner=forbidden):
            result = await warformation.strategic_objective(API(), 17, 0x82000009)
        self.assertEqual(result["status"], "unchanged")
        self.assertFalse(result["native_effect"]["submitted"])


if __name__ == "__main__":
    unittest.main()
