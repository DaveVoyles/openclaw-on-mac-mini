"""Route registration for the dashboard package."""

from collections.abc import Awaitable, Callable

from aiohttp import web

from .auth import login_api_handler, logout_handler
from .api_handlers import (
    api_agent_ask_handler,
    api_agent_ask_stream_handler,
    api_copilot_ping_handler,
    api_copilot_run_handler,
    api_copilot_sessions_handler,
    api_copilot_stream_handler,
    api_hermes_status_handler,
    api_hermes_memory_get_handler,
    api_hermes_memory_post_handler,
    api_hermes_memory_seed_handler,
    api_hermes_skills_seed_handler,
    api_hermes_sessions_handler,
    api_hermes_session_detail_handler,
    api_hermes_session_messages_handler,
    api_hermes_ask_handler,
    api_hermes_upgrade_handler,
    api_nas_browse_handler,
    api_nas_disk_handler,
    api_nas_status_handler,
    api_audit_recent_handler,
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
    api_docker_action_handler,
    api_docker_logs_handler,
    api_docker_status_handler,
    api_dream_health_handler,
    api_errors_handler,
    api_goals_handler,
    api_github_activity_handler,
    api_tautulli_activity_handler,
    api_tautulli_history_handler,
    api_arr_queue_handler,
    api_arr_history_handler,
    api_sabnzbd_queue_handler,
    api_qbt_status_handler,
    api_overseerr_recent_handler,
    api_overseerr_search_handler,
    api_overseerr_request_handler,
    api_sonarr_calendar_handler,
    api_webhook_sonarr_handler,
    api_webhook_radarr_handler,
    api_knowledge_graph_handler,
    api_memories_handler,
    api_manifest_handler,
    api_network_ping_handler,
    api_network_wol_handler,
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
    api_system_alerts_handler,
    api_system_health_handler,
    api_system_timemachine_handler,
    api_tailscale_status_handler,
    api_uptime_kuma_handler,
    api_task_status_detail_handler,
    api_task_status_handler,
    api_threads_handler,
    api_topology_handler,
    api_v1_models_handler,
    api_v1_chat_completions_handler,
    api_tools_openapi_handler,
    api_tools_search_files_handler,
    api_tools_read_file_handler,
    api_tools_run_shell_handler,
    api_tools_share_file_handler,
    api_changelog_handler,
    api_hermes_skills_handler,
)
from .html_handlers import (
    dashboard_handler,
    guide_handler,
    login_handler,
    onboarding_handler,
    openclaw_cli_download_handler,
    openclaw_cli_installer_handler,
    openclaw_cli_remote_installer_handler,
    openclaw_cli_support_download_handler,
    openclaw_cli_windows_installer_handler,
    hermes_installer_handler,
    parents_guide_handler,
    terminal_handler,
    webui_guide_handler,
)


