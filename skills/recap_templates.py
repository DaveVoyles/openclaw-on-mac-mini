"""
Topic-specific recap templates for OpenClaw's weekly recap system.

Predefined templates for common use cases:
- Entertainment: Box office, streaming, studio stocks, industry news
- Sports: NBA scores, standings, upcoming games, sports news
- Tech: Headlines, stock performance, product launches, funding
- Finance: Market summary, top movers, sector sentiment
- Everything: Combined condensed version of all above
"""

from datetime import datetime, timedelta
from typing import Any

# Template configurations
RECAP_TEMPLATES = {
    "entertainment": {
        "name": "Entertainment Industry Recap",
        "description": "Box office news, streaming highlights, studio stocks, and industry updates",
        "topics": ["entertainment", "box office", "streaming", "movies", "television"],
        "data_sources": ["news", "finance"],
        "format": "detailed",
        "sections": [
            "box_office_top_5",
            "streaming_highlights",
            "studio_stocks",
            "industry_news",
            "sentiment_analysis",
        ],
        "stocks": ["DIS", "WBD", "NFLX", "PARA"],  # Disney, Warner, Netflix, Paramount
        "max_articles": 15,
    },
    "sports": {
        "name": "Sports Recap",
        "description": "NBA scores, standings, upcoming matchups, and sports headlines",
        "topics": ["NBA", "basketball", "sports"],
        "data_sources": ["sports", "news"],
        "format": "detailed",
        "sections": [
            "nba_recent_games",
            "nba_standings_top_10",
            "upcoming_matchups",
            "sports_headlines",
            "trending_stories",
        ],
        "days_lookback": 7,
        "max_articles": 10,
    },
    "tech": {
        "name": "Tech Industry Recap",
        "description": "Tech headlines, FAANG+ stocks, product launches, and funding news",
        "topics": ["technology", "AI", "startups", "products", "tech"],
        "data_sources": ["news", "finance"],
        "format": "detailed",
        "sections": [
            "top_tech_headlines",
            "tech_stock_performance",
            "product_launches",
            "funding_announcements",
            "industry_sentiment",
        ],
        "stocks": ["AAPL", "GOOGL", "META", "AMZN", "MSFT", "NVDA", "TSLA"],  # FAANG + emerging
        "max_articles": 15,
    },
    "finance": {
        "name": "Finance & Markets Recap",
        "description": "Market indices, top movers, sector sentiment, and financial news",
        "topics": ["finance", "markets", "stocks", "economy"],
        "data_sources": ["finance", "news"],
        "format": "detailed",
        "sections": [
            "market_summary",
            "top_movers",
            "sector_sentiment",
            "financial_headlines",
            "economic_indicators",
        ],
        "indices": ["SPY", "QQQ", "DIA"],  # S&P 500, Nasdaq, Dow
        "max_articles": 12,
    },
    "everything": {
        "name": "Everything Recap",
        "description": "Condensed summary of entertainment, sports, tech, and finance",
        "topics": ["entertainment", "sports", "technology", "finance", "news"],
        "data_sources": ["news", "finance", "sports"],
        "format": "condensed",
        "sections": [
            "top_stories_all",
            "key_market_moves",
            "major_sports_results",
            "tech_highlights",
            "entertainment_updates",
        ],
        "max_articles": 20,
        "priority": "high_impact",  # Prioritize biggest stories only
    },
}


def get_available_templates() -> dict[str, Any]:
    """
    Get list of all available recap templates.

    Returns:
        {
            "templates": ["entertainment", "sports", "tech", "finance", "everything"],
            "details": {
                "entertainment": {
                    "name": "Entertainment Industry Recap",
                    "description": "...",
                    ...
                },
                ...
            }
        }
    """
    return {
        "templates": list(RECAP_TEMPLATES.keys()),
        "details": {
            name: {
                "name": config["name"],
                "description": config["description"],
                "format": config["format"],
                "sections": config["sections"],
            }
            for name, config in RECAP_TEMPLATES.items()
        },
    }


