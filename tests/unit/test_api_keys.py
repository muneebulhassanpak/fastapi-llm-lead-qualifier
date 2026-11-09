import pytest

from app.domain.api_keys import generate_key, hash_key, parse_prefix, verify_key


def test_generated_key_round_trips() -> None:
    key = generate_key("pepper")

    assert key.plain.startswith(f"lq_live_{key.prefix}_")
    assert parse_prefix(key.plain) == key.prefix
    assert verify_key(key.plain, "pepper", key.key_hash)


def test_hash_is_not_the_key() -> None:
    key = generate_key("pepper")

    assert key.plain not in key.key_hash
    assert key.key_hash == hash_key(key.plain, "pepper")


def test_wrong_pepper_fails_verification() -> None:
    key = generate_key("pepper")

    assert not verify_key(key.plain, "other-pepper", key.key_hash)


def test_keys_are_unique() -> None:
    assert len({generate_key("p").plain for _ in range(50)}) == 50


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "lq_live_",
        "lq_test_ab12cd34_" + "x" * 43,
        "lq_live_AB12CD34_" + "x" * 43,
        "lq_live_ab12cd3_" + "x" * 43,
        "lq_live_ab12cd34_short",
        "Bearer something",
    ],
)
def test_malformed_keys_have_no_prefix(bad: str) -> None:
    assert parse_prefix(bad) is None
