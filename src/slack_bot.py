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
  5. Enable slash command: /ask (any Request URL placeholder works in Socket Mode)
  6. Install app to workspace
  7. Copy Bot User OAuth Token (xoxb-...) to SLACK_BOT_TOKEN
  8. Copy App-Level Token (xapp-...) to SLACK_APP_TOKEN

Features:
  - @mention in channels → OpenClaw answer (in-thread)
  - DMs → OpenClaw answer
  - Thread context: follow-up messages in a thread carry prior Q&A as history
  - Model selector: append --model gemini|openai|anthropic|copilot|auto to any prompt
  - /ask slash command: native Slack slash command
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

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import aiohttp

import file_skills

from constants import ATTACHMENT_TEXT_MAX_CHARS
from document_skills import create_word
from http_session import SessionManager
from llm import analyze_image as llm_analyze_image

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


# Load prefs at import time so they are ready before any handler fires.
_load_prefs()

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
    "👋 *Hi! I'm OpenClaw — your personal AI assistant.*\n\n"
    "Here's what I can do:\n"
    "• 📄 *Edit or proofread a document* → drag in a Word file and say \"fix this\" or \"make it more professional\"\n"
    "• 📊 *Understand a spreadsheet* → upload your Excel file and ask \"what does this show?\" or \"summarize this\"\n"
    "• 💬 *Answer any question* → just ask, like you would Google or Gemini\n"
    "• 🖼️ *Describe an image* → drop in a photo and ask what's in it\n\n"
    "*Try it now:* upload a file, or just type a question!\n"
    "Type `/help` anytime to see examples."
)

_HELP_TEXT = (
    "*📚 OpenClaw Quick Help*\n\n"
    "*Working with files:*\n"
    "• Drag in a Word doc (.docx) → \"proofread this\" / \"make this more formal\" / \"summarize in 5 bullet points\"\n"
    "• Drag in an Excel file (.xlsx) → \"what is this tracking?\" / \"explain column C\" / \"find any errors\"\n"
    "• Drag in a PDF → \"summarize this\"\n"
    "• Drop in a photo → \"what's in this image?\"\n\n"
    "*Just chatting:*\n"
    "• Ask anything — \"what's the weather in Boston?\" / \"explain this email to me\" / \"help me write a thank-you note\"\n\n"
    "*Tips:*\n"
    "• `/simple on` — always get plain, easy-to-read answers (no need to type `--simple` every time)\n"
    "• Add `--simple` to any one message for a one-off plain answer\n"
    "• Reply in a thread to keep context from earlier messages\n\n"
    "_Example: Upload Budget2025.xlsx and type: \"summarize the totals for me\"_"
)

_SIMPLE_FLAG_RE = re.compile(r"\s*--simple\b", re.IGNORECASE)
_SIMPLE_SYSTEM_PREFIX = (
    "Please respond in plain, simple language. Avoid jargon and technical terms. "
    "Use short sentences. Write as if explaining to someone who is not technical. "
)

# ------------------------------------------------------------------
# Bot message registry for 👍/👎 feedback
# key: (channel, message_ts)  value: originating user_id
# ------------------------------------------------------------------
_bot_message_registry: dict[tuple[str, str], str] = {}

# Populated once after the Slack client performs auth.test
_BOT_USER_ID: str = ""

# ---------------------------------------------------------------------------
# Feature flag check
# ---------------------------------------------------------------------------

