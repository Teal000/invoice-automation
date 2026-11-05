"""Recovery sweeps and notification de-duplication.

Two of the plan's requirements live here: that interrupted processing can
resume without losing its source or history, and that repeated submissions
and retries do not produce repeated notifications.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from invoice_automation.config import PROJECT_ROOT, Settings
from invoice_automation.db import jobs as job_repo
from invoice_automation.db import repository as repo
from invoice_automation.pipeline import (
    ProcessingError,
    process_submission,
    record_arrival,
)
from invoice_automation.recovery import recover_jobs
from invoice_automation.rules.engine import BusinessState

pytestmark = pytest.mark.integration

PDF_DIR = PROJECT_ROOT / "data" / "fixtures" / "pdf"


@pytest.fixture
def storage_settings(settings: Settings, test_database: str, tmp_path: Path) -> Settings:
    """Settings with an isolated storage root, so a test can hide a file."""
    return Settings(db_name=test_database, storage_root=tmp_path / "storage")


def arrive(conn, storage_settings: Settings, name: str, **kwargs):
    path = PDF_DIR / name
    return record_arrival(
        conn,
        content=path.read_bytes(),
        original_file_name=path.name,
        source="cli",
        settings=storage_settings,
        **kwargs,
    )


def process(conn, storage_settings: Settings, submission_id):
    return process_submission(conn, submission_id, settings=storage_settings)


def notifications_of(conn, kind: str | None = None) -> list[dict]:
    rows = job_repo.list_notifications(conn)
    return [row for row in rows if kind is None or row["notification_type"] == kind]


class TestReviewNotifications:
    def test_a_review_case_is_announced_once(self, seeded, storage_settings):
        arrival = arrive(seeded, storage_settings, "total_mismatch_008.pdf")
        result = process(seeded, storage_settings, arrival.submission_id)
        assert result.state is BusinessState.NEEDS_REVIEW

        queued = notifications_of(seeded, "review_required")
        assert len(queued) == 1
        notification = queued[0]
        assert notification["invoice_id"] == result.invoice_id
        assert notification["status"] == "pending"
        assert notification["sent_at"] is None
        assert notification["payload"]["failed_codes"] == ["TOTAL_MISMATCH"]
        # Keyed to the revision, not the clock.
        assert notification["dedupe_key"].startswith(
            f"review_required:{result.invoice_id}:"
        )

    def test_processing_again_does_not_announce_again(self, seeded, storage_settings):
        arrival = arrive(seeded, storage_settings, "total_mismatch_008.pdf")
        process(seeded, storage_settings, arrival.submission_id)
        again = process(seeded, storage_settings, arrival.submission_id)

        assert again.replayed is True
        assert len(notifications_of(seeded, "review_required")) == 1

    def test_a_validated_invoice_announces_nothing(self, seeded, storage_settings):
        arrival = arrive(seeded, storage_settings, "valid_001.pdf")
        process(seeded, storage_settings, arrival.submission_id)
        assert notifications_of(seeded) == []

    def test_a_new_revision_is_new_information(self, seeded, storage_settings):
        """A second document claiming the same invoice number does notify."""
        first = arrive(seeded, storage_settings, "valid_001.pdf")
        process(seeded, storage_settings, first.submission_id)
        second = arrive(seeded, storage_settings, "business_duplicate_015.pdf")
        result = process(seeded, storage_settings, second.submission_id)

        assert result.revision_number == 2
        queued = notifications_of(seeded, "review_required")
        assert len(queued) == 1
        assert queued[0]["payload"]["revision_number"] == 2

    def test_a_repeat_arrival_is_announced_once_per_arrival(
        self, seeded, storage_settings
    ):
        first = arrive(seeded, storage_settings, "valid_001.pdf")
        process(seeded, storage_settings, first.submission_id)
        second = arrive(seeded, storage_settings, "exact_file_duplicate_016.pdf")
        process(seeded, storage_settings, second.submission_id)
        process(seeded, storage_settings, second.submission_id)

        queued = notifications_of(seeded, "duplicate_received")
        assert len(queued) == 1
        assert queued[0]["submission_id"] == second.submission_id

    def test_the_database_enforces_the_deduplication(self, seeded, storage_settings):
        arrival = arrive(seeded, storage_settings, "total_mismatch_008.pdf")
        process(seeded, storage_settings, arrival.submission_id)
        key = notifications_of(seeded, "review_required")[0]["dedupe_key"]

        with pytest.raises(psycopg.errors.UniqueViolation):
            seeded.execute(
                """
                INSERT INTO notification_outbox (notification_type, dedupe_key,
                                                 submission_id)
                VALUES ('review_required', %s, %s)
                """,
                (key, arrival.submission_id),
            )
        seeded.rollback()

    def test_enqueueing_the_same_key_twice_reports_not_created(self, seeded, storage_settings):
        arrival = arrive(seeded, storage_settings, "valid_001.pdf")
        first_id, created_first = job_repo.enqueue_notification(
            seeded,
            notification_type="review_required",
            dedupe_key="review_required:manual-test",
            submission_id=arrival.submission_id,
        )
        second_id, created_second = job_repo.enqueue_notification(
            seeded,
            notification_type="review_required",
            dedupe_key="review_required:manual-test",
            submission_id=arrival.submission_id,
        )
        assert created_first is True
        assert created_second is False
        assert second_id == first_id


class TestMarkingSent:
    def test_a_pending_notification_can_be_marked_sent(self, seeded, storage_settings):
        arrival = arrive(seeded, storage_settings, "total_mismatch_008.pdf")
        process(seeded, storage_settings, arrival.submission_id)
        notification = notifications_of(seeded, "review_required")[0]

        updated = job_repo.mark_notification_sent(
            seeded, notification["notification_id"], channel="manual"
        )
        assert updated["status"] == "sent"
        assert updated["channel"] == "manual"
        assert updated["sent_at"] is not None

    def test_marking_it_again_does_nothing(self, seeded, storage_settings):
        arrival = arrive(seeded, storage_settings, "total_mismatch_008.pdf")
        process(seeded, storage_settings, arrival.submission_id)
        notification_id = notifications_of(seeded, "review_required")[0][
            "notification_id"
        ]
        job_repo.mark_notification_sent(seeded, notification_id, channel="manual")
        assert (
            job_repo.mark_notification_sent(seeded, notification_id, channel="manual")
            is None
        )

    def test_a_sent_notification_must_have_a_timestamp(self, seeded):
        """The schema refuses an inconsistent pair rather than trusting code."""
        with pytest.raises(psycopg.errors.CheckViolation):
            seeded.execute(
                """
                INSERT INTO notification_outbox (notification_type, dedupe_key,
                                                 submission_id, status)
                VALUES ('review_required', 'k-no-timestamp',
                        (SELECT submission_id FROM submissions LIMIT 1), 'sent')
                """
            )
        seeded.rollback()


class TestRecoverySweep:
    def hide(self, storage_settings: Settings, storage_path: str) -> Path:
        stored = storage_settings.absolute_storage_root() / storage_path
        hidden = stored.with_suffix(".hidden")
        stored.rename(hidden)
        return hidden

    def test_a_failed_attempt_is_recovered_once_the_cause_is_gone(
        self, seeded, storage_settings
    ):
        arrival = arrive(seeded, storage_settings, "valid_001.pdf")
        hidden = self.hide(storage_settings, arrival.storage_path)

        with pytest.raises(ProcessingError):
            process(seeded, storage_settings, arrival.submission_id)

        candidates = job_repo.find_recoverable_jobs(seeded)
        assert [c["reason"] for c in candidates] == ["retry_pending"]

        # The cause clears; the sweep finishes the work with no re-upload.
        hidden.rename(hidden.with_suffix(".pdf"))
        recovered = recover_jobs(seeded, settings=storage_settings)
        assert len(recovered) == 1
        assert recovered[0].succeeded
        assert recovered[0].state == "validated"
        assert recovered[0].reason == "retry_pending"
        assert recovered[0].attempt_before == 1

        assert repo.get_job_for_submission(seeded, arrival.submission_id)["status"] == (
            "succeeded"
        )

    def test_a_stalled_running_job_is_picked_up(self, seeded, storage_settings):
        """A worker claimed the job and vanished; the lease has since expired."""
        arrival = arrive(seeded, storage_settings, "valid_001.pdf")
        job_repo.claim_job(seeded, arrival.submission_id, lease_seconds=300)
        seeded.execute(
            """
            UPDATE processing_jobs
               SET lease_expires_at = now() - interval '1 minute'
             WHERE submission_id = %s
            """,
            (arrival.submission_id,),
        )

        candidates = job_repo.find_recoverable_jobs(seeded)
        assert [c["reason"] for c in candidates] == ["lease_expired"]

        recovered = recover_jobs(seeded, settings=storage_settings)
        assert recovered[0].state == "validated"
        assert recovered[0].reason == "lease_expired"

    def test_a_job_nobody_triggered_is_picked_up(self, seeded, storage_settings):
        arrival = arrive(seeded, storage_settings, "valid_001.pdf")
        # Nothing called process. Backdate it past the grace period.
        seeded.execute(
            """
            UPDATE processing_jobs
               SET created_at = now() - interval '1 hour'
             WHERE submission_id = %s
            """,
            (arrival.submission_id,),
        )

        candidates = job_repo.find_recoverable_jobs(seeded)
        assert [c["reason"] for c in candidates] == ["queued_too_long"]
        recovered = recover_jobs(seeded, settings=storage_settings)
        assert recovered[0].state == "validated"

    def test_a_fresh_queued_job_is_left_alone(self, seeded, storage_settings):
        """Inside the grace period, an untriggered job is not yet stalled."""
        arrive(seeded, storage_settings, "valid_001.pdf")
        assert job_repo.find_recoverable_jobs(seeded) == []

    def test_a_job_with_no_attempts_left_is_left_for_a_person(
        self, seeded, storage_settings
    ):
        arrival = arrive(seeded, storage_settings, "valid_001.pdf")
        self.hide(storage_settings, arrival.storage_path)
        with pytest.raises(ProcessingError):
            process(seeded, storage_settings, arrival.submission_id)
        # Spend the remaining attempts.
        seeded.execute(
            """
            UPDATE processing_jobs
               SET attempt_count = max_attempts, status = 'failed'
             WHERE submission_id = %s
            """,
            (arrival.submission_id,),
        )

        assert job_repo.find_recoverable_jobs(seeded) == []
        assert recover_jobs(seeded, settings=storage_settings) == []

    def test_a_settled_submission_is_never_swept_up(self, seeded, storage_settings):
        arrival = arrive(seeded, storage_settings, "valid_001.pdf")
        process(seeded, storage_settings, arrival.submission_id)
        # Even if the job row is made to look stalled, the outcome rules.
        seeded.execute(
            """
            UPDATE processing_jobs
               SET status = 'retry_pending', attempt_count = 1
             WHERE submission_id = %s
            """,
            (arrival.submission_id,),
        )
        assert job_repo.find_recoverable_jobs(seeded) == []

    def test_the_sweep_does_not_create_a_second_invoice(self, seeded, storage_settings):
        arrival = arrive(seeded, storage_settings, "valid_001.pdf")
        seeded.execute(
            "UPDATE processing_jobs SET created_at = now() - interval '1 hour' "
            "WHERE submission_id = %s",
            (arrival.submission_id,),
        )
        recover_jobs(seeded, settings=storage_settings)
        recover_jobs(seeded, settings=storage_settings)

        count = seeded.execute("SELECT count(*) AS n FROM invoices").fetchone()["n"]
        assert count == 1

    def test_one_failing_job_does_not_block_the_others(self, seeded, storage_settings):
        """A poison document must not stop everything queued behind it."""
        broken = arrive(seeded, storage_settings, "valid_001.pdf")
        healthy = arrive(seeded, storage_settings, "valid_002.pdf")
        self.hide(storage_settings, broken.storage_path)
        seeded.execute(
            "UPDATE processing_jobs SET created_at = now() - interval '1 hour'"
        )

        recovered = recover_jobs(seeded, settings=storage_settings)
        by_submission = {job.submission_id: job for job in recovered}
        assert len(recovered) == 2
        assert by_submission[broken.submission_id].succeeded is False
        assert by_submission[broken.submission_id].job_status == "retry_pending"
        assert by_submission[healthy.submission_id].state == "validated"

    def test_a_failed_attempt_is_announced_per_attempt(self, seeded, storage_settings):
        arrival = arrive(seeded, storage_settings, "valid_001.pdf")
        self.hide(storage_settings, arrival.storage_path)

        with pytest.raises(ProcessingError):
            process(seeded, storage_settings, arrival.submission_id)
        assert len(notifications_of(seeded, "processing_failed")) == 1

        # A second failing attempt is new information.
        with pytest.raises(ProcessingError):
            process(seeded, storage_settings, arrival.submission_id)
        failures = notifications_of(seeded, "processing_failed")
        assert len(failures) == 2
        assert {n["payload"]["attempt"] for n in failures} == {1, 2}
