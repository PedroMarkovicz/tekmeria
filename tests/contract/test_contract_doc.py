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
    CitationIn,
    ClaimIn,
    ClaimStatus,
    FailOn,
    OverallVerdict,
)
from medcite.contract.descriptions import METRIC_MEANINGS, PASSAGE_PATHS
from medcite.contract.handles import HANDLE_PATTERN, SCHEMES, HandleCodec
from medcite.contract.normalize import _FOLDS  # pyright: ignore[reportPrivateUsage]
from medcite.infra.keys import DEMO_KEY

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


def _max_length(model: type, field: str) -> int:
    meta = model.model_fields[field].metadata  # type: ignore[attr-defined]
    return next(m.max_length for m in meta if hasattr(m, "max_length"))


def test_examples_are_real_demo_handles() -> None:
    block = DOC.split("Examples", 1)[1].split("```", 2)[1]
    examples = [line.strip() for line in block.splitlines() if "~" in line]
    assert len(examples) == 4
    codec = HandleCodec(DEMO_KEY)
    for handle in examples:
        codec.verify(handle)  # raises for an invented check code


def test_every_fold_is_documented_by_code_point() -> None:
    for code in [*_FOLDS, ord("…")]:
        assert f"U+{code:04X}" in DOC, f"fold of U+{code:04X} is not documented"


def test_input_limits_are_documented() -> None:
    limits = [
        _max_length(CitationIn, "handle"),
        _max_length(CitationIn, "quote"),
        _max_length(ClaimIn, "sentence"),
        _max_length(ClaimIn, "citations"),
        20000,  # answer_text, server/app.py
        100,  # claims, server/app.py
    ]
    section = DOC.split("## 4. Citing in an answer", 1)[1].split("## 5.", 1)[0]
    for limit in limits:
        assert f"{limit:,}" in section, f"limit {limit:,} is not documented in section 4"


def test_quote_accuracy_counts_only_quotes_that_reached_the_check() -> None:
    row = next(line for line in DOC.splitlines() if line.startswith("| `quote_accuracy` |"))
    assert "`ok` or `quote_mismatch`" in row
