"""
ML-Based Trend Detection and Forecasting.

Extends trend_tracker.py with machine learning models:
  - ARIMA for time series forecasting
  - Isolation Forest for anomaly detection
  - Seasonal decomposition (trend, seasonality, residual)
  - Cross-metric correlation analysis

Uses scikit-learn and statsmodels for analysis.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.seasonal import seasonal_decompose

log = logging.getLogger("openclaw.ml_trends")

# Database path
DB_PATH = Path(os.getenv("THREAD_DB_PATH", "/memory/openclaw.db"))


@dataclass
class ForecastResult:
    """Results from time series forecasting."""

    metric: str
    forecast_days: int
    predictions: list[float]
    confidence_intervals: list[tuple[float, float]]
    trend_direction: str  # "increasing", "decreasing", "stable"
    forecast_date: str


@dataclass
class AnomalyResult:
    """Results from anomaly detection."""

    metric: str
    anomalies: list[dict[str, Any]]
    total_points: int
    anomaly_count: int
    anomaly_rate: float


@dataclass
class CorrelationResult:
    """Results from correlation analysis."""

    metric_a: str
    metric_b: str
    correlation: float
    p_value: float
    strength: str  # "strong", "moderate", "weak", "none"
    direction: str  # "positive", "negative"


class MLTrendAnalyzer:
    """Machine learning-based trend analyzer."""

    def __init__(self, db_path: Path = DB_PATH):
        """Initialize ML analyzer."""
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Ensure required database tables exist."""
        # Reuse existing trend_data table from trend_tracker.py
        # Only create if database exists
        if not self.db_path.parent.exists():
            log.warning("Database directory %s does not exist", self.db_path.parent)
            return

        try:
            with sqlite3.connect(self.db_path) as conn:
                # Table may already exist - that's OK
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
                conn.commit()
        except Exception as e:
            log.warning("Could not ensure tables: %s", e)

    def _get_time_series_data(
        self,
        topic: str,
        category: str,
        days: int = 30
    ) -> pd.DataFrame:
        """
        Get time series data for a topic.

        Args:
            topic: Topic name
            category: Category (news, sports, finance, etc.)
            days: Number of days to fetch

        Returns:
            DataFrame with columns: timestamp, volume, sentiment
        """
        if not self.db_path.exists():
            log.warning("Database %s does not exist", self.db_path)
            return pd.DataFrame()

        cutoff = (datetime.now() - timedelta(days=days)).timestamp()

        try:
            with sqlite3.connect(self.db_path) as conn:
                query = """
                    SELECT timestamp, volume, sentiment
                    FROM trend_data
                    WHERE topic = ? AND category = ? AND timestamp >= ?
                    ORDER BY timestamp ASC
                """
                df = pd.read_sql_query(
                    query,
                    conn,
                    params=(topic, category, cutoff)
                )

                if not df.empty:
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
                    df.set_index('timestamp', inplace=True)

                return df
        except Exception as e:
            log.error("Error fetching time series data: %s", e)
            return pd.DataFrame()

    async def forecast_trend(
        self,
        topic: str,
        category: str,
        forecast_days: int = 7,
        history_days: int = 30
    ) -> ForecastResult:
        """
        Forecast future trend using ARIMA model.

        Args:
            topic: Topic to forecast
            category: Category (news, sports, finance, etc.)
            forecast_days: Number of days to forecast ahead
            history_days: Days of historical data to use

        Returns:
            ForecastResult with predictions and confidence intervals
        """
        try:
            df = self._get_time_series_data(topic, category, history_days)

            if df.empty or len(df) < 10:
                return ForecastResult(
                    metric=f"{category}/{topic}",
                    forecast_days=forecast_days,
                    predictions=[],
                    confidence_intervals=[],
                    trend_direction="insufficient_data",
                    forecast_date=datetime.now().isoformat(),
                )

            # Use volume for forecasting
            series = df['volume'].asfreq('D', fill_value=0)

            # Fit ARIMA model (p=1, d=1, q=1 as default)
            # p: autoregressive order
            # d: differencing order
            # q: moving average order
            model = ARIMA(series, order=(1, 1, 1))
            fitted = model.fit()

            # Forecast
            forecast_result = fitted.forecast(steps=forecast_days)
            predictions = forecast_result.tolist()

            # Get confidence intervals (95%)
            forecast_conf = fitted.get_forecast(steps=forecast_days)
            conf_int = forecast_conf.conf_int()
            confidence_intervals = [
                (float(conf_int.iloc[i, 0]), float(conf_int.iloc[i, 1]))
                for i in range(len(conf_int))
            ]

            # Determine trend direction
            if len(predictions) >= 2:
                slope = (predictions[-1] - predictions[0]) / len(predictions)
                if slope > 0.5:
                    trend = "increasing"
                elif slope < -0.5:
                    trend = "decreasing"
                else:
                    trend = "stable"
            else:
                trend = "stable"

            return ForecastResult(
                metric=f"{category}/{topic}",
                forecast_days=forecast_days,
                predictions=[float(p) for p in predictions],
                confidence_intervals=confidence_intervals,
                trend_direction=trend,
                forecast_date=datetime.now().isoformat(),
            )

        except Exception as e:
            log.error("Error forecasting trend for %s/%s: %s", category, topic, e)
            return ForecastResult(
                metric=f"{category}/{topic}",
                forecast_days=forecast_days,
                predictions=[],
                confidence_intervals=[],
                trend_direction="error",
                forecast_date=datetime.now().isoformat(),
            )

    async def detect_anomalies(
        self,
        topic: str,
        category: str,
        days: int = 30,
        contamination: float = 0.1
    ) -> AnomalyResult:
        """
        Detect anomalies in trend data using Isolation Forest.

        Args:
            topic: Topic to analyze
            category: Category
            days: Days of data to analyze
            contamination: Expected proportion of outliers (0.0-0.5)

        Returns:
            AnomalyResult with detected anomalies
        """
        try:
            df = self._get_time_series_data(topic, category, days)

            if df.empty or len(df) < 10:
                return AnomalyResult(
                    metric=f"{category}/{topic}",
                    anomalies=[],
                    total_points=0,
                    anomaly_count=0,
                    anomaly_rate=0.0,
                )

            # Prepare features: volume and sentiment
            X = df[['volume', 'sentiment']].values

            # Standardize features
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)

            # Fit Isolation Forest
            iso_forest = IsolationForest(
                contamination=contamination,
                random_state=42
            )
            predictions = iso_forest.fit_predict(X_scaled)

            # Extract anomalies (predictions == -1)
            anomaly_indices = np.where(predictions == -1)[0]

            anomalies = []
            for idx in anomaly_indices:
                row = df.iloc[idx]
                anomalies.append({
                    "timestamp": row.name.isoformat(),
                    "volume": int(row['volume']),
                    "sentiment": float(row['sentiment']),
                    "anomaly_score": float(iso_forest.score_samples(X_scaled)[idx]),
                })

            return AnomalyResult(
                metric=f"{category}/{topic}",
                anomalies=anomalies,
                total_points=len(df),
                anomaly_count=len(anomalies),
                anomaly_rate=len(anomalies) / len(df) if len(df) > 0 else 0.0,
            )

        except Exception as e:
            log.error("Error detecting anomalies for %s/%s: %s", category, topic, e)
            return AnomalyResult(
                metric=f"{category}/{topic}",
                anomalies=[],
                total_points=0,
                anomaly_count=0,
                anomaly_rate=0.0,
            )

    async def seasonal_decomposition(
        self,
        topic: str,
        category: str,
        days: int = 30,
        period: int = 7
    ) -> dict[str, Any]:
        """
        Perform seasonal decomposition of time series.

        Decomposes series into:
          - Trend: Long-term progression
          - Seasonal: Repeating patterns
          - Residual: Random fluctuations

        Args:
            topic: Topic to analyze
            category: Category
            days: Days of data
            period: Seasonal period (7 for weekly)

        Returns:
            {
                "status": "success",
                "metric": "news/AI",
                "trend": [1.2, 1.5, 1.8, ...],
                "seasonal": [0.1, -0.2, 0.3, ...],
                "residual": [0.05, -0.1, 0.02, ...]
            }
        """
        try:
            df = self._get_time_series_data(topic, category, days)

            if df.empty or len(df) < period * 2:
                return {
                    "status": "error",
                    "message": f"Insufficient data (need at least {period * 2} points)",
                }

            # Use volume for decomposition
            series = df['volume'].asfreq('D', fill_value=0)

            # Perform seasonal decomposition
            decomposition = seasonal_decompose(
                series,
                model='additive',
                period=period,
                extrapolate_trend='freq'
            )

            return {
                "status": "success",
                "metric": f"{category}/{topic}",
                "trend": decomposition.trend.dropna().tolist(),
                "seasonal": decomposition.seasonal.dropna().tolist(),
                "residual": decomposition.resid.dropna().tolist(),
                "period": period,
            }

        except Exception as e:
            log.error("Error in seasonal decomposition for %s/%s: %s", category, topic, e)
            return {
                "status": "error",
                "message": str(e),
            }


