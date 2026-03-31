"""
OpenClaw Dashboard — lightweight HTML dashboard served on the health endpoint.
Routes: GET /dashboard (HTML), GET /api/dashboard (JSON),
        GET /api/memories (JSON), GET /api/threads (JSON)
"""

import asyncio
import json
import logging
import os
import platform
import time
from pathlib import Path

import discord
import yaml
from aiohttp import web

from spending import tracker as spending_tracker, get_response_stats

log = logging.getLogger("openclaw.dashboard")

CONFIG_DIR = Path("/config")
GITHUB_REPO = "https://github.com/DaveVoyles/openclaw-on-mac-mini"
VERSION = "0.5.0"

_config_cache: dict | None = None
_config_mtime: float = 0.0


def _load_config() -> dict:
    global _config_cache, _config_mtime
    cfg_file = CONFIG_DIR / "config.yaml"
    try:
        current_mtime = cfg_file.stat().st_mtime if cfg_file.exists() else 0.0
    except OSError:
        current_mtime = 0.0
    if _config_cache is not None and current_mtime == _config_mtime:
        return _config_cache
    _config_cache = yaml.safe_load(cfg_file.read_text()) if cfg_file.exists() else {}
    _config_mtime = current_mtime
    return _config_cache or {}


# ---------------------------------------------------------------------------
# D-5  Live Status Banner endpoint
# ---------------------------------------------------------------------------


async def api_status_handler(request):
    """Return connectivity status for all backends."""
    import aiohttp

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
    except Exception as exc:
        log.debug("Docker status check failed: %s", exc)
        checks["docker"] = {"status": "down"}

    # Ollama
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{cfg.ollama_url}/api/tags", timeout=aiohttp.ClientTimeout(total=TIMEOUT_FAST)) as resp:
                checks["ollama"] = {"status": "ok" if resp.status == 200 else "down"}
    except Exception as exc:
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
            async with aiohttp.ClientSession() as session:
                token = cfg.copilot_proxy_token
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                async with session.get(f"{proxy_url}/models", headers=headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT_FAST)) as resp:
                    checks["copilot_proxy"] = {"status": "ok" if resp.status == 200 else "down"}
        except Exception as exc:
            log.debug("Copilot proxy check failed: %s", exc)
            checks["copilot_proxy"] = {"status": "down"}
    else:
        checks["copilot_proxy"] = {"status": "not_configured"}

    return web.json_response(checks)


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
        # The output uses multiple spaces instead of tabs sometimes. Split by 2+ spaces.
        import re
        lines = [line.strip() for line in container_text.split("\n") if line.strip() and not line.startswith("NAMES")]
        for line in lines:
            # Match Names (first col), Status (middle), ignore Ports (last)
            # Docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
            # But skills/__init__.py uses actual tab characters (\t) in the format string.
            # Let's check if it's splitting correctly.
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if not parts or len(parts) < 2:
                # Fallback to regex split if tabs aren't present
                parts = [p.strip() for p in re.split(r'\s{2,}', line) if p.strip()]

            if len(parts) >= 2:
                name = parts[0]
                status = parts[1]
                # Map status to health color/icon
                is_up = "Up" in status
                containers.append({
                    "name": name,
                    "status": status,
                    "is_up": is_up
                })

    # Get resource stats
    stats_text = await get_docker_stats()
    stats_list = []
    if not stats_text.startswith("\u274c"):
        # Parse table Format: NAME CPU% MEM NET
        stat_lines = [ln.strip() for ln in stats_text.split("\n") if ln.strip() and not ln.startswith("NAME")]
        for sl in stat_lines:
            parts = [p.strip() for p in sl.split("\t") if p.strip()]
            if not parts or len(parts) < 2:
                # Fallback to regex split if tabs aren't present
                import re
                parts = [p.strip() for p in re.split(r'\s{2,}', sl) if p.strip()]

            if len(parts) >= 2:
                stats_list.append({
                    "name": parts[0],
                    "cpu": parts[1] if len(parts) > 1 else "?",
                    "mem": parts[2] if len(parts) > 2 else "?",
                })

    # Get server system stats (CPU/MEM/Disk)
    sys_stats_text = await get_system_stats()
    # Format: **CPU**: 10.5% (8 cores)\n**Memory**: 4.2 / 16.0 GB (26.3%)\n**Disk** `/`: 200GB used / 500GB total (40%)
    sys_stats = {"cpu": "N/A", "mem": "N/A", "disk": "N/A"}
    for line in sys_stats_text.split("\n"):
        if "**CPU**" in line:
            sys_stats["cpu"] = line.split(":", 1)[1].strip()
        elif "Average" in line:
            sys_stats["cpu"] = line.split(":", 1)[1].strip()
        elif "**Memory**" in line:
            sys_stats["mem"] = line.split(":", 1)[1].strip()
        elif "**Disk**" in line:
            sys_stats["disk"] = line.split(":", 1)[1].strip()

    # Get ontology facts (limit to recent 5)
    ontology_text = await ontology_query()
    ontology_facts = []
    if not ontology_text.startswith("❌") and "Found" in ontology_text:
        # Simple extraction of bullets
        fact_lines = [ln.strip("• ").strip() for ln in ontology_text.split("\n") if ln.strip().startswith("•")]
        ontology_facts = fact_lines[:8]

    from config import cfg as app_cfg
    cfg = _load_config()
    sp = spending_tracker

    skills_list = []
    # Build skills from tool declarations (has descriptions)
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
            import json
            # Read most recent JSONL files (named YYYY-MM-DD.jsonl)
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

    # Model usage stats from error journal (has model_used per /ask call)
    model_usage = {}
    try:
        from error_tracker import get_recent_outcomes
        outcomes = get_recent_outcomes(hours=7 * 24, limit=5000)
        for entry in outcomes:
            model = entry.get("model_used", "")
            if model and model not in ("unknown", "error", "timeout", "none"):
                # Normalize: strip "models/" prefix for cleaner display
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
        # Get recent research from ChromaDB
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

        # Sort by date descending
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

                # Extract preview from first user message
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
            except Exception as exc:
                log.debug("Thread file parse failed %s: %s", f.name, exc)
                continue

    return web.json_response({"threads": threads[:30]})


