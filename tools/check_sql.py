"""Parse every SQL statement in the project with the real PostgreSQL grammar.

Why this exists: the schema and the hand-written queries are the substance of
this project, and a typo in them should not wait for a live database to be
found. ``pglast`` wraps ``libpg_query``, which is the PostgreSQL server's own
parser, so a statement that parses here is syntactically valid PostgreSQL.

What it does not check: that columns exist, that types match, or that views
resolve. Only a live database proves those, and the integration tests do.

    .venv/Scripts/python.exe -m pip install -r requirements-dev.txt
    .venv/Scripts/python.exe tools/check_sql.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

try:
    import pglast
except ModuleNotFoundError:  # pragma: no cover
    print("pglast is not installed: pip install -r requirements-dev.txt")
    raise SystemExit(2) from None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
#: Methods whose first string argument is SQL.
SQL_CALLS = {"execute", "executemany"}
#: Both psycopg placeholder styles: positional %s and named %(name)s. Replaced
#: with NULL before parsing, since the grammar does not know them.
PLACEHOLDER = re.compile(r"%\([A-Za-z_][A-Za-z0-9_]*\)s|%s")


def sql_literals(path: Path) -> list[tuple[int, str]]:
    """Every literal SQL string passed to execute()/executemany() in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in SQL_CALLS or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.append((first.lineno, first.value))
    return found


def check_insert_arity(statement: str) -> str | None:
    """Compare the column list with the value list of a parameterised INSERT.

    Literals such as ``'running'`` or ``now()`` count as values, so the check
    counts value expressions rather than placeholders.
    """
    flat = " ".join(statement.split())
    match = re.search(
        r"INSERT INTO\s+\w+\s*\(([^)]*)\)\s*VALUES\s*\(", flat, re.IGNORECASE
    )
    if not match:
        return None
    columns = [c.strip() for c in match.group(1).split(",") if c.strip()]

    # Walk the VALUES tuple, splitting on top-level commas only.
    start = match.end()
    depth = 1
    current = ""
    values: list[str] = []
    for char in flat[start:]:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                break
        if depth == 1 and char == ",":
            values.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        values.append(current.strip())

    if len(columns) != len(values):
        return f"{len(columns)} columns but {len(values)} values"
    return None


def main() -> int:
    problems = 0
    parsed = 0

    for path in sorted((PROJECT_ROOT / "db" / "migrations").glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        try:
            statements = pglast.parse_sql(text)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            problems += 1
            print(f"FAIL {path.name}: {exc}")
            continue
        parsed += len(statements)
        print(f"ok   {path.name}: {len(statements)} statements")

    for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        for lineno, statement in sql_literals(path):
            probe = PLACEHOLDER.sub("NULL", statement)
            try:
                pglast.parse_sql(probe)
                parsed += 1
            except Exception as exc:  # noqa: BLE001
                problems += 1
                print(f"FAIL {path.relative_to(PROJECT_ROOT)}:{lineno}: {exc}")
                print("     " + " ".join(statement.split())[:150])
            arity = check_insert_arity(statement)
            if arity:
                problems += 1
                print(f"FAIL {path.relative_to(PROJECT_ROOT)}:{lineno}: INSERT {arity}")

    print(f"\n{parsed} statements parsed, {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
