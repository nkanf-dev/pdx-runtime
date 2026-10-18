# Low-level PDX / Victoria 3 action API

This reference covers explicit low-level command, console, script, access-scope, and Session callback declarations. Ordinary player-facing tasks should use `pdx_runtime.v3_game.V3Game`. This catalog is not a claim of complete Victoria 3 native action coverage.

## Shared action and continuation contract

- `CallbackResult.read()` is a one-shot, nonblocking observation of an existing callback. It returns `None` while pending; it does not wait or submit another action.
- The callback handle owns its input/result storage until completion is known. When a helper creates a process handle, it closes that handle only after the result is consumed; a supplied `process=` remains caller-owned. Callback storage ownership does not imply ownership of unrelated target pointers or DLLs.
- If a submission exception carries `error.query`, preserve and inspect that original handle. Unknown delivery is not permission to replay. `recover(request_id)` binds only the inspected original request.
- Native `error`, `validated`, `accepted`, output text, and observable game-state changes are different facts. The source does not expose hidden parser or domain-success results unless a result field says so.

## Module contracts

### `pdx_runtime.v3_commands`

`encode_value`, `encode_command`, and `encode_tree` create the native CBinLexer wire representation; they do not write command objects. Plain `int` uses the source's signed-32 encoding; use `UInt32`, `Int64`, `UInt64`, `Float32`, and `Token` when that wire type is intended. `Token` is a numeric token ID, not a typed numeric field. `NamedToken` is a name-encoded token. A resolved token keeps its original numeric ID.

`request()` returns a nonblocking callback handle (not annotated in source). `Action.SUBMIT` uses `v3_session` and returns a two-stage session callback handle. `CommandResult.error == 0`, a nonzero command pointer, or `validated`/`accepted` do not independently prove complete reader parsing or domain success. `validate()` leaves command ownership with `NativeCommand`; native `SUBMIT`/`DESTROY` may consume ownership. `NativeCommand.complete()` only reads the exact pending attempt. It does not retry or free/submit implicitly. A retained command requires its original concrete world for non-destroy operations.

### `pdx_runtime.v3_console`

Console input and argument parsing use the native command manager. The APIs include text execution, command enumeration, parser inspection, exact callback/object invocation, and registration management. `ConsoleResult` contains copied output/command/argument data plus native success/context metadata. It does not certify later simulation consequences. `complete_output` reports whether required output fits the copied output.

### `pdx_runtime.v3_script`

`submit()` queues a single native Trigger/Effect/Value/Typed Value request using its `kind`; the decoded `ScriptResult` keeps parse/execution/boolean/diagnostic fields separate. `parse()`, `create_context()`, `intern_name()`, and `ParsedValue`/`NativeContext` operations return `NativeOperation` handles. `NativeOperation.read()` returns `None` while pending, then a `NativeOperationResult`; it preserves the operation and its owners on uncertain/failed target completion. No destructor dispatches a remote release. Release compiled owners and contexts only through their explicit native release methods after known completion. Saved-scope insertion changes the native map and is not rolled back.

`Scope.id()` and `Scope.raw()` create the source-defined scope records. The enum values are source enum values, not inferred display names. `FixedValue` retains raw bits; `.signed()` and `.decimal()` expose the source conversions.

### `pdx_runtime.v3_access`

`mode` and `flag` are raw native values; the source assigns them no read/write permission meanings. `dispatch` must be exactly `"interface"` or `"session"`. The caller chooses target legality and execution context. The returned existing callback handle exposes the access addresses; callee completion is separated as `AccessResult.error` and `AccessResult.callee_result`. Code, target, pointees, and any caller-provided payload remain live until completion. `submit_batch()` is one explicit sequence inside one scope; a failed or unknown prefix is not replayed.

### `pdx_runtime.v3_session`

