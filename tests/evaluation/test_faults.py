"""The fault harness breaks one decision at a time, restores it, and reports every miss."""

import pytest

from tekmerion.contract import audit as audit_module
from tekmerion.evaluation import faults, oracle
from tekmerion.evaluation.faults import FAULTS, NOT_EXERCISED, Fault, Row, injected, measure
from tekmerion.evaluation.snapshot import Snapshot
from tekmerion.infra.keys import DEMO_KEY

pytestmark = pytest.mark.anyio


def test_every_fault_puts_back_what_it_replaced() -> None:
    for fault in FAULTS:
        owner, attribute = fault.target
        real = getattr(owner, attribute)
        with injected(fault):
            assert getattr(owner, attribute) is not real, fault.name
        assert getattr(owner, attribute) is real, fault.name


def test_a_fault_is_put_back_even_when_the_run_fails() -> None:
    fault = FAULTS[0]
    owner, attribute = fault.target
    real = getattr(owner, attribute)
    with pytest.raises(ValueError), injected(fault):
        raise ValueError("the evaluation crashed")
    assert getattr(owner, attribute) is real


def test_every_fold_of_the_spec_has_its_fault() -> None:
    names = {fault.name for fault in FAULTS}
    assert {f"no fold for U+{ord(char):04X}" for char in oracle.FOLDS} <= names


async def test_a_real_fault_is_caught_and_a_harmless_one_is_not(
    snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_blind = next(f for f in FAULTS if f.name == "comparator ignores letter case")
    harmless = Fault("no change", (audit_module, "quote_in_passage"), lambda real: real)
    monkeypatch.setattr(faults, "FAULTS", (case_blind, harmless))
    rows = await measure(snapshot, DEMO_KEY, per_category=5)
    assert [(row.fault, row.caught) for row in rows] == [
        ("comparator ignores letter case", True),
        ("no change", False),
    ]
    assert not faults.passed(rows)  # a miss that is not listed fails the run


def test_only_the_listed_misses_may_go_uncaught() -> None:
    def row(name: str, caught: bool) -> Row:
        return Row(name, 10, 10 - caught, 4, 4)

    listed = next(iter(NOT_EXERCISED))
    assert faults.passed([row("a", True), row(listed, False)])
    assert not faults.passed([row("a", False), row(listed, False)])


def test_the_table_names_each_miss_and_its_reason() -> None:
    listed = next(iter(NOT_EXERCISED))
    text = faults.render([Row("a", 10, 9, 4, 4), Row(listed, 10, 10, 4, 4)])
    lines = text.splitlines()
    assert lines[0] == "injected faults: 1/2 caught"
    assert "| a | 9/10 | 4/4 |" in lines
    assert f"| {listed} (not caught: {NOT_EXERCISED[listed]}) | 10/10 | 4/4 |" in lines


async def test_the_evaluation_must_pass_before_any_fault(
    snapshot: Snapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Otherwise every fault would look caught.
    monkeypatch.setattr(audit_module, "_flags", lambda locator, record: [])
    with pytest.raises(RuntimeError, match="without any fault"):
        await measure(snapshot, DEMO_KEY, per_category=1)
