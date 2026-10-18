"""Native script request layout and shared callback lifetime, without a game."""
import struct
import unittest
from unittest.mock import Mock

from pdx_runtime import v3_script as script
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES


class MemoryProcess:
    pid = 123

    def __init__(self):
        self.memory = bytearray()
        self.base = 0x20000
        self.freed = []
    def resolve_export(self, module, symbol, *, aliases=None):
        if aliases is not None and aliases is not V3_EXPORT_ALIASES:
            raise AssertionError("unexpected export-alias policy in V3 CPU fixture")
        self.lookup = (module, symbol)
        self.lookup_aliases = aliases
        return 0x10000
    def allocate(self, size):
        self.memory = bytearray(size)
        return self.base
    def write(self, address, data):
        offset = address - self.base
        self.memory[offset:offset+len(data)] = data
    def read(self, address, size):
        offset = address - self.base
        return bytes(self.memory[offset:offset+size])
    def free(self, address):
        self.freed.append(address)
    def close(self):
        pass


class ScriptTests(unittest.TestCase):
    def _wire_request_fixture(self, command):
        """CPU transport: preserve raw responses through exact V3 wire commands."""
        if command == "world-status":
            return self.world_status()
        words = command.split()
        if len(words) == 3 and words[0] == "interface-callback":
            callback, address = int(words[1], 0), int(words[2], 0)
            self.assertEqual(command, f"interface-callback {callback:#x} {address:#x}")
            return self.interface_callback(callback, address)
        raise AssertionError(f"unexpected V3 fixture wire command: {command!r}")

    def setUp(self):
        self.client = Mock(spec_set=["pid", "request", "status"], pid=123)
        self.world_status = Mock()
        self.interface_callback = Mock()
        self.client.request.side_effect = self._wire_request_fixture
        self.interface_callback.return_value = {"status":"ok", "error": 0, "request_id":7}
        self.world_status.return_value = {"status":"ok", "completed_request_id":6}
        self.process = MemoryProcess()

    def test_scope_raw_is_not_limited_to_four_id_kinds(self):
        scope = script.Scope.raw(123, 0xFFFFFFFFFFFFFFFF)
        self.assertEqual(scope.to_bytes(), script.SCOPE.pack(123, 0, 0, 0, 0xFFFFFFFFFFFFFFFF))
        self.assertEqual(script.Scope.id(script.ScopeType.STATE, 0xFE000005).payload, 0xFE000005)
        with self.assertRaises(ValueError): script.Scope.id(script.ScopeType.STATE, 1 << 32)

    def test_script_text_storage_completion_and_raw_result(self):
        text = "always = yes # 国家"
        scope = script.Scope.id(script.ScopeType.COUNTRY, 1)
        query = script.trigger(self.client, text, scope, process=self.process)
        self.assertEqual(script.RESULT.size, 576)
        self.assertEqual(script.OWNER.size, 544)
        self.assertEqual(self.process.lookup, ("pdx_v3_script.dll", "PdxV3ScriptInterface"))
        self.assertIs(self.process.lookup_aliases, V3_EXPORT_ALIASES)
        self.assertEqual(self.process.memory[576:], text.encode("utf-8") + b"\0")
        self.assertEqual(query.text_address, self.process.base + 576)
        self.assertIsNone(query.read())
        self.assertFalse(self.process.freed)
        self.world_status.return_value = {"status":"ok", "completed_request_id":7}
        self.process.write(self.process.base, script.RESULT.pack(576, 0, 1,
            len(text.encode("utf-8")), 5, 0, 0, 0, 1, 1, 1, 1, 1, 7372, 0, 0, 0, bytes(512)))
        result = query.read()
        self.assertEqual(result.execution_thread_id, 7372)
        self.assertTrue(result.boolean)
        self.assertEqual(result.raw_boolean, 1)
        self.assertEqual(self.process.freed, [self.process.base])
        self.assertIs(query.read(), result)

    def test_parser_error_not_effect_success_or_retried(self):
        query = script.effect(self.client, "invalid_effect = yes", script.Scope.raw(5, 1),
                              process=self.process)
        self.world_status.return_value = {"status":"ok", "completed_request_id":7}
        self.process.write(self.process.base, script.RESULT.pack(576, 11, 2, 20,
            5, 0, 0, 0, 1, 1, 0, 0, 0, 7372, 13, 0, 0, b"Unknown effect"))
        result = query.read()
        self.assertEqual(result.error, 11)
        self.assertEqual(result.diagnostic_raw, b"Unknown effec")
        self.assertIsNone(result.boolean)
        self.interface_callback.assert_called_once()

    def test_numeric_request_saved_scope_in_owned_buffer(self):
        scope = script.Scope.raw(0x4C, 0xFE000005)
        saved = script.SavedScope(0x12345678, script.Scope.raw(5, 1))
        query = script.value(self.client, "scope:actor.gdp", scope,
                             saved_scopes=(saved,), process=self.process)
        self.assertEqual(script.VALUE_RESULT_BYTES, 616)
        self.assertEqual(script.SAVED_SCOPE.size, 24)
        self.assertEqual(self.process.lookup, ("pdx_v3_script.dll", "PdxV3ScriptValueInterface"))
        extension = script.VALUE_EXTENSION.unpack_from(self.process.memory, 576)
        self.assertEqual(extension, (0, 0, 0, 0, self.process.base + 616, 1, 640))
        self.assertEqual(self.process.memory[616:640], saved.to_bytes())
        self.assertEqual(query.text_address, self.process.base + 640)
        self.assertIsNone(query.read())
        self.assertFalse(self.process.freed)

    def test_negative_fixed_bits_decoded_exact_without_float(self):
        query = script.value(self.client, "-1.25000", script.Scope.raw(5, 1), process=self.process)
        self.world_status.return_value = {"status":"ok", "completed_request_id":7}
        raw = (-125000) & 0xFFFFFFFFFFFFFFFF
        self.process.write(self.process.base, script.RESULT.pack(616, 0, 3, 8,
            5, 0, 0, 0, 1, 1, 1, 1, 0, 7372, 0, 0, 0, bytes(512))
            + script.VALUE_EXTENSION.pack(1, 0, raw, 0, 0, 0, 616))
        result = query.read()
        self.assertEqual(result.value.bits, raw)
        self.assertEqual(result.value.signed, -125000)
        self.assertEqual(str(result.value.decimal), "-1.25")
        self.assertEqual(self.process.freed, [self.process.base])
        self.assertIs(query.read(), result)

    def test_typed_object_result_keeps_native_type_and_full_id(self):
        query = script.typed_value(self.client, "root", script.Scope.raw(5, 0xFE000001),
                                   context=0x100000, process=self.process)
        self.world_status.return_value = {"status":"ok", "completed_request_id":7}
        self.process.write(self.process.base, script.RESULT.pack(616, 0, 4, 4,
            5, 0, 0, 0, 0xFE000001, 1, 1, 1, 0, 7372, 0, 0, 0, bytes(512))
            + script.VALUE_EXTENSION.pack(5, 0, 0xFE000001, 0x100000, 0, 0, 616))
        result = query.read()
        self.assertEqual(result.value, script.Scope.raw(5, 0xFE000001))
        self.assertEqual(result.context, 0x100000)

    def test_numeric_invalid_feedback_is_not_genuine_zero(self):
        query = script.value(self.client, "unknown_value", script.Scope.raw(5, 1), process=self.process)
        self.world_status.return_value = {"status":"ok", "completed_request_id":7}
        self.process.write(self.process.base, script.RESULT.pack(616, 11, 3, 13,
            5, 0, 0, 0, 1, 1, 0, 0, 0, 7372, 0, 0, 0, bytes(512))
            + script.VALUE_EXTENSION.pack(0, 0, 0, 0, 0, 0, 616))
        result = query.read()
        self.assertEqual(result.error, 11)
        self.assertIsNone(result.value)
        self.interface_callback.assert_called_once()

    def test_numeric_pointer_patch_failure_releases_without_queue(self):
        def fail(address, data):
            raise OSError("write")
        self.process.write = fail
        with self.assertRaises(OSError):
            script.value(self.client, "1", script.Scope.raw(5, 1), process=self.process)
        self.assertEqual(self.process.freed, [self.process.base])
        self.interface_callback.assert_not_called()

    def test_embedded_nul_rejected_before_transport(self):
        with self.assertRaises(ValueError):
            script.trigger(self.client, "always = yes\0", script.Scope.raw(5,1), process=self.process)
        self.interface_callback.assert_not_called()


if __name__ == "__main__": unittest.main()
