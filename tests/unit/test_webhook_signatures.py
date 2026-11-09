import base64
import hashlib
import hmac

import pytest

from app.webhooks.signatures import sign_generic, sign_tally, verify_generic, verify_tally

SECRET = "whsec_test"
BODY = b'{"event_id":"evt_1","lead":{"message":"Hi"}}'
NOW = 1_800_000_000


def test_generic_signature_matches_the_documented_scheme() -> None:
    expected = hmac.new(SECRET.encode(), b"1800000000." + BODY, hashlib.sha256).hexdigest()

    assert sign_generic(SECRET, NOW, BODY) == f"sha256={expected}"


def test_generic_valid_signature() -> None:
    signature = sign_generic(SECRET, NOW, BODY)

    assert verify_generic(SECRET, BODY, str(NOW), signature, now=NOW + 10)


@pytest.mark.parametrize(
    ("body", "timestamp", "signature", "now"),
    [
        (BODY, str(NOW), sign_generic("whsec_other", NOW, BODY), NOW),  # wrong secret
        (BODY + b" ", str(NOW), sign_generic(SECRET, NOW, BODY), NOW),  # body changed
        (BODY, str(NOW + 1), sign_generic(SECRET, NOW, BODY), NOW),  # timestamp changed
        (BODY, str(NOW), sign_generic(SECRET, NOW, BODY), NOW + 301),  # too old
        (BODY, str(NOW), sign_generic(SECRET, NOW, BODY), NOW - 301),  # from the future
        (BODY, None, sign_generic(SECRET, NOW, BODY), NOW),  # no timestamp
        (BODY, "soon", sign_generic(SECRET, NOW, BODY), NOW),  # not a number
        (BODY, str(NOW), None, NOW),  # no signature
        (BODY, str(NOW), "sha256=" + "0" * 64, NOW),  # made up
    ],
)
def test_generic_rejects(
    body: bytes, timestamp: str | None, signature: str | None, now: int
) -> None:
    assert not verify_generic(SECRET, body, timestamp, signature, now=now)


def test_tally_signature_is_base64_hmac_of_the_body() -> None:
    expected = base64.b64encode(hmac.new(SECRET.encode(), BODY, hashlib.sha256).digest()).decode()

    assert sign_tally(SECRET, BODY) == expected
    assert verify_tally(SECRET, BODY, expected)


@pytest.mark.parametrize(
    ("body", "signature"),
    [
        (BODY, sign_tally("whsec_other", BODY)),
        (BODY + b" ", sign_tally(SECRET, BODY)),
        (BODY, None),
        (BODY, ""),
    ],
)
def test_tally_rejects(body: bytes, signature: str | None) -> None:
    assert not verify_tally(SECRET, body, signature)
