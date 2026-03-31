"""Search provider abstraction — unified interface for all search backends."""
import time
import logging
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger("openclaw.search")


@dataclass
class SearchStats:
    """Tracks per-provider usage statistics."""
    provider: str
    total_calls: int = 0
    total_successes: int = 0
    total_failures: int = 0
    total_latency_ms: float = 0.0

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
            "success_rate": round(s.success_rate * 100, 1),
            "avg_latency_ms": round(s.avg_latency_ms, 1),
        }
        for name, s in _stats.items()
    }