def apply_template(template_name: str, date_range: str = "7d") -> dict[str, Any]:
    """
    Apply a template and return configured parameters for recap generation.

    Args:
        template_name: One of: entertainment, sports, tech, finance, everything
        date_range: Time range (e.g., "7d", "14d", "1m")

    Returns:
        {
            "template": "entertainment",
            "config": {...template config...},
            "query_params": {
                "topics": [...],
                "stocks": [...],
                "date_from": "2024-01-01",
                "date_to": "2024-01-08",
                "max_articles": 15
            }
        }

    Raises:
        ValueError: If template_name is not recognized
    """
    if template_name not in RECAP_TEMPLATES:
        available = ", ".join(RECAP_TEMPLATES.keys())
        raise ValueError(
            f"Unknown template '{template_name}'. Available templates: {available}"
        )

    config = RECAP_TEMPLATES[template_name]

    # Parse date range
    days = _parse_date_range(date_range)
    date_to = datetime.now()
    date_from = date_to - timedelta(days=days)

    query_params = {
        "topics": config["topics"],
        "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"),
        "max_articles": config.get("max_articles", 10),
    }

    # Add stocks if present
    if "stocks" in config:
        query_params["stocks"] = config["stocks"]

    # Add indices if present
    if "indices" in config:
        query_params["indices"] = config["indices"]

    # Add sport-specific params
    if "days_lookback" in config:
        query_params["days_lookback"] = config["days_lookback"]

    return {
        "template": template_name,
        "config": config,
        "query_params": query_params,
    }


async def generate_recap_from_template(
    template_name: str,
    date_range: str = "7d",
) -> dict[str, Any]:
    """
    Generate a recap using a predefined template.

    Args:
        template_name: One of: entertainment, sports, tech, finance, everything
        date_range: Time range (e.g., "7d", "14d", "1m")

    Returns:
        {
            "status": "ok",
            "template": "entertainment",
            "recap": {
                "title": "Entertainment Industry Recap",
                "period": "Jan 1 - Jan 8, 2024",
                "sections": {
                    "box_office_top_5": [...],
                    "streaming_highlights": [...],
                    ...
                },
                "summary": "...",
                "generated_at": "2024-01-08T10:30:00Z"
            }
        }
    """
    try:
        template_config = apply_template(template_name, date_range)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    config = template_config["config"]
    params = template_config["query_params"]

    # Build recap based on template type
    if template_name == "entertainment":
        recap_data = await _generate_entertainment_recap(config, params)
    elif template_name == "sports":
        recap_data = await _generate_sports_recap(config, params)
    elif template_name == "tech":
        recap_data = await _generate_tech_recap(config, params)
    elif template_name == "finance":
        recap_data = await _generate_finance_recap(config, params)
    elif template_name == "everything":
        recap_data = await _generate_everything_recap(config, params)
    else:
        return {"status": "error", "message": f"Template '{template_name}' not implemented"}

    return {
        "status": "ok",
        "template": template_name,
        "recap": recap_data,
    }


# ============================================================================
# Private helper functions for each template type
# ============================================================================


async def _generate_entertainment_recap(config: dict, params: dict) -> dict[str, Any]:
    """Generate entertainment industry recap."""
    from skills.finance_skills import get_box_office_stocks, get_stock_info
    from skills.news_skills import search_news, top_headlines

    sections = {}

    # Box office news
    try:
        box_office_news = await search_news(
            query="box office",
            from_date=params["date_from"],
            to_date=params["date_to"],
            page_size=5,
        )
        sections["box_office_top_5"] = _extract_articles(box_office_news, limit=5)
    except Exception as e:
        sections["box_office_top_5"] = {"error": str(e)}

    # Streaming highlights
    try:
        streaming_news = await search_news(
            query="streaming OR Netflix OR Disney+ OR HBO Max",
            from_date=params["date_from"],
            to_date=params["date_to"],
            page_size=5,
        )
        sections["streaming_highlights"] = _extract_articles(streaming_news, limit=5)
    except Exception as e:
        sections["streaming_highlights"] = {"error": str(e)}

    # Studio stock performance
    try:
        stock_data = await get_box_office_stocks()
        sections["studio_stocks"] = stock_data
    except Exception as e:
        sections["studio_stocks"] = {"error": str(e)}

    # Industry news
    try:
        industry_news = await top_headlines(
            category="entertainment",
            page_size=5,
        )
        sections["industry_news"] = _extract_articles(industry_news, limit=5)
    except Exception as e:
        sections["industry_news"] = {"error": str(e)}

    # Sentiment analysis
    try:
        from skills.finance_skills import get_sentiment_analysis

        tickers = ",".join(params.get("stocks", []))
        sentiment = await get_sentiment_analysis(tickers)
        sections["sentiment_analysis"] = sentiment
    except Exception as e:
        sections["sentiment_analysis"] = {"error": str(e)}

    return {
        "title": config["name"],
        "period": f"{params['date_from']} to {params['date_to']}",
        "sections": sections,
        "summary": _generate_summary(sections, "entertainment"),
        "generated_at": datetime.now().isoformat(),
    }


