"""Bounded, copied-only snapshots of original V3 reminder/dialog sources.

The three profiled owners are original Jomini fallback items and the current
PopupManager general-information/confirmation pointer members. Ownership,
consumption and widget existence are not rendered visibility or native history.
No game session, process connection, acknowledgement or UI action is created.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
from types import MappingProxyType

from .native_bridge import NativeBridge, RemoteProcess
from .target_descriptor import (FunctionLocation, TargetDefinition,
                                verify_loaded_target)
from .v3 import ERROR_IO_PENDING, submit_callback
from .v3_data import Kind

HEADER = struct.Struct("<22IQ")
RECORD = struct.Struct("<8I")
FIELD = struct.Struct("<8IQ")
SNAPSHOT = 1
MAX_FIELDS = 6
MAX_ITEMS = 256
MAX_TEXT = 1048576
CONSUMED_VALID = 1
DIALOG_VALID = 2
WIDGET_VALID = 4
SOURCE_IDS = MappingProxyType({"jomini_notifications": 1, "general_information": 2,
                              "general_information_dialogs": 2, "confirmations": 3})
SOURCE_NAMES = MappingProxyType({1: "jomini_notifications", 2: "general_information", 3: "confirmations"})
FIELD_NAMES = MappingProxyType({1: "title", 2: "text", 3: "accept_label", 4: "decline_label",
                               5: "has_accept", 6: "has_decline", 7: "description"})
GETTERS = MappingProxyType({
    1: ((1, "JominiNotification.GetTitle"), (2, "JominiNotification.GetText"),
        (3, "JominiNotification.GetAcceptButtonText"), (4, "JominiNotification.GetDeclineButtonText"),
        (5, "JominiNotification.HasOnAccept"), (6, "JominiNotification.HasOnDecline")),
    2: ((1, "GeneralInformationWindow.GetTitle"), (2, "GeneralInformationWindow.GetText")),
    3: ((7, "ConfirmationWindow.GetDesc"),),
})

# Exact original executable file identity. All anchors are immutable PE code,
# not live globals/type IDs. The native adapter repeats PE/anchor checks before
# any layout read. No current mapped-image hash or semantic success is claimed.
_ANCHORS = (
    (0x34ac5f6, bytes.fromhex("48893d2b0b4802e80e49640084c0740733c9e8b355b40080")),
    (0x6568aa, bytes.fromhex("488d056fb41204488903488d4b08e8c358fcff")),
    (0x6568d9, bytes.fromhex("488b0d48682d05488b89e0050000e8548aa002498b8f500600004885c9740a488b01ba01000000ff1049899f50060000498b9f20060000")),
    (0x3046592, bytes.fromhex("8b5510486345143bc20f85880000008d4801c5f857c0c5fa2ac2c5fa5905fc4782")),
    (0x30466ee, bytes.fromhex("c683981200000048c783a01200000000000066c783a81200000100488bcde88f9c35")),
    (0x304637e, bytes.fromhex("488bb388000000488b008b040139052bdd99027f588b0543de99024c8d4c24404c8bc68944244033d2c644242001488bcfe85c995a00")),
    (0x21b5487, bytes.fromhex("8b05d7b181034c8d4c24404c8bc38944244033d2c644242001488bcfe868a84301")),
    (0x21b55b7, bytes.fromhex("8b059fb081034c8d4c24404c8bc38944244033d2c644242001488bcfe838a74301")),
    (0x21c88d8, bytes.fromhex("e8f383ebff4885c07404488b4020488ba8f01d0000")),
    (0x652239, bytes.fromhex("488b8e880000004885c9740a488b01ba01000000ff104889be88000000488b8dc0010000")),
    (0x21c57ac, bytes.fromhex("488b8f800000004885c9740a488b01ba01000000ff10488b4c243048899f800000004885c9740a488b")),
    (0x21efda0, bytes.fromhex("488b01ff6058")),
    (0x3046b4b, bytes.fromhex("c6839812000001")),
    (0x34aca5e, bytes.fromhex("488935c3064802488b89b8040000")),
)
_PROFILE = TargetDefinition(
    game="victoria3", version="sha256:7f53e3260cf4",
    sha256="7f53e3260cf46b6247f6906837464944038c4f74e860274ad3d394e0c04f811f",
    preferred_image_base=0x140000000, pe_offset=0x170, timestamp=0x6A846779,
    image_size=0x5DC6000, section_count=6,
    functions={f"dialog_anchor_{index}": FunctionLocation(rva, value)
               for index, (rva, value) in enumerate(_ANCHORS)},
    globals={"original_application": 0x592D128},
    layout={"wire_header_bytes": HEADER.size, "wire_record_bytes": RECORD.size, "wire_field_bytes": FIELD.size},
)


def verify_profile(process: RemoteProcess, image_base: int):
    """Check the supplied original module file and sampled loaded identity.

    This reads module identity/PE code, never dialog objects. Unknown file SHA
    or changed loaded code fails before allocation or callback submission.
    """
    return verify_loaded_target(process, image_base, definitions=(_PROFILE,))


@dataclass(frozen=True)
class DialogField:
    field_id: int
    error: int
    kind: int
    text_offset: int
    text_bytes_copied: int
    text_bytes_required: int
    complete: int
    reserved: int
    raw_bits: int
    raw_bytes: bytes
    expression: str

    @property
    def name(self) -> str:
        return FIELD_NAMES[self.field_id]

    @property
    def raw_text(self) -> str | None:
        if self.kind != Kind.STRING:
            return None
        try:
            return self.raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None

    @property
    def value(self) -> str | bool | None:
        if self.error or not self.complete:
            return None
        if self.kind == Kind.BOOL:
            return bool(self.raw_bits)
        if self.kind == Kind.STRING:
            return self.raw_text
        return None


@dataclass(frozen=True)
class DialogRecord:
    source_index: int
    error: int
    fields_offset: int
    field_count: int
    metadata_valid: int
    consumed: int
    has_dialog: int
    has_widget: int
    fields: tuple[DialogField, ...]

    @property
    def consumed_valid(self) -> bool:
        return bool(self.metadata_valid & CONSUMED_VALID)

    @property
    def dialog_valid(self) -> bool:
        return bool(self.metadata_valid & DIALOG_VALID)

    @property
    def widget_valid(self) -> bool:
        return bool(self.metadata_valid & WIDGET_VALID)


@dataclass(frozen=True)
class DialogSnapshot:
    size: int
    error: int
    action: int
    interface_thread_id: int
    source: int
    max_items: int
    start_index: int
    records_offset: int
    fields_offset: int
    fields_capacity: int
    text_offset: int
    text_capacity: int
    source_count: int
    returned_count: int
    field_count: int
    text_bytes_copied: int
    text_bytes_required: int
    truncated: int
    source_produced: int
    count_valid: int
    execution_thread_id: int
    reserved: int
    source_world: int
    records: tuple[DialogRecord, ...]

    @property
    def source_name(self) -> str:
        return SOURCE_NAMES[self.source]

    @classmethod
    def from_bytes(cls, raw: bytes) -> DialogSnapshot:
        if len(raw) < HEADER.size:
            raise ValueError("truncated dialog snapshot header")
        values = HEADER.unpack_from(raw)
        (size, error, action, thread, source, limit, start, records, fields, fields_capacity,
         text, text_capacity, count, returned, field_count, copied, required, truncated,
         produced, count_valid, executed, reserved, world) = values
        expected_fields = HEADER.size + limit * RECORD.size
        expected_text = expected_fields + limit * MAX_FIELDS * FIELD.size
        if (size != len(raw) or action != SNAPSHOT or source not in SOURCE_NAMES or reserved
                or not world or not thread or not 1 <= limit <= MAX_ITEMS or start > 0x7fffffff
                or text_capacity > MAX_TEXT or records != HEADER.size or fields != expected_fields
                or fields_capacity != limit * MAX_FIELDS or text != expected_text
                or size != text + text_capacity or returned > limit or field_count > fields_capacity
                or count > 0x7fffffff or copied > text_capacity or copied > required
                or produced not in (0, 1) or count_valid not in (0, 1) or truncated not in (0, 1)
                or count_valid and not produced or not count_valid and count
                or returned and not count_valid or not returned and field_count
                or not error and (not produced or not count_valid or truncated or executed != thread)):
            raise ValueError("invalid dialog snapshot bounds or source evidence")
        if count_valid and start <= count:
            maximum_returned = min(limit, count - start)
            if returned > maximum_returned or not error and returned != maximum_returned:
                raise ValueError("dialog snapshot ordinal/count mismatch")
        elif returned:
            raise ValueError("dialog records without a current ordinal range")
        if returned and executed != thread:
            raise ValueError("dialog records lack original Interface thread evidence")
        expected_metadata = 7 if source == 1 else 6
        getters = GETTERS[source]
        rows = []
        field_position = 0
        text_position = text
        required_total = 0
        for index in range(returned):
            row = RECORD.unpack_from(raw, records + index * RECORD.size)
            ordinal, row_error, field_offset, width, metadata, consumed, dialog, widget = row
            if (ordinal != start + index or ordinal >= count
                    or width > len(getters) or field_position + width > field_count
                    or field_offset != fields + field_position * FIELD.size
                    or metadata & ~expected_metadata or consumed not in (0, 1)
                    or dialog not in (0, 1) or widget not in (0, 1)
                    or consumed and not metadata & CONSUMED_VALID
                    or dialog and not metadata & DIALOG_VALID
                    or widget and (not dialog or not metadata & WIDGET_VALID)
                    or not row_error and (width != len(getters) or metadata != expected_metadata)
                    or not error and row_error):
                raise ValueError("invalid copied dialog record")
            field_rows = []
            for position in range(width):
                field = FIELD.unpack_from(raw, field_offset + position * FIELD.size)
                key, field_error, kind, offset, length, needed, complete, field_reserved, bits = field
                expected_key, expression = getters[position]
                expected_kind = Kind.BOOL if key in (5, 6) else Kind.STRING
                if (key != expected_key or field_reserved or complete not in (0, 1)
                        or kind > Kind.STRING or offset != text_position or offset + length > text + copied
                        or length > needed or not field_error and not complete
                        or complete and (field_error or kind != expected_kind or length != needed)
                        or field_error and not row_error or not error and field_error
                        or expected_kind == Kind.BOOL and (length or needed or bits > 1)
                        or expected_kind == Kind.STRING and bits):
                    raise ValueError("invalid copied dialog field")
                field_rows.append(DialogField(*field, raw[offset:offset + length], expression))
                text_position += length
                required_total += needed
            rows.append(DialogRecord(*row, tuple(field_rows)))
            field_position += width
        if field_position != field_count or text_position != text + copied or required_total != required:
            raise ValueError("dialog copied field/text totals do not match")
        return cls(*values, tuple(rows))


def snapshot(client: NativeBridge, *, source: str, process: RemoteProcess,
             image_base: int, world: int, interface_thread_id: int,
             max_items: int = 64, text_capacity: int = 16384, start_index: int = 0,
             adapter: str | int = "pdx_runtime.dll"):
    """Queue one original-owner/source/bind/getter/copy/cleanup callback.

    ``general_information_dialogs`` is an explicit catalog alias for adapter
    source ``general_information``. Supply the already owned process and actual
    main-module base. No process is opened or discovered here. Old DLLs without
    the export are unsupported; there is no escaped-pointer fallback. Unknown
    completion retains the exact original query; read/resume it without replay.
    ``start_index`` is a fresh-source ordinal, never a stable continuation ID.
    """
    if not isinstance(source, str) or source not in SOURCE_IDS:
        raise ValueError("unknown original dialog source")
    for name, value, low, high in (
            ("world", world, 1, 0xffffffffffffffff),
            ("image_base", image_base, 1, 0xffffffffffffffff - _PROFILE.image_size),
            ("interface_thread_id", interface_thread_id, 1, 0xffffffff),
            ("max_items", max_items, 1, MAX_ITEMS),
            ("text_capacity", text_capacity, 0, MAX_TEXT),
            ("start_index", start_index, 0, 0x7fffffff)):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(name + " is outside dialog snapshot bounds")
    client_pid, process_pid = getattr(client, "pid", None), getattr(process, "pid", None)
    if type(client_pid) is not int or client_pid <= 0 or type(process_pid) is not int or process_pid != client_pid:
        raise ValueError("the supplied process must match the original bridge PID")
    verify_profile(process, image_base)  # No layout/allocation/export/dispatch before this gate.
    fields = HEADER.size + max_items * RECORD.size
    text = fields + max_items * MAX_FIELDS * FIELD.size
    size = text + text_capacity
    values = (size, ERROR_IO_PENDING, SNAPSHOT, interface_thread_id, SOURCE_IDS[source],
              max_items, start_index, HEADER.size, fields, max_items * MAX_FIELDS, text, text_capacity,
              0, 0, 0, 0, 0, 0, 0, 0, 0, 0, world)
    initial = HEADER.pack(*values) + bytes(size - HEADER.size)
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3UiDialogsInterface", decode_result=DialogSnapshot.from_bytes,
        process=process)
