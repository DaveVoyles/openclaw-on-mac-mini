"""
OpenClaw Slack Bot — Socket Mode integration.

Listens for @OpenClaw mentions and DMs via Slack Socket Mode (no public URL needed).
Routes messages through OpenClaw's ask orchestrator and replies in thread.

Required environment variables:
  SLACK_BOT_TOKEN    — xoxb-... (Bot User OAuth Token)
  SLACK_APP_TOKEN    — xapp-... (App-Level Token with connections:write scope)
  SLACK_ENABLED      — "true" to enable (default: false)

Setup:
  1. Create app at https://api.slack.com/apps
  2. Enable Socket Mode (Features > Socket Mode)
  3. Add Bot Token Scopes: app_mentions:read, channels:history, chat:write,
     im:history, im:read, im:write, reactions:read
  4. Subscribe to events: app_mention, message.im, reaction_added
  5. Enable slash command: /chat (any Request URL placeholder works in Socket Mode)
  6. Install app to workspace
  7. Copy Bot User OAuth Token (xoxb-...) to SLACK_BOT_TOKEN
  8. Copy App-Level Token (xapp-...) to SLACK_APP_TOKEN

Features:
  - @mention in channels → OpenClaw answer (in-thread)
  - DMs → OpenClaw answer
  - Thread context: follow-up messages in a thread carry prior Q&A as history
  - Model selector: append --model gemini|openai|anthropic|copilot|auto to any prompt
  - /chat slash command: native Slack slash command
  - 👍/👎 feedback: react to any bot response to log a rating

Wiring into src/bot.py (add inside setup_hook or on_ready):
  # Start Slack bot if configured
  if os.getenv("SLACK_ENABLED", "false").lower() == "true":
      try:
          from slack_bot import create_slack_handler
          _slack_handler = await create_slack_handler()
          if _slack_handler:
              asyncio.create_task(_slack_handler.start_async())
              log.info("Slack Socket Mode handler started")
      except Exception as e:
          log.warning("Slack bot failed to start: %s", e)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import aiohttp

import file_skills
from constants import ATTACHMENT_TEXT_MAX_CHARS
from document_skills import create_word
from http_session import SessionManager
from llm import analyze_image as llm_analyze_image
from trace_context import set_trace as _set_trace

log = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")

# ---------------------------------------------------------------------------
# Per-user preferences (persisted to data/slack_user_prefs.json)
# ---------------------------------------------------------------------------
# Stored as: {"U123ABC": {"simple": true}, ...}
# Loaded once at startup; written on every change.
# ---------------------------------------------------------------------------

_PREFS_PATH: Path = Path(__file__).parent.parent / "data" / "slack_user_prefs.json"
_user_prefs: dict[str, dict] = {}

_PERSONAS_PATH: Path = Path(__file__).parent.parent / "data" / "slack_user_personas.json"
_personas: dict[str, dict] = {}


def _load_prefs() -> None:
    global _user_prefs
    try:
        if _PREFS_PATH.exists():
            _user_prefs = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to load user prefs from %s: %s", _PREFS_PATH, exc)
        _user_prefs = {}


def _save_prefs() -> None:
    try:
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_PATH.write_text(json.dumps(_user_prefs, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to save user prefs to %s: %s", _PREFS_PATH, exc)


def _get_user_simple(user_id: str) -> bool:
    """Return True if the user has enabled persistent simple mode."""
    return bool((_user_prefs.get(user_id) or {}).get("simple", False))


def _set_user_simple(user_id: str, value: bool) -> None:
    """Set (or clear) persistent simple mode for *user_id* and write to disk."""
    if user_id not in _user_prefs:
        _user_prefs[user_id] = {}
    _user_prefs[user_id]["simple"] = value
    _save_prefs()


def _load_personas() -> None:
    global _personas
    try:
        if _PERSONAS_PATH.exists():
            _personas = json.loads(_PERSONAS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to load personas from %s: %s", _PERSONAS_PATH, exc)
        _personas = {}


def _save_personas() -> None:
    try:
        _PERSONAS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERSONAS_PATH.write_text(json.dumps(_personas, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to save personas to %s: %s", _PERSONAS_PATH, exc)


async def _get_user_name(user_id: str, client: Any) -> str:
    """Return the user's preferred display name, resolving from Slack API if needed."""
    stored = (_personas.get(user_id) or {}).get("name")
    if stored:
        return stored
    try:
        result = await client.users_info(user=user_id)
        profile = (result.get("user") or {}).get("profile") or {}
        name = profile.get("display_name") or profile.get("real_name") or ""
        name = name.strip().split()[0] if name.strip() else ""  # first name only
        if name:
            if user_id not in _personas:
                _personas[user_id] = {}
            _personas[user_id]["name"] = name
            _save_personas()
            return name
    except Exception:
        pass
    return "there"


# Load prefs and personas at import time so they are ready before any handler fires.
_load_prefs()
_load_personas()

# ---------------------------------------------------------------------------
# Per-user email credentials (IMAP / Gmail App Password)
# ---------------------------------------------------------------------------
# Stored as: {"U123ABC": {"user": "chuck@gmail.com", "password": "xxxx xxxx xxxx xxxx"}}
# Allows each Slack user to connect their own Gmail. Never committed — local data/ only.
# ---------------------------------------------------------------------------

_USER_EMAIL_CREDS_PATH: Path = Path(__file__).parent.parent / "data" / "user_email_creds.json"
_user_email_creds: dict[str, dict] = {}


def _load_user_email_creds() -> None:
    global _user_email_creds
    try:
        if _USER_EMAIL_CREDS_PATH.exists():
            _user_email_creds = json.loads(_USER_EMAIL_CREDS_PATH.read_text(encoding="utf-8"))
        else:
            _user_email_creds = {}
    except Exception as exc:
        log.warning("Failed to load user email creds: %s", exc)
        _user_email_creds = {}


