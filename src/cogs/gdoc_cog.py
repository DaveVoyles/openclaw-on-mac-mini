"""
Google Docs Cog — Google Docs integration via Maton gateway.

Commands:
  /gdoc save  — create a new Google Doc with content
  /gdoc list  — list recent Google Docs
"""

import logging
from urllib.parse import quote as urlquote

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth, truncate_for_embed
from config import cfg
from discord_error import build_error_embed

log = logging.getLogger("openclaw")

_NO_KEY_MSG = (
    "❌ Google Docs not configured. Set MATON_API_KEY and connect Google Docs at maton.ai\n"
    "Run: `/ask Connect me to google-docs and google-drive via Maton`"
)


async def _drive_request(path: str, method: str = "GET", body: dict | None = None):
    """Call the Google Drive API through the Maton gateway."""
    from gateway import GATEWAY_BASE, _http_request  # noqa: PLC0415

    url = f"{GATEWAY_BASE}/google-drive/{path.lstrip('/')}"
    return await _http_request(url, method, body)


class GDocCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    gdoc = app_commands.Group(name="gdoc", description="Google Docs integration via Maton")

    # ── /gdoc save ────────────────────────────────────────────────────────

    @gdoc.command(name="save", description="Create a new Google Doc with the given content")
    @app_commands.describe(title="Document title", content="Document content")
    @require_auth()
    async def gdoc_save(
        self,
        interaction: discord.Interaction,
        title: str,
        content: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not cfg.maton_api_key:
            await interaction.followup.send(_NO_KEY_MSG, ephemeral=True)
            return
        try:
            from gateway import create_google_doc  # noqa: PLC0415

            result = await create_google_doc(title, content)
            audit_log(interaction.user, "gdoc_save", title)

            # Extract URL from the result string returned by create_google_doc
            doc_url = next(
                (w for w in result.split() if w.startswith("https://docs.google.com")),
                "",
            )
            embed = discord.Embed(
                title="📄 Google Doc Created",
                description=f"**[{title}]({doc_url})**" if doc_url else f"**{title}**",
                color=discord.Color.green(),
            )
            if doc_url:
                embed.add_field(name="🔗 Link", value=doc_url, inline=False)
            embed.set_footer(text="Saved to Google Docs (not NAS vault)")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            await interaction.followup.send(embed=build_error_embed(e, context="/gdoc save"), ephemeral=True)

    # ── /gdoc list ────────────────────────────────────────────────────────

    @gdoc.command(name="list", description="List recent Google Docs")
    async def gdoc_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not cfg.maton_api_key:
            await interaction.followup.send(_NO_KEY_MSG, ephemeral=True)
            return
        try:
            q = urlquote("mimeType='application/vnd.google-apps.document'")
            fields = urlquote("files(id,name,modifiedTime,webViewLink)")
            path = (
                f"v3/files?q={q}"
                "&orderBy=modifiedTime+desc"
                "&pageSize=10"
                f"&fields={fields}"
            )
            data = await _drive_request(path)
            files = data.get("files", [])
            if not files:
                await interaction.followup.send("ℹ️ No Google Docs found.", ephemeral=True)
                return

            lines = []
            for f in files[:10]:
                name = f.get("name", "Untitled")
                link = f.get("webViewLink", "")
                modified = f.get("modifiedTime", "")[:10]
                lines.append(
                    f"📄 [{name}]({link}) — {modified}" if link else f"📄 {name} — {modified}"
                )

            embed = discord.Embed(
                title="📂 Recent Google Docs",
                description=truncate_for_embed("\n".join(lines)),
                color=discord.Color.blue(),
            )
            embed.set_footer(text="Google Docs via Maton")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            await interaction.followup.send(embed=build_error_embed(e, context="/gdoc list"), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(GDocCog(bot))
