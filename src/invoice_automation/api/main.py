"""The HTTP service.

    .venv/Scripts/python.exe -m uvicorn invoice_automation.api.main:app --reload

Interactive documentation at ``/docs``.

It is a thin layer. Every endpoint either records an arrival, triggers
processing, or reads from the reporting views; none of them contain business
rules. That is the point: the same code path serves the CLI, the API and
later n8n, so there is one place where an invoice's fate is decided.

Authentication is a single internal service credential in the ``X-API-Key``
header, required everywhere except ``/health``. That is enough for a local
portfolio deployment and for n8n-to-API calls, and it is not a user
authentication system; the distinction is stated rather than implied.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Iterator
from uuid import UUID

import psycopg
from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import Settings, get_settings
from ..db import connect
from ..db import jobs as job_repo
from ..db import repository as repo
from ..pipeline import (
    ProcessingError,
    ProcessingResult,
    SubmissionNotFound,
    process_submission,
    record_arrival,
)
from ..models import RawInvoice
from ..recovery import recover_jobs
from ..review import (
    ACCEPT_EXCEPTION,
    REJECT,
    InvoiceNotFound,
    ReviewNotApplicable,
    ReviewOutcome,
    RevisionConflict,
    accept_exception,
    apply_correction,
    reject_invoice,
)
from ..storage import UnsupportedMediaType
from .schemas import (
    CorrectionRequest,
    ErrorBody,
    ErrorResponse,
    HealthResponse,
    InvoiceDetail,
    InvoiceList,
    NotificationList,
    NotificationOut,
    ProcessingOutcome,
    RecoveryReport,
    ReviewActionRequest,
    ReviewOutcomeOut,
    SubmissionCreated,
    SubmissionDetail,
    SummaryReport,
)

API_KEY_HEADER = "X-API-Key"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.api_key:
        raise RuntimeError(
            "INVAUT_API_KEY is not set. The service will not start without a "
            "credential: an API that runs open to anything that can reach the "
            "port is worse than one that refuses to start."
        )

    app = FastAPI(
        title="Invoice Processing and Exception Reporting",
        version=__version__,
        summary="Receives supplier invoices, validates them, records exceptions.",
        description=(
            "**All data served by this API is synthetic.** Suppliers, purchase "
            "orders and invoices are invented. 'Accepted' means recorded in a "
            "demonstration register; it does not mean approved for payment, and "
            "no payment is ever executed.\n\n"
            "Every endpoint except `/health` requires the `X-API-Key` header."
        ),
        responses={
            401: {"model": ErrorResponse, "description": "Missing or wrong credential"},
            404: {"model": ErrorResponse, "description": "No such record"},
        },
    )
    app.state.settings = settings

    _register_error_handlers(app)
    _register_routes(app)
    return app


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_db(request: Request) -> Iterator[psycopg.Connection]:
    """One connection per request.

    A connection pool would be the production choice; a connection per
    request is honest for a local service and keeps the failure mode obvious.
    """
    settings: Settings = request.app.state.settings
    with connect(settings) as conn:
        yield conn


def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    """Compare the credential in constant time."""
    import secrets

    expected: str = request.app.state.settings.api_key
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "unauthenticated",
                "message": f"A valid {API_KEY_HEADER} header is required.",
            },
        )


Db = Annotated[psycopg.Connection, Depends(get_db)]
Authenticated = Depends(require_api_key)


# ---------------------------------------------------------------------------
# Errors, in one shape
# ---------------------------------------------------------------------------


def _error(status_code: int, code: str, message: str, detail=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=ErrorBody(code=code, message=message, detail=detail)
        ).model_dump(mode="json"),
    )


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    def _http_exception(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return _error(
                exc.status_code,
                detail["code"],
                detail.get("message", ""),
                detail.get("detail"),
            )
        return _error(exc.status_code, "http_error", str(detail))

    @app.exception_handler(SubmissionNotFound)
    def _not_found(_: Request, exc: SubmissionNotFound) -> JSONResponse:
        return _error(
            status.HTTP_404_NOT_FOUND, "submission_not_found", str(exc)
        )

    @app.exception_handler(UnsupportedMediaType)
    def _unsupported_media(_: Request, exc: UnsupportedMediaType) -> JSONResponse:
        return _error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type", str(exc)
        )

    @app.exception_handler(InvoiceNotFound)
    def _invoice_missing(_: Request, exc: InvoiceNotFound) -> JSONResponse:
        return _error(status.HTTP_404_NOT_FOUND, "invoice_not_found", str(exc))

    @app.exception_handler(RevisionConflict)
    def _revision_conflict(_: Request, exc: RevisionConflict) -> JSONResponse:
        # 409, not 200: the reviewer is looking at values that are no longer
        # current, and applying the edit would discard what changed.
        return _error(
            status.HTTP_409_CONFLICT,
            "revision_conflict",
            str(exc),
            {
                "expected_revision_number": exc.expected,
                "current_revision_number": exc.actual,
            },
        )

    @app.exception_handler(ReviewNotApplicable)
    def _review_not_applicable(_: Request, exc: ReviewNotApplicable) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, "review_not_applicable", str(exc))

    @app.exception_handler(ProcessingError)
    def _processing_failed(_: Request, exc: ProcessingError) -> JSONResponse:
        # A technical failure, recorded against a retryable job. The caller is
        # told the job state so an orchestrator can decide what to do.
        return _error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "processing_failed",
            str(exc),
            {
                "submission_id": str(exc.submission_id),
                "job_status": exc.job_status,
                "retryable": exc.job_status == "retry_pending",
            },
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _outcome(result: ProcessingResult) -> ProcessingOutcome:
    return ProcessingOutcome(
        submission_id=result.submission_id,
        state=str(result.state),
        invoice_id=result.invoice_id,
        revision_number=result.revision_number,
        invoice_created=result.invoice_created,
        duplicate_of_submission_id=result.duplicate_of_submission_id,
        replayed=result.replayed,
        failed_codes=list(result.failed_codes),
        failed_checks=[
            {
                "code": check.code,
                "field_name": check.field_name,
                "expected": check.expected,
                "observed": check.observed,
                "message": check.message,
            }
            for check in result.results
            if check.failed
        ],
        job_attempt=result.job_attempt,
    )


def _actor(request: Request) -> str:
    """Who took the action, as far as the service can tell.

    There is one service credential and no users, so this cannot name a
    person. It records the caller, and the limitation is stated rather than
    dressed up: before reviewer actions are used in earnest, "who overrode
    this check" needs a real identity.
    """
    caller = request.headers.get("X-Actor")
    return f"api:{caller}" if caller else "api:service-credential"


def _review_outcome(outcome: ReviewOutcome) -> ReviewOutcomeOut:
    note = None
    if outcome.action == ACCEPT_EXCEPTION:
        note = (
            "Accepted into the register despite a failed check. This is not "
            "recorded as the checks having passed: the register marks it and "
            "the straight-through rate excludes it."
        )
    elif outcome.action == "correct" and outcome.failed_codes:
        note = "The correction did not resolve every check; the invoice is still in review."
    return ReviewOutcomeOut(
        invoice_id=outcome.invoice_id,
        action=outcome.action,
        actor=outcome.actor,
        reason=outcome.reason,
        state=str(outcome.state),
        revision_number=outcome.revision_number,
        failed_codes=list(outcome.failed_codes),
        review_action_id=outcome.review_action_id,
        note=note,
    )


def _register_routes(app: FastAPI) -> None:
    @app.get("/health", response_model=HealthResponse, tags=["service"])
    def health(request: Request) -> HealthResponse:
        """Liveness plus whether the database answers. No credential needed."""
        settings: Settings = request.app.state.settings
        try:
            with connect(settings) as conn:
                applied = conn.execute(
                    "SELECT count(*) AS n FROM schema_migrations"
                ).fetchone()["n"]
            return HealthResponse(
                status="ok",
                database="reachable",
                migrations_applied=applied,
                version=__version__,
            )
        except Exception as exc:  # noqa: BLE001 - reported, never raised here
            return HealthResponse(
                status="degraded",
                database=type(exc).__name__,
                migrations_applied=None,
                version=__version__,
            )

    @app.post(
        "/submissions",
        response_model=SubmissionCreated,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Authenticated],
        tags=["submissions"],
    )
    def create_submission(
        conn: Db,
        request: Request,
        response: Response,
        file: Annotated[UploadFile, File(description="Invoice PDF or structured JSON.")],
        idempotency_key: Annotated[
            str | None,
            Header(
                alias="Idempotency-Key",
                description="Replaying the same key returns the first submission and records nothing new.",
            ),
        ] = None,
        process: Annotated[
            bool,
            Query(
                description=(
                    "Process immediately instead of leaving the job queued. "
                    "Convenient for a demonstration; an orchestrator should "
                    "leave this false and call /process itself."
                )
            ),
        ] = False,
    ) -> SubmissionCreated:
        """Store a document and record its arrival.

        Returns a receipt whatever the document contains: an unreadable file
        is still recorded, and what it is gets decided during processing.
        """
        content = file.file.read()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "empty_file", "message": "The uploaded file is empty."},
            )

        arrival = record_arrival(
            conn,
            content=content,
            original_file_name=file.filename or "upload",
            source="api_upload",
            idempotency_key=idempotency_key,
            actor="api",
            settings=request.app.state.settings,
        )
        if arrival.replayed:
            response.status_code = status.HTTP_200_OK

        processed = None
        if process and not arrival.replayed:
            processed = _outcome(
                process_submission(
                    conn,
                    arrival.submission_id,
                    actor="api",
                    settings=request.app.state.settings,
                )
            )

        return SubmissionCreated(
            submission_id=arrival.submission_id,
            outcome=processed.state if processed else arrival.outcome,
            job_id=arrival.job_id,
            file_sha256=arrival.file_sha256,
            storage_path=arrival.storage_path,
            bytes_already_stored=arrival.bytes_already_stored,
            replayed=arrival.replayed,
            processed=processed,
        )

    @app.get(
        "/submissions/{submission_id}",
        response_model=SubmissionDetail,
        dependencies=[Authenticated],
        tags=["submissions"],
    )
    def read_submission(conn: Db, submission_id: UUID) -> SubmissionDetail:
        """Status of one arrival, its stored document, and its job."""
        row = repo.get_submission_summary(conn, submission_id)
        if row is None:
            raise SubmissionNotFound(str(submission_id))
        return SubmissionDetail(
            submission_id=row["submission_id"],
            source=row["source"],
            outcome=row["outcome"],
            received_at=row["received_at"],
            idempotency_key=row["idempotency_key"],
            invoice_id=row["invoice_id"],
            duplicate_of_submission_id=row["duplicate_of_submission_id"],
            notes=row["notes"],
            original_file_name=row["original_file_name"],
            media_type=row["media_type"],
            byte_size=row["byte_size"],
            file_sha256=row["content_sha256"],
            storage_path=row["storage_path"],
            extraction_method=row["extraction_method"],
            job={
                "job_id": row["job_id"],
                "status": row["job_status"],
                "attempt_count": row["attempt_count"],
                "max_attempts": row["max_attempts"],
                "last_error": row["last_error"],
            },
        )

    @app.post(
        "/submissions/{submission_id}/process",
        response_model=ProcessingOutcome,
        dependencies=[Authenticated],
        tags=["submissions"],
    )
    def process(conn: Db, request: Request, submission_id: UUID) -> ProcessingOutcome:
        """Extract, validate and record the outcome for an arrival.

        Safe to call repeatedly: a submission that already reached an outcome
        is reported, not processed again, so a repeated trigger cannot create
        a second invoice.
        """
        return _outcome(
            process_submission(
                conn,
                submission_id,
                actor="api",
                settings=request.app.state.settings,
            )
        )

    @app.post(
        "/jobs/{job_id}/retry",
        response_model=ProcessingOutcome,
        dependencies=[Authenticated],
        tags=["jobs"],
    )
    def retry_job(conn: Db, request: Request, job_id: UUID) -> ProcessingOutcome:
        """Retry a failed job without re-uploading the document.

        Retries the submission the job belongs to. If that submission has
        since reached an outcome, the recorded outcome is returned and nothing
        is processed again.
        """
        job = repo.get_job(conn, job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "job_not_found", "message": f"No job {job_id}."},
            )
        if job["status"] not in ("retry_pending", "failed", "queued"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "job_not_retryable",
                    "message": f"Job is {job['status']}; only a queued, retry_pending or failed job can be retried.",
                    "detail": {"job_status": job["status"]},
                },
            )
        return _outcome(
            process_submission(
                conn,
                job["submission_id"],
                actor="api:retry",
                settings=request.app.state.settings,
            )
        )

    @app.post(
        "/jobs/recover",
        response_model=RecoveryReport,
        dependencies=[Authenticated],
        tags=["jobs"],
    )
    def recover(
        conn: Db,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=200)] = 20,
    ) -> RecoveryReport:
        """Find and finish work that stalled.

        Picks up jobs that failed with attempts remaining, jobs whose lease
        expired while marked running, and jobs that were queued and never
        triggered. A job whose attempts are spent is left alone: that needs a
        person, not another automatic attempt.

        Intended to be called on a schedule by an orchestrator.
        """
        recovered = recover_jobs(
            conn, limit=limit, settings=request.app.state.settings, actor="api:recovery"
        )
        return RecoveryReport(
            examined=len(recovered),
            recovered=sum(
                1 for job in recovered if job.succeeded and not job.already_settled
            ),
            already_settled=sum(1 for job in recovered if job.already_settled),
            still_failing=sum(1 for job in recovered if not job.succeeded),
            jobs=[job.__dict__ for job in recovered],
        )

    @app.get(
        "/notifications",
        response_model=NotificationList,
        dependencies=[Authenticated],
        tags=["notifications"],
    )
    def list_notifications_endpoint(
        conn: Db,
        status_filter: Annotated[
            str | None, Query(alias="status", description="pending, sent, suppressed or failed.")
        ] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> NotificationList:
        """The outbox. Nothing here has been delivered by this service."""
        rows = job_repo.list_notifications(conn, status=status_filter, limit=limit)
        return NotificationList(count=len(rows), notifications=rows)

    @app.post(
        "/notifications/{notification_id}/sent",
        response_model=NotificationOut,
        dependencies=[Authenticated],
        tags=["notifications"],
    )
    def mark_sent(
        conn: Db,
        notification_id: UUID,
        channel: Annotated[
            str, Query(description="Where the caller delivered it, for the record.")
        ],
    ) -> NotificationOut:
        """Record that the caller delivered a pending notification.

        The service sends nothing itself, so this records a claim rather than
        an observation. Marking an already-sent notification is a conflict
        rather than a silent success, because that usually means two senders.
        """
        updated = job_repo.mark_notification_sent(
            conn, notification_id, channel=channel
        )
        if updated is None:
            existing = job_repo.list_notifications(conn, limit=500)
            known = {row["notification_id"] for row in existing}
            if notification_id not in known:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "notification_not_found",
                        "message": f"No notification {notification_id}.",
                    },
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "notification_not_pending",
                    "message": "Only a pending notification can be marked sent.",
                },
            )
        rows = job_repo.list_notifications(conn, limit=500)
        return next(
            row for row in rows if row["notification_id"] == notification_id
        )

    @app.get(
        "/invoices",
        response_model=InvoiceList,
        dependencies=[Authenticated],
        tags=["invoices"],
    )
    def list_invoices(
        conn: Db,
        state: Annotated[str | None, Query(description="Business state filter.")] = None,
        supplier_reference: Annotated[str | None, Query()] = None,
        exception_code: Annotated[
            str | None,
            Query(description="Only invoices failing this rule code on their current revision."),
        ] = None,
        invoice_date_from: Annotated[date | None, Query()] = None,
        invoice_date_to: Annotated[date | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> InvoiceList:
        """The invoice register, filtered. One row per canonical invoice."""
        rows = repo.list_invoices(
            conn,
            state=state,
            supplier_reference=supplier_reference,
            exception_code=exception_code,
            invoice_date_from=invoice_date_from,
            invoice_date_to=invoice_date_to,
            limit=limit,
            offset=offset,
        )
        return InvoiceList(
            count=len(rows), limit=limit, offset=offset, invoices=rows
        )

    @app.get(
        "/invoices/{invoice_id}",
        response_model=InvoiceDetail,
        dependencies=[Authenticated],
        tags=["invoices"],
    )
    def read_invoice(conn: Db, invoice_id: UUID) -> InvoiceDetail:
        """One invoice with its lines, every check, its revisions and events."""
        detail = repo.get_invoice_detail(conn, invoice_id)
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "invoice_not_found",
                    "message": f"No invoice {invoice_id}.",
                },
            )
        return InvoiceDetail(**detail)

    @app.post(
        "/invoices/{invoice_id}/revisions",
        response_model=ReviewOutcomeOut,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Authenticated],
        tags=["review"],
    )
    def create_revision(
        conn: Db,
        request: Request,
        invoice_id: UUID,
        body: CorrectionRequest,
    ) -> ReviewOutcomeOut:
        """Record corrected values as a new revision and re-run every check.

        The corrected values go through the same rules as extracted ones, so
        the outcome is whatever the checks now say: a correction that fixes
        the problem validates the invoice, one that does not leaves it in
        review with the remaining failures. Nothing is accepted because a
        person typed it.
        """
        raw = RawInvoice.model_validate(body.invoice)
        outcome = apply_correction(
            conn,
            invoice_id,
            expected_revision_number=body.expected_revision_number,
            raw=raw,
            actor=_actor(request),
            reason=body.reason,
            settings=request.app.state.settings,
        )
        return _review_outcome(outcome)

    @app.post(
        "/invoices/{invoice_id}/review-actions",
        response_model=ReviewOutcomeOut,
        dependencies=[Authenticated],
        tags=["review"],
    )
    def create_review_action(
        conn: Db, request: Request, invoice_id: UUID, body: ReviewActionRequest
    ) -> ReviewOutcomeOut:
        """Reject an invoice, or accept it despite a failed check.

        Both require a reason, which is stored. Accepting an exception puts
        the invoice in the register but is **not** recorded as the checks
        having passed: the register marks it and the straight-through rate
        excludes it.
        """
        if body.action not in (REJECT, ACCEPT_EXCEPTION):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "unknown_review_action",
                    "message": f"action must be {REJECT!r} or {ACCEPT_EXCEPTION!r}.",
                },
            )
        handler = reject_invoice if body.action == REJECT else accept_exception
        outcome = handler(
            conn,
            invoice_id,
            expected_revision_number=body.expected_revision_number,
            actor=_actor(request),
            reason=body.reason,
        )
        return _review_outcome(outcome)

    @app.get(
        "/reports/summary",
        response_model=SummaryReport,
        dependencies=[Authenticated],
        tags=["reports"],
    )
    def summary(conn: Db) -> SummaryReport:
        """Documented aggregates, read from the reporting views."""
        overview = repo.get_processing_overview(conn)
        exceptions = repo.get_exception_summary(conn)
        return SummaryReport(**overview, exceptions=exceptions)


app = create_app()
