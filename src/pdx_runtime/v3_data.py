"""V3 engine DataFactory expressions, object contexts and typed/raw results.

Expressions are the game's native data-system syntax, e.g. ``Country.GetGDP``.
Type IDs come from enumerate_types(), not a hardcoded enum. Native CData wrapper pointers
are inputs to the engine's registered wrapper-to-Any conversion. Country full IDs
use the full native wrapper constructor. They are not getter callbacks. All requests use the existing public Interface queue.

A successful evaluate result owns an engine CPdxAny value in the adapter. Its
adapter-owned dependencies, object wrappers and list descriptors remain held until
explicit release. Game-owned buffers can still change when the simulation changes.
The copied Python snapshot is separate from that ownership. No polling or replay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import struct
from typing import TYPE_CHECKING, Iterable, Mapping, Protocol, TypeAlias, cast

from .native_bridge import NativeBridge, RemoteProcess
from .v3 import CallbackResult, ERROR_IO_PENDING, submit_callback

if TYPE_CHECKING:
    from .v3_access import AccessResult


_RawTypeInfo: TypeAlias = tuple[int, int, int, int, int, int, int, bytes, bytes]
_RawFunctionInfo: TypeAlias = tuple[int, int, int, int, int, int, int, int, int, int, int, bytes, bytes]
_RawRequest: TypeAlias = tuple[
    int, int, int, int, int, int, int, int, int, int,
    int, int, int, int, int, int, int, int, int, int,
    int, int, int, int, int, int, int, bytes, bytes,
]
_RawCommand: TypeAlias = tuple[int, int, int]
_RawElementSuffix: TypeAlias = tuple[int, int, int, int, int, int, int, int, int]
_RawSourceObservation: TypeAlias = tuple[int, int, int, int, int, int, int, int, int, int, int, int]
_RawGameRuleSelection: TypeAlias = tuple[
    int, int, int, int, int, int, int, int, int, int,
    int, int, int, int, int, int, int, int, int, int,
    int, int, int, int, int, int, int, int, int, int,
    int, int, int, int, int, int, int, int, int, int,
]


class _PageDecodeContext(Protocol):
    query: ProjectionPageQuery


class _SequenceDecodeContext(Protocol):
    partial_sequence: ActionSequenceResult


class _AccessDecodeContext(Protocol):
    query: CallbackResult
    copied_bytes: bytes
    completion_status: Mapping[str, object] | None
    partial_result: AccessResult


class _RuleDecodeContext(Protocol):
    partial_game_rule_selection: GameRuleSelectionResult


REQUEST = struct.Struct("<4Ii7I6Q8IQ40s192s")
TYPE_INFO = struct.Struct("<i3I3Q128s256s")
FUNCTION_INFO = struct.Struct("<ii4I5Q128s256s")
BINDING_INPUT = struct.Struct("<iIQ")
ERROR_MORE_DATA = 234
ACTION_SUFFIX = struct.Struct("<6I")
ANY_INPUT = struct.Struct("<IIQQ")
ACTION_HEADER_SIZE = REQUEST.size + ACTION_SUFFIX.size
RETAINED_INPUT = 0x100
UTF8_INPUT = 0x101
EXPLICIT_ACTION = 1
ELEMENT_SUFFIX = struct.Struct("<Q4Ii3I")
ELEMENT_HEADER_SIZE = ACTION_HEADER_SIZE + ELEMENT_SUFFIX.size
POINTER_ARRAY_SUFFIX = struct.Struct("<3Ii6I")
ARRAY_ELEMENT_HEADER_SIZE = ELEMENT_HEADER_SIZE + POINTER_ARRAY_SUFFIX.size
ARRAY_COUNT_ONLY = 1
ARRAY_DIRECT_CONTEXT = 2
ARRAY_SOURCE_GUARD = 4
ARRAY_ELEMENT_SUBOBJECT = 8
SUBOBJECT_SOURCE_SUFFIX = struct.Struct("<IIQ")
SOURCE_GUARD_SUFFIX = struct.Struct("<6I")
GUARDED_ARRAY_HEADER_SIZE = ARRAY_ELEMENT_HEADER_SIZE + SOURCE_GUARD_SUFFIX.size
PROJECTION_SUFFIX = struct.Struct("<Q4I")
PROJECTION_HEADER_SIZE = REQUEST.size + PROJECTION_SUFFIX.size
SCOPED_PROJECTION_SUFFIX = struct.Struct("<4I")
SCOPED_PROJECTION_HEADER_SIZE = PROJECTION_HEADER_SIZE + SCOPED_PROJECTION_SUFFIX.size
CREATION_STEP = struct.Struct("<8IiI")
MAX_SCOPED_DEPTH = 8


class Action(IntEnum):
    TYPES = 1
    FUNCTIONS = 2
    EVALUATE = 3
    SNAPSHOT = 4
    RELEASE = 5
    LIST_COUNT = 6
    LIST_ELEMENT = 7
    INVOKE_ACTION = 8
    CREATE_STATE_NAME_CONTEXT = 9
    STATE_NAME_DRAFT = 10
    STATE_NAME_OVERRIDE = 11
    COMMAND_VALUE = 12
    EVALUATE_ELEMENT = 13
    INVOKE_ELEMENT_ACTION = 14
    EVALUATE_ARRAY_ELEMENT = 15
    INVOKE_ARRAY_ELEMENT_ACTION = 16
    EVALUATE_COPIED = 17
    SOURCE_COUNT = 18
    EVALUATE_SCOPED = 19
    SCOPED_SOURCE_COUNT = 20
    INVOKE_SOURCE_INDEX_ACTION = 21
    CREATE_OWNED_CONTEXT = 22


class NativeFieldKind(IntEnum):
    DECREE_TYPE_KEY = 1
    DECREE_ID = 2
    GAME_RULE_KEY = 3


class NativeSourceKind(IntEnum):
    PRESET_STORED_SETTINGS = 1
    FULL_COMPANY_TYPES = 2


class Kind(IntEnum):
    RAW = 0
    EMPTY = 1
    BOOL = 2
    I32 = 3
    U32 = 4
    I64 = 5
    U64 = 6
    FLOAT = 7
    DOUBLE = 8
    FIXED = 9
    STRING = 10
    OBJECT = 11
    LIST = 12
    COMMAND = 13


def _text(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("utf-8", "replace")


@dataclass(frozen=True)
class ObjectBinding:
    """Bind an engine object or a retained result to a dynamic DataFactory type.

    object is a native CData wrapper pointer when retained=False. With retained=True,
    object is a value_handle returned by this adapter, and the engine converter
    extracts its scope. Neither form writes a game object or assumes its layout.
    """
    type_id: int
    object: int
    retained: bool = False
    country_id: bool = False
    domain: str | None = None

    def pack(self) -> bytes:
        if self.retained and (self.country_id or self.domain is not None):
            raise ValueError("retained value and full ID are different binding forms")
        if self.country_id and self.domain not in (None, "Country"):
            raise ValueError("Country ID cannot select a different domain")
        flags = {"Country": 2, "State": 3, "Pop": 4, "Market": 5, "Building": 6}
        flag = flags[self.domain] if self.domain is not None else 2 if self.country_id else int(self.retained)
        return BINDING_INPUT.pack(self.type_id, flag, self.object)

    @classmethod
    def full_id(cls, domain: str, type_id: int, full_id: int) -> ObjectBinding:
        """Use the native domain wrapper path; type_id is still runtime metadata.

        Domain selects a proven CData factory/cache getter, not a getter catalog.
        Native unsupported/not-ready/invalid-full-ID results remain explicit.
        """
        if domain not in ("Country", "State", "Pop", "Market", "Building"):
            raise ValueError("unknown full-ID domain")
        if not 0 <= full_id <= 0xffffffff:
            raise ValueError("full_id must be uint32")
        return cls(type_id, full_id, domain=domain)

    @classmethod
    def state(cls, type_id: int, full_id: int) -> ObjectBinding:
        return cls.full_id("State", type_id, full_id)

    @classmethod
    def pop(cls, type_id: int, full_id: int) -> ObjectBinding:
        return cls.full_id("Pop", type_id, full_id)

    @classmethod
    def market(cls, type_id: int, full_id: int) -> ObjectBinding:
        return cls.full_id("Market", type_id, full_id)

    @classmethod
    def building(cls, type_id: int, full_id: int) -> ObjectBinding:
        return cls.full_id("Building", type_id, full_id)

    @classmethod
    def country(cls, type_id: int, full_id: int) -> ObjectBinding:
        """Resolve a full Country ID and construct its native CDataCountry wrapper."""
        if not 0 <= full_id <= 0xffffffff:
            raise ValueError("full_id must be uint32")
        return cls(type_id, full_id, country_id=True)


@dataclass(frozen=True)
class TypeInfo:
    type_id: int
    name_bytes: int
    description_bytes: int
    flags: int
    native_record: int
    object_to_any: int
    any_to_object: int
    name: str
    description: str

    @classmethod
    def from_bytes(cls, data: bytes) -> TypeInfo:
        fields = cast(_RawTypeInfo, TYPE_INFO.unpack(data))
        return cls(*fields[:-2], _text(fields[-2]), _text(fields[-1]))

    @property
    def can_bind_object(self) -> bool:
        return bool(self.flags & 1)


@dataclass(frozen=True)
class FunctionInfo:
    type_id: int
    return_type_id: int
    argument_count: int
    flags: int
    name_bytes: int
    description_bytes: int
    native_descriptor: int
    callbacks: tuple[int, int, int, int]
    name: str
    description: str

    @classmethod
    def from_bytes(cls, data: bytes) -> FunctionInfo:
        fields = cast(_RawFunctionInfo, FUNCTION_INFO.unpack(data))
        return cls(*fields[:7], fields[7:11], _text(fields[11]), _text(fields[12]))


@dataclass(frozen=True)
class FixedPoint:
    """Exact engine fixed-point bits; scale is not silently guessed."""
    bits: int

    @property
    def signed(self) -> int:
        return struct.unpack("<q", struct.pack("<Q", self.bits))[0]


@dataclass(frozen=True)
class NativeObject:
    pointer: int
    type_id: int
    value_handle: int
    native_type_name: str

    def binding(self) -> ObjectBinding:
        if self.type_id < 0:
            raise ValueError("native value has no proven DataFactory binding type; select an explicit type")
        return ObjectBinding(self.type_id, self.value_handle, retained=True)


@dataclass(frozen=True)
class NativeDataCommand:
    """Copied CDataCommand carrier; its pointers borrow the retained Any owner.

    Keep value_handle alive until a generic commands.request(Action.CLONE,
    command=primary_pointer, world=generation_world, capacity=8) completes.
    command_value() copies the source generation world; a raw 24-byte snapshot
    alone does not contain it (generation_world=0 means unavailable, not current).
    Never replace a captured world with a later world after a switch. The native clone is
    separately owned and can use the existing validate/submit/destroy API. Do not
    construct NativeCommand directly from these shared pointers or submit them.
    Releasing this Any uses its shared-control destructor, not unique delete.
    """
    primary_pointer: int
    shared_control: int
    game_command_pointer: int
    value_handle: int
    native_type_name: str
    generation_world: int = 0


@dataclass(frozen=True)
class NativeList:
    """Retained native list value plus its copied native payload descriptor.

    This is not a fabricated Python list. Native collection/model APIs can use
    payload_address/value_handle while ownership remains held. descriptor is a
    stable copy, but any pointers inside it stop being usable after release.
    """
    value_handle: int
    payload_address: int
    descriptor: bytes
    native_type_name: str


def _copied_scalar_value(kind: int, raw_bits: int, snapshot: bytes, total: int):
    kind = Kind(kind)
    if kind == Kind.EMPTY:
        return None
    if kind == Kind.BOOL:
        return bool(raw_bits & 0xff)
    layouts = {Kind.I32: "<i", Kind.U32: "<I", Kind.I64: "<q", Kind.U64: "<Q",
               Kind.FLOAT: "<f", Kind.DOUBLE: "<d"}
    if kind in layouts:
        layout = struct.Struct(layouts[kind])
        return layout.unpack(struct.pack("<Q", raw_bits)[:layout.size])[0]
    if kind == Kind.FIXED:
        return FixedPoint(raw_bits)
    if kind == Kind.STRING:
        if len(snapshot) < total:
            raise ValueError("string snapshot is incomplete; request a larger snapshot")
        return snapshot.decode("utf-8", "replace")
    raise ValueError("not a copied primitive/string")


@dataclass(frozen=True)
class DataResult:
    size: int
    error: int
    action: int
    flags: int
    type_id: int
    start: int
    capacity: int
    count: int
    next: int
    total: int
    execution_thread_id: int
    kind: int
    factory: int
    object: int
    value_handle: int
    any_type: int
    any_vtable: int
    payload_address: int
    payload_size: int
    snapshot_bytes: int
    text_offset: int
    text_bytes: int
    bindings_offset: int
    bindings_count: int
    output_offset: int
    output_bytes: int
    raw_bits: int
    raw_any: bytes
    native_type_name: str
    snapshot: bytes
    rows: tuple[TypeInfo | FunctionInfo, ...] = ()

    @classmethod
    def from_bytes(cls, data: bytes, *, allow_partial: bool = False) -> DataResult:
        fields = cast(_RawRequest, REQUEST.unpack_from(data))
        offset, length = fields[24], fields[19]
        # A partial copy still contains real header fields, including an owned
        # handle. Keep declared snapshot_bytes and only the bytes actually read.
        boundary = fields[0] if allow_partial else len(data)
        if length and (offset < REQUEST.size or offset + length > boundary):
            raise ValueError("native output exceeds result allocation")
        snapshot = data[offset:offset+length] if length else b""
        rows: tuple[TypeInfo | FunctionInfo, ...] = ()
        action = Action(fields[2])
        if action in (Action.TYPES, Action.FUNCTIONS):
            layout, cls_ = (TYPE_INFO, TypeInfo) if action == Action.TYPES else (FUNCTION_INFO, FunctionInfo)
            if len(snapshot) != fields[7] * layout.size:
                raise ValueError("native row count and output length disagree")
            rows = tuple(cls_.from_bytes(snapshot[i:i+layout.size])
                         for i in range(0, len(snapshot), layout.size))
        return cls(*fields[:-1], _text(fields[-1]), snapshot, rows)

    @property
    def value(self):
        """Decode proven scalar/string/object forms; preserve all raw forms."""
        if self.action == Action.COMMAND_VALUE:
            if self.snapshot_bytes != 32 or self.total != 32:
                raise ValueError("command carrier requires its complete 32-byte snapshot")
            primary, control, game, world = struct.unpack("<4Q", self.snapshot)
            return NativeDataCommand(primary, control, game, self.value_handle, "CDataCommand", world)
        kind = Kind(self.kind)
        if Kind.EMPTY <= kind <= Kind.STRING:
            return _copied_scalar_value(kind, self.raw_bits, self.snapshot, self.total)
        if kind == Kind.OBJECT:
            return NativeObject(self.object, self.type_id, self.value_handle, self.native_type_name)
        if kind == Kind.COMMAND:
            if self.payload_size != 24 or self.snapshot_bytes != 24:
                raise ValueError("CDataCommand requires its complete native 24-byte snapshot")
            command_fields = cast(_RawCommand, struct.unpack("<3Q", self.snapshot))
            return NativeDataCommand(*command_fields, self.value_handle, self.native_type_name)
        if kind == Kind.LIST:
            return NativeList(self.value_handle, self.payload_address, self.snapshot, self.native_type_name)
        return self.snapshot


def plain_cstring_literal(value: str) -> str:
    """Encode the proved plain native CString expression-literal subset only.

    The returned text is single-quoted native DSL, not an executed request or a
    genuine typed CString input. NUL and a leading nonempty converter prefix
    cannot use this plain route. Empty parentheses, an unclosed prefix, and
    leading whitespace stay literal data. No whitespace, CR/LF, double quote or
    Unicode normalization is applied. This does not validate native key existence
    or provide a total arbitrary-CString round trip.
    """
    if not isinstance(value, str):
        raise ValueError(f"plain CString literal requires str: {value!r}")
    encoded = value.encode("utf-8", "strict")
    if b"\0" in encoded:
        raise ValueError(f"plain CString literal cannot contain NUL: {value!r}")
    if encoded.startswith(b"(") and encoded.find(b")", 1) > 1:
        raise ValueError(f"plain CString literal cannot start with a nonempty converter prefix: {value!r}")
    literal = b"'" + encoded.replace(b"\\", b"\\\\").replace(b"'", b"\\'") + b"'"
    return literal.decode("utf-8", "strict")


def _creation_expression(expression: str) -> bytes:
    if not isinstance(expression, str):
        raise ValueError("native expression must be a string")
    try:
        text = expression.encode("utf-8", "strict")
    except UnicodeError as error:
        raise ValueError("native expression must contain valid UTF-8") from error
    if not text or b"\0" in text or len(text) > 0x7fffffff:
        raise ValueError("native expression must be nonempty NUL-free signed32 UTF-8")
    return text


@dataclass(frozen=True)
class NativeField:
    """One native copied field on a fresh original Creation row.

    DECREE_TYPE_KEY uses the current DecreeType row BindingScope when
    receiver_expression is empty. Otherwise the original registered expression
    (for example Decree.GetType) supplies its actual receiver in the same graph.
    DECREE_ID requires an empty receiver and copies the current Decree row's
    exact native U32 ID, including zero/UINTMAX. It is not an index or a hash.
    GAME_RULE_KEY is direct-only: receiver_expression must be empty. It requires
    the per-kind RuleFields capability, which certifies only GAME_RULE_KEY.
    This is not a DSL expression or a native formal-argument frame. No receiver
    owner escapes.
    """
    kind: NativeFieldKind
    receiver_expression: str = ""

    def __post_init__(self):
        if not isinstance(self.kind, NativeFieldKind):
            raise ValueError("native field kind must be a NativeFieldKind")
        if not isinstance(self.receiver_expression, str):
            raise ValueError("native field receiver_expression must be a string")
        if self.kind == NativeFieldKind.DECREE_ID and self.receiver_expression:
            raise ValueError("DECREE_ID requires an empty receiver_expression")
        if self.kind == NativeFieldKind.GAME_RULE_KEY and self.receiver_expression:
            raise ValueError("GAME_RULE_KEY requires an empty receiver_expression")
        if self.receiver_expression:
            _creation_expression(self.receiver_expression)


@dataclass(frozen=True)
class NativeSource:
    """One original native source used inside the existing Page callback.

    PRESET_STORED_SETTINGS reads the selected preset's stored setting entries.
    It requires at least one CreationStep and accepts no final source arguments.
    The existing prefix graph owns its lifetime. Copied GameRuleSetting.GetKey
    values are setting keys, not rule definition keys; prefix ordinals are
    observations, not stable identities.
    FULL_COMPANY_TYPES copies the full current native CompanyType source. Its
    first capability requires explicit limit=0/start=0, no prefix/group/final
    arguments, and one root binding row. These are implementation limits, not
    claims that other combinations are inherently unsafe. No pointer, layout
    or type ID is exposed here. Neither source publishes a retained row owner.
    """
    kind: NativeSourceKind

    def __post_init__(self):
        if not isinstance(self.kind, NativeSourceKind):
            raise ValueError("native source kind must be a NativeSourceKind")


@dataclass(frozen=True)
class CreationStep:
    """Select an index from one freshly produced original native Creation.

    The first source uses the supplied root context (global in legacy scoped
    calls). Each following source runs in the preceding original provider Binding.
    Index/count are current observations, not stable identity. An explicit changed
    count returns ERROR_RETRY; nothing is replayed.

    Page-only source_arguments supplies this step's complete original native
    parameter-reference SLOT order, not arbitrary formal argument order. Each
    typed Any is captured from the same root binding row before the first prefix
    borrow. Captures do not rerun lazy getters inside borrowed raw providers or
    retag their pointers. Empty captures keep the original raw-argument rules.
    Legacy evaluate_scoped/scoped_source_count reject nonempty step captures.
    """
    source_expression: str
    index: int
    expected_count: int | None = None
    source_arguments: tuple[str, ...] = field(default=(), kw_only=True)

    def __post_init__(self):
        _creation_expression(self.source_expression)
        if type(self.index) is not int or not 0 <= self.index <= 0x7fffffff:
            raise ValueError("Creation index must be signed32 nonnegative")
        if self.expected_count is not None and (type(self.expected_count) is not int
                or not 0 <= self.expected_count <= 0x7fffffff):
            raise ValueError("expected Creation count must be signed32 nonnegative")
        if isinstance(self.source_arguments, (str, bytes)):
            raise ValueError("source_arguments must contain capture expressions, not one string")
        try:
            arguments = iter(self.source_arguments)
        except TypeError as error:
            raise ValueError("source_arguments must contain capture expressions") from error
        captured: list[str] = []
        for expression in arguments:
            if len(captured) == MAX_PAGE_FIELDS:
                raise ValueError("Creation step has more than 32 captured argument slots")
            _creation_expression(expression)
            captured.append(expression)
        object.__setattr__(self, "source_arguments", tuple(captured))


@dataclass(frozen=True)
class CreationStepReceipt:
    """Copied observations for one original source/provider selection.

    count_valid distinguishes a proven empty source from unavailable production.
    An obtained element remains scoped; type/mode do not expose or retain it.
    """
    source_text_offset: int
    source_text_bytes: int
    index: int
    expected_count: int | None
    source_produced: int
    count_valid: int
    source_count: int
    element_obtained: int
    element_type_id: int
    binding_mode: int

    @classmethod
    def from_bytes(cls, raw: bytes, offset: int) -> CreationStepReceipt:
        fields = list(CREATION_STEP.unpack_from(raw, offset))
        if fields[3] == 0xffffffff:
            fields[3] = None
        return cls(*fields)


@dataclass(frozen=True)
class ProjectionResult:
    """Copied root result/count. No intermediate native owner survives callback.

    source_produced is positive evaluation evidence, not source visibility or
    stable identity. count_valid permits source_count=0 without treating failed
    root lookup as an empty list. Unknown older adapters keep both flags false.
    """
    result: DataResult
    source_world: int
    source_produced: int
    count_valid: int
    source_count: int
    reserved: int

    steps: tuple[CreationStepReceipt, ...] = ()
    completed_steps: int = 0

    @classmethod
    def from_bytes(cls, raw: bytes) -> ProjectionResult:
        if len(raw) < PROJECTION_HEADER_SIZE:
            raise ValueError("truncated copied projection header")
        result = DataResult.from_bytes(raw)
        if any((result.factory, result.object, result.value_handle, result.any_type,
                result.any_vtable, result.payload_address)) or any(result.raw_any):
            raise ValueError("copied projection contains a native value pointer")
        scoped = result.action in (Action.EVALUATE_SCOPED, Action.SCOPED_SOURCE_COUNT)
        count_action = result.action in (Action.SOURCE_COUNT, Action.SCOPED_SOURCE_COUNT)
        if result.action not in (Action.EVALUATE_COPIED, Action.SOURCE_COUNT,
                                 Action.EVALUATE_SCOPED, Action.SCOPED_SOURCE_COUNT):
            raise ValueError("unexpected copied projection action")
        world, produced, count_valid, count, reserved = PROJECTION_SUFFIX.unpack_from(raw, REQUEST.size)
        if produced not in (0, 1) or count_valid not in (0, 1) or reserved or (count_valid and not produced):
            raise ValueError("invalid copied production/count flags")
        if result.kind > Kind.STRING or (not result.error and result.kind == Kind.RAW):
            raise ValueError("projection is not a copied primitive/string")
        if not result.error and (not produced or count_action and not count_valid):
            raise ValueError("successful projection has no production evidence")
        steps: tuple[CreationStepReceipt, ...] = ()
        completed = 0
        if scoped:
            if len(raw) < SCOPED_PROJECTION_HEADER_SIZE or result.size != len(raw):
                raise ValueError("invalid scoped projection allocation")
            offset, depth, completed, nested_reserved = SCOPED_PROJECTION_SUFFIX.unpack_from(raw, PROJECTION_HEADER_SIZE)
            if (offset != SCOPED_PROJECTION_HEADER_SIZE or depth > MAX_SCOPED_DEPTH
                    or completed > depth or nested_reserved
                    or offset + depth * CREATION_STEP.size > len(raw)
                    or not world or result.flags or result.bindings_count):
                raise ValueError("invalid scoped projection step table")
            steps = tuple(CreationStepReceipt.from_bytes(raw, offset + i * CREATION_STEP.size)
                          for i in range(depth))
            cursor = offset + depth * CREATION_STEP.size
            for i, step in enumerate(steps):
                if (step.source_text_offset != cursor or not 0 < step.source_text_bytes <= 0x7fffffff
                        or cursor + step.source_text_bytes > len(raw)
                        or step.index > 0x7fffffff
                        or step.expected_count is not None and step.expected_count > 0x7fffffff):
                    raise ValueError("invalid scoped Creation source span/index/count")
                try:
                    _creation_expression(raw[cursor:cursor + step.source_text_bytes].decode("utf-8", "strict"))
                except UnicodeError as error:
                    raise ValueError("invalid scoped source UTF-8") from error
                cursor += step.source_text_bytes
                if (step.source_produced not in (0, 1) or step.count_valid not in (0, 1)
                        or step.element_obtained not in (0, 1)
                        or step.count_valid and not step.source_produced
                        or step.source_count > 0x7fffffff
                        or not step.count_valid and step.source_count
                        or step.element_obtained and (not step.count_valid or step.index >= step.source_count)
                        or i < completed and (not step.element_obtained
                            or step.element_type_id < 0 or step.binding_mode > 3)
                        or i > completed and (step.source_produced or step.count_valid or step.element_obtained)):
                    raise ValueError("invalid scoped Creation production/count/element receipt")
            if (result.text_offset != cursor or not 0 < result.text_bytes <= 0x7fffffff
                    or cursor + result.text_bytes > len(raw)):
                raise ValueError("invalid scoped final expression span")
            try:
                _creation_expression(raw[cursor:cursor + result.text_bytes].decode("utf-8", "strict"))
            except UnicodeError as error:
                raise ValueError("invalid scoped final UTF-8") from error
            cursor = (cursor + result.text_bytes + 7) & ~7
            if (result.bindings_offset != cursor or result.output_offset != cursor
                    or cursor + result.output_bytes != len(raw)
                    or result.snapshot_bytes > result.output_bytes
                    or count_action and result.output_bytes
                    or not result.error and completed != depth
                    or produced and completed != depth
                    or not count_valid and count):
                raise ValueError("invalid scoped projection output/completion receipt")
        return cls(result, world, produced, count_valid, count, reserved, steps, completed)

    @property
    def error(self) -> int:
        return self.result.error

    @property
    def value(self):
        return self.result.value

    @property
    def value_handle(self) -> int:
        return 0  # This projection never publishes a retained native value.


@dataclass(frozen=True)
class TypedInput:
    """An explicit native Any input. FixedPoint values keep exact integer bits.

    Type is explicit; no guessed coercion between float/fixed/int or type IDs.
    Retained inputs are genuine-copied and hold their adapter-owned dependencies.
    String input is UTF-8 copied into an engine CString, not a dangling Python view.
    """
    kind: Kind | int
    raw_bits: int = 0
    value_handle: int = 0
    text: str = ""

    @classmethod
    def empty(cls) -> TypedInput:
        return cls(Kind.EMPTY)

    @classmethod
    def boolean(cls, value: bool) -> TypedInput:
        return cls(Kind.BOOL, int(value))

    @classmethod
    def i32(cls, value: int) -> TypedInput:
        return cls(Kind.I32, struct.unpack("<I", struct.pack("<i", value))[0])

    @classmethod
    def u32(cls, value: int) -> TypedInput:
        return cls(Kind.U32, struct.unpack("<I", struct.pack("<I", value))[0])

    @classmethod
    def i64(cls, value: int) -> TypedInput:
        return cls(Kind.I64, struct.unpack("<Q", struct.pack("<q", value))[0])

    @classmethod
    def u64(cls, value: int) -> TypedInput:
        return cls(Kind.U64, struct.unpack("<Q", struct.pack("<Q", value))[0])

    @classmethod
    def float32(cls, value: float) -> TypedInput:
        return cls(Kind.FLOAT, struct.unpack("<I", struct.pack("<f", value))[0])

    @classmethod
    def float64(cls, value: float) -> TypedInput:
        return cls(Kind.DOUBLE, struct.unpack("<Q", struct.pack("<d", value))[0])

    @classmethod
    def fixed(cls, value: FixedPoint) -> TypedInput:
        return cls(Kind.FIXED, value.bits)

    @classmethod
    def string(cls, value: str) -> TypedInput:
        return cls(Kind.STRING, text=value)

    @classmethod
    def utf8_string(cls, value: str) -> TypedInput:
        """Explicit native CUTF8String input, distinct from CString string()."""
        return cls(UTF8_INPUT, text=value)

    @classmethod
    def retained(cls, value_handle: int) -> TypedInput:
        return cls(RETAINED_INPUT, value_handle=value_handle)

    def pack(self) -> bytes:
        kind = self.kind if self.kind in (RETAINED_INPUT, UTF8_INPUT) else Kind(self.kind)
        text = self.text.encode("utf-8") if kind in (Kind.STRING, UTF8_INPUT) else b""
        return ANY_INPUT.pack(kind, len(text), self.raw_bits, self.value_handle) + text


@dataclass(frozen=True)
class ActionResult:
    """Native action call status plus post-call Any; not world-effect observation."""
    result: DataResult
    input_offset: int
    input_bytes: int
    compiled_mode: int
    invoked: int
    accepted: int
    input_kind: int

    @classmethod
    def from_bytes(cls, data: bytes, *, allow_partial: bool = False) -> ActionResult:
        if len(data) < ACTION_HEADER_SIZE:
            raise ValueError("truncated native action header")
        return cls(DataResult.from_bytes(data, allow_partial=allow_partial),
                   *ACTION_SUFFIX.unpack_from(data, REQUEST.size))

    @property
    def error(self) -> int:
        return self.result.error

    @property
    def value(self):
        return self.result.value

    @property
    def value_handle(self) -> int:
        return self.result.value_handle


@dataclass(frozen=True)
class PointerArraySource:
    """One original native pointer-element array used only within a callback.

    root_expression is native VM syntax, freshly evaluated. byte_offset locates
    ONE fixed native Array24 header in that real root; it is not an offset chain
    or an invented Creation provider. type_id is the proven original element's
    runtime DataFactory type, not a tag to put on an unrelated object.

    The source owner may use direct unique deletion. No CPdxPtr lease is claimed.
    Use only the original after-frame owner-thread callback and native exclusive
    gate, with no synchronous source refresh/delete reentry through all cleanup.
    interface_thread_id comes from this session's original Interface status.
    element_subobject_offset optionally selects an ORIGINAL embedded subobject
    ADDRESS in each complete array element, after matching expected_element_vtable.
    It is not a pointer-member dereference, retag or lifetime extension. Caller
    must prove exact current-image layout/type and ownership; old DLLs reject the
    additive flag. Count-only uses the unchanged original array ABI.
    Optional guard_expression evaluates on this exact fresh source context before
    its list getter; its copied native kind/bytes must equal guard_input. A false
    match returns ERROR_RETRY without producing options or invoking an action.
    This is separate from the original element BOOL enabled condition. All guard
    compilation precedes source borrowing; raw element parameter rules do not change.
    """
    root_expression: str
    byte_offset: int
    type_id: int
    interface_thread_id: int
    index: int = 0
    expected_count: int | None = None
    guard_expression: str = ""
    guard_input: TypedInput | None = None
    element_subobject_offset: int = 0
    expected_element_vtable: int = 0

    def __post_init__(self):
        if not self.root_expression or "\0" in self.root_expression:
            raise ValueError("root_expression must be original NUL-free VM syntax")
        if not 0 <= self.byte_offset <= 0xffffffff or self.byte_offset % 8:
            raise ValueError("byte_offset must be an aligned uint32 native header offset")
        if not 0 <= self.type_id <= 0x7fffffff or not 0 <= self.index <= 0x7fffffff:
            raise ValueError("native source type/index must fit signed32")
        if not 0 < self.interface_thread_id <= 0xffffffff:
            raise ValueError("the original live Interface thread ID is required")
        if self.expected_count is not None and not 0 <= self.expected_count <= 0x7fffffff:
            raise ValueError("expected source count must fit signed32")
        if bool(self.guard_expression) != (self.guard_input is not None) or "\0" in self.guard_expression:
            raise ValueError("source guard requires original expression and explicit copied input")
        if (type(self.element_subobject_offset) is not int
                or not 0 <= self.element_subobject_offset <= 0xffffffff
                or self.element_subobject_offset % 8
                or type(self.expected_element_vtable) is not int
                or not 0 <= self.expected_element_vtable <= 0xffffffffffffffff
                or bool(self.element_subobject_offset) != bool(self.expected_element_vtable)):
            raise ValueError("embedded subobject requires an aligned uint32 offset and original element vtable")
        if self.guard_input is not None:
            if not isinstance(self.guard_input, TypedInput) or not Kind.EMPTY <= self.guard_input.kind <= Kind.STRING:
                raise ValueError("source guard input must be a copied native primitive/string/empty")


@dataclass(frozen=True)
class SourceGuardResult:
    text_offset: int
    text_bytes: int
    input_offset: int
    input_bytes: int
    checked: int
    passed: int


@dataclass(frozen=True)
class PointerArrayResult:
    root_text_offset: int
    root_text_bytes: int
    byte_offset: int
    source_type_id: int
    index: int
    expected_count: int
    interface_thread_id: int
    flags: int
    count: int
    stages: int


@dataclass(frozen=True)
class SubobjectSourceResult:
    byte_offset: int
    reserved: int
    expected_element_vtable: int


@dataclass(frozen=True)
class ElementResult:
    """Copied result of one fresh native list production/element consumption.

    There is no retained element/result handle. source_count/index are collection
    observations, NOT a stable domain identity. invoked/accepted are receipts,
    including when a later result copy fails; never infer no action or replay.
    """
    action: ActionResult
    source_world: int
    source_text_offset: int
    source_text_bytes: int
    condition_offset: int
    condition_bytes: int
    element_type_id: int
    condition_checked: int
    condition_passed: int
    source_count: int
    array_source: PointerArrayResult | None = None
    source_guard: SourceGuardResult | None = None
    subobject_source: SubobjectSourceResult | None = None

    @classmethod
    def from_bytes(cls, value: bytes) -> ElementResult:
        if len(value) < ELEMENT_HEADER_SIZE:
            raise ValueError("truncated native element header")
        action = ActionResult.from_bytes(value)
        if any((action.result.factory, action.result.value_handle, action.result.object,
                action.result.any_type, action.result.any_vtable,
                action.result.payload_address)) or any(action.result.raw_any):
            raise ValueError("copied-only element result contains a native value pointer")
        if action.result.kind > Kind.STRING or (not action.error and action.result.kind == Kind.RAW):
            raise ValueError("element result is not a copied native primitive/string")
        source = None
        guard = None
        subobject = None
        if action.result.action in (Action.EVALUATE_ARRAY_ELEMENT, Action.INVOKE_ARRAY_ELEMENT_ACTION):
            if len(value) < ARRAY_ELEMENT_HEADER_SIZE:
                raise ValueError("truncated pointer-array source header")
            source = PointerArrayResult(*POINTER_ARRAY_SUFFIX.unpack_from(value, ELEMENT_HEADER_SIZE))
            suffix_offset = ARRAY_ELEMENT_HEADER_SIZE
            if source.flags & ARRAY_ELEMENT_SUBOBJECT:
                if len(value) < suffix_offset + SUBOBJECT_SOURCE_SUFFIX.size:
                    raise ValueError("truncated embedded-subobject source header")
                subobject = SubobjectSourceResult(*SUBOBJECT_SOURCE_SUFFIX.unpack_from(value, suffix_offset))
                if subobject.reserved or not subobject.byte_offset or subobject.byte_offset % 8 or not subobject.expected_element_vtable:
                    raise ValueError("invalid embedded-subobject source profile")
                suffix_offset += SUBOBJECT_SOURCE_SUFFIX.size
            if source.flags & ARRAY_SOURCE_GUARD:
                if len(value) < suffix_offset + SOURCE_GUARD_SUFFIX.size:
                    raise ValueError("truncated copied-value source guard header")
                guard = SourceGuardResult(*SOURCE_GUARD_SUFFIX.unpack_from(value, suffix_offset))
        element_fields = cast(_RawElementSuffix, ELEMENT_SUFFIX.unpack_from(value, ACTION_HEADER_SIZE))
        return cls(action, *element_fields, source, guard, subobject)

    @property
    def root_count(self) -> int | None:
        return self.array_source.count if self.array_source is not None else None

    @property
    def source_stages(self) -> int:
        return self.array_source.stages if self.array_source is not None else 0

    @property
    def guard_checked(self) -> int:
        return self.source_guard.checked if self.source_guard is not None else 0

    @property
    def guard_passed(self) -> int:
        return self.source_guard.passed if self.source_guard is not None else 0

    @property
    def error(self) -> int:
        return self.action.error

    @property
    def value(self):
        return self.action.value

    @property
    def invoked(self) -> int:
        return self.action.invoked

    @property
    def accepted(self) -> int:
        return self.action.accepted

    @property
    def result(self) -> DataResult:
        return self.action.result


class DataQuery(CallbackResult):
    """Result storage and retained engine value have distinct lifetimes."""
    def __init__(self, client, process, callback, address, owns_process, size, adapter, decode=DataResult.from_bytes):
        super().__init__(client, process, callback, address, owns_process, size, decode)
        owner = getattr(process, "export_owner", None)
        base = owner(callback) if callable(owner) else None
        self.adapter = base if isinstance(base, int) else adapter

    def release_value(self, *, process: RemoteProcess | None = None) -> DataQuery:
        """Queue one explicit release of a completed retained value; never replay."""
        result = self.read()
        if result is None:
            raise RuntimeError("query is pending; cannot release its native value")
        if not result.value_handle:
            raise RuntimeError("result has no retained native value")
        return release(cast(NativeBridge, self.client), result.value_handle, adapter=self.adapter, process=process)



# Optional matrix-page ABI. The returned allocation contains copied bytes only.
PAGE_REQUEST = struct.Struct("<IIQ12IQQII")
PAGE_SPAN = struct.Struct("<II")
PAGE_FIELD = struct.Struct("<4I")
PAGE_SOURCE = struct.Struct("<4I")
PAGE_CELL = struct.Struct("<4IiI4QIIQQ")
MAX_PAGE_ROWS = 64
MAX_PAGE_FIELDS = 32
PAGE_SCOPED = 1
PAGE_CAPTURE_ARGUMENTS = 2
PAGE_CAPTURE_STEP_ARGUMENTS = 8
PAGE_NATIVE_FIELDS = 16
PAGE_ORIGINAL_SOURCES = 32
PAGE_SCOPED_SUFFIX = struct.Struct("<6I")
PAGE_GROUPED = 4
MAX_PAGE_SOURCES = 32
PAGE_GROUP_INPUT = struct.Struct("<4I")
PAGE_GROUP_OUTPUT = struct.Struct("<6I")
SOURCE_OBSERVATION = struct.Struct("<12I")


@dataclass(frozen=True)
class SourceObservation:
    source_index: int
    error: int
    source_produced: bool
    count_valid: bool
    source_count: int
    start: int
    row_count: int
    next: int
    row_offset: int
    finished: bool
    range_complete: bool



@dataclass(frozen=True)
class ProjectionCell:
    row: int
    field: int
    error: int
    kind: int
    type_id: int
    raw_bits: int
    snapshot: bytes
    total: int
    binding_mode: int = 0xffffffff  # original provider observation, not a lease
    binding_kind: int = 0xffffffff
    binding_native_type_name: str | None = None
    binding_error: int = 0

    @property
    def complete(self) -> bool:
        return len(self.snapshot) == self.total

    @property
    def value(self):
        return _copied_scalar_value(self.kind, self.raw_bits, self.snapshot, self.total)


@dataclass(frozen=True)
class ProjectionPage:
    error: int
    world: int
    fields_count: int
    start: int
    execution_thread_id: int
    source_count: int
    count_valid: bool
    row_count: int
    cells: tuple[ProjectionCell, ...]
    steps: tuple[CreationStepReceipt, ...] = ()
    completed_steps: int = 0
    sources: tuple[SourceObservation, ...] = ()
    next_cursor: tuple[int, int] | None = None
    range_complete: bool = False
    grouped: bool = False
    _group_has_more: bool | None = None

    @property
    def value_handle(self) -> int:
        return 0  # Only the query's copied-byte allocation has external ownership.

    @property
    def next(self) -> int:
        return self.start + self.row_count

    @property
    def has_more(self) -> bool | None:
        if self.grouped:
            return self._group_has_more
        return self.count_valid and self.next < self.source_count

    @property
    def rows(self) -> tuple[tuple[ProjectionCell, ...], ...]:
        return tuple(self.cells[i:i + self.fields_count]
                     for i in range(0, len(self.cells), self.fields_count))

    @classmethod
    def from_bytes(cls, header: bytes, copied: bytes) -> ProjectionPage:
        h = PAGE_REQUEST.unpack_from(header)
        count = h[17]
        grouped = bool(h[18] & PAGE_GROUPED)
        sources: tuple[SourceObservation, ...]
        sources, next_cursor, range_complete, more = (), None, False, None
        table_offset = 0
        if grouped:
            table_offset = len(copied)  # No cells until their prefix is available.
            if len(copied) >= PAGE_GROUP_OUTPUT.size:
                prefix, next_source, next_row, observed, more, complete = PAGE_GROUP_OUTPUT.unpack_from(copied)
                if prefix < PAGE_GROUP_OUTPUT.size + observed * SOURCE_OBSERVATION.size or prefix > h[16]:
                    raise ValueError("invalid copied source observation span")
                next_cursor, range_complete, more = (next_source, next_row), bool(complete), bool(more)
                values = []
                for i in range(min(observed, (len(copied) - PAGE_GROUP_OUTPUT.size) // SOURCE_OBSERVATION.size)):
                    v = cast(_RawSourceObservation, SOURCE_OBSERVATION.unpack_from(
                        copied, PAGE_GROUP_OUTPUT.size + i * SOURCE_OBSERVATION.size))
                    values.append(SourceObservation(v[0], v[1], bool(v[2]), bool(v[3]), *v[4:9], bool(v[9]), bool(v[10])))
                sources = tuple(values)
                table_offset = prefix
        table_bytes = table_offset + count * PAGE_CELL.size
        cells = []
        for i in range(min(count, max(0, len(copied) - table_offset) // PAGE_CELL.size)):
            (row, field, error, kind, type_id, mode, bits, offset, length, total,
             binding_kind, binding_error, type_offset, type_bytes) = PAGE_CELL.unpack_from(copied, table_offset + i * PAGE_CELL.size)
            if (offset < table_bytes or offset > h[16] or length > h[16] - offset
                    or length > total or not Kind.EMPTY <= kind <= Kind.STRING):
                raise ValueError("invalid copied projection cell span/kind")
            if type_bytes and (type_offset < table_bytes or type_offset > h[16]
                               or type_bytes > h[16] - type_offset):
                raise ValueError("invalid copied provider type span")
            # A prefix is not a complete native type name. The query still
            # owns all copied bytes, but incomplete metadata remains unknown.
            type_name = (copied[type_offset:type_offset + type_bytes].decode("utf-8", "replace")
                         if type_bytes and type_offset + type_bytes <= len(copied) else None)
            cells.append(ProjectionCell(row, field, error, kind, type_id, bits,
                                        copied[offset:offset + length], total, mode,
                                        binding_kind, type_name, binding_error))
        steps: tuple[CreationStepReceipt, ...]
        steps, completed = (), 0
        if h[18] & PAGE_SCOPED:
            offset, depth, completed, _, _, _ = PAGE_SCOPED_SUFFIX.unpack_from(header, PAGE_REQUEST.size)
            steps = tuple(CreationStepReceipt.from_bytes(header, offset + i * CREATION_STEP.size)
                          for i in range(depth))
        return cls(h[1], h[2], h[4], h[9], h[11], h[12], bool(h[13]), h[14], tuple(cells), steps, completed,
                   sources, next_cursor, range_complete, grouped, more)


class ProjectionPageQuery(DataQuery):
    """Existing callback owner plus one VirtualAlloc copy, never an engine lease.

    A failed read retains the byte prefix and allocation. read() continues that
    copy without invoking a getter. partial_result exposes cells already copied.
    Cleanup errors are separate from the primary result; read()/close() retry
    cleanup only. A pending callback retains all original storage.
    """
    def __init__(self, client, process, callback, address, owns_process, size, adapter):
        self.copied_address = 0
        self._copied_size = 0
        self._copied = bytearray()
        self._copied_freed = False
        self.partial_result: ProjectionPage | None = None
        self.cleanup_error: Exception | None = None
        super().__init__(client, process, callback, address, owns_process, size, adapter, self._decode_page)

    def _decode_page(self, header: bytes) -> ProjectionPage:
        h = PAGE_REQUEST.unpack_from(header)
        self.copied_address, self._copied_size = h[15:17]
        try:
            while len(self._copied) < self._copied_size:
                offset = len(self._copied)
                block = self.process.read(self.copied_address + offset,
                                          min(65536, self._copied_size - offset))
                if not block:
                    raise ValueError("empty read before copied projection end")
                self._copied.extend(block)
        except Exception as error:
            self.partial_result = ProjectionPage.from_bytes(header, bytes(self._copied))
            context = cast(_PageDecodeContext, error)
            context.query = self
            raise
        result = ProjectionPage.from_bytes(header, bytes(self._copied))
        self.partial_result = result
        self._copied.clear()
        return result

    def _release(self) -> None:
        try:
            if self.copied_address and not self._copied_freed:
                self.process.free(self.copied_address)
                self._copied_freed = True
            super()._release()
        except Exception as error:
            self.cleanup_error = error
            if self._result is None:
                raise  # shared submission attaches the still-owned query
            return
        self.cleanup_error = None


def _page_request(fields: Iterable[str | NativeField], binding_rows: Iterable[Iterable[ObjectBinding]], *,
                  source_expression: str | NativeSource | None, start: int, limit: int,
                  steps: Iterable[CreationStep] = (), source_arguments: Iterable[str] = (),
                  sources: Iterable[str | NativeSource] | None = None, source_index: int = 0) -> bytes:
    grouped = sources is not None
    source_texts: list[bytes] = []
    source_kinds: list[int] = []
    if grouped:
        if source_expression is not None:
            raise ValueError("sources and source_expression are mutually exclusive")
        for source_item in cast(Iterable[str | NativeSource], sources):
            if len(source_texts) == MAX_PAGE_SOURCES:
                raise ValueError("page has more than 32 sources")
            if isinstance(source_item, NativeSource):
                source_kinds.append(source_item.kind)
                source_texts.append(b"")
            else:
                source_kinds.append(0)
                source_texts.append(_creation_expression(source_item))
        if not source_texts or type(source_index) is not int or not 0 <= source_index <= len(source_texts):
            raise ValueError("group needs sources and a source_index within its cursor range")
    native_sources = isinstance(source_expression, NativeSource) or any(source_kinds)
    company_source = (isinstance(source_expression, NativeSource)
                      and source_expression.kind == NativeSourceKind.FULL_COMPANY_TYPES)
    if NativeSourceKind.FULL_COMPANY_TYPES in source_kinds:
        raise ValueError("FULL_COMPANY_TYPES grouped sources are not implemented in this first capability")
    argument_texts: list[bytes] = []
    for argument_expression in source_arguments:
        if len(argument_texts) == MAX_PAGE_FIELDS:
            raise ValueError("page has more than 32 captured source argument slots")
        argument_texts.append(_creation_expression(argument_expression))
    selected_steps: list[CreationStep] = []
    for step in steps:
        if len(selected_steps) == MAX_SCOPED_DEPTH or not isinstance(step, CreationStep):
            raise ValueError("page steps must contain at most eight CreationStep values")
        selected_steps.append(step)
    step_argument_texts = [[_creation_expression(expression) for expression in step.source_arguments]
                           for step in selected_steps]
    capture_steps = any(step_argument_texts)
    if grouped and argument_texts:
        raise ValueError("grouped pages do not combine source_arguments")
    if company_source:
        if selected_steps or argument_texts:
            raise ValueError("FULL_COMPANY_TYPES prefixes/final source_arguments are not implemented in this first capability")
        if type(start) is not int or start != 0 or type(limit) is not int or limit != 0:
            raise ValueError("FULL_COMPANY_TYPES currently requires start=0 and explicit limit=0 for its full snapshot")
    else:
        if native_sources and not selected_steps:
            raise ValueError("native sources require at least one fresh CreationStep")
        if native_sources and argument_texts:
            raise ValueError("native sources do not accept final source_arguments")
        if (selected_steps or argument_texts) and source_expression is None and not grouped:
            raise ValueError("page steps/arguments require a final Creation source_expression")
        if type(start) is not int or not 0 <= start <= 0x7fffffff:
            raise ValueError("start must fit nonnegative signed32")
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_ROWS:
            raise ValueError("limit must be 1..64")
    texts: list[bytes] = []
    field_kinds: list[int] = []
    for expression in fields:
        if len(texts) == MAX_PAGE_FIELDS:
            raise ValueError("page has more than 32 fields")
        if isinstance(expression, NativeField):
            field_kinds.append(expression.kind)
            texts.append(_creation_expression(expression.receiver_expression)
                         if expression.receiver_expression else b"")
        else:
            field_kinds.append(0)
            texts.append(_creation_expression(expression))
    native_fields = any(field_kinds)
    if native_fields and source_expression is None and not grouped:
        raise ValueError("native fields require a fresh Creation source_expression or sources")
    rows: list[bytes] = []
    for bindings in binding_rows:
        if len(rows) == MAX_PAGE_ROWS:
            raise ValueError("page has more than 64 binding rows")
        rows.append(b"".join(binding.pack() for binding in bindings))
    if not texts or not rows:
        raise ValueError("page needs fields and at least one binding row")
    if isinstance(source_expression, NativeSource):
        source = PAGE_SOURCE.pack(source_expression.kind, 0, 0, 0)
    else:
        source = b"" if source_expression is None else _creation_expression(source_expression)
    if ((source or grouped) and len(rows) != 1) or (not source and not grouped and (start or len(rows) > limit)):
        raise ValueError("Creation source needs one binding row; ID rows need start=0 and fit limit")
    flags = (PAGE_SCOPED if selected_steps else 0) | (PAGE_CAPTURE_ARGUMENTS if argument_texts else 0)
    if capture_steps:
        flags |= PAGE_CAPTURE_STEP_ARGUMENTS
    if grouped:
        flags |= PAGE_GROUPED
    if native_fields:
        flags |= PAGE_NATIVE_FIELDS
    if native_sources:
        flags |= PAGE_ORIGINAL_SOURCES
    fields_offset = PAGE_REQUEST.size + (PAGE_SCOPED_SUFFIX.size if flags else 0) + (PAGE_GROUP_INPUT.size if grouped else 0)
    rows_offset = fields_offset + len(texts) * (PAGE_FIELD.size if native_fields else PAGE_SPAN.size)
    steps_offset = rows_offset + len(rows) * PAGE_SPAN.size
    arguments_offset = steps_offset + len(selected_steps) * CREATION_STEP.size
    sources_offset = arguments_offset + len(argument_texts) * PAGE_SPAN.size
    tables_end = sources_offset + len(source_texts) * (PAGE_SOURCE.size if native_sources else PAGE_SPAN.size)
    step_arguments_offset = tables_end if capture_steps else 0
    step_argument_offsets = []
    if capture_steps:
        tables_end += len(selected_steps) * PAGE_SPAN.size
        for arguments in step_argument_texts:
            step_argument_offsets.append(tables_end if arguments else 0)
            tables_end += len(arguments) * PAGE_SPAN.size
    payload = bytearray(tables_end)
    for i, text in enumerate(texts):
        if native_fields:
            PAGE_FIELD.pack_into(payload, fields_offset + i * PAGE_FIELD.size,
                                 field_kinds[i], len(payload) if text else 0, len(text), 0)
        else:
            PAGE_SPAN.pack_into(payload, fields_offset + i * PAGE_SPAN.size, len(payload), len(text))
        payload.extend(text)
    source_offset = len(payload)
    payload.extend(source)
    for i, step in enumerate(selected_steps):
        text = _creation_expression(step.source_expression)
        CREATION_STEP.pack_into(payload, steps_offset + i * CREATION_STEP.size,
            len(payload), len(text), step.index,
            0xffffffff if step.expected_count is None else step.expected_count,
            0, 0, 0, 0, -1, 0xffffffff)
        payload.extend(text)
    for i, text in enumerate(argument_texts):
        PAGE_SPAN.pack_into(payload, arguments_offset + i * PAGE_SPAN.size, len(payload), len(text))
        payload.extend(text)
    for i, text in enumerate(source_texts):
        if native_sources:
            PAGE_SOURCE.pack_into(payload, sources_offset + i * PAGE_SOURCE.size,
                                  source_kinds[i], len(payload) if text else 0, len(text), 0)
        else:
            PAGE_SPAN.pack_into(payload, sources_offset + i * PAGE_SPAN.size, len(payload), len(text))
        payload.extend(text)
    if capture_steps:
        for i, arguments in enumerate(step_argument_texts):
            PAGE_SPAN.pack_into(payload, step_arguments_offset + i * PAGE_SPAN.size,
                                step_argument_offsets[i], len(arguments))
            for slot, text in enumerate(arguments):
                PAGE_SPAN.pack_into(payload, step_argument_offsets[i] + slot * PAGE_SPAN.size,
                                    len(payload), len(text))
                payload.extend(text)
    payload.extend(bytes(-len(payload) % 8))
    for i, row_bytes in enumerate(rows):
        PAGE_SPAN.pack_into(payload, rows_offset + i * PAGE_SPAN.size, len(payload), len(row_bytes) // BINDING_INPUT.size)
        payload.extend(row_bytes)
    PAGE_REQUEST.pack_into(payload, 0, len(payload), ERROR_IO_PENDING, 0,
        fields_offset, len(texts), rows_offset, len(rows), source_offset, len(source), start, limit,
        0, 0, 0, 0, 0, 0, 0, flags)
    if flags:
        PAGE_SCOPED_SUFFIX.pack_into(payload, PAGE_REQUEST.size, steps_offset, len(selected_steps), 0,
                                    arguments_offset, len(argument_texts), step_arguments_offset)
    if grouped:
        PAGE_GROUP_INPUT.pack_into(payload, PAGE_REQUEST.size + PAGE_SCOPED_SUFFIX.size,
                                   sources_offset, len(source_texts), source_index, 0)
    return bytes(payload)


def project_page(client: NativeBridge, fields: Iterable[str | NativeField],
                 binding_rows: Iterable[Iterable[ObjectBinding]] = ((),), *,
                 source_expression: str | NativeSource | None = None, start: int = 0, limit: int = 64,
                 steps: Iterable[CreationStep] = (), source_arguments: Iterable[str] = (),
                 sources: Iterable[str | NativeSource] | None = None, source_index: int = 0,
                 adapter: str | int = "pdx_v3_data.dll",
                 process: RemoteProcess | None = None) -> ProjectionPageQuery:
    """Copy up to64 rows ×32 fields in ONE native Interface/Access callback.

    ID mode accepts one full-ID/retained binding set per row. Source mode uses
    one root binding set and evaluates the original Creation once per page.
    Optional steps select original enclosing Creation rows before the final
    paged source. All prefix bindings/sources remain alive through leaf copies;
    every expression compiles before any row is borrowed. No ordinal is an ID.
    source_arguments explicitly supplies capture expressions in native parameter-
    reference SLOT order for the final source (not arbitrary formal argument
    order). Every slot must be supplied. Captures evaluate in the same owned root
    bindings before any prefix source; original typed Any values and root owners
    survive all nested frames. Native direct refs consume these values without
    rerunning argument getters. No pointer retagging or copied-scalar requirement.
    For Battle.GetBattleUnitTypeList(MilitaryFormation.Self), supply the exact
    original expression source_arguments=("MilitaryFormation.Self",) at slot0.
    This explicit substitution does not assert that a different capture expression
    is equivalent to the original argument. Each CreationStep.source_arguments
    separately supplies that prefix source's complete native SLOT order. All
    prefix and final captures evaluate in the same typed root binding row before
    the first prefix borrow. The 32-slot budget applies to each source, not the
    combined chain. Captures use original typed Anys and direct references, not
    Python-exported object handles, lazy ancestor getters or raw-pointer retags.
    Ordinary raw-argument restrictions remain when a source has no captures.
    The callback observes its current world inside Access; no expected-world
    read/proof is required from Python. A world0 application can produce fresh
    global sources within that same owner scope. This does not permit external
    raw pointers or legacy world0 retained bindings. Full-ID roots still need
    their native world. Retained inputs keep their real lifetime.
    Each provider Binding survives all its fields without retagging raw modes.
    Fields must not refresh/delete the original source while a row is borrowed.
    Registered field expressions compile once/page; all cells keep errors/zero/False.
    NativeField(NativeFieldKind.DECREE_TYPE_KEY) copies the current DecreeType
    row's key. receiver_expression="Decree.GetType" selects that actual getter's
    receiver instead. NativeField(NativeFieldKind.DECREE_ID) copies the current
    Decree row's exact native U32 ID and requires an empty receiver_expression.
    Pair it with the key field using "Decree.GetType" to copy ID and type key
    from the same actual Decree row, without masking or deriving an ID from an
    index. Native fields require a fresh original Creation source
    (source_expression or sources), not a plain retained/ID matrix. Receivers
    compile before borrowing and must have no native parameter-reference slots;
    native field formal arguments are not supported. No native receiver escapes.
    Native field cells use the unchanged copied result with type_id=-1.
    Strings copy in full into an owned byte allocation before native cleanup;
    reading large bytes never repeats a getter. Pages are not an atomic snapshot
    across calls. For ordinary/preset pages, continue at result.next while
    result.has_more. The standalone full Company source below is not paged.

    sources selects bounded ordered-source mode with one root binding row.
    Continue with source_index/start from next_cursor. Empty/failed sources do
    not spend the attempted-row budget; errors remain in sources observations.
    range_complete concerns this fragment's enumeration, not successful fields
    or stable collections across pages. A terminal cursor does not erase errors.
    Group capability is PdxV3DataProjectSourcesInterface, an alias to this same
    implementation. An older Page DLL may lack it; callers can choose single-
    source Page before submitting, never replay an ambiguous pending request.

    With registered source expressions and no native fields, prefix captures
    select PdxV3DataProjectTypedStepsInterface, including grouped pages. That capability
    uses the same native Page implementation and copied result owner. Missing
    capability fails before submission; no old-symbol fallback or replay is
    attempted. Without native fields or prefix captures, the original Page or
    Sources capability and wire bytes remain unchanged.

    GAME_RULE_KEY requires an empty receiver. It selects
    PdxV3DataProjectRuleFieldsInterface above all other Page aliases, including
    NativeSource pages. That marker certifies only GAME_RULE_KEY and uses the
    same Page implementation and copied-byte owner. Older aliases do not certify
    this kind. Missing capability fails before submission, without fallback/replay.
    The capability descriptions below apply to requests without GAME_RULE_KEY.

    Without NativeSource, any NativeField selects
    PdxV3DataProjectNativeFieldsInterface, including grouped pages and typed
    prefix captures. This alias uses the same Page implementation and copied-byte
    owner, without fallback or replay. Requests without NativeSource preserve
    the previous field/source wire and capability choice exactly.

    NativeSource(NativeSourceKind.PRESET_STORED_SETTINGS) selects the actual
    stored setting entries of the last fresh preset prefix Binding. Use
    steps=(CreationStep("GetGameRules.GetRulePresets", preset_index),) and this
    NativeSource as source_expression. Read GameRuleSetting.GetKey/GetName or
    GetDescription; GetKey is a SETTING key, not a rule definition key. The
    parent GameRuleSetting.GetGameRule.GetName is a name, not a canonical rule
    key. This route does not claim defaults or locks. Prefix indices remain
    current observations, not identities.
    PRESET_STORED_SETTINGS requires nonempty fresh Creation steps, accepts no
    final source_arguments, and exposes no pointer/layout/type ID. Prefix
    captures remain supported. Its leaf fields must have zero compiled native
    parameter-reference slots. Compilation precedes borrowing the original
    graph. Without FULL_COMPANY_TYPES, NativeSource single/group pages select
    PdxV3DataProjectOriginalSourcesInterface above the older Page aliases.

    NativeSource(NativeSourceKind.FULL_COMPANY_TYPES) requires explicit limit=0
    and start=0 to request all current native CompanyType positions in one
    callback, with fields such as "CompanyType.GetKey". Default/explicit64 is
    rejected, never silently changed into an unlimited request. The first
    capability supports only a standalone source with no steps, grouped sources
    or final source_arguments, plus one empty or actual typed root binding row.
    These are implementation limits, not inherent safety claims. It requires
    PdxV3DataProjectCompanyTypesInterface, including with NativeField columns;
    no OriginalSources fallback, getter replay or empty-catalog substitution.
    Required source_count is distinct from represented row_count, each cell's
    required total and its actually copied snapshot bytes. Null/error positions
    and successful prefixes remain visible. Counts/has_more alone do not prove
    complete keys; preserve page/cell errors and incomplete bytes. An empty
    catalog needs an observed valid count, not a missing provider. Short reads
    resume the same copied allocation, never native getters. These Company keys
    are not retained CompanyType bindings for a later CreateCompany call.
    Both source kinds retain the same copied decoder/query owner.
    """
    initial = _page_request(fields, binding_rows, source_expression=source_expression,
                            start=start, limit=limit, steps=steps, source_arguments=source_arguments,
                            sources=sources, source_index=source_index)
    # Inspect owned input bytes, not the caller's possibly one-shot fields.
    input_header = PAGE_REQUEST.unpack_from(initial)
    rule_fields = bool(input_header[18] & PAGE_NATIVE_FIELDS) and any(
        PAGE_FIELD.unpack_from(initial, input_header[3] + index * PAGE_FIELD.size)[0]
        == NativeFieldKind.GAME_RULE_KEY
        for index in range(input_header[4]))
    return cast(ProjectionPageQuery, submit_callback(client, initial, adapter=adapter,
        callback_symbol=("PdxV3DataProjectRuleFieldsInterface" if rule_fields
                         else "PdxV3DataProjectCompanyTypesInterface"
                         if isinstance(source_expression, NativeSource)
                         and source_expression.kind == NativeSourceKind.FULL_COMPANY_TYPES
                         else "PdxV3DataProjectOriginalSourcesInterface"
                         if PAGE_REQUEST.unpack_from(initial)[18] & PAGE_ORIGINAL_SOURCES
                         else "PdxV3DataProjectNativeFieldsInterface"
                         if PAGE_REQUEST.unpack_from(initial)[18] & PAGE_NATIVE_FIELDS
                         else "PdxV3DataProjectTypedStepsInterface"
                         if PAGE_REQUEST.unpack_from(initial)[18] & PAGE_CAPTURE_STEP_ARGUMENTS
                         else "PdxV3DataProjectSourcesInterface" if sources is not None
                         else "PdxV3DataProjectPageInterface"),
        decode_result=lambda raw: raw,
        process=process, handle_factory=lambda c, p, cb, address, owns:
            ProjectionPageQuery(c, p, cb, address, owns, len(initial), adapter)))


def _request(action: Action, *, type_id: int = -1, start: int = 0,
             capacity: int = 0, expression: bytes = b"", bindings: bytes = b"",
             value_handle: int = 0, output_bytes: int = 0, flags: int = 0) -> bytes:
    if start < 0 or capacity < 0 or output_bytes < 0:
        raise ValueError("start, capacity and output_bytes must be nonnegative")
    text_offset = REQUEST.size
    bindings_offset = (text_offset + len(expression) + 7) & ~7
    output_offset = bindings_offset + len(bindings)
    size = output_offset + output_bytes
    initial = REQUEST.pack(size, ERROR_IO_PENDING, action, flags, type_id,
        start, capacity, 0, 0, 0, 0, 0,
        0, 0, value_handle, 0, 0, 0,
        0, 0, text_offset, len(expression), bindings_offset, len(bindings)//BINDING_INPUT.size,
        output_offset, output_bytes, 0, bytes(40), bytes(192))
    return initial + expression + bytes(bindings_offset - text_offset - len(expression)) + bindings + bytes(output_bytes)


def _submit(client: NativeBridge, initial: bytes, *, adapter: str | int,
            process: RemoteProcess | None, decode=DataResult.from_bytes,
            callback_symbol: str = "PdxV3DataInterface") -> DataQuery:
    return cast(DataQuery, submit_callback(client, initial, adapter=adapter,
        callback_symbol=callback_symbol, decode_result=decode,
        process=process, handle_factory=lambda c, p, cb, address, owns:
            DataQuery(c, p, cb, address, owns, len(initial), adapter, decode)))


def enumerate_types(client: NativeBridge, *, start: int = 0, capacity: int = 32,
                    adapter: str | int = "pdx_v3_data.dll",
                    process: RemoteProcess | None = None) -> DataQuery:
    """One page from the live engine factory, not an offline generated catalog."""
    return _submit(client, _request(Action.TYPES, start=start, capacity=capacity,
        output_bytes=capacity*TYPE_INFO.size), adapter=adapter, process=process)


def enumerate_functions(client: NativeBridge, type_id: int, *, start: int = 0,
                        capacity: int = 32, adapter: str | int = "pdx_v3_data.dll",
                        process: RemoteProcess | None = None) -> DataQuery:
    """One live type's function table including native argument/return metadata.

    Returned callback addresses are evidence/raw composition points, not promised
    direct getter ABIs. evaluate() runs the real engine compile/context/callstack.
    """
    return _submit(client, _request(Action.FUNCTIONS, type_id=type_id, start=start,
        capacity=capacity, output_bytes=capacity*FUNCTION_INFO.size), adapter=adapter, process=process)


def evaluate(client: NativeBridge, expression: str, bindings: Iterable[ObjectBinding] = (), *,
             output_bytes: int = 4096, adapter: str | int = "pdx_v3_data.dll",
             process: RemoteProcess | None = None) -> DataQuery:
    """Compile and evaluate game-native data syntax with explicit object scopes.

    Use the dynamic type ID discovered in enumerate_types(). Example:
    evaluate(native, "Country.GetGDP", [ObjectBinding.country(country_type, country_id)]).
    Functions and parameters are parsed by the engine, not by a Python DSL.
    The result value_handle must later be released, including ERROR_MORE_DATA.
    """
    raw_bindings = b"".join(binding.pack() for binding in bindings)
    return _submit(client, _request(Action.EVALUATE, expression=expression.encode("utf-8"),
        bindings=raw_bindings, output_bytes=output_bytes), adapter=adapter, process=process)


def _projection_request(action: Action, expression: str, bindings: Iterable[ObjectBinding], *,
                        world: int, output_bytes: int) -> bytes:
    if type(world) is not int or not 0 < world <= 0xffffffffffffffff:
        raise ValueError("world must be captured before fresh root production")
    if type(output_bytes) is not int or output_bytes < 0:
        raise ValueError("output_bytes must be nonnegative")
    text = expression.encode("utf-8")
    if not text or b"\0" in text or len(text) > 0x7fffffff:
        raise ValueError("native expression must be nonempty NUL-free signed32 UTF-8")
    packed_bindings = b"".join(binding.pack() for binding in bindings)
    text_offset = PROJECTION_HEADER_SIZE
    bindings_offset = (text_offset + len(text) + 7) & ~7
    output_offset = bindings_offset + len(packed_bindings)
    size = output_offset + output_bytes
    if size > 0xffffffff:
        raise ValueError("native projection request exceeds uint32 allocation size")
    header = REQUEST.pack(size, ERROR_IO_PENDING, action, 0, -1, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, text_offset, len(text), bindings_offset,
        len(packed_bindings)//BINDING_INPUT.size, output_offset, output_bytes,
        0, bytes(40), bytes(192))
    return (header + PROJECTION_SUFFIX.pack(world, 0, 0, 0, 0) + text
        + bytes(bindings_offset - text_offset - len(text)) + packed_bindings + bytes(output_bytes))


def evaluate_copied(client: NativeBridge, expression: str, bindings: Iterable[ObjectBinding] = (), *,
                    world: int, output_bytes: int = 4096,
                    adapter: str | int = "pdx_v3_data.dll",
                    process: RemoteProcess | None = None) -> DataQuery:
    """Evaluate a fresh root and copy a primitive/string in one native callback.

    The Any, property and argument/binding intermediates all die before Access
    exit, including partial text results. No raw/object/list/command can escape.
    Explicit full-ID or genuinely retained proven-world bindings are supported;
    raw wrapper-pointer bindings are not given a lifetime by this new path.
    This is read-only evaluation, not action invocation or a mutation allowlist.
    Old DLLs report unsupported/error; never fall back to borrowed UI pointers.
    """
    initial = _projection_request(Action.EVALUATE_COPIED, expression, bindings,
        world=world, output_bytes=output_bytes)
    return _submit(client, initial, adapter=adapter, process=process, decode=ProjectionResult.from_bytes)


def source_count(client: NativeBridge, source_expression: str, bindings: Iterable[ObjectBinding] = (), *,
                 world: int, adapter: str | int = "pdx_v3_data.dll",
                 process: RemoteProcess | None = None) -> DataQuery:
    """Produce/count/destroy a fresh native Creation source in one callback.

    source_produced/count_valid distinguish actual zero count from unavailable
    sources. A count is not a lease, stable identity, atomic multi-field snapshot,
    source history or visibility proof. No source/result handle is retained.
    """
    initial = _projection_request(Action.SOURCE_COUNT, source_expression, bindings,
        world=world, output_bytes=0)
    return _submit(client, initial, adapter=adapter, process=process, decode=ProjectionResult.from_bytes)


def _scoped_projection_request(action: Action, steps: Iterable[CreationStep], expression: str, *,
                               world: int, output_bytes: int) -> bytes:
    if type(world) is not int or not 0 < world <= 0xffffffffffffffff:
        raise ValueError("world must be captured before fresh scoped production")
    if type(output_bytes) is not int or not 0 <= output_bytes <= 0xffffffff:
        raise ValueError("output_bytes must fit uint32")
    selected: list[CreationStep] = []
    for step in steps:
        if len(selected) == MAX_SCOPED_DEPTH:
            raise ValueError("scoped Creation chain exceeds maximum depth 8")
        if not isinstance(step, CreationStep):
            raise ValueError("steps must contain CreationStep values")
        if step.source_arguments:
            raise ValueError("step source_arguments require project_page typed-step capability")
        selected.append(step)
    final_text = _creation_expression(expression)
    source_texts = [_creation_expression(step.source_expression) for step in selected]
    cursor = SCOPED_PROJECTION_HEADER_SIZE + len(selected) * CREATION_STEP.size
    records = []
    for step, text in zip(selected, source_texts):
        records.append(CREATION_STEP.pack(cursor, len(text), step.index,
            0xffffffff if step.expected_count is None else step.expected_count,
            0, 0, 0, 0, -1, 0xffffffff))
        cursor += len(text)
    text_offset = cursor
    output_offset = (text_offset + len(final_text) + 7) & ~7
    size = output_offset + output_bytes
    if size > 0xffffffff:
        raise ValueError("native scoped projection exceeds uint32 allocation size")
    header = REQUEST.pack(size, ERROR_IO_PENDING, action, 0, -1, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, text_offset, len(final_text), output_offset, 0,
        output_offset, output_bytes, 0, bytes(40), bytes(192))
    return (header + PROJECTION_SUFFIX.pack(world, 0, 0, 0, 0)
        + SCOPED_PROJECTION_SUFFIX.pack(SCOPED_PROJECTION_HEADER_SIZE, len(selected), 0, 0)
        + b"".join(records) + b"".join(source_texts) + final_text
        + bytes(output_offset - text_offset - len(final_text)) + bytes(output_bytes))


def evaluate_scoped(client: NativeBridge, steps: Iterable[CreationStep], expression: str, *,
                    world: int, output_bytes: int = 4096,
                    adapter: str | int = "pdx_v3_data.dll",
                    process: RemoteProcess | None = None) -> DataQuery:
    """Read a copied leaf from fresh nested original Creation/provider bindings.

    Up to eight sources are produced once each, within the preceding Binding.
    All properties compile first. No intermediate element is retained or retagged.
    A false/zero/empty value is separate from the per-source production receipts.
    All cleanup finishes inside the original callback/Access, including errors
    and partial text. No action, native pointer, automatic replay or fallback.
    """
    initial = _scoped_projection_request(Action.EVALUATE_SCOPED, steps, expression,
        world=world, output_bytes=output_bytes)
    return _submit(client, initial, adapter=adapter, process=process, decode=ProjectionResult.from_bytes)


def scoped_source_count(client: NativeBridge, steps: Iterable[CreationStep], source_expression: str, *,
                        world: int, adapter: str | int = "pdx_v3_data.dll",
                        process: RemoteProcess | None = None) -> DataQuery:
    """Produce/count a fresh final Creation in the last selected original Binding.

    For a child count pass only the parent step. count_valid proves an actual
    empty final source; .steps retains earlier counts on a later failure. Indices
    and expected counts are observations, not stable identity or ownership.
    Old adapters reject additive actions. There is no retained-source fallback.
    """
    initial = _scoped_projection_request(Action.SCOPED_SOURCE_COUNT, steps, source_expression,
        world=world, output_bytes=0)
    return _submit(client, initial, adapter=adapter, process=process, decode=ProjectionResult.from_bytes)


def snapshot(client: NativeBridge, value_handle: int, *, output_bytes: int = 4096,
             adapter: str | int = "pdx_v3_data.dll",
             process: RemoteProcess | None = None) -> DataQuery:
    """Copy a retained value again without compiling or invoking its getter."""
    return _submit(client, _request(Action.SNAPSHOT, value_handle=value_handle,
        output_bytes=output_bytes), adapter=adapter, process=process)


def release(client: NativeBridge, value_handle: int, *, adapter: str | int = "pdx_v3_data.dll",
            process: RemoteProcess | None = None) -> DataQuery:
    """Destroy exactly one adapter-owned engine Any in the native access scope."""
    return _submit(client, _request(Action.RELEASE, value_handle=value_handle),
                   adapter=adapter, process=process)


def list_count(client: NativeBridge, value_handle: int, *,
               adapter: str | int = "pdx_v3_data.dll",
               process: RemoteProcess | None = None) -> DataQuery:
    """Ask the native Creation provider for its current element count.

    Only native CPdxGuiDataModelCreation values support this action. total is an
    element count in this result, unlike snapshot total (payload/string bytes).
    Count does not freeze a game-owned backing collection.
    """
    return _submit(client, _request(Action.LIST_COUNT, value_handle=value_handle),
                   adapter=adapter, process=process)


def list_element(client: NativeBridge, value_handle: int, index: int, *,
                 output_bytes: int = 4096, adapter: str | int = "pdx_v3_data.dll",
                 process: RemoteProcess | None = None) -> DataQuery:
    """Return one typed/raw native collection element as a separately-owned value.

    The native provider returns Binding64, then genuine engine Any copy retains
    the typed result. The new value holds the source evaluation dependency even
    after its public handle is released. Unsupported Binding0/1 is reported, not
    treated as an Any or fabricated into a Python list. Release the new handle.
    """
    if index < 0:
        raise ValueError("index must be nonnegative")
    return _submit(client, _request(Action.LIST_ELEMENT, value_handle=value_handle,
        start=index, output_bytes=output_bytes), adapter=adapter, process=process)


def encode_action_request(expression: str, input: TypedInput,
                          bindings: Iterable[ObjectBinding] = (), *,
                          output_bytes: int = 4096) -> bytes:
    """Pure original ActionRequest encoder. No allocation, dispatch or replay."""
    if output_bytes < 0:
        raise ValueError("output_bytes must be nonnegative")
    text = expression.encode("utf-8")
    binding_bytes = b"".join(binding.pack() for binding in bindings)
    input_bytes = input.pack()
    text_offset = ACTION_HEADER_SIZE
    bindings_offset = (text_offset + len(text) + 7) & ~7
    input_offset = bindings_offset + len(binding_bytes)
    output_offset = (input_offset + len(input_bytes) + 7) & ~7
    size = output_offset + output_bytes
    header = REQUEST.pack(size, ERROR_IO_PENDING, Action.INVOKE_ACTION, EXPLICIT_ACTION,
        -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, text_offset, len(text), bindings_offset, len(binding_bytes)//BINDING_INPUT.size,
        output_offset, output_bytes, 0, bytes(40), bytes(192))
    suffix = ACTION_SUFFIX.pack(input_offset, len(input_bytes), 0, 0, 0, input.kind)
    initial = (header + suffix + text + bytes(bindings_offset-text_offset-len(text))
        + binding_bytes + input_bytes + bytes(output_offset-input_offset-len(input_bytes))
        + bytes(output_bytes))
    return initial


def invoke_action(client: NativeBridge, expression: str, input: TypedInput,
                  bindings: Iterable[ObjectBinding] = (), *,
                  output_bytes: int = 4096, adapter: str | int = "pdx_v3_data.dll",
                  process: RemoteProcess | None = None) -> DataQuery:
    """Invoke a compiled native data action/setter with caller-explicit intent.

    This entrypoint calls native action categories3/4 only. It never changes the
    read-only evaluate() path to execute an action, and never redirects a getter.
    The input Any is copied/owned. The result snapshots that same Any after call;
    accepted is native AL, not proof of a world change. A completed failed/rejected
    action is never automatically replayed. Release any returned value_handle once.
    Expression syntax and parameters remain the game's native parser's own syntax.
    """
    initial = encode_action_request(expression, input, bindings, output_bytes=output_bytes)
    return _submit(client, initial, adapter=adapter, process=process, decode=ActionResult.from_bytes)



ACTION_SEQUENCE = struct.Struct("<8I")
ACTION_SEQUENCE_STEP = struct.Struct("<2I")
MAX_ACTION_SEQUENCE = 32


class ActionContinuation(IntEnum):
    ALWAYS = 0
    INVOKED_AND_ACCEPTED = 1


@dataclass(frozen=True)
class ActionStep:
    """One original action and caller-explicit dependency on its returned flags."""
    expression: str
    input: TypedInput
    continuation: ActionContinuation
    bindings: tuple[ObjectBinding, ...] = ()
    output_bytes: int = 4096


@dataclass(frozen=True)
class ActionSequenceResult:
    size: int
    error: int
    count: int
    steps_offset: int
    completed: int
    stopped: bool
    native_results: tuple[ActionResult, ...]
    copied_bytes: int

    @property
    def complete(self) -> bool:
        return self.copied_bytes >= self.size and len(self.native_results) == self.count

    @classmethod
    def from_bytes(cls, value: bytes) -> ActionSequenceResult:
        size, error, count, offset, completed, stopped, _, _ = ACTION_SEQUENCE.unpack_from(value)
        if (not 0 < count <= MAX_ACTION_SEQUENCE or completed > count
                or offset < ACTION_SEQUENCE.size or offset + count * ACTION_SEQUENCE_STEP.size > size):
            raise ValueError("invalid native action sequence span")
        results = []
        try:
            for index in range(count):
                descriptor = offset + index * ACTION_SEQUENCE_STEP.size
                if descriptor + ACTION_SEQUENCE_STEP.size > len(value): break
                request_offset, continuation = ACTION_SEQUENCE_STEP.unpack_from(value, descriptor)
                if request_offset < offset + count * ACTION_SEQUENCE_STEP.size or request_offset + ACTION_HEADER_SIZE > size:
                    raise ValueError("invalid original action sequence packet")
                if request_offset + ACTION_HEADER_SIZE > len(value): break
                request_size = struct.unpack_from("<I", value, request_offset)[0]
                if request_size < ACTION_HEADER_SIZE or request_size > size - request_offset:
                    raise ValueError("invalid original action request span")
                if struct.unpack_from("<I", value, request_offset + 8)[0] != Action.INVOKE_ACTION:
                    raise ValueError("invalid original action discriminator")
                results.append(ActionResult.from_bytes(value[request_offset:request_offset + request_size], allow_partial=True))
        except Exception as decode_error:
            # A later malformed packet must not hide prior decoded owners or
            # receipts. completed is a native observation, never a slice bound.
            context = cast(_SequenceDecodeContext, decode_error)
            context.partial_sequence = cls(size, error, count, offset, completed,
                                           bool(stopped), tuple(results), len(value))
            raise
        return cls(size, error, count, offset, completed, bool(stopped), tuple(results), len(value))


class ActionSequenceReadError(RuntimeError):
    """Original callback storage and copied prefix remain on query; no replay."""
    def __init__(self, query, partial_result, copied_bytes):
        self.query, self.partial_result, self.copied_bytes = query, partial_result, copied_bytes
        # Raw status is the generic reader's already-observed mapping. Its latest
        # callback_error is not necessarily this request's error. Keep IDs and
        # original Access/sequence/action results unchanged; never infer a code.
        self.completion_status = query.completion_status
        super().__init__("action sequence callback fault or incomplete result copy; original query retained")


def encode_action_sequence(steps: Iterable[ActionStep]) -> bytes:
    """Compose existing ActionRequest packets. No second encoder or evaluator."""
    steps = tuple(steps)
    if not 0 < len(steps) <= MAX_ACTION_SEQUENCE:
        raise ValueError("an action sequence contains 1..32 steps")
    offset = ACTION_SEQUENCE.size + len(steps) * ACTION_SEQUENCE_STEP.size
    descriptors, packets = [], []
    for step in steps:
        continuation = ActionContinuation(step.continuation)
        packet = encode_action_request(step.expression, step.input, step.bindings,
                                       output_bytes=step.output_bytes)
        descriptors.append(ACTION_SEQUENCE_STEP.pack(offset, continuation))
        padded = packet + bytes((-len(packet)) & 7)
        packets.append(padded)
        offset += len(padded)
    return (ACTION_SEQUENCE.pack(offset, ERROR_IO_PENDING, len(steps), ACTION_SEQUENCE.size, 0, 0, 0, 0)
            + b"".join(descriptors) + b"".join(packets))


def _retain_access_decoder(query, *, owns_process, data_adapter, payload_size,
                           payload_type, partial_attribute, read_error):
    """Retain this Data adapter's existing Access copy, never dispatch or reread."""
    from . import v3_access
    query._owns_process, query.data_adapter = owns_process, data_adapter
    original_decode = query.decode_result

    def decode_retained(value):
        try:
            result = original_decode(value)
        except Exception as error:
            context = cast(_AccessDecodeContext, error)
            context.query, context.copied_bytes = query, bytes(value)
            context.completion_status = query.completion_status
            query.copied_bytes = bytes(value)
            partial = getattr(error, partial_attribute, None)
            if not isinstance(partial, payload_type):
                partial = None
            if len(value) >= v3_access.REQUEST.size:
                # Decode only the original copied Access header. A payload
                # prefix already decoded before failure must never be rerun.
                result = v3_access.AccessResult.from_bytes(value, payload_size=payload_size,
                    decode_payload=lambda _: partial)
                context.partial_result = query.partial_result = result
            raise
        payload = result.callee_result
        if (result.error or payload.error == ERROR_IO_PENDING or not payload.complete
                or len(value) < query.result_size):
            query.partial_result, query.copied_bytes = result, bytes(value)
            raise read_error(query, result, bytes(value))
        return result

    query.decode_result = decode_retained
    return query


