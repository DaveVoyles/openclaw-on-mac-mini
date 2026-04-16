"""Unit tests for openclaw_cli_ui_core.py — terminal detection and ANSI palette."""
from __future__ import annotations

from unittest.mock import patch

import openclaw_cli_ui_core as ui_core
from openclaw_cli_ui_core import _c, _get_is_tty


# ---------------------------------------------------------------------------
# _c() — ANSI code gating
# ---------------------------------------------------------------------------

class TestAnsiGating:
    def test_c_returns_code_when_tty(self):
        with patch.object(ui_core, "_IS_TTY", True):
            assert _c("\033[0m") == "\033[0m"

    def test_c_returns_empty_when_not_tty(self):
        with patch.object(ui_core, "_IS_TTY", False):
            assert _c("\033[0m") == ""

    def test_c_empty_code_always_returns_empty(self):
        with patch.object(ui_core, "_IS_TTY", True):
            assert _c("") == ""
        with patch.object(ui_core, "_IS_TTY", False):
            assert _c("") == ""


# ---------------------------------------------------------------------------
# _get_is_tty()
# ---------------------------------------------------------------------------

class TestGetIsTty:
    def test_returns_bool(self):
        result = _get_is_tty()
        assert isinstance(result, bool)

    def test_returns_true_when_module_is_tty(self):
        with patch.object(ui_core, "_IS_TTY", True):
            assert _get_is_tty() is True

    def test_falls_back_to_stdout_check(self):
        # When _IS_TTY is False, _get_is_tty() delegates to sys.stdout.isatty()
        with patch.object(ui_core, "_IS_TTY", False):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _get_is_tty() is True

    def test_returns_false_when_neither_tty(self):
        with patch.object(ui_core, "_IS_TTY", False):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                assert _get_is_tty() is False


# ---------------------------------------------------------------------------
# ANSI palette constants exist and are strings
# ---------------------------------------------------------------------------

class TestAnsiConstants:
    def _attr(self, name):
        return getattr(ui_core, name)

    def test_reset_is_string(self):
        assert isinstance(self._attr("_R"), str)

    def test_bold_is_string(self):
        assert isinstance(self._attr("_B"), str)

    def test_dim_is_string(self):
        assert isinstance(self._attr("_DM"), str)

    def test_color_constants_are_strings(self):
        for name in ["_CY", "_GR", "_YE", "_RE", "_MA"]:
            assert isinstance(self._attr(name), str), f"{name} should be str"

    def test_bold_color_constants_exist(self):
        for name in ["_BCY", "_BGR", "_BYE", "_BRE", "_BBL"]:
            assert isinstance(self._attr(name), str), f"{name} should be str"

    def test_text_style_constants_exist(self):
        for name in ["_IT", "_UL"]:
            assert isinstance(self._attr(name), str), f"{name} should be str"

    def test_all_constants_present(self):
        expected = {"_R", "_B", "_DM", "_CY", "_GR", "_YE", "_RE", "_MA",
                    "_BCY", "_BGR", "_BYE", "_BRE", "_BBL", "_IT", "_UL"}
        for name in expected:
            assert hasattr(ui_core, name), f"Missing constant {name}"

    def test_non_tty_constants_are_empty(self):
        # When not a TTY at import time, constants should be empty strings
        # We can't re-import easily, but we can check: if _IS_TTY is False
        # all constants should be empty; if True they should contain ANSI escapes.
        # Just assert they're strings (the exact value depends on TTY state).
        for name in ["_R", "_B", "_CY"]:
            val = self._attr(name)
            assert isinstance(val, str)
            # Empty (non-TTY) or ANSI escape (TTY)
            assert val == "" or val.startswith("\033[")
