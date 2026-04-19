# OpenClaw — Background Tasks and Scheduler Architecture
<!-- Updated: 2026-04-18 -->


This document explains how OpenClaw runs long-lived background work, how loops are supervised, and where scheduled behavior lives.

## Scope

Read this alongside [ARCHITECTURE](ARCHITECTURE.md), [RESILIENCE](RESILIENCE.md), and [SERVICES](SERVICES.md).

## Execution model

OpenClaw uses two related patterns for non-interactive runtime work:

1. **Supervised background loops** for always-on monitoring and housekeeping.
2. **Scheduled/time-window tasks** for daily briefings, digests, and other periodic behavior.

`src/bg_tasks.py` is the top-level lifecycle owner for the first category.

## Supervisor architecture

### Factory registration

`_build_background_task_factories(bot)` assembles the canonical loop list:

- `background_cleanup`
- `audit_writer`
- `reminder`
- `morning_briefing`*
- `evening_digest`*
- `proactive_insight`*
- `error_monitor`*
- `container_health`*
- `resource_monitor`*

`*` alert-producing loops are only enabled when `ALERT_CHANNEL_ID` is configured.

### Supervision contract

For each loop, `bg_tasks.py`:

- creates one `asyncio.Task`,
- stores it in module-level registries,
- wraps execution in trace context and metrics collection,
- restarts the loop after crashes or unexpected exits.

Restart policy is centralized in `_handle_background_task_done()` and `_restart_background_task()`.

### Backoff behavior

`_BackoffTracker` provides per-loop restart delays:

- 5 seconds
- 15 seconds
- 60 seconds
- 300 seconds

After 30 minutes of clean runtime, the tracker resets. This prevents one flaky loop from thrashing the process indefinitely at high frequency.

## Loop families

### Healing and housekeeping (`src/bg_healing.py`)

| Loop | Purpose | Key side effects |
| --- | --- | --- |
| `audit_writer_loop()` | Flush buffered audit records | Writes JSONL audit files and flushes high-severity events eagerly |
| `background_cleanup_loop()` | Remove expired transient runtime state | Cleans conversation and approval stores; records metrics |
| `proactive_insight_loop(bot)` | Scan for notable issues on a slower cadence | Runs quality-drift checks, gathers system signals, and can alert into Discord |

### Monitoring (`src/bg_monitoring.py`)

| Loop | Purpose | Guardrails |
| --- | --- | --- |
| `error_monitor_loop(bot)` | Detect clustered failures | Skips optional scans under high user load; may run diagnosis/fix flows |
| `container_health_loop(bot)` | Watch Docker container status and cookie expiry | Alerts on state changes; auto-restarts only allow-listed containers |
| `resource_monitor_loop(bot)` | Enforce per-container CPU/memory thresholds | Posts bounded alerts with cooldown context |

### Time-of-day briefings (`src/bg_briefing.py`)

| Loop | Purpose | Scheduling model |
| --- | --- | --- |
| `morning_briefing_loop(bot)` | Generate daily briefing | Polls local-owner time window, then spawns `send_morning_briefing()` |
| `evening_digest_loop(bot)` | Generate daily digest | Polls local-owner time window, then spawns `send_evening_digest()` |

These loops do not rely on a separate cron daemon; they are cooperative asyncio schedulers that wake on interval checks.

### Reminder loop (`src/bg_tasks.py`)

`reminder_loop(bot)` is defined in the supervisor module because it is short and tightly coupled to lifecycle control. It polls due reminders every 15 seconds and sends user DMs.

## Scheduler boundaries

There are two scheduler concepts in the repo:

- `bg_tasks.py` supervises resident asyncio loops.
- the broader app architecture also references `src/scheduler.py` / `scheduler_advanced.py` for explicit task-scheduling surfaces used by other runtime features.

For architecture work, treat `bg_tasks.py` as the owner of **resident loop lifecycle**, and `scheduler*.py` as the owner of **feature-level scheduling abstractions**.

## Observability and failure semantics

Background work is instrumented in several layers:

- `trace_context` wraps supervised execution with stable command/component labels,
- `metrics_collector` records per-loop duration and success/failure,
- `audit_log()` records notable alerts and self-healing actions,
- `health_history` captures container-state trends used later by briefings and diagnostics.

A loop failure is not silent, but it also is not assumed fatal to the entire bot process.

## Startup and shutdown

- `start_background_tasks(bot)` is intended for `OpenClawBot.on_ready()`.
- A second start request is ignored when tasks are already active.
- `stop_background_tasks()` cancels all supervised loops, awaits them, clears registries, and marks the supervisor as stopping so cancellation does not trigger restarts.

## Design caveats

- Some scheduled behavior is interval polling plus time-window checks rather than exact cron semantics.
- Background state is process-local; restarting the bot recreates loops from factories instead of restoring per-task checkpoints.
- Supervisor restarts help availability but cannot by themselves fix a deterministic bug inside a loop.
