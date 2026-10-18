# Low-level PDX / Victoria 3 query API

This reference lists declarations from the named `pdx_runtime` modules. For ordinary player-facing tasks, prefer `pdx_runtime.v3_game.V3Game`; use these lower-level calls only when explicit raw data, query handles, or custom composition is needed. This catalog does not claim complete coverage of Victoria 3 native APIs.

## Shared query contract

- The callback-based query helpers return a handle. `pdx_runtime.v3.CallbackResult.read()` samples completion once: it returns `None` while pending and the decoded object after completion. It does not wait or resubmit.
- A query handle retains its result allocation until completion is read. If the helper created its own `RemoteProcess`, completion releases that allocation and closes the owned process handle. If the caller supplied `process=`, the helper frees its allocation but leaves that borrowed process open.
- If queue delivery raises with an attached `error.query`, retain that exact handle and its storage. Do not infer rejection from a transport error. `recover(request_id)` only binds a request ID established by inspection; it never resubmits.
- `BridgeError`, `QueryExecutionError`, `ValueError`, and native `error` fields are separate failure channels. Check the returned result fields and per-record errors. A successful batch can still contain invalid records.

## Query and data contracts

- `pdx_runtime.v3.QueryResult.gdp_raw64` and raw `*_raw64` fields are copied 64-bit integers. These helpers do not apply a display-unit conversion. The API does not declare GDP, prices, convoy cost, or effectiveness units.
- `pdx_runtime.v3_objects` returns decoded `ObjectsResult` pages. `start_index`, `max_items`, `capacity`, `returned_count`, `checked_count`, `scanned_count`, and `next_index` are page/scan metadata. For enumeration, continue at `next_index` only in the same live world; each page is a new native snapshot. `truncated` means scan slots remain, not that another real object is guaranteed. ID reads retain per-record native errors. Native pointers in record fields are borrowed snapshots, not owned Python resources.
- `Domain` distinguishes full-ID object domains from the signed definition-index domains. Definition ordinals are not generation IDs. Object validity and relationship validity have separate error fields. Text-bearing detail records contain copied UTF-8 bytes/strings and option metadata.
- `pdx_runtime.v3_data` is the live engine factory/data interface. `DataQuery.read()` yields `DataResult` or `ActionResult` (depending on operation), or `None` while pending. `DataResult.value` decodes only the source-supported scalar/string/object/list/command forms; otherwise it preserves raw bytes. `value_handle` is an adapter-owned retained native value: release it once with `release()` or `DataQuery.release_value()`. Callback storage and retained-value ownership are distinct.
- `DataResult.total` is operation-specific. For `LIST_COUNT` it is an element count; for snapshots it is payload/string bytes. A list count does not freeze a game-owned collection. `invoke_action` is the explicit action path; `accepted` is native acceptance, not proof of a visible world change. `FixedPoint` preserves integer bits; this API does not apply an assumed decimal scale.
- `pdx_runtime.v3_targets` reads already analyzed process fields through the supplied read callable. It returns raw pointers or dictionaries and does not call a native resolver, lock a snapshot, or validate a candidate for later action. Raw read failures and invalid IDs raise; iterator `stop` is an exclusive scan boundary.
- `pdx_runtime.v3_diagnostics` exposes copied records and sequence/coverage metadata. Flags and count fields are the native snapshot values; `gap`, `reset`, and `output_truncated` are derived from those fields.
- `pdx_runtime.v3_ai` exposes raw AI-state/cache fields and known-field masks. The `*_raw` and pointer fields are not normalized. Cache completeness/truncation/row errors are distinct properties.
- `pdx_runtime.v3_player_control` returns copied player state. `tag` decodes the returned bytes; country selection and observer toggles are callback operations, not evidence by themselves that later simulation effects completed.


## Callable signatures

Signatures below follow declarations in the named source modules. A missing `->` annotation means the source has no return annotation; no return type is inferred. Dataclass constructor signatures list declared fields and defaults.

### `pdx_runtime.v3`

```text
pdx_runtime.v3.QueryExecutionError(result: object, winerror: int, request_id: int)
pdx_runtime.v3.QueryResult(size: int, error: int, requested_id: int, resolved_id: int, country: int, fallback: int, gdp_raw64: int)
pdx_runtime.v3.QueryResult.from_bytes(cls, data: bytes) -> QueryResult
pdx_runtime.v3.QueryResult.valid(self) -> bool
pdx_runtime.v3.QueryResult.gdp_raw64_hex(self) -> str
pdx_runtime.v3.CallbackResult(client: NativeBridge, process: RemoteProcess, callback_address: int, result_address: int, owns_process: bool, result_size: int, decode_result: Callable[[bytes], object], *, status_getter: Callable[[], dict] | None = None)
pdx_runtime.v3.CallbackResult.read(self)
pdx_runtime.v3.CallbackResult.recover(self, request_id: int) -> CallbackResult
pdx_runtime.v3.CallbackResult.close(self) -> None
pdx_runtime.v3.CountryQuery(client: NativeBridge, process: RemoteProcess, callback_address: int, result_address: int, country_id: int, owns_process: bool)
pdx_runtime.v3.submit_callback(client: NativeBridge, initial_bytes: bytes, *, adapter: str | int, callback_symbol: str, decode_result: Callable[[bytes], object], process: RemoteProcess | None=None, handle_factory: Callable | None=None) -> CallbackResult
pdx_runtime.v3.submit(client: NativeBridge, country_id: int, *, adapter: str | int='pdx_v3_query.dll', process: RemoteProcess | None=None) -> CountryQuery
```

### `pdx_runtime.v3_objects`

