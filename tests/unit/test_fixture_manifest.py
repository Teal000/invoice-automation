"""The labelled fixture set, checked without a database.

The rules are pure functions over a prepared context, so the whole labelled
dataset can be replayed in memory: the supplier and purchase-order registries
come from the same JSON the CLI seeds, and the duplicate lookups are served by
an in-memory stand-in that records what has already been seen, in manifest
order, exactly as the database does.

This catches drift between the fixtures, the manifest and the rules on every
test run. It does not replace the integration test of the same manifest: this
one proves the expected codes are right, that one proves the database records
them.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from invoice_automation.config import PROJECT_ROOT, RuleConfig
from invoice_automation.models import (
    RawInvoice,
    SubmissionInput,
    normalize_identifier,
    normalize_invoice,
)
from invoice_automation.rules.base import (
    DuplicateMatch,
    PurchaseOrderRecord,
    SupplierRecord,
    ValidationContext,
)
from invoice_automation.rules.engine import ALL_RULE_CODES, run_rules

FIXTURES = PROJECT_ROOT / "data" / "fixtures" / "structured"
REFERENCE = PROJECT_ROOT / "data" / "fixtures" / "reference"
MANIFEST = PROJECT_ROOT / "data" / "ground_truth" / "structured_cases.json"
NAMESPACE = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _registries() -> tuple[dict[str, SupplierRecord], dict[str, PurchaseOrderRecord]]:
    suppliers_raw = json.loads((REFERENCE / "suppliers.json").read_text("utf-8"))
    orders_raw = json.loads((REFERENCE / "purchase_orders.json").read_text("utf-8"))

    suppliers = {}
    for entry in suppliers_raw["suppliers"]:
        reference = entry["reference_normalized"]
        suppliers[reference] = SupplierRecord(
            supplier_id=uuid5(NAMESPACE, reference),
            name=entry["name"],
            reference_normalized=reference,
            permitted_currency=entry["permitted_currency"],
            is_active=entry.get("is_active", True),
        )

    orders = {}
    for entry in orders_raw["purchase_orders"]:
        number = entry["po_number_normalized"]
        po_supplier = suppliers[normalize_identifier(entry["supplier_reference"])]
        orders[number] = PurchaseOrderRecord(
            purchase_order_id=uuid5(NAMESPACE, number),
            po_number_normalized=number,
            supplier_id=po_supplier.supplier_id,
            currency=entry["currency"],
            expected_amount=Decimal(entry["expected_amount"]),
            state=entry.get("state", "open"),
            supplier_name=po_supplier.name,
            supplier_reference=po_supplier.reference_normalized,
        )
    return suppliers, orders


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text("utf-8"))


@pytest.fixture(scope="module")
def replayed(manifest) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Run every case in manifest order and return its outcome."""
    suppliers, orders = _registries()
    config = RuleConfig()

    seen_hashes: dict[str, str] = {}  # file hash -> case id
    seen_invoices: dict[tuple[str, str], str] = {}  # (supplier key, number) -> case id
    outcomes: dict[str, tuple[str, tuple[str, ...]]] = {}

    for case in manifest["cases"]:
        path: Path = FIXTURES / case["file"]
        raw_bytes = path.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        raw = RawInvoice.model_validate(json.loads(raw_bytes))
        submission = SubmissionInput(
            source="cli", original_file_name=path.name, file_sha256=digest
        )

        result = normalize_invoice(raw, config)
        invoice = result.normalized
        supplier = suppliers.get(invoice.supplier_reference_normalized or "")
        order = orders.get(invoice.purchase_order_number_normalized or "")
        key = (
            str(supplier.supplier_id)
            if supplier
            else f"unresolved:{invoice.supplier_reference_normalized}"
        )

        file_duplicate = (
            DuplicateMatch(kind="file_hash", matched_value=digest)
            if digest in seen_hashes
            else None
        )
        identity = (key, invoice.invoice_number_normalized or f"unidentified:{case['case_id']}")
        business_duplicate = (
            DuplicateMatch(
                kind="supplier_invoice_number", matched_value=identity[1]
            )
            if identity in seen_invoices
            else None
        )

        outcome = run_rules(
            ValidationContext(
                invoice=invoice,
                submission=submission,
                config=config,
                problems=result.problems,
                supplier=supplier,
                purchase_order=order,
                file_duplicate=file_duplicate,
                business_duplicate=business_duplicate,
            )
        )
        outcomes[case["case_id"]] = (str(outcome.state), outcome.failed_codes)

        # Record what the database would now hold.
        seen_hashes.setdefault(digest, case["case_id"])
        if file_duplicate is None:
            seen_invoices.setdefault(identity, case["case_id"])

    return outcomes


def test_every_case_produces_its_labelled_outcome(manifest, replayed):
    mismatches = [
        (case["case_id"], (case["expected_state"], tuple(case["expected_failed_codes"])), replayed[case["case_id"]])
        for case in manifest["cases"]
        if replayed[case["case_id"]]
        != (case["expected_state"], tuple(case["expected_failed_codes"]))
    ]
    assert mismatches == []


def test_the_manifest_covers_every_rule_code(manifest):
    covered = {
        code for case in manifest["cases"] for code in case["expected_failed_codes"]
    }
    assert covered == set(ALL_RULE_CODES)


def test_each_failure_case_is_isolated(manifest, replayed):
    """A labelled failure case must fail exactly the code it is named for."""
    for case in manifest["cases"]:
        expected = tuple(case["expected_failed_codes"])
        if len(expected) == 1:
            assert replayed[case["case_id"]][1] == expected, case["case_id"]


def test_the_duplicate_pair_is_byte_identical(manifest):
    first = (FIXTURES / "valid_001.json").read_bytes()
    resend = (FIXTURES / "exact_file_duplicate_016.json").read_bytes()
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(resend).hexdigest()


def test_the_business_duplicate_case_is_a_different_file(manifest):
    first = (FIXTURES / "valid_001.json").read_bytes()
    other = (FIXTURES / "business_duplicate_015.json").read_bytes()
    assert first != other
    first_payload = json.loads(first)
    other_payload = json.loads(other)
    # Same supplier and invoice number, different content: a review case, not
    # a silent link.
    assert first_payload["invoice_number"] == other_payload["invoice_number"]
    assert first_payload["supplier_reference"] == other_payload["supplier_reference"]
