"""
Tests for uptime_kuma_skills.py — Uptime Kuma monitor queries.

Covers: get_all_monitor_status, get_monitor_detail, get_monitors_down,
get_uptime_summary, and error handling for connection failures.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

import uptime_kuma_skills as uks


# ---------------------------------------------------------------------------
# Helpers — reusable heartbeat fixtures
# ---------------------------------------------------------------------------

def _heartbeat_payload(monitors: list[dict]) -> dict:
    """Build a heartbeat API response from a list of monitor dicts.

    Each monitor dict should have at least 'name' and 'status'.
    Optional: 'ping', 'msg', extra beats for history.
    """
    heartbeat_list = {}
    for i, mon in enumerate(monitors, start=1):
        beats = mon.get("beats", None)
        if beats is None:
            beats = [{
                "name": mon["name"],
                "status": mon["status"],
                "ping": mon.get("ping", 42),
                "msg": mon.get("msg", ""),
            }]
        heartbeat_list[str(i)] = beats
    return {"heartbeatList": heartbeat_list}


@pytest.fixture()
def patch_heartbeat(monkeypatch):
    """Return a helper that patches _fetch_heartbeat to return given data."""
    def _patch(payload: dict):
        monkeypatch.setattr(uks, "_fetch_heartbeat", AsyncMock(return_value=payload))
    return _patch


@pytest.fixture()
def patch_heartbeat_error(monkeypatch):
    """Patch _fetch_heartbeat to raise aiohttp.ClientError."""
    monkeypatch.setattr(
        uks,
        "_fetch_heartbeat",
        AsyncMock(side_effect=aiohttp.ClientError("connection refused")),
    )


# ---------------------------------------------------------------------------
# get_all_monitor_status
# ---------------------------------------------------------------------------

class TestGetAllMonitorStatus:
    @pytest.mark.asyncio
    async def test_success(self, patch_heartbeat):
        payload = _heartbeat_payload([
            {"name": "Sonarr", "status": 1, "ping": 10},
            {"name": "Radarr", "status": 1, "ping": 20},
            {"name": "Plex",   "status": 0, "ping": 0, "msg": "timeout"},
        ])
        patch_heartbeat(payload)

        result = await uks.get_all_monitor_status()

        assert "Sonarr" in result
        assert "Radarr" in result
        assert "Plex" in result
        assert "2 up" in result
        assert "1 down" in result
        assert "📡" in result

    @pytest.mark.asyncio
    async def test_connection_error(self, patch_heartbeat_error):
        result = await uks.get_all_monitor_status()

        assert "❌" in result
        assert "Could not reach Uptime Kuma" in result


# ---------------------------------------------------------------------------
# get_monitor_detail
# ---------------------------------------------------------------------------

class TestGetMonitorDetail:
    @pytest.mark.asyncio
    async def test_found(self, patch_heartbeat):
        payload = _heartbeat_payload([
            {"name": "Sonarr", "status": 1, "ping": 15},
            {"name": "Radarr", "status": 1, "ping": 22},
        ])
        patch_heartbeat(payload)

        result = await uks.get_monitor_detail("sonarr")

        assert "Sonarr" in result
        assert "UP" in result
        assert "15ms" in result
        assert "100.0%" in result

    @pytest.mark.asyncio
    async def test_not_found(self, patch_heartbeat):
        payload = _heartbeat_payload([
            {"name": "Sonarr", "status": 1},
            {"name": "Radarr", "status": 1},
        ])
        patch_heartbeat(payload)

        result = await uks.get_monitor_detail("Lidarr")

        assert "No monitor matching" in result
        assert "Lidarr" in result
        assert "Sonarr" in result
        assert "Radarr" in result


# ---------------------------------------------------------------------------
# get_monitors_down
# ---------------------------------------------------------------------------

class TestGetMonitorsDown:
    @pytest.mark.asyncio
    async def test_all_up(self, patch_heartbeat):
        payload = _heartbeat_payload([
            {"name": "Sonarr", "status": 1},
            {"name": "Radarr", "status": 1},
            {"name": "Plex",   "status": 1},
        ])
        patch_heartbeat(payload)

        result = await uks.get_monitors_down()

        assert "All 3 monitors are UP!" in result

    @pytest.mark.asyncio
    async def test_some_down(self, patch_heartbeat):
        payload = _heartbeat_payload([
            {"name": "Sonarr", "status": 1},
            {"name": "Plex",   "status": 0, "msg": "refused"},
            {"name": "Lidarr", "status": 0, "msg": "dns fail"},
        ])
        patch_heartbeat(payload)

        result = await uks.get_monitors_down()

        assert "2 monitor(s) DOWN" in result
        assert "Plex" in result
        assert "Lidarr" in result
        assert "Sonarr" not in result


# ---------------------------------------------------------------------------
# get_uptime_summary
# ---------------------------------------------------------------------------

class TestGetUptimeSummary:
    @pytest.mark.asyncio
    async def test_percentage_calculations(self, patch_heartbeat):
        # Sonarr: 8/10 up = 80%, Radarr: 10/10 up = 100%
        sonarr_beats = [
            {"name": "Sonarr", "status": 1, "ping": 10}
        ] * 8 + [
            {"name": "Sonarr", "status": 0, "ping": 0}
        ] * 2
        radarr_beats = [
            {"name": "Radarr", "status": 1, "ping": 20}
        ] * 10

        payload = {
            "heartbeatList": {
                "1": sonarr_beats,
                "2": radarr_beats,
            }
        }
        patch_heartbeat(payload)

        result = await uks.get_uptime_summary()

        assert "80.0%" in result
        assert "100.0%" in result
        # Sorted worst-first, so Sonarr (80%) should appear before Radarr (100%)
        sonarr_pos = result.index("Sonarr")
        radarr_pos = result.index("Radarr")
        assert sonarr_pos < radarr_pos
        # Overall average: (80 + 100) / 2 = 90%
        assert "90.0%" in result
