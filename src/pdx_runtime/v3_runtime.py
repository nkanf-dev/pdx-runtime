"""Explicit V3 installation with bounded discovery and resumable partial work.

No build hashes, identity exports or byte-for-byte image matching are used.
The caller keeps the original client/process. Raw primitives remain public.
"""
from __future__ import annotations

from pdx_runtime.v3_wire import V3Wire
from pathlib import Path
import os
import struct

from .native_bridge import BridgeError, NativeBridge, RemoteProcess, runtime_dll_path

ARTIFACTS = {
    "query": ("pdx_v3_query.dll", "PdxV3QueryCountryInterface"),
    "validation": ("pdx_v3_construction_validate.dll", "PdxV3ValidateConstructionInterface"),
    "start": ("pdx_v3_start_game.dll", "PdxV3StartGameInterface"),
    "finalize": ("pdx_v3_play_finalize.dll", "PdxV3PlayFinalizeInterface"),
    "session": ("v3_session_callbacks.dll", "PdxV3SessionHookInstall"),
}
SESSION_HOOK_PREFIX = struct.Struct("<4I7Q2I2Q")
SESSION_HOOK_FIELDS = (
    "size", "installed", "error", "execution_thread_id", "callback_slot",
    "original_callback", "hook_callback", "session", "drain", "callback_count",
    "drain_count", "last_drain_error", "last_original_result", "main_module_base", "carrier",
)
ERROR_ALREADY_EXISTS = 183


def _session_state(process, address):
    values = SESSION_HOOK_PREFIX.unpack(process.read(address, SESSION_HOOK_PREFIX.size))
    state = dict(zip(SESSION_HOOK_FIELDS, values))
    if state["size"] < SESSION_HOOK_PREFIX.size:
        raise BridgeError("V3 Session hook state", reason="incompatible native state size")
    state["current_callback"] = int.from_bytes(process.read(state["callback_slot"], 8), "little") if state["callback_slot"] else 0
    return state


def _matching_session(state, carrier, drain):
    return bool(state["installed"] and state["carrier"] == carrier
                and state["drain"] == drain and state["hook_callback"]
                and state["current_callback"] == state["hook_callback"])


SESSION_CALLBACK = struct.Struct("<4I5Q")
SESSION_CALLBACK_FIELDS = ("size", "phase", "error", "execution_thread_id", "request_id",
                           "completed_request_id", "callbackfn", "opaque", "session")


def _native_binding(state):
    return bool(state["session"] and state["main_module_base"]
                and state["callback_slot"] == state["session"] + 0xB8)


def _reset_binding(state, carrier, drain):
    return bool(_native_binding(state) and state["carrier"] == carrier and carrier
                and state["drain"] == drain and drain
                and state["original_callback"] and state["hook_callback"]
                and state["current_callback"] == state["original_callback"])


def _session_callback(process, module):
    address = process.resolve_export(module, "PdxV3SessionCallbackState")
    state = dict(zip(SESSION_CALLBACK_FIELDS, SESSION_CALLBACK.unpack(process.read(address, SESSION_CALLBACK.size))))
    if state["size"] < SESSION_CALLBACK.size:
        raise BridgeError("V3 Session callback state", reason="incompatible native state size")
    return state


def _callback_executing(state):
    # A terminal phase with an older completion ID can be an in-progress native
    # status update. Keep the same request until its completion is visible.
    return state["phase"] == 2 or (state["phase"] not in (0, 1)
            and state["request_id"] != state["completed_request_id"])


def _session_ready(process, state):
    session = state["session"]
    word = lambda offset: int.from_bytes(process.read(session + offset, 8), "little")
    return bool(word(0xC0) == session and word(0x3D8) and word(0xD0)
                and process.read(session + 0x3F0, 1)[0]
                and not process.read(session + 0xC8, 1)[0])


def _session_service(process, state, carrier, drain, diagnostics):
    if not _matching_session(state, carrier, drain):
        return False
    # InstallAt host bindings have no native Session worker/readiness contract.
    return not _native_binding(state) or bool(_observe(
        lambda: _session_ready(process, state), diagnostics, "native Session worker readiness"))


def _path_key(path):
    return os.path.normcase(os.path.abspath(path))


