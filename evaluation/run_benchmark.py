"""Run the benchmark over a split of the labelled corpus.

    .venv/Scripts/python.exe evaluation/run_benchmark.py --split development
    .venv/Scripts/python.exe evaluation/run_benchmark.py --split validation
    .venv/Scripts/python.exe evaluation/run_benchmark.py --split test

Writes `evaluation/results/benchmark_<split>.json` and a Markdown summary.

No database: the rules are pure functions over a prepared context, so the
whole corpus is replayed in memory with the supplier and purchase-order
registries loaded from the corpus reference files and the duplicate lookups
served by a stand-in that records what has been seen, in manifest order. That
is the same path the database takes, minus the storage.

Three families of measurement, deliberately separate, because they answer
different questions and mixing them is how a benchmark flatters itself.

**Extraction.** Did the extractor recover the values the document was drawn
from? Compared after normalisation, so a date is compared as a date and an
amount as a decimal. Reported for familiar layouts and for the unseen layout
separately: a single average over both hides the only number that
generalises.

**Pipeline conformance.** Did the system reach the state and the exception
codes the case is labelled with? This is a specification test. A high score
means the implementation matches its own rules — it is not evidence that the
rules are the right ones.

**Fault detection.** Of the faults actually seeded into the documents, how
many did the rules flag? The corpus deliberately contains faults no rule can
catch (a supplier name that disagrees with a correct code, an invoice dated
next year, a zero-quantity line, a line entered twice). Recall is reported
over *all* seeded faults, so it is below 100% and says something true. The
detectable-only figure is reported beside it for comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from invoice_automation.config import RuleConfig  # noqa: E402
from invoice_automation.extraction import EXTRACTION_METHOD, extract_invoice  # noqa: E402
from invoice_automation.models import (  # noqa: E402
    NormalizedInvoice,
    RawInvoice,
    SubmissionInput,
    normalize_identifier,
    normalize_invoice,
)
from invoice_automation.rules.base import (  # noqa: E402
    DuplicateMatch,
    PurchaseOrderRecord,
    SupplierRecord,
    ValidationContext,
)
from invoice_automation.rules.engine import run_rules  # noqa: E402

CORPUS = PROJECT_ROOT / "data" / "corpus"
MANIFEST = PROJECT_ROOT / "data" / "manifests" / "corpus_splits.json"
RESULTS = PROJECT_ROOT / "evaluation" / "results"
NAMESPACE = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

HEADER_FIELDS = (
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
)

CRITICAL_FIELDS = tuple(f for f in HEADER_FIELDS if f != "due_date")


@dataclass
class Tally:
    correct: int = 0
    total: int = 0

    def add(self, ok: bool) -> None:
        self.total += 1
        self.correct += 1 if ok else 0

    @property
    def accuracy(self) -> float | None:
        return None if self.total == 0 else round(self.correct / self.total, 4)

    def as_dict(self) -> dict:
        return {"correct": self.correct, "total": self.total, "accuracy": self.accuracy}


@dataclass
class Registries:
    suppliers: dict[str, SupplierRecord] = field(default_factory=dict)
    orders: dict[str, PurchaseOrderRecord] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Registries":
        data = json.loads((CORPUS / "reference" / "suppliers.json").read_text("utf-8"))
        orders = json.loads(
            (CORPUS / "reference" / "purchase_orders.json").read_text("utf-8")
        )
        registries = cls()
        for entry in data["suppliers"]:
            reference = entry["reference_normalized"]
            registries.suppliers[reference] = SupplierRecord(
                supplier_id=uuid5(NAMESPACE, reference),
                name=entry["name"],
                reference_normalized=reference,
                permitted_currency=entry["permitted_currency"],
                is_active=entry.get("is_active", True),
            )
        for entry in orders["purchase_orders"]:
            number = entry["po_number_normalized"]
            # The order's supplier is attached here so that this in-memory
            # replay produces the same failed-check text as the database
            # path, which joins suppliers in the lookup.
            po_supplier = registries.suppliers[
                normalize_identifier(entry["supplier_reference"])
            ]
            registries.orders[number] = PurchaseOrderRecord(
                purchase_order_id=uuid5(NAMESPACE, number),
                po_number_normalized=number,
                supplier_id=po_supplier.supplier_id,
                currency=entry["currency"],
                expected_amount=Decimal(entry["expected_amount"]),
                state=entry.get("state", "open"),
                supplier_name=po_supplier.name,
                supplier_reference=po_supplier.reference_normalized,
            )
        return registries


def render(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def line_tuples(invoice: NormalizedInvoice) -> list[tuple]:
    return [
        (line.description, line.quantity, line.unit_price, line.line_amount)
        for line in invoice.lines
    ]


def run_split(split: str, manifest: dict, config: RuleConfig) -> dict:
    registries = Registries.load()
    cases = [case for case in manifest["cases"] if case["split"] == split]
    unseen_layout = manifest["unseen_layout"]

    per_field_all = {name: Tally() for name in (*HEADER_FIELDS, "line_items")}
    per_field_present = {name: Tally() for name in (*HEADER_FIELDS, "line_items")}
    documents_familiar = Tally()
    documents_unseen = Tally()
    layout_detection_familiar = Tally()
    layout_detection_unseen = Tally()
    conformance = Tally()
    per_layout: dict[str, Tally] = {}

    # Fault detection, measured over the faults actually seeded.
    faults_all = Tally()
    faults_detectable = Tally()
    faults_undetectable = Tally()
    flagged_total = 0
    flagged_with_fault = 0
    coincidental_flags = 0
    invented_on_unseen = 0

    seen_hashes: dict[str, str] = {}
    seen_invoices: dict[tuple[str, str], str] = {}
    case_rows: list[dict] = []

    for case in cases:
        pdf_path = CORPUS / "pdf" / case["pdf"]
        truth_payload = json.loads(
            (CORPUS / "payloads" / case["payload"]).read_text("utf-8")
        )
        truth = normalize_invoice(
            RawInvoice.model_validate(truth_payload), config
        ).normalized

        digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        extraction = extract_invoice(pdf_path)
        parsed = normalize_invoice(extraction.raw, config)
        predicted = parsed.normalized

        is_unseen = case["layout"] == unseen_layout
        (layout_detection_unseen if is_unseen else layout_detection_familiar).add(
            extraction.layout == case["layout"]
        )

        # --- extraction, field by field ---------------------------------
        field_results = {}
        for name in (*HEADER_FIELDS, "line_items"):
            if name == "line_items":
                expected, observed = line_tuples(truth), line_tuples(predicted)
            else:
                expected, observed = getattr(truth, name), getattr(predicted, name)
            ok = expected == observed
            present = bool(expected) if name == "line_items" else expected is not None
            per_field_all[name].add(ok)
            if present:
                per_field_present[name].add(ok)
            field_results[name] = ok
            if is_unseen and not ok and observed not in (None, [], "unknown"):
                invented_on_unseen += 1

        critical_ok = all(field_results[name] for name in CRITICAL_FIELDS)
        (documents_unseen if is_unseen else documents_familiar).add(critical_ok)
        per_layout.setdefault(case["layout"], Tally()).add(critical_ok)

        # --- the rules, with duplicate lookups as the database would ------
        supplier = registries.suppliers.get(predicted.supplier_reference_normalized or "")
        order = registries.orders.get(predicted.purchase_order_number_normalized or "")
        key = (
            str(supplier.supplier_id)
            if supplier
            else f"unresolved:{predicted.supplier_reference_normalized}"
        )
        identity = (
            key,
            predicted.invoice_number_normalized or f"unidentified:{case['case_id']}",
        )
        file_duplicate = (
            DuplicateMatch(kind="file_hash", matched_value=digest)
            if digest in seen_hashes
            else None
        )
        business_duplicate = (
            DuplicateMatch(kind="supplier_invoice_number", matched_value=identity[1])
            if identity in seen_invoices
            else None
        )

        outcome = run_rules(
            ValidationContext(
                invoice=predicted,
                submission=SubmissionInput(
                    source="cli", original_file_name=case["pdf"], file_sha256=digest
                ),
                config=config,
                problems=parsed.problems,
                supplier=supplier,
                purchase_order=order,
                file_duplicate=file_duplicate,
                business_duplicate=business_duplicate,
            )
        )
        seen_hashes.setdefault(digest, case["case_id"])
        if file_duplicate is None:
            seen_invoices.setdefault(identity, case["case_id"])

        expected_codes = list(case["expected_failed_codes"])
        actual_codes = list(outcome.failed_codes)
        matches = (
            str(outcome.state) == case["expected_state"]
            and actual_codes == expected_codes
        )
        conformance.add(matches)

        flagged = bool(actual_codes)
        has_fault = bool(case["seeded_faults"])
        if flagged:
            flagged_total += 1
            if has_fault:
                flagged_with_fault += 1
        if has_fault:
            faults_all.add(flagged)
            (faults_detectable if case["detectable"] else faults_undetectable).add(flagged)
            # A document whose layout was not recognised fails
            # REQUIRED_FIELD_MISSING whatever is wrong with it. Counting that
            # as having detected the seeded fault would be self-flattery, so
            # it is counted separately and subtracted in the write-up.
            if flagged and not case["detectable"] and extraction.layout is None:
                coincidental_flags += 1

        case_rows.append(
            {
                "case_id": case["case_id"],
                "layout": case["layout"],
                "layout_detected": extraction.layout,
                "unseen_layout": is_unseen,
                "seeded_faults": case["seeded_faults"],
                "detectable": case["detectable"],
                "expected_state": case["expected_state"],
                "actual_state": str(outcome.state),
                "expected_failed_codes": expected_codes,
                "actual_failed_codes": actual_codes,
                "conforms": matches,
                "critical_fields_correct": critical_ok,
            }
        )

    precision = round(flagged_with_fault / flagged_total, 4) if flagged_total else None

    return {
        "split": split,
        "documents": len(cases),
        "layout_detection": {
            "familiar": layout_detection_familiar.as_dict(),
            "unseen": layout_detection_unseen.as_dict(),
        },
        "extraction": {
            "critical_field_document_accuracy_familiar": documents_familiar.as_dict(),
            "critical_field_document_accuracy_unseen": documents_unseen.as_dict(),
            "per_field_all": {k: v.as_dict() for k, v in per_field_all.items()},
            "per_field_present_in_truth": {
                k: v.as_dict() for k, v in per_field_present.items()
            },
            "per_layout_critical_field_document_accuracy": {
                k: v.as_dict() for k, v in per_layout.items()
            },
            "values_invented_on_the_unseen_layout": invented_on_unseen,
        },
        "pipeline_conformance": conformance.as_dict(),
        "fault_detection": {
            "recall_over_all_seeded_faults": faults_all.as_dict(),
            "recall_over_detectable_faults": faults_detectable.as_dict(),
            "recall_over_undetectable_faults": faults_undetectable.as_dict(),
            "undetectable_flagged_only_because_unreadable": coincidental_flags,
            "precision": {
                "flagged": flagged_total,
                "flagged_with_a_seeded_fault": flagged_with_fault,
                "precision": precision,
            },
        },
        "cases": case_rows,
    }


def markdown(result: dict, manifest: dict) -> str:
    def rate(entry: dict) -> str:
        if entry.get("accuracy") is None:
            return "n/a"
        return f"{entry['correct']}/{entry['total']} ({entry['accuracy']:.0%})"

    extraction = result["extraction"]
    faults = result["fault_detection"]
    lines = [
        f"# Benchmark — {result['split']} split",
        "",
        f"Run {result['run_at']} against `{manifest['dataset']}`, extractor "
        f"`{result['extractor']}`. {result['documents']} documents.",
        "",
        "**Synthetic data.** Every document was generated by "
        "`data/generate_corpus.py` from a seeded payload, which is the ground "
        "truth. Figures describe this corpus and nothing else.",
        "",
        "## Extraction",
        "",
        "| Measure | Familiar layouts | Unseen layout |",
        "|---|---|---|",
        f"| Layout identified | {rate(result['layout_detection']['familiar'])} "
        f"| {rate(result['layout_detection']['unseen'])} |",
        f"| Critical-field document accuracy "
        f"| {rate(extraction['critical_field_document_accuracy_familiar'])} "
        f"| {rate(extraction['critical_field_document_accuracy_unseen'])} |",
        "",
        f"Values invented on the unseen layout: "
        f"**{extraction['values_invented_on_the_unseen_layout']}**. Reading "
        "nothing from an unfamiliar document is the designed behaviour; "
        "reading the wrong label into a total is not.",
        "",
        "### Per field (fields that carry a value in the ground truth)",
        "",
        "| Field | Accuracy |",
        "|---|---|",
    ]
    for name, entry in extraction["per_field_present_in_truth"].items():
        lines.append(f"| `{name}` | {rate(entry)} |")

    lines += [
        "",
        "### Per layout",
        "",
        "| Layout | Critical-field document accuracy |",
        "|---|---|",
    ]
    for name, entry in extraction["per_layout_critical_field_document_accuracy"].items():
        lines.append(f"| `{name}` | {rate(entry)} |")

    lines += [
        "",
        "## Pipeline conformance",
        "",
        f"{rate(result['pipeline_conformance'])} of documents reached the state "
        "and exception codes they are labelled with.",
        "",
        "This is a specification test: it says the implementation matches its "
        "own rules. It is not evidence that the rules are the right ones.",
        "",
        "## Fault detection",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Recall over **all** seeded faults | {rate(faults['recall_over_all_seeded_faults'])} |",
        f"| Recall over faults a rule targets | {rate(faults['recall_over_detectable_faults'])} |",
        f"| Recall over faults no rule targets | {rate(faults['recall_over_undetectable_faults'])} |",
        f"| Precision | {faults['precision']['flagged_with_a_seeded_fault']}"
        f"/{faults['precision']['flagged']}"
        + (
            f" ({faults['precision']['precision']:.0%})"
            if faults["precision"]["precision"] is not None
            else ""
        )
        + " |",
        "",
        (
            f"Of the undetectable faults that were flagged, "
            f"**{faults['undetectable_flagged_only_because_unreadable']}** were "
            "flagged only because the document's layout could not be read, so "
            "every field was reported missing. That is not detection of the "
            "seeded fault and should not be read as such."
        ),
        "",
        "The corpus seeds faults the rules cannot catch on purpose — a supplier "
        "name that disagrees with a correct code, an invoice dated next year, a "
        "zero-quantity line, a line entered twice. The first row is the honest "
        "recall; the second is what the rules were built for; the third is the "
        "blind spot, and it should read zero.",
        "",
        "## What this does not establish",
        "",
        "- Synthetic documents from one generator. Real supplier invoices vary "
        "in ways a generator does not anticipate.",
        "- Digitally generated PDFs with a text layer. No scans, no OCR.",
        "- No timing, no cost, no comparison with doing the work by hand.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split",
        choices=("development", "validation", "test"),
        required=True,
        help="Which split to score. 'test' is frozen: run it once, at the end.",
    )
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    config = RuleConfig()

    result = run_split(args.split, manifest, config)
    result["run_at"] = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    result["dataset"] = manifest["dataset"]
    result["seed"] = manifest["seed"]
    result["extractor"] = EXTRACTION_METHOD
    result["python"] = platform.python_version()
    if args.split == "test":
        result["note"] = (
            "Frozen test split. It contains the layout the extractor was never "
            "taught. Tuning anything against these figures invalidates them."
        )

    json_path = RESULTS / f"benchmark_{args.split}.json"
    json_path.write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    md_path = RESULTS / f"benchmark_{args.split}.md"
    md_path.write_text(markdown(result, manifest), encoding="utf-8", newline="\n")

    extraction = result["extraction"]
    faults = result["fault_detection"]
    print(f"split: {args.split}  documents: {result['documents']}")
    print(
        "  layout detected   familiar "
        f"{result['layout_detection']['familiar']['correct']}/{result['layout_detection']['familiar']['total']}"
        f" | unseen {result['layout_detection']['unseen']['correct']}/{result['layout_detection']['unseen']['total']}"
    )
    print(
        "  critical fields   familiar "
        f"{extraction['critical_field_document_accuracy_familiar']['correct']}/{extraction['critical_field_document_accuracy_familiar']['total']}"
        f" | unseen {extraction['critical_field_document_accuracy_unseen']['correct']}/{extraction['critical_field_document_accuracy_unseen']['total']}"
    )
    print(f"  values invented on unseen layout: {extraction['values_invented_on_the_unseen_layout']}")
    print(
        "  conformance       "
        f"{result['pipeline_conformance']['correct']}/{result['pipeline_conformance']['total']}"
    )
    print(
        "  fault recall      all "
        f"{faults['recall_over_all_seeded_faults']['correct']}/{faults['recall_over_all_seeded_faults']['total']}"
        f" | detectable {faults['recall_over_detectable_faults']['correct']}/{faults['recall_over_detectable_faults']['total']}"
        f" | undetectable {faults['recall_over_undetectable_faults']['correct']}/{faults['recall_over_undetectable_faults']['total']}"
    )
    print(f"\nwrote {json_path.relative_to(PROJECT_ROOT)} and {md_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
