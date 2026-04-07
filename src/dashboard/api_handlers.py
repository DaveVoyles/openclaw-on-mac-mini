"""JSON API endpoint handlers for the dashboard."""

import asyncio
import json
import math
import platform
import re
import time
from collections import defaultdict
from pathlib import Path

import aiohttp
import discord
from aiohttp import web

from http_session import SessionManager as _SessionManager
from spending import get_quota_status, get_response_stats
from spending import tracker as spending_tracker

from .helpers import GITHUB_REPO, VERSION, _command_list, _command_quickstart, _cron_to_human, _load_config, log

_dashboard_sessions = _SessionManager(timeout=10, name="dashboard")

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
    if (
        "source_diversity" in event
        or "single_source" in event
        or "mono_source" in event
        or "one_source" in event
    ):
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
    except Exception as exc:
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
        "risk_level": getattr(getattr(req, "risk_level", None), "value", "UNKNOWN"),
        "requester_name": getattr(req, "requester_name", "unknown"),
        "resolver_name": getattr(req, "resolver_name", None),
        "age_seconds": age_seconds,
        "status": status,
        "created_at": created_at,
        "resolved_at": now_epoch if status in {"approved", "denied", "expired"} else None,
    }


async def api_approvals_handler(request: web.Request) -> web.Response:
    """List pending and recent approval requests for dashboard tables."""
    from approvals import approval_store

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
    from approvals import approval_store

    request_id = str(request.match_info.get("request_id", "")).strip()
    if not request_id:
        return web.json_response({"ok": False, "error": "missing request_id"}, status=400)

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"ok": False, "error": "Invalid JSON payload"}, status=400)

    approved = bool(payload.get("approved"))
    resolver_name = str(payload.get("resolver_name") or request.headers.get("X-OpenClaw-Actor") or "dashboard").strip()[:120]
    resolved = approval_store.resolve(
        request_id=request_id,
        approved=approved,
        resolver_id=0,
        resolver_name=resolver_name or "dashboard",
    )
    if resolved is None:
        return web.json_response({"ok": False, "reason": "Request was not found, expired, or already resolved"}, status=404)

    now_epoch = int(time.time())
    return web.json_response({"ok": True, "request": _serialize_approval(resolved, now_epoch)})


