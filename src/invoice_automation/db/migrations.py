"""Migration runner.

Plain SQL files in ``db/migrations``, applied in filename order, each in its
own transaction, each recorded in ``schema_migrations`` with the SHA-256 of
the file. If an already-applied file changes on disk, the runner refuses to
continue rather than leaving two databases silently different.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg

from ..config import PROJECT_ROOT

MIGRATIONS_DIR = PROJECT_ROOT / "db" / "migrations"

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(directory: Path | None = None) -> list[Migration]:
    directory = directory or MIGRATIONS_DIR
    files = sorted(directory.glob("*.sql"))
    return [
        Migration(version=path.stem, path=path, sql=path.read_text(encoding="utf-8"))
        for path in files
    ]


def applied_versions(conn: psycopg.Connection) -> dict[str, str]:
    conn.execute(_BOOTSTRAP)
    conn.commit()
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    # Leave the connection idle. On a connection that is not in autocommit
    # mode this read would otherwise hold an implicit transaction open, which
    # would turn the per-migration transaction block below into a savepoint
    # that commits nothing. See connect() for the full story.
    conn.rollback()
    return {row["version"]: row["checksum"] for row in rows}


def apply_all(
    conn: psycopg.Connection, directory: Path | None = None
) -> list[str]:
    """Apply pending migrations. Returns the versions applied in this run."""
    already = applied_versions(conn)
    newly_applied: list[str] = []

    for migration in discover(directory):
        recorded = already.get(migration.version)
        if recorded is not None:
            if recorded != migration.checksum:
                raise MigrationError(
                    f"{migration.version} was applied with a different checksum. "
                    "Add a new migration instead of editing an applied one."
                )
            continue

        with conn.transaction():
            conn.execute(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (migration.version, migration.checksum),
            )
        newly_applied.append(migration.version)

    return newly_applied
