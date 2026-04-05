"""
Financial data skills using Alpha Vantage API

Free tier: 25 requests/day
Covers: stock data, market news, sentiment analysis, financial indicators
"""

from typing import Any

from config import cfg
from http_session import SessionManager
from tool_health import circuit_breaker, tool_health

_sessions = SessionManager(timeout=30, name="finance_skills")

ALPHAVANTAGE_BASE_URL = "https://www.alphavantage.co/query"

# Entertainment sector stocks for box office correlation
ENTERTAINMENT_STOCKS = {
    "disney": "DIS",
    "warner": "WBD",
    "paramount": "PARA",
    "netflix": "NFLX",
    "comcast": "CMCSA",
    "sony": "SONY",
    "lionsgate": "LGF.A",
}


async def get_stock_info(symbol: str) -> dict[str, Any]:
    """
    Get current stock price and key statistics.

    Args:
        symbol: Stock ticker symbol (e.g., "DIS" for Disney, "NFLX" for Netflix)

    Returns:
        {
            "status": "ok",
            "symbol": "DIS",
            "price": 95.42,
            "change": "+1.23",
            "change_percent": "+1.31%",
            "volume": "8234567",
            "market_cap": "174.2B",
            "pe_ratio": "34.2",
            "52_week_high": "125.00",
            "52_week_low": "78.50"
        }

    Free tier: 25 requests/day
    """
    if not cfg.alphavantage_key:
        return {
            "status": "error",
            "message": "ALPHAVANTAGE_KEY not configured",
        }

    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": symbol.upper(),
        "apikey": cfg.alphavantage_key,
    }

    session = await _sessions.get()
    async with session.get(ALPHAVANTAGE_BASE_URL, params=params, timeout=30) as resp:
        if resp.status == 429:
            tool_health.record("alphavantage", success=False)
            return {
                "status": "error",
                "message": "Alpha Vantage rate limit exceeded. Free tier: 25 requests/day.",
            }

        if resp.status != 200:
            error_text = await resp.text()
            tool_health.record("alphavantage", success=False)
            return {
                "status": "error",
                "message": f"Alpha Vantage error: {error_text}",
            }

        data = await resp.json()

        # Check for API limit message
        if "Note" in data or "Information" in data:
            tool_health.record("alphavantage", success=False)
            return {
                "status": "error",
                "message": "Alpha Vantage rate limit reached. Free tier: 25 requests/day. Try again tomorrow.",
            }

        quote = data.get("Global Quote", {})
        if not quote:
            return {
                "status": "error",
                "message": f"No data found for symbol: {symbol}",
            }

        tool_health.record("alphavantage", success=True)

        return {
            "status": "ok",
            "symbol": quote.get("01. symbol", symbol),
            "price": float(quote.get("05. price", 0)),
            "change": quote.get("09. change", "N/A"),
            "change_percent": quote.get("10. change percent", "N/A"),
            "volume": quote.get("06. volume", "N/A"),
            "latest_trading_day": quote.get("07. latest trading day", "N/A"),
            "previous_close": float(quote.get("08. previous close", 0)),
            "high": float(quote.get("03. high", 0)),
            "low": float(quote.get("04. low", 0)),
        }


