"""discord_background — backward-compatible re-exports after modularization.

The implementation has been split into focused sub-modules:
  bg_briefing   — morning/evening briefings
  bg_monitoring — error monitor, container health, resource monitor
  bg_healing    — proactive scan, self-healing, audit writer, cleanup
  bg_tasks      — task lifecycle (start/stop/restart/supervise)
"""

import asyncio  # needed so patch("discord_background.asyncio.sleep", ...) works in tests

from bg_briefing import (
    evening_digest_loop,
    morning_briefing_loop,
    send_evening_digest,
    send_morning_briefing,
)
from bg_healing import (
    _CopilotFixView,
    _SAFE_RESTART_TARGETS,
    _check_quality_drift_alert,
    _execute_self_healing,
    _gather_system_signals,
    _parse_heal_actions,
    _run_proactive_scan,
    audit_writer_loop,
    background_cleanup_loop,
    proactive_insight_loop,
)
from bg_monitoring import (
    _check_container_health,
    _check_monstervision_cookies,
    _container_prev_state,
    _container_unhealthy_count,
    _post_error_alert,
    container_health_loop,
    error_monitor_loop,
    resource_monitor_loop,
)
from bg_tasks import (
    _BACKGROUND_FACTORIES,
    _BACKGROUND_RESTART_DELAY_SECONDS,
    _BACKGROUND_STOPPING,
    _BACKGROUND_TASKS,
    _build_background_task_factories,
    _handle_background_task_done,
    _launch_background_task,
    _restart_background_task,
    _run_supervised_background_task,
    reminder_loop,
    start_background_tasks,
    stop_background_tasks,
)

__all__ = [
    # bg_briefing
    "morning_briefing_loop",
    "send_morning_briefing",
    "evening_digest_loop",
    "send_evening_digest",
    # bg_monitoring
    "error_monitor_loop",
    "_post_error_alert",
    "container_health_loop",
    "_check_container_health",
    "_check_monstervision_cookies",
    "_container_prev_state",
    "_container_unhealthy_count",
    "resource_monitor_loop",
    # bg_healing
    "proactive_insight_loop",
    "_check_quality_drift_alert",
    "_gather_system_signals",
    "_parse_heal_actions",
    "_execute_self_healing",
    "_run_proactive_scan",
    "_CopilotFixView",
    "_SAFE_RESTART_TARGETS",
    "audit_writer_loop",
    "background_cleanup_loop",
    # bg_tasks
    "reminder_loop",
    "_build_background_task_factories",
    "_handle_background_task_done",
    "_launch_background_task",
    "_run_supervised_background_task",
    "_restart_background_task",
    "start_background_tasks",
    "stop_background_tasks",
    "_BACKGROUND_TASKS",
    "_BACKGROUND_FACTORIES",
    "_BACKGROUND_STOPPING",
    "_BACKGROUND_RESTART_DELAY_SECONDS",
]
