"""
OpenClaw LLM Rate Limiter — sliding-window rate limiter with jittered backoff.
"""

import asyncio
import logging
import random
import threading
import time
from collections import deque

from config import cfg

log = logging.getLogger("openclaw.llm.ratelimit")

MAX_CALLS_PER_MINUTE = cfg.llm_rpm_limit
MAX_CALLS_PER_HOUR = cfg.llm_rph_limit


class RateLimiter:
    """Sliding-window rate limiter with jittered backoff for concurrent callers."""

    def __init__(self, per_minute: int = MAX_CALLS_PER_MINUTE, per_hour: int = MAX_CALLS_PER_HOUR):
        self._per_minute = per_minute
        self._per_hour = per_hour
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    def _evict(self) -> None:
        """Drop timestamps older than 1 hour from the front of the deque."""
        cutoff = time.monotonic() - 3600
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def check(self) -> bool:
        """Return True if a call is allowed right now (thread-safe)."""
        with self._sync_lock:
            self._evict()
            now = time.monotonic()
            minute_count = sum(1 for t in self._timestamps if now - t < 60)
            hour_count = len(self._timestamps)
            return minute_count < self._per_minute and hour_count < self._per_hour

    def record(self):
        """Record a call (thread-safe)."""
        with self._sync_lock:
            self._timestamps.append(time.monotonic())

    async def wait_for_capacity(self, max_wait: float = 30.0) -> bool:
        """Wait with jittered exponential backoff until capacity is available.

        Returns True if capacity was acquired, False if max_wait exceeded.
        Uses a lock to prevent thundering-herd: only one caller backs off at a time.
        """
        backoff = 1.0
        waited = 0.0
        async with self._lock:
            while not self.check():
                if waited >= max_wait:
                    return False
                jitter = random.uniform(0.8, 1.2)
                sleep_time = min(backoff * jitter, max_wait - waited)
                log.info("Rate limiter: backing off %.1fs (waited %.1fs)", sleep_time, waited)
                await asyncio.sleep(sleep_time)
                waited += sleep_time
                backoff = min(backoff * 2, 15.0)  # cap at 15s
        return True

    @property
    def remaining_minute(self) -> int:
        now = time.monotonic()
        used = sum(1 for t in self._timestamps if now - t < 60)
        return max(0, self._per_minute - used)

    @property
    def remaining_hour(self) -> int:
        self._evict()
        return max(0, self._per_hour - len(self._timestamps))


rate_limiter = RateLimiter()
