"""Tests for model-aware context window limits.

Covers:
  - llm.context_limits.MODEL_CONTEXT_WINDOWS and get_model_context_window() prefix matching
  - _resolve_context_limit_profile() picks up MODEL_CONTEXT_WINDOWS for known models
  - _context_pressure_snapshot() reflects model-registry source
  - _emit_context_overflow_warning() fires at 80%+ threshold, once per band
"""
from __future__ import annotations

from unittest.mock import patch

# ---------------------------------------------------------------------------
# Fixtures — import target modules
# ---------------------------------------------------------------------------
import llm.context_limits as cl_mod
import openclaw_cli_session_display as sd

# ===========================================================================
# 1. MODEL_CONTEXT_WINDOWS — structure and prefix matching
# ===========================================================================

class TestModelContextWindows:
    def test_dict_has_expected_keys(self):
        assert "gpt-4o" in cl_mod.MODEL_CONTEXT_WINDOWS
        assert "gemini-2.0-flash" in cl_mod.MODEL_CONTEXT_WINDOWS
        assert "claude-3-5-sonnet" in cl_mod.MODEL_CONTEXT_WINDOWS
        assert "ollama" in cl_mod.MODEL_CONTEXT_WINDOWS

    def test_exact_match(self):
        assert cl_mod.get_model_context_window("gpt-4o") == 128_000

    def test_prefix_match_with_hyphen_suffix(self):
        # "gemini-2.0-flash-exp" should match "gemini-2.0-flash"
        assert cl_mod.get_model_context_window("gemini-2.0-flash-exp") == 1_048_576

    def test_prefix_match_with_colon_suffix(self):
        # "ollama:mistral" should match "ollama"
        assert cl_mod.get_model_context_window("ollama:mistral") == 8_192

    def test_case_insensitive(self):
        assert cl_mod.get_model_context_window("GPT-4O") == 128_000
        assert cl_mod.get_model_context_window("Gemini-1.5-Pro") == 2_097_152

    def test_no_partial_overlap_without_separator(self):
        # "gpt-40" is NOT a variant of "gpt-4o"
        result = cl_mod.get_model_context_window("gpt-40-custom")
        # This should NOT match "gpt-4o"
        assert result != 128_000 or result is None  # no false positive

    def test_unknown_model_returns_none(self):
        assert cl_mod.get_model_context_window("totally-unknown-model") is None

    def test_none_input_returns_none(self):
        assert cl_mod.get_model_context_window(None) is None

    def test_empty_string_returns_none(self):
        assert cl_mod.get_model_context_window("") is None

    def test_claude_3_5_sonnet_prefix(self):
        assert cl_mod.get_model_context_window("claude-3-5-sonnet-20241022") == 200_000

    def test_gpt_4_does_not_match_gpt_4o(self):
        # "gpt-4" is in the dict, "gpt-4o" is also in the dict — they should not cross-match
        assert cl_mod.get_model_context_window("gpt-4") == 8_192
        assert cl_mod.get_model_context_window("gpt-4o") == 128_000

    def test_gemini_1_5_flash_prefix(self):
        assert cl_mod.get_model_context_window("gemini-1.5-flash-8b") == 1_048_576


# ===========================================================================
# 2. _resolve_context_limit_profile — model-registry path
# ===========================================================================

class TestResolveContextLimitProfile:
    def test_known_model_uses_registry(self, monkeypatch):
        monkeypatch.setattr(sd, "_PREFS", {"last_model": "", "route_mode": ""})
        profile = sd._resolve_context_limit_profile(model_hint="gpt-4o")
        assert profile["source"] == "model-registry"
        assert int(profile["limit_tokens"]) == 128_000
        assert profile["model_aware"] is True
        assert profile["approximate"] is False

    def test_known_model_with_variant_suffix(self, monkeypatch):
        monkeypatch.setattr(sd, "_PREFS", {"last_model": "", "route_mode": ""})
        profile = sd._resolve_context_limit_profile(model_hint="gemini-2.0-flash-exp")
        assert profile["source"] == "model-registry"
        assert int(profile["limit_tokens"]) == 1_048_576

    def test_model_with_explicit_k_suffix_takes_priority(self, monkeypatch):
        # A model name with an embedded "128k" should still use regex extraction
        monkeypatch.setattr(sd, "_PREFS", {"last_model": "", "route_mode": ""})
        profile = sd._resolve_context_limit_profile(model_hint="llama-3.1-sonar-small-128k-online")
        assert profile["source"] == "model-name"
        assert int(profile["limit_tokens"]) == 128_000

    def test_unknown_model_falls_through_to_fallback(self, monkeypatch):
        monkeypatch.setattr(sd, "_PREFS", {"last_model": "", "route_mode": ""})
        profile = sd._resolve_context_limit_profile(model_hint="some-unknown-model-xyz")
        assert profile["source"] == "fallback"

    def test_gemini_family_still_applies_when_not_in_registry(self, monkeypatch):
        # gemini-2.0-flash IS in the registry — test with hypothetical future gemini
        monkeypatch.setattr(sd, "_PREFS", {"last_model": "", "route_mode": ""})
        with patch.object(cl_mod, "MODEL_CONTEXT_WINDOWS", {}):
            # rebuild sorted prefixes
            with patch.object(cl_mod, "_SORTED_PREFIXES", []):
                profile = sd._resolve_context_limit_profile(model_hint="gemini-99.0-ultra")
        assert profile["source"] == "family-gemini"


