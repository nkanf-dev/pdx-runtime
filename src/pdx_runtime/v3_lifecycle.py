"""V3 native save/load/menu/time primitives for ordinary persistent Python.

Adapters are already loaded. Callbacks run in the native Interface context; game
functions and their scopes live in the small adapter. Completion of a callback is
separate from async save tasks, load replacement, or menu transition. No polling
loop or automatic replay is introduced here. Every underlying handle stays public.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
from pathlib import PurePosixPath
import struct

from .native_bridge import NativeBridge, RemoteProcess
from .v3 import CallbackResult, ERROR_IO_PENDING, submit_callback

PATH_BYTES = 1024
LIFECYCLE_RESULT = struct.Struct("<6Ii9I10Q1024s1024s16s")


class Action(IntEnum):
    OBSERVE = 0
    SAVE = 1
    POLL_SAVE = 2
    RELEASE_SAVE = 3
    LOAD = 4
    EXIT_TO_MENU = 5
    SET_SPEED = 6
    SET_PAUSED = 7
    RESOLVE_SAVE_PATH = 8
    SET_DATE = 9
    EXIT_APPLICATION = 10
    RESOLVE_EXIT_SAVE_PATH = 11


class ExitIntent(IntEnum):
    DISCARD = 0
    SAVE_AT_NATIVE_DESTINATION = 1


def _path_bytes(value: str) -> bytes:
    data = value.encode("utf-8")
    if b"\0" in data or len(data) >= PATH_BYTES:
        raise ValueError("UTF-8 path must fit in 1023 bytes without NUL")
    return data


def _decode_path(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("utf-8")


@dataclass(frozen=True)
class LifecycleResult:
    size: int
    error: int
    action: int
    flags: int
    save_type: int
    paused: int
    speed: int
    current_state: int
    pending_state: int
    transition_pending: int
    task_state: int
    task_completed: int
    file_present: int
    file_verified: int
    execution_thread_id: int
    native_result: int
    application: int
    world: int
    active_idler: int
    active_idler_vtable: int
    save_manager: int
    owned_task_handle: int
    task: int
    file_size: int
    date_raw: int
    frame: int
    path: str
    resolved_path: str
    date_text: str

    @classmethod
    def from_bytes(cls, data: bytes) -> LifecycleResult:
        values = list(LIFECYCLE_RESULT.unpack(data))
        values[-3:] = [_decode_path(value) for value in values[-3:]]
        return cls(*values)

    @property
    def displayed_speed(self) -> int:
        """Official GetGameSpeed semantics: 0 paused, otherwise native index+1."""
        return 0 if self.paused else self.speed + 1

    @property
    def date_parts(self) -> tuple[int, int, int, int] | None:
        if not self.date_text:
            return None
        parts = [int(part) for part in self.date_text.split(".")]
        if len(parts) == 3:
            parts.append(0)
        return tuple(parts)

    @property
    def saved(self) -> bool:
        return self.error == 0 and bool(self.task_completed and self.file_verified)

    @property
    def quit_requested(self) -> bool:
        """Native Application quit flag observed; not proof that its process exited."""
        return bool(self.flags & 4)

    @property
    def in_main_menu(self) -> bool:
        return bool(self.flags & 1)


class SaveOperation:
    """Own one native save-task reference; callback storage is a separate handle.

    ``poll`` submits one observation only. ``release`` submits native reference
    release; neither operation waits or resubmits the original save. Keep this
    operation until the retained reference has been explicitly released.
    """
    def __init__(self, result: LifecycleResult, client: NativeBridge, *,
                 adapter: str | int, process: RemoteProcess | None):
        if not result.owned_task_handle:
            raise ValueError("result has no owned native save-task handle")
        self.result = result
        self.client = client
        self.adapter = adapter
        self.process = process
        self.owned_task_handle = result.owned_task_handle
        self.pending_release: CallbackResult | None = None

    def poll(self) -> CallbackResult:
        if self.pending_release is not None:
            raise RuntimeError("settle the original release request before polling its native task")
        if not self.owned_task_handle:
            raise RuntimeError("native save-task reference was released")
        return submit(self.client, Action.POLL_SAVE, path=self.result.path,
                      resolved_path=self.result.resolved_path,
                      owned_task_handle=self.owned_task_handle,
                      overwrite=bool(self.result.flags & 2),
                      adapter=self.adapter, process=self.process)

    def release(self) -> CallbackResult:
        if self.pending_release is not None:
            raise RuntimeError("release request is already pending; settle that exact callback")
        if not self.owned_task_handle:
            raise RuntimeError("native save-task reference was already released")
        try:
            query = submit(self.client, Action.RELEASE_SAVE,
                           owned_task_handle=self.owned_task_handle,
                           adapter=self.adapter, process=self.process)
        except Exception as error:
            # Unknown delivery preserves the exact query, not permission to replay.
            self.pending_release = getattr(error, "query", None)
            raise
        self.pending_release = query
        return query

    def settle_release(self) -> LifecycleResult | None:
        """Read only the original release callback; never replay native release.

        An unknown transport outcome must first be bound to its proven request ID
        through ``pending_release.recover``. A pre-release callback error retains
        the task reference. An exception after native release began is unknown
        ownership, so the original query remains attached and blocks reuse.
        """
        if self.pending_release is None:
            raise RuntimeError("there is no pending release callback")
        result = self.pending_release.read()
        if result is None:
            return None
        if result.error == 0 and result.owned_task_handle == 0:
            self.owned_task_handle = 0
            self.pending_release = None
        elif result.error != 0 and result.owned_task_handle == self.owned_task_handle:
            self.pending_release = None
        else:
            raise RuntimeError("native task release ownership is unknown; retain the original query")
        return result


def submit(client: NativeBridge, action: Action | int = Action.OBSERVE, *,
           path: str = "", resolved_path: str = "", save_type: int = 3,
           paused: bool = True, speed: int = 0, owned_task_handle: int = 0,
           overwrite: bool = False, native_exit_save: bool = False,
           adapter: str | int = "pdx_v3_lifecycle.dll",
           process: RemoteProcess | None = None) -> CallbackResult:
    action = Action(action)
    if not 0 <= save_type <= 5:
        raise ValueError("save_type is the native byte enum in [0, 5]")
    if action == Action.SET_SPEED and not 0 <= speed <= 4:
        raise ValueError("native speed index is in [0, 4]")
    initial = LIFECYCLE_RESULT.pack(LIFECYCLE_RESULT.size, ERROR_IO_PENDING,
        action, (2 if overwrite else 0) | (8 if native_exit_save else 0),
        save_type, int(paused), speed, *([0] * 9),
        0, 0, 0, 0, 0, owned_task_handle, 0, 0, 0, 0,
        _path_bytes(path), _path_bytes(resolved_path), b"")
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3LifecycleInterface", decode_result=LifecycleResult.from_bytes,
        process=process)


def observe(client: NativeBridge, **kwargs) -> CallbackResult:
    return submit(client, Action.OBSERVE, **kwargs)


def resolve_save_path(client: NativeBridge, name: str, **kwargs) -> CallbackResult:
    """Ask the manager for its VFS root/name/extension; never modify that root."""
    return submit(client, Action.RESOLVE_SAVE_PATH, path=name, **kwargs)


def save(client: NativeBridge, name: str, *, save_type: int = 3,
         overwrite: bool = False, **kwargs) -> CallbackResult:
    """Save under the native VFS save root; overwrite requires explicit opt-in.

    Use a new temporary subdirectory and a new name without the native extension.
    The native filesystem creates its parent path and supplies root and extension.
    resolve_save_path returns a native VFS path, not necessarily an OS path.
    Validation must not target an original player save. Absolute-path input is not
    the native save API.
    """
    normalized = name.replace("\\", "/")
    parts = PurePosixPath(normalized)
    if not normalized or parts.is_absolute() or ":" in normalized or ".." in parts.parts:
        raise ValueError("save name must stay relative to the actual native save root")
    return submit(client, Action.SAVE, path=normalized, save_type=save_type,
                  overwrite=overwrite, **kwargs)


def load(client: NativeBridge, path: str, **kwargs) -> CallbackResult:
    """Consume native file/options from main menu; observe the async transition.

    For replace, first discard the current temporary world with exit_to_menu and
    observe main-menu completion. Never reuse any old object pointer after load.
    """
    return submit(client, Action.LOAD, path=path.replace("\\", "/"), **kwargs)


def exit_to_menu(client: NativeBridge, **kwargs) -> CallbackResult:
    """Request native discard-to-menu, not process quit or exit autosave."""
    return submit(client, Action.EXIT_TO_MENU, **kwargs)


def resolve_native_exit_save_path(client: NativeBridge, **kwargs) -> CallbackResult:
    """Read ordinary NSaves.EXIT_SAVE_NAME destination; ironman target is not guessed."""
    return submit(client, Action.RESOLVE_EXIT_SAVE_PATH, **kwargs)


def exit_application(client: NativeBridge, *, intent: ExitIntent, **kwargs) -> CallbackResult:
    """Request original engine shutdown with explicit discard/native-save intent.

    SAVE_AT_NATIVE_DESTINATION authorizes the game's configured autosave_exit or
    ironman destination, including native overwrite. It is not a caller-supplied
    temporary filename. To save a new temporary file instead, compose save/poll/
    release/settle_release before DISCARD. DISCARD refuses an active ironman world.
    Callback completion and quit_requested mean request acceptance only. Observe
    process completion independently through a process handle; never replay after
    the pipe closes or an unknown transport result.
    """
    if not isinstance(intent, ExitIntent):
        raise TypeError("intent must be an explicit ExitIntent value")
    return submit(client, Action.EXIT_APPLICATION,
                  native_exit_save=intent == ExitIntent.SAVE_AT_NATIVE_DESTINATION, **kwargs)


def set_speed(client: NativeBridge, speed: int, **kwargs) -> CallbackResult:
    return submit(client, Action.SET_SPEED, speed=speed, **kwargs)


def set_paused(client: NativeBridge, paused: bool, **kwargs) -> CallbackResult:
    return submit(client, Action.SET_PAUSED, paused=paused, **kwargs)


def set_date(client: NativeBridge, year: int, month: int, day: int,
             hour: int = 0, **kwargs) -> CallbackResult:
    """Preflight the engine calendar parser, then run its original date command."""
    return submit(client, Action.SET_DATE, path=f"{year}.{month}.{day}.{hour}", **kwargs)


def retain_save_result(result: LifecycleResult, client: NativeBridge, *,
                       adapter: str | int = "pdx_v3_lifecycle.dll",
                       process: RemoteProcess | None = None) -> SaveOperation:
    return SaveOperation(result, client, adapter=adapter, process=process)


SAVE_CATALOG_HEADER = struct.Struct("<12I3Q")
SAVE_DESCRIPTOR_HEADER = struct.Struct("<4IiIqQ2IQ")


class SaveSource(IntFlag):
    LOCAL = 1
    CLOUD = 2


class SaveCatalogAction(IntEnum):
    LIST = 0
    DESCRIBE_PATH = 1


@dataclass(frozen=True)
class SaveDescriptor:
    size: int
    cloud: int
    is_valid: int
    is_ironman_save: int
    number_of_players: int
    reserved: int
    modified_time_raw: int
    last_played_date_raw: int
    country_flag_id: int
    metadata_reserved: int
    game_date_raw: int
    path: str
    directory: str
    name: str
    version_name: str
    last_played_date_text: str
    country_rank_text: str
    game_date_text: str

    @property
    def game_date_parts(self) -> tuple[int, int, int, int] | None:
        """Split engine-formatted serialized game_date, not real/last-played date."""
        if not self.game_date_text:
            return None
        parts = [int(part) for part in self.game_date_text.split(".")]
        if len(parts) == 3:
            parts.append(0)
        return tuple(parts)

    @property
    def last_played_date_parts(self) -> tuple[int, int, int, int] | None:
        if not self.last_played_date_text:
            return None
        parts = [int(part) for part in self.last_played_date_text.split(".")]
        if len(parts) == 3:
            parts.append(0)
        return tuple(parts)

    @property
    def is_multiplayer(self) -> bool:
        return self.number_of_players > 1


@dataclass(frozen=True)
class SaveCatalogResult:
    size: int
    error: int
    action: int
    sources: int
    directory_bytes: int
    filter_bytes: int
    output_capacity: int
    output_size: int
    output_required: int
    count: int
    total_count: int
    execution_thread_id: int
    application: int
    save_manager: int
    descriptor: int
    records: tuple[SaveDescriptor, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> SaveCatalogResult:
        values = SAVE_CATALOG_HEADER.unpack_from(data)
        cursor = SAVE_CATALOG_HEADER.size + values[4] + values[5]
        end = cursor + values[7]
        if end > len(data):
            raise ValueError("native save catalog output exceeds callback storage")
        records = []
        for _ in range(values[9]):
            header = SAVE_DESCRIPTOR_HEADER.unpack_from(data, cursor)
            size = header[0]
            record_end = cursor + size
            if record_end > end:
                raise ValueError("native save descriptor exceeds published output")
            cursor += SAVE_DESCRIPTOR_HEADER.size
            strings = []
            for _ in range(7):
                length = struct.unpack_from("<I", data, cursor)[0]
                cursor += 4
                if cursor + length > record_end:
                    raise ValueError("native save descriptor string exceeds its record")
                strings.append(data[cursor:cursor + length].decode("utf-8"))
                cursor += length
            if cursor != record_end:
                raise ValueError("native save descriptor size does not match its fields")
            records.append(SaveDescriptor(*header, *strings))
        if cursor != end:
            raise ValueError("native save catalog count does not match its output")
        return cls(*values, tuple(records))


def submit_save_catalog(client: NativeBridge, action: SaveCatalogAction | int, *,
                        directory: str = "", name_filter: str = "",
                        sources: SaveSource = SaveSource.LOCAL,
                        output_capacity: int = 64 * 1024,
                        adapter: str | int = "pdx_v3_lifecycle.dll",
                        process: RemoteProcess | None = None) -> CallbackResult:
    """One original native manager scan/read, copied output and native RAII cleanup.

    A MORE_DATA result publishes complete records plus output_required/total_count.
    It does not automatically repeat a directory scan or descriptor read. Output
    strings are copied Python data; no descriptor pointer lifetime is concealed.
    """
    action = SaveCatalogAction(action)
    sources = SaveSource(sources)
    if not sources or int(sources) & ~3:
        raise ValueError("sources must select LOCAL and/or CLOUD")
    if not 0 <= output_capacity <= 0xFFFFFFFF:
        raise ValueError("output_capacity must fit uint32")
    path = directory.encode("utf-8")
    filter_text = name_filter.encode("utf-8")
    if b"\0" in path or b"\0" in filter_text:
        raise ValueError("native catalog inputs cannot contain NUL")
    size = SAVE_CATALOG_HEADER.size + len(path) + len(filter_text) + output_capacity
    initial = SAVE_CATALOG_HEADER.pack(size, ERROR_IO_PENDING, action, sources,
        len(path), len(filter_text), output_capacity, 0, 0, 0, 0, 0, 0, 0, 0)
    initial += path + filter_text + bytes(output_capacity)
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3SaveCatalogInterface", decode_result=SaveCatalogResult.from_bytes,
        process=process)


def list_saves(client: NativeBridge, *, directory: str = "", name_filter: str = "",
               sources: SaveSource = SaveSource.LOCAL, **kwargs) -> CallbackResult:
    """Enumerate through native manager/VFS; blank directory uses its live save root.

    Directory is a native VFS path, not an OS glob. name_filter is the original
    descriptor-field equality filter (+0x30), not a filename, substring, or predicate callback.
    Cloud selection uses the native provider and can return no records when absent.
    modified_time_raw keeps native int64 bits; no host epoch is assumed.
    last_played_date_text uses the original native dotted formatter, not relative
    localized UI age/fallback. country_flag_id is a coat-of-arms resource ID, not
    CountryFullID. country_rank_text preserves native localized formatting.
    game_date_raw/text comes from serialized game_date+210, separately from
    real_date/last-played+218. Both strings use original native date formatting.
    """
    return submit_save_catalog(client, SaveCatalogAction.LIST, directory=directory,
                               name_filter=name_filter, sources=sources, **kwargs)


def describe_save(client: NativeBridge, path: str, *, cloud: bool = False,
                  **kwargs) -> CallbackResult:
    """Read one owned native descriptor/header and return copied metadata.

    It does not load a world, write a save, or retain the descriptor after callback.
    Low-level descriptor/array ownership APIs remain separate public DLL exports.
    """
    return submit_save_catalog(client, SaveCatalogAction.DESCRIBE_PATH, directory=path,
                               sources=SaveSource.CLOUD if cloud else SaveSource.LOCAL, **kwargs)


SAVE_MANAGEMENT_HEADER = struct.Struct("<12I2Q")


class SaveManagementAction(IntEnum):
    RENAME_NATIVE = 0
    DELETE_PATH = 1
    DELETE_NAMED = 2


@dataclass(frozen=True)
class SaveManagementResult:
    size: int
    error: int
    action: int
    cloud: int
    source_bytes: int
    destination_bytes: int
    message_capacity: int
    message_size: int
    message_required: int
    native_success: int
    native_result_tag: int
    execution_thread_id: int
    application: int
    save_manager: int
    message_raw: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> SaveManagementResult:
        values = SAVE_MANAGEMENT_HEADER.unpack_from(data)
        start = SAVE_MANAGEMENT_HEADER.size + values[4] + values[5]
        end = start + values[7]
        if end > len(data):
            raise ValueError("native save management message exceeds callback storage")
        return cls(*values, data[start:end])

    @property
    def message(self) -> str:
        return self.message_raw.decode("utf-8", errors="replace")


def submit_save_management(client: NativeBridge, action: SaveManagementAction | int, *,
                           source: str, destination: str = "", cloud: bool = False,
                           message_capacity: int = 4096,
                           adapter: str | int = "pdx_v3_lifecycle.dll",
                           process: RemoteProcess | None = None) -> CallbackResult:
    """One explicit native file mutation. Native domain failure stays in its result.

    native_success and original result tag distinguish success from native error;
    callback error0 is not mutation success. A short message reports MORE_DATA
    after the original mutation and never automatically replays it for more text.
    Unknown transport delivery retains the exact callback; never blindly retry.
    No directory allowlist or implicit multi-file/delete-all operation is applied.
    """
    action = SaveManagementAction(action)
    if not 0 <= message_capacity <= 0xFFFFFFFF:
        raise ValueError("message_capacity must fit uint32")
    source_text, destination_text = source.encode("utf-8"), destination.encode("utf-8")
    if not source_text or b"\0" in source_text or b"\0" in destination_text:
        raise ValueError("explicit source is required and native paths cannot contain NUL")
    size = SAVE_MANAGEMENT_HEADER.size + len(source_text) + len(destination_text) + message_capacity
    initial = SAVE_MANAGEMENT_HEADER.pack(size, ERROR_IO_PENDING, action, int(cloud),
        len(source_text), len(destination_text), message_capacity, 0, 0, 0, 0xFFFFFFFF, 0, 0, 0)
    initial += source_text + destination_text + bytes(message_capacity)
    return submit_callback(client, initial, adapter=adapter,
        callback_symbol="PdxV3SaveManagementInterface", decode_result=SaveManagementResult.from_bytes,
        process=process)


def rename_save(client: NativeBridge, source_native_path: str, new_name: str,
                **kwargs) -> CallbackResult:
    """Rename one native VFS file to a new leaf in the same directory, no overwrite.

    Include the desired extension explicitly. The original native API resolves
    only the old path and replaces physical trailing components with its supplied
    replacement view, so this helper intentionally accepts a leaf, not a second
    arbitrary destination VFS path. It never deletes an existing target or writes
    save metadata. The raw original rename parameter interface remains public.
    """
    if not new_name or new_name in (".", "..") or any(char in new_name for char in "/\\:"):
        raise ValueError("new_name must be an explicit filename leaf")
    return submit_save_management(client, SaveManagementAction.RENAME_NATIVE,
                                  source=source_native_path, destination=new_name, **kwargs)


def delete_save(client: NativeBridge, native_path: str, **kwargs) -> CallbackResult:
    """Delete exactly the supplied native VFS path through its original API."""
    return submit_save_management(client, SaveManagementAction.DELETE_PATH,
                                  source=native_path, **kwargs)


def delete_named_save(client: NativeBridge, name: str, *, cloud: bool = False,
                      **kwargs) -> CallbackResult:
    """Original manager single-name delete, joined with live root/native extension.

    A local native success means final absence; a missing name may also return1.
    Cloud uses the original provider. No delete-all traversal is introduced.
    For an exact subdirectory/file path instead, compose delete_save explicitly.
    """
    return submit_save_management(client, SaveManagementAction.DELETE_NAMED,
                                  source=name, cloud=cloud, **kwargs)
