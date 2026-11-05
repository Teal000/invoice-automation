"""Request and response types for the API.

Typed on purpose: the schemas are the documentation, and FastAPI publishes
them at ``/docs``. Amounts are strings in JSON rather than floats, because a
JSON number is a float in most clients and these are decimals.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

AMOUNT_NOTE = "Decimal rendered as a string; never a JSON float."


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_serializer("*", when_used="json")
    def _serialise_decimals(self, value: Any) -> Any:
        if isinstance(value, Decimal):
            return format(value, "f")
        return value


class ViewModel(ApiModel):
    """A response built directly from a reporting-view row.

    Extra keys are ignored rather than rejected: the views carry columns the
    API does not publish (internal ids, a revision source), and adding a
    column to a view should not break the service. Request schemas keep
    ``extra="forbid"`` so a typo in a caller's payload is still an error.
    """

    model_config = ConfigDict(extra="ignore")


class HealthResponse(ApiModel):
    status: str = Field(examples=["ok"])
    database: str = Field(description="'reachable' or the error class name.")
    migrations_applied: int | None = None
    version: str


class ErrorBody(ApiModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str
    detail: Any | None = None


class ErrorResponse(ApiModel):
    error: ErrorBody


class SubmissionCreated(ApiModel):
    submission_id: UUID
    outcome: str = Field(description="'pending' until the submission is processed.")
    job_id: UUID | None = None
    file_sha256: str
    storage_path: str
    bytes_already_stored: bool = Field(
        description="True when an identical file was already stored by an earlier arrival."
    )
    replayed: bool = Field(
        description="True when this idempotency key had been used; nothing new was recorded."
    )
    processed: "ProcessingOutcome | None" = Field(
        default=None,
        description="Present only when the request asked for immediate processing.",
    )


class FailedCheck(ApiModel):
    code: str
    field_name: str | None = None
    expected: str | None = None
    observed: str | None = None
    message: str | None = None


class ProcessingOutcome(ApiModel):
    submission_id: UUID
    state: str = Field(
        description="validated, needs_review, duplicate, unsupported, or failed."
    )
    invoice_id: UUID | None = None
    revision_number: int | None = None
    invoice_created: bool = False
    duplicate_of_submission_id: UUID | None = None
    replayed: bool = Field(
        default=False,
        description="True when the submission had already been processed and was not processed again.",
    )
    failed_codes: list[str] = Field(default_factory=list)
    failed_checks: list[FailedCheck] = Field(default_factory=list)
    job_attempt: int | None = None


class JobState(ApiModel):
    job_id: UUID | None = None
    status: str | None = None
    attempt_count: int | None = None
    max_attempts: int | None = None
    last_error: str | None = None


class SubmissionDetail(ApiModel):
    submission_id: UUID
    source: str
    outcome: str
    received_at: datetime
    idempotency_key: str | None = None
    invoice_id: UUID | None = None
    duplicate_of_submission_id: UUID | None = None
    notes: str | None = None
    original_file_name: str
    media_type: str | None = None
    byte_size: int | None = None
    file_sha256: str
    storage_path: str | None = None
    extraction_method: str | None = None
    job: JobState


class InvoiceSummary(ViewModel):
    invoice_id: UUID
    business_state: str
    supplier_name: str | None = None
    supplier_reference: str | None = None
    invoice_number: str
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax_total: Decimal | None = None
    total: Decimal | None = None
    purchase_order_number: str | None = None
    current_revision_number: int | None = None
    current_revision_source: str | None = Field(
        default=None,
        description=(
            "Where the current values came from: 'extraction' or "
            "'reviewer_correction'. A reviewer needs to know which they are "
            "looking at."
        ),
    )
    failed_rule_codes: int = 0
    failed_checks: int = 0
    has_reviewer_edit: bool = False
    accepted_with_exception: bool = Field(
        default=False,
        description=(
            "A reviewer accepted this invoice despite a failed check. Not the "
            "same as the checks passing, and excluded from the "
            "straight-through rate."
        ),
    )
    review_action_count: int = 0
    first_recorded_at: datetime | None = None
    last_changed_at: datetime | None = None


class InvoiceList(ApiModel):
    count: int = Field(description="Number of rows in this page, not the total.")
    limit: int
    offset: int
    invoices: list[InvoiceSummary]


class InvoiceLine(ViewModel):
    line_number: int
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_amount: Decimal | None = None


class CheckResult(ViewModel):
    code: str
    status: str
    severity: str
    field_name: str | None = None
    expected_value: str | None = None
    observed_value: str | None = None
    message: str | None = None
    revision_number: int | None = None
    is_current: bool = False
    submission_level: bool = False


class RevisionSummary(ViewModel):
    revision_number: int
    source: str
    created_by: str
    created_at: datetime


class EventSummary(ViewModel):
    occurred_at: datetime
    event_type: str
    entity_type: str
    actor: str
    details: dict[str, Any] = Field(default_factory=dict)


class InvoiceDetail(InvoiceSummary):
    lines: list[InvoiceLine] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    revisions: list[RevisionSummary] = Field(default_factory=list)
    review_actions: list["ReviewActionSummary"] = Field(default_factory=list)
    events: list[EventSummary] = Field(default_factory=list)


class ExceptionCount(ViewModel):
    code: str
    invoices_affected: int
    failed_checks: int


class SummaryReport(ViewModel):
    """Aggregates straight from the reporting views.

    Every denominator is documented in dashboard/metric_definitions.md. The
    two rates are shares of *eligible* invoices, which excludes duplicates and
    unsupported documents, and are null when there are none.
    """

    submissions_received: int
    duplicate_arrivals: int
    failed_submissions: int
    pending_submissions: int
    unique_invoices: int
    eligible_invoices: int
    validated_invoices: int
    needs_review_invoices: int
    unsupported_invoices: int
    rejected_invoices: int
    duplicate_invoices: int
    validated_without_edit: int
    review_rate: Decimal | None = None
    straight_through_rate: Decimal | None = None
    exceptions: list[ExceptionCount] = Field(default_factory=list)
    data_note: str = "Synthetic portfolio data. Amounts are submitted or recorded invoice values, not payments."


SubmissionCreated.model_rebuild()


class RecoveredJobOut(ViewModel):
    job_id: UUID
    submission_id: UUID
    reason: str = Field(
        description="retry_pending, lease_expired, or queued_too_long."
    )
    attempt_before: int
    state: str | None = None
    already_settled: bool = False
    error: str | None = None
    job_status: str | None = None


class RecoveryReport(ApiModel):
    """What a recovery sweep found and what it did about each item."""

    examined: int
    recovered: int = Field(description="Jobs that reached a business outcome.")
    already_settled: int = Field(
        description="Jobs whose submission had been settled before the sweep reached it."
    )
    still_failing: int
    jobs: list[RecoveredJobOut] = Field(default_factory=list)


class NotificationOut(ViewModel):
    notification_id: UUID
    notification_type: str
    dedupe_key: str
    status: str
    channel: str
    invoice_id: UUID | None = None
    submission_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    attempt_count: int = 0
    last_error: str | None = None
    created_at: datetime
    sent_at: datetime | None = None


class NotificationList(ApiModel):
    count: int
    notifications: list[NotificationOut]
    delivery_note: str = (
        "This service delivers nothing. A status of 'sent' records what a "
        "caller reported after delivering the notification itself."
    )


class CorrectionRequest(ApiModel):
    """A reviewer submitting corrected values.

    The corrected values use the same shape extraction produces, so they run
    through the same normalisation and the same rules. A reviewer cannot
    produce an invoice that was never checked.
    """

    expected_revision_number: int = Field(
        ge=1,
        description=(
            "The revision the reviewer was looking at. The edit is refused if "
            "the invoice has moved on since, rather than overwriting whatever "
            "changed in between."
        ),
    )
    reason: str = Field(
        min_length=1,
        description="Why the correction was made. Required, and recorded.",
    )
    invoice: dict[str, Any] = Field(
        description="Corrected values, in the extraction payload shape."
    )


class ReviewActionRequest(ApiModel):
    action: str = Field(description="reject or accept_exception.")
    expected_revision_number: int = Field(ge=1)
    reason: str = Field(min_length=1)


class ReviewActionSummary(ViewModel):
    review_action_id: UUID
    actor: str
    action: str
    reason: str
    created_at: datetime
    from_revision_number: int | None = None
    to_revision_number: int | None = None


class ReviewOutcomeOut(ApiModel):
    invoice_id: UUID
    action: str
    actor: str
    reason: str
    state: str
    revision_number: int | None = None
    failed_codes: list[str] = Field(default_factory=list)
    review_action_id: UUID | None = None
    note: str | None = Field(
        default=None,
        description=(
            "Present when the outcome needs qualifying, for instance that an "
            "accepted exception is not the same as the checks passing."
        ),
    )


InvoiceDetail.model_rebuild()
