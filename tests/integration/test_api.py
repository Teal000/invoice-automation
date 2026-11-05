"""The HTTP service, against a live database.

Covers the two things the API adds over the pipeline: that arrival and
processing are genuinely separate steps, and that a technical failure leaves
something retryable behind. The second is the plan's week-2 requirement that
an interrupted job can resume without losing its source or event history.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from invoice_automation.api.main import API_KEY_HEADER, create_app
from invoice_automation.config import PROJECT_ROOT, Settings

pytestmark = pytest.mark.integration

PDF_DIR = PROJECT_ROOT / "data" / "fixtures" / "pdf"
STRUCTURED_DIR = PROJECT_ROOT / "data" / "fixtures" / "structured"

API_KEY = "test-service-credential-not-a-real-one"


@pytest.fixture
def api_settings(settings: Settings, test_database: str, tmp_path: Path) -> Settings:
    """Point the service at the test database and an isolated storage root."""
    return Settings(
        db_name=test_database,
        api_key=API_KEY,
        storage_root=tmp_path / "storage",
    )


@pytest.fixture
def client(api_settings: Settings, seeded) -> TestClient:
    """A client with the credential already set. ``seeded`` loads the registry."""
    app = create_app(api_settings)
    return TestClient(app, headers={API_KEY_HEADER: API_KEY})


@pytest.fixture
def anonymous(api_settings: Settings, seeded) -> TestClient:
    return TestClient(create_app(api_settings))


def upload(client: TestClient, path: Path, **kwargs):
    media = "application/pdf" if path.suffix == ".pdf" else "application/json"
    with path.open("rb") as handle:
        return client.post(
            "/submissions",
            files={"file": (path.name, handle, media)},
            **kwargs,
        )


class TestService:
    def test_health_needs_no_credential(self, anonymous: TestClient):
        response = anonymous.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "reachable"
        assert body["migrations_applied"] >= 3

    def test_the_service_refuses_to_start_without_a_credential(
        self, settings: Settings, test_database: str
    ):
        """An API running open to the port is worse than one that will not start."""
        with pytest.raises(RuntimeError, match="INVAUT_API_KEY"):
            create_app(Settings(db_name=test_database, api_key=""))

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/invoices"),
            ("get", "/reports/summary"),
            ("post", "/submissions"),
        ],
    )
    def test_endpoints_require_the_credential(
        self, anonymous: TestClient, method: str, path: str
    ):
        response = getattr(anonymous, method)(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    def test_a_wrong_credential_is_refused(self, api_settings: Settings, seeded):
        client = TestClient(create_app(api_settings), headers={API_KEY_HEADER: "wrong"})
        assert client.get("/invoices").status_code == 401

    def test_errors_share_one_shape(self, client: TestClient):
        response = client.get("/invoices/00000000-0000-4000-8000-000000000000")
        assert response.status_code == 404
        assert set(response.json()["error"]) == {"code", "message", "detail"}
        assert response.json()["error"]["code"] == "invoice_not_found"


class TestArrivalThenProcessing:
    def test_an_upload_is_recorded_without_being_processed(self, client: TestClient):
        response = upload(client, PDF_DIR / "valid_001.pdf")
        assert response.status_code == 201
        body = response.json()
        assert body["outcome"] == "pending"
        assert body["processed"] is None
        assert body["file_sha256"]
        assert body["job_id"]

        detail = client.get(f"/submissions/{body['submission_id']}").json()
        assert detail["outcome"] == "pending"
        assert detail["job"]["status"] == "queued"
        assert detail["job"]["attempt_count"] == 0
        assert detail["media_type"] == "application/pdf"
        # Extraction has not run yet, so there is nothing to report about it.
        assert detail["extraction_method"] is None

    def test_processing_produces_the_outcome(self, client: TestClient):
        submission_id = upload(client, PDF_DIR / "valid_001.pdf").json()["submission_id"]
        response = client.post(f"/submissions/{submission_id}/process")
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "validated"
        assert body["invoice_id"]
        assert body["invoice_created"] is True
        assert body["revision_number"] == 1
        assert body["failed_codes"] == []
        assert body["job_attempt"] == 1

        detail = client.get(f"/submissions/{submission_id}").json()
        assert detail["outcome"] == "validated"
        assert detail["job"]["status"] == "succeeded"
        assert detail["extraction_method"] == "pdf_text:v1/atlas"

    def test_processing_twice_does_not_process_twice(self, client: TestClient):
        submission_id = upload(client, PDF_DIR / "valid_001.pdf").json()["submission_id"]
        first = client.post(f"/submissions/{submission_id}/process").json()
        second = client.post(f"/submissions/{submission_id}/process").json()

        assert first["replayed"] is False
        assert second["replayed"] is True
        assert second["invoice_id"] == first["invoice_id"]
        assert len(client.get("/invoices").json()["invoices"]) == 1

    def test_processing_can_be_asked_for_inline(self, client: TestClient):
        body = upload(client, PDF_DIR / "valid_002.pdf", params={"process": "true"}).json()
        assert body["outcome"] == "validated"
        assert body["processed"]["state"] == "validated"
        assert body["processed"]["invoice_id"]

    def test_a_structured_payload_is_accepted_too(self, client: TestClient):
        body = upload(
            client, STRUCTURED_DIR / "valid_001.json", params={"process": "true"}
        ).json()
        assert body["processed"]["state"] == "validated"

    def test_a_failing_document_is_recorded_not_rejected(self, client: TestClient):
        body = upload(
            client, PDF_DIR / "total_mismatch_008.pdf", params={"process": "true"}
        ).json()
        assert body["processed"]["state"] == "needs_review"
        assert body["processed"]["failed_codes"] == ["TOTAL_MISMATCH"]
        check = body["processed"]["failed_checks"][0]
        assert check["code"] == "TOTAL_MISMATCH"
        assert check["expected"] and check["observed"]

    def test_processing_an_unknown_submission_is_404(self, client: TestClient):
        response = client.post(
            "/submissions/00000000-0000-4000-8000-000000000000/process"
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "submission_not_found"


class TestUploadValidation:
    def test_an_empty_file_is_refused(self, client: TestClient):
        response = client.post(
            "/submissions", files={"file": ("empty.pdf", b"", "application/pdf")}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "empty_file"

    def test_an_unaccepted_file_type_is_refused(self, client: TestClient):
        response = client.post(
            "/submissions", files={"file": ("notes.txt", b"hello", "text/plain")}
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_media_type"


class TestIdempotencyAndDuplicates:
    def test_the_same_key_records_nothing_new(self, client: TestClient):
        first = upload(client, PDF_DIR / "valid_001.pdf", headers={"Idempotency-Key": "k-1"})
        second = upload(client, PDF_DIR / "valid_001.pdf", headers={"Idempotency-Key": "k-1"})

        assert first.status_code == 201
        # Not 201: nothing was created the second time.
        assert second.status_code == 200
        assert second.json()["replayed"] is True
        assert second.json()["submission_id"] == first.json()["submission_id"]

    def test_an_identical_file_reuses_the_stored_bytes(self, client: TestClient):
        first = upload(client, PDF_DIR / "valid_001.pdf").json()
        second = upload(client, PDF_DIR / "exact_file_duplicate_016.pdf").json()

        assert first["bytes_already_stored"] is False
        # Content-addressed storage: identical bytes, one file on disk.
        assert second["bytes_already_stored"] is True
        assert second["storage_path"] == first["storage_path"]
        assert second["submission_id"] != first["submission_id"]

    def test_an_identical_file_is_linked_when_processed(self, client: TestClient):
        first = upload(client, PDF_DIR / "valid_001.pdf", params={"process": "true"}).json()
        second = upload(
            client, PDF_DIR / "exact_file_duplicate_016.pdf", params={"process": "true"}
        ).json()

        assert second["processed"]["state"] == "duplicate"
        assert second["processed"]["failed_codes"] == ["EXACT_FILE_DUPLICATE"]
        assert (
            second["processed"]["duplicate_of_submission_id"] == first["submission_id"]
        )
        assert len(client.get("/invoices").json()["invoices"]) == 1


class TestRecoverableFailure:
    """A technical failure must leave the source and a retryable job behind."""

    def test_a_missing_stored_file_fails_the_job_but_keeps_everything(
        self, client: TestClient, api_settings: Settings
    ):
        arrival = upload(client, PDF_DIR / "valid_001.pdf").json()
        stored = api_settings.absolute_storage_root() / arrival["storage_path"]
        assert stored.exists()

        # Simulate the storage being briefly unavailable mid-workflow.
        hidden = stored.with_suffix(".hidden")
        stored.rename(hidden)

        response = client.post(f"/submissions/{arrival['submission_id']}/process")
        assert response.status_code == 500
        error = response.json()["error"]
        assert error["code"] == "processing_failed"
        assert error["detail"]["retryable"] is True
        assert error["detail"]["job_status"] == "retry_pending"

        # The arrival, the document record and the event history survive.
        detail = client.get(f"/submissions/{arrival['submission_id']}").json()
        assert detail["outcome"] == "failed"
        assert detail["job"]["status"] == "retry_pending"
        assert detail["job"]["attempt_count"] == 1
        assert detail["job"]["last_error"]
        assert detail["file_sha256"] == arrival["file_sha256"]
        # No half-written invoice: the work transaction rolled back.
        assert client.get("/invoices").json()["invoices"] == []

    def test_the_job_can_then_be_retried_to_success(
        self, client: TestClient, api_settings: Settings
    ):
        arrival = upload(client, PDF_DIR / "valid_001.pdf").json()
        stored = api_settings.absolute_storage_root() / arrival["storage_path"]
        hidden = stored.with_suffix(".hidden")
        stored.rename(hidden)
        client.post(f"/submissions/{arrival['submission_id']}/process")

        job_id = client.get(f"/submissions/{arrival['submission_id']}").json()["job"][
            "job_id"
        ]

        # Storage comes back; the document never had to be uploaded again.
        hidden.rename(stored)
        retried = client.post(f"/jobs/{job_id}/retry")
        assert retried.status_code == 200
        body = retried.json()
        assert body["state"] == "validated"
        assert body["job_attempt"] == 2

        detail = client.get(f"/submissions/{arrival['submission_id']}").json()
        assert detail["outcome"] == "validated"
        assert detail["job"]["status"] == "succeeded"
        # One invoice, from one submission, after a failure and a retry.
        assert len(client.get("/invoices").json()["invoices"]) == 1

    def test_a_succeeded_job_is_not_retryable(self, client: TestClient):
        arrival = upload(client, PDF_DIR / "valid_001.pdf", params={"process": "true"}).json()
        job_id = client.get(f"/submissions/{arrival['submission_id']}").json()["job"][
            "job_id"
        ]
        response = client.post(f"/jobs/{job_id}/retry")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "job_not_retryable"

    def test_retrying_an_unknown_job_is_404(self, client: TestClient):
        response = client.post("/jobs/00000000-0000-4000-8000-000000000000/retry")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "job_not_found"


class TestReads:
    @pytest.fixture
    def loaded(self, client: TestClient) -> TestClient:
        for name in (
            "valid_001.pdf",
            "valid_002.pdf",
            "total_mismatch_008.pdf",
            "credit_note_010.pdf",
        ):
            upload(client, PDF_DIR / name, params={"process": "true"})
        return client

    def test_the_register_lists_invoices(self, loaded: TestClient):
        body = loaded.get("/invoices").json()
        assert body["count"] == 4
        assert body["limit"] == 50
        numbers = {row["invoice_number"] for row in body["invoices"]}
        assert "FA-2026-0147" in numbers
        # Amounts are strings, not JSON floats.
        totals = [row["total"] for row in body["invoices"] if row["total"]]
        assert all(isinstance(value, str) for value in totals)
        assert "1591.923" in totals

    def test_the_register_filters_by_state(self, loaded: TestClient):
        body = loaded.get("/invoices", params={"state": "needs_review"}).json()
        assert body["count"] == 1
        assert body["invoices"][0]["invoice_number"] == "FA-2026-0152"

    def test_the_register_filters_by_exception_code(self, loaded: TestClient):
        body = loaded.get("/invoices", params={"exception_code": "TOTAL_MISMATCH"}).json()
        assert [row["invoice_number"] for row in body["invoices"]] == ["FA-2026-0152"]
        assert loaded.get(
            "/invoices", params={"exception_code": "PO_NOT_FOUND"}
        ).json()["count"] == 0

    def test_paging_works(self, loaded: TestClient):
        first = loaded.get("/invoices", params={"limit": 2, "offset": 0}).json()
        second = loaded.get("/invoices", params={"limit": 2, "offset": 2}).json()
        assert first["count"] == 2 and second["count"] == 2
        ids = {row["invoice_id"] for row in first["invoices"]} | {
            row["invoice_id"] for row in second["invoices"]
        }
        assert len(ids) == 4

    def test_one_invoice_carries_its_whole_history(self, loaded: TestClient):
        invoice_id = loaded.get(
            "/invoices", params={"state": "needs_review"}
        ).json()["invoices"][0]["invoice_id"]
        detail = loaded.get(f"/invoices/{invoice_id}").json()

        assert detail["invoice_number"] == "FA-2026-0152"
        assert len(detail["lines"]) == 2
        assert detail["lines"][0]["description"]
        codes = {check["code"] for check in detail["checks"]}
        assert "TOTAL_MISMATCH" in codes
        failed = [c for c in detail["checks"] if c["status"] == "fail"]
        assert failed[0]["is_current"] is True
        assert [r["revision_number"] for r in detail["revisions"]] == [1]
        event_types = {event["event_type"] for event in detail["events"]}
        assert {"submission_received", "invoice_created", "validation_completed"} <= event_types

    def test_the_summary_reconciles_with_the_register(self, loaded: TestClient):
        summary = loaded.get("/reports/summary").json()
        assert summary["submissions_received"] == 4
        assert summary["unique_invoices"] == 4
        assert summary["unsupported_invoices"] == 1
        assert summary["eligible_invoices"] == 3
        assert summary["validated_invoices"] == 2
        assert summary["needs_review_invoices"] == 1
        # Rates are strings (decimals) with the documented denominator.
        assert summary["review_rate"] == "0.3333"
        assert "Synthetic" in summary["data_note"]
        codes = {row["code"] for row in summary["exceptions"]}
        assert {"TOTAL_MISMATCH", "UNSUPPORTED_DOCUMENT"} <= codes

    def test_the_openapi_document_is_served(self, loaded: TestClient):
        document = loaded.get("/openapi.json").json()
        assert document["info"]["title"]
        assert "synthetic" in document["info"]["description"].lower()
        assert "/submissions/{submission_id}/process" in document["paths"]
