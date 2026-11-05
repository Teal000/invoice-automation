"""Job leases, recovery candidates and the notification outbox.

Kept apart from ``repository.py`` because these answer a different question.
That module reads and writes invoice data; this one is about the machinery
that gets work done and does not lose it: which job may be claimed, which has
been abandoned, and what has already been announced.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


def claim_job(
    conn: psycopg.Connection,
    submission_id: UUID,
    *,
    job_type: str = "process_submission",
    max_attempts: int = 3,
    lease_seconds: int = 300,
) -> dict[str, Any]:
    """Mark the submission's job running, with a lease, and count the attempt.

    Always the same row, by the unique constraint on
    (submission_id, job_type): a retry increments an attempt rather than
    creating a second job, so a retried submission cannot produce a second
    invoice. The lease is what later tells a recovery sweep that somebody
    claimed this work and then vanished.
    """
    return conn.execute(
        """
        INSERT INTO processing_jobs (submission_id, job_type, status, attempt_count,
                                     max_attempts, started_at, updated_at,
                                     lease_expires_at)
        VALUES (%(submission)s, %(type)s, 'running', 1, %(max)s, now(), now(),
                now() + make_interval(secs => %(lease)s))
        ON CONFLICT (submission_id, job_type) DO UPDATE
            SET status = 'running',
                attempt_count = processing_jobs.attempt_count + 1,
                started_at = now(),
                updated_at = now(),
                lease_expires_at = now() + make_interval(secs => %(lease)s)
        RETURNING job_id, status, attempt_count, max_attempts, lease_expires_at
        """,
        {
            "submission": submission_id,
            "type": job_type,
            "max": max_attempts,
            "lease": lease_seconds,
        },
    ).fetchone()


def find_recoverable_jobs(
    conn: psycopg.Connection,
    *,
    limit: int = 20,
    queued_grace_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Jobs a recovery sweep should pick up, each with the reason it qualifies.

    Three kinds, and the reason comes back so a sweep can report what it found
    instead of silently retrying:

    ``retry_pending``
        An attempt failed and attempts remain.
    ``lease_expired``
        Still marked running after the lease elapsed, so whoever claimed it is
        gone. Safe to take, because processing is idempotent.
    ``queued_too_long``
        Recorded but never triggered. Without this, an arrival nobody called
        process for would sit unnoticed for ever.

    Jobs whose attempts are spent are excluded: those are ``failed`` and need
    a person, not another automatic attempt. Submissions that already reached
    a business outcome are excluded too, however their job row looks.
    """
    return conn.execute(
        """
        SELECT j.job_id, j.submission_id, j.status, j.attempt_count,
               j.max_attempts, j.lease_expires_at, j.last_error, j.created_at,
               CASE
                   WHEN j.status = 'retry_pending' THEN 'retry_pending'
                   WHEN j.status = 'running' THEN 'lease_expired'
                   ELSE 'queued_too_long'
               END AS reason
        FROM processing_jobs j
        JOIN submissions s ON s.submission_id = j.submission_id
        WHERE j.attempt_count < j.max_attempts
          AND s.outcome NOT IN ('validated', 'needs_review', 'duplicate', 'unsupported')
          AND (
                j.status = 'retry_pending'
             OR (j.status = 'running' AND j.lease_expires_at < now())
             OR (j.status = 'queued'
                 AND j.created_at < now() - make_interval(secs => %(grace)s))
          )
        ORDER BY j.created_at
        LIMIT %(limit)s
        """,
        {"grace": queued_grace_seconds, "limit": limit},
    ).fetchall()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def enqueue_notification(
    conn: psycopg.Connection,
    *,
    notification_type: str,
    dedupe_key: str,
    invoice_id: UUID | None = None,
    submission_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[UUID | None, bool]:
    """Queue a notification once. Returns ``(id, created)``.

    The unique ``dedupe_key`` does the work: a second call with the same key
    inserts nothing and reports ``created=False``. That is what stops a
    reprocessed submission from telling a reviewer twice, and it holds even if
    two workers try at the same instant, because the database decides.
    """
    row = conn.execute(
        """
        INSERT INTO notification_outbox (notification_type, dedupe_key, invoice_id,
                                         submission_id, payload)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (dedupe_key) DO NOTHING
        RETURNING notification_id
        """,
        (
            notification_type,
            dedupe_key,
            invoice_id,
            submission_id,
            Jsonb(payload or {}),
        ),
    ).fetchone()
    if row is not None:
        return row["notification_id"], True

    existing = conn.execute(
        "SELECT notification_id FROM notification_outbox WHERE dedupe_key = %s",
        (dedupe_key,),
    ).fetchone()
    return (existing["notification_id"] if existing else None), False


def list_notifications(
    conn: psycopg.Connection, *, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT notification_id, notification_type, dedupe_key, invoice_id,
               submission_id, status, channel, payload, attempt_count,
               last_error, created_at, sent_at
        FROM notification_outbox
        WHERE (%s::text IS NULL OR status = %s::text)
        ORDER BY created_at
        LIMIT %s
        """,
        (status, status, limit),
    ).fetchall()


def mark_notification_sent(
    conn: psycopg.Connection, notification_id: UUID, *, channel: str
) -> dict[str, Any] | None:
    """Record that a caller delivered a notification.

    This project delivers nothing itself: there is no mail transport, webhook
    or chat integration. The status records what a caller reported, which is
    why the channel is stored beside it.
    """
    return conn.execute(
        """
        UPDATE notification_outbox
           SET status = 'sent',
               channel = %s,
               sent_at = now(),
               attempt_count = attempt_count + 1
         WHERE notification_id = %s AND status = 'pending'
        RETURNING notification_id, status, channel, sent_at
        """,
        (channel, notification_id),
    ).fetchone()
