"""The recovery sweep: find work that stalled, and finish it.

Three things can leave a submission unfinished: an attempt failed and
attempts remain, a worker claimed a job and vanished before its lease
expired, or nothing ever triggered processing. A sweep finds all three and
processes them.

It is safe because processing is idempotent. A submission that reached an
outcome between the sweep finding it and processing it is reported, not
reprocessed; a job whose attempts are spent is left alone for a person.

One job failing does not stop the sweep: each is handled on its own and its
error is reported, because a single poison document must not block every
other document behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg

from .config import Settings, get_settings
from .pipeline import ProcessingError, ProcessingResult, SubmissionNotFound, process_submission


@dataclass(frozen=True)
class RecoveredJob:
    job_id: UUID
    submission_id: UUID
    #: Why it was picked up: retry_pending, lease_expired or queued_too_long.
    reason: str
    attempt_before: int
    #: The outcome if processing succeeded this time.
    state: str | None = None
    #: True when the submission turned out to be settled already.
    already_settled: bool = False
    #: Set when this attempt failed too.
    error: str | None = None
    job_status: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def recover_jobs(
    conn: psycopg.Connection,
    *,
    limit: int = 20,
    settings: Settings | None = None,
    actor: str = "recovery",
) -> list[RecoveredJob]:
    """Process every job that qualifies for recovery, and report each one."""
    from .db import jobs as job_repo

    settings = settings or get_settings()
    candidates = job_repo.find_recoverable_jobs(
        conn,
        limit=limit,
        queued_grace_seconds=settings.queued_job_grace_seconds,
    )

    recovered: list[RecoveredJob] = []
    for candidate in candidates:
        attempt_before = candidate["attempt_count"]
        try:
            result: ProcessingResult = process_submission(
                conn,
                candidate["submission_id"],
                actor=actor,
                settings=settings,
            )
        except ProcessingError as exc:
            recovered.append(
                RecoveredJob(
                    job_id=candidate["job_id"],
                    submission_id=candidate["submission_id"],
                    reason=candidate["reason"],
                    attempt_before=attempt_before,
                    error=str(exc),
                    job_status=exc.job_status,
                )
            )
            continue
        except SubmissionNotFound as exc:  # pragma: no cover - would need a deleted row
            recovered.append(
                RecoveredJob(
                    job_id=candidate["job_id"],
                    submission_id=candidate["submission_id"],
                    reason=candidate["reason"],
                    attempt_before=attempt_before,
                    error=f"submission missing: {exc}",
                )
            )
            continue

        recovered.append(
            RecoveredJob(
                job_id=candidate["job_id"],
                submission_id=candidate["submission_id"],
                reason=candidate["reason"],
                attempt_before=attempt_before,
                state=str(result.state),
                already_settled=result.replayed,
            )
        )

    return recovered
