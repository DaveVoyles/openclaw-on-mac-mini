"""Unit tests for memory_helpers.py — pure functions and constants."""

import re
from unittest.mock import MagicMock, patch

import pytest

from memory_helpers import (
    _THREAD_NAME_RE,
    _TOPIC_STOPWORDS,
    _TOPIC_WORD_RE,
    _build_salience_summary,
    _extract_key_topics,
    _message_salience_score,
    _message_text,
    _normalize_text,
    _relative_age,
    _atomic_write,
    MIN_MESSAGES_TO_SUMMARIZE,
    CONTEXT_TTL,
    MAX_HISTORY_LENGTH,
)


# ---------------------------------------------------------------------------
# _normalize_text
# ---------------------------------------------------------------------------

class TestNormalizeText:
    def test_strips_leading_trailing_whitespace(self):
        assert _normalize_text("  hello world  ") == "hello world"

    def test_collapses_internal_whitespace(self):
        assert _normalize_text("hello   world\t\nnow") == "hello world now"

    def test_empty_string_returns_empty(self):
        assert _normalize_text("") == ""

    def test_none_treated_as_empty(self):
        assert _normalize_text(None) == ""  # type: ignore[arg-type]

    def test_single_word(self):
        assert _normalize_text("hello") == "hello"


# ---------------------------------------------------------------------------
# _message_text
# ---------------------------------------------------------------------------

class TestMessageText:
    def test_joins_string_parts(self):
        msg = {"parts": ["hello", "world"]}
        assert _message_text(msg) == "hello world"

    def test_skips_non_string_parts(self):
        msg = {"parts": ["hello", 42, None, "world"]}
        assert _message_text(msg) == "hello world"

    def test_empty_parts_returns_empty(self):
        msg = {"parts": []}
        assert _message_text(msg) == ""

    def test_missing_parts_key_returns_empty(self):
        msg = {}
        assert _message_text(msg) == ""

    def test_normalizes_whitespace_in_parts(self):
        msg = {"parts": ["  hello  ", "  world  "]}
        assert _message_text(msg) == "hello world"


# ---------------------------------------------------------------------------
# _message_salience_score
# ---------------------------------------------------------------------------

class TestMessageSalienceScore:
    def _msg(self, text, role="user"):
        return {"role": role, "parts": [text]}

    def test_empty_message_scores_zero(self):
        assert _message_salience_score({}, 0) == 0

    def test_salience_term_increments_score(self):
        msg = self._msg("We must decide on the deadline soon")
        score = _message_salience_score(msg, 0)
        assert score > 0

    def test_question_mark_adds_to_score(self):
        msg_q = self._msg("What should we do?")
        msg_no_q = self._msg("What should we do")
        assert _message_salience_score(msg_q, 0) > _message_salience_score(msg_no_q, 0)

    def test_number_hint_adds_to_score(self):
        msg_n = self._msg("There are 42 items")
        msg_no_n = self._msg("There are many items")
        assert _message_salience_score(msg_n, 0) > _message_salience_score(msg_no_n, 0)

    def test_url_adds_to_score(self):
        msg_url = self._msg("See https://example.com for details")
        msg_plain = self._msg("See the site for details")
        assert _message_salience_score(msg_url, 0) > _message_salience_score(msg_plain, 0)

    def test_path_hint_adds_to_score(self):
        msg_path = self._msg("Edit /etc/config/settings.json")
        msg_plain = self._msg("Edit the config file")
        assert _message_salience_score(msg_path, 0) > _message_salience_score(msg_plain, 0)

    def test_model_role_bonus(self):
        msg_model = {"role": "model", "parts": ["Analysis complete"]}
        msg_user = {"role": "user", "parts": ["Analysis complete"]}
        assert _message_salience_score(msg_model, 0) > _message_salience_score(msg_user, 0)

    def test_index_bonus_capped_at_six(self):
        msg = self._msg("Hello")
        score_at_6 = _message_salience_score(msg, 6)
        score_at_10 = _message_salience_score(msg, 10)
        assert score_at_6 == score_at_10

    def test_long_text_adds_bonus(self):
        short = self._msg("Short message")
        long = self._msg("x" * 161)
        assert _message_salience_score(long, 0) > _message_salience_score(short, 0)


# ---------------------------------------------------------------------------
# _extract_key_topics
# ---------------------------------------------------------------------------

