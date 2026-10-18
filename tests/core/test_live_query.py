"""Fixed-layout decode and read-error tests; fixtures are not live acceptance."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from pdx_runtime import live_query as live

BASE = 0x180000000
APP = 0x20000000
DATABASE = 0x21000000
SLOTS = 0x22000000
FALLBACK = 0x23000000
OBJECT = 0x24000000


class Memory:
    def __init__(self):
        self.parts = {}

    def put(self, address, value, size):
        self.parts[(address, size)] = value.to_bytes(size, "little")

    def read(self, address, size):
        try:
            return self.parts[(address, size)]
        except KeyError:
            raise live.ReadFailure("read_failed", winerror=299,
                                   requested_bytes=size, received_bytes=0)

    def loaded(self, bound=3):
        self.put(BASE + live.APPLICATION_RVA, APP, 8)
        self.put(APP + live.WORLD_OFFSET, 0x25000000, 8)
        self.put(BASE + live.COUNTRY_DATABASE_RVA, DATABASE, 8)
        self.put(BASE + live.COUNTRY_FALLBACK_RVA, FALLBACK, 8)
        self.put(DATABASE + live.DATABASE_SLOTS_OFFSET, SLOTS, 8)
        self.put(DATABASE + live.DATABASE_BOUND_OFFSET, bound, 4)
        for index in range(bound):
            self.put(SLOTS + 16 * index + 8, 0, 8)
        return self

    def country(self, index, full_id, raw, obj=OBJECT):
        self.put(SLOTS + 16 * index + 8, obj, 8)
        self.put(obj + live.COUNTRY_ID_OFFSET, full_id, 4)
        self.put(obj + live.COUNTRY_GDP_OFFSET, raw, 8)
        return self


class LiveQueryTests(unittest.TestCase):
    def test_world_not_loaded_does_not_touch_database(self):
        memory = Memory()
        memory.put(BASE + live.APPLICATION_RVA, APP, 8)
        memory.put(APP + live.WORLD_OFFSET, 0, 8)
        result = live.sample_countries(memory.read, BASE)
        self.assertEqual(result["status"], "world_not_loaded")
        self.assertEqual(result["countries"], [])

    def test_aslr_full_id_and_raw64_preserve_bits(self):
        full_id = 0xAB000002
        raw = 0xFEDCBA9876543210
        memory = Memory().loaded().country(2, full_id, raw)
        result = live.sample_countries(memory.read, BASE)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["countries"], [{
            "country_id": full_id, "country_id_hex": "0xab000002",
            "gdp_raw64": raw, "gdp_raw64_hex": "0xfedcba9876543210"}])
        self.assertEqual(result["gdp_interpretation"]["signedness"], "unknown")

    def test_fallback_and_wrong_slot_ids_are_excluded(self):
        memory = Memory().loaded().country(1, 0xAB000002, 8)
        memory.put(SLOTS + 8, FALLBACK, 8)
        result = live.sample_countries(memory.read, BASE)
        self.assertEqual(result["countries"], [])
        self.assertEqual(result["stats"]["fallback_slots"], 1)
        self.assertEqual(result["stats"]["id_mismatches"], 1)

    def test_explicit_country_requires_entire_id_not_only_slot(self):
        memory = Memory().loaded().country(2, 0xAB000002, 18)
        result = live.sample_countries(memory.read, BASE, country_id=0xCD000002)
        self.assertEqual(result["status"], "invalid_country_id")
        self.assertEqual(result["reason"], "full_id_mismatch")
        valid = live.sample_countries(memory.read, BASE, country_id=0xAB000002)
        self.assertEqual(valid["countries"][0]["gdp_raw64"], 18)
        self.assertEqual(valid["stats"]["scanned_slots"], 1)

    def test_root_and_object_read_errors_are_reported(self):
        root_error = live.sample_countries(Memory().read, BASE)
        self.assertEqual(root_error["status"], "read_failed")
        self.assertEqual(root_error["errors"][0]["stage"], "application")
        memory = Memory().loaded().country(1, 0xAB000001, 18)
        del memory.parts[(OBJECT + live.COUNTRY_GDP_OFFSET, 8)]
        object_error = live.sample_countries(memory.read, BASE)
        self.assertEqual(object_error["status"], "read_failed")
        self.assertEqual(object_error["errors"][0]["slot_index"], 1)
        self.assertEqual(object_error["errors"][0]["stage"], "country_gdp")

    def test_bad_index_bound_uses_one_fixed_scan_budget(self):
        memory = Memory().loaded(bound=live.MAX_SCAN_SLOTS)
        memory.put(DATABASE + live.DATABASE_BOUND_OFFSET, 0xFFFFFFFF, 4)
        result = live.sample_countries(memory.read, BASE)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stats"]["scanned_slots"], live.MAX_SCAN_SLOTS)
        self.assertTrue(result["scan_budget_exhausted"])

    def test_requested_country_in_empty_database_is_invalid(self):
        memory = Memory().loaded(bound=0)
        result = live.sample_countries(memory.read, BASE, country_id=0)
        self.assertEqual(result["status"], "invalid_country_id")
        self.assertEqual(result["reason"], "index_out_of_range")

    def test_limit_stops_after_valid_countries(self):
        memory = Memory().loaded().country(0, 0xAB000000, 18)
        result = live.sample_countries(memory.read, BASE, limit=1)
        self.assertEqual(len(result["countries"]), 1)
        self.assertEqual(result["stats"]["scanned_slots"], 1)
        self.assertTrue(result["limit_reached"])

    def test_cli_calls_query_and_emits_result_not_placeholder(self):
        expected = {"sample_kind": "external_sample", "status": "read_failed",
                    "errors": [{"winerror": 299}]}
        output = StringIO()
        with patch.object(live, "query", return_value=expected) as query:
            with redirect_stdout(output):
                code = live.main(["--pid", "123", "--country-id", "0xab000002", "--limit", "4"])
        query.assert_called_once_with(123, 4, 0xAB000002)
        self.assertEqual(json.loads(output.getvalue()), expected)
        self.assertEqual(code, 1)

    def test_raw_read_has_no_game_build_or_world_gate(self):
        api = unittest.mock.Mock()
        api.main_module.return_value = {"name": "other.exe", "path": "other.exe", "base": BASE, "size": 0x1000}
        process = unittest.mock.Mock()
        process.image_path.return_value = "other.exe"
        process.read.return_value = b"MZ"
        with patch.object(live, "WindowsAPI", return_value=api), \
                patch.object(live, "WindowsProcess", return_value=process), \
                patch.object(live, "file_sha256", side_effect=AssertionError("raw read must not hash game layout")):
            result = live.query(42, 20, None, read_rva=0, size=2)
        process.read.assert_called_once_with(BASE, 2)
        process.close.assert_called_once()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["raw_read"]["bytes_hex"], "4d5a")
        self.assertFalse(result["consistent_snapshot"])

    def test_raw_read_cli_forwards_explicit_address_and_size(self):
        output = StringIO()
        with patch.object(live, "query", return_value={"status": "ok"}) as query:
            with redirect_stdout(output):
                self.assertEqual(live.main(["--pid", "42", "--read-address", "0x1234", "--size", "8"]), 0)
        query.assert_called_once_with(42, 20, None, read_rva=None, read_address=0x1234, size=8)


if __name__ == "__main__":
    unittest.main()
