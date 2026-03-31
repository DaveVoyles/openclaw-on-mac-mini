"""
Tests for subprocess_utils.py — async subprocess runner.

Covers: successful execution, non-zero return code, timeout handling,
and command-not-found error.
"""

import pytest

import subprocess_utils as su


class TestSubprocessRun:
    @pytest.mark.asyncio
    async def test_successful_command(self):
        """echo should return 0 with stdout."""
        rc, stdout, stderr = await su.run(["echo", "hello"])
        assert rc == 0
        assert "hello" in stdout

    @pytest.mark.asyncio
    async def test_nonzero_return_code(self):
        """A command that exits non-zero should return its exit code."""
        rc, stdout, stderr = await su.run(["python3", "-c", "import sys; sys.exit(42)"])
        assert rc == 42

    @pytest.mark.asyncio
    async def test_stderr_captured(self):
        """stderr output should be captured."""
        rc, stdout, stderr = await su.run(
            ["python3", "-c", "import sys; sys.stderr.write('err msg')"]
        )
        assert "err msg" in stderr

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        """A long-running command should be killed after timeout."""
        rc, stdout, stderr = await su.run(["sleep", "30"], timeout=1)
        assert rc == 1
        assert "timed out" in stderr.lower()

    @pytest.mark.asyncio
    async def test_command_not_found(self):
        """A missing binary should return an error message."""
        rc, stdout, stderr = await su.run(["nonexistent_binary_xyz_12345"])
        assert rc == 1
        assert "not found" in stderr.lower()

    @pytest.mark.asyncio
    async def test_default_timeout_is_15(self):
        assert su.COMMAND_TIMEOUT == 15
