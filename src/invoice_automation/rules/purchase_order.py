"""Purchase-order matching.

Codes: ``PO_NOT_FOUND``, ``PO_SUPPLIER_MISMATCH``, ``PO_AMOUNT_MISMATCH``.

This version matches one invoice to one purchase order. Partial deliveries,
split orders and multi-invoice allocation are out of scope and are not
approximated: a document that needs them is reported as unsupported upstream
rather than matched against a single order.

The amount tolerance has two parts and a difference passes if it is within
either: an absolute amount and a share of the order amount. Both are
configured and recorded in the results.
"""

from __future__ import annotations

from ..money import quantize_money
from .base import (
    Disposition,
    RuleResult,
    RuleStatus,
    Severity,
    ValidationContext,
    amount_text,
    supplier_text,
)


def _skipped(code: str, message: str, field_name: str) -> RuleResult:
    return RuleResult(
        code=code,
        status=RuleStatus.SKIPPED,
        severity=Severity.ERROR,
        disposition=Disposition.REVIEW,
        message=message,
        field_name=field_name,
    )


def po_not_found(ctx: ValidationContext) -> list[RuleResult]:
    po_number = ctx.invoice.purchase_order_number_normalized
    if po_number is None:
        return [
            _skipped(
                "PO_NOT_FOUND",
                "Not evaluated: no purchase-order number on the document.",
                "purchase_order_number",
            )
        ]
    if ctx.purchase_order is None:
        return [
            RuleResult(
                code="PO_NOT_FOUND",
                status=RuleStatus.FAIL,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="The referenced purchase order does not exist.",
                expected="a purchase order present in the register",
                observed=po_number,
                field_name="purchase_order_number",
            )
        ]
    return [
        RuleResult(
            code="PO_NOT_FOUND",
            status=RuleStatus.PASS,
            severity=Severity.ERROR,
            disposition=Disposition.REVIEW,
            message=f"Purchase order {po_number} found.",
            field_name="purchase_order_number",
            details={"purchase_order_id": str(ctx.purchase_order.purchase_order_id)},
        )
    ]


def po_supplier_mismatch(ctx: ValidationContext) -> list[RuleResult]:
    """The invoice supplier must be the supplier on the purchase order."""
    if ctx.purchase_order is None:
        return [
            _skipped(
                "PO_SUPPLIER_MISMATCH",
                "Not evaluated: no purchase order to compare against.",
                "purchase_order_number",
            )
        ]
    if ctx.supplier is None:
        return [
            _skipped(
                "PO_SUPPLIER_MISMATCH",
                "Not evaluated: the invoice supplier could not be resolved.",
                "supplier_reference",
            )
        ]
    if ctx.supplier.supplier_id != ctx.purchase_order.supplier_id:
        return [
            RuleResult(
                code="PO_SUPPLIER_MISMATCH",
                status=RuleStatus.FAIL,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="The invoice supplier is not the supplier on the purchase order.",
                expected=supplier_text(
                    ctx.purchase_order.supplier_id,
                    ctx.purchase_order.supplier_name,
                    ctx.purchase_order.supplier_reference,
                ),
                observed=supplier_text(
                    ctx.supplier.supplier_id,
                    ctx.supplier.name,
                    ctx.supplier.reference_normalized,
                ),
                field_name="supplier_reference",
                details={
                    "purchase_order_number": ctx.purchase_order.po_number_normalized,
                    "invoice_supplier_name": ctx.supplier.name,
                    "purchase_order_supplier_id": str(ctx.purchase_order.supplier_id),
                    "invoice_supplier_id": str(ctx.supplier.supplier_id),
                },
            )
        ]
    return [
        RuleResult(
            code="PO_SUPPLIER_MISMATCH",
            status=RuleStatus.PASS,
            severity=Severity.ERROR,
            disposition=Disposition.REVIEW,
            message="Invoice supplier matches the purchase-order supplier.",
            field_name="supplier_reference",
        )
    ]


def po_amount_mismatch(ctx: ValidationContext) -> list[RuleResult]:
    """The invoice total must be within tolerance of the order amount."""
    if ctx.purchase_order is None:
        return [
            _skipped(
                "PO_AMOUNT_MISMATCH",
                "Not evaluated: no purchase order to compare against.",
                "total",
            )
        ]
    total = ctx.invoice.total
    if total is None:
        return [
            _skipped(
                "PO_AMOUNT_MISMATCH",
                "Not evaluated: the invoice total could not be parsed.",
                "total",
            )
        ]

    expected = ctx.purchase_order.expected_amount
    difference = abs(total - expected)
    relative_allowance = quantize_money(
        expected * ctx.config.po_relative_tolerance,
        ctx.config.money_decimal_places,
        ctx.config.money_rounding,
    )
    allowance = max(ctx.config.po_absolute_tolerance, relative_allowance)

    details = {
        "purchase_order_number": ctx.purchase_order.po_number_normalized,
        "difference": amount_text(difference),
        "absolute_tolerance": amount_text(ctx.config.po_absolute_tolerance),
        "relative_tolerance": str(ctx.config.po_relative_tolerance),
        "applied_allowance": amount_text(allowance),
    }

    if difference <= allowance:
        return [
            RuleResult(
                code="PO_AMOUNT_MISMATCH",
                status=RuleStatus.PASS,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="The invoice total is within tolerance of the order amount.",
                expected=amount_text(expected),
                observed=amount_text(total),
                field_name="total",
                details=details,
            )
        ]
    return [
        RuleResult(
            code="PO_AMOUNT_MISMATCH",
            status=RuleStatus.FAIL,
            severity=Severity.ERROR,
            disposition=Disposition.REVIEW,
            message="The invoice total differs from the order amount by more than the tolerance.",
            expected=amount_text(expected),
            observed=amount_text(total),
            field_name="total",
            details=details,
        )
    ]
