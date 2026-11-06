"""Generate the labelled evaluation corpus: ~200 documents, five layouts, frozen splits.

    .venv/Scripts/python.exe data/generate_corpus.py

Writes:

    data/corpus/pdf/<case>.pdf          the document
    data/corpus/payloads/<case>.json    the values it was drawn from (ground truth)
    data/corpus/reference/*.json        suppliers and purchase orders
    data/manifests/corpus_splits.json   split, layout, seeded faults, labels

Deterministic: one seed, no clock, no randomness that survives a rerun. The
committed corpus and a regenerated one are byte-identical, which is what lets
"the frozen test set" mean anything.

Three design decisions carry the whole evaluation.

**Five layout families, two of them unfamiliar to the extractor.** Development
and validation use the families the extractor knows. The frozen test set adds
`kairouan`, which it has never been taught. A benchmark where every layout is
familiar measures the extractor against its own assumptions, and would report
a number that cannot fall.

**Some faults are undetectable on purpose.** If every seeded fault maps to a
rule, then "exception recall" restates the rule table and is worth nothing. So
the corpus also seeds faults no rule can catch: a supplier name that disagrees
with a correct supplier code, an invoice dated next year, a zero-quantity line,
a line entered twice. Each is labelled `detectable: false`. Recall measured
against *seeded faults* will therefore be below 100%, which is the honest
figure and the useful one.

**Duplicate families stay inside one split.** An exact resend and a
near-duplicate of the same invoice are generated as a family and assigned to a
split together, so no split can be scored on a document whose twin was in
another.
"""

from __future__ import annotations

import json
import random
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_pdf_fixtures import render  # noqa: E402
from generate_structured_fixtures import TAX_RATE, money  # noqa: E402

PROJECT_ROOT = HERE.parent
CORPUS = PROJECT_ROOT / "data" / "corpus"
PDFS = CORPUS / "pdf"
PAYLOADS = CORPUS / "payloads"
REFERENCE = CORPUS / "reference"
MANIFEST = PROJECT_ROOT / "data" / "manifests" / "corpus_splits.json"

#: One seed, fixed for ever. Changing it regenerates a different corpus and
#: invalidates every result recorded against the old one.
SEED = 20261106

#: Families the extractor is taught. `sousse` is taught as part of this work;
#: `kairouan` is not, and appears only in the frozen test split.
FAMILIAR_LAYOUTS = ("atlas", "medina", "carthage")
VALIDATION_EXTRA_LAYOUT = "sousse"
UNSEEN_LAYOUT = "kairouan"

SPLIT_SIZES = {"development": 120, "validation": 40, "test": 40}

SUPPLIER_NAMES = [
    "Fournitures Atlas SARL",
    "Papeterie Medina SA",
    "Logistique Carthage SARL",
    "Bureautique Sahel SA",
    "Comptoir Kairouan SARL",
    "Distribution Bizerte SA",
    "Materiel Sfax SARL",
    "Equipements Gabes SA",
    "Imprimerie Nabeul SARL",
    "Mobilier Sousse SA",
    "Technologies Ariana SARL",
    "Services Monastir SA",
]

PRODUCTS = [
    ("Ramettes papier A4 80g", "118.500", "125.000"),
    ("Cartouches encre noire", "44.000", "49.500"),
    ("Chaises de bureau", "275.000", "299.000"),
    ("Lampes de bureau LED", "58.000", "67.000"),
    ("Stylos bille bleus (boite de 50)", "16.500", "19.900"),
    ("Classeurs a levier", "11.200", "14.800"),
    ("Toner laser monochrome", "189.000", "215.000"),
    ("Armoire metallique 2 portes", "612.000", "689.000"),
    ("Agrafeuses metal", "23.400", "28.900"),
    ("Blocs-notes quadrilles", "7.800", "9.600"),
    ("Cables reseau RJ45 3m", "12.300", "15.700"),
    ("Ecrans 24 pouces", "445.000", "520.000"),
]

