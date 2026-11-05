"""Measure what the PDF extractor recovers, against the payloads it was drawn from.

    .venv/Scripts/python.exe evaluation/run_extraction_baseline.py

Writes a JSON result file and a Markdown summary to ``evaluation/results/``.

What is compared
----------------
Each PDF was rendered from a structured payload, so that payload is the ground
truth. Both sides are put through the same normalisation
(:func:`invoice_automation.models.normalize_invoice`) and the *normalised*
values are compared: a date is compared as a date, an amount as a decimal.
This measures whether extraction recovered the same meaning, not whether it
reproduced the same string.

Two denominators, both reported, because they answer different questions:

``all labelled fields``
    Every field in the contract, for every document. A field the document
    deliberately lacks counts as correct when the extractor also reports it
    absent. This is the figure that describes end-to-end behaviour.

``fields present in the ground truth``
    Only fields that actually carry a value. This is the harder figure and the
    one to quote when asking "does it read what is there".

Documents whose business outcome is excluded by the rules (a credit note, a
foreign currency) are reported separately as well, so they cannot quietly
inflate or deflate either figure.

What this does not establish
----------------------------
The documents are synthetic, and the three layouts were written alongside the
extractor's label sets. A high score here says the extractor reads the
documents this generator draws. It says nothing about real supplier invoices,
which word their labels differently, and nothing about scanned documents,
which this version does not handle at all.
"""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from invoice_automation.config import RuleConfig  # noqa: E402
from invoice_automation.extraction import EXTRACTION_METHOD, extract_invoice  # noqa: E402
from invoice_automation.models import (  # noqa: E402
    UNCLASSIFIED_DOCUMENT_TYPE,
    NormalizedInvoice,
    RawInvoice,
    normalize_invoice,
)

MANIFEST = PROJECT_ROOT / "data" / "ground_truth" / "pdf_cases.json"
UNSEEN_MANIFEST = PROJECT_ROOT / "data" / "ground_truth" / "pdf_unseen_cases.json"
UNSEEN_PDFS = PROJECT_ROOT / "data" / "fixtures" / "pdf_unseen"
STRUCTURED = PROJECT_ROOT / "data" / "fixtures" / "structured"
PDFS = PROJECT_ROOT / "data" / "fixtures" / "pdf"
RESULTS = PROJECT_ROOT / "evaluation" / "results"

#: Header fields compared individually.
HEADER_FIELDS = (
    "invoice_number_normalized",
    "supplier_reference_normalized",
    "invoice_date",
    "due_date",
    "currency",
    "purchase_order_number_normalized",
    "subtotal",
    "tax_total",
    "total",
    "document_type",
)

#: Fields that must all be right for a document to count as correct. The
#: supplier name is excluded: it is free text used for display, not for
#: matching, and the rules never key on it.
CRITICAL_FIELDS = (
    "invoice_number_normalized",
    "supplier_reference_normalized",
    "invoice_date",
    "currency",
    "purchase_order_number_normalized",
    "subtotal",
    "tax_total",
    "total",
    "document_type",
)

#: Business outcomes this version excludes rather than processes.
EXCLUDED_STATES = ("unsupported",)


@dataclass
class Tally:
    correct: int = 0
    total: int = 0

    def add(self, is_correct: bool) -> None:
        self.total += 1
        self.correct += 1 if is_correct else 0

    @property
    def accuracy(self) -> float | None:
        return None if self.total == 0 else round(self.correct / self.total, 4)

    def as_dict(self) -> dict:
        return {
            "correct": self.correct,
            "total": self.total,
            "accuracy": self.accuracy,
        }


def line_tuples(invoice: NormalizedInvoice) -> list[tuple]:
    return [
        (
            line.description,
            line.quantity,
            line.unit_price,
            line.line_amount,
        )
        for line in invoice.lines
    ]


def compare(truth: NormalizedInvoice, predicted: NormalizedInvoice) -> dict:
    """Field-by-field comparison of two normalised invoices."""
    fields: dict[str, dict] = {}
    for name in HEADER_FIELDS:
        expected = getattr(truth, name)
        observed = getattr(predicted, name)
        fields[name] = {
            "expected": _render(expected),
            "observed": _render(observed),
            "correct": expected == observed,
            "present_in_truth": expected is not None,
        }

    expected_lines = line_tuples(truth)
    observed_lines = line_tuples(predicted)
    fields["line_items"] = {
        "expected": [[_render(value) for value in line] for line in expected_lines],
        "observed": [[_render(value) for value in line] for line in observed_lines],
        "correct": expected_lines == observed_lines,
        "present_in_truth": bool(expected_lines),
    }
    return fields