SLACK_ENABLED = os.getenv("SLACK_ENABLED", "false").lower() == "true"
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    log.warning(
                        "Failed to download Slack file %s: HTTP %d", filename, resp.status
                    )
                    question += f"\n\n[Attachment: failed to download {filename}]"
                    continue

                data = await resp.read()

            if mimetype.startswith("image/"):
                image_answer = await llm_analyze_image(data, mimetype, question)
                question = f"{question}\n\n[Image attachment analysis: {image_answer}]"
            elif (
                mimetype.startswith("text/")
                or mimetype in ("application/pdf", "application/json")
                or mimetype.startswith("application/vnd.")
            ):
                doc_text = data.decode("utf-8", errors="replace")[:ATTACHMENT_TEXT_MAX_CHARS]
                question = (
                    f"{question}\n\n--- Attached Document: {filename} "
                    f"(first {ATTACHMENT_TEXT_MAX_CHARS} chars) ---\n"
                    f"{doc_text}\n"
                    f"--- End Document ---"
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


async def _build_thread_history(
    client: Any, channel: str, thread_ts: str
) -> list[dict[str, str]]:
    """Fetch previous messages in *thread_ts* and return them as conversation history.

    The last message (the current prompt) is excluded — the caller supplies that
    as the ``prompt`` argument to ``_ask``.
    """
    global _BOT_USER_ID
    try:
        result = await client.conversations_replies(
            channel=channel, ts=thread_ts, limit=20
        )
        messages: list[dict] = result.get("messages", [])
        history: list[dict[str, str]] = []
        for msg in messages[:-1]:  # exclude the triggering message
            content = (msg.get("text") or "").strip()
            if not content or content == "⏳ Thinking…":
                continue
            is_bot = bool(msg.get("bot_id")) or (
                _BOT_USER_ID and msg.get("user") == _BOT_USER_ID
            )
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

# Prompts sent to the LLM when a file action button is clicked
_FILE_ACTION_PROMPTS: dict[str, str] = {
    "file_proofread": (
        "Please proofread this document and correct any grammar, spelling, or punctuation "
        "errors. List each correction clearly."
    ),
    "file_summarize": "Please summarize the key points in a few bullet points.",
    "file_explain": (
        "Please explain what this document is about in plain, simple language. "
        "Assume the reader is non-technical."
    ),
    "file_errors": (
        "Please identify any errors, inconsistencies, unusual values, or potential problems "
        "in this document. Be specific."
    ),
    "file_describe": "Please describe what is in this image in detail.",
    "file_read_text": "Please read and transcribe all text visible in this image.",
}


def _register_file(file_id: str, file_obj: dict, file_bytes: bytes | None = None) -> None:
    """Store *file_obj* (and optionally raw bytes) in the registry, pruning to 200 entries."""
    _file_registry[file_id] = {"file_obj": file_obj, "file_bytes": file_bytes}
    if len(_file_registry) > 200:
        oldest = next(iter(_file_registry))
        del _file_registry[oldest]


def _build_file_blocks(
    filename: str, description: str | None, mimetype: str, file_id: str
) -> list[dict]:
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
        ]

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "actions", "elements": buttons},
    ]


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




async def _ask(
    prompt: str,
    user_id: str,
    *,
    model_pref: str = "auto",
    history: list[dict] | None = None,
    simple: bool = False,
) -> str:
    """Route a prompt through OpenClaw's agent ask pipeline."""
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
        return str(payload.get("response") or payload.get("text") or "(no response)").strip()
    except Exception as exc:  # broad: intentional
        log.error("_execute_agent_ask failed for slack user %s: %s", user_id, exc)
        raise


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
    try:
        answer = await _ask(prompt, user_id, model_pref=model_pref, history=history, simple=simple)
        text = _clean_for_slack(answer) if answer else "(no response)"
    except Exception as exc:
        text = f"❌ Sorry, something went wrong: {exc}"

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
# Slack app factory
# ---------------------------------------------------------------------------

