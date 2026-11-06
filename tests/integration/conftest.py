"""Fixtures for the database-backed tests.

These tests need a live PostgreSQL server. When none is reachable they are
skipped with a message rather than failing, so the unit suite still runs on a
machine without a database. See docs/setup.md.

The test database is created once per session from the configured
``INVAUT_TEST_DB_NAME``, migrated, and truncated between tests. It is never
the development database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import psycopg
import pytest

from invoice_automation.config import PROJECT_ROOT, Settings
from invoice_automation.db import apply_all, connect, create_database
from invoice_automation.db import repository as repo
from invoice_automation.models import normalize_identifier

REFERENCE_DIR = PROJECT_ROOT / "data" / "fixtures" / "reference"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures" / "structured"
MANIFEST = PROJECT_ROOT / "data" / "ground_truth" / "structured_cases.json"

_TRANSACTIONAL_TABLES = (
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


def _server_reachable(settings: Settings) -> tuple[bool, str]:
    try:
        with psycopg.connect(settings.dsn("postgres"), connect_timeout=3):
            return True, ""
    except Exception as exc:  # noqa: BLE001 - any failure means "not available"
        return False, str(exc).strip().splitlines()[0] if str(exc) else repr(exc)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def test_database(settings: Settings) -> str:
    reachable, reason = _server_reachable(settings)
    if not reachable:
        pytest.skip(
            "no PostgreSQL server reachable at "
            f"{settings.db_host}:{settings.db_port} ({reason}). "
            "See docs/setup.md."
        )
    create_database(settings, settings.test_db_name)
    with connect(settings, database=settings.test_db_name) as conn:
        apply_all(conn)
    return settings.test_db_name


@pytest.fixture
def conn(settings: Settings, test_database: str) -> Iterator[psycopg.Connection]:
    with connect(settings, database=test_database) as connection:
        connection.execute(
            "TRUNCATE " + ", ".join(_TRANSACTIONAL_TABLES) + " RESTART IDENTITY CASCADE"
        )
        connection.commit()
        yield connection


@pytest.fixture
def seeded(conn: psycopg.Connection) -> psycopg.Connection:
    """Reference data loaded from the same files the CLI uses."""
    suppliers = json.loads((REFERENCE_DIR / "suppliers.json").read_text("utf-8"))
    orders = json.loads((REFERENCE_DIR / "purchase_orders.json").read_text("utf-8"))

    supplier_ids = {}
    for supplier in suppliers["suppliers"]:
        supplier_ids[supplier["reference_normalized"]] = repo.upsert_supplier(
            conn,
            name=supplier["name"],
            reference=supplier["reference"],
            reference_normalized=supplier["reference_normalized"],
            permitted_currency=supplier["permitted_currency"],
            is_active=supplier.get("is_active", True),
        )
    for order in orders["purchase_orders"]:
        repo.upsert_purchase_order(
            conn,
            po_number=order["po_number"],
            po_number_normalized=order["po_number_normalized"],
            supplier_id=supplier_ids[normalize_identifier(order["supplier_reference"])],
            currency=order["currency"],
            expected_amount=order["expected_amount"],
            state=order.get("state", "open"),
        )
    conn.commit()
    return conn


@pytest.fixture(scope="session")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text("utf-8"))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR
