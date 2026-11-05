"""Reviewer actions: correct, reject, accept an exception.

The last of the plan's core requirements. A reviewer looks at an invoice that
failed a check and does one of three things, each of which must say why.

A correction is submitted as a complete set of corrected values, in the same
shape extraction produces. That is deliberate: the corrected values then run
through exactly the same normalisation and exactly the same rules as the
extracted ones, so a reviewer cannot produce an invoice that was never
checked, and a rule change applies to corrections for free.

Every action requires a stated reason, enforced by the database. Every action
is taken against a revision number the reviewer names, so an edit made while
somebody else was editing is refused rather than silently overwriting theirs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg

from .config import RuleConfig, Settings, get_settings
from .models import RawInvoice, SubmissionInput, normalize_invoice
from .rules.base import RuleResult, ValidationContext
from .rules.engine import BusinessState, run_rules

#: Actions a reviewer can take.
CORRECT = "correct"
REJECT = "reject"
ACCEPT_EXCEPTION = "accept_exception"


class InvoiceNotFound(LookupError):
    pass


class RevisionConflict(RuntimeError):
    """The invoice moved on since the reviewer loaded it.

    Raised rather than resolved: the reviewer is looking at values that are no
    longer current, and silently applying their edit would discard whatever
    changed in between.
    """

    def __init__(self, invoice_id: UUID, expected: int, actual: int | None):
        super().__init__(
            f"invoice {invoice_id} is at revision {actual}, not {expected}"
        )
        self.invoice_id = invoice_id
        self.expected = expected
        self.actual = actual


class ReviewNotApplicable(RuntimeError):
    """The action does not make sense for the invoice's current state."""


@dataclass(frozen=True)
class ReviewOutcome:
    invoice_id: UUID
    action: str
    actor: str
    reason: str
    state: BusinessState
    revision_number: int | None = None
    failed_codes: tuple[str, ...] = ()
    results: tuple[RuleResult, ...] = ()
    review_action_id: UUID | None = None


def _load(conn: psycopg.Connection, invoice_id: UUID) -> dict[str, Any]:
    from .db import repository as repo

    row = repo.get_invoice_for_review(conn, invoice_id)
    if row is None:
        raise InvoiceNotFound(str(invoice_id))
    return row


def _check_revision(row: dict[str, Any], expected_revision_number: int) -> None:
    actual = row["current_revision_number"]
    if actual != expected_revision_number:
        raise RevisionConflict(row["invoice_id"], expected_revision_number, actual)


def apply_correction(
    conn: psycopg.Connection,
    invoice_id: UUID,
    *,
    expected_revision_number: int,
    raw: RawInvoice,
    actor: str,
    reason: str,
    config: RuleConfig | None = None,
    settings: Settings | None = None,
) -> ReviewOutcome:
    """Record corrected values as a new revision and re-run every check.

    The new revision becomes current and the business state is whatever the
    rules now say: a correction that fixes the problem validates the invoice,
    and one that does not leaves it in review with the remaining failures.
    Nothing is taken on trust because a person typed it.
    """
    from .db import jobs as job_repo
    from .db import repository as repo

    settings = settings or get_settings()
    config = config or settings.rule_config()

    row = _load(conn, invoice_id)
    if not conn.autocommit:
        conn.rollback()
    _check_revision(row, expected_revision_number)
    if row["business_state"] == "rejected":
        raise ReviewNotApplicable(
            "a rejected invoice cannot be corrected; resubmit the document instead"
        )

    normalisation = normalize_invoice(raw, config)
    invoice = normalisation.normalized

    with conn.transaction():
        supplier = repo.get_supplier(conn, invoice.supplier_reference_normalized)
        purchase_order = repo.get_purchase_order(
            conn, invoice.purchase_order_number_normalized
        )
        key = repo.supplier_key(supplier, invoice.supplier_reference_normalized)
        # Excluding this invoice matters: without it, revalidation would find
        # the invoice's own number in the register and flag it as a duplicate
        # of itself.
        business_duplicate = repo.find_business_duplicate(
            conn,
            key,
            invoice.invoice_number_normalized,
            exclude_invoice_id=invoice_id,
        )

        submission = SubmissionInput(
            source=row["source"] or "cli",
            original_file_name=row["original_file_name"] or "(corrected)",
            file_sha256=row["content_sha256"] or "0" * 64,
            storage_path=row["storage_path"],
        )

        outcome = run_rules(
            ValidationContext(
                invoice=invoice,
                submission=submission,
                config=config,
                problems=normalisation.problems,
                supplier=supplier,
                purchase_order=purchase_order,
                # No new file arrived, so the file-duplicate check has nothing
                # to compare. It is recorded as a pass, not as a skip, because
                # the bytes were checked when they arrived.
                file_duplicate=None,
                business_duplicate=business_duplicate,
            )
        )

        revision_number = repo.next_revision_number(conn, invoice_id)
        revision_id = repo.insert_revision(
            conn,
            invoice_id=invoice_id,
            revision_number=revision_number,
            source="reviewer_correction",
            submission_id=row["first_submission_id"],
            document_id=row["document_id"],
            invoice=invoice,
            raw_values=raw.model_dump(mode="json"),
            problems=normalisation.problems,
            extraction_method=raw.extraction_method,
            created_by=actor,
            field_evidence=None,
        )
        repo.insert_validation_results(
            conn,
            outcome.results,
            submission_id=None,
            invoice_id=invoice_id,
            revision_id=revision_id,
        )
        repo.set_current_revision(conn, invoice_id, revision_id, str(outcome.state))

        review_action_id = repo.insert_review_action(
            conn,
            invoice_id=invoice_id,
            actor=actor,
            action=CORRECT,
            reason=reason,
            from_revision_id=row["current_revision_id"],
            to_revision_id=revision_id,
        )
        repo.record_event(
            conn,
            event_type="reviewer_correction",
            entity_type="invoice",
            entity_id=invoice_id,
            actor=actor,
            details={
                "reason": reason,
                "from_revision": expected_revision_number,
                "to_revision": revision_number,
                "business_state": str(outcome.state),
                "failed_codes": list(outcome.failed_codes),
            },
        )

        if outcome.state is BusinessState.NEEDS_REVIEW:
            # Still failing after the correction. A new revision means a new
            # deduplication key, so this is announced: the reviewer needs to
            # know their edit did not resolve it.
            job_repo.enqueue_notification(
                conn,
                notification_type="review_required",
                dedupe_key=f"review_required:{invoice_id}:{revision_id}",
                invoice_id=invoice_id,
                payload={
                    "revision_number": revision_number,
                    "failed_codes": list(outcome.failed_codes),
                    "after_correction": True,
                },
            )

        return ReviewOutcome(
            invoice_id=invoice_id,
            action=CORRECT,
            actor=actor,
            reason=reason,
            state=outcome.state,
            revision_number=revision_number,
            failed_codes=outcome.failed_codes,
            results=outcome.results,
            review_action_id=review_action_id,
        )


