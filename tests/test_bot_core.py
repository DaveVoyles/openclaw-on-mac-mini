"""Tests for bot.py — utility functions and formatting helpers."""

import asyncio
import os
from types import SimpleNamespace

# Redirect filesystem side-effects before importing the module
os.environ.setdefault("LOG_DIR", "/tmp/_test_bot_logs")
os.environ.setdefault("AUDIT_DIR", "/tmp/_test_bot_audit")


import bot as mod
import pytest

# ---------------------------------------------------------------------------
# truncate_for_embed
# ---------------------------------------------------------------------------


class TestTruncateForEmbed:
    def test_short_text_unchanged(self):
        assert mod.truncate_for_embed("hello", limit=100) == "hello"

    def test_exact_limit_unchanged(self):
        text = "x" * 100
        assert mod.truncate_for_embed(text, limit=100) == text

    def test_over_limit_truncated(self):
        text = "a" * 200
        result = mod.truncate_for_embed(text, limit=100)
        assert len(result) <= 100
        assert result.endswith("… (truncated)")

    def test_empty_string(self):
        assert mod.truncate_for_embed("", limit=100) == ""


# ---------------------------------------------------------------------------
# _extract_image_url
# ---------------------------------------------------------------------------


class TestExtractImageUrl:
    def test_markdown_image_link(self):
        text = "Check this ![property photo](https://example.com/pic.jpg) out"
        assert mod._extract_image_url(text) == "https://example.com/pic.jpg"

    def test_bare_image_url(self):
        text = "Here is the photo https://cdn.example.com/img.png done"
        assert mod._extract_image_url(text) == "https://cdn.example.com/img.png"

    def test_no_image(self):
        assert mod._extract_image_url("just plain text") is None

    def test_bare_url_with_query_params(self):
        text = "See https://img.host/photo.webp?w=800&h=600 for details"
        url = mod._extract_image_url(text)
        assert url is not None
        assert url.startswith("https://img.host/photo.webp")


# ---------------------------------------------------------------------------
# _format_markdown_for_discord
# ---------------------------------------------------------------------------


class TestFormatMarkdownForDiscord:
    def test_h1_becomes_bold_underline(self):
        result = mod._format_markdown_for_discord("# Title")
        assert "__**Title**__" in result

    def test_h2_becomes_bold(self):
        result = mod._format_markdown_for_discord("## Section")
        assert "**Section**" in result
        assert "__" not in result

    def test_code_block_preserved(self):
        text = "```python\n# heading\nprint('hi')\n```"
        result = mod._format_markdown_for_discord(text)
        assert "# heading" in result  # not converted inside code block

    def test_plain_text_unchanged(self):
        text = "Just regular text"
        assert mod._format_markdown_for_discord(text) == text


# ---------------------------------------------------------------------------
# _split_response
# ---------------------------------------------------------------------------


class TestSplitResponse:
    def test_short_text_single_chunk(self):
        assert mod._split_response("short") == ["short"]

    def test_long_text_split(self):
        text = ("line\n" * 2000)  # well over 3800 chars
        chunks = mod._split_response(text)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= mod._EMBED_LIMIT + 1  # +1 for trailing ellipsis char

    def test_empty_string(self):
        assert mod._split_response("") == [""]


# ---------------------------------------------------------------------------
# _format_tables_for_context
# ---------------------------------------------------------------------------


class TestFormatTablesForContext:
    def test_simple_table_gets_ansi_block(self):
        table = "| Name | Status |\n|------|--------|\n| Sonarr | OK |"
        result = mod._format_tables_for_context(table)
        assert "```text" in result
        assert "```" in result

    def test_no_table_text_unchanged(self):
        text = "No tables here"
        assert mod._format_tables_for_context(text) == text


