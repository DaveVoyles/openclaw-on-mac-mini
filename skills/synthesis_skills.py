"""
Multi-source data synthesis skills — Intelligent correlation across APIs.

Combines NewsAPI, API-Sports, and Alpha Vantage data with LLM-powered insights
to generate contextual, synthesized reports that connect data points across sources.

Core capabilities:
- Company reports combining stock data, news, and sentiment
- Entertainment reports linking box office with stock movements
- Market overviews with economic news and sector analysis
- Cross-API correlation detection

Uses circuit breakers and caching to handle API rate limits gracefully.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from config import cfg
from http_session import SessionManager
from llm_patterns import _client, _record_usage

# Import existing API skills
from skills import finance_skills, news_skills
from tool_health import circuit_breaker, tool_health

log = logging.getLogger("openclaw.synthesis")

_sessions = SessionManager(timeout=30, name="synthesis_skills")

# Cache for synthesis results (TTL: 15 minutes)
_synthesis_cache: dict[str, tuple[float, Any]] = {}
_SYNTHESIS_CACHE_TTL = 900  # 15 minutes


def _get_cached(key: str) -> Any | None:
    """Get cached synthesis result if still valid."""
    if key in _synthesis_cache:
        timestamp, data = _synthesis_cache[key]
        if datetime.now().timestamp() - timestamp < _SYNTHESIS_CACHE_TTL:
            return data
    return None


def _set_cached(key: str, data: Any) -> None:
    """Cache synthesis result with timestamp."""
    _synthesis_cache[key] = (datetime.now().timestamp(), data)


async def _generate_llm_summary(prompt: str, max_sentences: int = 3) -> str:
    """
    Generate concise LLM summary connecting data points.

    Args:
        prompt: Context and data to summarize
        max_sentences: Maximum sentences in output (default: 3)

    Returns:
        Concise summary connecting the data points
    """
    if not _client:
        return ""

    try:
        system_prompt = (
            f"You are a financial analyst generating {max_sentences}-sentence summaries. "
            "Connect data points, highlight correlations, and provide actionable insights. "
            "Be concise, factual, and focus on cause-effect relationships."
        )

        response = await _client.aio.models.generate_content(
            model=cfg.llm_model,
            contents=prompt,
            config={
                "system_instruction": system_prompt,
                "temperature": 0.3,
                "max_output_tokens": 200,
            }
        )

        await _record_usage(response)

        if response and response.text:
            return response.text.strip()

        return ""

    except Exception as e:
        log.warning(f"LLM summary generation failed: {e}")
        return ""


async def synthesize_company_report(ticker: str) -> dict[str, Any]:
    """
    Synthesize comprehensive company report combining stock data, news, and sentiment.

    Fetches:
    - Current stock price and movement from Alpha Vantage
    - Recent news mentioning the company from NewsAPI
    - Sentiment analysis from Alpha Vantage news feed
    - LLM-generated insights connecting the data

    Args:
        ticker: Stock ticker symbol (e.g., "DIS", "NFLX", "AAPL")

    Returns:
        {
            "status": "ok" | "error",
            "entity": "Disney",
            "ticker": "DIS",
            "stock_data": {
                "price": 96.61,
                "change": "+1.23",
                "change_percent": "+1.31%",
                "volume": "8234567"
            },
            "sentiment": {
                "score": 0.7,
                "label": "Bullish",
                "news_count": 15
            },
            "news_summary": "3 articles: Moana 2 box office, theme park attendance, streaming growth",
            "synthesis": "Disney stock rallied 5% as Moana 2 exceeded box office expectations...",
            "sources": ["Alpha Vantage", "NewsAPI"],
            "timestamp": "2024-01-15T10:30:00"
        }

    Rate limits: Uses 1-2 Alpha Vantage calls, 1 NewsAPI call
    """
    cache_key = f"company_report:{ticker}:{datetime.now().strftime('%Y-%m-%d-%H')}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    ticker = ticker.upper()
    sources_used = []
    sources_failed = []

    # Get company name mapping (common tickers)
    ticker_to_company = {
        "DIS": "Disney",
        "NFLX": "Netflix",
        "WBD": "Warner Bros Discovery",
        "PARA": "Paramount",
        "CMCSA": "Comcast",
        "SONY": "Sony",
        "AAPL": "Apple",
        "MSFT": "Microsoft",
        "GOOGL": "Google",
        "META": "Meta",
        "TSLA": "Tesla",
        "AMZN": "Amazon",
    }
    company_name = ticker_to_company.get(ticker, ticker)

    # Parallel API calls for efficiency
    tasks = []

    # Task 1: Get stock data
    if not circuit_breaker.is_open("alphavantage"):
        tasks.append(("stock", finance_skills.get_stock_info(ticker)))

    # Task 2: Get sentiment analysis
    if not circuit_breaker.is_open("alphavantage"):
        tasks.append(("sentiment", finance_skills.get_sentiment_analysis(ticker)))

    # Task 3: Get recent news
    if not circuit_breaker.is_open("newsapi"):
        tasks.append(("news", news_skills.search_news(
            query=company_name,
            from_date=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
            page_size=5,
        )))

    # Execute all tasks in parallel
    results = {}
    if tasks:
        task_results = await asyncio.gather(
            *[task[1] for task in tasks],
            return_exceptions=True,
        )
        for (name, _), result in zip(tasks, task_results):
            if isinstance(result, Exception):
                log.error(f"Failed to fetch {name} for {ticker}: {result}")
                sources_failed.append(name)
            else:
                results[name] = result

    # Process stock data
    stock_data = {}
    if "stock" in results and results["stock"].get("status") == "ok":
        stock_info = results["stock"]
        stock_data = {
            "price": stock_info.get("price", 0),
            "change": stock_info.get("change", "N/A"),
            "change_percent": stock_info.get("change_percent", "N/A"),
            "volume": stock_info.get("volume", "N/A"),
            "high": stock_info.get("high", 0),
            "low": stock_info.get("low", 0),
        }
        sources_used.append("Alpha Vantage (Stock)")
        circuit_breaker.record_success("alphavantage")
        tool_health.record("alphavantage", success=True)
    else:
        sources_failed.append("Alpha Vantage (Stock)")
        circuit_breaker.record_failure("alphavantage")
        tool_health.record("alphavantage", success=False)

    # Process sentiment
    sentiment_data = {}
    if "sentiment" in results and results["sentiment"].get("status") == "ok":
        sent_info = results["sentiment"].get("sentiment", {}).get(ticker, {})
        sentiment_data = {
            "score": sent_info.get("score", 0),
            "label": sent_info.get("label", "Neutral"),
            "news_count": sent_info.get("recent_news", 0),
        }
        sources_used.append("Alpha Vantage (Sentiment)")
        circuit_breaker.record_success("alphavantage")
        tool_health.record("alphavantage", success=True)
    else:
        sources_failed.append("Alpha Vantage (Sentiment)")

    # Process news
    news_summary = ""
    news_articles = []
    if "news" in results and results["news"].get("status") == "ok":
        articles = results["news"].get("articles", [])
        news_articles = articles[:3]  # Top 3 articles
        if news_articles:
            topics = ", ".join([
                article.get("title", "")[:50] for article in news_articles
            ])
            news_summary = f"{len(news_articles)} articles: {topics}"
            sources_used.append("NewsAPI")
            circuit_breaker.record_success("newsapi")
            tool_health.record("newsapi", success=True)
    else:
        sources_failed.append("NewsAPI")
        circuit_breaker.record_failure("newsapi")
        tool_health.record("newsapi", success=False)

    # Generate LLM synthesis
    synthesis = ""
    if stock_data and (sentiment_data or news_articles):
        prompt = f"""Synthesize this data about {company_name} ({ticker}) into 2-3 concise sentences:

Stock: ${stock_data.get('price', 0):.2f} ({stock_data.get('change_percent', 'N/A')})
Sentiment: {sentiment_data.get('label', 'N/A')} (score: {sentiment_data.get('score', 0):.2f})
Recent News: {news_summary or 'Limited coverage'}

Connect the stock movement with sentiment and news. Highlight any correlations."""

        synthesis = await _generate_llm_summary(prompt, max_sentences=3)

    # If synthesis failed or no data, provide basic fallback
    if not synthesis and stock_data:
        change = stock_data.get("change_percent", "N/A")
        synthesis = f"{company_name} is trading at ${stock_data.get('price', 0):.2f} ({change}). "
        if sentiment_data:
            synthesis += f"Market sentiment is {sentiment_data.get('label', 'neutral').lower()}. "

    result = {
        "status": "ok" if (stock_data or sentiment_data or news_articles) else "error",
        "entity": company_name,
        "ticker": ticker,
        "stock_data": stock_data,
        "sentiment": sentiment_data,
        "news_summary": news_summary,
        "news_articles": [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source": a.get("source", {}).get("name", ""),
            }
            for a in news_articles
        ],
        "synthesis": synthesis,
        "sources": sources_used,
        "sources_failed": sources_failed,
        "timestamp": datetime.now().isoformat(),
    }

    if result["status"] == "ok":
        _set_cached(cache_key, result)

    return result


async def synthesize_entertainment_report(topic: str = "box office") -> dict[str, Any]:
    """
    Synthesize entertainment industry report linking box office with stock movements.

    Correlates:
    - Entertainment stock prices (Disney, Warner, Paramount, etc.)
    - Entertainment news (movie releases, box office, streaming)
    - Sentiment analysis for entertainment sector
    - Box office performance data (if available)

    Args:
        topic: Focus area - "box office", "streaming", "theme parks", "general"

    Returns:
        {
            "status": "ok",
            "topic": "box office",
            "studios": {
                "Disney": {
                    "ticker": "DIS",
                    "price": 96.61,
                    "change_percent": "+1.31%",
                    "sentiment": {"score": 0.7, "label": "Bullish"},
                    "news_count": 3
                },
                ...
            },
            "synthesis": "Entertainment stocks rallied this week with Disney leading...",
            "key_correlations": [
                "Disney stock rose 5% following Moana 2 box office success",
                "Warner Bros declined 2% amid streaming subscriber concerns"
            ],
            "sources": ["Alpha Vantage", "NewsAPI"],
            "timestamp": "2024-01-15T10:30:00"
        }

    Rate limits: Uses 6-8 Alpha Vantage calls, 1-2 NewsAPI calls
    """
    cache_key = f"entertainment_report:{topic}:{datetime.now().strftime('%Y-%m-%d-%H')}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    sources_used = []
    sources_failed = []

    # Get entertainment stocks
    studios_data = {}

    if not circuit_breaker.is_open("alphavantage"):
        try:
            box_office_result = await finance_skills.get_box_office_stocks()

            if box_office_result.get("status") == "ok":
                studios = box_office_result.get("studios", {})
                sources_used.append("Alpha Vantage (Stocks)")

                # Enrich with sentiment (parallel calls)
                tickers = [
                    data.get("symbol") for name, data in studios.items()
                    if "error" not in data and "symbol" in data
                ]

                if tickers:
                    ticker_str = ",".join(tickers[:5])  # Limit to 5 to save API calls
                    sentiment_result = await finance_skills.get_sentiment_analysis(ticker_str)

                    if sentiment_result.get("status") == "ok":
                        sentiments = sentiment_result.get("sentiment", {})
                        sources_used.append("Alpha Vantage (Sentiment)")
                    else:
                        sentiments = {}
                else:
                    sentiments = {}

                # Combine stock + sentiment data
                for studio_name, stock_data in studios.items():
                    if "error" not in stock_data:
                        ticker = stock_data.get("symbol", "")
                        studios_data[studio_name] = {
                            "ticker": ticker,
                            "price": stock_data.get("price", 0),
                            "change": stock_data.get("change", "N/A"),
                            "change_percent": stock_data.get("change_percent", "N/A"),
                            "sentiment": sentiments.get(ticker, {"score": 0, "label": "Neutral"}),
                            "news_count": sentiments.get(ticker, {}).get("recent_news", 0),
                        }
            else:
                sources_failed.append("Alpha Vantage (Stocks)")
        except Exception as e:
            log.error(f"Failed to fetch entertainment stocks: {e}")
            sources_failed.append("Alpha Vantage")

    # Get entertainment news
    news_articles = []
    if not circuit_breaker.is_open("newsapi"):
        try:
            news_result = await news_skills.top_headlines(
                category="entertainment",
                page_size=5,
            )

            if news_result.get("status") == "ok":
                news_articles = news_result.get("articles", [])
                sources_used.append("NewsAPI")
        except Exception as e:
            log.error(f"Failed to fetch entertainment news: {e}")
            sources_failed.append("NewsAPI")

    # Generate synthesis
    synthesis = ""
    key_correlations = []

    if studios_data or news_articles:
        # Build context for LLM
        stock_summary = "\n".join([
            f"- {name}: ${data['price']:.2f} ({data['change_percent']}) - "
            f"Sentiment: {data['sentiment']['label']}"
            for name, data in studios_data.items()
        ])

        news_summary = "\n".join([
            f"- {article.get('title', '')[:80]}"
            for article in news_articles[:3]
        ])

        prompt = f"""Analyze the entertainment industry data and identify correlations:

