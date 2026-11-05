"""Builders for rule unit tests.

The baseline invoice is internally consistent at three decimal places:

    line 1: 10      x 120.050 = 1200.500
    line 2:  3      x  45.750 =  137.250
    subtotal                  = 1337.750
    tax at the configured 19% =  254.173   (1337.750 x 0.19 = 254.1725, half up)
    total                     = 1591.923

The purchase order carries the same expected amount, so the baseline passes
every check. Each test then changes exactly one thing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from invoice_automation.config import RuleConfig
from invoice_automation.models import (
    RawInvoice,
    RawLine,
    SubmissionInput,
    normalize_invoice,
)
from invoice_automation.rules.base import (
    DuplicateMatch,
    PurchaseOrderRecord,
    SupplierRecord,
    ValidationContext,
)

SUPPLIER_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_SUPPLIER_ID = UUID("22222222-2222-4222-8222-222222222222")
PO_ID = UUID("33333333-3333-4333-8333-333333333333")

BASELINE_TOTAL = Decimal("1591.923")


def rule_config(**overrides) -> RuleConfig:
    return RuleConfig(**overrides)


def raw_invoice(**overrides) -> RawInvoice:
    """A consistent invoice; pass keyword arguments to break exactly one thing."""
    data = dict(
        document_type="invoice",
        invoice_number="FA-2026-0147",
        supplier_reference="SUP-001",
        supplier_name="Fournitures Atlas SARL (synthetic)",
        invoice_date="2026-03-12",
        due_date="2026-04-11",
        currency="TND",
        purchase_order_number="PO-2026-0031",
        subtotal="1337.750",
        tax_total="254.173",
        total="1591.923",
        line_items=[
            RawLine(
                line_number=1,
                description="Ramettes papier A4 80g",
                quantity="10",
                unit_price="120.050",
                line_amount="1200.500",
            ),
            RawLine(
                line_number=2,
                description="Cartouches encre noire",
                quantity="3",
                unit_price="45.750",
                line_amount="137.250",
            ),
        ],
    )
    data.update(overrides)
    return RawInvoice(**data)


def submission(**overrides) -> SubmissionInput:
    data = dict(
        source="cli",
        original_file_name="FA-2026-0147.json",
        file_sha256="a" * 64,
        idempotency_key=None,
        received_at=datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc),
    )
    data.update(overrides)
    return SubmissionInput(**data)


def supplier(**overrides) -> SupplierRecord:
    data = dict(
        supplier_id=SUPPLIER_ID,
        name="Fournitures Atlas SARL (synthetic)",
        reference_normalized="SUP-001",
        permitted_currency="TND",
        is_active=True,
    )
    data.update(overrides)
    return SupplierRecord(**data)


def purchase_order(**overrides) -> PurchaseOrderRecord:
    data = dict(
        purchase_order_id=PO_ID,
        po_number_normalized="PO-2026-0031",
        supplier_id=SUPPLIER_ID,
        currency="TND",
        expected_amount=BASELINE_TOTAL,
        state="open",
    )
    data.update(overrides)
    return PurchaseOrderRecord(**data)


def duplicate_match(**overrides) -> DuplicateMatch:
    data = dict(
        kind="file_hash",
        submission_id=UUID("44444444-4444-4444-8444-444444444444"),
        invoice_id=UUID("55555555-5555-4555-8555-555555555555"),
        matched_value="a" * 64,
        first_seen_at=datetime(2026, 3, 12, 8, 0, tzinfo=timezone.utc),
    )
    data.update(overrides)
    return DuplicateMatch(**data)


def context(
    raw: RawInvoice | None = None,
    *,
    config: RuleConfig | None = None,
    with_supplier: bool = True,
    with_purchase_order: bool = True,
    supplier_record: SupplierRecord | None = None,
    purchase_order_record: PurchaseOrderRecord | None = None,
    file_duplicate: DuplicateMatch | None = None,
    business_duplicate: DuplicateMatch | None = None,
    submission_input: SubmissionInput | None = None,
) -> ValidationContext:
    """Build a context the way the pipeline would, with lookups already done."""
    cfg = config or rule_config()
    raw = raw if raw is not None else raw_invoice()
    result = normalize_invoice(raw, cfg)

    resolved_supplier = supplier_record
    if resolved_supplier is None and with_supplier:
        resolved_supplier = supplier()

    resolved_po = purchase_order_record
    if resolved_po is None and with_purchase_order:
        resolved_po = purchase_order()

    return ValidationContext(
        invoice=result.normalized,
        submission=submission_input or submission(),
        config=cfg,
        problems=result.problems,
        supplier=resolved_supplier,
        purchase_order=resolved_po,
        file_duplicate=file_duplicate,
        business_duplicate=business_duplicate,
    )