def setup_dashboard(
    app: web.Application,
    *,
    require_action_auth: Callable[[web.Request], web.Response | None] | None = None,
    require_session: Callable[[Callable], Callable] | None = None,
) -> None:
    """Register all dashboard routes on the given aiohttp application.

    Args:
        app: aiohttp Application instance
        require_action_auth: Optional callback to auth API write actions
        require_session: Optional decorator to require valid session for page routes
    """

    def action(handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
        if require_action_auth is None:
            return handler

        async def _authed(request: web.Request) -> web.StreamResponse:
            auth_error = require_action_auth(request)
            if auth_error is not None:
                return auth_error
            return await handler(request)

        return _authed

    def page(handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
        """Wrap page handler with session auth if available."""
        if require_session is None:
            return handler
        return require_session(handler)

    app.router.add_get("/dashboard", page(dashboard_handler))
    app.router.add_get("/manifest.json", api_manifest_handler)
    app.router.add_get("/login", login_handler)
    app.router.add_post("/api/login", login_api_handler)
    app.router.add_get("/api/logout", logout_handler)
    app.router.add_post("/api/logout", logout_handler)
    app.router.add_get("/tech-guide", page(guide_handler))
    app.router.add_get("/guide", lambda r: web.HTTPMovedPermanently("/tech-guide"))
    app.router.add_get("/terminal", page(terminal_handler))
    app.router.add_get("/onboarding", page(onboarding_handler))
    app.router.add_get("/parents-guide", page(parents_guide_handler))
    app.router.add_get("/webui-guide", page(webui_guide_handler))
    app.router.add_get("/install", page(openclaw_cli_installer_handler))
    app.router.add_get("/install-remote", page(openclaw_cli_remote_installer_handler))
    app.router.add_get("/install.ps1", page(openclaw_cli_windows_installer_handler))
    app.router.add_get("/install-hermes", page(hermes_installer_handler))
    app.router.add_get("/ih", page(hermes_installer_handler))  # short alias for single-line terminal use
    app.router.add_get("/downloads/openclaw_cli.py", page(openclaw_cli_download_handler))
    app.router.add_get("/downloads/openclaw-cli-support/{name}", page(openclaw_cli_support_download_handler))
    app.router.add_get("/downloads/openclaw-cli-installer.sh", page(openclaw_cli_installer_handler))

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
    app.router.add_get("/api/github/activity", api_github_activity_handler)
    app.router.add_get("/api/tautulli/activity", api_tautulli_activity_handler)
    app.router.add_get("/api/tautulli/history", api_tautulli_history_handler)
    app.router.add_get("/api/arr/queue", api_arr_queue_handler)
    app.router.add_get("/api/arr/history", api_arr_history_handler)
    app.router.add_get("/api/research", api_research_handler)
    app.router.add_get("/api/schedules", api_schedules_handler)
    app.router.add_post("/api/schedules/{task_id}", action(api_schedule_update_handler))
    app.router.add_post("/api/schedules/{task_id}/toggle", action(api_schedule_toggle_handler))
    app.router.add_delete("/api/schedules/{task_id}", action(api_schedule_delete_handler))
    app.router.add_get("/api/status", api_status_handler)
    app.router.add_post("/api/agent/ask/stream", action(api_agent_ask_stream_handler))
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

    # Docker management
    app.router.add_post("/api/docker/action", action(api_docker_action_handler))
    app.router.add_get("/api/docker/logs", api_docker_logs_handler)

    # Agent interaction
    app.router.add_post("/api/agent/ask", action(api_agent_ask_handler))
    app.router.add_post("/api/copilot/ping", action(api_copilot_ping_handler))
    app.router.add_get("/api/copilot/sessions", api_copilot_sessions_handler)
    app.router.add_get("/api/hermes/status", api_hermes_status_handler)
    app.router.add_get("/api/hermes/memory", api_hermes_memory_get_handler)
    app.router.add_post("/api/hermes/memory", action(api_hermes_memory_post_handler))
    app.router.add_get("/api/hermes/memory-seed", api_hermes_memory_seed_handler)
    app.router.add_get("/api/hermes/skills-seed", api_hermes_skills_seed_handler)
    app.router.add_get("/api/hermes/sessions", api_hermes_sessions_handler)
    app.router.add_get("/api/hermes/sessions/{session_id}", api_hermes_session_detail_handler)
    app.router.add_get("/api/hermes/sessions/{session_id}/messages", api_hermes_session_messages_handler)
    app.router.add_post("/api/hermes/ask", action(api_hermes_ask_handler))
    app.router.add_get("/api/nas/disk", api_nas_disk_handler)
    app.router.add_post("/api/copilot/run", action(api_copilot_run_handler))
    app.router.add_post("/api/copilot/stream", action(api_copilot_stream_handler))
    app.router.add_post("/api/recap/generate", action(api_recap_generate_handler))

    # OpenAI-compatible API for Open WebUI / external clients
    app.router.add_get("/v1/models", api_v1_models_handler)
    app.router.add_post("/v1/chat/completions", api_v1_chat_completions_handler)

    # Tool Server — OpenAPI-compatible endpoints for Open WebUI tool calling
    # Configure in Open WebUI: Admin → Tools → Tool Servers → http://openclaw:8765
    app.router.add_get("/tools/openapi.json", api_tools_openapi_handler)
    app.router.add_post("/tools/search_files", action(api_tools_search_files_handler))
    app.router.add_post("/tools/read_file", action(api_tools_read_file_handler))
    app.router.add_post("/tools/run_shell", action(api_tools_run_shell_handler))
    app.router.add_post("/tools/share_file", action(api_tools_share_file_handler))
    app.router.add_get("/api/changelog", api_changelog_handler)
    app.router.add_get("/api/hermes/skills", api_hermes_skills_handler)
    app.router.add_get("/api/docker/status", api_docker_status_handler)
    app.router.add_get("/api/network/ping", api_network_ping_handler)
    app.router.add_get("/api/network/wol", api_network_wol_handler)
    app.router.add_post("/api/network/wol", action(api_network_wol_handler))
    app.router.add_get("/api/tailscale/status", api_tailscale_status_handler)
    app.router.add_get("/api/uptime/status", api_uptime_kuma_handler)
    app.router.add_get("/api/system/alerts", api_system_alerts_handler)
    app.router.add_get("/api/system/health", api_system_health_handler)
    app.router.add_get("/api/system/timemachine", api_system_timemachine_handler)
    app.router.add_get("/api/sabnzbd/queue", api_sabnzbd_queue_handler)
    app.router.add_get("/api/qbt/status", api_qbt_status_handler)
    app.router.add_post("/api/hermes/upgrade", action(api_hermes_upgrade_handler))
    app.router.add_get("/api/nas/browse", api_nas_browse_handler)
    app.router.add_get("/api/overseerr/recent", api_overseerr_recent_handler)
    app.router.add_get("/api/overseerr/search", api_overseerr_search_handler)
    app.router.add_post("/api/overseerr/request", action(api_overseerr_request_handler))
    app.router.add_get("/api/sonarr/calendar", api_sonarr_calendar_handler)
    app.router.add_get("/api/audit/recent", api_audit_recent_handler)
    app.router.add_get("/api/nas/status", api_nas_status_handler)
    app.router.add_post("/api/webhooks/sonarr", api_webhook_sonarr_handler)
    app.router.add_post("/api/webhooks/radarr", api_webhook_radarr_handler)
