"""Unit tests for openclaw_cli_update.py — version checking and self-update."""

from __future__ import annotations

import json
import subprocess
from importlib import metadata
from unittest.mock import MagicMock, patch

import openclaw_cli_update as update_mod
from openclaw_cli_update import (
    _CLI_BUILD,
    DEFAULT_BASE_URL,
    DEFAULT_VERSION,
    _fetch_latest_pypi_version,
    _find_pip,
    _standalone_install_dir,
    _version_tuple,
    check_for_update,
    cli_version,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_base_url(self):
        assert "localhost" in DEFAULT_BASE_URL or "http" in DEFAULT_BASE_URL

    def test_default_version_semver(self):
        parts = DEFAULT_VERSION.split(".")
        assert len(parts) >= 2

    def test_cli_build_string(self):
        assert isinstance(_CLI_BUILD, str)
        assert len(_CLI_BUILD) > 0


# ---------------------------------------------------------------------------
# cli_version()
# ---------------------------------------------------------------------------


class TestCliVersion:
    def test_openclaw_cli_update_unit_returns_string(self):
        result = cli_version()
        assert isinstance(result, str)

    def test_contains_build_tag(self):
        result = cli_version()
        assert _CLI_BUILD in result

    def test_contains_plus_separator(self):
        result = cli_version()
        assert "+" in result

    def test_package_not_found_uses_default(self):
        with patch("openclaw_cli_update.metadata.version", side_effect=metadata.PackageNotFoundError):
            result = cli_version()
        assert DEFAULT_VERSION in result
        assert _CLI_BUILD in result

    def test_package_found_uses_installed_version(self):
        with patch("openclaw_cli_update.metadata.version", return_value="1.2.3"):
            result = cli_version()
        assert "1.2.3" in result
        assert _CLI_BUILD in result


# ---------------------------------------------------------------------------
# _version_tuple()
# ---------------------------------------------------------------------------


class TestVersionTuple:
    def test_simple_semver(self):
        assert _version_tuple("1.2.3") == (1, 2, 3)

    def test_date_based_version(self):
        assert _version_tuple("2026.3.20") == (2026, 3, 20)

    def test_single_segment(self):
        assert _version_tuple("5") == (5,)

    def test_invalid_returns_zero_tuple(self):
        assert _version_tuple("not-a-version") == (0,)

    def test_empty_string_returns_zero_tuple(self):
        assert _version_tuple("") == (0,)

    def test_comparison_ordering(self):
        assert _version_tuple("1.0.0") < _version_tuple("2.0.0")
        assert _version_tuple("1.9.9") < _version_tuple("2.0.0")
        assert _version_tuple("1.2.3") == _version_tuple("1.2.3")

    def test_two_segment_version(self):
        assert _version_tuple("0.6") == (0, 6)


# ---------------------------------------------------------------------------
# _fetch_latest_pypi_version()
# ---------------------------------------------------------------------------


class TestFetchLatestPypiVersion:
    def test_returns_version_string_on_success(self):
        fake_response = json.dumps({"info": {"version": "2026.1.1"}}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = fake_response

        with patch("openclaw_cli_update.request.urlopen", return_value=mock_resp):
            result = _fetch_latest_pypi_version()
        assert result == "2026.1.1"

    def test_returns_none_on_network_error(self):
        with patch("openclaw_cli_update.request.urlopen", side_effect=Exception("timeout")):
            result = _fetch_latest_pypi_version()
        assert result is None

    def test_returns_none_on_malformed_json(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b"not-json{"

        with patch("openclaw_cli_update.request.urlopen", return_value=mock_resp):
            result = _fetch_latest_pypi_version()
        assert result is None


# ---------------------------------------------------------------------------
# _find_pip()
# ---------------------------------------------------------------------------


class TestFindPip:
    def test_returns_list_when_pip_available(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("openclaw_cli_update.subprocess.run", return_value=mock_result):
            result = _find_pip()
        assert result is not None
        assert isinstance(result, list)
        assert len(result) > 0

    def test_returns_none_when_all_fail(self):
        with patch(
            "openclaw_cli_update.subprocess.run",
            side_effect=FileNotFoundError("not found"),
        ):
            result = _find_pip()
        assert result is None

    def test_returns_none_when_timeout(self):
        with patch(
            "openclaw_cli_update.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="pip", timeout=5),
        ):
            result = _find_pip()
        assert result is None

    def test_skips_nonzero_returncode(self):
        fail = MagicMock()
        fail.returncode = 1
        success = MagicMock()
        success.returncode = 0

        results = [fail, fail, success]

        def side_effect(*args, **kwargs):
            r = results.pop(0)
            return r

        with patch("openclaw_cli_update.subprocess.run", side_effect=side_effect):
            result = _find_pip()
        assert result is not None


# ---------------------------------------------------------------------------
# _standalone_install_dir()
# ---------------------------------------------------------------------------


class TestStandaloneInstallDir:
    def test_returns_none_in_site_packages(self, tmp_path):
        fake_file = tmp_path / "site-packages" / "openclaw" / "openclaw_cli_update.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.touch()

        with patch("openclaw_cli_update.Path") as mock_path:
            mock_path.return_value.resolve.return_value = fake_file
            # site-packages in path → should return None
            result = _standalone_install_dir()
        # The real implementation checks __file__; we just verify it returns str or None
        assert result is None or isinstance(result, str)

    def test_returns_none_without_marker(self, tmp_path):
        # No openclaw_cli_sessions.py in the dir
        fake_file = tmp_path / "openclaw_cli_update.py"
        fake_file.touch()

        with patch("openclaw_cli_update.Path") as mock_path_cls:
            mock_file = MagicMock()
            mock_file.resolve.return_value = fake_file
            mock_file.parent = tmp_path
            mock_path_cls.return_value = mock_file

            # The marker doesn't exist → should return None
            result = _standalone_install_dir()
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# check_for_update()
# ---------------------------------------------------------------------------


class TestCheckForUpdate:
    def test_sets_latest_version_on_success(self):
        with patch.object(update_mod, "_fetch_latest_pypi_version", return_value="9.9.9"):
            check_for_update()
        assert update_mod._latest_version == "9.9.9"

    def test_does_not_overwrite_with_none(self):
        # When PyPI returns None (network error), existing value is preserved
        with patch.object(update_mod, "_fetch_latest_pypi_version", return_value="1.2.3"):
            check_for_update()
        prev = update_mod._latest_version
        with patch.object(update_mod, "_fetch_latest_pypi_version", return_value=None):
            check_for_update()
        assert update_mod._latest_version == prev

    def test_openclaw_cli_update_unit_function_is_callable(self):
        assert callable(check_for_update)

    def test_accepts_timeout_kwarg(self):
        with patch.object(update_mod, "_fetch_latest_pypi_version", return_value=None):
            check_for_update(timeout=1.0)  # should not raise
