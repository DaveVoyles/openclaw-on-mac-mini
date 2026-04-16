"""Unit tests for openclaw_cli_auth.py — token resolution helpers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openclaw_cli_auth import (
    AUTH_FILE_NAME,
    KEYCHAIN_SERVICE,
    TOKEN_ENV_VARS,
    OpenClawCliError,
    TokenResolution,
    auth_storage_path,
    delete_keychain_token,
    read_keychain_token,
    write_keychain_token,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_keychain_service_is_string(self):
        assert isinstance(KEYCHAIN_SERVICE, str)
        assert len(KEYCHAIN_SERVICE) > 0

    def test_token_env_vars_string(self):
        assert "OPENCLAW_TOKEN" in TOKEN_ENV_VARS
        assert "DASHBOARD_API_TOKEN" in TOKEN_ENV_VARS

    def test_auth_file_name(self):
        assert AUTH_FILE_NAME == "token"


# ---------------------------------------------------------------------------
# OpenClawCliError
# ---------------------------------------------------------------------------

class TestOpenClawCliError:
    def test_is_runtime_error(self):
        assert issubclass(OpenClawCliError, RuntimeError)

    def test_can_raise_and_catch(self):
        with pytest.raises(OpenClawCliError, match="oops"):
            raise OpenClawCliError("oops")

    def test_message_preserved(self):
        err = OpenClawCliError("token is missing")
        assert "token is missing" in str(err)


# ---------------------------------------------------------------------------
# TokenResolution
# ---------------------------------------------------------------------------

class TestTokenResolution:
    def test_creation(self):
        tr = TokenResolution(token="abc123", source="keychain")
        assert tr.token == "abc123"
        assert tr.source == "keychain"

    def test_env_source(self):
        tr = TokenResolution(token="env-token", source="environment")
        assert tr.source == "environment"


# ---------------------------------------------------------------------------
# auth_storage_path
# ---------------------------------------------------------------------------

class TestAuthStoragePath:
    def test_returns_path(self):
        p = auth_storage_path(platform_name="linux")
        assert isinstance(p, Path)

    def test_linux_path_ends_with_token(self):
        p = auth_storage_path(platform_name="linux")
        assert p.name == "token"

    def test_linux_uses_xdg_config_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        p = auth_storage_path(platform_name="linux")
        assert str(tmp_path) in str(p)
        assert p.name == "token"

    def test_linux_fallback_to_home_config(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        p = auth_storage_path(platform_name="linux")
        assert ".config" in str(p)
        assert "openclaw" in str(p)

    def test_darwin_path(self):
        p = auth_storage_path(platform_name="darwin")
        assert "Application Support" in str(p)
        assert "OpenClaw" in str(p)
        assert p.name == "token"

    def test_windows_path_appdata(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        p = auth_storage_path(platform_name="win32")
        assert str(tmp_path) in str(p)
        assert p.name == "token"

    def test_windows_path_fallback(self, monkeypatch):
        monkeypatch.delenv("APPDATA", raising=False)
        p = auth_storage_path(platform_name="win32")
        assert "AppData" in str(p) or "OpenClaw" in str(p)
        assert p.name == "token"

    def test_uses_sys_platform_by_default(self):
        p = auth_storage_path()
        assert isinstance(p, Path)
        assert p.name == "token"


# ---------------------------------------------------------------------------
# read_keychain_token
# ---------------------------------------------------------------------------

class TestReadKeychainToken:
    def test_non_darwin_returns_empty(self):
        with patch("openclaw_cli_auth.sys") as mock_sys:
            mock_sys.platform = "linux"
            result = read_keychain_token()
        assert result == ""

    def test_darwin_no_account_returns_empty(self):
        with patch("openclaw_cli_auth.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("openclaw_cli_auth.os.getenv", return_value=""):
                with patch("openclaw_cli_auth.getpass.getuser", return_value=""):
                    result = read_keychain_token()
        assert result == ""

    def test_darwin_subprocess_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "my-token\n"
        with patch("openclaw_cli_auth.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("openclaw_cli_auth.subprocess.run", return_value=mock_result):
                result = read_keychain_token(account="testuser")
        assert result == "my-token"

    def test_darwin_subprocess_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("openclaw_cli_auth.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("openclaw_cli_auth.subprocess.run", return_value=mock_result):
                result = read_keychain_token(account="testuser")
        assert result == ""

    def test_darwin_subprocess_exception_returns_empty(self):
        with patch("openclaw_cli_auth.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("openclaw_cli_auth.subprocess.run", side_effect=OSError("no binary")):
                result = read_keychain_token(account="testuser")
        assert result == ""


# ---------------------------------------------------------------------------
# write_keychain_token
# ---------------------------------------------------------------------------

class TestWriteKeychainToken:
    def test_empty_token_raises(self):
        with pytest.raises(OpenClawCliError, match="empty"):
            write_keychain_token("")

    def test_whitespace_only_token_raises(self):
        with pytest.raises(OpenClawCliError, match="empty"):
            write_keychain_token("   ")

    def test_no_account_raises(self):
        with patch("openclaw_cli_auth.os.getenv", return_value=""):
            with patch("openclaw_cli_auth.getpass.getuser", return_value=""):
                with pytest.raises(OpenClawCliError, match="account"):
                    write_keychain_token("my-token")

    def test_subprocess_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("openclaw_cli_auth.subprocess.run", return_value=mock_result):
            # Should not raise
            write_keychain_token("my-token", account="testuser")

    def test_subprocess_failure_raises(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error detail"
        mock_result.stdout = ""
        with patch("openclaw_cli_auth.subprocess.run", return_value=mock_result):
            with pytest.raises(OpenClawCliError):
                write_keychain_token("my-token", account="testuser")

    def test_subprocess_exception_raises(self):
        with patch("openclaw_cli_auth.subprocess.run", side_effect=OSError("binary missing")):
            with pytest.raises(OpenClawCliError, match="Keychain"):
                write_keychain_token("my-token", account="testuser")


# ---------------------------------------------------------------------------
# delete_keychain_token
# ---------------------------------------------------------------------------

class TestDeleteKeychainToken:
    def test_non_darwin_returns_false(self):
        with patch("openclaw_cli_auth.sys") as mock_sys:
            mock_sys.platform = "linux"
            result = delete_keychain_token()
        assert result is False

    def test_no_account_returns_false(self):
        with patch("openclaw_cli_auth.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("openclaw_cli_auth.os.getenv", return_value=""):
                with patch("openclaw_cli_auth.getpass.getuser", return_value=""):
                    result = delete_keychain_token()
        assert result is False

    def test_success_returns_true(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("openclaw_cli_auth.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("openclaw_cli_auth.subprocess.run", return_value=mock_result):
                result = delete_keychain_token(account="testuser")
        assert result is True

    def test_item_not_found_returns_false(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "The specified item could not be found"
        mock_result.stdout = ""
        with patch("openclaw_cli_auth.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("openclaw_cli_auth.subprocess.run", return_value=mock_result):
                result = delete_keychain_token(account="testuser")
        assert result is False

    def test_unknown_error_raises(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some other error"
        mock_result.stdout = ""
        with patch("openclaw_cli_auth.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("openclaw_cli_auth.subprocess.run", return_value=mock_result):
                with pytest.raises(OpenClawCliError):
                    delete_keychain_token(account="testuser")

    def test_subprocess_exception_raises(self):
        with patch("openclaw_cli_auth.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("openclaw_cli_auth.subprocess.run", side_effect=OSError("binary gone")):
                with pytest.raises(OpenClawCliError):
                    delete_keychain_token(account="testuser")
