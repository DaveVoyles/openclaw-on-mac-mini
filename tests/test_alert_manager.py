"""Tests for bounded alert routing helpers."""

from alert_manager import reset_bounded_alert_cache, should_route_bounded_alert


def test_bounded_alert_deduplicates_within_cooldown():
    reset_bounded_alert_cache()
    allowed_first, reason_first = should_route_bounded_alert(
        "quality_calibration_drift",
        fingerprint="same-payload",
        cooldown_seconds=300,
        now_ts=1000.0,
    )
    allowed_second, reason_second = should_route_bounded_alert(
        "quality_calibration_drift",
        fingerprint="same-payload",
        cooldown_seconds=300,
        now_ts=1100.0,
    )

    assert allowed_first is True
    assert reason_first == "routed"
    assert allowed_second is False
    assert reason_second == "duplicate_within_cooldown"


def test_bounded_alert_allows_after_cooldown():
    reset_bounded_alert_cache()
    should_route_bounded_alert(
        "quality_calibration_drift",
        fingerprint="payload-a",
        cooldown_seconds=120,
        now_ts=1000.0,
    )
    allowed, reason = should_route_bounded_alert(
        "quality_calibration_drift",
        fingerprint="payload-a",
        cooldown_seconds=120,
        now_ts=1121.0,
    )

    assert allowed is True
    assert reason == "routed"
