"""Tests for cogs/imdb_cog.py."""

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

import cogs.imdb_cog as mod

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
    return mod.ImdbCog(_FakeBot())


def _mock_cfg(has_key=True):
    cfg = MagicMock()
    cfg.omdb_api_key = "testkey123" if has_key else ""
    return cfg


_SAMPLE_MOVIE = {
    "Response": "True",
    "Title": "The Matrix",
    "Year": "1999",
    "imdbID": "tt0133093",
    "Rated": "R",
    "Runtime": "136 min",
    "Genre": "Action, Sci-Fi",
    "Director": "The Wachowskis",
    "Actors": "Keanu Reeves",
    "imdbRating": "8.7",
    "Plot": "A hacker discovers the truth.",
    "Poster": "N/A",
}

_SAMPLE_SERIES = {
    "Response": "True",
    "Title": "Breaking Bad",
    "Year": "2008–2013",
    "imdbID": "tt0903747",
    "Rated": "TV-MA",
    "Runtime": "45 min",
    "Genre": "Crime, Drama",
    "Director": "N/A",
    "Actors": "Bryan Cranston",
    "imdbRating": "9.5",
    "Plot": "A chemistry teacher turns to crime.",
    "Poster": "N/A",
    "totalSeasons": "5",
}


# ── __init__ ──────────────────────────────────────────────────────────────────


def test_imdb_cog_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── movie ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_movie_no_key():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.imdb_cog.cfg", _mock_cfg(has_key=False)):
        await cog.movie.callback(cog, inter, title="The Matrix")

    inter.followup.send.assert_awaited_once()
    assert "OMDb not configured" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_movie_success():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.imdb_cog.cfg", _mock_cfg()),
        patch("cogs.imdb_cog._omdb_get", new=AsyncMock(return_value=_SAMPLE_MOVIE)),
    ):
        await cog.movie.callback(cog, inter, title="The Matrix")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_movie_not_found():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.imdb_cog.cfg", _mock_cfg()),
        patch(
            "cogs.imdb_cog._omdb_get", new=AsyncMock(return_value={"Response": "False", "Error": "Movie not found!"})
        ),
    ):
        await cog.movie.callback(cog, inter, title="NoSuchFilm12345")

    inter.followup.send.assert_awaited_once()
    assert "No movie found" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_movie_error():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.imdb_cog.cfg", _mock_cfg()),
        patch("cogs.imdb_cog._omdb_get", new=AsyncMock(side_effect=Exception("Network error"))),
    ):
        await cog.movie.callback(cog, inter, title="The Matrix")

    inter.followup.send.assert_awaited_once()
    assert "❌" in inter.followup.send.call_args[0][0]


# ── tv ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tv_no_key():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.imdb_cog.cfg", _mock_cfg(has_key=False)):
        await cog.tv.callback(cog, inter, title="Breaking Bad")

    inter.followup.send.assert_awaited_once()
    assert "OMDb not configured" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_tv_success():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.imdb_cog.cfg", _mock_cfg()),
        patch("cogs.imdb_cog._omdb_get", new=AsyncMock(return_value=_SAMPLE_SERIES)),
    ):
        await cog.tv.callback(cog, inter, title="Breaking Bad")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_tv_not_found():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.imdb_cog.cfg", _mock_cfg()),
        patch("cogs.imdb_cog._omdb_get", new=AsyncMock(return_value={"Response": "False"})),
    ):
        await cog.tv.callback(cog, inter, title="NoSuchShow99999")

    inter.followup.send.assert_awaited_once()
    assert "No TV series found" in inter.followup.send.call_args[0][0]


# ── search ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_no_key():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.imdb_cog.cfg", _mock_cfg(has_key=False)):
        await cog.search.callback(cog, inter, query="matrix")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_success():
    cog = _make_cog()
    inter = _make_interaction()

    search_results = {
        "Search": [
            {"Title": "The Matrix", "Year": "1999", "imdbID": "tt0133093", "Type": "movie"},
            {"Title": "The Matrix Reloaded", "Year": "2003", "imdbID": "tt0234215", "Type": "movie"},
        ]
    }

    with (
        patch("cogs.imdb_cog.cfg", _mock_cfg()),
        patch("cogs.imdb_cog._search_both", new=AsyncMock(return_value=(search_results, {"Search": []}))),
    ):
        await cog.search.callback(cog, inter, query="matrix")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_no_results():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.imdb_cog.cfg", _mock_cfg()),
        patch("cogs.imdb_cog._search_both", new=AsyncMock(return_value=({"Search": []}, {"Search": []}))),
    ):
        await cog.search.callback(cog, inter, query="xyznonexistent999")

    inter.followup.send.assert_awaited_once()
    assert "No results" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_search_error():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.imdb_cog.cfg", _mock_cfg()),
        patch("cogs.imdb_cog._search_both", new=AsyncMock(side_effect=Exception("API down"))),
    ):
        await cog.search.callback(cog, inter, query="matrix")

    inter.followup.send.assert_awaited_once()
    assert "❌" in inter.followup.send.call_args[0][0]
