"""
Tests for data visualization module
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.visualization import (
    _chart_cache,
    _get_cache_key,
    clear_chart_cache,
    create_comparison_chart,
    create_stock_chart,
    create_trend_chart,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear cache before each test."""
    _chart_cache.clear()
    yield
    _chart_cache.clear()


@pytest.fixture
def sample_stock_data():
    """Sample stock data for testing."""
    return {
        "ticker": "AAPL",
        "data": [
            {"date": "2024-01-10", "open": 170.0, "high": 172.0, "low": 169.0, "close": 171.0, "volume": 1000000},
            {"date": "2024-01-11", "open": 171.0, "high": 173.0, "low": 170.0, "close": 172.5, "volume": 1100000},
            {"date": "2024-01-12", "open": 172.5, "high": 175.0, "low": 172.0, "close": 174.0, "volume": 1200000},
            {"date": "2024-01-15", "open": 174.0, "high": 176.0, "low": 173.0, "close": 175.5, "volume": 1300000},
        ]
    }


@pytest.fixture
def sample_trend_data():
    """Sample trend data for testing."""
    return {
        "ticker": "MSFT",
        "data": [
            {"date": "2024-01-10", "value": 380.0},
            {"date": "2024-01-11", "value": 382.5},
            {"date": "2024-01-12", "value": 385.0},
            {"date": "2024-01-15", "value": 387.5},
        ]
    }


@pytest.fixture
def sample_comparison_data():
    """Sample comparison data for testing."""
    return {
        "assets": [
            {
                "ticker": "AAPL",
                "data": [
                    {"date": "2024-01-10", "value": 170.0},
                    {"date": "2024-01-11", "value": 172.5},
                    {"date": "2024-01-12", "value": 174.0},
                ]
            },
            {
                "ticker": "MSFT",
                "data": [
                    {"date": "2024-01-10", "value": 380.0},
                    {"date": "2024-01-11", "value": 382.5},
                    {"date": "2024-01-12", "value": 385.0},
                ]
            }
        ]
    }


class TestCacheKey:
    """Test cache key generation."""

    def test_cache_key_generation(self):
        """Test that cache keys are generated consistently."""
        data1 = {"ticker": "AAPL", "data": [{"date": "2024-01-10", "value": 170}]}
        data2 = {"ticker": "AAPL", "data": [{"date": "2024-01-10", "value": 170}]}

        key1 = _get_cache_key(data1, "candlestick_png")
        key2 = _get_cache_key(data2, "candlestick_png")

        assert key1 == key2

    def test_different_data_different_keys(self):
        """Test that different data produces different keys."""
        data1 = {"ticker": "AAPL", "data": [{"date": "2024-01-10", "value": 170}]}
        data2 = {"ticker": "MSFT", "data": [{"date": "2024-01-10", "value": 380}]}

        key1 = _get_cache_key(data1, "candlestick_png")
        key2 = _get_cache_key(data2, "candlestick_png")

        assert key1 != key2


class TestCreateStockChart:
    """Test create_stock_chart function."""

    @patch("src.visualization._save_chart")
    def test_create_candlestick_chart(self, mock_save, sample_stock_data):
        """Test candlestick chart creation."""
        mock_path = Path("data/charts/test123.png")
        mock_save.return_value = mock_path

        result = create_stock_chart(sample_stock_data, chart_type="candlestick", format="png")

        assert result["status"] == "ok"
        assert result["chart_type"] == "candlestick"
        assert result["ticker"] == "AAPL"
        assert result["cached"] is False
        assert mock_save.called

    @patch("src.visualization._save_chart")
    def test_create_line_chart(self, mock_save, sample_stock_data):
        """Test line chart creation."""
        mock_path = Path("data/charts/test456.png")
        mock_save.return_value = mock_path

        result = create_stock_chart(sample_stock_data, chart_type="line", format="png")

        assert result["status"] == "ok"
        assert result["chart_type"] == "line"
        assert result["ticker"] == "AAPL"
        assert mock_save.called

    def test_empty_data_error(self):
        """Test error handling for empty data."""
        empty_data = {"ticker": "AAPL", "data": []}

        result = create_stock_chart(empty_data)

        assert result["status"] == "error"
        assert "No data" in result["message"]

    @patch("src.visualization._save_chart")
    @patch("src.visualization._get_cached_chart")
    def test_caching(self, mock_get_cached, mock_save, sample_stock_data):
        """Test that charts are cached properly."""
        # First call - no cache
        mock_get_cached.return_value = None
        mock_path = Path("data/charts/test789.png")
        mock_save.return_value = mock_path

        result1 = create_stock_chart(sample_stock_data)
        assert result1["cached"] is False

        # Second call - with cache
        mock_get_cached.return_value = mock_path
        result2 = create_stock_chart(sample_stock_data)
        assert result2["cached"] is True


