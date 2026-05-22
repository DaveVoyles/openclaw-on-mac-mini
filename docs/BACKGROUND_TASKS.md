# OpenClaw — Background Tasks and Scheduler Architecture
<!-- Updated: 2026-05-21 -->

This document explains how OpenClaw runs long-lived background work and where scheduled behavior lives.

## Scope

Read this alongside [ARCHITECTURE](ARCHITECTURE.md), [RESILIENCE](RESILIENCE.md), and [SERVICES](SERVICES.md).

> **Historical note:** A supervised `bg_tasks.py` / `bg_monitoring.py` / `bg_healing.py` / `bg_briefing.py` family existed during the Discord era. All four modules were deleted in May 2026 when Discord was removed — they were tightly coupled to `discord.py` and were never wired into the Slack runtime. The pattern below is the current reality.

---

## Execution model

OpenClaw uses two patterns for non-interactive runtime work:

1. **Inline `asyncio.create_task()` loops** — started directly in `slack_bot.py`'s startup block for always-on background work.
2. **`src/scheduler.py` / `src/scheduler_advanced.py`** — for feature-level scheduling abstractions (daily briefings, digests, scheduled reminders, cron-style tasks).

There is **no centralized supervisor** that restarts crashed loops with backoff. Each loop handles its own error recovery, and the Docker container restart policy (`restart: unless-stopped`) provides the process-level recovery layer.

---

## Active background loops

All three loops are started in `slack_bot.py` around line 5953 inside the `app_started` event handler:

| Loop | Module | Purpose |
| ---- | ------ | ------- |
| `_file_alert_loop(client)` | `src/slack_bot.py` | Polls for new file uploads/alerts and notifies the configured Slack user |
| `_digest_loop(client)` | `src/slack_bot.py` | Sends scheduled digests and monthly tips to opted-in users via Slack DM |
| `dropbox_watch_loop(client, user_id)` | `src/dropbox_sync.py` | Watches a Dropbox folder for new files (only started when `DROPBOX_CONFIGURED` is true) |

Each loop is an `async def` coroutine that runs forever with `while True: try: ... except asyncio.CancelledError: break` structure. Errors inside the loop body are logged and the loop sleeps before retrying — crashes cause the task to exit silently until the container restarts.

---

## Adding a new background loop

See [`docs/AGENT-EXTENSION-GUIDE.md` § 6](AGENT-EXTENSION-GUIDE.md#6-add-a-background-loop) for the full recipe. Short version:

1. Define `async def my_loop(client: Any) -> None` in a dedicated module (e.g. `src/my_loop.py`)
2. Implement the `while True: try/except CancelledError` structure
3. Add `asyncio.create_task(my_loop(app.client))` in `slack_bot.py` around line 5953

---

## Scheduler abstraction

`src/scheduler.py` and `src/scheduler_advanced.py` provide explicit scheduling surfaces used by other runtime features (reminders, daily briefings, digest delivery). These are not the same as the raw background loops above — they are higher-level task scheduling APIs that themselves may fire from a background loop or on command invocation.

---

## Observability

- Background loop errors are logged via `log.warning(...)` with a standard prefix matching the loop name.
- `src/health_checker.py` and `src/health_history.py` capture container state trends independently of the loop framework.
- The `/health` endpoint reflects process liveness, not individual loop health.
