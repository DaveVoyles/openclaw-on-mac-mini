"""
Tests for nas.py — Synology DSM API skills.

Covers: storage health formatting, folder creation with path-traversal
rejection, file-write validation, raw login, and SSRF-style path guards.
All aiohttp calls are mocked.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import nas as mod


@pytest.fixture(autouse=True)
def _isolate_nas_globals(monkeypatch):
    """Reset module-level session/SID state between tests."""
    monkeypatch.setattr(mod, "_nas_session", None)
    monkeypatch.setattr(mod, "_cached_sid", None)
    monkeypatch.setattr(mod, "_sid_obtained_at", 0.0)
    monkeypatch.setattr(mod, "_sid_lock", None)
    monkeypatch.setattr(mod, "NAS_USER", "admin")
    monkeypatch.setattr(mod, "NAS_PASSWORD", "secret")
    monkeypatch.setattr(mod, "NAS_URL", "https://nas.local:5001")


# ---------------------------------------------------------------------------
# _raw_login
# ---------------------------------------------------------------------------


class TestRawLogin:
    async def test_login_success(self, monkeypatch):
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={"success": True, "data": {"sid": "abc123"}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        sid = await mod._raw_login(mock_session)
        assert sid == "abc123"

    async def test_login_failure(self, monkeypatch):
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(
            return_value={"success": False, "error": {"code": 403}}
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        sid = await mod._raw_login(mock_session)
        assert sid is None

    async def test_login_network_error(self, monkeypatch):
        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=OSError("connection refused"))

        sid = await mod._raw_login(mock_session)
        assert sid is None


# ---------------------------------------------------------------------------
# nas_create_folder — path traversal rejection
# ---------------------------------------------------------------------------


class TestNasCreateFolder:
    async def test_path_traversal_rejected(self):
        result = await mod.nas_create_folder("../etc/passwd")
        assert "traversal" in result.lower()

    async def test_relative_path_rejected(self):
        result = await mod.nas_create_folder("volume1/share")
        assert "absolute path" in result.lower()

    async def test_dotdot_in_middle_rejected(self):
        result = await mod.nas_create_folder("/volume1/../etc")
        assert "traversal" in result.lower()

    async def test_success_response(self):
        with patch.object(
            mod, "_dsm", AsyncMock(return_value={"success": True})
        ):
            result = await mod.nas_create_folder("/volume1/documents/new")
        assert "✅" in result
        assert "/volume1/documents/new" in result

    async def test_missing_creds(self, monkeypatch):
        monkeypatch.setattr(mod, "NAS_USER", "")
        monkeypatch.setattr(mod, "NAS_PASSWORD", "")
        result = await mod.nas_create_folder("/volume1/test")
        assert "not configured" in result


# ---------------------------------------------------------------------------
# nas_write_file — filename validation
# ---------------------------------------------------------------------------


class TestNasWriteFile:
    async def test_dotdot_in_filename_rejected(self):
        result = await mod.nas_write_file("data", "/volume1/docs", "../evil.txt")
        assert "Invalid filename" in result

    async def test_slash_in_filename_rejected(self):
        result = await mod.nas_write_file("data", "/volume1/docs", "sub/file.txt")
        assert "Invalid filename" in result

    async def test_missing_creds(self, monkeypatch):
        monkeypatch.setattr(mod, "NAS_USER", "")
        monkeypatch.setattr(mod, "NAS_PASSWORD", "")
        result = await mod.nas_write_file("data", "/volume1/docs", "file.md")
        assert "not configured" in result


# ---------------------------------------------------------------------------
# get_nas_storage_health
# ---------------------------------------------------------------------------


class TestNasStorageHealth:
    async def test_format_output(self):
        util_resp = {
            "success": True,
            "data": {
                "cpu": {"1min_load": 12},
                "memory": {"total_real": 8192000, "real_usage": 45},
                "space": {
                    "volume": [
                        {"display_name": "Volume 1", "utilization": 30},
                    ]
                },
                "disk": {
                    "disk": [
                        {"display_name": "Drive 1", "type": "internal", "utilization": 15},
                    ]
                },
            },
        }
        sys_resp = {
            "success": True,
            "data": {
                "model": "DS920+",
                "firmware_ver": "7.2",
                "sys_temp": 42,
                "up_time": "5 days",
            },
        }
        with patch.object(
            mod, "_dsm", AsyncMock(side_effect=[util_resp, sys_resp])
        ):
            result = await mod.get_nas_storage_health()

        assert "NAS System Overview" in result
        assert "DS920+" in result
        assert "Volume 1" in result
        assert "Drive 1" in result
        assert "45%" in result  # memory

    async def test_failure_response(self):
        err_resp = {"success": False, "_err": "auth failed"}
        ok_resp = {"success": False}
        with patch.object(
            mod, "_dsm", AsyncMock(side_effect=[err_resp, ok_resp])
        ):
            result = await mod.get_nas_storage_health()
        assert "❌" in result
