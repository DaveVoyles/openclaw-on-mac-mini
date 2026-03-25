"""
OpenClaw LLM Integration — Phase 5: Gemini + Function Calling
Manages the Gemini API connection, tool declarations, and chat sessions.

Hybrid routing:
  - Simple / conversational queries → Ollama (local, free, fast)
  - Anything requiring tool/function calls  → Gemini 2.0 Flash
  - Ollama unavailable or LOCAL_LLM_ENABLED=false → Gemini for everything
"""

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import aiohttp
import google.generativeai as genai

from skills import SKILLS
from spending import tracker as spending_tracker

log = logging.getLogger("openclaw.llm")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
MODEL_NAME = os.getenv("LLM_MODEL", "gemini-2.5-flash")
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))

# Local LLM (Ollama) settings
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:12b")
LOCAL_LLM_ENABLED = os.getenv("LOCAL_LLM_ENABLED", "true").lower() == "true"

# Deep / thinking mode — used for /research and multi-step synthesis
# Set to a thinking-capable model (e.g. gemini-2.5-flash or gemini-2.0-flash-thinking-exp)
THINKING_MODEL = os.getenv("THINKING_MODEL", "gemini-2.5-flash")
THINKING_BUDGET = int(os.getenv("THINKING_BUDGET", "8000"))  # tokens for reasoning

# Rate limits (paid tier: 1000 RPM Flash, 50 RPM Pro)
MAX_CALLS_PER_MINUTE = int(os.getenv("LLM_RPM_LIMIT", "60"))
MAX_CALLS_PER_HOUR = int(os.getenv("LLM_RPH_LIMIT", "500"))

# Function-call loop limit (prevent infinite tool invocations)
MAX_TOOL_ROUNDS = 12

# ---------------------------------------------------------------------------
# System prompt (cached with mtime-based invalidation)
# ---------------------------------------------------------------------------

_system_prompt_cache: str | None = None
_system_prompt_mtime: float = 0.0


def _load_system_prompt() -> str:
    """Load the system prompt from config/prompts/system.txt with mtime cache."""
    global _system_prompt_cache, _system_prompt_mtime
    prompt_file = CONFIG_DIR / "prompts" / "system.txt"
    try:
        current_mtime = prompt_file.stat().st_mtime if prompt_file.exists() else 0.0
    except OSError:
        current_mtime = 0.0
    if _system_prompt_cache is not None and current_mtime == _system_prompt_mtime:
        return _system_prompt_cache
    if prompt_file.exists():
        _system_prompt_cache = prompt_file.read_text().strip()
    else:
        _system_prompt_cache = (
            "You are OpenClaw, a helpful AI assistant managing a home media server. "
            "Be concise, professional, and use emojis sparingly."
        )
    _system_prompt_mtime = current_mtime
    return _system_prompt_cache


# ---------------------------------------------------------------------------
# Tool / function declarations for Gemini
# ---------------------------------------------------------------------------

