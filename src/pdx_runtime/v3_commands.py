"""V3 registered commands through native factory/CPersistent I/O/Session.

No common command field layout is invented. Typed values are native CBinLexer
wire values, not an agent-tool list or a DSL. Deserialization uses the native
CReader and each command's own parameter schema, resolver and allocator. The
adapter exposes no Reader total-parse or required-field status. Existing raw
call/process/Interface/Session primitives remain public.
"""
from __future__ import annotations
from pdx_runtime.v3_wire import resolve_v3_export
from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import Mapping

from . import v3, v3_session
from .native_bridge import NativeBridge, RemoteProcess

RESULT = struct.Struct("<4I2Q8I3Q")
REGISTRATION = struct.Struct("<2IQ128s")
ADAPTER = "pdx_v3_commands.dll"
REGISTERED_CAPACITY = 239  # unique concrete startup factory registrations, not RTTI candidates
VALIDATION_REASON_CAPACITY = 4096

class Action(IntEnum):
    ENUMERATE = 1
    CREATE = 2
    DESERIALIZE = 3
    SERIALIZE = 4
    VALIDATE = 5
    DESTROY = 6
    SUBMIT = 7
    CLONE = 8
    TOKEN_NAME = 9
    RESOLVE_TOKEN = 10

@dataclass(frozen=True)
class Token:
    """A native token ID, distinct from a typed numeric field value."""
    value: int

@dataclass(frozen=True)
class ResolvedToken(Token):
    """Numeric native token plus an explicitly queried name for readable trees.

    Encoding retains the original numeric token ID. This is not an escaped
    NamedToken and does not query, intern or register anything on its own.
    """
    name: str

@dataclass(frozen=True)
class NamedToken:
    """An escaped native token name (binary marker23), not a string field."""
    value: str

@dataclass(frozen=True)
class UInt32:
    value: int

@dataclass(frozen=True)
class Int64:
    value: int

@dataclass(frozen=True)
class UInt64:
    value: int

@dataclass(frozen=True)
class Float32:
    value: float

@dataclass(frozen=True)
class Assignment:
    key: Token | NamedToken
    value: object

@dataclass(frozen=True)
class Registration:
    type_token: int
    factory_index: int
    factory: int
    token_name: str

@dataclass(frozen=True)
class CommandResult:
    """Decoded adapter receipt, not proof of domain success.

    error=0 and a nonnull command do not independently establish native CReader
    total parsing or required-field correctness. No such Reader status is exposed.
    validated and accepted report their native calls, not independently seen effects.
    """
    size: int
    error: int
    action: int
    type_token: int
    command: int
    buffer: int
    buffer_capacity: int
    buffer_size: int
    count: int
    submit_flags: int
    validated: int
    accepted: int
    execution_thread_id: int
    reserved: int
    application: int
    world: int
    session: int
    data: bytes = b""
    registrations: tuple[Registration, ...] = ()
    reason: str = ""
    cloned_command: int = 0
    token_name: str = ""

    @property
    def token_name_truncated(self) -> bool:
        return self.action == Action.TOKEN_NAME and self.buffer_size > self.buffer_capacity

    @property
    def reason_truncated(self) -> bool:
        return self.action == Action.VALIDATE and self.buffer_size > self.buffer_capacity

    @classmethod
    def from_bytes(cls, data: bytes) -> CommandResult:
        values = RESULT.unpack_from(data)
        size, error, action = values[:3]
        if size != RESULT.size:
            raise ValueError("native command result layout mismatch")
        buffer_size, count = values[7], values[8]
        payload = bytes(data[RESULT.size:])
        entries = ()
        reason = ""
        cloned_command = 0
        token_name = ""
        if action == Action.ENUMERATE and not error:
            if count * REGISTRATION.size > len(payload):
                raise ValueError("native registrations exceed owned result buffer")
            entries = tuple(Registration(token, index, factory,
                name.split(b"\0", 1)[0].decode("utf-8")) for token, index, factory, name
                in REGISTRATION.iter_unpack(payload[:count * REGISTRATION.size]))
        elif action == Action.SERIALIZE and not error:
            if buffer_size > len(payload):
                raise ValueError("native serialization exceeds owned result buffer")
            payload = payload[:buffer_size]
        elif action == Action.VALIDATE and error in (0, 122):
            copied = min(buffer_size, values[6])
            if copied > len(payload):
                raise ValueError("native validation reason exceeds owned result buffer")
            payload = payload[:copied]
            # A truncated UTF-8 prefix can end inside a code point. Raw data and
            # full native byte count remain available; there is no automatic retry.
            reason = payload.decode("utf-8", errors="replace" if buffer_size > copied else "strict")
        elif action == Action.CLONE and buffer_size:
            if buffer_size != 8 or len(payload) < 8:
                raise ValueError("native cloned command pointer exceeds owned result buffer")
            payload = payload[:8]
            cloned_command = struct.unpack("<Q", payload)[0]
        elif action == Action.TOKEN_NAME and error in (0, 122):
            copied = min(buffer_size, values[6])
            if copied > len(payload):
                raise ValueError("native token name exceeds owned result buffer")
            payload = payload[:copied]
            token_name = payload.decode("utf-8", errors="replace" if buffer_size > copied else "strict")
        return cls(*values, payload, entries, reason, cloned_command, token_name)


