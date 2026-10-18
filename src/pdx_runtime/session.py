"""A live native connection and a persistent, ordinary Python namespace."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .native_bridge import NativeBridge, RemoteProcess
from .query_executor import RetainedOwners


class RuntimeSession:
    """Own one bridge client without restricting its native API.

    ``client`` and ``native`` refer to the supplied client itself. Code executes
    in ``namespace`` with the same dictionary for globals and locals, so normal
    Python variables, imports, functions and connections survive later calls.
    Closing the session closes its client, but keeps the namespace inspectable.
    owner_pool holds original resource owners until explicit retirement; raw
    close does not drain or discard those owners.
    """

    def __init__(self, client: NativeBridge):
        self.client = client
        self.native = client
        self.owner_pool = RetainedOwners()
        self.namespace: dict[str, Any] = {
            "__name__": "__main__",
            "__doc__": None,
            "session": self,
            "client": client,
            "native": client,
            "NativeBridge": NativeBridge,
            "RemoteProcess": RemoteProcess,
        }
        self._closed = False

    @classmethod
    def connect(cls, pid: int, timeout_ms: int = 5000) -> RuntimeSession:
        """Connect to the bridge already loaded in the target process."""
        return cls(NativeBridge.connect(pid, timeout_ms=timeout_ms))

    @classmethod
    def load(cls, pid: int, dll_path: str | Path | None = None) -> RuntimeSession:
        """Load the packaged one DLL, or an explicit development bridge path."""
        return cls(NativeBridge.load(pid) if dll_path is None else NativeBridge.load(pid, dll_path))

    @classmethod
    def attach(cls, pid: int, dll_path: str | Path | None = None, *,
               process: RemoteProcess | None = None, timeout_ms: int = 0) -> RuntimeSession:
        """Connect/reuse before loading; pending retains the shared connection attempt.

        Resume error.connection_attempt to obtain its original NativeBridge, then
        wrap that client in RuntimeSession. No independent session loader exists.
        """
        return cls(NativeBridge.attach(pid, dll_path, process=process, timeout_ms=timeout_ms))

    def execute(self, source: str, filename: str = "<runtime>") -> None:
        """Execute Python statements in this session; let exceptions propagate."""
        exec(compile(source, filename, "exec"), self.namespace, self.namespace)

    def evaluate(self, expression: str, filename: str = "<runtime>") -> Any:
        """Evaluate Python, return its result and retain it as ``_``."""
        result = eval(compile(expression, filename, "eval"),
                      self.namespace, self.namespace)
        self.namespace["_"] = result
        return result

    def close(self) -> None:
        """Release the native client once; keep variables and results."""
        if not self._closed:
            self.client.close()
            # NativeBridge retains its exact handle if CloseHandle fails. Do
            # not mark that owner closed and block an explicit cleanup retry.
            self._closed = not bool(getattr(self.client, "handle", None))

    def __enter__(self) -> RuntimeSession:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
