"""CPU-only lifecycle tests. Every OS/bridge/native interface is a fake."""
from __future__ import annotations
import asyncio
import threading
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pdx_runtime.v3_game import V3Game
from pdx_runtime.v3_game import lifecycle as api
from pdx_runtime.v3_game._core import PendingOperation, NativeRejection
from pdx_runtime import v3_lifecycle as native
from pdx_runtime.native_bridge import BridgeError, LibraryLoadPending
from pdx_runtime.session import RuntimeSession
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES


def life(**changes):
    strings = {"path", "resolved_path", "date_text"}
    values = {f.name: "" if f.name in strings else 0 for f in fields(native.LifecycleResult)}
    values.update(size=native.LIFECYCLE_RESULT.size, flags=1)
    values.update(changes)
    return native.LifecycleResult(**values)


def value(**changes):
    return SimpleNamespace(error=0, **changes)


class Query:
    def __init__(self, result):
        self.result = result
        self.reads = 0
        self.started = threading.Event()
    def read(self):
        self.started.set()
        self.reads += 1
        return self.result


class FakeNative:
    pid = 42
    def __init__(self):
        self.closed = 0
        self.handle = 111
        self.world = 0
        self.request_id = 0
        self.entered = 0
        self.wire_commands = []
    def request(self, command):
        self.wire_commands.append(command)
        if command == "world-status":
            return self._world_status()
        if command == "world-enter 0x0":
            return self._enter_world()
        raise AssertionError(f"unexpected wire command {command!r}")
    def status(self):
        return {"status": "ok", "dll_base": "0x10000", "main_module_base": "0x140000000"}
    def _world_status(self):
        return {"status": "ok", "world": hex(self.world), "installed": 1,
                "interface_thread_id": 17, "pending_operation": 0, "transition_pending": 0,
                "phase": 8 if self.world else 0, "request_id": self.request_id,
                "completed_request_id": self.request_id, "world_error": 0}
    def _enter_world(self):
        self.entered += 1
        self.world = 0x90000
        self.request_id += 1
        return {"status": "ok", "error": 0, "request_id": self.request_id}
    def close(self):
        self.closed += 1
        self.handle = None


class FakeProcess:
    def __init__(self, pid=42):
        self.pid = pid
        self.handle = 77
        self.closed = 0
        self.exit_code = None
        self.pending_library_loads = ()
        self.module = {"base": 0x10000, "name": "pdx_runtime.dll", "path": str(api.runtime_dll_path())}
        self.loads = 0
        self.starts = 0
    def poll(self): return self.exit_code
    def close(self): self.closed += 1; self.handle = None
    def modules(self): return [self.module]
    def resolve_export(self, module, symbol, *, aliases=None):
        if aliases is None:
            if (module, symbol) != (self.module["base"], "PdxRuntimeInitialize"):
                raise AssertionError("only the exact loaded carrier initializer uses raw resolution")
        elif aliases is not V3_EXPORT_ALIASES:
            raise AssertionError("expected explicit V3 aliases")
        return 0x20000
    def start_thread(self, address):
        self.starts += 1
        return SimpleNamespace(poll=lambda: 0, close=Mock())
    def load_library(self, path, timeout_ms=0):
        self.loads += 1
        return self.module


def installed(process):
    return {"status": "ready", "pending": [], "stages": {"interface": "ready", "session": "ready"},
            "interface": {"world": "0x0"},
            "modules": {role: process.module for role in ("carrier", *api.v3_runtime.ARTIFACTS)}}


def game_fixture(timeout=.05):
    bridge, process = FakeNative(), FakeProcess()
    session = RuntimeSession(bridge)
    game = V3Game(session, process=process, timeout=timeout)
    game._ctx.poll_interval = 0
    return game, session, bridge, process


async def settled(operation):
    """Keep fake native patches alive through the original operation's result."""
    result = await operation
    if isinstance(result, PendingOperation):
        result = await result.resume(timeout=1)
    if isinstance(result, PendingOperation):
        raise AssertionError("fake operation did not settle within the fixture budget")
    return result


