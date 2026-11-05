"""LINE_TOTAL_MISMATCH, SUBTOTAL_MISMATCH, TOTAL_MISMATCH."""

from __future__ import annotations

from decimal import Decimal

from factories import context, raw_invoice, rule_config

from invoice_automation.models import RawLine
from invoice_automation.rules.arithmetic import (
    line_total_mismatch,
    subtotal_mismatch,
    total_mismatch,
)
from invoice_automation.rules.base import RuleStatus


def lines(*, line_amount_1="1200.500", quantity_1="10", unit_price_1="120.050"):
    return [
        RawLine(line_number=1, description="Ramettes papier A4 80g",
                quantity=quantity_1, unit_price=unit_price_1, line_amount=line_amount_1),
        RawLine(line_number=2, description="Cartouches encre noire",
                quantity="3", unit_price="45.750", line_amount="137.250"),
    ]


class TestLineTotalMismatch:
    def test_passes_on_the_baseline(self):
        results = line_total_mismatch(context())
        assert [r.status for r in results] == [RuleStatus.PASS, RuleStatus.PASS]

    def test_fails_only_the_offending_line(self):
        ctx = context(raw_invoice(line_items=lines(line_amount_1="1300.500")))
        results = line_total_mismatch(ctx)
        assert results[0].failed
        assert results[1].status is RuleStatus.PASS
        assert results[0].expected == "1200.500"
        assert results[0].observed == "1300.500"
        assert results[0].details["difference"] == "100.000"
        assert results[0].field_name == "line_items[1].line_amount"

    def test_fractional_quantity_is_rounded_once_at_the_configured_precision(self):
        # 2.5 x 10.001 = 25.0025 -> 25.003 half up
        ctx = context(
            raw_invoice(
                line_items=[
                    RawLine(line_number=1, description="Cable", quantity="2.5",
                            unit_price="10.001", line_amount="25.003")
                ]
            )
        )
        (result,) = line_total_mismatch(ctx)
        assert result.status is RuleStatus.PASS

    def test_one_millime_difference_is_inside_tolerance(self):
        ctx = context(raw_invoice(line_items=lines(line_amount_1="1200.501")))
        results = line_total_mismatch(ctx)
        assert results[0].status is RuleStatus.PASS

    def test_two_millimes_difference_fails(self):
        ctx = context(raw_invoice(line_items=lines(line_amount_1="1200.502")))
        results = line_total_mismatch(ctx)
        assert results[0].failed

    def test_tolerance_is_configurable(self):
        ctx = context(
            raw_invoice(line_items=lines(line_amount_1="1200.600")),
            config=rule_config(arithmetic_tolerance=Decimal("0.100")),
        )
        results = line_total_mismatch(ctx)
        assert results[0].status is RuleStatus.PASS

    def test_skipped_for_a_line_with_a_missing_value(self):
        ctx = context(raw_invoice(line_items=lines(quantity_1=None)))
        results = line_total_mismatch(ctx)
        assert results[0].status is RuleStatus.SKIPPED
        assert results[1].status is RuleStatus.PASS

    def test_skipped_without_lines(self):
        (result,) = line_total_mismatch(context(raw_invoice(line_items=[])))
        assert result.status is RuleStatus.SKIPPED


class TestSubtotalMismatch:
    def test_passes_on_the_baseline(self):
        (result,) = subtotal_mismatch(context())
        assert result.status is RuleStatus.PASS

    def test_fails_when_lines_do_not_sum_to_the_subtotal(self):
        (result,) = subtotal_mismatch(context(raw_invoice(subtotal="1400.000")))
        assert result.failed
        assert result.expected == "1337.750"
        assert result.observed == "1400.000"
        assert result.details["difference"] == "62.250"
        assert result.details["line_count"] == 2

    def test_skipped_when_a_line_amount_is_missing(self):
        ctx = context(raw_invoice(line_items=lines(line_amount_1=None)))
        (result,) = subtotal_mismatch(ctx)
        assert result.status is RuleStatus.SKIPPED

    def test_skipped_when_the_subtotal_is_missing(self):
        (result,) = subtotal_mismatch(context(raw_invoice(subtotal=None)))
        assert result.status is RuleStatus.SKIPPED


class TestTotalMismatch:
    def test_passes_on_the_baseline(self):
        (result,) = total_mismatch(context())
        assert result.status is RuleStatus.PASS

    def test_fails_when_subtotal_plus_tax_does_not_match_the_total(self):
        (result,) = total_mismatch(context(raw_invoice(total="1600.000")))
        assert result.failed
        assert result.expected == "1591.923"
        assert result.observed == "1600.000"
        assert result.details["subtotal"] == "1337.750"
        assert result.details["tax_total"] == "254.173"

    def test_fails_when_the_tax_amount_is_wrong_for_the_stated_total(self):
        (result,) = total_mismatch(context(raw_invoice(tax_total="200.000")))
        assert result.failed
        assert result.expected == "1537.750"

    def test_skipped_when_a_component_is_missing(self):
        (result,) = total_mismatch(context(raw_invoice(tax_total=None)))
        assert result.status is RuleStatus.SKIPPED
        assert "tax_total" in result.message
