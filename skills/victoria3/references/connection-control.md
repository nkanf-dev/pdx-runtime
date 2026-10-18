# Connect, start, save and leave

## Run as an ordinary Python program

Install the delivered wheel in the Python environment used by the Agent, then
start Victoria 3 normally. Save this complete entry as `agent_entry.py` and run
`python agent_entry.py`. Pass one caller-chosen native tax band, such as
`python agent_entry.py low`, to request that change after listing real options.
With no argument it only observes. No repository experiment or extra DLL path
is needed for a wheel/portable installation.

```python
import asyncio
import sys
from pprint import pprint
from pdx_runtime.v3_game import V3Game, PendingOperation

async def settle(value):
    while isinstance(value, PendingOperation):
        value = await value.resume()
    return value

async def main(tax_band=None):
    game = await settle(await V3Game.attach())
    async with game:
        pprint(await settle(await game.country.summary()))
        pprint(await settle(await game.economy.tax_options()))
        if tax_band is not None:
            pprint(await settle(await game.economy.set_tax_level(tax_band)))
            pprint(await settle(await game.economy.tax_level()))

asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
```

The context manager detaches; it does not quit the game. A client does not own
the named-pipe endpoint or reserve execution while idle. Separate short-lived
programs can attach, perform their work and detach; client connections are
independent and actual native request dispatch is serialized.

Resume an unfinished operation rather than repeating an action. `pdx-runtime --help` and
`python -m pdx_runtime.shell --help` describe the separate raw session CLI: its
`--pid` is required, and a connection without `--dll` expects a resident Bridge.
For installation troubleshooting, `python -c "import pdx_runtime; print(pdx_runtime.__file__)"`
shows which installed/source copy this interpreter uses.

## Natural entry

```python
from pdx_runtime.v3_game import V3Game, PendingOperation

game = await V3Game.attach()
while isinstance(game, PendingOperation):
    game = await game.resume()
```

`attach(pid=None, **options)` discovers one running Victoria 3 when no PID is
supplied. Zero/multiple matches raise `ProcessSelectionError` with real choices;
it does not silently choose a different game. Connection/loading, explicit
adapter installation and original pending-load continuation are internal.
It does not restart a world or change research.

`launch(executable=None, *, arguments=(), cwd=None, **options)` starts one normal
process. The executable can be explicit; otherwise discovery uses
`VICTORIA3_EXE` or actual Steam library metadata. Arguments are separate strings.
It returns an attached game after readiness, not an already-started campaign.

Shared entry options accepted by the implementation are `process=None`,
`session=None`, `creation_flags=0`, `timeout=60.0`, `poll_interval=.01`,
`dll_path=None`, `artifact_dir=None`, `country_id=None`, and `adapters=None`.
These are advanced integration overrides, not ordinary onboarding steps.
A timeout returns the original `PendingOperation`; `resume(timeout=None)`
continues it. It is not a request to attach/launch again.

For an already connected integration, `V3Game(session, *, process=None,
country_id=None, adapters=None, timeout=30.0)` wraps the existing objects.
It does not launch/load/install. Omitted country follows the controlled Player;
an explicit country ID/tag selects the API target, not a forced Player switch.

## Campaign and save operations

| Instance method | Parameters and result |
| --- | --- |
| `new_campaign(country="CHI", *, save=False)` | Native world creation, country tag/ID resolution, paused start and Play; returns started/country/paused observations. It replaces the current world through menu. |
| `saves(*, cloud=False, directory="")` | Native catalog rows with name/path, validity, game/last-played date, cloud/ironman and other copied metadata. `cloud=True` includes cloud rows. |
| `save(name)` | A relative new native save name; no overwrite. Returns saved/path/bytes/date only after file verification; task release is internal. |
| `load(name, *, save=False)` | Matches one actual catalog name/path, returns to menu, loads and observes the new world. Cloud download is not implemented. |
| `menu(*, save=False)` | Native return to menu. `False` discards the current world; `True` first creates a fresh named save; a string supplies that fresh name. |
| `quit(*, save=False)` | Native game exit plus original OS-process exit code. `False` discards; `True` uses the game's configured exit-save destination, which can overwrite; a string first creates a new named save. |
| `detach()` | Waits for retained operations and closes only facade-owned session/process handles. It does not quit, unload the library, or close borrowed integration handles. |

`load` and `new_campaign` use the same `save` choices as `menu`. The API refreshes
new-world metadata internally. Names/paths come from the native catalog and VFS;
players do not construct a host-specific save root.

A separate new-game example, not an action to run on an existing campaign:

```python
game = await V3Game.launch()
while isinstance(game, PendingOperation):
    game = await game.resume()
started = await game.new_campaign(country="CHI")
```

For saves, pick a new name or one returned by the catalog:

```python
catalog = await game.saves()
saved = await game.save("agent-session/checkpoint-01")
```

`async with game` calls `detach()` on exit; it never silently exits the game.
The Python lifecycle methods are implemented; new high-level native integration
coverage must be established independently of earlier low-level helper tests.

## Raw connection/control lookup

# Connection, staged installation, and player control

Advanced raw API reference. Ordinary gameplay uses `V3Game` and the
[game-level methods](gameplay.md). These details remain available for custom
compositions and diagnostics; they are not required player setup.

## Install roles explicitly

`v3_runtime.install(client, process, artifact_dir=None, *, modules=None,
stage="all")` uses the packaged native directory by default. `stage` accepts
`"interface"`, `"session"`, or `"all"`. Client and process must target the same PID.
An explicit artifact directory or role-to-module hints are development inputs.
Integer adapter bases remain exact; release adapter-name aliases resolve to the
packaged DLL when a real split development module is not loaded.

