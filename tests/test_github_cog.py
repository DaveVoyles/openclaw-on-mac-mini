"""Tests for GitHubCog commands and helpers."""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/t_cogs.db")

import pytest

# -- Import helpers ----------------------------------------------------------

def _get_mod():
    """Import github_cog (cached after first call), with Loop.start patched to no-op."""
    if "cogs.github_cog" in sys.modules:
        return sys.modules["cogs.github_cog"]
    from discord.ext import tasks as dxt
    with patch.object(dxt.Loop, "start", lambda *a, **k: None):
        import cogs.github_cog as m
    return m


def _make_bot():
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 999
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(return_value=None)
    bot.wait_until_ready = AsyncMock()
    return bot


def _make_interaction(user_id=1, channel_id=100):
    inter = AsyncMock()
    inter.user.id = user_id
    inter.user.display_name = "TestUser"
    inter.user.__str__ = lambda self: "TestUser#0001"
    inter.channel_id = channel_id
    inter.guild_id = 999
    inter.response.send_message = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


def _make_cog(bot=None):
    mod = _get_mod()
    bot = bot or _make_bot()
    with patch.object(type(mod.GitHubCog._monitor_task), "start", lambda *a, **k: None):
        cog = mod.GitHubCog(bot)
    return cog


# ── Helper functions ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_watches_missing_file(tmp_path, monkeypatch):
    mod = _get_mod()
    monkeypatch.setattr(mod, "WATCHES_FILE", tmp_path / "ghost.json")
    result = await mod._load_watches()
    assert result == {}


@pytest.mark.asyncio
async def test_load_watches_corrupt_file(tmp_path, monkeypatch):
    mod = _get_mod()
    f = tmp_path / "watches.json"
    f.write_text("not-json{{{")
    monkeypatch.setattr(mod, "WATCHES_FILE", f)
    result = await mod._load_watches()
    assert result == {}


@pytest.mark.asyncio
async def test_load_watches_valid(tmp_path, monkeypatch):
    mod = _get_mod()
    f = tmp_path / "watches.json"
    f.write_text('{"42": {"repos": ["owner/repo"]}}')
    monkeypatch.setattr(mod, "WATCHES_FILE", f)
    result = await mod._load_watches()
    assert result == {"42": {"repos": ["owner/repo"]}}


@pytest.mark.asyncio
async def test_save_watches(tmp_path, monkeypatch):
    mod = _get_mod()
    monkeypatch.setattr(mod, "WATCHES_FILE", tmp_path / "watches.json")
    await mod._save_watches({"user": "data"})
    assert (tmp_path / "watches.json").read_text() == '{\n  "user": "data"\n}'


def test_gh_headers_with_token():
    mod = _get_mod()
    cfg_mock = MagicMock()
    cfg_mock.github_token = "my_secret_token"
    with patch("config.cfg", cfg_mock):
        headers = mod._gh_headers()
    assert "Authorization" in headers
    assert headers["Authorization"] == "token my_secret_token"


def test_gh_headers_without_token():
    mod = _get_mod()
    cfg_mock = MagicMock()
    cfg_mock.github_token = None
    with patch("config.cfg", cfg_mock):
        headers = mod._gh_headers()
    assert "Authorization" not in headers
    assert headers["Accept"] == "application/vnd.github+json"


def test_parse_repo_explicit():
    mod = _get_mod()
    assert mod._parse_repo("owner/repo") == "owner/repo"


def test_parse_repo_from_config():
    mod = _get_mod()
    cfg_mock = MagicMock()
    cfg_mock.github_default_repos = ["default/repo"]
    with patch("config.cfg", cfg_mock):
        assert mod._parse_repo(None) == "default/repo"


def test_parse_repo_none_when_unconfigured():
    mod = _get_mod()
    cfg_mock = MagicMock()
    cfg_mock.github_default_repos = []
    with patch("config.cfg", cfg_mock):
        assert mod._parse_repo(None) is None


def test_fmt_date_valid():
    mod = _get_mod()
    assert mod._fmt_date("2024-03-15T12:34:56Z") == "2024-03-15"


