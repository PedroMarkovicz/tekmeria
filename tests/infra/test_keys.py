from pathlib import Path

import pytest

from medcite.infra.keys import DEMO_KEY, HandleKeyError, load_handle_key
from medcite.infra.settings import Settings


def test_demo_mode_uses_public_key(tmp_path: Path) -> None:
    assert load_handle_key(Settings(), demo=True, key_path=tmp_path / "k") == DEMO_KEY


def test_creates_key_file_once(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "handle.key"
    first = load_handle_key(Settings(), demo=False, key_path=path)
    second = load_handle_key(Settings(), demo=False, key_path=path)
    assert len(first) == 32 and first == second and path.read_bytes() == first


def test_env_override_wins(tmp_path: Path) -> None:
    settings = Settings.model_validate({"MEDCITE_HANDLE_KEY": "ab" * 32})
    assert load_handle_key(settings, demo=False, key_path=tmp_path / "k") == bytes([0xAB]) * 32


def test_empty_env_override_counts_as_unset(tmp_path: Path) -> None:
    settings = Settings.model_validate({"MEDCITE_HANDLE_KEY": ""})
    key = load_handle_key(settings, demo=False, key_path=tmp_path / "k")
    assert key == (tmp_path / "k").read_bytes()


@pytest.mark.parametrize("value", ["zz", "ab" * 8, DEMO_KEY.hex()])
def test_rejects_bad_or_demo_key_outside_demo(tmp_path: Path, value: str) -> None:
    settings = Settings.model_validate({"MEDCITE_HANDLE_KEY": value})
    with pytest.raises(HandleKeyError):
        load_handle_key(settings, demo=False, key_path=tmp_path / "k")
