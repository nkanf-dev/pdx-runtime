# Exact-address ordinary Win64 calls

The public `pdx_runtime.native_call` module is the raw ABI layer. It does not
infer signatures, find targets, create game guards, or make arbitrary addresses
safe. Supply an explicit adapter context for dispatch and completion; no game
queue is selected implicitly. The raw-memory worker is not an engine-call thread.

## Calls and batches

- `submit(client, target, arguments=(), *, context,
  adapter="pdx_runtime.dll", process=None)` queues one exact-address call
  through that context. Retain the returned request and its original query.
- `submit_batch(client, calls, *, context, adapter="pdx_runtime.dll",
  process=None)` submits a fixed sequence of `(target, arguments)` pairs. It is not a plan,
  dependency solver, transaction, or rollback facility.
- `float32_bits(value)` and `float64_bits(value)` encode explicit IEEE argument
  bits. They do not convert engine fixed-point numbers.
- `encode_request(target, arguments=(), *, request_address)` packs a request
  into caller-owned storage at that exact process-local base.
- `encode_batch(calls, *, request_address=0)` packs a batch; patch any template
  pointers to the live allocation before dispatch.

The native helper uses ordinary Win64 positional slots. Slots 1..4 populate
both matching GP and XMM registers; later slots occupy eight-byte stack slots.
Pass hidden output/`this` pointers and ABI-required by-reference structs explicitly
in their correct positions. This is not `__vectorcall`. Verify the complete
native signature, argument widths, and supported return handling before use.
`NativeCallResult.rax` and `xmm0` retain raw return state; an integer-looking bit
pattern is not automatically a pointer or a domain success flag.

## Explicit callback context

`pdx_runtime.native_callback.CallbackContext` has six required members:

| Member | Adapter responsibility |
| --- | --- |
| `client` | The exact client object passed to submit, not merely an equal PID. |
| `dispatch(callback_address, result_address)` | Dispatch the original callback and return the adapter's raw acknowledgement. |
| `status_getter()` | Return `status="ok"` and `completed_request_id` after successful sampling; optional `error`, `request_id`, `callback_error` keep their defined request scope. |
| `process_factory()` | Open the owned target process only when no process was supplied. |
| `process_matches(process)` | Confirm target identity with exact `True`, without side effects; identity is not inferred from client attributes. |
| `classify_submission(raw)` | Return protocol-proved `CallbackDisposition`, not a guess from an error/status word. |

A disposition is `accepted` with a positive integer `request_id`, `rejected`
with proof of non-delivery, or `unknown`. A proved rejection can have error 0.
Unknown/malformed acknowledgements and dispatch exceptions retain `error.query`.
Callback errors belong only to their matching latest request, not every earlier
completion. Status sampling and copying result bytes are separate observations.

The optional keyword-only `export_resolver(process, adapter, symbol)` is not a
seventh required member. Its default `None` uses the same process's strict export
lookup, without a distribution/game alias map. A supplied resolver selects its
policy explicitly; the original client/process identity and ownership remain.
The single/batch default adapter name is canonical `pdx_runtime.dll`.

## Example with an injected host adapter

`host` is supplied by the application, not an SDK-provided backend. It exposes
the six members above plus `call_adapter` (an already-loaded module name/base)
and `process` (a caller-owned process, or `None` to use its factory). This local
function wires those real callbacks; it does not implement or simulate a backend.
Inputs remain a proven `target_address` and correctly encoded `argument_slots`.

```python
from pdx_runtime import native_call
from pdx_runtime.native_callback import CallbackContext


def submit_host_call(host, target_address, argument_slots=()):
    client = host.client
    context = CallbackContext(
        client=client,
        dispatch=host.dispatch,
        status_getter=host.status_getter,
        process_factory=host.process_factory,
        process_matches=host.process_matches,
        classify_submission=host.classify_submission,
    )
    return native_call.submit(
        client, target_address, argument_slots,
        context=context, adapter=host.call_adapter, process=host.process,
    )
```

The returned handle's `read()` samples once. If it returns `None`, retain that
same handle; reading it later observes the original callback, without resubmitting
the target. If submission raises, retain any `error.query` instead of issuing the
mutation again. A supplied process remains caller-owned; an internally opened
process is released with completed/proved-rejected callback storage, not pending
input.

A raw batch stops on returned validation error. A native exception/fault can
leave a possibly executed prefix. Do not replay that prefix to obtain later
results. Dependencies need explicit caller-owned output addresses kept live
through completion. A batch never grants object lifetime, access scope, TLS,
or game-specific eligibility.

## Game placement stays explicit

Game queue and access-scope composition belongs to its installed adapter.
Victoria 3 usage stays in the
[Victoria 3 action reference](../../victoria3/references/lowlevel-action-api.md);
the shared call API does not select that game's context implicitly.
An entered guard must be left on the same native stack before callback return.
Never split native Enter/Leave across remote threads or separate callbacks.
