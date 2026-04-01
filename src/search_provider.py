"""Search provider abstraction — unified interface for all search backends."""
import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Callable, Coroutine, Any

import aiohttp

log = logging.getLogger("openclaw.search")


@dataclass
class SearchStats:
    """Tracks per-provider usage statistics."""
    provider: str
    total_calls: int = 0
    total_successes: int = 0
    total_failures: int = 0
    total_latency_ms: float = 0.0
    total_retries: int = 0

    @property
    def success_rate(self) -> float:
        return self.total_successes / max(self.total_calls, 1)

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(self.total_successes, 1)

    def record_success(self, latency_ms: float):
        self.total_calls += 1
        self.total_successes += 1
        self.total_latency_ms += latency_ms

    def record_failure(self):
        self.total_calls += 1
        self.total_failures += 1

    def record_retry(self):
        self.total_retries += 1


# Global stats registry
_stats: dict[str, SearchStats] = {}


def get_stats(provider: str) -> SearchStats:
    if provider not in _stats:
        _stats[provider] = SearchStats(provider=provider)
    return _stats[provider]


def all_stats() -> dict[str, dict]:
    """Return all provider stats as serializable dict."""
    return {
        name: {
            "calls": s.total_calls,
            "successes": s.total_successes,
            "failures": s.total_failures,
            "retries": s.total_retries,
            "success_rate": round(s.success_rate * 100, 1),
            "avg_latency_ms": round(s.avg_latency_ms, 1),
        }
        for name, s in _stats.items()
    }


# ---------------------------------------------------------------------------
# Transient-error retry helper
# ---------------------------------------------------------------------------

_TRANSIENT_HTTP_CODES = frozenset(range(500, 600))


async def retry_once(
    coro_factory: Callable[[], Coroutine[Any, Any, Any]],
    provider_name: str,
) -> Any:
    """Execute *coro_factory()*, retry **once** after 1 s on transient errors.

    Transient errors: ``aiohttp.ClientError``, ``asyncio.TimeoutError``,
    or an ``aiohttp.ClientResponseError`` with a 5xx status.
    """
    try:
        return await coro_factory()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        stats = get_stats(provider_name)
        stats.record_retry()
        log.info("Retrying %s after transient error: %s", provider_name, exc)
        await asyncio.sleep(1)
        return await coro_factory()
