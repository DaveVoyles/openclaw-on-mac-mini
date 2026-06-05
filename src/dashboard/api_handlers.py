"""JSON API endpoint handlers for the dashboard."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import math
import platform
import re
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import aiohttp
from aiohttp import web

from http_session import SessionManager as _SessionManager
from openclaw_cli_sessions import export_session as export_cli_session
from openclaw_cli_sessions import list_sessions as list_cli_sessions
from openclaw_cli_sessions import load_watch_state as load_cli_watch_state
from openclaw_cli_sessions import queue_watch_intervention as queue_cli_watch_intervention
from openclaw_cli_sessions import require_session as require_cli_session
from spending import get_quota_status, get_response_stats
from spending import tracker as spending_tracker

from .helpers import GITHUB_REPO, VERSION, _command_list, _command_quickstart, _cron_to_human, _load_config, log

_dashboard_sessions = _SessionManager(timeout=10, name="dashboard")

# Simple in-process TTL cache for expensive API calls
_api_cache: dict = {}  # key -> (value, expires_at_float)


def _cache_get(key: str):
    entry = _api_cache.get(key)
    if entry and entry[1] > __import__('time').time():
        return entry[0]
    return None


def _cache_set(key: str, value, ttl_seconds: int = 60):
    _api_cache[key] = (value, __import__('time').time() + ttl_seconds)


def _overseerr_headers():
    import os

    # Seerr/Overseerr expects the raw API key value from settings.json
    # (which is itself a base64 string) — do NOT decode it further
    key = os.environ.get("OVERSEERR_API_KEY", "")
    return {"X-Api-Key": key, "Content-Type": "application/json"}


async def _ntfy_push(title: str, body_text: str) -> None:
    import os

    ntfy_url = os.environ.get("NTFY_URL", "https://ntfy.sh")
    topic = os.environ.get("NTFY_TOPIC", "openclaw-alerts")
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(
                f"{ntfy_url}/{topic}",
                data=body_text.encode(),
                headers={"Title": title, "Priority": "default"},
                timeout=aiohttp.ClientTimeout(total=5),
            )
    except Exception as e:
        log.warning("ntfy push failed: %s", e)


_QUALITY_DOMAIN_LIMIT = 6
_QUALITY_FAILURE_LIMIT = 6
_QUALITY_SIGNAL_LIMIT = 6

_QUALITY_FAILURE_CATEGORY_LABELS: dict[str, str] = {
    "requested_item_shortfall": "requested-item shortfall",
    "source_diversity_shortfall": "source-diversity shortfall",
    "low_evidence_completeness": "low evidence completeness",
    "degrade_mode_constrained": "degrade-mode constrained",
    "provider_timeout_pressure": "provider-timeout pressure",
    "quality_regression": "quality regression",
    "other": "other",
}


def _safe_non_negative_int(value: object, default: int = 0) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _get_perplexity_cache_stats() -> dict:
    """Return Perplexity cache stats without hard-importing at module level."""
    try:
        from skills.search_skills import get_perplexity_cache_stats

        return get_perplexity_cache_stats()
    except (ImportError, AttributeError):
        return {"size": 0, "live_entries": 0, "hits": 0, "ttl_seconds": 300}


def _get_quality_retry_count() -> int:
    """Return quality retry count from answer_policy without hard-importing."""
    try:
        from answer_policy import get_quality_retry_count

        return get_quality_retry_count()
    except (ImportError, AttributeError):
        return 0


def _content_extraction_check() -> dict[str, str]:
    """Report which content-extraction fallback is currently available."""
    extractor = "unavailable"
    status = "unavailable"
    try:
        import trafilatura  # noqa: F401

        extractor = "trafilatura"
        status = "ok"
    except ImportError:
        try:
            import newspaper  # noqa: F401

            extractor = "newspaper"
            status = "ok"
        except ImportError:
            try:
                import playwright.async_api  # noqa: F401

                extractor = "playwright"
                status = "ok"
            except ImportError:
                extractor = "unavailable"
                status = "unavailable"

    return {
        "status": status,
        "chain": "trafilatura → Jina AI Reader → Playwright",
        "jina_reader": "available",
        "active": extractor,
    }


def _normalize_event_counts(raw_counts: object) -> dict[str, int]:
    if not isinstance(raw_counts, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw_counts.items():
        name = str(key or "").strip().lower()
        if not name:
            continue
        count = _safe_non_negative_int(value, default=0)
        if count <= 0:
            continue
        normalized[name] = normalized.get(name, 0) + count
    return normalized


def _infer_quality_domain(event_name: str) -> str:
    event = str(event_name or "").strip().lower()
    if not event:
        return "general"
    if event.startswith("ask_feedback_"):
        return "feedback"
    if event.startswith("degrade_mode_"):
        return "degrade"
    if "_" not in event:
        return event
    domain, _ = event.split("_", 1)
    return domain or "general"


def _classify_quality_signal(event_name: str) -> tuple[bool, bool, bool]:
    event = str(event_name or "").strip().lower()
    if not event:
        return False, False, False
    is_mitigation = (
        "improved" in event
        or "accepted" in event
        or ("helpful" in event and "not_helpful" not in event)
        or "recovered" in event
    )
    is_failure = (
        "fallback" in event
        or "incident" in event
        or "warning" in event
        or "failed" in event
        or "degrade" in event
        or "low" in event
        or "not_helpful" in event
        or "suppressed" in event
        or "no_improvement" in event
        or "timeout" in event
        or "error" in event
    )
    is_degrade = (
        "degrade" in event
        or "fallback" in event
        or "incident" in event
        or "warning" in event
        or "failed" in event
        or "low" in event
        or "no_improvement" in event
    )
    return is_failure, is_mitigation, is_degrade


def _build_quality_domain_summary(
    event_counts: dict[str, int],
    *,
    limit: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, list[dict[str, object]]]]:
    safe_limit = max(1, min(limit, 12))
    domain_rollup: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "total_events": 0,
            "failure_events": 0,
            "mitigation_events": 0,
            "degrade_events": 0,
        }
    )
    recurring_failures: list[dict[str, object]] = []
    mitigation_signals: list[dict[str, object]] = []
    degrade_signals: list[dict[str, object]] = []
    for event, count in event_counts.items():
        domain = _infer_quality_domain(event)
        is_failure, is_mitigation, is_degrade = _classify_quality_signal(event)
        bucket = domain_rollup[domain]
        bucket["total_events"] += count
        if is_failure:
            bucket["failure_events"] += count
            recurring_failures.append({"event": event, "count": count, "domain": domain})
        if is_mitigation:
            bucket["mitigation_events"] += count
            mitigation_signals.append({"signal": event, "count": count, "domain": domain})
        if is_degrade:
            bucket["degrade_events"] += count
            degrade_signals.append({"signal": event, "count": count, "domain": domain})
    domain_summary: list[dict[str, object]] = []
    for domain, counts in domain_rollup.items():
        failure = counts["failure_events"]
        mitigation = counts["mitigation_events"]
        if failure == 0 and mitigation > 0:
            trend = "improving"
        elif failure >= max(2, mitigation * 2):
            trend = "degrading"
        elif failure > mitigation:
            trend = "watch"
        else:
            trend = "stable"
        domain_summary.append(
            {
                "domain": domain,
                "total_events": counts["total_events"],
                "failure_events": failure,
                "mitigation_events": mitigation,
                "degrade_events": counts["degrade_events"],
                "trend": trend,
            }
        )
    domain_summary.sort(
        key=lambda entry: (
            _safe_non_negative_int(entry.get("total_events", 0), default=0),
            _safe_non_negative_int(entry.get("failure_events", 0), default=0),
        ),
        reverse=True,
    )
    recurring_failures.sort(key=lambda entry: _safe_non_negative_int(entry.get("count", 0), default=0), reverse=True)
    mitigation_signals.sort(key=lambda entry: _safe_non_negative_int(entry.get("count", 0), default=0), reverse=True)
    degrade_signals.sort(key=lambda entry: _safe_non_negative_int(entry.get("count", 0), default=0), reverse=True)
    return (
        domain_summary[:safe_limit],
        recurring_failures[:safe_limit],
        {
            "mitigation": mitigation_signals[:safe_limit],
            "degrade": degrade_signals[:safe_limit],
        },
    )


def _normalize_quality_failure_category(event_name: str) -> str:
    event = str(event_name or "").strip().lower().replace(" ", "_")
    if not event:
        return "other"
    if "degrade_mode_constrained" in event:
        return "degrade_mode_constrained"
    if (
        "requested_item" in event
        or "missing_item" in event
        or "insufficient_item" in event
        or "low_results" in event
        or "item_coverage" in event
    ):
        return "requested_item_shortfall"
    if "source_diversity" in event or "single_source" in event or "mono_source" in event or "one_source" in event:
        return "source_diversity_shortfall"
    if (
        "partial_coverage" in event
        or "evidence" in event
        or "citation" in event
        or "grounding" in event
        or "completeness" in event
    ):
        return "low_evidence_completeness"
    if "timeout" in event or "rate_limit" in event:
        return "provider_timeout_pressure"
    if (
        "fallback" in event
        or "failed" in event
        or "error" in event
        or "no_improvement" in event
        or "not_helpful" in event
        or "suppressed" in event
    ):
        return "quality_regression"
    return "other"


def _build_quality_failure_category_summary(
    event_counts: dict[str, int],
    *,
    limit: int,
) -> dict[str, object]:
    safe_limit = max(1, min(int(limit or 10), 20))
    counts: dict[str, int] = defaultdict(int)
    examples: dict[str, list[dict[str, int | str]]] = defaultdict(list)
    total_failures = 0
    for event, count in event_counts.items():
        is_failure, _, _ = _classify_quality_signal(event)
        normalized_count = _safe_non_negative_int(count, default=0)
        if not is_failure or normalized_count <= 0:
            continue
        total_failures += normalized_count
        category = _normalize_quality_failure_category(event)
        counts[category] += normalized_count
        sample_bucket = examples[category]
        if len(sample_bucket) < 3:
            sample_bucket.append({"event": event, "count": normalized_count})
    sorted_counts = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    top: list[dict[str, object]] = []
    for index, (category, count) in enumerate(sorted_counts[:safe_limit], start=1):
        share = round(count / total_failures, 3) if total_failures > 0 else 0.0
        top.append(
            {
                "category": category,
                "label": _QUALITY_FAILURE_CATEGORY_LABELS.get(category, "other"),
                "count": count,
                "share": share,
                "rank": index,
                "examples": examples.get(category, [])[:3],
            }
        )
    return {
        "counts": {name: int(value) for name, value in sorted_counts},
        "top": top,
        "total_classified_failures": int(sum(counts.values())),
        "total_failure_events": int(total_failures),
    }


def _parse_scope_id(raw_value: str | int | None, *, field: str, required: bool = False) -> str | None:
    if raw_value in (None, ""):
        if required:
            raise ValueError(f"{field} is required")
        return None
    value = str(raw_value).strip()
    if not value:
        if required:
            raise ValueError(f"{field} is required")
        return None
    if not value.isdigit():
        raise ValueError(f"{field} must be a numeric Discord ID")
    return value


def _audit_scope_action(
    actor: str,
    action: str,
    *,
    channel_id: str,
    thread_id: str | None,
    detail: dict | None = None,
) -> None:
    try:
        from audit import audit_log

        payload = {
            "scope": {"channel_id": channel_id, "thread_id": thread_id},
            **(detail or {}),
        }
        audit_log(actor or "dashboard", action, detail=json.dumps(payload, separators=(",", ":")))
    except (ImportError, OSError, TypeError, ValueError) as exc:
        log.debug("Audit log write failed for %s: %s", action, exc)


async def _build_scope_clear_preview(*, channel_id: str, thread_id: str | None) -> dict:
    """Build a scoped clear preview using existing inspect data shape."""
    import vector_store

    summary = await vector_store.get_scoped_memory_summary(
        channel_id=channel_id,
        thread_id=thread_id,
        latest_limit=5,
        include_anchor=True,
    )
    collections = summary.get("collections", {}) if isinstance(summary, dict) else {}
    collections_preview: dict[str, dict[str, object]] = {}
    for name, info in collections.items():
        count = int((info or {}).get("count", 0) or 0)
        collections_preview[name] = {
            "count": count,
            "latest": (info or {}).get("latest", [])[:2],
        }

    return {
        "scope": {"channel_id": channel_id, "thread_id": thread_id},
        "total_entries": int(summary.get("total_count", 0) or 0) if isinstance(summary, dict) else 0,
        "anchor": summary.get("anchor", {}) if isinstance(summary, dict) else {},
        "alerts": summary.get("alerts", {}) if isinstance(summary, dict) else {},
        "collections": collections_preview,
    }


def _parse_sms_user_id(raw_value: str | int | None) -> int | None:
    if raw_value in (None, ""):
        return None
    try:
        parsed = int(raw_value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _serialize_approval(req, now_epoch: int) -> dict:
    age_seconds = int(getattr(req, "age_seconds", 0) or 0)
    created_at = max(0, now_epoch - age_seconds)
    status = "pending"
    if getattr(req, "resolved", False):
        status = "approved" if getattr(req, "approved", False) else "denied"
    elif getattr(req, "is_expired", False):
        status = "expired"
    return {
        "request_id": getattr(req, "request_id", ""),
        "action": getattr(req, "action", ""),
        "target": getattr(req, "target", ""),
        "detail": _preview_text(getattr(req, "detail", ""), limit=220),
        "risk_level": getattr(getattr(req, "risk_level", None), "value", "UNKNOWN"),
        "requester_name": getattr(req, "requester_name", "unknown"),
        "resolver_name": getattr(req, "resolver_name", None),
        "session_id": getattr(req, "session_id", ""),
        "plan_id": getattr(req, "plan_id", ""),
        "task_id": getattr(req, "task_id", ""),
        "age_seconds": age_seconds,
        "status": status,
        "created_at": created_at,
        "resolved_at": now_epoch if status in {"approved", "denied", "expired"} else None,
    }


def _serialize_cli_session(session) -> dict[str, object]:
    """Normalize a local CLI session for dashboard display."""
    return {
        "session_id": getattr(session, "session_id", ""),
        "title": getattr(session, "title", ""),
        "cwd": getattr(session, "cwd", ""),
        "files": list(getattr(session, "files", []) or []),
        "plan_id": getattr(session, "plan_id", ""),
        "task_id": getattr(session, "task_id", ""),
        "status": getattr(session, "status", "active"),
        "created_at": getattr(session, "created_at", ""),
        "updated_at": getattr(session, "updated_at", ""),
        "last_command": getattr(session, "last_command", ""),
        "last_summary": getattr(session, "last_summary", ""),
        "command_count": int(getattr(session, "command_count", 0) or 0),
        "file_edit_count": int(getattr(session, "file_edit_count", 0) or 0),
        "output_count": int(getattr(session, "output_count", 0) or 0),
        "automation_mode": getattr(session, "automation_mode", ""),
        "automation_status": getattr(session, "automation_status", ""),
        "watch_interval_seconds": int(getattr(session, "watch_interval_seconds", 0) or 0),
        "checkpoint_count": int(getattr(session, "checkpoint_count", 0) or 0),
        "last_checkpoint_at": getattr(session, "last_checkpoint_at", ""),
    }


def _serialize_schedule_task(task) -> dict[str, object]:
    """Normalize scheduler task fields for the dashboard APIs."""
    cron_expr = getattr(task, "cron_expression", "") or ""
    interval = int(getattr(task, "interval_minutes", 0) or 0)
    cron_hour = int(getattr(task, "cron_hour", -1) or -1)
    cron_minute = int(getattr(task, "cron_minute", 0) or 0)

    if cron_expr:
        schedule_human = _cron_to_human(cron_expr)
    elif interval > 0:
        if interval >= 1440:
            schedule_human = f"Every {interval // 1440} day(s)"
        elif interval >= 60:
            schedule_human = f"Every {interval // 60} hour(s)"
        else:
            schedule_human = f"Every {interval} min"
    elif cron_hour >= 0:
        schedule_human = f"Daily at {cron_hour:02d}:{cron_minute:02d}"
    else:
        schedule_human = "On demand"

    return {
        "id": getattr(task, "task_id", ""),
        "name": getattr(task, "action", "") or "unknown",
        "interval": interval,
        "cron_expression": cron_expr,
        "cron_hour": cron_hour,
        "cron_minute": cron_minute,
        "schedule_human": schedule_human,
        "prompt": getattr(task, "prompt", "") or "",
        "last_run": getattr(task, "last_run", "") or "",
        "last_result": getattr(task, "last_result", "") or "",
        "next_run": getattr(task, "next_run_str", ""),
        "enabled": bool(getattr(task, "enabled", True)),
        "created_by": getattr(task, "created_by", "") or "",
        "run_count": int(getattr(task, "run_count", 0) or 0),
        "args": str(getattr(task, "args", {}) or {})[:160],
    }


def _preview_text(value: object, *, limit: int = 160) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _humanize_status(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "Unknown"
    return text.replace("_", " ").replace("-", " ").title()


def _plan_step_counts(steps: list[object]) -> tuple[int, int, dict[str, int]]:
    counts = Counter(str(getattr(step, "status", "pending") or "pending") for step in steps)
    completed = 0
    for step in steps:
        if bool(getattr(step, "is_complete", False)) or str(getattr(step, "status", "") or "").strip() in {
            "done",
            "failed",
            "skipped",
        }:
            completed += 1
    return completed, len(steps), dict(counts)


def _serialize_plan_step(step) -> dict[str, object]:
    return {
        "num": _safe_non_negative_int(getattr(step, "num", 0), default=0),
        "description": str(getattr(step, "description", "") or ""),
        "status": str(getattr(step, "status", "pending") or "pending"),
        "status_label": _humanize_status(getattr(step, "status", "pending")),
        "output_preview": _preview_text(getattr(step, "output", ""), limit=240),
        "worker_id": str(getattr(step, "worker_id", "") or ""),
        "depends_on": list(getattr(step, "depends_on", []) or []),
    }


def _serialize_plan(
    plan, *, detail: bool = False, linked_sessions: list[dict[str, object]] | None = None
) -> dict[str, object]:
    steps = list(getattr(plan, "steps", []) or [])
    lessons = list(getattr(plan, "lessons", []) or [])
    context = getattr(plan, "context", {}) or {}
    completed, total, step_counts = _plan_step_counts(steps)
    current_step = next((step for step in steps if str(getattr(step, "status", "") or "") == "in-progress"), None)

    payload = {
        "plan_id": str(getattr(plan, "plan_id", "") or ""),
        "goal": str(getattr(plan, "goal", "") or ""),
        "status": str(getattr(plan, "status", "in-progress") or "in-progress"),
        "status_label": _humanize_status(getattr(plan, "status", "in-progress")),
        "initiator": str(getattr(plan, "initiator", "") or ""),
        "channel_id": _safe_non_negative_int(getattr(plan, "channel_id", 0), default=0),
        "created_at": str(getattr(plan, "created_at", "") or ""),
        "updated_at": str(getattr(plan, "updated_at", "") or ""),
        "progress": {
            "completed": completed,
            "total": total,
            "label": f"{completed}/{total}" if total else "0/0",
        },
        "step_counts": step_counts,
        "context_keys": sorted(str(key) for key in context.keys())[:20],
        "lessons_count": len(lessons),
        "linked_session_count": len(linked_sessions or []),
        "current_step": _serialize_plan_step(current_step) if current_step is not None else None,
    }
    if detail:
        payload["steps"] = [_serialize_plan_step(step) for step in steps]
        payload["lessons"] = [str(item) for item in lessons[:20]]
        payload["context"] = {str(key): _preview_text(value, limit=400) for key, value in list(context.items())[:20]}
    return payload


def _load_plan_object(plan_id: str):
    normalized = str(plan_id or "").strip()
    if not normalized:
        return None
    try:
        from agent_loop import load_plan as load_agent_plan
    except ImportError as exc:
        log.debug("Plan loader unavailable for %s: %s", normalized, exc)
        return None
    try:
        return load_agent_plan(normalized)
    except (AttributeError, ValueError, RuntimeError) as exc:
        log.debug("Failed to load plan %s: %s", normalized, exc)
        return None


def _list_plan_objects(status_filter: str = "in-progress") -> list[object]:
    normalized = str(status_filter or "in-progress").strip() or "in-progress"
    try:
        from agent_loop import list_plans as list_agent_plans
    except ImportError as exc:
        log.debug("Plan listing unavailable: %s", exc)
        return []
    try:
        return list(list_agent_plans(normalized))
    except (AttributeError, ValueError, RuntimeError) as exc:
        log.debug("Plan listing failed for %s: %s", normalized, exc)
        return []


def _linked_session_payloads(
    *,
    plan_id: str = "",
    task_id: str = "",
    sessions: list[dict[str, object]] | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    normalized_plan = str(plan_id or "").strip()
    normalized_task = str(task_id or "").strip()
    source_sessions = sessions
    if source_sessions is None:
        source_sessions = [_serialize_cli_session(session) for session in list_cli_sessions(limit=max(limit, 200))]

    matches: list[dict[str, object]] = []
    for session in source_sessions:
        session_plan = str(session.get("plan_id", "") or "").strip()
        session_task = str(session.get("task_id", "") or "").strip()
        if normalized_plan and session_plan == normalized_plan:
            matches.append(session)
        elif normalized_task and session_task == normalized_task:
            matches.append(session)
    return matches[: max(1, limit)]


def _mission_status_group(status: str) -> str:
    mapping = {
        "backlog": "pending",
        "in_progress": "active",
        "review": "review",
        "done": "done",
        "permanent": "active",
    }
    return mapping.get(str(status or "").strip(), "pending")


def _load_mission_control_tasks() -> list[dict[str, object]]:
    try:
        from mission_control import _load_tasks as load_mission_tasks

        payload = load_mission_tasks()
        records = payload.get("tasks", []) if isinstance(payload, dict) else []
        return [item for item in records if isinstance(item, dict)]
    except (ImportError, AttributeError, KeyError, RuntimeError) as exc:
        log.debug("Mission Control tasks unavailable: %s", exc)
        return []


def _serialize_mission_control_task(
    task: dict[str, object],
    *,
    detail: bool = False,
    linked_sessions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    subtasks = [item for item in (task.get("subtasks", []) or []) if isinstance(item, dict)]
    comments = [item for item in (task.get("comments", []) or []) if isinstance(item, dict)]
    completed_subtasks = sum(1 for item in subtasks if item.get("done"))
    status = str(task.get("status", "backlog") or "backlog")
    task_id = str(task.get("id", "") or "").strip()
    last_comment = comments[-1] if comments else {}
    updated_at = str(
        task.get("updated_at")
        or task.get("updatedAt")
        or last_comment.get("created_at")
        or last_comment.get("createdAt")
        or last_comment.get("timestamp")
        or ""
    )

    payload = {
        "id": task_id,
        "source": "mission-control",
        "source_label": "Mission Control",
        "title": str(task.get("title", task_id or "Untitled") or task_id or "Untitled"),
        "status": status,
        "status_label": _humanize_status(status),
        "status_group": _mission_status_group(status),
        "summary": _preview_text(task.get("description") or last_comment.get("text") or "", limit=180),
        "description": _preview_text(task.get("description") or "", limit=400)
        if detail
        else _preview_text(task.get("description") or "", limit=180),
        "priority": str(task.get("priority", "") or ""),
        "priority_label": _humanize_status(task.get("priority", "")) if task.get("priority") else "",
        "created_at": str(task.get("created_at") or task.get("createdAt") or ""),
        "updated_at": updated_at,
        "subtask_progress": {
            "completed": completed_subtasks,
            "total": len(subtasks),
            "label": f"{completed_subtasks}/{len(subtasks)}" if subtasks else "0/0",
        },
        "comments_count": len(comments),
        "enabled": True,
        "schedule_human": "",
        "next_run": "",
        "run_count": 0,
        "linked_session_count": len(linked_sessions or []),
    }
    if detail:
        payload["subtasks"] = [
            {
                "title": str(item.get("title", "") or ""),
                "done": bool(item.get("done")),
            }
            for item in subtasks
        ]
        payload["comments_preview"] = [
            {
                "author": str(item.get("author", "") or ""),
                "text": _preview_text(item.get("text") or "", limit=200),
            }
            for item in comments[-5:]
        ]
    return payload


def _serialize_scheduler_control_task(
    task,
    *,
    detail: bool = False,
    linked_sessions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    payload = _serialize_schedule_task(task)
    status = "paused"
    if payload.get("enabled"):
        status = "overdue" if str(payload.get("next_run", "") or "").strip().lower() == "overdue" else "scheduled"
    control_payload = {
        **payload,
        "source": "scheduled",
        "source_label": "Scheduler",
        "title": str(payload.get("name", payload.get("id", "")) or payload.get("id", "")),
        "status": status,
        "status_label": _humanize_status(status),
        "status_group": "paused" if status == "paused" else ("overdue" if status == "overdue" else "active"),
        "summary": _preview_text(
            payload.get("prompt") or payload.get("args") or payload.get("last_result") or "", limit=180
        ),
        "created_at": str(getattr(task, "created_at", "") or ""),
        "updated_at": str(payload.get("last_run") or getattr(task, "created_at", "") or ""),
        "linked_session_count": len(linked_sessions or []),
    }
    if detail:
        control_payload["last_result_preview"] = _preview_text(payload.get("last_result") or "", limit=240)
    return control_payload


def _normalize_task_source(source: object) -> str:
    normalized = str(source or "").strip().lower()
    if normalized in {"mission", "mission-control", "mission_control", "mc"}:
        return "mission-control"
    if normalized in {"scheduled", "schedule", "scheduler"}:
        return "scheduled"
    return ""


def _resolve_task_status(
    task_id: str,
    *,
    source: str = "",
    detail: bool = False,
    sessions: list[dict[str, object]] | None = None,
) -> dict[str, object] | None:
    normalized_id = str(task_id or "").strip()
    if not normalized_id:
        return None

    linked_sessions = _linked_session_payloads(task_id=normalized_id, sessions=sessions, limit=20)

    def _mission_lookup() -> dict[str, object] | None:
        task = next(
            (item for item in _load_mission_control_tasks() if str(item.get("id", "") or "").strip() == normalized_id),
            None,
        )
        if task is None:
            return None
        return _serialize_mission_control_task(task, detail=detail, linked_sessions=linked_sessions)

    def _schedule_lookup() -> dict[str, object] | None:
        try:
            from scheduler import scheduler
        except ImportError as exc:
            log.debug("Scheduler task lookup unavailable for %s: %s", normalized_id, exc)
            return None
        try:
            task = scheduler.get(normalized_id)
            if task is None and hasattr(scheduler, "list_tasks"):
                task = next(
                    (item for item in scheduler.list_tasks() if getattr(item, "task_id", "") == normalized_id), None
                )
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.debug("Failed to resolve scheduled task %s: %s", normalized_id, exc)
            return None
        if task is None:
            return None
        return _serialize_scheduler_control_task(task, detail=detail, linked_sessions=linked_sessions)

    preferred = _normalize_task_source(source)
    if preferred == "mission-control":
        return _mission_lookup()
    if preferred == "scheduled":
        return _schedule_lookup()
    if normalized_id.startswith("sched-"):
        return _schedule_lookup() or _mission_lookup()
    return _mission_lookup() or _schedule_lookup()


def _list_unified_task_statuses(
    *,
    limit: int = 40,
    source_filter: str = "all",
    sessions: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    normalized_source = _normalize_task_source(source_filter)
    items: list[dict[str, object]] = []

    if normalized_source in {"", "mission-control"}:
        for record in _load_mission_control_tasks():
            task_id = str(record.get("id", "") or "").strip()
            linked = _linked_session_payloads(task_id=task_id, sessions=sessions, limit=20) if task_id else []
            items.append(_serialize_mission_control_task(record, linked_sessions=linked))

    if normalized_source in {"", "scheduled"}:
        try:
            from scheduler import scheduler

            for task in scheduler.list_tasks():
                task_id = str(getattr(task, "task_id", "") or "").strip()
                linked = _linked_session_payloads(task_id=task_id, sessions=sessions, limit=20) if task_id else []
                items.append(_serialize_scheduler_control_task(task, linked_sessions=linked))
        except (ImportError, AttributeError, RuntimeError) as exc:
            log.debug("Scheduled task list unavailable: %s", exc)

    priority = {
        "overdue": 0,
        "active": 1,
        "review": 2,
        "pending": 3,
        "paused": 4,
        "done": 5,
    }
    items.sort(
        key=lambda item: (
            priority.get(str(item.get("status_group", "") or ""), 9),
            str(item.get("updated_at", "") or ""),
            str(item.get("title", "") or ""),
        )
    )
    return items[: max(1, limit)]


_WATCH_INSIGHTS_LIMIT = 5


def _build_watch_insights(watch_state: dict) -> dict[str, object]:
    """Derive compact, operator-friendly watch insight fields from a watch_state dict.

    Returns recent completed checkpoints, retry history, the latest checkpoint
    summary, and the active phase/attempt so operators can scan progress quickly.
    """
    if not isinstance(watch_state, dict):
        return {}

    raw_checkpoints = [cp for cp in list(watch_state.get("checkpoints") or []) if isinstance(cp, dict)]
    recent_checkpoints: list[dict[str, object]] = [
        {
            "poll": int(cp.get("poll") or 0),
            "status": str(cp.get("status") or ""),
            "summary": str(cp.get("summary") or cp.get("last_message") or "")[:200],
            "phase": str(cp.get("phase") or ""),
            "completed_at": str(cp.get("completed_at") or cp.get("updated_at") or ""),
            "attempt_count": len(list(cp.get("attempts") or [])),
        }
        for cp in raw_checkpoints[-_WATCH_INSIGHTS_LIMIT:]
    ]

    raw_retries = [r for r in list(watch_state.get("retry_history") or []) if isinstance(r, dict)]
    retry_history: list[dict[str, object]] = [
        {
            "poll": int(r.get("poll") or 0),
            "attempt": int(r.get("attempt") or 0),
            "error": str(r.get("error") or "")[:200],
            "transient": bool(r.get("transient")),
            "created_at": str(r.get("created_at") or ""),
        }
        for r in raw_retries[-_WATCH_INSIGHTS_LIMIT:]
    ]

    active = watch_state.get("active_checkpoint")
    active_phase = ""
    active_attempt = 0
    if isinstance(active, dict) and active:
        active_phase = str(active.get("phase") or "")
        active_attempt = len(list(active.get("attempts") or []))

    return {
        "recent_checkpoints": recent_checkpoints,
        "retry_history": retry_history,
        "latest_checkpoint_summary": str(watch_state.get("last_summary") or "")[:200],
        "active_phase": active_phase,
        "active_attempt": active_attempt,
        "poll_count": int(watch_state.get("poll_count") or 0),
        "retry_limit": int(watch_state.get("retry_limit") or 0),
    }


def _list_enriched_session_payloads(*, limit: int = 20, lookup_limit: int = 200) -> list[dict[str, object]]:
    raw_sessions = list_cli_sessions(limit=max(limit, lookup_limit))
    session_payloads = [_serialize_cli_session(session) for session in raw_sessions]
    plan_cache: dict[str, dict[str, object] | None] = {}
    task_cache: dict[str, dict[str, object] | None] = {}

    enriched: list[dict[str, object]] = []
    for payload in session_payloads[: max(1, limit)]:
        item = dict(payload)
        plan_id = str(item.get("plan_id", "") or "").strip()
        task_id = str(item.get("task_id", "") or "").strip()
        if plan_id:
            if plan_id not in plan_cache:
                plan_obj = _load_plan_object(plan_id)
                plan_cache[plan_id] = _serialize_plan(plan_obj) if plan_obj is not None else None
            plan_summary = plan_cache.get(plan_id)
            if plan_summary:
                item["plan_goal"] = plan_summary.get("goal", "")
                item["plan_status"] = plan_summary.get("status", "")
                item["plan_progress"] = plan_summary.get("progress", {}).get("label", "")
        if task_id:
            if task_id not in task_cache:
                task_cache[task_id] = _resolve_task_status(task_id, sessions=session_payloads)
            task_summary = task_cache.get(task_id)
            if task_summary:
                item["task_title"] = task_summary.get("title", "")
                item["task_status"] = task_summary.get("status", "")
                item["task_source"] = task_summary.get("source", "")
        watch_state = load_cli_watch_state(str(item.get("session_id", "") or "").strip())
        if isinstance(watch_state, dict):
            item["watch_status"] = str(watch_state.get("status", "") or "")
            item["last_error"] = str(watch_state.get("last_error", "") or "")
            item["failure_count"] = _safe_non_negative_int(watch_state.get("failure_count"), default=0)
            item["consecutive_failures"] = _safe_non_negative_int(
                watch_state.get("consecutive_failures"),
                default=0,
            )
            item["pending_intervention_count"] = sum(
                1
                for entry in list(watch_state.get("interventions") or [])
                if isinstance(entry, dict) and str(entry.get("status") or "") == "pending"
            )
            progress_log = [entry for entry in list(watch_state.get("progress_log") or []) if isinstance(entry, dict)]
            if progress_log:
                item["last_progress_message"] = str(progress_log[-1].get("message", "") or "")
            active_checkpoint = watch_state.get("active_checkpoint")
            if isinstance(active_checkpoint, dict) and active_checkpoint:
                item["active_phase"] = str(active_checkpoint.get("phase", "") or "")
        enriched.append(item)
    return enriched


def _list_serialized_plans(
    *,
    limit: int = 10,
    status_filter: str = "in-progress",
    sessions: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for plan in _list_plan_objects(status_filter):
        plan_id = str(getattr(plan, "plan_id", "") or "").strip()
        linked = _linked_session_payloads(plan_id=plan_id, sessions=sessions, limit=20) if plan_id else []
        items.append(_serialize_plan(plan, linked_sessions=linked))
    items.sort(key=lambda item: str(item.get("updated_at", "") or ""), reverse=True)
    return items[: max(1, limit)]


async def api_approvals_handler(request: web.Request) -> web.Response:
    """List pending and recent approval requests for dashboard tables."""
    from approval_store import approval_store

    limit_raw = request.query.get("limit", "40")
    try:
        limit = max(1, min(int(limit_raw), 100))
    except ValueError:
        limit = 40

    now_epoch = int(time.time())
    pending = [_serialize_approval(req, now_epoch) for req in approval_store.list_pending()][:limit]

    # Include resolved/expired requests for "recent history". This uses the in-memory
    # store so history is process-lifetime scoped.
    all_requests = list(getattr(approval_store, "_pending", {}).values())
    recent = sorted(
        (_serialize_approval(req, now_epoch) for req in all_requests),
        key=lambda item: int(item.get("created_at") or 0),
        reverse=True,
    )[:limit]

    return web.json_response({"pending": pending, "recent": recent})


async def api_approval_decision_handler(request: web.Request) -> web.Response:
    """Resolve a pending approval request from dashboard actions."""
    from approval_store import approval_store

    request_id = str(request.match_info.get("request_id", "")).strip()
    if not request_id:
        return web.json_response({"ok": False, "error": "missing request_id"}, status=400)

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"ok": False, "error": "Invalid JSON payload"}, status=400)

    approved = bool(payload.get("approved"))
    resolver_name = str(payload.get("resolver_name") or request.headers.get("X-OpenClaw-Actor") or "dashboard").strip()[
        :120
    ]
    resolved = approval_store.resolve(
        request_id=request_id,
        approved=approved,
        resolver_id=0,
        resolver_name=resolver_name or "dashboard",
    )
    if resolved is None:
        return web.json_response(
            {"ok": False, "reason": "Request was not found, expired, or already resolved"}, status=404
        )

    now_epoch = int(time.time())
    return web.json_response({"ok": True, "request": _serialize_approval(resolved, now_epoch)})


async def api_plans_handler(request: web.Request) -> web.Response:
    """List persisted agent-loop plans for dashboard control-plane views."""
    limit_raw = request.query.get("limit", "10")
    status_filter = str(request.query.get("status", "in-progress") or "in-progress").strip() or "in-progress"
    try:
        limit = max(1, min(int(limit_raw), 100))
    except ValueError:
        limit = 10

    session_payloads = _list_enriched_session_payloads(limit=max(limit, 50))
    plans = _list_serialized_plans(limit=limit, status_filter=status_filter, sessions=session_payloads)
    return web.json_response(
        {
            "plans": plans,
            "meta": {
                "count": len(plans),
                "status": status_filter,
                "statuses": dict(Counter(str(item.get("status", "") or "") for item in plans)),
            },
        }
    )


async def api_plan_detail_handler(request: web.Request) -> web.Response:
    """Return full detail for a persisted plan plus linked session/task context."""
    plan_id = str(request.match_info.get("plan_id", "")).strip()
    if not plan_id:
        return web.json_response({"ok": False, "error": "Missing plan_id"}, status=400)

    plan = _load_plan_object(plan_id)
    if plan is None:
        return web.json_response({"ok": False, "error": f"Plan '{plan_id}' was not found"}, status=404)

    session_payloads = _list_enriched_session_payloads(limit=200)
    linked_sessions = _linked_session_payloads(plan_id=plan_id, sessions=session_payloads, limit=20)

    linked_tasks: list[dict[str, object]] = []
    seen_task_ids: set[str] = set()
    for session in linked_sessions:
        task_id = str(session.get("task_id", "") or "").strip()
        if not task_id or task_id in seen_task_ids:
            continue
        resolved = _resolve_task_status(task_id, sessions=session_payloads)
        if resolved is not None:
            linked_tasks.append(resolved)
            seen_task_ids.add(task_id)

    return web.json_response(
        {
            "ok": True,
            "plan": _serialize_plan(plan, detail=True, linked_sessions=linked_sessions),
            "linked_sessions": linked_sessions,
            "linked_tasks": linked_tasks,
        }
    )


async def api_task_status_handler(request: web.Request) -> web.Response:
    """Return Mission Control and scheduler tasks in one normalized dashboard shape."""
    limit_raw = request.query.get("limit", "20")
    source_filter = str(request.query.get("source", "all") or "all").strip() or "all"
    try:
        limit = max(1, min(int(limit_raw), 200))
    except ValueError:
        limit = 20

    session_payloads = _list_enriched_session_payloads(limit=200)
    tasks = _list_unified_task_statuses(limit=limit, source_filter=source_filter, sessions=session_payloads)
    return web.json_response(
        {
            "tasks": tasks,
            "meta": {
                "count": len(tasks),
                "source": source_filter,
                "sources": dict(Counter(str(item.get("source", "") or "") for item in tasks)),
            },
        }
    )


async def api_task_status_detail_handler(request: web.Request) -> web.Response:
    """Return normalized detail for a Mission Control or scheduled task."""
    source = str(request.match_info.get("source", "")).strip()
    task_id = str(request.match_info.get("task_id", "")).strip()
    if not task_id:
        return web.json_response({"ok": False, "error": "Missing task_id"}, status=400)

    session_payloads = _list_enriched_session_payloads(limit=200)
    task = _resolve_task_status(task_id, source=source, detail=True, sessions=session_payloads)
    if task is None:
        return web.json_response({"ok": False, "error": f"Task '{task_id}' was not found"}, status=404)

    linked_sessions = _linked_session_payloads(task_id=task_id, sessions=session_payloads, limit=20)
    linked_plans: list[dict[str, object]] = []
    seen_plan_ids: set[str] = set()
    for session in linked_sessions:
        plan_id = str(session.get("plan_id", "") or "").strip()
        if not plan_id or plan_id in seen_plan_ids:
            continue
        plan = _load_plan_object(plan_id)
        if plan is not None:
            linked_plans.append(
                _serialize_plan(
                    plan,
                    linked_sessions=_linked_session_payloads(plan_id=plan_id, sessions=session_payloads, limit=20),
                )
            )
            seen_plan_ids.add(plan_id)

    return web.json_response(
        {
            "ok": True,
            "task": task,
            "linked_sessions": linked_sessions,
            "linked_plans": linked_plans,
        }
    )


async def api_agent_sessions_handler(request: web.Request) -> web.Response:
    """List local terminal-agent sessions for dashboard supervision."""
    limit_raw = request.query.get("limit", "20")
    try:
        limit = max(1, min(int(limit_raw), 100))
    except ValueError:
        limit = 20

    payload = _list_enriched_session_payloads(limit=limit)
    return web.json_response(
        {
            "sessions": payload,
            "meta": {
                "count": len(payload),
                "active": sum(
                    1
                    for item in payload
                    if item.get("automation_status") in {"watching", "running", "waiting"}
                    or item.get("status") == "active"
                ),
            },
        }
    )


async def api_agent_session_detail_handler(request: web.Request) -> web.Response:
    """Return a full local CLI session export."""
    session_id = str(request.match_info.get("session_id", "")).strip()
    if not session_id:
        return web.json_response({"ok": False, "error": "Missing session_id"}, status=400)
    try:
        payload = export_cli_session(session_id)
    except FileNotFoundError:
        return web.json_response({"ok": False, "error": f"Session '{session_id}' was not found"}, status=404)

    session_payload = dict(payload.get("session") or {})
    session_payloads = _list_enriched_session_payloads(limit=200)
    plan_id = str(session_payload.get("plan_id", "") or "").strip()
    task_id = str(session_payload.get("task_id", "") or "").strip()

    plan_summary = None
    if plan_id:
        plan = _load_plan_object(plan_id)
        if plan is not None:
            linked_plan_sessions = _linked_session_payloads(plan_id=plan_id, sessions=session_payloads, limit=20)
            plan_summary = _serialize_plan(plan, detail=True, linked_sessions=linked_plan_sessions)
            session_payload["plan_goal"] = plan_summary.get("goal", "")
            session_payload["plan_status"] = plan_summary.get("status", "")
            session_payload["plan_progress"] = plan_summary.get("progress", {}).get("label", "")
    else:
        linked_plan_sessions = []

    task_summary = _resolve_task_status(task_id, detail=True, sessions=session_payloads) if task_id else None
    if task_summary is not None:
        session_payload["task_title"] = task_summary.get("title", "")
        session_payload["task_status"] = task_summary.get("status", "")
        session_payload["task_source"] = task_summary.get("source", "")

    raw_watch_state = payload.get("watch_state")
    watch_insights = _build_watch_insights(raw_watch_state) if isinstance(raw_watch_state, dict) else {}

    return web.json_response(
        {
            "ok": True,
            **payload,
            "session": session_payload,
            "plan": plan_summary,
            "task": task_summary,
            "linked_plan_sessions": linked_plan_sessions,
            "supports_interventions": bool(raw_watch_state),
            "watch_insights": watch_insights,
        }
    )


async def api_agent_session_intervention_handler(request: web.Request) -> web.Response:
    """Queue a safe watch/session intervention from the dashboard."""
    session_id = str(request.match_info.get("session_id", "")).strip()
    action = str(request.match_info.get("action", "")).strip().lower()
    if not session_id:
        return web.json_response({"ok": False, "error": "Missing session_id"}, status=400)
    if not action:
        return web.json_response({"ok": False, "error": "Missing action"}, status=400)

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}

    try:
        require_cli_session(session_id)
    except FileNotFoundError:
        return web.json_response({"ok": False, "error": f"Session '{session_id}' was not found"}, status=404)

    watch_state = load_cli_watch_state(session_id)
    if not isinstance(watch_state, dict):
        return web.json_response({"ok": False, "error": "This session has no watch state to control"}, status=409)

    status = str(watch_state.get("status", "") or "").strip().lower()
    if status in {"completed", "interrupted"}:
        return web.json_response(
            {"ok": False, "error": f"Watch session is already {status or 'finished'}"},
            status=409,
        )

    actor = str(
        payload.get("actor") or request.headers.get("X-OpenClaw-Actor") or payload.get("resolver_name") or "dashboard"
    ).strip()[:120]
    reason = str(payload.get("reason") or "").strip()[:240]

    try:
        intervention = queue_cli_watch_intervention(
            session_id,
            action=action,
            actor=actor or "dashboard",
            reason=reason,
        )
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    refreshed = export_cli_session(session_id)
    refreshed_watch = refreshed.get("watch_state") if isinstance(refreshed, dict) else {}
    pending_count = sum(
        1
        for entry in list((refreshed_watch or {}).get("interventions") or [])
        if isinstance(entry, dict) and str(entry.get("status") or "") == "pending"
    )
    return web.json_response(
        {
            "ok": True,
            "intervention": intervention,
            "pending_count": pending_count,
            "session": refreshed.get("session", {}) if isinstance(refreshed, dict) else {},
            "watch_state": refreshed_watch if isinstance(refreshed_watch, dict) else {},
        }
    )


async def api_status_handler(request):
    """Return connectivity status for all backends."""
    from config import TIMEOUT_FAST, cfg

    checks = {}

    # Docker
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "info",
            "--format",
            "{{.ContainersRunning}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_FAST)
        checks["docker"] = {"status": "ok", "containers": stdout.decode().strip()}
    except (OSError, asyncio.TimeoutError) as exc:
        log.debug("Docker status check failed: %s", exc)
        checks["docker"] = {"status": "down"}

    # Ollama
    try:
        session = await _dashboard_sessions.get()
        async with session.get(f"{cfg.ollama_url}/api/tags", timeout=aiohttp.ClientTimeout(total=TIMEOUT_FAST)) as resp:
            checks["ollama"] = {"status": "ok" if resp.status == 200 else "down"}
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        log.debug("Ollama status check failed: %s", exc)
        checks["ollama"] = {"status": "down"}

    # Hermes
    try:
        _reader, _writer = await asyncio.wait_for(
            asyncio.open_connection("192.168.1.93", 22), timeout=2.0
        )
        _writer.close()
        try:
            await _writer.wait_closed()
        except Exception:
            pass
        checks["hermes"] = {"status": "ok"}
    except Exception:
        checks["hermes"] = {"status": "down"}

    # Search provider
    perplexity_key = cfg.perplexity_api_key
    firecrawl_key = cfg.firecrawl_api_key
    tavily_key = cfg.tavily_api_key
    if perplexity_key:
        cascade = (
            "Perplexity → Firecrawl → Tavily → DDG → Bing Lite"
            if firecrawl_key
            else "Perplexity → Tavily → DDG → Bing Lite"
        )
        checks["search_provider"] = {"status": "ok", "active": "Perplexity AI", "cascade": cascade}
    elif firecrawl_key:
        checks["search_provider"] = {
            "status": "ok",
            "active": "Firecrawl",
            "cascade": "Firecrawl → Tavily → DDG → Bing Lite",
        }
    elif tavily_key:
        checks["search_provider"] = {"status": "ok", "active": "Tavily", "cascade": "Tavily → DDG → Bing Lite"}
    else:
        checks["search_provider"] = {"status": "ok", "active": "DuckDuckGo", "cascade": "DDG → Bing Lite"}

    # Firecrawl tier indicator
    checks["firecrawl"] = {
        "status": "ok" if firecrawl_key else "not_configured",
        "tier": "Free (500 pages/mo)" if firecrawl_key else "Not configured",
        "configured": bool(firecrawl_key),
    }

    # Content extraction chain (Jina Reader is free / no key required)
    checks["content_extraction"] = _content_extraction_check()

    # Copilot proxy
    proxy_url = cfg.copilot_proxy_url
    if proxy_url:
        try:
            session = await _dashboard_sessions.get()
            token = cfg.copilot_proxy_token
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            async with session.get(
                f"{proxy_url}/models", headers=headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT_FAST)
            ) as resp:
                checks["copilot_proxy"] = {"status": "ok" if resp.status == 200 else "down"}
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.debug("Copilot proxy check failed: %s", exc)
            checks["copilot_proxy"] = {"status": "down"}
    else:
        checks["copilot_proxy"] = {"status": "not_configured"}

    # Patreon (MonsterVision) — enhanced health check
    try:
        from patreon_monitor import get_patreon_checker

        checker = get_patreon_checker()
        health = await checker.check_health()

        # Map status to dashboard format
        from patreon_monitor import PatreonHealthStatus

        # Base status response — fields live in health.metadata, not as direct attributes
        meta = health.metadata
        api_data_cached = meta.get("api_data") or {}
        patreon_data = {
            "cookie_age_hours": meta.get("cookie_age_hours", -1),
            "hours_since_download": meta.get("hours_since_download", -1),
            "downloaded_count": api_data_cached.get("downloaded", meta.get("downloaded_count", 0)),
            "total_count": api_data_cached.get("total", meta.get("total_count", 0)),
            "pending_count": api_data_cached.get("pending", meta.get("pending_count", 0)),
            "auto_recovery_active": meta.get("auto_recovery_active", False),
        }

        if health.status == PatreonHealthStatus.OK:
            patreon_data["status"] = "ok"
            patreon_data["detail"] = "healthy"
        elif health.status == PatreonHealthStatus.WARNING:
            # Use the primary issue as detail
            detail = health.issues[0] if health.issues else "attention needed"
            patreon_data["status"] = "no_key"
            patreon_data["detail"] = detail[:50]
        elif health.status == PatreonHealthStatus.CRITICAL:
            detail = health.issues[0] if health.issues else "critical"
            patreon_data["status"] = "down"
            patreon_data["detail"] = detail[:50]
        else:
            patreon_data["status"] = "down"
            patreon_data["detail"] = "unknown"

        checks["patreon"] = patreon_data

    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        log.debug("Patreon health check failed: %s", exc)
        checks["patreon"] = {"status": "down", "detail": "unreachable"}
    except Exception as exc:  # broad: intentional
        log.debug("Patreon check error: %s", exc)
        checks["patreon"] = {"status": "down"}

    return web.json_response(checks)


async def api_github_activity_handler(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    import json as _json
    import os

    cached = _cache_get("github_activity")
    if cached is not None:
        return web.Response(content_type="application/json", text=json.dumps(cached))

    token = os.environ.get("GITHUB_TOKEN", "")
    repos_raw = os.environ.get("GITHUB_DEFAULT_REPOS", "")

    if not token:
        return web.json_response({"error": "GITHUB_TOKEN not set", "commits": [], "prs": [], "repos": []})

    repos = [repo.strip() for repo in repos_raw.split(",") if repo.strip()] if repos_raw else []
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    result: dict[str, list[dict[str, object]] | list[str]] = {"commits": [], "prs": [], "repos": repos}

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for repo in repos[:5]:
            try:
                async with session.get(
                    f"https://api.github.com/repos/{repo}/commits?per_page=3",
                    headers=headers,
                ) as resp:
                    if resp.status == 200:
                        commits = _json.loads(await resp.text())
                        for commit in commits:
                            result["commits"].append(
                                {
                                    "repo": repo,
                                    "sha": str(commit.get("sha", ""))[:7],
                                    "message": str(((commit.get("commit") or {}).get("message") or "").split("\n")[0])[:80],
                                    "author": str((((commit.get("commit") or {}).get("author") or {}).get("name") or "")),
                                    "date": str((((commit.get("commit") or {}).get("author") or {}).get("date") or "")),
                                    "url": str(commit.get("html_url") or ""),
                                }
                            )
            except Exception as e:
                log.warning("api_github_activity_handler error: %s", e)

            try:
                async with session.get(
                    f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=5",
                    headers=headers,
                ) as resp:
                    if resp.status == 200:
                        prs = _json.loads(await resp.text())
                        for pr in prs:
                            result["prs"].append(
                                {
                                    "repo": repo,
                                    "number": pr.get("number"),
                                    "title": str(pr.get("title") or "")[:60],
                                    "url": str(pr.get("html_url") or ""),
                                    "author": str(((pr.get("user") or {}).get("login") or "")),
                                }
                            )
            except Exception as e:
                log.warning("api_github_activity_handler error: %s", e)

    result["commits"].sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    result["commits"] = result["commits"][:10]
    _cache_set("github_activity", result, 300)
    return web.Response(content_type="application/json", text=json.dumps(result))


async def api_sms_settings_handler(request: web.Request) -> web.Response:
    """Get/update dashboard SMS preferences for a specific Discord user."""
    from config import cfg
    from sms_ux import UserSMSPrefs, configure_sms_phone, sms_prefs, status_snapshot

    if request.method == "GET":
        user_id = _parse_sms_user_id(request.query.get("user_id"))
        if user_id is None:
            return web.json_response(
                {
                    "needs_user_id": True,
                    "twilio_enabled": bool(cfg.twilio_enabled),
                }
            )

        prefs = sms_prefs.get(user_id)
        snap = status_snapshot(user_id)
        return web.json_response(
            {
                "user_id": user_id,
                "phone_number": prefs.phone_number,
                "masked_phone": snap["masked_phone"],
                "is_verified": prefs.is_verified,
                "verification_status": prefs.verification_status or "unknown",
                "verification_started_at": prefs.verification_started_at,
                "verified_at": prefs.verified_at,
                "remaining_sends": snap["remaining_sends"],
                "twilio_enabled": bool(cfg.twilio_enabled),
            }
        )

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON payload"}, status=400)

    user_id = _parse_sms_user_id(payload.get("user_id"))
    if user_id is None:
        return web.json_response({"error": "Valid user_id is required"}, status=400)

    phone_number = str(payload.get("phone_number", "")).strip()
    if not phone_number:
        prefs = UserSMSPrefs(user_id=user_id)
        await sms_prefs.update(prefs)
        return web.json_response(
            {
                "ok": True,
                "user_id": user_id,
                "phone_number": "",
                "masked_phone": "not set",
                "is_verified": False,
                "verification_status": "unknown",
            }
        )

    try:
        prefs = await configure_sms_phone(user_id, phone_number)
    except (ValueError, OSError, RuntimeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)

    snap = status_snapshot(user_id)
    return web.json_response(
        {
            "ok": True,
            "user_id": user_id,
            "phone_number": prefs.phone_number,
            "masked_phone": snap["masked_phone"],
            "is_verified": prefs.is_verified,
            "verification_status": prefs.verification_status or "unknown",
            "remaining_sends": snap["remaining_sends"],
        }
    )


async def api_sms_status_handler(request: web.Request) -> web.Response:
    """Return SMS status details for dashboard display."""
    from config import cfg
    from sms_ux import status_snapshot

    user_id = _parse_sms_user_id(request.query.get("user_id"))
    if user_id is None:
        return web.json_response(
            {
                "needs_user_id": True,
                "twilio_enabled": bool(cfg.twilio_enabled),
                "configured": False,
            }
        )

    snap = status_snapshot(user_id)
    return web.json_response(
        {
            "user_id": user_id,
            "configured": bool(snap["phone_number"]),
            "twilio_enabled": bool(cfg.twilio_enabled),
            **snap,
        }
    )


async def api_sms_history_handler(request: web.Request) -> web.Response:
    """Return recent outbound SMS sends for dashboard display."""
    from sms_ux import recent_sends_snapshot

    user_id = _parse_sms_user_id(request.query.get("user_id"))
    if user_id is None:
        return web.json_response({"needs_user_id": True, "sends": []})

    limit_raw = request.query.get("limit", "10")
    try:
        limit = max(1, min(int(limit_raw), 25))
    except ValueError:
        limit = 10

    return web.json_response({"user_id": user_id, "sends": recent_sends_snapshot(user_id, limit=limit)})


async def api_runs_handler(request: web.Request) -> web.Response:
    """Return recent LLM runs (with explainability context) for dashboard timeline."""
    from error_tracker import get_recent_outcomes

    try:
        hours = float(request.query.get("hours", 24))
    except (TypeError, ValueError):
        hours = 24
    try:
        limit = int(request.query.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    entries = get_recent_outcomes(hours=hours, limit=limit)
    runs = []
    for e in reversed(entries):
        explainability = e.get("explainability") if isinstance(e.get("explainability"), dict) else {}
        scope_mode = e.get("scope_mode") or explainability.get("scope_mode")
        lock_mode = e.get("lock_mode") or explainability.get("lock_mode")
        anchor_id = e.get("anchor_id") or explainability.get("anchor_id")
        anchor_age_seconds = e.get("anchor_age") if e.get("anchor_age") is not None else e.get("anchor_age_seconds")
        if anchor_age_seconds is None:
            anchor_age_seconds = explainability.get("anchor_age_seconds")
        if anchor_age_seconds is None:
            anchor_age_seconds = explainability.get("anchor_age")

        effective_profile_values = e.get("profile_values")
        if not isinstance(effective_profile_values, dict) or not effective_profile_values:
            effective_profile_values = e.get("effective_profile")
        if not isinstance(effective_profile_values, dict) or not effective_profile_values:
            effective_profile_values = explainability.get("effective_profile")
        if not isinstance(effective_profile_values, dict):
            effective_profile_values = {}

        run_payload = {
            "timestamp": int(e.get("ts", 0)),
            "trace_id": str(e.get("trace_id", "") or "").strip(),
            "user": e.get("user_id", 0),
            "question": e.get("question", "")[:200],
            "model": e.get("model_used", "unknown"),
            "status": "success" if e.get("success") else "error",
            "error": e.get("error", ""),
            "latency_ms": e.get("latency_ms", 0),
            "routing_notes": e.get("routing_notes", []),
            "tools_called": e.get("tools_called", []),
            "reflected": e.get("reflected", False),
            "scope_mode": scope_mode,
            "lock_mode": lock_mode,
            "anchor_id": anchor_id,
            "anchor_age": anchor_age_seconds,
            "anchor_age_seconds": anchor_age_seconds,
            "profile_values": effective_profile_values,
            "effective_profile_values": effective_profile_values,
            "explainability": {
                "trace_id": str(e.get("trace_id", "") or "").strip(),
                "scope_mode": scope_mode,
                "lock_mode": lock_mode,
                "anchor_id": anchor_id,
                "anchor_age_seconds": anchor_age_seconds,
                "effective_profile_values": effective_profile_values,
            },
        }
        runs.append(run_payload)

    return web.json_response(
        {
            "runs": runs,
            "filters": {
                "status": sorted({r.get("status", "unknown") for r in runs}),
                "models": sorted({str(r.get("model", "unknown")) for r in runs}),
                "users": sorted({str(r.get("user", 0)) for r in runs}),
            },
        }
    )


async def api_quality_eval_handler(request: web.Request) -> web.Response:
    """Return quality-eval scorecards (latest + history + trendlines)."""
    try:
        window_hours = float(request.query.get("hours", "24"))
    except (TypeError, ValueError):
        window_hours = 24.0
    try:
        run_limit = max(20, min(int(request.query.get("run_limit", "250")), 1000))
    except (TypeError, ValueError):
        run_limit = 250
    try:
        history_limit = max(1, min(int(request.query.get("history", "20")), 100))
    except (TypeError, ValueError):
        history_limit = 20
    refresh = str(request.query.get("refresh", "1")).lower() not in {"0", "false", "no"}
    include_calibration = str(request.query.get("calibration", "1")).lower() not in {"0", "false", "no"}

    try:
        from runtime_state import (
            create_quality_eval_scorecard,
            ensure_quality_eval_scorecard,
            list_quality_eval_scorecards,
        )

        latest = (
            ensure_quality_eval_scorecard(window_hours=window_hours, limit=run_limit)
            if refresh
            else create_quality_eval_scorecard(window_hours=window_hours, limit=run_limit, persist=False)
        )
        history = list_quality_eval_scorecards(limit=history_limit)
        if latest.get("scorecard_id") is None:
            history = [latest, *history]

        chronological = list(reversed(history))
        metric_names: set[str] = set()
        for card in chronological:
            metrics = card.get("metrics")
            if isinstance(metrics, dict):
                metric_names.update(str(name) for name in metrics.keys())

        metric_trend: dict[str, list[dict[str, float | int]]] = {}
        for metric_name in sorted(metric_names):
            points: list[dict[str, float | int]] = []
            for card in chronological:
                metrics = card.get("metrics")
                if not isinstance(metrics, dict):
                    continue
                metric = metrics.get(metric_name)
                if not isinstance(metric, dict):
                    continue
                points.append(
                    {
                        "timestamp": float(card.get("timestamp") or 0.0),
                        "rate": float(metric.get("rate") or 0.0),
                        "sample": int(metric.get("sample") or 0),
                    }
                )
            metric_trend[metric_name] = points

        summary_trend = [
            {
                "timestamp": float(card.get("timestamp") or 0.0),
                "rate": float((card.get("summary") or {}).get("rate") or 0.0),
                "sample": int(card.get("sample_size") or 0),
            }
            for card in chronological
        ]

        calibration_payload = (
            _build_offline_quality_calibration_payload()
            if include_calibration
            else {
                "available": False,
                "advisory_only": True,
                "auto_apply": False,
                "drift": {
                    "baseline_available": False,
                    "status": "disabled",
                    "metrics": {},
                    "severity": {"level": "unknown", "severe": False, "score": 0, "reasons": []},
                },
                "recommendations": {"advisory_only": True, "auto_apply": False, "proposals": []},
            }
        )

        return web.json_response(
            {
                "latest": latest,
                "history": history,
                "trend": {
                    "summary": summary_trend,
                    "metrics": metric_trend,
                },
                "calibration": calibration_payload,
            }
        )
    except Exception as exc:  # broad: intentional
        log.debug("Quality eval API failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


def _build_offline_quality_calibration_payload() -> dict[str, object]:
    """Run deterministic offline replay calibration and return advisory-only summary."""
    repo_root = Path(__file__).resolve().parents[2]
    fixtures_path = repo_root / "tests" / "evals" / "fixtures" / "replay_prompts.json"
    baseline_path = repo_root / ".github" / "quality" / "offline_quality_baseline.json"
    try:
        from offline_quality_eval import load_baseline_report, load_replay_fixtures, run_quality_eval

        cases = load_replay_fixtures(fixtures_path)
        baseline = load_baseline_report(baseline_path) if baseline_path.exists() else None
        report = run_quality_eval(cases, baseline=baseline)
        calibration = report.get("calibration")
        if not isinstance(calibration, dict):
            calibration = {}
        drift = calibration.get("drift")
        if not isinstance(drift, dict):
            drift = {"baseline_available": False, "status": "unavailable", "metrics": {}}
        severity = drift.get("severity")
        if not isinstance(severity, dict):
            severity = {"level": "unknown", "severe": False, "score": 0, "reasons": []}
        drift["severity"] = severity
        recommendations = calibration.get("recommendations")
        if not isinstance(recommendations, dict):
            recommendations = {"advisory_only": True, "auto_apply": False, "proposals": []}
        return {
            "available": True,
            "advisory_only": True,
            "auto_apply": False,
            "pass": bool(report.get("pass")),
            "summary": report.get("summary") if isinstance(report.get("summary"), dict) else {},
            "drift": drift,
            "recommendations": recommendations,
        }
    except Exception as exc:  # broad: intentional
        log.debug("Offline quality calibration unavailable: %s", exc)
        return {
            "available": False,
            "advisory_only": True,
            "auto_apply": False,
            "summary": {},
            "drift": {
                "baseline_available": False,
                "status": "unavailable",
                "metrics": {},
                "severity": {"level": "unknown", "severe": False, "score": 0, "reasons": []},
            },
            "recommendations": {"advisory_only": True, "auto_apply": False, "proposals": []},
            "error": str(exc),
        }


async def api_dashboard_handler(request: web.Request) -> web.Response:
    """JSON blob with all dashboard data."""
    bot = request.app.get("bot")
    uptime_s = time.monotonic() - bot.start_time if bot else 0

    from llm import _TOOL_DECLARATIONS, LOCAL_LLM_ENABLED, MODEL_NAME, OLLAMA_MODEL, get_rate_info
    from ontology_skills import ontology_query
    from skills import SKILLS, get_docker_stats, get_system_stats, list_containers

    # Get container status list
    container_text = await list_containers()
    containers = []
    if not container_text.startswith("\u274c"):
        lines = [line.strip() for line in container_text.split("\n") if line.strip() and not line.startswith("NAMES")]
        for line in lines:
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if not parts or len(parts) < 2:
                parts = [p.strip() for p in re.split(r"\s{2,}", line) if p.strip()]

            if len(parts) >= 2:
                name = parts[0]
                status = parts[1]
                is_up = "Up" in status
                containers.append({"name": name, "status": status, "is_up": is_up})

    # Fetch NAS containers (Synology DS920+)
    try:
        from config import cfg as _net_cfg

        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-p",
            str(_net_cfg.nas_ssh_port),
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=no",
            f"{_net_cfg.nas_ssh_user}@{_net_cfg.nas_ip}",
            "/usr/local/bin/docker ps --format '{{.Names}}\t{{.Status}}'",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            for line in stdout.decode().strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    containers.append(
                        {
                            "name": f"{parts[0]} (NAS)",
                            "status": parts[1],
                            "is_up": "Up" in parts[1],
                        }
                    )
    except (OSError, asyncio.TimeoutError) as e:
        log.debug("NAS container fetch failed: %s", e)

    # Get resource stats
    stats_text = await get_docker_stats()
    stats_list = []
    if not stats_text.startswith("\u274c"):
        stat_lines = [ln.strip() for ln in stats_text.split("\n") if ln.strip() and not ln.startswith("NAME")]
        for sl in stat_lines:
            parts = [p.strip() for p in sl.split("\t") if p.strip()]
            if not parts or len(parts) < 2:
                parts = [p.strip() for p in re.split(r"\s{2,}", sl) if p.strip()]

            if len(parts) >= 2:
                stats_list.append(
                    {
                        "name": parts[0],
                        "cpu": parts[1] if len(parts) > 1 else "?",
                        "mem": parts[2] if len(parts) > 2 else "?",
                    }
                )

    # Get server system stats (CPU/MEM/Disk)
    sys_stats_text = await get_system_stats()
    sys_stats = {"cpu": "N/A", "mem": "N/A", "disk": "N/A", "nas_disks": []}
    for line in sys_stats_text.split("\n"):
        if "**CPU**" in line:
            sys_stats["cpu"] = line.split(":", 1)[1].strip()
        elif "Average" in line:
            sys_stats["cpu"] = line.split(":", 1)[1].strip()
        elif "**Memory**" in line:
            sys_stats["mem"] = line.split(":", 1)[1].strip()
        elif "**Disk**" in line:
            sys_stats["disk"] = line.split(":", 1)[1].strip()

    # NAS disk space
    try:
        from maintenance_skills import check_nas_health

        nas_health = await check_nas_health()
        for line in nas_health.split("\n"):
            if "/volume" in line:
                match = re.search(r"\*\*(/volume\d+)\*\*:\s+(.+?\s+used)\s*/\s*(.+?\s+total)\s*\((\d+)%\)", line)
                if match:
                    sys_stats["nas_disks"].append(
                        {
                            "mount": match.group(1),
                            "used": match.group(2).replace(" used", ""),
                            "total": match.group(3).replace(" total", ""),
                            "pct": int(match.group(4)),
                        }
                    )
    except (OSError, ValueError, AttributeError) as exc:
        log.debug("NAS disk stats for dashboard failed: %s", exc)

    # Get ontology facts (limit to recent 5)
    ontology_text = await ontology_query()
    ontology_facts = []
    if not ontology_text.startswith("❌") and "Found" in ontology_text:
        fact_lines = [ln.strip("• ").strip() for ln in ontology_text.split("\n") if ln.strip().startswith("•")]
        ontology_facts = fact_lines[:8]

    from config import cfg as app_cfg

    cfg = _load_config()
    sp = spending_tracker

    skills_list = []
    decl_map = {d["name"]: d.get("description", "") for d in _TOOL_DECLARATIONS}
    for name in sorted(SKILLS.keys()):
        skills_list.append(
            {
                "name": name,
                "description": decl_map.get(name, getattr(SKILLS[name], "__doc__", "") or ""),
            }
        )

    # Build categorized skill data for collapsible dashboard display
    from skills import SKILL_CATEGORIES

    skill_categories = {}
    for cat_name, cat_skills in SKILL_CATEGORIES.items():
        valid = [n for n in sorted(cat_skills) if n in SKILLS]
        if valid:
            skill_categories[cat_name] = [
                {"name": n, "description": decl_map.get(n, getattr(SKILLS[n], "__doc__", "") or "")} for n in valid
            ]

    # Recent activity from audit log
    activity: list[dict] = []
    try:
        from config import cfg as app_cfg

        audit_dir = app_cfg.audit_dir
        if audit_dir.exists():
            log_files = sorted(audit_dir.glob("*.jsonl"), reverse=True)
            raw_entries: list[dict] = []
            for lf in log_files:
                if len(raw_entries) >= 50:
                    break
                try:
                    lines = lf.read_text().strip().split("\n")
                    for line in reversed(lines):
                        if not line.strip():
                            continue
                        try:
                            raw_entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                        if len(raw_entries) >= 50:
                            break
                except OSError:
                    continue
            for entry in raw_entries[:20]:
                activity.append(
                    {
                        "timestamp": entry.get("ts", ""),
                        "user": entry.get("user", "unknown"),
                        "action": entry.get("action", ""),
                        "detail": entry.get("detail", "")[:100],
                        "result": entry.get("result", ""),
                    }
                )
    except (OSError, ValueError) as exc:
        log.debug("Failed to load recent activity: %s", exc)

    # Model usage stats from error journal
    model_usage = {}
    try:
        from error_tracker import get_recent_outcomes

        outcomes = get_recent_outcomes(hours=7 * 24, limit=5000)
        for entry in outcomes:
            model = entry.get("model_used", "")
            if model and model not in ("unknown", "error", "timeout", "none"):
                model = model.replace("models/", "")
                model_usage[model] = model_usage.get(model, 0) + 1
    except (ImportError, OSError, json.JSONDecodeError) as exc:
        log.debug("Model usage stats failed: %s", exc)

    # D-6: 7-day token usage for sparkline
    daily_tokens: list[dict] = []
    try:
        from datetime import datetime, timedelta

        daily_data = sp._data.get("daily", {})
        for i in range(6, -1, -1):
            day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            tokens = daily_data.get(day, {})
            daily_tokens.append(
                {
                    "date": day,
                    "input": tokens.get("input_tokens", 0),
                    "output": tokens.get("output_tokens", 0),
                    "total": tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0),
                }
            )
    except (KeyError, AttributeError, TypeError) as exc:
        log.debug("Daily token stats failed: %s", exc)

    control_plane_sessions = _list_enriched_session_payloads(limit=200)
    recent_sessions = control_plane_sessions[:5]

    payload = {
        "version": VERSION,
        "uptime_seconds": round(uptime_s, 1),
        "bot_user": str(bot.user) if bot and bot.user else None,
        "guilds": len(bot.guilds) if bot else 0,
        "latency_ms": round(bot.latency * 1000, 1) if bot and bot.latency else 0,
        "python": platform.python_version(),
        "slack_sdk": importlib.metadata.version("slack_sdk"),
        "search_provider": "Perplexity AI"
        if app_cfg.perplexity_api_key
        else ("Firecrawl" if app_cfg.firecrawl_api_key else ("Tavily" if app_cfg.tavily_api_key else "DuckDuckGo")),
        "firecrawl_tier": "Free (500 pages/mo)" if app_cfg.firecrawl_api_key else "Not configured",
        "content_extraction": _content_extraction_check(),
        "model": MODEL_NAME,
        "local_model": OLLAMA_MODEL if LOCAL_LLM_ENABLED else None,
        "rate_info": get_rate_info(),
        "github_repo": GITHUB_REPO,
        "containers": containers,
        "stats": stats_list,
        "sys_stats": sys_stats,
        "ontology": ontology_facts,
        "config": {
            "llm": cfg.get("llm", {}),
            "security": cfg.get("security", {}),
            "phase": cfg.get("phase", "?"),
        },
        "spending": {
            "total_cost": round(sp.total_cost, 6),
            "budget_limit": sp.budget_limit,
            "budget_remaining": round(sp.budget_remaining, 6),
            "budget_pct": round(sp.budget_pct_used, 2),
            "total_input_tokens": sp.total_input_tokens,
            "total_output_tokens": sp.total_output_tokens,
            "calls": sp.calls,
            "daily": sp.daily,
            "perplexity": sp._data.get("perplexity", {"calls": 0, "total_cost_usd": 0.0, "daily": {}}),
            "firecrawl": sp._data.get(
                "firecrawl", {"calls": 0, "pages_scraped": 0, "total_cost_usd": 0.0, "daily": {}}
            ),
            "copilot": sp._data.get("copilot", {"calls": 0, "daily": {}}),
            "perplexity_cache": _get_perplexity_cache_stats(),
            "quality_retries": _get_quality_retry_count(),
        },
        "daily_tokens": daily_tokens,
        "skills": skills_list,
        "skill_count": len(skills_list),
        "skill_categories": skill_categories,
        "commands": _command_list(),
        "command_quickstart": _command_quickstart(),
        "activity": activity,
        "model_usage": model_usage,
        "response_stats": get_response_stats(),
        "agent_sessions": recent_sessions,
        "active_plans": _list_serialized_plans(limit=5, sessions=control_plane_sessions),
        "task_statuses": _list_unified_task_statuses(limit=10, sessions=control_plane_sessions),
    }
    return web.json_response(payload)


async def api_memories_handler(request: web.Request) -> web.Response:
    """Return QMD facts, learned rules, and vector store stats."""
    data: dict = {"facts": [], "rules": [], "stats": {}}

    # QMD facts (last 50, newest first)
    try:
        from qmd import qmd_store

        data["facts"] = list(qmd_store._memory[-50:])
        data["facts"].reverse()
    except (ImportError, AttributeError) as exc:
        log.debug("QMD facts load failed: %s", exc)

    # Learned rules (last 20, newest first)
    try:
        from rules_engine import _load_rules

        rules = await _load_rules()
        data["rules"] = rules[-20:]
        data["rules"].reverse()
    except (ImportError, OSError) as exc:
        log.debug("Rules load failed: %s", exc)

    # Vector store collection stats
    try:
        import vector_store

        data["stats"] = await vector_store.get_stats()
    except (ImportError, AttributeError, RuntimeError) as exc:
        log.debug("Vector store stats failed: %s", exc)

    return web.json_response(data)


async def api_channel_memory_inspect_handler(request: web.Request) -> web.Response:
    """Inspect vector memory visibility for a channel/thread scope."""
    try:
        channel_id = _parse_scope_id(
            request.query.get("channel_id"),
            field="channel_id",
            required=True,
        )
        thread_id = _parse_scope_id(
            request.query.get("thread_id"),
            field="thread_id",
            required=False,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    include_anchor = str(request.query.get("include_anchor", "1")).lower() not in {"0", "false", "no"}
    limit_raw = request.query.get("limit", "5")
    try:
        latest_limit = max(1, min(int(limit_raw), 20))
    except ValueError:
        latest_limit = 5

    try:
        import vector_store

        summary = await vector_store.get_scoped_memory_summary(
            channel_id=channel_id,
            thread_id=thread_id,
            latest_limit=latest_limit,
            include_anchor=include_anchor,
        )
        alerts = summary.get("alerts", {}) if isinstance(summary, dict) else {}
        warnings: dict[str, object] = {}
        if isinstance(alerts, dict) and alerts.get("count", 0):
            warnings.update(
                {
                    "scoped_recall_alerts": alerts.get("count", 0),
                    "message": "Potential cross-channel/thread recall leakage was blocked recently.",
                }
            )
        compaction = summary.get("compaction", {}) if isinstance(summary, dict) else {}
        if isinstance(compaction, dict):
            warnings["recent_compactions"] = int(compaction.get("count", 0) or 0)
        if warnings:
            summary["warnings"] = warnings
        return web.json_response(summary)
    except (ImportError, AttributeError, OSError, RuntimeError) as exc:
        log.debug("Channel memory inspect failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def api_channel_memory_action_handler(request: web.Request) -> web.Response:
    """Run scoped channel-memory actions (clear/retrain)."""
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON payload"}, status=400)

    action = str(payload.get("action", "")).strip().lower()
    actor = str(payload.get("actor") or request.headers.get("X-OpenClaw-Actor") or "dashboard").strip()[:120]
    confirm = bool(payload.get("confirm"))
    try:
        channel_id = _parse_scope_id(payload.get("channel_id"), field="channel_id", required=True)
        thread_id = _parse_scope_id(payload.get("thread_id"), field="thread_id", required=False)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    if action not in {"clear", "retrain", "clear_retrain"}:
        return web.json_response({"error": "Unsupported action. Use clear, retrain, or clear_retrain."}, status=400)

    response: dict = {
        "ok": True,
        "action": action,
        "scope": {"channel_id": channel_id, "thread_id": thread_id},
    }
    try:
        if action in {"clear", "clear_retrain"} and not confirm:
            preview = await _build_scope_clear_preview(channel_id=channel_id, thread_id=thread_id)
            return web.json_response(
                {
                    "ok": False,
                    "action": action,
                    "scope": {"channel_id": channel_id, "thread_id": thread_id},
                    "requires_confirmation": True,
                    "message": "Clear action is destructive. Re-submit with confirm=true after reviewing preview.",
                    "preview": preview,
                },
                status=409,
            )

        if action in {"clear", "clear_retrain"}:
            import vector_store

            cleared = await vector_store.clear_scoped_memory(
                channel_id=channel_id,
                thread_id=thread_id,
            )
            response["clear"] = cleared
            _audit_scope_action(
                actor,
                "channel_memory_clear",
                channel_id=channel_id,
                thread_id=thread_id,
                detail={"deleted": cleared.get("deleted", {}), "total_deleted": cleared.get("total_deleted", 0)},
            )

        if action in {"retrain", "clear_retrain"}:
            from dream_cycle import DreamCycle

            cycle = DreamCycle()
            report = await cycle.run()
            response["retrain"] = {
                "triggered": True,
                "report_excerpt": report[:220],
            }
            _audit_scope_action(
                actor,
                "channel_memory_retrain",
                channel_id=channel_id,
                thread_id=thread_id,
                detail={"report_chars": len(report)},
            )

        return web.json_response(response)
    except (ImportError, AttributeError, OSError, RuntimeError) as exc:
        log.debug("Channel memory action failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def api_channel_profile_recommendations_handler(request: web.Request) -> web.Response:
    """List profile recommendations for a specific channel/thread scope."""
    try:
        channel_id_raw = _parse_scope_id(
            request.query.get("channel_id"),
            field="channel_id",
            required=True,
        )
        thread_id_raw = _parse_scope_id(
            request.query.get("thread_id"),
            field="thread_id",
            required=False,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    include_history = str(request.query.get("include_history", "0")).lower() in {"1", "true", "yes"}
    channel_id = int(channel_id_raw) if channel_id_raw else None
    thread_id = int(thread_id_raw) if thread_id_raw else None

    try:
        from runtime_state import (
            get_channel_profile,
            get_channel_profile_usage_signals,
            list_channel_profile_recommendations,
            refresh_channel_profile_recommendations,
        )

        refresh_channel_profile_recommendations(channel_id, thread_id=thread_id)
        recommendations = list_channel_profile_recommendations(
            channel_id,
            thread_id=thread_id,
            include_history=include_history,
        )
        profile = get_channel_profile(channel_id, thread_id=thread_id)
        signals = get_channel_profile_usage_signals(channel_id, thread_id=thread_id)
        return web.json_response(
            {
                "scope": {"channel_id": channel_id_raw, "thread_id": thread_id_raw},
                "profile": profile,
                "signals": signals,
                "recommendations": recommendations,
            }
        )
    except (ImportError, AttributeError, ValueError, RuntimeError) as exc:
        log.debug("Channel profile recommendations API failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def api_channel_profile_recommendation_action_handler(request: web.Request) -> web.Response:
    """Approve/reject/apply/revert a profile recommendation."""
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON payload"}, status=400)

    recommendation_id_raw = payload.get("recommendation_id")
    action = str(payload.get("action", "")).strip().lower()
    actor = str(payload.get("actor") or request.headers.get("X-OpenClaw-Actor") or "dashboard").strip()[:120]

    try:
        recommendation_id = int(recommendation_id_raw)
    except (TypeError, ValueError):
        return web.json_response({"error": "recommendation_id must be an integer"}, status=400)

    try:
        from runtime_state import update_channel_profile_recommendation

        updated = update_channel_profile_recommendation(
            recommendation_id,
            action=action,
            actor=actor,
        )
        _audit_scope_action(
            actor,
            f"channel_profile_recommendation_{action}",
            channel_id=str(updated.get("channel_id", "")),
            thread_id=str(updated.get("thread_id")) if updated.get("thread_id") is not None else None,
            detail={
                "recommendation_id": recommendation_id,
                "status": updated.get("status"),
                "profile_field": updated.get("profile_field"),
                "recommended_value": updated.get("recommended_value"),
            },
        )
        return web.json_response({"ok": True, "recommendation": updated})
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except (ImportError, AttributeError, RuntimeError) as exc:
        log.debug("Channel profile recommendation action failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def api_goals_handler(request):
    """Return active goals for the dashboard."""
    try:
        from goal_tracker import get_active_goals

        goals = get_active_goals()
        return web.json_response({"goals": goals})
    except (ImportError, AttributeError, RuntimeError) as exc:
        log.debug("Goals API failed: %s", exc)
        return web.json_response({"goals": []})


async def api_research_handler(request):
    """Return past research reports for the dashboard."""
    try:
        import vector_store

        col = vector_store._get_collection(vector_store.RESEARCH_COLLECTION)
        if col.count() == 0:
            return web.json_response({"reports": []})

        results = col.get(
            include=["metadatas", "documents"],
            limit=20,
        )

        reports = []
        for i, doc_id in enumerate(results.get("ids", [])):
            meta = results["metadatas"][i] if results.get("metadatas") else {}
            text = results["documents"][i][:200] if results.get("documents") else ""
            reports.append(
                {
                    "id": doc_id,
                    "query": meta.get("query", "Unknown query"),
                    "date": meta.get("added_at", 0),
                    "excerpt": text,
                    "sources": meta.get("sources", ""),
                }
            )

        reports.sort(key=lambda r: r.get("date", 0), reverse=True)
        return web.json_response({"reports": reports[:20]})
    except (ImportError, AttributeError, RuntimeError) as e:
        log.debug("Research API failed: %s", e)
        return web.json_response({"reports": [], "error": str(e)})


async def api_threads_handler(request: web.Request) -> web.Response:
    """Return saved conversation threads for the dashboard."""
    from memory import THREADS_DIR

    threads: list[dict] = []
    if THREADS_DIR.exists():
        for f in sorted(
            THREADS_DIR.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                raw = json.loads(f.read_text())
                history = raw if isinstance(raw, list) else raw.get("history", [])

                preview = ""
                for msg in history[:5]:
                    if msg.get("role") == "user":
                        parts = msg.get("parts", [])
                        preview = " ".join(p for p in parts if isinstance(p, str))[:100]
                        break

                threads.append(
                    {
                        "name": f.stem,
                        "messages": len(history),
                        "preview": preview,
                        "modified": f.stat().st_mtime,
                        "size_kb": round(f.stat().st_size / 1024, 1),
                    }
                )
            except (json.JSONDecodeError, OSError, KeyError) as exc:
                log.debug("Thread file parse failed %s: %s", f.name, exc)
                continue

    return web.json_response({"threads": threads[:30]})


async def api_schedules_handler(request):
    """Return scheduled tasks for the dashboard."""
    try:
        from scheduler import scheduler

        tasks = [_serialize_schedule_task(task) for task in scheduler.list_tasks()]
        return web.json_response({"tasks": tasks})
    except (ImportError, AttributeError, RuntimeError) as exc:
        log.debug("Schedules API failed: %s", exc)
        return web.json_response({"tasks": []})


async def api_schedule_toggle_handler(request):
    """Toggle a scheduled task enabled state from the dashboard."""
    task_id = str(request.match_info.get("task_id", "")).strip()
    if not task_id:
        return web.json_response({"ok": False, "error": "Missing task_id"}, status=400)
    try:
        from scheduler import scheduler

        new_state = scheduler.toggle(task_id)
        if new_state is None:
            return web.json_response({"ok": False, "error": f"Task '{task_id}' not found"}, status=404)
        task = scheduler.get(task_id)
        return web.json_response({"ok": True, "task": _serialize_schedule_task(task)})
    except (ImportError, AttributeError, RuntimeError) as exc:
        log.debug("Schedule toggle failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def api_schedule_update_handler(request):
    """Update a scheduled task from the dashboard."""
    task_id = str(request.match_info.get("task_id", "")).strip()
    if not task_id:
        return web.json_response({"ok": False, "error": "Missing task_id"}, status=400)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"ok": False, "error": "Invalid JSON payload"}, status=400)

    try:
        from scheduler import scheduler

        interval_raw = payload.get("interval_minutes")
        cron_hour_raw = payload.get("cron_hour")
        cron_minute_raw = payload.get("cron_minute")
        updated = scheduler.update(
            task_id,
            action=str(payload["name"]).strip() if payload.get("name") is not None else None,
            prompt=str(payload["prompt"]).strip() if payload.get("prompt") is not None else None,
            cron_expression=str(payload["cron_expression"]).strip()
            if payload.get("cron_expression") is not None
            else None,
            interval_minutes=None if interval_raw in (None, "") else int(interval_raw),
            cron_hour=None if cron_hour_raw in (None, "") else int(cron_hour_raw),
            cron_minute=None if cron_minute_raw in (None, "") else int(cron_minute_raw),
            enabled=None if payload.get("enabled") is None else bool(payload.get("enabled")),
        )
        if updated is None:
            return web.json_response({"ok": False, "error": f"Task '{task_id}' not found"}, status=404)
        return web.json_response({"ok": True, "task": _serialize_schedule_task(updated)})
    except (TypeError, ValueError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except (ImportError, AttributeError, RuntimeError) as exc:
        log.debug("Schedule update failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def api_schedule_delete_handler(request):
    """Delete a scheduled task by ID."""
    try:
        task_id = request.match_info.get("task_id", "")
        if not task_id:
            return web.json_response({"error": "Missing task_id"}, status=400)

        from scheduler import cancel_scheduled_task

        result = await cancel_scheduled_task(task_id)

        if result.startswith("✅"):
            return web.json_response({"ok": True, "message": result})
        else:
            return web.json_response({"ok": False, "message": result}, status=404)
    except (ImportError, AttributeError, OSError, RuntimeError) as exc:
        log.debug("Schedule delete failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def api_errors_handler(request):
    """Return error stats for the dashboard."""
    try:
        from error_tracker import get_error_stats

        stats = get_error_stats(hours=24)
        return web.json_response(stats)
    except (ImportError, OSError, json.JSONDecodeError) as exc:
        log.debug("Error stats API failed: %s", exc)
        return web.json_response({"total": 0, "success_rate": 1.0, "recent_errors": []})


async def api_response_stats_handler(request):
    """Return response-time statistics for /ask queries."""
    return web.json_response(get_response_stats())


async def api_dream_health_handler(request):
    """Return dream/memory health data for the dashboard."""
    try:
        from dream_cycle import DreamCycle, _compute_health, _load_index

        cycle = DreamCycle()
        index = _load_index(cycle.index_path)
        health = _compute_health(index, cycle.memory_path)
        entries = index.get("entries", [])
        stats = index.get("stats", {})
        return web.json_response(
            {
                "overall": round(health["overall"] * 100, 1),
                "metrics": health["metrics"],
                "entry_count": len(entries),
                "avg_importance": round(stats.get("avgImportance", 0), 2),
                "last_dream": stats.get("lastDream", None),
                "health_history": stats.get("healthHistory", [])[-14:],
            }
        )
    except (ImportError, OSError, AttributeError, ValueError) as exc:
        log.debug("Dream health API failed: %s", exc)
        return web.json_response(
            {
                "overall": 0,
                "metrics": {},
                "entry_count": 0,
                "avg_importance": 0,
                "last_dream": None,
                "health_history": [],
            }
        )


async def api_config_status_handler(request):
    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    from config import cfg

    provider_states: dict[str, dict] = {}
    try:
        from llm.providers import PROVIDER_FALLBACK_CHAIN, _circuit, is_circuit_open

        for prov in PROVIDER_FALLBACK_CHAIN + ["copilot", "gemini", "ollama", "openai", "anthropic"]:
            if prov in provider_states:
                continue
            state = _circuit.get(prov, {})
            is_open = is_circuit_open(prov)
            provider_states[prov] = {
                "open": is_open,
                "failures": state.get("failures", 0),
                "open_until": state.get("open_until", 0.0) if is_open else None,
                "badge": "danger" if is_open else "success",
            }
    except Exception as e:
        log.warning("api_config_status_handler error: %s", e)

    return web.json_response({"services": cfg.config_status(), "providers": provider_states})


async def api_search_stats_handler(request):
    """Return per-provider search usage statistics."""
    from search_provider import all_stats

    return web.json_response(all_stats())


async def api_quota_status_handler(request):
    """Return estimated remaining quota per provider."""
    return web.json_response(get_quota_status())


async def api_skill_stats_handler(request):
    """Return skill invocation counts."""
    from llm_tools import get_skill_stats

    return web.json_response(get_skill_stats())


def _parse_quality_query(request: web.Request) -> dict:
    """Parse query parameters for the quality metrics endpoint.

    The endpoint currently has no client-tunable knobs; this returns
    the fixed defaults and exists to keep the pipeline shape consistent.
    """
    return {
        "snapshot_limit": 25,
        "runtime_hours": 24,
        "runtime_run_limit": 120,
    }


def _load_quality_metrics(params: dict) -> dict:
    """Load raw inputs (snapshot, recent runs, calibration payload)."""
    from error_tracker import get_recent_outcomes
    from metrics_collector import get_quality_event_snapshot

    snapshot = get_quality_event_snapshot(limit=params["snapshot_limit"])
    recent_runs = get_recent_outcomes(hours=params["runtime_hours"], limit=params["runtime_run_limit"])
    if not isinstance(recent_runs, list):
        recent_runs = []
    try:
        calibration_payload = _build_offline_quality_calibration_payload()
    except Exception as exc:  # broad: intentional
        log.debug("Quality metrics drift status unavailable: %s", exc)
        calibration_payload = None
    return {
        "snapshot": snapshot,
        "recent_runs": recent_runs,
        "calibration_payload": calibration_payload,
    }


def _compute_quality_feedback(event_counts: dict, snapshot: dict) -> dict:
    feedback_snapshot = snapshot.get("feedback")
    if not isinstance(feedback_snapshot, dict):
        feedback_snapshot = {}
    helpful = int(feedback_snapshot.get("helpful", event_counts.get("ask_feedback_helpful", 0)) or 0)
    not_helpful = int(feedback_snapshot.get("not_helpful", event_counts.get("ask_feedback_not_helpful", 0)) or 0)
    total = helpful + not_helpful
    helpful_rate = round(helpful / total, 3) if total > 0 else None
    accepted = int(feedback_snapshot.get("accepted", event_counts.get("ask_feedback_accepted", total)) or 0)
    suppressed = int(feedback_snapshot.get("suppressed", event_counts.get("ask_feedback_suppressed", 0)) or 0)
    suppressed_dedupe = int(
        feedback_snapshot.get("suppressed_dedupe", event_counts.get("ask_feedback_suppressed_dedupe", 0)) or 0
    )
    suppressed_rate_limited = int(
        feedback_snapshot.get(
            "suppressed_rate_limited",
            int(event_counts.get("ask_feedback_suppressed_rate_limited_user", 0) or 0)
            + int(event_counts.get("ask_feedback_suppressed_rate_limited_channel", 0) or 0),
        )
        or 0
    )
    return {
        "helpful": helpful,
        "not_helpful": not_helpful,
        "total": total,
        "helpful_rate": helpful_rate,
        "accepted": accepted,
        "suppressed": suppressed,
        "suppressed_dedupe": suppressed_dedupe,
        "suppressed_rate_limited": suppressed_rate_limited,
    }


def _compute_quality_calibration_drift(calibration_payload) -> dict:
    drift_default = {
        "available": False,
        "baseline_available": False,
        "status": "unavailable",
        "severity_level": "unknown",
        "severe": False,
        "score": 0,
        "regressed_metrics": [],
    }
    if not isinstance(calibration_payload, dict):
        return drift_default
    drift = calibration_payload.get("drift")
    if not isinstance(drift, dict):
        drift = {}
    severity = drift.get("severity")
    if not isinstance(severity, dict):
        severity = {}
    regressed_metrics = drift.get("regressed_metrics")
    if not isinstance(regressed_metrics, list):
        regressed_metrics = []
    return {
        "available": bool(calibration_payload.get("available")),
        "baseline_available": bool(drift.get("baseline_available")),
        "status": str(drift.get("status") or "unavailable"),
        "severity_level": str(severity.get("level") or "unknown"),
        "severe": bool(severity.get("severe")),
        "score": int(severity.get("score") or 0),
        "regressed_metrics": [str(item) for item in regressed_metrics if str(item).strip()],
    }


def _compute_quality_runtime_stats(recent_runs: list, event_counts: dict) -> dict:
    score_distribution = {"high": 0, "medium": 0, "low": 0}
    retry_outcomes = {
        "attempted": 0,
        "improved": 0,
        "no_improvement": 0,
        "failed": 0,
        "skipped": 0,
    }
    low_confidence_reason_counts: dict[str, int] = {}
    low_confidence_prompt_count = 0
    runs_with_quality = 0
    runs_with_retry = 0

    for run in recent_runs:
        if not isinstance(run, dict):
            continue
        explainability = run.get("explainability")
        if not isinstance(explainability, dict):
            explainability = {}
        final_meta = explainability.get("final_meta")
        if not isinstance(final_meta, dict):
            final_meta = {}

        quality_meta = run.get("answer_quality")
        if not isinstance(quality_meta, dict):
            quality_meta = explainability.get("answer_quality")
        if not isinstance(quality_meta, dict):
            quality_meta = final_meta.get("answer_quality")
        if not isinstance(quality_meta, dict):
            quality_meta = {}

        retry_meta = run.get("answer_quality_retry")
        if not isinstance(retry_meta, dict):
            retry_meta = explainability.get("answer_quality_retry")
        if not isinstance(retry_meta, dict):
            retry_meta = final_meta.get("answer_quality_retry")
        if not isinstance(retry_meta, dict):
            retry_meta = {}

        quality_status = str(quality_meta.get("status", "")).strip().lower()
        if quality_status not in {"high", "medium", "low"}:
            score_value = quality_meta.get("score")
            try:
                parsed_score = int(score_value)
            except (TypeError, ValueError):
                parsed_score = None
            if parsed_score is not None:
                quality_status = "high" if parsed_score >= 75 else "medium" if parsed_score >= 45 else "low"

        if quality_status in score_distribution:
            score_distribution[quality_status] += 1
            runs_with_quality += 1

        low_confidence_from_run = quality_status == "low"
        if low_confidence_from_run:
            reasons = quality_meta.get("reasons")
            if isinstance(reasons, list):
                for reason in reasons:
                    if not isinstance(reason, str):
                        continue
                    normalized = reason.strip()
                    if not normalized:
                        continue
                    low_confidence_reason_counts[normalized] = low_confidence_reason_counts.get(normalized, 0) + 1
            low_confidence_prompt_count += 1

        routing_notes = run.get("routing_notes")
        if not low_confidence_from_run and isinstance(routing_notes, list):
            if any(isinstance(note, str) and "low confidence" in note.lower() for note in routing_notes):
                low_confidence_prompt_count += 1

        if retry_meta:
            runs_with_retry += 1
            if bool(retry_meta.get("attempted")):
                retry_outcomes["attempted"] += 1
            outcome = str(retry_meta.get("outcome", "")).strip().lower()
            if outcome in {"improved", "no_improvement", "failed", "skipped"}:
                retry_outcomes[outcome] += 1

    retry_outcomes["improved"] = max(
        retry_outcomes["improved"],
        int(event_counts.get("ask_quality_retry_improved", 0) or 0),
    )
    retry_outcomes["no_improvement"] = max(
        retry_outcomes["no_improvement"],
        int(event_counts.get("ask_quality_retry_no_improvement", 0) or 0),
    )
    retry_outcomes["failed"] = max(
        retry_outcomes["failed"],
        int(event_counts.get("ask_quality_retry_failed", 0) or 0),
    )
    retry_outcomes["skipped"] = max(
        retry_outcomes["skipped"],
        int(event_counts.get("ask_quality_retry_skipped", 0) or 0),
    )
    retry_outcomes["attempted"] = max(
        retry_outcomes["attempted"],
        int(event_counts.get("ask_quality_retry_attempted", 0) or 0),
        retry_outcomes["improved"] + retry_outcomes["no_improvement"] + retry_outcomes["failed"],
    )
    low_confidence_prompt_count = max(
        low_confidence_prompt_count,
        int(event_counts.get("ask_low_score_detected", 0) or 0),
    )
    top_low_confidence_reasons = sorted(
        low_confidence_reason_counts.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:5]

    return {
        "score_distribution": score_distribution,
        "retry_outcomes": retry_outcomes,
        "low_confidence_prompt_count": low_confidence_prompt_count,
        "top_low_confidence_reasons": top_low_confidence_reasons,
        "runs_with_quality": runs_with_quality,
        "runs_with_retry": runs_with_retry,
    }


def _compute_quality_aggregates(raw: dict, params: dict) -> dict:
    """Compute derived aggregates from raw inputs."""
    snapshot = raw["snapshot"]
    recent_runs = raw["recent_runs"]
    calibration_payload = raw["calibration_payload"]

    event_counts = _normalize_event_counts(snapshot.get("event_counts", {}))
    feedback = _compute_quality_feedback(event_counts, snapshot)

    signals = {
        "search_fallback_activation": _safe_non_negative_int(event_counts.get("search_fallback_activation", 0)),
        "search_low_results_incident": _safe_non_negative_int(event_counts.get("search_low_results_incident", 0)),
        "recap_fallback_activation": _safe_non_negative_int(event_counts.get("recap_fallback_activation", 0)),
        "recap_partial_coverage_warning": _safe_non_negative_int(event_counts.get("recap_partial_coverage_warning", 0)),
    }
    domain_trends, top_recurring_failures, recent_signal_slices = _build_quality_domain_summary(
        event_counts,
        limit=max(_QUALITY_DOMAIN_LIMIT, _QUALITY_FAILURE_LIMIT, _QUALITY_SIGNAL_LIMIT),
    )
    quality_failure_categories = snapshot.get("quality_failure_categories")
    if not isinstance(quality_failure_categories, dict):
        quality_failure_categories = _build_quality_failure_category_summary(
            event_counts,
            limit=_QUALITY_FAILURE_LIMIT,
        )
    top_quality_failure_categories = snapshot.get("top_quality_failure_categories")
    if not isinstance(top_quality_failure_categories, list):
        top_quality_failure_categories = list(quality_failure_categories.get("top", []))
    top_recurring_failures = top_recurring_failures[:_QUALITY_FAILURE_LIMIT]
    top_quality_failure_categories = top_quality_failure_categories[:_QUALITY_FAILURE_LIMIT]
    recent_signal_slices = {
        "mitigation": list(recent_signal_slices.get("mitigation", []))[:_QUALITY_SIGNAL_LIMIT],
        "degrade": list(recent_signal_slices.get("degrade", []))[:_QUALITY_SIGNAL_LIMIT],
    }

    total = int(snapshot.get("total_events", 0) or 0)
    warning_pressure = signals["search_low_results_incident"] + signals["recap_partial_coverage_warning"]
    if total <= 0:
        status = "no_data"
    elif warning_pressure <= max(2, total // 5):
        status = "healthy"
    elif warning_pressure <= max(5, total // 3):
        status = "watch"
    else:
        status = "degraded"

    calibration_drift = _compute_quality_calibration_drift(calibration_payload)
    if calibration_drift["severe"]:
        status = "degraded"

    runtime_stats = _compute_quality_runtime_stats(recent_runs, event_counts)

    return {
        "snapshot": snapshot,
        "signals": signals,
        "domain_trends": domain_trends[:_QUALITY_DOMAIN_LIMIT],
        "top_recurring_failures": top_recurring_failures,
        "top_quality_failure_categories": top_quality_failure_categories,
        "quality_failure_categories": quality_failure_categories,
        "recent_signal_slices": recent_signal_slices,
        "status": status,
        "calibration_drift": calibration_drift,
        "warning_pressure": warning_pressure,
        "feedback": feedback,
        "runtime_stats": runtime_stats,
        "recent_runs_count": len(recent_runs),
        "runtime_hours": params["runtime_hours"],
    }


def _build_quality_response(data: dict) -> web.Response:
    """Build the JSON response from computed aggregates."""
    runtime = data["runtime_stats"]
    return web.json_response(
        {
            **data["snapshot"],
            "signals": data["signals"],
            "domain_trends": data["domain_trends"],
            "top_recurring_failures": data["top_recurring_failures"],
            "top_quality_failure_categories": data["top_quality_failure_categories"],
            "quality_failure_categories": data["quality_failure_categories"],
            "recent_signal_slices": data["recent_signal_slices"],
            "status": data["status"],
            "calibration_drift": data["calibration_drift"],
            "warning_pressure": data["warning_pressure"],
            "score_distribution": runtime["score_distribution"],
            "low_confidence": {
                "prompt_count": int(runtime["low_confidence_prompt_count"]),
                "top_reasons": [
                    {"reason": reason, "count": int(count)} for reason, count in runtime["top_low_confidence_reasons"]
                ],
            },
            "retry_outcomes": runtime["retry_outcomes"],
            "runtime_window": {
                "hours": data["runtime_hours"],
                "runs_considered": data["recent_runs_count"],
                "runs_with_quality": int(runtime["runs_with_quality"]),
                "runs_with_retry": int(runtime["runs_with_retry"]),
            },
            "feedback": data["feedback"],
        }
    )


def _build_quality_error_response() -> web.Response:
    return web.json_response(
        {
            "total_events": 0,
            "event_counts": {},
            "context_counts": {},
            "top_events": [],
            "top_contexts": [],
            "signals": {
                "search_fallback_activation": 0,
                "search_low_results_incident": 0,
                "recap_fallback_activation": 0,
                "recap_partial_coverage_warning": 0,
            },
            "domain_trends": [],
            "top_recurring_failures": [],
            "top_quality_failure_categories": [],
            "quality_failure_categories": {
                "counts": {},
                "top": [],
                "total_classified_failures": 0,
                "total_failure_events": 0,
            },
            "recent_signal_slices": {"mitigation": [], "degrade": []},
            "status": "no_data",
            "calibration_drift": {
                "available": False,
                "baseline_available": False,
                "status": "unavailable",
                "severity_level": "unknown",
                "severe": False,
                "score": 0,
                "regressed_metrics": [],
            },
            "warning_pressure": 0,
            "score_distribution": {"high": 0, "medium": 0, "low": 0},
            "low_confidence": {"prompt_count": 0, "top_reasons": []},
            "retry_outcomes": {
                "attempted": 0,
                "improved": 0,
                "no_improvement": 0,
                "failed": 0,
                "skipped": 0,
            },
            "runtime_window": {
                "hours": 24,
                "runs_considered": 0,
                "runs_with_quality": 0,
                "runs_with_retry": 0,
            },
            "feedback": {
                "helpful": 0,
                "not_helpful": 0,
                "total": 0,
                "helpful_rate": None,
                "accepted": 0,
                "suppressed": 0,
                "suppressed_dedupe": 0,
                "suppressed_rate_limited": 0,
            },
        }
    )


async def api_quality_metrics_handler(request: web.Request) -> web.Response:
    """Return quality telemetry counters for dashboard quality operations."""
    try:
        params = _parse_quality_query(request)
        raw = _load_quality_metrics(params)
        data = _compute_quality_aggregates(raw, params)
        return _build_quality_response(data)
    except Exception as exc:  # broad: intentional
        log.debug("Quality metrics API failed: %s", exc)
        return _build_quality_error_response()


async def api_knowledge_graph_handler(request):
    """Return knowledge graph nodes and edges for 3D visualization."""
    index_path = Path("/app/data/dream/index.json")
    if not index_path.exists():
        return web.json_response({"nodes": [], "edges": []})
    try:
        data = json.loads(index_path.read_text())
        entries = data.get("entries", [])
        nodes = []
        edges = []
        for e in entries:
            if e.get("archived"):
                continue
            nodes.append(
                {
                    "id": e["id"],
                    "summary": e.get("summary", "")[:60],
                    "importance": e.get("importance", 0.5),
                    "tags": e.get("tags", []),
                    "created": e.get("created", ""),
                }
            )
            for rel in e.get("related", []):
                edges.append({"source": e["id"], "target": rel})
        return web.json_response({"nodes": nodes, "edges": edges})
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        log.debug("Knowledge graph API failed: %s", exc)
        return web.json_response({"nodes": [], "edges": []})


async def api_topology_handler(request):
    """Return network topology for visualization."""
    from config import cfg as _topo_cfg

    nodes = [
        {"id": "mac-mini", "label": "Mac Mini M4", "type": "host", "ip": _topo_cfg.docker_host_ip, "x": 400, "y": 275},
        {"id": "nas", "label": "Synology NAS", "type": "host", "ip": _topo_cfg.nas_ip, "x": 200, "y": 275},
        {"id": "internet", "label": "Internet", "type": "cloud", "x": 300, "y": 50},
        {"id": "traefik", "label": "Traefik", "type": "proxy", "x": 300, "y": 160},
        {"id": "adguard", "label": "AdGuard Home", "type": "container", "status": "up", "x": 80, "y": 160},
    ]
    edges = [
        {"source": "internet", "target": "mac-mini", "label": "APIs / Discord"},
        {"source": "internet", "target": "nas", "label": "HTTPS:443"},
        {"source": "nas", "target": "traefik", "label": "SSL termination"},
        {"source": "traefik", "target": "mac-mini", "label": "HTTP:8100"},
        {"source": "mac-mini", "target": "nas", "label": "NFS/SMB"},
        {"source": "nas", "target": "adguard", "label": "DNS:53"},
    ]

    try:
        from skills import list_containers

        container_text = await list_containers()
        if not container_text.startswith("\u274c"):
            lines = [ln.strip() for ln in container_text.split("\n") if ln.strip() and not ln.startswith("NAMES")]
            num_containers = max(len(lines), 1)
            radius = max(180, num_containers * 14)
            angle_step = (2 * math.pi) / num_containers
            for i, line in enumerate(lines):
                parts = [p.strip() for p in line.split("\t") if p.strip()]
                if not parts:
                    parts = [p.strip() for p in re.split(r"\s{2,}", line) if p.strip()]
                if parts:
                    name = parts[0]
                    is_up = any("Up" in p for p in parts)
                    angle = angle_step * i - (math.pi / 2)
                    x = 400 + math.cos(angle) * radius
                    y = 250 + math.sin(angle) * radius
                    nodes.append(
                        {
                            "id": name,
                            "label": name,
                            "type": "container",
                            "status": "up" if is_up else "down",
                            "x": round(x),
                            "y": round(y),
                        }
                    )
                    edges.append({"source": "mac-mini", "target": name})
    except (OSError, ValueError, RuntimeError) as e:
        log.debug("Topology container fetch failed: %s", e)

    return web.json_response({"nodes": nodes, "edges": edges})


# ---------------------------------------------------------------------------
# Agent interaction endpoints (dashboard chat & report generation)
# ---------------------------------------------------------------------------


async def api_agent_ask_handler(request: web.Request) -> web.Response:
    """POST /api/agent/ask — Submit a prompt to OpenClaw and return the response.

    Body (JSON):
        prompt   (str, required)  — the user's question or command
        model    (str, optional)  — model preference: "auto" | "gemini" | "openai" | "anthropic" | "local" | "copilot"
        history  (list, optional) — prior conversation turns [{"role": ..., "content": ...}]
        user_name (str, optional) — logical caller label used for ask context/audit attribution

    Returns JSON:
        response  (str)  — assistant reply text
        model     (str)  — model that was used
        tokens    (int)  — approximate token usage (0 if unavailable)
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"error": "prompt is required"}, status=400)

    model_pref = body.get("model", "auto")
    history: list[dict] = body.get("history") or []
    if not isinstance(history, list):
        return web.json_response({"error": "history must be a list"}, status=400)
    user_name = str(body.get("user_name") or "Dashboard").strip() or "Dashboard"
    routing_profile = str(body.get("routing_profile") or "").strip()

    try:
        payload = await _execute_agent_ask(
            prompt=prompt,
            model_pref=model_pref,
            history=history,
            user_name=user_name,
            routing_profile=routing_profile,
        )
        return web.json_response(payload)
    except Exception as exc:  # broad: intentional
        log.error("api_agent_ask_handler error: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def _execute_agent_ask(
    *,
    prompt: str,
    model_pref: str,
    history: list[dict],
    user_name: str,
    routing_profile: str = "",
    on_partial_chunk: callable | None = None,
) -> dict[str, object]:
    from ask_orchestrator import run_ask_stream
    from llm import chat_stream as llm_chat_stream
    from quality_helpers import (
        _build_ask_recovery_block,
        _run_quality_auto_repair,
        _safe_score_answer_quality,
        _with_requested_item_target,
    )

    latest_history = list(history)
    last_partial = ""

    def _update_history(updated_history: list[dict]) -> None:
        nonlocal latest_history
        latest_history = updated_history

    async def _handle_partial(chunk_text: str) -> None:
        nonlocal last_partial
        if on_partial_chunk is None:
            return
        text = str(chunk_text or "")
        delta = text[len(last_partial) :] if last_partial and text.startswith(last_partial) else text
        last_partial = text
        if delta:
            await on_partial_chunk(delta)

    result = await run_ask_stream(
        llm_stream=llm_chat_stream,
        user_message=prompt,
        history=history,
        user_name=user_name,
        model_preference=model_pref,
        channel_id=None,
        thread_id=None,
        user_id=user_name,
        update_history=_update_history,
        context_controls=None,
        routing_profile=routing_profile,
        on_partial_chunk=_handle_partial if on_partial_chunk is not None else None,
    )
    response_text = str(result.response_text or "").strip()
    model_used = str(result.model_used or model_pref)
    final_meta = _with_requested_item_target(result.final_meta, question=prompt)
    quality_meta = _safe_score_answer_quality(
        response_text,
        final_meta=final_meta,
        context="ask",
    )

    async def _run_retry_stream(retry_prompt: str):
        _retry_pref = "copilot" if (model_used or "").startswith("gemini") else model_pref
        return await run_ask_stream(
            llm_stream=llm_chat_stream,
            user_message=retry_prompt,
            history=latest_history,
            user_name=user_name,
            model_preference=_retry_pref,
            channel_id=None,
            thread_id=None,
            user_id=user_name,
            update_history=_update_history,
            context_controls=None,
        )

    repair_result = await _run_quality_auto_repair(
        question=prompt,
        response_text=response_text,
        model_used=model_used,
        final_meta=final_meta,
        quality_meta=quality_meta,
        context="ask",
        run_retry_stream=_run_retry_stream,
        think_hook=None,
    )
    response_text = str(repair_result["response_text"])
    model_used = str(repair_result["model_used"])
    final_meta = dict(repair_result["final_meta"])

    recovery_block = _build_ask_recovery_block(final_meta)
    if recovery_block and "Recovery note" not in response_text:
        response_text = f"{response_text.rstrip()}{recovery_block}"

    tokens_raw = final_meta.get("total_tokens", 0) if isinstance(final_meta, dict) else 0
    try:
        tokens = int(tokens_raw or 0)
    except (TypeError, ValueError):
        tokens = 0

    return {
        "response": response_text,
        "model": model_used,
        "tokens": tokens,
    }


