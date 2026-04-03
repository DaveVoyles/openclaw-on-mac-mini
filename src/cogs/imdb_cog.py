"""
IMDb Cog — movie and TV lookups via OMDb API.

Commands (under /media group):
  /media movie <title>   — full details for a movie
  /media tv <title>      — full details for a TV series
  /media search <query>  — search both movies and TV, return top 5 results
"""

import asyncio
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import truncate_for_embed
from config import cfg

log = logging.getLogger("openclaw")

_NO_KEY_MSG = (
    "❌ OMDb not configured. Get a free key at "
    "https://www.omdbapi.com/apikey.aspx and set OMDB_API_KEY"
)


async def _omdb_get(params: dict) -> dict:
    params["apikey"] = cfg.omdb_api_key
    async with aiohttp.ClientSession() as s:
        async with s.get("http://www.omdbapi.com/", params=params) as r:
            return await r.json()


class ImdbCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    media = app_commands.Group(name="media", description="Movie and TV lookups via IMDb/OMDb")

    # ── /media movie ──────────────────────────────────────────────────────

    @media.command(name="movie", description="Look up a movie on IMDb via OMDb")
    @app_commands.describe(title="Movie title to search for")
    async def movie(self, interaction: discord.Interaction, title: str):
        await interaction.response.defer(ephemeral=True)
        try:
            if not cfg.omdb_api_key:
                await interaction.followup.send(_NO_KEY_MSG, ephemeral=True)
                return

            data = await _omdb_get({"t": title, "type": "movie", "plot": "full"})

            if data.get("Response") == "False":
                await interaction.followup.send(
                    f"❌ No movie found for '{title}'", ephemeral=True
                )
                return

            embed = _build_media_embed(data, discord.Color.gold())
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            log.exception("movie lookup failed")
            await interaction.followup.send("❌ Failed to fetch movie info.", ephemeral=True)

    # ── /media tv ─────────────────────────────────────────────────────────

    @media.command(name="tv", description="Look up a TV series on IMDb via OMDb")
    @app_commands.describe(title="TV series title to search for")
    async def tv(self, interaction: discord.Interaction, title: str):
        await interaction.response.defer(ephemeral=True)
        try:
            if not cfg.omdb_api_key:
                await interaction.followup.send(_NO_KEY_MSG, ephemeral=True)
                return

            data = await _omdb_get({"t": title, "type": "series", "plot": "full"})

            if data.get("Response") == "False":
                await interaction.followup.send(
                    f"❌ No TV series found for '{title}'", ephemeral=True
                )
                return

            embed = _build_media_embed(data, discord.Color.gold())

            # Series-specific: seasons and run status
            seasons = data.get("totalSeasons", "")
            year = data.get("Year", "")
            if seasons:
                run_label = "Still running" if year.endswith("–") else f"Ended {year.split('–')[-1]}"
                embed.add_field(
                    name="Seasons",
                    value=f"{seasons} ({run_label})",
                    inline=True,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            log.exception("tv lookup failed")
            await interaction.followup.send("❌ Failed to fetch TV info.", ephemeral=True)

    # ── /media search ─────────────────────────────────────────────────────

    @media.command(name="search", description="Search IMDb for movies and TV shows (top 5)")
    @app_commands.describe(query="Title or keyword to search")
    async def search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            if not cfg.omdb_api_key:
                await interaction.followup.send(_NO_KEY_MSG, ephemeral=True)
                return

            # OMDb search doesn't support multi-type in one call; run both
            movie_data, tv_data = await _search_both(query)

            results: list[dict] = []
            for item in (movie_data.get("Search") or []):
                results.append(item)
            for item in (tv_data.get("Search") or []):
                # Avoid duplicates by imdbID
                if not any(r["imdbID"] == item["imdbID"] for r in results):
                    results.append(item)

            results = results[:5]

            if not results:
                await interaction.followup.send(
                    f"❌ No results found for '{query}'", ephemeral=True
                )
                return

            lines = []
            for i, r in enumerate(results, 1):
                kind = r.get("Type", "unknown")
                year = r.get("Year", "?")
                imdb_id = r.get("imdbID", "")
                link = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else ""
                label = f"**{i}. {r['Title']}** ({year}) — *{kind}*"
                if link:
                    label += f"\n  <{link}>"
                lines.append(label)

            embed = discord.Embed(
                title=f"🎬 IMDb Search: \"{query}\"",
                description=truncate_for_embed("\n\n".join(lines)),
                color=discord.Color.gold(),
            )
            embed.set_footer(text="Use /media movie or /media tv for full details")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            log.exception("imdb search failed")
            await interaction.followup.send("❌ Failed to search IMDb.", ephemeral=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _search_both(query: str) -> tuple[dict, dict]:
    """Run OMDb search for movies and series concurrently."""

    movie_task = asyncio.create_task(_omdb_get({"s": query, "type": "movie"}))
    tv_task = asyncio.create_task(_omdb_get({"s": query, "type": "series"}))
    return await asyncio.gather(movie_task, tv_task)


def _build_media_embed(data: dict, color: discord.Color) -> discord.Embed:
    """Build a rich embed from an OMDb detail response."""
    title = data.get("Title", "Unknown")
    year = data.get("Year", "")
    imdb_id = data.get("imdbID", "")
    imdb_url = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else None

    embed = discord.Embed(
        title=f"{title} ({year})" if year else title,
        url=imdb_url,
        color=color,
    )

    poster = data.get("Poster", "")
    if poster and poster != "N/A":
        embed.set_thumbnail(url=poster)

    rated = data.get("Rated", "")
    runtime = data.get("Runtime", "")
    genre = data.get("Genre", "")
    director = data.get("Director", "")
    actors = data.get("Actors", "")
    rating = data.get("imdbRating", "")
    votes = data.get("imdbVotes", "")
    plot = data.get("Plot", "")
    awards = data.get("Awards", "")

    if rated and rated != "N/A":
        embed.add_field(name="Rated", value=rated, inline=True)
    if runtime and runtime != "N/A":
        embed.add_field(name="Runtime", value=runtime, inline=True)
    if genre and genre != "N/A":
        embed.add_field(name="Genre", value=genre, inline=True)
    if director and director != "N/A":
        embed.add_field(name="Director", value=director, inline=True)
    if actors and actors != "N/A":
        cast = ", ".join(actors.split(", ")[:3])
        embed.add_field(name="Cast", value=cast, inline=True)
    if rating and rating != "N/A":
        votes_str = f" ({votes} votes)" if votes and votes != "N/A" else ""
        embed.add_field(name="IMDb Rating", value=f"⭐ {rating}{votes_str}", inline=True)
    if plot and plot != "N/A":
        embed.add_field(name="Plot", value=truncate_for_embed(plot, 500), inline=False)
    if awards and awards != "N/A" and awards.lower() != "n/a":
        embed.add_field(name="Awards", value=truncate_for_embed(awards, 200), inline=False)

    if imdb_url:
        embed.add_field(name="IMDb", value=f"[View on IMDb]({imdb_url})", inline=False)

    return embed


async def setup(bot):
    await bot.add_cog(ImdbCog(bot))
