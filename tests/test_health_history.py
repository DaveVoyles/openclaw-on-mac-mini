"""Tests for src/health_history.py — HealthHistory with SQLite."""

import time

import pytest

import health_history as hh
from health_history import HealthHistory


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module-level singleton between tests."""
    hh._instance = None
    yield
    if hh._instance is not None:
        try:
            hh._instance.db.close()
        except Exception:
            pass
    hh._instance = None


@pytest.fixture
def history(tmp_path):
    db_path = tmp_path / "health.db"
    return HealthHistory(db_path=db_path)


class TestHealthHistoryRecord:
    def test_record_stores_entry(self, history):
        history.record("plex", "ok", "all good")
        rows = history.db.execute("SELECT * FROM health_checks").fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "plex"
        assert rows[0][2] == "ok"

    def test_record_multiple_entries(self, history):
        history.record("plex", "ok")
        history.record("sonarr", "degraded", "slow response")
        history.record("radarr", "down", "connection refused")
        rows = history.db.execute("SELECT * FROM health_checks").fetchall()
        assert len(rows) == 3

    def test_record_without_message(self, history):
        history.record("plex", "ok")
        rows = history.db.execute("SELECT * FROM health_checks").fetchall()
        assert rows[0][3] == ""


class TestHealthHistoryDisk:
    def test_record_disk_stores_entry(self, history):
        history.record_disk("/", 500.0, 250.0, 250.0, 50.0)
        rows = history.db.execute("SELECT * FROM disk_usage").fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "/"
        assert rows[0][2] == pytest.approx(500.0)

    def test_predict_full_insufficient_data(self, history):
        result = history.predict_full("/")
        assert result["prediction"] == "insufficient data"
        assert result["days_until_full"] is None

    def test_predict_full_stable_usage(self, history):
        # Record same usage twice — stable, slope = 0
        history.record_disk("/", 500.0, 100.0, 400.0, 20.0)
        # Adjust timestamps slightly so regression has data
        history.db.execute("UPDATE disk_usage SET timestamp = timestamp - 86400 WHERE rowid = 1")
        history.record_disk("/", 500.0, 100.0, 400.0, 20.0)
        result = history.predict_full("/")
        assert "stable" in result["prediction"].lower() or result["days_until_full"] is None

    def test_predict_full_growing_usage(self, history):
        # Two records with growing usage over time
        history.db.execute(
            "INSERT INTO disk_usage (mount_point, total_gb, used_gb, free_gb, percent_used, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("/data", 1000.0, 100.0, 900.0, 10.0, time.time() - 86400 * 10),
        )
        history.db.execute(
            "INSERT INTO disk_usage (mount_point, total_gb, used_gb, free_gb, percent_used, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("/data", 1000.0, 200.0, 800.0, 20.0, time.time()),
        )
        history.db.commit()
        result = history.predict_full("/data")
        assert result["days_until_full"] is not None
        assert result["growth_rate_gb_day"] > 0


class TestHealthHistoryTrend:
    def test_trend_empty_service(self, history):
        result = history.get_trend("unknown-svc", days=1)
        assert result["total_checks"] == 0
        assert result["uptime_pct"] == 0

    def test_trend_all_ok(self, history):
        for _ in range(5):
            history.record("plex", "ok")
        result = history.get_trend("plex", days=1)
        assert result["total_checks"] == 5
        assert result["uptime_pct"] == pytest.approx(100.0)

    def test_trend_mixed_statuses(self, history):
        history.record("svc", "ok")
        history.record("svc", "ok")
        history.record("svc", "down", "error")
        result = history.get_trend("svc", days=1)
        assert result["uptime_pct"] == pytest.approx(2 / 3 * 100, abs=0.2)
        assert len(result["recent_incidents"]) == 1

    def test_sparkline_length(self, history):
        history.record("plex", "ok")
        result = history.get_trend("plex", days=1)
        assert len(result["sparkline"]) == 24


class TestModuleLevelWrappers:
    def test_record_wrapper(self, tmp_path, monkeypatch):
        db_path = tmp_path / "health.db"
        instance = HealthHistory(db_path=db_path)
        monkeypatch.setattr(hh, "_instance", instance)
        hh.record("test-svc", "ok", "msg")
        rows = instance.db.execute("SELECT * FROM health_checks").fetchall()
        assert len(rows) == 1

    def test_get_trend_wrapper(self, tmp_path, monkeypatch):
        db_path = tmp_path / "health.db"
        instance = HealthHistory(db_path=db_path)
        monkeypatch.setattr(hh, "_instance", instance)
        result = hh.get_trend("svc", days=1)
        assert "service" in result

    def test_predict_full_wrapper(self, tmp_path, monkeypatch):
        db_path = tmp_path / "health.db"
        instance = HealthHistory(db_path=db_path)
        monkeypatch.setattr(hh, "_instance", instance)
        result = hh.predict_full("/")
        assert "mount_point" in result