async def api_agent_ask_stream_handler(request: web.Request) -> web.StreamResponse:
    """POST /api/agent/ask/stream — stream ask output as SSE."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"error": "prompt is required"}, status=400)

    model_pref = body.get("model", "auto")
    history: list[dict] = body.get("history") or []
    if not isinstance(history, list):
        return web.json_response({"error": "history must be a list"}, status=400)
    user_name = str(body.get("user_name") or "Dashboard").strip() or "Dashboard"
    routing_profile = str(body.get("routing_profile") or "").strip()

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)

    async def _write_event(event: str, data: dict[str, object]) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        await resp.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))

    try:
        payload = await _execute_agent_ask(
            prompt=prompt,
            model_pref=model_pref,
            history=history,
            user_name=user_name,
            routing_profile=routing_profile,
            on_partial_chunk=lambda chunk: _write_event("chunk", {"delta": chunk}),
        )
        await _write_event("final", payload)
    except Exception as exc:  # broad: intentional
        log.error("api_agent_ask_stream_handler error: %s", exc)
        await _write_event("error", {"error": str(exc)})
    finally:
        await resp.write_eof()

    return resp


async def api_manifest_handler(request):
    """Serve PWA web app manifest."""
    manifest = {
        "name": "OpenClaw",
        "short_name": "OpenClaw",
        "description": "OpenClaw Home Lab AI Dashboard",
        "start_url": "/dashboard",
        "display": "standalone",
        "background_color": "#0d0e1a",
        "theme_color": "#7c3aed",
        "orientation": "portrait-primary",
        "icons": [
            {
                "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'%3E%3Crect width='192' height='192' rx='24' fill='%237c3aed'/%3E%3Ctext x='96' y='130' font-size='100' text-anchor='middle' font-family='system-ui'%3E⚕%3C/text%3E%3C/svg%3E",
                "sizes": "192x192",
                "type": "image/svg+xml",
                "purpose": "any maskable"
            }
        ]
    }
    return web.Response(
        content_type="application/manifest+json",
        text=json.dumps(manifest)
    )


# ---------------------------------------------------------------------------
# OpenAI-compatible API  (/v1/models  +  /v1/chat/completions)
# These endpoints let Open WebUI (or any OpenAI SDK client) talk to OpenClaw.
# ---------------------------------------------------------------------------

# Models exposed to Open WebUI — matches the model_pref values accepted by
# api_agent_ask_handler.
_OAI_MODELS = [
    {"id": "auto",      "label": "OpenClaw Auto"},
    {"id": "gemini",    "label": "Gemini"},
    {"id": "openai",    "label": "OpenAI"},
    {"id": "anthropic", "label": "Anthropic / Claude"},
    {"id": "local",     "label": "Local (Ollama)"},
    {"id": "copilot",   "label": "Copilot CLI (SSH)"},
    {"id": "shell",     "label": "Mac Mini Shell (bash)"},
]

import os as _os

# Regex to strip the "_via Model Name_" attribution footer OpenClaw appends to
# LLM responses.  It's useful in Slack/dashboard but is noise in Open WebUI.
_VIA_FOOTER_RE = re.compile(r"\n_via [^\n]+_[ \t]*$", re.MULTILINE)

# Copilot CLI emits terminal status bar lines at the end of every run (even with
# TERM=dumb).  These are noise in Open WebUI and should be filtered from /v1 output.
_COPILOT_NOISE_RE = re.compile(
    r"^(Changes\s+[+-]\d|AI Credits\s+\d|Tokens\s+[↑↓]|"
    r"Duration\s+\d|\u25cf\s*(Load|Loading)|❯\s*$)",
    re.IGNORECASE,
)

# Lines that indicate Copilot is using a tool — extracted and emitted as ⚙️ progress.
_COPILOT_TOOL_RE = re.compile(
    r"^\s*[●•✦✶◆▸→⚡]\s*"
    r"(?:Using tool|Running tool|Executing|Tool call|Calling tool|Running):\s*(.+)$",
    re.IGNORECASE,
)
# Also match lines like "  bash" or "  read_file" that are bare tool names after a tool-use header.
_COPILOT_TOOL_NAME_RE = re.compile(
    r"^\s*(bash|python|read_file|write_file|search_files?|run_shell|share_file|"
    r"list_dir|grep|glob|web_search|web_fetch|get_file|create_file|edit_file)\s*$",
    re.IGNORECASE,
)


def _is_copilot_noise_line(line: str) -> bool:
    stripped = line.strip()
    return bool(_COPILOT_NOISE_RE.match(stripped)) or not stripped


def _copilot_tool_label(line: str) -> str | None:
    """Return a short ⚙️ label if the line describes tool usage, else None."""
    stripped = line.strip()
    m = _COPILOT_TOOL_RE.match(stripped)
    if m:
        return f"⚙️ tool: {m.group(1).strip()}"
    if _COPILOT_TOOL_NAME_RE.match(stripped):
        return f"⚙️ tool: {stripped}"
    return None



# ---------------------------------------------------------------------------
# NAS path detection — auto-generate share links for file paths in responses
# ---------------------------------------------------------------------------
_NAS_PATH_RE = re.compile(
    r"(/(?:Volumes/ROMs|ROMs)/[^\s\n\"'`\)>]+\.[a-zA-Z0-9]{1,6})",
)


def _extract_nas_paths(text: str) -> list[str]:
    """Return unique NAS file paths found in *text* (deduplicated, order-preserved)."""
    seen: set[str] = set()
    result: list[str] = []
    for m in _NAS_PATH_RE.finditer(text):
        p = m.group(1).rstrip(".,;:")
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


async def _try_share_links(text: str) -> str:
    """Scan *text* for NAS file paths and return a formatted share-links block.

    Returns an empty string if no paths are found or all share-link attempts fail.
    Silently swallows errors so callers never need to handle exceptions.
    """
    paths = _extract_nas_paths(text)
    if not paths:
        return ""
    try:
        from nas import nas_create_share_link
    except ImportError:
        return ""
    lines: list[str] = []
    for p in paths[:5]:  # cap at 5 to avoid flooding
        try:
            result = await nas_create_share_link(p)
            if not result.startswith("❌"):
                lines.append(result)
        except Exception:
            pass
    if not lines:
        return ""
    return "\n\n---\n" + "\n".join(lines)


def _v1_auth_ok(request: web.Request) -> bool:
    """Return True if the request carries a valid /v1 API key.

    Accepts the key via ``Authorization: Bearer <key>`` header.
    If ``OPENCLAW_V1_API_KEY`` is not set (or empty) the check is skipped
    and all requests are allowed, preserving backward compatibility.
    """
    expected = _os.environ.get("OPENCLAW_V1_API_KEY", "").strip()
    if not expected:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() == expected
    # Also accept as query param for curl-friendly testing
    return request.rel_url.query.get("api_key", "") == expected


async def api_v1_models_handler(request: web.Request) -> web.Response:
    """GET /v1/models — OpenAI-compatible model list for Open WebUI."""
    if not _v1_auth_ok(request):
        return web.json_response(
            {"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}},
            status=401,
        )
    created = 1700000000
    data = [
        {
            "id": m["id"],
            "object": "model",
            "created": created,
            "owned_by": "openclaw",
            "name": m["label"],
        }
        for m in _OAI_MODELS
    ]
    return web.json_response({"object": "list", "data": data})


async def api_v1_chat_completions_handler(request: web.Request) -> web.Response:
    """POST /v1/chat/completions — OpenAI-compatible chat endpoint for Open WebUI.

    Maps OpenAI ``messages`` format onto OpenClaw's prompt/history model.
    Supports both non-streaming (default) and streaming (``stream: true``)
    responses.  Streaming emits OpenAI-format SSE delta chunks.
    """
    if not _v1_auth_ok(request):
        return web.json_response(
            {"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}},
            status=401,
        )

    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": {"message": "Invalid JSON", "type": "invalid_request_error"}}, status=400)

    messages: list[dict] = body.get("messages") or []
    model_pref: str = str(body.get("model") or "auto").strip()
    do_stream: bool = bool(body.get("stream", False))

    if not messages:
        return web.json_response(
            {"error": {"message": "messages is required", "type": "invalid_request_error"}},
            status=400,
        )

    # Estimate prompt token count before we transform messages (rough approximation).
    prompt_tokens_est = len(json.dumps(messages).encode("utf-8")) // 4

    # Extract the last user message as the prompt; everything before is history.
    prompt = ""
    history: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        content = str(msg.get("content") or "").strip()
        if role == "user":
            if prompt:
                history.append({"role": "user", "content": prompt})
            prompt = content
        elif role in ("assistant", "model"):
            history.append({"role": "model", "content": content})
        elif role == "system":
            history.append({"role": "user", "content": f"[system] {content}"})
            history.append({"role": "model", "content": "Understood."})

    if not prompt:
        return web.json_response(
            {"error": {"message": "No user message found in messages array", "type": "invalid_request_error"}},
            status=400,
        )

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created_ts = int(time.time())

    # ------------------------------------------------------------------
    # Copilot CLI path — route directly to SSH bridge (skips LLM dispatch)
    # ------------------------------------------------------------------
    if model_pref == "copilot":
        _copilot_backend = _os.environ.get("COPILOT_BACKEND", "ssh").lower()
        _copilot_model_name = "hermes" if _copilot_backend == "hermes" else "copilot-cli"
        try:
            if _copilot_backend == "hermes":
                from host_bridge import run_hermes_stream
            else:
                from host_bridge import run_copilot, run_copilot_stream
        except ImportError as exc:
            return web.json_response(
                {"error": {"message": f"host_bridge unavailable: {exc}", "type": "server_error"}},
                status=500,
            )

        async def _copilot_event_stream():
            if _copilot_backend == "hermes":
                async for event in run_hermes_stream(
                    prompt=prompt,
                    slack_user_id="open-webui",
                    timeout_s=120,
                ):
                    yield event
                return

            async for event in run_copilot_stream(prompt=prompt, slack_user_id="open-webui"):
                yield event

        # Inject Mac Mini system context so Copilot CLI knows the local environment.
        _bridge_workdir = _os.environ.get("OPENCLAW_HOST_BRIDGE_WORKDIR", "/Users/davevoyles/docker-stack")
        context_lines = [
            "[System context — Mac Mini environment]",
            "Host: Mac Mini M4 (macOS), user: davevoyles",
            f"Working directory: {_bridge_workdir}",
            "",
            "## NAS access (Synology DS920+, IP 192.168.1.8)",
            "SMB shares mounted on Mac Mini:",
            "  /Users/davevoyles/mnt/ROMs          — ROMs library (NAS /volume1/ROMs)",
            "  /Users/davevoyles/mnt/PlexMediaServer — Plex media (NAS /volume1/PlexMediaServer)",
            "  /Users/davevoyles/mnt/Misc           — Misc/comics (NAS /volume1/Misc)",
            "  /Volumes/ROMs                        — same ROMs share via Finder mount (alias)",
            "SSH to NAS:  ssh -p 24 dave@192.168.1.8 '<command>'",
            "NAS files live at /volume1/<share> when accessed over SSH.",
            "Example: find ROMs on NAS via SSH: ssh -p 24 dave@192.168.1.8 'ls /volume1/ROMs/ROMs/'",
            "DSM web API: https://192.168.1.8:5001  (TLS verify=false)",
            "Public NAS URL: https://davevoyles.synology.me:5001",
            "",
            "## ROMs library path map",
            "Mac Mini path:  /Users/davevoyles/mnt/ROMs/ROMs/<system>/",
            "NAS SSH path:   /volume1/ROMs/ROMs/<system>/",
            "Systems include: Sega - Saturn, Sega - Genesis, Nintendo - NES, Sony - PlayStation, etc.",
            "",
            "## Other paths",
            "AI files: /Users/davevoyles/ai-files",
            "OpenClaw repo: /Users/davevoyles/openclaw",
            "Docker stack:  /Users/davevoyles/docker-stack",
            "",
            "## Instruction files — read these for the relevant task type",
            "Base execution rules (always apply):    /Users/davevoyles/openclaw/.github/copilot-instructions.md",
            "Fleet/multi-agent orchestration rules:  /Users/davevoyles/openclaw/.github/agents/autonomous-fleet-agent.md",
            "OpenClaw context entrypoint (load first for openclaw tasks): /Users/davevoyles/openclaw/.github/docs/README.md",
            "Docker-stack agent guide (load for infra/Docker/NAS tasks):  /Users/davevoyles/docker-stack/docs/AGENT-GUIDE.md",
            "NAS access quick-reference:             /Users/davevoyles/openclaw/.github/docs/NAS-ACCESS.md",
            "",
            "## Preferred approach for NAS file tasks",
            "1. Read/list files: use the SMB mount at /Users/davevoyles/mnt/ROMs/ directly.",
            "2. Run NAS-side commands (Docker, DSM): ssh -p 24 dave@192.168.1.8 '<cmd>'",
            "3. Create share links: POST https://openclaw.davevoyles.synology.me/tools/share_file",
            "   with JSON body: {\"path\": \"/Users/davevoyles/mnt/ROMs/ROMs/<system>/<file>\"}",
            "",
        ]
        prompt = "\n".join(context_lines) + prompt

        # Prepend conversation history so Copilot CLI has context for multi-turn chats.
        if history:
            history_lines = ["[Conversation history]"]
            for turn in history:
                role_label = "User" if turn.get("role") == "user" else "Assistant"
                history_lines.append(f"{role_label}: {turn.get('content', '').strip()}")
            history_lines.append("")  # blank line separator
            prompt = "\n".join(history_lines) + f"Current question: {prompt}"

        if do_stream:
            stream_resp = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
            await stream_resp.prepare(request)

            role_chunk = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created_ts, "model": _copilot_model_name,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            await stream_resp.write(f"data: {json.dumps(role_chunk)}\n\n".encode())

            _accumulated_copilot: list[str] = []
            try:
                async for event in _copilot_event_stream():
                    if event.get("type") in ("chunk", "stderr"):
                        text = event.get("text", "")
                        if _copilot_backend != "hermes":
                            if _is_copilot_noise_line(text):
                                continue
                            tool_label = _copilot_tool_label(text)
                            if tool_label:
                                # Emit as a special tool-progress chunk so the dashboard can style it.
                                progress_chunk = {
                                    "id": completion_id, "object": "chat.completion.chunk",
                                    "created": created_ts, "model": _copilot_model_name,
                                    "choices": [{"index": 0, "delta": {"content": tool_label + "\n"}, "finish_reason": None}],
                                    "x_tool_progress": True,
                                }
                                await stream_resp.write(f"data: {json.dumps(progress_chunk)}\n\n".encode())
                                continue
                        _accumulated_copilot.append(text)
                        chunk = {
                            "id": completion_id, "object": "chat.completion.chunk",
                            "created": created_ts, "model": _copilot_model_name,
                            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                        }
                        await stream_resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    elif event.get("type") == "error":
                        err_text = f"\n⚠️ Copilot error: {event.get('error', 'Unknown error')}"
                        err_chunk = {
                            "id": completion_id, "object": "chat.completion.chunk",
                            "created": created_ts, "model": _copilot_model_name,
                            "choices": [{"index": 0, "delta": {"content": err_text}, "finish_reason": "stop"}],
                        }
                        await stream_resp.write(f"data: {json.dumps(err_chunk)}\n\n".encode())
            except Exception as exc:
                log.error("api_v1_chat_completions_handler copilot stream error: %s", exc)
                err_chunk = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created_ts, "model": _copilot_model_name,
                    "choices": [{"index": 0, "delta": {"content": f"\n[error: {exc}]"}, "finish_reason": "stop"}],
                }
                await stream_resp.write(f"data: {json.dumps(err_chunk)}\n\n".encode())

            share_suffix = await _try_share_links("".join(_accumulated_copilot))
            if share_suffix:
                share_chunk = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created_ts, "model": _copilot_model_name,
                    "choices": [{"index": 0, "delta": {"content": share_suffix}, "finish_reason": None}],
                }
                await stream_resp.write(f"data: {json.dumps(share_chunk)}\n\n".encode())

            stop_chunk = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created_ts, "model": _copilot_model_name,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            await stream_resp.write(f"data: {json.dumps(stop_chunk)}\n\n".encode())
            await stream_resp.write(b"data: [DONE]\n\n")
            await stream_resp.write_eof()
            return stream_resp

        # Non-streaming copilot path
        try:
            if _copilot_backend == "hermes":
                _copilot_parts: list[str] = []
                _copilot_error = ""
                async for event in _copilot_event_stream():
                    event_type = event.get("type")
                    if event_type in ("chunk", "stderr"):
                        _copilot_parts.append(event.get("text", ""))
                    elif event_type == "error":
                        _copilot_error = str(event.get("error") or "Unknown error")
                    elif event_type == "done" and not event.get("success", True):
                        _copilot_error = str(event.get("error") or _copilot_error or "Unknown error")

                copilot_text = f"⚠️ Copilot error: {_copilot_error}" if _copilot_error else "".join(_copilot_parts).strip()
            else:
                bridge_result = await run_copilot(prompt=prompt, slack_user_id="open-webui")
                if bridge_result.error:
                    # Return error as an assistant message so Open WebUI displays it inline.
                    copilot_text = f"⚠️ Copilot error: {bridge_result.error}"
                else:
                    copilot_text = "\n".join(
                        line for line in (bridge_result.stdout or "").splitlines()
                        if not _is_copilot_noise_line(line)
                    ).strip()
        except Exception as exc:
            log.error("api_v1_chat_completions_handler copilot run error: %s", exc)
            return web.json_response(
                {"error": {"message": str(exc), "type": "server_error"}}, status=500
            )
        copilot_text += await _try_share_links(copilot_text)
        completion_tokens_est = len(copilot_text.encode()) // 4
        return web.json_response({
            "id": completion_id, "object": "chat.completion", "created": created_ts,
            "model": _copilot_model_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": copilot_text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens_est, "completion_tokens": completion_tokens_est, "total_tokens": prompt_tokens_est + completion_tokens_est},
        })

    # ------------------------------------------------------------------
    # Shell model path — run raw bash command on Mac Mini via SSH bridge
    # ------------------------------------------------------------------
    if model_pref == "shell":
        try:
            from host_bridge import run_shell, run_shell_stream
        except ImportError as exc:
            return web.json_response(
                {"error": {"message": f"host_bridge unavailable: {exc}", "type": "server_error"}},
                status=500,
            )

        if do_stream:
            stream_resp = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
            await stream_resp.prepare(request)

            role_chunk = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created_ts, "model": "shell",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            await stream_resp.write(f"data: {json.dumps(role_chunk)}\n\n".encode())

            _accumulated_shell: list[str] = []
            try:
                async for event in run_shell_stream(command=prompt, slack_user_id="open-webui"):
                    if event.get("type") in ("chunk", "stderr"):
                        text = event.get("text", "")
                        _accumulated_shell.append(text)
                        chunk = {
                            "id": completion_id, "object": "chat.completion.chunk",
                            "created": created_ts, "model": "shell",
                            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                        }
                        await stream_resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    elif event.get("type") == "error":
                        err_text = f"\n⚠️ Shell error: {event.get('error', 'Unknown error')}"
                        err_chunk = {
                            "id": completion_id, "object": "chat.completion.chunk",
                            "created": created_ts, "model": "shell",
                            "choices": [{"index": 0, "delta": {"content": err_text}, "finish_reason": "stop"}],
                        }
                        await stream_resp.write(f"data: {json.dumps(err_chunk)}\n\n".encode())
            except Exception as exc:
                log.error("api_v1_chat_completions_handler shell stream error: %s", exc)
                err_chunk = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created_ts, "model": "shell",
                    "choices": [{"index": 0, "delta": {"content": f"\n[error: {exc}]"}, "finish_reason": "stop"}],
                }
                await stream_resp.write(f"data: {json.dumps(err_chunk)}\n\n".encode())

            share_suffix = await _try_share_links("".join(_accumulated_shell))
            if share_suffix:
                share_chunk = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created_ts, "model": "shell",
                    "choices": [{"index": 0, "delta": {"content": share_suffix}, "finish_reason": None}],
                }
                await stream_resp.write(f"data: {json.dumps(share_chunk)}\n\n".encode())

            stop_chunk = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created_ts, "model": "shell",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            await stream_resp.write(f"data: {json.dumps(stop_chunk)}\n\n".encode())
            await stream_resp.write(b"data: [DONE]\n\n")
            await stream_resp.write_eof()
            return stream_resp

        # Non-streaming shell path
        try:
            bridge_result = await run_shell(command=prompt, slack_user_id="open-webui")
        except Exception as exc:
            log.error("api_v1_chat_completions_handler shell run error: %s", exc)
            return web.json_response(
                {"error": {"message": str(exc), "type": "server_error"}}, status=500
            )
        if bridge_result.error:
            shell_text = f"⚠️ Shell error: {bridge_result.error}"
        else:
            shell_text = (bridge_result.stdout or "").rstrip()
            if bridge_result.stderr and not bridge_result.success:
                shell_text += f"\n\n[stderr]\n{bridge_result.stderr.rstrip()}"
        shell_text += await _try_share_links(shell_text)
        completion_tokens_est = len(shell_text.encode()) // 4
        return web.json_response({
            "id": completion_id, "object": "chat.completion", "created": created_ts,
            "model": "shell",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": shell_text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens_est, "completion_tokens": completion_tokens_est, "total_tokens": prompt_tokens_est + completion_tokens_est},
        })

    # ------------------------------------------------------------------
    # Streaming path — LLM dispatch via _execute_agent_ask
    # ------------------------------------------------------------------
    if do_stream:
        stream_resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await stream_resp.prepare(request)

        # Buffer to hold the last chunk; lets us strip _via footer before emitting stop.
        _stream_pending: list[str] = []

        async def _send_chunk(delta_content: str) -> None:
            # Flush any previously held chunk before buffering the new one.
            if _stream_pending:
                prev = _stream_pending.pop()
                ch = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created_ts, "model": model_pref,
                    "choices": [{"index": 0, "delta": {"content": prev}, "finish_reason": None}],
                }
                await stream_resp.write(f"data: {json.dumps(ch)}\n\n".encode())
            _stream_pending.append(delta_content)

        # First chunk carries role
        role_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model_pref,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        await stream_resp.write(f"data: {json.dumps(role_chunk)}\n\n".encode())

        try:
            await _execute_agent_ask(
                prompt=prompt,
                model_pref=model_pref,
                history=history,
                user_name="open-webui",
                on_partial_chunk=_send_chunk,
            )
        except Exception as exc:
            log.error("api_v1_chat_completions_handler (stream) error: %s", exc)
            err_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model_pref,
                "choices": [{"index": 0, "delta": {"content": f"\n[error: {exc}]"}, "finish_reason": "stop"}],
            }
            await stream_resp.write(f"data: {json.dumps(err_chunk)}\n\n".encode())

        # Flush final pending chunk with _via footer stripped, then emit stop.
        if _stream_pending:
            last = _strip_via_footer(_stream_pending.pop())
            if last:
                final_ch = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created_ts, "model": model_pref,
                    "choices": [{"index": 0, "delta": {"content": last}, "finish_reason": None}],
                }
                await stream_resp.write(f"data: {json.dumps(final_ch)}\n\n".encode())

        # Final stop chunk
        stop_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model_pref,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        await stream_resp.write(f"data: {json.dumps(stop_chunk)}\n\n".encode())
        await stream_resp.write(b"data: [DONE]\n\n")
        await stream_resp.write_eof()
        return stream_resp

    # ------------------------------------------------------------------
    # Non-streaming path — LLM dispatch
    # ------------------------------------------------------------------
    try:
        result = await _execute_agent_ask(
            prompt=prompt,
            model_pref=model_pref,
            history=history,
            user_name="open-webui",
        )
    except Exception as exc:
        log.error("api_v1_chat_completions_handler error: %s", exc)
        return web.json_response(
            {"error": {"message": str(exc), "type": "server_error"}},
            status=500,
        )

    response_text = _strip_via_footer(str(result.get("response") or ""))
    model_used = str(result.get("model") or model_pref)
    completion_tokens = int(result.get("tokens") or 0)

    return web.json_response({
        "id": completion_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": model_used,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens_est,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens_est + completion_tokens,
        },
    })


async def api_recap_generate_handler(request: web.Request) -> web.Response:
    """POST /api/recap/generate — Generate a recap report on demand.

    Body (JSON):
        days   (int, optional)    — number of days to cover (default: 7)
        style  (str, optional)    — "highlights" | "action-items" | "table" (default: "highlights")
        focus  (str, optional)    — optional topic/angle to emphasize

    Returns JSON:
        report   (str)  — the generated report text
        model    (str)  — model used
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}

    days: int = int(body.get("days", 7))
    style: str = body.get("style", "highlights")
    focus: str = body.get("focus", "")

    days = max(1, min(days, 30))
    if style not in ("highlights", "action-items", "table"):
        style = "highlights"

    prompt_parts = [
        f"Generate a {style} weekly recap for the past {days} days.",
        "Summarize key activities, decisions, and outcomes.",
    ]
    if focus:
        prompt_parts.append(f"Focus on: {focus}.")

    prompt = " ".join(prompt_parts)

    try:
        from llm.chat import chat as llm_chat

        result = await llm_chat(
            user_message=prompt,
            user_name="Dashboard",
            model_preference="auto",
        )
        if isinstance(result, tuple):
            response_text, _hist, meta = result
        else:
            response_text, meta = result, {}

        model_used = meta.get("model_used", "auto") if isinstance(meta, dict) else "auto"

        return web.json_response({"report": response_text, "model": model_used})
    except Exception as exc:  # broad: intentional
        log.error("api_recap_generate_handler error: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def api_copilot_ping_handler(request: web.Request) -> web.Response:
    """POST /api/copilot/ping — Lightweight SSH connectivity test for the host bridge.

    Runs ``echo __ping_ok__`` over SSH (no copilot invocation).  Used by the
    dashboard status indicator to show whether the bridge is reachable.

    Returns JSON: {"ok": bool, "latency_ms": float|null, "error": str|null}
    """
    from host_bridge import ping_bridge

    result = await ping_bridge(timeout_s=8)
    return web.json_response(result)


async def api_copilot_stream_handler(request: web.Request) -> web.StreamResponse:
    """POST /api/copilot/stream — Stream Copilot CLI output as Server-Sent Events.

    Body (JSON):
        prompt     (str, required)  — prompt to pass to `copilot -p <prompt>`
        timeout_s  (int, optional)  — max seconds (default: host_bridge default)
        workdir    (str, optional)  — working directory on host (overrides OPENCLAW_HOST_BRIDGE_WORKDIR)

    SSE events emitted:
        event: chunk   data: {"text": str}               — stdout line
        event: stderr  data: {"text": str}               — stderr line
        event: done    data: {"success": bool, "duration_s": float, ...}
        event: error   data: {"error": str}
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"error": "prompt is required"}, status=400)

    timeout_s: int | None = None
    raw_timeout = body.get("timeout_s")
    if raw_timeout is not None:
        try:
            timeout_s = int(raw_timeout)
        except (TypeError, ValueError):
            pass

    workdir: str | None = (body.get("workdir") or "").strip() or None

    try:
        from host_bridge import run_copilot_stream
    except ImportError as exc:
        return web.json_response({"error": f"host_bridge not available: {exc}"}, status=500)

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)

    try:
        async for event in run_copilot_stream(
            prompt=prompt,
            slack_user_id="dashboard-ui",
            timeout_s=timeout_s,
            workdir=workdir,
        ):
            event_type = event.get("type", "chunk")
            payload = json.dumps(event, ensure_ascii=False)
            await resp.write(f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8"))
    except Exception as exc:
        log.error("api_copilot_stream_handler error: %s", exc)
        payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
        await resp.write(f"event: error\ndata: {payload}\n\n".encode("utf-8"))
    finally:
        await resp.write_eof()

    return resp


async def api_copilot_run_handler(request: web.Request) -> web.Response:
    """POST /api/copilot/run — Run a single Copilot CLI prompt on the Mac Mini host.

    Body (JSON):
        prompt     (str, required)  — the prompt to pass to `copilot -p <prompt>`
        timeout_s  (int, optional)  — max seconds to wait (default: host_bridge default)

    Returns JSON:
        response   (str)   — combined stdout (+ stderr on failure)
        model      (str)   — always "copilot-cli"
        session_id (str)   — audit session ID
        duration_s (float) — wall-clock seconds
        success    (bool)  — whether the command exited cleanly
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"error": "prompt is required"}, status=400)

    timeout_s: int | None = None
    raw_timeout = body.get("timeout_s")
    if raw_timeout is not None:
        try:
            timeout_s = int(raw_timeout)
        except (TypeError, ValueError):
            pass

    try:
        from host_bridge import run_copilot
    except ImportError as exc:
        return web.json_response({"error": f"host_bridge not available: {exc}"}, status=500)

    result = await run_copilot(
        prompt=prompt,
        slack_user_id="dashboard-ui",
        timeout_s=timeout_s,
    )

    if result.error:
        return web.json_response(
            {
                "error": result.error,
                "response": result.error,
                "model": "copilot-cli",
                "session_id": result.session_id,
                "duration_s": result.duration_s,
                "success": False,
            },
            status=200,  # surface error in the UI rather than as HTTP error
        )

    response_text = result.stdout or ""
    if result.stderr and not result.success:
        response_text = f"{response_text}\n\n[stderr]\n{result.stderr}".strip()

    return web.json_response(
        {
            "response": response_text,
            "model": "copilot-cli",
            "session_id": result.session_id,
            "duration_s": round(result.duration_s, 2),
            "success": result.success,
        }
    )


