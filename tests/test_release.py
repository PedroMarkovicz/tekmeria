"""The release metadata: CITATION.cff and the changelog agree with the package."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _citation(field: str) -> str:
    match = re.search(
        rf"^{field}: (.+)$", (ROOT / "CITATION.cff").read_text(encoding="utf-8"), re.M
    )
    assert match, field
    return match.group(1).strip().strip('"')


def _orcid_is_valid(orcid: str) -> bool:
    """ISO 7064 MOD 11-2, the check character of an ORCID iD."""
    digits = orcid.removeprefix("https://orcid.org/").replace("-", "")
    total = 0
    for char in digits[:15]:
        total = (total + int(char)) * 2
    check = (12 - total % 11) % 11
    return len(digits) == 16 and digits[15] == ("X" if check == 10 else str(check))


def test_citation_metadata_matches_the_package() -> None:
    assert _citation("cff-version") == "1.2.0"
    assert _citation("title") == PROJECT["name"]
    assert _citation("version") == PROJECT["version"]
    assert _citation("license") == PROJECT["license"]
    assert _citation("repository-code") == PROJECT["urls"]["Repository"]


def test_the_author_orcid_is_well_formed() -> None:
    text = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    (orcid,) = re.findall(r'orcid: "(https://orcid\.org/[0-9X-]+)"', text)
    assert _orcid_is_valid(orcid), orcid
    assert not _orcid_is_valid(orcid[:-1] + ("0" if orcid[-1] != "0" else "1"))


def test_the_changelog_opens_with_the_package_version() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.findall(r"^## (\S+)", text, re.M)[0] == PROJECT["version"]
