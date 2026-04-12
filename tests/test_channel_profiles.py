"""Tests for simplified channel_profiles module."""

from channel_profiles import channel_context_prefix, resolve_retrieval_profile_settings


class TestResolveRetrievalProfileSettings:
    def test_returns_dict(self):
        result = resolve_retrieval_profile_settings("any query")
        assert isinstance(result, dict)

    def test_min_results_is_three(self):
        result = resolve_retrieval_profile_settings("nba scores")
        assert result["min_results"] == 3

    def test_expand_query_is_false(self):
        result = resolve_retrieval_profile_settings("weather today")
        assert result["expand_query"] is False

    def test_topic_class_is_general(self):
        result = resolve_retrieval_profile_settings("anything")
        assert result["topic_class"] == "general"

    def test_empty_query_returns_default(self):
        result = resolve_retrieval_profile_settings("")
        assert result == {"min_results": 3, "expand_query": False, "topic_class": "general"}

    def test_channel_name_ignored(self):
        r1 = resolve_retrieval_profile_settings("query", channel_name="sports")
        r2 = resolve_retrieval_profile_settings("query", channel_name="movies")
        assert r1 == r2


class TestChannelContextPrefix:
    def test_named_channel_returns_prefix(self):
        assert channel_context_prefix("movies") == "Channel: #movies\n"

    def test_sports_channel_returns_prefix(self):
        assert channel_context_prefix("wwe") == "Channel: #wwe\n"

    def test_empty_string_returns_empty(self):
        assert channel_context_prefix("") == ""

    def test_general_channel_returns_empty(self):
        assert channel_context_prefix("general") == ""

    def test_prefix_ends_with_newline(self):
        result = channel_context_prefix("lacrosse")
        assert result.endswith("\n")

    def test_prefix_contains_hash(self):
        result = channel_context_prefix("real-estate")
        assert "#real-estate" in result
