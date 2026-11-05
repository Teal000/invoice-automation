"""UNKNOWN_SUPPLIER, EXACT_FILE_DUPLICATE, BUSINESS_DUPLICATE."""

from __future__ import annotations

from factories import context, duplicate_match, raw_invoice, supplier

from invoice_automation.rules.base import Disposition, RuleStatus
from invoice_automation.rules.duplicates import business_duplicate, exact_file_duplicate
from invoice_automation.rules.supplier import unknown_supplier


class TestUnknownSupplier:
    def test_passes_when_the_reference_resolves(self):
        (result,) = unknown_supplier(context())
        assert result.status is RuleStatus.PASS
        assert "supplier_id" in result.details

    def test_fails_when_the_reference_does_not_resolve(self):
        ctx = context(raw_invoice(supplier_reference="SUP-999"), with_supplier=False)
        (result,) = unknown_supplier(ctx)
        assert result.failed
        assert result.observed == "SUP-999"
        assert result.disposition is Disposition.REVIEW

    def test_fails_when_the_supplier_is_inactive(self):
        ctx = context(supplier_record=supplier(is_active=False))
        (result,) = unknown_supplier(ctx)
        assert result.failed
        assert "inactive" in result.observed

    def test_skipped_when_no_reference_was_extracted(self):
        ctx = context(raw_invoice(supplier_reference=None), with_supplier=False)
        (result,) = unknown_supplier(ctx)
        assert result.status is RuleStatus.SKIPPED


class TestExactFileDuplicate:
    def test_passes_for_a_hash_not_seen_before(self):
        (result,) = exact_file_duplicate(context())
        assert result.status is RuleStatus.PASS

    def test_fails_and_links_to_the_earlier_submission(self):
        match = duplicate_match()
        (result,) = exact_file_duplicate(context(file_duplicate=match))
        assert result.failed
        assert result.disposition is Disposition.DUPLICATE_LINK
        assert result.details["earlier_submission_id"] == str(match.submission_id)
        assert result.details["first_seen_at"] == "2026-03-12T08:00:00+00:00"


class TestBusinessDuplicate:
    def test_passes_when_the_invoice_number_is_unused(self):
        (result,) = business_duplicate(context())
        assert result.status is RuleStatus.PASS

    def test_fails_when_an_invoice_already_exists_for_supplier_and_number(self):
        match = duplicate_match(kind="supplier_invoice_number", matched_value="FA-2026-0147")
        (result,) = business_duplicate(context(business_duplicate=match))
        assert result.failed
        # A different file claiming the same number is a review case, not a
        # silent link: both source files are retained.
        assert result.disposition is Disposition.REVIEW
        assert result.observed == "FA-2026-0147"
        assert result.details["existing_invoice_id"] == str(match.invoice_id)

    def test_skipped_without_an_invoice_number(self):
        ctx = context(raw_invoice(invoice_number=None))
        (result,) = business_duplicate(ctx)
        assert result.status is RuleStatus.SKIPPED
