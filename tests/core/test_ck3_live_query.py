"""Synthetic memory checks for the CK3 static layout, not live acceptance."""
import unittest

from pdx_runtime import ck3_live_query as ck3

BASE = 0x180000000
DB = 0x20000000
SLOTS = 0x21000000
FALLBACK = 0x22000000
CHARACTER = 0x23000000
COMPONENT = 0x24000000


class Memory:
    def __init__(self, bound=3):
        self.parts = {}
        self.put(BASE + ck3.CHARACTER_DATABASE_RVA, DB, 8)
        self.put(BASE + ck3.CHARACTER_FALLBACK_RVA, FALLBACK, 8)
        self.put(DB + ck3.DATABASE_SLOTS_OFFSET, SLOTS, 8)
        self.put(DB + ck3.DATABASE_BOUND_OFFSET, bound, 4)
        for index in range(bound):
            self.put(SLOTS + index * 16 + 8, 0, 8)

    def put(self, address, value, size, signed=False):
        self.parts[address, size] = value.to_bytes(size, "little", signed=signed)

    def read(self, address, size):
        try:
            return self.parts[address, size]
        except KeyError:
            raise ck3.ReadFailure("read_failed", requested_bytes=size, received_bytes=0)

    def character(self, full_id=0xAB000002, raw=0xFEDCBA9876543210):
        self.put(SLOTS + 2 * 16 + 8, CHARACTER, 8)
        self.put(CHARACTER + ck3.CHARACTER_ID_OFFSET, full_id, 4)
        self.put(CHARACTER + ck3.CHARACTER_TYPE_OFFSET, ck3.CHARACTER_TYPE_TAG, 4)
        self.put(CHARACTER + ck3.CHARACTER_COMPONENT_OFFSET, COMPONENT, 8)
        self.put(CHARACTER + ck3.HEALTH_SELECTOR_OFFSET, -1, 2, signed=True)
        self.put(COMPONENT + ck3.COMPONENT_HEALTH_OFFSET, raw, 8)
        return self


class CK3LiveQueryTests(unittest.TestCase):
    def test_component_health_keeps_all_raw_bits(self):
        memory = Memory().character()
        result = ck3.sample_characters(memory.read, BASE)
        self.assertEqual(result["status"], "ok")
        row = result["characters"][0]
        self.assertEqual(row["character_id"], 0xAB000002)
        self.assertEqual(row["health_raw64"], 0xFEDCBA9876543210)
        self.assertEqual(row["health_source"], "character_component")

    def test_native_resolver_fallback_semantics_and_full_id(self):
        memory = Memory().character()
        database = ck3.character_database(memory.read, BASE)
        self.assertEqual(ck3.resolve_character(memory.read, database, 0xAB000002), CHARACTER)
        self.assertEqual(ck3.resolve_character(memory.read, database, 0xCD000002), FALLBACK)
        self.assertEqual(ck3.resolve_character(memory.read, database, 0xAB000003), FALLBACK)
        self.assertEqual(ck3.resolve_character(memory.read, database, 0xAB000001), FALLBACK)

    def test_positive_selector_uses_global_last_value(self):
        memory = Memory().character()
        memory.put(CHARACTER + ck3.HEALTH_SELECTOR_OFFSET, 0, 2)
        memory.put(BASE + ck3.HEALTH_DEFAULT_COUNT_RVA, 2, 4)
        memory.put(BASE + ck3.HEALTH_DEFAULT_ARRAY_RVA, 0x25000000, 8)
        memory.put(0x25000008, 12345, 8)
        row = ck3.sample_characters(memory.read, BASE)["characters"][0]
        self.assertEqual(row["health_raw64"], 12345)
        self.assertEqual(row["health_source"], "global_last_value")

    def test_null_component_returns_zero_without_selector_read(self):
        memory = Memory().character()
        memory.put(CHARACTER + ck3.CHARACTER_COMPONENT_OFFSET, 0, 8)
        del memory.parts[CHARACTER + ck3.HEALTH_SELECTOR_OFFSET, 2]
        row = ck3.sample_characters(memory.read, BASE)["characters"][0]
        self.assertEqual(row["health_raw64"], 0)
        self.assertEqual(row["health_source"], "component_null")

    def test_type_tag_and_fallback_slots_are_excluded(self):
        memory = Memory().character()
        memory.put(SLOTS + 8, FALLBACK, 8)
        memory.put(CHARACTER + ck3.CHARACTER_TYPE_OFFSET, 0, 4)
        result = ck3.sample_characters(memory.read, BASE)
        self.assertEqual(result["characters"], [])
        self.assertEqual(result["stats"]["invalid_objects"], 1)
        self.assertEqual(result["stats"]["fallback_slots"], 1)

    def test_scan_limit_is_configurable_and_zero_enumerates(self):
        memory = Memory().character()
        limited = ck3.sample_characters(memory.read, BASE, scan_limit=2)
        self.assertEqual(limited["status"], "partial")
        self.assertEqual(limited["characters"], [])
        complete = ck3.sample_characters(memory.read, BASE, scan_limit=0)
        self.assertEqual(len(complete["characters"]), 1)

    def test_read_failure_and_null_root(self):
        memory = Memory().character()
        del memory.parts[COMPONENT + ck3.COMPONENT_HEALTH_OFFSET, 8]
        self.assertEqual(ck3.sample_characters(memory.read, BASE)["status"], "read_failed")
        memory.put(BASE + ck3.CHARACTER_DATABASE_RVA, 0, 8)
        self.assertEqual(ck3.sample_characters(memory.read, BASE)["status"],
                         "character_database_not_loaded")


if __name__ == "__main__":
    unittest.main()
