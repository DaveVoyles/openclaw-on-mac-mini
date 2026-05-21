# OpenClaw — Resilience, Error Handling, and Fallbacks
<!-- Updated: 2026-04-18 -->


This document maps the runtime paths that keep OpenClaw usable when providers, background loops, or local surfaces fail.

## Scope

Use this deep dive alongside [ARCHITECTURE](ARCHITECTURE.md), [LLM-ROUTING](LLM-ROUTING.md), and [BACKGROUND_TASKS](BACKGROUND_TASKS.md).

## Resilience Layers

| Layer | Primary modules | Resilience behavior |
| --- | --- | --- |
| LLM provider calls | `src/llm/providers.py`, `src/llm/chat.py`, `src/model_routing_policy.py` | Retries transient HTTP failures, opens provider circuit breakers, walks a fallback chain, and falls back to Gemini when non-Gemini routing yields no usable reply. |
| Background supervision | `src/slack_bot.py` (inline `asyncio.create_task` background loops such as `_digest_loop`, `_file_alert_loop`), `src/health_checker.py`, `src/health_history.py` | Long-lived loops run as tasks owned by the Slack Bolt process; failures are recorded by the health checker. |
| Health and self-healing | `src/health_checker.py`, `src/health_history.py`, `src/openclaw_cli_health.py` | Distinguishes `healthy` / `degraded` / `unhealthy`, records history, and exposes operator-facing status summaries. |
| CLI UX safeguards | `src/openclaw_cli.py`, `src/openclaw_cli_router.py`, `src/openclaw_cli_health.py`, `src/openclaw_cli_sessions.py` | Converts connection failures into actionable hints, keeps routing logic isolated from UI, and preserves session/watch state on disk. |

## LLM failure handling

### Provider-layer protections

`src/llm/providers.py` is the single non-Gemini HTTP boundary.

- `check_proxy_health()` caches Copilot proxy health and drives a background re-check loop.
- `_call_with_retry()` retries transient provider failures (`429`, `500`, `502`, `503`, `504`, connection errors, and timeouts) with exponential backoff.
- `_is_open()`, `_record_failure()`, and `_record_success()` implement provider circuit breakers so repeated failures fast-fail instead of amplifying latency.
- `call_provider()` returns a typed `ProviderResponse`, preserves token accounting, and walks `PROVIDER_FALLBACK_CHAIN` when the requested provider returns no usable text.
- `scan_providers()` and `src/llm/startup.py` separate startup availability checks from normal request orchestration.

### Orchestration-level fallback order

`src/llm/chat.py` treats non-Gemini routing as opportunistic, not authoritative.

1. Try web-search direct return for live-information queries.
2. Try Copilot coding fast-path for code-oriented prompts without recalled context.
3. Try automatic provider routing via `classify_query()` and `select_auto_route()`.
4. Try forced provider modes when the caller explicitly requested one.
5. Fall through to Gemini when earlier paths fail, return empty, or are unavailable.

That means routing failures degrade into a slower or more expensive answer path rather than a hard user-visible error in the common case.

### Tool-routing guardrail

`select_tool_route()` prefers providers with native tool/function calling support, but defaults back to Gemini if none are available. Tool-heavy flows therefore keep one canonical safety net.

## Background-loop recovery

Background loops live inline in `src/slack_bot.py` (started as `asyncio.create_task` during Bolt startup). They no longer share a dedicated supervisor module.

- Each loop wraps its body in `try/except` and logs failures via the standard logger.
- The Slack Bolt process restarts in Docker on crash, which restarts all loops; there is no in-process restart with exponential backoff today.
- `src/health_checker.py` and `src/health_history.py` track outcomes for observability.

> Historical note: a dedicated `bg_tasks.py` supervisor with `5s → 15s → 60s → 300s` backoff existed during the Discord-era architecture; it was removed when the Discord runtime was dropped.

## Monitoring and self-healing behaviors

### Container health

`src/health_checker.py` combines state-change detection with bounded remediation:

- alerts only on unhealthy/exited state transitions,
- tracks consecutive failures per container,
- auto-restarts only allow-listed containers after two failed checks,
- records status history through `health_history.record()`.

### Health checker

`src/health_checker.py` models health as `healthy`, `degraded`, or `unhealthy`.

- readiness runs all registered checks and stores last results,
- overall health is derived from the last readiness snapshot,
- `self_heal()` performs small, local remediation steps for known unhealthy categories.

This is intentionally conservative: diagnostics are centralized, but healing remains limited and explicit.

## CLI degradation behavior

The standalone CLI is designed to fail with recovery hints instead of opaque exceptions.

- `fetch_health()` and `format_http_error()` normalize server-side failures.
- `format_url_error()` and `_build_error_recovery_hints()` turn transport issues into next actions such as `openclaw health` or `/retry`.
- `analyze_health_payload()` classifies partial/variant `/health` payloads as best effort instead of requiring a single exact schema.
- `openclaw_cli_router.py` keeps routing decisions pure, so broken rendering state does not corrupt route classification.
- `openclaw_cli_sessions.py` persists session metadata, events, watch state, and checkpoints independently, letting one damaged artifact fail soft without losing all session history.

## Operational caveats

- Some healing paths are advisory or partial (`HealthChecker.self_heal()` contains placeholders for database reconnect / disk cleanup).
- Provider failover only helps when alternate providers are configured and reachable.
- Background auto-restart protects long-lived loops, but logic bugs inside a loop can still repeatedly trip backoff.
- CLI persistence is durable at the file level, not transactional across multiple session artifacts.
