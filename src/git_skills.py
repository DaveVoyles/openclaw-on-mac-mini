import asyncio
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("openclaw.git_skills")
_SKILLS_DIR = Path(__file__).parent.parent
_WEBFETCH_CLI = _SKILLS_DIR / "skills" / "webfetch-md" / "cli.js"

async def _run_git(args):
    cmd = ["git"] + args
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=-1, stderr=-1, cwd=os.getcwd())
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 15)
        return stdout.decode().strip() or stderr.decode().strip()
    except (OSError, asyncio.TimeoutError) as e:
        return f"❌ git {' '.join(args)} failed: {e}"

async def git_status():
    """Check the project's Git status, showing which files are staged, unstaged, or untracked."""
    return await _run_git(["status"])

async def git_log(limit: int = 5):
    """View recent commit history for the project codebase."""
    return await _run_git(["log", "--oneline", f"-n {limit}"])

async def git_diff(staged: bool = False):
    """Compare code changes between current state and previous commits."""
    args = ["diff"]
    if staged:
        args.append("--staged")
    return await _run_git(args)

async def git_commit(message: str):
    """Commit all current changes with a brief descriptive message."""
    return await _run_git(["commit", "-am", message])

async def _run_webfetch(url: str) -> str:
    """Smartly scrape and fetch any URL, converting it into clean Markdown."""
    if not _WEBFETCH_CLI.exists():
        return "❌ webfetch-md skill not installed."
    cmd = ["node", str(_WEBFETCH_CLI), "--url", url]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=-1, stderr=-1)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        data = json.loads(stdout.decode())
        if not data.get("success"):
            return f"❌ webfetch-md failed: {data.get('error')}"
        markdown = data.get("markdown", "")
        if len(markdown) > 8000:
            markdown = markdown[:8000] + "\n... (truncated)"
        return f"**Title**: {data.get('title')}\n\n{markdown}"
    except (OSError, json.JSONDecodeError, asyncio.TimeoutError) as e:
        return f"❌ webfetch-md failed: {e}"

GIT_SKILLS = {
    "webfetch_md": _run_webfetch,
    "git_status": git_status,
    "git_log": git_log,
    "git_diff": git_diff,
    "git_commit": git_commit,
}
