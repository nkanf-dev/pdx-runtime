# Low-level PDX / Victoria 3 lifecycle API

This reference covers explicit installation, control, lifecycle, option, and partial-time-step declarations. Ordinary player-facing tasks should use `pdx_runtime.v3_game.V3Game`. It does not claim complete native lifecycle coverage.

## Shared callback and ownership contract

- Callback calls return handles that are read with `read()`. The read is nonblocking and does not submit another operation. Pending callback input/result storage must remain alive until completion is established.
- When `process=None`, the helper creates and later closes its own process handle after the callback storage is consumed. A caller-supplied `RemoteProcess` is borrowed and remains open. Native pointers returned by result records are not thereby owned.
- A transport exception may carry the original handle as `error.query`. Keep it and inspect it; do not replay an operation whose delivery or ownership is unknown.
- Callback completion, native acceptance, asynchronous task completion, world transition, and process exit are separate states. Use the result fields or explicit follow-up observations that the source provides.

## Units, values, and lifecycle behavior

- `v3_lifecycle.submit()` accepts the source-defined save type byte range `0..5`; the code does not name meanings for each value. The native speed index is `0..4`. `LifecycleResult.displayed_speed` maps paused to `0`, otherwise native index plus one. `set_date()` accepts year, month, day, and hour components; `date_parts` parses the engine's date text and does not apply a separate calendar conversion.
- A save callback can yield an owned native task handle. `retain_save_result()` wraps that reference in `SaveOperation`. `poll()` queues one status observation; `release()` queues one native reference release; `settle_release()` reads that exact release callback. Neither polls in a loop nor repeats the original save/release. `saved` is true only when the result has no error and reports both task completion and file verification.
- Save catalog output is copied into `SaveDescriptor` records. `output_capacity`, `output_size`, `output_required`, `count`, and `total_count` describe returned native output; a short output is not automatically rescanned. `SaveSource.LOCAL`/`CLOUD` are bit flags. File dates remain the engine-provided values/text.
- `exit_application()` requires explicit `ExitIntent`. `quit_requested` reports the Application flag, not process termination. Observe process exit independently with `RemoteProcess.poll()`.
- `v3_control.start()` separates callback completion, `start_accepted`, and `game_started`. `finalize_play()` does not select a country or start simulation; its native sync/ready fields and later GUI transition remain separate. Construction validation returns the validator's raw output words; it is not construction submission.
- `v3_options` returns an `AccessResult`; its `callee_result` is `OptionsResult`. Access scope and native options ownership are separate. `Ownership.UNKNOWN` is not a release/transfer receipt. Constructed options/settings pointers follow the source ownership enum; destroy or transfer an owned pointer exactly once after known completion. Settings are native 112-byte subobjects, while the native Options owner is 224 bytes; the API is not a full object clone or generic settings editor.
- `v3_time_step.submit()` queues one native partial tick in an already paused world. The source defines it as **6 game hours**, not one hour or one day. `Step.submission.read()` observes the admission callback; `Step.observe()` queues one separate snapshot; `compare()` compares the baseline to that snapshot. `date_hours` is the low 32-bit date value. Completion requires the source's accepted, same-world, paused, initialized, settled date-plus-six-hours conditions. It does not imply all later effects are complete.
- `v3_runtime.install()` returns a dictionary with role/module/address/stage/diagnostic/pending-owner information. It operates on the supplied client/process and does not close them. Its internal continuation is based on retained pending work; do not discard pending owners.


## Callable signatures

Signatures below follow declarations in the named source modules. A missing `->` annotation means the source has no return annotation; no return type is inferred. Dataclass constructor signatures list declared fields and defaults.

### `pdx_runtime.v3_runtime`

```text
pdx_runtime.v3_runtime.install(client: NativeBridge, process: RemoteProcess, artifact_dir: str | Path | None=None, *, modules: dict[str, int | dict] | None=None, stage: str='all') -> dict
```

### `pdx_runtime.v3_control`

