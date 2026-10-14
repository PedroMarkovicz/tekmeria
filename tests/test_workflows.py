"""The GitHub workflows keep least privilege and never cut a deployment short."""

import re
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


def test_pages_runs_after_ci_on_main_and_on_request() -> None:
    text = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
    trigger = "  workflow_run:\n    workflows: [ci]\n    types: [completed]\n    branches: [main]\n"
    assert f"\non:\n{trigger}  workflow_dispatch:\n\npermissions" in text
    # workflow_run finds CI by its name: after a rename the demo page would stop updating.
    assert (WORKFLOWS / "ci.yml").read_text(encoding="utf-8").startswith("name: ci\n")


def test_ci_runs_the_planted_error_evaluation() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "run: uv run tekmeria evaluate" in text


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
    assert "run: uv run python -m tekmeria.evaluation.mutants" in text


def test_ci_runs_the_injected_faults() -> None:
    # Ten cases per category cycle through every fold variant at least once.
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "run: uv run python -m tekmeria.evaluation.faults --per-category 10" in text


def test_a_release_is_published_by_trusted_publishing_after_approval() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "on:\n  release:\n    types: [published]\n" in text
    build, rest = text.split("\n  publish:\n")
    publish, registry = rest.split("\n  registry:\n")
    # Only the two uploads may ask for an identity token, and the package only from the
    # approved environment; the registry entry follows the package it lists.
    assert "id-token" not in build
    assert "    permissions:\n      id-token: write\n" in publish
    assert "    environment:\n      name: pypi\n" in publish
    assert "    needs: build\n" in publish
    assert "      id-token: write\n" in registry
    assert "    needs: publish\n" in registry
    assert "run: ./mcp-publisher login github-oidc" in registry
    # No stored credential: trusted publishing and the registry's OIDC login need none.
    assert "password:" not in text and "secrets." not in text


def test_a_release_needs_ci_to_have_passed_on_the_tagged_commit() -> None:
    # The release job runs only some of the checks; CI on the same commit ran them all.
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    query = "actions/workflows/ci.yml/runs?head_sha=$GITHUB_SHA&status=success"
    assert query in text
    assert text.index(query) < text.index("run: uv build")
    build = text.split("\n  publish:\n")[0]
    assert "      actions: read\n" in build


def test_the_registry_publisher_is_pinned_and_checked_before_the_upload() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert re.search(r"releases/download/v\d+\.\d+\.\d+/mcp-publisher_linux_amd64\.tar\.gz", text)
    assert re.search(r"MCP_PUBLISHER_SHA256: [0-9a-f]{64}\n", text)
    assert text.count("| sha256sum --check") == 2
    # An invalid entry stops the release before anything is uploaded.
    assert text.index("./mcp-publisher validate") < text.index("run: uv build")


def test_a_release_is_tested_and_must_match_the_package_version() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    tag_check = 'run: test "$GITHUB_REF_NAME" = "v$(uv version --short)"'
    assert text.index(tag_check) < text.index("run: uv run pytest") < text.index("run: uv build")


def test_a_release_waits_for_the_demo_page_the_readme_links_to() -> None:
    # The README, frozen into the package page at release, links the audit view on GitHub
    # Pages: no release while that page does not answer.
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    check = "run: curl --fail --silent --show-error --location --output /dev/null"
    assert f"{check} https://pedromarkovicz.github.io/tekmeria/" in text
    assert text.index(check) < text.index("run: uv build")


def test_every_action_is_pinned_to_a_commit() -> None:
    # A tag can be moved to other code; a commit hash cannot. The comment names the version.
    for path in sorted(WORKFLOWS.glob("*.yml")):
        uses = re.findall(r"uses: (\S+)(.*)", path.read_text(encoding="utf-8"))
        assert uses, path.name
        for action, comment in uses:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), f"{path.name}: {action}"
            assert re.fullmatch(r" # v\d+\.\d+\.\d+", comment), f"{path.name}: {action}{comment}"


def test_dependency_updates_are_applied_by_hand() -> None:
    # No scheduled update pull requests: an update of the audit view's packages needs the
    # bundled view rebuilt with it, and a pinned action needs its new commit checked.
    assert not (WORKFLOWS.parent / "dependabot.yml").exists()
