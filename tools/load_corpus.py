"""Load the evaluation corpus into a database, for reporting with real volume.

    .venv/Scripts/python.exe tools/load_corpus.py
    .venv/Scripts/python.exe tools/load_corpus.py --limit 50 --reset

A dashboard built over three invoices tells you nothing about whether it
reads well. This pushes the 200-document corpus through the same path a real
arrival takes — stored under its hash, recorded as a submission, extracted,
validated, persisted — so the reporting views have something to show.

Documents are loaded in manifest order, which matters: the corpus contains
byte-identical resends and near-duplicates, and they only behave as
duplicates if the original arrives first.

This is a loader for demonstration data. It is not part of the benchmark, and
nothing here is measured: `evaluation/run_benchmark.py` is where figures come
from.

`--spread-days` invents arrival timestamps across a past window so the daily
chart has more than one bar. They are made up, exactly as the documents are,
and the dashboard says so on the page rather than implying a history that
happened.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from invoice_automation.config import Settings  # noqa: E402
from invoice_automation.db import connect  # noqa: E402
from invoice_automation.db import repository as repo  # noqa: E402
from invoice_automation.models import normalize_identifier  # noqa: E402
from invoice_automation.pipeline import (  # noqa: E402
    ProcessingError,
    process_submission,
    record_arrival,
)

CORPUS = PROJECT_ROOT / "data" / "corpus"
MANIFEST = PROJECT_ROOT / "data" / "manifests" / "corpus_splits.json"

TRANSACTIONAL_TABLES = (
    "notification_outbox",
    "review_actions",
    "validation_results",
    "invoice_lines",
    "processing_jobs",
    "audit_events",
    "invoice_revisions",
    "submissions",
    "invoices",
    "documents",
)


def seed_reference(conn) -> tuple[int, int]:
    """Load the corpus suppliers and purchase orders."""
    suppliers = json.loads((CORPUS / "reference" / "suppliers.json").read_text("utf-8"))
    orders = json.loads(
        (CORPUS / "reference" / "purchase_orders.json").read_text("utf-8")
    )
    ids = {}
    with conn.transaction():
        for entry in suppliers["suppliers"]:
            ids[entry["reference_normalized"]] = repo.upsert_supplier(
                conn,
                name=entry["name"],
                reference=entry["reference"],
                reference_normalized=entry["reference_normalized"],
                permitted_currency=entry["permitted_currency"],
                is_active=entry.get("is_active", True),
            )
        for entry in orders["purchase_orders"]:
            repo.upsert_purchase_order(
                conn,
                po_number=entry["po_number"],
                po_number_normalized=entry["po_number_normalized"],
                supplier_id=ids[normalize_identifier(entry["supplier_reference"])],
                currency=entry["currency"],
                expected_amount=entry["expected_amount"],
                state=entry.get("state", "open"),
            )
    return len(suppliers["suppliers"]), len(orders["purchase_orders"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Load at most this many.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Empty the transactional tables first, keeping reference data.",
    )
    parser.add_argument("--split", default=None, help="Only load one split.")
    parser.add_argument(
        "--spread-days",
        type=int,
        default=0,
        help=(
            "Spread arrival timestamps evenly over this many past days instead "
            "of recording them all at once. The timestamps are invented, like "
            "the documents: it exists so the daily chart has more than one bar, "
            "and the dashboard guide says so on the page."
        ),
    )
    args = parser.parse_args()

    settings = Settings()
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    cases = [c for c in manifest["cases"] if not args.split or c["split"] == args.split]
    if args.limit:
        cases = cases[: args.limit]

    print(f"database: {settings.db_name} on {settings.db_host}:{settings.db_port}")

    with connect(settings) as conn:
        if args.reset:
            conn.execute(
                "TRUNCATE " + ", ".join(TRANSACTIONAL_TABLES) + " RESTART IDENTITY CASCADE"
            )
            print("transactional tables emptied")

        suppliers, orders = seed_reference(conn)
        print(f"reference data: {suppliers} suppliers, {orders} purchase orders")

        outcomes: dict[str, int] = {}
        failures = 0
        start = datetime.now(tz=timezone.utc) - timedelta(days=args.spread_days)
        for index, case in enumerate(cases, start=1):
            path = CORPUS / "pdf" / case["pdf"]
            received_at = None
            if args.spread_days:
                # Deterministic, ordered, and invented. Duplicate families stay
                # in order because the offset grows with the index.
                received_at = start + timedelta(
                    seconds=(index / max(len(cases), 1)) * args.spread_days * 86400
                )
            arrival = record_arrival(
                conn,
                content=path.read_bytes(),
                original_file_name=path.name,
                source="cli",
                actor="corpus-loader",
                received_at=received_at,
                settings=settings,
            )
            try:
                result = process_submission(
                    conn, arrival.submission_id, actor="corpus-loader", settings=settings
                )
                outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1
            except ProcessingError as exc:
                failures += 1
                print(f"  {case['case_id']}: {exc}")
            if index % 50 == 0:
                print(f"  {index}/{len(cases)}")

    print(f"\nloaded {len(cases)} documents")
    for outcome, count in sorted(outcomes.items()):
        print(f"  {outcome:14} {count}")
    if failures:
        print(f"  technical failures: {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