def _token(value: int) -> bytes:
    if not 0 <= value <= 0xffff:
        raise ValueError("native token is uint16")
    if value in (12, 13, 14, 15, 19, 20, 23, 359, 668):
        raise ValueError("typed-value markers are not ordinary native tokens")
    return struct.pack("<H", value)


def encode_value(value: object) -> bytes:
    """Encode CBinLexer values without writing native command object fields.

    Plain int is signed32; engine ID fields normally use UInt32. Int64/UInt64,
    Float32 and Token are explicit to retain native wire types. Blocks are lists
    or mappings keyed by Token/int token IDs, including native nested containers.
    """
    if isinstance(value, Token):
        return _token(value.value)
    if isinstance(value, NamedToken):
        raw = value.value.encode("utf-8")
        if len(raw) > 0xffff:
            raise ValueError("native token name exceeds uint16 byte count")
        return struct.pack("<HH", 23, len(raw)) + raw
    if isinstance(value, bool):
        return struct.pack("<HB", 14, value)
    if isinstance(value, UInt32):
        return struct.pack("<HI", 20, value.value)
    if isinstance(value, Int64):
        return struct.pack("<Hq", 359, value.value)
    if isinstance(value, UInt64):
        return struct.pack("<HQ", 668, value.value)
    if isinstance(value, Float32):
        return struct.pack("<Hf", 13, value.value)
    if isinstance(value, int):
        return struct.pack("<Hi", 12, value)
    if isinstance(value, str):
        raw = value.encode("utf-8")
        if len(raw) > 0xffff:
            raise ValueError("native string exceeds uint16 byte count")
        return struct.pack("<HH", 15, len(raw)) + raw
    if isinstance(value, Mapping):
        items = tuple(Assignment(key if isinstance(key, (Token, NamedToken)) else Token(key), item)
                      for key, item in value.items())
        return _token(3) + b"".join(encode_value(item) for item in items) + _token(4)
    if isinstance(value, Assignment):
        return encode_value(value.key) + _token(1) + encode_value(value.value)
    if isinstance(value, (list, tuple)):
        return _token(3) + b"".join(encode_value(item) for item in value) + _token(4)
    raise TypeError(f"unsupported native wire value: {type(value).__name__}")


def encode_command(type_token: int, parameters: Mapping | tuple = ()) -> bytes:
    """Complete native type-token assignment and command-body block."""
    return _token(type_token) + _token(1) + encode_value(parameters)


def encode_tree(command: Assignment) -> bytes:
    """Reverse one decoded or explicitly named native command tree losslessly.

    Numeric ResolvedToken labels keep their original IDs. Real NamedToken nodes
    keep marker23. Scalar widths, block order and duplicate assignments remain
    native wire values; this does not compile an independent command language.
    """
    if not isinstance(command, Assignment) or not isinstance(command.value, tuple):
        raise ValueError("expected one native command assignment/block")
    if not isinstance(command.key, (Token, NamedToken)):
        raise TypeError("native command key must be a numeric or escaped token")
    return encode_value(command)