#: Faults a rule is designed to catch, with the code each should produce.
DETECTABLE_FAULTS = {
    "total_mismatch": "TOTAL_MISMATCH",
    "subtotal_mismatch": "SUBTOTAL_MISMATCH",
    "line_total_mismatch": "LINE_TOTAL_MISMATCH",
    "missing_total": "REQUIRED_FIELD_MISSING",
    "unparseable_subtotal": "REQUIRED_FIELD_MISSING",
    "unknown_supplier": "UNKNOWN_SUPPLIER",
    "po_not_found": "PO_NOT_FOUND",
    "po_supplier_mismatch": "PO_SUPPLIER_MISMATCH",
    "po_amount_mismatch": "PO_AMOUNT_MISMATCH",
    "date_inconsistent": "DATE_INCONSISTENT",
    "foreign_currency": "CURRENCY_UNSUPPORTED",
    "credit_note": "UNSUPPORTED_DOCUMENT",
}

#: Faults no rule in this version can catch. Seeded deliberately so that
#: recall is measured against reality rather than against the rule table.
UNDETECTABLE_FAULTS = {
    # The code resolves and the name is simply somebody else's. No rule
    # compares the printed name with the registered one.
    "supplier_name_mismatch": "Supplier name does not match the registered name for that code",
    # Nothing checks that an invoice date is plausible.
    "future_invoice_date": "Invoice dated next year",
    # Arithmetic reconciles perfectly with a quantity of zero.
    "zero_quantity_line": "A line billed with quantity zero",
    # The same line twice; every total still agrees.
    "duplicated_line": "An identical line item entered twice",
}


