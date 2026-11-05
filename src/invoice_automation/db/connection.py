"""Database connections.

A plain psycopg 3 connection with ``dict_row``. No ORM: the schema and the
reporting views are the interesting part of this project, and hand-written SQL
keeps them visible.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg import sql as pgsql
from psycopg.rows import dict_row

from ..config import Settings, get_settings


@contextmanager
def connect(
    settings: Settings | None = None,
    database: str | None = None,
    autocommit: bool = True,
) -> Iterator[psycopg.Connection]:
    """Open a connection as a context manager.

    Transactions are explicit at the call site: the pipeline wraps its work in
    ``with conn.transaction():`` so that a failure cannot leave a submission
    recorded without its events.

    ``autocommit`` defaults to **True**, which is what makes that work. With
    autocommit off, psycopg opens an implicit transaction on the first
    statement; a read performed before a ``conn.transaction()`` block means
    the block is no longer the outermost transaction, so psycopg implements it
    as a savepoint and exiting it commits nothing. The work then disappears
    when the connection closes, with no error raised anywhere.

    That is not a hypothetical: it is what happened the first time this schema
    met a real server. Both the migration runner and the pipeline read before
    writing, so both lost everything they did. With autocommit on, a read
    leaves the connection idle and ``conn.transaction()`` is always a real
    transaction that commits on exit.
    """
    settings = settings or get_settings()
    conn = psycopg.connect(
        settings.dsn(database), row_factory=dict_row, autocommit=autocommit
    )
    try:
        yield conn
    finally:
        conn.close()


def database_exists(settings: Settings, name: str) -> bool:
    with connect(settings, database="postgres", autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        return row is not None


def create_database(settings: Settings, name: str) -> bool:
    """Create ``name`` if it does not exist. Returns True when created."""
    if database_exists(settings, name):
        return False
    with connect(settings, database="postgres", autocommit=True) as conn:
        # Identifiers cannot be parameterised; quote it instead.
        conn.execute(
            pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(name))
        )
    return True


def drop_database(settings: Settings, name: str) -> None:
    """Drop ``name`` if present. Used only by the integration test fixture."""
    with connect(settings, database="postgres", autocommit=True) as conn:
        conn.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                pgsql.Identifier(name)
            )
        )
