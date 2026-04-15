"""Unit tests for openclaw_cli_watch.py — pure/utility functions."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import openclaw_cli_watch as mod  # type: ignore


# ---------------------------------------------------------------------------
# utc_timestamp
# ---------------------------------------------------------------------------


def test_utc_timestamp_returns_iso_string():
    ts = mod.utc_timestamp()
    assert ts.endswith("Z")
    assert "T" in ts
    # Must be parseable
    datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# _parse_utc_timestamp
# ---------------------------------------------------------------------------


def test_parse_utc_timestamp_valid():
    dt = mod._parse_utc_timestamp("2024-01-15T12:00:00Z")
    assert dt is not None
    assert dt.year == 2024
    assert dt.month == 1


def test_parse_utc_timestamp_empty():
    assert mod._parse_utc_timestamp("") is None
    assert mod._parse_utc_timestamp(None) is None


def test_parse_utc_timestamp_invalid():
    assert mod._parse_utc_timestamp("not-a-date") is None


# ---------------------------------------------------------------------------
# _elapsed_seconds
# ---------------------------------------------------------------------------


def test_elapsed_seconds_positive():
    start = "2024-01-01T00:00:00Z"
    end = "2024-01-01T00:01:00Z"
    elapsed = mod._elapsed_seconds(start, end)
    assert elapsed == pytest.approx(60.0)


def test_elapsed_seconds_same_timestamps():
    ts = "2024-06-01T10:00:00Z"
    elapsed = mod._elapsed_seconds(ts, ts)
    assert elapsed == pytest.approx(0.0)


def test_elapsed_seconds_none_for_invalid_start():
    assert mod._elapsed_seconds("bad-ts", "2024-01-01T00:00:00Z") is None


def test_elapsed_seconds_uses_now_when_finished_is_none():
    start = "2020-01-01T00:00:00Z"
    elapsed = mod._elapsed_seconds(start)
    assert elapsed is not None
    assert elapsed > 0


# ---------------------------------------------------------------------------
# _format_elapsed_compact
# ---------------------------------------------------------------------------


def test_format_elapsed_compact_sub_second():
    assert mod._format_elapsed_compact(0.5) == "0.5s"


def test_format_elapsed_compact_seconds():
    assert mod._format_elapsed_compact(5.3) == "5.3s"
    assert mod._format_elapsed_compact(45) == "45s"


def test_format_elapsed_compact_minutes():
    assert mod._format_elapsed_compact(90) == "1m 30s"
    assert mod._format_elapsed_compact(120) == "2m"


def test_format_elapsed_compact_hours():
    assert mod._format_elapsed_compact(3600) == "1h"
    assert mod._format_elapsed_compact(3661) == "1h 1m"


def test_format_elapsed_compact_invalid():
    assert mod._format_elapsed_compact("bad") == "0s"
    assert mod._format_elapsed_compact(None) == "0s"


# ---------------------------------------------------------------------------
# _single_line_excerpt
# ---------------------------------------------------------------------------


def test_single_line_excerpt_within_limit():
    assert mod._single_line_excerpt("hello world", max_chars=50) == "hello world"


def test_single_line_excerpt_truncates():
    text = "a" * 100
    result = mod._single_line_excerpt(text, max_chars=20)
    assert result.endswith("…")
    assert len(result) <= 20


def test_single_line_excerpt_collapses_whitespace():
    assert mod._single_line_excerpt("foo\n  bar  \nbaz", max_chars=100) == "foo bar baz"


# ---------------------------------------------------------------------------
# _dedupe_preserve_order
# ---------------------------------------------------------------------------


def test_dedupe_preserve_order_basic():
    result = mod._dedupe_preserve_order(["a", "b", "a", "c"])
    assert result == ["a", "b", "c"]


def test_dedupe_preserve_order_filters_empty():
    result = mod._dedupe_preserve_order(["x", "", "  ", "y"])
    assert result == ["x", "y"]


def test_dedupe_preserve_order_empty_list():
    assert mod._dedupe_preserve_order([]) == []


# ---------------------------------------------------------------------------
# _status_family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,expected", [
    ("done", "complete"),
    ("completed", "complete"),
    ("success", "complete"),
    ("running", "active"),
    ("in_progress", "active"),
    ("pending", "waiting"),
    ("queued", "waiting"),
    ("idle", "idle"),
    ("error", "error"),
    ("failed", "error"),
    ("warn", "warn"),
    ("blocked", "blocked"),
    ("paused", "paused"),
    ("cancelled", "paused"),
    ("unknown_xyz", "unknown"),
])
def test_status_family_classifications(status: str, expected: str):
    assert mod._status_family(status) == expected


# ---------------------------------------------------------------------------
# normalize_watch_state
# ---------------------------------------------------------------------------


def test_normalize_watch_state_backfills_defaults():
    state = normalize_state = mod.normalize_watch_state({})
    assert "last_error" in state
    assert state["failure_count"] == 0
    assert state["consecutive_failures"] == 0
    assert "retry_limit" in state
    assert isinstance(state["retry_history"], list)
    assert isinstance(state["progress_log"], list)
    assert isinstance(state["interventions"], list)
    assert state["force_run_once"] is False
    assert state["stop_requested"] is False


def test_normalize_watch_state_filters_non_dict_entries():
    state = {
        "retry_history": [{"ok": True}, "bad", 42],
        "progress_log": ["nope", {"fine": True}],
        "interventions": [None, {"x": 1}],
    }
    result = mod.normalize_watch_state(state)
    assert all(isinstance(i, dict) for i in result["retry_history"])
    assert all(isinstance(i, dict) for i in result["progress_log"])
    assert all(isinstance(i, dict) for i in result["interventions"])


def test_normalize_watch_state_on_none():
    result = mod.normalize_watch_state(None)
    assert isinstance(result, dict)
    assert result["failure_count"] == 0


# ---------------------------------------------------------------------------
# watch_retry_delay_seconds
# ---------------------------------------------------------------------------


def test_watch_retry_delay_seconds_attempt_1():
    assert mod.watch_retry_delay_seconds(1) == 1


def test_watch_retry_delay_seconds_exponential():
    assert mod.watch_retry_delay_seconds(2) == 2
    assert mod.watch_retry_delay_seconds(3) == 4


def test_watch_retry_delay_seconds_capped():
    # Should not exceed WATCH_RETRY_MAX_DELAY_SECONDS (8)
    for attempt in range(1, 15):
        assert mod.watch_retry_delay_seconds(attempt) <= mod.WATCH_RETRY_MAX_DELAY_SECONDS


# ---------------------------------------------------------------------------
# is_transient_watch_error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("msg", [
    "timed out waiting for server",
    "connection refused on port 80",
    "http 429 rate limit",
    "http 503 service unavailable",
    "network is unreachable",
    "temporary failure in name resolution",
])
def test_is_transient_watch_error_matches(msg: str):
    assert mod.is_transient_watch_error(msg) is True


def test_is_transient_watch_error_no_match():
    assert mod.is_transient_watch_error("syntax error in file.py") is False
    assert mod.is_transient_watch_error("") is False
    assert mod.is_transient_watch_error(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_watch_state
# ---------------------------------------------------------------------------


def test_build_watch_state_contains_required_keys():
    from openclaw_cli_sessions import SessionSummary  # type: ignore

    session = SessionSummary(session_id="s1", title="T", cwd="/cwd")
    state = mod.build_watch_state(
        session=session,
        mode="interval",
        goal="check status",
        interval_seconds=60,
        max_polls=10,
        on_change=False,
    )
    for key in ("session_id", "mode", "goal", "status", "poll_count", "interval_seconds"):
        assert key in state
    assert state["session_id"] == "s1"
    assert state["mode"] == "interval"
    assert state["poll_count"] == 0
    assert state["status"] == "idle"


# ---------------------------------------------------------------------------
# start_watch_checkpoint
# ---------------------------------------------------------------------------


def test_start_watch_checkpoint_initial_state():
    cp = mod.start_watch_checkpoint(iteration=3, mode="interval")
    assert cp["poll"] == 3
    assert cp["mode"] == "interval"
    assert cp["status"] == "running"
    assert "started_at" in cp
