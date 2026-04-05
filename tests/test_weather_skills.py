"""
Tests for weather_skills.py — OpenWeatherMap API integration.
"""

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Basic config tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_weather_no_api_key():
    """Test error when API key not configured."""
    from skills.weather_skills import get_current_weather

    with patch("skills.weather_skills.cfg.openweather_api_key", ""):
        result = await get_current_weather("Seattle")
        assert result["status"] == "error"
        assert "not configured" in result["message"]


@pytest.mark.asyncio
async def test_get_forecast_no_api_key():
    """Test error when API key not configured."""
    from skills.weather_skills import get_forecast

    with patch("skills.weather_skills.cfg.openweather_api_key", ""):
        result = await get_forecast("Seattle")
        assert result["status"] == "error"
        assert "not configured" in result["message"]


@pytest.mark.asyncio
async def test_get_air_quality_no_api_key():
    """Test error when API key not configured."""
    from skills.weather_skills import get_air_quality

    with patch("skills.weather_skills.cfg.openweather_api_key", ""):
        result = await get_air_quality("Seattle")
        assert result["status"] == "error"
        assert "not configured" in result["message"]


# ---------------------------------------------------------------------------
# WEATHER_SKILLS export tests
# ---------------------------------------------------------------------------


def test_weather_skills_export():
    """Test that WEATHER_SKILLS is properly configured."""
    from skills.weather_skills import WEATHER_SKILLS

    assert len(WEATHER_SKILLS) == 3

    skill_names = [s["name"] for s in WEATHER_SKILLS]
    assert "get_current_weather" in skill_names
    assert "get_forecast" in skill_names
    assert "get_air_quality" in skill_names

    for skill in WEATHER_SKILLS:
        assert "name" in skill
        assert "function" in skill
        assert "description" in skill
        assert callable(skill["function"])
        assert len(skill["description"]) > 0