def _render(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def unseen_layout_probe(config: RuleConfig) -> dict:
    """What the extractor does with a layout it was never taught.

    This is the only figure on this dataset that is not the extractor marking
    its own homework, so it is reported next to the headline numbers rather
    than in a footnote.

    The number that matters is ``fields_invented``: values the extractor
    produced that are wrong. A system that fails by reading nothing is safe,
    because the rules then route the document to a person. A system that fails
    by reading the wrong label into a field is not, because a wrong total that
    reconciles with a wrong subtotal can pass every arithmetic check.
    """
    if not UNSEEN_MANIFEST.exists():
        return {"available": False}

    manifest = json.loads(UNSEEN_MANIFEST.read_text(encoding="utf-8"))
    recognised = Tally()
    recovered = Tally()
    invented = 0
    cases: list[dict] = []

    for case in manifest["cases"]:
        truth_payload = json.loads(
            (STRUCTURED / case["ground_truth"]).read_text(encoding="utf-8")
        )
        truth = normalize_invoice(
            RawInvoice.model_validate(truth_payload), config
        ).normalized
        extraction = extract_invoice(UNSEEN_PDFS / case["pdf"])
        predicted = normalize_invoice(extraction.raw, config).normalized

        recognised.add(extraction.layout is not None)
        case_invented = []
        for name in HEADER_FIELDS:
            expected = getattr(truth, name)
            observed = getattr(predicted, name)
            if expected is not None:
                recovered.add(expected == observed)
            # An explicit "I could not classify this" is a refusal, not an
            # invented value, so it does not count here. Counting it would
            # overstate the failure and hide the property being measured.
            refusal = (
                name == "document_type" and observed == UNCLASSIFIED_DOCUMENT_TYPE
            )
            if observed is not None and observed != expected and not refusal:
                invented += 1
                case_invented.append(name)

        cases.append(
            {
                "case_id": case["case_id"],
                "layout_drawn": case["layout"],
                "layout_detected": extraction.layout,
                "fields_invented": case_invented,
                "line_items_read": len(predicted.lines),
            }
        )

    return {
        "available": True,
        "dataset": manifest["dataset"],
        "document_count": len(cases),
        "layout_recognised": recognised.as_dict(),
        "fields_recovered_of_those_present": recovered.as_dict(),
        "fields_invented": invented,
        "fields_invented_definition": (
            "Fields where the extractor produced a value that differs from the "
            "ground truth. An explicit unclassified marker on document_type is "
            "a refusal rather than a value, and is not counted."
        ),
        "interpretation": (
            "The extractor is not taught this layout. Recognising none of them "
            "and inventing no values is the designed behaviour: the documents "
            "then fail REQUIRED_FIELD_MISSING and are routed to review rather "
            "than validated on values read with the wrong labels."
        ),
        "cases": cases,
    }


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    config = RuleConfig()

    per_field: dict[str, Tally] = {name: Tally() for name in (*HEADER_FIELDS, "line_items")}
    per_field_present: dict[str, Tally] = {
        name: Tally() for name in (*HEADER_FIELDS, "line_items")
    }
    per_layout: dict[str, Tally] = {}
    layout_detection = Tally()
    documents_all = Tally()
    documents_included = Tally()
    cases_out: list[dict] = []

    for case in manifest["cases"]:
        truth_payload = json.loads(
            (STRUCTURED / case["ground_truth"]).read_text(encoding="utf-8")
        )
        truth = normalize_invoice(RawInvoice.model_validate(truth_payload), config).normalized

        extraction = extract_invoice(PDFS / case["pdf"])
        predicted = normalize_invoice(extraction.raw, config).normalized

        fields = compare(truth, predicted)
        layout_detection.add(extraction.layout == case["layout"])

        layout = case["layout"]
        per_layout.setdefault(layout, Tally())

        excluded = case["expected_state"] in EXCLUDED_STATES
        critical_ok = all(fields[name]["correct"] for name in CRITICAL_FIELDS)
        documents_all.add(critical_ok)
        if not excluded:
            documents_included.add(critical_ok)
        per_layout[layout].add(critical_ok)

        for name, outcome in fields.items():
            per_field[name].add(outcome["correct"])
            if outcome["present_in_truth"]:
                per_field_present[name].add(outcome["correct"])

        cases_out.append(
            {
                "case_id": case["case_id"],
                "pdf": case["pdf"],
                "layout_expected": case["layout"],
                "layout_detected": extraction.layout,
                "business_excluded": excluded,
                "critical_fields_correct": critical_ok,
                "fields": fields,
            }
        )

    result = {
        "run_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "dataset": manifest["dataset"],
        "synthetic": True,
        "extractor": EXTRACTION_METHOD,
        "python": platform.python_version(),
        "document_count": len(cases_out),
        "configuration": {
            "currency": config.currency,
            "money_decimal_places": config.money_decimal_places,
            "money_rounding": config.money_rounding,
        },
        "definitions": {
            "field_accuracy_all": "correct normalised field values / all labelled field values; a field absent in both counts as correct",
            "field_accuracy_present": "correct normalised field values / field values that carry a value in the ground truth",
            "critical_field_document_accuracy": "documents where every critical field is correct / documents; critical fields are "
            + ", ".join(CRITICAL_FIELDS),
            "layout_detection": "documents whose layout was identified as the one it was drawn in / documents",
            "business_excluded": "documents the rules exclude rather than process (credit note, unconfigured currency); reported separately so they cannot distort the figures",
        },
        "summary": {
            "layout_detection": layout_detection.as_dict(),
            "critical_field_document_accuracy_all": documents_all.as_dict(),
            "critical_field_document_accuracy_excluding_business_exclusions": documents_included.as_dict(),
            "per_field_all": {name: tally.as_dict() for name, tally in per_field.items()},
            "per_field_present_in_truth": {
                name: tally.as_dict() for name, tally in per_field_present.items()
            },
            "per_layout_critical_field_document_accuracy": {
                name: tally.as_dict() for name, tally in per_layout.items()
            },
        },
        "unseen_layout_probe": unseen_layout_probe(config),
        "limitations": [
            "Synthetic documents only. The layouts and the extractor's label sets were written together, so this measures the extractor against its own assumptions.",
            "Digitally generated PDFs with an embedded text layer. No scanned documents and no OCR.",
            "16 documents across 3 layouts. Too few to estimate a rate with any precision; no confidence intervals are reported because none would be meaningful.",
            "No unseen layout is held out, so nothing here speaks to generalisation. The plan's frozen split with an unseen layout is still to come.",
        ],
        "cases": cases_out,
    }

    json_path = RESULTS / "extraction_baseline.json"
    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")

    md_path = RESULTS / "extraction_baseline.md"
    md_path.write_text(_markdown(result), encoding="utf-8", newline="\n")

    print(f"documents: {result['document_count']}")
    print(f"layout detection: {layout_detection.correct}/{layout_detection.total}")
    print(
        "critical-field document accuracy: "
        f"{documents_all.correct}/{documents_all.total}"
    )
    for name, tally in per_field_present.items():
        print(f"  {name:34} {tally.correct}/{tally.total}")
    probe = result["unseen_layout_probe"]
    if probe.get("available"):
        print(
            "\nunseen-layout probe: "
            f"layout recognised {probe['layout_recognised']['correct']}/"
            f"{probe['layout_recognised']['total']}, "
            f"fields recovered {probe['fields_recovered_of_those_present']['correct']}/"
            f"{probe['fields_recovered_of_those_present']['total']}, "
            f"fields invented {probe['fields_invented']}"
        )
    print(f"\nwrote {json_path.relative_to(PROJECT_ROOT)}")
    print(f"wrote {md_path.relative_to(PROJECT_ROOT)}")
    return 0


def _markdown(result: dict) -> str:
    summary = result["summary"]
    lines = [
        "# Extraction baseline",
        "",
        f"Run {result['run_at']} against `{result['dataset']}`, "
        f"extractor `{result['extractor']}`.",
        "",
        "**Synthetic data.** Every document was generated by "
        "`data/generate_pdf_fixtures.py` from the structured payloads, which are "
        "therefore the ground truth.",
        "",
        "## Results",
        "",
        "| Measure | Value |",
        "|---|---|",
    ]

    def rate(entry: dict) -> str:
        if entry["accuracy"] is None:
            return "n/a"
        return f"{entry['correct']}/{entry['total']} ({entry['accuracy']:.0%})"

    lines += [
        f"| Documents | {result['document_count']} |",
        f"| Layout detection | {rate(summary['layout_detection'])} |",
        f"| Critical-field document accuracy | {rate(summary['critical_field_document_accuracy_all'])} |",
        f"| Critical-field document accuracy, business exclusions removed | {rate(summary['critical_field_document_accuracy_excluding_business_exclusions'])} |",
        "",
        "### Per field",
        "",
        "| Field | All labelled fields | Fields present in ground truth |",
        "|---|---|---|",
    ]
    for name in summary["per_field_all"]:
        lines.append(
            f"| `{name}` | {rate(summary['per_field_all'][name])} "
            f"| {rate(summary['per_field_present_in_truth'][name])} |"
        )

    lines += ["", "### Per layout", "", "| Layout | Critical-field document accuracy |", "|---|---|"]
    for name, entry in summary["per_layout_critical_field_document_accuracy"].items():
        lines.append(f"| `{name}` | {rate(entry)} |")

    probe = result.get("unseen_layout_probe", {})
    if probe.get("available"):
        lines += [
            "",
            "## Held-out probe: a layout the extractor was not taught",
            "",
            f"`{probe['dataset']}`, {probe['document_count']} documents.",
            "",
            "| Measure | Value |",
            "|---|---|",
            f"| Layout recognised | {rate(probe['layout_recognised'])} |",
            f"| Fields recovered, of those present | {rate(probe['fields_recovered_of_those_present'])} |",
            f"| **Fields invented (wrong values produced)** | **{probe['fields_invented']}** |",
            "",
            probe["interpretation"],
        ]

    lines += ["", "## Definitions", ""]
    for name, text in result["definitions"].items():
        lines.append(f"- **{name}** — {text}")

    lines += ["", "## What this does not establish", ""]
    for item in result["limitations"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
