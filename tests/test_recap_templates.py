"""Tests for recap templates skills."""

import pytest

# Test data imports only
from skills.recap_templates import (
    RECAP_TEMPLATES,
    _extract_articles,
    _extract_games,
    _generate_summary,
    _parse_date_range,
    apply_template,
    get_available_templates,
)


class TestRecapTemplates:
    """Test recap template functionality."""

    def test_all_templates_defined(self):
        """Verify all expected templates are defined."""
        expected = ["entertainment", "sports", "tech", "finance", "everything"]
        for template_name in expected:
            assert template_name in RECAP_TEMPLATES
            assert "name" in RECAP_TEMPLATES[template_name]
            assert "description" in RECAP_TEMPLATES[template_name]
            assert "sections" in RECAP_TEMPLATES[template_name]

    def test_get_available_templates(self):
        """Test getting available templates."""
        result = get_available_templates()
        assert "templates" in result
        assert "details" in result
        assert len(result["templates"]) == 5
        assert "entertainment" in result["templates"]
        assert "sports" in result["templates"]
        assert "tech" in result["templates"]
        assert "finance" in result["templates"]
        assert "everything" in result["templates"]

    def test_template_details(self):
        """Test template detail structure."""
        result = get_available_templates()
        for template_name, details in result["details"].items():
            assert "name" in details
            assert "description" in details
            assert "format" in details
            assert "sections" in details
            assert isinstance(details["sections"], list)
            assert len(details["sections"]) > 0

    def test_apply_template_entertainment(self):
        """Test applying entertainment template."""
        result = apply_template("entertainment", "7d")
        assert result["template"] == "entertainment"
        assert "config" in result
        assert "query_params" in result
        params = result["query_params"]
        assert "topics" in params
        assert "stocks" in params
        assert "DIS" in params["stocks"]  # Disney
        assert "NFLX" in params["stocks"]  # Netflix

    def test_apply_template_sports(self):
        """Test applying sports template."""
        result = apply_template("sports", "7d")
        assert result["template"] == "sports"
        params = result["query_params"]
        assert "topics" in params
        assert "NBA" in params["topics"] or "basketball" in params["topics"]

    def test_apply_template_tech(self):
        """Test applying tech template."""
        result = apply_template("tech", "14d")
        assert result["template"] == "tech"
        params = result["query_params"]
        assert "stocks" in params
        assert "AAPL" in params["stocks"]
        assert "GOOGL" in params["stocks"]

    def test_apply_template_finance(self):
        """Test applying finance template."""
        result = apply_template("finance", "7d")
        assert result["template"] == "finance"
        params = result["query_params"]
        assert "indices" in params
        assert "SPY" in params["indices"]

    def test_apply_template_everything(self):
        """Test applying everything template."""
        result = apply_template("everything", "7d")
        assert result["template"] == "everything"
        assert result["config"]["format"] == "condensed"

    def test_apply_template_invalid(self):
        """Test applying invalid template raises error."""
        with pytest.raises(ValueError, match="Unknown template"):
            apply_template("invalid_template", "7d")

    def test_parse_date_range_days(self):
        """Test parsing date range in days."""
        assert _parse_date_range("7d") == 7
        assert _parse_date_range("14d") == 14
        assert _parse_date_range("1d") == 1

    def test_parse_date_range_weeks(self):
        """Test parsing date range in weeks."""
        assert _parse_date_range("1w") == 7
        assert _parse_date_range("2w") == 14

    def test_parse_date_range_months(self):
        """Test parsing date range in months."""
        assert _parse_date_range("1m") == 30
        assert _parse_date_range("2m") == 60

    def test_parse_date_range_numeric(self):
        """Test parsing numeric date range."""
        assert _parse_date_range("10") == 10
        assert _parse_date_range("30") == 30

    def test_parse_date_range_default(self):
        """Test parsing invalid date range defaults to 7."""
        assert _parse_date_range("invalid") == 7
        assert _parse_date_range("") == 7

    def test_extract_articles_success(self):
        """Test extracting articles from API response."""
        mock_response = {
            "status": "ok",
            "articles": [
                {
                    "title": "Test Article 1",
                    "description": "Description 1",
                    "url": "https://example.com/1",
                    "source": {"name": "Test Source"},
                    "publishedAt": "2024-01-01T10:00:00Z",
                },
                {
                    "title": "Test Article 2",
                    "description": "Description 2",
                    "url": "https://example.com/2",
                    "source": {"name": "Test Source 2"},
                    "publishedAt": "2024-01-02T10:00:00Z",
                },
            ],
        }
        result = _extract_articles(mock_response, limit=2)
        assert len(result) == 2
        assert result[0]["title"] == "Test Article 1"
        assert result[1]["title"] == "Test Article 2"

    def test_extract_articles_limit(self):
        """Test extracting articles respects limit."""
        mock_response = {
            "status": "ok",
            "articles": [{"title": f"Article {i}"} for i in range(10)],
        }
        result = _extract_articles(mock_response, limit=3)
        assert len(result) == 3

    def test_extract_articles_error(self):
        """Test extracting articles from error response."""
        mock_response = {"status": "error", "message": "API error"}
        result = _extract_articles(mock_response)
        assert result == []

    def test_extract_games_success(self):
        """Test extracting games from API response."""
        mock_response = {
            "status": "ok",
            "games": [
                {
                    "date": "2024-01-15",
                    "teams": {
                        "home": {"name": "Lakers"},
                        "away": {"name": "Warriors"},
                    },
                    "time": "19:30",
                },
            ],
        }
        result = _extract_games(mock_response, limit=1)
        assert len(result) == 1
        assert result[0]["home"] == "Lakers"
        assert result[0]["away"] == "Warriors"

    def test_generate_summary_entertainment(self):
        """Test generating summary for entertainment recap."""
        sections = {
            "box_office_top_5": [{"title": "Movie 1"}],
            "streaming_highlights": [{"title": "Show 1"}],
        }
        summary = _generate_summary(sections, "entertainment")
        assert "entertainment" in summary
        assert "2 sections" in summary

    def test_generate_summary_sports(self):
        """Test generating summary for sports recap."""
        sections = {
            "nba_recent_games": [{"home": "Lakers"}, {"home": "Warriors"}],
        }
        summary = _generate_summary(sections, "sports")
        assert "sports" in summary
        assert "2 recent NBA games" in summary

    def test_generate_summary_with_errors(self):
        """Test summary generation with error sections."""
        sections = {
            "section1": {"error": "API failed"},
            "section2": [{"data": "success"}],
        }
        summary = _generate_summary(sections, "tech")
        assert "1 sections" in summary


