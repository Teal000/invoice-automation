"""Reviewer corrections, rejections and accepted exceptions.

The plan's requirement 4: a reviewer can correct data, explain a decision and
trigger revalidation. Also the distinction it insists on — that a person
accepting an exception is not the same as every check passing — which is a
reporting claim, so it is tested in the reporting view and not only in the
code.
"""

from __future__ import annotations

import hashlib
import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from invoice_automation.api.main import API_KEY_HEADER, create_app
from invoice_automation.config import PROJECT_ROOT, Settings
from invoice_automation.db import jobs as job_repo
from invoice_automation.db import repository as repo
from invoice_automation.models import RawInvoice, SubmissionInput
from invoice_automation.pipeline import process_structured_submission
from invoice_automation.review import (
    ReviewNotApplicable,
    RevisionConflict,
    accept_exception,
    apply_correction,
    reject_invoice,
)
from invoice_automation.rules.engine import BusinessState

pytestmark = pytest.mark.integration

STRUCTURED = PROJECT_ROOT / "data" / "fixtures" / "structured"
API_KEY = "review-test-credential"


def payload(name: str) -> dict:
    return json.loads((STRUCTURED / name).read_text(encoding="utf-8"))


def ingest(conn, name: str):
    """Record and process a structured fixture."""
    raw_bytes = (STRUCTURED / name).read_bytes()
    invoice = RawInvoice.model_validate(json.loads(raw_bytes))
    submission = SubmissionInput(
        source="cli",
        original_file_name=name,
        file_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        storage_path=f"data/fixtures/structured/{name}",
    )
    return process_structured_submission(conn, invoice, submission)


def register_row(conn, invoice_id) -> dict:
    return conn.execute(
        "SELECT * FROM v_invoice_register WHERE invoice_id = %s", (invoice_id,)
    ).fetchone()


def overview(conn) -> dict:
    return conn.execute("SELECT * FROM v_processing_overview").fetchone()


@pytest.fixture
def review_case(seeded):
    """An invoice in review: its stated total does not reconcile."""
    result = ingest(seeded, "total_mismatch_008.json")
    assert result.state is BusinessState.NEEDS_REVIEW
    assert result.failed_codes == ("TOTAL_MISMATCH",)
    return result


