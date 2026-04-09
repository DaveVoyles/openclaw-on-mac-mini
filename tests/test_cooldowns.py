"""Tests for per-user command cooldown tracking."""
import time

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cooldowns import check_cooldown, reset_cooldown


@pytest.fixture(autouse=True)
def clean_cooldowns():
    """Reset cooldowns under test so tests are isolated."""
    yield
    reset_cooldown("ask", 1001)
    reset_cooldown("ask", 1002)
    reset_cooldown("recap", 1001)
    reset_cooldown("research", 1001)


def test_first_call_is_allowed():
    """First invocation should always be allowed (returns 0.0)."""
    remaining = check_cooldown("ask", 1001, cooldown_seconds=5.0)
    assert remaining == 0.0


def test_immediate_second_call_is_blocked():
    """Second call right after the first should be rate-limited."""
    check_cooldown("ask", 1001, cooldown_seconds=5.0)
    remaining = check_cooldown("ask", 1001, cooldown_seconds=5.0)
    assert remaining > 0.0
    assert remaining <= 5.0


def test_cooldown_expires():
    """After the cooldown period, the user should be allowed again."""
    check_cooldown("ask", 1001, cooldown_seconds=0.05)  # 50ms cooldown
    time.sleep(0.1)  # wait longer than the cooldown
    remaining = check_cooldown("ask", 1001, cooldown_seconds=0.05)
    assert remaining == 0.0


def test_different_users_have_independent_cooldowns():
    """One user being on cooldown should not affect another user."""
    check_cooldown("ask", 1001, cooldown_seconds=5.0)
    # User 1002 has never used the command
    remaining = check_cooldown("ask", 1002, cooldown_seconds=5.0)
    assert remaining == 0.0


def test_different_commands_have_independent_cooldowns():
    """Cooldowns are tracked per-command, not globally per-user."""
    check_cooldown("ask", 1001, cooldown_seconds=5.0)
    # Same user, different command
    remaining = check_cooldown("recap", 1001, cooldown_seconds=30.0)
    assert remaining == 0.0