class TestTemplateStructure:
    """Test template structure and completeness."""

    def test_entertainment_template_structure(self):
        """Test entertainment template has all required fields."""
        template = RECAP_TEMPLATES["entertainment"]
        assert template["name"] == "Entertainment Industry Recap"
        assert "box_office_top_5" in template["sections"]
        assert "streaming_highlights" in template["sections"]
        assert "studio_stocks" in template["sections"]
        assert "DIS" in template["stocks"]

    def test_sports_template_structure(self):
        """Test sports template has all required fields."""
        template = RECAP_TEMPLATES["sports"]
        assert template["name"] == "Sports Recap"
        assert "nba_recent_games" in template["sections"]
        assert "nba_standings_top_10" in template["sections"]
        assert "upcoming_matchups" in template["sections"]

    def test_tech_template_structure(self):
        """Test tech template has all required fields."""
        template = RECAP_TEMPLATES["tech"]
        assert template["name"] == "Tech Industry Recap"
        assert "top_tech_headlines" in template["sections"]
        assert "tech_stock_performance" in template["sections"]
        assert "AAPL" in template["stocks"]
        assert "GOOGL" in template["stocks"]

    def test_finance_template_structure(self):
        """Test finance template has all required fields."""
        template = RECAP_TEMPLATES["finance"]
        assert template["name"] == "Finance & Markets Recap"
        assert "market_summary" in template["sections"]
        assert "top_movers" in template["sections"]
        assert "SPY" in template["indices"]

    def test_everything_template_structure(self):
        """Test everything template has all required fields."""
        template = RECAP_TEMPLATES["everything"]
        assert template["name"] == "Everything Recap"
        assert template["format"] == "condensed"
        assert "top_stories_all" in template["sections"]
        assert len(template["topics"]) >= 4  # Multiple topic areas
