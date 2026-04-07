"""Offline replay eval harness tests."""

from __future__ import annotations

import json
from pathlib import Path

from offline_quality_eval import DEFAULT_RECOMMENDATION_BOUNDS, load_replay_fixtures, run_quality_eval

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "replay_prompts.json"


def test_run_quality_eval_returns_machine_readable_result():
    cases = load_replay_fixtures(FIXTURE_PATH)
    result = run_quality_eval(cases)

    assert result["pass"] is True
    assert result["summary"]["sample_size"] == 4
    assert result["summary"]["coverage_proxy"] == 1.0
    assert result["summary"]["source_diversity_proxy"] == 0.875
    assert result["summary"]["evidence_completeness"] == 1.0
    assert result["summary"]["unsupported_claim_rate"] == 0.0
    assert result["summary"]["warning_rate"] == 0.25
    assert result["summary"]["max_latency_bucket"] == "slow"
    assert result["summary"]["domain_counts"] == {
        "sports": 1,
        "engineering": 1,
        "news": 1,
        "gaming": 1,
    }
    assert result["summary"]["quality_status_counts"] == {"pass": 3, "review": 1}
    assert result["summary"]["target_assertions"]["evaluated_cases"] == 4
    assert result["summary"]["target_assertions"]["pass_rate"] == 1.0
    assert result["checks"]["coverage_proxy"] is True
    assert result["checks"]["source_diversity_proxy"] is True
    assert result["checks"]["evidence_completeness"] is True
    assert result["checks"]["warning_rate"] is True
    assert result["checks"]["latency_bucket"] is True
    assert result["ci_summary"]["drift_status"] == "no-baseline"
    assert result["ci_summary"]["drift_severity_level"] == "unknown"
    assert result["ci_summary"]["drift_severity_score"] == 0
    assert result["ci_summary"]["target_assertion_failures"] == []
    assert result["calibration"]["advisory_only"] is True
    assert result["calibration"]["auto_apply"] is False

    encoded = json.dumps(result)
    decoded = json.loads(encoded)
    assert decoded["summary"]["sample_size"] == 4


def test_run_quality_eval_flags_regression_when_thresholds_breached():
    cases = load_replay_fixtures(FIXTURE_PATH)
    regressed = [dict(case) for case in cases]
    regressed[0]["response"] = "Short response with no supporting detail."
    regressed[0]["simulated_latency_ms"] = 5000
    regressed[1]["response"] = "⚠️ partial coverage warning and no source links."
    regressed[1]["simulated_latency_ms"] = 4200

    result = run_quality_eval(regressed)

    assert result["pass"] is False
    assert result["checks"]["coverage_proxy"] is False
    assert result["checks"]["warning_rate"] is False
    assert result["checks"]["latency_bucket"] is False


