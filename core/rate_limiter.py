"""Rate limiter — sliding-window counters for API rate limiting.

Two backends:

- ``InMemoryRateLimiter`` — per-process in-memory deques (default, dev).
- ``RedisRateLimiter`` — Redis sorted sets (production).

Usage::

    limiter = get_rate_limiter()
    allowed, retry_after = await limiter.check_and_increment("user:xxx:tasks", 60, 60)
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque

from core.config import settings


# ======================================================================
# Abstract
# ======================================================================


class RateLimiter(ABC):
    @abstractmethod
    async def check_and_increment(
        self, key: str, max_requests: int, window_seconds: int = 60
    ) -> tuple[bool, int]:
        """Check and record a request.

        Returns ``(allowed, retry_after_seconds)`` where *retry_after* is 0
        when the request is allowed, or the number of seconds to wait before
        retrying when denied.
        """
        ...


# ======================================================================
# In-memory (sliding window via deque)
# ======================================================================


class InMemoryRateLimiter(RateLimiter):
    """Per-process sliding-window rate limiter.

    Uses a dict-of-deques storing epoch-second timestamps.  Thread-safe
    within an async process because there is no actual concurrency.
    """

    def __init__(self):
        self._windows: dict[str, deque[float]] = defaultdict(deque)

    async def check_and_increment(
        self, key: str, max_requests: int, window_seconds: int = 60
    ) -> tuple[bool, int]:
        now = time.time()
        cutoff = now - window_seconds
        dq = self._windows[key]

        # Evict expired entries
        while dq and dq[0] < cutoff:
            dq.popleft()

        if len(dq) >= max_requests:
            # How long until the oldest request in the window expires?
            retry_after = int(dq[0] - cutoff)
            return False, max(retry_after, 1)

        dq.append(now)
        return True, 0


# ======================================================================
# Redis-backed (sorted set)
# ======================================================================


class RedisRateLimiter(RateLimiter):
    """Distributed sliding-window rate limiter backed by a Redis sorted set.

    Each key is a Redis sorted set whose members are timestamps.  Expired
    members are removed via ``ZREMRANGEBYSCORE`` and the remaining count is
    checked via ``ZCARD``.  The set is given a TTL via ``EXPIRE`` so it
    cleans up automatically.
    """

    def __init__(self):
        import redis.asyncio as aioredis

        self._redis: aioredis.Redis | None = None
        self._redis_url = settings.redis_url

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def check_and_increment(
        self, key: str, max_requests: int, window_seconds: int = 60
    ) -> tuple[bool, int]:
        r = await self._get_redis()
        now = time.time()
        cutoff = now - window_seconds
        redis_key = f"ratelimit:{key}"

        # Remove expired entries
        await r.zremrangebyscore(redis_key, "-inf", cutoff)

        # Count remaining
        count = await r.zcard(redis_key)

        if count >= max_requests:
            # Get the oldest entry's score to compute retry-after
            oldest = await r.zrange(redis_key, 0, 0, withscores=True)
            if oldest:
                retry_after = int(oldest[0][1] - cutoff)
            else:
                retry_after = window_seconds
            return False, max(retry_after, 1)

        # Record this request
        await r.zadd(redis_key, {str(now): now})
        await r.expire(redis_key, window_seconds * 2)
        return True, 0

    async def close(self):
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


# ======================================================================
# Factory
# ======================================================================

_limiter_instance: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter_instance
    if _limiter_instance is None:
        backend = settings.rate_limit_backend
        if backend == "redis":
            _limiter_instance = RedisRateLimiter()
        else:
            _limiter_instance = InMemoryRateLimiter()
    return _limiter_instance


def reset_rate_limiter():
    global _limiter_instance
    if isinstance(_limiter_instance, RedisRateLimiter):
        import asyncio
        try:
            asyncio.create_task(_limiter_instance.close())
        except Exception:
            pass
    _limiter_instance = None
