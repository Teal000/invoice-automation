"""docs/user_stories.md names a test for every claim; this keeps those names real.

The checker is tested against the real document and against broken copies of
it, because a checker that has only ever printed OK has not been checked.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_user_stories", ROOT / "tools" / "check_user_stories.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load_checker()
STORIES = (ROOT / "docs" / "user_stories.md").read_text(encoding="utf-8")
REQUIREMENTS = (ROOT / "docs" / "business_requirements.md").read_text(encoding="utf-8")


def test_the_real_stories_have_no_problems():
    assert CHECK.problems(STORIES, REQUIREMENTS) == []


def test_every_requirement_in_the_requirements_doc_is_read():
    """If the requirements table changes shape, the coverage check must not
    silently start checking nothing."""
    numbers = {int(n) for n in CHECK.REQUIREMENT_ROW.findall(REQUIREMENTS)}
    assert numbers == set(range(1, 9))


@pytest.mark.parametrize(("broken", "expected"), [
    pytest.param(
        STORIES.replace("test_both_revisions_survive", "test_both_revisions_vanish"),
        "test_both_revisions_vanish is not defined", id="a test that does not exist"),
    pytest.param(
        STORIES.replace("tests/unit/test_rules_arithmetic.py", "tests/unit/test_rules_maths.py"),
        "test_rules_maths.py does not exist", id="a file that does not exist"),
    pytest.param(
        STORIES.replace("| 2 | US-02 |", "| 2 | US-03 |"),
        "traceability row 2", id="a traceability row that disagrees"),
    pytest.param(
        STORIES.replace("**Requirement:** 1 · **Status:** Built",
                        "**Requirement:** 2 · **Status:** Built").replace("| 1 | US-01 |\n", ""),
        "requirement 1 has no story", id="a requirement left uncovered"),
    pytest.param(
        STORIES.replace("### US-02 —", "### US-04 —", 1),
        "not US-01..", id="story ids out of order"),
])
def test_breakage_is_reported(broken: str, expected: str):
    assert broken != STORIES, "the mutation did not apply"
    found = CHECK.problems(broken, REQUIREMENTS)
    assert any(expected in line for line in found), found


def test_a_story_with_no_evidence_is_reported():
    block_start = STORIES.index("### US-15")
    block_end = STORIES.index("### US-16")
    stripped_block = "\n".join(
        line for line in STORIES[block_start:block_end].splitlines()
        if not line.startswith("- `"))
    broken = STORIES[:block_start] + stripped_block + "\n\n" + STORIES[block_end:]
    assert any("US-15: names no evidence" in line
               for line in CHECK.problems(broken, REQUIREMENTS))
