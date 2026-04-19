"""
openclaw_cli_settings — Pure helper functions for settings-related CLI commands.

Extraction findings (TD-18)
---------------------------
After reading _cmd_theme, _cmd_colorscheme, _cmd_emoji, _cmd_overlay,
_cmd_emojiheaders, _cmd_accessibility, and _cmd_pasteguard, no pure helper
functions were found that satisfy ALL of the following constraints simultaneously:

  (a) contain no I/O of their own (or minimal, side-effect-free state reads)
  (b) depend only on openclaw_cli_prefs and openclaw_cli_ui_core (no circular
      imports via openclaw_cli.py)
  (c) can be relocated without breaking existing pytest monkeypatching that
      targets openclaw_cli.sys.stdin and openclaw_cli._IS_TTY

Specific blockers per command:

- _cmd_colorscheme, _cmd_emoji, _cmd_emojiheaders, _cmd_pasteguard
    Fully inline dispatch with no standalone sub-functions.

- _cmd_theme / _print_theme_preview / _cycle_theme
    _print_theme_preview and _cycle_theme both call _theme_ansi(), _e(), and
    _status_emoji(), which live in openclaw_cli.py and cannot be imported here
    without creating a circular import.

- _cmd_overlay / _interactive_overlays_enabled / _overlay_available
    Both helpers read _PREFS and sys.stdin.isatty(), which are compatible with
    our import constraints. However, the test suite monkeypatches
    openclaw_cli.sys.stdin and openclaw_cli._IS_TTY; moving these functions to
    a separate module would bypass those patches and fail four overlay tests.

- _cmd_accessibility / _a11y_* helpers
    _a11y_reduced_motion, _a11y_plain_mode, and _a11y_high_contrast are omitted
    per task constraints (they are settings command-domain helpers but are
    tightly coupled to the _A11Y_* constants defined in openclaw_cli.py).

Resolution: This module is retained as a placeholder for future extraction when
the overlay tests are refactored to patch at the module boundary instead of the
openclaw_cli namespace, or when a settings-specific sys/tty abstraction is
introduced in openclaw_cli_ui_core.
"""

from __future__ import annotations