```text
pdx_runtime.v3_objects.Country(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, gdp_raw64: int)
pdx_runtime.v3_objects.Country.valid(self) -> bool
pdx_runtime.v3_objects.State(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, owner_id: int, reserved: int)
pdx_runtime.v3_objects.State.valid(self) -> bool
pdx_runtime.v3_objects.Building(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, state_id: int, levels: int, type_pointer: int)
pdx_runtime.v3_objects.Building.valid(self) -> bool
pdx_runtime.v3_objects.Pop(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, type_pointer: int)
pdx_runtime.v3_objects.Pop.valid(self) -> bool
pdx_runtime.v3_objects.Market(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, owner_id: int, reserved: int)
pdx_runtime.v3_objects.Market.valid(self) -> bool
pdx_runtime.v3_objects.MilitaryFormation(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, owner_country_id: int, owner_country_error: int, owner_country_pointer: int)
pdx_runtime.v3_objects.MilitaryFormation.valid(self) -> bool
pdx_runtime.v3_objects.War(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, diplomatic_play_id: int, diplomatic_play_error: int, diplomatic_play_pointer: int, attacker_leader_id: int, defender_leader_id: int, attacker_leader_error: int, defender_leader_error: int, attacker_leader_pointer: int, defender_leader_pointer: int)
pdx_runtime.v3_objects.War.valid(self) -> bool
pdx_runtime.v3_objects.DiplomaticPlay(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, attacker_leader_id: int, defender_leader_id: int, attacker_leader_error: int, defender_leader_error: int, attacker_leader_pointer: int, defender_leader_pointer: int)
pdx_runtime.v3_objects.DiplomaticPlay.valid(self) -> bool
pdx_runtime.v3_objects.Character(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, is_female: int, is_female_error: int)
pdx_runtime.v3_objects.Character.valid(self) -> bool
pdx_runtime.v3_objects.Character.is_female_valid(self) -> bool
pdx_runtime.v3_objects.Ship(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, country_id: int, country_error: int, country_pointer: int, fleet_id: int, fleet_error: int, fleet_pointer: int, template_version_id: int, template_version_error: int, template_version_pointer: int, template_id: int, template_error: int, template_pointer: int)
pdx_runtime.v3_objects.Ship.valid(self) -> bool
pdx_runtime.v3_objects.Ship.has_country(self) -> bool
pdx_runtime.v3_objects.Ship.has_fleet(self) -> bool
pdx_runtime.v3_objects.Ship.has_template(self) -> bool
pdx_runtime.v3_objects.ShipDetails(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, country_id: int, country_error: int, country_pointer: int, fleet_id: int, fleet_error: int, fleet_pointer: int, template_version_id: int, template_version_error: int, template_version_pointer: int, template_id: int, template_error: int, template_pointer: int, name_no_formatting_error: int, name_no_formatting_offset: int, name_no_formatting_bytes: int, reserved: int, name_no_formatting_utf8: bytes = b'')
pdx_runtime.v3_objects.ShipDetails.has_name_no_formatting(self) -> bool
pdx_runtime.v3_objects.ShipDetails.name_no_formatting(self) -> str | None
pdx_runtime.v3_objects.ShipOptions(text_offset: int, text_capacity: int, text_bytes_written: int, reserved: int, text_bytes_required: int, required_buffer_size: int)
pdx_runtime.v3_objects.Company(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, country_id: int, country_error: int, country_pointer: int, company_type_pointer: int, company_type_error: int, reserved: int)
pdx_runtime.v3_objects.Company.valid(self) -> bool
pdx_runtime.v3_objects.Company.has_country(self) -> bool
pdx_runtime.v3_objects.Company.has_company_type(self) -> bool
pdx_runtime.v3_objects.CompanyDetails(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, country_id: int, country_error: int, country_pointer: int, company_type_pointer: int, company_type_error: int, reserved: int, name_error: int, name_offset: int, name_bytes: int, name_reserved: int, name_utf8: bytes = b'')
pdx_runtime.v3_objects.CompanyDetails.has_name(self) -> bool
pdx_runtime.v3_objects.CompanyDetails.name(self) -> str | None
pdx_runtime.v3_objects.CompanyOptions(text_offset: int, text_capacity: int, text_bytes_written: int, reserved: int, text_bytes_required: int, required_buffer_size: int)
pdx_runtime.v3_objects.ShippingLane(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, country_id: int, country_error: int, country_pointer: int, begin_state_id: int, begin_state_error: int, begin_state_pointer: int, end_state_id: int, end_state_error: int, end_state_pointer: int, begin_market_id: int, begin_market_error: int, begin_market_pointer: int, end_market_id: int, end_market_error: int, end_market_pointer: int, convoy_cost_raw64: int, effectiveness_raw64: int, convoy_cost_error: int, effectiveness_error: int)
pdx_runtime.v3_objects.ShippingLane.valid(self) -> bool
pdx_runtime.v3_objects.ShippingLane.has_country(self) -> bool
pdx_runtime.v3_objects.ShippingLane.has_begin_market(self) -> bool
pdx_runtime.v3_objects.ShippingLane.has_end_market(self) -> bool
pdx_runtime.v3_objects.InterestGroup(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, country_id: int, country_error: int, country_pointer: int)
pdx_runtime.v3_objects.InterestGroup.valid(self) -> bool
pdx_runtime.v3_objects.InterestGroup.has_country(self) -> bool
pdx_runtime.v3_objects.InterestGroupDetails(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, country_id: int, country_error: int, country_pointer: int, name_error: int, name_offset: int, name_bytes: int, reserved: int, name_utf8: bytes = b'')
pdx_runtime.v3_objects.InterestGroupDetails.has_name(self) -> bool
pdx_runtime.v3_objects.InterestGroupDetails.name(self) -> str | None
pdx_runtime.v3_objects.InterestGroupOptions(text_offset: int, text_capacity: int, text_bytes_written: int, reserved: int, text_bytes_required: int, required_buffer_size: int)
pdx_runtime.v3_objects.PopDetails(requested_id: int, resolved_id: int, error: int, slot_index: int, pointer: int, type_pointer: int, total_size: int, workforce: int, dependents: int, state_id: int, country_id: int, quantities_error: int, state_error: int, country_error: int, reserved: int, state_pointer: int, country_pointer: int)
pdx_runtime.v3_objects.PopDetails.valid(self) -> bool
pdx_runtime.v3_objects.MarketGoodsValue(requested_market_id: int, resolved_market_id: int, market_error: int, goods_error: int, market_pointer: int, goods_pointer: int, price_raw64: int, goods_index: int, definition_index: int, price_error: int, reserved: int)
pdx_runtime.v3_objects.MarketGoodsValue.valid(self) -> bool
pdx_runtime.v3_objects.MarketGoodsValue.price_raw64_signed(self) -> int
pdx_runtime.v3_objects.MarketGoodsDetails(requested_market_id: int, resolved_market_id: int, market_error: int, goods_error: int, market_pointer: int, goods_pointer: int, price_raw64: int, goods_index: int, definition_index: int, price_error: int, reserved: int, buy_orders_raw64: int, sell_orders_raw64: int, imports_raw64: int, exports_raw64: int, buy_orders_error: int, sell_orders_error: int, imports_error: int, exports_error: int)
pdx_runtime.v3_objects.MarketGoodsDetails.valid(self) -> bool
pdx_runtime.v3_objects.DefinitionOptions(text_offset: int, text_capacity: int, text_bytes_written: int, reserved: int, text_bytes_required: int, required_buffer_size: int)
pdx_runtime.v3_objects.LawDefinition(requested_index: int, resolved_index: int, error: int, key_error: int, pointer: int, group_index: int, group_error: int, group_pointer: int, key_offset: int, key_bytes_count: int, key_size: int, group_key_offset: int, group_key_bytes_count: int, group_key_size: int, group_key_error: int, reserved: int, key_bytes: bytes, group_key_bytes: bytes)
pdx_runtime.v3_objects.LawDefinition.valid(self) -> bool
pdx_runtime.v3_objects.LawDefinition.key(self) -> str
pdx_runtime.v3_objects.LawDefinition.group_key(self) -> str
pdx_runtime.v3_objects.TechnologyDefinition(requested_index: int, resolved_index: int, error: int, key_error: int, pointer: int, category_index: int, category_error: int, category_pointer: int, category_token: int, category_token_error: int, category_is_default: int, key_offset: int, key_bytes_count: int, key_size: int, key_bytes: bytes)
pdx_runtime.v3_objects.TechnologyDefinition.valid(self) -> bool
pdx_runtime.v3_objects.TechnologyDefinition.key(self) -> str
pdx_runtime.v3_objects.TechnologyDefinition.has_category(self) -> bool
pdx_runtime.v3_objects.ObjectsResult(size: int, error: int, domain: Domain, operation: int, start_index: int, max_items: int, capacity: int, input_count: int, ids_offset: int, records_offset: int, record_size: int, returned_count: int, checked_count: int, scanned_count: int, next_index: int, slot_bound: int, truncated: bool, buffer_size: int, database: int, fallback: int, world: int, records: tuple[Country | State | Building | Pop | Market | MilitaryFormation | War | DiplomaticPlay | Character | InterestGroup | InterestGroupDetails | ShippingLane | Company | CompanyDetails | Ship | ShipDetails | LawDefinition | TechnologyDefinition | PopDetails | MarketGoodsValue | MarketGoodsDetails, ...], definition_options: DefinitionOptions | None = None, interest_group_options: InterestGroupOptions | None = None, company_options: CompanyOptions | None = None, ship_options: ShipOptions | None = None)
pdx_runtime.v3_objects.ObjectsResult.valid(self) -> bool
pdx_runtime.v3_objects.ObjectsResult.from_bytes(cls, data: bytes) -> ObjectsResult
pdx_runtime.v3_objects.enumerate_objects(client: NativeBridge, domain: Domain, *, start_index: int, max_items: int, capacity: int | None=None, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.read_ids(client: NativeBridge, domain: Domain, ids: Iterable[int], *, start_index: int=0, max_items: int | None=None, capacity: int | None=None, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.enumerate_pop_details(client: NativeBridge, *, start_index: int, max_items: int, capacity: int | None=None, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.read_pop_details(client: NativeBridge, ids: Iterable[int], *, start_index: int=0, max_items: int | None=None, capacity: int | None=None, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.read_market_goods(client: NativeBridge, pairs: Iterable[tuple[int, int]], *, start_index: int=0, max_items: int | None=None, capacity: int | None=None, details: bool=False, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.enumerate_market_goods(client: NativeBridge, market_id: int, *, start_index: int, max_items: int, capacity: int | None=None, details: bool=False, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.enumerate_law_definitions(client: NativeBridge, *, start_index: int, max_items: int, capacity: int | None=None, text_capacity: int=4096, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.read_law_definition_indices(client: NativeBridge, indices: Iterable[int], *, start_index: int=0, max_items: int | None=None, capacity: int | None=None, text_capacity: int=4096, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.enumerate_technology_definitions(client: NativeBridge, *, start_index: int, max_items: int, capacity: int | None=None, text_capacity: int=4096, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.read_technology_definition_indices(client: NativeBridge, indices: Iterable[int], *, start_index: int=0, max_items: int | None=None, capacity: int | None=None, text_capacity: int=4096, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.enumerate_interest_group_details(client: NativeBridge, *, start_index: int, max_items: int, capacity: int | None=None, text_capacity: int=4096, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.read_interest_group_details(client: NativeBridge, ids: Iterable[int], *, start_index: int=0, max_items: int | None=None, capacity: int | None=None, text_capacity: int=4096, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.enumerate_company_details(client: NativeBridge, *, start_index: int, max_items: int, capacity: int | None=None, text_capacity: int=4096, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.read_company_details(client: NativeBridge, ids: Iterable[int], *, start_index: int=0, max_items: int | None=None, capacity: int | None=None, text_capacity: int=4096, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.enumerate_ship_details(client: NativeBridge, *, start_index: int, max_items: int, capacity: int | None=None, text_capacity: int=4096, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_objects.read_ship_details(client: NativeBridge, ids: Iterable[int], *, start_index: int=0, max_items: int | None=None, capacity: int | None=None, text_capacity: int=4096, adapter: str | int='pdx_v3_objects.dll', process: RemoteProcess | None=None) -> CallbackResult
```

