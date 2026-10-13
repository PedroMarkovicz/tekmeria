"""The README stays true to the code: tools, flags, settings, links and the demo output."""

import re
from pathlib import Path

import pytest
from mcp import Client

from tekmeria.__main__ import main
from tekmeria.demo.scenario import run_demo
from tekmeria.infra.settings import Settings

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
    lines = [line for line in README.splitlines() if "tekmeria" in line and "--" in line]
    flags = {flag for line in lines for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]+", line)}
    flags -= {"--from"}  # uvx's own flag
    assert flags, "no tekmeria flags found in the README"
    for flag in flags:
        assert flag in known, f"{flag} is not a tekmeria flag"


REPO = "https://github.com/PedroMarkovicz/tekmeria"
RAW = "https://raw.githubusercontent.com/PedroMarkovicz/tekmeria/main/"


def _targets() -> list[str]:
    """Every link and image target: Markdown links and the HTML attributes of the hero."""
    return re.findall(r"\]\(([^)\s]+)\)", README) + re.findall(r'(?:href|src)="([^"]+)"', README)


def test_linked_files_exist() -> None:
    targets = _targets()
    blob, tree = f"{REPO}/blob/main/", f"{REPO}/tree/main/"
    files = [t.removeprefix(blob) for t in targets if t.startswith(blob)]
    folders = [t.removeprefix(tree) for t in targets if t.startswith(tree)]
    images = [t.removeprefix(RAW) for t in targets if t.startswith(RAW)]
    assert files and folders and images
    for target in files + images:
        assert (ROOT / target.split("#")[0]).is_file(), f"README links to missing {target}"
    for target in folders:
        assert (ROOT / target).is_dir(), f"README links to missing folder {target}"
    assert "https://pedromarkovicz.github.io/tekmeria/" in README


def test_every_link_works_on_pypi_too() -> None:
    # PyPI shows this README as the package description, where relative links break; a
    # release's description cannot be changed afterwards.
    relative = [t for t in _targets() if not t.startswith(("https://", "http://", "#", "mailto:"))]
    assert relative == []


def test_every_section_link_has_its_anchor() -> None:
    # PyPI gives headings no ids, so every "#section" link needs an explicit anchor.
    anchors = set(re.findall(r'<a id="([^"]+)"></a>', README))
    links = {t.removeprefix("#") for t in _targets() if t.startswith("#")}
    assert links and links <= anchors, sorted(links - anchors)


def test_the_banner_has_its_source_beside_it() -> None:
    assert (ROOT / "docs" / "assets" / "banner.html").exists()
    hero = README.split("\n## ", 1)[0]
    assert README.startswith('<div align="center">')
    assert f'src="{RAW}docs/assets/banner.png"' in hero


def test_headings_carry_no_emoji() -> None:
    # An academic register: the headings stay plain text.
    headings = [line for line in README.splitlines() if line.startswith("#")]
    assert headings
    for heading in headings:
        assert all(ord(char) < 0x2000 for char in heading), heading
    assert "Built with ☕ and a healthy distrust of unverifiable citations" in README


def test_no_text_assumes_a_private_repository() -> None:
    acceptance = (ROOT / "docs" / "ACCEPTANCE.md").read_text(encoding="utf-8")
    for text in (README, acceptance):
        assert "repository is private" not in text
    assert "Once GitHub Pages is enabled" not in README


def _acceptance_status(row: int) -> str:
    doc = (ROOT / "docs" / "ACCEPTANCE.md").read_text(encoding="utf-8")
    line = next(line for line in doc.splitlines() if line.startswith(f"| {row} |"))
    return line.rsplit("|", 2)[-2].strip()


def test_claims_about_hosts_wait_for_the_acceptance_checks() -> None:
    # The Pages demo the README links to is checked by the release workflow, which refuses
    # to publish while that page does not answer (tests/test_workflows.py).
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


