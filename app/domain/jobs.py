"""Background job types, statuses and the retry schedule."""

import random
from enum import StrEnum


class JobType(StrEnum):
    QUALIFY_LEAD = "qualify_lead"
    SYNC_LEAD = "sync_lead"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


def backoff_seconds(
    attempt: int,
    *,
    base: float,
    cap: float,
    rng: random.Random | None = None,
) -> float:
    """Delay before retry number `attempt` (1-based): exponential with "full jitter".

    The ceiling doubles each attempt (base, 2*base, 4*base, ...) up to `cap`, and the actual
    delay is random below it, so many failing jobs don't all retry at the same moment.
    """
    if attempt < 1:
        raise ValueError("attempt starts at 1")
    ceiling = min(cap, base * 2 ** (attempt - 1))
    return (rng or random).uniform(ceiling / 2, ceiling)
