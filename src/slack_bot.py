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

import logging
import os
import re
from typing import Any

import aiohttp

from constants import ATTACHMENT_TEXT_MAX_CHARS
from http_session import SessionManager
from llm import analyze_image as llm_analyze_image

log = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")

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
    "• Add `--simple` to any message for plain, easy-to-read responses\n"
    "• Reply in a thread to keep context from earlier messages\n\n"
    "_Example: Upload Budget2025.xlsx and type: \"summarize the totals for me --simple\"_"
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
# Core ask helper
# ---------------------------------------------------------------------------

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
    # Handler: file_shared — file uploaded without accompanying text
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

        filename = file_obj.get("name", "file")
        mimetype = (file_obj.get("mimetype") or "")
        suggestion = _suggest_actions_for_file(filename, mimetype)
        try:
            await client.chat_postMessage(channel=channel, text=suggestion)
        except Exception as exc:
            log.warning("file_shared: failed to post suggestion for %s: %s", filename, exc)

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
