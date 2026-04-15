"""Discord UI view and follow-up generation for /ask responses."""

from __future__ import annotations

import datetime
import json
import logging
import sys as _sys
from pathlib import Path
from typing import Any

import aiofiles
import discord

from agentmail import send_agent_mail
from constants import EMBED_SPLIT_LIMIT
from feedback_guardrails import _apply_feedback_guardrails
from llm import chat as llm_chat
from memory import store as conversation_store
from qmd import remember_fact
from runtime_state import (
    get_anchor_state,
    request_context,
    reset_anchor_state,
    reset_context_lock,
    resolve_context_lock,
    set_context_lock,
)

try:
    from quality_helpers import _record_quality_metric
except ImportError:
    def _record_quality_metric(event: str, context: str = "ask") -> None:
        try:
            from metrics_collector import get_collector
            get_collector().record_quality_event(event=event, context=context)
        except Exception:
            pass

log = logging.getLogger(__name__)


_ORIG: dict[str, Any] = {}  # populated at module bottom
_SENTINEL = object()


def _b(name: str, local_val: Any) -> Any:
    """Resolve name supporting patches to either this module or the bot module."""
    orig = _ORIG.get(name)
    if orig is not None and local_val is not orig:
        return local_val  # this module was patched
    bot_mod = _sys.modules.get('bot')
    if bot_mod is not None:
        bot_val = getattr(bot_mod, name, _SENTINEL)
        if bot_val is not _SENTINEL:
            return bot_val
    return local_val

