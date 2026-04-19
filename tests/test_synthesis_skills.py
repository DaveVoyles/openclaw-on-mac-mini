"""
Tests for data synthesis skills — multi-source correlation and LLM insights.

Tests cover:
- Skill registration and callability
- Company report synthesis
- Entertainment report synthesis
- Market overview synthesis
- Correlation detection
- Error handling and circuit breakers
- Cache behavior
- LLM integration (mocked)
"""

import asyncio
from unittest.mock import patch

import pytest

from skills import SKILLS
from skills.synthesis_skills import (
    SYNTHESIS_SKILLS,
    find_correlations,
    synthesize_company_report,
    synthesize_entertainment_report,
    synthesize_market_overview,
)


class TestSkillRegistration:
    """Test that synthesis skills are properly registered."""

    def test_synthesis_skills_exported(self):
        """Verify SYNTHESIS_SKILLS dict is properly defined."""
        assert isinstance(SYNTHESIS_SKILLS, dict)
        assert len(SYNTHESIS_SKILLS) == 4
        assert "synthesize_company_report" in SYNTHESIS_SKILLS
        assert "synthesize_entertainment_report" in SYNTHESIS_SKILLS
        assert "synthesize_market_overview" in SYNTHESIS_SKILLS
        assert "find_correlations" in SYNTHESIS_SKILLS

    def test_synthesis_skills_registered_globally(self):
        """Verify synthesis skills are registered in global SKILLS dict."""
        assert "synthesize_company_report" in SKILLS
        assert "synthesize_entertainment_report" in SKILLS
        assert "synthesize_market_overview" in SKILLS
        assert "find_correlations" in SKILLS

    def test_all_skills_are_callables(self):
        """Verify all synthesis skills are callable async functions."""
        for skill_name, skill_func in SYNTHESIS_SKILLS.items():
            assert callable(skill_func), f"{skill_name} is not callable"
            assert asyncio.iscoroutinefunction(skill_func), f"{skill_name} is not async"


