"""Webhook signing secrets: generated once, stored encrypted, decrypted to check signatures.

The encryption key is derived from WEBHOOK_SECRET_KEY with HKDF, so that setting can be any
long random string. Changing it makes every stored secret unreadable: endpoints must then be
created again.
"""

import base64
import secrets

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

GENERATED_PREFIX = "whsec_"
_HKDF_INFO = b"lead-qualification-api/webhook-secrets/v1"


class SecretKeyMissingError(Exception):
    """WEBHOOK_SECRET_KEY is not set."""


class SecretDecryptionError(Exception):
    """A stored secret can't be decrypted, usually because WEBHOOK_SECRET_KEY changed."""


def generate_secret() -> str:
    return GENERATED_PREFIX + secrets.token_urlsafe(32)


def encrypt_secret(secret: str, key: str) -> str:
    return _fernet(key).encrypt(secret.encode()).decode()


def decrypt_secret(token: str, key: str) -> str:
    try:
        return _fernet(key).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptionError("Stored webhook secret can't be decrypted.") from exc


def _fernet(key: str) -> Fernet:
    if not key:
        raise SecretKeyMissingError("WEBHOOK_SECRET_KEY is not set.")
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        key.encode()
    )
    return Fernet(base64.urlsafe_b64encode(derived))
