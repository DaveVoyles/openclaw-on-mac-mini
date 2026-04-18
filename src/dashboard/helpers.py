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
            {"name": "/ask <question> [model] [scope] [reset_context] [anchor]", "desc": "AI-powered query — auto mode follows the active routing profile for non-tool asks and keeps Gemini for tool-native flows. Context controls are first-class slash options: scope (current/cross-channel/prior-report), reset_context, and anchor override ('none' disables anchor). Legacy inline flags (e.g. --cross-channel, --reset-context, --anchor, --no-anchor) still work."},
            {"name": "/model show", "desc": "Show your current LLM routing preference and Ollama status."},
            {"name": "/model set <preference>", "desc": "Set your default LLM routing: auto (routing profile), local (Gemma), gemini (cloud), openai (GPT-4o), anthropic (Claude), or copilot (enterprise proxy). Alias accepted: claude → anthropic."},
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
_REPO_ROOT = Path(__file__).resolve().parents[2]
_OPENCLAW_CLI_SOURCE = _REPO_ROOT / "src" / "openclaw_cli.py"
_OPENCLAW_CLI_SUPPORT_SOURCES = {
    "openclaw_cli_actions.py": _REPO_ROOT / "src" / "openclaw_cli_actions.py",
    "openclaw_cli_sessions.py": _REPO_ROOT / "src" / "openclaw_cli_sessions.py",
    "subprocess_utils.py": _REPO_ROOT / "src" / "subprocess_utils.py",
}
DASHBOARD_HTML = (_TEMPLATES_DIR / "dashboard.html").read_text()
GUIDE_HTML = (_TEMPLATES_DIR / "guide.html").read_text()
TERMINAL_HTML = (_TEMPLATES_DIR / "terminal.html").read_text()
ONBOARDING_HTML = (_TEMPLATES_DIR / "onboarding.html").read_text()


def load_openclaw_cli_source() -> str:
    """Return the standalone OpenClaw CLI Python source."""
    return _OPENCLAW_CLI_SOURCE.read_text()


def load_openclaw_cli_support_source(name: str) -> str:
    """Return one of the support modules required by the standalone CLI."""
    source_path = _OPENCLAW_CLI_SUPPORT_SOURCES.get(str(name or "").strip())
    if source_path is None:
        raise FileNotFoundError(f"Unknown OpenClaw CLI support module: {name}")
    return source_path.read_text()