# Map skill names → Gemini FunctionDeclarations
_TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "list_containers",
        "description": "List all running Docker containers with name, status, and ports.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_container_status",
        "description": "Get detailed status, resource usage, and port mapping for a specific Docker container.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Container name (e.g. sonarr, radarr, plex, sabnzbd)",
                },
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_container_logs",
        "description": "Retrieve the last N lines of logs from a Docker container.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Container name",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of log lines to retrieve (5-100, default 30)",
                },
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_docker_stats",
        "description": "Get CPU, memory, and network usage for all running containers.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_system_stats",
        "description": "Get Mac Mini system resource usage (CPU, memory, disk).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_uptime",
        "description": "Get system uptime of the Mac Mini.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_compose_config",
        "description": (
            "Read the Docker Compose configuration file and return port mappings, volume mounts, "
            "and environment variable names for all services (or a specific one). "
            "Use this when troubleshooting permission errors, port conflicts, or when you need "
            "to know exactly how a container is configured."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Optional service name to filter results (e.g. 'sonarr', 'plex'). Leave empty for all services.",
                },
            },
        },
    },
    # -- Phase 5: Advanced Skills --
    {
        "name": "check_arr_health",
        "description": "Check health status of all *arr services (Sonarr, Radarr, Lidarr, Prowlarr).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "check_download_clients",
        "description": "Check connectivity of download clients (SABnzbd and qBittorrent).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "check_plex_status",
        "description": "Check Plex server status and version via Tautulli.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_plex_activity",
        "description": (
            "Get real-time Plex activity: who is currently watching, what they're watching, "
            "playback progress, video quality, and whether the stream is direct play or transcode. "
            "Use this when the user asks 'what's playing on Plex', 'is anyone watching?', "
            "'who's using Plex right now', or similar."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "search_media",
        "description": "Search for TV shows or movies across Sonarr and Radarr catalogs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term (e.g. 'Breaking Bad', 'The Matrix')",
                },
                "media_type": {
                    "type": "string",
                    "description": "Type filter: 'tv', 'movie', or 'all' (default: all)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_download_queue",
        "description": "Get active downloads from SABnzbd (Usenet) and qBittorrent (torrents).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_recent_additions",
        "description": "Get recently added media from Plex (via Tautulli).",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent items to return (1-25, default 10)",
                },
            },
        },
    },
    {
        "name": "ping_host",
        "description": "Ping a hostname or IP to check connectivity and latency.",
        "parameters": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "Hostname or IP address to ping",
                },
            },
            "required": ["host"],
        },
    },
    {
        "name": "check_service_ports",
        "description": "Check if all key services are listening on their expected ports.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "create_status_report",
        "description": "Generate a comprehensive system status report covering all services, downloads, and Plex.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "analyze_logs",
        "description": "Analyze container logs using AI to identify errors, warnings, and suggest fixes.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Container name to analyze logs for",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of log lines to analyze (10-200, default 50)",
                },
            },
            "required": ["service"],
        },
    },
    # -- Phase 5: Augmented Skills (QMD, AgentMail) --
    {
        "name": "remember_fact",
        "description": "Store a fact or piece of information in the long-term memory (QMD).",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact or information to remember",
                },
                "tags": {
                    "type": "string",
                    "description": "Optional comma-separated list of tags to associate with this memory",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "recall_fact",
        "description": "Search long-term memory (QMD) for a specific fact or information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords or topic to search for",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_memories",
        "description": "List all facts stored in the long-term memory (QMD).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "send_agent_mail",
        "description": "Send an automated e-mail message to a single recipient.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "The recipient's e-mail address",
                },
                "subject": {
                    "type": "string",
                    "description": "The subject of the e-mail",
                },
                "body": {
                    "type": "string",
                    "description": "The message body (plain text)",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    # -- Phase 6: Network & Remote Access --
    {
        "name": "get_network_status",
        "description": "Check LAN, internet, DNS, and Tailscale VPN connectivity and return a full status summary.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_tailscale_status",
        "description": "Show Tailscale VPN status and this device's Tailscale IP address.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "run_speed_test",
        "description": "Run a brief network speed test and return measured download speed and DNS latency.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    # -- Spending Tracker --
    {
        "name": "get_spending",
        "description": "Get current Gemini API spending summary including total cost, budget remaining, and token usage.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_daily_spending",
        "description": "Get daily spending breakdown for the last N days.",
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to show (1-30, default 7)",
                },
            },
        },
    },
    # -- Phase 6: Overseerr Media Requests --
    {
        "name": "get_pending_requests",
        "description": "List all pending media requests awaiting approval in Overseerr.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "approve_request",
        "description": "Approve a pending Overseerr media request by its numeric ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "integer",
                    "description": "The numeric ID of the request to approve (from get_pending_requests)",
                },
            },
            "required": ["request_id"],
        },
    },
    {
        "name": "deny_request",
        "description": "Decline a pending Overseerr media request by its numeric ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "integer",
                    "description": "The numeric ID of the request to decline (from get_pending_requests)",
                },
            },
            "required": ["request_id"],
        },
    },
    {
        "name": "get_request_stats",
        "description": "Get a summary count of all Overseerr media requests by status (pending, approved, available, etc.).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    # -- Phase 6: Synology NAS --
    {
        "name": "get_nas_storage_health",
        "description": "Get Synology NAS storage volume and disk health status.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_backup_status",
        "description": "Get Synology Hyper Backup task status and last run time.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_nas_alerts",
        "description": "Get Synology DSM system health alerts (fans, temperature, power, disk warnings).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_disk_smart_status",
        "description": "Get SMART health status for all physical disks in the Synology NAS.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    # -- Phase 6: Email (Gmail / Outlook) --
    {
        "name": "read_inbox",
        "description": "Read the most recent emails from a Gmail or Outlook inbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Email provider: 'gmail' or 'outlook' (default: gmail)",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of recent messages to return (1-25, default 10)",
                },
            },
        },
    },
    {
        "name": "search_emails",
        "description": "Search for emails by keyword in the Gmail or Outlook inbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword or phrase to search for in email subjects and bodies",
                },
                "provider": {
                    "type": "string",
                    "description": "Email provider: 'gmail' or 'outlook' (default: gmail)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "send_email",
        "description": "Send an email via Gmail or Outlook.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line",
                },
                "body": {
                    "type": "string",
                    "description": "Email body (plain text)",
                },
                "provider": {
                    "type": "string",
                    "description": "Email provider: 'gmail' or 'outlook' (default: gmail)",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    # -- Phase 6: Google Calendar --
    {
        "name": "get_upcoming_events",
        "description": "Get Google Calendar events scheduled in the next N days.",
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days ahead to look (1-30, default 7)",
                },
            },
        },
    },
    {
        "name": "create_calendar_event",
        "description": "Create a Google Calendar event with a title, start/end time, and optional description.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Event title",
                },
                "start_time": {
                    "type": "string",
                    "description": "Start time in ISO 8601 format (e.g. '2026-03-25T14:00:00' or '2026-03-25' for all-day)",
                },
                "end_time": {
                    "type": "string",
                    "description": "End time in ISO 8601 format (for all-day, use the next day's date)",
                },
                "description": {
                    "type": "string",
                    "description": "Optional event notes or description",
                },
            },
            "required": ["summary", "start_time", "end_time"],
        },
    },
    {
        "name": "get_todays_events",
        "description": "Get all Google Calendar events scheduled for today.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    # -- Phase 8: Web Search & Browsing --
    {
        "name": "search_web",
        "description": "Search the live web for current information using Tavily AI Search (with DuckDuckGo and Bing fallbacks). Use when the user asks about news, current events, facts, documentation, real estate listings, weather, or anything that requires up-to-date information from the internet.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query (be specific for better results)",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return (1-10, default 5)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_weather",
        "description": "Get current weather conditions and a 3-day forecast for any location (city, airport code, or landmark). No API key required.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name, airport code, or landmark (e.g. 'Philadelphia PA', 'JFK', 'Narberth PA'). Leave empty to use the default configured location.",
                },
                "units": {
                    "type": "string",
                    "description": "Unit system: 'uscs' for Fahrenheit/mph (default) or 'metric' for Celsius/kmh",
                },
            },
        },
    },
    {
        "name": "browse_url",
        "description": "Fetch and read the content of a specific web page or URL. Use when the user provides a URL to read, or after a web search when you want to get the full content of a result.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch (must start with http:// or https://)",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "webfetch_md",
        "description": "Smartly scrape and fetch any URL, converting it into clean Markdown (optimized for AI reading). Use this as a more robust alternative to browse_url.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "git_status",
        "description": "Check the project's Git status, showing which files are staged, unstaged, or untracked.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "git_log",
        "description": "View recent commit history for the project codebase.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of commits to return (default 5)",
                },
            },
        },
    },
    {
        "name": "git_diff",
        "description": "Compare code changes between current state and previous commits.",
        "parameters": {
            "type": "object",
            "properties": {
                "staged": {
                    "type": "boolean",
                    "description": "If true, show staged changes; if false, show unstaged changes.",
                },
            },
        },
    },
    {
        "name": "git_commit",
        "description": "Commit all current changes with a brief descriptive message.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The commit message summarizing the work done",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "init_planning_files",
        "description": "Initialize Manus-style file-based planning (task_plan.md, findings.md, progress.md) in the project workspace. Use this for ANY multi-step or complex task to ensure the agent maintains state and context.",
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "A clear, one-sentence description of the end goal of the task.",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "update_plan_status",
        "description": "Log progress or update status of a phase in the current planning files (progress.md). Use this after completing significant steps or hitting errors to record findings.",
        "parameters": {
            "type": "object",
            "properties": {
                "phase": {
                    "type": "integer",
                    "description": "The current phase number being worked on.",
                },
                "status": {
                    "type": "string",
                    "description": "Updated status summary (e.g., 'complete', 'in progress', 'error encountered').",
                },
                "note": {
                    "type": "string",
                    "description": "Optional detailed progress note or error report.",
                },
            },
            "required": ["phase", "status"],
        },
    },
    # restart_container is intentionally EXCLUDED from LLM tool access.
    # The LLM can suggest a restart, but it must go through the /restart command
    # with proper authorization and policy checks.

    # -- Maton API Gateway (managed OAuth proxy to 100+ third-party APIs) --
    {
        "name": "gateway_request",
        "description": (
            "Call any third-party API (Slack, GitHub, Google Sheets, Notion, HubSpot, "
            "Stripe, Airtable, and 100+ more) through the Maton managed OAuth gateway. "
            "Requires an active Maton connection for the target app."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": (
                        "Service slug, e.g. 'slack', 'github', 'google-sheets', "
                        "'notion', 'hubspot', 'stripe', 'airtable'."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Native API path without the app prefix, e.g. "
                        "'api/chat.postMessage' for Slack, "
                        "'repos/owner/repo/issues' for GitHub."
                    ),
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method: GET, POST, PUT, PATCH, or DELETE (default: GET).",
                },
                "body": {
                    "type": "string",
                    "description": "Optional JSON-encoded request body as a string.",
                },
                "connection_id": {
                    "type": "string",
                    "description": "Optional connection UUID if you have multiple connections for the same app.",
                },
            },
            "required": ["app", "path"],
        },
    },
    {
        "name": "gateway_list_connections",
        "description": "List all active Maton OAuth connections, optionally filtered by app name.",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "Optional service name to filter (e.g. 'slack'). Leave empty for all.",
                },
            },
        },
    },
    {
        "name": "gateway_create_connection",
        "description": (
            "Create a new Maton OAuth connection for a third-party app. "
            "Returns a URL for the user to open in a browser to complete OAuth."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "Service to connect, e.g. 'slack', 'github', 'google-calendar', 'notion'.",
                },
            },
            "required": ["app"],
        },
    },
    # -- File Creation --
    {
        "name": "nas_create_folder",
        "description": "Create a new folder on the Synology NAS FileStation.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Full folder path to create, e.g. '/volume1/documents/reports'.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "nas_write_file",
        "description": (
            "Write a text or markdown file directly to the Synology NAS. "
            "Use this to save research reports, notes, or any generated content to network storage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Full text content to write to the file.",
                },
                "remote_folder": {
                    "type": "string",
                    "description": "Destination folder on the NAS, e.g. '/volume1/documents'. Default: '/volume1/documents'.",
                },
                "filename": {
                    "type": "string",
                    "description": "File name including extension, e.g. 'research_report.md'. Default: 'openclaw_output.md'.",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "create_google_doc",
        "description": (
            "Create a Google Doc with a title and text content. "
            "Requires a 'google-docs' connection via Maton. "
            "Returns the document URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title for the new Google Doc.",
                },
                "content": {
                    "type": "string",
                    "description": "Full text content to insert into the document.",
                },
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "create_onedrive_file",
        "description": (
            "Save a text or markdown file to OneDrive. "
            "Requires a 'microsoft-onedrive' connection via Maton. "
            "Returns the file URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "File name including extension, e.g. 'report.md'.",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write to the file.",
                },
                "folder_path": {
                    "type": "string",
                    "description": "OneDrive folder path (default: 'OpenClaw'). Use '/' for root.",
                },
            },
            "required": ["filename", "content"],
        },
    },
    # Ontology - structured graph memory
    {
        "name": "ontology_create_entity",
        "description": "Create a structured ontology entity in the local graph memory. Use for people, projects, tasks, events, documents, devices, and durable facts that should be linked and queried later.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": "Entity type, e.g. Person, Project, Task, Event, Document, Note, Device.",
                },
                "properties_json": {
                    "type": "string",
                    "description": "Entity properties as a JSON object string, e.g. '{\"name\":\"Alice\"}' or '{\"title\":\"Fix parser\",\"status\":\"open\"}'.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Optional explicit entity ID. Leave empty to auto-generate.",
                },
            },
            "required": ["entity_type", "properties_json"],
        },
    },
    {
        "name": "ontology_get_entity",
        "description": "Fetch a single ontology entity by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Entity ID to fetch.",
                },
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "ontology_query",
        "description": "Query ontology entities by type and property filters. Use to answer 'what do we know about X', list project tasks, or find structured records.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": "Optional entity type filter, e.g. Task or Project.",
                },
                "where_json": {
                    "type": "string",
                    "description": "Optional JSON filter on entity properties, e.g. '{\"status\":\"open\"}'.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "ontology_update_entity",
        "description": "Update properties on an existing ontology entity.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Entity ID to update.",
                },
                "properties_json": {
                    "type": "string",
                    "description": "Properties to merge into the entity as a JSON object string.",
                },
            },
            "required": ["entity_id", "properties_json"],
        },
    },
    {
        "name": "ontology_relate",
        "description": "Create a typed relation between two ontology entities, such as project->has_task->task or task->blocks->task.",
        "parameters": {
            "type": "object",
            "properties": {
                "from_id": {
                    "type": "string",
                    "description": "Source entity ID.",
                },
                "relation": {
                    "type": "string",
                    "description": "Relation type, e.g. has_task, has_owner, blocks, for_event.",
                },
                "to_id": {
                    "type": "string",
                    "description": "Target entity ID.",
                },
                "properties_json": {
                    "type": "string",
                    "description": "Optional relation properties as a JSON object string.",
                },
            },
            "required": ["from_id", "relation", "to_id"],
        },
    },
    {
        "name": "ontology_get_related",
        "description": "Get entities related to a given ontology entity. Use for dependency tracing, project membership, ownership, and graph traversal.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Entity ID to traverse from.",
                },
                "relation": {
                    "type": "string",
                    "description": "Optional relation filter.",
                },
                "direction": {
                    "type": "string",
                    "description": "Traversal direction: outgoing, incoming, or both. Default both.",
                },
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "ontology_validate",
        "description": "Validate the ontology graph against the local schema and report any structural errors.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    # Mission Control
    {
        "name": "get_mission_tasks",
        "description": "List Mission Control Kanban tasks. Optionally filter by status: backlog, in_progress, review, done, permanent.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status: backlog, in_progress, review, done, permanent. Omit for all tasks.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_task_detail",
        "description": "Get full details for a specific Mission Control task including subtasks and comments.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID, e.g. 'task_001'."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "update_task_status",
        "description": "Update a Mission Control task's status.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID."},
                "new_status": {"type": "string", "description": "New status: backlog, in_progress, review, done."},
            },
            "required": ["task_id", "new_status"],
        },
    },
    {
        "name": "complete_task",
        "description": "Mark a Mission Control task as complete (moves to review) with a summary of what was done.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID."},
                "summary": {"type": "string", "description": "Summary of what was accomplished."},
            },
            "required": ["task_id", "summary"],
        },
    },
    {
        "name": "add_task_comment",
        "description": "Add a comment or progress update to a Mission Control task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID."},
                "comment": {"type": "string", "description": "The comment text."},
            },
            "required": ["task_id", "comment"],
        },
    },
    # -- Autonomous: Worker sub-agent --
    {
        "name": "spawn_worker",
        "description": (
            "Spawn a focused AI sub-agent to accomplish a specific goal autonomously. "
            "The worker runs its own tool loop and returns a clean result. "
            "Use this to delegate complex subtasks that require multiple independent tool calls, "
            "such as gathering data from several sources, doing research and summarizing it, "
            "or performing a multi-step diagnostic while you handle the rest of the response. "
            "Examples: 'check Sonarr health AND search web for that error', "
            "'look up my calendar AND get the weather for tomorrow'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Clear, specific description of what the worker should accomplish.",
                },
                "context": {
                    "type": "string",
                    "description": "Optional background context or constraints for the worker.",
                },
                "max_rounds": {
                    "type": "integer",
                    "description": "Maximum tool call rounds for the worker (1-8, default 6).",
                },
            },
            "required": ["goal"],
        },
    },
    # -- Autonomous: LLM-controlled scheduling --
    {
        "name": "create_scheduled_task",
        "description": (
            "Create a recurring scheduled task that runs a skill automatically. "
            "Use interval_minutes for 'every N minutes' tasks, or hour+minute for a daily cron. "
            "Examples: schedule a daily health check at 07:00, run speed test every 60 min, "
            "check Overseerr requests every 30 min."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "The skill name to schedule (e.g. check_arr_health, run_speed_test).",
                },
                "interval_minutes": {
                    "type": "integer",
                    "description": "Run every N minutes (e.g. 30 for every 30 min). Use 0 for daily cron.",
                },
                "hour": {
                    "type": "integer",
                    "description": "Hour (0-23) for a daily cron schedule. Use -1 if using interval_minutes.",
                },
                "minute": {
                    "type": "integer",
                    "description": "Minute (0-59) for a daily cron schedule (default 0).",
                },
                "args_json": {
                    "type": "string",
                    "description": "Optional JSON object of arguments for the skill, e.g. '{\"days\": 7}'.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional human-readable label describing the task purpose.",
                },
            },
            "required": ["skill_name"],
        },
    },
    {
        "name": "cancel_scheduled_task",
        "description": "Cancel (remove) a scheduled task by its task ID. Use list_scheduled_tasks to find IDs.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to cancel, e.g. 'sched-3'.",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "list_scheduled_tasks",
        "description": "List all active scheduled tasks with their IDs, actions, schedules, and run counts.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    # -- RSS Feed Monitoring --
    {
        "name": "fetch_rss_feed",
        "description": (
            "Fetch recent items from any RSS or Atom feed URL. Use this to read news sources, "
            "tech blogs, GitHub release notes, Reddit feeds, podcast listings, or any site that "
            "publishes an RSS/Atom feed. Returns titles, dates, and summaries."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL of the RSS or Atom feed (e.g. https://feeds.bbci.co.uk/news/rss.xml).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of items to return (1-20, default 10).",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "search_rss",
        "description": "Fetch a feed and filter items matching a keyword or phrase.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full RSS/Atom feed URL.",
                },
                "query": {
                    "type": "string",
                    "description": "Keyword or phrase to search for in titles and summaries.",
                },
            },
            "required": ["url", "query"],
        },
    },
    {
        "name": "get_rss_digest",
        "description": (
            "Fetch multiple RSS/Atom feeds in parallel and synthesize a combined digest. "
            "Use this for a quick news summary across several sources, or to monitor "
            "multiple topics at once. The LLM summarizes the most notable items."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "urls_json": {
                    "type": "string",
                    "description": "JSON array of feed URLs, e.g. '[\"https://news.ycombinator.com/rss\"]'.",
                },
                "topic": {
                    "type": "string",
                    "description": "Optional focus topic — only surface articles related to this subject.",
                },
            },
            "required": ["urls_json"],
        },
    },
    {
        "name": "list_rss_feeds",
        "description": "List all RSS/Atom feed URLs that have been fetched or saved.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    # -- URL Change Monitoring --
    {
        "name": "snapshot_url",
        "description": (
            "Take a baseline snapshot of a URL for change monitoring. "
            "Call this once to record the current content, then schedule "
            "check_url_for_changes to run periodically and alert on differences. "
            "Use for competitor pricing pages, job boards, status pages, listings, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full https URL to monitor.",
                },
                "label": {
                    "type": "string",
                    "description": "Human-readable name for this monitor (e.g. 'Amazon GPU Pricing').",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "check_url_for_changes",
        "description": (
            "Compare the current content of a URL against its stored snapshot. "
            "Returns a change alert with before/after diff if content changed, "
            "or a clean status if unchanged. Designed to be called by the scheduler."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to check (must have been snapshotted with snapshot_url first).",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "list_monitored_urls",
        "description": "List all URLs currently being monitored for content changes, with their last-checked and last-changed timestamps.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "remove_url_monitor",
        "description": "Stop monitoring a URL and remove its snapshot record.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to stop monitoring.",
                },
            },
            "required": ["url"],
        },
    },
    # -- Multi-source comparison --
    {
        "name": "compare_sources",
        "description": (
            "Browse multiple URLs in parallel and synthesize a comparison answer. "
            "Use this for competitive analysis, comparing documentation pages, "
            "fact-checking across multiple sources, or getting a balanced view "
            "of a topic from several sites at once."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "urls_json": {
                    "type": "string",
                    "description": "JSON array of up to 5 URLs to compare, e.g. '[\"https://a.com\",\"https://b.com\"]'.",
                },
                "question": {
                    "type": "string",
                    "description": "The question to answer or aspect to compare across the sources.",
                },
            },
            "required": ["urls_json", "question"],
        },
    },
    # -- Goal decomposition --
    {
        "name": "decompose_goal",
        "description": (
            "Break a complex goal into concrete Mission Control tasks using AI planning. "
            "The agent analyzes the goal, produces an ordered task list, and creates each "
            "task in the Mission Control kanban board for tracking. "
            "Use when the user says 'plan X', 'set up a project for Y', or gives a multi-step goal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Clear description of what needs to be accomplished.",
                },
                "project_name": {
                    "type": "string",
                    "description": "Optional short prefix for task titles (e.g. 'HomeReno', 'Q2Launch').",
                },
            },
            "required": ["goal"],
        },
    },
]

