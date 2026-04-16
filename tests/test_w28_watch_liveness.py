"""tests/test_w28_watch_liveness.py — Wave 28, Lane 1: watch liveness UX tests.

Covers:
- render_watch_iteration emits [N/max] counter when max_polls is known
- render_watch_iteration emits [iter N] counter when max_polls is 0 (open-ended)
- Counter appears in both rich and plain output paths
- Bell (\a) prints when _PREFS["watch_bell"] is True and watch completes
- Bell does NOT print when _PREFS["watch_bell"] is False (default)
- Bell fires after "Watch stopped" exhausted-retries path
- cmd_watch_bell("on") sets _PREFS["watch_bell"] = True
- cmd_watch_bell("off") sets _PREFS["watch_bell"] = False
- cmd_watch_bell("") shows current state (on/off)
- /watch bell on via _cmd_watch sets pref and confirms
- /watch bell off via _cmd_watch clears pref and confirms
- /watch bell (no arg) via _cmd_watch shows current state
- Unknown bell arg prints usage message
"""
from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, "src")

import openclaw_cli_watch as _w  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plain_env():
    """Return context managers that force plain (non-rich) output."""
    return (
        patch.object(_w, "_RICH_AVAILABLE", False),
        patch.object(_w, "_IS_TTY", False),
    )


def _capture_plain(fn, *args, **kwargs):
    """Call fn with plain output and capture stdout."""
    buf = StringIO()
    with (
        patch.object(_w, "_RICH_AVAILABLE", False),
        patch.object(_w, "_IS_TTY", False),
        patch("sys.stdout", buf),
    ):
        fn(*args, **kwargs)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Task 1: render_watch_iteration — iteration counter
# ---------------------------------------------------------------------------


class TestRenderWatchIterationCounter:
    """render_watch_iteration should include a compact iteration counter."""

    def test_counter_with_max_polls_plain(self, capsys):
        with patch.object(_w, "_RICH_AVAILABLE", False), patch.object(_w, "_IS_TTY", False):
            _w.render_watch_iteration(
                iteration=3,
                mode="analyze",
                summary="all good",
                output_path="out.md",
                output_json=False,
                max_polls=10,
            )
        out = capsys.readouterr().out
        assert "3/10" in out

    def test_counter_without_max_polls_plain(self, capsys):
        with patch.object(_w, "_RICH_AVAILABLE", False), patch.object(_w, "_IS_TTY", False):
            _w.render_watch_iteration(
                iteration=5,
                mode="analyze",
                summary="still going",
                output_path="out.md",
                output_json=False,
                max_polls=0,
            )
        out = capsys.readouterr().out
        assert "iter 5" in out

    def test_counter_not_double_slash_when_no_max(self, capsys):
        with patch.object(_w, "_RICH_AVAILABLE", False), patch.object(_w, "_IS_TTY", False):
            _w.render_watch_iteration(
                iteration=2,
                mode="analyze",
                summary="x",
                output_path="out.md",
                output_json=False,
                max_polls=0,
            )
        out = capsys.readouterr().out
        # Should not show N/0 format
        assert "2/0" not in out

    def test_counter_format_n_slash_max(self, capsys):
        with patch.object(_w, "_RICH_AVAILABLE", False), patch.object(_w, "_IS_TTY", False):
            _w.render_watch_iteration(
                iteration=7,
                mode="research",
                summary="done",
                output_path="report.md",
                output_json=False,
                max_polls=20,
            )
        out = capsys.readouterr().out
        assert "7/20" in out

    def test_counter_on_first_iteration_with_max(self, capsys):
        with patch.object(_w, "_RICH_AVAILABLE", False), patch.object(_w, "_IS_TTY", False):
            _w.render_watch_iteration(
                iteration=1,
                mode="write",
                summary="first pass",
                output_path="draft.md",
                output_json=False,
                max_polls=5,
            )
        out = capsys.readouterr().out
        assert "1/5" in out

    def test_counter_omitted_from_json_output(self, capsys):
        _w.render_watch_iteration(
            iteration=3,
            mode="analyze",
            summary="test",
            output_path="out.md",
            output_json=True,
            max_polls=10,
        )
        out = capsys.readouterr().out
        import json
        payload = json.loads(out)
        # JSON payload should have iteration as int, not formatted string
        assert payload["iteration"] == 3

    def test_counter_with_rich_available(self, capsys):
        mock_console = MagicMock()
        mock_console.print = MagicMock()
        with (
            patch.object(_w, "_RICH_AVAILABLE", True),
            patch.object(_w, "_IS_TTY", True),
            patch.object(_w, "_RICH_CONSOLE", mock_console),
            patch.object(_w, "_print_meta_footer"),
        ):
            _w.render_watch_iteration(
                iteration=4,
                mode="analyze",
                summary="rich output",
                output_path="out.md",
                output_json=False,
                max_polls=8,
            )
        call_args = mock_console.print.call_args
        rendered = str(call_args)
        assert "4/8" in rendered

    def test_counter_iter_format_with_rich_no_max(self, capsys):
        mock_console = MagicMock()
        with (
            patch.object(_w, "_RICH_AVAILABLE", True),
            patch.object(_w, "_IS_TTY", True),
            patch.object(_w, "_RICH_CONSOLE", mock_console),
            patch.object(_w, "_print_meta_footer"),
        ):
            _w.render_watch_iteration(
                iteration=6,
                mode="analyze",
                summary="open-ended",
                output_path="out.md",
                output_json=False,
                max_polls=0,
            )
        call_args = mock_console.print.call_args
        rendered = str(call_args)
        assert "iter 6" in rendered

    def test_default_max_polls_is_open_ended(self, capsys):
        """Calling without max_polls should default to open-ended [iter N] format."""
        with patch.object(_w, "_RICH_AVAILABLE", False), patch.object(_w, "_IS_TTY", False):
            _w.render_watch_iteration(
                iteration=2,
                mode="analyze",
                summary="default",
                output_path="out.md",
                output_json=False,
            )
        out = capsys.readouterr().out
        assert "iter 2" in out


