"""Command line interface.

The visible end of the vertical slice. Every command here is also the fastest
way to demonstrate the system before the API and the review interface exist:

    init-db           create the database if absent and apply migrations
    seed              load the synthetic supplier and purchase-order registry
    ingest            process one structured payload
    ingest-pdf        extract one PDF, then process it
    ingest-manifest   process the labelled structured set and check it
    ingest-pdf-manifest  extract and process the labelled PDF set and check it
    register          print the invoice register
    overview          print the processing overview with its denominators
    exceptions        print failure counts per rule code
    show              print one invoice with its checks, revisions and events
    reset             empty the transactional tables (keeps reference data)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from .config import PROJECT_ROOT, get_settings
from .db import apply_all, connect, create_database, repository as repo
from .extraction import ExtractionResult, extract_invoice
from .models import RawInvoice, SubmissionInput, normalize_identifier
from .pipeline import process_structured_submission

app = typer.Typer(
    add_completion=False,
    help="Invoice processing and exception reporting (synthetic portfolio project).",
)
console = Console()

DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "ground_truth" / "structured_cases.json"
DEFAULT_FIXTURES = PROJECT_ROOT / "data" / "fixtures" / "structured"
DEFAULT_PDF_MANIFEST = PROJECT_ROOT / "data" / "ground_truth" / "pdf_cases.json"
DEFAULT_PDF_DIR = PROJECT_ROOT / "data" / "fixtures" / "pdf"
DEFAULT_REFERENCE = PROJECT_ROOT / "data" / "fixtures" / "reference"

#: Tables emptied by `reset`, in dependency order. Reference data is kept.
_TRANSACTIONAL_TABLES = (
    "validation_results",
    "invoice_lines",
    "processing_jobs",
    "audit_events",
    "invoice_revisions",
    "submissions",
    "invoices",
    "documents",
)


def _load_payload(path: Path) -> tuple[RawInvoice, SubmissionInput]:
    """Read a structured payload and describe its arrival.

    The hash is taken over the file bytes, exactly as it would be for a PDF,
    so the duplicate check behaves the same way once documents replace these
    payloads.
    """
    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes)
    invoice = RawInvoice.model_validate(payload)
    submission = SubmissionInput(
        source="cli",
        original_file_name=path.name,
        file_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        storage_path=_storage_path(path),
    )
    return invoice, submission


def _load_pdf(path: Path) -> tuple[ExtractionResult, RawInvoice, SubmissionInput]:
    """Extract a PDF and describe its arrival.

    The hash is taken over the file bytes, exactly as for a structured
    payload, so the duplicate checks behave identically for both.
    """
    raw_bytes = path.read_bytes()
    extraction = extract_invoice(path)
    submission = SubmissionInput(
        source="cli",
        original_file_name=path.name,
        file_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        storage_path=_storage_path(path),
    )
    return extraction, extraction.raw, submission


def _process_pdf(conn, extraction: ExtractionResult, invoice, submission):
    """Hand an extracted document to the same pipeline the fixtures use."""
    return process_structured_submission(
        conn,
        invoice,
        submission,
        raw_text=extraction.raw_text,
        field_evidence=extraction.evidence_payload(),
        media_type="application/pdf",
        byte_size=(PROJECT_ROOT / submission.storage_path).stat().st_size
        if submission.storage_path and not Path(submission.storage_path).is_absolute()
        else None,
    )


def _storage_path(path: Path) -> str:
    """Record where the source file is, relative to the project when possible.

    A path outside the project is stored absolute rather than raising: the
    point of the column is that an extracted value can be traced back to its
    source, and a file someone ingested from elsewhere still needs to be
    findable.
    """
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(resolved).replace("\\", "/")


def _amount(value: Any) -> str:
    return "-" if value is None else format(value, "f")


@app.command("init-db")
def init_db(
    database: str = typer.Option(None, help="Override the configured database name."),
) -> None:
    """Create the database if it does not exist, then apply migrations."""
    settings = get_settings()
    name = database or settings.db_name
    created = create_database(settings, name)
    console.print(
        f"database [bold]{name}[/]: {'created' if created else 'already present'}"
    )
    with connect(settings, database=name) as conn:
        applied = apply_all(conn)
    if applied:
        console.print("applied migrations: " + ", ".join(applied))
    else:
        console.print("migrations: already up to date")


@app.command()
def seed(
    reference_dir: Path = typer.Option(DEFAULT_REFERENCE, help="Reference data directory."),
) -> None:
    """Load the synthetic supplier registry and purchase orders."""
    suppliers = json.loads((reference_dir / "suppliers.json").read_text("utf-8"))
    purchase_orders = json.loads(
        (reference_dir / "purchase_orders.json").read_text("utf-8")
    )

    # One transaction: a half-loaded registry would make every later run
    # behave differently depending on where the load stopped.
    with connect() as conn, conn.transaction():
        supplier_ids: dict[str, UUID] = {}
        for supplier in suppliers["suppliers"]:
            supplier_ids[supplier["reference_normalized"]] = repo.upsert_supplier(
                conn,
                name=supplier["name"],
                reference=supplier["reference"],
                reference_normalized=supplier["reference_normalized"],
                permitted_currency=supplier["permitted_currency"],
                is_active=supplier.get("is_active", True),
            )
        for order in purchase_orders["purchase_orders"]:
            reference = normalize_identifier(order["supplier_reference"])
            supplier_id = supplier_ids.get(reference)
            if supplier_id is None:
                raise typer.BadParameter(
                    f"purchase order {order['po_number']} references unknown supplier "
                    f"{order['supplier_reference']}"
                )
            repo.upsert_purchase_order(
                conn,
                po_number=order["po_number"],
                po_number_normalized=order["po_number_normalized"],
                supplier_id=supplier_id,
                currency=order["currency"],
                expected_amount=order["expected_amount"],
                state=order.get("state", "open"),
            )
    console.print(
        f"seeded {len(suppliers['suppliers'])} synthetic suppliers and "
        f"{len(purchase_orders['purchase_orders'])} purchase orders"
    )


@app.command()
def ingest(
    path: Path = typer.Argument(..., help="Structured payload (JSON)."),
    idempotency_key: str = typer.Option(
        None, help="Replaying the same key returns the earlier outcome."
    ),
) -> None:
    """Process one structured payload and print the outcome."""
    invoice, submission = _load_payload(path)
    if idempotency_key:
        submission = submission.model_copy(update={"idempotency_key": idempotency_key})

    with connect() as conn:
        result = process_structured_submission(conn, invoice, submission)

    _print_result(path, result)


def _print_result(path: Path, result) -> None:
    label = "replayed" if result.replayed else str(result.state)
    console.print(f"[bold]{path.name}[/] -> {label}")
    console.print(f"  submission {result.submission_id}")
    if result.invoice_id:
        console.print(
            f"  invoice    {result.invoice_id}"
            f" (revision {result.revision_number}, "
            f"{'new' if result.invoice_created else 'existing'})"
        )
    if result.duplicate_of_submission_id:
        console.print(f"  duplicate of submission {result.duplicate_of_submission_id}")
    if result.failed_codes:
        console.print("  failed checks: " + ", ".join(result.failed_codes))
        for check in result.results:
            if check.failed:
                console.print(
                    f"    - {check.code} [{check.field_name or '-'}] "
                    f"expected {check.expected!r}, observed {check.observed!r}"
                )
    elif not result.replayed:
        console.print("  all applicable checks passed")


@app.command("ingest-pdf")
def ingest_pdf(
    path: Path = typer.Argument(..., help="Invoice PDF."),
    idempotency_key: str = typer.Option(
        None, help="Replaying the same key returns the earlier outcome."
    ),
) -> None:
    """Extract one PDF, then process it exactly as a structured payload."""
    extraction, invoice, submission = _load_pdf(path)
    if idempotency_key:
        submission = submission.model_copy(update={"idempotency_key": idempotency_key})

    with connect() as conn:
        result = _process_pdf(conn, extraction, invoice, submission)

    console.print(
        f"  layout     {extraction.layout or '[red]not recognised[/]'}"
        f"  ({len(extraction.evidence)} fields with evidence)"
    )
    _print_result(path, result)


def _run_manifest(
    *,
    manifest: Path,
    source_dir: Path,
    file_key: str,
    loader,
) -> None:
    """Process a labelled manifest in order and compare with ground truth.

    This is the acceptance check from the plan: every deliberately introduced
    failure must produce its expected exception code. Exits non-zero on any
    mismatch, so it can be used in a pipeline.
    """
    data = json.loads(manifest.read_text("utf-8"))
    table = Table(title=f"{data['dataset']} (synthetic)")
    columns = ["case", "state", "expected", "failed codes", "match"]
    show_layout = any("layout" in case for case in data["cases"])
    if show_layout:
        columns.insert(1, "layout")
    for column in columns:
        table.add_column(column)

    mismatches = 0
    with connect() as conn:
        for case in data["cases"]:
            result, extra = loader(conn, source_dir / case[file_key])

            expected_codes = tuple(case["expected_failed_codes"])
            ok = (
                str(result.state) == case["expected_state"]
                and result.failed_codes == expected_codes
            )
            if not ok:
                mismatches += 1
            row = [
                case["case_id"],
                str(result.state),
                case["expected_state"],
                ", ".join(result.failed_codes) or "-",
                "[green]yes[/]" if ok else "[red]no[/]",
            ]
            if show_layout:
                detected = extra or "-"
                expected_layout = case.get("layout")
                row.insert(
                    1,
                    detected
                    if detected == expected_layout
                    else f"[red]{detected}[/]",
                )
            table.add_row(*row)

    console.print(table)
    if mismatches:
        console.print(f"[red]{mismatches} case(s) did not match the ground truth[/]")
        raise typer.Exit(code=1)
    console.print(f"[green]all {len(data['cases'])} cases matched the ground truth[/]")


@app.command("ingest-manifest")
def ingest_manifest(
    manifest: Path = typer.Option(DEFAULT_MANIFEST, help="Labelled case manifest."),
    fixtures: Path = typer.Option(DEFAULT_FIXTURES, help="Fixture directory."),
) -> None:
    """Process the labelled structured cases and check them against ground truth."""

    def load(conn, path: Path):
        invoice, submission = _load_payload(path)
        return process_structured_submission(conn, invoice, submission), None

    _run_manifest(
        manifest=manifest, source_dir=fixtures, file_key="file", loader=load
    )


@app.command("ingest-pdf-manifest")
def ingest_pdf_manifest(
    manifest: Path = typer.Option(DEFAULT_PDF_MANIFEST, help="Labelled PDF manifest."),
    pdfs: Path = typer.Option(DEFAULT_PDF_DIR, help="PDF directory."),
) -> None:
    """Extract and process every labelled PDF, checking state and layout."""

    def load(conn, path: Path):
        extraction, invoice, submission = _load_pdf(path)
        result = _process_pdf(conn, extraction, invoice, submission)
        return result, extraction.layout

    _run_manifest(manifest=manifest, source_dir=pdfs, file_key="pdf", loader=load)


@app.command()
def register(
    state: str = typer.Option(None, help="Filter by business state."),
    limit: int = typer.Option(50),
) -> None:
    """Print the invoice register."""
    with connect() as conn:
        rows = repo.get_invoice_register(conn, state=state, limit=limit)

    table = Table(title="Invoice register (synthetic portfolio data)")
    for column in (
        "state", "supplier", "invoice no", "date", "total", "PO", "rev", "failed codes"
    ):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row["business_state"],
            (row["supplier_name"] or row["supplier_reference"] or "-")[:34],
            row["invoice_number"],
            row["invoice_date"].isoformat() if row["invoice_date"] else "-",
            _amount(row["total"]),
            row["purchase_order_number"] or "-",
            str(row["current_revision_number"]),
            str(row["failed_rule_codes"]),
        )
    console.print(table)


@app.command()
def overview() -> None:
    """Print the processing overview, with each denominator stated."""
    with connect() as conn:
        row = repo.get_processing_overview(conn)

    table = Table(title="Processing overview (synthetic portfolio data)")
    table.add_column("measure")
    table.add_column("value", justify="right")
    for key in (
        "submissions_received",
        "duplicate_arrivals",
        "failed_submissions",
        "pending_submissions",
        "unique_invoices",
        "eligible_invoices",
        "validated_invoices",
        "needs_review_invoices",
        "unsupported_invoices",
        "rejected_invoices",
        "validated_without_edit",
    ):
        table.add_row(key.replace("_", " "), str(row[key]))
    for key in ("review_rate", "straight_through_rate"):
        value = row[key]
        table.add_row(key.replace("_", " "), "-" if value is None else f"{value:.4f}")
    console.print(table)
    console.print(
        "review rate and straight-through rate are shares of eligible invoices "
        "(duplicate and unsupported invoices excluded); duplicate arrivals are "
        "counted separately and never as processed invoices."
    )


@app.command()
def exceptions() -> None:
    """Print failure counts per rule code."""
    with connect() as conn:
        rows = repo.get_exception_summary(conn)

    table = Table(title="Exceptions by rule code (synthetic portfolio data)")
    table.add_column("code")
    table.add_column("invoices affected", justify="right")
    table.add_column("failed checks", justify="right")
    for row in rows:
        table.add_row(row["code"], str(row["invoices_affected"]), str(row["failed_checks"]))
    console.print(table)


@app.command()
def show(invoice_id: str = typer.Argument(..., help="Invoice UUID.")) -> None:
    """Print one invoice with its checks, revisions and event history."""
    with connect() as conn:
        detail = repo.get_invoice_detail(conn, UUID(invoice_id))
    if detail is None:
        console.print("[red]no such invoice[/]")
        raise typer.Exit(code=1)

    console.print(
        f"[bold]{detail['invoice_number']}[/] "
        f"({detail['supplier_name'] or detail['supplier_reference']}) "
        f"state={detail['business_state']} total={_amount(detail['total'])} "
        f"{(detail['currency'] or '').strip()}"
    )

    lines = Table(title="Lines")
    for column in ("no", "description", "quantity", "unit price", "line amount"):
        lines.add_column(column)
    for line in detail["lines"]:
        lines.add_row(
            str(line["line_number"]),
            line["description"] or "-",
            _amount(line["quantity"]),
            _amount(line["unit_price"]),
            _amount(line["line_amount"]),
        )
    console.print(lines)

    checks = Table(title="Checks (current revision marked *)")
    for column in ("rev", "code", "status", "field", "expected", "observed"):
        checks.add_column(column)
    for check in detail["checks"]:
        colour = {"fail": "red", "pass": "green", "skipped": "yellow"}[check["status"]]
        if check["submission_level"]:
            revision = "arrival"
        else:
            revision = str(check["revision_number"])
            if check["is_current"]:
                revision += " *"
        checks.add_row(
            revision,
            check["code"],
            f"[{colour}]{check['status']}[/]",
            check["field_name"] or "-",
            check["expected_value"] or "-",
            check["observed_value"] or "-",
        )
    console.print(checks)

    revisions = Table(title="Revisions")
    for column in ("no", "source", "by", "at"):
        revisions.add_column(column)
    for revision in detail["revisions"]:
        revisions.add_row(
            str(revision["revision_number"]),
            revision["source"],
            revision["created_by"],
            revision["created_at"].isoformat(timespec="seconds"),
        )
    console.print(revisions)

    events = Table(title="Events")
    for column in ("at", "type", "entity", "actor"):
        events.add_column(column)
    for event in detail["events"]:
        events.add_row(
            event["occurred_at"].isoformat(timespec="seconds"),
            event["event_type"],
            event["entity_type"],
            event["actor"],
        )
    console.print(events)


@app.command()
def reset(
    yes: bool = typer.Option(False, "--yes", help="Confirm emptying the tables."),
) -> None:
    """Empty the transactional tables, keeping suppliers and purchase orders."""
    if not yes:
        console.print("refusing to empty the tables without --yes")
        raise typer.Exit(code=1)
    with connect() as conn:
        # TRUNCATE rather than DELETE: audit_events refuses row deletion by
        # design, and this is a development convenience, not a business action.
        conn.execute(
            "TRUNCATE " + ", ".join(_TRANSACTIONAL_TABLES) + " RESTART IDENTITY CASCADE"
        )
        conn.commit()
    console.print("transactional tables emptied; reference data kept")


if __name__ == "__main__":
    app()
