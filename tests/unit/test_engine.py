"""The rule engine: outcome mapping and one isolated case per rule code.

The case table below is the unit-level form of the plan's acceptance check
"every deliberately introduced validation failure produces its expected
exception code". Each case changes exactly one thing and asserts that exactly
one code fails, so a rule that fires too broadly is caught here. The final
test asserts the table covers every code the engine can emit.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from factories import (
    OTHER_SUPPLIER_ID,
    context,
    duplicate_match,
    purchase_order,
    raw_invoice,
    supplier,
)

from invoice_automation.models import RawLine
from invoice_automation.rules.engine import (
    ALL_RULE_CODES,
    BusinessState,
    RuleStatus,
    run_rules,
)

BASELINE_LINES = [
    RawLine(line_number=1, description="Ramettes papier A4 80g", quantity="10",
            unit_price="120.050", line_amount="1200.500"),
    RawLine(line_number=2, description="Cartouches encre noire", quantity="3",
            unit_price="45.750", line_amount="137.250"),
]


def _lines_with_wrong_unit_price():
    """Line amount still sums to the subtotal, but qty x price does not match."""
    return [
        RawLine(line_number=1, description="Ramettes papier A4 80g", quantity="10",
                unit_price="130.050", line_amount="1200.500"),
        BASELINE_LINES[1],
    ]


#: (code, context) pairs. Each context fails exactly that one code.
CASES = {
    "EXACT_FILE_DUPLICATE": lambda: context(file_duplicate=duplicate_match()),
    "UNSUPPORTED_DOCUMENT": lambda: context(raw_invoice(document_type="credit_note")),
    "CURRENCY_UNSUPPORTED": lambda: context(raw_invoice(currency="EUR")),
    "REQUIRED_FIELD_MISSING": lambda: context(raw_invoice(invoice_number=None)),
    "DATE_INCONSISTENT": lambda: context(
        raw_invoice(invoice_date="2026-03-12", due_date="2026-03-01")
    ),
    "UNKNOWN_SUPPLIER": lambda: context(
        raw_invoice(supplier_reference="SUP-999"), with_supplier=False
    ),
    "BUSINESS_DUPLICATE": lambda: context(
        business_duplicate=duplicate_match(
            kind="supplier_invoice_number", matched_value="FA-2026-0147"
        )
    ),
    "LINE_TOTAL_MISMATCH": lambda: context(
        raw_invoice(line_items=_lines_with_wrong_unit_price())
    ),
    # Subtotal is wrong while the stated total still reconciles with it.
    "SUBTOTAL_MISMATCH": lambda: context(
        raw_invoice(subtotal="1400.000", tax_total="191.923")
    ),
    # Total is wrong by 8.077, which is inside the purchase-order allowance.
    "TOTAL_MISMATCH": lambda: context(raw_invoice(total="1600.000")),
    "PO_NOT_FOUND": lambda: context(
        raw_invoice(purchase_order_number="PO-9999"), with_purchase_order=False
    ),
    "PO_SUPPLIER_MISMATCH": lambda: context(
        purchase_order_record=purchase_order(supplier_id=OTHER_SUPPLIER_ID)
    ),
    "PO_AMOUNT_MISMATCH": lambda: context(
        purchase_order_record=purchase_order(expected_amount=Decimal("1200.000"))
    ),
}

#: The business state each code should produce on its own.
EXPECTED_STATE = {
    "EXACT_FILE_DUPLICATE": BusinessState.DUPLICATE,
    "UNSUPPORTED_DOCUMENT": BusinessState.UNSUPPORTED,
    "CURRENCY_UNSUPPORTED": BusinessState.UNSUPPORTED,
}


def test_baseline_invoice_is_validated():
    outcome = run_rules(context())
    assert outcome.state is BusinessState.VALIDATED
    assert outcome.failed == ()
    # Every code was evaluated and none was skipped on the baseline.
    assert {r.code for r in outcome.results} == set(ALL_RULE_CODES)
    assert not any(r.status is RuleStatus.SKIPPED for r in outcome.results)


@pytest.mark.parametrize("code", sorted(CASES))
def test_each_case_fails_exactly_its_own_code(code):
    outcome = run_rules(CASES[code]())
    assert outcome.failed_codes == (code,)


@pytest.mark.parametrize("code", sorted(CASES))
def test_each_case_produces_the_expected_business_state(code):
    outcome = run_rules(CASES[code]())
    assert outcome.state is EXPECTED_STATE.get(code, BusinessState.NEEDS_REVIEW)


def test_identical_file_short_circuits_the_remaining_checks():
    outcome = run_rules(CASES["EXACT_FILE_DUPLICATE"]())
    assert outcome.short_circuited is True
    # Only the duplicate check ran: a repeat arrival is not reprocessed.
    assert [r.code for r in outcome.results] == ["EXACT_FILE_DUPLICATE"]


def test_unsupported_outranks_review():
    """A document this version cannot handle is not sent to a reviewer."""
    ctx = context(raw_invoice(currency="EUR", total="1600.000"))
    outcome = run_rules(ctx)
    assert "CURRENCY_UNSUPPORTED" in outcome.failed_codes
    assert "TOTAL_MISMATCH" in outcome.failed_codes
    assert outcome.state is BusinessState.UNSUPPORTED


def test_several_failures_are_all_recorded():
    ctx = context(
        raw_invoice(subtotal="1400.000", due_date="2026-03-01"),
        with_supplier=False,
    )
    outcome = run_rules(ctx)
    assert set(outcome.failed_codes) >= {
        "DATE_INCONSISTENT",
        "UNKNOWN_SUPPLIER",
        "SUBTOTAL_MISMATCH",
        "TOTAL_MISMATCH",
    }
    assert outcome.state is BusinessState.NEEDS_REVIEW


def test_a_skipped_check_is_not_counted_as_a_pass():
    ctx = context(raw_invoice(purchase_order_number=None), with_purchase_order=False)
    outcome = run_rules(ctx)
    assert outcome.code_status("PO_NOT_FOUND") is RuleStatus.SKIPPED
    # The missing PO number itself is the failure, and the invoice is not
    # validated on the strength of three skipped purchase-order checks.
    assert outcome.failed_codes == ("REQUIRED_FIELD_MISSING",)
    assert outcome.state is BusinessState.NEEDS_REVIEW


def test_inactive_supplier_is_a_review_case():
    outcome = run_rules(context(supplier_record=supplier(is_active=False)))
    assert outcome.failed_codes == ("UNKNOWN_SUPPLIER",)
    assert outcome.state is BusinessState.NEEDS_REVIEW


def test_every_rule_code_has_a_case():
    """No rule may ship without a case that makes it fail."""
    assert set(CASES) == set(ALL_RULE_CODES)
