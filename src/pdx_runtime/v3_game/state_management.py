"""Copied state-management data and original state commands on one session."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, TypedDict

from .. import v3_commands as commands, v3_data as data
from ..query_executor import OperationResult
from pdx_runtime.v3_game.country.names import EDIT_NAMES
from ._projection import _projection_page, _projection_value
from ._run import KnownResultError, SourceUnavailable
from . import _run

if TYPE_CHECKING:
    from ._core import GameContext

__all__ = ["StateManagementAPI", "StateIncorporation", "IncorporationAction",
           "StateNames", "StateRenameResult"]

# Every expression is an original zero-argument State getter. Parent promotions
# compile and live inside the same Page callback as their copied scalar leaves.
_INCORPORATION_FIELDS = (
    ("id", "State.GetID"),
    ("name", "State.GetNameNoFormatting"),
    ("owner_country_id", "State.GetOwner.GetID"),
    ("incorporated", "State.IsIncorporated"),
    ("incorporating", "State.IsIncorporating"),
    ("unincorporated", "State.IsUnincorporated"),
    ("progress", "State.GetIncorporationProgress"),
    ("years_to_incorporate", "State.GetYearsToIncorporate"),
    ("time_left", "State.GetIncorporationTimeLeft"),
    ("time_description", "State.GetIncorporationTimeDesc"),
    ("partial_effects", "State.GetIncorporationPartialEffectsDesc"),
    ("base_bureaucracy_cost", "State.GetBaseBureaucracyCostRaw"),
    ("predicted_bureaucracy_cost", "State.PredictBureaucracyCost"),
    ("current_bureaucracy_cost", "State.GetBureaucracyCost"),
    ("bureaucracy_cost_description", "State.GetBureaucracyCostDesc"),
    ("owner_bureaucracy_balance", "State.GetOwner.GetPowerDelta('bureaucracy')"),
)

CopiedValue = bool | int | float | str | data.FixedPoint | None


@dataclass(frozen=True)
class StateIncorporation:
    """One requested state's copied fields and the entire original page.

    ``requested_state_id`` identifies the request; ``values["id"]`` is the
    observed native identity. Native zero, false, partial text and field errors
    remain visible. Missing fields mean that this callback did not copy them.
    ``complete`` covers this field set only, not all incorporation mechanics.
    Fixed-point values keep their original integer bits in data.FixedPoint.
    """

    requested_state_id: int
    page_row: int
    native_page: data.ProjectionPage

    @property
    def cells(self) -> dict[str, data.ProjectionCell]:
        return {_INCORPORATION_FIELDS[cell.field][0]: cell
                for cell in self.native_page.cells
                if cell.row == self.page_row and 0 <= cell.field < len(_INCORPORATION_FIELDS)}

    @property
    def values(self) -> dict[str, CopiedValue]:
        return {name: _projection_value(cell)[0] for name, cell in self.cells.items()}

    @property
    def field_issues(self) -> dict[str, str]:
        issues = {}
        for name, cell in self.cells.items():
            reason = _projection_value(cell)[1]
            if reason is not None:
                issues[name] = reason
        return issues

    @property
    def missing_fields(self) -> tuple[str, ...]:
        copied = self.cells
        return tuple(name for name, _ in _INCORPORATION_FIELDS if name not in copied)

    @property
    def complete(self) -> bool:
        return (self.native_page.error == 0 and self.native_page.count_valid
                and self.page_row < self.native_page.row_count
                and not self.missing_fields
                and all(not cell.error and not cell.binding_error and cell.complete
                        for cell in self.cells.values()))


def _state_id(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 0xffffffff:
        raise ValueError("state_id must be a native uint32 full ID")
    return value


class IncorporationAction(TypedDict, total=False):
    """Original command receipts; acceptance is not completion of incorporation."""
    action: str
    state_id: int
    world: int
    status: str
    accepted: bool | None
    eligible: bool | None
    validation: commands.CommandResult
    native_result: commands.CommandResult | data.DataResult
    native_error: int
    native_outcome: str | None
    reason: str | None
    operations: tuple[tuple[str, object], ...]
    cleanup: dict[str, object]


# Original native order: State followed by city/port/farm/mine/wood hubs.
_STATE_NAME_FIELDS = ("state", "city", "port", "farm", "mine", "wood")


@dataclass(frozen=True)
class StateNames:
    """Independent raw override reads, not one atomic six-field snapshot.

    Empty bytes are an unset override, not a localized display name. ``values``
    displays copied UTF-8 prefixes; each original result and every size-growth
    observation remain in ``attempts``. ``complete`` covers ``requested_fields``
    only; it does not prove a rename command has settled.
    """
    state_id: int
    attempts: dict[str, tuple[data.DataResult, ...]]
    requested_fields: tuple[str, ...] = _STATE_NAME_FIELDS

    @property
    def fields(self) -> dict[str, data.DataResult]:
        return {name: attempts[-1] for name, attempts in self.attempts.items() if attempts}

    @property
    def values(self) -> dict[str, str | None]:
        return {name: (field.snapshot.decode("utf-8", "replace")
                      if field.kind == data.Kind.STRING else None)
                for name, field in self.fields.items()}

    @property
    def complete(self) -> bool:
        fields = self.fields
        return (all(name in fields for name in self.requested_fields)
                and all(field.error == 0 and field.kind == data.Kind.STRING
                        and field.snapshot_bytes == field.total for field in fields.values()))


class StateRenameResult(TypedDict, total=False):
    """Native draft receipts and one original command, separate from its effect."""
    action: str
    state_id: int
    requested: dict[str, str]
    before: StateNames
    desired: dict[str, bytes]
    edits: dict[str, data.ActionResult]
    context: data.DataResult
    world: int
    status: str
    accepted: bool | None
    eligible: bool | None
    validation: commands.CommandResult
    native_result: commands.CommandResult | data.DataResult | data.ActionResult
    native_error: int
    native_outcome: str | None
    field: str
    reason: str | None
    operations: tuple[tuple[str, object], ...]
    cleanup: dict[str, object]


async def _read_state_names(run: Run, state_id: int,
                            requested_fields: tuple[str, ...] = _STATE_NAME_FIELDS) -> StateNames:
    attempts: dict[str, tuple[data.DataResult, ...]] = {}
    try:
        for index, field in enumerate(_STATE_NAME_FIELDS):
            if field not in requested_fields:
                continue
            capacity = 4096
            attempts[field] = ()
            while True:
                copied = await run.capture("names." + field,
                    lambda index=index, capacity=capacity: data.state_name_override(
                        run.native, state_id, index=index, output_bytes=capacity,
                        adapter=run.adapters["data"], process=run.process))
                attempts[field] += (copied,)
                # Only a completed read with an explicit larger native size is
                # repeated. Mutations and unresolved original queries are not.
                if copied.error != 234 or copied.total <= capacity:
                    break
                capacity = copied.total
    except BaseException as error:
        setattr(error, "state_names", StateNames(state_id, dict(attempts), requested_fields))
        raise
    return StateNames(state_id, attempts, requested_fields)


class StateManagementAPI:
    """State observations, incorporation and naming on the shared session."""

    def __init__(self, ctx: GameContext) -> None:
        self._ctx = ctx

    async def _incorporation(self, state_ids: tuple[int, ...]) -> tuple[StateIncorporation, ...]:
        if not state_ids:
            return ()
        state_type = await self._ctx.type_id("State")
        fields = tuple(expression for _, expression in _INCORPORATION_FIELDS)
        reports: list[StateIncorporation] = []
        for offset in range(0, len(state_ids), 64):
            chunk = state_ids[offset:offset + 64]
            bindings = tuple((data.ObjectBinding.state(state_type, state_id),) for state_id in chunk)
            try:
                page = await _projection_page(self._ctx, "state_management.incorporation",
                    fields, bindings, limit=len(chunk))
            except BaseException as error:
                # The shared executor keeps the original pending query. Store
                # only completed copied pages on this same exception.
                setattr(error, "completed_state_incorporations", tuple(reports))
                raise
            reports.extend(StateIncorporation(state_id, row, page)
                           for row, state_id in enumerate(chunk))
        return tuple(reports)

    async def incorporation(self, state_id: int) -> OperationResult[StateIncorporation]:
        """Read status, progress, timing, bureaucracy and original effect text.

        This is one native copied-page callback. The owner's bureaucracy balance
        is an observed resource amount, not an SDK affordability/permission rule.
        This does not start incorporation or replace native command validation.
        """
        identity = _state_id(state_id)
        async def read():
            return (await self._incorporation((identity,)))[0]
        return await self._ctx.perform("state_management.incorporation", read)

    async def incorporation_many(self, state_ids: Iterable[int]
                                 ) -> OperationResult[tuple[StateIncorporation, ...]]:
        """Read explicit states in batches of up to 64, preserving input order.

        Each page is one native observation; different pages are not atomic.
        If a later transport read raises, the original exception carries
        ``completed_state_incorporations`` from the earlier completed pages.
        """
        identities = tuple(_state_id(value) for value in state_ids)
        return await self._ctx.perform("state_management.incorporation_many",
            lambda: self._incorporation(identities))

    async def incorporate(self, state_id: int, *, validate_only: bool = False
                          ) -> OperationResult[IncorporationAction]:
        """Request incorporation using the selected state's original command.

        Native rules validate the generated command. validate_only returns that
        validation without submitting. No unincorporation action is implied.
        A submitted command's acceptance is distinct from its observed effect;
        incorporation() reads the current progress. Unknown delivery keeps the
        original query and source owners instead of repeating the command.
        """
        identity = _state_id(state_id)
        if type(validate_only) is not bool:
            raise TypeError("validate_only must be a boolean")

        async def apply() -> IncorporationAction:
            state_type = await self._ctx.type_id("State")
            run = _run.Run(self._ctx, "state_management.incorporate", namespace="state_management")
            result: IncorporationAction = {"action": "incorporate", "state_id": identity,
                "accepted": None}
            try:
                owner = await run.generated_owner("State.IncorporateState",
                    (data.ObjectBinding.state(state_type, identity),))
                result["world"] = owner.world
                await run.validate_submit(owner, result, validate_only=validate_only)
            except KnownResultError as error:
                result.update({"status": "native_result", "native_result": error.result,
                               "native_error": error.result.error})
            except SourceUnavailable as error:
                result.update({"status": "source_unavailable", "reason": str(error)})
            except BaseException as error:
                result["operations"] = tuple(run.operations)
                setattr(error, "state_management_result", result)
                raise
            result["operations"] = tuple(run.operations)
            return await run.finish(result)

        return await self._ctx.perform("state_management.incorporate", apply)

    async def names(self, state_id: int) -> OperationResult[StateNames]:
        """Read raw state and five hub overrides, preserving native errors.

        Values are not localized fallback names. Separate callbacks can observe
        different moments. An exception keeps ``state_names`` with all completed
        observations plus the original pending query.
        """
        identity = _state_id(state_id)
        async def read() -> StateNames:
            run = _run.Run(self._ctx, "state_management.names", namespace="state_management")
            result = await _read_state_names(run, identity)
            # These are copied bytes, not retained Any values. The native query
            # carries its own cleanup receipts through the shared executor.
            await run.finish({"state_names": result})
            return result
        return await self._ctx.perform("state_management.names", read)

    async def rename(self, state_id: int, *, state: str | None = None,
                     city: str | None = None, port: str | None = None,
                     farm: str | None = None, mine: str | None = None,
                     wood: str | None = None, validate_only: bool = False
                     ) -> OperationResult[StateRenameResult]:
        """Set selected names with the original isolated native draft and Confirm.

        None preserves the raw override. An empty string requests clearing it;
        native validation decides whether that is allowed. Confirm writes all
        six names, so unspecified overrides are restored to the draft without
        substituting localized fallbacks. Only those fields need old-value reads.
        With no requested changes, no native call is made and acceptance is None.
        The command uses the game's current native actor, not an explicit-country
        read selector. Native validation runs once. Acceptance is not observed
        completion; names() provides a separate current observation.
        """
        identity = _state_id(state_id)
        if type(validate_only) is not bool:
            raise TypeError("validate_only must be a boolean")
        requested: dict[str, str] = {}
        for field, value in zip(_STATE_NAME_FIELDS, (state, city, port, farm, mine, wood)):
            if value is None:
                continue
            if not isinstance(value, str):
                raise TypeError(field + " must be a string or None")
            if "\0" in value:
                raise ValueError(field + " cannot contain NUL: the native UTF-8 setter truncates it")
            value.encode("utf-8", "strict")
            requested[field] = value
        async def apply() -> StateRenameResult:
            result: StateRenameResult = {"action": "rename", "state_id": identity,
                "requested": requested, "edits": {}, "accepted": None}
            if not requested:
                result["status"] = "no_request"
                return result
            run = _run.Run(self._ctx, "state_management.rename", namespace="state_management")
            try:
                # Establish the native world-bound owner before copying overrides.
                # Later native binding checks reject a changed world without an
                # SDK world poll, an expected-world replacement or a UI popup.
                created = await run.capture("names.create_context", lambda:
                    data.create_state_name_context(run.native, identity,
                        adapter=run.adapters["data"], process=run.process))
                result["context"] = created
                if created.error:
                    raise KnownResultError("names.create_context", created)
                value = created.value
                if not isinstance(value, data.NativeObject) or not value.value_handle:
                    raise SourceUnavailable("native state-name context has no owned object binding")
                if value.type_id < 0:
                    raise SourceUnavailable("native state-name context has no dynamic binding type")
                binding = value.binding()
                before = await _read_state_names(run, identity,
                    tuple(field for field in _STATE_NAME_FIELDS if field not in requested))
                result["before"] = before
                fields = before.fields
                if not before.complete:
                    result.update({"status": "incomplete_original_overrides",
                        "reason": "Confirm needs complete raw overrides for every unselected field"})
                    result["operations"] = tuple(run.operations)
                    return await run.finish(result)
                desired = {field: (requested[field].encode("utf-8") if field in requested
                                  else fields[field].snapshot) for field in _STATE_NAME_FIELDS}
                result["desired"] = desired
                texts: dict[str, str] = {}
                for field, raw in desired.items():
                    try:
                        if b"\0" in raw:
                            raise ValueError("original override contains NUL and cannot round-trip the native setter")
                        texts[field] = raw.decode("utf-8", "strict")
                    except (UnicodeDecodeError, ValueError) as error:
                        result.update({"status": "unrepresentable_original_override",
                                       "field": field, "reason": str(error)})
                        result["operations"] = tuple(run.operations)
                        return await run.finish(result)
                for field, handler in zip(_STATE_NAME_FIELDS, EDIT_NAMES):
                    edited = await run.capture("names.edit." + field, lambda field=field, handler=handler:
                        data.invoke_action(run.native, "StateNameChangePopup." + handler,
                            data.TypedInput.utf8_string(texts[field]), (binding,),
                            adapter=run.adapters["data"], process=run.process))
                    result["edits"][field] = edited
                    # A copy/metadata error after accepted invocation does not
                    # undo a setter. Keep the full receipt, do not replay it.
                    if edited.invoked != 1 or edited.accepted != 1:
                        result.update({"status": "draft_edit_rejected", "field": field,
                            "native_result": edited, "native_error": edited.error})
                        result["operations"] = tuple(run.operations)
                        return await run.finish(result)
                owner = await run.generated_owner("StateNameChangePopup.Confirm", (binding,))
                result["world"] = owner.world
                await run.validate_submit(owner, result, validate_only=validate_only)
            except KnownResultError as error:
                result.update({"status": "native_result", "native_result": error.result,
                               "native_error": error.result.error})
            except SourceUnavailable as error:
                result.update({"status": "source_unavailable", "reason": str(error)})
            except BaseException as error:
                result["operations"] = tuple(run.operations)
                setattr(error, "state_management_result", result)
                raise
            result["operations"] = tuple(run.operations)
            return await run.finish(result)

        return await self._ctx.perform("state_management.rename", apply)
