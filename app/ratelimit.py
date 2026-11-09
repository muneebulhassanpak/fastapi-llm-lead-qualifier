"""Per-key request limits, counted in memory over a sliding one-minute window.

Each process counts on its own, so with N instances a key gets up to N times its limit.
Running more than one instance would mean moving the counters to Redis or Postgres.
"""

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_after: float  # seconds until the oldest counted request leaves the window

    def headers(self) -> dict[str, str]:
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(math.ceil(self.reset_after)),
        }
        if not self.allowed:
            headers["Retry-After"] = str(max(1, math.ceil(self.reset_after)))
        return headers


class SlidingWindowLimiter:
    """Remembers the time of each allowed request per key, for the length of the window.

    Exact (no burst at window edges, unlike fixed windows), and cheap enough at these limits:
    at most `limit` timestamps are kept per key.
    """

    def __init__(
        self, window: float = WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str, limit: int) -> RateLimitResult:
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        allowed = len(hits) < limit
        if allowed:
            hits.append(now)
        reset_after = hits[0] + self._window - now if hits else 0.0
        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            remaining=max(0, limit - len(hits)),
            reset_after=reset_after,
        )
