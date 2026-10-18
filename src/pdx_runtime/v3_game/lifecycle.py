"""V3 entry, native campaigns/saves, and explicit exit over one session.

Normal use: ``game = await V3Game.attach()`` (or ``launch()``), then
``await game.new_campaign(country="CHI")``. ``async with game`` detaches only.
A timeout returns an awaitable PendingOperation; await its ``resume()`` to
continue the original operation. It never means "submit the action again".
"""
from __future__ import annotations

from pdx_runtime.v3_wire import V3Wire, V3_EXPORT_ALIASES
import asyncio
import os
from pathlib import Path, PurePosixPath
import re
import uuid

from ..native_bridge import (BridgeError, ConnectionAttempt, ConnectionPending,
    LibraryLoadPending, NativeBridge, NativeWindowsAPI, RemoteProcess, runtime_dll_path)
from ..session import RuntimeSession
from .. import v3_runtime, v3_lifecycle as native_lifecycle
from .. import v3_control, v3_player_control
from ._core import DEFAULT_ADAPTERS, PendingOperation, NativeRejection, integer
from ._run import rejected


class ProcessSelectionError(RuntimeError):
    """No process was chosen. ``choices`` contains the actual candidate PIDs."""
    def __init__(self, choices):
        self.choices = tuple(choices)
        if choices:
            text = "; ".join(f"{p['pid']}: {p['path']}" for p in choices)
            super().__init__("Several Victoria 3 processes are running; pass pid=. " + text)
        else:
            super().__init__("Victoria 3 is not running. Use V3Game.launch() to start it.")


def discover_processes(api=None):
    """Use OS process/module metadata only; no connection, injection, or hash gate."""
    api = api or NativeWindowsAPI()
    choices = []
    for pid in sorted(api.find_processes("victoria3.exe")):
        try:
            module = api.main_module(pid)
            path = module["path"]
        except Exception:
            path = "victoria3.exe (image path unavailable)"
        choices.append({"pid": pid, "path": path})
    return choices


def discover_executable():
    """Find a normal Steam installation, or the explicit VICTORIA3_EXE setting."""
    explicit = os.environ.get("VICTORIA3_EXE")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"VICTORIA3_EXE does not exist: {path}")
        return path
    roots = set()
    for key in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(key):
            roots.add(Path(os.environ[key]) / "Steam")
    try:
        import winreg
        for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as opened:
                    roots.add(Path(winreg.QueryValueEx(opened, value)[0]))
            except OSError:
                pass
    except ImportError:
        pass
    libraries = set(roots)
    for root in roots:
        manifest = root / "steamapps" / "libraryfolders.vdf"
        if manifest.is_file():
            text = manifest.read_text(encoding="utf-8", errors="replace")
            libraries.update(Path(value.replace(chr(92) * 2, chr(92))) for value in
                re.findall(r'"path"\s*"([^"\n]+)"', text))
    candidates = sorted({root / "steamapps" / "common" / "Victoria 3" / "binaries" / "victoria3.exe"
                         for root in libraries if (root / "steamapps" / "common" / "Victoria 3" /
                         "binaries" / "victoria3.exe").is_file()}, key=str)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise RuntimeError("Several Victoria 3 installations exist; pass executable=. " +
                           "; ".join(map(str, candidates)))
    raise FileNotFoundError("Victoria 3 executable not found. Pass executable= or set VICTORIA3_EXE.")


class _EntryContext:
    """Keep the original OS/load/install witnesses before a GameContext exists."""
    def __init__(self, timeout, interval):
        self.timeout, self.interval = timeout, interval
        self._wake = asyncio.Event()
        self.pending_queries = []
        self.diagnostics = {}

    async def _finish(self, operation, timeout=None):
        seconds = self.timeout if timeout is None else timeout
        try:
            if seconds is None:
                return await asyncio.shield(operation.task)
            return await asyncio.wait_for(asyncio.shield(operation.task), seconds)
        except asyncio.TimeoutError:
            if operation.task.done():
                return operation.task.result()
            return operation


def _running(process):
    code = process.poll()
    if code is not None:
        raise RuntimeError(f"Victoria 3 exited with code {code} before it was ready.")


