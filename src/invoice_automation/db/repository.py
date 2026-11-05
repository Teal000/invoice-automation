"""All SQL for the processing pipeline.

Every function takes a connection and does one thing, so the pipeline reads as
a sequence of steps and the SQL stays inspectable. Nothing here decides
business outcomes; that is the rule engine's job.
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any, Iterable
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from ..models import (
    FieldProblem,
    NormalizedInvoice,
    RawInvoice,
    SubmissionInput,
)
from ..rules.base import (
    DuplicateMatch,
    PurchaseOrderRecord,
    RuleResult,
    SupplierRecord,
)

UNRESOLVED_SUPPLIER_PREFIX = "unresolved:"


def supplier_key(
    supplier: SupplierRecord | None, supplier_reference_normalized: str | None
) -> str:
    """Identity used for duplicate detection.

    A resolved supplier contributes its id. An unresolved one contributes its
    normalised reference under an explicit prefix, so two documents from the
    same unknown supplier still count as the same business invoice while never
    colliding with a real supplier id.
    """
    if supplier is not None:
        return str(supplier.supplier_id)
    if supplier_reference_normalized:
        return f"{UNRESOLVED_SUPPLIER_PREFIX}{supplier_reference_normalized}"
    return f"{UNRESOLVED_SUPPLIER_PREFIX}(none)"


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


def upsert_supplier(
    conn: psycopg.Connection,
    *,
    name: str,
    reference: str,
    reference_normalized: str,
    permitted_currency: str,
    is_active: bool = True,
) -> UUID:
    row = conn.execute(
        """
        INSERT INTO suppliers (name, reference, reference_normalized,
                               permitted_currency, is_active)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (reference_normalized) DO UPDATE
            SET name = EXCLUDED.name,
                reference = EXCLUDED.reference,
                permitted_currency = EXCLUDED.permitted_currency,
                is_active = EXCLUDED.is_active
        RETURNING supplier_id
        """,
        (name, reference, reference_normalized, permitted_currency, is_active),
    ).fetchone()
    return row["supplier_id"]


def upsert_purchase_order(
    conn: psycopg.Connection,
    *,
    po_number: str,
    po_number_normalized: str,
    supplier_id: UUID,
    currency: str,
    expected_amount: Decimal,
    state: str = "open",
) -> UUID:
    row = conn.execute(
        """
        INSERT INTO purchase_orders (po_number, po_number_normalized, supplier_id,
                                     currency, expected_amount, state)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (po_number_normalized) DO UPDATE
            SET supplier_id = EXCLUDED.supplier_id,
                currency = EXCLUDED.currency,
                expected_amount = EXCLUDED.expected_amount,
                state = EXCLUDED.state
        RETURNING purchase_order_id
        """,
        (po_number, po_number_normalized, supplier_id, currency, expected_amount, state),
    ).fetchone()
    return row["purchase_order_id"]


def get_supplier(
    conn: psycopg.Connection, reference_normalized: str | None
) -> SupplierRecord | None:
    if not reference_normalized:
        return None
    row = conn.execute(
        """
        SELECT supplier_id, name, reference_normalized, permitted_currency, is_active
        FROM suppliers WHERE reference_normalized = %s
        """,
        (reference_normalized,),
    ).fetchone()
    if row is None:
        return None
    return SupplierRecord(
        supplier_id=row["supplier_id"],
        name=row["name"],
        reference_normalized=row["reference_normalized"],
        permitted_currency=row["permitted_currency"].strip(),
        is_active=row["is_active"],
    )


def get_purchase_order(
    conn: psycopg.Connection, po_number_normalized: str | None
) -> PurchaseOrderRecord | None:
    if not po_number_normalized:
        return None
    row = conn.execute(
        """
        SELECT purchase_order_id, po_number_normalized, supplier_id, currency,
               expected_amount, state
        FROM purchase_orders WHERE po_number_normalized = %s
        """,
        (po_number_normalized,),
    ).fetchone()
    if row is None:
        return None
    return PurchaseOrderRecord(
        purchase_order_id=row["purchase_order_id"],
        po_number_normalized=row["po_number_normalized"],
        supplier_id=row["supplier_id"],
        currency=row["currency"].strip(),
        expected_amount=row["expected_amount"],
        state=row["state"],
    )


# ---------------------------------------------------------------------------
# Duplicate lookups
# ---------------------------------------------------------------------------


def find_file_duplicate(
    conn: psycopg.Connection, content_sha256: str, *, exclude_submission_id: UUID | None = None
) -> DuplicateMatch | None:
    """The earliest submission that carried a file with this hash."""
    row = conn.execute(
        """
        SELECT s.submission_id, s.invoice_id, s.received_at
        FROM submissions s
        JOIN documents d ON d.document_id = s.document_id
        WHERE d.content_sha256 = %s
          AND (%s::uuid IS NULL OR s.submission_id <> %s::uuid)
        ORDER BY s.received_at, s.submission_id
        LIMIT 1
        """,
        (content_sha256, exclude_submission_id, exclude_submission_id),
    ).fetchone()
    if row is None:
        return None
    return DuplicateMatch(
        kind="file_hash",
        submission_id=row["submission_id"],
        invoice_id=row["invoice_id"],
        matched_value=content_sha256,
        first_seen_at=row["received_at"],
    )


def find_business_duplicate(
    conn: psycopg.Connection, key: str, invoice_number_normalized: str | None
) -> DuplicateMatch | None:
    """An existing canonical invoice for this supplier and invoice number."""
    if not invoice_number_normalized:
        return None
    row = conn.execute(
        """
        SELECT invoice_id, invoice_number_normalized, created_at
        FROM invoices
        WHERE supplier_key = %s
          AND invoice_number_normalized = %s
          AND business_state <> 'rejected'
        LIMIT 1
        """,
        (key, invoice_number_normalized),
    ).fetchone()
    if row is None:
        return None
    return DuplicateMatch(
        kind="supplier_invoice_number",
        invoice_id=row["invoice_id"],
        matched_value=row["invoice_number_normalized"],
        first_seen_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Arrivals
# ---------------------------------------------------------------------------


def find_submission_by_idempotency_key(
    conn: psycopg.Connection, key: str | None
) -> dict[str, Any] | None:
    if not key:
        return None
    return conn.execute(
        "SELECT * FROM submissions WHERE idempotency_key = %s", (key,)
    ).fetchone()


def insert_document(
    conn: psycopg.Connection,
    *,
    original_file_name: str,
    content_sha256: str,
    storage_path: str | None,
    media_type: str | None,
    byte_size: int | None,
    extraction_method: str | None,
    raw_payload: RawInvoice | dict[str, Any] | None,
    raw_text: str | None = None,
) -> UUID:
    payload = (
        raw_payload.model_dump(mode="json")
        if isinstance(raw_payload, RawInvoice)
        else raw_payload
    )
    row = conn.execute(
        """
        INSERT INTO documents (original_file_name, storage_path, content_sha256,
                               byte_size, media_type, extraction_method,
                               raw_payload, raw_text)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING document_id
        """,
        (
            original_file_name,
            storage_path,
            content_sha256,
            byte_size,
            media_type,
            extraction_method,
            Jsonb(payload) if payload is not None else None,
            raw_text,
        ),
    ).fetchone()
    return row["document_id"]


def insert_submission(
    conn: psycopg.Connection,
    *,
    document_id: UUID,
    submission: SubmissionInput,
) -> UUID:
    row = conn.execute(
        """
        INSERT INTO submissions (document_id, source, idempotency_key, received_at)
        VALUES (%s, %s, %s, %s)
        RETURNING submission_id
        """,
        (
            document_id,
            submission.source,
            submission.idempotency_key,
            submission.received_at,
        ),
    ).fetchone()
    return row["submission_id"]


def update_submission_outcome(
    conn: psycopg.Connection,
    submission_id: UUID,
    *,
    outcome: str,
    invoice_id: UUID | None = None,
    duplicate_of_submission_id: UUID | None = None,
    notes: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE submissions
           SET outcome = %s,
               invoice_id = COALESCE(%s, invoice_id),
               duplicate_of_submission_id = COALESCE(%s, duplicate_of_submission_id),
               notes = COALESCE(%s, notes)
         WHERE submission_id = %s
        """,
        (outcome, invoice_id, duplicate_of_submission_id, notes, submission_id),
    )


