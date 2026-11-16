"""Check that docs/user_stories.md still describes the code it claims to.

    .\\.venv\\Scripts\\python.exe tools\\check_user_stories.py

A story is only as good as the evidence beside it. This checks that evidence
mechanically: every test a story names must exist (the file, the class, the
function), every file it names must exist, every story must name something,
every requirement in business_requirements.md must have a story, and the
traceability table at the bottom must agree with the stories above it, since a
hand-kept table is the first thing to drift.

Whether a test actually asserts what its story says cannot be checked
automatically and is not attempted. That is a reading job.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORIES = ROOT / "docs" / "user_stories.md"
REQUIREMENTS = ROOT / "docs" / "business_requirements.md"

STATUSES = ("Built", "Partly built", "Not built")
HEADING = re.compile(r"^### (US-\d{2}) — .+$", re.MULTILINE)
META = re.compile(
    r"\*\*Requirement:\*\*\s*([\d,\s]+?)\s*·\s*\*\*Status:\*\*\s*(Built|Partly built|Not built)")
EVIDENCE = re.compile(r"^- `([^`]+)`\s*$", re.MULTILINE)
REQUIREMENT_ROW = re.compile(r"^\| (\d+) \| ", re.MULTILINE)
TRACE_ROW = re.compile(r"^\| (\d+) \| ((?:US-\d{2}(?:, )?)+) \|\s*$", re.MULTILINE)


def _defined(path: Path, parts: list[str]) -> bool:
    """Whether `Class::function` (or a module-level `function`) exists in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scope: list[ast.stmt] = tree.body
    for i, name in enumerate(parts):
        last = i == len(parts) - 1
        wanted = (ast.FunctionDef, ast.AsyncFunctionDef) if last else (ast.ClassDef,)
        match = next((n for n in scope if isinstance(n, wanted) and n.name == name), None)
        if match is None:
            return False
        scope = match.body
    return True


def problems(stories: str, requirements: str, root: Path = ROOT) -> list[str]:
    """Everything wrong with the stories text, as readable lines. Empty means fine."""
    found: list[str] = []
    marks = list(HEADING.finditer(stories))
    if not marks:
        return ["no stories found"]

    ids = [m.group(1) for m in marks]
    expected = [f"US-{i:02d}" for i in range(1, len(ids) + 1)]
    if ids != expected:
        found.append(f"story ids are not US-01..US-{len(ids):02d} in order: {', '.join(ids)}")

    covers: dict[int, set[str]] = {}
    for i, mark in enumerate(marks):
        sid = mark.group(1)
        end = marks[i + 1].start() if i + 1 < len(marks) else len(stories)
        block = stories[mark.start():end]
        # Stop at the next level-2 section, so the "Not built" table is not read
        # as part of the last story.
        block = re.split(r"^## ", block, maxsplit=1, flags=re.MULTILINE)[0]

        meta = META.search(block)
        if meta is None:
            found.append(f"{sid}: no **Requirement:** · **Status:** line")
            continue
        for number in re.findall(r"\d+", meta.group(1)):
            covers.setdefault(int(number), set()).add(sid)

        evidence = EVIDENCE.findall(block)
        if not evidence:
            found.append(f"{sid}: names no evidence")
        for ref in evidence:
            path_part, *parts = ref.split("::")
            path = root / path_part
            if not path.exists():
                found.append(f"{sid}: {path_part} does not exist")
            elif parts and not _defined(path, parts):
                found.append(f"{sid}: {ref} is not defined")

    for number in sorted({int(n) for n in REQUIREMENT_ROW.findall(requirements)}):
        if number not in covers:
            found.append(f"requirement {number} has no story")

    table = {int(n): set(s.split(", ")) for n, s in TRACE_ROW.findall(stories)}
    if not table:
        found.append("no traceability table found")
    for number in sorted(set(table) | set(covers)):
        if table.get(number, set()) != covers.get(number, set()):
            found.append(
                f"traceability row {number} lists {sorted(table.get(number, set()))} "
                f"but the stories say {sorted(covers.get(number, set()))}")
    return found


def main() -> int:
    for path in (STORIES, REQUIREMENTS):
        if not path.exists():
            sys.exit(f"{path} not found.")
    found = problems(STORIES.read_text(encoding="utf-8"),
                     REQUIREMENTS.read_text(encoding="utf-8"))
    count = len(HEADING.findall(STORIES.read_text(encoding="utf-8")))
    if found:
        print(f"{len(found)} problem(s) in docs/user_stories.md:")
        for line in found:
            print(f"  - {line}")
        return 1
    print(f"OK: {count} stories, every named test and file exists, "
          "every requirement is covered, and the traceability table agrees.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
