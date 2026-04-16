"""
Calendar Cog — Google Calendar integration for Discord.

Commands:
  /calendar today    — show today's events
  /calendar upcoming — show upcoming events (default 7 days)
  /calendar add      — create a new calendar event
  /calendar delete   — delete an event by ID
"""

import datetime
import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import require_auth, truncate_for_embed

log = logging.getLogger("openclaw.calendar_cog")


class CalendarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    calendar = app_commands.Group(name="calendar", description="Google Calendar commands")

    # ── /calendar today ───────────────────────────────────────────────────

    @calendar.command(name="today", description="Show today's calendar events")
    async def calendar_today(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            from calendar_skills import get_todays_events

            result = await get_todays_events()
            embed = discord.Embed(
                title="📅 Today's Events",
                description=truncate_for_embed(result),
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("calendar today failed")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    # ── /calendar upcoming ────────────────────────────────────────────────

    @calendar.command(name="upcoming", description="Show upcoming calendar events")
    @app_commands.describe(days="Number of days to look ahead (1–30, default 7)")
    async def calendar_upcoming(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 30] = 7,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            from calendar_skills import get_upcoming_events

            result = await get_upcoming_events(days=days)
            embed = discord.Embed(
                title=f"📅 Upcoming Events ({days} days)",
                description=truncate_for_embed(result),
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("calendar upcoming failed")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    # ── /calendar add ─────────────────────────────────────────────────────

    @calendar.command(name="add", description="Create a new calendar event")
    @app_commands.describe(
        title="Event title",
        when='Date/time string, e.g. "tomorrow at 3pm" or "Friday 2pm-4pm"',
        description="Optional event description",
        location="Optional event location",
    )
    @require_auth()
    async def calendar_add(
        self,
        interaction: discord.Interaction,
        title: str,
        when: str,
        description: str = "",
        location: str = "",
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            from dateutil import parser as dateutil_parser

            from calendar_skills import create_calendar_event

            # Parse the natural-language when string.
            # Support "2pm-4pm" style ranges by splitting on " - " or "-" after time.
            start_dt: datetime.datetime | None = None
            end_dt: datetime.datetime | None = None

            # Try to detect a range like "Friday 2pm-4pm" or "3pm - 5pm"
            import re
            range_match = re.search(r"(\d{1,2}(?::\d{2})?(?:am|pm)?)\s*[-–]\s*(\d{1,2}(?::\d{2})?(?:am|pm))", when, re.IGNORECASE)
            if range_match:
                base = when[: range_match.start()].strip()
                start_part = base + " " + range_match.group(1)
                end_part = base + " " + range_match.group(2)
                try:
                    start_dt = dateutil_parser.parse(start_part, fuzzy=True)
                    end_dt = dateutil_parser.parse(end_part, fuzzy=True)
                except (ValueError, TypeError):
                    start_dt = None

            if start_dt is None:
                start_dt = dateutil_parser.parse(when, fuzzy=True)

            if end_dt is None:
                end_dt = start_dt + datetime.timedelta(hours=1)

            start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
            end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%S")

            # Append location to description if provided (skill has no location param)
            full_description = description
            if location:
                full_description = f"{description}\nLocation: {location}".strip()

            result = await create_calendar_event(
                summary=title,
                start_time=start_iso,
                end_time=end_iso,
                description=full_description,
            )
            embed = discord.Embed(
                title="✅ Event Created",
                description=truncate_for_embed(result),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("calendar add failed")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    # ── /calendar delete ──────────────────────────────────────────────────

    @calendar.command(name="delete", description="Delete a calendar event by ID")
    @app_commands.describe(event_id="The Google Calendar event ID to delete")
    @require_auth()
    async def calendar_delete(self, interaction: discord.Interaction, event_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            from calendar_skills import delete_calendar_event

            result = await delete_calendar_event(event_id)
            embed = discord.Embed(
                title="🗑️ Delete Event",
                description=truncate_for_embed(result),
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("calendar delete failed")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(CalendarCog(bot))