# ---------------------------------------------------------------------------
# Ollama — local LLM for simple / conversational queries
# ---------------------------------------------------------------------------

# Shared aiohttp session for all Ollama requests (avoids per-request TCP handshakes)
_ollama_session: aiohttp.ClientSession | None = None


async def _get_ollama_session() -> aiohttp.ClientSession:
    """Return the shared Ollama aiohttp session, (re)creating if closed."""
    global _ollama_session
    if _ollama_session is None or _ollama_session.closed:
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
        _ollama_session = aiohttp.ClientSession(connector=connector)
    return _ollama_session


# ---------------------------------------------------------------------------
# Routing heuristics — decide whether to use Gemma (local) or Gemini
# ---------------------------------------------------------------------------
import re as _re

# Tier 1 — Route DIRECTLY to Gemini.
# These are imperative action+noun combos that require live tool execution.
# Gemma has no tools, so these would produce hallucinations or refusals.
_LIVE_ACTION_PATTERN = _re.compile(
    # Container / service control verbs
    r"\b(restart|reboot|stop|start|kill)\b.{0,40}\b(container|service|plex|sonarr|radarr|lidarr|sabnzbd|qbittorrent|prowlarr|jellyfin)\b"
    # Requests for live system data
    r"|\b(show|list|get|check|pull|view)\b.{0,40}\b(log|stats?|status|health|container|queue|request|download|backup|alert|metric)\b"
    # Explicit web-search actions
    r"|\b(search|find|look\s+up)\b.{0,40}\b(web|online|house|home|listing|property|zillow|redfin|real[\s-]?estate|news|current\s+price|weather)\b"
    # Weather: any standalone weather request routes through Gemini (needs get_weather tool)
    r"|\b(weather|forecast|temperature|rain|snow|sunny|humidity|wind\s+speed)\b"
    # Live-data questions: "is plex up?", "what's the current…"
    r"|\bis\s+(the\s+)?(server|plex|sonarr|radarr|nas|docker)\s+(up|running|online|working|down)\b"
    r"|\bwhat'?s?\s+(?:the\s+)?(?:current|latest|running)\b.{0,50}\b(status|usage|queue|activity)\b"
    # Approvals, sends, creates
    r"|\b(approve|deny)\b.{0,20}\b(request|id)\b"
    r"|\bsend\b.{0,20}\b(email|mail)\b"
    r"|\bcreate\b.{0,30}\b(task|event|entity|connection|calendar)\b"
    # Diagnostics / jobs
    r"|\brun\b.{0,20}\b(speed\s+test|status\s+report|ping|backup|diagnostic)\b"
    r"|\bping\s+[\w.]+"
    # URLs always need browse_url
    r"|https?://",
    _re.IGNORECASE,
)

