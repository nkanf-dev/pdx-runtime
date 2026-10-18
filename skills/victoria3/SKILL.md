---
name: victoria3
description: Play and inspect Victoria 3 through V3Game. Use for attaching or launching, starting or loading campaigns, country overview, budget and markets, research, construction, laws and institutions, diplomacy and treaties, armies and fleets, readable events, normal time, saves and explicit exit. Game-level methods are the default; complete raw process, Script, DataFactory, command and callback APIs remain available in focused references.
compatibility: Windows x64; installed pdx_runtime package with its packaged native library and the Victoria 3 adapter.
---

# Victoria 3

Use `V3Game` for normal play. It handles native connection, discovery, paging,
real key resolution, command ownership, completed cleanup and original pending
operations. The API does not select a national strategy or require per-action
approval. Shared raw Windows/native contracts are in the `pdx` skill.

## Install the delivered package

Use Windows x64 and 64-bit Python 3.11+. Build the native DLL and install the
current source into the Python environment used by the Agent. The repository
README gives the build and installation commands; no published release is required.
The wheel includes its native DLL and both skills. A portable ZIP can instead
be extracted and used from its root, or with that root on `PYTHONPATH`.
Use the same Python environment for installation and the Agent.
`pdx-runtime` is a raw PID-based session CLI, not a high-level V3 command shell.
The normal entry below uses the installed Python API, not external experiments.

## Join the running game

```python
from pdx_runtime.v3_game import V3Game, PendingOperation

game = await V3Game.attach()
while isinstance(game, PendingOperation):
    game = await game.resume()
```

If several games are running, attachment returns real process choices through
`ProcessSelectionError`; select one with `pid=`. To start a new process instead,
use `await V3Game.launch()`. Attachment does not start a new campaign or replay
current research. Existing integrations can use `V3Game(session, process=process)`
without creating a second connection.

## Choose the task and its source

Read the view needed for the current decision. Open details on a selected
country, war, formation or queue position instead of reading every domain first.
Use native UI data bindings to interpret amounts, relationships and actions;
display names and queue positions are not stable object identities.

```python
budget = await game.economy.budget()
construction = await game.construction.queue(start=0, limit=20)
```

Read actual state, inspect candidate costs/constraints, choose a game action,
then read its effect. Those are game-level calls, not player-managed native
plans, owners, type IDs or Validate/Submit steps. Unknown fields retain reasons;
acceptance is not construction completion, law passage or ship arrival.

## Load only the detail needed

| Task | Reference |
| --- | --- |
| Attach/launch, campaign, save/load, menu, detach versus quit | [Entry and lifecycle](references/connection-control.md) |
| All country/economy/research/construction/politics/diplomacy/military methods and examples | [Game-level methods](references/gameplay.md) |
| Read original events, choose an option, run normal time and interrupt | [Events and time](references/events-time.md) |
| Raw object/DataFactory reads, typed values and source lifetimes | [Query composition](references/queries-data.md) |
| Raw commands, native Script and gameplay effect observations | [Action composition](references/commands-gameplay.md) |
| Raw partial ticks, original Script/events and diagnostics | [Script/event/time composition](references/script-events-time.md) |
| Raw save-task, VFS and native shutdown ownership | [Save/exit composition](references/saves-shutdown.md) |
| Complete raw query signatures and result contracts | [Query API lookup](references/lowlevel-query-api.md) |
| Complete raw action/Script/console/access/Session signatures | [Action API lookup](references/lowlevel-action-api.md) |
| Complete raw install/control/lifecycle/options/tick signatures | [Lifecycle API lookup](references/lowlevel-lifecycle-api.md) |

## Pending work and leaving

A genuinely unfinished operation can return `PendingOperation`. Resume that
object with `await pending.resume()`; it retains the original operation inside
the SDK. Normal success returns game data, not an ownership ledger. A game-rule
rejection reports its reason and does not prevent unrelated work.

`await game.detach()` releases only facade-owned SDK resources; the game keeps
running. `await game.quit(save=False)` explicitly requests native game exit and
observes the original process exit. An async context manager detaches, not quits.
See the lifecycle guide for the distinct save-before-menu/load/quit semantics.

`game.raw` keeps the supplied session, native client, process and public SDK
modules available. Custom raw composition retains its real ABI/context/lifetime
requirements; it is not the default player workflow. Use the relevant game-level reference for source scope,
optional detail reads and available actions.
