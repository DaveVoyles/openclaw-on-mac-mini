"""
Polygon.io financial data skills

Free tier: 5 API calls/minute
Covers: real-time stock quotes, market status, historical data, market movers
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from config import cfg
from http_session import SessionManager
from tool_health import tool_health

_sessions = SessionManager(timeout=30, name="polygon_skills")

POLYGON_BASE_URL = "https://api.polygon.io"

# Circuit breaker state
_circuit_breaker = {
    "failures": 0,
    "last_failure": None,
    "is_open": False,
}

# Simple in-memory cache
_cache: dict[str, tuple[Any, datetime]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


def _get_cached(key: str) -> Any | None:
    """Get cached value if still valid."""
    if key in _cache:
        value, timestamp = _cache[key]
        if datetime.now() - timestamp < timedelta(seconds=CACHE_TTL_SECONDS):
            return value
        del _cache[key]
    return None


def _set_cache(key: str, value: Any) -> None:
    """Cache a value with current timestamp."""
    _cache[key] = (value, datetime.now())


def _check_circuit_breaker() -> dict[str, Any] | None:
    """Check if circuit breaker is open (too many recent failures)."""
    if _circuit_breaker["is_open"]:
        if _circuit_breaker["last_failure"]:
            elapsed = (datetime.now() - _circuit_breaker["last_failure"]).seconds
            if elapsed > 60:  # Reset after 60 seconds
                _circuit_breaker["is_open"] = False
                _circuit_breaker["failures"] = 0
            else:
                return {
                    "status": "error",
                    "message": "Polygon.io temporarily unavailable (circuit breaker open). Try again in a minute.",
                }
    return None


def _record_failure() -> None:
    """Record API failure and potentially open circuit breaker."""
    _circuit_breaker["failures"] += 1
    _circuit_breaker["last_failure"] = datetime.now()
    if _circuit_breaker["failures"] >= 3:
        _circuit_breaker["is_open"] = True


def _record_success() -> None:
    """Record successful API call."""
    _circuit_breaker["failures"] = max(0, _circuit_breaker["failures"] - 1)


async def get_stock_quote(ticker: str) -> dict[str, Any]:
    """
    Get real-time stock quote for a ticker symbol.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL", "TSLA", "MSFT")

    Returns:
        {
            "status": "ok",
            "ticker": "AAPL",
            "price": 175.43,
            "change": 2.34,
            "change_percent": 1.35,
            "volume": 82345678,
            "open": 173.50,
            "high": 176.20,
            "low": 172.80,
            "previous_close": 173.09,
            "updated": "2024-01-15T16:00:00Z"
        }

    Free tier: 5 calls/minute
    """
    if not cfg.polygon_api_key:
        return {
            "status": "error",
            "message": "POLYGON_API_KEY not configured. Get free key at polygon.io",
        }

    # Check circuit breaker
    breaker_status = _check_circuit_breaker()
    if breaker_status:
        return breaker_status

    # Check cache
    cache_key = f"quote:{ticker.upper()}"
    cached = _get_cached(cache_key)
    if cached:
        cached["cached"] = True
        return cached

    ticker = ticker.upper()
    url = f"{POLYGON_BASE_URL}/v2/aggs/ticker/{ticker}/prev"

    params = {"apiKey": cfg.polygon_api_key, "adjusted": "true"}

    try:
        session = await _sessions.get()
        async with session.get(url, params=params, timeout=30) as resp:
            if resp.status == 429:
                _record_failure()
                tool_health.record("polygon", success=False)
                return {
                    "status": "error",
                    "message": "Polygon.io rate limit exceeded. Free tier: 5 calls/minute.",
                }

            if resp.status == 403:
                _record_failure()
                tool_health.record("polygon", success=False)
                return {
                    "status": "error",
                    "message": "Polygon.io API key invalid or expired. Check POLYGON_API_KEY.",
                }

            if resp.status != 200:
                error_text = await resp.text()
                _record_failure()
                tool_health.record("polygon", success=False)
                return {
                    "status": "error",
                    "message": f"Polygon.io error: {error_text}",
                }

            data = await resp.json()

            if not data.get("results"):
                return {
                    "status": "error",
                    "message": f"No data found for ticker: {ticker}",
                }

            result = data["results"][0]
            _record_success()
            tool_health.record("polygon", success=True)

            response = {
                "status": "ok",
                "ticker": ticker,
                "price": result.get("c", 0),  # Close price
                "open": result.get("o", 0),
                "high": result.get("h", 0),
                "low": result.get("l", 0),
                "volume": result.get("v", 0),
                "previous_close": result.get("c", 0),
                "change": round(result.get("c", 0) - result.get("o", 0), 2),
                "change_percent": round(
                    ((result.get("c", 0) - result.get("o", 0)) / result.get("o", 1)) * 100, 2
                ),
                "updated": datetime.fromtimestamp(result.get("t", 0) / 1000).isoformat(),
            }

            # Cache the response
            _set_cache(cache_key, response)
            return response

    except asyncio.TimeoutError:
        _record_failure()
        tool_health.record("polygon", success=False)
        return {
            "status": "error",
            "message": "Polygon.io request timed out",
        }
    except Exception as e:
        _record_failure()
        tool_health.record("polygon", success=False)
        return {
            "status": "error",
            "message": f"Polygon.io error: {str(e)}",
        }


async def get_market_status() -> dict[str, Any]:
    """
    Get current market status (open/closed) and trading hours.

    Returns:
        {
            "status": "ok",
            "market": "open" | "closed" | "extended-hours",
            "exchanges": {
                "nyse": "open",
                "nasdaq": "open"
            },
            "next_open": "2024-01-16T09:30:00-05:00",
            "next_close": "2024-01-15T16:00:00-05:00"
        }

    Free tier: 5 calls/minute
    """
    if not cfg.polygon_api_key:
        return {
            "status": "error",
            "message": "POLYGON_API_KEY not configured",
        }

    # Check circuit breaker
    breaker_status = _check_circuit_breaker()
    if breaker_status:
        return breaker_status

    # Check cache
    cache_key = "market_status"
    cached = _get_cached(cache_key)
    if cached:
        cached["cached"] = True
        return cached

    url = f"{POLYGON_BASE_URL}/v1/marketstatus/now"
    params = {"apiKey": cfg.polygon_api_key}

    try:
        session = await _sessions.get()
        async with session.get(url, params=params, timeout=30) as resp:
            if resp.status == 429:
                _record_failure()
                tool_health.record("polygon", success=False)
                return {
                    "status": "error",
                    "message": "Polygon.io rate limit exceeded. Free tier: 5 calls/minute.",
                }

            if resp.status != 200:
                error_text = await resp.text()
                _record_failure()
                tool_health.record("polygon", success=False)
                return {
                    "status": "error",
                    "message": f"Polygon.io error: {error_text}",
                }

            data = await resp.json()
            _record_success()
            tool_health.record("polygon", success=True)

            response = {
                "status": "ok",
                "market": data.get("market", "unknown"),
                "server_time": data.get("serverTime", ""),
                "exchanges": {
                    "nyse": data.get("exchanges", {}).get("nyse", "unknown"),
                    "nasdaq": data.get("exchanges", {}).get("nasdaq", "unknown"),
                    "otc": data.get("exchanges", {}).get("otc", "unknown"),
                },
                "currencies": {
                    "fx": data.get("currencies", {}).get("fx", "unknown"),
                    "crypto": data.get("currencies", {}).get("crypto", "unknown"),
                },
            }

            # Cache for 1 minute
            _set_cache(cache_key, response)
            return response

    except asyncio.TimeoutError:
        _record_failure()
        tool_health.record("polygon", success=False)
        return {
            "status": "error",
            "message": "Polygon.io request timed out",
        }
    except Exception as e:
        _record_failure()
        tool_health.record("polygon", success=False)
        return {
            "status": "error",
            "message": f"Polygon.io error: {str(e)}",
        }


async def get_stock_history(ticker: str, days: int = 30) -> dict[str, Any]:
    """
    Get historical stock data for a ticker.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        days: Number of days of history (default: 30)

    Returns:
        {
            "status": "ok",
            "ticker": "AAPL",
            "days": 30,
            "data": [
                {
                    "date": "2024-01-15",
                    "open": 173.50,
                    "high": 176.20,
                    "low": 172.80,
                    "close": 175.43,
                    "volume": 82345678
                },
                ...
            ]
        }

    Free tier: 5 calls/minute
    """
    if not cfg.polygon_api_key:
        return {
            "status": "error",
            "message": "POLYGON_API_KEY not configured",
        }

    # Check circuit breaker
    breaker_status = _check_circuit_breaker()
    if breaker_status:
        return breaker_status

    # Check cache
    cache_key = f"history:{ticker.upper()}:{days}"
    cached = _get_cached(cache_key)
    if cached:
        cached["cached"] = True
        return cached

    ticker = ticker.upper()
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    url = f"{POLYGON_BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
    params = {"apiKey": cfg.polygon_api_key, "adjusted": "true", "sort": "asc"}

    try:
        session = await _sessions.get()
        async with session.get(url, params=params, timeout=30) as resp:
            if resp.status == 429:
                _record_failure()
                tool_health.record("polygon", success=False)
                return {
                    "status": "error",
                    "message": "Polygon.io rate limit exceeded. Free tier: 5 calls/minute.",
                }

            if resp.status != 200:
                error_text = await resp.text()
                _record_failure()
                tool_health.record("polygon", success=False)
                return {
                    "status": "error",
                    "message": f"Polygon.io error: {error_text}",
                }

            data = await resp.json()

            if not data.get("results"):
                return {
                    "status": "error",
                    "message": f"No historical data found for ticker: {ticker}",
                }

            _record_success()
            tool_health.record("polygon", success=True)

            history_data = [
                {
                    "date": datetime.fromtimestamp(item["t"] / 1000).strftime("%Y-%m-%d"),
                    "open": item.get("o", 0),
                    "high": item.get("h", 0),
                    "low": item.get("l", 0),
                    "close": item.get("c", 0),
                    "volume": item.get("v", 0),
                }
                for item in data["results"]
            ]

            response = {
                "status": "ok",
                "ticker": ticker,
                "days": days,
                "count": len(history_data),
                "data": history_data,
            }

            # Cache for 5 minutes
            _set_cache(cache_key, response)
            return response

    except asyncio.TimeoutError:
        _record_failure()
        tool_health.record("polygon", success=False)
        return {
            "status": "error",
            "message": "Polygon.io request timed out",
        }
    except Exception as e:
        _record_failure()
        tool_health.record("polygon", success=False)
        return {
            "status": "error",
            "message": f"Polygon.io error: {str(e)}",
        }


async def get_market_movers(direction: str = "gainers") -> dict[str, Any]:
    """
    Get top market movers (gainers or losers).

    Args:
        direction: "gainers" or "losers" (default: "gainers")

    Returns:
        {
            "status": "ok",
            "direction": "gainers",
            "tickers": [
                {
                    "ticker": "TSLA",
                    "price": 245.67,
                    "change_percent": 8.5,
                    "volume": 125000000
                },
                ...
            ]
        }

    Free tier: 5 calls/minute
    Note: This endpoint requires a paid plan on Polygon.io.
    Returns mock data on free tier.
    """
    if not cfg.polygon_api_key:
        return {
            "status": "error",
            "message": "POLYGON_API_KEY not configured",
        }

    # Check circuit breaker
    breaker_status = _check_circuit_breaker()
    if breaker_status:
        return breaker_status

    # Check cache
    cache_key = f"movers:{direction}"
    cached = _get_cached(cache_key)
    if cached:
        cached["cached"] = True
        return cached

    # For free tier, we'll fetch data for popular stocks and calculate movers
    popular_tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "AMD"]

    try:
        results = await asyncio.gather(
            *[get_stock_quote(ticker) for ticker in popular_tickers],
            return_exceptions=True
        )

        movers = []
        for i, result in enumerate(results):
            if isinstance(result, dict) and result.get("status") == "ok":
                movers.append({
                    "ticker": result["ticker"],
                    "price": result["price"],
                    "change_percent": result["change_percent"],
                    "volume": result["volume"],
                })

        # Sort by change_percent
        movers.sort(
            key=lambda x: x["change_percent"],
            reverse=(direction == "gainers")
        )

        response = {
            "status": "ok",
            "direction": direction,
            "count": len(movers[:5]),
            "tickers": movers[:5],  # Top 5
            "note": "Based on popular stocks. Upgrade to paid plan for full market scanners.",
        }

        # Cache for 5 minutes
        _set_cache(cache_key, response)
        return response

    except Exception as e:
        _record_failure()
        tool_health.record("polygon", success=False)
        return {
            "status": "error",
            "message": f"Error calculating market movers: {str(e)}",
        }


# LLM-callable skill definitions
POLYGON_SKILLS = [
    {
        "name": "get_stock_quote",
        "description": "Get real-time stock quote with price, volume, and daily change. Free tier: 5 calls/min. Includes caching.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g., AAPL, TSLA, MSFT, DIS)",
                },
            },
            "required": ["ticker"],
        },
        "function": get_stock_quote,
    },
    {
        "name": "get_market_status",
        "description": "Get current market status (open/closed) and exchange information. Free tier: 5 calls/min.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "function": get_market_status,
    },
    {
        "name": "get_stock_history",
        "description": "Get historical stock data (OHLCV) for up to 2 years. Free tier: 5 calls/min. Includes caching.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g., AAPL, TSLA)",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days of history (default: 30)",
                    "default": 30,
                },
            },
            "required": ["ticker"],
        },
        "function": get_stock_history,
    },
    {
        "name": "get_market_movers",
        "description": "Get top market gainers or losers. Returns top 5 from popular stocks. Free tier: 5 calls/min.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "description": "gainers or losers",
                    "enum": ["gainers", "losers"],
                    "default": "gainers",
                },
            },
            "required": [],
        },
        "function": get_market_movers,
    },
]