STOCKS:
{stock_summary or 'No stock data available'}

NEWS:
{news_summary or 'No major news'}

Generate:
1. A 2-3 sentence overview of the entertainment sector
2. List 2-3 key correlations between stock movements and news

Focus on cause-effect relationships."""

        synthesis = await _generate_llm_summary(prompt, max_sentences=3)

        # Extract correlations (basic pattern: look for movers)
        if studios_data:
            for studio_name, data in studios_data.items():
                change_str = data.get("change_percent", "0%").replace("%", "").replace("+", "")
                try:
                    change_val = float(change_str)
                    if abs(change_val) > 2:  # Significant movement
                        direction = "rose" if change_val > 0 else "declined"
                        key_correlations.append(
                            f"{studio_name} {direction} {abs(change_val):.1f}% - "
                            f"Sentiment: {data['sentiment']['label']}"
                        )
                except (ValueError, TypeError):
                    pass

    result = {
        "status": "ok" if (studios_data or news_articles) else "error",
        "topic": topic,
        "studios": studios_data,
        "news": [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source": a.get("source", {}).get("name", ""),
            }
            for a in news_articles[:5]
        ],
        "synthesis": synthesis,
        "key_correlations": key_correlations[:3],
        "sources": sources_used,
        "sources_failed": sources_failed,
        "timestamp": datetime.now().isoformat(),
    }

    if result["status"] == "ok":
        _set_cached(cache_key, result)

    return result


async def synthesize_market_overview() -> dict[str, Any]:
    """
    Synthesize comprehensive market overview with indices, news, and sector sentiment.

    Combines:
    - Major market indices (if available)
    - Top economic/financial news
    - Sector sentiment analysis
    - Market trend identification

    Returns:
        {
            "status": "ok",
            "market_summary": "Markets mixed as tech sector outperforms...",
            "top_news": [
                {
                    "title": "Fed Holds Rates Steady",
                    "sentiment": {"score": 0.3, "label": "Somewhat-Bullish"},
                    "url": "https://..."
                },
                ...
            ],
            "sector_sentiment": {
                "technology": {"score": 0.5, "label": "Bullish"},
                "finance": {"score": -0.2, "label": "Somewhat-Bearish"}
            },
            "synthesis": "Technology sector leads market gains...",
            "sources": ["Alpha Vantage", "NewsAPI"],
            "timestamp": "2024-01-15T10:30:00"
        }

    Rate limits: Uses 1-2 Alpha Vantage calls, 1 NewsAPI call
    """
    cache_key = f"market_overview:{datetime.now().strftime('%Y-%m-%d-%H')}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    sources_used = []
    sources_failed = []

    # Parallel data fetching
    tasks = []

    # Get financial/business news
    if not circuit_breaker.is_open("newsapi"):
        tasks.append(("news", news_skills.top_headlines(
            category="business",
            page_size=5,
        )))

    # Get market news with sentiment from Alpha Vantage
    if not circuit_breaker.is_open("alphavantage"):
        tasks.append(("market_news", finance_skills.get_market_news(
            topics="financial_markets,economy_fiscal,economy_monetary",
            limit=5,
        )))

    results = {}
    if tasks:
        task_results = await asyncio.gather(
            *[task[1] for task in tasks],
            return_exceptions=True,
        )
        for (name, _), result in zip(tasks, task_results):
            if isinstance(result, Exception):
                log.error(f"Failed to fetch {name}: {result}")
                sources_failed.append(name)
            else:
                results[name] = result

    # Process news
    top_news = []
    if "news" in results and results["news"].get("status") == "ok":
        articles = results["news"].get("articles", [])
        top_news = [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source": a.get("source", {}).get("name", ""),
                "description": (a.get("description") or "")[:150],
            }
            for a in articles[:5]
        ]
        sources_used.append("NewsAPI")

    # Process market news with sentiment
    market_news = []
    sector_sentiment = {}
    if "market_news" in results and results["market_news"].get("status") == "ok":
        feed = results["market_news"].get("feed", [])
        market_news = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "sentiment": item.get("sentiment", {}),
                "topics": item.get("topics", []),
            }
            for item in feed[:5]
        ]

        # Aggregate sentiment by topic/sector
        topic_scores: dict[str, float] = {}
        topic_counts: dict[str, int] = {}
        for item in feed:
            for topic in item.get("topics", []):
                if topic not in topic_scores:
                    topic_scores[topic] = 0.0
                    topic_counts[topic] = 0
                sentiment = item.get("sentiment", {})
                topic_scores[topic] += sentiment.get("score", 0.0)
                topic_counts[topic] += 1

        # Calculate average sentiment per sector
        for topic, total_score in topic_scores.items():
            count = topic_counts[topic]
            avg_score = round(total_score / count, 2) if count > 0 else 0.0
            label = "Bullish" if avg_score > 0.15 else "Bearish" if avg_score < -0.15 else "Neutral"
            sector_sentiment[topic] = {
                "score": avg_score,
                "label": label,
                "news_count": count,
            }

        sources_used.append("Alpha Vantage")

    # Generate synthesis
    synthesis = ""
    if market_news or top_news:
        news_titles = [n.get("title", "") for n in (market_news + top_news)[:5]]
        sentiment_summary = "\n".join([
            f"- {sector}: {data['label']} ({data['score']:.2f})"
            for sector, data in list(sector_sentiment.items())[:5]
        ])

        prompt = f"""Analyze market conditions and provide a concise 2-3 sentence overview:

