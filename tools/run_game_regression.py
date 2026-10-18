"""Opt-in fixed-save regression for an explicitly selected disposable V3 process.

Default: validate local input files and emit NOT RUN; no SDK or process calls.
Execution never discovers/launches games, installs DLLs, saves, quits, or kills.
The operator must prepare an isolated game/profile and load the runtime first.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
LEVELS = {"very_low", "low", "medium", "high", "very_high"}


def sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def checked_file(record, base):
    path = Path(record["path"])
    if not path.is_absolute():
        path = base / path
    path = path.resolve(strict=True)
    expected = record["sha256"]
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Each pinned file needs a lowercase SHA-256 hash")
    if sha256(path) != expected:
        raise ValueError(f"SHA-256 mismatch: {path}")
    return {"path": str(path), "sha256": expected}


def validate_manifest(path):
    path = Path(path).resolve(strict=True)
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1 or value.get("scenario") != "tax-clock-reload-v1":
        raise ValueError("Expected schema=1, scenario=tax-clock-reload-v1")
    if value.get("disposable_process") is not True or value.get("isolated_profile") is not True:
        raise ValueError("The manifest must attest a disposable process and isolated profile")
    pid = value.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("An explicit positive PID is required; discovery is not supported")
    country = value.get("country_id")
    if isinstance(country, bool) or not isinstance(country, int) or not 0 <= country < 0xffffffff:
        raise ValueError("An expected native country_id is required")
    for key in ("save", "executable", "runtime"):
        value[key] = checked_file(value[key], path.parent)
    if Path(value["executable"]["path"]).name.lower() != "victoria3.exe":
        raise ValueError("The pinned executable must be victoria3.exe")
    if Path(value["runtime"]["path"]).name.lower() != "pdx_runtime.dll":
        raise ValueError("The pinned runtime must be pdx_runtime.dll")
    profile = Path(value["profile_directory"])
    if not profile.is_absolute():
        profile = path.parent / profile
    profile = profile.resolve(strict=True)
    if not Path(value["save"]["path"]).is_relative_to(profile):
        raise ValueError("The fixed save must be within the disposable profile directory")
    value["profile_directory"] = str(profile)
    for key in ("initial_tax_level", "target_tax_level"):
        if value.get(key) not in LEVELS:
            raise ValueError(f"{key} must be a native tax band")
    if value["initial_tax_level"] == value["target_tax_level"]:
        raise ValueError("Target tax band must differ from the fixed save baseline")
    datetime.fromisoformat(value["initial_date"])
    if value.get("days", 1) != 1 or isinstance(value.get("days", 1), bool):
        raise ValueError("This first scenario requests one game day")
    return value


class ScenarioStop(Exception):
    def __init__(self, outcome, message):
        super().__init__(message)
        self.outcome = outcome


class Trace:
    def __init__(self, directory):
        self.directory = Path(directory)
        # Never overwrite an earlier regression trace.
        self.directory.mkdir(parents=True, exist_ok=False)
        self.path = self.directory / "trace.jsonl"
        self.summary = {"evidence": "REAL GAME", "status": "NOT RUN", "steps": 0}

    def event(self, stage, outcome, **fields):
        row = {"sequence": self.summary["steps"], "time_ns": time.time_ns(),
               "stage": stage, "outcome": outcome, **fields}
        # Fail on opaque values rather than inventing serialized native owners.
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
        self.summary["steps"] += 1

    def finish(self, status, **fields):
        fixture = {"fixture": self.summary["fixture"]} if "fixture" in self.summary else {}
        self.summary = {"evidence": "REAL GAME", "steps": self.summary["steps"],
                        "status": status, "owners_retained": False, **fixture, **fields}
        (self.directory / "summary.json").write_text(
            json.dumps(self.summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return dict(self.summary)


class Scenario:
    """One original scenario task. Timeout observation never cancels or resubmits it.

    Keep this object AND its event loop alive on PENDING/UNKNOWN. Trace files are
    evidence, not resumable native handles. No action is automatically replayed.
    """
    def __init__(self, manifest, trace, *, game_factory=None, connector=None):
        self.manifest, self.trace = manifest, trace
        self.game_factory, self.connector = game_factory, connector
        self.game = self.session = self.process = self.pending = self.clock = None
        self.task = None
        self.unsettled = False
        self.game_calls_started = False

    async def _settle(self, stage, call):
        from pdx_runtime.v3_game import NativeRejection, PendingOperation
        self.trace.event(stage, "STARTED")
        try:
            value = await call()
            while isinstance(value, PendingOperation):
                self.pending = value
                self.trace.event(stage, "PENDING", operation=value.label)
                value = await value.resume(timeout=30)
        except NativeRejection as error:
            self.pending = None
            raise ScenarioStop("REJECTED", f"{stage}: {error}") from error
        self.pending = None
        self.trace.event(stage, "RETURNED", value=value)
        return value

    def _pin_save(self):
        save = self.manifest["save"]
        if sha256(save["path"]) != save["sha256"]:
            raise ScenarioStop("FAIL", "Fixed save hash changed; load not submitted")

    async def _connect(self):
        if self.connector is not None:
            self.game = await self.connector(self)
            return
        from pdx_runtime.native_bridge import NativeBridge, RemoteProcess
        from pdx_runtime.session import RuntimeSession
        from pdx_runtime.v3_game import V3Game
        if self.process is None:
            class ResidentProcess(RemoteProcess):
                def load_library(self, *args, **kwargs):
                    raise RuntimeError("Regression uses resident DLLs only; no load/replacement allowed")
            self.process = ResidentProcess(self.manifest["pid"])
        if self.process.poll() is not None:
            raise ScenarioStop("FAIL", "Explicit disposable process has exited")
        modules = self.process.modules()
        # Read-only identity checks precede all bridge/game calls. No load fallback.
        for key in ("executable", "runtime"):
            pinned = self.manifest[key]
            # Normalize both sides: Windows TEMP/module paths can use 8.3 aliases.
            pinned_path = Path(pinned["path"]).resolve(strict=True)
            matches = [m for m in modules if Path(m["path"]).resolve() == pinned_path]
            if len(matches) != 1 or sha256(pinned["path"]) != pinned["sha256"]:
                raise ScenarioStop("FAIL", f"Pinned {key} is not loaded in the explicit process")
        pinned_runtime = matches[0]
        other_adapters = [m["path"] for m in modules
            if Path(m["path"]).name.lower().startswith(("pdx_", "v3_"))
            and Path(m["path"]).suffix.lower() == ".dll"
            and Path(m["path"]).resolve() != Path(self.manifest["runtime"]["path"]).resolve(strict=True)]
        if other_adapters:
            raise ScenarioStop("NOT RUN", "Unpinned split runtime modules are loaded: " + "; ".join(other_adapters))
        bridge = NativeBridge.connect(self.process.pid, timeout_ms=1000)
        self.session = RuntimeSession(bridge)
        status = bridge.status()
        if (status.get("pid") != self.process.pid or
                int(str(status.get("dll_base", "0")), 0) != pinned_runtime["base"]):
            raise ScenarioStop("NOT RUN", "Connected bridge is not the pinned resident runtime")
        factory = self.game_factory or V3Game
        self.game = factory(self.session, process=self.process, timeout=30)
        self.trace.event("connect", "CONNECTED", pid=self.process.pid)

    async def _disconnect(self, *, final=False):
        if self.game is not None:
            await self._settle("detach", self.game.detach)
            self.game = None
        if self.session is not None:
            self.session.close()
            self.session = None
        if final and self.process is not None:
            self.process.close()
            self.process = None

    def _expect(self, condition, message):
        if not condition:
            raise ScenarioStop("FAIL", message)

    async def _observe(self, stage):
        clock = await self._settle(stage + ".clock", self.game.time.observe)
        tax = await self._settle(stage + ".tax", self.game.economy.tax_level)
        self._expect(tax.get("country_id") == self.manifest["country_id"], "Controlled country differs from fixture")
        self._expect(tax.get("level") in LEVELS, "Tax observation is incomplete")
        self._expect(clock.get("paused") is True, "Expected paused clock")
        return clock, tax

    async def _load(self, stage):
        self._pin_save()
        self.game_calls_started = True
        result = await self._settle(stage, lambda: self.game.load(self.manifest["save"]["path"], save=False))
        self._expect(result.get("loaded") is True, "Native load did not report completion")
        await self._settle(stage + ".pause", self.game.time.pause)

    async def _body(self):
        self.trace.finish("RUNNING", fixture=self.manifest)
        try:
            await self._connect()
            await self._load("load.fixed_save")
            before, tax = await self._observe("baseline")
            self._expect(datetime.fromisoformat(before["date"]) ==
                         datetime.fromisoformat(self.manifest["initial_date"]), "Baseline date mismatch")
            self._expect(tax["level"] == self.manifest["initial_tax_level"], "Baseline tax mismatch")
            target = self.manifest["target_tax_level"]
            receipt = await self._settle("action.tax", lambda: self.game.economy.set_tax_level(target))
            if receipt.get("accepted") is False:
                raise ScenarioStop("REJECTED", receipt.get("reason") or "Game rejected the action")
            if receipt.get("accepted") is not True:
                self.unsettled = True
                raise ScenarioStop("UNKNOWN", "Action receipt does not establish acceptance; never replay")
            self.trace.event("action.tax", "ACCEPTED", effect_verified=False)
            self.clock = await self._settle("time.start", lambda: self.game.time.run_for(days=1, speed=3))
            from pdx_runtime.v3_game.time import TimeRun
            if isinstance(self.clock, TimeRun):
                notice = await self.clock.wait()
                self.trace.event("time.complete", "RETURNED", value=notice)
            else:
                notice = self.clock
            # An event/manual pause is a distinct stop, never a passed day advance.
            if notice.get("reason") != "date":
                if notice.get("reason") == "error":
                    self.unsettled = True
                    raise ScenarioStop("UNKNOWN", "Clock error; retain original operation")
                raise ScenarioStop("BLOCKED", "Clock stopped at " + str(notice.get("reason")))
            after, changed = await self._observe("after_action")
            self._expect(datetime.fromisoformat(after["date"]) >=
                         datetime.fromisoformat(before["date"]) + timedelta(days=1),
                         "Clock did not advance by at least one day")
            self._expect(changed["level"] == target, "Accepted action effect not observed")
            self.trace.event("action.tax", "EFFECT OBSERVED", level=target)
            await self._disconnect()
            await self._connect()  # same retained OS process; no launch/install
            reconnected, connected_tax = await self._observe("reconnected")
            self._expect(datetime.fromisoformat(reconnected["date"]) ==
                         datetime.fromisoformat(after["date"]) and connected_tax["level"] == target,
                         "Reconnect observation differs")
            await self._load("reload.fixed_save")
            restored, restored_tax = await self._observe("restored")
            self._expect(datetime.fromisoformat(restored["date"]) ==
                         datetime.fromisoformat(before["date"]) and restored_tax["level"] == tax["level"],
                         "Reload did not restore the pinned baseline")
            self._pin_save()
            await self._disconnect(final=True)
            return self.trace.finish("PASS", action="EFFECT OBSERVED", reload="BASELINE RESTORED")
        except ScenarioStop as error:
            outcome = error.outcome if self.game_calls_started else "NOT RUN"
            if not self.unsettled:
                try:
                    await self._disconnect(final=True)
                except Exception as cleanup_error:
                    self.unsettled = True
                    self.trace.event("detach", "UNKNOWN", error=str(cleanup_error))
                    return self.trace.finish("UNKNOWN" if self.game_calls_started else "NOT RUN",
                        scenario_outcome=outcome, error=str(cleanup_error), owners_retained=True)
            return self.trace.finish(outcome, error=str(error), owners_retained=self.unsettled)
        except Exception as error:
            if not self.game_calls_started:
                try:
                    await self._disconnect(final=True)
                except Exception:
                    self.unsettled = True
                return self.trace.finish("NOT RUN", error=str(error), owners_retained=self.unsettled)
            # A transport/read exception is not proof that mutation failed.
            self.unsettled = True
            self.trace.event("exception", "UNKNOWN", error=f"{type(error).__name__}: {error}")
            return self.trace.finish("UNKNOWN", error=str(error), owners_retained=True)

    async def wait(self, timeout=30):
        if self.task is None:
            self.task = asyncio.create_task(self._body(), name="fixed-save-regression")
        done, _ = await asyncio.wait({self.task}, timeout=timeout)
        if done:
            return self.task.result()
        return self.trace.finish("PENDING", owners_retained=True,
                                 message="Original scenario task continues; do not replay")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New private trace directory")
    parser.add_argument("--execute-isolated-game", action="store_true")
    args = parser.parse_args(argv)
    trace = Trace(args.out)
    try:
        manifest = validate_manifest(args.manifest)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps(trace.finish("NOT RUN", error=str(error))))
        return 2
    trace.event("manifest", "VALIDATED", fixture=manifest)
    if not args.execute_isolated_game:
        print(json.dumps(trace.finish("NOT RUN", message="Validation only; no process or SDK calls")))
        return 0
    sys.path.insert(0, str(ROOT / "src"))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    scenario = Scenario(manifest, trace)
    try:
        while True:
            result = loop.run_until_complete(scenario.wait(30))
            print(json.dumps(result), flush=True)
            if result["status"] != "PENDING":
                break
        if scenario.unsettled:
            print("UNKNOWN: controller and native owners retained. Do not restart/replay. "
                  "Inspect the disposable game and trace; Ctrl+C abandons this controller.", flush=True)
            loop.run_forever()
        return 0 if result["status"] == "PASS" else 2 if result["status"] == "NOT RUN" else 1
    except KeyboardInterrupt:
        trace.finish("UNKNOWN", owners_retained=False,
                     message="Operator abandoned controller; pending game work may remain. Do not replay.")
        return 130
    finally:
        # Do not cancel original SDK operations or force cleanup after uncertainty.
        if not scenario.unsettled and (scenario.task is None or scenario.task.done()):
            loop.close()


if __name__ == "__main__":
    raise SystemExit(main())
