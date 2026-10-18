"""Native V3 script parsing/evaluation through the existing Interface callback.

This is not console text input. Script text enters the game's native parser with
an explicit CScopeObjectReference, then native Trigger/Effect runners execute it.
The carrier and pdx_v3_script.dll must already be loaded. Low-level native
parse/scope/context/evaluate/execute/release exports remain public and compose
with native_call/v3_session; callback placement alone does not grant access.
"""
from __future__ import annotations

from pdx_runtime.v3_wire import resolve_v3_export
from dataclasses import dataclass
from enum import IntEnum
from decimal import Decimal
import struct
from typing import Iterable

from . import v3, v3_access
from .native_bridge import BridgeError, NativeBridge, RemoteProcess

SCOPE = struct.Struct("<H3HQ")
RESULT = struct.Struct("<4IH3HQ8I512s")
OWNER = struct.Struct("<4IQ2I512s")
CONTEXT_BYTES = 320
VALUE_EXTENSION = struct.Struct("<IIQQQII")
VALUE_RESULT_BYTES = RESULT.size + VALUE_EXTENSION.size
SAVED_SCOPE = struct.Struct("<IIH3HQ")
SCOPE_TYPE_ROW = struct.Struct("<IIi5IQII256s256s")
SCOPE_TYPES_RESULT = struct.Struct("<12I2Q4I")
FIXED_SCALE = 100000  # Native decimal converter2253998, not a guessed display scale.


class ScriptKind(IntEnum):
    TRIGGER = 1
    EFFECT = 2
    VALUE = 3
    TYPED_VALUE = 4


class ScopeType(IntEnum):
    COUNTRY = 5
    BUILDING = 8
    POP = 0x3F
    STATE = 0x4C


@dataclass(frozen=True)
class Scope:
    """Native script reference; not a GUI DataFactory/CPdxData type ID.

    ``raw`` keeps every native scope type/payload accessible. For ID-based game
    objects prefer ``id``; the complete uint32 ID is zero-extended into payload.
    Native validity is checked inside the callback, never inferred from a pointer.
    """
    type: int
    payload: int

    def __post_init__(self):
        if not 0 <= self.type <= 0xFFFF:
            raise ValueError("scope type must be uint16")
        if not 0 <= self.payload <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("scope payload must be uint64")

    @classmethod
    def id(cls, type: int, full_id: int) -> Scope:
        if not 0 <= full_id <= 0xFFFFFFFF:
            raise ValueError("full_id must be the complete uint32 database ID")
        return cls(type, full_id)

    @classmethod
    def raw(cls, type: int, payload: int) -> Scope:
        return cls(type, payload)

    def to_bytes(self) -> bytes:
        return SCOPE.pack(self.type, 0, 0, 0, self.payload)


@dataclass(frozen=True)
class ScriptResult:
    size: int
    error: int
    kind: int
    text_bytes: int
    scope: Scope
    scope_valid: int
    parsed: int
    executed: int
    raw_boolean: int
    execution_thread_id: int
    diagnostic_bytes: int
    diagnostic_truncated: int
    diagnostic_raw: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> ScriptResult:
        fields = RESULT.unpack_from(data)
        size, error, kind, text_bytes, type, _p1, _p2, _p3, payload = fields[:9]
        valid, parsed, executed, raw_bool, thread, count, truncated, _reserved, diagnostic = fields[9:]
        if count > len(diagnostic):
            raise ValueError("native diagnostic length exceeds result buffer")
        return cls(size, error, kind, text_bytes, Scope(type, payload), valid,
                   parsed, executed, raw_bool, thread, count, truncated, diagnostic[:count])

    @property
    def boolean(self) -> bool | None:
        """Native AL result for a successfully evaluated Trigger; else None."""
        if self.error or not self.executed or self.kind != ScriptKind.TRIGGER:
            return None
        return bool(self.raw_boolean)

    @property
    def diagnostic(self) -> str:
        return self.diagnostic_raw.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class SavedScope:
    """A native interned name ID and an actual script type/payload value.

    Names can be obtained through public PdxV3ScriptInternScopeName. This retains
    the game's interned-name primitive rather than inventing an external catalog.
    """
    name: int
    value: Scope

    def to_bytes(self) -> bytes:
        if not 0 <= self.name <= 0xFFFFFFFF:
            raise ValueError("saved scope name must be the native uint32 interned ID")
        return SAVED_SCOPE.pack(self.name, 0, self.value.type, 0, 0, 0, self.value.payload)


