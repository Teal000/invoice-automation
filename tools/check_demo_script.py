"""Check that every path, CLI command and API route the demo script names exists.

    .\\.venv\\Scripts\\python.exe tools\\check_demo_script.py

A demo script that cites a deleted file is worse than no script, because it is
discovered on camera. This one did: it pointed at
`evaluation/results/extraction_baseline.md` for days after that file was
replaced by the benchmark splits, and at two steps marked NOT BUILT YET that
had since been built.

Prose cannot be checked automatically and is not attempted here. What can be
checked is every concrete thing the script tells a presenter to type or open.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "docs" / "demo_script.md"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

#: Routes that are not defined in this codebase.
EXTERNAL_ROUTES = {"/docs", "/webhook/invoice-submission"}

FILE_IN_BACKTICKS = re.compile(r"`([A-Za-z0-9_./\\-]+\.(?:py|md|json|pdf|ps1))`")
FILE_IN_COMMAND = re.compile(
    r"(?:data|tools|evaluation|workflows|dashboard|docs)[\\/]"
    r"[A-Za-z0-9_./\\-]+\.(?:py|md|json|pdf|ps1)")
DIR_IN_BACKTICKS = re.compile(
    r"`((?:workflows|dashboard|data|docs|evaluation)/[A-Za-z0-9_./-]*/)`")
CLI_COMMAND = re.compile(r"invoice_automation\.cli\s+([a-z][a-z-]+)")
ROUTE_IN_PROSE = re.compile(r"(?:GET|POST|PATCH|DELETE)\s+(/[A-Za-z0-9_{}/-]+)")
ROUTE_IN_URL = re.compile(r"http://127\.0\.0\.1:8000(/[A-Za-z0-9_{}/-]+)")
ROUTE_DECORATOR = re.compile(
    r'@\w+\.(?:get|post|patch|put|delete)\(\s*["\']([^"\']+)')
HELP_COMMAND = re.compile(r"^\s*│\s*([a-z][a-z-]+)\s", re.MULTILINE)


def normalise(route: str) -> str:
    """`/invoices/{id}/revisions` and `/invoices/{invoice_id}/revisions` are one route."""
    return re.sub(r"\{[^}]+\}", "{}", route.rstrip("/"))


def main() -> int:
    if not SCRIPT.exists():
        sys.exit(f"{SCRIPT} not found.")
    text = SCRIPT.read_text(encoding="utf-8")
    problems: list[str] = []

    files = set(FILE_IN_BACKTICKS.findall(text)) | set(FILE_IN_COMMAND.findall(text))
    for raw in sorted(files):
        if not (ROOT / raw.replace("\\", "/")).exists():
            problems.append(f"missing file: {raw}")

    dirs = set(DIR_IN_BACKTICKS.findall(text))
    for raw in sorted(dirs):
        if not (ROOT / raw.replace("\\", "/")).is_dir():
            problems.append(f"missing directory: {raw}")

    used = set(CLI_COMMAND.findall(text))
    if used:
        if not PYTHON.exists():
            problems.append(f"cannot check CLI commands: {PYTHON} not found")
            available: set[str] = set()
        else:
            help_text = subprocess.run(
                [str(PYTHON), "-m", "invoice_automation.cli", "--help"],
                cwd=ROOT, capture_output=True, text=True).stdout
            available = set(HELP_COMMAND.findall(help_text))
            for cmd in sorted(used - available):
                problems.append(f"CLI command not found: {cmd}")

    routes: set[str] = set()
    for py in (ROOT / "src").rglob("*.py"):
        routes |= set(ROUTE_DECORATOR.findall(
            py.read_text(encoding="utf-8", errors="replace")))
    known = {normalise(r) for r in routes}

    cited = set(ROUTE_IN_PROSE.findall(text)) | set(ROUTE_IN_URL.findall(text))
    for route in sorted(cited):
        if route in EXTERNAL_ROUTES:
            continue
        if normalise(route) not in known:
            problems.append(f"API route not defined in src/: {route}")

    print(f"{len(files)} files, {len(dirs)} directories, {len(used)} CLI commands, "
          f"{len(cited)} API routes checked in {SCRIPT.relative_to(ROOT)}")

    if problems:
        print("\nFAILED:")
        for p in problems:
            print("  !!", p)
        return 1
    print("\nOK: everything the demo script names exists.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
