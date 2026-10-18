"""Focused copied SaveCatalog wire tests; no native filesystem, cloud or process is opened."""
from __future__ import annotations

import struct
import unittest
from unittest.mock import Mock

from pdx_runtime import v3_lifecycle as life
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES


def record(path, directory, name, *, version="1.13.11", cloud=0, valid=1,
           ironman=0, players=1, modified=-1, last_date_raw=0,
           last_date_text="", flag_resource_id=0, rank_text="", game_date_raw=0,
           game_date_text=""):
    strings = [value.encode("utf-8") for value in
               (path, directory, name, version, last_date_text, rank_text, game_date_text)]
    body = b"".join(struct.pack("<I", len(value)) + value for value in strings)
    return life.SAVE_DESCRIPTOR_HEADER.pack(56 + len(body), cloud, valid,
        ironman, players, 0, modified, last_date_raw, flag_resource_id, 0, game_date_raw) + body


def response(initial, published, *, error=0, total=None, required=None):
    header = list(life.SAVE_CATALOG_HEADER.unpack_from(initial))
    output = b"".join(published)
    header[1] = error
    header[7] = len(output)
    header[8] = len(output) if required is None else required
    header[9] = len(published)
    header[10] = len(published) if total is None else total
    header[11:] = [7372, 0x30000, 0x40000, 0]
    start = 72 + header[4] + header[5]
    return (life.SAVE_CATALOG_HEADER.pack(*header) + initial[72:start]
            + output + bytes(header[6] - len(output)))


class SaveCatalogTests(unittest.TestCase):
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
        self.process.allocate.return_value = 0x20000

    def initial(self):
        return self.process.write.call_args.args[1]

    def test_utf8_list_inputs_and_explicit_native_sources(self):
        directory, name = "save games/临时", "存档一"
        life.list_saves(self.client, directory=directory, name_filter=name,
                        sources=life.SaveSource.LOCAL | life.SaveSource.CLOUD,
                        output_capacity=96, process=self.process)
        initial = self.initial()
        header = life.SAVE_CATALOG_HEADER.unpack_from(initial)
        self.assertEqual(life.SAVE_CATALOG_HEADER.size, 72)
        self.assertEqual(life.SAVE_DESCRIPTOR_HEADER.size, 56)
        self.assertEqual(header[:7], (72 + len(directory.encode()) + len(name.encode()) + 96,
                                     997, life.SaveCatalogAction.LIST, 3,
                                     len(directory.encode()), len(name.encode()), 96))
        self.assertEqual(initial[72:72 + header[4] + header[5]],
                         directory.encode() + name.encode())
        self.process.resolve_export.assert_called_once_with("pdx_v3_lifecycle.dll", "PdxV3SaveCatalogInterface", aliases=V3_EXPORT_ALIASES)
        before = self.interface_callback.call_count
        with self.assertRaises(ValueError):
            life.list_saves(self.client, directory="bad\0path", process=self.process)
        self.assertEqual(self.interface_callback.call_count, before)

    def test_descriptor_decode_is_owned_unicode_metadata_and_pending_keeps_storage(self):
        query = life.describe_save(self.client, "save games/临时/一.v3", cloud=True,
                                   output_capacity=256, process=self.process)
        self.world_status.return_value = {"status": "ok", "completed_request_id": 6}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        copied = record("save games/临时/一.v3", "save games/临时", "一", cloud=1,
                        valid=1, ironman=1, players=2, version="版本 1.13.11",
                        modified=-9223372036854775807,
                        last_date_raw=0xFFFFFFFF29C77F80, last_date_text="126.10.14",
                        flag_resource_id=0xFEDCBA98, rank_text="强权国家",
                        game_date_raw=0xABCDEF1234567890, game_date_text="1836.1.3")
        self.process.read.return_value = response(self.initial(), [copied])
        self.world_status.return_value = {"status": "ok", "completed_request_id": 7}
        result = query.read()
        self.assertEqual(result.action, life.SaveCatalogAction.DESCRIBE_PATH)
        self.assertEqual(result.records[0].path, "save games/临时/一.v3")
        self.assertEqual(result.records[0].directory, "save games/临时")
        self.assertEqual(result.records[0].name, "一")
        self.assertEqual(result.records[0].cloud, 1)
        self.assertEqual(result.records[0].is_valid, 1)
        self.assertEqual(result.records[0].is_ironman_save, 1)
        self.assertEqual(result.records[0].number_of_players, 2)
        self.assertTrue(result.records[0].is_multiplayer)
        self.assertEqual(result.records[0].version_name, "版本 1.13.11")
        self.assertEqual(result.records[0].last_played_date_raw, 0xFFFFFFFF29C77F80)
        self.assertEqual(result.records[0].last_played_date_text, "126.10.14")
        self.assertEqual(result.records[0].last_played_date_parts, (126, 10, 14, 0))
        self.assertEqual(result.records[0].game_date_raw, 0xABCDEF1234567890)
        self.assertEqual(result.records[0].game_date_text, "1836.1.3")
        self.assertEqual(result.records[0].game_date_parts, (1836, 1, 3, 0))
        self.assertEqual(result.records[0].country_flag_id, 0xFEDCBA98)
        self.assertEqual(result.records[0].country_rank_text, "强权国家")
        self.assertEqual(result.records[0].modified_time_raw, -9223372036854775807)
        self.assertEqual(result.descriptor, 0)
        self.assertIs(query.read(), result)
        self.process.free.assert_called_once_with(0x20000)

    def test_more_data_preserves_complete_records_and_does_not_repeat_scan(self):
        first = record("save games/one.v3", "save games", "one", last_date_text="126.10.14.3",
                       game_date_text="1836.1.3.6")
        second = record("save games/two.v3", "save games", "two")
        query = life.list_saves(self.client, output_capacity=len(first), process=self.process)
        self.process.read.return_value = response(self.initial(), [first], error=234,
                                                  total=2, required=len(first) + len(second))
        result = query.read()
        self.assertEqual(result.error, 234)
        self.assertEqual((result.count, result.total_count), (1, 2))
        self.assertEqual(result.output_required, len(first) + len(second))
        self.assertEqual(tuple(value.name for value in result.records), ("one",))
        self.assertFalse(result.records[0].is_multiplayer)
        self.assertEqual(result.records[0].last_played_date_parts, (126, 10, 14, 3))
        self.assertEqual(result.records[0].game_date_parts, (1836, 1, 3, 6))
        self.assertIs(query.read(), result)
        self.interface_callback.assert_called_once()
        self.process.free.assert_called_once_with(0x20000)

    def test_unknown_catalog_delivery_preserves_original_request_without_replay(self):
        self.interface_callback.side_effect = BridgeError("ReadFile(pipe)", 109)
        with self.assertRaises(BridgeError) as caught:
            life.list_saves(self.client, process=self.process)
        query = caught.exception.query
        self.assertIs(query.client, self.client)
        self.assertEqual(query.result_address, 0x20000)
        self.process.free.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            query.read()
        self.interface_callback.assert_called_once()
        self.process.free.assert_not_called()


if __name__ == "__main__":
    unittest.main()