# ---------------------------------------------------------------------------
# Invoices and revisions
# ---------------------------------------------------------------------------


def insert_or_get_invoice(
    conn: psycopg.Connection,
    *,
    key: str,
    invoice_number: str,
    invoice_number_normalized: str,
    supplier_id: UUID | None,
    first_submission_id: UUID,
) -> tuple[UUID, bool]:
    """Insert a canonical invoice, or return the existing one.

    The partial unique index on (supplier_key, invoice_number_normalized) is
    what makes this safe: if two submissions race, one insert conflicts and
    that caller continues with the existing invoice instead of creating a
    second accepted record. Returns ``(invoice_id, created)``.
    """
    row = conn.execute(
        """
        INSERT INTO invoices (supplier_key, invoice_number, invoice_number_normalized,
                              supplier_id, business_state, first_submission_id)
        VALUES (%s, %s, %s, %s, 'processing', %s)
        ON CONFLICT (supplier_key, invoice_number_normalized)
            WHERE business_state <> 'rejected'
            DO NOTHING
        RETURNING invoice_id
        """,
        (
            key,
            invoice_number,
            invoice_number_normalized,
            supplier_id,
            first_submission_id,
        ),
    ).fetchone()
    if row is not None:
        return row["invoice_id"], True

    existing = conn.execute(
        """
        SELECT invoice_id FROM invoices
        WHERE supplier_key = %s AND invoice_number_normalized = %s
          AND business_state <> 'rejected'
        """,
        (key, invoice_number_normalized),
    ).fetchone()
    if existing is None:  # pragma: no cover - only on a concurrent rejection
        raise RuntimeError("invoice conflicted but could not be read back")
    return existing["invoice_id"], False


