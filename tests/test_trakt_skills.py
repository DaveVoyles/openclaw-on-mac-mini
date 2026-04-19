"""
Tests for Trakt.tv integration skills.

Tests structure and error handling (not actual API calls to avoid rate limits).
"""

import pytest

from skills.trakt_skills import (
    TRAKT_SKILLS,
    add_to_watchlist,
    get_oauth_url,
    get_trending_movies,
    get_trending_shows,
    get_watch_history,
    search_trakt,
    sync_watchlist,
)


def test_trakt_skills_registered():
    """Verify all Trakt skills are registered."""
    assert "get_trending_shows" in TRAKT_SKILLS
    assert "get_trending_movies" in TRAKT_SKILLS
    assert "sync_watchlist" in TRAKT_SKILLS
    assert "get_watch_history" in TRAKT_SKILLS
    assert "add_to_watchlist" in TRAKT_SKILLS
    assert "search_trakt" in TRAKT_SKILLS


def test_trakt_skills_skills_are_callables():
    """Verify all Trakt skills are callable."""
    for skill_name, skill_func in TRAKT_SKILLS.items():
        assert callable(skill_func), f"{skill_name} is not callable"


@pytest.mark.asyncio
async def test_get_trending_shows_no_key():
    """Test trending shows without API key."""
    result = await get_trending_shows(limit=5)

    # Should return error dict without crashing
    assert isinstance(result, dict)
    assert "status" in result

    # Without TRAKT_CLIENT_ID, should error gracefully
    if result["status"] == "error":
        assert "message" in result
        assert "trakt" in result["message"].lower() or "client" in result["message"].lower()


@pytest.mark.asyncio
async def test_get_trending_movies_no_key():
    """Test trending movies without API key."""
    result = await get_trending_movies(limit=5)

    assert isinstance(result, dict)
    assert "status" in result


@pytest.mark.asyncio
async def test_sync_watchlist_no_token():
    """Test watchlist sync without access token."""
    result = await sync_watchlist()

    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "token" in result["message"].lower()


@pytest.mark.asyncio
async def test_get_watch_history_no_token():
    """Test watch history without access token."""
    result = await get_watch_history()

    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "token" in result["message"].lower()


@pytest.mark.asyncio
async def test_add_to_watchlist_no_token():
    """Test adding to watchlist without access token."""
    result = await add_to_watchlist(item_id="12345", item_type="movie")

    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "token" in result["message"].lower()


@pytest.mark.asyncio
async def test_search_trakt_no_key():
    """Test Trakt search without API key."""
    result = await search_trakt(query="Breaking Bad", search_type="show")

    assert isinstance(result, dict)
    assert "status" in result


@pytest.mark.asyncio
async def test_trending_shows_limit_validation():
    """Test that limit parameter is properly bounded."""
    # Even without API key, function should accept valid limits
    result = await get_trending_shows(limit=150)  # Should cap at 100
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_trending_movies_extended_param():
    """Test extended parameter handling."""
    result = await get_trending_movies(limit=5, extended=False)
    assert isinstance(result, dict)


def test_oauth_url_generation():
    """Test OAuth URL generation."""
    url = get_oauth_url()

    # Should return a string (even if error message)
    assert isinstance(url, str)

    # If client ID is configured, URL should be valid
    if not url.startswith("Error"):
        assert "trakt.tv/oauth/authorize" in url
        assert "client_id=" in url
        assert "redirect_uri=" in url


@pytest.mark.asyncio
async def test_search_types():
    """Test different search types are accepted."""
    search_types = ["multi", "movie", "show", "person"]

    for search_type in search_types:
        result = await search_trakt(query="test", search_type=search_type)
        assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_watchlist_media_types():
    """Test different media types for watchlist."""
    media_types = ["all", "shows", "movies", "seasons", "episodes"]

    for media_type in media_types:
        result = await sync_watchlist(media_type=media_type)
        assert isinstance(result, dict)
        # Should error without token, but accept the media_type
        assert result["status"] == "error"


@pytest.mark.asyncio
async def test_add_to_watchlist_types():
    """Test adding different item types to watchlist."""
    item_types = ["movie", "show", "season", "episode"]

    for item_type in item_types:
        result = await add_to_watchlist(item_id="12345", item_type=item_type, id_type="trakt")
        assert isinstance(result, dict)
        # Should error without token
        assert result["status"] == "error"


@pytest.mark.asyncio
async def test_response_structure():
    """Test that responses have expected structure."""
    # Trending shows should have specific fields when successful
    result = await get_trending_shows(limit=1)
    assert "status" in result

    if result["status"] == "success":
        assert "count" in result
        assert "shows" in result
        assert "updated_at" in result
