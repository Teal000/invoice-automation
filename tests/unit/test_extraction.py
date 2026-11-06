"""PDF extraction, against the committed synthetic documents.

No database needed. These tests use the generated fixtures, so they check the
extractor against the documents it is meant to read, and the held-out layout
checks what it does with one it is not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from invoice_automation.config import PROJECT_ROOT, RuleConfig
from invoice_automation.extraction import extract_invoice
from invoice_automation.extraction.words import trailing_amount
from invoice_automation.models import (
    UNCLASSIFIED_DOCUMENT_TYPE,
    SubmissionInput,
    normalize_invoice,
)
from invoice_automation.rules import ValidationContext, run_rules
from invoice_automation.rules.base import RuleStatus
from invoice_automation.rules.engine import BusinessState

PDF_DIR = PROJECT_ROOT / "data" / "fixtures" / "pdf"
CORPUS_PDFS = PROJECT_ROOT / "data" / "corpus" / "pdf"
CORPUS_MANIFEST = PROJECT_ROOT / "data" / "manifests" / "corpus_splits.json"
STRUCTURED = PROJECT_ROOT / "data" / "fixtures" / "structured"
PDF_MANIFEST = PROJECT_ROOT / "data" / "ground_truth" / "pdf_cases.json"

MANIFEST = json.loads(PDF_MANIFEST.read_text(encoding="utf-8"))
CORPUS = json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))
UNSEEN_LAYOUT = CORPUS["unseen_layout"]
# Computed at module scope on purpose: a comprehension inside a class body
# cannot see the class's own names, so doing this in the class picked up the
# wrong manifest entirely.
UNSEEN_CASES = [c for c in CORPUS["cases"] if c["layout"] == UNSEEN_LAYOUT][:3]
CASES = {case["case_id"]: case for case in MANIFEST["cases"]}


def extract(case_id: str):
    return extract_invoice(PDF_DIR / CASES[case_id]["pdf"])


class TestTrailingAmount:
    """The tail-reading rule, which several layouts depend on."""

    def test_rejoins_a_french_thousands_group(self):
        assert trailing_amount("1 337,750") == ("1 337,750", None)

    def test_ignores_a_rate_inside_the_label(self):
        # "Taxe (19%) 291,574" must not yield 19.
        assert trailing_amount("(19%) 291,574") == ("291,574", None)

    def test_takes_a_trailing_currency_code_off_first(self):
        assert trailing_amount("1 591,923 TND") == ("1 591,923", "TND")

    def test_returns_nothing_for_text(self):
        assert trailing_amount("mille deux cents") == (None, None)

    def test_returns_nothing_for_an_empty_tail(self):
        assert trailing_amount("") == (None, None)


class TestLayoutDetection:
    @pytest.mark.parametrize("case_id", sorted(CASES))
    def test_each_document_is_read_in_the_layout_it_was_drawn_in(self, case_id):
        result = extract(case_id)
        assert result.layout == CASES[case_id]["layout"]

    def test_the_winning_layout_scores_higher_than_the_others(self):
        result = extract("valid_001")
        scores = result.layout_scores
        assert scores["atlas"] > scores["medina"]
        assert scores["atlas"] > scores["carthage"]


class TestAtlasLayout:
    def test_every_header_field_is_read(self):
        raw = extract("valid_001").raw
        assert raw.document_type == "invoice"
        assert raw.invoice_number == "FA-2026-0147"
        assert raw.supplier_reference == "SUP-001"
        assert raw.supplier_name == "Fournitures Atlas SARL (synthetic)"
        assert raw.invoice_date == "12/03/2026"
        assert raw.due_date == "11/04/2026"
        assert raw.currency == "TND"
        assert raw.purchase_order_number == "PO-2026-0031"

    def test_amounts_are_read_with_their_thousands_group(self):
        raw = extract("valid_001").raw
        assert raw.subtotal == "1 337,750"
        assert raw.tax_total == "254,173"
        assert raw.total == "1 591,923"

    def test_the_supplier_label_does_not_match_the_supplier_code_line(self):
        """"Fournisseur :" and "Code fournisseur :" are different fields."""
        raw = extract("valid_001").raw
        assert raw.supplier_name == "Fournitures Atlas SARL (synthetic)"
        assert raw.supplier_reference == "SUP-001"

    def test_line_items_are_read_into_columns(self):
        lines = extract("valid_001").raw.line_items
        assert len(lines) == 2
        assert lines[0].description == "Ramettes papier A4 80g"
        assert lines[0].quantity == "10"
        assert lines[0].unit_price == "120,050"
        assert lines[0].line_amount == "1 200,500"
        assert lines[1].description == "Cartouches encre noire"
        assert lines[1].line_amount == "137,250"

    def test_a_label_with_no_value_yields_nothing_not_zero(self):
        raw = extract("missing_total_003").raw
        assert raw.total is None
        assert raw.subtotal == "1 337,750"


class TestMedinaLayout:
    def test_header_fields_are_read_from_the_right_aligned_grid(self):
        raw = extract("valid_002").raw
        assert raw.invoice_number == "PM-2026-0552"
        assert raw.supplier_reference == "SUP-002"
        assert raw.invoice_date == "12/03/2026"
        assert raw.currency == "TND"
        assert raw.purchase_order_number == "PO-2026-0032"

    def test_the_rate_in_the_tax_label_is_not_read_as_the_amount(self):
        raw = extract("valid_002").raw
        assert raw.tax_total == "291,574"

    def test_the_supplier_name_is_read_from_the_heading(self):
        raw = extract("valid_002").raw
        assert raw.supplier_name == "Papeterie Medina SA (synthetic)"

    def test_a_credit_note_says_so_on_its_face(self):
        raw = extract("credit_note_010").raw
        assert raw.document_type == "credit_note"

    def test_unreadable_text_is_kept_rather_than_dropped(self):
        """The document says "mille deux cents" where an amount belongs."""
        raw = extract("unparseable_subtotal_004").raw
        assert raw.subtotal == "mille deux cents"
        # The rules, not the extractor, decide what that means.
        normalised = normalize_invoice(raw, RuleConfig())
        assert normalised.normalized.subtotal is None
        assert ("subtotal", "unparseable") in [
            (p.field_name, p.reason) for p in normalised.problems
        ]


class TestCarthageLayout:
    def test_the_invoice_number_is_read_from_the_title_line(self):
        raw = extract("unknown_supplier_005").raw
        assert raw.invoice_number == "XX-2026-0001"

    def test_the_supplier_code_is_read_from_the_brackets(self):
        raw = extract("unknown_supplier_005").raw
        assert raw.supplier_reference == "SUP-999"
        assert raw.supplier_name == "Societe Inconnue (synthetic)"

    def test_the_currency_falls_back_to_the_code_after_the_total(self):
        """This layout has no currency field at all."""
        raw = extract("unknown_supplier_005").raw
        assert raw.currency == "TND"

    def test_inline_dates_are_read(self):
        raw = extract("date_inconsistent_014").raw
        assert raw.invoice_date == "12/03/2026"
        assert raw.due_date == "01/03/2026"


class TestEvidence:
    def test_every_read_field_records_where_it_came_from(self):
        result = extract("valid_001")
        fields = {item.field_name for item in result.evidence}
        assert {"invoice_number", "total", "subtotal", "currency"} <= fields

    def test_evidence_carries_the_page_and_the_source_line(self):
        result = extract("valid_001")
        total = next(i for i in result.evidence if i.field_name == "total")
        assert total.page_number == 1
        assert "Total TTC" in total.source_text
        assert "1 591,923" in total.source_text
        x0, top, x1, bottom = total.bbox
        assert x0 < x1 and top < bottom

    def test_evidence_is_serialisable_for_storage(self):
        payload = extract("valid_001").evidence_payload()
        assert payload and all(
            set(item) == {"field_name", "page_number", "source_text", "bbox"}
            for item in payload
        )
        json.dumps(payload)  # must not raise

    def test_the_extraction_method_records_the_layout(self):
        assert extract("valid_001").raw.extraction_method == "pdf_text:v1/atlas"
        assert extract("valid_002").raw.extraction_method == "pdf_text:v1/medina"


class TestGroundTruthAgreement:
    @pytest.mark.parametrize("case_id", sorted(CASES))
    def test_extraction_reproduces_the_payload_it_was_drawn_from(self, case_id):
        """Normalised values must match the payload the PDF was rendered from.

        Compared after normalisation, so a date is compared as a date and an
        amount as a decimal: this asks whether the meaning survived, not
        whether the formatting did.
        """
        config = RuleConfig()
        truth_payload = json.loads(
            (STRUCTURED / CASES[case_id]["ground_truth"]).read_text(encoding="utf-8")
        )
        from invoice_automation.models import RawInvoice

        truth = normalize_invoice(RawInvoice.model_validate(truth_payload), config).normalized
        predicted = normalize_invoice(extract(case_id).raw, config).normalized

        for field in (
            "document_type",
            "invoice_number_normalized",
            "supplier_reference_normalized",
            "invoice_date",
            "due_date",
            "currency",
            "purchase_order_number_normalized",
            "subtotal",
            "tax_total",
            "total",
        ):
            assert getattr(predicted, field) == getattr(truth, field), field

        assert [
            (line.description, line.quantity, line.unit_price, line.line_amount)
            for line in predicted.lines
        ] == [
            (line.description, line.quantity, line.unit_price, line.line_amount)
            for line in truth.lines
        ]


class TestUnseenLayout:
    """The layout held out of the frozen test split. It must fail, not guess.

    These assertions are what stop the held-out layout quietly becoming a
    familiar one: teaching the extractor `kairouan` would break them, and the
    benchmark's only generalising number along with it.
    """

    UNSEEN = UNSEEN_CASES

    @pytest.mark.parametrize("case", UNSEEN, ids=lambda c: c["case_id"])
    def test_no_layout_is_claimed(self, case):
        result = extract_invoice(CORPUS_PDFS / case["pdf"])
        assert result.layout is None
        assert result.raw.extraction_method.endswith("unknown-layout")

    @pytest.mark.parametrize("case", UNSEEN, ids=lambda c: c["case_id"])
    def test_no_values_are_invented(self, case):
        result = extract_invoice(CORPUS_PDFS / case["pdf"])
        raw = result.raw
        # The kind may still be read: it is printed on the face of the
        # document and does not depend on knowing the layout. Reporting
        # "this is an invoice I cannot parse" is better than reporting
        # nothing, and it keeps the document out of the excluded-kind bucket.
        assert raw.document_type in ("invoice", UNCLASSIFIED_DOCUMENT_TYPE)
        # No *value* is invented, which is the property that matters.
        for field in (
            "invoice_number",
            "supplier_reference",
            "invoice_date",
            "due_date",
            "currency",
            "purchase_order_number",
            "subtotal",
            "tax_total",
            "total",
        ):
            assert getattr(raw, field) is None, field
        assert raw.line_items == []

    def test_the_document_is_routed_to_review_not_to_unsupported(self):
        """An unreadable document is a review case, not an excluded kind."""
        config = RuleConfig()
        result = extract_invoice(CORPUS_PDFS / UNSEEN_CASES[0]["pdf"])
        normalised = normalize_invoice(result.raw, config)
        outcome = run_rules(
            ValidationContext(
                invoice=normalised.normalized,
                submission=SubmissionInput(
                    original_file_name="unseen.pdf", file_sha256="c" * 64
                ),
                config=config,
                problems=normalised.problems,
            )
        )
        assert outcome.state is BusinessState.NEEDS_REVIEW
        assert outcome.failed_codes == ("REQUIRED_FIELD_MISSING",)
        # Never reported as an excluded document kind. Whether the kind was
        # read or not, "we could not parse this" is a review case, and the
        # two must not be confused.
        assert outcome.code_status("UNSUPPORTED_DOCUMENT") is not RuleStatus.FAIL


class TestUnrelatedDocument:
    def test_a_document_that_is_not_an_invoice_is_not_read_as_one(self, tmp_path: Path):
        from reportlab.pdfgen.canvas import Canvas

        path = tmp_path / "letter.pdf"
        canvas = Canvas(str(path), invariant=1)
        canvas.drawString(72, 700, "Note interne concernant les horaires d'ouverture")
        canvas.drawString(72, 680, "Merci de prendre note des nouveaux horaires.")
        canvas.save()

        result = extract_invoice(path)
        assert result.layout is None
        assert result.raw.invoice_number is None
        assert result.raw.total is None
