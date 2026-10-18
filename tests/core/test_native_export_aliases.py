"""CPU-only explicit export-alias policy and same-process composition checks.

The PE images and process reads are bytearray fixtures. No OS process is opened.
"""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from pdx_runtime import native_bridge
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES, resolve_v3_export
from tests.core.test_unified_sdk import Process


class ExplicitExportAliasTests(unittest.TestCase):
    def setUp(self):
        self.process = Process()
        self.base = self.process.base
        self.process.add("pdx_runtime.dll", self.base, {"Leaf": None})
        self.leaf = self.process.addresses[(self.base, "Leaf")]

    def test_default_resolver_does_not_select_v3_or_distribution_aliases(self):
        for name in (*V3_EXPORT_ALIASES, "foreign.dll"):
            with self.subTest(module=name), self.assertRaises(native_bridge.BridgeError) as caught:
                self.process.resolve_export(name, "Leaf")
            self.assertEqual(caught.exception.details["reason"], "module not loaded")
        self.assertEqual(self.process.resolve_export("pdx_runtime.dll", "Leaf"), self.leaf)

    def test_empty_map_is_not_an_implicit_runtime_fallback(self):
        with self.assertRaises(native_bridge.BridgeError):
            self.process.resolve_export("pdx_v3_data.dll", "Leaf", aliases={})
        with self.assertRaises(native_bridge.BridgeError):
            self.process.resolve_export("unrelated.dll", "Leaf", aliases=V3_EXPORT_ALIASES)


    def test_loaded_split_module_wins_before_mapping_is_even_inspected(self):
        split = self.base + 0x100000
        self.process.add("pdx_v3_data.dll", split, {"Leaf": None})
        poisoned = Mock()
        poisoned.items.side_effect = AssertionError("loaded module must not inspect alias policy")
        self.assertEqual(self.process.resolve_export("PDX_V3_DATA", "Leaf", aliases=poisoned), split + 0x2000)
        poisoned.items.assert_not_called()
        self.assertEqual(resolve_v3_export(self.process, "pdx_v3_data.dll", "Leaf"), split + 0x2000)

    def test_exact_integer_base_never_uses_alias_policy(self):
        poisoned = Mock()
        poisoned.items.side_effect = AssertionError("integer base must not inspect aliases")
        self.assertEqual(self.process.resolve_export(self.base, "Leaf", aliases=poisoned), self.leaf)
        with self.assertRaises(native_bridge.BridgeError):
            self.process.resolve_export(0xDEADBEEF, "Leaf", aliases=poisoned)
        poisoned.items.assert_not_called()

    def test_only_explicit_entry_and_actual_loaded_target_can_resolve(self):
        with self.assertRaises(native_bridge.BridgeError):
            self.process.resolve_export("foreign", "Leaf", aliases={"other": "pdx_runtime.dll"})
        with self.assertRaises(native_bridge.BridgeError):
            self.process.resolve_export("foreign", "Leaf", aliases={"foreign": "missing-target"})
        # No implicit transitive alias chain is introduced.
        with self.assertRaises(native_bridge.BridgeError):
            self.process.resolve_export("foreign", "Leaf", aliases={"foreign": "second", "second": "pdx_runtime.dll"})

    def test_alias_target_rejects_bool_and_invalid_types_or_addresses(self):
        for target in (True, False, None, 1.0, [], object()):
            with self.subTest(target=target), self.assertRaises(TypeError):
                self.process.resolve_export("legacy", "Leaf", aliases={"legacy": target})
        for target in (0, -1, 1 << 64, "", "bad\0name"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.process.resolve_export("legacy", "Leaf", aliases={"legacy": target})

    def test_loaded_module_missing_export_never_falls_back_to_alias_target(self):
        split = self.base + 0x100000
        self.process.add("pdx_v3_data.dll", split, {"DifferentExport": None})
        with self.assertRaises(native_bridge.BridgeError) as caught:
            resolve_v3_export(self.process, "pdx_v3_data.dll", "Leaf")
        self.assertEqual(caught.exception.details["reason"], "export not found")
        self.assertEqual(caught.exception.details["module"], "pdx_v3_data.dll")

    def test_explicit_v3_aliases_resolve_and_mapping_is_immutable(self):
        self.assertTrue(all(target == "pdx_runtime.dll" for target in V3_EXPORT_ALIASES.values()))
        for name in V3_EXPORT_ALIASES:
            self.assertEqual(resolve_v3_export(self.process, name, "Leaf"), self.leaf)
        with self.assertRaises(TypeError):
            V3_EXPORT_ALIASES["arbitrary.dll"] = "pdx_runtime.dll"
        with self.assertRaises(native_bridge.BridgeError):
            resolve_v3_export(self.process, "arbitrary.dll", "Leaf")


    def test_forwarded_export_retains_mapping_and_actual_leaf_owner(self):
        forwarder = self.base + 0x100000
        self.process.add("forwarder.dll", forwarder, {"Start": "legacy.Leaf"})
        aliases = {"legacy": "pdx_runtime.dll"}
        resolved = self.process.resolve_export("forwarder.dll", "Start", aliases=aliases)
        self.assertEqual(resolved, self.leaf)
        self.assertEqual(self.process.export_owner(resolved), self.base)
        with self.assertRaises(native_bridge.BridgeError):
            self.process.resolve_export("forwarder.dll", "Start")

    def test_forwarder_ordinal_and_cycle_detection_remain(self):
        forwarder = self.base + 0x100000
        self.process.add("ordinal.dll", forwarder, {"Start": "legacy.#1"})
        self.assertEqual(self.process.resolve_export("ordinal", "Start",
            aliases={"legacy": self.base}), self.leaf)
        alpha, beta = self.base + 0x200000, self.base + 0x300000
        self.process.add("alpha.dll", alpha, {"Start": "legacy-beta.Next"})
        self.process.add("beta.dll", beta, {"Next": "alpha.Start"})
        with self.assertRaises(native_bridge.BridgeError) as caught:
            self.process.resolve_export("alpha", "Start", aliases={"legacy-beta": "beta"})
        self.assertEqual(caught.exception.details["reason"], "forwarder cycle")


if __name__ == "__main__":
    unittest.main()
