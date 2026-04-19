"""
OpenClaw Tool Health — Circuit breaker + failure tracking for external tools.

Provides:
  - CircuitBreaker: per-tool failure counting with cooldown
  - ToolHealthTracker: persistent success/failure rates for adaptive routing

Usage::

    from tool_health import circuit_breaker, tool_health

    # Before calling an external tool:
    if circuit_breaker.is_open("search_web"):
        return "⚠️ search_web is temporarily unavailable (circuit open)."

    try:
        result = await search_web(query)
        circuit_breaker.record_success("search_web")
        tool_health.record("search_web", success=True)
    except Exception:
        circuit_breaker.record_failure("search_web")
        tool_health.record("search_web", success=False)
"""

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit Breaker — fast fail on repeatedly broken tools
# ---------------------------------------------------------------------------


@dataclass
class _ToolState:
    failures: int = 0
    last_failure: float = 0.0
    opened_at: float = 0.0


class CircuitBreaker:
    """Per-tool circuit breaker with configurable thresholds.

    After ``max_failures`` consecutive failures, the circuit opens for
    ``cooldown_seconds``.  During cooldown, ``is_open()`` returns True
    and callers should skip the tool / use a fallback.
    """

    def __init__(self, max_failures: int = 3, cooldown_seconds: float = 300):
        self._max_failures = max_failures
        self._cooldown = cooldown_seconds
        self._tools: dict[str, _ToolState] = defaultdict(_ToolState)

    def is_open(self, tool_name: str) -> bool:
        """Return True if the tool's circuit is currently open (broken)."""
        state = self._tools.get(tool_name)
        if state is None or state.failures < self._max_failures:
            return False
        # Check cooldown expiry
        if time.monotonic() - state.opened_at >= self._cooldown:
            # Half-open: allow one retry
            state.failures = self._max_failures - 1
            return False
        return True

    def record_success(self, tool_name: str) -> None:
        """Reset the failure counter on success."""
        state = self._tools.get(tool_name)
        if state:
            state.failures = 0
            state.opened_at = 0.0

    def record_failure(self, tool_name: str) -> None:
        """Increment the failure counter; open circuit if threshold reached."""
        state = self._tools[tool_name]
        state.failures += 1
        state.last_failure = time.monotonic()
        if state.failures >= self._max_failures and state.opened_at == 0.0:
            state.opened_at = time.monotonic()
            log.warning(
                "Circuit OPEN for %s (%d consecutive failures, cooldown %.0fs)",
                tool_name,
                state.failures,
                self._cooldown,
            )

    def status(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot of all tracked tools for diagnostics."""
        now = time.monotonic()
        result = {}
        for name, state in self._tools.items():
            is_open = state.failures >= self._max_failures and (now - state.opened_at < self._cooldown)
            result[name] = {
                "failures": state.failures,
                "is_open": is_open,
                "seconds_until_retry": max(0, self._cooldown - (now - state.opened_at)) if is_open else 0,
            }
        return result


# Global instance
circuit_breaker = CircuitBreaker()


# ---------------------------------------------------------------------------
# Tool Health Tracker — persistent success/failure rates
# ---------------------------------------------------------------------------

_HEALTH_FILE = Path(os.getenv("MEMORY_DIR", "data/memory")) / "tool_health.json"


class ToolHealthTracker:
    """Track per-tool success/failure counts for adaptive routing.

    Persisted to ``data/memory/tool_health.json`` on every Nth write.
    """

    def __init__(self, persist_every: int = 10):
        self._persist_every = persist_every
        self._write_count = 0
        self._stats: dict[str, dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        try:
            if _HEALTH_FILE.exists():
                self._stats = json.loads(_HEALTH_FILE.read_text())
                log.info("Loaded tool health stats for %d tools", len(self._stats))
        except (OSError, json.JSONDecodeError, ValueError, KeyError) as e:
            log.warning("Failed to load tool health file: %s", e)
            self._stats = {}

    def _save(self) -> None:
        try:
            _HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
            _HEALTH_FILE.write_text(json.dumps(self._stats, indent=2))
        except (OSError, ValueError, TypeError) as e:
            log.warning("Failed to save tool health: %s", e)

    def record(self, tool_name: str, success: bool) -> None:
        """Record a tool call outcome."""
        if tool_name not in self._stats:
            self._stats[tool_name] = {"success": 0, "failure": 0}
        key = "success" if success else "failure"
        self._stats[tool_name][key] += 1
        self._write_count += 1
        if self._write_count >= self._persist_every:
            self._save()
            self._write_count = 0

    def success_rate(self, tool_name: str) -> float:
        """Return the tool's success rate (0.0–1.0). Returns 1.0 if no data."""
        entry = self._stats.get(tool_name)
        if not entry:
            return 1.0
        total = entry["success"] + entry["failure"]
        if total == 0:
            return 1.0
        return entry["success"] / total

    def summary(self) -> dict[str, dict[str, Any]]:
        """Return all tool stats for diagnostics."""
        result = {}
        for name, counts in self._stats.items():
            total = counts["success"] + counts["failure"]
            result[name] = {
                **counts,
                "total": total,
                "success_rate": round(counts["success"] / total, 3) if total else 1.0,
            }
        return result


# Global instance
tool_health = ToolHealthTracker()