def test_fmt_date_invalid():
    mod = _get_mod()
    result = mod._fmt_date("not-a-date")
    assert result == "not-a-date"  # first 10 chars of a 10-char string


# ── /github prs ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_github_prs_no_repo_no_default():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()
    cfg_mock = MagicMock()
    cfg_mock.github_token = None
    cfg_mock.github_default_repos = []
    with patch("config.cfg", cfg_mock):
        await cog.github_prs.callback(cog, inter, repo="")
    inter.followup.send.assert_awaited_once()
    assert "No repo" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_github_prs_success_with_prs():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()

    fake_prs = [
        {
            "number": 1,
            "title": "Fix bug",
            "html_url": "https://github.com/owner/repo/pull/1",
            "user": {"login": "alice"},
            "created_at": "2024-01-10T00:00:00Z",
        }
    ]
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = fake_prs
    fake_resp.raise_for_status = MagicMock()

    cfg_mock = MagicMock()
    cfg_mock.github_token = "tok"
    cfg_mock.github_default_repos = ["owner/repo"]

    with patch("config.cfg", cfg_mock), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await cog.github_prs.callback(cog, inter, repo="owner/repo")

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args.kwargs["embed"]
    assert "Fix bug" in embed.description


@pytest.mark.asyncio
async def test_github_prs_empty():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = []
    fake_resp.raise_for_status = MagicMock()

    cfg_mock = MagicMock()
    cfg_mock.github_token = "tok"
    cfg_mock.github_default_repos = ["owner/repo"]

    with patch("config.cfg", cfg_mock), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await cog.github_prs.callback(cog, inter, repo="owner/repo")

    embed = inter.followup.send.call_args.kwargs["embed"]
    assert "No open pull requests" in embed.description


@pytest.mark.asyncio
async def test_github_prs_404():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()

    fake_resp = MagicMock()
    fake_resp.status_code = 404

    cfg_mock = MagicMock()
    cfg_mock.github_token = "tok"
    cfg_mock.github_default_repos = ["owner/repo"]

    with patch("config.cfg", cfg_mock), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await cog.github_prs.callback(cog, inter, repo="owner/repo")

    assert "not found" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_github_prs_401():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()

    fake_resp = MagicMock()
    fake_resp.status_code = 401

    cfg_mock = MagicMock()
    cfg_mock.github_token = "bad_tok"
    cfg_mock.github_default_repos = ["owner/repo"]

    with patch("config.cfg", cfg_mock), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await cog.github_prs.callback(cog, inter, repo="owner/repo")

    assert "401" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_github_prs_generic_exception():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()

    cfg_mock = MagicMock()
    cfg_mock.github_token = "tok"
    cfg_mock.github_default_repos = ["owner/repo"]

    with patch("config.cfg", cfg_mock), \
         patch("httpx.AsyncClient", side_effect=Exception("network error")):
        await cog.github_prs.callback(cog, inter, repo="owner/repo")

    assert "Failed to fetch" in inter.followup.send.call_args[0][0]


# ── /github issues ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_github_issues_success():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()

    fake_issues = [
        {
            "number": 10,
            "title": "Bug report",
            "html_url": "https://github.com/owner/repo/issues/10",
            "user": {"login": "bob"},
            "created_at": "2024-02-01T00:00:00Z",
            "labels": [{"name": "bug"}],
            "state": "open",
        }
    ]
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = fake_issues
    fake_resp.raise_for_status = MagicMock()

    cfg_mock = MagicMock()
    cfg_mock.github_token = "tok"
    cfg_mock.github_default_repos = ["owner/repo"]

    with patch("config.cfg", cfg_mock), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await cog.github_issues.callback(cog, inter, repo="owner/repo", label="bug")

    embed = inter.followup.send.call_args.kwargs["embed"]
    assert "Bug report" in embed.description
    assert "bug" in embed.title