class TestCreateTrendChart:
    """Test create_trend_chart function."""

    @patch("src.visualization._save_chart")
    def test_create_trend_chart(self, mock_save, sample_trend_data):
        """Test trend chart creation."""
        mock_path = Path("data/charts/trend123.png")
        mock_save.return_value = mock_path

        result = create_trend_chart(sample_trend_data, format="png")

        assert result["status"] == "ok"
        assert result["ticker"] == "MSFT"
        assert result["cached"] is False
        assert mock_save.called

    def test_trend_with_close_prices(self):
        """Test trend chart with close prices instead of values."""
        data = {
            "ticker": "TSLA",
            "data": [
                {"date": "2024-01-10", "close": 220.0},
                {"date": "2024-01-11", "close": 225.0},
            ]
        }

        with patch("src.visualization._save_chart") as mock_save:
            mock_save.return_value = Path("data/charts/trend456.png")
            result = create_trend_chart(data)

            assert result["status"] == "ok"
            assert result["ticker"] == "TSLA"

    def test_empty_trend_data(self):
        """Test error handling for empty trend data."""
        empty_data = {"ticker": "AAPL", "data": []}

        result = create_trend_chart(empty_data)

        assert result["status"] == "error"
        assert "No data" in result["message"]


class TestCreateComparisonChart:
    """Test create_comparison_chart function."""

    @patch("src.visualization._save_chart")
    def test_create_comparison_chart(self, mock_save, sample_comparison_data):
        """Test comparison chart creation."""
        mock_path = Path("data/charts/comp123.png")
        mock_save.return_value = mock_path

        result = create_comparison_chart(sample_comparison_data, format="png")

        assert result["status"] == "ok"
        assert "AAPL" in result["assets"]
        assert "MSFT" in result["assets"]
        assert result["cached"] is False
        assert mock_save.called

    def test_empty_assets_error(self):
        """Test error handling for no assets."""
        empty_data = {"assets": []}

        result = create_comparison_chart(empty_data)

        assert result["status"] == "error"
        assert "No assets" in result["message"]

    @patch("src.visualization._save_chart")
    def test_multiple_assets(self, mock_save):
        """Test comparison with multiple assets."""
        data = {
            "assets": [
                {
                    "ticker": "AAPL",
                    "data": [{"date": "2024-01-10", "value": 170}]
                },
                {
                    "ticker": "MSFT",
                    "data": [{"date": "2024-01-10", "value": 380}]
                },
                {
                    "ticker": "GOOGL",
                    "data": [{"date": "2024-01-10", "value": 140}]
                }
            ]
        }

        mock_save.return_value = Path("data/charts/comp456.png")
        result = create_comparison_chart(data)

        assert result["status"] == "ok"
        assert len(result["assets"]) == 3


class TestClearCache:
    """Test clear_chart_cache function."""

    def test_clear_empty_cache(self):
        """Test clearing an empty cache."""
        result = clear_chart_cache()

        assert result["status"] == "ok"
        assert result["cleared"] == 0

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    def test_clear_cache_with_files(self, mock_unlink, mock_exists):
        """Test clearing cache with files."""
        # Populate cache
        from datetime import datetime
        _chart_cache["key1"] = (Path("data/charts/test1.png"), datetime.now())
        _chart_cache["key2"] = (Path("data/charts/test2.png"), datetime.now())

        mock_exists.return_value = True

        result = clear_chart_cache()

        assert result["status"] == "ok"
        assert result["cleared"] == 2
        assert len(_chart_cache) == 0
