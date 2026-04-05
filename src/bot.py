"""
OpenClaw Discord Bot - Phase 6: Remote Access & Monitoring
Autonomous AI agent for home automation and system management.

This is the core bot file — init, /ask command, and entry point.
Slash commands live in discord_commands.py, background tasks in
discord_background.py, and the web/health server in discord_web.py.
"""

import asyncio
import datetime
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import discord
import yaml
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from agent_loop import scan_interrupted as scan_interrupted_plans
from agentmail import send_agent_mail
from approvals import is_emergency_stopped
from config import cfg
from constants import (
    ATTACHMENT_TEXT_MAX_CHARS,
    EMBED_DESC_LIMIT,
    EMBED_SPLIT_LIMIT,
    MAX_FILE_SIZE,
)
from llm import SUPPORTED_IMAGE_MIMES, get_rate_info
from llm import analyze_image as llm_analyze_image
from llm import chat as llm_chat
from llm import chat_stream as llm_chat_stream
from llm import is_configured as llm_is_configured
from memory import get_model_preference
from memory import store as conversation_store
from permissions import (  # noqa: F401 — re-exported for backward compat
    ALLOWED_USER_IDS,
    is_allowed,
    is_service_allowed,
    require_auth,
)
from qmd import remember_fact
from runtime_state import set_bot
from scheduler import scheduler
from skills import SKILLS
from trace_context import setup_trace_logging

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
AUDIT_DIR = Path(os.getenv("AUDIT_DIR", "/audit"))
LOG_DIR = Path(os.getenv("LOG_DIR", "/logs"))
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))

# ---------------------------------------------------------------------------
# Channel role architecture — prevents context bleed between workflows
# ---------------------------------------------------------------------------

_CHANNEL_ROLES: dict[int, str] = {}
_CHANNEL_PROMPTS: dict[str, str] = {}


def _load_channel_config() -> None:
    """Load channel roles from config.yaml and map them to env-provided IDs."""
    global _CHANNEL_ROLES, _CHANNEL_PROMPTS
    config_file = CONFIG_DIR / "config.yaml"
    if config_file.exists():
        try:
            with open(config_file) as f:
                cfg_yaml = yaml.safe_load(f) or {}
            roles = cfg_yaml.get("channels", {}).get("roles", {})
            for role_name, role_cfg in roles.items():
                prompt = role_cfg.get("prompt_override", "")
                if prompt:
                    _CHANNEL_PROMPTS[role_name] = prompt
        except Exception as e:
            log.warning("Failed to load channel config: %s", e)

    for role in ("research", "analytics", "bookmarks", "real_estate"):
        raw = os.getenv(f"DISCORD_CHANNEL_{role.upper()}_ID", "0")
        try:
            cid = int(raw)
            if cid:
                _CHANNEL_ROLES[cid] = role
        except ValueError:
            pass

    if _CHANNEL_ROLES:
        role_summary = {v: k for k, v in _CHANNEL_ROLES.items()}
        log.info("Channel roles loaded: %s", role_summary)
    else:
        log.info("No channel role IDs configured (DISCORD_CHANNEL_<ROLE>_ID not set)")


VERSION = cfg.version

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "openclaw.log"),
    ],
)
log = logging.getLogger("openclaw")
setup_trace_logging()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def truncate_for_embed(text: str, limit: int = EMBED_DESC_LIMIT) -> str:
    """Truncate *text* to fit in a Discord embed description."""
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


# ---------------------------------------------------------------------------
# Audit logger — uses shared audit module; buffer lives in audit.py
# ---------------------------------------------------------------------------

AUDIT_DIR.mkdir(parents=True, exist_ok=True)

from audit import _audit_buffer, audit_log  # noqa: E402

# ---------------------------------------------------------------------------
# Shared HTTP session (reused for attachment downloads)
# ---------------------------------------------------------------------------
from http_session import SessionManager as _SessionManager
from constants import HTTP_TIMEOUT_DEFAULT

_bot_sessions = _SessionManager(timeout=HTTP_TIMEOUT_DEFAULT, name="bot")


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True


