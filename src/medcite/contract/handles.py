"""Handle grammar, locators and the check code (spec section 4)."""

import hashlib
import hmac
import re
from dataclasses import dataclass

CROCKFORD_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
SCHEMES = ("pmid", "pmcid", "ppr", "nct", "setid")

_RECORD = r"(pmid|pmcid|ppr|nct|setid):([A-Za-z0-9-]+)(?:@([A-Za-z0-9.-]+))?"
_PATH = r"([a-z0-9_]+(?:\.[a-z0-9_]+)*)"
HANDLE_PATTERN = rf"^{_RECORD}/{_PATH}~([0-9a-hjkmnp-tv-z]{{5}})$"
HANDLE_RE = re.compile(HANDLE_PATTERN)
RECORD_ID_RE = re.compile(rf"^{_RECORD}$")
PATH_RE = re.compile(rf"^{_PATH}$")
MIN_KEY_BYTES = 32


class MalformedHandleError(ValueError):
    """The text does not match the handle grammar."""


class ForgedHandleError(ValueError):
    """The handle is well formed but its check code was not issued by this key."""


@dataclass(frozen=True, order=True)
class RecordRef:
    scheme: str
    id: str
    version: str | None = None

    @property
    def key(self) -> str:
        """Scheme and id without version, e.g. 'pmid:38421676'."""
        return f"{self.scheme}:{self.id}"

    def __str__(self) -> str:
        return self.key if self.version is None else f"{self.key}@{self.version}"

    def at(self, path: str) -> "Locator":
        if not PATH_RE.match(path):
            raise ValueError(f"invalid passage path: {path!r}")
        return Locator(self.scheme, self.id, self.version, path)


@dataclass(frozen=True, order=True)
class Locator:
    scheme: str
    id: str
    version: str | None
    path: str

    @property
    def record(self) -> RecordRef:
        return RecordRef(self.scheme, self.id, self.version)

    def __str__(self) -> str:
        return f"{self.record}/{self.path}"


def parse_record_id(text: str) -> RecordRef:
    match = RECORD_ID_RE.match(text)
    if not match:
        raise ValueError(f"invalid record id: {text!r}")
    scheme, ident, version = match.groups()
    return RecordRef(scheme, ident, version)


def parse_handle(text: str) -> tuple[Locator, str]:
    """Split a handle into its locator and check code, or raise MalformedHandleError."""
    match = HANDLE_RE.match(text)
    if not match:
        raise MalformedHandleError(text)
    scheme, ident, version, path, check = match.groups()
    return Locator(scheme, ident, version, path), check


class HandleCodec:
    """Issues and verifies handles with a per-installation key."""

    def __init__(self, key: bytes) -> None:
        if len(key) < MIN_KEY_BYTES:
            raise ValueError(f"handle key must be at least {MIN_KEY_BYTES} bytes")
        self._key = key

    def check_code(self, locator: Locator) -> str:
        digest = hmac.new(self._key, str(locator).encode("utf-8"), hashlib.sha256).digest()
        bits = int.from_bytes(digest[:4], "big") >> 7  # first 25 bits
        return "".join(CROCKFORD_ALPHABET[(bits >> shift) & 31] for shift in (20, 15, 10, 5, 0))

    def issue(self, locator: Locator) -> str:
        return f"{locator}~{self.check_code(locator)}"

    def verify(self, handle: str) -> Locator:
        """Return the locator of a genuine handle; raise Malformed/ForgedHandleError otherwise."""
        locator, check = parse_handle(handle)
        if not hmac.compare_digest(check, self.check_code(locator)):
            raise ForgedHandleError(handle)
        return locator

    def sign(self, purpose: str, payload: bytes) -> str:
        """Hex HMAC for non-handle data (e.g. cursors), domain-separated by purpose."""
        message = purpose.encode("utf-8") + b"|" + payload
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()[:32]
