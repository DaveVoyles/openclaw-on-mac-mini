"""Route registration for the dashboard package."""

from collections.abc import Awaitable, Callable

from aiohttp import web

from .api_handlers import (
    api_agent_ask_handler,
    api_agent_ask_stream_handler,
    api_agent_session_detail_handler,
    api_agent_session_intervention_handler,
    api_agent_sessions_handler,
    api_approval_decision_handler,
    api_approvals_handler,
    api_channel_memory_action_handler,
    api_channel_memory_inspect_handler,
    api_channel_profile_recommendation_action_handler,
    api_channel_profile_recommendations_handler,
    api_config_status_handler,
    api_dashboard_handler,
    api_dream_health_handler,
    api_errors_handler,
    api_goals_handler,
    api_knowledge_graph_handler,
    api_memories_handler,
    api_plan_detail_handler,
    api_plans_handler,
    api_quality_eval_handler,
    api_quality_metrics_handler,
    api_quota_status_handler,
    api_recap_generate_handler,
    api_research_handler,
    api_response_stats_handler,
    api_runs_handler,
    api_schedule_delete_handler,
    api_schedule_toggle_handler,
    api_schedule_update_handler,
    api_schedules_handler,
    api_search_stats_handler,
    api_skill_stats_handler,
    api_sms_history_handler,
    api_sms_settings_handler,
    api_sms_status_handler,
    api_status_handler,
    api_task_status_detail_handler,
    api_task_status_handler,
    api_threads_handler,
    api_topology_handler,
)
from .html_handlers import (
    dashboard_handler,
    guide_handler,
    openclaw_cli_download_handler,
    openclaw_cli_installer_handler,
    openclaw_cli_remote_installer_handler,
    openclaw_cli_support_download_handler,
    terminal_handler,
)


def setup_dashboard(
    app: web.Application,
    *,
    require_action_auth: Callable[[web.Request], web.Response | None] | None = None,
) -> None:
    """Register all dashboard routes on the given aiohttp application."""
    def action(handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
        if require_action_auth is None:
            return handler

        async def _authed(request: web.Request) -> web.StreamResponse:
            auth_error = require_action_auth(request)
            if auth_error is not None:
                return auth_error
            return await handler(request)

        return _authed

    app.router.add_get("/", dashboard_handler)
    app.router.add_get("/dashboard", dashboard_handler)
    app.router.add_get("/guide", guide_handler)
    app.router.add_get("/terminal", terminal_handler)
    app.router.add_get("/install", openclaw_cli_installer_handler)
    app.router.add_get("/install-remote", openclaw_cli_remote_installer_handler)
    app.router.add_get("/downloads/openclaw_cli.py", openclaw_cli_download_handler)
    app.router.add_get("/downloads/openclaw-cli-support/{name}", openclaw_cli_support_download_handler)
    app.router.add_get("/downloads/openclaw-cli-installer.sh", openclaw_cli_installer_handler)

    app.router.add_get("/api/dashboard", api_dashboard_handler)
    app.router.add_get("/api/runs", api_runs_handler)
    app.router.add_get("/api/quality-evals", api_quality_eval_handler)
    app.router.add_get("/api/quality-metrics", api_quality_metrics_handler)
    app.router.add_get("/api/memories", api_memories_handler)
    app.router.add_get("/api/channel-memory/inspect", api_channel_memory_inspect_handler)
    app.router.add_post("/api/channel-memory/action", action(api_channel_memory_action_handler))
    app.router.add_get("/api/channel-profile/recommendations", api_channel_profile_recommendations_handler)
    app.router.add_post(
        "/api/channel-profile/recommendations/action",
        action(api_channel_profile_recommendation_action_handler),
    )
    app.router.add_get("/api/approvals", api_approvals_handler)
    app.router.add_post("/api/approvals/{request_id}/decision", action(api_approval_decision_handler))
    app.router.add_get("/api/agent/sessions", api_agent_sessions_handler)
    app.router.add_get("/api/agent/sessions/{session_id}", api_agent_session_detail_handler)
    app.router.add_post(
        "/api/agent/sessions/{session_id}/interventions/{action}",
        action(api_agent_session_intervention_handler),
    )
    app.router.add_get("/api/plans", api_plans_handler)
    app.router.add_get("/api/plans/{plan_id}", api_plan_detail_handler)
    app.router.add_get("/api/tasks", api_task_status_handler)
    app.router.add_get("/api/tasks/{source}/{task_id}", api_task_status_detail_handler)
    app.router.add_get("/api/threads", api_threads_handler)
    app.router.add_get("/api/goals", api_goals_handler)
    app.router.add_get("/api/research", api_research_handler)
    app.router.add_get("/api/schedules", api_schedules_handler)
    app.router.add_post("/api/schedules/{task_id}", action(api_schedule_update_handler))
    app.router.add_post("/api/schedules/{task_id}/toggle", action(api_schedule_toggle_handler))
    app.router.add_delete("/api/schedules/{task_id}", action(api_schedule_delete_handler))
    app.router.add_get("/api/status", api_status_handler)
    app.router.add_post("/api/agent/ask/stream", api_agent_ask_stream_handler)
    app.router.add_get("/api/errors", api_errors_handler)
    app.router.add_get("/api/response-stats", api_response_stats_handler)
    app.router.add_get("/api/dream-health", api_dream_health_handler)
    app.router.add_get("/api/config-status", api_config_status_handler)
    app.router.add_get("/api/search-stats", api_search_stats_handler)
    app.router.add_get("/api/quota-status", api_quota_status_handler)
    app.router.add_get("/api/skill-stats", api_skill_stats_handler)
    app.router.add_get("/api/knowledge-graph", api_knowledge_graph_handler)
    app.router.add_get("/api/topology", api_topology_handler)
    app.router.add_get("/api/sms/settings", api_sms_settings_handler)
    app.router.add_post("/api/sms/settings", action(api_sms_settings_handler))
    app.router.add_get("/api/sms/status", api_sms_status_handler)
    app.router.add_get("/api/sms/history", api_sms_history_handler)

    # Agent interaction
    app.router.add_post("/api/agent/ask", action(api_agent_ask_handler))
    app.router.add_post("/api/recap/generate", action(api_recap_generate_handler))