_EMBED_LIMIT = EMBED_SPLIT_LIMIT
_FILE_THRESHOLD = 8000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_channel_thread_scope(
    channel: Any,
    channel_id: int | None,
    *,
    user_id: int | str | None = None,
) -> tuple[int | None, int | None]:
    """Normalize Discord channel/thread into (channel_id, thread_id) scope."""
    resolved_channel_id = channel_id
    resolved_thread_id = None
    if isinstance(channel, discord.Thread):
        resolved_thread_id = channel.id
        if channel.parent_id:
            resolved_channel_id = channel.parent_id
    lock, _ = resolve_context_lock(
        user_id=user_id,
        channel_id=resolved_channel_id,
        thread_id=resolved_thread_id,
    )
    if lock and lock.get("mode") in {"channel", "thread", "prior_report"}:
        if lock.get("channel_id"):
            resolved_channel_id = int(lock["channel_id"])
        if lock.get("mode") in {"thread", "prior_report"}:
            resolved_thread_id = int(lock["thread_id"]) if lock.get("thread_id") is not None else None
        elif lock.get("mode") == "channel":
            resolved_thread_id = None
    return resolved_channel_id, resolved_thread_id


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
        thread_id: int | None = None,
        timeout: float = 300,
        follow_ups: list[str] | None = None,
        bot=None,
        show_download: bool = False,
    ):
        super().__init__(timeout=timeout)
        self._response_text = response_text
        self._question = question
        self._user_id = user_id
        self._channel_id = channel_id
        self._thread_id = thread_id
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
        # W8-4: Download button when response spans more than 3 chunks
        if show_download:
            dl_btn = discord.ui.Button(
                label="📄 Download",
                style=discord.ButtonStyle.secondary,
                custom_id="download_response",
                row=1,
            )
            dl_btn.callback = self._download_callback
            self.add_item(dl_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("Only the original requester can use these buttons.", ephemeral=True)
            return False
        return True

    # W8-3: Feedback buttons in row 0 FIRST so they're always visible on mobile
    @discord.ui.button(label="👍 Helpful", style=discord.ButtonStyle.success, row=0)
    async def thumbs_up_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._record_feedback(interaction, "helpful")

    @discord.ui.button(label="👎 Not helpful", style=discord.ButtonStyle.danger, row=0)
    async def thumbs_down_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._record_feedback(interaction, "not_helpful")

    @discord.ui.button(label="📌 Save", style=discord.ButtonStyle.secondary, row=0)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            fact = self._response_text[:500]
            result = await _b("remember_fact", remember_fact)(
                f"Saved from /ask: {self._question[:100]}", fact
            )
            await interaction.followup.send(f"📌 Saved to memory.\n{result}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Save failed: {e}", ephemeral=True)

    @discord.ui.button(label="🔄 Regenerate", style=discord.ButtonStyle.secondary, row=0)
    async def regen_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        conv = _b("conversation_store", conversation_store).get(
            user_id=self._user_id,
            channel_id=self._channel_id,
            user_name=str(interaction.user.display_name),
        )
        if len(conv.history) >= 2:
            conv.history = conv.history[:-2]
        try:
            scoped_channel_id, scoped_thread_id = _b("_resolve_channel_thread_scope", _resolve_channel_thread_scope)(
                interaction.channel,
                self._channel_id,
                user_id=self._user_id,
            )
            with request_context(channel_id=scoped_channel_id, thread_id=scoped_thread_id, user_id=str(self._user_id)):
                response_text, updated_history, model_used = await _b("llm_chat", llm_chat)(
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

    @discord.ui.button(label="📧 Email", style=discord.ButtonStyle.secondary, row=0)
    async def email_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            result = await _b("send_agent_mail", send_agent_mail)(
                subject=f"OpenClaw: {self._question[:80]}",
                body=self._response_text,
            )
            await interaction.followup.send(f"📧 Emailed!\n{result}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Email failed: {e}", ephemeral=True)

    @discord.ui.button(label="🔒 Lock to Channel", style=discord.ButtonStyle.secondary, row=2)
    async def lock_channel_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _b("set_context_lock", set_context_lock)(
            user_id=self._user_id,
            mode="channel",
            channel_id=self._channel_id,
            thread_id=None,
        )
        await interaction.response.send_message("🔒 Context locked to this channel.", ephemeral=True)

    @discord.ui.button(label="🧵 Lock to Thread", style=discord.ButtonStyle.secondary, row=2)
    async def lock_thread_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        scoped_channel_id, scoped_thread_id = _b("_resolve_channel_thread_scope", _resolve_channel_thread_scope)(
            interaction.channel,
            self._channel_id,
            user_id=self._user_id,
        )
        _b("set_context_lock", set_context_lock)(
            user_id=self._user_id,
            mode="thread",
            channel_id=scoped_channel_id or self._channel_id,
            thread_id=scoped_thread_id,
        )
        await interaction.response.send_message(
            "🧵 Context locked to this thread." if scoped_thread_id else "ℹ️ Not in a thread. Locked to channel instead.",
            ephemeral=True,
        )

    @discord.ui.button(label="📎 Use Prior Report", style=discord.ButtonStyle.secondary, row=2)
    async def use_prior_report_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        scoped_channel_id, scoped_thread_id = _b("_resolve_channel_thread_scope", _resolve_channel_thread_scope)(
            interaction.channel,
            self._channel_id,
            user_id=self._user_id,
        )
        anchor = _b("get_anchor_state", get_anchor_state)(channel_id=scoped_channel_id, thread_id=scoped_thread_id)
        if not anchor:
            await interaction.response.send_message(
                "⚠️ No prior report/job anchor found for this scope yet.",
                ephemeral=True,
            )
            return
        _b("set_context_lock", set_context_lock)(
            user_id=self._user_id,
            mode="prior_report",
            channel_id=scoped_channel_id or self._channel_id,
            thread_id=scoped_thread_id,
            anchor_id=anchor.get("anchor_id"),
        )
        await interaction.response.send_message(
            f"📎 Follow-ups will use prior report anchor `{anchor.get('anchor_id')}`.",
            ephemeral=True,
        )

    @discord.ui.button(label="♻️ Reset Context", style=discord.ButtonStyle.secondary, row=2)
    async def reset_context_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        scoped_channel_id, scoped_thread_id = _b("_resolve_channel_thread_scope", _resolve_channel_thread_scope)(
            interaction.channel,
            self._channel_id,
            user_id=self._user_id,
        )
        _b("reset_context_lock", reset_context_lock)(self._user_id)
        if scoped_channel_id is not None:
            _b("reset_anchor_state", reset_anchor_state)(channel_id=scoped_channel_id, thread_id=scoped_thread_id)
        await interaction.response.send_message("♻️ Context lock and anchor reset for this scope.", ephemeral=True)

    def _make_followup_callback(self, follow_up_question: str):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()
            conv = _b("conversation_store", conversation_store).get(
                user_id=self._user_id,
                channel_id=self._channel_id,
                user_name=str(interaction.user.display_name),
            )
            try:
                scoped_channel_id, scoped_thread_id = _b("_resolve_channel_thread_scope", _resolve_channel_thread_scope)(
                    interaction.channel,
                    self._channel_id,
                    user_id=self._user_id,
                )
                with request_context(channel_id=scoped_channel_id, thread_id=scoped_thread_id, user_id=str(self._user_id)):
                    response_text, updated_history, model_used = await _b("llm_chat", llm_chat)(
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
                new_follow_ups = await _b("_generate_follow_ups", _generate_follow_ups)(follow_up_question, response_text)
                view = ResponseActions(
                    response_text=response_text,
                    question=follow_up_question,
                    user_id=self._user_id,
                    channel_id=self._channel_id,
                    thread_id=scoped_thread_id,
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
        conv = _b("conversation_store", conversation_store).get(
            user_id=self._user_id,
            channel_id=self._channel_id,
            user_name=str(interaction.user.display_name),
        )
        try:
            scoped_channel_id, scoped_thread_id = _b("_resolve_channel_thread_scope", _resolve_channel_thread_scope)(
                interaction.channel,
                self._channel_id,
                user_id=self._user_id,
            )
            with request_context(channel_id=scoped_channel_id, thread_id=scoped_thread_id, user_id=str(self._user_id)):
                response_text, updated_history, model_used = await _b("llm_chat", llm_chat)(
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

    async def _download_callback(self, interaction: discord.Interaction) -> None:
        """W8-4: Send full response as a downloadable .txt file."""
        await interaction.response.defer(ephemeral=True)
        try:
            import io as _io
            import datetime as _dt
            ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
            buf = _io.BytesIO(self._response_text.encode("utf-8"))
            file = discord.File(buf, filename=f"openclaw-response-{ts}.txt")
            await interaction.followup.send("📄 Full response:", file=file, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Download failed: {e}", ephemeral=True)

    async def _record_feedback(self, interaction: discord.Interaction, rating: str) -> None:
        normalized_rating = (
            "helpful"
            if str(rating).strip().lower() in {"helpful", "positive", "up", "thumbs_up"}
            else "not_helpful"
        )
        channel_id = getattr(interaction.channel, "id", None)
        message_id = getattr(interaction.message, "id", None)
        accepted, decision_reason = _b("_apply_feedback_guardrails", _apply_feedback_guardrails)(
            user_id=getattr(interaction.user, "id", None),
            channel_id=channel_id,
            message_id=message_id,
            rating=normalized_rating,
        )
        try:
            if not accepted:
                _b("_record_quality_metric", _record_quality_metric)(
                    event="ask_feedback_suppressed",
                    context="discord_ask",
                )
                _b("_record_quality_metric", _record_quality_metric)(
                    event=f"ask_feedback_suppressed_{decision_reason}",
                    context="discord_ask",
                )
                if decision_reason == "dedupe":
                    emoji = "👍" if normalized_rating == "helpful" else "👎"
                    await interaction.response.send_message(
                        f"{emoji} Already captured that feedback just now — thanks!",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "⏱️ Thanks — feedback is rate-limited right now. Try again shortly.",
                        ephemeral=True,
                    )
                return

            feedback_file = Path("/memory/feedback.jsonl")
            entry = {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "user_id": interaction.user.id,
                "channel_id": channel_id,
                "message_id": message_id,
                "question": self._question[:200],
                "rating": normalized_rating,
            }
            feedback_file.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(feedback_file, "a") as f:
                await f.write(json.dumps(entry) + "\n")
            _b("_record_quality_metric", _record_quality_metric)(
                event=f"ask_feedback_{normalized_rating}",
                context="discord_ask",
            )
            _b("_record_quality_metric", _record_quality_metric)(
                event="ask_feedback_accepted",
                context="discord_ask",
            )
            emoji = "👍" if normalized_rating == "helpful" else "👎"
            await interaction.response.send_message(
                f"{emoji} Feedback recorded — thanks!", ephemeral=True,
            )
        except Exception as e:
            log.debug("Feedback capture failed: %s", e)
            await interaction.response.send_message(
                "⚠️ Couldn't save feedback this time, but thanks for sharing.",
                ephemeral=True,
            )


async def _generate_follow_ups(question: str, response: str) -> list[str]:
    """Generate up to 3 short follow-up questions based on the Q&A exchange."""
    try:
        from llm.chat import chat
        prompt = (
            f"Based on this Q&A exchange, suggest up to 3 short follow-up questions "
            f"the user might want to ask next.\n"
            f"Q: {question[:800]}\n"
            f"A: {response[:1200]}\n\n"
            f'Return ONLY a JSON object: {{"follow_ups": ["question 1", "question 2", "question 3"]}}\n'
            f"Keep each question under 60 characters."
        )
        text, _, _ = await chat(prompt, model_preference="auto")
        # Try JSON parse first, fall back to line-split
        try:
            data = json.loads(text.strip())
            lines = data.get("follow_ups", [])
            if not isinstance(lines, list):
                raise ValueError("Not a list")
        except (json.JSONDecodeError, ValueError, AttributeError):
            lines = [line.strip() for line in text.strip().split("\n") if line.strip()]

        # Validate and filter suggestions
        question_lower = question.lower().strip()
        filtered: list[str] = []
        for line in lines:
            line = line.strip().strip('"').strip("'")
            if len(line) < 15:
                continue
            line_lower = line.lower()
            if line_lower == question_lower:
                continue
            # Filter vapid filler suggestions
            import re as _re
            if _re.match(r'^(explain more|can you)\b', line_lower) and len(line) < 40:
                continue
            filtered.append(line)
        return filtered[:3]
    except (ImportError, RuntimeError, TimeoutError):
        # LLM may be unavailable; return empty list to skip follow-ups
        return []


# ---------------------------------------------------------------------------
# Originals registry for _b() patch detection
# ---------------------------------------------------------------------------
_ORIG.update({
    "remember_fact": remember_fact,
    "send_agent_mail": send_agent_mail,
    "set_context_lock": set_context_lock,
    "reset_context_lock": reset_context_lock,
    "reset_anchor_state": reset_anchor_state,
    "resolve_context_lock": resolve_context_lock,
    "get_anchor_state": get_anchor_state,
    "llm_chat": llm_chat,
    "conversation_store": conversation_store,
    "_resolve_channel_thread_scope": _resolve_channel_thread_scope,
    "_generate_follow_ups": _generate_follow_ups,
    "_apply_feedback_guardrails": _apply_feedback_guardrails,
    "_record_quality_metric": _record_quality_metric,
})
