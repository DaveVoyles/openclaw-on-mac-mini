"""
openclaw_cli_macros — Macro recording, storage, and workflow execution engine.

Imports from: openclaw_cli_sessions (load_session), openclaw_cli_ui_core (ANSI colors)
Does NOT import from openclaw_cli.py.
"""
from __future__ import annotations

from typing import Any

try:
    from openclaw_cli_sessions import load_session
except ImportError:  # pragma: no cover
    load_session = None  # type: ignore[assignment]

try:
    from openclaw_cli_ui_core import _B, _CY, _DM, _GR, _R
except ImportError:  # pragma: no cover
    _B = _R = _CY = _GR = _DM = ""


def _workflow_store(prefs: dict) -> dict[str, list[str]]:
    """Return the workflow/macro store from prefs, initialising it if needed."""
    raw = prefs.setdefault("macros", {})
    if not isinstance(raw, dict):
        raw = {}
        prefs["macros"] = raw
    return raw


def _history_command_texts(prefs: dict, limit: int) -> list[str]:
    """Return the last *limit* command texts from cmd_history in prefs."""
    items = list(prefs.get("cmd_history", []))
    commands: list[str] = []
    for entry in items:
        if isinstance(entry, dict):
            text = str(entry.get("text", entry.get("prompt", entry.get("cmd", ""))) or "").strip()
        else:
            text = str(entry or "").strip()
        if text:
            commands.append(text)
    return commands[-max(1, limit):]


def _render_workflow_step(command: str, ctx: Any) -> str:
    """Substitute session placeholders in *command* using the session in *ctx*."""
    session = None
    if load_session is not None and getattr(ctx, "session_id", None):
        session = load_session(ctx.session_id)
    replacements = {
        "{session}": session.session_id if session else "",
        "{cwd}": session.cwd if session else "",
        "{plan}": session.plan_id if session else "",
        "{task}": session.task_id if session else "",
    }
    rendered = str(command or "")
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered


def _print_workflow_preview(name: str, steps: list[str], ctx: Any) -> None:
    """Print a dry-run preview of a workflow with placeholder substitution."""
    print(f"\n  {_B}Workflow preview '{name}'{_R}\n")
    for index, step in enumerate(steps, start=1):
        rendered = _render_workflow_step(step, ctx)
        print(f"  {_DM}{index:>2}{_R}  {_CY}{step}{_R}")
        if rendered != step:
            print(f"      {_DM}→ {rendered}{_R}")
    print(f"\n  {_DM}dry run only — use /workflow run {name} to execute.{_R}\n")


def _print_macro_progress(
    steps: list,
    current_idx: int,
    done_indices: set,
    a11y_plain: bool = False,
) -> None:
    """Print a live macro step progress tracker."""
    if a11y_plain:
        return
    total = len(steps)
    print()
    for i, step in enumerate(steps):
        step_str = str(step)[:50]
        if i in done_indices:
            marker = f"{_GR}✓{_R}"
            style = _DM
            end_style = _R
        elif i == current_idx:
            marker = f"{_CY}▸{_R}"
            style = _B
            end_style = _R
        else:
            marker = " "
            style = _DM
            end_style = _R
        num = f"Step {i+1}/{total}:"
        print(f"  {marker} {style}{num} {step_str}{end_style}")
    print()
