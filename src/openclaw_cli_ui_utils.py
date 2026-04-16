"""
openclaw_cli_ui_utils — UI utility functions: spinner, banner, status bar, celebrations.

Extracted from openclaw_cli.py.

Imports from:
  - openclaw_cli_ui_core   (ANSI palette, TTY detection — leaf module)
  - openclaw_cli_prefs     (user preferences — leaf module)
  - openclaw_cli_exec      (spinner helpers — no openclaw_cli dependency)
  - openclaw_cli_update    (cli_version — leaf module)
  - openclaw_cli_router    (_session_auto_route_enabled — no openclaw_cli dependency)
  - openclaw_cli_session_display (_progress_cell — no openclaw_cli dependency)
  - openclaw_cli_session_cmds    (_build_workspace_capsule_plain_lines — no openclaw_cli dependency)
  - stdlib only elsewhere

Does NOT import from openclaw_cli.py at module level (avoids circular imports).
Functions that still live in openclaw_cli.py (_theme_ansi, _print_feedback) are
lazy-imported at call time — by that point openclaw_cli.py is fully loaded.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from openclaw_cli_exec import _spinner_progress_snapshot
from openclaw_cli_prefs import (
    _A11Y_HIGH_CONTRAST,
    _A11Y_PLAIN_MODE,
    _A11Y_REDUCED_MOTION,
    _EMOJI_PACKS,
    _PREFS,
    _emoji_pack_name,
)
from openclaw_cli_router import _session_auto_route_enabled
from openclaw_cli_session_cmds import _build_workspace_capsule_plain_lines
from openclaw_cli_session_display import _progress_cell
from openclaw_cli_ui_core import (
    _B,
    _BBL,
    _BCY,
    _BGR,
    _BYE,
    _CY,
    _DM,
    _GR,
    _IS_TTY,
    _MA,
    _R,
    _RE,
    _YE,
    _get_is_tty,
)
from openclaw_cli_update import cli_version

if TYPE_CHECKING:
    from openclaw_cli import CliConfig

# ---------------------------------------------------------------------------
# Rich — graceful fallback when not installed
# ---------------------------------------------------------------------------
try:
    from rich.console import Console as _RichConsole
    from rich.panel import Panel as _RichPanel
    from rich.table import Table as _RichTable
    from rich.text import Text as _RichText

    _RICH_CONSOLE = _RichConsole(highlight=False)
    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RICH_AVAILABLE = False

# ---------------------------------------------------------------------------
# Local constants
# ---------------------------------------------------------------------------

_SPINNER_HEARTBEAT_SECONDS = 4.0

# Emoji fallbacks (subset used by this module)
_EMOJI_FALLBACKS: dict[str, str] = {
    "🦞": "[openclaw]",
    "💬": ">>",
    "📍": "@",
    "💡": "[hint]",
    "📎": "[src]",
    "⌨": "[ctrl-c]",
    "⏱": "[time]",
    "🗂": "[session]",
    "👤": "[user]",
    "⚡": "!",
}

# ---------------------------------------------------------------------------
# Local accessibility helpers (mirror openclaw_cli.py — read from _PREFS)
# ---------------------------------------------------------------------------


def _a11y_plain_mode() -> bool:
    return bool(_PREFS.get(_A11Y_PLAIN_MODE, False))


def _a11y_high_contrast() -> bool:
    return bool(_PREFS.get(_A11Y_HIGH_CONTRAST, False))


def _a11y_reduced_motion() -> bool:
    return bool(_PREFS.get(_A11Y_REDUCED_MOTION, False))


def _terminal_width(*, fallback: int = 80) -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return fallback


def _e(emoji: str, fallback: str = "") -> str:
    """Return *emoji* or its ASCII fallback depending on the emoji pref."""
    pack = _emoji_pack_name()
    if pack == "classic":
        return emoji
    if pack == "minimal":
        return _EMOJI_PACKS["minimal"].get(emoji, fallback or _EMOJI_FALLBACKS.get(emoji, ""))
    return fallback or _EMOJI_FALLBACKS.get(emoji, "")


def _time_greeting() -> str:
    """Return a time-of-day greeting with emoji."""
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "Good morning 🌅"
    elif 12 <= hour < 17:
        return "Good afternoon ☀️"
    elif 17 <= hour < 21:
        return "Good evening 🌙"
    else:
        return "Hello 🦞"


def _motion_pause(stage: str) -> None:
    """Delegate to openclaw_cli_exec._motion_pause with live accessibility state."""
    from openclaw_cli_exec import _motion_pause as _exec_motion_pause  # noqa: PLC0415
    _exec_motion_pause(
        stage,
        is_tty=_get_is_tty(),
        plain_mode=_a11y_plain_mode(),
        reduced_motion=_a11y_reduced_motion(),
    )


# ---------------------------------------------------------------------------
# Exported UI utility functions
# ---------------------------------------------------------------------------


def _with_spinner(
    label: str,
    fn: Any,
    *args: Any,
    output_json: bool = False,
    _override_is_tty: bool | None = None,
    _override_heartbeat_secs: float | None = None,
    **kwargs: Any,
) -> Any:
    """Run *fn* in a background thread while showing an animated braille spinner.

    Falls back to a direct call when output is not a TTY or when --json output
    is requested so that machine-readable output is never corrupted.

    When reduced-motion mode is active, skips the animation and prints a single
    static "thinking..." line instead, then runs *fn* directly.

    The ``_override_is_tty`` and ``_override_heartbeat_secs`` parameters are
    internal hooks used by the ``openclaw_cli`` shim so that monkeypatched
    module attributes in tests are forwarded correctly.
    """
    is_tty = _override_is_tty if _override_is_tty is not None else _get_is_tty()
    if not (is_tty and not output_json):
        return fn(*args, **kwargs)

    result_holder: list[Any] = []
    exc_holder: list[BaseException] = []

    def _run() -> None:
        try:
            result_holder.append(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            exc_holder.append(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    start = time.monotonic()
    heartbeat_secs = _override_heartbeat_secs if _override_heartbeat_secs is not None else _SPINNER_HEARTBEAT_SECONDS
    heartbeat_every = max(0.01, float(heartbeat_secs))

    # Reduced-motion path: no animation, but still emit periodic liveness cues.
    if _a11y_reduced_motion():
        snapshot = _spinner_progress_snapshot(0.0)
        # Lazy import to avoid circular dependency
        from openclaw_cli import _print_feedback, _theme_ansi  # noqa: PLC0415
        prefix = "[working]" if _a11y_plain_mode() else f"{_theme_ansi()}{_e('⏳', '[working]')}{_R}"
        status_style = "" if (_a11y_plain_mode() or _a11y_high_contrast()) else _DM
        sys.stdout.write(
            f"  {prefix} {status_style}{label}... "
            f"{snapshot['phase']} · step {snapshot['step_index']}/{snapshot['step_total']} · "
            f"{snapshot['trust_copy']}{_R if status_style else ''}\n"
        )
        sys.stdout.flush()
        last_heartbeat = 0.0
        join_timeout = min(0.1, heartbeat_every / 2.0)
        while thread.is_alive():
            thread.join(timeout=join_timeout)
            elapsed = time.monotonic() - start
            if elapsed - last_heartbeat >= heartbeat_every:
                snapshot = _spinner_progress_snapshot(elapsed)
                _print_feedback(
                    f"Still working on {label}",
                    level="info",
                    detail=(
                        f"phase {snapshot['step_index']}/{snapshot['step_total']} · "
                        f"{snapshot['trust_copy']} · {elapsed:.0f}s elapsed"
                    ),
                )
                last_heartbeat = elapsed
        if exc_holder:
            raise exc_holder[0]
        snapshot = _spinner_progress_snapshot(max(time.monotonic() - start, 4.0))
        _print_feedback(
            "response ready.",
            level="success",
            detail=(
                f"step {snapshot['step_total']}/{snapshot['step_total']} · "
                f"{snapshot['trust_copy']} · {label} · {time.monotonic() - start:.1f}s"
            ),
        )
        return result_holder[0] if result_holder else None

    spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    frame_idx = 0
    last_heartbeat = 0.0
    while thread.is_alive():
        elapsed = time.monotonic() - start
        frame = spinner_frames[frame_idx % len(spinner_frames)]
        snapshot = _spinner_progress_snapshot(elapsed)
        extra = " · still working" if elapsed - last_heartbeat >= heartbeat_every else ""
        sys.stdout.write(
            f"\r{frame} {label} · {snapshot['phase']} · "
            f"step {snapshot['step_index']}/{snapshot['step_total']} · "
            f"{snapshot['trust_copy']}  {elapsed:.0f}s{extra}"
        )
        sys.stdout.flush()
        frame_idx += 1
        if extra:
            last_heartbeat = elapsed
        time.sleep(0.1)

    thread.join()
    # Clear the spinner line.
    sys.stdout.write("\r" + " " * (len(label) + 20) + "\r")
    sys.stdout.flush()

    if exc_holder:
        raise exc_holder[0]
    # Lazy import to avoid circular dependency
    from openclaw_cli import _print_feedback  # noqa: PLC0415
    snapshot = _spinner_progress_snapshot(max(time.monotonic() - start, 4.0))
    _print_feedback(
        "response ready.",
        level="success",
        detail=(
            f"step {snapshot['step_total']}/{snapshot['step_total']} · "
            f"{snapshot['trust_copy']} · {label} · {time.monotonic() - start:.1f}s"
        ),
    )
    return result_holder[0]


def _print_startup_banner(config: "CliConfig", session_id: str) -> None:
    """Print a colored startup banner for the interactive REPL."""
    autoroute_on = _session_auto_route_enabled(session_id)
    ver = cli_version()
    cols = _terminal_width()

    # Compute session milestone (best-effort)
    _milestone = None
    _session_count = 0
    try:
        from openclaw_cli_sessions import list_sessions as _list_sessions  # noqa: PLC0415
        _session_count = len(_list_sessions(limit=1001))
        for m in (10, 50, 100, 250, 500, 1000):
            if _session_count == m:
                _milestone = m
                break
    except Exception:  # noqa: BLE001  # broad: intentional
        pass

    # Plain-mode path: no ANSI, no emoji, no decorative borders.
    if _a11y_plain_mode() or cols < 40:
        autoroute_str = "on" if autoroute_on else "off"
        print(f"🦞 OpenClaw {ver}")
        print(_time_greeting())
        print(f"Server: {config.base_url}")
        print(f"User: {config.user_name}")
        if session_id:
            print(f"Session: {session_id[:8]}…")
        print("Type /help for commands. /quit to exit.")
        print(f"Auto-routing: {autoroute_str}")
        if _milestone:
            print(f"  🎉 {_milestone} sessions with OpenClaw! That's a milestone!")
        return

    if _RICH_AVAILABLE and _IS_TTY:
        t = _RichText()
        t.append(f"{_e('🦞', '[openclaw]')} OpenClaw", style="bold cyan")
        t.append(f"  {ver}", style="cyan dim")
        t.append("  connected to ", style="dim")
        t.append(config.base_url, style="cyan")
        t.append(f"\n  {_time_greeting()}", style="dim")
        t.append(f"\n  {_e('👤', '[user]')} ", style="dim")
        t.append(config.user_name, style="bold green")
        if session_id:
            t.append(f"  ·  {_e('🗂', '[session]')}  session: ", style="dim")
            t.append(session_id[:8] + "…", style="yellow")
        t.append("\n\n  ", style="")
        t.append("Type anything to chat", style="dim")
        t.append(" · ", style="dim")
        t.append("/help", style="bold cyan")
        t.append(" for commands", style="dim")
        t.append(" · ", style="dim")
        t.append("/quit", style="bold cyan")
        t.append(" to exit", style="dim")
        t.append(" · ", style="dim")
        t.append("Tab", style="bold")
        t.append(" completes /commands", style="dim")
        t.append("\n  ", style="")
        t.append("Auto-routing", style="bold")
        if autoroute_on:
            t.append(" is on — smart prompts route to analyze/research/exec automatically", style="dim")
        else:
            t.append(" is off", style="dim yellow")
            t.append(" — use /autoroute on to enable", style="dim")
        _RICH_CONSOLE.print(
            _RichPanel(
                t,
                border_style="bold white" if _a11y_high_contrast() else "cyan",
                padding=(0, 1),
            )
        )
        if _milestone:
            _RICH_CONSOLE.print(f"  🎉 [bold cyan]{_milestone} sessions with OpenClaw![/] [dim]That's a milestone![/]")
        _motion_pause("banner")
    else:
        session_line = (
            f"\n  {_DM}{_e('🗂', '[session]')}  session:{_R}  {_YE}{session_id[:8]}…{_R}" if session_id else ""
        )
        if autoroute_on:
            autoroute_line = f"\n  {_B}Auto-routing{_R} {_DM}is on — smart prompts route to analyze/research/exec automatically{_R}"
        else:
            autoroute_line = f"\n  {_B}Auto-routing{_R} {_YE}is off{_R} {_DM}— use /autoroute on to enable{_R}"
        print(
            f"\n{_BCY}{_e('🦞', '[openclaw]')} OpenClaw{_R}  {_DM}{ver}{_R}"
            f"\n  {_DM}{_time_greeting()}{_R}"
            f"\n  {_DM}connected to{_R}  {_CY}{config.base_url}{_R}"
            f"\n  {_DM}{_e('👤', '[user]')} user:{_R}      {_BGR}{config.user_name}{_R}"
            f"{session_line}"
            f"\n"
            f"\n  Type anything to chat · {_BCY}/help{_R} for commands · {_BCY}/quit{_R} to exit · {_B}Tab{_R}{_DM} completes /commands{_R}"
            f"{autoroute_line}\n"
        )
        if _milestone:
            print(f"  🎉 {_BCY}{_milestone} sessions with OpenClaw!{_R} {_DM}That's a milestone!{_R}")


def _print_status_bar(
    *,
    session_id: str = "",
    autoroute_on: bool = True,
    history_len: int = 0,
    _override_is_tty: bool | None = None,
    _override_rich_available: bool | None = None,
    _override_cols: int | None = None,
) -> None:
    """Print a compact dim status line below the response.

    Shows session, context size, and autoroute state so the user always has
    situational awareness without cluttering the response output itself.

    The ``_override_*`` parameters are internal hooks so that monkeypatched
    module attributes in tests are forwarded correctly from the openclaw_cli shim.
    """
    is_tty = _override_is_tty if _override_is_tty is not None else _get_is_tty()
    if not is_tty:
        return
    cols = _override_cols if _override_cols is not None else _terminal_width()
    rich_available = _override_rich_available if _override_rich_available is not None else _RICH_AVAILABLE
    narrow = cols < 60
    parts: list[str] = []
    if session_id:
        short = session_id[:6] if narrow else session_id[:10]
        parts.append(f"{_e('📍', '@')} {short}…")
    turns = history_len // 2  # history contains alternating user/assistant pairs
    if turns and not narrow:
        parts.append(f"{_e('💬', 'msgs:')} {turns} turn{'s' if turns != 1 else ''}")
    autoroute_state = "on" if autoroute_on else "off"
    if _a11y_plain_mode():
        parts.append(f"autoroute {autoroute_state}")
        print("Status: " + " | ".join(parts))
        return
    if _a11y_high_contrast():
        color = "\033[1;92m" if autoroute_on else "\033[1;93m"
    else:
        color = "\033[32m" if autoroute_on else "\033[33m"
    parts.append(f"autoroute {color}{autoroute_state}{_R}")
    if narrow:
        for idx, part in enumerate(parts):
            prefix = "Status:" if idx == 0 else "       "
            print(f"  {prefix} {part}")
    else:
        line = "  ·  ".join(parts)
        if rich_available and is_tty:
            style = "bold white" if _a11y_high_contrast() else "dim"
            _RICH_CONSOLE.print(f"[{style}]  {line}[/]")
        else:
            # Lazy import to avoid circular dependency
            from openclaw_cli import _theme_ansi  # noqa: PLC0415
            style = _theme_ansi() if _a11y_high_contrast() else _DM
            reset = _R if style else ""
            print(f"  {style}{line}{reset}")


def _print_shell_top_bar(
    *,
    session_id: str = "",
    model_name: str = "",
    autoroute_on: bool = True,
    watch_active: bool = False,
    _override_is_tty: bool | None = None,
    _override_rich_available: bool | None = None,
    _override_cols: int | None = None,
) -> None:
    """Print the always-on top context bar (session · model · autoroute · watch).

    Shown once after the startup banner and again after each AI response so the
    user always knows which session, model, and routing state they are in.

    Degradation:
    - Rich + TTY  → dim unicode bar with ``╸`` accent
    - ANSI TTY    → dim ANSI bar
    - Plain mode / non-TTY → ``---`` text separator
    - Narrow (<60 cols)    → compact single-line form

    The ``_override_*`` parameters are internal hooks so that monkeypatched
    module attributes in tests are forwarded correctly from the openclaw_cli shim.
    """
    is_tty = _override_is_tty if _override_is_tty is not None else _get_is_tty()
    cols = _override_cols if _override_cols is not None else _terminal_width()
    rich_available = _override_rich_available if _override_rich_available is not None else _RICH_AVAILABLE
    narrow = cols < 60

    # Build badge parts -------------------------------------------------------
    parts: list[str] = []
    if session_id:
        short = session_id[:6] if narrow else session_id[:12]
        parts.append(f"session: {short}…")
    if model_name and not narrow:
        parts.append(f"model: {model_name}")
    autoroute_state = "on" if autoroute_on else "off"
    if narrow:
        parts.append(f"ar:{autoroute_state}")
    else:
        parts.append(f"autoroute: {autoroute_state}")
    if watch_active:
        parts.append("watch: active")

    separator = " · "
    text = separator.join(parts)

    # Plain mode / non-TTY ----------------------------------------------------
    if _a11y_plain_mode() or not is_tty:
        if is_tty:
            print("--- " + " | ".join(parts) + " ---")
        return

    # Rich path ---------------------------------------------------------------
    if rich_available:
        _RICH_CONSOLE.print(f"[dim]╸ {text}[/]")
        return

    # ANSI fallback -----------------------------------------------------------
    print(f"  {_DM}╸ {text}{_R}")


def _print_shell_bottom_bar(
    *,
    mode: str = "chat",
    hints: list[str] | None = None,
    _override_is_tty: bool | None = None,
    _override_rich_available: bool | None = None,
    _override_cols: int | None = None,
) -> None:
    """Print the always-on bottom control bar before the REPL prompt.

    Shows the current mode and 1–2 inline hint commands so the user always
    has a quick reference for how to navigate.

    Degradation:
    - Rich + TTY  → dim unicode bar with ``╸`` accent
    - ANSI TTY    → dim ANSI bar
    - Plain mode / non-TTY → simple text separator
    - Narrow (<60 cols)    → collapse to minimal hints only

    The ``_override_*`` parameters are internal hooks so that monkeypatched
    module attributes in tests are forwarded correctly from the openclaw_cli shim.
    """
    is_tty = _override_is_tty if _override_is_tty is not None else _get_is_tty()
    cols = _override_cols if _override_cols is not None else _terminal_width()
    rich_available = _override_rich_available if _override_rich_available is not None else _RICH_AVAILABLE
    narrow = cols < 60

    # Build hint parts --------------------------------------------------------
    effective_hints: list[str] = list(hints or [])
    if not effective_hints:
        if narrow:
            effective_hints = ["/help", "/quit"]
        else:
            effective_hints = ["/help for commands", "/quit to exit", "Tab completes"]

    mode_label = f"mode: {mode}" if mode else ""
    separator = " · "
    hint_text = separator.join(effective_hints)
    full_text = (mode_label + separator + hint_text) if mode_label else hint_text

    # Plain mode / non-TTY ----------------------------------------------------
    if _a11y_plain_mode() or not is_tty:
        if is_tty:
            print("--- " + " | ".join(([mode_label] if mode_label else []) + effective_hints) + " ---")
        return

    # Rich path ---------------------------------------------------------------
    if rich_available:
        _RICH_CONSOLE.print(f"[dim]╸ {full_text}[/]")
        return

    # ANSI fallback -----------------------------------------------------------
    print(f"  {_DM}╸ {full_text}{_R}")


def _celebration_burst(message: str = "") -> None:
    """Print a short animated celebration burst (confetti + message)."""
    import random  # noqa: PLC0415
    is_tty = _get_is_tty()
    if not is_tty or _a11y_reduced_motion() or _a11y_plain_mode():
        if message:
            print(f"🎉 {message}")
        return

    confetti_chars = ["✦", "✧", "★", "◆", "◇", "❋", "✿", "❀", "🎊", "🎉", "⭐", "💫"]
    colors = [_RE, _YE, _GR, _CY, _MA, _BBL]

    width = 60

    for frame in range(3):
        line1 = ""
        line2 = ""
        for _ in range(width // 3):
            char = random.choice(confetti_chars)
            color = random.choice(colors)
            line1 += f"{color}{char}{_R} "
        for _ in range(width // 3):
            char = random.choice(confetti_chars)
            color = random.choice(colors)
            line2 += f"{color}{char}{_R} "
        sys.stdout.write(f"\r  {line1}\n  {line2}\n")
        sys.stdout.flush()
        time.sleep(0.15)
        sys.stdout.write("\033[2A")
        sys.stdout.flush()

    sys.stdout.write(f"\r{' ' * 80}\n{' ' * 80}\n")
    sys.stdout.write("\033[2A")

    stars = "⭐" * 5
    if _RICH_AVAILABLE:
        _RICH_CONSOLE.print(f"\n  [bold yellow]{stars}  {message or 'Perfect rating!'}  {stars}[/]\n")
    else:
        print(f"\n  {_BYE}{stars}  {message or 'Perfect rating!'}  {stars}{_R}\n")


def _print_workspace_capsule(capsule: dict[str, Any], *, title: str = "Workspace Capsule") -> None:
    """Render a compact workspace recovery summary."""
    tracked_files = list(capsule.get("tracked_files") or [])
    bookmarks = list(capsule.get("bookmarks") or [])
    recent_outputs = list(capsule.get("recent_outputs") or [])
    if _RICH_AVAILABLE and _IS_TTY:
        lines = [
            f"cwd: {capsule.get('cwd', '')}",
            _progress_cell("files", str(capsule.get("tracked_file_count", len(tracked_files))), status="active" if tracked_files else "idle"),
            _progress_cell("bookmarks", str(capsule.get("bookmark_count", len(bookmarks))), status="complete" if bookmarks else "idle"),
            _progress_cell("outputs", str(capsule.get("output_count", len(recent_outputs))), status="complete" if recent_outputs else "idle"),
        ]
        watch_status = str(capsule.get("watch_status") or "").strip()
        if watch_status:
            lines.append(_progress_cell("watch", watch_status, status="active" if watch_status not in {"idle", "waiting"} else "idle"))
        signature = str(capsule.get("workspace_signature") or "").strip()
        if signature:
            lines.append(f"signature: {signature}")
        if capsule.get("plan_id"):
            lines.append(f"plan: {capsule.get('plan_id')}")
        if capsule.get("task_id"):
            lines.append(f"task: {capsule.get('task_id')}")
        if recent_outputs:
            lines.append("recent outputs:")
            lines.extend(f"  - {item.get('name', '')}" for item in recent_outputs[:3])
        if bookmarks:
            lines.append("recent bookmarks:")
            lines.extend(f"  - [{item.get('id', '')}] {item.get('label', '')}" for item in bookmarks[-3:])
        grid = _RichTable.grid(padding=(0, 1))
        grid.add_column()
        for line in lines:
            grid.add_row(str(line))
        _RICH_CONSOLE.print(_RichPanel(grid, title=f"[bold cyan]{title}[/]", border_style="cyan", padding=(0, 1)))
    else:
        plain_lines = _build_workspace_capsule_plain_lines(capsule)
        print(title)
        print("-" * len(title))
        for line in plain_lines:
            print(line)
