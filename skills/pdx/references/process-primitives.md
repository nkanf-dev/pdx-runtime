# Process and memory primitives

Imports come from `pdx_runtime.native_bridge`, not the package root.
`RuntimeSession` comes from `pdx_runtime.session`.

## Launch or open

`RemoteProcess(pid, api=None)` opens an OS process handle.
`RemoteProcess.launch(executable, arguments=(), *, cwd=None,
creation_flags=0, api=None)` creates one process and retains its original handle.
Arguments are separate strings, not shell text. No game flags, DLL loads, hooks,
or world entry are implicit. `close()` never terminates the process.
A caller can use the returned `pid` with `RuntimeSession.load` explicitly.

`poll()` samples the retained process handle once: `None` means running;
an integer is the exit DWORD. Keep this original handle across native shutdown.
A disconnected pipe or native quit flag is not process-exit proof.

## First-class primitives

| Method on `RemoteProcess` | Contract |
| --- | --- |
| `modules()` | Current module records: name, path, full-width base, size. |
| `read(address, size)` / `write(address, data)` | Exact raw bytes; no game lock or pointee lease. |
| `allocate(size, protection=PAGE_READWRITE)` / `free(address)` | Caller-owned remote allocation; free only after every native consumer finishes. |
| `query_memory(address)` | One copied Windows region snapshot, not validity proof. |
| `protect(address, size, protection)` | Explicit Windows page protection; returns the first page's old flags. Restore each original region as needed. |
| `flush_instruction_cache(address=0, size=0)` | Explicit cache flush; no patching or dispatch. |
| `resolve_export(module_name, symbol)` | Resolve a loaded module name or exact base and export name or ordinal. |
| `export_owner(address)` | Actual resident module base, including forwarded targets. |
| `start_thread(function_address, argument_address=0)` | Verified `DWORD WINAPI(void*)` entry only; not a generic game-call ABI. |
| `load_library(dll_path, *, timeout_ms=INFINITE)` | One load or continuation of that path's unfinished original load. |

`NativeBridge.read(address, size)` and `write(address, data)` are also public.
Each bridge transfer is 1..4096 bytes. They do not supply engine access guards.
Direct process reads do not have that bridge message bound.

## Retain the original loader

This example needs the existing `process` and an explicit `adapter_path` input.
It does not choose or install a game adapter.

```python
from pdx_runtime.native_bridge import LibraryLoadPending

try:
    module = process.load_library(adapter_path, timeout_ms=0)
except LibraryLoadPending as error:
    original_load = error.attempt
```

On a later turn call `original_load.resume()`. It either returns the module or
raises `LibraryLoadPending` carrying that same attempt. Preserve it again.
`process.pending_library_loads` exposes unfinished attempts. A visible module
alone does not prove the loader thread finished using its input.
The pending owner retains the original process even after a requested close;
continuation can finish cleanup without PID reopen or a second loader thread.

`RemoteThread.poll()` samples once; `None` is pending and an integer is its exit
DWORD. `wait(timeout_ms=INFINITE)` is available when an explicit bounded/native
wait is appropriate. `close()` releases the observation handle, not the thread's
input storage. Do not close the only observation handle while its owned input is
still in use. A thread exit DWORD is not an x64 pointer or module base.

Export discovery and PE format validation are not build-identity/hash gates.
Use actual resident exports and the installed adapter's native state contracts.