# Tier 2 — Well-known domains where Gemma consistently fabricates answers.
# These are proper nouns tied to live services or specialised data sources.
_GEMMA_WEAK_DOMAINS = _re.compile(
    r"\b(zillow|redfin|trulia|narberth|upper\s+darby|maton|tailscale|tautulli"
    r"|overseerr|prowlarr|sabnzbd|synology|hyper\s+backup|ontology)\b",
    _re.IGNORECASE,
)


def _needs_tools(message: str) -> bool:
    """Return True if the query requires live tool execution and should bypass Gemma."""
    return bool(_LIVE_ACTION_PATTERN.search(message) or _GEMMA_WEAK_DOMAINS.search(message))


# Compiled patterns that signal Gemma is pretending to call tools it doesn't have.
# Any match in Gemma's response triggers an automatic fallback to Gemini.
_GEMMA_HALLUCINATION_RE = _re.compile(
    r"(i'?m?\s+)?(now\s+)?(searching|browsing|checking|fetching|looking\s+up)\b"
    r"|\b(let\s+me\s+)?(search|check|look\s+that\s+up|fetch)\s+(that|the|for)\b"
    r"|(checking|querying)\s+(zillow|redfin|the\s+server|docker|container|plex)\b"
    r"|\b(i\s+)?(don'?t|cannot|can'?t)\s+(access|browse|check|reach)\s+(the\s+)?(internet|web|real[\s-]?time|live|current)\b"
    r"|\b(as\s+an?\s+ai|as\s+a\s+language\s+model)\b.{0,80}\b(cannot|don'?t|no\s+access)\b"
    r"|\bi\s+don'?t\s+have\s+(real[\s-]?time|access\s+to|live)\b"
    r"|(would\s+need\s+to\s+|i\s+could\s+)?(search|check|query)\s+(this|that|it)\s+for\s+you\b",
    _re.IGNORECASE,
)


