# Fix the rate limiter

`app/limiter.py` implements a token-bucket rate limiter. After a quiet period it lets a
burst of requests through that is larger than its capacity: a limiter with capacity 3 that
has been idle for a minute allows far more than 3 requests in a row.

Fix `RateLimiter.allow()` so that no more than `capacity` requests are ever allowed in a
burst, however long the limiter was idle. Requests that are refused must return `False`
with a short reason.
