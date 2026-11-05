"""The invoice data contract.

Three layers, kept separate because the validation rules depend on the
distinction:

``RawInvoice``
    What arrived. Every field is an optional string, because an extracted
    document is allowed to be missing a field or to carry a value that cannot
    be parsed. Nothing here is trusted.

``NormalizedInvoice``
    The parsed form. Fields that could not be parsed stay ``None`` and the
    reason is recorded in a :class:`FieldProblem`, so a missing value is never
    silently replaced with a zero or with the current date.

``SubmissionInput``
    Facts about the arrival itself (file name, hash, source, idempotency key)
    rather than about the invoice. A submission is not an invoice: the same
    invoice can arrive twice, and both arrivals are recorded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import RuleConfig
from .money import MoneyParseError, parse_decimal, quantize_money

#: Document kinds this version handles. Anything else is reported as
#: UNSUPPORTED_DOCUMENT rather than approximated.
SUPPORTED_DOCUMENT_TYPE = "invoice"

#: What an extractor reports when it could not tell what the document is.
#: Distinct from a recognised kind that this version excludes: "this is a
#: credit note and we do not handle credit notes" is a business exclusion,
#: while "we could not read this document" is a review case. Treating the
#: second as the first would quietly move unreadable documents out of the
#: review queue and into a bucket nobody looks at.
UNCLASSIFIED_DOCUMENT_TYPE = "unknown"
KNOWN_UNSUPPORTED_DOCUMENT_TYPES = (
    "credit_note",
    "debit_note",
    "partial_invoice",
    "proforma",
    "statement",
)

#: Accepted date input formats, tried in this order. ISO first.
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_identifier(value: str | None) -> str | None:
    """Conservative identifier normalisation for matching.

    Applied to supplier invoice numbers, supplier references and PO numbers:

    1. Trim leading and trailing whitespace.
    2. Collapse internal whitespace runs to a single space.
    3. Upper-case.

    No characters are removed. Hyphens, slashes, dots, prefixes and leading
    zeros are all preserved, because removing them can merge two genuinely
    different invoices (``FA-001`` and ``FA001`` stay distinct). The original
    string is always stored alongside the normalised one.
    """
    if value is None:
        return None
    collapsed = _WHITESPACE_RUN.sub(" ", value.strip())
    if not collapsed:
        return None
    return collapsed.upper()


def parse_date(value: str | None) -> date | None:
    """Parse a date in one of :data:`DATE_FORMATS`, else return ``None``."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class RawLine(BaseModel):
    """One line item as extracted, before parsing."""

    model_config = ConfigDict(extra="forbid")

    line_number: int | None = None
    description: str | None = None
    quantity: str | None = None
    unit_price: str | None = None
    line_amount: str | None = None


class RawInvoice(BaseModel):
    """Header fields and lines as extracted, before parsing."""

    model_config = ConfigDict(extra="forbid")

    document_type: str | None = SUPPORTED_DOCUMENT_TYPE
    invoice_number: str | None = None
    supplier_reference: str | None = None
    supplier_name: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    currency: str | None = None
    purchase_order_number: str | None = None
    subtotal: str | None = None
    tax_total: str | None = None
    total: str | None = None
    line_items: list[RawLine] = Field(default_factory=list)

    #: How these values were produced, e.g. "structured_fixture:v1".
    extraction_method: str = "structured_fixture:v1"


