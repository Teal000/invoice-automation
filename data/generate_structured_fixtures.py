"""Generate the structured fixture set and its ground truth.

Run it from the project root:

    .venv/Scripts/python.exe data/generate_structured_fixtures.py

It writes, deterministically and with no randomness:

    data/fixtures/reference/suppliers.json
    data/fixtures/reference/purchase_orders.json
    data/fixtures/structured/<case>.json        one payload per case
    data/ground_truth/structured_cases.json     expected outcome per case

Every supplier, invoice and purchase order here is invented. The French
labels and TND amounts reflect a Tunisian business setting; the 19% tax rate
is a fictional test configuration and is not tax guidance.

These payloads are *structured values*, not documents. They exist so the
validation rules can be developed and tested without an extractor in the way.
The PDF generator in the next increment produces documents whose extracted
values have this same shape.

The cases are ordered. Two of them depend on an earlier case having been
processed first (the exact-file duplicate and the business duplicate), so the
manifest preserves the order and the ingest command follows it.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "data" / "fixtures" / "structured"
REFERENCE = PROJECT_ROOT / "data" / "fixtures" / "reference"
GROUND_TRUTH = PROJECT_ROOT / "data" / "ground_truth"

TAX_RATE = Decimal("0.19")
PLACES = Decimal("0.001")

SUPPLIERS = [
    {
        "name": "Fournitures Atlas SARL (synthetic)",
        "reference": "SUP-001",
        "reference_normalized": "SUP-001",
        "permitted_currency": "TND",
        "is_active": True,
    },
    {
        "name": "Papeterie Medina SA (synthetic)",
        "reference": "SUP-002",
        "reference_normalized": "SUP-002",
        "permitted_currency": "TND",
        "is_active": True,
    },
    {
        "name": "Logistique Carthage SARL (synthetic)",
        "reference": "SUP-003",
        "reference_normalized": "SUP-003",
        "permitted_currency": "TND",
        "is_active": True,
    },
]


def money(value: Decimal) -> str:
    return format(value.quantize(PLACES, rounding="ROUND_HALF_UP"), "f")


def build(
    *,
    invoice_number: str,
    supplier_reference: str = "SUP-001",
    supplier_name: str | None = None,
    po_number: str,
    lines: list[tuple[str, str, str]],
    invoice_date: str = "2026-03-12",
    due_date: str | None = "2026-04-11",
    currency: str = "TND",
    document_type: str = "invoice",
    subtotal: str | None = None,
    tax_total: str | None = None,
    total: str | None = None,
) -> dict:
    """Build one payload, computing consistent amounts unless overridden.

    ``lines`` are (description, quantity, unit_price) triples; the line amount
    is computed, so a line is internally consistent unless a case overrides it.
    """
    names = {s["reference"]: s["name"] for s in SUPPLIERS}
    line_items = []
    computed_subtotal = Decimal("0")
    for index, (description, quantity, unit_price) in enumerate(lines, start=1):
        amount = Decimal(quantity) * Decimal(unit_price)
        computed_subtotal += amount.quantize(PLACES, rounding="ROUND_HALF_UP")
        line_items.append(
            {
                "line_number": index,
                "description": description,
                "quantity": quantity,
                "unit_price": money(Decimal(unit_price)),
                "line_amount": money(amount),
            }
        )

    computed_tax = computed_subtotal * TAX_RATE
    payload = {
        "document_type": document_type,
        "invoice_number": invoice_number,
        "supplier_reference": supplier_reference,
        "supplier_name": supplier_name or names.get(supplier_reference, "Unknown (synthetic)"),
        "invoice_date": invoice_date,
        "due_date": due_date,
        "currency": currency,
        "purchase_order_number": po_number,
        "subtotal": subtotal if subtotal is not None else money(computed_subtotal),
        "tax_total": tax_total if tax_total is not None else money(computed_tax),
        "total": total,
        "line_items": line_items,
        "extraction_method": "structured_fixture:v1",
    }
    if total is None:
        # Derive the total from the stated subtotal and tax so that an
        # overridden subtotal stays internally consistent with the total. When
        # a case deliberately makes one of them unreadable, fall back to the
        # computed amounts so the total itself remains correct.
        try:
            derived = Decimal(payload["subtotal"]) + Decimal(payload["tax_total"])
        except InvalidOperation:
            derived = computed_subtotal + computed_tax
        payload["total"] = money(derived)
    return payload


BASE_LINES = [
    ("Ramettes papier A4 80g", "10", "120.050"),
    ("Cartouches encre noire", "3", "45.750"),
]
ALT_LINES = [
    ("Chaises de bureau", "4", "289.900"),
    ("Lampes de bureau LED", "6", "62.500"),
]
SMALL_LINES = [("Stylos bille bleus (boite de 50)", "2", "18.400")]


def main() -> None:
    for directory in (FIXTURES, REFERENCE, GROUND_TRUTH):
        directory.mkdir(parents=True, exist_ok=True)

    cases: list[dict] = []
    payloads: dict[str, dict] = {}
    purchase_orders: list[dict] = []

    def add_case(
        case_id: str,
        payload: dict,
        *,
        expected_state: str,
        expected_failed_codes: list[str],
        label: str,
        po_expected_amount: str | None = None,
        po_supplier_reference: str | None = None,
        create_po: bool = True,
        file_name: str | None = None,
    ) -> None:
        file_name = file_name or f"{case_id}.json"
        payloads[file_name] = payload
        cases.append(
            {
                "case_id": case_id,
                "file": file_name,
                "expected_state": expected_state,
                "expected_failed_codes": expected_failed_codes,
                "label": label,
            }
        )
        if create_po and payload["purchase_order_number"]:
            amount = po_expected_amount or payload["total"]
            purchase_orders.append(
                {
                    "po_number": payload["purchase_order_number"],
                    "po_number_normalized": payload["purchase_order_number"].upper(),
                    "supplier_reference": po_supplier_reference
                    or payload["supplier_reference"],
                    "currency": "TND",
                    "expected_amount": amount,
                    "state": "open",
                }
            )

    # 01 - a clean invoice that passes every check.
    valid = build(invoice_number="FA-2026-0147", po_number="PO-2026-0031", lines=BASE_LINES)
    add_case(
        "valid_001",
        valid,
        expected_state="validated",
        expected_failed_codes=[],
        label="Clean invoice; all checks pass.",
    )

    # 02 - a second clean invoice, from another supplier.
    add_case(
        "valid_002",
        build(
            invoice_number="PM-2026-0552",
            supplier_reference="SUP-002",
            po_number="PO-2026-0032",
            lines=ALT_LINES,
        ),
        expected_state="validated",
        expected_failed_codes=[],
        label="Clean invoice from a second supplier.",
    )

    # 03 - required field absent.
    add_case(
        "missing_total_003",
        build(
            invoice_number="FA-2026-0148",
            po_number="PO-2026-0033",
            lines=BASE_LINES,
            total="",
        ),
        expected_state="needs_review",
        expected_failed_codes=["REQUIRED_FIELD_MISSING"],
        label="Document total absent; dependent checks are skipped, not passed.",
    )

    # 04 - required field present but unreadable.
    add_case(
        "unparseable_subtotal_004",
        build(
            invoice_number="FA-2026-0149",
            po_number="PO-2026-0034",
            lines=BASE_LINES,
            subtotal="mille deux cents",
        ),
        expected_state="needs_review",
        expected_failed_codes=["REQUIRED_FIELD_MISSING"],
        label="Subtotal cannot be parsed; recorded as unparseable rather than guessed.",
    )

    # 05 - supplier not in the registry.
    add_case(
        "unknown_supplier_005",
        build(
            invoice_number="XX-2026-0001",
            supplier_reference="SUP-999",
            supplier_name="Societe Inconnue (synthetic)",
            po_number="PO-2026-0035",
            lines=BASE_LINES,
        ),
        expected_state="needs_review",
        expected_failed_codes=["UNKNOWN_SUPPLIER"],
        label="Supplier reference absent from the registry.",
        po_supplier_reference="SUP-001",
    )

    # 06 - quantity x unit price does not match the stated line amount, while
    #      the line amounts still sum to the stated subtotal.
    line_mismatch = build(
        invoice_number="FA-2026-0150", po_number="PO-2026-0036", lines=BASE_LINES
    )
    line_mismatch["line_items"][0]["unit_price"] = "130.050"
    add_case(
        "line_total_mismatch_006",
        line_mismatch,
        expected_state="needs_review",
        expected_failed_codes=["LINE_TOTAL_MISMATCH"],
        label="Unit price inconsistent with the line amount on line 1.",
    )

    # 07 - lines do not sum to the subtotal, while subtotal + tax still equals
    #      the stated total.
    add_case(
        "subtotal_mismatch_007",
        build(
            invoice_number="FA-2026-0151",
            po_number="PO-2026-0037",
            lines=BASE_LINES,
            subtotal="1400.000",
            tax_total="191.923",
            total="1591.923",
        ),
        expected_state="needs_review",
        expected_failed_codes=["SUBTOTAL_MISMATCH"],
        label="Stated subtotal does not match the sum of the lines.",
    )

    # 08 - subtotal + tax does not match the stated total; the difference is
    #      inside the purchase-order tolerance, so only this check fails.
    add_case(
        "total_mismatch_008",
        build(
            invoice_number="FA-2026-0152",
            po_number="PO-2026-0038",
            lines=BASE_LINES,
            total="1600.000",
        ),
        expected_state="needs_review",
        expected_failed_codes=["TOTAL_MISMATCH"],
        label="Subtotal plus tax does not reconcile with the total.",
        po_expected_amount="1600.000",
    )

    # 09 - a currency this version does not handle.
    add_case(
        "currency_eur_009",
        build(
            invoice_number="FA-2026-0153",
            po_number="PO-2026-0039",
            lines=BASE_LINES,
            currency="EUR",
        ),
        expected_state="unsupported",
        expected_failed_codes=["CURRENCY_UNSUPPORTED"],
        label="Currency outside the configured set; no conversion is attempted.",
    )

    # 10 - an excluded document kind.
    add_case(
        "credit_note_010",
        build(
            invoice_number="AV-2026-0007",
            po_number="PO-2026-0040",
            lines=SMALL_LINES,
            document_type="credit_note",
        ),
        expected_state="unsupported",
        expected_failed_codes=["UNSUPPORTED_DOCUMENT"],
        label="Credit note; excluded from this version with an explicit code.",
    )

    # 11 - referenced purchase order does not exist.
    add_case(
        "po_not_found_011",
        build(
            invoice_number="FA-2026-0154",
            po_number="PO-9999999",
            lines=BASE_LINES,
        ),
        expected_state="needs_review",
        expected_failed_codes=["PO_NOT_FOUND"],
        label="Purchase-order reference not in the register.",
        create_po=False,
    )

    # 12 - the order belongs to a different supplier.
    add_case(
        "po_supplier_mismatch_012",
        build(
            invoice_number="PM-2026-0553",
            supplier_reference="SUP-002",
            po_number="PO-2026-0041",
            lines=BASE_LINES,
        ),
        expected_state="needs_review",
        expected_failed_codes=["PO_SUPPLIER_MISMATCH"],
        label="Invoice supplier is not the supplier on the purchase order.",
        po_supplier_reference="SUP-003",
    )

    # 13 - invoice total beyond the configured order tolerance.
    add_case(
        "po_amount_mismatch_013",
        build(
            invoice_number="FA-2026-0155",
            po_number="PO-2026-0042",
            lines=BASE_LINES,
        ),
        expected_state="needs_review",
        expected_failed_codes=["PO_AMOUNT_MISMATCH"],
        label="Invoice total exceeds the order amount by more than the tolerance.",
        po_expected_amount="1200.000",
    )

    # 14 - due date before the invoice date.
    add_case(
        "date_inconsistent_014",
        build(
            invoice_number="FA-2026-0156",
            po_number="PO-2026-0043",
            lines=BASE_LINES,
            invoice_date="2026-03-12",
            due_date="2026-03-01",
        ),
        expected_state="needs_review",
        expected_failed_codes=["DATE_INCONSISTENT"],
        label="Due date falls before the invoice date.",
    )

    # 15 - a different document claiming an invoice number already recorded.
    #      Arithmetic is consistent and the amounts match the original order,
    #      so the duplicate is the only failure. Depends on case 01.
    business_duplicate = build(
        invoice_number="FA-2026-0147", po_number="PO-2026-0031", lines=BASE_LINES
    )
    business_duplicate["line_items"][0]["description"] = "Ramettes papier A4 80g (relance)"
    add_case(
        "business_duplicate_015",
        business_duplicate,
        expected_state="needs_review",
        expected_failed_codes=["BUSINESS_DUPLICATE"],
        label="Second document for an invoice number already recorded; both files kept.",
        create_po=False,
    )

    # 16 - the identical file sent again. Byte-identical to case 01, so its
    #      SHA-256 matches and the remaining checks are not re-run.
    add_case(
        "exact_file_duplicate_016",
        valid,
        expected_state="duplicate",
        expected_failed_codes=["EXACT_FILE_DUPLICATE"],
        label="Byte-identical resend of valid_001; linked, not reprocessed.",
        create_po=False,
        file_name="exact_file_duplicate_016.json",
    )

    # Write payloads. valid_001 and its resend must be byte identical.
    for file_name, payload in payloads.items():
        (FIXTURES / file_name).write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8",
            newline="\n",
        )

    # De-duplicate purchase orders by number, keeping the first definition.
    seen: set[str] = set()
    unique_pos = []
    for po in purchase_orders:
        if po["po_number_normalized"] in seen:
            continue
        seen.add(po["po_number_normalized"])
        unique_pos.append(po)

    (REFERENCE / "suppliers.json").write_text(
        json.dumps({"synthetic": True, "suppliers": SUPPLIERS}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (REFERENCE / "purchase_orders.json").write_text(
        json.dumps({"synthetic": True, "purchase_orders": unique_pos}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (GROUND_TRUTH / "structured_cases.json").write_text(
        json.dumps(
            {
                "dataset": "structured_slice1_v1",
                "synthetic": True,
                "note": (
                    "Expected outcomes for the structured fixture set. The order "
                    "matters: business_duplicate_015 and exact_file_duplicate_016 "
                    "depend on valid_001 having been processed first."
                ),
                "configured_tax_rate": str(TAX_RATE),
                "currency": "TND",
                "cases": cases,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"wrote {len(payloads)} payloads, {len(unique_pos)} purchase orders, "
          f"{len(cases)} labelled cases")


if __name__ == "__main__":
    main()