async def api_copilot_sessions_handler(request: web.Request) -> web.Response:
    """GET /api/copilot/sessions — List active Copilot CLI sessions."""
    import time as _time
    try:
        from host_bridge import get_session_manager
    except ImportError:
        return web.json_response({"sessions": [], "error": "host_bridge unavailable"})

    mgr = get_session_manager()
    sessions = []
    for rec in mgr.list_sessions():
        sessions.append({
            "session_id": rec.session_id,
            "slack_user": rec.slack_user,
            "cwd": getattr(rec, "cwd", ""),
            "status": rec.status,
            "turns": getattr(rec, "turns", 0),
            "started_at": getattr(rec, "started_at", 0),
            "last_activity": getattr(rec, "last_activity", 0),
            "age_s": int(_time.time() - getattr(rec, "started_at", _time.time())),
            "idle_s": int(_time.time() - getattr(rec, "last_activity", _time.time())),
            "live": mgr.is_live(rec.session_id),
        })
    return web.json_response({"sessions": sessions})


async def api_hermes_status_handler(request: web.Request) -> web.Response:
    """Return Hermes agent status and stats."""
    import os
    import sqlite3

    import yaml

    hermes_home = Path("/Users/davevoyles/.hermes")
    binary = Path("/Users/davevoyles/.local/bin/hermes")
    result: dict[str, object] = {"installed": False}

    if not hermes_home.exists() and not binary.exists():
        return web.json_response({
            "installed": False,
            "note": "Hermes runs on host, not in container",
        })

    try:
        result["installed"] = binary.exists()

        config_path = hermes_home / "config.yaml"
        if config_path.exists():
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
            if isinstance(model_cfg, dict):
                result["model"] = model_cfg.get("default", "unknown")
                result["provider"] = model_cfg.get("provider", "unknown")

        memories_path = hermes_home / "memories"
        memory_md = memories_path / "MEMORY.md"
        user_md = memories_path / "USER.md"
        result["memory_md_chars"] = len(memory_md.read_text(encoding="utf-8")) if memory_md.exists() else 0
        result["user_md_chars"] = len(user_md.read_text(encoding="utf-8")) if user_md.exists() else 0

        skills_path = hermes_home / "skills"
        custom_skill_count = len(list(skills_path.iterdir())) if skills_path.exists() else 0
        result["skill_count"] = custom_skill_count
        result["skill_count_label"] = f"{custom_skill_count} custom + 90 bundled"

        state_db = hermes_home / "state.db"
        result["state_db_exists"] = state_db.exists()
        if state_db.exists():
            try:
                # Use immutable=1 URI so SQLite skips locking (safe on a read-only mount)
                conn = sqlite3.connect(f"file:{state_db}?immutable=1", uri=True)
                rows = conn.execute(
                    "SELECT id, started_at, message_count FROM sessions ORDER BY started_at DESC LIMIT 3"
                ).fetchall()
                conn.close()
                if rows:
                    result["last_session_id"] = rows[0][0]
                    result["last_session_at"] = rows[0][1]
                    result["recent_sessions"] = [
                        {"id": r[0], "created_at": r[1], "messages": r[2] or 0}
                        for r in rows
                    ]
            except Exception:
                pass

        result["copilot_backend"] = os.environ.get("COPILOT_BACKEND", "ssh")
    except Exception as exc:
        result["error"] = str(exc)

    return web.json_response(result)