# ---------------------------------------------------------------------------
# D-4  Scheduled Tasks endpoint
# ---------------------------------------------------------------------------

_CRON_DOW = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def _cron_to_human(expr: str) -> str:
    """Convert a cron expression to a human-readable string (best-effort)."""
    try:
        parts = expr.strip().split()
        if len(parts) != 5:
            return expr
        minute, hour, dom, month, dow = parts

        time_str = ""
        if hour != "*" and minute != "*":
            time_str = f"at {hour.zfill(2)}:{minute.zfill(2)}"
        elif hour != "*":
            time_str = f"at hour {hour}"
        elif minute != "*":
            every_m = minute.lstrip("*/")
            time_str = f"every {every_m} min"

        day_str = ""
        if dow != "*":
            day_names = []
            for part in dow.split(","):
                if "-" in part:
                    lo, hi = part.split("-", 1)
                    day_names.append(f"{_CRON_DOW.get(int(lo), lo)}–{_CRON_DOW.get(int(hi), hi)}")
                else:
                    day_names.append(_CRON_DOW.get(int(part), part))
            day_str = ", ".join(day_names)
        elif dom != "*":
            day_str = f"day {dom} of month"

        if month != "*":
            day_str += f" (month {month})"

        if day_str and time_str:
            return f"{day_str} {time_str}"
        return day_str or time_str or expr
    except Exception:
        return expr


async def api_schedules_handler(request):
    """Return scheduled tasks for the dashboard."""
    try:
        import json
        from pathlib import Path
        schedules_file = Path("/memory/schedules.json")
        if not schedules_file.exists():
            return web.json_response({"tasks": []})
        tasks = json.loads(schedules_file.read_text())
        # Keep only essential fields
        clean = []
        for t in tasks:
            # Resolve task name: prefer action, then skill_name, then name
            name = t.get("action") or t.get("skill_name") or t.get("name") or "unknown"

            # Build human-readable schedule description
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


