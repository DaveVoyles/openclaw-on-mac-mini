"""
OpenClaw Error Tracker — Self-Healing Phase
Records every /ask outcome for pattern detection and auto-diagnosis.
"""

import json
import logging
import os
import time
from pathlib import Path

from trace_context import get_trace_id

log = logging.getLogger(__name__)

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
    scope_mode: str = None,
    lock_mode: str = None,
    anchor_id: str = None,
    anchor_age: float = None,
    profile_values: dict = None,
    response_preview: str = "",
    explainability: dict | None = None,
    trace_id: str | None = None,
) -> None:
    """Record a /ask outcome to the error journal."""
    resolved_trace_id = (trace_id or "").strip() or get_trace_id()
    if resolved_trace_id == "no-trace":
        resolved_trace_id = ""
    entry = {
        "ts": time.time(),
        "trace_id": resolved_trace_id,
        "user_id": user_id,
        "question": question[:200],
        "model_used": model_used,
        "success": success,
        "error": error_msg[:500] if error_msg else "",
        "latency_ms": latency_ms,
        "routing_notes": routing_notes or [],
        "tools_called": tools_called or [],
        "reflected": reflected,
        "scope_mode": scope_mode,
        "lock_mode": lock_mode,
        "anchor_id": anchor_id,
        "anchor_age": anchor_age,
        "profile_values": profile_values or {},
        "response_preview": (response_preview or "")[:2000],
        "explainability": explainability or {},
    }

    try:
        JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError as e:
        log.debug("Failed to write error journal: %s", e)


def journal_ask_outcome(
    *,
    question: str = "",
    response_text: str = "",
    model_used: str = "unknown",
    final_meta: dict | None = None,
    success: bool = True,
    latency_ms: int = 0,
    error_msg: str = "",
) -> None:
    """Best-effort journaling of a completed /ask outcome.

    Maps the executor's ``final_meta`` onto :func:`record_outcome`. Wrapped so it
    never raises into the response path — telemetry must not break a user answer.
    """
    try:
        meta = final_meta if isinstance(final_meta, dict) else {}
        routing_notes = meta.get("routing_notes")
        routing_notes = [str(n) for n in routing_notes] if isinstance(routing_notes, list) else []
        tools_called = meta.get("tools_called")
        tools_called = [str(t) for t in tools_called] if isinstance(tools_called, list) else []
        record_outcome(
            question=question or "",
            model_used=model_used or "unknown",
            success=success,
            error_msg=error_msg,
            latency_ms=int(latency_ms or 0),
            routing_notes=routing_notes,
            tools_called=tools_called,
            response_preview=response_text or "",
        )
        # Feed aggregate response-time stats (p50/p95/p99) from the same hook.
        if latency_ms and success:
            try:
                from spending import record_response_time

                record_response_time(float(latency_ms), model_used or "unknown")
            except Exception:  # broad: telemetry must never break the response path
                log.debug("record_response_time failed", exc_info=True)
    except Exception:  # broad: telemetry must never break the response path
        log.debug("journal_ask_outcome failed", exc_info=True)


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
    except OSError as e:
        log.debug("Failed to read error journal: %s", e)

    return entries[-limit:]


