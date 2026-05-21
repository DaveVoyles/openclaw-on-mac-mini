"""Tests for audit.py and constants.py pure functions."""

from __future__ import annotations

from unittest.mock import MagicMock

from audit import _audit_buffer, audit_log

# ===========================================================================
# audit.py
# ===========================================================================


class TestAuditLog:
    def setup_method(self):
        _audit_buffer.clear()

    def test_appends_to_buffer(self):
        audit_log("user", "test_action", "details")
        assert len(_audit_buffer) == 1

    def test_entry_has_required_fields(self):
        audit_log("user", "cmd", "some detail", result="ok")
        entry = _audit_buffer[-1]
        assert "ts" in entry
        assert entry["action"] == "cmd"
        assert entry["detail"] == "some detail"
        assert entry["result"] == "ok"

    def test_none_user_defaults_to_system(self):
        audit_log(None, "action")
        assert _audit_buffer[-1]["user"] == "system"
        assert _audit_buffer[-1]["user_id"] == "0"

    def test_user_object_with_id(self):
        user = MagicMock()
        user.id = 42
        user.__str__ = lambda self: "TestUser"
        audit_log(user, "login")
        entry = _audit_buffer[-1]
        assert entry["user_id"] == "42"

    def test_audit_constants_alerts_default_result_is_success(self):
        audit_log("u", "act")
        assert _audit_buffer[-1]["result"] == "success"

    def test_detail_none_does_not_break(self):
        audit_log("u", "act", detail=None)
        assert _audit_buffer[-1]["detail"] == ""

    def test_detail_text_appears_in_record(self):
        audit_log("u", "act", detail="restart docker")
        assert _audit_buffer[-1]["detail"] == "restart docker"

    def test_default_severity_is_info(self):
        audit_log("u", "act")
        assert _audit_buffer[-1]["severity"] == "INFO"

    def test_severity_stored_uppercased(self):
        audit_log("u", "act", severity="high")
        assert _audit_buffer[-1]["severity"] == "HIGH"

    def test_high_severity_flushes_stdout(self, capsys):
        audit_log("u", "critical_action", detail="ctx", severity="CRITICAL")
        captured = capsys.readouterr()
        assert "AUDIT:CRITICAL" in captured.out
        assert "critical_action" in captured.out

    def test_info_severity_does_not_print(self, capsys):
        audit_log("u", "routine", severity="INFO")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_timestamp_is_iso_format(self):
        audit_log("u", "act")
        ts = _audit_buffer[-1]["ts"]
        # ISO 8601 with timezone
        assert "T" in ts and ("+" in ts or "Z" in ts or ts.endswith("+00:00"))

    def test_buffer_respects_maxlen(self):
        for i in range(10_001):
            audit_log("u", f"action_{i}")
        assert len(_audit_buffer) == 10_000


# ===========================================================================
# constants.py
# ===========================================================================


class TestConstants:
    def test_discord_limits_are_positive(self):
        from constants import DISCORD_MESSAGE_LIMIT, EMBED_DESC_LIMIT, EMBED_SPLIT_LIMIT

        assert DISCORD_MESSAGE_LIMIT > 0
        assert EMBED_DESC_LIMIT > 0
        assert EMBED_SPLIT_LIMIT > 0

    def test_embed_split_less_than_desc(self):
        from constants import EMBED_DESC_LIMIT, EMBED_SPLIT_LIMIT

        assert EMBED_SPLIT_LIMIT < EMBED_DESC_LIMIT

    def test_intervals_positive(self):
        from constants import AUDIT_FLUSH_INTERVAL, BRIEFING_CHECK_INTERVAL, CLEANUP_INTERVAL

        assert AUDIT_FLUSH_INTERVAL > 0
        assert CLEANUP_INTERVAL > 0
        assert BRIEFING_CHECK_INTERVAL > 0

    def test_max_file_size_is_mb_range(self):
        from constants import MAX_FILE_SIZE

        assert MAX_FILE_SIZE >= 1024 * 1024  # at least 1 MB
