"""Context menu (right-click) commands."""

import re

import discord
from discord import app_commands
from discord.ext import commands

from cogs.sms_cog import SMSSendConfirmView
from sms_ux import format_sms_error, validate_sms_body

from ._helpers import _is_allowed

_MASKED_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_FENCED_CODE_RE = re.compile(r"```[^\n]*\n([\s\S]*?)```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_EMOJI_RE = re.compile(r"<a?:([a-zA-Z0-9_]+):\d+>")
_MENTION_RE = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
_CHANNEL_RE = re.compile(r"<#(\d+)>")
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_STYLE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"\*\*(.*?)\*\*", r"\1"),
    (r"__(.*?)__", r"\1"),
    (r"\*(.*?)\*", r"\1"),
    (r"_(.*?)_", r"\1"),
    (r"~~(.*?)~~", r"\1"),
)


def _strip_discord_markdown_noise(text: str) -> str:
    cleaned = (text or "").replace("\r\n", "\n").strip()
    if not cleaned:
        return ""

    cleaned = _MASKED_LINK_RE.sub(r"\1", cleaned)
    cleaned = _FENCED_CODE_RE.sub(lambda m: m.group(1).strip(), cleaned)
    cleaned = _INLINE_CODE_RE.sub(r"\1", cleaned)
    cleaned = _EMOJI_RE.sub(r":\1:", cleaned)
    cleaned = _MENTION_RE.sub("@user", cleaned)
    cleaned = _ROLE_MENTION_RE.sub("@role", cleaned)
    cleaned = _CHANNEL_RE.sub("#channel", cleaned)
    cleaned = re.sub(r"^>\s?", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.replace("||", "")
    for pattern, replacement in _STYLE_REPLACEMENTS:
        cleaned = re.sub(pattern, replacement, cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _build_copy_workflow_payload(raw: str, bullet_limit: int = 5) -> str:
    cleaned = _strip_discord_markdown_noise(raw)
    if not cleaned:
        return ""

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    summary = lines[0] if lines else cleaned
    summary = summary[:220].rstrip()

    bullet_candidates: list[str] = []
    for line in lines:
        normalized = _BULLET_PREFIX_RE.sub("", line).strip()
        if not normalized:
            continue
        if line != summary:
            bullet_candidates.append(normalized)

    if not bullet_candidates:
        fragments = [frag.strip() for frag in _SENTENCE_SPLIT_RE.split(cleaned) if frag.strip()]
        for frag in fragments:
            normalized = _BULLET_PREFIX_RE.sub("", frag).strip()
            if normalized and normalized != summary:
                bullet_candidates.append(normalized)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in bullet_candidates:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item[:220])
        if len(deduped) >= bullet_limit:
            break

    payload_lines = [summary]
    if deduped:
        payload_lines.append("")
        payload_lines.extend(f"• {item}" for item in deduped)

    payload = "\n".join(payload_lines).strip()
    return payload[:1200].rstrip()


def _register_context_menus(bot: commands.Bot) -> None:
    """Register standalone context-menu commands."""

    async def _send_to_sms(interaction: discord.Interaction, message: discord.Message) -> None:
        if not _is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.",
                ephemeral=True,
            )
            return

        raw = (message.content or "").strip()
        if not raw:
            await interaction.response.send_message(
                "❌ Selected message has no text content to send via SMS.",
                ephemeral=True,
            )
            return

        try:
            cleaned = validate_sms_body(raw)
            preview = cleaned if len(cleaned) <= 220 else f"{cleaned[:220]}…"
            embed = discord.Embed(
                title="📲 Send Selected Message to SMS?",
                description=f"```text\n{preview}\n```",
                color=discord.Color.orange(),
            )
            embed.set_footer(text="Confirm to send to your configured phone.")
            await interaction.response.send_message(
                embed=embed,
                view=SMSSendConfirmView(interaction.user.id, cleaned),
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.response.send_message(format_sms_error(exc), ephemeral=True)

    async def _copy_workflow_context(interaction: discord.Interaction, message: discord.Message) -> None:
        if not _is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.",
                ephemeral=True,
            )
            return

        payload = _build_copy_workflow_payload((message.content or "").strip())
        if not payload:
            await interaction.response.send_message(
                "❌ Selected message has no text content to export.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"📋 Copy-ready export (mobile-friendly):\n```text\n{payload}\n```",
            ephemeral=True,
        )

    bot.tree.add_command(app_commands.ContextMenu(name="Send to SMS", callback=_send_to_sms))
    bot.tree.add_command(
        app_commands.ContextMenu(name="Copy Workflow Context", callback=_copy_workflow_context)
    )
