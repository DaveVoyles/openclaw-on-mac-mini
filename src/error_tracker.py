"""
OpenClaw Error Tracker — Self-Healing Phase
Records every /ask outcome for pattern detection and auto-diagnosis.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("openclaw.error_tracker")

JOURNAL_FILE = Path(os.getenv("ERROR_JOURNAL", "/memory/error_journal.jsonl"))


def record_outcome(
    *,
    user_id: int = 0,
    question: str = "",
    model_used: str = "unknown",
    success: bool = True,
    error_msg: str = "",
    latency_ms: int = 0,
    routing_notes: list[str] | None = None,
    tools_called: list[str] | None = None,
    reflected: bool = False,
) -> None:
    """Record a /ask outcome to the error journal."""
    entry = {
        "ts": time.time(),
        "user_id": user_id,
        "question": question[:200],
        "model_used": model_used,
        "success": success,
        "error": error_msg[:500] if error_msg else "",
        "latency_ms": latency_ms,
        "routing_notes": routing_notes or [],
        "tools_called": tools_called or [],
        "reflected": reflected,
    }

    try:
        JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        log.debug("Failed to write error journal: %s", e)


def get_recent_outcomes(hours: int = 24, limit: int = 100) -> list[dict]:
    """Read recent outcomes from the journal."""
    if not JOURNAL_FILE.exists():
        return []

    cutoff = time.time() - (hours * 3600)
    entries = []

    try:
        with open(JOURNAL_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("ts", 0) >= cutoff:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.debug("Failed to read error journal: %s", e)

    return entries[-limit:]


def get_error_stats(hours: int = 24) -> dict:
    """Calculate error statistics for the dashboard."""
    entries = get_recent_outcomes(hours=hours)
    if not entries:
        return {
            "total": 0, "successes": 0, "failures": 0,
            "success_rate": 1.0, "avg_latency_ms": 0,
            "recent_errors": [], "model_breakdown": {},
        }

    successes = sum(1 for e in entries if e.get("success"))
    failures = sum(1 for e in entries if not e.get("success"))
    total = len(entries)
    avg_latency = int(sum(e.get("latency_ms", 0) for e in entries) / total) if total else 0

    # Recent errors (last 5)
    recent_errors = [
        {
            "ts": e.get("ts", 0),
            "question": e.get("question", "")[:80],
            "error": e.get("error", "")[:100],
            "model": e.get("model_used", ""),
        }
        for e in reversed(entries) if not e.get("success")
    ][:5]

    # Model breakdown
    model_counts: dict[str, dict] = {}
    for e in entries:
        model = e.get("model_used", "unknown")
        if model not in model_counts:
            model_counts[model] = {"total": 0, "failures": 0}
        model_counts[model]["total"] += 1
        if not e.get("success"):
            model_counts[model]["failures"] += 1

    return {
        "total": total,
        "successes": successes,
        "failures": failures,
        "success_rate": round(successes / total, 3) if total else 1.0,
        "avg_latency_ms": avg_latency,
        "recent_errors": recent_errors,
        "model_breakdown": model_counts,
    }


def check_error_patterns(window_minutes: int = 30) -> list[dict]:
    """Detect failure patterns in recent /ask outcomes.

    Returns list of detected patterns:
    [{"type": "...", "severity": "warning|critical", "detail": "...", "count": N}, ...]
    """
    entries = get_recent_outcomes(hours=window_minutes / 60, limit=200)
    if not entries:
        return []

    patterns: list[dict] = []

    # Pattern 1: High failure rate (>30% in window)
    total = len(entries)
    failures = sum(1 for e in entries if not e.get("success"))
    if total >= 3 and failures / total > 0.3:
        patterns.append({
            "type": "high_failure_rate",
            "severity": "critical" if failures / total > 0.5 else "warning",
            "detail": f"{failures}/{total} failures ({int(failures / total * 100)}%) in last {window_minutes} min",
            "count": failures,
        })

    # Pattern 2: Same error repeated 3+ times
    error_counts: dict[str, int] = {}
    for e in entries:
        if not e.get("success") and e.get("error"):
            key = e["error"][:80].strip()
            error_counts[key] = error_counts.get(key, 0) + 1
    for error_msg, count in error_counts.items():
        if count >= 3:
            patterns.append({
                "type": "repeated_error",
                "severity": "warning",
                "detail": f"'{error_msg[:60]}' occurred {count} times",
                "count": count,
            })

    # Pattern 3: Ollama timeout streak
    recent = entries[-10:]
    ollama_timeouts = sum(
        1 for e in recent
        if any("Ollama" in n or "timed out" in n for n in e.get("routing_notes", []))
    )
    if ollama_timeouts >= 3:
        patterns.append({
            "type": "ollama_timeout_streak",
            "severity": "warning",
            "detail": f"Ollama timed out {ollama_timeouts} of last {len(recent)} queries",
            "count": ollama_timeouts,
        })

    # Pattern 4: Specific model failures
    for model, counts in get_error_stats(hours=1).get("model_breakdown", {}).items():
        if counts.get("failures", 0) >= 3 and counts.get("total", 0) > 0:
            rate = counts["failures"] / counts["total"]
            if rate > 0.5:
                patterns.append({
                    "type": "model_failures",
                    "severity": "critical" if rate > 0.8 else "warning",
                    "detail": f"Model '{model}' failing {int(rate * 100)}% ({counts['failures']}/{counts['total']})",
                    "count": counts["failures"],
                })

    # Pattern 5: High latency streak (avg > 15s for last 5 queries)
    recent_latencies = [e.get("latency_ms", 0) for e in entries[-5:] if e.get("latency_ms")]
    if recent_latencies and sum(recent_latencies) / len(recent_latencies) > 15000:
        avg = int(sum(recent_latencies) / len(recent_latencies))
        patterns.append({
            "type": "high_latency",
            "severity": "warning",
            "detail": f"Average latency {avg}ms over last {len(recent_latencies)} queries",
            "count": len(recent_latencies),
        })

    return patterns
