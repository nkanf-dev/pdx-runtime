"""CPU-only ship rename consumer tests. No native/game functions execute."""
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pdx_runtime.v3_game._run import KnownResultError
from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.query_executor import Diagnostics, RetainedOwners
from pdx_runtime.v3_game.military import naval_design as warnavaldesign
from pdx_runtime.v3_game.military._warfare import WarfareRead
from pdx_runtime.v3_game._run import Run as OwnedRun


POPUP_ANY = ".?AU?$SAnyType@V?$CPdxPtr@VCShipChangeNamePopup@@@@@PdxAnyImpl@@"
SHIP_ANY = ".?AU?$SAnyType@V?$CPdxPtr@VCDataShip@@@@@PdxAnyImpl@@"
POPUP_BINDING = data.ObjectBinding(701, 900, retained=True)


def native_packet(initial, *, error=0, kind=data.Kind.RAW, handle=0,
                  type_id=-1, type_name="", snapshot=b"", total=None):
    """CPU transport supplies original wire headers; no NativeObject stand-in."""
    packet = bytearray(initial)
    header = list(data.REQUEST.unpack_from(packet))
    header[1], header[4], header[10], header[11] = error, type_id, 91, kind
    header[9] = len(snapshot) if total is None else total
    header[13], header[14], header[18], header[19] = 0, handle, header[9], len(snapshot)
    header[28] = type_name.encode().ljust(192, b"\0")
    packet[header[24]:header[24] + len(snapshot)] = snapshot
    data.REQUEST.pack_into(packet, 0, *header)
    return bytes(packet)


def owned_result(*, error=0, handle=900, type_name=POPUP_ANY, type_id=701):
    packet = native_packet(data._request(data.Action.CREATE_OWNED_CONTEXT,
        flags=5, output_bytes=8), error=error, handle=handle,
        type_name=type_name, type_id=type_id, snapshot=b"raw-any!")
    return data.DataResult.from_bytes(packet)


class FakeAPI:
    @staticmethod
    def _native_full_id(value):
        return value if type(value) is int and 0 <= value <= 0xffffffff else None


class FakeRun:
    owned_binding = OwnedRun.owned_binding
    _capture_values = OwnedRun._capture_values

    def __init__(self):
        self.native = object()
        self.process = object()
        self.adapters = {"data": "data"}
        self.values, self.value_adapters = [], {}
        self.calls = []
        self.field_calls = []
        self.generated = []
        self.validated = []
        self.retain_unknown = False
        self.observed_name = "HMS New"
        self.edit_result = SimpleNamespace(invoked=1, accepted=1, error=0)
        self.metadata = {
            name: SimpleNamespace(argument_count=count, callbacks=(1,))
            for name, count in warnavaldesign.SHIP_RENAME_METADATA["methods"].items()
        }

    async def call(self, label, factory):
        self.calls.append(label)
        value = factory()
        if hasattr(value, "__await__"):
            value = await value
        self._capture_values(value, None)
        if getattr(value, "error", 0) and label == "ship_rename.context":
            raise KnownResultError(label, value)
        return value

    async def capture(self, label, factory):
        return await self.call(label, factory)

    async def functions(self, named_type):
        self.calls.append(named_type + ".functions")
        return self.metadata

    def wrapper(self, result, named_type):
        raise AssertionError("host pointer decoder is not the owned binding protocol")

    async def evaluate(self, expression, bindings):
        raise AssertionError("unexpected evaluate: " + expression)

    async def origin(self, result, named_type):
        raise AssertionError("unexpected origin")

    async def field(self, named_type, method, bindings, kinds, *, expression=None):
        self.field_calls.append((named_type, method, tuple(bindings), tuple(kinds), expression))
        if named_type == "Ship" and method == "GetNameNoFormatting":
            return self.observed_name, None
        raise AssertionError("unexpected field: " + repr(self.field_calls[-1]))

    async def generated_owner(self, expression, bindings):
        self.generated.append((expression, tuple(bindings)))
        return "owner"

    async def validate_submit(self, owner, result, *, validate_only=False):
        self.validated.append((owner, validate_only))
        if validate_only:
            result.update(status="available", eligible=True, accepted=None)
            return False
        result.update(status="accepted", eligible=True, accepted=True)
        return True


class NavalDesignTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.run = FakeRun()
        self.ship = SimpleNamespace(object=700)
        self.current_ship = SimpleNamespace(object=701)
        self.context_result = owned_result()
        self.factory_calls = []
        self.action_calls = []

    def factory(self, native, source_handle, **options):
        self.factory_calls.append((native, source_handle, options))
        return self.context_result

    def invoke(self, native, expression, input_value, bindings, **options):
        self.action_calls.append((native, expression, input_value, tuple(bindings), options))
        return self.run.edit_result

    async def owned(self, api, label, operation):
        return await operation(self.run)

    async def execute(self, *, validate_only=False):
        with (
            patch.object(warnavaldesign, "owned_read", new=self.owned),
            patch.object(warnavaldesign, "_ship", AsyncMock(side_effect=[
                (7, self.ship), (8, self.current_ship)])),
            patch.object(data, "create_ship_name_context", side_effect=self.factory),
            patch.object(data, "invoke_action", side_effect=self.invoke),
        ):
            return await warnavaldesign.ship_rename(
                self.api, 0x81000007, "HMS New", validate_only=validate_only)


    async def test_metadata_and_getship_witness_are_not_rename_gates(self):
        self.run.functions = AsyncMock(side_effect=AssertionError("metadata witness"))
        result = await self.execute()
        self.run.functions.assert_not_awaited()
        self.assertTrue(result["accepted"])
        self.assertEqual(len(self.factory_calls), 1)
        self.assertEqual(len(self.action_calls), 1)

    async def test_edit_rejection_never_produces_or_submits_command(self):
        self.run.edit_result = SimpleNamespace(invoked=1, accepted=0, error=0)
        result = await self.execute()
        self.assertEqual(result["status"], "edit_rejected")
        self.assertIsNone(result["accepted"])
        self.assertEqual(self.run.generated, [])
        self.assertEqual(self.run.validated, [])

    async def test_edit_continues_only_for_exact_native_one_flags(self):
        for invoked, accepted in ((2, 1), (1, 2), (-1, 1), (1, -1),
                                  (None, 1), (1, None), (0, 1), (1, 0)):
            with self.subTest(invoked=invoked, accepted=accepted):
                self.run.edit_result = SimpleNamespace(
                    invoked=invoked, accepted=accepted, error=0)
                result = await self.execute()
                self.assertIs(result["edit_action"], self.run.edit_result)
                self.assertIs(result["native_effect"]["edit_invoked"], invoked)
                self.assertIs(result["native_effect"]["edit_accepted"], accepted)
                self.assertEqual(result["status"], "edit_rejected")
                self.assertIsNone(result["accepted"])
                self.assertEqual(self.run.generated, [])
                self.assertEqual(self.run.validated, [])


    async def test_edit_error_returns_original_result_and_does_not_claim_rejection(self):
        self.run.edit_result = SimpleNamespace(invoked=0, accepted=0, error=1168)
        result = await self.execute()
        self.assertIs(result["edit_action"], self.run.edit_result)
        self.assertEqual(result["status"], "native_result")
        self.assertEqual(result["native_error"], 1168)
        self.assertEqual(result["native_effect"]["edit_invoked"], 0)
        self.assertIs(type(result["native_effect"]["edit_invoked"]), int)
        self.assertEqual(result["native_effect"]["edit_accepted"], 0)
        self.assertIsNone(result["accepted"])
        self.assertEqual(self.run.generated, [])
        self.assertEqual(self.run.validated, [])

    async def test_native_accepted_edit_is_not_hidden_by_supplement_error(self):
        self.run.edit_result = SimpleNamespace(invoked=1, accepted=1, error=122)
        result = await self.execute()
        self.assertIs(result["edit_action"], self.run.edit_result)
        self.assertEqual(result["native_effect"]["edit_error"], 122)
        self.assertTrue(result["accepted"])
        self.assertEqual(len(self.run.generated), 1)
        self.assertEqual(len(self.run.validated), 1)


    async def test_readback_native_error_keeps_original_result_beside_acceptance(self):
        receipt = SimpleNamespace(error=1168)
        self.run.field = AsyncMock(side_effect=KnownResultError(
            "ship.name", receipt))
        result = await self.execute()
        self.assertTrue(result["accepted"])
        self.assertIs(result["observation_native_result"], receipt)
        self.assertEqual(result["observation_native_error"], 1168)


    async def test_actual_raw_header_uses_dynamic_type_without_host_pointer(self):
        self.context_result = owned_result(type_id=1701)
        result = await self.execute()
        self.assertEqual(self.context_result.kind, data.Kind.RAW)
        self.assertEqual(self.context_result.object, 0)
        self.assertEqual(self.context_result.value, b"raw-any!")
        self.assertIn(900, self.run.values)
        self.assertEqual(self.run.value_adapters[900], "data")
        self.assertEqual(self.action_calls[0][3],
                         (data.ObjectBinding(1701, 900, retained=True),))
        self.assertTrue(result["accepted"])


    async def test_name_readback_preserves_empty_name_and_unknown_match(self):
        for observed in ("", None):
            with self.subTest(observed=observed):
                self.run.observed_name = observed
                result = await self.execute()
                self.assertIs(result["native_effect"]["observed_name_raw"], observed)
                self.assertEqual(result["observed_name_matches"],
                                 False if observed == "" else None)


