"""CPU packaging fixtures only. No native execution, MSVC, PID or game access."""
import base64
import contextlib
import csv
import hashlib
import importlib
import importlib.util
import io
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import release_builder as release
import release_backend as backend


def fixture_dll():
    # Deliberately NOT a functioning DLL; a CPU-only PE-container header fixture.
    raw = bytearray(256)
    raw[:2] = b"MZ"
    struct.pack_into("<I", raw, 0x3C, 0x80)
    raw[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", raw, 0x84, 0x8664)
    struct.pack_into("<H", raw, 0x96, 0x2000)
    struct.pack_into("<H", raw, 0x98, 0x20B)
    return bytes(raw)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        # Fixture input location is independent of the production build layout.
        native_input = "fixture-inputs/native/pdx_runtime.dll"
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        # A fixture version is test input, not the product's next release number.
        project, versions = re.subn(r'(?m)^version\s*=.*$', 'version = "0.1.0"',
            project, count=1)
        if versions != 1:
            raise ValueError("packaging fixture requires an explicit project version")
        project, replacements = re.subn(r"(?m)^[ \t]*native[ \t]*=.*$",
            f'native = "{native_input}"', project, count=1)
        if replacements != 1:
            raise ValueError("packaging fixture requires the native input configuration key")
        (self.root / "pyproject.toml").write_text(project, encoding="utf-8")
        self.license_bytes = (ROOT / "LICENSE").read_bytes()
        (self.root / "LICENSE").write_bytes(self.license_bytes)
        self.sdk = self.root / "src/pdx_runtime"
        fixture_modules = {"__init__.py": '"""CPU package fixture."""\n',
            "shell.py": "def main(): return 0\n",
            "session.py": "class RuntimeSession: pass\n", "native_bridge.py": "class Bridge: pass\n",
            "v3_game/__init__.py": "class V3Game: pass\n",
            "v3_game/economy/__init__.py": '"""Current domain fixture."""\n',
            "v3_game/research/__init__.py": "class ResearchAPI: pass\n"}
        for relative, text in fixture_modules.items():
            path = self.sdk / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (self.sdk / "py.typed").write_bytes(b"")
        for skill_name in ("pdx", "victoria3"):
            folder = self.root / "skills" / skill_name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text("# CPU resource fixture only\n")
            (folder / "references").mkdir()
            (folder / "references/fixture.md").write_text("CPU resource fixture, not final skill/API prose.\n")
        self.native = self.root / native_input
        self.native.parent.mkdir(parents=True)
        self.native.write_bytes(fixture_dll())

    def payload(self):
        return release.collect(self.root)

    def artifacts(self):
        result = release.build(self.root, self.root / "out")
        return result, self.root / "out" / result["wheel"], self.root / "out" / result["portable"]

    def test_exact_one_native_dll_and_only_selected_payload(self):
        (self.sdk / "pdx_v3_data.dll").write_bytes(b"must not ship")
        (self.sdk / "x.pdb").write_bytes(b"must not ship")
        (self.sdk / "reviews").mkdir()
        (self.sdk / "reviews/secret.py").write_text("secret = 1")
        _, _, _, files = self.payload()
        self.assertEqual([n for n in files if n.endswith(".dll")], [release.DLL_PATH])
        self.assertFalse(any("reviews" in n or n.endswith(".pdb") for n in files))
        self.assertFalse(any(n.startswith("tests/") or n.startswith("native/") for n in files))
        self.assertFalse(any("research_queue_current_18400.bin" in n for n in files))

    def test_current_source_packages_are_selected_without_extra_namespaces(self):
        _, _, _, files = self.payload()
        self.assertIn("pdx_runtime/v3_game/economy/__init__.py", files)
        self.assertEqual(files["pdx_runtime/v3_game/research/__init__.py"],
                         (self.sdk / "v3_game/research/__init__.py").read_bytes())
        self.assertFalse((self.root / "examples").exists())
        self.assertFalse(any(part in {"examples", "_services", "_support"}
            for name in files for part in Path(name).parts))

    def test_backend_accepts_only_current_explicit_inputs(self):
        self.assertEqual(backend._inputs({"sdk-source": ["src"], "native-dll": "native.dll",
            "skills-source": "skills"}), {"sdk_source": "src", "native_dll": "native.dll", "skills_source": "skills"})
        with self.assertRaisesRegex(ValueError, "unsupported build settings"):
            backend._inputs({"removed-input": "old"})
        with self.assertRaisesRegex(ValueError, "supply one native-dll"):
            backend._inputs({"native-dll": ["one", "two"]})

    def test_mpl_full_bytes_and_typing_marker_are_package_resources_in_both_payloads(self):
        marker=b"partial\n"  # Fixture marker bytes, not a change to the real SDK.
        (self.sdk/"py.typed").write_bytes(marker)
        _,wheel,portable=self.artifacts()
        with zipfile.ZipFile(wheel) as w,zipfile.ZipFile(portable) as p:
            for archive in (w,p):
                self.assertEqual(archive.read("pdx_runtime/LICENSE"),self.license_bytes)
                self.assertEqual(archive.read("pdx_runtime/py.typed"),marker)
                self.assertNotIn("LICENSE",archive.namelist())
                self.assertNotIn("NOTICE",archive.namelist())
            self.assertEqual(w.read("pdx_runtime-0.1.0.dist-info/licenses/LICENSE"),self.license_bytes)
            self.assertNotIn("pdx_runtime-0.1.0.dist-info/licenses/LICENSE",p.namelist())
            text=w.read("pdx_runtime-0.1.0.dist-info/METADATA").decode()
            self.assertIn("Metadata-Version: 2.4\n",text)
            self.assertIn("License-Expression: MPL-2.0\n",text)
            self.assertIn("License-File: LICENSE\n",text)
            self.assertNotIn("License: ",text)

    def test_optional_notice_stays_inside_package_not_site_packages_root(self):
        notice=b"CPU fixture notice only.\n"
        (self.root/"NOTICE").write_bytes(notice)
        _,wheel,portable=self.artifacts()
        with zipfile.ZipFile(wheel) as w,zipfile.ZipFile(portable) as p:
            for archive in (w,p):
                self.assertEqual(archive.read("pdx_runtime/NOTICE"),notice)
                self.assertNotIn("NOTICE",archive.namelist())

    def test_declared_license_and_typing_marker_are_required(self):
        (self.root/"LICENSE").unlink()
        with self.assertRaisesRegex(FileNotFoundError,"missing declared license"):
            self.payload()
        (self.root/"LICENSE").write_bytes(self.license_bytes)
        (self.sdk/"py.typed").unlink()
        with self.assertRaisesRegex(FileNotFoundError,"py.typed"):
            self.payload()

    def test_unknown_legal_configuration_is_rejected_without_guessing(self):
        original=(self.root/"pyproject.toml").read_text()
        changes=(('license = "MPL-2.0"','license = "MIT"',"SPDX expression MPL-2.0"),
            ('license-files = ["LICENSE"]','license-files = ["COPYING"]',"only license-files"))
        for old,new,reason in changes:
            with self.subTest(new=new):
                self.assertIn(old,original)
                (self.root/"pyproject.toml").write_text(original.replace(old,new,1))
                with self.assertRaisesRegex(ValueError,reason):
                    self.payload()
        (self.root/"pyproject.toml").write_text(original)

    def test_payload_rejects_top_level_legal_or_unselected_marker_paths(self):
        _,_,_,files=self.payload()
        for path in ("LICENSE","NOTICE","py.typed","pdx_runtime/other.typed"):
            with self.subTest(path=path),self.assertRaisesRegex(ValueError,"unselected release member"):
                release.validate_payload({**files,path:b"fixture"})

    def test_prepared_metadata_keeps_legal_bytes_and_matches_both_build_paths(self):
        project,name,version,payload=self.payload()
        with contextlib.chdir(self.root):
            info=backend.prepare_metadata_for_build_wheel(str(self.root/"prepared"))
            direct_info,expected=release.metadata(project,name,version,payload)
            self.assertEqual(info,direct_info)
            for path,raw in expected.items():
                self.assertEqual((self.root/"prepared"/path).read_bytes(),raw)
            self.assertEqual((self.root/"prepared"/info/"licenses/LICENSE").read_bytes(),self.license_bytes)
            filename=backend.build_wheel(str(self.root/"prepared-wheel"),metadata_directory=str(self.root/"prepared"/info))
            parent_filename=backend.build_wheel(str(self.root/"prepared-parent-wheel"),metadata_directory=str(self.root/"prepared"))
        expected_wheel=release.wheel_bytes(project,name,version,payload)
        self.assertEqual((self.root/"prepared-wheel"/filename).read_bytes(),expected_wheel)
        self.assertEqual((self.root/"prepared-parent-wheel"/parent_filename).read_bytes(),expected_wheel)
        with zipfile.ZipFile(io.BytesIO(expected_wheel)) as archive:
            for path,raw in expected.items():
                self.assertEqual(archive.read(path),raw)
            rows=list(csv.reader(io.StringIO(archive.read(info+"/RECORD").decode())))
            self.assertIn(info+"/licenses/LICENSE",{row[0] for row in rows})
            self.assertIn("pdx_runtime/py.typed",{row[0] for row in rows})

    def test_prepared_license_mismatch_is_not_silently_discarded(self):
        with contextlib.chdir(self.root):
            info=backend.prepare_metadata_for_build_wheel(str(self.root/"prepared"))
            (self.root/"prepared"/info/"licenses/LICENSE").write_bytes(b"different CPU fixture bytes")
            with self.assertRaisesRegex(ValueError,"prepared metadata differs.*licenses/LICENSE"):
                backend.build_wheel(str(self.root/"out"),metadata_directory=str(self.root/"prepared"/info))

    def test_platform_and_non_pure_wheel_metadata(self):
        result, wheel, _ = self.artifacts()
        self.assertTrue(wheel.name.endswith("-py3-none-win_amd64.whl"))
        with zipfile.ZipFile(wheel) as z:
            text = z.read("pdx_runtime-0.1.0.dist-info/WHEEL").decode()
            self.assertIn("Root-Is-Purelib: false", text)
            self.assertIn("Tag: py3-none-win_amd64", text)
        self.assertFalse(result["native_functionality_tested_by_builder"])

    def test_record_hashes_and_sizes_match_every_member(self):
        _, wheel, _ = self.artifacts()
        with zipfile.ZipFile(wheel) as z:
            rows = list(csv.reader(io.StringIO(z.read("pdx_runtime-0.1.0.dist-info/RECORD").decode())))
            self.assertEqual({r[0] for r in rows}, set(z.namelist()))
            for name, digest, size in rows:
                if name.endswith("/RECORD"):
                    self.assertEqual((digest, size), ("", ""))
                else:
                    raw = z.read(name)
                    expected = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
                    self.assertEqual(digest, "sha256=" + expected)
                    self.assertEqual(int(size), len(raw))

    def test_portable_payload_matches_wheel_product(self):
        _, wheel, portable = self.artifacts()
        with zipfile.ZipFile(wheel) as w, zipfile.ZipFile(portable) as p:
            names = [n for n in w.namelist() if ".dist-info/" not in n]
            self.assertEqual(set(names), set(p.namelist()))
            for name in names:
                self.assertEqual(w.read(name), p.read(name))
            self.assertIn("pdx_runtime/shell.py", p.namelist())
            self.assertEqual([n for n in p.namelist() if n.endswith(".dll")], [release.DLL_PATH])

    def test_reproducible_bytes_ignore_build_location(self):
        first = release.build(self.root, self.root / "out1")
        second = release.build(self.root, self.root / "elsewhere/out2")
        self.assertEqual(first["sha256"], second["sha256"])

    def test_runtime_entry_point_uses_session_shell(self):
        _, wheel, _ = self.artifacts()
        with zipfile.ZipFile(wheel) as z:
            text = z.read("pdx_runtime-0.1.0.dist-info/entry_points.txt").decode()
            self.assertIn("pdx-runtime = pdx_runtime.shell:main", text)
            self.assertNotIn("attach", text)

    def test_missing_native_fails_instead_of_pure_any_release(self):
        self.native.unlink()
        with self.assertRaises(FileNotFoundError):
            self.payload()

    def test_x86_or_non_dll_pe_rejected(self):
        raw = bytearray(fixture_dll())
        struct.pack_into("<H", raw, 0x84, 0x14C)
        with self.assertRaises(ValueError):
            release.check_native(raw)
        struct.pack_into("<H", raw, 0x84, 0x8664)
        struct.pack_into("<H", raw, 0x96, 0)
        with self.assertRaises(ValueError):
            release.check_native(raw)

    def test_private_paths_rejected_in_selected_sdk(self):
        (self.sdk / "bad.py").write_text('path = "C:/Users/private/save"\n')
        with self.assertRaisesRegex(ValueError, "private/game"):
            self.payload()

    def test_sdk_parent_directory_supported(self):
        a = release.collect(self.root, sdk_source=self.sdk)
        b = release.collect(self.root, sdk_source=self.sdk.parent)
        self.assertEqual(a, b)



    def test_pep517_backend_matches_direct_wheel(self):
        project, name, version, files = self.payload()
        with contextlib.chdir(self.root):
            filename = backend.build_wheel(str(self.root / "pep517"))
            info = backend.prepare_metadata_for_build_wheel(str(self.root / "metadata"))
        self.assertEqual((self.root / "pep517" / filename).read_bytes(), release.wheel_bytes(project, name, version, files))
        self.assertEqual(info, "pdx_runtime-0.1.0.dist-info")
        self.assertEqual(backend.get_requires_for_build_wheel(), [])

    def test_skill_is_in_both_source_and_release_resources(self):
        _, _, _, files = self.payload()
        resources = [name for name in files if name.startswith("pdx_runtime/skills/")]
        self.assertIn("pdx_runtime/skills/pdx/SKILL.md", resources)
        self.assertIn("pdx_runtime/skills/victoria3/SKILL.md", resources)
        self.assertIn("pdx_runtime/skills/victoria3/references/fixture.md", resources)
        self.assertFalse(any("/skills/pdx-runtime/" in name or "/skills/v3-runtime/" in name for name in resources))
        for name in resources:
            self.assertTrue(name.endswith(".md"))


    def test_both_reviewed_skill_resources_are_required(self):
        shutil.rmtree(self.root / "skills/victoria3")
        with self.assertRaisesRegex(FileNotFoundError, "both reviewed"):
            self.payload()











if __name__ == "__main__":
    unittest.main(verbosity=2)