async def get_market_news(topics: str | None = None, tickers: str | None = None, limit: int = 10) -> dict[str, Any]:
    """
    Get market news with AI-powered sentiment and relevance scores.

    Args:
        topics: Comma-separated topics (e.g., "technology", "earnings", "ipo")
        tickers: Comma-separated ticker symbols (e.g., "DIS,NFLX,WBD")
        limit: Number of articles (1-200)

    Returns:
        {
            "status": "ok",
            "feed": [
                {
                    "title": "Disney Reports Q4 Earnings Beat",
                    "url": "https://...",
                    "summary": "...",
                    "source": "Reuters",
                    "published": "2024-01-15T10:30:00",
                    "sentiment": {
                        "score": 0.75,
                        "label": "Bullish"
                    },
                    "tickers": ["DIS"]
                },
                ...
            ]
        }

    Topics: blockchain, earnings, ipo, mergers_and_acquisitions, financial_markets,
            economy_fiscal, economy_monetary, economy_macro, energy_transportation,
            finance, life_sciences, manufacturing, real_estate, retail_wholesale,
            technology

    Free tier: 25 requests/day
    """
    if not cfg.alphavantage_key:
        return {
            "status": "error",
            "message": "ALPHAVANTAGE_KEY not configured",
            "feed": [],
        }

    params = {
        "function": "NEWS_SENTIMENT",
        "apikey": cfg.alphavantage_key,
        "limit": min(limit, 200),
    }

    if topics:
        params["topics"] = topics
    if tickers:
        params["tickers"] = tickers

    session = await _sessions.get()
    async with session.get(ALPHAVANTAGE_BASE_URL, params=params, timeout=30) as resp:
        if resp.status == 429:
            tool_health.record("alphavantage", success=False)
            return {
                "status": "error",
                "message": "Alpha Vantage rate limit exceeded. Free tier: 25 requests/day.",
                "feed": [],
            }

        if resp.status != 200:
            error_text = await resp.text()
            tool_health.record("alphavantage", success=False)
            return {
                "status": "error",
                "message": f"Alpha Vantage error: {error_text}",
                "feed": [],
            }

        data = await resp.json()

        if "Note" in data or "Information" in data:
            tool_health.record("alphavantage", success=False)
            return {
                "status": "error",
                "message": "Alpha Vantage rate limit reached. Free tier: 25 requests/day.",
                "feed": [],
            }

        tool_health.record("alphavantage", success=True)

        articles = []
        for item in data.get("feed", []):
            # Get overall sentiment
            sentiment_score = float(item.get("overall_sentiment_score", 0))
            sentiment_label = item.get("overall_sentiment_label", "Neutral")

            articles.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "summary": item.get("summary", "")[:300],  # Truncate
                "source": item.get("source", ""),
                "published": item.get("time_published", ""),
                "sentiment": {
                    "score": sentiment_score,
                    "label": sentiment_label,
                },
                "topics": [t["topic"] for t in item.get("topics", [])],
                "tickers": [t["ticker"] for t in item.get("ticker_sentiment", [])],
            })

        return {
            "status": "ok",
            "items": data.get("items", "0"),
            "sentiment_score_definition": data.get("sentiment_score_definition", ""),
            "feed": articles,
        }


async def get_sentiment_analysis(tickers: str) -> dict[str, Any]:
    """
    Get AI-powered sentiment analysis for specific stocks.

    Args:
        tickers: Comma-separated ticker symbols (e.g., "DIS,NFLX,WBD")

    Returns:
        {
            "status": "ok",
            "tickers": ["DIS", "NFLX"],
            "sentiment": {
                "DIS": {
                    "score": 0.25,
                    "label": "Somewhat-Bullish",
                    "recent_news": 15
                },
                "NFLX": {
                    "score": -0.10,
                    "label": "Somewhat-Bearish",
                    "recent_news": 8
                }
            }
        }

    Sentiment labels:
        - Bullish: score > 0.35
        - Somewhat-Bullish: 0.15 < score <= 0.35
        - Neutral: -0.15 <= score <= 0.15
        - Somewhat-Bearish: -0.35 <= score < -0.15
        - Bearish: score < -0.35

    Free tier: 25 requests/day
    """
    if not cfg.alphavantage_key:
        return {
            "status": "error",
            "message": "ALPHAVANTAGE_KEY not configured",
        }

    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": tickers,
        "apikey": cfg.alphavantage_key,
    }

    session = await _sessions.get()
    async with session.get(ALPHAVANTAGE_BASE_URL, params=params, timeout=30) as resp:
        if resp.status == 429:
            tool_health.record("alphavantage", success=False)
            return {
                "status": "error",
                "message": "Alpha Vantage rate limit exceeded. Free tier: 25 requests/day.",
            }

        if resp.status != 200:
            error_text = await resp.text()
            tool_health.record("alphavantage", success=False)
            return {
                "status": "error",
                "message": f"Alpha Vantage error: {error_text}",
            }

        data = await resp.json()

        if "Note" in data or "Information" in data:
            tool_health.record("alphavantage", success=False)
            return {
                "status": "error",
                "message": "Alpha Vantage rate limit reached. Free tier: 25 requests/day.",
            }

        tool_health.record("alphavantage", success=True)

        # Aggregate sentiment by ticker
        ticker_list = [t.strip() for t in tickers.split(",")]
        sentiment_map = {ticker: {"scores": [], "count": 0} for ticker in ticker_list}

        for article in data.get("feed", []):
            for ticker_sentiment in article.get("ticker_sentiment", []):
                ticker = ticker_sentiment.get("ticker", "")
                if ticker in sentiment_map:
                    score = float(ticker_sentiment.get("ticker_sentiment_score", 0))
                    sentiment_map[ticker]["scores"].append(score)
                    sentiment_map[ticker]["count"] += 1

        # Calculate average sentiment
        result_sentiment = {}
        for ticker, info in sentiment_map.items():
            if info["count"] > 0:
                avg_score = sum(info["scores"]) / info["count"]
                result_sentiment[ticker] = {
                    "score": round(avg_score, 3),
                    "label": _get_sentiment_label(avg_score),
                    "recent_news": info["count"],
                }
            else:
                result_sentiment[ticker] = {
                    "score": 0.0,
                    "label": "No Recent News",
                    "recent_news": 0,
                }

        return {
            "status": "ok",
            "tickers": ticker_list,
            "sentiment": result_sentiment,
        }


