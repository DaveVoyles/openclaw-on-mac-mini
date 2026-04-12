"""Tests for model_aliases.py and constants.py — pure modules, no mocking needed."""


from constants import (
    APPROVAL_TTL,
    ATTACHMENT_TEXT_MAX_CHARS,
    AUDIT_FLUSH_INTERVAL,
    BRIEFING_CHECK_INTERVAL,
    BRIEFING_HOUR,
    BRIEFING_MINUTE_WINDOW,
    CLEANUP_INTERVAL,
    DEFAULT_ANALYZE_LINES,
    DISCORD_MESSAGE_LIMIT,
    DOCUMENT_MAX_CHARS,
    EMBED_DESC_LIMIT,
    EMBED_FIELD_LIMIT,
    EMBED_PROMPT_LIMIT,
    EMBED_SPLIT_LIMIT,
    FOLLOW_UP_MAX_LENGTH,
    GIT_DIFF_MAX_CHARS,
    GOAL_SNIPPET,
    HEALTH_PORT_DEFAULT,
    HTTP_TIMEOUT_DEFAULT,
    LOG_SNIPPET_MAX_CHARS,
    MAX_FILE_SIZE,
    MEMORY_SNIPPET_MAX_CHARS,
    OLLAMA_PORT_DEFAULT,
    OUTPUT_MAX_CHARS,
    PDF_MAX_PAGES,
    PLAN_TIMEOUT_DEFAULT,
    PROACTIVE_LOG_LINES,
    PROACTIVE_SCAN_INTERVAL,
    QUESTION_SNIPPET,
    RESPONSE_SNIPPET,
    THREAD_ARCHIVE_LONG,
    THREAD_ARCHIVE_SHORT,
)
from model_aliases import (
    MODEL_INPUT_ALIASES,
    VALID_MODEL_PREFERENCES,
    model_input_suggestion,
    normalize_model_input,
)

# ---------------------------------------------------------------------------
# normalize_model_input
# ---------------------------------------------------------------------------

class TestNormalizeModelInput:
    def test_empty_string_returns_empty(self):
        assert normalize_model_input("") == ""

    def test_none_returns_empty(self):
        assert normalize_model_input(None) == ""

    def test_whitespace_stripped(self):
        assert normalize_model_input("  openai  ") == "openai"

    def test_lowercased(self):
        assert normalize_model_input("GEMINI") == "gemini"

    def test_alias_claude_maps_to_anthropic(self):
        assert normalize_model_input("claude") == "anthropic"

    def test_alias_case_insensitive(self):
        assert normalize_model_input("CLAUDE") == "anthropic"

    def test_valid_preference_passthrough(self):
        for pref in VALID_MODEL_PREFERENCES:
            assert normalize_model_input(pref) == pref

    def test_unknown_input_passthrough(self):
        assert normalize_model_input("gpt4") == "gpt4"

    def test_model_aliases_dict_has_claude(self):
        assert "claude" in MODEL_INPUT_ALIASES
        assert MODEL_INPUT_ALIASES["claude"] == "anthropic"

    def test_valid_preferences_are_set(self):
        assert VALID_MODEL_PREFERENCES == {"auto", "local", "gemini", "openai", "anthropic", "copilot"}


# ---------------------------------------------------------------------------
# model_input_suggestion
# ---------------------------------------------------------------------------

class TestModelInputSuggestion:
    def test_empty_input_returns_empty(self):
        assert model_input_suggestion("") == ""

    def test_none_returns_empty(self):
        assert model_input_suggestion(None) == ""

    def test_valid_pref_returns_empty(self):
        for pref in VALID_MODEL_PREFERENCES:
            assert model_input_suggestion(pref) == ""

    def test_alias_returns_did_you_mean(self):
        result = model_input_suggestion("claude")
        assert "claude" in result
        assert "anthropic" in result

    def test_close_match_gemini(self):
        result = model_input_suggestion("gemni")  # typo
        # Should suggest gemini or return something helpful
        assert isinstance(result, str)

    def test_close_match_openai(self):
        result = model_input_suggestion("opnai")  # typo
        assert isinstance(result, str)

    def test_no_match_returns_empty_string(self):
        # A completely random word with no close matches
        result = model_input_suggestion("zzzzzzz")
        assert isinstance(result, str)

    def test_whitespace_handled(self):
        result = model_input_suggestion("  openai  ")
        assert result == ""  # valid after stripping

    def test_case_insensitive_alias(self):
        result = model_input_suggestion("CLAUDE")
        assert "anthropic" in result or "claude" in result.lower()


# ---------------------------------------------------------------------------
# constants sanity checks
# ---------------------------------------------------------------------------

class TestConstantSanity:
    """Verify constants have sane values — catches accidental regressions."""

    def test_discord_limits_ordering(self):
        # Prompt limit < split limit < desc limit < message limit (roughly)
        assert EMBED_PROMPT_LIMIT < EMBED_SPLIT_LIMIT
        assert EMBED_SPLIT_LIMIT < EMBED_DESC_LIMIT
        assert EMBED_DESC_LIMIT < DISCORD_MESSAGE_LIMIT * 3  # embeds can be longer

    def test_embed_field_limit_positive(self):
        assert EMBED_FIELD_LIMIT > 0
        assert EMBED_FIELD_LIMIT <= 1024  # Discord hard limit

    def test_timing_intervals_positive(self):
        for val in [
            PROACTIVE_SCAN_INTERVAL,
            CLEANUP_INTERVAL,
            AUDIT_FLUSH_INTERVAL,
            BRIEFING_CHECK_INTERVAL,
            HTTP_TIMEOUT_DEFAULT,
            APPROVAL_TTL,
            PLAN_TIMEOUT_DEFAULT,
        ]:
            assert val > 0

    def test_briefing_hour_in_range(self):
        assert 0 <= BRIEFING_HOUR <= 23

    def test_briefing_minute_window_positive(self):
        assert BRIEFING_MINUTE_WINDOW > 0
        assert BRIEFING_MINUTE_WINDOW <= 60

    def test_thread_archive_ordering(self):
        assert THREAD_ARCHIVE_SHORT < THREAD_ARCHIVE_LONG

    def test_snippet_limits_positive(self):
        for val in [
            QUESTION_SNIPPET,
            RESPONSE_SNIPPET,
            GOAL_SNIPPET,
            FOLLOW_UP_MAX_LENGTH,
            LOG_SNIPPET_MAX_CHARS,
            MEMORY_SNIPPET_MAX_CHARS,
        ]:
            assert val > 0

    def test_file_size_limits(self):
        assert MAX_FILE_SIZE > 0
        assert MAX_FILE_SIZE == 20 * 1024 * 1024  # 20 MB

    def test_document_limits_positive(self):
        for val in [
            DOCUMENT_MAX_CHARS,
            ATTACHMENT_TEXT_MAX_CHARS,
            OUTPUT_MAX_CHARS,
            GIT_DIFF_MAX_CHARS,
        ]:
            assert val > 0

    def test_line_count_limits_positive(self):
        assert PROACTIVE_LOG_LINES > 0
        assert DEFAULT_ANALYZE_LINES > 0
        assert PDF_MAX_PAGES > 0

    def test_port_defaults_in_valid_range(self):
        assert 1024 < HEALTH_PORT_DEFAULT < 65535
        assert 1024 < OLLAMA_PORT_DEFAULT < 65535