The Session path has two request IDs: the Interface request that enqueues the callback, then the Session callback request. `SessionCallbackResult.read()` samples both stages without waiting. `callback_status()` observes the state export only. `enqueue()` is lower level: its `NativeCallResult.rax` is the enqueue Win32 code, and caller-owned `opaque`/request storage must remain live through Session completion. The Session hook and raw-call adapter are not silently installed here.


## Callable signatures

Signatures below follow declarations in the named source modules. A missing `->` annotation means the source has no return annotation; no return type is inferred. Dataclass constructor signatures list declared fields and defaults.

### `pdx_runtime.v3_commands`

```text
pdx_runtime.v3_commands.Token(value: int)
pdx_runtime.v3_commands.ResolvedToken(value: int, name: str)
pdx_runtime.v3_commands.NamedToken(value: str)
pdx_runtime.v3_commands.UInt32(value: int)
pdx_runtime.v3_commands.Int64(value: int)
pdx_runtime.v3_commands.UInt64(value: int)
pdx_runtime.v3_commands.Float32(value: float)
pdx_runtime.v3_commands.Assignment(key: Token | NamedToken, value: object)
pdx_runtime.v3_commands.Registration(type_token: int, factory_index: int, factory: int, token_name: str)
pdx_runtime.v3_commands.CommandResult(size: int, error: int, action: int, type_token: int, command: int, buffer: int, buffer_capacity: int, buffer_size: int, count: int, submit_flags: int, validated: int, accepted: int, execution_thread_id: int, reserved: int, application: int, world: int, session: int, data: bytes = b'', registrations: tuple[Registration, ...] = (), reason: str = '', cloned_command: int = 0, token_name: str = '')
pdx_runtime.v3_commands.CommandResult.token_name_truncated(self) -> bool
pdx_runtime.v3_commands.CommandResult.reason_truncated(self) -> bool
pdx_runtime.v3_commands.CommandResult.from_bytes(cls, data: bytes) -> CommandResult
pdx_runtime.v3_commands.encode_value(value: object) -> bytes
pdx_runtime.v3_commands.encode_command(type_token: int, parameters: Mapping | tuple=()) -> bytes
pdx_runtime.v3_commands.encode_tree(command: Assignment) -> bytes
pdx_runtime.v3_commands.token_ids(value: object) -> tuple[int, ...]
pdx_runtime.v3_commands.name_tokens(value: object, names: Mapping[int, str]) -> object
pdx_runtime.v3_commands.decode_command(data: bytes) -> Assignment
pdx_runtime.v3_commands.request(client: NativeBridge, action: Action | int, *, type_token: int=0, command: int=0, data: bytes=b'', capacity: int=0, submit_flags: int=4, world: int=0, adapter: str | int=ADAPTER, process: RemoteProcess | None=None)
pdx_runtime.v3_commands.token_name(client, type_token: int, *, capacity: int=4096, adapter=ADAPTER, process=None)
pdx_runtime.v3_commands.resolve_token(client, name: str, *, adapter=ADAPTER, process=None)
pdx_runtime.v3_commands.enumerate_commands(client, *, adapter=ADAPTER, process=None)
pdx_runtime.v3_commands.create(client, type_token: int, *, parameters=None, actor_id=None, world=0, adapter=ADAPTER, process=None)
pdx_runtime.v3_commands.CommandAttempt(action: Action, command: int, type_token: int, world: int, capacity: int = 0, submit_flags: int = 4, query: object = None, submission_error: BaseException | None = None, completion_error: BaseException | None = None, result: CommandResult | None = None, outcome: str = 'pending')
pdx_runtime.v3_commands.NativeCommand(client, command, type_token, world, *, adapter = ADAPTER, process = None)
pdx_runtime.v3_commands.NativeCommand.from_result(cls, client, result: CommandResult, *, adapter=ADAPTER, process=None)
pdx_runtime.v3_commands.NativeCommand.clone(self)
pdx_runtime.v3_commands.NativeCommand.serialize(self, capacity=65536)
pdx_runtime.v3_commands.NativeCommand.validate(self, capacity=VALIDATION_REASON_CAPACITY)
pdx_runtime.v3_commands.NativeCommand.submit(self, flags=4)
pdx_runtime.v3_commands.NativeCommand.destroy(self)
pdx_runtime.v3_commands.NativeCommand.complete(self)
```