@dataclass(frozen=True)
class TriggerValue:
    raw_boolean: int

    @property
    def boolean(self) -> bool:
        return bool(self.raw_boolean)


@dataclass(frozen=True)
class FixedValue:
    bits: int

    @property
    def signed(self) -> int:
        return struct.unpack("<q", struct.pack("<Q", self.bits))[0]

    @property
    def decimal(self) -> Decimal:
        return Decimal(self.signed) / FIXED_SCALE


@dataclass(frozen=True)
class ValueResult:
    script: ScriptResult
    value_type: int
    value_reserved: int
    value_payload: int
    context: int
    saved_scopes: int
    saved_count: int
    text_offset: int

    @classmethod
    def from_bytes(cls, data: bytes) -> ValueResult:
        return cls(ScriptResult.from_bytes(data), *VALUE_EXTENSION.unpack_from(data, RESULT.size))

    @property
    def error(self) -> int:
        return self.script.error

    @property
    def raw(self) -> Scope:
        """Exact native typed16 value; no object pointer or string guess."""
        return Scope.raw(self.value_type, self.value_payload)

    @property
    def value(self):
        if self.error or not self.script.executed:
            return None
        if self.value_type == 1:
            return FixedValue(self.value_payload)
        if self.value_type == 2:
            return bool(self.value_payload)
        # Native flags/object references stay typed raw values, not fabricated
        # Python objects or pointers that outlive an unloaded world.
        return self.raw


def encode_value_request(text: str, scope: Scope, *, kind: ScriptKind,
                         saved_scopes: Iterable[SavedScope] = (), context: int = 0,
                         request_address: int = 0) -> bytes:
    """Pack numeric owner+named scopes+text in one callback-owned allocation."""
    kind = ScriptKind(kind)
    if kind not in (ScriptKind.VALUE, ScriptKind.TYPED_VALUE):
        raise ValueError("encode_value_request requires a native numeric/typed kind")
    encoded = text.encode("utf-8")
    if b"\0" in encoded or len(encoded) > 0x7FFFFFF5:
        raise ValueError("script value must be NUL-free and fit the native signed byte-count")
    if not 0 <= context <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("context must be a current native process address")
    saved = tuple(saved_scopes)
    saved_bytes = b"".join(item.to_bytes() for item in saved)
    text_offset = VALUE_RESULT_BYTES + len(saved_bytes)
    prefix = RESULT.pack(VALUE_RESULT_BYTES, v3.ERROR_IO_PENDING, kind, len(encoded),
        scope.type, 0, 0, 0, scope.payload, *([0] * 8), bytes(512))
    extension = VALUE_EXTENSION.pack(0, 0, 0, context,
        request_address + VALUE_RESULT_BYTES if saved else 0, len(saved), text_offset)
    return prefix + extension + saved_bytes + encoded + b"\0"


