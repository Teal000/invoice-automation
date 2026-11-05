"""Deterministic validation rules.

Each rule is a pure function of a :class:`~invoice_automation.rules.base.ValidationContext`.
Lookups happen before the rules run, so the rules perform no I/O and are
testable on their own.
"""

from .base import (
    Disposition,
    DuplicateMatch,
    PurchaseOrderRecord,
    RuleResult,
    RuleStatus,
    Severity,
    SupplierRecord,
    ValidationContext,
)
from .engine import (
    ALL_RULE_CODES,
    RULE_ORDER,
    BusinessState,
    ValidationOutcome,
    decide_state,
    run_rules,
)

__all__ = [
    "ALL_RULE_CODES",
    "RULE_ORDER",
    "BusinessState",
    "Disposition",
    "DuplicateMatch",
    "PurchaseOrderRecord",
    "RuleResult",
    "RuleStatus",
    "Severity",
    "SupplierRecord",
    "ValidationContext",
    "ValidationOutcome",
    "decide_state",
    "run_rules",
]
