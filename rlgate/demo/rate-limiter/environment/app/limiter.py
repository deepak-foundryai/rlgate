"""A token-bucket rate limiter."""

import time


class RateLimiter:
    def __init__(self, capacity: int, refill_per_s: float, clock=time.monotonic):
        self.capacity = capacity
        self.refill_per_s = refill_per_s
        self.clock = clock
        self.tokens = float(capacity)
        self.last = clock()

    def allow(self) -> tuple[bool, str]:
        now = self.clock()
        self.tokens += (now - self.last) * self.refill_per_s
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True, "ok"
        return False, "denied"