def _save_user_email_creds() -> None:
    try:
        _USER_EMAIL_CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _USER_EMAIL_CREDS_PATH.write_text(json.dumps(_user_email_creds, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to save user email creds: %s", exc)


# ---------------------------------------------------------------------------
# Per-user Dropbox tokens
# ---------------------------------------------------------------------------
# Stored as: {"U123ABC": {"token": "sl.xxx", "watch_path": "/OpenClaw"}}
# ---------------------------------------------------------------------------

_USER_DROPBOX_PATH: Path = Path(__file__).parent.parent / "data" / "user_dropbox_tokens.json"
_user_dropbox_tokens: dict[str, dict] = {}


def _load_user_dropbox_tokens() -> None:
    global _user_dropbox_tokens
    try:
        if _USER_DROPBOX_PATH.exists():
            _user_dropbox_tokens = json.loads(_USER_DROPBOX_PATH.read_text(encoding="utf-8"))
        else:
            _user_dropbox_tokens = {}
    except Exception as exc:
        log.warning("Failed to load user Dropbox tokens: %s", exc)
        _user_dropbox_tokens = {}


def _save_user_dropbox_tokens() -> None:
    try:
        _USER_DROPBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
        _USER_DROPBOX_PATH.write_text(json.dumps(_user_dropbox_tokens, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to save user Dropbox tokens: %s", exc)


_load_user_email_creds()
_load_user_dropbox_tokens()

# ---------------------------------------------------------------------------
# Per-user file history (persisted to data/slack_file_history.json)
# ---------------------------------------------------------------------------
# Stored as: {"U123": [{"name": "doc.docx", "size": 1234, "sha256": "...",
#   "last_used_ts": 1234567890.0, "mimetype": "..."}, ...]}
# Newest entry first; capped at _FILE_HISTORY_MAX per user.
# ---------------------------------------------------------------------------

_FILE_HISTORY_PATH: Path = Path(__file__).parent.parent / "data" / "slack_file_history.json"
_file_history: dict[str, list[dict]] = {}
_FILE_HISTORY_MAX = 20


def _load_file_history() -> None:
    global _file_history
    try:
        if _FILE_HISTORY_PATH.exists():
            _file_history = json.loads(_FILE_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to load file history: %s", exc)
        _file_history = {}


def _save_file_history() -> None:
    try:
        _FILE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FILE_HISTORY_PATH.write_text(json.dumps(_file_history, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to save file history: %s", exc)


def _record_file_history(user_id: str, file_obj: dict, file_bytes: bytes | None = None) -> None:
    """Add or update a file in the user's history. Keeps newest _FILE_HISTORY_MAX."""
    import hashlib
    import time as _time

    name = file_obj.get("name", "")
    size = file_obj.get("size", 0)
    sha256 = ""
    if file_bytes:
        sha256 = hashlib.sha256(file_bytes).hexdigest()[:16]
    entry = {
        "name": name,
        "size": size,
        "sha256": sha256,
        "last_used_ts": _time.time(),
        "mimetype": file_obj.get("mimetype", ""),
    }
    history = _file_history.setdefault(user_id, [])
    history[:] = [h for h in history if h.get("name") != name]
    history.insert(0, entry)
    history[:] = history[:_FILE_HISTORY_MAX]
    _save_file_history()


_load_file_history()

# ---------------------------------------------------------------------------
# Onboarding state
# ---------------------------------------------------------------------------
_onboarded_users: set[str] = set()  # users who have received onboarding or used the bot


def _match_question_to_history(user_id: str, question: str) -> dict | None:
    """Return the best-matching file from history for this question, or None.

    Matches on filename keywords (minus extension). Returns the most recently
    used match if any keyword appears in the question text (case-insensitive).
    """
    history = _file_history.get(user_id, [])
    if not history:
        return None
    question_lower = question.lower()
    for entry in history:  # history is newest-first
        name = entry.get("name", "")
        stem = Path(name).stem.lower().replace("_", " ").replace("-", " ")
        keywords = [w for w in stem.split() if len(w) > 3]
        if any(kw in question_lower for kw in keywords):
            return entry
    return None


async def _check_new_user_onboarding(user_id: str, client: Any) -> None:
    """Send a welcome DM to a new user after a delay if they haven't interacted."""
    delay = int(os.environ.get("OPENCLAW_ONBOARDING_DELAY_SECS", "60"))
    if user_id in _onboarded_users:
        return
    _onboarded_users.add(user_id)
    await asyncio.sleep(delay)
    # After delay, check if they've actually used the bot (prefs exist or history exists)
    if _user_prefs.get(user_id) or _file_history.get(user_id):
        return  # they've already used it, skip
    name = await _get_user_name(user_id, client)
    try:
        await client.chat_postMessage(
            channel=user_id,
            text=f"Hi {name}! " + _WELCOME_MESSAGE,
        )
        log.info("Sent onboarding DM to new user %s", user_id)
    except Exception as exc:
        log.debug("Onboarding DM failed for %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# File attachment support
# NOTE: The Slack app requires the `files:read` Bot Token Scope to download
# private file URLs. Add files:read in the Slack app manifest if not present.
# ---------------------------------------------------------------------------

_slack_dl_sessions = SessionManager(timeout=30, name="slack_attachments")


# ---------------------------------------------------------------------------

_RE_RECOVERY_BLOCKQUOTE = re.compile(
    r"\n{1,2}> ℹ️ \*\*Recovery note:\*\*\n(?:> [^\n]*\n?)*",
    re.MULTILINE,
)
_RE_RECOVERY_PLAIN = re.compile(
    r"\n{1,2}[ℹ️:information_source:]\s*\*?\*?Recovery note\*?\*?:?[^\n]*\n(?:[^\n]*\n?){0,6}",
    re.MULTILINE,
)
_RE_VIA_TRAILER = re.compile(r"\n_via [^\n]+_[ \t]*(?=\n|$)")
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_RE_H = re.compile(r"^#{1,3} (.+)$", re.MULTILINE)
_RE_HR = re.compile(r"^[-─*]{3,}$", re.MULTILINE)


def _clean_for_slack(text: str) -> str:
    """Strip recovery notes / CLI footers and convert markdown to Slack mrkdwn."""
    # Remove recovery note blocks (blockquote and plain variants)
    text = _RE_RECOVERY_BLOCKQUOTE.sub("", text)
    text = _RE_RECOVERY_PLAIN.sub("", text)

    # Strip _via model_ attribution line
    text = _RE_VIA_TRAILER.sub("", text)

    # Convert markdown links [text](url) → <url|text>
    text = _RE_MD_LINK.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", text)

    # Convert **bold** → *bold* (Slack mrkdwn)
    text = _RE_BOLD.sub(lambda m: f"*{m.group(1)}*", text)

    # Demote ATX headers (#, ##, ###) to bold lines
    text = _RE_H.sub(lambda m: f"*{m.group(1)}*", text)

    # Remove horizontal rules
    text = _RE_HR.sub("", text)

    return text.strip()


def _suggest_actions_for_file(filename: str, mimetype: str) -> str:
    """Return friendly action suggestions based on file type."""
    name_lower = filename.lower()
    mime_lower = mimetype.lower()

    if name_lower.endswith((".docx", ".doc")) or "word" in mime_lower or "wordprocessingml" in mime_lower:
        return (
            f"📄 I see you uploaded *{filename}*. What would you like?\n"
            "• Proofread and fix grammar\n"
            "• Make it more professional / formal\n"
            "• Summarize in bullet points\n"
            "• Rewrite a specific section\n\n"
            "_Just reply and tell me what you need!_"
        )
    if name_lower.endswith((".xlsx", ".xls", ".csv")) or "spreadsheet" in mime_lower or "excel" in mime_lower:
        return (
            f"📊 I see you uploaded *{filename}*. What would you like?\n"
            "• Summarize what this is tracking\n"
            "• Explain any formulas or columns\n"
            "• Find errors or unusual values\n"
            "• Create a summary paragraph\n\n"
            "_Just reply and tell me what you need!_"
        )
    if name_lower.endswith(".pdf") or mime_lower == "application/pdf":
        return (
            f"📑 I see you uploaded *{filename}*. What would you like?\n"
            "• Summarize the key points\n"
            "• Extract action items\n"
            "• Answer a specific question about it\n\n"
            "_Just reply and tell me what you need!_"
        )
    if mime_lower.startswith("image/"):
        return (
            f"🖼️ I see you uploaded *{filename}*. What would you like?\n"
            "• Describe what's in the image\n"
            "• Read any text visible in the photo\n"
            "• Answer a question about it\n\n"
            "_Just reply and tell me what you need!_"
        )
    return (
        f"📎 I see you uploaded *{filename}*. What would you like me to do with it?\n"
        "_Just tell me and I'll get started!_"
    )


# ------------------------------------------------------------------
# Model selector  --model <alias>
# ------------------------------------------------------------------
_MODEL_FLAG_RE = re.compile(r"--model\s+(\S+)", re.IGNORECASE)
_MODEL_ALIASES: dict[str, str] = {
    "auto": "auto",
    "gemini": "gemini",
    "openai": "openai",
    "gpt": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "copilot": "copilot",
}

_WELCOME_MESSAGE = (
    "👋 *Hi! I'm OpenClaw — your personal AI assistant, right here in Slack.*\n\n"
    "Here are some things you can ask me right now:\n"
    '• "What should I make for dinner tonight with chicken and vegetables?" 🍳\n'
    '• "Help me write a message to my doctor about rescheduling my appointment" ✍️\n'
    '• "What does this letter say?" *(then drop in a photo or PDF)* 📄\n'
    '• "Look at this spreadsheet and tell me what it shows" *(drop in an Excel file)* 📊\n'
    '• "What\'s a good birthday gift for someone who likes gardening?" 🎁\n'
    '• "Explain this in plain English" *(paste in any confusing text)* 🤔\n\n'
    "*You don't need any special commands — just talk to me like you'd talk to a friend!*\n"
    "Type `/help` anytime to see more examples."
)

_HELP_TEXT = (
    "*📚 OpenClaw Quick Help*\n\n"
    "*Working with files:*\n"
    '• Drag in a Word doc (.docx) → "proofread this" / "make this more formal" / "summarize in 5 bullet points"\n'
    '• Drag in an Excel file (.xlsx) → "what is this tracking?" / "explain column C" / "find any errors"\n'
    '• Drag in a PDF → "summarize this"\n'
    '• Drop in a photo → "what\'s in this image?"\n\n'
    "*Just chatting:*\n"
    '• Ask anything — "what\'s the weather in Boston?" / "explain this email to me" / "help me write a thank-you note"\n\n'
    "*Tips:*\n"
    "• `/simple on` — always get plain, easy-to-read answers (no need to type `--simple` every time)\n"
    "• Add `--simple` to any one message for a one-off plain answer\n"
    "• Reply in a thread to keep context from earlier messages\n\n"
    '_Example: Upload Budget2025.xlsx and type: "summarize the totals for me"_'
)

_SIMPLE_FLAG_RE = re.compile(r"\s*--simple\b", re.IGNORECASE)
_SIMPLE_SYSTEM_PREFIX = (
    "Please respond in plain, simple language. Avoid jargon and technical terms. "
    "Use short sentences. Write as if explaining to someone who is not technical. "
)

_BROWSER_NAV_PATTERNS = re.compile(
    r"""(?ix)
    \b(?:go\s+to|navigate\s+to|visit|open|browse\s+to|
        extract\s+from|get\s+content\s+from|read\s+page\s+at|
        fetch\s+page\s+at|scrape)\s+
    (https?://[^\s>]+)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SCREENSHOT_PATTERNS = re.compile(
    r"""(?ix)
    (?:take\s+a?\s*|grab\s+a?\s*|capture\s+a?\s*|get\s+a?\s*)?
    screenshot\s+(?:of\s+|from\s+)?
    (https?://\S+|\S+\.\S+)
    |
    (?:screenshot|capture)\s+this\s+(?:page|site|url|website)?:?\s*
    (https?://\S+|\S+\.\S+)
    |
    show\s+me\s+(?:what\s+)?
    (https?://\S+|\S+\.\S+)
    \s*(?:looks?\s+like|appearance|screenshot)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ------------------------------------------------------------------
# Bot message registry for 👍/👎 feedback
# key: (channel, message_ts)  value: originating user_id
# ------------------------------------------------------------------
_bot_message_registry: dict[tuple[str, str], str] = {}

# Wave 8: retry cache for error recovery UX
_retry_cache: dict[str, str] = {}
_RETRY_CACHE_MAX: int = 50

# Populated once after the Slack client performs auth.test
_BOT_USER_ID: str = ""

# ---------------------------------------------------------------------------
# Observability — wave 4 tracking vars
# ---------------------------------------------------------------------------
_BOT_START_TIME: float = 0.0
_model_last_success: dict[str, float] = {}
_daily_query_count: int = 0
_error_window: list[float] = []
_last_alert_ts: float = 0.0

# ---------------------------------------------------------------------------
# Feature flag check
# ---------------------------------------------------------------------------

SLACK_ENABLED = os.getenv("SLACK_ENABLED", "false").lower() == "true"
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")
# Wave 14: User token for channel management (xoxp-...) — optional
SLACK_USER_TOKEN = os.getenv("SLACK_USER_TOKEN", "")

# Wave 4: upload endpoint + proactive alerts
OPENCLAW_UPLOAD_KEY = os.getenv("OPENCLAW_UPLOAD_KEY", "")
OPENCLAW_UPLOAD_PORT = int(os.getenv("OPENCLAW_UPLOAD_PORT", "8080"))
SLACK_NOTIFY_USER_ID = os.getenv("SLACK_NOTIFY_USER_ID", "")
_slack_client_ref: Any = None  # set at startup for use in upload handler
_AI_FILES_DIR = Path(os.getenv("AI_FILES_DIR", "/ai-files"))
_KNOWN_FILES_PATH = Path(__file__).parent.parent / "data" / "known_files.json"
_LAST_SYNC_PATH = Path(__file__).parent.parent / "data" / "last_sync.json"
_FILE_POLL_INTERVAL = int(os.getenv("OPENCLAW_FILE_POLL_INTERVAL", "60"))

# --- Wave 5: digest ---
_DIGEST_PREFS_PATH = Path(__file__).parent.parent / "data" / "digest_prefs.json"
_DIGEST_CHECK_INTERVAL: int = int(os.getenv("DIGEST_CHECK_INTERVAL", "3600"))  # check every hour
_DIGEST_LOOKBACK_HOURS: int = int(os.getenv("DIGEST_LOOKBACK_HOURS", "24"))  # show files modified in last N hours

# --- /incident: incident-copilot bridge (mirrors Discord IncidentCog) ---
# Allowed users defaults to the proactive-alert owner when not explicitly set.
OPENCLAW_INCIDENT_ALLOWED_USERS = os.getenv("OPENCLAW_INCIDENT_ALLOWED_USERS", "")
# Cached action list per incident id; tuple of (created_at_monotonic, actions).
# In-memory only — incidents themselves are durable in incident_store SQLite.
_incident_actions_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_INCIDENT_CACHE_TTL_S = int(os.getenv("OPENCLAW_INCIDENT_CACHE_TTL_S", "1800"))
_INCIDENT_REPORT_TIMEOUT_S = int(os.getenv("OPENCLAW_INCIDENT_REPORT_TIMEOUT_S", "45"))


def _incident_allowed_user_ids() -> set[str]:
    """Resolve the set of Slack user IDs allowed to invoke /incident.

    Falls back to SLACK_NOTIFY_USER_ID (the proactive-alert owner) when
    OPENCLAW_INCIDENT_ALLOWED_USERS is unset. Returns empty set when neither
    is configured — handler will refuse all callers in that case.
    """
    raw = OPENCLAW_INCIDENT_ALLOWED_USERS or SLACK_NOTIFY_USER_ID
    return {part.strip() for part in raw.split(",") if part.strip()}


def _incident_cache_put(incident_id: int, actions: list[dict[str, Any]]) -> None:
    _incident_actions_cache[incident_id] = (time.monotonic(), list(actions))
    # Opportunistic cleanup so the dict can't grow unboundedly across long uptimes.
    cutoff = time.monotonic() - _INCIDENT_CACHE_TTL_S
    stale = [k for k, (ts, _) in _incident_actions_cache.items() if ts < cutoff]
    for k in stale:
        _incident_actions_cache.pop(k, None)


def _incident_cache_get(incident_id: int) -> list[dict[str, Any]] | None:
    entry = _incident_actions_cache.get(incident_id)
    if not entry:
        return None
    ts, actions = entry
    if time.monotonic() - ts > _INCIDENT_CACHE_TTL_S:
        _incident_actions_cache.pop(incident_id, None)
        return None
    return actions


def _parse_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _incident_action_blocks(
    incident_id: int,
    summary: str,
    causes: list[str],
    actions: list[dict[str, Any]],
    model_used: str,
    error: str | None = None,
) -> list[dict[str, Any]]:
    """Build Slack Block Kit blocks for an incident Copilot report."""
    blocks: list[dict[str, Any]] = []
    header_lines = [f"🚨 *Incident #{incident_id}* — Copilot report (model: `{model_used}`)"]
    if error:
        header_lines.append(f"⚠️ _{error}_")
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(header_lines)}})
    if summary:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Summary:*\n{summary[:2800]}"}})
    if causes:
        cause_text = "\n".join(f"• {c}" for c in causes[:5])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Suspected causes:*\n{cause_text}"}})
    if actions:
        action_lines = []
        buttons: list[dict[str, Any]] = []
        for idx, action in enumerate(actions[:3]):
            title = str(action.get("title", "Action"))[:120]
            desc = str(action.get("description", ""))[:200]
            risk = str(action.get("risk_level", "high")).upper()
            executable = bool(action.get("executable"))
            tag = "🟢 executable" if executable else "ℹ️ recommendation"
            action_lines.append(f"*{idx + 1}. {title}* — `{risk}` {tag}\n{desc}")
            if executable:
                buttons.append(
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": f"Run #{idx + 1}: {title[:60]}"},
                        "style": "primary" if risk in {"LOW", "MEDIUM"} else "danger",
                        "action_id": "incident_action_run",
                        "value": json.dumps({"id": incident_id, "idx": idx}),
                    }
                )
        if action_lines:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "*Proposed actions:*\n\n" + "\n\n".join(action_lines)}}
            )
        if buttons:
            blocks.append({"type": "actions", "elements": buttons})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"`/incident status {incident_id}` · `/incident timeline {incident_id}` · `/incident resolve {incident_id}`",
                }
            ],
        }
    )
    return blocks


async def _run_incident_start(*, client: Any, channel_id: str, user_id: str, title: str) -> None:
    """Create an incident, run the Copilot report, and post action buttons.

    Shared between the /incident slash command and tests. Returns nothing;
    user feedback is delivered via Slack messages.
    """
    from incident_copilot import generate_incident_report  # lazy
    from incident_workflows import incident_store  # lazy

    try:
        incident = incident_store.create_incident(
            title=title[:200],
            severity="high",
            description="",
            channel_id=None,
            channel_name=channel_id,
            thread_id=None,
            thread_name=None,
            created_by=None,
            created_by_name=user_id,
        )
    except Exception as exc:  # broad: surface to user
        log.warning("/incident start: create_incident failed: %s", exc)
        await client.chat_postEphemeral(
            channel=channel_id, user=user_id, text=f"❌ Failed to create incident: `{exc}`"
        )
        return

    incident_id = int(incident.get("id", 0))
    stub_ts: str | None = None
    try:
        stub = await client.chat_postMessage(
            channel=channel_id,
            text=f"🚨 Incident #{incident_id} started — gathering telemetry and drafting Copilot actions…",
        )
        stub_ts = (stub or {}).get("ts")
    except Exception as exc:  # broad: stub failure must not abort the report
        log.warning("/incident start: stub chat_postMessage failed: %s", exc)

    error: str | None = None
    summary = ""
    causes: list[str] = []
    actions: list[dict[str, Any]] = []
    model_used = "unknown"
    try:
        report = await asyncio.wait_for(
            generate_incident_report(incident, requested_services=""),
            timeout=_INCIDENT_REPORT_TIMEOUT_S,
        )
        summary = str(report.get("summary", "")).strip()
        causes = [str(item) for item in report.get("suspected_causes", []) if str(item).strip()]
        actions = [item for item in report.get("actions", []) if isinstance(item, dict)]
        model_used = str(report.get("model_used", "unknown"))[:80]
    except asyncio.TimeoutError:
        error = "Incident Copilot timed out gathering telemetry."
        model_used = "timeout"
        log.warning("Incident Copilot timed out for incident #%s", incident_id)
    except Exception as exc:  # broad: intentional
        error = f"Incident Copilot was unavailable: {exc}"
        model_used = "error"
        log.warning("Incident Copilot failed for incident #%s: %s", incident_id, exc)

    # Persist Copilot output to the incident timeline (parity with Discord cog).
    try:
        incident_store.append_event(
            incident_id,
            event_type="copilot_summary",
            note=json.dumps({"summary": summary, "causes": causes, "model": model_used}),
            actor_id=None,
            actor_name=user_id,
        )
        incident_store.append_event(
            incident_id,
            event_type="copilot_actions",
            note=json.dumps(
                {
                    "actions": [
                        {k: a.get(k) for k in ("title", "command", "target", "risk_level", "executable")}
                        for a in actions[:3]
                    ]
                }
            ),
            actor_id=None,
            actor_name=user_id,
        )
    except Exception as exc:  # broad: telemetry failure must not break user flow
        log.warning("/incident start: append_event failed: %s", exc)

    _incident_cache_put(incident_id, actions)
    blocks = _incident_action_blocks(incident_id, summary, causes, actions, model_used, error=error)
    final_text = f"🚨 Incident #{incident_id}: {summary[:120] or title[:120]}"
    try:
        if stub_ts:
            await client.chat_update(channel=channel_id, ts=stub_ts, text=final_text, blocks=blocks)
        else:
            await client.chat_postMessage(channel=channel_id, text=final_text, blocks=blocks)
    except Exception as exc:  # broad: fall back to a plain post
        log.warning("/incident start: chat_update failed (%s); posting fresh message", exc)
        await client.chat_postMessage(channel=channel_id, text=final_text, blocks=blocks)


def _parse_action_button_value(raw: str) -> tuple[int, int] | None:
    """Parse the JSON `value` on an incident action button into (incident_id, idx)."""
    try:
        payload = json.loads(raw or "{}")
        return int(payload["id"]), int(payload["idx"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


_CASUAL_TIPS = [
    "\n\n💡 _Tip: You can just talk to me naturally — no commands needed!_",
    "\n\n💡 _Tip: Drop a photo or file into Slack anytime and ask me about it._",
    '\n\n💡 _Tip: Not happy with my answer? Just say "try again" or "make it shorter"._',
    "\n\n💡 _Tip: You can ask me anything — cooking, writing, explaining something — just like texting a friend._",
    "\n\n💡 _Tip: Type `/help` to see example questions you can ask me._",
]

_MONTHLY_TIP_MESSAGE = (
    "👋 *Monthly tip from OpenClaw!*\n\n"
    "Here are 3 things you might not have tried yet:\n\n"
    "1️⃣ *Drop in a photo of any document* — a letter, receipt, or handwritten note — and ask me to read it.\n"
    "2️⃣ *Ask me to write something for you* — a thank-you note, a message to a teacher, anything at all.\n"
    "3️⃣ *Reply in a thread* to keep our conversation going — I'll remember what we were talking about.\n\n"
    "_You'll get one of these friendly reminders once a month. Just ignore it if you already knew all this! 😊_"
)

_MONTHLY_TIP_INTERVAL: int = 30 * 24 * 3600  # 30 days in seconds


def _maybe_append_tip() -> str:
    """Return a casual tip string 1-in-12 calls, otherwise empty string."""
    if random.randint(1, 12) == 1:
        return random.choice(_CASUAL_TIPS)
    return ""


# --- Wave 5: templates ---
_DATA_DIR = Path(__file__).parent.parent / "data"
_TEMPLATES_DIR = _DATA_DIR / "templates"

# --- Wave 10: Dropbox sync ---
_DROPBOX_TOKEN: str | None = os.getenv("DROPBOX_APP_TOKEN")
_DROPBOX_FOLDER: str = os.getenv("DROPBOX_WATCH_FOLDER", "/Family AI")
_DROPBOX_NOTIFY_CHANNEL: str | None = os.getenv("DROPBOX_NOTIFY_CHANNEL")
_DROPBOX_CACHE_DIR: Path = _DATA_DIR / "dropbox_cache"
_DROPBOX_CURSOR_PATH: Path = _DATA_DIR / "dropbox_cursor.json"
_DROPBOX_VIRTUAL_USER: str = "dropbox_sync"

# --- Wave 12: Dropbox OAuth2 (per-user one-click connect) ---
_DROPBOX_APP_KEY: str = os.getenv("DROPBOX_APP_KEY", "")
_DROPBOX_APP_SECRET: str = os.getenv("DROPBOX_APP_SECRET", "")
# Public URL of this server (e.g. http://192.168.1.100:8080) — used as OAuth2 redirect base.
_OPENCLAW_PUBLIC_URL: str = os.getenv("OPENCLAW_PUBLIC_URL", "").rstrip("/")
# In-memory map of state_token → slack_user_id (ephemeral CSRF protection)
_dropbox_oauth_states: dict[str, str] = {}

# --- Wave 10 Yoda: Google Calendar OAuth ---
_GOOGLE_CLIENT_ID: str | None = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
_GOOGLE_CLIENT_SECRET: str | None = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
_GOOGLE_REFRESH_TOKEN: str | None = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN")
_google_token_cache: dict[str, Any] = {}  # {access_token, expires_at}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Wave 4: /upload HTTP endpoint helpers
# ---------------------------------------------------------------------------

_ALLOWED_UPLOAD_EXTENSIONS = {".docx", ".xlsx", ".pdf", ".txt", ".csv"}


async def _handle_upload(request: "aiohttp.web.Request") -> "aiohttp.web.Response":
    """Handle POST /upload — accept a file and write it to /ai-files/."""
    from aiohttp import web

    # Authenticate via shared secret header
    provided_key = request.headers.get("X-OpenClaw-Key", "")
    if OPENCLAW_UPLOAD_KEY and provided_key != OPENCLAW_UPLOAD_KEY:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        reader = await request.multipart()
    except Exception:
        return web.json_response({"error": "Expected multipart/form-data"}, status=400)

    field = await reader.next()
    if field is None or field.name != "file":
        return web.json_response({"error": "Missing 'file' field"}, status=400)

    filename = field.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        return web.json_response(
            {"error": f"Extension '{ext}' not allowed. Supported: {sorted(_ALLOWED_UPLOAD_EXTENSIONS)}"},
            status=415,
        )

    _AI_FILES_DIR.mkdir(parents=True, exist_ok=True)
    dest = _AI_FILES_DIR / Path(filename).name
    data = await field.read(decode=True)
    dest.write_bytes(data)
    log.info("Upload: wrote %d bytes to %s", len(data), dest)

    if _slack_client_ref and SLACK_NOTIFY_USER_ID:
        asyncio.create_task(_notify_upload_received(dest.name, len(data)))

    return web.json_response({"status": "ok", "filename": dest.name, "size": len(data)})


async def _handle_dropbox_oauth_callback(request: "aiohttp.web.Request") -> "aiohttp.web.Response":
    """Handle GET /dropbox/callback — complete the Dropbox OAuth2 flow."""
    from aiohttp import web

    _HTML_OK = (
        "<html><head><title>OpenClaw</title></head><body style='font-family:sans-serif;text-align:center;padding:60px'>"
        "<h2>✅ Dropbox connected!</h2>"
        "<p>You can close this window and return to Slack.</p>"
        "</body></html>"
    )
    _HTML_ERR = (
        "<html><head><title>OpenClaw</title></head><body style='font-family:sans-serif;text-align:center;padding:60px'>"
        "<h2>❌ Something went wrong</h2>"
        "<p>{msg}</p><p>Try <code>/dropbox connect</code> in Slack again.</p>"
        "</body></html>"
    )

    error = request.rel_url.query.get("error", "")
    if error:
        return web.Response(
            text=_HTML_ERR.format(msg=f"Dropbox declined: {error}"),
            content_type="text/html",
            status=400,
        )

    code = request.rel_url.query.get("code", "")
    state = request.rel_url.query.get("state", "")

    if not code or not state:
        return web.Response(
            text=_HTML_ERR.format(msg="Missing code or state parameter."),
            content_type="text/html",
            status=400,
        )

    user_id = _dropbox_oauth_states.pop(state, None)
    if not user_id:
        return web.Response(
            text=_HTML_ERR.format(msg="Session expired or invalid. Please try again."),
            content_type="text/html",
            status=400,
        )

    # Exchange authorization code for an access token
    redirect_uri = f"{_OPENCLAW_PUBLIC_URL}/dropbox/callback"
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                "https://api.dropboxapi.com/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                auth=aiohttp.BasicAuth(_DROPBOX_APP_KEY, _DROPBOX_APP_SECRET),
                timeout=aiohttp.ClientTimeout(total=15),
            )
            token_data = await resp.json()
    except Exception as exc:
        log.error("Dropbox token exchange failed: %s", exc)
        return web.Response(
            text=_HTML_ERR.format(msg="Could not reach Dropbox to complete auth."),
            content_type="text/html",
            status=502,
        )

    access_token = token_data.get("access_token", "")
    if not access_token:
        err_desc = token_data.get("error_description", token_data.get("error", "unknown"))
        log.error("Dropbox token exchange error: %s", err_desc)
        return web.Response(
            text=_HTML_ERR.format(msg=f"Dropbox auth error: {err_desc}"),
            content_type="text/html",
            status=400,
        )

    # Persist per-user token (preserve existing watch_path if set)
    existing = _user_dropbox_tokens.get(user_id, {})
    _user_dropbox_tokens[user_id] = {
        "token": access_token,
        "watch_path": existing.get("watch_path", "/OpenClaw"),
    }
    _save_user_dropbox_tokens()
    log.info("Dropbox OAuth: stored token for Slack user %s", user_id)

    # DM the user in Slack to confirm
    if SLACK_BOT_TOKEN:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    "https://slack.com/api/chat.postMessage",
                    json={
                        "channel": user_id,
                        "text": (
                            "✅ *Your Dropbox is now connected!*\n\n"
                            "Try `/dropbox list` to see your files, or drop a file into your "
                            "*OpenClaw* Dropbox folder and OpenClaw will notice it automatically.\n\n"
                            "To disconnect later, type `/dropbox forget`."
                        ),
                    },
                    headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
        except Exception as exc:
            log.warning("Failed to DM user after Dropbox connect: %s", exc)

    return web.Response(text=_HTML_OK, content_type="text/html")


async def _run_upload_server() -> None:
    """Start the aiohttp upload HTTP server on OPENCLAW_UPLOAD_PORT."""
    try:
        from aiohttp import web
    except ImportError:
        log.warning("aiohttp not available — upload server not started")
        return

    upload_app = web.Application()
    upload_app.router.add_post("/upload", _handle_upload)
    upload_app.router.add_get("/health", lambda _req: web.json_response({"status": "ok"}))
    upload_app.router.add_get("/dropbox/callback", _handle_dropbox_oauth_callback)

    runner = web.AppRunner(upload_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", OPENCLAW_UPLOAD_PORT)
    await site.start()
    log.info("Upload server listening on port %d", OPENCLAW_UPLOAD_PORT)


# ---------------------------------------------------------------------------
# Wave 4: Proactive file-alert helpers
# ---------------------------------------------------------------------------


def _load_known_files() -> set[str]:
    """Load the set of known filenames from disk."""
    try:
        if _KNOWN_FILES_PATH.exists():
            data = json.loads(_KNOWN_FILES_PATH.read_text(encoding="utf-8"))
            return set(data.get("files", []))
    except Exception as exc:
        log.warning("Could not load known_files.json: %s", exc)
    return set()


def _save_known_files(known: set[str]) -> None:
    try:
        _KNOWN_FILES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _KNOWN_FILES_PATH.write_text(json.dumps({"files": sorted(known)}, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Could not save known_files.json: %s", exc)


def _human_time(ts: float) -> str:
    """Return a human-readable relative time string."""
    delta = time.time() - ts
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _load_digest_prefs() -> dict[str, dict]:
    """Load per-user digest preferences from disk."""
    try:
        if _DIGEST_PREFS_PATH.exists():
            return json.loads(_DIGEST_PREFS_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_digest_prefs(prefs: dict[str, dict]) -> None:
    """Persist per-user digest preferences to disk."""
    try:
        _DIGEST_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DIGEST_PREFS_PATH.write_text(json.dumps(prefs, indent=2))
    except Exception as exc:
        log.warning("_save_digest_prefs: %s", exc)


async def _digest_loop(client: Any) -> None:
    """Background task: DM enabled users with a digest of recent files."""
    log.info("Digest loop started (check every %ds, lookback %dh)", _DIGEST_CHECK_INTERVAL, _DIGEST_LOOKBACK_HOURS)
    while True:
        await asyncio.sleep(_DIGEST_CHECK_INTERVAL)
        try:
            prefs = _load_digest_prefs()
            now = time.time()
            cutoff = now - (_DIGEST_LOOKBACK_HOURS * 3600)
            recent: list[tuple[str, float]] = []
            if _AI_FILES_DIR.exists():
                for f in _AI_FILES_DIR.iterdir():
                    if f.is_file() and f.stat().st_mtime >= cutoff:
                        recent.append((f.name, f.stat().st_mtime))
            recent.sort(key=lambda x: x[1], reverse=True)

            for user_id, pref in prefs.items():
                if not pref.get("enabled"):
                    continue

                # Monthly tips nudge
                last_monthly_tip = pref.get("last_monthly_tip", 0)
                if now - last_monthly_tip >= _MONTHLY_TIP_INTERVAL:
                    try:
                        await client.chat_postMessage(channel=user_id, text=_MONTHLY_TIP_MESSAGE)
                        prefs[user_id]["last_monthly_tip"] = now
                        log.info("Sent monthly tip to %s", user_id)
                    except Exception as exc:
                        log.warning("_digest_loop: failed to send monthly tip to %s: %s", user_id, exc)

                last_sent = pref.get("last_sent", 0)
                if now - last_sent < (_DIGEST_LOOKBACK_HOURS * 3600 * 0.9):
                    continue
                if not recent:
                    continue
                lines = [f"• *{name}* — {_human_time(mtime)}" for name, mtime in recent[:10]]
                text = (
                    f"📊 *Your {_DIGEST_LOOKBACK_HOURS}h digest* — {len(recent)} file(s) updated\n\n"
                    + "\n".join(lines)
                    + "\n\n_Reply with a filename to work with it, or type `/digest off` to unsubscribe._"
                )
                try:
                    await client.chat_postMessage(channel=user_id, text=text)
                    prefs[user_id]["last_sent"] = now
                    log.info("Sent digest to %s (%d files)", user_id, len(recent))
                except Exception as exc:
                    log.warning("_digest_loop: failed to DM %s: %s", user_id, exc)
            _save_digest_prefs(prefs)
        except Exception as exc:
            log.warning("_digest_loop: error: %s", exc)


async def _file_alert_loop(client: Any) -> None:
    """Background task: poll /ai-files for new files and DM the notify user."""
    if not SLACK_NOTIFY_USER_ID:
        log.info("SLACK_NOTIFY_USER_ID not set — proactive file alerts disabled")
        return

    known = _load_known_files()

    while True:
        await asyncio.sleep(_FILE_POLL_INTERVAL)
        try:
            if not _AI_FILES_DIR.exists():
                continue

            current = {
                f.name
                for f in _AI_FILES_DIR.iterdir()
                if f.is_file() and f.suffix.lower() in _ALLOWED_UPLOAD_EXTENSIONS
            }
            new_files = current - known
            if new_files:
                for fname in sorted(new_files):
                    await _send_file_alert(client, fname)
                known = current
                _save_known_files(known)
        except Exception as exc:
            log.warning("file_alert_loop: error during poll: %s", exc)


async def _notify_upload_received(filename: str, size: int) -> None:
    """DM the notify user when a file is uploaded via the HTTP endpoint."""
    if size > 1024:
        size_human = f"{size // 1024} KB"
    else:
        size_human = f"{size} bytes"
    try:
        await _slack_client_ref.chat_postMessage(
            channel=SLACK_NOTIFY_USER_ID,
            text=(
                f"📥 *Got it!* I received `{filename}` ({size_human}). "
                'Just ask me what to do with it — for example: "summarize this" or "what\'s in this file?"'
            ),
        )
    except Exception as exc:
        log.debug("_notify_upload_received: failed to DM %s: %s", SLACK_NOTIFY_USER_ID, exc)


async def _send_file_alert(client: Any, filename: str) -> None:
    """DM the notify user about a newly detected file with Block Kit action buttons."""
    mimetype = _mimetype_for(filename)
    synthetic_id = f"aifiles::{filename}"
    synthetic_obj = {
        "id": synthetic_id,
        "name": filename,
        "mimetype": mimetype,
        "size": 0,
        "url_private": None,
        "ai_files_path": str(_AI_FILES_DIR / filename),
    }
    _register_file(synthetic_id, synthetic_obj)

    blocks = _build_file_blocks(
        filename=filename,
        description="📥 New file synced to OpenClaw — what would you like to do?",
        mimetype=mimetype,
        file_id=synthetic_id,
    )
    try:
        await client.chat_postMessage(
            channel=SLACK_NOTIFY_USER_ID,
            blocks=blocks,
            text=f"📄 New file synced: *{filename}*",
        )
        log.info("Sent file alert to %s for %s", SLACK_NOTIFY_USER_ID, filename)
    except Exception as exc:
        log.warning("_send_file_alert: failed to DM %s: %s", SLACK_NOTIFY_USER_ID, exc)


async def _process_slack_files(files: list[dict], token: str, question: str) -> str:
    """Download and incorporate Slack file attachments into *question*.

    Supports:
    - Images (mimetype image/*): analyzed via Gemini vision (llm_analyze_image)
    - Text/docs (text/*, application/pdf, application/vnd.*, etc.): decoded as
      UTF-8 and appended, capped at ATTACHMENT_TEXT_MAX_CHARS chars
    - Other: a note about unsupported type is appended

    Requires ``files:read`` scope on the Slack bot token.
    """
    for file in files:
        url = file.get("url_private_download") or file.get("url_private")
        if not url:
            continue

        filename = file.get("name", "unknown")
        mimetype = (file.get("mimetype") or "").lower()

        try:
            session = await _slack_dl_sessions.get()
            headers = {"Authorization": f"Bearer {token}"}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    log.warning("Failed to download Slack file %s: HTTP %d", filename, resp.status)
                    question += f"\n\n[Attachment: failed to download {filename}]"
                    continue

                data = await resp.read()

            if mimetype.startswith("image/"):
                # OCR intent: extract text verbatim instead of describing the image
                from ocr_skill import is_ocr_request, ocr_file  # lazy import

                if is_ocr_request(question):
                    ocr_text = await ocr_file(data, mimetype, hint="")
                    question = f"{question}\n\n[OCR result for {filename}]:\n{ocr_text}"
                else:
                    image_answer = await llm_analyze_image(data, mimetype, question)
                    question = f"{question}\n\n[Image attachment analysis: {image_answer}]"
            elif mimetype == "application/pdf":
                # OCR intent on PDF: extract text layer (or ask user to send as image)
                from ocr_skill import is_ocr_request, ocr_file  # lazy import

                if is_ocr_request(question):
                    ocr_text = await ocr_file(data, mimetype, hint="")
                    question = f"{question}\n\n[OCR result for {filename}]:\n{ocr_text}"
                else:
                    doc_text = data.decode("utf-8", errors="replace")[:ATTACHMENT_TEXT_MAX_CHARS]
                    question = (
                        f"{question}\n\n--- Attached Document: {filename} "
                        f"(first {ATTACHMENT_TEXT_MAX_CHARS} chars) ---\n"
                        f"{doc_text}\n"
                        f"--- End Document ---"
                    )
            elif (
                mimetype.startswith("text/")
                or mimetype in ("application/json",)
                or mimetype.startswith("application/vnd.")
            ):
                doc_text = data.decode("utf-8", errors="replace")[:ATTACHMENT_TEXT_MAX_CHARS]
                question = (
                    f"{question}\n\n--- Attached Document: {filename} "
                    f"(first {ATTACHMENT_TEXT_MAX_CHARS} chars) ---\n"
                    f"{doc_text}\n"
                    f"--- End Document ---"
                )
            elif mimetype.startswith("audio/"):
                question += (
                    f"\n\n[🎵 Audio file detected: {filename} — audio transcription is not yet supported. "
                    "Please describe what you need help with in text!]"
                )
            else:
                question += f"\n\n[Attachment: unsupported file type {filename} ({mimetype})]"

        except Exception as exc:
            log.warning("Failed to process Slack file %s: %s", filename, exc)
            question += f"\n\n[Attachment: error processing {filename}]"

    return question


def _parse_model_flag(text: str) -> tuple[str, str]:
    """Extract --model <alias> from *text*.

    Returns (cleaned_text, model_pref) where model_pref is a valid
    OpenClaw model preference string.
    """
    match = _MODEL_FLAG_RE.search(text)
    if not match:
        return text, "auto"
    alias = match.group(1).lower()
    model_pref = _MODEL_ALIASES.get(alias, "auto")
    clean = _MODEL_FLAG_RE.sub("", text).strip()
    return clean, model_pref


def _parse_flags(text: str) -> tuple[str, str, bool]:
    """Parse --simple and --model flags from *text*.

    Returns (cleaned_text, model_pref, use_simple).
    """
    use_simple = bool(_SIMPLE_FLAG_RE.search(text))
    cleaned = _SIMPLE_FLAG_RE.sub("", text).strip()
    cleaned, model_pref = _parse_model_flag(cleaned)
    return cleaned, model_pref, use_simple


def _register_bot_message(channel: str, ts: str, user_id: str) -> None:
    """Track a bot message so reactions can be matched back to the requester."""
    _bot_message_registry[(channel, ts)] = user_id
    # Prune to avoid unbounded growth (keep last 500 entries)
    if len(_bot_message_registry) > 500:
        oldest = next(iter(_bot_message_registry))
        del _bot_message_registry[oldest]


async def _build_thread_history(client: Any, channel: str, thread_ts: str) -> list[dict[str, str]]:
    """Fetch previous messages in *thread_ts* and return them as conversation history.

    The last message (the current prompt) is excluded — the caller supplies that
    as the ``prompt`` argument to ``_ask``.
    """
    global _BOT_USER_ID
    try:
        result = await client.conversations_replies(channel=channel, ts=thread_ts, limit=20)
        messages: list[dict] = result.get("messages", [])
        history: list[dict[str, str]] = []
        for msg in messages[:-1]:  # exclude the triggering message
            content = (msg.get("text") or "").strip()
            if not content or content == "⏳ Thinking…":
                continue
            is_bot = bool(msg.get("bot_id")) or (_BOT_USER_ID and msg.get("user") == _BOT_USER_ID)
            role = "assistant" if is_bot else "user"
            history.append({"role": role, "content": content})
        return history
    except Exception as exc:
        log.warning("Failed to fetch thread history for %s/%s: %s", channel, thread_ts, exc)
        return []


def _slack_is_configured() -> bool:
    if not SLACK_ENABLED:
        log.warning("Slack bot disabled: SLACK_ENABLED != 'true'")
        return False
    if not SLACK_BOT_TOKEN or not SLACK_BOT_TOKEN.startswith("xoxb-"):
        log.warning("Slack bot disabled: SLACK_BOT_TOKEN missing or invalid (expected xoxb-...)")
        return False
    if not SLACK_APP_TOKEN or not SLACK_APP_TOKEN.startswith("xapp-"):
        log.warning("Slack bot disabled: SLACK_APP_TOKEN missing or invalid (expected xapp-...)")
        return False
    return True


# ---------------------------------------------------------------------------
# File action registry + Block Kit helpers
# ---------------------------------------------------------------------------
# file_id → full file_obj dict from files_info (url_private, name, mimetype, …)
# Kept in memory to avoid re-calling files_info on every button click.
# Pruned to last 200 entries.
_file_registry: dict[str, dict] = {}

# Batch file grouping state: channel:ts → list of file events queued within grouping window
_pending_batch: dict[str, list[dict]] = {}
_batch_lock: asyncio.Lock | None = None  # initialized lazily

# Compare flow: user_id → file_id of Document A awaiting a second file
_compare_pending: dict[str, str] = {}

# Prompts sent to the LLM when a file action button is clicked
_FILE_ACTION_PROMPTS: dict[str, str] = {
    "file_proofread": (
        "Please proofread this document and correct any grammar, spelling, or punctuation "
        "errors. List each correction clearly."
    ),
    "file_summarize": "Please summarize the key points in a few bullet points.",
    "file_explain": (
        "Please explain what this document is about in plain, simple language. Assume the reader is non-technical."
    ),
    "file_errors": (
        "Please identify any errors, inconsistencies, unusual values, or potential problems "
        "in this document. Be specific."
    ),
    "file_research": (
        "Using web research to enhance your analysis. "
        "First identify key entities, terms, or claims in this document that would benefit from current information. "
        "Then provide a research-enhanced analysis incorporating both the document content and current facts."
    ),
    "file_describe": "Please describe what is in this image in detail.",
    "file_read_text": "Please read and transcribe all text visible in this image.",
    "file_chart": (
        "Analyze this spreadsheet data. Identify the best columns to visualize as a chart. "
        "Return a JSON object with these fields: "
        '{"chart_type": "bar|line|pie", "x_column": "column name", "y_columns": ["col1", "col2"], '
        '"title": "chart title", "description": "one-sentence description of what the chart shows"}. '
        "Return ONLY the JSON, no other text."
    ),
    "file_formula": (
        "Examine this spreadsheet carefully. List all the formulas you find and explain what each one does in plain English. "
        "For any formula that seems complex or hard to understand, suggest a simpler alternative. "
        "If any formulas appear to have errors or could cause problems, flag them clearly."
    ),
    "file_translate": (
        "Please translate this document into {language}. "
        "Preserve the original formatting and structure as much as possible. "
        "Return only the translated text."
    ),
    "file_compare": (
        "You are comparing two documents. Identify the key differences between them: "
        "structural changes, added/removed sections, significant wording changes, and any "
        "factual differences. Present as a clear summary with bullet points."
    ),
}


def _register_file(file_id: str, file_obj: dict, file_bytes: bytes | None = None) -> None:
    """Store *file_obj* (and optionally raw bytes) in the registry, pruning to 200 entries."""
    _file_registry[file_id] = {"file_obj": file_obj, "file_bytes": file_bytes}
    if len(_file_registry) > 200:
        oldest = next(iter(_file_registry))
        del _file_registry[oldest]


def _build_file_blocks(filename: str, description: str | None, mimetype: str, file_id: str) -> list[dict]:
    """Build Slack Block Kit blocks for a file upload suggestion message."""
    is_image = (mimetype or "").lower().startswith("image/")

    header = f"📎 I see you uploaded *{filename}*."
    if description:
        header = f"{header}\n_{description}_"
    header += "\n\nWhat would you like to do?"

    if is_image:
        buttons: list[dict] = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔍 Describe it", "emoji": True},
                "action_id": "file_describe",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📝 Read text in image", "emoji": True},
                "action_id": "file_read_text",
                "value": file_id,
            },
        ]
    elif (mimetype or "").lower() == "application/pdf" or filename.lower().endswith(".pdf"):
        buttons = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📋 Summarize", "emoji": True},
                "action_id": "file_summarize",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📌 Key Points", "emoji": True},
                "action_id": "file_explain",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✅ Action Items", "emoji": True},
                "action_id": "file_errors",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🌍 Translate", "emoji": True},
                "action_id": "file_translate",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔀 Compare", "emoji": True},
                "action_id": "file_compare_start",
                "value": file_id,
            },
        ]
    elif (mimetype or "").lower().startswith("audio/"):
        buttons = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🎵 Audio — transcription coming soon", "emoji": True},
                "action_id": "audio_unsupported",
                "value": file_id,
            },
        ]
    else:
        buttons = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✏️ Proofread", "emoji": True},
                "action_id": "file_proofread",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📋 Summarize", "emoji": True},
                "action_id": "file_summarize",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "❓ Explain it", "emoji": True},
                "action_id": "file_explain",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔍 Find errors", "emoji": True},
                "action_id": "file_errors",
                "value": file_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔬 Research", "emoji": True},
                "action_id": "file_research",
                "value": file_id,
            },
        ]

        # Add chart button only for spreadsheet files
        if filename.endswith((".xlsx", ".csv")) or mimetype in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "text/csv",
        ):
            buttons.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📊 Chart", "emoji": True},
                    "action_id": "file_chart",
                    "value": file_id,
                }
            )
            buttons.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📐 Formulas", "emoji": True},
                    "action_id": "file_formula",
                    "value": file_id,
                }
            )

        # Add translate button for all non-image document files
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🌍 Translate", "emoji": True},
                "action_id": "file_translate",
                "value": file_id,
            }
        )

        # Compare button for all non-image document files
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔀 Compare", "emoji": True},
                "action_id": "file_compare_start",
                "value": file_id,
            }
        )

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "actions", "elements": buttons},
    ]


