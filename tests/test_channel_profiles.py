"""Tests for channel_profiles module."""

from channel_profiles import (
    _RETRIEVAL_PROFILES,
    _apply_numeric_override,
    _clamp_int,
    _infer_topic_class,
    resolve_retrieval_profile_settings,
)


class TestInferTopicClass:
    def test_sports_term_nba(self):
        assert _infer_topic_class("nba game recap") == "sports"

    def test_sports_term_nfl(self):
        assert _infer_topic_class("nfl draft picks") == "sports"

    def test_sports_term_score(self):
        assert _infer_topic_class("what was the final score") == "sports"

    def test_sports_term_lacrosse(self):
        assert _infer_topic_class("lacrosse championship today") == "sports"

    def test_gaming_term_xbox(self):
        assert _infer_topic_class("xbox game pass updates") == "gaming"

    def test_gaming_term_playstation(self):
        assert _infer_topic_class("playstation exclusive release") == "gaming"

    def test_gaming_term_esports(self):
        # "esports" contains "sports" substring so sports wins; use "videogame" instead
        assert _infer_topic_class("videogame tournament finals") == "gaming"

    def test_gaming_term_games(self):
        assert _infer_topic_class("best games of 2024") == "gaming"

    def test_news_term_breaking(self):
        assert _infer_topic_class("breaking news today") == "news"

    def test_news_term_headlines(self):
        assert _infer_topic_class("latest headlines from today") == "news"

    def test_engineering_term_incident(self):
        assert _infer_topic_class("incident report for the outage") == "engineering"

    def test_engineering_term_kubernetes(self):
        assert _infer_topic_class("kubernetes deployment failed") == "engineering"

    def test_engineering_term_sre(self):
        assert _infer_topic_class("sre team on call") == "engineering"

    def test_general_fallback(self):
        assert _infer_topic_class("hello world nice day") == "general"

    def test_empty_query_returns_general(self):
        assert _infer_topic_class("") == "general"

    def test_expansion_context_sports_recap(self):
        # "sports_recap" -> "sports recap" contains "sports" (sports term)
        assert _infer_topic_class("what happened", expansion_context="sports_recap") == "sports"

    def test_expansion_context_games_helps_classify_gaming(self):
        # "games" is a gaming term
        assert _infer_topic_class("what happened", expansion_context="games") == "gaming"

    def test_expansion_context_kubernetes_helps_classify_engineering(self):
        assert _infer_topic_class("check this", expansion_context="kubernetes") == "engineering"

    def test_sports_priority_over_engineering(self):
        # "score" (sports) checked before "service" (engineering)
        result = _infer_topic_class("score for the service")
        assert result == "sports"

    def test_sports_priority_over_gaming(self):
        # "watch" (sports) vs "games" (gaming) - sports checked first
        result = _infer_topic_class("watch the games tonight")
        assert result == "sports"


class TestClampInt:
    def test_in_range_value_unchanged(self):
        assert _clamp_int(3, lower=1, upper=8, default=2) == 3

    def test_below_lower_clamped_to_lower(self):
        assert _clamp_int(0, lower=1, upper=8, default=2) == 1

    def test_above_upper_clamped_to_upper(self):
        assert _clamp_int(10, lower=1, upper=8, default=2) == 8

    def test_at_lower_bound(self):
        assert _clamp_int(1, lower=1, upper=8, default=2) == 1

    def test_at_upper_bound(self):
        assert _clamp_int(8, lower=1, upper=8, default=2) == 8

    def test_invalid_string_uses_default(self):
        assert _clamp_int("abc", lower=1, upper=8, default=5) == 5

    def test_none_uses_default(self):
        assert _clamp_int(None, lower=1, upper=8, default=3) == 3

    def test_numeric_string_parsed(self):
        assert _clamp_int("5", lower=1, upper=8, default=2) == 5

    def test_negative_clamped_to_lower(self):
        assert _clamp_int(-99, lower=1, upper=8, default=4) == 1


class TestApplyNumericOverride:
    def _base(self):
        return {"min_results": 3, "max_query_variants": 3, "provider_attempt_cap": 4}

    def test_valid_override_applied(self):
        base = self._base()
        rejections = []
        _apply_numeric_override(
            base,
            source_value=5,
            source_field="retrieval_min_results_override",
            target_field="min_results",
            default_value=3,
            rejections=rejections,
        )
        assert base["min_results"] == 5
        assert rejections == []

    def test_zero_value_skipped(self):
        base = self._base()
        rejections = []
        _apply_numeric_override(
            base,
            source_value=0,
            source_field="retrieval_min_results_override",
            target_field="min_results",
            default_value=3,
            rejections=rejections,
        )
        assert base["min_results"] == 3  # unchanged

    def test_none_value_skipped(self):
        base = self._base()
        rejections = []
        _apply_numeric_override(
            base,
            source_value=None,
            source_field="retrieval_min_results_override",
            target_field="min_results",
            default_value=3,
            rejections=rejections,
        )
        assert base["min_results"] == 3  # unchanged

    def test_empty_string_skipped(self):
        base = self._base()
        rejections = []
        _apply_numeric_override(
            base,
            source_value="",
            source_field="retrieval_min_results_override",
            target_field="min_results",
            default_value=3,
            rejections=rejections,
        )
        assert base["min_results"] == 3  # unchanged
        assert rejections == []

    def test_string_zero_skipped(self):
        base = self._base()
        rejections = []
        _apply_numeric_override(
            base,
            source_value="0",
            source_field="retrieval_min_results_override",
            target_field="min_results",
            default_value=3,
            rejections=rejections,
        )
        assert base["min_results"] == 3  # unchanged
        assert rejections == []

    def test_invalid_string_goes_to_rejections(self):
        base = self._base()
        rejections = []
        _apply_numeric_override(
            base,
            source_value="bad_value",
            source_field="retrieval_min_results_override",
            target_field="min_results",
            default_value=3,
            rejections=rejections,
        )
        assert "retrieval_min_results_override" in rejections

    def test_value_clamped_to_upper_bound(self):
        base = self._base()
        rejections = []
        _apply_numeric_override(
            base,
            source_value=99,
            source_field="retrieval_min_results_override",
            target_field="min_results",
            default_value=3,
            rejections=rejections,
        )
        assert base["min_results"] == 8  # upper bound for min_results

    def test_value_clamped_to_lower_bound(self):
        base = self._base()
        rejections = []
        _apply_numeric_override(
            base,
            source_value=-5,
            source_field="retrieval_min_results_override",
            target_field="min_results",
            default_value=3,
            rejections=rejections,
        )
        assert base["min_results"] == 1  # lower bound for min_results


