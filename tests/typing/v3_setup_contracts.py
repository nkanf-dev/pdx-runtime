"""Static Setup public API usage. No connection or game calls run here."""
from typing import assert_type
from pdx_runtime.v3_game import V3Game
from pdx_runtime.query_executor import OperationResult
from pdx_runtime.v3_game.setup import SetupAction, SetupAPI, SetupPage

async def contracts(game: V3Game, api: SetupAPI) -> None:
    assert_type(game.setup, SetupAPI)
    rules = await api.rules()
    assert_type(rules, OperationResult[SetupPage])
    rule_ui_state = await api.rule_ui_state()
    assert_type(rule_ui_state, OperationResult[SetupPage])
    presets = await api.presets()
    assert_type(presets, OperationResult[SetupPage])
    draft = await api.draft()
    assert_type(draft, OperationResult[SetupPage])
    global_ironman = await api.ironman()
    assert_type(global_ironman, OperationResult[SetupPage])
    global_ironman_set = await api.set_ironman(False)
    assert_type(global_ironman_set, OperationResult[SetupAction])
    if isinstance(global_ironman_set, dict):
        assert_type(global_ironman_set["requested_enabled"], bool)
    staged = await api.set_preset_name("(original) O'Brien")
    assert_type(staged, OperationResult[SetupAction])
    saved = await api.save_preset()
    assert_type(saved, OperationResult[SetupAction])
    removed = await api.delete_selected_preset()
    assert_type(removed, OperationResult[SetupAction])
    removed_name = await api.delete_draft_named_preset()
    assert_type(removed_name, OperationResult[SetupAction])

    if isinstance(presets, SetupPage):
        assert_type(presets.has_more, bool | None)
    stored_settings = await api.preset_settings(0, start=0, limit=64)
    assert_type(stored_settings, OperationResult[SetupPage])
    if isinstance(stored_settings, SetupPage):
        assert_type(stored_settings.requested_preset_index, int | None)
        assert_type(stored_settings.requested_rule_index, int | None)

    from pdx_runtime import v3_data as data
    from pdx_runtime.v3_game.setup import PresetApplyResult
    applied = await game.setup.apply_preset(0)
    assert_type(applied, OperationResult[PresetApplyResult])
    if isinstance(applied, dict):
        assert_type(applied["native_result"], data.ElementResult)

    from pdx_runtime import v3_access as access
    from pdx_runtime.v3_game.setup import PresetNamedResult
    named_save = await game.setup.save_preset_as("O'Brien")
    assert_type(named_save, OperationResult[PresetNamedResult])
    named_delete = await api.delete_preset_named("old name")
    assert_type(named_delete, OperationResult[PresetNamedResult])
    if isinstance(named_save, dict):
        assert_type(named_save["native_result"], access.AccessResult)
        assert_type(named_save["snapshot_attempts"], list[dict[str, object]])
        assert_type(named_save["complete"], bool)

    choices = await api.rule_choices(0, start=0, limit=64, include_achievement_flags=True)
    assert_type(choices, OperationResult[SetupPage])
    if isinstance(choices, SetupPage):
        assert_type(choices.requested_rule_index, int | None)

    applied_rules = await api.apply_rules()
    assert_type(applied_rules, OperationResult[SetupAction])
    reset_rules = await api.reset_rules_to_defaults()
    assert_type(reset_rules, OperationResult[SetupAction])
    draft_ironman = await api.set_draft_ironman(False)
    assert_type(draft_ironman, OperationResult[SetupAction])
    if isinstance(draft_ironman, dict):
        assert_type(draft_ironman["requested_enabled"], bool)
        assert_type(draft_ironman["native_result"], data.ActionResult)

    from pdx_runtime.v3_game.setup import RuleSetResult
    selected_rule = await game.setup.set_rule("ai_behavior", "actual_setting_key", output_bytes=65536)
    assert_type(selected_rule, OperationResult[RuleSetResult])
    if isinstance(selected_rule, dict):
        assert_type(selected_rule["native_result"], access.AccessResult)


    # Canonical rule keys are copied cells, not typed stable row IDs or setters.
    for observed in (rules, choices, stored_settings):
        if isinstance(observed, SetupPage):
            assert_type(observed.rows[0].cells["rule_key"], data.ProjectionCell)
            assert_type(observed.rows[0].cells["rule_key"].snapshot, bytes)
            assert_type(observed.rows[0].values["rule_key"], object)

    if isinstance(saved, dict):
        assert_type(saved["native_result"], data.ActionResult)
        assert_type(saved["snapshot_attempts"], list[dict[str, object]])
        assert_type(saved["snapshot_error"], Exception)
        assert_type(saved["complete"], bool)
