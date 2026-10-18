"""Offline checks for exact-build research profiles. Never loads native code.

Anchors cover raw-backed virtual spans only; BSS and runtime ASLR addresses are
not read. Profile declarations are research assertions, not semantic proofs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

# Compatibility reexports are the canonical shared PE/identity objects.
from .pe import AMD64, InvalidImage, PEImage, fingerprint, number
COMMON_PROOFS = (
    "object_type", "object_layout", "abi", "ownership", "execution_domain",
    "lifecycle", "result_semantics",
)
REQUIRED_PROOFS = {
    "query": COMMON_PROOFS + ("numeric_representation", "snapshot_consistency"),
    "command": COMMON_PROOFS + ("native_validation", "operation_identity"),
}
CAPTURE_STATES = {
    "needs_decode", "incomplete_analysis", "captured_with_unresolved_edges", "captured",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"Expected {label} mapping")
    return value


def document(root: Path, relative: str) -> Path:
    if not isinstance(relative, str):
        raise ValueError("Expected relative evidence path")
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError("Evidence path escapes docs root")
    return path


def code_block(text: str) -> str:
    matches = re.findall(r"^```c\n(.*?)\n```[ \t]*$", text, re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise ValueError("Expected one C capture block")
    return matches[0]


def verify_profile(binary: Path, profile: Path, docs: Path) -> dict:
    """Verify structure and provenance; report declared proof gaps, not callable APIs."""
    docs = docs.resolve()
    report = {
        "schema_version": 1,
        "structural_valid": False,
        "structural_errors": [],
        "source_provenance": [],
        "source_provenance_complete": False,
        "capabilities": {},
        "available_runtime_capabilities": [],
        "runtime_implemented": False,
        "proof_review": "Only mappings listed by this research profile are checked; declarations are not semantic or ABI verification",
        "semantic_proofs_verified": False,
    }
    errors = report["structural_errors"]

    def fail(scope: str, reason: str) -> None:
        errors.append({"scope": scope, "reason": reason})

    try:
        value = mapping(json.loads(profile.read_text(encoding="utf-8")), "profile")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["artifact_kind"] != "static_research_profile":
            raise ValueError("Unsupported research profile schema/kind")
        build = mapping(value["build"], "build")
        sha = fingerprint(build["sha256"])
        machine = number(build["machine"])
        base = number(build["image_base"])
        if build["game"] not in {"victoria3", "ck3"}:
            raise ValueError("Unsupported game")
        if machine != AMD64:
            raise ValueError("Profile must bind AMD64")
        image = PEImage(binary)
        report["build_sha256"] = image.sha256
        if image.sha256 != sha:
            fail("build", "Binary SHA-256 mismatch")
        if image.machine != AMD64 or image.machine != machine:
            fail("build", "Binary machine mismatch; AMD64 required")
        if image.image_base != base:
            fail("build", "Static image base mismatch")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        fail("profile_or_image", str(exc))
        return report

    anchors = value.get("anchors", {})
    valid_anchors = {}
    if not isinstance(anchors, dict) or not anchors:
        fail("anchors", "Nonempty anchor mapping required")
        anchors = {}
    for key, anchor in anchors.items():
        try:
            anchor = mapping(anchor, "anchor")
            rva, size = number(anchor["rva"]), number(anchor["size"])
            if size == 0:
                raise ValueError("Anchor span must be nonempty")
            image.offset(rva, size)
            sections = [s for s in image.sections if s.rva <= rva and
                        rva + size <= s.rva + s.raw_size and
                        rva + size <= s.rva + s.virtual_size]
            if len(sections) != 1:
                raise ValueError("Anchor must lie in one raw-backed virtual section")
            section = sections[0]
            if section.name != anchor["section"] or section.flags != number(anchor["section_flags"]):
                raise ValueError("Section name/flags mismatch")
            readable, writable = bool(section.flags & 0x40000000), bool(section.flags & 0x80000000)
            kind = anchor["kind"]
            if kind == "code":
                valid = section.executable and readable and not writable
            elif kind == "vtable":
                valid = readable and not section.executable and not writable and size % 8 == 0
            elif kind == "data":
                valid = readable and not section.executable
            else:
                raise ValueError("Unsupported anchor kind")
            if not valid:
                raise ValueError("Section attributes conflict with anchor kind")
            valid_anchors[key] = (rva, size, kind)
        except (InvalidImage, ValueError, KeyError, TypeError) as exc:
            fail("anchor:" + key, str(exc))

    evidence = value.get("evidence", {})
    valid_evidence = set()
    evidence_rvas = {}
    sources = {}
    if not isinstance(evidence, dict) or not evidence:
        fail("evidence", "Nonempty function evidence mapping required")
        evidence = {}
    try:
        index = mapping(json.loads(document(docs, value["function_index"]).read_text(encoding="utf-8")), "function index")
        mapping(index["functions"], "indexed functions")
        if index["schema_version"] != 1 or index["input_sha256"] != sha:
            raise ValueError("Function index build/schema mismatch")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        fail("function_index", str(exc))
        index = {"functions": {}}
    for key, pin in evidence.items():
        scope = "evidence:" + key
        try:
            pin = mapping(pin, "evidence pin")
            rva = number(pin["rva"])
            entry = mapping(index["functions"][hex(rva)], "indexed function")
            code_sha = fingerprint(pin["code_sha256"])
            source_sha = fingerprint(pin["source_sha256"])
            note_sha = fingerprint(pin["note_sha256"])
            if pin["input_sha256"] != sha or number(entry["ea"]) != base + rva:
                raise ValueError("Function evidence build/RVA/EA mismatch")
            if entry["code_sha256"] != code_sha or entry["source_sha256"] != source_sha:
                raise ValueError("Function index code/source hash mismatch")
            if entry["note"] != pin["note"] or entry["capture_state"] != pin["capture_state"]:
                raise ValueError("Function index note/capture state mismatch")
            if entry["runtime_verified"] is not False or entry["capture_state"] not in CAPTURE_STATES:
                raise ValueError("Invalid static capture metadata")
            image.offset(rva)
            if not image.executable_va(base + rva):
                raise ValueError("Documented function is not executable")
            raw_note = document(docs, pin["note"]).read_bytes()
            if digest(raw_note) != note_sha:
                raise ValueError("Function Markdown hash mismatch")
            text = raw_note.decode("utf-8").replace("\r\n", "\n")
            if not text.startswith(f"# {hex(rva)}\n"):
                raise ValueError("Function Markdown RVA mismatch")
            metadata = (
                f"- 输入 SHA-256：`{sha}`",
                f"- 捕获状态：`{entry['capture_state']}`",
                f"- 来源 SHA-256：`{source_sha}`",
            )
            if not all(line in text.splitlines() for line in metadata):
                raise ValueError("Function Markdown build/source metadata mismatch")
            code = code_block(text)
            if digest(code.encode("utf-8")) != code_sha:
                raise ValueError("Function Markdown code hash mismatch")
            source_path = Path(entry["local_source"])
            source_key = (str(source_path), source_sha)
            if source_key not in sources:
                provenance = {"source": str(source_path), "source_sha256": source_sha,
                              "availability": "missing", "hash_verified": False,
                              "build_identity": "not_checked"}
                report["source_provenance"].append(provenance)
                sources[source_key] = (None, provenance)
                raw_source = source_path.read_bytes()
                provenance["availability"] = "available"
                if digest(raw_source) != source_sha:
                    raise ValueError("Raw source JSON hash mismatch")
                provenance["hash_verified"] = True
                source = mapping(json.loads(raw_source.decode("utf-8-sig")), "raw source")
                capture = mapping(source.get("capture", {}), "capture")
                if capture:
                    input_sha = capture.get("input_sha256")
                    database_identity = mapping(capture.get("database_identity", {}), "database identity")
                    database_sha = database_identity.get("sha256")
                    database_base = database_identity.get("image_base")
                    if input_sha is not None and input_sha != sha:
                        raise ValueError("Raw source capture build mismatch")
                    if database_sha is not None and database_sha != sha:
                        raise ValueError("Raw source database build mismatch")
                    if database_base is not None and number(database_base) != base:
                        raise ValueError("Raw source database image base mismatch")
                    complete = input_sha == sha and database_sha == sha and database_base is not None
                    provenance["build_identity"] = "verified" if complete else "partial"
                else:
                    provenance["build_identity"] = "not_recorded"
                sources[source_key] = (source, provenance)
            source, provenance = sources[source_key]
            if source is None:
                raise ValueError("Raw source JSON unavailable or invalid")
            records = source.get("result", [])
            if isinstance(records, dict):
                records = [records]
            matches = [record for record in records if isinstance(record, dict) and
                       record.get("rva") == hex(rva) and "pseudocode" in record]
            if len(matches) != 1 or number(matches[0]["ea"]) != base + rva:
                raise ValueError("Raw source function RVA/EA missing or ambiguous")
            if matches[0]["pseudocode"] != code:
                raise ValueError("Raw source pseudocode differs from Markdown")
            valid_evidence.add(key)
            evidence_rvas[key] = rva
        except (OSError, ValueError, KeyError, TypeError) as exc:
            fail(scope, str(exc))

    tables = value.get("vtables", {})
    if not isinstance(tables, dict):
        fail("vtables", "Expected vtable mapping")
        tables = {}
    valid_slots = set()
    for key, table in tables.items():
        try:
            table = mapping(table, "vtable")
            mapping(table["slots"], "vtable slots")
            rva, size, kind = valid_anchors[table["anchor"]]
            if kind != "vtable" or not table["slots"]:
                raise ValueError("Vtable requires a vtable anchor and specified slots")
            for slot, target in table["slots"].items():
                target = mapping(target, "vtable slot target")
                slot_number = number(slot)
                if (slot_number + 1) * 8 > size:
                    raise ValueError("Vtable slot exceeds anchor span")
                expected = number(target["target_rva"])
                evidence_key = target["evidence"]
                if evidence_key not in valid_evidence or evidence_rvas[evidence_key] != expected:
                    raise ValueError("Vtable target lacks matching valid function evidence")
                actual = image.unpack("<Q", image.offset(rva + slot_number * 8, 8))[0]
                if actual != base + expected:
                    raise ValueError(f"Vtable slot {slot_number} pointer mismatch")
                valid_slots.add((key, slot_number, evidence_key))
        except (InvalidImage, ValueError, KeyError, TypeError) as exc:
            fail("vtable:" + key, str(exc))

    capabilities = value.get("capabilities", {})
    if not isinstance(capabilities, dict) or not capabilities:
        fail("capabilities", "Nonempty capability mapping required")
        capabilities = {}
    for key, capability in capabilities.items():
        blockers = []
        kind = capability.get("kind") if isinstance(capability, dict) else None
        if not isinstance(kind, str) or kind not in REQUIRED_PROOFS:
            blockers.append({"requirement": "kind", "state": "unsupported", "detail": str(kind)})
            fail("capability:" + key, "Unsupported capability kind")
        else:
            proofs = capability.get("proofs", {})
            if not isinstance(proofs, dict):
                proofs = {}
            for requirement in REQUIRED_PROOFS[kind]:
                proof = proofs.get(requirement, {})
                state = proof.get("state", "missing") if isinstance(proof, dict) else "missing"
                references = proof.get("evidence", []) if isinstance(proof, dict) else []
                if state != "static_confirmed":
                    blockers.append({"requirement": requirement, "state": state,
                                     "detail": proof.get("detail", "No confirmed proof")
                                     if isinstance(proof, dict) else "Invalid proof declaration"})
                elif not isinstance(references, list) or not references or any(
                        not isinstance(ref, str) or ref not in valid_evidence for ref in references):
                    blockers.append({"requirement": requirement, "state": "invalid_evidence",
                                     "detail": "Confirmed proof requires valid pinned function evidence"})
        dependencies = capability.get("structure_dependencies", {}) if isinstance(capability, dict) else {}
        dependency_valid = False
        try:
            dependencies = mapping(dependencies, "structure dependencies")
            required_anchors = dependencies["anchors"]
            required_slots = dependencies["vtable_slots"]
            if not isinstance(required_anchors, list) or not required_anchors or any(
                    not isinstance(ref, str) or ref not in valid_anchors for ref in required_anchors):
                raise ValueError("Capability requires valid anchor dependencies")
            if not isinstance(required_slots, list) or (kind == "command" and not required_slots):
                raise ValueError("Command capability requires explicit vtable slot dependencies")
            for dependency in required_slots:
                dependency = mapping(dependency, "vtable slot dependency")
                if (dependency["table"], number(dependency["slot"]), dependency["evidence"]) not in valid_slots:
                    raise ValueError("Capability depends on an unchecked or missing vtable slot")
            dependency_valid = True
        except (ValueError, KeyError, TypeError) as exc:
            blockers.append({"requirement": "structure_dependencies", "state": "missing_or_invalid",
                             "detail": str(exc)})
        report["capabilities"][key] = {
            "kind": kind, "declared_static_proofs_complete": not blockers,
            "declared_structure_dependencies_valid": dependency_valid,
            "semantic_proofs_verified": False,
            "proof_blockers": blockers,
            "runtime_status": "unavailable",
            "runtime_blockers": ["native_runtime_not_implemented"],
        }
    report["structural_valid"] = not errors
    for capability_report in report["capabilities"].values():
        capability_report["structural_checks_passed"] = report["structural_valid"] and capability_report["declared_structure_dependencies_valid"]
    report["source_provenance_complete"] = bool(evidence) and len(valid_evidence) == len(evidence) and all(
        item["hash_verified"] and item["build_identity"] == "verified"
        for item in report["source_provenance"])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--docs", required=True, type=Path)
    args = parser.parse_args()
    report = verify_profile(args.binary, args.profile, args.docs)
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    raise SystemExit(0 if report["structural_valid"] else 1)


if __name__ == "__main__":
    main()
