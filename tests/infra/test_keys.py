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


def test_a_key_another_process_wrote_first_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "handle.key"
    path.write_bytes(b"k" * 32)  # another process created the key...
    monkeypatch.setattr(Path, "exists", lambda self: False)  # ...after this one looked
    assert load_handle_key(Settings(), demo=False, key_path=path) == b"k" * 32
    assert path.read_bytes() == b"k" * 32
    assert [p.name for p in tmp_path.iterdir()] == ["handle.key"]  # no temporary file left


def test_settings_never_come_from_a_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("MEDCITE_HANDLE_KEY=" + "ab" * 32 + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEDCITE_HANDLE_KEY", raising=False)
    assert Settings().handle_key_hex is None


def test_a_key_is_created_where_hard_links_are_not_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_links(source: object, target: object) -> None:
        raise OSError("hard links are not supported on this drive")

    monkeypatch.setattr("os.link", no_links)
    path = tmp_path / "handle.key"
    key = load_handle_key(Settings(), demo=False, key_path=path)
    assert len(key) == 32 and path.read_bytes() == key
    assert [p.name for p in tmp_path.iterdir()] == ["handle.key"]
