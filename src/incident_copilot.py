"""Incident Copilot context collection, summarization, and action execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from health_history import get_trend
from llm import chat as llm_chat
from memory import recall_memories as memory_recall
from skills import (
    get_container_logs,
    get_container_status,
    get_system_stats,
    restart_container,
)

log = logging.getLogger(__name__)

AUDIT_DIR = Path(os.getenv("AUDIT_DIR", "/audit"))
DEFAULT_SERVICE_CANDIDATES = (
    "sonarr",
    "radarr",
    "lidarr",
    "prowlarr",
    "sabnzbd",
    "qbittorrent",
    "overseerr",
    "tautulli",
    "plex",
)
SAFE_RESTART_TARGETS = frozenset(
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
ERROR_PATTERN = re.compile(r"error|warn|exception|critical|fatal|traceback|failed", re.IGNORECASE)


def _select_services(incident: dict[str, Any], requested_services: str = "", limit: int = 4) -> list[str]:
    if requested_services.strip():
        chosen = [part.strip().lower() for part in requested_services.split(",") if part.strip()]
    else:
        haystack = f"{incident.get('title', '')} {incident.get('description', '')}".lower()
        chosen = [svc for svc in DEFAULT_SERVICE_CANDIDATES if svc in haystack]
    if not chosen:
        chosen = list(DEFAULT_SERVICE_CANDIDATES[:limit])
    deduped: list[str] = []
    seen: set[str] = set()
    for service in chosen:
        if service not in seen:
            deduped.append(service)
            seen.add(service)
    return deduped[: max(1, min(limit, 8))]


def _read_recent_audit(limit: int = 120) -> list[dict[str, str]]:
    if not AUDIT_DIR.exists():
        return []
    entries: list[dict[str, str]] = []
    for jsonl_file in sorted(AUDIT_DIR.glob("*.jsonl"), reverse=True):
        try:
            lines = jsonl_file.read_text().splitlines()
        except OSError as exc:
            log.debug("Audit read failed for %s: %s", jsonl_file, exc)
            continue
        for line in reversed(lines):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries.append(
                {
                    "ts": str(raw.get("ts", ""))[:40],
                    "action": str(raw.get("action", ""))[:80],
                    "detail": str(raw.get("detail", ""))[:220],
                    "result": str(raw.get("result", ""))[:40],
                }
            )
            if len(entries) >= limit:
                return entries
    return entries


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _sanitize_actions(raw_actions: list[Any]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in raw_actions[:6]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()[:140]
        description = str(item.get("description", "")).strip()[:280]
        command = str(item.get("command", "")).strip().lower()
        target = str(item.get("target", "")).strip().lower()
        risk_level = str(item.get("risk_level", "medium")).strip().lower()
        rationale = str(item.get("rationale", "")).strip()[:220]
        executable = command == "restart_container" and target in SAFE_RESTART_TARGETS
        if not title:
            continue
        sanitized.append(
            {
                "title": title,
                "description": description or "No description provided.",
                "command": command if executable else "",
                "target": target if executable else "",
                "risk_level": risk_level if risk_level in {"low", "medium", "high", "critical"} else "medium",
                "executable": executable,
                "rationale": rationale,
            }
        )
    return sanitized


def _heuristic_actions(service_errors: dict[str, int]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    noisy_services = sorted(service_errors.items(), key=lambda item: item[1], reverse=True)
    if noisy_services:
        primary, _ = noisy_services[0]
        if primary in SAFE_RESTART_TARGETS:
            actions.append(
                {
                    "title": f"Restart {primary}",
                    "description": f"{primary} has concentrated error signals in recent logs.",
                    "command": "restart_container",
                    "target": primary,
                    "risk_level": "high",
                    "executable": True,
                    "rationale": "Fast mitigation for recurring runtime faults.",
                }
            )
        actions.append(
            {
                "title": f"Review {primary} status",
                "description": f"Confirm health/state for {primary} and dependent services after mitigation.",
                "command": "",
                "target": "",
                "risk_level": "medium",
                "executable": False,
                "rationale": "Validate impact after first mitigation step.",
            }
        )
    else:
        actions.append(
            {
                "title": "Capture more telemetry",
                "description": "No dominant failing service detected; gather extra logs and metrics before remediation.",
                "command": "",
                "target": "",
                "risk_level": "low",
                "executable": False,
                "rationale": "Reduce risk of incorrect mitigation.",
            }
        )
    return actions


async def build_incident_context(
    incident: dict[str, Any],
    *,
    requested_services: str = "",
    log_lines: int = 120,
) -> dict[str, Any]:
    services = _select_services(incident, requested_services=requested_services)

    status_results = await asyncio.gather(
        *(get_container_status(service) for service in services),
        return_exceptions=True,
    )
    log_results = await asyncio.gather(
        *(get_container_logs(service, lines=max(100, min(log_lines, 240))) for service in services),
        return_exceptions=True,
    )

    service_details: list[dict[str, Any]] = []
    service_errors: dict[str, int] = {}
    for idx, service in enumerate(services):
        status_value = status_results[idx]
        logs_value = log_results[idx]
        status_text = status_value if isinstance(status_value, str) else f"Unavailable ({status_value})"
        logs_text = logs_value if isinstance(logs_value, str) else f"Unavailable ({logs_value})"
        error_hits = len(ERROR_PATTERN.findall(logs_text))
        service_errors[service] = error_hits
        service_details.append(
            {
                "service": service,
                "status": status_text[:800],
                "logs_excerpt": logs_text[:2000],
                "error_hits": error_hits,
            }
        )

    health_trends: list[dict[str, Any]] = []
    for service in services:
        try:
            trend = get_trend(service, days=3)
            health_trends.append(
                {
                    "service": service,
                    "uptime_pct": trend.get("uptime_pct", 0),
                    "status_counts": trend.get("status_counts", {}),
                    "recent_incidents": trend.get("recent_incidents", [])[:3],
                }
            )
        except (OSError, ValueError, TypeError) as exc:
            log.debug("Health trend unavailable for %s: %s", service, exc)

    try:
        system_stats = await get_system_stats()
    except (OSError, ValueError, TypeError) as exc:
        log.debug("System stats unavailable: %s", exc)
        system_stats = "System stats unavailable."

    query = f"{incident.get('title', '')} {incident.get('description', '')}".strip()
    try:
        recalled = await memory_recall(query or "incident", top_k=5, include_rules=True, include_profile=True)
    except (OSError, ValueError, TypeError) as exc:
        log.debug("Memory recall unavailable: %s", exc)
        recalled = []

    return {
        "services": services,
        "service_details": service_details,
        "service_errors": service_errors,
        "health_trends": health_trends,
        "audit_tail": _read_recent_audit(limit=100),
        "system_stats": system_stats[:2000],
        "memory_hits": recalled[:5],
    }


async def generate_incident_report(
    incident: dict[str, Any],
    *,
    requested_services: str = "",
) -> dict[str, Any]:
    context = await build_incident_context(incident, requested_services=requested_services)
    prompt = (
        "You are Incident Copilot. Summarize the incident and produce next actions.\n"
        "Return ONLY JSON with this schema:\n"
        "{"
        '"summary": string,'
        '"suspected_causes": [string],'
        '"actions": ['
        "{"
        '"title": string,'
        '"description": string,'
        '"command": "restart_container"|"",'
        '"target": string,'
        '"risk_level": "low"|"medium"|"high"|"critical",'
        '"rationale": string'
        "}"
        "]"
        "}\n"
        "Only use command='restart_container' for safe media services. Prefer 2-4 actions.\n\n"
        f"INCIDENT: {json.dumps(incident, default=str)[:2000]}\n"
        f"CONTEXT: {json.dumps(context, default=str)[:12000]}"
    )

    model_used = "heuristic"
    parsed: dict[str, Any] | None = None
    try:
        response_text, _, model_used = await llm_chat(
            prompt,
            history=[],
            model_preference="auto",
            tool_declarations=[],
        )
        parsed = _extract_json_payload(response_text)
    except Exception as exc:  # broad: intentional
        log.warning("Incident Copilot LLM generation failed: %s", exc)

    summary = ""
    causes: list[str] = []
    actions: list[dict[str, Any]] = []
    if parsed:
        summary = str(parsed.get("summary", "")).strip()[:1200]
        raw_causes = parsed.get("suspected_causes", [])
        if isinstance(raw_causes, list):
            causes = [str(item).strip()[:180] for item in raw_causes if str(item).strip()][:5]
        actions = _sanitize_actions(parsed.get("actions", []) if isinstance(parsed.get("actions", []), list) else [])

    if not summary:
        top_errors = sorted(context["service_errors"].items(), key=lambda item: item[1], reverse=True)
        if top_errors and top_errors[0][1] > 0:
            service, hits = top_errors[0]
            summary = (
                f"Detected concentrated runtime faults around `{service}` "
                f"({hits} error-like log signals) during incident triage."
            )
            causes = [f"{service} emitted repeated error patterns.", "Service-level degradation likely contributing."]
        else:
            summary = "Initial telemetry does not show a single dominant failing service. Continue targeted triage."

    if not actions:
        actions = _heuristic_actions(context["service_errors"])

    return {
        "summary": summary,
        "suspected_causes": causes,
        "actions": actions,
        "model_used": model_used,
        "context": context,
    }


async def execute_incident_action(action: dict[str, Any]) -> str:
    command = str(action.get("command", "")).strip().lower()
    target = str(action.get("target", "")).strip().lower()
    if command == "restart_container" and target in SAFE_RESTART_TARGETS:
        return await restart_container(target)
    return "❌ Unsupported or unsafe incident action."