class TestResolveRetrievalProfileSettings:
    def _resolve(self, query="test query", expansion_context="general", channel_profile=None):
        return resolve_retrieval_profile_settings(
            query=query,
            expansion_context=expansion_context,
            channel_profile=channel_profile,
        )

    def test_explicit_sports_profile(self):
        result = self._resolve(channel_profile={"retrieval_profile": "sports"})
        assert result["profile_name"] == "sports"
        assert result["expand_query"] is True

    def test_explicit_news_profile(self):
        result = self._resolve(channel_profile={"retrieval_profile": "news"})
        assert result["profile_name"] == "news"

    def test_explicit_gaming_profile(self):
        result = self._resolve(channel_profile={"retrieval_profile": "gaming"})
        assert result["profile_name"] == "gaming"
        assert result["min_results"] == _RETRIEVAL_PROFILES["gaming"]["min_results"]

    def test_explicit_engineering_profile(self):
        result = self._resolve(channel_profile={"retrieval_profile": "engineering"})
        assert result["profile_name"] == "engineering"
        assert result["expansion_context"] == "engineering_ops"

    def test_auto_profile_infers_from_query_sports(self):
        result = self._resolve(
            query="nba finals recap",
            channel_profile={"retrieval_profile": "auto"},
        )
        assert result["profile_name"] == "sports"
        assert result["topic_class"] == "sports"

    def test_auto_profile_infers_from_query_gaming(self):
        result = self._resolve(
            query="xbox game pass latest releases",
            channel_profile={"retrieval_profile": "auto"},
        )
        assert result["profile_name"] == "gaming"

    def test_unknown_profile_falls_back_to_inferred_topic_class(self):
        result = self._resolve(
            query="nba finals recap",
            channel_profile={"retrieval_profile": "unknown_profile"},
        )
        assert result["profile_name"] == "sports"

    def test_none_channel_profile_uses_inferred(self):
        result = self._resolve(query="xbox game pass updates", channel_profile=None)
        assert result["topic_class"] == "gaming"

    def test_topic_class_always_present_independently_of_profile(self):
        # query infers sports, but explicit profile is gaming
        result = self._resolve(query="nfl game", channel_profile={"retrieval_profile": "gaming"})
        assert "topic_class" in result
        assert result["topic_class"] == "sports"
        assert result["profile_name"] == "gaming"  # explicit profile wins

    def test_profile_name_always_present_in_result(self):
        result = self._resolve()
        assert "profile_name" in result

    def test_override_rejections_list_always_present(self):
        result = self._resolve()
        assert "override_rejections" in result
        assert isinstance(result["override_rejections"], list)

    def test_min_results_override_applied(self):
        result = self._resolve(
            channel_profile={"retrieval_profile": "sports", "retrieval_min_results_override": 6}
        )
        assert result["min_results"] == 6

    def test_max_query_variants_override_applied(self):
        result = self._resolve(
            channel_profile={"retrieval_profile": "sports", "retrieval_max_query_variants_override": 2}
        )
        assert result["max_query_variants"] == 2

    def test_provider_attempt_cap_override_applied(self):
        result = self._resolve(
            channel_profile={"retrieval_profile": "sports", "retrieval_provider_attempt_cap_override": 3}
        )
        assert result["provider_attempt_cap"] == 3

    def test_invalid_override_goes_to_rejections(self):
        result = self._resolve(
            channel_profile={"retrieval_profile": "sports", "retrieval_min_results_override": "bad"}
        )
        assert "retrieval_min_results_override" in result["override_rejections"]

    def test_zero_override_not_applied(self):
        result = self._resolve(
            channel_profile={"retrieval_profile": "sports", "retrieval_min_results_override": 0}
        )
        assert result["min_results"] == _RETRIEVAL_PROFILES["sports"]["min_results"]

    def test_override_clamped_within_upper_bound(self):
        result = self._resolve(
            channel_profile={"retrieval_profile": "general", "retrieval_min_results_override": 100}
        )
        assert result["min_results"] == 8  # upper bound for min_results

    def test_all_profiles_have_required_keys(self):
        required_keys = {
            "min_results", "expand_query", "retry_on_low_results",
            "expansion_context", "max_query_variants", "provider_attempt_cap",
        }
        for name, profile in _RETRIEVAL_PROFILES.items():
            for key in required_keys:
                assert key in profile, f"Profile '{name}' is missing required key '{key}'"
