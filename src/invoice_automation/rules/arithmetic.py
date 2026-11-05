"""Arithmetic reconciliation checks.

Codes: ``LINE_TOTAL_MISMATCH``, ``SUBTOTAL_MISMATCH``, ``TOTAL_MISMATCH``.

All comparisons use :class:`decimal.Decimal`, are quantised to the configured
number of decimal places with the configured rounding mode, and allow the
configured absolute tolerance (one millime by default). The configuration is
explicit rather than implied, because a tolerance that is not written down is
not reproducible.

``TOTAL_MISMATCH`` checks internal consistency: ``subtotal + tax_total``
against ``total``. It does not check the tax amount against the configured tax
rate; the configured rate drives the synthetic document generator, and this
project makes no claim about tax correctness.
"""

from __future__ import annotations

from decimal import Decimal

from ..money import quantize_money, within_tolerance
from .base import (
    Disposition,
    RuleResult,
    RuleStatus,
    Severity,
    ValidationContext,
    amount_text,
)


def _quantize(value: Decimal, ctx: ValidationContext) -> Decimal:
    return quantize_money(
        value, ctx.config.money_decimal_places, ctx.config.money_rounding
    )


def _skipped(code: str, message: str, field_name: str | None = None) -> RuleResult:
    return RuleResult(
        code=code,
        status=RuleStatus.SKIPPED,
        severity=Severity.ERROR,
        disposition=Disposition.REVIEW,
        message=message,
        field_name=field_name,
    )


def line_total_mismatch(ctx: ValidationContext) -> list[RuleResult]:
    """Quantity x unit price must reconcile with the line amount, per line."""
    results: list[RuleResult] = []
    if not ctx.invoice.lines:
        return [_skipped("LINE_TOTAL_MISMATCH", "Not evaluated: no line items.", "line_items")]

    for line in ctx.invoice.lines:
        field_name = f"line_items[{line.line_number}].line_amount"
        if line.quantity is None or line.unit_price is None or line.line_amount is None:
            results.append(
                _skipped(
                    "LINE_TOTAL_MISMATCH",
                    f"Not evaluated for line {line.line_number}: a value is missing.",
                    field_name,
                )
            )
            continue

        expected = _quantize(line.quantity * line.unit_price, ctx)
        if within_tolerance(expected, line.line_amount, ctx.config.arithmetic_tolerance):
            results.append(
                RuleResult(
                    code="LINE_TOTAL_MISMATCH",
                    status=RuleStatus.PASS,
                    severity=Severity.ERROR,
                    disposition=Disposition.REVIEW,
                    message=f"Line {line.line_number} reconciles.",
                    field_name=field_name,
                )
            )
        else:
            results.append(
                RuleResult(
                    code="LINE_TOTAL_MISMATCH",
                    status=RuleStatus.FAIL,
                    severity=Severity.ERROR,
                    disposition=Disposition.REVIEW,
                    message=(
                        f"Line {line.line_number}: quantity x unit price does not "
                        "match the line amount."
                    ),
                    expected=amount_text(expected),
                    observed=amount_text(line.line_amount),
                    field_name=field_name,
                    details={
                        "line_number": line.line_number,
                        "quantity": amount_text(line.quantity),
                        "unit_price": amount_text(line.unit_price),
                        "difference": amount_text(
                            _quantize(line.line_amount - expected, ctx)
                        ),
                    },
                )
            )
    return results


def subtotal_mismatch(ctx: ValidationContext) -> list[RuleResult]:
    """Line amounts must reconcile with the document subtotal."""
    invoice = ctx.invoice
    if invoice.subtotal is None:
        return [_skipped("SUBTOTAL_MISMATCH", "Not evaluated: no subtotal.", "subtotal")]
    if not invoice.lines:
        return [_skipped("SUBTOTAL_MISMATCH", "Not evaluated: no line items.", "line_items")]
    if any(line.line_amount is None for line in invoice.lines):
        return [
            _skipped(
                "SUBTOTAL_MISMATCH",
                "Not evaluated: at least one line amount is missing.",
                "subtotal",
            )
        ]

    expected = _quantize(sum(line.line_amount for line in invoice.lines), ctx)
    if within_tolerance(expected, invoice.subtotal, ctx.config.arithmetic_tolerance):
        return [
            RuleResult(
                code="SUBTOTAL_MISMATCH",
                status=RuleStatus.PASS,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="Line amounts reconcile with the subtotal.",
                field_name="subtotal",
            )
        ]
    return [
        RuleResult(
            code="SUBTOTAL_MISMATCH",
            status=RuleStatus.FAIL,
            severity=Severity.ERROR,
            disposition=Disposition.REVIEW,
            message="The sum of line amounts does not match the subtotal.",
            expected=amount_text(expected),
            observed=amount_text(invoice.subtotal),
            field_name="subtotal",
            details={
                "line_count": len(invoice.lines),
                "difference": amount_text(_quantize(invoice.subtotal - expected, ctx)),
            },
        )
    ]


def total_mismatch(ctx: ValidationContext) -> list[RuleResult]:
    """Subtotal plus document tax must reconcile with the document total."""
    invoice = ctx.invoice
    missing = [
        name
        for name, value in (
            ("subtotal", invoice.subtotal),
            ("tax_total", invoice.tax_total),
            ("total", invoice.total),
        )
        if value is None
    ]
    if missing:
        return [
            _skipped(
                "TOTAL_MISMATCH",
                f"Not evaluated: {', '.join(missing)} missing.",
                "total",
            )
        ]

    expected = _quantize(invoice.subtotal + invoice.tax_total, ctx)
    if within_tolerance(expected, invoice.total, ctx.config.arithmetic_tolerance):
        return [
            RuleResult(
                code="TOTAL_MISMATCH",
                status=RuleStatus.PASS,
                severity=Severity.ERROR,
                disposition=Disposition.REVIEW,
                message="Subtotal plus tax reconciles with the total.",
                field_name="total",
            )
        ]
    return [
        RuleResult(
            code="TOTAL_MISMATCH",
            status=RuleStatus.FAIL,
            severity=Severity.ERROR,
            disposition=Disposition.REVIEW,
            message="Subtotal plus tax does not match the document total.",
            expected=amount_text(expected),
            observed=amount_text(invoice.total),
            field_name="total",
            details={
                "subtotal": amount_text(invoice.subtotal),
                "tax_total": amount_text(invoice.tax_total),
                "difference": amount_text(_quantize(invoice.total - expected, ctx)),
            },
        )
    ]
