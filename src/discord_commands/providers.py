"""Provider status command: /providers."""

import time

import discord
from discord.ext import commands

from ._helpers import require_auth


def _register_providers_commands(bot: commands.Bot) -> None:
    """Register /providers."""

    @bot.tree.command(name="providers", description="Show live LLM provider availability and circuit-breaker state")
    @require_auth
    async def providers_cmd(interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            from llm.providers import _circuit, _proxy_healthy, scan_providers

            availability = await scan_providers()

            lines: list[str] = []
            for provider, info in availability.items():
                available = info["available"]
                latency_ms = info["latency_ms"]
                avail_icon = "✅" if available else "❌"
                latency_str = f"{latency_ms}ms" if latency_ms is not None else "—"

                state = _circuit.get(provider, {})
                open_until = state.get("open_until", 0.0)
                is_open = open_until > time.monotonic()
                circuit_label = "🔴 circuit open" if is_open else "🟢 circuit closed"

                extra = ""
                if provider == "copilot":
                    proxy_icon = "🟢" if _proxy_healthy else "🔴"
                    extra = f"  proxy {proxy_icon}"

                lines.append(f"`{provider:<10}` {avail_icon}  `{latency_str:<8}`  {circuit_label}{extra}")

            embed = discord.Embed(
                title="🔌 Provider Status",
                description="\n".join(lines) if lines else "No providers found.",
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed)
        except Exception as exc:  # broad: intentional
            await interaction.followup.send(embed=embed)