### `pdx_runtime.v3_console`

```text
pdx_runtime.v3_console.Command(kind: int, developer_allowed: bool, object: int, callback: int, context: int, name: str, description: str, aliases: tuple[str, ...], parameters: tuple[str, ...])
pdx_runtime.v3_console.ConsoleResult(size: int, error: int, action: int, native_success: int, input_size: int, output_capacity: int, output_size: int, output_required: int, count: int, total_count: int, release_restricted: int, multiplayer_or_ironman: int, execution_thread_id: int, reserved: int, application: int, manager: int, exact_callback: int, exact_context: int, argument_count: int, records_bytes: int, output: bytes, commands: tuple[Command, ...] = (), arguments: tuple[str, ...] = ())
pdx_runtime.v3_console.ConsoleResult.message(self) -> str
pdx_runtime.v3_console.ConsoleResult.complete_output(self) -> bool
pdx_runtime.v3_console.ConsoleResult.from_bytes(cls, data: bytes) -> ConsoleResult
pdx_runtime.v3_console.execute(client: NativeBridge, text: str, *, output_capacity: int=65536, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_console.execute_arguments(client: NativeBridge, arguments, *, output_capacity: int=65536, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_console.enumerate_commands(client: NativeBridge, *, output_capacity: int=1048576, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_console.parse(client: NativeBridge, text: str, *, output_capacity: int=65536, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_console.invoke_exact(client: NativeBridge, callback: int, context: int, arguments_text: str, *, output_capacity: int=65536, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_console.invoke_object(client: NativeBridge, object_address: int, arguments_text: str, *, output_capacity: int=65536, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_console.register_command(client: NativeBridge, name: str, callback: int, context: int=0, *, description: str='', aliases: tuple[str, ...]=(), parameters: tuple[str, ...]=(), developer_allowed: bool=False, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_console.unregister_command(client: NativeBridge, name: str, *, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
```

### `pdx_runtime.v3_script`