async def api_hermes_sessions_handler(request: web.Request) -> web.Response:
    """GET /api/hermes/sessions — list recent Hermes sessions."""
    state_db = Path("/Users/davevoyles/.hermes/state.db")
    if not state_db.exists():
        return web.json_response({"sessions": []})
    try:
        import datetime
        import sqlite3

        loop = asyncio.get_event_loop()

        def _read_sessions():
            conn = sqlite3.connect(f"file:{state_db}?immutable=1", uri=True)
            try:
                rows = conn.execute(
                    "SELECT id, model, started_at, ended_at, message_count, title "
                    "FROM sessions ORDER BY started_at DESC LIMIT 20"
                ).fetchall()
                sessions = []
                for row in rows:
                    sid, model, started, ended, msg_count, title = row
                    try:
                        dt = datetime.datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        dt = str(started)
                    sessions.append(
                        {
                            "id": sid,
                            "model": model or "unknown",
                            "started_at": dt,
                            "message_count": msg_count or 0,
                            "title": title or sid,
                        }
                    )
                return sessions
            finally:
                conn.close()

        sessions = await loop.run_in_executor(None, _read_sessions)
        return web.json_response({"sessions": sessions})
    except Exception as exc:
        return web.json_response({"sessions": [], "error": str(exc)})


