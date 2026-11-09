import pytest

from app.webhooks.secrets import (
    SecretDecryptionError,
    SecretKeyMissingError,
    decrypt_secret,
    encrypt_secret,
    generate_secret,
)


def test_secret_round_trips_and_is_not_stored_in_clear() -> None:
    secret = generate_secret()

    token = encrypt_secret(secret, "server-key")

    assert secret.startswith("whsec_")
    assert secret not in token
    assert decrypt_secret(token, "server-key") == secret


def test_generated_secrets_differ() -> None:
    assert generate_secret() != generate_secret()


def test_wrong_server_key_cannot_decrypt() -> None:
    token = encrypt_secret(generate_secret(), "server-key")

    with pytest.raises(SecretDecryptionError):
        decrypt_secret(token, "another-key")


def test_missing_server_key_is_an_error() -> None:
    with pytest.raises(SecretKeyMissingError):
        encrypt_secret("whsec_x", "")
