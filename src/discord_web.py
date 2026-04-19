"""
OpenClaw web / health server — extracted from bot.py.

Provides the aiohttp web application with health, metrics, smoke-test,
dashboard, and webhook endpoints.  The bot instance is stored in
``app["bot"]`` so handlers can access it without importing bot.py.
"""

import asyncio
import hmac
import json
import logging
import os
import platform
import sqlite3
import subprocess
import time
from pathlib import Path

import aiohttp
import discord
from aiohttp import web

from constants import EMBED_FIELD_LIMIT
from dashboard import setup_dashboard
from llm import chat as llm_chat
from metrics_collector import get_collector

log = logging.getLogger("openclaw")

from config import cfg as _web_cfg

HEALTH_PORT = _web_cfg.health_port
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))
WEBHOOK_SECRET = _web_cfg.webhook_secret
WEBHOOK_REQUIRE_AUTH = _web_cfg.webhook_require_auth
API_ACTION_TOKEN = _web_cfg.dashboard_api_token
API_ACTION_AUTH_REQUIRED = _web_cfg.dashboard_api_auth_required


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _git_sha() -> str:
    """Return the current HEAD commit SHA (short form), or 'unknown'.

    Reads from src/_git_sha.txt if present (written at deploy time by make ship-server),
    then falls back to subprocess git call, then returns 'unknown'.
    """
    sha_file = Path(__file__).parent / "_git_sha.txt"
    if sha_file.exists():
        try:
            return sha_file.read_text().strip() or "unknown"
        except OSError:
            pass
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
            cwd=Path(__file__).parent.parent,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _error_response(
    code: str,
    message: str,
    status: int = 400,
    details: dict | None = None,
) -> web.Response:
    """Return a standardized JSON error response."""
    body: dict = {"error": code, "message": message}
    if details:
        body["details"] = details
    return web.json_response(body, status=status)