def _gemma_response_seems_valid(reply: str) -> bool:
    """Return True if the Gemma response is genuine and not a tool-use hallucination."""
    if len(reply.strip()) < 10:
        return False
    return not bool(_GEMMA_HALLUCINATION_RE.search(reply))


async def _ollama_available() -> bool:
    """Return True if Ollama is reachable and the model is loaded."""
    try:
        session = await _get_ollama_session()
        async with session.get(
            f"{OLLAMA_URL}/api/tags", timeout=aiohttp.ClientTimeout(total=3)
        ) as resp:
            if resp.status != 200:
                return False
            data = await resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return any(OLLAMA_MODEL.split(":")[0] in m for m in models)
    except Exception:
        return False


async def _chat_ollama(
    user_message: str,
    history: list[dict],
    system_prompt: str,
) -> str | None:
    """
    Send a message to Ollama's /api/chat endpoint.
    Returns the response text, or None on failure.
    """
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history[-10:]:  # keep last 10 turns for context
        role = msg["role"]
        content = " ".join(p for p in msg["parts"] if isinstance(p, str))
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": TEMPERATURE, "num_predict": MAX_TOKENS},
    }

    try:
        session = await _get_ollama_session()
        async with session.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                log.warning("Ollama returned HTTP %d", resp.status)
                return None
            data = await resp.json()
            return data.get("message", {}).get("content") or None
    except asyncio.TimeoutError:
        log.warning("Ollama request timed out")
        return None
    except Exception as e:
        log.warning("Ollama error: %s", e)
        return None


def _build_tools() -> list:
    """Build the Gemini tools list from declarations."""
    return [genai.protos.Tool(function_declarations=[
        genai.protos.FunctionDeclaration(
            name=d["name"],
            description=d["description"],
            parameters=genai.protos.Schema(**_convert_schema(d["parameters"])),
        )
        for d in _TOOL_DECLARATIONS
    ])]


