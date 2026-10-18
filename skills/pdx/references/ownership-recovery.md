# Ownership and recovery

Track these independent facts for every operation:

1. Was delivery accepted, rejected, or unknown?
2. Did this exact native callback finish?
3. Did the engine accept the domain operation?
4. Did a fresh observation show the intended effect?
5. Which process allocation and native object references are still owned?

## Callback storage

V3 uses the public `v3.CallbackResult` contract. `read()` samples completion once
and returns `None` while pending. After completion it copies the result and frees
callback storage; the saved Python result remains reusable. This release is not
release of a separate engine Any, command, Script context, or save-task reference.
`close()` does not cancel a callback and refuses to free pending input.

A delivery exception can expose `error.query`. Keep that exact object, its
client, process, module owner, and input storage. Unknown delivery is not known
rejection. `query.recover(request_id)` binds only an independently established
ID belonging to that operation. A queue's latest ID has no opaque identity;
copying it without ownership evidence can bind another caller's work.
If ownership cannot yet be proved, retain storage and gather evidence. Do not
replay a mutation, free its input, or invent a new ID.

## Recover and continue

| Situation | Next action |
| --- | --- |
| A pure read fails transiently | Make a bounded fresh observation; retain earlier ownership records. |
| `LibraryLoadPending` | Continue its original `.attempt.resume()`; do not start another load. |
| Known callback ID, still pending | Read the same handle on a later turn. |
| Session enqueue completed | Observe the original Session completion; enqueue return is not target completion. |
| Native command rejected | Read its native reason; keep or destroy only a still-owned command. Change actual game conditions if needed. |
| Callback accepted, effect not yet visible | Advance required native time, resolve real blocking events, then read fresh state. |
| Native destructor outcome unknown | Keep the original owner and release query; do not destroy again. |
| World/load transition | Finish valid outstanding work; discard borrowed pointers and rediscover new-world IDs/context. |

Run independent usable roles while another role is deferred. Reobserve native
readiness rather than repeatedly loading DLLs or reinstalling healthy hooks.
Do not add a human approval for each tick. Continue within the user's game/task
scope, retaining a clear operation/effect record for later turns.

## Cleanup order

1. Settle original callback/loader deliveries and dependent operations.
2. Explicitly release retained engine values, Script owners/contexts, and known
   owned commands/save-task references through their original native APIs.
3. Observe each release receipt before freeing storage it still uses.
4. Close process/client handles after their native consumers finish.

Do not use a blanket `finally: free(...)` on memory passed to an asynchronous
callback. Keep pending objects in a persistent Python namespace. A Python
interpreter restart loses the in-memory ownership journal; a new connection is
not a continuation of those lost owners. Plan persistence before long runs.
Native shutdown is a game-adapter operation, not `RemoteProcess.close()` or
`RuntimeSession.close()`. Keep the original process handle until exit is proved.
