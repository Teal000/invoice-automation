"""The rule engine: run every check, then decide one business outcome.

Business state is kept separate from job state. This module only produces
business state; whether the *processing* succeeded, retried or failed is the
job's own concern and lives in ``processing_jobs``.

One deliberate short circuit: when the identical file has already been
received, the remaining checks are not run. The arrival is recorded and linked
to the earlier submission instead of being reprocessed, which is what the plan
asks for and what keeps duplicate arrivals out of the throughput figures.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .arithmetic import line_total_mismatch, subtotal_mismatch, total_mismatch
from .base import Disposition, Rule, RuleResult, RuleStatus, ValidationContext
from .duplicates import business_duplicate, exact_file_duplicate
from .identity import (
    currency_unsupported,
    date_inconsistent,
    required_field_missing,
    unsupported_document,
)
from .purchase_order import po_amount_mismatch, po_not_found, po_supplier_mismatch
from .supplier import unknown_supplier


class BusinessState(StrEnum):
    """Outcome of an arrival, as recorded against the invoice."""

    RECEIVED = "received"
    PROCESSING = "processing"
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE = "duplicate"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"


#: Evaluation order. The file-duplicate check runs first because it can stop
#: the rest; everything after it is order independent.
RULE_ORDER: tuple[tuple[str, Rule], ...] = (
    ("EXACT_FILE_DUPLICATE", exact_file_duplicate),
    ("UNSUPPORTED_DOCUMENT", unsupported_document),
    ("CURRENCY_UNSUPPORTED", currency_unsupported),
    ("REQUIRED_FIELD_MISSING", required_field_missing),
    ("DATE_INCONSISTENT", date_inconsistent),
    ("UNKNOWN_SUPPLIER", unknown_supplier),
    ("BUSINESS_DUPLICATE", business_duplicate),
    ("LINE_TOTAL_MISMATCH", line_total_mismatch),
    ("SUBTOTAL_MISMATCH", subtotal_mismatch),
    ("TOTAL_MISMATCH", total_mismatch),
    ("PO_NOT_FOUND", po_not_found),
    ("PO_SUPPLIER_MISMATCH", po_supplier_mismatch),
    ("PO_AMOUNT_MISMATCH", po_amount_mismatch),
)

#: Every code the engine can emit, for documentation and test coverage checks.
ALL_RULE_CODES: tuple[str, ...] = tuple(code for code, _ in RULE_ORDER)


@dataclass(frozen=True)
class ValidationOutcome:
    state: BusinessState
    results: tuple[RuleResult, ...]
    short_circuited: bool = False

    @property
    def failed(self) -> tuple[RuleResult, ...]:
        return tuple(r for r in self.results if r.failed)

    @property
    def failed_codes(self) -> tuple[str, ...]:
        """Distinct failed codes, in evaluation order."""
        seen: list[str] = []
        for result in self.failed:
            if result.code not in seen:
                seen.append(result.code)
        return tuple(seen)

    def code_status(self, code: str) -> RuleStatus | None:
        for result in self.results:
            if result.code == code:
                return result.status
        return None


def run_rules(ctx: ValidationContext) -> ValidationOutcome:
    """Run the checks in order and derive the business state."""
    results: list[RuleResult] = []

    for code, rule in RULE_ORDER:
        produced = rule(ctx)
        results.extend(produced)
        if code == "EXACT_FILE_DUPLICATE" and any(r.failed for r in produced):
            return ValidationOutcome(
                state=BusinessState.DUPLICATE,
                results=tuple(results),
                short_circuited=True,
            )

    return ValidationOutcome(
        state=decide_state(results),
        results=tuple(results),
    )


def decide_state(results: list[RuleResult] | tuple[RuleResult, ...]) -> BusinessState:
    """Map rule results to one business state.

    Precedence: an unsupported document outranks review, because a reviewer
    cannot correct a document this version does not handle. Everything else
    that failed sends the invoice to review. ``validated`` requires that no
    check failed; a check that was skipped does not count as a pass, and the
    skipped status is stored so reports can tell the two apart.
    """
    failures = [r for r in results if r.failed]
    if not failures:
        return BusinessState.VALIDATED
    if any(r.disposition is Disposition.UNSUPPORTED for r in failures):
        return BusinessState.UNSUPPORTED
    if any(r.disposition is Disposition.DUPLICATE_LINK for r in failures):
        return BusinessState.DUPLICATE
    return BusinessState.NEEDS_REVIEW