# Singleton instance
_ml_analyzer = MLTrendAnalyzer()


# ============================================================================
# Public API functions
# ============================================================================

async def forecast_trend(
    metric: str,
    days: int = 7
) -> dict[str, Any]:
    """
    Forecast future trend for a metric.

    Args:
        metric: Metric in format "category/topic" (e.g., "news/AI")
        days: Days to forecast ahead

    Returns:
        {
            "status": "success",
            "metric": "news/AI",
            "forecast_days": 7,
            "predictions": [12.5, 13.2, 14.1, ...],
            "confidence_intervals": [(10.2, 14.8), (11.0, 15.4), ...],
            "trend_direction": "increasing"
        }
    """
    try:
        parts = metric.split("/", 1)
        if len(parts) != 2:
            return {
                "status": "error",
                "message": "Metric must be in format 'category/topic'",
            }

        category, topic = parts
        result = await _ml_analyzer.forecast_trend(topic, category, days)

        return {
            "status": "success" if result.predictions else "error",
            "metric": result.metric,
            "forecast_days": result.forecast_days,
            "predictions": result.predictions,
            "confidence_intervals": result.confidence_intervals,
            "trend_direction": result.trend_direction,
            "forecast_date": result.forecast_date,
        }

    except Exception as e:
        log.error("Error in forecast_trend: %s", e)
        return {"status": "error", "message": str(e)}


