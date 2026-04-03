"""
GitHub Cog — PR and issue monitoring for Discord.

Commands:
  /github prs      — list open pull requests for a repo
  /github issues   — list open issues for a repo
  /github watch    — subscribe to PR/issue updates via DM
  /github unwatch  — unsubscribe from a repo
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

from cog_helpers import require_auth

log = logging.getLogger("openclaw.github_cog")

VAULT_DIR = Path(os.getenv("VAULT_DIR", "/vault"))
WATCHES_FILE = VAULT_DIR / "github_watches.json"

GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

async def _load_watches() -> dict:
    """Load the watches JSON; returns {} on missing/corrupt file."""
    def _read():
        if not WATCHES_FILE.exists():
            return {}
        try:
            return json.loads(WATCHES_FILE.read_text())
        except Exception:
            return {}
    return await asyncio.to_thread(_read)


async def _save_watches(data: dict) -> None:
    """Persist the watches dict to disk."""
    def _write():
        WATCHES_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATCHES_FILE.write_text(json.dumps(data, indent=2))
    await asyncio.to_thread(_write)


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _gh_headers() -> dict:
    from config import cfg
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if cfg.github_token:
        headers["Authorization"] = f"token {cfg.github_token}"
    return headers


def _parse_repo(repo: str | None) -> str | None:
    """Return 'owner/repo' string or None if nothing configured."""
    if repo:
        return repo.strip()
    from config import cfg
    if cfg.github_default_repos:
        return cfg.github_default_repos[0]
    return None


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return iso[:10]


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class GitHubCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._monitor_task.start()

    def cog_unload(self):
        self._monitor_task.cancel()

    github = app_commands.Group(name="github", description="GitHub PR and issue monitoring")

    # ── /github prs ───────────────────────────────────────────────────────

    @github.command(name="prs", description="List open pull requests for a GitHub repo")
    @app_commands.describe(repo="Repository in owner/repo format (optional if GITHUB_DEFAULT_REPOS is set)")
    async def github_prs(self, interaction: discord.Interaction, repo: str = ""):
        await interaction.response.defer(ephemeral=True)
        try:
            from config import cfg

            target = _parse_repo(repo or None)
            if not target:
                await interaction.followup.send(
                    "❌ No repo specified and `GITHUB_DEFAULT_REPOS` is not configured.\n"
                    "Usage: `/github prs owner/repo`",
                    ephemeral=True,
                )
                return

            if not cfg.github_token:
                log.warning("GITHUB_TOKEN not configured — requests will be rate-limited")

            url = f"{GITHUB_API}/repos/{target}/pulls?state=open&per_page=10"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=_gh_headers())

            if resp.status_code == 404:
                await interaction.followup.send(f"❌ Repo `{target}` not found or not accessible.", ephemeral=True)
                return
            if resp.status_code == 401:
                await interaction.followup.send(
                    "❌ GitHub returned 401 — check your `GITHUB_TOKEN`.", ephemeral=True
                )
                return
            resp.raise_for_status()

            prs = resp.json()
            embed = discord.Embed(
                title=f"🔀 Open PRs — {target}",
                color=discord.Color.blue(),
                url=f"https://github.com/{target}/pulls",
            )
            if not prs:
                embed.description = "No open pull requests."
            else:
                lines = []
                for pr in prs[:10]:
                    lines.append(
                        f"[#{pr['number']} {pr['title']}]({pr['html_url']})\n"
                        f"  by **{pr['user']['login']}** · opened {_fmt_date(pr['created_at'])}"
                    )
                embed.description = "\n\n".join(lines)
                embed.set_footer(text=f"Showing {len(prs)} of open PRs")

            if not cfg.github_token:
                embed.set_footer(text="⚠️ No GITHUB_TOKEN set — unauthenticated requests are rate-limited (60/hr)")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except httpx.HTTPStatusError as e:
            log.exception("github prs HTTP error")
            await interaction.followup.send(f"❌ GitHub API error: {e.response.status_code}", ephemeral=True)
        except Exception:
            log.exception("github prs failed")
            await interaction.followup.send("❌ Failed to fetch pull requests.", ephemeral=True)

    # ── /github issues ────────────────────────────────────────────────────

    @github.command(name="issues", description="List open issues for a GitHub repo")
    @app_commands.describe(
        repo="Repository in owner/repo format (optional if GITHUB_DEFAULT_REPOS is set)",
        label="Filter by label (optional)",
    )
    async def github_issues(self, interaction: discord.Interaction, repo: str = "", label: str = ""):
        await interaction.response.defer(ephemeral=True)
        try:
            from config import cfg

            target = _parse_repo(repo or None)
            if not target:
                await interaction.followup.send(
                    "❌ No repo specified and `GITHUB_DEFAULT_REPOS` is not configured.\n"
                    "Usage: `/github issues owner/repo`",
                    ephemeral=True,
                )
                return

            params = "state=open&per_page=30"
            if label:
                params += f"&labels={label}"
            url = f"{GITHUB_API}/repos/{target}/issues?{params}"

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=_gh_headers())

            if resp.status_code == 404:
                await interaction.followup.send(f"❌ Repo `{target}` not found or not accessible.", ephemeral=True)
                return
            resp.raise_for_status()

            # GitHub issues endpoint also returns PRs — filter them out
            issues = [i for i in resp.json() if "pull_request" not in i][:10]

            title = f"🐛 Open Issues — {target}"
            if label:
                title += f" [{label}]"

            embed = discord.Embed(
                title=title,
                color=discord.Color.orange(),
                url=f"https://github.com/{target}/issues",
            )
            if not issues:
                embed.description = "No open issues" + (f" matching label `{label}`." if label else ".")
            else:
                lines = []
                for issue in issues:
                    label_str = ""
                    if issue.get("labels"):
                        label_str = " · " + " ".join(f"`{lb['name']}`" for lb in issue["labels"][:3])
                    lines.append(
                        f"[#{issue['number']} {issue['title']}]({issue['html_url']})\n"
                        f"  by **{issue['user']['login']}** · {_fmt_date(issue['created_at'])}{label_str}"
                    )
                embed.description = "\n\n".join(lines)
                embed.set_footer(text=f"Showing {len(issues)} issues (PRs excluded)")

            if not cfg.github_token:
                embed.set_footer(text="⚠️ No GITHUB_TOKEN set — unauthenticated requests are rate-limited (60/hr)")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except httpx.HTTPStatusError as e:
            log.exception("github issues HTTP error")
            await interaction.followup.send(f"❌ GitHub API error: {e.response.status_code}", ephemeral=True)
        except Exception:
            log.exception("github issues failed")
            await interaction.followup.send("❌ Failed to fetch issues.", ephemeral=True)

    # ── /github watch ─────────────────────────────────────────────────────

    @github.command(name="watch", description="Get DM notifications when PRs or issues change")
    @app_commands.describe(repo="Repository to watch in owner/repo format")
    @require_auth()
    async def github_watch(self, interaction: discord.Interaction, repo: str):
        await interaction.response.defer(ephemeral=False)
        try:
            repo = repo.strip()
            if "/" not in repo:
                await interaction.followup.send("❌ Repo must be in `owner/repo` format.", ephemeral=True)
                return

            watches = await _load_watches()
            user_key = str(interaction.user.id)
            user_repos: list[str] = watches.get(user_key, {}).get("repos", [])

            if repo in user_repos:
                await interaction.followup.send(f"ℹ️ You're already watching **{repo}**.")
                return

            user_repos.append(repo)
            if user_key not in watches:
                watches[user_key] = {}
            watches[user_key]["repos"] = user_repos
            # seed last_checked so the monitor doesn't flood old history on first run
            if "last_checked" not in watches[user_key]:
                watches[user_key]["last_checked"] = {}
            watches[user_key]["last_checked"][repo] = datetime.now(timezone.utc).isoformat()

            await _save_watches(watches)
            log.info("User %s watching %s", interaction.user, repo)
            await interaction.followup.send(
                f"👁 Now watching **{repo}** — I'll DM you when PRs or issues are opened/closed."
            )
        except Exception:
            log.exception("github watch failed")
            await interaction.followup.send("❌ Failed to save watch.", ephemeral=True)

    # ── /github unwatch ───────────────────────────────────────────────────

    @github.command(name="unwatch", description="Stop receiving notifications for a repo")
    @app_commands.describe(repo="Repository to stop watching in owner/repo format")
    async def github_unwatch(self, interaction: discord.Interaction, repo: str):
        await interaction.response.defer(ephemeral=True)
        try:
            repo = repo.strip()
            watches = await _load_watches()
            user_key = str(interaction.user.id)

            user_data = watches.get(user_key, {})
            user_repos: list[str] = user_data.get("repos", [])

            if repo not in user_repos:
                await interaction.followup.send(f"ℹ️ You weren't watching **{repo}**.", ephemeral=True)
                return

            user_repos.remove(repo)
            watches[user_key]["repos"] = user_repos
            # clean up last_checked entry too
            watches[user_key].get("last_checked", {}).pop(repo, None)

            if not user_repos:
                watches.pop(user_key, None)

            await _save_watches(watches)
            log.info("User %s unwatched %s", interaction.user, repo)
            await interaction.followup.send(f"✅ Stopped watching **{repo}**", ephemeral=True)
        except Exception:
            log.exception("github unwatch failed")
            await interaction.followup.send("❌ Failed to remove watch.", ephemeral=True)

    # ── Background monitor ────────────────────────────────────────────────

    @tasks.loop(minutes=30)
    async def _monitor_task(self):
        """Poll watched repos and DM users about new/closed PRs and issues."""
        try:
            watches = await _load_watches()
            if not watches:
                return

            # Collect all unique repos to batch API calls
            all_repos: dict[str, list[str]] = {}  # repo -> [user_ids]
            for user_id, user_data in watches.items():
                for repo in user_data.get("repos", []):
                    all_repos.setdefault(repo, []).append(user_id)

            now = datetime.now(timezone.utc)
            dirty = False  # track if we need to re-save

            async with httpx.AsyncClient(timeout=15) as client:
                for repo, user_ids in all_repos.items():
                    try:
                        changes = await self._check_repo_changes(client, repo, watches, user_ids, now)
                        if changes:
                            dirty = True
                    except Exception:
                        log.exception("Monitor error for repo %s", repo)

            if dirty:
                await _save_watches(watches)

        except Exception:
            log.exception("GitHub monitor task failed")

    async def _check_repo_changes(
        self,
        client: httpx.AsyncClient,
        repo: str,
        watches: dict,
        user_ids: list[str],
        now: datetime,
    ) -> bool:
        """Check for changes in a repo and DM watchers. Returns True if watches was mutated."""
        headers = _gh_headers()
        dirty = False

        # Determine the earliest last_checked timestamp among all watchers
        since_ts: str | None = None
        for uid in user_ids:
            ts = watches.get(uid, {}).get("last_checked", {}).get(repo)
            if ts and (since_ts is None or ts < since_ts):
                since_ts = ts

        since_param = f"&since={since_ts}" if since_ts else ""

        # Fetch recent PRs and issues
        prs_resp = await client.get(
            f"{GITHUB_API}/repos/{repo}/pulls?state=all&per_page=20&sort=updated&direction=desc",
            headers=headers,
        )
        issues_resp = await client.get(
            f"{GITHUB_API}/repos/{repo}/issues?state=all&per_page=20&sort=updated&direction=desc{since_param}",
            headers=headers,
        )

        if prs_resp.status_code != 200 or issues_resp.status_code != 200:
            log.warning("Monitor: non-200 for %s (prs=%s issues=%s)", repo, prs_resp.status_code, issues_resp.status_code)
            return False

        prs = prs_resp.json()
        raw_issues = issues_resp.json()
        issues = [i for i in raw_issues if "pull_request" not in i]

        for uid in user_ids:
            user_since = watches.get(uid, {}).get("last_checked", {}).get(repo)
            if not user_since:
                # First run — just stamp and skip to avoid notification flood
                watches.setdefault(uid, {}).setdefault("last_checked", {})[repo] = now.isoformat()
                dirty = True
                continue

            since_dt = datetime.fromisoformat(user_since.replace("Z", "+00:00"))
            notifications: list[str] = []

            for pr in prs:
                updated = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
                if updated <= since_dt:
                    continue
                created = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
                if created > since_dt:
                    notifications.append(
                        f"🔀 **{repo}**: PR #{pr['number']} '{pr['title']}' was **opened** by {pr['user']['login']}\n{pr['html_url']}"
                    )
                elif pr["state"] == "closed":
                    action = "merged" if pr.get("merged_at") else "closed"
                    notifications.append(
                        f"🔀 **{repo}**: PR #{pr['number']} '{pr['title']}' was **{action}**\n{pr['html_url']}"
                    )

            for issue in issues:
                updated = datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
                if updated <= since_dt:
                    continue
                created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
                if created > since_dt:
                    notifications.append(
                        f"🐛 **{repo}**: Issue #{issue['number']} '{issue['title']}' was **opened** by {issue['user']['login']}\n{issue['html_url']}"
                    )
                elif issue["state"] == "closed":
                    notifications.append(
                        f"🐛 **{repo}**: Issue #{issue['number']} '{issue['title']}' was **closed**\n{issue['html_url']}"
                    )

            if notifications:
                user = self.bot.get_user(int(uid))
                if user is None:
                    try:
                        user = await self.bot.fetch_user(int(uid))
                    except Exception:
                        log.warning("Monitor: could not fetch user %s", uid)
                        user = None

                if user:
                    try:
                        msg = "🔔 **GitHub Update**\n\n" + "\n\n".join(notifications[:5])
                        await user.send(msg[:2000])
                    except discord.Forbidden:
                        log.warning("Monitor: DMs closed for user %s", uid)
                    except Exception:
                        log.exception("Monitor: failed to DM user %s", uid)

            # Update last_checked for this user+repo
            watches.setdefault(uid, {}).setdefault("last_checked", {})[repo] = now.isoformat()
            dirty = True

        return dirty

    @_monitor_task.before_loop
    async def _before_monitor(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(GitHubCog(bot))
