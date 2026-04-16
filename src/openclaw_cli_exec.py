"""
openclaw_cli_exec — Shell command execution, progress animation, and footer rendering.

Imports from: openclaw_cli_ui_core (ANSI palette)
Does NOT import from openclaw_cli.py.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Any

try:
    from openclaw_cli_ui_core import (
        _B,
        _BYE,
        _CY,
        _DM,
        _GR,
        _R,
        _RE,
        _YE,
    )
except ImportError:
    _B = _BYE = _CY = _DM = _GR = _R = _RE = _YE = ""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MOTION_PACING_SECONDS: dict[str, float] = {
    "banner": 0.04,
    "separator": 0.03,
    "footer": 0.02,
}

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _separator_fill(width: int, *, high_contrast: bool = False, plain_mode: bool = False) -> str:
    """Return a separator line sized for the current terminal/mode."""
    char = "=" if high_contrast or plain_mode else "─"
    return char * max(1, width)


def _motion_pause(
    stage: str,
    *,
    is_tty: bool = True,
    plain_mode: bool = False,
    reduced_motion: bool = False,
) -> None:
    """Sleep briefly to stagger premium UI choreography when motion is enabled."""
    if not (is_tty and not plain_mode and not reduced_motion):
        return
    delay = float(_MOTION_PACING_SECONDS.get(stage, 0.0) or 0.0)
    if delay > 0:
        time.sleep(delay)


def _spinner_phase_label(elapsed: float) -> str:
    """Return a lightweight motion-language label for spinner pacing."""
    return _spinner_progress_snapshot(elapsed)["phase"]


def _spinner_progress_snapshot(elapsed: float) -> dict[str, Any]:
    """Return live phase/step copy for the request spinner."""
    if elapsed < 1.0:
        return {
            "phase": "warming up",
            "step_index": 1,
            "step_total": 3,
            "trust_copy": "preparing the request",
        }
    if elapsed < 4.0:
        return {
            "phase": "working",
            "step_index": 2,
            "step_total": 3,
            "trust_copy": "waiting for the agent response",
        }
    return {
        "phase": "wrapping up",
        "step_index": 3,
        "step_total": 3,
        "trust_copy": "finalizing the answer",
    }


def _response_footer_lines(
    *,
    elapsed: float = 0.0,
    tokens: int = 0,
    model: str = "",
    done_symbol: str = "✨",
) -> tuple[str, str]:
    """Return the footer headline and metadata line for a response."""
    parts: list[str] = []
    if elapsed > 0:
        parts.append(f"⏱ {elapsed:.1f}s")
    if tokens:
        parts.append(f"{tokens} tokens")
    if model:
        parts.append(model)
    detail = "  •  ".join(parts)
    if elapsed > 0:
        headline = f"{done_symbol} Response complete in {elapsed:.1f}s"
    else:
        headline = f"{done_symbol} Response complete"
    if tokens:
        headline += f" · {tokens} tokens"
    return headline, detail


# ---------------------------------------------------------------------------
# Progress bar / animation
# ---------------------------------------------------------------------------


def _progress_bar(current: int, total: int, width: int = 30, label: str = "") -> str:
    """Return a colored ANSI progress bar string."""
    if total <= 0:
        return ""
    pct = min(current / total, 1.0)
    filled = int(width * pct)
    empty = width - filled

    if pct < 0.33:
        color = _RE
    elif pct < 0.66:
        color = _YE
    else:
        color = _GR

    bar = f"{color}{'█' * filled}{_DM}{'░' * empty}{_R}"
    pct_str = f"{int(pct * 100):>3}%"
    if label:
        return f"  {bar} {_B}{pct_str}{_R}  {_DM}{label}{_R}"
    return f"  {bar} {_B}{pct_str}{_R}"


def _exec_progress_animate(
    proc: Any,
    label: str = "",
    *,
    is_tty: bool = True,
    plain_mode: bool = False,
    reduced_motion: bool = False,
) -> tuple:
    """Animate an indeterminate progress bar while proc runs. Returns (stdout, stderr, returncode)."""
    if not is_tty or reduced_motion or plain_mode:
        stdout, stderr = proc.communicate()
        return stdout, stderr, proc.returncode

    width = 30
    frames = []
    for pos in list(range(0, width - 8)) + list(range(width - 8, 0, -1)):
        bar = "░" * pos + "█████████" + "░" * (width - pos - 9)
        bar = bar[:width]
        frames.append(bar)

    frame_idx = 0
    done = threading.Event()
    stdout_buf: list[bytes] = []
    stderr_buf: list[bytes] = []
    rc_buf: list[int] = []

    def _run() -> None:
        o, e = proc.communicate()
        stdout_buf.append(o)
        stderr_buf.append(e)
        rc_buf.append(proc.returncode)
        done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    start = time.time()
    while not done.is_set():
        elapsed = time.time() - start
        frame = frames[frame_idx % len(frames)]
        elapsed_str = f"{elapsed:.1f}s"
        sys.stdout.write(f"\r  {_CY}{frame}{_R}  {_DM}{elapsed_str}  {label}{_R}")
        sys.stdout.flush()
        frame_idx += 1
        done.wait(0.08)

    sys.stdout.write(f"\r{' ' * 60}\r")
    sys.stdout.flush()

    return stdout_buf[0] if stdout_buf else b"", stderr_buf[0] if stderr_buf else b"", rc_buf[0] if rc_buf else -1


# ---------------------------------------------------------------------------
# Error analysis and display
# ---------------------------------------------------------------------------


def _analyze_exec_error(cmd: str, stderr: str, returncode: int) -> list[str]:
    """Analyze a failed command and return smart recovery hints."""
    if returncode == 0:
        return []
    import re as _re

    hints: list[str] = []
    err_lower = (stderr or "").lower()
    cmd_lower = (cmd or "").lower()

    if "permission denied" in err_lower:
        hints.append("Try: sudo " + cmd.strip())
        hints.append("Or: chmod +x <file> if it's a script")

    if "command not found" in err_lower or "not found" in err_lower:
        first_word = cmd.strip().split()[0] if cmd.strip() else ""
        if first_word:
            hints.append(f"Install {first_word}? Try: brew install {first_word} or pip install {first_word}")
        hints.append("Check PATH: echo $PATH")

    if "modulenotfounderror" in err_lower or "no module named" in err_lower:
        m = _re.search(r"no module named '([^']+)'", err_lower)
        if m:
            mod_name = m.group(1)
            hints.append(f"Install missing module: pip install {mod_name}")
        hints.append("Check virtual environment: which python3")

    if "address already in use" in err_lower or ("port" in err_lower and "use" in err_lower):
        hints.append("Port already in use — try a different port or: lsof -i :<port>")
        hints.append("Kill process: kill $(lsof -t -i :<port>)")

    if "no such file or directory" in err_lower:
        hints.append("Check file path: ls -la")
        hints.append("Create missing dirs: mkdir -p <path>")

    if "timeout" in err_lower or "connection refused" in err_lower:
        hints.append("Service may be down — check: docker ps or systemctl status")
        hints.append("Try: /exec curl -s http://localhost:PORT/health")

    if "docker" in cmd_lower and ("error" in err_lower or returncode != 0):
        hints.append("Check Docker status: docker ps")
        hints.append("View logs: docker logs <container>")

    if not hints:
        if returncode == 1:
            hints.append("Exit code 1 — general error. Check stderr above.")
        elif returncode == 2:
            hints.append("Exit code 2 — misuse of command or bad arguments.")
        elif returncode == 127:
            hints.append("Exit code 127 — command not found. Check PATH.")
        elif returncode == 130:
            hints.append("Exit code 130 — interrupted by Ctrl+C.")
        else:
            hints.append(f"Exit code {returncode} — see stderr for details.")

    return hints[:3]


def _print_exec_error_hints(
    cmd: str,
    stderr: str,
    returncode: int,
    *,
    plain_mode: bool = False,
    is_tty: bool = True,
) -> None:
    """Print smart recovery hints after a failed exec command."""
    if plain_mode:
        return
    hints = _analyze_exec_error(cmd, stderr, returncode)
    if not hints:
        return

    _rich_ok = False
    if is_tty:
        try:
            from rich.console import Console as _RichConsole

            _console = _RichConsole()
            _console.print("\n[bold yellow]💡 Recovery hints:[/]")
            for hint in hints:
                _console.print(f"  [dim]→[/] {hint}")
            _console.print()
            _rich_ok = True
        except (ImportError, OSError, AttributeError):
            pass

    if not _rich_ok:
        print(f"\n{_BYE}💡 Recovery hints:{_R}")
        for hint in hints:
            print(f"  {_DM}→{_R} {hint}")
        print()
