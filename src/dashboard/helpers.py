"""Shared utilities for the dashboard package."""

import logging
import re
from pathlib import Path

import yaml

log = logging.getLogger("openclaw.dashboard")

CONFIG_DIR = Path("/config")
GITHUB_REPO = "https://github.com/DaveVoyles/openclaw-on-mac-mini"
VERSION = "0.5.0"

_config_cache: dict | None = None
_config_mtime: float = 0.0


def _load_config() -> dict:
    global _config_cache, _config_mtime
    cfg_file = CONFIG_DIR / "config.yaml"
    try:
        current_mtime = cfg_file.stat().st_mtime if cfg_file.exists() else 0.0
    except OSError:
        current_mtime = 0.0
    if _config_cache is not None and current_mtime == _config_mtime:
        return _config_cache
    _config_cache = yaml.safe_load(cfg_file.read_text()) if cfg_file.exists() else {}
    _config_mtime = current_mtime
    return _config_cache or {}


_CRON_DOW = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def _cron_to_human(expr: str) -> str:
    """Convert a cron expression to a human-readable string (best-effort)."""
    try:
        parts = expr.strip().split()
        if len(parts) != 5:
            return expr
        minute, hour, dom, month, dow = parts

        time_str = ""
        if hour != "*" and minute != "*":
            time_str = f"at {hour.zfill(2)}:{minute.zfill(2)}"
        elif hour != "*":
            time_str = f"at hour {hour}"
        elif minute != "*":
            every_m = minute.lstrip("*/")
            time_str = f"every {every_m} min"

        day_str = ""
        if dow != "*":
            day_names = []
            for part in dow.split(","):
                if "-" in part:
                    lo, hi = part.split("-", 1)
                    day_names.append(f"{_CRON_DOW.get(int(lo), lo)}–{_CRON_DOW.get(int(hi), hi)}")
                else:
                    day_names.append(_CRON_DOW.get(int(part), part))
            day_str = ", ".join(day_names)
        elif dom != "*":
            day_str = f"day {dom} of month"

        if month != "*":
            day_str += f" (month {month})"

        if day_str and time_str:
            return f"{day_str} {time_str}"
        return day_str or time_str or expr
    except (ValueError, IndexError, KeyError) as exc:
        log.debug("Cron expression parse failed for %r: %s", expr, exc)
        return expr


