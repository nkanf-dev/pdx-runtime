"""V3's native console parser/dispatcher, without opening the console GUI.

The engine owns tokenization, command lookup, restrictions and result strings.
This module submits an ordinary shared Interface callback and retains all UTF-8
input/output storage until completion. It does not patch console permissions.
"""
from __future__ import annotations
from dataclasses import dataclass
import struct
from .native_bridge import NativeBridge, RemoteProcess
from .v3 import CallbackResult, ERROR_IO_PENDING, submit_callback

REQUEST = struct.Struct("<14I4Q2I")
RECORD = struct.Struct("<6I3Q")
U32 = struct.Struct("<I")

@dataclass(frozen=True)
class Command:
    kind: int
    developer_allowed: bool
    object: int
    callback: int
    context: int
    name: str
    description: str
    aliases: tuple[str, ...]
    parameters: tuple[str, ...]

@dataclass(frozen=True)
class ConsoleResult:
    size: int
    error: int
    action: int
    native_success: int
    input_size: int
    output_capacity: int
    output_size: int
    output_required: int
    count: int
    total_count: int
    release_restricted: int
    multiplayer_or_ironman: int
    execution_thread_id: int
    reserved: int
    application: int
    manager: int
    exact_callback: int
    exact_context: int
    argument_count: int
    records_bytes: int
    output: bytes
    commands: tuple[Command, ...] = ()
    arguments: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        return self.output.decode("utf-8", errors="replace")

    @property
    def complete_output(self) -> bool:
        return self.output_size == self.output_required

    @classmethod
    def from_bytes(cls, data: bytes) -> ConsoleResult:
        fields = REQUEST.unpack_from(data)
        result = cls(*fields, data[REQUEST.size+fields[4]:REQUEST.size+fields[4]+fields[6]])
        if result.action == 2:
            cursor, commands = 0, []
            for _ in range(result.count):
                values = RECORD.unpack_from(result.output, cursor)
                size, kind, allowed, alias_count, parameter_count, _, obj, callback, context = values
                end = cursor + size
                cursor += RECORD.size
                texts = []
                for _ in range(2 + alias_count + parameter_count):
                    length = U32.unpack_from(result.output, cursor)[0]
                    cursor += U32.size
                    texts.append(result.output[cursor:cursor+length].decode("utf-8", errors="replace"))
                    cursor += length
                if cursor != end:
                    raise ValueError("invalid native console record length")
                commands.append(Command(kind, bool(allowed), obj, callback, context,
                    texts[0], texts[1], tuple(texts[2:2+alias_count]),
                    tuple(texts[2+alias_count:])))
            object.__setattr__(result, "commands", tuple(commands))
        elif result.action == 3 and result.complete_output:
            cursor, arguments = 0, []
            for _ in range(result.argument_count):
                length = U32.unpack_from(result.output, cursor)[0]
                cursor += U32.size
                arguments.append(result.output[cursor:cursor+length].decode("utf-8", errors="replace"))
                cursor += length
            object.__setattr__(result, "arguments", tuple(arguments))
        return result


def _submit(client: NativeBridge, action: int, text: str | bytes, *, output_capacity: int,
            adapter: str | int, process: RemoteProcess | None,
            callback: int = 0, context: int = 0) -> CallbackResult:
    encoded = text.encode("utf-8") if isinstance(text, str) else text
    if action not in (6, 8) and b"\0" in encoded:
        raise ValueError("native console input cannot contain NUL")
    if not 0 <= output_capacity <= 0xFFFFFFFF:
        raise ValueError("output_capacity must fit uint32")
    total = REQUEST.size + len(encoded) + output_capacity
    if total > 0xFFFFFFFF:
        raise ValueError("native console request must fit uint32")
    initial = REQUEST.pack(total, ERROR_IO_PENDING, action, 0, len(encoded),
        output_capacity, *([0]*8), 0, 0, callback, context, 0, 0)
    return submit_callback(client, initial+encoded+bytes(output_capacity), adapter=adapter,
        callback_symbol="PdxV3ConsoleInterface", decode_result=ConsoleResult.from_bytes,
        process=process)


def execute(client: NativeBridge, text: str, *, output_capacity: int = 65536,
            adapter: str | int = "pdx_v3_console.dll",
            process: RemoteProcess | None = None) -> CallbackResult:
    """Execute text once through native lookup; preserve success and error message.

    error is the adapter/Win32 status; native_success is the engine command result.
    ERROR_MORE_DATA means execution already happened and output was truncated.
    Never resubmit a mutation just to recover a longer result message.
    """
    if not text:
        raise ValueError("text must not be empty")
    return _submit(client, 1, text, output_capacity=output_capacity, adapter=adapter, process=process)