def create_slack_app():  # type: ignore[return]
    """Build and return a configured AsyncApp, or None if Slack is disabled."""
    if not _slack_is_configured():
        return None

    try:
        from slack_bolt.async_app import AsyncApp
    except ImportError:
        log.error("slack_bolt not installed — run: pip install slack_bolt>=1.18.0")
        return None

    app = AsyncApp(token=SLACK_BOT_TOKEN)

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

        if not raw_text and not files:
            await say(text=_WELCOME_MESSAGE)
            return

        prompt, model_pref, use_simple = _parse_flags(raw_text)
        use_simple = use_simple or _get_user_simple(user_id)

        # Enrich prompt with any uploaded file content
        if files:
            prompt = await _process_slack_files(files, SLACK_BOT_TOKEN, prompt)

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
    # Handler: /ask slash command
    # ------------------------------------------------------------------

    @app.command("/ask")
    async def handle_slash_ask(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()  # must acknowledge within 3 seconds

        user_id: str = body.get("user_id", "unknown")
        channel: str = body.get("channel_id", "")
        raw_text: str = (body.get("text") or "").strip()

        if not raw_text:
            await say(
                text="Usage: `/ask your question here`\nNeed ideas? Type `/help` to see examples."
            )
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
    # Handler: file_shared — auto-brief + Block Kit action buttons
    # ------------------------------------------------------------------

    @app.event("file_shared")
    async def handle_file_shared(event: dict[str, Any], client: Any, say: Any) -> None:
        file_id: str = event.get("file_id", "")
        channel: str = event.get("channel_id", "")

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
        mimetype = (file_obj.get("mimetype") or "")

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
        except Exception as exc:
            log.debug("file_shared: could not pre-download bytes for %s: %s", file_id, exc)

        # Show a placeholder while we run the auto-brief
        try:
            placeholder = await client.chat_postMessage(
                channel=channel, text=f"📎 *{filename}* — reading…"
            )
            placeholder_ts = (placeholder or {}).get("ts")
        except Exception:
            placeholder_ts = None

        # Auto-brief: 1-sentence description of the file (graceful fallback on error)
        description = await _auto_brief_file(file_obj, SLACK_BOT_TOKEN)

        blocks = _build_file_blocks(filename, description, mimetype, file_id)
        fallback_text = (
            f"📎 *{filename}*"
            + (f"\n_{description}_" if description else "")
            + "\n\nWhat would you like to do?"
        )

        try:
            if placeholder_ts:
                await client.chat_update(
                    channel=channel, ts=placeholder_ts, text=fallback_text, blocks=blocks
                )
            else:
                await client.chat_postMessage(channel=channel, text=fallback_text, blocks=blocks)
        except Exception as exc:
            # Block Kit may fail if interactivity is not yet enabled in the manifest.
            # Fall back to plain text suggestion.
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

    async def _dispatch_file_action(
        action_id: str, ack: Any, body: dict[str, Any], client: Any, say: Any
    ) -> None:
        await ack()

        user_id: str = (body.get("user") or {}).get("id", "unknown")
        actions: list[dict] = body.get("actions", [{}])
        file_id: str = (actions[0] if actions else {}).get("value", "")
        channel: str = (
            (body.get("channel") or {}).get("id", "")
            or (body.get("container") or {}).get("channel_id", "")
        )

        if not file_id or not channel:
            await say(text="⚠️ Couldn't identify the file. Please upload it again.")
            return

        file_obj = _file_registry.get(file_id)
        if not file_obj:
            await say(
                text="⚠️ I've lost track of that file — try uploading it again and I'll be ready."
            )
            return

        # Registry now stores {"file_obj": ..., "file_bytes": ...}
        if isinstance(file_obj, dict) and "file_obj" in file_obj:
            file_bytes = file_obj.get("file_bytes")
            file_obj = file_obj["file_obj"]
        else:
            file_bytes = None

        prompt_text = _FILE_ACTION_PROMPTS.get(action_id, "Please analyze this file.")

        # Handle files referenced from /ai-files directly (no Slack download needed)
        if file_obj.get("ai_files_path"):
            file_content = await file_skills.read_local_file(file_obj["ai_files_path"])
            prompt = f"{prompt_text}\n\n--- File: {file_obj['name']} ---\n{file_content}\n--- End ---"
        else:
            prompt = await _process_slack_files([file_obj], SLACK_BOT_TOKEN, prompt_text)

        use_simple = _get_user_simple(user_id)

        # Smart model routing based on file type + action
        filename_for_routing = file_obj.get("name", "")
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
        user_id: str = (body.get("user_id") or "unknown")
        channel: str = body.get("channel_id", "")
        text: str = (body.get("text") or "").strip()

        if not text:
            # List mode — show all files in /ai-files
            listing = await file_skills.list_local_files("/ai-files")

            if "empty" in listing.lower() or "not found" in listing.lower():
                await client.chat_postEphemeral(
                    channel=channel,
                    user=user_id,
                    text=(
                        "📂 No files yet! Drop a Word doc into your OpenClaw folder "
                        "and it'll appear here."
                    ),
                )
                return

            lines = listing.splitlines()
            file_blocks: list[dict] = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "📁 *Files in OpenClaw:*"}},
            ]
            for line in lines[1:21]:  # skip header line, cap at 20
                stripped = line.strip()
                if stripped:
                    file_blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"`{stripped}`"},
                    })
            file_blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "Tip: `/files budget.xlsx` to select a file"}],
            })
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
    for _action_id in list(_FILE_ACTION_PROMPTS.keys()):
        def _make_handler(aid: str):
            async def handler(ack: Any, body: dict[str, Any], client: Any, say: Any) -> None:
                await _dispatch_file_action(aid, ack, body, client, say)
            handler.__name__ = f"handle_{aid}"
            return handler

        app.action(_action_id)(_make_handler(_action_id))

    return app


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def create_slack_handler():  # type: ignore[return]
    """Return an AsyncSocketModeHandler configured for this app, or None."""
    global _BOT_USER_ID

    app = create_slack_app()
    if app is None:
        return None

    try:
        from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
    except ImportError:
        log.error("AsyncSocketModeHandler not available — ensure slack_bolt[async]>=1.18.0 is installed")
        return None

    # Resolve the bot's own user ID so thread-history can distinguish bot messages
    try:
        auth = await app.client.auth_test()
        _BOT_USER_ID = auth.get("user_id", "")
        log.info("Slack bot user ID: %s", _BOT_USER_ID)
    except Exception as exc:
        log.warning("Could not resolve Slack bot user ID: %s", exc)

    handler = AsyncSocketModeHandler(app, SLACK_APP_TOKEN)
    log.info("Slack Socket Mode handler created")
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
