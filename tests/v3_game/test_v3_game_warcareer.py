"""CPU-only held-choice tests for commander career property actions."""
from __future__ import annotations

from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game.military import commander_careers as warcareer


class Context:
    def __init__(self):
        self.native, self.process = object(), object()
        self.adapters = {"data": "data", "commands": "commands"}
        self.perform_calls = []
        self.world, self.diagnostics, self.query_calls = 0, [], []
        self._types = {"Building": 15}
    async def query(self, label, factory, **kwargs):
        self.query_calls.append((label, kwargs))
        value = factory()
        return getattr(value, "_cpu_result", value)
    async def perform(self, label, operation):
        self.perform_calls.append(label)
        return await operation()
    async def country_id(self): return 42


class API:
    def __init__(self, ctx): self._ctx = ctx
    def _native_full_id(self, value):
        return value if type(value) is int and 0 <= value <= 0xffffffff else None


class Run:
    def __init__(self, api, handles=()):
        self.api, self.ctx = api, api._ctx
        self.native, self.process = self.ctx.native, self.ctx.process
        self.adapters = dict(self.ctx.adapters)
        self.value_adapters = {handle: self.adapters["data"] for handle in handles}
        self.values, self.operations = list(handles), []
        self.status, self.retain_unknown = "working", False
        self.actor = 42
        self.field_values, self.field_calls = {}, []
    async def country(self): return data.ObjectBinding.country(1, 42)
    async def type_id(self, name): return {"Character": 11, "CharacterInteraction": 12}[name]
    async def provider(self, *args): return object()
    async def functions(self, name): return {"GetFullNameNoFormatting": object()}
    async def field(self, receiver, method, bindings, kinds, *, arguments=0, expression=None):
        key = expression or receiver + "." + method
        self.field_calls.append(key)
        value = self.field_values.get((key, bindings), self.field_values.get(key,
            (None, "unavailable: " + key)))
        return value() if callable(value) else value
    def wrapper(self, result, named_type):
        expected = ".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@"
        if (result.value.native_type_name != expected or result.value_handle not in self.values
                or not result.value.pointer):
            raise RuntimeError("not an owned wrapper")
        return result.value
    async def finish(self, result):
        if self.retain_unknown:
            self.status = "retained_pending_or_unknown"
        else:
            self.values.clear(); self.status = "complete"
        return result


def native_result(named_type, type_id, handle):
    name = ".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@"
    return NS(value=data.NativeObject(0x1000 + handle, type_id, handle, name),
              value_handle=handle, error=0, type_id=type_id, native_type_name=name)


