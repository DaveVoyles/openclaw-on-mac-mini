"""Skills commands: /skills."""

import discord
from discord import app_commands
from discord.ext import commands

from audit import audit_log
from skills import SKILLS

from ._helpers import require_auth


def _register_skills_commands(bot: commands.Bot) -> None:
    """Register /skills."""

    @bot.tree.command(name="skills", description="List all available OpenClaw skills")
    @app_commands.describe(category="Filter by category name (leave empty for overview)")
    @require_auth
    async def skills_cmd(interaction: discord.Interaction, category: str | None = None):
        """List skills grouped by category. Pick a category for details."""
        from skills import SKILL_CATEGORIES

        if category:
            match = None
            for cat_name in SKILL_CATEGORIES:
                if category.lower() in cat_name.lower():
                    match = cat_name
                    break
            if match:
                skill_names = SKILL_CATEGORIES[match]
                lines = []
                for name in sorted(skill_names):
                    fn = SKILLS.get(name)
                    if fn:
                        doc = (fn.__doc__ or "No description").strip().split("\n")[0][:100]
                        lines.append(f"• `{name}` — {doc}")
                embed = discord.Embed(
                    title=f"{match} ({len(lines)} skills)",
                    description="\n".join(lines) or "No skills in this category.",
                    color=discord.Color.blurple(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                audit_log(interaction.user, "skills", detail=f"category={match}")
                return
            else:
                await interaction.response.send_message(
                    f"❌ Unknown category `{category}`. Use `/skills` to see all categories.",
                    ephemeral=True,
                )
                return

        embed = discord.Embed(
            title=f"🧰 OpenClaw Skills ({len(SKILLS)} total)",
            description="Skills are grouped by category. Use `/skills category:<name>` to see details.\n"
                        "The LLM calls these automatically via `/ask`.",
            color=discord.Color.blurple(),
        )
        for cat_name, skill_names in SKILL_CATEGORIES.items():
            valid = [n for n in skill_names if n in SKILLS]
            if valid:
                preview = ", ".join(f"`{n}`" for n in sorted(valid)[:5])
                if len(valid) > 5:
                    preview += f" + {len(valid) - 5} more"
                embed.add_field(name=f"{cat_name} ({len(valid)})", value=preview, inline=False)

        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "skills")