async def _health_handler(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    uptime_s = time.monotonic() - bot.start_time
    checks: dict[str, str] = {}

    # Lightweight DB check (timeout=2 to keep /health fast)
    try:
        from thread_store import DB_PATH as _health_db_path
        conn = sqlite3.connect(str(_health_db_path), timeout=2)
        conn.execute("SELECT 1")
        conn.close()
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"

    # Vector store check (best-effort import only)
    try:
        from vector_store import _get_client
        _get_client().heartbeat()
        checks["vector_store"] = "ok"
    except Exception:
        checks["vector_store"] = "unavailable"

    overall = "healthy" if checks.get("db") == "ok" else "degraded"
    payload = {
        "status": overall,
        "uptime_seconds": round(uptime_s, 1),
        "bot_user": str(bot.user) if bot.user else None,
        "guilds": len(bot.guilds),
        "python": platform.python_version(),
        "discord_py": discord.__version__,
        "git_sha": _git_sha(),
        "checks": checks,
        "ts": time.time(),
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
    basic_metrics = "\n".join(lines)

    collector_metrics = ""
    content_type = "text/plain; version=0.0.4; charset=utf-8"
    try:
        collector = get_collector()
        collector_metrics = collector.export_prometheus().decode("utf-8")
        content_type = collector.get_prometheus_content_type()
    except Exception as exc:  # broad: intentional — collector may raise any internal error
        log.warning("Failed to export collector metrics: %s", exc)

    metrics_payload = collector_metrics
    if metrics_payload and not metrics_payload.endswith("\n"):
        metrics_payload += "\n"
    metrics_payload += basic_metrics

    return web.Response(
        body=metrics_payload.encode("utf-8"),
        headers={"Content-Type": content_type},
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
    except Exception as exc:  # broad: intentional — Gemini API throws provider-specific exceptions
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
    except (ImportError, asyncio.TimeoutError, aiohttp.ClientError, ConnectionError) as exc:
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
    except Exception as exc:  # broad: intentional — smoke test catches any connectivity failure
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
    except (ImportError, sqlite3.Error, OSError, ValueError) as exc:
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
    except (ImportError, AttributeError, KeyError) as exc:
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
    except (ImportError, AttributeError, KeyError) as exc:
        checks["skill_registry"] = {"status": "fail", "error": str(exc)[:200]}
        overall = "fail"

    payload = {
        "status": overall,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    status_code = 200 if overall == "pass" else 503
    return web.json_response(payload, status=status_code)


async def _trigger_scan_handler(request: web.Request) -> web.Response:
    """POST /api/trigger-scan — immediately run a proactive insight scan."""
    auth_error = _require_api_action_auth(request)
    if auth_error is not None:
        return auth_error
    import asyncio

    from discord_background import _run_proactive_scan
    bot = request.app["bot"]
    asyncio.create_task(_run_proactive_scan(bot))
    return web.json_response({"status": "scan triggered"})


async def _webhook_handler(request: web.Request) -> web.Response:
    """Receive inbound webhooks from Sonarr, Radarr, Plex, qBittorrent, etc.

    POST /webhook/<source>
    Payload: arbitrary JSON from the upstream service.
    The handler formats a human-readable Discord notification and posts it
    to ALERT_CHANNEL_ID (if configured), then returns 200 OK.
    """
    if WEBHOOK_REQUIRE_AUTH:
        if not WEBHOOK_SECRET:
            log.error("Webhook rejected: WEBHOOK_REQUIRE_AUTH=true but WEBHOOK_SECRET is not configured")
            return _error_response("INTERNAL_ERROR", "webhook auth not configured", status=503)
        if not _is_authorized_bearer(request, WEBHOOK_SECRET):
            return _error_response("UNAUTHORIZED", "unauthorized", status=401)

    from webhook_formatter import FORMATTERS, format_generic

    bot = request.app["bot"]
    source = request.match_info.get("source", "unknown").lower()
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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
            except Exception as e:  # broad: intentional — tests inject generic exceptions
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
    except Exception as e:  # broad: intentional — LLM and Discord can each raise many types
        log.warning("Webhook auto-analysis failed: %s", e)


def _is_authorized_bearer(request: web.Request, secret: str) -> bool:
    auth = request.headers.get("Authorization", "").strip()
    alt = request.headers.get("X-OpenClaw-Token", "").strip()
    expected = f"Bearer {secret}"
    return (
        hmac.compare_digest(auth.encode(), expected.encode())
        or hmac.compare_digest(alt.encode(), secret.encode())
    )


def _require_api_action_auth(request: web.Request) -> web.Response | None:
    if not API_ACTION_AUTH_REQUIRED:
        return None
    if not API_ACTION_TOKEN:
        log.error("API action auth required but DASHBOARD_API_TOKEN is not configured")
        return _error_response("INTERNAL_ERROR", "api action auth not configured", status=503)
    if not _is_authorized_bearer(request, API_ACTION_TOKEN):
        return _error_response("UNAUTHORIZED", "unauthorized", status=401)
    return None


# ---------------------------------------------------------------------------
# Granular health-check endpoints
# ---------------------------------------------------------------------------


async def _health_llm_handler(request: web.Request) -> web.Response:
    """Check LLM provider availability, token usage, and circuit-breaker state."""
    # Lazy import to avoid circular imports at module load time.
    try:
        from llm.providers import (
            COPILOT_PROXY_ENABLED,
            _circuit,
            _is_open,
            proxy_is_healthy,
            token_usage_summary,
        )
        _providers_available = True
    except (ImportError, Exception):
        COPILOT_PROXY_ENABLED = False
        _circuit: dict = {}
        _is_open = lambda p: False  # noqa: E731
        proxy_is_healthy = lambda: False  # noqa: E731
        token_usage_summary = lambda: {}  # noqa: E731
        _providers_available = False

    checks: dict[str, str] = {}

    # Ollama
    try:
        from config import cfg as _cfg
        ollama_url = _cfg.ollama_url
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{ollama_url}/api/tags",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as r:
                checks["ollama"] = "ok" if r.status == 200 else "down"
    except Exception:  # broad: intentional — health probe catches any connectivity failure
        checks["ollama"] = "down"
    checks["gemini"] = "ok" if os.getenv("GOOGLE_API_KEY") else "unconfigured"

    # Copilot proxy
    checks["copilot_proxy"] = "ok" if COPILOT_PROXY_ENABLED else "unconfigured"

    # Circuit-breaker state for all known providers (plus any that have tripped).
    _known_providers = {"copilot", "openai", "anthropic", "ollama"}
    circuit_state = {
        p: {"open": _is_open(p)}
        for p in sorted(_known_providers | set(_circuit.keys()))
    }

    any_ok = any(v == "ok" for v in checks.values())
    status_code = 200 if any_ok else 503
    return web.json_response(
        {
            "status": "ok" if any_ok else "down",
            "proxy_healthy": proxy_is_healthy(),
            "checks": checks,
            "token_usage": token_usage_summary(),
            "circuit_state": circuit_state,
        },
        status=status_code,
    )


async def _health_llm_circuit_handler(request: web.Request) -> web.Response:
    """GET /health/llm/circuit — lightweight circuit-breaker state only."""
    from llm.providers import _circuit, _is_open

    _known_providers = {"copilot", "openai", "anthropic", "ollama"}
    circuit_state = {
        p: {"open": _is_open(p)}
        for p in sorted(_known_providers | set(_circuit.keys()))
    }
    return web.json_response(circuit_state)


async def _health_llm_reset_handler(request: web.Request) -> web.Response:
    """POST /health/llm/reset — reset circuit-breaker state for one or all providers.

    Query params:
        provider (optional): name of a single provider to reset; omit to reset all.

    Returns:
        {"reset": [<provider>, ...], "circuit_state": {...}}
    """
    auth_error = _require_api_action_auth(request)
    if auth_error is not None:
        return auth_error

    from llm.providers import PROVIDER_FALLBACK_CHAIN, _circuit, _is_open, reset_circuit

    provider_param = request.rel_url.query.get("provider")
    _known_providers = {"copilot", "openai", "anthropic", "ollama"}
    all_providers = sorted(_known_providers | set(_circuit.keys()) | set(PROVIDER_FALLBACK_CHAIN))

    if provider_param:
        reset_circuit(provider_param)
        reset_list = [provider_param]
    else:
        for p in all_providers:
            reset_circuit(p)
        reset_list = all_providers

    circuit_state = {
        p: {"open": _is_open(p)}
        for p in sorted(_known_providers | set(_circuit.keys()) | set(PROVIDER_FALLBACK_CHAIN))
    }
    return web.json_response({"reset": reset_list, "circuit_state": circuit_state})


async def _health_memory_handler(request: web.Request) -> web.Response:
    """Check memory subsystem health (ChromaDB, QMD, threads DB)."""
    checks: dict[str, str] = {}

    # ChromaDB
    try:
        from vector_store import _get_client
        client = _get_client()
        client.heartbeat()
        checks["chromadb"] = "ok"
    except Exception:  # broad: intentional — health probe catches any connectivity failure
        checks["chromadb"] = "down"
    qmd_path = Path(os.getenv("QMD_PATH", "/app/data/qmd.json"))
    checks["qmd"] = "ok" if qmd_path.exists() else "missing"

    # Thread store SQLite
    try:
        import sqlite3 as _sqlite3

        from thread_store import DB_PATH as _threads_db_path
        conn = _sqlite3.connect(str(_threads_db_path), timeout=3)
        try:
            conn.execute("SELECT 1 FROM threads LIMIT 1")
            checks["threads_db"] = "ok"
        finally:
            conn.close()
    except Exception:  # broad: intentional — health probe catches any connectivity failure
        checks["threads_db"] = "down"

    chroma_ok = checks.get("chromadb") == "ok"
    overall = "ok" if chroma_ok else "degraded"
    status_code = 200 if chroma_ok else 503
    return web.json_response(
        {"status": overall, "checks": checks},
        status=status_code,
    )


async def _health_services_handler(request: web.Request) -> web.Response:
    """Check external service connectivity (Docker, NAS, scheduler)."""
    checks: dict[str, str] = {}

    # Docker socket
    docker_sock = Path("/var/run/docker.sock")
    checks["docker"] = "ok" if docker_sock.exists() else "unavailable"

    # NAS connectivity
    try:
        from config import cfg as _cfg
        nas_host = getattr(_cfg, "nas_host", "") or os.getenv("NAS_HOST", "")
        if nas_host:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"http://{nas_host}:5000",
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as r:
                    checks["nas"] = "ok" if r.status < 500 else "down"
        else:
            checks["nas"] = "unconfigured"
    except Exception:  # broad: intentional — health probe catches any connectivity failure
        checks["nas"] = "down"

    # Scheduler
    try:
        from scheduler import scheduler
        task_count = len(scheduler.list_tasks())
        checks["scheduler"] = f"ok ({task_count} tasks)"
    except Exception:  # broad: intentional — health probe catches any connectivity failure
        checks["scheduler"] = "down"

    any_down = any(v == "down" for v in checks.values())
    overall = "degraded" if any_down else "ok"
    status_code = 200 if not any_down else 503
    return web.json_response(
        {"status": overall, "checks": checks},
        status=status_code,
    )


# ---------------------------------------------------------------------------
# CLI self-update endpoint
# ---------------------------------------------------------------------------

_CLI_UPDATE_WHITELIST = {
    "openclaw_cli.py",
    "openclaw_cli_actions.py",
    "openclaw_cli_sessions.py",
    "subprocess_utils.py",
}


async def _cli_update_handler(request: web.Request) -> web.Response:
    filename = request.match_info["filename"]
    if filename not in _CLI_UPDATE_WHITELIST:
        return web.Response(status=404, text="Not found")
    file_path = Path(__file__).parent / filename
    return web.Response(text=file_path.read_text(encoding="utf-8"), content_type="text/plain")


async def _cli_update_meta_handler(request: web.Request) -> web.Response:
    """Return SHA256 hashes of the CLI source files for update checking."""
    import hashlib
    src_dir = Path(__file__).parent
    meta: dict[str, str] = {}
    for fname in sorted(_CLI_UPDATE_WHITELIST):
        fpath = src_dir / fname
        if fpath.exists():
            meta[fname] = hashlib.sha256(fpath.read_bytes()).hexdigest()
    return web.json_response(meta)


# ---------------------------------------------------------------------------
# OpenAI-compatible API endpoints (/v1/)
# ---------------------------------------------------------------------------

_OAI_MODELS = [
    {"id": "openclaw-auto",      "object": "model", "created": 1700000000, "owned_by": "openclaw"},
    {"id": "openclaw-gemini",    "object": "model", "created": 1700000000, "owned_by": "openclaw"},
    {"id": "openclaw-copilot",   "object": "model", "created": 1700000000, "owned_by": "openclaw"},
    {"id": "openclaw-openai",    "object": "model", "created": 1700000000, "owned_by": "openclaw"},
    {"id": "openclaw-anthropic", "object": "model", "created": 1700000000, "owned_by": "openclaw"},
]

_OAI_MODEL_MAP: dict[str, str] = {
    "openclaw-auto":      "auto",
    "openclaw-gemini":    "gemini",
    "openclaw-copilot":   "copilot",
    "openclaw-openai":    "openai",
    "openclaw-anthropic": "anthropic",
}


async def _v1_models_handler(request: web.Request) -> web.Response:
    """GET /v1/models — return OpenAI-compatible model list."""
    return web.json_response({"object": "list", "data": _OAI_MODELS})


async def _v1_chat_completions_handler(request: web.Request) -> web.Response | web.StreamResponse:
    """POST /v1/chat/completions — OpenAI-compatible chat endpoint."""
    import time as _time
    import uuid as _uuid

    from dashboard.api_handlers import _execute_agent_ask

    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    messages: list[dict] = body.get("messages") or []
    if not messages:
        return web.json_response({"error": "messages is required and must be non-empty"}, status=400)

    prompt = (messages[-1].get("content") or "").strip()
    if not prompt:
        return web.json_response({"error": "Last message content is required"}, status=400)

    history = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages[:-1]]
    model_name = str(body.get("model") or "openclaw-auto")
    model_pref = _OAI_MODEL_MAP.get(model_name, "auto")
    stream = bool(body.get("stream", False))
    completion_id = f"chatcmpl-{_uuid.uuid4().hex}"
    created_ts = int(_time.time())

    if not stream:
        try:
            result = await _execute_agent_ask(
                prompt=prompt,
                model_pref=model_pref,
                history=history,
                user_name="openwebui",
            )
        except Exception as exc:
            log.error("_v1_chat_completions_handler error: %s", exc)
            return web.json_response({"error": str(exc)}, status=500)

        return web.json_response({
            "id": completion_id,
            "object": "chat.completion",
            "created": created_ts,
            "model": result.get("model", model_name),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": result.get("response", "")},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": result.get("tokens", 0),
            },
        })

    # --- Streaming path ---
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)

    chunks_sent: int = 0

    async def _send_chunk(delta_content: str) -> None:
        nonlocal chunks_sent
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model_name,
            "choices": [{"index": 0, "delta": {"content": delta_content}, "finish_reason": None}],
        }
        await resp.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        chunks_sent += 1

    result: dict = {}
    try:
        result = await _execute_agent_ask(
            prompt=prompt,
            model_pref=model_pref,
            history=history,
            user_name="openwebui",
            on_partial_chunk=_send_chunk,
        )
    except Exception as exc:
        log.error("_v1_chat_completions_handler stream error: %s", exc)

    # Most LLM routes yield a single final chunk (no intermediate partials).
    # If _send_chunk was never called, emit the complete response now so the
    # client receives content before the stop chunk.
    if chunks_sent == 0:
        full_text = str(result.get("response", "")) if result else ""
        if full_text:
            await _send_chunk(full_text)

    # Final stop chunk
    stop_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created_ts,
        "model": model_name,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    await resp.write(f"data: {json.dumps(stop_chunk)}\n\n".encode("utf-8"))
    await resp.write(b"data: [DONE]\n\n")
    await resp.write_eof()
    return resp


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def start_health_server(bot) -> web.AppRunner:
    """Create and start the aiohttp web application. Returns the AppRunner for cleanup."""
    app = web.Application()
    app["bot"] = bot

    setup_dashboard(app, require_action_auth=_require_api_action_auth)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/health/llm", _health_llm_handler)
    app.router.add_get("/health/llm/circuit", _health_llm_circuit_handler)
    app.router.add_post("/health/llm/reset", _health_llm_reset_handler)
    app.router.add_get("/health/memory", _health_memory_handler)
    app.router.add_get("/health/services", _health_services_handler)
    app.router.add_get("/metrics", _metrics_handler)
    app.router.add_get("/smoke", _smoke_handler)
    app.router.add_post("/webhook/{source}", _webhook_handler)
    app.router.add_post("/api/trigger-scan", _trigger_scan_handler)
    app.router.add_get("/cli-update/{filename}", _cli_update_handler)
    app.router.add_get("/cli-update/meta", _cli_update_meta_handler)
    app.router.add_get("/v1/models", _v1_models_handler)
    app.router.add_post("/v1/chat/completions", _v1_chat_completions_handler)

    # Wave 4: file upload endpoint (POST /upload) — registered here so it
    # shares the existing aiohttp server instead of spawning a second one.
    try:
        from slack_bot import _handle_upload
        app.router.add_post("/upload", _handle_upload)
        log.info("POST /upload route registered (Wave 4 file upload endpoint)")
    except Exception as exc:
        log.warning("Could not register /upload route: %s", exc)

    # Wave 12: Dropbox OAuth2 callback — GET /dropbox/callback
    try:
        from slack_bot import _handle_dropbox_oauth_callback
        app.router.add_get("/dropbox/callback", _handle_dropbox_oauth_callback)
        log.info("GET /dropbox/callback route registered (Wave 12 Dropbox OAuth2)")
    except Exception as exc:
        log.warning("Could not register /dropbox/callback route: %s", exc)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
    await site.start()
    log.info(
        "Health endpoint listening on :%d/health (and /metrics, /smoke, /dashboard, /tech-guide, /webhook/<source>, /cli-update/<filename>, /cli-update/meta, /upload, /dropbox/callback)",
        HEALTH_PORT,
    )
    return runner
