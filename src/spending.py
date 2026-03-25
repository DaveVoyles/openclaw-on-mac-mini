"""
OpenClaw Spending Tracker — Gemini API Cost Monitoring
Tracks token usage per API call and persists cumulative spending to disk.
"""

import asyncio
import datetime
import fcntl
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("openclaw.spending")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPENDING_FILE = Path(os.getenv("SPENDING_FILE", "/memory/spending.json"))

# Gemini 2.0 Flash pricing (paid tier 1, ≤128K context window)
# https://ai.google.dev/pricing
PRICE_INPUT_PER_M = float(os.getenv("GEMINI_PRICE_INPUT_PER_M", "0.10"))   # $/1M input tokens
PRICE_OUTPUT_PER_M = float(os.getenv("GEMINI_PRICE_OUTPUT_PER_M", "0.40"))  # $/1M output tokens
BUDGET_LIMIT = float(os.getenv("GEMINI_BUDGET_LIMIT", "30.00"))             # $ budget cap


# ---------------------------------------------------------------------------
# Spending store
# ---------------------------------------------------------------------------


class SpendingTracker:
    """Persistent JSON-based token usage and cost tracker."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if SPENDING_FILE.exists():
            try:
                with open(SPENDING_FILE) as f:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    try:
                        return json.load(f)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
            except Exception as e:
                log.error("Failed to load spending data: %s", e)
        return self._empty()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "budget_limit": BUDGET_LIMIT,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0.0,
            "calls": 0,
            "daily": {},      # "2026-03-23": {input, output, cost, calls}
            "first_call": None,
            "last_call": None,
        }

    def _save(self):
        SPENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp = SPENDING_FILE.with_suffix(".tmp")
            with open(tmp, "w") as f:
                f.write(json.dumps(self._data, indent=2))
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(SPENDING_FILE)
        except Exception as e:
            log.error("Failed to save spending data: %s", e)

    # -----------------------------------------------------------------------
    # Recording
    # -----------------------------------------------------------------------

    async def record(self, input_tokens: int, output_tokens: int):
        """Record token usage from a single API call."""
        async with self._lock:
            await self._record_locked(input_tokens, output_tokens)

    async def _record_locked(self, input_tokens: int, output_tokens: int):
        """Internal: called while holding self._lock."""
        cost_input = (input_tokens / 1_000_000) * PRICE_INPUT_PER_M
        cost_output = (output_tokens / 1_000_000) * PRICE_OUTPUT_PER_M
        cost = cost_input + cost_output

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        today = datetime.date.today().isoformat()

        self._data["total_input_tokens"] += input_tokens
        self._data["total_output_tokens"] += output_tokens
        self._data["total_cost_usd"] += cost
        self._data["calls"] += 1
        self._data["last_call"] = now
        if self._data["first_call"] is None:
            self._data["first_call"] = now

        # Daily bucket
        day = self._data["daily"].setdefault(today, {
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0,
        })
        day["input_tokens"] += input_tokens
        day["output_tokens"] += output_tokens
        day["cost_usd"] += cost
        day["calls"] += 1

        self._save()

        log.info(
            "Spending: +%d in / +%d out ($%.6f) — total $%.4f / $%.2f budget",
            input_tokens, output_tokens, cost,
            self._data["total_cost_usd"], BUDGET_LIMIT,
        )

    # -----------------------------------------------------------------------
    # Queries
    # -----------------------------------------------------------------------

    @property
    def total_cost(self) -> float:
        return self._data["total_cost_usd"]

    @property
    def budget_remaining(self) -> float:
        return max(0.0, BUDGET_LIMIT - self._data["total_cost_usd"])

    @property
    def budget_pct_used(self) -> float:
        if BUDGET_LIMIT <= 0:
            return 0.0
        return min(100.0, (self._data["total_cost_usd"] / BUDGET_LIMIT) * 100)

    @property
    def is_over_budget(self) -> bool:
        return self._data["total_cost_usd"] >= BUDGET_LIMIT

    @property
    def budget_limit(self) -> float:
        return self._data["budget_limit"]

    @property
    def total_input_tokens(self) -> int:
        return self._data["total_input_tokens"]

    @property
    def total_output_tokens(self) -> int:
        return self._data["total_output_tokens"]

    @property
    def calls(self) -> int:
        return self._data["calls"]

    @property
    def daily(self) -> dict:
        return self._data.get("daily", {})

    def summary(self) -> str:
        """Human-readable spending summary."""
        d = self._data
        total_tokens = d["total_input_tokens"] + d["total_output_tokens"]

        # Progress bar
        pct = self.budget_pct_used
        filled = int(pct / 5)  # 20-char bar
        bar = "█" * filled + "░" * (20 - filled)

        lines = [
            f"**💰 Gemini API Spending**",
            f"",
            f"**Budget:** ${d['total_cost_usd']:.4f} / ${BUDGET_LIMIT:.2f}",
            f"**Remaining:** ${self.budget_remaining:.4f}",
            f"[{bar}] {pct:.1f}%",
            f"",
            f"**Tokens Used:**",
            f"  Input:  {d['total_input_tokens']:,}",
            f"  Output: {d['total_output_tokens']:,}",
            f"  Total:  {total_tokens:,}",
            f"",
            f"**API Calls:** {d['calls']:,}",
        ]

        if d["calls"] > 0:
            avg_cost = d["total_cost_usd"] / d["calls"]
            avg_tokens = total_tokens / d["calls"]
            lines.append(f"**Avg per call:** ${avg_cost:.6f} ({avg_tokens:.0f} tokens)")

            # Estimate calls remaining at current rate
            if avg_cost > 0:
                calls_left = int(self.budget_remaining / avg_cost)
                lines.append(f"**Est. calls remaining:** ~{calls_left:,}")

        if d["first_call"]:
            lines.append(f"")
            lines.append(f"**Tracking since:** {d['first_call'][:10]}")

        return "\n".join(lines)

    def daily_breakdown(self, days: int = 7) -> str:
        """Show per-day spending for the last N days."""
        daily = self._data.get("daily", {})
        if not daily:
            return "No daily data yet."

        sorted_days = sorted(daily.keys(), reverse=True)[:days]
        lines = ["**📊 Daily Breakdown**", ""]
        for day_key in sorted_days:
            d = daily[day_key]
            tokens = d["input_tokens"] + d["output_tokens"]
            lines.append(
                f"**{day_key}**: ${d['cost_usd']:.4f} "
                f"({tokens:,} tokens, {d['calls']} calls)"
            )
        return "\n".join(lines)

    def reset(self) -> str:
        """Reset all spending data. Returns confirmation."""
        self._data = self._empty()
        self._save()
        return "✅ Spending tracker reset to zero."


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

tracker = SpendingTracker()


# ---------------------------------------------------------------------------
# Skills (callable by LLM via /ask)
# ---------------------------------------------------------------------------


async def get_spending() -> str:
    """Return current Gemini API spending summary."""
    return tracker.summary()


async def get_daily_spending(days: int = 7) -> str:
    """Return daily spending breakdown."""
    return tracker.daily_breakdown(days=days)
