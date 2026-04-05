"""
OpenClaw Discord UI Components — reusable Views, Selects, Modals, and helpers.
"""

import discord
from discord import ui

# ---------------------------------------------------------------------------
# Standardized Embed Colors
# ---------------------------------------------------------------------------


class EmbedColors:
    """Consistent color palette for all embeds across the bot."""
    SUCCESS = 0x00FF00  # Green - successful operations
    INFO = 0x3498DB     # Blue - informational messages
    WARNING = 0xFF9900  # Orange - warnings and cautions
    ERROR = 0xFF0000    # Red - errors and failures
    AI = 0x9B59B6       # Purple - AI-generated content


# ---------------------------------------------------------------------------
# Pagination View — reusable ◀️/▶️ buttons for any list of embeds
# ---------------------------------------------------------------------------


class PaginationView(ui.View):
    """Paginate through a list of embeds with ◀️/▶️ buttons."""

    def __init__(self, pages: list[discord.Embed], *, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.current = 0
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current >= len(self.pages) - 1
        self.page_label.label = f"{self.current + 1}/{len(self.pages)}"

    @ui.button(label="◀️", style=discord.ButtonStyle.secondary, custom_id="page_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.current = max(0, self.current - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True, custom_id="page_label")
    async def page_label(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()

    @ui.button(label="▶️", style=discord.ButtonStyle.secondary, custom_id="page_next")
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.current = min(len(self.pages) - 1, self.current + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)


def paginate_items(
    items: list[str],
    *,
    title: str = "Results",
    color: int = EmbedColors.INFO,
    per_page: int = 10,
    footer: str = "",
) -> list[discord.Embed]:
    """Split a list of string items into paginated embeds."""
    if not items:
        return [discord.Embed(title=title, description="No items.", color=color)]
    pages = []
    for i in range(0, len(items), per_page):
        chunk = items[i : i + per_page]
        embed = discord.Embed(
            title=title,
            description="\n".join(chunk),
            color=color,
        )
        if footer:
            embed.set_footer(text=footer)
        pages.append(embed)
    return pages


# ---------------------------------------------------------------------------
# Embed builder — consistent formatting across all commands
# ---------------------------------------------------------------------------


def build_embed(
    title: str,
    description: str = "",
    *,
    color: int = EmbedColors.INFO,
    footer: str = "",
    model: str = "",
    thumbnail_url: str = "",
) -> discord.Embed:
    """Create a consistently-formatted embed with timestamp."""
    import datetime

    embed = discord.Embed(
        title=title,
        description=description[:4096] if description else "",
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    if footer:
        embed.set_footer(text=footer)
    elif model:
        embed.set_footer(text=f"via {model}")
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    return embed


def error_embed(message: str, *, title: str = "❌ Error") -> discord.Embed:
    """Create a consistently-formatted error embed."""
    import datetime

    return discord.Embed(
        title=title,
        description=message[:4096],
        color=EmbedColors.ERROR,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
