"""Tests for plugin version compatibility checking in plugin_registry."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from plugin_system.plugin_registry import (
    _check_plugin_version_compat,
    _is_valid_semver,
    _parse_version,
    _version_at_most,
    _version_satisfies,
)


def test_parse_version_basic():
    assert _parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_with_v_prefix():
    assert _parse_version("v1.2.3") == (1, 2, 3)


def test_parse_version_with_prerelease():
    assert _parse_version("1.2.3-beta.1") == (1, 2, 3)


def test_version_satisfies_equal():
    assert _version_satisfies("1.0.0", "1.0.0") is True


def test_version_satisfies_newer():
    assert _version_satisfies("2.0.0", "1.0.0") is True


def test_version_satisfies_older():
    assert _version_satisfies("0.9.0", "1.0.0") is False


def test_version_at_most_equal():
    assert _version_at_most("1.0.0", "1.0.0") is True


def test_version_at_most_older():
    assert _version_at_most("0.9.0", "1.0.0") is True


def test_version_at_most_newer():
    assert _version_at_most("2.0.0", "1.0.0") is False


def test_is_valid_semver_valid():
    assert _is_valid_semver("1.0.0") is True
    assert _is_valid_semver("v2.3.4") is True
    assert _is_valid_semver("1.0.0-beta.1") is True


def test_is_valid_semver_invalid():
    assert _is_valid_semver("not-a-version") is False
    assert _is_valid_semver("1.x.0") is False


def test_check_compat_no_version_reqs():
    """Plugin with no version requirements → no warnings."""
    warnings = _check_plugin_version_compat("test-plugin", {"name": "test"})
    assert warnings == []


def test_check_compat_satisfied_min():
    """Plugin min version satisfied → no warnings."""
    warnings = _check_plugin_version_compat("test-plugin", {"min_openclaw_version": "0.1.0"})
    assert warnings == []


def test_check_compat_unsatisfied_min():
    """Plugin requires version far in future → warning."""
    warnings = _check_plugin_version_compat("test-plugin", {"min_openclaw_version": "99.0.0"})
    assert len(warnings) == 1
    assert "requires OpenClaw" in warnings[0]


def test_check_compat_unsatisfied_min_host_version_alias():
    """Plugin using min_host_version alias → warning when unsatisfied."""
    warnings = _check_plugin_version_compat("test-plugin", {"min_host_version": "99.0.0"})
    assert len(warnings) == 1
    assert "requires OpenClaw" in warnings[0]


def test_check_compat_max_version_exceeded():
    """Plugin max version lower than host → warning."""
    warnings = _check_plugin_version_compat("test-plugin", {"max_openclaw_version": "0.0.1"})
    assert len(warnings) == 1
    assert "Compatibility not guaranteed" in warnings[0]


def test_check_compat_max_version_ok():
    """Plugin max version at or above host → no warning."""
    warnings = _check_plugin_version_compat("test-plugin", {"max_openclaw_version": "99.0.0"})
    assert warnings == []


def test_check_compat_invalid_plugin_version():
    """Plugin with invalid version string → warning."""
    warnings = _check_plugin_version_compat("test-plugin", {"version": "not-semver-at-all"})
    assert any("valid semver" in w for w in warnings)


def test_check_compat_valid_plugin_version():
    """Plugin with valid version string → no semver warning."""
    warnings = _check_plugin_version_compat("test-plugin", {"version": "1.2.3"})
    assert not any("valid semver" in w for w in warnings)