async def _generate_sports_recap(config: dict, params: dict) -> dict[str, Any]:
    """Generate sports recap."""
    from skills.news_skills import search_news
    from skills.sports_skills import get_nba_scores, get_schedule, get_team_standings

    sections = {}
    days_back = params.get("days_lookback", 7)

    # Recent NBA games (last 7 days)
    try:
        recent_games = []
        for i in range(days_back):
            game_date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            scores = await get_nba_scores(date=game_date)
            if scores.get("status") == "ok":
                recent_games.extend(scores.get("games", []))
        sections["nba_recent_games"] = recent_games[:10]  # Top 10 most recent
    except Exception as e:
        sections["nba_recent_games"] = {"error": str(e)}

    # Current standings (top 10)
    try:
        standings = await get_team_standings(sport="nba")
        if standings.get("status") == "ok":
            teams = standings.get("standings", [])[:10]
            sections["nba_standings_top_10"] = teams
    except Exception as e:
        sections["nba_standings_top_10"] = {"error": str(e)}

    # Upcoming marquee matchups
    try:
        upcoming = await get_schedule(
            sport="nba",
            date_from=datetime.now().strftime("%Y-%m-%d"),
            date_to=(datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
        )
        sections["upcoming_matchups"] = _extract_games(upcoming, limit=5)
    except Exception as e:
        sections["upcoming_matchups"] = {"error": str(e)}

    # Sports news headlines
    try:
        sports_news = await search_news(
            query="NBA OR basketball",
            from_date=params["date_from"],
            to_date=params["date_to"],
            page_size=5,
        )
        sections["sports_headlines"] = _extract_articles(sports_news, limit=5)
    except Exception as e:
        sections["sports_headlines"] = {"error": str(e)}

    # Trending stories
    try:
        trending = await search_news(
            query="NBA trending OR player news",
            from_date=params["date_from"],
            to_date=params["date_to"],
            sort_by="popularity",
            page_size=5,
        )
        sections["trending_stories"] = _extract_articles(trending, limit=5)
    except Exception as e:
        sections["trending_stories"] = {"error": str(e)}

    return {
        "title": config["name"],
        "period": f"{params['date_from']} to {params['date_to']}",
        "sections": sections,
        "summary": _generate_summary(sections, "sports"),
        "generated_at": datetime.now().isoformat(),
    }


async def _generate_tech_recap(config: dict, params: dict) -> dict[str, Any]:
    """Generate tech industry recap."""
    from skills.finance_skills import get_stock_info
    from skills.news_skills import search_news

    sections = {}

    # Top tech headlines
    try:
        tech_news = await search_news(
            query="technology OR AI OR artificial intelligence OR startup",
            from_date=params["date_from"],
            to_date=params["date_to"],
            page_size=10,
        )
        sections["top_tech_headlines"] = _extract_articles(tech_news, limit=10)
    except Exception as e:
        sections["top_tech_headlines"] = {"error": str(e)}

    # Tech stock performance
    try:
        stock_performance = []
        for ticker in params.get("stocks", []):
            stock_data = await get_stock_info(ticker)
            if stock_data.get("status") == "ok":
                stock_performance.append(stock_data)
        sections["tech_stock_performance"] = stock_performance
    except Exception as e:
        sections["tech_stock_performance"] = {"error": str(e)}

    # Product launches
    try:
        product_news = await search_news(
            query="product launch OR new product OR unveil",
            from_date=params["date_from"],
            to_date=params["date_to"],
            page_size=5,
        )
        sections["product_launches"] = _extract_articles(product_news, limit=5)
    except Exception as e:
        sections["product_launches"] = {"error": str(e)}

    # Funding announcements
    try:
        funding_news = await search_news(
            query="funding OR Series A OR Series B OR investment OR venture capital",
            from_date=params["date_from"],
            to_date=params["date_to"],
            page_size=5,
        )
        sections["funding_announcements"] = _extract_articles(funding_news, limit=5)
    except Exception as e:
        sections["funding_announcements"] = {"error": str(e)}

    # Industry sentiment
    try:
        from skills.finance_skills import get_sentiment_analysis

        tickers = ",".join(params.get("stocks", []))
        sentiment = await get_sentiment_analysis(tickers)
        sections["industry_sentiment"] = sentiment
    except Exception as e:
        sections["industry_sentiment"] = {"error": str(e)}

    return {
        "title": config["name"],
        "period": f"{params['date_from']} to {params['date_to']}",
        "sections": sections,
        "summary": _generate_summary(sections, "tech"),
        "generated_at": datetime.now().isoformat(),
    }


async def _generate_finance_recap(config: dict, params: dict) -> dict[str, Any]:
    """Generate finance and markets recap."""
    from skills.finance_skills import get_market_news, get_stock_info
    from skills.news_skills import search_news

    sections = {}

    # Market summary (major indices)
    try:
        market_summary = []
        for ticker in params.get("indices", []):
            index_data = await get_stock_info(ticker)
            if index_data.get("status") == "ok":
                market_summary.append(index_data)
        sections["market_summary"] = market_summary
    except Exception as e:
        sections["market_summary"] = {"error": str(e)}

    # Top movers
    try:
        market_news = await get_market_news(
            topics="gainers,losers",
            limit=10,
        )
        sections["top_movers"] = market_news
    except Exception as e:
        sections["top_movers"] = {"error": str(e)}

    # Sector sentiment
    try:
        from skills.finance_skills import get_sentiment_analysis

        # Get sentiment for major sectors
        sector_tickers = "XLK,XLF,XLV,XLE,XLY"  # Tech, Finance, Health, Energy, Consumer
        sentiment = await get_sentiment_analysis(sector_tickers)
        sections["sector_sentiment"] = sentiment
    except Exception as e:
        sections["sector_sentiment"] = {"error": str(e)}

    # Financial headlines
    try:
        finance_news = await search_news(
            query="finance OR stock market OR economy",
            from_date=params["date_from"],
            to_date=params["date_to"],
            page_size=8,
        )
        sections["financial_headlines"] = _extract_articles(finance_news, limit=8)
    except Exception as e:
        sections["financial_headlines"] = {"error": str(e)}

    # Economic indicators
    try:
        econ_news = await search_news(
            query="economic indicators OR GDP OR inflation OR unemployment OR Fed",
            from_date=params["date_from"],
            to_date=params["date_to"],
            page_size=5,
        )
        sections["economic_indicators"] = _extract_articles(econ_news, limit=5)
    except Exception as e:
        sections["economic_indicators"] = {"error": str(e)}

    return {
        "title": config["name"],
        "period": f"{params['date_from']} to {params['date_to']}",
        "sections": sections,
        "summary": _generate_summary(sections, "finance"),
        "generated_at": datetime.now().isoformat(),
    }


async def _generate_everything_recap(config: dict, params: dict) -> dict[str, Any]:
    """Generate condensed everything recap."""
    from skills.news_skills import top_headlines

    sections = {}

    # Top stories across all categories
    try:
        all_news = await top_headlines(page_size=20)
        sections["top_stories_all"] = _extract_articles(all_news, limit=20)
    except Exception as e:
        sections["top_stories_all"] = {"error": str(e)}

    # Key market moves (condensed)
    try:
        from skills.finance_skills import get_stock_info

        key_stocks = ["SPY", "QQQ", "DIS", "NFLX", "AAPL"]
        market_moves = []
        for ticker in key_stocks:
            stock_data = await get_stock_info(ticker)
            if stock_data.get("status") == "ok":
                market_moves.append(stock_data)
        sections["key_market_moves"] = market_moves
    except Exception as e:
        sections["key_market_moves"] = {"error": str(e)}

    # Major sports results (condensed)
    try:
        from skills.sports_skills import get_nba_scores

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        scores = await get_nba_scores(date=yesterday)
        if scores.get("status") == "ok":
            sections["major_sports_results"] = scores.get("games", [])[:3]
    except Exception as e:
        sections["major_sports_results"] = {"error": str(e)}

    # Tech highlights (condensed)
    try:
        from skills.news_skills import search_news

        tech_news = await search_news(
            query="technology OR AI",
            from_date=params["date_from"],
            to_date=params["date_to"],
            page_size=5,
        )
        sections["tech_highlights"] = _extract_articles(tech_news, limit=5)
    except Exception as e:
        sections["tech_highlights"] = {"error": str(e)}

    # Entertainment updates (condensed)
    try:
        from skills.news_skills import search_news

        ent_news = await search_news(
            query="entertainment OR movies OR streaming",
            from_date=params["date_from"],
            to_date=params["date_to"],
            page_size=5,
        )
        sections["entertainment_updates"] = _extract_articles(ent_news, limit=5)
    except Exception as e:
        sections["entertainment_updates"] = {"error": str(e)}

    return {
        "title": config["name"],
        "period": f"{params['date_from']} to {params['date_to']}",
        "sections": sections,
        "summary": _generate_summary(sections, "everything"),
        "generated_at": datetime.now().isoformat(),
    }


# ============================================================================
# Utility functions
# ============================================================================


def _parse_date_range(date_range: str) -> int:
    """Parse date range string to number of days."""
    date_range = date_range.lower().strip()

    try:
        if date_range.endswith("d"):
            return int(date_range[:-1])
        elif date_range.endswith("w"):
            return int(date_range[:-1]) * 7
        elif date_range.endswith("m"):
            return int(date_range[:-1]) * 30
        elif date_range.isdigit():
            return int(date_range)
        else:
            return 7  # Default to 7 days
    except (ValueError, IndexError):
        return 7  # Fallback for any parsing errors


def _extract_articles(news_response: dict, limit: int = 10) -> list[dict]:
    """Extract and format articles from news API response."""
    if news_response.get("status") != "ok":
        return []

    articles = news_response.get("articles", [])
    return [
        {
            "title": article.get("title"),
            "description": article.get("description"),
            "url": article.get("url"),
            "source": article.get("source", {}).get("name"),
            "publishedAt": article.get("publishedAt"),
        }
        for article in articles[:limit]
    ]


def _extract_games(schedule_response: dict, limit: int = 5) -> list[dict]:
    """Extract and format games from sports API response."""
    if schedule_response.get("status") != "ok":
        return []

    games = schedule_response.get("games", [])
    return [
        {
            "date": game.get("date"),
            "home": game.get("teams", {}).get("home", {}).get("name"),
            "away": game.get("teams", {}).get("away", {}).get("name"),
            "time": game.get("time"),
        }
        for game in games[:limit]
    ]


def _generate_summary(sections: dict, template_type: str) -> str:
    """Generate a brief summary of the recap."""
    summary_parts = []

    # Count successful sections
    successful_sections = sum(
        1 for section_data in sections.values() if not isinstance(section_data, dict) or "error" not in section_data
    )

    summary_parts.append(f"Generated {successful_sections} sections for {template_type} recap.")

    # Add type-specific summary
    if template_type == "entertainment":
        if "box_office_top_5" in sections and sections["box_office_top_5"]:
            summary_parts.append("Includes box office and streaming updates.")
    elif template_type == "sports":
        if "nba_recent_games" in sections:
            games_count = len(sections["nba_recent_games"]) if isinstance(sections["nba_recent_games"], list) else 0
            summary_parts.append(f"Covers {games_count} recent NBA games.")
    elif template_type == "tech":
        if "top_tech_headlines" in sections:
            summary_parts.append("Features latest tech news and stock performance.")
    elif template_type == "finance":
        if "market_summary" in sections:
            summary_parts.append("Includes market indices and economic indicators.")
    elif template_type == "everything":
        summary_parts.append("Condensed view across all major categories.")

    return " ".join(summary_parts)