def _observe(call, diagnostics, operation):
    """At most two observations. Never pass a load/hook/callback action here."""
    for attempt in range(2):
        try:
            value = call()
            if isinstance(value, dict) and value.get("status") == "error":
                diagnostics.append({"operation": operation, "result": value,
                                    "action": "reobserve" if attempt == 0 else "retain partial state"})
                if attempt == 0:
                    continue
            return value
        except Exception as error:
            diagnostics.append({"operation": operation, "exception": error,
                                "action": "reobserve" if attempt == 0 else "retain partial state"})
    return None


def _file_callback(path, symbols):
    """Discover actual Win64 exports, not a build/version/byte-match gate."""
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        return False
    nt = struct.unpack_from("<I", data, 60)[0]
    if nt + 264 > len(data) or data[nt:nt+4] != b"PE\0\0":
        return False
    if struct.unpack_from("<H", data, nt+4)[0] != 0x8664 or struct.unpack_from("<H", data, nt+24)[0] != 0x20B:
        return False
    count = struct.unpack_from("<H", data, nt+6)[0]
    optional = nt+24
    table = optional+struct.unpack_from("<H", data, nt+20)[0]
    if count > 96 or table+count*40 > len(data):
        return False
    sections = [struct.unpack_from("<IIII", data, table+i*40+8) for i in range(count)]
    def read(rva, size):
        for _virtual, start, raw_size, raw in sections:
            if start <= rva and rva+size <= start+raw_size:
                return data[raw+rva-start:raw+rva-start+size]
        raise ValueError("export RVA is not file-backed")
    export, size = struct.unpack_from("<II", data, optional+112)
    if not export or size < 40:
        return False
    _ordinal, functions, names, funcs, strings, ordinals = struct.unpack_from("<6I", read(export, 40), 16)
    if names > len(data)//4 or functions > len(data)//4:
        return False
    found = set()
    for i in range(names):
        rva = struct.unpack("<I", read(strings+i*4, 4))[0]
        name = bytearray()
        for offset in range(128):
            value = read(rva+offset, 1)
            if value == b"\0":
                break
            name.extend(value)
        if bytes(name) not in symbols:
            continue
        index = struct.unpack("<H", read(ordinals+i*2, 2))[0]
        if index < functions and struct.unpack("<I", read(funcs+index*4, 4))[0]:
            found.add(bytes(name))
    return found == set(symbols)


