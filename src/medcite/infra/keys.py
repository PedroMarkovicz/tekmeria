"""Handle key loading (spec section 4.4)."""

import contextlib
import os
import secrets
from pathlib import Path

from platformdirs import user_data_dir

from medcite.contract.handles import MIN_KEY_BYTES
from medcite.infra.settings import Settings

# Published on purpose: the offline demo must produce the same handles everywhere.
DEMO_KEY = b"medcite-demo-key-public-not-secret"


class HandleKeyError(ValueError):
    pass


def default_key_path() -> Path:
    return Path(user_data_dir("medcite", appauthor=False)) / "handle.key"


def _create_key_file(path: Path) -> None:
    """Write a new random key unless one exists. The key goes to a temporary file that is
    linked into place only if the name is still free, so no process reads a half-written
    key, and a key another process created first is never replaced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    temporary.write_bytes(secrets.token_bytes(MIN_KEY_BYTES))
    if os.name == "posix":
        temporary.chmod(0o600)
    try:
        os.link(temporary, path)
    except FileExistsError:
        pass  # another process won; its key is the one to use
    except OSError:  # no hard links on this drive: create the file exclusively instead
        with contextlib.suppress(FileExistsError), path.open("xb") as file:
            file.write(temporary.read_bytes())
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
            raise HandleKeyError("MEDCITE_HANDLE_KEY must be hex") from exc
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
