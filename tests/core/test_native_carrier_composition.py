"""Carrier composition source checks and opt-in isolated stub-host wire fixtures.

No test discovers or opens a game. PDX_BRIDGE_DISPATCH_HOST selects a source-linked
host with stub V3 entrypoints; it never loads a runtime DLL or starts its pipe.
These tests do not prove native game execution or REAL GAME regression success.
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest


class NativeCarrierStubHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("PDX_BRIDGE_DISPATCH_HOST")
        if not path:
            raise unittest.SkipTest("set PDX_BRIDGE_DISPATCH_HOST for isolated stub-adapter host fixtures")
        result = subprocess.run([path], capture_output=True, text=True, timeout=30, check=True)
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        cls.rows = {row["case"]: row for row in rows}
        if len(cls.rows) != len(rows):
            raise AssertionError("duplicate fixture case")

    def test_raw_status_protocol(self):
        status = self.rows["status"]["response"]
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["protocol"], 1)
        self.assertGreater(status["pid"], 0)
        self.assertEqual(status["max_raw"], 4096)
        self.assertEqual(status["error"], 0)
        self.assertEqual(status["native_context"], "worker_raw_memory_and_enqueue")
        self.assertEqual(status, self.rows["status_trailing_preserved"]["response"])
        self.assertEqual(self.rows["status"]["world_reads"], 0)

    def test_unknown_and_malformed_raw_commands_keep_exact_errors(self):
        invalid = ("empty", "unknown_empty", "unknown_text", "unknown_negative", "no_adapter",
                   "raw_read_zero", "raw_read_oversize", "raw_read_extra", "raw_write_odd",
                   "raw_write_invalid", "raw_write_extra")
        for name in invalid:
            self.assertEqual(self.rows[name]["response"], {"status": "error", "error": 87, "bytes": 0}, name)
        for name in ("unknown_number", "unknown_extra", "foreign_unclaimed"):
            self.assertEqual(self.rows[name]["response"], {"status": "error", "error": 1, "bytes": 0}, name)

    def test_generic_core_supports_absent_and_foreign_adapters_and_raw_io(self):
        self.assertEqual(self.rows["foreign_adapter"]["response"],
                         {"status": "ok", "value": 0, "enabled": False})
        for name in ("raw_read", "foreign_raw_read"):
            self.assertEqual(self.rows[name]["response"],
                             {"status": "ok", "error": 0, "bytes": 8, "hex": "50445800783634ff"})
        self.assertEqual(self.rows["raw_write"]["response"], {"status": "ok", "error": 0, "bytes": 3})
        self.assertEqual(self.rows["raw_read_after_write"]["response"],
                         {"status": "ok", "error": 0, "bytes": 3, "hex": "00ffa5"})
        for name in ("foreign_adapter", "raw_read", "foreign_raw_read", "raw_write", "no_adapter"):
            self.assertEqual(self.rows[name]["world_reads"], 0, name)
            self.assertEqual(self.rows[name]["construction_reads"], 0, name)

    def test_world_response_preserves_all_fields_full_width_and_zero(self):
        expected = {
            "status": "ok", "error": 0, "world_error": 123, "installed": 0,
            "phase": 8, "pending_operation": 0, "interface_thread_id": 44,
            "application_state": 9, "transition_pending": 0,
            "request_id": 0x100000007, "completed_request_id": 0x100000006,
            "main_module_base": "0x140000000", "application": "0x200000001",
            "original_vtable": "0x200000002", "hook_vtable": "0x200000003",
            "interface_function": "0x200000004", "options_factory": "0x200000005",
            "entry_function": "0x200000006", "options_result": "0x0",
            "world": "0x200000007", "active_idler": "0x200000008",
            "active_idler_vtable": "0x200000009", "pending_idler": "0x0", "frame": 0x100000008,
        }
        self.assertEqual(self.rows["world_status"]["response"], expected)
        self.assertEqual(self.rows["world_status"]["operation_calls"], 0)
        self.assertEqual(self.rows["world_status"]["world_reads"], 1)
        for name in ("world_error", "interface_callback_error"):
            self.assertEqual(self.rows[name]["response"], {**expected, "status": "error", "error": 170})
            self.assertEqual(self.rows[name]["operation_calls"], 1)

    def test_v3_dispatch_submits_once_with_exact_arguments(self):
        calls = {
            "hook_install": ("hook-install", 0, 0), "hook_remove": ("hook-remove", 0, 0),
            "options_create": ("options-create", 0, 0), "world_enter_zero": ("world-enter", 0, 0),
            "world_enter_full": ("world-enter", 0x100000001, 0),
            "options_submit_zero": ("options-submit", 0, 0),
            "options_destroy_full": ("options-destroy", 0x100000002, 0),
            "interface_callback_full": ("interface-callback", 0x100000003, 0x200000004),
            "interface_callback_zero": ("interface-callback", 0, 0),
            "construction_submit_full": ("construction-submit", 0xffffffff, 0x80000001),
            "construction_submit_zero": ("construction-submit", 0, 0),
        }
        for name, expected in calls.items():
            row = self.rows[name]
            self.assertEqual((row["operation"], row["argument1"], row["argument2"]), expected, name)
            self.assertEqual(row["operation_calls"], 1, name)
            self.assertEqual(row["response"]["status"], "ok", name)
        self.assertEqual(self.rows["construction_submit_full"]["submitted_size"], 24)
        self.assertEqual(self.rows["construction_submit_full"]["submitted_reserved"], 0)
        self.assertEqual(self.rows["construction_submit_full"]["submitted_type"], 0x100000009)

    def test_adapter_malformed_namespace_commands_never_submit_or_fall_back(self):
        world_invalid = ("world_status_argument", "hook_install_argument", "options_submit_missing",
                         "options_destroy_negative", "world_enter_extra", "world_unknown",
                         "hook_unknown", "options_unknown", "interface_callback_missing",
                         "interface_callback_extra", "interface_callback_negative")
        construction_invalid = ("construction_status_extra", "construction_submit_missing",
                                "construction_submit_overflow", "construction_state_overflow",
                                "construction_submit_negative", "construction_submit_extra", "construction_unknown")
        for names, key, status_reads in ((world_invalid, "world_error", "world_reads"),
                                         (construction_invalid, "construction_error", "construction_reads")):
            for name in names:
                row = self.rows[name]
                self.assertEqual(row["operation_calls"], 0, name)
                self.assertEqual(row[status_reads], 1, name)
                self.assertEqual(row["response"]["status"], "error", name)
                self.assertEqual(row["response"]["error"], 87, name)
                self.assertIn(key, row["response"], name)
                self.assertNotIn("bytes", row["response"], name)

    def test_construction_response_preserves_non_atomic_before_after_and_error_channels(self):
        before = {"error": 0, "country": "0x300000001", "queue": "0x300000002", "records": "0x300000003",
                  "count": 0, "capacity": 8, "matching_count": 0, "first_matching_index": 0xffffffff}
        expected = {
            "status": "ok", "error": 0, "construction_error": 456, "phase": 6, "accepted": 0,
            "execution_thread_id": 45, "effect_observed": 0, "request_id": 0x100000009,
            "completed_request_id": 0x100000008, "main_module_base": "0x140000000",
            "session": "0x200000010", "world": "0x200000007", "actor_id": 0xffffffff,
            "state_id": 0x80000001, "building_type": "0x100000009", "before": before,
            "after": {**before, "error": 789, "count": 1, "matching_count": 1, "first_matching_index": 0},
        }
        self.assertEqual(self.rows["construction_status"]["response"], expected)
        self.assertEqual(self.rows["construction_status"]["operation_calls"], 0)
        self.assertEqual(self.rows["construction_status"]["construction_reads"], 1)
        self.assertEqual(self.rows["construction_error"]["response"], {**expected, "status": "error", "error": 170})
        self.assertEqual(self.rows["construction_error"]["operation_calls"], 1)


if __name__ == "__main__":
    unittest.main()
