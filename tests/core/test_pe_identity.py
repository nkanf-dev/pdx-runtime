"""CPU-only canonical PE identity helpers and builder dependency boundary."""
from copy import deepcopy
import unittest

from pdx_runtime import pe
from tests.core import test_build_profile as build_fixtures


class PEIdentityTests(unittest.TestCase):

    def test_number_preserves_full_width_zero_and_hex_behavior(self):
        for value, expected in ((0, 0), ('0', 0), ('0x0', 0),
                                (0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF),
                                ('0xffffffffffffffff', 0xFFFFFFFFFFFFFFFF),
                                ('42', 42), (2 ** 128, 2 ** 128)):
            with self.subTest(value=value):
                self.assertEqual(pe.number(value), expected)
        for value in (False, True, None, 1.0, [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Expected an integer or hex string'):
                pe.number(value)
        for value in (-1, '-1', '-0x1'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Expected a nonnegative integer'):
                pe.number(value)

    def test_fingerprint_requires_exact_lowercase_sha_and_returns_original(self):
        value = '0123456789abcdef' * 4
        self.assertIs(pe.fingerprint(value), value)
        for invalid in (None, False, b'0' * 64, '', '0' * 63, '0' * 65,
                        'A' * 64, 'g' * 64, ' ' + value, value + '\n'):
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, 'Expected a full lowercase SHA-256'):
                pe.fingerprint(invalid)


    def test_builder_game_recipe_coverage_is_not_widened_by_helper_extraction(self):
        # Reuse only the inert fixture's setup/verification interface, not its suite.
        fixture = build_fixtures.BuildProfileTests(methodName='test_build_identity_rejects_wrong_binary_machine_and_base')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for game in ('victoria3', 'ck3'):
            value = deepcopy(fixture.value)
            value['build']['game'] = game
            report = fixture.verify(value)
            self.assertTrue(report['structural_valid'], report['structural_errors'])
        value = deepcopy(fixture.value)
        value['build']['game'] = 'unimplemented-game-recipe'
        report = fixture.verify(value)
        self.assertFalse(report['structural_valid'])
        self.assertEqual(report['structural_errors'], [{'scope': 'profile_or_image', 'reason': 'Unsupported game'}])
        self.assertEqual(report['available_runtime_capabilities'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
