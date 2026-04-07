"""
Tests for src/enhanced_logging.py

Covers: JSONFormatter, setup_logging, AuditLogger methods, get_audit_logger.
All file I/O and external calls are mocked.
"""

import json
import logging
import logging.handlers
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

# Patch the legacy audit module before importing the module under test
import sys

_mock_audit_mod = MagicMock()
sys.modules.setdefault("audit", _mock_audit_mod)

import enhanced_logging as mod
from enhanced_logging import (
    AuditLogger,
    JSONFormatter,
    get_audit_logger,
    setup_logging,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    name="test_logger",
    level=logging.INFO,
    msg="hello world",
    exc_info=None,
    **extra,
):
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="test_file.py",
        lineno=42,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


# ---------------------------------------------------------------------------
# JSONFormatter
# ---------------------------------------------------------------------------


class TestJSONFormatter:
    def setup_method(self):
        self.fmt = JSONFormatter()

    def test_basic_format(self):
        record = _make_record(msg="basic message")
        output = self.fmt.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["message"] == "basic message"
        assert data["logger"] == "test_logger"
        assert data["line"] == 42
        assert data["timestamp"].endswith("Z")

    def test_includes_module_and_function(self):
        record = _make_record()
        data = json.loads(self.fmt.format(record))
        assert "module" in data
        assert "function" in data

    def test_exception_info_included(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = _make_record(exc_info=exc_info)
        data = json.loads(self.fmt.format(record))
        assert "exception" in data
        assert "ValueError" in data["exception"]

    def test_no_exception_when_none(self):
        record = _make_record()
        data = json.loads(self.fmt.format(record))
        assert "exception" not in data

    def test_extra_user_id(self):
        record = _make_record(user_id="user-123")
        data = json.loads(self.fmt.format(record))
        assert data["user_id"] == "user-123"

    def test_extra_correlation_id(self):
        record = _make_record(correlation_id="corr-abc")
        data = json.loads(self.fmt.format(record))
        assert data["correlation_id"] == "corr-abc"

    def test_extra_command(self):
        record = _make_record(command="!status")
        data = json.loads(self.fmt.format(record))
        assert data["command"] == "!status"

    def test_extra_metadata(self):
        meta = {"key": "value"}
        record = _make_record(metadata=meta)
        data = json.loads(self.fmt.format(record))
        assert data["metadata"] == meta

    def test_missing_extra_fields_not_included(self):
        record = _make_record()
        data = json.loads(self.fmt.format(record))
        for field in ("user_id", "correlation_id", "command", "metadata"):
            assert field not in data

    def test_warning_level(self):
        record = _make_record(level=logging.WARNING, msg="warn!")
        data = json.loads(self.fmt.format(record))
        assert data["level"] == "WARNING"

    def test_error_level(self):
        record = _make_record(level=logging.ERROR, msg="err!")
        data = json.loads(self.fmt.format(record))
        assert data["level"] == "ERROR"

    def test_output_is_valid_json(self):
        record = _make_record(msg='message with "quotes" and {braces}')
        output = self.fmt.format(record)
        data = json.loads(output)  # must not raise
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


class TestSetupLogging:
    def test_creates_log_dir(self, tmp_path):
        log_dir = tmp_path / "test_logs"
        setup_logging(log_dir=log_dir)
        assert log_dir.exists()

    def test_existing_log_dir_ok(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        setup_logging(log_dir=log_dir)  # should not raise

    def test_root_logger_level_set(self, tmp_path):
        setup_logging(log_dir=tmp_path, log_level=logging.DEBUG)
        assert logging.getLogger().level == logging.DEBUG

    def test_handlers_added_to_root(self, tmp_path):
        root = logging.getLogger()
        root.handlers.clear()
        setup_logging(log_dir=tmp_path)
        # console + file + error = at least 3
        assert len(root.handlers) >= 3

    def test_old_handlers_cleared(self, tmp_path):
        root = logging.getLogger()
        dummy = logging.StreamHandler()
        root.addHandler(dummy)
        handler_count_before = len(root.handlers)
        setup_logging(log_dir=tmp_path)
        # dummy should be gone; handlers should be fresh set
        assert dummy not in root.handlers

    def test_json_formatter_when_enabled(self, tmp_path):
        setup_logging(log_dir=tmp_path, enable_json=True)
        root = logging.getLogger()
        file_handlers = [
            h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert any(isinstance(h.formatter, JSONFormatter) for h in file_handlers)

    def test_standard_formatter_when_json_disabled(self, tmp_path):
        setup_logging(log_dir=tmp_path, enable_json=False)
        root = logging.getLogger()
        file_handlers = [
            h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert any(not isinstance(h.formatter, JSONFormatter) for h in file_handlers)

    def test_audit_logger_created(self, tmp_path):
        setup_logging(log_dir=tmp_path)
        audit_logger = logging.getLogger("audit")
        assert audit_logger.level == logging.INFO
        assert not audit_logger.propagate

    def test_error_log_handler_level(self, tmp_path):
        setup_logging(log_dir=tmp_path)
        root = logging.getLogger()
        error_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
            and h.level == logging.ERROR
        ]
        assert len(error_handlers) >= 1

    def test_log_files_in_correct_dir(self, tmp_path):
        setup_logging(log_dir=tmp_path)
        # After setup the files are created (or will be on first log)
        root = logging.getLogger()
        rotating = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        base_dirs = {Path(h.baseFilename).parent for h in rotating}
        assert tmp_path in base_dirs

    def test_custom_max_bytes(self, tmp_path):
        setup_logging(log_dir=tmp_path, max_bytes=1024)
        root = logging.getLogger()
        rotating = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert any(h.maxBytes == 1024 for h in rotating)

    def test_custom_backup_count(self, tmp_path):
        setup_logging(log_dir=tmp_path, backup_count=5)
        root = logging.getLogger()
        rotating = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert any(h.backupCount == 5 for h in rotating)


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_logger():
    """Return an AuditLogger with a mocked internal logger."""
    al = AuditLogger()
    al.logger = MagicMock()
    return al


@pytest.fixture(autouse=True)
def _reset_legacy_audit():
    """Reset the mock between tests."""
    _mock_audit_mod.audit_log.reset_mock()


class TestAuditLoggerLogUserAction:
    def test_calls_logger_info(self, audit_logger):
        audit_logger.log_user_action("u1", "join", "joined #general")
        audit_logger.logger.info.assert_called_once()

    def test_message_contains_action(self, audit_logger):
        audit_logger.log_user_action("u1", "leave")
        call_args = audit_logger.logger.info.call_args
        assert "leave" in call_args[0][0]

    def test_extra_has_user_id(self, audit_logger):
        audit_logger.log_user_action("user-42", "kick")
        extra = audit_logger.logger.info.call_args[1]["extra"]
        assert extra["user_id"] == "user-42"

    def test_calls_legacy_audit_log(self, audit_logger, monkeypatch):
        import enhanced_logging as _enh_mod
        mock_fn = MagicMock()
        monkeypatch.setattr(_enh_mod, "legacy_audit_log", mock_fn)
        audit_logger.log_user_action("u1", "test_action", "some detail", result="success")
        mock_fn.assert_called_once()

    def test_default_result_is_success(self, audit_logger):
        audit_logger.log_user_action("u1", "act")
        extra = audit_logger.logger.info.call_args[1]["extra"]
        assert extra["metadata"]["result"] == "success"

    def test_metadata_category(self, audit_logger):
        audit_logger.log_user_action("u1", "act")
        extra = audit_logger.logger.info.call_args[1]["extra"]
        assert extra["metadata"]["category"] == "user_action"

    def test_custom_metadata_merged(self, audit_logger):
        audit_logger.log_user_action("u1", "act", metadata={"foo": "bar"})
        extra = audit_logger.logger.info.call_args[1]["extra"]
        assert extra["metadata"]["metadata"]["foo"] == "bar"


class TestAuditLoggerLogCommandExecution:
    def test_success_uses_info_level(self, audit_logger):
        audit_logger.log_command_execution("u1", "!status", result="success")
        audit_logger.logger.log.assert_called_once()
        level = audit_logger.logger.log.call_args[0][0]
        assert level == logging.INFO

    def test_error_uses_error_level(self, audit_logger):
        audit_logger.log_command_execution("u1", "!crash", result="error")
        level = audit_logger.logger.log.call_args[0][0]
        assert level == logging.ERROR

    def test_message_contains_command(self, audit_logger):
        audit_logger.log_command_execution("u1", "!ping")
        msg = audit_logger.logger.log.call_args[0][1]
        assert "!ping" in msg

    def test_extra_has_command(self, audit_logger):
        audit_logger.log_command_execution("u1", "!test")
        extra = audit_logger.logger.log.call_args[1]["extra"]
        assert extra["command"] == "!test"

    def test_extra_metadata_has_parameters(self, audit_logger):
        audit_logger.log_command_execution("u1", "!cmd", parameters={"k": "v"})
        extra = audit_logger.logger.log.call_args[1]["extra"]
        assert extra["metadata"]["parameters"] == {"k": "v"}

    def test_category_is_command_execution(self, audit_logger):
        audit_logger.log_command_execution("u1", "!x")
        extra = audit_logger.logger.log.call_args[1]["extra"]
        assert extra["metadata"]["category"] == "command_execution"


class TestAuditLoggerLogPermissionChange:
    def test_calls_warning(self, audit_logger):
        audit_logger.log_permission_change("admin-1", "user-2", "promote")
        audit_logger.logger.warning.assert_called_once()

    def test_message_contains_action(self, audit_logger):
        audit_logger.log_permission_change("a", "u", "demote")
        msg = audit_logger.logger.warning.call_args[0][0]
        assert "demote" in msg

    def test_extra_metadata_has_category(self, audit_logger):
        audit_logger.log_permission_change("a", "u", "promote")
        extra = audit_logger.logger.warning.call_args[1]["extra"]
        assert extra["metadata"]["category"] == "permission_change"

    def test_extra_has_admin_user_id(self, audit_logger):
        audit_logger.log_permission_change("admin-99", "user-1", "x")
        extra = audit_logger.logger.warning.call_args[1]["extra"]
        assert extra["metadata"]["admin_user_id"] == "admin-99"


class TestAuditLoggerLogConfigChange:
    def test_calls_warning(self, audit_logger):
        audit_logger.log_config_change("u1", "max_retries", 3, 5)
        audit_logger.logger.warning.assert_called_once()

    def test_message_contains_key(self, audit_logger):
        audit_logger.log_config_change("u1", "log_level", "INFO", "DEBUG")
        msg = audit_logger.logger.warning.call_args[0][0]
        assert "log_level" in msg

    def test_values_stored_as_strings(self, audit_logger):
        audit_logger.log_config_change("u1", "k", 1, 2)
        extra = audit_logger.logger.warning.call_args[1]["extra"]
        assert extra["metadata"]["old_value"] == "1"
        assert extra["metadata"]["new_value"] == "2"

    def test_category_is_config_change(self, audit_logger):
        audit_logger.log_config_change("u1", "k", "a", "b")
        extra = audit_logger.logger.warning.call_args[1]["extra"]
        assert extra["metadata"]["category"] == "config_change"


class TestAuditLoggerLogSecurityEvent:
    def test_warning_severity(self, audit_logger):
        audit_logger.log_security_event("failed_login", severity="warning")
        audit_logger.logger.log.assert_called_once()
        assert audit_logger.logger.log.call_args[0][0] == logging.WARNING

    def test_error_severity(self, audit_logger):
        audit_logger.log_security_event("intrusion", severity="error")
        assert audit_logger.logger.log.call_args[0][0] == logging.ERROR

    def test_critical_severity(self, audit_logger):
        audit_logger.log_security_event("breach", severity="critical")
        assert audit_logger.logger.log.call_args[0][0] == logging.CRITICAL

    def test_info_severity(self, audit_logger):
        audit_logger.log_security_event("scan", severity="info")
        assert audit_logger.logger.log.call_args[0][0] == logging.INFO

    def test_unknown_severity_defaults_to_warning(self, audit_logger):
        audit_logger.log_security_event("x", severity="unknown_level")
        assert audit_logger.logger.log.call_args[0][0] == logging.WARNING

    def test_message_contains_event_type(self, audit_logger):
        audit_logger.log_security_event("my_event_type")
        msg = audit_logger.logger.log.call_args[0][1]
        assert "my_event_type" in msg

    def test_unknown_user_fallback(self, audit_logger):
        audit_logger.log_security_event("evt", user_id=None)
        extra = audit_logger.logger.log.call_args[1]["extra"]
        assert extra["metadata"]["user_id"] == "unknown"

    def test_category_is_security_event(self, audit_logger):
        audit_logger.log_security_event("evt")
        extra = audit_logger.logger.log.call_args[1]["extra"]
        assert extra["metadata"]["category"] == "security_event"


class TestAuditLoggerLogFailedAuth:
    def test_delegates_to_log_security_event(self, audit_logger):
        with patch.object(audit_logger, "log_security_event") as mock_se:
            audit_logger.log_failed_auth("bad_user", "wrong password")
            mock_se.assert_called_once_with(
                event_type="failed_auth",
                user_id="bad_user",
                detail="wrong password",
                severity="warning",
            )


class TestAuditLoggerLogSuspiciousActivity:
    def test_delegates_to_log_security_event(self, audit_logger):
        with patch.object(audit_logger, "log_security_event") as mock_se:
            audit_logger.log_suspicious_activity("u1", "port scanning")
            mock_se.assert_called_once_with(
                event_type="suspicious_activity",
                user_id="u1",
                detail="port scanning",
                severity="error",
            )


# ---------------------------------------------------------------------------
# AuditLogger.get_audit_logs
# ---------------------------------------------------------------------------


def _make_log_entry(user_id="u1", category="user_action", age_days=0):
    ts = (datetime.utcnow() - timedelta(days=age_days)).isoformat() + "Z"
    return json.dumps({
        "timestamp": ts,
        "user_id": user_id,
        "metadata": {"category": category},
        "level": "INFO",
        "message": "test",
    })


class TestGetAuditLogs:
    def test_returns_empty_when_file_missing(self, tmp_path):
        al = AuditLogger()
        with patch("enhanced_logging.Path") as mock_path_cls:
            mock_path_cls.return_value.exists.return_value = False
            result = al.get_audit_logs()
        assert result == []

    def test_reads_and_parses_logs(self):
        al = AuditLogger()
        entries = "\n".join([_make_log_entry("u1"), _make_log_entry("u2")])
        m = mock_open(read_data=entries)
        with patch("builtins.open", m), \
             patch("enhanced_logging.Path") as mp:
            mp.return_value.exists.return_value = True
            result = al.get_audit_logs()
        assert len(result) == 2

    def test_filters_by_user_id(self):
        al = AuditLogger()
        entries = "\n".join([_make_log_entry("alice"), _make_log_entry("bob")])
        m = mock_open(read_data=entries)
        with patch("builtins.open", m), \
             patch("enhanced_logging.Path") as mp:
            mp.return_value.exists.return_value = True
            result = al.get_audit_logs(user_id="alice")
        assert all(e["user_id"] == "alice" for e in result)

    def test_filters_by_category(self):
        al = AuditLogger()
        entries = "\n".join([
            _make_log_entry(category="user_action"),
            _make_log_entry(category="command_execution"),
        ])
        m = mock_open(read_data=entries)
        with patch("builtins.open", m), \
             patch("enhanced_logging.Path") as mp:
            mp.return_value.exists.return_value = True
            result = al.get_audit_logs(category="user_action")
        assert all(e["metadata"]["category"] == "user_action" for e in result)

    def test_filters_by_days(self):
        al = AuditLogger()
        recent = _make_log_entry(age_days=1)
        old = _make_log_entry(age_days=10)
        m = mock_open(read_data="\n".join([recent, old]))
        with patch("builtins.open", m), \
             patch("enhanced_logging.Path") as mp:
            mp.return_value.exists.return_value = True
            result = al.get_audit_logs(days=7)
        assert len(result) == 1

    def test_skips_invalid_json_lines(self):
        al = AuditLogger()
        entries = "not valid json\n" + _make_log_entry("u1")
        m = mock_open(read_data=entries)
        with patch("builtins.open", m), \
             patch("enhanced_logging.Path") as mp:
            mp.return_value.exists.return_value = True
            result = al.get_audit_logs()
        assert len(result) == 1

    def test_handles_file_read_exception(self):
        al = AuditLogger()
        with patch("enhanced_logging.Path") as mp:
            mp.return_value.exists.return_value = True
            with patch("builtins.open", side_effect=OSError("disk error")):
                result = al.get_audit_logs()
        assert result == []


# ---------------------------------------------------------------------------
# get_audit_logger (singleton)
# ---------------------------------------------------------------------------


class TestGetAuditLogger:
    def setup_method(self):
        # Reset global singleton before each test
        mod._audit_logger = None

    def test_returns_audit_logger_instance(self):
        result = get_audit_logger()
        assert isinstance(result, AuditLogger)

    def test_returns_same_instance_on_repeat_calls(self):
        first = get_audit_logger()
        second = get_audit_logger()
        assert first is second

    def test_creates_new_after_reset(self):
        first = get_audit_logger()
        mod._audit_logger = None
        second = get_audit_logger()
        assert first is not second
