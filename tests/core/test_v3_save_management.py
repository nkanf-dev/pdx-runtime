"""Focused save-management wire/ownership tests; no native file or game is touched."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from pdx_runtime import v3_lifecycle as life
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES

ADDRESS = 0x20000


def completed_message(initial: bytes, message: bytes, *, error: int = 0,
                      native_success: int = 0, native_tag: int = 1,
                      required: int | None = None) -> bytes:
    header = list(life.SAVE_MANAGEMENT_HEADER.unpack_from(initial))
    start = life.SAVE_MANAGEMENT_HEADER.size + header[4] + header[5]
    header[1] = error
    header[7] = len(message)
    header[8] = len(message) if required is None else required
    header[9] = native_success
    header[10] = native_tag
    header[11:] = [7372, 0x30000, 0x40000]
    return (life.SAVE_MANAGEMENT_HEADER.pack(*header) + initial[64:start]
            + message + bytes(header[6] - len(message)))


class SaveManagementTests(unittest.TestCase):
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
        self.interface_callback.return_value = {"status": "ok", "error": 0, "request_id": 7}
        self.world_status.return_value = {"status": "ok", "completed_request_id": 7}
        self.process = Mock(pid=123)
        self.process.resolve_export.return_value = 0x10000
        self.process.allocate.return_value = ADDRESS

    def initial(self):
        return self.process.write.call_args.args[1]

    def test_explicit_utf8_source_and_same_directory_leaf_do_not_enable_overwrite_or_delete(self):
        source, destination = "save games/临时目录/旧名.v3", "新名.v3"
        with patch.object(life, "delete_save") as delete:
            life.rename_save(self.client, source, destination, message_capacity=64, process=self.process)
        header = life.SAVE_MANAGEMENT_HEADER.unpack_from(self.initial())
        self.assertEqual(life.SAVE_MANAGEMENT_HEADER.size, 64)
        self.assertEqual(header[:7], (64 + len(source.encode()) + len(destination.encode()) + 64,
                                     997, life.SaveManagementAction.RENAME_NATIVE, 0,
                                     len(source.encode()), len(destination.encode()), 64))
        self.assertEqual(header[10], 0xFFFFFFFF)
        self.assertEqual(self.initial()[64:64 + header[4] + header[5]],
                         source.encode() + destination.encode())
        delete.assert_not_called()
        before = self.interface_callback.call_count
        for invalid in ("", "../other.v3", "dir/new.v3", "dir\\new.v3", "drive:new.v3"):
            with self.subTest(destination=invalid):
                with self.assertRaises(ValueError):
                    life.rename_save(self.client, source, invalid, process=self.process)
        with self.assertRaises(ValueError):
            life.delete_save(self.client, "bad\0path", process=self.process)
        self.assertEqual(self.interface_callback.call_count, before)

        life.delete_named_save(self.client, "精确单名", cloud=True, process=self.process)
        named = life.SAVE_MANAGEMENT_HEADER.unpack_from(self.initial())
        self.assertEqual((named[2], named[3], named[5], named[10]),
                         (life.SaveManagementAction.DELETE_NAMED, 1, 0, 0xFFFFFFFF))
        self.assertEqual(self.initial()[64:64 + named[4]], "精确单名".encode())

    def test_pending_then_native_domain_failure_preserves_original_diagnostic_without_retry(self):
        query = life.delete_save(self.client, "save games/临时/不存在.v3", process=self.process)
        self.process.resolve_export.assert_called_once_with("pdx_v3_lifecycle.dll", "PdxV3SaveManagementInterface", aliases=V3_EXPORT_ALIASES)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 6}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        message = "原生文件删除失败".encode()
        self.process.read.return_value = completed_message(self.initial(), message)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 7}
        result = query.read()
        self.assertEqual((result.error, result.native_success, result.native_result_tag), (0, 0, 1))
        self.assertEqual(result.action, life.SaveManagementAction.DELETE_PATH)
        self.assertEqual(result.message_raw, message)
        self.assertEqual(result.message, "原生文件删除失败")
        self.assertIs(query.read(), result)
        self.interface_callback.assert_called_once()
        self.process.free.assert_called_once_with(ADDRESS)
        self.process.close.assert_not_called()

    def test_short_native_message_retains_raw_bytes_required_size_and_does_not_replay_mutation(self):
        query = life.rename_save(self.client, "save games/a.v3", "existing.v3",
                                 message_capacity=4, process=self.process)
        complete = "目标已经存在".encode()
        partial = complete[:4]  # Deliberately ends inside an UTF-8 code point.
        self.process.read.return_value = completed_message(self.initial(), partial,
            error=234, native_success=0, native_tag=1, required=len(complete))
        result = query.read()
        self.assertEqual((result.error, result.native_success, result.native_result_tag), (234, 0, 1))
        self.assertEqual((result.message_size, result.message_required), (4, len(complete)))
        self.assertEqual(result.message_raw, partial)
        self.assertEqual(result.message, partial.decode("utf-8", errors="replace"))
        self.assertIs(query.read(), result)
        self.interface_callback.assert_called_once()
        self.process.allocate.assert_called_once()
        self.process.free.assert_called_once_with(ADDRESS)

    def test_unknown_delivery_preserves_whole_request_and_never_replays_or_frees_it(self):
        self.interface_callback.side_effect = BridgeError("ReadFile(pipe)", 109)
        with self.assertRaises(BridgeError) as caught:
            life.rename_save(self.client, "save games/临时/旧.v3", "新.v3", process=self.process)
        query = caught.exception.query
        self.assertIs(query.client, self.client)
        self.assertIs(query.process, self.process)
        self.assertEqual(query.result_address, ADDRESS)
        self.process.allocate.assert_called_once_with(len(self.initial()))
        self.process.free.assert_not_called()
        self.process.close.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            query.read()
        self.interface_callback.assert_called_once()
        self.process.free.assert_not_called()


if __name__ == "__main__":
    unittest.main()
