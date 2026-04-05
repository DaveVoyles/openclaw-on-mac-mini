"""
Trend detection skills — LLM-callable functions for tracking and analyzing trends.

Skills:
  - track_topic: Start tracking a topic
  - get_trending_topics: Get top trending topics
  - detect_breaking_news: Identify sudden spikes
  - get_topic_trajectory: Show topic trend over time
  - untrack_topic: Stop tracking a topic
"""

import logging
from datetime import datetime
from typing import Any

from alert_manager import render_text_chart
from config import cfg
from http_session import SessionManager

# Import news and finance skills for data collection
from skills import finance_skills, news_skills, sports_skills
from trend_tracker import get_tracker

log = logging.getLogger("openclaw.trend_skills")

_sessions = SessionManager(timeout=30, name="trend_skills")


async def track_topic(
    topic: str,
    category: str = "General",
    user_id: str = "",
) -> dict[str, Any]:
    """
    Start tracking a topic for trend analysis.

    Args:
        topic: Topic to track (e.g., "Bitcoin", "Moana 2", "Lakers")
        category: Category (Entertainment, Finance, Sports, News, General)
        user_id: User who initiated tracking

    Returns:
        {
            "status": "ok",
            "message": "Now tracking Bitcoin in Finance category",
            "topic": "Bitcoin",
            "category": "Finance"
        }

    Example:
        track_topic("Moana 2", "Entertainment", "user123")
    """
    tracker = get_tracker()

    # Enable tracking in database
    success = tracker.enable_tracking(topic, category, user_id)

    if not success:
        return {
            "status": "error",
            "message": f"Failed to enable tracking for {topic}",
        }

    # Collect initial data point
    await _collect_data_point(topic, category)

    return {
        "status": "ok",
        "message": f"Now tracking **{topic}** in {category} category",
        "topic": topic,
        "category": category,
    }


async def untrack_topic(topic: str) -> dict[str, Any]:
    """
    Stop tracking a topic.

    Args:
        topic: Topic to stop tracking

    Returns:
        {
            "status": "ok",
            "message": "Stopped tracking Bitcoin"
        }
    """
    tracker = get_tracker()
    success = tracker.disable_tracking(topic)

    if not success:
        return {
            "status": "error",
            "message": f"Failed to stop tracking {topic}",
        }

    return {
        "status": "ok",
        "message": f"Stopped tracking **{topic}**",
    }


async def get_trending_topics(
    category: str = "",
    timeframe: str = "24h",
    limit: int = 10,
) -> dict[str, Any]:
    """
    Get top trending topics with volume and sentiment metrics.

    Args:
        category: Filter by category (Entertainment, Finance, Sports, News, General)
        timeframe: Time window (24h, 7d, 30d)
        limit: Maximum number of results (default: 10)

    Returns:
        {
            "status": "ok",
            "trending_topics": [
                {
                    "topic": "Bitcoin",
                    "category": "Finance",
                    "volume": 47,
                    "volume_change": "+380%",
                    "sentiment": 0.82,
                    "sentiment_change": "+0.15",
                    "trend_direction": "up",
                    "is_spike": True,
                    "is_breakout": False,
                    "sources": ["NewsAPI", "Alpha Vantage"]
                },
                ...
            ],
            "count": 3,
            "timeframe": "24h"
        }

    Example:
        get_trending_topics("Finance", "24h")
        get_trending_topics("", "7d", limit=5)
    """
    # Parse timeframe
    hours_map = {"24h": 24, "7d": 168, "30d": 720}
    hours = hours_map.get(timeframe, 24)

    tracker = get_tracker()
    analyses = tracker.get_trending_topics(category, hours, limit)

    trending_list = []
    for analysis in analyses:
        trending_list.append({
            "topic": analysis.topic,
            "category": analysis.category,
            "volume": analysis.current_volume,
            "volume_change": f"{'+' if analysis.volume_change_pct > 0 else ''}{analysis.volume_change_pct:.0f}%",
            "sentiment": round(analysis.current_sentiment, 2),
            "sentiment_change": f"{'+' if analysis.sentiment_change_24h > 0 else ''}{analysis.sentiment_change_24h:.2f}",
            "trend_direction": analysis.trend_direction,
            "is_spike": analysis.is_spike,
            "is_breakout": analysis.is_breakout,
            "velocity": round(analysis.velocity, 2),
            "z_score": round(analysis.z_score, 2),
            "sources": analysis.sources,
        })

    return {
        "status": "ok",
        "trending_topics": trending_list,
        "count": len(trending_list),
        "timeframe": timeframe,
        "category": category or "All",
    }