class TestAskTimeoutMessage:
    def test_timeout_message_includes_steps_and_model(self):
        message = mod._build_ask_timeout_message(
            elapsed_seconds=22,
            progress_lines=["💭 Recalling relevant memories… (1s)", "🔄 Using `search`… (2s)"],
            model_pref="auto",
            trace_id="abc123trace",
        )
        assert "Timed out after 22s" in message
        assert "model:auto" in message
        assert "abc123trace" in message
        assert "Recalling relevant memories" in message
        assert "/incident start" in message

    def test_timeout_message_handles_no_progress(self):
        message = mod._build_ask_timeout_message(
            elapsed_seconds=5,
            progress_lines=[],
            model_pref="gemini",
            trace_id="trace-2",
        )
        assert "No progress checkpoints were recorded" in message
        assert "model:gemini" in message
        assert "trace-2" in message

    def test_failure_message_includes_trace_and_category_hints(self):
        message = mod._build_ask_failure_message(
            question="help me debug this",
            model_pref="auto",
            trace_id="trace-xyz",
            category="provider",
        )
        assert "trace-xyz" in message
        assert "model:auto" in message
        assert "/incident start" in message


class TestAnswerQualityScoring:
    def test_score_answer_quality_is_deterministic(self):
        text = (
            "| Team | Result |\n"
            "| --- | --- |\n"
            "| A | W |\n"
            "| B | L |\n"
            "| C | W |\n"
            "- updated today\n"
            "Sources: https://espn.com/x https://apnews.com/y"
        )
        a = mod._score_answer_quality(text, final_meta={})
        b = mod._score_answer_quality(text, final_meta={})
        assert a == b
        assert 0 <= a["score"] <= 100
        assert a["status"] in {"high", "medium", "low"}
        assert isinstance(a["reasons"], list) and a["reasons"]

    def test_safe_score_falls_back_on_error(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("scoring-failed")

        monkeypatch.setattr(mod, "_score_answer_quality", _boom)
        result = mod._safe_score_answer_quality("hello", final_meta={}, context="ask")
        assert result["status"] == "medium"
        assert result["score"] == 50
        assert "Quality scoring unavailable" in result["reasons"][0]

    def test_score_answer_quality_penalizes_when_requested_item_target_not_met(self):
        text = "- story one\n- story two\n- story three\nSources: https://example.com/story"
        result = mod._score_answer_quality(
            text,
            final_meta={"requested_item_count": 8},
        )
        assert result["status"] == "low"
        assert result["requested_item_count"] == 8
        assert any("Requested 8 items but only 3 were included" in reason for reason in result["reasons"])


class TestResponsePackaging:
    def test_extract_requested_item_count(self):
        assert mod._extract_requested_item_count("bring in 8 stories for this week") == 8
        assert mod._extract_requested_item_count("Give me top 10 gaming headlines this weekend") == 10
        assert mod._extract_requested_item_count("5 results from today's games") == 5
        assert mod._extract_requested_item_count("division 1 lacrosse games this weekend") is None
        assert mod._extract_requested_item_count("recap please") is None

    def test_should_prefer_file_for_multichunk_response_when_requested_count_is_high(self):
        should = mod._should_prefer_file_for_multichunk_response(
            question="Bring in 8 stories for this weekend recap",
            chunks=["one", "two"],
            response_text="long enough response body",
        )
        assert should is True

    def test_should_not_prefer_file_for_single_chunk_response(self):
        should = mod._should_prefer_file_for_multichunk_response(
            question="Bring in 8 stories for this weekend recap",
            chunks=["one"],
            response_text="short",
        )
        assert should is False

    def test_should_prefer_file_for_dense_single_chunk_recap(self):
        dense = "Weekly recap\n" + "\n".join(f"- headline {idx}" for idx in range(8))
        should = mod._should_prefer_file_for_multichunk_response(
            question="weekly recap",
            chunks=[dense],
            response_text=dense,
        )
        assert should is True

    def test_build_coverage_summary_for_embed(self):
        summary = mod._build_coverage_summary_for_embed(
            {
                "answer_quality": {
                    "status": "medium",
                    "item_count": 4,
                    "requested_item_count": 8,
                }
            }
        )
        assert summary == "Coverage medium · 4/8 items"

    def test_build_coverage_summary_for_embed_includes_shortfall_hint_when_low(self):
        summary = mod._build_coverage_summary_for_embed(
            {
                "answer_quality": {
                    "status": "low",
                    "item_count": 3,
                    "requested_item_count": 8,
                }
            }
        )
        assert summary == "Coverage low · 3/8 items (short 5) · retry narrower scope"

    def test_build_coverage_summary_for_embed_includes_runtime_constrained_hint(self):
        summary = mod._build_coverage_summary_for_embed(
            {
                "answer_quality": {
                    "status": "medium",
                    "item_count": 4,
                    "requested_item_count": 8,
                },
                "answer_quality_retry": {"degrade_mode": "constrained"},
            }
        )
        assert summary == (
            "Coverage medium · 4/8 items · Runtime constrained · retry narrower scope/timeframe"
        )

    def test_build_ask_recovery_block_for_coverage_shortfall(self):
        block = mod._build_ask_recovery_block(
            {
                "answer_quality": {
                    "status": "low",
                    "item_count": 4,
                    "requested_item_count": 9,
                    "evidence_completeness": 0.42,
                }
            }
        )
        assert block is not None
        assert "Recovery note" in block
        assert "Coverage shortfall: **4/9** requested items covered." in block
        assert "missing **5** item(s)" in block
        assert "Confidence: partial" in block

    def test_build_ask_recovery_block_when_runtime_constrained_without_shortfall(self):
        block = mod._build_ask_recovery_block(
            {
                "answer_quality": {
                    "status": "high",
                    "item_count": 5,
                    "requested_item_count": 5,
                    "evidence_completeness": 0.9,
                },
                "answer_quality_retry": {"degrade_mode": "constrained"},
            }
        )
        assert block is not None
        assert "Runtime mode is constrained right now." in block
        assert "Scope hint: retry with a tighter timeframe" in block


class TestQualityAutoRepair:
    @pytest.mark.asyncio
    async def test_skipped_when_high_quality(self, monkeypatch):
        events: list[str] = []
        monkeypatch.setattr(mod, "_record_quality_metric", lambda event, context="ask": events.append(f"{context}:{event}"))

        async def _retry_stream(_prompt: str):
            raise AssertionError("retry should not run for high quality")

        result = await mod._run_quality_auto_repair(
            question="q",
            response_text="original",
            model_used="gemini",
            final_meta={},
            quality_meta={"status": "high", "score": 90, "reasons": []},
            context="ask",
            run_retry_stream=_retry_stream,
        )

        assert result["response_text"] == "original"
        summary = result["retry_summary"]
        assert summary["outcome"] == "skipped"
        assert summary["attempted"] is False
        assert summary["skip_reason"] == "high_quality"
        assert "ask:ask_quality_retry_skipped" in events

    @pytest.mark.asyncio
    async def test_skipped_when_ineligible_model_error(self, monkeypatch):
        events: list[str] = []
        monkeypatch.setattr(mod, "_record_quality_metric", lambda event, context="ask": events.append(f"{context}:{event}"))

        async def _retry_stream(_prompt: str):
            raise AssertionError("retry should not run for ineligible result")

        result = await mod._run_quality_auto_repair(
            question="q",
            response_text="original",
            model_used="error",
            final_meta={},
            quality_meta={"status": "low", "score": 10, "reasons": []},
            context="ask",
            run_retry_stream=_retry_stream,
        )

        assert result["retry_summary"]["outcome"] == "skipped"
        assert result["retry_summary"]["skip_reason"] == "ineligible"
        assert "ask:ask_quality_retry_skipped" in events

    @pytest.mark.asyncio
    async def test_retry_metadata_carries_requested_item_count_for_ask_and_message_contexts(self, monkeypatch):
        monkeypatch.setattr(mod, "_record_quality_metric", lambda *args, **kwargs: None)
        monkeypatch.setattr(mod, "_record_budget_policy_metric", lambda **kwargs: None)
        monkeypatch.setattr(mod, "get_effective_channel_profile", lambda: {"retrieval_profile": "general"})
        monkeypatch.setattr(mod, "get_latency_load_snapshot", lambda command_hint="ask": None)
        def _fake_safe_score(*args, **kwargs):
            final_meta = kwargs.get("final_meta") if isinstance(kwargs, dict) else None
            requested = None
            if isinstance(final_meta, dict) and isinstance(final_meta.get("requested_item_count"), int):
                requested = final_meta["requested_item_count"]
            return {"status": "high", "score": 90, "reasons": [], "requested_item_count": requested}

        monkeypatch.setattr(mod, "_safe_score_answer_quality", _fake_safe_score)
        monkeypatch.setattr(mod, "_quality_retry_improved", lambda **kwargs: True)

        async def _retry_stream(_prompt: str):
            return SimpleNamespace(
                response_text="| Item | Value |\n| --- | --- |\n| A | 1 |\n| B | 2 |\n| C | 3 |\n| D | 4 |\n| E | 5 |\n| F | 6 |\n| G | 7 |\n| H | 8 |\nSources: https://a.com/x https://b.com/y",
                model_used="gemini",
                final_meta={},
            )

        for context in ("ask", "ask_message_flow"):
            result = await mod._run_quality_auto_repair(
                question="Bring in 8 stories for this weekend recap",
                response_text="short",
                model_used="gemini",
                final_meta={},
                quality_meta={"status": "low", "score": 10, "reasons": ["Limited item coverage detected."]},
                context=context,
                run_retry_stream=_retry_stream,
            )
            assert result["final_meta"]["answer_quality"]["requested_item_count"] == 8
            assert result["final_meta"]["answer_quality_retry"]["requested_item_count"] == 8

    @pytest.mark.asyncio
    async def test_improved_outcome_uses_retry_response(self, monkeypatch):
        events: list[str] = []
        monkeypatch.setattr(mod, "_record_quality_metric", lambda event, context="ask": events.append(f"{context}:{event}"))
        monkeypatch.setattr(mod, "_quality_retry_improved", lambda **kwargs: True)
        monkeypatch.setattr(
            mod,
            "_safe_score_answer_quality",
            lambda *args, **kwargs: {"status": "high", "score": 92, "reasons": []},
        )

        async def _retry_stream(_prompt: str):
            return SimpleNamespace(
                response_text="improved",
                model_used="gemini",
                final_meta={"x": 1},
            )

        result = await mod._run_quality_auto_repair(
            question="q",
            response_text="original",
            model_used="gemini",
            final_meta={},
            quality_meta={"status": "low", "score": 10, "reasons": ["thin"]},
            context="ask",
            run_retry_stream=_retry_stream,
        )

        assert result["response_text"] == "improved"
        assert result["retry_summary"]["outcome"] == "improved"
        assert result["retry_summary"]["attempted"] is True
        assert "ask:ask_low_score_detected" in events
        assert "ask:ask_quality_retry_attempted" in events
        assert "ask:ask_quality_retry_improved" in events

    @pytest.mark.asyncio
    async def test_no_improvement_keeps_original_response(self, monkeypatch):
        events: list[str] = []
        monkeypatch.setattr(mod, "_record_quality_metric", lambda event, context="ask": events.append(f"{context}:{event}"))
        monkeypatch.setattr(mod, "_quality_retry_improved", lambda **kwargs: False)
        monkeypatch.setattr(
            mod,
            "_safe_score_answer_quality",
            lambda *args, **kwargs: {"status": "low", "score": 15, "reasons": []},
        )

        async def _retry_stream(_prompt: str):
            return SimpleNamespace(
                response_text="still-thin",
                model_used="gemini",
                final_meta={},
            )

        result = await mod._run_quality_auto_repair(
            question="q",
            response_text="original",
            model_used="gemini",
            final_meta={},
            quality_meta={"status": "low", "score": 12, "reasons": ["thin"]},
            context="ask",
            run_retry_stream=_retry_stream,
        )

        assert result["response_text"] == "original"
        assert result["retry_summary"]["outcome"] == "no_improvement"
        assert result["retry_summary"]["attempted"] is True
        assert "ask:ask_low_score_detected" in events
        assert "ask:ask_quality_retry_attempted" in events
        assert "ask:ask_quality_retry_no_improvement" in events

    @pytest.mark.asyncio
    async def test_failed_timeout_keeps_original_response(self, monkeypatch):
        events: list[str] = []
        monkeypatch.setattr(mod, "_record_quality_metric", lambda event, context="ask": events.append(f"{context}:{event}"))
        monkeypatch.setattr(
            mod,
            "apply_repair_budget",
            lambda **kwargs: {"max_attempts": 1, "timeout_seconds": 1},
        )

        async def _retry_stream(_prompt: str):
            await asyncio.sleep(2)
            return SimpleNamespace(
                response_text="late",
                model_used="gemini",
                final_meta={},
            )

        result = await mod._run_quality_auto_repair(
            question="q",
            response_text="original",
            model_used="gemini",
            final_meta={},
            quality_meta={"status": "low", "score": 10, "reasons": []},
            context="ask",
            run_retry_stream=_retry_stream,
        )

        assert result["response_text"] == "original"
        assert result["retry_summary"]["outcome"] == "failed"
        assert result["retry_summary"]["error"] == "timeout"
        assert "ask:ask_low_score_detected" in events
        assert "ask:ask_quality_retry_attempted" in events
        assert "ask:ask_quality_retry_failed" in events

    @pytest.mark.asyncio
    async def test_failed_error_keeps_original_response(self, monkeypatch):
        events: list[str] = []
        monkeypatch.setattr(mod, "_record_quality_metric", lambda event, context="ask": events.append(f"{context}:{event}"))

        async def _retry_stream(_prompt: str):
            raise RuntimeError("retry-failed")

        result = await mod._run_quality_auto_repair(
            question="q",
            response_text="original",
            model_used="gemini",
            final_meta={},
            quality_meta={"status": "low", "score": 10, "reasons": []},
            context="ask",
            run_retry_stream=_retry_stream,
        )

        assert result["response_text"] == "original"
        assert result["retry_summary"]["outcome"] == "failed"
        assert "retry-failed" in result["retry_summary"]["error"]
        assert "ask:ask_low_score_detected" in events
        assert "ask:ask_quality_retry_attempted" in events
        assert "ask:ask_quality_retry_failed" in events


class TestLatencyAwareBudgeting:
    def test_policy_selection_prefers_latency_under_high_load(self):
        policy = mod.select_latency_budget_policy(
            profile_name="sports",
            load_stats={"request_rate_rpm": 120.0, "p95_latency_ms": 3200.0, "error_rate": 0.02},
        )
        assert policy["profile_name"] == "sports"
        assert policy["load_tier"] == "high"
        assert policy["decision"] == "latency"
        assert policy["repair"]["allow_retry"] is False

    @pytest.mark.asyncio
    async def test_repair_budget_applied_from_profile_and_load(self, monkeypatch):
        events: list[str] = []
        monkeypatch.setattr(mod, "_record_quality_metric", lambda event, context="ask": events.append(f"{context}:{event}"))
        monkeypatch.setattr(mod, "get_effective_channel_profile", lambda: {"retrieval_profile": "sports"})
        monkeypatch.setattr(
            mod,
            "get_latency_load_snapshot",
            lambda command_hint="ask": {"request_rate_rpm": 120.0, "p95_latency_ms": 3200.0, "error_rate": 0.02},
        )

        async def _retry_stream(_prompt: str):
            raise AssertionError("retry should be disabled under high load")

        result = await mod._run_quality_auto_repair(
            question="q",
            response_text="original",
            model_used="gemini",
            final_meta={},
            quality_meta={"status": "low", "score": 10, "reasons": []},
            context="ask",
            run_retry_stream=_retry_stream,
        )

        summary = result["retry_summary"]
        assert summary["max_attempts"] == 0
        assert summary["load_tier"] == "high"
        assert summary["latency_decision"] == "latency"
        assert summary["outcome"] == "skipped"
        assert summary["skip_reason"] == "ineligible"
        assert "ask:ask_budget_decision_latency" in events

    @pytest.mark.asyncio
    async def test_repair_budget_fallback_defaults_when_metrics_missing(self, monkeypatch):
        monkeypatch.setattr(mod, "get_effective_channel_profile", lambda: {"retrieval_profile": "general"})
        monkeypatch.setattr(mod, "get_latency_load_snapshot", lambda command_hint="ask": None)

        async def _retry_stream(_prompt: str):
            return SimpleNamespace(
                response_text="retry",
                model_used="gemini",
                final_meta={},
            )

        result = await mod._run_quality_auto_repair(
            question="q",
            response_text="original",
            model_used="gemini",
            final_meta={},
            quality_meta={"status": "low", "score": 10, "reasons": []},
            context="ask",
            run_retry_stream=_retry_stream,
        )

        summary = result["retry_summary"]
        assert summary["metrics_available"] is False
        assert summary["load_tier"] == "unknown"
        assert summary["timeout_seconds"] == 14
