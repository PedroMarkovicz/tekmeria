"""The GitHub workflows keep least privilege and never cut a deployment short."""

from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


@pytest.mark.parametrize("name", ["ci.yml", "live.yml", "mutants.yml", "pages.yml"])
def test_every_workflow_declares_its_permissions(name: str) -> None:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert "\npermissions:\n  contents: read\n" in text


def test_a_pages_deployment_is_never_cancelled_mid_way() -> None:
    text = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
    assert "cancel-in-progress: false" in text


def test_pages_deploys_the_commit_ci_tested_after_a_push() -> None:
    text = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
    assert "ref: ${{ github.event.workflow_run.head_sha || github.sha }}" in text
    assert "github.event.workflow_run.event == 'push'" in text


def test_ci_runs_the_planted_error_evaluation() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "run: uv run tekmerion evaluate" in text


def test_every_ci_job_has_a_time_limit() -> None:
    # A hung job would otherwise run for GitHub's default of six hours.
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    jobs = text.split("\njobs:\n", 1)[1].splitlines()
    names = [line for line in jobs if line.startswith("  ") and line[2] != " "]
    assert names and text.count("    timeout-minutes: ") == len(names)


def test_the_mutation_run_is_on_request_only() -> None:
    # About 80 runs of the evaluation: worth a manual run, too slow for every push.
    text = (WORKFLOWS / "mutants.yml").read_text(encoding="utf-8")
    assert "on:\n  workflow_dispatch:\n\npermissions" in text
    assert "timeout-minutes: 30" in text
    assert "run: uv run python -m tekmerion.evaluation.mutants" in text


def test_ci_runs_the_injected_faults() -> None:
    # Eight cases per category cycle through every fold variant at least once.
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "run: uv run python -m tekmerion.evaluation.faults --per-category 8" in text
