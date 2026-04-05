"""Route registration for the dashboard package."""

from aiohttp import web

from .api_handlers import (
    api_channel_memory_action_handler,
    api_channel_memory_inspect_handler,
    api_config_status_handler,
    api_dashboard_handler,
    api_dream_health_handler,
    api_errors_handler,
    api_goals_handler,
    api_knowledge_graph_handler,
    api_memories_handler,
    api_quota_status_handler,
    api_research_handler,
    api_response_stats_handler,
    api_schedule_delete_handler,
    api_schedules_handler,
    api_search_stats_handler,
    api_skill_stats_handler,
    api_sms_history_handler,
    api_sms_settings_handler,
    api_sms_status_handler,
    api_status_handler,
    api_threads_handler,
    api_topology_handler,
)
from .html_handlers import dashboard_handler, guide_handler, terminal_handler


def setup_dashboard(app: web.Application) -> None:
    """Register all dashboard routes on the given aiohttp application."""
    app.router.add_get("/", dashboard_handler)
    app.router.add_get("/dashboard", dashboard_handler)
    app.router.add_get("/guide", guide_handler)
    app.router.add_get("/terminal", terminal_handler)

    app.router.add_get("/api/dashboard", api_dashboard_handler)
    app.router.add_get("/api/memories", api_memories_handler)
    app.router.add_get("/api/channel-memory/inspect", api_channel_memory_inspect_handler)
    app.router.add_post("/api/channel-memory/action", api_channel_memory_action_handler)
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
    app.router.add_get("/api/quota-status", api_quota_status_handler)
    app.router.add_get("/api/skill-stats", api_skill_stats_handler)
    app.router.add_get("/api/knowledge-graph", api_knowledge_graph_handler)
    app.router.add_get("/api/topology", api_topology_handler)
    app.router.add_get("/api/sms/settings", api_sms_settings_handler)
    app.router.add_post("/api/sms/settings", api_sms_settings_handler)
    app.router.add_get("/api/sms/status", api_sms_status_handler)
    app.router.add_get("/api/sms/history", api_sms_history_handler)
