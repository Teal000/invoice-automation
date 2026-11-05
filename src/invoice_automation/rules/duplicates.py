"""Duplicate detection.

Codes: ``EXACT_FILE_DUPLICATE``, ``BUSINESS_DUPLICATE``.

Two different things, kept apart on purpose:

* ``EXACT_FILE_DUPLICATE`` means the identical file arrived before (same
  SHA-256). The arrival is still recorded as a submission and linked to the
  earlier one, but it is not reprocessed and creates no second invoice.
* ``BUSINESS_DUPLICATE`` means a different file claims the same supplier and
  invoice number as an existing canonical invoice. Both source files are kept
  and a reviewer decides, because the two documents are not identical and one
  of them may be the corrected version.

Neither outcome is called fraud. A duplicate here is a process event.
"""

from __future__ import annotations

from .base import (
    Disposition,
    RuleResult,
    RuleStatus,
    Severity,
    ValidationContext,
)


def exact_file_duplicate(ctx: ValidationContext) -> list[RuleResult]:
    match = ctx.file_duplicate
    if match is None:
        return [
            RuleResult(
                code="EXACT_FILE_DUPLICATE",
                status=RuleStatus.PASS,
                severity=Severity.WARNING,
                disposition=Disposition.DUPLICATE_LINK,
                message="This file hash has not been received before.",
                field_name="file_sha256",
            )
        ]
    return [
        RuleResult(
            code="EXACT_FILE_DUPLICATE",
            status=RuleStatus.FAIL,
            severity=Severity.WARNING,
            disposition=Disposition.DUPLICATE_LINK,
            message="An identical file was already received; linked to the earlier submission.",
            expected="a file hash not seen before",
            observed=ctx.submission.file_sha256,
            field_name="file_sha256",
            details={
                "earlier_submission_id": str(match.submission_id)
                if match.submission_id
                else None,
                "earlier_invoice_id": str(match.invoice_id) if match.invoice_id else None,
                "first_seen_at": match.first_seen_at.isoformat()
                if match.first_seen_at
                else None,
            },
        )
    ]


def business_duplicate(ctx: ValidationContext) -> list[RuleResult]:
    invoice_number = ctx.invoice.invoice_number_normalized
    if invoice_number is None:
        return [
            RuleResult(
                code="BUSINESS_DUPLICATE",
                status=RuleStatus.SKIPPED,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="Not evaluated: no invoice number on the document.",
                field_name="invoice_number",
            )
        ]
    match = ctx.business_duplicate
    if match is None:
        return [
            RuleResult(
                code="BUSINESS_DUPLICATE",
                status=RuleStatus.PASS,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="No existing invoice for this supplier and invoice number.",
                field_name="invoice_number",
            )
        ]
    return [
        RuleResult(
            code="BUSINESS_DUPLICATE",
            status=RuleStatus.FAIL,
            severity=Severity.ERROR,
            disposition=Disposition.REVIEW,
            message=(
                "An invoice with this supplier and invoice number already exists; "
                "both source files are retained for review."
            ),
            expected="an unused supplier and invoice-number combination",
            observed=match.matched_value or invoice_number,
            field_name="invoice_number",
            details={
                "existing_invoice_id": str(match.invoice_id) if match.invoice_id else None,
                "first_seen_at": match.first_seen_at.isoformat()
                if match.first_seen_at
                else None,
            },
        )
    ]
