import tomllib
from pathlib import Path

import medcite


def test_version_and_contract_version() -> None:
    assert medcite.__version__ == "0.1.0"
    assert medcite.CONTRACT_VERSION == "0.1.0"


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
    assert project["project"]["urls"]["Repository"] == "https://github.com/PedroMarkovicz/medcite"