```text
pdx_runtime.v3_control.ConstructionValidationResult(size: int, error: int, actor_id: int, state_id: int, building_type: int, carrier: int, raw_low: int, raw_high: int, validated: int, execution_thread_id: int, application: int, world: int)
pdx_runtime.v3_control.ConstructionValidationResult.from_bytes(cls, data: bytes) -> ConstructionValidationResult
pdx_runtime.v3_control.StartResult(size: int, error: int, action: int, country_id: int, local_machine_id: int, selected_country_id: int, paused: int, game_started: int, start_accepted: int, reserved: int, application: int, world: int, idler: int, logic: int, player: int)
pdx_runtime.v3_control.StartResult.from_bytes(cls, data: bytes) -> StartResult
pdx_runtime.v3_control.validate_construction(client: NativeBridge, actor_id: int, state_id: int, building_type: int, *, carrier: int, adapter: str | int='pdx_v3_construction_validate.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_control.start(client: NativeBridge, action: StartAction | int, country_id: int=0, *, adapter: str | int='pdx_v3_start_game.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_control.PlayFinalizeResult(size: int, error: int, action: int, country_id: int, gui_mode: int, paused: int, simulation_started: int, player_flags: int, lobby_visible: int, native_sync_done: int, native_ready_called: int, reserved: int, application: int, world: int, idler: int, gfx: int, handler: int, lobby: int, player: int, player_ref_database: int, player_country_id: int, handler_country_id: int, handler_ref_database: int, lobby_country_id: int, lobby_selected_country_id: int, lobby_ref_database: int)
pdx_runtime.v3_control.PlayFinalizeResult.from_bytes(cls, data: bytes) -> PlayFinalizeResult
pdx_runtime.v3_control.finalize_play(client: NativeBridge, country_id: int, *, observe: bool=False, adapter: str | int='pdx_v3_play_finalize.dll', process: RemoteProcess | None=None) -> CallbackResult
```

### `pdx_runtime.v3_lifecycle`