class TestCorrection:
    def test_a_correction_that_fixes_the_problem_validates_the_invoice(
        self, seeded, review_case
    ):
        corrected = payload("total_mismatch_008.json")
        # The document said 1600.000; subtotal plus tax is 1591.923.
        corrected["total"] = "1591.923"

        outcome = apply_correction(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            raw=RawInvoice.model_validate(corrected),
            actor="reviewer:nidhal",
            reason="Total re-keyed from the document; 1600.000 was a typing error.",
        )

        assert outcome.state is BusinessState.VALIDATED
        assert outcome.revision_number == 2
        assert outcome.failed_codes == ()

        row = register_row(seeded, review_case.invoice_id)
        assert row["business_state"] == "validated"
        assert row["current_revision_number"] == 2
        assert row["current_revision_source"] == "reviewer_correction"
        assert str(row["total"]) == "1591.923"
        assert row["has_reviewer_edit"] is True
        assert row["failed_rule_codes"] == 0

    def test_the_corrected_values_are_checked_not_trusted(self, seeded, review_case):
        """A correction that introduces a different fault is caught."""
        corrected = payload("total_mismatch_008.json")
        corrected["total"] = "1591.923"
        corrected["line_items"][0]["line_amount"] = "999.000"

        outcome = apply_correction(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            raw=RawInvoice.model_validate(corrected),
            actor="reviewer:nidhal",
            reason="Attempted fix with a mistyped line amount.",
        )

        assert outcome.state is BusinessState.NEEDS_REVIEW
        assert set(outcome.failed_codes) >= {"LINE_TOTAL_MISMATCH", "SUBTOTAL_MISMATCH"}

    def test_a_correction_that_does_not_resolve_it_announces_again(
        self, seeded, review_case
    ):
        before = len(job_repo.list_notifications(seeded))
        corrected = payload("total_mismatch_008.json")
        corrected["total"] = "1700.000"  # still wrong

        apply_correction(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            raw=RawInvoice.model_validate(corrected),
            actor="reviewer:nidhal",
            reason="Re-keyed, still does not reconcile.",
        )
        notifications = job_repo.list_notifications(seeded)
        assert len(notifications) == before + 1
        assert notifications[-1]["payload"]["after_correction"] is True

    def test_a_correction_is_not_a_duplicate_of_itself(self, seeded, review_case):
        """Revalidation must exclude the invoice being revalidated.

        Without that exclusion the invoice finds its own supplier and number
        in the register and reports BUSINESS_DUPLICATE against itself, which
        would make every correction fail.
        """
        corrected = payload("total_mismatch_008.json")
        corrected["total"] = "1591.923"
        outcome = apply_correction(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            raw=RawInvoice.model_validate(corrected),
            actor="reviewer:nidhal",
            reason="Fixing the total.",
        )
        assert "BUSINESS_DUPLICATE" not in outcome.failed_codes
        statuses = {
            result.code: result.status
            for result in outcome.results
            if result.code == "BUSINESS_DUPLICATE"
        }
        assert str(statuses["BUSINESS_DUPLICATE"]) == "pass"

    def test_a_correction_still_detects_a_real_duplicate(self, seeded, review_case):
        """Editing one invoice into another one's number is still caught."""
        other = ingest(seeded, "valid_001.json")
        corrected = payload("total_mismatch_008.json")
        corrected["invoice_number"] = payload("valid_001.json")["invoice_number"]

        outcome = apply_correction(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            raw=RawInvoice.model_validate(corrected),
            actor="reviewer:nidhal",
            reason="Mistakenly re-keyed to another invoice number.",
        )
        assert "BUSINESS_DUPLICATE" in outcome.failed_codes
        assert other.invoice_id is not None

    def test_the_action_is_recorded_with_its_reason(self, seeded, review_case):
        corrected = payload("total_mismatch_008.json")
        corrected["total"] = "1591.923"
        apply_correction(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            raw=RawInvoice.model_validate(corrected),
            actor="reviewer:nidhal",
            reason="Total re-keyed from the document.",
        )
        actions = repo.list_review_actions(seeded, review_case.invoice_id)
        assert len(actions) == 1
        assert actions[0]["action"] == "correct"
        assert actions[0]["actor"] == "reviewer:nidhal"
        assert "re-keyed" in actions[0]["reason"]
        assert actions[0]["from_revision_number"] == 1
        assert actions[0]["to_revision_number"] == 2

    def test_both_revisions_survive(self, seeded, review_case):
        corrected = payload("total_mismatch_008.json")
        corrected["total"] = "1591.923"
        apply_correction(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            raw=RawInvoice.model_validate(corrected),
            actor="reviewer:nidhal",
            reason="Fixing the total.",
        )
        detail = repo.get_invoice_detail(seeded, review_case.invoice_id)
        assert [r["revision_number"] for r in detail["revisions"]] == [1, 2]
        assert [r["source"] for r in detail["revisions"]] == [
            "extraction",
            "reviewer_correction",
        ]
        # The original failure is still on record, against revision 1.
        historical = [
            check
            for check in detail["checks"]
            if check["code"] == "TOTAL_MISMATCH" and check["status"] == "fail"
        ]
        assert historical and historical[0]["revision_number"] == 1
        assert historical[0]["is_current"] is False

    def test_an_edit_against_a_stale_revision_is_refused(self, seeded, review_case):
        corrected = payload("total_mismatch_008.json")
        corrected["total"] = "1591.923"
        apply_correction(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            raw=RawInvoice.model_validate(corrected),
            actor="reviewer:first",
            reason="First correction.",
        )
        # A second reviewer still holding revision 1.
        with pytest.raises(RevisionConflict) as caught:
            apply_correction(
                seeded,
                review_case.invoice_id,
                expected_revision_number=1,
                raw=RawInvoice.model_validate(corrected),
                actor="reviewer:second",
                reason="Edit from a stale view.",
            )
        assert caught.value.expected == 1
        assert caught.value.actual == 2

    def test_an_empty_reason_is_refused_by_the_database(self, seeded, review_case):
        with pytest.raises(psycopg.errors.CheckViolation):
            seeded.execute(
                """
                INSERT INTO review_actions (invoice_id, actor, action, reason)
                VALUES (%s, 'reviewer', 'reject', '   ')
                """,
                (review_case.invoice_id,),
            )
        seeded.rollback()


