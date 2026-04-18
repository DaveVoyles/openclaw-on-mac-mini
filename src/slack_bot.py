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
     im:history, im:read, im:write
  4. Subscribe to events: app_mention, message.im
  5. Install app to workspace
  6. Copy Bot User OAuth Token (xoxb-...) to SLACK_BOT_TOKEN
  7. Copy App-Level Token (xapp-...) to SLACK_APP_TOKEN

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
# Feature flag check
# ---------------------------------------------------------------------------

SLACK_ENABLED = os.getenv("SLACK_ENABLED", "false").lower() == "true"
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")


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

async def _ask(prompt: str, user_id: str) -> str:
    """Route a prompt through OpenClaw's agent ask pipeline."""
    from dashboard.api_handlers import _execute_agent_ask

    try:
        payload = await _execute_agent_ask(
            prompt=prompt,
            model_pref="auto",
            history=[],
            user_name=f"slack:{user_id}",
        )
        return str(payload.get("response") or payload.get("text") or "(no response)").strip()
    except Exception as exc:  # broad: intentional
        log.error("_execute_agent_ask failed for slack user %s: %s", user_id, exc)
        raise


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
        thread_ts: str = event.get("thread_ts") or event.get("ts", "")
        raw_text: str = event.get("text", "")

        # Strip the @mention token(s) to get the clean prompt
        prompt = _MENTION_RE.sub("", raw_text).strip()
        if not prompt:
            await say(
                text="Hey! I'm OpenClaw. Ask me anything.",
                thread_ts=thread_ts,
            )
            return

        # Acknowledge immediately with a thinking placeholder
        thinking_resp = await say(text="⏳ Thinking…", thread_ts=thread_ts)
        thinking_ts = (thinking_resp or {}).get("ts")

        try:
            answer = await _ask(prompt, user_id)
            text = answer or "(no response)"
        except Exception as exc:
            text = f"❌ Sorry, something went wrong: {exc}"

        # Update the placeholder message, or post a new one if update fails
        if thinking_ts:
            try:
                await client.chat_update(
                    channel=channel,
                    ts=thinking_ts,
                    text=text,
                )
                return
            except Exception:
                pass

        await say(text=text, thread_ts=thread_ts)

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
        prompt: str = (event.get("text") or "").strip()

        if not prompt:
            return

        # Post thinking placeholder
        thinking_resp = await say(text="⏳ Thinking…")
        thinking_ts = (thinking_resp or {}).get("ts")

        try:
            answer = await _ask(prompt, user_id)
            text = answer or "(no response)"
        except Exception as exc:
            text = f"❌ Sorry, something went wrong: {exc}"

        if thinking_ts:
            try:
                await client.chat_update(
                    channel=channel,
                    ts=thinking_ts,
                    text=text,
                )
                return
            except Exception:
                pass

        await say(text=text)

    return app


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def create_slack_handler():  # type: ignore[return]
    """Return an AsyncSocketModeHandler configured for this app, or None."""
    app = create_slack_app()
    if app is None:
        return None

    try:
        from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
    except ImportError:
        log.error("AsyncSocketModeHandler not available — ensure slack_bolt[async]>=1.18.0 is installed")
        return None

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
