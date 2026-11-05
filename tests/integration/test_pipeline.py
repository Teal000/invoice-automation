"""Database-backed pipeline tests.

These cover the acceptance checks from the plan that cannot be tested without
a database: persistence, the duplicate guards, idempotency, the append-only
event log, and reconciliation between the reporting views and direct SQL.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import psycopg
import pytest

from invoice_automation.config import Settings
from invoice_automation.db import connect
from invoice_automation.models import RawInvoice, SubmissionInput
from invoice_automation.pipeline import process_structured_submission
from invoice_automation.rules.engine import BusinessState

pytestmark = pytest.mark.integration


def load(path: Path, *, idempotency_key: str | None = None):
    raw_bytes = path.read_bytes()
    invoice = RawInvoice.model_validate(json.loads(raw_bytes))
    submission = SubmissionInput(
        source="cli",
        original_file_name=path.name,
        file_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        idempotency_key=idempotency_key,
        storage_path=f"data/fixtures/structured/{path.name}",
    )
    return invoice, submission


def scalar(conn: psycopg.Connection, sql: str, params=()) -> int:
    row = conn.execute(sql, params).fetchone()
    return list(row.values())[0]


class TestValidInvoice:
    def test_a_clean_invoice_is_persisted_as_validated(self, seeded, fixtures_dir):
        invoice, submission = load(fixtures_dir / "valid_001.json")
        result = process_structured_submission(seeded, invoice, submission)

        assert result.state is BusinessState.VALIDATED
        assert result.invoice_created is True
        assert result.revision_number == 1
        assert result.failed_codes == ()

        row = seeded.execute(
            "SELECT * FROM v_invoice_register WHERE invoice_id = %s",
            (result.invoice_id,),
        ).fetchone()
        assert row["business_state"] == "validated"
        assert row["invoice_number"] == "FA-2026-0147"
        assert str(row["total"]) == "1591.923"
        assert row["failed_rule_codes"] == 0
        assert row["has_reviewer_edit"] is False

    def test_the_source_document_and_raw_values_are_retained(self, seeded, fixtures_dir):
        invoice, submission = load(fixtures_dir / "valid_001.json")
        result = process_structured_submission(seeded, invoice, submission)

        document = seeded.execute(
            """
            SELECT d.content_sha256, d.storage_path, d.raw_payload, d.extraction_method
            FROM documents d
            JOIN submissions s ON s.document_id = d.document_id
            WHERE s.submission_id = %s
            """,
            (result.submission_id,),
        ).fetchone()
        assert document["content_sha256"] == submission.file_sha256
        assert document["storage_path"].endswith("valid_001.json")
        # The raw strings survive next to the normalised columns.
        assert document["raw_payload"]["total"] == "1591.923"
        assert document["extraction_method"] == "structured_fixture:v1"

        revision = seeded.execute(
            """
            SELECT raw_values, field_problems, total
            FROM invoice_revisions WHERE invoice_id = %s
            """,
            (result.invoice_id,),
        ).fetchone()
        assert revision["raw_values"]["subtotal"] == "1337.750"
        assert revision["field_problems"] == []

    def test_every_check_is_recorded_not_only_the_failures(self, seeded, fixtures_dir):
        invoice, submission = load(fixtures_dir / "valid_001.json")
        result = process_structured_submission(seeded, invoice, submission)
        statuses = seeded.execute(
            """
            SELECT status, count(*) AS n FROM validation_results
            WHERE invoice_id = %s GROUP BY status
            """,
            (result.invoice_id,),
        ).fetchall()
        counts = {row["status"]: row["n"] for row in statuses}
        assert counts.get("fail", 0) == 0
        assert counts["pass"] >= 13

    def test_a_job_is_recorded_for_the_submission(self, seeded, fixtures_dir):
        invoice, submission = load(fixtures_dir / "valid_001.json")
        result = process_structured_submission(seeded, invoice, submission)
        job = seeded.execute(
            "SELECT * FROM processing_jobs WHERE submission_id = %s",
            (result.submission_id,),
        ).fetchone()
        assert job["status"] == "succeeded"
        assert job["attempt_count"] == 1
        assert job["finished_at"] is not None


class TestGroundTruth:
    def test_every_labelled_case_produces_its_expected_outcome(
        self, seeded, fixtures_dir, manifest
    ):
        """The plan's acceptance check, end to end against the database."""
        mismatches = []
        for case in manifest["cases"]:
            invoice, submission = load(fixtures_dir / case["file"])
            result = process_structured_submission(seeded, invoice, submission)
            actual = (str(result.state), list(result.failed_codes))
            expected = (case["expected_state"], case["expected_failed_codes"])
            if actual != expected:
                mismatches.append((case["case_id"], expected, actual))
        assert mismatches == []

    def test_the_manifest_covers_every_rule_code(self, manifest):
        from invoice_automation.rules.engine import ALL_RULE_CODES

        covered = {
            code for case in manifest["cases"] for code in case["expected_failed_codes"]
        }
        assert covered == set(ALL_RULE_CODES)