def submit(client: NativeBridge, text: str, scope: Scope, *, kind: ScriptKind,
           adapter: str | int = "pdx_v3_script.dll",
           process: RemoteProcess | None = None,
           saved_scopes: Iterable[SavedScope] = (), context: int = 0) -> v3.CallbackResult:
    """Parse/run/release once through the natural Interface runner path; never wait.

    The native Effect runner takes its own analyzed logic (2,1) guard. Text and
    result share callback-owned storage. ``read`` returns None until completion,
    then copies the result and releases storage. Transport-unknown delivery keeps
    exception.query for recovery. Effects are never retried or replayed.
    """
    kind = ScriptKind(kind)
    if kind in (ScriptKind.VALUE, ScriptKind.TYPED_VALUE):
        saved_scopes = tuple(saved_scopes)
        initial = encode_value_request(text, scope, kind=kind, saved_scopes=saved_scopes, context=context)
        def prepare(client, process, callback, address, owns):
            process.write(address, encode_value_request(text, scope, kind=kind,
                saved_scopes=saved_scopes, context=context, request_address=address))
            return v3.CallbackResult(client, process, callback, address, owns, len(initial), ValueResult.from_bytes)
        handle = v3.submit_callback(client, initial, adapter=adapter,
            callback_symbol="PdxV3ScriptValueInterface", decode_result=ValueResult.from_bytes,
            process=process, handle_factory=prepare)
        handle.text_address = handle.result_address + VALUE_RESULT_BYTES + len(saved_scopes) * SAVED_SCOPE.size
    else:
        if context or tuple(saved_scopes):
            raise ValueError("saved_scopes/context currently belong to numeric/typed evaluation")
        encoded = text.encode("utf-8")
        if b"\0" in encoded:
            raise ValueError("script text must not contain an embedded NUL")
        if len(encoded) > 0x7FFFFFFE:
            raise ValueError("script exceeds the native parser's signed byte-count limit")
        initial = RESULT.pack(RESULT.size, v3.ERROR_IO_PENDING, kind, len(encoded),
            scope.type, 0, 0, 0, scope.payload, *([0] * 8), bytes(512)) + encoded + b"\0"
        handle = v3.submit_callback(client, initial, adapter=adapter,
            callback_symbol="PdxV3ScriptInterface", decode_result=ScriptResult.from_bytes,
            process=process)
        handle.text_address = handle.result_address + RESULT.size
    handle.scope = scope
    handle.kind = kind
    handle.text = text
    return handle


def trigger(client: NativeBridge, text: str, scope: Scope, **kwargs):
    """Evaluate a native Trigger block; preserves raw AL and boolean result."""
    return submit(client, text, scope, kind=ScriptKind.TRIGGER, **kwargs)


def effect(client: NativeBridge, text: str, scope: Scope, **kwargs):
    """Execute a native Effect block once; independently verify its world change."""
    return submit(client, text, scope, kind=ScriptKind.EFFECT, **kwargs)


def value(client: NativeBridge, text: str, scope: Scope, **kwargs):
    """Evaluate a native numeric ScriptValue; preserves CFixedPoint raw64.

    A nonzero context explicitly borrows an already-constructed native context.
    Supplied saved_scopes are inserted into it and modify its named scope map.
    The caller retains that context through completion; this helper does not copy
    or release it. With context=0, insertions belong to the temporary owned context.
    """
    return submit(client, text, scope, kind=ScriptKind.VALUE, **kwargs)


def typed_value(client: NativeBridge, text: str, scope: Scope, **kwargs):
    """Evaluate a native event-target value; returns its exact type/payload.

    Borrowed context and saved_scopes have the same explicit mutation/lifetime
    contract as value(): named scopes are inserted, not copied or rolled back.
    """
    return submit(client, text, scope, kind=ScriptKind.TYPED_VALUE, **kwargs)


@dataclass(frozen=True)
class OwnerInfo:
    size: int
    error: int
    kind: int
    scope_type: int
    object: int
    diagnostic_bytes: int
    diagnostic_truncated: int
    diagnostic_raw: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> OwnerInfo:
        fields = OWNER.unpack(data)
        if fields[5] > 512:
            raise ValueError("native diagnostic exceeds owner buffer")
        return cls(*fields[:-1], fields[-1][:fields[5]])


@dataclass(frozen=True)
class NativeOperationResult:
    error: int
    value: object
    access: object


