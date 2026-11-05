"""Command line interface.

The visible end of the vertical slice. Every command here is also the fastest
way to demonstrate the system before the API and the review interface exist:

    init-db           create the database if absent and apply migrations
    seed              load the synthetic supplier and purchase-order registry
    ingest            process one structured payload
    ingest-manifest   process the labelled case set in order and check it
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
from .models import RawInvoice, SubmissionInput, normalize_identifier
from .pipeline import process_structured_submission

app = typer.Typer(
    add_completion=False,
    help="Invoice processing and exception reporting (synthetic portfolio project).",
)
console = Console()

DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "ground_truth" / "structured_cases.json"
DEFAULT_FIXTURES = PROJECT_ROOT / "data" / "fixtures" / "structured"
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
        storage_path=str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
    )
    return invoice, submission


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

    with connect() as conn:
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
        conn.commit()
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


@app.command("ingest-manifest")
def ingest_manifest(
    manifest: Path = typer.Option(DEFAULT_MANIFEST, help="Labelled case manifest."),
    fixtures: Path = typer.Option(DEFAULT_FIXTURES, help="Fixture directory."),
) -> None:
    """Process the labelled cases in order and compare with their ground truth.

    This is the acceptance check from the plan: every deliberately introduced
    failure must produce its expected exception code. The command exits
    non-zero if any case does not match, so it can be used in a pipeline.
    """
    data = json.loads(manifest.read_text("utf-8"))
    table = Table(title=f"{data['dataset']} (synthetic)")
    for column in ("case", "state", "expected", "failed codes", "match"):
        table.add_column(column)

    mismatches = 0
    with connect() as conn:
        for case in data["cases"]:
            invoice, submission = _load_payload(fixtures / case["file"])
            result = process_structured_submission(conn, invoice, submission)

            expected_codes = tuple(case["expected_failed_codes"])
            ok = (
                str(result.state) == case["expected_state"]
                and result.failed_codes == expected_codes
            )
            if not ok:
                mismatches += 1
            table.add_row(
                case["case_id"],
                str(result.state),
                case["expected_state"],
                ", ".join(result.failed_codes) or "-",
                "[green]yes[/]" if ok else "[red]no[/]",
            )

    console.print(table)
    if mismatches:
        console.print(f"[red]{mismatches} case(s) did not match the ground truth[/]")
        raise typer.Exit(code=1)
    console.print(f"[green]all {len(data['cases'])} cases matched the ground truth[/]")


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

    checks = Table(title="Checks")
    for column in ("code", "status", "field", "expected", "observed"):
        checks.add_column(column)
    for check in detail["checks"]:
        colour = {"fail": "red", "pass": "green", "skipped": "yellow"}[check["status"]]
        checks.add_row(
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
