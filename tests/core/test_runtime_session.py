"""Session composition tests; fake clients are not native acceptance."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pdx_runtime import shell
from pdx_runtime.session import NativeBridge, RuntimeSession


class Client:
    def __init__(self):
        self.closes = 0
        self.reads = []

    def read(self, address, size):
        self.reads.append((address, size))
        return b"PDX"[:size]

    def close(self):
        self.closes += 1


class RuntimeSessionTests(unittest.TestCase):
    def test_python_namespace_and_results_persist(self):
        with RuntimeSession(Client()) as session:
            session.execute("values = [7]\ndef total():\n    return sum(values)")
            session.execute("values.append(11)")
            self.assertEqual(session.evaluate("total()"), 18)
            self.assertEqual(session.namespace["_"], 18)
            session.execute("result = total() + _")
            self.assertEqual(session.namespace["result"], 36)
            self.assertIs(session.namespace["total"].__globals__, session.namespace)

    def test_client_is_composed_directly_not_wrapped_or_gated(self):
        client = Client()
        with RuntimeSession(client) as session:
            self.assertIs(session.client, client)
            self.assertIs(session.native, client)
            self.assertIs(session.namespace["client"], client)
            self.assertIs(session.namespace["native"], client)
            session.execute("data = native.read(0x1234, 3)")
            self.assertEqual(session.namespace["data"], b"PDX")
            self.assertEqual(client.reads, [(0x1234, 3)])

    def test_execution_error_propagates_and_context_releases_client(self):
        client = Client()
        with self.assertRaisesRegex(ValueError, "native failure"):
            with RuntimeSession(client) as session:
                session.execute("saved = 42\nraise ValueError('native failure')")
        self.assertEqual(client.closes, 1)
        self.assertEqual(session.namespace["saved"], 42)

    def test_close_is_idempotent_and_does_not_swallow_release_errors(self):
        client = Client()
        session = RuntimeSession(client)
        session.close()
        session.close()
        self.assertEqual(client.closes, 1)

        class FailingClient(Client):
            def close(self):
                raise OSError("close failed")

        with self.assertRaisesRegex(OSError, "close failed"):
            RuntimeSession(FailingClient()).close()


    def test_failed_native_handle_cleanup_remains_explicitly_retryable(self):
        from unittest.mock import Mock
        from pdx_runtime.native_bridge import BridgeError
        api = Mock()
        api.close_handle.side_effect = [False, True]
        api.failure.return_value = BridgeError("CloseHandle(pipe)", 6)
        client = NativeBridge(42, api=api)
        client.handle = 123
        session = RuntimeSession(client)
        session.close()
        self.assertEqual(client.handle, 123)
        self.assertFalse(session._closed)
        self.assertEqual(client.connection_info["cleanup_errors"][0]["winerror"], 6)
        session.close()
        session.close()
        self.assertIsNone(client.handle)
        self.assertTrue(session._closed)
        self.assertEqual(api.close_handle.call_count, 2)

    def test_factories_preserve_native_client_and_explicit_load(self):
        client = Client()
        with patch.object(NativeBridge, "connect", return_value=client) as connect:
            with RuntimeSession.connect(42, timeout_ms=1200) as session:
                self.assertIs(session.client, client)
            connect.assert_called_once_with(42, timeout_ms=1200)
        self.assertEqual(client.closes, 1)

        loaded = Client()
        dll = Path("bridge.dll")
        with patch.object(NativeBridge, "load", return_value=loaded) as load:
            with RuntimeSession.load(43, dll) as session:
                self.assertIs(session.client, loaded)
            load.assert_called_once_with(43, dll)
        self.assertEqual(loaded.closes, 1)

    def test_shell_command_and_script_use_session_and_release_on_error(self):
        command_session = RuntimeSession(Client())
        with patch.object(RuntimeSession, "connect", return_value=command_session):
            self.assertEqual(shell.main(["--pid", "42", "-c", "result = native.read(1, 3)"]), 0)
        self.assertEqual(command_session.namespace["result"], b"PDX")
        self.assertEqual(command_session.client.closes, 1)

        script_session = RuntimeSession(Client())
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "script.py"
            script.write_text("saved = 42\nraise RuntimeError('script failed')\n", encoding="utf-8")
            with patch.object(RuntimeSession, "connect", return_value=script_session):
                with self.assertRaisesRegex(RuntimeError, "script failed"):
                    shell.main(["--pid", "42", "--script", str(script)])
            self.assertEqual(script_session.namespace["__file__"], str(script.resolve()))
        self.assertEqual(script_session.namespace["saved"], 42)
        self.assertEqual(script_session.client.closes, 1)

    def test_shell_interactive_console_uses_same_namespace(self):
        session = RuntimeSession(Client())
        with patch.object(RuntimeSession, "connect", return_value=session), \
                patch.object(shell.code, "InteractiveConsole") as console:
            self.assertEqual(shell.main(["--pid", "42"]), 0)
        console.assert_called_once_with(locals=session.namespace)
        console.return_value.interact.assert_called_once()
        self.assertEqual(session.client.closes, 1)


if __name__ == "__main__":
    unittest.main()