class NativeOperation:
    """One existing guarded raw-call handle plus explicit native-owner lifetimes.

    ``query`` is the unchanged v3_access/CallbackResult handle. Its recover method
    binds an inspected Interface request ID after unknown transport; no resubmit.
    All addresses and owners remain public. No destructor dispatches remotely.
    """
    def __init__(self, process, owners, finish, temporary=(), owns_process=False,
                 release_only_on_success=False):
        self.process = process
        self.owners = tuple(owners)
        self.owner = self.owners[0] if self.owners else None
        self.query = None
        self.finish = finish
        self.temporary = tuple(temporary)
        self._owns_process = owns_process
        self._release_only_on_success = release_only_on_success
        self._access = None
        self._result = None
        self._freed = set()
        self._closed = False
        self._settled = False

    def read(self) -> NativeOperationResult | None:
        if self._access is None:
            if self.query is None:
                raise RuntimeError("submission has no recoverable callback handle")
            try:
                self._access = self.query.read()
            except Exception as error:
                error.native_operation = self
                raise
            if self._access is None:
                return None
        callee = self._access.callee_result
        if self._access.error or callee is None or callee.error:
            # A native target that did not return has unknown ownership effects.
            # Keep every native owner/input live; caller can inspect raw addresses.
            error = BridgeError("native Script operation", self._access.error or
                                (callee.error if callee is not None else 0))
            error.native_operation = self
            raise error
        if self._result is None:
            self._result = NativeOperationResult(callee.rax, self.finish(callee.rax), self._access)
        if not self._settled:
            for owner in self.owners:
                if owner.pending is self:
                    owner.pending = None
            self._settled = True
        release_storage = not self._release_only_on_success or self._result.error == 0
        for address in self.temporary if release_storage else ():
            if address not in self._freed:
                self.process.free(address)
                self._freed.add(address)
        if release_storage and self._owns_process and not self._closed:
            self.process.close()
            self._closed = True
        return self._result


def _guarded_operation(client, process, symbol, arguments, *, adapter,
                       owners=(), finish=lambda error: None, temporary=(),
                       owns_process=False, release_only_on_success=False,
                       cleanup_on_prequeue=True) -> NativeOperation:
    owners = tuple(dict.fromkeys(owners))
    for owner in owners:
        owner._available()
    operation = NativeOperation(process, owners, finish, temporary, owns_process,
                                release_only_on_success)
    for owner in owners:
        owner.pending = operation
    try:
        target = resolve_v3_export(process, adapter, symbol)
        operation.query = v3_access.submit_call(client, target, arguments,
            mode=1, flag=0, dispatch="interface", process=process)
    except Exception as error:
        if hasattr(error, "query"):
            operation.query = error.query
            error.native_operation = operation
        else:
            for owner in owners:
                if owner.pending is operation:
                    owner.pending = None
            if cleanup_on_prequeue:
                for address in temporary:
                    process.free(address)
                if owns_process:
                    process.close()
        raise
    return operation


class NativeContext:
    """Explicit caller-owned native TopScope320B, no shallow copy or auto release.

    Create it with create_context(). A pending/unknown operation blocks competing
    use or release. ``address``/``process`` stay public for raw composition. Saved
    insertion intentionally changes its native named map and is not rolled back.
    """
    def __init__(self, client, process, address, scope, adapter, owns_process):
        self.client, self.process, self.address = client, process, address
        self.scope, self.adapter = scope, adapter
        self._owns_process = owns_process
        self.pending = None
        self.constructed = False
        self.released = False

    def _available(self):
        if self.released:
            raise RuntimeError("native context storage was released")
        if self.pending is not None:
            raise RuntimeError("native context has a pending or unknown operation")

    def _live(self):
        self._available()
        if not self.constructed:
            raise RuntimeError("native context is not constructed")

    def set_saved(self, saved: SavedScope) -> NativeOperation:
        self._live()
        saved.to_bytes()  # validate native uint32 name and scope before allocation
        address = self.process.allocate(SCOPE.size)
        try:
            self.process.write(address, saved.value.to_bytes())
        except Exception:
            self.process.free(address)
            raise
        return _guarded_operation(self.client, self.process, "PdxV3ScriptContextSetSaved",
            (self.address, saved.name, address), adapter=self.adapter, owners=(self,),
            temporary=(address,))

    def release(self) -> NativeOperation:
        """Queue original ContextRelease; read completion before process-buffer free."""
        self._live()
        def finished(error):
            if not error:
                self.constructed = False
                self.released = True
            return None
        return _guarded_operation(self.client, self.process, "PdxV3ScriptContextRelease",
            (self.address,), adapter=self.adapter, owners=(self,), finish=finished,
            temporary=(self.address,), owns_process=self._owns_process,
            release_only_on_success=True, cleanup_on_prequeue=False)

    def discard_unconstructed(self) -> None:
        """Free storage only after a definitely completed failed ContextRoot."""
        self._available()
        if self.constructed:
            raise RuntimeError("constructed context requires native ContextRelease")
        self.process.free(self.address)
        self.released = True
        if self._owns_process:
            self.process.close()


