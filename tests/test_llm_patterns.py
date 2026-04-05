"""
Tests for llm_patterns.py — query routing heuristics and response validation.
"""

import pytest

import llm_patterns as mod

# ---------------------------------------------------------------------------
# _needs_tools — live-action pattern matching
# ---------------------------------------------------------------------------


class TestNeedsTools:
    @pytest.mark.parametrize("query", [
        "restart the plex container",
        "show docker stats",
        "check the server status",
        "search zillow for houses in Narberth",
        "list folders on the NAS",
        "what's the weather today",
        "is plex running?",
        "search redfin.com for listings",
        "browse audiobooks folder",
        "run speed test",
        "https://example.com/page",
        "search for python tutorials",
        "approve request id 42",
        "send email to dave",
        "schedule a weekly backup report",
        "what's on my calendar tomorrow",
        "search my inbox for receipts",
        "is anything broken in the media stack",
    ])
    def test_tool_queries_detected(self, query):
        assert mod._needs_tools(query) is True, f"Expected True for: {query}"

    @pytest.mark.parametrize("query", [
        "what is the capital of France?",
        "explain quantum computing",
        "tell me a joke",
        "how does photosynthesis work?",
    ])
    def test_pure_knowledge_queries_not_flagged(self, query):
        assert mod._needs_tools(query) is False, f"Expected False for: {query}"


# ---------------------------------------------------------------------------
# _gemma_response_seems_valid — hallucination detection
# ---------------------------------------------------------------------------


class TestGemmaValidation:
    @pytest.mark.parametrize("reply", [
        "I'm now searching Zillow for listings...",
        "Let me check the server for you.",
        "Checking docker container status now...",
        "I don't have access to real-time data.",
        "As an AI language model, I cannot access the internet.",
        "I'll search that for you",
        "One moment while I look that up",
        "Let me start browsing",
        "I don't have real-time information",
    ])
    def test_hallucination_detected(self, reply):
        assert mod._gemma_response_seems_valid(reply) is False, f"Should reject: {reply}"

    @pytest.mark.parametrize("reply", [
        "The capital of France is Paris. It has been the capital since the 10th century.",
        "Python is a high-level programming language created by Guido van Rossum.",
        "Here are the steps to configure your router: 1. Open settings...",
    ])
    def test_genuine_response_accepted(self, reply):
        assert mod._gemma_response_seems_valid(reply) is True, f"Should accept: {reply}"

    def test_very_short_response_rejected(self):
        assert mod._gemma_response_seems_valid("ok") is False
        assert mod._gemma_response_seems_valid("   ") is False


# ---------------------------------------------------------------------------
# Vague response regex
# ---------------------------------------------------------------------------


class TestVagueResponseRegex:
    @pytest.mark.parametrize("text", [
        "I'm not sure about that.",
        "I don't have specific information on this.",
        "I couldn't find any results.",
        "I don't have access to real-time data.",
        "My training data doesn't include this.",
        "My knowledge cutoff is 2023.",
        "I recommend checking the official docs.",
        "You might want to search online for that.",
    ])
    def test_vague_patterns_match(self, text):
        assert mod._VAGUE_RESPONSE_RE.search(text), f"Should match: {text}"

    def test_normal_text_not_flagged(self):
        assert mod._VAGUE_RESPONSE_RE.search("The answer is 42.") is None


# ---------------------------------------------------------------------------
# Factual question regex
# ---------------------------------------------------------------------------


class TestFactualQuestionRegex:
    @pytest.mark.parametrize("query", [
        "Who invented the telephone?",
        "What is Docker?",
        "When was Python released?",
        "Where is the config file?",
        "How does DNS work?",
        "Is the server running?",
        "Are there any updates?",
        "Did the backup complete?",
        "Does this support Python 3.12?",
        "Can I use async here?",
    ])
    def test_factual_questions_match(self, query):
        assert mod._FACTUAL_QUESTION_RE.match(query), f"Should match: {query}"

    @pytest.mark.parametrize("query", [
        "restart the container",
        "please help me",
        "thanks for the info",
    ])
    def test_non_questions_dont_match(self, query):
        assert mod._FACTUAL_QUESTION_RE.match(query) is None, f"Should not match: {query}"