class TestDuplicates:
    def test_an_identical_file_does_not_create_a_second_invoice(
        self, seeded, fixtures_dir
    ):
        first_invoice, first_submission = load(fixtures_dir / "valid_001.json")
        first = process_structured_submission(seeded, first_invoice, first_submission)

        # Byte-identical content under a different file name.
        again_invoice, again_submission = load(
            fixtures_dir / "exact_file_duplicate_016.json"
        )
        assert again_submission.file_sha256 == first_submission.file_sha256
        second = process_structured_submission(seeded, again_invoice, again_submission)

        assert second.state is BusinessState.DUPLICATE
        assert second.failed_codes == ("EXACT_FILE_DUPLICATE",)
        assert second.duplicate_of_submission_id == first.submission_id
        assert second.invoice_id == first.invoice_id

        assert scalar(seeded, "SELECT count(*) AS n FROM invoices") == 1
        assert scalar(seeded, "SELECT count(*) AS n FROM submissions") == 2
        # The repeat arrival is not reprocessed, so no second revision.
        assert scalar(seeded, "SELECT count(*) AS n FROM invoice_revisions") == 1

    def test_the_duplicate_arrival_stays_visible_as_an_event(self, seeded, fixtures_dir):
        invoice, submission = load(fixtures_dir / "valid_001.json")
        process_structured_submission(seeded, invoice, submission)
        again, again_submission = load(fixtures_dir / "exact_file_duplicate_016.json")
        process_structured_submission(seeded, again, again_submission)

        events = seeded.execute(
            "SELECT event_type FROM audit_events ORDER BY event_id"
        ).fetchall()
        types = [event["event_type"] for event in events]
        assert types.count("submission_received") == 2
        assert "duplicate_file_linked" in types

    def test_a_second_document_for_the_same_invoice_number_is_linked_for_review(
        self, seeded, fixtures_dir
    ):
        invoice, submission = load(fixtures_dir / "valid_001.json")
        first = process_structured_submission(seeded, invoice, submission)

        other, other_submission = load(fixtures_dir / "business_duplicate_015.json")
        assert other_submission.file_sha256 != submission.file_sha256
        second = process_structured_submission(seeded, other, other_submission)

        assert second.failed_codes == ("BUSINESS_DUPLICATE",)
        assert second.state is BusinessState.NEEDS_REVIEW
        assert second.invoice_id == first.invoice_id
        assert second.invoice_created is False
        assert second.revision_number == 2

        # One canonical invoice, two arrivals, two revisions, both files kept.
        assert scalar(seeded, "SELECT count(*) AS n FROM invoices") == 1
        assert scalar(seeded, "SELECT count(*) AS n FROM documents") == 2
        state = scalar(
            seeded,
            "SELECT business_state AS s FROM invoices WHERE invoice_id = %s",
            (first.invoice_id,),
        )
        assert state == "needs_review"

    def test_concurrent_submissions_cannot_create_two_accepted_invoices(
        self, seeded, settings: Settings, test_database, fixtures_dir
    ):
        """Two threads submit the same invoice number at the same time."""
        payloads = [
            load(fixtures_dir / "valid_001.json"),
            load(fixtures_dir / "business_duplicate_015.json"),
        ]
        barrier = threading.Barrier(len(payloads))
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            invoice, submission = payloads[index]
            try:
                with connect(settings, database=test_database) as own_conn:
                    barrier.wait(timeout=10)
                    process_structured_submission(own_conn, invoice, submission)
            except BaseException as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(payloads))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == []
        assert scalar(seeded, "SELECT count(*) AS n FROM invoices") == 1
        assert scalar(seeded, "SELECT count(*) AS n FROM submissions") == 2


