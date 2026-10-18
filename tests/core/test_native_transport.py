"""Owned native transport regression. No game discovery, injection, or external PID.

Build pdx_transport_host with PDX_BUILD_TEST_HOSTS=ON and set
PDX_TRANSPORT_HOST to its explicit path. Each test creates and reaps its own host.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import unittest

from pdx_runtime.native_bridge import NativeBridge
from pdx_runtime.session import RuntimeSession


@unittest.skipUnless(os.name == "nt" and os.getenv("PDX_TRANSPORT_HOST"),
                     "requires the explicit owned generic transport host")
class OwnedTransportTests(unittest.TestCase):
    def setUp(self):
        host = Path(os.environ["PDX_TRANSPORT_HOST"]).resolve(strict=True)
        self.assertEqual(host.name, "pdx_transport_host.exe")
        self.child = subprocess.Popen([str(host)], stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self.stop_host)
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(self.child.stdout.readline()), daemon=True).start()
        self.hello = json.loads(lines.get(timeout=10))
        self.assertEqual(self.hello["pid"], self.child.pid)
        self.assertEqual(self.hello["initialize_error"], 0)
        self.assertEqual(self.hello["initialize_again_error"], 1247)
        self.pid = self.child.pid
        self.sample = int(self.hello["sample_address"], 0)

    def stop_host(self):
        if self.child.poll() is None:
            self.child.kill()
        self.child.wait(timeout=5)
        self.child.stdout.close()

    def attach(self):
        bridge = NativeBridge.attach(self.pid, timeout_ms=5000)
        self.addCleanup(bridge.close)
        return bridge

    def test_idle_client_and_separate_short_hosts(self):
        # The first connection stays attached and sends no request until all
        # short hosts have finished. It must not own the listener/execution slot.
        idle = self.attach()
        source = (
            "import sys; from pdx_runtime.session import RuntimeSession; "
            "s=RuntimeSession.attach(int(sys.argv[1]), timeout_ms=5000); "
            "a=int(sys.argv[2]); value=bytes([int(sys.argv[3])]); "
            "assert s.native.write(a,value)==1; assert s.native.read(a,1)==value; "
            "assert s.native.status()['pid']==int(sys.argv[1]); "
            "__import__('os')._exit(0) if int(sys.argv[3])==23 else s.close()"
        )
        for value in (17, 23, 41):
            result = subprocess.run([sys.executable, "-c", source, str(self.pid),
                                     str(self.sample), str(value)],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(idle.read(self.sample, 1), bytes([41]))
        self.assertEqual(idle.status()["pid"], self.pid)
        idle.close()
        # Normal CLI shutdown closes just that short host's connection.
        for value in (57, 63):
            command = (f"assert native.write({self.sample}, bytes([{value}])) == 1; "
                       f"assert native.read({self.sample}, 1) == bytes([{value}])")
            result = subprocess.run([sys.executable, "-m", "pdx_runtime.shell",
                                     "--pid", str(self.pid), "-c", command],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Also retire a connection that sends no request at all. Its EOF must
        # not terminate the process-owned accept loop.
        NativeBridge.connect(self.pid).close()
        with RuntimeSession.attach(self.pid, timeout_ms=5000) as session:
            self.assertEqual(session.native.read(self.sample, 1), bytes([63]))

    def test_concurrent_connections_have_correlated_exchanges(self):
        count = 6
        start = threading.Barrier(count)
        def exchange(index):
            start.wait(timeout=5)
            bridge = self.attach()
            for iteration in range(12):
                value = bytes([index, iteration, 0xA5, 0x5A])
                address = self.sample + index * 4
                self.assertEqual(bridge.write(address, value), 4)
                self.assertEqual(bridge.read(address, 4), value)
            bridge.close()
            return index
        with ThreadPoolExecutor(max_workers=count) as workers:
            results = list(workers.map(exchange, range(count)))
        self.assertEqual(results, list(range(count)))
        with self.attach() as again:
            self.assertEqual(again.read(self.sample, 24),
                             b"".join(bytes([i, 11, 0xA5, 0x5A]) for i in range(6)))

    def test_dispatch_serializes_without_reserving_connection_or_native_owner(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.OpenEventW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.SetEvent.argtypes = [wintypes.HANDLE]
        kernel.SetEvent.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        events = {}
        for name in ("entered", "release", "probe"):
            handle = kernel.OpenEventW(0x00100000 | 0x0002, False,
                                       f"Local\\pdx-transport-{name}-{self.pid}")
            self.assertTrue(handle, ctypes.get_last_error())
            events[name] = handle
            self.addCleanup(kernel.CloseHandle, handle)
        first = self.attach()
        # A running native dispatch, unlike an idle connection, is serialized.
        with ThreadPoolExecutor(max_workers=2) as workers:
            holding = workers.submit(first.request, "hold")
            try:
                self.assertEqual(kernel.WaitForSingleObject(events["entered"], 5000), 0)
                second = self.attach()  # Can attach even while dispatch is busy.
                probing = workers.submit(second.request, "probe")
                self.assertEqual(kernel.WaitForSingleObject(events["probe"], 100), 258)
            finally:
                self.assertTrue(kernel.SetEvent(events["release"]))
            self.assertEqual(holding.result(timeout=5), {"status": "ok", "wait": 0})
            self.assertEqual(probing.result(timeout=5), {"status": "ok", "probe": True})
        partial = first.request("op-submit")
        self.assertEqual(partial, {"status": "error", "error": 170, "delivery": "unknown",
                                  "request_id": 1, "pending": True, "submissions": 1,
                                  "partial": {"value": 0, "enabled": False}})
        first.close()
        # Closing/reconnecting transports must not reset native owners, submit
        # again, or issue hidden queries. Exactly this observation is requested.
        with self.attach() as resumed:
            self.assertEqual(resumed.request("op-status"),
                             {"status": "ok", "request_id": 1, "pending": True,
                              "submissions": 1, "observations": 1})
            self.assertEqual(resumed.request("op-finish"), {"status": "ok", "request_id": 1})
            self.assertEqual(resumed.request("op-status"),
                             {"status": "ok", "request_id": 1, "pending": False,
                              "submissions": 1, "observations": 2})


if __name__ == "__main__":
    unittest.main()
