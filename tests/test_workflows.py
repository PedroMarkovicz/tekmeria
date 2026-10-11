"""The GitHub workflows keep least privilege and never cut a deployment short."""

from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


@pytest.mark.parametrize("name", ["ci.yml", "live.yml", "mutants.yml", "pages.yml", "release.yml"])
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


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_every_job_has_a_time_limit(name: str) -> None:
    # A hung job would otherwise run for GitHub's default of six hours.
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
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
    # Ten cases per category cycle through every fold variant at least once.
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "run: uv run python -m tekmerion.evaluation.faults --per-category 10" in text


def test_a_release_is_published_by_trusted_publishing_after_approval() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "on:\n  release:\n    types: [published]\n" in text
    build, publish = text.split("\n  publish:\n")
    # Only the upload may ask for an identity token, and only from the approved environment.
    assert "id-token" not in build
    assert "    permissions:\n      id-token: write\n" in publish
    assert "    environment:\n      name: pypi\n" in publish
    assert "    needs: build\n" in publish
    # No stored credential: trusted publishing needs none.
    assert "password:" not in text and "secrets." not in text


def test_a_release_is_tested_and_must_match_the_package_version() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    tag_check = 'run: test "$GITHUB_REF_NAME" = "v$(uv version --short)"'
    assert text.index(tag_check) < text.index("run: uv run pytest") < text.index("run: uv build")
