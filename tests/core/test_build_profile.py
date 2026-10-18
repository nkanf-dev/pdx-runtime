"""Behavior tests for offline verification; all fixtures are inert byte files."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from pdx_runtime import build_profile

BASE = 0x140000000
TEXT_FLAGS = 0x60000020
RDATA_FLAGS = 0x40000040


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inert_pe():
    data = bytearray(0x600)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 2)
    struct.pack_into("<H", data, 0x94, 0xf0)
    struct.pack_into("<H", data, 0x98, 0x20b)
    struct.pack_into("<Q", data, 0x98 + 24, BASE)
    for i, (name, rva, offset, flags) in enumerate([
            (b".text", 0x1000, 0x200, TEXT_FLAGS),
            (b".rdata", 0x2000, 0x400, RDATA_FLAGS)]):
        struct.pack_into("<8sIIIIIIHHI", data, 0x188 + i * 40,
                         name, 0x200, rva, 0x200, offset, 0, 0, 0, 0, flags)
    data[0x200] = 0xc3
    struct.pack_into("<Q", data, 0x400, BASE + 0x1000)
    return bytes(data)


class BuildProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / "docs"
        self.docs.mkdir()
        self.binary = self.root / "inert.exe"
        self.binary.write_bytes(inert_pe())
        self.build_sha = sha(self.binary.read_bytes())
        self.code = "int captured()\n{\n  return 1;\n}\n"
        self.source = self.root / "capture.json"
        self.source_value = {
            "capture": {"input_sha256": self.build_sha,
                        "database_identity": {"sha256": self.build_sha, "image_base": hex(BASE)}},
            "result": [{"rva": "0x1000", "ea": hex(BASE + 0x1000), "pseudocode": self.code}],
        }
        self.source.write_text(json.dumps(self.source_value), encoding="utf-8")
        source_sha = sha(self.source.read_bytes())
        self.note = self.docs / "function.md"
        self.note.write_text(
            f"# 0x1000\n\n- 输入 SHA-256：`{self.build_sha}`\n"
            f"- 捕获状态：`captured_with_unresolved_edges`\n- 来源 SHA-256：`{source_sha}`\n"
            f"\n```c\n{self.code}\n```\n", encoding="utf-8")
        entry = {"ea": hex(BASE + 0x1000), "note": "function.md",
                 "capture_state": "captured_with_unresolved_edges", "runtime_verified": False,
                 "code_sha256": sha(self.code.encode()), "source_sha256": source_sha,
                 "local_source": str(self.source)}
        self.index_value = {"schema_version": 1, "input_sha256": self.build_sha,
                            "functions": {"0x1000": entry}}
        self.index = self.docs / "index.json"
        self.index.write_text(json.dumps(self.index_value), encoding="utf-8")
        self.value = {
            "schema_version": 1, "artifact_kind": "static_research_profile",
            "build": {"game": "victoria3", "sha256": self.build_sha,
                      "machine": "0x8664", "image_base": hex(BASE)},
            "function_index": "index.json",
            "anchors": {
                "fn": {"rva": "0x1000", "size": 1, "kind": "code",
                       "section": ".text", "section_flags": hex(TEXT_FLAGS)},
                "table": {"rva": "0x2000", "size": 8, "kind": "vtable",
                          "section": ".rdata", "section_flags": hex(RDATA_FLAGS)},
            },
            "vtables": {"command": {"anchor": "table", "slots": {
                "0": {"target_rva": "0x1000", "evidence": "fn"}}}},
            "evidence": {"fn": {
                "input_sha256": self.build_sha, "rva": "0x1000", "note": "function.md",
                "capture_state": entry["capture_state"], "code_sha256": entry["code_sha256"],
                "source_sha256": source_sha, "note_sha256": sha(self.note.read_bytes()),
            }},
            "capabilities": {"construct": {"kind": "command",
                "structure_dependencies": {"anchors": ["fn", "table"], "vtable_slots": [
                    {"table": "command", "slot": 0, "evidence": "fn"}]}, "proofs": {
                requirement: {"state": "static_confirmed", "evidence": ["fn"]}
                for requirement in build_profile.REQUIRED_PROOFS["command"]}}},
        }
        self.profile = self.docs / "profile.json"

    def verify(self, value=None):
        self.profile.write_text(json.dumps(self.value if value is None else value), encoding="utf-8")
        return build_profile.verify_profile(self.binary, self.profile, self.docs)

    def assert_structural_failure(self):
        report = self.verify()
        self.assertFalse(report["structural_valid"])
        self.assertTrue(report["structural_errors"])
        self.assertEqual(report["available_runtime_capabilities"], [])
        return report

    def update_source_pins(self):
        self.source.write_text(json.dumps(self.source_value), encoding="utf-8")
        old_sha = self.value["evidence"]["fn"]["source_sha256"]
        new_sha = sha(self.source.read_bytes())
        self.index_value["functions"]["0x1000"]["source_sha256"] = new_sha
        self.index.write_text(json.dumps(self.index_value), encoding="utf-8")
        self.value["evidence"]["fn"]["source_sha256"] = new_sha
        text = self.note.read_text(encoding="utf-8").replace(old_sha, new_sha)
        self.note.write_text(text, encoding="utf-8")
        self.value["evidence"]["fn"]["note_sha256"] = sha(self.note.read_bytes())

    def test_valid_unresolved_capture_is_not_semantic_certificate_or_automatic_failure(self):
        report = self.verify()
        self.assertTrue(report["structural_valid"], report["structural_errors"])
        self.assertTrue(report["source_provenance_complete"])
        capability = report["capabilities"]["construct"]
        self.assertTrue(capability["declared_static_proofs_complete"])
        self.assertEqual(capability["runtime_status"], "unavailable")
        self.assertFalse(report["runtime_implemented"])
        self.assertFalse(report["semantic_proofs_verified"])
        self.assertFalse(capability["semantic_proofs_verified"])
        self.assertTrue(capability["structural_checks_passed"])
        self.assertEqual(report["available_runtime_capabilities"], [])

    def test_build_identity_rejects_wrong_binary_machine_and_base(self):
        original = self.binary.read_bytes()
        for offset, payload in [(0x250, b"x"), (0x84, struct.pack("<H", 0x14c)),
                                (0x98 + 24, struct.pack("<Q", BASE + 0x100000))]:
            with self.subTest(offset=offset):
                data = bytearray(original)
                data[offset:offset + len(payload)] = payload
                self.binary.write_bytes(data)
                self.assert_structural_failure()
        self.binary.write_bytes(original)
        for field, value in [("sha256", "0" * 64), ("machine", "0x14c"),
                             ("image_base", "0x180000000")]:
            changed = deepcopy(self.value)
            changed["build"][field] = value
            self.assertFalse(self.verify(changed)["structural_valid"])

    def test_vtable_slot_pointer_and_span_are_checked(self):
        self.value["vtables"]["command"]["slots"] = {
            "1": {"target_rva": "0x1000", "evidence": "fn"}}
        self.assert_structural_failure()
        self.value["vtables"]["command"]["slots"] = {
            "0": {"target_rva": "0x1001", "evidence": "fn"}}
        self.assert_structural_failure()
        self.value["vtables"]["command"]["slots"]["0"]["target_rva"] = "0x1000"
        data = bytearray(self.binary.read_bytes())
        struct.pack_into("<Q", data, 0x400, BASE + 0x1001)
        self.binary.write_bytes(data)
        old_build_sha = self.build_sha
        self.build_sha = sha(data)
        self.value["build"]["sha256"] = self.build_sha
        self.value["evidence"]["fn"]["input_sha256"] = self.build_sha
        self.index_value["input_sha256"] = self.build_sha
        self.source_value["capture"]["input_sha256"] = self.build_sha
        self.source_value["capture"]["database_identity"]["sha256"] = self.build_sha
        self.note.write_text(self.note.read_text(encoding="utf-8").replace(old_build_sha, self.build_sha),
                             encoding="utf-8")
        self.update_source_pins()
        report = self.assert_structural_failure()
        self.assertTrue(all(error["scope"].startswith("vtable:") for error in report["structural_errors"]))

    def test_anchor_section_flags_kind_and_boundaries(self):
        for changes in [{"section_flags": "0xe0000020"}, {"kind": "vtable"},
                        {"rva": "0x11ff", "size": 2}, {"rva": "0x9000"}]:
            changed = deepcopy(self.value)
            changed["anchors"]["fn"].update(changes)
            self.assertFalse(self.verify(changed)["structural_valid"])

    def test_markdown_missing_or_tampered_even_with_unchanged_index(self):
        original = self.note.read_bytes()
        self.note.write_bytes(original + b"tampered")
        self.assert_structural_failure()
        self.note.unlink()
        self.assert_structural_failure()


    def test_index_mismatch_and_missing_evidence(self):
        self.index_value["functions"]["0x1000"]["code_sha256"] = "0" * 64
        self.index.write_text(json.dumps(self.index_value), encoding="utf-8")
        self.assert_structural_failure()
        self.index_value["functions"] = {}
        self.index.write_text(json.dumps(self.index_value), encoding="utf-8")
        self.assert_structural_failure()

    def test_raw_source_missing_or_tampered_is_not_silently_skipped(self):
        self.source.write_bytes(self.source.read_bytes() + b" ")
        report = self.assert_structural_failure()
        self.assertTrue(report["source_provenance"][0]["availability"] == "available")
        self.assertFalse(report["source_provenance"][0]["hash_verified"])
        self.source.unlink()
        report = self.assert_structural_failure()
        self.assertEqual(report["source_provenance"][0]["availability"], "missing")

    def test_source_database_identity_must_match_even_when_input_sha_matches(self):
        self.source_value["capture"]["database_identity"]["sha256"] = "f" * 64
        self.update_source_pins()
        self.assert_structural_failure()

    def test_legacy_source_is_hash_verified_but_build_provenance_is_incomplete(self):
        del self.source_value["capture"]
        self.update_source_pins()
        report = self.verify()
        self.assertTrue(report["structural_valid"])
        self.assertFalse(report["source_provenance_complete"])
        self.assertTrue(report["source_provenance"][0]["hash_verified"])
        self.assertEqual(report["source_provenance"][0]["build_identity"], "not_recorded")

    def test_capture_with_missing_database_identity_stays_partial(self):
        for database_identity in [None, {}, {"sha256": self.build_sha}, {"image_base": hex(BASE)}]:
            with self.subTest(database_identity=database_identity):
                if database_identity is None:
                    self.source_value["capture"].pop("database_identity", None)
                else:
                    self.source_value["capture"]["database_identity"] = database_identity
                self.update_source_pins()
                report = self.verify()
                self.assertTrue(report["structural_valid"], report["structural_errors"])
                self.assertFalse(report["source_provenance_complete"])
                self.assertEqual(report["source_provenance"][0]["build_identity"], "partial")

    def test_fixed_proof_policy_cannot_be_weakened_by_removing_requirements(self):
        for kind in ["command", "query"]:
            changed = deepcopy(self.value)
            changed["capabilities"]["construct"].update({"kind": kind, "required_proofs": [], "proofs": {}})
            report = self.verify(changed)
            self.assertTrue(report["structural_valid"])
            blockers = report["capabilities"]["construct"]["proof_blockers"]
            self.assertEqual({b["requirement"] for b in blockers},
                             set(build_profile.REQUIRED_PROOFS[kind]))
            self.assertFalse(report["capabilities"]["construct"]["declared_static_proofs_complete"])
        del self.value["capabilities"]["construct"]["proofs"]["ownership"]
        self.assertFalse(self.verify()["capabilities"]["construct"]["declared_static_proofs_complete"])

    def test_candidate_missing_invalid_evidence_and_unsupported_kinds_block_declarations(self):
        for proof in [{"state": "candidate", "evidence": ["fn"]}, {"state": "missing"},
                      {"state": "static_confirmed", "evidence": []},
                      {"state": "static_confirmed", "evidence": ["unknown"]}]:
            changed = deepcopy(self.value)
            changed["capabilities"]["construct"]["proofs"]["abi"] = proof
            self.assertFalse(self.verify(changed)["capabilities"]["construct"]["declared_static_proofs_complete"])
        self.value["capabilities"]["construct"]["kind"] = "unsupported"
        report = self.verify()
        self.assertFalse(report["capabilities"]["construct"]["declared_static_proofs_complete"])
        self.assertFalse(report["structural_valid"])
        self.assertFalse(report["capabilities"]["construct"]["structural_checks_passed"])
        self.assertEqual(report["available_runtime_capabilities"], [])

    def test_forged_runtime_declaration_cannot_enable_runtime(self):
        self.value["runtime_verified"] = True
        self.value["runtime_implemented"] = True
        self.value["available_runtime_capabilities"] = ["construct"]
        self.value["capabilities"]["construct"]["runtime_verified"] = True
        report = self.verify()
        self.assertTrue(report["structural_valid"])
        self.assertFalse(report["runtime_implemented"])
        self.assertEqual(report["available_runtime_capabilities"], [])

    def test_cli_exit_is_structural_only_and_output_is_json(self):
        self.value["capabilities"]["construct"]["proofs"] = {}
        self.verify()
        argv = ["build_profile", str(self.binary), str(self.profile), "--docs", str(self.docs)]
        with patch("sys.argv", argv), redirect_stdout(StringIO()) as output:
            with self.assertRaises(SystemExit) as result:
                build_profile.main()
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(json.loads(output.getvalue())["available_runtime_capabilities"], [])
        self.note.unlink()
        with patch("sys.argv", argv), redirect_stdout(StringIO()) as output:
            with self.assertRaises(SystemExit) as result:
                build_profile.main()
        self.assertEqual(result.exception.code, 1)
        self.assertFalse(json.loads(output.getvalue())["structural_valid"])
        self.value["capabilities"]["construct"]["kind"] = "unknown"
        self.verify()
        with patch("sys.argv", argv), redirect_stdout(StringIO()) as output:
            with self.assertRaises(SystemExit) as result:
                build_profile.main()
        self.assertEqual(result.exception.code, 1)
        self.assertFalse(json.loads(output.getvalue())["structural_valid"])


    def test_removing_a_checked_slot_blocks_capability_structure_dependencies(self):
        self.value["vtables"] = {}
        report = self.verify()
        self.assertTrue(report["structural_valid"])
        capability = report["capabilities"]["construct"]
        self.assertFalse(capability["declared_static_proofs_complete"])
        self.assertFalse(capability["declared_structure_dependencies_valid"])
        self.assertEqual(report["available_runtime_capabilities"], [])

    def test_malformed_profile_is_a_structural_failure(self):
        for value in [[], {}, {"schema_version": 4},
                      {**self.value, "anchors": []}, {**self.value, "evidence": []},
                      {**self.value, "anchors": {"bad": None}},
                      {**self.value, "evidence": {"bad": None}},
                      {**self.value, "vtables": {"bad": None}}]:
            self.assertFalse(self.verify(value)["structural_valid"])


if __name__ == "__main__":
    unittest.main()
