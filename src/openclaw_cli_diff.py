"""
openclaw_cli_diff — Diff colorization for unified diff output.

Imports from: openclaw_cli_ui_core (ANSI constants)
Does NOT import from: openclaw_cli.py (no circular deps)
"""
from __future__ import annotations

try:
    from openclaw_cli_ui_core import _B, _CY, _DM, _GR, _R, _RE
except ImportError:
    _B = _R = _GR = _RE = _CY = _DM = ""


def _render_diff_ansi(diff_text: str, *, plain_mode: bool = False) -> str:
    """Apply ANSI colors to unified diff output (+ green, - red, @@ cyan)."""
    if plain_mode:
        return diff_text
    lines = diff_text.split("\n")
    result = []
    for line in lines:
        if line.startswith("+++") or line.startswith("---"):
            result.append(f"{_B}{line}{_R}")
        elif line.startswith("@@"):
            result.append(f"{_CY}{line}{_R}")
        elif line.startswith("+"):
            result.append(f"{_GR}{line}{_R}")
        elif line.startswith("-"):
            result.append(f"{_RE}{line}{_R}")
        else:
            result.append(f"{_DM}{line}{_R}")
    return "\n".join(result)