def get_latency_stats(hours: int = 2160, limit: int = 1000) -> dict:
    """Compute response-latency percentiles from the journal.

    Journal-backed (persists across restarts), unlike the in-memory
    ``spending.get_response_stats`` deque. Returns count + p50/p95/p99 in ms
    plus a per-model breakdown, for the dashboard agent-capability metrics.
    """
    entries = get_recent_outcomes(hours=hours, limit=limit)
    times = sorted(
        float(e.get("latency_ms") or 0)
        for e in entries
        if isinstance(e.get("latency_ms"), (int, float)) and e.get("latency_ms")
    )
    if not times:
        return {
            "count": 0,
            "avg_ms": 0,
            "p50_ms": 0,
            "p95_ms": 0,
            "p99_ms": 0,
            "last_10": [],
            "by_model": {},
        }

    n = len(times)
    by_model: dict[str, list[float]] = {}
    for e in entries:
        v = e.get("latency_ms")
        if isinstance(v, (int, float)) and v:
            by_model.setdefault(str(e.get("model_used") or "unknown"), []).append(float(v))
    model_summary = {}
    for model, vals in by_model.items():
        vs = sorted(vals)
        mn = len(vs)
        model_summary[model] = {
            "count": mn,
            "avg_ms": round(sum(vs) / mn),
            "p95_ms": round(vs[int(mn * 0.95)]) if mn >= 20 else round(vs[-1]),
        }

    last_10 = [
        round(float(e.get("latency_ms")))
        for e in entries[-10:]
        if isinstance(e.get("latency_ms"), (int, float)) and e.get("latency_ms")
    ]
    return {
        "count": n,
        "avg_ms": round(sum(times) / n),
        "p50_ms": round(times[n // 2]),
        "p95_ms": round(times[int(n * 0.95)]) if n >= 20 else round(times[-1]),
        "p99_ms": round(times[int(n * 0.99)]) if n >= 100 else round(times[-1]),
        "last_10": last_10,
        "by_model": model_summary,
    }


def get_error_stats(hours: int = 24) -> dict:
    """Calculate error statistics for the dashboard."""
    entries = get_recent_outcomes(hours=hours)
    if not entries:
        return {
            "total": 0,
            "successes": 0,
            "failures": 0,
            "success_rate": 1.0,
            "avg_latency_ms": 0,
            "recent_errors": [],
            "model_breakdown": {},
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
        for e in reversed(entries)
        if not e.get("success")
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
        patterns.append(
            {
                "type": "high_failure_rate",
                "severity": "critical" if failures / total > 0.5 else "warning",
                "detail": f"{failures}/{total} failures ({int(failures / total * 100)}%) in last {window_minutes} min",
                "count": failures,
            }
        )

    # Pattern 2: Same error repeated 3+ times
    error_counts: dict[str, int] = {}
    for e in entries:
        if not e.get("success") and e.get("error"):
            key = e["error"][:80].strip()
            error_counts[key] = error_counts.get(key, 0) + 1
    for error_msg, count in error_counts.items():
        if count >= 3:
            patterns.append(
                {
                    "type": "repeated_error",
                    "severity": "warning",
                    "detail": f"'{error_msg[:60]}' occurred {count} times",
                    "count": count,
                }
            )

    # Pattern 3: Ollama timeout streak
    recent = entries[-10:]
    ollama_timeouts = sum(
        1 for e in recent if any("Ollama" in n or "timed out" in n for n in e.get("routing_notes", []))
    )
    if ollama_timeouts >= 3:
        patterns.append(
            {
                "type": "ollama_timeout_streak",
                "severity": "warning",
                "detail": f"Ollama timed out {ollama_timeouts} of last {len(recent)} queries",
                "count": ollama_timeouts,
            }
        )

    # Pattern 4: Specific model failures
    for model, counts in get_error_stats(hours=1).get("model_breakdown", {}).items():
        if counts.get("failures", 0) >= 3 and counts.get("total", 0) > 0:
            rate = counts["failures"] / counts["total"]
            if rate > 0.5:
                patterns.append(
                    {
                        "type": "model_failures",
                        "severity": "critical" if rate > 0.8 else "warning",
                        "detail": f"Model '{model}' failing {int(rate * 100)}% ({counts['failures']}/{counts['total']})",
                        "count": counts["failures"],
                    }
                )

    # Pattern 5: High latency streak (avg > 15s for last 5 queries)
    recent_latencies = [e.get("latency_ms", 0) for e in entries[-5:] if e.get("latency_ms")]
    if recent_latencies and sum(recent_latencies) / len(recent_latencies) > 15000:
        avg = int(sum(recent_latencies) / len(recent_latencies))
        patterns.append(
            {
                "type": "high_latency",
                "severity": "warning",
                "detail": f"Average latency {avg}ms over last {len(recent_latencies)} queries",
                "count": len(recent_latencies),
            }
        )

    return patterns


# ---------------------------------------------------------------------------
# E3: Auto-Diagnosis
# ---------------------------------------------------------------------------


async def diagnose_error_pattern(
    patterns: list[dict],
    recent_errors: list[dict] | None = None,
) -> dict:
    """Use LLM to diagnose the root cause of detected error patterns.

    Returns: {
        "cause": str,           # Root cause description
        "severity": str,        # "low", "medium", "high", "critical"
        "fix_type": str,        # "restart_service", "switch_model", "increase_timeout",
                                # "clear_circuit_breaker", "manual_required", "none"
        "fix_target": str,      # Service name, model name, etc.
        "confidence": float,    # 0.0-1.0
        "explanation": str,     # Human-readable explanation
    }
    """
    from llm_client import quick_generate

    _default = {
        "cause": "unknown",
        "severity": "low",
        "fix_type": "manual_required",
        "fix_target": "",
        "confidence": 0.0,
    }

    pattern_desc = "\n".join(f"- [{p['severity'].upper()}] {p['type']}: {p['detail']}" for p in patterns)

    error_samples = ""
    if recent_errors:
        error_samples = "\nRecent error samples:\n" + "\n".join(
            f"- [{e.get('model_used', '?')}] {e.get('error', 'no error msg')[:150]}" for e in recent_errors[:5]
        )

    container_context = ""
    try:
        import subprocess

        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            container_context = f"\nContainer status:\n{result.stdout[:500]}"
    except subprocess.TimeoutExpired:
        log.warning("Docker ps timed out while getting container context")
    except OSError as e:
        log.warning("Failed to get docker container context: %s", e)

    prompt = (
        "You are an error diagnosis system for OpenClaw, a Discord bot running on a Mac Mini.\n"
        "Analyze these error patterns and determine the root cause.\n\n"
        f"Detected patterns:\n{pattern_desc}\n{error_samples}\n{container_context}\n\n"
        "Respond in this EXACT JSON format (nothing else):\n"
        "{\n"
        '  "cause": "brief root cause description",\n'
        '  "severity": "low|medium|high|critical",\n'
        '  "fix_type": "restart_service|switch_model|increase_timeout|clear_circuit_breaker|manual_required|none",\n'
        '  "fix_target": "service or model name if applicable",\n'
        '  "confidence": 0.0-1.0,\n'
        '  "explanation": "human-readable explanation of what happened and why"\n'
        "}"
    )

    try:
        text = await quick_generate(prompt, max_tokens=500, temperature=0.1)
        if not text:
            return {**_default, "explanation": "No API key or generation failed"}
        from json_utils import repair_json

        result = repair_json(text)
        if isinstance(result, dict) and "cause" in result:
            valid_fixes = {
                "restart_service",
                "switch_model",
                "increase_timeout",
                "clear_circuit_breaker",
                "manual_required",
                "none",
            }
            if result.get("fix_type") not in valid_fixes:
                result["fix_type"] = "manual_required"
            return result

        return {**_default, "severity": "medium", "confidence": 0.3, "cause": text[:200], "explanation": text[:300]}
    except Exception as e:  # broad: intentional
        return {**_default, "cause": str(e), "explanation": f"Diagnosis failed: {e}"}


# ---------------------------------------------------------------------------
# E4: Auto-Fix
# ---------------------------------------------------------------------------

_SAFE_RESTART_TARGETS = frozenset(
    {
        "sonarr",
        "radarr",
        "lidarr",
        "prowlarr",
        "sabnzbd",
        "qbittorrent",
        "tautulli",
        "overseerr",
    }
)


async def execute_fix(diagnosis: dict) -> dict:
    """Execute a safe auto-fix based on the diagnosis.

    Returns: {"action_taken": str, "success": bool, "detail": str}
    """
    fix_type = diagnosis.get("fix_type", "none")
    fix_target = diagnosis.get("fix_target", "")
    confidence = diagnosis.get("confidence", 0.0)

    if confidence < 0.6:
        return {
            "action_taken": "skipped",
            "success": False,
            "detail": f"Confidence too low ({confidence:.0%}) for auto-fix",
        }

    if fix_type == "restart_service":
        target = fix_target.lower().strip()
        if target not in _SAFE_RESTART_TARGETS:
            return {"action_taken": "skipped", "success": False, "detail": f"'{target}' not in safe restart list"}
        try:
            from skills import restart_container

            result = await restart_container(target)
            log.info("Auto-fix: restarted %s → %s", target, result[:80])
            return {"action_taken": f"restart_service:{target}", "success": True, "detail": result}
        except (ImportError, OSError, RuntimeError) as e:
            return {"action_taken": f"restart_service:{target}", "success": False, "detail": str(e)}

    elif fix_type == "switch_model":
        log.info("Auto-fix: recommending model switch to '%s'", fix_target or "gemini")
        return {
            "action_taken": f"switch_model:{fix_target or 'gemini'}",
            "success": True,
            "detail": f"Recommended switching to {fix_target or 'gemini'}. Users can override with /model set.",
        }

    elif fix_type == "clear_circuit_breaker":
        try:
            from tool_health import circuit_breaker

            target = fix_target.lower().strip()
            if target and target in circuit_breaker._tools:
                circuit_breaker._tools[target].failures = 0
                circuit_breaker._tools[target].last_failure = 0
                log.info("Auto-fix: cleared circuit breaker for %s", target)
                return {
                    "action_taken": f"clear_circuit_breaker:{target}",
                    "success": True,
                    "detail": f"Circuit breaker reset for {target}",
                }
            return {
                "action_taken": "clear_circuit_breaker",
                "success": False,
                "detail": f"Tool '{target}' not found in circuit breaker",
            }
        except (ImportError, AttributeError, KeyError) as e:
            return {"action_taken": "clear_circuit_breaker", "success": False, "detail": str(e)}

    elif fix_type == "increase_timeout":
        return {
            "action_taken": "increase_timeout",
            "success": True,
            "detail": f"Recommendation: increase timeout for {fix_target}. "
            "Adjust OLLAMA_TIMEOUT or LLM timeout in .env.",
        }

    else:
        return {
            "action_taken": "manual_required",
            "success": False,
            "detail": diagnosis.get("explanation", "Manual intervention needed"),
        }


# ---------------------------------------------------------------------------
# E5: Error Learning
# ---------------------------------------------------------------------------

INCIDENTS_FILE = Path(os.getenv("INCIDENTS_FILE", "/memory/incidents.json"))


async def record_incident(
    patterns: list[dict],
    diagnosis: dict,
    fix_result: dict,
) -> None:
    """Record a complete incident for learning."""
    incident = {
        "ts": time.time(),
        "patterns": patterns,
        "diagnosis": {
            "cause": diagnosis.get("cause", ""),
            "fix_type": diagnosis.get("fix_type", ""),
            "fix_target": diagnosis.get("fix_target", ""),
            "confidence": diagnosis.get("confidence", 0),
        },
        "fix": {
            "action": fix_result.get("action_taken", ""),
            "success": fix_result.get("success", False),
            "detail": fix_result.get("detail", ""),
        },
    }

    try:
        incidents: list = []
        if INCIDENTS_FILE.exists():
            incidents = json.loads(INCIDENTS_FILE.read_text())
        incidents.append(incident)
        incidents = incidents[-100:]
        INCIDENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        INCIDENTS_FILE.write_text(json.dumps(incidents, indent=2, default=str))
    except (OSError, ValueError) as e:
        log.debug("Failed to save incident: %s", e)

    if fix_result.get("success"):
        try:
            from rules_engine import add_rule

            pattern_types = ", ".join(p["type"] for p in patterns)
            rule_text = (
                f"When error pattern '{pattern_types}' is detected: "
                f"{diagnosis.get('explanation', diagnosis.get('cause', 'unknown cause'))}. "
                f"Auto-fix: {fix_result['action_taken']}."
            )
            await add_rule(
                rule_text,
                source_message=f"auto-heal incident @ {time.strftime('%Y-%m-%d %H:%M')}",
            )
            log.info("Error learning: created rule from incident: %s", rule_text[:100])
        except (ImportError, OSError) as e:
            log.debug("Failed to create rule from incident: %s", e)


def get_past_incidents(pattern_type: str = "", limit: int = 5) -> list[dict]:
    """Look up past incidents for similar patterns."""
    if not INCIDENTS_FILE.exists():
        return []
    try:
        incidents = json.loads(INCIDENTS_FILE.read_text())
        if pattern_type:
            incidents = [i for i in incidents if any(p.get("type") == pattern_type for p in i.get("patterns", []))]
        return incidents[-limit:]
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Failed to load error incidents: %s", e)
        return []
    except (AttributeError, TypeError, KeyError):
        return []
