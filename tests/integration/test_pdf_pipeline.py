"""PDFs through the whole pipeline, against a live database.

The point of these is that a document and a structured payload take the same
path once extraction has run: same rules, same persistence, same outcomes.
What is additional for a document is its provenance — the text the extractor
saw and where each value was read — and that it is stored.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from invoice_automation.config import PROJECT_ROOT
from invoice_automation.extraction import extract_invoice
from invoice_automation.models import RawInvoice, SubmissionInput
from invoice_automation.pipeline import process_structured_submission
from invoice_automation.rules.engine import BusinessState

pytestmark = pytest.mark.integration

PDF_DIR = PROJECT_ROOT / "data" / "fixtures" / "pdf"
# The layout the extractor is not taught, held out of the frozen test split.
UNSEEN_DIR = PROJECT_ROOT / "data" / "corpus" / "pdf"
PDF_MANIFEST = PROJECT_ROOT / "data" / "ground_truth" / "pdf_cases.json"


def ingest(conn, path: Path, *, idempotency_key: str | None = None):
    """Extract a PDF and process it, as the CLI does."""
    raw_bytes = path.read_bytes()
    extraction = extract_invoice(path)
    submission = SubmissionInput(
        source="cli",
        original_file_name=path.name,
        file_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        idempotency_key=idempotency_key,
        storage_path=f"data/fixtures/pdf/{path.name}",
    )
    result = process_structured_submission(
        conn,
        extraction.raw,
        submission,
        raw_text=extraction.raw_text,
        field_evidence=extraction.evidence_payload(),
        media_type="application/pdf",
        byte_size=len(raw_bytes),
    )
    return result, extraction


def scalar(conn, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    return list(row.values())[0]


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(PDF_MANIFEST.read_text(encoding="utf-8"))


class TestOnePdf:
    def test_a_clean_invoice_pdf_is_validated(self, seeded):
        result, extraction = ingest(seeded, PDF_DIR / "valid_001.pdf")
        assert extraction.layout == "atlas"
        assert result.state is BusinessState.VALIDATED
        assert result.failed_codes == ()

        row = seeded.execute(
            "SELECT * FROM v_invoice_register WHERE invoice_id = %s",
            (result.invoice_id,),
        ).fetchone()
        assert row["invoice_number"] == "FA-2026-0147"
        assert str(row["total"]) == "1591.923"

    def test_the_document_is_stored_as_a_pdf_with_its_text(self, seeded):
        result, extraction = ingest(seeded, PDF_DIR / "valid_001.pdf")
        document = seeded.execute(
            """
            SELECT d.media_type, d.byte_size, d.raw_text, d.extraction_method,
                   d.content_sha256, d.storage_path
            FROM documents d
            JOIN submissions s ON s.document_id = d.document_id
            WHERE s.submission_id = %s
            """,
            (result.submission_id,),
        ).fetchone()
        assert document["media_type"] == "application/pdf"
        assert document["byte_size"] > 0
        assert document["extraction_method"] == "pdf_text:v1/atlas"
        assert document["storage_path"].endswith("valid_001.pdf")
        # The text the extractor saw is kept, so a disputed value can be
        # checked without re-running extraction.
        assert "Total TTC" in document["raw_text"]
        assert "FA-2026-0147" in document["raw_text"]

    def test_field_evidence_is_stored_on_the_revision(self, seeded):
        result, _ = ingest(seeded, PDF_DIR / "valid_001.pdf")
        evidence = seeded.execute(
            "SELECT field_evidence FROM invoice_revisions WHERE invoice_id = %s",
            (result.invoice_id,),
        ).fetchone()["field_evidence"]

        by_field = {item["field_name"]: item for item in evidence}
        assert {"invoice_number", "subtotal", "tax_total", "total"} <= set(by_field)
        total = by_field["total"]
        assert total["page_number"] == 1
        assert "1 591,923" in total["source_text"]
        assert len(total["bbox"]) == 4

    def test_a_structured_payload_stores_no_evidence(self, seeded):
        """Evidence is for documents; a payload has no document behind it."""
        path = PROJECT_ROOT / "data" / "fixtures" / "structured" / "valid_002.json"
        raw_bytes = path.read_bytes()
        invoice = RawInvoice.model_validate(json.loads(raw_bytes))
        submission = SubmissionInput(
            source="cli",
            original_file_name=path.name,
            file_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            storage_path=f"data/fixtures/structured/{path.name}",
        )
        result = process_structured_submission(seeded, invoice, submission)
        row = seeded.execute(
            "SELECT field_evidence, raw_values FROM invoice_revisions WHERE invoice_id = %s",
            (result.invoice_id,),
        ).fetchone()
        assert row["field_evidence"] == []
        assert row["raw_values"]["total"] == "1826.174"


class TestPdfGroundTruth:
    def test_every_labelled_pdf_produces_its_expected_outcome(self, seeded, manifest):
        mismatches = []
        for case in manifest["cases"]:
            result, extraction = ingest(seeded, PDF_DIR / case["pdf"])
            actual = (str(result.state), list(result.failed_codes), extraction.layout)
            expected = (
                case["expected_state"],
                case["expected_failed_codes"],
                case["layout"],
            )
            if actual != expected:
                mismatches.append((case["case_id"], expected, actual))
        assert mismatches == []

    def test_the_outcomes_match_the_structured_run(self, seeded, manifest):
        """A document and its payload must reach the same business state.

        This is what the extraction seam is for: if these ever diverge, either
        extraction lost something or the rules depend on how a value was
        formatted.
        """
        structured_manifest = json.loads(
            (
                PROJECT_ROOT / "data" / "ground_truth" / "structured_cases.json"
            ).read_text(encoding="utf-8")
        )
        expected_by_case = {
            case["case_id"]: (case["expected_state"], case["expected_failed_codes"])
            for case in structured_manifest["cases"]
        }
        for case in manifest["cases"]:
            result, _ = ingest(seeded, PDF_DIR / case["pdf"])
            assert (str(result.state), list(result.failed_codes)) == expected_by_case[
                case["case_id"]
            ], case["case_id"]


class TestPdfDuplicates:
    def test_an_identical_pdf_is_linked_not_reprocessed(self, seeded):
        first, _ = ingest(seeded, PDF_DIR / "valid_001.pdf")
        again, _ = ingest(seeded, PDF_DIR / "exact_file_duplicate_016.pdf")

        assert again.state is BusinessState.DUPLICATE
        assert again.failed_codes == ("EXACT_FILE_DUPLICATE",)
        assert again.duplicate_of_submission_id == first.submission_id
        assert scalar(seeded, "SELECT count(*) AS n FROM invoices") == 1
        assert scalar(seeded, "SELECT count(*) AS n FROM invoice_revisions") == 1
        assert scalar(seeded, "SELECT count(*) AS n FROM documents") == 2

    def test_replaying_a_pdf_with_the_same_key_changes_nothing(self, seeded):
        first, _ = ingest(seeded, PDF_DIR / "valid_001.pdf", idempotency_key="pdf-1")
        second, _ = ingest(seeded, PDF_DIR / "valid_001.pdf", idempotency_key="pdf-1")
        assert second.replayed is True
        assert second.invoice_id == first.invoice_id
        assert scalar(seeded, "SELECT count(*) AS n FROM submissions") == 1


class TestUnreadableLayout:
    def test_a_document_in_an_unknown_layout_is_recorded_for_review(self, seeded):
        result, extraction = ingest(seeded, UNSEEN_DIR / "tes_0004.pdf")

        assert extraction.layout is None
        assert result.state is BusinessState.NEEDS_REVIEW
        assert result.failed_codes == ("REQUIRED_FIELD_MISSING",)

        row = seeded.execute(
            """
            SELECT r.extraction_method, r.invoice_number_normalized, r.total,
                   d.raw_text
            FROM invoice_revisions r
            JOIN documents d ON d.document_id = r.document_id
            WHERE r.invoice_id = %s
            """,
            (result.invoice_id,),
        ).fetchone()
        assert row["extraction_method"].endswith("unknown-layout")
        # Nothing was invented, and the source text is kept so a person can
        # read the document that defeated the extractor.
        assert row["total"] is None
        assert row["raw_text"]  # the text that defeated the extractor is kept

    def test_it_is_kept_separate_from_an_excluded_document_kind(self, seeded):
        unreadable, _ = ingest(seeded, UNSEEN_DIR / "tes_0009.pdf")
        excluded, _ = ingest(seeded, PDF_DIR / "credit_note_010.pdf")

        assert unreadable.state is BusinessState.NEEDS_REVIEW
        assert excluded.state is BusinessState.UNSUPPORTED
        assert excluded.failed_codes == ("UNSUPPORTED_DOCUMENT",)
