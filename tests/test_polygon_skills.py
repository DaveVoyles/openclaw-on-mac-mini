"""
Tests for Polygon.io financial data skills
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skills.polygon_skills import (
    _cache,
    _check_circuit_breaker,
    _circuit_breaker,
    _get_cached,
    _record_failure,
    _record_success,
    _sessions,
    _set_cache,
    get_market_movers,
    get_market_status,
    get_stock_history,
    get_stock_quote,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset cache and circuit breaker state before each test."""
    _cache.clear()
    _circuit_breaker["failures"] = 0
    _circuit_breaker["last_failure"] = None
    _circuit_breaker["is_open"] = False
    yield
    _cache.clear()
    _circuit_breaker["failures"] = 0
    _circuit_breaker["last_failure"] = None
    _circuit_breaker["is_open"] = False


class TestCaching:
    """Test caching functionality."""

    def test_cache_miss(self):
        """Test cache miss returns None."""
        result = _get_cached("test_key")
        assert result is None

    def test_cache_hit(self):
        """Test cache hit returns cached value."""
        test_data = {"status": "ok", "value": 123}
        _set_cache("test_key", test_data)
        result = _get_cached("test_key")
        assert result == test_data

    def test_cache_expiration(self):
        """Test that expired cache entries return None."""
        test_data = {"status": "ok", "value": 123}
        _set_cache("test_key", test_data)

        # Manually expire the cache entry
        _cache["test_key"] = (test_data, datetime(2020, 1, 1))

        result = _get_cached("test_key")
        assert result is None
        assert "test_key" not in _cache


class TestCircuitBreaker:
    """Test circuit breaker functionality."""

    def test_circuit_breaker_closed_initially(self):
        """Test circuit breaker is closed initially."""
        result = _check_circuit_breaker()
        assert result is None

    def test_circuit_breaker_opens_after_failures(self):
        """Test circuit breaker opens after 3 failures."""
        _record_failure()
        _record_failure()
        assert _check_circuit_breaker() is None

        _record_failure()
        result = _check_circuit_breaker()
        assert result is not None
        assert result["status"] == "error"
        assert "circuit breaker" in result["message"].lower()

    def test_circuit_breaker_success_reduces_failures(self):
        """Test successful calls reduce failure count."""
        _record_failure()
        _record_failure()
        assert _circuit_breaker["failures"] == 2

        _record_success()
        assert _circuit_breaker["failures"] == 1

    def test_circuit_breaker_resets_after_timeout(self):
        """Test circuit breaker resets after 60 seconds."""
        _record_failure()
        _record_failure()
        _record_failure()
        assert _circuit_breaker["is_open"] is True

        # Manually set last_failure to 61 seconds ago
        _circuit_breaker["last_failure"] = datetime(2020, 1, 1)

        result = _check_circuit_breaker()
        assert result is None
        assert _circuit_breaker["is_open"] is False
        assert _circuit_breaker["failures"] == 0


class TestGetStockQuote:
    """Test get_stock_quote function."""

    @pytest.mark.asyncio
    async def test_missing_api_key(self):
        """Test error when API key is missing."""
        with patch("skills.polygon_skills.cfg") as mock_cfg:
            mock_cfg.polygon_api_key = ""
            result = await get_stock_quote("AAPL")
            assert result["status"] == "error"
            assert "not configured" in result["message"]

    @pytest.mark.asyncio
    async def test_successful_quote(self):
        """Test successful stock quote retrieval."""
        mock_response_data = {
            "results": [
                {
                    "c": 175.43,  # close
                    "o": 173.50,  # open
                    "h": 176.20,  # high
                    "l": 172.80,  # low
                    "v": 82345678,  # volume
                    "t": 1705363200000,  # timestamp
                }
            ]
        }

        with patch("skills.polygon_skills.cfg") as mock_cfg:
            mock_cfg.polygon_api_key = "test_key"

            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=mock_response_data)
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(return_value=mock_response)

            with patch.object(type(_sessions), 'get', return_value=mock_session):
                result = await get_stock_quote("AAPL")

                assert result["status"] == "ok"
                assert result["ticker"] == "AAPL"
                assert result["price"] == 175.43
                assert result["open"] == 173.50
                assert result["high"] == 176.20
                assert result["low"] == 172.80
                assert result["volume"] == 82345678

    @pytest.mark.asyncio
    async def test_rate_limit_error(self):
        """Test rate limit error handling."""
        with patch("skills.polygon_skills.cfg") as mock_cfg:
            mock_cfg.polygon_api_key = "test_key"

            mock_response = AsyncMock()
            mock_response.status = 429
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(return_value=mock_response)

            with patch.object(type(_sessions), 'get', return_value=mock_session):
                result = await get_stock_quote("AAPL")

                assert result["status"] == "error"
                assert "rate limit" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_polygon_skills_caching(self):
        """Test that successful responses are cached."""
        mock_response_data = {
            "results": [
                {
                    "c": 175.43,
                    "o": 173.50,
                    "h": 176.20,
                    "l": 172.80,
                    "v": 82345678,
                    "t": 1705363200000,
                }
            ]
        }

        with patch("skills.polygon_skills.cfg") as mock_cfg:
            mock_cfg.polygon_api_key = "test_key"

            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=mock_response_data)
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(return_value=mock_response)

            with patch.object(type(_sessions), 'get', return_value=mock_session):
                # First call - should hit API
                result1 = await get_stock_quote("AAPL")
                assert result1["status"] == "ok"
                assert "cached" not in result1

                # Second call - should use cache
                result2 = await get_stock_quote("AAPL")
                assert result2["status"] == "ok"
                assert result2.get("cached") is True


