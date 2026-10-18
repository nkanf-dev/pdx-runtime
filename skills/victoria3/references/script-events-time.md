# Script, real event choices, and time

Advanced raw API reference. Ordinary gameplay uses `V3Game` and the
[game-level methods](gameplay.md). These details remain available for custom
compositions and diagnostics; they are not required player setup.

## Native Script is not console text

`v3_script.trigger/effect/value/typed_value(client, text, scope, ...)` invoke the
original native parser and runner through the existing Interface callback.
Use actual game-native syntax. Keep `error`, `scope_valid`, `parsed`, `executed`,
and parser diagnostics distinct. `effect` submits once; independently observe
its real world change. A missing/partial diagnostics message is not a reason
to replay a potentially executed effect.

Discover live Script scope types with `enumerate_scope_types` or
`resolve_scope_type`. These IDs are not DataFactory types. `Scope.id(type,
full_id)` zero-extends a complete uint32 native object ID; `Scope.raw(type,
payload)` preserves an exact native payload without inventing validity.
`SavedScope` uses a genuine native interned name and native scope reference.
A borrowed `context` is a live constructed native context, not a storage buffer.
Supplying saved scopes inserts into that context's native named map; it is not
an automatic isolated copy or rollback.

For repeated numeric/Trigger/typed evaluation, `create_context` and `parse`
return operations with explicit `.owner` objects (`NativeContext` and
`ParsedValue`). Complete the original operations before use. Retain them across
evaluations; queue and complete each explicit native `.release()` once when
finished. No shallow copies or pending-owner destruction. The convenient parsed
owner helper does not create a retained Effect owner; low-level exports remain
public for a proven composition.

Native `CFixedPoint`/typed values preserve raw bits. Do not assume all Clausewitz
versions, numeric types, or games have one decimal scaling or Script ABI.
Script results reflect the engine's context and rules. Raw memory and native
calls remain separate caller compositions with explicit ABI, context, and ownership.

## One admitted six-hour partial tick

Start with a genuinely started, paused, initialized world. Set pause through
`v3_lifecycle.set_paused(client, True, process=process)` when needed and finish
its original receipt. Do not treat setting a date as equivalent to simulating.

```python
from pdx_runtime import v3_time_step

step = v3_time_step.submit(client, process=process)
baseline = step.submission.read()
```

If `baseline` is pending, read this same submission later. `accepted` only means
tick admission; do not submit another tick because the date has not moved yet.
After a confirmed baseline, submit one fresh boundary observation explicitly:

```python
from pdx_runtime import v3_time_step

boundary_query = step.observe()
boundary = boundary_query.read()
if boundary is not None:
    observation = v3_time_step.compare(baseline, boundary)
```

A pending `boundary_query.read()` reads only that callback; it never queues
another observation. Once complete, another `step.observe()` is an explicit new
snapshot, not another tick. `compare` requires the same native binding, date+6,
a settled paused boundary, and worker readiness. `pending`/`reason` explain why
that boundary is not yet proved. Raw counters are not completion identities.
An unexpected delta or changed world is not permission to claim one clean tick.

After a proved tick, another intentional `submit` admits the next tick. Compose
as many steps as the requested gameplay needs; use natural weekly/monthly
settlement where required. Keep the same pending state across agent turns.
The caller composes time progression; the SDK adds no human approval per tick.
Companion play and autonomous decisions remain upper-layer choices. Native
followups and autosave policy remain unchanged.

## Event and popup response

Do not construct a substitute event, fabricate an option, or retain a uniquely
owned GUI element across arbitrary callbacks/ticks. Discover actual pending
sources and option metadata with the native DataFactory. For a Creation list,
use `evaluate_element` / `invoke_element_action` to freshly produce and consume
an option within one callback. Supply the original nonzero source `world`,
actual input types, and native enabled condition. For genuine pointer arrays,
use `PointerArraySource` only with its proven original owner-thread/exclusive
source contract; do not guess the array header or a converter type.

Read the chosen original option's receipt, then verify popup closure and the
resulting game state. A source-retry result means refresh the original source,
not write an enabled bit. Progress native time after the choice where required.

## Observe effects, not just receipts

For research, enactment, mobilization, transfers, and construction, capture
before-state identity, submit once, settle the original request, progress enough
native ticks, and query the same domain freshly. Record both intermediate and
final state. A later outcome can remain pending even after a valid admission.
`v3_diagnostics.status/records` copy the existing native log store; they do not
create a viewer, attach sinks, or guarantee all categories were subscribed.
Continue pages with `next_sequence` and account for GAP/RESET/PRUNED/truncation.
Log sequence IDs are not ownership proof for a callback or command.