async def detect_breaking_news(
    category: str = "News",
    spike_threshold: float = 3.0,
) -> dict[str, Any]:
    """
    Detect breaking news by identifying sudden volume spikes.

    Args:
        category: Category to analyze (default: News)
        spike_threshold: Spike multiplier (default: 3.0x normal volume)

    Returns:
        {
            "status": "ok",
            "breaking_news": [
                {
                    "topic": "Moana 2",
                    "volume": 47,
                    "spike_multiplier": 3.8,
                    "hours_ago": 2,
                    "sentiment": 0.82,
                    "category": "Entertainment"
                },
                ...
            ],
            "count": 2
        }

    Example:
        detect_breaking_news("Entertainment")
        detect_breaking_news("News", spike_threshold=5.0)
    """
    tracker = get_tracker()
    analyses = tracker.get_trending_topics(category, hours=24, limit=50)

    # Filter for spikes only
    breaking = []
    for analysis in analyses:
        if analysis.is_spike and analysis.current_volume > 0:
            # Calculate when spike occurred
            hours_ago = 0
            if analysis.peak_time:
                hours_ago = int((datetime.now().timestamp() - analysis.peak_time) / 3600)

            spike_multiplier = (
                analysis.current_volume / analysis.avg_volume_7d
                if analysis.avg_volume_7d > 0
                else 0
            )

            if spike_multiplier >= spike_threshold:
                breaking.append({
                    "topic": analysis.topic,
                    "volume": analysis.current_volume,
                    "spike_multiplier": round(spike_multiplier, 1),
                    "hours_ago": hours_ago,
                    "sentiment": round(analysis.current_sentiment, 2),
                    "category": analysis.category,
                    "z_score": round(analysis.z_score, 1),
                })

    # Sort by spike multiplier
    breaking.sort(key=lambda x: x["spike_multiplier"], reverse=True)

    return {
        "status": "ok",
        "breaking_news": breaking,
        "count": len(breaking),
        "spike_threshold": spike_threshold,
    }


async def get_topic_trajectory(
    topic: str,
    category: str = "",
    timeframe: str = "24h",
) -> dict[str, Any]:
    """
    Get detailed trend trajectory for a specific topic with ASCII chart.

    Args:
        topic: Topic to analyze
        category: Optional category filter
        timeframe: Time window (24h, 7d, 30d)

    Returns:
        {
            "status": "ok",
            "topic": "Bitcoin",
            "category": "Finance",
            "current_volume": 47,
            "volume_change": "+380%",
            "sentiment": 0.82,
            "sentiment_change": "+0.15",
            "is_trending": True,
            "is_spike": True,
            "trend_direction": "up",
            "velocity": 4.2,
            "chart": "ASCII chart string",
            "analysis": "Bitcoin is experiencing a strong upward trend..."
        }

    Example:
        get_topic_trajectory("Bitcoin", "Finance", "7d")
    """
    # Parse timeframe
    hours_map = {"24h": 24, "7d": 168, "30d": 720}
    hours = hours_map.get(timeframe, 24)

    tracker = get_tracker()

    # Get trend analysis
    analysis = tracker.is_trending(topic, category)

    if analysis.current_volume == 0:
        return {
            "status": "error",
            "message": f"No data available for {topic}",
            "topic": topic,
        }

    # Generate chart
    chart = render_text_chart(topic, category, hours, width=30)

    # Generate analysis text
    analysis_text = _generate_analysis_text(analysis)

    return {
        "status": "ok",
        "topic": topic,
        "category": analysis.category,
        "current_volume": analysis.current_volume,
        "volume_change": f"{'+' if analysis.volume_change_pct > 0 else ''}{analysis.volume_change_pct:.0f}%",
        "sentiment": round(analysis.current_sentiment, 2),
        "sentiment_change": f"{'+' if analysis.sentiment_change_24h > 0 else ''}{analysis.sentiment_change_24h:.2f}",
        "is_trending": analysis.is_trending,
        "is_spike": analysis.is_spike,
        "is_breakout": analysis.is_breakout,
        "trend_direction": analysis.trend_direction,
        "velocity": round(analysis.velocity, 2),
        "z_score": round(analysis.z_score, 2),
        "chart": chart,
        "analysis": analysis_text,
        "timeframe": timeframe,
    }


