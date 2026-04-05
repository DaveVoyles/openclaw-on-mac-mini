"""
Quick test to verify new API skills are registered and callable.
Not testing actual API calls (would hit rate limits), just structure.
"""

import pytest

from skills import SKILLS


def test_news_skills_registered():
    """Verify NewsAPI skills are registered."""
    assert "search_news" in SKILLS
    assert "top_headlines" in SKILLS
    assert "news_by_source" in SKILLS


def test_sports_skills_registered():
    """Verify API-Sports skills are registered."""
    assert "get_nba_scores" in SKILLS
    assert "get_nfl_scores" in SKILLS
    assert "get_team_standings" in SKILLS
    assert "get_schedule" in SKILLS


def test_finance_skills_registered():
    """Verify Alpha Vantage skills are registered."""
    assert "get_stock_info" in SKILLS
    assert "get_market_news" in SKILLS
    assert "get_sentiment_analysis" in SKILLS
    assert "get_box_office_stocks" in SKILLS


def test_skills_are_callables():
    """Verify all new skills are callable functions."""
    new_skills = [
        "search_news",
        "top_headlines",
        "news_by_source",
        "get_nba_scores",
        "get_nfl_scores",
        "get_team_standings",
        "get_schedule",
        "get_stock_info",
        "get_market_news",
        "get_sentiment_analysis",
        "get_box_office_stocks",
    ]

    for skill_name in new_skills:
        skill = SKILLS[skill_name]
        assert callable(skill), f"{skill_name} is not callable"


@pytest.mark.asyncio
async def test_news_api_no_key_handling():
    """Test graceful error when API key not set."""
    from skills.news_skills import search_news

    # This should return error dict, not crash
    result = await search_news("test query")

    assert "status" in result
    # Without API key, should fail gracefully
    if result["status"] == "error":
        assert "message" in result


@pytest.mark.asyncio
async def test_sports_api_no_key_handling():
    """Test graceful error when API key not set."""
    from skills.sports_skills import get_nba_scores

    result = await get_nba_scores()

    assert "status" in result
    if result["status"] == "error":
        assert "message" in result


@pytest.mark.asyncio
async def test_finance_api_no_key_handling():
    """Test graceful error when API key not set."""
    from skills.finance_skills import get_stock_info

    result = await get_stock_info("DIS")

    assert "status" in result
    if result["status"] == "error":
        assert "message" in result