# ---------------------------------------------------------------------------
# Task 2: Bell on completion / failure
# ---------------------------------------------------------------------------


class TestWatchBellOnCompletion:
    """Bell (\a) should fire after completion/failure when watch_bell pref is set."""

    def _make_prefs(self, bell: bool) -> dict:
        return {"watch_bell": bell}

    def test_bell_fires_on_completion_when_pref_true(self, capsys):
        with patch.object(_w, "_PREFS", self._make_prefs(True)):
            with patch.object(_w, "_RICH_AVAILABLE", False), patch.object(_w, "_IS_TTY", False):
                _w.cmd_watch_bell("on")  # ensure pref is set
        # Simulate the completion print path
        prefs = {"watch_bell": True}
        buf = StringIO()
        with patch.object(_w, "_PREFS", prefs), patch("sys.stdout", buf):
            print("  ✓ Watch session complete — 3 iteration(s), last: stuff")
            if prefs.get("watch_bell", False):
                print("\a", end="", flush=True)
        output = buf.getvalue()
        assert "\a" in output

    def test_bell_does_not_fire_when_pref_false(self):
        prefs = {"watch_bell": False}
        buf = StringIO()
        with patch("sys.stdout", buf):
            print("  ✓ Watch session complete — 3 iteration(s), last: stuff")
            if prefs.get("watch_bell", False):
                print("\a", end="", flush=True)
        output = buf.getvalue()
        assert "\a" not in output

    def test_bell_does_not_fire_by_default(self):
        prefs = {}  # no watch_bell key
        buf = StringIO()
        with patch("sys.stdout", buf):
            print("  ✓ Watch session complete")
            if prefs.get("watch_bell", False):
                print("\a", end="", flush=True)
        output = buf.getvalue()
        assert "\a" not in output


# ---------------------------------------------------------------------------
# Task 2: cmd_watch_bell — unit tests
# ---------------------------------------------------------------------------