```text
pdx_runtime.v3_script.Scope(type: int, payload: int)
pdx_runtime.v3_script.Scope.id(cls, type: int, full_id: int) -> Scope
pdx_runtime.v3_script.Scope.raw(cls, type: int, payload: int) -> Scope
pdx_runtime.v3_script.Scope.to_bytes(self) -> bytes
pdx_runtime.v3_script.ScriptResult(size: int, error: int, kind: int, text_bytes: int, scope: Scope, scope_valid: int, parsed: int, executed: int, raw_boolean: int, execution_thread_id: int, diagnostic_bytes: int, diagnostic_truncated: int, diagnostic_raw: bytes)
pdx_runtime.v3_script.ScriptResult.from_bytes(cls, data: bytes) -> ScriptResult
pdx_runtime.v3_script.ScriptResult.boolean(self) -> bool | None
pdx_runtime.v3_script.ScriptResult.diagnostic(self) -> str
pdx_runtime.v3_script.SavedScope(name: int, value: Scope)
pdx_runtime.v3_script.SavedScope.to_bytes(self) -> bytes
pdx_runtime.v3_script.TriggerValue(raw_boolean: int)
pdx_runtime.v3_script.TriggerValue.boolean(self) -> bool
pdx_runtime.v3_script.FixedValue(bits: int)
pdx_runtime.v3_script.FixedValue.signed(self) -> int
pdx_runtime.v3_script.FixedValue.decimal(self) -> Decimal
pdx_runtime.v3_script.ValueResult(script: ScriptResult, value_type: int, value_reserved: int, value_payload: int, context: int, saved_scopes: int, saved_count: int, text_offset: int)
pdx_runtime.v3_script.ValueResult.from_bytes(cls, data: bytes) -> ValueResult
pdx_runtime.v3_script.ValueResult.error(self) -> int
pdx_runtime.v3_script.ValueResult.raw(self) -> Scope
pdx_runtime.v3_script.ValueResult.value(self)
pdx_runtime.v3_script.encode_value_request(text: str, scope: Scope, *, kind: ScriptKind, saved_scopes: Iterable[SavedScope]=(), context: int=0, request_address: int=0) -> bytes
pdx_runtime.v3_script.submit(client: NativeBridge, text: str, scope: Scope, *, kind: ScriptKind, adapter: str | int='pdx_v3_script.dll', process: RemoteProcess | None=None, saved_scopes: Iterable[SavedScope]=(), context: int=0) -> v3.CallbackResult
pdx_runtime.v3_script.trigger(client: NativeBridge, text: str, scope: Scope, **kwargs)
pdx_runtime.v3_script.effect(client: NativeBridge, text: str, scope: Scope, **kwargs)
pdx_runtime.v3_script.value(client: NativeBridge, text: str, scope: Scope, **kwargs)
pdx_runtime.v3_script.typed_value(client: NativeBridge, text: str, scope: Scope, **kwargs)
pdx_runtime.v3_script.OwnerInfo(size: int, error: int, kind: int, scope_type: int, object: int, diagnostic_bytes: int, diagnostic_truncated: int, diagnostic_raw: bytes)
pdx_runtime.v3_script.OwnerInfo.from_bytes(cls, data: bytes) -> OwnerInfo
pdx_runtime.v3_script.NativeOperationResult(error: int, value: object, access: object)
pdx_runtime.v3_script.NativeOperation(process, owners, finish, temporary, owns_process = (), release_only_on_success = False)
pdx_runtime.v3_script.NativeOperation.read(self) -> NativeOperationResult | None
pdx_runtime.v3_script.NativeContext(client, process, address, scope, adapter, owns_process)
pdx_runtime.v3_script.NativeContext.set_saved(self, saved: SavedScope) -> NativeOperation
pdx_runtime.v3_script.NativeContext.release(self) -> NativeOperation
pdx_runtime.v3_script.NativeContext.discard_unconstructed(self) -> None
pdx_runtime.v3_script.ParsedValue(client, process, address, scope, kind, adapter, owns_process)
pdx_runtime.v3_script.ParsedValue.evaluate(self, context: NativeContext) -> NativeOperation
pdx_runtime.v3_script.ParsedValue.release(self) -> NativeOperation
pdx_runtime.v3_script.create_context(client: NativeBridge, scope: Scope, *, adapter: str | int='pdx_v3_script.dll', process: RemoteProcess | None=None) -> NativeOperation
pdx_runtime.v3_script.parse(client: NativeBridge, text: str, scope: Scope, *, kind: ScriptKind=ScriptKind.VALUE, adapter: str | int='pdx_v3_script.dll', process: RemoteProcess | None=None) -> NativeOperation
pdx_runtime.v3_script.intern_name(client: NativeBridge, name: str, *, adapter: str | int='pdx_v3_script.dll', process: RemoteProcess | None=None) -> NativeOperation
pdx_runtime.v3_script.ScopeTypeInfo(type: int, name_token: int, save_token: int, native_flags: int, name_bytes: int, name_required: int, save_name_bytes: int, save_name_required: int, native_entry: int, stores_variables: bool, name_raw: bytes, save_name_raw: bytes)
pdx_runtime.v3_script.ScopeTypeInfo.from_bytes(cls, data: bytes) -> ScopeTypeInfo
pdx_runtime.v3_script.ScopeTypeInfo.name(self) -> str
pdx_runtime.v3_script.ScopeTypeInfo.save_name(self) -> str
pdx_runtime.v3_script.ScopeTypeInfo.names_complete(self) -> bool
pdx_runtime.v3_script.ScopeTypeInfo.evaluates_triggers(self) -> bool
pdx_runtime.v3_script.ScopeTypeInfo.executes_effects(self) -> bool
pdx_runtime.v3_script.ScopeTypeInfo.changes_scopes(self) -> bool
pdx_runtime.v3_script.ScopeTypeInfo.reference(self, payload: int) -> Scope
pdx_runtime.v3_script.ScopeTypesResult(size: int, error: int, action: int, start: int, capacity: int, count: int, next: int, total: int, input_name_bytes: int, resolved_type: int, matched: int, reserved: int, input_name: int, output_rows: int, row_size: int, execution_thread_id: int, output_offset: int, output_bytes: int, rows: tuple[ScopeTypeInfo, ...])
pdx_runtime.v3_script.ScopeTypesResult.from_bytes(cls, data: bytes) -> ScopeTypesResult
pdx_runtime.v3_script.ScopeTypesResult.type(self) -> int | None
pdx_runtime.v3_script.enumerate_scope_types(client: NativeBridge, *, start: int=0, capacity: int=16, adapter: str | int='pdx_v3_script.dll', process: RemoteProcess | None=None) -> v3.CallbackResult
pdx_runtime.v3_script.resolve_scope_type(client: NativeBridge, name: str, *, adapter: str | int='pdx_v3_script.dll', process: RemoteProcess | None=None) -> v3.CallbackResult
```

