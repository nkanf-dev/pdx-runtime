"""Pure CPU tests for game-level exact native quantities."""
import unittest
from decimal import Decimal, Inexact, Rounded, localcontext
from pdx_runtime.v3_data import FixedPoint
from pdx_runtime.v3_game._values import fixed_decimal


class FixedQuantityTests(unittest.TestCase):
    def test_native_units_and_signed_raw(self):
        for raw, expected in [(0,"0"),(1,"0.00001"),(100000,"1"),
                (-100001,"-1.00001"),(10074756742,"100747.56742"),
                (-(1<<63),"-92233720368547.75808"),((1<<63)-1,"92233720368547.75807")]:
            with self.subTest(raw=raw):
                self.assertEqual(fixed_decimal(raw), Decimal(expected))
                self.assertEqual(fixed_decimal(FixedPoint(raw & ((1<<64)-1))), Decimal(expected))

    def test_ambient_precision_and_traps_cannot_round_native_quantity(self):
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            self.assertEqual(fixed_decimal(478986750617), Decimal("4789867.50617"))
            self.assertEqual(fixed_decimal(-478986750617), Decimal("-4789867.50617"))

    def test_bool_float_string_do_not_masquerade_as_native_raw(self):
        for value in (True, 1.5, "100000", None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                fixed_decimal(value)

if __name__ == "__main__":
    unittest.main()
