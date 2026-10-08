"""Handle key loading (spec section 4.4)."""

import contextlib
import os
import secrets
from pathlib import Path

from platformdirs import user_data_dir

from tekmerion.contract.handles import MIN_KEY_BYTES
from tekmerion.infra.settings import Settings

# Published on purpose: the offline demo must produce the same handles everywhere.
DEMO_KEY = b"tekmerion-demo-key-public-not-secret"


class HandleKeyError(ValueError):
    pass


def default_key_path() -> Path:
    return Path(user_data_dir("tekmerion", appauthor=False)) / "handle.key"


def _write_new(path: Path, data: bytes) -> None:
    """Create `path`, which must not exist, readable by its owner only (on POSIX)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as file:
        file.write(data)


def _create_key_file(path: Path) -> None:
    """Write a new random key unless one exists. The key goes to a temporary file that is
    linked into place only if the name is still free, so no process reads a half-written
    key, and a key another process created first is never replaced. Both files are created
    readable by their owner only: the key can mint handles."""
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(MIN_KEY_BYTES)
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    _write_new(temporary, key)
    try:
        os.link(temporary, path)
    except FileExistsError:
        pass  # another process won; its key is the one to use
    except OSError:  # no hard links on this drive: create the file exclusively instead
        with contextlib.suppress(FileExistsError):
            _write_new(path, key)
    finally:
        temporary.unlink()


def load_handle_key(settings: Settings, *, demo: bool, key_path: Path | None = None) -> bytes:
    """Demo key only in demo mode; otherwise env override, else a key file created on first run."""
    if demo:
        return DEMO_KEY
    override = settings.handle_key_hex.get_secret_value() if settings.handle_key_hex else ""
    if override:  # an empty variable (e.g. an unset CI secret) counts as unset
        try:
            key = bytes.fromhex(override)
        except ValueError as exc:
            raise HandleKeyError("TEKMERION_HANDLE_KEY must be hex") from exc
    else:
        path = key_path or default_key_path()
        if not path.exists():
            _create_key_file(path)
        key = path.read_bytes()
    if len(key) < MIN_KEY_BYTES:
        raise HandleKeyError(f"handle key must be at least {MIN_KEY_BYTES} bytes")
    if key == DEMO_KEY:
        raise HandleKeyError("the public demo key is only accepted with --demo")
    return key