async def detect_anomalies(metric: str, days: int = 30) -> dict[str, Any]:
    """
    Detect anomalies in metric data.

    Args:
        metric: Metric in format "category/topic"
        days: Days of data to analyze

    Returns:
        {
            "status": "success",
            "metric": "news/AI",
            "anomalies": [
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "volume": 156,
                    "sentiment": 0.8,
                    "anomaly_score": -0.35
                },
                ...
            ],
            "total_points": 30,
            "anomaly_count": 3,
            "anomaly_rate": 0.1
        }
    """
    try:
        parts = metric.split("/", 1)
        if len(parts) != 2:
            return {
                "status": "error",
                "message": "Metric must be in format 'category/topic'",
            }

        category, topic = parts
        result = await _ml_analyzer.detect_anomalies(topic, category, days)

        return {
            "status": "success",
            "metric": result.metric,
            "anomalies": result.anomalies,
            "total_points": result.total_points,
            "anomaly_count": result.anomaly_count,
            "anomaly_rate": result.anomaly_rate,
        }

    except Exception as e:
        log.error("Error in detect_anomalies: %s", e)
        return {"status": "error", "message": str(e)}


# Skill metadata
ML_TREND_SKILLS = {
    "forecast_trend": forecast_trend,
    "detect_anomalies": detect_anomalies,
}