async def _connect(context, process, dll_path):
    """Schedule the shared connection owner; no V3 loader/pipe state machine."""
    operation = ConnectionAttempt(process.pid, dll_path, process=process)
    context.diagnostics["connection"] = operation
    context.pending_queries.append(operation)
    try:
        while True:
            try:
                return operation.resume()
            except (ConnectionPending, LibraryLoadPending) as error:
                context.diagnostics["connection_error"] = error
                await asyncio.sleep(context.interval)
    finally:
        # A fatal observation can still leave an original native thread alive.
        if not operation.pending_native:
            context.pending_queries.remove(operation)


async def _recover_connection(native, process, state, interval):
    """Schedule shared local-client recovery without replaying a native command."""
    _running(process)
    try:
        native.status()
        return
    except BridgeError as error:
        if error.winerror not in (6, 109, 121, 232, 233, 299):
            raise
        state["connection_error"] = error
    while True:
        _running(process)
        try:
            native.recover(timeout_ms=0)
        except BridgeError as error:
            if error.winerror not in (2, 121, 231):
                raise
            state["reconnection_error"] = error
            await asyncio.sleep(interval)
            continue
        state["connection_recovered"] = True
        return


async def _install(native, process, *, artifact_dir=None, modules=None, interval=.01, state=None):
    state = {} if state is None else state
    while True:
        _running(process)
        await _recover_connection(native, process, state, interval)
        result = v3_runtime.install(native, process, artifact_dir, modules=modules, stage="all")
        state["install"] = result
        interface = result.get("interface") or {}
        menu_ready = (result["stages"].get("interface") == "ready" and
                      result["stages"].get("session") == "deferred" and
                      integer(interface.get("world", 0)) == 0 and
                      all(role in result.get("modules", {}) for role in v3_runtime.ARTIFACTS))
        if not result.get("pending") and (result.get("status") == "ready" or menu_ready):
            return result
        await asyncio.sleep(interval)


async def _enter(game_cls, *, pid=None, process=None, session=None, executable=None,
                 arguments=(), cwd=None, creation_flags=0, timeout=60.0,
                 poll_interval=.01, dll_path=None, artifact_dir=None,
                 country_id=None, adapters=None, launching=False):
    context = _EntryContext(timeout, poll_interval)
    owns_process = process is None and (session is None or session.namespace.get("process") is None)
    owns_session = session is None

    async def work():
        nonlocal process, session, pid
        try:
            if process is None and session is not None:
                process = session.namespace.get("process")
            if launching:
                path = discover_executable() if executable is None else Path(executable)
                process = RemoteProcess.launch(path, arguments, cwd=cwd, creation_flags=creation_flags)
                pid = process.pid
            elif process is not None:
                if pid is not None and pid != process.pid:
                    raise ValueError("pid and process refer to different games")
                pid = process.pid
            elif session is not None:
                if pid is not None and pid != session.native.pid:
                    raise ValueError("pid and session refer to different games")
                pid = session.native.pid
            elif pid is None:
                choices = discover_processes()
                if len(choices) != 1:
                    raise ProcessSelectionError(choices)
                pid = choices[0]["pid"]
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise ValueError("pid must be a positive process ID")
            if session is not None and session.native.pid != pid:
                raise ValueError("session and process refer to different games")
            if process is None:
                process = RemoteProcess(pid)
            context.diagnostics["process"] = process
            if session is None:
                session = RuntimeSession(await _connect(context, process, dll_path))
            context.diagnostics["session"] = session
            installed = await _install(session.native, process, artifact_dir=artifact_dir,
                interval=poll_interval, state=context.diagnostics)
            selected_adapters = dict(adapters or {})
            for role, module in installed.get("modules", {}).items():
                selected_adapters.setdefault(role, module["base"])
            # Bind legacy aliases from install's existing inventory. Explicit
            # hints and real split images win; unified aliases use the serving
            # carrier, never the first same-name DLL from another installation.
            inventory = installed.get("resident_modules")
            carrier = installed.get("modules", {}).get("carrier")
            if inventory is not None and carrier is not None:
                for role, selector in DEFAULT_ADAPTERS.items():
                    if role in selected_adapters:
                        continue
                    split = next((m for m in inventory
                                  if m["name"].casefold() == selector.casefold()), None)
                    if selector in V3_EXPORT_ALIASES:
                        selected_adapters[role] = split["base"] if split else carrier["base"]
                    elif selector == "pdx_runtime.dll":
                        selected_adapters[role] = carrier["base"]
            game = game_cls(session, process=process, country_id=country_id,
                            adapters=selected_adapters, timeout=timeout)
            game.lifecycle._adopt(owns_session=owns_session, owns_process=owns_process)
            game._ctx.diagnostics["entry"] = context.diagnostics
            await game._ctx.refresh()
            return game
        except BaseException as error:
            # Timeout does not reach here: shield keeps this same work alive.
            # A fatal exception with uncertain native work keeps its witnesses
            # reachable instead of closing the handle under an executing thread.
            error.entry_context = context
            if not context.pending_queries and not context.diagnostics.get("install", {}).get("pending"):
                for label, owner, owns in (("session", session, owns_session),
                                           ("process", process, owns_process)):
                    if owns and owner is not None:
                        try:
                            owner.close()
                        except Exception as cleanup_error:
                            context.diagnostics.setdefault("cleanup_errors", []).append({
                                "resource": label, "type": type(cleanup_error).__name__,
                                "message": str(cleanup_error)})
            raise

    task = asyncio.create_task(work(), name="v3-game:" + ("launch" if launching else "attach"))
    operation = PendingOperation(context, "launch" if launching else "attach", task)
    return await context._finish(operation)


