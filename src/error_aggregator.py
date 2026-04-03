"""
Error aggregation — deduplicates similar errors before posting to Discord.

Groups errors by a fingerprint (service + normalised error text) and posts
summaries instead of individual messages.  For example, 12 separate
"Sonarr returned 500" alerts collapse into a single line:
    ⚠️ Sonarr returned 500 (**12x** in last 60m)
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

log = logging.getLogger("openclaw")

# ---------------------------------------------------------------------------
# Fingerprint helpers
# ---------------------------------------------------------------------------

_RE_LONG_NUMBERS = re.compile(r"\d{2,}")
_RE_HEX_IDS = re.compile(r"[0-9a-f]{8,}", re.IGNORECASE)


def _fingerprint(service: str, error_msg: str) -> str:
    """Normalise an error message so similar errors share a fingerprint."""
    normalised = _RE_HEX_IDS.sub("ID", error_msg)   # hex IDs first
    normalised = _RE_LONG_NUMBERS.sub("N", normalised)
    return f"{service}:{normalised[:100]}"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ErrorBucket:
    """Tracks occurrences of a specific error type."""

    fingerprint: str
    first_message: str
    count: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.first_seen


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class ErrorAggregator:
    """Collect errors and deduplicate before posting.

    Usage::

        agg = ErrorAggregator(window_seconds=3600, flush_interval=300)
        await agg.record("sonarr", "HTTP 500 from /api/v3/health")
        # … later …
        lines = await agg.flush()   # summary of accumulated errors
    """

    def __init__(
        self,
        window_seconds: int = 3600,
        flush_interval: int = 300,
    ) -> None:
        self.window_seconds = window_seconds
        self.flush_interval = flush_interval
        self._buckets: dict[str, ErrorBucket] = {}
        self._lock = asyncio.Lock()

    # -- recording ----------------------------------------------------------

    async def record(self, service: str, error_msg: str) -> None:
        """Record a single error occurrence."""
        fp = _fingerprint(service, error_msg)
        async with self._lock:
            if fp in self._buckets:
                self._buckets[fp].count += 1
                self._buckets[fp].last_seen = time.time()
            else:
                self._buckets[fp] = ErrorBucket(
                    fingerprint=fp,
                    first_message=error_msg,
                    count=1,
                )

    # -- flushing -----------------------------------------------------------

    async def flush(self) -> list[str]:
        """Return summary lines for accumulated errors, then clear buckets."""
        async with self._lock:
            now = time.time()
            lines: list[str] = []

            for bucket in self._buckets.values():
                # Skip stale buckets that expired outside the window
                if now - bucket.last_seen > self.window_seconds:
                    continue

                if bucket.count == 1:
                    lines.append(f"⚠️ {bucket.first_message}")
                else:
                    age_min = int(bucket.age_seconds // 60) or 1
                    lines.append(
                        f"⚠️ {bucket.first_message} "
                        f"(**{bucket.count}x** in last {age_min}m)"
                    )

            self._buckets.clear()
            return lines

    # -- introspection ------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Total un-flushed error occurrences."""
        return sum(b.count for b in self._buckets.values())


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
error_aggregator = ErrorAggregator()
