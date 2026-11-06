"""Create the read-only role the dashboard connects as.

    .venv/Scripts/python.exe tools/create_reporting_role.py

The plan asks for a read-only reporting connection. This makes one that is
read-only by construction rather than by intention: the role is granted
`SELECT` on the reporting **views only**, and on no table at all. A report
cannot then read a column nobody decided to publish, and cannot write
anything whatever a mis-click in a reporting tool asks for.

The password is read from `INVAUT_REPORTING_PASSWORD` in `.env`, which is
git-ignored. One is generated and written there on first run.
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

from invoice_automation.config import Settings  # noqa: E402
from invoice_automation.db import connect  # noqa: E402

ENV_FILE = PROJECT_ROOT / ".env"
ROLE = "invoice_reporting"

#: Exactly what the dashboard may read. Tables are deliberately absent.
REPORTING_VIEWS = (
    "v_invoice_register",
    "v_rule_failures",
    "v_exception_summary",
    "v_processing_overview",
    "v_daily_processing",
    "v_review_queue",
)


def ensure_password() -> str:
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    match = re.search(r"^INVAUT_REPORTING_PASSWORD=(.*)$", text, re.MULTILINE)
    if match and match.group(1).strip():
        return match.group(1).strip()
    password = secrets.token_urlsafe(24)
    text = text.rstrip("\n") + (
        "\n\n# Read-only role used by the dashboard. Generated; git-ignored.\n"
        f"INVAUT_REPORTING_PASSWORD={password}\n"
    )
    ENV_FILE.write_text(text, encoding="utf-8", newline="\n")
    print("generated INVAUT_REPORTING_PASSWORD in .env")
    return password


def main() -> int:
    settings = Settings()
    password = ensure_password()

    with connect(settings) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,)
        ).fetchone()
        role = sql.Identifier(ROLE)
        statement = sql.SQL(
            "ALTER ROLE {} WITH LOGIN PASSWORD {}"
            if exists
            else "CREATE ROLE {} WITH LOGIN PASSWORD {}"
        ).format(role, sql.Literal(password))
        conn.execute(statement)
        print(f"role {ROLE}: {'updated' if exists else 'created'}")

        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(settings.db_name), role
            )
        )
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))

        # Revoke first, so a view removed from the list above actually loses
        # its grant rather than lingering from an earlier run.
        conn.execute(
            sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role)
        )
        for view in REPORTING_VIEWS:
            conn.execute(
                sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(view), role)
            )
        print(f"granted SELECT on {len(REPORTING_VIEWS)} views, and on nothing else")

    # Prove it rather than assert it.
    reporting = Settings(
        db_user=ROLE,
        db_password=password,
        db_host=settings.db_host,
        db_port=settings.db_port,
        db_name=settings.db_name,
    )
    with psycopg.connect(reporting.dsn()) as check:
        count = check.execute("SELECT count(*) FROM v_invoice_register").fetchone()[0]
        print(f"reading as {ROLE}: v_invoice_register has {count} rows")
        for forbidden, what in (
            ("SELECT count(*) FROM invoices", "reading a table"),
            ("UPDATE invoices SET business_state = 'validated'", "writing"),
        ):
            try:
                check.execute(forbidden)
            except psycopg.errors.InsufficientPrivilege:
                print(f"  {what}: refused, as intended")
            else:  # pragma: no cover - would mean the grants are wrong
                print(f"  {what}: ALLOWED - the grants are wrong")
                return 1
            check.rollback()

    print(
        "\nPower BI connection:\n"
        f"  server   {settings.db_host}:{settings.db_port}\n"
        f"  database {settings.db_name}\n"
        f"  user     {ROLE}\n"
        "  password INVAUT_REPORTING_PASSWORD in .env"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