def _raw_command_groups() -> list[dict]:
    """Canonical command metadata grouped by category."""
    return [
        {"category": "🏛️ Foundation", "commands": [
            {"name": "/ping", "desc": "Check if bot is alive"},
            {"name": "/about", "desc": "Version and system info"},
            {"name": "/whoami", "desc": "Your Discord identity & permissions"},
            {"name": "/help", "desc": "List all commands"},
        ]},
        {"category": "🐳 Docker & System", "commands": [
            {"name": "/containers", "desc": "List running containers"},
            {"name": "/status <service>", "desc": "Container detail + resources"},
            {"name": "/logs <service> [lines]", "desc": "View container logs"},
            {"name": "/system", "desc": "CPU, memory, disk usage"},
            {"name": "/dockerstats", "desc": "Per-container resource usage"},
            {"name": "/restart <service>", "desc": "Restart a container (requires approval)"},
        ]},
        {"category": "🤖 AI & LLM", "commands": [
            {"name": "/ask <question> [model] [scope] [reset_context] [anchor]", "desc": "AI-powered query — auto-routes to Gemini (tools) or Ollama (chat). Context controls are first-class slash options: scope (current/cross-channel/prior-report), reset_context, and anchor override ('none' disables anchor). Legacy inline flags (e.g. --cross-channel, --reset-context, --anchor, --no-anchor) still work."},
            {"name": "/model show", "desc": "Show your current LLM routing preference and Ollama status."},
            {"name": "/model set <preference>", "desc": "Set your default LLM routing: auto (smart), local (Gemma), gemini (cloud), openai (GPT-4o), or anthropic (Claude). Alias accepted: claude → anthropic."},
            {"name": "/research <query> [deep:true]", "desc": "Deep multi-step research — Discord thread, planned sub-queries, 4-tier search (Perplexity → Tavily → DDG → Bing Lite), source ranking, cross-referencing, confidence levels, synthesized report with methodology section"},
            {"name": "/weather [location]", "desc": "Current conditions + 3-day forecast for any location (default: WEATHER_DEFAULT_LOCATION env var)"},
            {"name": "/clear", "desc": "Clear active conversation history"},
            {"name": "/save <name>", "desc": "Save current conversation as a named thread (persisted to disk)"},
            {"name": "/resume <name>", "desc": "Resume a previously saved conversation thread"},
            {"name": "/threads", "desc": "List all your saved conversation threads"},
            {"name": "/forget <name>", "desc": "Delete a saved conversation thread"},
            {"name": "/analyze <service> [lines]", "desc": "AI log analysis"},
        ]},
        {"category": "🗓️ Recaps & Watch Guides", "commands": [
            {"name": "/recap weekly [days] [style]", "desc": "Summarize the current Discord channel or thread with highlights, action items, or a compact table. Optional save-to-vault and Monday scheduling."},
            {"name": "/sports upcoming [query]", "desc": "Create a sports watch guide with matchups, ET kickoff times, and where-to-watch details from live web research. Optional save-to-vault and Monday scheduling."},
            {"name": "Create recap from thread", "desc": "Right-click a Discord message or thread to generate a recap without typing a slash command."},
        ]},
        {"category": "🎬 Media & Downloads", "commands": [
            {"name": "/search <query> [type]", "desc": "Search Sonarr/Radarr catalogs"},
            {"name": "/queue", "desc": "Active downloads (SABnzbd + qBit)"},
            {"name": "/recent [count]", "desc": "Recently added Plex media"},
            {"name": "/health", "desc": "Check *arr + download client health"},
            {"name": "/ports", "desc": "Service port connectivity check"},
            {"name": "/report", "desc": "Comprehensive status report"},
        ]},
        {"category": "🚨 Incident Operations", "commands": [
            {"name": "/incident start <title> <severity> [details] [services]", "desc": "Create an incident and post Copilot triage summary + recommended actions in the incident thread."},
            {"name": "/incident create <title> <severity> [details]", "desc": "Create a manual incident room entry without Copilot triage."},
            {"name": "/incident status <id> [state] [note]", "desc": "Check or update incident state (open/investigating/monitoring)."},
            {"name": "/incident list [state] [limit]", "desc": "List recent incidents (active/all/open/investigating/monitoring/resolved)."},
            {"name": "/incident timeline [id] [limit]", "desc": "Show timeline events for an incident; defaults to current incident thread when possible."},
            {"name": "/incident resolve <id> <summary> [action_items] [notes]", "desc": "Resolve an incident and capture postmortem notes/actions."},
        ]},
        {"category": "🧠 Memory & Automation", "commands": [
            {"name": "/remember <fact> [tags]", "desc": "Store a fact in long-term memory"},
            {"name": "/recall <query>", "desc": "Search long-term memory"},
            {"name": "/schedule", "desc": "Manage scheduled tasks (CRUD via slash command)"},
            {"name": "/skills", "desc": "List all LLM-callable skills"},
            {"name": "/briefing", "desc": "On-demand morning briefing (weather + health + calendar)"},
            {"name": "/audit-summary", "desc": "Analytics on today's audit log"},
            {"name": "/nowplaying", "desc": "Live Plex active streams"},
            {"name": "/dream", "desc": "Run cognitive dream cycle (memory consolidation)"},
            {"name": "/memory-health", "desc": "Show memory health score and 5 metrics"},
            {"name": "/memory-export", "desc": "Export memory bundle"},
        ]},
        {"category": "🌐 Network & Monitoring", "commands": [
            {"name": "/network", "desc": "LAN, internet, DNS connectivity"},
            {"name": "/tailscale", "desc": "Tailscale VPN status"},
            {"name": "/speedtest", "desc": "Network speed test"},
            {"name": "/spending [breakdown]", "desc": "Gemini API cost tracking"},
        ]},
        {"category": "Security & Admin", "commands": [
            {"name": "/pending", "desc": "Pending approval requests"},
            {"name": "/auditlog [lines]", "desc": "View audit trail"},
            {"name": "/estop [stop|resume]", "desc": "Emergency stop all actions"},
            {"name": "/mail <to> <subject> <body>", "desc": "Send email via AgentMail"},
        ]},
        {"category": "📋 Copy/Paste Workflow", "commands": [
            {"name": "/recap copy-latest", "desc": "Copy-ready export of your latest OpenClaw response in the current channel/thread"},
            {"name": "/recap copy-thread [days] [style]", "desc": "Generate and export a copy-ready recap for the current channel/thread"},
            {"name": "Context menu: Copy Workflow Context", "desc": "Right-click any message to export a mobile-friendly copy block"},
        ]},
        {"category": "Document Review & Interview", "commands": [
            {"name": "/review text [mode]", "desc": "Paste text for structured critique (writing/technical/quick)"},
            {"name": "/review file [mode]", "desc": "Upload DOCX/PDF/TXT/etc for structured critique"},
            {"name": "/interview <goal>", "desc": "Sequential Q&A modals → personalized output"},
        ]},
        {"category": "Calendar & Email", "commands": [
            {"name": "/calendar today", "desc": "List today's Google Calendar events"},
            {"name": "/calendar upcoming [days]", "desc": "Next N days of events"},
            {"name": "/calendar add <title> <when>", "desc": "Create event"},
            {"name": "/email inbox [count]", "desc": "Show recent emails (ephemeral)"},
            {"name": "/email search <query>", "desc": "Search inbox"},
            {"name": "/email read <id>", "desc": "Read full email"},
            {"name": "/email send <to> <subject> <body>", "desc": "Send email (requires approval)"},
        ]},
        {"category": "Journal & GitHub", "commands": [
            {"name": "/journal write [entry]", "desc": "Save today's journal entry to vault"},
            {"name": "/journal read [date]", "desc": "Read past entry"},
            {"name": "/journal streak", "desc": "Streak counter"},
            {"name": "/journal prompt", "desc": "AI writing prompt"},
            {"name": "/github prs [repo]", "desc": "List open pull requests"},
            {"name": "/github issues [repo]", "desc": "List open issues"},
            {"name": "/github watch <repo>", "desc": "Subscribe to activity DMs"},
        ]},
        {"category": "🎨 Image Generation", "commands": [
            {"name": "/imagine generate <prompt> [size] [negative]", "desc": "Generate image via Stable Diffusion txt2img"},
            {"name": "/imagine status", "desc": "Check SD online status and list models"},
        ]},
        {"category": "🌐 DNS Management", "commands": [
            {"name": "/dns status", "desc": "AdGuard Home status and filtering toggle"},
            {"name": "/dns stats", "desc": "Query/block counts and top domains"},
            {"name": "/dns block <domain>", "desc": "Block domain via DNS rewrite"},
            {"name": "/dns allow <domain>", "desc": "Unblock a domain"},
            {"name": "/dns blocked", "desc": "List all manually blocked domains"},
        ]},
        {"category": "📝 Notion", "commands": [
            {"name": "/notion search <query>", "desc": "Search Notion pages and databases"},
            {"name": "/notion page <title> <content>", "desc": "Create a new Notion page"},
            {"name": "/notion todo <item>", "desc": "Add item to Notion todo database"},
        ]},
        {"category": "📄 Google Docs", "commands": [
            {"name": "/gdoc save <title> <content>", "desc": "Create a new Google Doc"},
            {"name": "/gdoc list", "desc": "List recent Google Docs"},
        ]},
        {"category": "🖥️ System Performance", "commands": [
            {"name": "/perf", "desc": "CPU, memory, disk, load average via Glances"},
        ]},
        {"category": "📱 Push Notifications", "commands": [
            {"name": "/ntfy send <message> [title] [priority]", "desc": "Send phone push notification via ntfy"},
            {"name": "/ntfy test", "desc": "Send test notification to verify setup"},
        ]},
        {"category": "📲 SMS One-Tap", "commands": [
            {"name": "/sms config <phone> [send_verification]", "desc": "Save phone number for one-tap SMS; can trigger verification send"},
            {"name": "/sms test [code]", "desc": "Start verification or submit code from SMS"},
            {"name": "/sms status", "desc": "Show masked phone, verification state, and remaining send budget"},
            {"name": "/sms send <message>", "desc": "Confirmation-based SMS send to configured phone"},
            {"name": "Context menu: Send to SMS", "desc": "Right-click a Discord message and forward it via SMS with confirmation"},
        ]},
        {"category": "🎬 Movie & TV", "commands": [
            {"name": "/media movie <title>", "desc": "Look up a movie with poster and ratings"},
            {"name": "/media tv <title>", "desc": "Look up a TV show with season/episode info"},
            {"name": "/media search <query>", "desc": "Search movies and TV via OMDb"},
        ]},
        {"category": "🐛 Error Monitoring", "commands": [
            {"name": "/sentry issues [project]", "desc": "List unresolved Sentry issues"},
            {"name": "/sentry projects", "desc": "List Sentry org projects"},
            {"name": "/sentry resolve <issue_id>", "desc": "Resolve a Sentry issue"},
            {"name": "/sentry stats [project]", "desc": "Hourly error rate stats"},
        ]},
        {"category": "Third-Party API Gateway (via /ask)", "commands": [
            {"name": "gateway_request", "desc": "Call any of 100+ APIs (Slack, GitHub, Notion, HubSpot, Stripe…) via Maton managed OAuth. Invoked by /ask."},
            {"name": "gateway_list_connections", "desc": "List active Maton OAuth connections (optionally filter by app). Invoked by /ask."},
            {"name": "gateway_create_connection", "desc": "Create a new Maton OAuth connection for an app and return the authorization URL. Invoked by /ask."},
        ]},
        {"category": "Knowledge Graph & Ontology (via /ask)", "commands": [
            {"name": "ontology_create_entity", "desc": "Create a new typed entity (Person, Project, Task, etc.) in graph memory."},
            {"name": "ontology_get_entity", "desc": "Retrieve all details and relations for a specific entity name."},
            {"name": "ontology_relate", "desc": "Create a typed link between two entities (e.g., 'blocks', 'manages')."},
            {"name": "ontology_query", "desc": "Search the knowledge graph for entities by name or type."},
        ]},
        {"category": "Self-Management & Autonomy (via /ask)", "commands": [
            {"name": "spawn_worker", "desc": "Spawn a focused AI sub-agent to accomplish a specific goal autonomously using its own tool loop."},
            {"name": "create_scheduled_task", "desc": "Create a recurring scheduled task (LLM-controlled). Supports cron expressions, prompt jobs, or interval-based."},
            {"name": "cancel_scheduled_task", "desc": "Cancel a scheduled task by ID."},
            {"name": "list_scheduled_tasks", "desc": "List all active scheduled tasks with cron expressions, run counts, and next run times."},
            {"name": "webfetch_md", "desc": "Smartly scrape any URL and convert main content to clean Markdown."},
            {"name": "git_status", "desc": "Check project repository status for code changes."},
            {"name": "git_log", "desc": "View recent code change history (commit log)."},
            {"name": "git_diff", "desc": "Compare code changes or view uncommitted changes."},
            {"name": "git_commit", "desc": "Commit all current changes with a brief summary message."},
            {"name": "init_planning_files", "desc": "Initialize task_plan.md, findings.md, progress.md for complex tasks."},
            {"name": "update_plan_status", "desc": "Log progress or update status of a phase in planning files."},
        ]},
        {"category": "📝 Notes & Vault", "commands": [
            {"name": "/note create", "desc": "Create a note in the Obsidian vault"},
            {"name": "/note list", "desc": "Browse recent vault notes"},
            {"name": "/note view", "desc": "View a vault note's content"},
            {"name": "/note search", "desc": "Search vault notes by content"},
        ]},
        {"category": "📋 Agent Loop & Plans", "commands": [
            {"name": "/plans [status]", "desc": "List active/recent agent plans. Filter: all, in-progress, completed, interrupted."},
            {"name": "/plan-detail <plan_id>", "desc": "Show full details of a specific plan (steps, status, outputs)."},
            {"name": "/resume-plan <plan_id>", "desc": "Resume an interrupted plan from where it left off."},
            {"name": "/cancel-plan <plan_id>", "desc": "Cancel an active plan (marks interrupted, resets in-progress steps)."},
            {"name": "create_plan", "desc": "(via /ask) Create a new task plan with a goal and ordered steps. Returns plan_id."},
            {"name": "update_plan_step", "desc": "(via /ask) Update a step's status (done/failed/skipped) with output summary."},
            {"name": "read_plan", "desc": "(via /ask) Read the current state of a plan including all step statuses."},
            {"name": "list_plans", "desc": "(via /ask) List plans filtered by status."},
            {"name": "adjust_plan", "desc": "(via /ask) Add, remove, or reorder steps in an active plan."},
            {"name": "cancel_plan", "desc": "(via /ask) Cancel an active plan and mark it interrupted."},
            {"name": "resume_plan", "desc": "(via /ask) Resume an interrupted plan from where it left off."},
        ]},
    ]