async def attach(game_cls, pid=None, **kwargs):
    """Attach automatically to one running V3; return resumable work on timeout."""
    return await _enter(game_cls, pid=pid, **kwargs)


async def launch(game_cls, executable=None, *, arguments=(), cwd=None, **kwargs):
    """Launch normally and retain the actual CreateProcess handle; never force kill."""
    return await _enter(game_cls, executable=executable, arguments=arguments,
                        cwd=cwd, launching=True, **kwargs)


def _check(label, result):
    if result.error:
        raise NativeRejection(label, result)
    return result


def _save_name(name):
    if not isinstance(name, str):
        raise TypeError("save name must be a string")
    name = name.replace(chr(92), "/")
    parts = PurePosixPath(name)
    if not name or parts.is_absolute() or ":" in name or ".." in parts.parts or "\0" in name:
        raise ValueError("save name must be relative to the game's save directory")
    return name


class LifecycleAPI:
    """Native game lifecycle. Resources supplied by callers remain borrowed."""
    def __init__(self, ctx):
        self.ctx = ctx
        self.owns_session = False
        self.owns_process = bool(getattr(ctx, "owns_process", False))
        self.detached = False
        self._lock = asyncio.Lock()

    def _adopt(self, *, owns_session, owns_process):
        self.owns_session, self.owns_process = owns_session, owns_process

    def _options(self, role="lifecycle"):
        defaults = {"start": "pdx_v3_start_game.dll", "finalize": "pdx_v3_play_finalize.dll"}
        return {"adapter": self.ctx.adapters.get(role, defaults.get(role)), "process": self.ctx.process}

    def _active(self):
        if self.detached:
            raise RuntimeError("This facade is detached. Attach again to use the game.")

    async def _perform(self, label, function):
        self._active()
        async def serial():
            async with self._lock:
                self._active()
                return await function()
        return await self.ctx.perform(label, serial)

    async def _query(self, label, function):
        return _check(label, await self.ctx.query(label, function))

    async def _observe(self):
        return await self._query("lifecycle.observe", lambda: native_lifecycle.observe(
            self.ctx.native, **self._options()))

    async def _ready(self):
        await _install(self.ctx.native, self.ctx.process,
            modules=self.ctx.adapters, interval=self.ctx.poll_interval,
            state=self.ctx.diagnostics.setdefault("lifecycle", {}))

    async def _saves(self, *, cloud=False, directory=""):
        sources = native_lifecycle.SaveSource.LOCAL
        if cloud:
            sources |= native_lifecycle.SaveSource.CLOUD
        capacity = 64 * 1024
        while True:
            result = await self.ctx.query("saves.list", lambda: native_lifecycle.list_saves(
                self.ctx.native, directory=directory, sources=sources,
                output_capacity=capacity, **self._options()))
            if result.error == 234 and result.output_required > capacity:
                capacity = result.output_required
                continue  # Read-only scan, never a replayed game mutation.
            _check("saves.list", result)
            return [self._save_metadata(row) for row in result.records]

    @staticmethod
    def _save_metadata(row):
        return {"name": row.name, "path": row.path, "directory": row.directory,
            "valid": bool(row.is_valid), "ironman": bool(row.is_ironman_save),
            "cloud": bool(row.cloud), "version": row.version_name,
            "date": row.game_date_text or None,
            "last_played": row.last_played_date_text or None,
            "players": row.number_of_players, "country_rank": row.country_rank_text or None,
            "country_flag_id": row.country_flag_id}

    async def _named_save(self, name):
        # Enumeration can omit saves in subdirectories. Ask the actual manager
        # for its root/extension, then open a native descriptor at that path.
        # No OS path joins, guessed native root, or fabricated metadata.
        result = None
        if "/" in name or "\\" in name or ":" in name:
            result = await self.ctx.query("save.describe", lambda: native_lifecycle.describe_save(
                self.ctx.native, name.replace(chr(92), "/"), **self._options()))
            if result.error not in (0, 2):
                _check("save.describe", result)
        if result is None or result.error == 2:
            resolved = await self._query("save.resolve", lambda: native_lifecycle.resolve_save_path(
                self.ctx.native, _save_name(name), **self._options()))
            result = await self.ctx.query("save.describe", lambda: native_lifecycle.describe_save(
                self.ctx.native, resolved.resolved_path, **self._options()))
        if result.error == 2:
            raise FileNotFoundError(f"Save {name!r} was not found by the game's save manager.")
        _check("save.describe", result)
        if len(result.records) != 1:
            raise FileNotFoundError(f"The game returned no save descriptor for {name!r}.")
        return self._save_metadata(result.records[0])

    async def saves(self, *, cloud=False, directory=""):
        """List copied metadata from the game's native save catalog."""
        return await self._perform("saves", lambda: self._saves(cloud=cloud, directory=directory))

    async def _save(self, name):
        name = _save_name(name)
        result = await self.ctx.query("save.begin", lambda: native_lifecycle.save(
            self.ctx.native, name, overwrite=False, **self._options()))
        operation = None
        if result.owned_task_handle:
            operation = native_lifecycle.retain_save_result(result, self.ctx.native, **self._options())
            self.ctx.diagnostics.setdefault("lifecycle", {})["save_task"] = operation
        try:
            _check("save", result)
            if operation is None:
                raise RuntimeError("Save returned no native task; no save success was established.")
            while not result.task_completed:
                await asyncio.sleep(self.ctx.poll_interval)
                result = await self.ctx.query("save.observe", operation.poll)
                _check("save", result)
            if not result.saved:
                raise RuntimeError("The native save finished without a verified nonempty file.")
            return {"saved": True, "name": name, "path": result.resolved_path,
                    "bytes": result.file_size, "date": result.date_text or None}
        finally:
            if operation is not None and operation.owned_task_handle:
                # Complete the exact release query and update its owner. Never
                # enqueue a second release after uncertain transport delivery.
                while operation.owned_task_handle:
                    query = await self.ctx.submit(operation.release)
                    await self.ctx.wait(query)
                    released = operation.settle_release()
                    if released.error in (21, 170) and operation.pending_release is None:
                        await asyncio.sleep(self.ctx.poll_interval)
                        continue  # known non-consumption, not uncertain replay
                    _check("save.release", released)
                    break

    async def save(self, name):
        """Write one new named save, verify it, and release its native task."""
        name = _save_name(name)
        return await self._perform("save", lambda: self._save(name))

    async def _menu(self, save=False):
        observed = await self._observe()
        saved = None
        if observed.in_main_menu and not observed.world and not observed.transition_pending:
            await self.ctx.refresh()
            return {"menu": True, "save": None}
        if save is not False and save is not None:
            name = "pdx-runtime/menu-" + uuid.uuid4().hex if save is True else save
            saved = await self._save(name)
        await self._query("menu", lambda: native_lifecycle.exit_to_menu(self.ctx.native, **self._options()))
        while True:
            observed = await self._observe()
            if observed.in_main_menu and not observed.world and not observed.transition_pending:
                await self.ctx.refresh()
                return {"menu": True, "save": saved}
            await asyncio.sleep(self.ctx.poll_interval)

    async def menu(self, *, save=False):
        """Return to menu; save=True first creates a new SDK-named save."""
        return await self._perform("menu", lambda: self._menu(save))

    async def load(self, name, *, save=False):
        """Load an exact native catalog name/path, replacing the current world."""
        if not isinstance(name, str) or not name:
            raise ValueError("choose a save name or path from saves()")
        async def work():
            rows = await self._saves(cloud=True)
            matches = [row for row in rows if name in (row["name"], row["path"])]
            if not matches:
                # Save metadata names can omit the native extension. Compare
                # only actual native path leaves, never fabricate a VFS root.
                matches = [row for row in rows if PurePosixPath(row["path"]).stem == name]
            if not matches:
                matches = [await self._named_save(name)]
            if len(matches) != 1:
                raise ValueError("Several saves match; choose a path: " + "; ".join(r["path"] for r in matches))
            chosen = matches[0]
            if not chosen["valid"]:
                raise ValueError(f"The game reports this save as invalid: {chosen['path']}")
            if chosen["cloud"]:
                raise RuntimeError("The current native load helper requires a local save; cloud download is not implemented.")
            await self._menu(save)
            await self._query("load", lambda: native_lifecycle.load(
                self.ctx.native, chosen["path"], **self._options()))
            while True:
                observed = await self._observe()
                if observed.world and not observed.in_main_menu and not observed.transition_pending:
                    await self._ready()
                    await self.ctx.refresh()
                    return {"loaded": True, "name": chosen["name"], "path": chosen["path"],
                            "date": observed.date_text or None}
                await asyncio.sleep(self.ctx.poll_interval)
        return await self._perform("load", work)

    async def new_campaign(self, country="CHI", *, save=False):
        """Create a real world, resolve its tag, select/start, and finish native Play."""
        if isinstance(country, str):
            v3_player_control._tag_bytes(country)
        elif not isinstance(country, int) or isinstance(country, bool) or not 0 <= country < 0xffffffff:
            raise ValueError("country must be a native tag (for example 'CHI') or a country ID")
        async def work():
            await self._menu(save)
            before = V3Wire(self.ctx.native).world_status()
            while before.get("pending_operation"):
                await asyncio.sleep(self.ctx.poll_interval)
                before = V3Wire(self.ctx.native).world_status()
            if integer(before.get("world", 0)):
                raise RuntimeError("The old world is still active; no new entry was sent.")
            journal = self.ctx.diagnostics.setdefault("lifecycle", {})
            journal["entry_before"] = before
            journal["entry_started"] = True
            request = None
            try:
                entry = V3Wire(self.ctx.native).enter_world()
                journal["entry"] = entry
                if entry.get("error"):
                    raise RuntimeError(f"Native world entry rejected: {entry['error']}")
                request = entry["request_id"]
            except BridgeError as error:
                journal["entry_unknown"] = error  # Observe, never dispatch again.
            while True:
                observed = V3Wire(self.ctx.native).world_status()
                journal["entry_observation"] = observed
                complete = request is not None and observed.get("completed_request_id", 0) >= request
                if complete and observed.get("world_error") and not observed.get("transition_pending"):
                    raise RuntimeError(f"Native world entry failed: {observed['world_error']}")
                if (observed.get("phase") == 8 and integer(observed.get("world", 0)) and
                    not observed.get("pending_operation") and not observed.get("transition_pending") and
                    (complete or observed.get("request_id", 0) > before.get("request_id", 0))):
                    break
                await asyncio.sleep(self.ctx.poll_interval)
            await self._ready()
            await self.ctx.refresh()
            if isinstance(country, str):
                chosen = await self._query("campaign.country", lambda: v3_player_control.resolve_tag(
                    self.ctx.native, country, **self._options("console")))
                if chosen.target_error or chosen.target_country_id == 0xffffffff:
                    raise RuntimeError(f"Country {country!r} was not resolved (native error {chosen.target_error}).")
                country_id = chosen.target_country_id
            else:
                country_id = country
            selected = await self._query("campaign.select", lambda: v3_control.start(self.ctx.native,
                v3_control.StartAction.SELECT_COUNTRY, country_id, **self._options("start")))
            if selected.selected_country_id != country_id:
                raise RuntimeError("The native selection did not select the requested country.")
            started = await self._query("campaign.start", lambda: v3_control.start(self.ctx.native,
                v3_control.StartAction.START_PAUSED, country_id, **self._options("start")))
            if not started.start_accepted:
                raise RuntimeError("The game did not accept campaign start.")
            played = await self._query("campaign.play", lambda: v3_control.finalize_play(
                self.ctx.native, country_id, **self._options("finalize")))
            if not played.native_ready_called or not played.native_sync_done:
                raise RuntimeError("The game did not complete the native Play request.")
            while True:
                observed = await self._query("campaign.ready", lambda: v3_control.finalize_play(
                    self.ctx.native, country_id, observe=True, **self._options("finalize")))
                if (observed.gui_mode == 2 and not observed.lobby_visible and observed.simulation_started
                        and observed.paused and observed.player_country_id == country_id):
                    self.ctx.explicit_country_id = None
                    await self.ctx.refresh()
                    return {"started": True, "country": country, "country_id": country_id, "paused": True}
                await asyncio.sleep(self.ctx.poll_interval)
        return await self._perform("new_campaign", work)

    async def quit(self, *, save=False):
        """Request graceful native exit and verify the retained OS process exited.

        save=True uses the game's normal exit-save destination. A string creates
        a new named save first. No branch force-kills a game.
        """
        async def work():
            saved = None
            if save is not False and save is not None and save is not True:
                saved = await self._save(save)
            process = self.ctx.process
            if process is None:
                raise RuntimeError("A retained process observation handle is required to verify exit.")
            before = process.poll()
            if before is not None:
                return {"exited": True, "exit_code": before, "save": saved}
            intent = (native_lifecycle.ExitIntent.SAVE_AT_NATIVE_DESTINATION if save is True
                      else native_lifecycle.ExitIntent.DISCARD)
            query = None
            try:
                query = await self.ctx.submit(lambda: native_lifecycle.exit_application(
                    self.ctx.native, intent=intent, **self._options()))
            except Exception as error:
                # Shutdown can close its pipe before acknowledgement. A real OS
                # witness may still prove completion; never submit exit again.
                self.ctx.diagnostics.setdefault("lifecycle", {})["exit_delivery_error"] = error
                if not isinstance(error, BridgeError):
                    raise
            while True:
                code = process.poll()
                if code is not None:
                    if query is not None:
                        self.ctx._forget(query)
                    self.ctx.diagnostics.setdefault("lifecycle", {})["exit_query"] = query
                    return {"exited": True, "exit_code": code, "save": saved}
                if query is not None:
                    try:
                        result = query.read()
                    except Exception as error:
                        self.ctx.diagnostics.setdefault("lifecycle", {})["exit_observation_error"] = error
                    else:
                        if result is not None:
                            self.ctx._forget(query)
                            _check("quit", result)
                await asyncio.sleep(self.ctx.poll_interval)
        return await self._perform("quit", work)

    async def detach(self):
        """Close only facade-owned SDK handles. Never exit or unload the game."""
        if self.detached:
            return {"detached": True}
        async def work():
            current = asyncio.current_task()
            others = [task for task in getattr(self.ctx, "_operations", ())
                      if task is not current and not task.done() and task.get_name() != "v3-game:detach"]
            if others:
                await asyncio.gather(*(asyncio.shield(task) for task in others), return_exceptions=True)
            async with self._lock:
                if not self.detached:
                    if self.owns_session:
                        self.ctx.session.close()
                    if self.owns_process and self.ctx.process is not None:
                        self.ctx.process.close()
                    self.detached = True
            return {"detached": True}
        return await self.ctx.perform("detach", work)
