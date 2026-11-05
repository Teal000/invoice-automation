"""The processing pipeline: one arrival from input to recorded outcome.

This is the vertical slice. A structured payload arrives, it is recorded,
normalised, checked against the deterministic rules, and persisted with its
rule outcomes, revision history and event trail. Extraction from PDFs and
orchestration by n8n plug in at the two marked seams; nothing else about this
flow needs to change when they do.

Everything happens inside one database transaction. Either the submission,
its invoice revision, its rule outcomes and its events are all recorded, or
none of them are.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg

from .config import RuleConfig, get_settings
from .models import RawInvoice, SubmissionInput, normalize_invoice
from .rules.base import RuleResult, ValidationContext
from .rules.engine import BusinessState, ValidationOutcome, run_rules

#: Business states that can be written to ``submissions.outcome``.
_OUTCOME_FOR_STATE = {
    BusinessState.VALIDATED: "validated",
    BusinessState.NEEDS_REVIEW: "needs_review",
    BusinessState.DUPLICATE: "duplicate",
    BusinessState.UNSUPPORTED: "unsupported",
}

_STATE_FOR_OUTCOME = {value: key for key, value in _OUTCOME_FOR_STATE.items()}


@dataclass(frozen=True)
class ProcessingResult:
    submission_id: UUID
    state: BusinessState
    outcome: str
    invoice_id: UUID | None = None
    revision_number: int | None = None
    duplicate_of_submission_id: UUID | None = None
    invoice_created: bool = False
    #: True when this call returned an earlier recorded result unchanged
    #: because the same idempotency key had already been processed.
    replayed: bool = False
    results: tuple[RuleResult, ...] = ()
    failed_codes: tuple[str, ...] = ()
    job_attempt: int | None = None

    @property
    def created_invoice(self) -> bool:
        return self.invoice_created


def process_structured_submission(
    conn: psycopg.Connection,
    raw: RawInvoice,
    submission: SubmissionInput,
    *,
    config: RuleConfig | None = None,
    actor: str = "pipeline",
) -> ProcessingResult:
    """Record, validate and persist one structured arrival.

    ``raw`` is the extracted payload. In this version it comes from a
    structured fixture; the PDF extractor will produce the same type, which is
    why validation can be developed and tested without OCR.
    """
    from .db import repository as repo

    config = config or get_settings().rule_config()

    # --- Idempotency: the same key never does the work twice. --------------
    existing = repo.find_submission_by_idempotency_key(conn, submission.idempotency_key)
    if existing is not None:
        repo.record_event(
            conn,
            event_type="submission_replayed",
            entity_type="submission",
            entity_id=existing["submission_id"],
            actor=actor,
            details={"idempotency_key": submission.idempotency_key},
        )
        conn.commit()
        outcome = existing["outcome"]
        return ProcessingResult(
            submission_id=existing["submission_id"],
            state=_STATE_FOR_OUTCOME.get(outcome, BusinessState.RECEIVED),
            outcome=outcome,
            invoice_id=existing["invoice_id"],
            duplicate_of_submission_id=existing["duplicate_of_submission_id"],
            replayed=True,
        )

    with conn.transaction():
        document_id = repo.insert_document(
            conn,
            original_file_name=submission.original_file_name,
            content_sha256=submission.file_sha256,
            storage_path=submission.storage_path,
            media_type="application/json",
            byte_size=None,
            extraction_method=raw.extraction_method,
            raw_payload=raw,
        )
        submission_id = repo.insert_submission(
            conn, document_id=document_id, submission=submission
        )
        repo.record_event(
            conn,
            event_type="submission_received",
            entity_type="submission",
            entity_id=submission_id,
            actor=actor,
            details={
                "source": submission.source,
                "file_sha256": submission.file_sha256,
                "original_file_name": submission.original_file_name,
            },
        )

        job = repo.start_job(conn, submission_id)

        # --- Normalisation (seam: a PDF extractor replaces the input) ------
        normalisation = normalize_invoice(raw, config)
        invoice = normalisation.normalized

        # --- Lookups, done once, before any rule runs ----------------------
        supplier = repo.get_supplier(conn, invoice.supplier_reference_normalized)
        purchase_order = repo.get_purchase_order(
            conn, invoice.purchase_order_number_normalized
        )
        key = repo.supplier_key(supplier, invoice.supplier_reference_normalized)
        file_duplicate = repo.find_file_duplicate(
            conn, submission.file_sha256, exclude_submission_id=submission_id
        )
        business_duplicate = repo.find_business_duplicate(
            conn, key, invoice.invoice_number_normalized
        )

        outcome: ValidationOutcome = run_rules(
            ValidationContext(
                invoice=invoice,
                submission=submission,
                config=config,
                problems=normalisation.problems,
                supplier=supplier,
                purchase_order=purchase_order,
                file_duplicate=file_duplicate,
                business_duplicate=business_duplicate,
            )
        )

        # --- An identical file is linked, not reprocessed ------------------
        if outcome.state is BusinessState.DUPLICATE and file_duplicate is not None:
            repo.insert_validation_results(
                conn,
                outcome.results,
                submission_id=submission_id,
                invoice_id=file_duplicate.invoice_id,
                revision_id=None,
            )
            repo.update_submission_outcome(
                conn,
                submission_id,
                outcome="duplicate",
                invoice_id=file_duplicate.invoice_id,
                duplicate_of_submission_id=file_duplicate.submission_id,
                notes="Identical file already received; linked to the earlier submission.",
            )
            repo.record_event(
                conn,
                event_type="duplicate_file_linked",
                entity_type="submission",
                entity_id=submission_id,
                actor=actor,
                details={
                    "earlier_submission_id": str(file_duplicate.submission_id),
                    "file_sha256": submission.file_sha256,
                },
            )
            repo.finish_job(conn, job["job_id"], status="succeeded")
            return ProcessingResult(
                submission_id=submission_id,
                state=BusinessState.DUPLICATE,
                outcome="duplicate",
                invoice_id=file_duplicate.invoice_id,
                duplicate_of_submission_id=file_duplicate.submission_id,
                results=outcome.results,
                failed_codes=outcome.failed_codes,
                job_attempt=job["attempt_count"],
            )

        # --- Canonical invoice --------------------------------------------
        if invoice.invoice_number_normalized is None:
            # No usable invoice number. A placeholder keyed to this submission
            # keeps the record visible without merging it with any other
            # unidentified document.
            number = invoice.invoice_number or "(absent)"
            number_normalized = f"UNIDENTIFIED-SUBMISSION:{submission_id}"
        else:
            number = invoice.invoice_number or invoice.invoice_number_normalized
            number_normalized = invoice.invoice_number_normalized

        invoice_id, created = repo.insert_or_get_invoice(
            conn,
            key=key,
            invoice_number=number,
            invoice_number_normalized=number_normalized,
            supplier_id=supplier.supplier_id if supplier else None,
            first_submission_id=submission_id,
        )

        revision_number = repo.next_revision_number(conn, invoice_id)
        revision_id = repo.insert_revision(
            conn,
            invoice_id=invoice_id,
            revision_number=revision_number,
            source="extraction",
            submission_id=submission_id,
            document_id=document_id,
            invoice=invoice,
            raw_values=raw.model_dump(mode="json"),
            problems=normalisation.problems,
            extraction_method=raw.extraction_method,
            created_by=actor,
        )

        repo.insert_validation_results(
            conn,
            outcome.results,
            submission_id=submission_id,
            invoice_id=invoice_id,
            revision_id=revision_id,
        )

        submission_outcome = _OUTCOME_FOR_STATE[outcome.state]
        repo.set_current_revision(conn, invoice_id, revision_id, str(outcome.state))
        repo.update_submission_outcome(
            conn, submission_id, outcome=submission_outcome, invoice_id=invoice_id
        )

        repo.record_event(
            conn,
            event_type="invoice_created" if created else "invoice_revision_recorded",
            entity_type="invoice",
            entity_id=invoice_id,
            actor=actor,
            details={
                "revision_number": revision_number,
                "submission_id": str(submission_id),
                "business_state": str(outcome.state),
            },
        )
        repo.record_event(
            conn,
            event_type="validation_completed",
            entity_type="invoice",
            entity_id=invoice_id,
            actor=actor,
            details={
                "business_state": str(outcome.state),
                "failed_codes": list(outcome.failed_codes),
                "checks_run": len(outcome.results),
            },
        )
        repo.finish_job(conn, job["job_id"], status="succeeded")

        return ProcessingResult(
            submission_id=submission_id,
            state=outcome.state,
            outcome=submission_outcome,
            invoice_id=invoice_id,
            revision_number=revision_number,
            invoice_created=created,
            results=outcome.results,
            failed_codes=outcome.failed_codes,
            job_attempt=job["attempt_count"],
        )