class OpenClawBot(commands.Bot):
    """Discord bot with slash commands, cog extensions, and app-command tree."""

    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.start_time = time.monotonic()
        self._health_runner = None

    async def setup_hook(self) -> None:
        """Load cogs dynamically, register commands, and sync on startup."""
        cogs_dir = Path(__file__).parent / "cogs"
        loaded: list[str] = []
        for cog_file in sorted(cogs_dir.glob("*_cog.py")):
            if cog_file.name.startswith("_"):
                continue
            module = f"cogs.{cog_file.stem}"
            try:
                await self.load_extension(module)
                loaded.append(module)
                log.info("Loaded cog: %s", module)
            except Exception as e:
                log.error("Failed to load cog %s: %s", module, e)
        log.info("Loaded %d cogs: %s", len(loaded), ", ".join(loaded))

        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced commands to guild %s", DISCORD_GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Synced commands globally")

        # Start health-check HTTP server
        from discord_web import start_health_server
        self._health_runner = await start_health_server(self)

    async def on_ready(self) -> None:
        """Initialize bot on connection to Discord.
        
        Sets up:
        - Channel configurations
        - Skill scheduler with daily maintenance
        - Background health monitors (container status, log scanning)
        - Proactive monitoring loops (every 2 hours)
        - GitHub workflow tracking for failed jobs
        """
        log.info("OpenClaw online as %s (ID %s)", self.user, self.user.id)
        audit_log(None, "bot_ready", f"Logged in as {self.user}")
        set_bot(self)

        _load_channel_config()

        scheduler.register_skills(SKILLS)
        scheduler.start()
        log.info("Scheduler started with %d registered skills", len(SKILLS))

        # Register recurring cron jobs (idempotent)
        existing_actions = {t.action for t in scheduler.list_tasks()}
        if "run_maintenance" not in existing_actions:
            scheduler.create(
                action="run_maintenance", args={}, hour=4, minute=0,
                created_by="system", notify_channel_id=ALERT_CHANNEL_ID, alert_only=False,
            )
            log.info("Registered 4:00 AM maintenance cron job")
        if "index_vault_to_qmd" not in existing_actions:
            scheduler.create(
                action="index_vault_to_qmd", args={}, hour=3, minute=50,
                created_by="system", notify_channel_id=0, alert_only=False,
            )
            log.info("Registered 3:50 AM vault indexer cron job")

        # Wire scheduler -> Discord notification callback
        async def _scheduler_notify(task_id: str, action: str, result: str, is_alert: bool) -> None:
            task = scheduler.get(task_id)
            if task is None:
                return
            channel = self.get_channel(task.notify_channel_id)
            if channel is None:
                return
            color = discord.Color.red() if is_alert else discord.Color.green()
            icon = "🚨" if is_alert else "✅"
            chunks = _split_response(result) if result else ["(no output)"]
            try:
                for idx, chunk in enumerate(chunks):
                    embed = discord.Embed(
                        title=f"{icon} Watch Alert: `{action}`" if idx == 0 else None,
                        description=chunk,
                        color=color,
                    )
                    if idx == len(chunks) - 1:
                        embed.set_footer(text=f"Task {task_id} • {action}")
                    await channel.send(embed=embed)
            except Exception as e:
                log.error("Failed to post scheduler result for %s: %s", task_id, e)

        scheduler.notify_callback = _scheduler_notify

        # Start background tasks (cleanup, audit writer, proactive loops)
        from discord_background import start_background_tasks
        start_background_tasks(self)

        # Set bot presence/activity
        container_count = len(self.guilds)
        try:
            from skills import list_containers
            result = await list_containers()
            container_count = len([ln for ln in result.split("\n") if ln.strip() and not ln.startswith("NAMES")])
        except (ImportError, RuntimeError, ConnectionError):
            # list_containers may fail if Docker socket unavailable; use guild count as fallback
            pass
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{container_count} containers | /ask",
            )
        )

        # Scan for interrupted plans from previous runs
        if ALERT_CHANNEL_ID:
            interrupted = scan_interrupted_plans()
            if interrupted:
                channel = self.get_channel(ALERT_CHANNEL_ID)
                if channel:
                    names = ", ".join(f"`{p.plan_id}`" for p in interrupted[:5])
                    try:
                        await channel.send(
                            f"🔄 Found **{len(interrupted)}** interrupted plan(s) from a previous session: {names}\n"
                            f"Use `/resume <plan_id>` to continue, or `/plans` to review."
                        )
                    except Exception as e:
                        log.warning("Failed to post interrupted plan notice: %s", e)
                log.info("Found %d interrupted plan(s) on startup", len(interrupted))

    async def close(self) -> None:
        """Graceful shutdown: flush audit log, close sessions, stop health server."""
        if _audit_buffer:
            entries = list(_audit_buffer)
            _audit_buffer.clear()
            today = datetime.date.today().isoformat()
            audit_file = AUDIT_DIR / f"{today}.jsonl"
            try:
                with open(audit_file, "a") as f:
                    for e in entries:
                        f.write(json.dumps(e) + "\n")
            except Exception as exc:
                log.warning("Failed to flush audit buffer on shutdown: %s", exc)

        _close_fns = [
            ("llm", lambda: __import__("llm").close_sessions()),
            ("agentmail", lambda: __import__("agentmail").close_session()),
            ("nas", lambda: __import__("nas").close_session()),
            ("http_sessions", lambda: __import__("http_session").close_all()),
        ]
        for name, fn in _close_fns:
            try:
                await fn()
            except Exception as exc:
                log.debug("close %s: %s", name, exc)
        if self._health_runner:
            await self._health_runner.cleanup()
        await super().close()


bot = OpenClawBot()

# ---------------------------------------------------------------------------
# Register standalone slash commands (everything except /ask)
# ---------------------------------------------------------------------------

from discord_commands import register_commands  # noqa: E402

register_commands(bot)

# ---------------------------------------------------------------------------
# /ask command and helpers (core — stays in bot.py)
# ---------------------------------------------------------------------------

_EMBED_LIMIT = EMBED_SPLIT_LIMIT
_FILE_THRESHOLD = 8000

