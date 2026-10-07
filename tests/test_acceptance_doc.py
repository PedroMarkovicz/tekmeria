"""docs/ACCEPTANCE.md only points at tests that exist (spec 13)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "ACCEPTANCE.md").read_text(encoding="utf-8")
# `path::name`, for Python tests (`def name`) and Vitest tests (`it("name"`).
REFERENCE = re.compile(r"`((?:tests|web/src)/[\w/.]+)::([^`]+)`")


def test_every_referenced_test_exists() -> None:
    references = REFERENCE.findall(DOC)
    assert len(references) >= 8
    for path, name in references:
        source = (ROOT / path).read_text(encoding="utf-8")
        if path.endswith(".py"):
            assert re.search(rf"def {re.escape(name)}\(", source), f"{path}::{name} not found"
        else:
            assert f'"{name}"' in source, f"{path}::{name} not found"


def test_every_criterion_has_a_row() -> None:
    rows = re.findall(r"^\| (\d) \|", DOC, flags=re.MULTILINE)
    assert rows == [str(n) for n in range(1, 9)]


def test_no_personal_local_paths() -> None:
    assert not re.search(r"[A-Z]:/(?:Projects|Users)/", DOC)
