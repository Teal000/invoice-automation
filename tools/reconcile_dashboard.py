"""Check the reporting views against direct SQL, and print what a dashboard must show.

    .venv/Scripts/python.exe tools/reconcile_dashboard.py

Two jobs.

First, it recomputes every headline figure **from the base tables**, without
touching the views, and compares. A view and a hand-written query agreeing is
weak evidence on its own — but these are written differently on purpose, so
a mistake in either shows up as a disagreement rather than as a plausible
number.

Second, it prints the figures a correctly built dashboard must display. Open
it beside the report and compare. The plan's requirement is that dashboard
aggregates reconcile with direct SQL calculations; this is how that is
checked rather than asserted.

Exits non-zero if anything disagrees.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from invoice_automation.config import Settings  # noqa: E402
from invoice_automation.db import connect  # noqa: E402

#: (label, query over the views, query over the base tables)
CHECKS: tuple[tuple[str, str, str], ...] = (
    (
        "Submissions received",
        "SELECT submissions_received FROM v_processing_overview",
        "SELECT count(*) FROM submissions",
    ),
    (
        "Duplicate arrivals",
        "SELECT duplicate_arrivals FROM v_processing_overview",
        "SELECT count(*) FROM submissions WHERE outcome = 'duplicate'",
    ),
    (
        "Unique invoices",
        "SELECT unique_invoices FROM v_processing_overview",
        "SELECT count(*) FROM invoices",
    ),
    (
        "Validated invoices",
        "SELECT validated_invoices FROM v_processing_overview",
        "SELECT count(*) FROM invoices WHERE business_state = 'validated'",
    ),
    (
        "Invoices needing review",
        "SELECT needs_review_invoices FROM v_processing_overview",
        "SELECT count(*) FROM invoices WHERE business_state = 'needs_review'",
    ),
    (
        "Unsupported invoices",
        "SELECT unsupported_invoices FROM v_processing_overview",
        "SELECT count(*) FROM invoices WHERE business_state = 'unsupported'",
    ),
    (
        "Eligible invoices (rate denominator)",
        "SELECT eligible_invoices FROM v_processing_overview",
        "SELECT count(*) FROM invoices "
        "WHERE business_state NOT IN ('duplicate', 'unsupported')",
    ),
    (
        "Validated with no reviewer involvement",
        "SELECT validated_without_edit FROM v_processing_overview",
        """
        SELECT count(*) FROM invoices i
        WHERE i.business_state = 'validated'
          AND NOT EXISTS (SELECT 1 FROM invoice_revisions r
                          WHERE r.invoice_id = i.invoice_id
                            AND r.source = 'reviewer_correction')
          AND NOT EXISTS (SELECT 1 FROM review_actions ra
                          WHERE ra.invoice_id = i.invoice_id
                            AND ra.action = 'accept_exception')
        """,
    ),
    (
        "Review queue size",
        "SELECT count(*) FROM v_review_queue",
        "SELECT count(*) FROM invoices WHERE business_state = 'needs_review'",
    ),
    (
        "Rows in the register",
        "SELECT count(*) FROM v_invoice_register",
        "SELECT count(*) FROM invoices",
    ),
    (
        "Distinct failing rule codes",
        "SELECT count(*) FROM v_exception_summary",
        """
        SELECT count(DISTINCT v.code)
        FROM validation_results v
        JOIN invoices i ON i.invoice_id = v.invoice_id
                       AND v.revision_id = i.current_revision_id
        WHERE v.status = 'fail'
        """,
    ),
    (
        "Individual failed checks (current revisions)",
        "SELECT coalesce(sum(failed_checks), 0) FROM v_exception_summary",
        """
        SELECT count(*)
        FROM validation_results v
        JOIN invoices i ON i.invoice_id = v.invoice_id
                       AND v.revision_id = i.current_revision_id
        WHERE v.status = 'fail'
        """,
    ),
    (
        "Submissions in the daily view",
        "SELECT coalesce(sum(submissions), 0) FROM v_daily_processing",
        "SELECT count(*) FROM submissions",
    ),
)


def scalar(conn, query: str):
    row = conn.execute(query).fetchone()
    return list(row.values())[0]


def main() -> int:
    settings = Settings()
    print(f"database: {settings.db_name} on {settings.db_host}:{settings.db_port}\n")

    mismatches = 0
    width = max(len(label) for label, _, _ in CHECKS)
    with connect(settings) as conn:
        print(f"{'measure'.ljust(width)}  {'view':>8}  {'direct SQL':>10}  result")
        print("-" * (width + 32))
        for label, view_query, direct_query in CHECKS:
            from_view = scalar(conn, view_query)
            from_tables = scalar(conn, direct_query)
            agree = from_view == from_tables
            mismatches += 0 if agree else 1
            print(
                f"{label.ljust(width)}  {str(from_view):>8}  {str(from_tables):>10}  "
                f"{'ok' if agree else 'MISMATCH'}"
            )

        overview = conn.execute("SELECT * FROM v_processing_overview").fetchone()
        exceptions = conn.execute(
            "SELECT code, invoices_affected, failed_checks FROM v_exception_summary "
            "ORDER BY invoices_affected DESC, code"
        ).fetchall()
        days = conn.execute(
            "SELECT count(*) AS n, min(arrival_date) AS first, max(arrival_date) AS last "
            "FROM v_daily_processing"
        ).fetchone()
        bands = conn.execute(
            "SELECT age_band, count(*) AS n FROM v_review_queue GROUP BY 1 ORDER BY 1"
        ).fetchall()

    print("\nWhat the dashboard must show")
    print("-" * 36)
    print(f"  Processing overview page")
    print(f"    submissions received      {overview['submissions_received']}")
    print(f"    duplicate arrivals        {overview['duplicate_arrivals']}")
    print(f"    unique invoices           {overview['unique_invoices']}")
    print(f"    eligible invoices         {overview['eligible_invoices']}")
    print(f"    review rate               {overview['review_rate']}")
    print(f"    straight-through rate     {overview['straight_through_rate']}")
    print(f"    days with arrivals        {days['n']} ({days['first']} to {days['last']})")
    print(f"  Exception analysis page")
    for row in exceptions[:8]:
        print(
            f"    {row['code']:<24} {row['invoices_affected']:>3} invoices, "
            f"{row['failed_checks']:>3} checks"
        )
    if len(exceptions) > 8:
        print(f"    ... and {len(exceptions) - 8} more codes")
    print(f"  Review queue by age")
    for row in bands:
        print(f"    {row['age_band']:<14} {row['n']}")

    print(
        "\nTwo counts that must never be added together: 'invoices affected' and\n"
        "'failed checks'. Three bad line items are three checks on one invoice."
    )

    if mismatches:
        print(f"\n{mismatches} measure(s) disagree between the views and direct SQL")
        return 1
    print("\nevery measure agrees between the views and direct SQL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