The installer discovers actual exports, retains already healthy bindings, and
returns partial diagnostics when another role cannot yet run. It does not close
the caller's client/process, unload DLLs, create a world, or wait in a loop.

- Preserve the same process object: its retained journal owns pending loads,
  Interface install delivery, Session install thread, and reset continuation.
- Inspect `stages`, `diagnostics`, and `pending`, not just a top-level status.
- A pending native loader continues its original thread and input allocation.
- A worldless Session or native initialization can be `deferred`. Interface can
  remain usable. Continue the world transition and then call the same installer.
- A Session binding conflict is left intact. Do not remove another owner's hook.
- A reset recovery preserves original queued Session work. Do not Remove/requeue
  a request to work around initialization. Observe its original completion.
  The established native menu shutdown/init recovery is not permission to
  force a reset inside an active world or claim every reset context is tested.

## Explicit V3 wire

`V3Wire` is an explicit game adapter over the existing connected transport:

```python
from pdx_runtime.v3_wire import V3Wire

wire = V3Wire(client)
```

Construction performs no I/O. The view retains the exact `client`; it does not
open, reconnect, replace or own a transport, process, Session or query. Methods
use that client's original `request`, return its unchanged response and propagate
its exceptions. They add no retry, completion inference or access scope.

```text
pdx_runtime.v3_wire.V3Wire(client: WireTransport)
pdx_runtime.v3_wire.V3Wire.queue_interface_callback(self, callback_address: int, opaque_address: int=0) -> dict
pdx_runtime.v3_wire.V3Wire.construction_status(self) -> dict
pdx_runtime.v3_wire.V3Wire.queue_construction(self, actor_id: int, state_id: int, building_type: int) -> dict
pdx_runtime.v3_wire.V3Wire.install_interface_hook(self) -> dict
pdx_runtime.v3_wire.V3Wire.remove_interface_hook(self) -> dict
pdx_runtime.v3_wire.V3Wire.world_status(self) -> dict
pdx_runtime.v3_wire.V3Wire.enter_world(self, options: int=0) -> dict
pdx_runtime.v3_wire.V3Wire.create_options(self) -> dict
pdx_runtime.v3_wire.V3Wire.submit_options(self, options: int) -> dict
pdx_runtime.v3_wire.V3Wire.destroy_options(self, options: int) -> dict
pdx_runtime.v3_wire.resolve_v3_export(process: RemoteProcess, module_name: str | int, symbol: str | int) -> int
```

`V3_MODULE_ALIASES` holds 17 V3 selectors; `V3_EXPORT_ALIASES` explicitly combines
those with the two generic distribution selectors. `resolve_v3_export` passes
that map to the same process's resolver. Loaded split-module names and exact
integer bases take priority; a missing export in an existing module does not
redirect. The V3 callback facade explicitly injects this policy; the generic
resolver/callback API does not choose it.

## Menu to started paused world

Choose actions from fresh `wire.world_status()` and native control results.
The following are operations, not an unconditional script to rerun on recovery:

1. Install the Interface stage. If it is ready and no world exists,
   `wire.enter_world(options=0)` queues the native factory/entry path.
   Keep its returned request evidence and observe the same transition through
   `wire.world_status()`. Submission is not proof of an in-game world.
2. For explicit Options ownership use `wire.create_options()`,
   `wire.submit_options(options)`, and `wire.destroy_options(options)`. Only destroy
   a known-owned, unsubmitted object. Submission transfers ownership.
   `v3_options` additionally provides native full-object construction and base
   settings assignment; it requires explicit `mode`, `flag`, and `dispatch`.
   Do not fabricate a settings buffer or shallow-copy a native Options object.
3. Continue `v3_runtime.install(client, process)` on the same retained objects.
   Observe Session readiness; an installed slot alone is not worker readiness.
4. Discover the intended full Country ID from live queries. Use
   `v3_control.start(client, StartAction.SELECT_AND_START_PAUSED, country_id,
   process=process)` once when selection and start are both intended.
   `SELECT_COUNTRY`, `START_PAUSED`, and `OBSERVE_START` are separate actions.
   Read the original callback, then observe `game_started` independently.
5. If native lobby synchronization/Ready remains necessary, use
   `v3_control.finalize_play(client, country_id, process=process)` for the
   already-controlled Player. It neither selects nor starts. Later use
   `observe=True` and inspect actual GUI/lobby and country references.

## Country and observer control

These live wrappers use the original native console dispatcher for mutation:

- `v3_player_control.observe_player(client, process=process)` copies selected
  and controlled country references plus the observer bit. They are not the
  same reference and must not be substituted for each other.
- `resolve_tag(client, tag, process=process)` resolves the native three-byte tag
  to a checked full Country ID without switching.
- `switch_country(client, tag, process=process)` sends the real registered
  `tag` operation once. Observe player state afterward.
- `toggle_observer(client, process=process)` is a toggle, not an absolute setter.
  First read whether a toggle is needed; do not repeat it blindly.

`v3_ai.observe_country_ai(client, country_full_id, process=process)` copies an
existing actor without creating it. `disable_ai(client, selector, ...)` and
`enable_ai(client, selector, ...)` retain distinct native TAG/`all`/`self`
semantics. `all` refers to the current cached actor cohort, not every future
actor or a global manager override. `observe_cached_ai` copies that exact native
cache, including duplicate/error rows. Reobserve actual actor/manager state;
console callback success alone is not proof of the intended AI state.