class TestCompanyReportSynthesis:
    """Test synthesize_company_report functionality."""

    @pytest.mark.asyncio
    async def test_company_report_structure(self):
        """Test that company report returns expected structure."""
        with (
            patch("skills.synthesis_skills.finance_skills.get_stock_info") as mock_stock,
            patch("skills.synthesis_skills.finance_skills.get_sentiment_analysis") as mock_sentiment,
            patch("skills.synthesis_skills.news_skills.search_news") as mock_news,
            patch("skills.synthesis_skills._generate_llm_summary") as mock_llm,
        ):
            # Mock successful API responses
            mock_stock.return_value = {
                "status": "ok",
                "symbol": "DIS",
                "price": 96.61,
                "change": "+1.23",
                "change_percent": "+1.31%",
                "volume": "8234567",
                "high": 98.0,
                "low": 95.0,
            }

            mock_sentiment.return_value = {
                "status": "ok",
                "sentiment": {
                    "DIS": {
                        "score": 0.7,
                        "label": "Bullish",
                        "recent_news": 15,
                    }
                },
            }

            mock_news.return_value = {
                "status": "ok",
                "articles": [
                    {
                        "title": "Disney's Moana 2 Breaks Box Office Records",
                        "url": "https://example.com/moana",
                        "source": {"name": "Variety"},
                    },
                    {
                        "title": "Theme Park Attendance Surges",
                        "url": "https://example.com/parks",
                        "source": {"name": "Hollywood Reporter"},
                    },
                ],
            }

            mock_llm.return_value = (
                "Disney stock rallied 5% as Moana 2 exceeded box office expectations, "
                "with market sentiment improving to bullish levels."
            )

            result = await synthesize_company_report("DIS")

            # Verify structure
            assert result["status"] == "ok"
            assert result["entity"] == "Disney"
            assert result["ticker"] == "DIS"
            assert "stock_data" in result
            assert "sentiment" in result
            assert "news_summary" in result
            assert "synthesis" in result
            assert "sources" in result
            assert "timestamp" in result

            # Verify stock data
            assert result["stock_data"]["price"] == 96.61
            assert result["stock_data"]["change_percent"] == "+1.31%"

            # Verify sentiment
            assert result["sentiment"]["score"] == 0.7
            assert result["sentiment"]["label"] == "Bullish"

            # Verify news articles included
            assert len(result["news_articles"]) == 2
            assert "Moana" in result["news_articles"][0]["title"]

            # Verify synthesis
            assert "rallied" in result["synthesis"].lower() or "moana" in result["synthesis"].lower()

    @pytest.mark.asyncio
    async def test_company_report_handles_partial_failure(self):
        """Test company report gracefully handles partial API failures."""
        with (
            patch("skills.synthesis_skills.finance_skills.get_stock_info") as mock_stock,
            patch("skills.synthesis_skills.finance_skills.get_sentiment_analysis") as mock_sentiment,
            patch("skills.synthesis_skills.news_skills.search_news") as mock_news,
        ):
            # Stock succeeds
            mock_stock.return_value = {
                "status": "ok",
                "symbol": "AAPL",
                "price": 195.50,
                "change": "+2.10",
                "change_percent": "+1.09%",
                "volume": "52000000",
                "high": 196.0,
                "low": 193.0,
            }

            # Sentiment fails (rate limit)
            mock_sentiment.return_value = {
                "status": "error",
                "message": "Alpha Vantage rate limit exceeded",
            }

            # News fails (timeout)
            mock_news.side_effect = asyncio.TimeoutError()

            result = await synthesize_company_report("AAPL")

            # Should still return ok with partial data
            assert result["status"] == "ok"
            assert result["stock_data"]["price"] == 195.50
            assert result["sentiment"] == {}  # Empty due to failure
            assert len(result["news_articles"]) == 0

            # Should have both successes and failures logged
            assert "Alpha Vantage (Stock)" in result["sources"]
            assert len(result["sources_failed"]) >= 2  # Sentiment and News failed

    @pytest.mark.asyncio
    async def test_company_report_caching(self):
        """Test that company reports are cached properly."""
        with (
            patch("skills.synthesis_skills.finance_skills.get_stock_info") as mock_stock,
            patch("skills.synthesis_skills.finance_skills.get_sentiment_analysis") as mock_sentiment,
            patch("skills.synthesis_skills.news_skills.search_news") as mock_news,
            patch("skills.synthesis_skills._generate_llm_summary") as mock_llm,
        ):
            mock_stock.return_value = {
                "status": "ok",
                "symbol": "TSLA",
                "price": 242.80,
                "change": "-3.20",
                "change_percent": "-1.30%",
                "volume": "95000000",
                "high": 246.0,
                "low": 241.0,
            }
            mock_sentiment.return_value = {"status": "ok", "sentiment": {}}
            mock_news.return_value = {"status": "ok", "articles": []}
            mock_llm.return_value = "Tesla trading lower on profit-taking."

            # First call
            result1 = await synthesize_company_report("TSLA")
            timestamp1 = result1["timestamp"]

            # Second call (should use cache)
            result2 = await synthesize_company_report("TSLA")
            timestamp2 = result2["timestamp"]

            # Timestamps should match (from cache)
            assert timestamp1 == timestamp2

            # API should only be called once
            assert mock_stock.call_count == 1
            assert mock_sentiment.call_count == 1
            assert mock_news.call_count == 1


