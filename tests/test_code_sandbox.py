"""
Tests for code_sandbox.py — Docker subprocess calls are fully mocked.

Covers successful execution, timeout, and unsupported-language guard.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from code_sandbox import run_code


# ---------------------------------------------------------------------------
# run_code
# ---------------------------------------------------------------------------


class TestRunCode:
    async def test_run_code_returns_stdout(self):
        """Mocked docker run returns captured stdout."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"42\n", b""))

        with patch("code_sandbox.asyncio.create_subprocess_exec", return_value=mock_proc):
            stdout, stderr, rc = await run_code("print(42)")

        assert rc == 0
        assert "42" in stdout

    async def test_run_code_timeout(self):
        """Timeout during docker run returns error message."""
        async def _hang(*a, **kw):
            await asyncio.sleep(999)

        mock_proc = AsyncMock()
        mock_proc.communicate = _hang

        # Also mock the docker kill subprocess
        mock_kill = AsyncMock()
        mock_kill.wait = AsyncMock()

        call_count = 0

        async def _exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_proc
            return mock_kill

        with (
            patch("code_sandbox.asyncio.create_subprocess_exec", side_effect=_exec),
            patch("code_sandbox.SANDBOX_TIMEOUT", 0),
        ):
            stdout, stderr, rc = await run_code("import time; time.sleep(999)")

        assert rc == 1
        assert "timed out" in stderr.lower()

    async def test_run_code_disabled(self):
        """Unsupported language returns an error message immediately."""
        stdout, stderr, rc = await run_code("console.log(1)", language="javascript")
        assert rc == 1
        assert "unsupported" in stderr.lower()

    async def test_run_code_empty(self):
        """Empty code returns an error."""
        stdout, stderr, rc = await run_code("   ")
        assert rc == 1
        assert "no code" in stderr.lower()
