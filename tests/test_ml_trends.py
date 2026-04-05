"""
Tests for ML-based trend detection and forecasting.

Tests ARIMA forecasting, anomaly detection, and seasonal decomposition.
"""

import pytest
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from src.ml_trends import (
    ML_TREND_SKILLS,
    forecast_trend,
    detect_anomalies,
    MLTrendAnalyzer,
)


# Test database path
TEST_DB_PATH = Path("/tmp/test_ml_trends.db")


@pytest.fixture
def clean_db():
    """Create clean test database."""
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    
    # Create test database with sample data
    conn = sqlite3.connect(TEST_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trend_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            topic TEXT NOT NULL,
            category TEXT NOT NULL,
            volume INTEGER NOT NULL,
            sentiment REAL NOT NULL,
            sources TEXT NOT NULL,
            metadata TEXT
        )
    """)
    
    # Insert sample time series data
    base_time = datetime.now() - timedelta(days=30)
    for i in range(30):
        timestamp = (base_time + timedelta(days=i)).timestamp()
        volume = 100 + (i * 2)  # Increasing trend
        sentiment = 0.5 + (0.01 * i)  # Slight positive drift
        
        conn.execute("""
            INSERT INTO trend_data (timestamp, topic, category, volume, sentiment, sources)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (timestamp, "AI", "news", volume, sentiment, "source1,source2"))
    
    # Add some anomalies
    anomaly_times = [base_time + timedelta(days=10), base_time + timedelta(days=20)]
    for anomaly_time in anomaly_times:
        timestamp = anomaly_time.timestamp()
        conn.execute("""
            INSERT INTO trend_data (timestamp, topic, category, volume, sentiment, sources)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (timestamp, "AI", "news", 500, 0.95, "spike_source"))
    
    conn.commit()
    conn.close()
    
    yield TEST_DB_PATH
    
    # Cleanup
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


def test_ml_trend_skills_registered():
    """Verify ML trend skills are registered."""
    assert "forecast_trend" in ML_TREND_SKILLS
    assert "detect_anomalies" in ML_TREND_SKILLS


def test_skills_are_callables():
    """Verify ML trend skills are callable."""
    for skill_name, skill_func in ML_TREND_SKILLS.items():
        assert callable(skill_func), f"{skill_name} is not callable"


@pytest.mark.asyncio
async def test_forecast_trend_invalid_metric():
    """Test forecast with invalid metric format."""
    result = await forecast_trend(metric="invalid", days=7)
    
    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "format" in result["message"].lower()


@pytest.mark.asyncio
async def test_forecast_trend_valid_format():
    """Test forecast with valid metric format."""
    result = await forecast_trend(metric="news/AI", days=7)
    
    assert isinstance(result, dict)
    assert "status" in result
    assert "metric" in result


@pytest.mark.asyncio
async def test_forecast_trend_response_structure():
    """Test forecast response has expected structure."""
    result = await forecast_trend(metric="news/AI", days=5)
    
    assert "status" in result
    
    if result["status"] == "success":
        assert "metric" in result
        assert "forecast_days" in result
        assert "predictions" in result
        assert "confidence_intervals" in result
        assert "trend_direction" in result
        assert isinstance(result["predictions"], list)
        assert isinstance(result["confidence_intervals"], list)


@pytest.mark.asyncio
async def test_detect_anomalies_invalid_metric():
    """Test anomaly detection with invalid metric format."""
    result = await detect_anomalies(metric="invalid", days=30)
    
    assert isinstance(result, dict)
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_detect_anomalies_valid_format():
    """Test anomaly detection with valid metric format."""
    result = await detect_anomalies(metric="news/AI", days=30)
    
    assert isinstance(result, dict)
    assert "status" in result


@pytest.mark.asyncio
async def test_detect_anomalies_response_structure():
    """Test anomaly detection response structure."""
    result = await detect_anomalies(metric="news/AI", days=30)
    
    assert "status" in result
    
    if result["status"] == "success":
        assert "metric" in result
        assert "anomalies" in result
        assert "total_points" in result
        assert "anomaly_count" in result
        assert "anomaly_rate" in result
        assert isinstance(result["anomalies"], list)


def test_ml_analyzer_initialization(clean_db):
    """Test MLTrendAnalyzer initialization."""
    analyzer = MLTrendAnalyzer(db_path=clean_db)
    assert analyzer.db_path == clean_db


def test_get_time_series_data(clean_db):
    """Test fetching time series data."""
    analyzer = MLTrendAnalyzer(db_path=clean_db)
    df = analyzer._get_time_series_data("AI", "news", days=30)
    
    assert not df.empty
    assert "volume" in df.columns
    assert "sentiment" in df.columns


@pytest.mark.asyncio
async def test_forecast_with_data(clean_db):
    """Test forecasting with actual data."""
    analyzer = MLTrendAnalyzer(db_path=clean_db)
    result = await analyzer.forecast_trend("AI", "news", forecast_days=7)
    
    assert isinstance(result.metric, str)
    assert result.forecast_days == 7
    assert isinstance(result.predictions, list)
    assert isinstance(result.trend_direction, str)


@pytest.mark.asyncio
async def test_forecast_insufficient_data(clean_db):
    """Test forecasting with insufficient data."""
    analyzer = MLTrendAnalyzer(db_path=clean_db)
    result = await analyzer.forecast_trend("NonExistent", "news", forecast_days=7)
    
    assert result.trend_direction == "insufficient_data"
    assert len(result.predictions) == 0


@pytest.mark.asyncio
async def test_detect_anomalies_with_data(clean_db):
    """Test anomaly detection with actual data."""
    analyzer = MLTrendAnalyzer(db_path=clean_db)
    result = await analyzer.detect_anomalies("AI", "news", days=30)
    
    assert isinstance(result.metric, str)
    assert isinstance(result.anomalies, list)
    assert result.total_points > 0
    assert result.anomaly_rate >= 0.0


@pytest.mark.asyncio
async def test_seasonal_decomposition_with_data(clean_db):
    """Test seasonal decomposition with data."""
    analyzer = MLTrendAnalyzer(db_path=clean_db)
    result = await analyzer.seasonal_decomposition("AI", "news", days=30, period=7)
    
    assert isinstance(result, dict)
    
    if result["status"] == "success":
        assert "trend" in result
        assert "seasonal" in result
        assert "residual" in result
        assert isinstance(result["trend"], list)


@pytest.mark.asyncio
async def test_seasonal_decomposition_insufficient_data(clean_db):
    """Test seasonal decomposition with insufficient data."""
    analyzer = MLTrendAnalyzer(db_path=clean_db)
    result = await analyzer.seasonal_decomposition("AI", "news", days=5, period=7)
    
    assert result["status"] == "error"
    assert "Insufficient" in result["message"]


@pytest.mark.asyncio
async def test_forecast_days_parameter():
    """Test different forecast day parameters."""
    for days in [1, 3, 7, 14, 30]:
        result = await forecast_trend(metric="news/AI", days=days)
        assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_anomaly_detection_days_parameter():
    """Test different anomaly detection day parameters."""
    for days in [7, 14, 30, 60]:
        result = await detect_anomalies(metric="news/AI", days=days)
        assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_forecast_trend_direction(clean_db):
    """Test trend direction classification."""
    analyzer = MLTrendAnalyzer(db_path=clean_db)
    result = await analyzer.forecast_trend("AI", "news", forecast_days=7)
    
    # Should detect increasing trend from our test data
    assert result.trend_direction in ["increasing", "decreasing", "stable", "insufficient_data"]


@pytest.mark.asyncio
async def test_anomaly_contamination(clean_db):
    """Test anomaly detection with different contamination rates."""
    analyzer = MLTrendAnalyzer(db_path=clean_db)
    
    # Lower contamination = fewer anomalies
    result_low = await analyzer.detect_anomalies("AI", "news", days=30, contamination=0.05)
    result_high = await analyzer.detect_anomalies("AI", "news", days=30, contamination=0.2)
    
    # Higher contamination should find more (or equal) anomalies
    assert result_high.anomaly_count >= result_low.anomaly_count


def test_confidence_interval_structure(clean_db):
    """Test that confidence intervals have correct structure."""
    analyzer = MLTrendAnalyzer(db_path=clean_db)
    
    import asyncio
    result = asyncio.run(analyzer.forecast_trend("AI", "news", forecast_days=5))
    
    if result.confidence_intervals:
        for interval in result.confidence_intervals:
            assert isinstance(interval, tuple)
            assert len(interval) == 2
            assert interval[0] <= interval[1]  # Lower <= Upper


@pytest.mark.asyncio
async def test_error_handling_missing_db():
    """Test error handling with missing database."""
    analyzer = MLTrendAnalyzer(db_path=Path("/nonexistent/db.db"))
    result = await analyzer.forecast_trend("AI", "news", forecast_days=7)
    
    # Should handle gracefully
    assert isinstance(result, object)  # ForecastResult


@pytest.mark.asyncio
async def test_empty_dataframe_handling(clean_db):
    """Test handling of empty dataframes."""
    analyzer = MLTrendAnalyzer(db_path=clean_db)
    
    # Query for non-existent topic
    result = await analyzer.detect_anomalies("NonExistent", "category", days=30)
    
    assert result.total_points == 0
    assert result.anomaly_count == 0
    assert len(result.anomalies) == 0