def test_load_replay_fixtures_supports_domain_tagged_canary_payloads(tmp_path: Path):
    payload_path = tmp_path / "domain_canary.json"
    payload_path.write_text(
        json.dumps(
            {
                "cases": [],
                "canary_cases": {
                    "gaming": [
                        {
                            "id": "g-1",
                            "prompt": "gaming prompt",
                            "response": "Sources:\n- https://example.com",
                            "required_terms": [],
                        }
                    ],
                    "sports": [
                        {
                            "id": "s-1",
                            "prompt": "sports prompt",
                            "response": "Sources:\n- https://example.org",
                            "required_terms": [],
                            "tags": ["production-replay"],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    cases = load_replay_fixtures(payload_path)
    assert len(cases) == 2
    by_id = {case["id"]: case for case in cases}
    assert by_id["g-1"]["domain"] == "gaming"
    assert "canary" in [tag.lower() for tag in by_id["g-1"]["tags"]]
    assert by_id["s-1"]["domain"] == "sports"
    assert "canary" in [tag.lower() for tag in by_id["s-1"]["tags"]]


def test_run_quality_eval_penalizes_unsupported_claims():
    cases = [
        {
            "id": "unsupported-claims",
            "prompt": "recap",
            "response": (
                "## Recap\n"
                "| Item | Value | Sources |\n"
                "| --- | --- | --- |\n"
                "| Revenue | $42M | N/A |\n"
                "| Error rate | 12% | none |\n"
            ),
            "required_terms": ["Recap"],
            "expected_min_sources": 1,
        },
    ]
    result = run_quality_eval(cases)
    assert result["summary"]["evidence_completeness"] == 0.0
    assert result["summary"]["unsupported_claim_rate"] == 1.0
    assert result["checks"]["evidence_completeness"] is False


def test_run_quality_eval_no_false_penalty_when_sources_are_present():
    cases = [
        {
            "id": "grounded-claims",
            "prompt": "recap",
            "response": (
                "## Recap\n"
                "| Item | Value | Sources |\n"
                "| --- | --- | --- |\n"
                "| Revenue | $42M | https://example.com/revenue |\n"
                "| Error rate | 12% | status.example.com |\n"
            ),
            "required_terms": ["Recap"],
            "expected_min_sources": 1,
        },
    ]
    result = run_quality_eval(cases)
    assert result["summary"]["evidence_completeness"] == 1.0
    assert result["summary"]["unsupported_claim_rate"] == 0.0
    assert result["checks"]["evidence_completeness"] is True


def test_run_quality_eval_generates_drift_report_against_baseline():
    cases = load_replay_fixtures(FIXTURE_PATH)
    baseline = run_quality_eval(cases)
    regressed = [dict(case) for case in cases]
    regressed[0]["response"] = "Short answer with no supporting detail."
    regressed[0]["simulated_latency_ms"] = 5100
    result = run_quality_eval(regressed, baseline=baseline)

    drift = result["calibration"]["drift"]
    assert drift["baseline_available"] is True
    assert drift["status"] == "drifted"
    assert "coverage_proxy" in drift["regressed_metrics"]
    assert drift["metrics"]["coverage_proxy"]["regressed"] is True
    assert drift["metrics"]["coverage_proxy"]["direction"] == "worse"
    assert drift["severity"]["level"] in {"elevated", "severe"}
    assert drift["severity"]["score"] >= 1
    assert result["ci_summary"]["drift_status"] == "drifted"
    assert result["ci_summary"]["drift_severity_level"] in {"elevated", "severe"}
    assert result["ci_summary"]["drift_severity_score"] >= 1


def test_run_quality_eval_maps_severe_drift_deterministically():
    cases = load_replay_fixtures(FIXTURE_PATH)
    baseline = run_quality_eval(cases)
    regressed = [dict(case) for case in cases]
    for case in regressed:
        case["response"] = "partial coverage warning with no grounded evidence."
        case["simulated_latency_ms"] = 6200
    result = run_quality_eval(regressed, baseline=baseline)

    drift = result["calibration"]["drift"]
    assert drift["status"] == "drifted"
    assert drift["severity"]["level"] == "severe"
    assert drift["severity"]["severe"] is True
    assert drift["severity"]["score"] >= 4


def test_run_quality_eval_recommendations_are_bounded_and_review_only():
    cases = load_replay_fixtures(FIXTURE_PATH)
    regressed = [dict(case) for case in cases]
    for case in regressed:
        case["response"] = "⚠️ partial coverage warning."
        case["simulated_latency_ms"] = 5000
    result = run_quality_eval(regressed)

    recommendations = result["calibration"]["recommendations"]
    assert recommendations["advisory_only"] is True
    assert recommendations["auto_apply"] is False
    assert recommendations["proposals"]
    for proposal in recommendations["proposals"]:
        assert proposal["review_required"] is True
        assert proposal["auto_apply"] is False
        if proposal["threshold"] == "max_latency_bucket":
            assert abs(int(proposal["delta_rank"])) <= int(DEFAULT_RECOMMENDATION_BOUNDS["max_latency_bucket_rank"])
        else:
            assert abs(float(proposal["delta"])) <= float(proposal["bounded_delta_limit"])