### `pdx_runtime.v3_data`

```text
pdx_runtime.v3_data.ObjectBinding(type_id: int, object: int, retained: bool = False, country_id: bool = False, domain: str | None = None)
pdx_runtime.v3_data.ObjectBinding.pack(self) -> bytes
pdx_runtime.v3_data.ObjectBinding.full_id(cls, domain: str, type_id: int, full_id: int) -> ObjectBinding
pdx_runtime.v3_data.ObjectBinding.state(cls, type_id: int, full_id: int) -> ObjectBinding
pdx_runtime.v3_data.ObjectBinding.pop(cls, type_id: int, full_id: int) -> ObjectBinding
pdx_runtime.v3_data.ObjectBinding.market(cls, type_id: int, full_id: int) -> ObjectBinding
pdx_runtime.v3_data.ObjectBinding.building(cls, type_id: int, full_id: int) -> ObjectBinding
pdx_runtime.v3_data.ObjectBinding.country(cls, type_id: int, full_id: int) -> ObjectBinding
pdx_runtime.v3_data.TypeInfo(type_id: int, name_bytes: int, description_bytes: int, flags: int, native_record: int, object_to_any: int, any_to_object: int, name: str, description: str)
pdx_runtime.v3_data.TypeInfo.from_bytes(cls, data: bytes) -> TypeInfo
pdx_runtime.v3_data.TypeInfo.can_bind_object(self) -> bool
pdx_runtime.v3_data.FunctionInfo(type_id: int, return_type_id: int, argument_count: int, flags: int, name_bytes: int, description_bytes: int, native_descriptor: int, callbacks: tuple[int, int, int, int], name: str, description: str)
pdx_runtime.v3_data.FunctionInfo.from_bytes(cls, data: bytes) -> FunctionInfo
pdx_runtime.v3_data.FixedPoint(bits: int)
pdx_runtime.v3_data.FixedPoint.signed(self) -> int
pdx_runtime.v3_data.NativeObject(pointer: int, type_id: int, value_handle: int, native_type_name: str)
pdx_runtime.v3_data.NativeObject.binding(self) -> ObjectBinding
pdx_runtime.v3_data.NativeDataCommand(primary_pointer: int, shared_control: int, game_command_pointer: int, value_handle: int, native_type_name: str, generation_world: int = 0)
pdx_runtime.v3_data.NativeList(value_handle: int, payload_address: int, descriptor: bytes, native_type_name: str)
pdx_runtime.v3_data.DataResult(size: int, error: int, action: int, flags: int, type_id: int, start: int, capacity: int, count: int, next: int, total: int, execution_thread_id: int, kind: int, factory: int, object: int, value_handle: int, any_type: int, any_vtable: int, payload_address: int, payload_size: int, snapshot_bytes: int, text_offset: int, text_bytes: int, bindings_offset: int, bindings_count: int, output_offset: int, output_bytes: int, raw_bits: int, raw_any: bytes, native_type_name: str, snapshot: bytes, rows: tuple[TypeInfo | FunctionInfo, ...] = ())
pdx_runtime.v3_data.DataResult.from_bytes(cls, data: bytes) -> DataResult
pdx_runtime.v3_data.DataResult.value(self)
pdx_runtime.v3_data.TypedInput(kind: Kind | int, raw_bits: int = 0, value_handle: int = 0, text: str = '')
pdx_runtime.v3_data.TypedInput.empty(cls) -> TypedInput
pdx_runtime.v3_data.TypedInput.boolean(cls, value: bool) -> TypedInput
pdx_runtime.v3_data.TypedInput.i32(cls, value: int) -> TypedInput
pdx_runtime.v3_data.TypedInput.u32(cls, value: int) -> TypedInput
pdx_runtime.v3_data.TypedInput.i64(cls, value: int) -> TypedInput
pdx_runtime.v3_data.TypedInput.u64(cls, value: int) -> TypedInput
pdx_runtime.v3_data.TypedInput.float32(cls, value: float) -> TypedInput
pdx_runtime.v3_data.TypedInput.float64(cls, value: float) -> TypedInput
pdx_runtime.v3_data.TypedInput.fixed(cls, value: FixedPoint) -> TypedInput
pdx_runtime.v3_data.TypedInput.string(cls, value: str) -> TypedInput
pdx_runtime.v3_data.TypedInput.utf8_string(cls, value: str) -> TypedInput
pdx_runtime.v3_data.TypedInput.retained(cls, value_handle: int) -> TypedInput
pdx_runtime.v3_data.TypedInput.pack(self) -> bytes
pdx_runtime.v3_data.ActionResult(result: DataResult, input_offset: int, input_bytes: int, compiled_mode: int, invoked: int, accepted: int, input_kind: int)
pdx_runtime.v3_data.ActionResult.from_bytes(cls, data: bytes) -> ActionResult
pdx_runtime.v3_data.ActionResult.error(self) -> int
pdx_runtime.v3_data.ActionResult.value(self)
pdx_runtime.v3_data.ActionResult.value_handle(self) -> int
pdx_runtime.v3_data.PointerArraySource(root_expression: str, byte_offset: int, type_id: int, interface_thread_id: int, index: int = 0, expected_count: int | None = None, guard_expression: str = '', guard_input: TypedInput | None = None)
pdx_runtime.v3_data.SourceGuardResult(text_offset: int, text_bytes: int, input_offset: int, input_bytes: int, checked: int, passed: int)
pdx_runtime.v3_data.PointerArrayResult(root_text_offset: int, root_text_bytes: int, byte_offset: int, source_type_id: int, index: int, expected_count: int, interface_thread_id: int, flags: int, count: int, stages: int)
pdx_runtime.v3_data.ElementResult(action: ActionResult, source_world: int, source_text_offset: int, source_text_bytes: int, condition_offset: int, condition_bytes: int, element_type_id: int, condition_checked: int, condition_passed: int, source_count: int, array_source: PointerArrayResult | None = None, source_guard: SourceGuardResult | None = None)
pdx_runtime.v3_data.ElementResult.from_bytes(cls, value: bytes) -> ElementResult
pdx_runtime.v3_data.ElementResult.root_count(self) -> int | None
pdx_runtime.v3_data.ElementResult.source_stages(self) -> int
pdx_runtime.v3_data.ElementResult.guard_checked(self) -> int
pdx_runtime.v3_data.ElementResult.guard_passed(self) -> int
pdx_runtime.v3_data.ElementResult.error(self) -> int
pdx_runtime.v3_data.ElementResult.value(self)
pdx_runtime.v3_data.ElementResult.invoked(self) -> int
pdx_runtime.v3_data.ElementResult.accepted(self) -> int
pdx_runtime.v3_data.ElementResult.result(self) -> DataResult
pdx_runtime.v3_data.DataQuery(client, process, callback, address, owns_process, size, adapter, decode)
pdx_runtime.v3_data.DataQuery.release_value(self, *, process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.enumerate_types(client: NativeBridge, *, start: int=0, capacity: int=32, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.enumerate_functions(client: NativeBridge, type_id: int, *, start: int=0, capacity: int=32, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.evaluate(client: NativeBridge, expression: str, bindings: Iterable[ObjectBinding]=(), *, output_bytes: int=4096, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.snapshot(client: NativeBridge, value_handle: int, *, output_bytes: int=4096, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.release(client: NativeBridge, value_handle: int, *, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.list_count(client: NativeBridge, value_handle: int, *, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.list_element(client: NativeBridge, value_handle: int, index: int, *, output_bytes: int=4096, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.invoke_action(client: NativeBridge, expression: str, input: TypedInput, bindings: Iterable[ObjectBinding]=(), *, output_bytes: int=4096, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.invoke_property_action(client: NativeBridge, expression: str, bindings: Iterable[ObjectBinding]=(), *, output_bytes: int=4096, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.create_state_name_context(client: NativeBridge, state_id: int, *, output_bytes: int=4096, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.state_name_draft(client: NativeBridge, value_handle: int, *, index: int=0, output_bytes: int=4096, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.state_name_override(client: NativeBridge, state_id: int, *, index: int=0, output_bytes: int=4096, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.command_value(client: NativeBridge, value_handle: int, *, output_bytes: int=32, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.evaluate_element(client: NativeBridge, source_expression: str, index: int, expression: str, bindings: Iterable[ObjectBinding]=(), *, world: int, condition: str='', expected_count: int | None=None, pointer_array: PointerArraySource | None=None, output_bytes: int=4096, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.invoke_element_action(client: NativeBridge, source_expression: str, index: int, expression: str, input: TypedInput, bindings: Iterable[ObjectBinding]=(), *, world: int, condition: str='', expected_count: int | None=None, pointer_array: PointerArraySource | None=None, output_bytes: int=4096, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.pointer_array_count(client: NativeBridge, source: PointerArraySource, *, world: int, bindings: Iterable[ObjectBinding]=(), adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
pdx_runtime.v3_data.evaluate_pointer_array(client: NativeBridge, source: PointerArraySource, expression: str, bindings: Iterable[ObjectBinding]=(), *, world: int, condition: str='', output_bytes: int=4096, adapter: str | int='pdx_v3_data.dll', process: RemoteProcess | None=None) -> DataQuery
```