def build_openclaw_cli_installer(
    default_base_url: str,
    *,
    enable_remote_login_default: bool = False,
) -> str:
    """Build a repo-free shell installer for the standalone OpenClaw CLI."""
    template = r'''#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_URL_VALUE="${OPENCLAW_URL:-__DEFAULT_BASE_URL__}"
TARGET_RC_FILE=""
TARGET_SHELL="${OPENCLAW_SHELL:-}"
INSTALL_ROOT="${HOME}/.local/share/openclaw-cli"
BIN_DIR="${HOME}/.local/bin"
ENABLE_REMOTE_LOGIN=__ENABLE_REMOTE_LOGIN__
SKIP_TOKEN_PROMPT=0
VERIFY_INSTALL=1
ALLOW_USER="${USER}"
MAC_MINI_PUBLIC_KEY="${OPENCLAW_MAC_MINI_PUBLIC_KEY:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGoHSUm8PsFsETafvwQVYKD8DCXo4wRWHw24yPpGC40s davevoyles@mac-mini}"

usage() {
  cat <<'EOF'
Usage: openclaw-cli-installer.sh [--url URL] [--shell SHELL] [--rc-file FILE] [--zshrc FILE] [--install-root DIR] [--enable-remote-login] [--allow-user USER] [--skip-token-prompt] [--skip-verify]

Installs a standalone OpenClaw terminal client without requiring a local repo checkout.
The preferred launcher is `openclaw`, with `OpenClaw` and `openclaw-cli` compatibility shims.

Optional extras:
  --enable-remote-login   Also authorize the Mac Mini SSH key and ensure Remote Login is enabled on this Mac
  --skip-verify           Skip the post-install openclaw --health verification step
EOF
}

die() {
  echo "OpenClaw installer error: $*" >&2
  exit 1
}

require_command() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || die "Missing required command: $name"
}

ensure_dir_writable() {
  local dir="$1"
  mkdir -p "$dir" || die "Unable to create directory: $dir"
  [[ -w "$dir" ]] || die "Directory is not writable: $dir"
}

ensure_parent_writable() {
  local target="$1"
  local parent
  parent="$(dirname "$target")"
  mkdir -p "$parent" || die "Unable to create directory: $parent"
  [[ -w "$parent" ]] || die "Directory is not writable: $parent"
  if [[ -e "$target" && ! -w "$target" ]]; then
    die "Target file is not writable: $target"
  fi
}

detect_shell_name() {
  local shell_hint="${TARGET_SHELL:-${SHELL:-zsh}}"
  shell_hint="$(basename "$shell_hint")"
  case "$shell_hint" in
    bash|zsh)
      printf '%s\n' "$shell_hint"
      ;;
    *)
      printf 'zsh\n'
      ;;
  esac
}

default_rc_file_for_shell() {
  local shell_name="$1"
  case "$shell_name" in
    bash)
      printf '%s/.bashrc\n' "$HOME"
      ;;
    *)
      printf '%s/.zshrc\n' "$HOME"
      ;;
  esac
}

append_line_once() {
  local file="$1"
  local line="$2"
  touch "$file"
  if ! grep -Fqx "$line" "$file"; then
    printf '%s\n' "$line" >>"$file"
  fi
}

append_pubkey_once() {
  local file="$1"
  local pubkey="$2"
  touch "$file"
  if ! grep -Fqx "$pubkey" "$file"; then
    printf '%s\n' "$pubkey" >>"$file"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      OPENCLAW_URL_VALUE="$2"
      shift 2
      ;;
    --shell)
      TARGET_SHELL="$2"
      shift 2
      ;;
    --rc-file|--zshrc)
      TARGET_RC_FILE="$2"
      shift 2
      ;;
    --install-root)
      INSTALL_ROOT="$2"
      shift 2
      ;;
    --enable-remote-login)
      ENABLE_REMOTE_LOGIN=1
      shift
      ;;
    --allow-user)
      ALLOW_USER="$2"
      shift 2
      ;;
    --skip-token-prompt)
      SKIP_TOKEN_PROMPT=1
      shift
      ;;
    --skip-verify)
      VERIFY_INSTALL=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

TARGET_SHELL="$(detect_shell_name)"
if [[ -z "$TARGET_RC_FILE" ]]; then
  TARGET_RC_FILE="$(default_rc_file_for_shell "$TARGET_SHELL")"
fi

require_command curl
require_command python3
ensure_dir_writable "$INSTALL_ROOT"
ensure_dir_writable "$BIN_DIR"
ensure_parent_writable "$TARGET_RC_FILE"

download_cli_file() {
  local remote_path="$1"
  local output_path="$2"
  if ! curl -fsSL "$remote_path" -o "$output_path"; then
    die "Failed to download OpenClaw CLI support from $remote_path"
  fi
}

download_cli_file "$OPENCLAW_URL_VALUE/downloads/openclaw_cli.py" "$INSTALL_ROOT/openclaw_cli.py"
download_cli_file "$OPENCLAW_URL_VALUE/downloads/openclaw-cli-support/openclaw_cli_actions.py" "$INSTALL_ROOT/openclaw_cli_actions.py"
download_cli_file "$OPENCLAW_URL_VALUE/downloads/openclaw-cli-support/openclaw_cli_sessions.py" "$INSTALL_ROOT/openclaw_cli_sessions.py"
download_cli_file "$OPENCLAW_URL_VALUE/downloads/openclaw-cli-support/subprocess_utils.py" "$INSTALL_ROOT/subprocess_utils.py"

cat >"$BIN_DIR/openclaw" <<EOF
#!/usr/bin/env bash
exec python3 "$INSTALL_ROOT/openclaw_cli.py" "\$@"
EOF
chmod +x "$BIN_DIR/openclaw"
ln -sf "$BIN_DIR/openclaw" "$BIN_DIR/openclaw-cli"

cat >"$INSTALL_ROOT/openclaw_aliases.sh" <<EOF
unalias OpenClaw openclaw oc-health oc-dash oc-ask oc-chat 2>/dev/null || true

openclaw() {
  "$BIN_DIR/openclaw" "\$@"
}

OpenClaw() {
  openclaw "\$@"
}

oc-health() {
  curl -fsS "\${OPENCLAW_URL:-__DEFAULT_BASE_URL__}/health" | python3 -m json.tool
}

oc-dash() {
  open "\${OPENCLAW_URL:-__DEFAULT_BASE_URL__}/dashboard"
}

oc-ask() {
  openclaw ask "\$@"
}

oc-chat() {
  openclaw chat "\$@"
}
EOF

if [[ "$ENABLE_REMOTE_LOGIN" -eq 1 ]]; then
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "--enable-remote-login is supported on macOS only." >&2
    exit 1
  fi
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  append_pubkey_once "$HOME/.ssh/authorized_keys" "$MAC_MINI_PUBLIC_KEY"
  chmod 600 "$HOME/.ssh/authorized_keys"
  remote_login_state="$(sudo systemsetup -getremotelogin 2>/dev/null || true)"
  if [[ "$remote_login_state" != *"On"* ]]; then
    sudo systemsetup -setremotelogin on
  fi
  sudo dseditgroup -o edit -a "$ALLOW_USER" -t user com.apple.access_ssh
fi

append_line_once "$TARGET_RC_FILE" 'export PATH="$HOME/.local/bin:$PATH"'
append_line_once "$TARGET_RC_FILE" "export OPENCLAW_URL=\"$OPENCLAW_URL_VALUE\""
append_line_once "$TARGET_RC_FILE" "source \"$INSTALL_ROOT/openclaw_aliases.sh\""

if [[ "$SKIP_TOKEN_PROMPT" -eq 0 ]] && command -v security >/dev/null 2>&1; then
  if ! security find-generic-password -s "OpenClaw CLI" -a "${USER}" >/dev/null 2>&1; then
    if [[ -r /dev/tty ]]; then
      printf 'OpenClaw API token (leave blank to skip Keychain setup): ' >/dev/tty
      IFS= read -r -s OPENCLAW_TOKEN_INPUT </dev/tty || OPENCLAW_TOKEN_INPUT=""
      printf '\n' >/dev/tty
      if [[ -n "${OPENCLAW_TOKEN_INPUT}" ]]; then
        security add-generic-password -U -s "OpenClaw CLI" -a "${USER}" -w "${OPENCLAW_TOKEN_INPUT}" >/dev/null
      fi
      unset OPENCLAW_TOKEN_INPUT
    else
      echo "Skipping Keychain token setup (/dev/tty unavailable)." >&2
    fi
  fi
fi

VERIFY_STATUS="skipped"
VERIFY_COMMAND="$BIN_DIR/openclaw --url $OPENCLAW_URL_VALUE --health"
if [[ "$VERIFY_INSTALL" -eq 1 ]]; then
  VERIFY_OUTPUT=""
  if VERIFY_OUTPUT="$("$BIN_DIR/openclaw" --url "$OPENCLAW_URL_VALUE" --health 2>&1)"; then
    VERIFY_STATUS="passed"
  else
    printf '%s\n' "$VERIFY_OUTPUT" >&2
    die "Post-install verification failed. Re-run '$VERIFY_COMMAND' after fixing connectivity."
  fi
fi

cat <<EOF
Standalone OpenClaw CLI installed.

Configured values:
  OPENCLAW_URL=$OPENCLAW_URL_VALUE
  INSTALL_ROOT=$INSTALL_ROOT
  BIN_DIR=$BIN_DIR
  TARGET_SHELL=$TARGET_SHELL
  RC_FILE=$TARGET_RC_FILE

Authentication:
  On macOS, the installer can store your token in Keychain under "OpenClaw CLI".
  Otherwise, export OPENCLAW_TOKEN in your shell profile or pass --token to openclaw.

Verification:
  STATUS=$VERIFY_STATUS
  COMMAND=$VERIFY_COMMAND

Next step:
  source "$TARGET_RC_FILE"
  OpenClaw
  openclaw "what changed overnight?"
EOF
'''
    return (
        template.replace("__DEFAULT_BASE_URL__", default_base_url)
        .replace("__ENABLE_REMOTE_LOGIN__", "1" if enable_remote_login_default else "0")
    )