async def list_tracked_topics() -> dict[str, Any]:
    """
    Get list of all topics currently being tracked.

    Returns:
        {
            "status": "ok",
            "tracked_topics": [
                {
                    "topic": "Bitcoin",
                    "category": "Finance",
                    "enabled": True,
                    "created_at": "2024-01-15T10:30:00"
                },
                ...
            ],
            "count": 5
        }
    """
    tracker = get_tracker()
    topics = tracker.get_tracked_topics(enabled_only=True)

    formatted = []
    for topic in topics:
        created_dt = datetime.fromtimestamp(topic["created_at"])
        formatted.append({
            "topic": topic["topic"],
            "category": topic["category"],
            "enabled": bool(topic["enabled"]),
            "created_at": created_dt.isoformat(),
            "spike_threshold": topic["spike_threshold"],
            "sentiment_threshold": topic["sentiment_threshold"],
        })

    return {
        "status": "ok",
        "tracked_topics": formatted,
        "count": len(formatted),
    }


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


async def _collect_data_point(topic: str, category: str) -> bool:
    """
    Collect a data point for a topic from appropriate API based on category.

    Args:
        topic: Topic name
        category: Category (Entertainment, Finance, Sports, News)

    Returns:
        True if successful
    """
    tracker = get_tracker()
    volume = 0
    sentiment = 0.0
    sources = []

    try:
        # Collect from NewsAPI (all categories)
        if cfg.newsapi_key:
            news_data = await news_skills.search_news(topic, page_size=20)
            if news_data.get("status") == "ok":
                articles = news_data.get("articles", [])
                volume += len(articles)
                sources.append("NewsAPI")

                # Calculate simple sentiment (placeholder - could use real sentiment analysis)
                sentiment = _calculate_simple_sentiment(articles)

        # Finance-specific data
        if category == "Finance" and cfg.alphavantage_key:
            # Check if it's a stock symbol or company name
            stock_data = await finance_skills.get_stock_info(topic)
            if stock_data.get("status") == "ok":
                volume += 1  # Stock data counts as 1 data point
                sources.append("Alpha Vantage")

                # Sentiment from price change
                change_str = stock_data.get("change_percent", "0%").replace("%", "").replace("+", "")
                try:
                    change_pct = float(change_str)
                    # Map price change to sentiment (-1 to 1)
                    sentiment = max(-1.0, min(1.0, change_pct / 10.0))
                except ValueError:
                    pass

        # Sports-specific data
        if category == "Sports" and cfg.apisports_key:
            # Try to get recent games mentioning the topic (team name)
            sports_data = await sports_skills.get_nba_scores()
            if sports_data.get("status") == "ok":
                games = sports_data.get("games", [])
                # Filter games involving the topic
                relevant_games = [
                    g for g in games
                    if topic.lower() in str(g).lower()
                ]
                volume += len(relevant_games)
                if relevant_games:
                    sources.append("API-Sports")

        # Store the data point
        if volume > 0:
            tracker.track_entity(topic, category, volume, sentiment, sources)
            log.info("Collected data: %s/%s vol=%d sent=%.2f", category, topic, volume, sentiment)
            return True

    except Exception as e:
        log.error("Error collecting data for %s: %s", topic, e)

    return False