class CareerTests(unittest.IsolatedAsyncioTestCase):
    async def catalogs(self):
        ctx, api = Context(), API(Context())
        ctx = api._ctx
        character_run = Run(api, (21,))
        interaction_run = Run(api, (31,))
        character_result = native_result("Character", 11, 21)
        interaction_result = native_result("CharacterInteraction", 12, 31)
        character = data.ObjectBinding(11, 21, retained=True)
        interaction = data.ObjectBinding(12, 31, retained=True)
        character_run.field_values.update({
            ("Character.GetID", (character,)): (73, None),
            ("Character.GetFullNameNoFormatting", (character,)): ("#bold Ada#!", None),
        })
        interaction_run.field_values.update({
            ("CharacterInteraction.GetName", (interaction,)): ("Retire Commander", None),
            ("CharacterInteraction.GetTooltipTag", (interaction,)): ("retire_commander", None),
        })
        async def elements(run, catalog, expression, bindings):
            if expression == "Country.AccessGenerals": yield 0, character_result
            elif expression == "Country.AccessAdmirals": return
            else: yield 0, interaction_result
        with patch.object(warcareer, "WarfareRead", side_effect=[character_run, interaction_run]),                 patch.object(warcareer, "_choice_elements", elements):
            commanders = await warcareer.commander_choices(api, role="general", perform=False)
            interactions = await warcareer.commander_interaction_choices(api, commanders[0], perform=False)
        return api, character_run, interaction_run, commanders, interactions

    async def test_catalogs_hold_parent_and_do_not_validate_interaction_list(self):
        api, _, interaction_run, commanders, interactions = await self.catalogs()
        self.assertEqual(len(commanders), 1)
        self.assertEqual(len(interactions), 1)
        self.assertEqual(commanders[0]["id"], 73)
        self.assertFalse(interactions[0]["validity_checked"])
        self.assertFalse(any("IsCommandValid" in call for call in interaction_run.field_calls))
        self.assertEqual(commanders.active_leases, 1)
        await interactions.close()
        await commanders.close()
        self.assertTrue(interactions.closed)
        self.assertTrue(commanders.closed)
        self.assertEqual(api._ctx.perform_calls, [])

    async def test_selected_interaction_invokes_exact_condition_and_action_once(self):
        api, _, _, commanders, interactions = await self.catalogs()
        captured = []
        async def selected(api_arg, label, expression, bindings, *, condition, namespace, perform):
            run = Run(api_arg)
            actual = await bindings(run)
            captured.append((label, expression, condition, namespace, perform, actual))
            run.status = "complete"
            return {"status": "invoked", "invoked": True, "accepted": True,
                    "native_error": 0, "acceptance_scope": "property callback"}
        with patch("pdx_runtime.v3_game._political_actions.selected_property_action", selected):
            result = await warcareer.invoke_commander_interaction(api, commanders[0],
                interactions[0], readback=False, perform=False)
        self.assertTrue(result["invoked"])
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][1], "CharacterInteraction.ExecuteCommand(Character.Self)")
        self.assertEqual(captured[0][2], "CharacterInteraction.IsCommandValid(Character.Self)")
        self.assertFalse(captured[0][4])
        self.assertEqual(captured[0][5][0].object, 31)
        self.assertEqual(captured[0][5][1].object, 21)
        await interactions.close(); await commanders.close()

    async def test_retirement_effect_comes_from_state_change_not_callback_acceptance(self):
        api, _, _, commanders, interactions = await self.catalogs()
        states = iter(({"retired": False}, {"retired": True}))
        async def observe(*args): return next(states)
        async def selected(api_arg, label, expression, bindings, **kwargs):
            run = Run(api_arg); await bindings(run); run.status = "complete"
            return {"status": "invoked", "invoked": True, "accepted": False}
        with patch.object(warcareer, "_commander_state", observe),                 patch("pdx_runtime.v3_game._political_actions.selected_property_action", selected):
            result = await warcareer.invoke_commander_interaction(api, commanders[0], interactions[0],
                perform=False)
        self.assertFalse(result["accepted"])
        self.assertTrue(result["retirement_state_changed"])
        self.assertTrue(result["business_effect_observed"])
        await interactions.close(); await commanders.close()

    async def test_wrong_parent_interaction_is_rejected_before_native_work(self):
        api, _, _, commanders, interactions = await self.catalogs()
        other = await self.catalogs()
        with self.assertRaises(ValueError):
            await warcareer.invoke_commander_interaction(api, commanders[0], other[4][0],
                readback=False, perform=False)
        await interactions.close(); await commanders.close()
        await other[4].close(); await other[3].close()

    async def test_shared_native_predicate_and_property_callback_execute_once(self):
        api, _, _, commanders, interactions = await self.catalogs()
        condition = NS(error=0, kind=data.Kind.BOOL, value=True,
                       action=data.Action.EVALUATE, value_handle=0)
        invocation = NS(error=122, invoked=1, accepted=1,
                        result=NS(action=data.Action.INVOKE_ACTION, value_handle=0))
        with patch.object(data, "evaluate", return_value=condition) as evaluate, \
                patch.object(data, "invoke_property_action", return_value=invocation) as invoke:
            result = await warcareer.invoke_commander_interaction(api, commanders[0],
                interactions[0], readback=False, perform=False)
        self.assertEqual((evaluate.call_count, invoke.call_count), (1, 1))
        self.assertEqual(evaluate.call_args.args[1],
                         "CharacterInteraction.IsCommandValid(Character.Self)")
        self.assertEqual(invoke.call_args.args[1],
                         "CharacterInteraction.ExecuteCommand(Character.Self)")
        self.assertEqual([binding.object for binding in invoke.call_args.args[2]], [31, 21])
        self.assertIs(result["invocation"], invocation)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["native_error"], 122)
        self.assertIsNone(result["business_effect_observed"])
        self.assertFalse(result["completed"])
        self.assertEqual(api._ctx.perform_calls, [])
        self.assertEqual(commanders.active_leases, 1)  # interaction source still holds parent
        self.assertEqual(interactions.active_leases, 0)
        await interactions.close(); await commanders.close()

    async def test_shared_false_condition_never_invokes_or_retries_callback(self):
        api, _, _, commanders, interactions = await self.catalogs()
        condition = NS(error=0, kind=data.Kind.BOOL, value=False,
                       action=data.Action.EVALUATE, value_handle=0)
        with patch.object(data, "evaluate", return_value=condition) as evaluate, \
                patch.object(data, "invoke_property_action") as invoke:
            result = await warcareer.invoke_commander_interaction(api, commanders[0],
                interactions[0], readback=False, perform=False)
        self.assertEqual(evaluate.call_count, 1)
        invoke.assert_not_called()
        self.assertFalse(result["invoked"])
        self.assertFalse(result["accepted"])
        self.assertIsNone(result["business_effect_observed"])
        await interactions.close(); await commanders.close()

    async def test_nonretirement_career_effect_is_observed_from_existing_state_reads(self):
        api, _, _, commanders, interactions = await self.catalogs()
        states = iter(({"retired": False, "in_exile_pool": False, "has_formation": True},
                       {"retired": False, "in_exile_pool": True, "has_formation": False}))
        async def observe(*args): return next(states)
        async def selected(api_arg, label, expression, bindings, **kwargs):
            run = Run(api_arg); await bindings(run); run.status = "complete"
            return {"status": "invoked", "invoked": True, "accepted": True}
        with patch.object(warcareer, "_commander_state", observe), \
                patch("pdx_runtime.v3_game._political_actions.selected_property_action", selected):
            result = await warcareer.invoke_commander_interaction(api, commanders[0],
                interactions[0], perform=False)
        self.assertFalse(result["retirement_state_changed"])
        self.assertTrue(result["business_effect_observed"])
        self.assertEqual(result["observed_state_changes"], {
            "in_exile_pool": {"before": False, "after": True},
            "has_formation": {"before": True, "after": False}})
        self.assertIn("not all possible", result["observation_scope"])
        await interactions.close(); await commanders.close()

    async def test_failed_readback_keeps_original_invocation_and_never_replays(self):
        api, _, _, commanders, interactions = await self.catalogs()
        failure = RuntimeError("post-action observation failed")
        calls = []
        async def observe(*args):
            if calls: raise failure
            return {"retired": False}
        async def selected(api_arg, label, expression, bindings, **kwargs):
            run = Run(api_arg); await bindings(run); run.status = "complete"
            calls.append(expression)
            return {"status": "invoked", "invoked": True, "accepted": True,
                    "native_error": 0}
        with patch.object(warcareer, "_commander_state", observe), \
                patch("pdx_runtime.v3_game._political_actions.selected_property_action", selected):
            result = await warcareer.invoke_commander_interaction(api, commanders[0],
                interactions[0], perform=False)
        self.assertEqual(len(calls), 1)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["native_error"], 0)
        self.assertIs(result["readback_error"], failure)
        self.assertIsNone(result["business_effect_observed"])
        await interactions.close(); await commanders.close()