async def _generate_chart(
    file_obj: dict,
    token: str,
    user_id: str,
) -> bytes | None:
    """Generate a chart PNG from an Excel/CSV file. Returns PNG bytes or None on failure."""
    try:
        import matplotlib

        matplotlib.use("Agg")  # non-interactive backend
        import matplotlib.pyplot as plt
        import openpyxl
    except ImportError as exc:
        log.warning("_generate_chart: missing dependency: %s", exc)
        return None

    # Download file bytes
    file_bytes = None
    registry_entry = _file_registry.get(file_obj.get("id", "")) or {}
    if isinstance(registry_entry, dict) and "file_bytes" in registry_entry:
        file_bytes = registry_entry["file_bytes"]

    if not file_bytes:
        url = file_obj.get("url_private") or file_obj.get("ai_files_path")
        if not url:
            return None
        if file_obj.get("ai_files_path"):
            file_bytes = Path(file_obj["ai_files_path"]).read_bytes()
        else:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                    if resp.status != 200:
                        return None
                    file_bytes = await resp.read()

    if not file_bytes:
        return None

    # Parse Excel
    try:
        import io as _io

        wb = openpyxl.load_workbook(_io.BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 2:
            return None
        headers = [str(h or f"Col{i}") for i, h in enumerate(rows[0])]
        data_rows = rows[1:51]  # cap at 50 rows
    except Exception as exc:
        log.warning("_generate_chart: failed to parse Excel: %s", exc)
        return None

    # Get LLM chart spec
    try:
        sample = "\t".join(headers) + "\n" + "\n".join("\t".join(str(v or "") for v in r) for r in data_rows[:5])
        spec_prompt = (
            f"Spreadsheet columns: {headers}\nSample data:\n{sample}\n\n"
            'Return JSON: {"chart_type": "bar|line|pie", "x_column": "col", '
            '"y_columns": ["col"], "title": "...", "description": "..."}'
        )
        spec_json = await _ask(spec_prompt, user_id=user_id, simple=False, model_pref="gemini")
        import json as _json
        import re as _re

        json_match = _re.search(r"\{.*\}", spec_json, _re.DOTALL)
        spec = _json.loads(json_match.group() if json_match else spec_json)
    except Exception as exc:
        log.warning("_generate_chart: LLM spec failed: %s", exc)
        # Fallback: bar chart of first two numeric columns
        spec = {
            "chart_type": "bar",
            "x_column": headers[0],
            "y_columns": [headers[1]] if len(headers) > 1 else [headers[0]],
            "title": "Data Chart",
            "description": "",
        }

    # Build chart
    try:
        import io as _io2

        x_col = spec.get("x_column", headers[0])
        y_cols = spec.get("y_columns", [headers[1]] if len(headers) > 1 else [])
        chart_type = spec.get("chart_type", "bar")
        title = spec.get("title", "Chart")

        x_idx = headers.index(x_col) if x_col in headers else 0
        y_idxs = [headers.index(c) for c in y_cols if c in headers]
        if not y_idxs:
            y_idxs = [1] if len(headers) > 1 else [0]

        x_vals = [str(r[x_idx] or "") for r in data_rows]

        fig, ax = plt.subplots(figsize=(10, 6))
        for y_idx in y_idxs[:3]:  # cap at 3 series
            y_vals = []
            for r in data_rows:
                try:
                    y_vals.append(float(r[y_idx] or 0))
                except (TypeError, ValueError):
                    y_vals.append(0.0)
            label = headers[y_idx]
            if chart_type == "line":
                ax.plot(x_vals, y_vals, marker="o", label=label)
            elif chart_type == "pie" and len(y_idxs) == 1:
                ax.pie(y_vals, labels=x_vals, autopct="%1.1f%%")
                ax.set_title(title)
                break
            else:
                ax.bar(x_vals, y_vals, label=label)

        if chart_type != "pie":
            ax.set_title(title)
            ax.set_xlabel(x_col)
            if len(y_idxs) > 1:
                ax.legend()
            plt.xticks(rotation=45, ha="right")

        plt.tight_layout()
        buf = _io2.BytesIO()
        plt.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        log.warning("_generate_chart: chart render failed: %s", exc)
        return None


def _mimetype_for(filename: str) -> str:
    """Return a reasonable MIME type from a filename suffix."""
    suffix = Path(filename).suffix.lower()
    return {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".csv": "text/csv",
    }.get(suffix, "application/octet-stream")


def _route_model_for_file(filename: str, action: str) -> str:
    """Return the best model_pref for this file + action combination.

    Routing table (highest priority first):
    - .docx + proofread/summarize → gemini (long context)
    - .xlsx + analyze/chart → copilot (structured data)
    - any + research/search → perplexity-direct (web search)
    - default → auto
    """
    suffix = Path(filename).suffix.lower()
    action_lower = action.lower()

    if suffix == ".docx" and any(kw in action_lower for kw in ("proofread", "summarize")):
        return "gemini"
    if suffix == ".xlsx" and any(kw in action_lower for kw in ("analyze", "chart")):
        return "copilot"
    if any(kw in action_lower for kw in ("research", "search")):
        return "perplexity-direct"
    return "auto"


# ---------------------------------------------------------------------------
# Research request detection
# ---------------------------------------------------------------------------

_RESEARCH_KEYWORDS_RE = re.compile(
    r"\b(research|look\s+up|find\s+info|search\s+for)\b",
    re.IGNORECASE,
)


def _is_research_request(text: str) -> bool:
    """Return True if *text* contains a research-intent keyword."""
    return bool(_RESEARCH_KEYWORDS_RE.search(text))


# ---------------------------------------------------------------------------
# Batch upload detection
# ---------------------------------------------------------------------------


def _is_batch_upload(files: list) -> bool:
    """Return True if two or more files are present (batch mode)."""
    return len(files) >= 2


# ---------------------------------------------------------------------------
# Research pipeline (Perplexity → optional Gemini incorporation)
# ---------------------------------------------------------------------------


async def _run_research_pipeline(
    client: Any,
    channel: str,
    user: str,
    text: str,
    file_obj: dict | None = None,
) -> None:
    """Two-phase research pipeline: Perplexity search → optional Gemini incorporation.

    Phase 1: Call _ask() with perplexity-direct to get web research results.
    Phase 2: If a file is active, call _ask() with gemini to incorporate findings.
    Phase 3: Post combined answer.  If no file: post Perplexity results with a tip.
    """
    # Phase 1: Perplexity research
    perplexity_prompt = f"Research the following topic and provide a concise, cited summary with key facts:\n\n{text}"
    try:
        research_results = await _ask(perplexity_prompt, user, model_pref="perplexity-direct")
    except Exception as exc:
        log.warning("_run_research_pipeline: Perplexity phase failed: %s", exc)
        research_results = f"(Research unavailable: {exc})"

    # No active file — return Perplexity results with tip
    if file_obj is None:
        answer = f"🔍 *Research Results*\n\n{research_results}\n\n_Tip: share a Word doc to incorporate these findings_"
        try:
            await client.chat_postMessage(channel=channel, text=answer)
        except Exception as exc:
            log.warning("_run_research_pipeline: post failed: %s", exc)
        return

    # Interim acknowledgement
    try:
        await client.chat_postMessage(
            channel=channel,
            text="🔍 Found research results. Incorporating into your document...",
        )
    except Exception as exc:
        log.warning("_run_research_pipeline: interim post failed: %s", exc)

    # Phase 2: Gemini incorporates research into document context
    file_content_preview = ""
    try:
        file_content_preview = await _process_slack_files([file_obj], SLACK_BOT_TOKEN, "")
    except Exception:
        pass

    gemini_prompt = (
        f"Research findings:\n{research_results}\n\n"
        f"Using the above research, help incorporate these findings into the document:\n"
        f"{file_content_preview}"
    )
    try:
        gemini_answer = await _ask(gemini_prompt, user, model_pref="gemini")
    except Exception as exc:
        log.warning("_run_research_pipeline: Gemini phase failed: %s", exc)
        gemini_answer = "(Could not incorporate findings into document)"

    # Phase 3: Post final combined answer
    final_text = f"🔍 *Research Summary*\n{research_results}\n\n📄 *Suggested document update*\n{gemini_answer}"
    try:
        await client.chat_postMessage(channel=channel, text=_clean_for_slack(final_text))
    except Exception as exc:
        log.warning("_run_research_pipeline: final post failed: %s", exc)


# ---------------------------------------------------------------------------
# Batch file processor
# ---------------------------------------------------------------------------


async def _process_batch(
    client: Any,
    channel: str,
    thread_ts: str,
    files: list,
    action: str,
    dispatch_fn=None,
) -> list[dict]:
    """Process *files* sequentially, posting progress updates in *thread_ts*.

    Args:
        dispatch_fn: Optional async callable(file_obj, action_id, user_id) -> str.
                     When None a no-op placeholder is used (useful in tests).
    """
    total = len(files)
    try:
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"📦 Processing {total} files...",
        )
    except Exception as exc:
        log.warning("_process_batch: initial post failed: %s", exc)

    results: list[dict] = []

    for i, file_obj in enumerate(files, start=1):
        filename = file_obj.get("name", f"file_{i}")

        # Build progress snapshot
        progress_lines: list[str] = []
        for j, f in enumerate(files, start=1):
            fname = f.get("name", f"file_{j}")
            if j < i:
                progress_lines.append(f"✅ {j}/{total}: {fname} done")
            elif j == i:
                progress_lines.append(f"⏳ {j}/{total}: {fname}...")
            else:
                progress_lines.append(f"⬜ {j}/{total}: {fname}")

        try:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="\n".join(progress_lines),
            )
        except Exception as exc:
            log.warning("_process_batch: progress post failed for %s: %s", filename, exc)

        # Process the file
        try:
            if dispatch_fn is not None:
                result = await dispatch_fn(
                    file_obj=file_obj,
                    action_id=f"file_{action}",
                    user_id="batch",
                )
            else:
                result = f"Processed {filename}"
            results.append({"filename": filename, "status": "done", "result": result})
        except Exception as exc:
            log.error("_process_batch: error processing %s: %s", filename, exc)
            results.append({"filename": filename, "status": "error", "error": str(exc)})

        # Brief pause between files to respect rate limits
        if i < total:
            await asyncio.sleep(2)

    done_count = sum(1 for r in results if r["status"] == "done")
    summary = (
        f"✅ All {done_count} files processed!" if done_count == total else f"✅ {done_count}/{total} files processed."
    )
    try:
        await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=summary)
    except Exception as exc:
        log.warning("_process_batch: summary post failed: %s", exc)

    return results


