"""PO_NOT_FOUND, PO_SUPPLIER_MISMATCH, PO_AMOUNT_MISMATCH."""

from __future__ import annotations

from decimal import Decimal

from factories import (
    OTHER_SUPPLIER_ID,
    OTHER_SUPPLIER_NAME,
    OTHER_SUPPLIER_REFERENCE,
    SUPPLIER_ID,
    SUPPLIER_NAME,
    context,
    other_supplier_order,
    purchase_order,
    raw_invoice,
    rule_config,
)

from invoice_automation.rules.base import RuleStatus
from invoice_automation.rules.purchase_order import (
    po_amount_mismatch,
    po_not_found,
    po_supplier_mismatch,
)


class TestPoNotFound:
    def test_passes_when_the_order_exists(self):
        (result,) = po_not_found(context())
        assert result.status is RuleStatus.PASS

    def test_fails_when_the_order_does_not_exist(self):
        ctx = context(raw_invoice(purchase_order_number="PO-9999"),
                      with_purchase_order=False)
        (result,) = po_not_found(ctx)
        assert result.failed
        assert result.observed == "PO-9999"

    def test_skipped_when_no_order_number_was_extracted(self):
        ctx = context(raw_invoice(purchase_order_number=None),
                      with_purchase_order=False)
        (result,) = po_not_found(ctx)
        assert result.status is RuleStatus.SKIPPED


class TestPoSupplierMismatch:
    def test_passes_when_suppliers_agree(self):
        (result,) = po_supplier_mismatch(context())
        assert result.status is RuleStatus.PASS

    def test_fails_when_the_order_belongs_to_another_supplier(self):
        ctx = context(purchase_order_record=other_supplier_order())
        (result,) = po_supplier_mismatch(ctx)
        assert result.failed
        # Both sides are named, because a reviewer reading this row has to be
        # able to tell which supplier is which.
        assert result.expected == f"{OTHER_SUPPLIER_NAME} ({OTHER_SUPPLIER_REFERENCE})"
        assert result.observed == f"{SUPPLIER_NAME} (SUP-001)"
        assert str(OTHER_SUPPLIER_ID) not in result.expected

    def test_the_ids_stay_available_for_machines(self):
        ctx = context(purchase_order_record=other_supplier_order())
        (result,) = po_supplier_mismatch(ctx)
        assert result.details["purchase_order_supplier_id"] == str(OTHER_SUPPLIER_ID)
        assert result.details["invoice_supplier_id"] == str(SUPPLIER_ID)

    def test_falls_back_to_the_id_when_the_order_has_no_supplier_name(self):
        # A registry that holds ids alone still produces a usable record; the
        # message degrades to the id rather than the lookup failing.
        ctx = context(
            purchase_order_record=purchase_order(
                supplier_id=OTHER_SUPPLIER_ID,
                supplier_name=None,
                supplier_reference=None,
            )
        )
        (result,) = po_supplier_mismatch(ctx)
        assert result.expected == f"supplier {OTHER_SUPPLIER_ID}"

    def test_skipped_without_a_resolved_supplier(self):
        ctx = context(with_supplier=False)
        (result,) = po_supplier_mismatch(ctx)
        assert result.status is RuleStatus.SKIPPED

    def test_skipped_without_an_order(self):
        ctx = context(with_purchase_order=False)
        (result,) = po_supplier_mismatch(ctx)
        assert result.status is RuleStatus.SKIPPED


class TestPoAmountMismatch:
    def test_passes_when_the_total_equals_the_order_amount(self):
        (result,) = po_amount_mismatch(context())
        assert result.status is RuleStatus.PASS
        assert result.details["difference"] == "0.000"

    def test_absolute_tolerance_applies_to_small_orders(self):
        # Order of 20.000: 2% is 0.400, so the absolute 1.000 allowance wins.
        ctx = context(
            raw_invoice(subtotal="20.000", tax_total="0.800", total="20.800",
                        line_items=[{"line_number": 1, "description": "Stylos",
                                     "quantity": "1", "unit_price": "20.000",
                                     "line_amount": "20.000"}]),
            purchase_order_record=purchase_order(expected_amount=Decimal("20.000")),
        )
        (result,) = po_amount_mismatch(ctx)
        assert result.status is RuleStatus.PASS
        assert result.details["applied_allowance"] == "1.000"

    def test_relative_tolerance_applies_to_large_orders(self):
        # The allowance is a share of the ORDER amount, not of the invoice
        # total: 2% of 1565.000 is 31.300, which exceeds the absolute 1.000
        # allowance and covers the 26.923 difference against the invoice.
        ctx = context(purchase_order_record=purchase_order(
            expected_amount=Decimal("1565.000")))
        (result,) = po_amount_mismatch(ctx)
        assert result.status is RuleStatus.PASS
        assert result.details["applied_allowance"] == "31.300"
        assert result.details["difference"] == "26.923"

    def test_relative_tolerance_does_not_cover_a_larger_gap(self):
        # 2% of 1560.000 is 31.200; the difference of 31.923 is outside it.
        ctx = context(purchase_order_record=purchase_order(
            expected_amount=Decimal("1560.000")))
        (result,) = po_amount_mismatch(ctx)
        assert result.failed
        assert result.details["applied_allowance"] == "31.200"

    def test_fails_beyond_both_tolerances(self):
        ctx = context(purchase_order_record=purchase_order(
            expected_amount=Decimal("1200.000")))
        (result,) = po_amount_mismatch(ctx)
        assert result.failed
        assert result.expected == "1200.000"
        assert result.observed == "1591.923"
        assert result.details["difference"] == "391.923"

    def test_tolerances_are_configurable(self):
        ctx = context(
            purchase_order_record=purchase_order(expected_amount=Decimal("1200.000")),
            config=rule_config(po_relative_tolerance=Decimal("0.50")),
        )
        (result,) = po_amount_mismatch(ctx)
        assert result.status is RuleStatus.PASS

    def test_skipped_when_the_total_is_unparseable(self):
        ctx = context(raw_invoice(total="environ mille"))
        (result,) = po_amount_mismatch(ctx)
        assert result.status is RuleStatus.SKIPPED
