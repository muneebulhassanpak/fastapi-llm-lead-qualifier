import random

import pytest

from app.domain.jobs import backoff_seconds


@pytest.mark.parametrize(("attempt", "ceiling"), [(1, 5), (2, 10), (3, 20), (4, 40), (5, 80)])
def test_ceiling_doubles_each_attempt(attempt: int, ceiling: float) -> None:
    rng = random.Random(1)

    delays = [backoff_seconds(attempt, base=5, cap=1000, rng=rng) for _ in range(200)]

    assert all(ceiling / 2 <= d <= ceiling for d in delays)


def test_delay_never_exceeds_cap() -> None:
    assert backoff_seconds(30, base=5, cap=60, rng=random.Random(1)) <= 60


def test_jitter_spreads_retries() -> None:
    rng = random.Random(1)

    assert len({backoff_seconds(3, base=5, cap=600, rng=rng) for _ in range(20)}) > 1


def test_attempt_starts_at_one() -> None:
    with pytest.raises(ValueError, match="attempt starts at 1"):
        backoff_seconds(0, base=5, cap=60)