def _command_list() -> list[dict]:
    """Canonical command metadata grouped by category."""
    normalized: list[dict] = []
    for group in _raw_command_groups():
        category = group.get("category", "Other")
        commands: list[dict] = []
        for cmd in group.get("commands", []):
            name = str(cmd.get("name", "")).strip()
            desc = str(cmd.get("desc", "")).strip()
            tokens = re.findall(r"[a-z0-9_/-]+", f"{category} {name} {desc}".lower())
            commands.append({
                "name": name,
                "desc": desc,
                "keywords": sorted(set(tokens)),
            })
        normalized.append({"category": category, "commands": commands})
    return normalized


def _command_quickstart() -> list[dict]:
    """Quick-start commands to improve discoverability surfaces."""
    return [
        {"name": "/help", "desc": "Browse commands by category"},
        {"name": "/ask", "desc": "Use plain English and let OpenClaw route tools"},
        {"name": "/research", "desc": "Run deep multi-source research"},
        {"name": "/schedule", "desc": "Create and manage automations"},
        {"name": "/incident start", "desc": "Kick off guided incident triage"},
        {"name": "/recap weekly", "desc": "Summarize a channel/thread quickly"},
    ]


def _md_escape(value: str) -> str:
    """Escape markdown table-sensitive characters."""
    return value.replace("|", "\\|").replace("\n", " ").strip()