def token_ids(value: object) -> tuple[int, ...]:
    """Numeric token IDs actually present in a tree, in first-seen order.

    Both parameter keys and enum-value tokens are included; ordinary UInt32/int
    fields are not tokens. The caller can explicitly query their native names
    through token_name() and pass that copied mapping to name_tokens().
    """
    found = {}
    def visit(item):
        if isinstance(item, Token):
            found.setdefault(item.value, None)
        elif isinstance(item, Assignment):
            visit(item.key)
            visit(item.value)
        elif isinstance(item, Mapping):
            for key, child in item.items():
                visit(key if isinstance(key, (Token, NamedToken)) else Token(key))
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
    visit(value)
    return tuple(found)


def name_tokens(value: object, names: Mapping[int, str]) -> object:
    """Attach copied native names without discarding numeric IDs or native types.

    Missing IDs stay numeric; duplicate fields and nested order stay intact.
    Mapping inputs become Assignment tuples so two equal native names cannot
    overwrite separate fields. No native calls, mutation or hidden retries occur.
    """
    if isinstance(value, Token):
        return ResolvedToken(value.value, names[value.value]) if value.value in names else value
    if isinstance(value, Assignment):
        return Assignment(name_tokens(value.key, names), name_tokens(value.value, names))
    if isinstance(value, Mapping):
        return tuple(Assignment(name_tokens(key if isinstance(key, (Token, NamedToken)) else Token(key), names),
                                name_tokens(child, names)) for key, child in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(name_tokens(child, names) for child in value)
    return value


def decode_command(data: bytes) -> Assignment:
    """Decode native binary I/O, retaining tokens and scalar widths exactly.

    Lists retain Assignment members instead of guessing a universal field schema.
    Escaped native token names (marker23) stay NamedToken, never guessed numeric IDs.
    """
    data = bytes(data)
    offset = 0
    def take(fmt):
        nonlocal offset
        codec = struct.Struct(fmt)
        if offset + codec.size > len(data):
            raise ValueError("truncated native binary value")
        result = codec.unpack_from(data, offset)
        offset += codec.size
        return result[0] if len(result) == 1 else result
    def item():
        nonlocal offset
        marker = take("<H")
        if marker == 3:
            values = []
            while True:
                if offset + 2 > len(data):
                    raise ValueError("unterminated native block")
                if struct.unpack_from("<H", data, offset)[0] == 4:
                    offset += 2
                    return tuple(values)
                values.append(item())
        if marker in (1, 4):
            raise ValueError("unexpected native structural token")
        if marker in (12, 20, 359, 668, 13, 14):
            fmt, wrap = {12:("<i", int), 20:("<I", UInt32), 359:("<q", Int64),
                668:("<Q", UInt64), 13:("<f", Float32), 14:("<B", bool)}[marker]
            return wrap(take(fmt))
        if marker in (15, 23):
            length = take("<H")
            if offset + length > len(data):
                raise ValueError("truncated native binary string")
            text = data[offset:offset+length].decode("utf-8")
            offset += length
            if marker == 15:
                return text
            key = NamedToken(text)
        else:
            key = Token(marker)
        if offset + 2 <= len(data) and struct.unpack_from("<H", data, offset)[0] == 1:
            offset += 2
            return Assignment(key, item())
        return key
    value = item()
    if offset != len(data) or not isinstance(value, Assignment) or not isinstance(value.value, tuple):
        raise ValueError("expected one native command assignment/block")
    return value


def request(client: NativeBridge, action: Action | int, *, type_token: int = 0,
            command: int = 0, data: bytes = b"", capacity: int = 0,
            submit_flags: int = 4, world: int = 0,
            adapter: str | int = ADAPTER, process: RemoteProcess | None = None):
    """One asynchronous native operation; submit uses the existing Session queue.

    ``command`` is a caller-owned raw native pointer. A nonzero world must come
    from its original input source before production, not a later world stamp.
    world=0 preserves the raw unchecked-world path. A normally returning Session
    receiver consumes even on rejection; an exception or pointer0 alone does not
    prove handoff. This operation does not automatically validate, execute or retry.
    error=0/nonnull command is not independent proof of native CReader total
    parsing, required-field correctness or domain success; no Reader status is exposed.
    Buffer bytes belong to the callback handle until completion, then are copied.
    VALIDATE with capacity>0 constructs a native CString reason, copies its UTF-8
    bytes and destroys it in the same callback. capacity=0 keeps the nullable raw
    validation path. buffer_size is the full reason byte count; error122 means a
    truncated reason, not a failed native validator call. Never auto-submit/retry.
    """
    action = Action(action)
    data = bytes(data)
    if action == Action.DESERIALIZE:
        value = decode_command(data)
        if not isinstance(value.key, Token):
            raise ValueError("command type must use the registered numeric token")
        type_token = value.key.value
    capacity = max(capacity, len(data))
    initial = RESULT.pack(RESULT.size, v3.ERROR_IO_PENDING, action, type_token,
        command, 0, capacity, len(data), 0, submit_flags, 0, 0, 0, 0, 0, world, 0)
    payload = data + bytes(capacity - len(data))
    def prepared(address):
        fields = list(RESULT.unpack(initial))
        fields[5] = address + RESULT.size if capacity else 0
        return RESULT.pack(*fields) + payload
    if action == Action.SUBMIT:
        owns = process is None
        process = process or RemoteProcess(client.pid)
        try:
            callback = resolve_v3_export(process, adapter, "PdxV3CommandSession")
            handle = v3_session.submit_callback(client, callback, initial + payload,
                decode_result=CommandResult.from_bytes, process=process,
                _prepare_payload=prepared)
        except Exception as error:
            if owns:
                if getattr(error, "query", None) is not None:
                    error.query._owns_process = True
                else:
                    process.close()
            raise
        handle._owns_process = owns
        return handle
    def make_handle(client, process, callback, address, owns):
        process.write(address, prepared(address))
        return v3.CallbackResult(client, process, callback, address, owns,
            RESULT.size + capacity, CommandResult.from_bytes)
    return v3.submit_callback(client, initial + payload, adapter=adapter,
        callback_symbol="PdxV3CommandInterface", decode_result=CommandResult.from_bytes,
        process=process, handle_factory=make_handle)


def token_name(client, type_token: int, *, capacity: int = 4096,
               adapter=ADAPTER, process=None):
    """Copy an actual engine token name; not restricted to command-type tokens.

    Output is CommandResult.token_name/data. Unknown ID is native NOT_FOUND.
    Capacity truncation is explicit; this never retries or interns a new token.
    """
    return request(client, Action.TOKEN_NAME, type_token=type_token, capacity=capacity,
                   adapter=adapter, process=process)


def resolve_token(client, name: str, *, adapter=ADAPTER, process=None):
    """Native case-insensitive token-directory lookup, without registration.

    Return type_token is a process-directory ID. It can denote a field, enum or
    command type; it is not proof that an arbitrary name has a command factory.
    Unknown name is NOT_FOUND with UINT32_MAX, not an interned success token.
    """
    return request(client, Action.RESOLVE_TOKEN, type_token=0xffffffff,
                   data=name.encode("utf-8"), adapter=adapter, process=process)


def enumerate_commands(client, *, adapter=ADAPTER, process=None):
    """List only the analyzed startup registrations whose live factory is nonnull."""
    return request(client, Action.ENUMERATE, capacity=REGISTERED_CAPACITY * REGISTRATION.size,
                   adapter=adapter, process=process)


def create(client, type_token: int, *, parameters=None, actor_id=None, world=0,
           adapter=ADAPTER, process=None):
    """Create by registered type ID, optionally filling its native parameter block.

    ``parameters`` is the type-specific token mapping/list inside native token65.
    ``actor_id`` optionally supplies CGameCommand's separate root token10304 as
    uint32; do not supply it for non-game Session commands. Omitting both calls
    only the default factory. For exact header/root fields, use encode_command()
    and request(DESERIALIZE) directly. No domain token or value is guessed here.
    Nonzero world must be captured from the original input source before command
    production, not stamped from a later lookup. world=0 keeps the raw behavior.
    """
    if parameters is None and actor_id is None:
        return request(client, Action.CREATE, type_token=type_token, world=world,
                       adapter=adapter, process=process)
    body = {65: parameters if parameters is not None else {}}
    if actor_id is not None:
        body = {10304: UInt32(actor_id), **body}
    return request(client, Action.DESERIALIZE, data=encode_command(type_token, body),
                   world=world, adapter=adapter, process=process)


@dataclass
class CommandAttempt:
    """One owner's current dispatch evidence; unknowns stay blocked, even query=None.

    This is one bounded record, replaced only by the next known-owned operation.
    It does not retry, recover, release storage or authorize a domain action.
    """
    action: Action
    command: int
    type_token: int
    world: int
    capacity: int = 0
    submit_flags: int = 4
    query: object = None
    submission_error: BaseException | None = None
    completion_error: BaseException | None = None
    result: CommandResult | None = None
    outcome: str = "pending"


class NativeCommand:
    """Explicit native ownership, bound to one client and world lifetime.

    One operation may be in flight at a time. No destructor dispatch, no automatic
    replay, no direct ExecuteGameCommand. attempt records intent before dispatch;
    pending remains its exact query, which can be None after a dispatch exception.
    complete() reads only that query. Unknown delivery/ownership retains the record
    and blocks further owner operations. Raw request/process APIs remain available.
    """
    def __init__(self, client, command, type_token, world, *, adapter=ADAPTER, process=None):
        self.client, self.process, self.adapter = client, process, adapter
        self.command, self.type_token, self.world = command, type_token, world
        self.pending = None
        self.attempt: CommandAttempt | None = None

    @classmethod
    def from_result(cls, client, result: CommandResult, *, adapter=ADAPTER, process=None):
        command = result.cloned_command if result.action == Action.CLONE else result.command
        if result.error or not command:
            raise ValueError("result does not own a native command")
        return cls(client, command, result.type_token, result.world,
                   adapter=adapter, process=process)

    def _request(self, action, **options):
        if self.pending is not None or (self.attempt is not None
                and self.attempt.outcome in ("pending", "unknown")):
            raise RuntimeError("native command operation pending/unknown; retain its original attempt")
        if not self.command:
            raise RuntimeError("native command ownership was consumed")
        if not self.world and action != Action.DESTROY:
            raise RuntimeError("retained command requires a concrete world lifetime")
        attempt = CommandAttempt(action, self.command, self.type_token, self.world,
            max(options.get("capacity", 0), 0), options.get("submit_flags", 4))
        self.attempt = attempt  # before any fallible dispatch, even before a query exists
        try:
            attempt.query = request(self.client, action, command=attempt.command, world=attempt.world,
                adapter=self.adapter, process=self.process, **options)
        except BaseException as error:
            attempt.submission_error = error
            attempt.query = getattr(error, "query", None)
            attempt.outcome = "unknown"
            self.pending = attempt.query
            raise
        self.pending = attempt.query
        return self.pending

    def clone(self):
        """Native copy construction; complete() leaves this source owned.

        The returned CommandResult.cloned_command is a separate owned pointer;
        NativeCommand.from_result(client, result) constructs its separate owner.
        There is no object-layout memcpy or automatic submit of either command.
        """
        return self._request(Action.CLONE, capacity=8)

    def serialize(self, capacity=65536):
        return self._request(Action.SERIALIZE, capacity=capacity)

    def validate(self, capacity=VALIDATION_REASON_CAPACITY):
        """Return the native bool and localized reason without consuming ownership.

        capacity=0 requests only the bool. A false bool is a domain rejection, not
        a transport error. Reason truncation is explicit and never triggers replay.
        """
        return self._request(Action.VALIDATE, capacity=capacity)

    def submit(self, flags=4):
        return self._request(Action.SUBMIT, submit_flags=flags)

    def destroy(self):
        return self._request(Action.DESTROY)

    def _ownership(self, attempt, result):
        query = attempt.query
        unchecked_destroy = attempt.action == Action.DESTROY and attempt.world == 0
        if (not isinstance(query, v3.CallbackResult)
                or not isinstance(result, CommandResult) or result.size != RESULT.size
                or result.action != attempt.action
                or (result.world != attempt.world and not
                    (unchecked_destroy and not result.error and not result.command))
                or (self.command, self.type_token, self.world) !=
                   (attempt.command, attempt.type_token, attempt.world)
                or result.submit_flags != attempt.submit_flags
                or result.buffer_capacity != attempt.capacity or result.count or result.reserved
                or getattr(query, "_result", None) is not result
                or type(getattr(query, "request_id", None)) is not int
                or not 0 < query.request_id <= 0xffffffffffffffff):
            return "unknown"
        expected_buffer = ((getattr(query, "opaque_address", query.result_address)
                            + RESULT.size) if attempt.capacity else 0)
        expected_type = attempt.type_token if not result.error and result.command else 0
        if (result.buffer != expected_buffer or result.type_token != expected_type
                or result.validated not in (0, 1) or result.accepted not in (0, 1)
                or (attempt.action != Action.VALIDATE and result.validated)
                or (attempt.action != Action.SUBMIT and result.accepted)
                or (result.error and result.accepted)
                or (attempt.action in (Action.DESTROY, Action.SUBMIT) and result.buffer_size)
                or (attempt.action == Action.VALIDATE and not result.error
                    and result.buffer_size > attempt.capacity)):
            return "unknown"
        if attempt.action == Action.SUBMIT:
            if (not isinstance(query, v3_session.SessionCallbackResult)
                    or not query.session_request_id or query.session_status is None
                    or query.session_status.completed_request_id < query.session_request_id):
                return "unknown"
        # Native Destroy/Submit clear *owned before a fallible callee. Only a
        # matching error0 receipt proves normal destruction/receiver return.
        # An explicit world0 Destroy is unchecked: result.world is callback
        # context only. Neither the owner's nor the attempt's world is stamped.
        if not result.error and attempt.action in (Action.DESTROY, Action.SUBMIT):
            return "consumed" if not result.command else "unknown"
        if result.command != attempt.command:
            return "unknown"
        if attempt.action == Action.CLONE and (result.cloned_command == attempt.command
                or (result.error and result.cloned_command)):
            return "unknown"
        if not result.error:
            return "owned" if attempt.action != Action.CLONE or result.cloned_command else "unknown"
        # Native callback/operation checks return before command dispatch or
        # before *owned=0. These codes do not stand for arbitrary native failures.
        if result.error in (21, 87, 5023) or (result.error == 1444 and attempt.action == Action.SUBMIT):
            return "unknown" if result.validated else "owned"
        # Native buffer truncation is a completed call, not an unknown exception.
        if (result.error == 122 and attempt.action in (Action.VALIDATE, Action.SERIALIZE)
                and result.buffer_size > attempt.capacity
                and (attempt.action != Action.VALIDATE or attempt.capacity > 0)):
            return "owned"
        if (result.error == 8 and attempt.action == Action.CLONE
                and result.buffer_size == 8 and not result.cloned_command):
            return "owned"
        return "unknown"

    def complete(self):
        attempt = self.attempt
        if attempt is None or attempt.outcome not in ("pending", "unknown"):
            raise RuntimeError("no native command operation pending")
        if attempt.query is None or self.pending is not attempt.query:
            attempt.outcome = "unknown"
            raise RuntimeError("original native command query unavailable/changed; retain its attempt")
        if attempt.result is not None:
            # A decoded receipt is final evidence. In particular, a later free
            # failure must not cause another read/_release attempt here.
            if attempt.completion_error is not None:
                raise attempt.completion_error
            return attempt.result
        try:
            result = attempt.query.read()
        except BaseException as error:
            attempt.completion_error = error
            attempt.result = getattr(attempt.query, "_result", None)
            attempt.outcome = "unknown"
            raise
        if result is not None:
            attempt.result = result
            attempt.outcome = self._ownership(attempt, result)
            if attempt.outcome in ("owned", "consumed"):
                self.command = result.command
                self.pending = None
        return result