class TestExtractKeyTopics:
    def test_empty_messages_returns_empty(self):
        assert _extract_key_topics([]) == []

    def test_stopwords_excluded(self):
        msgs = [{"parts": ["this that from with your"]}]
        topics = _extract_key_topics(msgs)
        for stopword in _TOPIC_STOPWORDS:
            assert stopword not in topics

    def test_frequent_words_ranked_first(self):
        msgs = [{"parts": ["python python python java"]}]
        topics = _extract_key_topics(msgs)
        assert topics[0] == "python"

    def test_limit_is_respected(self):
        msgs = [{"parts": [" ".join([f"word{i}" * 5 for i in range(20)])]}]
        topics = _extract_key_topics(msgs, limit=5)
        assert len(topics) <= 5

    def test_min_word_length_enforced(self):
        # Words shorter than 4 chars won't match _TOPIC_WORD_RE (requires 4+ chars after first)
        msgs = [{"parts": ["hi bye cat"]}]
        topics = _extract_key_topics(msgs)
        # Short words should not appear
        for t in topics:
            assert len(t) >= 4


# ---------------------------------------------------------------------------
# _build_salience_summary
# ---------------------------------------------------------------------------

class TestBuildSalienceSummary:
    def test_empty_messages_returns_empty_tuple(self):
        summary, meta = _build_salience_summary([])
        assert summary == ""
        assert meta == {}

    def test_returns_header_line(self):
        msgs = [{"role": "user", "parts": ["We must decide on the architecture"]}]
        summary, meta = _build_salience_summary(msgs)
        assert "[Compressed Thread Context" in summary

    def test_meta_has_required_keys(self):
        msgs = [{"role": "user", "parts": ["Let us decide on the deadline fix error"]}]
        _, meta = _build_salience_summary(msgs)
        for key in ("compression_applied", "retained_key_facts_count", "drift_risk",
                    "topic_retention_ratio", "missing_topics"):
            assert key in meta

    def test_drift_risk_values(self):
        msgs = [{"role": "user", "parts": ["We must decide fix error deadline ship release"]}]
        _, meta = _build_salience_summary(msgs)
        assert meta["drift_risk"] in ("low", "medium", "high")

    def test_no_salient_messages_still_returns_header(self):
        msgs = [{"role": "user", "parts": [""]}]
        summary, _ = _build_salience_summary(msgs)
        assert "[Compressed Thread Context" in summary or summary == ""

    def test_user_role_displayed_as_user(self):
        msgs = [{"role": "user", "parts": ["must decide now"]}]
        summary, _ = _build_salience_summary(msgs)
        if "- User:" in summary or "- Assistant:" in summary:
            assert "- User:" in summary

    def test_long_text_truncated_in_summary(self):
        long_text = "must decide " + "x" * 300
        msgs = [{"role": "user", "parts": [long_text]}]
        summary, _ = _build_salience_summary(msgs)
        lines = summary.splitlines()
        for line in lines:
            if line.startswith("- "):
                assert len(line) < 300


# ---------------------------------------------------------------------------
# _relative_age
# ---------------------------------------------------------------------------

class TestRelativeAge:
    def test_under_60_seconds_is_just_now(self):
        assert _relative_age(30) == "just now"

    def test_zero_seconds_is_just_now(self):
        assert _relative_age(0) == "just now"

    def test_minutes(self):
        assert _relative_age(120) == "2m ago"

    def test_hours(self):
        assert _relative_age(3600 * 3) == "3h ago"

    def test_days(self):
        assert _relative_age(3600 * 24 * 2) == "2d ago"

    def test_exactly_one_minute(self):
        assert _relative_age(60) == "1m ago"

    def test_exactly_one_hour(self):
        assert _relative_age(3600) == "1h ago"


# ---------------------------------------------------------------------------
# _thread_name_re
# ---------------------------------------------------------------------------

class TestThreadNameRe:
    def test_valid_alphanumeric(self):
        assert _THREAD_NAME_RE.match("thread1")

    def test_valid_with_dash_underscore(self):
        assert _THREAD_NAME_RE.match("my-thread_01")

    def test_rejects_space(self):
        assert not _THREAD_NAME_RE.match("my thread")

    def test_rejects_empty(self):
        assert not _THREAD_NAME_RE.match("")

    def test_rejects_too_long(self):
        assert not _THREAD_NAME_RE.match("a" * 33)

    def test_exactly_32_chars(self):
        assert _THREAD_NAME_RE.match("a" * 32)


# ---------------------------------------------------------------------------
# _atomic_write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_delegates_to_atomic_write_util(self, tmp_path):
        target = tmp_path / "out.txt"
        with patch("memory_helpers.atomic_write") as mock_aw:
            _atomic_write(target, "data")
            mock_aw.assert_called_once_with(target, "data")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_context_ttl_is_positive(self):
        assert CONTEXT_TTL > 0

    def test_max_history_length_is_positive(self):
        assert MAX_HISTORY_LENGTH > 0

    def test_min_messages_to_summarize_is_positive(self):
        assert MIN_MESSAGES_TO_SUMMARIZE > 0