async def _auto_brief_file(file_obj: dict, token: str) -> str | None:
    """Return a one-sentence description of the file, or None if unavailable.

    Downloads the first 800 chars of the file content and asks the LLM for a
    brief, plain-language description. Falls back to None on any error.
    Images are skipped (the vision model handles them differently).
    """
    url = file_obj.get("url_private_download") or file_obj.get("url_private")
    if not url:
        return None

    filename = file_obj.get("name", "file")
    mimetype = (file_obj.get("mimetype") or "").lower()

    # Images: skip (vision analysis runs separately when the user picks an action)
    if mimetype.startswith("image/"):
        return None

    # Only try to extract text from supported types
    if not (
        mimetype.startswith("text/")
        or mimetype == "application/pdf"
        or mimetype == "application/json"
        or mimetype.startswith("application/vnd.")
    ):
        return None

    try:
        session = await _slack_dl_sessions.get()
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
    except Exception as exc:
        log.debug("_auto_brief_file: download failed for %s: %s", filename, exc)
        return None

    try:
        preview = data.decode("utf-8", errors="replace")[:800].strip()
    except Exception:
        return None

    if not preview:
        return None

    try:
        brief = await _ask(
            (
                f"In one short sentence (max 20 words), describe what this file is about. "
                f"Be specific.\n\nFile name: {filename}\n\nContent preview:\n{preview}"
            ),
            user_id="_auto_brief",
            simple=True,
        )
        return brief.strip().rstrip(".") if brief else None
    except Exception as exc:
        log.debug("_auto_brief_file: LLM failed for %s: %s", filename, exc)
        return None


async def _compare_documents(
    file_obj_a: dict,
    file_obj_b: dict,
    token: str,
    user_id: str,
    simple: bool = False,
) -> str:
    """Compare two documents semantically and return a diff summary."""
    content_a = await _process_slack_files([file_obj_a], token, "Return the full text content of this document.")
    content_b = await _process_slack_files([file_obj_b], token, "Return the full text content of this document.")
    compare_prompt = (
        "Compare these two documents and summarize the key differences:\n\n"
        f"--- Document A: {file_obj_a.get('name', 'Document A')} ---\n{content_a}\n\n"
        f"--- Document B: {file_obj_b.get('name', 'Document B')} ---\n{content_b}\n\n"
        "Focus on: structural changes, added/removed sections, significant wording differences, "
        "and any factual changes. Use bullet points."
    )
    return await _ask(compare_prompt, user_id=user_id, simple=simple, model_pref="gemini")


async def _two_phase_research(file_obj: dict, token: str, base_prompt: str) -> str:
    """Two-phase pipeline: Perplexity for web context + Gemini for doc integration.

    Falls back to single-phase if Perplexity routing is unavailable.
    """
    # Phase 1: get web research via Perplexity
    doc_content = await _process_slack_files(
        [file_obj],
        token,
        "Extract the main topics, entities, and key claims from this document in a brief list.",
    )
    research_prompt = f"Research current information about these topics from a document: {doc_content[:500]}"
    try:
        research_result = await _ask(research_prompt, user_id="system", simple=False, model_pref="perplexity-direct")
    except Exception as exc:
        log.warning("_two_phase_research: Perplexity phase failed, falling back: %s", exc)
        research_result = ""

    # Phase 2: Gemini synthesizes doc + research
    if research_result:
        combined_prompt = (
            f"{base_prompt}\n\n"
            f"--- Document ---\n{doc_content}\n--- End Document ---\n\n"
            f"--- Current Research ---\n{research_result}\n--- End Research ---\n\n"
            "Please provide an analysis that incorporates both the document content and the current research above."
        )
    else:
        combined_prompt = await _process_slack_files([file_obj], token, base_prompt)

    return combined_prompt


async def _ask(
    prompt: str,
    user_id: str,
    *,
    model_pref: str = "auto",
    history: list[dict] | None = None,
    simple: bool = False,
    client: Any = None,
) -> str:
    """Route a prompt through OpenClaw's agent ask pipeline."""
    global _daily_query_count, _error_window, _last_alert_ts
    from dashboard.api_handlers import _execute_agent_ask

    try:
        if simple:
            prompt = _SIMPLE_SYSTEM_PREFIX + prompt
        payload = await _execute_agent_ask(
            prompt=prompt,
            model_pref=model_pref,
            history=history or [],
            user_name=f"slack:{user_id}",
        )
        result = str(payload.get("response") or payload.get("text") or "(no response)").strip()
        _model_last_success[model_pref] = time.monotonic()
        _daily_query_count += 1
        return result
    except Exception as exc:  # broad: intentional
        log.error("_execute_agent_ask failed for slack user %s: %s", user_id, exc)
        now = time.monotonic()
        _error_window.append(now)
        _error_window[:] = [t for t in _error_window if now - t < 300]
        if len(_error_window) >= 3 and client is not None:
            await _alert_admin(client, f"model={model_pref} user={user_id} err={exc}")
        raise


# ---------------------------------------------------------------------------
# Progress streaming helpers
# ---------------------------------------------------------------------------

_PROGRESS_STEPS: list[str] = [
    "📖 Reading your document…",
    "🔍 Analyzing content…",
    "✍️ Writing response…",
    "⏳ Almost done…",
]


async def _edit_thinking_with_progress(
    client: Any,
    channel: str,
    ts: str,
    steps: list[str],
    interval_secs: float = 8.0,
) -> None:
    """Cycle through step messages on the thinking placeholder until cancelled."""
    for step in steps:
        await asyncio.sleep(interval_secs)
        try:
            await client.chat_update(channel=channel, ts=ts, text=step)
        except Exception:
            break


# ---------------------------------------------------------------------------
# Shared send-and-track helper
# ---------------------------------------------------------------------------


async def _send_answer(
    *,
    client: Any,
    say: Any,
    channel: str,
    thread_ts: str | None,
    thinking_ts: str | None,
    prompt: str,
    user_id: str,
    model_pref: str = "auto",
    history: list[dict] | None = None,
    simple: bool = False,
) -> None:
    """Ask OpenClaw, update the thinking placeholder, and register the reply for feedback."""
    t0 = time.monotonic()
    progress_task: asyncio.Task | None = None
    if thinking_ts:
        progress_task = asyncio.create_task(_edit_thinking_with_progress(client, channel, thinking_ts, _PROGRESS_STEPS))
    try:
        try:
            answer = await _ask(prompt, user_id, model_pref=model_pref, history=history, simple=simple)
            text = _clean_for_slack(answer) if answer else "(no response)"
            text += _maybe_append_tip()
            _log_query_metrics(
                user_id,
                action="message",
                model_used=model_pref or "auto",
                duration_ms=int((time.monotonic() - t0) * 1000),
                status="ok",
            )
        except Exception as exc:
            log.warning("_send_answer: _ask failed: %s", exc)
            text = "Hmm, something didn't work right — sorry about that! Try sending your message again. If it keeps happening, you can let Dave know."
            _log_query_metrics(
                user_id,
                action="message",
                model_used=model_pref or "auto",
                duration_ms=int((time.monotonic() - t0) * 1000),
                status="error",
            )
            # Post a Block Kit "Try again" button so the user has a recovery path
            prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
            _retry_cache[prompt_hash] = prompt
            if len(_retry_cache) > _RETRY_CACHE_MAX:
                oldest_key = next(iter(_retry_cache))
                del _retry_cache[oldest_key]
            try:
                retry_blocks = [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "⚠️ Something went wrong — want me to try again?"},
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "🔁 Retry", "emoji": True},
                                "action_id": "retry_last_prompt",
                                "value": prompt_hash,
                            }
                        ],
                    },
                ]
                await say(blocks=retry_blocks, text="⚠️ Something went wrong — want me to try again?")
            except Exception as retry_exc:
                log.warning("_send_answer: failed to post retry button: %s", retry_exc)
    finally:
        if progress_task and not progress_task.done():
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

    sent_ts: str | None = None

    if thinking_ts:
        try:
            resp = await client.chat_update(channel=channel, ts=thinking_ts, text=text)
            sent_ts = (resp or {}).get("ts") or thinking_ts
        except Exception:
            pass

    if sent_ts is None:
        kwargs: dict[str, Any] = {"text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        resp2 = await say(**kwargs)
        sent_ts = (resp2 or {}).get("ts")

    if sent_ts:
        _register_bot_message(channel, sent_ts, user_id)


# ---------------------------------------------------------------------------
# Admin DM alerting
# ---------------------------------------------------------------------------


async def _alert_admin(client: Any, message: str) -> None:
    """DM the admin when error rate spikes."""
    global _last_alert_ts
    admin_user = os.environ.get("SLACK_ADMIN_USER_ID", "")
    if not admin_user:
        return
    now = time.monotonic()
    if now - _last_alert_ts < 300:
        return
    _last_alert_ts = now
    try:
        await client.chat_postMessage(channel=admin_user, text=f"⚠️ OpenClaw alert:\n{message}")
    except Exception as exc:
        log.warning("_alert_admin: failed to DM admin: %s", exc)


async def _handle_browser_nav_intent(
    client: Any,
    channel: str,
    raw_text: str,
    thread_ts: str | None = None,
) -> bool:
    """Detect browser navigation intent in raw_text; fetch page content and post result.

    Returns True if a navigation intent was detected (caller should return early).
    """
    m = _BROWSER_NAV_PATTERNS.search(raw_text)
    if not m:
        return False

    url = m.group(1)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        from browser_skills import navigate_and_extract  # lazy import — lives in /app/skills

        content = await navigate_and_extract(url)
    except Exception as exc:
        log.warning("_handle_browser_nav_intent: failed for %s: %s", url, exc)
        kwargs: dict[str, Any] = {
            "channel": channel,
            "text": f"⚠️ Couldn't fetch {url} — site may be down or blocking bots.",
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        await client.chat_postMessage(**kwargs)
        return True

    reply_kwargs: dict[str, Any] = {
        "channel": channel,
        "text": f"🌐 Content from <{url}|{url}>:\n```\n{content}\n```",
    }
    if thread_ts:
        reply_kwargs["thread_ts"] = thread_ts
    await client.chat_postMessage(**reply_kwargs)
    return True


async def _handle_screenshot_intent(
    client: Any,
    channel: str,
    raw_text: str,
    thread_ts: str | None = None,
) -> bool:
    """Detect screenshot intent in raw_text; capture and upload if found.

    Returns True if a screenshot intent was detected (caller should return early).
    """
    m = _SCREENSHOT_PATTERNS.search(raw_text)
    if not m:
        return False

    url = next((g for g in m.groups() if g), None)
    if not url:
        return False

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urllib.parse.urlparse(url)
    safe_hostname = re.sub(r"[^a-zA-Z0-9._-]", "_", parsed.netloc or "page")

    try:
        from screenshot_skill import take_website_screenshot  # lazy import — lives in /app/skills

        png_bytes = await take_website_screenshot(url)
    except Exception as exc:
        log.warning("_handle_screenshot_intent: failed for %s: %s", url, exc)
        kwargs: dict[str, Any] = {
            "channel": channel,
            "text": f"⚠️ Couldn't screenshot {url} — site may be down or blocking bots. Try a different URL.",
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        await client.chat_postMessage(**kwargs)
        return True

    upload_kwargs: dict[str, Any] = {
        "channel": channel,
        "filename": f"screenshot_{safe_hostname}.png",
        "content": png_bytes,
        "initial_comment": f"📸 Screenshot of {url}",
    }
    if thread_ts:
        upload_kwargs["thread_ts"] = thread_ts
    await client.files_upload_v2(**upload_kwargs)
    return True


# ---------------------------------------------------------------------------
# Slack app factory
# ---------------------------------------------------------------------------


async def _handle_batch_file(event: dict, client: Any, say: Any) -> None:
    """Group simultaneous file_shared events and process as a batch when multiple arrive.

    Uses a 0.5-second grouping window. If multiple files land with the same
    channel_id + event_ts key, they are processed sequentially with progress
    updates in thread. Single-file uploads are processed via the normal path
    (auto-brief + Block Kit buttons) with no behaviour change.
    """
    global _batch_lock
    if _batch_lock is None:
        _batch_lock = asyncio.Lock()

    channel: str = event.get("channel_id", "")
    # Use event_ts as the grouping key; fall back to ts when absent
    group_ts: str = event.get("event_ts") or event.get("ts", "")
    batch_key = f"{channel}:{group_ts}"

    async with _batch_lock:
        if batch_key not in _pending_batch:
            _pending_batch[batch_key] = []
        _pending_batch[batch_key].append(event)
        is_first = len(_pending_batch[batch_key]) == 1

    if not is_first:
        # Another coroutine is already managing this batch window; nothing to do.
        return

    # Wait briefly for any additional file_shared events that belong to the same message
    await asyncio.sleep(0.5)

    async with _batch_lock:
        batch_events = _pending_batch.pop(batch_key, [])

    if len(batch_events) <= 1:
        # Single file — delegate to the normal inline handling below
        await _process_single_file_shared(event, client, say)
        return

    # Batch path: multiple files in one message
    n = len(batch_events)
    try:
        header_resp = await client.chat_postMessage(
            channel=channel,
            text=f"📦 Processing {n} files…",
        )
        thread_ts = (header_resp or {}).get("ts", "")
    except Exception as exc:
        log.warning("_handle_batch_file: could not post batch header: %s", exc)
        thread_ts = ""

    for i, file_event in enumerate(batch_events, start=1):
        fid: str = file_event.get("file_id", "")
        try:
            file_info_resp = await client.files_info(file=fid)
            file_obj: dict = (file_info_resp or {}).get("file", {})
        except Exception as exc:
            log.warning("_handle_batch_file: files_info failed for %s: %s", fid, exc)
            continue

        if not file_obj:
            continue

        _register_file(fid, file_obj)
        filename = file_obj.get("name", f"file_{i}")
        mimetype = file_obj.get("mimetype") or ""

        # Per-file progress indicator
        if thread_ts:
            try:
                await client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"⏳ {i}/{n}: processing *{filename}*…",
                )
            except Exception as exc:
                log.warning("_handle_batch_file: progress post failed for %s: %s", filename, exc)

        description = await _auto_brief_file(file_obj, SLACK_BOT_TOKEN)
        blocks = _build_file_blocks(filename, description, mimetype, fid)
        fallback_text = (
            f"📎 *{filename}*" + (f"\n_{description}_" if description else "") + "\n\nWhat would you like to do?"
        )
        try:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts or None,
                text=fallback_text,
                blocks=blocks,
            )
        except Exception as exc:
            log.warning("_handle_batch_file: Block Kit post failed for %s: %s", filename, exc)

    if thread_ts:
        try:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"✅ All {n} files ready!",
            )
        except Exception as exc:
            log.warning("_handle_batch_file: summary post failed: %s", exc)


async def _process_single_file_shared(event: dict, client: Any, say: Any) -> None:
    """Handle a single file_shared event: auto-brief + Block Kit action buttons."""
    file_id: str = event.get("file_id", "")
    channel: str = event.get("channel_id", "")
    user_id: str = event.get("user_id", "")

    if not file_id or not channel:
        return

    try:
        file_info_resp = await client.files_info(file=file_id)
        file_obj: dict = (file_info_resp or {}).get("file", {})
    except Exception as exc:
        log.warning("file_shared: failed to fetch info for file %s: %s", file_id, exc)
        return

    if not file_obj:
        return

    _register_file(file_id, file_obj)
    filename = file_obj.get("name", "file")
    mimetype = file_obj.get("mimetype") or ""

    # Compare flow: if user has already selected Document A, treat this as Document B
    if user_id and user_id in _compare_pending:
        file_id_a = _compare_pending.pop(user_id)
        file_obj_a_entry = _file_registry.get(file_id_a) or {}
        if isinstance(file_obj_a_entry, dict) and "file_obj" in file_obj_a_entry:
            file_obj_a = file_obj_a_entry["file_obj"]
        else:
            file_obj_a = file_obj_a_entry or {}
        thinking_resp = await say(text="⏳ Comparing documents…")
        thinking_ts = (thinking_resp or {}).get("ts")
        use_simple = _get_user_simple(user_id)
        result = await _compare_documents(file_obj_a, file_obj, SLACK_BOT_TOKEN, user_id, simple=use_simple)
        text = _clean_for_slack(result)
        if thinking_ts:
            try:
                await client.chat_update(channel=channel, ts=thinking_ts, text=text)
            except Exception:
                await say(text=text)
        else:
            await say(text=text)
        return  # skip normal auto-brief for this file

    # Download file bytes now for later use in corrected-doc upload
    try:
        url = file_obj.get("url_private_download") or file_obj.get("url_private")
        if url:
            session = await _slack_dl_sessions.get()
            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    file_bytes = await resp.read()
                    _register_file(file_id, file_obj, file_bytes)
                    if user_id:
                        _record_file_history(user_id, file_obj, file_bytes)
    except Exception as exc:
        log.debug("file_shared: could not pre-download bytes for %s: %s", file_id, exc)

    # Show a placeholder while we run the auto-brief
    try:
        placeholder = await client.chat_postMessage(channel=channel, text=f"📎 *{filename}* — reading…")
        placeholder_ts = (placeholder or {}).get("ts")
    except Exception:
        placeholder_ts = None

    # Auto-brief: 1-sentence description of the file (graceful fallback on error)
    description = await _auto_brief_file(file_obj, SLACK_BOT_TOKEN)

    blocks = _build_file_blocks(filename, description, mimetype, file_id)
    fallback_text = (
        f"📎 *{filename}*" + (f"\n_{description}_" if description else "") + "\n\nWhat would you like to do?"
    )

    try:
        if placeholder_ts:
            await client.chat_update(channel=channel, ts=placeholder_ts, text=fallback_text, blocks=blocks)
        else:
            await client.chat_postMessage(channel=channel, text=fallback_text, blocks=blocks)
    except Exception as exc:
        # Block Kit may fail if interactivity is not yet enabled in the manifest.
        log.warning("file_shared: Block Kit failed for %s (%s); using plain text", filename, exc)
        plain = _suggest_actions_for_file(filename, mimetype)
        if description:
            plain = f"_{description}_\n\n{plain}"
        try:
            if placeholder_ts:
                await client.chat_update(channel=channel, ts=placeholder_ts, text=plain)
            else:
                await client.chat_postMessage(channel=channel, text=plain)
        except Exception as exc2:
            log.warning("file_shared: plain fallback also failed for %s: %s", filename, exc2)


def _log_query_metrics(
    user_id: str,
    action: str,
    model_used: str,
    duration_ms: int,
    status: str,  # "ok" or "error"
) -> None:
    """Append one JSON line to logs/slack_metrics.jsonl. No PII stored."""
    import hashlib

    metrics_path = Path(os.environ.get("SLACK_METRICS_PATH", "logs/slack_metrics.jsonl"))
    try:
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "user_hash": hashlib.sha256(user_id.encode()).hexdigest()[:12],
            "action": action,
            "model": model_used,
            "duration_ms": duration_ms,
            "status": status,
        }
        with metrics_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        log.debug("_log_query_metrics: failed to write: %s", exc)


def _read_metrics_summary(path: Path | str) -> dict:
    """Read last 7 days from a metrics JSONL file and return a summary dict.

    Returns an empty dict (with ``no_data=True``) if the file doesn't exist or
    has no qualifying records.
    """
    path = Path(path)
    if not path.exists():
        return {"no_data": True}

    cutoff = time.time() - 7 * 24 * 3600
    total = 0
    errors = 0
    total_duration = 0
    action_counts: dict[str, int] = {}
    user_counts: dict[str, int] = {}

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ts", 0) < cutoff:
                continue
            total += 1
            if rec.get("status") == "error":
                errors += 1
            total_duration += rec.get("duration_ms", 0)
            action = rec.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1
            user_hash = rec.get("user_hash", "")
            if user_hash:
                user_counts[user_hash] = user_counts.get(user_hash, 0) + 1

    if total == 0:
        return {"no_data": True}

    top_actions = sorted(action_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    top_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:3]

    return {
        "no_data": False,
        "total": total,
        "errors": errors,
        "avg_duration_ms": total_duration // total,
        "top_actions": top_actions,
        "top_users": [u for u, _ in top_users],
    }


_VALID_TIMES_RE = re.compile(
    r"^(?:([01]?\d|2[0-3]):([0-5]\d)|([01]?\d|2[0-3])([ap]m?)|off)$",
    re.IGNORECASE,
)


def _parse_schedule_time(text: str) -> int | None:
    """Parse a time string like '9am', '8:30', '14:00' into an hour (0-23). Returns None for 'off'."""
    text = text.strip().lower()
    if text == "off":
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if m:
        return int(m.group(1)) % 24
    m = re.match(r"^(\d{1,2})(am?|pm?)$", text)
    if m:
        hour = int(m.group(1))
        if "p" in m.group(2) and hour != 12:
            hour += 12
        elif "a" in m.group(2) and hour == 12:
            hour = 0
        return hour % 24
    return -1  # parse error


