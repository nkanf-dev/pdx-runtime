# Query objects and the live DataFactory

Advanced raw API reference. Ordinary gameplay uses `V3Game` and the
[game-level methods](gameplay.md). These details remain available for custom
compositions and diagnostics; they are not required player setup.

## Native object pages

Use `v3.submit(client, country_id, process=process)` for the focused Country/GDP
query. `QueryResult.valid` checks the native error; `gdp_raw64` stays exact.
Do not replace engine fixed-point numbers with guessed IEEE or decimal scaling.

`v3_objects.enumerate_objects(client, domain, *, start_index, max_items,
capacity=None, process=None)` returns a nonblocking page handle. `read_ids`
resolves caller-supplied full IDs. A successful batch can still contain invalid
individual records. Inspect each record's native errors and resolved ID.

```python
from pdx_runtime import v3_objects

countries_query = v3_objects.enumerate_objects(
    client, v3_objects.Domain.COUNTRY,
    start_index=0, max_items=32, process=process,
)
countries_page = countries_query.read()
```

If pending, read the same `countries_query` later. On a completed page use
`next_index` and `slot_bound` for the next explicit page. Do not use a small fixed
page count as a claim to cover the whole world. Copy IDs and scalar data;
pointers in snapshots are borrowed observations, not leases across ticks/loads.
`ObjectsResult.world` is the captured source world for dependent operations.

`Domain` includes COUNTRY, STATE, BUILDING, POP, MARKET, MILITARY_FORMATION, WAR,
DIPLOMATIC_PLAY, CHARACTER, LAW_DEFINITION, TECHNOLOGY_DEFINITION, INTEREST_GROUP,
SHIPPING_LANE, COMPANY, and SHIP. Specialized entry points include:

| Data | Actual API families |
| --- | --- |
| Pop counts and State/Country relations | `enumerate_pop_details`, `read_pop_details` |
| Goods price and orders/trade detail | `enumerate_market_goods`, `read_market_goods`; explicitly choose `details=True` when needed |
| Law/technology native keys | `enumerate_law_definitions`, `read_law_definition_indices`, `enumerate_technology_definitions`, `read_technology_definition_indices` |
| Interest group, company, ship copied names/relations | `enumerate_interest_group_details`, `read_interest_group_details`, `enumerate_company_details`, `read_company_details`, `enumerate_ship_details`, `read_ship_details` |

Full object IDs, static definition indices, native type pointers, and market
goods pointers have different contracts. Do not substitute one for another or
claim a fixed goods count from a previous world.

## Discover native DataFactory metadata

`v3_data.enumerate_types(client, start=0, capacity=32, process=process)` returns
live dynamic type metadata. Page with the completed result's `next` and `total`.
For a discovered type, `enumerate_functions(client, type_id, start=0,
capacity=32, process=process)` copies native argument/return metadata.
These callbacks are raw composition evidence, not a universal direct-getter ABI.
A DataFactory type ID is not a Script scope ID or command token.

Inputs below are a current `country_id` and `country_type_id` discovered from
the live Country metadata. `Country.GetGDP` is native data-system syntax.

```python
from pdx_runtime import v3_data

binding = v3_data.ObjectBinding.country(country_type_id, country_id)
gdp_query = v3_data.evaluate(
    client, "Country.GetGDP", [binding], process=process
)
gdp_result = gdp_query.read()
```

Read native `error`, `kind`, `raw_bits`, `snapshot`, and `native_type_name` before
using `value`. The scalar/string/object decoding only covers proven forms.
A successful evaluation can own `value_handle` independently of callback bytes.
After all consumers finish, queue exactly one `gdp_query.release_value(
process=process)` and read that release handle to completion. Do not call release
again because its first receipt is pending. `snapshot` copies a retained value
without rerunning its original getter.

`ObjectBinding.country/state/pop/market/building(type_id, full_id)` use genuine
native wrappers. A raw `ObjectBinding(type_id, object)` expects a real native
CData wrapper, not an arbitrary gameplay pointer. Retained bindings use an
existing adapter `value_handle` with `retained=True`.

## Collections and explicit actions

- `list_count` and `list_element` apply only to supported native Creation
  providers. An element is a separately owned value; release each owner once.
- `invoke_action(client, expression, input, bindings=(), ...)` and
  `invoke_property_action(client, expression, bindings=(), ...)` are explicit
  native actions. `evaluate` remains read-only; it does not execute an action
  because the expression happens to name one.
- `TypedInput` keeps native kind explicit: `boolean`, integer width variants,
  `float32/float64`, `fixed`, `string`, `utf8_string`, and `retained` are distinct.
- For transient source elements, use `evaluate_element` or
  `invoke_element_action` to produce and consume the fresh element in one native
  callback. Capture nonzero `world` from the original source **before** queueing;
  never stamp a stale pointer with a later current-world value.
- `PointerArraySource` / `pointer_array_count` /
  `evaluate_pointer_array` require the proven original owner, array header,
  dynamic element type, Interface thread, and exclusive owner gate. They do not
  fabricate a Creation provider or grant a lease to uniquely owned GUI objects.
  Inspect the SDK contracts before use; do not guess offsets from another game.
- `command_value` copies a retained command carrier and captured world. It does
  not clone, validate, or submit it. Keep the source value alive until a genuine
  clone/consumer completes.

On a load/world replacement, rediscover IDs, source owners, and dynamic context.
A Python copy of a borrowed pointer does not extend the native object's lifetime.
