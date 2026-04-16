"""Shared Discord progress indicator for long-running bot operations."""
import logging
import time

import discord

log = logging.getLogger(__name__)


class ProgressTracker:
    """Manages a live-updating Discord embed for long-running operations."""

    def __init__(self, interaction, title="Working…"):
        self.interaction = interaction
        self.title = title
        self._lines = []
        self._start = time.monotonic()
        self._message = None

    async def start(self):
        """Send initial progress embed."""
        embed = self._build_embed()
        if self.interaction.response.is_done():
            self._message = await self.interaction.followup.send(embed=embed)
        else:
            await self.interaction.response.send_message(embed=embed)
            self._message = await self.interaction.original_response()

    async def update(self, line):
        """Append a progress line and update the embed."""
        self._lines.append(line)
        if self._message:
            try:
                await self._message.edit(embed=self._build_embed())
            except Exception as exc:  # broad: intentional  # noqa: BLE001
                log.debug("ProgressTracker edit failed (message may be deleted): %s", exc)

    async def done(self, summary=""):
        """Mark as complete."""
        elapsed = time.monotonic() - self._start
        suffix = (": " + summary) if summary else ""
        self._lines.append(f"✅ Done in {elapsed:.1f}s{suffix}")
        if self._message:
            try:
                await self._message.edit(embed=self._build_embed())
            except Exception as exc:  # broad: intentional  # noqa: BLE001
                log.debug("ProgressTracker edit failed (message may be deleted): %s", exc)

    def _build_embed(self):
        body = "\n".join(self._lines[-10:]) or "Starting…"
        elapsed = time.monotonic() - self._start
        embed = discord.Embed(title=self.title, description=body, color=discord.Color.purple())
        embed.set_footer(text=f"⏱ {elapsed:.1f}s elapsed")
        return embed
