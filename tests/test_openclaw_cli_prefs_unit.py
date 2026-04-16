"""Unit tests for openclaw_cli_prefs.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import openclaw_cli_prefs as mod

# ---------------------------------------------------------------------------
# _normalize_theme_name
# ---------------------------------------------------------------------------

def test_normalize_theme_name_valid_themes():
    for name in ("default", "green", "yellow", "magenta", "cyan", "mono"):
        assert mod._normalize_theme_name(name) == name


def test_normalize_theme_name_aliases():
    assert mod._normalize_theme_name("blue") == "default"
    assert mod._normalize_theme_name("classic") == "default"
    assert mod._normalize_theme_name("amber") == "yellow"
    assert mod._normalize_theme_name("purple") == "magenta"
    assert mod._normalize_theme_name("teal") == "cyan"
    assert mod._normalize_theme_name("gray") == "mono"
    assert mod._normalize_theme_name("grey") == "mono"


def test_normalize_theme_name_invalid_defaults_to_default():
    assert mod._normalize_theme_name("neon-pink") == "default"
    assert mod._normalize_theme_name("") == "default"
    assert mod._normalize_theme_name(None) == "default"


def test_normalize_theme_name_case_insensitive():
    assert mod._normalize_theme_name("GREEN") == "green"
    assert mod._normalize_theme_name("CYAN") == "cyan"


# ---------------------------------------------------------------------------
# _prefs_dir_path
# ---------------------------------------------------------------------------

def test_prefs_dir_path_uses_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", "/custom/home")
    path = mod._prefs_dir_path()
    assert str(path) == "/custom/home/.openclaw"


def test_prefs_dir_path_default_is_home_dotopenclaw(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENCLAW_CLI_HOME", raising=False)
    path = mod._prefs_dir_path()
    assert path == Path.home() / ".openclaw"


# ---------------------------------------------------------------------------
# _prefs_file_path
# ---------------------------------------------------------------------------

def test_prefs_file_path_ends_with_prefs_json(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENCLAW_CLI_HOME", raising=False)
    path = mod._prefs_file_path()
    assert path.name == "prefs.json"


def test_prefs_file_path_respects_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", "/override/path")
    path = mod._prefs_file_path()
    assert str(path) == "/override/path/.openclaw/prefs.json"


# ---------------------------------------------------------------------------
# _emoji_pack_name
# ---------------------------------------------------------------------------

def test_emoji_pack_name_returns_classic_by_default():
    with patch.dict(mod._PREFS, {"emoji_pack": "classic", "emoji": True}):
        assert mod._emoji_pack_name() == "classic"


def test_emoji_pack_name_returns_ascii():
    with patch.dict(mod._PREFS, {"emoji_pack": "ascii"}):
        assert mod._emoji_pack_name() == "ascii"


def test_emoji_pack_name_falls_back_on_emoji_bool():
    # If emoji_pack is an unknown value, fall back to emoji bool
    with patch.dict(mod._PREFS, {"emoji_pack": "unknown", "emoji": True}):
        assert mod._emoji_pack_name() == "classic"

    with patch.dict(mod._PREFS, {"emoji_pack": "unknown", "emoji": False}):
        assert mod._emoji_pack_name() == "ascii"


# ---------------------------------------------------------------------------
# _normalize_personalization_prefs
# ---------------------------------------------------------------------------

def test_normalize_personalization_clamps_layout():
    with patch.dict(mod._PREFS, {"layout": "super-verbose"}):
        mod._normalize_personalization_prefs()
        assert mod._PREFS["layout"] == "normal"


def test_normalize_personalization_valid_layout_preserved():
    for mode in ("compact", "normal", "verbose", "plain"):
        with patch.dict(mod._PREFS, {"layout": mode}):
            mod._normalize_personalization_prefs()
            assert mod._PREFS["layout"] == mode


def test_normalize_personalization_clamps_layout_preset():
    with patch.dict(mod._PREFS, {"layout_preset": "invalid-preset"}):
        mod._normalize_personalization_prefs()
        assert mod._PREFS["layout_preset"] == ""


def test_normalize_personalization_layout_preset_aliases():
    with patch.dict(mod._PREFS, {"layout_preset": "watch"}):
        mod._normalize_personalization_prefs()
        assert mod._PREFS["layout_preset"] == "watch-monitor"

    with patch.dict(mod._PREFS, {"layout_preset": "collab"}):
        mod._normalize_personalization_prefs()
        assert mod._PREFS["layout_preset"] == "handoff"


def test_normalize_personalization_clamps_layout_focus():
    with patch.dict(mod._PREFS, {"layout_focus": "bad-value"}):
        mod._normalize_personalization_prefs()
        assert mod._PREFS["layout_focus"] == "primary"


def test_normalize_personalization_valid_focus_preserved():
    for focus in ("primary", "supporting"):
        with patch.dict(mod._PREFS, {"layout_focus": focus}):
            mod._normalize_personalization_prefs()
            assert mod._PREFS["layout_focus"] == focus


def test_normalize_personalization_theme():
    with patch.dict(mod._PREFS, {"theme": "neon"}):
        mod._normalize_personalization_prefs()
        assert mod._PREFS["theme"] == "default"


# ---------------------------------------------------------------------------
# _load_prefs
# ---------------------------------------------------------------------------

def test_load_prefs_silently_ignores_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
    # No prefs.json in the temp dir — should not raise
    mod._load_prefs()


def test_load_prefs_reads_valid_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
    openclaw_dir = tmp_path / ".openclaw"
    openclaw_dir.mkdir(parents=True)
    prefs_file = openclaw_dir / "prefs.json"
    prefs_file.write_text(json.dumps({"theme": "green"}), "utf-8")
    with patch.dict(mod._PREFS, {}):
        mod._load_prefs()
        assert mod._PREFS.get("theme") == "green"


def test_load_prefs_ignores_invalid_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
    openclaw_dir = tmp_path / ".openclaw"
    openclaw_dir.mkdir(parents=True)
    prefs_file = openclaw_dir / "prefs.json"
    prefs_file.write_text("not valid json", "utf-8")
    # Should not raise
    mod._load_prefs()


# ---------------------------------------------------------------------------
# _save_prefs
# ---------------------------------------------------------------------------

def test_save_prefs_creates_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
    mod._save_prefs()
    prefs_file = tmp_path / ".openclaw" / "prefs.json"
    assert prefs_file.exists()


def test_save_prefs_writes_valid_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
    with patch.dict(mod._PREFS, {"theme": "cyan"}):
        mod._save_prefs()
    prefs_file = tmp_path / ".openclaw" / "prefs.json"
    data = json.loads(prefs_file.read_text("utf-8"))
    assert data["theme"] == "cyan"


# ---------------------------------------------------------------------------
# _prefs_set
# ---------------------------------------------------------------------------

def test_prefs_set_updates_in_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
    with patch.dict(mod._PREFS, {}):
        mod._prefs_set("my_key", "my_value")
        assert mod._PREFS["my_key"] == "my_value"


def test_prefs_set_persists_to_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
    with patch.dict(mod._PREFS, {}):
        mod._prefs_set("saved_key", 42)
    prefs_file = tmp_path / ".openclaw" / "prefs.json"
    assert prefs_file.exists()


# ---------------------------------------------------------------------------
# Constants / data structures
# ---------------------------------------------------------------------------

def test_themes_dict_has_expected_entries():
    assert "default" in mod._THEMES
    assert "green" in mod._THEMES
    assert "mono" in mod._THEMES


def test_emoji_packs_has_expected_keys():
    assert "classic" in mod._EMOJI_PACKS
    assert "minimal" in mod._EMOJI_PACKS
    assert "ascii" in mod._EMOJI_PACKS


def test_prefs_defaults_include_expected_keys():
    for key in ("theme", "emoji", "layout", "layout_preset", "layout_focus"):
        assert key in mod._PREFS


def test_tips_list_nonempty_strings():
    assert len(mod._OPENCLAW_TIPS) > 0
    assert all(isinstance(t, str) and t.strip() for t in mod._OPENCLAW_TIPS)
