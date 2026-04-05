"""
Trakt.tv API integration for TV show and movie tracking.

Free API with OAuth2 authentication. Provides trending content,
watchlists, watch history, and integrates with Sonarr/Radarr.

API Docs: https://trakt.docs.apiary.io/
OAuth: https://trakt.tv/oauth/applications
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from config import cfg
from http_session import SessionManager

log = logging.getLogger("openclaw.trakt_skills")
_sessions = SessionManager(timeout=30, name="trakt_skills")

TRAKT_BASE_URL = "https://api.trakt.tv"
TRAKT_API_VERSION = "2"


def _get_headers(include_auth: bool = False) -> dict[str, str]:
    """Get headers for Trakt API requests."""
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": TRAKT_API_VERSION,
        "trakt-api-key": cfg.trakt_client_id,
    }

    if include_auth and cfg.trakt_access_token:
        headers["Authorization"] = f"Bearer {cfg.trakt_access_token}"

    return headers


async def get_trending_shows(limit: int = 10, extended: bool = True) -> dict[str, Any]:
    """
    Get trending TV shows on Trakt.

    Args:
        limit: Number of shows to return (1-100, default: 10)
        extended: Include extended info (ratings, runtime, etc.)

    Returns:
        {
            "status": "success",
            "count": 10,
            "shows": [
                {
                    "watchers": 1234,
                    "show": {
                        "title": "The Last of Us",
                        "year": 2023,
                        "ids": {"trakt": 123456, "imdb": "tt1234567"},
                        "overview": "...",
                        "rating": 8.9,
                        "network": "HBO"
                    }
                },
                ...
            ]
        }

    Free tier: Unlimited with OAuth2
    """
    if not cfg.trakt_client_id:
        return {
            "status": "error",
            "message": "Trakt.tv client ID not configured. Set TRAKT_CLIENT_ID in .env",
        }

    try:
        params = {"limit": min(limit, 100)}
        if extended:
            params["extended"] = "full"

        async with _sessions.get() as session:
            url = f"{TRAKT_BASE_URL}/shows/trending"
            async with session.get(url, headers=_get_headers(), params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "status": "success",
                        "count": len(data),
                        "shows": data,
                        "updated_at": datetime.utcnow().isoformat(),
                    }
                else:
                    error_text = await resp.text()
                    return {
                        "status": "error",
                        "message": f"Trakt API error: {resp.status}",
                        "details": error_text,
                    }

    except Exception as e:
        log.error("Error fetching trending shows: %s", e)
        return {"status": "error", "message": str(e)}


async def get_trending_movies(limit: int = 10, extended: bool = True) -> dict[str, Any]:
    """
    Get trending movies on Trakt.

    Args:
        limit: Number of movies to return (1-100, default: 10)
        extended: Include extended info (ratings, runtime, etc.)

    Returns:
        {
            "status": "success",
            "count": 10,
            "movies": [
                {
                    "watchers": 5678,
                    "movie": {
                        "title": "Avatar: The Way of Water",
                        "year": 2022,
                        "ids": {"trakt": 654321, "imdb": "tt7654321"},
                        "overview": "...",
                        "rating": 8.1,
                        "runtime": 192
                    }
                },
                ...
            ]
        }

    Free tier: Unlimited with OAuth2
    """
    if not cfg.trakt_client_id:
        return {
            "status": "error",
            "message": "Trakt.tv client ID not configured. Set TRAKT_CLIENT_ID in .env",
        }

    try:
        params = {"limit": min(limit, 100)}
        if extended:
            params["extended"] = "full"

        async with _sessions.get() as session:
            url = f"{TRAKT_BASE_URL}/movies/trending"
            async with session.get(url, headers=_get_headers(), params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "status": "success",
                        "count": len(data),
                        "movies": data,
                        "updated_at": datetime.utcnow().isoformat(),
                    }
                else:
                    error_text = await resp.text()
                    return {
                        "status": "error",
                        "message": f"Trakt API error: {resp.status}",
                        "details": error_text,
                    }

    except Exception as e:
        log.error("Error fetching trending movies: %s", e)
        return {"status": "error", "message": str(e)}


async def sync_watchlist(user_id: str = "me", media_type: str = "all") -> dict[str, Any]:
    """
    Sync user's Trakt watchlist.

    Args:
        user_id: Trakt user ID or "me" for authenticated user
        media_type: Filter by type: "all", "shows", "movies", "seasons", "episodes"

    Returns:
        {
            "status": "success",
            "count": 15,
            "watchlist": [
                {
                    "rank": 1,
                    "listed_at": "2024-01-15T12:00:00Z",
                    "type": "show",
                    "show": {
                        "title": "Breaking Bad",
                        "year": 2008,
                        "ids": {...}
                    }
                },
                ...
            ]
        }

    Requires: OAuth2 access token (TRAKT_ACCESS_TOKEN)
    """
    if not cfg.trakt_access_token:
        return {
            "status": "error",
            "message": "Trakt.tv access token not configured. Run OAuth2 flow first.",
        }

    try:
        async with _sessions.get() as session:
            url = f"{TRAKT_BASE_URL}/users/{user_id}/watchlist/{media_type}/added"
            async with session.get(url, headers=_get_headers(include_auth=True)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "status": "success",
                        "count": len(data),
                        "watchlist": data,
                        "updated_at": datetime.utcnow().isoformat(),
                    }
                elif resp.status == 401:
                    return {
                        "status": "error",
                        "message": "Unauthorized. Access token may be expired. Re-authenticate.",
                    }
                else:
                    error_text = await resp.text()
                    return {
                        "status": "error",
                        "message": f"Trakt API error: {resp.status}",
                        "details": error_text,
                    }

    except Exception as e:
        log.error("Error syncing watchlist: %s", e)
        return {"status": "error", "message": str(e)}


async def get_watch_history(
    user_id: str = "me",
    media_type: str = "all",
    limit: int = 50
) -> dict[str, Any]:
    """
    Get user's watch history from Trakt.

    Args:
        user_id: Trakt user ID or "me" for authenticated user
        media_type: Filter by type: "all", "shows", "movies", "seasons", "episodes"
        limit: Number of items to return (default: 50)

    Returns:
        {
            "status": "success",
            "count": 25,
            "history": [
                {
                    "id": 3373536619,
                    "watched_at": "2024-01-15T20:30:00Z",
                    "action": "watch",
                    "type": "episode",
                    "episode": {
                        "season": 1,
                        "number": 5,
                        "title": "Pilot",
                        "ids": {...}
                    },
                    "show": {
                        "title": "Breaking Bad",
                        "year": 2008,
                        "ids": {...}
                    }
                },
                ...
            ]
        }

    Requires: OAuth2 access token (TRAKT_ACCESS_TOKEN)
    """
    if not cfg.trakt_access_token:
        return {
            "status": "error",
            "message": "Trakt.tv access token not configured. Run OAuth2 flow first.",
        }

    try:
        params = {"limit": limit}

        async with _sessions.get() as session:
            url = f"{TRAKT_BASE_URL}/users/{user_id}/history/{media_type}"
            async with session.get(
                url,
                headers=_get_headers(include_auth=True),
                params=params
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "status": "success",
                        "count": len(data),
                        "history": data,
                        "updated_at": datetime.utcnow().isoformat(),
                    }
                elif resp.status == 401:
                    return {
                        "status": "error",
                        "message": "Unauthorized. Access token may be expired. Re-authenticate.",
                    }
                else:
                    error_text = await resp.text()
                    return {
                        "status": "error",
                        "message": f"Trakt API error: {resp.status}",
                        "details": error_text,
                    }

    except Exception as e:
        log.error("Error fetching watch history: %s", e)
        return {"status": "error", "message": str(e)}


async def add_to_watchlist(
    item_id: str,
    item_type: str = "movie",
    id_type: str = "trakt"
) -> dict[str, Any]:
    """
    Add item to user's Trakt watchlist.

    Args:
        item_id: ID of the item (Trakt ID, IMDb ID, etc.)
        item_type: Type of item: "movie", "show", "season", "episode"
        id_type: ID type: "trakt", "imdb", "tmdb", "tvdb"

    Returns:
        {
            "status": "success",
            "added": {
                "movies": 1,
                "shows": 0
            },
            "not_found": {
                "movies": [],
                "shows": []
            }
        }

    Requires: OAuth2 access token (TRAKT_ACCESS_TOKEN)
    """
    if not cfg.trakt_access_token:
        return {
            "status": "error",
            "message": "Trakt.tv access token not configured. Run OAuth2 flow first.",
        }

    try:
        # Build request payload
        payload = {
            f"{item_type}s": [
                {
                    "ids": {id_type: item_id}
                }
            ]
        }

        async with _sessions.get() as session:
            url = f"{TRAKT_BASE_URL}/sync/watchlist"
            async with session.post(
                url,
                headers=_get_headers(include_auth=True),
                json=payload
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    return {
                        "status": "success",
                        "added": data.get("added", {}),
                        "not_found": data.get("not_found", {}),
                    }
                elif resp.status == 401:
                    return {
                        "status": "error",
                        "message": "Unauthorized. Access token may be expired. Re-authenticate.",
                    }
                else:
                    error_text = await resp.text()
                    return {
                        "status": "error",
                        "message": f"Trakt API error: {resp.status}",
                        "details": error_text,
                    }

    except Exception as e:
        log.error("Error adding to watchlist: %s", e)
        return {"status": "error", "message": str(e)}


async def search_trakt(
    query: str,
    search_type: str = "multi",
    limit: int = 10
) -> dict[str, Any]:
    """
    Search Trakt for shows, movies, or people.

    Args:
        query: Search query
        search_type: Type to search: "multi", "movie", "show", "person"
        limit: Number of results (default: 10)

    Returns:
        {
            "status": "success",
            "count": 5,
            "results": [
                {
                    "type": "show",
                    "score": 1000.0,
                    "show": {
                        "title": "Breaking Bad",
                        "year": 2008,
                        "ids": {...}
                    }
                },
                ...
            ]
        }

    Free tier: Unlimited
    """
    if not cfg.trakt_client_id:
        return {
            "status": "error",
            "message": "Trakt.tv client ID not configured. Set TRAKT_CLIENT_ID in .env",
        }

    try:
        params = {
            "query": query,
            "limit": limit,
        }

        async with _sessions.get() as session:
            url = f"{TRAKT_BASE_URL}/search/{search_type}"
            async with session.get(url, headers=_get_headers(), params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "status": "success",
                        "count": len(data),
                        "results": data,
                    }
                else:
                    error_text = await resp.text()
                    return {
                        "status": "error",
                        "message": f"Trakt API error: {resp.status}",
                        "details": error_text,
                    }

    except Exception as e:
        log.error("Error searching Trakt: %s", e)
        return {"status": "error", "message": str(e)}


def get_oauth_url() -> str:
    """
    Generate Trakt OAuth2 authorization URL.

    Returns:
        Authorization URL for user to visit and grant access.
    """
    if not cfg.trakt_client_id:
        return "Error: TRAKT_CLIENT_ID not configured"

    redirect_uri = "urn:ietf:wg:oauth:2.0:oob"  # Out-of-band for CLI apps
    return (
        f"https://trakt.tv/oauth/authorize?"
        f"response_type=code&"
        f"client_id={cfg.trakt_client_id}&"
        f"redirect_uri={redirect_uri}"
    )


# Skill metadata for registration
TRAKT_SKILLS = {
    "get_trending_shows": get_trending_shows,
    "get_trending_movies": get_trending_movies,
    "sync_watchlist": sync_watchlist,
    "get_watch_history": get_watch_history,
    "add_to_watchlist": add_to_watchlist,
    "search_trakt": search_trakt,
}
