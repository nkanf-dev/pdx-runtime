"""CPU-only business-path tests for the owned formation rename context."""
from __future__ import annotations

from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from pdx_runtime.v3_game._run import KnownResultError, SourceUnavailable
from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.query_executor import Diagnostics, RetainedOwners
from pdx_runtime.v3_game.military import formation_names as warrename
from pdx_runtime.v3_game.military._warfare import WarfareRead
from pdx_runtime.v3_game._run import Run as OwnedRun


POPUP_ANY = ".?AU?$SAnyType@V?$CPdxPtr@VCMilitaryFormationNameChangePopup@@@@@PdxAnyImpl@@"


class API:
    pass


class Run:
    wrapper = OwnedRun.wrapper
    owned_binding = OwnedRun.owned_binding

    def __init__(self):
        self.native = object()
        self.process = object()
        self.adapters = {"data": "data", "commands": "commands"}
        self.values = [222]
        self.value_adapters = {222: self.adapters["data"]}
        self.calls = []
        self.generated = []
        self.validated = []
        self.retain_unknown = False
        self.field_value = ("New Army", None)

    async def call(self, label, factory, **kwargs):
        self.calls.append(label)
        return factory()

    capture = call

    async def generated_owner(self, expression, bindings):
        self.generated.append((expression, bindings))
        return NS(command=333, pending=None, attempt=None)

    async def validate_submit(self, owner, result, *, validate_only=False):
        self.validated.append((owner, validate_only))
        if validate_only:
            result.update(status="available", eligible=True, accepted=None)
            return False
        result.update(status="accepted", eligible=True, accepted=True)
        return True

    async def functions(self, named_type):
        return {"GetNameNoFormatting": object()}

    async def field(self, receiver, method, bindings, kinds, *, arguments=0, expression=None):
        return self.field_value