class ShipPacketTransport:
    """In-memory I/O only. Real low-level wrappers and DataQuery decoding run.

    Supplied headers are CPU fixtures, not evidence of game factory execution.
    The actual _Run owns adopted handles and command/cleanup decisions.
    """
    pid = 1

    def __init__(self, **options):
        self.options = options
        self.memory, self.packets, self.freed = {}, [], []
        self.exports, self.commands, self.queries, self.results = [], [], [], {}
        self.address = 0x10000
        self.request_id = 0
        self.native = SimpleNamespace(pid=1, request=self.native_request)
        self.process = self
        self.adapters = {"data": 0xA000, "commands": 0xB000}
        self.world = 77
        self.owner_pool, self.diagnostics = RetainedOwners(), Diagnostics()
        self.pending = RuntimeError("original ship operation pending")

    def allocate(self, size):
        self.address += 0x10000
        return self.address

    def write(self, address, packet):
        self.memory[address] = bytes(packet)
        self.packets.append((address, bytes(packet)))

    def read(self, address, size):
        return self.response(self.memory[address])

    def free(self, address):
        self.freed.append(address)

    def resolve_export(self, adapter, name, *, aliases=None):
        self.exports.append((adapter, name))
        return 0xA100

    def export_owner(self, address):
        return 0xA000

    def native_request(self, command):
        self.commands.append(command)
        if command.startswith("interface-callback "):
            self.request_id += 1
        return {"status": "ok", "error": 0, "request_id": self.request_id,
                "completed_request_id": self.request_id}

    @staticmethod
    def phase(packet):
        action = data.REQUEST.unpack_from(packet)[2]
        return {data.Action.CREATE_OWNED_CONTEXT: "context",
                data.Action.INVOKE_ACTION: "edit",
                data.Action.EVALUATE: "confirm",
                data.Action.COMMAND_VALUE: "carrier",
                data.Action.RELEASE: "release"}[action]

    def response(self, packet):
        h = data.REQUEST.unpack_from(packet)
        phase = self.phase(packet)
        if phase == "context":
            return native_packet(packet,
                error=self.options.get("context_error", 0),
                handle=self.options.get("context_handle", 222), type_id=701,
                type_name=self.options.get("context_type_name", POPUP_ANY),
                snapshot=self.options.get("context_snapshot", b"raw-any!"), total=8)
        if phase == "edit":
            raw = bytearray(native_packet(packet,
                error=self.options.get("edit_error", 0), handle=223,
                snapshot=b"draft", total=5))
            suffix = list(data.ACTION_SUFFIX.unpack_from(raw, data.REQUEST.size))
            suffix[2:5] = [3, self.options.get("edit_invoked", 1),
                           self.options.get("edit_accepted", 1)]
            data.ACTION_SUFFIX.pack_into(raw, data.REQUEST.size, *suffix)
            return bytes(raw)
        if phase == "confirm":
            return native_packet(packet,
                error=self.options.get("confirm_error", 0),
                handle=224, type_id=99, kind=data.Kind.COMMAND,
                type_name=".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@",
                snapshot=struct.pack("<3Q", 0x4000, 0x5000, 0x4018))
        if phase == "carrier":
            return native_packet(packet, handle=h[14],
                snapshot=struct.pack("<4Q", 0x4000, 0x5000, 0x4018, 77))
        return native_packet(packet, error=self.options.get("release_error", 0))

    async def query(self, label, factory, **kwargs):
        query = factory()
        self.queries.append((label, query))
        if not isinstance(query, data.DataQuery):
            return query
        packet = self.memory[query.result_address]
        phase = self.phase(packet)
        if phase == self.options.get("pending_phase"):
            self.pending.query = query
            if self.options.get("pending_partial"):
                partial = query.decode_result(self.response(packet))
                self.pending.partial_result = partial
            raise self.pending
        result = query.read()
        self.results.setdefault(phase, []).append(result)
        return result


