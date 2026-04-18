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

log = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")

# ---------------------------------------------------------------------------
# Response cleanup for Slack
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
) -> str:
    """Route a prompt through OpenClaw's agent ask pipeline."""
    from dashboard.api_handlers import _execute_agent_ask

    try:
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
) -> None:
    """Ask OpenClaw, update the thinking placeholder, and register the reply for feedback."""
    try:
        answer = await _ask(prompt, user_id, model_pref=model_pref, history=history)
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
        if not prompt_raw:
            await say(
                text="Hey! I'm OpenClaw. Ask me anything. Tip: add `--model gemini` (or openai/anthropic/copilot) to pick a model.",
                thread_ts=thread_ts,
            )
            return

        prompt, model_pref = _parse_model_flag(prompt_raw)

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

        if not raw_text:
            return

        prompt, model_pref = _parse_model_flag(raw_text)

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
                text="Usage: `/ask your question here`\nTip: add `--model gemini` (or openai/anthropic/copilot) to pick a model."
            )
            return

        prompt, model_pref = _parse_model_flag(raw_text)

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
