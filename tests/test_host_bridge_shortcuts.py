"""Phase 5 — unit tests for host_bridge_shortcuts."""
from __future__ import annotations

import sys
from pathlib import Path

# Add src/ to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from host_bridge_shortcuts import (  # noqa: E402
    SHORTCUTS,
    ResolvedShortcut,
    ShortcutError,
    help_text,
    list_shortcuts,
    resolve,
)


class TestRegistry:
    def test_all_expected_shortcuts_present(self) -> None:
        assert set(SHORTCUTS) == {
            "status", "logs", "restart", "disk", "net", "plex-fix", "git",
        }

    def test_list_shortcuts_sorted(self) -> None:
        names = [s.name for s in list_shortcuts()]
        assert names == sorted(names)

    def test_help_text_lists_every_shortcut(self) -> None:
        h = help_text()
        for sc in SHORTCUTS.values():
            assert sc.name in h
            assert sc.usage in h

    def test_required_args_metadata_consistent(self) -> None:
        # Anything with {service} or {args} in template must require_arg.
        for sc in SHORTCUTS.values():
            needs = "{service}" in sc.prompt_template or "{args}" in sc.prompt_template
            assert needs == sc.requires_arg, f"{sc.name} requires_arg mismatch"


class TestResolveHappyPath:
    def test_status_no_args(self) -> None:
        r = resolve("status")
        assert isinstance(r, ResolvedShortcut)
        assert r.name == "status"
        assert "docker ps" in r.prompt

    def test_disk(self) -> None:
        r = resolve("disk")
        assert isinstance(r, ResolvedShortcut)
        assert "df -h" in r.prompt

    def test_net(self) -> None:
        r = resolve("net")
        assert isinstance(r, ResolvedShortcut)
        assert "192.168.1.8" in r.prompt

    def test_plex_fix(self) -> None:
        r = resolve("plex-fix")
        assert isinstance(r, ResolvedShortcut)
        assert "Plex" in r.prompt

    def test_logs_default_lines(self) -> None:
        r = resolve("logs sonarr")
        assert isinstance(r, ResolvedShortcut)
        assert "sonarr" in r.prompt
        assert "200" in r.prompt

    def test_logs_custom_lines(self) -> None:
        r = resolve("logs sonarr 50")
        assert isinstance(r, ResolvedShortcut)
        assert "50" in r.prompt

    def test_restart(self) -> None:
        r = resolve("restart sonarr")
        assert isinstance(r, ResolvedShortcut)
        assert "sonarr" in r.prompt

    def test_restart_plex_special_case(self) -> None:
        r = resolve("restart plex")
        assert isinstance(r, ResolvedShortcut)
        # Prompt must mention native macOS, not just docker restart.
        assert "native macOS" in r.prompt or "AppleScript" in r.prompt

    def test_git(self) -> None:
        r = resolve("git status")
        assert isinstance(r, ResolvedShortcut)
        assert "git -C ~/docker-stack" in r.prompt
        assert "status" in r.prompt

    def test_git_with_multiple_args(self) -> None:
        r = resolve('git log --oneline -n 5')
        assert isinstance(r, ResolvedShortcut)
        assert "log --oneline -n 5" in r.prompt


class TestResolveErrors:
    @pytest.mark.parametrize("inp", ["", "  ", "help", "?", "-h", "--help"])
    def test_empty_or_help_returns_help_text(self, inp: str) -> None:
        r = resolve(inp)
        assert isinstance(r, ShortcutError)
        assert "/host" in r.message

    def test_unknown_subcommand(self) -> None:
        r = resolve("nope")
        assert isinstance(r, ShortcutError)
        assert "unknown subcommand" in r.message
        assert "nope" in r.message

    def test_missing_required_arg(self) -> None:
        r = resolve("logs")
        assert isinstance(r, ShortcutError)
        assert "requires arguments" in r.message

    def test_logs_invalid_n(self) -> None:
        r = resolve("logs sonarr notanumber")
        assert isinstance(r, ShortcutError)
        assert "integer" in r.message

    def test_logs_n_clamped_high(self) -> None:
        r = resolve("logs sonarr 99999999")
        assert isinstance(r, ResolvedShortcut)
        assert "5000" in r.prompt
        assert "99999999" not in r.prompt

    def test_logs_n_clamped_low(self) -> None:
        r = resolve("logs sonarr 0")
        assert isinstance(r, ResolvedShortcut)
        assert "1 lines" in r.prompt or "1 line" in r.prompt

    def test_leading_slash_tolerated(self) -> None:
        # User types `/status` instead of `status` — accept it.
        r = resolve("/status")
        assert isinstance(r, ResolvedShortcut)
        assert r.name == "status"

    def test_case_insensitive_subcommand(self) -> None:
        r = resolve("STATUS")
        assert isinstance(r, ResolvedShortcut)
        assert r.name == "status"

    def test_unclosed_quote(self) -> None:
        r = resolve('git "unclosed')
        assert isinstance(r, ShortcutError)
        assert "parse" in r.message


class TestSafeScrubbing:
    def test_shell_metachars_stripped_from_service_name(self) -> None:
        r = resolve("logs sonarr`whoami`")
        assert isinstance(r, ResolvedShortcut)
        # Sanitized arg has no backticks; prompt template's own markdown
        # backticks survive (around the {service} placeholder).
        assert "sonarrwhoami" in r.prompt
        assert "`whoami`" not in r.prompt  # no surviving command substitution

    def test_pipe_stripped_from_git_args(self) -> None:
        r = resolve("git status | cat /etc/passwd")
        assert isinstance(r, ResolvedShortcut)
        assert "|" not in r.prompt
        assert "/etc/passwd" in r.prompt  # path itself isn't dangerous