def invoke_action_sequence(client: NativeBridge, steps: Iterable[ActionStep], *,
                           adapter: str | int = "pdx_v3_data.dll",
                           access_adapter: str | int = "pdx_v3_access.dll",
                           process: RemoteProcess | None = None):
    """Run dependent original actions inside one existing Interface/Access scope.

    Each step explicitly selects ALWAYS or INVOKED_AND_ACCEPTED. Native error0
    does not control dependencies: an accepted action may have a later copy error.
    No transaction, rollback, action replay, UI workflow or effect proof is added.
    The existing AccessResult.callee_result is ActionSequenceResult; every copied
    original ActionResult remains available, including unexecuted997 suffixes.
    Every actual returned Any handle still belongs to the original data registry.
    Adopt/release it through query.data_adapter, the actual Data DLL module base,
    NOT the Access adapter. Post-input tails can be read from those same handles.
    Pending/fault/short copies keep the original query/allocation and copied bytes.
    A normal completed read frees only callback storage, never returned Anys.
    """
    from . import v3_access
    from .v3_wire import resolve_v3_export
    initial = encode_action_sequence(steps)
    owns = process is None
    process = process or RemoteProcess(client.pid)
    data_adapter = None
    try:
        handler = resolve_v3_export(process, adapter, "PdxV3DataInvokeActionSequenceInterface")
        data_adapter = process.export_owner(handler)
        query = v3_access.submit_callback(client, handler, initial,
            mode=1, flag=0, dispatch="interface", adapter=access_adapter, process=process,
            source="pdx-runtime/v3_data.action_sequence", decode_payload=ActionSequenceResult.from_bytes)
    except BaseException as error:
        query = getattr(error, "query", None)
        if query is not None:
            _retain_access_decoder(query, owns_process=owns, data_adapter=data_adapter,
                payload_size=len(initial), payload_type=ActionSequenceResult,
                partial_attribute="partial_sequence", read_error=ActionSequenceReadError)
        elif owns:
            process.close()
        raise
    return _retain_access_decoder(query, owns_process=owns, data_adapter=data_adapter,
        payload_size=len(initial), payload_type=ActionSequenceResult,
        partial_attribute="partial_sequence", read_error=ActionSequenceReadError)


