"""Tests for quality_helpers.py — pure helper functions and logic paths."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

import quality_helpers as mod
from quality_helpers import (
    _append_explainability_footer,
    _build_ask_context_controls,
    _build_ask_failure_message,
    _build_ask_recovery_block,
    _build_ask_timeout_message,
    _build_coverage_summary_for_embed,
    _build_quality_broadening_prompt,
    _classify_ask_failure,
    _count_markdown_table_items,
    _explainability_note_from_meta,
    _extract_distinct_source_domains,
    _extract_reported_evidence_completeness,
    _extract_requested_item_count,
    _quality_retry_improved,
    _safe_score_answer_quality,
    _score_answer_quality,
    _with_requested_item_target,
)

# ---------------------------------------------------------------------------
# _explainability_note_from_meta
# ---------------------------------------------------------------------------


class TestExplainabilityNoteFromMeta:
    def test_returns_empty_for_none(self):
        assert _explainability_note_from_meta(None) == ""

    def test_returns_empty_for_non_dict(self):
        assert _explainability_note_from_meta("string") == ""

    def test_returns_empty_when_key_missing(self):
        assert _explainability_note_from_meta({}) == ""

    def test_returns_note_stripped(self):
        assert _explainability_note_from_meta({"explainability_note": "  hello  "}) == "hello"

    def test_returns_empty_when_note_not_string(self):
        assert _explainability_note_from_meta({"explainability_note": 42}) == ""


# ---------------------------------------------------------------------------
# _append_explainability_footer
# ---------------------------------------------------------------------------


class TestAppendExplainabilityFooter:
    def test_no_note_returns_base(self):
        assert _append_explainability_footer("base", "") == "base"

    def test_none_note_returns_base(self):
        assert _append_explainability_footer("base", None) == "base"

    def test_appends_note(self):
        result = _append_explainability_footer("base", "some note")
        assert result == "base | 🧭 some note"

    def test_whitespace_only_note_returns_base(self):
        assert _append_explainability_footer("base", "   ") == "base"


# ---------------------------------------------------------------------------
# _build_ask_context_controls
# ---------------------------------------------------------------------------


class TestBuildAskContextControls:
    def test_empty_inputs_returns_empty_dict(self):
        assert _build_ask_context_controls() == {}

    def test_scope_normalizes(self):
        result = _build_ask_context_controls(scope="  Channel_Thread  ")
        assert result["scope"] == "channel-thread"

    def test_reset_context_false(self):
        result = _build_ask_context_controls(reset_context=False)
        assert result["reset_context"] is False

    def test_reset_context_true(self):
        result = _build_ask_context_controls(reset_context=True)
        assert result["reset_context"] is True

    def test_anchor_included(self):
        result = _build_ask_context_controls(anchor="  abc123  ")
        assert result["anchor"] == "abc123"

    def test_empty_anchor_not_included(self):
        result = _build_ask_context_controls(anchor="")
        assert "anchor" not in result

    def test_all_options(self):
        result = _build_ask_context_controls(scope="thread", reset_context=True, anchor="x")
        assert result["scope"] == "thread"
        assert result["reset_context"] is True
        assert result["anchor"] == "x"


# ---------------------------------------------------------------------------
# _build_ask_timeout_message
# ---------------------------------------------------------------------------


class TestBuildAskTimeoutMessage:
    def test_contains_elapsed_seconds(self):
        msg = _build_ask_timeout_message(
            elapsed_seconds=30.7,
            progress_lines=[],
            model_pref="gemini",
            trace_id="trace-1",
        )
        assert "31s" in msg

    def test_contains_trace_id(self):
        msg = _build_ask_timeout_message(
            elapsed_seconds=10.0,
            progress_lines=[],
            model_pref="auto",
            trace_id="my-trace-id",
        )
        assert "my-trace-id" in msg

    def test_no_progress_lines_shows_default(self):
        msg = _build_ask_timeout_message(
            elapsed_seconds=5.0,
            progress_lines=[],
            model_pref="gemini",
        )
        assert "No progress checkpoints" in msg

    def test_progress_lines_shown(self):
        msg = _build_ask_timeout_message(
            elapsed_seconds=5.0,
            progress_lines=["step 1", "step 2"],
            model_pref="gemini",
        )
        assert "step 1" in msg or "step 2" in msg

    def test_at_most_6_progress_lines(self):
        lines = [f"step {i}" for i in range(10)]
        msg = _build_ask_timeout_message(
            elapsed_seconds=5.0,
            progress_lines=lines,
            model_pref="auto",
        )
        # last 6 lines only
        assert "step 9" in msg
        assert "step 0" not in msg


# ---------------------------------------------------------------------------
# _classify_ask_failure
# ---------------------------------------------------------------------------


class TestClassifyAskFailure:
    def test_timeout_in_message(self):
        assert _classify_ask_failure("request timed out") == "timeout"

    def test_quality_helpers_coverage_rate_limit_429(self):
        assert _classify_ask_failure("error 429 from api") == "rate_limit"

    def test_rate_limit_quota(self):
        assert _classify_ask_failure("quota exceeded") == "rate_limit"

    def test_tool_error(self):
        assert _classify_ask_failure("tool call failed") == "tool"

    def test_provider_gemini(self):
        assert _classify_ask_failure("gemini returned 503") == "provider"

    def test_provider_openai(self):
        assert _classify_ask_failure("openai connection refused") == "provider"

    def test_quality_helpers_coverage_general_fallback(self):
        assert _classify_ask_failure("something weird happened") == "general"

    def test_quality_helpers_coverage_empty_message(self):
        assert _classify_ask_failure("") == "general"

    def test_routing_notes_factor_in(self):
        result = _classify_ask_failure("unknown", routing_notes=["tool gateway error"])
        assert result == "tool"


# ---------------------------------------------------------------------------
# _build_ask_failure_message
# ---------------------------------------------------------------------------


class TestBuildAskFailureMessage:
    def test_contains_category_title_timeout(self):
        msg = _build_ask_failure_message(
            question="what's up?",
            model_pref="auto",
            trace_id="t1",
            category="timeout",
        )
        assert "Timeout" in msg
        assert "t1" in msg

    def test_contains_question_in_code_block(self):
        msg = _build_ask_failure_message(
            question="my question",
            model_pref="auto",
            trace_id="t2",
            category="general",
        )
        assert "my question" in msg

    def test_unknown_category_falls_back_gracefully(self):
        msg = _build_ask_failure_message(
            question="q",
            model_pref="auto",
            trace_id="t3",
            category="unknown_cat",
        )
        assert "Request failure" in msg


# ---------------------------------------------------------------------------
# _count_markdown_table_items
# ---------------------------------------------------------------------------


class TestCountMarkdownTableItems:
    def test_empty_text_returns_zero(self):
        assert _count_markdown_table_items("") == 0

    def test_no_table_returns_zero(self):
        assert _count_markdown_table_items("just plain text") == 0

    def test_header_only_returns_zero(self):
        text = "| Name | Value |\n|------|-------|\n"
        assert _count_markdown_table_items(text) == 0

    def test_one_data_row(self):
        text = "| Name | Value |\n|------|-------|\n| a | b |\n"
        # header row + separator + 1 data row = 2 non-separator rows, minus 1 header
        assert _count_markdown_table_items(text) == 1

    def test_multiple_rows(self):
        text = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |\n"
        assert _count_markdown_table_items(text) == 3


# ---------------------------------------------------------------------------
# _extract_distinct_source_domains
# ---------------------------------------------------------------------------


class TestExtractDistinctSourceDomains:
    def test_empty_returns_empty_set(self):
        assert _extract_distinct_source_domains("") == set()

    def test_single_domain(self):
        domains = _extract_distinct_source_domains("See https://example.com/page for details")
        assert "example.com" in domains

    def test_multiple_domains(self):
        text = "From https://nytimes.com/a and https://espn.com/b"
        domains = _extract_distinct_source_domains(text)
        assert "nytimes.com" in domains
        assert "espn.com" in domains

    def test_quality_helpers_coverage_strips_www(self):
        domains = _extract_distinct_source_domains("https://www.google.com/search")
        assert "google.com" in domains

    def test_deduplication(self):
        text = "https://example.com/a and https://example.com/b"
        domains = _extract_distinct_source_domains(text)
        assert len(domains) == 1


# ---------------------------------------------------------------------------
# _extract_reported_evidence_completeness
# ---------------------------------------------------------------------------


class TestExtractReportedEvidenceCompleteness:
    def test_no_data_returns_none_false(self):
        val, missing = _extract_reported_evidence_completeness("")
        assert val is None
        assert missing is False

    def test_reads_from_meta_float(self):
        val, missing = _extract_reported_evidence_completeness("", final_meta={"evidence_completeness": 0.75})
        assert val == 0.75
        assert missing is False

    def test_reads_from_meta_int(self):
        val, missing = _extract_reported_evidence_completeness("", final_meta={"evidence_completeness": 1})
        assert val == 1.0

    def test_reads_from_meta_clamps(self):
        val, _ = _extract_reported_evidence_completeness("", final_meta={"evidence_completeness": 1.5})
        assert val == 1.0

    def test_reads_from_text_regex(self):
        text = "Evidence Completeness: **80%**"
        val, missing = _extract_reported_evidence_completeness(text)
        assert val == 0.8
        assert missing is False

    def test_source_fields_missing_flag(self):
        text = "Evidence Completeness: **60%** fail-safe (source fields missing)"
        val, missing = _extract_reported_evidence_completeness(text)
        assert val == 0.6
        assert missing is True


# ---------------------------------------------------------------------------
# _extract_requested_item_count
# ---------------------------------------------------------------------------


class TestExtractRequestedItemCount:
    def test_quality_helpers_coverage_no_count_returns_none(self):
        assert _extract_requested_item_count("what happened today?") is None

    def test_quality_helpers_coverage_top_n_stories(self):
        assert _extract_requested_item_count("give me top 5 stories") == 5

    def test_quality_helpers_coverage_bare_count(self):
        assert _extract_requested_item_count("10 headlines from this week") == 10

    def test_quality_helpers_coverage_capped_at_25(self):
        assert _extract_requested_item_count("top 99 stories") == 25

    def test_minimum_1(self):
        # Edge case: "top 0 stories" — regex matches \d{1,2} so 0 is valid, clamped to 1
        result = _extract_requested_item_count("top 0 stories")
        if result is not None:
            assert result >= 1


# ---------------------------------------------------------------------------
# _with_requested_item_target
# ---------------------------------------------------------------------------


class TestWithRequestedItemTarget:
    def test_adds_requested_count(self):
        result = _with_requested_item_target(None, question="give me top 5 stories")
        assert result.get("requested_item_count") == 5

    def test_preserves_existing_meta(self):
        result = _with_requested_item_target({"foo": "bar"}, question="anything")
        assert result.get("foo") == "bar"

    def test_no_count_in_question(self):
        result = _with_requested_item_target(None, question="what happened?")
        assert "requested_item_count" not in result


# ---------------------------------------------------------------------------
# _score_answer_quality
# ---------------------------------------------------------------------------


class TestScoreAnswerQuality:
    def test_empty_text_returns_low_score(self):
        result = _score_answer_quality("")
        assert result["score"] < 50
        assert result["status"] in {"low", "medium"}

    def test_high_quality_response(self):
        text = (
            "Today's latest top stories updated as of this week:\n"
            "- Item one from https://nytimes.com/1\n"
            "- Item two from https://espn.com/2\n"
            "- Item three from https://bbc.com/3\n"
            "- Item four from https://cnn.com/4\n"
            "- Item five from https://reuters.com/5\n"
            "- Item six from https://bloomberg.com/6\n"
        )
        result = _score_answer_quality(text)
        assert result["score"] >= 45
        assert result["item_count"] >= 3

    def test_uncertainty_markers_reduce_score(self):
        text = "Not sure, unclear, might be, possibly, unknown, incomplete, tbd"
        base = _score_answer_quality("Some clear answer today.")
        uncertain = _score_answer_quality(text)
        assert uncertain["score"] <= base["score"]

    def test_evidence_completeness_from_meta(self):
        result = _score_answer_quality("ok text", final_meta={"evidence_completeness": 0.9})
        assert result["evidence_completeness"] == 0.9

    def test_score_clamped_0_to_100(self):
        result = _score_answer_quality("")
        assert 0 <= result["score"] <= 100

    def test_requested_item_shortfall(self):
        result = _score_answer_quality(
            "- one\n- two\n",
            final_meta={"requested_item_count": 10},
        )
        assert result["status"] == "low"
        assert result["score"] <= 50

    def test_status_keys_present(self):
        result = _score_answer_quality("Some text")
        assert "score" in result
        assert "status" in result
        assert "reasons" in result
        assert "item_count" in result
        assert "source_domain_count" in result


# ---------------------------------------------------------------------------
# _safe_score_answer_quality
# ---------------------------------------------------------------------------


class TestSafeScoreAnswerQuality:
    def test_normal_case(self):
        result = _safe_score_answer_quality("some answer text")
        assert "score" in result

    def test_exception_returns_fallback(self):
        with patch.object(mod, "_score_answer_quality", side_effect=RuntimeError("boom")):
            result = _safe_score_answer_quality("text")
        assert result["score"] == 50
        assert result["status"] == "medium"
        assert "error" in result

    def test_quality_helpers_coverage_low_evidence_emits_metric(self):
        metric_calls = []
        with patch.object(mod, "_record_quality_metric", side_effect=lambda e, **_: metric_calls.append(e)):
            _safe_score_answer_quality("x", final_meta={"evidence_completeness": 0.3})
        assert "ask_low_evidence_completeness" in metric_calls


# ---------------------------------------------------------------------------
# _build_coverage_summary_for_embed
# ---------------------------------------------------------------------------


class TestBuildCoverageSummaryForEmbed:
    def test_quality_helpers_coverage_none_meta_returns_none(self):
        assert _build_coverage_summary_for_embed(None) is None

    def test_no_answer_quality_returns_none(self):
        assert _build_coverage_summary_for_embed({}) is None

    def test_basic_status(self):
        meta = {"answer_quality": {"status": "high"}}
        result = _build_coverage_summary_for_embed(meta)
        assert result is not None
        assert "high" in result

    def test_with_item_counts(self):
        meta = {
            "answer_quality": {
                "status": "low",
                "item_count": 3,
                "requested_item_count": 8,
            }
        }
        result = _build_coverage_summary_for_embed(meta)
        assert "3/8" in result

    def test_with_evidence_completeness(self):
        meta = {
            "answer_quality": {
                "status": "medium",
                "evidence_completeness": 0.72,
            }
        }
        result = _build_coverage_summary_for_embed(meta)
        assert "72%" in result

    def test_constrained_degrade_mode(self):
        meta = {
            "answer_quality": {"status": "medium"},
            "answer_quality_retry": {"degrade_mode": "constrained"},
        }
        result = _build_coverage_summary_for_embed(meta)
        assert "constrained" in (result or "").lower() or "Runtime" in (result or "")


# ---------------------------------------------------------------------------
# _build_ask_recovery_block
# ---------------------------------------------------------------------------


class TestBuildAskRecoveryBlock:
    def test_quality_helpers_coverage_none_meta_returns_none_v2(self):
        assert _build_ask_recovery_block(None) is None

    def test_high_quality_returns_none(self):
        meta = {"answer_quality": {"status": "high"}}
        assert _build_ask_recovery_block(meta) is None

    def test_low_quality_without_shortfall_returns_none(self):
        # General low quality (no numeric shortfall, not constrained) should NOT show recovery block
        meta = {"answer_quality": {"status": "low"}}
        result = _build_ask_recovery_block(meta)
        assert result is None

    def test_low_quality_with_shortfall_returns_block(self):
        meta = {
            "answer_quality": {
                "status": "low",
                "item_count": 1,
                "requested_item_count": 5,
            }
        }
        result = _build_ask_recovery_block(meta)
        assert result is not None
        assert "Recovery note" in result

    def test_item_shortfall_shows_counts(self):
        meta = {
            "answer_quality": {
                "status": "low",
                "item_count": 2,
                "requested_item_count": 10,
            }
        }
        result = _build_ask_recovery_block(meta)
        assert "2/10" in result

    def test_constrained_mode_surfaced(self):
        meta = {
            "answer_quality": {},
            "answer_quality_retry": {"degrade_mode": "constrained"},
        }
        result = _build_ask_recovery_block(meta)
        assert result is not None
        assert "constrained" in result.lower() or "Runtime" in result


# ---------------------------------------------------------------------------
# _build_quality_broadening_prompt
# ---------------------------------------------------------------------------


class TestBuildQualityBroadeningPrompt:
    def test_contains_original_question(self):
        result = _build_quality_broadening_prompt("my question", ["low coverage"])
        assert "my question" in result

    def test_contains_reason(self):
        result = _build_quality_broadening_prompt("q", ["low coverage", "uncertainty"])
        assert "low coverage" in result

    def test_empty_reasons(self):
        result = _build_quality_broadening_prompt("q", [])
        assert "q" in result


# ---------------------------------------------------------------------------
# _quality_retry_improved
# ---------------------------------------------------------------------------


class TestQualityRetryImproved:
    def test_status_upgrade_to_high(self):
        original = {"score": 40, "status": "low"}
        retried = {"score": 80, "status": "high"}
        assert _quality_retry_improved(original=original, retried=retried) is True

    def test_score_improvement_threshold(self):
        original = {"score": 40, "status": "low"}
        retried = {"score": 51, "status": "medium"}
        assert _quality_retry_improved(original=original, retried=retried) is True

    def test_score_below_threshold_returns_false(self):
        original = {"score": 40, "status": "low"}
        retried = {"score": 45, "status": "medium"}
        assert _quality_retry_improved(original=original, retried=retried) is False

    def test_both_high_same_score_returns_false(self):
        original = {"score": 80, "status": "high"}
        retried = {"score": 85, "status": "high"}
        # 85 < 80 + 10, so no improvement
        assert _quality_retry_improved(original=original, retried=retried) is False


# ---------------------------------------------------------------------------
# _run_quality_auto_repair (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_quality_auto_repair_skips_when_high_quality():
    """High-quality answers skip retry."""
    from quality_helpers import _run_quality_auto_repair

    mock_profile = MagicMock(return_value={"retrieval_profile": "general"})
    mock_snapshot = MagicMock(return_value=None)
    mock_policy = MagicMock(
        return_value={
            "load_tier": "normal",
            "decision": "allow",
            "degrade_mode": "normal",
            "degrade_reasons": [],
            "metrics_available": True,
        }
    )
    mock_budget = MagicMock(return_value={"max_attempts": 1, "timeout_seconds": 45})
    mock_metric = MagicMock()

    with (
        patch.object(mod, "get_effective_channel_profile", mock_profile),
        patch.object(mod, "get_latency_load_snapshot", mock_snapshot),
        patch.object(mod, "apply_repair_budget", mock_budget),
        patch.object(mod, "_record_quality_metric", mock_metric),
        patch.object(mod, "_record_budget_policy_metric", MagicMock()),
    ):
        with patch("ask_orchestrator.select_latency_budget_policy", mock_policy):
            result = await _run_quality_auto_repair(
                question="test q",
                response_text="good answer",
                model_used="gemini",
                final_meta={},
                quality_meta={"score": 80, "status": "high", "reasons": []},
                context="test",
                run_retry_stream=AsyncMock(),
            )
    assert result["response_text"] == "good answer"
    assert result["retry_summary"]["outcome"] == "skipped"


@pytest.mark.asyncio
async def test_run_quality_auto_repair_runs_retry_on_low_quality():
    """Low-quality answers attempt retry stream."""
    from quality_helpers import _run_quality_auto_repair

    retry_result = SimpleNamespace(
        response_text="improved answer",
        final_meta={"evidence_completeness": 0.9},
        model_used="gemini",
    )
    mock_run = AsyncMock(return_value=retry_result)

    mock_profile = MagicMock(return_value={"retrieval_profile": "general"})
    mock_snapshot = MagicMock(return_value=None)
    mock_policy = MagicMock(
        return_value={
            "load_tier": "normal",
            "decision": "allow",
            "degrade_mode": "normal",
            "degrade_reasons": [],
            "metrics_available": True,
        }
    )
    mock_budget = MagicMock(return_value={"max_attempts": 1, "timeout_seconds": 45})

    with (
        patch.object(mod, "get_effective_channel_profile", mock_profile),
        patch.object(mod, "get_latency_load_snapshot", mock_snapshot),
        patch.object(mod, "apply_repair_budget", mock_budget),
        patch.object(mod, "_record_quality_metric", MagicMock()),
        patch.object(mod, "_record_budget_policy_metric", MagicMock()),
        patch.object(
            mod, "_safe_score_answer_quality", MagicMock(return_value={"score": 85, "status": "high", "reasons": []})
        ),
        patch.object(mod, "_quality_retry_improved", MagicMock(return_value=True)),
    ):
        with patch("ask_orchestrator.select_latency_budget_policy", mock_policy):
            result = await _run_quality_auto_repair(
                question="tell me top 5 stories",
                response_text="weak answer",
                model_used="gemini",
                final_meta={},
                quality_meta={"score": 30, "status": "low", "reasons": ["low coverage"]},
                context="test",
                run_retry_stream=mock_run,
            )
    assert result["retry_summary"]["attempted"] is True