class ParsedValue:
    """One native compiled Trigger/numeric/typed owner; parse once, evaluate many times.

    Owner wire and compiled engine value have distinct lifetimes. Release queues
    the native destructor, then frees the owner wire only after definite success.
    Addresses are current-process/world values; no byte copy clones native owners.
    """
    def __init__(self, client, process, address, scope, kind, adapter, owns_process):
        self.client, self.process, self.address = client, process, address
        self.scope, self.kind, self.adapter = scope, kind, adapter
        self._owns_process = owns_process
        self.pending = None
        self.info = None
        self.released = False

    def _available(self):
        if self.released:
            raise RuntimeError("parsed native owner storage was released")
        if self.pending is not None:
            raise RuntimeError("parsed native owner has a pending or unknown operation")

    def evaluate(self, context: NativeContext) -> NativeOperation:
        self._available()
        context._live()
        if self.info is None or self.info.error or not self.info.object:
            raise RuntimeError("native script value has no successfully parsed owner")
        if context.client.pid != self.client.pid:
            raise ValueError("native context and parsed value belong to different processes")
        size = {ScriptKind.TRIGGER: 4, ScriptKind.VALUE: 8, ScriptKind.TYPED_VALUE: SCOPE.size}[self.kind]
        out = self.process.allocate(size)
        symbol = {ScriptKind.TRIGGER: "PdxV3ScriptEvaluate", ScriptKind.VALUE: "PdxV3ScriptEvaluateValue",
                  ScriptKind.TYPED_VALUE: "PdxV3ScriptEvaluateTyped"}[self.kind]
        def finished(error):
            if error:
                return None
            data = self.process.read(out, size)
            if self.kind == ScriptKind.TRIGGER:
                return TriggerValue(struct.unpack("<I", data)[0])
            if self.kind == ScriptKind.VALUE:
                return FixedValue(struct.unpack("<Q", data)[0])
            type, _p1, _p2, _p3, payload = SCOPE.unpack(data)
            return Scope.raw(type, payload)
        return _guarded_operation(self.client, self.process, symbol,
            (self.address, context.address, out), adapter=self.adapter,
            owners=(self, context), finish=finished, temporary=(out,))

    def release(self) -> NativeOperation:
        """Release once, including a completed parse failure's empty owner wire."""
        self._available()
        if self.info is None:
            raise RuntimeError("parse outcome must be completed before owner release")
        def finished(error):
            if not error:
                self.info = OwnerInfo.from_bytes(self.process.read(self.address, OWNER.size))
                if self.info.object:
                    raise RuntimeError("native release returned without clearing compiled owner")
                self.released = True
            return None
        return _guarded_operation(self.client, self.process, "PdxV3ScriptRelease",
            (self.address,), adapter=self.adapter, owners=(self,), finish=finished,
            temporary=(self.address,), owns_process=self._owns_process,
            release_only_on_success=True, cleanup_on_prequeue=False)


def create_context(client: NativeBridge, scope: Scope, *,
                   adapter: str | int = "pdx_v3_script.dll",
                   process: RemoteProcess | None = None) -> NativeOperation:
    """Native ContextRoot constructs320B once; returns operation.owner immediately."""
    owns_process = process is None
    process = process or RemoteProcess(client.pid)
    address = None
    try:
        address = process.allocate(CONTEXT_BYTES + SCOPE.size)
        if address % 16:
            raise ValueError("native context allocation must be16-byte aligned")
        process.write(address + CONTEXT_BYTES, scope.to_bytes())
        owner = NativeContext(client, process, address, scope, adapter, owns_process)
        def finished(error):
            owner.constructed = not error
            return owner
        return _guarded_operation(client, process, "PdxV3ScriptContextRoot",
            (address + CONTEXT_BYTES, address), adapter=adapter, owners=(owner,), finish=finished)
    except Exception as error:
        if not hasattr(error, "native_operation"):
            if address is not None:
                process.free(address)
            if owns_process:
                process.close()
        raise