def _command_list() -> list[dict]:
    """Static command reference grouped by category."""
    return [
        {"category": "🏛️ Foundation", "commands": [
            {"name": "/ping", "desc": "Check if bot is alive"},
            {"name": "/about", "desc": "Version and system info"},
            {"name": "/whoami", "desc": "Your Discord identity & permissions"},
            {"name": "/help", "desc": "List all commands"},
        ]},
        {"category": "🐳 Docker & System", "commands": [
            {"name": "/containers", "desc": "List running containers"},
            {"name": "/status <service>", "desc": "Container detail + resources"},
            {"name": "/logs <service> [lines]", "desc": "View container logs"},
            {"name": "/system", "desc": "CPU, memory, disk usage"},
            {"name": "/dockerstats", "desc": "Per-container resource usage"},
            {"name": "/restart <service>", "desc": "Restart a container (requires approval)"},
        ]},
        {"category": "🤖 AI & LLM", "commands": [
            {"name": "/ask <question> [model]", "desc": "AI-powered query — auto-routes to Gemini (tools) or Ollama (chat). Optional model: auto/local/gemini."},
            {"name": "/model show", "desc": "Show your current LLM routing preference and Ollama status."},
            {"name": "/model set <preference>", "desc": "Set your default LLM routing: auto (smart), local (Gemma), or gemini (cloud)."},
            {"name": "/research <query> [deep:true]", "desc": "Deep multi-step research — Discord thread, planned sub-queries, 4-tier search (Perplexity → Tavily → DDG → Bing Lite), source ranking, cross-referencing, confidence levels, synthesized report with methodology section"},
            {"name": "/weather [location]", "desc": "Current conditions + 3-day forecast for any location (default: WEATHER_DEFAULT_LOCATION env var)"},
            {"name": "/clear", "desc": "Clear active conversation history"},
            {"name": "/save <name>", "desc": "Save current conversation as a named thread (persisted to disk)"},
            {"name": "/resume <name>", "desc": "Resume a previously saved conversation thread"},
            {"name": "/threads", "desc": "List all your saved conversation threads"},
            {"name": "/forget <name>", "desc": "Delete a saved conversation thread"},
            {"name": "/analyze <service> [lines]", "desc": "AI log analysis"},
        ]},
        {"category": "🎬 Media & Downloads", "commands": [
            {"name": "/search <query> [type]", "desc": "Search Sonarr/Radarr catalogs"},
            {"name": "/queue", "desc": "Active downloads (SABnzbd + qBit)"},
            {"name": "/recent [count]", "desc": "Recently added Plex media"},
            {"name": "/health", "desc": "Check *arr + download client health"},
            {"name": "/ports", "desc": "Service port connectivity check"},
            {"name": "/report", "desc": "Comprehensive status report"},
        ]},
        {"category": "🧠 Memory & Automation", "commands": [
            {"name": "/remember <fact> [tags]", "desc": "Store a fact in long-term memory"},
            {"name": "/recall <query>", "desc": "Search long-term memory"},
            {"name": "/schedule", "desc": "Manage scheduled tasks (CRUD via slash command)"},
            {"name": "/skills", "desc": "List all LLM-callable skills"},
            {"name": "/briefing", "desc": "On-demand morning briefing (weather + health + calendar)"},
            {"name": "/audit-summary", "desc": "Analytics on today's audit log"},
            {"name": "/nowplaying", "desc": "Live Plex active streams"},
            {"name": "/dream", "desc": "Run cognitive dream cycle (memory consolidation)"},
            {"name": "/memory-health", "desc": "Show memory health score and 5 metrics"},
            {"name": "/memory-export", "desc": "Export memory bundle"},
        ]},
        {"category": "🌐 Network & Monitoring", "commands": [
            {"name": "/network", "desc": "LAN, internet, DNS connectivity"},
            {"name": "/tailscale", "desc": "Tailscale VPN status"},
            {"name": "/speedtest", "desc": "Network speed test"},
            {"name": "/spending [breakdown]", "desc": "Gemini API cost tracking"},
        ]},
        {"category": "Security & Admin", "commands": [
            {"name": "/pending", "desc": "Pending approval requests"},
            {"name": "/auditlog [lines]", "desc": "View audit trail"},
            {"name": "/estop [stop|resume]", "desc": "Emergency stop all actions"},
            {"name": "/mail <to> <subject> <body>", "desc": "Send email via AgentMail"},
        ]},
        {"category": "Third-Party API Gateway (via /ask)", "commands": [
            {"name": "gateway_request", "desc": "Call any of 100+ APIs (Slack, GitHub, Notion, HubSpot, Stripe…) via Maton managed OAuth. Invoked by /ask."},
            {"name": "gateway_list_connections", "desc": "List active Maton OAuth connections (optionally filter by app). Invoked by /ask."},
            {"name": "gateway_create_connection", "desc": "Create a new Maton OAuth connection for an app and return the authorization URL. Invoked by /ask."},
        ]},
        {"category": "Knowledge Graph & Ontology (via /ask)", "commands": [
            {"name": "ontology_create_entity", "desc": "Create a new typed entity (Person, Project, Task, etc.) in graph memory."},
            {"name": "ontology_get_entity", "desc": "Retrieve all details and relations for a specific entity name."},
            {"name": "ontology_relate", "desc": "Create a typed link between two entities (e.g., 'blocks', 'manages')."},
            {"name": "ontology_query", "desc": "Search the knowledge graph for entities by name or type."},
        ]},
        {"category": "Self-Management & Autonomy (via /ask)", "commands": [
            {"name": "spawn_worker", "desc": "Spawn a focused AI sub-agent to accomplish a specific goal autonomously using its own tool loop."},
            {"name": "create_scheduled_task", "desc": "Create a recurring scheduled task (LLM-controlled). Supports cron expressions, prompt jobs, or interval-based."},
            {"name": "cancel_scheduled_task", "desc": "Cancel a scheduled task by ID."},
            {"name": "list_scheduled_tasks", "desc": "List all active scheduled tasks with cron expressions, run counts, and next run times."},
            {"name": "webfetch_md", "desc": "Smartly scrape any URL and convert main content to clean Markdown."},
            {"name": "git_status", "desc": "Check project repository status for code changes."},
            {"name": "git_log", "desc": "View recent code change history (commit log)."},
            {"name": "git_diff", "desc": "Compare code changes or view uncommitted changes."},
            {"name": "git_commit", "desc": "Commit all current changes with a brief summary message."},
            {"name": "init_planning_files", "desc": "Initialize task_plan.md, findings.md, progress.md for complex tasks."},
            {"name": "update_plan_status", "desc": "Log progress or update status of a phase in planning files."},
        ]},
        {"category": "📋 Agent Loop & Plans", "commands": [
            {"name": "/plans [status]", "desc": "List active/recent agent plans. Filter: all, in-progress, completed, interrupted."},
            {"name": "/plan-detail <plan_id>", "desc": "Show full details of a specific plan (steps, status, outputs)."},
            {"name": "/resume-plan <plan_id>", "desc": "Resume an interrupted plan from where it left off."},
            {"name": "/cancel-plan <plan_id>", "desc": "Cancel an active plan (marks interrupted, resets in-progress steps)."},
            {"name": "create_plan", "desc": "(via /ask) Create a new task plan with a goal and ordered steps. Returns plan_id."},
            {"name": "update_plan_step", "desc": "(via /ask) Update a step's status (done/failed/skipped) with output summary."},
            {"name": "read_plan", "desc": "(via /ask) Read the current state of a plan including all step statuses."},
            {"name": "list_plans", "desc": "(via /ask) List plans filtered by status."},
            {"name": "adjust_plan", "desc": "(via /ask) Add, remove, or reorder steps in an active plan."},
            {"name": "cancel_plan", "desc": "(via /ask) Cancel an active plan and mark it interrupted."},
            {"name": "resume_plan", "desc": "(via /ask) Resume an interrupted plan from where it left off."},
        ]},
    ]