TOP NEWS:
{chr(10).join([f'- {title}' for title in news_titles])}

SECTOR SENTIMENT:
{sentiment_summary or 'No sentiment data'}

Identify the main market trend and any notable sector movements."""

        synthesis = await _generate_llm_summary(prompt, max_sentences=3)

    result = {
        "status": "ok" if (market_news or top_news) else "error",
        "market_summary": synthesis,
        "top_news": (market_news + top_news)[:5],
        "sector_sentiment": sector_sentiment,
        "synthesis": synthesis,
        "sources": sources_used,
        "sources_failed": sources_failed,
        "timestamp": datetime.now().isoformat(),
    }

    if result["status"] == "ok":
        _set_cached(cache_key, result)

    return result


async def find_correlations(entity: str, entity_type: str = "company") -> dict[str, Any]:
    """
    Find correlations and relationships across data sources for a given entity.

    Searches for connections between:
    - Stock movements and news events
    - Sentiment changes and market activity
    - Cross-entity relationships (competitors, suppliers, etc.)

    Args:
        entity: Company name, ticker, or topic to analyze
        entity_type: "company", "sector", "topic" (default: "company")

    Returns:
        {
            "status": "ok",
            "entity": "Disney",
            "correlations": [
                {
                    "type": "stock_news",
                    "description": "5% stock increase correlates with positive Moana 2 reviews",
                    "confidence": "high",
                    "data_points": {...}
                },
                ...
            ],
            "synthesis": "Analysis reveals strong correlation between...",
            "sources": ["Alpha Vantage", "NewsAPI"],
            "timestamp": "2024-01-15T10:30:00"
        }

    Rate limits: Uses 2-3 Alpha Vantage calls, 1-2 NewsAPI calls
    """
    cache_key = f"correlations:{entity_type}:{entity}:{datetime.now().strftime('%Y-%m-%d')}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    sources_used = []
    correlations = []

    if entity_type == "company":
        # Use company report as base
        report = await synthesize_company_report(entity)

        if report.get("status") == "ok":
            sources_used.extend(report.get("sources", []))

            # Analyze for correlations
            stock_data = report.get("stock_data", {})
            sentiment = report.get("sentiment", {})
            news_articles = report.get("news_articles", [])

            # Stock-sentiment correlation
            if stock_data and sentiment:
                change_str = stock_data.get("change_percent", "0%").replace("%", "").replace("+", "")
                try:
                    change_val = float(change_str)
                    sent_score = sentiment.get("score", 0)

                    # Check alignment
                    if (change_val > 1 and sent_score > 0.15) or (change_val < -1 and sent_score < -0.15):
                        correlations.append({
                            "type": "stock_sentiment_alignment",
                            "description": (
                                f"Stock movement ({change_val:+.1f}%) aligns with "
                                f"{sentiment.get('label', 'neutral')} sentiment (score: {sent_score:.2f})"
                            ),
                            "confidence": "high",
                            "data_points": {
                                "stock_change": change_val,
                                "sentiment_score": sent_score,
                                "sentiment_label": sentiment.get("label"),
                            },
                        })
                    elif abs(change_val) > 2:
                        correlations.append({
                            "type": "stock_sentiment_divergence",
                            "description": (
                                f"Stock movement ({change_val:+.1f}%) diverges from "
                                f"{sentiment.get('label', 'neutral')} sentiment"
                            ),
                            "confidence": "medium",
                            "data_points": {
                                "stock_change": change_val,
                                "sentiment_score": sent_score,
                            },
                        })
                except (ValueError, TypeError):
                    pass

            # News-stock correlation
            if news_articles and stock_data:
                news_count = len(news_articles)
                correlations.append({
                    "type": "news_coverage",
                    "description": f"{news_count} recent news articles may be influencing stock movement",
                    "confidence": "medium",
                    "data_points": {
                        "news_count": news_count,
                        "top_headline": news_articles[0].get("title", "") if news_articles else "",
                    },
                })

    # Generate synthesis
    synthesis = ""
    if correlations:
        corr_summary = "\n".join([
            f"- {c['description']}" for c in correlations
        ])

        prompt = f"""Analyze these correlations for {entity}:

{corr_summary}

Provide a 2-sentence summary highlighting the most significant correlation and what it suggests."""

        synthesis = await _generate_llm_summary(prompt, max_sentences=2)
    else:
        synthesis = f"No significant correlations detected for {entity} in available data sources."

    result = {
        "status": "ok" if correlations else "partial",
        "entity": entity,
        "entity_type": entity_type,
        "correlations": correlations,
        "synthesis": synthesis,
        "sources": sources_used,
        "timestamp": datetime.now().isoformat(),
    }

    if correlations:
        _set_cached(cache_key, result)

    return result


# Export skills
SYNTHESIS_SKILLS = {
    "synthesize_company_report": synthesize_company_report,
    "synthesize_entertainment_report": synthesize_entertainment_report,
    "synthesize_market_overview": synthesize_market_overview,
    "find_correlations": find_correlations,
}
