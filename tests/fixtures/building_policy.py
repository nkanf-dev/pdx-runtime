"""Strict byte memory and callback transport for current subsidy tests only."""
import struct
from pdx_runtime import native_call, v3_session, v3_data as data
from pdx_runtime import v3_commands as commands, v3_objects as objects
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES

WORLD, ACTOR = 0x50000, 0x02000004
BUILDING, STATE, UNIQUE, SHARED = 0x03000007, 0x04000009, 0xD0000, 0xC0000


class PolicyMemory:
    pid = 123

    def __init__(self):
        self.blocks, self.freed = {}, []
        self.next_address, self.closed = 0x200000, False
        names = ("PdxV3ObjectsInterface", "PdxV3CommandInterface", "PdxV3CommandSession",
            "PdxNativeCallInterface", "PdxV3QueueSessionCallback", "PdxV3SessionCallbackState")
        self.exports = {name: 0x10000+i*0x100 for i, name in enumerate(names)}
        self.exports.update(PdxV3DataInterface=0x19000, PdxV3ConsolePlayerStateInterface=0x19100)
        self.blocks[self.exports["PdxV3SessionCallbackState"]] = bytearray(v3_session.CALLBACK_STATUS.size)

    def resolve_export(self, module, symbol, *, aliases=None):
        if aliases is not None and aliases is not V3_EXPORT_ALIASES:
            raise AssertionError("unexpected export-alias policy")
        return self.exports[symbol]

    def allocate(self, size):
        address = self.next_address
        self.next_address += ((size+0xFFF) & ~0xFFF)+0x1000
        self.blocks[address] = bytearray(size)
        return address

    def _block(self, address, size):
        for base, block in self.blocks.items():
            if base <= address and address+size <= base+len(block):
                return block, address-base
        raise AssertionError(f"not live caller-owned storage: {address:#x}/{size}")

    def read(self, address, size):
        block, offset = self._block(address, size)
        return bytes(block[offset:offset+size])

    def store(self, address, value):
        block, offset = self._block(address, len(value))
        block[offset:offset+len(value)] = value

    def write(self, address, value):
        self.store(address, value)
        return len(value)

    def free(self, address):
        if address in self.freed:
            raise AssertionError("callback input freed twice")
        self.freed.append(address)
        del self.blocks[address]

    def close(self):
        self.closed = True

    def active_callback_buffers(self):
        return set(self.blocks)-{self.exports["PdxV3SessionCallbackState"]}