class TestIdempotency:
    def test_replaying_a_request_creates_no_second_record(self, seeded, fixtures_dir):
        invoice, submission = load(
            fixtures_dir / "valid_001.json", idempotency_key="req-0001"
        )
        first = process_structured_submission(seeded, invoice, submission)
        second = process_structured_submission(seeded, invoice, submission)

        assert first.replayed is False
        assert second.replayed is True
        assert second.submission_id == first.submission_id
        assert second.invoice_id == first.invoice_id
        assert second.outcome == "validated"

        assert scalar(seeded, "SELECT count(*) AS n FROM submissions") == 1
        assert scalar(seeded, "SELECT count(*) AS n FROM invoices") == 1
        assert scalar(seeded, "SELECT count(*) AS n FROM invoice_revisions") == 1
        assert scalar(seeded, "SELECT count(*) AS n FROM processing_jobs") == 1

    def test_the_replay_is_recorded_as_an_event(self, seeded, fixtures_dir):
        invoice, submission = load(
            fixtures_dir / "valid_001.json", idempotency_key="req-0002"
        )
        process_structured_submission(seeded, invoice, submission)
        process_structured_submission(seeded, invoice, submission)
        replays = scalar(
            seeded,
            "SELECT count(*) AS n FROM audit_events WHERE event_type = 'submission_replayed'",
        )
        assert replays == 1

    def test_the_key_is_enforced_by_the_database(self, seeded, fixtures_dir):
        invoice, submission = load(
            fixtures_dir / "valid_001.json", idempotency_key="req-0003"
        )
        process_structured_submission(seeded, invoice, submission)
        with pytest.raises(psycopg.errors.UniqueViolation):
            seeded.execute(
                """
                INSERT INTO submissions (source, idempotency_key)
                VALUES ('cli', 'req-0003')
                """
            )
        seeded.rollback()


class TestAuditTrail:
    def test_events_cannot_be_updated(self, seeded, fixtures_dir):
        invoice, submission = load(fixtures_dir / "valid_001.json")
        process_structured_submission(seeded, invoice, submission)
        with pytest.raises(psycopg.errors.RaiseException):
            seeded.execute("UPDATE audit_events SET actor = 'someone else'")
        seeded.rollback()

    def test_events_cannot_be_deleted(self, seeded, fixtures_dir):
        invoice, submission = load(fixtures_dir / "valid_001.json")
        process_structured_submission(seeded, invoice, submission)
        with pytest.raises(psycopg.errors.RaiseException):
            seeded.execute("DELETE FROM audit_events")
        seeded.rollback()


class TestReporting:
    def test_the_overview_reconciles_with_direct_sql(self, seeded, fixtures_dir, manifest):
        for case in manifest["cases"]:
            invoice, submission = load(fixtures_dir / case["file"])
            process_structured_submission(seeded, invoice, submission)

        overview = seeded.execute("SELECT * FROM v_processing_overview").fetchone()

        assert overview["submissions_received"] == scalar(
            seeded, "SELECT count(*) AS n FROM submissions"
        )
        assert overview["unique_invoices"] == scalar(
            seeded, "SELECT count(*) AS n FROM invoices"
        )
        assert overview["duplicate_arrivals"] == scalar(
            seeded, "SELECT count(*) AS n FROM submissions WHERE outcome = 'duplicate'"
        )
        assert overview["needs_review_invoices"] == scalar(
            seeded,
            "SELECT count(*) AS n FROM invoices WHERE business_state = 'needs_review'",
        )
        # Eligible excludes duplicate and unsupported invoices.
        assert overview["eligible_invoices"] == scalar(
            seeded,
            """
            SELECT count(*) AS n FROM invoices
            WHERE business_state NOT IN ('duplicate', 'unsupported')
            """,
        )
        expected_rate = (
            overview["needs_review_invoices"] / overview["eligible_invoices"]
        )
        assert abs(float(overview["review_rate"]) - expected_rate) < 0.0001

    def test_duplicate_arrivals_are_not_counted_as_processed_invoices(
        self, seeded, fixtures_dir
    ):
        invoice, submission = load(fixtures_dir / "valid_001.json")
        process_structured_submission(seeded, invoice, submission)
        again, again_submission = load(fixtures_dir / "exact_file_duplicate_016.json")
        process_structured_submission(seeded, again, again_submission)

        overview = seeded.execute("SELECT * FROM v_processing_overview").fetchone()
        assert overview["submissions_received"] == 2
        assert overview["duplicate_arrivals"] == 1
        assert overview["unique_invoices"] == 1
        assert overview["eligible_invoices"] == 1
        assert float(overview["straight_through_rate"]) == 1.0

    def test_exception_counts_separate_invoices_from_individual_checks(
        self, seeded, fixtures_dir, manifest
    ):
        for case in manifest["cases"]:
            invoice, submission = load(fixtures_dir / case["file"])
            process_structured_submission(seeded, invoice, submission)

        rows = seeded.execute(
            "SELECT * FROM v_exception_summary ORDER BY code"
        ).fetchall()
        by_code = {row["code"]: row for row in rows}
        # Every failure in the summary is attributed to a current revision.
        for row in rows:
            assert row["failed_checks"] >= row["invoices_affected"]
        assert "PO_AMOUNT_MISMATCH" in by_code
