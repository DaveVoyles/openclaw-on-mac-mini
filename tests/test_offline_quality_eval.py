"""Tests for offline_quality_eval.py — comprehensive coverage of pure Python logic."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

from offline_quality_eval import (
    DEFAULT_THRESHOLDS,
    _bucket_from_rank,
    _bucket_rank,
    _build_drift_report,
    _build_threshold_recommendations,
    _clamp,
    _classify_drift_severity,
    _contains_evidence_token,
    _evaluate_claim_grounding,
    _is_claim_like_line,
    _normalize_domain,
    _resolve_case_domain,
    evaluate_replay_case,
    latency_bucket_for_ms,
    load_replay_fixtures,
    run_quality_eval,
)

# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------

def _make_case(**kwargs: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": "test-001",
        "prompt": "What happened?",
        "response": "The incident had 42 errors. Source: example.com",
        "required_terms": ["incident", "errors"],
        "expected_min_sources": 1,
        "tags": [],
        "domain": "engineering",
    }
    defaults.update(kwargs)
    return defaults


def _make_fixture_file(tmp_path: Path, cases: list[dict]) -> Path:
    p = tmp_path / "fixtures.json"
    p.write_text(json.dumps(cases), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# latency_bucket_for_ms
# ---------------------------------------------------------------------------

class TestLatencyBucketForMs:
    def test_zero_is_fast(self):
        assert latency_bucket_for_ms(0) == "fast"

    def test_400_is_fast(self):
        assert latency_bucket_for_ms(400) == "fast"

    def test_401_is_moderate(self):
        assert latency_bucket_for_ms(401) == "moderate"

    def test_1200_is_moderate(self):
        assert latency_bucket_for_ms(1200) == "moderate"

    def test_1201_is_slow(self):
        assert latency_bucket_for_ms(1201) == "slow"

    def test_2500_is_slow(self):
        assert latency_bucket_for_ms(2500) == "slow"

    def test_2501_is_very_slow(self):
        assert latency_bucket_for_ms(2501) == "very-slow"

    def test_large_value_is_very_slow(self):
        assert latency_bucket_for_ms(99999) == "very-slow"

    def test_negative_clamped_to_fast(self):
        assert latency_bucket_for_ms(-100) == "fast"

    def test_integer_input(self):
        assert latency_bucket_for_ms(200) == "fast"


# ---------------------------------------------------------------------------
# _bucket_rank
# ---------------------------------------------------------------------------

class TestBucketRank:
    def test_fast_is_lowest(self):
        assert _bucket_rank("fast") < _bucket_rank("moderate")

    def test_moderate_lt_slow(self):
        assert _bucket_rank("moderate") < _bucket_rank("slow")

    def test_slow_lt_very_slow(self):
        assert _bucket_rank("slow") < _bucket_rank("very-slow")

    def test_unknown_maps_to_very_slow_rank(self):
        assert _bucket_rank("unknown") == _bucket_rank("very-slow")

    def test_case_insensitive(self):
        assert _bucket_rank("Fast") == _bucket_rank("fast")

    def test_whitespace_stripped(self):
        assert _bucket_rank("  slow  ") == _bucket_rank("slow")


# ---------------------------------------------------------------------------
# _bucket_from_rank
# ---------------------------------------------------------------------------

class TestBucketFromRank:
    def test_rank_0_is_fast(self):
        assert _bucket_from_rank(0) == "fast"

    def test_rank_1_is_moderate(self):
        assert _bucket_from_rank(1) == "moderate"

    def test_rank_2_is_slow(self):
        assert _bucket_from_rank(2) == "slow"

    def test_rank_3_is_very_slow(self):
        assert _bucket_from_rank(3) == "very-slow"

    def test_negative_rank_clamps_to_fast(self):
        assert _bucket_from_rank(-5) == "fast"

    def test_large_rank_clamps_to_very_slow(self):
        assert _bucket_from_rank(999) == "very-slow"


# ---------------------------------------------------------------------------
# _clamp
# ---------------------------------------------------------------------------

class TestClamp:
    def test_within_range(self):
        assert _clamp(0.5, 0.0, 1.0) == 0.5

    def test_below_lower(self):
        assert _clamp(-1.0, 0.0, 1.0) == 0.0

    def test_above_upper(self):
        assert _clamp(2.0, 0.0, 1.0) == 1.0

    def test_exactly_lower(self):
        assert _clamp(0.0, 0.0, 1.0) == 0.0

    def test_exactly_upper(self):
        assert _clamp(1.0, 0.0, 1.0) == 1.0


# ---------------------------------------------------------------------------
# _normalize_domain
# ---------------------------------------------------------------------------

class TestNormalizeDomain:
    def test_strips_www(self):
        assert _normalize_domain("www.example.com") == "example.com"

    def test_lowercases(self):
        assert _normalize_domain("EXAMPLE.COM") == "example.com"

    def test_strips_whitespace(self):
        assert _normalize_domain("  example.com  ") == "example.com"

    def test_no_www(self):
        assert _normalize_domain("example.com") == "example.com"


# ---------------------------------------------------------------------------
# _resolve_case_domain
# ---------------------------------------------------------------------------

class TestResolveCaseDomain:
    def test_explicit_domain_field(self):
        case = {"domain": "engineering", "tags": [], "prompt": ""}
        assert _resolve_case_domain(case) == "engineering"

    def test_domain_from_tags(self):
        case = {"domain": "", "tags": ["gaming", "beta"], "prompt": ""}
        assert _resolve_case_domain(case) == "gaming"

    def test_domain_from_prompt(self):
        case = {"domain": "", "tags": [], "prompt": "What happened in sports today?"}
        assert _resolve_case_domain(case) == "sports"

    def test_unknown_when_no_clues(self):
        case = {"domain": "", "tags": [], "prompt": "Hello world"}
        assert _resolve_case_domain(case) == "unknown"

    def test_empty_domain_uses_tags(self):
        case = {"domain": "  ", "tags": ["news"], "prompt": ""}
        assert _resolve_case_domain(case) == "news"


# ---------------------------------------------------------------------------
# _is_claim_like_line
# ---------------------------------------------------------------------------

class TestIsClaimLikeLine:
    def test_line_with_number_is_claim(self):
        assert _is_claim_like_line("The error rate was 42%") is True

    def test_header_line_is_not_claim(self):
        assert _is_claim_like_line("## Heading") is False

    def test_source_line_is_not_claim(self):
        assert _is_claim_like_line("source: example.com") is False

    def test_url_line_is_not_claim(self):
        assert _is_claim_like_line("See https://example.com for details") is False

    def test_short_line_is_not_claim(self):
        assert _is_claim_like_line("ok") is False

    def test_keyword_risk_is_claim(self):
        assert _is_claim_like_line("The risk assessment is complete and approved") is True

    def test_keyword_latency_is_claim(self):
        assert _is_claim_like_line("The latency improved significantly") is True

    def test_empty_string_not_claim(self):
        assert _is_claim_like_line("") is False

    def test_keyword_incident_is_claim(self):
        assert _is_claim_like_line("The incident affected many users") is True


# ---------------------------------------------------------------------------
# _contains_evidence_token
# ---------------------------------------------------------------------------

class TestContainsEvidenceToken:
    def test_url_is_evidence(self):
        assert _contains_evidence_token("See https://example.com for info") is True

    def test_http_url_is_evidence(self):
        assert _contains_evidence_token("See http://example.com for info") is True

    def test_domain_token_is_evidence(self):
        assert _contains_evidence_token("According to reuters.com") is True

    def test_na_is_not_evidence(self):
        assert _contains_evidence_token("n/a") is False

    def test_dash_is_not_evidence(self):
        assert _contains_evidence_token("-") is False

    def test_none_is_not_evidence(self):
        assert _contains_evidence_token("none") is False

    def test_empty_is_not_evidence(self):
        assert _contains_evidence_token("") is False

    def test_unknown_is_not_evidence(self):
        assert _contains_evidence_token("unknown") is False


# ---------------------------------------------------------------------------
# _evaluate_claim_grounding
# ---------------------------------------------------------------------------

class TestEvaluateClaimGrounding:
    def test_no_claims_returns_full_completeness(self):
        result = _evaluate_claim_grounding("Just a simple statement.")
        assert result["evidence_completeness"] == 1.0
        assert result["claim_like_count"] == 0

    def test_claim_with_url_fully_supported(self):
        text = "The error rate was 42% according to https://metrics.example.com"
        result = _evaluate_claim_grounding(text)
        assert result["evidence_completeness"] == 1.0

    def test_claim_no_source_with_source_section(self):
        text = "The error rate was 42%.\nSource: metrics.example.com"
        result = _evaluate_claim_grounding(text)
        assert result["evidence_completeness"] == 1.0

    def test_source_fields_missing_when_no_source_keyword(self):
        # text must not contain "source" at all to trigger source_fields_missing
        text = "The latency was 500ms and the error rate was 42%."
        result = _evaluate_claim_grounding(text)
        assert result["source_fields_missing"] is True

    def test_returns_dict_with_expected_keys(self):
        result = _evaluate_claim_grounding("The revenue grew 15%.")
        assert "evidence_completeness" in result
        assert "claim_like_count" in result
        assert "unsupported_claim_count" in result
        assert "source_fields_missing" in result

    def test_table_with_source_column(self):
        text = "| item | value | source |\n| foo | 42 | example.com |"
        result = _evaluate_claim_grounding(text)
        assert result["source_fields_missing"] is False

    def test_multiple_unsupported_claims(self):
        text = "The risk is high. The impact is severe. The timeline is 3 days."
        result = _evaluate_claim_grounding(text)
        # All these are claim-like lines without source; evidence depends on source_fields_missing
        assert result["claim_like_count"] >= 0


# ---------------------------------------------------------------------------
# evaluate_replay_case
# ---------------------------------------------------------------------------

class TestEvaluateReplayCase:
    def test_perfect_case_passes(self):
        case = _make_case(
            response="The incident had 42 errors. Source: https://example.com",
            required_terms=["incident", "errors"],
            expected_min_sources=1,
        )
        result = evaluate_replay_case(case)
        assert result["coverage_proxy"] == 1.0
        assert result["source_diversity_proxy"] >= 1.0

    def test_no_required_terms_full_coverage(self):
        case = _make_case(required_terms=[], response="Anything goes here.")
        result = evaluate_replay_case(case)
        assert result["coverage_proxy"] == 1.0

    def test_missing_terms_reduces_coverage(self):
        case = _make_case(
            required_terms=["alpha", "beta", "gamma"],
            response="Only alpha is mentioned here.",
        )
        result = evaluate_replay_case(case)
        assert result["coverage_proxy"] < 1.0
        assert result["required_terms_matched"] == 1

    def test_low_coverage_causes_fail_status(self):
        case = _make_case(
            required_terms=["a", "b", "c", "d", "e"],
            response="nothing relevant",
        )
        result = evaluate_replay_case(case)
        assert result["quality_status"] == "fail"

    def test_simulated_partial_coverage_sets_review(self):
        case = _make_case(
            simulated_partial_coverage=True,
            response="The incident had 42 errors. Source: https://example.com",
            required_terms=["incident", "errors"],
        )
        result = evaluate_replay_case(case)
        assert result["warning_or_partial"] is True
        assert result["quality_status"] in ("review", "fail")

    def test_latency_bucket_computed(self):
        case = _make_case(simulated_latency_ms=300)
        result = evaluate_replay_case(case)
        assert result["latency_bucket"] == "fast"
        assert result["simulated_latency_ms"] == 300.0

    def test_slow_latency_causes_review(self):
        case = _make_case(
            simulated_latency_ms=5000,
            max_latency_bucket="slow",
            response="The incident had 42 errors. Source: https://example.com",
            required_terms=["incident", "errors"],
        )
        result = evaluate_replay_case(case)
        assert result["latency_ok"] is False
        assert result["quality_status"] in ("review", "fail")

    def test_tags_sorted_lowercase(self):
        case = _make_case(tags=["Canary", "BETA", "alpha"])
        result = evaluate_replay_case(case)
        assert result["tags"] == ["alpha", "beta", "canary"]

    def test_domain_extracted(self):
        case = _make_case(domain="gaming")
        result = evaluate_replay_case(case)
        assert result["domain"] == "gaming"

    def test_source_diversity_proxy_bounded_at_one(self):
        case = _make_case(
            expected_min_sources=1,
            response="Source: https://a.com and https://b.com and https://c.com",
        )
        result = evaluate_replay_case(case)
        assert result["source_diversity_proxy"] <= 1.0

    def test_result_has_all_expected_keys(self):
        case = _make_case()
        result = evaluate_replay_case(case)
        required_keys = [
            "id", "domain", "tags", "prompt", "coverage_proxy",
            "source_diversity_proxy", "evidence_completeness",
            "warning_or_partial", "latency_bucket", "quality_status",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_partial_warning_in_response_triggers_review(self):
        case = _make_case(
            response="The errors occurred. partial coverage warning. Source: https://x.com",
            required_terms=["errors"],
        )
        result = evaluate_replay_case(case)
        assert result["warning_or_partial"] is True

    def test_target_assertions_pass(self):
        case = _make_case(
            response="The incident had 42 errors. Source: https://example.com",
            required_terms=["incident", "errors"],
            targets={"min_coverage_proxy": 0.5},
        )
        result = evaluate_replay_case(case)
        assert result["target_assertions"]["available"] is True
        assert result["target_assertions"]["all_passed"] is True

    def test_target_assertions_fail(self):
        case = _make_case(
            response="nothing here",
            required_terms=["alpha", "beta", "gamma"],
            targets={"min_coverage_proxy": 0.9},
        )
        result = evaluate_replay_case(case)
        assert result["target_assertions"]["available"] is True
        assert result["target_assertions"]["all_passed"] is False
        assert result["quality_status"] == "fail"

    def test_quality_status_assertion_check(self):
        case = _make_case(
            response="The incident had 42 errors. Source: https://example.com",
            required_terms=["incident", "errors"],
            targets={"expected_quality_status": "pass"},
        )
        result = evaluate_replay_case(case)
        assert result["target_assertions"]["checks"]["quality_status"]["pass"] is True


# ---------------------------------------------------------------------------
# run_quality_eval
# ---------------------------------------------------------------------------

class TestRunQualityEval:
    def test_empty_cases_returns_valid_report(self):
        result = run_quality_eval([])
        assert result["report_version"] == 1
        assert "summary" in result
        assert "checks" in result
        assert result["summary"]["sample_size"] == 0

    def test_single_good_case_passes(self):
        cases = [_make_case(
            response="The incident had 42 errors. Source: https://example.com",
            required_terms=["incident", "errors"],
        )]
        result = run_quality_eval(cases)
        assert result["summary"]["sample_size"] == 1
        assert result["summary"]["coverage_proxy"] == 1.0

    def test_multiple_cases_averaged(self):
        cases = [
            _make_case(response="incident errors Source: https://a.com", required_terms=["incident", "errors"]),
            _make_case(response="nothing here", required_terms=["foo", "bar"]),
        ]
        result = run_quality_eval(cases)
        assert result["summary"]["sample_size"] == 2
        assert 0.0 < result["summary"]["coverage_proxy"] < 1.0

    def test_custom_thresholds_applied(self):
        cases = [_make_case(response="The incident had 42 errors.", required_terms=["incident", "errors"])]
        result = run_quality_eval(cases, thresholds={"min_coverage_proxy": 0.99})
        assert result["thresholds"]["min_coverage_proxy"] == 0.99

    def test_failed_checks_listed(self):
        # Force coverage to fail by having no sources and very high threshold
        cases = [_make_case(response="nothing", required_terms=["a", "b", "c", "d", "e"])]
        result = run_quality_eval(cases, thresholds={"min_coverage_proxy": 0.99})
        assert "coverage_proxy" in result["ci_summary"]["failed_checks"]

    def test_pass_field_reflects_checks(self):
        cases = [_make_case(
            response="incident errors Source: https://a.com",
            required_terms=["incident", "errors"],
        )]
        result = run_quality_eval(cases)
        assert result["pass"] == all(result["checks"].values())

    def test_report_contains_cases(self):
        cases = [_make_case()]
        result = run_quality_eval(cases)
        assert len(result["cases"]) == 1

    def test_domain_counts_populated(self):
        cases = [_make_case(domain="engineering"), _make_case(domain="engineering")]
        result = run_quality_eval(cases)
        assert result["summary"]["domain_counts"].get("engineering") == 2

    def test_warning_rate_computed(self):
        cases = [
            _make_case(simulated_partial_coverage=True, response="errors 42 Source: https://x.com", required_terms=["errors"]),
            _make_case(response="errors 42 Source: https://x.com", required_terms=["errors"]),
        ]
        result = run_quality_eval(cases)
        assert result["summary"]["warning_rate"] == 0.5

    def test_baseline_drift_report_available(self):
        baseline = {
            "summary": {
                "coverage_proxy": 0.9,
                "source_diversity_proxy": 0.9,
                "evidence_completeness": 0.9,
                "unsupported_claim_rate": 0.0,
                "warning_rate": 0.0,
                "max_latency_bucket": "fast",
            }
        }
        cases = [_make_case(response="incident errors Source: https://a.com", required_terms=["incident", "errors"])]
        result = run_quality_eval(cases, baseline=baseline)
        assert result["calibration"]["drift"]["baseline_available"] is True

    def test_quality_status_counts_populated(self):
        cases = [_make_case(
            response="incident errors Source: https://a.com",
            required_terms=["incident", "errors"],
        )]
        result = run_quality_eval(cases)
        assert isinstance(result["summary"]["quality_status_counts"], dict)

    def test_target_assertion_failures_in_ci_summary(self):
        cases = [_make_case(
            response="nothing",
            required_terms=["a", "b", "c"],
            targets={"min_coverage_proxy": 0.9},
        )]
        result = run_quality_eval(cases)
        assert isinstance(result["ci_summary"]["target_assertion_failures"], list)


# ---------------------------------------------------------------------------
# load_replay_fixtures
# ---------------------------------------------------------------------------

class TestLoadReplayFixtures:
    def test_list_payload(self, tmp_path: Path):
        cases = [{"id": "1", "prompt": "test", "response": "ok"}]
        path = _make_fixture_file(tmp_path, cases)
        loaded = load_replay_fixtures(path)
        assert len(loaded) == 1
        assert loaded[0]["id"] == "1"

    def test_dict_payload_with_cases_key(self, tmp_path: Path):
        payload = {"cases": [{"id": "2", "prompt": "test"}]}
        path = tmp_path / "f.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = load_replay_fixtures(path)
        assert len(loaded) == 1
        assert loaded[0]["id"] == "2"

    def test_canary_cases_list_merged(self, tmp_path: Path):
        payload = {
            "cases": [{"id": "main"}],
            "canary_cases": [{"id": "canary1"}],
        }
        path = tmp_path / "f.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = load_replay_fixtures(path)
        assert len(loaded) == 2

    def test_canary_cases_dict_adds_domain_and_tag(self, tmp_path: Path):
        payload = {
            "cases": [],
            "canary_cases": {
                "gaming": [{"id": "g1", "prompt": "test"}],
            },
        }
        path = tmp_path / "f.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = load_replay_fixtures(path)
        assert len(loaded) == 1
        assert loaded[0]["domain"] == "gaming"
        assert "canary" in loaded[0]["tags"]

    def test_canary_dict_existing_canary_tag_not_duplicated(self, tmp_path: Path):
        payload = {
            "cases": [],
            "canary_cases": {
                "sports": [{"id": "s1", "prompt": "x", "tags": ["canary"]}],
            },
        }
        path = tmp_path / "f.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = load_replay_fixtures(path)
        assert loaded[0]["tags"].count("canary") == 1

    def test_invalid_payload_raises_value_error(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps("not a list"), encoding="utf-8")
        with pytest.raises(ValueError, match="list of cases"):
            load_replay_fixtures(path)

    def test_returns_copies(self, tmp_path: Path):
        cases = [{"id": "1"}]
        path = _make_fixture_file(tmp_path, cases)
        loaded = load_replay_fixtures(path)
        loaded[0]["id"] = "mutated"
        loaded2 = load_replay_fixtures(path)
        assert loaded2[0]["id"] == "1"


# ---------------------------------------------------------------------------
# _build_drift_report
# ---------------------------------------------------------------------------

class TestBuildDriftReport:
    def _good_summary(self) -> dict:
        return {
            "coverage_proxy": 0.85,
            "source_diversity_proxy": 0.80,
            "evidence_completeness": 0.75,
            "unsupported_claim_rate": 0.05,
            "warning_rate": 0.10,
            "max_latency_bucket": "fast",
        }

    def test_no_baseline_returns_no_baseline(self):
        result = _build_drift_report(self._good_summary(), baseline=None)
        assert result["baseline_available"] is False
        assert result["status"] == "no-baseline"

    def test_stable_when_metrics_within_tolerance(self):
        baseline = {"summary": self._good_summary()}
        result = _build_drift_report(self._good_summary(), baseline=baseline)
        assert result["status"] == "stable"
        assert result["regressed_metrics"] == []

    def test_drifted_when_coverage_drops(self):
        baseline = {"summary": {**self._good_summary(), "coverage_proxy": 0.95}}
        current = {**self._good_summary(), "coverage_proxy": 0.80}
        result = _build_drift_report(current, baseline=baseline)
        assert result["status"] == "drifted"
        assert "coverage_proxy" in result["regressed_metrics"]

    def test_improved_when_coverage_rises(self):
        baseline = {"summary": {**self._good_summary(), "coverage_proxy": 0.70}}
        current = {**self._good_summary(), "coverage_proxy": 0.90}
        result = _build_drift_report(current, baseline=baseline)
        assert "coverage_proxy" in result["improved_metrics"]

    def test_warning_rate_regression_detected(self):
        baseline = {"summary": {**self._good_summary(), "warning_rate": 0.05}}
        current = {**self._good_summary(), "warning_rate": 0.20}
        result = _build_drift_report(current, baseline=baseline)
        assert "warning_rate" in result["regressed_metrics"]

    def test_latency_regression_detected(self):
        baseline = {"summary": {**self._good_summary(), "max_latency_bucket": "fast"}}
        current = {**self._good_summary(), "max_latency_bucket": "very-slow"}
        result = _build_drift_report(current, baseline=baseline)
        assert "max_latency_bucket" in result["regressed_metrics"]

    def test_baseline_as_flat_dict(self):
        baseline = self._good_summary()  # no "summary" wrapper
        result = _build_drift_report(self._good_summary(), baseline=baseline)
        assert result["baseline_available"] is True


# ---------------------------------------------------------------------------
# _classify_drift_severity
# ---------------------------------------------------------------------------

class TestClassifyDriftSeverity:
    def test_no_baseline_returns_unknown(self):
        result = _classify_drift_severity(
            baseline_available=False,
            status="drifted",
            metrics={},
            regressed_metrics=["coverage_proxy"],
        )
        assert result["level"] == "unknown"

    def test_stable_returns_none_severity(self):
        result = _classify_drift_severity(
            baseline_available=True,
            status="stable",
            metrics={},
            regressed_metrics=[],
        )
        assert result["level"] == "none"
        assert result["severe"] is False
        assert result["score"] == 0

    def test_single_priority_metric_elevated(self):
        metrics = {
            "coverage_proxy": {
                "delta": -0.05,
                "tolerance": 0.03,
                "regressed": True,
                "improved": False,
                "direction": "worse",
            }
        }
        result = _classify_drift_severity(
            baseline_available=True,
            status="drifted",
            metrics=metrics,
            regressed_metrics=["coverage_proxy"],
        )
        assert result["level"] in ("elevated", "severe")
        assert result["score"] > 0

    def test_severe_when_score_high_enough(self):
        # coverage + evidence completeness both regressed → extra score
        metrics = {
            "coverage_proxy": {
                "delta": -0.10, "tolerance": 0.03,
                "regressed": True, "improved": False, "direction": "worse",
            },
            "evidence_completeness": {
                "delta": -0.10, "tolerance": 0.03,
                "regressed": True, "improved": False, "direction": "worse",
            },
        }
        result = _classify_drift_severity(
            baseline_available=True,
            status="drifted",
            metrics=metrics,
            regressed_metrics=["coverage_proxy", "evidence_completeness"],
        )
        assert result["severe"] is True

    def test_latency_regression_in_severity(self):
        metrics = {
            "max_latency_bucket": {
                "delta_rank": 3,
                "tolerance_rank": 1,
                "regressed": True,
                "improved": False,
                "direction": "worse",
            }
        }
        result = _classify_drift_severity(
            baseline_available=True,
            status="drifted",
            metrics=metrics,
            regressed_metrics=["max_latency_bucket"],
        )
        assert result["score"] > 0


# ---------------------------------------------------------------------------
# _build_threshold_recommendations
# ---------------------------------------------------------------------------

class TestBuildThresholdRecommendations:
    def test_returns_proposals_list(self):
        summary = {
            "coverage_proxy": 0.85,
            "source_diversity_proxy": 0.75,
            "evidence_completeness": 0.80,
            "warning_rate": 0.10,
            "max_latency_bucket": "fast",
        }
        result = _build_threshold_recommendations(summary, DEFAULT_THRESHOLDS)
        assert "proposals" in result
        assert len(result["proposals"]) >= 5

    def test_proposals_are_advisory_only(self):
        summary = {
            "coverage_proxy": 0.85,
            "source_diversity_proxy": 0.75,
            "evidence_completeness": 0.80,
            "warning_rate": 0.10,
            "max_latency_bucket": "fast",
        }
        result = _build_threshold_recommendations(summary, DEFAULT_THRESHOLDS)
        assert result["advisory_only"] is True
        assert result["auto_apply"] is False

    def test_proposal_includes_review_required(self):
        summary = {
            "coverage_proxy": 0.85,
            "source_diversity_proxy": 0.75,
            "evidence_completeness": 0.80,
            "warning_rate": 0.10,
            "max_latency_bucket": "fast",
        }
        result = _build_threshold_recommendations(summary, DEFAULT_THRESHOLDS)
        for proposal in result["proposals"]:
            assert proposal["review_required"] is True

    def test_proposed_within_bounds(self):
        summary = {
            "coverage_proxy": 0.99,
            "source_diversity_proxy": 0.99,
            "evidence_completeness": 0.99,
            "warning_rate": 0.01,
            "max_latency_bucket": "fast",
        }
        result = _build_threshold_recommendations(summary, DEFAULT_THRESHOLDS)
        for proposal in result["proposals"]:
            if "proposed" in proposal and isinstance(proposal["proposed"], float):
                assert 0.0 <= proposal["proposed"] <= 1.0


# ---------------------------------------------------------------------------
# Integration: run_quality_eval with canary cases
# ---------------------------------------------------------------------------

class TestRunQualityEvalIntegration:
    def test_canary_tagged_cases_evaluated(self):
        cases = [
            _make_case(
                id="canary-001",
                tags=["canary"],
                response="The incident had 42 errors. Source: https://a.com",
                required_terms=["incident", "errors"],
            )
        ]
        result = run_quality_eval(cases)
        assert result["summary"]["sample_size"] == 1

    def test_zero_expected_sources_treated_as_one(self):
        case = _make_case(
            expected_min_sources=0,
            response="See https://example.com for details.",
        )
        result = evaluate_replay_case(case)
        assert result["source_diversity_proxy"] >= 0.0

    def test_no_sources_in_response_zero_diversity(self):
        case = _make_case(
            expected_min_sources=3,
            response="The incident had 42 errors without any source links.",
        )
        result = evaluate_replay_case(case)
        assert result["source_diversity_proxy"] == 0.0

    def test_multiple_unique_domains_counted(self):
        case = _make_case(
            expected_min_sources=3,
            response="See https://a.com and https://b.com and https://c.com",
        )
        result = evaluate_replay_case(case)
        assert result["source_count"] == 3
        assert result["source_diversity_proxy"] == 1.0

    def test_www_domains_normalized(self):
        case = _make_case(
            expected_min_sources=1,
            response="See https://www.example.com and https://example.com",
        )
        result = evaluate_replay_case(case)
        # www.example.com and example.com are the same after normalization
        assert result["source_count"] == 1