async def api_hermes_session_detail_handler(request: web.Request) -> web.Response:
    """GET /api/hermes/sessions/{session_id} — get messages for a session."""
    session_id = request.match_info.get("session_id", "")
    state_db = Path("/Users/davevoyles/.hermes/state.db")
    if not state_db.exists() or not session_id:
        return web.json_response({"messages": []})
    try:
        import sqlite3

        loop = asyncio.get_event_loop()

        def _read_messages():
            conn = sqlite3.connect(f"file:{state_db}?immutable=1", uri=True)
            try:
                rows = conn.execute(
                    "SELECT role, content, timestamp FROM messages "
                    "WHERE session_id = ? AND active = 1 ORDER BY timestamp ASC",
                    (session_id,),
                ).fetchall()
                messages = []
                for role, content, ts in rows:
                    messages.append({"role": role, "content": (content or "")[:2000], "timestamp": ts})
                return messages
            finally:
                conn.close()

        messages = await loop.run_in_executor(None, _read_messages)
        return web.json_response({"session_id": session_id, "messages": messages})
    except Exception as exc:
        return web.json_response({"messages": [], "error": str(exc)})


async def api_hermes_memory_handler(request: web.Request) -> web.Response:
    """GET/POST /api/hermes/memory — read or update Hermes MEMORY.md (or SOUL.md with ?file=soul)."""
    file_param = request.rel_url.query.get("file", "memory")
    if file_param == "soul":
        target_file = Path("/Users/davevoyles/.hermes/SOUL.md")
    else:
        target_file = Path("/Users/davevoyles/.hermes/memories/MEMORY.md")

    if request.method == "GET":
        content = target_file.read_text(encoding="utf-8") if target_file.exists() else ""
        return web.json_response({"content": content, "path": str(target_file), "file": file_param})

    body = await request.json()
    new_content = body.get("content", "")
    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(new_content, encoding="utf-8")
    except OSError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
    return web.json_response({"ok": True, "bytes": len(new_content.encode("utf-8")), "file": file_param})


