from app.ratelimit import SlidingWindowLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_the_limit_then_refuses() -> None:
    limiter = SlidingWindowLimiter(clock=FakeClock())

    results = [limiter.hit("k", 3) for _ in range(4)]

    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results] == [2, 1, 0, 0]


def test_refused_requests_are_not_counted() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)
    limiter.hit("k", 1)
    for _ in range(10):
        limiter.hit("k", 1)

    clock.now += 60

    assert limiter.hit("k", 1).allowed


def test_window_slides() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)
    limiter.hit("k", 2)  # t=0
    clock.now += 30
    limiter.hit("k", 2)  # t=30

    clock.now += 29.9  # t=59.9: both still in the window
    assert not limiter.hit("k", 2).allowed
    clock.now += 0.2  # t=60.1: the first one has left
    assert limiter.hit("k", 2).allowed


def test_keys_are_counted_separately() -> None:
    limiter = SlidingWindowLimiter(clock=FakeClock())
    limiter.hit("a", 1)

    assert not limiter.hit("a", 1).allowed
    assert limiter.hit("b", 1).allowed


def test_headers() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)
    limiter.hit("k", 1)
    clock.now += 20.5

    refused = limiter.hit("k", 1)

    assert refused.headers() == {
        "X-RateLimit-Limit": "1",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": "40",
        "Retry-After": "40",
    }
    assert "Retry-After" not in limiter.hit("other", 5).headers()
