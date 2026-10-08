"""`tekmerion evaluate` prints the report, can write it as JSON, and fails CI on a wrong case."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from tekmerion.__main__ import main
from tekmerion.contract.audit import Resolution, Unavailable
from tekmerion.contract.handles import Locator
from tekmerion.evaluation.snapshot import Snapshot


class EverythingDown:
    async def resolve(self, locators: Sequence[Locator]) -> Mapping[Locator, Resolution]:
        return {locator: Unavailable("down") for locator in locators}


def test_evaluate_prints_the_report_and_writes_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / "evaluation.json"
    main(["evaluate", "--per-category", "2", "--json-out", str(out_path)])
    out = capsys.readouterr().out
    assert out.startswith("planted cases: 102/102 correct (seed 0, 2 per category;")
    assert out.splitlines()[1] == "whole answers: 41/41 correct"
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["correct"] == report["cases"] == 102
    assert [(a["kind"], a["answers"], a["correct"]) for a in report["answers"]] == [
        ("mixed", 13, 13),
        ("clean", 2, 2),
        ("paired", 26, 26),
    ]


def test_evaluate_fails_when_a_case_gets_the_wrong_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(snapshot: Snapshot) -> EverythingDown:
        return EverythingDown()

    monkeypatch.setattr("tekmerion.__main__.SnapshotResolver", broken)
    with pytest.raises(SystemExit) as exited:
        main(["evaluate", "--per-category", "1"])
    assert exited.value.code == 1


@pytest.mark.parametrize("value", ["0", "-3"])
def test_evaluate_needs_at_least_one_case_per_category(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exited:  # zero cases would pass vacuously
        main(["evaluate", "--per-category", value])
    assert exited.value.code == 2
    assert "--per-category: must be at least 1" in capsys.readouterr().err