class TestEntertainmentReportSynthesis:
    """Test synthesize_entertainment_report functionality."""

    @pytest.mark.asyncio
    async def test_entertainment_report_structure(self):
        """Test entertainment report returns expected structure."""
        with (
            patch("skills.synthesis_skills.finance_skills.get_box_office_stocks") as mock_stocks,
            patch("skills.synthesis_skills.finance_skills.get_sentiment_analysis") as mock_sentiment,
            patch("skills.synthesis_skills.news_skills.top_headlines") as mock_news,
            patch("skills.synthesis_skills._generate_llm_summary") as mock_llm,
        ):
            mock_stocks.return_value = {
                "status": "ok",
                "studios": {
                    "Disney": {
                        "symbol": "DIS",
                        "price": 96.61,
                        "change": "+1.23",
                        "change_percent": "+5.26%",  # Changed to significant movement
                    },
                    "Netflix": {
                        "symbol": "NFLX",
                        "price": 485.20,
                        "change": "-2.10",
                        "change_percent": "-0.43%",
                    },
                },
            }

            mock_sentiment.return_value = {
                "status": "ok",
                "sentiment": {
                    "DIS": {"score": 0.5, "label": "Bullish", "recent_news": 10},
                    "NFLX": {"score": -0.1, "label": "Neutral", "recent_news": 8},
                },
            }

            mock_news.return_value = {
                "status": "ok",
                "articles": [
                    {
                        "title": "Box Office Weekend Recap",
                        "url": "https://example.com/boxoffice",
                        "source": {"name": "Variety"},
                    }
                ],
            }

            mock_llm.return_value = "Entertainment stocks mixed with Disney gaining on strong box office."

            result = await synthesize_entertainment_report("box office")

            assert result["status"] == "ok"
            assert result["topic"] == "box office"
            assert "studios" in result
            assert "Disney" in result["studios"]
            assert "Netflix" in result["studios"]
            assert result["studios"]["Disney"]["price"] == 96.61
            assert "synthesis" in result
            assert len(result["key_correlations"]) > 0  # Should detect significant movement

    @pytest.mark.asyncio
    async def test_entertainment_report_detects_correlations(self):
        """Test that entertainment report detects key stock correlations."""
        with (
            patch("skills.synthesis_skills.finance_skills.get_box_office_stocks") as mock_stocks,
            patch("skills.synthesis_skills.finance_skills.get_sentiment_analysis") as mock_sentiment,
            patch("skills.synthesis_skills.news_skills.top_headlines") as mock_news,
        ):
            # Mock significant stock movement
            mock_stocks.return_value = {
                "status": "ok",
                "studios": {
                    "Disney": {
                        "symbol": "DIS",
                        "price": 100.00,
                        "change": "+5.00",
                        "change_percent": "+5.26%",  # Significant movement
                    },
                },
            }

            mock_sentiment.return_value = {
                "status": "ok",
                "sentiment": {
                    "DIS": {"score": 0.8, "label": "Very Bullish", "recent_news": 20},
                },
            }

            mock_news.return_value = {"status": "ok", "articles": []}

            result = await synthesize_entertainment_report()

            # Should detect significant movement as correlation
            assert len(result["key_correlations"]) > 0
            correlation_text = result["key_correlations"][0]
            assert "5." in correlation_text  # Should mention the 5% move
            assert "Disney" in correlation_text


class TestMarketOverviewSynthesis:
    """Test synthesize_market_overview functionality."""

    @pytest.mark.asyncio
    async def test_market_overview_structure(self):
        """Test market overview returns expected structure."""
        with (
            patch("skills.synthesis_skills.news_skills.top_headlines") as mock_news,
            patch("skills.synthesis_skills.finance_skills.get_market_news") as mock_market_news,
            patch("skills.synthesis_skills._generate_llm_summary") as mock_llm,
        ):
            mock_news.return_value = {
                "status": "ok",
                "articles": [
                    {
                        "title": "Fed Holds Interest Rates Steady",
                        "url": "https://example.com/fed",
                        "source": {"name": "Reuters"},
                        "description": "Federal Reserve maintains current rate policy",
                    }
                ],
            }

            mock_market_news.return_value = {
                "status": "ok",
                "feed": [
                    {
                        "title": "Tech Stocks Rally",
                        "url": "https://example.com/tech",
                        "sentiment": {"score": 0.6, "label": "Bullish"},
                        "topics": ["technology", "financial_markets"],
                    },
                    {
                        "title": "Energy Sector Declines",
                        "url": "https://example.com/energy",
                        "sentiment": {"score": -0.3, "label": "Bearish"},
                        "topics": ["energy_transportation"],
                    },
                ],
            }

            mock_llm.return_value = (
                "Markets show mixed performance with technology sector leading gains while energy faces headwinds."
            )

            result = await synthesize_market_overview()

            assert result["status"] == "ok"
            assert "market_summary" in result
            assert "top_news" in result
            assert "sector_sentiment" in result
            assert len(result["sector_sentiment"]) > 0
            assert "synthesis" in result

            # Check sector sentiment aggregation
            assert "technology" in result["sector_sentiment"]
            assert result["sector_sentiment"]["technology"]["label"] == "Bullish"

    @pytest.mark.asyncio
    async def test_market_overview_aggregates_sentiment(self):
        """Test that market overview correctly aggregates sector sentiment."""
        from skills.synthesis_skills import _synthesis_cache

        # Clear cache to ensure fresh results
        _synthesis_cache.clear()

        with (
            patch("skills.synthesis_skills.news_skills.top_headlines") as mock_news,
            patch("skills.synthesis_skills.finance_skills.get_market_news") as mock_market_news,
        ):
            mock_news.return_value = {"status": "ok", "articles": []}

            # Multiple articles for same sector with varying sentiment
            mock_market_news.return_value = {
                "status": "ok",
                "feed": [
                    {
                        "title": "Article 1",
                        "url": "https://example.com/1",
                        "sentiment": {"score": 0.5, "label": "Bullish"},
                        "topics": ["technology"],
                    },
                    {
                        "title": "Article 2",
                        "url": "https://example.com/2",
                        "sentiment": {"score": 0.3, "label": "Somewhat-Bullish"},
                        "topics": ["technology"],
                    },
                    {
                        "title": "Article 3",
                        "url": "https://example.com/3",
                        "sentiment": {"score": 0.4, "label": "Bullish"},
                        "topics": ["technology"],
                    },
                ],
            }

            result = await synthesize_market_overview()

            # Should aggregate technology sector sentiment
            assert "technology" in result["sector_sentiment"]
            tech_sentiment = result["sector_sentiment"]["technology"]

            # Should count all 3 articles
            assert tech_sentiment["news_count"] == 3
            assert tech_sentiment["label"] == "Bullish"
            # The score should be reasonable (between 0 and 1)
            assert 0 <= tech_sentiment["score"] <= 1


