"""
openclaw_cli_ui_core — Terminal detection and ANSI palette.

Leaf module: imported by openclaw_cli.py and future render/commands modules.
No imports from other openclaw_cli_* modules.
"""

from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# Terminal detection
# ---------------------------------------------------------------------------

_IS_TTY: bool = sys.stdout.isatty()


def _c(code: str) -> str:
    """Return *code* only when stdout is a real terminal; empty string otherwise."""
    return code if _IS_TTY else ""


def _get_is_tty() -> bool:
    """Live TTY check — re-reads isatty() to handle tmux/iTerm late binding."""
    return _IS_TTY or sys.stdout.isatty()


# ---------------------------------------------------------------------------
# ANSI palette (pre-computed at import time for performance)
# ---------------------------------------------------------------------------

_R = _c("\033[0m")  # reset
_B = _c("\033[1m")  # bold
_DM = _c("\033[2m")  # dim
_CY = _c("\033[36m")  # cyan
_GR = _c("\033[32m")  # green
_YE = _c("\033[33m")  # yellow
_RE = _c("\033[31m")  # red
_MA = _c("\033[35m")  # magenta
_BCY = _c("\033[1;36m")  # bold cyan
_BGR = _c("\033[1;32m")  # bold green
_BYE = _c("\033[1;33m")  # bold yellow
_BRE = _c("\033[1;31m")  # bold red
_BBL = _c("\033[1;34m")  # bold blue
_IT = _c("\033[3m")  # italic
_UL = _c("\033[4m")  # underline
