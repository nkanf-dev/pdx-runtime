"""Build one Windows-x64 runtime wheel and matching portable product ZIP.

This packages an already-built DLL. It never compiles, injects, starts a game,
looks up a PID, reads a game binary, or copies research/artifact directories.
The explicit native input is required. CPU fixture tests are not native proof.
"""
from __future__ import annotations
import argparse
import ast
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import tomllib
import zipfile

DLL_PATH = "pdx_runtime/_native/pdx_runtime.dll"
TAG = "py3-none-win_amd64"
FORBIDDEN = {"reviews", "work", "functions", "index", "notes", "__pycache__", ".git",
             "examples", "_services", "_support"}
PRIVATE_PATH = re.compile(r"(?:[A-Z]:[/\\](?:Users|Games|Program Files)|/Users/|/home/)", re.I)


def check_native(raw):
    """Check PE container architecture, not ABI/native functionality."""
    if len(raw) < 64 or raw[:2] != b"MZ":
        raise ValueError("native DLL must be an already-built Windows PE file")
    offset = struct.unpack_from("<I", raw, 0x3C)[0]
    if offset < 64 or offset + 26 > len(raw) or raw[offset:offset+4] != b"PE\0\0":
        raise ValueError("invalid PE header")
    machine, = struct.unpack_from("<H", raw, offset+4)
    flags, = struct.unpack_from("<H", raw, offset+22)
    magic, = struct.unpack_from("<H", raw, offset+24)
    if machine != 0x8664 or magic != 0x20B or not flags & 0x2000:
        raise ValueError("native DLL must be an AMD64 PE32+ DLL")


def safe_text(path):
    if path.is_symlink():
        raise ValueError("symlink input is not accepted: " + path.name)
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    if PRIVATE_PATH.search(text):
        raise ValueError("machine-specific private/game installation path in " + path.name)
    ast.parse(text, filename=path.name)
    return raw


def license_files(project):
    """Validate the one reviewed legal configuration; never guess a license."""
    if project.get("license") != "MPL-2.0":
        raise ValueError("release license must be the reviewed SPDX expression MPL-2.0")
    if project.get("license-files") != ["LICENSE"]:
        raise ValueError("release supports only license-files = [\"LICENSE\"]")
    return ("LICENSE",)


def collect(repo, *, sdk_source=None, native_dll=None, skills_source=None):
    repo = Path(repo).resolve()
    meta = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    project, config = meta["project"], meta["tool"]["pdx-release"]
    declared_licenses = license_files(project)
    if config["platform"] != "win_amd64" or project.get("dependencies"):
        raise ValueError("this stdlib builder supports the current dependency-free win_amd64 product only")
    name = project["name"].replace("-", "_")
    version = project["version"]
    if not re.fullmatch(r"[A-Za-z0-9_]+", name) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?", version):
        raise ValueError("unsupported project name/version")
    sdk = Path(sdk_source) if sdk_source is not None else repo / config["sdk"]
    if not (sdk / "__init__.py").is_file() and (sdk / "pdx_runtime/__init__.py").is_file():
        sdk = sdk / "pdx_runtime"
    dll = Path(native_dll) if native_dll is not None else repo / config["native"]
    if not dll.is_file():
        raise FileNotFoundError(
            f"already-built native DLL not found: {dll}. From the repository root run:\n"
            'cmake -S . -B build/native -G "Visual Studio 17 2022" -A x64\n'
            "cmake --build build/native --config Release\n"
            "Or select an existing build with --native-dll PATH "
            "(PEP 517: --config-settings native-dll=PATH).")
    if not (sdk / "__init__.py").is_file():
        raise FileNotFoundError("supply the current SDK and already-built ONE pdx_runtime.dll")
    if dll.is_symlink():
        raise ValueError("native DLL symlink is not accepted")
    native = dll.read_bytes()
    check_native(native)
    files = {DLL_PATH: native}
    for source in sorted(sdk.rglob("*.py")):
        relative = source.relative_to(sdk)
        if any(part in FORBIDDEN or part in {"tests"} for part in relative.parts):
            continue
        files["pdx_runtime/" + relative.as_posix()] = safe_text(source)
    typed_marker = sdk / "py.typed"
    if not typed_marker.is_file():
        raise FileNotFoundError("the typed SDK must include py.typed")
    if typed_marker.is_symlink():
        raise ValueError("py.typed symlink is not accepted")
    files["pdx_runtime/py.typed"] = typed_marker.read_bytes()
    skills = Path(skills_source) if skills_source is not None else repo / "skills"
    for skill_name in ("pdx", "victoria3"):
        folder = skills / skill_name
        if not (folder / "SKILL.md").is_file():
            raise FileNotFoundError("include both reviewed skills/pdx and skills/victoria3 source resources")
        for source in sorted(folder.rglob("*.md")):
            text = source.read_text(encoding="utf-8")
            if source.is_symlink() or source.stat().st_size > 131072 or PRIVATE_PATH.search(text):
                raise ValueError("unsafe or oversized skill resource " + source.name)
            files["pdx_runtime/skills/" + skill_name + "/" + source.relative_to(folder).as_posix()] = text.encode("utf-8")
    for required in ("pdx_runtime/shell.py", "pdx_runtime/session.py", "pdx_runtime/native_bridge.py"):
        if required not in files:
            raise ValueError("missing existing ordinary-Python runtime entry: " + required)
    for filename in ("LICENSE", "NOTICE"):
        source = repo / filename
        if not source.is_file():
            if filename in declared_licenses:
                raise FileNotFoundError("missing declared license file: " + filename)
            continue
        if source.is_symlink():
            raise ValueError("legal file symlink is not accepted: " + filename)
        raw = source.read_bytes()
        if not raw and filename in declared_licenses:
            raise ValueError("declared license file is empty: " + filename)
        if PRIVATE_PATH.search(raw.decode("utf-8")):
            raise ValueError("private path in public legal file")
        files["pdx_runtime/" + filename] = raw
    validate_payload(files)
    return project, name, version, files