class PolicyTransport:
    pid = 123

    def __init__(self, process):
        self.process = process
        self.request_id = self.completed_id = 0
        self.world, self.hold, self.held = WORLD, False, None
        self.queued, self.commands, self.inputs = [], [], []
        self.command_results, self.object_requests = [], []
        self.command_live = True
        self.session_state = process.exports["PdxV3SessionCallbackState"]
        self.evaluations, self.bindings, self.releases = [], [], []
        self.next_handle, self.generated_handle = 0xA1000, 0
        self.state_owner, self.player_actor, self.observer = ACTOR, ACTOR, 0
        self.state_id, self.resolved_building, self.type_pointer = STATE, BUILDING, 0xF0020
        self.enabled = {"subsidies": False}
        self.bool_kind, self.selected = data.Kind.BOOL, None
        self.carrier_world, self.clone_pointer = WORLD, UNIQUE
        self.actor_wire, self.building_wire, self.mode_wire = ACTOR, (commands.UInt32(BUILDING),), commands.Token(10760)
        self.validation_bool, self.validation_error, self.validation_reason = 1, 0, b""
        self.accepted, self.submit_error, self.serialized_wire = 1, 0, None

    def request(self, command):
        if command == "world-status":
            return self.v3_world_status()
        words = command.split()
        if len(words) == 3 and words[0] == "interface-callback":
            callback, address = int(words[1], 0), int(words[2], 0)
            if command != f"interface-callback {callback:#x} {address:#x}":
                raise AssertionError(f"noncanonical callback: {command!r}")
            return self.queue_v3_interface_callback(callback, address)
        raise AssertionError(f"unexpected wire command: {command!r}")

    def v3_world_status(self):
        return {"status": "ok", "world": hex(self.world), "transition_pending": 0,
            "request_id": self.request_id, "completed_request_id": self.completed_id}

    def queue_v3_interface_callback(self, callback, address):
        symbol = next(name for name, value in self.process.exports.items() if value == callback)
        self.queued.append((symbol, address))
        self.request_id += 1
        request_id = self.request_id
        if self.hold:
            self.held = (symbol, address, request_id)
        else:
            self._execute(symbol, address, request_id)
            self.completed_id = request_id
        return {"status": "ok", "error": 0, "request_id": request_id}

    def finish_held(self):
        symbol, address, request_id = self.held
        self._execute(symbol, address, request_id)
        self.completed_id = request_id
        self.held = None

    def _execute(self, symbol, address, request_id):
        if symbol == "PdxV3DataInterface":
            self._data(address)  # Supplied by the current subsidy-test transport.
        elif symbol == "PdxV3ObjectsInterface":
            self._objects(address)
        elif symbol == "PdxV3CommandInterface":
            self._command(address)
        elif symbol == "PdxNativeCallInterface":
            fields = list(native_call.REQUEST.unpack(self.process.read(address, native_call.REQUEST.size)))
            callback, opaque, slot = struct.unpack("<3Q", self.process.read(fields[4], 24))
            assert fields[2] == self.process.exports["PdxV3QueueSessionCallback"]
            assert callback == self.process.exports["PdxV3CommandSession"]
            self._command(opaque)
            self.process.store(slot, struct.pack("<Q", request_id))
            self.process.store(self.session_state, v3_session.CALLBACK_STATUS.pack(
                v3_session.CALLBACK_STATUS.size, 2, 0, 33,
                request_id, request_id, callback, opaque, 0xABC00))
            fields[1], fields[5] = 0, 0
            self.process.store(address, native_call.REQUEST.pack(*fields))
        else:
            raise AssertionError(f"unexpected callback: {symbol}")

    def _objects(self, address):
        fields = list(objects.HEADER.unpack(self.process.read(address, objects.HEADER.size)))
        self.object_requests.append(tuple(fields))
        domain = objects.Domain(fields[2])
        assert fields[3] == objects.READ_IDS and fields[7] == 1
        target, = struct.unpack("<I", self.process.read(address+fields[8], 4))
        if domain == objects.Domain.BUILDING:
            assert target == BUILDING
            row = objects.BUILDING_RECORD.pack(target, self.resolved_building, 0, 7, 0xF0010,
                self.state_id, 2, self.type_pointer)
        elif domain == objects.Domain.STATE:
            assert target == self.state_id
            row = objects.STATE_RECORD.pack(target, target, 0, 9, 0xF0030, self.state_owner, 0)
        else:
            raise AssertionError("unexpected Company/Market/business object request")
        self.process.store(address+fields[9], row)
        fields[1], fields[11], fields[12], fields[13] = 0, 1, 1, 1
        fields[14], fields[15], fields[16] = 1, 1, 0
        fields[18], fields[19], fields[20] = 0xF0040, 0xF0050, self.world
        self.process.store(address, objects.HEADER.pack(*fields))

    def _command(self, address):
        fields = list(commands.RESULT.unpack(self.process.read(address, commands.RESULT.size)))
        action = commands.Action(fields[2]); token = self.selected[3]
        self.commands.append(action); self.inputs.append(commands.RESULT.pack(*fields))
        assert fields[15] == WORLD
        fields[1],fields[12],fields[14],fields[15],fields[16] = 0,33,0xABC01,WORLD,0xABC00
        if action == commands.Action.CLONE:
            assert fields[4] == SHARED and fields[6] == 8
            fields[3],fields[7] = token,8
            self.process.store(fields[5],struct.pack("<Q",self.clone_pointer))
        elif action == commands.Action.SERIALIZE:
            assert fields[4] == UNIQUE
            self.serialized_wire = commands.encode_command(token,
                {10304:commands.UInt32(self.actor_wire),65:{10232:self.building_wire,1012:self.mode_wire}})
            fields[3],fields[7] = token,len(self.serialized_wire)
            self.process.store(fields[5],self.serialized_wire)
        elif action == commands.Action.VALIDATE:
            assert fields[4] == UNIQUE
            # Native callback resolves Type only on error0, and ValidateText
            # copies capacity bytes while reporting the original required size.
            fields[1],fields[3],fields[7],fields[10] = (self.validation_error,
                0 if self.validation_error else token,len(self.validation_reason),self.validation_bool)
            self.process.store(fields[5],self.validation_reason[:fields[6]])
        elif action in (commands.Action.SUBMIT,commands.Action.DESTROY):
            assert fields[4] == UNIQUE and self.command_live
            if action == commands.Action.SUBMIT: assert fields[9] == 4
            fields[1] = self.submit_error if action == commands.Action.SUBMIT else 0
            # Native *owned is cleared before Destroy/Receive. The input type
            # is zero, and callback() queries Type only on error0/command!=0.
            fields[3],fields[4],fields[11] = 0,0,self.accepted if action == commands.Action.SUBMIT else 0
            self.command_live = False
        else:
            raise AssertionError("no create/deserialize/execute allowed by this producer fixture")
        raw = commands.RESULT.pack(*fields)
        self.command_results.append((address,raw)); self.process.store(address,raw)
