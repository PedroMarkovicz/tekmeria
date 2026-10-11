"""The README stays true to the code: tools, flags, settings, links and the demo output."""

import re
from pathlib import Path

import pytest
from mcp import Client

from tekmerion.__main__ import main
from tekmerion.demo.scenario import run_demo
from tekmerion.infra.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_every_tool_is_listed(client: Client) -> None:
    for tool in (await client.list_tools()).tools:
        assert f"| `{tool.name}` |" in README, f"{tool.name} is missing from the tools table"


@pytest.mark.anyio
async def test_the_demo_output_matches_the_demo(client: Client) -> None:
    run = await run_demo(client)
    audit = run.audit.content[0].text  # type: ignore[union-attr]
    block = re.search(r"```text\n(verdict: .*?)\n```", README, flags=re.DOTALL)
    assert block, "README has no demo audit block"
    assert block.group(1) == audit.strip()


def test_every_setting_is_documented() -> None:
    for field in Settings.model_fields.values():
        assert f"| `{field.alias}` |" in README, f"{field.alias} is missing from the README"


def _help(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit):
        main(argv)
    return capsys.readouterr().out


def test_every_flag_in_the_readme_exists(capsys: pytest.CaptureFixture[str]) -> None:
    known = _help(["--help"], capsys) + _help(["demo", "--help"], capsys)
    lines = [line for line in README.splitlines() if "tekmerion" in line and "--" in line]
    flags = {flag for line in lines for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]+", line)}
    flags -= {"--from"}  # uvx's own flag
    assert flags, "no tekmerion flags found in the README"
    for flag in flags:
        assert flag in known, f"{flag} is not a tekmerion flag"


REPO = "https://github.com/PedroMarkovicz/tekmerion"
RAW = "https://raw.githubusercontent.com/PedroMarkovicz/tekmerion/main/"


def test_linked_files_exist() -> None:
    files = re.findall(rf"\]\({re.escape(REPO)}/blob/main/([^)#]+)\)", README)
    images = re.findall(rf"\]\({re.escape(RAW)}([^)#]+)\)", README)
    assert files and images
    for target in files + images:
        assert (ROOT / target).exists(), f"README links to missing {target}"
    assert "https://pedromarkovicz.github.io/tekmerion/" in README


def test_every_link_works_on_pypi_too() -> None:
    # PyPI shows this README as the package description, where relative links break; a
    # release's description cannot be changed afterwards.
    targets = re.findall(r"\]\(([^)]+)\)", README)
    relative = [t for t in targets if not t.startswith(("https://", "http://", "#", "mailto:"))]
    assert relative == []


def test_no_text_assumes_a_private_repository() -> None:
    acceptance = (ROOT / "docs" / "ACCEPTANCE.md").read_text(encoding="utf-8")
    for text in (README, acceptance):
        assert "repository is private" not in text
    assert "Once GitHub Pages is enabled" not in README


def _acceptance_status(row: int) -> str:
    doc = (ROOT / "docs" / "ACCEPTANCE.md").read_text(encoding="utf-8")
    line = next(line for line in doc.splitlines() if line.startswith(f"| {row} |"))
    return line.rsplit("|", 2)[-2].strip()


def test_claims_about_pages_and_hosts_wait_for_the_acceptance_checks() -> None:
    if not _acceptance_status(5).startswith("Done"):
        assert "is published at" not in README, "Pages is not deployed yet (criterion 5)"
    if not _acceptance_status(6).startswith("Done"):
        assert "results as the audit view" not in README, "no host render shown yet (criterion 6)"


def test_the_evaluation_output_matches_the_evaluation(capsys: pytest.CaptureFixture[str]) -> None:
    main(["evaluate"])
    block = README.split("<!-- evaluation:start -->", 1)[1].split("<!-- evaluation:end -->", 1)[0]
    assert block.strip() == capsys.readouterr().out.strip()


def test_the_evaluation_section_claims_only_what_it_shows() -> None:
    section = README.split("## Evaluation", 1)[1].split("\n## ", 1)[0]
    assert "Each case cites a real passage" not in section  # withdrawn cases cite made-up ones
    assert "reference oracle" in section and "not independent" in section
