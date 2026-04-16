"""
Perf Cog — Glances system monitor.

Commands:
  /perf  — show CPU, memory, load, disk, and process stats
"""

import asyncio
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import truncate_for_embed
from discord_error import build_error_embed

log = logging.getLogger("openclaw")


def _fmt_bytes(n: int) -> str:
    """Human-readable bytes (GB with one decimal)."""
    gb = n / (1024**3)
    if gb >= 1:
        return f"{gb:.1f} GB"
    mb = n / (1024**2)
    return f"{mb:.0f} MB"


class PerfCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="perf", description="Show system performance via Glances")
    async def perf(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            from config import cfg

            base = cfg.glances_url.rstrip("/")
            endpoints = ["cpu", "mem", "load", "fs", "processcount"]

            async with aiohttp.ClientSession() as session:
                async def fetch(ep: str):
                    async with session.get(f"{base}/api/3/{ep}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                        r.raise_for_status()
                        return await r.json()

                try:
                    results = await asyncio.gather(*[fetch(ep) for ep in endpoints])
                except Exception:  # broad: intentional
                    await interaction.followup.send(
                        f"⚠️ Glances not reachable at `{base}`. Is it running?",
                        ephemeral=True,
                    )
                    return

            cpu, mem, load, fs, procs = results

            # CPU line
            cpu_total = cpu.get("total", 0)
            cpu_user = cpu.get("user", 0)
            cpu_sys = cpu.get("system", 0)
            cpu_line = f"**CPU:** {cpu_total:.1f}% total (user: {cpu_user:.1f}%, sys: {cpu_sys:.1f}%)"

            # Load line
            load_line = f"**Load:** 1m={load.get('min1', 0):.2f}  5m={load.get('min5', 0):.2f}  15m={load.get('min15', 0):.2f}"

            # Memory line
            mem_pct = mem.get("percent", 0)
            mem_used = _fmt_bytes(mem.get("used", 0))
            mem_total = _fmt_bytes(mem.get("total", 0))
            mem_line = f"**Memory:** {mem_pct:.0f}% ({mem_used} / {mem_total})"

            # Disk lines (skip pseudo-filesystems)
            disk_lines = []
            for mount in fs:
                mnt = mount.get("mnt_point", "")
                if not mnt or mnt.startswith(("/proc", "/sys", "/dev", "/run")):
                    continue
                pct = mount.get("percent", 0)
                used = _fmt_bytes(mount.get("used", 0))
                size = _fmt_bytes(mount.get("size", 0))
                disk_lines.append(f"  `{mnt}` — {pct:.0f}% ({used} / {size})")
            disk_section = "**Disk:**\n" + "\n".join(disk_lines) if disk_lines else "**Disk:** N/A"

            # Processes
            p_total = procs.get("total", 0)
            p_running = procs.get("running", 0)
            proc_line = f"**Processes:** {p_total} total ({p_running} running)"

            description = truncate_for_embed(
                "\n".join([cpu_line, load_line, "", mem_line, "", disk_section, "", proc_line])
            )

            embed = discord.Embed(
                title="🖥️ System Performance",
                description=description,
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:  # broad: intentional
            log.exception("perf command failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/perf"), ephemeral=True)


async def setup(bot):
    await bot.add_cog(PerfCog(bot))
