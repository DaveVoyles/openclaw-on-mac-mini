"""
Tests for git_skills.py — Git operations and webfetch wrapper.

Covers: _run_git subprocess handling, git_status/log/diff/commit,
webfetch missing skill handling, and GIT_SKILLS dict.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import git_skills as gs

# ---------------------------------------------------------------------------
# _run_git
# ---------------------------------------------------------------------------


class TestRunGit:
    @pytest.mark.asyncio
    async def test_returns_stdout_on_success(self):
        """git --version should succeed and return version info."""
        result = await gs._run_git(["--version"])
        assert "git version" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_error_on_bad_command(self):
        """Invalid git subcommand should return error message."""
        result = await gs._run_git(["not-a-real-git-command"])
        # Git returns an error for unknown commands
        assert result  # Should contain some output (error message from git)

    @pytest.mark.asyncio
    async def test_handles_timeout(self, monkeypatch):
        """If the subprocess times out, should return an error string."""

        async def mock_create_subprocess_exec(*args, **kwargs):
            mock_proc = MagicMock()

            async def slow_communicate():
                await asyncio.sleep(100)
                return b"", b""

            mock_proc.communicate = slow_communicate
            return mock_proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess_exec)
        result = await gs._run_git(["status"])
        assert "failed" in result.lower()


# ---------------------------------------------------------------------------
# Webfetch
# ---------------------------------------------------------------------------


class TestWebfetch:
    @pytest.mark.asyncio
    async def test_webfetch_missing_skill(self, monkeypatch, tmp_path):
        """When webfetch-md CLI doesn't exist, returns an error message."""
        monkeypatch.setattr(gs, "_WEBFETCH_CLI", tmp_path / "nonexistent" / "cli.js")
        result = await gs._run_webfetch("https://example.com")
        assert "not installed" in result.lower()


# ---------------------------------------------------------------------------
# GIT_SKILLS dict
# ---------------------------------------------------------------------------


class TestGitSkillsDict:
    def test_expected_skills_present(self):
        expected = {"webfetch_md", "git_status", "git_log", "git_diff", "git_commit"}
        assert expected == set(gs.GIT_SKILLS.keys())

    def test_all_skills_callable(self):
        for name, fn in gs.GIT_SKILLS.items():
            assert callable(fn), f"{name} is not callable"


# ---------------------------------------------------------------------------
# git_log
# ---------------------------------------------------------------------------


class TestGitLog:
    @pytest.mark.asyncio
    async def test_git_log_uses_limit(self):
        """git_log should pass the limit argument."""
        with patch.object(gs, "_run_git", new_callable=AsyncMock, return_value="abc123 message") as mock:
            await gs.git_log(limit=3)
            mock.assert_called_once_with(["log", "--oneline", "-n 3"])


# ---------------------------------------------------------------------------
# git_diff
# ---------------------------------------------------------------------------


class TestGitDiff:
    @pytest.mark.asyncio
    async def test_git_diff_unstaged(self):
        with patch.object(gs, "_run_git", new_callable=AsyncMock, return_value="") as mock:
            await gs.git_diff(staged=False)
            mock.assert_called_once_with(["diff"])

    @pytest.mark.asyncio
    async def test_git_diff_staged(self):
        with patch.object(gs, "_run_git", new_callable=AsyncMock, return_value="") as mock:
            await gs.git_diff(staged=True)
            mock.assert_called_once_with(["diff", "--staged"])


# ---------------------------------------------------------------------------
# git_commit
# ---------------------------------------------------------------------------


class TestGitCommit:
    @pytest.mark.asyncio
    async def test_git_commit_passes_message(self):
        with patch.object(gs, "_run_git", new_callable=AsyncMock, return_value="committed") as mock:
            result = await gs.git_commit("fix: update tests")
            mock.assert_called_once_with(["commit", "-am", "fix: update tests"])
            assert "committed" in result
