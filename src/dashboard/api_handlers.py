"""JSON API endpoint handlers for the dashboard."""

import asyncio
import json
import math
import platform
import re
import time
from pathlib import Path

import aiohttp
import discord
from aiohttp import web

from http_session import SessionManager as _SessionManager
from spending import get_quota_status, get_response_stats
from spending import tracker as spending_tracker

from .helpers import GITHUB_REPO, VERSION, _command_list, _cron_to_human, _load_config, log

_dashboard_sessions = _SessionManager(timeout=10, name="dashboard")


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


def _parse_sms_user_id(raw_value: str | int | None) -> int | None:
    if raw_value in (None, ""):
        return None
    try:
        parsed = int(raw_value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


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

        if health.status == PatreonHealthStatus.OK:
            checks["patreon"] = {"status": "ok", "detail": "healthy"}
        elif health.status == PatreonHealthStatus.WARNING:
            # Use the primary issue as detail
            detail = health.issues[0] if health.issues else "attention needed"
            checks["patreon"] = {"status": "no_key", "detail": detail[:50]}
        elif health.status == PatreonHealthStatus.CRITICAL:
            detail = health.issues[0] if health.issues else "critical"
            checks["patreon"] = {"status": "down", "detail": detail[:50]}
        else:
            checks["patreon"] = {"status": "down", "detail": "unknown"}

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