async def api_hermes_memory_seed_handler(request: web.Request) -> web.Response:
    """GET /api/hermes/memory-seed — Serve MEMORY.md for new machine installs."""
    memory_file = Path("/Users/davevoyles/.hermes/memories/MEMORY.md")
    if not memory_file.exists():
        return web.Response(text=f"# Hermes Memory — {os.uname().nodename}\n", content_type="text/plain")
    return web.Response(
        text=memory_file.read_text(encoding="utf-8"),
        content_type="text/plain",
        headers={"Cache-Control": "no-cache"},
    )


async def api_hermes_skills_seed_handler(request: web.Request) -> web.Response:
    """GET /api/hermes/skills-seed — Serve custom skills as tar.gz for new machine installs."""
    import io
    import tarfile

    skills_path = Path("/Users/davevoyles/.hermes/skills")
    if not skills_path.exists():
        return web.Response(status=204, text="no custom skills")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for skill_file in skills_path.iterdir():
            if skill_file.is_file():
                tar.add(str(skill_file), arcname=skill_file.name)
    buf.seek(0)

    return web.Response(
        body=buf.read(),
        content_type="application/gzip",
        headers={
            "Content-Disposition": "attachment; filename=hermes-skills.tar.gz",
            "Cache-Control": "no-cache",
        },
    )


async def api_hermes_ask_handler(request: web.Request) -> web.StreamResponse:
    """POST /api/hermes/ask — Stream a single Hermes query as Server-Sent Events.

    Body (JSON):
        prompt             (str, required)  — prompt for Hermes
        hermes_session_id  (str, optional)  — resume a prior Hermes session

    SSE events emitted:
        event: chunk   data: {"text": str}
        event: done    data: {"success": bool, "duration_s": float}
        event: error   data: {"error": str}
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"error": "prompt is required"}, status=400)

    hermes_session_id: str | None = (body.get("hermes_session_id") or "").strip() or None

    try:
        from host_bridge import run_hermes_stream
    except ImportError as exc:
        return web.json_response({"error": f"host_bridge not available: {exc}"}, status=500)

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)

    try:
        async for event in run_hermes_stream(
            prompt=prompt,
            slack_user_id="dashboard-ui",
            hermes_session_id=hermes_session_id,
        ):
            event_type = event.get("type", "chunk")
            payload = json.dumps(event, ensure_ascii=False)
            await resp.write(f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8"))
    except Exception as exc:
        log.error("api_hermes_ask_handler error: %s", exc)
        err_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
        await resp.write(f"event: error\ndata: {err_payload}\n\n".encode("utf-8"))
    finally:
        await resp.write_eof()

    return resp


_DOCKER_CONTAINER_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,128}$")
_ORBSTACK_DOCKER = "/Applications/OrbStack.app/Contents/MacOS/xbin/docker"


def _docker_bin() -> str:
    return _ORBSTACK_DOCKER if Path(_ORBSTACK_DOCKER).exists() else "docker"


async def api_docker_action_handler(request: web.Request) -> web.Response:
    """POST /api/docker/action — Run a lifecycle action on a Docker container.

    Body (JSON):
        action     (str, required) — one of: restart, stop, start
        container  (str, required) — container name or ID

    Returns JSON:
        success  (bool)
        output   (str)
        error    (str)
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    action = (body.get("action") or "").strip().lower()
    container = (body.get("container") or "").strip()

    if action not in ("restart", "stop", "start"):
        return web.json_response({"error": "action must be one of: restart, stop, start"}, status=400)

    if not container or not _DOCKER_CONTAINER_RE.match(container):
        return web.json_response(
            {"error": "container must be a non-empty name (alphanumeric, dash, underscore, dot; max 128 chars)"},
            status=400,
        )

    log.info("docker action %s on %s", action, container)
    try:
        proc = await asyncio.create_subprocess_exec(
            _docker_bin(),
            action,
            container,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        success = proc.returncode == 0
        return web.json_response(
            {
                "success": success,
                "output": stdout.decode(errors="replace").strip(),
                "error": stderr.decode(errors="replace").strip() if not success else "",
            }
        )
    except asyncio.TimeoutError:
        return web.json_response({"success": False, "output": "", "error": "docker command timed out"}, status=504)
    except OSError as exc:
        log.error("api_docker_action_handler OSError: %s", exc)
        return web.json_response({"success": False, "output": "", "error": str(exc)}, status=500)


async def api_docker_logs_handler(request: web.Request) -> web.Response:
    """GET /api/docker/logs — Fetch recent logs for a Docker container.

    Query params:
        service  (str, required)      — container name or ID
        lines    (int, optional=50)   — number of tail lines (max 200)

    Returns JSON:
        service  (str)
        lines    (int)
        output   (str)
    """
    service = (request.rel_url.query.get("service") or "").strip()
    if not service or not _DOCKER_CONTAINER_RE.match(service):
        return web.json_response(
            {"error": "service must be a non-empty container name (alphanumeric, dash, underscore, dot; max 128 chars)"},
            status=400,
        )

    try:
        lines = int(request.rel_url.query.get("lines", 50))
    except (TypeError, ValueError):
        lines = 50
    lines = max(1, min(lines, 200))

    try:
        proc = await asyncio.create_subprocess_exec(
            _docker_bin(),
            "logs",
            "--tail",
            str(lines),
            service,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode(errors="replace").strip()
        if proc.returncode != 0 and not output:
            return web.json_response({"error": f"docker logs exited {proc.returncode}"}, status=500)
        return web.json_response({"service": service, "lines": lines, "output": output})
    except asyncio.TimeoutError:
        return web.json_response({"error": "docker logs timed out"}, status=504)
    except OSError as exc:
        log.error("api_docker_logs_handler OSError: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# OpenClaw Tool Server — OpenAPI-compatible endpoints for Open WebUI tool calling
# ---------------------------------------------------------------------------
# Configure in Open WebUI: Admin → Tools → Tool Servers → add http://openclaw:8765
# Works automatically with function-calling models (Claude, GPT-4o).
# Gemma (no native tool calling) can use the /v1 shell model directly instead.
# ---------------------------------------------------------------------------

_TOOL_SERVER_SPEC = {
    "openapi": "3.1.0",
    "info": {
        "title": "OpenClaw Tool Server",
        "version": "1.0.0",
        "description": "Access Mac Mini files and shell via OpenClaw SSH bridge",
    },
    "servers": [{"url": "/tools"}],
    "paths": {
        "/search_files": {
            "post": {
                "operationId": "search_files",
                "summary": "Search for files by name pattern on the Mac Mini / NAS",
                "description": "Runs `find <path> -name '<query>'` on the Mac Mini host. The NAS is mounted at /Volumes/ROMs. Returns up to 50 results.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["query"],
                                "properties": {
                                    "query": {"type": "string", "description": "File name glob pattern (e.g. '*.md', 'shmups*')"},
                                    "path": {"type": "string", "description": "Directory to search (default: /)", "default": "/"},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Matching file paths",
                        "content": {"application/json": {"schema": {"type": "object", "properties": {"results": {"type": "string"}}}}},
                    }
                },
            }
        },
        "/read_file": {
            "post": {
                "operationId": "read_file",
                "summary": "Read the contents of a file on the Mac Mini / NAS",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["path"],
                                "properties": {
                                    "path": {"type": "string", "description": "Absolute file path to read"},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "File contents",
                        "content": {"application/json": {"schema": {"type": "object", "properties": {"content": {"type": "string"}}}}},
                    }
                },
            }
        },
        "/share_file": {
            "post": {
                "operationId": "share_file",
                "summary": "Create a Synology share link for a file or folder on the NAS",
                "description": "Calls the Synology FileStation Sharing API to generate a shareable download URL. Accepts Mac Mini paths (/Volumes/ROMs/...) or NAS FileStation paths (/ROMs/...).",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["path"],
                                "properties": {
                                    "path": {"type": "string", "description": "File or folder path (Mac or NAS format)"},
                                    "expire_days": {"type": "integer", "description": "Days until link expires (0 = never)", "default": 0},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Share link URL",
                        "content": {"application/json": {"schema": {"type": "object", "properties": {"url": {"type": "string"}, "message": {"type": "string"}}}}},
                    }
                },
            }
        },
        "/run_shell": {
            "post": {
                "operationId": "run_shell",
                "summary": "Run a bash command on the Mac Mini host",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["command"],
                                "properties": {
                                    "command": {"type": "string", "description": "Bash command to execute"},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Command output",
                        "content": {"application/json": {"schema": {"type": "object", "properties": {"output": {"type": "string"}}}}},
                    }
                },
            }
        },
    },
}