GAME_RULE_SELECTION = struct.Struct("<40I")
GAME_RULE_RECEIPT_SIZE = ACTION_HEADER_SIZE + ANY_INPUT.size


class GameRuleSelectionOutcome(IntEnum):
    UNOBSERVED = 0
    NATIVE_NULL = 1
    EMPTY = 2
    TARGET_ABSENT = 3
    CURRENT_NOT_MEMBER = 4
    ALREADY_SELECTED = 5
    SELECTED = 6
    ACTION_STOPPED = 7
    OUTPUT_PARTIAL = 8
    NATIVE_ERROR = 9


class GameRuleSelectionStage(IntEnum):
    VALIDATE = 1
    ROOT = 2
    RESOLVE = 3
    CHOICES = 4
    KEYS = 5
    PLAN = 6
    ACTIONS = 7
    FINAL = 8
    COMPLETE = 9


@dataclass(frozen=True)
class GameRuleSelectionResult:
    """Copied canonical keys and original action receipts from one callback.

    All header values remain native observations. Indices describe the current
    canonical ordering, not stable IDs. copied_bytes measures the Python copy;
    size and copied_output_bytes are distinct native header values. A valid but
    incompletely copied key has no Python value, not a fabricated empty key.
    """
    size: int
    error: int
    rule_input_offset: int
    rule_input_bytes: int
    setting_input_offset: int
    setting_input_bytes: int
    output_offset: int
    output_bytes: int
    stage: int
    outcome: int
    root_observed: int
    root_found: int
    row_observed: int
    row_found: int
    count_valid: int
    choice_count: int
    current_found: int
    target_found: int
    current_index: int
    target_index: int
    direction: int
    planned: int
    completed: int
    stopped: int
    receipts_offset: int
    receipts_count: int
    keys_offset: int
    keys_count: int
    final_observed: int
    final_selected: int
    execution_thread_id: int
    initial_key_valid: int
    initial_key_offset: int
    initial_key_bytes: int
    final_key_valid: int
    final_key_offset: int
    final_key_bytes: int
    receipts_bytes: int
    copied_output_bytes: int
    observation_error: int
    key_bytes: tuple[bytes, ...] = ()
    initial_key_value: bytes | None = None
    final_key_value: bytes | None = None
    native_results: tuple[ActionResult, ...] = ()
    copied_bytes: int = 0
    _decode_valid: bool = True

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(key.decode("utf-8", "replace") for key in self.key_bytes)

    @property
    def initial_key(self) -> str | None:
        return (self.initial_key_value.decode("utf-8", "replace")
                if self.initial_key_value is not None else None)

    @property
    def final_key(self) -> str | None:
        return (self.final_key_value.decode("utf-8", "replace")
                if self.final_key_value is not None else None)

    @property
    def complete(self) -> bool:
        return (self._decode_valid and self.copied_bytes >= self.size
                and len(self.key_bytes) == self.keys_count
                and len(self.native_results) == self.receipts_count
                and (not self.initial_key_valid or self.initial_key_value is not None)
                and (not self.final_key_valid or self.final_key_value is not None))

    @classmethod
    def from_bytes(cls, value: bytes) -> GameRuleSelectionResult:
        h = cast(_RawGameRuleSelection, GAME_RULE_SELECTION.unpack_from(value))
        keys: list[bytes] = []
        results: list[ActionResult] = []
        initial: bytes | None = None
        final: bytes | None = None

        def snapshot(*, valid: bool = True) -> GameRuleSelectionResult:
            return cls(*h, tuple(keys), initial, final, tuple(results), len(value), valid)

        def output_span(offset, length, name):
            if (offset < h[6] or offset > h[0] or length > h[0] - offset
                    or offset > h[6] + h[7] or length > h[6] + h[7] - offset):
                raise ValueError("invalid game-rule " + name + " span")
            return value[offset:offset + length]

        try:
            if (h[0] < GAME_RULE_SELECTION.size or h[6] < GAME_RULE_SELECTION.size
                    or h[6] > h[0] or h[7] > h[0] - h[6]):
                raise ValueError("invalid game-rule allocation/output span")
            if h[27]:
                # Validate the declared table against the allocation, then read
                # only complete entries and complete canonical keys from it.
                table = output_span(h[26], h[27] * PAGE_SPAN.size, "key table")
                for index in range(min(h[27], len(table) // PAGE_SPAN.size)):
                    offset, length = PAGE_SPAN.unpack_from(table, index * PAGE_SPAN.size)
                    key = output_span(offset, length, "canonical key")
                    if len(key) != length:
                        break
                    keys.append(key)
            if h[31]:
                key = output_span(h[32], h[33], "initial key")
                if len(key) == h[33]:
                    initial = key
            if h[34]:
                key = output_span(h[35], h[36], "final key")
                if len(key) == h[36]:
                    final = key
            if h[25] or h[37]:
                receipts = output_span(h[24], h[37], "receipts")
                for index in range(h[25]):
                    start = index * GAME_RULE_RECEIPT_SIZE
                    if start + GAME_RULE_RECEIPT_SIZE > h[37]:
                        raise ValueError("invalid game-rule original action span")
                    packet = receipts[start:start + GAME_RULE_RECEIPT_SIZE]
                    if len(packet) < ACTION_HEADER_SIZE:
                        break
                    size, _, action = struct.unpack_from("<3I", packet)
                    if size != GAME_RULE_RECEIPT_SIZE:
                        raise ValueError("invalid game-rule original action size")
                    if action != Action.INVOKE_ACTION:
                        raise ValueError("invalid game-rule original action discriminator")
                    input_offset, input_bytes, _, _, _, input_kind = ACTION_SUFFIX.unpack_from(packet, REQUEST.size)
                    if (input_offset != ACTION_HEADER_SIZE or input_bytes != ANY_INPUT.size
                            or input_kind != Kind.EMPTY):
                        raise ValueError("invalid game-rule original empty input span/kind")
                    if (len(packet) == GAME_RULE_RECEIPT_SIZE
                            and ANY_INPUT.unpack_from(packet, ACTION_HEADER_SIZE) != (Kind.EMPTY, 0, 0, 0)):
                        raise ValueError("invalid game-rule original empty input")
                    results.append(ActionResult.from_bytes(packet, allow_partial=True))
        except Exception as error:
            # Keep the exact original exception, all header observations and the
            # decoded prefix. completed is never a bound on observed receipts.
            context = cast(_RuleDecodeContext, error)
            context.partial_game_rule_selection = snapshot(valid=False)
            raise
        return snapshot()


class GameRuleSelectionReadError(RuntimeError):
    """The same callback owns incomplete/faulted output; no action is replayed."""
    def __init__(self, query, partial_result, copied_bytes):
        self.query, self.partial_result, self.copied_bytes = query, partial_result, copied_bytes
        self.completion_status = query.completion_status
        super().__init__("game-rule callback fault or incomplete result copy; original query retained")


def encode_game_rule_selection(rule_key: str, setting_key: str, *,
                               output_bytes: int = 65536) -> bytes:
    """Pack two existing CString TypedInputs. User text is never Data DSL."""
    if not isinstance(rule_key, str) or not isinstance(setting_key, str):
        raise TypeError("rule_key and setting_key must be strings")
    if type(output_bytes) is not int or not 0 <= output_bytes <= 0xffffffff:
        raise ValueError("output_bytes must fit uint32")
    rule = TypedInput.string(rule_key).pack()
    setting = TypedInput.string(setting_key).pack()
    rule_offset = GAME_RULE_SELECTION.size
    setting_offset = (rule_offset + len(rule) + 7) & ~7
    output_offset = (setting_offset + len(setting) + 7) & ~7
    size = output_offset + output_bytes
    if size > 0xffffffff:
        raise ValueError("game-rule request exceeds uint32 allocation size")
    header = [0] * 40
    header[:8] = (size, ERROR_IO_PENDING, rule_offset, len(rule),
                  setting_offset, len(setting), output_offset, output_bytes)
    header[18] = header[19] = 0xffffffff
    return (GAME_RULE_SELECTION.pack(*header) + rule
            + bytes(setting_offset - rule_offset - len(rule)) + setting
            + bytes(output_offset - setting_offset - len(setting)) + bytes(output_bytes))


def select_game_rule(client: NativeBridge, rule_key: str, setting_key: str, *,
                     output_bytes: int = 65536,
                     adapter: str | int = "pdx_v3_data.dll",
                     access_adapter: str | int = "pdx_v3_access.dll",
                     process: RemoteProcess | None = None):
    """Select a canonical rule setting in one existing Interface/Access callback.

    This returns the original CallbackResult. Its AccessResult.callee_result is
    GameRuleSelectionResult. Keys are copied native observations; actions retain
    their original invoked/accepted/error flags, not inferred effect evidence.
    ApplySettings is separate and is never invoked here. No policy filters user
    strings, adds a gameplay step cap, retries actions or selects an old-DLL path.
    Native OUTPUT_PARTIAL/error234 can be a completed partial outcome. Outer
    Access errors, native pending997, short copies and decode errors retain the
    exact query/allocation, copied_bytes and already-observed completion_status.
    """
    from . import v3_access
    from .v3_wire import resolve_v3_export
    initial = encode_game_rule_selection(rule_key, setting_key, output_bytes=output_bytes)
    owns = process is None
    process = process or RemoteProcess(client.pid)
    data_adapter = None

    try:
        handler = resolve_v3_export(process, adapter, "PdxV3DataSelectGameRuleInterface")
        data_adapter = process.export_owner(handler)
        query = v3_access.submit_callback(client, handler, initial,
            mode=1, flag=0, dispatch="interface", adapter=access_adapter, process=process,
            source="pdx-runtime/v3_data.select_game_rule", decode_payload=GameRuleSelectionResult.from_bytes)
    except BaseException as error:
        query = getattr(error, "query", None)
        if query is not None:
            _retain_access_decoder(query, owns_process=owns, data_adapter=data_adapter,
                payload_size=len(initial), payload_type=GameRuleSelectionResult,
                partial_attribute="partial_game_rule_selection", read_error=GameRuleSelectionReadError)
        elif owns:
            process.close()
        raise
    return _retain_access_decoder(query, owns_process=owns, data_adapter=data_adapter,
        payload_size=len(initial), payload_type=GameRuleSelectionResult,
        partial_attribute="partial_game_rule_selection", read_error=GameRuleSelectionReadError)


def invoke_property_action(client: NativeBridge, expression: str,
                           bindings: Iterable[ObjectBinding] = (), *,
                           output_bytes: int = 4096,
                           adapter: str | int = "pdx_v3_data.dll",
                           process: RemoteProcess | None = None) -> DataQuery:
    """Invoke an explicit native property action whose arguments are in expression.

    This supplies an empty input Any. The game's compiler parses and owns all
    expression arguments and reference tables. For example, a SortStates action
    takes its CString sort property from native parameter context, not inputAny.
    No custom literal encoding, function-name catalog, forced setter dispatch or
    retry is added. Callback acceptance is still separate from an observed change.
    """
    return invoke_action(client, expression, TypedInput.empty(), bindings,
        output_bytes=output_bytes, adapter=adapter, process=process)


def create_state_name_context(client: NativeBridge, state_id: int, *,
                              output_bytes: int = 4096,
                              adapter: str | int = "pdx_v3_data.dll",
                              process: RemoteProcess | None = None) -> DataQuery:
    """Create/initialize an isolated real native State-name draft context.

    A complete live State ID is resolved before the native factory. This does not
    Show, install in the game's popup manager, Confirm, rename a State or save.
    The returned owned Any can be bound into existing evaluate()/invoke_action();
    use its live dynamic type, not a fixed ID. Release the retained value once.
    Creation and initialization failure retain no published partial owner.
    """
    if not 0 <= state_id <= 0xffffffff:
        raise ValueError("state_id must be the complete uint32 ID")
    return _submit(client, _request(Action.CREATE_STATE_NAME_CONTEXT, start=state_id,
        output_bytes=output_bytes), adapter=adapter, process=process)



class OwnedContextKind(IntEnum):
    RELEASE_COUNTRY = 1
    POWER_BLOC = 2
    MILITARY_FORMATION_NAME = 3
    POWER_BLOC_FORMATION = 4
    SHIP_NAME = 5


def _create_owned_context(client: NativeBridge, source_handle: int, kind: OwnedContextKind, *,
                          output_bytes: int, adapter: str | int,
                          process: RemoteProcess | None) -> DataQuery:
    return _submit(client, _request(Action.CREATE_OWNED_CONTEXT, flags=kind,
        value_handle=source_handle, output_bytes=output_bytes), adapter=adapter, process=process,
        callback_symbol="PdxV3DataCreateOwnedContextInterface")


def create_release_country_context(client: NativeBridge, creation_handle: int, *,
                                   output_bytes: int = 4096,
                                   adapter: str | int = "pdx_v3_data.dll",
                                   process: RemoteProcess | None = None) -> DataQuery:
    """Create isolated ReleaseCountryWindow from the original owned Creation Any.

    creation_handle is a live original ValueNode, never an ordinal/raw pointer.
    Native code uses the original CountryCreation converter and full constructor.
    Returns an owned CPdxPtr<CReleaseCountryWindow> Any in the same registry.
    It retains the source graph. No Show, manager change, Confirm or release action.
    Bind its dynamic result.type_id and value_handle; release that Any once.
    """
    return _create_owned_context(client, creation_handle, OwnedContextKind.RELEASE_COUNTRY,
        output_bytes=output_bytes, adapter=adapter, process=process)


def create_power_bloc_context(client: NativeBridge, bloc_handle: int, *,
                              output_bytes: int = 4096,
                              adapter: str | int = "pdx_v3_data.dll",
                              process: RemoteProcess | None = None) -> DataQuery:
    """Create existing-Bloc customization context from its original owned Any.

    Native PowerBloc conversion supplies the original complete reference, then
    the original resolver/constructor/configuration run inside this callback.
    Returns CPdxPtr<CPowerBlocCustomizationPopup>, retaining the source graph.
    No guessed ID/name, GUI Show, manager assignment or Confirm occurs.
    Formation-draft mode is not this function. Release the returned Any once.
    """
    return _create_owned_context(client, bloc_handle, OwnedContextKind.POWER_BLOC,
        output_bytes=output_bytes, adapter=adapter, process=process)



def create_power_bloc_formation_context(client: NativeBridge, formation_handle: int, *,
                                       output_bytes: int = 4096,
                                       adapter: str | int = "pdx_v3_data.dll",
                                       process: RemoteProcess | None = None) -> DataQuery:
    """Create customization context from an actual owned PowerBlocFormation Any.

    The original configuration consumes one extra native retained reference.
    It retains a separate reference in the popup. No caller raw pointer, global
    lookup, Show or Confirm is used. Returns an owned PowerBlocCustomizationPopup
    in formation-draft mode, NOT existing-PowerBloc mode. A later explicit native
    Confirm edits that original draft; it is not a world-command producer.
    """
    return _create_owned_context(client, formation_handle, OwnedContextKind.POWER_BLOC_FORMATION,
        output_bytes=output_bytes, adapter=adapter, process=process)


def create_military_formation_name_context(client: NativeBridge, formation_handle: int, *,
                                           output_bytes: int = 4096,
                                           adapter: str | int = "pdx_v3_data.dll",
                                           process: RemoteProcess | None = None) -> DataQuery:
    """Create and initialize the original isolated formation-name context.

    Input is the original owned CPdxPtr<CDataMilitaryFormation> ValueNode handle.
    Data type is MilitaryFormationChangeNamePopup; its exact C++ object is
    CMilitaryFormationNameChangePopup. Mandatory native initialization preserves
    current flag/color values. No Show, Confirm, rename or command submit occurs.
    The returned owned Any and original source share the existing owner graph.
    """
    return _create_owned_context(client, formation_handle, OwnedContextKind.MILITARY_FORMATION_NAME,
        output_bytes=output_bytes, adapter=adapter, process=process)


def create_ship_name_context(client: NativeBridge, ship_handle: int, *,
                             output_bytes: int = 4096,
                             adapter: str | int = "pdx_v3_data.dll",
                             process: RemoteProcess | None = None) -> DataQuery:
    """Create and initialize the original isolated ship-name context.

    ship_handle is an original owned CPdxPtr<CDataShip> ValueNode handle, not a
    raw ship pointer or a full reference. Native code uses its proven wrapper,
    full-reference resolver and ref0 factory, then mandatory initialization.
    Data type is ShipChangeNamePopup; its exact C++ object is
    CShipChangeNamePopup. The existing owned Any/dependency graph retains the
    source. No Show, popup-manager change, Confirm or Session submit occurs.
    Release the returned native value once, including a partial/error result
    that publishes a new value_handle. Never release the input as that output.
    """
    return _create_owned_context(client, ship_handle, OwnedContextKind.SHIP_NAME,
        output_bytes=output_bytes, adapter=adapter, process=process)


def state_name_draft(client: NativeBridge, value_handle: int, *, index: int = 0,
                     output_bytes: int = 4096, adapter: str | int = "pdx_v3_data.dll",
                     process: RemoteProcess | None = None) -> DataQuery:
    """Copy native draft bytes without inventing an expression text getter.

    Index 0 is the State-name draft; indices 1..5 are the native city/port/farm/mine/wood
    draft sequence. This copies existing native storage only, does not edit or
    Confirm. The source handle remains owned by the caller. After a world change,
    this adapter-created context is not used to dereference old world objects.
    """
    if not 0 <= index <= 5:
        raise ValueError("index must be 0..5")
    return _submit(client, _request(Action.STATE_NAME_DRAFT, value_handle=value_handle,
        start=index, output_bytes=output_bytes), adapter=adapter, process=process)


def state_name_override(client: NativeBridge, state_id: int, *, index: int = 0,
                        output_bytes: int = 4096, adapter: str | int = "pdx_v3_data.dll",
                        process: RemoteProcess | None = None) -> DataQuery:
    """Copy a live State's stored custom name, independent of popup draft storage.

    Index 0 is the custom State name; indices 1..5 are City/Port/Farm/Mine/Wood.
    Empty bytes mean no stored override, not an empty localized display name.
    This resolves and validates the full State ID in the native read scope. It
    does not Initialize a popup, construct a command, edit names or submit it.
    """
    if not 0 <= state_id <= 0xffffffff or not 0 <= index <= 5:
        raise ValueError("state_id must be a complete uint32 ID and index must be 0..5")
    return _submit(client, _request(Action.STATE_NAME_OVERRIDE, start=state_id,
        flags=index, output_bytes=output_bytes), adapter=adapter, process=process)


def command_value(client: NativeBridge, value_handle: int, *, output_bytes: int = 32,
                  adapter: str | int = "pdx_v3_data.dll",
                  process: RemoteProcess | None = None) -> DataQuery:
    """Copy a shared command carrier plus its captured generation world.

    This checks the retained handle/native CDataCommand type in the native scope.
    It does not clone, validate or submit. Keep source value_handle alive through
    the separate generic Clone callback, then own/destroy/submit that raw clone.
    A zero generation_world means no captured world is available; do not supply
    the generic Clone default or a later current world as a substitute.
    """
    return _submit(client, _request(Action.COMMAND_VALUE, value_handle=value_handle,
        output_bytes=output_bytes), adapter=adapter, process=process)


def _element_request(source_expression: str, index: int, expression: str,
                     bindings: Iterable[ObjectBinding], *, world: int,
                     input: TypedInput | None, condition: str,
                     expected_count: int | None, output_bytes: int,
                     pointer_array: PointerArraySource | None = None,
                     count_only: bool = False, direct_context: bool = False,
                     source_index_action: bool = False) -> bytes:
    if source_index_action and (pointer_array is not None or count_only or direct_context or input is None):
        raise ValueError("source index action uses a fresh Creation and its selected ordinal")
    if not (0 <= world <= 0xffffffffffffffff if source_index_action else 0 < world <= 0xffffffffffffffff):
        raise ValueError("world must be captured before original source production")
    if not 0 <= index <= 0x7fffffff:
        raise ValueError("index must fit native signed32")
    if output_bytes < 0:
        raise ValueError("output_bytes must be nonnegative")
    if expected_count is not None and not 0 <= expected_count <= 0x7fffffff:
        raise ValueError("expected_count must fit native signed32")
    if pointer_array is not None and not isinstance(pointer_array, PointerArraySource):
        raise TypeError("pointer_array must describe one original native pointer array")
    guarded = pointer_array is not None and pointer_array.guard_input is not None
    subobject = pointer_array is not None and bool(pointer_array.element_subobject_offset) and not count_only
    if count_only and (pointer_array is None or input is not None or condition or direct_context or guarded):
        raise ValueError("count-only is a read-only original pointer-array source operation")
    if direct_context and (pointer_array is None or source_expression or input is not None):
        raise ValueError("direct context is a read-only getter on the original pointer-array element")
    source, text, check = (value.encode("utf-8") for value in
                           (source_expression, expression, condition))
    root = pointer_array.root_expression.encode("utf-8") if pointer_array is not None else b""
    guard_text = cast(PointerArraySource, pointer_array).guard_expression.encode("utf-8") if guarded else b""
    guard_input = cast(TypedInput, cast(PointerArraySource, pointer_array).guard_input).pack() if guarded else b""
    if (not count_only and (not text or (not direct_context and not source))) or any(
            b"\0" in value or len(value) > 0x7fffffff for value in (source, text, check, root, guard_text)):
        raise ValueError("native expressions must be NUL-free, nonempty and fit signed32")
    binding_bytes = b"".join(binding.pack() for binding in bindings)
    input_bytes = (input or TypedInput.empty()).pack()
    text_offset = (ARRAY_ELEMENT_HEADER_SIZE if pointer_array is not None else ELEMENT_HEADER_SIZE)
    if subobject:
        text_offset += SUBOBJECT_SOURCE_SUFFIX.size
    if guarded:
        text_offset += SOURCE_GUARD_SUFFIX.size
    source_offset = text_offset + len(text)
    condition_offset = source_offset + len(source)
    root_offset = condition_offset + len(check)
    guard_text_offset = root_offset + len(root)
    bindings_offset = (guard_text_offset + len(guard_text) + 7) & ~7
    input_offset = bindings_offset + len(binding_bytes)
    guard_input_offset = (input_offset + len(input_bytes) + 7) & ~7
    output_offset = (guard_input_offset + len(guard_input) + 7) & ~7
    size = output_offset + output_bytes
    if size > 0xffffffff:
        raise ValueError("native element request exceeds uint32 allocation size")
    if source_index_action:
        action = Action.INVOKE_SOURCE_INDEX_ACTION
    elif pointer_array is not None:
        action = Action.INVOKE_ARRAY_ELEMENT_ACTION if input is not None else Action.EVALUATE_ARRAY_ELEMENT
    else:
        action = Action.INVOKE_ELEMENT_ACTION if input is not None else Action.EVALUATE_ELEMENT
    header = REQUEST.pack(size, ERROR_IO_PENDING, action,
        EXPLICIT_ACTION if input is not None else 0, -1, index,
        0xffffffff if expected_count is None else expected_count,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        text_offset, len(text), bindings_offset, len(binding_bytes)//BINDING_INPUT.size,
        output_offset, output_bytes, 0, bytes(40), bytes(192))
    suffix = ACTION_SUFFIX.pack(input_offset, len(input_bytes), 0, 0, 0,
                                input.kind if input is not None else Kind.EMPTY)
    element = ELEMENT_SUFFIX.pack(world, source_offset, len(source),
        condition_offset, len(check), -1, 0, 0, 0)
    array = (POINTER_ARRAY_SUFFIX.pack(root_offset, len(root), pointer_array.byte_offset,
        pointer_array.type_id, pointer_array.index,
        0xffffffff if pointer_array.expected_count is None else pointer_array.expected_count,
        pointer_array.interface_thread_id,
        (ARRAY_COUNT_ONLY if count_only else ARRAY_DIRECT_CONTEXT if direct_context else 0)
            | (ARRAY_SOURCE_GUARD if guarded else 0) | (ARRAY_ELEMENT_SUBOBJECT if subobject else 0), 0, 0)
        if pointer_array is not None else b"")
    guard = (SOURCE_GUARD_SUFFIX.pack(guard_text_offset, len(guard_text),
                                    guard_input_offset, len(guard_input), 0, 0) if guarded else b"")
    subobject_bytes = (SUBOBJECT_SOURCE_SUFFIX.pack(cast(PointerArraySource, pointer_array).element_subobject_offset, 0,
        cast(PointerArraySource, pointer_array).expected_element_vtable) if subobject else b"")
    return (header + suffix + element + array + subobject_bytes + guard + text + source + check + root + guard_text
        + bytes(bindings_offset-guard_text_offset-len(guard_text)) + binding_bytes + input_bytes
        + bytes(guard_input_offset-input_offset-len(input_bytes)) + guard_input
        + bytes(output_offset-guard_input_offset-len(guard_input)) + bytes(output_bytes))


def evaluate_element(client: NativeBridge, source_expression: str, index: int,
                     expression: str, bindings: Iterable[ObjectBinding] = (), *,
                     world: int, condition: str = "", expected_count: int | None = None,
                     pointer_array: PointerArraySource | None = None,
                     output_bytes: int = 4096,
                     adapter: str | int = "pdx_v3_data.dll",
                     process: RemoteProcess | None = None) -> DataQuery:
    """Produce an original Creation list and copy one element getter in one callback.

    source_expression runs afresh; no old list descriptor/element is reused. world
    must be captured BEFORE queueing production, never stamped onto an old owner.
    Retained source bindings need their original nonzero world provenance; raw
    wrapper-pointer bindings are not accepted by this guarded path. Full-ID and
    genuine retained owning bindings remain supported. An old zero-world owner
    must not be upgraded by passing today's world.

    Original provider BindingScope handles raw modes0/1 without Any-copy/retag.
    These borrowed modes reject parameter expressions: Prepare argument getters
    could rebuild the source. Native function names are not allowlisted. Returns
    only copied empty/bool/numeric/string values and NO value_handle/raw pointer.
    Optional condition must be a successful original native BOOL getter on the
    same element; false returns ERROR_CANCELLED(1223), without consumer invocation.
    expected_count detects count changes only; index/count are NOT stable identity.
    Caller must prove source/selection identity and avoid reentrant source mutation.
    pointer_array can provide the actual source context from one original native
    pointer-element array in this same owner-thread callback. Its source_type_id
    must match the original source expression's required type. The original fixed
    array ABI is read under the native gate; no Creation provider is synthesized.
    """
    initial = _element_request(source_expression, index, expression, bindings,
        world=world, input=None, condition=condition,
        expected_count=expected_count, output_bytes=output_bytes, pointer_array=pointer_array)
    return _submit(client, initial, adapter=adapter, process=process,
                   decode=ElementResult.from_bytes)


def invoke_element_action(client: NativeBridge, source_expression: str, index: int,
                          expression: str, input: TypedInput,
                          bindings: Iterable[ObjectBinding] = (), *,
                          world: int, condition: str = "",
                          expected_count: int | None = None,
                          pointer_array: PointerArraySource | None = None, output_bytes: int = 4096,
                          adapter: str | int = "pdx_v3_data.dll",
                          process: RemoteProcess | None = None) -> DataQuery:
    """Explicit original action on a freshly produced element; no borrowed escape.

    Same source/world/borrowed-argument rules as evaluate_element(). Retained
    input values also need their original nonzero world provenance; use explicit
    immediate TypedInput for ordinary scalars/strings. All source, binding,
    condition, compile and prepare failures stop BEFORE the action. No
    native business names are allowlisted. A nonempty condition is strongly
    recommended for native enabled checks. This does not invent an element ID.
    Selection must use an independently proven native identity contract, not a
    stale filtered-list position. invoked/accepted are NOT resolution/effect proof.
    After invocation an error/truncated copy may follow partial effects: retain
    the exact query/receipt, inspect actual state, and NEVER replay automatically.
    There are no result values to release after definite callback completion.
    """
    if not isinstance(input, TypedInput):
        raise TypeError("input must be an explicit native TypedInput")
    initial = _element_request(source_expression, index, expression, bindings,
        world=world, input=input, condition=condition,
        expected_count=expected_count, output_bytes=output_bytes, pointer_array=pointer_array)
    return _submit(client, initial, adapter=adapter, process=process,
                   decode=ElementResult.from_bytes)



def invoke_source_index_action(client: NativeBridge, source_expression: str, index: int,
                               expression: str, bindings: Iterable[ObjectBinding] = (), *,
                               condition: str = "", output_bytes: int = 4096,
                               adapter: str | int = "pdx_v3_data.dll",
                               process: RemoteProcess | None = None) -> DataQuery:
    """Select a current source ordinal and pass it to its original root action.

    The original Creation, count, provider binding, optional element condition
    and action share ONE Interface/Access callback. Native code derives I32 from
    the selected ordinal, not a separate caller input. This supports GUI root
    setters whose native index relies on the corresponding original collection.
    Supply the matching source/action contract. An ordinal is not a stable ID.
    The world is observed in that callback, including an application's world0;
    external raw or legacy unknown-world retained roots are not admitted.
    Raw provider argument restrictions still apply. The selected Binding ends
    before root invocation; the original source/root graph stays alive. Actions
    must not delete that backing owner or invalidate its Creation cleanup.
    No pointer is retagged. Returns the existing ElementResult, including original error/invoked/accepted
    and partial post-input bytes. No effect, persistence or rollback is implied.
    Pending/ambiguous results retain the original query; never replay an action.
    Capability: PdxV3DataInvokeSourceIndexInterface. No unsafe old-DLL fallback.
    """
    initial = _element_request(source_expression, index, expression, bindings,
        world=0, input=TypedInput.i32(0), condition=condition, expected_count=None,
        output_bytes=output_bytes, source_index_action=True)
    return _submit(client, initial, adapter=adapter, process=process,
        decode=ElementResult.from_bytes, callback_symbol="PdxV3DataInvokeSourceIndexInterface")

def pointer_array_count(client: NativeBridge, source: PointerArraySource, *,
                        world: int, bindings: Iterable[ObjectBinding] = (),
                        adapter: str | int = "pdx_v3_data.dll",
                        process: RemoteProcess | None = None) -> DataQuery:
    """Inspect one fresh original pointer-array header in the native owner callback.

    No element, provider, getter/action consumer or pointer is returned. count and
    source_stages distinguish a genuinely observed empty array from root failure.
    Source owner-thread/gate/no-reentry rules are those of PointerArraySource.
    A raw Self value retained from an earlier callback is never used as a lease.
    """
    initial = _element_request("", 0, "", bindings, world=world, input=None,
        condition="", expected_count=None, output_bytes=0,
        pointer_array=source, count_only=True)
    return _submit(client, initial, adapter=adapter, process=process,
                   decode=ElementResult.from_bytes)


def evaluate_pointer_array(client: NativeBridge, source: PointerArraySource,
                           expression: str, bindings: Iterable[ObjectBinding] = (), *,
                           world: int, condition: str = "", output_bytes: int = 4096,
                           adapter: str | int = "pdx_v3_data.dll",
                           process: RemoteProcess | None = None) -> DataQuery:
    """Copy a native getter on the original array element, within its owner scope.

    This binds the actual element through its original dynamic type converter.
    It does not construct an event, fabricate a Creation provider, publish the
    element pointer, retain a borrowed context for later use, or submit an action.
    Only copied primitives/string/empty values leave the original callback/gate.
    All owner-thread/no-reentry/native-ABI rules of PointerArraySource apply.
    """
    initial = _element_request("", 0, expression, bindings, world=world, input=None,
        condition=condition, expected_count=None, output_bytes=output_bytes,
        pointer_array=source, direct_context=True)
    return _submit(client, initial, adapter=adapter, process=process,
                   decode=ElementResult.from_bytes)
