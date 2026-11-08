"""Shared types for the deterministic validation rules.

Every rule is a plain function from a :class:`ValidationContext` to a list of
:class:`RuleResult`. The context holds data that has *already* been fetched
(the resolved supplier, the referenced purchase order, duplicate lookups), so
no rule performs I/O and every rule can be unit tested without a database.

Each result records the expected and the observed value. That is deliberate:
a failed check that only says "mismatch" is not reviewable, and the plan
requires expected/observed to be stored for every failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Callable
from uuid import UUID

from ..config import RuleConfig
from ..models import FieldProblem, NormalizedInvoice, SubmissionInput


class Severity(StrEnum):
    """How serious a failed check is."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Disposition(StrEnum):
    """What a failed check implies for the business outcome.

    ``REVIEW``
        A human should look at it. The invoice is recorded as needing review.
    ``UNSUPPORTED``
        The document is outside the scope of this version. It gets an explicit
        exception code instead of being approximated.
    ``DUPLICATE_LINK``
        The arrival is a repeat. It is linked to the earlier record and does
        not create a second canonical invoice.
    """

    REVIEW = "review"
    UNSUPPORTED = "unsupported"
    DUPLICATE_LINK = "duplicate_link"


class RuleStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    #: The check could not be evaluated, e.g. a PO check when the invoice
    #: carries no PO number. Recorded rather than silently dropped, so a
    #: skipped check is never mistaken for a passed one.
    SKIPPED = "skipped"


@dataclass(frozen=True)
class RuleResult:
    code: str
    status: RuleStatus
    severity: Severity
    disposition: Disposition
    message: str
    expected: str | None = None
    observed: str | None = None
    field_name: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status is RuleStatus.FAIL

    @property
    def passed(self) -> bool:
        return self.status is RuleStatus.PASS


# ---------------------------------------------------------------------------
# Reference data, as the rules see it. These are read-only views of database
# rows; the repository layer builds them.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupplierRecord:
    supplier_id: UUID
    name: str
    reference_normalized: str
    permitted_currency: str
    is_active: bool = True


@dataclass(frozen=True)
class PurchaseOrderRecord:
    """A purchase order, with enough of its supplier to describe it.

    ``supplier_name``/``supplier_reference`` are carried so that a rule
    reporting a supplier disagreement can name both sides. Without them the
    only identifier available is ``supplier_id``, and a reviewer shown two
    UUIDs learns nothing about what disagreed. They are optional because a
    record can be built from a registry that holds ids alone.
    """

    purchase_order_id: UUID
    po_number_normalized: str
    supplier_id: UUID
    currency: str
    expected_amount: Decimal
    state: str
    supplier_name: str | None = None
    supplier_reference: str | None = None


@dataclass(frozen=True)
class DuplicateMatch:
    """An earlier record that the current arrival matches."""

    kind: str  # "file_hash" or "supplier_invoice_number"
    submission_id: UUID | None = None
    invoice_id: UUID | None = None
    matched_value: str | None = None
    first_seen_at: datetime | None = None


@dataclass(frozen=True)
class ValidationContext:
    """Everything the rules need, already fetched.

    ``supplier``/``purchase_order`` are ``None`` when the lookup found nothing,
    which is what ``UNKNOWN_SUPPLIER`` and ``PO_NOT_FOUND`` report on.
    """

    invoice: NormalizedInvoice
    submission: SubmissionInput
    config: RuleConfig
    problems: tuple[FieldProblem, ...] = ()
    supplier: SupplierRecord | None = None
    purchase_order: PurchaseOrderRecord | None = None
    file_duplicate: DuplicateMatch | None = None
    business_duplicate: DuplicateMatch | None = None


#: A rule takes a context and returns zero or more results.
Rule = Callable[[ValidationContext], list[RuleResult]]


def amount_text(value: Decimal | None) -> str | None:
    """Render an amount for storage in expected/observed, or ``None``."""
    return None if value is None else format(value, "f")


def supplier_text(
    supplier_id: UUID, name: str | None = None, reference: str | None = None
) -> str:
    """Name a supplier for a reviewer reading a failed check.

    ``expected`` and ``observed`` exist so a person can see what the rule
    wanted beside what the document said. A bare UUID satisfies neither, so
    the name is used whenever it is known, with the supplier reference in
    brackets because two suppliers may share a name. The id is the last
    resort rather than the default, and it stays in ``details`` either way
    for anything reading the record by machine.
    """
    if not name:
        return f"supplier {supplier_id}"
    if reference:
        return f"{name} ({reference})"
    return name