def _calculate_simple_sentiment(articles: list[dict]) -> float:
    """
    Calculate simple sentiment from article titles and descriptions.

    Args:
        articles: List of article dicts

    Returns:
        Sentiment score -1.0 to 1.0
    """
    if not articles:
        return 0.0

    # Simple keyword-based sentiment
    positive_words = ["success", "win", "gain", "rise", "up", "breakthrough", "record", "best", "high"]
    negative_words = ["fail", "loss", "down", "crash", "drop", "worst", "low", "decline"]

    positive_count = 0
    negative_count = 0

    for article in articles:
        text = f"{article.get('title', '')} {article.get('description', '')}".lower()

        positive_count += sum(1 for word in positive_words if word in text)
        negative_count += sum(1 for word in negative_words if word in text)

    total = positive_count + negative_count
    if total == 0:
        return 0.0

    # Normalize to -1 to 1
    sentiment = (positive_count - negative_count) / total
    return max(-1.0, min(1.0, sentiment))


def _generate_analysis_text(analysis) -> str:
    """
    Generate human-readable analysis text from TrendAnalysis.

    Args:
        analysis: TrendAnalysis object

    Returns:
        Analysis text
    """
    parts = []

    # Overall trend
    if analysis.is_spike:
        parts.append(f"🚨 **{analysis.topic}** is experiencing a major spike in coverage.")
    elif analysis.is_breakout:
        parts.append(f"🆕 **{analysis.topic}** is a new trending topic emerging rapidly.")
    elif analysis.is_trending:
        parts.append(f"📈 **{analysis.topic}** is trending {analysis.trend_direction}.")
    else:
        parts.append(f"**{analysis.topic}** is showing stable activity.")

    # Volume details
    if analysis.volume_change_pct > 100:
        parts.append(
            f"Coverage volume has surged by **{analysis.volume_change_pct:.0f}%** "
            f"compared to the 7-day average."
        )
    elif analysis.volume_change_pct > 50:
        parts.append(
            f"Coverage has increased significantly by **{analysis.volume_change_pct:.0f}%**."
        )
    elif analysis.volume_change_pct < -50:
        parts.append(
            f"Coverage has dropped by **{abs(analysis.volume_change_pct):.0f}%**."
        )

    # Sentiment
    if analysis.current_sentiment > 0.5:
        parts.append(f"Sentiment is strongly **positive** ({analysis.current_sentiment:.2f}).")
    elif analysis.current_sentiment > 0.2:
        parts.append(f"Sentiment is **positive** ({analysis.current_sentiment:.2f}).")
    elif analysis.current_sentiment < -0.5:
        parts.append(f"Sentiment is strongly **negative** ({analysis.current_sentiment:.2f}).")
    elif analysis.current_sentiment < -0.2:
        parts.append(f"Sentiment is **negative** ({analysis.current_sentiment:.2f}).")
    else:
        parts.append(f"Sentiment is **neutral** ({analysis.current_sentiment:.2f}).")

    # Velocity
    if analysis.velocity > 3.0:
        parts.append(f"⚡ The trend is **accelerating rapidly** (velocity: {analysis.velocity:.1f}x).")
    elif analysis.velocity > 2.0:
        parts.append(f"The trend is **accelerating** (velocity: {analysis.velocity:.1f}x).")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Background job for automatic trend collection
# ---------------------------------------------------------------------------


async def update_all_tracked_trends() -> str:
    """
    Background job: Update trend data for all tracked topics.

    This function should be called periodically (every 1-6 hours) by the scheduler.

    Returns:
        Summary of updates
    """
    tracker = get_tracker()
    topics = tracker.get_tracked_topics(enabled_only=True)

    updated = 0
    failed = 0

    for topic_config in topics:
        topic = topic_config["topic"]
        category = topic_config["category"]

        try:
            success = await _collect_data_point(topic, category)
            if success:
                updated += 1
            else:
                failed += 1
        except Exception as e:
            log.error("Failed to update trend for %s: %s", topic, e)
            failed += 1

    # Cleanup old data
    deleted = tracker.cleanup_old_data()

    summary = (
        f"📊 Trend Update Complete\n"
        f"Updated: {updated} topics\n"
        f"Failed: {failed} topics\n"
        f"Cleaned: {deleted} old records\n"
        f"Total tracked: {len(topics)}"
    )

    log.info(summary.replace("\n", " | "))
    return summary