# ===========================================================================
# 3. _context_pressure_snapshot — model-registry integration
# ===========================================================================

class TestContextPressureSnapshotWithRegistry:
    def test_gpt4o_limit_used_correctly(self, monkeypatch):
        monkeypatch.setattr(sd, "_PREFS", {"last_model": "", "route_mode": ""})
        # gpt-4o has 128k limit; fill 80% = 102400 tokens = ~409600 chars
        history = [{"role": "user", "content": "x" * (128_000 * 4 * 80 // 100)}]
        snapshot = sd._context_pressure_snapshot(history, model_hint="gpt-4o")
        assert int(snapshot["limit_tokens"]) == 128_000
        assert snapshot["limit_source"] == "model-registry"
        assert int(snapshot["pct_history_raw"]) >= 75  # ~80%

    def test_large_model_shows_low_pressure_for_small_history(self, monkeypatch):
        monkeypatch.setattr(sd, "_PREFS", {"last_model": "", "route_mode": ""})
        history = [{"role": "user", "content": "hello"}]
        snapshot = sd._context_pressure_snapshot(history, model_hint="gemini-1.5-pro")
        assert int(snapshot["limit_tokens"]) == 2_097_152
        assert int(snapshot["pct_history_raw"]) == 0
        assert snapshot["band"] == "low"


# ===========================================================================
# 4. _emit_context_overflow_warning — threshold and de-duplication
# ===========================================================================

import openclaw_cli as cli_mod


class TestEmitContextOverflowWarning:
    def _make_history(self, fill_pct: int, limit: int = 128_000) -> list[dict]:
        """Build a history that puts context at approximately fill_pct% of limit."""
        chars = int(limit * 4 * fill_pct / 100)
        return [{"role": "user", "content": "x" * chars}]

    def setup_method(self):
        cli_mod._context_overflow_warned.clear()

    def test_warning_fires_at_80_percent(self, capsys, monkeypatch):
        monkeypatch.setattr(cli_mod, "_PREFS", {"last_model": "gpt-4o", "route_mode": "", "system_prompt": ""})
        monkeypatch.setattr(cli_mod, "_next_inject", "")
        history = self._make_history(82)
        cli_mod._emit_context_overflow_warning(history, session_id="test-sess")
        out = capsys.readouterr().out
        assert "⚠️" in out or "Warning" in out.lower() or "Context at" in out

    def test_warning_includes_percentage_and_model(self, capsys, monkeypatch):
        monkeypatch.setattr(cli_mod, "_PREFS", {"last_model": "gpt-4o", "route_mode": "", "system_prompt": ""})
        monkeypatch.setattr(cli_mod, "_next_inject", "")
        history = self._make_history(85)
        cli_mod._emit_context_overflow_warning(history, session_id="test-sess")
        out = capsys.readouterr().out
        assert "%" in out
        assert "128k" in out or "128,000" in out or "gpt-4o" in out

    def test_warning_does_not_refire_for_same_threshold(self, capsys, monkeypatch):
        monkeypatch.setattr(cli_mod, "_PREFS", {"last_model": "gpt-4o", "route_mode": "", "system_prompt": ""})
        monkeypatch.setattr(cli_mod, "_next_inject", "")
        history = self._make_history(85)
        cli_mod._emit_context_overflow_warning(history, session_id="test-sess")
        capsys.readouterr()  # clear
        cli_mod._emit_context_overflow_warning(history, session_id="test-sess")
        out2 = capsys.readouterr().out
        assert out2 == ""  # no second warning

    def test_warning_fires_again_at_next_threshold(self, capsys, monkeypatch):
        monkeypatch.setattr(cli_mod, "_PREFS", {"last_model": "gpt-4o", "route_mode": "", "system_prompt": ""})
        monkeypatch.setattr(cli_mod, "_next_inject", "")
        history_80 = self._make_history(83)
        cli_mod._emit_context_overflow_warning(history_80, session_id="test-sess")
        capsys.readouterr()

        history_92 = self._make_history(93)
        cli_mod._emit_context_overflow_warning(history_92, session_id="test-sess")
        out = capsys.readouterr().out
        # Should fire again at 90% threshold
        assert "⚠️" in out or "Context at" in out

    def test_no_warning_below_80_percent(self, capsys, monkeypatch):
        monkeypatch.setattr(cli_mod, "_PREFS", {"last_model": "gpt-4o", "route_mode": "", "system_prompt": ""})
        monkeypatch.setattr(cli_mod, "_next_inject", "")
        history = self._make_history(50)
        cli_mod._emit_context_overflow_warning(history, session_id="test-sess")
        out = capsys.readouterr().out
        assert out == ""

    def test_warning_isolated_per_session(self, capsys, monkeypatch):
        monkeypatch.setattr(cli_mod, "_PREFS", {"last_model": "gpt-4o", "route_mode": "", "system_prompt": ""})
        monkeypatch.setattr(cli_mod, "_next_inject", "")
        history = self._make_history(85)
        cli_mod._emit_context_overflow_warning(history, session_id="sess-a")
        capsys.readouterr()
        # Different session — should warn independently
        cli_mod._emit_context_overflow_warning(history, session_id="sess-b")
        out = capsys.readouterr().out
        assert "⚠️" in out or "Context at" in out