class DiscoveryTests(unittest.TestCase):
    def test_process_choices_are_concrete_without_connection(self):
        os_api = Mock()
        os_api.find_processes.return_value = [50, 42]
        os_api.main_module.side_effect = lambda pid: {"path": f"C:/Games/{pid}/victoria3.exe"}
        self.assertEqual(api.discover_processes(os_api), [
            {"pid": 42, "path": "C:/Games/42/victoria3.exe"},
            {"pid": 50, "path": "C:/Games/50/victoria3.exe"}])
        os_api.find_processes.assert_called_once_with("victoria3.exe")

    def test_invalid_save_names(self):
        for name in ("../original", "C:/save", "/save", "x\x00z", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                api._save_name(name)


class EntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_running_game_is_automatic_and_owned(self):
        bridge, process = FakeNative(), FakeProcess()
        with patch.object(api, "discover_processes", return_value=[{"pid": 42, "path": "victoria3.exe"}]), \
             patch.object(api, "RemoteProcess", return_value=process), \
             patch.object(api.NativeBridge, "connect", return_value=bridge) as connect, \
             patch.object(api.v3_runtime, "install", return_value=installed(process)), \
             patch.object(native, "observe", return_value=Query(life())):
            game = await V3Game.attach()
        self.assertIs(game.raw.process, process)
        self.assertIs(game.raw.native, bridge)
        self.assertTrue(game.lifecycle.owns_process)
        self.assertTrue(game.lifecycle.owns_session)
        self.assertEqual(game._ctx.adapters["data"], "pdx_v3_data.dll")
        connect.assert_called_once_with(42, timeout_ms=0)
        self.assertEqual(process.loads, 0)
        await game.detach()
        self.assertEqual((bridge.closed, process.closed), (1, 1))

    async def test_several_processes_never_guess_or_connect(self):
        choices = [{"pid": 42, "path": "C:/A/victoria3.exe"}, {"pid": 50, "path": "D:/B/victoria3.exe"}]
        with patch.object(api, "discover_processes", return_value=choices), \
             patch.object(api.NativeBridge, "connect") as connect:
            with self.assertRaises(api.ProcessSelectionError) as caught:
                await V3Game.attach()
        self.assertEqual(caught.exception.choices, tuple(choices))
        self.assertIn("50: D:/B", str(caught.exception))
        connect.assert_not_called()

    async def test_no_running_process_offers_launch(self):
        with patch.object(api, "discover_processes", return_value=[]):
            with self.assertRaisesRegex(api.ProcessSelectionError, "launch"):
                await V3Game.attach()

    async def test_existing_session_and_process_are_reused_not_owned(self):
        game, session, bridge, process = game_fixture()
        with patch.object(api.NativeBridge, "connect") as connect, \
             patch.object(api.v3_runtime, "install", return_value=installed(process)), \
             patch.object(native, "observe", return_value=Query(life())):
            wrapped = await V3Game.attach(session=session, process=process)
        connect.assert_not_called()
        self.assertIs(wrapped.raw.session, session)
        self.assertIs(wrapped.raw.process, process)
        await wrapped.detach()
        self.assertEqual((bridge.closed, process.closed), (0, 0))

    async def test_session_namespace_process_is_borrowed(self):
        _, session, bridge, process = game_fixture()
        session.namespace["process"] = process
        with patch.object(api.v3_runtime, "install", return_value=installed(process)), \
             patch.object(native, "observe", return_value=Query(life())):
            game = await V3Game.attach(session=session)
        await game.detach()
        self.assertIs(game.raw.process, process)
        self.assertEqual(process.closed, 0)

    async def test_launch_keeps_original_os_witness(self):
        bridge, process = FakeNative(), FakeProcess()
        with patch.object(api.RemoteProcess, "launch", return_value=process) as launch, \
             patch.object(api.NativeBridge, "connect", return_value=bridge), \
             patch.object(api.v3_runtime, "install", return_value=installed(process)), \
             patch.object(native, "observe", return_value=Query(life())):
            game = await V3Game.launch("C:/Games/victoria3.exe", arguments=("-debug_mode",))
        launch.assert_called_once_with(Path("C:/Games/victoria3.exe"), ("-debug_mode",), cwd=None, creation_flags=0)
        self.assertIs(game.raw.process, process)
        await game.detach()
        self.assertEqual(process.closed, 1)
        self.assertIsNone(process.exit_code)

    async def test_pending_original_load_is_continued_without_duplicate_load(self):
        bridge, process = FakeNative(), FakeProcess()
        class Attempt:
            path = api.runtime_dll_path()
            def __init__(self):
                self.count = 0; self.state = "waiting"; self.thread = None
                self.argument_address = 0x40000
                self.loader_exit_dword = None
                self.last_error = None
            def resume(self):
                self.count += 1
                if self.count == 1: raise LibraryLoadPending(self)
                return process.module
        original = Attempt()
        process.pending_library_loads = (original,)
        with patch.object(api.NativeBridge, "connect", side_effect=[BridgeError("WaitNamedPipeW", 2), bridge]):
            connected = await api._connect(api._EntryContext(.1, 0), process, None)
        self.assertIs(connected, bridge)
        self.assertEqual(original.count, 2)
        self.assertEqual(process.loads, 0)
        self.assertEqual(process.starts, 1)

    async def test_new_load_timeout_returns_original_pending_operation(self):
        bridge, process = FakeNative(), FakeProcess()
        gate = asyncio.Event()
        calls = []
        async def install(*args, **kwargs):
            calls.append(process)
            await gate.wait()
            return installed(process)
        with patch.object(api.NativeBridge, "connect", return_value=bridge), \
             patch.object(api, "_install", side_effect=install), \
             patch.object(native, "observe", return_value=Query(life())):
            pending = await V3Game.attach(process=process, timeout=.001)
            self.assertIsInstance(pending, PendingOperation)
            original_task = pending.task
            self.assertFalse(original_task.done())
            gate.set()
            game = await pending.resume(timeout=.1)
        self.assertIs(pending.task, original_task)
        self.assertEqual(len(calls), 1)
        self.assertIs(game.raw.process, process)
        self.assertEqual(bridge.closed, 0)
        await game.detach()
        self.assertEqual(bridge.closed, 1)
        self.assertEqual(process.closed, 0)

    async def test_install_resumes_same_native_journal(self):
        bridge, process = FakeNative(), FakeProcess()
        result = installed(process)
        pending = {**result, "status": "pending", "pending": [object()],
                   "stages": {"interface": "ready", "session": "pending"}}
        with patch.object(api.v3_runtime, "install", side_effect=[pending, result]) as install:
            await api._install(bridge, process, interval=0)
        self.assertEqual(install.call_count, 2)
        self.assertTrue(all(c.args[:2] == (bridge, process) for c in install.call_args_list))

    async def test_menu_attach_accepts_deferred_session_without_reentering_world(self):
        bridge, process = FakeNative(), FakeProcess()
        result = installed(process)
        result.update(status="partial", stages={"interface": "ready", "session": "deferred"})
        with patch.object(api.v3_runtime, "install", return_value=result):
            self.assertIs(await api._install(bridge, process), result)
        self.assertEqual(bridge.entered, 0)

    async def test_broken_pipe_repaired_in_place(self):
        process = FakeProcess()
        native_client = Mock(pid=42, handle=111)
        native_client.status.side_effect = [BridgeError("ReadFile(pipe)", 109), {"status": "ok"}]
        replacement = SimpleNamespace(handle=222)
        state = {}
        await api._recover_connection(native_client, process, state, 0)
        native_client.recover.assert_called_once_with(timeout_ms=0)
        native_client.close.assert_not_called()  # The shared client owns failed-handle recovery.
        self.assertTrue(state["connection_recovered"])

    async def test_missing_native_adapter_is_not_fake_menu_readiness(self):
        bridge, process = FakeNative(), FakeProcess()
        complete = installed(process)
        complete.update(status="partial", stages={"interface": "ready", "session": "deferred"})
        partial = {**complete, "modules": {"carrier": process.module}}
        with patch.object(api.v3_runtime, "install", side_effect=[partial, complete]) as install:
            result = await api._install(bridge, process, interval=0)
        self.assertIs(result, complete)
        self.assertEqual(install.call_count, 2)


class ActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_borrowed_context_manager_only_detaches(self):
        game, session, bridge, process = game_fixture()
        with patch.object(native, "exit_application") as quit_game, \
             patch.object(native, "exit_to_menu") as menu:
            async with game as same:
                self.assertIs(same, game)
        quit_game.assert_not_called()
        menu.assert_not_called()
        self.assertEqual((bridge.closed, process.closed), (0, 0))
        self.assertTrue(game.lifecycle.detached)
        with self.assertRaisesRegex(RuntimeError, "detached"):
            await settled(game.save("new"))

    async def test_owned_detach_is_idempotent_and_never_quits(self):
        game, _, bridge, process = game_fixture()
        game.lifecycle._adopt(owns_session=True, owns_process=True)
        with patch.object(native, "exit_application") as quit_game:
            await asyncio.gather(settled(game.detach()), settled(game.detach()))
            await settled(game.detach())
        self.assertEqual((bridge.closed, process.closed), (1, 1))
        quit_game.assert_not_called()

    async def test_save_waits_task_verifies_file_and_releases_once(self):
        game, _, bridge, process = game_fixture()
        begun = life(flags=0, world=9, owned_task_handle=0x50000, path="test/new",
                     resolved_path="native-root/test/new.v3")
        saved = life(flags=0, world=9, owned_task_handle=0x50000, task_completed=1,
                     file_verified=1, file_size=800, path="test/new",
                     resolved_path="native-root/test/new.v3", date_text="1836.1.1")
        released = life(owned_task_handle=0)
        def submit(client, action, **kwargs):
            self.assertIs(client, bridge)
            if action == native.Action.POLL_SAVE: return Query(saved)
            if action == native.Action.RELEASE_SAVE: return Query(released)
            self.fail(f"unexpected action {action}")
        with patch.object(native, "save", return_value=Query(begun)) as begin, \
             patch.object(native, "submit", side_effect=submit) as query:
            result = await settled(game.save("test/new"))
        begin.assert_called_once()
        self.assertFalse(begin.call_args.kwargs["overwrite"])
        self.assertTrue(result["saved"])
        self.assertEqual(result["path"], "native-root/test/new.v3")
        self.assertEqual(query.call_count, 2)
        operation = game.diagnostics["lifecycle"]["save_task"]
        self.assertEqual(operation.owned_task_handle, 0)
        self.assertIsNone(operation.pending_release)

    async def test_failed_save_does_not_claim_success_and_still_releases(self):
        game, _, _, _ = game_fixture()
        failed = life(error=29, owned_task_handle=0x50000, task_completed=1)
        with patch.object(native, "save", return_value=Query(failed)) as begin, \
             patch.object(native, "submit", return_value=Query(life(owned_task_handle=0))) as release:
            with self.assertRaises(NativeRejection):
                await settled(game.save("new"))
        begin.assert_called_once()
        release.assert_called_once()
        self.assertEqual(release.call_args.args[1], native.Action.RELEASE_SAVE)

    async def test_save_existing_original_is_not_implicitly_overwritten(self):
        game, _, _, _ = game_fixture()
        with patch.object(native, "save", return_value=Query(life(error=80))) as save:
            with self.assertRaises(NativeRejection):
                await settled(game.save("original"))
        self.assertFalse(save.call_args.kwargs["overwrite"])

    async def test_catalog_keeps_genuine_metadata_and_expands_read_buffer(self):
        game, _, _, _ = game_fixture()
        row = value(name="new", path="native/new.v3", directory="native", is_valid=1,
                    is_ironman_save=0, cloud=0, version_name="1.x", game_date_text="1836.1.1",
                    last_played_date_text="2026.10.16", number_of_players=1,
                    country_rank_text="Great Power", country_flag_id=500)
        more = SimpleNamespace(error=234, output_required=80000)
        catalog = value(records=(row,))
        with patch.object(native, "list_saves", side_effect=[Query(more), Query(catalog)]) as scan:
            result = await settled(game.saves())
        self.assertEqual(scan.call_args.kwargs["output_capacity"], 80000)
        self.assertEqual(result[0]["date"], "1836.1.1")
        self.assertEqual(result[0]["country_flag_id"], 500)
        self.assertNotIn("country_id", result[0])

    async def test_load_missing_or_invalid_save_does_not_discard_world(self):
        game, _, _, _ = game_fixture()
        for rows in ([], [{"name": "bad", "path": "native/bad.v3", "valid": False}]):
            with patch.object(game.lifecycle, "_saves", return_value=rows), \
                 patch.object(game.lifecycle, "_named_save", side_effect=FileNotFoundError("bad")), \
                 patch.object(game.lifecycle, "_menu", new_callable=AsyncMock) as menu, \
                 patch.object(native, "load") as load:
                with self.assertRaises((FileNotFoundError, ValueError)):
                    await settled(game.load("bad"))
                menu.assert_not_called()
                load.assert_not_called()

    async def test_failed_native_load_is_submitted_once(self):
        game, _, _, _ = game_fixture()
        rows = [{"name": "good", "path": "native/good.v3", "valid": True, "cloud": False}]
        with patch.object(game.lifecycle, "_saves", return_value=rows), \
             patch.object(game.lifecycle, "_menu", new_callable=AsyncMock), \
             patch.object(native, "load", return_value=Query(life(error=2))) as load:
            with self.assertRaises(NativeRejection):
                await settled(game.load("good"))
        load.assert_called_once()

    async def test_load_observes_transition_refreshes_world_and_uses_actual_path(self):
        game, _, bridge, _ = game_fixture()
        rows = [{"name": "good", "path": "native/good.v3", "valid": True, "cloud": False}]
        def loaded(*args, **kwargs):
            bridge.world = 99
            return Query(life(world=99, flags=0))
        with patch.object(game.lifecycle, "_saves", return_value=rows), \
             patch.object(game.lifecycle, "_menu", new_callable=AsyncMock), \
             patch.object(game.lifecycle, "_ready", new_callable=AsyncMock), \
             patch.object(native, "load", side_effect=loaded) as load, \
             patch.object(native, "observe", return_value=Query(life(world=99, flags=0))):
            game._ctx._types["Country"] = 8
            result = await settled(game.load("good"))
        self.assertEqual(load.call_args.args[1], "native/good.v3")
        self.assertTrue(result["loaded"])
        self.assertEqual(game._ctx.world, 99)
        self.assertEqual(game._ctx._types, {})

    async def test_campaign_uses_resolved_tag_and_actual_native_bootstrap(self):
        game, _, bridge, _ = game_fixture()
        def observe(*args, **kwargs):
            return Query(life(world=bridge.world, flags=0 if bridge.world else 1))
        country_id = 0x1234001
        selected = value(selected_country_id=country_id)
        started = value(start_accepted=1)
        play = value(native_ready_called=1, native_sync_done=1)
        ready = value(gui_mode=2, lobby_visible=0, simulation_started=1, paused=1,
                      player_country_id=country_id)
        with patch.object(native, "observe", side_effect=observe), \
             patch.object(game.lifecycle, "_ready", new_callable=AsyncMock), \
             patch.object(api.v3_player_control, "resolve_tag", return_value=Query(
                 value(target_error=0, target_country_id=country_id))) as tag, \
             patch.object(api.v3_control, "start", side_effect=[Query(selected), Query(started)]) as start, \
             patch.object(api.v3_control, "finalize_play", side_effect=[Query(play), Query(ready)]) as finalize:
            result = await settled(game.new_campaign(country="CHI"))
        self.assertTrue(result["started"])
        self.assertEqual(result["country_id"], country_id)
        tag.assert_called_once()
        self.assertEqual(tag.call_args.args[1], "CHI")
        self.assertEqual(bridge.entered, 1)
        self.assertEqual(bridge.request_id, 1)
        self.assertEqual(bridge.wire_commands.count("world-enter 0x0"), 1)
        self.assertEqual(start.call_count, 2)
        self.assertEqual(finalize.call_count, 2)
        self.assertIsNone(game._ctx.explicit_country_id)

    async def test_quit_requires_real_os_exit_not_acknowledgement(self):
        game, _, _, process = game_fixture(timeout=.001)
        with patch.object(native, "exit_application", return_value=Query(life(flags=4))) as quit_game:
            pending = await game.quit()
            self.assertIsInstance(pending, PendingOperation)
            self.assertFalse(pending.done)
            self.assertEqual(process.closed, 0)
            process.exit_code = 259  # Signaled OS result, even STILL_ACTIVE's numeric value.
            result = await pending.resume(timeout=.1)
        self.assertTrue(result["exited"])
        self.assertEqual(result["exit_code"], 259)
        quit_game.assert_called_once()
        self.assertEqual(quit_game.call_args.kwargs["intent"], native.ExitIntent.DISCARD)
        self.assertEqual(process.closed, 0)

    async def test_quit_pipe_loss_can_only_succeed_from_os_witness(self):
        game, _, _, process = game_fixture(timeout=.001)
        class LostQuery:
            def __init__(self): self.started = threading.Event()
            def read(self):
                self.started.set()
                raise BridgeError("ReadFile(pipe)", 109)
        original = LostQuery()
        self.addCleanup(setattr, process, "exit_code", 0)
        error = BridgeError("ReadFile(pipe)", 109)
        error.query = original
        with patch.object(native, "exit_application", side_effect=error) as quit_game:
            pending = await game.quit(save=True)
            self.assertIsInstance(pending, PendingOperation)
            self.assertTrue(await asyncio.to_thread(original.started.wait, 1))
            self.assertIn(original, pending.pending_queries)
            process.exit_code = 0
            result = await pending.resume(timeout=.1)
        self.assertTrue(result["exited"])
        quit_game.assert_called_once()
        self.assertEqual(quit_game.call_args.kwargs["intent"], native.ExitIntent.SAVE_AT_NATIVE_DESTINATION)
        self.assertEqual(game._ctx.pending_queries, [])

    async def test_known_exit_rejection_is_not_reported_as_exit(self):
        game, _, _, process = game_fixture()
        with patch.object(native, "exit_application", return_value=Query(life(error=50))) as quit_game:
            with self.assertRaises(NativeRejection):
                await settled(game.quit())
        quit_game.assert_called_once()
        self.assertIsNone(process.exit_code)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_pending_loader_and_init_thread_are_not_recreated(self):
        bridge, process = FakeNative(), FakeProcess()
        process.modules = Mock(return_value=[])
        class Attempt:
            path = api.runtime_dll_path()
            argument_address = 0x50000
            loader_exit_dword = None
            last_error = None
            thread = None
            state = "waiting"
            def __init__(self): self.count = 0
            def resume(self):
                self.count += 1
                if self.count == 1: raise LibraryLoadPending(self)
                return process.module
        original = Attempt()
        process.load_library = Mock(side_effect=LibraryLoadPending(original))
        original_thread = Mock()
        original_thread.poll.side_effect = [None, 0]
        process.start_thread = Mock(return_value=original_thread)
        with patch.object(api.NativeBridge, "connect", side_effect=[
                BridgeError("WaitNamedPipeW", 2), BridgeError("CreateFileW(pipe)", 2), bridge]):
            context = api._EntryContext(.1, 0)
            await api._connect(context, process, None)
        process.load_library.assert_called_once()
        process.start_thread.assert_called_once()
        original_thread.close.assert_called_once()
        self.assertEqual(original.count, 2)
        self.assertEqual(context.pending_queries, [])

    async def test_unknown_save_and_release_delivery_retain_the_exact_queries(self):
        game, _, _, _ = game_fixture()
        saved = life(flags=0, task_completed=1, file_verified=1, file_size=99,
                     owned_task_handle=0x50000, resolved_path="native/new.v3")
        original_save = Query(saved)
        save_error = BridgeError("ReadFile(pipe)", 109)
        save_error.query = original_save
        original_release = Query(life(owned_task_handle=0))
        release_error = BridgeError("ReadFile(pipe)", 109)
        release_error.query = original_release
        with patch.object(native, "save", side_effect=save_error) as begin, \
             patch.object(native, "submit", side_effect=release_error) as release:
            result = await settled(game.save("new"))
        self.assertTrue(result["saved"])
        begin.assert_called_once()
        release.assert_called_once()
        self.assertGreater(original_save.reads, 0)
        self.assertGreater(original_release.reads, 0)
        self.assertEqual(game._ctx.pending_queries, [])
        self.assertEqual(game.diagnostics["lifecycle"]["save_task"].owned_task_handle, 0)

    async def test_known_not_ready_release_retries_only_after_confirmed_nonconsumption(self):
        game, _, _, _ = game_fixture()
        saved = life(flags=0, task_completed=1, file_verified=1, file_size=99,
                     owned_task_handle=0x50000, resolved_path="native/new.v3")
        not_ready = life(error=21, owned_task_handle=0x50000)
        released = life(owned_task_handle=0)
        with patch.object(native, "save", return_value=Query(saved)) as begin, \
             patch.object(native, "submit", side_effect=[Query(not_ready), Query(released)]) as release:
            result = await settled(game.save("new"))
        begin.assert_called_once()
        self.assertEqual(release.call_count, 2)
        self.assertTrue(result["saved"])

    async def test_menu_save_failure_never_discards_current_world(self):
        game, _, _, _ = game_fixture()
        with patch.object(native, "observe", return_value=Query(life(world=99, flags=0))), \
             patch.object(game.lifecycle, "_save", side_effect=RuntimeError("save failed")), \
             patch.object(native, "exit_to_menu") as menu:
            with self.assertRaisesRegex(RuntimeError, "save failed"):
                await settled(game.menu(save="keep"))
        menu.assert_not_called()

    async def test_load_pending_resumes_original_callback_without_reload(self):
        game, _, _, _ = game_fixture(timeout=.001)
        rows = [{"name": "good", "path": "native/good.v3", "valid": True, "cloud": False}]
        original = Query(None)
        self.addCleanup(setattr, original, "result", life(world=99, flags=0))
        with patch.object(game.lifecycle, "_saves", return_value=rows), \
             patch.object(game.lifecycle, "_menu", new_callable=AsyncMock), \
             patch.object(game.lifecycle, "_ready", new_callable=AsyncMock), \
             patch.object(native, "load", return_value=original) as load, \
             patch.object(native, "observe", return_value=Query(life(world=99, flags=0))):
            pending = await game.load("good")
            self.assertIsInstance(pending, PendingOperation)
            self.assertTrue(await asyncio.to_thread(original.started.wait, 1))
            self.assertIn(original, pending.pending_queries)
            original.result = life(world=99, flags=0)
            result = await pending.resume(timeout=.1)
        load.assert_called_once()
        self.assertTrue(result["loaded"])

    async def test_subdirectory_save_uses_native_resolver_not_guessed_root(self):
        game, _, _, _ = game_fixture()
        row = value(name="new", path="live-vfs/campaign/new.native", directory="live-vfs/campaign",
                    is_valid=1, is_ironman_save=0, cloud=0, version_name="1.x",
                    game_date_text="1836.1.1", last_played_date_text="", number_of_players=1,
                    country_rank_text="", country_flag_id=1)
        missing = SimpleNamespace(error=2)
        with patch.object(native, "describe_save", side_effect=[Query(missing), Query(value(records=(row,)))]) as describe, \
             patch.object(native, "resolve_save_path", return_value=Query(life(
                 resolved_path="live-vfs/campaign/new.native"))) as resolve:
            result = await game.lifecycle._named_save("campaign/new")
        self.assertEqual(result["path"], "live-vfs/campaign/new.native")
        resolve.assert_called_once()
        self.assertEqual(describe.call_args.args[1], "live-vfs/campaign/new.native")

    async def test_detach_waits_for_original_inflight_work_before_close(self):
        game, _, bridge, process = game_fixture(timeout=.001)
        game.lifecycle._adopt(owns_session=True, owns_process=True)
        gate = asyncio.Event()
        pending_action = await game._ctx.perform("test", lambda: gate.wait())
        self.assertIsInstance(pending_action, PendingOperation)
        pending_detach = await game.detach()
        self.assertIsInstance(pending_detach, PendingOperation)
        self.assertEqual((bridge.closed, process.closed), (0, 0))
        gate.set()
        await pending_action.resume(timeout=.1)
        await pending_detach.resume(timeout=.1)
        self.assertEqual((bridge.closed, process.closed), (1, 1))

    async def test_failed_owned_attach_closes_its_resources_not_the_game(self):
        bridge, process = FakeNative(), FakeProcess()
        with patch.object(api, "RemoteProcess", return_value=process), \
             patch.object(api.NativeBridge, "connect", return_value=bridge), \
             patch.object(api, "_install", side_effect=RuntimeError("unsupported helper")):
            with self.assertRaisesRegex(RuntimeError, "unsupported helper"):
                await V3Game.attach(pid=42)
        self.assertEqual((bridge.closed, process.closed), (1, 1))
        self.assertIsNone(process.exit_code)


if __name__ == "__main__":
    unittest.main()