### `pdx_runtime.v3_targets`

```text
pdx_runtime.v3_targets.resolve_country(read: Read, base: int, country_id: int) -> int
pdx_runtime.v3_targets.resolve_state(read: Read, base: int, state_id: int) -> int
pdx_runtime.v3_targets.iter_owned_states(read: Read, base: int, country_id: int, *, start: int=0, stop: int | None=None) -> Iterator[dict[str, int]]
pdx_runtime.v3_targets.resolve_building(read: Read, base: int, building_id: int) -> int
pdx_runtime.v3_targets.iter_existing_building_targets(read: Read, base: int, country_id: int, state_id: int, *, start: int=0, stop: int | None=None) -> Iterator[dict[str, int]]
```

### `pdx_runtime.v3_diagnostics`

```text
pdx_runtime.v3_diagnostics.LogRecord(native_sequence: int, category: int, source_line: int, group_raw: int, level_raw: int, native_time_raw: int, label: str, source: str, message: str)
pdx_runtime.v3_diagnostics.DiagnosticsResult(size: int, error: int, operation: int, flags: Flags, after_sequence: int, first_sequence: int, last_sequence: int, next_sequence: int, max_records: int, output_capacity: int, output_size: int, output_required: int, returned_count: int, eligible_count: int, store_count: int, has_more: int, logging_ready: int, viewer_available: int, subscription_coverage_known: int, execution_thread_id: int, expected_viewer: int, viewer: int, native_limit: int, native_prune: int, records: tuple[LogRecord, ...])
pdx_runtime.v3_diagnostics.DiagnosticsResult.gap(self) -> bool
pdx_runtime.v3_diagnostics.DiagnosticsResult.reset(self) -> bool
pdx_runtime.v3_diagnostics.DiagnosticsResult.output_truncated(self) -> bool
pdx_runtime.v3_diagnostics.DiagnosticsResult.from_bytes(cls, data: bytes) -> DiagnosticsResult
pdx_runtime.v3_diagnostics.status(client: NativeBridge, *, after_sequence: int=-1, expected_viewer: int=0, adapter: str | int='pdx_v3_diagnostics.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_diagnostics.records(client: NativeBridge, *, after_sequence: int=-1, max_records: int=256, output_capacity: int=65536, expected_viewer: int=0, adapter: str | int='pdx_v3_diagnostics.dll', process: RemoteProcess | None=None) -> CallbackResult
```

