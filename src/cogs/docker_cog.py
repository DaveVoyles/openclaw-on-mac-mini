"""
Docker / Infrastructure Cog — extracted from bot.py
Handles: /containers, /status, /logs, /system, /dockerstats, /restart

Enhanced with interactive Discord UI components (select menus + action buttons).
"""

import json
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from approvals import (
    ApprovalView,
    RiskLevel,
    approval_store,
    build_approval_embed,
    is_emergency_stopped,
)
from cog_helpers import audit_log, is_service_allowed
from discord_error import build_error_embed
from discord_progress import ProgressTracker
from resource_monitor import resource_monitor
from skills import (
    get_container_logs,
    get_container_status,
    get_docker_stats,
    get_system_stats,
    get_uptime,
    list_containers,
    restart_container,
    stop_container,
)
from subprocess_utils import run as _run
from ui_components import EmbedColors

log = logging.getLogger("openclaw.docker_cog")

# ---------------------------------------------------------------------------
# Container list cache (avoids spawning `docker ps` on every autocomplete keystroke)
# ---------------------------------------------------------------------------
_container_cache: dict = {"data": [], "ts": 0.0}
CACHE_TTL = 10.0


async def _cached_container_list() -> str:
    """Return cached output of list_containers(), refreshing every CACHE_TTL seconds."""
    now = time.monotonic()
    if now - _container_cache["ts"] < CACHE_TTL and _container_cache["data"]:
        return _container_cache["data"]
    result = await list_containers()
    _container_cache["data"] = result
    _container_cache["ts"] = now
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _list_containers_structured() -> list[dict]:
    """Return container info as a list of dicts using docker ps JSON output."""
    rc, out, err = await _run([
        "docker", "ps", "-a",
        "--format", '{{json .}}',
    ])
    if rc != 0:
        return []
    containers = []
    for line in out.strip().splitlines():
        if line.strip():
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return containers


def _build_container_embed(container: dict) -> discord.Embed:
    """Build a rich embed for a single container's details."""
    name = container.get("Names", container.get("Name", "unknown"))
    state = container.get("State", "unknown")
    status = container.get("Status", "unknown")
    image = container.get("Image", "unknown")
    ports = container.get("Ports", "none")

    running = state.lower() == "running"
    color = EmbedColors.SUCCESS if running else EmbedColors.ERROR
    emoji = "🟢" if running else "🔴"

    embed = discord.Embed(
        title=f"{emoji} {name}",
        color=color,
    )
    embed.add_field(name="State", value=f"`{state}`", inline=True)
    embed.add_field(name="Status", value=f"`{status}`", inline=True)
    embed.add_field(name="Image", value=f"`{image[:50]}`", inline=False)
    if ports and ports != "none":
        embed.add_field(name="Ports", value=f"`{ports[:100]}`", inline=False)
    embed.set_footer(text="Select an action below")
    return embed


async def _container_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[str]]:
    """Live autocomplete: query docker ps and return matching container names."""
    try:
        result = await _cached_container_list()
        names = []
        for line in result.split("\n"):
            if line.strip() and not line.startswith("NAMES"):
                name = line.split()[0].strip() if line.split() else ""
                if name and (not current or current.lower() in name.lower()):
                    names.append(name)
        return [app_commands.Choice(name=n, value=n) for n in sorted(names)[:25]]
    except Exception:  # broad: intentional
        log.exception("container autocomplete failed")
        return []


# ---------------------------------------------------------------------------
# Interactive UI Views
# ---------------------------------------------------------------------------