def _convert_schema(schema: dict) -> dict:
    """Convert a JSON-Schema-style dict to Gemini Schema keyword args."""
    type_map = {
        "object": genai.protos.Type.OBJECT,
        "string": genai.protos.Type.STRING,
        "integer": genai.protos.Type.INTEGER,
        "number": genai.protos.Type.NUMBER,
        "boolean": genai.protos.Type.BOOLEAN,
        "array": genai.protos.Type.ARRAY,
    }
    result: dict[str, Any] = {"type_": type_map.get(schema.get("type", "object"), genai.protos.Type.OBJECT)}

    if "properties" in schema:
        result["properties"] = {
            k: genai.protos.Schema(
                type_=type_map.get(v.get("type", "string"), genai.protos.Type.STRING),
                description=v.get("description", ""),
            )
            for k, v in schema["properties"].items()
        }

    if "required" in schema:
        result["required"] = schema["required"]

    return result


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Simple sliding-window rate limiter using a deque for O(1) amortized eviction."""

    def __init__(self, per_minute: int = MAX_CALLS_PER_MINUTE, per_hour: int = MAX_CALLS_PER_HOUR):
        self._per_minute = per_minute
        self._per_hour = per_hour
        self._timestamps: deque[float] = deque()

    def _evict(self) -> None:
        """Drop timestamps older than 1 hour from the front of the deque."""
        cutoff = time.monotonic() - 3600
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def check(self) -> bool:
        """Return True if a call is allowed right now."""
        self._evict()
        now = time.monotonic()
        minute_count = sum(1 for t in self._timestamps if now - t < 60)
        hour_count = len(self._timestamps)
        return minute_count < self._per_minute and hour_count < self._per_hour

    def record(self):
        """Record a call."""
        self._timestamps.append(time.monotonic())

    @property
    def remaining_minute(self) -> int:
        now = time.monotonic()
        used = sum(1 for t in self._timestamps if now - t < 60)
        return max(0, self._per_minute - used)

    @property
    def remaining_hour(self) -> int:
        self._evict()
        return max(0, self._per_hour - len(self._timestamps))


_rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# Tool result TTL cache — avoid redundant calls for read-only snapshot tools
# ---------------------------------------------------------------------------

# Tools whose results are safe to cache within a short window.
# These don't change faster than 30 seconds and are frequently chained together.
_CACHEABLE_TOOLS: frozenset[str] = frozenset({
    "get_system_stats",
    "get_docker_stats",
    "get_nas_storage_health",
    "get_nas_alerts",
    "get_disk_smart_status",
    "get_backup_status",
    "get_uptime",
    "check_arr_health",
    "check_download_clients",
    "check_plex_status",
    "get_plex_activity",
    "get_network_status",
    "get_tailscale_status",
})
_TOOL_CACHE_TTL = 30  # seconds

# {"tool_name|arg_hash": (result, timestamp)}
_tool_cache: dict[str, tuple[str, float]] = {}


def _cache_key(name: str, args: dict) -> str:
    import hashlib
    return f"{name}|{hashlib.md5(str(sorted(args.items())).encode()).hexdigest()[:8]}"


# ---------------------------------------------------------------------------
# Execute a function call from the LLM
# ---------------------------------------------------------------------------


async def _execute_function_call(name: str, args: dict) -> str:
    """Look up and execute a skill by name, returning the string result."""
    skill_fn = SKILLS.get(name)
    if skill_fn is None:
        return f"Unknown function: {name}"

    # Return cached result for read-only snapshot tools if still fresh
    if name in _CACHEABLE_TOOLS:
        key = _cache_key(name, args)
        if key in _tool_cache:
            cached_result, cached_at = _tool_cache[key]
            if time.monotonic() - cached_at < _TOOL_CACHE_TTL:
                log.debug("Returning cached result for %s (age: %.1fs)", name, time.monotonic() - cached_at)
                return cached_result

    log.info("LLM invoking skill: %s(%s)", name, args)
    try:
        result = await skill_fn(**args)
        if name in _CACHEABLE_TOOLS:
            _tool_cache[_cache_key(name, args)] = (result, time.monotonic())
        return result
    except Exception as e:
        log.error("Skill %s failed: %s", name, e)
        return f"Error executing {name}: {e}"


# ---------------------------------------------------------------------------
# Main chat interface
# ---------------------------------------------------------------------------

_model: genai.GenerativeModel | None = None


_model_system_prompt: str | None = None


def _init_gemini_model(
    model_name: str,
    *,
    temperature: float = TEMPERATURE,
    max_tokens: int = MAX_TOKENS,
    thinking_budget: int | None = None,
    with_tools: bool = True,
) -> genai.GenerativeModel:
    """Create a configured GenerativeModel instance (shared factory)."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY not set. Add it to your .env file.")

    genai.configure(api_key=GOOGLE_API_KEY)

    gen_config_kwargs: dict[str, Any] = {
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }

    if thinking_budget is not None:
        thinking_cfg = getattr(genai.types, "ThinkingConfig", None)
        if thinking_cfg is not None:
            gen_config_kwargs["thinking_config"] = thinking_cfg(thinking_budget=thinking_budget)
            log.info("ThinkingConfig enabled (budget=%d tokens)", thinking_budget)
        else:
            log.info("ThinkingConfig not available in this SDK version — using low-temperature deep mode")

    return genai.GenerativeModel(
        model_name=model_name,
        system_instruction=_load_system_prompt(),
        tools=_build_tools() if with_tools else None,
        generation_config=genai.GenerationConfig(**gen_config_kwargs),
    )