def validate_payload(files):
    dlls = sorted(name for name in files if name.lower().endswith(".dll"))
    if dlls != [DLL_PATH]:
        raise ValueError("release must contain exactly pdx_runtime/_native/pdx_runtime.dll")
    for name in files:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or any(part in FORBIDDEN for part in path.parts):
            raise ValueError("unsafe release member " + name)
        skill_resource = any(name.startswith("pdx_runtime/skills/" + skill + "/") for skill in ("pdx", "victoria3")) and name.endswith(".md")
        resources = {"pdx_runtime/py.typed", "pdx_runtime/LICENSE", "pdx_runtime/NOTICE"}
        if not (name.endswith(".py") or name == DLL_PATH or name in resources or skill_resource):
            raise ValueError("unselected release member " + name)


def metadata(project, name, version, payload):
    """Core Metadata 2.4 and legal bytes from the same selected payload."""
    declared_licenses = license_files(project)
    info = name + "-" + version + ".dist-info"
    text = ("Metadata-Version: 2.4\nName: " + project["name"] + "\nVersion: " + version
        + "\nSummary: " + project["description"] + "\nRequires-Python: " + project["requires-python"]
        + "\nLicense-Expression: " + project["license"] + "\n"
        + "".join("License-File: " + filename + "\n" for filename in declared_licenses) + "\n")
    scripts = "[console_scripts]\n" + "".join(key + " = " + value + "\n"
        for key, value in sorted(project.get("scripts", {}).items()))
    files = {info + "/METADATA": text.encode(), info + "/WHEEL":
        ("Wheel-Version: 1.0\nGenerator: pdx-runtime-release-builder\nRoot-Is-Purelib: false\nTag: " + TAG + "\n").encode(),
        info + "/entry_points.txt": scripts.encode()}
    for filename in declared_licenses:
        path = "pdx_runtime/" + filename
        if path not in payload or not payload[path]:
            raise ValueError("selected payload is missing declared license bytes: " + filename)
        files[info + "/licenses/" + filename] = payload[path]
    return info, files


def zip_bytes(files):
    """Fixed timestamps, sorted paths, stable permissions; never include host paths."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, raw in sorted(files.items()):
            item = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.create_system = 3
            item.external_attr = 0o100644 << 16
            archive.writestr(item, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def wheel_bytes(project, name, version, payload):
    info, extra = metadata(project, name, version, payload)
    files = dict(payload, **extra)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for path, raw in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        writer.writerow((path, "sha256=" + digest, len(raw)))
    writer.writerow((info + "/RECORD", "", ""))
    files[info + "/RECORD"] = output.getvalue().encode()
    return zip_bytes(files)


def build(repo, output, **inputs):
    project, name, version, files = collect(repo, **inputs)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    wheel = output / (name + "-" + version + "-" + TAG + ".whl")
    portable = output / (name + "-" + version + "-win_amd64-portable.zip")
    wheel.write_bytes(wheel_bytes(project, name, version, files))
    # Portable adds no duplicate DLL, adapter bundle, metadata tree or runner.
    # Product root is directly importable with python -m pdx_runtime.shell.
    portable.write_bytes(zip_bytes(files))
    return {"wheel": wheel.name, "portable": portable.name, "platform": TAG,
        "dll_members": [DLL_PATH], "payload_files": len(files),
        "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (wheel, portable)},
        "native_functionality_tested_by_builder": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sdk-source", type=Path)
    parser.add_argument("--native-dll", type=Path)
    parser.add_argument("--skills-source", type=Path)
    args = parser.parse_args(argv)
    result = build(args.repo, args.out, sdk_source=args.sdk_source, native_dll=args.native_dll,
        skills_source=args.skills_source)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
