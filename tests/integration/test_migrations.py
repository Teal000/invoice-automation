"""Migration runner behaviour."""

from __future__ import annotations

import pytest

from invoice_automation.db import apply_all, discover
from invoice_automation.db.migrations import MigrationError

pytestmark = pytest.mark.integration


def test_applying_twice_is_a_no_op(conn):
    # The session fixture already applied them.
    assert apply_all(conn) == []


def test_every_migration_is_recorded_with_its_checksum(conn):
    recorded = {
        row["version"]: row["checksum"]
        for row in conn.execute("SELECT version, checksum FROM schema_migrations")
    }
    for migration in discover():
        assert recorded[migration.version] == migration.checksum


def test_editing_an_applied_migration_is_refused(conn, tmp_path):
    """A changed file must not be silently re-applied or skipped."""
    version = discover()[0].version
    altered = tmp_path / f"{version}.sql"
    altered.write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError):
        apply_all(conn, tmp_path)


def test_the_reporting_views_exist(conn):
    views = {
        row["table_name"]
        for row in conn.execute(
            """
            SELECT table_name FROM information_schema.views
            WHERE table_schema = 'public'
            """
        )
    }
    assert {
        "v_invoice_register",
        "v_rule_failures",
        "v_exception_summary",
        "v_processing_overview",
    } <= views