def next_revision_number(conn: psycopg.Connection, invoice_id: UUID) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(max(revision_number), 0) + 1 AS next
        FROM invoice_revisions WHERE invoice_id = %s
        """,
        (invoice_id,),
    ).fetchone()
    return row["next"]


def insert_revision(
    conn: psycopg.Connection,
    *,
    invoice_id: UUID,
    revision_number: int,
    source: str,
    submission_id: UUID | None,
    document_id: UUID | None,
    invoice: NormalizedInvoice,
    raw_values: dict[str, Any],
    problems: Iterable[FieldProblem],
    extraction_method: str | None,
    created_by: str,
) -> UUID:
    row = conn.execute(
        """
        INSERT INTO invoice_revisions (
            invoice_id, revision_number, source, submission_id, document_id,
            document_type, invoice_number, invoice_number_normalized,
            supplier_reference, supplier_reference_normalized, supplier_name,
            invoice_date, due_date, currency,
            purchase_order_number, purchase_order_number_normalized,
            subtotal, tax_total, total,
            raw_values, field_problems, extraction_method, created_by)
        VALUES (%s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s)
        RETURNING revision_id
        """,
        (
            invoice_id,
            revision_number,
            source,
            submission_id,
            document_id,
            invoice.document_type,
            invoice.invoice_number,
            invoice.invoice_number_normalized,
            invoice.supplier_reference,
            invoice.supplier_reference_normalized,
            invoice.supplier_name,
            invoice.invoice_date,
            invoice.due_date,
            invoice.currency,
            invoice.purchase_order_number,
            invoice.purchase_order_number_normalized,
            invoice.subtotal,
            invoice.tax_total,
            invoice.total,
            Jsonb(raw_values),
            Jsonb([asdict(problem) for problem in problems]),
            extraction_method,
            created_by,
        ),
    ).fetchone()
    revision_id = row["revision_id"]

    for line in invoice.lines:
        conn.execute(
            """
            INSERT INTO invoice_lines (revision_id, line_number, description,
                                       quantity, unit_price, line_amount)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                revision_id,
                line.line_number,
                line.description,
                line.quantity,
                line.unit_price,
                line.line_amount,
            ),
        )
    return revision_id


def set_current_revision(
    conn: psycopg.Connection,
    invoice_id: UUID,
    revision_id: UUID,
    business_state: str,
) -> None:
    conn.execute(
        """
        UPDATE invoices
           SET current_revision_id = %s,
               business_state = %s,
               updated_at = now()
         WHERE invoice_id = %s
        """,
        (revision_id, business_state, invoice_id),
    )


# ---------------------------------------------------------------------------
# Rule outcomes
# ---------------------------------------------------------------------------


def insert_validation_results(
    conn: psycopg.Connection,
    results: Iterable[RuleResult],
    *,
    submission_id: UUID | None,
    invoice_id: UUID | None,
    revision_id: UUID | None,
) -> int:
    rows = [
        (
            submission_id,
            invoice_id,
            revision_id,
            result.code,
            str(result.status),
            str(result.severity),
            str(result.disposition),
            result.message,
            result.field_name,
            result.expected,
            result.observed,
            Jsonb(result.details or {}),
        )
        for result in results
    ]
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO validation_results (
                submission_id, invoice_id, revision_id, code, status, severity,
                disposition, message, field_name, expected_value, observed_value,
                details)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
    return len(rows)


# ---------------------------------------------------------------------------
# Jobs and events
# ---------------------------------------------------------------------------


def start_job(
    conn: psycopg.Connection,
    submission_id: UUID,
    *,
    job_type: str = "process_submission",
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Create or claim the one job for this submission and mark it running.

    A retry increments ``attempt_count`` on the same row rather than creating a
    second job, which is what keeps a retried submission from producing a
    second invoice.
    """
    row = conn.execute(
        """
        INSERT INTO processing_jobs (submission_id, job_type, status, attempt_count,
                                     max_attempts, started_at, updated_at)
        VALUES (%s, %s, 'running', 1, %s, now(), now())
        ON CONFLICT (submission_id, job_type) DO UPDATE
            SET status = 'running',
                attempt_count = processing_jobs.attempt_count + 1,
                started_at = now(),
                updated_at = now()
        RETURNING job_id, status, attempt_count, max_attempts
        """,
        (submission_id, job_type, max_attempts),
    ).fetchone()
    return row


def finish_job(
    conn: psycopg.Connection,
    job_id: UUID,
    *,
    status: str,
    last_error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE processing_jobs
           SET status = %s,
               last_error = %s,
               finished_at = now(),
               updated_at = now()
         WHERE job_id = %s
        """,
        (status, last_error, job_id),
    )


def record_event(
    conn: psycopg.Connection,
    *,
    event_type: str,
    entity_type: str,
    entity_id: UUID | None,
    actor: str,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_events (event_type, entity_type, entity_id, actor, details)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (event_type, entity_type, entity_id, actor, Jsonb(details or {})),
    )


# ---------------------------------------------------------------------------
# Reads for the CLI
# ---------------------------------------------------------------------------


def get_invoice_register(
    conn: psycopg.Connection, *, state: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT * FROM v_invoice_register
        WHERE (%s::text IS NULL OR business_state = %s::text)
        ORDER BY first_recorded_at DESC
        LIMIT %s
        """,
        (state, state, limit),
    ).fetchall()


def get_processing_overview(conn: psycopg.Connection) -> dict[str, Any]:
    return conn.execute("SELECT * FROM v_processing_overview").fetchone()


def get_exception_summary(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT * FROM v_exception_summary ORDER BY invoices_affected DESC, code"
    ).fetchall()


def get_invoice_detail(conn: psycopg.Connection, invoice_id: UUID) -> dict[str, Any] | None:
    invoice = conn.execute(
        "SELECT * FROM v_invoice_register WHERE invoice_id = %s", (invoice_id,)
    ).fetchone()
    if invoice is None:
        return None
    invoice["lines"] = conn.execute(
        """
        SELECT l.line_number, l.description, l.quantity, l.unit_price, l.line_amount
        FROM invoice_lines l
        JOIN invoices i ON i.current_revision_id = l.revision_id
        WHERE i.invoice_id = %s
        ORDER BY l.line_number
        """,
        (invoice_id,),
    ).fetchall()
    invoice["checks"] = conn.execute(
        """
        SELECT code, status, severity, field_name, expected_value, observed_value, message
        FROM validation_results
        WHERE invoice_id = %s
        ORDER BY evaluated_at, code
        """,
        (invoice_id,),
    ).fetchall()
    invoice["revisions"] = conn.execute(
        """
        SELECT revision_number, source, created_by, created_at
        FROM invoice_revisions WHERE invoice_id = %s ORDER BY revision_number
        """,
        (invoice_id,),
    ).fetchall()
    invoice["events"] = conn.execute(
        """
        SELECT occurred_at, event_type, entity_type, actor, details
        FROM audit_events
        WHERE (entity_type = 'invoice' AND entity_id = %s)
           OR (entity_type = 'submission' AND entity_id IN (
                   SELECT submission_id FROM submissions WHERE invoice_id = %s))
        ORDER BY occurred_at, event_id
        """,
        (invoice_id, invoice_id),
    ).fetchall()
    return invoice
