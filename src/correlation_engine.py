"""
Multi-Source Correlation Analysis Engine.

Discovers relationships between different data sources:
  - Weather vs. sports performance
  - Stock prices vs. news sentiment
  - Social trends vs. entertainment consumption

Generates insights and visualizations from cross-API data.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from scipy.stats import pearsonr, spearmanr

log = logging.getLogger(__name__)

# Database path
DB_PATH = Path(os.getenv("THREAD_DB_PATH", "/memory/openclaw.db"))


@dataclass
class CorrelationInsight:
    """Correlation analysis result."""

    metric_a: str
    metric_b: str
    correlation: float
    p_value: float
    strength: str  # "strong", "moderate", "weak", "none"
    direction: str  # "positive", "negative", "none"
    sample_size: int
    insight: str  # Human-readable insight


class CorrelationEngine:
    """Multi-source correlation analyzer."""

    def __init__(self, db_path: Path = DB_PATH):
        """Initialize correlation engine."""
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Ensure correlation cache table exists."""
        if not self.db_path.parent.exists():
            log.warning("Database directory %s does not exist", self.db_path.parent)
            return

        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
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
                conn.commit()
        except sqlite3.Error as e:
            log.warning("Could not ensure tables: %s", e)

    def _get_metric_data(self, metric: str, days: int = 30) -> pd.Series:
        """
        Get time series data for a metric.

        Args:
            metric: Metric identifier (category/topic or custom)
            days: Days of data to fetch

        Returns:
            Pandas Series indexed by date
        """
        if not self.db_path.exists():
            log.warning("Database %s does not exist", self.db_path)
            return pd.Series(dtype=float)

        # Try trend_data table first
        parts = metric.split("/", 1)
        if len(parts) == 2:
            category, topic = parts
            cutoff = (datetime.now() - timedelta(days=days)).timestamp()

            try:
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    query = """
                        SELECT timestamp, volume
                        FROM trend_data
                        WHERE category = ? AND topic = ? AND timestamp >= ?
                        ORDER BY timestamp ASC
                    """
                    df = pd.read_sql_query(query, conn, params=(category, topic, cutoff))

                    if not df.empty:
                        df["date"] = pd.to_datetime(df["timestamp"], unit="s").dt.date
                        df = df.groupby("date")["volume"].sum()
                        return df
            except (sqlite3.Error, KeyError, ValueError, TypeError) as e:
                log.error("Error fetching metric data: %s", e)

        # Could extend to support other metric sources (weather, stocks, etc.)
        return pd.Series(dtype=float)

    def _classify_correlation(self, correlation: float, p_value: float) -> tuple[str, str]:
        """
        Classify correlation strength and direction.

        Args:
            correlation: Correlation coefficient (-1 to 1)
            p_value: Statistical significance (< 0.05 is significant)

        Returns:
            (strength, direction) tuple
        """
        abs_corr = abs(correlation)

        # Not statistically significant
        if p_value >= 0.05:
            return "none", "none"

        # Classify strength
        if abs_corr >= 0.7:
            strength = "strong"
        elif abs_corr >= 0.4:
            strength = "moderate"
        elif abs_corr >= 0.2:
            strength = "weak"
        else:
            strength = "none"

        # Classify direction
        if correlation > 0.05:
            direction = "positive"
        elif correlation < -0.05:
            direction = "negative"
        else:
            direction = "none"

        return strength, direction

    def _generate_insight(self, metric_a: str, metric_b: str, correlation: float, strength: str, direction: str) -> str:
        """
        Generate human-readable insight from correlation.

        Args:
            metric_a: First metric name
            metric_b: Second metric name
            correlation: Correlation coefficient
            strength: Strength classification
            direction: Direction classification

        Returns:
            Insight string
        """
        if strength == "none":
            return f"No significant correlation between {metric_a} and {metric_b}."

        percentage = abs(correlation) * 100

        if direction == "positive":
            return (
                f"{strength.capitalize()} positive correlation ({percentage:.0f}%): "
                f"When {metric_a} increases, {metric_b} tends to increase."
            )
        elif direction == "negative":
            return (
                f"{strength.capitalize()} negative correlation ({percentage:.0f}%): "
                f"When {metric_a} increases, {metric_b} tends to decrease."
            )
        else:
            return f"No meaningful correlation between {metric_a} and {metric_b}."

    async def find_correlations(
        self, metric_a: str, metric_b: str, days: int = 30, method: str = "pearson"
    ) -> CorrelationInsight:
        """
        Find correlation between two metrics.

        Args:
            metric_a: First metric (e.g., "news/AI")
            metric_b: Second metric (e.g., "finance/NVDA")
            days: Days of data to analyze
            method: "pearson" (linear) or "spearman" (rank-based)

        Returns:
            CorrelationInsight with analysis results
        """
        try:
            # Get data for both metrics
            data_a = self._get_metric_data(metric_a, days)
            data_b = self._get_metric_data(metric_b, days)

            # Align data by date (inner join)
            df = pd.DataFrame(
                {
                    "a": data_a,
                    "b": data_b,
                }
            ).dropna()

            if len(df) < 10:
                return CorrelationInsight(
                    metric_a=metric_a,
                    metric_b=metric_b,
                    correlation=0.0,
                    p_value=1.0,
                    strength="none",
                    direction="none",
                    sample_size=len(df),
                    insight=f"Insufficient overlapping data ({len(df)} points).",
                )

            # Calculate correlation
            if method == "pearson":
                corr, p_val = pearsonr(df["a"], df["b"])
            else:
                corr, p_val = spearmanr(df["a"], df["b"])

            strength, direction = self._classify_correlation(corr, p_val)
            insight = self._generate_insight(metric_a, metric_b, corr, strength, direction)

            result = CorrelationInsight(
                metric_a=metric_a,
                metric_b=metric_b,
                correlation=float(corr),
                p_value=float(p_val),
                strength=strength,
                direction=direction,
                sample_size=len(df),
                insight=insight,
            )

            # Cache result
            self._cache_correlation(result)

            return result

        except Exception as e:  # broad: intentional — complex statistical + LLM computation
            log.error("Error finding correlation: %s", e)
            return CorrelationInsight(
                metric_a=metric_a,
                metric_b=metric_b,
                correlation=0.0,
                p_value=1.0,
                strength="none",
                direction="none",
                sample_size=0,
                insight=f"Error: {str(e)}",
            )

    def _cache_correlation(self, result: CorrelationInsight) -> None:
        """Cache correlation result in database."""
        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO correlation_cache
                    (metric_a, metric_b, correlation, p_value, strength,
                     direction, sample_size, insight, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        result.metric_a,
                        result.metric_b,
                        result.correlation,
                        result.p_value,
                        result.strength,
                        result.direction,
                        result.sample_size,
                        result.insight,
                        datetime.now().timestamp(),
                    ),
                )
                conn.commit()
        except sqlite3.Error as e:
            log.error("Error caching correlation: %s", e)

    async def discover_correlations(
        self, metrics: list[str], days: int = 30, threshold: float = 0.4
    ) -> list[CorrelationInsight]:
        """
        Discover all significant correlations among a list of metrics.

        Args:
            metrics: List of metrics to analyze (e.g., ["news/AI", "finance/NVDA"])
            days: Days of data to analyze
            threshold: Minimum absolute correlation to report

        Returns:
            List of CorrelationInsight objects for significant correlations
        """
        results = []

        # Compare all pairs
        for i, metric_a in enumerate(metrics):
            for metric_b in metrics[i + 1 :]:
                result = await self.find_correlations(metric_a, metric_b, days)

                if abs(result.correlation) >= threshold and result.p_value < 0.05:
                    results.append(result)

        # Sort by absolute correlation (strongest first)
        results.sort(key=lambda r: abs(r.correlation), reverse=True)

        return results

    async def explain_correlation(self, metric_a: str, metric_b: str) -> dict[str, Any]:
        """
        Generate detailed explanation of correlation between two metrics.

        Args:
            metric_a: First metric
            metric_b: Second metric

        Returns:
            {
                "status": "success",
                "correlation": 0.75,
                "p_value": 0.001,
                "strength": "strong",
                "direction": "positive",
                "insight": "...",
                "sample_size": 30,
                "visualization_data": {
                    "metric_a_values": [...],
                    "metric_b_values": [...]
                }
            }
        """
        result = await self.find_correlations(metric_a, metric_b)

        # Get data for visualization
        data_a = self._get_metric_data(metric_a)
        data_b = self._get_metric_data(metric_b)

        df = pd.DataFrame(
            {
                "a": data_a,
                "b": data_b,
            }
        ).dropna()

        return {
            "status": "success",
            "metric_a": metric_a,
            "metric_b": metric_b,
            "correlation": result.correlation,
            "p_value": result.p_value,
            "strength": result.strength,
            "direction": result.direction,
            "insight": result.insight,
            "sample_size": result.sample_size,
            "visualization_data": {
                "metric_a_values": df["a"].tolist() if not df.empty else [],
                "metric_b_values": df["b"].tolist() if not df.empty else [],
                "dates": [str(d) for d in df.index] if not df.empty else [],
            },
        }