def _get_sentiment_label(score: float) -> str:
    """Convert sentiment score to human-readable label."""
    if score > 0.35:
        return "Bullish"
    elif score > 0.15:
        return "Somewhat-Bullish"
    elif score >= -0.15:
        return "Neutral"
    elif score >= -0.35:
        return "Somewhat-Bearish"
    else:
        return "Bearish"


async def get_box_office_stocks() -> dict[str, Any]:
    """
    Get stock prices for major entertainment companies (box office correlation).

    Returns:
        {
            "status": "ok",
            "studios": {
                "Disney": {"symbol": "DIS", "price": 95.42, "change": "+1.23%"},
                "Warner Bros": {"symbol": "WBD", "price": 12.34, "change": "-0.5%"},
                ...
            }
        }

    Useful for box office performance analysis.
    Free tier: 25 requests/day (this uses 1 request per stock!)
    Consider caching results for 1+ hours.
    """
    if not cfg.alphavantage_key:
        return {
            "status": "error",
            "message": "ALPHAVANTAGE_KEY not configured",
        }

    # For free tier, just get a few key studios
    key_studios = {
        "Disney": "DIS",
        "Warner Bros Discovery": "WBD",
        "Netflix": "NFLX",
        "Paramount": "PARA",
    }

    studios = {}
    for name, symbol in key_studios.items():
        stock_data = await get_stock_info(symbol)
        if stock_data.get("status") == "ok":
            studios[name] = {
                "symbol": symbol,
                "price": stock_data["price"],
                "change": stock_data["change_percent"],
                "volume": stock_data["volume"],
            }
        else:
            studios[name] = {
                "symbol": symbol,
                "error": stock_data.get("message", "Unknown error"),
            }

    return {
        "status": "ok",
        "studios": studios,
        "note": "Stock prices reflect broader company performance, not just film divisions",
    }


# LLM-callable skill definitions
FINANCE_SKILLS = [
    {
        "name": "get_stock_info",
        "description": "Get current stock price and statistics for a ticker symbol. Great for entertainment stocks (DIS, NFLX, WBD). Free tier: 25 req/day.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g., DIS, NFLX, WBD, AAPL)",
                },
            },
            "required": ["symbol"],
        },
        "function": get_stock_info,
    },
    {
        "name": "get_market_news",
        "description": "Get market news with AI sentiment analysis. Can filter by topics or tickers. Includes relevance scores. Free tier: 25 req/day.",
        "parameters": {
            "type": "object",
            "properties": {
                "topics": {
                    "type": "string",
                    "description": "Comma-separated topics: technology, earnings, ipo, financial_markets, entertainment, etc.",
                },
                "tickers": {
                    "type": "string",
                    "description": "Comma-separated ticker symbols (e.g., 'DIS,NFLX,WBD')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of articles (1-200)",
                    "default": 10,
                },
            },
            "required": [],
        },
        "function": get_market_news,
    },
    {
        "name": "get_sentiment_analysis",
        "description": "Get AI-powered sentiment analysis for specific stocks based on recent news. Bullish/Bearish scoring. Free tier: 25 req/day.",
        "parameters": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "string",
                    "description": "Comma-separated ticker symbols (e.g., 'DIS,NFLX,WBD')",
                },
            },
            "required": ["tickers"],
        },
        "function": get_sentiment_analysis,
    },
    {
        "name": "get_box_office_stocks",
        "description": "Get stock performance for major entertainment studios (Disney, Warner Bros, Netflix, Paramount). Useful for box office correlation analysis. Uses 4 API calls. Free tier: 25 req/day total.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "function": get_box_office_stocks,
    },
]