def test_the_summary_numbers_are_the_evaluation_s() -> None:
    # The results table, and the diagrams in docs/DESIGN.md, repeat numbers of the
    # evaluation's output, which another test compares with a real run.
    from tekmeria.evaluation.faults import FAULTS, NOT_EXERCISED

    block = README.split("<!-- evaluation:start -->", 1)[1].split("<!-- evaluation:end -->", 1)[0]
    outside = README.replace(block, "")
    audits = int(re.search(r"planted cases: (\d+)/\1 correct", block).group(1))  # type: ignore[union-attr]
    answers = int(re.search(r"whole answers: (\d+)/\1 correct", block).group(1))  # type: ignore[union-attr]
    passages = re.search(r"(\d+) passages\)", block).group(1)  # type: ignore[union-attr]
    categories = len(re.findall(r"^\| `[a-z_]+` \| \d+ \| (?:\d+/\d+|-) \|", block, re.M))
    caught = len(FAULTS) - len(NOT_EXERCISED)
    for expected in (
        f"**{audits:,} / {audits:,}** audits correct",
        f"**{answers} / {answers}** correct",
        f"{categories * 25} cases in {categories} categories",
        f"{passages} passages",
        f"{len(FAULTS)} hand-chosen faults",
        f"**{caught}** caught",
    ):
        assert expected in outside, expected
    design = (ROOT / "docs" / "DESIGN.md").read_text(encoding="utf-8")
    for expected in (
        f"{passages} passages",
        f"{categories} categories",
        f"broken in {len(FAULTS)} ways",
    ):
        assert expected in design, expected


def test_the_framework_badges_show_the_pinned_versions() -> None:
    import json
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    floors: dict[str, str] = {}
    for dependency in project["dependencies"]:
        name, _, floor = dependency.partition(">=")
        floors[name] = floor.split(",")[0]
    web = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    packages = {**web.get("dependencies", {}), **web.get("devDependencies", {})}
    for badge in (
        f"python-{project['requires-python'].removeprefix('>=')}-",
        f"MCP%20Python%20SDK-{floors['mcp']}-",
        f"Pydantic-{floors['pydantic'].split('.')[0]}-",
        f"React-{packages['react'].split('.')[0]}-",
        f"TypeScript-{packages['typescript'].split('.')[0]}-",
    ):
        assert f"https://img.shields.io/badge/{badge}" in README, badge


def test_the_name_is_capitalised_only_where_a_sentence_starts() -> None:
    prose = re.sub(r"```.*?```|`[^`]*`", "", README, flags=re.S)
    assert not re.search(r"(?:^|[.!?] |\*\* )tekmeria [a-z]", prose, re.M)
    assert "Tekmeria moves the guarantee into the tool server." in prose


def _section(title: str) -> str:
    return README.split(f"\n## {title}\n", 1)[1].split("\n## ", 1)[0]


def test_the_readme_says_what_the_software_is_before_anything_else() -> None:
    # Research READMEs open with what the software is, and put a quickstart near the top.
    overview = _section("Overview")
    first = next(line for line in overview.splitlines() if line and not line.startswith("<"))
    assert first.startswith("Tekmeria is ")
    assert "**Status.**" in overview and "It is meant for" in overview
    order = [README.index(f"\n## {title}\n") for title in ("Overview", "Quickstart", "Results")]
    assert order == sorted(order)
    assert "tekmeria demo --open" in _section("Quickstart")
    assert README[: order[1]].count("\n") < 80, "the quickstart belongs near the top"


def test_the_readme_says_how_to_report_a_problem_and_contribute() -> None:
    section = _section("Contributing and support")
    assert f"{REPO}/issues" in section
    assert "pull request" in section.lower()


def test_long_reference_material_is_folded_or_moved() -> None:
    assert "## Highlights" not in README and "## Low-level design" not in README
    assert f"{REPO}/blob/main/docs/DESIGN.md" in README
    evaluation = _section("Evaluation")
    start = evaluation.index("<!-- evaluation:start -->")
    assert "<details>" in evaluation[:start] and "</details>" in evaluation[start:]
    assert README.count("```mermaid") == 2  # the verdict order and the architecture


def test_the_security_policy_says_where_to_report_privately() -> None:
    policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert f"{REPO}/security/advisories/new" in policy
    assert f"{REPO}/blob/main/SECURITY.md" in _section("Contributing and support")