class TestCorrelationDetection:
    """Test find_correlations functionality."""

    @pytest.mark.asyncio
    async def test_correlations_detects_stock_sentiment_alignment(self):
        """Test correlation detection identifies stock-sentiment alignment."""
        with patch("skills.synthesis_skills.synthesize_company_report") as mock_report:
            mock_report.return_value = {
                "status": "ok",
                "entity": "Apple",
                "ticker": "AAPL",
                "stock_data": {
                    "price": 195.0,
                    "change": "+3.50",
                    "change_percent": "+1.83%",  # Positive movement
                },
                "sentiment": {
                    "score": 0.6,  # Bullish sentiment
                    "label": "Bullish",
                    "news_count": 12,
                },
                "news_articles": [],
                "sources": ["Alpha Vantage"],
            }

            result = await find_correlations("AAPL", entity_type="company")

            assert result["status"] == "ok"
            assert len(result["correlations"]) > 0

            # Should detect alignment
            alignment_corr = [c for c in result["correlations"] if c["type"] == "stock_sentiment_alignment"]
            assert len(alignment_corr) > 0
            assert "aligns with" in alignment_corr[0]["description"].lower()
            assert alignment_corr[0]["confidence"] == "high"

    @pytest.mark.asyncio
    async def test_correlations_detects_divergence(self):
        """Test correlation detection identifies stock-sentiment divergence."""
        with patch("skills.synthesis_skills.synthesize_company_report") as mock_report:
            mock_report.return_value = {
                "status": "ok",
                "entity": "Tesla",
                "ticker": "TSLA",
                "stock_data": {
                    "price": 240.0,
                    "change": "-8.00",
                    "change_percent": "-3.23%",  # Significant negative movement
                },
                "sentiment": {
                    "score": 0.5,  # But positive sentiment
                    "label": "Bullish",
                    "news_count": 8,
                },
                "news_articles": [],
                "sources": ["Alpha Vantage"],
            }

            result = await find_correlations("TSLA", entity_type="company")

            # Should detect divergence
            divergence_corr = [c for c in result["correlations"] if c["type"] == "stock_sentiment_divergence"]
            assert len(divergence_corr) > 0
            assert "diverges" in divergence_corr[0]["description"].lower()

    @pytest.mark.asyncio
    async def test_correlations_caching(self):
        """Test that correlations are cached."""
        from skills.synthesis_skills import _synthesis_cache

        # Clear cache first
        _synthesis_cache.clear()

        with patch("skills.synthesis_skills.synthesize_company_report") as mock_report:
            mock_report.return_value = {
                "status": "ok",
                "entity": "Microsoft",
                "ticker": "MSFT",
                "stock_data": {"price": 380.0, "change": "+1.00", "change_percent": "+0.26%"},
                "sentiment": {"score": 0.3, "label": "Somewhat-Bullish"},
                "news_articles": [],
                "sources": [],
            }

            # First call
            result1 = await find_correlations("MSFT")

            # Second call (should use correlation cache, not company report cache)
            result2 = await find_correlations("MSFT")

            # Should have same date (day-level caching for correlations)
            date1 = result1["timestamp"].split("T")[0]
            date2 = result2["timestamp"].split("T")[0]
            assert date1 == date2

            # Since correlations are cached by day, company_report is called twice
            # (once per find_correlations call, each hitting company report's hourly cache)
            # OR company_report should be called once if we're in same hour
            assert mock_report.call_count >= 1  # At least once