def execute_arguments(client: NativeBridge, arguments, *, output_capacity: int = 65536,
                      adapter: str | int = "pdx_v3_console.dll",
                      process: RemoteProcess | None = None) -> CallbackResult:
    """Dispatch explicit strings once, without the engine's space tokenizer.

    Element0 is the command name. Spaces, quotes, Unicode and empty parameters
    stay in their original elements. Native command lookup and restrictions still
    apply. Output truncation never causes a replay. The command itself may parse
    a parameter further according to its own native semantics.
    """
    if isinstance(arguments, (str, bytes)):
        raise TypeError("arguments must be an iterable of strings, not command text")
    encoded = [argument.encode("utf-8") for argument in arguments]
    if not encoded or not encoded[0]:
        raise ValueError("arguments must start with a nonempty command name")
    if any(b"\0" in argument for argument in encoded):
        raise ValueError("native console arguments cannot contain NUL")
    payload = U32.pack(len(encoded)) + b"".join(U32.pack(len(argument)) + argument
                                               for argument in encoded)
    return _submit(client, 8, payload, output_capacity=output_capacity,
                   adapter=adapter, process=process)


def enumerate_commands(client: NativeBridge, *, output_capacity: int = 1048576,
                       adapter: str | int = "pdx_v3_console.dll",
                       process: RemoteProcess | None = None) -> CallbackResult:
    """Copy both native registration directories, including restricted entries.

    Each command keeps the exact live descriptor/object and callback/context.
    Enumeration is not proof that a command has been run successfully. A second
    larger enumeration is safe if error==234; execution is never auto-replayed.
    """
    return _submit(client, 2, "", output_capacity=output_capacity, adapter=adapter, process=process)


def parse(client: NativeBridge, text: str, *, output_capacity: int = 65536,
          adapter: str | int = "pdx_v3_console.dll",
          process: RemoteProcess | None = None) -> CallbackResult:
    """Use the engine's actual space-delimited tokenizer, including empty input."""
    return _submit(client, 3, text, output_capacity=output_capacity, adapter=adapter, process=process)


def invoke_exact(client: NativeBridge, callback: int, context: int, arguments_text: str, *,
                 output_capacity: int = 65536, adapter: str | int = "pdx_v3_console.dll",
                 process: RemoteProcess | None = None) -> CallbackResult:
    """Invoke a kind1 native SResult(args,context) callback with parsed args only.

    The input is parameters only: no command name is stripped. This ABI is only
    for kind1 descriptors. Use invoke_object for kind2 method callbacks.
    Dispatcher checks are intentionally not run.
    This is a public low-level primitive, not a permissions workaround. The caller
    owns selecting a valid callback/context and its required game scope.
    """
    if not callback:
        raise ValueError("callback must be nonzero")
    return _submit(client, 4, arguments_text, output_capacity=output_capacity,
                   adapter=adapter, process=process, callback=callback, context=context)


def invoke_object(client: NativeBridge, object_address: int, arguments_text: str, *,
                  output_capacity: int = 65536, adapter: str | int = "pdx_v3_console.dll",
                  process: RemoteProcess | None = None) -> CallbackResult:
    """Invoke a kind2 native method(object,out,args), with parameters-only text.

    The native object owns its context; this does not call a kind1 callback or
    strip a command name. Caller provides a live registered object and scope.
    """
    if not object_address:
        raise ValueError("object_address must be nonzero")
    return _submit(client, 5, arguments_text, output_capacity=output_capacity,
                   adapter=adapter, process=process, context=object_address)


def register_command(client: NativeBridge, name: str, callback: int, context: int = 0, *,
                     description: str = "", aliases: tuple[str, ...] = (),
                     parameters: tuple[str, ...] = (), developer_allowed: bool = False,
                     adapter: str | int = "pdx_v3_console.dll",
                     process: RemoteProcess | None = None) -> CallbackResult:
    """Register/copy a native kind1 callback descriptor, not a Python callback.

    Native duplicate-name behavior replaces the descriptor. The supplied callback
    module and context must remain valid until unregister and pending calls drain.
    Native descriptor strings are copied and our temporary engine strings freed.
    developer_allowed configures this descriptor; no global permission is changed.
    """
    if not name or not callback:
        raise ValueError("name and native callback must be nonzero")
    if len(aliases) > 3 or len(parameters) > 10:
        raise ValueError("native descriptor supports at most 3 aliases and 10 parameters")
    parts = []
    for text in (name, description, *aliases, *parameters):
        encoded = text.encode("utf-8")
        if b"\0" in encoded:
            raise ValueError("native descriptor strings cannot contain NUL")
        parts.append(U32.pack(len(encoded)) + encoded)
    payload = struct.pack("<3I", bool(developer_allowed), len(aliases), len(parameters)) + b"".join(parts)
    return _submit(client, 6, payload, output_capacity=0, adapter=adapter,
                   process=process, callback=callback, context=context)


def unregister_command(client: NativeBridge, name: str, *,
                       adapter: str | int = "pdx_v3_console.dll",
                       process: RemoteProcess | None = None) -> CallbackResult:
    """Remove a descriptor by its native name; caller-owned callback is not freed."""
    if not name:
        raise ValueError("name must not be empty")
    return _submit(client, 7, name, output_capacity=0, adapter=adapter, process=process)