### `pdx_runtime.v3_ai`

```text
pdx_runtime.v3_ai.AiState(size: int, error: int, country_full_id: int, country_error: int, actor_full_id: int, actor_error: int, owner_country_full_id: int, actor_enabled_raw: int, manager_override_raw: int, manager_error: int, role_index: int, role_raw: int, role_count: int, role_error: int, observed_predicate: int, observed_predicate_valid: int, paused: int, simulation_started: int, execution_thread_id: int, reserved: int, application: int, world: int, idler: int, country_manager: int, ai_manager: int, country: int, actor: int, role_data: int)
pdx_runtime.v3_ai.AiState.from_bytes(cls, data: bytes) -> AiState
pdx_runtime.v3_ai.observe_country_ai(client: NativeBridge, country_full_id: int, *, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_ai.disable_ai(client: NativeBridge, selector: str, *, output_capacity: int=65536, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_ai.enable_ai(client: NativeBridge, selector: str, *, output_capacity: int=65536, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_ai.AiCacheRow(cache_index: int, valid_fields: AiCacheValidFields, actor_full_id: int, owner_country_full_id: int, actor_error: int, owner_error: int, link_error: int, role_error: int, actor_enabled_raw: int, actor_141_raw: int, role_index: int, role_raw: int, observed_predicate: int, reserved0: int, reserved1: int, reserved2: int)
pdx_runtime.v3_ai.AiCacheRow.known_fields(self) -> AiCacheValidFields
pdx_runtime.v3_ai.AiCacheRow.actor_fields_known(self) -> bool
pdx_runtime.v3_ai.AiCacheRow.actor_identity_known(self) -> bool
pdx_runtime.v3_ai.AiCacheRow.owner_identity_known(self) -> bool
pdx_runtime.v3_ai.AiCacheRow.role_value_known(self) -> bool
pdx_runtime.v3_ai.AiCacheRow.observed_predicate_known(self) -> bool
pdx_runtime.v3_ai.AiCacheRow.has_errors(self) -> bool
pdx_runtime.v3_ai.AiCacheSnapshot(size: int, error: int, row_capacity: int, row_count: int, native_total: int, flags: AiCacheFlags, required_bytes: int, returned_bytes: int, row_bytes: int, manager_override_raw: int, manager_error: int, role_count: int, paused: int, simulation_started: int, execution_thread_id: int, row_error_count: int, reserved: int, expected_world: int, world: int, application: int, rows: tuple[AiCacheRow, ...])
pdx_runtime.v3_ai.AiCacheSnapshot.context_valid(self) -> bool
pdx_runtime.v3_ai.AiCacheSnapshot.header_valid(self) -> bool
pdx_runtime.v3_ai.AiCacheSnapshot.total_known(self) -> bool
pdx_runtime.v3_ai.AiCacheSnapshot.complete(self) -> bool
pdx_runtime.v3_ai.AiCacheSnapshot.truncated(self) -> bool
pdx_runtime.v3_ai.AiCacheSnapshot.prefix_fault(self) -> bool
pdx_runtime.v3_ai.AiCacheSnapshot.has_row_errors(self) -> bool
pdx_runtime.v3_ai.AiCacheSnapshot.from_bytes(cls, data: bytes, *, capacity: int | None=None, expected_world: int | None=None) -> AiCacheSnapshot
pdx_runtime.v3_ai.observe_cached_ai(client: NativeBridge, *, capacity: int=256, expected_world: int=0, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
```

