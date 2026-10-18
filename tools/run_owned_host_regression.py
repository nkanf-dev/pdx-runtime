"""Run only freshly built owned hosts; never discover or attach to a game.

The build directory is explicit. The only bridge PID comes from our Popen child.
Each child has a deadline and is reaped. Results are NOT real-game E2E evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def bounded(command, *, timeout, env=None):
    # subprocess is contained in this standalone test controller, not the SDK.
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, env=env) as child:
        try:
            output, _ = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            output, _ = child.communicate()
            raise TimeoutError(f"Owned child exceeded {timeout}s: {command[0]}\n{output}")
        if child.returncode:
            raise RuntimeError(f"Owned child exit {child.returncode}: {command[0]}\n{output}")
        return output


def run_bridge(host, dll, timeout):
    # Ignore inherited opt-in PID/SAMPLE/DLL; accept no external process selector.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PDX_NATIVE_TEST_")}
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
    with subprocess.Popen([str(host)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, env=env) as child:
        try:
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: lines.put(child.stdout.readline()), daemon=True)
            reader.start()
            try:
                hello = json.loads(lines.get(timeout=min(timeout, 10)))
            except queue.Empty as error:
                raise TimeoutError("Owned bridge host did not announce itself") from error
            if hello["pid"] != child.pid or child.poll() is not None:
                raise RuntimeError("Owned bridge host PID/liveness mismatch")
            if hello.get("sample_hex") != "50445800783634ff":
                raise RuntimeError("Unexpected owned-host memory fixture")
            env.update(PDX_NATIVE_TEST_PID=str(child.pid), PDX_NATIVE_TEST_DLL=str(dll),
                       PDX_NATIVE_TEST_SAMPLE=hello["sample_address"])
            output = bounded([sys.executable, str(Path(__file__).resolve()), "--bridge-child"],
                             timeout=timeout, env=env)
            return {"pid": child.pid, "output": output}
        finally:
            # This is our own fresh host, never a game PID.
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


def bridge_child():
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "tests.core.test_native_bridge.NativeHostTests.test_remote_load_status_raw_memory_and_reconnect")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun == 1 and not result.skipped else 1


def run_transport(host, timeout):
    # Tests spawn/reap only this explicit, freshly built generic host. They do
    # not accept an inherited PID, discover a game, or load a DLL into a game.
    host = host.resolve(strict=True)
    if host.name != "pdx_transport_host.exe":
        raise ValueError("Expected the explicit owned generic transport host")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PDX_NATIVE_TEST_") and k != "PDX_TRANSPORT_HOST"}
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
    env["PDX_TRANSPORT_HOST"] = str(host)
    return bounded([sys.executable, str(Path(__file__).resolve()), "--transport-child"],
                   timeout=timeout, env=env)


def transport_child():
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "tests.core.test_native_transport.OwnedTransportTests")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun == 3 and not result.skipped else 1


def run_dispatch(host, timeout):
    # The explicit build-tree path always replaces inherited fixture selectors.
    host = host.resolve()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PDX_NATIVE_TEST_") and k != "PDX_BRIDGE_DISPATCH_HOST"}
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
    env["PDX_BRIDGE_DISPATCH_HOST"] = str(host)
    return bounded([sys.executable, str(Path(__file__).resolve()),
                    "--dispatch-child", "--dispatch-host", str(host)],
                   timeout=timeout, env=env)


def dispatch_child(host):
    # Run assertions over all 55 rows, not only the fixture executable's exit.
    host = host.resolve(strict=True)
    if host.name != "pdx_bridge_dispatch_host.exe":
        raise ValueError("Expected the explicit owned dispatch-host executable")
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    previous = os.environ.get("PDX_BRIDGE_DISPATCH_HOST")
    os.environ["PDX_BRIDGE_DISPATCH_HOST"] = str(host)
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName(
            "tests.core.test_native_carrier_composition.NativeCarrierStubHostTests")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() and result.testsRun == 7 and not result.skipped else 1
    finally:
        if previous is None:
            os.environ.pop("PDX_BRIDGE_DISPATCH_HOST", None)
        else:
            os.environ["PDX_BRIDGE_DISPATCH_HOST"] = previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--timeout", type=float, default=60)
    child_mode = parser.add_mutually_exclusive_group()
    child_mode.add_argument("--bridge-child", action="store_true", help=argparse.SUPPRESS)
    child_mode.add_argument("--transport-child", action="store_true", help=argparse.SUPPRESS)
    child_mode.add_argument("--dispatch-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dispatch-host", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.bridge_child:
        return bridge_child()
    if args.transport_child:
        return transport_child()
    if args.dispatch_child:
        if args.dispatch_host is None:
            parser.error("--dispatch-child requires --dispatch-host")
        return dispatch_child(args.dispatch_host)
    if args.dispatch_host is not None:
        parser.error("--dispatch-host is only used with --dispatch-child")
    if os.name != "nt":
        parser.error("Owned native hosts require Windows x64")
    if args.bin_dir is None or args.out is None or not 1 <= args.timeout <= 120:
        parser.error("--bin-dir and --out are required; --timeout must be 1..120 seconds")
    binary = args.bin_dir.resolve()
    names = ("pdx_runtime.dll", "pdx_bridge_host.exe", "pdx_native_call_host.exe",
             "pdx_native_call_result_chaining_host.exe", "pdx_v3_data_projection_host.exe",
             "pdx_bridge_dispatch_host.exe", "pdx_transport_host.exe")
    report = {"evidence": "OWNED HOST", "real_game_e2e": "NOT RUN", "status": "FAIL",
              "artifacts": {}, "checks": []}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    try:
        report["artifacts"] = {name: sha256(binary / name) for name in names}
        for name in names[2:]:
            if name == "pdx_transport_host.exe":
                output = run_transport(binary / name, args.timeout)
                report["checks"].append({"name": name, "status": "PASS",
                    "scope": "generic multi-client listener; idle, short-host, concurrent and pending-owner behavior",
                    "tests": 3, "output": output})
                continue
            if name == "pdx_bridge_dispatch_host.exe":
                output = run_dispatch(binary / name, args.timeout)
                report["checks"].append({"name": name, "status": "PASS",
                    "scope": "isolated source-linked carrier with stub V3 entrypoints; no game",
                    "tests": 7, "wire_cases": 55, "output": output})
                continue
            command = [str(binary / name)]
            if "chaining" in name:
                command.append(str(binary / names[0]))
            output = bounded(command, timeout=args.timeout)
            report["checks"].append({"name": name, "status": "PASS", "output": output})
        bridge = run_bridge(binary / names[1], binary / names[0], args.timeout)
        report["checks"].append({"name": "bridge load/memory/reconnect", "status": "PASS", **bridge})
        report["status"] = "PASS"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        report["seconds"] = round(time.monotonic() - start, 3)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