def parse(client: NativeBridge, text: str, scope: Scope, *, kind: ScriptKind = ScriptKind.VALUE,
          adapter: str | int = "pdx_v3_script.dll",
          process: RemoteProcess | None = None) -> NativeOperation:
    """Parse a native Trigger/numeric/typed value once; returns operation.owner.

    ParsedValue keeps its established name for compatibility. Effect ownership and
    raw Execute remain public, but this helper does not pretend Interface(1,0)
    supplies the natural Effect runner's logic scope/logging policy.
    """
    kind = ScriptKind(kind)
    if kind not in (ScriptKind.TRIGGER, ScriptKind.VALUE, ScriptKind.TYPED_VALUE):
        raise ValueError("retained ParsedValue supports Trigger/numeric/typed; Effect remains raw")
    encoded = text.encode("utf-8")
    if b"\0" in encoded or len(encoded) > 0x7FFFFFF5:
        raise ValueError("native script value must be NUL-free and fit its signed byte-count")
    owns_process = process is None
    process = process or RemoteProcess(client.pid)
    address = None
    try:
        address = process.allocate(OWNER.size + len(encoded) + 1)
        process.write(address, bytes(OWNER.size) + encoded + b"\0")
        owner = ParsedValue(client, process, address, scope, kind, adapter, owns_process)
        def finished(error):
            owner.info = OwnerInfo.from_bytes(process.read(address, OWNER.size))
            return owner
        return _guarded_operation(client, process, "PdxV3ScriptParse",
            (kind, address + OWNER.size, len(encoded), scope.type, address),
            adapter=adapter, owners=(owner,), finish=finished)
    except Exception as error:
        if not hasattr(error, "native_operation"):
            if address is not None:
                process.free(address)
            if owns_process:
                process.close()
        raise


def intern_name(client: NativeBridge, name: str, *, adapter: str | int = "pdx_v3_script.dll",
                process: RemoteProcess | None = None) -> NativeOperation:
    """Return an actual native name ID through one explicit guarded raw call."""
    encoded = name.encode("utf-8")
    if not encoded or b"\0" in encoded:
        raise ValueError("native scope name must be nonempty and NUL-free")
    owns_process = process is None
    process = process or RemoteProcess(client.pid)
    address = None
    try:
        address = process.allocate(len(encoded) + 1 + 4)
        process.write(address, encoded + b"\0" + bytes(4))
    except Exception:
        if address is not None:
            process.free(address)
        if owns_process:
            process.close()
        raise
    output = address + len(encoded) + 1
    return _guarded_operation(client, process, "PdxV3ScriptInternScopeName",
        (address, len(encoded), output), adapter=adapter,
        finish=lambda error: None if error else struct.unpack("<I", process.read(output, 4))[0],
        temporary=(address,), owns_process=owns_process)


@dataclass(frozen=True)
class ScopeTypeInfo:
    type: int
    name_token: int
    save_token: int
    native_flags: int
    name_bytes: int
    name_required: int
    save_name_bytes: int
    save_name_required: int
    native_entry: int
    stores_variables: bool
    name_raw: bytes
    save_name_raw: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> ScopeTypeInfo:
        f = SCOPE_TYPE_ROW.unpack(data)
        if f[4] > 255 or f[6] > 255:
            raise ValueError("native scope-type name length exceeds copied buffer")
        return cls(*f[:9], bool(f[9]), f[11][:f[4]], f[12][:f[6]])

    @property
    def name(self) -> str:
        return self.name_raw.decode("utf-8", errors="replace")

    @property
    def save_name(self) -> str:
        return self.save_name_raw.decode("utf-8", errors="replace")

    @property
    def names_complete(self) -> bool:
        return self.name_bytes == self.name_required and self.save_name_bytes == self.save_name_required

    @property
    def evaluates_triggers(self) -> bool:
        return bool(self.native_flags & 1)

    @property
    def executes_effects(self) -> bool:
        return bool(self.native_flags & 2)

    @property
    def changes_scopes(self) -> bool:
        return bool(self.native_flags & 4)

    def reference(self, payload: int) -> Scope:
        """Construct an exact raw native reference; payload ownership stays caller's."""
        return Scope.raw(self.type, payload)


