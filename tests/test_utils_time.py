"""Tests for time utility functions."""

import pytest

from utils.time import (
    format_duration,
    format_duration_short,
    parse_duration,
    relative_time,
    seconds_until_hour,
)


class TestParseDuration:
    def test_parse_seconds(self):
        assert parse_duration("30s") == 30

    def test_parse_minutes(self):
        assert parse_duration("5m") == 300

    def test_parse_hours(self):
        assert parse_duration("2h") == 7200

    def test_parse_days(self):
        assert parse_duration("1d") == 86400

    def test_parse_combined(self):
        assert parse_duration("1h 30m") == 5400
        assert parse_duration("2d 3h 15m") == 184500  # 2*86400 + 3*3600 + 15*60

    def test_parse_with_spaces(self):
        assert parse_duration("  5 m  ") == 300

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            parse_duration("invalid")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            parse_duration("")


class TestFormatDuration:
    def test_zero_seconds(self):
        assert format_duration(0) == "0 seconds"

    def test_seconds_only(self):
        assert format_duration(45) == "45 seconds"

    def test_one_second(self):
        assert "1 second" in format_duration(1)

    def test_utils_time_minutes(self):
        assert format_duration(300) == "5 minutes"

    def test_utils_time_hours(self):
        assert format_duration(7200) == "2 hours"

    def test_utils_time_days(self):
        assert format_duration(86400) == "1 day"

    def test_combined_precision_2(self):
        result = format_duration(90, precision=2)
        assert "1 minute" in result
        assert "30 seconds" in result

    def test_combined_precision_1(self):
        result = format_duration(90, precision=1)
        assert "minute" in result
        assert "second" not in result

    def test_negative_duration(self):
        result = format_duration(-60)
        assert result.startswith("-")
        assert "1 minute" in result


class TestFormatDurationShort:
    def test_seconds(self):
        assert format_duration_short(30) == "30s"

    def test_utils_time_minutes_v2(self):
        assert format_duration_short(300) == "5m"

    def test_utils_time_hours_v2(self):
        assert format_duration_short(7200) == "2h"

    def test_combined(self):
        result = format_duration_short(5400)
        assert "1h" in result
        assert "30m" in result

    def test_max_two_units(self):
        # 2d 3h 15m should show only "2d 3h"
        result = format_duration_short(183900)
        assert "2d" in result
        assert "3h" in result
        assert "m" not in result

    def test_zero(self):
        assert format_duration_short(0) == "0s"


class TestSecondsUntilHour:
    def test_invalid_hour_raises(self):
        with pytest.raises(ValueError):
            seconds_until_hour(25)

        with pytest.raises(ValueError):
            seconds_until_hour(-1)

    def test_invalid_minute_raises(self):
        with pytest.raises(ValueError):
            seconds_until_hour(12, 60)

        with pytest.raises(ValueError):
            seconds_until_hour(12, -1)

    def test_returns_positive_seconds(self):
        # Should return a positive number of seconds
        result = seconds_until_hour(23, 59)
        assert result > 0
        assert result < 86400  # Less than 24 hours


class TestRelativeTime:
    def test_just_now(self):
        assert relative_time(30) == "just now"
        assert relative_time(59) == "just now"

    def test_minutes_ago(self):
        assert relative_time(300) == "5m ago"
        assert relative_time(120) == "2m ago"

    def test_hours_ago(self):
        assert relative_time(7200) == "2h ago"
        assert relative_time(3600) == "1h ago"

    def test_days_ago(self):
        assert relative_time(86400) == "1d ago"
        assert relative_time(172800) == "2d ago"
