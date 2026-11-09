"""API key format, generation and verification.

A key looks like `lq_live_<prefix>_<secret>`. The prefix is stored in plain text and used to
find the key; only an HMAC-SHA256 of the whole key (with a server-side pepper) is stored.
API keys are long random secrets, so a fast keyed hash is enough and keeps auth cheap.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

KEY_NAMESPACE = "lq_live"
_PREFIX_RE = re.compile(r"^[a-z0-9]{8}$")


@dataclass(frozen=True)
class GeneratedKey:
    plain: str
    prefix: str
    key_hash: str


def hash_key(plain: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), plain.encode(), hashlib.sha256).hexdigest()


def generate_key(pepper: str) -> GeneratedKey:
    prefix = secrets.token_hex(4)
    plain = f"{KEY_NAMESPACE}_{prefix}_{secrets.token_urlsafe(32)}"
    return GeneratedKey(plain=plain, prefix=prefix, key_hash=hash_key(plain, pepper))


def parse_prefix(plain: str) -> str | None:
    """Return the lookup prefix, or None if the key is not in our format."""
    parts = plain.split("_", 3)
    if len(parts) != 4 or f"{parts[0]}_{parts[1]}" != KEY_NAMESPACE:
        return None
    prefix, secret = parts[2], parts[3]
    if not _PREFIX_RE.match(prefix) or len(secret) < 32:
        return None
    return prefix


def verify_key(plain: str, pepper: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_key(plain, pepper), key_hash)
