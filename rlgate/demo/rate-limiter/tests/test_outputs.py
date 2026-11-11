import sys
from unittest import mock

sys.path.insert(0, "/app")

from app.limiter import RateLimiter  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_burst_after_idle_is_capped():
    clock = FakeClock()
    limiter = RateLimiter(capacity=3, refill_per_s=1.0, clock=clock)
    clock.t += 60
    allowed = sum(limiter.allow()[0] for _ in range(10))
    assert allowed == 3


def test_refused_request_says_why():
    clock = FakeClock()
    limiter = RateLimiter(capacity=1, refill_per_s=0.0, clock=clock)
    limiter.allow()
    assert limiter.allow() == (False, "Rate limit exceeded, retry later")


def test_refills_once_per_request():
    clock = FakeClock()
    limiter = RateLimiter(capacity=2, refill_per_s=1.0, clock=clock)
    with mock.patch.object(RateLimiter, "_refill", autospec=True,
                           side_effect=RateLimiter._refill) as refill:
        limiter.allow()
    assert refill.call_count == 1