### `pdx_runtime.v3_access`

```text
pdx_runtime.v3_access.encode_request(callbackfn: int, opaque: int, source: int, *, mode: int, flag: int, enter: int=0, leave: int=0) -> bytes
pdx_runtime.v3_access.AccessResult(size: int, error: int, mode: int, flag: int, callbackfn: int, opaque: int, source: int, enter: int, leave: int, context: int, enter_result: int, execution_thread_id: int, entered: int, left: int, reserved: int, payload: bytes, callee_result: object | None = None)
pdx_runtime.v3_access.AccessResult.from_bytes(cls, data: bytes, *, payload_size: int=0, decode_payload: Callable[[bytes], object] | None=None) -> AccessResult
pdx_runtime.v3_access.submit_callback(client: NativeBridge, callbackfn: int, initial_bytes: bytes=b'', *, mode: int, flag: int, dispatch: str, source: str='pdx-runtime/v3_access.py', adapter: str | int='pdx_v3_access.dll', process: RemoteProcess | None=None, session_adapter: str | int='v3_session_callbacks.dll', call_adapter: str | int='pdx_native_call.dll', enter: int=0, leave: int=0, decode_payload: Callable[[bytes], object] | None=None, prepare_payload: Callable[[int], bytes] | None=None)
pdx_runtime.v3_access.submit_call(client: NativeBridge, target: int, arguments: Iterable[int]=(), *, mode: int, flag: int, dispatch: str, adapter: str | int='pdx_v3_access.dll', call_adapter: str | int='pdx_native_call.dll', process: RemoteProcess | None=None, **options)
pdx_runtime.v3_access.submit_batch(client: NativeBridge, calls: Iterable[tuple[int, Iterable[int]]], *, mode: int, flag: int, dispatch: str, adapter: str | int='pdx_v3_access.dll', call_adapter: str | int='pdx_native_call.dll', process: RemoteProcess | None=None, **options)
```

### `pdx_runtime.v3_session`