# Singleton instance
_correlation_engine = CorrelationEngine()


# ============================================================================
# Public API functions
# ============================================================================


async def find_correlations(metrics: list[str], days: int = 30) -> dict[str, Any]:
    """
    Discover correlations among multiple metrics.

    Args:
        metrics: List of metrics in format "category/topic"
        days: Days of data to analyze

    Returns:
        {
            "status": "success",
            "correlations": [
                {
                    "metric_a": "news/AI",
                    "metric_b": "finance/NVDA",
                    "correlation": 0.75,
                    "strength": "strong",
                    "direction": "positive",
                    "insight": "..."
                },
                ...
            ]
        }
    """
    try:
        if len(metrics) < 2:
            return {
                "status": "error",
                "message": "Need at least 2 metrics to find correlations",
            }

        results = await _correlation_engine.discover_correlations(metrics, days)

        return {
            "status": "success",
            "count": len(results),
            "correlations": [
                {
                    "metric_a": r.metric_a,
                    "metric_b": r.metric_b,
                    "correlation": r.correlation,
                    "p_value": r.p_value,
                    "strength": r.strength,
                    "direction": r.direction,
                    "insight": r.insight,
                    "sample_size": r.sample_size,
                }
                for r in results
            ],
        }

    except Exception as e:  # broad: intentional — wraps complex engine operations
        log.error("Error in find_correlations: %s", e)
        return {"status": "error", "message": str(e)}


async def explain_correlation(metric_a: str, metric_b: str) -> dict[str, Any]:
    """
    Explain correlation between two specific metrics.

    Args:
        metric_a: First metric (e.g., "news/AI")
        metric_b: Second metric (e.g., "finance/NVDA")

    Returns:
        Detailed correlation analysis with visualization data
    """
    try:
        return await _correlation_engine.explain_correlation(metric_a, metric_b)
    except Exception as e:  # broad: intentional — wraps complex engine operations
        log.error("Error in explain_correlation: %s", e)
        return {"status": "error", "message": str(e)}


# Skill metadata
CORRELATION_SKILLS = {
    "find_correlations": find_correlations,
    "explain_correlation": explain_correlation,
}