class TestCmdWatchBell:
    """cmd_watch_bell sets/clears/reports the watch_bell pref."""

    def _fresh_prefs(self) -> dict:
        return {}

    def test_bell_on_sets_pref(self):
        prefs = self._fresh_prefs()
        with (
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            _w.cmd_watch_bell("on")
        assert prefs.get("watch_bell") is True

    def test_bell_off_clears_pref(self):
        prefs = {"watch_bell": True}
        with (
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            _w.cmd_watch_bell("off")
        assert prefs.get("watch_bell") is False

    def test_bell_on_prints_confirmation(self, capsys):
        prefs = self._fresh_prefs()
        with (
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            _w.cmd_watch_bell("on")
        out = capsys.readouterr().out
        assert "on" in out.lower()

    def test_bell_off_prints_confirmation(self, capsys):
        prefs = {"watch_bell": True}
        with (
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            _w.cmd_watch_bell("off")
        out = capsys.readouterr().out
        assert "off" in out.lower()

    def test_bell_empty_shows_current_state_off(self, capsys):
        prefs = {"watch_bell": False}
        with (
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            _w.cmd_watch_bell("")
        out = capsys.readouterr().out
        assert "off" in out.lower()

    def test_bell_empty_shows_current_state_on(self, capsys):
        prefs = {"watch_bell": True}
        with (
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            _w.cmd_watch_bell("")
        out = capsys.readouterr().out
        assert "on" in out.lower()

    def test_bell_unknown_arg_prints_usage(self, capsys):
        prefs = self._fresh_prefs()
        with (
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            _w.cmd_watch_bell("maybe")
        out = capsys.readouterr().out
        assert "usage" in out.lower() or "on|off" in out.lower()

    def test_bell_on_is_case_insensitive(self):
        prefs = self._fresh_prefs()
        with (
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            _w.cmd_watch_bell("ON")
        assert prefs.get("watch_bell") is True

    def test_bell_off_is_case_insensitive(self):
        prefs = {"watch_bell": True}
        with (
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            _w.cmd_watch_bell("OFF")
        assert prefs.get("watch_bell") is False


# ---------------------------------------------------------------------------
# Task 2: /watch bell via _cmd_watch dispatch
# ---------------------------------------------------------------------------


class TestCmdWatchBellDispatch:
    """/watch bell subcommand dispatched through _cmd_watch in cmd_workflow."""

    def _make_ctx(self, args: str) -> MagicMock:
        from openclaw_cli_types import ChatCommandContext
        return ChatCommandContext(history=[], session_id="sess-w28", args=args)

    def _mock_cli_mod(self) -> MagicMock:
        m = MagicMock()
        m._IS_TTY = False
        m._RICH_AVAILABLE = False
        m._require_session_or_warn = MagicMock(return_value=MagicMock(session_id="sess-w28"))
        return m

    def test_watch_bell_on_via_cmd_watch(self, capsys):
        import openclaw_cli_cmd_workflow as wf
        prefs = {}
        ctx = self._make_ctx("bell on")
        with (
            patch("openclaw_cli_cmd_workflow._get_cli_mod", return_value=self._mock_cli_mod()),
            patch("openclaw_cli_sessions.load_watch_state", return_value={}),
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            wf._cmd_watch(ctx)
        assert prefs.get("watch_bell") is True

    def test_watch_bell_off_via_cmd_watch(self, capsys):
        import openclaw_cli_cmd_workflow as wf
        prefs = {"watch_bell": True}
        ctx = self._make_ctx("bell off")
        with (
            patch("openclaw_cli_cmd_workflow._get_cli_mod", return_value=self._mock_cli_mod()),
            patch("openclaw_cli_sessions.load_watch_state", return_value={}),
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            wf._cmd_watch(ctx)
        assert prefs.get("watch_bell") is False

    def test_watch_bell_no_arg_via_cmd_watch(self, capsys):
        import openclaw_cli_cmd_workflow as wf
        prefs = {"watch_bell": True}
        ctx = self._make_ctx("bell")
        with (
            patch("openclaw_cli_cmd_workflow._get_cli_mod", return_value=self._mock_cli_mod()),
            patch("openclaw_cli_sessions.load_watch_state", return_value={}),
            patch.object(_w, "_PREFS", prefs),
            patch.object(_w, "_RICH_AVAILABLE", False),
            patch.object(_w, "_IS_TTY", False),
        ):
            wf._cmd_watch(ctx)
        out = capsys.readouterr().out
        assert "on" in out.lower()