class ShipNativeCommandFixture:
    """Only the native command boundary is supplied; _Run is production code."""
    def __init__(self, *, rejected=False, submit_error=0, submit_accepted=1):
        self.command, self.world, self.pending, self.attempt = 333, 77, None, None
        self.calls = []
        self.validation = SimpleNamespace(error=0, validated=0 if rejected else 1,
                                          reason="" if not rejected else "native rejection", command=333)
        self.submission = SimpleNamespace(error=submit_error, accepted=submit_accepted, command=0)

    def validate(self, *, capacity):
        self.calls.append(("validate", capacity))
        self.attempt = SimpleNamespace(outcome="owned")
        return self.validation

    def submit(self, *, flags):
        self.calls.append(("submit", flags))
        self.command = 0
        self.attempt = SimpleNamespace(outcome="consumed")
        return self.submission

    def destroy(self):
        self.calls.append(("destroy", None))
        self.command = 0
        self.attempt = SimpleNamespace(outcome="consumed")
        return SimpleNamespace(error=0, command=0)


class ShipRenameSharedPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, *, validate_only=False, observation=None,
                      observation_error=None, **options):
        self.transport = ShipPacketTransport(**options)
        self.owner = ShipNativeCommandFixture(
            rejected=options.get("validation_rejected", False),
            submit_error=options.get("submit_error", 0),
            submit_accepted=options.get("submit_accepted", 1))
        self.runs, self.clones = [], []

        async def ship(run, wanted):
            self.assertEqual(wanted, 0x81000007)
            fresh = bool(self.runs)
            if not fresh:
                self.runs.append(run)
            elif observation_error is not None:
                raise observation_error
            handle = 112 if fresh else 111
            packet = native_packet(data._request(data.Action.EVALUATE, output_bytes=8),
                handle=handle, type_id=37, type_name=SHIP_ANY, snapshot=b"ship-any")
            source = await run.capture("ship.source", lambda: data.DataResult.from_bytes(packet))
            return (8 if fresh else 7), run.owned_binding(source, "Ship")

        def clone(native, action, **kwargs):
            self.clones.append((action, kwargs))
            self.assertEqual(action, commands.Action.CLONE)
            self.assertEqual(kwargs["command"], 0x4000)
            self.assertEqual(kwargs["world"], 77)
            return SimpleNamespace(error=0, cloned_command=333, command=0,
                                   world=77, action=action)

        async def field(run, receiver, method, bindings, kinds, **kwargs):
            self.assertEqual((receiver, method), ("Ship", "GetNameNoFormatting"))
            self.assertEqual(bindings, (data.ObjectBinding(37, 112, retained=True),))
            return ("HMS New", None) if observation is None else observation

        with (patch.object(warnavaldesign, "_ship", ship),
              patch.object(commands, "request", clone),
              patch.object(commands.NativeCommand, "from_result", return_value=self.owner),
              patch.object(WarfareRead, "field", field)):
            return await warnavaldesign.ship_rename(
                SimpleNamespace(_ctx=self.transport), 0x81000007,
                "HMS New", validate_only=validate_only)

    def packets(self, action):
        return [packet for _, packet in self.transport.packets
                if data.REQUEST.unpack_from(packet)[2] == action]

    def released_handles(self):
        return [data.REQUEST.unpack_from(packet)[14]
                for packet in self.packets(data.Action.RELEASE)]

    async def test_real_factory_wire_raw_header_and_original_shared_command_pipeline(self):
        result = await self.execute(edit_error=234, submit_error=574)
        context = self.transport.results["context"][0]
        self.assertIsInstance(context, data.DataResult)
        self.assertEqual((context.action, context.flags, context.kind, context.object),
                         (22, 5, data.Kind.RAW, 0))
        self.assertEqual(context.value, b"raw-any!")
        self.assertIn(("ship_rename.context", context), self.runs[0].operations)
        packet = self.packets(data.Action.CREATE_OWNED_CONTEXT)
        self.assertEqual(len(packet), 1)
        h = data.REQUEST.unpack_from(packet[0])
        self.assertEqual((h[3], h[14]), (5, 111))
        self.assertIn((0xA000, "PdxV3DataCreateOwnedContextInterface"), self.transport.exports)
        for action in (data.Action.INVOKE_ACTION, data.Action.EVALUATE):
            selected = self.packets(action)[0]
            h = data.REQUEST.unpack_from(selected)
            self.assertEqual(data.BINDING_INPUT.unpack_from(selected, h[22]), (701, 1, 222))
        edit = self.packets(data.Action.INVOKE_ACTION)[0]
        suffix = data.ACTION_SUFFIX.unpack_from(edit, data.REQUEST.size)
        self.assertEqual(data.ANY_INPUT.unpack_from(edit, suffix[0])[0], data.UTF8_INPUT)
        self.assertIs(result["edit_action"], self.transport.results["edit"][0])
        self.assertEqual(result["edit_action"].error, 234)
        self.assertIs(result["native_result"], self.owner.submission)
        self.assertEqual(result["native_error"], 574)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["observed_name_matches"])
        self.assertEqual(self.owner.calls, [("validate", 65536), ("submit", 4)])
        self.assertEqual(len(self.clones), 1)
        self.assertEqual(self.released_handles(), [112, 224, 223, 222, 111])
        self.assertEqual(self.runs[0].status, "complete")
        self.assertNotIn(self.runs[0], self.transport.owner_pool.snapshot())

    async def test_partial_raw_payload_needs_no_pointer_or_copy_completeness_witness(self):
        result = await self.execute(context_snapshot=b"ra")
        context = self.transport.results["context"][0]
        self.assertEqual((context.kind, context.object, context.snapshot_bytes, context.total),
                         (data.Kind.RAW, 0, 2, 8))
        self.assertEqual(context.value, b"ra")
        self.assertTrue(result["accepted"])
        self.assertEqual(self.owner.calls, [("validate", 65536), ("submit", 4)])
        self.assertEqual(self.released_handles(), [112, 224, 223, 222, 111])

    async def test_wrong_owned_factory_header_is_released_without_edit_or_retag(self):
        result = await self.execute(context_type_name=
            ".?AU?$SAnyType@V?$CPdxPtr@VCDataShipChangeNamePopup@@@@@PdxAnyImpl@@")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(self.released_handles(), [222, 111])
        self.assertFalse(self.packets(data.Action.INVOKE_ACTION))
        self.assertFalse(self.clones)

    async def test_factory_partial_error_handles_adopt_and_release_without_type_gate(self):
        for error in (234, 574):
            with self.subTest(error=error):
                result = await self.execute(context_error=error, context_type_name="",
                                            context_snapshot=b"ra")
                context = self.transport.results["context"][0]
                self.assertIs(result["native_result"], context)
                self.assertEqual((context.error, context.value_handle, context.kind),
                                 (error, 222, data.Kind.RAW))
                self.assertEqual(context.snapshot, b"ra")
                self.assertEqual(context.total, 8)
                self.assertEqual(self.released_handles(), [222, 111])
                self.assertFalse(self.packets(data.Action.INVOKE_ACTION))
                self.assertFalse(self.clones)
                self.assertEqual(self.runs[0].status, "complete")

    async def test_factory_error_without_output_releases_source_once_not_input_echo(self):
        result = await self.execute(context_error=13, context_handle=0, context_type_name="")
        self.assertEqual(result["native_error"], 13)
        self.assertEqual(result["native_result"].value_handle, 0)
        self.assertEqual(self.released_handles(), [111])
        self.assertFalse(self.clones)

    async def test_pending_factory_retains_same_query_and_source_without_replay(self):
        with self.assertRaises(RuntimeError) as caught:
            await self.execute(pending_phase="context")
        self.assertIs(caught.exception, self.transport.pending)
        self.assertIsInstance(caught.exception.query, data.DataQuery)
        run = self.runs[0]
        self.assertIs(run.uncertain_query, caught.exception.query)
        self.assertTrue(run.retain_unknown)
        self.assertEqual(run.values, [111])
        self.assertIn(run, self.transport.owner_pool.snapshot())
        self.assertEqual(len(self.packets(data.Action.CREATE_OWNED_CONTEXT)), 1)
        self.assertFalse(self.packets(data.Action.INVOKE_ACTION))
        self.assertFalse(self.released_handles())

    async def test_fault_with_partial_factory_header_keeps_original_new_handle(self):
        with self.assertRaises(RuntimeError) as caught:
            await self.execute(pending_phase="context", pending_partial=True, context_error=234)
        run = self.runs[0]
        self.assertEqual(run.values, [111, 222])
        self.assertEqual(run.value_adapters[222], 0xA000)
        self.assertIs(run.uncertain_query, caught.exception.query)
        self.assertIs(run.operations[-1][1], caught.exception.partial_result)
        self.assertFalse(self.released_handles())
        self.assertFalse(self.clones)

    async def test_pending_edit_keeps_factory_source_and_original_query_no_confirm(self):
        with self.assertRaises(RuntimeError) as caught:
            await self.execute(pending_phase="edit")
        run = self.runs[0]
        self.assertIs(run.uncertain_query, caught.exception.query)
        self.assertEqual(run.values, [111, 222])
        self.assertEqual(len(self.packets(data.Action.INVOKE_ACTION)), 1)
        self.assertFalse(self.packets(data.Action.EVALUATE))
        self.assertFalse(self.clones)
        self.assertFalse(self.released_handles())

    async def test_pending_confirm_keeps_completed_edit_and_original_query(self):
        with self.assertRaises(RuntimeError) as caught:
            await self.execute(pending_phase="confirm")
        partial = caught.exception.ship_rename_result
        self.assertIs(partial["edit_action"], self.transport.results["edit"][0])
        self.assertIsNone(partial["accepted"])
        self.assertIs(self.runs[0].uncertain_query, caught.exception.query)
        self.assertEqual(self.runs[0].values, [111, 222, 223])
        self.assertEqual(len(self.packets(data.Action.EVALUATE)), 1)
        self.assertFalse(self.clones)
        self.assertFalse(self.released_handles())

    async def test_confirm_native_error_keeps_completed_edit_and_both_results(self):
        result = await self.execute(confirm_error=1168)
        self.assertIs(result["edit_action"], self.transport.results["edit"][0])
        self.assertIs(result["native_result"], self.transport.results["confirm"][0])
        self.assertEqual(result["native_error"], 1168)
        self.assertEqual(result["status"], "native_result")
        self.assertEqual(self.released_handles(), [224, 223, 222, 111])
        self.assertFalse(self.clones)

    async def test_validate_only_and_native_validation_reject_never_submit(self):
        for validate_only, rejected in ((True, False), (False, True)):
            with self.subTest(validate_only=validate_only, rejected=rejected):
                result = await self.execute(validate_only=validate_only,
                                            validation_rejected=rejected)
                self.assertEqual(result["status"], "rejected" if rejected else "available")
                self.assertIs(result["accepted"], False if rejected else None)
                self.assertEqual(self.owner.calls, [("validate", 65536), ("destroy", None)])
                self.assertEqual(len(self.clones), 1)
                self.assertEqual(self.released_handles(), [224, 223, 222, 111])
                self.assertNotIn("name_readback", result)

    async def test_native_edit_reject_keeps_result_and_releases_original_outputs(self):
        result = await self.execute(edit_accepted=0)
        self.assertEqual(result["status"], "edit_rejected")
        self.assertIs(result["native_result"], self.transport.results["edit"][0])
        self.assertIsNone(result["accepted"])
        self.assertEqual(self.released_handles(), [223, 222, 111])
        self.assertFalse(self.packets(data.Action.EVALUATE))
        self.assertFalse(self.clones)

    async def test_cleanup_failure_cannot_erase_accepted_primary_or_raw_error(self):
        result = await self.execute(release_error=5, submit_error=574)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["native_error"], 574)
        self.assertIs(result["native_result"], self.owner.submission)
        self.assertIs(result["cleanup"]["native_result"], self.transport.results["release"][0])
        self.assertEqual(result["cleanup"]["status"], "native_error_retained")
        self.assertIn(self.runs[0], self.transport.owner_pool.snapshot())
        self.assertEqual(self.owner.calls, [("validate", 65536), ("submit", 4)])
        self.assertEqual(len(self.clones), 1)

    async def test_validate_only_primary_survives_raw_cleanup_error(self):
        result = await self.execute(validate_only=True, release_error=5)
        self.assertEqual(result["status"], "available")
        self.assertIsNone(result["accepted"])
        self.assertIs(result["validation"], self.owner.validation)
        self.assertEqual(result["cleanup"]["native_result"].error, 5)
        self.assertEqual(self.owner.calls, [("validate", 65536), ("destroy", None)])
        self.assertEqual(len(self.clones), 1)
        self.assertIn(self.runs[0], self.transport.owner_pool.snapshot())

    async def test_partial_readback_does_not_assert_name_effect(self):
        result = await self.execute(observation=("HMS New", "partial native text"))
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["observed_name_matches"])
        self.assertEqual(result["name_readback"]["name_raw"], "HMS New")
        self.assertEqual(result["name_readback"]["unavailable"]["name"], "partial native text")

    async def test_pending_readback_keeps_primary_and_original_query_separately(self):
        pending = RuntimeError("original ship readback pending")
        pending.query = object()
        result = await self.execute(observation_error=pending)
        self.assertTrue(result["accepted"])
        self.assertIs(result["native_result"], self.owner.submission)
        self.assertIs(result["observation_query"], pending.query)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(self.runs[0].values, [111, 222, 223, 224])
        self.assertFalse(self.released_handles())
        self.assertEqual(len(self.clones), 1)


if __name__ == "__main__":
    unittest.main()