def render_command_reference_markdown() -> str:
    """Render docs/COMMANDS.md from canonical command metadata."""
    groups = _raw_command_groups()
    total_commands = sum(len(group.get("commands", [])) for group in groups)

    lines = [
        "# OpenClaw Command Reference",
        "",
        "> Source of truth: `src/dashboard/helpers.py::_raw_command_groups()`",
        ">",
        "> This file is generated from runtime command metadata used by the dashboard and guide command finder.",
        "",
        f"Total documented commands: **{total_commands}**",
        "",
        "## Quick Start",
        "",
    ]
    for cmd in _command_quickstart():
        lines.append(f"- `{_md_escape(cmd['name'])}` — {_md_escape(cmd['desc'])}")

    for group in groups:
        category = _md_escape(str(group.get("category", "Other")))
        lines.extend([
            "",
            f"## {category}",
            "",
            "| Command | Description |",
            "| --- | --- |",
        ])
        for cmd in group.get("commands", []):
            name = _md_escape(str(cmd.get("name", "")))
            desc = _md_escape(str(cmd.get("desc", "")))
            lines.append(f"| `{name}` | {desc} |")

    lines.extend([
        "",
        "---",
        "",
        "_Generated from runtime metadata to prevent command-doc drift._",
        "",
    ])

    return "\n".join(lines)


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
DASHBOARD_HTML = (_TEMPLATES_DIR / "dashboard.html").read_text()
GUIDE_HTML = (_TEMPLATES_DIR / "guide.html").read_text()
TERMINAL_HTML = (_TEMPLATES_DIR / "terminal.html").read_text()