### `pdx_runtime.v3_player_control`

```text
pdx_runtime.v3_player_control.PlayerState(size: int, error: int, action: int, tag_bytes: int, local_machine_id: int, application_player_id: int, player_machine_id: int, player_flags_raw: int, observer_enabled: int, observer_global: int, paused: int, simulation_started: int, controlled_country_id: int, controlled_country_error: int, selected_country_id: int, selected_country_error: int, target_country_id: int, target_error: int, target_control_eligible: int, execution_thread_id: int, application: int, world: int, idler: int, player: int, controlled_ref_database: int, selected_ref_database: int, controlled_country: int, selected_country: int, target_country: int, tag_raw: bytes)
pdx_runtime.v3_player_control.PlayerState.from_bytes(cls, data: bytes) -> PlayerState
pdx_runtime.v3_player_control.PlayerState.tag(self) -> str
pdx_runtime.v3_player_control.observe_player(client: NativeBridge, *, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_player_control.resolve_tag(client: NativeBridge, tag: str, *, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_player_control.switch_country(client: NativeBridge, tag: str, *, output_capacity: int=65536, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
pdx_runtime.v3_player_control.toggle_observer(client: NativeBridge, *, output_capacity: int=65536, adapter: str | int='pdx_v3_console.dll', process: RemoteProcess | None=None) -> CallbackResult
```

## Public result and value shapes

Fields and property return types below follow the source annotations. Raw integer fields remain raw; source meanings are summarized in the contract notes rather than inferred from their names.

### `pdx_runtime.v3`

**QueryResult**
- Fields: `size: int`; `error: int`; `requested_id: int`; `resolved_id: int`; `country: int`; `fallback: int`; `gdp_raw64: int`
- Properties: `valid -> bool`; `gdp_raw64_hex -> str`

### `pdx_runtime.v3_objects`

**Domain**: `COUNTRY=1`, `STATE=2`, `BUILDING=3`, `POP=4`, `MARKET=5`, `MILITARY_FORMATION=6`, `WAR=7`, `DIPLOMATIC_PLAY=8`, `CHARACTER=9`, `LAW_DEFINITION=10`, `TECHNOLOGY_DEFINITION=11`, `INTEREST_GROUP=12`, `SHIPPING_LANE=13`, `COMPANY=14`, `SHIP=15`

**Country**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `gdp_raw64: int`
- Properties: `valid -> bool`

**State**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `owner_id: int`; `reserved: int`
- Properties: `valid -> bool`

**Building**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `state_id: int`; `levels: int`; `type_pointer: int`
- Properties: `valid -> bool`

**Pop**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `type_pointer: int`
- Properties: `valid -> bool`

**Market**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `owner_id: int`; `reserved: int`
- Properties: `valid -> bool`

**MilitaryFormation**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `owner_country_id: int`; `owner_country_error: int`; `owner_country_pointer: int`
- Properties: `valid -> bool`

**War**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `diplomatic_play_id: int`; `diplomatic_play_error: int`; `diplomatic_play_pointer: int`; `attacker_leader_id: int`; `defender_leader_id: int`; `attacker_leader_error: int`; `defender_leader_error: int`; `attacker_leader_pointer: int`; `defender_leader_pointer: int`
- Properties: `valid -> bool`

**DiplomaticPlay**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `attacker_leader_id: int`; `defender_leader_id: int`; `attacker_leader_error: int`; `defender_leader_error: int`; `attacker_leader_pointer: int`; `defender_leader_pointer: int`
- Properties: `valid -> bool`

**Character**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `is_female: int`; `is_female_error: int`
- Properties: `valid -> bool`; `is_female_valid -> bool`

**Ship**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `country_id: int`; `country_error: int`; `country_pointer: int`; `fleet_id: int`; `fleet_error: int`; `fleet_pointer: int`; `template_version_id: int`; `template_version_error: int`; `template_version_pointer: int`; `template_id: int`; `template_error: int`; `template_pointer: int`
- Properties: `valid -> bool`; `has_country -> bool`; `has_fleet -> bool`; `has_template -> bool`

**ShipDetails**
- Fields: `name_no_formatting_error: int`; `name_no_formatting_offset: int`; `name_no_formatting_bytes: int`; `reserved: int`; `name_no_formatting_utf8: bytes = b''`
- Properties: `has_name_no_formatting -> bool`; `name_no_formatting -> str | None`

**ShipOptions**
- Fields: `text_offset: int`; `text_capacity: int`; `text_bytes_written: int`; `reserved: int`; `text_bytes_required: int`; `required_buffer_size: int`

**Company**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `country_id: int`; `country_error: int`; `country_pointer: int`; `company_type_pointer: int`; `company_type_error: int`; `reserved: int`
- Properties: `valid -> bool`; `has_country -> bool`; `has_company_type -> bool`

**CompanyDetails**
- Fields: `name_error: int`; `name_offset: int`; `name_bytes: int`; `name_reserved: int`; `name_utf8: bytes = b''`
- Properties: `has_name -> bool`; `name -> str | None`

**CompanyOptions**
- Fields: `text_offset: int`; `text_capacity: int`; `text_bytes_written: int`; `reserved: int`; `text_bytes_required: int`; `required_buffer_size: int`

**ShippingLane**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `country_id: int`; `country_error: int`; `country_pointer: int`; `begin_state_id: int`; `begin_state_error: int`; `begin_state_pointer: int`; `end_state_id: int`; `end_state_error: int`; `end_state_pointer: int`; `begin_market_id: int`; `begin_market_error: int`; `begin_market_pointer: int`; `end_market_id: int`; `end_market_error: int`; `end_market_pointer: int`; `convoy_cost_raw64: int`; `effectiveness_raw64: int`; `convoy_cost_error: int`; `effectiveness_error: int`
- Properties: `valid -> bool`; `has_country -> bool`; `has_begin_market -> bool`; `has_end_market -> bool`

**InterestGroup**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `country_id: int`; `country_error: int`; `country_pointer: int`
- Properties: `valid -> bool`; `has_country -> bool`

