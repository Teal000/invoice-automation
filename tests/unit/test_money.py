"""Decimal handling: parsing, quantisation and tolerance."""

from __future__ import annotations

import decimal
from decimal import Decimal

import pytest

from invoice_automation.money import (
    MoneyParseError,
    parse_decimal,
    quantize_money,
    within_tolerance,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1200.500", Decimal("1200.500")),
        ("1200,500", Decimal("1200.500")),  # comma as decimal separator
        ("1 200,500", Decimal("1200.500")),  # space as group separator
        ("1 200,500", Decimal("1200.500")),  # non-breaking space
        ("1,234.500", Decimal("1234.500")),  # comma as group separator
        ("1200.500 TND", Decimal("1200.500")),
        ("-45.750", Decimal("-45.750")),
        (10, Decimal("10")),
        (Decimal("3.5"), Decimal("3.5")),
    ],
)
def test_parse_decimal_accepts_documented_forms(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "n/a", "mille dinars", "12.3.4"])
def test_parse_decimal_refuses_to_guess(raw):
    with pytest.raises(MoneyParseError):
        parse_decimal(raw)


def test_quantize_uses_half_up_at_three_places():
    assert quantize_money(Decimal("254.1725"), 3) == Decimal("254.173")
    assert quantize_money(Decimal("254.1724"), 3) == Decimal("254.172")


def test_quantize_can_use_another_documented_mode():
    assert quantize_money(
        Decimal("254.1725"), 3, rounding=decimal.ROUND_HALF_EVEN
    ) == Decimal("254.172")


def test_quantize_rejects_unknown_rounding_mode():
    with pytest.raises(ValueError):
        quantize_money(Decimal("1.0"), 3, rounding="ROUND_SOMEHOW")


def test_tolerance_is_inclusive_at_the_boundary():
    tolerance = Decimal("0.001")
    assert within_tolerance(Decimal("100.000"), Decimal("100.001"), tolerance)
    assert within_tolerance(Decimal("100.000"), Decimal("99.999"), tolerance)
    assert not within_tolerance(Decimal("100.000"), Decimal("100.002"), tolerance)
