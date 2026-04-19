"""Code commands: /diff, /run-code."""

import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands

from audit import audit_log
from code_sandbox import run_code as sandbox_run_code
from constants import OUTPUT_MAX_CHARS
from git_skills import git_diff, git_status

from ._helpers import require_auth, truncate_for_embed


def _register_code_commands(bot: commands.Bot) -> None:
    """Register /diff and /run-code."""

    # ------------------------------------------------------------------
    # /diff
    # ------------------------------------------------------------------

    @bot.tree.command(name="diff", description="Show uncommitted git changes in the OpenClaw repo")
    @require_auth
    async def diff_cmd(interaction: discord.Interaction):
        await interaction.response.defer()
        status, diff = await asyncio.gather(git_status(), git_diff())
        description = f"**Status**\n```\n{status[:800]}\n```\n**Diff**\n```diff\n{diff[:2600]}\n```"
        description = truncate_for_embed(description)
        embed = discord.Embed(
            title="🔀 Git Changes",
            description=description,
            color=discord.Color.gold(),
        )
        embed.set_footer(text='Run /ask "commit these changes" to commit via LLM')
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "diff")

    # ------------------------------------------------------------------
    # /run-code
    # ------------------------------------------------------------------

    @bot.tree.command(name="run-code", description="Execute Python code in a sandboxed container (safe, isolated)")
    @app_commands.describe(
        code="Python code to run (or wrap in a code block ```python ... ```)",
    )
    @require_auth
    async def run_code_cmd(interaction: discord.Interaction, code: str):
        await interaction.response.defer()

        if code.startswith("```"):
            lines = code.split("\n")
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            elif lines[0].strip().startswith("```"):
                lines[0] = ""
            code = "\n".join(lines).strip()

        if not code:
            await interaction.edit_original_response(content="❌ No code provided.")
            return

        if len(code) > 10_000:
            await interaction.edit_original_response(content="❌ Code too long (max 10,000 chars).")
            return

        await interaction.edit_original_response(content="⚙️ *Running code in sandboxed container…*")

        stdout, stderr, exit_code = await sandbox_run_code(code)

        parts = []
        if stdout:
            parts.append(f"**stdout:**\n```\n{stdout[:OUTPUT_MAX_CHARS]}\n```")
        if stderr:
            parts.append(f"**stderr:**\n```\n{stderr[:1500]}\n```")
        if not stdout and not stderr:
            parts.append("*(no output)*")

        code_status = "✅" if exit_code == 0 else "❌"
        header = f"{code_status} Exit code: {exit_code}"

        embed = discord.Embed(
            title="⚙️ Code Execution Result",
            description=f"{header}\n\n" + "\n".join(parts),
            color=discord.Color.green() if exit_code == 0 else discord.Color.red(),
        )
        embed.set_footer(text="Sandboxed · python:3.12-slim · no network · 256MB RAM · 30s timeout")

        out_file = None
        if len(stdout) > OUTPUT_MAX_CHARS:
            out_file = discord.File(io.BytesIO(stdout.encode()), filename="output.txt")

        from typing import Any

        kwargs: dict[str, Any] = {"content": None, "embed": embed}
        if out_file:
            kwargs["attachments"] = [out_file]
        await interaction.edit_original_response(**kwargs)
        audit_log(interaction.user, "run_code", detail=code[:200])
