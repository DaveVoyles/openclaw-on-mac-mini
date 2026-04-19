"""Tests for sms_ux.py — pure SMS formatting, validation, and rate limiting helpers."""

from __future__ import annotations

import time

import pytest

import sms_ux as mod
from sms_ux import (
    SMSUXError,
    UserSMSPrefs,
    _rate_limit_error,
    format_sms_error,
    mask_phone_number,
    normalize_phone_number,
    validate_sms_body,
)

# ===========================================================================
# normalize_phone_number
# ===========================================================================


class TestNormalizePhoneNumber:
    def test_valid_e164_accepted(self):
        assert normalize_phone_number("+15551234567") == "+15551234567"

    def test_sms_ux_strips_whitespace(self):
        assert normalize_phone_number("  +15551234567  ") == "+15551234567"

    def test_removes_spaces(self):
        assert normalize_phone_number("+1 555 1234567") == "+15551234567"

    def test_invalid_number_raises(self):
        with pytest.raises(SMSUXError, match="Invalid phone number"):
            normalize_phone_number("12345")

    def test_no_plus_raises(self):
        with pytest.raises(SMSUXError, match="Invalid phone number"):
            normalize_phone_number("15551234567")


# ===========================================================================
# mask_phone_number
# ===========================================================================


class TestMaskPhoneNumber:
    def test_normal_number_masked(self):
        result = mask_phone_number("+15551234567")
        assert result.startswith("+1")
        assert "••••••" in result
        assert result.endswith("67")

    def test_short_number_returns_not_set(self):
        assert mask_phone_number("12") == "not set"

    def test_empty_returns_not_set(self):
        assert mask_phone_number("") == "not set"


# ===========================================================================
# validate_sms_body
# ===========================================================================


class TestValidateSmsBody:
    def test_valid_body_returned(self):
        assert validate_sms_body("Hello!") == "Hello!"

    def test_sms_ux_strips_whitespace_v2(self):
        assert validate_sms_body("  hi  ") == "hi"

    def test_empty_raises(self):
        with pytest.raises(SMSUXError, match="empty"):
            validate_sms_body("")

    def test_whitespace_only_raises(self):
        with pytest.raises(SMSUXError, match="empty"):
            validate_sms_body("   ")

    def test_too_long_raises(self):
        long = "A" * (mod.SMS_MAX_BODY + 1)
        with pytest.raises(SMSUXError, match="too long"):
            validate_sms_body(long)

    def test_exactly_at_max_accepted(self):
        at_limit = "A" * mod.SMS_MAX_BODY
        result = validate_sms_body(at_limit)
        assert len(result) == mod.SMS_MAX_BODY


# ===========================================================================
# _rate_limit_error
# ===========================================================================


class TestRateLimitError:
    def test_no_limit_when_fresh(self):
        prefs = UserSMSPrefs(user_id=1)
        err = _rate_limit_error(prefs, time.time())
        assert err is None

    def test_cooldown_enforced(self):
        prefs = UserSMSPrefs(user_id=1)
        prefs.last_sent_at = time.time()  # just sent
        err = _rate_limit_error(prefs, time.time() + 1)
        assert err is not None
        assert "cooldown" in str(err).lower() or "⏳" in str(err)

    def test_rate_limit_when_too_many_sends(self):
        prefs = UserSMSPrefs(user_id=1)
        now = time.time()
        prefs.send_timestamps = [now - 10] * mod.SMS_RATE_MAX_SENDS
        err = _rate_limit_error(prefs, now)
        assert err is not None
        assert "rate limit" in str(err).lower() or "🚦" in str(err)

    def test_old_timestamps_pruned(self):
        prefs = UserSMSPrefs(user_id=1)
        old = time.time() - mod.SMS_RATE_WINDOW_SECONDS - 1
        prefs.send_timestamps = [old] * mod.SMS_RATE_MAX_SENDS
        err = _rate_limit_error(prefs, time.time())
        # Old timestamps get pruned, so no rate limit
        assert err is None


# ===========================================================================
# format_sms_error
# ===========================================================================


class TestFormatSmsError:
    def test_sms_ux_error_returns_message(self):
        err = SMSUXError("❌ Bad number")
        assert format_sms_error(err) == "❌ Bad number"

    def test_generic_exception_prefixed(self):
        result = format_sms_error(RuntimeError("boom"))
        assert "SMS failed" in result or "❌" in result

    def test_sms_ux_returns_string(self):
        result = format_sms_error(Exception("test"))
        assert isinstance(result, str)


# ===========================================================================
# status_snapshot
# ===========================================================================


class TestStatusSnapshot:
    def test_returns_expected_keys(self):
        from sms_ux import SMSPrefsStore, status_snapshot

        store = SMSPrefsStore()
        prefs = store.get(1)
        prefs.phone_number = "+15551234567"
        prefs.is_verified = True

        # Patch the global store
        import sms_ux

        original = sms_ux.sms_prefs
        sms_ux.sms_prefs = store
        try:
            snap = status_snapshot(1)
        finally:
            sms_ux.sms_prefs = original

        assert "phone_number" in snap
        assert "is_verified" in snap
        assert "remaining_sends" in snap
        assert "masked_phone" in snap

    def test_remaining_sends_is_int(self):
        from sms_ux import SMSPrefsStore, status_snapshot

        store = SMSPrefsStore()
        import sms_ux

        original = sms_ux.sms_prefs
        sms_ux.sms_prefs = store
        try:
            snap = status_snapshot(999)
        finally:
            sms_ux.sms_prefs = original
        assert isinstance(snap["remaining_sends"], int)
