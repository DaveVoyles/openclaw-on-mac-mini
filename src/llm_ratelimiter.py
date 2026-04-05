"""Backward-compatible async rate limiter interface."""

import asyncio
import time
from collections import deque


class RateLimiter:
    """Simple async sliding-window limiter used by legacy integration tests."""

    def __init__(self, max_requests: int = 60, time_window: int = 60):
        self.max_requests = max_requests
        self.time_window = time_window
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def check(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.time_window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.max_requests:
                return False
            self._timestamps.append(now)
            return True
