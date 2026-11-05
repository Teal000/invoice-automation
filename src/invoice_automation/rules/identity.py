"""Field-level and document-level checks.

Codes: ``REQUIRED_FIELD_MISSING``, ``DATE_INCONSISTENT``,
``CURRENCY_UNSUPPORTED``, ``UNSUPPORTED_DOCUMENT``.
"""

from __future__ import annotations

from ..models import KNOWN_UNSUPPORTED_DOCUMENT_TYPES, SUPPORTED_DOCUMENT_TYPE
from .base import (
    Disposition,
    RuleResult,
    RuleStatus,
    Severity,
    ValidationContext,
)

#: Header fields that must be present and parseable. ``due_date`` is absent
#: from this list on purpose: the data contract marks it as optional.
REQUIRED_HEADER_FIELDS = (
    "invoice_number",
    "supplier_reference",
    "invoice_date",
    "currency",
    "purchase_order_number",
    "subtotal",
    "tax_total",
    "total",
    "line_items",
)

#: Line-item fields that must be present and parseable.
REQUIRED_LINE_FIELDS = ("description", "quantity", "unit_price", "line_amount")


def _is_required(field_name: str) -> bool:
    if field_name in REQUIRED_HEADER_FIELDS:
        return True
    if field_name.startswith("line_items[") and "." in field_name:
        return field_name.rsplit(".", 1)[1] in REQUIRED_LINE_FIELDS
    return False


def required_field_missing(ctx: ValidationContext) -> list[RuleResult]:
    """One result per required field that is absent or unparseable."""
    relevant = [p for p in ctx.problems if _is_required(p.field_name)]
    if not relevant:
        return [
            RuleResult(
                code="REQUIRED_FIELD_MISSING",
                status=RuleStatus.PASS,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="All required fields are present and parseable.",
            )
        ]
    return [
        RuleResult(
            code="REQUIRED_FIELD_MISSING",
            status=RuleStatus.FAIL,
            severity=Severity.ERROR,
            disposition=Disposition.REVIEW,
            message=f"Required field {problem.field_name} is {problem.reason}.",
            expected="a present, parseable value",
            observed=problem.raw_value if problem.raw_value is not None else "(absent)",
            field_name=problem.field_name,
            details={"reason": problem.reason},
        )
        for problem in relevant
    ]


def date_inconsistent(ctx: ValidationContext) -> list[RuleResult]:
    """Due date must not fall before the invoice date.

    Skipped when either date is absent or unparseable; that is already
    reported by ``REQUIRED_FIELD_MISSING`` (invoice date) and a missing due
    date is permitted by the contract.
    """
    invoice = ctx.invoice
    if invoice.invoice_date is None:
        return [
            RuleResult(
                code="DATE_INCONSISTENT",
                status=RuleStatus.SKIPPED,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="Not evaluated: the invoice date could not be parsed.",
                field_name="invoice_date",
            )
        ]
    if invoice.due_date is None:
        return [
            RuleResult(
                code="DATE_INCONSISTENT",
                status=RuleStatus.SKIPPED,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="Not evaluated: no due date on the document.",
                field_name="due_date",
            )
        ]
    if invoice.due_date < invoice.invoice_date:
        return [
            RuleResult(
                code="DATE_INCONSISTENT",
                status=RuleStatus.FAIL,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="The due date falls before the invoice date.",
                expected=f"due date on or after {invoice.invoice_date.isoformat()}",
                observed=invoice.due_date.isoformat(),
                field_name="due_date",
            )
        ]
    return [
        RuleResult(
            code="DATE_INCONSISTENT",
            status=RuleStatus.PASS,
            severity=Severity.ERROR,
            disposition=Disposition.REVIEW,
            message="Invoice and due dates are consistent.",
        )
    ]


def currency_unsupported(ctx: ValidationContext) -> list[RuleResult]:
    """The currency must be in the configured set.

    This version configures exactly one currency and performs no conversion,
    so another currency is reported as unsupported rather than sent to review
    for a correction that could not be made correctly.
    """
    currency = ctx.invoice.currency
    supported = ctx.config.supported_currencies
    if currency is None:
        return [
            RuleResult(
                code="CURRENCY_UNSUPPORTED",
                status=RuleStatus.SKIPPED,
                severity=Severity.ERROR,
                disposition=Disposition.UNSUPPORTED,
                message="Not evaluated: no currency on the document.",
                field_name="currency",
            )
        ]
    if currency not in supported:
        return [
            RuleResult(
                code="CURRENCY_UNSUPPORTED",
                status=RuleStatus.FAIL,
                severity=Severity.ERROR,
                disposition=Disposition.UNSUPPORTED,
                message="The document currency is not configured in this version.",
                expected=", ".join(supported),
                observed=currency,
                field_name="currency",
            )
        ]
    return [
        RuleResult(
            code="CURRENCY_UNSUPPORTED",
            status=RuleStatus.PASS,
            severity=Severity.ERROR,
            disposition=Disposition.UNSUPPORTED,
            message=f"Currency {currency} is configured.",
        )
    ]


def unsupported_document(ctx: ValidationContext) -> list[RuleResult]:
    """Only plain invoices are handled; other document kinds are excluded."""
    document_type = ctx.invoice.document_type
    if document_type == SUPPORTED_DOCUMENT_TYPE:
        return [
            RuleResult(
                code="UNSUPPORTED_DOCUMENT",
                status=RuleStatus.PASS,
                severity=Severity.ERROR,
                disposition=Disposition.UNSUPPORTED,
                message="Document type is a supported invoice.",
            )
        ]
    known = document_type in KNOWN_UNSUPPORTED_DOCUMENT_TYPES
    return [
        RuleResult(
            code="UNSUPPORTED_DOCUMENT",
            status=RuleStatus.FAIL,
            severity=Severity.ERROR,
            disposition=Disposition.UNSUPPORTED,
            message=(
                "Document kind is excluded from this version."
                if known
                else "Document kind is not recognised by this version."
            ),
            expected=SUPPORTED_DOCUMENT_TYPE,
            observed=document_type,
            field_name="document_type",
            details={"recognised_exclusion": known},
        )
    ]
