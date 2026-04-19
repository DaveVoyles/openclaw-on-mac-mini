"""Plugin management commands: /plugin."""

from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from audit import audit_log
from plugin_system import PluginRegistry

from ._helpers import require_auth

# Global plugin registry instance (initialized in bot.py)
_plugin_registry: PluginRegistry | None = None


def set_plugin_registry(registry: PluginRegistry) -> None:
    """Set the global plugin registry instance."""
    global _plugin_registry
    _plugin_registry = registry


def _register_plugin_commands(bot: commands.Bot) -> None:
    """Register /plugin commands."""

    plugin_group = app_commands.Group(name="plugin", description="Manage OpenClaw plugins")

    @plugin_group.command(name="list", description="List all installed plugins")
    @require_auth
    async def plugin_list(interaction: discord.Interaction):
        """List installed plugins."""
        if not _plugin_registry:
            await interaction.response.send_message(
                "❌ Plugin system not initialized",
                ephemeral=True,
            )
            return

        plugins = _plugin_registry.list_plugins()
        disabled = _plugin_registry.list_disabled_plugins()

        if not plugins and not disabled:
            await interaction.response.send_message(
                "No plugins installed.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🔌 Installed Plugins",
            description=f"{len(plugins)} active, {len(disabled)} disabled",
            color=discord.Color.blurple(),
        )

        # List active plugins
        if plugins:
            lines = []
            for metadata in sorted(plugins, key=lambda m: m.name):
                lines.append(
                    f"**{metadata.name}** v{metadata.version}\n"
                    f"  ↳ {metadata.description or 'No description'}\n"
                    f"  ↳ by {metadata.author}"
                )
            embed.add_field(
                name="✅ Active Plugins",
                value="\n\n".join(lines[:10]),  # Limit to 10
                inline=False,
            )

        # List disabled plugins
        if disabled:
            embed.add_field(
                name="❌ Disabled Plugins",
                value=", ".join(f"`{name}`" for name in sorted(disabled)),
                inline=False,
            )

        embed.set_footer(text="Use /plugin info name:<plugin> for details")
        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "plugin.list")

    @plugin_group.command(name="info", description="Get information about a plugin")
    @app_commands.describe(name="Plugin name")
    @require_auth
    async def plugin_info(interaction: discord.Interaction, name: str):
        """Get plugin information."""
        if not _plugin_registry:
            await interaction.response.send_message(
                "❌ Plugin system not initialized",
                ephemeral=True,
            )
            return

        info = _plugin_registry.get_plugin_info(name)
        if not info:
            await interaction.response.send_message(
                f"❌ Plugin '{name}' not found",
                ephemeral=True,
            )
            return

        metadata = info["metadata"]
        embed = discord.Embed(
            title=f"🔌 {metadata['name']} v{metadata['version']}",
            description=metadata.get("description", "No description"),
            color=discord.Color.green() if info["loaded"] else discord.Color.greyple(),
        )

        # Basic info
        embed.add_field(name="Author", value=metadata["author"], inline=True)
        embed.add_field(
            name="Status",
            value="✅ Active" if info["enabled"] else "❌ Disabled",
            inline=True,
        )

        # Skills
        skills = info.get("skills", [])
        if skills:
            skill_list = "\n".join(f"• `{s}`" for s in skills[:10])
            if len(skills) > 10:
                skill_list += f"\n... and {len(skills) - 10} more"
            embed.add_field(name=f"Skills ({len(skills)})", value=skill_list, inline=False)

        # Dependencies
        deps = metadata.get("dependencies", [])
        if deps:
            embed.add_field(
                name="Dependencies",
                value="\n".join(f"• {d}" for d in deps),
                inline=False,
            )

        # Permissions
        perms = metadata.get("permissions", [])
        if perms:
            embed.add_field(
                name="Permissions",
                value=", ".join(f"`{p}`" for p in perms),
                inline=False,
            )

        if metadata.get("homepage"):
            embed.add_field(name="Homepage", value=metadata["homepage"], inline=False)

        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "plugin.info", detail=f"name={name}")

    @plugin_group.command(name="enable", description="Enable a disabled plugin")
    @app_commands.describe(name="Plugin name to enable")
    @require_auth
    async def plugin_enable(interaction: discord.Interaction, name: str):
        """Enable a plugin."""
        if not _plugin_registry:
            await interaction.response.send_message(
                "❌ Plugin system not initialized",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        success, message = await _plugin_registry.enable_plugin(name)

        embed = discord.Embed(
            title="✅ Plugin Enabled" if success else "❌ Failed",
            description=message,
            color=discord.Color.green() if success else discord.Color.red(),
        )

        await interaction.followup.send(embed=embed, ephemeral=True)
        audit_log(interaction.user, "plugin.enable", detail=f"name={name}")

    @plugin_group.command(name="disable", description="Disable an active plugin")
    @app_commands.describe(name="Plugin name to disable")
    @require_auth
    async def plugin_disable(interaction: discord.Interaction, name: str):
        """Disable a plugin."""
        if not _plugin_registry:
            await interaction.response.send_message(
                "❌ Plugin system not initialized",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        success, message = await _plugin_registry.disable_plugin(name)

        embed = discord.Embed(
            title="✅ Plugin Disabled" if success else "❌ Failed",
            description=message,
            color=discord.Color.orange() if success else discord.Color.red(),
        )

        await interaction.followup.send(embed=embed, ephemeral=True)
        audit_log(interaction.user, "plugin.disable", detail=f"name={name}")

    @plugin_group.command(name="reload", description="Reload a plugin (hot-reload)")
    @app_commands.describe(name="Plugin name to reload")
    @require_auth
    async def plugin_reload(interaction: discord.Interaction, name: str):
        """Reload a plugin."""
        if not _plugin_registry:
            await interaction.response.send_message(
                "❌ Plugin system not initialized",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        success, message = await _plugin_registry.reload_plugin(name)

        embed = discord.Embed(
            title="✅ Plugin Reloaded" if success else "❌ Failed",
            description=message,
            color=discord.Color.green() if success else discord.Color.red(),
        )

        await interaction.followup.send(embed=embed, ephemeral=True)
        audit_log(interaction.user, "plugin.reload", detail=f"name={name}")

    @plugin_group.command(name="install", description="Install a plugin from directory")
    @app_commands.describe(path="Path to plugin directory")
    @require_auth
    async def plugin_install(interaction: discord.Interaction, path: str):
        """Install a plugin."""
        if not _plugin_registry:
            await interaction.response.send_message(
                "❌ Plugin system not initialized",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        plugin_path = Path(path)
        if not plugin_path.exists():
            await interaction.followup.send(
                f"❌ Path not found: {path}",
                ephemeral=True,
            )
            return

        success, message = await _plugin_registry.install_plugin(plugin_path)

        embed = discord.Embed(
            title="✅ Plugin Installed" if success else "❌ Installation Failed",
            description=message,
            color=discord.Color.green() if success else discord.Color.red(),
        )

        await interaction.followup.send(embed=embed, ephemeral=True)
        audit_log(interaction.user, "plugin.install", detail=f"path={path}")

    @plugin_group.command(name="uninstall", description="Uninstall a plugin")
    @app_commands.describe(name="Plugin name to uninstall")
    @require_auth
    async def plugin_uninstall(interaction: discord.Interaction, name: str):
        """Uninstall a plugin."""
        if not _plugin_registry:
            await interaction.response.send_message(
                "❌ Plugin system not initialized",
                ephemeral=True,
            )
            return

        # Confirmation
        await interaction.response.send_message(
            f"⚠️ Are you sure you want to uninstall `{name}`? This cannot be undone.\nReply with 'yes' to confirm.",
            ephemeral=True,
        )
        # Note: Full confirmation flow would require message listener
        # For now, this is a placeholder
        audit_log(interaction.user, "plugin.uninstall.attempt", detail=f"name={name}")

    # Add group to bot
    bot.tree.add_command(plugin_group)
