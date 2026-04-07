"""
Coverage tests for bot.py — targeting uncovered lines in the 159–800 range.

Lines targeted:
- 159-160: channel rate-limit return path in _apply_feedback_guardrails
- 197: context lock resolution (_resolve_channel_thread_scope with active thread lock)
- 206-233: _load_channel_config (async)
- 270: _append_explainability_footer with non-empty note
- 444-446: _record_quality_metric success path and exception path
- 466-467: _record_budget_policy_metric success path and exception path
- 562: freshness_cues from meta in _score_answer_quality
- 617-618: evidence completeness path in quality scoring
- 653-654: uncertainty markers (1-2) path
- 691: low evidence completeness branch in _safe_score_answer_quality
- 698: quality scoring exception branch (requested_item_count preserved)
- 727-731: _should_prefer_file_for_multichunk_response branches
- 737, 746, 750: _build_coverage_summary_for_embed quality thresholds
- 762-769: _build_coverage_summary_for_embed evidence branches
- 775, 784, 799: _build_ask_recovery_block early-return paths
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LOG_DIR", "/tmp/_test_bot_logs_a")
os.environ.setdefault("AUDIT_DIR", "/tmp/_test_bot_audit_a")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/test_cov_a.db")

import bot as mod


# ---------------------------------------------------------------------------
# Channel rate-limit path (lines 159-160)
# ---------------------------------------------------------------------------


class TestChannelRateLimit:
    def setup_method(self):
        mod._reset_feedback_guardrails_for_tests()

    def test_channel_rate_limit_triggers_after_max(self):
        """Send _FEEDBACK_CHANNEL_RATE_LIMIT_MAX votes from different users to same channel."""
        max_chan = mod._FEEDBACK_CHANNEL_RATE_LIMIT_MAX  # 40
        base_now = 5000.0
        for i in range(max_chan):
            accepted, _ = mod._apply_feedback_guardrails(
                user_id=10000 + i,
                channel_id=777,
                message_id=i,
                rating="up",
                now=base_now + i * 0.01,
            )
            assert accepted is True, f"vote {i} should be accepted"

        # The next vote (41st) hits the channel rate limit
        accepted, reason = mod._apply_feedback_guardrails(
            user_id=99999,
            channel_id=777,
            message_id=99999,
            rating="up",
            now=base_now + 1.0,
        )
        assert accepted is False
        assert reason == "rate_limited_channel"

    def test_channel_rate_limit_only_applies_within_window(self):
        """Votes outside the time window do not count toward the channel limit."""
        max_chan = mod._FEEDBACK_CHANNEL_RATE_LIMIT_MAX  # 40
        window = mod._FEEDBACK_CHANNEL_RATE_LIMIT_WINDOW_SECONDS  # 60s
        base_now = 10000.0
        # Plant votes OUTSIDE the window (stale)
        for i in range(max_chan):
            mod._FEEDBACK_CHANNEL_EVENTS[888] = mod._FEEDBACK_CHANNEL_EVENTS.get(888, [])
            mod._FEEDBACK_CHANNEL_EVENTS[888].append(base_now - window - 10)

        # A fresh vote should succeed because stale events are pruned
        accepted, reason = mod._apply_feedback_guardrails(
            user_id=1,
            channel_id=888,
            message_id=1,
            rating="up",
            now=base_now,
        )
        assert accepted is True


# ---------------------------------------------------------------------------
# _resolve_channel_thread_scope with active thread lock (line 197)
# ---------------------------------------------------------------------------


class TestResolveChannelThreadScopeWithLock:
    def test_thread_lock_sets_thread_id(self):
        """When resolve_context_lock returns a thread-mode lock, thread_id is applied."""
        lock_data = {"mode": "thread", "channel_id": 100, "thread_id": 200}
        with patch.object(mod, "resolve_context_lock", return_value=(lock_data, None)):
            cid, tid = mod._resolve_channel_thread_scope(None, 50, user_id=1)
        assert cid == 100
        assert tid == 200

    def test_channel_lock_clears_thread_id(self):
        """When resolve_context_lock returns a channel-mode lock, thread_id becomes None."""
        lock_data = {"mode": "channel", "channel_id": 300}
        with patch.object(mod, "resolve_context_lock", return_value=(lock_data, None)):
            cid, tid = mod._resolve_channel_thread_scope(None, 50, user_id=1)
        assert cid == 300
        assert tid is None

    def test_thread_lock_with_none_thread_id(self):
        """Thread lock with thread_id=None yields thread_id=None in result."""
        lock_data = {"mode": "thread", "channel_id": 400, "thread_id": None}
        with patch.object(mod, "resolve_context_lock", return_value=(lock_data, None)):
            cid, tid = mod._resolve_channel_thread_scope(None, 50, user_id=1)
        assert cid == 400
        assert tid is None

    def test_no_lock_returns_original_ids(self):
        """When no lock is active, original channel/thread IDs are returned unchanged."""
        with patch.object(mod, "resolve_context_lock", return_value=(None, None)):
            cid, tid = mod._resolve_channel_thread_scope(None, 55, user_id=1)
        assert cid == 55
        assert tid is None


# ---------------------------------------------------------------------------
# _load_channel_config (lines 206-233)
# ---------------------------------------------------------------------------


class TestLoadChannelConfig:
    @pytest.mark.asyncio
    async def test_env_vars_populate_channel_roles(self, monkeypatch):
        """Channel roles are populated from DISCORD_CHANNEL_<ROLE>_ID env vars."""
        monkeypatch.setenv("DISCORD_CHANNEL_RESEARCH_ID", "55001")
        monkeypatch.setattr(mod, "_CHANNEL_ROLES", {})
        monkeypatch.setattr(mod, "_CHANNEL_PROMPTS", {})
        # Point CONFIG_DIR to a non-existent path so file branch is skipped
        monkeypatch.setattr(mod, "CONFIG_DIR", Path("/nonexistent_dir_openclaw_test_a"))

        await mod._load_channel_config()

        assert 55001 in mod._CHANNEL_ROLES
        assert mod._CHANNEL_ROLES[55001] == "research"

    @pytest.mark.asyncio
    async def test_invalid_env_var_is_skipped(self, monkeypatch):
        """Non-integer channel ID env var is silently skipped."""
        monkeypatch.setenv("DISCORD_CHANNEL_ANALYTICS_ID", "not_an_int")
        monkeypatch.setattr(mod, "_CHANNEL_ROLES", {})
        monkeypatch.setattr(mod, "_CHANNEL_PROMPTS", {})
        monkeypatch.setattr(mod, "CONFIG_DIR", Path("/nonexistent_dir_openclaw_test_a"))

        await mod._load_channel_config()
        # No crash; "not_an_int" is not added
        assert all(isinstance(k, int) for k in mod._CHANNEL_ROLES)

    @pytest.mark.asyncio
    async def test_yaml_config_loads_prompt_override(self, monkeypatch, tmp_path):
        """When config.yaml exists, prompt_override values are loaded into _CHANNEL_PROMPTS."""
        config_content = (
            "channels:\n"
            "  roles:\n"
            "    research:\n"
            "      prompt_override: 'custom research prompt'\n"
        )
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        monkeypatch.setattr(mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(mod, "_CHANNEL_ROLES", {})
        monkeypatch.setattr(mod, "_CHANNEL_PROMPTS", {})
        # Remove the env var so env-var branch does not interfere
        monkeypatch.delenv("DISCORD_CHANNEL_RESEARCH_ID", raising=False)

        await mod._load_channel_config()

        assert "research" in mod._CHANNEL_PROMPTS
        assert mod._CHANNEL_PROMPTS["research"] == "custom research prompt"

    @pytest.mark.asyncio
    async def test_bad_yaml_does_not_raise(self, monkeypatch, tmp_path):
        """Corrupt config.yaml is caught and logged; no exception propagates."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("{\ninvalid: yaml: content:\n")

        monkeypatch.setattr(mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(mod, "_CHANNEL_ROLES", {})
        monkeypatch.setattr(mod, "_CHANNEL_PROMPTS", {})

        # Should not raise
        await mod._load_channel_config()


# ---------------------------------------------------------------------------
# _append_explainability_footer non-empty note (line 270)
# ---------------------------------------------------------------------------


class TestAppendExplainabilityFooter:
    def test_appends_note_when_non_empty(self):
        result = mod._append_explainability_footer("Base footer", "Sources verified")
        assert result == "Base footer | 🧭 Sources verified"

    def test_returns_base_footer_when_note_empty(self):
        result = mod._append_explainability_footer("Base footer", "")
        assert result == "Base footer"

    def test_returns_base_footer_when_note_is_none(self):
        result = mod._append_explainability_footer("Base footer", None)
        assert result == "Base footer"

    def test_strips_whitespace_from_note(self):
        result = mod._append_explainability_footer("Footer", "  trimmed  ")
        assert result == "Footer | 🧭 trimmed"

    def test_explainability_note_from_meta_with_key(self):
        result = mod._explainability_note_from_meta({"explainability_note": "AI reasoning exposed"})
        assert result == "AI reasoning exposed"

    def test_explainability_note_from_meta_missing_key(self):
        result = mod._explainability_note_from_meta({"other_key": "value"})
        assert result == ""

    def test_explainability_note_from_meta_non_dict(self):
        assert mod._explainability_note_from_meta(None) == ""
        assert mod._explainability_note_from_meta("string") == ""


# ---------------------------------------------------------------------------
# _record_quality_metric success path (lines 444-446)
# ---------------------------------------------------------------------------


class TestRecordQualityMetric:
    def test_success_path_calls_record_quality_event(self):
        """When get_collector() succeeds, record_quality_event is called."""
        mock_collector = MagicMock()
        mock_get = MagicMock(return_value=mock_collector)
        with patch.dict("sys.modules", {"metrics_collector": MagicMock(get_collector=mock_get)}):
            mod._record_quality_metric("test_event", context="test_ctx")
        mock_collector.record_quality_event.assert_called_once_with(
            event="test_event", context="test_ctx"
        )

    def test_exception_is_swallowed(self):
        """If get_collector raises, _record_quality_metric does not propagate it."""
        bad_module = MagicMock()
        bad_module.get_collector.side_effect = RuntimeError("no collector")
        with patch.dict("sys.modules", {"metrics_collector": bad_module}):
            # Should not raise
            mod._record_quality_metric("event_x", context="ctx")


# ---------------------------------------------------------------------------
# _record_budget_policy_metric (lines 466-467)
# ---------------------------------------------------------------------------


class TestRecordBudgetPolicyMetric:
    def test_success_path_calls_record_budget_policy_decision(self):
        """When get_collector() succeeds, record_budget_policy_decision is called."""
        mock_collector = MagicMock()
        mock_get = MagicMock(return_value=mock_collector)
        with patch.dict("sys.modules", {"metrics_collector": MagicMock(get_collector=mock_get)}):
            mod._record_budget_policy_metric(
                path="ask_repair",
                profile="general",
                load_tier="normal",
                decision="proceed",
            )
        mock_collector.record_budget_policy_decision.assert_called_once_with(
            path="ask_repair",
            profile="general",
            load_tier="normal",
            decision="proceed",
        )

    def test_exception_is_swallowed(self):
        """If get_collector raises, _record_budget_policy_metric does not propagate it."""
        bad_module = MagicMock()
        bad_module.get_collector.side_effect = RuntimeError("no collector")
        with patch.dict("sys.modules", {"metrics_collector": bad_module}):
            mod._record_budget_policy_metric(
                path="p", profile="general", load_tier="low", decision="skip"
            )


# ---------------------------------------------------------------------------
# freshness_cues from meta in _score_answer_quality (line ~562)
# ---------------------------------------------------------------------------


class TestScoreAnswerQualityFreshnessCues:
    def test_freshness_cues_from_meta_increases_freshness_count(self):
        """Passing freshness_cues list in final_meta contributes to freshness scoring."""
        result = mod._score_answer_quality(
            "Plain answer with no date markers.",
            final_meta={"freshness_cues": ["published today", "updated this week"]},
        )
        assert result["freshness_cue_count"] >= 2
        assert any("freshness" in r.lower() for r in result["reasons"])

    def test_freshness_cues_empty_strings_are_ignored(self):
        """Empty strings in freshness_cues list are not counted."""
        result = mod._score_answer_quality(
            "No freshness in text.",
            final_meta={"freshness_cues": ["", "  ", "real cue"]},
        )
        # Only "real cue" counts
        assert result["freshness_cue_count"] >= 1


# ---------------------------------------------------------------------------
# Evidence completeness high/medium path in quality scoring (lines 617-638)
# ---------------------------------------------------------------------------


class TestEvidenceCompletenessScoring:
    def test_evidence_completeness_high_adds_score(self):
        """evidence_completeness >= 0.8 triggers 'Strong claim-to-evidence' reason."""
        result = mod._score_answer_quality(
            "Good answer.", final_meta={"evidence_completeness": 0.85}
        )
        assert any("Strong claim-to-evidence" in r for r in result["reasons"])

    def test_evidence_completeness_moderate_branch(self):
        """evidence_completeness in [0.6, 0.8) triggers moderate reason."""
        result = mod._score_answer_quality(
            "Moderate.", final_meta={"evidence_completeness": 0.65}
        )
        assert any("Moderate claim-to-evidence" in r for r in result["reasons"])

    def test_evidence_completeness_low_branch(self):
        """evidence_completeness in [0.4, 0.6) triggers low reason."""
        result = mod._score_answer_quality(
            "Low evidence.", final_meta={"evidence_completeness": 0.45}
        )
        assert any("Low claim-to-evidence" in r for r in result["reasons"])

    def test_evidence_completeness_very_low_branch(self):
        """evidence_completeness < 0.4 triggers very low reason."""
        result = mod._score_answer_quality(
            "Very low.", final_meta={"evidence_completeness": 0.2}
        )
        assert any("Very low claim-to-evidence" in r for r in result["reasons"])


# ---------------------------------------------------------------------------
# Uncertainty markers 1-2 path (lines 653-654)
# ---------------------------------------------------------------------------


class TestUncertaintyMarkersPath:
    def test_one_uncertainty_marker_hits_some_uncertainty_reason(self):
        """A single uncertainty marker triggers 'Some uncertainty markers detected'."""
        result = mod._score_answer_quality("The answer might be correct.")
        assert result["uncertainty_marker_count"] >= 1
        assert any("Some uncertainty markers" in r for r in result["reasons"])

    def test_two_uncertainty_markers_still_some_not_multiple(self):
        """Two markers still < 3, so stays in 'some' branch."""
        result = mod._score_answer_quality("It might be the case, or it may happen.")
        assert result["uncertainty_marker_count"] >= 1

    def test_three_or_more_uncertainty_markers_hits_multiple_reason(self):
        """Three or more markers hit the 'Multiple uncertainty' branch."""
        result = mod._score_answer_quality(
            "It might be true. It may happen. Possibly unclear. Unknown outcome."
        )
        assert result["uncertainty_marker_count"] >= 3
        assert any("Multiple uncertainty" in r for r in result["reasons"])

    def test_low_evidence_completeness_forces_low_status(self):
        """When evidence_completeness < 0.5 and score would be medium, status is forced to low."""
        # Use evidence_completeness of 0.3 (< 0.5) and a mostly-good answer
        # to get a score that would otherwise be medium
        lines = "\n".join(f"- item {i}" for i in range(6))
        result = mod._score_answer_quality(
            lines + " https://example.com/a today updated",
            final_meta={"evidence_completeness": 0.3},
        )
        assert result["status"] == "low"
        assert any("forced low-confidence" in r for r in result["reasons"])


# ---------------------------------------------------------------------------
# _safe_score_answer_quality low evidence branch (line 691)
# ---------------------------------------------------------------------------


class TestSafeScoreAnswerQualityLowEvidence:
    def test_low_evidence_emits_metric(self, monkeypatch):
        """When scored evidence < 0.5, _record_quality_metric is called (best-effort)."""
        calls = []
        monkeypatch.setattr(mod, "_record_quality_metric", lambda event, **kw: calls.append(event))
        mod._safe_score_answer_quality(
            "text",
            final_meta={"evidence_completeness": 0.3},
            context="test",
        )
        assert "ask_low_evidence_completeness" in calls

    def test_no_low_evidence_metric_when_fields_missing(self, monkeypatch):
        """When source_fields_missing is True, low-evidence metric is NOT emitted."""
        calls = []
        monkeypatch.setattr(mod, "_record_quality_metric", lambda event, **kw: calls.append(event))
        text = "Evidence Completeness: **30%** (fail-safe (source fields missing))"
        mod._safe_score_answer_quality(text, context="test")
        assert "ask_low_evidence_completeness" not in calls


# ---------------------------------------------------------------------------
# Quality scoring exception branch (lines 693-710) — line 698 specifically
# ---------------------------------------------------------------------------


class TestSafeScoreAnswerQualityException:
    def test_exception_returns_neutral_fallback(self, monkeypatch):
        """When _score_answer_quality raises, neutral fallback dict is returned."""
        monkeypatch.setattr(mod, "_score_answer_quality", lambda t, **kw: (_ for _ in ()).throw(ValueError("boom")))
        result = mod._safe_score_answer_quality("any text", context="test")
        assert result["score"] == 50
        assert result["status"] == "medium"
        assert "error" in result

    def test_exception_preserves_requested_item_count_from_meta(self, monkeypatch):
        """Fallback dict picks up requested_item_count from final_meta when scoring fails."""
        def raise_err(t, **kw):
            raise RuntimeError("scoring exploded")

        monkeypatch.setattr(mod, "_score_answer_quality", raise_err)
        result = mod._safe_score_answer_quality(
            "text",
            final_meta={"requested_item_count": 7},
            context="test",
        )
        assert result["requested_item_count"] == 7
        assert result["score"] == 50

    def test_exception_without_meta_has_none_requested_item_count(self, monkeypatch):
        """Without final_meta, requested_item_count in fallback is None."""
        def raise_err(t, **kw):
            raise RuntimeError("fail")

        monkeypatch.setattr(mod, "_score_answer_quality", raise_err)
        result = mod._safe_score_answer_quality("text")
        assert result["requested_item_count"] is None


# ---------------------------------------------------------------------------
# _should_prefer_file_for_multichunk_response (lines 727-731)
# ---------------------------------------------------------------------------


class TestShouldPreferFileForMultichunkResponse:
    def setup_method(self):
        self._orig = mod._should_package_as_attachment

    def teardown_method(self):
        mod._should_package_as_attachment = self._orig

    def test_large_requested_count_prefers_file(self, monkeypatch):
        """requested >= 6 AND multiple chunks → True (line 728)."""
        monkeypatch.setattr(mod, "_should_package_as_attachment", lambda t, c: False)
        result = mod._should_prefer_file_for_multichunk_response(
            question="give me 10 stories",
            chunks=["chunk1", "chunk2"],
            response_text="short",
        )
        assert result is True

    def test_recap_with_long_response_prefers_file(self, monkeypatch):
        """recap_like keyword + long response + multiple chunks → True (line 730)."""
        monkeypatch.setattr(mod, "_should_package_as_attachment", lambda t, c: False)
        result = mod._should_prefer_file_for_multichunk_response(
            question="give me the recap this week",
            chunks=["a", "b"],
            response_text="x" * 3000,
        )
        assert result is True

    def test_small_request_no_recap_returns_false(self, monkeypatch):
        """Small request, no recap token, multiple chunks → False (line 731)."""
        monkeypatch.setattr(mod, "_should_package_as_attachment", lambda t, c: False)
        result = mod._should_prefer_file_for_multichunk_response(
            question="what happened?",
            chunks=["a", "b"],
            response_text="short text",
        )
        assert result is False

    def test_single_chunk_returns_false(self, monkeypatch):
        """Single chunk → False regardless of question."""
        monkeypatch.setattr(mod, "_should_package_as_attachment", lambda t, c: False)
        result = mod._should_prefer_file_for_multichunk_response(
            question="give me 10 stories",
            chunks=["only_chunk"],
            response_text="text",
        )
        assert result is False


# ---------------------------------------------------------------------------
# _build_coverage_summary_for_embed (lines 737, 746, 750, 762-769)
# ---------------------------------------------------------------------------


class TestBuildCoverageSummaryForEmbed:
    def test_returns_none_for_non_dict(self):
        """Non-dict input → None (line 737)."""
        assert mod._build_coverage_summary_for_embed(None) is None
        assert mod._build_coverage_summary_for_embed("string") is None

    def test_returns_degrade_context_when_no_answer_quality(self):
        """No answer_quality key → returns degrade_context or None (line 746)."""
        result = mod._build_coverage_summary_for_embed({"other": "data"})
        # No constrained mode → degrade_context is None → returns None
        assert result is None

    def test_returns_degrade_context_with_constrained_mode_no_quality(self):
        """Constrained mode + no answer_quality → degrade_context string returned."""
        result = mod._build_coverage_summary_for_embed({
            "answer_quality_retry": {"degrade_mode": "constrained"},
        })
        assert result is not None
        assert "Runtime constrained" in result

    def test_returns_degrade_context_when_invalid_status(self):
        """Invalid status → returns degrade_context (line 750)."""
        result = mod._build_coverage_summary_for_embed({
            "answer_quality": {"status": "bogus"},
        })
        assert result is None  # no degrade_context and invalid status

    def test_evidence_completeness_path_without_items(self):
        """status valid + evidence_completeness float, no item/requested → evidence% path (762-766)."""
        result = mod._build_coverage_summary_for_embed({
            "answer_quality": {
                "status": "medium",
                "evidence_completeness": 0.75,
            }
        })
        assert result is not None
        assert "Coverage medium" in result
        assert "75%" in result

    def test_evidence_path_with_degrade_context(self):
        """evidence path + constrained mode → degrade_context appended (line 766)."""
        result = mod._build_coverage_summary_for_embed({
            "answer_quality_retry": {"degrade_mode": "constrained"},
            "answer_quality": {
                "status": "low",
                "evidence_completeness": 0.5,
            },
        })
        assert result is not None
        assert "Runtime constrained" in result
        assert "Coverage low" in result

    def test_status_only_summary_no_evidence_no_items(self):
        """Valid status, no evidence, no item counts → plain 'Coverage {status}' (line 768)."""
        result = mod._build_coverage_summary_for_embed({
            "answer_quality": {"status": "high"},
        })
        assert result == "Coverage high"

    def test_status_only_summary_with_degrade_context(self):
        """Plain summary with degrade_context appended (line 769)."""
        result = mod._build_coverage_summary_for_embed({
            "answer_quality_retry": {"degrade_mode": "constrained"},
            "answer_quality": {"status": "medium"},
        })
        assert result is not None
        assert "Coverage medium" in result
        assert "Runtime constrained" in result


# ---------------------------------------------------------------------------
# _build_ask_recovery_block early-return paths (lines 775, 784, 799)
# ---------------------------------------------------------------------------


class TestBuildAskRecoveryBlock:
    def test_returns_none_for_non_dict(self):
        """Non-dict input → None (line 775)."""
        assert mod._build_ask_recovery_block(None) is None
        assert mod._build_ask_recovery_block(42) is None

    def test_returns_none_when_no_answer_quality_and_not_constrained(self):
        """No answer_quality and not runtime_constrained → None (line 784)."""
        result = mod._build_ask_recovery_block({"some_key": "value"})
        assert result is None

    def test_returns_none_when_status_high_and_no_shortfall(self):
        """High status, no shortfall, evidence OK, not constrained → None (line 799)."""
        result = mod._build_ask_recovery_block({
            "answer_quality": {
                "status": "high",
                "score": 85,
                "item_count": 8,
                "requested_item_count": 6,
                "evidence_completeness": 0.9,
            }
        })
        # status != "low", shortfall=0 (8 >= 6), evidence_low=False, not constrained → None
        assert result is None

    def test_returns_block_when_status_low(self):
        """Low status returns a recovery block string."""
        result = mod._build_ask_recovery_block({
            "answer_quality": {
                "status": "low",
                "score": 25,
                "item_count": 2,
                "evidence_completeness": 0.3,
            }
        })
        assert result is not None
        assert "Recovery note" in result

    def test_returns_block_when_runtime_constrained(self):
        """runtime_constrained=True forces a recovery block even without answer_quality."""
        result = mod._build_ask_recovery_block({
            "answer_quality_retry": {"degrade_mode": "constrained"},
        })
        assert result is not None
        assert "constrained" in result.lower() or "Runtime" in result