@dataclass(frozen=True)
class ScopeTypesResult:
    size: int
    error: int
    action: int
    start: int
    capacity: int
    count: int
    next: int
    total: int
    input_name_bytes: int
    resolved_type: int
    matched: int
    reserved: int
    input_name: int
    output_rows: int
    row_size: int
    execution_thread_id: int
    output_offset: int
    output_bytes: int
    rows: tuple[ScopeTypeInfo, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> ScopeTypesResult:
        fields = SCOPE_TYPES_RESULT.unpack_from(data)
        count, row_size, offset, output_bytes = fields[5], fields[14], fields[16], fields[17]
        if row_size not in (0, SCOPE_TYPE_ROW.size) or count > fields[4]:
            raise ValueError("invalid native scope-type row count/size")
        size = count * SCOPE_TYPE_ROW.size
        if size and (offset < SCOPE_TYPES_RESULT.size or size > output_bytes or offset + size > len(data)):
            raise ValueError("native scope-type rows exceed owned callback storage")
        rows = tuple(ScopeTypeInfo.from_bytes(data[offset+i*SCOPE_TYPE_ROW.size:
            offset+(i+1)*SCOPE_TYPE_ROW.size]) for i in range(count))
        return cls(*fields, rows)

    @property
    def type(self) -> int | None:
        """Resolved native Script scope ID, never a DataFactory dynamic type ID."""
        return self.resolved_type if not self.error and self.matched else None


def _scope_types(client, *, action, start=0, capacity=16, name=b"",
                 adapter="pdx_v3_script.dll", process=None):
    if not 0 <= start <= 0xFFFFFFFF or not 0 <= capacity <= 65536:
        raise ValueError("start must be uint32 and capacity must fit the native scope-ID range")
    output_offset = SCOPE_TYPES_RESULT.size
    output_bytes = capacity * SCOPE_TYPE_ROW.size
    name_offset = output_offset + output_bytes
    def encode(address):
        return SCOPE_TYPES_RESULT.pack(SCOPE_TYPES_RESULT.size, v3.ERROR_IO_PENDING,
            action, start, capacity, 0, start, 0, len(name), 0, 0, 0,
            address+name_offset if action == 2 else 0,
            address+output_offset if capacity else 0,
            SCOPE_TYPE_ROW.size, 0, output_offset, output_bytes)
    initial = encode(0) + bytes(output_bytes) + name + (b"\0" if action == 2 else b"")
    def prepare(client, process, callback, address, owns):
        process.write(address, encode(address) + initial[SCOPE_TYPES_RESULT.size:])
        return v3.CallbackResult(client, process, callback, address, owns, len(initial),
                                 ScopeTypesResult.from_bytes)
    return v3.submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3ScriptScopeTypesInterface", decode_result=ScopeTypesResult.from_bytes,
        process=process, handle_factory=prepare)


def enumerate_scope_types(client: NativeBridge, *, start: int = 0, capacity: int = 16,
                          adapter: str | int = "pdx_v3_script.dll",
                          process: RemoteProcess | None = None) -> v3.CallbackResult:
    """Page the live original Script scope registry; no static allowlist or loop.

    Result.next is the next entry index; caller explicitly requests the next page.
    Native names and copied byte-count/required-count preserve truncation. Metadata
    flags are the same fields reported by native32b4080, not invented permissions.
    """
    return _scope_types(client, action=1, start=start, capacity=capacity,
                        adapter=adapter, process=process)


def resolve_scope_type(client: NativeBridge, name: str, *,
                       adapter: str | int = "pdx_v3_script.dll",
                       process: RemoteProcess | None = None) -> v3.CallbackResult:
    """Resolve a complete exact native Script scope name into result.type/row.

    This compares original runtime CString bytes, not a truncated displayed prefix
    or a DataFactory type ID. No name is interned/registered by this read-only query.
    """
    encoded = name.encode("utf-8")
    if b"\0" in encoded or len(encoded) > 0xFFFFFFFF:
        raise ValueError("scope type name must be NUL-free and fit uint32 byte count")
    return _scope_types(client, action=2, capacity=1, name=encoded,
                        adapter=adapter, process=process)
