"""Original frontend game-rule and preset operations on the supplied session.

Reads use the shared copied-page callback. Actions use original native data
handlers. A native action receipt reports dispatch, not an observed effect or
finished preset-file I/O. Current-draft methods keep their original single-action
semantics. Named save/delete methods edit then consume the draft in one callback.
They do not roll back an edit or later native effects when a later step fails.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from .. import v3_access as access, v3_data as data
from ..query_executor import OperationResult
from ._projection import _projection_page, _projection_value
from ._run import rejected
from . import _run

if TYPE_CHECKING:
    from ._core import GameContext

__all__ = ["SetupAPI", "SetupPage", "SetupRow", "SetupAction", "PresetApplyResult",
           "PresetNamedResult", "RuleSetResult"]

_RULE_FIELDS = (
    ("rule_name", "GuiGameRule.GetRule.GetName"),
    ("setting_key", "GuiGameRule.GetSetting.GetKey"),
    ("setting_name", "GuiGameRule.GetSetting.GetName"),
    ("description", "GuiGameRule.GetSetting.GetDescription"),
    ("recommendation", "GuiGameRule.GetSetting.GetDescriptionWithObjectiveRecommendation"),
    ("rule_key", data.NativeField(data.NativeFieldKind.GAME_RULE_KEY)),
)
_RULE_CHOICE_FIELDS = (
    ("rule_name", "GameRuleSetting.GetGameRule.GetName"),
    ("setting_key", "GameRuleSetting.GetKey"),
    ("setting_name", "GameRuleSetting.GetName"),
    ("description", "GameRuleSetting.GetDescription"),
    ("recommendation", "GameRuleSetting.GetDescriptionWithObjectiveRecommendation"),
    ("rule_key", data.NativeField(data.NativeFieldKind.GAME_RULE_KEY)),
)
_PRESET_FIELDS = (
    ("name", "GuiGameRulePreset.GetName"),
    ("ironman", "GuiGameRulePreset.IsIronmanEnabled"),
)
_DRAFT_FIELDS = (
    ("name", "GetGameRules.GetPresetSaveName"),
    ("selected_index", "GetGameRules.GetSelectedPresetIndex"),
    ("valid_selection", "GetGameRules.HasValidPresetSelected"),
    ("ironman", "GetGameRules.IsIronman"),
    ("valid_name", "GetGameRules.IsValidPresetName"),
    ("name_validation_text", "GetGameRules.GetInvalidPresetNameTooltip"),
)
_IRONMAN_FIELDS = (("enabled", "IsIronmanEnabled"),)
_RULE_UI_FIELDS = (
    ("is_host", "IsHost"),
    ("is_in_game", "IsInGame"),
    ("rule_arrows_condition", "And( IsHost, Not( IsInGame ) )"),
    ("multiplayer", "GameHasMultiplePlayers"),
    ("preset_strip_condition", "Not( GameHasMultiplePlayers )"),
    ("preset_popup_shown", "GetGameRules.IsPresetWindowShown"),
)


@dataclass(frozen=True)
class SetupRow:
    """Copied fields at a current native provider ordinal, not a stable ID."""
    source_index: int
    cells: dict[str, data.ProjectionCell]

    @property
    def values(self) -> dict[str, object]:
        return {name: _projection_value(cell)[0] for name, cell in self.cells.items()}

    @property
    def field_issues(self) -> dict[str, str]:
        issues = {}
        for name, cell in self.cells.items():
            reason = _projection_value(cell)[1]
            if reason is not None:
                issues[name] = reason
        return issues


@dataclass(frozen=True)
class SetupPage:
    """One native copied observation with its errors, bytes and source count.

    The runtime must support the requested app-owned source in its current
    phase. Native world0/not-ready results are returned unchanged. No world,
    country, source ownership or empty-catalog conclusion is fabricated.
    """
    source: str
    field_names: tuple[str, ...]
    native_page: data.ProjectionPage
    requested_rule_index: int | None = None
    requested_preset_index: int | None = None

    @property
    def rows(self) -> tuple[SetupRow, ...]:
        page = self.native_page
        grouped: dict[int, dict[str, data.ProjectionCell]] = {
            index: {} for index in range(page.start, page.next)}
        for cell in page.cells:
            row = grouped.setdefault(cell.row, {})
            if 0 <= cell.field < len(self.field_names):
                row[self.field_names[cell.field]] = cell
        return tuple(SetupRow(index, cells) for index, cells in sorted(grouped.items()))

    @property
    def next(self) -> int:
        return self.native_page.next

    @property
    def has_more(self) -> bool | None:
        """Native continuation, including unknown for an incomplete grouped page."""
        return self.native_page.has_more

    @property
    def enumeration_complete(self) -> bool:
        """All current source rows fit this one start0 page; not all rule data."""
        page = self.native_page
        return (page.error == 0 and page.count_valid and page.start == 0
                and page.has_more is False and page.row_count == page.source_count)


class SetupAction(TypedDict, total=False):
    """Original action receipt with additive post-input snapshot diagnostics.

    ``complete`` concerns snapshot bytes only, not acceptance or effects. A
    ``snapshot_error`` does not replace the original ``native_result``.
    """
    action: str
    requested_name: str
    requested_enabled: bool
    native_result: data.ActionResult
    snapshot_attempts: list[dict[str, object]]
    complete: bool
    snapshot_error: Exception
    cleanup: dict[str, object]


class PresetApplyResult(TypedDict, total=False):
    """Original scoped preset selection receipt, not proof of full rule effects."""
    action: str
    source_index: int
    native_result: data.ElementResult
    cleanup: dict[str, object]


class PresetNamedResult(TypedDict, total=False):
    """Ordered original receipts. An edit can survive a later save/delete failure.

    ``complete`` describes post-input snapshot copies only. It is not action,
    effect or file-persistence completion. ``native_result`` is never rewritten.
    """
    action: str
    requested_name: str
    native_result: access.AccessResult
    snapshot_attempts: list[dict[str, object]]
    complete: bool
    cleanup: dict[str, object]


class RuleSetResult(TypedDict, total=False):
    """Exact scoped rule-selection receipt, separate from any global apply."""
    action: str
    requested_rule_key: str
    requested_setting_key: str
    native_result: access.AccessResult
    cleanup: dict[str, object]


def _preset_name(name: str) -> None:
    if not isinstance(name, str):
        raise TypeError("name must be text")
    if "\0" in name:
        raise ValueError("the native preset-name edit handler truncates embedded NUL")
    name.encode("utf-8")


def _rule_fields(*, choices: bool, include_achievement_flags: bool
                 ) -> tuple[tuple[str, str | data.NativeField], ...]:
    if type(include_achievement_flags) is not bool:
        raise TypeError("include_achievement_flags must be bool")
    fields = _RULE_CHOICE_FIELDS if choices else _RULE_FIELDS
    if not include_achievement_flags:
        return fields
    # The argument is the fixed stock GUI flag, never user expression syntax.
    expression = ("GameRuleSetting.HasFlag( 'blocks_achievements' )" if choices else
                  "GuiGameRule.GetSetting.HasFlag( 'blocks_achievements' )")
    return fields + (("blocks_achievements", expression),)


def _page_bounds(start: int, limit: int) -> None:
    if type(start) is not int or not 0 <= start <= 0x7fffffff:
        raise ValueError("start must be a nonnegative native int32 source index")
    if type(limit) is not int or not 1 <= limit <= 64:
        raise ValueError("limit must be between 1 and 64 copied rows")


class SetupAPI:
    """Frontend rule observations and explicit native preset actions."""
    def __init__(self, ctx: GameContext) -> None:
        self._ctx = ctx

    async def _page(self, source, fields, *, expression=None, start=0, limit=64,
                    steps=(), requested_rule_index=None, requested_preset_index=None) -> SetupPage:
        field_names = tuple(name for name, _ in fields)
        try:
            page = await _projection_page(self._ctx, "setup." + source,
                tuple(expression for _, expression in fields), ((),),
                source_expression=expression, start=start, limit=limit, steps=steps)
        except Exception as error:
            partial = getattr(getattr(error, "query", None), "partial_result", None)
            if isinstance(partial, data.ProjectionPage):
                setattr(error, "setup_page", SetupPage(source, field_names, partial,
                    requested_rule_index, requested_preset_index))
            raise
        return SetupPage(source, field_names, page, requested_rule_index, requested_preset_index)

    async def rules(self, *, start: int = 0, limit: int = 64,
                    include_achievement_flags: bool = False) -> OperationResult[SetupPage]:
        """Copy canonical rule keys and current draft setting names, keys and text.

        rule_key is the original parent rule definition's key, not a setting key
        or display name. Defaults, editability and applied campaign rules are
        not inferred from this frontend draft observation. Pass
        include_achievement_flags=True to append the original setting-level
        blocks_achievements flag in the same page. That flag is not a global
        achievement-eligibility verdict. The existing six columns stay first.
        """
        _page_bounds(start, limit)
        fields = _rule_fields(choices=False, include_achievement_flags=include_achievement_flags)
        return await self._ctx.perform("setup.rules", lambda: self._page("rules", fields,
            expression="GetGameRules.GetSettings", start=start, limit=limit))

    async def rule_ui_state(self) -> OperationResult[SetupPage]:
        """Copy shared original rule-window conditions and their native inputs.

        rule_arrows_condition is the stock Previous/Next visibility expression.
        It describes ordinary row controls relative to an existing window/row,
        not per-rule backend authorization or proof that a control is rendered.
        The separate preset strip condition and popup observation keep their own
        scopes. No condition gates set_rule, Apply, preset or ironman actions.

        All six leaves are copied in one ordinary callback. They are separate
        native observations, not a coherent state snapshot. Original false,
        zero, None, getter errors and partial pages remain unchanged. Conditions
        are evaluated by the original native expressions, not recomputed from
        the copied input flags. This call does not open or mutate the window.
        """
        return await self._ctx.perform("setup.rule_ui_state", lambda: self._page(
            "rule_ui_state", _RULE_UI_FIELDS, limit=1))

    async def rule_choices(self, index: int, *, start: int = 0, limit: int = 64,
                           include_achievement_flags: bool = False) -> OperationResult[SetupPage]:
        """Copy canonical settings for a current native rule ordinal in one callback.

        The original rule row and its definition-owned GameRule.GetSettings
        Creation remain scoped until every key/name/text leaf is copied. No raw
        const setting pointer is returned or retained. Both rule and choice
        ordinals refer to current source positions, not stable IDs. The copied
        parent rule name and canonical rule_key belong to this observation, not
        an earlier rules page. rule_key is not the choice's setting_key.
        This lists canonical choices; it does not prove defaults, lock policy,
        current selection, rule authorization or effects of changing a rule.
        Pass include_achievement_flags=True to also copy each choice's original
        blocks_achievements flag beside its description in this same callback.
        False, unavailable and getter errors stay distinct; the flag alone is
        not a global achievement-eligibility verdict.
        """
        if type(index) is not int or not 0 <= index <= 0x7fffffff:
            raise ValueError("index must be a nonnegative native int32 source ordinal")
        _page_bounds(start, limit)
        fields = _rule_fields(choices=True, include_achievement_flags=include_achievement_flags)
        return await self._ctx.perform("setup.rule_choices", lambda: self._page(
            "rule_choices", fields, expression="GuiGameRule.GetRule.GetSettings",
            steps=(data.CreationStep("GetGameRules.GetSettings", index),),
            start=start, limit=limit, requested_rule_index=index))

    async def set_rule(self, rule_key: str, setting_key: str, *, output_bytes: int = 65536
                       ) -> OperationResult[RuleSetResult]:
        """Select an actual canonical setting key through original Next/Prev.

        Both keys are explicit-length CString data, not display names, ordinals
        or expression syntax. Empty strings, embedded NUL and quotes are passed
        unchanged. The native callback resolves one current row, reads its real
        canonical choices and current setting, then takes a bounded original
        Next/Prev path within that same borrowed scope. No borrowed row or setting
        pointer escapes. There is no host, in-game, lock or UI-visibility gate.

        Inspect native_result.callee_result for the original observations and
        every copied action receipt. Null/unobserved rows, zero choices, absent
        targets, nonmember current choices and already-selected outcomes do not
        fabricate accepted or an error. Earlier choice changes survive a later
        failure; no rollback or automatic mutation replay occurs. output_bytes
        controls copied output capacity, not a gameplay step limit. A partial
        output is returned unchanged. Its complete flag means copy integrity,
        not successful selection. apply_rules() is separate and never implicit.
        """
        if not isinstance(rule_key, str) or not isinstance(setting_key, str):
            raise TypeError("rule_key and setting_key must be strings")
        rule_key.encode("utf-8")
        setting_key.encode("utf-8")
        if type(output_bytes) is not int or not 0 <= output_bytes <= 0xffffffff:
            raise ValueError("output_bytes must fit uint32")

        async def select() -> RuleSetResult:
            run = _run.Run(self._ctx, "setup.set_rule", namespace="setup")
            result: RuleSetResult = {"action": "set_rule", "requested_rule_key": rule_key,
                "requested_setting_key": setting_key}
            try:
                result["native_result"] = await run.capture("set_rule", lambda:
                    data.select_game_rule(run.native, rule_key, setting_key,
                        output_bytes=output_bytes, adapter=run.adapters["data"],
                        access_adapter=run.adapters["access"], process=run.process))
            except BaseException as error:
                partial = getattr(error, "partial_result", None)
                if partial is not None:
                    result["native_result"] = partial
                setattr(error, "setup_result", result)
                raise
            return await run.finish(result)

        return await self._ctx.perform("setup.set_rule", select)

    async def presets(self, *, start: int = 0, limit: int = 64) -> OperationResult[SetupPage]:
        """Read original preset names and ironman values; ordinals are page locations."""
        _page_bounds(start, limit)
        return await self._ctx.perform("setup.presets", lambda: self._page("presets", _PRESET_FIELDS,
            expression="GetGameRules.GetRulePresets", start=start, limit=limit))

    async def preset_settings(self, index: int, *, start: int = 0, limit: int = 64,
                              include_achievement_flags: bool = False) -> OperationResult[SetupPage]:
        """Copy the actual stored settings of one current native preset.

        A fresh preset Creation row and its original stored array remain scoped
        through all copied setting keys, names, parent rule keys/names and text.
        rule_key is copied from the original parent rule definition. This does
        not select or apply the preset, edit a draft, or look up guessed rule
        keys. A setting key is not its parent rule's key. Defaults and locks are
        not inferred. requested_preset_index and row ordinals describe this
        observation, not stable identities. Pages are not atomic across calls.

        Native source errors, counts and partial bytes remain unchanged. On a
        copy exception, the original query and its partial_result stay reachable;
        setup_page adds a view of that same partial page when one was copied.
        include_achievement_flags=True appends the original setting-level
        blocks_achievements flag without selecting or applying the preset.
        """
        if type(index) is not int or not 0 <= index <= 0x7fffffff:
            raise ValueError("index must be a nonnegative native int32 source ordinal")
        _page_bounds(start, limit)
        fields = _rule_fields(choices=True, include_achievement_flags=include_achievement_flags)
        async def read() -> SetupPage:
            return await self._page("preset_settings", fields,
                expression=data.NativeSource(data.NativeSourceKind.PRESET_STORED_SETTINGS),
                steps=(data.CreationStep("GetGameRules.GetRulePresets", index),),
                start=start, limit=limit, requested_preset_index=index)
        return await self._ctx.perform("setup.preset_settings", read)

    async def draft(self) -> OperationResult[SetupPage]:
        """Read the current name, selection, ironman and native name-validity text."""
        return await self._ctx.perform("setup.draft",
            lambda: self._page("draft", _DRAFT_FIELDS, limit=1))

    async def ironman(self) -> OperationResult[SetupPage]:
        """Copy the original V3 global getter result, not the Jomini draft value.

        The native getter can initialize its accessor object and can return
        false through an internal guard. Its result is not a raw storage-byte
        read. The stock GUI uses this getter for its global enable/disable
        controls. Native false, errors and partial outcomes stay unchanged.
        This does not infer achievement eligibility or gate a setter.
        """
        return await self._ctx.perform("setup.ironman", lambda: self._page(
            "ironman", _IRONMAN_FIELDS, limit=1))

    async def set_ironman(self, enabled: bool) -> OperationResult[SetupAction]:
        """Invoke the stock V3 global ironman action with a fixed bool FORMAL.

        This uses SetIronmanEnabledStatus, not GetGameRules.SetIronman. It does
        not select/apply/save a preset or emulate the GUI visibility condition.
        requested_enabled is caller intent. The original handler can finish
        without writing after its own native started-game check. Its handled
        receipt is not proof of a flag change, achievements or persistence.
        """
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        expression = ("SetIronmanEnabledStatus('(bool)yes')" if enabled
                      else "SetIronmanEnabledStatus('(bool)no')")
        return await self._ctx.perform("setup.set_ironman", lambda: self._action(
            "set_ironman", expression, requested_enabled=enabled))

    async def _action(self, action: str, expression: str, *,
                      input: data.TypedInput | None = None, requested_name: str | None = None,
                      requested_enabled: bool | None = None) -> SetupAction:
        run = _run.Run(self._ctx, "setup." + action, namespace="setup")
        result: SetupAction = {"action": action}
        if requested_name is not None:
            result["requested_name"] = requested_name
        if requested_enabled is not None:
            result["requested_enabled"] = requested_enabled
        def invoke():
            options = {"adapter": run.adapters["data"], "process": run.process}
            if input is None:
                return data.invoke_property_action(run.native, expression, **options)
            return data.invoke_action(run.native, expression, input, **options)
        try:
            original = await run.capture(action, invoke)
            result["native_result"] = original
        except BaseException as error:
            setattr(error, "setup_result", result)
            raise
        try:
            result.update(await run.complete_action_inputs(original))
        except Exception as error:
            # The action is already observed. A supplemental copy failure must
            # not replace its receipt or invoke that action again. The shared
            # run retains the original snapshot query/Any when it is unsettled.
            completion = getattr(error, "snapshot_completion", None)
            if completion is not None:
                result.update(completion)
            result["complete"] = False
            result["snapshot_error"] = error
            self._ctx.diagnostics.append({"label": "setup." + action + ".post_input_snapshot",
                "native_result": original, "error": error, "snapshot_completion": completion})
        except BaseException as error:
            setattr(error, "setup_result", result)
            raise
        return await run.finish(result)

    async def set_preset_name(self, name: str) -> OperationResult[SetupAction]:
        """Edit the draft name through the original CUTF8String text-edit handler.

        Quotes, backslashes and converter-shaped prefixes are passed as data,
        never expression syntax. Embedded NUL is rejected because this native
        setter copies only the prefix before NUL. This call does not save/delete.
        """
        _preset_name(name)
        return await self._ctx.perform("setup.set_preset_name", lambda: self._action(
            "set_preset_name", "GetGameRules.OnPresetSaveNameEdit",
            input=data.TypedInput.utf8_string(name), requested_name=name))

    async def save_preset(self) -> OperationResult[SetupAction]:
        """Save/overwrite the current draft name, rule choices and ironman state.

        The original action updates the in-memory preset collection and requests
        asynchronous persistence. Its receipt does not confirm finished disk I/O.
        The name is the current native draft at invocation, not an earlier read.
        """
        return await self._ctx.perform("setup.save_preset", lambda: self._action(
            "save_preset", "GetGameRules.SavePreset"))

    async def reset_rules_to_defaults(self) -> OperationResult[SetupAction]:
        """Reset current draft rule choices through the original native resolver.

        The native operation resolves defaults for the current rule rows, clears
        the selected preset and rebuilds draft rows from valid returned settings.
        Conditional defaults and validity fallbacks remain native behavior. The
        draft ironman value is unchanged. Applying rules and saving presets are
        separate operations. This mutates the draft; it is not a default query.
        The original receipt is not proof of later effects or persistence.
        """
        return await self._ctx.perform("setup.reset_rules_to_defaults", lambda: self._action(
            "reset_rules_to_defaults", "GetGameRules.ResetAllToDefaults"))

    async def set_draft_ironman(self, enabled: bool) -> OperationResult[SetupAction]:
        """Set the Jomini frontend draft ironman value, not V3's global toggle.

        The fixed native expression supplies a genuine Boolean FORMAL parameter
        through the original compiler-owned reference frame. It is not an input
        Any setter or user-provided expression. Applying rules and saving presets
        stay separate. requested_enabled records caller intent; native_result is
        the unchanged original receipt, not an observed draft or global effect.
        """
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        expression = ("GetGameRules.SetIronman('(bool)yes')" if enabled
                      else "GetGameRules.SetIronman('(bool)no')")
        return await self._ctx.perform("setup.set_draft_ironman", lambda: self._action(
            "set_draft_ironman", expression, requested_enabled=enabled))

    async def apply_rules(self) -> OperationResult[SetupAction]:
        """Apply the current frontend rule choices and ironman state explicitly.

        Original ApplySettings writes the applied/global manager and requests
        asynchronous persistence. It is separate from a draft choice change or
        preset save. No host, in-game or UI-visibility gate is added. The original
        receipt does not prove later effects or completed persistence.
        """
        return await self._ctx.perform("setup.apply_rules", lambda: self._action(
            "apply_rules", "GetGameRules.ApplySettings"))

    async def delete_selected_preset(self) -> OperationResult[SetupAction]:
        """Request removal of the currently selected preset through its native index.

        The selected int32 index is read and consumed in the same native action
        callback. The original delete method checks its range. No raw ordinal is
        reused from an earlier Python catalog, and no preset is first applied.
        Persistence uses the original asynchronous collection-save operation.
        """
        return await self._ctx.perform("setup.delete_selected_preset", lambda: self._action(
            "delete_selected_preset",
            "GetGameRules.DeletePresetAtIndex(GetGameRules.GetSelectedPresetIndex)"))

    async def delete_draft_named_preset(self) -> OperationResult[SetupAction]:
        """Request removal of the first preset matching the current draft name.

        The original native name getter and delete consumer run in one callback.
        This method does not change the draft or apply a preset. A prior name edit
        is a separate operation and can be changed by another UI/agent action.
        """
        return await self._ctx.perform("setup.delete_draft_named_preset", lambda: self._action(
            "delete_draft_named_preset",
            "GetGameRules.DeletePresetWithName(GetGameRules.GetPresetSaveName)"))

    async def _named_preset(self, action: str, name: str, consumer: str) -> PresetNamedResult:
        run = _run.Run(self._ctx, "setup." + action, namespace="setup")
        result: PresetNamedResult = {"action": action, "requested_name": name}
        steps = (
            data.ActionStep("GetGameRules.OnPresetSaveNameEdit", data.TypedInput.utf8_string(name),
                data.ActionContinuation.INVOKED_AND_ACCEPTED),
            data.ActionStep(consumer, data.TypedInput.empty(), data.ActionContinuation.ALWAYS),
        )
        try:
            original = await run.capture(action, lambda: data.invoke_action_sequence(
                run.native, steps, adapter=run.adapters["data"],
                access_adapter=run.adapters["access"], process=run.process))
            result["native_result"] = original
            result.update(await run.complete_action_inputs(original))
        except BaseException as error:
            partial = getattr(error, "partial_result", None)
            if partial is not None:
                result["native_result"] = partial
            completion = getattr(error, "snapshot_completion", None)
            if completion is not None:
                result.update(completion)
            setattr(error, "setup_result", result)
            raise
        return await run.finish(result)

    async def save_preset_as(self, name: str) -> OperationResult[PresetNamedResult]:
        """Edit the draft name then save its current rules in one native callback.

        Save runs only if the original edit was invoked and accepted, including
        an accepted edit with a later copy error. Every original step receipt is
        kept in native_result.callee_result. Supplemental input snapshots stay
        separate. An edit can remain when save fails; there is no rollback.
        The save receipt does not confirm completed asynchronous file I/O.
        """
        _preset_name(name)
        return await self._ctx.perform("setup.save_preset_as", lambda: self._named_preset(
            "save_preset_as", name, "GetGameRules.SavePreset"))

    async def delete_preset_named(self, name: str) -> OperationResult[PresetNamedResult]:
        """Edit the draft name then delete its first native match in one callback.

        The original name getter supplies DeletePresetWithName after an invoked
        and accepted edit. This changes the draft even if deletion later fails
        or finds no match. It does not apply the preset or restore the old name.
        Original receipts and partial results remain visible. Persistence is
        asynchronous; the receipt does not confirm completed file I/O.
        """
        _preset_name(name)
        return await self._ctx.perform("setup.delete_preset_named", lambda: self._named_preset(
            "delete_preset_named", name,
            "GetGameRules.DeletePresetWithName(GetGameRules.GetPresetSaveName)"))

    async def apply_preset(self, index: int) -> OperationResult[PresetApplyResult]:
        """Apply a current native preset ordinal through its original root action.

        The original preset Creation, count, selected element and OnSelectPreset
        share one Interface/Access callback. Native code derives the exact I32
        from that selected ordinal. No earlier Python count or cached row pointer
        is used. An ordinal selects the current source; it is not a stable ID.
        The original native action decides rule locks and changes. Its complete
        ElementResult stays visible, including errors after accepted invocation.
        This does not save a preset, confirm disk persistence or roll back effects.
        """
        if type(index) is not int or not 0 <= index <= 0x7fffffff:
            raise ValueError("index must be a nonnegative native int32 source ordinal")

        async def apply() -> PresetApplyResult:
            run = _run.Run(self._ctx, "setup.apply_preset", namespace="setup")
            result: PresetApplyResult = {"action": "apply_preset", "source_index": index}
            try:
                result["native_result"] = await run.capture("apply_preset", lambda:
                    data.invoke_source_index_action(run.native, "GetGameRules.GetRulePresets",
                        index, "GetGameRules.OnSelectPreset", adapter=run.adapters["data"],
                        process=run.process))
            except BaseException as error:
                setattr(error, "setup_result", result)
                raise
            return await run.finish(result)

        return await self._ctx.perform("setup.apply_preset", apply)
