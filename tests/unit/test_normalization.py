"""Normalisation: identifiers, dates and recorded field problems."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from factories import raw_invoice, rule_config

from invoice_automation.models import (
    RawLine,
    normalize_identifier,
    normalize_invoice,
    parse_date,
)


def test_identifier_normalisation_trims_collapses_and_uppercases():
    assert normalize_identifier("  fa-2026/011 ") == "FA-2026/011"
    assert normalize_identifier("FA  2026\t011") == "FA 2026 011"


def test_identifier_normalisation_does_not_merge_distinct_invoices():
    """Hyphens, dots and leading zeros are preserved, so these stay distinct."""
    variants = ["FA-001", "FA001", "FA.001", "FA-1", "fa-001 "]
    normalised = {normalize_identifier(v) for v in variants}
    # FA-001 and "fa-001 " normalise together; nothing else collapses.
    assert normalised == {"FA-001", "FA001", "FA.001", "FA-1"}


def test_identifier_normalisation_of_blank_is_none():
    assert normalize_identifier("   ") is None
    assert normalize_identifier(None) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03-12", date(2026, 3, 12)),
        ("12/03/2026", date(2026, 3, 12)),
        ("12-03-2026", date(2026, 3, 12)),
        ("12.03.2026", date(2026, 3, 12)),
    ],
)
def test_date_formats(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["03/2026", "mars 2026", "2026-13-45", ""])
def test_unparseable_dates_are_none(raw):
    assert parse_date(raw) is None


def test_baseline_invoice_normalises_without_problems():
    result = normalize_invoice(raw_invoice(), rule_config())
    assert result.problems == ()
    assert result.normalized.subtotal == Decimal("1337.750")
    assert result.normalized.total == Decimal("1591.923")
    assert len(result.normalized.lines) == 2


def test_missing_due_date_is_not_a_problem():
    result = normalize_invoice(raw_invoice(due_date=None), rule_config())
    assert result.problems == ()
    assert result.normalized.due_date is None


def test_absent_field_is_recorded_as_missing_not_zero():
    result = normalize_invoice(raw_invoice(total=None), rule_config())
    assert result.normalized.total is None
    assert [(p.field_name, p.reason) for p in result.problems] == [("total", "missing")]


def test_unreadable_field_is_recorded_as_unparseable():
    result = normalize_invoice(raw_invoice(total="environ mille"), rule_config())
    assert result.normalized.total is None
    assert [(p.field_name, p.reason) for p in result.problems] == [
        ("total", "unparseable")
    ]


def test_line_problems_carry_the_line_reference():
    raw = raw_invoice(
        line_items=[RawLine(line_number=1, description="Papier", quantity=None,
                            unit_price="120.050", line_amount="1200.500")]
    )
    result = normalize_invoice(raw, rule_config())
    assert [p.field_name for p in result.problems] == ["line_items[1].quantity"]


def test_amounts_are_quantised_to_the_configured_places():
    result = normalize_invoice(raw_invoice(tax_total="254.1725"), rule_config())
    assert result.normalized.tax_total == Decimal("254.173")


def test_quantity_keeps_its_own_precision():
    raw = raw_invoice(
        line_items=[RawLine(line_number=1, description="Cable", quantity="2.5555",
                            unit_price="10.000", line_amount="25.555")]
    )
    result = normalize_invoice(raw, rule_config())
    assert result.normalized.lines[0].quantity == Decimal("2.5555")