def reject_invoice(
    conn: psycopg.Connection,
    invoice_id: UUID,
    *,
    expected_revision_number: int,
    actor: str,
    reason: str,
) -> ReviewOutcome:
    """Reject an invoice, with a reason.

    Rejection releases the supplier and invoice number: the partial unique
    index excludes rejected invoices, so a corrected document can be
    submitted later and accepted. The rejected record and its history stay.
    """
    from .db import repository as repo

    row = _load(conn, invoice_id)
    if not conn.autocommit:
        conn.rollback()
    _check_revision(row, expected_revision_number)
    if row["business_state"] == "rejected":
        raise ReviewNotApplicable("the invoice is already rejected")

    with conn.transaction():
        repo.set_business_state(conn, invoice_id, str(BusinessState.REJECTED))
        review_action_id = repo.insert_review_action(
            conn,
            invoice_id=invoice_id,
            actor=actor,
            action=REJECT,
            reason=reason,
            from_revision_id=row["current_revision_id"],
            to_revision_id=None,
        )
        repo.record_event(
            conn,
            event_type="invoice_rejected",
            entity_type="invoice",
            entity_id=invoice_id,
            actor=actor,
            details={"reason": reason, "revision": expected_revision_number},
        )

    return ReviewOutcome(
        invoice_id=invoice_id,
        action=REJECT,
        actor=actor,
        reason=reason,
        state=BusinessState.REJECTED,
        revision_number=expected_revision_number,
        review_action_id=review_action_id,
    )


def accept_exception(
    conn: psycopg.Connection,
    invoice_id: UUID,
    *,
    expected_revision_number: int,
    actor: str,
    reason: str,
) -> ReviewOutcome:
    """Accept an invoice into the register despite a failed check.

    The invoice ends in the same state as one the rules validated, because
    that is what acceptance means here, but the reports do **not** treat the
    two alike: `accepted_with_exception` marks it, and the straight-through
    rate excludes it. Overstating how much the rules handled would be the
    easiest number in this project to get quietly wrong.

    Only an invoice that is actually in review can be accepted this way.
    Accepting one that passed everything would record a decision nobody made.
    """
    from .db import repository as repo

    row = _load(conn, invoice_id)
    if not conn.autocommit:
        conn.rollback()
    _check_revision(row, expected_revision_number)
    if row["business_state"] != "needs_review":
        raise ReviewNotApplicable(
            f"only an invoice in review can be accepted as an exception; "
            f"this one is {row['business_state']}"
        )

    with conn.transaction():
        repo.set_business_state(conn, invoice_id, str(BusinessState.VALIDATED))
        review_action_id = repo.insert_review_action(
            conn,
            invoice_id=invoice_id,
            actor=actor,
            action=ACCEPT_EXCEPTION,
            reason=reason,
            from_revision_id=row["current_revision_id"],
            to_revision_id=None,
        )
        repo.record_event(
            conn,
            event_type="exception_accepted",
            entity_type="invoice",
            entity_id=invoice_id,
            actor=actor,
            details={
                "reason": reason,
                "revision": expected_revision_number,
                "note": (
                    "Accepted into the register by a reviewer despite a failed "
                    "check. Not the same as the checks passing."
                ),
            },
        )

    return ReviewOutcome(
        invoice_id=invoice_id,
        action=ACCEPT_EXCEPTION,
        actor=actor,
        reason=reason,
        state=BusinessState.VALIDATED,
        revision_number=expected_revision_number,
        review_action_id=review_action_id,
    )