class TestRejection:
    def test_rejecting_records_the_reason_and_the_state(self, seeded, review_case):
        outcome = reject_invoice(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            actor="reviewer:nidhal",
            reason="Supplier confirmed the invoice was issued in error.",
        )
        assert outcome.state is BusinessState.REJECTED
        assert register_row(seeded, review_case.invoice_id)["business_state"] == (
            "rejected"
        )
        actions = repo.list_review_actions(seeded, review_case.invoice_id)
        assert actions[0]["action"] == "reject"
        assert actions[0]["to_revision_number"] is None

    def test_rejection_releases_the_invoice_number(self, seeded, review_case):
        """A corrected document can be submitted after a rejection."""
        reject_invoice(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            actor="reviewer:nidhal",
            reason="Rejected; supplier to reissue.",
        )
        # The same supplier and number arriving again creates a new invoice
        # rather than colliding with the rejected one.
        corrected = payload("total_mismatch_008.json")
        corrected["total"] = "1591.923"
        raw_bytes = json.dumps(corrected).encode()
        result = process_structured_submission(
            seeded,
            RawInvoice.model_validate(corrected),
            SubmissionInput(
                source="cli",
                original_file_name="reissued.json",
                file_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            ),
        )
        assert result.invoice_created is True
        assert result.invoice_id != review_case.invoice_id
        assert result.state is BusinessState.VALIDATED

    def test_rejecting_twice_is_refused(self, seeded, review_case):
        reject_invoice(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            actor="reviewer:nidhal",
            reason="First rejection.",
        )
        with pytest.raises(ReviewNotApplicable):
            reject_invoice(
                seeded,
                review_case.invoice_id,
                expected_revision_number=1,
                actor="reviewer:nidhal",
                reason="Again.",
            )

    def test_a_rejected_invoice_cannot_be_corrected(self, seeded, review_case):
        reject_invoice(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            actor="reviewer:nidhal",
            reason="Rejected.",
        )
        with pytest.raises(ReviewNotApplicable):
            apply_correction(
                seeded,
                review_case.invoice_id,
                expected_revision_number=1,
                raw=RawInvoice.model_validate(payload("total_mismatch_008.json")),
                actor="reviewer:nidhal",
                reason="Trying to edit a rejected invoice.",
            )


class TestAcceptedException:
    def test_accepting_puts_it_in_the_register_but_marks_it(self, seeded, review_case):
        outcome = accept_exception(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            actor="reviewer:nidhal",
            reason="Supplier confirmed the total in writing; difference accepted.",
        )
        assert outcome.state is BusinessState.VALIDATED

        row = register_row(seeded, review_case.invoice_id)
        assert row["business_state"] == "validated"
        # The distinction the plan insists on.
        assert row["accepted_with_exception"] is True
        assert row["has_reviewer_edit"] is False
        # The failed check is still on the current revision: nothing was
        # rewritten to make the invoice look clean.
        assert row["failed_rule_codes"] == 1

    def test_the_straight_through_rate_excludes_it(self, seeded, review_case):
        clean = ingest(seeded, "valid_001.json")
        assert clean.state is BusinessState.VALIDATED

        before = overview(seeded)
        assert before["validated_invoices"] == 1
        assert before["validated_without_edit"] == 1

        accept_exception(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            actor="reviewer:nidhal",
            reason="Accepted after supplier confirmation.",
        )

        after = overview(seeded)
        # Two invoices are now in the register...
        assert after["validated_invoices"] == 2
        # ...but only one of them got there on the rules alone.
        assert after["validated_without_edit"] == 1
        assert after["accepted_with_exception_invoices"] == 1
        assert float(after["straight_through_rate"]) == 0.5

    def test_a_correction_also_excludes_it_from_straight_through(
        self, seeded, review_case
    ):
        corrected = payload("total_mismatch_008.json")
        corrected["total"] = "1591.923"
        apply_correction(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            raw=RawInvoice.model_validate(corrected),
            actor="reviewer:nidhal",
            reason="Total re-keyed.",
        )
        after = overview(seeded)
        assert after["validated_invoices"] == 1
        assert after["validated_without_edit"] == 0
        assert after["corrected_invoices"] == 1
        assert float(after["straight_through_rate"]) == 0.0

    def test_accepting_an_invoice_that_passed_is_refused(self, seeded):
        clean = ingest(seeded, "valid_001.json")
        with pytest.raises(ReviewNotApplicable, match="only an invoice in review"):
            accept_exception(
                seeded,
                clean.invoice_id,
                expected_revision_number=1,
                actor="reviewer:nidhal",
                reason="Nothing to accept.",
            )


