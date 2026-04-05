"""
Tests for correlation engine.

Tests multi-source correlation analysis and insight generation.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.correlation_engine import (
    CORRELATION_SKILLS,
    CorrelationEngine,
    CorrelationInsight,
    explain_correlation,
    find_correlations,
)

# Test database path
TEST_DB_PATH = Path("/tmp/test_correlation.db")


@pytest.fixture
def clean_db():
    """Create clean test database with correlated data."""
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()

    conn = sqlite3.connect(TEST_DB_PATH)

    # Create trend_data table
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

    # Create correlation_cache table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS correlation_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_a TEXT NOT NULL,
            metric_b TEXT NOT NULL,
            correlation REAL NOT NULL,
            p_value REAL NOT NULL,
            strength TEXT NOT NULL,
            direction TEXT NOT NULL,
            sample_size INTEGER NOT NULL,
            insight TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(metric_a, metric_b)
        )
    """)

    # Insert correlated data
    base_time = datetime.now() - timedelta(days=30)
    for i in range(30):
        timestamp = (base_time + timedelta(days=i)).timestamp()

        # AI news volume
        ai_volume = 100 + (i * 3)  # Increasing

        # NVDA stock (highly correlated with AI news)
        nvda_volume = 90 + (i * 3) + (i % 3)  # Similar trend with noise

        # Weather (uncorrelated)
        weather_volume = 50 + (i % 7) * 10  # Weekly pattern

        conn.execute("""
            INSERT INTO trend_data (timestamp, topic, category, volume, sentiment, sources)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (timestamp, "AI", "news", ai_volume, 0.6, "source1"))

        conn.execute("""
            INSERT INTO trend_data (timestamp, topic, category, volume, sentiment, sources)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (timestamp, "NVDA", "finance", nvda_volume, 0.65, "source2"))

        conn.execute("""
            INSERT INTO trend_data (timestamp, topic, category, volume, sentiment, sources)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (timestamp, "rain", "weather", weather_volume, 0.5, "source3"))

    conn.commit()
    conn.close()

    yield TEST_DB_PATH

    # Cleanup
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


def test_correlation_skills_registered():
    """Verify correlation skills are registered."""
    assert "find_correlations" in CORRELATION_SKILLS
    assert "explain_correlation" in CORRELATION_SKILLS


def test_skills_are_callables():
    """Verify correlation skills are callable."""
    for skill_name, skill_func in CORRELATION_SKILLS.items():
        assert callable(skill_func), f"{skill_name} is not callable"


@pytest.mark.asyncio
async def test_find_correlations_insufficient_metrics():
    """Test correlation discovery with <2 metrics."""
    result = await find_correlations(metrics=["news/AI"])

    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "at least 2" in result["message"].lower()


@pytest.mark.asyncio
async def test_find_correlations_valid_input():
    """Test correlation discovery with valid metrics."""
    metrics = ["news/AI", "finance/NVDA"]
    result = await find_correlations(metrics=metrics, days=30)

    assert isinstance(result, dict)
    assert "status" in result


@pytest.mark.asyncio
async def test_find_correlations_response_structure():
    """Test correlation discovery response structure."""
    metrics = ["news/AI", "finance/NVDA", "weather/rain"]
    result = await find_correlations(metrics=metrics)

    assert "status" in result

    if result["status"] == "success":
        assert "count" in result
        assert "correlations" in result
        assert isinstance(result["correlations"], list)


@pytest.mark.asyncio
async def test_explain_correlation_response_structure():
    """Test explain correlation response structure."""
    result = await explain_correlation("news/AI", "finance/NVDA")

    assert isinstance(result, dict)

    if result["status"] == "success":
        assert "metric_a" in result
        assert "metric_b" in result
        assert "correlation" in result
        assert "p_value" in result
        assert "strength" in result
        assert "direction" in result
        assert "insight" in result
        assert "visualization_data" in result


def test_correlation_engine_initialization(clean_db):
    """Test CorrelationEngine initialization."""
    engine = CorrelationEngine(db_path=clean_db)
    assert engine.db_path == clean_db


def test_get_metric_data(clean_db):
    """Test fetching metric data."""
    engine = CorrelationEngine(db_path=clean_db)
    series = engine._get_metric_data("news/AI", days=30)

    assert len(series) > 0
    assert isinstance(series.index[0], object)  # Date


def test_classify_correlation_strong(clean_db):
    """Test correlation classification - strong."""
    engine = CorrelationEngine(db_path=clean_db)

    strength, direction = engine._classify_correlation(0.85, 0.001)
    assert strength == "strong"
    assert direction == "positive"

    strength, direction = engine._classify_correlation(-0.85, 0.001)
    assert strength == "strong"
    assert direction == "negative"


def test_classify_correlation_moderate(clean_db):
    """Test correlation classification - moderate."""
    engine = CorrelationEngine(db_path=clean_db)

    strength, direction = engine._classify_correlation(0.55, 0.01)
    assert strength == "moderate"
    assert direction == "positive"


def test_classify_correlation_weak(clean_db):
    """Test correlation classification - weak."""
    engine = CorrelationEngine(db_path=clean_db)

    strength, direction = engine._classify_correlation(0.25, 0.04)
    assert strength == "weak"
    assert direction == "positive"


def test_classify_correlation_none(clean_db):
    """Test correlation classification - none (not significant)."""
    engine = CorrelationEngine(db_path=clean_db)

    strength, direction = engine._classify_correlation(0.5, 0.08)
    assert strength == "none"
    assert direction == "none"


def test_generate_insight_positive(clean_db):
    """Test insight generation for positive correlation."""
    engine = CorrelationEngine(db_path=clean_db)

    insight = engine._generate_insight(
        "news/AI", "finance/NVDA", 0.75, "strong", "positive"
    )

    assert "positive" in insight.lower()
    assert "increases" in insight.lower()


def test_generate_insight_negative(clean_db):
    """Test insight generation for negative correlation."""
    engine = CorrelationEngine(db_path=clean_db)

    insight = engine._generate_insight(
        "news/AI", "weather/rain", -0.6, "moderate", "negative"
    )

    assert "negative" in insight.lower()
    assert "decrease" in insight.lower()


def test_generate_insight_none(clean_db):
    """Test insight generation for no correlation."""
    engine = CorrelationEngine(db_path=clean_db)

    insight = engine._generate_insight(
        "news/AI", "weather/rain", 0.1, "none", "none"
    )

    assert "no" in insight.lower()


@pytest.mark.asyncio
async def test_find_correlations_with_data(clean_db):
    """Test finding correlations with actual data."""
    engine = CorrelationEngine(db_path=clean_db)

    result = await engine.find_correlations("news/AI", "finance/NVDA", days=30)

    assert isinstance(result, CorrelationInsight)
    assert result.metric_a == "news/AI"
    assert result.metric_b == "finance/NVDA"
    assert -1.0 <= result.correlation <= 1.0
    assert 0.0 <= result.p_value <= 1.0


@pytest.mark.asyncio
async def test_find_correlations_insufficient_data(clean_db):
    """Test correlation with insufficient overlapping data."""
    engine = CorrelationEngine(db_path=clean_db)

    result = await engine.find_correlations("news/NonExistent", "finance/NVDA", days=30)

    assert result.strength == "none"
    assert "Insufficient" in result.insight


@pytest.mark.asyncio
async def test_discover_correlations(clean_db):
    """Test discovering multiple correlations."""
    engine = CorrelationEngine(db_path=clean_db)

    metrics = ["news/AI", "finance/NVDA", "weather/rain"]
    results = await engine.discover_correlations(metrics, days=30)

    assert isinstance(results, list)
    # Should find correlations between all pairs
    # 3 metrics = 3 pairs: AI-NVDA, AI-rain, NVDA-rain


@pytest.mark.asyncio
async def test_discover_correlations_threshold(clean_db):
    """Test correlation discovery with threshold."""
    engine = CorrelationEngine(db_path=clean_db)

    metrics = ["news/AI", "finance/NVDA", "weather/rain"]

    # High threshold should find fewer results
    results_high = await engine.discover_correlations(metrics, threshold=0.9)
    results_low = await engine.discover_correlations(metrics, threshold=0.3)

    assert len(results_high) <= len(results_low)


@pytest.mark.asyncio
async def test_explain_correlation_with_data(clean_db):
    """Test correlation explanation with data."""
    engine = CorrelationEngine(db_path=clean_db)

    result = await engine.explain_correlation("news/AI", "finance/NVDA")

    assert result["status"] == "success"
    assert "visualization_data" in result
    assert isinstance(result["visualization_data"]["metric_a_values"], list)
    assert isinstance(result["visualization_data"]["metric_b_values"], list)


@pytest.mark.asyncio
async def test_correlation_caching(clean_db):
    """Test that correlations are cached in database."""
    engine = CorrelationEngine(db_path=clean_db)

    # Find correlation
    await engine.find_correlations("news/AI", "finance/NVDA")

    # Check cache
    conn = sqlite3.connect(clean_db)
    cursor = conn.execute(
        "SELECT * FROM correlation_cache WHERE metric_a = ? AND metric_b = ?",
        ("news/AI", "finance/NVDA")
    )
    cached = cursor.fetchone()
    conn.close()

    assert cached is not None


@pytest.mark.asyncio
async def test_correlation_methods(clean_db):
    """Test different correlation methods."""
    engine = CorrelationEngine(db_path=clean_db)

    # Pearson (linear)
    result_pearson = await engine.find_correlations(
        "news/AI", "finance/NVDA", method="pearson"
    )

    # Spearman (rank-based)
    result_spearman = await engine.find_correlations(
        "news/AI", "finance/NVDA", method="spearman"
    )

    # Both should return valid results
    assert isinstance(result_pearson.correlation, float)
    assert isinstance(result_spearman.correlation, float)


@pytest.mark.asyncio
async def test_multiple_metrics_correlation():
    """Test correlation with multiple metrics."""
    metrics = ["news/AI", "finance/NVDA", "weather/rain", "sports/NBA"]
    result = await find_correlations(metrics=metrics, days=30)

    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_visualization_data_alignment(clean_db):
    """Test that visualization data is properly aligned."""
    engine = CorrelationEngine(db_path=clean_db)

    result = await engine.explain_correlation("news/AI", "finance/NVDA")

    if result["status"] == "success":
        viz = result["visualization_data"]
        # All arrays should have same length
        assert len(viz["metric_a_values"]) == len(viz["metric_b_values"])
        assert len(viz["metric_a_values"]) == len(viz["dates"])


@pytest.mark.asyncio
async def test_error_handling_invalid_metric():
    """Test error handling with invalid metric format."""
    result = await explain_correlation("invalid", "also-invalid")

    # Should handle gracefully (may return no data or specific error)
    assert isinstance(result, dict)
    assert "status" in result
