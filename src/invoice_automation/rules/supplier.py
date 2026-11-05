"""Supplier resolution check.

Code: ``UNKNOWN_SUPPLIER``.

The lookup itself happens in the repository layer, which resolves the
normalised supplier reference against the registry and puts the result (or
``None``) on the context. This rule only reports on that outcome.
"""

from __future__ import annotations

from .base import (
    Disposition,
    RuleResult,
    RuleStatus,
    Severity,
    ValidationContext,
)


def unknown_supplier(ctx: ValidationContext) -> list[RuleResult]:
    reference = ctx.invoice.supplier_reference_normalized
    if reference is None:
        return [
            RuleResult(
                code="UNKNOWN_SUPPLIER",
                status=RuleStatus.SKIPPED,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="Not evaluated: no supplier reference on the document.",
                field_name="supplier_reference",
            )
        ]
    if ctx.supplier is None:
        return [
            RuleResult(
                code="UNKNOWN_SUPPLIER",
                status=RuleStatus.FAIL,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="The supplier reference does not resolve to the registry.",
                expected="a reference present in the supplier registry",
                observed=reference,
                field_name="supplier_reference",
            )
        ]
    if not ctx.supplier.is_active:
        return [
            RuleResult(
                code="UNKNOWN_SUPPLIER",
                status=RuleStatus.FAIL,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="The supplier resolves but is marked inactive in the registry.",
                expected="an active supplier",
                observed=f"{reference} (inactive)",
                field_name="supplier_reference",
                details={"supplier_id": str(ctx.supplier.supplier_id)},
            )
        ]
    return [
        RuleResult(
            code="UNKNOWN_SUPPLIER",
            status=RuleStatus.PASS,
            severity=Severity.ERROR,
            disposition=Disposition.REVIEW,
            message=f"Supplier resolved to {ctx.supplier.name}.",
            field_name="supplier_reference",
            details={"supplier_id": str(ctx.supplier.supplier_id)},
        )
    ]
