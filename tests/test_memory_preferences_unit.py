"""Unit tests for memory_preferences.py — user model and routing preferences."""

import json
from unittest.mock import MagicMock, patch

import memory_preferences as mp_module
from memory_preferences import (
    _VALID_MODEL_PREFS,
    _load_prefs,
    _prefs_path,
    _save_prefs,
    get_model_preference,
    get_routing_profile,
    set_model_preference,
    set_routing_profile,
)

# ---------------------------------------------------------------------------
# _prefs_path
# ---------------------------------------------------------------------------

class TestPrefsPath:
    def test_returns_json_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        path = _prefs_path(42)
        assert path.suffix == ".json"
        assert "42" in path.name

    def test_creates_prefs_dir(self, tmp_path, monkeypatch):
        prefs_dir = tmp_path / "new_prefs"
        monkeypatch.setattr(mp_module, "_PREFS_DIR", prefs_dir)
        _prefs_path(1)
        assert prefs_dir.exists()


# ---------------------------------------------------------------------------
# _load_prefs
# ---------------------------------------------------------------------------

class TestLoadPrefs:
    def test_returns_empty_dict_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        assert _load_prefs(999) == {}

    def test_loads_existing_prefs(self, tmp_path, monkeypatch):
        prefs_dir = tmp_path / "prefs"
        prefs_dir.mkdir(parents=True)
        monkeypatch.setattr(mp_module, "_PREFS_DIR", prefs_dir)
        payload = {"model_preference": "gemini"}
        (prefs_dir / "1.json").write_text(json.dumps(payload))
        result = _load_prefs(1)
        assert result["model_preference"] == "gemini"

    def test_returns_empty_dict_on_corrupt_file(self, tmp_path, monkeypatch):
        prefs_dir = tmp_path / "prefs"
        prefs_dir.mkdir(parents=True)
        monkeypatch.setattr(mp_module, "_PREFS_DIR", prefs_dir)
        (prefs_dir / "1.json").write_text("{bad json")
        assert _load_prefs(1) == {}


# ---------------------------------------------------------------------------
# _save_prefs
# ---------------------------------------------------------------------------

class TestSavePrefs:
    def test_save_delegates_to_atomic_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        with patch("memory_preferences._atomic_write") as mock_aw:
            _save_prefs(1, {"model_preference": "local"})
        mock_aw.assert_called_once()
        written = json.loads(mock_aw.call_args[0][1])
        assert written["model_preference"] == "local"


# ---------------------------------------------------------------------------
# get_model_preference
# ---------------------------------------------------------------------------

class TestGetModelPreference:
    def test_returns_default_when_no_pref_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        import config
        monkeypatch.setattr(config.cfg, "default_model_preference", "auto")
        result = get_model_preference(999)
        assert result == "auto"

    def test_returns_stored_preference(self, tmp_path, monkeypatch):
        prefs_dir = tmp_path / "prefs"
        prefs_dir.mkdir(parents=True)
        monkeypatch.setattr(mp_module, "_PREFS_DIR", prefs_dir)
        (prefs_dir / "1.json").write_text(json.dumps({"model_preference": "openai"}))
        result = get_model_preference(1)
        assert result == "openai"


# ---------------------------------------------------------------------------
# set_model_preference
# ---------------------------------------------------------------------------

class TestSetModelPreference:
    def test_valid_preference_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        with patch("memory_preferences.normalize_model_input", return_value="gemini"), \
             patch("memory_preferences._atomic_write"):
            result = set_model_preference(1, "gemini")
        assert "✅" in result

    def test_invalid_preference_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        with patch("memory_preferences.normalize_model_input", return_value="invalid_val"), \
             patch("memory_preferences.model_input_suggestion", return_value=""):
            result = set_model_preference(1, "invalid_val")
        assert "❌" in result

    def test_empty_input_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        with patch("memory_preferences.normalize_model_input", return_value=""), \
             patch("memory_preferences.model_input_suggestion", return_value=""):
            result = set_model_preference(1, "")
        assert "❌" in result

    def test_all_valid_prefs_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        for pref in _VALID_MODEL_PREFS:
            with patch("memory_preferences.normalize_model_input", return_value=pref), \
                 patch("memory_preferences._atomic_write"):
                result = set_model_preference(1, pref)
            assert "✅" in result, f"Expected ✅ for valid pref '{pref}', got: {result}"

    def test_suggestion_appended_on_invalid(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        with patch("memory_preferences.normalize_model_input", return_value="badval"), \
             patch("memory_preferences.model_input_suggestion", return_value="Did you mean gemini?"):
            result = set_model_preference(1, "badval")
        assert "Did you mean gemini?" in result


# ---------------------------------------------------------------------------
# get_routing_profile
# ---------------------------------------------------------------------------

class TestGetRoutingProfile:
    def test_returns_empty_string_when_none_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        assert get_routing_profile(999) == ""

    def test_memory_preferences_unit_returns_stored_profile(self, tmp_path, monkeypatch):
        prefs_dir = tmp_path / "prefs"
        prefs_dir.mkdir(parents=True)
        monkeypatch.setattr(mp_module, "_PREFS_DIR", prefs_dir)
        (prefs_dir / "1.json").write_text(json.dumps({"routing_profile": "balanced"}))
        assert get_routing_profile(1) == "balanced"


# ---------------------------------------------------------------------------
# set_routing_profile
# ---------------------------------------------------------------------------

class TestSetRoutingProfile:
    def _make_routing_mocks(self, valid_profiles=None, normalized="balanced"):
        routing_mock = MagicMock()
        routing_mock.VALID_ROUTING_PROFILES = valid_profiles or {
            "balanced", "copilot-first", "gemini-first", "cost-saver"
        }
        routing_mock.normalize_routing_profile = MagicMock(return_value=normalized)
        return routing_mock

    def test_invalid_profile_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        routing_mock = self._make_routing_mocks(normalized="balanced")
        with patch.dict("sys.modules", {"model_routing_policy": routing_mock}):
            result = set_routing_profile(1, "not-a-real-profile")
        assert "❌" in result

    def test_valid_profile_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        routing_mock = self._make_routing_mocks(normalized="balanced")
        with patch.dict("sys.modules", {"model_routing_policy": routing_mock}), \
             patch("memory_preferences._atomic_write"):
            result = set_routing_profile(1, "balanced")
        assert "✅" in result

    def test_empty_profile_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp_module, "_PREFS_DIR", tmp_path / "prefs")
        routing_mock = self._make_routing_mocks(normalized="balanced")
        with patch.dict("sys.modules", {"model_routing_policy": routing_mock}):
            result = set_routing_profile(1, "")
        assert "❌" in result
