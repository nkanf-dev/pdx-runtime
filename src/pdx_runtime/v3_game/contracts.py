"""Public typing contracts for research, UI perception and pending handles.

These types describe current return values. They do not wrap results, validate
native data, or change the ordinary ``await game.research.current()`` call.
``OperationResult[T]`` is either the final value or the existing in-memory
PendingOperation, described structurally by ``PendingResult[T]``.

Research command ``accepted`` records native receiver acceptance, not proof of
selection, completion, or AI acceptance. The separately observed ``status`` and
queue fields describe the later, non-atomic observation. A completed submit
that has a native error, unknown ownership, receiver rejection or unavailable
post-state returns ResearchNativeResult with the exact CommandResult. Input,
pre-submit validation and unfinished query handling remain separate.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Literal, NotRequired, TypeAlias, TypedDict

__all__ = [
    "OperationResult", "PendingResult", "ResearchCategory", "ResearchCurrent",
    "ResearchCurrentTechnology", "ResearchNoCurrent", "ResearchOption",
    "ResearchDetails", "ResearchQueueEntry", "ResearchAlreadyCurrent",
    "ResearchSubmittedChange", "ResearchNativeResult", "ResearchChange",
    "NotificationCursor", "NotificationSource", "NotificationReceipt",
    "NotificationChange", "PerceptionSnapshot", "PerceptionPredicate",
    "ConstructionQueueField", "ConstructionQueueItem", "ConstructionQueueObservation", "ConstructionIntentResult",
]

from ..query_executor import OperationResult, PendingResult, _T_co
from ..v3_commands import CommandResult


ResearchCategory: TypeAlias = Literal["production", "military", "society", "other"]


class _ResearchObservation(TypedDict):
    country_id: int
    atomic_snapshot: Literal[False]
    unit: Literal["research_points"]


class ResearchCurrentTechnology(_ResearchObservation):
    """Current identity; nullable values are not replaced by zero or guessed names."""

    has_current_research: Literal[True]
    key: str
    name: str | None
    name_reason: str | None
    progress: Decimal | None
    cost: Decimal | None
    is_current: Literal[True]


class ResearchNoCurrent(_ResearchObservation):
    """No current research; is_current and name_reason are absent, not false/null."""

    has_current_research: Literal[False]
    key: None
    name: None
    progress: None
    cost: None
    reason: Literal["no current research"]


ResearchCurrent: TypeAlias = ResearchCurrentTechnology | ResearchNoCurrent


class _ResearchTechnology(TypedDict):
    key: str
    name: str | None
    progress: Decimal
    cost: Decimal
    unit: Literal["research_points"]
    researched: bool
    is_current: bool


class ResearchOption(_ResearchTechnology):
    """A native candidate; candidate membership alone is not selection eligibility."""

    category: ResearchCategory
    researchable: bool | None
    can_start: bool | None
    eligibility_reason: str | None


class ResearchDetails(_ResearchTechnology):
    """Definition-key read; it does not query category or selection eligibility."""

    category: None
    category_reason: Literal["category is not queried by this definition-key read"]
    researchable: None
    can_start: None
    eligibility_reason: Literal["selection eligibility is not queried by this read"]
    name_reason: NotRequired[str]


class ResearchQueueEntry(TypedDict):
    """One-based observed queue position, excluding current research; order is kept."""

    key: str
    position: int


class ResearchAlreadyCurrent(TypedDict):
    """start(key) observed the same current key and did not submit a command."""

    key: str
    accepted: None
    is_current: Literal[True]
    status: Literal["already_current"]
    submitted: Literal[False]


class ResearchSubmittedChange(TypedDict):
    """Accepted native command plus a later observation, not an atomic effect receipt.

    ``accepted_not_observed`` retains acceptance without claiming selection or
    queue membership. ``removed`` means the target was absent from the observed
    queue and was not current; it does not prove a research-completion effect.
    """

    key: str
    accepted: Literal[True]
    submitted: Literal[True]
    status: Literal["current", "removed", "queued", "accepted_not_observed"]
    is_current: bool
    queue: list[ResearchQueueEntry]
    queue_changed: bool
    target_selection_changed: bool
    atomic_snapshot: Literal[False]
    native_result: NotRequired[CommandResult]
    native_error: NotRequired[Literal[0]]
    native_outcome: NotRequired[Literal["consumed"]]
    cleanup: NotRequired[dict[str, object]]


class ResearchNativeResult(TypedDict):
    """Completed native submit, without inventing receiver acceptance or post-state.

    The original result and pending data stay available. ``accepted`` is None
    unless the native receiver result is known. Unqueried post-state fields are
    None, not false or empty. Before observations remain their original objects.
    """

    key: str
    status: Literal["native_result"]
    submission_attempted: Literal[True]
    submitted: Literal[True] | None
    accepted: bool | None
    native_result: CommandResult
    native_error: int
    native_outcome: str | None
    before: dict[str, object] | None
    submit_before: dict[str, object] | None
    pending: dict[str, object] | None
    cleanup: dict[str, object]
    is_current: None
    queue: None
    queue_changed: None
    target_selection_changed: None
    reason: str
    atomic_snapshot: Literal[False]
    observation_error: NotRequired[Exception]


ResearchChange: TypeAlias = ResearchAlreadyCurrent | ResearchSubmittedChange | ResearchNativeResult


class NotificationCursor(TypedDict):
    """Context-local sampled batch cursor; not a native history position."""

    world: int
    sequence: int


class NotificationSource(TypedDict):
    source: str
    status: Literal["available", "partial", "unavailable", "failed"]
    reason: str | None
    coverage: str | list[str] | None
    count: NotRequired[int | None]
    root_expression: NotRequired[str | None]
    atomic_snapshot: NotRequired[Literal[False]]
    history_complete: NotRequired[Literal[False]]
    world: NotRequired[int]
    observed_source_counts: NotRequired[list[int]]
    history_available: NotRequired[Literal[False]]
    native_cursor_available: NotRequired[Literal[False]]
    stable_identity_available: NotRequired[Literal[False]]
    native_validation: NotRequired[str]
    count_observation: NotRequired[dict[str, object]]
    presence: NotRequired[dict[str, object]]
    read_finished_monotonic_ns: NotRequired[int]
    sampled: NotRequired[bool]
    sampling_status: NotRequired[str]
    stale: NotRequired[bool]
    enumeration_complete: NotRequired[bool]
    page_start_index: NotRequired[int]
    next_index: NotRequired[int]
    page_scope: NotRequired[str]
    reason_non_atomic: NotRequired[str]


class NotificationReceipt(TypedDict):
    """Copied receipt, not permission to act or evidence of an active war.

    Native identity is absent unless the source proves it. EventWindow local_id
    reuses the existing local event token. List positions and text are never
    promoted to stable identity. fields may contain copied raw_bytes bytes.
    """

    source: str
    kind: str
    provenance: Literal["native_ui_data", "state_observation"]
    native_identity: int | str | bytes | None
    native_identity_reason: str | None
    local_id: str | None
    world: int
    actor_country_id: int | None
    actor_reason: NotRequired[str]
    raw_text: dict[str, str | None] | None
    fields: dict[str, object]
    scope: dict[str, object]
    visibility: dict[str, object]
    time: dict[str, object]
    actions: list[dict[str, object]]
    deduplication: dict[str, object]
    identity_scope: NotRequired[str | None]
    source_index: NotRequired[int]
    parent_source_index: NotRequired[int]
    source_path: NotRequired[list[dict[str, object]]]
    observations_are_atomic: NotRequired[Literal[False]]
    identity_uncertainty: NotRequired[str]
    event: NotRequired[dict[str, object]]
    active: NotRequired[None]
    active_reason: NotRequired[str]


class NotificationChange(TypedDict):
    lifecycle: Literal["first_observed", "updated", "observed", "no_longer_observed"]
    receipt: NotificationReceipt
    reason: NotRequired[str]


class PerceptionSnapshot(TypedDict):
    world: int
    world_after: int
    world_changed: bool
    actor_country_id: int | None
    actor_reason: str | None
    date_parts: tuple[int, int, int, int] | None
    atomic_snapshot: Literal[False]
    sources: list[NotificationSource]
    receipts: list[NotificationReceipt]
    selected_sources_complete: bool
    coverage_complete: Literal[False]
    history_complete: Literal[False]
    history_reason: str
    cursor: NotificationCursor | None
    cursor_status: Literal["baseline", "valid", "gap", "invalid", "world_changed"]
    changes: list[NotificationChange]
    sampling: NotRequired[dict[str, object]]
    provider_sampling_cursor: NotRequired[dict[str, object] | None]
    guard_dependencies: NotRequired[dict[str, object]]


PerceptionPredicate: TypeAlias = Callable[[PerceptionSnapshot], bool]


class ConstructionQueueField(TypedDict):
    """One copied original field, including partial bytes and its native error."""
    native_result: object
    error: int
    kind: int
    raw_bits: int
    snapshot: bytes
    snapshot_bytes: int
    total: int
    copy_complete: bool
    complete: bool
    value: object
    decode_error: NotRequired[Exception]
    reason: NotRequired[str]


class ConstructionQueueItem(TypedDict):
    """Observed source position and transient native index, not an action ID."""
    source_position: int
    index: int | None
    progress: Decimal | int | float | None
    construction_speed: Decimal | int | float | None
    valid_construction: bool | None
    state_name: str | None
    building_name: str | None
    fields: dict[str, ConstructionQueueField]
    native_element: object
    complete: bool
    progress_units: Literal["fraction (0 to 1)"]
    text_format: Literal["native_formatted"]
    tooltip: NotRequired[str | None]
    queue_number: NotRequired[int | None]
    base_construction_speed: NotRequired[Decimal | int | float | None]
    currently_under_construction: NotRequired[bool | None]
    weeks_left: NotRequired[int | None]
    reason: NotRequired[str]


class ConstructionQueueObservation(TypedDict):
    """One requested source range; counts and fields are not an atomic snapshot."""
    queue: Literal["government"]
    source_expression: Literal["Country.AccessGovernmentConstructionQueue"]
    selection_scope: Literal["current_unfiltered_government_source"]
    country_id: int | None
    items: list[ConstructionQueueItem]
    source_count: int | None
    requested_range: dict[str, int | None]
    observed_range: dict[str, int]
    next: int | None
    range_complete: bool
    complete: bool
    atomic_snapshot: Literal[False]
    status: str
    source_result: object
    count_result: object
    work_scope: str
    cleanup: NotRequired[dict[str, object]]
    operations: NotRequired[list[tuple[str, object]]]
    error: NotRequired[BaseException]
    exception_native_result: NotRequired[object]


class ConstructionIntentResult(TypedDict):
    """Native build/estimate intent; admission and observations stay separate."""
    state_id: int
    building_key: str
    levels: int
    eligible: bool | None
    accepted: bool | None
    status: str
    reason: str | None
    reason_complete: bool
    reason_details: dict[str, object]
    creation: NotRequired[object]
    validation: NotRequired[object]
    native_result: NotRequired[object]
    reason_raw: NotRequired[str | None]
    post_observation: NotRequired[dict[str, object]]
    post_observation_error: NotRequired[Exception]
    enrichment_operations: NotRequired[list[tuple[str, object]]]
    partial_native_results: NotRequired[list[object]]
    cleanup: NotRequired[dict[str, object]]
    operations: NotRequired[list[tuple[str, object]]]
    current_costs: NotRequired[dict[str, object] | None]
    work_scope: NotRequired[str]
    name: NotRequired[object]
    candidate_source: NotRequired[str]
    native_fields: NotRequired[dict[str, object]]
    construction_costs_description: NotRequired[object]
    construction_costs_description_reason: NotRequired[str]
    name_reason: NotRequired[str]
    existing_levels_reason: NotRequired[str]
    maximum_levels_reason: NotRequired[str]
    required_construction_reason: NotRequired[str]
    required_construction: NotRequired[Decimal | int | float | None]
    required_construction_per_level: NotRequired[Decimal | int | float | None]
    construction_units: NotRequired[str]
    existing_levels: NotRequired[object]
    maximum_levels: NotRequired[object]
    enrichment_error: NotRequired[Exception]
    error: NotRequired[BaseException]
