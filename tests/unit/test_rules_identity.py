"""REQUIRED_FIELD_MISSING, DATE_INCONSISTENT, CURRENCY_UNSUPPORTED, UNSUPPORTED_DOCUMENT."""

from __future__ import annotations

import pytest
from factories import context, raw_invoice

from invoice_automation.models import RawLine
from invoice_automation.rules.base import Disposition, RuleStatus
from invoice_automation.rules.identity import (
    currency_unsupported,
    date_inconsistent,
    required_field_missing,
    unsupported_document,
)


class TestRequiredFieldMissing:
    def test_passes_on_the_baseline(self):
        (result,) = required_field_missing(context())
        assert result.status is RuleStatus.PASS

    @pytest.mark.parametrize(
        "field_name",
        [
            "invoice_number",
            "supplier_reference",
            "invoice_date",
            "currency",
            "purchase_order_number",
            "subtotal",
            "tax_total",
            "total",
        ],
    )
    def test_each_required_header_field_is_reported(self, field_name):
        ctx = context(raw_invoice(**{field_name: None}))
        results = required_field_missing(ctx)
        assert [r.field_name for r in results if r.failed] == [field_name]
        assert results[0].observed == "(absent)"

    def test_unparseable_value_is_reported_with_the_raw_value(self):
        ctx = context(raw_invoice(total="environ mille"))
        (result,) = [r for r in required_field_missing(ctx) if r.failed]
        assert result.field_name == "total"
        assert result.observed == "environ mille"
        assert result.details["reason"] == "unparseable"

    def test_missing_due_date_does_not_fail(self):
        (result,) = required_field_missing(context(raw_invoice(due_date=None)))
        assert result.status is RuleStatus.PASS

    def test_missing_line_items_is_reported(self):
        ctx = context(raw_invoice(line_items=[]))
        failures = [r for r in required_field_missing(ctx) if r.failed]
        assert [r.field_name for r in failures] == ["line_items"]

    def test_missing_line_field_is_reported_per_line(self):
        raw = raw_invoice(
            line_items=[
                RawLine(line_number=1, description="Papier", quantity="10",
                        unit_price="120.050", line_amount="1200.500"),
                RawLine(line_number=2, description=None, quantity="3",
                        unit_price="45.750", line_amount="137.250"),
            ]
        )
        failures = [r for r in required_field_missing(context(raw)) if r.failed]
        assert [r.field_name for r in failures] == ["line_items[2].description"]

    def test_every_failure_routes_to_review(self):
        ctx = context(raw_invoice(invoice_number=None))
        failures = [r for r in required_field_missing(ctx) if r.failed]
        assert all(r.disposition is Disposition.REVIEW for r in failures)


class TestDateInconsistent:
    def test_passes_when_due_date_follows_invoice_date(self):
        (result,) = date_inconsistent(context())
        assert result.status is RuleStatus.PASS

    def test_fails_when_due_date_precedes_invoice_date(self):
        ctx = context(raw_invoice(invoice_date="2026-03-12", due_date="2026-03-01"))
        (result,) = date_inconsistent(ctx)
        assert result.failed
        assert result.observed == "2026-03-01"
        assert "2026-03-12" in result.expected

    def test_passes_when_dates_are_equal(self):
        ctx = context(raw_invoice(invoice_date="2026-03-12", due_date="2026-03-12"))
        (result,) = date_inconsistent(ctx)
        assert result.status is RuleStatus.PASS

    def test_skipped_without_a_due_date(self):
        (result,) = date_inconsistent(context(raw_invoice(due_date=None)))
        assert result.status is RuleStatus.SKIPPED

    def test_skipped_when_the_invoice_date_is_unparseable(self):
        ctx = context(raw_invoice(invoice_date="mars 2026"))
        (result,) = date_inconsistent(ctx)
        assert result.status is RuleStatus.SKIPPED


class TestCurrencyUnsupported:
    def test_passes_for_the_configured_currency(self):
        (result,) = currency_unsupported(context())
        assert result.status is RuleStatus.PASS

    def test_lowercase_currency_is_accepted_after_normalisation(self):
        (result,) = currency_unsupported(context(raw_invoice(currency="tnd")))
        assert result.status is RuleStatus.PASS

    def test_other_currency_is_unsupported_not_review(self):
        (result,) = currency_unsupported(context(raw_invoice(currency="EUR")))
        assert result.failed
        assert result.disposition is Disposition.UNSUPPORTED
        assert result.observed == "EUR"
        assert result.expected == "TND"

    def test_skipped_when_absent(self):
        (result,) = currency_unsupported(context(raw_invoice(currency=None)))
        assert result.status is RuleStatus.SKIPPED


class TestUnsupportedDocument:
    def test_passes_for_an_invoice(self):
        (result,) = unsupported_document(context())
        assert result.status is RuleStatus.PASS

    @pytest.mark.parametrize(
        "document_type", ["credit_note", "partial_invoice", "proforma", "statement"]
    )
    def test_known_excluded_kinds_are_flagged_as_recognised(self, document_type):
        ctx = context(raw_invoice(document_type=document_type))
        (result,) = unsupported_document(ctx)
        assert result.failed
        assert result.disposition is Disposition.UNSUPPORTED
        assert result.details["recognised_exclusion"] is True
        assert result.observed == document_type

    def test_unknown_kind_is_flagged_as_unrecognised(self):
        ctx = context(raw_invoice(document_type="bordereau"))
        (result,) = unsupported_document(ctx)
        assert result.failed
        assert result.details["recognised_exclusion"] is False
