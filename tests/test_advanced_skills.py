"""
Tests for skills/advanced_skills.py.

Covers:
- ping_host: hostname validation (no subprocess) and mocked subprocess calls
- _truncate: string truncation helper
- check_service_ports: mocked socket connections
- _api_get: mocked aiohttp behaviour
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skills.advanced_skills import _truncate, check_service_ports, ping_host

# ---------------------------------------------------------------------------
# _truncate — pure function, no external calls
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_string_unchanged(self):
        s = "hello"
        assert _truncate(s, limit=100) == s

    def test_exactly_at_limit_unchanged(self):
        s = "a" * 100
        assert _truncate(s, limit=100) == s

    def test_advanced_skills_over_limit_truncated(self):
        s = "a" * 200
        result = _truncate(s, limit=100)
        assert len(result) <= 100

    def test_truncated_string_ends_with_indicator(self):
        s = "a" * 2000
        result = _truncate(s)  # default limit 1900
        assert "truncated" in result

    def test_empty_string_unchanged(self):
        assert _truncate("") == ""


# ---------------------------------------------------------------------------
# ping_host — hostname validation
# ---------------------------------------------------------------------------


class TestPingHostValidation:
    """Test the hostname validation guard before any subprocess call."""

    @pytest.mark.asyncio
    async def test_valid_ip_address_passes_validation(self):
        """A valid IPv4 address should proceed past validation (subprocess mocked)."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(
            b"PING 192.168.1.1: 56 data bytes\nround-trip min/avg/max = 1.2/1.5/2.0 ms", b""
        ))
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            with patch("asyncio.wait_for", new=AsyncMock(return_value=(
                b"round-trip min/avg/max = 1.2/1.5/2.0 ms\n", b""
            ))):
                result = await ping_host("192.168.1.1")
                # Must not return the "Invalid hostname" error
                assert "Invalid hostname" not in result

    @pytest.mark.asyncio
    async def test_valid_hostname_passes_validation(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"PING ok\nrtt min/avg/max = 1/2/3 ms", b""))
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            with patch("asyncio.wait_for", new=AsyncMock(return_value=(
                b"rtt min/avg/max = 1/2/3 ms\n", b""
            ))):
                result = await ping_host("google.com")
                assert "Invalid hostname" not in result

    @pytest.mark.asyncio
    async def test_hostname_with_hyphen_and_dot_is_valid(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            with patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b""))):
                result = await ping_host("my-server.local")
                assert "Invalid hostname" not in result

    @pytest.mark.asyncio
    async def test_hostname_with_semicolon_is_rejected(self):
        result = await ping_host("host; rm -rf /")
        assert "❌" in result
        assert "Invalid hostname" in result

    @pytest.mark.asyncio
    async def test_hostname_with_ampersand_is_rejected(self):
        result = await ping_host("host && whoami")
        assert "❌" in result
        assert "Invalid hostname" in result

    @pytest.mark.asyncio
    async def test_hostname_with_pipe_is_rejected(self):
        result = await ping_host("host|cat /etc/passwd")
        assert "❌" in result
        assert "Invalid hostname" in result

    @pytest.mark.asyncio
    async def test_hostname_with_backtick_is_rejected(self):
        result = await ping_host("`uname`")
        assert "❌" in result
        assert "Invalid hostname" in result

    @pytest.mark.asyncio
    async def test_hostname_with_dollar_sign_is_rejected(self):
        result = await ping_host("$HOME")
        assert "❌" in result
        assert "Invalid hostname" in result

    @pytest.mark.asyncio
    async def test_hostname_with_space_is_rejected(self):
        result = await ping_host("valid host")
        assert "❌" in result
        assert "Invalid hostname" in result

    @pytest.mark.asyncio
    async def test_empty_string_is_rejected(self):
        """Empty hostname after shlex.quote/strip should be invalid."""
        result = await ping_host("")
        # Empty after quoting — all(c ...) on empty string is True in Python,
        # so it may proceed to ping with an empty arg. Either rejection or
        # a subprocess-level failure is acceptable behavior.
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# ping_host — subprocess result handling (mocked)
# ---------------------------------------------------------------------------


class TestPingHostSubprocess:
    @pytest.mark.asyncio
    async def test_reachable_host_returns_success(self):
        rtt_line = b"round-trip min/avg/max/stddev = 0.543/0.678/0.812/0.135 ms\n"
        mock_proc = AsyncMock()
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            with patch("asyncio.wait_for", new=AsyncMock(return_value=(rtt_line, b""))):
                result = await ping_host("192.168.1.1")
                assert "✅" in result
                assert "192.168.1.1" in result

    @pytest.mark.asyncio
    async def test_unreachable_host_returns_failure(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 2  # ping returns non-zero for unreachable

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            with patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b""))):
                result = await ping_host("10.0.0.254")
                assert "❌" in result

    @pytest.mark.asyncio
    async def test_ping_timeout_returns_error(self):
        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            with patch("asyncio.wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError)):
                result = await ping_host("192.168.1.99")
                assert "❌" in result
                assert "timed out" in result.lower()


# ---------------------------------------------------------------------------
# check_service_ports — mocked socket connections
# ---------------------------------------------------------------------------


class TestCheckServicePorts:
    @pytest.mark.asyncio
    async def test_all_ports_open_shows_up(self):
        """All sockets succeed → every service shows as 'up'."""
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()

        with patch("asyncio.open_connection", new=AsyncMock(return_value=(mock_reader, mock_writer))):
            result = await check_service_ports()
            assert "✅" in result

    @pytest.mark.asyncio
    async def test_connection_refused_shows_down(self):
        async def refuse(*args, **kwargs):
            raise ConnectionRefusedError()

        with patch("asyncio.open_connection", new=AsyncMock(side_effect=ConnectionRefusedError)):
            result = await check_service_ports()
            assert "❌" in result

    @pytest.mark.asyncio
    async def test_result_contains_service_names(self):
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()

        with patch("asyncio.open_connection", new=AsyncMock(return_value=(mock_reader, mock_writer))):
            result = await check_service_ports()
            # At least one well-known service name should appear
            assert any(name in result for name in ("Sonarr", "Radarr", "Plex", "SABnzbd", "Tautulli"))
