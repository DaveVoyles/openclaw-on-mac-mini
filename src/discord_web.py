"""
OpenClaw web / health server — extracted from bot.py.

Provides the aiohttp web application with health, metrics, smoke-test,
dashboard, and webhook endpoints.  The bot instance is stored in
``app["bot"]`` so handlers can access it without importing bot.py.
"""

import asyncio
import json
import logging
import os
import platform
import time

import discord
from aiohttp import web

from audit import audit_log
from constants import EMBED_FIELD_LIMIT, EMBED_SPLIT_LIMIT
from dashboard import (
    api_config_status_handler,
    api_dashboard_handler,
    api_dream_health_handler,
    api_errors_handler,
    api_goals_handler,
    api_memories_handler,
    api_research_handler,
    api_response_stats_handler,
    api_schedule_delete_handler,
    api_schedules_handler,
    api_search_stats_handler,
    api_skill_stats_handler,
    api_status_handler,
    api_threads_handler,
    dashboard_handler,
    guide_handler,
)
from llm import chat as llm_chat

log = logging.getLogger("openclaw")

HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8765"))
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def _health_handler(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    uptime_s = time.monotonic() - bot.start_time
    payload = {
        "status": "healthy",
        "uptime_seconds": round(uptime_s, 1),
        "bot_user": str(bot.user) if bot.user else None,
        "guilds": len(bot.guilds),
        "python": platform.python_version(),
        "discord_py": discord.__version__,
    }
    return web.json_response(payload)


async def _metrics_handler(request: web.Request) -> web.Response:
    """Expose Prometheus-format metrics for Grafana / Uptime Kuma scraping."""
    bot = request.app["bot"]
    uptime_s = time.monotonic() - bot.start_time
    guilds = len(bot.guilds)
    latency_ms = round(bot.latency * 1000, 1) if bot.latency else 0

    lines = [
        "# HELP openclaw_up Whether the bot is running (1=up)",
        "# TYPE openclaw_up gauge",
        "openclaw_up 1",
        "",
        "# HELP openclaw_uptime_seconds Seconds since bot started",
        "# TYPE openclaw_uptime_seconds counter",
        f"openclaw_uptime_seconds {uptime_s:.1f}",
        "",
        "# HELP openclaw_guilds Number of Discord guilds connected to",
        "# TYPE openclaw_guilds gauge",
        f"openclaw_guilds {guilds}",
        "",
        "# HELP openclaw_latency_ms Discord gateway latency in milliseconds",
        "# TYPE openclaw_latency_ms gauge",
        f"openclaw_latency_ms {latency_ms}",
        "",
    ]
    return web.Response(
        text="\n".join(lines),
        content_type="text/plain",
    )


async def _smoke_handler(request: web.Request) -> web.Response:
    """Run lightweight subsystem smoke tests and return JSON results."""
    from datetime import datetime, timezone

    checks: dict[str, dict] = {}
    overall = "pass"

    # 1. gemini_api
    try:
        t0 = time.monotonic()
        from llm import _get_model
        model = await asyncio.wait_for(_get_model(), timeout=10)
        resp = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, "Say hello"),
            timeout=10,
        )
        latency = round((time.monotonic() - t0) * 1000)
        if resp and resp.text:
            checks["gemini_api"] = {"status": "pass", "latency_ms": latency}
        else:
            checks["gemini_api"] = {"status": "fail", "error": "empty response"}
            overall = "fail"
    except Exception as exc:
        checks["gemini_api"] = {"status": "fail", "error": str(exc)[:200]}
        overall = "fail"

    # 2. ollama
    try:
        from llm import LOCAL_LLM_ENABLED, _ollama_available
        if not LOCAL_LLM_ENABLED:
            checks["ollama"] = {"status": "skipped", "reason": "LOCAL_LLM_ENABLED=false"}
        else:
            t0 = time.monotonic()
            up = await asyncio.wait_for(_ollama_available(), timeout=10)
            latency = round((time.monotonic() - t0) * 1000)
            if up:
                checks["ollama"] = {"status": "pass", "latency_ms": latency}
            else:
                checks["ollama"] = {"status": "fail", "error": "ollama not reachable"}
                overall = "fail"
    except Exception as exc:
        checks["ollama"] = {"status": "fail", "error": str(exc)[:200]}
        overall = "fail"

    # 3. chromadb
    try:
        t0 = time.monotonic()
        from vector_store import _get_client
        client = _get_client()
        client.heartbeat()
        latency = round((time.monotonic() - t0) * 1000)
        checks["chromadb"] = {"status": "pass", "latency_ms": latency}
    except Exception as exc:
        checks["chromadb"] = {"status": "fail", "error": str(exc)[:200]}
        overall = "fail"

    # 4. memory_sqlite
    try:
        import sqlite3 as _sqlite3

        from thread_store import DB_PATH as _threads_db_path
        t0 = time.monotonic()
        conn = _sqlite3.connect(str(_threads_db_path), timeout=5)
        try:
            row = conn.execute("SELECT count(*) FROM threads").fetchone()
            thread_count = row[0] if row else 0
        finally:
            conn.close()
        latency = round((time.monotonic() - t0) * 1000)
        checks["memory_sqlite"] = {"status": "pass", "threads": thread_count}
    except Exception as exc:
        checks["memory_sqlite"] = {"status": "fail", "error": str(exc)[:200]}
        overall = "fail"

    # 5. config
    try:
        from config import cfg as _cfg
        if _cfg.discord_bot_token and _cfg.google_api_key:
            checks["config"] = {"status": "pass"}
        else:
            missing = []
            if not _cfg.discord_bot_token:
                missing.append("discord_bot_token")
            if not _cfg.google_api_key:
                missing.append("google_api_key")
            checks["config"] = {"status": "fail", "error": f"missing: {', '.join(missing)}"}
            overall = "fail"
    except Exception as exc:
        checks["config"] = {"status": "fail", "error": str(exc)[:200]}
        overall = "fail"

    # 6. skill_registry
    try:
        from skills import SKILLS as _skills
        count = len(_skills)
        has_search = "search_web" in _skills
        if count > 0 and has_search:
            checks["skill_registry"] = {"status": "pass", "skill_count": count}
        else:
            checks["skill_registry"] = {
                "status": "fail",
                "error": f"count={count}, search_web={'found' if has_search else 'missing'}",
            }
            overall = "fail"
    except Exception as exc:
        checks["skill_registry"] = {"status": "fail", "error": str(exc)[:200]}
        overall = "fail"

    payload = {
        "status": overall,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    status_code = 200 if overall == "pass" else 503
    return web.json_response(payload, status=status_code)


async def _webhook_handler(request: web.Request) -> web.Response:
    """Receive inbound webhooks from Sonarr, Radarr, Plex, qBittorrent, etc.

    POST /webhook/<source>
    Payload: arbitrary JSON from the upstream service.
    The handler formats a human-readable Discord notification and posts it
    to ALERT_CHANNEL_ID (if configured), then returns 200 OK.
    """
    if WEBHOOK_SECRET:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {WEBHOOK_SECRET}":
            return web.json_response({"error": "unauthorized"}, status=401)

    from webhook_formatter import FORMATTERS, format_generic

    bot = request.app["bot"]
    source = request.match_info.get("source", "unknown").lower()
    try:
        payload = await request.json()
    except Exception as exc:
        log.debug("Webhook JSON parse failed: %s", exc)
        payload = {}

    if not isinstance(payload, dict):
        payload = {"raw": str(payload)}

    formatter = FORMATTERS.get(source)
    if formatter:
        title, description, color = formatter(payload)
    else:
        title, description, color = format_generic(source, payload)
    log.info("Webhook received from %s: %s", source, description[:120])

    if ALERT_CHANNEL_ID:
        channel = bot.get_channel(ALERT_CHANNEL_ID)
        if channel:
            embed = discord.Embed(title=title, description=description, color=color)
            embed.set_footer(text=f"Incoming webhook → {source}")
            try:
                await channel.send(embed=embed)
            except Exception as e:
                log.error("Failed to send webhook notification: %s", e)

            _error_keywords = {"error", "fail", "critical", "down", "unhealthy", "exception", "warning"}
            payload_lower = json.dumps(payload).lower()
            event_lower = (payload.get("eventType") or payload.get("event") or "").lower()
            is_error_event = (
                any(kw in payload_lower for kw in _error_keywords)
                or event_lower in ("error", "warning", "applicationupdate", "health")
            )
            if is_error_event:
                asyncio.create_task(_analyze_webhook_event(source, payload, channel))

    return web.json_response({"ok": True})


async def _analyze_webhook_event(source: str, payload: dict, channel):
    """Run a quick LLM analysis on an error-bearing webhook payload and post as follow-up."""
    prompt = (
        f"A '{source}' webhook arrived with the following payload:\n"
        f"{json.dumps(payload, indent=2)[:2000]}\n\n"
        "In 2-3 sentences: what happened, and what (if any) action should the operator take?"
    )
    try:
        analysis, _, _ = await asyncio.wait_for(llm_chat(prompt), timeout=20)
        if analysis:
            embed = discord.Embed(
                title="🔍 AI Assessment",
                description=analysis[:EMBED_FIELD_LIMIT],
                color=discord.Color.orange(),
            )
            await channel.send(embed=embed)
    except Exception as e:
        log.warning("Webhook auto-analysis failed: %s", e)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def start_health_server(bot) -> web.AppRunner:
    """Create and start the aiohttp web application. Returns the AppRunner for cleanup."""
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/", dashboard_handler)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/metrics", _metrics_handler)
    app.router.add_get("/dashboard", dashboard_handler)
    app.router.add_get("/api/dashboard", api_dashboard_handler)
    app.router.add_get("/api/memories", api_memories_handler)
    app.router.add_get("/api/threads", api_threads_handler)
    app.router.add_get("/api/goals", api_goals_handler)
    app.router.add_get("/api/research", api_research_handler)
    app.router.add_get("/api/schedules", api_schedules_handler)
    app.router.add_delete("/api/schedules/{task_id}", api_schedule_delete_handler)
    app.router.add_get("/api/status", api_status_handler)
    app.router.add_get("/api/errors", api_errors_handler)
    app.router.add_get("/api/response-stats", api_response_stats_handler)
    app.router.add_get("/api/dream-health", api_dream_health_handler)
    app.router.add_get("/api/config-status", api_config_status_handler)
    app.router.add_get("/api/search-stats", api_search_stats_handler)
    app.router.add_get("/api/skill-stats", api_skill_stats_handler)
    app.router.add_get("/guide", guide_handler)
    app.router.add_get("/smoke", _smoke_handler)
    app.router.add_post("/webhook/{source}", _webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
    await site.start()
    log.info(
        "Health endpoint listening on :%d/health (and /metrics, /smoke, /dashboard, /guide, /webhook/<source>)",
        HEALTH_PORT,
    )
    return runner