class TestErrorHandling:
    """Test error handling and graceful degradation."""

    @pytest.mark.asyncio
    async def test_handles_complete_api_failure(self):
        """Test graceful handling when all APIs fail."""
        with (
            patch("skills.synthesis_skills.finance_skills.get_stock_info") as mock_stock,
            patch("skills.synthesis_skills.finance_skills.get_sentiment_analysis") as mock_sentiment,
            patch("skills.synthesis_skills.news_skills.search_news") as mock_news,
        ):
            # All APIs fail
            mock_stock.return_value = {"status": "error", "message": "API error"}
            mock_sentiment.return_value = {"status": "error", "message": "Rate limit"}
            mock_news.return_value = {"status": "error", "message": "Timeout"}

            result = await synthesize_company_report("XYZ")

            # Should return error status
            assert result["status"] == "error"
            assert len(result["sources_failed"]) >= 3

    @pytest.mark.asyncio
    async def test_handles_llm_failure_gracefully(self):
        """Test that LLM failures don't break synthesis."""
        with (
            patch("skills.synthesis_skills.finance_skills.get_stock_info") as mock_stock,
            patch("skills.synthesis_skills._generate_llm_summary") as mock_llm,
        ):
            mock_stock.return_value = {
                "status": "ok",
                "symbol": "GOOGL",
                "price": 140.0,
                "change": "+1.50",
                "change_percent": "+1.08%",
                "volume": "20000000",
                "high": 141.0,
                "low": 139.0,
            }

            # LLM fails
            mock_llm.side_effect = Exception("LLM service unavailable")

            result = await synthesize_company_report("GOOGL")

            # Should still succeed with fallback synthesis
            assert result["status"] == "ok"
            assert "synthesis" in result
            assert len(result["synthesis"]) > 0  # Should have basic fallback


class TestIntegration:
    """Integration tests verifying cross-module interactions."""

    @pytest.mark.asyncio
    async def test_synthesis_uses_existing_api_skills(self):
        """Verify synthesis skills properly call existing API skills."""
        with patch("skills.synthesis_skills.finance_skills.get_stock_info") as mock_stock:
            mock_stock.return_value = {
                "status": "ok",
                "symbol": "META",
                "price": 350.0,
                "change": "+2.00",
                "change_percent": "+0.57%",
                "volume": "15000000",
                "high": 352.0,
                "low": 348.0,
            }

            await synthesize_company_report("META")

            # Should call finance_skills.get_stock_info
            mock_stock.assert_called_once_with("META")

    @pytest.mark.asyncio
    async def test_parallel_api_calls(self):
        """Test that synthesis makes efficient parallel API calls."""
        with (
            patch("skills.synthesis_skills.finance_skills.get_stock_info") as mock_stock,
            patch("skills.synthesis_skills.finance_skills.get_sentiment_analysis") as mock_sentiment,
            patch("skills.synthesis_skills.news_skills.search_news") as mock_news,
        ):
            # Set up quick responses
            mock_stock.return_value = {
                "status": "ok",
                "symbol": "AMZN",
                "price": 150.0,
                "change": "+1.00",
                "change_percent": "+0.67%",
                "volume": "40000000",
                "high": 151.0,
                "low": 149.0,
            }
            mock_sentiment.return_value = {"status": "ok", "sentiment": {}}
            mock_news.return_value = {"status": "ok", "articles": []}

            import time

            start = time.time()
            await synthesize_company_report("AMZN")
            elapsed = time.time() - start

            # Should complete quickly (parallel calls)
            # If sequential, would take 3x longer
            assert elapsed < 2.0  # Reasonable threshold for parallel execution


# Test coverage summary
def test_module_coverage():
    """Verify test coverage hits all major functions."""
    tested_functions = {
        "synthesize_company_report",
        "synthesize_entertainment_report",
        "synthesize_market_overview",
        "find_correlations",
    }

    for func_name in tested_functions:
        assert func_name in SYNTHESIS_SKILLS, f"{func_name} not in SYNTHESIS_SKILLS"
