# PDX raw bridge, session, and native-call API

This reference lists the shared low-level Python surfaces declared in `pdx_runtime.session`, `pdx_runtime.native_bridge`, `pdx_runtime.native_callback`, and `pdx_runtime.native_call`. Raw access does not establish target-specific ABI, object-layout, execution-context or access-scope legality. Game-specific placement and gameplay APIs remain in each game adapter's reference.

## Artifact and explicit export lookup

The packaged native artifact is `pdx_runtime.dll`, returned by `runtime_dll_path()` from the package's `_native` directory. The shared native-call defaults select this canonical name.

`RemoteProcess.resolve_export(module_name, symbol, *, aliases=None)` resolves a loaded module name/base and export name/ordinal. With no map, lookup is strict: it does not select a distribution or game alias catalog. Loaded module names and exact integer bases take priority. Only a missing string selector can use its explicit normalized key in `aliases` to select one loaded target. An existing module's missing export does not fall back to an alias. Forwarded exports retain the same explicit policy.

`pdx_runtime.native_distribution.RUNTIME_EXPORT_ALIASES` supplies the two generic legacy selectors, `pdx_native_call.dll` and `pdx_runtime_bridge.dll`, for callers that explicitly choose that distribution map. It is not selected automatically by the process resolver. Game wire operations and game-specific alias policy belong in the [game adapter reference](../../victoria3/references/connection-control.md#explicit-v3-wire).

## Resource, error, and continuation contract

- `BridgeError` preserves `operation`, `winerror`, and `details`; `as_dict()` returns those values in a status mapping. Native return codes and Python exceptions remain distinct.
- `RemoteProcess` is the explicit Windows process/memory/thread layer. `read()` and `write()` require complete transfers. They return copied bytes / written byte count; `allocate()` returns a target-process address, not a Python buffer. `close()` releases this process handle and does not terminate a launched process. A `RemoteProcess` context manager closes its owned observation handle.
- A remote thread's `poll()` samples once and returns `None` while running or its exit DWORD after signaling; `wait(timeout_ms)` waits for that thread. The DWORD is not treated as an address. `start_thread()` is only a raw `DWORD WINAPI(void*)` primitive, not an arbitrary game-call ABI.
- `load_library()` may raise `LibraryLoadPending`. The exception exposes the original `attempt`; call `attempt.resume()` to continue that same load. The attempt retains its thread/input resources until completion is proven. Do not dispatch a second load because module visibility alone is not completion proof.
- `NativeBridge.connect()` connects to an existing pipe; `NativeBridge.load()` loads/initializes the packaged or explicit bridge artifact and connects. `NativeBridge.close()` closes the pipe. Its generic request/status/read/write methods return source-defined status dictionaries or copied memory, or raise `BridgeError`; they do not select a game wire adapter or establish an engine execution/access scope.
- `RuntimeSession` owns a `NativeBridge` client and a persistent ordinary Python namespace. `execute()` propagates statement exceptions; `evaluate()` stores the result in `_`; `close()` closes its client but leaves the namespace inspectable.
- `native_callback.CallbackContext` explicitly binds the exact client, dispatch, normalized completion source, owned-process factory, side-effect-free process identity predicate and protocol submission classifier. No game queue or PID contract is inferred. Accepted delivery needs a positive request ID; only a proved rejection permits immediate release. Unknown/malformed delivery retains `error.query` without replay.
- `native_call.encode_request()` only packs a request; it does not allocate or dispatch. `native_call.submit(..., context=...)` returns `NativeCallRequest`, whose inherited `read()` is nonblocking. The result exposes raw `rax` and `xmm0` bytes. The source's ordinary Win64 helper puts positional slots 1–4 in matching GP and XMM registers and later arguments in 8-byte stack slots. It is not `__vectorcall`, does not infer a function signature, and supplies no game guard/TLS/domain. Caller owns target legality, pointees, and loaded target-module lifetime.
- `NativeCallRequest` owns the single request-plus-argument allocation. Unknown callback delivery retains the same handle in `error.query`; recover only after inspecting the original request ID. Do not replay an unknown call or a batch with an executed prefix.
- A supplied process remains caller-owned. Without one, `context.process_factory()` provides the owned process. `process_matches(process)` must return exact `True` before lookup/allocation; custom adapters may use opaque target identity rather than PIDs. Context selection supplies no guard, TLS or object lifetime. Game-specific placement remains in that adapter's reference.


## Callable signatures

Signatures below follow declarations in the named source modules. A missing `->` annotation means the source has no return annotation; no return type is inferred. Dataclass constructor signatures list declared fields and defaults.

### `pdx_runtime.session`

```text
pdx_runtime.session.RuntimeSession(client: NativeBridge)
pdx_runtime.session.RuntimeSession.connect(cls, pid: int, timeout_ms: int=5000) -> RuntimeSession
pdx_runtime.session.RuntimeSession.load(cls, pid: int, dll_path: str | Path | None=None) -> RuntimeSession
pdx_runtime.session.RuntimeSession.execute(self, source: str, filename: str='<runtime>') -> None
pdx_runtime.session.RuntimeSession.evaluate(self, expression: str, filename: str='<runtime>') -> Any
pdx_runtime.session.RuntimeSession.close(self) -> None
```

### `pdx_runtime.native_bridge`

```text
pdx_runtime.native_bridge.runtime_dll_path() -> Path
pdx_runtime.native_bridge.BridgeError(operation: str, winerror: int, **details)
pdx_runtime.native_bridge.BridgeError.as_dict(self) -> dict
pdx_runtime.native_bridge.MemoryRegion(base_address: int, allocation_base: int, allocation_protection: int, partition_id: int, size: int, state: int, protection: int, type: int)
pdx_runtime.native_bridge.NativeWindowsAPI()
pdx_runtime.native_bridge.NativeWindowsAPI.failure(operation: str) -> BridgeError
pdx_runtime.native_bridge.NativeWindowsAPI.modules(self, pid: int) -> list[dict]
pdx_runtime.native_bridge.RemoteThread(api: NativeWindowsAPI, handle: int, tid: int)
pdx_runtime.native_bridge.RemoteThread.poll(self) -> int | None
pdx_runtime.native_bridge.RemoteThread.wait(self, timeout_ms: int=INFINITE) -> int
pdx_runtime.native_bridge.RemoteThread.close(self)
pdx_runtime.native_bridge.LibraryLoadPending(attempt: LibraryLoadAttempt)
pdx_runtime.native_bridge.LibraryLoadAttempt(process: RemoteProcess, path: Path, key: str, loader: int)
pdx_runtime.native_bridge.LibraryLoadAttempt.resume(self) -> dict
pdx_runtime.native_bridge.RemoteProcess(pid: int, api: NativeWindowsAPI | None)
pdx_runtime.native_bridge.RemoteProcess.launch(cls, executable: str | Path, arguments: Iterable[str]=(), *, cwd: str | Path | None=None, creation_flags: int=0, api: NativeWindowsAPI | None=None) -> RemoteProcess
pdx_runtime.native_bridge.RemoteProcess.pending_library_loads(self) -> tuple[LibraryLoadAttempt, ...]
pdx_runtime.native_bridge.RemoteProcess.close(self)
pdx_runtime.native_bridge.RemoteProcess.modules(self) -> list[dict]
pdx_runtime.native_bridge.RemoteProcess.poll(self) -> int | None
pdx_runtime.native_bridge.RemoteProcess.read(self, address: int, size: int) -> bytes
pdx_runtime.native_bridge.RemoteProcess.write(self, address: int, data: bytes) -> int
pdx_runtime.native_bridge.RemoteProcess.allocate(self, size: int, protection: int=PAGE_READWRITE) -> int
pdx_runtime.native_bridge.RemoteProcess.free(self, address: int)
pdx_runtime.native_bridge.RemoteProcess.query_memory(self, address: int) -> MemoryRegion
pdx_runtime.native_bridge.RemoteProcess.protect(self, address: int, size: int, protection: int) -> int
pdx_runtime.native_bridge.RemoteProcess.flush_instruction_cache(self, address: int=0, size: int=0) -> None
pdx_runtime.native_bridge.RemoteProcess.start_thread(self, function_address: int, argument_address: int=0) -> RemoteThread
pdx_runtime.native_bridge.RemoteProcess.resolve_export(self, module_name: str | int, symbol: str | int, _seen=None, *, aliases: Mapping[str, str | int] | None=None) -> int
pdx_runtime.native_bridge.RemoteProcess.export_owner(self, address: int) -> int
pdx_runtime.native_bridge.RemoteProcess.load_library(self, dll_path: str | Path, *, timeout_ms: int=INFINITE) -> dict
pdx_runtime.native_bridge.NativeBridge(pid: int, api: NativeWindowsAPI | None)
pdx_runtime.native_bridge.NativeBridge.connect(cls, pid: int, timeout_ms: int=5000) -> 'NativeBridge'
pdx_runtime.native_bridge.NativeBridge.load(cls, pid: int, dll_path: str | Path | None=None) -> 'NativeBridge'
pdx_runtime.native_bridge.NativeBridge.close(self)
pdx_runtime.native_bridge.NativeBridge.request(self, command: str) -> dict
pdx_runtime.native_bridge.NativeBridge.status(self) -> dict
pdx_runtime.native_bridge.NativeBridge.read(self, address: int, size: int) -> bytes
pdx_runtime.native_bridge.NativeBridge.write(self, address: int, data: bytes) -> int
pdx_runtime.native_bridge.main(argv=None) -> int
```

### `pdx_runtime.native_callback`

```text
pdx_runtime.native_callback.CallbackExecutionError(result: object, winerror: int, request_id: int)
pdx_runtime.native_callback.CallbackDisposition(kind: str, request_id: int | None=None, error: int=0, reason: str | None=None)
pdx_runtime.native_callback.CallbackContext(client: object, dispatch: Callable[[int, int], dict], status_getter: Callable[[], dict], process_factory: Callable[[], RemoteProcess], process_matches: Callable[[RemoteProcess], bool], classify_submission: Callable[[object], CallbackDisposition], submit_operation: str='callback submit', completion_operation: str='callback completion', execution_error_factory: Callable[[object, int, int], BridgeError]=CallbackExecutionError, *, export_resolver: Callable[[RemoteProcess, str | int, str], int] | None=None)
pdx_runtime.native_callback.CallbackResult(client: object, process: RemoteProcess, callback_address: int, result_address: int, owns_process: bool, result_size: int, decode_result: Callable[[bytes], object], *, context: CallbackContext)
pdx_runtime.native_callback.CallbackResult.read(self)
pdx_runtime.native_callback.CallbackResult.recover(self, request_id: int) -> CallbackResult
pdx_runtime.native_callback.CallbackResult.close(self) -> None
pdx_runtime.native_callback.submit_callback(client: object, initial_bytes: bytes, *, context: CallbackContext, adapter: str | int, callback_symbol: str, decode_result: Callable[[bytes], object], process: RemoteProcess | None=None, handle_factory: Callable | None=None) -> CallbackResult
```

`status_getter()` returns `status="ok"` on successful sampling with `completed_request_id` and optional `error`/`request_id`/`callback_error`; a callback error belongs only to the matching latest request. `classify_submission(raw)` returns `CallbackDisposition('accepted', request_id=...)`, `CallbackDisposition('rejected', error=..., reason=...)` or an unknown disposition. Error 0 alone is not delivery proof. Status reads and copying result storage are not an atomic transaction.

The optional keyword-only `export_resolver(process, adapter, symbol)` receives the original process and exact adapter name/base. `None` calls that process's strict `resolve_export(adapter, symbol)` with no alias policy. An explicitly supplied resolver can select a policy without replacing the client/process or changing callback ownership.

### `pdx_runtime.native_call`

```text
pdx_runtime.native_call.float32_bits(value: float) -> int
pdx_runtime.native_call.float64_bits(value: float) -> int
pdx_runtime.native_call.encode_request(target: int, arguments: Iterable[int]=(), *, request_address: int) -> bytes
pdx_runtime.native_call.NativeCallResult(size: int, error: int, target: int, argument_count: int, arguments_address: int, rax: int, xmm0: bytes)
pdx_runtime.native_call.NativeCallResult.from_bytes(cls, data: bytes) -> NativeCallResult
pdx_runtime.native_call.NativeCallResult.xmm0_low64(self) -> int
pdx_runtime.native_call.NativeCallResult.xmm0_high64(self) -> int
pdx_runtime.native_call.NativeCallRequest(client: NativeBridge, process: RemoteProcess, callback_address: int, result_address: int, owns_process: bool, *, target: int, argument_count: int, context: CallbackContext)
pdx_runtime.native_call.submit(client: NativeBridge, target: int, arguments: Iterable[int]=(), *, context: CallbackContext, adapter: str | int='pdx_runtime.dll', process: RemoteProcess | None=None) -> NativeCallRequest
pdx_runtime.native_call.NativeCallBatchResult(size: int, error: int, count: int, requests_address: int, completed: int, calls: tuple[NativeCallResult, ...])
pdx_runtime.native_call.NativeCallBatchResult.from_bytes(cls, data: bytes) -> NativeCallBatchResult
pdx_runtime.native_call.encode_batch(calls: Iterable[tuple[int, Iterable[int]]], *, request_address: int=0) -> bytes
pdx_runtime.native_call.submit_batch(client: NativeBridge, calls: Iterable[tuple[int, Iterable[int]]], *, context: CallbackContext, adapter: str | int='pdx_runtime.dll', process: RemoteProcess | None=None) -> CallbackResult
```

## Public result and value shapes

Fields and property return types below follow the source annotations. Raw integer fields remain raw; source meanings are summarized in the contract notes rather than inferred from their names.

### `pdx_runtime.native_bridge`

**MemoryRegion**
- Fields: `base_address: int`; `allocation_base: int`; `allocation_protection: int`; `partition_id: int`; `size: int`; `state: int`; `protection: int`; `type: int`

**RemoteProcess**
- Properties: `pending_library_loads -> tuple[LibraryLoadAttempt, ...]`

### `pdx_runtime.native_call`

**NativeCallResult**
- Fields: `size: int`; `error: int`; `target: int`; `argument_count: int`; `arguments_address: int`; `rax: int`; `xmm0: bytes`
- Properties: `xmm0_low64 -> int`; `xmm0_high64 -> int`

**NativeCallBatchResult**
- Fields: `size: int`; `error: int`; `count: int`; `requests_address: int`; `completed: int`; `calls: tuple[NativeCallResult, ...]`
