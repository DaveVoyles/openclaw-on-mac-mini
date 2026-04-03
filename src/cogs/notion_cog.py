"""
Notion Cog — Notion integration via Maton gateway.

Commands:
  /notion search  — search Notion pages and databases
  /notion page    — create a new Notion page
  /notion todo    — add an item to a Notion todo database
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth, truncate_for_embed
from config import cfg

log = logging.getLogger("openclaw")

_NO_KEY_MSG = (
    "❌ Notion not configured. Set MATON_API_KEY and connect Notion at maton.ai"
)


async def _notion_request(path: str, method: str = "GET", body: dict | None = None):
    """Call the Notion API through the Maton gateway."""
    from gateway import GATEWAY_BASE, _http_request  # noqa: PLC0415

    url = f"{GATEWAY_BASE}/notion/{path.lstrip('/')}"
    return await _http_request(url, method, body)


def _page_title(result: dict) -> str:
    """Extract a displayable title from a Notion search result."""
    props = result.get("properties", {})
    for key in ("title", "Name", "name"):
        field = props.get(key)
        if field and isinstance(field, dict):
            rich = field.get("title", field.get("rich_text", []))
            if rich:
                return "".join(r.get("plain_text", "") for r in rich)
    title_arr = result.get("title", [])
    if title_arr:
        return "".join(r.get("plain_text", "") for r in title_arr)
    return result.get("id", "Untitled")[:8]


class NotionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    notion = app_commands.Group(name="notion", description="Notion integration via Maton")

    # ── /notion search ────────────────────────────────────────────────────

    @notion.command(name="search", description="Search Notion pages and databases")
    @app_commands.describe(query="Search query")
    async def notion_search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        if not cfg.maton_api_key:
            await interaction.followup.send(_NO_KEY_MSG, ephemeral=True)
            return
        try:
            data = await _notion_request(
                "v1/search", "POST", {"query": query, "page_size": 10}
            )
            results = data.get("results", [])
            if not results:
                embed = discord.Embed(
                    title=f"🔍 Notion: '{query}'",
                    description="No results found.",
                    color=discord.Color.orange(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            lines = []
            for r in results[:10]:
                title = _page_title(r)
                url = r.get("url", "")
                icon = "🗃️" if r.get("object") == "database" else "📄"
                lines.append(f"{icon} [{title}]({url})" if url else f"{icon} {title}")

            embed = discord.Embed(
                title=f"🔍 Notion: '{query}' ({len(results)} result{'s' if len(results) != 1 else ''})",
                description=truncate_for_embed("\n".join(lines)),
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            log.exception("notion search failed")
            await interaction.followup.send("❌ Notion search failed.", ephemeral=True)

    # ── /notion page ──────────────────────────────────────────────────────

    @notion.command(name="page", description="Create a new Notion page")
    @app_commands.describe(title="Page title", content="Page content (markdown supported)")
    @require_auth()
    async def notion_page(
        self,
        interaction: discord.Interaction,
        title: str,
        content: str,
    ):
        await interaction.response.defer(ephemeral=True)
        if not cfg.maton_api_key:
            await interaction.followup.send(_NO_KEY_MSG, ephemeral=True)
            return
        try:
            body = {
                "parent": {"type": "workspace", "workspace": True},
                "properties": {
                    "title": {"title": [{"text": {"content": title}}]}
                },
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"text": {"content": content}}]
                        },
                    }
                ],
            }
            result = await _notion_request("v1/pages", "POST", body)
            page_url = result.get("url", "")
            audit_log(interaction.user, "notion_page_create", title)
            msg = f"📄 Page '**{title}**' created in Notion"
            if page_url:
                msg += f"\n🔗 {page_url}"
            await interaction.followup.send(msg, ephemeral=True)
        except Exception:
            log.exception("notion page create failed")
            await interaction.followup.send("❌ Failed to create Notion page.", ephemeral=True)

    # ── /notion todo ──────────────────────────────────────────────────────

    @notion.command(name="todo", description="Add an item to a Notion todo database")
    @app_commands.describe(item="Todo item to add")
    @require_auth()
    async def notion_todo(self, interaction: discord.Interaction, item: str):
        await interaction.response.defer(ephemeral=True)
        if not cfg.maton_api_key:
            await interaction.followup.send(_NO_KEY_MSG, ephemeral=True)
            return
        try:
            # Find a todo/tasks database to insert into
            search_result = await _notion_request(
                "v1/search",
                "POST",
                {
                    "query": "todo",
                    "filter": {"value": "database", "property": "object"},
                    "page_size": 5,
                },
            )
            databases = search_result.get("results", [])
            if not databases:
                await interaction.followup.send(
                    "❌ No todo database found in Notion. "
                    "Create a database named 'Todo' or 'Tasks' first.",
                    ephemeral=True,
                )
                return

            db_id = databases[0]["id"]
            body = {
                "parent": {"database_id": db_id},
                "properties": {
                    "Name": {"title": [{"text": {"content": item}}]}
                },
            }
            await _notion_request("v1/pages", "POST", body)
            audit_log(interaction.user, "notion_todo_add", item)
            await interaction.followup.send(f"✅ Added to Notion: {item}", ephemeral=True)
        except Exception:
            log.exception("notion todo add failed")
            await interaction.followup.send("❌ Failed to add Notion todo.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(NotionCog(bot))
