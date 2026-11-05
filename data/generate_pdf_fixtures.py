"""Render the structured fixtures as synthetic invoice PDFs.

    .venv/Scripts/python.exe data/generate_pdf_fixtures.py

Every PDF is generated from a payload in ``data/fixtures/structured``, so the
structured values are the ground truth for extraction: whatever the extractor
reads is compared against the payload the document was drawn from.

Three layout families, deliberately different in wording and geometry:

``atlas``
    Labels in a left column, values to their right. Ruled line-item table with
    the columns Designation / Qte / P.U. HT / Montant HT. Totals bottom right.
``medina``
    Header as a key/value grid at the top right, wider table with the columns
    Description / Quantite / Prix unitaire / Total ligne, and a tax label that
    carries the rate in brackets, which the extractor must not mistake for the
    amount.
``carthage``
    Compact. Header fields inline in running text, supplier code in brackets
    after the name, no currency field at all: the currency appears only as a
    suffix on the total, so the extractor has to fall back to reading it there.

Amounts are written the French way, with a space as the thousands separator
and a comma as the decimal separator. Dates are DD/MM/YYYY. Every page carries
a visible synthetic-data notice.

Output is byte-identical between runs (reportlab ``invariant=1``), which makes
the set reproducible and lets the exact-file duplicate case be a genuine
regeneration of the same document rather than a copied file.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRUCTURED = PROJECT_ROOT / "data" / "fixtures" / "structured"
PDFS = PROJECT_ROOT / "data" / "fixtures" / "pdf"
MANIFEST_IN = PROJECT_ROOT / "data" / "ground_truth" / "structured_cases.json"
MANIFEST_OUT = PROJECT_ROOT / "data" / "ground_truth" / "pdf_cases.json"
UNSEEN_PDFS = PROJECT_ROOT / "data" / "fixtures" / "pdf_unseen"
UNSEEN_MANIFEST_OUT = PROJECT_ROOT / "data" / "ground_truth" / "pdf_unseen_cases.json"

PAGE_WIDTH, PAGE_HEIGHT = A4

SYNTHETIC_NOTICE = (
    "Document synthetique genere pour un projet de demonstration. "
    "Fournisseur, montants et references sont fictifs."
)

#: Which layout each case is drawn in. The duplicate pair shares a layout and
#: a payload, which is what makes their bytes identical.
LAYOUT_BY_CASE = {
    "valid_001": "atlas",
    "valid_002": "medina",
    "missing_total_003": "atlas",
    "unparseable_subtotal_004": "medina",
    "unknown_supplier_005": "carthage",
    "line_total_mismatch_006": "atlas",
    "subtotal_mismatch_007": "medina",
    "total_mismatch_008": "carthage",
    "currency_eur_009": "atlas",
    "credit_note_010": "medina",
    "po_not_found_011": "carthage",
    "po_supplier_mismatch_012": "atlas",
    "po_amount_mismatch_013": "medina",
    "date_inconsistent_014": "carthage",
    "business_duplicate_015": "atlas",
    "exact_file_duplicate_016": "atlas",
}

#: Document titles per kind. A credit note says so on the document, which is
#: how the extractor learns it is not an invoice.
TITLE_BY_DOCUMENT_TYPE = {
    "invoice": "FACTURE",
    "credit_note": "AVOIR",
    "debit_note": "NOTE DE DEBIT",
    "partial_invoice": "FACTURE PARTIELLE",
    "proforma": "FACTURE PROFORMA",
    "statement": "RELEVE DE COMPTE",
}


#: Thousands separator. A plain space, not U+202F or U+00A0: the base-14
#: Helvetica encoding has no glyph for those and reportlab renders U+202F as
#: "n", which turned 1200.500 into "1n200,500" in the first version of this
#: generator. A space also means the extractor has to join numeric tokens
#: that belong to one amount, which is what real invoices demand of it.
THOUSANDS_SEPARATOR = " "


def fr_amount(value: str | None) -> str:
    """Format an amount the French way: 1 591,923.

    A value that is not a number is written through unchanged, because a case
    that deliberately carries unreadable text must show that text.
    """
    if value is None or str(value).strip() == "":
        return ""
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001 - deliberate: pass the text through
        return str(value)
    sign = "-" if number < 0 else ""
    whole, _, fraction = f"{abs(number):.3f}".partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{sign}{THOUSANDS_SEPARATOR.join(groups)},{fraction}"


def fr_date(value: str | None) -> str:
    """Render an ISO date as DD/MM/YYYY, as the documents would carry it."""
    if value is None or str(value).strip() == "":
        return ""
    text = str(value).strip()
    try:
        year, month, day = text.split("-")
        return f"{int(day):02d}/{int(month):02d}/{year}"
    except ValueError:
        return text


def fr_quantity(value: str | None) -> str:
    if value is None or str(value).strip() == "":
        return ""
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return str(value)
    text = format(number.normalize(), "f")
    return text.replace(".", ",")


def _notice(canvas: Canvas) -> None:
    canvas.setFont("Helvetica-Oblique", 7)
    canvas.setFillGray(0.45)
    canvas.drawString(20 * mm, 12 * mm, SYNTHETIC_NOTICE)
    canvas.setFillGray(0)


def draw_atlas(canvas: Canvas, payload: dict) -> None:
    """Labels left, values right; ruled table; totals bottom right."""
    title = TITLE_BY_DOCUMENT_TYPE.get(payload["document_type"], "DOCUMENT")
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(20 * mm, PAGE_HEIGHT - 28 * mm, title)

    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(
        PAGE_WIDTH - 20 * mm, PAGE_HEIGHT - 24 * mm, "Fournitures et bureautique"
    )

    y = PAGE_HEIGHT - 45 * mm
    rows = [
        ("Facture N° :", payload["invoice_number"]),
        ("Date :", fr_date(payload["invoice_date"])),
        ("Echéance :", fr_date(payload["due_date"])),
        ("Devise :", payload["currency"]),
        ("Bon de commande :", payload["purchase_order_number"]),
        ("Fournisseur :", payload["supplier_name"]),
        ("Code fournisseur :", payload["supplier_reference"]),
    ]
    for label, value in rows:
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(20 * mm, y, label)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(62 * mm, y, "" if value is None else str(value))
        y -= 6 * mm

    y -= 4 * mm
    columns = (20 * mm, 110 * mm, 130 * mm, 165 * mm)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(columns[0], y, "Désignation")
    canvas.drawRightString(columns[1] + 12 * mm, y, "Qté")
    canvas.drawRightString(columns[2] + 22 * mm, y, "P.U. HT")
    canvas.drawRightString(columns[3] + 25 * mm, y, "Montant HT")
    y -= 2 * mm
    canvas.line(20 * mm, y, PAGE_WIDTH - 20 * mm, y)
    y -= 6 * mm

    canvas.setFont("Helvetica", 9)
    for line in payload["line_items"]:
        canvas.drawString(columns[0], y, line["description"] or "")
        canvas.drawRightString(columns[1] + 12 * mm, y, fr_quantity(line["quantity"]))
        canvas.drawRightString(columns[2] + 22 * mm, y, fr_amount(line["unit_price"]))
        canvas.drawRightString(columns[3] + 25 * mm, y, fr_amount(line["line_amount"]))
        y -= 6 * mm

    y -= 2 * mm
    canvas.line(110 * mm, y, PAGE_WIDTH - 20 * mm, y)
    y -= 7 * mm
    for label, value in (
        ("Total HT", payload["subtotal"]),
        ("TVA", payload["tax_total"]),
        ("Total TTC", payload["total"]),
    ):
        canvas.setFont("Helvetica-Bold" if label == "Total TTC" else "Helvetica", 9)
        canvas.drawString(120 * mm, y, label)
        canvas.drawRightString(PAGE_WIDTH - 20 * mm, y, fr_amount(value))
        y -= 6 * mm

    _notice(canvas)


def draw_medina(canvas: Canvas, payload: dict) -> None:
    """Header grid at the top right; wider table; tax label carries the rate."""
    title = TITLE_BY_DOCUMENT_TYPE.get(payload["document_type"], "DOCUMENT")
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(18 * mm, PAGE_HEIGHT - 25 * mm, payload["supplier_name"] or "")
    canvas.setFont("Helvetica", 9)
    canvas.drawString(18 * mm, PAGE_HEIGHT - 31 * mm, "Papeterie et mobilier de bureau")

    canvas.setFont("Helvetica-Bold", 22)
    canvas.drawRightString(PAGE_WIDTH - 18 * mm, PAGE_HEIGHT - 25 * mm, title)

    y = PAGE_HEIGHT - 40 * mm
    rows = [
        ("N° de facture", payload["invoice_number"]),
        ("Date d'émission", fr_date(payload["invoice_date"])),
        ("Date d'échéance", fr_date(payload["due_date"])),
        ("Monnaie", payload["currency"]),
        ("Réf. commande", payload["purchase_order_number"]),
        ("Identifiant fournisseur", payload["supplier_reference"]),
    ]
    for label, value in rows:
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(PAGE_WIDTH - 55 * mm, y, label)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawRightString(PAGE_WIDTH - 18 * mm, y, "" if value is None else str(value))
        y -= 5.5 * mm

    y -= 8 * mm
    canvas.setFillGray(0.9)
    canvas.rect(18 * mm, y - 2 * mm, PAGE_WIDTH - 36 * mm, 7 * mm, stroke=0, fill=1)
    canvas.setFillGray(0)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(20 * mm, y, "Description")
    canvas.drawRightString(118 * mm, y, "Quantité")
    canvas.drawRightString(150 * mm, y, "Prix unitaire")
    canvas.drawRightString(PAGE_WIDTH - 20 * mm, y, "Total ligne")
    y -= 8 * mm

    canvas.setFont("Helvetica", 9)
    for line in payload["line_items"]:
        canvas.drawString(20 * mm, y, line["description"] or "")
        canvas.drawRightString(118 * mm, y, fr_quantity(line["quantity"]))
        canvas.drawRightString(150 * mm, y, fr_amount(line["unit_price"]))
        canvas.drawRightString(PAGE_WIDTH - 20 * mm, y, fr_amount(line["line_amount"]))
        y -= 6 * mm

    y -= 6 * mm
    for label, value in (
        ("Sous-total", payload["subtotal"]),
        ("Taxe (19%)", payload["tax_total"]),
        ("Total à payer", payload["total"]),
    ):
        canvas.setFont("Helvetica-Bold" if label.startswith("Total") else "Helvetica", 9)
        canvas.drawRightString(150 * mm, y, label)
        canvas.drawRightString(PAGE_WIDTH - 20 * mm, y, fr_amount(value))
        y -= 6 * mm

    _notice(canvas)


def draw_carthage(canvas: Canvas, payload: dict) -> None:
    """Inline header text, supplier code in brackets, currency only on the total."""
    title = TITLE_BY_DOCUMENT_TYPE.get(payload["document_type"], "DOCUMENT")
    currency = (payload["currency"] or "").strip()

    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(
        18 * mm,
        PAGE_HEIGHT - 26 * mm,
        f"{title} N° {payload['invoice_number'] or ''}",
    )

    canvas.setFont("Helvetica", 9)
    y = PAGE_HEIGHT - 36 * mm
    canvas.drawString(18 * mm, y, f"Emise le {fr_date(payload['invoice_date'])}")
    y -= 5.5 * mm
    if payload["due_date"]:
        canvas.drawString(18 * mm, y, f"Règlement avant le {fr_date(payload['due_date'])}")
        y -= 5.5 * mm
    canvas.drawString(
        18 * mm,
        y,
        f"Fournisseur : {payload['supplier_name'] or ''} "
        f"[{payload['supplier_reference'] or ''}]",
    )
    y -= 5.5 * mm
    canvas.drawString(18 * mm, y, f"Commande : {payload['purchase_order_number'] or ''}")
    y -= 10 * mm

    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(18 * mm, y, "Article")
    canvas.drawRightString(120 * mm, y, "Nombre")
    canvas.drawRightString(152 * mm, y, "Tarif")
    canvas.drawRightString(PAGE_WIDTH - 18 * mm, y, "Somme")
    y -= 6.5 * mm

    canvas.setFont("Helvetica", 9)
    for line in payload["line_items"]:
        canvas.drawString(18 * mm, y, line["description"] or "")
        canvas.drawRightString(120 * mm, y, fr_quantity(line["quantity"]))
        canvas.drawRightString(152 * mm, y, fr_amount(line["unit_price"]))
        canvas.drawRightString(PAGE_WIDTH - 18 * mm, y, fr_amount(line["line_amount"]))
        y -= 6 * mm

    y -= 8 * mm
    canvas.setFont("Helvetica", 9)
    canvas.drawString(18 * mm, y, "Montant hors taxe")
    canvas.drawRightString(PAGE_WIDTH - 18 * mm, y, fr_amount(payload["subtotal"]))
    y -= 6 * mm
    canvas.drawString(18 * mm, y, "Dont taxe")
    canvas.drawRightString(PAGE_WIDTH - 18 * mm, y, fr_amount(payload["tax_total"]))
    y -= 6 * mm
    canvas.setFont("Helvetica-Bold", 10)
    total = fr_amount(payload["total"])
    canvas.drawString(18 * mm, y, "Total TTC")
    canvas.drawRightString(
        PAGE_WIDTH - 18 * mm, y, f"{total} {currency}".strip()
    )

    _notice(canvas)


def draw_sousse(canvas: Canvas, payload: dict) -> None:
    """A fourth layout the extractor is deliberately NOT taught.

    Every label is worded differently from the three supported families and
    the totals block is a single run of text. Nothing in
    ``src/invoice_automation/extraction/layouts.py`` mentions these labels, and
    nothing should: documents drawn here exist to measure what the extractor
    does with a layout it has never seen, which is the only number on this
    dataset that is not measuring the extractor against its own assumptions.

    The wanted behaviour is a clean failure — no layout recognised, no values
    invented, the document routed to review — not a lucky guess.
    """
    title = TITLE_BY_DOCUMENT_TYPE.get(payload["document_type"], "DOCUMENT")
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(16 * mm, PAGE_HEIGHT - 24 * mm, f"PIECE COMPTABLE - {title}")

    canvas.setFont("Helvetica", 9)
    y = PAGE_HEIGHT - 36 * mm
    rows = [
        ("Numéro de pièce", payload["invoice_number"]),
        ("Daté du", fr_date(payload["invoice_date"])),
        ("Exigible le", fr_date(payload["due_date"])),
        ("Unité monétaire", payload["currency"]),
        ("Rattaché à la commande", payload["purchase_order_number"]),
        ("Emetteur", payload["supplier_name"]),
        ("Emetteur réf.", payload["supplier_reference"]),
    ]
    for label_text, value in rows:
        canvas.drawString(16 * mm, y, f"{label_text} > {value if value else ''}")
        y -= 5.5 * mm

    y -= 6 * mm
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(16 * mm, y, "Libellé")
    canvas.drawRightString(118 * mm, y, "Nb")
    canvas.drawRightString(150 * mm, y, "Prix")
    canvas.drawRightString(PAGE_WIDTH - 16 * mm, y, "Valeur")
    y -= 6.5 * mm

    canvas.setFont("Helvetica", 9)
    for line in payload["line_items"]:
        canvas.drawString(16 * mm, y, line["description"] or "")
        canvas.drawRightString(118 * mm, y, fr_quantity(line["quantity"]))
        canvas.drawRightString(150 * mm, y, fr_amount(line["unit_price"]))
        canvas.drawRightString(PAGE_WIDTH - 16 * mm, y, fr_amount(line["line_amount"]))
        y -= 6 * mm

    y -= 8 * mm
    canvas.setFont("Helvetica", 9)
    canvas.drawString(
        16 * mm,
        y,
        f"Base HT {fr_amount(payload['subtotal'])} / "
        f"Taxes {fr_amount(payload['tax_total'])} / "
        f"Net à régler {fr_amount(payload['total'])}",
    )

    _notice(canvas)


LAYOUTS = {
    "atlas": draw_atlas,
    "medina": draw_medina,
    "carthage": draw_carthage,
    "sousse": draw_sousse,
}

#: Cases also rendered in the unseen layout, for the held-out probe. Chosen to
#: cover a clean invoice, a second supplier and one with a seeded arithmetic
#: fault, so the probe is not measured on one document alone.
UNSEEN_CASES = ("valid_001", "valid_002", "line_total_mismatch_006")


def render(payload: dict, layout: str, destination: Path) -> None:
    canvas = Canvas(
        str(destination),
        pagesize=A4,
        # Fixed dates and document ids: the output must not change between runs.
        invariant=1,
    )
    canvas.setTitle(f"{payload['document_type']} {payload['invoice_number']}")
    canvas.setAuthor("Synthetic fixture generator")
    canvas.setSubject("Synthetic portfolio data")
    LAYOUTS[layout](canvas, payload)
    canvas.save()


def main() -> None:
    PDFS.mkdir(parents=True, exist_ok=True)
    structured = json.loads(MANIFEST_IN.read_text(encoding="utf-8"))

    cases = []
    for case in structured["cases"]:
        case_id = case["case_id"]
        layout = LAYOUT_BY_CASE[case_id]
        payload = json.loads((STRUCTURED / case["file"]).read_text(encoding="utf-8"))
        pdf_name = f"{case_id}.pdf"
        render(payload, layout, PDFS / pdf_name)
        cases.append(
            {
                "case_id": case_id,
                "pdf": pdf_name,
                "layout": layout,
                "ground_truth": case["file"],
                "expected_state": case["expected_state"],
                "expected_failed_codes": case["expected_failed_codes"],
                "label": case["label"],
            }
        )

    # The held-out probe: the same payloads drawn in a layout the extractor
    # knows nothing about.
    UNSEEN_PDFS.mkdir(parents=True, exist_ok=True)
    unseen = []
    by_case = {case["case_id"]: case for case in structured["cases"]}
    for case_id in UNSEEN_CASES:
        case = by_case[case_id]
        payload = json.loads((STRUCTURED / case["file"]).read_text(encoding="utf-8"))
        pdf_name = f"unseen_{case_id}.pdf"
        render(payload, "sousse", UNSEEN_PDFS / pdf_name)
        unseen.append(
            {
                "case_id": f"unseen_{case_id}",
                "pdf": pdf_name,
                "layout": "sousse",
                "ground_truth": case["file"],
                "label": (
                    "Same payload drawn in a layout the extractor was not "
                    "taught; measures behaviour on an unseen layout."
                ),
            }
        )

    UNSEEN_MANIFEST_OUT.write_text(
        json.dumps(
            {
                "dataset": "pdf_unseen_layout_probe_v1",
                "synthetic": True,
                "note": (
                    "Held-out probe. The 'sousse' layout is drawn by this "
                    "generator and is deliberately absent from "
                    "src/invoice_automation/extraction/layouts.py. Do not add its "
                    "labels there: that would destroy the only measurement here "
                    "that is not the extractor marking its own homework. The "
                    "wanted behaviour is a clean failure, not a lucky guess."
                ),
                "cases": unseen,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    MANIFEST_OUT.write_text(
        json.dumps(
            {
                "dataset": "pdf_slice2_v1",
                "synthetic": True,
                "note": (
                    "Synthetic invoice PDFs rendered from the structured payloads, "
                    "which are therefore the ground truth for extraction. Order "
                    "matters: business_duplicate_015 and exact_file_duplicate_016 "
                    "depend on valid_001 having been processed first. The layouts "
                    "and the extractor were written together, so accuracy on this "
                    "set reflects the generator's assumptions and says nothing "
                    "about real supplier documents."
                ),
                "layouts": sorted(set(LAYOUT_BY_CASE.values())),
                "cases": cases,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    by_layout: dict[str, int] = {}
    for case in cases:
        by_layout[case["layout"]] = by_layout.get(case["layout"], 0) + 1
    print(f"wrote {len(cases)} PDFs across {len(by_layout)} layouts: {by_layout}")
    print(f"wrote {len(unseen)} unseen-layout PDFs for the held-out probe")


if __name__ == "__main__":
    main()
