"""A project-local PostgreSQL server for development, with no system install.

Why this exists: the documented setup is a local PostgreSQL 18 service (see
docs/setup.md). On a machine without one, this starts a server that lives
entirely inside the project directory, needs no administrator rights and no
Windows service, and is removed by deleting one folder.

    .venv/Scripts/python.exe tools/pg_dev_server.py start
    .venv/Scripts/python.exe tools/pg_dev_server.py status
    .venv/Scripts/python.exe tools/pg_dev_server.py stop

It uses the `pgserver` package, which bundles PostgreSQL binaries as a wheel.
**The bundled version is not necessarily the version named in docs/setup.md**;
`status` prints the version actually running, and anything verified against it
should be reported with that version, not with the documented one.

On Windows the server binds a free port chosen at start time, so `start`
writes the discovered host and port into `.env`. That keeps one source of
truth: the CLI and the tests both read `.env` and neither needs to know which
kind of server is behind it.

The data directory is `pgdata/`, which is git-ignored.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PGDATA = PROJECT_ROOT / "pgdata"
ENV_FILE = PROJECT_ROOT / ".env"

try:
    import pgserver
except ModuleNotFoundError:  # pragma: no cover
    print("pgserver is not installed: pip install -r requirements-dev.txt")
    raise SystemExit(2) from None

import psycopg


def read_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        raise SystemExit("No .env found. Copy .env.example to .env first.")
    settings: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        settings[key.strip()] = value.strip()
    return settings


def write_env_value(key: str, value: str) -> bool:
    """Set ``key`` in .env, preserving comments and order. True when changed."""
    text = ENV_FILE.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    replacement = f"{key}={value}"
    if pattern.search(text):
        if pattern.search(text).group(0) == replacement:
            return False
        text = pattern.sub(replacement, text, count=1)
    else:
        text = text.rstrip("\n") + f"\n{replacement}\n"
    ENV_FILE.write_text(text, encoding="utf-8", newline="\n")
    return True


def server():
    PGDATA.mkdir(exist_ok=True)
    # cleanup_mode=None: the server keeps running after this process exits,
    # which is what makes it usable from separate command invocations.
    try:
        return pgserver.get_server(PGDATA, cleanup_mode=None)
    except subprocess.TimeoutExpired:
        # pgserver gives pg_ctl ten seconds. A cold start on this machine can
        # take longer and still succeed: the postmaster comes up moments after
        # pg_ctl has been given up on. Treating that as a failure made `start`
        # report an error about a server that was in fact running, so poll for
        # readiness instead of trusting the timeout.
        print("pg_ctl timed out; waiting for the postmaster to answer...")
        deadline = time.monotonic() + 60
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            time.sleep(2)
            try:
                return pgserver.get_server(PGDATA, cleanup_mode=None)
            except Exception as exc:  # noqa: BLE001 - retried until the deadline
                last_error = exc
        raise RuntimeError(
            f"the server did not become ready within 60 seconds: {last_error}"
        ) from last_error


def postgres_version(uri: str) -> str:
    with psycopg.connect(uri) as conn:
        row = conn.execute("SELECT version()").fetchone()
    return row[0].split(" on ")[0]


def ensure_role(uri: str, user: str, password: str) -> str:
    """Create or update the application role. Returns what was done."""
    with psycopg.connect(uri, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)
        ).fetchone()
        quoted_user = psycopg.sql.Identifier(user)
        if exists:
            conn.execute(
                psycopg.sql.SQL("ALTER ROLE {} WITH LOGIN CREATEDB PASSWORD {}").format(
                    quoted_user, psycopg.sql.Literal(password)
                )
            )
            return f"role {user}: updated"
        conn.execute(
            psycopg.sql.SQL("CREATE ROLE {} WITH LOGIN CREATEDB PASSWORD {}").format(
                quoted_user, psycopg.sql.Literal(password)
            )
        )
        return f"role {user}: created"


def cmd_start() -> int:
    settings = read_env()
    user = settings.get("INVAUT_DB_USER")
    password = settings.get("INVAUT_DB_PASSWORD")
    if not user or not password or password == "change_me":
        raise SystemExit("Set INVAUT_DB_USER and INVAUT_DB_PASSWORD in .env first.")

    srv = server()
    info = srv.get_postmaster_info()
    uri = srv.get_uri()
    host, port = info.hostname, str(info.port)

    print(f"server running: pid {srv.get_pid()}, {host}:{port}")
    print(f"version: {postgres_version(uri)}")
    print(ensure_role(uri, user, password))

    for key, value in (("INVAUT_DB_HOST", host), ("INVAUT_DB_PORT", port)):
        if write_env_value(key, value):
            print(f".env: {key} set to {value}")
    print("\nnext: .venv/Scripts/python.exe -m invoice_automation.cli init-db")
    return 0


def cmd_status() -> int:
    if not PGDATA.exists():
        print("no pgdata directory; the server has never been started here")
        return 1
    srv = pgserver.get_server(PGDATA, cleanup_mode=None)
    info = srv.get_postmaster_info()
    if info is None:
        print("server is not running")
        return 1
    print(f"pid {info.pid}, {info.hostname}:{info.port}, status {info.status}")
    print(f"version: {postgres_version(srv.get_uri())}")
    print(f"pgdata: {PGDATA}")
    return 0


def cmd_stop() -> int:
    if not PGDATA.exists():
        print("no pgdata directory; nothing to stop")
        return 0
    try:
        pgserver.pg_ctl(["-w", "stop"], pgdata=PGDATA)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        print(f"could not stop the server: {exc}")
        return 1
    print("server stopped; pgdata kept")
    return 0


COMMANDS = {"start": cmd_start, "status": cmd_status, "stop": cmd_stop}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in COMMANDS:
        print(f"usage: {Path(argv[0]).name} {{{'|'.join(COMMANDS)}}}")
        return 2
    return COMMANDS[argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