class FormationRenameTests(unittest.IsolatedAsyncioTestCase):
    def immediate(self, run):
        async def owned_read(api, label, operation):
            run.api = api
            return await operation(run)
        return owned_read

    @staticmethod
    def popup_result(*, native_name=POPUP_ANY, kind=data.Kind.OBJECT, type_id=701):
        snapshot = b"original copied Any payload" if kind == data.Kind.RAW else b""
        header = list(data.REQUEST.unpack(bytes(data.REQUEST.size)))
        header[0] = data.REQUEST.size + len(snapshot)
        header[2] = data.Action.CREATE_OWNED_CONTEXT
        header[3] = data.OwnedContextKind.MILITARY_FORMATION_NAME
        header[4] = type_id
        header[9] = len(snapshot)
        header[11] = kind
        header[13] = 0xABC0 if kind == data.Kind.OBJECT else 0
        header[14] = 222
        header[19] = len(snapshot)
        header[24] = data.REQUEST.size
        header[25] = len(snapshot)
        header[28] = native_name.encode("ascii").ljust(192, b"\0")
        return data.DataResult.from_bytes(data.REQUEST.pack(*header) + snapshot)


    async def test_edit_rejection_never_materializes_confirm(self):
        run = Run()

        async def formation(current, wanted, **kwargs):
            return "army", data.ObjectBinding(37, 111, retained=True)

        with (patch.object(warrename, "owned_read", self.immediate(run)),
              patch.object(warrename, "_formation", formation),
              patch.object(warrename.data, "create_military_formation_name_context",
                           lambda *args, **kwargs: self.popup_result()),
              patch.object(warrename.data, "invoke_action",
                           lambda *args, **kwargs: NS(error=0, invoked=1, accepted=0,
                                                     value_handle=0))):
            result = await warrename.formation_rename(API(), 17, "Rejected")

        self.assertEqual(result["status"], "edit_rejected")
        self.assertFalse(run.generated)
        self.assertFalse(run.validated)


    async def test_native_edit_failure_keeps_original_error_without_confirm(self):
        run = Run()
        edited = NS(error=5, invoked=0, accepted=0, value_handle=0)

        async def formation(current, wanted, **kwargs):
            return "army", data.ObjectBinding(37, 111, retained=True)

        with (patch.object(warrename, "owned_read", self.immediate(run)),
              patch.object(warrename, "_formation", formation),
              patch.object(warrename.data, "create_military_formation_name_context",
                           lambda *args, **kwargs: self.popup_result()),
              patch.object(warrename.data, "invoke_action", return_value=edited)):
            result = await warrename.formation_rename(API(), 17, "New Army")

        self.assertIs(result["edit_action"], edited)
        self.assertIs(result["native_result"], edited)
        self.assertEqual(result["native_error"], 5)
        self.assertIsNone(result["accepted"])
        self.assertEqual(result["status"], "native_result")
        self.assertFalse(run.generated)
        self.assertFalse(run.validated)

    async def test_partial_name_readback_is_not_a_proven_name_mismatch(self):
        run = Run()
        run.field_value = ("New", "native text snapshot is partial")

        async def formation(current, wanted, **kwargs):
            return "army", data.ObjectBinding(37, 111, retained=True)

        with (patch.object(warrename, "owned_read", self.immediate(run)),
              patch.object(warrename, "_formation", formation),
              patch.object(warrename.data, "create_military_formation_name_context",
                           lambda *args, **kwargs: self.popup_result()),
              patch.object(warrename.data, "invoke_action",
                           return_value=NS(error=0, invoked=1, accepted=1, value_handle=223))):
            result = await warrename.formation_rename(API(), 17, "New Army")

        self.assertTrue(result["accepted"])
        self.assertIsNone(result["observed_name_matches"])
        self.assertEqual(result["name_readback"]["name_raw"], "New")
        self.assertIn("partial", result["name_readback"]["unavailable"]["name"])

    async def test_confirm_failure_keeps_completed_edit_receipt(self):
        run = Run()
        edited = NS(error=0, invoked=1, accepted=1, value_handle=223)
        native_failure = NS(error=1168)

        async def formation(current, wanted, **kwargs):
            return "army", data.ObjectBinding(37, 111, retained=True)

        async def generated(*args):
            raise KnownResultError("Confirm", native_failure)

        run.generated_owner = generated
        with (patch.object(warrename, "owned_read", self.immediate(run)),
              patch.object(warrename, "_formation", formation),
              patch.object(warrename.data, "create_military_formation_name_context",
                           lambda *args, **kwargs: self.popup_result()),
              patch.object(warrename.data, "invoke_action", return_value=edited)):
            result = await warrename.formation_rename(API(), 17, "New Army")

        self.assertIs(result["edit_action"], edited)
        self.assertIs(result["native_result"], native_failure)
        self.assertEqual(result["native_error"], 1168)
        self.assertIsNone(result["accepted"])
        self.assertFalse(run.validated)

    async def test_invalid_name_type_fails_before_acquiring_an_owner(self):
        with patch.object(warrename, "owned_read", side_effect=AssertionError("no native work")):
            with self.assertRaisesRegex(TypeError, "new_name must be a string"):
                await warrename.formation_rename(API(), 17, 123)

    async def test_only_exact_native_invoked_and_accepted_flags_allow_confirm(self):
        for invoked, accepted in ((2, 1), (1, 2), (0, 1), (1, 0)):
            with self.subTest(invoked=invoked, accepted=accepted):
                run = Run()

                async def formation(current, wanted, **kwargs):
                    return "army", data.ObjectBinding(37, 111, retained=True)

                with (patch.object(warrename, "owned_read", self.immediate(run)),
                      patch.object(warrename, "_formation", formation),
                      patch.object(warrename.data, "create_military_formation_name_context",
                                   lambda *args, **kwargs: self.popup_result()),
                      patch.object(warrename.data, "invoke_action", return_value=NS(
                          error=0, invoked=invoked, accepted=accepted, value_handle=0))):
                    result = await warrename.formation_rename(API(), 17, "New Army")

                self.assertIsNone(result["accepted"])
                self.assertFalse(run.generated)
                self.assertFalse(run.validated)


class RenameScopeContext:
    """CPU transport only; ownership and command logic stay in the real _Run."""
    def __init__(self):
        self.native = object()
        self.process = object()
        self.adapters = {"data": "data-module", "commands": "commands-module"}
        self.world = 77
        self.owner_pool = RetainedOwners()
        self.diagnostics = Diagnostics()
        self.queries = []

    async def query(self, label, factory, **kwargs):
        query = factory()
        self.queries.append((label, query))
        error = getattr(query, "_test_error", None)
        if error is not None:
            error.query = query
            raise error
        return getattr(query, "_test_result", query)


