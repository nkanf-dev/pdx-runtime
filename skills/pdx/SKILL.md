---
name: pdx
description: Use PDX's shared Windows x64 process, memory and native-call APIs when working with NativeBridge, RemoteProcess, RuntimeSession, exact-address Win64 calls, loader continuation, or custom game adapters. For playing Victoria 3, use the victoria3 skill and its game-level API first; this skill provides the complete raw fallback contracts.
compatibility: Windows x64; installed pdx_runtime Python package and its packaged native library.
---

# PDX

For Victoria 3 gameplay, load `victoria3`. Its game-level API handles discovery,
connection, metadata, native command ownership, cleanup and original pending
operations internally. Companion play, autonomous play and strategy remain the
caller's choices.

This skill covers the shared raw layer. Windows process access and ordinary
Win64 ABI contracts are reusable. Engine layouts, Script scopes, Session hooks
and VFS operations still belong to each game adapter; the Victoria 3
implementation does not establish unchanged HOI4 or CK3 compatibility.

## Raw lookup

| Need | Reference |
| --- | --- |
| Process creation, memory, exports, allocation, protection and loader continuation | [Process primitives](references/process-primitives.md) |
| Ordinary Win64 slots, calls, batches and explicit outputs | [Native calls](references/raw-calls.md) |
| Owned/borrowed storage, original callback continuation and cleanup | [Ownership and continuation](references/ownership-recovery.md) |
| Full public signatures, results, parameters and native contracts | [Shared API reference](references/raw-api.md) |

Raw memory and native calls remain available. A custom native composition needs
its real ABI, execution context and ownership; the SDK does not infer these from
an address. A pending call retains its original operation rather than replaying
a mutation. These are execution facts, not an approval or gameplay policy.

Exact Python imports and artifact names such as `pdx_runtime`, `RuntimeSession`
and `pdx_runtime.dll` are unchanged. The caller supplies the target when using
the raw connection API; high-level game attachment can discover it.