class SubmissionInput(BaseModel):
    """Facts about an arrival, not about the invoice it contains."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["cli", "api_upload", "mailbox", "n8n"] = "cli"
    original_file_name: str
    file_sha256: str
    idempotency_key: str | None = None
    received_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    #: Path of the stored original, relative to the storage root.
    storage_path: str | None = None


@dataclass(frozen=True)
class FieldProblem:
    """A field that is absent or could not be parsed."""

    field_name: str
    raw_value: str | None
    reason: Literal["missing", "unparseable"]


@dataclass(frozen=True)
class NormalizedLine:
    line_number: int
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    line_amount: Decimal | None


@dataclass(frozen=True)
class NormalizedInvoice:
    """Parsed values. ``None`` means absent or unparseable, never zero."""

    document_type: str
    invoice_number: str | None
    invoice_number_normalized: str | None
    supplier_reference: str | None
    supplier_reference_normalized: str | None
    supplier_name: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str | None
    purchase_order_number: str | None
    purchase_order_number_normalized: str | None
    subtotal: Decimal | None
    tax_total: Decimal | None
    total: Decimal | None
    lines: tuple[NormalizedLine, ...] = ()


@dataclass(frozen=True)
class NormalizationResult:
    normalized: NormalizedInvoice
    problems: tuple[FieldProblem, ...] = ()

    def problem_fields(self) -> tuple[str, ...]:
        return tuple(problem.field_name for problem in self.problems)


def _parse_amount(
    raw: str | None,
    field_name: str,
    config: RuleConfig,
    problems: list[FieldProblem],
    quantize: bool = True,
) -> Decimal | None:
    """Parse one amount, recording a problem instead of guessing a value."""
    if raw is None or str(raw).strip() == "":
        problems.append(FieldProblem(field_name, raw, "missing"))
        return None
    try:
        value = parse_decimal(raw)
    except MoneyParseError:
        problems.append(FieldProblem(field_name, raw, "unparseable"))
        return None
    if not quantize:
        return value
    return quantize_money(value, config.money_decimal_places, config.money_rounding)


def normalize_invoice(raw: RawInvoice, config: RuleConfig) -> NormalizationResult:
    """Parse a :class:`RawInvoice` and report what could not be parsed.

    This function never guesses. A value that cannot be read becomes ``None``
    with a recorded reason; the rules then decide what that means.
    """
    problems: list[FieldProblem] = []

    def identifier(value: str | None, field_name: str) -> tuple[str | None, str | None]:
        if value is None or not value.strip():
            problems.append(FieldProblem(field_name, value, "missing"))
            return None, None
        return value, normalize_identifier(value)

    invoice_number, invoice_number_normalized = identifier(
        raw.invoice_number, "invoice_number"
    )
    supplier_reference, supplier_reference_normalized = identifier(
        raw.supplier_reference, "supplier_reference"
    )
    po_number, po_number_normalized = identifier(
        raw.purchase_order_number, "purchase_order_number"
    )

    def parsed_date(value: str | None, field_name: str, required: bool) -> date | None:
        if value is None or not value.strip():
            if required:
                problems.append(FieldProblem(field_name, value, "missing"))
            return None
        result = parse_date(value)
        if result is None:
            problems.append(FieldProblem(field_name, value, "unparseable"))
        return result

    invoice_date = parsed_date(raw.invoice_date, "invoice_date", required=True)
    # due_date is optional in the data contract: absent is not a problem.
    due_date = parsed_date(raw.due_date, "due_date", required=False)

    if raw.currency is None or not raw.currency.strip():
        problems.append(FieldProblem("currency", raw.currency, "missing"))
        currency = None
    else:
        currency = raw.currency.strip().upper()

    subtotal = _parse_amount(raw.subtotal, "subtotal", config, problems)
    tax_total = _parse_amount(raw.tax_total, "tax_total", config, problems)
    total = _parse_amount(raw.total, "total", config, problems)

    lines: list[NormalizedLine] = []
    if not raw.line_items:
        problems.append(FieldProblem("line_items", None, "missing"))
    for index, raw_line in enumerate(raw.line_items, start=1):
        line_number = raw_line.line_number or index
        prefix = f"line_items[{line_number}]"

        description = raw_line.description
        if description is None or not description.strip():
            problems.append(
                FieldProblem(f"{prefix}.description", description, "missing")
            )
            description = None

        lines.append(
            NormalizedLine(
                line_number=line_number,
                description=description,
                # Quantities are not money and keep their own precision.
                quantity=_parse_amount(
                    raw_line.quantity,
                    f"{prefix}.quantity",
                    config,
                    problems,
                    quantize=False,
                ),
                unit_price=_parse_amount(
                    raw_line.unit_price, f"{prefix}.unit_price", config, problems
                ),
                line_amount=_parse_amount(
                    raw_line.line_amount, f"{prefix}.line_amount", config, problems
                ),
            )
        )

    if raw.document_type is None or not raw.document_type.strip():
        problems.append(FieldProblem("document_type", raw.document_type, "missing"))
        document_type = UNCLASSIFIED_DOCUMENT_TYPE
    else:
        document_type = raw.document_type.strip().lower()
        if document_type == UNCLASSIFIED_DOCUMENT_TYPE:
            # The extractor read the document but could not classify it. That
            # is a field it failed to determine, not a document kind.
            problems.append(
                FieldProblem("document_type", raw.document_type, "unparseable")
            )

    normalized = NormalizedInvoice(
        document_type=document_type,
        invoice_number=invoice_number,
        invoice_number_normalized=invoice_number_normalized,
        supplier_reference=supplier_reference,
        supplier_reference_normalized=supplier_reference_normalized,
        supplier_name=raw.supplier_name,
        invoice_date=invoice_date,
        due_date=due_date,
        currency=currency,
        purchase_order_number=po_number,
        purchase_order_number_normalized=po_number_normalized,
        subtotal=subtotal,
        tax_total=tax_total,
        total=total,
        lines=tuple(lines),
    )
    return NormalizationResult(normalized=normalized, problems=tuple(problems))