_IMAGE_LINK_RE = re.compile(
    r"!?\[(?:[^\]]*(?:photo|image|📸|🖼️|property|listing)[^\]]*)\]\((https?://[^)]+)\)",
    re.IGNORECASE,
)
_BARE_IMAGE_RE = re.compile(
    r"(https?://\S+\.(?:jpg|jpeg|png|webp|gif)(?:\?\S*)?)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Message formatting (extracted to bot_formatting.py)
# ---------------------------------------------------------------------------

from bot_formatting import (
    extract_file_attachment as _extract_file_attachment,
    extract_image_url as _extract_image_url,
    format_markdown_for_discord as _format_markdown_for_discord,
    format_tables_for_discord as _format_tables_for_discord,
    split_response as _split_response,
)

_STREAM_EDIT_INTERVAL = 3.0
    """Return the first image URL found in the response text, or None."""
    m = _IMAGE_LINK_RE.search(text)
    if m:
        return m.group(1)
    m = _BARE_IMAGE_RE.search(text)
    if m:
        return m.group(1)
    return None


def _format_markdown_for_discord(text: str) -> str:
    """Convert markdown elements that Discord embeds don't render natively."""
    lines = text.split("\n")
    result: list[str] = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue
        if in_code_block:
            result.append(line)
            continue

        header_match = re.match(r'^(#{1,3})\s+(.+)$', line)
        if header_match:
            level = len(header_match.group(1))
            heading_text = header_match.group(2).strip()
            if level == 1:
                result.append(f"__**{heading_text}**__")
            else:
                result.append(f"**{heading_text}**")
            continue

        result.append(line)

    return "\n".join(result)


    def _flush_table(tlines: list[str]) -> None:
        rows: list[list[str]] = []
        separator_indices: list[int] = []
        for i, tl in enumerate(tlines):
            cells = [c.strip() for c in tl.strip().strip("|").split("|")]
            cleaned = []
            for c in cells:
                c = c.strip("*")
                c = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', c)
                cleaned.append(c)
            cells = cleaned
            stripped = tl.strip()
            is_sep = stripped.startswith("|") and all(c in "|-: " for c in stripped.replace("|", ""))
            if is_sep:
                separator_indices.append(i)
            else:
                rows.append(cells)

        if not rows:
            result.extend(tlines)
            return

        num_cols = max(len(r) for r in rows)
        col_widths = [0] * num_cols
        for row in rows:
            for j, cell in enumerate(row):
                if j < num_cols:
                    col_widths[j] = max(col_widths[j], len(cell))

        result.append("```ansi")

        row_idx = 0
        for i, tl in enumerate(tlines):
            if i in separator_indices:
                sep = "┼".join("─" * (w + 2) for w in col_widths)
                result.append(f"┼{sep}┼")
            else:
                if row_idx < len(rows):
                    cells = rows[row_idx]
                    padded = []
                    for j in range(num_cols):
                        cell = cells[j] if j < len(cells) else ""
                        padded.append(f" {cell:<{col_widths[j]}} ")
                    line_text = "│" + "│".join(padded) + "│"
                    if row_idx == 0:
                        line_text = f"\u001b[1;37m{line_text}\u001b[0m"
                    result.append(line_text)
                    row_idx += 1

        result.append("```")

    for line in lines:
        stripped = line.strip()
        is_table_row = stripped.startswith("|") and stripped.endswith("|")
        is_separator = is_table_row and all(c in "|-: " for c in stripped.replace("|", ""))

        if is_table_row or is_separator:
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(line)
        else:
            if in_table:
                _flush_table(table_lines)
                in_table = False
                table_lines = []
            result.append(line)

    if in_table and table_lines:
        _flush_table(table_lines)

    return "\n".join(result)


_STREAM_EDIT_INTERVAL = 3.0

_CODE_BLOCK_RE = re.compile(
    r"```(\w+)?\n([\s\S]+?)```",
)


# ---------------------------------------------------------------------------
# Reaction-based action buttons on responses
# ---------------------------------------------------------------------------

class ResponseActions(discord.ui.View):
    """Buttons attached to /ask responses: Save, Regenerate, Email."""

    def __init__(
        self,
        *,
        response_text: str,
        question: str,
        user_id: int,
        channel_id: int,
        timeout: float = 300,
        follow_ups: list[str] | None = None,
        bot=None,
    ):
        super().__init__(timeout=timeout)
        self._response_text = response_text
        self._question = question
        self._user_id = user_id
        self._channel_id = channel_id
        self._bot = bot
        # Add follow-up buttons dynamically on row 1
        for i, fq in enumerate(follow_ups or []):
            btn = discord.ui.Button(
                label=fq[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"followup_{i}",
                row=1,
            )
            btn.callback = self._make_followup_callback(fq)
            self.add_item(btn)
        # Go Deeper button on row 1
        deeper_btn = discord.ui.Button(
            label="🔁 Go Deeper",
            style=discord.ButtonStyle.secondary,
            custom_id="go_deeper",
            row=1,
        )
        deeper_btn.callback = self._go_deeper_callback
        self.add_item(deeper_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("Only the original requester can use these buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="📌 Save", style=discord.ButtonStyle.secondary)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            fact = self._response_text[:500]
            result = await remember_fact(
                f"Saved from /ask: {self._question[:100]}", fact
            )
            await interaction.followup.send(f"📌 Saved to memory.\n{result}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Save failed: {e}", ephemeral=True)

    @discord.ui.button(label="🔄 Regenerate", style=discord.ButtonStyle.secondary)
    async def regen_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        conv = conversation_store.get(
            user_id=self._user_id,
            channel_id=self._channel_id,
            user_name=str(interaction.user.display_name),
        )
        if len(conv.history) >= 2:
            conv.history = conv.history[:-2]
        try:
            response_text, updated_history, model_used = await llm_chat(
                user_message=self._question,
                history=conv.history,
                user_name=str(interaction.user.display_name),
            )
            conv.update_from_llm(updated_history)
            embed = discord.Embed(description=response_text[:_EMBED_LIMIT], color=discord.Color.purple())
            embed.set_footer(text=f"🔄 Regenerated | via {model_used}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Regeneration failed: {e}")

    @discord.ui.button(label="📧 Email", style=discord.ButtonStyle.secondary)
    async def email_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            result = await send_agent_mail(
                subject=f"OpenClaw: {self._question[:80]}",
                body=self._response_text,
            )
            await interaction.followup.send(f"📧 Emailed!\n{result}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Email failed: {e}", ephemeral=True)

    @discord.ui.button(label="👍", style=discord.ButtonStyle.success)
    async def thumbs_up_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._record_feedback(interaction, "positive")

    @discord.ui.button(label="👎", style=discord.ButtonStyle.danger)
    async def thumbs_down_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._record_feedback(interaction, "negative")

    def _make_followup_callback(self, follow_up_question: str):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()
            conv = conversation_store.get(
                user_id=self._user_id,
                channel_id=self._channel_id,
                user_name=str(interaction.user.display_name),
            )
            try:
                response_text, updated_history, model_used = await llm_chat(
                    user_message=follow_up_question,
                    history=conv.history,
                    user_name=str(interaction.user.display_name),
                )
                conv.update_from_llm(updated_history)
                embed = discord.Embed(
                    description=response_text[:_EMBED_LIMIT],
                    color=discord.Color.purple(),
                )
                embed.set_footer(text=f"💬 Follow-up | via {model_used}")
                new_follow_ups = await _generate_follow_ups(follow_up_question, response_text)
                view = ResponseActions(
                    response_text=response_text,
                    question=follow_up_question,
                    user_id=self._user_id,
                    channel_id=self._channel_id,
                    follow_ups=new_follow_ups,
                    bot=self._bot,
                )
                await interaction.followup.send(embed=embed, view=view)
            except Exception as e:
                await interaction.followup.send(f"❌ Follow-up failed: {e}")
        return callback

    async def _go_deeper_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        deeper_q = f"Give a much more detailed and thorough explanation of: {self._question}"
        conv = conversation_store.get(
            user_id=self._user_id,
            channel_id=self._channel_id,
            user_name=str(interaction.user.display_name),
        )
        try:
            response_text, updated_history, model_used = await llm_chat(
                user_message=deeper_q,
                history=conv.history,
                user_name=str(interaction.user.display_name),
            )
            conv.update_from_llm(updated_history)
            embed = discord.Embed(
                description=response_text[:_EMBED_LIMIT],
                color=discord.Color.purple(),
            )
            embed.set_footer(text=f"🔁 Deep dive | via {model_used}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed: {e}")

    async def _record_feedback(self, interaction: discord.Interaction, rating: str) -> None:
        import json
        from pathlib import Path
        try:
            feedback_file = Path("/memory/feedback.jsonl")
            entry = {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "user_id": interaction.user.id,
                "question": self._question[:200],
                "rating": rating,
            }
            feedback_file.parent.mkdir(parents=True, exist_ok=True)
            with open(feedback_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
            emoji = "👍" if rating == "positive" else "👎"
            await interaction.response.send_message(
                f"{emoji} Feedback recorded — thanks!", ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)


async def _generate_follow_ups(question: str, response: str) -> list[str]:
    """Generate 2 short follow-up questions based on the Q&A exchange."""
    try:
        from llm.chat import chat
        prompt = (
            f"Based on this Q&A exchange, suggest exactly 2 short follow-up questions the user might want to ask next.\n"
            f"Q: {question[:300]}\n"
            f"A: {response[:500]}\n\n"
            f"Return ONLY the 2 questions, one per line, no numbering, no extra text. Keep each under 60 characters."
        )
        text, _, _ = await chat(prompt, model_preference="gemini")
        lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
        return lines[:2]
    except (ImportError, RuntimeError, TimeoutError):
        # LLM may be unavailable; return empty list to skip follow-ups
        return []


from bot_attachments import (
    handle_image_attachment as _handle_image_attachment,
    handle_doc_attachment as _handle_doc_attachment,
)

@bot.tree.command(name="ask", description="Ask OpenClaw anything (AI-powered with function calling)")
@app_commands.describe(
    question="Your question or request",
    attachment="Optional image or document to include in your question",
    model="LLM routing: auto (smart), local (Gemma), gemini (cloud), openai (GPT-4o), or anthropic (Claude)",
)
@app_commands.choices(model=[
    app_commands.Choice(name="🔄 Auto (Copilot → Gemini)", value="auto"),
    app_commands.Choice(name="🏠 Local (Gemma/Ollama)", value="local"),
    app_commands.Choice(name="☁️ Gemini (cloud)", value="gemini"),
    app_commands.Choice(name="🟢 OpenAI (GPT-4o)", value="openai"),
    app_commands.Choice(name="🟣 Anthropic (Claude)", value="anthropic"),
])
async def ask_cmd(
    interaction: discord.Interaction,
    question: str,
    attachment: discord.Attachment | None = None,
    model: app_commands.Choice[str] | None = None,
) -> None:
    """Main user query handler — routes to Gemini (tool-capable) or Ollama (conversational)."""

    if is_emergency_stopped():
        await interaction.response.send_message(
            "🛑 **Emergency stop is active.** `/ask` is disabled. Use `/estop resume` to resume.",
            ephemeral=True,
        )
        return

    if not llm_is_configured():
        await interaction.response.send_message(
            "⚠️ LLM not configured. Set `GOOGLE_API_KEY` in your `.env` file.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    _ask_start = time.monotonic()

    # Set up request tracing
    from trace_context import TraceContext, _current_trace
    _trace = TraceContext(command="ask", user_id=interaction.user.id,
                          channel_id=interaction.channel_id)
    _trace_token = _current_trace.set(_trace)
    log.info("ask_cmd start question=%.80s", question)

    _progress_lines: list[str] = []
    _progress_start = time.monotonic()

    async def _think(status: str) -> None:
        elapsed = time.monotonic() - _progress_start
        _progress_lines.append(f"💭 {status} ({elapsed:.0f}s)")
        progress = "\n".join(_progress_lines) + "\n\n⏳ *thinking…*"
        try:
            embed = discord.Embed(description=progress, color=discord.Color.dark_grey())
            embed.set_author(
                name=f"Replying to: {question[:100]}",
                icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
            )
            await interaction.edit_original_response(content=None, embed=embed)
        except Exception as exc:
            log.debug("Progress edit failed: %s", exc)

    async def _on_tool_call(tool_name: str, round_num: int, *, args: dict | None = None, result_preview: str | None = None) -> None:
        elapsed = time.monotonic() - _progress_start
        if result_preview is not None:
            _progress_lines.append(f"✅ `{tool_name}` → {result_preview[:80]}")
        elif args is not None:
            args_str = ", ".join(f"{k}={v!r}" for k, v in args.items()) if args else ""
            _progress_lines.append(f"🔄 Using `{tool_name}({args_str})`… ({elapsed:.0f}s)")
        else:
            _progress_lines.append(f"🔄 Using `{tool_name}`… ({elapsed:.0f}s)")
        progress = "\n".join(_progress_lines) + "\n\n⏳ *working…*"
        try:
            embed = discord.Embed(
                description=progress,
                color=discord.Color.dark_grey(),
            )
            embed.set_author(
                name=f"Replying to: {question[:100]}",
                icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
            )
            await interaction.edit_original_response(content=None, embed=embed)
        except Exception as exc:
            log.debug("Failed to update tool progress: %s", exc)

    # If an attachment was provided, route through the appropriate analyzer
    if attachment:
        mime = (attachment.content_type or "").split(";")[0].strip()
        if mime in SUPPORTED_IMAGE_MIMES and attachment.size <= MAX_FILE_SIZE:
            question = await _handle_image_attachment(attachment, question)
        elif attachment.size > MAX_FILE_SIZE:
            log.info("ask_cmd: attachment too large (%d bytes), skipping", attachment.size)
        else:
            question = await _handle_doc_attachment(attachment, question)

    # Get or create conversation context
    conv = conversation_store.get(
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
        user_name=str(interaction.user.display_name),
    )

    # Research thread context injection
    if isinstance(interaction.channel, discord.Thread) and not conv.history:
        thread_name = interaction.channel.name or ""
        if thread_name.startswith("Research:"):
            try:
                report_text = ""
                async for msg in interaction.channel.history(limit=20, oldest_first=True):
                    if msg.embeds:
                        for embed in msg.embeds:
                            if embed.description:
                                report_text += embed.description + "\n"
                if report_text:
                    conv.history.append({
                        "role": "model",
                        "parts": [f"[Previous Research Report]\n{report_text[:8000]}"],
                    })
                    log.info("Injected research context (%d chars) for thread: %s",
                             len(report_text), thread_name)
            except Exception as e:
                log.debug("Research context injection failed: %s", e)

    # Thread continuation suggestion
    thread_hint = ""
    if not conv.history:
        try:
            import vector_store
            hits = await vector_store.search(
                vector_store.CONVERSATIONS_COLLECTION, question, top_k=1, threshold=0.75
            )
            if hits:
                meta = hits[0].get("metadata", {})
                thread_name = meta.get("thread_name", "")
                sim = hits[0].get("similarity", 0)
                if thread_name and sim >= 0.75:
                    thread_hint = (
                        f"\n\n> 💡 *This looks related to your thread "
                        f"**{thread_name}**. Use `/resume {thread_name}` to continue it.*"
                    )
        except Exception as exc:
            log.debug("Thread hint search failed: %s", exc)

    # Channel role injection
    if not conv.history:
        channel_role = _CHANNEL_ROLES.get(interaction.channel_id)
        if channel_role:
            role_prompt = _CHANNEL_PROMPTS.get(channel_role, "")
            if role_prompt:
                conv.history.append({
                    "role": "model",
                    "parts": [f"📌 *{channel_role.capitalize()} mode active.* {role_prompt}"],
                })
                log.debug("Injected %s channel role prompt for channel %d", channel_role, interaction.channel_id)

    response_text = ""
    model_used = "unknown"
    model_pref = model.value if model else get_model_preference(interaction.user.id)

    # Guardrail: if user picks "local" but query clearly needs tools, auto-upgrade
    from llm import _needs_tools as llm_needs_tools
    if model_pref == "local" and llm_needs_tools(question):
        model_pref = "gemini"
        guardrail_note = "\n\n> ⚡ *Auto-upgraded to Gemini (your query requires tool access)*"
    else:
        guardrail_note = ""

    try:
        # Contextual recall
        await _think("Recalling relevant memories…")
        try:
            import vector_store
            context_hits = await vector_store.recall(question, top_k=3)
            if context_hits:
                conv.history.append({
                    "role": "model",
                    "parts": [f"[Relevant context from memory]\n{context_hits}"],
                })
        except Exception as e:
            log.debug("Contextual recall skipped: %s", e)

        # Inject learned rules (Phase 14A)
        await _think("Checking learned rules…")
        try:
            from rules_engine import get_relevant_rules
            rules = await get_relevant_rules(question, top_k=3)
            if rules:
                rules_block = "\n".join(f"• {r}" for r in rules)
                conv.history.append({
                    "role": "model",
                    "parts": [f"[Learned rules — follow these]\n{rules_block}"],
                })
        except Exception as e:
            log.debug("Rules injection skipped: %s", e)

        # Inject user profile context (Phase 14C)
        try:
            from user_profile import get_profile_prompt
            profile_ctx = get_profile_prompt()
            if profile_ctx:
                conv.history.append({
                    "role": "model",
                    "parts": [profile_ctx],
                })
        except Exception as e:
            log.debug("Profile injection skipped: %s", e)

        # Streaming response with progressive Discord edits
        _routing_notes: list[str] = []
        _model_labels = {"auto": "smart routing", "local": "Gemma (local)", "gemini": "Gemini", "openai": "GPT-4o", "anthropic": "Claude"}
        await _think(f"Routing to {_model_labels.get(model_pref, model_pref)}…")
        last_edit = 0.0
        display_question = question if len(question) < 200 else question[:197] + "..."

        _DISCORD_TIMEOUT = 840

        try:
          async for chunk_text, is_final, meta in llm_chat_stream(
            user_message=question,
            history=conv.history,
            user_name=str(interaction.user.display_name),
            on_tool_call=_on_tool_call,
            model_preference=model_pref,
        ):
            model_used = meta.get("model_used", "unknown")

            if is_final:
                response_text = chunk_text
                _routing_notes.extend(meta.get("routing_notes", []))
                if "updated_history" in meta:
                    conv.update_from_llm(meta["updated_history"])
                    conversation_store.auto_save_thread(
                        interaction.user.id, interaction.channel_id, str(interaction.user.display_name)
                    )
                log.info("ask_cmd LLM done model=%s chars=%d", model_used, len(response_text))
                break

            now = time.monotonic()
            if now - last_edit >= _STREAM_EDIT_INTERVAL and chunk_text:
                try:
                    preview = chunk_text[:_EMBED_LIMIT - 50] + "\n\n*⏳ streaming…*"
                    embed = discord.Embed(description=preview, color=discord.Color.purple())
                    embed.set_author(
                        name=f"Replying to: {display_question}",
                        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                    )
                    await interaction.edit_original_response(content=None, embed=embed)
                    last_edit = now
                except Exception as exc:
                    log.debug("Stream edit failed: %s", exc)

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - _progress_start
            log.warning("LLM response timed out after %.0fs for: %.80s", elapsed, question)
            progress_so_far = "\n".join(_progress_lines) if _progress_lines else "No progress recorded"
            response_text = (
                f"⏰ **Timed out** after {elapsed:.0f}s.\n\n"
                f"**Steps completed:**\n{progress_so_far}\n\n"
                f"Try a simpler query, or use `/ask model:gemini` to retry."
            )
            model_used = "timeout"

    except Exception as e:
        log.error("LLM error: %s", e)
        safe_question = discord.utils.escape_markdown(question)
        response_text = (
            f"❌ **LLM Error:** {str(e)}\n\n"
            "**Your message was saved below for easy copy-pasting/retry:**\n"
            f"```\n{safe_question}\n```"
        )
        model_used = "error"

    # Empty/useless response detection
    if response_text and model_used != "error":
        stripped = response_text.strip()
        is_empty = len(stripped) < 10
        is_echo = stripped.lower().replace("'", "").replace('"', "") == question.lower().replace("'", "").replace('"', "")[:len(stripped)]
        if is_empty or is_echo:
            log.warning("Empty/echo response detected for: %.80s (response: %.80s)", question, stripped)
            response_text = (
                f"⚠️ I wasn't able to generate a useful response for this query.\n\n"
                f"**What happened:** The model returned {'an empty response' if is_empty else 'your question echoed back'}.\n"
                f"**Suggestion:** Try rephrasing, or use `/ask model:gemini` to force Gemini with tools.\n\n"
                f"```\n{question[:300]}\n```"
            )
            _routing_notes.append("Empty/echo response detected")
            model_used = "error"

    # Final response with embeds, file attachments, and action buttons
    if guardrail_note:
        response_text += guardrail_note
    if thread_hint:
        response_text += thread_hint

    # Render markdown tables as images
    table_image_file = None
    try:
        from table_renderer import extract_table_text, render_table_image
        table_text = extract_table_text(response_text)
        if table_text:
            img_bytes = render_table_image(table_text)
            if img_bytes:
                table_image_file = discord.File(io.BytesIO(img_bytes), filename="table.png")
    except Exception as e:
        log.debug("Table image rendering failed: %s", e)

    response_text = _format_markdown_for_discord(response_text)
    response_text = _format_tables_for_discord(response_text)
    chunks = _split_response(response_text)
    image_url = _extract_image_url(response_text)
    file_attachment = _extract_file_attachment(response_text)

    # Generate follow-up questions asynchronously
    follow_ups = await _generate_follow_ups(question, response_text)
    action_view = ResponseActions(
        response_text=response_text,
        question=question,
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
        follow_ups=follow_ups,
        bot=None,
    )

    # Auto-create thread for /ask responses (if not already in a thread/DM)
    _auto_thread = None
    if (
        cfg.thread_auto_create
        and not isinstance(interaction.channel, discord.Thread)
        and not isinstance(interaction.channel, discord.DMChannel)
        and hasattr(interaction.channel, "create_thread")
    ):
        try:
            _thread_name = question[:50].strip() + ("…" if len(question) > 50 else "")
            _archive_dur = 60 if cfg.thread_archive_minutes <= 60 else 1440
            _auto_thread = await interaction.channel.create_thread(
                name=f"💬 {_thread_name}",
                auto_archive_duration=_archive_dur,
                reason="Auto-threaded /ask conversation",
            )
            log.info("Auto-created thread '%s' for %s",
                     _auto_thread.name, interaction.user)
        except Exception as e:
            log.debug("Auto-thread creation failed: %s", e)

    def _build_footer() -> str:
        display_model = model_used.replace("models/", "") if model_used else "unknown"
        if "gemini" not in display_model.lower() and "gpt" not in display_model.lower() and "claude" not in display_model.lower():
            rate_str = "local · unlimited"
        else:
            rate_str = get_rate_info()
        if "gemma" in display_model.lower() or "ollama" in display_model.lower():
            actual_icon = "🏠"
        elif "gemini" in display_model.lower():
            actual_icon = "☁️"
        elif "gpt" in display_model.lower() or "openai" in display_model.lower():
            actual_icon = "🟢"
        elif "claude" in display_model.lower() or "anthropic" in display_model.lower():
            actual_icon = "🟣"
        else:
            actual_icon = "🔄"
        ft = f"💬 {conv.message_count} msgs | {rate_str} | {actual_icon} {display_model}"
        if _routing_notes:
            ft += " | ⚠️ " + " → ".join(_routing_notes)
        return ft

    # Long-response path: send as downloadable .md file
    if len(response_text) > _FILE_THRESHOLD:
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        md_file = discord.File(
            io.BytesIO(response_text.encode()),
            filename=f"openclaw-response-{ts}.md",
        )

        summary = response_text[:500].rstrip()
        if len(response_text) > 500:
            summary += "\n\n📎 **Full response attached as file**"

        embed = discord.Embed(description=summary, color=discord.Color.purple())
        display_question = question if len(question) < 200 else question[:197] + "..."
        embed.set_author(
            name=f"Replying to: {display_question}",
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
        )
        if image_url:
            embed.set_image(url=image_url)
        embed.set_footer(text=_build_footer())

        attachments = [md_file]
        if file_attachment:
            attachments.append(file_attachment[0])

        try:
            await interaction.edit_original_response(
                content=None, embed=embed, attachments=attachments, view=action_view,
            )
        except discord.NotFound:
            log.warning("Interaction expired, using followup for long response file")
            await interaction.followup.send(
                embed=embed, file=md_file, view=action_view,
            )

    # Normal path: split across embeds
    else:
        # If auto-threaded, redirect responses there and leave a link in the channel
        if _auto_thread:
            await interaction.edit_original_response(
                content=f"💬 Conversation continued in {_auto_thread.mention}",
                embed=None,
            )
            for i, chunk in enumerate(chunks):
                embed = discord.Embed(description=chunk, color=discord.Color.purple())
                if i == 0:
                    display_question = question if len(question) < 200 else question[:197] + "..."
                    embed.set_author(
                        name=f"Replying to: {display_question}",
                        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                    )
                    if image_url:
                        embed.set_image(url=image_url)
                is_last = i == len(chunks) - 1
                if is_last:
                    embed.set_footer(text=_build_footer())
                send_kwargs = {"embed": embed}
                if is_last:
                    send_kwargs["view"] = action_view
                if file_attachment and is_last:
                    send_kwargs["file"] = file_attachment[0]
                await _auto_thread.send(**send_kwargs)
        else:
            for i, chunk in enumerate(chunks):
                embed = discord.Embed(description=chunk, color=discord.Color.purple())
                if i == 0:
                    display_question = question if len(question) < 200 else question[:197] + "..."
                    embed.set_author(
                        name=f"Replying to: {display_question}",
                        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                    )
                    if image_url:
                        embed.set_image(url=image_url)

                is_last = i == len(chunks) - 1
                if is_last:
                    embed.set_footer(text=_build_footer())

                if i == 0:
                    kwargs: dict[str, Any] = {"content": None, "embed": embed}
                    if is_last:
                        kwargs["view"] = action_view
                    if file_attachment and is_last:
                        kwargs["attachments"] = [file_attachment[0]]
                    try:
                        await interaction.edit_original_response(**kwargs)
                    except discord.NotFound:
                        log.warning("Interaction expired, using followup for response")
                        fb_kwargs = {"embed": embed}
                        if is_last:
                            fb_kwargs["view"] = action_view
                        if file_attachment and is_last:
                            fb_kwargs["file"] = file_attachment[0]
                        await interaction.followup.send(**fb_kwargs)
                else:
                    kwargs = {"embed": embed}
                    if is_last:
                        kwargs["view"] = action_view
                    if file_attachment and is_last:
                        kwargs["file"] = file_attachment[0]
                    await interaction.followup.send(**kwargs)

    # Send table image if one was rendered
    if table_image_file:
        try:
            await interaction.followup.send(file=table_image_file)
        except Exception as e:
            log.debug("Failed to send table image: %s", e)

    audit_log(interaction.user, "ask", detail=question[:200])

    # Error tracking: record /ask outcome
    try:
        from error_tracker import record_outcome
        record_outcome(
            user_id=interaction.user.id,
            question=question,
            model_used=model_used,
            success=(model_used != "error"),
            error_msg=response_text if model_used == "error" else "",
            latency_ms=int((time.monotonic() - _ask_start) * 1000),
            routing_notes=_routing_notes,
        )
    except Exception as exc:
        log.debug("Error tracking record failed: %s", exc)

    # Response-time tracking
    try:
        from spending import record_response_time
        elapsed_ms = (time.monotonic() - _ask_start) * 1000
        record_response_time(elapsed_ms, model=model_used)
    except Exception as exc:
        log.debug("Response time tracking failed: %s", exc)

    conversation_store.cleanup_expired()

    # Fire-and-forget: correction detection & profile learning (Phase 14)
    async def _post_response_learning():
        try:
            from rules_engine import add_rule, detect_correction, extract_rule
            if detect_correction(question):
                prev_bot_msg = ""
                for msg in reversed(conv.history[:-1]):
                    if msg.get("role") == "model":
                        parts = msg.get("parts", [])
                        prev_bot_msg = " ".join(p for p in parts if isinstance(p, str))[:500]
                        break
                if prev_bot_msg:
                    rule = await extract_rule(question, prev_bot_msg)
                    if rule:
                        await add_rule(rule, question[:300])
                        try:
                            await interaction.followup.send(
                                f"📝 Got it — I'll remember: *{rule}*", ephemeral=True
                            )
                        except Exception as exc:
                            log.debug("Correction followup send failed: %s", exc)
        except Exception as e:
            log.debug("Correction detection failed (non-critical): %s", e)

        try:
            from user_profile import learn_from_message
            await learn_from_message(question, response_text)
        except Exception as e:
            log.debug("Profile learning failed (non-critical): %s", e)

        try:
            from fact_extractor import extract_and_store_facts, should_extract
            if should_extract(interaction.user.id, question):
                await extract_and_store_facts(question, response_text, interaction.user.id)
        except Exception as e:
            log.debug("Fact extraction failed (non-critical): %s", e)

        try:
            from goal_tracker import detect_goal, extract_and_store_goal
            if detect_goal(question):
                goal = await extract_and_store_goal(question, interaction.user.id)
                if goal:
                    try:
                        await interaction.followup.send(
                            f"🎯 Tracking goal: *{goal}*", ephemeral=True
                        )
                    except Exception as exc:
                        log.debug("Goal followup send failed: %s", exc)
        except Exception as e:
            log.debug("Goal tracking failed (non-critical): %s", e)

    asyncio.get_running_loop().create_task(_post_response_learning())


# ---------------------------------------------------------------------------
# Thread follow-up listener — treat messages in bot-created threads as /ask
# ---------------------------------------------------------------------------

@bot.event
async def on_message(message: discord.Message) -> None:
    """Handle follow-up messages in bot-created threads as conversational /ask."""
    # Ignore bot messages
    if message.author.bot:
        return

    # Only handle messages inside threads
    if not isinstance(message.channel, discord.Thread):
        await bot.process_commands(message)
        return

    # Only handle threads the bot owns
    if message.channel.owner_id != bot.user.id:
        await bot.process_commands(message)
        return

    # Auth check
    if not is_allowed(message.author.id):
        return

    if is_emergency_stopped():
        await message.channel.send(
            "🛑 **Emergency stop is active.** Conversation is disabled. Use `/estop resume` to resume."
        )
        return

    if not llm_is_configured():
        await message.channel.send("⚠️ LLM not configured.")
        return

    # Max message guard
    if cfg.thread_max_messages > 0:
        conv = conversation_store.get(
            user_id=message.author.id,
            channel_id=message.channel.id,
            user_name=str(message.author.display_name),
        )
        if conv.message_count >= cfg.thread_max_messages * 2:
            await message.channel.send(
                f"⚠️ This thread has reached {cfg.thread_max_messages} exchanges. "
                "Please start a new `/ask` for a fresh conversation."
            )
            return

    user_question = message.content.strip()
    if not user_question:
        return

    _ask_start = time.monotonic()

    async with message.channel.typing():
        conv = conversation_store.get(
            user_id=message.author.id,
            channel_id=message.channel.id,
            user_name=str(message.author.display_name),
        )

        model_pref = get_model_preference(message.author.id)
        from llm import _needs_tools as llm_needs_tools
        if model_pref == "local" and llm_needs_tools(user_question):
            model_pref = "gemini"

        response_text = ""

        try:
            async for chunk_text, is_final, meta in llm_chat_stream(
                user_message=user_question,
                history=conv.history,
                user_name=str(message.author.display_name),
                model_preference=model_pref,
            ):
                if is_final:
                    response_text = chunk_text
                    if "updated_history" in meta:
                        conv.update_from_llm(meta["updated_history"])
                        conversation_store.auto_save_thread(
                            message.author.id, message.channel.id, str(message.author.display_name)
                        )
                    break
        except Exception as e:
            log.error("Thread follow-up LLM error: %s", e)
            response_text = f"❌ **Error:** {e}"

        if not response_text or len(response_text.strip()) < 5:
            response_text = "⚠️ I wasn't able to generate a useful response. Try rephrasing your question."

        response_text = _format_markdown_for_discord(response_text)
        response_text = _format_tables_for_discord(response_text)
        chunks = _split_response(response_text)

        for chunk in chunks:
            embed = discord.Embed(description=chunk, color=discord.Color.purple())
            await message.channel.send(embed=embed)

    audit_log(message.author, "thread_followup", detail=user_question[:200])
    conversation_store.cleanup_expired()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the OpenClaw Discord bot."""
    # Run config validation and log results
    issues = cfg.validate()
    for issue in issues:
        if issue.startswith("❌"):
            log.error("Config: %s", issue)
        elif issue.startswith("⚠️"):
            log.warning("Config: %s", issue)
        else:
            log.info("Config: %s", issue)

    if not DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set. Create a .env file or set the environment variable.")
        sys.exit(1)

    log.info("Starting OpenClaw bot...")
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