```text
pdx_runtime.v3_lifecycle.LifecycleResult(size: int, error: int, action: int, flags: int, save_type: int, paused: int, speed: int, current_state: int, pending_state: int, transition_pending: int, task_state: int, task_completed: int, file_present: int, file_verified: int, execution_thread_id: int, native_result: int, application: int, world: int, active_idler: int, active_idler_vtable: int, save_manager: int, owned_task_handle: int, task: int, file_size: int, date_raw: int, frame: int, path: str, resolved_path: str, date_text: str)
pdx_runtime.v3_lifecycle.LifecycleResult.from_bytes(cls, data: bytes) -> LifecycleResult
pdx_runtime.v3_lifecycle.LifecycleResult.displayed_speed(self) -> int
pdx_runtime.v3_lifecycle.LifecycleResult.date_parts(self) -> tuple[int, int, int, int] | None
pdx_runtime.v3_lifecycle.LifecycleResult.saved(self) -> bool
pdx_runtime.v3_lifecycle.LifecycleResult.quit_requested(self) -> bool
pdx_runtime.v3_lifecycle.LifecycleResult.in_main_menu(self) -> bool
pdx_runtime.v3_lifecycle.SaveOperation(result: LifecycleResult, client: NativeBridge, *, adapter: str | int, process: RemoteProcess | None)
pdx_runtime.v3_lifecycle.SaveOperation.poll(self) -> CallbackResult
pdx_runtime.v3_lifecycle.SaveOperation.release(self) -> CallbackResult
pdx_runtime.v3_lifecycle.SaveOperation.settle_release(self) -> LifecycleResult | None
pdx_runtime.v3_lifecycle.submit(client: NativeBridge, action: Action | int=Action.OBSERVE, *, path: str='', resolved_path: str='', save_type: int=3, paused: bool=True, speed: int=0, owned_task_handle: int=0, overwrite: bool=False, native_exit_save: bool=False, adapter: str | int='pdx_v3_lifecycle.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_lifecycle.observe(client: NativeBridge, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.resolve_save_path(client: NativeBridge, name: str, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.save(client: NativeBridge, name: str, *, save_type: int=3, overwrite: bool=False, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.load(client: NativeBridge, path: str, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.exit_to_menu(client: NativeBridge, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.resolve_native_exit_save_path(client: NativeBridge, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.exit_application(client: NativeBridge, *, intent: ExitIntent, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.set_speed(client: NativeBridge, speed: int, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.set_paused(client: NativeBridge, paused: bool, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.set_date(client: NativeBridge, year: int, month: int, day: int, hour: int=0, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.retain_save_result(result: LifecycleResult, client: NativeBridge, *, adapter: str | int='pdx_v3_lifecycle.dll', process: RemoteProcess | None=None) -> SaveOperation
pdx_runtime.v3_lifecycle.SaveDescriptor(size: int, cloud: int, is_valid: int, is_ironman_save: int, number_of_players: int, reserved: int, modified_time_raw: int, last_played_date_raw: int, country_flag_id: int, metadata_reserved: int, game_date_raw: int, path: str, directory: str, name: str, version_name: str, last_played_date_text: str, country_rank_text: str, game_date_text: str)
pdx_runtime.v3_lifecycle.SaveDescriptor.game_date_parts(self) -> tuple[int, int, int, int] | None
pdx_runtime.v3_lifecycle.SaveDescriptor.last_played_date_parts(self) -> tuple[int, int, int, int] | None
pdx_runtime.v3_lifecycle.SaveDescriptor.is_multiplayer(self) -> bool
pdx_runtime.v3_lifecycle.SaveCatalogResult(size: int, error: int, action: int, sources: int, directory_bytes: int, filter_bytes: int, output_capacity: int, output_size: int, output_required: int, count: int, total_count: int, execution_thread_id: int, application: int, save_manager: int, descriptor: int, records: tuple[SaveDescriptor, ...])
pdx_runtime.v3_lifecycle.SaveCatalogResult.from_bytes(cls, data: bytes) -> SaveCatalogResult
pdx_runtime.v3_lifecycle.submit_save_catalog(client: NativeBridge, action: SaveCatalogAction | int, *, directory: str='', name_filter: str='', sources: SaveSource=SaveSource.LOCAL, output_capacity: int=64 * 1024, adapter: str | int='pdx_v3_lifecycle.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_lifecycle.list_saves(client: NativeBridge, *, directory: str='', name_filter: str='', sources: SaveSource=SaveSource.LOCAL, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.describe_save(client: NativeBridge, path: str, *, cloud: bool=False, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.SaveManagementResult(size: int, error: int, action: int, cloud: int, source_bytes: int, destination_bytes: int, message_capacity: int, message_size: int, message_required: int, native_success: int, native_result_tag: int, execution_thread_id: int, application: int, save_manager: int, message_raw: bytes)
pdx_runtime.v3_lifecycle.SaveManagementResult.from_bytes(cls, data: bytes) -> SaveManagementResult
pdx_runtime.v3_lifecycle.SaveManagementResult.message(self) -> str
pdx_runtime.v3_lifecycle.submit_save_management(client: NativeBridge, action: SaveManagementAction | int, *, source: str, destination: str='', cloud: bool=False, message_capacity: int=4096, adapter: str | int='pdx_v3_lifecycle.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_lifecycle.rename_save(client: NativeBridge, source_native_path: str, new_name: str, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.delete_save(client: NativeBridge, native_path: str, **kwargs) -> CallbackResult
pdx_runtime.v3_lifecycle.delete_named_save(client: NativeBridge, name: str, *, cloud: bool=False, **kwargs) -> CallbackResult
```

### `pdx_runtime.v3_options`

```text
pdx_runtime.v3_options.OptionsResult(size: int, error: int, action: int, ownership: int, options: int, source_settings: int, settings_address: int, operation_options: int, started: int, execution_thread_id: int, reserved0: int, reserved1: int)
pdx_runtime.v3_options.OptionsResult.from_bytes(cls, data: bytes) -> OptionsResult
pdx_runtime.v3_options.OptionsResult.owns_pointer(self) -> bool
pdx_runtime.v3_options.OptionsResult.ownership_unknown(self) -> bool
pdx_runtime.v3_options.submit(client: NativeBridge, action: Action | int, *, options: int=0, source_settings: int=0, mode: int, flag: int, dispatch: str, adapter: str | int='pdx_v3_options.dll', access_adapter: str | int='pdx_v3_access.dll', process: RemoteProcess | None=None, **scope_options)
pdx_runtime.v3_options.create(client: NativeBridge, **scope_options)
pdx_runtime.v3_options.destroy(client: NativeBridge, owned_options: int, **scope_options)
pdx_runtime.v3_options.settings_address(client: NativeBridge, native_options: int, **scope_options)
pdx_runtime.v3_options.copy_settings(client: NativeBridge, native_options: int, source_settings: int, **scope_options)
pdx_runtime.v3_options.create_from_settings(client: NativeBridge, source_settings: int, **scope_options)
```

