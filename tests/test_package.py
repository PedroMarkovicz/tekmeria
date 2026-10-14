import subprocess
import tomllib
from pathlib import Path

import tekmeria


def test_version_and_contract_version() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert tekmeria.__version__ == pyproject["project"]["version"]
    assert tekmeria.CONTRACT_VERSION == "0.1.0"


def test_licence_files_ship_with_the_package() -> None:
    root = Path(__file__).resolve().parents[1]
    licence = (root / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in licence and "Version 2.0, January 2004" in licence
    assert "Copyright 2026 Pedro Markovicz" in (root / "NOTICE").read_text(encoding="utf-8")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["license"] == "Apache-2.0 AND CC-BY-4.0"
    assert project["project"]["license-files"] == ["LICENSE", "NOTICE"]
    sdist = project["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"]
    assert {"LICENSE", "NOTICE"} <= set(sdist)
    assert project["project"]["urls"]["Repository"] == "https://github.com/PedroMarkovicz/tekmeria"


# The project's earlier names. They may appear only in records of what happened under them.
OLD_NAMES = ("med" + "cite", "tekmer" + "ion")
HISTORY = ("docs/live-run.md",)
SPEC = "docs/specs/2026-10-06-tekmeria-server-design.md"
RENAME_SECTIONS = ("### 16.8 ", "### 16.21 ")  # the spec's records of the two renames


def _scanned_files(root: Path) -> list[str]:
    """Every tracked file outside the historical records."""
    listed = subprocess.run(
        ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    return [name for name in listed if not name.startswith(HISTORY)]


def _text_or_none(path: Path) -> str | None:
    """The file as text, or None for a binary file (an image, a database)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def test_the_earlier_name_appears_only_in_history() -> None:
    root = Path(__file__).resolve().parents[1]
    found: list[str] = []
    for name in _scanned_files(root):
        text = _text_or_none(root / name)
        if text is None or name == "tests/test_package.py":
            continue
        if name == SPEC:
            text = _without_sections(text, RENAME_SECTIONS)
        if any(old in text.lower() for old in OLD_NAMES):
            found.append(name)
    assert found == []


def _without_sections(text: str, headings: tuple[str, ...]) -> str:
    """The document without the sections that start at these headings."""
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        if line.startswith("#"):
            skipping = line.startswith(headings)
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def test_the_guard_drops_only_the_named_sections() -> None:
    lines = ["# a", "keep", "### 16.8 The name", "drop", "### 16.9 Next", "keep too"]
    text = "\n".join([*lines, "### 16.21 Again", "drop"])
    assert _without_sections(text, RENAME_SECTIONS) == "# a\nkeep\n### 16.9 Next\nkeep too"


def test_the_guard_reads_every_tracked_text_file(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    scanned = set(_scanned_files(root))
    for name in ("schemas/audit_report.schema.json", "web/scripts/gen-types.mjs", ".gitignore"):
        assert name in scanned, name
    binary = tmp_path / "image.bin"
    binary.write_bytes(bytes(range(256)))
    assert _text_or_none(binary) is None
