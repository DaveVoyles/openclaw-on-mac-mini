"""Tests for per-user routing profile storage and override behavior."""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so memory_preferences imports cleanly without the full env
# ---------------------------------------------------------------------------

_stub_cfg = MagicMock()
_stub_cfg.routing_profile = "balanced"

_stub_config = types.ModuleType("config")
_stub_config.cfg = _stub_cfg  # type: ignore[attr-defined]
sys.modules.setdefault("config", _stub_config)

_stub_mrp = types.ModuleType("model_routing_policy")
_stub_mrp.VALID_ROUTING_PROFILES = {"copilot-first", "balanced", "gemini-first", "cost-saver"}  # type: ignore[attr-defined]

def _normalize_routing_profile(p: str) -> str:
    p = p.strip().lower().replace("_", "-")
    valid = {"copilot-first", "balanced", "gemini-first", "cost-saver"}
    return p if p in valid else "copilot-first"

_stub_mrp.normalize_routing_profile = _normalize_routing_profile  # type: ignore[attr-defined]
sys.modules.setdefault("model_routing_policy", _stub_mrp)

# Stub model_input helpers used by memory_preferences
_stub_helpers = types.ModuleType("model_input_helpers")
_stub_helpers.normalize_model_input = lambda x: x  # type: ignore[attr-defined]
_stub_helpers.model_input_suggestion = lambda x: ""  # type: ignore[attr-defined]
sys.modules.setdefault("model_input_helpers", _stub_helpers)


# ---------------------------------------------------------------------------
# Import the module under test using a temp directory for prefs storage
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_prefs_dir(tmp_path):
    """Patch _PREFS_DIR to a temp directory so tests don't pollute real prefs."""
    import pathlib

    import memory_preferences as mp
    original = mp._PREFS_DIR
    mp._PREFS_DIR = pathlib.Path(tmp_path / "prefs")
    mp._PREFS_DIR.mkdir(parents=True, exist_ok=True)
    yield
    mp._PREFS_DIR = original


import memory_preferences as mp


class TestGetRoutingProfile:
    def test_returns_empty_string_when_no_prefs_file(self):
        assert mp.get_routing_profile(99999) == ""

    def test_returns_empty_string_when_key_not_set(self):
        prefs = {"model_preference": "auto"}
        path = mp._prefs_path(42)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(prefs, f)
        assert mp.get_routing_profile(42) == ""

    def test_per_user_routing_returns_stored_profile(self):
        prefs = {"routing_profile": "gemini-first"}
        path = mp._prefs_path(7)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(prefs, f)
        assert mp.get_routing_profile(7) == "gemini-first"


class TestSetRoutingProfile:
    def test_valid_profile_is_persisted(self):
        result = mp.set_routing_profile(1, "copilot-first")
        assert "✅" in result
        assert mp.get_routing_profile(1) == "copilot-first"

    def test_all_valid_profiles_accepted(self):
        for profile in ["copilot-first", "balanced", "gemini-first", "cost-saver"]:
            result = mp.set_routing_profile(2, profile)
            assert "✅" in result, f"Expected success for profile: {profile}"
            assert mp.get_routing_profile(2) == profile

    def test_invalid_profile_rejected(self):
        result = mp.set_routing_profile(3, "turbo-mode")
        assert "❌" in result
        assert mp.get_routing_profile(3) == ""

    def test_profile_does_not_overwrite_model_preference(self):
        mp.set_model_preference(4, "gemini")
        mp.set_routing_profile(4, "cost-saver")
        assert mp.get_model_preference(4) == "gemini"
        assert mp.get_routing_profile(4) == "cost-saver"

    def test_model_preference_does_not_overwrite_profile(self):
        mp.set_routing_profile(5, "balanced")
        mp.set_model_preference(5, "copilot")
        assert mp.get_routing_profile(5) == "balanced"
        assert mp.get_model_preference(5) == "copilot"


class TestProfileOverridesSystemDefault:
    """Profile stored per-user should override the config-level system default."""

    def test_user_profile_returned_not_system_default(self):
        # System config says "balanced"
        _stub_cfg.routing_profile = "balanced"
        mp.set_routing_profile(10, "cost-saver")
        # The per-user getter should return the user's override
        assert mp.get_routing_profile(10) == "cost-saver"

    def test_empty_profile_means_system_default_applies(self):
        # No per-user override means caller should use system default
        assert mp.get_routing_profile(11) == ""