**InterestGroupDetails**
- Fields: `name_error: int`; `name_offset: int`; `name_bytes: int`; `reserved: int`; `name_utf8: bytes = b''`
- Properties: `has_name -> bool`; `name -> str | None`

**InterestGroupOptions**
- Fields: `text_offset: int`; `text_capacity: int`; `text_bytes_written: int`; `reserved: int`; `text_bytes_required: int`; `required_buffer_size: int`

**PopDetails**
- Fields: `requested_id: int`; `resolved_id: int`; `error: int`; `slot_index: int`; `pointer: int`; `type_pointer: int`; `total_size: int`; `workforce: int`; `dependents: int`; `state_id: int`; `country_id: int`; `quantities_error: int`; `state_error: int`; `country_error: int`; `reserved: int`; `state_pointer: int`; `country_pointer: int`
- Properties: `valid -> bool`

**MarketGoodsValue**
- Fields: `requested_market_id: int`; `resolved_market_id: int`; `market_error: int`; `goods_error: int`; `market_pointer: int`; `goods_pointer: int`; `price_raw64: int`; `goods_index: int`; `definition_index: int`; `price_error: int`; `reserved: int`
- Properties: `valid -> bool`; `price_raw64_signed -> int`

**MarketGoodsDetails**
- Fields: `buy_orders_raw64: int`; `sell_orders_raw64: int`; `imports_raw64: int`; `exports_raw64: int`; `buy_orders_error: int`; `sell_orders_error: int`; `imports_error: int`; `exports_error: int`
- Properties: `valid -> bool`

**DefinitionOptions**
- Fields: `text_offset: int`; `text_capacity: int`; `text_bytes_written: int`; `reserved: int`; `text_bytes_required: int`; `required_buffer_size: int`

**LawDefinition**
- Fields: `requested_index: int`; `resolved_index: int`; `error: int`; `key_error: int`; `pointer: int`; `group_index: int`; `group_error: int`; `group_pointer: int`; `key_offset: int`; `key_bytes_count: int`; `key_size: int`; `group_key_offset: int`; `group_key_bytes_count: int`; `group_key_size: int`; `group_key_error: int`; `reserved: int`; `key_bytes: bytes`; `group_key_bytes: bytes`
- Properties: `valid -> bool`; `key -> str`; `group_key -> str`

**TechnologyDefinition**
- Fields: `requested_index: int`; `resolved_index: int`; `error: int`; `key_error: int`; `pointer: int`; `category_index: int`; `category_error: int`; `category_pointer: int`; `category_token: int`; `category_token_error: int`; `category_is_default: int`; `key_offset: int`; `key_bytes_count: int`; `key_size: int`; `key_bytes: bytes`
- Properties: `valid -> bool`; `key -> str`; `has_category -> bool`

**ObjectsResult**
- Fields: `size: int`; `error: int`; `domain: Domain`; `operation: int`; `start_index: int`; `max_items: int`; `capacity: int`; `input_count: int`; `ids_offset: int`; `records_offset: int`; `record_size: int`; `returned_count: int`; `checked_count: int`; `scanned_count: int`; `next_index: int`; `slot_bound: int`; `truncated: bool`; `buffer_size: int`; `database: int`; `fallback: int`; `world: int`; `records: tuple[Country | State | Building | Pop | Market | MilitaryFormation | War | DiplomaticPlay | Character | InterestGroup | InterestGroupDetails | ShippingLane | Company | CompanyDetails | Ship | ShipDetails | LawDefinition | TechnologyDefinition | PopDetails | MarketGoodsValue | MarketGoodsDetails, ...]`; `definition_options: DefinitionOptions | None = None`; `interest_group_options: InterestGroupOptions | None = None`; `company_options: CompanyOptions | None = None`; `ship_options: ShipOptions | None = None`
- Properties: `valid -> bool`

### `pdx_runtime.v3_data`

**Action**: `TYPES=1`, `FUNCTIONS=2`, `EVALUATE=3`, `SNAPSHOT=4`, `RELEASE=5`, `LIST_COUNT=6`, `LIST_ELEMENT=7`, `INVOKE_ACTION=8`, `CREATE_STATE_NAME_CONTEXT=9`, `STATE_NAME_DRAFT=10`, `STATE_NAME_OVERRIDE=11`, `COMMAND_VALUE=12`, `EVALUATE_ELEMENT=13`, `INVOKE_ELEMENT_ACTION=14`, `EVALUATE_ARRAY_ELEMENT=15`, `INVOKE_ARRAY_ELEMENT_ACTION=16`

**Kind**: `RAW=0`, `EMPTY=1`, `BOOL=2`, `I32=3`, `U32=4`, `I64=5`, `U64=6`, `FLOAT=7`, `DOUBLE=8`, `FIXED=9`, `STRING=10`, `OBJECT=11`, `LIST=12`, `COMMAND=13`

**ObjectBinding**
- Fields: `type_id: int`; `object: int`; `retained: bool = False`; `country_id: bool = False`; `domain: str | None = None`

**TypeInfo**
- Fields: `type_id: int`; `name_bytes: int`; `description_bytes: int`; `flags: int`; `native_record: int`; `object_to_any: int`; `any_to_object: int`; `name: str`; `description: str`
- Properties: `can_bind_object -> bool`

**FunctionInfo**
- Fields: `type_id: int`; `return_type_id: int`; `argument_count: int`; `flags: int`; `name_bytes: int`; `description_bytes: int`; `native_descriptor: int`; `callbacks: tuple[int, int, int, int]`; `name: str`; `description: str`

**FixedPoint**
- Fields: `bits: int`
- Properties: `signed -> int`

**NativeObject**
- Fields: `pointer: int`; `type_id: int`; `value_handle: int`; `native_type_name: str`

**NativeDataCommand**
- Fields: `primary_pointer: int`; `shared_control: int`; `game_command_pointer: int`; `value_handle: int`; `native_type_name: str`; `generation_world: int = 0`

**NativeList**
- Fields: `value_handle: int`; `payload_address: int`; `descriptor: bytes`; `native_type_name: str`

**DataResult**
- Fields: `size: int`; `error: int`; `action: int`; `flags: int`; `type_id: int`; `start: int`; `capacity: int`; `count: int`; `next: int`; `total: int`; `execution_thread_id: int`; `kind: int`; `factory: int`; `object: int`; `value_handle: int`; `any_type: int`; `any_vtable: int`; `payload_address: int`; `payload_size: int`; `snapshot_bytes: int`; `text_offset: int`; `text_bytes: int`; `bindings_offset: int`; `bindings_count: int`; `output_offset: int`; `output_bytes: int`; `raw_bits: int`; `raw_any: bytes`; `native_type_name: str`; `snapshot: bytes`; `rows: tuple[TypeInfo | FunctionInfo, ...] = ()`
- Properties: `value -> (no annotation)`

