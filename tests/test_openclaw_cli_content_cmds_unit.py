"""Unit tests for openclaw_cli_content_cmds helpers."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openclaw_cli_content_cmds import (
    _build_ascii_bar_rows,
    _build_export_body,
    _build_session_stats_agg,
    _compute_cmd_freq,
    _compute_rating_freq,
)


# ---------------------------------------------------------------------------
# _build_export_body — md
# ---------------------------------------------------------------------------

def test_export_body_md_header():
    body = _build_export_body([], "md", "2024-01-01", "2024-01-01T00:00:00")
    assert "# OpenClaw Session Export" in body
    assert "2024-01-01" in body


def test_export_body_md_string_entry():
    body = _build_export_body(["hello world"], "md", "now", "iso")
    assert "hello world" in body
    assert "### [1]" in body


def test_export_body_md_dict_entry():
    entry = {"text": "git status", "timestamp": "12:00"}
    body = _build_export_body([entry], "md", "now", "iso")
    assert "git status" in body
    assert "_12:00_" in body


def test_export_body_json_structure():
    body = _build_export_body(["cmd1"], "json", "now", "2024-01-01T00:00:00")
    data = json.loads(body)
    assert data["entry_count"] == 1
    assert data["exported_at"] == "2024-01-01T00:00:00"
    assert data["history"] == ["cmd1"]


def test_export_body_txt_format():
    body = _build_export_body(["ls"], "txt", "now", "iso")
    assert "[1] ls" in body
    assert "=" * 60 in body


def test_export_body_txt_dict_entry():
    body = _build_export_body([{"cmd": "make test"}], "txt", "now", "iso")
    assert "make test" in body


# ---------------------------------------------------------------------------
# _compute_cmd_freq
# ---------------------------------------------------------------------------

def test_compute_cmd_freq_string_entries():
    freq = _compute_cmd_freq(["ls -la", "ls", "git status"])
    assert freq["ls"] == 2
    assert freq["git"] == 1


def test_compute_cmd_freq_dict_entries():
    freq = _compute_cmd_freq([{"cmd": "pytest tests/"}, {"command": "make"}])
    assert freq["pytest"] == 1
    assert freq["make"] == 1


def test_compute_cmd_freq_empty():
    assert _compute_cmd_freq([]) == {}


# ---------------------------------------------------------------------------
# _compute_rating_freq
# ---------------------------------------------------------------------------

def test_compute_rating_freq_numeric_strings():
    freq = _compute_rating_freq(["3", "5", "3"])
    assert freq["⭐⭐⭐"] == 2
    assert freq["⭐⭐⭐⭐⭐"] == 1


def test_compute_rating_freq_dict_entries():
    freq = _compute_rating_freq([{"score": "4"}, {"rating": "2"}])
    assert freq["⭐⭐⭐⭐"] == 1
    assert freq["⭐⭐"] == 1


def test_compute_rating_freq_non_digit():
    freq = _compute_rating_freq(["good", "bad"])
    assert freq["good"] == 1


# ---------------------------------------------------------------------------
# _build_ascii_bar_rows
# ---------------------------------------------------------------------------

def test_build_ascii_bar_rows_empty():
    assert _build_ascii_bar_rows({}) == []


def test_build_ascii_bar_rows_single_entry():
    rows = _build_ascii_bar_rows({"ls": 5})
    assert len(rows) == 1
    label, bar, count = rows[0]
    assert label == "ls"
    assert count == 5
    assert "█" in bar


def test_build_ascii_bar_rows_sorted_desc():
    data = {"a": 1, "b": 10, "c": 5}
    rows = _build_ascii_bar_rows(data)
    counts = [r[2] for r in rows]
    assert counts == sorted(counts, reverse=True)


def test_build_ascii_bar_rows_caps_at_10():
    data = {str(i): i for i in range(1, 20)}
    rows = _build_ascii_bar_rows(data)
    assert len(rows) <= 10


# ---------------------------------------------------------------------------
# _build_session_stats_agg
# ---------------------------------------------------------------------------

def _make_session(**kwargs):
    defaults = dict(
        command_count=0,
        file_edit_count=0,
        checkpoint_count=0,
        status="closed",
        updated_at="2024-06-01T12:00:00",
        created_at="2024-01-01T00:00:00",
        cwd="/home/user",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_session_stats_agg_empty():
    result = _build_session_stats_agg([])
    assert result["total_sessions"] == 0
    assert result["newest"] == "—"
    assert result["oldest"] == "—"


def test_session_stats_agg_totals():
    sessions = [_make_session(command_count=3, file_edit_count=2, checkpoint_count=1)]
    result = _build_session_stats_agg(sessions)
    assert result["total_sessions"] == 1
    assert result["total_commands"] == 3
    assert result["total_edits"] == 2
    assert result["total_checkpoints"] == 1


def test_session_stats_agg_active_count():
    sessions = [
        _make_session(status="active"),
        _make_session(status="closed"),
        _make_session(status="active"),
    ]
    result = _build_session_stats_agg(sessions)
    assert result["active"] == 2


def test_session_stats_agg_top_cwds():
    sessions = [
        _make_session(cwd="/a"),
        _make_session(cwd="/a"),
        _make_session(cwd="/b"),
    ]
    result = _build_session_stats_agg(sessions)
    top = dict(result["top_cwds"])
    assert top["/a"] == 2
