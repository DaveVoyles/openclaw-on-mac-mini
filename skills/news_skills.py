"""
News aggregation skills using NewsAPI.org

Free tier: 100 requests/day
Rate limit handled via tool circuit breaker
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from config import cfg
from decorators import retry_on_error
from http_session import SessionManager
from tool_health import tool_health

_sessions = SessionManager(timeout=30, name="news_skills")

NEWS_API_BASE_URL = "https://newsapi.org/v2"
NEWS_CACHE_TTL = 3600  # 1 hour cache for free tier


@retry_on_error(max_retries=2, delay=1.0, backoff=2.0, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
async def search_news(
    query: str,
    from_date: str | None = None,
    to_date: str | None = None,
    language: str = "en",
    sort_by: str = "relevancy",
    page_size: int = 10,
) -> dict[str, Any]:
    """
    Search news articles matching a query.

    Args:
        query: Keywords or phrases to search for
        from_date: Start date (YYYY-MM-DD format)
        to_date: End date (YYYY-MM-DD format)
        language: 2-letter ISO-639-1 code (default: en)
        sort_by: relevancy, popularity, or publishedAt
        page_size: Number of results (max 100)

    Returns:
        {
            "status": "ok",
            "totalResults": 123,
            "articles": [
                {
                    "title": "...",
                    "description": "...",
                    "url": "...",
                    "publishedAt": "2024-01-15T10:30:00Z",
                    "source": {"name": "CNN"},
                    "author": "John Doe"
                },
                ...
            ]
        }

    Free tier limit: 100 requests/day
    """
    if not cfg.newsapi_key:
        return {
            "status": "error",
            "message": "NEWSAPI_KEY not configured",
            "articles": [],
        }

    # Default to last 7 days if no dates specified
    if not from_date:
        from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    params = {
        "q": query,
        "language": language,
        "sortBy": sort_by,
        "pageSize": min(page_size, 100),
        "apiKey": cfg.newsapi_key,
    }

    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    url = f"{NEWS_API_BASE_URL}/everything"

    session = await _sessions.get()
    async with session.get(url, params=params, timeout=30) as resp:
        if resp.status == 429:
            # Rate limit hit
            tool_health.record("newsapi", success=False)
            return {
                "status": "error",
                "message": "NewsAPI rate limit exceeded. Free tier: 100 requests/day.",
                "articles": [],
            }

        if resp.status != 200:
            error_text = await resp.text()
            tool_health.record("newsapi", success=False)
            return {
                "status": "error",
                "message": f"NewsAPI error: {error_text}",
                "articles": [],
            }

        data = await resp.json()
        tool_health.record("newsapi", success=True)
        return data


@retry_on_error(max_retries=2, delay=1.0, backoff=2.0, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
async def top_headlines(
    category: str | None = None,
    country: str = "us",
    query: str | None = None,
    page_size: int = 10,
) -> dict[str, Any]:
    """
    Get top headlines from NewsAPI.

    Args:
        category: business, entertainment, general, health, science, sports, technology
        country: 2-letter ISO 3166-1 code (us, gb, au, etc.)
        query: Keywords to search within headlines
        page_size: Number of results (max 100)

    Returns:
        Same format as search_news()

    Free tier limit: 100 requests/day
    """
    if not cfg.newsapi_key:
        return {
            "status": "error",
            "message": "NEWSAPI_KEY not configured",
            "articles": [],
        }

    params = {
        "country": country,
        "pageSize": min(page_size, 100),
        "apiKey": cfg.newsapi_key,
    }

    if category:
        valid_categories = ["business", "entertainment", "general", "health", "science", "sports", "technology"]
        if category not in valid_categories:
            return {
                "status": "error",
                "message": f"Invalid category. Must be one of: {', '.join(valid_categories)}",
                "articles": [],
            }
        params["category"] = category

    if query:
        params["q"] = query

    url = f"{NEWS_API_BASE_URL}/top-headlines"

    session = await _sessions.get()
    async with session.get(url, params=params, timeout=30) as resp:
        if resp.status == 429:
            tool_health.record("newsapi", success=False)
            return {
                "status": "error",
                "message": "NewsAPI rate limit exceeded. Free tier: 100 requests/day.",
                "articles": [],
            }

        if resp.status != 200:
            error_text = await resp.text()
            tool_health.record("newsapi", success=False)
            return {
                "status": "error",
                "message": f"NewsAPI error: {error_text}",
                "articles": [],
            }

        data = await resp.json()
        tool_health.record("newsapi", success=True)
        return data


@retry_on_error(max_retries=2, delay=1.0, backoff=2.0, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
async def news_by_source(
    source_id: str,
    query: str | None = None,
    from_date: str | None = None,
    page_size: int = 10,
) -> dict[str, Any]:
    """
    Get news from a specific source.

    Args:
        source_id: Source identifier (e.g., "cnn", "bbc-news", "techcrunch")
        query: Optional search query within this source
        from_date: Start date (YYYY-MM-DD)
        page_size: Number of results (max 100)

    Returns:
        Same format as search_news()

    Popular sources:
        - cnn, bbc-news, the-verge, techcrunch, wired
        - ars-technica, hacker-news, reddit-r-all
        - the-wall-street-journal, bloomberg, fortune

    Free tier limit: 100 requests/day
    """
    if not cfg.newsapi_key:
        return {
            "status": "error",
            "message": "NEWSAPI_KEY not configured",
            "articles": [],
        }

    params = {
        "sources": source_id,
        "pageSize": min(page_size, 100),
        "apiKey": cfg.newsapi_key,
    }

    if query:
        params["q"] = query
    if from_date:
        params["from"] = from_date

    url = f"{NEWS_API_BASE_URL}/everything"

    session = await _sessions.get()
    async with session.get(url, params=params, timeout=30) as resp:
        if resp.status == 429:
            tool_health.record("newsapi", success=False)
            return {
                "status": "error",
                "message": "NewsAPI rate limit exceeded. Free tier: 100 requests/day.",
                "articles": [],
            }

        if resp.status != 200:
            error_text = await resp.text()
            tool_health.record("newsapi", success=False)
            return {
                "status": "error",
                "message": f"NewsAPI error: {error_text}",
                "articles": [],
            }

        data = await resp.json()
        tool_health.record("newsapi", success=True)
        return data


# LLM-callable skill definitions
NEWS_SKILLS = [
    {
        "name": "search_news",
        "description": "Search news articles by keywords. Great for 'What's happening with X?' queries. Free tier: 100 req/day.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords or phrases to search (e.g., 'artificial intelligence', 'box office', 'NBA playoffs')",
                },
                "from_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format (default: 7 days ago)",
                },
                "to_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format (default: today)",
                },
                "language": {
                    "type": "string",
                    "description": "Language code (en, es, fr, de, etc.)",
                    "default": "en",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["relevancy", "popularity", "publishedAt"],
                    "description": "How to sort results",
                    "default": "relevancy",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of results (1-100)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
        "function": search_news,
    },
    {
        "name": "top_headlines",
        "description": "Get breaking news and top headlines by category or country. Use for 'what's trending?' queries. Free tier: 100 req/day.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["business", "entertainment", "general", "health", "science", "sports", "technology"],
                    "description": "News category",
                },
                "country": {
                    "type": "string",
                    "description": "2-letter country code (us, gb, au, ca, etc.)",
                    "default": "us",
                },
                "query": {
                    "type": "string",
                    "description": "Optional search keywords within headlines",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of results (1-100)",
                    "default": 10,
                },
            },
            "required": [],
        },
        "function": top_headlines,
    },
    {
        "name": "news_by_source",
        "description": "Get news from a specific publication (CNN, BBC, TechCrunch, etc.). Free tier: 100 req/day.",
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "Source ID (cnn, bbc-news, techcrunch, the-verge, wired, bloomberg, etc.)",
                },
                "query": {
                    "type": "string",
                    "description": "Optional search query within this source",
                },
                "from_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of results (1-100)",
                    "default": 10,
                },
            },
            "required": ["source_id"],
        },
        "function": news_by_source,
    },
]