def transport_query(result, *, adapter="data-module", error=None):
    query = object.__new__(data.DataQuery)
    query.adapter = adapter
    query._test_result = result
    query._test_error = error
    return query


def scope_result(action, *, handle=0, error=0, value=None):
    return NS(action=action, error=error, value_handle=handle, value=value)


class RenameCommand:
    """Fake native receipts, used by the real generated_owner/validate_submit."""
    def __init__(self, *, submit_error=0):
        self.command = 333
        self.world = 77
        self.pending = None
        self.attempt = None
        self.calls = []
        self.validation = NS(error=0, validated=1, reason="", command=333)
        self.submission = NS(error=submit_error, accepted=1, command=0)

    def validate(self, *, capacity):
        self.calls.append(("validate", capacity))
        self.attempt = NS(outcome="owned")
        return self.validation

    def submit(self, *, flags):
        self.calls.append(("submit", flags))
        self.command = 0
        self.attempt = NS(outcome="consumed")
        return self.submission

    def destroy(self):
        self.calls.append(("destroy", None))
        self.command = 0
        self.attempt = NS(outcome="consumed")
        return NS(error=0, command=0)


class FormationRenameSharedScopeTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, *, edit_error=0, submit_error=0, release_error=0,
                      pending_edit=False, pending_confirm=False, validate_only=False,
                      observation_error=None, context_result=None,
                      context_adapter="data-module"):
        self.ctx = RenameScopeContext()
        api = NS(_ctx=self.ctx)
        self.runs, self.releases, self.requests, self.edits = [], [], [], []
        self.owner = RenameCommand(submit_error=submit_error)
        self.context_result = (FormationRenameTests.popup_result()
                               if context_result is None else context_result)
        self.pending = RuntimeError("native edit is pending") if pending_edit else None
        self.confirm_pending = RuntimeError("native Confirm is pending") if pending_confirm else None
        self.edit = data.ActionResult(
            scope_result(data.Action.INVOKE_ACTION, handle=223, error=edit_error),
            0, 0, 3, 1, 1, data.UTF8_INPUT)
        source_name = ".?AU?$SAnyType@V?$CPdxPtr@VCDataMilitaryFormation@@@@@PdxAnyImpl@@"

        async def formation(run, wanted, **kwargs):
            self.assertEqual(wanted, 17)
            fresh = bool(self.runs)
            if not fresh:
                self.runs.append(run)
            elif observation_error is not None:
                raise observation_error
            handle = 112 if fresh else 111
            source = scope_result(data.Action.EVALUATE, handle=handle,
                value=data.NativeObject(0xF000 + handle, 37, handle, source_name))
            captured = await run.capture("formation", lambda: transport_query(source))
            return "army", run.wrapper(captured, "MilitaryFormation").binding()

        def create_context(native, formation_handle, **kwargs):
            self.assertEqual(formation_handle, 111)
            return transport_query(self.context_result, adapter=context_adapter)

        def edit(native, expression, typed_input, bindings, **kwargs):
            self.edits.append((expression, typed_input, bindings))
            return transport_query(self.edit, error=self.pending)

        def evaluate(native, expression, bindings, **kwargs):
            self.assertEqual(expression, "MilitaryFormationChangeNamePopup.Confirm")
            self.assertEqual(bindings, (data.ObjectBinding(
                self.context_result.type_id, 222, retained=True),))
            carrier = data.NativeDataCommand(0x4000, 0x5000, 0x4018, 224,
                ".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@")
            return transport_query(scope_result(data.Action.EVALUATE, handle=224, value=carrier),
                                   error=self.confirm_pending)

        def command_value(native, handle, **kwargs):
            self.assertEqual(handle, 224)
            carrier = data.NativeDataCommand(0x4000, 0x5000, 0x4018, 224,
                ".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@", 77)
            return transport_query(scope_result(data.Action.COMMAND_VALUE, value=carrier))

        def request(native, action, **kwargs):
            self.requests.append((action, kwargs))
            self.assertEqual(action, commands.Action.CLONE)
            self.assertEqual(kwargs["world"], 77)
            return NS(error=0, command=0, cloned_command=333, world=77)

        def release(native, handle, **kwargs):
            self.releases.append((handle, kwargs["adapter"]))
            return transport_query(scope_result(data.Action.RELEASE, error=release_error))

        async def functions(run, name):
            return {"GetNameNoFormatting": object()}

        async def field(run, *args, **kwargs):
            return "新军团 \"alpha\"", None

        with (patch.object(warrename, "_formation", formation),
              patch.multiple(data, create_military_formation_name_context=create_context,
                             invoke_action=edit, evaluate=evaluate,
                             command_value=command_value, release=release),
              patch.object(commands, "request", request),
              patch.object(commands.NativeCommand, "from_result", return_value=self.owner),
              patch.object(WarfareRead, "functions", functions),
              patch.object(WarfareRead, "field", field)):
            return await warrename.formation_rename(
                api, 17, "新军团 \"alpha\"", validate_only=validate_only)

    async def test_actual_run_captures_action22_and8_then_one_clone_validate_submit(self):
        result = await self.execute(edit_error=234, submit_error=574)
        run = self.runs[0]
        self.assertIs(result["edit_action"], self.edit)
        self.assertEqual(result["edit_action"].error, 234)
        self.assertIs(result["native_result"], self.owner.submission)
        self.assertEqual(result["native_error"], 574)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["observed_name_matches"])
        self.assertEqual(self.owner.calls, [("validate", 65536), ("submit", 4)])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.edits), 1)
        self.assertEqual(self.edits[0][1].text, "新军团 \"alpha\"")
        self.assertEqual([handle for handle, _ in self.releases], [112, 224, 223, 222, 111])
        self.assertEqual(run.status, "complete")
        self.assertNotIn(run, self.ctx.owner_pool.snapshot())

    async def test_actual_run_cleanup_error_cannot_erase_accepted_rename(self):
        result = await self.execute(release_error=5)
        run = self.runs[0]
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted")
        self.assertIs(result["native_result"], self.owner.submission)
        self.assertTrue(result["observed_name_matches"])
        self.assertEqual(result["cleanup"]["native_result"].error, 5)
        self.assertEqual(result["cleanup"]["status"], "native_error_retained")
        self.assertIs(run.public_result, result)
        self.assertIn(run, self.ctx.owner_pool.snapshot())
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.owner.calls, [("validate", 65536), ("submit", 4)])

    async def test_actual_run_pending_edit_retains_source_and_context_without_replay(self):
        with self.assertRaisesRegex(RuntimeError, "native edit is pending") as raised:
            await self.execute(pending_edit=True)
        run = self.runs[0]
        self.assertTrue(run.retain_unknown)
        self.assertIs(run.uncertain_query, raised.exception.query)
        self.assertEqual(run.values, [111, 222])
        self.assertIn(run, self.ctx.owner_pool.snapshot())
        self.assertEqual(len(self.edits), 1)
        self.assertFalse(self.requests)
        self.assertFalse(self.releases)
        self.assertFalse(self.owner.calls)

    async def test_actual_run_validate_only_destroys_owner_without_submit_or_readback(self):
        result = await self.execute(validate_only=True)
        self.assertEqual(result["status"], "available")
        self.assertIsNone(result["accepted"])
        self.assertNotIn("name_readback", result)
        self.assertEqual(self.owner.calls, [("validate", 65536), ("destroy", None)])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual([handle for handle, _ in self.releases], [224, 223, 222, 111])
        self.assertEqual(self.runs[0].status, "complete")

    async def test_actual_run_unknown_readback_preserves_submission_and_owner_graph(self):
        result = await self.execute(observation_error=RuntimeError("readback unknown"))
        run = self.runs[0]
        self.assertTrue(result["accepted"])
        self.assertIs(result["native_result"], self.owner.submission)
        self.assertEqual(result["observation_error"], "readback unknown")
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(self.owner.calls, [("validate", 65536), ("submit", 4)])
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.releases)
        self.assertEqual(run.values, [111, 222, 223, 224])
        self.assertIn(run, self.ctx.owner_pool.snapshot())

    async def test_actual_run_native_readback_failure_keeps_both_original_results(self):
        observation = NS(error=1168)
        result = await self.execute(observation_error=KnownResultError(
            "formation.readback", observation))
        self.assertTrue(result["accepted"])
        self.assertIs(result["native_result"], self.owner.submission)
        self.assertIs(result["observation_native_result"], observation)
        self.assertEqual(result["observation_native_error"], 1168)
        self.assertEqual(self.owner.calls, [("validate", 65536), ("submit", 4)])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.runs[0].status, "complete")

    async def test_actual_run_pending_confirm_preserves_completed_edit_and_original_query(self):
        with self.assertRaisesRegex(RuntimeError, "native Confirm is pending") as raised:
            await self.execute(pending_confirm=True)
        run = self.runs[0]
        self.assertIs(raised.exception, self.confirm_pending)
        partial = raised.exception.formation_rename_result
        self.assertIs(partial["edit_action"], self.edit)
        self.assertIsNone(partial["accepted"])
        self.assertTrue(run.retain_unknown)
        self.assertIs(run.uncertain_query, raised.exception.query)
        self.assertEqual(run.values, [111, 222, 223])
        self.assertIn(run, self.ctx.owner_pool.snapshot())
        self.assertEqual(len(self.edits), 1)
        self.assertFalse(self.requests)
        self.assertFalse(self.releases)
        self.assertFalse(self.owner.calls)

    async def test_actual_raw_context_uses_original_header_dynamic_type_and_retained_handle(self):
        context = FormationRenameTests.popup_result(kind=data.Kind.RAW, type_id=1701)
        self.assertIsInstance(context, data.DataResult)
        self.assertEqual(context.object, 0)
        self.assertIsInstance(context.value, bytes)
        self.assertNotIsInstance(context.value, data.NativeObject)
        result = await self.execute(context_result=context)
        run = self.runs[0]
        self.assertTrue(result["accepted"])
        self.assertTrue(result["observed_name_matches"])
        self.assertEqual(self.edits[0][2], (data.ObjectBinding(1701, 222, retained=True),))
        self.assertEqual(self.owner.calls, [("validate", 65536), ("submit", 4)])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.edits), 1)
        self.assertIn(("formation_rename.context", context), run.operations)
        self.assertEqual((context.kind, context.object, context.type_id, context.value),
                         (data.Kind.RAW, 0, 1701, b"original copied Any payload"))
        self.assertEqual(run.status, "complete")

    async def test_raw_context_wrong_original_header_is_rejected_before_edit_or_confirm(self):
        context = FormationRenameTests.popup_result(kind=data.Kind.RAW,
            native_name=".?AU?$SAnyType@PEAVCMilitaryFormationNameChangePopup@@@PdxAnyImpl@@")
        result = await self.execute(context_result=context)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("exact owned popup context", result["reason"])
        self.assertFalse(self.edits)
        self.assertFalse(self.requests)
        self.assertFalse(self.owner.calls)
        self.assertEqual(self.runs[0].status, "complete")

    async def test_missing_original_context_producer_is_rejected_without_pointer_fallback(self):
        run = Run()
        run.value_adapters.clear()

        async def formation(current, wanted, **kwargs):
            return "army", data.ObjectBinding(37, 111, retained=True)

        with (patch.object(warrename, "owned_read", FormationRenameTests.immediate(self, run)),
              patch.object(warrename, "_formation", formation),
              patch.object(data, "create_military_formation_name_context",
                           return_value=FormationRenameTests.popup_result()),
              patch.object(data, "invoke_action", side_effect=AssertionError("edit forbidden"))):
            with self.assertRaisesRegex(SourceUnavailable, "exact owned popup context"):
                await warrename.formation_rename(API(), 17, "No producer")
        self.assertFalse(run.generated)
        self.assertFalse(run.validated)

    async def test_raw_context_without_dynamic_type_is_rejected_before_edit(self):
        context = FormationRenameTests.popup_result(kind=data.Kind.RAW, type_id=-1)
        result = await self.execute(context_result=context)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("exact owned popup context", result["reason"])
        self.assertFalse(self.edits)
        self.assertFalse(self.requests)
        self.assertFalse(self.owner.calls)

    async def test_existing_factory_wire_captures_original_source_graph_with_header_binding(self):
        ctx = RenameScopeContext()
        ctx.native = Mock(pid=1)
        ctx.native.request.side_effect = lambda command: (
            {"status": "ok", "request_id": 7, "error": 0}
            if command.startswith("interface-callback ")
            else {"status": "ok", "completed_request_id": 7})
        ctx.process = Mock(pid=1)
        ctx.process.allocate.return_value = 0x2000
        ctx.process.resolve_export.return_value = 0xA100
        ctx.process.export_owner.return_value = 0xA000
        ctx.adapters["data"] = 0xA000
        run = WarfareRead(NS(_ctx=ctx), "formation_rename.factory_wire")
        source_header = list(data.REQUEST.unpack(bytes(data.REQUEST.size)))
        source_header[0] = data.REQUEST.size
        source_header[2] = data.Action.EVALUATE
        source_header[4] = 37
        source_header[11] = data.Kind.RAW
        source_header[14] = 111
        source_header[28] = (
            b".?AU?$SAnyType@V?$CPdxPtr@VCDataMilitaryFormation@@@@@PdxAnyImpl@@"
            .ljust(192, b"\0"))
        source = data.DataResult.from_bytes(data.REQUEST.pack(*source_header))
        await run.capture("formation.source", lambda: transport_query(source, adapter=0xA000))

        def original_factory():
            query = data.create_military_formation_name_context(
                run.native, source.value_handle, adapter=run.adapters["data"], process=run.process)
            wire = bytearray(ctx.process.write.call_args.args[1])
            header = list(data.REQUEST.unpack_from(wire))
            self.assertEqual((header[2], header[3], header[14]),
                             (data.Action.CREATE_OWNED_CONTEXT,
                              data.OwnedContextKind.MILITARY_FORMATION_NAME, 111))
            snapshot = b"original opaque popup Any"
            header[1] = 0
            header[4] = 1901
            header[9] = len(snapshot)
            header[11] = data.Kind.RAW
            header[13] = 0
            header[14] = 222
            header[19] = len(snapshot)
            header[28] = POPUP_ANY.encode("ascii").ljust(192, b"\0")
            offset = header[24]
            wire[offset:offset + len(snapshot)] = snapshot
            data.REQUEST.pack_into(wire, 0, *header)
            ctx.process.read.return_value = bytes(wire)
            result = query.read()
            self.assertIsInstance(result, data.DataResult)
            self.assertEqual(query.adapter, 0xA000)
            return transport_query(result, adapter=query.adapter)

        context = await run.call("formation_rename.context", original_factory)
        self.assertEqual((context.kind, context.object, context.value),
                         (data.Kind.RAW, 0, b"original opaque popup Any"))
        self.assertEqual(run.owned_binding(context, "MilitaryFormationChangeNamePopup"),
                         data.ObjectBinding(1901, 222, retained=True))
        self.assertEqual(run.values, [111, 222])
        self.assertEqual(run.value_adapters, {111: 0xA000, 222: 0xA000})
        self.assertIn(run, ctx.owner_pool.snapshot())
        self.assertEqual(ctx.process.free.call_count, 1)
        self.assertEqual(sum(call.args[0].startswith("interface-callback ")
                             for call in ctx.native.request.call_args_list), 1)
        released = []

        def release(native, handle, **kwargs):
            released.append((handle, kwargs["adapter"]))
            return transport_query(scope_result(data.Action.RELEASE), adapter=0xA000)

        with patch.object(data, "release", release):
            await run.finish({"status": "observed"})
        self.assertEqual(released, [(222, 0xA000), (111, 0xA000)])
        self.assertEqual(run.status, "complete")
        self.assertNotIn(run, ctx.owner_pool.snapshot())

    async def test_actual_raw_context_missing_producer_metadata_retains_graph_without_edit(self):
        context = FormationRenameTests.popup_result(kind=data.Kind.RAW, type_id=1701)
        result = await self.execute(context_result=context, context_adapter=None)
        run = self.runs[0]
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("exact owned popup context", result["reason"])
        self.assertFalse(self.edits)
        self.assertFalse(self.requests)
        self.assertFalse(self.owner.calls)
        self.assertEqual(run.values, [111, 222])
        self.assertIsNone(run.value_adapters[222])
        self.assertTrue(run.retain_unknown)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertIn(run, self.ctx.owner_pool.snapshot())
        self.assertFalse(self.releases)


if __name__ == "__main__":
    unittest.main()