### `pdx_runtime.v3_time_step`

```text
pdx_runtime.v3_time_step.Snapshot(size: int, error: int, action: int, accepted: int, execution_thread_id: int, paused: int, game_started: int, transition_pending: int, initialized: int, thread_stop: int, world_updating: int, queues_settled: int, outgoing_active: int, outgoing_pending: int, incoming_active: int, incoming_pending: int, session_buffered: int, logic_tick_pending: int, raw_counter_5964f10: int, raw_counter_5964f14: int, application: int, world: int, idler: int, logic: int, session: int, dispatcher: int, thread_handle: int, active_command: int, date_raw: int, frame: int)
pdx_runtime.v3_time_step.Snapshot.from_bytes(cls, data: bytes) -> Snapshot
pdx_runtime.v3_time_step.Snapshot.date_hours(self) -> int
pdx_runtime.v3_time_step.Observation(completed: bool, pending: bool, delta_hours: int, reason: str, baseline: Snapshot, current: Snapshot)
pdx_runtime.v3_time_step.compare(baseline: Snapshot, current: Snapshot) -> Observation
pdx_runtime.v3_time_step.observe(client: NativeBridge, *, adapter: str | int='pdx_v3_time_step.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_time_step.Step(client: NativeBridge, submission: CallbackResult, adapter: str | int, process: RemoteProcess | None)
pdx_runtime.v3_time_step.Step.observe(self, *, process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_time_step.submit(client: NativeBridge, *, adapter: str | int='pdx_v3_time_step.dll', process: RemoteProcess | None=None) -> Step
```

## Public result and value shapes

Fields and property return types below follow the source annotations. Raw integer fields remain raw; source meanings are summarized in the contract notes rather than inferred from their names.

### `pdx_runtime.v3_control`

**StartAction**: `SELECT_COUNTRY=1`, `START_PAUSED=2`, `SELECT_AND_START_PAUSED=3`, `OBSERVE_START=4`

**ConstructionValidationResult**
- Fields: `size: int`; `error: int`; `actor_id: int`; `state_id: int`; `building_type: int`; `carrier: int`; `raw_low: int`; `raw_high: int`; `validated: int`; `execution_thread_id: int`; `application: int`; `world: int`

**StartResult**
- Fields: `size: int`; `error: int`; `action: int`; `country_id: int`; `local_machine_id: int`; `selected_country_id: int`; `paused: int`; `game_started: int`; `start_accepted: int`; `reserved: int`; `application: int`; `world: int`; `idler: int`; `logic: int`; `player: int`

**PlayFinalizeResult**
- Fields: `size: int`; `error: int`; `action: int`; `country_id: int`; `gui_mode: int`; `paused: int`; `simulation_started: int`; `player_flags: int`; `lobby_visible: int`; `native_sync_done: int`; `native_ready_called: int`; `reserved: int`; `application: int`; `world: int`; `idler: int`; `gfx: int`; `handler: int`; `lobby: int`; `player: int`; `player_ref_database: int`; `player_country_id: int`; `handler_country_id: int`; `handler_ref_database: int`; `lobby_country_id: int`; `lobby_selected_country_id: int`; `lobby_ref_database: int`

### `pdx_runtime.v3_lifecycle`

**Action**: `OBSERVE=0`, `SAVE=1`, `POLL_SAVE=2`, `RELEASE_SAVE=3`, `LOAD=4`, `EXIT_TO_MENU=5`, `SET_SPEED=6`, `SET_PAUSED=7`, `RESOLVE_SAVE_PATH=8`, `SET_DATE=9`, `EXIT_APPLICATION=10`, `RESOLVE_EXIT_SAVE_PATH=11`

**ExitIntent**: `DISCARD=0`, `SAVE_AT_NATIVE_DESTINATION=1`

**SaveSource**: `LOCAL=1`, `CLOUD=2`

