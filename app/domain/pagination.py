"""Opaque cursors for keyset pagination. Callers must treat a cursor as a black box."""

import base64
import binascii
import re

_LEAD_ID_RE = re.compile(r"^ld_[0-9A-Z]{26}$")


class InvalidCursorError(ValueError):
    pass


def encode_cursor(last_id: str) -> str:
    return base64.urlsafe_b64encode(last_id.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = base64.urlsafe_b64decode(padded.encode()).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursorError(cursor) from exc
    if not _LEAD_ID_RE.match(value):
        raise InvalidCursorError(cursor)
    return value