def _get_model() -> genai.GenerativeModel:
    """Lazy-init the Gemini model; reloads when system prompt changes."""
    global _model, _model_system_prompt
    system_prompt = _load_system_prompt()
    if _model is not None and _model_system_prompt == system_prompt:
        return _model

    _model = _init_gemini_model(MODEL_NAME, temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
    _model_system_prompt = system_prompt
    log.info("Gemini model initialized: %s (temp=%.1f, max_tokens=%d)", MODEL_NAME, TEMPERATURE, MAX_TOKENS)
    return _model


# ---------------------------------------------------------------------------
# Shared tool-calling loop (used by chat and chat_deep)
# ---------------------------------------------------------------------------

async def _run_tool_loop(
    chat_session,
    response,
    *,
    max_rounds: int = MAX_TOOL_ROUNDS,
    on_tool_call: Any | None = None,
    parallel: bool = True,
    label: str = "LLM",
) -> tuple[Any, int]:
    """Execute the function-call loop on *chat_session*.

    Returns ``(final_response, rounds_executed)``.

    When *parallel* is True (default for normal chat), all function_call
    parts in a single response are gathered concurrently.  When False
    (deep research), only the first function_call part is executed per
    round — matching the sequential research pattern that's easier to
    follow in Discord progress updates.
    """
    loop = asyncio.get_event_loop()
    rounds = 0

    while rounds < max_rounds:
        # Collect function_call parts from this response
        try:
            all_parts = response.candidates[0].content.parts
        except (IndexError, AttributeError):
            break

        function_calls = [
            (part.function_call.name, dict(part.function_call.args) if part.function_call.args else {})
            for part in all_parts
            if hasattr(part, "function_call") and part.function_call.name
        ]

        if not function_calls:
            break

        # In sequential mode, process only the first call per round
        if not parallel:
            function_calls = function_calls[:1]

        log.info("%s function call(s) [round %d]: %s", label, rounds + 1,
                 ", ".join(f"{n}({a})" for n, a in function_calls))

        # Fire progress callbacks
        if on_tool_call:
            for fn_name, _ in function_calls:
                try:
                    await on_tool_call(fn_name, rounds + 1)
                except Exception:
                    pass

        # Execute tool calls
        results = await asyncio.gather(*[
            _execute_function_call(fn_name, fn_args)
            for fn_name, fn_args in function_calls
        ])

        # Rate-limit check before sending results back
        _rate_limiter.record()
        if not _rate_limiter.check():
            # Return partial results as a courtesy message
            partial = "\n".join(results)
            # Build a fake text-only response — caller handles this
            return response, rounds + 1

        # Send all function results back to the model
        response_parts = [
            genai.protos.Part(
                function_response=genai.protos.FunctionResponse(
                    name=fn_name,
                    response={"result": result},
                )
            )
            for (fn_name, _), result in zip(function_calls, results)
        ]

        response = await loop.run_in_executor(
            None,
            lambda parts=response_parts: chat_session.send_message(
                genai.protos.Content(parts=parts)
            ),
        )
        await _record_usage(response)
        rounds += 1

    return response, rounds


async def chat(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
) -> tuple[str, list[dict], str]:
    """
    Send a message and return (response_text, updated_history, model_used).

    ``on_tool_call(tool_name, round_num)`` is an optional async callback invoked
    before each tool execution — used for progressive Discord status updates.

    Routing decision tree:
      1. Does the query need live tool execution? (_needs_tools)
            YES → Gemini directly (function-calling capable)
      2. Is Gemma available and LOCAL_LLM_ENABLED?
            NO  → Gemini
      3. Does Gemma's response pass the hallucination / quality check?
            YES → Return Gemma response (fast, free, private)
            NO  → Silently retry with Gemini
    """
    history = history or []

    # -- History trimming: keep first 2 turns (persona context) + last 18 ------
    # Prevents context overflow on long conversations without losing important
    # early context (e.g. preferences established at the start of a session).
    _MAX_HISTORY_TURNS = 20
    if len(history) > _MAX_HISTORY_TURNS:
        history = history[:2] + history[-((_MAX_HISTORY_TURNS - 2)):]

    # -- Local model (Gemma) path ---------------------------------------------──
    # Use Gemma for conversational queries that don't require live tool calls.
    # Falls through to Gemini if:
    #   • the query pattern requires tools (_needs_tools)
    #   • Gemma is unreachable
    #   • Gemma returns an empty or hallucinated response
    if LOCAL_LLM_ENABLED and not _needs_tools(user_message):
        if await _ollama_available():
            system_prompt = _load_system_prompt()
            gemma_reply = await _chat_ollama(user_message, history, system_prompt)

            if gemma_reply and _gemma_response_seems_valid(gemma_reply):
                log.info("Served by Gemma (%s): %.60s…", OLLAMA_MODEL, user_message)
                updated = list(history) + [
                    {"role": "user", "parts": [user_message]},
                    {"role": "model", "parts": [gemma_reply]},
                ]
                return gemma_reply, updated, OLLAMA_MODEL

            if gemma_reply:
                log.info("Gemma response failed validation (hallucination signals detected), falling back to Gemini")
            else:
                log.info("Gemma returned empty response, falling back to Gemini")
        else:
            log.debug("Gemma/Ollama not reachable, using Gemini")
    # Falls through to Gemini ↓

    # -- Gemini path: rate-limit check with exponential backoff on throttle ---
    _MAX_RETRIES = 3
    _backoff = 2.0  # seconds; doubles per retry
    for _attempt in range(_MAX_RETRIES):
        if _rate_limiter.check():
            break
        if _attempt == _MAX_RETRIES - 1:
            return (
                "⚠️ Rate limit reached. Please wait a moment before asking again. "
                f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)",
                history,
                MODEL_NAME,
            )
        log.info("Rate limit hit, backing off %.1fs (attempt %d/%d)", _backoff, _attempt + 1, _MAX_RETRIES)
        await asyncio.sleep(_backoff)
        _backoff *= 2

    model = _get_model()

    # Build Gemini-compatible history
    gemini_history = []
    for msg in (history or []):
        gemini_history.append(
            genai.types.ContentDict(role=msg["role"], parts=msg["parts"])
        )

    chat_session = model.start_chat(history=gemini_history)

    # Send user message (runs in executor to not block the event loop)
    loop = asyncio.get_event_loop()
    _rate_limiter.record()
    response = await loop.run_in_executor(
        None, lambda: chat_session.send_message(user_message)
    )
    await _record_usage(response)

    # Handle function-call loop (shared implementation)
    response, rounds = await _run_tool_loop(
        chat_session, response,
        max_rounds=MAX_TOOL_ROUNDS,
        on_tool_call=on_tool_call,
        parallel=True,
        label="LLM",
    )

    # Extract final text
    try:
        text = response.text
    except (AttributeError, ValueError):
        try:
            parts = response.candidates[0].content.parts
            text = "".join(p.text for p in parts if hasattr(p, "text") and p.text)
        except Exception:
            text = ""

        if not text:
            if rounds >= MAX_TOOL_ROUNDS:
                log.info("Tool round limit hit with no synthesis — requesting forced summary")
                try:
                    _rate_limiter.record()
                    synthesis_response = await loop.run_in_executor(
                        None,
                        lambda: chat_session.send_message(
                            "You have reached the maximum number of tool calls. "
                            "Please synthesize everything you have gathered so far "
                            "into a final, helpful answer for the user. "
                            "Do not call any more tools."
                        ),
                    )
                    await _record_usage(synthesis_response)
                    text = synthesis_response.text
                except Exception as e:
                    log.error("Forced synthesis failed: %s", e)

            if not text:
                text = "I processed your request but the model returned no text content."
                if hasattr(response, "prompt_feedback") and response.prompt_feedback:
                    text += f" (Safety/Blocked: {response.prompt_feedback})"

    if rounds >= MAX_TOOL_ROUNDS:
        text += f"\n\n⚠️ *Tool call limit reached ({MAX_TOOL_ROUNDS}) — some sources may not have been checked.*"

    # Build updated history
    updated_history = _extract_history(chat_session)

    return text, updated_history, MODEL_NAME


def _extract_history(chat_session) -> list[dict]:
    """Convert a ChatSession's history to our serializable format."""
    history = []
    for content in chat_session.history:
        parts = []
        for part in content.parts:
            if hasattr(part, "text") and part.text:
                parts.append(part.text)
            elif hasattr(part, "function_call") and part.function_call.name:
                parts.append(f"[Called {part.function_call.name}]")
            elif hasattr(part, "function_response") and part.function_response.name:
                parts.append(f"[Result from {part.function_response.name}]")
        if parts:
            history.append({"role": content.role, "parts": parts})
    return history


# ---------------------------------------------------------------------------
# Convenience: check if LLM is configured
# ---------------------------------------------------------------------------


async def close_sessions() -> None:
    """Close all persistent aiohttp sessions. Call on bot shutdown."""
    global _ollama_session
    if _ollama_session is not None and not _ollama_session.closed:
        await _ollama_session.close()
        _ollama_session = None
        log.info("Closed Ollama aiohttp session")