_VAGUE_PATTERNS: frozenset[str] = frozenset(
    [
        "help",
        "hi",
        "hello",
        "hey",
        "thanks",
        "ok",
        "okay",
        "this",
        "it",
        "stuff",
        "things",
        "something",
        "anything",
        "can you",
        "please",
        "yes",
        "no",
        "sure",
    ]
)


def _is_vague_question(text: str, has_files: bool = False) -> bool:
    """Return True if the message is too vague to answer well without clarification.

    A message is vague when it is short (< 6 words), has no file attachment to
    provide context, and the words used are all generic/filler terms.
    """
    if has_files:
        return False
    words = text.strip().lower().split()
    if len(words) == 0:
        return True
    if len(words) >= 6:
        return False
    # All words must be vague patterns (or punctuation) for it to be flagged
    clean_words = [w.strip("?!.,") for w in words if w.strip("?!.,")]
    return bool(clean_words) and all(w in _VAGUE_PATTERNS for w in clean_words)


# ---------------------------------------------------------------------------
# App Home tab
# ---------------------------------------------------------------------------


def _build_home_view(user_id: str, name: str) -> dict:
    """Build a Slack Block Kit Home tab view for the given user."""
    greeting_name = name if name and name != "there" else "there"
    greeting = f"👋 Hi {greeting_name}! Welcome to your OpenClaw hub."

    commands_text = (
        "*Your commands:*\n"
        "• `/chat <question>` — ask me anything\n"
        "• `/help` — full command list\n"
        "• `/files` — browse your uploaded files\n"
        "• `/brief` — last 5 uploads at a glance\n"
        "• `/search <keyword>` — search your file history\n"
        "• `/research <topic>` — web research\n"
        "• `/batch summarize|proofread|explain` — process all your files\n"
        "• `/template list` — starter document templates\n"
        "• `/simple on|off` — plain-language mode\n"
        "• `/digest on|off|status` — daily file digest\n"
        "• `/schedule <time>` — set digest delivery time\n"
        "• `/saved` — view your bookmarked responses\n"
        "• `/nickname <name>` — set your display name\n"
        "• `/clear` — reset active file context\n"
        "• `/metrics` — usage stats (admin)\n"
        "• `/health` — bot status"
    )

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": greeting, "emoji": True}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": commands_text}},
        {"type": "divider"},
    ]

    recent = list(reversed(_file_history.get(user_id, [])))[:3]
    if recent:
        file_lines = []
        for f in recent:
            fname = f.get("name", "unknown")
            uploaded = f.get("uploaded_at", "")[:10] if f.get("uploaded_at") else ""
            file_lines.append(f"• *{fname}*" + (f" ({uploaded})" if uploaded else ""))
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*📁 Your recent files:*\n" + "\n".join(file_lines)},
            }
        )
        blocks.append({"type": "divider"})

    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "📖 Full guide: `/help` · Questions? Just send me a DM!"}],
        }
    )

    return {"type": "home", "blocks": blocks}


async def _post_clarification_prompt(client: Any, channel: str, user_id: str) -> None:
    """Post a friendly Block Kit clarification card to help the user get started."""
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "👋 I want to make sure I help you well! What would you like to do?",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📄 Ask about a file", "emoji": True},
                    "action_id": "clarify_file",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "💬 Ask me anything", "emoji": True},
                    "action_id": "clarify_question",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📝 Help me write something", "emoji": True},
                    "action_id": "clarify_write",
                },
            ],
        },
    ]
    try:
        await client.chat_postEphemeral(
            channel=channel,
            user=user_id,
            text="What would you like to do?",
            blocks=blocks,
        )
    except Exception as exc:
        log.warning("_post_clarification_prompt failed: %s", exc)


# ---------------------------------------------------------------------------
# Wave 10: Dropbox helpers
# ---------------------------------------------------------------------------


def _get_dropbox_client(token: str | None = None) -> Any | None:
    """Return a Dropbox client using *token* or the server-level DROPBOX_APP_TOKEN."""
    active_token = token or _DROPBOX_TOKEN
    if not active_token:
        return None
    try:
        import dropbox  # noqa: PLC0415

        return dropbox.Dropbox(active_token)
    except ImportError:
        return None


def _dropbox_list_folder(path: str, token: str | None = None) -> list[dict]:
    """List files in a Dropbox folder using *token* (or server token). Returns [] if not configured."""
    dbx = _get_dropbox_client(token=token)
    if dbx is None:
        return []
    try:
        result = dbx.files_list_folder(path)
        files = []
        for entry in result.entries:
            if hasattr(entry, "server_modified"):
                files.append(
                    {
                        "name": entry.name,
                        "size": getattr(entry, "size", 0),
                        "modified": entry.server_modified.strftime("%Y-%m-%d %H:%M"),
                        "id": entry.id,
                        "path": entry.path_lower,
                    }
                )
        return sorted(files, key=lambda f: f["modified"], reverse=True)
    except Exception:  # noqa: BLE001
        return []


async def _dropbox_sync_new_files(slack_client: Any) -> int:
    """Poll Dropbox for new files and sync them. Returns count of new files."""
    from datetime import datetime

    dbx = _get_dropbox_client()
    if dbx is None:
        return 0

    # Load cursor
    cursor: str | None = None
    if _DROPBOX_CURSOR_PATH.exists():
        try:
            cursor = json.loads(_DROPBOX_CURSOR_PATH.read_text()).get("cursor")
        except Exception:  # noqa: BLE001
            cursor = None

    try:
        if cursor:
            result = dbx.files_list_folder_continue(cursor)
        else:
            result = dbx.files_list_folder(_DROPBOX_FOLDER)
    except Exception:  # noqa: BLE001
        return 0

    new_count = 0
    _DROPBOX_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    for entry in result.entries:
        if not hasattr(entry, "server_modified"):
            continue  # skip folders/deletes
        ext = Path(entry.name).suffix.lower()
        if ext not in {".docx", ".pdf", ".xlsx", ".txt", ".doc", ".csv"}:
            continue
        try:
            local_path = _DROPBOX_CACHE_DIR / entry.name
            dbx.files_download_to_file(str(local_path), entry.path_lower)
            if _DROPBOX_VIRTUAL_USER not in _file_history:
                _file_history[_DROPBOX_VIRTUAL_USER] = []
            _file_history[_DROPBOX_VIRTUAL_USER].append(
                {
                    "name": entry.name,
                    "uploaded_at": datetime.now().isoformat(),
                    "auto_brief": None,
                    "source": "dropbox",
                }
            )
            _save_file_history()
            new_count += 1
            if slack_client and _DROPBOX_NOTIFY_CHANNEL:
                await slack_client.chat_postMessage(
                    channel=_DROPBOX_NOTIFY_CHANNEL,
                    text=f"📦 New file from Dropbox: *{entry.name}* — ready to analyze!",
                )
        except Exception:  # noqa: BLE001
            continue

    try:
        _DROPBOX_CURSOR_PATH.write_text(json.dumps({"cursor": result.cursor}))
    except Exception:  # noqa: BLE001
        pass

    return new_count


# ---------------------------------------------------------------------------
# Wave 10 Yoda: Google Calendar helpers
# ---------------------------------------------------------------------------


async def _get_google_access_token() -> str | None:
    """Exchange refresh token for a short-lived access token. Cached for 55 min."""
    import time
    import urllib.parse
    import urllib.request

    if not (_GOOGLE_CLIENT_ID and _GOOGLE_CLIENT_SECRET and _GOOGLE_REFRESH_TOKEN):
        return None
    now = time.time()
    if _google_token_cache.get("expires_at", 0) > now + 60:
        return _google_token_cache["access_token"]
    try:
        data = urllib.parse.urlencode(
            {
                "client_id": _GOOGLE_CLIENT_ID,
                "client_secret": _GOOGLE_CLIENT_SECRET,
                "refresh_token": _GOOGLE_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            }
        ).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=data,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        _google_token_cache["access_token"] = result["access_token"]
        _google_token_cache["expires_at"] = now + result.get("expires_in", 3600)
        return result["access_token"]
    except Exception:  # noqa: BLE001
        return None


async def _get_calendar_events(days_ahead: int = 0) -> list[dict]:
    """Fetch Google Calendar events for today or the next N days."""
    import urllib.parse
    import urllib.request

    token = await _get_google_access_token()
    if token is None:
        return []
    try:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
        time_max = now.replace(hour=23, minute=59, second=59) + timedelta(days=days_ahead)
        params = urllib.parse.urlencode(
            {
                "calendarId": "primary",
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 20,
            }
        )
        req = urllib.request.Request(
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        events = []
        for item in data.get("items", []):
            start = item.get("start", {})
            end = item.get("end", {})
            events.append(
                {
                    "summary": item.get("summary", "(no title)"),
                    "start": start.get("dateTime", start.get("date", "")),
                    "end": end.get("dateTime", end.get("date", "")),
                    "location": item.get("location", ""),
                }
            )
        return events
    except Exception:  # noqa: BLE001
        return []


def _format_calendar_events(events: list[dict], label: str = "today") -> str:
    """Format calendar events as plain text."""
    from datetime import datetime

    if not events:
        return f"📅 Nothing on the calendar {label}."
    lines = [f"📅 *Your schedule for {label}:*"]
    for ev in events:
        start_str = ev["start"]
        try:
            dt = datetime.fromisoformat(start_str)
            time_part = dt.strftime("%-I:%M %p")
        except (ValueError, TypeError):
            time_part = start_str
        loc = f"  ·  📍 {ev['location']}" if ev.get("location") else ""
        lines.append(f"• {time_part} — {ev['summary']}{loc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Wave 10 Leia: Gmail helpers
# ---------------------------------------------------------------------------

_gmail_message_cache: list[dict] = []  # stores last /inbox result per session


async def _get_gmail_unread(max_results: int = 5) -> list[dict]:
    """Fetch last N unread emails from Gmail inbox (metadata only, no body)."""
    token = await _get_google_access_token()
    if token is None:
        return []
    try:
        import urllib.parse  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        params = urllib.parse.urlencode(
            {
                "labelIds": "INBOX,UNREAD",
                "maxResults": max_results,
            }
        )
        req = urllib.request.Request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        messages = data.get("messages", [])
        results = []
        for msg in messages:
            msg_id = msg["id"]
            meta_params = urllib.parse.urlencode(
                {
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"],
                }
            )
            meta_req = urllib.request.Request(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?{meta_params}",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(meta_req, timeout=10) as resp:
                meta = json.loads(resp.read())
            headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
            results.append(
                {
                    "id": msg_id,
                    "subject": headers.get("Subject", "(no subject)"),
                    "from": headers.get("From", "Unknown"),
                    "date": headers.get("Date", ""),
                }
            )
        return results
    except Exception:  # noqa: BLE001
        return []


async def _get_gmail_body(message_id: str) -> str:
    """Fetch full email body text, truncated at 4000 chars."""
    token = await _get_google_access_token()
    if token is None:
        return "(Gmail not configured)"
    try:
        import base64  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        req = urllib.request.Request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?format=full",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        def _extract_text(payload: dict) -> str:
            if payload.get("mimeType") == "text/plain":
                encoded = payload.get("body", {}).get("data", "")
                if encoded:
                    return base64.urlsafe_b64decode(encoded + "==").decode("utf-8", errors="replace")
            for part in payload.get("parts", []):
                text = _extract_text(part)
                if text:
                    return text
            return ""

        body = _extract_text(data.get("payload", {}))
        if not body:
            body = "(empty email)"
        return body[:4000]
    except Exception:  # noqa: BLE001
        return "(Could not load email body)"


def _register_core_handlers(app: Any) -> None:
    """Register core event handlers: App Home, @mention, DM, /chat, reactions, and basic commands."""
    # ------------------------------------------------------------------
    # Handler: App Home tab opened
    # ------------------------------------------------------------------

    @app.event("app_home_opened")
    async def handle_app_home_opened(event: dict[str, Any], client: Any) -> None:
        """Publish a personalized Home tab view when the user opens the App Home."""
        user_id: str = event.get("user", "")
        if not user_id:
            return
        name: str = (_personas.get(user_id) or {}).get("name", "there")
        view = _build_home_view(user_id, name)
        try:
            await client.views_publish(user_id=user_id, view=view)
        except Exception as exc:
            log.warning("handle_app_home_opened: failed to publish view for %s: %s", user_id, exc)

    # ------------------------------------------------------------------
    # Handler: @mention in a channel
    # ------------------------------------------------------------------

    @app.event("app_mention")
    async def handle_mention(event: dict[str, Any], say: Any, client: Any) -> None:
        user_id: str = event.get("user", "unknown")
        channel: str = event.get("channel", "")
        msg_ts: str = event.get("ts", "")
        thread_ts: str = event.get("thread_ts") or msg_ts
        raw_text: str = event.get("text", "")

        _set_trace(command="mention")

        asyncio.create_task(_check_new_user_onboarding(user_id, client))

        # Strip @mention token(s) and extract optional --model flag
        prompt_raw = _MENTION_RE.sub("", raw_text).strip()
        files: list[dict] = event.get("files", [])

        if not prompt_raw and not files:
            await say(
                text=_WELCOME_MESSAGE,
                thread_ts=thread_ts,
            )
            return

        prompt, model_pref, use_simple = _parse_flags(prompt_raw)
        use_simple = use_simple or _get_user_simple(user_id)

        # Screenshot intent — capture and upload before any LLM call
        if await _handle_screenshot_intent(client, channel, prompt_raw, thread_ts=thread_ts):
            return

        # Browser navigation intent — fetch page content before any LLM call
        if await _handle_browser_nav_intent(client, channel, prompt_raw, thread_ts=thread_ts):
            return
        if not event.get("files"):
            match = _match_question_to_history(user_id, prompt_raw)
            if match:
                filename = match.get("name", "")
                suggestion_msg = (
                    f"💡 Did you mean to use `{filename}`? Type `/files {filename}` to select it, or just ask away!"
                )
                await say(text=suggestion_msg, thread_ts=thread_ts)

        # Enrich prompt with any uploaded file content
        if files:
            prompt = await _process_slack_files(files, SLACK_BOT_TOKEN, prompt)

        # Build thread history when this is a reply in an existing thread
        history: list[dict] = []
        if event.get("thread_ts"):
            history = await _build_thread_history(client, channel, thread_ts)

        thinking_resp = await say(text="⏳ Thinking…", thread_ts=thread_ts)
        thinking_ts = (thinking_resp or {}).get("ts")

        await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=thread_ts,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            model_pref=model_pref,
            history=history,
            simple=use_simple,
        )
        _onboarded_users.add(user_id)

    # ------------------------------------------------------------------
    # Handler: DMs (direct messages)
    # ------------------------------------------------------------------

    @app.event("message")
    async def handle_dm(event: dict[str, Any], say: Any, client: Any) -> None:
        # Ignore bot messages, edited messages, and non-DM channels
        if event.get("bot_id") or event.get("subtype"):
            return
        if event.get("channel_type") != "im":
            return

        user_id: str = event.get("user", "unknown")
        channel: str = event.get("channel", "")
        raw_text: str = (event.get("text") or "").strip()
        files: list[dict] = event.get("files", [])

        _set_trace(command="dm")
        asyncio.create_task(_check_new_user_onboarding(user_id, client))

        if not raw_text and not files:
            await say(text=_WELCOME_MESSAGE)
            return

        prompt, model_pref, use_simple = _parse_flags(raw_text)
        use_simple = use_simple or _get_user_simple(user_id)

        # Screenshot intent — capture and upload before any LLM call
        thread_ts_dm: str | None = event.get("thread_ts")
        if await _handle_screenshot_intent(client, channel, raw_text, thread_ts=thread_ts_dm):
            return

        # Browser navigation intent — fetch page content before any LLM call
        if await _handle_browser_nav_intent(client, channel, raw_text, thread_ts=thread_ts_dm):
            return

        # Clarification prompt for vague top-level DMs (not thread replies)
        has_files = bool(event.get("files"))
        if event.get("thread_ts") is None and _is_vague_question(raw_text, has_files=has_files):
            await _post_clarification_prompt(client, channel, user_id)
            return

        # Batch processing: multiple files in one message
        if _is_batch_upload(files):
            msg_ts = event.get("ts", "")
            await _process_batch(client, channel, msg_ts, files, "summarize")
            return

        # Research pipeline: Perplexity search + optional Gemini document incorporation
        if _is_research_request(prompt):
            active_file_id = (_user_prefs.get(user_id) or {}).get("active_file_id")
            file_obj_for_research: dict | None = None
            if active_file_id and active_file_id in _file_registry:
                reg_entry = _file_registry[active_file_id]
                if isinstance(reg_entry, dict) and "file_obj" in reg_entry:
                    file_obj_for_research = reg_entry["file_obj"]
            await _run_research_pipeline(client, channel, user_id, prompt, file_obj_for_research)
            return

        # Smart file suggestion — only when no file is attached
        if not event.get("files"):
            match = _match_question_to_history(user_id, prompt)
            if match:
                filename = match.get("name", "")
                suggestion_msg = (
                    f"💡 Did you mean to use `{filename}`? Type `/files {filename}` to select it, or just ask away!"
                )
                await say(text=suggestion_msg)

        # Enrich prompt with any uploaded file content
        if files:
            prompt = await _process_slack_files(files, SLACK_BOT_TOKEN, prompt)

        # Carry thread history for DM thread replies (same as handle_mention)
        thread_ts: str | None = event.get("thread_ts")
        history: list[dict] | None = None
        if thread_ts:
            history = await _build_thread_history(client, channel, thread_ts)

        thinking_resp = await say(text="⏳ Thinking…")
        thinking_ts = (thinking_resp or {}).get("ts")

        await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=thread_ts,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            model_pref=model_pref,
            simple=use_simple,
            history=history,
        )
        _onboarded_users.add(user_id)

    # ------------------------------------------------------------------
    # Handler: /chat slash command
    # ------------------------------------------------------------------

    @app.command("/chat")
    async def handle_slash_ask(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()  # must acknowledge within 3 seconds

        user_id: str = body.get("user_id", "unknown")
        channel: str = body.get("channel_id", "")
        raw_text: str = (body.get("text") or "").strip()

        if not raw_text:
            await say(text="Usage: `/chat your question here`\nNeed ideas? Type `/help` to see examples.")
            return

        prompt, model_pref, use_simple = _parse_flags(raw_text)
        use_simple = use_simple or _get_user_simple(user_id)

        thinking_resp = await say(text="⏳ Thinking…")
        thinking_ts = (thinking_resp or {}).get("ts")

        await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=None,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            model_pref=model_pref,
            simple=use_simple,
        )

    # ------------------------------------------------------------------
    # Handler: 👍/👎 reaction feedback
    # ------------------------------------------------------------------

    @app.event("reaction_added")
    async def handle_reaction(event: dict[str, Any], client: Any) -> None:
        emoji: str = event.get("reaction", "")
        if emoji not in ("thumbsup", "+1", "thumbsdown", "-1"):
            return

        item: dict = event.get("item", {})
        if item.get("type") != "message":
            return

        channel: str = item.get("channel", "")
        ts: str = item.get("ts", "")
        key = (channel, ts)

        if key not in _bot_message_registry:
            return

        rating = 1 if emoji in ("thumbsup", "+1") else -1
        original_user = _bot_message_registry[key]
        reacting_user = event.get("user", "unknown")
        log.info(
            "Slack feedback: rating=%+d message_ts=%s channel=%s original_user=%s reacting_user=%s",
            rating,
            ts,
            channel,
            original_user,
            reacting_user,
        )
        # Acknowledge with a quiet emoji so the user knows it registered
        try:
            ack_emoji = "white_check_mark" if rating > 0 else "noted"
            await client.reactions_add(channel=channel, timestamp=ts, name=ack_emoji)
        except Exception:
            pass  # reaction may already exist; not critical

    # ------------------------------------------------------------------
    # Handler: /help slash command — beginner-friendly guide
    # ------------------------------------------------------------------

    @app.command("/help")
    async def handle_slash_help(ack: Any, say: Any) -> None:
        await ack()
        await say(text=_HELP_TEXT)

    # ------------------------------------------------------------------
    # Handler: /health slash command — bot health card
    # ------------------------------------------------------------------

    @app.command("/health")
    async def handle_slash_status(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "unknown")
        channel = body.get("channel_id", "")

        now = time.monotonic()
        uptime_secs = int(now - _BOT_START_TIME) if _BOT_START_TIME else 0
        hours, remainder = divmod(uptime_secs, 3600)
        minutes = remainder // 60
        uptime_str = f"{hours}h {minutes}m" if hours else f"{minutes}m"

        version = os.environ.get("OPENCLAW_VERSION", "dev")
        lines: list[str] = [f"🤖 *OpenClaw Bot Status* (v{version})\n"]
        lines.append(f"⏱ Uptime: {uptime_str}  |  Queries today: {_daily_query_count}")

        # Mac Mini reachability
        mac_mini_ip = os.getenv("OPENCLAW_MAC_MINI_IP", "192.168.1.93")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as sess:
                async with sess.get(f"http://{mac_mini_ip}:8080/health") as resp:
                    lines.append("✅ Mac Mini: reachable" if resp.status == 200 else f"⚠️ Mac Mini: HTTP {resp.status}")
        except Exception:
            lines.append("❌ Mac Mini: unreachable")

        # /ai-files inventory
        try:
            if _AI_FILES_DIR.exists():
                files = [
                    f for f in _AI_FILES_DIR.iterdir() if f.is_file() and f.suffix.lower() in _ALLOWED_UPLOAD_EXTENSIONS
                ]
                lines.append(f"📁 Storage: {len(files)} file(s)")
            else:
                lines.append("📁 Storage: folder not found")
        except Exception:
            lines.append("📁 Storage: error reading")

        # Last sync
        try:
            if _LAST_SYNC_PATH.exists():
                sync_data = json.loads(_LAST_SYNC_PATH.read_text(encoding="utf-8"))
                sync_ts = sync_data.get("timestamp", "")
                sync_file = sync_data.get("last_file", "")
                lines.append(f"🔄 Last sync: {sync_ts}" + (f" ({sync_file})" if sync_file else ""))
            else:
                lines.append("🔄 Last sync: none recorded")
        except Exception:
            lines.append("🔄 Last sync: unknown")

        # Model health
        model_lines: list[str] = []
        for model, ts in sorted(_model_last_success.items()):
            ago = int(now - ts)
            ago_str = f"{ago}s ago" if ago < 60 else f"{ago // 60}m ago"
            model_lines.append(f"  • {model}: {ago_str}")
        lines.append("\n*Model health:*\n" + ("\n".join(model_lines) if model_lines else "  (none used yet)"))

        if SLACK_NOTIFY_USER_ID:
            lines.append(f"🔔 File alerts: enabled (<@{SLACK_NOTIFY_USER_ID}>)")

        status_text = "\n".join(lines)
        try:
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text=status_text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": status_text}}],
            )
        except Exception as exc:
            log.warning("handle_slash_status: failed to post ephemeral: %s", exc)

    # ------------------------------------------------------------------
    # Handler: /digest — per-user periodic file digest opt-in
    # ------------------------------------------------------------------

    @app.command("/digest")
    async def handle_slash_digest(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "unknown")
        arg: str = (body.get("text") or "").strip().lower()
        prefs = _load_digest_prefs()
        user_pref = prefs.setdefault(user_id, {"enabled": False, "last_sent": 0})

        if arg in ("on", "enable", "1", "yes", "daily"):
            user_pref["enabled"] = True
            _save_digest_prefs(prefs)
            try:
                await client.chat_postEphemeral(
                    channel=body["channel_id"],
                    user=user_id,
                    text=(
                        f"✅ *Digest enabled!* I'll DM you every {_DIGEST_LOOKBACK_HOURS} hours "
                        f"with a summary of recently synced files. "
                        f"Type `/digest off` to stop anytime."
                    ),
                )
            except Exception as exc:
                log.warning("handle_slash_digest on: %s", exc)
        elif arg in ("off", "disable", "0", "no"):
            user_pref["enabled"] = False
            _save_digest_prefs(prefs)
            try:
                await client.chat_postEphemeral(
                    channel=body["channel_id"],
                    user=user_id,
                    text="🔕 *Digest disabled.* You won't receive automatic file summaries. Type `/digest on` to re-enable.",
                )
            except Exception as exc:
                log.warning("handle_slash_digest off: %s", exc)
        else:
            enabled = user_pref.get("enabled", False)
            last_sent = user_pref.get("last_sent", 0)
            last_str = _human_time(last_sent) if last_sent else "never"
            status_emoji = "✅" if enabled else "🔕"
            try:
                await client.chat_postEphemeral(
                    channel=body["channel_id"],
                    user=user_id,
                    text=(
                        f"{status_emoji} *Digest status:* {'enabled' if enabled else 'disabled'}\n"
                        f"• Last sent: {last_str}\n"
                        f"• Lookback window: {_DIGEST_LOOKBACK_HOURS} hours\n\n"
                        f"Commands: `/digest on` · `/digest off`"
                    ),
                )
            except Exception as exc:
                log.warning("handle_slash_digest status: %s", exc)

    # ------------------------------------------------------------------
    # Handler: /simple — toggle persistent plain-language mode per user
    # ------------------------------------------------------------------

    @app.command("/simple")
    async def handle_slash_simple(ack: Any, body: dict[str, Any], say: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "unknown")
        arg: str = (body.get("text") or "").strip().lower()

        if arg in ("on", "enable", "1", "yes"):
            _set_user_simple(user_id, True)
            await say(
                text=(
                    "✅ *Simple mode on!* I'll always give you plain, easy-to-read answers — "
                    "no jargon, short sentences. You don't need to add anything to your messages.\n"
                    "Type `/simple off` any time to go back to normal."
                )
            )
        elif arg in ("off", "disable", "0", "no"):
            _set_user_simple(user_id, False)
            await say(
                text=(
                    "🔄 *Simple mode off.* Back to normal responses.\n"
                    "You can still add `--simple` to any individual message for a plain answer."
                )
            )
        else:
            status = "on ✅" if _get_user_simple(user_id) else "off"
            await say(
                text=(
                    f"Simple mode is currently *{status}*.\n\n"
                    "• `/simple on` — always get plain, easy-to-read answers\n"
                    "• `/simple off` — go back to normal\n\n"
                    "_Tip: turn it on once and forget about it!_"
                )
            )

    # ------------------------------------------------------------------
    # Handler: /research — Perplexity research pipeline slash command
    # ------------------------------------------------------------------

    @app.command("/research")
    async def handle_slash_research(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "unknown")
        channel: str = body.get("channel_id", "")
        raw_text: str = (body.get("text") or "").strip()

        if not raw_text:
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text="Usage: `/research climate change for my annual report`",
            )
            return

        active_file_id = (_user_prefs.get(user_id) or {}).get("active_file_id")
        file_obj_for_research: dict | None = None
        if active_file_id and active_file_id in _file_registry:
            reg_entry = _file_registry[active_file_id]
            if isinstance(reg_entry, dict) and "file_obj" in reg_entry:
                file_obj_for_research = reg_entry["file_obj"]

        await _run_research_pipeline(client, channel, user_id, raw_text, file_obj_for_research)

    # ------------------------------------------------------------------
    # Handler: /batch — batch process all registered files
    # ------------------------------------------------------------------

    @app.command("/batch")
    async def handle_slash_batch(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "unknown")
        channel: str = body.get("channel_id", "")
        raw_text: str = (body.get("text") or "summarize").strip()

        # Collect all registered files (registry stores {"file_obj": ..., ...})
        user_files: list[dict] = []
        for fid, fobj in _file_registry.items():
            reg_file_obj = fobj.get("file_obj") if isinstance(fobj, dict) and "file_obj" in fobj else fobj
            if reg_file_obj:
                user_files.append(reg_file_obj)

        if not user_files:
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text="⚠️ No files found. Upload your files first, then run `/batch`.",
            )
            return

        action = raw_text.split()[0] if raw_text else "summarize"
        resp = await client.chat_postMessage(
            channel=channel,
            text=f"📦 Starting batch {action} on {len(user_files)} file(s)...",
        )
        thread_ts = (resp or {}).get("ts", "")

        await _process_batch(client, channel, thread_ts, user_files, action)


