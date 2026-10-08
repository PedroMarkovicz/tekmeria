"""Opaque, signed pagination cursors: the server keeps no paging state (spec 6.1)."""

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping

from pydantic import JsonValue

from tekmerion.contract.handles import HandleCodec
from tekmerion.sources.errors import InvalidInputError

_HINT = "Repeat the call with the same arguments and the next_cursor from the previous result."


def _args_digest(args: Mapping[str, object]) -> str:
    canonical = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def encode_cursor(
    codec: HandleCodec, tool: str, args: Mapping[str, object], state: JsonValue
) -> str:
    """Sign `state` (an offset, or per-source positions) together with the tool and arguments."""
    payload = json.dumps({"t": tool, "a": _args_digest(args), "o": state}, separators=(",", ":"))
    body = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{body}.{codec.sign('cursor', payload.encode('utf-8'))}"


def decode_cursor(
    codec: HandleCodec, tool: str, args: Mapping[str, object], cursor: str
) -> JsonValue:
    """Return the signed state, or raise InvalidInputError if tampered with or reused."""
    body, _, signature = cursor.partition(".")
    try:
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        data = json.loads(payload)
    except (binascii.Error, ValueError) as exc:
        raise InvalidInputError("cursor is not valid", hint=_HINT) from exc
    if not hmac.compare_digest(signature, codec.sign("cursor", payload)):
        raise InvalidInputError("cursor signature does not match", hint=_HINT)
    if not isinstance(data, dict) or data.get("t") != tool or data.get("a") != _args_digest(args):
        raise InvalidInputError("cursor belongs to a different query", hint=_HINT)
    return data.get("o")


def decode_offset(codec: HandleCodec, tool: str, args: Mapping[str, object], cursor: str) -> int:
    """A cursor whose state is a plain non-negative offset."""
    offset = decode_cursor(codec, tool, args, cursor)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise InvalidInputError("cursor offset is not valid", hint=_HINT)
    return offset