@pytest.mark.asyncio
async def test_github_issues_empty():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = []
    fake_resp.raise_for_status = MagicMock()

    cfg_mock = MagicMock()
    cfg_mock.github_token = "tok"
    cfg_mock.github_default_repos = ["owner/repo"]

    with patch("config.cfg", cfg_mock), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await cog.github_issues.callback(cog, inter, repo="owner/repo", label="")

    embed = inter.followup.send.call_args.kwargs["embed"]
    assert "No open issues" in embed.description


@pytest.mark.asyncio
async def test_github_issues_filters_prs():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()

    # Mixes a PR (has pull_request key) and an issue
    items = [
        {"number": 1, "title": "PR", "html_url": "...", "user": {"login": "a"}, "created_at": "2024-01-01T00:00:00Z", "labels": [], "state": "open", "pull_request": {}},
        {"number": 2, "title": "Real Issue", "html_url": "...", "user": {"login": "b"}, "created_at": "2024-01-01T00:00:00Z", "labels": [], "state": "open"},
    ]
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = items
    fake_resp.raise_for_status = MagicMock()

    cfg_mock = MagicMock()
    cfg_mock.github_token = "tok"
    cfg_mock.github_default_repos = ["owner/repo"]

    with patch("config.cfg", cfg_mock), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await cog.github_issues.callback(cog, inter, repo="owner/repo", label="")

    embed = inter.followup.send.call_args.kwargs["embed"]
    assert "Real Issue" in embed.description
    assert "PR" not in embed.description


@pytest.mark.asyncio
async def test_github_issues_no_repo():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()

    cfg_mock = MagicMock()
    cfg_mock.github_token = None
    cfg_mock.github_default_repos = []

    with patch("config.cfg", cfg_mock):
        await cog.github_issues.callback(cog, inter, repo="", label="")

    assert "No repo" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_github_issues_404():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()

    fake_resp = MagicMock()
    fake_resp.status_code = 404

    cfg_mock = MagicMock()
    cfg_mock.github_token = "tok"
    cfg_mock.github_default_repos = ["owner/repo"]

    with patch("config.cfg", cfg_mock), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await cog.github_issues.callback(cog, inter, repo="owner/repo", label="")

    assert "not found" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_github_issues_generic_exception():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction()

    cfg_mock = MagicMock()
    cfg_mock.github_token = "tok"
    cfg_mock.github_default_repos = ["owner/repo"]

    with patch("config.cfg", cfg_mock), \
         patch("httpx.AsyncClient", side_effect=Exception("boom")):
        await cog.github_issues.callback(cog, inter, repo="owner/repo", label="")

    assert "Failed to fetch" in inter.followup.send.call_args[0][0]


# ── /github watch ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_github_watch_success(tmp_path, monkeypatch):
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction(user_id=42)

    monkeypatch.setattr(mod, "WATCHES_FILE", tmp_path / "watches.json")

    with patch("cogs.github_cog._load_watches", AsyncMock(return_value={})), \
         patch("cogs.github_cog._save_watches", AsyncMock()) as mock_save, \
         patch("cogs.github_cog.require_auth", lambda: lambda f: f):
        await cog.github_watch.callback(cog, inter, repo="owner/repo")

    inter.followup.send.assert_awaited_once()
    assert "Now watching" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_github_watch_already_watching():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction(user_id=42)

    existing = {"42": {"repos": ["owner/repo"], "last_checked": {}}}

    with patch("cogs.github_cog._load_watches", AsyncMock(return_value=existing)), \
         patch("cogs.github_cog._save_watches", AsyncMock()):
        await cog.github_watch.callback(cog, inter, repo="owner/repo")

    assert "already watching" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_github_watch_invalid_format():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction(user_id=42)

    with patch("cogs.github_cog._load_watches", AsyncMock(return_value={})):
        await cog.github_watch.callback(cog, inter, repo="badformat")

    assert "owner/repo" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_github_watch_exception():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction(user_id=42)

    with patch("cogs.github_cog._load_watches", AsyncMock(side_effect=Exception("disk error"))):
        await cog.github_watch.callback(cog, inter, repo="owner/repo")

    assert "Failed to save" in inter.followup.send.call_args[0][0]


