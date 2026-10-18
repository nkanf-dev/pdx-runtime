"""Exact file identity, ASLR and process-bound fingerprint reuse; CPU only."""
from dataclasses import replace
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from pdx_runtime import target_descriptor as td

BASE = 0x140000000


def inert_pe():
    data = bytearray(0x800)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3C, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HHI', data, 0x84, 0x8664, 3, 12345)
    struct.pack_into('<H', data, 0x94, 240)
    struct.pack_into('<H', data, 0x98, 0x20B)
    struct.pack_into('<Q', data, 0x98 + 24, BASE)
    struct.pack_into('<I', data, 0x98 + 56, 0x5000)
    for i, (name, rva, raw, flags) in enumerate([
        (b'.text', 0x1000, 0x200, 0x60000020),
        (b'.rdata', 0x2000, 0x400, 0x40000040),
        (b'.data', 0x3000, 0x600, 0xC0000040),
    ]):
        struct.pack_into('<8sIIIIIIHHI', data, 0x188 + i * 40,
                         name, 0x200, rva, 0x200, raw, 0, 0, 0, 0, flags)
    data[0x200] = 0xC3
    return bytes(data)


def definition(data, *, game='owned-fixture', layout=None):
    return td.TargetDefinition(game=game, version='synthetic-test',
        sha256=hashlib.sha256(data).hexdigest(), preferred_image_base=BASE,
        pe_offset=0x80, timestamp=12345, image_size=0x5000, section_count=3,
        functions={'article_type_provider': td.FunctionLocation(0x1000, data[0x200:0x210])},
        globals={}, layout={'fixture_offset': 0} if layout is None else layout)


class Memory:
    pid = 24680
    def __init__(self, raw, base, path=None):
        self.base = base
        self.path = path
        self.image = bytearray(0x5000)
        self.image[:0x200] = raw[:0x200]
        for rva, offset in ((0x1000, 0x200), (0x2000, 0x400), (0x3000, 0x600)):
            self.image[rva:rva + 0x200] = raw[offset:offset + 0x200]
        self.reads = []
        self.short_at = None
    def modules(self):
        return [{'base': self.base, 'path': str(self.path)}]
    def read(self, address, size):
        self.reads.append((address, size))
        if not self.base <= address <= address + size <= self.base + len(self.image):
            raise OSError('synthetic unreadable source')
        value = bytes(self.image[address - self.base:address - self.base + size])
        return value[:-1] if address == self.short_at else value


class TargetDescriptorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.binary = Path(self.temp.name) / 'inert.exe'
        self.raw = inert_pe()
        self.binary.write_bytes(self.raw)
        self.definition = definition(self.raw)
    def verified(self, d=None):
        return td.verify_target(self.binary, definitions=(d or self.definition,))
    def test_unknown_file_has_no_default_based_on_version_or_base(self):
        with self.assertRaises(td.UnsupportedBuild):
            td.verify_target(self.binary, definitions=())
        self.binary.write_bytes(self.raw + b'new-version')
        with self.assertRaises(td.UnsupportedBuild):
            self.verified()
        with self.assertRaises(td.UnsupportedBuild):
            td.VerifiedTarget(self.definition)
    def test_aslr_only_rebases_after_exact_file_and_loaded_checks(self):
        verified = self.verified()
        for base in (0x7FF600000000, 0x180000000):
            memory = Memory(self.raw, base)
            self.assertEqual(verified.function_address('article_type_provider', read=memory.read, image_base=base), base + 0x1000)
    def test_runtime_header_or_anchor_mismatch_never_returns_address(self):
        verified = self.verified()
        for offset in (0x3C, 0x84, 0x88, 0x98 + 24, 0x98 + 56, 0x1000):
            memory = Memory(self.raw, 0x180000000)
            memory.image[offset] ^= 1
            with self.assertRaises(td.TargetMismatch):
                verified.function_address('article_type_provider', read=memory.read, image_base=memory.base)
    def test_short_runtime_read_is_not_a_match(self):
        memory = Memory(self.raw, 0x180000000)
        memory.short_at = memory.base + 0x1000
        with self.assertRaises(td.TargetMismatch):
            self.verified().validate_runtime(memory.read, memory.base)
    def test_file_metadata_and_entry_are_checked_beyond_declared_sha(self):
        for d in (replace(self.definition, timestamp=12346),
                  replace(self.definition, functions={'article_type_provider': td.FunctionLocation(0x1000, b'\x90')})):
            with self.assertRaises(td.TargetMismatch):
                self.verified(d)
    def test_process_cache_reuses_file_hash_but_rechecks_runtime_and_file_changes(self):
        process = Memory(self.raw, 0x180000000, self.binary)
        with patch.object(td, 'PEImage', wraps=td.PEImage) as image:
            first = td.verify_loaded_target(process, process.base, definitions=(self.definition,))
            second = td.verify_loaded_target(process, process.base, definitions=(self.definition,))
            self.assertIs(first, second)
            self.assertEqual(image.call_count, 1)
            process.image[0x1000] ^= 1
            with self.assertRaises(td.TargetMismatch):
                td.verify_loaded_target(process, process.base, definitions=(self.definition,))
            self.assertEqual(image.call_count, 1)
            self.binary.write_bytes(self.raw + b'changed')
            with self.assertRaises(td.UnsupportedBuild):
                td.verify_loaded_target(process, process.base, definitions=(self.definition,))
            self.assertEqual(image.call_count, 2)
    def test_verifiers_require_definitions_before_file_or_process_access(self):
        with patch.object(td, 'PEImage', side_effect=AssertionError('must not open a file')):
            with self.assertRaises(TypeError):
                td.verify_target(self.binary)
        process = unittest.mock.Mock()
        with self.assertRaises(TypeError):
            td.verify_loaded_target(process, 0x180000000)
        process.modules.assert_not_called()
        process.read.assert_not_called()

    def test_named_non_v3_target_uses_same_exact_file_and_runtime_checks(self):
        d = replace(self.definition, game='another-owned-target', version='fixture-2')
        verified = td.verify_target(self.binary, definitions=(d,))
        memory = Memory(self.raw, 0x7FF600000000)
        self.assertIs(verified.definition, d)
        self.assertEqual(verified.function_address('article_type_provider', read=memory.read,
                                                 image_base=memory.base), memory.base + 0x1000)

    def test_definition_requires_named_target_version_and_amd64_not_a_game_allowlist(self):
        for values in ({'game': ''}, {'game': None}, {'game': False}, {'game': ' '},
                       {'version': ''}, {'version': None}, {'version': ' '}, {'machine': 0x14C}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(self.definition, **values)
        self.assertEqual(replace(self.definition, game='unlisted-target').game, 'unlisted-target')


    def test_explicit_catalog_must_select_one_exact_definition(self):
        for definitions in ((), (replace(self.definition, sha256='0' * 64),),
                            (self.definition, replace(self.definition))):
            with self.subTest(definitions=definitions), self.assertRaises(td.UnsupportedBuild):
                td.verify_target(self.binary, definitions=definitions)
        verified = td.verify_target(self.binary, definitions=iter((self.definition,)))
        self.assertIs(verified.definition, self.definition)

    def test_definition_maps_are_copied_immutable_without_renaming_layout(self):
        functions = dict(self.definition.functions)
        layout = {'fixture_offset': 0}
        d = replace(self.definition, functions=functions, layout=layout)
        functions.clear()
        layout['fixture_offset'] = 999
        self.assertIn('article_type_provider', d.functions)
        self.assertEqual(d.layout['fixture_offset'], 0)
        with self.assertRaises(TypeError):
            d.layout['fixture_offset'] = 1

    def test_loaded_identity_requires_unique_current_path_before_reading_memory(self):
        process = Memory(self.raw, 0x180000000, self.binary)
        for modules in ([], [{'base': process.base, 'path': ''}],
                        [{'base': process.base, 'path': str(self.binary)}] * 2,
                        [{'base': process.base + 1, 'path': str(self.binary)}]):
            with self.subTest(modules=modules), patch.object(process, 'modules', return_value=modules):
                with self.assertRaises(td.UnsupportedBuild):
                    td.verify_loaded_target(process, process.base, definitions=(self.definition,))
        self.assertEqual(process.reads, [])

    def test_cache_is_bound_to_explicit_definition_identity(self):
        process = Memory(self.raw, 0x180000000, self.binary)
        alternative = replace(self.definition)
        with patch.object(td, 'PEImage', wraps=td.PEImage) as image:
            first = td.verify_loaded_target(process, process.base, definitions=(self.definition,))
            second = td.verify_loaded_target(process, process.base, definitions=(alternative,))
            self.assertIsNot(first, second)
            self.assertIs(second.definition, alternative)
            self.assertEqual(image.call_count, 2)



if __name__ == '__main__':
    unittest.main(verbosity=2)