**TypedInput**
- Fields: `kind: Kind | int`; `raw_bits: int = 0`; `value_handle: int = 0`; `text: str = ''`

**ActionResult**
- Fields: `result: DataResult`; `input_offset: int`; `input_bytes: int`; `compiled_mode: int`; `invoked: int`; `accepted: int`; `input_kind: int`
- Properties: `error -> int`; `value -> (no annotation)`; `value_handle -> int`

**PointerArraySource**
- Fields: `root_expression: str`; `byte_offset: int`; `type_id: int`; `interface_thread_id: int`; `index: int = 0`; `expected_count: int | None = None`; `guard_expression: str = ''`; `guard_input: TypedInput | None = None`

**SourceGuardResult**
- Fields: `text_offset: int`; `text_bytes: int`; `input_offset: int`; `input_bytes: int`; `checked: int`; `passed: int`

**PointerArrayResult**
- Fields: `root_text_offset: int`; `root_text_bytes: int`; `byte_offset: int`; `source_type_id: int`; `index: int`; `expected_count: int`; `interface_thread_id: int`; `flags: int`; `count: int`; `stages: int`

**ElementResult**
- Fields: `action: ActionResult`; `source_world: int`; `source_text_offset: int`; `source_text_bytes: int`; `condition_offset: int`; `condition_bytes: int`; `element_type_id: int`; `condition_checked: int`; `condition_passed: int`; `source_count: int`; `array_source: PointerArrayResult | None = None`; `source_guard: SourceGuardResult | None = None`
- Properties: `root_count -> int | None`; `source_stages -> int`; `guard_checked -> int`; `guard_passed -> int`; `error -> int`; `value -> (no annotation)`; `invoked -> int`; `accepted -> int`; `result -> DataResult`

### `pdx_runtime.v3_diagnostics`

**Flags**: `GAP=1`, `RESET=2`, `PRUNED=4`, `OUTPUT_TRUNCATED=8`

**LogRecord**
- Fields: `native_sequence: int`; `category: int`; `source_line: int`; `group_raw: int`; `level_raw: int`; `native_time_raw: int`; `label: str`; `source: str`; `message: str`

**DiagnosticsResult**
- Fields: `size: int`; `error: int`; `operation: int`; `flags: Flags`; `after_sequence: int`; `first_sequence: int`; `last_sequence: int`; `next_sequence: int`; `max_records: int`; `output_capacity: int`; `output_size: int`; `output_required: int`; `returned_count: int`; `eligible_count: int`; `store_count: int`; `has_more: int`; `logging_ready: int`; `viewer_available: int`; `subscription_coverage_known: int`; `execution_thread_id: int`; `expected_viewer: int`; `viewer: int`; `native_limit: int`; `native_prune: int`; `records: tuple[LogRecord, ...]`
- Properties: `gap -> bool`; `reset -> bool`; `output_truncated -> bool`

### `pdx_runtime.v3_ai`

**AiCacheFlags**: `CONTEXT_VALID=1`, `HEADER_VALID=2`, `TOTAL_KNOWN=4`, `COMPLETE=8`, `TRUNCATED=16`, `ROW_ERRORS=32`, `PREFIX_FAULT=64`

**AiCacheValidFields**: `ACTOR_FIELDS=1`, `ACTOR_IDENTITY=2`, `OWNER_IDENTITY=4`, `ROLE_VALUE=8`, `PREDICATE=16`

**AiState**
- Fields: `size: int`; `error: int`; `country_full_id: int`; `country_error: int`; `actor_full_id: int`; `actor_error: int`; `owner_country_full_id: int`; `actor_enabled_raw: int`; `manager_override_raw: int`; `manager_error: int`; `role_index: int`; `role_raw: int`; `role_count: int`; `role_error: int`; `observed_predicate: int`; `observed_predicate_valid: int`; `paused: int`; `simulation_started: int`; `execution_thread_id: int`; `reserved: int`; `application: int`; `world: int`; `idler: int`; `country_manager: int`; `ai_manager: int`; `country: int`; `actor: int`; `role_data: int`

**AiCacheRow**
- Fields: `cache_index: int`; `valid_fields: AiCacheValidFields`; `actor_full_id: int`; `owner_country_full_id: int`; `actor_error: int`; `owner_error: int`; `link_error: int`; `role_error: int`; `actor_enabled_raw: int`; `actor_141_raw: int`; `role_index: int`; `role_raw: int`; `observed_predicate: int`; `reserved0: int`; `reserved1: int`; `reserved2: int`
- Properties: `known_fields -> AiCacheValidFields`; `actor_fields_known -> bool`; `actor_identity_known -> bool`; `owner_identity_known -> bool`; `role_value_known -> bool`; `observed_predicate_known -> bool`; `has_errors -> bool`

**AiCacheSnapshot**
- Fields: `size: int`; `error: int`; `row_capacity: int`; `row_count: int`; `native_total: int`; `flags: AiCacheFlags`; `required_bytes: int`; `returned_bytes: int`; `row_bytes: int`; `manager_override_raw: int`; `manager_error: int`; `role_count: int`; `paused: int`; `simulation_started: int`; `execution_thread_id: int`; `row_error_count: int`; `reserved: int`; `expected_world: int`; `world: int`; `application: int`; `rows: tuple[AiCacheRow, ...]`
- Properties: `context_valid -> bool`; `header_valid -> bool`; `total_known -> bool`; `complete -> bool`; `truncated -> bool`; `prefix_fault -> bool`; `has_row_errors -> bool`

### `pdx_runtime.v3_player_control`

**PlayerState**
- Fields: `size: int`; `error: int`; `action: int`; `tag_bytes: int`; `local_machine_id: int`; `application_player_id: int`; `player_machine_id: int`; `player_flags_raw: int`; `observer_enabled: int`; `observer_global: int`; `paused: int`; `simulation_started: int`; `controlled_country_id: int`; `controlled_country_error: int`; `selected_country_id: int`; `selected_country_error: int`; `target_country_id: int`; `target_error: int`; `target_control_eligible: int`; `execution_thread_id: int`; `application: int`; `world: int`; `idler: int`; `player: int`; `controlled_ref_database: int`; `selected_ref_database: int`; `controlled_country: int`; `selected_country: int`; `target_country: int`; `tag_raw: bytes`
- Properties: `tag -> str`
