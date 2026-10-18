"""Run ordinary Python against a persistent live native bridge connection."""
from __future__ import annotations

import argparse
import code
from pathlib import Path
import tokenize

from .session import RuntimeSession


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int,
                        help="target process ID")
    parser.add_argument("--dll", type=Path,
                        help="load and initialize this DLL before connecting")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--script", type=Path,
                        help="execute a Python file in the session namespace")
    source.add_argument("-c", dest="command",
                        help="execute Python source in the session namespace")
    args = parser.parse_args(argv)

    session = (RuntimeSession.load(args.pid, args.dll) if args.dll
               else RuntimeSession.connect(args.pid))
    with session:
        if args.script is not None:
            path = args.script.resolve()
            session.namespace["__file__"] = str(path)
            with tokenize.open(path) as stream:
                session.execute(stream.read(), str(path))
        elif args.command is not None:
            session.execute(args.command, "<command>")
        else:
            console = code.InteractiveConsole(locals=session.namespace)
            console.interact(
                banner=(f"PDX Runtime Python session connected to PID {args.pid}.\n"
                        "session, client, native, NativeBridge and RemoteProcess are available.\n"
                        "client/native are the original native bridge client; use normal Python."),
                exitmsg="Leaving PDX Runtime Python session.",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
