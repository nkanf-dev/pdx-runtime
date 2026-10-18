"""Exact game-level quantities for Victoria 3's five-digit fixed-point values.

The generic Data layer deliberately exposes raw bits only. This game-specific
layer applies the same scale as the existing V3 native script/economy adapters.
Conversion does not depend on the caller's decimal context.
"""
from decimal import Decimal

from ..v3_data import FixedPoint
from ..v3_script import FIXED_SCALE


def fixed_decimal(value: FixedPoint | int) -> Decimal:
    """Convert a Data FixedPoint or a signed raw integer, without float rounding."""
    raw = value.signed if isinstance(value, FixedPoint) else value
    if type(raw) is not int:
        raise TypeError("expected a native FixedPoint or signed raw integer")
    places = len(str(FIXED_SCALE)) - 1
    if FIXED_SCALE != 10 ** places:
        raise ValueError("V3 fixed-point scale is not a decimal power")
    digits = tuple(int(char) for char in str(abs(raw)))
    return Decimal((int(raw < 0), digits, -places))
