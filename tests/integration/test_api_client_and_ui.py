"""The API client, and the review screen driven through it.

The client is exercised against the real endpoints: FastAPI's TestClient is
itself an ``httpx.Client``, so injecting it means these tests cover the actual
request and response shapes rather than a mock of them.

The screen is run headlessly with Streamlit's own AppTest, with the client
injected. That catches the thing most likely to break a Streamlit app — an
attribute or a key that does not exist — which a human clicking through would
find and a unit test would not.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from invoice_automation.api_client import ApiClient, ApiError
from invoice_automation.api.main import API_KEY_HEADER, create_app
from invoice_automation.config import PROJECT_ROOT, Settings

pytestmark = pytest.mark.integration

PDF_DIR = PROJECT_ROOT / "data" / "fixtures" / "pdf"
APP_FILE = PROJECT_ROOT / "apps" / "review_ui" / "app.py"
API_KEY = "client-test-credential"


@pytest.fixture
def api(settings: Settings, test_database: str, tmp_path: Path) -> TestClient:
    app = create_app(
        Settings(db_name=test_database, api_key=API_KEY, storage_root=tmp_path / "s")
    )
    return TestClient(app)


@pytest.fixture
def client(api: TestClient, seeded) -> ApiClient:
    return ApiClient(http=api, api_key=API_KEY, actor="reviewer:test")


def upload(client: ApiClient, name: str, *, process: bool = True) -> dict:
    path = PDF_DIR / name
    return client.upload(
        file_name=path.name,
        content=path.read_bytes(),
        media_type="application/pdf",
        process=process,
    )


class TestApiClient:
    def test_health_needs_no_credential(self, api: TestClient, seeded):
        anonymous = ApiClient(http=api)
        assert anonymous.health()["status"] == "ok"

    def test_a_missing_credential_raises_with_the_service_code(
        self, api: TestClient, seeded
    ):
        anonymous = ApiClient(http=api)
        with pytest.raises(ApiError) as caught:
            anonymous.invoices()
        assert caught.value.status_code == 401
        assert caught.value.code == "unauthenticated"

    def test_upload_and_process(self, client: ApiClient):
        body = upload(client, "valid_001.pdf", process=False)
        assert body["outcome"] == "pending"
        outcome = client.process(body["submission_id"])
        assert outcome["state"] == "validated"
        assert client.submission(body["submission_id"])["outcome"] == "validated"

    def test_the_register_and_detail_round_trip(self, client: ApiClient):
        upload(client, "total_mismatch_008.pdf")
        listing = client.invoices(state="needs_review")
        assert listing["count"] == 1
        detail = client.invoice(listing["invoices"][0]["invoice_id"])
        assert detail["invoice_number"] == "FA-2026-0152"
        assert any(c["code"] == "TOTAL_MISMATCH" for c in detail["checks"])

    def test_a_correction_through_the_client(self, client: ApiClient):
        upload(client, "total_mismatch_008.pdf")
        detail = client.invoice(
            client.invoices(state="needs_review")["invoices"][0]["invoice_id"]
        )
        corrected = {
            "invoice_number": detail["invoice_number"],
            "supplier_reference": detail["supplier_reference"],
            "invoice_date": "12/03/2026",
            "due_date": "11/04/2026",
            "currency": "TND",
            "purchase_order_number": detail["purchase_order_number"],
            "subtotal": "1337.750",
            "tax_total": "254.173",
            "total": "1591.923",
            "line_items": [
                {
                    "line_number": line["line_number"],
                    "description": line["description"],
                    "quantity": line["quantity"],
                    "unit_price": line["unit_price"],
                    "line_amount": line["line_amount"],
                }
                for line in detail["lines"]
            ],
        }
        outcome = client.correct(
            detail["invoice_id"],
            expected_revision_number=detail["current_revision_number"],
            invoice=corrected,
            reason="Total re-keyed from the document.",
        )
        assert outcome["state"] == "validated"
        assert outcome["actor"] == "api:reviewer:test"

    def test_a_stale_revision_raises_a_conflict_the_caller_can_act_on(
        self, client: ApiClient
    ):
        upload(client, "total_mismatch_008.pdf")
        invoice_id = client.invoices(state="needs_review")["invoices"][0]["invoice_id"]
        client.review_action(
            invoice_id,
            action="accept_exception",
            expected_revision_number=1,
            reason="Accepted.",
        )
        with pytest.raises(ApiError) as caught:
            client.review_action(
                invoice_id,
                action="reject",
                expected_revision_number=99,
                reason="Stale.",
            )
        assert caught.value.is_conflict
        assert caught.value.code == "revision_conflict"
        assert caught.value.detail["current_revision_number"] == 1

    def test_the_summary_and_notifications_are_readable(self, client: ApiClient):
        upload(client, "total_mismatch_008.pdf")
        summary = client.summary()
        assert summary["needs_review_invoices"] == 1
        notifications = client.notifications(status="pending")
        assert notifications["count"] == 1
        assert "delivers nothing" in notifications["delivery_note"]

    def test_recovery_is_reachable(self, client: ApiClient):
        report = client.recover()
        assert report["examined"] == 0


class TestReviewScreen:
    def run_app(self, client: ApiClient | None, **session) -> AppTest:
        app = AppTest.from_file(str(APP_FILE), default_timeout=30)
        app.session_state["api_client"] = client
        for key, value in session.items():
            app.session_state[key] = value
        return app.run()

    def test_it_renders_the_queue(self, client: ApiClient):
        upload(client, "total_mismatch_008.pdf")
        app = self.run_app(client)

        assert not app.exception
        assert app.header[0].value == "Invoices needing review"
        # The screen says what the data is, every time it is opened.
        assert "Synthetic portfolio data" in app.sidebar.caption[0].value
        # And it reports that the service answered, rather than assuming so.
        assert "API reachable" in app.success[0].value

    def test_it_degrades_instead_of_crashing_when_the_api_is_down(self):
        """A reviewer should be told the service is unreachable, not shown a stack trace."""
        broken = ApiClient.for_base_url("http://127.0.0.1:1", "irrelevant")
        app = self.run_app(broken)
        assert not app.exception
        assert any("unreachable" in err.value for err in app.error)

    def test_an_invoice_opens_with_its_failed_check_visible(self, client: ApiClient):
        upload(client, "total_mismatch_008.pdf")
        invoice_id = client.invoices(state="needs_review")["invoices"][0]["invoice_id"]
        app = self.run_app(client, invoice_id=invoice_id, actor="nidhal")
        app.sidebar.radio[0].set_value("Invoice").run()

        assert not app.exception
        errors = " ".join(err.value for err in app.error)
        # The rule, the expectation and what the document said, all on screen.
        assert "TOTAL_MISMATCH" in errors
        assert "1591.923" in errors
        assert "1600.000" in errors

    def test_it_asks_for_a_name_before_offering_decisions(self, client: ApiClient):
        upload(client, "total_mismatch_008.pdf")
        invoice_id = client.invoices(state="needs_review")["invoices"][0]["invoice_id"]
        app = self.run_app(client, invoice_id=invoice_id)
        app.sidebar.radio[0].set_value("Invoice").run()

        warnings = " ".join(w.value for w in app.warning)
        assert "every action records who took it" in warnings

    def test_the_overview_states_its_denominators(self, client: ApiClient):
        upload(client, "valid_001.pdf")
        app = self.run_app(client)
        app.sidebar.radio[0].set_value("Overview").run()

        assert not app.exception
        captions = " ".join(c.value for c in app.caption)
        assert "shares of eligible invoices" in captions
        assert "metric_definitions.md" in captions
