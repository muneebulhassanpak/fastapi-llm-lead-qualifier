"""Signature schemes, each computed over the exact raw request body.

- generic: `X-Timestamp: <unix seconds>` and `X-Signature: sha256=<hex>`, where the hex is
  HMAC-SHA256 of `"{timestamp}.{body}"`. Signing the timestamp lets us reject old deliveries
  (replays) and ones dated in the future.
- tally: `Tally-Signature: <base64>`, HMAC-SHA256 of the body, as Tally sends it. Tally signs
  no timestamp, so replays are stopped by deduplicating on its event ID instead.
"""

import base64
import hashlib
import hmac

GENERIC_TOLERANCE_SECONDS = 300


def sign_generic(secret: str, timestamp: int, body: bytes) -> str:
    message = str(timestamp).encode() + b"." + body
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_generic(
    secret: str,
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    *,
    now: float,
    tolerance: int = GENERIC_TOLERANCE_SECONDS,
) -> bool:
    if not timestamp_header or not signature_header or not timestamp_header.isdigit():
        return False
    timestamp = int(timestamp_header)
    if abs(now - timestamp) > tolerance:
        return False
    expected = sign_generic(secret, timestamp, body)
    return hmac.compare_digest(expected.encode(), signature_header.encode())


def sign_tally(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_tally(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    return hmac.compare_digest(sign_tally(secret, body).encode(), signature_header.encode())
