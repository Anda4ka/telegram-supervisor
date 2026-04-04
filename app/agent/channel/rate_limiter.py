"""Async rate limiter using token bucket algorithm.

Provides per-source-type rate limiting for external API calls
to prevent 429 errors from Brave Search, Nitter, Reddit, etc.
"""

from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    """Token bucket rate limiter for async contexts.

    Args:
        calls_per_second: Sustained rate of allowed calls.
        burst: Maximum burst size (tokens in bucket at any time).
    """

    def __init__(self, calls_per_second: float, burst: int = 1) -> None:
        self._rate = calls_per_second
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                self._last_refill = now

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return

                wait_time = (1.0 - self._tokens) / self._rate

            # Sleep OUTSIDE lock so other coroutines can check/acquire
            await asyncio.sleep(wait_time)

    async def __aenter__(self) -> AsyncRateLimiter:
        await self.acquire()
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


# Singleton rate limiters for external APIs
brave_limiter = AsyncRateLimiter(calls_per_second=1.0, burst=2)
twitter_limiter = AsyncRateLimiter(calls_per_second=2.0, burst=3)
reddit_limiter = AsyncRateLimiter(calls_per_second=1.0, burst=2)