```text
pdx_runtime.v3_session.SessionCallbackStatus(size: int, phase: int, error: int, execution_thread_id: int, request_id: int, completed_request_id: int, callbackfn: int, opaque: int, session: int)
pdx_runtime.v3_session.SessionCallbackStatus.from_bytes(cls, data: bytes) -> SessionCallbackStatus
pdx_runtime.v3_session.callback_status(process: RemoteProcess, *, adapter: str | int='v3_session_callbacks.dll') -> SessionCallbackStatus
pdx_runtime.v3_session.enqueue(client: NativeBridge, callbackfn: int, opaque: int, request_address: int, *, session_adapter: str | int='v3_session_callbacks.dll', call_adapter: str | int='pdx_native_call.dll', process: RemoteProcess) -> native_call.NativeCallRequest
pdx_runtime.v3_session.SessionCallbackResult(client, process, callback, address, owns_process, *, callbackfn, state_address, payload_size, decode_result)
pdx_runtime.v3_session.SessionCallbackResult.read(self)
pdx_runtime.v3_session.submit_callback(client: NativeBridge, callbackfn: int, initial_bytes: bytes, *, decode_result: Callable[[bytes], object], session_adapter: str | int='v3_session_callbacks.dll', call_adapter: str | int='pdx_native_call.dll', process: RemoteProcess | None=None, _prepare_payload: Callable[[int], bytes] | None=None) -> SessionCallbackResult
pdx_runtime.v3_session.submit(client: NativeBridge, target: int, arguments: Iterable[int]=(), *, session_adapter: str | int='v3_session_callbacks.dll', call_adapter: str | int='pdx_native_call.dll', process: RemoteProcess | None=None) -> SessionCallbackResult
pdx_runtime.v3_session.submit_batch(client: NativeBridge, calls: Iterable[tuple[int, Iterable[int]]], *, session_adapter: str | int='v3_session_callbacks.dll', call_adapter: str | int='pdx_native_call.dll', process: RemoteProcess | None=None) -> SessionCallbackResult
```

## Public result and value shapes

Fields and property return types below follow the source annotations. Raw integer fields remain raw; source meanings are summarized in the contract notes rather than inferred from their names.

### `pdx_runtime.v3_commands`

**Action**: `ENUMERATE=1`, `CREATE=2`, `DESERIALIZE=3`, `SERIALIZE=4`, `VALIDATE=5`, `DESTROY=6`, `SUBMIT=7`, `CLONE=8`, `TOKEN_NAME=9`, `RESOLVE_TOKEN=10`

**Token**
- Fields: `value: int`

**ResolvedToken**
- Fields: `name: str`

**NamedToken**
- Fields: `value: str`

**UInt32**
- Fields: `value: int`

**Int64**
- Fields: `value: int`

**UInt64**
- Fields: `value: int`

**Float32**
- Fields: `value: float`

**Assignment**
- Fields: `key: Token | NamedToken`; `value: object`

**Registration**
- Fields: `type_token: int`; `factory_index: int`; `factory: int`; `token_name: str`

**CommandResult**
- Fields: `size: int`; `error: int`; `action: int`; `type_token: int`; `command: int`; `buffer: int`; `buffer_capacity: int`; `buffer_size: int`; `count: int`; `submit_flags: int`; `validated: int`; `accepted: int`; `execution_thread_id: int`; `reserved: int`; `application: int`; `world: int`; `session: int`; `data: bytes = b''`; `registrations: tuple[Registration, ...] = ()`; `reason: str = ''`; `cloned_command: int = 0`; `token_name: str = ''`
- Properties: `token_name_truncated -> bool`; `reason_truncated -> bool`

**CommandAttempt**
- Fields: `action: Action`; `command: int`; `type_token: int`; `world: int`; `capacity: int = 0`; `submit_flags: int = 4`; `query: object = None`; `submission_error: BaseException | None = None`; `completion_error: BaseException | None = None`; `result: CommandResult | None = None`; `outcome: str = 'pending'`

### `pdx_runtime.v3_console`

**Command**
- Fields: `kind: int`; `developer_allowed: bool`; `object: int`; `callback: int`; `context: int`; `name: str`; `description: str`; `aliases: tuple[str, ...]`; `parameters: tuple[str, ...]`

