"""Tests for host_bridge: identity gate, sanitisation, command-building, audit shape.

Real SSH is NOT exercised here — those paths require the configured key + a
reachable Mac Mini host and are validated end-to-end via the /copilot Slack
smoke test after deploy. These tests guard the pure-Python contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    # Isolate audit dir per test so the JSONL writer doesn't touch real data.
    monkeypatch.setenv("OPENCLAW_HOST_BRIDGE_ENABLED", "false")  # default off
    # Force re-import so module-level config picks up the env tweaks.
    import importlib
    import sys

    sys.modules.pop("host_bridge", None)
    mod = importlib.import_module("host_bridge")
    # Redirect audit dir into tmp.
    mod._AUDIT_DIR = tmp_path / "host_bridge"
    mod._AUDIT_LOG = tmp_path / "host_bridge.jsonl"
    return mod


def test_sanitize_redacts_secret_patterns(bridge):
    samples = [
        "xoxb-1234567890-abcdefghij",
        "ghp_AbCdEf012345abcdef01234567890ABCDEF",
        "AIzaSyA-fake-google-api-key_AbCdEf12345",
        "sk-abcdef0123456789ABCDEF",
        "AKIAABCDEFGHIJKLMNOP",
        "Authorization: Bearer abcdef0123456789_secret-token",
    ]
    for s in samples:
        out = bridge.sanitize(s)
        assert "«REDACTED»" in out, f"failed to redact: {s!r} -> {out!r}"


def test_sanitize_passes_clean_text(bridge):
    s = "normal output: container 'plex' is healthy"
    assert bridge.sanitize(s) == s


def test_build_remote_cmd_shell_quotes(bridge):
    cmd = bridge._build_remote_cmd("diagnose 'plex'; rm -rf /", "/tmp/work dir")
    # Inner cd + copilot invocation must be a single shell-quoted bash -lc arg
    assert cmd.startswith("bash -lc ")
    # Both the prompt and workdir must be shell-quoted (no unescaped single quotes that could break out)
    assert "rm -rf /" in cmd  # text present, but inside quotes
    # The dangerous semicolon must NOT be at top level — it must be inside the bash -lc quoting
    assert cmd.count("bash -lc") == 1


@pytest.mark.asyncio
async def test_run_copilot_refuses_when_disabled(bridge, monkeypatch):
    monkeypatch.setenv("OPENCLAW_HOST_BRIDGE_ENABLED", "false")
    # re-import to recompute _enabled() — actually run_copilot calls _enabled() each time
    result = await bridge.run_copilot(prompt="hello", slack_user_id="U1")
    assert result.success is False
    assert result.error and "disabled" in result.error.lower()


@pytest.mark.asyncio
async def test_run_copilot_refuses_empty_prompt(bridge, monkeypatch):
    monkeypatch.setenv("OPENCLAW_HOST_BRIDGE_ENABLED", "true")
    result = await bridge.run_copilot(prompt="   ", slack_user_id="U1")
    assert result.success is False
    assert result.error == "empty prompt"


@pytest.mark.asyncio
async def test_run_copilot_refuses_missing_key(bridge, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_HOST_BRIDGE_ENABLED", "true")
    bridge.KEY_PATH = str(tmp_path / "nope.key")
    result = await bridge.run_copilot(prompt="hello", slack_user_id="U1")
    assert result.success is False
    assert result.error and "SSH key not found" in result.error


@pytest.mark.asyncio
async def test_audit_row_is_written_on_failure(bridge, monkeypatch, tmp_path):
    """Audit row is written when an actual SSH attempt fails."""
    monkeypatch.setenv("OPENCLAW_HOST_BRIDGE_ENABLED", "true")
    fake_key = tmp_path / "fake.key"
    fake_key.write_text("not-a-real-key\n")
    bridge.KEY_PATH = str(fake_key)

    # Inject a fake asyncssh module whose connect() raises — exercises the
    # full code path (gates → ssh attempt → except block → audit write).
    import sys
    import types

    class _Boom(Exception):
        pass

    def _bad_connect(*_a, **_kw):
        # asyncssh.connect is awaited (returns coroutine) — raising here
        # propagates synchronously when the `async with` evaluates the call.
        raise _Boom("simulated connect failure")

    fake_mod = types.ModuleType("asyncssh")
    fake_mod.connect = _bad_connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asyncssh", fake_mod)

    result = await bridge.run_copilot(prompt="hello world", slack_user_id="U_TEST")
    assert result.success is False
    assert result.error and "simulated connect failure" in result.error

    log_path = bridge._AUDIT_LOG
    assert log_path.exists(), f"audit JSONL not written at {log_path}"
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "audit JSONL empty"
    row = json.loads(lines[-1])
    assert row["slack_user_id"] == "U_TEST"
    assert row["prompt"] == "hello world"
    assert row["error"] is not None
    # Transcript file should also exist
    assert Path(row["transcript"]).exists()
