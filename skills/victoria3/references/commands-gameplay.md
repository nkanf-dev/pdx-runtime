# Registered commands and gameplay actions

Advanced raw API reference. Ordinary gameplay uses `V3Game` and the
[game-level methods](gameplay.md). These details remain available for custom
compositions and diagnostics; they are not required player setup.

The adapter exposes the original game's command schema and validation results.
Validation returns the native bool/reason, not an added business policy or
approval gate. The SDK does not expose a universal `enact_law`, `declare_war`,
or other invented business API.
Game actions compose the native command registry, DataFactory, Script, console,
and public raw primitives. Adapter results retain native eligibility and
original execution semantics.

## Discover and construct a real command

1. `v3_commands.enumerate_commands(client, process=process)` lists startup
   registrations with a live factory. A listed command is not proof its action
   is legal in the current world or has been dynamically exercised.
2. Resolve actual type/field/enum tokens through `resolve_token` / `token_name`.
   Keep numeric token IDs and native widths. Do not guess a parameter block from
   a command name. Use a proven type-specific schema or original serialized
   command. `decode_command`, `encode_tree`, `token_ids`, and `name_tokens`
   preserve original typed native CBinLexer data, including order/duplicates.
3. `create(client, type_token, parameters=None, actor_id=None, world=0,
   process=process)` uses the actual factory; when parameters/actor are supplied
   it uses the native reader. `actor_id` is the separate game-command root ID,
   not a field to add indiscriminately to non-game Session commands.
4. Capture `world` from the original source before command production. Raw
   `world=0` behavior remains public; it is not permission to retain a command
   across world replacement.

For the example, supply proven `type_token`, native `parameter_tree`, and
`source_world`. The request reads once and does not wait or auto-submit.

```python
from pdx_runtime import v3_commands

create_query = v3_commands.create(
    client, type_token, parameters=parameter_tree,
    world=source_world, process=process,
)
create_result = create_query.read()
```

Native reader success is not a total-parse/required-field guarantee: the adapter
exposes no such Reader status. Inspect native serialization, validation, and
actual effect. Do not invent that missing status in a wrapper or documentation.

## Explicit owner lifecycle

Only after a non-error, nonnull completed create/clone result:

```python
from pdx_runtime import v3_commands

command = v3_commands.NativeCommand.from_result(
    client, create_result, process=process
)
validation_query = command.validate(capacity=4096)
validation_result = command.complete()
```

`complete()` reads only this owner's original pending query. If `None`, call it
again later. Inspect `validated`, `error`, `reason`, and `reason_truncated`.
A false native validation is a domain rejection, not a transport failure.
After validation completes with ownership still known, `command.submit(flags=4)`
queues the original Session receiver. Read with `command.complete()` until the
original operation settles. `accepted` is not final gameplay effect.

The owner keeps `.attempt`, `.pending`, `.command`, and `.world` public.
Pending/unknown delivery blocks competing owner operations. A consumed command
must not be submitted or destroyed again. `clone()` creates a separate native
owner; `serialize()` preserves native output; `destroy()` is only for a known
still-owned command. No destructor auto-dispatch or shallow object memcpy occurs.
For `v3_data` command carriers, use the original shared carrier clone contract;
a borrowed primary pointer is not automatically caller-owned.

## Construction direct path

Discover fresh actor/state IDs and a real BuildingType pointer. Candidate
existing buildings do not prove buildability. `v3_targets` is a raw-read helper,
not a locked native validator. Recheck eligibility in the actual native scope.

`v3_control.validate_construction(client, actor_id, state_id, building_type,
carrier=carrier_base, process=process)` runs the native validator. `validated`
means execution finished; `raw_low`/`raw_high` remain exact uninterpreted native
words, not guessed booleans. `carrier_base` is the actual live module base from
installation, not a cached address from another process.

Bind the V3 wire view to the existing connected client; this opens no connection
and creates no new control owner:

```python
from pdx_runtime.v3_wire import V3Wire

wire = V3Wire(client)
```

`wire.queue_construction(actor_id, state_id, building_type)` submits one native
level. Observe `wire.construction_status()` and the original Session
completion, then read the real queue/building state after required ticks.
A returned submission ID is not completed construction or a future reservation.

## Research return values

`game.research.start`, `enqueue` and `dequeue` keep known receiver acceptance
with a later queue observation as `ResearchSubmittedChange`. A completed submit
that has a native error,
unknown ownership, receiver rejection or unavailable post-state returns
`ResearchNativeResult` instead. It carries the exact `native_result`,
`native_error` and `native_outcome`, plus available `before` and `submit_before`
observations. `accepted=None` means receiver acceptance is not confirmed; the raw
native accepted word remains in `native_result`. Unobserved queue/current/change
fields stay `None` with `reason`, not false, empty or a claimed success.

Cleanup diagnostics are supplementary and do not replace the native result.
A truly unfinished operation still keeps its original pending handle/task.
Continue that operation; do not resubmit it or destroy an unknown owner.

## Gameplay evidence plan

| Intent | Read before action | Verify after native submission and time |
| --- | --- | --- |
| Construction, subsidies, auto-expansion | Owned State/Building, original queue and eligibility | Exact queue/flags/levels, construction progress and completion |
| Tariffs, consumption tax, wages | Controlled Country, native Goods/BuildingType and fiscal state | Intended policy/value plus original native result |
| Law, movement, institution, research | Actual law/technology definitions, Country state, native conditions | Correct active law/queue identity and progress across required weekly ticks |
| Diplomatic play, treaty, mobilization | Real countries, relations, play/war/formation state | Created play/proposal, native response, or actual mobilized state |
| Ship transfer or rename | Current full Ship ID and original source/destination owner | Actual name/formation after natural settlement, not only acceptance |
| Events and popup choices | Fresh source owner, world, original option/condition | Selected original option, resolved popup, resulting state after ticks |

Native preconditions can change through game actions and time progression.
Adapter results report the engine's observed conditions; queued/accepted work is
distinct from a later observed state. Raw memory and native-call composition
remain available with caller-provided ABI, execution context, and ownership.

## Read the original received treaty terms before responding

For a received proposal, freshly discover the actual proposal/article refs and
parties. Copy its original native serialized parameters before acceptance.
Preserve the original UTF-8 bytes, newline content, native byte lengths, type
tags, and completeness/truncation indicators. A localized summary, caller's
submitted text, or a guessed template is not the received treaty body.
Use the existing native serialization/raw-call composition with its proven
parameter schema; there is no universal `read_treaty_body` SDK convenience API.
Only after inspecting those received terms, submit the intended original
response once. Requery the same parties/ref and native draft-to-in-force state,
then verify any later tick-dependent result separately. A native serializer for
one article shape is not proof that every possible article/value variant has
the same layout. Continue the same original owner and release all consumed or
known-owned storage without recreating the proposal on a read failure.

## Console remains a distinct native interface

`v3_console.enumerate_commands` copies native registration metadata, including
restricted entries. `execute(client, text, ...)` uses the engine's tokenizer and
dispatcher; `execute_arguments(client, arguments, ...)` preserves explicit string
elements. Neither bypasses native multiplayer/ironman or release restrictions.
`error` is adapter status; `native_success` and `message` are the original result.
Truncated output can follow an already executed mutation: never rerun it just
for a larger message. `invoke_exact` and `invoke_object` use different proven
native descriptor ABIs; they are not interchangeable. Native callback registration
requires a live native callback/context, not a Python function pointer.