class Corpus:
    def __init__(self, seed: int = SEED) -> None:
        self.rng = random.Random(seed)
        self.suppliers: list[dict] = []
        self.purchase_orders: list[dict] = []
        self.cases: list[dict] = []
        self.po_counter = 1000
        self.invoice_counter = 1000

    # -- reference data ----------------------------------------------------

    def build_suppliers(self) -> None:
        for index, name in enumerate(SUPPLIER_NAMES, start=1):
            reference = f"SUP-{index:03d}"
            self.suppliers.append(
                {
                    "name": f"{name} (synthetic)",
                    "reference": reference,
                    "reference_normalized": reference,
                    "permitted_currency": "TND",
                    "is_active": True,
                }
            )

    def next_po(self) -> str:
        self.po_counter += 1
        return f"PO-2026-{self.po_counter}"

    def next_invoice_number(self, supplier_reference: str) -> str:
        self.invoice_counter += 1
        prefix = supplier_reference.replace("SUP-", "FA")
        return f"{prefix}-2026-{self.invoice_counter}"

    # -- documents ---------------------------------------------------------

    def build_payload(self, supplier: dict, *, line_count: int) -> dict:
        lines = []
        subtotal = Decimal("0")
        for number, (description, low, high) in enumerate(
            self.rng.sample(PRODUCTS, line_count), start=1
        ):
            quantity = self.rng.choice(["1", "2", "3", "4", "5", "6", "8", "10", "12", "20"])
            unit_price = Decimal(low) + (Decimal(high) - Decimal(low)) * Decimal(
                self.rng.randrange(0, 11)
            ) / Decimal(10)
            amount = (Decimal(quantity) * unit_price).quantize(Decimal("0.001"))
            subtotal += amount
            lines.append(
                {
                    "line_number": number,
                    "description": description,
                    "quantity": quantity,
                    "unit_price": money(unit_price),
                    "line_amount": money(amount),
                }
            )

        tax = subtotal * TAX_RATE
        day = self.rng.randrange(1, 28)
        month = self.rng.randrange(1, 10)
        invoice_date = f"2026-{month:02d}-{day:02d}"
        due_month = month + 1
        due_date = f"2026-{due_month:02d}-{day:02d}"

        return {
            "document_type": "invoice",
            "invoice_number": self.next_invoice_number(supplier["reference"]),
            "supplier_reference": supplier["reference"],
            "supplier_name": supplier["name"],
            "invoice_date": invoice_date,
            "due_date": due_date,
            "currency": "TND",
            "purchase_order_number": self.next_po(),
            "subtotal": money(subtotal),
            "tax_total": money(tax),
            "total": money(subtotal + tax),
            "line_items": lines,
            "extraction_method": "structured_fixture:v1",
        }

    def apply_fault(self, payload: dict, fault: str) -> tuple[list[str], bool]:
        """Mutate a payload, returning (expected failed codes, detectable)."""
        if fault == "total_mismatch":
            payload["total"] = money(Decimal(payload["total"]) + Decimal("37.500"))
            return ["TOTAL_MISMATCH"], True
        if fault == "subtotal_mismatch":
            payload["subtotal"] = money(Decimal(payload["subtotal"]) + Decimal("55.000"))
            payload["tax_total"] = money(
                Decimal(payload["total"]) - Decimal(payload["subtotal"])
            )
            return ["SUBTOTAL_MISMATCH"], True
        if fault == "line_total_mismatch":
            line = payload["line_items"][0]
            line["unit_price"] = money(Decimal(line["unit_price"]) + Decimal("9.000"))
            return ["LINE_TOTAL_MISMATCH"], True
        if fault == "missing_total":
            payload["total"] = ""
            return ["REQUIRED_FIELD_MISSING"], True
        if fault == "unparseable_subtotal":
            payload["subtotal"] = "montant illisible"
            return ["REQUIRED_FIELD_MISSING"], True
        if fault == "unknown_supplier":
            payload["supplier_reference"] = "SUP-900"
            payload["supplier_name"] = "Fournisseur Inconnu (synthetic)"
            return ["UNKNOWN_SUPPLIER"], True
        if fault == "po_not_found":
            payload["purchase_order_number"] = "PO-2026-999999"
            return ["PO_NOT_FOUND"], True
        if fault == "po_supplier_mismatch":
            return ["PO_SUPPLIER_MISMATCH"], True  # wired when the PO is written
        if fault == "po_amount_mismatch":
            return ["PO_AMOUNT_MISMATCH"], True  # wired when the PO is written
        if fault == "date_inconsistent":
            payload["due_date"] = "2026-01-01"
            payload["invoice_date"] = "2026-06-15"
            return ["DATE_INCONSISTENT"], True
        if fault == "foreign_currency":
            payload["currency"] = "EUR"
            return ["CURRENCY_UNSUPPORTED"], True
        if fault == "credit_note":
            payload["document_type"] = "credit_note"
            return ["UNSUPPORTED_DOCUMENT"], True

        # --- faults no rule can catch -------------------------------------
        if fault == "supplier_name_mismatch":
            others = [s for s in self.suppliers if s["reference"] != payload["supplier_reference"]]
            payload["supplier_name"] = self.rng.choice(others)["name"]
            return [], False
        if fault == "future_invoice_date":
            payload["invoice_date"] = "2027-03-04"
            payload["due_date"] = "2027-04-03"
            return [], False
        if fault == "zero_quantity_line":
            line = payload["line_items"][0]
            line["quantity"] = "0"
            line["line_amount"] = money(Decimal("0"))
            subtotal = sum(Decimal(l["line_amount"]) for l in payload["line_items"])
            payload["subtotal"] = money(subtotal)
            payload["tax_total"] = money(subtotal * TAX_RATE)
            payload["total"] = money(subtotal + subtotal * TAX_RATE)
            return [], False
        if fault == "duplicated_line":
            first = dict(payload["line_items"][0])
            first["line_number"] = len(payload["line_items"]) + 1
            payload["line_items"].append(first)
            subtotal = sum(Decimal(l["line_amount"]) for l in payload["line_items"])
            payload["subtotal"] = money(subtotal)
            payload["tax_total"] = money(subtotal * TAX_RATE)
            payload["total"] = money(subtotal + subtotal * TAX_RATE)
            return [], False

        raise ValueError(f"unknown fault {fault}")

    def write_purchase_order(
        self, payload: dict, *, fault: str | None, supplier: dict
    ) -> None:
        if fault == "po_not_found":
            return
        amount = payload["total"]
        try:
            Decimal(amount)
        except Exception:  # noqa: BLE001 - a deliberately broken total
            amount = money(
                sum(Decimal(l["line_amount"]) for l in payload["line_items"])
                * (Decimal(1) + TAX_RATE)
            )
        owner = supplier["reference"]
        if fault == "po_supplier_mismatch":
            others = [s for s in self.suppliers if s["reference"] != owner]
            owner = self.rng.choice(others)["reference"]
        if fault == "po_amount_mismatch":
            amount = money(Decimal(amount) * Decimal("0.70"))
        self.purchase_orders.append(
            {
                "po_number": payload["purchase_order_number"],
                "po_number_normalized": payload["purchase_order_number"].upper(),
                "supplier_reference": owner,
                "currency": "TND",
                "expected_amount": amount,
                "state": "open",
            }
        )

    # -- assembly ----------------------------------------------------------

    def add_case(
        self,
        case_id: str,
        payload: dict,
        *,
        split: str,
        layout: str,
        faults: list[str],
        expected_codes: list[str],
        detectable: bool,
        family: str,
        note: str = "",
    ) -> None:
        state = "validated"
        if "UNSUPPORTED_DOCUMENT" in expected_codes or "CURRENCY_UNSUPPORTED" in expected_codes:
            state = "unsupported"
        elif "EXACT_FILE_DUPLICATE" in expected_codes:
            state = "duplicate"
        elif expected_codes:
            state = "needs_review"

        (PAYLOADS / f"{case_id}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        render(payload, layout, PDFS / f"{case_id}.pdf")
        self.cases.append(
            {
                "case_id": case_id,
                "pdf": f"{case_id}.pdf",
                "payload": f"{case_id}.json",
                "split": split,
                "layout": layout,
                "family": family,
                "seeded_faults": faults,
                "detectable": detectable,
                "expected_state": state,
                "expected_failed_codes": expected_codes,
                "note": note,
            }
        )

    def layouts_for(self, split: str) -> list[str]:
        if split == "development":
            return list(FAMILIAR_LAYOUTS)
        if split == "validation":
            return [*FAMILIAR_LAYOUTS, VALIDATION_EXTRA_LAYOUT]
        return [*FAMILIAR_LAYOUTS, VALIDATION_EXTRA_LAYOUT, UNSEEN_LAYOUT]

    def build_split(self, split: str, size: int) -> None:
        layouts = self.layouts_for(split)
        detectable = list(DETECTABLE_FAULTS)
        undetectable = list(UNDETECTABLE_FAULTS)

        index = 0
        while index < size:
            layout = layouts[index % len(layouts)]
            supplier = self.rng.choice(self.suppliers)
            payload = self.build_payload(supplier, line_count=self.rng.randrange(1, 5))
            case_id = f"{split[:3]}_{index:04d}"

            roll = self.rng.random()
            # A duplicate family: the same invoice twice, kept in one split.
            if roll < 0.06 and index + 2 < size:
                self.write_purchase_order(payload, fault=None, supplier=supplier)
                family = f"{split[:3]}-family-{index:04d}"
                self.add_case(
                    case_id, payload, split=split, layout=layout, faults=[],
                    expected_codes=[], detectable=True, family=family,
                    note="First arrival of a duplicate family.",
                )
                # Byte-identical resend: the same payload and the same layout.
                self.add_case(
                    f"{case_id}_resend", payload, split=split, layout=layout,
                    faults=["exact_resend"], expected_codes=["EXACT_FILE_DUPLICATE"],
                    detectable=True, family=family,
                    note="Byte-identical resend of the previous document.",
                )
                # Same invoice number, different document.
                variant = json.loads(json.dumps(payload))
                variant["line_items"][0]["description"] += " (relance)"
                self.add_case(
                    f"{case_id}_variant", variant, split=split, layout=layout,
                    faults=["business_duplicate"],
                    expected_codes=["BUSINESS_DUPLICATE"], detectable=True,
                    family=family,
                    note="Different document claiming an invoice number already recorded.",
                )
                index += 3
                continue

            if roll < 0.46:
                fault = detectable[index % len(detectable)]
                codes, is_detectable = self.apply_fault(payload, fault)
                self.write_purchase_order(payload, fault=fault, supplier=supplier)
                self.add_case(
                    case_id, payload, split=split, layout=layout, faults=[fault],
                    expected_codes=codes, detectable=is_detectable,
                    family="unique",
                )
            elif roll < 0.58:
                fault = undetectable[index % len(undetectable)]
                codes, is_detectable = self.apply_fault(payload, fault)
                self.write_purchase_order(payload, fault=None, supplier=supplier)
                self.add_case(
                    case_id, payload, split=split, layout=layout, faults=[fault],
                    expected_codes=codes, detectable=is_detectable,
                    family="unique", note=UNDETECTABLE_FAULTS[fault],
                )
            else:
                self.write_purchase_order(payload, fault=None, supplier=supplier)
                self.add_case(
                    case_id, payload, split=split, layout=layout, faults=[],
                    expected_codes=[], detectable=True, family="unique",
                )
            index += 1


def main() -> None:
    for directory in (PDFS, PAYLOADS, REFERENCE, MANIFEST.parent):
        directory.mkdir(parents=True, exist_ok=True)
    for stale in list(PDFS.glob("*.pdf")) + list(PAYLOADS.glob("*.json")):
        stale.unlink()

    corpus = Corpus()
    corpus.build_suppliers()
    for split, size in SPLIT_SIZES.items():
        corpus.build_split(split, size)

    (REFERENCE / "suppliers.json").write_text(
        json.dumps({"synthetic": True, "suppliers": corpus.suppliers}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (REFERENCE / "purchase_orders.json").write_text(
        json.dumps(
            {"synthetic": True, "purchase_orders": corpus.purchase_orders}, indent=2
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    by_split: dict[str, int] = {}
    by_layout: dict[str, int] = {}
    detectable = undetectable = clean = 0
    for case in corpus.cases:
        by_split[case["split"]] = by_split.get(case["split"], 0) + 1
        by_layout[case["layout"]] = by_layout.get(case["layout"], 0) + 1
        if not case["seeded_faults"]:
            clean += 1
        elif case["detectable"]:
            detectable += 1
        else:
            undetectable += 1

    MANIFEST.write_text(
        json.dumps(
            {
                "dataset": "corpus_v1",
                "synthetic": True,
                "seed": SEED,
                "note": (
                    "Deterministic from the seed: regenerating produces byte-identical "
                    "documents. The test split is frozen - it contains the kairouan "
                    "layout, which the extractor is not taught, and must not be used "
                    "to tune anything. Duplicate families share a split so no split is "
                    "scored on a document whose twin is in another."
                ),
                "familiar_layouts": list(FAMILIAR_LAYOUTS),
                "validation_extra_layout": VALIDATION_EXTRA_LAYOUT,
                "unseen_layout": UNSEEN_LAYOUT,
                "counts": {
                    "documents": len(corpus.cases),
                    "by_split": by_split,
                    "by_layout": by_layout,
                    "clean": clean,
                    "with_detectable_fault": detectable,
                    "with_undetectable_fault": undetectable,
                },
                "undetectable_faults": UNDETECTABLE_FAULTS,
                "cases": corpus.cases,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"documents: {len(corpus.cases)}")
    print(f"  by split:  {by_split}")
    print(f"  by layout: {by_layout}")
    print(f"  clean {clean} | detectable fault {detectable} | undetectable fault {undetectable}")
    print(f"  suppliers {len(corpus.suppliers)} | purchase orders {len(corpus.purchase_orders)}")


if __name__ == "__main__":
    main()