# ---------------------------------------------------------------------------
# HTML dashboard (self-contained, no external deps)
# ---------------------------------------------------------------------------


async def dashboard_handler(request: web.Request) -> web.Response:
    """Serve the dashboard HTML page."""
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def guide_handler(request: web.Request) -> web.Response:
    """Serve the guide / tutorial HTML page."""
    return web.Response(text=GUIDE_HTML, content_type="text/html")


# ---------------------------------------------------------------------------
# E7  Error Dashboard endpoint
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Config Status endpoint
# ---------------------------------------------------------------------------


async def api_config_status_handler(request):
    """Return configuration status for every key API/service."""
    from config import cfg
    return web.json_response({"services": cfg.config_status()})


async def api_search_stats_handler(request):
    """Return per-provider search usage statistics."""
    from search_provider import all_stats
    return web.json_response(all_stats())


async def api_skill_stats_handler(request):
    """Return skill invocation counts."""
    from llm_tools import get_skill_stats
    return web.json_response(get_skill_stats())


_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
DASHBOARD_HTML = (_TEMPLATES_DIR / "dashboard.html").read_text()



# ---------------------------------------------------------------------------
# Guide / Tutorial page
# ---------------------------------------------------------------------------

GUIDE_HTML = (_TEMPLATES_DIR / "guide.html").read_text()
