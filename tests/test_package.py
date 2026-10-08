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


def test_the_earlier_name_appears_only_in_history() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = root / "docs" / "specs" / "2026-10-06-tekmerion-server-design.md"
    found: list[str] = []
    for folder in ("src", "tests", "docs", "web/src", ".github"):
        for path in (root / folder).rglob("*"):
            relative = path.relative_to(root).as_posix()
            if not path.is_file() or relative.startswith(HISTORY) or "__pycache__" in relative:
                continue
            if path.suffix in {".png", ".pyc"} or relative.startswith("web/src/generated/"):
                continue
            text = path.read_text(encoding="utf-8")
            if path == spec:  # its section 16.8 records the rename
                text = text.split("### 16.8", 1)[0]
            if OLD_NAME in text.lower() and path != Path(__file__).resolve():
                found.append(relative)
    for name in ("README.md", "pyproject.toml", "NOTICE", "web/package.json", "web/vite.config.ts"):
        if OLD_NAME in (root / name).read_text(encoding="utf-8").lower():
            found.append(name)
    assert found == []