def install(client: NativeBridge, process: RemoteProcess, artifact_dir: str | Path | None = None, *,
            modules: dict[str, int | dict] | None = None, stage: str = "all") -> dict:
    """Discover, install and resume these five roles without discarding progress.

    Stale role hints fall back to actual resident exports, then DLLs in the given
    directory. Each read may be retried once. Loads and hook deliveries are never
    blindly replayed. Partial results expose diagnostics and the original pending
    owners; another call with the same process resumes its retained journal.
    Healthy hooks stay installed. A conflicting Session binding is left intact.
    Local unavailable roles do not prevent an independent hook stage from running.
    No stage creates a world, waits for readiness, unloads modules or closes the
    supplied client/process. The ordinary raw APIs are not wrapped or restricted.
    """
    if stage not in ("interface", "session", "all"):
        raise ValueError("stage must be interface, session or all")
    if client.pid != process.pid:
        raise ValueError("client and process must target the same PID")
    directory = Path(artifact_dir).resolve() if artifact_dir is not None else runtime_dll_path().parent
    journal = getattr(process, "_v3_install_journal", None)
    if journal is None:
        journal = {"modules": {}, "loads": {}, "session": None, "interface": None, "client": client, "cycle": 0}
        process._v3_install_journal = journal
    journal.setdefault("session_reset", None)
    journal["cycle"] += 1
    diagnostics = []
    result = {"client": client, "process": process, "modules": {}, "addresses": {}, "stage": stage,
              "interface": None, "session_hook": None, "session_hook_install_error": None,
              "stages": {"interface": "unavailable" if stage in ("interface", "all") else "not requested",
                         "session": "unavailable" if stage in ("session", "all") else "not requested"},
              "diagnostics": diagnostics, "journal": journal}
    addresses, resident = result["addresses"], result["modules"]
    loaded = _observe(process.modules, diagnostics, "modules") or []
    # Only the raw loader's retained attempt can settle an uncertain load.
    # Module visibility alone does not establish loader-thread completion.
    for path, pending in journal["loads"].items():
        attempt = pending.get("attempt")
        if attempt is None or pending.get("settled"):
            continue
        try:
            module = attempt.resume()
        except Exception as error:
            pending["exception"] = error
            if getattr(attempt, "state", None) == "failed":
                pending["settled"] = True
            diagnostics.append({"operation": "original LoadLibrary continuation", "exception": error,
                                "owner": attempt, "action": "try another candidate" if pending.get("settled")
                                else "retain original attempt; continue independent roles"})
        else:
            pending["settled"] = True
            loaded = [item for item in loaded if item["base"] != module["base"]] + [module]
    status = _observe(client.status, diagnostics, "carrier status")
    carrier_base = int(status["dll_base"], 16) if status and status.get("status") == "ok" else None
    if carrier_base is None and journal["modules"].get("carrier"):
        carrier_base = journal["modules"]["carrier"]["base"]
    carrier = next((m for m in loaded if m["base"] == carrier_base), None)
    if carrier is not None:
        # Bind from the status already read above, never a second handshake.
        connection_info = getattr(client, "connection_info", None)
        if isinstance(connection_info, dict):
            connection_info["serving_module"] = dict(carrier)
        resident["carrier"] = {**carrier, "reused": True}
        journal["modules"]["carrier"] = carrier
        addresses["construction_drain"] = _observe(
            lambda: process.resolve_export(carrier_base, "PdxV3DrainConstruction"), diagnostics, "carrier drain export")
    files = _observe(lambda: sorted(directory.glob("*.dll")), diagnostics, "artifact directory") or []
    selections = modules or {}
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
    for role, (filename, symbol) in ARTIFACTS.items():
        required = [symbol] + (["PdxV3SessionHookState"] if role == "session" else [])
        hint = selections.get(role)
        hint = hint.get("base") if isinstance(hint, dict) else hint
        previous = journal["modules"].get(role, {}).get("base")
        candidates = sorted(loaded, key=lambda m: (m["base"] != hint,
            m["name"].casefold() != filename.casefold(),
            m["base"] != carrier_base, m["base"] != previous))
        # Try hints and expected names first. Renamed modules can still work:
        # discover actual exports of other non-system DLLs, not OS exports.
        candidates = [m for m in candidates if m["base"] in (hint, previous)
                      or m["name"].casefold().startswith(("pdx_", "v3_"))
                      or Path(m["path"]).parent == directory
                      or (m["name"].casefold().endswith(".dll")
                          and not Path(m["path"]).resolve().is_relative_to(system_root))]
        module, exports, reused = None, None, True
        conflict, stopped, resettable, uninstalled, stale, unreadable = None, None, None, None, None, None
        for candidate in candidates:
            path_pending = journal["loads"].get(_path_key(candidate["path"]))
            if path_pending and not path_pending.get("settled"):
                continue
            found = _observe(lambda m=candidate: {s: process.resolve_export(m["base"], s) for s in required},
                             diagnostics, role + " resident exports")
            if found:
                if role == "session":
                    observed = _observe(lambda: _session_state(process, found["PdxV3SessionHookState"]),
                                        diagnostics, "resident Session hook discovery")
                    if observed and _matching_session(observed, carrier_base, addresses.get("construction_drain")):
                        if _session_service(process, observed, carrier_base, addresses.get("construction_drain"), diagnostics):
                            module, exports = candidate, found
                            break  # Prefer a live worker, not only the installed/slot flags.
                        stopped = stopped or (candidate, found)
                        continue
                    if observed and _reset_binding(observed, carrier_base, addresses.get("construction_drain")):
                        resettable = resettable or (candidate, found)
                    elif observed and observed["installed"]:
                        if observed["hook_callback"] and observed["current_callback"] == observed["hook_callback"]:
                            conflict = conflict or (candidate, found)
                        else:
                            stale = stale or (candidate, found)
                    elif observed:
                        uninstalled = uninstalled or (candidate, found)
                    else:
                        unreadable = unreadable or (candidate, found)
                    continue
                module, exports = candidate, found
                break
        fallback = conflict or stopped or resettable or uninstalled or stale or unreadable
        if module is None and fallback is not None:
            module, exports = fallback
        if module is None and not any(pending.get("role") == role and not pending.get("settled")
                                      for pending in journal["loads"].values()):
            paths = sorted(files, key=lambda path: path.name.casefold() != filename.casefold())
            for path in paths:
                path = path.resolve()
                prior = journal["loads"].get(_path_key(path))
                if prior and (not prior.get("settled") or prior.get("cycle") == journal["cycle"]):
                    continue
                eligible = _observe(lambda: _file_callback(path, [s.encode("ascii") for s in required]),
                                    diagnostics, role + " file exports")
                if not eligible:
                    continue
                try:
                    candidate = process.load_library(path, timeout_ms=0)
                except Exception as error:
                    attempt = getattr(error, "native_load", getattr(error, "attempt", None))
                    # The raw loader uses .attempt for uncertain delivery. Other
                    # failures are settled failures, so another candidate is safe.
                    journal["loads"][_path_key(path)] = {"attempt": attempt, "exception": error, "role": role,
                                                   "cycle": journal["cycle"], "settled": attempt is None}
                    diagnostics.append({"operation": role + " load", "exception": error,
                                        "owner": attempt, "action": "resume original attempt or try a compatible candidate"})
                    if not journal["loads"][_path_key(path)]["settled"]:
                        break
                    continue
                loaded.append(candidate)
                found = _observe(lambda: {s: process.resolve_export(candidate["base"], s) for s in required},
                                 diagnostics, role + " loaded exports")
                if found:
                    module, exports, reused = candidate, found, False
                    break
        if module is None:
            diagnostics.append({"operation": role, "action": "rediscover on the next install; other roles remain usable"})
            continue
        resident[role] = {**module, "reused": reused}
        journal["modules"][role] = module
        addresses[role] = exports[symbol]
        if role == "session":
            addresses["session_hook_state"] = exports["PdxV3SessionHookState"]
    interface = _observe(V3Wire(client).world_status, diagnostics, "Interface state")
    result["interface"] = interface
    if interface and interface.get("installed"):
        journal["interface"] = None
        if stage in ("interface", "all"):
            result["stages"]["interface"] = "ready"
    elif stage in ("interface", "all"):
        result["stages"]["interface"] = "pending" if journal["interface"] else "unavailable"
        if interface and interface.get("status") == "ok" and not journal["interface"] and not interface.get("pending_operation"):
            try:
                outcome = V3Wire(client).install_interface_hook()
                result["interface"] = outcome
                if outcome.get("installed"):
                    result["stages"]["interface"] = "ready"
                else:
                    diagnostics.append({"operation": "Interface install", "result": outcome,
                                        "action": "reobserve native readiness on the next install"})
            except Exception as error:
                journal["interface"] = {"client": client, "exception": error}
                result["stages"]["interface"] = "pending"
                diagnostics.append({"operation": "Interface delivery", "exception": error,
                                    "owner": client, "action": "reobserve hook state without replay"})
    reset = journal["session_reset"]
    if reset is not None:
        # The recorded request belongs to its original module, even when the
        # current slot/discovery now selects a foreign or different owner.
        observed = _observe(lambda: _session_callback(process, reset["module"]),
                            diagnostics, "original Session reset callback")
        result["session_callback"] = observed
        request = (reset.get("callback") or {}).get("request_id")
        if observed is not None and observed["phase"] in (0, 3, 4, 5) and (
                observed["request_id"] == observed["completed_request_id"] == request):
            journal["session_reset_completion"] = {**reset, "callback": observed}
            journal["session_reset"] = None
    if "session_hook_state" in addresses:
        state = _observe(lambda: _session_state(process, addresses["session_hook_state"]), diagnostics, "Session state")
        result["session_hook"] = state
        owner = journal["session"]
        if owner and owner.get("thread"):
            code = _observe(owner["thread"].poll, diagnostics, "original Session install thread")
            if code is not None:
                owner["thread"].close()
                owner["thread"] = None
                owner["completed"] = code
                state = _observe(lambda: _session_state(process, addresses["session_hook_state"]), diagnostics, "Session after original thread")
                result["session_hook"] = state
                result["session_hook_install_error"] = code
                journal["session_completion"] = owner
                journal["session"] = None  # Delivery is settled; fresh state decides any reset repair.
        reset_wait, repair = None, False
        if stage in ("session", "all") and state and _reset_binding(state, carrier_base, addresses.get("construction_drain")):
            queued = _observe(lambda: _session_callback(process, resident["session"]["base"]),
                              diagnostics, "Session callback during reset recovery")
            result["session_callback"] = queued
            journal["session_reset"] = {"module": resident["session"]["base"], "callback": queued}
            if queued is not None and _callback_executing(queued):
                reset_wait = "pending"
            elif not _observe(lambda: _session_ready(process, state), diagnostics, "native Session reinitialization"):
                reset_wait = "deferred"
            else:
                repair = True  # Native Install now preserves the same QUEUED request.
        if stage in ("session", "all"):
            result["stages"]["session"] = "unavailable"
            if reset_wait:
                result["stages"]["session"] = reset_wait
                diagnostics.append({"operation": "Session reset recovery", "callback": result.get("session_callback"),
                                    "action": "continue original request and native initialization; no Remove or resubmit"})
            elif state and state["installed"] and not repair:
                if _matching_session(state, carrier_base, addresses.get("construction_drain")):
                    if _session_service(process, state, carrier_base, addresses.get("construction_drain"), diagnostics):
                        result["stages"]["session"] = "ready"
                        journal["session_reset"] = None
                        if journal["session"] and journal["session"].get("thread") is None:
                            journal["session"] = None
                    else:
                        result["stages"]["session"] = "deferred"
                        diagnostics.append({"operation": "native Session worker readiness", "state": state,
                                            "action": "keep hook and original requests; observe native initialization"})
                else:
                    diagnostics.append({"operation": "Session binding", "state": state,
                                        "action": "keep existing hook; independent Interface remains usable"})
            elif journal["session"]:
                result["stages"]["session"] = "pending"
            elif not repair and interface and "world" in interface and int(str(interface["world"]), 0) == 0:
                result["stages"]["session"] = "deferred"
                diagnostics.append({"operation": "Session readiness", "action": "Interface retained; the same install resumes when a world exists"})
            elif state and carrier is not None and addresses.get("construction_drain"):
                thread = None
                try:
                    thread = process.start_thread(addresses["session"], carrier_base)
                    journal["session"] = {"thread": thread, "process": process}
                    code = thread.wait(0)
                    thread.close()
                    journal["session"] = None
                    result["session_hook_install_error"] = code
                    state = _observe(lambda: _session_state(process, addresses["session_hook_state"]), diagnostics, "Session after install")
                    result["session_hook"] = state
                    if state and _session_service(process, state, carrier_base, addresses["construction_drain"], diagnostics):
                        result["stages"]["session"] = "ready"
                        journal["session_reset"] = None
                    elif state and _matching_session(state, carrier_base, addresses["construction_drain"]):
                        result["stages"]["session"] = "deferred"
                        diagnostics.append({"operation": "native Session worker readiness", "state": state,
                                            "action": "retain completed delivery; observe native initialization"})
                    else:
                        result["stages"]["session"] = "pending" if code == 170 else ("deferred" if code == 21 else "unavailable")
                        diagnostics.append({"operation": "Session readiness", "native_error": code,
                                            "action": "retain modules; reobserve the current world on the next install"})
                except Exception as error:
                    diagnostics.append({"operation": "Session install", "exception": error,
                                        "owner": journal["session"], "action": "poll original thread; do not replay"})
                    if thread is not None:
                        result["stages"]["session"] = "pending"
    pending = [record for record in journal["loads"].values() if not record.get("settled")]
    pending += [journal[key] for key in ("interface", "session", "session_reset") if journal[key]]
    result["pending"] = pending
    # Expose the already-read inventory for facade alias binding, not a new scan.
    result["resident_modules"] = loaded
    requested = [value for value in result["stages"].values() if value != "not requested"]
    result["status"] = "pending" if pending else ("ready" if all(value == "ready" for value in requested)
                                                     and all(role in resident for role in ARTIFACTS) else "partial")
    return result