async def _record_usage(response) -> None:
    """Extract usage_metadata from a Gemini response and record spending."""
    try:
        meta = response.usage_metadata
        if meta:
            inp = getattr(meta, "prompt_token_count", 0) or 0
            out = getattr(meta, "candidates_token_count", 0) or 0
            if inp or out:
                await spending_tracker.record(inp, out)
    except Exception as e:
        log.warning("Failed to record token usage: %s", e)


def is_configured() -> bool:
    """Return True if a Google API key is set (Gemini) OR local LLM is enabled."""
    return bool(GOOGLE_API_KEY) or LOCAL_LLM_ENABLED


def get_rate_info() -> str:
    """Return a human-readable rate limit status for Gemini Flash."""
    return f"{_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining"


# ---------------------------------------------------------------------------
# Deep research chat — Gemini with extended thinking (for /research)
# ---------------------------------------------------------------------------

_thinking_model: genai.GenerativeModel | None = None
_thinking_model_prompt: str | None = None


def _get_thinking_model() -> genai.GenerativeModel:
    """Lazy-init the thinking/deep-research variant of the Gemini model."""
    global _thinking_model, _thinking_model_prompt
    system_prompt = _load_system_prompt()
    if _thinking_model is not None and _thinking_model_prompt == system_prompt:
        return _thinking_model

    _thinking_model = _init_gemini_model(
        THINKING_MODEL,
        temperature=0.3,
        max_tokens=MAX_TOKENS * 2,
        thinking_budget=THINKING_BUDGET,
    )
    _thinking_model_prompt = system_prompt
    log.info("Thinking model initialized: %s", THINKING_MODEL)
    return _thinking_model


async def chat_deep(
    user_message: str,
    history: list[dict] | None = None,
    on_tool_call: Any | None = None,
) -> tuple[str, list[dict]]:
    """
    Deep research chat — always uses Gemini with extended thinking.
    Supports a progress callback ``on_tool_call(tool_name, round_num)``
    for streaming progress updates to a Discord thread.

    Returns (response_text, updated_history).
    """
    history = history or []

    if not _rate_limiter.check():
        return (
            "⚠️ Rate limit reached. Please wait a moment.",
            history,
        )

    try:
        model = _get_thinking_model()
    except Exception:
        # Fall back to normal model if thinking config is unsupported
        log.warning("Thinking model unavailable, falling back to standard model")
        model = _get_model()

    gemini_history = [
        genai.types.ContentDict(role=m["role"], parts=m["parts"])
        for m in history
    ]
    chat_session = model.start_chat(history=gemini_history)

    loop = asyncio.get_event_loop()
    _rate_limiter.record()
    response = await loop.run_in_executor(
        None, lambda: chat_session.send_message(user_message)
    )
    await _record_usage(response)

    # Use shared tool loop — sequential mode for deep research
    response, rounds = await _run_tool_loop(
        chat_session, response,
        max_rounds=MAX_TOOL_ROUNDS * 2,
        on_tool_call=on_tool_call,
        parallel=False,
        label="Deep research",
    )

    try:
        text = response.text
    except (AttributeError, ValueError):
        try:
            parts = response.candidates[0].content.parts
            text = "".join(p.text for p in parts if hasattr(p, "text") and p.text)
            if not text:
                text = "Research completed but no text summary was generated."
        except Exception as e:
            text = f"Research completed but summary extraction failed: {e}"

    return text, _extract_history(chat_session)


async def summarize_conversation(history: list[dict]) -> str:
    """
    Produce a 3-5 sentence summary of a conversation history for
    long-term memory storage. Uses the standard Gemini model directly
    (no tools, no conversation context).
    """
    if not GOOGLE_API_KEY or not history:
        return ""

    # Build a compact transcript (user turns only for efficiency)
    lines = []
    for msg in history[-20:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = " ".join(str(p) for p in msg["parts"] if isinstance(p, str))[:200]
        if content:
            lines.append(f"{role}: {content}")

    if not lines:
        return ""

    transcript = "\n".join(lines)
    prompt = (
        "Summarize the following conversation in 3-5 concise sentences. "
        "Capture the main topics, any decisions made, and key facts mentioned. "
        "Write in third person (e.g. 'The user asked about...').\n\n"
        f"Conversation:\n{transcript}"
    )

    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        summary_model = genai.GenerativeModel(
            model_name=MODEL_NAME,
            generation_config=genai.GenerationConfig(
                max_output_tokens=300,
                temperature=0.2,
            ),
        )
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: summary_model.generate_content(prompt)
        )
        return response.text.strip()
    except Exception as e:
        log.warning("Failed to summarize conversation: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Phase 8: Multimodal helpers (image + document analysis)
# ---------------------------------------------------------------------------

# Supported image MIME types for Gemini
SUPPORTED_IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/webp",
    "image/heic", "image/heif", "image/gif",
}


async def analyze_image(
    image_bytes: bytes,
    mime_type: str,
    prompt: str = "Describe this image in detail. Note any text, errors, or important information.",
) -> str:
    """
    Analyze an image using Gemini's multimodal vision capabilities.
    Returns a descriptive text response.
    """
    if not GOOGLE_API_KEY:
        return "❌ GOOGLE_API_KEY not configured."
    if mime_type not in SUPPORTED_IMAGE_MIMES:
        return f"❌ Unsupported image type: {mime_type}"

    genai.configure(api_key=GOOGLE_API_KEY)
    # Use a fresh model without tools for vision tasks
    vision_model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config=genai.GenerationConfig(
            max_output_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        ),
    )

    try:
        image_part = genai.protos.Part(
            inline_data=genai.protos.Blob(mime_type=mime_type, data=image_bytes)
        )
        text_part = genai.protos.Part(text=prompt)
        content = genai.protos.Content(parts=[image_part, text_part])

        response = await asyncio.to_thread(vision_model.generate_content, content)
        await _record_usage(response)
        return response.text or "No response from model."
    except Exception as e:
        log.error("Image analysis failed: %s", e)
        return f"❌ Image analysis failed: {e}"


async def analyze_document(text: str, prompt: str) -> str:
    """
    Analyze document text using Gemini (no tool loop — direct generation).
    Used by /analyze-file command.
    """
    if not GOOGLE_API_KEY:
        return "❌ GOOGLE_API_KEY not configured."

    genai.configure(api_key=GOOGLE_API_KEY)
    doc_model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config=genai.GenerationConfig(
            max_output_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        ),
    )

    full_prompt = f"{prompt}\n\n---\n\n{text}"

    try:
        response = await asyncio.to_thread(doc_model.generate_content, full_prompt)
        await _record_usage(response)
        return response.text or "No response from model."
    except Exception as e:
        log.error("Document analysis failed: %s", e)
        return f"❌ Document analysis failed: {e}"