# ── /github unwatch ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_github_unwatch_success():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction(user_id=42)

    existing = {"42": {"repos": ["owner/repo"], "last_checked": {"owner/repo": "2024-01-01T00:00:00Z"}}}

    with patch("cogs.github_cog._load_watches", AsyncMock(return_value=existing)), \
         patch("cogs.github_cog._save_watches", AsyncMock()):
        await cog.github_unwatch.callback(cog, inter, repo="owner/repo")

    assert "Stopped watching" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_github_unwatch_not_watching():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction(user_id=42)

    with patch("cogs.github_cog._load_watches", AsyncMock(return_value={})):
        await cog.github_unwatch.callback(cog, inter, repo="owner/repo")

    assert "weren't watching" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_github_unwatch_cleans_up_empty_user():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction(user_id=42)

    existing = {"42": {"repos": ["owner/repo"], "last_checked": {}}}
    saved_data = {}

    async def fake_save(data):
        saved_data.update(data)

    with patch("cogs.github_cog._load_watches", AsyncMock(return_value=existing)), \
         patch("cogs.github_cog._save_watches", AsyncMock(side_effect=fake_save)):
        await cog.github_unwatch.callback(cog, inter, repo="owner/repo")

    # User should be removed entirely from watches
    assert "42" not in saved_data


@pytest.mark.asyncio
async def test_github_unwatch_exception():
    mod = _get_mod()
    cog = _make_cog()
    inter = _make_interaction(user_id=42)

    with patch("cogs.github_cog._load_watches", AsyncMock(side_effect=Exception("disk error"))):
        await cog.github_unwatch.callback(cog, inter, repo="owner/repo")

    assert "Failed to remove" in inter.followup.send.call_args[0][0]


# ── _check_repo_changes ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_repo_changes_first_run_stamps_and_returns_true():
    mod = _get_mod()
    cog = _make_cog()

    now = datetime.now(timezone.utc)
    watches = {"42": {"repos": ["owner/repo"]}}  # no last_checked

    prs_resp = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    issues_resp = MagicMock(status_code=200, json=MagicMock(return_value=[]))

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[prs_resp, issues_resp])

    result = await cog._check_repo_changes(client, "owner/repo", watches, ["42"], now)
    assert result is True
    assert "last_checked" in watches["42"]


@pytest.mark.asyncio
async def test_check_repo_changes_non_200_returns_false():
    mod = _get_mod()
    cog = _make_cog()

    now = datetime.now(timezone.utc)
    watches = {}

    prs_resp = MagicMock(status_code=403)
    issues_resp = MagicMock(status_code=200)

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[prs_resp, issues_resp])

    result = await cog._check_repo_changes(client, "owner/repo", watches, [], now)
    assert result is False


@pytest.mark.asyncio
async def test_check_repo_changes_new_pr_sends_dm():
    mod = _get_mod()
    bot = _make_bot()
    cog = _make_cog(bot)

    now = datetime.now(timezone.utc)
    past = "2024-01-01T00:00:00+00:00"
    watches = {"42": {"repos": ["owner/repo"], "last_checked": {"owner/repo": past}}}

    new_pr = {
        "number": 99,
        "title": "New feature",
        "html_url": "https://github.com/owner/repo/pull/99",
        "user": {"login": "dev"},
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "state": "open",
        "merged_at": None,
    }
    prs_resp = MagicMock(status_code=200, json=MagicMock(return_value=[new_pr]))
    issues_resp = MagicMock(status_code=200, json=MagicMock(return_value=[]))

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[prs_resp, issues_resp])

    fake_user = AsyncMock()
    fake_user.send = AsyncMock()
    bot.fetch_user = AsyncMock(return_value=fake_user)

    result = await cog._check_repo_changes(client, "owner/repo", watches, ["42"], now)
    assert result is True
    fake_user.send.assert_awaited_once()
    assert "opened" in fake_user.send.call_args[0][0]