class ContainerActionView(discord.ui.View):
    """Action buttons shown after a container is selected."""

    def __init__(self, container: dict, requester_id: int) -> None:
        super().__init__(timeout=120)
        self.container = container
        self.requester_id = requester_id
        self.container_name = container.get("Names", container.get("Name", "unknown"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "❌ Only the person who ran `/containers` can use these buttons.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Logs", emoji="📋", style=discord.ButtonStyle.primary)
    async def logs_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        button.disabled = True
        try:
            result = await get_container_logs(self.container_name, 30)
            # If output is long, send as a file attachment
            if len(result) > 1900:
                file = discord.File(
                    fp=__import__("io").BytesIO(result.encode()),
                    filename=f"{self.container_name}_logs.txt",
                )
                await interaction.followup.send(
                    f"📋 Logs for **{self.container_name}** (attached — too long for embed):",
                    file=file,
                )
            else:
                embed = discord.Embed(
                    title=f"📋 Logs: {self.container_name}",
                    description=f"```\n{result}\n```",
                    color=discord.Color.greyple(),
                )
                await interaction.followup.send(embed=embed)
            audit_log(interaction.user, "containers_logs", detail=self.container_name)
        except Exception as e:  # broad: intentional
            await interaction.followup.send(embed=build_error_embed(e, context="/containers logs"), ephemeral=True)
        finally:
            await interaction.message.edit(view=self)

    @discord.ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.primary)
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        button.disabled = True
        try:
            result = await get_container_status(self.container_name)
            embed = discord.Embed(
                title=f"📊 Stats: {self.container_name}",
                description=f"```\n{result}\n```",
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed)
            audit_log(interaction.user, "containers_stats", detail=self.container_name)
        except Exception as e:  # broad: intentional
            await interaction.followup.send(embed=build_error_embed(e, context="/containers stats"), ephemeral=True)
        finally:
            await interaction.message.edit(view=self)

    @discord.ui.button(label="Restart", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def restart_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_emergency_stopped():
            await interaction.response.send_message(
                "🛑 **Emergency stop is active.** All actions halted.", ephemeral=True,
            )
            return
        if not is_service_allowed("restart_container", self.container_name):
            await interaction.response.send_message(
                f"🚫 Restarting `{self.container_name}` is not permitted by policy.", ephemeral=True,
            )
            return

        button.disabled = True
        await interaction.message.edit(view=self)

        req = approval_store.create(
            action="restart_container",
            target=self.container_name,
            risk_level=RiskLevel.HIGH,
            requester_id=interaction.user.id,
            requester_name=str(interaction.user),
            channel_id=interaction.channel_id,
        )

        async def execute_restart(approved_req):
            result = await restart_container(approved_req.target)
            color = discord.Color.green() if result.startswith("✅") else discord.Color.red()
            embed = discord.Embed(
                title=f"🔄 Restart: {approved_req.target}",
                description=result,
                color=color,
            )
            audit_log(
                None, "restart_executed",
                detail=f"{approved_req.target} approved_by={approved_req.resolver_name}",
                result="success" if result.startswith("✅") else "failed",
            )
            return embed

        view = ApprovalView(req.request_id, execute_restart)
        embed = build_approval_embed(req)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        audit_log(interaction.user, "containers_restart_requested", detail=self.container_name)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_emergency_stopped():
            await interaction.response.send_message(
                "🛑 **Emergency stop is active.** All actions halted.", ephemeral=True,
            )
            return
        if not is_service_allowed("stop_container", self.container_name):
            await interaction.response.send_message(
                f"🚫 Stopping `{self.container_name}` is not permitted by policy.", ephemeral=True,
            )
            return

        button.disabled = True
        await interaction.message.edit(view=self)

        req = approval_store.create(
            action="stop_container",
            target=self.container_name,
            risk_level=RiskLevel.HIGH,
            requester_id=interaction.user.id,
            requester_name=str(interaction.user),
            channel_id=interaction.channel_id,
        )

        async def execute_stop(approved_req):
            result = await stop_container(approved_req.target)
            color = discord.Color.green() if result.startswith("✅") else discord.Color.red()
            embed = discord.Embed(
                title=f"⏹️ Stop: {approved_req.target}",
                description=result,
                color=color,
            )
            audit_log(
                None, "stop_executed",
                detail=f"{approved_req.target} approved_by={approved_req.resolver_name}",
                result="success" if result.startswith("✅") else "failed",
            )
            return embed

        view = ApprovalView(req.request_id, execute_stop)
        embed = build_approval_embed(req)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        audit_log(interaction.user, "containers_stop_requested", detail=self.container_name)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                child.disabled = True
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except Exception:  # broad: intentional — message may be deleted
                log.debug("Failed to disable view on timeout (message likely deleted)")


class ContainerSelect(discord.ui.Select):
    """Dropdown listing all containers. Selecting one shows details + action buttons."""

    def __init__(self, options: list[discord.SelectOption], containers: list[dict]) -> None:
        super().__init__(
            placeholder="Pick a container to manage…",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._containers_by_name = {
            c.get("Names", c.get("Name", "")): c for c in containers
        }

    async def callback(self, interaction: discord.Interaction):
        selected_name = self.values[0]
        container = self._containers_by_name.get(selected_name)
        if container is None:
            await interaction.response.send_message(
                f"❌ Container `{selected_name}` is no longer available.", ephemeral=True,
            )
            return

        embed = _build_container_embed(container)
        action_view = ContainerActionView(container, requester_id=interaction.user.id)
        action_view.message = await interaction.message.edit(embed=embed, view=action_view)
        await interaction.response.defer()
        audit_log(interaction.user, "containers_select", detail=selected_name)


class ContainerSelectView(discord.ui.View):
    """Interactive container management with a select dropdown."""

    def __init__(self, containers: list[dict], requester_id: int) -> None:
        super().__init__(timeout=120)
        self.requester_id = requester_id
        options = []
        for c in containers[:25]:  # Discord limit: 25 options
            name = c.get("Names", c.get("Name", "unknown"))
            state = c.get("State", "unknown")
            status = c.get("Status", "unknown")
            image = c.get("Image", "unknown")
            emoji = "🟢" if state.lower() == "running" else "🔴"
            options.append(discord.SelectOption(
                label=name[:25],
                value=name,
                description=f"{status[:40]} | {image[:40]}"[:100],
                emoji=emoji,
            ))
        if options:
            self.add_item(ContainerSelect(options=options, containers=containers))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "❌ Only the person who ran `/containers` can use this menu.", ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                child.disabled = True
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except Exception:  # broad: intentional — message may be deleted
                log.debug("Failed to disable view on timeout (message likely deleted)")


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class DockerCog(commands.Cog, name="Docker"):
    """Docker and infrastructure management commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        embed = build_error_embed(error, context="docker command")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="containers", description="List all Docker containers with interactive management")
    async def containers_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        containers = await _list_containers_structured()

        if not containers:
            # Fallback to plain text if JSON parsing failed
            result = await list_containers()
            embed = discord.Embed(
                title="🐳 Running Containers",
                description=f"```\n{result}\n```",
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed)
            audit_log(interaction.user, "containers")
            return

        running = sum(1 for c in containers if c.get("State", "").lower() == "running")
        stopped = len(containers) - running

        embed = discord.Embed(
            title="🐳 Docker Containers",
            description=(
                f"**{len(containers)}** containers — "
                f"🟢 {running} running, 🔴 {stopped} stopped\n\n"
                "Select a container from the dropdown to view details and manage it."
            ),
            color=discord.Color.blue(),
        )

        view = ContainerSelectView(containers, requester_id=interaction.user.id)
        msg = await interaction.followup.send(embed=embed, view=view)
        view.message = msg
        audit_log(interaction.user, "containers")

    @app_commands.command(name="status", description="Get detailed status for a container")
    @app_commands.describe(service="Container name (e.g. sonarr, radarr, plex)")
    @app_commands.autocomplete(service=_container_autocomplete)
    async def status_cmd(self, interaction: discord.Interaction, service: str) -> None:
        progress = ProgressTracker(interaction, title="🐳 Container Status")
        await progress.start()
        await progress.update(f"🐳 Checking {service}…")
        result = await get_container_status(service)
        await progress.done(service)
        embed = discord.Embed(
            title=f"📦 Status: {service}",
            description=f"```\n{result}\n```",
            color=discord.Color.blue(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "status", detail=service)

    @app_commands.command(name="logs", description="View recent logs from a container")
    @app_commands.describe(service="Container name", lines="Number of lines (5-100, default 30)")
    @app_commands.autocomplete(service=_container_autocomplete)
    async def logs_cmd(self, interaction: discord.Interaction, service: str, lines: int = 30) -> None:
        await interaction.response.defer()
        result = await get_container_logs(service, lines)
        embed = discord.Embed(
            title=f"📜 Logs: {service} (last {min(max(lines, 5), 100)})",
            description=f"```\n{result}\n```",
            color=discord.Color.greyple(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "logs", detail=f"{service} lines={lines}")

    @app_commands.command(name="system", description="Show system resource usage")
    async def system_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        stats = await get_system_stats()
        uptime_str = await get_uptime()
        embed = discord.Embed(
            title="🖥️ System Stats",
            description=stats,
            color=discord.Color.green(),
        )
        embed.add_field(name="Uptime", value=f"```{uptime_str}```", inline=False)
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "system")

    @app_commands.command(name="dockerstats", description="Show resource usage per container")
    async def dockerstats_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        result = await get_docker_stats()
        embed = discord.Embed(
            title="📊 Docker Resource Usage",
            description=f"```\n{result}\n```",
            color=discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "dockerstats")

    @app_commands.command(name="restart", description="Restart a Docker container (requires approval)")
    @app_commands.describe(service="Container name to restart")
    @app_commands.autocomplete(service=_container_autocomplete)
    async def restart_cmd(self, interaction: discord.Interaction, service: str):
        if is_emergency_stopped():
            await interaction.response.send_message(
                "🛑 **Emergency stop is active.** All actions are halted. Use `/estop resume` to resume.",
                ephemeral=True,
            )
            audit_log(interaction.user, "restart", detail=service, result="blocked_estop")
            return

        if not is_service_allowed("restart_container", service):
            await interaction.response.send_message(
                f"🚫 Restarting `{service}` is not permitted by policy.", ephemeral=True,
            )
            audit_log(interaction.user, "restart", detail=service, result="blocked_by_policy")
            return

        req = approval_store.create(
            action="restart_container",
            target=service,
            risk_level=RiskLevel.HIGH,
            requester_id=interaction.user.id,
            requester_name=str(interaction.user),
            channel_id=interaction.channel_id,
        )

        async def execute_restart(approved_req):
            result = await restart_container(approved_req.target)
            color = discord.Color.green() if result.startswith("✅") else discord.Color.red()
            embed = discord.Embed(
                title=f"🔄 Restart: {approved_req.target}",
                description=result,
                color=color,
            )
            audit_log(
                None, "restart_executed",
                detail=f"{approved_req.target} approved_by={approved_req.resolver_name}",
                result="success" if result.startswith("✅") else "failed",
            )
            return embed

        view = ApprovalView(req.request_id, execute_restart)
        embed = build_approval_embed(req)

        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        audit_log(interaction.user, "restart_requested", detail=service)


    # -----------------------------------------------------------------------
    # /monitor subcommand group
    # -----------------------------------------------------------------------

    monitor_group = app_commands.Group(
        name="monitor",
        description="Container resource monitoring — set CPU/memory thresholds and get alerts",
    )

    @monitor_group.command(name="set", description="Set CPU/memory alert thresholds for a container")
    @app_commands.describe(
        container="Container name",
        cpu="CPU threshold % (default 80)",
        memory="Memory threshold % (default 90)",
    )
    @app_commands.autocomplete(container=_container_autocomplete)
    async def monitor_set(
        self,
        interaction: discord.Interaction,
        container: str,
        cpu: float = 80.0,
        memory: float = 90.0,
    ) -> None:
        t = resource_monitor.set_threshold(container, cpu=cpu, memory=memory)
        embed = discord.Embed(
            title=f"📐 Monitor Set: {container}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="CPU Threshold", value=f"{t.cpu_percent}%", inline=True)
        embed.add_field(name="Memory Threshold", value=f"{t.memory_percent}%", inline=True)
        embed.add_field(name="Cooldown", value=f"{t.cooldown_seconds}s", inline=True)
        embed.set_footer(text="Alerts fire when usage exceeds these thresholds")
        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "monitor_set", detail=f"{container} cpu={cpu} mem={memory}")

    @monitor_group.command(name="remove", description="Stop monitoring a container")
    @app_commands.describe(container="Container name to stop monitoring")
    @app_commands.autocomplete(container=_container_autocomplete)
    async def monitor_remove(self, interaction: discord.Interaction, container: str) -> None:
        if resource_monitor.remove(container):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=f"🗑️ Monitor Removed: {container}",
                    description="Resource alerts disabled for this container.",
                    color=discord.Color.greyple(),
                )
            )
        else:
            await interaction.response.send_message(
                f"⚠️ No monitor found for `{container}`.", ephemeral=True
            )
        audit_log(interaction.user, "monitor_remove", detail=container)

    @monitor_group.command(name="list", description="Show all monitored containers and their thresholds")
    async def monitor_list(self, interaction: discord.Interaction):
        thresholds = resource_monitor.list_all()
        if not thresholds:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="📋 Resource Monitors",
                    description="No containers are being monitored.\nUse `/monitor set` to add one.",
                    color=discord.Color.light_grey(),
                )
            )
            return
        lines = []
        for t in thresholds:
            status = "✅" if t.enabled else "⏸️"
            lines.append(f"{status} **{t.container}** — CPU: {t.cpu_percent}% · Mem: {t.memory_percent}%")
        embed = discord.Embed(
            title="📋 Resource Monitors",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{len(thresholds)} container(s) monitored")
        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "monitor_list")

    @monitor_group.command(name="check", description="Run a manual resource check right now")
    async def monitor_check(self, interaction: discord.Interaction):
        await interaction.response.defer()
        violations = await resource_monitor.check_all()
        if not violations:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="✅ All Clear",
                    description="No monitored containers exceed their thresholds.",
                    color=discord.Color.green(),
                )
            )
        else:
            for threshold, stats in violations:
                embed = discord.Embed(
                    title=f"⚠️ Resource Alert: {threshold.container}",
                    color=discord.Color.red(),
                )
                embed.add_field(
                    name="CPU",
                    value=f"{stats['cpu']:.1f}% (threshold: {threshold.cpu_percent}%)",
                    inline=True,
                )
                embed.add_field(
                    name="Memory",
                    value=f"{stats['memory']:.1f}% (threshold: {threshold.memory_percent}%)",
                    inline=True,
                )
                await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "monitor_check", detail=f"{len(violations)} violations")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DockerCog(bot))