def _register_file_handlers(app: Any) -> None:
    """Register file event handlers: file_shared, Block Kit action buttons, /files command, compare/translate."""
    # ------------------------------------------------------------------
    # Handler: file_shared — auto-brief + Block Kit action buttons
    # ------------------------------------------------------------------

    @app.event("file_shared")
    async def handle_file_shared(event: dict[str, Any], client: Any, say: Any) -> None:
        await _handle_batch_file(event, client, say)

    # ------------------------------------------------------------------
    # Handlers: Block Kit file action buttons
    # Each button in the file suggestion message carries the file_id as
    # its value; the handler re-downloads the file and runs the LLM.
    # Requires interactivity enabled in the manifest: make slack-manifest
    # ------------------------------------------------------------------

    async def _return_corrected_doc(
        file_obj: dict,
        channel: str,
        user_id: str,
        corrected_text: str,
        client: Any,
    ) -> None:
        """Upload a corrected .docx back to Slack. Skips non-.docx files gracefully."""
        filename = file_obj.get("name", "document.docx")
        if not filename.lower().endswith(".docx"):
            try:
                await client.chat_postMessage(
                    channel=channel,
                    text="ℹ️ Corrected document return is only supported for .docx files.",
                )
            except Exception:
                pass
            return

        try:
            corrected_filename = "corrected_" + filename
            new_bytes = await create_word(title=corrected_filename, content=corrected_text)
            await client.files_upload_v2(
                channel=channel,
                filename=corrected_filename,
                content=new_bytes,
                initial_comment="✅ Here's your corrected document!",
            )
        except Exception as exc:
            log.warning("_return_corrected_doc: upload failed for %s: %s", filename, exc)

    async def _dispatch_file_action(action_id: str, ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
        await ack()

        user_id: str = (body.get("user") or {}).get("id", "unknown")
        actions: list[dict] = body.get("actions", [{}])
        file_id: str = (actions[0] if actions else {}).get("value", "")
        channel: str = (body.get("channel") or {}).get("id", "") or (body.get("container") or {}).get("channel_id", "")

        if not file_id or not channel:
            await say(text="⚠️ Couldn't identify the file. Please upload it again.")
            return

        file_obj = _file_registry.get(file_id)
        if not file_obj:
            await say(text="⚠️ I've lost track of that file — try uploading it again and I'll be ready.")
            return

        # Registry now stores {"file_obj": ..., "file_bytes": ...}
        if isinstance(file_obj, dict) and "file_obj" in file_obj:
            file_obj = file_obj["file_obj"]

        # file_chart: generate PNG chart from spreadsheet data
        if action_id == "file_chart":
            thinking_resp = await say(text="⏳ Generating chart…")
            thinking_ts = (thinking_resp or {}).get("ts")
            png_bytes = await _generate_chart(file_obj, SLACK_BOT_TOKEN, user_id)
            if png_bytes:
                try:
                    await client.files_upload_v2(
                        channel=channel,
                        content=png_bytes,
                        filename=f"chart_{file_obj.get('name', 'data')}.png",
                        title=f"Chart: {file_obj.get('name', 'data')}",
                    )
                    if thinking_ts:
                        await client.chat_delete(channel=channel, ts=thinking_ts)
                except Exception as exc:
                    log.warning("_dispatch_file_action: chart upload failed: %s", exc)
                    if thinking_ts:
                        await client.chat_update(
                            channel=channel,
                            ts=thinking_ts,
                            text="⚠️ Chart generated but upload failed.",
                        )
            else:
                msg = "📊 Chart generation requires `matplotlib` and `openpyxl`. Ask an admin to install them."
                if thinking_ts:
                    await client.chat_update(channel=channel, ts=thinking_ts, text=msg)
                else:
                    await say(text=msg)
            return  # don't fall through to normal dispatch

        prompt_text = _FILE_ACTION_PROMPTS.get(action_id, "Please analyze this file.")

        # Handle files referenced from /ai-files directly (no Slack download needed)
        if action_id == "file_research":
            prompt = await _two_phase_research(file_obj, SLACK_BOT_TOKEN, prompt_text)
        elif file_obj.get("ai_files_path"):
            file_content = await file_skills.read_local_file(file_obj["ai_files_path"])
            prompt = f"{prompt_text}\n\n--- File: {file_obj['name']} ---\n{file_content}\n--- End ---"
        else:
            prompt = await _process_slack_files([file_obj], SLACK_BOT_TOKEN, prompt_text)

        use_simple = _get_user_simple(user_id)

        # Smart model routing based on file type + action
        filename_for_routing = file_obj.get("name", "")
        if action_id == "file_research":
            model_pref = "gemini"
        else:
            model_pref = _route_model_for_file(filename_for_routing, action_id)

        thinking_resp = await say(text="⏳ Thinking…")
        thinking_ts = (thinking_resp or {}).get("ts")

        await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=None,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            simple=use_simple,
            model_pref=model_pref,
        )

        # For proofread on .docx: also return a corrected document file
        if action_id == "file_proofread":
            filename = (file_obj.get("name") or "").lower()
            if filename.endswith(".docx"):
                try:
                    correction_prompt = await _process_slack_files(
                        [file_obj],
                        SLACK_BOT_TOKEN,
                        "Return ONLY the fully corrected version of this document as plain text. "
                        "Fix all spelling, grammar, and punctuation errors. "
                        "Preserve the same paragraph structure.",
                    )
                    corrected_text = await _ask(correction_prompt, user_id=user_id, simple=False)
                    await _return_corrected_doc(file_obj, channel, user_id, corrected_text, client)
                except Exception as exc:
                    log.warning("_dispatch_file_action: corrected doc failed for %s: %s", filename, exc)

    # ------------------------------------------------------------------
    # Handler: /files — browse and reference synced documents
    # ------------------------------------------------------------------

    @app.command("/files")
    async def handle_slash_files(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id") or "unknown"
        channel: str = body.get("channel_id", "")
        text: str = (body.get("text") or "").strip()

        if text.lower() in ("recent", "history"):
            history = _file_history.get(user_id, [])
            if not history:
                await client.chat_postEphemeral(
                    channel=channel,
                    user=user_id,
                    text="📂 No file history yet. Upload a file and it'll appear here next time.",
                )
                return
            import time as _time

            lines = [f"📋 *Your recent files (last {len(history)}):*"]
            for entry in history:
                name = entry.get("name", "?")
                size = entry.get("size", 0)
                ts = entry.get("last_used_ts", 0)
                ago = int(_time.time() - ts)
                if ago < 3600:
                    ago_str = f"{ago // 60}m ago"
                elif ago < 86400:
                    ago_str = f"{ago // 3600}h ago"
                else:
                    ago_str = f"{ago // 86400}d ago"
                lines.append(f"  • `{name}` ({size:,} bytes, {ago_str})")
            lines.append("\n_Tip: `/files <name>` to select a file from storage_")
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text="\n".join(lines),
            )
            return

        if not text:
            # List mode — show all files in /ai-files
            listing = await file_skills.list_local_files("/ai-files")

            if "empty" in listing.lower() or "not found" in listing.lower():
                await client.chat_postEphemeral(
                    channel=channel,
                    user=user_id,
                    text=("📂 No files yet! Drop a Word doc into your OpenClaw folder and it'll appear here."),
                )
                return

            lines = listing.splitlines()
            file_blocks: list[dict] = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "📁 *Files in OpenClaw:*"}},
            ]
            for line in lines[1:21]:  # skip header line, cap at 20
                stripped = line.strip()
                if stripped:
                    file_blocks.append(
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"`{stripped}`"},
                        }
                    )
            file_blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "Tip: `/files budget.xlsx` to select a file"}],
                }
            )
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                blocks=file_blocks,
                text="Files in OpenClaw",
            )
            return

        # Reference mode — select a specific file from /ai-files
        target = Path("/ai-files") / text
        if not target.exists() or not target.is_file():
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text=f"⚠️ File not found: `{text}`. Use `/files` to see available files.",
            )
            return

        stat = target.stat()
        synthetic_file_id = f"aifiles::{text}"
        synthetic_file_obj = {
            "id": synthetic_file_id,
            "name": text,
            "mimetype": _mimetype_for(text),
            "size": stat.st_size,
            "url_private": None,
            "ai_files_path": str(target),
        }
        _register_file(synthetic_file_id, synthetic_file_obj)

        if user_id not in _user_prefs:
            _user_prefs[user_id] = {}
        _user_prefs[user_id]["active_file_id"] = synthetic_file_id
        _save_prefs()

        blocks = _build_file_blocks(
            filename=text,
            description=f"From OpenClaw storage ({stat.st_size:,} bytes)",
            mimetype=synthetic_file_obj["mimetype"],
            file_id=synthetic_file_id,
        )
        await client.chat_postMessage(
            channel=channel,
            blocks=blocks,
            text=f"Selected file: {text}",
        )

    # Register one handler per action_id using closures
    # Note: file_translate and file_compare are excluded from the generic dispatch loop because
    # they have their own flows registered separately below.
    _excluded_from_generic = {"file_translate", "file_compare"}
    for _action_id in [k for k in _FILE_ACTION_PROMPTS.keys() if k not in _excluded_from_generic]:

        def _make_handler(aid: str) -> Any:
            async def handler(ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
                await _dispatch_file_action(aid, ack, body, client, say)

            handler.__name__ = f"handle_{aid}"
            return handler

        app.action(_action_id)(_make_handler(_action_id))


def _register_slash_commands(app: Any) -> None:
    """Register slash command handlers: /metrics, /brief, /mystats, /template, /mypins, /filesearch, /schedule, /clear, /nickname."""
    # ------------------------------------------------------------------
    # Handler: /metrics — usage summary for last 7 days
    # ------------------------------------------------------------------

    @app.command("/metrics")
    async def handle_slash_metrics(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        channel = body.get("channel_id", "")
        user_id = body.get("user_id", "unknown")

        metrics_path = Path(os.environ.get("SLACK_METRICS_PATH", "logs/slack_metrics.jsonl"))
        summary = _read_metrics_summary(metrics_path)

        if summary.get("no_data"):
            text = "📊 *OpenClaw Usage* — No metrics recorded yet."
        else:
            top_actions_lines = "\n".join(f"  • {action}: {count}" for action, count in summary["top_actions"])
            top_users_str = ", ".join(summary["top_users"]) or "—"
            text = (
                f"📊 *OpenClaw Usage (Last 7 Days)*\n"
                f"Total queries: {summary['total']}  |  "
                f"Errors: {summary['errors']}  |  "
                f"Avg response: {summary['avg_duration_ms']:,}ms\n\n"
                f"*Top actions:*\n{top_actions_lines}\n\n"
                f"*Top users (anonymized):* {top_users_str}"
            )

        try:
            await client.chat_postEphemeral(channel=channel, user=user_id, text=text)
        except Exception as exc:
            log.warning("handle_slash_metrics: failed to post ephemeral: %s", exc)

    # ------------------------------------------------------------------
    # Handler: /clear — reset session state for the calling user
    # ------------------------------------------------------------------

    @app.command("/brief")
    async def handle_slash_brief(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        entries = _file_history.get(user_id, [])
        if not entries:
            await client.chat_postEphemeral(
                channel=body["channel_id"],
                user=user_id,
                text="📂 You haven't uploaded any files yet. Drop a file here to get started!",
            )
            return

        import datetime

        recent = list(reversed(entries))[:5]
        lines = []
        for entry in recent:
            name = entry.get("name", "unknown")
            uploaded_at = entry.get("uploaded_at", "")
            if uploaded_at:
                try:
                    dt = datetime.datetime.fromisoformat(uploaded_at)
                    delta = datetime.datetime.now() - dt
                    days = delta.days
                    if days == 0:
                        when = "today"
                    elif days == 1:
                        when = "yesterday"
                    else:
                        when = f"{days} days ago"
                except Exception:
                    when = uploaded_at[:10]
            else:
                when = "recently"
            lines.append(f"• *{name}* — {when}")

        text = "*📂 Your recent files:*\n" + "\n".join(lines)
        text += "\n\n_Type `/files recent` to see the full list, or just upload a new file!_"
        await client.chat_postEphemeral(
            channel=body["channel_id"],
            user=user_id,
            text=text,
        )

    @app.command("/mystats")
    async def handle_slash_mystats(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        import hashlib

        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", "")
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()[:12]

        metrics_path = Path(__file__).parent.parent / "logs" / "slack_metrics.jsonl"

        query_count = 0
        file_count = 0
        total_ms = 0
        error_count = 0
        action_counts: dict[str, int] = {}

        if metrics_path.exists():
            try:
                with open(metrics_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if rec.get("user_id") != user_hash:
                            continue
                        query_count += 1
                        action = rec.get("action", "")
                        action_counts[action] = action_counts.get(action, 0) + 1
                        if "file" in action.lower():
                            file_count += 1
                        dur = rec.get("duration_ms", 0)
                        if dur:
                            total_ms += dur
                        if rec.get("status") == "error":
                            error_count += 1
            except Exception as exc:
                log.warning("mystats: error reading metrics: %s", exc)

        avg_ms = int(total_ms / query_count) if query_count > 0 else 0
        top_actions = sorted(action_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        top_str = ", ".join(f"{a} ({c})" for a, c in top_actions) if top_actions else "none yet"

        text = (
            f"*📊 Your OpenClaw Stats*\n\n"
            f"• Queries answered: *{query_count}*\n"
            f"• Files processed: *{file_count}*\n"
            f"• Average response time: *{avg_ms}ms*\n"
            f"• Errors: *{error_count}*\n"
            f"• Top actions: {top_str}\n\n"
            f"_Stats tracked since OpenClaw Wave 4. Your ID is anonymized._"
        )
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=text,
        )

    @app.command("/template")
    async def handle_slash_template(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "unknown")
        channel_id: str = body.get("channel_id", user_id)
        arg: str = (body.get("text") or "").strip().lower()

        _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        available: list[Path] = sorted(
            [f for f in _TEMPLATES_DIR.iterdir() if f.is_file() and f.suffix in {".xlsx", ".docx", ".pdf", ".txt"}]
            if _TEMPLATES_DIR.exists()
            else []
        )

        if not arg or arg == "list":
            if not available:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="📂 No templates available yet. Contact your OpenClaw admin to add templates to `data/templates/`.",
                )
                return
            names = "\n".join(f"• `{f.stem}` ({f.suffix[1:].upper()})" for f in available)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    f"📄 *Available templates ({len(available)})* — type `/template <name>` to download:\n\n" + names
                ),
            )
            return

        match: Path | None = None
        for tpl in available:
            if tpl.stem.lower() == arg:
                match = tpl
                break
        if not match:
            for tpl in available:
                if tpl.stem.lower().startswith(arg):
                    match = tpl
                    break

        if not match:
            names_str = ", ".join(f"`{f.stem}`" for f in available)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    f"❓ Template `{arg}` not found.\n"
                    f"Available: {names_str or 'none yet'}\n"
                    f"Type `/template list` to see all options."
                ),
            )
            return

        try:
            file_bytes = match.read_bytes()
            await client.files_upload_v2(
                channel=user_id,
                filename=match.name,
                content=file_bytes,
                initial_comment=f"📄 Here's your *{match.stem}* template! Fill in the highlighted areas and you're good to go.",
            )
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"✅ *{match.name}* sent to your DMs!",
            )
        except Exception as exc:
            log.warning("handle_slash_template: failed to upload %s: %s", match.name, exc)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"⚠️ Couldn't upload {match.name}. Please try again in a moment.",
            )

    @app.command("/mypins")
    async def handle_slash_saved(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", "")

        notes_path = _DATA_DIR / "slack_saved_notes.json"
        if not notes_path.exists():
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="You haven't saved any messages yet — react 🔖 to any bot response to save it!",
            )
            return

        try:
            all_notes: list[dict] = json.loads(notes_path.read_text())
        except Exception:
            all_notes = []

        user_notes = [n for n in all_notes if n.get("user_id") == user_id]
        if not user_notes:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="You haven't saved any messages yet — react 🔖 to any bot response to save it!",
            )
            return

        recent = list(reversed(user_notes))[:5]
        blocks: list[dict] = [
            {"type": "header", "text": {"type": "plain_text", "text": "🔖 Your Saved Notes", "emoji": True}},
        ]
        for note in recent:
            saved_at = note.get("saved_at", "")
            try:
                import datetime

                dt = datetime.datetime.fromisoformat(saved_at)
                delta = datetime.datetime.now() - dt
                days = delta.days
                when = "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"
            except Exception:
                when = saved_at[:10] if saved_at else "recently"
            preview = (note.get("text") or "")[:200].replace("\n", " ")
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{when}* — {preview}…"
                        if len(note.get("text", "")) > 200
                        else f"*{when}* — {preview}",
                    },
                }
            )
            blocks.append({"type": "divider"})

        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_Showing {len(recent)} of {len(user_notes)} saved notes_"}],
            }
        )

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            blocks=blocks,
            text="🔖 Your Saved Notes",
        )

    @app.command("/filesearch")
    async def handle_slash_search(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", "")
        keyword: str = (body.get("text") or "").strip().lower()

        if not keyword:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/search <keyword>` — e.g. `/search budget`",
            )
            return

        entries = _file_history.get(user_id, [])
        matches = [
            e
            for e in entries
            if keyword in (e.get("name") or "").lower() or keyword in (e.get("auto_brief") or "").lower()
        ]

        if not matches:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"No files matching *{keyword}* found — try `/brief` to see all your recent uploads.",
            )
            return

        lines = []
        for entry in list(reversed(matches))[:10]:
            name = entry.get("name", "unknown")
            uploaded_at = entry.get("uploaded_at", "")
            try:
                import datetime

                dt = datetime.datetime.fromisoformat(uploaded_at)
                delta = datetime.datetime.now() - dt
                days = delta.days
                when = "today" if days == 0 else "yesterday" if days == 1 else f"{days}d ago"
            except Exception:
                when = "recently"
            brief = entry.get("auto_brief", "")
            brief_str = f" — _{brief}_" if brief else ""
            lines.append(f"• *{name}* ({when}){brief_str}")

        text = f"🔍 Files matching *{keyword}*:\n" + "\n".join(lines)
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=text,
        )

    @app.command("/schedule")
    async def handle_slash_schedule(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", "")
        text: str = (body.get("text") or "").strip()

        if not text:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/schedule <time>` — e.g. `/schedule 9am` or `/schedule 14:00` or `/schedule off`",
            )
            return

        parsed = _parse_schedule_time(text)
        if parsed == -1:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Couldn't parse *{text}* — try formats like `9am`, `8:30`, `14:00`, or `off`",
            )
            return

        prefs = _load_digest_prefs()
        if user_id not in prefs:
            prefs[user_id] = {"enabled": False}

        if parsed is None:
            prefs[user_id].pop("preferred_hour", None)
            _save_digest_prefs(prefs)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="✅ Digest schedule cleared — digest will use the default 24-hour interval.",
            )
        else:
            prefs[user_id]["preferred_hour"] = parsed
            _save_digest_prefs(prefs)
            if parsed == 0:
                hour_str = "12:00am"
            elif parsed < 12:
                hour_str = f"{parsed}:00am"
            elif parsed == 12:
                hour_str = "12:00pm"
            else:
                hour_str = f"{parsed - 12}:00pm"
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"✅ Digest scheduled for *{hour_str}* daily. Make sure `/digest on` is enabled!",
            )

    @app.command("/clear")
    async def handle_slash_clear(ack: Any, body: dict[str, Any], say: Any) -> None:
        await ack()
        user_id = body.get("user_id", "unknown")
        _compare_pending.pop(user_id, None)
        if user_id in _user_prefs:
            _user_prefs[user_id].pop("active_file_id", None)
            _user_prefs[user_id].pop("translate_file_id", None)
            _save_prefs()
        await say(
            text="✅ *Session cleared!* Thread history and active file selections have been reset. Start fresh with your next message."
        )

    @app.command("/nickname")
    async def handle_slash_nickname(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", "")
        name: str = (body.get("text") or "").strip()
        if not name:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/nickname <your name>` — e.g. `/nickname Chuck`",
            )
            return
        if user_id not in _personas:
            _personas[user_id] = {}
        _personas[user_id]["name"] = name
        _save_personas()
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"✅ Got it! I'll call you *{name}* from now on. 👋",
        )


def _register_integration_handlers(app: Any) -> None:
    """Register integration handlers: Gmail (/inbox, /email, /today, /calendar), Dropbox (/clawbox), channels (/clawchan)."""
    # ------------------------------------------------------------------
    # Wave 10 Leia: /inbox — show unread Gmail emails
    # ------------------------------------------------------------------

    @app.command("/inbox")
    async def handle_slash_inbox(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        if not _GOOGLE_REFRESH_TOKEN:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="📧 Gmail is not connected. Ask Dave to run `scripts/google_oauth_setup.py`.",
            )
            return
        emails = await _get_gmail_unread(max_results=5)
        global _gmail_message_cache
        _gmail_message_cache = emails
        if not emails:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="📭 No unread emails in your inbox.",
            )
            return
        blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": "📧 *Your unread emails:*"}}]
        for i, email in enumerate(emails, 1):
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{i}.* {email['subject']}\n_From: {email['from']}_",
                    },
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "📖 Summarize"},
                        "action_id": "gmail_summarize",
                        "value": email["id"],
                    },
                }
            )
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            blocks=blocks,
            text="Your unread emails",
        )

    # ------------------------------------------------------------------
    # Wave 10 Leia / Wave 11: /email — per-user Gmail via IMAP, or server OAuth fallback
    # ------------------------------------------------------------------
    # Subcommands:
    #   /email setup <address> <app_password>  — store personal Gmail creds
    #   /email forget                          — remove stored creds
    #   /email [today|week|<keyword>]          — read inbox or search
    #   /email <number>                        — (legacy) summarize email # from /inbox cache
    # ------------------------------------------------------------------

    @app.command("/email")
    async def handle_slash_email(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        text = (body.get("text") or "").strip()
        lower = text.lower()

        # -- /email setup <address> <app_password> --
        if lower.startswith("setup"):
            parts = text.split(None, 2)  # ["setup", "chuck@gmail.com", "xxxx xxxx xxxx xxxx"]
            if len(parts) < 3:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=(
                        "📧 *Gmail setup:*\n"
                        "`/email setup <your@gmail.com> <app password>`\n\n"
                        "To get an app password:\n"
                        "1. Go to myaccount.google.com → Security → 2-Step Verification (enable if needed)\n"
                        "2. myaccount.google.com → Security → *App Passwords* → Create\n"
                        "3. Copy the 16-character password and paste it here\n\n"
                        "_This message is only visible to you and is not stored in Slack._"
                    ),
                )
                return
            _, address, app_password = parts[0], parts[1], parts[2]
            _user_email_creds[user_id] = {"user": address, "password": app_password}
            _save_user_email_creds()
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    f"✅ *Gmail connected!*\n"
                    f"I'll use *{address}* when you run `/email`.\n"
                    f"Try `/email` to see your inbox, or `/email doctor` to search."
                ),
            )
            return

        # -- /email forget --
        if lower == "forget":
            if user_id in _user_email_creds:
                del _user_email_creds[user_id]
                _save_user_email_creds()
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="✅ Your Gmail credentials have been removed.",
                )
            else:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="ℹ️ No Gmail credentials were stored for you.",
                )
            return

        # -- Personal IMAP path (per-user creds stored) --
        creds = _user_email_creds.get(user_id)
        if creds:
            try:
                from email_skills import read_inbox, search_emails
            except ImportError:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="❌ Email skills module not available.",
                )
                return

            import os as _os

            _orig_user = _os.environ.get("GMAIL_USER")
            _orig_pass = _os.environ.get("GMAIL_APP_PASSWORD")
            _os.environ["GMAIL_USER"] = creds["user"]
            _os.environ["GMAIL_APP_PASSWORD"] = creds["password"]
            try:
                if not text or lower in ("today", "inbox"):
                    result = await read_inbox(count=10)
                elif lower == "week":
                    result = await read_inbox(count=25)
                else:
                    try:
                        idx = int(text) - 1
                        # legacy: summarize by number — fall through to OAuth path
                        result = None
                    except ValueError:
                        result = await search_emails(text)
            finally:
                if _orig_user is not None:
                    _os.environ["GMAIL_USER"] = _orig_user
                elif "GMAIL_USER" in _os.environ:
                    del _os.environ["GMAIL_USER"]
                if _orig_pass is not None:
                    _os.environ["GMAIL_APP_PASSWORD"] = _orig_pass
                elif "GMAIL_APP_PASSWORD" in _os.environ:
                    del _os.environ["GMAIL_APP_PASSWORD"]

            if result is not None:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=result)
                return
            # fall through to legacy number-based summarize below

        # -- Legacy number-based summarize (server OAuth path) --
        if not _GOOGLE_REFRESH_TOKEN and not creds:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "📧 *No Gmail connected yet.*\n"
                    "Run `/email setup your@gmail.com <app password>` to connect your own Gmail.\n\n"
                    "Need help? Type `/email setup` for step-by-step instructions."
                ),
            )
            return

        try:
            idx = int(text) - 1
        except ValueError:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/email 1` — summarize email #1 from your inbox. Run `/inbox` first.",
            )
            return
        emails = _gmail_message_cache or await _get_gmail_unread()
        if idx < 0 or idx >= len(emails):
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"No email #{idx + 1}. Run `/inbox` to see your emails.",
            )
            return
        msg_id = emails[idx]["id"]
        body_text = await _get_gmail_body(msg_id)
        summary = await _ask(
            f"Summarize this email in 3 bullet points:\n\n{body_text}",
            model_name=None,
        )
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"📧 *Email summary:*\n{summary}",
        )

    # ------------------------------------------------------------------
    # /today — Show today's Google Calendar events (Wave 10 Yoda)
    # ------------------------------------------------------------------

    @app.command("/today")
    async def handle_slash_today(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        if not _GOOGLE_REFRESH_TOKEN:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="📅 Google Calendar is not connected. Ask Dave to run `scripts/google_oauth_setup.py`.",
            )
            return
        events = await _get_calendar_events(days_ahead=0)
        msg = _format_calendar_events(events, label="today")
        await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)

    # ------------------------------------------------------------------
    # /calendar — Google Calendar events and event creation
    # ------------------------------------------------------------------

    @app.command("/calendar")
    async def handle_slash_calendar(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", "")
        text: str = (body.get("text") or "").strip().lower()
        name: str = _get_user_name(user_id)

        try:
            from calendar_skills import get_todays_events, get_upcoming_events
        except ImportError:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="❌ Calendar skills module not found.",
            )
            return

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"📅 Fetching calendar for {name}…",
        )

        if not text or text == "today":
            result = await get_todays_events()
        elif text == "week":
            result = await get_upcoming_events(days=7)
        else:
            result = await get_upcoming_events(days=7)

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=result,
        )

    # ------------------------------------------------------------------
    # /dropbox — Browse and sync Dropbox folder (per-user or server token)
    # ------------------------------------------------------------------
    # Subcommands:
    #   /dropbox connect         — one-click OAuth2 link (recommended)
    #   /dropbox setup <token>   — store personal Dropbox access token (advanced)
    #   /dropbox forget          — remove stored token
    #   /dropbox list            — list recent files
    #   /dropbox sync            — check for new files
    #   /dropbox status          — connection status
    # ------------------------------------------------------------------

    @app.command("/clawbox")
    async def handle_slash_dropbox(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        text = (body.get("text") or "").strip()
        lower = text.lower()

        # -- /dropbox connect — OAuth2 one-click flow --
        if lower == "connect":
            if not _DROPBOX_APP_KEY or not _OPENCLAW_PUBLIC_URL:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=(
                        "⚠️ Dropbox OAuth is not configured yet.\n\n"
                        "Ask Dave to add `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, and "
                        "`OPENCLAW_PUBLIC_URL` to the server's `.env` file."
                    ),
                )
                return

            state = secrets.token_urlsafe(16)
            _dropbox_oauth_states[state] = user_id

            redirect_uri = f"{_OPENCLAW_PUBLIC_URL}/dropbox/callback"
            auth_url = (
                "https://www.dropbox.com/oauth2/authorize"
                f"?client_id={urllib.parse.quote(_DROPBOX_APP_KEY)}"
                f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
                f"&response_type=code"
                f"&state={state}"
                f"&token_access_type=offline"
            )

            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "📦 *Connect your Dropbox*\n\n"
                    "Click the link below, sign into Dropbox, and click *Allow*:\n"
                    f"<{auth_url}|👉 Connect my Dropbox to OpenClaw>\n\n"
                    "_This link is private — only you can see it. It expires in 10 minutes._\n\n"
                    "Once you approve, OpenClaw will DM you a confirmation."
                ),
            )
            return

        # -- /dropbox setup <token> --
        if lower.startswith("setup"):
            parts = text.split(None, 1)
            if len(parts) < 2:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=(
                        "📦 *Dropbox setup:*\n"
                        "`/dropbox setup <your-access-token>`\n\n"
                        "To get a token:\n"
                        "1. Go to <https://www.dropbox.com/developers/apps|dropbox.com/developers/apps>\n"
                        "2. Create a new app → *Full Dropbox* access\n"
                        "3. Under *OAuth 2* → click *Generate* access token\n"
                        "4. Paste it here: `/dropbox setup sl.your_token_here`\n\n"
                        "_This message is only visible to you._"
                    ),
                )
                return
            token = parts[1].strip()
            _user_dropbox_tokens[user_id] = {"token": token, "watch_path": "/OpenClaw"}
            _save_user_dropbox_tokens()
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "✅ *Dropbox connected!*\n"
                    "I'll watch your `/OpenClaw` folder for new files.\n"
                    "Create that folder in Dropbox, then drop files there and I'll notify you.\n"
                    "Try `/dropbox list` to see recent files."
                ),
            )
            return

        # -- /dropbox forget --
        if lower == "forget":
            if user_id in _user_dropbox_tokens:
                del _user_dropbox_tokens[user_id]
                _save_user_dropbox_tokens()
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="✅ Your Dropbox token has been removed.",
                )
            else:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="ℹ️ No Dropbox token was stored for you.",
                )
            return

        # -- Resolve active token: per-user first, then server-level --
        user_dbx_creds = _user_dropbox_tokens.get(user_id)
        active_token = (user_dbx_creds or {}).get("token") or _DROPBOX_TOKEN
        active_folder = (user_dbx_creds or {}).get("watch_path") or _DROPBOX_FOLDER

        if active_token is None:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "📦 *Dropbox not connected yet.*\n"
                    "Run `/dropbox setup` to see how to connect your own Dropbox account.\n"
                    "Or ask Dave to set up the shared `DROPBOX_APP_TOKEN`."
                ),
            )
            return

        if lower in ("sync", ""):
            count = await _dropbox_sync_new_files(client)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"☁️ Dropbox sync complete — {count} new file(s) pulled.",
            )
        elif lower == "list":
            files = _dropbox_list_folder(active_folder, token=active_token)[:10]
            if not files:
                msg = f"☁️ No files found in Dropbox folder `{active_folder}`."
            else:
                lines = [f"☁️ *Dropbox — {active_folder}* (last {len(files)} files)"]
                for f in files:
                    lines.append(f"• 📄 {f['name']}  ·  {f['modified']}")
                msg = "\n".join(lines)
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)
        elif lower == "status":
            folder_files = _dropbox_list_folder(active_folder, token=active_token)
            source = "personal" if user_dbx_creds else "shared"
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"✅ Dropbox connected ({source}). Watching `{active_folder}` — {len(folder_files)} file(s) found.",
            )
        else:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Usage: `/dropbox list` · `/dropbox sync` · `/dropbox status` · `/dropbox setup <token>`",
            )

    # ------------------------------------------------------------------
    # Background: Dropbox poll loop (Wave 10)
    # ------------------------------------------------------------------

    async def _dropbox_poll_loop() -> None:
        """Poll Dropbox every 30 minutes for new files."""
        if _DROPBOX_TOKEN is None:
            return
        while True:
            await asyncio.sleep(1800)  # 30 minutes
            try:
                await _dropbox_sync_new_files(app.client)
            except Exception:  # noqa: BLE001
                pass

    asyncio.ensure_future(_dropbox_poll_loop())

    # ------------------------------------------------------------------
    # Handler: /channels — list and archive Slack channels (Wave 14)
    # Requires SLACK_USER_TOKEN (xoxp-...) with channels:read,
    # channels:manage, groups:read, groups:write scopes.
    # ------------------------------------------------------------------

    @app.command("/clawchan")
    async def handle_slash_channels(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user_id", "")
        channel_id = body.get("channel_id", user_id)
        text = (body.get("text") or "").strip()
        lower = text.lower()

        if not SLACK_USER_TOKEN or not SLACK_USER_TOKEN.startswith("xoxp-"):
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "⚠️ *Channel management not configured.*\n\n"
                    "Ask Dave to add `SLACK_USER_TOKEN` (xoxp-...) to the server's `.env` file.\n"
                    "The token requires `channels:read`, `channels:manage`, `groups:read`, and `groups:write` scopes."
                ),
            )
            return

        import aiohttp as _aiohttp

        # Helper: call Slack API with user token
        async def _user_api(method: str, payload: dict) -> dict:
            url = f"https://slack.com/api/{method}"
            headers = {"Authorization": f"Bearer {SLACK_USER_TOKEN}", "Content-Type": "application/json"}
            async with _aiohttp.ClientSession() as sess:
                async with sess.post(url, json=payload, headers=headers) as resp:
                    return await resp.json()

        # -- /channels list --
        if not text or lower == "list":
            try:
                result = await _user_api(
                    "conversations.list",
                    {"types": "public_channel,private_channel", "exclude_archived": True, "limit": 50},
                )
                if not result.get("ok"):
                    raise RuntimeError(result.get("error", "unknown"))
                channels = result.get("channels", [])
                if not channels:
                    msg = "📋 No active channels found."
                else:
                    lines = ["📋 *Active Slack channels:*\n"]
                    for ch in sorted(channels, key=lambda c: c.get("name", "")):
                        name = ch.get("name", "?")
                        members = ch.get("num_members", "?")
                        is_private = "🔒" if ch.get("is_private") else "#"
                        lines.append(f"  {is_private} {name}  ({members} members)")
                    lines.append(
                        f"\n_{len(channels)} channel(s) total. Use `/clawchan archive <name>` to archive one._"
                    )
                    msg = "\n".join(lines)
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)
            except Exception as exc:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"❌ Failed to list channels: {exc}"
                )
            return

        # -- /channels archive <name> --
        if lower.startswith("archive "):
            target_name = text.split(None, 1)[1].strip().lstrip("#")
            try:
                # First: look up channel ID by name
                result = await _user_api(
                    "conversations.list",
                    {"types": "public_channel,private_channel", "exclude_archived": True, "limit": 200},
                )
                if not result.get("ok"):
                    raise RuntimeError(result.get("error", "unknown"))
                channels = result.get("channels", [])
                match = next((c for c in channels if c.get("name", "").lower() == target_name.lower()), None)
                if not match:
                    await client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text=f"⚠️ Channel `#{target_name}` not found (or already archived).",
                    )
                    return
                arch_result = await _user_api("conversations.archive", {"channel": match["id"]})
                if not arch_result.get("ok"):
                    raise RuntimeError(arch_result.get("error", "unknown"))
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"✅ Channel `#{target_name}` has been archived.",
                )
            except Exception as exc:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text=f"❌ Failed to archive `#{target_name}`: {exc}"
                )
            return

        # -- usage fallback --
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=(
                "📋 *Channel management commands:*\n"
                "• `/channels list` — list all active channels\n"
                "• `/channels archive <name>` — archive a channel\n\n"
                "_Note: channel deletion is not supported by Slack's API — archive is the closest option._"
            ),
        )

    # ------------------------------------------------------------------
    # /drive — Google Drive file browser and uploader
    # ------------------------------------------------------------------
    # Subcommands:
    #   /drive list [query]       — list Drive files, optional Drive query syntax
    #   /drive read <file_id>     — read/export a file as plain text
    #   /drive upload <name> <content> — (advanced) create a new text file
    # ------------------------------------------------------------------

    @app.command("/drive")
    async def handle_slash_drive(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", user_id)
        text: str = (body.get("text") or "").strip()
        parts = text.split(None, 1)
        sub = parts[0].lower() if parts else "list"
        arg = parts[1] if len(parts) > 1 else ""

        if not _GOOGLE_REFRESH_TOKEN:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="📁 Google Drive is not connected. Ask Dave to run `scripts/google_oauth_setup.py` with Drive scopes.",
            )
            return

        try:
            from calendar_skills import list_drive_files, read_drive_file
        except ImportError:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="❌ Drive skills module not found.",
            )
            return

        if sub in ("list", ""):
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"📁 Listing Drive files{f' matching `{arg}`' if arg else ''}…",
            )
            result = await list_drive_files(query=arg)
        elif sub == "read":
            if not arg:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Usage: `/drive read <file_id>`",
                )
                return
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"📄 Reading Drive file `{arg}`…",
            )
            result = await read_drive_file(file_id=arg)
        else:
            result = (
                "📁 *Drive commands:*\n"
                "• `/drive list [query]` — list files (optional Drive query filter)\n"
                "• `/drive read <file_id>` — read/export a file as plain text"
            )

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=result,
        )

    # ------------------------------------------------------------------
    # /contacts — Google People / Contacts search
    # ------------------------------------------------------------------
    # Subcommands:
    #   /contacts search <query>       — search contacts by name or email
    #   /contacts get <resource_name>  — get full contact details
    # ------------------------------------------------------------------

    @app.command("/contacts")
    async def handle_slash_contacts(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", user_id)
        text: str = (body.get("text") or "").strip()
        parts = text.split(None, 1)
        sub = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        if not _GOOGLE_REFRESH_TOKEN:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="👤 Google Contacts is not connected. Ask Dave to run `scripts/google_oauth_setup.py` with Contacts scopes.",
            )
            return

        try:
            from calendar_skills import get_contact, search_contacts
        except ImportError:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="❌ Contacts skills module not found.",
            )
            return

        if sub == "search":
            if not arg:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Usage: `/contacts search <name or email>`",
                )
                return
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"👤 Searching contacts for `{arg}`…",
            )
            result = await search_contacts(query=arg)
        elif sub == "get":
            if not arg:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Usage: `/contacts get <resource_name>` (e.g. `people/c1234`)",
                )
                return
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"👤 Fetching contact `{arg}`…",
            )
            result = await get_contact(resource_name=arg)
        else:
            result = (
                "👤 *Contacts commands:*\n"
                "• `/contacts search <query>` — search by name or email\n"
                "• `/contacts get <resource_name>` — full contact details (e.g. `people/c1234`)"
            )

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=result,
        )

    # ------------------------------------------------------------------
    # Handler: /incident — Slack bridge to Incident Copilot
    #
    # Mirrors the Discord IncidentCog (src/cogs/incident_cog.py) by reusing
    # the same shared incident_store + incident_copilot modules. Owner-only:
    # access is gated by OPENCLAW_INCIDENT_ALLOWED_USERS (CSV of Slack user
    # IDs), falling back to SLACK_NOTIFY_USER_ID.
    #
    # Subcommands:
    #   /incident start <title>              — open incident + run Copilot
    #   /incident status <id>                — show incident summary
    #   /incident resolve <id> [postmortem]  — close incident
    #   /incident list                       — recent incidents
    #   /incident timeline <id>              — event timeline
    # ------------------------------------------------------------------

    @app.command("/incident")
    async def handle_slash_incident(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", user_id)
        text: str = (body.get("text") or "").strip()

        allowed = _incident_allowed_user_ids()
        if not allowed:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "🛑 `/incident` is not configured. Set `OPENCLAW_INCIDENT_ALLOWED_USERS` "
                    "(or `SLACK_NOTIFY_USER_ID`) to a Slack user ID."
                ),
            )
            return
        if user_id not in allowed:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="🛑 You are not allowed to run `/incident` on this workspace.",
            )
            return

        # Lazy imports — these modules pull in LLM/skills layers and we want
        # slack_bot import to stay cheap when /incident isn't used.
        try:
            from incident_copilot import execute_incident_action, generate_incident_report  # noqa: F401
            from incident_workflows import incident_store
        except ImportError as exc:
            log.warning("/incident: incident modules unavailable: %s", exc)
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"❌ Incident workflow not available: `{exc}`",
            )
            return

        parts = text.split(None, 2)
        sub = (parts[0].lower() if parts else "") or "help"
        arg1 = parts[1] if len(parts) > 1 else ""
        arg2 = parts[2] if len(parts) > 2 else ""

        if sub in {"", "help"}:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    "🚨 *Incident Copilot commands:*\n"
                    "• `/incident start <title>` — open incident, run Copilot, post actions\n"
                    "• `/incident status <id>` — show incident summary\n"
                    "• `/incident resolve <id> [postmortem...]` — close incident\n"
                    "• `/incident list` — recent incidents\n"
                    "• `/incident timeline <id>` — event timeline"
                ),
            )
            return

        if sub == "list":
            try:
                rows = incident_store.list_recent(limit=10)
            except Exception as exc:  # broad: surface to user
                log.warning("/incident list failed: %s", exc)
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ {exc}")
                return
            if not rows:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text="📭 No incidents recorded yet.")
                return
            lines = ["🚨 *Recent incidents:*"]
            for inc in rows:
                lines.append(
                    f"• #{inc.get('id')} [{inc.get('status', 'open')}] "
                    f"{inc.get('severity', '?')} — {(inc.get('title') or '')[:80]}"
                )
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines))
            return

        if sub == "status":
            inc_id = _parse_int(arg1)
            if inc_id is None:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text="Usage: `/incident status <id>`"
                )
                return
            inc = incident_store.get_incident(inc_id)
            if not inc:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ Incident #{inc_id} not found.")
                return
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=(
                    f"🚨 *Incident #{inc['id']}* — `{inc.get('status', '?')}` / `{inc.get('severity', '?')}`\n"
                    f"*Title:* {inc.get('title', '')}\n"
                    f"*Description:* {(inc.get('description') or '_(none)_')[:1500]}\n"
                    f"*Created:* {inc.get('created_at', '?')} by {inc.get('created_by_name', '?')}"
                ),
            )
            return

        if sub == "timeline":
            inc_id = _parse_int(arg1)
            if inc_id is None:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text="Usage: `/incident timeline <id>`"
                )
                return
            events = incident_store.get_timeline(inc_id, limit=20)
            if not events:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"📭 No events for incident #{inc_id}.")
                return
            lines = [f"🕒 *Timeline for incident #{inc_id}:*"]
            for ev in events:
                lines.append(
                    f"• `{ev.get('created_at', '?')}` *{ev.get('event_type', '?')}* — "
                    f"{(ev.get('note') or '')[:200]}"
                )
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines[:25]))
            return

        if sub == "resolve":
            inc_id = _parse_int(arg1)
            if inc_id is None:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text="Usage: `/incident resolve <id> [postmortem...]`"
                )
                return
            try:
                inc = incident_store.resolve_incident(
                    inc_id,
                    postmortem=arg2 or None,
                    actor_id=None,
                    actor_name=user_id,
                )
            except Exception as exc:  # broad: surface to user
                log.warning("/incident resolve failed: %s", exc)
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ {exc}")
                return
            if not inc:
                await client.chat_postEphemeral(channel=channel_id, user=user_id, text=f"❌ Incident #{inc_id} not found.")
                return
            _incident_actions_cache.pop(inc_id, None)
            await client.chat_postMessage(
                channel=channel_id,
                text=f"✅ Incident #{inc['id']} resolved by <@{user_id}>.",
            )
            return

        if sub == "start":
            title = (arg1 + (" " + arg2 if arg2 else "")).strip()
            if not title:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text="Usage: `/incident start <title>`"
                )
                return
            await _run_incident_start(client=client, channel_id=channel_id, user_id=user_id, title=title)
            return

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"❓ Unknown subcommand `{sub}`. Try `/incident help`.",
        )