@pytest.mark.asyncio
async def test_check_repo_changes_closed_pr_sends_dm():
    mod = _get_mod()
    bot = _make_bot()
    cog = _make_cog(bot)

    now = datetime.now(timezone.utc)
    past = "2024-01-01T00:00:00+00:00"
    old_time = "2023-12-01T00:00:00+00:00"
    watches = {"42": {"repos": ["owner/repo"], "last_checked": {"owner/repo": past}}}

    closed_pr = {
        "number": 50,
        "title": "Old PR",
        "html_url": "https://github.com/owner/repo/pull/50",
        "user": {"login": "dev"},
        "created_at": old_time,
        "updated_at": now.isoformat(),
        "state": "closed",
        "merged_at": now.isoformat(),
    }
    prs_resp = MagicMock(status_code=200, json=MagicMock(return_value=[closed_pr]))
    issues_resp = MagicMock(status_code=200, json=MagicMock(return_value=[]))

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[prs_resp, issues_resp])

    fake_user = AsyncMock()
    fake_user.send = AsyncMock()
    bot.fetch_user = AsyncMock(return_value=fake_user)

    result = await cog._check_repo_changes(client, "owner/repo", watches, ["42"], now)
    assert result is True
    assert "merged" in fake_user.send.call_args[0][0]


@pytest.mark.asyncio
async def test_check_repo_changes_new_issue_sends_dm():
    mod = _get_mod()
    bot = _make_bot()
    cog = _make_cog(bot)

    now = datetime.now(timezone.utc)
    past = "2024-01-01T00:00:00+00:00"
    watches = {"42": {"repos": ["owner/repo"], "last_checked": {"owner/repo": past}}}

    new_issue = {
        "number": 200,
        "title": "New bug",
        "html_url": "https://github.com/owner/repo/issues/200",
        "user": {"login": "reporter"},
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "state": "open",
    }
    prs_resp = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    issues_resp = MagicMock(status_code=200, json=MagicMock(return_value=[new_issue]))

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[prs_resp, issues_resp])

    fake_user = AsyncMock()
    fake_user.send = AsyncMock()
    bot.fetch_user = AsyncMock(return_value=fake_user)

    await cog._check_repo_changes(client, "owner/repo", watches, ["42"], now)
    fake_user.send.assert_awaited_once()
    assert "opened" in fake_user.send.call_args[0][0]


@pytest.mark.asyncio
async def test_check_repo_changes_dm_forbidden():
    """Closed DMs should not raise; just log a warning."""
    mod = _get_mod()
    bot = _make_bot()
    cog = _make_cog(bot)

    import discord
    now = datetime.now(timezone.utc)
    past = "2024-01-01T00:00:00+00:00"
    watches = {"42": {"repos": ["owner/repo"], "last_checked": {"owner/repo": past}}}

    new_pr = {
        "number": 1, "title": "PR", "html_url": "...", "user": {"login": "dev"},
        "created_at": now.isoformat(), "updated_at": now.isoformat(),
        "state": "open", "merged_at": None,
    }
    prs_resp = MagicMock(status_code=200, json=MagicMock(return_value=[new_pr]))
    issues_resp = MagicMock(status_code=200, json=MagicMock(return_value=[]))

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[prs_resp, issues_resp])

    fake_user = AsyncMock()
    fake_user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Cannot DM"))
    bot.fetch_user = AsyncMock(return_value=fake_user)

    # Should not raise
    await cog._check_repo_changes(client, "owner/repo", watches, ["42"], now)


# ── _monitor_task ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_monitor_task_no_watches():
    mod = _get_mod()
    cog = _make_cog()

    with patch("cogs.github_cog._load_watches", AsyncMock(return_value={})):
        # Call the underlying coroutine directly via the Loop object
        await mod.GitHubCog._monitor_task.coro(cog)


@pytest.mark.asyncio
async def test_monitor_task_with_watches():
    mod = _get_mod()
    cog = _make_cog()

    watches = {"42": {"repos": ["owner/repo"], "last_checked": {"owner/repo": "2024-01-01T00:00:00Z"}}}

    with patch("cogs.github_cog._load_watches", AsyncMock(return_value=watches)), \
         patch("cogs.github_cog._save_watches", AsyncMock()), \
         patch.object(cog, "_check_repo_changes", AsyncMock(return_value=True)), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await mod.GitHubCog._monitor_task.coro(cog)
