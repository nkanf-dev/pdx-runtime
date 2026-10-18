"""Native bulk snapshots of V3 live objects and typed definitions.

Each request is one existing Interface callback and one native (1,0) access
scope. Native resolvers check complete IDs; Country GDP uses the real getter.
No external per-field reads, synchronous wait, game-side Python or framework.
``read()`` copies the preallocated buffer and releases it after completion.
Pointers are borrowed observations, not handles retained across ticks/worlds.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import Iterable

from .native_bridge import NativeBridge, RemoteProcess
from .v3 import CallbackResult, ERROR_IO_PENDING, submit_callback


class Domain(IntEnum):
    COUNTRY = 1
    STATE = 2
    BUILDING = 3
    POP = 4
    MARKET = 5
    MILITARY_FORMATION = 6
    WAR = 7
    DIPLOMATIC_PLAY = 8
    CHARACTER = 9
    LAW_DEFINITION = 10
    TECHNOLOGY_DEFINITION = 11
    INTEREST_GROUP = 12
    SHIPPING_LANE = 13
    COMPANY = 14
    SHIP = 15


ENUMERATE = 1
READ_IDS = 2
ENUMERATE_POP_DETAILS = 3
READ_POP_DETAILS = 4
READ_MARKET_GOODS = 5
ENUMERATE_MARKET_GOODS = 6
READ_MARKET_GOODS_DETAILS = 7
ENUMERATE_MARKET_GOODS_DETAILS = 8
ENUMERATE_DEFINITIONS = 9
READ_DEFINITION_INDICES = 10
ENUMERATE_INTEREST_GROUP_DETAILS = 11
READ_INTEREST_GROUP_DETAILS = 12
ENUMERATE_COMPANY_DETAILS = 13
READ_COMPANY_DETAILS = 14
ENUMERATE_SHIP_DETAILS = 15
READ_SHIP_DETAILS = 16
HEADER = struct.Struct("<18I3Q")
COUNTRY_RECORD = struct.Struct("<4I2Q")
STATE_RECORD = struct.Struct("<4IQ2I")
BUILDING_RECORD = struct.Struct("<4IQIiQ")
POP_RECORD = struct.Struct("<4I2Q")
MARKET_RECORD = struct.Struct("<4IQ2I")
MILITARY_FORMATION_RECORD = struct.Struct("<4IQ2IQ")
WAR_RECORD = struct.Struct("<4IQ2IQ4I2Q")
DIPLOMATIC_PLAY_RECORD = struct.Struct("<4IQ4I2Q")
CHARACTER_RECORD = struct.Struct("<4IQ2I")
SHIP_RECORD = struct.Struct("<4IQ" + "2IQ" * 4)
SHIP_DETAILS_RECORD = struct.Struct("<4IQ" + "2IQ" * 4 + "4I")
COMPANY_RECORD = struct.Struct("<4IQ2I2Q2I")
COMPANY_DETAILS_RECORD = struct.Struct("<4IQ2I2Q6I")
SHIPPING_LANE_RECORD = struct.Struct("<4IQ" + "2IQ" * 5 + "2Q2I")
INTEREST_GROUP_RECORD = struct.Struct("<4IQ2IQ")
INTEREST_GROUP_DETAILS_RECORD = struct.Struct("<4IQ2IQ4I")
DEFINITION_OPTIONS = struct.Struct("<4I2Q")
LAW_DEFINITION_RECORD = struct.Struct("<iiIIQiIQ8I")
TECHNOLOGY_DEFINITION_RECORD = struct.Struct("<iiIIQiIQ6I")
POP_DETAILS_RECORD = struct.Struct("<4I2Q3q6I2Q")
MARKET_GOODS_INPUT = struct.Struct("<IIQ")
MARKET_GOODS_RECORD = struct.Struct("<4I3Qi3I")
MARKET_GOODS_DETAILS_RECORD = struct.Struct("<4I3Qi3I4Q4I")
UINT32_MAX = 0xFFFFFFFF


@dataclass(frozen=True)
class Country:
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    gdp_raw64: int

    @property
    def valid(self) -> bool:
        return self.error == 0


@dataclass(frozen=True)
class State:
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    owner_id: int
    reserved: int

    @property
    def valid(self) -> bool:
        return self.error == 0


@dataclass(frozen=True)
class Building:
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    state_id: int
    levels: int
    type_pointer: int

    @property
    def valid(self) -> bool:
        return self.error == 0


@dataclass(frozen=True)
class Pop:
    """Native full-ID snapshot and borrowed Type pointer; not a population total."""
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    type_pointer: int

    @property
    def valid(self) -> bool:
        return self.error == 0


@dataclass(frozen=True)
class Market:
    """Native reference origin and cached full Country owner ID (may be -1)."""
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    owner_id: int
    reserved: int

    @property
    def valid(self) -> bool:
        return self.error == 0


@dataclass(frozen=True)
class MilitaryFormation:
    """True full-ID formation and separately checked Country owner relation."""
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    owner_country_id: int
    owner_country_error: int
    owner_country_pointer: int

    @property
    def valid(self) -> bool:
        return self.error == 0


@dataclass(frozen=True)
class War:
    """War -> actual DiplomaticPlay -> two Country leaders, each with own error."""
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    diplomatic_play_id: int
    diplomatic_play_error: int
    diplomatic_play_pointer: int
    attacker_leader_id: int
    defender_leader_id: int
    attacker_leader_error: int
    defender_leader_error: int
    attacker_leader_pointer: int
    defender_leader_pointer: int

    @property
    def valid(self) -> bool:
        return self.error == 0


@dataclass(frozen=True)
class DiplomaticPlay:
    """Full native play ID and independently checked attacker/defender leaders."""
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    attacker_leader_id: int
    defender_leader_id: int
    attacker_leader_error: int
    defender_leader_error: int
    attacker_leader_pointer: int
    defender_leader_pointer: int

    @property
    def valid(self) -> bool:
        return self.error == 0


@dataclass(frozen=True)
class Character:
    """Full native Character ID and exact IsFemale leaf result, with its own error."""
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    is_female: int
    is_female_error: int

    @property
    def valid(self) -> bool:
        return self.error == 0

    @property
    def is_female_valid(self) -> bool:
        return self.valid and self.is_female_error == 0


@dataclass(frozen=True)
class Ship:
    """Live fullIDShip with four actualref relations, not ShipType/CombatUnit IDs."""
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    country_id: int
    country_error: int
    country_pointer: int
    fleet_id: int
    fleet_error: int
    fleet_pointer: int
    template_version_id: int
    template_version_error: int
    template_version_pointer: int
    template_id: int
    template_error: int
    template_pointer: int

    @property
    def valid(self) -> bool:
        return self.error == 0

    @property
    def has_country(self) -> bool:
        return self.valid and self.country_error == 0

    @property
    def has_fleet(self) -> bool:
        return self.valid and self.fleet_error == 0

    @property
    def has_template(self) -> bool:
        return self.valid and self.template_version_error == 0 and self.template_error == 0


@dataclass(frozen=True)
class ShipDetails(Ship):
    """Original GetNameNoFormatting bytes; not formatted DataShip.GetName cache."""
    name_no_formatting_error: int
    name_no_formatting_offset: int
    name_no_formatting_bytes: int
    reserved: int
    name_no_formatting_utf8: bytes = b""

    @property
    def has_name_no_formatting(self) -> bool:
        return self.valid and self.name_no_formatting_error == 0

    @property
    def name_no_formatting(self) -> str | None:
        return self.name_no_formatting_utf8.decode("utf-8") if self.has_name_no_formatting else None


@dataclass(frozen=True)
class ShipOptions:
    text_offset: int
    text_capacity: int
    text_bytes_written: int
    reserved: int
    text_bytes_required: int
    required_buffer_size: int


@dataclass(frozen=True)
class Company:
    """Real live fullID Company; CompanyType is a separate static borrowed pointer."""
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    country_id: int
    country_error: int
    country_pointer: int
    company_type_pointer: int
    company_type_error: int
    reserved: int

    @property
    def valid(self) -> bool:
        return self.error == 0

    @property
    def has_country(self) -> bool:
        return self.valid and self.country_error == 0

    @property
    def has_company_type(self) -> bool:
        return self.valid and self.company_type_error == 0


@dataclass(frozen=True)
class CompanyDetails(Company):
    """Complete original native localized Name bytes, not a CompanyType key."""
    name_error: int
    name_offset: int
    name_bytes: int
    name_reserved: int
    name_utf8: bytes = b""

    @property
    def has_name(self) -> bool:
        return self.valid and self.name_error == 0

    @property
    def name(self) -> str | None:
        return self.name_utf8.decode("utf-8") if self.has_name else None


@dataclass(frozen=True)
class CompanyOptions:
    text_offset: int
    text_capacity: int
    text_bytes_written: int
    reserved: int
    text_bytes_required: int
    required_buffer_size: int


@dataclass(frozen=True)
class ShippingLane:
    """Real current transport fullID, checked Country/State/effectiveMarket joins.

    Begin/end use native names, not an inferred import/export direction. Convoy
    cost retains native64bits before its Data int32 truncation; effectiveness is
    the original CFixedPoint getter. Scalars are not percentages or Trade IDs.
    """
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    country_id: int
    country_error: int
    country_pointer: int
    begin_state_id: int
    begin_state_error: int
    begin_state_pointer: int
    end_state_id: int
    end_state_error: int
    end_state_pointer: int
    begin_market_id: int
    begin_market_error: int
    begin_market_pointer: int
    end_market_id: int
    end_market_error: int
    end_market_pointer: int
    convoy_cost_raw64: int
    effectiveness_raw64: int
    convoy_cost_error: int
    effectiveness_error: int

    @property
    def valid(self) -> bool:
        return self.error == 0

    @property
    def has_country(self) -> bool:
        return self.valid and self.country_error == 0

    @property
    def has_begin_market(self) -> bool:
        return self.valid and self.begin_market_error == 0

    @property
    def has_end_market(self) -> bool:
        return self.valid and self.end_market_error == 0


@dataclass(frozen=True)
class InterestGroup:
    """Live full-generation IG instance and independently checked Country relation."""
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    country_id: int
    country_error: int
    country_pointer: int

    @property
    def valid(self) -> bool:
        return self.error == 0

    @property
    def has_country(self) -> bool:
        return self.valid and self.country_error == 0


@dataclass(frozen=True)
class InterestGroupDetails(InterestGroup):
    """Copied complete native localized display bytes, not a definition key."""
    name_error: int
    name_offset: int
    name_bytes: int
    reserved: int
    name_utf8: bytes = b""

    @property
    def has_name(self) -> bool:
        return self.valid and self.name_error == 0

    @property
    def name(self) -> str | None:
        return self.name_utf8.decode("utf-8") if self.has_name else None


@dataclass(frozen=True)
class InterestGroupOptions:
    text_offset: int
    text_capacity: int
    text_bytes_written: int
    reserved: int
    text_bytes_required: int
    required_buffer_size: int


@dataclass(frozen=True)
class PopDetails:
    requested_id: int
    resolved_id: int
    error: int
    slot_index: int
    pointer: int
    type_pointer: int
    total_size: int
    workforce: int
    dependents: int
    state_id: int
    country_id: int
    quantities_error: int
    state_error: int
    country_error: int
    reserved: int
    state_pointer: int
    country_pointer: int

    @property
    def valid(self) -> bool:
        return self.error == 0


@dataclass(frozen=True)
class MarketGoodsValue:
    requested_market_id: int
    resolved_market_id: int
    market_error: int
    goods_error: int
    market_pointer: int
    goods_pointer: int
    price_raw64: int
    goods_index: int
    definition_index: int
    price_error: int
    reserved: int

    @property
    def valid(self) -> bool:
        return self.price_error == 0

    @property
    def price_raw64_signed(self) -> int:
        return self.price_raw64 - (1 << 64) if self.price_raw64 & (1 << 63) else self.price_raw64


@dataclass(frozen=True)
class MarketGoodsDetails(MarketGoodsValue):
    """Native ordinary-Goods orders/trade raw64; no guessed physical/display units."""
    buy_orders_raw64: int
    sell_orders_raw64: int
    imports_raw64: int
    exports_raw64: int
    buy_orders_error: int
    sell_orders_error: int
    imports_error: int
    exports_error: int

    @property
    def valid(self) -> bool:
        return not (self.price_error or self.buy_orders_error or self.sell_orders_error
                    or self.imports_error or self.exports_error)


@dataclass(frozen=True)
class DefinitionOptions:
    text_offset: int
    text_capacity: int
    text_bytes_written: int
    reserved: int
    text_bytes_required: int
    required_buffer_size: int


@dataclass(frozen=True)
class LawDefinition:
    """CLawType definition ordinal/key and a checked LawGroup, not CountryLaw ID."""
    requested_index: int
    resolved_index: int
    error: int
    key_error: int
    pointer: int
    group_index: int
    group_error: int
    group_pointer: int
    key_offset: int
    key_bytes_count: int
    key_size: int
    group_key_offset: int
    group_key_bytes_count: int
    group_key_size: int
    group_key_error: int
    reserved: int
    key_bytes: bytes
    group_key_bytes: bytes

    @property
    def valid(self) -> bool:
        return self.error == 0

    @property
    def key(self) -> str:
        return self.key_bytes.decode("utf-8")

    @property
    def group_key(self) -> str:
        return self.group_key_bytes.decode("utf-8")


@dataclass(frozen=True)
class TechnologyDefinition:
    """CTechnologyType ordinal/key and native Category, not live research status."""
    requested_index: int
    resolved_index: int
    error: int
    key_error: int
    pointer: int
    category_index: int
    category_error: int
    category_pointer: int
    category_token: int
    category_token_error: int
    category_is_default: int
    key_offset: int
    key_bytes_count: int
    key_size: int
    key_bytes: bytes

    @property
    def valid(self) -> bool:
        return self.error == 0

    @property
    def key(self) -> str:
        return self.key_bytes.decode("utf-8")

    @property
    def has_category(self) -> bool:
        return self.valid and not self.category_error and not self.category_is_default


_LAYOUTS = {
    Domain.COUNTRY: (COUNTRY_RECORD, Country),
    Domain.STATE: (STATE_RECORD, State),
    Domain.BUILDING: (BUILDING_RECORD, Building),
    Domain.POP: (POP_RECORD, Pop),
    Domain.MARKET: (MARKET_RECORD, Market),
    Domain.MILITARY_FORMATION: (MILITARY_FORMATION_RECORD, MilitaryFormation),
    Domain.WAR: (WAR_RECORD, War),
    Domain.DIPLOMATIC_PLAY: (DIPLOMATIC_PLAY_RECORD, DiplomaticPlay),
    Domain.CHARACTER: (CHARACTER_RECORD, Character),
    Domain.INTEREST_GROUP: (INTEREST_GROUP_RECORD, InterestGroup),
    Domain.SHIPPING_LANE: (SHIPPING_LANE_RECORD, ShippingLane),
    Domain.COMPANY: (COMPANY_RECORD, Company),
    Domain.SHIP: (SHIP_RECORD, Ship),
}


def _layout(domain: Domain, operation: int):
    if domain == Domain.SHIP and operation in (ENUMERATE_SHIP_DETAILS, READ_SHIP_DETAILS):
        return SHIP_DETAILS_RECORD, ShipDetails
    if domain == Domain.COMPANY and operation in (ENUMERATE_COMPANY_DETAILS, READ_COMPANY_DETAILS):
        return COMPANY_DETAILS_RECORD, CompanyDetails
    if domain == Domain.INTEREST_GROUP and operation in (ENUMERATE_INTEREST_GROUP_DETAILS, READ_INTEREST_GROUP_DETAILS):
        return INTEREST_GROUP_DETAILS_RECORD, InterestGroupDetails
    if operation in (ENUMERATE, READ_IDS) and domain in _LAYOUTS:
        return _LAYOUTS[domain]
    if domain == Domain.LAW_DEFINITION and operation in (ENUMERATE_DEFINITIONS, READ_DEFINITION_INDICES):
        return LAW_DEFINITION_RECORD, LawDefinition
    if domain == Domain.TECHNOLOGY_DEFINITION and operation in (ENUMERATE_DEFINITIONS, READ_DEFINITION_INDICES):
        return TECHNOLOGY_DEFINITION_RECORD, TechnologyDefinition
    if domain == Domain.POP and operation in (ENUMERATE_POP_DETAILS, READ_POP_DETAILS):
        return POP_DETAILS_RECORD, PopDetails
    if domain == Domain.MARKET and operation in (READ_MARKET_GOODS, ENUMERATE_MARKET_GOODS):
        return MARKET_GOODS_RECORD, MarketGoodsValue
    if domain == Domain.MARKET and operation in (READ_MARKET_GOODS_DETAILS, ENUMERATE_MARKET_GOODS_DETAILS):
        return MARKET_GOODS_DETAILS_RECORD, MarketGoodsDetails
    raise ValueError("operation is not supported for this domain")


@dataclass(frozen=True)
class ObjectsResult:
    size: int
    error: int
    domain: Domain
    operation: int
    start_index: int
    max_items: int
    capacity: int
    input_count: int
    ids_offset: int
    records_offset: int
    record_size: int
    returned_count: int
    checked_count: int
    scanned_count: int
    next_index: int
    slot_bound: int
    truncated: bool
    buffer_size: int
    database: int
    fallback: int
    world: int
    records: tuple[Country | State | Building | Pop | Market | MilitaryFormation | War | DiplomaticPlay | Character | InterestGroup | InterestGroupDetails | ShippingLane | Company | CompanyDetails | Ship | ShipDetails | LawDefinition | TechnologyDefinition | PopDetails | MarketGoodsValue | MarketGoodsDetails, ...]
    definition_options: DefinitionOptions | None = None
    interest_group_options: InterestGroupOptions | None = None
    company_options: CompanyOptions | None = None
    ship_options: ShipOptions | None = None

    @property
    def valid(self) -> bool:
        """Batch execution succeeded; READ_IDS can still contain invalid records."""
        return self.error == 0

    @classmethod
    def from_bytes(cls, data: bytes) -> ObjectsResult:
        values = list(HEADER.unpack_from(data))
        domain = Domain(values[2])
        layout, record_type = _layout(domain, values[3])
        offset, stride, count = values[9], values[10], values[11]
        if (values[0] != HEADER.size or values[17] != len(data)
                or stride != layout.size or count > values[6]
                or offset < HEADER.size or offset + count * stride > len(data)):
            raise ValueError("invalid native object result layout")
        if record_type is ShipDetails:
            at = values[8]
            if at < HEADER.size or at + DEFINITION_OPTIONS.size > offset:
                raise ValueError("invalid Ship text options")
            ship_options = ShipOptions(*DEFINITION_OPTIONS.unpack_from(data, at))
            if (ship_options.text_offset < offset + values[6] * stride
                    or ship_options.text_offset + ship_options.text_capacity > len(data)
                    or ship_options.text_bytes_written > ship_options.text_capacity):
                raise ValueError("invalid Ship text arena")
            ship_records = []
            for i in range(count):
                record = layout.unpack_from(data, offset + i * stride)
                name_error, name_at, name_bytes = record[17:20]
                if name_error:
                    if name_bytes:
                        raise ValueError("failed Ship NameNoFormatting has copied bytes")
                    name = b""
                else:
                    if (name_at < ship_options.text_offset or name_at + name_bytes
                            > ship_options.text_offset + ship_options.text_bytes_written):
                        raise ValueError("Ship NameNoFormatting is outside its complete text arena")
                    name = data[name_at:name_at + name_bytes]
                ship_records.append(record_type(*record, name))
            values[2] = domain
            values[16] = bool(values[16])
            return cls(*values, tuple(ship_records), None, None, None, ship_options)
        if record_type is CompanyDetails:
            at = values[8]
            if at < HEADER.size or at + DEFINITION_OPTIONS.size > offset:
                raise ValueError("invalid Company text options")
            company_options = CompanyOptions(*DEFINITION_OPTIONS.unpack_from(data, at))
            if (company_options.text_offset < offset + values[6] * stride
                    or company_options.text_offset + company_options.text_capacity > len(data)
                    or company_options.text_bytes_written > company_options.text_capacity):
                raise ValueError("invalid Company text arena")
            company_records = []
            for i in range(count):
                record = layout.unpack_from(data, offset + i * stride)
                name_error, name_at, name_bytes = record[11:14]
                if name_error:
                    if name_bytes:
                        raise ValueError("failed native Company name has copied bytes")
                    name = b""
                else:
                    if (name_at < company_options.text_offset or name_at + name_bytes
                            > company_options.text_offset + company_options.text_bytes_written):
                        raise ValueError("native Company name is outside its complete text arena")
                    name = data[name_at:name_at + name_bytes]
                company_records.append(record_type(*record, name))
            values[2] = domain
            values[16] = bool(values[16])
            return cls(*values, tuple(company_records), None, None, company_options)
        if record_type is InterestGroupDetails:
            at = values[8]
            if at < HEADER.size or at + DEFINITION_OPTIONS.size > offset:
                raise ValueError("invalid InterestGroup text options")
            ig_options = InterestGroupOptions(*DEFINITION_OPTIONS.unpack_from(data, at))
            if (ig_options.text_offset < offset + values[6] * stride
                    or ig_options.text_offset + ig_options.text_capacity > len(data)
                    or ig_options.text_bytes_written > ig_options.text_capacity):
                raise ValueError("invalid InterestGroup text arena")
            ig_records = []
            for i in range(count):
                record = layout.unpack_from(data, offset + i * stride)
                name_error, name_at, name_bytes = record[8:11]
                if name_error:
                    if name_bytes:
                        raise ValueError("failed native InterestGroup name has copied bytes")
                    name = b""
                else:
                    if (name_at < ig_options.text_offset or name_at + name_bytes
                            > ig_options.text_offset + ig_options.text_bytes_written):
                        raise ValueError("native InterestGroup name is outside its complete text arena")
                    name = data[name_at:name_at + name_bytes]
                ig_records.append(record_type(*record, name))
            values[2] = domain
            values[16] = bool(values[16])
            return cls(*values, tuple(ig_records), None, ig_options)
        options = None
        if record_type in (LawDefinition, TechnologyDefinition):
            options = DefinitionOptions(*DEFINITION_OPTIONS.unpack_from(data, values[8]))
            if (options.text_offset < offset + values[6] * stride
                    or options.text_offset + options.text_capacity > len(data)
                    or options.text_bytes_written > options.text_capacity):
                raise ValueError("invalid definition text arena")
            def copied_key(at: int, copied: int, required: int, error: int) -> bytes:
                if error:
                    if copied:
                        raise ValueError("failed native key has copied bytes")
                    return b""
                if (copied != required or at < options.text_offset
                        or at + copied > options.text_offset + options.text_bytes_written):
                    raise ValueError("native definition key is partial or outside its arena")
                return data[at:at + copied]
            records = []
            for i in range(count):
                record = layout.unpack_from(data, offset + i * stride)
                if record_type is LawDefinition:
                    key = copied_key(record[8], record[9], record[10], record[3])
                    group_key = copied_key(record[11], record[12], record[13], record[14])
                    records.append(record_type(*record, key, group_key))
                else:
                    key = copied_key(record[11], record[12], record[13], record[3])
                    records.append(record_type(*record, key))
            records = tuple(records)
        else:
            records = tuple(record_type(*layout.unpack_from(data, offset + i * stride))
                            for i in range(count))
        values[2] = domain
        values[16] = bool(values[16])
        return cls(*values, records, options)


def _uint32(name: str, value: int) -> int:
    if not isinstance(value, int) or not 0 <= value <= UINT32_MAX:
        raise ValueError(f"{name} must be uint32")
    return value


def _payload_buffer(domain: Domain, operation: int, start_index: int, max_items: int,
                    capacity: int, input_count: int, payload: bytes) -> bytes:
    domain = Domain(domain)
    start_index = _uint32("start_index", start_index)
    max_items = _uint32("max_items", max_items)
    capacity = _uint32("capacity", capacity)
    input_count = _uint32("input_count", input_count)
    layout, _ = _layout(domain, operation)
    ids_offset = HEADER.size if operation not in (ENUMERATE, ENUMERATE_POP_DETAILS) else 0
    records_offset = (HEADER.size + len(payload) + 7) & ~7
    buffer_size = _uint32("buffer_size", records_offset + capacity * layout.size)
    header = HEADER.pack(HEADER.size, ERROR_IO_PENDING, domain, operation,
        start_index, max_items, capacity, input_count, ids_offset, records_offset,
        layout.size, 0, 0, 0, start_index, 0, 0, buffer_size, 0, 0, 0)
    result = bytearray(buffer_size)
    result[:HEADER.size] = header
    result[HEADER.size:HEADER.size + len(payload)] = payload
    return bytes(result)


def _buffer(domain: Domain, operation: int, start_index: int, max_items: int,
            capacity: int, ids: tuple[int, ...]) -> bytes:
    for full_id in ids:
        _uint32("full_id", full_id)
    payload = b"".join(struct.pack("<I", full_id) for full_id in ids)
    return _payload_buffer(domain, operation, start_index, max_items, capacity, len(ids), payload)


def _submit_objects(client: NativeBridge, initial: bytes, *, adapter: str | int,
                    process: RemoteProcess | None) -> CallbackResult:
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3ObjectsInterface", decode_result=ObjectsResult.from_bytes,
        process=process)


def enumerate_objects(client: NativeBridge, domain: Domain, *, start_index: int,
                      max_items: int, capacity: int | None = None,
                      adapter: str | int = "pdx_v3_objects.dll",
                      process: RemoteProcess | None = None) -> CallbackResult:
    """Queue a caller-sized page of actual live objects, without a global cap.

    The native callback scans slots from ``start_index`` until the output page is
    full or the real slot bound is reached. Empty/fallback/stale IDs are skipped;
    every returned record passed its actual native resolver and complete-ID check.
    Continue with ``result.next_index`` only in the same still-live world. The
    bound is not a live count. ``truncated`` means slots remain unscanned, not that
    another live record definitely exists. A later page is a new native snapshot.
    """
    initial = _buffer(domain, ENUMERATE, start_index, max_items,
                      max_items if capacity is None else capacity, ())
    return _submit_objects(client, initial, adapter=adapter, process=process)


def read_ids(client: NativeBridge, domain: Domain, ids: Iterable[int], *,
             start_index: int = 0, max_items: int | None = None,
             capacity: int | None = None, adapter: str | int = "pdx_v3_objects.dll",
             process: RemoteProcess | None = None) -> CallbackResult:
    """Resolve a batch of full IDs in one scope, retaining per-ID native errors.

    Invalid IDs produce ERROR_NOT_FOUND records with the raw resolver pointer
    (usually the native fallback), not a fabricated valid zero-value object.
    ``start_index`` / ``next_index`` are indices into the supplied ID list here.
    No pointer or Type name is guessed. Native database absence is a batch error.
    """
    ids = tuple(ids)
    start_index = _uint32("start_index", start_index)
    remaining = max(0, len(ids) - start_index)
    max_items = remaining if max_items is None else max_items
    initial = _buffer(domain, READ_IDS, start_index, max_items,
                      max_items if capacity is None else capacity, ids)
    return _submit_objects(client, initial, adapter=adapter, process=process)


def enumerate_pop_details(client: NativeBridge, *, start_index: int, max_items: int,
                          capacity: int | None = None,
                          adapter: str | int = "pdx_v3_objects.dll",
                          process: RemoteProcess | None = None) -> CallbackResult:
    """One scope: three native count getters and full State/Country relations.

    Pop validity, quantities and both relations have separate error fields. A
    missing relation/component is not a fabricated valid zero; total_size is
    returned by the game's own GetTotalSize leaf, not Python-side summation.
    """
    initial = _buffer(Domain.POP, ENUMERATE_POP_DETAILS, start_index, max_items,
                      max_items if capacity is None else capacity, ())
    return _submit_objects(client, initial, adapter=adapter, process=process)


def read_pop_details(client: NativeBridge, ids: Iterable[int], *, start_index: int = 0,
                     max_items: int | None = None, capacity: int | None = None,
                     adapter: str | int = "pdx_v3_objects.dll",
                     process: RemoteProcess | None = None) -> CallbackResult:
    """Full-ID batch using explicit PopDetails96B records; legacy Pop32B stays unchanged."""
    ids = tuple(ids)
    start_index = _uint32("start_index", start_index)
    max_items = max(0, len(ids) - start_index) if max_items is None else max_items
    initial = _buffer(Domain.POP, READ_POP_DETAILS, start_index, max_items,
                      max_items if capacity is None else capacity, ids)
    return _submit_objects(client, initial, adapter=adapter, process=process)


def read_market_goods(client: NativeBridge, pairs: Iterable[tuple[int, int]], *,
                      start_index: int = 0, max_items: int | None = None,
                      capacity: int | None = None, details: bool = False,
                      adapter: str | int = "pdx_v3_objects.dll",
                      process: RemoteProcess | None = None) -> CallbackResult:
    """Batch explicit (complete MarketID, actual native CGoods pointer) price reads.

    ``details=True`` explicitly selects operation7/104B records with native
    buy/sell orders and imports/exports. Default operation5/56B is unchanged.
    Native definition lookup rechecks every Goods pointer/index. Goods indexes
    are definition indexes, not generation IDs. All pointers remain borrowed.
    """
    pairs = tuple(pairs)
    payload = bytearray()
    for market_id, goods_pointer in pairs:
        _uint32("market_id", market_id)
        if not isinstance(goods_pointer, int) or not 0 <= goods_pointer <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("goods_pointer must be uint64")
        payload.extend(MARKET_GOODS_INPUT.pack(market_id, 0, goods_pointer))
    start_index = _uint32("start_index", start_index)
    max_items = max(0, len(pairs) - start_index) if max_items is None else max_items
    operation = READ_MARKET_GOODS_DETAILS if details else READ_MARKET_GOODS
    initial = _payload_buffer(Domain.MARKET, operation, start_index,
        max_items, max_items if capacity is None else capacity, len(pairs), bytes(payload))
    return _submit_objects(client, initial, adapter=adapter, process=process)


def enumerate_market_goods(client: NativeBridge, market_id: int, *, start_index: int,
                           max_items: int, capacity: int | None = None, details: bool = False,
                           adapter: str | int = "pdx_v3_objects.dll",
                           process: RemoteProcess | None = None) -> CallbackResult:
    """Page actual Goods definitions and prices for one complete MarketID in one scope.

    ``details=True`` explicitly selects operation8/104B records. Default
    operation6/56B preserves the already verified price-only ABI.
    The native provider supplies the actual Goods database; its index getter
    supplies each pointer. slot_bound is definition count in this operation, not
    a live RefDB bound. Absent native price entries return genuine zero.
    """
    operation = ENUMERATE_MARKET_GOODS_DETAILS if details else ENUMERATE_MARKET_GOODS
    initial = _buffer(Domain.MARKET, operation, start_index, max_items,
                      max_items if capacity is None else capacity, (_uint32("market_id", market_id),))
    return _submit_objects(client, initial, adapter=adapter, process=process)


def _definition_buffer(operation: int, indices: tuple[int, ...], start_index: int,
                       max_items: int, capacity: int, text_capacity: int, *,
                       domain: Domain = Domain.LAW_DEFINITION) -> bytes:
    domain = Domain(domain)
    layout, _ = _layout(domain, operation)
    if domain not in (Domain.LAW_DEFINITION, Domain.TECHNOLOGY_DEFINITION):
        raise ValueError("explicit definition operation requires a definition domain")
    for index in indices:
        if not isinstance(index, int) or not -(1 << 31) <= index < (1 << 31):
            raise ValueError("definition index must be signed int32, not a full-generation ID")
    start_index = _uint32("start_index", start_index)
    max_items = _uint32("max_items", max_items)
    capacity = _uint32("capacity", capacity)
    text_capacity = _uint32("text_capacity", text_capacity)
    _uint32("input_count", len(indices))
    records_offset = (HEADER.size + DEFINITION_OPTIONS.size + len(indices) * 4 + 7) & ~7
    text_offset = records_offset + capacity * layout.size
    buffer_size = _uint32("buffer_size", text_offset + text_capacity)
    data = bytearray(buffer_size)
    HEADER.pack_into(data, 0, HEADER.size, ERROR_IO_PENDING, domain,
        operation, start_index, max_items, capacity, len(indices), HEADER.size,
        records_offset, layout.size, 0, 0, 0, start_index, 0, 0,
        buffer_size, 0, 0, 0)
    DEFINITION_OPTIONS.pack_into(data, HEADER.size, text_offset, text_capacity, 0, 0, 0, text_offset)
    for i, index in enumerate(indices):
        struct.pack_into("<i", data, HEADER.size + DEFINITION_OPTIONS.size + i * 4, index)
    return bytes(data)


def enumerate_law_definitions(client: NativeBridge, *, start_index: int, max_items: int,
                              capacity: int | None = None, text_capacity: int = 4096,
                              adapter: str | int = "pdx_v3_objects.dll",
                              process: RemoteProcess | None = None) -> CallbackResult:
    """Page actual CLawType definitions and copied native keys/checked LawGroup keys.

    Definition ordinals are not CountryLaw full IDs or Script scope/type IDs.
    Native provider/index/key getters run in one scope. With a short text arena,
    ERROR_MORE_DATA and required_buffer_size describe the next whole record;
    returned records form a complete prefix and next_index does not skip it.
    text_capacity is caller-selected, with no hidden global cap or auto-retry.
    """
    initial = _definition_buffer(ENUMERATE_DEFINITIONS, (), start_index, max_items,
        max_items if capacity is None else capacity, text_capacity)
    return _submit_objects(client, initial, adapter=adapter, process=process)


def read_law_definition_indices(client: NativeBridge, indices: Iterable[int], *,
                                start_index: int = 0, max_items: int | None = None,
                                capacity: int | None = None, text_capacity: int = 4096,
                                adapter: str | int = "pdx_v3_objects.dll",
                                process: RemoteProcess | None = None) -> CallbackResult:
    """Read signed definition ordinals using real LawType DB index lookup, not RefDB.

    Negative/out-of-count inputs retain per-record1168/fallback results. Keys are
    complete byte copies, never localized name guesses or partial UTF-8 strings.
    Check object/key/group errors before using an input for a native command.
    """
    indices = tuple(indices)
    start_index = _uint32("start_index", start_index)
    max_items = max(0, len(indices) - start_index) if max_items is None else max_items
    initial = _definition_buffer(READ_DEFINITION_INDICES, indices, start_index, max_items,
        max_items if capacity is None else capacity, text_capacity)
    return _submit_objects(client, initial, adapter=adapter, process=process)


def enumerate_technology_definitions(client: NativeBridge, *, start_index: int, max_items: int,
                                     capacity: int | None = None, text_capacity: int = 4096,
                                     adapter: str | int = "pdx_v3_objects.dll",
                                     process: RemoteProcess | None = None) -> CallbackResult:
    """Page real CTechnologyType definitions, exact native keys and checked Category.

    Native provider/index/key/category execute in one scope. Category token is a
    native interned-string token, not a type ID/command token/RefID. Default -1
    has category_is_default1/error1168; no token is fabricated from its fallback.
    Positive category bounds are checked before the native unchecked getter.
    Short text returns234 and next_index does not skip the unwritten record.
    """
    initial = _definition_buffer(ENUMERATE_DEFINITIONS, (), start_index, max_items,
        max_items if capacity is None else capacity, text_capacity,
        domain=Domain.TECHNOLOGY_DEFINITION)
    return _submit_objects(client, initial, adapter=adapter, process=process)


def read_technology_definition_indices(client: NativeBridge, indices: Iterable[int], *,
                                      start_index: int = 0, max_items: int | None = None,
                                      capacity: int | None = None, text_capacity: int = 4096,
                                      adapter: str | int = "pdx_v3_objects.dll",
                                      process: RemoteProcess | None = None) -> CallbackResult:
    """Read signed technology definition ordinals, never CountryTechnologyStatus IDs.

    Negative/out-of-count indices preserve native fallback plus per-record1168.
    Key bytes are complete copies; all pointers are borrowed same-world snapshots.
    No DataTechnology/DataEra wrapper is fabricated or interpreted as a definition.
    """
    indices = tuple(indices)
    start_index = _uint32("start_index", start_index)
    max_items = max(0, len(indices) - start_index) if max_items is None else max_items
    initial = _definition_buffer(READ_DEFINITION_INDICES, indices, start_index, max_items,
        max_items if capacity is None else capacity, text_capacity,
        domain=Domain.TECHNOLOGY_DEFINITION)
    return _submit_objects(client, initial, adapter=adapter, process=process)


def _interest_group_buffer(operation: int, ids: tuple[int, ...], start_index: int,
                           max_items: int, capacity: int, text_capacity: int) -> bytes:
    layout, _ = _layout(Domain.INTEREST_GROUP, operation)
    if operation not in (ENUMERATE_INTEREST_GROUP_DETAILS, READ_INTEREST_GROUP_DETAILS):
        raise ValueError("InterestGroup names require their explicit detail operation")
    if operation == ENUMERATE_INTEREST_GROUP_DETAILS and ids:
        raise ValueError("InterestGroup enumeration has no input IDs")
    for full_id in ids:
        _uint32("InterestGroup full ID", full_id)
    start_index = _uint32("start_index", start_index)
    max_items = _uint32("max_items", max_items)
    capacity = _uint32("capacity", capacity)
    text_capacity = _uint32("text_capacity", text_capacity)
    _uint32("input_count", len(ids))
    records_offset = (HEADER.size + DEFINITION_OPTIONS.size + len(ids) * 4 + 7) & ~7
    text_offset = records_offset + capacity * layout.size
    buffer_size = _uint32("buffer_size", text_offset + text_capacity)
    data = bytearray(buffer_size)
    HEADER.pack_into(data, 0, HEADER.size, ERROR_IO_PENDING, Domain.INTEREST_GROUP,
        operation, start_index, max_items, capacity, len(ids), HEADER.size,
        records_offset, layout.size, 0, 0, 0, start_index, 0, 0,
        buffer_size, 0, 0, 0)
    DEFINITION_OPTIONS.pack_into(data, HEADER.size, text_offset, text_capacity, 0, 0, 0, text_offset)
    for i, full_id in enumerate(ids):
        struct.pack_into("<I", data, HEADER.size + DEFINITION_OPTIONS.size + i * 4, full_id)
    return bytes(data)


def enumerate_interest_group_details(client: NativeBridge, *, start_index: int,
                                     max_items: int, capacity: int | None = None,
                                     text_capacity: int = 4096,
                                     adapter: str | int = "pdx_v3_objects.dll",
                                     process: RemoteProcess | None = None) -> CallbackResult:
    """Page real IG full IDs, checked Country relations and native display names.

    Ops1/2 still provide InterestGroup's explicit40B base via enumerate_objects
    and read_ids. This56B detail operation invokes the original ownedCString
    name builder and game destructor in one scope, copying complete UTF8 bytes
    to the caller arena. Name can include localization/markup; it is not a key.
    Short text returns234 with required64 sizes and next_index on the unwritten
    slot. No partial name, hidden cap, auto-retry or durable borrowed pointer.
    """
    initial = _interest_group_buffer(ENUMERATE_INTEREST_GROUP_DETAILS, (), start_index,
        max_items, max_items if capacity is None else capacity, text_capacity)
    return _submit_objects(client, initial, adapter=adapter, process=process)


def read_interest_group_details(client: NativeBridge, ids: Iterable[int], *,
                                start_index: int = 0, max_items: int | None = None,
                                capacity: int | None = None, text_capacity: int = 4096,
                                adapter: str | int = "pdx_v3_objects.dll",
                                process: RemoteProcess | None = None) -> CallbackResult:
    """Read complete uint32 IG instance IDs, never IGType definition ordinals.

    Native resolver checks full generation. Invalid/stale/fallback records keep
    object/name errors; Country failure is independent of a valid IG. Complete
    native names are copied before the original owned CString is destroyed.
    All execution/copy/free uses the shared CallbackResult, including pending.
    """
    ids = tuple(ids)
    start_index = _uint32("start_index", start_index)
    max_items = max(0, len(ids) - start_index) if max_items is None else max_items
    initial = _interest_group_buffer(READ_INTEREST_GROUP_DETAILS, ids, start_index,
        max_items, max_items if capacity is None else capacity, text_capacity)
    return _submit_objects(client, initial, adapter=adapter, process=process)


def _company_buffer(operation: int, ids: tuple[int, ...], start_index: int,
                    max_items: int, capacity: int, text_capacity: int) -> bytes:
    layout, _ = _layout(Domain.COMPANY, operation)
    if operation not in (ENUMERATE_COMPANY_DETAILS, READ_COMPANY_DETAILS):
        raise ValueError("Company names require their explicit detail operation")
    if operation == ENUMERATE_COMPANY_DETAILS and ids:
        raise ValueError("Company enumeration has no input IDs")
    for full_id in ids:
        _uint32("Company full ID", full_id)
    start_index = _uint32("start_index", start_index)
    max_items = _uint32("max_items", max_items)
    capacity = _uint32("capacity", capacity)
    text_capacity = _uint32("text_capacity", text_capacity)
    _uint32("input_count", len(ids))
    records_offset = (HEADER.size + DEFINITION_OPTIONS.size + len(ids) * 4 + 7) & ~7
    text_offset = records_offset + capacity * layout.size
    buffer_size = _uint32("buffer_size", text_offset + text_capacity)
    data = bytearray(buffer_size)
    HEADER.pack_into(data, 0, HEADER.size, ERROR_IO_PENDING, Domain.COMPANY,
        operation, start_index, max_items, capacity, len(ids), HEADER.size,
        records_offset, layout.size, 0, 0, 0, start_index, 0, 0,
        buffer_size, 0, 0, 0)
    DEFINITION_OPTIONS.pack_into(data, HEADER.size, text_offset, text_capacity, 0, 0, 0, text_offset)
    for i, full_id in enumerate(ids):
        struct.pack_into("<I", data, HEADER.size + DEFINITION_OPTIONS.size + i * 4, full_id)
    return bytes(data)


def enumerate_company_details(client: NativeBridge, *, start_index: int,
                              max_items: int, capacity: int | None = None,
                              text_capacity: int = 4096,
                              adapter: str | int = "pdx_v3_objects.dll",
                              process: RemoteProcess | None = None) -> CallbackResult:
    """Page current liveCompanies, checkedCountry/staticType and originalnativeName.

    Base ops1/2 remain explicit56B through enumerate_objects/read_ids. This72B
    detailop constructs/copies/destroys the originalownedCString in one native
    scope. CompanyType remains a real borroweddefinitionpointer, not livefullID,
    guessedordinal or key. Shorttext returns234/required64/next_index stays on
    unwrittenrecord. No fabricatedData wrapper, auto-retry or durablepointer.
    """
    initial = _company_buffer(ENUMERATE_COMPANY_DETAILS, (), start_index, max_items,
        max_items if capacity is None else capacity, text_capacity)
    return _submit_objects(client, initial, adapter=adapter, process=process)


def read_company_details(client: NativeBridge, ids: Iterable[int], *,
                         start_index: int = 0, max_items: int | None = None,
                         capacity: int | None = None, text_capacity: int = 4096,
                         adapter: str | int = "pdx_v3_objects.dll",
                         process: RemoteProcess | None = None) -> CallbackResult:
    """Read fulluint32Company instanceIDs, not CompanyType definitions.

    Invalid/stale IDs retain nativefallback/error; Country/Type/Name errors stay
    separate. CompleteName bytes may contain native localization/markup. ZeroID
    is not automaticallyinvalid, and emptyerror0Name is not unavailable.
    CurrentGetState/Building-region fallback is not silently converted into an
    invented HeadquarterStateId field. SharedCallbackResult handles ownership.
    """
    ids = tuple(ids)
    start_index = _uint32("start_index", start_index)
    max_items = max(0, len(ids) - start_index) if max_items is None else max_items
    initial = _company_buffer(READ_COMPANY_DETAILS, ids, start_index, max_items,
        max_items if capacity is None else capacity, text_capacity)
    return _submit_objects(client, initial, adapter=adapter, process=process)


def _ship_buffer(operation: int, ids: tuple[int, ...], start_index: int,
                 max_items: int, capacity: int, text_capacity: int) -> bytes:
    layout, _ = _layout(Domain.SHIP, operation)
    if operation not in (ENUMERATE_SHIP_DETAILS, READ_SHIP_DETAILS):
        raise ValueError("Ship names require their explicit detail operation")
    if operation == ENUMERATE_SHIP_DETAILS and ids:
        raise ValueError("Ship enumeration has no input IDs")
    for full_id in ids:
        _uint32("Ship full ID", full_id)
    start_index = _uint32("start_index", start_index)
    max_items = _uint32("max_items", max_items)
    capacity = _uint32("capacity", capacity)
    text_capacity = _uint32("text_capacity", text_capacity)
    _uint32("input_count", len(ids))
    records_offset = (HEADER.size + DEFINITION_OPTIONS.size + len(ids) * 4 + 7) & ~7
    text_offset = records_offset + capacity * layout.size
    buffer_size = _uint32("buffer_size", text_offset + text_capacity)
    data = bytearray(buffer_size)
    HEADER.pack_into(data, 0, HEADER.size, ERROR_IO_PENDING, Domain.SHIP,
        operation, start_index, max_items, capacity, len(ids), HEADER.size,
        records_offset, layout.size, 0, 0, 0, start_index, 0, 0,
        buffer_size, 0, 0, 0)
    DEFINITION_OPTIONS.pack_into(data, HEADER.size, text_offset, text_capacity, 0, 0, 0, text_offset)
    for i, full_id in enumerate(ids):
        struct.pack_into("<I", data, HEADER.size + DEFINITION_OPTIONS.size + i * 4, full_id)
    return bytes(data)


def enumerate_ship_details(client: NativeBridge, *, start_index: int, max_items: int,
                           capacity: int | None = None, text_capacity: int = 4096,
                           adapter: str | int = "pdx_v3_objects.dll",
                           process: RemoteProcess | None = None) -> CallbackResult:
    """Page realShip fullIDs, fourrefrelations and originalNameNoFormatting bytes.

    Base88B remains through enumerate_objects/read_ids. This104B detailop uses
    original17be620(rawShip,outCString32), successfulconstructor guard/game dtor
    in the same Interface(1,0)scope. It never calls formattedData cache1cf3650 or
    fabricateswrappers. Wholetext234 preserves nextunwrittenitem/required64.
    Fleet is assignedMilitaryFormation, not conditionalGetTargetFleet. Template
    crossesactualVersion/Template fullIDdomains, not ShipType definition/Design.
    """
    initial = _ship_buffer(ENUMERATE_SHIP_DETAILS, (), start_index, max_items,
        max_items if capacity is None else capacity, text_capacity)
    return _submit_objects(client, initial, adapter=adapter, process=process)


def read_ship_details(client: NativeBridge, ids: Iterable[int], *, start_index: int = 0,
                      max_items: int | None = None, capacity: int | None = None,
                      text_capacity: int = 4096, adapter: str | int = "pdx_v3_objects.dll",
                      process: RemoteProcess | None = None) -> CallbackResult:
    """Read fulluint32liveShip IDs; invalid/stale fallback and eachfield error stay.

    Only the original NameNoFormatting is copied. It is not a stablekey or the
    formattedUIName; metadata/borrowedpointers are sameworld snapshots only.
    No automaticpoll/replay, fakeShipGroup0/filter, or directShipList list_element.
    SharedCallbackResult owns pending/copy/free as before.
    """
    ids = tuple(ids)
    start_index = _uint32("start_index", start_index)
    max_items = max(0, len(ids) - start_index) if max_items is None else max_items
    initial = _ship_buffer(READ_SHIP_DETAILS, ids, start_index, max_items,
        max_items if capacity is None else capacity, text_capacity)
    return _submit_objects(client, initial, adapter=adapter, process=process)
