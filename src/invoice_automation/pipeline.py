"""From an arrival to a recorded outcome.

Two steps, deliberately separate:

:func:`record_arrival`
    Store the file, record the submission, open a queued job. Cheap, and it
    never fails on the document's contents, so a caller that uploads
    something unreadable still gets a receipt.

:func:`process_submission`
    Read the stored file, extract, validate, persist. Repeatable: a submission
    that has already reached an outcome is not processed again, and a failed
    attempt leaves a job row that can be retried without re-uploading.

That split is what makes orchestration possible. An orchestrator can be told
"submission X arrived", decide when to process it, and retry processing
without the document passing through it again. It is also why the authority
on what happened lives in PostgreSQL rather than in the orchestrator's run
history.

:func:`process_structured_submission` does both in one call for a caller that
has already parsed its values and has no file to store.

Validation and persistence happen inside one database transaction. Either the
revision, the rule outcomes and the events are all recorded, or none are. The
job row is deliberately outside it: a failed attempt must survive the
rollback of the work it was attempting, or there would be nothing to retry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from .config import RuleConfig, Settings, get_settings
from .models import RawInvoice, SubmissionInput, normalize_invoice
from .rules.base import RuleResult, ValidationContext
from .rules.engine import BusinessState, ValidationOutcome, run_rules
from .storage import resolve, store

#: Business states that can be written to ``submissions.outcome``.
_OUTCOME_FOR_STATE = {
    BusinessState.VALIDATED: "validated",
    BusinessState.NEEDS_REVIEW: "needs_review",
    BusinessState.DUPLICATE: "duplicate",
    BusinessState.UNSUPPORTED: "unsupported",
}

_STATE_FOR_OUTCOME = {value: key for key, value in _OUTCOME_FOR_STATE.items()}

#: Outcomes that mean processing is finished and must not be repeated.
_SETTLED_OUTCOMES = frozenset(_OUTCOME_FOR_STATE.values())


class ProcessingError(RuntimeError):
    """Processing failed for a technical reason, not a business one.

    A business problem with a document is a validation failure and a recorded
    outcome. This is the other kind: the file could not be read, the
    extractor raised, the database refused something. It leaves a retryable
    job behind.
    """

    def __init__(self, message: str, *, submission_id: UUID, job_status: str | None = None):
        super().__init__(message)
        self.submission_id = submission_id
        self.job_status = job_status


class SubmissionNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ArrivalResult:
    submission_id: UUID
    document_id: UUID
    job_id: UUID
    outcome: str
    storage_path: str
    file_sha256: str
    #: True when an identical file was already on disk from an earlier arrival.
    bytes_already_stored: bool = False
    #: True when this idempotency key had already been used, in which case the
    #: earlier submission is returned and nothing new was recorded.
    replayed: bool = False


@dataclass(frozen=True)
class ProcessingResult:
    submission_id: UUID
    state: BusinessState
    outcome: str
    invoice_id: UUID | None = None
    revision_number: int | None = None
    duplicate_of_submission_id: UUID | None = None
    invoice_created: bool = False
    #: True when this call returned an earlier recorded result unchanged,
    #: either because the idempotency key had been used or because the
    #: submission had already been processed.
    replayed: bool = False
    results: tuple[RuleResult, ...] = ()
    failed_codes: tuple[str, ...] = ()
    job_attempt: int | None = None

    @property
    def created_invoice(self) -> bool:
        return self.invoice_created


def _storage_root(settings: Settings | None) -> Path:
    return (settings or get_settings()).absolute_storage_root()


def _ensure_idle(conn: psycopg.Connection) -> None:
    """Leave a non-autocommit connection outside a transaction.

    A read opens an implicit transaction when autocommit is off, which would
    turn the following ``conn.transaction()`` block into a savepoint that
    commits nothing. See ``db.connection.connect``.
    """
    if not conn.autocommit:
        conn.rollback()


# ---------------------------------------------------------------------------
# Step one: the arrival
# ---------------------------------------------------------------------------


def record_arrival(
    conn: psycopg.Connection,
    *,
    content: bytes,
    original_file_name: str,
    source: str = "api_upload",
    idempotency_key: str | None = None,
    received_at: datetime | None = None,
    actor: str = "api",
    settings: Settings | None = None,
) -> ArrivalResult:
    """Store a document and record that it arrived.

    Does not look at the contents beyond storing them, so an unreadable or
    unsupported document is still recorded with a receipt the caller can
    quote. Deciding what it is comes later, in processing.
    """
    from .db import repository as repo

    existing = repo.find_submission_by_idempotency_key(conn, idempotency_key)
    _ensure_idle(conn)
    if existing is not None:
        repo.record_event(
            conn,
            event_type="submission_replayed",
            entity_type="submission",
            entity_id=existing["submission_id"],
            actor=actor,
            details={"idempotency_key": idempotency_key, "stage": "arrival"},
        )
        job = repo.get_job_for_submission(conn, existing["submission_id"])
        return ArrivalResult(
            submission_id=existing["submission_id"],
            document_id=existing["document_id"],
            job_id=job["job_id"] if job else None,
            outcome=existing["outcome"],
            storage_path=existing.get("storage_path") or "",
            file_sha256=existing.get("content_sha256") or "",
            replayed=True,
        )

    stored = store(
        content, file_name=original_file_name, root=_storage_root(settings)
    )

    with conn.transaction():
        document_id = repo.insert_document(
            conn,
            original_file_name=original_file_name,
            content_sha256=stored.sha256,
            storage_path=stored.storage_path,
            media_type=stored.media_type,
            byte_size=stored.byte_size,
            extraction_method=None,
            raw_payload=None,
        )
        submission_id = repo.insert_submission(
            conn,
            document_id=document_id,
            submission=SubmissionInput(
                source=source,
                original_file_name=original_file_name,
                file_sha256=stored.sha256,
                idempotency_key=idempotency_key,
                received_at=received_at or datetime.now(tz=timezone.utc),
                storage_path=stored.storage_path,
            ),
        )
        job = repo.enqueue_job(conn, submission_id)
        repo.record_event(
            conn,
            event_type="submission_received",
            entity_type="submission",
            entity_id=submission_id,
            actor=actor,
            details={
                "source": source,
                "file_sha256": stored.sha256,
                "original_file_name": original_file_name,
                "media_type": stored.media_type,
                "byte_size": stored.byte_size,
                "bytes_already_stored": stored.already_present,
            },
        )

    return ArrivalResult(
        submission_id=submission_id,
        document_id=document_id,
        job_id=job["job_id"],
        outcome="pending",
        storage_path=stored.storage_path,
        file_sha256=stored.sha256,
        bytes_already_stored=stored.already_present,
    )


# ---------------------------------------------------------------------------
# Step two: processing
# ---------------------------------------------------------------------------


def _extract(path: Path, media_type: str) -> tuple[RawInvoice, str | None, list[dict]]:
    """Turn a stored file into a ``RawInvoice``, however it is encoded."""
    if media_type == "application/pdf":
        # Imported here: pdfminer is slow to import and a caller processing
        # structured payloads should not pay for it.
        from .extraction import extract_invoice

        result = extract_invoice(path)
        return result.raw, result.raw_text, result.evidence_payload()
    if media_type == "application/json":
        return RawInvoice.model_validate(json.loads(path.read_bytes())), None, []
    raise ValueError(f"no extractor for media type {media_type!r}")


def process_submission(
    conn: psycopg.Connection,
    submission_id: UUID,
    *,
    config: RuleConfig | None = None,
    actor: str = "pipeline",
    settings: Settings | None = None,
) -> ProcessingResult:
    """Extract, validate and persist a submission that has already arrived.

    Safe to call again. A submission that already reached an outcome returns
    that outcome without being processed a second time, so a repeated trigger
    or a retry after a timeout cannot create a second invoice.
    """
    from .db import jobs as job_repo
    from .db import repository as repo

    settings = settings or get_settings()
    config = config or settings.rule_config()

    row = repo.get_submission_with_document(conn, submission_id)
    _ensure_idle(conn)
    if row is None:
        raise SubmissionNotFound(str(submission_id))

    if row["outcome"] in _SETTLED_OUTCOMES:
        # Already processed. Report what was recorded rather than doing it
        # again; this is what makes a repeated trigger harmless.
        return ProcessingResult(
            submission_id=submission_id,
            state=_STATE_FOR_OUTCOME[row["outcome"]],
            outcome=row["outcome"],
            invoice_id=row["invoice_id"],
            duplicate_of_submission_id=row["duplicate_of_submission_id"],
            replayed=True,
        )

    # Claim the job, with a lease. Outside the work transaction on purpose: a
    # failed attempt has to survive the rollback of the work it attempted, or
    # there would be nothing left to retry.
    job = job_repo.claim_job(
        conn, submission_id, lease_seconds=settings.job_lease_seconds
    )

    submission = SubmissionInput(
        source=row["source"],
        original_file_name=row["original_file_name"],
        file_sha256=row["content_sha256"],
        idempotency_key=row["idempotency_key"],
        received_at=row["received_at"],
        storage_path=row["storage_path"],
    )

    try:
        path = resolve(row["storage_path"], _storage_root(settings))
        raw, raw_text, field_evidence = _extract(path, row["media_type"])

        with conn.transaction():
            repo.update_document_extraction(
                conn,
                row["document_id"],
                raw_payload=raw,
                raw_text=raw_text,
                extraction_method=raw.extraction_method,
            )
            return _validate_and_persist(
                conn,
                raw=raw,
                submission=submission,
                submission_id=submission_id,
                document_id=row["document_id"],
                job=job,
                config=config,
                actor=actor,
                field_evidence=field_evidence,
            )
    except Exception as exc:
        # The work rolled back; the job and the failure record do not.
        status = repo.fail_job(conn, job["job_id"], error=f"{type(exc).__name__}: {exc}")
        repo.update_submission_outcome(conn, submission_id, outcome="failed")
        repo.record_event(
            conn,
            event_type="processing_failed",
            entity_type="submission",
            entity_id=submission_id,
            actor=actor,
            details={
                "error": f"{type(exc).__name__}: {exc}",
                "job_status": status["status"],
                "attempt": status["attempt_count"],
                "max_attempts": status["max_attempts"],
            },
        )
        # One notification per failed attempt: a retry that fails again is new
        # information, but the same attempt reported twice is not.
        job_repo.enqueue_notification(
            conn,
            notification_type="processing_failed",
            dedupe_key=(
                f"processing_failed:{submission_id}:attempt-{status['attempt_count']}"
            ),
            submission_id=submission_id,
            payload={
                "error": f"{type(exc).__name__}: {exc}",
                "job_status": status["status"],
                "attempt": status["attempt_count"],
            },
        )
        raise ProcessingError(
            f"processing submission {submission_id} failed: {exc}",
            submission_id=submission_id,
            job_status=status["status"],
        ) from exc


def process_structured_submission(
    conn: psycopg.Connection,
    raw: RawInvoice,
    submission: SubmissionInput,
    *,
    config: RuleConfig | None = None,
    actor: str = "pipeline",
    raw_text: str | None = None,
    field_evidence: list[dict[str, Any]] | None = None,
    media_type: str = "application/json",
    byte_size: int | None = None,
) -> ProcessingResult:
    """Record and process one arrival whose values are already parsed.

    For a caller that holds values rather than a file: it records the arrival
    and processes it in one go. ``record_arrival`` plus
    :func:`process_submission` is the path for a document.
    """
    from .db import jobs as job_repo
    from .db import repository as repo

    settings = get_settings()
    config = config or settings.rule_config()

    existing = repo.find_submission_by_idempotency_key(conn, submission.idempotency_key)
    _ensure_idle(conn)

    if existing is not None:
        repo.record_event(
            conn,
            event_type="submission_replayed",
            entity_type="submission",
            entity_id=existing["submission_id"],
            actor=actor,
            details={"idempotency_key": submission.idempotency_key},
        )
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
            media_type=media_type,
            byte_size=byte_size,
            extraction_method=raw.extraction_method,
            raw_payload=raw,
            raw_text=raw_text,
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
        job = job_repo.claim_job(
            conn, submission_id, lease_seconds=settings.job_lease_seconds
        )

        return _validate_and_persist(
            conn,
            raw=raw,
            submission=submission,
            submission_id=submission_id,
            document_id=document_id,
            job=job,
            config=config,
            actor=actor,
            field_evidence=field_evidence,
        )


# ---------------------------------------------------------------------------
# The common core: rules, persistence, outcome
# ---------------------------------------------------------------------------


def _validate_and_persist(
    conn: psycopg.Connection,
    *,
    raw: RawInvoice,
    submission: SubmissionInput,
    submission_id: UUID,
    document_id: UUID,
    job: dict[str, Any],
    config: RuleConfig,
    actor: str,
    field_evidence: list[dict[str, Any]] | None,
) -> ProcessingResult:
    """Validate an arrival and write everything it produced.

    Must be called inside a transaction. Identical for a document and for a
    structured payload, which is the property the integration tests assert.
    """
    from .db import jobs as job_repo
    from .db import repository as repo

    normalisation = normalize_invoice(raw, config)
    invoice = normalisation.normalized

    # --- Lookups, done once, before any rule runs --------------------------
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

    # --- An identical file is linked, not reprocessed ----------------------
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
        # One notification per repeat arrival, keyed to the arrival, so a
        # reprocessed submission cannot announce the same duplicate twice.
        job_repo.enqueue_notification(
            conn,
            notification_type="duplicate_received",
            dedupe_key=f"duplicate_received:{submission_id}",
            invoice_id=file_duplicate.invoice_id,
            submission_id=submission_id,
            payload={
                "file_sha256": submission.file_sha256,
                "earlier_submission_id": str(file_duplicate.submission_id),
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

    # --- Canonical invoice -------------------------------------------------
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
        field_evidence=field_evidence,
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

    if outcome.state is BusinessState.NEEDS_REVIEW:
        # Keyed to the revision, not the clock: processing the same revision
        # again announces nothing new, while a genuinely new revision (a
        # second document claiming this invoice number, say) is new
        # information and does notify. Written in this transaction with the
        # work, so a rolled-back invoice cannot have told anybody about
        # itself; delivery happens separately from the outbox.
        job_repo.enqueue_notification(
            conn,
            notification_type="review_required",
            dedupe_key=f"review_required:{invoice_id}:{revision_id}",
            invoice_id=invoice_id,
            submission_id=submission_id,
            payload={
                "invoice_number": number,
                "revision_number": revision_number,
                "failed_codes": list(outcome.failed_codes),
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