class TestEventTrail:
    def test_every_reviewer_action_is_an_event(self, seeded, review_case):
        corrected = payload("total_mismatch_008.json")
        corrected["total"] = "1591.923"
        apply_correction(
            seeded,
            review_case.invoice_id,
            expected_revision_number=1,
            raw=RawInvoice.model_validate(corrected),
            actor="reviewer:nidhal",
            reason="Total re-keyed.",
        )
        reject_invoice(
            seeded,
            review_case.invoice_id,
            expected_revision_number=2,
            actor="reviewer:nidhal",
            reason="Rejected after all.",
        )
        events = [
            row["event_type"]
            for row in seeded.execute(
                """
                SELECT event_type FROM audit_events
                WHERE entity_type = 'invoice' AND entity_id = %s
                ORDER BY event_id
                """,
                (review_case.invoice_id,),
            )
        ]
        assert "reviewer_correction" in events
        assert "invoice_rejected" in events


class TestReviewApi:
    @pytest.fixture
    def client(self, settings: Settings, test_database: str, seeded) -> TestClient:
        app = create_app(Settings(db_name=test_database, api_key=API_KEY))
        return TestClient(app, headers={API_KEY_HEADER: API_KEY})

    def test_a_correction_over_http(self, client: TestClient, review_case):
        corrected = payload("total_mismatch_008.json")
        corrected["total"] = "1591.923"
        response = client.post(
            f"/invoices/{review_case.invoice_id}/revisions",
            json={
                "expected_revision_number": 1,
                "reason": "Total re-keyed from the document.",
                "invoice": corrected,
            },
            headers={"X-Actor": "nidhal"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["state"] == "validated"
        assert body["revision_number"] == 2
        assert body["actor"] == "api:nidhal"
        assert body["failed_codes"] == []

    def test_a_stale_revision_is_a_conflict(self, client: TestClient, review_case):
        corrected = payload("total_mismatch_008.json")
        corrected["total"] = "1591.923"
        body = {
            "expected_revision_number": 1,
            "reason": "Fixing.",
            "invoice": corrected,
        }
        client.post(f"/invoices/{review_case.invoice_id}/revisions", json=body)
        again = client.post(f"/invoices/{review_case.invoice_id}/revisions", json=body)
        assert again.status_code == 409
        error = again.json()["error"]
        assert error["code"] == "revision_conflict"
        assert error["detail"]["current_revision_number"] == 2

    def test_accepting_an_exception_over_http_says_what_it_means(
        self, client: TestClient, review_case
    ):
        response = client.post(
            f"/invoices/{review_case.invoice_id}/review-actions",
            json={
                "action": "accept_exception",
                "expected_revision_number": 1,
                "reason": "Supplier confirmed the total.",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "validated"
        assert "not recorded as the checks having passed" in body["note"]

    def test_a_reason_is_required(self, client: TestClient, review_case):
        response = client.post(
            f"/invoices/{review_case.invoice_id}/review-actions",
            json={
                "action": "reject",
                "expected_revision_number": 1,
                "reason": "",
            },
        )
        assert response.status_code == 422

    def test_an_unknown_action_is_refused(self, client: TestClient, review_case):
        response = client.post(
            f"/invoices/{review_case.invoice_id}/review-actions",
            json={
                "action": "approve_everything",
                "expected_revision_number": 1,
                "reason": "Trying it on.",
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unknown_review_action"

    def test_the_invoice_detail_shows_the_review_history(
        self, client: TestClient, review_case
    ):
        client.post(
            f"/invoices/{review_case.invoice_id}/review-actions",
            json={
                "action": "accept_exception",
                "expected_revision_number": 1,
                "reason": "Accepted after supplier confirmation.",
            },
        )
        detail = client.get(f"/invoices/{review_case.invoice_id}").json()
        assert detail["accepted_with_exception"] is True
        assert len(detail["review_actions"]) == 1
        assert detail["review_actions"][0]["action"] == "accept_exception"
        assert "supplier confirmation" in detail["review_actions"][0]["reason"]

    def test_correcting_an_unknown_invoice_is_404(self, client: TestClient):
        response = client.post(
            "/invoices/00000000-0000-4000-8000-000000000000/revisions",
            json={
                "expected_revision_number": 1,
                "reason": "No such invoice.",
                "invoice": payload("valid_001.json"),
            },
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "invoice_not_found"
