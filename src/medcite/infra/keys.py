"""Handle key loading (spec section 4.4)."""

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
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(secrets.token_bytes(MIN_KEY_BYTES))
            if os.name == "posix":
                path.chmod(0o600)
        key = path.read_bytes()
    if len(key) < MIN_KEY_BYTES:
        raise HandleKeyError(f"handle key must be at least {MIN_KEY_BYTES} bytes")
    if key == DEMO_KEY:
        raise HandleKeyError("the public demo key is only accepted with --demo")
    return key
