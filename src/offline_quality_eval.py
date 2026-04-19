"""Offline replay quality evaluation harness for deterministic CI smoke checks."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_DOMAIN_RE = re.compile(r"https?://([a-z0-9.-]+)", re.IGNORECASE)
_PARTIAL_WARNING_RE = re.compile(r"partial coverage warning", re.IGNORECASE)
_EVIDENCE_DOMAIN_TOKEN_RE = re.compile(r"\b(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}\b", re.IGNORECASE)
_LATENCY_BUCKETS: tuple[tuple[float, str], ...] = (
    (400.0, "fast"),
    (1200.0, "moderate"),
    (2500.0, "slow"),
    (float("inf"), "very-slow"),
)
_LATENCY_BUCKET_ORDER = {name: idx for idx, (_, name) in enumerate(_LATENCY_BUCKETS)}

DEFAULT_THRESHOLDS: dict[str, float | str] = {
    "min_coverage_proxy": 0.78,
    "min_source_diversity_proxy": 0.67,
    "min_evidence_completeness": 0.7,
    "max_warning_rate": 0.34,
    "max_latency_bucket": "slow",
}

DEFAULT_DRIFT_TOLERANCES: dict[str, float | int] = {
    "coverage_proxy": 0.03,
    "source_diversity_proxy": 0.03,
    "evidence_completeness": 0.03,
    "unsupported_claim_rate": 0.02,
    "warning_rate": 0.03,
    "max_latency_bucket_rank": 1,
}

SEVERE_DRIFT_SCORE_THRESHOLD = 4
SEVERE_DRIFT_TOLERANCE_MULTIPLIER = 2.0
SEVERE_DRIFT_PRIORITY_METRICS = frozenset(
    {
        "coverage_proxy",
        "evidence_completeness",
        "max_latency_bucket",
    }
)

DEFAULT_RECOMMENDATION_BOUNDS: dict[str, float | int] = {
    "min_coverage_proxy": 0.05,
    "min_source_diversity_proxy": 0.05,
    "min_evidence_completeness": 0.05,
    "max_warning_rate": 0.05,
    "max_latency_bucket_rank": 1,
}


def load_replay_fixtures(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    canary_cases: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        cases = payload.get("cases")
        canary_payload = payload.get("canary_cases")
        if isinstance(canary_payload, list):
            canary_cases = [case for case in canary_payload if isinstance(case, dict)]
        elif isinstance(canary_payload, dict):
            for domain_name, domain_cases in canary_payload.items():
                if not isinstance(domain_cases, list):
                    continue
                for case in domain_cases:
                    if not isinstance(case, dict):
                        continue
                    enriched = dict(case)
                    enriched.setdefault("domain", str(domain_name))
                    tags = [str(tag) for tag in enriched.get("tags", []) if str(tag).strip()]
                    if "canary" not in {tag.lower() for tag in tags}:
                        tags.append("canary")
                    enriched["tags"] = tags
                    canary_cases.append(enriched)
    else:
        cases = payload
    if not isinstance(cases, list):
        raise ValueError("Replay fixture payload must contain a list of cases.")
    merged_cases = [case for case in cases if isinstance(case, dict)] + canary_cases
    return [dict(case) for case in merged_cases]


def latency_bucket_for_ms(latency_ms: float | int) -> str:
    value = max(0.0, float(latency_ms))
    for upper_bound, bucket in _LATENCY_BUCKETS:
        if value <= upper_bound:
            return bucket
    return "very-slow"


def evaluate_replay_case(case: dict[str, Any]) -> dict[str, Any]:
    response = str(case.get("response") or "")
    response_lc = response.lower()
    required_terms = [str(term) for term in case.get("required_terms", []) if str(term).strip()]
    domain = _resolve_case_domain(case)
    tags = sorted({str(tag).strip().lower() for tag in case.get("tags", []) if str(tag).strip()})

    matched_terms = sum(1 for term in required_terms if term.lower() in response_lc)
    coverage_proxy = 1.0 if not required_terms else round(matched_terms / len(required_terms), 3)

    domains = sorted({_normalize_domain(match.group(1)) for match in _DOMAIN_RE.finditer(response)})
    source_count = len(domains)
    expected_min_sources = max(1, int(case.get("expected_min_sources") or 1))
    source_diversity_proxy = round(min(1.0, source_count / expected_min_sources), 3)
    claim_grounding = _evaluate_claim_grounding(response)

    warning_or_partial = bool(case.get("simulated_partial_coverage")) or bool(_PARTIAL_WARNING_RE.search(response))

    latency_ms = max(0.0, float(case.get("simulated_latency_ms") or 0.0))
    latency_bucket = latency_bucket_for_ms(latency_ms)
    max_latency_bucket = str(case.get("max_latency_bucket") or DEFAULT_THRESHOLDS["max_latency_bucket"])
    latency_ok = _bucket_rank(latency_bucket) <= _bucket_rank(max_latency_bucket)
    quality_status = "pass"
    if warning_or_partial or claim_grounding["unsupported_claim_count"] > 0 or not latency_ok:
        quality_status = "review"
    if coverage_proxy < 0.5 or source_diversity_proxy < 0.5:
        quality_status = "fail"
    target_assertions = _evaluate_case_targets(
        case,
        coverage_proxy=coverage_proxy,
        source_diversity_proxy=source_diversity_proxy,
        quality_status=quality_status,
    )
    if target_assertions["available"] and not target_assertions["all_passed"]:
        quality_status = "fail"
        target_assertions = _evaluate_case_targets(
            case,
            coverage_proxy=coverage_proxy,
            source_diversity_proxy=source_diversity_proxy,
            quality_status=quality_status,
        )

    return {
        "id": str(case.get("id") or ""),
        "domain": domain,
        "tags": tags,
        "prompt": str(case.get("prompt") or ""),
        "coverage_proxy": coverage_proxy,
        "required_terms_total": len(required_terms),
        "required_terms_matched": matched_terms,
        "source_domains": domains,
        "source_count": source_count,
        "expected_min_sources": expected_min_sources,
        "source_diversity_proxy": source_diversity_proxy,
        "evidence_completeness": claim_grounding["evidence_completeness"],
        "claim_like_count": claim_grounding["claim_like_count"],
        "unsupported_claim_count": claim_grounding["unsupported_claim_count"],
        "source_fields_missing": claim_grounding["source_fields_missing"],
        "warning_or_partial": warning_or_partial,
        "simulated_latency_ms": latency_ms,
        "latency_bucket": latency_bucket,
        "max_latency_bucket": max_latency_bucket,
        "latency_ok": latency_ok,
        "quality_status": quality_status,
        "target_assertions": target_assertions,
    }


def run_quality_eval(
    cases: list[dict[str, Any]],
    *,
    thresholds: dict[str, float | str] | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_thresholds = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        effective_thresholds.update(thresholds)

    results = [evaluate_replay_case(case) for case in cases]
    sample_size = len(results)
    denominator = max(sample_size, 1)

    coverage_proxy = round(sum(item["coverage_proxy"] for item in results) / denominator, 3)
    source_diversity_proxy = round(sum(item["source_diversity_proxy"] for item in results) / denominator, 3)
    evidence_completeness = round(sum(item["evidence_completeness"] for item in results) / denominator, 3)
    warning_rate = round(sum(1 for item in results if item["warning_or_partial"]) / denominator, 3)
    unsupported_claim_rate = round(
        sum(1 for item in results if item["unsupported_claim_count"] > 0) / denominator,
        3,
    )

    bucket_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    domain_coverage_proxy: dict[str, float] = {}
    domain_source_diversity_proxy: dict[str, float] = {}
    quality_status_counts: dict[str, int] = {}
    assertion_total = 0
    assertion_passed = 0
    assertion_failures: list[dict[str, Any]] = []
    for item in results:
        bucket = str(item["latency_bucket"])
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        domain = str(item.get("domain") or "unknown")
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        domain_coverage_proxy[domain] = domain_coverage_proxy.get(domain, 0.0) + float(item["coverage_proxy"])
        domain_source_diversity_proxy[domain] = domain_source_diversity_proxy.get(domain, 0.0) + float(
            item["source_diversity_proxy"]
        )
        quality_status = str(item.get("quality_status") or "unknown")
        quality_status_counts[quality_status] = quality_status_counts.get(quality_status, 0) + 1
        assertion_payload = item.get("target_assertions")
        if isinstance(assertion_payload, dict) and bool(assertion_payload.get("available")):
            assertion_total += 1
            if bool(assertion_payload.get("all_passed")):
                assertion_passed += 1
            else:
                assertion_failures.append(
                    {
                        "id": item.get("id"),
                        "domain": domain,
                        "checks": assertion_payload.get("checks", {}),
                    }
                )
    max_observed_bucket = max(
        (str(item["latency_bucket"]) for item in results),
        key=_bucket_rank,
        default="fast",
    )

    checks = {
        "coverage_proxy": coverage_proxy >= float(effective_thresholds["min_coverage_proxy"]),
        "source_diversity_proxy": source_diversity_proxy >= float(effective_thresholds["min_source_diversity_proxy"]),
        "evidence_completeness": evidence_completeness >= float(effective_thresholds["min_evidence_completeness"]),
        "warning_rate": warning_rate <= float(effective_thresholds["max_warning_rate"]),
        "latency_bucket": _bucket_rank(max_observed_bucket)
        <= _bucket_rank(str(effective_thresholds["max_latency_bucket"])),
    }

    summary = {
        "sample_size": sample_size,
        "coverage_proxy": coverage_proxy,
        "source_diversity_proxy": source_diversity_proxy,
        "evidence_completeness": evidence_completeness,
        "unsupported_claim_rate": unsupported_claim_rate,
        "warning_rate": warning_rate,
        "max_latency_bucket": max_observed_bucket,
        "latency_bucket_counts": bucket_counts,
        "domain_counts": domain_counts,
        "domain_coverage_proxy": {
            domain: round(total / max(domain_counts.get(domain, 1), 1), 3)
            for domain, total in domain_coverage_proxy.items()
        },
        "domain_source_diversity_proxy": {
            domain: round(total / max(domain_counts.get(domain, 1), 1), 3)
            for domain, total in domain_source_diversity_proxy.items()
        },
        "quality_status_counts": quality_status_counts,
        "target_assertions": {
            "evaluated_cases": assertion_total,
            "passed_cases": assertion_passed,
            "failed_cases": max(assertion_total - assertion_passed, 0),
            "pass_rate": round(assertion_passed / max(assertion_total, 1), 3) if assertion_total else 1.0,
        },
    }
    drift_report = _build_drift_report(summary, baseline=baseline)
    recommendations = _build_threshold_recommendations(summary, effective_thresholds)
    failed_checks = sorted([check_name for check_name, passed in checks.items() if not passed])
    ci_summary = {
        "status": "pass" if all(checks.values()) else "fail",
        "failed_checks": failed_checks,
        "drift_status": str(drift_report.get("status") or "unknown"),
        "drift_regressed_metrics": drift_report.get("regressed_metrics", []),
        "drift_severity_level": str((drift_report.get("severity") or {}).get("level") or "unknown"),
        "drift_severity_score": int((drift_report.get("severity") or {}).get("score") or 0),
        "drift_severity_severe": bool((drift_report.get("severity") or {}).get("severe")),
        "target_assertion_failures": assertion_failures,
        "quality_status_counts": quality_status_counts,
    }

    return {
        "report_version": 1,
        "summary": summary,
        "ci_summary": ci_summary,
        "thresholds": effective_thresholds,
        "checks": checks,
        "pass": all(checks.values()),
        "calibration": {
            "advisory_only": True,
            "auto_apply": False,
            "drift": drift_report,
            "recommendations": recommendations,
        },
        "cases": results,
    }


def load_baseline_report(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _build_drift_report(
    current_summary: dict[str, Any],
    *,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    baseline_summary = _extract_baseline_summary(baseline)
    if not baseline_summary:
        return {
            "baseline_available": False,
            "status": "no-baseline",
            "severity": {
                "level": "unknown",
                "severe": False,
                "score": 0,
                "reasons": ["baseline unavailable"],
            },
            "metrics": {},
            "regressed_metrics": [],
            "improved_metrics": [],
        }

    metrics: dict[str, dict[str, Any]] = {}
    regressed_metrics: list[str] = []
    improved_metrics: list[str] = []

    for metric_name in (
        "coverage_proxy",
        "source_diversity_proxy",
        "evidence_completeness",
        "unsupported_claim_rate",
        "warning_rate",
    ):
        baseline_value = float(baseline_summary.get(metric_name, 0.0))
        current_value = float(current_summary.get(metric_name, 0.0))
        delta = round(current_value - baseline_value, 3)
        tolerance = float(DEFAULT_DRIFT_TOLERANCES.get(metric_name, 0.03))
        direction = "flat"
        regressed = False
        improved = False
        if metric_name in {"unsupported_claim_rate", "warning_rate"}:
            if delta > tolerance:
                direction = "worse"
                regressed = True
            elif delta < -tolerance:
                direction = "better"
                improved = True
        else:
            if delta < -tolerance:
                direction = "worse"
                regressed = True
            elif delta > tolerance:
                direction = "better"
                improved = True
        if regressed:
            regressed_metrics.append(metric_name)
        if improved:
            improved_metrics.append(metric_name)
        metrics[metric_name] = {
            "baseline": round(baseline_value, 3),
            "current": round(current_value, 3),
            "delta": delta,
            "tolerance": round(tolerance, 3),
            "direction": direction,
            "regressed": regressed,
            "improved": improved,
        }

    baseline_bucket = str(baseline_summary.get("max_latency_bucket") or "fast")
    current_bucket = str(current_summary.get("max_latency_bucket") or "fast")
    bucket_delta = _bucket_rank(current_bucket) - _bucket_rank(baseline_bucket)
    bucket_tolerance = int(DEFAULT_DRIFT_TOLERANCES.get("max_latency_bucket_rank", 1) or 1)
    bucket_direction = "flat"
    bucket_regressed = False
    bucket_improved = False
    if bucket_delta > bucket_tolerance:
        bucket_direction = "worse"
        bucket_regressed = True
        regressed_metrics.append("max_latency_bucket")
    elif bucket_delta < -bucket_tolerance:
        bucket_direction = "better"
        bucket_improved = True
        improved_metrics.append("max_latency_bucket")
    metrics["max_latency_bucket"] = {
        "baseline": baseline_bucket,
        "current": current_bucket,
        "delta_rank": int(bucket_delta),
        "tolerance_rank": bucket_tolerance,
        "direction": bucket_direction,
        "regressed": bucket_regressed,
        "improved": bucket_improved,
    }

    severity = _classify_drift_severity(
        baseline_available=True,
        status="drifted" if regressed_metrics else "stable",
        metrics=metrics,
        regressed_metrics=regressed_metrics,
    )
    return {
        "baseline_available": True,
        "status": "drifted" if regressed_metrics else "stable",
        "severity": severity,
        "metrics": metrics,
        "regressed_metrics": sorted(set(regressed_metrics)),
        "improved_metrics": sorted(set(improved_metrics)),
    }


def _build_threshold_recommendations(
    summary: dict[str, Any],
    thresholds: dict[str, float | str],
) -> dict[str, Any]:
    proposals: list[dict[str, Any]] = []
    min_threshold_metrics = (
        ("min_coverage_proxy", "coverage_proxy"),
        ("min_source_diversity_proxy", "source_diversity_proxy"),
        ("min_evidence_completeness", "evidence_completeness"),
    )
    for threshold_name, summary_name in min_threshold_metrics:
        current_threshold = float(thresholds.get(threshold_name, 0.0))
        observed_value = float(summary.get(summary_name, 0.0))
        bound = float(DEFAULT_RECOMMENDATION_BOUNDS.get(threshold_name, 0.05))
        raw_delta = observed_value - current_threshold
        bounded_delta = _clamp(raw_delta, -bound, bound)
        proposed_value = round(_clamp(current_threshold + bounded_delta, 0.0, 1.0), 3)
        proposals.append(
            {
                "threshold": threshold_name,
                "metric": summary_name,
                "current": round(current_threshold, 3),
                "observed": round(observed_value, 3),
                "proposed": proposed_value,
                "delta": round(proposed_value - current_threshold, 3),
                "bounded_delta_limit": round(bound, 3),
                "review_required": True,
                "auto_apply": False,
            }
        )

    warning_threshold = float(thresholds.get("max_warning_rate", 0.0))
    warning_observed = float(summary.get("warning_rate", 0.0))
    warning_bound = float(DEFAULT_RECOMMENDATION_BOUNDS.get("max_warning_rate", 0.05))
    warning_raw_delta = warning_observed - warning_threshold
    warning_bounded = _clamp(warning_raw_delta, -warning_bound, warning_bound)
    warning_proposed = round(_clamp(warning_threshold + warning_bounded, 0.0, 1.0), 3)
    proposals.append(
        {
            "threshold": "max_warning_rate",
            "metric": "warning_rate",
            "current": round(warning_threshold, 3),
            "observed": round(warning_observed, 3),
            "proposed": warning_proposed,
            "delta": round(warning_proposed - warning_threshold, 3),
            "bounded_delta_limit": round(warning_bound, 3),
            "review_required": True,
            "auto_apply": False,
        }
    )

    latency_current = str(thresholds.get("max_latency_bucket", "slow"))
    latency_observed = str(summary.get("max_latency_bucket", "slow"))
    latency_bound = int(DEFAULT_RECOMMENDATION_BOUNDS.get("max_latency_bucket_rank", 1) or 1)
    raw_latency_delta = _bucket_rank(latency_observed) - _bucket_rank(latency_current)
    bounded_latency_delta = int(_clamp(float(raw_latency_delta), float(-latency_bound), float(latency_bound)))
    proposed_latency_bucket = _bucket_from_rank(_bucket_rank(latency_current) + bounded_latency_delta)
    proposals.append(
        {
            "threshold": "max_latency_bucket",
            "metric": "max_latency_bucket",
            "current": latency_current,
            "observed": latency_observed,
            "proposed": proposed_latency_bucket,
            "delta_rank": bounded_latency_delta,
            "bounded_delta_limit_rank": latency_bound,
            "review_required": True,
            "auto_apply": False,
        }
    )

    return {
        "advisory_only": True,
        "auto_apply": False,
        "proposals": proposals,
    }


def _extract_baseline_summary(baseline: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(baseline, dict):
        return {}
    summary = baseline.get("summary")
    if isinstance(summary, dict):
        return summary
    if any(
        key in baseline
        for key in (
            "coverage_proxy",
            "source_diversity_proxy",
            "evidence_completeness",
            "unsupported_claim_rate",
            "warning_rate",
            "max_latency_bucket",
        )
    ):
        return baseline
    return {}


def _classify_drift_severity(
    *,
    baseline_available: bool,
    status: str,
    metrics: dict[str, dict[str, Any]],
    regressed_metrics: list[str],
) -> dict[str, Any]:
    if not baseline_available:
        return {"level": "unknown", "severe": False, "score": 0, "reasons": ["baseline unavailable"]}
    if status != "drifted" or not regressed_metrics:
        return {"level": "none", "severe": False, "score": 0, "reasons": []}

    score = 0
    reasons: list[str] = []
    regressed_set = set(regressed_metrics)
    for metric_name in sorted(regressed_set):
        metric = metrics.get(metric_name, {})
        weight = 2 if metric_name in SEVERE_DRIFT_PRIORITY_METRICS else 1
        score += weight
        if metric_name == "max_latency_bucket":
            delta_rank = abs(int(metric.get("delta_rank", 0) or 0))
            tolerance_rank = max(1, int(metric.get("tolerance_rank", 1) or 1))
            if delta_rank >= max(2, tolerance_rank * 2):
                score += 1
                reasons.append(f"{metric_name} regressed by {delta_rank} buckets")
            continue
        delta = abs(float(metric.get("delta", 0.0) or 0.0))
        tolerance = float(metric.get("tolerance", 0.03) or 0.03)
        if tolerance > 0 and delta >= (tolerance * SEVERE_DRIFT_TOLERANCE_MULTIPLIER):
            score += 1
            reasons.append(f"{metric_name} exceeded {SEVERE_DRIFT_TOLERANCE_MULTIPLIER:.0f}x tolerance")

    if {"coverage_proxy", "evidence_completeness"}.issubset(regressed_set):
        score += 1
        reasons.append("coverage and evidence completeness both regressed")

    severe = score >= SEVERE_DRIFT_SCORE_THRESHOLD
    return {
        "level": "severe" if severe else "elevated",
        "severe": severe,
        "score": int(score),
        "reasons": reasons,
    }


def _bucket_from_rank(rank: int) -> str:
    ordered = [name for _, name in _LATENCY_BUCKETS]
    bounded = max(0, min(rank, len(ordered) - 1))
    return ordered[bounded]


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _normalize_domain(domain: str) -> str:
    normalized = domain.strip().lower()
    if normalized.startswith("www."):
        return normalized[4:]
    return normalized


def _resolve_case_domain(case: dict[str, Any]) -> str:
    domain = str(case.get("domain") or "").strip().lower()
    if domain:
        return domain
    known_domains = {"gaming", "sports", "news", "engineering"}
    for tag in case.get("tags", []):
        tag_norm = str(tag).strip().lower()
        if tag_norm in known_domains:
            return tag_norm
    prompt = str(case.get("prompt") or "").lower()
    for candidate in sorted(known_domains):
        if candidate in prompt:
            return candidate
    return "unknown"


def _evaluate_case_targets(
    case: dict[str, Any],
    *,
    coverage_proxy: float,
    source_diversity_proxy: float,
    quality_status: str,
) -> dict[str, Any]:
    targets = case.get("targets")
    if not isinstance(targets, dict):
        return {"available": False, "all_passed": True, "checks": {}}

    checks: dict[str, dict[str, Any]] = {}
    if "min_coverage_proxy" in targets:
        minimum = float(targets.get("min_coverage_proxy", 0.0))
        checks["coverage_proxy"] = {
            "expected": round(minimum, 3),
            "operator": ">=",
            "actual": round(float(coverage_proxy), 3),
            "pass": float(coverage_proxy) >= minimum,
        }
    if "min_source_diversity_proxy" in targets:
        minimum = float(targets.get("min_source_diversity_proxy", 0.0))
        checks["source_diversity_proxy"] = {
            "expected": round(minimum, 3),
            "operator": ">=",
            "actual": round(float(source_diversity_proxy), 3),
            "pass": float(source_diversity_proxy) >= minimum,
        }
    if "expected_quality_status" in targets:
        expected_status = str(targets.get("expected_quality_status") or "").strip().lower()
        checks["quality_status"] = {
            "expected": expected_status,
            "operator": "==",
            "actual": str(quality_status).strip().lower(),
            "pass": str(quality_status).strip().lower() == expected_status,
        }

    all_passed = all(bool(check.get("pass")) for check in checks.values()) if checks else True
    return {"available": bool(checks), "all_passed": all_passed, "checks": checks}


def _bucket_rank(bucket: str) -> int:
    return _LATENCY_BUCKET_ORDER.get(str(bucket).strip().lower(), _LATENCY_BUCKET_ORDER["very-slow"])


def _evaluate_claim_grounding(response_text: str) -> dict[str, Any]:
    """Return deterministic evidence completeness for claim-like rows/statements."""
    lines = (response_text or "").splitlines()
    source_fields_missing = "source" not in (response_text or "").lower()
    in_sources_section = False
    global_sources_present = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith(("source:", "sources:")):
            in_sources_section = True
            source_fields_missing = False
            if _contains_evidence_token(line):
                global_sources_present = True
            continue
        if in_sources_section and line.startswith("-"):
            if _contains_evidence_token(line):
                global_sources_present = True
            continue
        if in_sources_section and not line.startswith(("-", "*")):
            in_sources_section = False

    claim_like_count = 0
    supported_claim_count = 0

    in_sources_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith(("source:", "sources:")):
            in_sources_section = True
            source_fields_missing = False
            if _contains_evidence_token(line):
                global_sources_present = True
            continue
        if in_sources_section and line.startswith("-"):
            if _contains_evidence_token(line):
                global_sources_present = True
            continue
        if in_sources_section and not line.startswith(("-", "*")):
            in_sources_section = False

        if line.startswith("|"):
            source_fields_missing = source_fields_missing and ("source" not in lowered)

        if not _is_claim_like_line(line):
            continue
        claim_like_count += 1
        if _contains_evidence_token(line) or global_sources_present:
            supported_claim_count += 1

    if claim_like_count == 0 or source_fields_missing:
        return {
            "evidence_completeness": 1.0,
            "claim_like_count": int(claim_like_count),
            "unsupported_claim_count": 0,
            "source_fields_missing": bool(source_fields_missing),
        }

    completeness = round(supported_claim_count / claim_like_count, 3)
    unsupported_claim_count = max(claim_like_count - supported_claim_count, 0)
    return {
        "evidence_completeness": completeness,
        "claim_like_count": int(claim_like_count),
        "unsupported_claim_count": int(unsupported_claim_count),
        "source_fields_missing": False,
    }


def _is_claim_like_line(line: str) -> bool:
    lowered = (line or "").lower()
    if not lowered:
        return False
    if lowered.startswith(("#", "source:", "sources:")):
        return False
    if "http://" in lowered or "https://" in lowered:
        return False
    if len(lowered.strip()) < 16:
        return False
    if re.search(r"\b\d+(?:\.\d+)?\b", lowered):
        return True
    return bool(
        re.search(
            r"\b(final|impact|timeline|mitigation|risk|follow-up|wins|loss|revenue|latency|error|incident)\b",
            lowered,
        )
    )


def _contains_evidence_token(text: str) -> bool:
    lowered = " ".join((text or "").split()).strip().lower()
    if not lowered:
        return False
    if "http://" in lowered or "https://" in lowered:
        return True
    if lowered in {"n/a", "-", "none", "unknown", "tbd"}:
        return False
    return bool(_EVIDENCE_DOMAIN_TOKEN_RE.search(lowered))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic offline quality calibration.")
    parser.add_argument("--fixtures", required=True, help="Path to replay fixture JSON.")
    parser.add_argument("--baseline", help="Optional baseline report JSON path.")
    parser.add_argument("--output", help="Optional output path for machine-readable report.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    cases = load_replay_fixtures(args.fixtures)
    baseline = load_baseline_report(args.baseline) if args.baseline else None
    result = run_quality_eval(cases, baseline=baseline)
    encoded = json.dumps(result, sort_keys=True)
    if args.output:
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
