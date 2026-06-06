"""Shared utilities for the dashboard package."""

import logging
import re
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

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
    """Canonical Slack slash command metadata grouped by category."""
    return [
        {
            "category": "💬 Conversation & AI",
            "commands": [
                {
                    "name": "/chat <question> [--model <alias>] [--simple]",
                    "desc": "Ask OpenClaw anything — routes through the agent pipeline with your preferred model.",
                },
                {
                    "name": "/research <query>",
                    "desc": "Deep multi-source research: Perplexity → Tavily → DDG, synthesized report with sources.",
                },
                {
                    "name": "/simple [on|off]",
                    "desc": "Toggle simple/plain response mode — shorter answers without rich formatting.",
                },
                {"name": "/clear", "desc": "Clear active comparison state and file context for your session."},
            ],
        },
        {
            "category": "📁 Files & Batch Processing",
            "commands": [
                {
                    "name": "/files [recent|history|list|delete <name>]",
                    "desc": "Manage uploaded files — list recent, view history, or delete a file by name.",
                },
                {
                    "name": "/batch [prompt]",
                    "desc": "Run a prompt against all currently uploaded files at once (batch analysis).",
                },
                {"name": "/filesearch <keyword>", "desc": "Search your uploaded file history by filename keyword."},
                {"name": "/brief", "desc": "AI-generated brief of your most recently uploaded file."},
            ],
        },
        {
            "category": "📬 Email & Inbox",
            "commands": [
                {"name": "/inbox [count]", "desc": "Show recent Gmail inbox messages (ephemeral, metadata only)."},
                {"name": "/email inbox [count]", "desc": "Show recent emails."},
                {"name": "/email search <query>", "desc": "Search Gmail inbox by keyword."},
                {"name": "/email read <id>", "desc": "Read a full email by message ID."},
                {"name": "/email send <to> <subject> <body>", "desc": "Send email via Gmail (requires approval)."},
            ],
        },
        {
            "category": "🗓️ Calendar & Scheduling",
            "commands": [
                {"name": "/calendar today", "desc": "List today's Google Calendar events."},
                {"name": "/calendar upcoming [days]", "desc": "Show next N days of calendar events."},
                {"name": "/calendar add <title> <when>", "desc": "Create a new calendar event."},
                {"name": "/today", "desc": "Morning briefing: today's calendar, weather, and tasks in one shot."},
                {
                    "name": "/schedule [list|add|delete <id>]",
                    "desc": "Manage recurring scheduled tasks — list, create, or delete automations.",
                },
            ],
        },
        {
            "category": "🚨 Incident Operations",
            "commands": [
                {
                    "name": "/incident start <title> <severity> [details]",
                    "desc": "Create an incident and post Copilot triage summary + recommended actions.",
                },
                {
                    "name": "/incident status <id> [state] [note]",
                    "desc": "Check or update incident state (open/investigating/monitoring/resolved).",
                },
                {"name": "/incident list [state]", "desc": "List recent incidents filtered by state."},
                {
                    "name": "/incident resolve <id> <summary>",
                    "desc": "Resolve an incident and capture postmortem notes.",
                },
            ],
        },
        {
            "category": "🤖 Copilot CLI",
            "commands": [
                {
                    "name": "/copilot <prompt>",
                    "desc": "Run the configured host bridge backend on the Mac Mini. Starts or resumes your threaded session.",
                },
                {
                    "name": "/hermes <prompt>",
                    "desc": "Always run Hermes on the Mac Mini host, regardless of `COPILOT_BACKEND`.",
                },
                {"name": "/h <prompt>", "desc": "Alias for `/hermes` — always run Hermes on the Mac Mini host."},
                {"name": "/copilot-sessions", "desc": "List your active Copilot CLI sessions."},
                {"name": "/copilot-cancel", "desc": "Cancel the current Copilot task."},
                {"name": "/copilot-end", "desc": "End your Copilot session and release the host connection."},
                {"name": "/copilot-attach", "desc": "Attach to an existing Copilot session by ID."},
                {
                    "name": "/host <command>",
                    "desc": "Run an arbitrary command on the Mac Mini host via the host bridge.",
                },
            ],
        },
        {
            "category": "🏠 Home Lab",
            "commands": [
                {"name": "/wake [mbp|mbp2]", "desc": "Send a Wake-on-LAN magic packet to a configured MacBook Pro."},
                {
                    "name": "/nas [df|ls <path>|free]",
                    "desc": "Inspect NAS disk usage, browse allowed mount paths, or show NAS CPU/memory when available.",
                },
                {
                    "name": "/plex [status|recent|search|request]",
                    "desc": "Check Plex server status, recent activity, or search/request media via Overseerr.",
                },
                {
                    "name": "/watching",
                    "desc": "Show who is streaming on Plex right now, with recent watches when idle.",
                },
            ],
        },
        {
            "category": "💾 Storage & Drive",
            "commands": [
                {"name": "/clawbox [list|get|upload|delete]", "desc": "Manage files in Dropbox via OpenClaw."},
                {"name": "/clawchan [list|send|get]", "desc": "Cross-channel file transfer and Dropbox sync."},
                {"name": "/drive [list|search|open]", "desc": "Browse and search Google Drive files."},
            ],
        },
        {
            "category": "👤 Contacts & People",
            "commands": [
                {"name": "/contacts [search <name>|add|list]", "desc": "Search, add, or list Google Contacts."},
                {"name": "/nickname <name>", "desc": "Set your preferred display name for OpenClaw responses."},
                {"name": "/mystats", "desc": "View your personal usage stats (anonymised, local only)."},
                {"name": "/mypins", "desc": "View your saved/pinned notes and messages."},
            ],
        },
        {
            "category": "📋 Templates & Output",
            "commands": [
                {
                    "name": "/template [list|use <name>|save <name>]",
                    "desc": "Manage reusable prompt templates — list, apply, or save new ones.",
                },
                {
                    "name": "/metrics",
                    "desc": "Show Slack bot usage metrics: commands run, files processed, error rate.",
                },
            ],
        },
        {
            "category": "❓ Help & Health",
            "commands": [
                {"name": "/help", "desc": "Show a guide to available Slack commands with examples."},
                {"name": "/health", "desc": "Bot health check — uptime, model status, container connectivity."},
                {"name": "/digest [on|off]", "desc": "Enable or disable the daily file digest DM."},
            ],
        },
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
            commands.append(
                {
                    "name": name,
                    "desc": desc,
                    "keywords": sorted(set(tokens)),
                }
            )
        normalized.append({"category": category, "commands": commands})
    return normalized


def _command_quickstart() -> list[dict]:
    """Quick-start commands to improve discoverability surfaces."""
    return [
        {"name": "/help", "desc": "Browse commands by category"},
        {"name": "/chat", "desc": "Ask OpenClaw anything in Slack"},
        {"name": "/research", "desc": "Run deep multi-source research"},
        {"name": "/copilot", "desc": "Launch the configured host bridge backend on the Mac Mini"},
        {"name": "/hermes", "desc": "Launch a Hermes session on the Mac Mini"},
        {"name": "/incident start", "desc": "Kick off guided incident triage"},
        {"name": "/schedule", "desc": "Create and manage automations"},
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
        lines.extend(
            [
                "",
                f"## {category}",
                "",
                "| Command | Description |",
                "| --- | --- |",
            ]
        )
        for cmd in group.get("commands", []):
            name = _md_escape(str(cmd.get("name", "")))
            desc = _md_escape(str(cmd.get("desc", "")))
            lines.append(f"| `{name}` | {desc} |")

    lines.extend(
        [
            "",
            "---",
            "",
            "_Generated from runtime metadata to prevent command-doc drift._",
            "",
        ]
    )

    return "\n".join(lines)


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_OPENCLAW_CLI_SOURCE = _REPO_ROOT / "src" / "openclaw_cli.py"
_OPENCLAW_CLI_SUPPORT_SOURCES = {
    "openclaw_cli_actions.py": _REPO_ROOT / "src" / "openclaw_cli_actions.py",
    "openclaw_cli_sessions.py": _REPO_ROOT / "src" / "openclaw_cli_sessions.py",
    "openclaw_cli_cmd_core.py": _REPO_ROOT / "src" / "openclaw_cli_cmd_core.py",
    "subprocess_utils.py": _REPO_ROOT / "src" / "subprocess_utils.py",
}
DASHBOARD_HTML = (_TEMPLATES_DIR / "dashboard.html").read_text()
GUIDE_HTML = (_TEMPLATES_DIR / "tech-guide.html").read_text()
TERMINAL_HTML = (_TEMPLATES_DIR / "terminal.html").read_text()
ONBOARDING_HTML = (_TEMPLATES_DIR / "onboarding.html").read_text()
WEBUI_GUIDE_HTML = (_TEMPLATES_DIR / "webui-guide.html").read_text()
PARENTS_GUIDE_HTML = (_TEMPLATES_DIR / "parents-guide.html").read_text()


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
    template = r"""#!/usr/bin/env bash
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
"""
    return template.replace("__DEFAULT_BASE_URL__", default_base_url).replace(
        "__ENABLE_REMOTE_LOGIN__", "1" if enable_remote_login_default else "0"
    )


def build_hermes_installer(openclaw_base_url: str) -> str:
    """Build a shell installer that installs Hermes agent and seeds the Copilot config."""
    template = r"""#!/usr/bin/env bash
# Hermes Agent Installer — generated by OpenClaw dashboard
# Usage: curl -fsSL __OPENCLAW_BASE_URL__/install-hermes | bash
set -euo pipefail

HERMES_HOME="${HOME}/.hermes"
BIN_DIR="${HOME}/.local/bin"
OPENCLAW_BASE_URL="__OPENCLAW_BASE_URL__"

die() { echo "❌ $*" >&2; exit 1; }
info() { echo "  $*"; }
ok() { echo "✅ $*"; }

echo ""
echo "⚕ Hermes Agent Installer"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
command -v curl >/dev/null 2>&1 || die "curl is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required (install via Homebrew: brew install python)"

# ── 2. Install uv (fast Python package manager used by Hermes) ────────────────
if ! command -v uv >/dev/null 2>&1; then
  info "Installing uv..."
  curl -fsSL https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.cargo/bin:${HOME}/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || export PATH="${HOME}/.cargo/bin:${HOME}/.local/bin:$PATH"
ok "uv ready: $(uv --version 2>/dev/null || echo 'found')"

# ── 3. Install Hermes via pip ─────────────────────────────────────────────────
info "Installing Hermes..."
uv tool install hermes-agent --upgrade 2>/dev/null || pip3 install --user --upgrade hermes-agent
export PATH="${HOME}/.local/bin:$PATH"
command -v hermes >/dev/null 2>&1 || die "hermes binary not found in PATH after install. Try: export PATH=~/.local/bin:\$PATH"
HERMES_VER="$(hermes --version 2>/dev/null | head -1 || echo 'installed')"
ok "Hermes installed: ${HERMES_VER}"

# ── 4. Bootstrap config dir ───────────────────────────────────────────────────
mkdir -p "${HERMES_HOME}/memories" "${HERMES_HOME}/skills" "${HERMES_HOME}/logs"

# ── 5. Seed config.yaml (Copilot provider) ────────────────────────────────────
CONFIG_FILE="${HERMES_HOME}/config.yaml"
if [[ ! -f "${CONFIG_FILE}" ]]; then
  info "Writing default config (Copilot provider)..."
  cat > "${CONFIG_FILE}" <<'YAML'
model:
  provider: copilot
  default: claude-sonnet-4.6
terminal:
  backend: docker
display:
  compact: false
YAML
  ok "Config written: ${CONFIG_FILE}"
else
  info "Config already exists — skipping (${CONFIG_FILE})"
fi

# ── 6. GitHub Copilot Authentication ─────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔑 GitHub Copilot Authentication"
echo ""
# Hermes uses the system `gh` CLI for Copilot auth — no separate login needed.
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  ok "gh CLI authenticated — Copilot provider ready automatically"
else
  echo "  ⚠️  GitHub CLI (gh) is not authenticated."
  echo "  Hermes uses gh for Copilot auth. Run after install:"
  echo ""
  echo "  → brew install gh    # if not installed"
  echo "  → gh auth login      # authenticate with GitHub"
  echo ""
  echo "  Then start Hermes: hermes"
fi

# ── 7. Seed MEMORY.md ─────────────────────────────────────────────────────────
MEMORY_FILE="${HERMES_HOME}/memories/MEMORY.md"
if [[ ! -f "${MEMORY_FILE}" ]]; then
  info "Seeding memory from OpenClaw..."
  curl -fsSL "${OPENCLAW_BASE_URL}/api/hermes/memory-seed" -o "${MEMORY_FILE}" 2>/dev/null || \
    echo "# Hermes Memory — $(hostname)" > "${MEMORY_FILE}"
  ok "Memory file seeded"
fi

# ── 7b. Seed custom skills ────────────────────────────────────────────────────
SKILLS_DIR="${HERMES_HOME}/skills"
mkdir -p "${SKILLS_DIR}"
SKILLS_TAR="/tmp/hermes-skills.tar.gz"
if curl -fsSL "${OPENCLAW_BASE_URL}/api/hermes/skills-seed" -o "${SKILLS_TAR}" 2>/dev/null && \
   [[ -s "${SKILLS_TAR}" ]]; then
  tar -xzf "${SKILLS_TAR}" -C "${SKILLS_DIR}" --skip-old-files 2>/dev/null || true
  rm -f "${SKILLS_TAR}"
  ok "Custom skills seeded"
fi

# ── 8. Add to PATH in shell RC ────────────────────────────────────────────────
SHELL_RC="${HOME}/.zshrc"
[[ "$(basename "${SHELL:-zsh}")" == "bash" ]] && SHELL_RC="${HOME}/.bashrc"

if ! grep -q 'hermes\|\.local/bin' "${SHELL_RC}" 2>/dev/null; then
  echo '' >> "${SHELL_RC}"
  echo '# Hermes agent PATH' >> "${SHELL_RC}"
  echo 'export PATH="${HOME}/.local/bin:$PATH"' >> "${SHELL_RC}"
  ok "Added ~/.local/bin to ${SHELL_RC}"
fi

# ── 9. Verify ─────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Hermes installed"
echo ""
echo "  Config:  ${CONFIG_FILE}"
echo "  Memory:  ${MEMORY_FILE}"
echo "  Binary:  $(command -v hermes 2>/dev/null || echo '~/.local/bin/hermes')"
echo ""
echo "Next steps:"
echo "  1. source ${SHELL_RC}"
if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
  echo "  2. gh auth login   # authenticate GitHub CLI for Copilot"
  echo "  3. hermes"
else
  echo "  2. hermes"
fi
echo ""
echo "  Docs:  https://github.com/NousResearch/hermes-agent"
echo "  Home:  ${OPENCLAW_BASE_URL}"
"""
    return template.replace("__OPENCLAW_BASE_URL__", openclaw_base_url)