**SaveCatalogAction**: `LIST=0`, `DESCRIBE_PATH=1`

**SaveManagementAction**: `RENAME_NATIVE=0`, `DELETE_PATH=1`, `DELETE_NAMED=2`

**LifecycleResult**
- Fields: `size: int`; `error: int`; `action: int`; `flags: int`; `save_type: int`; `paused: int`; `speed: int`; `current_state: int`; `pending_state: int`; `transition_pending: int`; `task_state: int`; `task_completed: int`; `file_present: int`; `file_verified: int`; `execution_thread_id: int`; `native_result: int`; `application: int`; `world: int`; `active_idler: int`; `active_idler_vtable: int`; `save_manager: int`; `owned_task_handle: int`; `task: int`; `file_size: int`; `date_raw: int`; `frame: int`; `path: str`; `resolved_path: str`; `date_text: str`
- Properties: `displayed_speed -> int`; `date_parts -> tuple[int, int, int, int] | None`; `saved -> bool`; `quit_requested -> bool`; `in_main_menu -> bool`

**SaveDescriptor**
- Fields: `size: int`; `cloud: int`; `is_valid: int`; `is_ironman_save: int`; `number_of_players: int`; `reserved: int`; `modified_time_raw: int`; `last_played_date_raw: int`; `country_flag_id: int`; `metadata_reserved: int`; `game_date_raw: int`; `path: str`; `directory: str`; `name: str`; `version_name: str`; `last_played_date_text: str`; `country_rank_text: str`; `game_date_text: str`
- Properties: `game_date_parts -> tuple[int, int, int, int] | None`; `last_played_date_parts -> tuple[int, int, int, int] | None`; `is_multiplayer -> bool`

**SaveCatalogResult**
- Fields: `size: int`; `error: int`; `action: int`; `sources: int`; `directory_bytes: int`; `filter_bytes: int`; `output_capacity: int`; `output_size: int`; `output_required: int`; `count: int`; `total_count: int`; `execution_thread_id: int`; `application: int`; `save_manager: int`; `descriptor: int`; `records: tuple[SaveDescriptor, ...]`

**SaveManagementResult**
- Fields: `size: int`; `error: int`; `action: int`; `cloud: int`; `source_bytes: int`; `destination_bytes: int`; `message_capacity: int`; `message_size: int`; `message_required: int`; `native_success: int`; `native_result_tag: int`; `execution_thread_id: int`; `application: int`; `save_manager: int`; `message_raw: bytes`
- Properties: `message -> str`

### `pdx_runtime.v3_options`

**Action**: `CREATE=1`, `DESTROY=2`, `SETTINGS_ADDRESS=3`, `COPY_SETTINGS=4`, `CREATE_FROM_SETTINGS=5`

**Ownership**: `NONE=0`, `OWNED=1`, `RELEASED=2`, `UNKNOWN=3`

**OptionsResult**
- Fields: `size: int`; `error: int`; `action: int`; `ownership: int`; `options: int`; `source_settings: int`; `settings_address: int`; `operation_options: int`; `started: int`; `execution_thread_id: int`; `reserved0: int`; `reserved1: int`
- Properties: `owns_pointer -> bool`; `ownership_unknown -> bool`

### `pdx_runtime.v3_time_step`

**Snapshot**
- Fields: `size: int`; `error: int`; `action: int`; `accepted: int`; `execution_thread_id: int`; `paused: int`; `game_started: int`; `transition_pending: int`; `initialized: int`; `thread_stop: int`; `world_updating: int`; `queues_settled: int`; `outgoing_active: int`; `outgoing_pending: int`; `incoming_active: int`; `incoming_pending: int`; `session_buffered: int`; `logic_tick_pending: int`; `raw_counter_5964f10: int`; `raw_counter_5964f14: int`; `application: int`; `world: int`; `idler: int`; `logic: int`; `session: int`; `dispatcher: int`; `thread_handle: int`; `active_command: int`; `date_raw: int`; `frame: int`
- Properties: `date_hours -> int`

**Observation**
- Fields: `completed: bool`; `pending: bool`; `delta_hours: int`; `reason: str`; `baseline: Snapshot`; `current: Snapshot`