async def api_tools_openapi_handler(request: web.Request) -> web.Response:
    """GET /tools/openapi.json — OpenAPI 3.1 spec for the OpenClaw Tool Server."""
    return web.json_response(_TOOL_SERVER_SPEC)


async def api_tools_search_files_handler(request: web.Request) -> web.Response:
    """POST /tools/search_files — find files by name pattern via SSH bridge."""
    if not _v1_auth_ok(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    query = (body.get("query") or "").strip()
    if not query:
        return web.json_response({"error": "query is required"}, status=400)
    path = (body.get("path") or "/").strip() or "/"

    import shlex as _shlex
    command = f"find {_shlex.quote(path)} -name {_shlex.quote(query)} 2>/dev/null | head -50"

    try:
        from host_bridge import run_shell
    except ImportError as exc:
        return web.json_response({"error": f"host_bridge unavailable: {exc}"}, status=500)

    result = await run_shell(command=command, slack_user_id="tool-server")
    if result.error:
        return web.json_response({"error": result.error}, status=500)
    return web.json_response({"results": result.stdout.strip() or "(no results)"})


async def api_tools_read_file_handler(request: web.Request) -> web.Response:
    """POST /tools/read_file — read a file by absolute path via SSH bridge."""
    if not _v1_auth_ok(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    path = (body.get("path") or "").strip()
    if not path:
        return web.json_response({"error": "path is required"}, status=400)

    import shlex as _shlex
    command = f"cat {_shlex.quote(path)}"

    try:
        from host_bridge import run_shell
    except ImportError as exc:
        return web.json_response({"error": f"host_bridge unavailable: {exc}"}, status=500)

    result = await run_shell(command=command, slack_user_id="tool-server")
    if result.error:
        return web.json_response({"error": result.error}, status=500)
    if not result.success:
        return web.json_response({"error": result.stderr or "File not found or permission denied"}, status=404)
    return web.json_response({"content": result.stdout})


async def api_tools_run_shell_handler(request: web.Request) -> web.Response:
    """POST /tools/run_shell — execute an arbitrary bash command on the Mac Mini."""
    if not _v1_auth_ok(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    command = (body.get("command") or "").strip()
    if not command:
        return web.json_response({"error": "command is required"}, status=400)

    try:
        from host_bridge import run_shell
    except ImportError as exc:
        return web.json_response({"error": f"host_bridge unavailable: {exc}"}, status=500)

    result = await run_shell(command=command, slack_user_id="tool-server")
    if result.error:
        return web.json_response({"error": result.error}, status=500)
    output = result.stdout or ""
    if result.stderr:
        output += f"\n[stderr]\n{result.stderr}"
    return web.json_response({"output": output.rstrip()})


async def api_tools_share_file_handler(request: web.Request) -> web.Response:
    """POST /tools/share_file — create a Synology share link for a file or folder."""
    if not _v1_auth_ok(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    path = (body.get("path") or "").strip()
    if not path:
        return web.json_response({"error": "path is required"}, status=400)
    expire_days = int(body.get("expire_days") or 0)

    try:
        from nas import nas_create_share_link
    except ImportError as exc:
        return web.json_response({"error": f"nas module unavailable: {exc}"}, status=500)

    result_msg = await nas_create_share_link(path, expire_days=expire_days)
    if result_msg.startswith("❌"):
        return web.json_response({"error": result_msg}, status=500)

    # Extract the URL from the message for clean JSON response
    url = ""
    for part in result_msg.split():
        if part.startswith("http"):
            url = part
            break
    return web.json_response({"url": url, "message": result_msg})



async def api_changelog_handler(request: web.Request) -> web.Response:
    """GET /api/changelog — return last N entries from history.md."""
    history_file = Path(__file__).parent.parent.parent / "history.md"
    if not history_file.exists():
        history_file = Path("/app/history.md")
    if not history_file.exists():
        return web.json_response({"entries": [], "error": "history.md not found"})

    try:
        lines = history_file.read_text(encoding="utf-8").strip().splitlines()
        entries = []
        for line in reversed(lines):
            line = line.strip()
            if not line or not line.startswith("- "):
                continue
            content = line[2:]
            date_str = ""
            text = content
            if ": " in content[:15]:
                parts = content.split(": ", 1)
                date_str = parts[0].strip()
                text = parts[1].strip() if len(parts) > 1 else content
            entries.append({"date": date_str, "text": text, "raw": line})
            if len(entries) >= 15:
                break
        return web.json_response({"entries": entries})
    except Exception as exc:
        return web.json_response({"entries": [], "error": str(exc)})


async def api_nas_disk_handler(request: web.Request) -> web.Response:
    """GET /api/nas/disk — return NAS share disk usage via host bridge df."""
    import os
    import shutil

    results: list[dict[str, str]] = []
    source = "local"

    bridge_enabled = os.environ.get("OPENCLAW_HOST_BRIDGE_ENABLED", "false").lower() == "true"
    if bridge_enabled:
        try:
            from host_bridge import run_shell

            cmd = (
                "df -h /Users/davevoyles/mnt/PlexMediaServer "
                "/Users/davevoyles/mnt/docker "
                "/Users/davevoyles/mnt/Misc "
                "2>/dev/null | tail -n +2 || "
                "df -h /Users/davevoyles/mnt 2>/dev/null | tail -n +2"
            )
            bridge_result = await run_shell(command=cmd, slack_user_id="dashboard", timeout_s=10)
            output = bridge_result if isinstance(bridge_result, str) else (getattr(bridge_result, "stdout", "") or "")
            bridge_error = "" if isinstance(bridge_result, str) else getattr(bridge_result, "error", "")
            if bridge_error:
                log.warning("api_nas_disk: host bridge error: %s", bridge_error)
            for line in output.strip().splitlines():
                parts = line.split()
                if len(parts) < 6:
                    continue
                mount = " ".join(parts[5:])
                results.append(
                    {
                        "filesystem": parts[0],
                        "size": parts[1],
                        "used": parts[2],
                        "avail": parts[3],
                        "use_pct": parts[4],
                        "mount": mount,
                        "label": mount.rstrip("/").split("/")[-1] or mount,
                    }
                )
            if results:
                source = "host_bridge"
        except Exception as exc:
            log.warning("api_nas_disk: host bridge error: %s", exc)

    if not results:
        for mount_path, label in [
            ("/Users/davevoyles/mnt/PlexMediaServer", "PlexMediaServer"),
            ("/Users/davevoyles/mnt/docker", "docker"),
            ("/Users/davevoyles/mnt/Misc", "Misc"),
        ]:
            try:
                usage = shutil.disk_usage(mount_path)
                total_gb = usage.total / 1024**3
                used_gb = usage.used / 1024**3
                free_gb = usage.free / 1024**3
                pct = int(usage.used * 100 / usage.total) if usage.total > 0 else 0
                results.append(
                    {
                        "label": label,
                        "size": f"{total_gb:.0f}G",
                        "used": f"{used_gb:.0f}G",
                        "avail": f"{free_gb:.0f}G",
                        "use_pct": f"{pct}%",
                        "mount": mount_path,
                        "filesystem": "local",
                    }
                )
            except OSError:
                pass

    return web.json_response({"shares": results, "source": source})


async def api_hermes_skills_handler(request: web.Request) -> web.Response:
    """GET /api/hermes/skills — list custom Hermes skills from ~/.hermes/skills/."""
    skills_dir = Path("/Users/davevoyles/.hermes/skills")
    if not skills_dir.exists():
        return web.json_response({"skills": []})

    skills = []
    for item in sorted(skills_dir.iterdir()):
        if not item.is_dir():
            continue
        description = ""
        for readme_name in ["README.md", "readme.md", "SKILL.md", f"{item.name}.md"]:
            readme = item / readme_name
            if readme.exists():
                try:
                    lines = readme.read_text(encoding="utf-8", errors="replace").splitlines()
                    for ln in lines:
                        ln = ln.strip()
                        if ln and not ln.startswith("#"):
                            description = ln[:120]
                            break
                        if ln.startswith("#"):
                            description = ln.lstrip("#").strip()[:80]
                            break
                except Exception:
                    pass
                break

        file_count = sum(1 for child in item.rglob("*") if child.is_file())
        skills.append(
            {
                "name": item.name,
                "description": description,
                "file_count": file_count,
                "path": str(item),
            }
        )

    return web.json_response({"skills": skills, "count": len(skills)})


async def api_docker_status_handler(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    import os

    cached = _cache_get("docker_status")
    if cached is not None:
        return web.Response(content_type="application/json", text=json.dumps(cached))

    bridge_enabled = os.environ.get("OPENCLAW_HOST_BRIDGE_ENABLED", "false").lower() == "true"
    containers: list[dict[str, str]] = []
    error = None

    if bridge_enabled:
        try:
            from host_bridge import run_shell

            cmd = 'docker ps -a --format \'{"name":"{{.Names}}","status":"{{.Status}}","state":"{{.State}}","image":"{{.Image}}","ports":"{{.Ports}}"}\''
            bridge_result = await run_shell(command=cmd, slack_user_id="dashboard", timeout_s=10)
            output = bridge_result if isinstance(bridge_result, str) else (getattr(bridge_result, "stdout", "") or "")
            bridge_error = "" if isinstance(bridge_result, str) else getattr(bridge_result, "error", "")
            if bridge_error:
                error = bridge_error
                log.warning("api_docker_status_handler error: %s", bridge_error)
            for line in output.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    c = json.loads(line)
                    state = str(c.get("state", "")).lower()
                    if state == "running":
                        badge = "up"
                    elif state == "exited":
                        badge = "down"
                    elif state in ("paused", "restarting"):
                        badge = state
                    else:
                        badge = state or "unknown"
                    containers.append(
                        {
                            "name": str(c.get("name", "")),
                            "status": str(c.get("status", "")),
                            "badge": badge,
                            "image": str(c.get("image", "")).split(":")[0].split("/")[-1],
                        }
                    )
                except Exception as e:
                    log.warning("api_docker_status_handler error: %s", e)
        except Exception as exc:
            error = str(exc)
            log.warning("api_docker_status_handler error: %s", exc)

    if not containers and not bridge_enabled:
        error = "Host bridge disabled — set OPENCLAW_HOST_BRIDGE_ENABLED=true"

    result = {
        "containers": containers,
        "count": len(containers),
        "running": sum(1 for c in containers if c["badge"] == "up"),
        "error": error,
    }
    _cache_set("docker_status", result, 30)
    return web.Response(content_type="application/json", text=json.dumps(result))


async def api_network_wol_handler(request: web.Request) -> web.Response:
    """Send Wake-on-LAN magic packet to a named machine or default MAC.

    Supports two machines via env vars:
      WOL_MACBOOK_PRO_MAC   — hardware MAC of MacBook Pro (192.168.1.131)
      WOL_MACBOOK_PRO2_MAC  — hardware MAC of MacBook Pro 2 (192.168.1.136)
      WOL_BROADCAST_IP      — broadcast address (default 192.168.1.255)

    POST body (JSON): {"machine": "mbp"} or {"machine": "mbp2"} or {} for first available.
    """
    import socket
    import os

    broadcast_ip = os.environ.get("WOL_BROADCAST_IP", "192.168.1.255")

    # Build machine registry from env
    machines = {}
    if os.environ.get("WOL_MACBOOK_PRO_MAC"):
        machines["mbp"] = {"label": "MacBook Pro", "mac": os.environ["WOL_MACBOOK_PRO_MAC"], "ip": "192.168.1.131"}
    if os.environ.get("WOL_MACBOOK_PRO2_MAC"):
        machines["mbp2"] = {"label": "MacBook Pro 2", "mac": os.environ["WOL_MACBOOK_PRO2_MAC"], "ip": "192.168.1.136"}
    # Legacy single-machine support
    if not machines and os.environ.get("WOL_MACBOOK_MAC"):
        machines["default"] = {"label": "MacBook", "mac": os.environ["WOL_MACBOOK_MAC"], "ip": None}

    if not machines:
        return web.json_response(
            {"error": "No WoL MAC configured. Set WOL_MACBOOK_PRO_MAC and/or WOL_MACBOOK_PRO2_MAC in .env", "sent": False},
            status=400,
        )

    # GET: return available machines list
    if request.method == "GET":
        return web.json_response({"machines": {k: {"label": v["label"], "ip": v["ip"]} for k, v in machines.items()}})

    # POST: send magic packet
    try:
        body = await request.json()
    except Exception:
        body = {}
    target_key = body.get("machine", "") or next(iter(machines))
    if target_key not in machines:
        return web.json_response({"error": f"Unknown machine '{target_key}'. Valid: {list(machines)}", "sent": False}, status=400)

    machine = machines[target_key]
    mac = machine["mac"]
    mac_clean = re.sub(r"[:\-]", "", mac).upper()
    if len(mac_clean) != 12:
        return web.json_response({"error": f"Invalid MAC address format: {mac}", "sent": False}, status=400)
    try:
        magic = bytes.fromhex("FF" * 6 + mac_clean * 16)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # Send to subnet broadcast AND global broadcast for reliability
            sock.sendto(magic, (broadcast_ip, 9))
            sock.sendto(magic, ("255.255.255.255", 9))
        return web.json_response({
            "sent": True,
            "mac": mac,
            "machine": machine["label"],
            "broadcast": broadcast_ip,
            "message": f"Magic packet sent to {machine['label']} ({mac})",
        })
    except Exception as exc:
        return web.json_response({"error": str(exc), "sent": False}, status=500)



async def api_hermes_upgrade_handler(request: web.Request) -> web.Response:
    from host_bridge import HERMES_BIN, _enabled as _host_bridge_enabled, run_shell

    if not _host_bridge_enabled():
        return web.json_response({"error": "Host bridge disabled", "updated": False})

    old_ver = ""
    try:
        ver_result = await run_shell(command=f"{HERMES_BIN} --version", slack_user_id="dashboard", timeout_s=10)
        old_stdout = ver_result if isinstance(ver_result, str) else getattr(ver_result, "stdout", "")
        old_ver = old_stdout.strip().splitlines()[0] if old_stdout else ""
    except Exception:
        pass

    output = ""
    error = ""
    try:
        upg_result = await run_shell(
            command=f"{HERMES_BIN} --version && uv tool upgrade hermes-agent",
            slack_user_id="dashboard",
            timeout_s=120,
        )
        output = (upg_result if isinstance(upg_result, str) else getattr(upg_result, "stdout", "") or "").strip()
        err_out = "" if isinstance(upg_result, str) else getattr(upg_result, "error", "") or getattr(upg_result, "stderr", "")
        if err_out:
            error = str(err_out)
    except Exception as exc:
        error = str(exc)

    new_ver = ""
    try:
        ver2_result = await run_shell(command=f"{HERMES_BIN} --version", slack_user_id="dashboard", timeout_s=10)
        new_stdout = ver2_result if isinstance(ver2_result, str) else getattr(ver2_result, "stdout", "")
        new_ver = new_stdout.strip().splitlines()[0] if new_stdout else ""
    except Exception:
        pass

    updated = bool(new_ver and old_ver and new_ver != old_ver)
    return web.json_response(
        {"old_version": old_ver, "new_version": new_ver, "updated": updated, "output": output, "error": error}
    )


async def api_nas_browse_handler(request: web.Request) -> web.Response:
    """Browse NAS directory via host bridge."""
    import os
    import re
    import shlex

    from host_bridge import _enabled as _host_bridge_enabled, run_shell

    if not _host_bridge_enabled():
        return web.json_response({"error": "Host bridge disabled", "entries": []})

    raw_path = request.query.get("path", "/Volumes/Misc")
    path = os.path.normpath(raw_path)
    if not re.match(r"^(/Volumes(?:/|$)|/Users/davevoyles/mnt(?:/|$))", path):
        return web.json_response({"error": "Path not allowed", "entries": []}, status=400)

    entries: list[dict[str, str | bool]] = []
    error = ""
    try:
        cmd = f"ls -la --time-style=long-iso {shlex.quote(path)} 2>&1 || ls -la {shlex.quote(path)} 2>&1"
        result = await run_shell(command=cmd, slack_user_id="dashboard", timeout_s=15)
        output = result if isinstance(result, str) else getattr(result, "stdout", "")
        for line in (output or "").strip().splitlines():
            if line.startswith("total"):
                continue
            parts = line.split(None, 8)
            if len(parts) < 8:
                continue
            perms = parts[0]
            size = parts[4] if len(parts) > 4 else ""
            name = parts[8] if len(parts) >= 9 else parts[7]
            if name in (".", ".."):
                continue
            entries.append(
                {
                    "name": name,
                    "is_dir": perms.startswith("d"),
                    "size": "" if perms.startswith("d") else size,
                    "perms": perms,
                }
            )
    except Exception as exc:
        error = str(exc)

    return web.json_response({"path": path, "entries": entries, "error": error})



def _check_auth(request: web.Request) -> bool:
    dashboard_token = _os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    dashboard_auth_required = _os.environ.get("DASHBOARD_API_AUTH_REQUIRED", "true").lower() == "true"
    if dashboard_token and dashboard_auth_required:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer ") and auth[7:].strip() == dashboard_token:
            return True
        header_token = request.headers.get("X-OpenClaw-Token", "").strip()
        query_token = request.rel_url.query.get("api_key", "").strip()
        return header_token == dashboard_token or query_token == dashboard_token
    return _v1_auth_ok(request)


async def api_network_ping_handler(request):
    import asyncio
    import socket

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    machines = {
        "mbp": {"label": "MacBook Pro", "ip": "192.168.1.131"},
        "mbp2": {"label": "MacBook Pro 2", "ip": "192.168.1.136"},
    }

    async def _tcp_reachable(ip: str, port: int = 22, timeout: float = 2.0) -> bool:
        """Check if a host is reachable by attempting a TCP connection (SSH port)."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    results = {}
    checks = {key: _tcp_reachable(info["ip"]) for key, info in machines.items()}
    for key, coro in checks.items():
        online = await coro
        results[key] = {"label": machines[key]["label"], "ip": machines[key]["ip"], "online": online}

    return web.Response(
        content_type="application/json",
        text=json.dumps({"machines": results, "timestamp": time.time()}),
    )


async def api_hermes_memory_get_handler(request):
    import os

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    file_param = (request.rel_url.query.get("file", "memory") or "memory").strip().lower()
    hermes_base = "/Users/davevoyles/.hermes"
    path = f"{hermes_base}/SOUL.md" if file_param == "soul" else f"{hermes_base}/memories/MEMORY.md"
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        return web.Response(
            content_type="application/json",
            text=json.dumps({
                "content": content,
                "path": path,
                "file": file_param,
                "saved_at": os.path.getmtime(path),
            }),
        )
    except Exception as e:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"error": str(e), "content": "", "path": path, "file": file_param}),
        )


async def api_hermes_memory_post_handler(request):
    import os

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    file_param = (request.rel_url.query.get("file", "memory") or "memory").strip().lower()
    hermes_base = "/Users/davevoyles/.hermes"
    path = f"{hermes_base}/SOUL.md" if file_param == "soul" else f"{hermes_base}/memories/MEMORY.md"
    try:
        data = await request.json()
        content = data.get("content", "")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return web.Response(
            content_type="application/json",
            text=json.dumps({"ok": True, "path": path, "file": file_param, "saved_at": time.time()}),
        )
    except Exception as e:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"ok": False, "error": str(e), "path": path, "file": file_param}),
        )


def _format_uptime_human(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


async def api_system_health_handler(request):
    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    import os
    import shutil
    import time

    loop = asyncio.get_event_loop()

    def _read_proc_files():
        proc_data = {}
        try:
            with open("/proc/loadavg", encoding="utf-8") as f:
                proc_data["loadavg"] = f.read().strip()
        except Exception as e:
            proc_data["loadavg_error"] = str(e)
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                proc_data["meminfo"] = f.read().strip()
        except Exception as e:
            proc_data["meminfo_error"] = str(e)
        try:
            with open("/proc/uptime", encoding="utf-8") as f:
                proc_data["uptime"] = f.read().strip()
        except Exception as e:
            proc_data["uptime_error"] = str(e)
        return proc_data

    proc_data = await loop.run_in_executor(None, _read_proc_files)
    result = {}

    try:
        loadavg = proc_data["loadavg"]
        load_parts = loadavg.split()
        result["loadavg_raw"] = loadavg
        result["loadavg"] = {
            "1m": float(load_parts[0]) if len(load_parts) > 0 else 0.0,
            "5m": float(load_parts[1]) if len(load_parts) > 1 else 0.0,
            "15m": float(load_parts[2]) if len(load_parts) > 2 else 0.0,
        }
    except Exception as e:
        result["loadavg_raw"] = f"error: {proc_data.get('loadavg_error', e)}"
        result["loadavg"] = {"1m": 0.0, "5m": 0.0, "15m": 0.0}

    try:
        meminfo = {}
        for line in proc_data["meminfo"].splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meminfo[key] = value.strip()
        mem_total_kb = int(meminfo.get("MemTotal", "0 kB").split()[0])
        mem_avail_kb = int(meminfo.get("MemAvailable", "0 kB").split()[0])
        mem_used_kb = max(0, mem_total_kb - mem_avail_kb)
        result["memory"] = {
            "total_kb": mem_total_kb,
            "available_kb": mem_avail_kb,
            "used_kb": mem_used_kb,
            "total_gb": round(mem_total_kb / 1024 / 1024, 2),
            "available_gb": round(mem_avail_kb / 1024 / 1024, 2),
            "used_gb": round(mem_used_kb / 1024 / 1024, 2),
        }
    except Exception as e:
        result["memory"] = {"error": str(proc_data.get("meminfo_error", e)), "total_gb": 0.0, "available_gb": 0.0, "used_gb": 0.0}

    try:
        disk = shutil.disk_usage("/")
        result["disk"] = {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "total_gb": round(disk.total / 1024 / 1024 / 1024, 2),
            "used_gb": round(disk.used / 1024 / 1024 / 1024, 2),
            "free_gb": round(disk.free / 1024 / 1024 / 1024, 2),
        }
    except Exception as e:
        result["disk"] = {"error": str(e), "total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0}

    try:
        uptime_secs = float(proc_data["uptime"].split()[0])
        result["uptime"] = {
            "seconds": uptime_secs,
            "human": _format_uptime_human(uptime_secs),
        }
    except Exception as e:
        result["uptime"] = {"error": str(proc_data.get("uptime_error", e)), "seconds": 0.0, "human": "Unavailable"}

    result["hostname"] = os.uname().nodename
    result["timestamp"] = time.time()
    return web.Response(content_type="application/json", text=json.dumps(result))


async def api_system_alerts_handler(request: web.Request) -> web.Response:
    import os
    import shutil
    import time

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    alerts: list[dict[str, str]] = []

    try:
        disk = shutil.disk_usage("/")
        pct = disk.used / disk.total * 100
        if pct > 90:
            alerts.append({"severity": "error", "msg": f"/ disk {pct:.0f}% full ({disk.free / 1e9:.0f}GB free)", "icon": "🔴"})
        elif pct > 80:
            alerts.append({"severity": "warn", "msg": f"/ disk {pct:.0f}% used — watch this", "icon": "🟡"})
    except Exception as e:
        alerts.append({"severity": "info", "msg": f"Disk check failed: {e}", "icon": "ℹ️"})

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "ps",
            "--filter",
            "health=unhealthy",
            "--format",
            "{{.Names}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        unhealthy = [line for line in out.decode().splitlines() if line.strip()]
        for name in unhealthy:
            alerts.append({"severity": "error", "msg": f"Container unhealthy: {name}", "icon": "🔴"})
    except Exception:
        pass

    if not os.environ.get("WOL_MACBOOK_PRO_MAC"):
        alerts.append({"severity": "info", "msg": "WoL: MacBook Pro 1 MAC not set — enable SSH on MBP to configure", "icon": "ℹ️"})

    try:
        nas_path = os.environ.get("NAS_BACKUP_PATH", "/Volumes/Misc")
        if os.path.exists(nas_path):
            nas_disk = shutil.disk_usage(nas_path)
            nas_pct = nas_disk.used / nas_disk.total * 100
            if nas_pct > 90:
                alerts.append({"severity": "error", "msg": f"NAS disk {nas_pct:.0f}% full", "icon": "🔴"})
            elif nas_pct > 80:
                alerts.append({"severity": "warn", "msg": f"NAS disk {nas_pct:.0f}% used", "icon": "🟡"})
    except Exception:
        pass

    ok = len(alerts) == 0 or all(alert["severity"] == "info" for alert in alerts)
    return web.Response(
        content_type="application/json",
        text=json.dumps({"alerts": alerts, "ok": ok, "timestamp": time.time()}),
    )



async def api_tautulli_activity_handler(request):
    import os, aiohttp

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")
    cached = _cache_get("tautulli_activity")
    if cached is not None:
        return web.Response(content_type="application/json", text=json.dumps(cached))
    url = os.environ.get("TAUTULLI_URL", "http://localhost:8181")
    key = os.environ.get("TAUTULLI_API_KEY", "")
    if not key:
        return web.Response(content_type="application/json", text=json.dumps({"error": "TAUTULLI_API_KEY not set"}))
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{url}/api/v2",
                params={"apikey": key, "cmd": "get_activity"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                data = await r.json()
        activity = data.get("response", {}).get("data", {})
        _cache_set("tautulli_activity", activity, 30)
        return web.Response(
            content_type="application/json",
            text=json.dumps(activity),
        )
    except Exception as e:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"error": str(e), "sessions": [], "stream_count": "0"}),
        )


async def api_tautulli_history_handler(request):
    import os, aiohttp

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")
    url = os.environ.get("TAUTULLI_URL", "http://localhost:8181")
    key = os.environ.get("TAUTULLI_API_KEY", "")
    if not key:
        return web.Response(content_type="application/json", text=json.dumps({"error": "TAUTULLI_API_KEY not set", "data": []}))
    length = request.rel_url.query.get("length", "10")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{url}/api/v2",
                params={"apikey": key, "cmd": "get_history", "length": length},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                data = await r.json()
        hist = data.get("response", {}).get("data", {})
        return web.Response(content_type="application/json", text=json.dumps(hist))
    except Exception as e:
        return web.Response(content_type="application/json", text=json.dumps({"error": str(e), "data": []}))


async def api_arr_queue_handler(request):
    import os, aiohttp

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    sonarr_url = os.environ.get("SONARR_URL", "http://localhost:8989")
    sonarr_key = os.environ.get("SONARR_API_KEY", "")
    radarr_url = os.environ.get("RADARR_URL", "http://localhost:7878")
    radarr_key = os.environ.get("RADARR_API_KEY", "")
    lidarr_url = os.environ.get("LIDARR_URL", "http://host.docker.internal:8686")
    lidarr_key = os.environ.get("LIDARR_API_KEY", "")

    result = {
        "sonarr": [],
        "radarr": [],
        "lidarr": [],
        "sonarr_total": 0,
        "radarr_total": 0,
        "lidarr_total": 0,
        "radarr_missing": 0,
    }

    def _pct(item):
        size = item.get("size") or 0
        return round(100 * (1 - item.get("sizeleft", 0) / max(size, 1))) if size else 0

    async def _fetch_json(session, url, *, params=None):
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as response:
            return await response.json()

    async with aiohttp.ClientSession() as s:
        tasks = {}
        if sonarr_key:
            tasks["sonarr_queue"] = asyncio.create_task(
                _fetch_json(s, f"{sonarr_url}/api/v3/queue", params={"apikey": sonarr_key, "pageSize": 10})
            )
        if radarr_key:
            tasks["radarr_queue"] = asyncio.create_task(
                _fetch_json(s, f"{radarr_url}/api/v3/queue", params={"apikey": radarr_key, "pageSize": 10})
            )
            tasks["radarr_missing"] = asyncio.create_task(
                _fetch_json(s, f"{radarr_url}/api/v3/wanted/missing", params={"apikey": radarr_key, "pageSize": 1})
            )
        if lidarr_key:
            tasks["lidarr_queue"] = asyncio.create_task(
                _fetch_json(
                    s,
                    f"{lidarr_url}/api/v1/queue?page=1&pageSize=20&apikey={lidarr_key}",
                )
            )

        task_results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    for name, payload in zip(tasks.keys(), task_results):
        if isinstance(payload, Exception):
            result[f"{name}_error"] = str(payload)
            continue
        if name == "sonarr_queue":
            result["sonarr_total"] = payload.get("totalRecords", 0)
            result["sonarr"] = [
                {
                    "title": item.get("title", "?"),
                    "status": item.get("status", "?"),
                    "pct": _pct(item),
                    "type": "tv",
                }
                for item in payload.get("records", [])
            ]
        elif name == "radarr_queue":
            result["radarr_total"] = payload.get("totalRecords", 0)
            result["radarr"] = [
                {
                    "title": item.get("title", "?"),
                    "status": item.get("status", "?"),
                    "pct": _pct(item),
                    "type": "movie",
                }
                for item in payload.get("records", [])
            ]
        elif name == "radarr_missing":
            result["radarr_missing"] = payload.get("totalRecords", 0)
        elif name == "lidarr_queue":
            result["lidarr_total"] = payload.get("totalRecords", 0)
            result["lidarr"] = [
                {
                    "title": item.get("title") or (item.get("album") or {}).get("title") or "?",
                    "artistName": (item.get("artist") or {}).get("artistName") or item.get("artistName", ""),
                    "status": item.get("status", "?"),
                    "pct": _pct(item),
                    "protocol": item.get("protocol", "?"),
                    "type": "music",
                }
                for item in payload.get("records", [])
            ]

    return web.Response(content_type="application/json", text=json.dumps(result))


async def api_arr_history_handler(request):
    import os, aiohttp

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")
    sonarr_url = os.environ.get("SONARR_URL", "http://localhost:8989")
    sonarr_key = os.environ.get("SONARR_API_KEY", "")
    radarr_url = os.environ.get("RADARR_URL", "http://localhost:7878")
    radarr_key = os.environ.get("RADARR_API_KEY", "")
    grabs = []
    async with aiohttp.ClientSession() as s:
        for base_url, key, kind in [(sonarr_url, sonarr_key, "tv"), (radarr_url, radarr_key, "movie")]:
            if not key:
                continue
            try:
                async with s.get(
                    f"{base_url}/api/v3/history",
                    params={"apikey": key, "pageSize": 5, "eventType": 1},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    d = await r.json()
                    for item in d.get("records", []):
                        grabs.append(
                            {
                                "title": item.get("sourceTitle", "?"),
                                "date": item.get("date", ""),
                                "quality": item.get("quality", {}).get("quality", {}).get("name", "?"),
                                "kind": kind,
                            }
                        )
            except Exception:
                pass
    grabs.sort(key=lambda x: x.get("date", ""), reverse=True)
    return web.Response(content_type="application/json", text=json.dumps({"grabs": grabs[:10]}))


async def api_hermes_session_messages_handler(request: web.Request) -> web.Response:
    import os
    import sqlite3 as _sqlite3

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    session_id = request.match_info.get("session_id", "")
    if not session_id:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"error": "session_id required"}),
            status=400,
        )

    db_path = "/Users/davevoyles/.hermes/state.db"
    if not os.path.exists(db_path):
        return web.Response(
            content_type="application/json",
            text=json.dumps({"error": "Hermes state DB not found"}),
            status=404,
        )

    try:
        loop = asyncio.get_event_loop()

        def _read_session_messages():
            conn = _sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
            conn.row_factory = _sqlite3.Row
            try:
                session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
                if not session_row:
                    return None, None

                message_rows = conn.execute(
                    "SELECT id, session_id, role, content, timestamp, active, tool_name, finish_reason "
                    "FROM messages WHERE session_id=? AND active=1 ORDER BY timestamp ASC",
                    (session_id,),
                ).fetchall()
                return dict(session_row), [dict(row) for row in message_rows]
            finally:
                conn.close()

        session_row, message_rows = await loop.run_in_executor(None, _read_session_messages)
        if session_row is None:
            return web.Response(
                content_type="application/json",
                text=json.dumps({"error": "Session not found"}),
                status=404,
            )

        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {
                    "session": session_row,
                    "messages": message_rows,
                }
            ),
        )
    except Exception as e:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"error": str(e)}),
            status=500,
        )


async def api_tailscale_status_handler(request):
    """GET /api/tailscale/status — Tailscale device list."""
    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    import asyncio
    import json as _json
    import os

    async def _get_tailscale_json() -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "tailscale",
                "status",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                return out.decode()
        except (FileNotFoundError, asyncio.TimeoutError):
            pass
        except Exception:
            pass

        if os.environ.get("OPENCLAW_HOST_BRIDGE_ENABLED", "false").lower() != "true":
            return None

        try:
            from host_bridge import run_shell

            result = await run_shell(command="tailscale status --json", slack_user_id="dashboard", timeout_s=10)
            if isinstance(result, str):
                return result
            return getattr(result, "stdout", "") or ""
        except Exception:
            return None

    raw = await _get_tailscale_json()
    if not raw:
        return web.Response(
            content_type="application/json",
            text=_json.dumps({"error": "tailscale not available", "devices": []}),
        )

    try:
        data = _json.loads(raw)
    except Exception as e:
        return web.Response(
            content_type="application/json",
            text=_json.dumps({"error": f"parse error: {e}", "devices": []}),
        )

    self_node = data.get("Self", {}) or {}
    devices: list[dict[str, object]] = []
    if self_node:
        devices.append(
            {
                "name": self_node.get("HostName", "Mac Mini"),
                "ip": (self_node.get("TailscaleIPs") or ["?"])[0],
                "online": True,
                "os": self_node.get("OS", "macOS"),
                "self": True,
                "lastSeen": None,
            }
        )

    for peer in (data.get("Peer", {}) or {}).values():
        devices.append(
            {
                "name": peer.get("HostName", "?"),
                "ip": (peer.get("TailscaleIPs") or ["?"])[0],
                "online": peer.get("Online", False),
                "os": peer.get("OS", "?"),
                "self": False,
                "lastSeen": peer.get("LastSeen", ""),
                "relay": peer.get("Relay", ""),
            }
        )

    devices.sort(key=lambda d: (not bool(d.get("online")), str(d.get("name", "")).lower()))
    return web.Response(
        content_type="application/json",
        text=_json.dumps({"devices": devices, "count": len(devices)}),
    )


async def api_system_timemachine_handler(request):
    """GET /api/system/timemachine — Time Machine status via host bridge."""
    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    import json as _json
    import os
    import plistlib
    import re

    tm_status: dict[str, object] = {
        "last_backup": None,
        "running": False,
        "percent": -1,
        "error": None,
    }

    if os.environ.get("OPENCLAW_HOST_BRIDGE_ENABLED", "false").lower() != "true":
        tm_status["error"] = "host bridge disabled"
        return web.Response(content_type="application/json", text=_json.dumps(tm_status))

    try:
        from host_bridge import run_shell
    except ImportError as exc:
        tm_status["error"] = f"host_bridge unavailable: {exc}"
        return web.Response(content_type="application/json", text=_json.dumps(tm_status), status=500)

    try:
        latest_result = await run_shell(command="tmutil latestbackup 2>/dev/null || true", slack_user_id="dashboard", timeout_s=10)
        latest_output = latest_result if isinstance(latest_result, str) else (getattr(latest_result, "stdout", "") or "")
        match = re.search(r"(\d{4}-\d{2}-\d{2}-\d{6})", latest_output)
        if match:
            tm_status["last_backup"] = match.group(1)[:10]

        status_result = await run_shell(command="tmutil status 2>/dev/null || true", slack_user_id="dashboard", timeout_s=10)
        status_output = status_result if isinstance(status_result, str) else (getattr(status_result, "stdout", "") or "")
        if status_output.strip():
            xml_start = status_output.find("<?xml")
            if xml_start == -1:
                xml_start = status_output.find("<plist")
            if xml_start >= 0:
                status_output = status_output[xml_start:]
            try:
                parsed = plistlib.loads(status_output.encode("utf-8"))
                tm_status["running"] = bool(parsed.get("Running", False))
                tm_status["percent"] = parsed.get("Percent", -1)
            except Exception:
                import re as _re

                running_match = _re.search(r"<key>Running</key>\s*<integer>(\d+)</integer>", status_output)
                if running_match:
                    tm_status["running"] = running_match.group(1) == "1"
                percent_match = _re.search(r"<key>Percent</key>\s*<real>([0-9.]+)</real>", status_output)
                if percent_match:
                    tm_status["percent"] = float(percent_match.group(1))
    except Exception as exc:
        tm_status["error"] = str(exc)

    return web.Response(content_type="application/json", text=_json.dumps(tm_status))


async def api_overseerr_recent_handler(request):
    """GET /api/overseerr/recent — recent media requests"""
    import os

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")
    url = os.environ.get("OVERSEERR_URL", "http://localhost:5055")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{url}/api/v1/request",
                params={"take": 10, "sort": "added", "filter": "all"},
                headers=_overseerr_headers(),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                data = await r.json()
        results = data.get("results", [])
        items = []
        for req in results:
            media = req.get("media", {})
            items.append(
                {
                    "id": req.get("id"),
                    "type": media.get("mediaType", "?"),
                    "status": req.get("status", 0),
                    "title": media.get("originalTitle") or media.get("title", "?"),
                    "year": media.get("firstAirDate", "")[:4] or str(media.get("releaseDate", ""))[:4],
                    "requestedBy": req.get("requestedBy", {}).get("displayName", "?"),
                    "createdAt": req.get("createdAt", ""),
                }
            )
        return web.Response(content_type="application/json", text=json.dumps({"requests": items}))
    except Exception as e:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"error": str(e), "requests": []}),
        )


async def api_overseerr_search_handler(request):
    """GET /api/overseerr/search?q=<query> — search for media"""
    import os

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")
    query = request.rel_url.query.get("q", "").strip()
    if not query:
        return web.Response(content_type="application/json", text=json.dumps({"results": []}))
    url = os.environ.get("OVERSEERR_URL", "http://localhost:5055")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{url}/api/v1/search",
                params={"query": query, "page": 1, "language": "en"},
                headers=_overseerr_headers(),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                data = await r.json()
        results = data.get("results", [])[:8]
        items = [
            {
                "id": item.get("id"),
                "mediaType": item.get("mediaType", "?"),
                "title": item.get("originalTitle") or item.get("originalName") or item.get("name", "?"),
                "year": str(item.get("releaseDate", "") or item.get("firstAirDate", ""))[:4],
                "overview": (item.get("overview", "") or "")[:150],
                "mediaInfo": item.get("mediaInfo"),
            }
            for item in results
        ]
        return web.Response(content_type="application/json", text=json.dumps({"results": items}))
    except Exception as e:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"error": str(e), "results": []}),
        )


async def api_overseerr_request_handler(request):
    """POST /api/overseerr/request — create a media request"""
    import os

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")
    try:
        body = await request.json()
    except Exception:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"error": "Invalid JSON"}),
            status=400,
        )
    media_id = body.get("mediaId")
    media_type = body.get("mediaType", "movie")
    if not media_id:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"error": "mediaId required"}),
            status=400,
        )
    url = os.environ.get("OVERSEERR_URL", "http://localhost:5055")
    payload = {"mediaType": media_type, "mediaId": media_id}
    if media_type == "tv":
        payload["seasons"] = "all"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{url}/api/v1/request",
                json=payload,
                headers=_overseerr_headers(),
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                status = r.status
                data = await r.json()
        if status in (200, 201):
            return web.Response(
                content_type="application/json",
                text=json.dumps({"success": True, "request": data}),
            )
        return web.Response(
            content_type="application/json",
            text=json.dumps({"success": False, "error": data.get("message", "Request failed")}),
            status=status,
        )
    except Exception as e:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"success": False, "error": str(e)}),
            status=500,
        )


async def api_sonarr_calendar_handler(request):
    """GET /api/sonarr/calendar — upcoming episodes (next 14 days)"""
    import os
    from datetime import datetime, timedelta

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")
    url = os.environ.get("SONARR_URL", "http://localhost:8989")
    key = os.environ.get("SONARR_API_KEY", "")
    days = int(request.rel_url.query.get("days", "14"))
    start = datetime.utcnow().strftime("%Y-%m-%d")
    end = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{url}/api/v3/calendar",
                params={"apikey": key, "start": start, "end": end, "includeSeries": "true"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                episodes = await r.json()
        result = []
        for ep in episodes:
            result.append(
                {
                    "seriesTitle": ep.get("series", {}).get("title", ep.get("title", "?")),
                    "season": ep.get("seasonNumber", 0),
                    "episode": ep.get("episodeNumber", 0),
                    "title": ep.get("title", ""),
                    "airDate": ep.get("airDateUtc", "")[:10],
                    "hasFile": ep.get("hasFile", False),
                    "id": ep.get("id"),
                }
            )
        result.sort(key=lambda x: x["airDate"])
        return web.Response(
            content_type="application/json",
            text=json.dumps({"episodes": result, "count": len(result)}),
        )
    except Exception as e:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"error": str(e), "episodes": []}),
        )


async def api_webhook_sonarr_handler(request):
    """POST /api/webhooks/sonarr — Sonarr download completion webhook"""
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON")

    event_type = body.get("eventType", "")
    if event_type == "Download":
        series = body.get("series", {})
        episodes = body.get("episodes", [])
        series_title = series.get("title", "Unknown")
        if episodes:
            ep = episodes[0]
            msg = (
                f"📺 Downloaded: {series_title} "
                f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d} — {ep.get('title', '')}"
            )
        else:
            msg = f"📺 Downloaded: {series_title}"
        asyncio.create_task(_ntfy_push("📺 Download Complete", msg))
        log.info("Sonarr webhook: %s", msg)
    elif event_type == "Test":
        log.info("Sonarr webhook test received")

    return web.Response(status=200, text="OK")


async def api_webhook_radarr_handler(request):
    """POST /api/webhooks/radarr — Radarr download completion webhook"""
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON")

    event_type = body.get("eventType", "")
    if event_type == "Download":
        movie = body.get("movie", {})
        msg = f"🎬 Downloaded: {movie.get('title', '?')} ({movie.get('year', '')})"
        asyncio.create_task(_ntfy_push("🎬 Download Complete", msg))
        log.info("Radarr webhook: %s", msg)
    elif event_type == "Test":
        log.info("Radarr webhook test received")

    return web.Response(status=200, text="OK")


async def api_uptime_kuma_handler(request):
    """GET /api/uptime/status — Uptime Kuma status page."""
    import os

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    base = os.environ.get("UPTIME_KUMA_URL", "http://host.docker.internal:3001")
    slug = os.environ.get("UPTIME_KUMA_STATUS_SLUG", "main")
    try:
        async with aiohttp.ClientSession() as session:
            # Fetch monitor list + heartbeats in parallel
            page_resp, hb_resp = await asyncio.gather(
                session.get(f"{base}/api/status-page/{slug}", timeout=aiohttp.ClientTimeout(total=5)),
                session.get(f"{base}/api/status-page/heartbeat/{slug}", timeout=aiohttp.ClientTimeout(total=5)),
            )
            page_data = await page_resp.json()
            hb_data = await hb_resp.json()

        # heartbeatList: {monitor_id: [{status: 1|0, time, msg, ping}, ...]}
        # uptimeList: {"{id}_24": float, "{id}_720": float}
        heartbeats: dict = hb_data.get("heartbeatList", {})
        uptime_list: dict = hb_data.get("uptimeList", {})

        # Build id→name+group map from status page
        id_meta: dict = {}
        for group in page_data.get("publicGroupList", []):
            for monitor in group.get("monitorList", []):
                id_meta[str(monitor["id"])] = {
                    "name": monitor.get("name", "?"),
                    "group": group.get("name", ""),
                }

        services: list[dict] = []
        for monitor_id, beats in heartbeats.items():
            meta = id_meta.get(str(monitor_id), {"name": f"Monitor {monitor_id}", "group": ""})
            last_beat = beats[-1] if beats else {}
            up = last_beat.get("status", 0) == 1
            uptime_pct = uptime_list.get(f"{monitor_id}_24", 0)
            services.append(
                {
                    "name": meta["name"],
                    "group": meta["group"],
                    "up": up,
                    "uptime": round(float(uptime_pct) * 100, 1),  # convert 0-1 → percent
                    "ping": last_beat.get("ping"),
                    "last_check": last_beat.get("time", ""),
                }
            )

        # Sort: down first, then alphabetical within each group
        services.sort(key=lambda s: (s["up"], s["name"]))

        all_up = all(s["up"] for s in services) if services else True
        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {
                    "services": services,
                    "all_up": all_up,
                    "total": len(services),
                    "down": sum(1 for s in services if not s["up"]),
                }
            ),
        )
    except Exception as exc:
        return web.Response(
            content_type="application/json",
            text=json.dumps({"error": str(exc), "services": []}),
        )


async def api_sabnzbd_queue_handler(request):
    """GET /api/sabnzbd/queue — SABnzbd download queue."""
    import os

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    cached = _cache_get("sabnzbd_queue")
    if cached:
        return web.Response(content_type="application/json", text=json.dumps(cached))

    base = os.environ.get("SABNZBD_URL", "http://host.docker.internal:8775")
    api_key = os.environ.get("SABNZBD_API_KEY", "")
    if not api_key:
        return web.Response(content_type="application/json", text=json.dumps({"error": "SABNZBD_API_KEY not set"}))

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base}/api",
                params={"mode": "queue", "apikey": api_key, "output": "json"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()

        q = data.get("queue", {})
        slots = q.get("slots", [])
        items = []
        for s in slots:
            items.append(
                {
                    "name": s.get("filename", s.get("name", "?")),
                    "progress": float(s.get("percentage", 0)),
                    "size": s.get("size", "?"),
                    "sizeleft": s.get("sizeleft", "?"),
                    "timeleft": s.get("timeleft", "?"),
                    "status": s.get("status", "?"),
                    "category": s.get("cat", ""),
                }
            )

        result = {
            "status": q.get("status", "Unknown"),
            "speed": q.get("kbpersec", "0"),
            "speed_mb": round(float(q.get("kbpersec", 0)) / 1024, 2),
            "queue_size": int(q.get("noofslots_total", 0) or 0),
            "size_left": q.get("sizeleft", "0 B"),
            "eta": q.get("timeleft", ""),
            "slots": items,
        }
        _cache_set("sabnzbd_queue", result, ttl_seconds=20)
        return web.Response(content_type="application/json", text=json.dumps(result))
    except Exception as exc:
        return web.Response(content_type="application/json", text=json.dumps({"error": str(exc)}))


async def api_qbt_status_handler(request):
    """GET /api/qbt/status — qBittorrent active count and speeds."""
    import os

    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    cached = _cache_get("qbt_status")
    if cached:
        return web.Response(content_type="application/json", text=json.dumps(cached))

    qbt_url = os.environ.get("QBIT_URL", "")
    qbt_user = os.environ.get("QBIT_USER", "admin")
    qbt_pass = os.environ.get("QBIT_PASSWORD", "")
    if not qbt_url or not qbt_pass:
        return web.Response(content_type="application/json", text=json.dumps({"error": "QBIT_PASSWORD not set"}))

    try:
        jar = aiohttp.CookieJar()
        async with aiohttp.ClientSession(cookie_jar=jar) as s:
            async with s.post(
                f"{qbt_url}/api/v2/auth/login",
                data={"username": qbt_user, "password": qbt_pass},
                timeout=aiohttp.ClientTimeout(total=6),
            ) as r:
                auth = await r.text()
            if auth.strip() not in ("Ok.", "Ok"):
                raise ValueError(f"auth failed: {auth}")

            async with s.get(f"{qbt_url}/api/v2/transfer/info", timeout=aiohttp.ClientTimeout(total=4)) as r:
                xfer = await r.json()
            async with s.get(
                f"{qbt_url}/api/v2/torrents/info",
                params={"filter": "active"},
                timeout=aiohttp.ClientTimeout(total=4),
            ) as r:
                active = await r.json()

        result = {
            "active_count": len(active),
            "dl_speed_mbps": round(xfer.get("dl_info_speed", 0) / 1024 / 1024, 2),
            "up_speed_mbps": round(xfer.get("up_info_speed", 0) / 1024 / 1024, 2),
            "free_space_gb": round(xfer.get("free_space_on_disk", 0) / 1e9, 1),
        }
        _cache_set("qbt_status", result, ttl_seconds=30)
        return web.Response(content_type="application/json", text=json.dumps(result))
    except Exception as exc:
        return web.Response(content_type="application/json", text=json.dumps({"error": str(exc)}))


async def api_nas_status_handler(request):
    """GET /api/nas/status — SSH to NAS, return disk % and container count."""
    if not _check_auth(request):
        return web.Response(status=401, text="Unauthorized")

    cached = _cache_get("nas_status")
    if cached:
        return web.Response(content_type="application/json", text=json.dumps(cached))

    import asyncio as _asyncio, re as _re
    nas_host = _os.environ.get("NAS_HOST", "192.168.1.8")
    nas_port = _os.environ.get("NAS_SSH_PORT", "24")
    nas_user = _os.environ.get("NAS_SSH_USER", "dave")
    nas_cmd = (
        "df -h /volume1 2>/dev/null | tail -1; echo '---'; "
        "/usr/local/bin/docker ps --format '{{.Status}}' | wc -l; echo '---'; "
        "/usr/local/bin/docker ps --format '{{.Names}}|{{.Status}}' | grep -i unhealthy; "
        "uptime"
    )
    try:
        proc = await _asyncio.create_subprocess_exec(
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=6",
            "-o", "BatchMode=yes", "-p", nas_port, f"{nas_user}@{nas_host}", nas_cmd,
            stdout=_asyncio.subprocess.PIPE, stderr=_asyncio.subprocess.PIPE,
        )
        stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=12)
        raw = stdout.decode().strip()
    except Exception as exc:
        return web.Response(content_type="application/json",
                            text=json.dumps({"error": str(exc)}))

    parts = raw.split("---")
    disk_line = parts[0].strip() if parts else ""
    cont_section = parts[1].strip() if len(parts) > 1 else ""
    tail_section = parts[2].strip() if len(parts) > 2 else ""

    # Parse disk %
    disk_pct = None
    disk_used = None
    disk_total = None
    if disk_line:
        cols = disk_line.split()
        if len(cols) >= 5:
            disk_total = cols[1]
            disk_used = cols[2]
            disk_pct = int(cols[4].replace("%", "")) if cols[4].replace("%", "").isdigit() else None

    # Container count
    container_count = int(cont_section.splitlines()[0].strip()) if cont_section.splitlines() else 0
    unhealthy = [l.split("|")[0] for l in cont_section.splitlines()[1:] if "|" in l]

    # Load average
    load_1m = None
    if tail_section:
        lm = _re.search(r"load average[s]?:?\s*([\d.]+)", tail_section)
        if lm:
            load_1m = float(lm.group(1))

    result = {
        "disk_pct": disk_pct,
        "disk_used": disk_used,
        "disk_total": disk_total,
        "container_count": container_count,
        "unhealthy": unhealthy,
        "load_1m": load_1m,
        "host": nas_host,
    }
    _cache_set("nas_status", result, ttl_seconds=60)
    return web.Response(content_type="application/json", text=json.dumps(result))