async def api_status_handler(request):
    """Return connectivity status for all backends."""
    from config import TIMEOUT_FAST, cfg

    checks = {}

    # Docker
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "info", "--format", "{{.ContainersRunning}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
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

    # Gemini
    checks["gemini"] = {"status": "ok" if cfg.google_api_key else "no_key"}

    # Search provider
    perplexity_key = cfg.perplexity_api_key
    firecrawl_key = cfg.firecrawl_api_key
    tavily_key = cfg.tavily_api_key
    if perplexity_key:
        cascade = "Perplexity → Firecrawl → Tavily → DDG → Bing Lite" if firecrawl_key else "Perplexity → Tavily → DDG → Bing Lite"
        checks["search_provider"] = {"status": "ok", "active": "Perplexity AI", "cascade": cascade}
    elif firecrawl_key:
        checks["search_provider"] = {"status": "ok", "active": "Firecrawl", "cascade": "Firecrawl → Tavily → DDG → Bing Lite"}
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
    checks["content_extraction"] = {
        "status": "ok",
        "chain": "trafilatura → Jina AI Reader → Playwright",
        "jina_reader": "available",
    }

    # Copilot proxy
    proxy_url = cfg.copilot_proxy_url
    if proxy_url:
        try:
            session = await _dashboard_sessions.get()
            token = cfg.copilot_proxy_token
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            async with session.get(f"{proxy_url}/models", headers=headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT_FAST)) as resp:
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

        # Base status response
        patreon_data = {
            "cookie_age_hours": health.cookie_age_hours if health.cookie_age_hours is not None else -1,
            "hours_since_download": health.hours_since_download if health.hours_since_download is not None else -1,
            "downloaded_count": health.downloaded_count,
            "total_count": health.total_count,
            "pending_count": health.pending_count,
            "auto_recovery_active": health.auto_recovery_active,
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
    except Exception as exc:
        log.debug("Patreon check error: %s", exc)
        checks["patreon"] = {"status": "down"}

    return web.json_response(checks)


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
                "remaining_sends": 5,
            }
        )

    try:
        prefs = await configure_sms_phone(user_id, phone_number)
    except Exception as exc:
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
    except Exception:
        hours = 24
    try:
        limit = int(request.query.get("limit", 100))
    except Exception:
        limit = 100
    entries = get_recent_outcomes(hours=hours, limit=limit)
    runs = []
    for e in reversed(entries):
        explainability = e.get("explainability") if isinstance(e.get("explainability"), dict) else {}
        scope_mode = e.get("scope_mode") or explainability.get("scope_mode")
        lock_mode = e.get("lock_mode") or explainability.get("lock_mode")
        anchor_id = e.get("anchor_id") or explainability.get("anchor_id")
        anchor_age_seconds = (
            e.get("anchor_age")
            if e.get("anchor_age") is not None
            else e.get("anchor_age_seconds")
        )
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

        calibration_payload = _build_offline_quality_calibration_payload() if include_calibration else {
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
    except Exception as exc:
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
    except Exception as exc:
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
                parts = [p.strip() for p in re.split(r'\s{2,}', line) if p.strip()]

            if len(parts) >= 2:
                name = parts[0]
                status = parts[1]
                is_up = "Up" in status
                containers.append({
                    "name": name,
                    "status": status,
                    "is_up": is_up
                })

    # Fetch NAS containers (Synology DS920+)
    try:
        from config import cfg as _net_cfg
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-p", str(_net_cfg.nas_ssh_port), "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
            f"{_net_cfg.nas_ssh_user}@{_net_cfg.nas_ip}",
            "/usr/local/bin/docker ps --format '{{.Names}}\t{{.Status}}'",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            for line in stdout.decode().strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    containers.append({
                        "name": f"{parts[0]} (NAS)",
                        "status": parts[1],
                        "is_up": "Up" in parts[1],
                    })
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
                parts = [p.strip() for p in re.split(r'\s{2,}', sl) if p.strip()]

            if len(parts) >= 2:
                stats_list.append({
                    "name": parts[0],
                    "cpu": parts[1] if len(parts) > 1 else "?",
                    "mem": parts[2] if len(parts) > 2 else "?",
                })

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
                match = re.search(r'\*\*(/volume\d+)\*\*:\s+(.+?\s+used)\s*/\s*(.+?\s+total)\s*\((\d+)%\)', line)
                if match:
                    sys_stats["nas_disks"].append({
                        "mount": match.group(1),
                        "used": match.group(2).replace(" used", ""),
                        "total": match.group(3).replace(" total", ""),
                        "pct": int(match.group(4)),
                    })
    except Exception as exc:
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
        skills_list.append({
            "name": name,
            "description": decl_map.get(name, getattr(SKILLS[name], "__doc__", "") or ""),
        })

    # Build categorized skill data for collapsible dashboard display
    from skills import SKILL_CATEGORIES
    skill_categories = {}
    for cat_name, cat_skills in SKILL_CATEGORIES.items():
        valid = [n for n in sorted(cat_skills) if n in SKILLS]
        if valid:
            skill_categories[cat_name] = [
                {"name": n, "description": decl_map.get(n, getattr(SKILLS[n], "__doc__", "") or "")}
                for n in valid
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
                activity.append({
                    "timestamp": entry.get("ts", ""),
                    "user": entry.get("user", "unknown"),
                    "action": entry.get("action", ""),
                    "detail": entry.get("detail", "")[:100],
                    "result": entry.get("result", ""),
                })
    except Exception as exc:
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
    except Exception as exc:
        log.debug("Model usage stats failed: %s", exc)

    # D-6: 7-day token usage for sparkline
    daily_tokens: list[dict] = []
    try:
        from datetime import datetime, timedelta
        daily_data = sp._data.get("daily", {})
        for i in range(6, -1, -1):
            day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            tokens = daily_data.get(day, {})
            daily_tokens.append({
                "date": day,
                "input": tokens.get("input_tokens", 0),
                "output": tokens.get("output_tokens", 0),
                "total": tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0),
            })
    except Exception as exc:
        log.debug("Daily token stats failed: %s", exc)

    payload = {
        "version": VERSION,
        "uptime_seconds": round(uptime_s, 1),
        "bot_user": str(bot.user) if bot and bot.user else None,
        "guilds": len(bot.guilds) if bot else 0,
        "latency_ms": round(bot.latency * 1000, 1) if bot and bot.latency else 0,
        "python": platform.python_version(),
        "discord_py": discord.__version__,
        "search_provider": "Perplexity AI" if app_cfg.perplexity_api_key else ("Firecrawl" if app_cfg.firecrawl_api_key else ("Tavily" if app_cfg.tavily_api_key else "DuckDuckGo")),
        "firecrawl_tier": "Free (500 pages/mo)" if app_cfg.firecrawl_api_key else "Not configured",
        "content_extraction": "trafilatura → Jina Reader → Playwright",
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
            "firecrawl": sp._data.get("firecrawl", {"calls": 0, "pages_scraped": 0, "total_cost_usd": 0.0, "daily": {}}),
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
    except Exception as exc:
        log.debug("QMD facts load failed: %s", exc)

    # Learned rules (last 20, newest first)
    try:
        from rules_engine import _load_rules
        rules = await _load_rules()
        data["rules"] = rules[-20:]
        data["rules"].reverse()
    except Exception as exc:
        log.debug("Rules load failed: %s", exc)

    # Vector store collection stats
    try:
        import vector_store
        data["stats"] = await vector_store.get_stats()
    except Exception as exc:
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
            warnings.update({
                "scoped_recall_alerts": alerts.get("count", 0),
                "message": "Potential cross-channel/thread recall leakage was blocked recently.",
            })
        compaction = summary.get("compaction", {}) if isinstance(summary, dict) else {}
        if isinstance(compaction, dict):
            warnings["recent_compactions"] = int(compaction.get("count", 0) or 0)
        if warnings:
            summary["warnings"] = warnings
        return web.json_response(summary)
    except Exception as exc:
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
    except Exception as exc:
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
    except Exception as exc:
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
    except Exception as exc:
        log.debug("Channel profile recommendation action failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def api_goals_handler(request):
    """Return active goals for the dashboard."""
    try:
        from goal_tracker import get_active_goals
        goals = get_active_goals()
        return web.json_response({"goals": goals})
    except Exception as exc:
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
            reports.append({
                "id": doc_id,
                "query": meta.get("query", "Unknown query"),
                "date": meta.get("added_at", 0),
                "excerpt": text,
                "sources": meta.get("sources", ""),
            })

        reports.sort(key=lambda r: r.get("date", 0), reverse=True)
        return web.json_response({"reports": reports[:20]})
    except Exception as e:
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
                        preview = " ".join(
                            p for p in parts if isinstance(p, str)
                        )[:100]
                        break

                threads.append({
                    "name": f.stem,
                    "messages": len(history),
                    "preview": preview,
                    "modified": f.stat().st_mtime,
                    "size_kb": round(f.stat().st_size / 1024, 1),
                })
            except (json.JSONDecodeError, OSError, KeyError) as exc:
                log.debug("Thread file parse failed %s: %s", f.name, exc)
                continue

    return web.json_response({"threads": threads[:30]})


async def api_schedules_handler(request):
    """Return scheduled tasks for the dashboard."""
    try:
        schedules_file = Path("/memory/schedules.json")
        if not schedules_file.exists():
            return web.json_response({"tasks": []})
        tasks = json.loads(schedules_file.read_text())
        clean = []
        for t in tasks:
            name = t.get("action") or t.get("skill_name") or t.get("name") or "unknown"

            cron_expr = t.get("cron_expression") or t.get("cron") or ""
            interval = t.get("interval_minutes", t.get("interval", 0))
            cron_hour = t.get("cron_hour", -1)
            cron_minute = t.get("cron_minute", 0)

            if cron_expr:
                schedule_human = _cron_to_human(cron_expr)
            elif interval and interval > 0:
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

            clean.append({
                "id": t.get("task_id", t.get("id", "")),
                "name": name,
                "interval": interval,
                "cron_expression": cron_expr,
                "schedule_human": schedule_human,
                "prompt": t.get("prompt", ""),
                "last_run": t.get("last_run", 0),
                "next_run": t.get("next_run", 0),
                "enabled": t.get("enabled", True),
                "args": str(t.get("args", t.get("args_json", {})))[:80],
            })
        return web.json_response({"tasks": clean})
    except Exception as exc:
        log.debug("Schedules API failed: %s", exc)
        return web.json_response({"tasks": []})


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
    except Exception as exc:
        log.debug("Schedule delete failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def api_errors_handler(request):
    """Return error stats for the dashboard."""
    try:
        from error_tracker import get_error_stats
        stats = get_error_stats(hours=24)
        return web.json_response(stats)
    except Exception as exc:
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
        return web.json_response({
            "overall": round(health["overall"] * 100, 1),
            "metrics": health["metrics"],
            "entry_count": len(entries),
            "avg_importance": round(stats.get("avgImportance", 0), 2),
            "last_dream": stats.get("lastDream", None),
            "health_history": stats.get("healthHistory", [])[-14:],
        })
    except Exception as exc:
        log.debug("Dream health API failed: %s", exc)
        return web.json_response({
            "overall": 0, "metrics": {}, "entry_count": 0,
            "avg_importance": 0, "last_dream": None, "health_history": [],
        })


async def api_config_status_handler(request):
    """Return configuration status for every key API/service."""
    from config import cfg
    return web.json_response({"services": cfg.config_status()})


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


async def api_quality_metrics_handler(request: web.Request) -> web.Response:
    """Return quality telemetry counters for dashboard quality operations."""
    try:
        from error_tracker import get_recent_outcomes
        from metrics_collector import get_quality_event_snapshot

        snapshot = get_quality_event_snapshot(limit=25)
        event_counts = _normalize_event_counts(snapshot.get("event_counts", {}))
        feedback_snapshot = snapshot.get("feedback")
        if not isinstance(feedback_snapshot, dict):
            feedback_snapshot = {}
        feedback_helpful = int(
            feedback_snapshot.get("helpful", event_counts.get("ask_feedback_helpful", 0)) or 0
        )
        feedback_not_helpful = int(
            feedback_snapshot.get("not_helpful", event_counts.get("ask_feedback_not_helpful", 0)) or 0
        )
        feedback_total = feedback_helpful + feedback_not_helpful
        feedback_helpful_rate = (
            round(feedback_helpful / feedback_total, 3)
            if feedback_total > 0
            else None
        )
        feedback_accepted = int(
            feedback_snapshot.get("accepted", event_counts.get("ask_feedback_accepted", feedback_total)) or 0
        )
        feedback_suppressed = int(
            feedback_snapshot.get("suppressed", event_counts.get("ask_feedback_suppressed", 0)) or 0
        )
        feedback_suppressed_dedupe = int(
            feedback_snapshot.get(
                "suppressed_dedupe", event_counts.get("ask_feedback_suppressed_dedupe", 0)
            )
            or 0
        )
        feedback_suppressed_rate_limited = int(
            feedback_snapshot.get(
                "suppressed_rate_limited",
                int(event_counts.get("ask_feedback_suppressed_rate_limited_user", 0) or 0)
                + int(event_counts.get("ask_feedback_suppressed_rate_limited_channel", 0) or 0),
            )
            or 0
        )

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
        calibration_drift = {
            "available": False,
            "baseline_available": False,
            "status": "unavailable",
            "severity_level": "unknown",
            "severe": False,
            "score": 0,
            "regressed_metrics": [],
        }
        try:
            calibration_payload = _build_offline_quality_calibration_payload()
            if isinstance(calibration_payload, dict):
                drift = calibration_payload.get("drift")
                if not isinstance(drift, dict):
                    drift = {}
                severity = drift.get("severity")
                if not isinstance(severity, dict):
                    severity = {}
                regressed_metrics = drift.get("regressed_metrics")
                if not isinstance(regressed_metrics, list):
                    regressed_metrics = []
                calibration_drift = {
                    "available": bool(calibration_payload.get("available")),
                    "baseline_available": bool(drift.get("baseline_available")),
                    "status": str(drift.get("status") or "unavailable"),
                    "severity_level": str(severity.get("level") or "unknown"),
                    "severe": bool(severity.get("severe")),
                    "score": int(severity.get("score") or 0),
                    "regressed_metrics": [str(item) for item in regressed_metrics if str(item).strip()],
                }
                if calibration_drift["severe"]:
                    status = "degraded"
        except Exception as exc:
            log.debug("Quality metrics drift status unavailable: %s", exc)

        recent_runs = get_recent_outcomes(hours=24, limit=120)
        if not isinstance(recent_runs, list):
            recent_runs = []

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
                if any(
                    isinstance(note, str) and "low confidence" in note.lower()
                    for note in routing_notes
                ):
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

        return web.json_response(
            {
                **snapshot,
                "signals": signals,
                "domain_trends": domain_trends[:_QUALITY_DOMAIN_LIMIT],
                "top_recurring_failures": top_recurring_failures,
                "top_quality_failure_categories": top_quality_failure_categories,
                "quality_failure_categories": quality_failure_categories,
                "recent_signal_slices": recent_signal_slices,
                "status": status,
                "calibration_drift": calibration_drift,
                "warning_pressure": warning_pressure,
                "score_distribution": score_distribution,
                "low_confidence": {
                    "prompt_count": int(low_confidence_prompt_count),
                    "top_reasons": [
                        {"reason": reason, "count": int(count)}
                        for reason, count in top_low_confidence_reasons
                    ],
                },
                "retry_outcomes": retry_outcomes,
                "runtime_window": {
                    "hours": 24,
                    "runs_considered": len(recent_runs),
                    "runs_with_quality": int(runs_with_quality),
                    "runs_with_retry": int(runs_with_retry),
                },
                "feedback": {
                    "helpful": feedback_helpful,
                    "not_helpful": feedback_not_helpful,
                    "total": feedback_total,
                    "helpful_rate": feedback_helpful_rate,
                    "accepted": feedback_accepted,
                    "suppressed": feedback_suppressed,
                    "suppressed_dedupe": feedback_suppressed_dedupe,
                    "suppressed_rate_limited": feedback_suppressed_rate_limited,
                },
            }
        )
    except Exception as exc:
        log.debug("Quality metrics API failed: %s", exc)
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
            nodes.append({
                "id": e["id"],
                "summary": e.get("summary", "")[:60],
                "importance": e.get("importance", 0.5),
                "tags": e.get("tags", []),
                "created": e.get("created", ""),
            })
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
                    parts = [p.strip() for p in re.split(r'\s{2,}', line) if p.strip()]
                if parts:
                    name = parts[0]
                    is_up = any("Up" in p for p in parts)
                    angle = angle_step * i - (math.pi / 2)
                    x = 400 + math.cos(angle) * radius
                    y = 250 + math.sin(angle) * radius
                    nodes.append({
                        "id": name, "label": name, "type": "container",
                        "status": "up" if is_up else "down",
                        "x": round(x), "y": round(y),
                    })
                    edges.append({"source": "mac-mini", "target": name})
    except Exception as e:
        log.debug("Topology container fetch failed: %s", e)

    return web.json_response({"nodes": nodes, "edges": edges})


# ---------------------------------------------------------------------------
# Agent interaction endpoints (dashboard chat & report generation)
# ---------------------------------------------------------------------------

async def api_agent_ask_handler(request: web.Request) -> web.Response:
    """POST /api/agent/ask — Submit a prompt to OpenClaw and return the response.

    Body (JSON):
        prompt   (str, required)  — the user's question or command
        model    (str, optional)  — model preference: "auto" | "gemini" | "openai" | "anthropic" | "local"
        history  (list, optional) — prior conversation turns [{"role": ..., "content": ...}]

    Returns JSON:
        response  (str)  — assistant reply text
        model     (str)  — model that was used
        tokens    (int)  — approximate token usage (0 if unavailable)
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"error": "prompt is required"}, status=400)

    model_pref = body.get("model", "auto")
    history: list[dict] = body.get("history") or []

    try:
        from llm.chat import chat as llm_chat
        result = await llm_chat(
            user_message=prompt,
            history=history,
            user_name="Dashboard",
            model_preference=model_pref,
        )
        # chat() returns (response_text, updated_history, metadata_dict)
        if isinstance(result, tuple):
            response_text, _hist, meta = result
        else:
            response_text, meta = result, {}

        model_used = meta.get("model_used", model_pref) if isinstance(meta, dict) else model_pref
        tokens = meta.get("total_tokens", 0) if isinstance(meta, dict) else 0

        return web.json_response({
            "response": response_text,
            "model": model_used,
            "tokens": tokens,
        })
    except Exception as exc:
        log.error("api_agent_ask_handler error: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


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
    except Exception:
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
    except Exception as exc:
        log.error("api_recap_generate_handler error: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)
