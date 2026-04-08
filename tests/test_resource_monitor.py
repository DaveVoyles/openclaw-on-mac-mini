"""Tests for src/resource_monitor.py — ResourceMonitor CRUD and threshold checks."""
import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import resource_monitor as rm  # noqa: E402
from resource_monitor import ResourceMonitor, ResourceThreshold


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    path = tmp_path / "monitors.json"
    monkeypatch.setattr(rm, "MONITOR_FILE", path)
    m = ResourceMonitor()
    return m


class TestResourceMonitorLoad:
    def test_starts_empty_when_no_file(self, monitor):
        assert monitor._thresholds == {}

    def test_loads_existing_data(self, tmp_path, monkeypatch):
        path = tmp_path / "monitors.json"
        data = {
            "mycontainer": {
                "container": "mycontainer",
                "cpu_percent": 75.0,
                "memory_percent": 85.0,
                "enabled": True,
                "last_alert": 0.0,
                "cooldown_seconds": 300,
            }
        }
        path.write_text(json.dumps(data))
        monkeypatch.setattr(rm, "MONITOR_FILE", path)
        m = ResourceMonitor()
        assert "mycontainer" in m._thresholds
        assert m._thresholds["mycontainer"].cpu_percent == 75.0

    def test_bad_json_starts_empty(self, tmp_path, monkeypatch):
        path = tmp_path / "monitors.json"
        path.write_text("not json")
        monkeypatch.setattr(rm, "MONITOR_FILE", path)
        m = ResourceMonitor()
        assert m._thresholds == {}


class TestSetThreshold:
    def test_set_threshold_creates_entry(self, monitor):
        t = monitor.set_threshold("redis", cpu=70.0, memory=80.0)
        assert isinstance(t, ResourceThreshold)
        assert t.cpu_percent == 70.0
        assert t.memory_percent == 80.0

    def test_set_threshold_persists(self, monitor):
        monitor.set_threshold("redis")
        saved = json.loads(rm.MONITOR_FILE.read_text())
        assert "redis" in saved

    def test_set_threshold_overwrites_existing(self, monitor):
        monitor.set_threshold("redis", cpu=50.0)
        monitor.set_threshold("redis", cpu=90.0)
        assert monitor._thresholds["redis"].cpu_percent == 90.0


class TestRemoveThreshold:
    def test_remove_existing_returns_true(self, monitor):
        monitor.set_threshold("redis")
        assert monitor.remove("redis") is True
        assert "redis" not in monitor._thresholds

    def test_remove_nonexistent_returns_false(self, monitor):
        assert monitor.remove("ghost") is False

    def test_remove_persists(self, monitor):
        monitor.set_threshold("redis")
        monitor.remove("redis")
        saved = json.loads(rm.MONITOR_FILE.read_text())
        assert "redis" not in saved


class TestListAll:
    def test_list_all_empty(self, monitor):
        assert monitor.list_all() == []

    def test_list_all_returns_thresholds(self, monitor):
        monitor.set_threshold("redis")
        monitor.set_threshold("postgres")
        result = monitor.list_all()
        assert len(result) == 2


class TestCheckAll:
    @pytest.mark.asyncio
    async def test_check_all_empty_thresholds(self, monitor):
        result = await monitor.check_all()
        assert result == []

    @pytest.mark.asyncio
    async def test_check_all_no_violations(self, monitor):
        monitor.set_threshold("redis", cpu=80.0, memory=90.0)
        with patch.object(
            monitor,
            "_get_stats_raw",
            new=AsyncMock(return_value=[{"name": "redis", "cpu": 10.0, "memory": 20.0}]),
        ):
            result = await monitor.check_all()
        assert result == []

    @pytest.mark.asyncio
    async def test_check_all_cpu_violation(self, monitor):
        monitor.set_threshold("redis", cpu=50.0, memory=90.0)
        with patch.object(
            monitor,
            "_get_stats_raw",
            new=AsyncMock(return_value=[{"name": "redis", "cpu": 95.0, "memory": 20.0}]),
        ):
            result = await monitor.check_all()
        assert len(result) == 1
        threshold, stats = result[0]
        assert threshold.container == "redis"
        assert stats["cpu"] == 95.0

    @pytest.mark.asyncio
    async def test_check_all_memory_violation(self, monitor):
        monitor.set_threshold("redis", cpu=80.0, memory=50.0)
        with patch.object(
            monitor,
            "_get_stats_raw",
            new=AsyncMock(return_value=[{"name": "redis", "cpu": 10.0, "memory": 95.0}]),
        ):
            result = await monitor.check_all()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_check_all_skips_disabled(self, monitor):
        monitor.set_threshold("redis")
        monitor._thresholds["redis"].enabled = False
        with patch.object(
            monitor,
            "_get_stats_raw",
            new=AsyncMock(return_value=[{"name": "redis", "cpu": 99.0, "memory": 99.0}]),
        ):
            result = await monitor.check_all()
        assert result == []

    @pytest.mark.asyncio
    async def test_check_all_respects_cooldown(self, monitor):
        monitor.set_threshold("redis", cpu=50.0)
        monitor._thresholds["redis"].last_alert = time.time()  # Just fired
        with patch.object(
            monitor,
            "_get_stats_raw",
            new=AsyncMock(return_value=[{"name": "redis", "cpu": 99.0, "memory": 10.0}]),
        ):
            result = await monitor.check_all()
        assert result == []

    @pytest.mark.asyncio
    async def test_check_all_stats_fetch_error_returns_empty(self, monitor):
        monitor.set_threshold("redis")
        with patch.object(
            monitor,
            "_get_stats_raw",
            new=AsyncMock(side_effect=RuntimeError("oops")),
        ):
            result = await monitor.check_all()
        assert result == []


class TestGetStatsRaw:
    @pytest.mark.asyncio
    async def test_docker_failure_returns_empty(self, monitor):
        with patch("resource_monitor._run", new=AsyncMock(return_value=(1, "", "err"))):
            result = await monitor._get_stats_raw()
        assert result == []

    @pytest.mark.asyncio
    async def test_parses_docker_output(self, monitor):
        output = "redis\t5.0%\t30.0%\npostgres\t2.0%\t50.0%"
        with patch("resource_monitor._run", new=AsyncMock(return_value=(0, output, ""))):
            result = await monitor._get_stats_raw()
        assert len(result) == 2
        assert result[0]["name"] == "redis"
        assert result[0]["cpu"] == pytest.approx(5.0)
