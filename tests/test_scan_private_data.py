"""Unit tests for scripts/scan_private_data.py — the public-repo private-data scanner.

Verifies the scanner detects real personal data / secrets, ignores placeholders
and test fixtures, and exits 0 on the current (clean) tree.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "scan_private_data.py"


def _import():
    """Import scan_private_data as a module via file path."""
    spec = importlib.util.spec_from_file_location("scan_private_data", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Owner email assembled from parts so the literal address is not stored verbatim
# in this public repo (still exercises real detection end-to-end).
_OWNER_EMAIL = "dnvoyles" + "@" + "gmail.com"

# Real-looking secrets that contain NO placeholder markers, so they must be flagged.
REAL_LEAKS = [
    _OWNER_EMAIL,
    "https://join.slack.com/t/dvopenclaw/shared_invite/zt-3rstuv-realtoken99",
    "xoxb-9988776655-" + "B" * 20,
    "ghp_" + "B" * 36,
    "sk-" + "B" * 30,
    "sk-ant-" + "B" * 30,
    "AKIA" + "B" * 16,
    "AIza" + "B" * 35,
    "-----BEGIN OPENSSH PRIVATE KEY-----",
]

# Strings that must NOT be flagged (placeholders, fixtures, acceptable data).
SAFE_STRINGS = [
    "you@example.com",
    "xoxb-YOUR-BOT-TOKEN",
    "ghp_xxxxxxxxxxxxxxxxxxxx",
    "xoxb-1234567890-abcdefghij",  # tests/test_host_bridge.py fixture
    "ghp_AbCdEf012345abcdef01234567890ABCDEF",  # fixture
    "AIzaSyA-fake-google-api-key_AbCdEf12345",  # fixture
    'placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"',
    "192.168.1.93",  # private LAN IP — acceptable, not scanned
    "/Users/davevoyles/docker-stack",  # personal path — acceptable, not scanned
    "var order=['task-status-card','run-history-card'];",  # 'sk-' substring, not a token
    'div class="task-description"',
]


class TestScanLine:
    @pytest.mark.parametrize("leak", REAL_LEAKS)
    def test_detects_real_leak(self, leak):
        mod = _import()
        findings = mod.scan_line(leak)
        assert findings, f"expected a finding for: {leak!r}"

    @pytest.mark.parametrize("safe", SAFE_STRINGS)
    def test_ignores_safe_strings(self, safe):
        mod = _import()
        findings = mod.scan_line(safe)
        assert not findings, f"unexpected finding for safe string: {safe!r} -> {findings}"


class TestIsPlaceholder:
    def test_markers_detected(self):
        mod = _import()
        assert mod.is_placeholder("xoxb-YOUR-BOT-TOKEN")
        assert mod.is_placeholder("ghp_xxxxxxxxxxxxxxxxxxxx")
        assert mod.is_placeholder("AIzaSyA-fake-key")

    def test_real_token_not_placeholder(self):
        mod = _import()
        assert not mod.is_placeholder("ghp_" + "B" * 36)


class TestScanFile:
    def test_flags_file_with_leak(self, tmp_path):
        mod = _import()
        f = tmp_path / "leak.txt"
        f.write_text(f"line one\nemail {_OWNER_EMAIL} here\nline three\n")
        results = mod.scan_file(tmp_path, "leak.txt")
        assert len(results) == 1
        lineno, name, _desc, matched = results[0]
        assert lineno == 2
        assert name == "personal-email"
        assert matched == _OWNER_EMAIL

    def test_clean_file_no_findings(self, tmp_path):
        mod = _import()
        f = tmp_path / "clean.txt"
        f.write_text("nothing to see at you@example.com\n192.168.1.1\n")
        assert mod.scan_file(tmp_path, "clean.txt") == []

    def test_binary_file_skipped(self, tmp_path):
        mod = _import()
        f = tmp_path / "blob.bin"
        f.write_bytes(b"\x00\x01\x02\xff " + _OWNER_EMAIL.encode() + b" \x00")
        # Unreadable as utf-8 -> skipped, no crash
        assert mod.scan_file(tmp_path, "blob.bin") == []


class TestEndToEnd:
    def test_clean_tree_exits_zero(self):
        """The real repo tree must be free of private data (scrubbed pre-publish)."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "No private/personal data" in result.stdout

    def test_allowlist_contains_self_and_policy(self):
        mod = _import()
        assert "scripts/scan_private_data.py" in mod.ALLOWLISTED_FILES
        assert ".github/docs/README.md" in mod.ALLOWLISTED_FILES
        assert "tests/test_scan_private_data.py" in mod.ALLOWLISTED_FILES
