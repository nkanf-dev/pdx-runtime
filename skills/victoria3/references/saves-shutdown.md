# Native saves, load, and orderly shutdown

Advanced raw API reference. Ordinary gameplay uses `V3Game` and the
[game-level methods](gameplay.md). These details remain available for custom
compositions and diagnostics; they are not required player setup.

All paths here follow the V3 native save manager/VFS. A resolved VFS path need
not be an OS path. Never change the native VFS root or assume a private home
layout. Catalog metadata is copied data, not a retained descriptor pointer.

## Catalog and protect original saves

- `v3_lifecycle.list_saves(client, directory="", name_filter="",
  sources=SaveSource.LOCAL, ...)` enumerates the native root/provider.
  `directory` is a VFS path. `name_filter` is native descriptor-field equality,
  not filename substring, glob, or Python predicate. Cloud can legitimately be
  empty when its provider is absent.
- `describe_save(client, path, cloud=False, ...)` reads one native descriptor
  without loading, writing, or retaining it. Serialized game date and
  last-played date are distinct metadata.
- `resolve_save_path(client, name, ...)` asks the real manager for root/name/
  extension. Do not append a guessed extension or make a host-side root.
- For validation, create a fresh temporary subdirectory and a new name without
  the native extension. Keep `overwrite=False`. Original player saves must not
  become test output or cleanup targets. Inventory originals before and after.
- Rename/delete are explicit mutations: `rename_save(source_native_path,
  new_name)` takes a new leaf **with** the desired extension in the same
  directory, not an arbitrary destination VFS path. `delete_save(native_path)`
  deletes exactly that path; `delete_named_save(name, cloud=False)` uses the
  live root/native extension. Prefix all these calls with the live `client`.
  Cleanup only the newly created test artifacts, never a broad directory sweep.

## Own the asynchronous native save task

Input `temporary_save_name` is the fresh relative name chosen for this run.

```python
from pdx_runtime import v3_lifecycle

save_query = v3_lifecycle.save(
    client, temporary_save_name, overwrite=False, process=process
)
save_receipt = save_query.read()
```

If pending, retain `save_query`. Its callback completion is not necessarily
finished saving. When its result exposes an owned task reference, retain it:

```python
from pdx_runtime import v3_lifecycle

save_operation = v3_lifecycle.retain_save_result(
    save_receipt, client, process=process
)
save_poll_query = save_operation.poll()
save_progress = save_poll_query.read()
```

`poll()` submits one task observation, not another save. Finish each original
poll query before another observation. `saved` requires no native error,
`task_completed`, and `file_verified`; verify the intended path and file state.
Once no task consumer remains, release the task reference once:

```python
release_query = save_operation.release()
release_result = save_operation.settle_release()
```

If release is pending, keep `save_operation` and call `settle_release()` on a
later turn. Do not call `release()` again. Unknown transport requires recovery
of its exact `pending_release` with a proved request identity, not a repeated
native reference release. Save-task ownership and callback-buffer ownership are
different lifetimes.

## Load and world replacement

`exit_to_menu(client, process=process)` requests native discard-to-menu, not
process shutdown or an exit autosave. Observe fresh lifecycle state until
`in_main_menu` and the transition is settled. Then `load(client, path,
process=process)` consumes native file/options through the original main-menu
path. Complete the original load receipt and observe the asynchronous world
transition. Never replay a load because its new world is not ready yet.
Do not reuse old object/GUI/command pointers. Rediscover source world and IDs,
and resume the same installer journal for current native readiness.

`set_speed(client, speed, ...)` uses the native index. `displayed_speed` is zero
when paused, otherwise index+1. `set_paused` is separate. `set_date(client,
year, month, day, hour=0, ...)` preflights the native calendar parser and runs
the original date command; it is not evidence that intervening ticks settled.

## Native game shutdown and OS exit proof

`exit_application` requires an explicit `ExitIntent`. `DISCARD` uses native
shutdown without creating an exit save. `SAVE_AT_NATIVE_DESTINATION` authorizes
the game's configured exit/ironman destination and native overwrite; it is
**not** a temporary caller-selected filename. Use
`resolve_native_exit_save_path` for the ordinary configured destination; do not
guess an ironman target. To preserve originals during a validation run, finish
a new temporary save first if wanted, then select `DISCARD`.

After all intended work and owned releases settle:

```python
from pdx_runtime import v3_lifecycle

quit_query = v3_lifecycle.exit_application(
    client, intent=v3_lifecycle.ExitIntent.DISCARD, process=process
)
quit_receipt = quit_query.read()
```

The native process can exit before a final callback read succeeds. Keep the
original query and retained `process` observation handle; do not replay quit.
`quit_requested` is a native flag, not exit proof. On later turns,
`process.poll()` returns `None` while running or the actual exit DWORD when
signaled. Pipe disconnection alone proves neither success nor failure.
Do not replace native shutdown with forced kill during ordinary cleanup.
After confirmed exit and local ownership accounting, close `process` and
`session`. These closes release local handles; they do not implement shutdown.
