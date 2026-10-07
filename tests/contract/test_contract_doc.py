"""docs/CONTRACT.md documents every value the code can produce (spec 12, acceptance 8)."""

import re
from pathlib import Path
from typing import get_args

import pytest

from medcite.contract import CONTRACT_VERSION
from medcite.contract.audit_models import (
    DEFAULT_FAIL_ON,
    FLAG_NAMES,
    VERDICTS,
    ClaimStatus,
    FailOn,
    OverallVerdict,
)
from medcite.contract.descriptions import METRIC_MEANINGS, PASSAGE_PATHS
from medcite.contract.handles import HANDLE_PATTERN, SCHEMES

DOC = (Path(__file__).resolve().parents[2] / "docs" / "CONTRACT.md").read_text(encoding="utf-8")

DOCUMENTED = sorted(
    {
        *VERDICTS,
        *FLAG_NAMES,
        *SCHEMES,
        *get_args(ClaimStatus),
        *get_args(OverallVerdict),
        *get_args(FailOn),
        *METRIC_MEANINGS,
        *PASSAGE_PATHS,
    }
)


def test_contract_version_is_stated() -> None:
    assert f"Contract version: `{CONTRACT_VERSION}`" in DOC


def test_handle_pattern_is_quoted_verbatim() -> None:
    assert HANDLE_PATTERN in DOC


@pytest.mark.parametrize("value", DOCUMENTED)
def test_every_value_in_the_code_is_documented(value: str) -> None:
    assert f"`{value}`" in DOC, f"`{value}` is not documented in docs/CONTRACT.md"


def test_verdicts_are_listed_in_precedence_order() -> None:
    rows = re.findall(r"^\| (\d) \| `([a-z_]+)` \|", DOC, flags=re.MULTILINE)
    assert [verdict for _, verdict in rows] == list(VERDICTS)
    assert [int(order) for order, _ in rows] == list(range(1, len(VERDICTS) + 1))


def test_default_fail_on_is_documented() -> None:
    line = next(line for line in DOC.splitlines() if "The default is" in line)
    assert all(f"`{value}`" in line for value in DEFAULT_FAIL_ON)
