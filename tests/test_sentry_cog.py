"""Tests for cogs/sentry_cog.py."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/t_cogs.db")

import pytest

import cog_helpers as _ch

_orig_require_auth = _ch.require_auth


def _noop_auth(*args, **kwargs):
    if args and callable(args[0]):
        return args[0]
    return lambda f: f


_ch.require_auth = _noop_auth

import cogs.sentry_cog as mod

_ch.require_auth = _orig_require_auth


class _FakeTree:
    def add_command(self, *a, **k):
        pass

    def remove_command(self, *a, **k):
        pass


class _FakeBot:
    def __init__(self):
        self.tree = _FakeTree()


def _make_interaction(user_id=1, done=False):
    inter = AsyncMock()
    inter.user.id = user_id
    inter.user.display_name = "TestUser"
    inter.user.__str__ = lambda self: "TestUser#0001"
    inter.channel_id = 100
    inter.guild_id = 999
    inter.response.send_message = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.response.is_done = MagicMock(return_value=done)
    inter.followup.send = AsyncMock()
    return inter


def _make_cog():
    return mod.SentryCog(_FakeBot())


def _mock_cfg(has_token=True):
    cfg = MagicMock()
    cfg.sentry_auth_token = "token123" if has_token else ""
    cfg.sentry_org = "myorg"
    cfg.sentry_url = "https://sentry.io"
    return cfg


_SAMPLE_ISSUES = [
    {
        "id": "1",
        "title": "NullPointerException",
        "level": "error",
        "count": "42",
        "lastSeen": "2024-01-01T10:00:00Z",
    }
]


# ── __init__ ──────────────────────────────────────────────────────────────────


def test_sentry_cog_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── sentry_issues ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sentry_issues_no_token():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.sentry_cog.cfg", _mock_cfg(has_token=False)):
        await cog.sentry_issues.callback(cog, inter, project="")

    inter.followup.send.assert_awaited_once()
    assert "not configured" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_sentry_issues_org_wide():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.sentry_cog.cfg", _mock_cfg()),
        patch("cogs.sentry_cog._sentry", new=AsyncMock(return_value=_SAMPLE_ISSUES)),
    ):
        await cog.sentry_issues.callback(cog, inter, project="")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_sentry_issues_by_project():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.sentry_cog.cfg", _mock_cfg()),
        patch("cogs.sentry_cog._sentry", new=AsyncMock(return_value=_SAMPLE_ISSUES)),
    ):
        await cog.sentry_issues.callback(cog, inter, project="my-app")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_sentry_issues_empty():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.sentry_cog.cfg", _mock_cfg()), patch("cogs.sentry_cog._sentry", new=AsyncMock(return_value=[])):
        await cog.sentry_issues.callback(cog, inter, project="")

    inter.followup.send.assert_awaited_once()
    assert "No unresolved" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_sentry_issues_error():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.sentry_cog.cfg", _mock_cfg()),
        patch("cogs.sentry_cog._sentry", new=AsyncMock(side_effect=Exception("Network error"))),
    ):
        await cog.sentry_issues.callback(cog, inter, project="")

    inter.followup.send.assert_awaited_once()
    assert "❌" in inter.followup.send.call_args[0][0]


# ── sentry_projects ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sentry_projects_no_token():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.sentry_cog.cfg", _mock_cfg(has_token=False)):
        await cog.sentry_projects.callback(cog, inter)

    inter.followup.send.assert_awaited_once()
    assert "not configured" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_sentry_projects_success():
    cog = _make_cog()
    inter = _make_interaction()

    projects = [{"slug": "my-app", "name": "My App", "platform": "python"}]
    with (
        patch("cogs.sentry_cog.cfg", _mock_cfg()),
        patch("cogs.sentry_cog._sentry", new=AsyncMock(return_value=projects)),
    ):
        await cog.sentry_projects.callback(cog, inter)

    inter.followup.send.assert_awaited_once()


# ── sentry_resolve ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sentry_resolve_no_token():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.sentry_cog.cfg", _mock_cfg(has_token=False)):
        await cog.sentry_resolve.callback(cog, inter, issue_id="123")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_sentry_resolve_success():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.sentry_cog.cfg", _mock_cfg()),
        patch("cogs.sentry_cog._sentry", new=AsyncMock(return_value={"status": "resolved"})),
    ):
        await cog.sentry_resolve.callback(cog, inter, issue_id="456")

    inter.followup.send.assert_awaited_once()


# ── sentry_stats ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sentry_stats_no_token():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.sentry_cog.cfg", _mock_cfg(has_token=False)):
        await cog.sentry_stats.callback(cog, inter, project="")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_sentry_stats_success():
    cog = _make_cog()
    inter = _make_interaction()

    # Stats API returns list of [timestamp_seconds, count] pairs
    import time

    now = int(time.time())
    stats = [[now - (i * 3600), i * 5] for i in range(24)]
    with patch("cogs.sentry_cog.cfg", _mock_cfg()), patch("cogs.sentry_cog._sentry", new=AsyncMock(return_value=stats)):
        await cog.sentry_stats.callback(cog, inter, project="my-app")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_sentry_stats_no_project_fallback():
    cog = _make_cog()
    inter = _make_interaction()

    import time

    now = int(time.time())
    stats = [[now - (i * 3600), i * 5] for i in range(24)]
    projects = [{"slug": "my-app", "name": "My App"}]

    call_count = [0]

    async def _mock_sentry(method, path, **kwargs):
        call_count[0] += 1
        if "projects" in path and "/" not in path.replace("organizations/myorg/projects/", ""):
            return projects
        return stats

    with patch("cogs.sentry_cog.cfg", _mock_cfg()), patch("cogs.sentry_cog._sentry", side_effect=_mock_sentry):
        await cog.sentry_stats.callback(cog, inter, project="")

    inter.followup.send.assert_awaited_once()