def _register_action_handlers(app: Any) -> None:
    """Register all @app.action handlers: file actions, retry/clarify, and Gmail summarize."""

    # ------------------------------------------------------------------
    # Handler: 🔀 Compare — first step, store Document A and prompt for B
    # ------------------------------------------------------------------

    @app.action("file_compare_start")
    async def handle_compare_start(ack: Any, body: dict[str, Any], say: Any) -> None:
        await ack()
        user_id = (body.get("user") or {}).get("id", "unknown")
        actions = body.get("actions", [{}])
        file_id = (actions[0] if actions else {}).get("value", "")
        if not file_id:
            await say(text="⚠️ Couldn't identify the file. Please try again.")
            return
        _compare_pending[user_id] = file_id
        file_obj_entry = _file_registry.get(file_id) or {}
        if isinstance(file_obj_entry, dict) and "file_obj" in file_obj_entry:
            file_obj_entry = file_obj_entry["file_obj"]
        filename = (file_obj_entry.get("name") or "the file") if file_obj_entry else "the file"
        await say(text=f"📄 Got *{filename}* as Document A. Now upload or share Document B and I'll compare them.")

    # ------------------------------------------------------------------
    # Handler: 🌍 Translate — language picker + translation dispatch
    # ------------------------------------------------------------------

    @app.action("file_translate")
    async def handle_translate_pick(ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
        await ack()
        user_id = (body.get("user") or {}).get("id", "unknown")
        actions = body.get("actions", [{}])
        file_id = (actions[0] if actions else {}).get("value", "")
        channel = (body.get("channel") or {}).get("id", "") or (body.get("container") or {}).get("channel_id", "")

        if user_id not in _user_prefs:
            _user_prefs[user_id] = {}
        _user_prefs[user_id]["translate_file_id"] = file_id
        _save_prefs()

        lang_options = [
            {"text": {"type": "plain_text", "text": lang}, "value": lang}
            for lang in [
                "Spanish",
                "French",
                "German",
                "Italian",
                "Portuguese",
                "Japanese",
                "Chinese (Simplified)",
                "Korean",
                "Arabic",
                "Russian",
            ]
        ]
        await client.chat_postEphemeral(
            channel=channel,
            user=user_id,
            text="Pick a language to translate to:",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "🌍 *Pick a language to translate to:*"},
                    "accessory": {
                        "type": "static_select",
                        "placeholder": {"type": "plain_text", "text": "Select language"},
                        "options": lang_options,
                        "action_id": "translate_lang_selected",
                    },
                }
            ],
        )

    @app.action("translate_lang_selected")
    async def handle_translate_lang_selected(ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
        await ack()
        user_id = (body.get("user") or {}).get("id", "unknown")
        actions = body.get("actions", [{}])
        selected_lang = (actions[0] if actions else {}).get("selected_option", {}).get("value", "Spanish")
        channel = (body.get("channel") or {}).get("id", "") or (body.get("container") or {}).get("channel_id", "")

        file_id = (_user_prefs.get(user_id) or {}).get("translate_file_id", "")
        if not file_id:
            await say(text="⚠️ Couldn't find the file to translate. Please tap 🌍 Translate again.")
            return

        file_obj_entry = _file_registry.get(file_id) or {}
        if isinstance(file_obj_entry, dict) and "file_obj" in file_obj_entry:
            file_obj = file_obj_entry["file_obj"]
        else:
            file_obj = file_obj_entry or {}

        if user_id not in _user_prefs:
            _user_prefs[user_id] = {}
        _user_prefs[user_id]["translate_lang"] = selected_lang
        _save_prefs()

        thinking_resp = await say(text=f"⏳ Translating to {selected_lang}…")
        thinking_ts = (thinking_resp or {}).get("ts")
        use_simple = _get_user_simple(user_id)

        translate_prompt = (
            f"Please translate this document into {selected_lang}. "
            "Preserve the original formatting and structure as much as possible. "
            "Return only the translated text."
        )
        prompt = await _process_slack_files([file_obj], SLACK_BOT_TOKEN, translate_prompt)

        await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=None,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            simple=use_simple,
            model_pref="gemini",
        )

    # ------------------------------------------------------------------
    # Handler: retry / clarify button actions
    # ------------------------------------------------------------------

    @app.action("retry_last_prompt")
    async def handle_retry_last_prompt(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        prompt_hash: str = (body.get("actions") or [{}])[0].get("value", "")
        user_id: str = (body.get("user") or {}).get("id", "")
        channel: str = (body.get("channel") or {}).get("id", "")

        prompt = _retry_cache.get(prompt_hash)
        if not prompt:
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text="⚠️ Retry context expired — please send your message again.",
            )
            return

        use_simple = _get_user_simple(user_id)
        thinking_resp = await say(text="⏳ Retrying…")
        thinking_ts = (thinking_resp or {}).get("ts")

        await _send_answer(
            client=client,
            say=say,
            channel=channel,
            thread_ts=None,
            thinking_ts=thinking_ts,
            prompt=prompt,
            user_id=user_id,
            model_pref="auto",
            simple=use_simple,
        )

    @app.action("clarify_file")
    async def handle_clarify_file(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user", {}).get("id", "")
        channel = (body.get("channel") or {}).get("id", user_id)
        await client.chat_postEphemeral(
            channel=channel,
            user=user_id,
            text="📄 Go ahead and upload your file, then type your question about it!",
        )

    @app.action("clarify_question")
    async def handle_clarify_question(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user", {}).get("id", "")
        channel = (body.get("channel") or {}).get("id", user_id)
        await client.chat_postEphemeral(
            channel=channel,
            user=user_id,
            text="💬 Of course! What would you like to know? Just type your question.",
        )

    @app.action("clarify_write")
    async def handle_clarify_write(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = body.get("user", {}).get("id", "")
        channel = (body.get("channel") or {}).get("id", user_id)
        await client.chat_postEphemeral(
            channel=channel,
            user=user_id,
            text="📝 Happy to help! What are you working on — a letter, email, list, or something else?",
        )

    # ------------------------------------------------------------------
    # Handler: Gmail summarize button action
    # ------------------------------------------------------------------

    @app.action("gmail_summarize")
    async def handle_gmail_summarize(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        action = body.get("actions", [{}])[0]
        message_id = action.get("value", "")
        user_id = body.get("user", {}).get("id", "")
        channel_id = body.get("channel", {}).get("id", user_id)
        if not message_id:
            return
        body_text = await _get_gmail_body(message_id)
        summary = await _ask(
            f"Summarize this email in 3 bullet points:\n\n{body_text}",
            model_name=None,
        )
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"📧 *Email summary:*\n{summary}",
        )

    # ------------------------------------------------------------------
    # Handler: 🚨 Incident action — execute a Copilot-suggested action
    #
    # Mirrors the Discord IncidentActionView button flow. Identity-gated by
    # OPENCLAW_INCIDENT_ALLOWED_USERS (or SLACK_NOTIFY_USER_ID). Actions are
    # additionally gated server-side by incident_copilot.SAFE_RESTART_TARGETS.
    # ------------------------------------------------------------------

    @app.action("incident_action_run")
    async def handle_incident_action_run(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        user_id = (body.get("user") or {}).get("id", "unknown")
        channel_id = (body.get("channel") or {}).get("id", "") or (body.get("container") or {}).get("channel_id", "")
        message_ts = (body.get("message") or {}).get("ts") or (body.get("container") or {}).get("message_ts", "")

        allowed = _incident_allowed_user_ids()
        if not allowed or user_id not in allowed:
            await client.chat_postEphemeral(
                channel=channel_id or user_id,
                user=user_id,
                text="🛑 You are not allowed to run incident actions on this workspace.",
            )
            return

        actions_payload = body.get("actions") or [{}]
        raw_value = (actions_payload[0] if actions_payload else {}).get("value", "")
        parsed = _parse_action_button_value(raw_value)
        if parsed is None:
            await client.chat_postEphemeral(
                channel=channel_id or user_id, user=user_id, text="❌ Malformed action button payload."
            )
            return
        incident_id, action_idx = parsed

        cached = _incident_cache_get(incident_id)
        if not cached or action_idx < 0 or action_idx >= len(cached):
            await client.chat_postEphemeral(
                channel=channel_id or user_id,
                user=user_id,
                text=(
                    f"⌛ Incident #{incident_id} actions are no longer cached "
                    f"(TTL {_INCIDENT_CACHE_TTL_S}s). Re-run `/incident start <title>` to refresh."
                ),
            )
            return
        action = cached[action_idx]
        if not action.get("executable"):
            await client.chat_postEphemeral(
                channel=channel_id or user_id, user=user_id, text="ℹ️ That action is recommendation-only."
            )
            return

        try:
            from incident_copilot import execute_incident_action
            from incident_workflows import incident_store
        except ImportError as exc:
            await client.chat_postEphemeral(
                channel=channel_id or user_id, user=user_id, text=f"❌ Incident modules unavailable: `{exc}`"
            )
            return

        title = str(action.get("title", "action"))[:120]
        command = str(action.get("command", ""))
        target = str(action.get("target", ""))

        try:
            incident_store.append_event(
                incident_id,
                event_type="copilot_action_requested",
                note=f"{command}:{target} requested by {user_id} via Slack",
                actor_id=None,
                actor_name=user_id,
            )
        except Exception as exc:  # broad: telemetry must not block execution
            log.warning("incident_action_run: append_event(requested) failed: %s", exc)

        try:
            result = await execute_incident_action(action)
        except Exception as exc:  # broad: surface to user
            log.warning("incident_action_run: execute failed: %s", exc)
            result = f"❌ Action failed: {exc}"

        try:
            incident_store.append_event(
                incident_id,
                event_type="copilot_action_executed",
                note=f"{command}:{target} => {str(result)[:300]}",
                actor_id=None,
                actor_name=user_id,
            )
        except Exception as exc:  # broad
            log.warning("incident_action_run: append_event(executed) failed: %s", exc)

        reply_text = (
            f"🚨 *Incident #{incident_id}* — action *{title}* executed by <@{user_id}>\n"
            f"```\n{str(result)[:1800]}\n```"
        )
        try:
            if channel_id and message_ts:
                await client.chat_postMessage(channel=channel_id, thread_ts=message_ts, text=reply_text)
            else:
                await client.chat_postMessage(channel=channel_id or user_id, text=reply_text)
        except Exception as exc:  # broad: fall back to ephemeral
            log.warning("incident_action_run: chat_postMessage failed: %s", exc)
            await client.chat_postEphemeral(channel=channel_id or user_id, user=user_id, text=reply_text)


def create_slack_app() -> Any | None:  # type: ignore[return]
    """Build and return a configured AsyncApp, or None if Slack is disabled."""
    if not _slack_is_configured():
        return None

    try:
        from slack_bolt.async_app import AsyncApp
    except ImportError:
        log.error("slack_bolt not installed — run: pip install slack_bolt>=1.18.0")
        return None

    app = AsyncApp(token=SLACK_BOT_TOKEN)

    _register_core_handlers(app)
    _register_file_handlers(app)
    _register_slash_commands(app)
    _register_integration_handlers(app)
    _register_action_handlers(app)

    return app


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def create_slack_handler() -> Any | None:  # type: ignore[return]
    """Return an AsyncSocketModeHandler configured for this app, or None."""
    global _BOT_USER_ID, _BOT_START_TIME

    app = create_slack_app()
    if app is None:
        return None

    try:
        from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
    except ImportError:
        log.error("AsyncSocketModeHandler not available — ensure slack_bolt[async]>=1.18.0 is installed")
        return None

    _BOT_START_TIME = time.monotonic()

    # Resolve the bot's own user ID so thread-history can distinguish bot messages
    try:
        auth = await app.client.auth_test()
        _BOT_USER_ID = auth.get("user_id", "")
        log.info("Slack bot user ID: %s", _BOT_USER_ID)
    except Exception as exc:
        log.warning("Could not resolve Slack bot user ID: %s", exc)

    handler = AsyncSocketModeHandler(app, SLACK_APP_TOKEN)
    log.info("Slack Socket Mode handler created")

    # Start proactive file-alert loop (works whether bot is started via
    # start_slack_bot() or the main Discord bot's create_slack_handler()).
    if SLACK_NOTIFY_USER_ID:
        asyncio.create_task(_file_alert_loop(app.client))
        log.info("Proactive file-alert loop started (notifying %s)", SLACK_NOTIFY_USER_ID)

    # Make the Slack client available to the upload HTTP handler
    global _slack_client_ref
    _slack_client_ref = app.client

    # Seed digest prefs for notify user so digest is on by default
    if SLACK_NOTIFY_USER_ID:
        _prefs = _load_digest_prefs()
        if SLACK_NOTIFY_USER_ID not in _prefs:
            _prefs[SLACK_NOTIFY_USER_ID] = {"enabled": True, "last_sent": 0}
            _save_digest_prefs(_prefs)
            log.info("Seeded digest prefs for notify user %s", SLACK_NOTIFY_USER_ID)

    # Start digest background loop
    asyncio.create_task(_digest_loop(app.client))
    log.info("Digest loop started")

    # Start Dropbox watch loop (no-op when DROPBOX_ACCESS_TOKEN not set)
    try:
        from dropbox_sync import DROPBOX_CONFIGURED, dropbox_watch_loop

        if DROPBOX_CONFIGURED and SLACK_NOTIFY_USER_ID:
            asyncio.create_task(dropbox_watch_loop(app.client, SLACK_NOTIFY_USER_ID))
            log.info("Dropbox watch loop started")
    except ImportError:
        pass

    return handler


async def start_slack_bot() -> None:
    """Create the Socket Mode handler and run until the process exits."""
    handler = await create_slack_handler()
    if handler is None:
        log.warning("Slack bot not started (disabled or misconfigured)")
        return

    log.info("Starting Slack Socket Mode bot…")
    await handler.start_async()


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    asyncio.run(start_slack_bot())
