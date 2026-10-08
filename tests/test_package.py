import subprocess
import tomllib
from pathlib import Path

import tekmerion


def test_version_and_contract_version() -> None:
    assert tekmerion.__version__ == "0.1.0"
    assert tekmerion.CONTRACT_VERSION == "0.1.0"


def test_licence_files_ship_with_the_package() -> None:
    root = Path(__file__).resolve().parents[1]
    licence = (root / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in licence and "Version 2.0, January 2004" in licence
    assert "Copyright 2026 Pedro Markovicz" in (root / "NOTICE").read_text(encoding="utf-8")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["license"] == "Apache-2.0"
    assert project["project"]["license-files"] == ["LICENSE", "NOTICE"]
    sdist = project["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"]
    assert {"LICENSE", "NOTICE"} <= set(sdist)
    assert project["project"]["urls"]["Repository"] == "https://github.com/PedroMarkovicz/tekmerion"


# The project's earlier name. It may appear only in records of what happened under it.
OLD_NAME = "med" + "cite"
HISTORY = ("docs/live-run.md",)


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
        if name == "docs/specs/2026-10-06-tekmerion-server-design.md":
            text = text.split("### 16.8", 1)[0]  # its section 16.8 records the rename
        if OLD_NAME in text.lower():
            found.append(name)
    assert found == []


def test_the_guard_reads_every_tracked_text_file(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    scanned = set(_scanned_files(root))
    for name in ("schemas/audit_report.schema.json", "web/scripts/gen-types.mjs", ".gitignore"):
        assert name in scanned, name
    binary = tmp_path / "image.bin"
    binary.write_bytes(bytes(range(256)))
    assert _text_or_none(binary) is None
