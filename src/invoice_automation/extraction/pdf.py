"""Extract invoice values from a digitally generated PDF.

The output is a :class:`~invoice_automation.models.RawInvoice`: the same type
the structured fixtures produce. That is the whole point of the seam. The
validation rules, the persistence and the reporting do not know or care
whether the values came from a JSON payload or from a document.

Nothing here guesses. A field the layout does not yield is left as ``None``
and the rules report it; a document whose layout cannot be identified yields
almost nothing and goes to review, rather than being read with the wrong set
of labels.

This is deterministic text extraction, not OCR and not a model. Scanned
documents are not handled, and no language model is involved. If one is added
later it has to beat the baseline this produces, measured on the same set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import RawInvoice, RawLine
from .layouts import DOCUMENT_TITLES, detect_layout
from .words import Document, Line, find_table, load_document, trailing_amount

#: Version of this extractor, recorded on every document and revision so a
#: later change can be told apart in the data.
EXTRACTION_METHOD = "pdf_text:v1"

TOTALS_STOP_LABELS = (
    "Total HT",
    "Sous-total",
    "Montant hors taxe",
    "TVA",
    "Taxe",
    "Dont taxe",
    "Total TTC",
    "Total à payer",
    "Document synthetique",
)


@dataclass(frozen=True)
class FieldEvidence:
    """Where a value was read from, so a reviewer can check it."""

    field_name: str
    page_number: int
    source_text: str
    bbox: tuple[float, float, float, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "page_number": self.page_number,
            "source_text": self.source_text,
            "bbox": [round(value, 2) for value in self.bbox],
        }


@dataclass(frozen=True)
class ExtractionResult:
    raw: RawInvoice
    raw_text: str
    layout: str | None
    method: str
    evidence: tuple[FieldEvidence, ...] = ()
    layout_scores: dict[str, int] = field(default_factory=dict)

    def evidence_payload(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.evidence]


def detect_document_type(document: Document) -> tuple[str, Line | None]:
    """Read the document kind from its title.

    A credit note says "AVOIR" on its face. Reading the kind from the document
    rather than assuming "invoice" is what lets the rules exclude it with an
    explicit code instead of validating it as something it is not.
    """
    for line in document.lines:
        normalised = _fold(line.text)
        for title, document_type in DOCUMENT_TITLES:
            if normalised.startswith(title):
                return document_type, line
    return "unknown", None


def _fold(text: str) -> str:
    """Case-fold and strip accents, so AVOIR matches Avoir and ÉMISE émise."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.split()).casefold()


def _amount_at(document: Document, labels: tuple[str, ...]) -> tuple[str | None, str | None, Line | None]:
    """Read an amount from a totals line.

    Returns (value, currency, line). When the line carries no number, the
    remaining text is returned as the value instead of ``None``: a document
    that says "mille deux cents" where an amount belongs should be recorded
    with what it actually says, so the reviewer sees it and the rules report
    it as unparseable rather than merely absent.
    """
    found = document.find_label(labels)
    if found is None:
        return None, None, None
    line, _, consumed = found
    remainder = line.remainder_after(consumed).strip().lstrip(":").strip()
    if not remainder:
        return None, None, line
    amount, currency = trailing_amount(remainder)
    if amount is None:
        return remainder, currency, line
    return amount, currency, line


def extract_invoice(path: str | Path) -> ExtractionResult:
    """Read one PDF into a :class:`RawInvoice` with field evidence."""
    document = load_document(str(path))
    document_type, title_line = detect_document_type(document)
    spec, scores = detect_layout(document)

    evidence: list[FieldEvidence] = []

    def record(field_name: str, line: Line | None, value: str | None) -> None:
        if line is not None and value:
            evidence.append(
                FieldEvidence(
                    field_name=field_name,
                    page_number=line.page_number,
                    source_text=line.text,
                    bbox=line.bbox,
                )
            )

    if title_line is not None:
        record("document_type", title_line, document_type)

    if spec is None:
        # No layout recognised. Report what the document is and nothing else;
        # the rules will send it to review for a missing-field failure, which
        # is the honest outcome for a document this version cannot read.
        return ExtractionResult(
            raw=RawInvoice(
                document_type=document_type,
                extraction_method=f"{EXTRACTION_METHOD}/unknown-layout",
            ),
            raw_text=document.raw_text,
            layout=None,
            method=f"{EXTRACTION_METHOD}/unknown-layout",
            evidence=tuple(evidence),
            layout_scores=scores,
        )

    header: dict[str, str | None] = {}
    for field_name, rule in spec.header_rules().items():
        found = rule(document)
        if found is None:
            header[field_name] = None
            continue
        value, line = found
        header[field_name] = value
        record(field_name, line, value)

    subtotal, _, subtotal_line = _amount_at(document, spec.subtotal_labels)
    tax_total, _, tax_line = _amount_at(document, spec.tax_labels)
    total, total_currency, total_line = _amount_at(document, spec.total_labels)
    record("subtotal", subtotal_line, subtotal)
    record("tax_total", tax_line, tax_total)
    record("total", total_line, total)

    currency = header.get("currency")
    if not currency and total_currency:
        # The compact layout has no currency field: it writes the code after
        # the total. Reading it there is a documented fallback, not a guess.
        currency = total_currency
        record("currency", total_line, currency)

    table = find_table(document, spec.columns, TOTALS_STOP_LABELS)
    lines: list[RawLine] = []
    if table is not None:
        for number, row in enumerate(table.rows, start=1):
            lines.append(
                RawLine(
                    line_number=number,
                    description=row.cells.get("description") or None,
                    quantity=row.cells.get("quantity") or None,
                    unit_price=row.cells.get("unit_price") or None,
                    line_amount=row.cells.get("line_amount") or None,
                )
            )
            record(f"line_items[{number}]", row.line, row.line.text)

    method = f"{EXTRACTION_METHOD}/{spec.name}"
    raw = RawInvoice(
        document_type=document_type,
        invoice_number=header.get("invoice_number"),
        supplier_reference=header.get("supplier_reference"),
        supplier_name=header.get("supplier_name"),
        invoice_date=header.get("invoice_date"),
        due_date=header.get("due_date"),
        currency=currency,
        purchase_order_number=header.get("purchase_order_number"),
        subtotal=subtotal,
        tax_total=tax_total,
        total=total,
        line_items=lines,
        extraction_method=method,
    )
    return ExtractionResult(
        raw=raw,
        raw_text=document.raw_text,
        layout=spec.name,
        method=method,
        evidence=tuple(evidence),
        layout_scores=scores,
    )
