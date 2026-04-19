"""Tests for the centralized answer acceptance policy (answer_policy.py)."""

from __future__ import annotations

import pytest

import answer_policy as ap

pytestmark = pytest.mark.smoke


class TestResponseSeemsValidGemma:
    """Gemma provider — tool-hallucination detection."""

    def test_answer_policy_accepts_genuine_answer(self):
        assert ap.response_seems_valid("The weather in Philadelphia is 72°F and sunny.", provider="gemma")

    def test_answer_policy_rejects_empty(self):
        assert not ap.response_seems_valid("", provider="gemma")

    def test_answer_policy_rejects_short(self):
        assert not ap.response_seems_valid("Ok", provider="gemma")

    def test_rejects_now_searching(self):
        assert not ap.response_seems_valid("I'm now searching the web for that.", provider="gemma")

    def test_rejects_let_me_check(self):
        assert not ap.response_seems_valid("Let me check that for you.", provider="gemma")

    def test_rejects_cannot_browse(self):
        assert not ap.response_seems_valid(
            "As an AI, I cannot access the internet or real-time data.", provider="gemma"
        )

    def test_rejects_dont_have_live(self):
        assert not ap.response_seems_valid("I don't have real-time access to that information.", provider="gemma")

    def test_rejects_one_moment(self):
        assert not ap.response_seems_valid("One moment while I look that up.", provider="gemma")

    def test_rejects_ill_search(self):
        assert not ap.response_seems_valid("I'll search for that right now.", provider="gemma")


class TestResponseSeemsValidRemote:
    """Remote providers (copilot, openai, anthropic, gemini)."""

    def test_answer_policy_accepts_genuine_answer_v2(self):
        assert ap.response_seems_valid("Here is the Pythagorean theorem: a² + b² = c².", provider="copilot")

    def test_answer_policy_rejects_empty_v2(self):
        assert not ap.response_seems_valid("", provider="openai")

    def test_answer_policy_rejects_short_v2(self):
        assert not ap.response_seems_valid("Sure", provider="anthropic")

    def test_rejects_one_moment_placeholder(self):
        assert not ap.response_seems_valid("One moment, I'll retrieve that for you.", provider="copilot")

    def test_rejects_let_me_retrieve(self):
        assert not ap.response_seems_valid("Let me retrieve that for you.", provider="openai")

    def test_accepts_no_access_disclaimer_from_remote(self):
        # Remote providers CAN legitimately say they lack live data — this is NOT rejected
        assert ap.response_seems_valid(
            "I don't have access to real-time sports scores, but here's what I know: …" * 3,
            provider="copilot",
        )


class TestShouldReturnDirectly:
    """Direct-return bypass gate."""

    def test_perplexity_direct_marker_triggers_bypass(self):
        result = "Here are the games for today. _via perplexity-direct_"
        assert ap.should_return_directly("generate_sports_watch_report", result)

    def test_no_marker_does_not_bypass(self):
        result = "Here are the games for today."
        assert not ap.should_return_directly("generate_sports_watch_report", result)

    def test_unknown_tool_never_bypasses(self):
        result = "Some result _via perplexity-direct_"
        assert not ap.should_return_directly("search_web", result)

    def test_empty_result_does_not_bypass(self):
        assert not ap.should_return_directly("generate_sports_watch_report", "")

    def test_none_result_does_not_bypass(self):
        assert not ap.should_return_directly("generate_sports_watch_report", None)  # type: ignore[arg-type]


class TestIsLowQuality:
    """Quality gate for Phase 28 retry logic."""

    def test_empty_string_is_low_quality(self):
        assert ap.is_low_quality("")

    def test_short_response_is_low_quality(self):
        assert ap.is_low_quality("I'm not sure.")

    def test_i_dont_know_is_low_quality(self):
        assert ap.is_low_quality("I don't know the answer to that question.")

    def test_im_unable_to_is_low_quality(self):
        assert ap.is_low_quality("I'm unable to provide that information right now.")

    def test_i_cant_help_is_low_quality(self):
        assert ap.is_low_quality("I can't help you with that request at this time.")

    def test_error_prefix_is_low_quality(self):
        assert ap.is_low_quality("❌ Something went wrong.")

    def test_warning_prefix_is_low_quality(self):
        assert ap.is_low_quality("⚠️ Rate limit reached.")

    def test_good_response_not_low_quality(self):
        long_ok = "The capital of France is Paris. It has been the capital since the late 10th century and is home to many famous landmarks including the Eiffel Tower."
        assert not ap.is_low_quality(long_ok)

    def test_substantive_response_with_dont_know_phrase_buried_is_still_low_quality(self):
        # Phrase match triggers even in a longer text
        assert ap.is_low_quality("I cannot answer this question because I lack the knowledge needed to give you a correct response today.")

    def test_none_is_low_quality(self):
        assert ap.is_low_quality(None)  # type: ignore[arg-type]
