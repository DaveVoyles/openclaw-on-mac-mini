"""Unit tests for AUDIT_ROTATE_INTERVAL env-var wiring in src/llm/telemetry.py."""

import asyncio
import importlib
import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stub google.genai before any llm imports
# ---------------------------------------------------------------------------
_genai_mock = MagicMock()
_genai_mock.types.ThinkingConfig = MagicMock()
_genai_mock.types.ContentDict = dict
_genai_mock.types.GenerateContentConfig = MagicMock()
_genai_mock.types.Tool = MagicMock()
_genai_mock.types.FunctionDeclaration = MagicMock()
_genai_mock.types.Schema = MagicMock()
_genai_mock.types.Type = MagicMock()
_genai_mock.types.Part = MagicMock()
_genai_mock.types.FunctionResponse = MagicMock()
_genai_mock.types.Content = MagicMock()
_genai_mock.types.Blob = MagicMock()
_genai_mock.Client = MagicMock()
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.genai", _genai_mock)
sys.modules.setdefault("google.genai.types", _genai_mock.types)

import llm.telemetry as telemetry_mod  # noqa: E402

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_default_interval_is_3600(monkeypatch):
    """_AUDIT_ROTATE_INTERVAL defaults to 3600 when env var is unset."""
    monkeypatch.delenv("AUDIT_ROTATE_INTERVAL", raising=False)
    importlib.reload(telemetry_mod)
    assert telemetry_mod._AUDIT_ROTATE_INTERVAL == 3600


def test_env_var_overrides_interval(monkeypatch):
    """AUDIT_ROTATE_INTERVAL env var is picked up on (re)import."""
    monkeypatch.setenv("AUDIT_ROTATE_INTERVAL", "120")
    importlib.reload(telemetry_mod)
    assert telemetry_mod._AUDIT_ROTATE_INTERVAL == 120
    # restore default
    monkeypatch.delenv("AUDIT_ROTATE_INTERVAL", raising=False)
    importlib.reload(telemetry_mod)


def test_rotate_audit_log_trims_when_over_limit(tmp_path, monkeypatch):
    """rotate_audit_log trims file to _AUDIT_KEEP_LINES when over _AUDIT_MAX_LINES."""
    importlib.reload(telemetry_mod)
    max_lines = telemetry_mod._AUDIT_MAX_LINES
    keep_lines = telemetry_mod._AUDIT_KEEP_LINES

    audit_file = tmp_path / "routing_audit.jsonl"
    total_lines = max_lines + 10
    audit_file.write_text("\n".join(f'{{"i":{i}}}' for i in range(total_lines)) + "\n")

    monkeypatch.setattr(telemetry_mod, "_LOG_PATH", audit_file)

    asyncio.run(telemetry_mod.rotate_audit_log())

    result = audit_file.read_text().splitlines()
    assert len(result) == keep_lines


def test_rotate_audit_log_noop_when_under_limit(tmp_path, monkeypatch):
    """rotate_audit_log leaves file untouched when line count is below _AUDIT_MAX_LINES."""
    importlib.reload(telemetry_mod)

    audit_file = tmp_path / "routing_audit.jsonl"
    lines = [f'{{"i":{i}}}' for i in range(5)]
    audit_file.write_text("\n".join(lines) + "\n")

    monkeypatch.setattr(telemetry_mod, "_LOG_PATH", audit_file)

    asyncio.run(telemetry_mod.rotate_audit_log())

    result = audit_file.read_text().splitlines()
    assert len(result) == 5


def test_rotate_audit_log_noop_when_file_missing(tmp_path, monkeypatch):
    """rotate_audit_log raises no exception when the audit file does not exist."""
    importlib.reload(telemetry_mod)

    missing = tmp_path / "nonexistent_audit.jsonl"
    monkeypatch.setattr(telemetry_mod, "_LOG_PATH", missing)

    # Should complete without raising
    asyncio.run(telemetry_mod.rotate_audit_log())
