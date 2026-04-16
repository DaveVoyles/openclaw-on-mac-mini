"""
OpenClaw Trend Tracker — Time-series data storage and analysis.

Tracks topics over time, detects anomalies, and identifies trending patterns.
Uses SQLite for persistent storage with time-series analysis capabilities.

Features:
  - Volume spike detection (3x normal threshold)
  - Sentiment shift detection (0.3+ change)
  - Velocity analysis (acceleration/deceleration)
  - Breakout detection (new topics appearing)
  - Rolling window comparisons (24h, 7d, 30d)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("openclaw.trend_tracker")

# Database path (reuse existing openclaw.db)
DB_PATH = Path(os.getenv("THREAD_DB_PATH", "/memory/openclaw.db"))

# Analysis parameters
SPIKE_THRESHOLD = 3.0  # 3x normal volume = spike
SENTIMENT_SHIFT_THRESHOLD = 0.3  # ±0.3 change = significant
VELOCITY_THRESHOLD = 2.0  # 2x acceleration = trending
Z_SCORE_ANOMALY = 2.0  # 2 standard deviations = anomaly
DATA_RETENTION_DAYS = 90  # Keep 90 days of history


@dataclass
class DataPoint:
    """A single time-series data point."""

    timestamp: float
    topic: str
    category: str
    volume: int  # Number of articles/mentions
    sentiment: float  # -1.0 to 1.0
    sources: str  # Comma-separated list
    metadata: str = ""  # JSON string for extra data


@dataclass
class TrendAnalysis:
    """Results of trend analysis for a topic."""

    topic: str
    category: str
    current_volume: int
    avg_volume_24h: float
    avg_volume_7d: float
    volume_change_pct: float
    current_sentiment: float
    sentiment_change_24h: float
    velocity: float  # Rate of change acceleration
    is_trending: bool
    is_spike: bool
    is_breakout: bool
    trend_direction: str  # "up", "down", "stable"
    z_score: float
    peak_time: float | None = None
    sources: list[str] | None = None

    def __post_init__(self) -> None:
        """Initialize mutable default values."""
        if self.sources is None:
            self.sources = []


class TrendTracker:
    """Manages time-series data storage and trend analysis."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        """Initialize trend tracker with database path.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._db: sqlite3.Connection | None = None
        self._ensure_tables()

    def _get_db(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._db is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
        return self._db

    def _ensure_tables(self) -> None:
        """Create tables if they don't exist."""
        db = self._get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS trend_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                topic TEXT NOT NULL,
                category TEXT NOT NULL,
                volume INTEGER DEFAULT 0,
                sentiment REAL DEFAULT 0.0,
                sources TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}',
                UNIQUE(timestamp, topic, category) ON CONFLICT REPLACE
            );

            CREATE INDEX IF NOT EXISTS idx_trend_topic ON trend_data(topic);
            CREATE INDEX IF NOT EXISTS idx_trend_category ON trend_data(category);
            CREATE INDEX IF NOT EXISTS idx_trend_timestamp ON trend_data(timestamp);
            CREATE INDEX IF NOT EXISTS idx_trend_lookup ON trend_data(topic, category, timestamp);

            CREATE TABLE IF NOT EXISTS trend_config (
                topic TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                spike_threshold REAL DEFAULT 3.0,
                sentiment_threshold REAL DEFAULT 0.3,
                alert_cooldown INTEGER DEFAULT 3600,
                last_alert REAL DEFAULT 0,
                created_at REAL NOT NULL,
                user_id TEXT DEFAULT ''
            );
        """)
        db.commit()
        log.info("Trend tracker tables initialized at %s", self.db_path)

    def track_entity(
        self,
        topic: str,
        category: str,
        volume: int = 1,
        sentiment: float = 0.0,
        sources: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Record a data point for a topic.

        Args:
            topic: Topic name (e.g., "Moana 2", "Bitcoin")
            category: Category (e.g., "Entertainment", "Finance", "Sports")
            volume: Number of mentions/articles
            sentiment: Sentiment score -1.0 to 1.0
            sources: List of data sources
            metadata: Additional metadata as dict

        Returns:
            True if successful
        """
        try:
            timestamp = time.time()
            sources_str = ",".join(sources or [])
            metadata_str = json.dumps(metadata or {})

            db = self._get_db()
            db.execute(
                """
                INSERT INTO trend_data (timestamp, topic, category, volume, sentiment, sources, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (timestamp, topic, category, volume, sentiment, sources_str, metadata_str),
            )
            db.commit()
            log.debug(
                "Tracked: %s/%s vol=%d sent=%.2f", category, topic, volume, sentiment
            )
            return True
        except Exception as e:  # broad: intentional — DB ops can raise sqlite3.Error or RuntimeError from mocks
            log.error("Failed to track entity %s: %s", topic, e)
            return False

    def get_trend(
        self, topic: str, category: str = "", hours: int = 24
    ) -> list[DataPoint]:
        """
        Get historical data points for a topic.

        Args:
            topic: Topic name
            category: Optional category filter
            hours: Number of hours to look back

        Returns:
            List of DataPoint objects
        """
        cutoff = time.time() - (hours * 3600)
        db = self._get_db()

        if category:
            cursor = db.execute(
                """
                SELECT timestamp, topic, category, volume, sentiment, sources, metadata
                FROM trend_data
                WHERE topic = ? AND category = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """,
                (topic, category, cutoff),
            )
        else:
            cursor = db.execute(
                """
                SELECT timestamp, topic, category, volume, sentiment, sources, metadata
                FROM trend_data
                WHERE topic = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """,
                (topic, cutoff),
            )

        points = []
        for row in cursor:
            points.append(
                DataPoint(
                    timestamp=row["timestamp"],
                    topic=row["topic"],
                    category=row["category"],
                    volume=row["volume"],
                    sentiment=row["sentiment"],
                    sources=row["sources"],
                    metadata=row["metadata"],
                )
            )

        return points

    def detect_anomalies(
        self, topic: str, category: str = "", window_hours: int = 168
    ) -> list[tuple[float, str]]:
        """
        Detect anomalies using z-score analysis.

        Args:
            topic: Topic name
            category: Optional category filter
            window_hours: Hours to analyze (default: 7 days)

        Returns:
            List of (timestamp, reason) tuples for anomalies
        """
        points = self.get_trend(topic, category, window_hours)
        if len(points) < 3:
            return []

        volumes = [p.volume for p in points]
        sentiments = [p.sentiment for p in points]

        try:
            vol_mean = statistics.mean(volumes)
            vol_stdev = statistics.stdev(volumes) if len(volumes) > 1 else 0
            sent_mean = statistics.mean(sentiments)
            sent_stdev = statistics.stdev(sentiments) if len(sentiments) > 1 else 0
        except statistics.StatisticsError:
            return []

        anomalies = []
        for point in points:
            # Volume anomaly
            if vol_stdev > 0:
                vol_z = abs((point.volume - vol_mean) / vol_stdev)
                if vol_z >= Z_SCORE_ANOMALY:
                    anomalies.append(
                        (point.timestamp, f"Volume spike: {point.volume} (z={vol_z:.1f})")
                    )

            # Sentiment anomaly
            if sent_stdev > 0:
                sent_z = abs((point.sentiment - sent_mean) / sent_stdev)
                if sent_z >= Z_SCORE_ANOMALY:
                    anomalies.append(
                        (
                            point.timestamp,
                            f"Sentiment shift: {point.sentiment:.2f} (z={sent_z:.1f})",
                        )
                    )

        return anomalies

    def is_trending(
        self, topic: str, category: str = "", min_volume: int = 5
    ) -> TrendAnalysis:
        """
        Determine if a topic is trending with detailed analysis.

        Args:
            topic: Topic name
            category: Optional category filter
            min_volume: Minimum volume to consider trending

        Returns:
            TrendAnalysis object with detailed metrics
        """
        # Get data points for different windows
        points_24h = self.get_trend(topic, category, 24)
        points_7d = self.get_trend(topic, category, 168)
        points_30d = self.get_trend(topic, category, 720)

        if not points_24h:
            return TrendAnalysis(
                topic=topic,
                category=category or "Unknown",
                current_volume=0,
                avg_volume_24h=0,
                avg_volume_7d=0,
                volume_change_pct=0,
                current_sentiment=0,
                sentiment_change_24h=0,
                velocity=0,
                is_trending=False,
                is_spike=False,
                is_breakout=False,
                trend_direction="stable",
                z_score=0,
            )

        # Current metrics
        current = points_24h[-1]
        current_volume = current.volume
        current_sentiment = current.sentiment

        # Calculate averages
        avg_volume_24h = statistics.mean([p.volume for p in points_24h]) if points_24h else 0
        avg_volume_7d = statistics.mean([p.volume for p in points_7d]) if points_7d else avg_volume_24h
        avg_volume_30d = statistics.mean([p.volume for p in points_30d]) if points_30d else avg_volume_7d

        # Volume change
        volume_change_pct = (
            ((current_volume - avg_volume_7d) / avg_volume_7d * 100)
            if avg_volume_7d > 0
            else 0
        )

        # Sentiment change
        if len(points_24h) >= 2:
            prev_sentiment = statistics.mean([p.sentiment for p in points_24h[:-1]])
            sentiment_change_24h = current_sentiment - prev_sentiment
        else:
            sentiment_change_24h = 0

        # Velocity (acceleration of growth)
        velocity = 0.0
        if len(points_7d) >= 2:
            # Compare recent growth rate to historical growth rate
            recent_growth = (
                (avg_volume_24h - avg_volume_7d) / avg_volume_7d
                if avg_volume_7d > 0
                else 0
            )
            historical_growth = (
                (avg_volume_7d - avg_volume_30d) / avg_volume_30d
                if avg_volume_30d > 0
                else 0
            )
            velocity = (
                recent_growth / historical_growth if historical_growth != 0 else 0
            )

        # Detection flags
        is_spike = current_volume >= avg_volume_7d * SPIKE_THRESHOLD and current_volume >= min_volume
        is_breakout = len(points_7d) < 3 and current_volume >= min_volume  # New topic with activity
        is_trending = (
            is_spike
            or (velocity >= VELOCITY_THRESHOLD and current_volume >= min_volume)
            or (abs(sentiment_change_24h) >= SENTIMENT_SHIFT_THRESHOLD and current_volume >= min_volume)
        )

        # Trend direction
        if volume_change_pct > 20:
            trend_direction = "up"
        elif volume_change_pct < -20:
            trend_direction = "down"
        else:
            trend_direction = "stable"

        # Z-score
        if len(points_7d) > 1:
            vol_mean = statistics.mean([p.volume for p in points_7d])
            vol_stdev = statistics.stdev([p.volume for p in points_7d])
            z_score = (
                (current_volume - vol_mean) / vol_stdev if vol_stdev > 0 else 0
            )
        else:
            z_score = 0

        # Peak time (when volume was highest in last 24h)
        peak_time = None
        if points_24h:
            peak_point = max(points_24h, key=lambda p: p.volume)
            peak_time = peak_point.timestamp

        # Collect unique sources
        all_sources = set()
        for point in points_24h:
            if point.sources:
                all_sources.update(point.sources.split(","))

        return TrendAnalysis(
            topic=topic,
            category=category or current.category,
            current_volume=current_volume,
            avg_volume_24h=avg_volume_24h,
            avg_volume_7d=avg_volume_7d,
            volume_change_pct=volume_change_pct,
            current_sentiment=current_sentiment,
            sentiment_change_24h=sentiment_change_24h,
            velocity=velocity,
            is_trending=is_trending,
            is_spike=is_spike,
            is_breakout=is_breakout,
            trend_direction=trend_direction,
            z_score=z_score,
            peak_time=peak_time,
            sources=list(all_sources),
        )

    def get_trending_topics(
        self, category: str = "", hours: int = 24, limit: int = 10
    ) -> list[TrendAnalysis]:
        """
        Get top trending topics across all tracked entities.

        Args:
            category: Optional category filter
            hours: Hours to analyze
            limit: Maximum number of results

        Returns:
            List of TrendAnalysis objects sorted by trending score
        """
        cutoff = time.time() - (hours * 3600)
        db = self._get_db()

        # Get all unique topics in the time window
        if category:
            cursor = db.execute(
                """
                SELECT DISTINCT topic, category
                FROM trend_data
                WHERE category = ? AND timestamp >= ?
            """,
                (category, cutoff),
            )
        else:
            cursor = db.execute(
                """
                SELECT DISTINCT topic, category
                FROM trend_data
                WHERE timestamp >= ?
            """,
                (cutoff,),
            )

        # Analyze each topic
        analyses = []
        for row in cursor:
            analysis = self.is_trending(row["topic"], row["category"])
            if analysis.is_trending:
                analyses.append(analysis)

        # Sort by trending score (combination of volume change and velocity)
        analyses.sort(
            key=lambda a: (a.volume_change_pct * (1 + a.velocity)), reverse=True
        )

        return analyses[:limit]

    def cleanup_old_data(self, days: int = DATA_RETENTION_DAYS) -> int:
        """
        Remove data points older than specified days.

        Args:
            days: Number of days to retain

        Returns:
            Number of rows deleted
        """
        cutoff = time.time() - (days * 86400)
        db = self._get_db()
        cursor = db.execute("DELETE FROM trend_data WHERE timestamp < ?", (cutoff,))
        db.commit()
        deleted = cursor.rowcount
        log.info("Cleaned up %d old trend data points (>%d days)", deleted, days)
        return deleted

    def enable_tracking(
        self,
        topic: str,
        category: str,
        user_id: str = "",
        spike_threshold: float = SPIKE_THRESHOLD,
        sentiment_threshold: float = SENTIMENT_SHIFT_THRESHOLD,
    ) -> bool:
        """
        Enable tracking for a topic with custom thresholds.

        Args:
            topic: Topic to track
            category: Category
            user_id: User who enabled tracking
            spike_threshold: Custom spike threshold
            sentiment_threshold: Custom sentiment threshold

        Returns:
            True if successful
        """
        try:
            db = self._get_db()
            db.execute(
                """
                INSERT INTO trend_config (topic, category, enabled, spike_threshold, sentiment_threshold, created_at, user_id)
                VALUES (?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(topic) DO UPDATE SET
                    category = excluded.category,
                    enabled = 1,
                    spike_threshold = excluded.spike_threshold,
                    sentiment_threshold = excluded.sentiment_threshold,
                    user_id = excluded.user_id
            """,
                (topic, category, spike_threshold, sentiment_threshold, time.time(), user_id),
            )
            db.commit()
            log.info("Enabled tracking: %s/%s by %s", category, topic, user_id)
            return True
        except Exception as e:  # broad: intentional — DB ops can raise sqlite3.Error or RuntimeError from mocks
            log.error("Failed to enable tracking for %s: %s", topic, e)
            return False

    def disable_tracking(self, topic: str) -> bool:
        """
        Disable tracking for a topic.

        Args:
            topic: Topic to stop tracking

        Returns:
            True if successful
        """
        try:
            db = self._get_db()
            db.execute("UPDATE trend_config SET enabled = 0 WHERE topic = ?", (topic,))
            db.commit()
            log.info("Disabled tracking: %s", topic)
            return True
        except Exception as e:  # broad: intentional — DB ops can raise sqlite3.Error or RuntimeError from mocks
            log.error("Failed to disable tracking for %s: %s", topic, e)
            return False

    def get_tracked_topics(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        """
        Get list of topics being tracked.

        Args:
            enabled_only: Only return enabled topics

        Returns:
            List of topic configuration dicts
        """
        db = self._get_db()
        if enabled_only:
            cursor = db.execute(
                "SELECT * FROM trend_config WHERE enabled = 1 ORDER BY created_at DESC"
            )
        else:
            cursor = db.execute("SELECT * FROM trend_config ORDER BY created_at DESC")

        topics = []
        for row in cursor:
            topics.append(dict(row))

        return topics

    def can_alert(self, topic: str, cooldown_seconds: int = 3600) -> bool:
        """
        Check if enough time has passed since last alert for this topic.

        Args:
            topic: Topic name
            cooldown_seconds: Minimum seconds between alerts

        Returns:
            True if alert is allowed
        """
        db = self._get_db()
        cursor = db.execute(
            "SELECT last_alert FROM trend_config WHERE topic = ?", (topic,)
        )
        row = cursor.fetchone()

        if not row:
            return True

        last_alert: float = row["last_alert"]
        return (time.time() - last_alert) >= cooldown_seconds

    def record_alert(self, topic: str) -> bool:
        """
        Record that an alert was sent for a topic.

        Args:
            topic: Topic name

        Returns:
            True if successful
        """
        try:
            db = self._get_db()
            db.execute(
                "UPDATE trend_config SET last_alert = ? WHERE topic = ?",
                (time.time(), topic),
            )
            db.commit()
            return True
        except Exception as e:  # broad: intentional — DB ops can raise sqlite3.Error or RuntimeError from mocks
            log.error("Failed to record alert for %s: %s", topic, e)
            return False


# Global singleton
_tracker: TrendTracker | None = None


def get_tracker() -> TrendTracker:
    """Get or create the global TrendTracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = TrendTracker()
    return _tracker