class TestGetMarketStatus:
    """Test get_market_status function."""

    @pytest.mark.asyncio
    async def test_successful_market_status(self):
        """Test successful market status retrieval."""
        mock_response_data = {
            "market": "open",
            "serverTime": "2024-01-15T14:30:00Z",
            "exchanges": {
                "nyse": "open",
                "nasdaq": "open",
                "otc": "open",
            },
            "currencies": {
                "fx": "open",
                "crypto": "open",
            }
        }

        with patch("skills.polygon_skills.cfg") as mock_cfg:
            mock_cfg.polygon_api_key = "test_key"

            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=mock_response_data)
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(return_value=mock_response)

            with patch.object(type(_sessions), 'get', return_value=mock_session):
                result = await get_market_status()

                assert result["status"] == "ok"
                assert result["market"] == "open"
                assert result["exchanges"]["nyse"] == "open"
                assert result["exchanges"]["nasdaq"] == "open"


class TestGetStockHistory:
    """Test get_stock_history function."""

    @pytest.mark.asyncio
    async def test_successful_history(self):
        """Test successful historical data retrieval."""
        mock_response_data = {
            "results": [
                {
                    "t": 1705363200000,
                    "o": 173.50,
                    "h": 176.20,
                    "l": 172.80,
                    "c": 175.43,
                    "v": 82345678,
                },
                {
                    "t": 1705449600000,
                    "o": 175.50,
                    "h": 178.20,
                    "l": 174.80,
                    "c": 177.43,
                    "v": 92345678,
                },
            ]
        }

        with patch("skills.polygon_skills.cfg") as mock_cfg:
            mock_cfg.polygon_api_key = "test_key"

            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=mock_response_data)
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(return_value=mock_response)

            with patch.object(type(_sessions), 'get', return_value=mock_session):
                result = await get_stock_history("AAPL", days=30)

                assert result["status"] == "ok"
                assert result["ticker"] == "AAPL"
                assert result["days"] == 30
                assert result["count"] == 2
                assert len(result["data"]) == 2
                assert result["data"][0]["close"] == 175.43


class TestGetMarketMovers:
    """Test get_market_movers function."""

    @pytest.mark.asyncio
    async def test_market_gainers(self):
        """Test market gainers retrieval."""
        with patch("skills.polygon_skills.cfg") as mock_cfg:
            mock_cfg.polygon_api_key = "test_key"

            with patch("skills.polygon_skills.get_stock_quote") as mock_quote:
                # Mock responses with different change percentages
                mock_quote.side_effect = [
                    {"status": "ok", "ticker": "AAPL", "price": 175, "change_percent": 2.5, "volume": 1000000},
                    {"status": "ok", "ticker": "MSFT", "price": 380, "change_percent": 1.2, "volume": 2000000},
                    {"status": "ok", "ticker": "GOOGL", "price": 140, "change_percent": -0.5, "volume": 1500000},
                    {"status": "ok", "ticker": "AMZN", "price": 150, "change_percent": 3.1, "volume": 1200000},
                    {"status": "ok", "ticker": "TSLA", "price": 220, "change_percent": -1.8, "volume": 3000000},
                    {"status": "ok", "ticker": "META", "price": 360, "change_percent": 0.8, "volume": 1800000},
                    {"status": "ok", "ticker": "NVDA", "price": 520, "change_percent": 4.2, "volume": 2500000},
                    {"status": "ok", "ticker": "AMD", "price": 180, "change_percent": 1.5, "volume": 1100000},
                ]

                result = await get_market_movers("gainers")

                assert result["status"] == "ok"
                assert result["direction"] == "gainers"
                assert result["count"] == 5
                # Check that NVDA (4.2%) is first
                assert result["tickers"][0]["ticker"] == "NVDA"
                assert result["tickers"][0]["change_percent"] == 4.2

    @pytest.mark.asyncio
    async def test_market_losers(self):
        """Test market losers retrieval."""
        with patch("skills.polygon_skills.cfg") as mock_cfg:
            mock_cfg.polygon_api_key = "test_key"

            with patch("skills.polygon_skills.get_stock_quote") as mock_quote:
                mock_quote.side_effect = [
                    {"status": "ok", "ticker": "AAPL", "price": 175, "change_percent": 2.5, "volume": 1000000},
                    {"status": "ok", "ticker": "MSFT", "price": 380, "change_percent": -0.5, "volume": 2000000},
                    {"status": "ok", "ticker": "GOOGL", "price": 140, "change_percent": -1.5, "volume": 1500000},
                    {"status": "ok", "ticker": "AMZN", "price": 150, "change_percent": 0.1, "volume": 1200000},
                    {"status": "ok", "ticker": "TSLA", "price": 220, "change_percent": -2.8, "volume": 3000000},
                    {"status": "ok", "ticker": "META", "price": 360, "change_percent": 0.8, "volume": 1800000},
                    {"status": "ok", "ticker": "NVDA", "price": 520, "change_percent": 1.2, "volume": 2500000},
                    {"status": "ok", "ticker": "AMD", "price": 180, "change_percent": -0.3, "volume": 1100000},
                ]

                result = await get_market_movers("losers")

                assert result["status"] == "ok"
                assert result["direction"] == "losers"
                # Check that TSLA (-2.8%) is first
                assert result["tickers"][0]["ticker"] == "TSLA"
                assert result["tickers"][0]["change_percent"] == -2.8
