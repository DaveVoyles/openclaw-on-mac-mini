"""Tests for notification_prefs.py — UserNotifPrefs and NotificationPrefsStore."""

import asyncio
import json
import os

# Ensure src/ is importable
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from notification_prefs import NotificationPrefsStore


@pytest.fixture()
def store(tmp_path: Path) -> NotificationPrefsStore:
    return NotificationPrefsStore(path=tmp_path / "prefs.json")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_default_prefs_all_enabled(self, store: NotificationPrefsStore):
        prefs = store.get(1234)
        assert prefs.enabled is True
        assert prefs.dm_alerts is False
        assert prefs.muted_until == 0.0
        assert prefs.severity_filter == "all"
        assert prefs.blocked_services == []

    def test_should_notify_by_default(self, store: NotificationPrefsStore):
        assert store.should_notify(1234) is True
        assert store.should_notify(1234, service="sonarr", severity="info") is True


# ---------------------------------------------------------------------------
# Muting / Unmuting
# ---------------------------------------------------------------------------

class TestMuting:
    def test_muted_user_not_notified(self, store: NotificationPrefsStore):
        prefs = store.get(1)
        prefs.muted_until = time.time() + 3600
        store._prefs[1] = prefs
        assert store.should_notify(1) is False

    def test_expired_mute_allows_notification(self, store: NotificationPrefsStore):
        prefs = store.get(2)
        prefs.muted_until = time.time() - 10  # expired
        store._prefs[2] = prefs
        assert store.should_notify(2) is True

    def test_unmute_resets_timestamp(self, store: NotificationPrefsStore):
        prefs = store.get(3)
        prefs.muted_until = time.time() + 3600
        store._prefs[3] = prefs
        assert store.should_notify(3) is False
        prefs.muted_until = 0.0
        store._prefs[3] = prefs
        assert store.should_notify(3) is True


# ---------------------------------------------------------------------------
# Service Blocking
# ---------------------------------------------------------------------------

class TestServiceBlocking:
    def test_blocked_service_filtered(self, store: NotificationPrefsStore):
        prefs = store.get(10)
        prefs.blocked_services = ["sonarr"]
        store._prefs[10] = prefs
        assert store.should_notify(10, service="sonarr") is False
        assert store.should_notify(10, service="radarr") is True

    def test_blocking_is_case_insensitive(self, store: NotificationPrefsStore):
        prefs = store.get(11)
        prefs.blocked_services = ["Sonarr"]
        store._prefs[11] = prefs
        assert store.should_notify(11, service="sonarr") is False
        assert store.should_notify(11, service="SONARR") is False


# ---------------------------------------------------------------------------
# Severity Filtering
# ---------------------------------------------------------------------------

class TestSeverityFiltering:
    def test_filter_all_passes_everything(self, store: NotificationPrefsStore):
        prefs = store.get(20)
        prefs.severity_filter = "all"
        store._prefs[20] = prefs
        assert store.should_notify(20, severity="info") is True
        assert store.should_notify(20, severity="warning") is True
        assert store.should_notify(20, severity="critical") is True

    def test_filter_warning_blocks_info(self, store: NotificationPrefsStore):
        prefs = store.get(21)
        prefs.severity_filter = "warning"
        store._prefs[21] = prefs
        assert store.should_notify(21, severity="info") is False
        assert store.should_notify(21, severity="warning") is True
        assert store.should_notify(21, severity="critical") is True

    def test_filter_critical_blocks_info_and_warning(self, store: NotificationPrefsStore):
        prefs = store.get(22)
        prefs.severity_filter = "critical"
        store._prefs[22] = prefs
        assert store.should_notify(22, severity="info") is False
        assert store.should_notify(22, severity="warning") is False
        assert store.should_notify(22, severity="critical") is True


# ---------------------------------------------------------------------------
# Disabled
# ---------------------------------------------------------------------------

class TestDisabled:
    def test_disabled_user_not_notified(self, store: NotificationPrefsStore):
        prefs = store.get(30)
        prefs.enabled = False
        store._prefs[30] = prefs
        assert store.should_notify(30, severity="critical") is False


# ---------------------------------------------------------------------------
# Persistence (save / load round-trip)
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_load_round_trip(self, tmp_path: Path):
        path = tmp_path / "prefs.json"
        store1 = NotificationPrefsStore(path=path)

        prefs = store1.get(99)
        prefs.dm_alerts = True
        prefs.severity_filter = "critical"
        prefs.blocked_services = ["sabnzbd", "lidarr"]
        prefs.muted_until = 9999999999.0

        asyncio.run(store1.update(prefs))

        # Load into a fresh store from same file
        store2 = NotificationPrefsStore(path=path)
        loaded = store2.get(99)
        assert loaded.dm_alerts is True
        assert loaded.severity_filter == "critical"
        assert loaded.blocked_services == ["sabnzbd", "lidarr"]
        assert loaded.muted_until == 9999999999.0

    def test_file_created_on_first_save(self, tmp_path: Path):
        path = tmp_path / "subdir" / "prefs.json"
        store = NotificationPrefsStore(path=path)
        asyncio.run(store.update(store.get(1)))
        assert path.exists()
        data = json.loads(path.read_text())
        assert "1" in data