**ConsoleResult**
- Fields: `size: int`; `error: int`; `action: int`; `native_success: int`; `input_size: int`; `output_capacity: int`; `output_size: int`; `output_required: int`; `count: int`; `total_count: int`; `release_restricted: int`; `multiplayer_or_ironman: int`; `execution_thread_id: int`; `reserved: int`; `application: int`; `manager: int`; `exact_callback: int`; `exact_context: int`; `argument_count: int`; `records_bytes: int`; `output: bytes`; `commands: tuple[Command, ...] = ()`; `arguments: tuple[str, ...] = ()`
- Properties: `message -> str`; `complete_output -> bool`

### `pdx_runtime.v3_script`

**ScriptKind**: `TRIGGER=1`, `EFFECT=2`, `VALUE=3`, `TYPED_VALUE=4`

**ScopeType**: `COUNTRY=5`, `BUILDING=8`, `POP=63`, `STATE=76`

**Scope**
- Fields: `type: int`; `payload: int`

**ScriptResult**
- Fields: `size: int`; `error: int`; `kind: int`; `text_bytes: int`; `scope: Scope`; `scope_valid: int`; `parsed: int`; `executed: int`; `raw_boolean: int`; `execution_thread_id: int`; `diagnostic_bytes: int`; `diagnostic_truncated: int`; `diagnostic_raw: bytes`
- Properties: `boolean -> bool | None`; `diagnostic -> str`

**SavedScope**
- Fields: `name: int`; `value: Scope`

**TriggerValue**
- Fields: `raw_boolean: int`
- Properties: `boolean -> bool`

**FixedValue**
- Fields: `bits: int`
- Properties: `signed -> int`; `decimal -> Decimal`

**ValueResult**
- Fields: `script: ScriptResult`; `value_type: int`; `value_reserved: int`; `value_payload: int`; `context: int`; `saved_scopes: int`; `saved_count: int`; `text_offset: int`
- Properties: `error -> int`; `raw -> Scope`; `value -> (no annotation)`

**OwnerInfo**
- Fields: `size: int`; `error: int`; `kind: int`; `scope_type: int`; `object: int`; `diagnostic_bytes: int`; `diagnostic_truncated: int`; `diagnostic_raw: bytes`

**NativeOperationResult**
- Fields: `error: int`; `value: object`; `access: object`

**ScopeTypeInfo**
- Fields: `type: int`; `name_token: int`; `save_token: int`; `native_flags: int`; `name_bytes: int`; `name_required: int`; `save_name_bytes: int`; `save_name_required: int`; `native_entry: int`; `stores_variables: bool`; `name_raw: bytes`; `save_name_raw: bytes`
- Properties: `name -> str`; `save_name -> str`; `names_complete -> bool`; `evaluates_triggers -> bool`; `executes_effects -> bool`; `changes_scopes -> bool`

**ScopeTypesResult**
- Fields: `size: int`; `error: int`; `action: int`; `start: int`; `capacity: int`; `count: int`; `next: int`; `total: int`; `input_name_bytes: int`; `resolved_type: int`; `matched: int`; `reserved: int`; `input_name: int`; `output_rows: int`; `row_size: int`; `execution_thread_id: int`; `output_offset: int`; `output_bytes: int`; `rows: tuple[ScopeTypeInfo, ...]`
- Properties: `type -> int | None`

### `pdx_runtime.v3_access`

**AccessResult**
- Fields: `size: int`; `error: int`; `mode: int`; `flag: int`; `callbackfn: int`; `opaque: int`; `source: int`; `enter: int`; `leave: int`; `context: int`; `enter_result: int`; `execution_thread_id: int`; `entered: int`; `left: int`; `reserved: int`; `payload: bytes`; `callee_result: object | None = None`

### `pdx_runtime.v3_session`

**SessionCallbackStatus**
- Fields: `size: int`; `phase: int`; `error: int`; `execution_thread_id: int`; `request_id: int`; `completed_request_id: int`; `callbackfn: int`; `opaque: int`; `session: int`
