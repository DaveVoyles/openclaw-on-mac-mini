"""
Note Cog — quick note-taking and vault search from Discord.

Commands:
  /note create  — save a markdown note to the Obsidian vault
  /note list    — browse recent vault notes by type
  /note view    — read a vault note's content
  /note search  — full-text search across vault notes
"""

import io
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("openclaw")

VAULT_DIR = Path(os.getenv("VAULT_DIR", "/vault"))


class NoteCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    note = app_commands.Group(name="note", description="Quick note-taking to Obsidian vault")

    # ── /note create ──────────────────────────────────────────────────────

    @note.command(name="create", description="Create a note and save to vault")
    @app_commands.describe(
        title="Note title",
        content="Note content (supports markdown)",
        tags="Optional comma-separated tags",
    )
    async def note_create(
        self,
        interaction: discord.Interaction,
        title: str,
        content: str,
        tags: str = "",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            from obsidian_writer import save_to_vault

            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            result = await save_to_vault(
                title=title,
                content=content,
                content_type="note",
                tags=tag_list,
                source_url="",
            )
            embed = discord.Embed(
                title="📝 Note Saved",
                description=f"**{title}**\n\n{result}",
                color=discord.Color.green(),
            )
            if tag_list:
                embed.add_field(name="Tags", value=", ".join(f"`{t}`" for t in tag_list))
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("note create failed")
            await interaction.followup.send(f"❌ Failed to save note: {e}", ephemeral=True)

    # ── /note list ────────────────────────────────────────────────────────

    @note.command(name="list", description="List recent vault notes")
    @app_commands.describe(content_type="Filter by type: all, note, research, bookmark")
    async def note_list(
        self,
        interaction: discord.Interaction,
        content_type: str = "all",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            from obsidian_writer import list_vault

            result = await list_vault(content_type if content_type != "all" else "")
            embed = discord.Embed(
                title=f"📂 Vault Notes ({content_type})",
                description=result[:4000] if result else "No notes found.",
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("note list failed")
            await interaction.followup.send(f"❌ Failed to list notes: {e}", ephemeral=True)

    # ── /note view ────────────────────────────────────────────────────────

    @note.command(name="view", description="View a vault note's content")
    @app_commands.describe(filename="Filename of the note to view")
    async def note_view(self, interaction: discord.Interaction, filename: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            found = None
            for f in VAULT_DIR.rglob("*.md"):
                if filename.lower() in f.name.lower():
                    found = f
                    break
            if not found:
                await interaction.followup.send(
                    f"❌ Note `{filename}` not found in vault", ephemeral=True
                )
                return

            content = found.read_text()
            if len(content) > 4000:
                file = discord.File(io.BytesIO(content.encode()), filename=found.name)
                await interaction.followup.send(f"📄 {found.name}", file=file, ephemeral=True)
            else:
                embed = discord.Embed(
                    title=f"📄 {found.name}",
                    description=content,
                    color=discord.Color.blue(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("note view failed")
            await interaction.followup.send(f"❌ Failed to view note: {e}", ephemeral=True)

    # ── /note search ──────────────────────────────────────────────────────

    @note.command(name="search", description="Search vault notes by content")
    @app_commands.describe(query="Search term")
    async def note_search(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            matches: list[str] = []
            query_lower = query.lower()
            for f in VAULT_DIR.rglob("*.md"):
                try:
                    content = f.read_text()
                except (OSError, UnicodeDecodeError):
                    continue
                if query_lower in content.lower() or query_lower in f.name.lower():
                    preview = ""
                    for line in content.split("\n"):
                        if query_lower in line.lower():
                            preview = line.strip()[:100]
                            break
                    if not preview:
                        preview = content[:100].strip()
                    matches.append(f"📄 **{f.name}**\n> {preview}")

            if matches:
                embed = discord.Embed(
                    title=f"🔍 Search: '{query}' ({len(matches)} results)",
                    description="\n\n".join(matches[:10]),
                    color=discord.Color.blue(),
                )
            else:
                embed = discord.Embed(
                    title=f"🔍 Search: '{query}'",
                    description="No matching notes found.",
                    color=discord.Color.orange(),
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("note search failed")
            await interaction.followup.send(f"❌ Search failed: {e}", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(NoteCog(bot))
