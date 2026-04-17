"""
openclaw_cli_cmd_core.py — Core system, file, and AI command handlers.

Extracted from openclaw_cli.py (TD-33).
Handlers: _cmd_exporttemplates, _cmd_runbook, _cmd_help, _cmd_clear,
          _cmd_context, _cmd_cwd, _cmd_files, _cmd_routing, _cmd_why,
          _cmd_trace, _cmd_autoroute, _cmd_snapshot, _cmd_rollback,
          _cmd_analyze, _cmd_research, _cmd_write, _cmd_exec, _cmd_edit,
          _cmd_update, _cmd_version, _cmd_draft, _cmd_template, _cmd_inject,
          _cmd_tokeninfo.

All openclaw_cli.py globals and functions are accessed via _get_cli_mod() to
respect test monkeypatching.  Direct imports are only taken from leaf modules
that have no circular dependency on openclaw_cli.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from openclaw_cli_actions import (
    _print_usage,
    infer_command_risk,
    infer_file_edit_risk,
    replace_text_in_file,
    write_text_file,
)
from openclaw_cli_auth import OpenClawCliError
from openclaw_cli_exec import (
    _exec_progress_animate as _exec_animate_fn,
)
from openclaw_cli_session_display import _context_pressure_snapshot
from openclaw_cli_sessions import (
    load_conversation_history,
    restore_last_routed_action_checkpoint,
    update_session,
)
from openclaw_cli_types import ChatCommandContext
from openclaw_cli_ui_core import (
    _B,
    _CY,
    _DM,
    _GR,
    _IS_TTY,
    _R,
    _RE,
    _YE,
    _get_is_tty,
)
from openclaw_cli_ui_utils import _e
from openclaw_cli_update import (
    _standalone_install_dir,
    _update_standalone_install,
    cli_version,
    handle_update_command,
)

try:
    from rich.console import Console as _RichConsole
    from rich.panel import Panel as _RichPanel
    from rich.table import Table as _RichTable
    from rich.text import Text as _RichText

    _RICH_CONSOLE = _RichConsole(highlight=False)
    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RICH_CONSOLE = None  # type: ignore[assignment]
    _RICH_AVAILABLE = False

# Sentinel strings — mirror openclaw_cli._CMD_CONTINUE / _CMD_QUIT.
_CMD_CONTINUE: str = "continue"
_CMD_QUIT: str = "quit"


def _get_cli_mod() -> Any:
    """Lazy import of main module for monkeypatch-safe back-references."""
    import openclaw_cli as _m  # noqa: PLC0415
    return _m


# ---------------------------------------------------------------------------
# _cmd_exporttemplates
# ---------------------------------------------------------------------------

def _cmd_exporttemplates(ctx: ChatCommandContext) -> str:
    """/exporttemplates [list|show <name>] — inspect built-in runbook/export templates."""
    m = _get_cli_mod()
    raw = (ctx.args or "").strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else "list"

    if sub in {"", "list"}:
        if _RICH_AVAILABLE and _IS_TTY:
            tbl = _RichTable(title="Export Templates", border_style="cyan", header_style="bold cyan")
            tbl.add_column("Name", style="bold")
            tbl.add_column("Audience", style="dim")
            tbl.add_column("Sections")
            for name, template in sorted(m._RUNBOOK_TEMPLATES.items()):
                sections = ", ".join(str(s) for s in template.get("sections", ()))
                tbl.add_row(name, str(template.get("audience", "")), sections)
            _RICH_CONSOLE.print(tbl)
        else:
            print("Export templates:")
            for name, template in sorted(m._RUNBOOK_TEMPLATES.items()):
                sections = ", ".join(str(s) for s in template.get("sections", ()))
                print(f"  {name}: {template.get('audience', '')} — {sections}")
        return _CMD_CONTINUE

    if sub == "show":
        name = parts[1].strip() if len(parts) > 1 else ""
        resolved = m._resolve_runbook_template(name)
        if resolved is None:
            valid = ", ".join(sorted(m._RUNBOOK_TEMPLATES))
            m._print_error(f"Unknown export template '{name}'. Available: {valid}")
            return _CMD_CONTINUE
        template_key, template = resolved
        sections = ", ".join(str(s) for s in template.get("sections", ()))
        print(f"Template: {template_key}")
        print(f"Audience: {template.get('audience', '')}")
        print(f"Sections: {sections}")
        return _CMD_CONTINUE

    m._print_error("Usage: /exporttemplates [list|show <name>]")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_runbook
# ---------------------------------------------------------------------------

def _cmd_runbook(ctx: ChatCommandContext) -> str:
    """/runbook [template] [save <path>] — render a long-form session runbook."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    raw = (ctx.args or "").strip()
    parts = shlex.split(raw) if raw else []
    template_name = "operator"
    save_path = ""

    if parts and parts[0].lower() == "list":
        return _cmd_exporttemplates(ChatCommandContext(history=ctx.history, session_id=ctx.session_id, args="list"))
    if parts and parts[0].lower() != "save":
        template_name = parts.pop(0)
    if parts:
        if parts[0].lower() != "save" or len(parts) < 2:
            m._print_error("Usage: /runbook [template] [save <path>]")
            return _CMD_CONTINUE
        save_path = parts[1]

    try:
        content = m._build_session_runbook_text(session.session_id, template_name=template_name)
    except OpenClawCliError as exc:
        m._print_error(str(exc))
        return _CMD_CONTINUE

    if save_path:
        target = Path(save_path).expanduser()
        if not target.suffix:
            target = target.with_suffix(".md")
        target.write_text(content, encoding="utf-8")
        print(f"Runbook saved → {target.resolve()}")
        return _CMD_CONTINUE

    print(content.rstrip())
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_help
# ---------------------------------------------------------------------------

def _cmd_help(ctx: ChatCommandContext) -> str:
    m = _get_cli_mod()
    token = ctx.args.strip().lower()
    if token.startswith("search "):
        m.print_chat_help(search=token[7:].strip())
    else:
        m.print_chat_help()
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_clear
# ---------------------------------------------------------------------------

def _cmd_clear(ctx: ChatCommandContext) -> str:
    m = _get_cli_mod()
    n = len(ctx.history)
    ctx.history.clear()
    if ctx.session_id:
        m.append_event(
            ctx.session_id,
            kind="chat",
            content="/clear",
            metadata={"summary": "cleared chat history"},
        )
    m._print_feedback("Conversation history cleared.", level="success", detail=f"{n} message(s) removed")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_context
# ---------------------------------------------------------------------------

def _cmd_context(ctx: ChatCommandContext) -> str:
    """/context [last] — show the effective local grounding for the active session."""
    m = _get_cli_mod()
    arg = (ctx.args or "").strip().lower()
    if arg in {"last", "grounding"}:
        last_block = m._PREFS.get("_last_grounding_block")
        if not last_block:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No grounding block recorded yet — run /analyze, /research, or /write first.[/]")
            else:
                print("  No grounding block recorded yet — run /analyze, /research, or /write first.")
        else:
            if _RICH_AVAILABLE and _IS_TTY:
                grid = _RichTable.grid(padding=(0, 1))
                grid.add_column(style="bold cyan", no_wrap=True)
                grid.add_column()
                grid.add_row("Type:", str(last_block.get("type") or "(unknown)"))
                grid.add_row("Query / subject:", str(last_block.get("query") or "(none)"))
                grid.add_row("Confidence boost:", "yes" if last_block.get("grounded") else "none")
                grid.add_row("Rationale:", str(last_block.get("rationale") or "(none)")[:300])
                _RICH_CONSOLE.print(_RichPanel(grid, title="[bold cyan]Last Grounding Block[/]", border_style="dim", padding=(0, 1)))
            else:
                print("  Last grounding block (analyze/research/write):")
                print(f"    Type: {last_block.get('type') or '(unknown)'}")
                print(f"    Query / subject: {last_block.get('query') or '(none)'}")
                print(f"    Confidence boost: {'yes' if last_block.get('grounded') else 'none'}")
                print(f"    Rationale: {str(last_block.get('rationale') or '(none)')[:300]}")
        return _CMD_CONTINUE
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    sys_prompt = m._PREFS.get("system_prompt", "").strip()
    pending_inject = str(getattr(m, "_next_inject", "") or "")
    pressure = _context_pressure_snapshot(
        ctx.history,
        system_prompt=sys_prompt,
        pending_inject=pending_inject,
        model_hint=m._PREFS.get("last_model", ""),
        route_hint=m._PREFS.get("route_mode", ""),
    )
    summary_lines = [
        f"cwd: {session.cwd or '(none)'}",
        m._progress_cell("files", str(len(session.files or [])), status="active" if session.files else "idle"),
        m._progress_cell("plan", session.plan_id or "none", status="active" if session.plan_id else "idle"),
        m._progress_cell("task", session.task_id or "none", status="active" if session.task_id else "idle"),
    ]
    if sys_prompt:
        summary_lines.append(m._progress_cell("system", f"{len(sys_prompt)} chars", status="info"))
    if pending_inject:
        summary_lines.append(m._progress_cell("inject", f"{len(pending_inject)} chars queued", status="warn"))
    detail_lines = []
    if session.files:
        detail_lines.extend(f"file: {path}" for path in session.files)
    else:
        detail_lines.append("files: (none tracked)")
    if session.plan_id:
        plan_validation = m._validate_plan_id_local(session.plan_id, cwd=session.cwd)
        detail_lines.append(f"plan: {session.plan_id}{m._link_validation_suffix(plan_validation)}")
    if session.task_id:
        task_validation = m._validate_task_id_local(session.task_id, cwd=session.cwd)
        detail_lines.append(f"task: {session.task_id}{m._link_validation_suffix(task_validation)}")
    grounding_preview = m._render_effective_grounding_preview(session)
    if grounding_preview:
        detail_lines.append("effective grounding preview:")
        detail_lines.extend(str(grounding_preview).splitlines())
    if sys_prompt:
        preview = sys_prompt[:80] + ("…" if len(sys_prompt) > 80 else "")
        detail_lines.append(f"system: {preview}")
    if pending_inject:
        detail_lines.append(f"inject: ({len(pending_inject)} chars pending)")
        detail_lines.append("next send guardrail: injected context is queued for one message only")
    if sys_prompt or pending_inject:
        extra_chars = len(sys_prompt) + len(pending_inject)
        detail_lines.append(f"next send extras: ~{max(1, extra_chars // 4) if extra_chars else 0} est. tokens before your next typed message")
    if int(pressure["pct_next"]) >= 50:
        detail_lines.append(
            f"context pressure: ~{int(pressure['next_tokens']):,} est. tokens on the next send ({int(pressure['pct_next_raw'])}% of {pressure['limit_label']})"
        )
        if bool(pressure["overflow"]):
            detail_lines.append("overflow cue: next send likely exceeds the resolved window — trim hidden context before retrying")
        elif int(pressure["pct_next"]) >= 80:
            detail_lines.append("recovery guardrail: save /bookmark before /clear if you need a lighter restart")
        else:
            detail_lines.append("staleness cue: /tokeninfo can confirm whether context pressure is causing drift")
    if pressure["hidden_pressure"] and int(pressure["pct_next"]) >= 80:
        detail_lines.append("hidden context cue: system or queued inject content pushes the next send closer to capacity")
    if pressure["has_pending_inject"]:
        detail_lines.append("recovery cue: /inject clear drops the queued one-shot context before a retry")
    action_lines = []
    if not session.files:
        action_lines.append("/files add <path> to add grounding files")
    else:
        action_lines.append("/files to review or remove tracked files")
    if session.plan_id or session.task_id:
        action_lines.append("/session to compare grounding against session health")
    else:
        action_lines.append("/plan <id> or /task <id> to strengthen work context")
    if sys_prompt or pending_inject:
        action_lines.append("/promptdebug to preview the exact next payload before sending")
    else:
        action_lines.append("/inject status or /system view to inspect hidden context before sending")
    if int(pressure["pct_next"]) >= 50 or pressure["hidden_pressure"]:
        action_lines.append("/tokeninfo to inspect the current context budget")
    if int(pressure["pct_next"]) >= 80:
        action_lines.append("/bookmark before /clear if you need a clean recovery reset")
    if bool(pressure["overflow"]) or pressure["hidden_pressure"]:
        action_lines.append("/promptdebug to verify the next payload before sending")
    if pressure["has_pending_inject"]:
        action_lines.append("/inject clear to remove the queued one-shot context")
    m._print_dashboard_surface(
        "Context Dashboard",
        summary_lines=summary_lines,
        detail_lines=detail_lines,
        action_lines=action_lines,
    )
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_cwd
# ---------------------------------------------------------------------------

def _cmd_cwd(ctx: ChatCommandContext) -> str:
    """/cwd [path] — show or switch the session working directory."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    new_path = ctx.args.strip()
    if not new_path:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[dim]cwd[/]  {session.cwd}")
        else:
            print(f"cwd: {session.cwd}")
        return _CMD_CONTINUE
    resolved = str(Path(new_path).expanduser().resolve())
    if not Path(resolved).is_dir():
        m._print_error(f"not a directory: {resolved}")
        return _CMD_CONTINUE
    update_session(ctx.session_id, cwd=resolved)
    _get_cli_mod().append_event(
        ctx.session_id,
        kind="chat",
        content=f"/cwd {new_path}",
        metadata={"summary": f"switched cwd to {resolved}"},
    )
    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(f"[dim]cwd[/] [green]→[/] {resolved}")
    else:
        print(f"cwd → {resolved}")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_files
# ---------------------------------------------------------------------------

def _cmd_files(ctx: ChatCommandContext) -> str:
    """/files [add <path> | rm <path>] — list, add, or remove tracked files."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    raw = ctx.args.strip()
    if not raw:
        if not session.files:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No tracked files.[/]")
            else:
                print("No tracked files.")
        else:
            if _RICH_AVAILABLE and _IS_TTY:
                for f in session.files:
                    _RICH_CONSOLE.print(f"  [cyan]📄[/] {f}")
            else:
                for f in session.files:
                    print(f"  {f}")
        return _CMD_CONTINUE

    parts = raw.split(maxsplit=1)
    subcmd = parts[0].lower()
    target = parts[1].strip() if len(parts) > 1 else ""

    if subcmd in ("add", "+"):
        if not target:
            _print_usage("Usage: /files add <path>")
            return _CMD_CONTINUE
        resolved = str(Path(target).expanduser().resolve())
        current = list(session.files)
        if resolved in current:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"[yellow]already tracked:[/] {resolved}")
            else:
                print(f"Already tracked: {resolved}")
            return _CMD_CONTINUE
        current.append(resolved)
        update_session(ctx.session_id, files=current)
        _get_cli_mod().append_event(
            ctx.session_id,
            kind="chat",
            content=f"/files add {target}",
            metadata={"summary": f"added file {resolved}"},
        )
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]✓[/] tracked: {resolved}")
        else:
            print(f"tracked: {resolved}")

    elif subcmd in ("rm", "remove", "-"):
        if not target:
            _print_usage("Usage: /files rm <path>")
            return _CMD_CONTINUE
        resolved = str(Path(target).expanduser().resolve())
        current = list(session.files)
        matched = [f for f in current if f == resolved or f == target or Path(f).name == target]
        if not matched:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"[yellow]not tracked:[/] {target}")
            else:
                print(f"Not tracked: {target}")
            return _CMD_CONTINUE
        for item in matched:
            current.remove(item)
        update_session(ctx.session_id, files=current)
        _get_cli_mod().append_event(
            ctx.session_id,
            kind="chat",
            content=f"/files rm {target}",
            metadata={"summary": f"removed file {target}"},
        )
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[red]✗[/] untracked: {', '.join(matched)}")
        else:
            print(f"untracked: {', '.join(matched)}")

    else:
        _print_usage("Usage: /files  |  /files add <path>  |  /files rm <path>")

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_routing
# ---------------------------------------------------------------------------

def _cmd_routing(ctx: ChatCommandContext) -> str:
    """/routing [suggest|analyze] — inspect learned routing hints from past ratings."""
    m = _get_cli_mod()
    arg = (ctx.args or "").strip().lower()
    sub = arg or "suggest"
    if sub not in {"suggest", "analyze"}:
        m._print_error("Usage: /routing [suggest|analyze]")
        return _CMD_CONTINUE
    rows = m._route_quality_summary()
    if not rows:
        print("No route-quality history yet. Use /rate after routed responses to build suggestions.")
        return _CMD_CONTINUE
    if sub == "suggest":
        best = rows[0]
        print("Routing suggestion")
        print("------------------")
        print(f"  Best-rated route: /{best['route']}")
        print(f"  Average score:    {best['avg']:.1f}/5 across {best['count']} rating(s)")
        print(f"  High-rate share:  {best['high_rate']}%")
        if len(rows) > 1:
            runner_up = rows[1]
            print(f"  Runner-up:        /{runner_up['route']} ({runner_up['avg']:.1f}/5)")
        print("  Learned behavior is advisory only; auto-routing remains unchanged.")
        return _CMD_CONTINUE
    print("Routing quality lanes")
    print("---------------------")
    for entry in rows[:5]:
        print(f"  /{entry['route']:<12} avg {entry['avg']:.1f}/5  ratings {entry['count']:<2}  high-rate {entry['high_rate']}%")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_why
# ---------------------------------------------------------------------------

def _cmd_why(ctx: ChatCommandContext) -> str:
    """/why — explain the last routing or tool decision from session history."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    snapshot = m._last_trace_snapshot(ctx.session_id)
    if snapshot is None:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]No routing decisions recorded yet. Try a prompt that triggers auto-routing.[/]")
        else:
            print("No routing decisions recorded yet. Try a prompt that triggers auto-routing.")
        return _CMD_CONTINUE

    if _RICH_AVAILABLE and _IS_TTY:
        grid = _RichTable.grid(padding=(0, 1))
        grid.add_column(style="bold cyan", no_wrap=True)
        grid.add_column()
        grid.add_row("What happened:", str(snapshot.get("what_happened") or ""))
        grid.add_row("Why:", str(snapshot.get("rationale") or "")[:300])
        if snapshot.get("route_reason"):
            grid.add_row("Route reason:", str(snapshot.get("route_reason") or "")[:300])
        grid.add_row("Confidence:", f"[{snapshot.get('conf_color', 'dim')}]{snapshot.get('conf_label', '(unknown)')}[/]")
        if snapshot.get("target_text"):
            grid.add_row("Target:", str(snapshot.get("target_text") or "")[:120])
        if snapshot.get("args_text"):
            grid.add_row("Args:", str(snapshot.get("args_text") or "")[:120])
        grid.add_row("When:", str(snapshot.get("ts") or ""))
        _RICH_CONSOLE.print(_RichPanel(grid, title="[bold cyan]Last Decision[/]", border_style=str(snapshot.get("border_style") or "dim"), padding=(0, 1)))
    else:
        print(f"  What happened: {str(snapshot.get('what_happened') or '')}")
        print(f"  Why:           {str(snapshot.get('rationale') or '')[:300]}")
        if snapshot.get("route_reason"):
            print(f"  Route reason:  {str(snapshot.get('route_reason') or '')[:300]}")
        print(f"  Confidence:    {str(snapshot.get('conf_label') or '(unknown)')}")
        if snapshot.get("target_text"):
            print(f"  Target:        {str(snapshot.get('target_text') or '')[:120]}")
        if snapshot.get("args_text"):
            print(f"  Args:          {str(snapshot.get('args_text') or '')[:120]}")
        print(f"  When:          {str(snapshot.get('ts') or '')}")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_trace
# ---------------------------------------------------------------------------

def _cmd_trace(ctx: ChatCommandContext) -> str:
    """/trace — show the latest routing trace plus the current quality context."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    snapshot = m._last_trace_snapshot(ctx.session_id)
    if snapshot is None:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]No trace data yet. Route or run a command first.[/]")
        else:
            print("No trace data yet. Route or run a command first.")
        return _CMD_CONTINUE

    if _RICH_AVAILABLE and _IS_TTY:
        grid = _RichTable.grid(padding=(0, 1))
        grid.add_column(style="bold cyan", no_wrap=True)
        grid.add_column()
        grid.add_row("Route:", str(snapshot.get("what_happened") or ""))
        grid.add_row("Rationale:", str(snapshot.get("rationale") or "")[:300])
        grid.add_row("Confidence:", f"[{snapshot.get('conf_color', 'dim')}]{snapshot.get('conf_label', '(unknown)')}[/]")
        if snapshot.get("latest_rating"):
            grid.add_row("Latest rating:", str(snapshot.get("latest_rating") or ""))
        grid.add_row("Ratings logged:", str(snapshot.get("rating_count") or 0))
        grid.add_row("When:", str(snapshot.get("ts") or ""))
        _RICH_CONSOLE.print(_RichPanel(grid, title="[bold cyan]Trace Snapshot[/]", border_style=str(snapshot.get("border_style") or "dim"), padding=(0, 1)))
    else:
        print("Trace Snapshot")
        print(f"  Route:         {str(snapshot.get('what_happened') or '')}")
        print(f"  Rationale:     {str(snapshot.get('rationale') or '')[:300]}")
        print(f"  Confidence:    {str(snapshot.get('conf_label') or '(unknown)')}")
        if snapshot.get("latest_rating"):
            print(f"  Latest rating: {str(snapshot.get('latest_rating') or '')}")
        print(f"  Ratings logged:{int(snapshot.get('rating_count') or 0):>4}")
        print(f"  When:          {str(snapshot.get('ts') or '')}")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_autoroute
# ---------------------------------------------------------------------------

def _cmd_autoroute(ctx: ChatCommandContext) -> str:
    """/autoroute [on|off] — show or set session-level REPL auto-routing."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    raw = ctx.args.strip().lower()
    current = bool(getattr(session, "repl_auto_route", True))
    if not raw:
        if _RICH_AVAILABLE and _IS_TTY:
            state = "[green]✓ ON[/]" if current else "[dim]✗ OFF[/]"
            _RICH_CONSOLE.print(f"🔀 auto-route: {state}  [dim](high-confidence prompts only)[/]")
        else:
            print(f"Auto-route: {'ON' if current else 'OFF'} (high-confidence prompts only)")
        return _CMD_CONTINUE
    if raw not in {"on", "off"}:
        m._print_error("Usage: /autoroute [on|off]")
        return _CMD_CONTINUE
    enabled = raw == "on"
    update_session(ctx.session_id, repl_auto_route=enabled)
    _get_cli_mod().append_event(
        ctx.session_id,
        kind="chat",
        content=f"/autoroute {raw}",
        metadata={"summary": f"auto-route {'enabled' if enabled else 'disabled'}"},
    )
    if _RICH_AVAILABLE and _IS_TTY:
        state = "[green]✓ ON[/]" if enabled else "[dim]✗ OFF[/]"
        note = "" if enabled else "  [dim]prompts will stay in chat[/]"
        _RICH_CONSOLE.print(f"🔀 auto-route → {state}{note}")
    else:
        if enabled:
            print("Auto-route enabled for this session.")
        else:
            print("Auto-route disabled for this session; prompts will stay in chat.")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_snapshot
# ---------------------------------------------------------------------------

def _cmd_snapshot(ctx: ChatCommandContext) -> str:
    """/snapshot [name] — save current git HEAD as a named restore point."""
    m = _get_cli_mod()
    name = ctx.args.strip() or "auto"
    is_tty = _get_is_tty()

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5
            # Security: shell=False, timeout=5, hardcoded command with no user input interpolation
        )
        sha = result.stdout.strip()[:12]

        if not sha:
            msg = "Not in a git repo or no commits yet."
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
            else:
                print(msg)
            return _CMD_CONTINUE

        snapshots = m._PREFS.get("snapshots", {})
        import datetime  # noqa: PLC0415
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        snapshots[name] = {"sha": sha, "ts": ts}
        m._prefs_set("snapshots", snapshots)

        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓[/] Snapshot [bold]{name}[/] saved at [dim]{sha}[/]")
        else:
            print(f"✓ Snapshot '{name}' saved at {sha}")
    except (subprocess.CalledProcessError, OSError) as e:  # noqa: BLE001
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[red]Error:[/] {e}")
        else:
            print(f"Error: {e}")

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_rollback
# ---------------------------------------------------------------------------

def _cmd_rollback(ctx: ChatCommandContext) -> str:
    """/rollback [last|list|<name>] — restore latest checkpoint, list git snapshots, or preview/exec a git snapshot rollback."""
    m = _get_cli_mod()
    arg = ctx.args.strip()
    arg_lower = arg.lower()

    if not arg or arg_lower == "list":
        is_tty = _get_is_tty()
        snapshots = m._PREFS.get("snapshots", {})
        if not snapshots:
            msg = "No snapshots saved. Use /snapshot [name] to save one."
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[dim]{msg}[/]")
            else:
                print(msg)
            return _CMD_CONTINUE
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print("\n[bold cyan]📸 Saved Snapshots[/]\n")
            for snap_name, snap_data in snapshots.items():
                sha = snap_data.get("sha", "?")
                ts = snap_data.get("ts", "")[:10]
                _RICH_CONSOLE.print(f"  [bold green]{snap_name:<20}[/] [dim]{sha}[/]  {ts}")
            _RICH_CONSOLE.print()
        else:
            print("\n📸 Saved Snapshots\n")
            for snap_name, snap_data in snapshots.items():
                sha = snap_data.get("sha", "?")
                ts = snap_data.get("ts", "")[:10]
                print(f"  {snap_name:<20} {sha}  {ts}")
            print()
        return _CMD_CONTINUE

    if arg_lower == "last":
        session = m._require_session_or_warn(ctx)
        if session is None:
            return _CMD_CONTINUE
        outcome = restore_last_routed_action_checkpoint(session.session_id)
        if outcome is None:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]—  no routed action checkpoints available for this session[/]")
            else:
                print("No routed action checkpoints are available for this session.")
            m._set_command_result(ctx, ok=False, summary="no routed checkpoints")
            return _CMD_CONTINUE
        checkpoint = outcome.get("checkpoint") or {}
        checkpoint_id = str(checkpoint.get("checkpoint_id") or "").strip()
        action_kind = str(checkpoint.get("action_kind") or "action").strip()
        reason = str(outcome.get("reason") or "").strip()
        status = str(outcome.get("status") or "").strip()
        if status == "restored":
            restored_files = [str(item) for item in outcome.get("restored_files") or []]
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"[green]✓[/] rolled back [dim]{action_kind}[/] via checkpoint [dim]{checkpoint_id}[/]  [cyan]({len(restored_files)} file(s) restored)[/]")
                for path in restored_files[:5]:
                    _RICH_CONSOLE.print(f"  [dim]↩ {path}[/]")
            else:
                print(f"Rolled back last routed {action_kind} action via checkpoint {checkpoint_id}. Restored {len(restored_files)} file(s).")
                for path in restored_files[:5]:
                    print(f"  restored: {path}")
            m._set_command_result(ctx, ok=True, summary=f"rolled back checkpoint {checkpoint_id}")
            return _CMD_CONTINUE
        if status == "already_rolled_back":
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"[dim]—  checkpoint {checkpoint_id} was already restored[/]")
            else:
                print(f"Checkpoint {checkpoint_id} for the last routed action was already restored.")
            m._set_command_result(ctx, ok=True, summary=f"checkpoint {checkpoint_id} already restored")
            return _CMD_CONTINUE
        if status == "unsupported":
            workspace_signature = str(checkpoint.get("workspace_signature") or "").strip()
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"[yellow]⚠[/] rollback unavailable for [dim]{checkpoint_id}[/]: {reason or 'manual recovery required'}")
                if workspace_signature:
                    _RICH_CONSOLE.print(f"  [dim]workspace before action:[/] {workspace_signature}")
            else:
                print(f"Checkpoint {checkpoint_id} recorded the last routed {action_kind} action, but automatic rollback is unavailable: {reason or 'manual recovery required.'}")
                if workspace_signature:
                    print(f"workspace signature before action: {workspace_signature}")
            m._set_command_result(ctx, ok=False, summary=f"rollback unavailable for {checkpoint_id}")
            return _CMD_CONTINUE
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[red]✗[/] rollback failed for [dim]{checkpoint_id}[/]: {reason or 'unable to restore the latest routed action'}")
        else:
            print(f"Rollback failed for checkpoint {checkpoint_id}: {reason or 'unable to restore the latest routed action.'}")
        m._set_command_result(ctx, ok=False, summary=f"rollback failed for {checkpoint_id}")
        return _CMD_CONTINUE

    is_tty = _get_is_tty()
    parts = arg.split()
    exec_mode = "--exec" in parts
    snap_name = parts[0] if parts else ""
    snapshots = m._PREFS.get("snapshots", {})

    if snap_name not in snapshots:
        msg = f"No snapshot named '{snap_name}'. Use /rollback list to see saved snapshots."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    sha = snapshots[snap_name].get("sha", "")
    # Security: validate sha is a hex git SHA (alphanumeric only) to prevent argument injection
    import re as _re
    if not _re.fullmatch(r"[0-9a-f]{1,40}", sha or ""):
        msg = f"Invalid snapshot SHA '{sha}': must be a hex git SHA."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[red]Error:[/] {msg}")
        else:
            print(f"Error: {msg}")
        return _CMD_CONTINUE

    if exec_mode:
        try:
            result = subprocess.run(
                ["git", "checkout", sha],
                capture_output=True, text=True, timeout=10
                # Security: shell=False, timeout=10, sha validated as hex git SHA above
            )
            if result.returncode == 0:
                if _RICH_AVAILABLE and is_tty:
                    _RICH_CONSOLE.print(f"[green]✓[/] Rolled back to snapshot [bold]{snap_name}[/] ({sha})")
                else:
                    print(f"✓ Rolled back to {snap_name} ({sha})")
            else:
                if _RICH_AVAILABLE and is_tty:
                    _RICH_CONSOLE.print(f"[red]Error:[/] {result.stderr}")
                else:
                    print(f"Error: {result.stderr}")
        except (subprocess.CalledProcessError, OSError) as e:  # noqa: BLE001
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[red]Error:[/] {e}")
            else:
                print(f"Error: {e}")
    else:
        try:
            result = subprocess.run(
                ["git", "diff", "--stat", f"{sha}..HEAD"],
                capture_output=True, text=True, timeout=10
                # Security: shell=False, timeout=10, sha validated as hex git SHA above
            )
            diff_stat = result.stdout.strip() or "(no differences)"
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"\n[bold cyan]📸 Rollback Preview:[/] [bold]{snap_name}[/] → current HEAD\n")
                _RICH_CONSOLE.print(f"[dim]{diff_stat}[/]")
                _RICH_CONSOLE.print(f"\n[yellow]⚠️  Use /rollback {snap_name} --exec to actually rollback (DESTRUCTIVE)[/]\n")
            else:
                print(f"\n📸 Rollback Preview: {snap_name} → HEAD\n{diff_stat}")
                print(f"\n⚠️  Use /rollback {snap_name} --exec to rollback (DESTRUCTIVE)\n")
        except (subprocess.CalledProcessError, OSError) as e:  # noqa: BLE001
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[red]Error:[/] {e}")
            else:
                print(f"Error: {e}")

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_analyze
# ---------------------------------------------------------------------------

def _cmd_analyze(ctx: ChatCommandContext) -> str:
    """/analyze <goal> — run an analysis using the current session context."""
    m = _get_cli_mod()
    config = m._require_config_or_warn(ctx)
    if config is None:
        return _CMD_CONTINUE
    goal = ctx.args.strip()
    if not goal:
        m._print_error("Usage: /analyze <goal>")
        m._set_command_result(ctx, ok=False, summary="missing analysis goal")
        return _CMD_CONTINUE
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    _, context_text = _get_cli_mod().collect_workspace_context(cwd=session.cwd or None, targets=list(session.files))
    scoped_config = m.bind_config_to_session(config, session.session_id)
    prompt = m.build_analysis_prompt(goal=goal, context_text=context_text, session=session)
    _get_cli_mod().append_event(
        session.session_id,
        kind="analyze",
        content=goal,
        metadata={"summary": goal, "cwd": session.cwd, "files": list(session.files)},
    )
    try:
        response = m._with_spinner(
            "🔍 Analyzing…",
            m.invoke_openclaw,
            prompt,
            config=scoped_config,
            history=list(ctx.history),
            output_json=False,
        )
    except OpenClawCliError as exc:
        m._print_error(str(exc))
        m._set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    m.print_response(response, output_json=config.output_json)
    m.persist_response(session.session_id, goal, response.response)
    ctx.history[:] = load_conversation_history(session.session_id)
    m._set_command_result(
        ctx,
        ok=True,
        summary=m._summarize_terminal_result(response.response, fallback=f"analysis complete for {goal}"),
    )
    if response.response:
        _DIM = "\033[2m" if sys.stdout.isatty() else ""
        _RESET = "\033[0m" if sys.stdout.isatty() else ""
        word_count = len(response.response.split())
        print(f"  {_DIM}✓ Analysis complete — {word_count} words{_RESET}")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_research
# ---------------------------------------------------------------------------

def _cmd_research(ctx: ChatCommandContext) -> str:
    """/research <query> — run the research agent using the current session context."""
    m = _get_cli_mod()
    query = ctx.args.strip()
    if not query:
        m._print_error("Usage: /research <query>")
        m._set_command_result(ctx, ok=False, summary="missing research query")
        return _CMD_CONTINUE
    try:
        from research_agent import ResearchAgent  # type: ignore[import]  # noqa: PLC0415
    except ImportError:
        m._print_error(m.missing_feature_hint("openclaw research"))
        m._set_command_result(ctx, ok=False, summary="research agent unavailable")
        return _CMD_CONTINUE
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    _, context_text = _get_cli_mod().collect_workspace_context(cwd=session.cwd or None, targets=list(session.files))
    effective_query = query
    plan_ctx = m._plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd)
    if plan_ctx:
        effective_query = f"{plan_ctx}\n\n{effective_query}"
    if context_text and session.files:
        effective_query = f"{effective_query}\n\nLocal workspace context:\n{context_text[:4000]}"

    async def _progress(message: str) -> None:
        if _IS_TTY:
            sys.stdout.write(f"\r🔍 {message:<60}")
            sys.stdout.flush()
        else:
            print(message)

    _get_cli_mod().append_event(session.session_id, kind="research", content=query, metadata={"summary": query})
    try:
        report = m.run_async(ResearchAgent().run(effective_query, on_progress=_progress))
    except Exception as exc:  # noqa: BLE001  # broad: intentional
        m._LOG.error("research agent failed", exc_info=True)
        m._print_error(str(exc))
        m._set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    if _IS_TTY:
        sys.stdout.write("\r" + " " * 62 + "\r")
        sys.stdout.flush()
    output_target = _get_cli_mod().save_output(
        session.session_id,
        m.output_name_from_title(query, default_stem="research-report", suffix=".md"),
        report,
    )
    _get_cli_mod().append_event(
        session.session_id,
        kind="assistant",
        content=report,
        metadata={"summary": f"saved research to {output_target}"},
    )
    print(report)
    m._print_meta_footer(("saved", output_target))
    m._set_command_result(ctx, ok=True, summary=f"saved research to {output_target}")
    if report:
        _DIM = "\033[2m" if sys.stdout.isatty() else ""
        _RESET = "\033[0m" if sys.stdout.isatty() else ""
        word_count = len(report.split())
        import re as _re  # noqa: PLC0415
        source_count = len(set(_re.findall(r'\[\d+\]', report))) or report.count("http")
        print(f"  {_DIM}✓ Research complete — {word_count} words, {source_count} sources{_RESET}")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_write
# ---------------------------------------------------------------------------

def _cmd_write(ctx: ChatCommandContext) -> str:
    """/write <task> — generate a markdown document using the current session context."""
    m = _get_cli_mod()
    config = m._require_config_or_warn(ctx)
    if config is None:
        return _CMD_CONTINUE
    task_text = ctx.args.strip()
    if not task_text:
        m._print_error("Usage: /write <task>")
        m._set_command_result(ctx, ok=False, summary="missing writing task")
        return _CMD_CONTINUE
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    _, context_text = _get_cli_mod().collect_workspace_context(cwd=session.cwd or None, targets=list(session.files))
    title = task_text[:80]
    scoped_config = m.bind_config_to_session(config, session.session_id)
    prompt = m.build_write_prompt(task=task_text, context_text=context_text, session=session, title=title)
    _get_cli_mod().append_event(session.session_id, kind="write", content=task_text, metadata={"summary": task_text})
    try:
        response = m._with_spinner(
            "✍️  Writing…",
            m.invoke_openclaw,
            prompt,
            config=scoped_config,
            history=list(ctx.history),
            output_json=False,
        )
    except OpenClawCliError as exc:
        m._print_error(str(exc))
        m._set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    m.persist_response(session.session_id, task_text, response.response)
    output_target = _get_cli_mod().save_output(
        session.session_id,
        m.output_name_from_title(title, default_stem="draft", suffix=".md"),
        response.response,
    )
    print(response.response)
    m._print_meta_footer(("saved", output_target))
    ctx.history[:] = load_conversation_history(session.session_id)
    m._set_command_result(ctx, ok=True, summary=f"saved draft to {output_target}")
    if response.response:
        _DIM = "\033[2m" if sys.stdout.isatty() else ""
        _RESET = "\033[0m" if sys.stdout.isatty() else ""
        word_count = len(response.response.split())
        print(f"  {_DIM}✓ Draft complete — {word_count} words{_RESET}")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_exec
# ---------------------------------------------------------------------------

def _cmd_exec(ctx: ChatCommandContext) -> str:
    """/exec [--] <command> — run a shell command with session tracking and approval."""
    m = _get_cli_mod()
    raw = ctx.args.strip()
    if raw.startswith("-- "):
        raw = raw[3:]
    if not raw:
        m._print_error("Usage: /exec [--] <command>")
        m._set_command_result(ctx, ok=False, summary="missing shell command")
        return _CMD_CONTINUE
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    try:
        command_parts = shlex.split(raw)
    except ValueError as exc:
        m._print_error(f"invalid shell command: {exc}")
        m._set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    if not command_parts:
        m._print_error("Usage: /exec [--] <command>")
        m._set_command_result(ctx, ok=False, summary="missing shell command")
        return _CMD_CONTINUE
    risk_level = infer_command_risk(command_parts)
    m._print_risky_action_warning(
        action="/exec",
        target=raw,
        risk_level=risk_level,
        recovery_hint="check the cwd and use your shell history or VCS tools before re-running.",
    )
    approval_started = time.monotonic()
    approved = _get_cli_mod().request_cli_approval(
        action="shell.exec",
        target=raw,
        risk_level=risk_level,
        detail=f"cwd={session.cwd}",
        review_lines=m._build_exec_approval_review(command_text=raw, cwd=session.cwd or ""),
        trust_note="approving runs exactly the shell text shown above; denying keeps the workspace unchanged.",
        recovery_hint="if this looks wrong, deny it, inspect /cwd, then rerun /exec with a safer command.",
        auto_approve=False,
        session_id=session.session_id,
        plan_id=session.plan_id,
        task_id=session.task_id,
    )
    approval_seconds = max(0.0, time.monotonic() - approval_started)
    _get_cli_mod().append_event(
        session.session_id,
        kind="approval",
        content=raw,
        metadata={
            "summary": f"{'approved' if approved else 'denied'} /exec {raw[:80]}",
            "action": "shell.exec",
            "approved": approved,
            "approval_seconds": approval_seconds,
            "risk_level": risk_level.value,
            "cwd": session.cwd,
        },
    )
    if not approved:
        m._print_error("shell command not approved")
        m._print_feedback(
            "Approval denied.",
            level="warn",
            detail=(
                f"nothing ran · after {m._format_elapsed_compact(approval_seconds)}"
                " · verify /cwd and rerun /exec when ready"
            ),
        )
        m._set_command_result(ctx, ok=False, summary="shell command not approved")
        return _CMD_CONTINUE
    if not m._capture_routed_action_checkpoint(
        ctx,
        session=session,
        action_kind="exec",
        target=raw,
        detail=f"cwd={session.cwd}",
    ):
        return _CMD_CONTINUE
    exec_started = time.monotonic()
    _exec_cwd = session.cwd or None
    _use_animation = _get_is_tty() and not m._a11y_reduced_motion() and not m._a11y_plain_mode()
    try:
        if _use_animation:
            import subprocess as _sp  # noqa: PLC0415
            _proc = _sp.Popen(
                command_parts,
                cwd=_exec_cwd,
                stdout=_sp.PIPE,
                stderr=_sp.PIPE,
            )
            _raw_stdout, _raw_stderr, _rc = _exec_animate_fn(
                _proc,
                label=raw[:50],
                is_tty=_get_is_tty(),
                plain_mode=m._a11y_plain_mode(),
                reduced_motion=m._a11y_reduced_motion(),
            )
            from openclaw_cli_actions import ShellCommandResult, normalize_cwd  # noqa: PLC0415
            result = ShellCommandResult(
                command=shlex.join(command_parts),
                cwd=str(normalize_cwd(_exec_cwd)),
                returncode=_rc,
                stdout=_raw_stdout.decode(errors="replace"),
                stderr=_raw_stderr.decode(errors="replace"),
                timed_out=False,
            )
        else:
            result = m.run_async(_get_cli_mod().run_shell_command(command_parts, cwd=_exec_cwd, timeout=60))
    except Exception as exc:  # noqa: BLE001  # broad: intentional
        m._LOG.error("shell command execution failed", exc_info=True)
        m._print_error(str(exc))
        m._set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    exec_seconds = max(0.0, time.monotonic() - exec_started)
    _get_cli_mod().append_event(
        session.session_id,
        kind="exec",
        content=raw,
        metadata={
            "summary": f"exit {result.returncode}: {raw}",
            "cwd": result.cwd,
            "risk_level": risk_level.value,
            "returncode": result.returncode,
            "approval_seconds": approval_seconds,
            "elapsed_seconds": exec_seconds,
        },
    )
    m._print_shell_result(result)
    if result.returncode != 0:
        m._print_exec_error_hints(raw, result.stderr, result.returncode)
    m._print_feedback(
        "Command complete.",
        level="success" if result.returncode == 0 else "warn",
        detail=(
            f"exit {result.returncode} · {m._format_elapsed_compact(exec_seconds)} run"
            f" · approval {m._format_elapsed_compact(approval_seconds)} · cwd {result.cwd}"
        ),
    )
    m._set_command_result(
        ctx,
        ok=result.returncode == 0,
        summary=f"exit {result.returncode}: {raw}",
    )
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_edit
# ---------------------------------------------------------------------------

def _cmd_edit(ctx: ChatCommandContext) -> str:
    """/edit <path> [--content <text> | --append <text> | --replace OLD NEW] — inspect or write a file."""
    m = _get_cli_mod()
    raw = ctx.args.strip()
    if not raw:
        m._print_error("Usage: /edit <path> [--content <text>] [--append <text>] [--replace OLD NEW]")
        m._set_command_result(ctx, ok=False, summary="missing edit target")
        return _CMD_CONTINUE
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        m._print_error(f"invalid edit arguments: {exc}")
        m._set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    if not parts:
        m._print_error("Usage: /edit <path> [--content <text>] [--append <text>] [--replace OLD NEW]")
        m._set_command_result(ctx, ok=False, summary="missing edit target")
        return _CMD_CONTINUE
    path = parts[0]
    rest = parts[1:]
    content = ""
    append_mode = False
    replace_values: list[str] = []

    if rest[:1] == ["--content"]:
        content = " ".join(rest[1:])
    elif rest[:1] == ["--append"]:
        content = " ".join(rest[1:])
        append_mode = True
    elif rest[:1] == ["--replace"]:
        if len(rest) < 3:
            m._print_error("Usage: /edit <path> [--content <text>] [--append <text>] [--replace OLD NEW]")
            m._set_command_result(ctx, ok=False, summary="missing replace arguments")
            return _CMD_CONTINUE
        replace_values = rest[1:3]
    elif rest and not rest[0].startswith("--"):
        content = " ".join(rest)

    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    if not content and not replace_values:
        resolved = str(Path(path).expanduser().resolve())
        try:
            p = Path(resolved)
            if p.exists():
                lines = p.read_text(errors="replace").splitlines()
                if _RICH_AVAILABLE and _IS_TTY:
                    _RICH_CONSOLE.print(f"[bold]{resolved}[/]  [dim]({len(lines)} lines)[/]")
                    for ln in lines[:10]:
                        _RICH_CONSOLE.print(f"  [dim]{ln}[/]")
                    if len(lines) > 10:
                        _RICH_CONSOLE.print(f"  [dim]… ({len(lines) - 10} more lines)[/]")
                else:
                    print(f"{resolved}  ({len(lines)} lines)")
                    for ln in lines[:10]:
                        print(f"  {ln}")
                    if len(lines) > 10:
                        print(f"  ... ({len(lines) - 10} more lines)")
                m._set_command_result(ctx, ok=True, summary=f"previewed {resolved}")
            else:
                m._print_error(f"file not found: {resolved}")
                m._set_command_result(ctx, ok=False, summary=f"file not found: {resolved}")
        except OSError as exc:  # noqa: BLE001
            m._LOG.error("error reading file %s", path, exc_info=True)
            m._print_error(f"error reading {path}: {exc}")
            m._set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    risk_level = infer_file_edit_risk(path)
    try:
        preview_result = m._preview_file_edit(
            path,
            content=content,
            append=append_mode,
            replace_values=replace_values,
        )
    except OSError as exc:  # noqa: BLE001
        m._LOG.error("file preview failed for %s", path, exc_info=True)
        m._print_error(str(exc))
        m._set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    m._print_file_edit_preview(preview_result)
    if not preview_result.changed:
        m._print_feedback("No changes applied.", level="info", detail="approval skipped")
        m._set_command_result(ctx, ok=True, summary=preview_result.summary)
        return _CMD_CONTINUE
    m._print_risky_action_warning(
        action="/edit",
        target=path,
        risk_level=risk_level,
        recovery_hint="routed edits can use /rollback last; otherwise recover with your editor or VCS.",
    )
    approval_started = time.monotonic()
    approved = _get_cli_mod().request_cli_approval(
        action="file.edit",
        target=path,
        risk_level=risk_level,
        detail=(
            f"append={append_mode};replace={bool(replace_values)};"
            f"changed={preview_result.changed};summary={preview_result.summary[:120]}"
        ),
        review_lines=m._build_edit_approval_review(
            path=path,
            preview_result=preview_result,
            append_mode=append_mode,
            replace_values=replace_values,
        ),
        trust_note="the diff preview above is the exact change queued for approval; denying leaves the file untouched.",
        recovery_hint="deny to adjust the preview, or use /rollback last after routed edits if you approve the wrong change.",
        auto_approve=False,
        session_id=session.session_id,
        plan_id=session.plan_id,
        task_id=session.task_id,
    )
    approval_seconds = max(0.0, time.monotonic() - approval_started)
    _get_cli_mod().append_event(
        session.session_id,
        kind="approval",
        content=path,
        metadata={
            "summary": f"{'approved' if approved else 'denied'} /edit {path}",
            "action": "file.edit",
            "approved": approved,
            "approval_seconds": approval_seconds,
            "risk_level": risk_level.value,
        },
    )
    if not approved:
        m._print_error("file edit not approved")
        m._print_feedback(
            "Approval denied.",
            level="warn",
            detail=(
                f"preview not applied · after {m._format_elapsed_compact(approval_seconds)}"
                " · rerun /edit to adjust the diff"
            ),
        )
        m._set_command_result(ctx, ok=False, summary="file edit not approved")
        return _CMD_CONTINUE
    resolved_path = str(Path(path).expanduser().resolve())
    if not m._capture_routed_action_checkpoint(
        ctx,
        session=session,
        action_kind="edit",
        target=resolved_path,
        detail=f"append={append_mode};replace={bool(replace_values)}",
        file_paths=[resolved_path],
    ):
        return _CMD_CONTINUE
    edit_started = time.monotonic()
    try:
        if replace_values:
            result = replace_text_in_file(path, old=replace_values[0], new=replace_values[1])
        else:
            result = write_text_file(path, content=content, append=append_mode)
    except OSError as exc:  # noqa: BLE001
        m._LOG.error("file write failed for %s", path, exc_info=True)
        m._print_error(str(exc))
        m._set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    edit_seconds = max(0.0, time.monotonic() - edit_started)
    _get_cli_mod().append_event(
        session.session_id,
        kind="edit",
        content=path,
        metadata={
            "summary": result.summary,
            "files": [result.path],
            "changed": result.changed,
            "risk_level": risk_level.value,
            "approval_seconds": approval_seconds,
            "elapsed_seconds": edit_seconds,
        },
    )
    m._print_file_edit_result(result)
    m._print_feedback(
        "Edit complete.",
        level="success" if result.changed else "info",
        detail=f"{result.summary} · {m._format_elapsed_compact(edit_seconds)} write · approval {m._format_elapsed_compact(approval_seconds)}",
    )
    m._set_command_result(ctx, ok=True, summary=result.summary)
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_update
# ---------------------------------------------------------------------------

def _cmd_update(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/update — self-upgrade openclaw via pip without leaving the REPL."""
    import argparse as _argparse  # noqa: PLC0415
    install_dir = _standalone_install_dir()
    if install_dir and ctx.config is not None:
        _update_standalone_install(install_dir, current=cli_version(), base_url=ctx.config.base_url)
    else:
        handle_update_command(_argparse.Namespace())
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]Restart openclaw to use the new version.[/]")
        else:
            print("Restart openclaw to use the new version.")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_version
# ---------------------------------------------------------------------------

def _cmd_version(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/version — show the running CLI version and build stamp."""
    ver = cli_version()
    server = ctx.config.base_url if ctx.config else "unknown"
    if _RICH_AVAILABLE and _IS_TTY:
        t = _RichText()
        t.append(f"{_e('🦞', '[openclaw]')} OpenClaw  ", style="bold cyan")
        t.append(ver, style="bold")
        t.append("\n  server  ", style="dim")
        t.append(server, style="cyan")
        _RICH_CONSOLE.print(_RichPanel(t, border_style="dim", padding=(0, 1)))
    else:
        print(f"\n  openclaw {ver}  ·  server: {server}\n")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_tokeninfo
# ---------------------------------------------------------------------------

def _cmd_tokeninfo(ctx: ChatCommandContext) -> str:
    """/tokeninfo — show estimated token usage for this session."""
    m = _get_cli_mod()
    breakdown = m._history_token_breakdown(ctx.history)
    est_tokens = int(breakdown["total_tokens"])
    msg_count = int(breakdown["total_messages"])
    sys_prompt = str(m._PREFS.get("system_prompt", "") or "")
    pending_inject = str(getattr(m, "_next_inject", "") or "")
    pressure = _context_pressure_snapshot(
        ctx.history,
        system_prompt=sys_prompt,
        pending_inject=pending_inject,
        model_hint=m._PREFS.get("last_model", ""),
        route_hint=m._PREFS.get("route_mode", ""),
    )
    sys_tokens = int(pressure["system_tokens"])
    inject_tokens = int(pressure["inject_tokens"])
    next_turn_tokens = int(pressure["next_tokens"])
    pct_history = int(pressure["pct_history"])
    pct_history_raw = int(pressure["pct_history_raw"])
    pct_next_turn = int(pressure["pct_next"])
    pct_next_turn_raw = int(pressure["pct_next_raw"])

    if pct_history < 50:
        fill_color = _GR
    elif pct_history < 80:
        fill_color = _YE
    else:
        fill_color = _RE

    bar_width = 20
    filled = round(bar_width * pct_history / 100)
    bar = f"{fill_color}{'█' * filled}{_DM}{'░' * (bar_width - filled)}{_R}"

    print(f"\n  {_B}Context usage{_R} {_DM}(estimated){_R}")
    print(f"  Messages:   {_B}{msg_count}{_R}")
    print(f"  Est. tokens:{_B}{est_tokens:,}{_R}")
    print(f"  Window:     {bar} {fill_color}{pct_history_raw}%{_R} of {pressure['limit_display']}")
    if pressure["limit_model_label"] and pressure["limit_model_label"] != "current route":
        print(f"  Model hint: {_B}{pressure['limit_model_label']}{_R}")
    print(f"  {_DM}{pressure['limit_note']}{_R}")
    role_rows = list(breakdown["roles"])
    if role_rows:
        print(f"\n  {_B}Breakdown by actor{_R}")
        for role, details in role_rows[:4]:
            role_tokens = int(details["tokens"])
            share = round((role_tokens / est_tokens) * 100) if est_tokens else 0
            print(
                "  "
                f"{role:<10}"
                f"{details['messages']:>2} msgs"
                f"  ~{role_tokens:>6,} tok"
                f"  {share:>3}%"
            )
        top_role, top_details = role_rows[0]
        top_share = round((int(top_details["tokens"]) / est_tokens) * 100) if est_tokens else 0
        print(f"\n  {_DM}Largest share: {top_role} ({top_share}% of estimated tokens).{_R}")
    if sys_tokens or inject_tokens:
        print(f"\n  {_B}Next send extras{_R}")
        if sys_tokens:
            print(f"  system prompt: ~{sys_tokens:,} tok ({len(sys_prompt):,} chars)")
        if inject_tokens:
            print(f"  pending inject: ~{inject_tokens:,} tok ({len(pending_inject):,} chars)")
        print(f"  next request est.: ~{next_turn_tokens:,} tok total ({pct_next_turn_raw}% of {pressure['limit_label']})")
        print(f"  {_DM}Trust cue: use /promptdebug to preview exactly what will be sent next.{_R}")
    if bool(pressure["overflow"]):
        print(f"\n  {_RE}⚠  Next request likely exceeds the resolved window — trim /inject or /system content, then /bookmark and /clear if needed.{_R}")
    elif pct_history >= 90:
        print(f"\n  {_RE}⚠  Context is near capacity — use /bookmark before /clear so you can resume cleanly.{_R}")
    elif pct_history >= 80:
        print(f"\n  {_YE}⚠  Context is getting full — consider /bookmark, then /clear to reset.{_R}")
    elif pct_history >= 50:
        print(f"\n  {_DM}Tip: If responses feel stale, save a /bookmark and use /clear to refresh context.{_R}")
    if pct_next_turn >= 90 and pct_history < 90 and not bool(pressure["overflow"]):
        print(f"  {_RE}⚠  Hidden context will push the next request near capacity — verify /context or /promptdebug before sending.{_R}")
    elif inject_tokens:
        print(f"  {_DM}Recovery cue: /inject clear removes the queued one-shot context if it is no longer needed.{_R}")
    print()
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_draft
# ---------------------------------------------------------------------------

def _cmd_draft(ctx: ChatCommandContext) -> str:
    """Handler for /draft — save, load, clear, or restore a draft prompt."""
    m = _get_cli_mod()

    parts = ctx.args.strip().split(None, 1)
    sub = parts[0].lower() if parts else ""

    if sub == "save":
        text = parts[1].strip() if len(parts) > 1 else ""
        if not text:
            print(f"  {_DM}Usage: /draft save <text to draft>{_R}")
            return _CMD_CONTINUE
        m._draft_buffer = text
        print(f"  {_GR}Draft saved.{_R}")
        return _CMD_CONTINUE

    if sub == "load":
        if m._draft_buffer:
            print(f"  {_CY}Current draft:{_R}\n  {m._draft_buffer}")
        else:
            print(f"  {_DM}No draft saved. Use /draft save <text> to save one.{_R}")
        return _CMD_CONTINUE

    if sub == "clear":
        m._draft_buffer = ""
        print(f"  {_GR}Draft cleared.{_R}")
        return _CMD_CONTINUE

    if sub == "restore":
        if m._last_interrupted_prompt:
            print(f"  {_DM}Last interrupted prompt:{_R}  {m._last_interrupted_prompt}")
            m._draft_buffer = m._last_interrupted_prompt
        else:
            print(f"  {_DM}No interrupted prompt to restore.{_R}")
        return _CMD_CONTINUE

    if sub == "multiline":
        rest = (parts[1].strip().lower() if len(parts) > 1 else "")
        if rest == "on":
            m._multiline_mode = True
            print(f"  {_GR}Multiline mode: ON{_R} — type \\end on its own line to submit")
        elif rest == "off":
            m._multiline_mode = False
            print("  Multiline mode: OFF")
        else:
            state = "ON" if m._multiline_mode else "OFF"
            print(f"  Multiline mode is currently {_B}{state}{_R}. Usage: /draft multiline on | off")
        return _CMD_CONTINUE

    if m._draft_buffer:
        print(f"  {_CY}Current draft:{_R}\n  {m._draft_buffer}")
    else:
        print(f"  {_DM}No draft saved.{_R} Usage: /draft save <text> | load | clear | restore | multiline on|off")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_template
# ---------------------------------------------------------------------------

def _cmd_template(ctx: ChatCommandContext) -> str:
    """Handler for /template — manage reusable prompt templates."""
    m = _get_cli_mod()

    import re as _re  # noqa: PLC0415

    parts = ctx.args.strip().split(None, 1)
    sub = parts[0].lower() if parts else ""

    templates: dict = m._PREFS.setdefault("templates", {})

    def _show_templates() -> None:
        if not templates:
            print(f"  {_DM}No templates saved. Use /template save <name> <text> to create one.{_R}")
            return
        if _RICH_AVAILABLE and _IS_TTY:
            t = _RichTable(title="Saved Templates", border_style="cyan", header_style="bold cyan")
            t.add_column("Name", style="bold")
            t.add_column("Preview", style="dim")
            for name, text in sorted(templates.items()):
                preview = text[:60] + ("…" if len(text) > 60 else "")
                t.add_row(name, preview)
            _RICH_CONSOLE.print(t)
        else:
            print("  Saved templates:")
            for name, text in sorted(templates.items()):
                preview = text[:60] + ("…" if len(text) > 60 else "")
                print(f"    {_B}{name}{_R}  {_DM}{preview}{_R}")

    if not sub or sub == "list":
        _show_templates()
        return _CMD_CONTINUE

    if sub == "save":
        rest = parts[1].strip() if len(parts) > 1 else ""
        save_parts = rest.split(None, 1)
        if len(save_parts) < 2:
            print(f"  {_DM}Usage: /template save <name> <text>{_R}")
            return _CMD_CONTINUE
        name, text = save_parts[0], save_parts[1].strip()
        if not _re.fullmatch(r"[A-Za-z0-9\-]+", name):
            m._print_error(f"Template name '{name}' is invalid — use letters, digits, and hyphens only.")
            return _CMD_CONTINUE
        templates[name] = text
        m._save_prefs()
        print(f"  {_GR}Template '{name}' saved.{_R}")
        return _CMD_CONTINUE

    if sub == "use":
        name = (parts[1].strip() if len(parts) > 1 else "")
        if not name:
            print(f"  {_DM}Usage: /template use <name>{_R}")
            return _CMD_CONTINUE
        text = templates.get(name)
        if text is None:
            m._print_error(f"Template '{name}' not found. Use /template list to see available templates.")
            return _CMD_CONTINUE
        m._draft_buffer = text
        print(f"  {_GR}Template '{name}' loaded into draft.{_R} Use /draft load to review or submit directly.")
        return _CMD_CONTINUE

    if sub == "delete":
        name = (parts[1].strip() if len(parts) > 1 else "")
        if not name:
            print(f"  {_DM}Usage: /template delete <name>{_R}")
            return _CMD_CONTINUE
        if name not in templates:
            m._print_error(f"Template '{name}' not found. Use /template list to see available templates.")
            return _CMD_CONTINUE
        del templates[name]
        m._save_prefs()
        print(f"  {_GR}Template '{name}' deleted.{_R}")
        return _CMD_CONTINUE

    m._print_error(f"Unknown /template subcommand '{sub}'. Usage: list | use <name> | save <name> <text> | delete <name>")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_inject
# ---------------------------------------------------------------------------

def _cmd_inject(ctx: ChatCommandContext) -> str:
    """/inject — inject file or URL content as context prefix for the next message."""
    m = _get_cli_mod()
    is_tty = _get_is_tty()
    arg = ctx.args.strip()

    if not arg:
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(
                "[dim]Usage:[/]  /inject <path>  |  /inject --url <url>  |  /inject clear  |  /inject status"
            )
        else:
            print("Usage:  /inject <path>  |  /inject --url <url>  |  /inject clear  |  /inject status")
        return _CMD_CONTINUE

    if arg == "clear":
        m._next_inject = ""
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print("[green]✓[/] Injection cleared")
        else:
            print("✓ Injection cleared")
        return _CMD_CONTINUE

    if arg == "status":
        current_inject = m._next_inject
        if current_inject:
            preview = current_inject[:100]
            suffix = "…" if len(current_inject) > 100 else ""
            char_count = len(current_inject)
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(
                    _RichPanel(
                        f"[dim]{preview}{suffix}[/]\n\n[bold]{char_count}[/] chars queued",
                        title="📎 Inject",
                        border_style="cyan",
                    )
                )
            else:
                print(f"📎 Inject ({char_count} chars): {preview}{suffix}")
        else:
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print("[dim](no injection set)[/]")
            else:
                print("(no injection set)")
        return _CMD_CONTINUE

    if arg.startswith("--url "):
        url = arg[6:].strip()
        try:
            import requests as _requests  # noqa: PLC0415
        except ImportError:
            m._print_error("requests library not available — install with pip install requests")
            return _CMD_CONTINUE
        try:
            content = _requests.get(url, timeout=10).text
        except (ConnectionError, TimeoutError, OSError) as exc:  # noqa: BLE001
            m._print_error(f"Failed to fetch URL: {exc}")
            return _CMD_CONTINUE
        _MAX = 200_000
        truncated = False
        if len(content) > _MAX:
            content = content[:_MAX]
            truncated = True
        m._next_inject = content
        preview = content[:60].replace("\n", " ")
        suffix = "…" if len(content) > 60 else ""
        trunc_note = f" [truncated at {_MAX:,} chars]" if truncated else ""
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(
                f"[green]✓[/] Loaded [bold]{len(content)}[/] chars from URL{trunc_note}\n"
                f"[dim]Preview: {preview}{suffix}[/]"
            )
        else:
            print(f"✓ Loaded {len(content)} chars from URL{trunc_note}\nPreview: {preview}{suffix}")
        return _CMD_CONTINUE

    path = Path(arg).expanduser().resolve()
    if not path.exists():
        m._print_error(f"File not found: {path}")
        return _CMD_CONTINUE
    if not path.is_file():
        m._print_error(f"Not a file: {path}")
        return _CMD_CONTINUE
    try:
        raw = path.read_bytes()
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        m._print_error("file appears to be binary")
        return _CMD_CONTINUE
    except OSError as exc:
        m._print_error(f"Could not read file: {exc}")
        return _CMD_CONTINUE
    _MAX = 100_000
    truncated = False
    if len(content) > _MAX:
        content = content[:_MAX]
        truncated = True
    m._next_inject = content
    preview = content[:60].replace("\n", " ")
    suffix = "…" if len(content) > 60 else ""
    trunc_note = f" [truncated at {_MAX:,} chars]" if truncated else ""
    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(
            f"[green]✓[/] Loaded [bold]{len(content)}[/] chars from [cyan]{path.name}[/]{trunc_note}\n"
            f"[dim]Preview: {preview}{suffix}[/]"
        )
    else:
        print(f"✓ Loaded {len(content)} chars from {path.name}{trunc_note}\nPreview: {preview}{suffix}")
    return _CMD_CONTINUE
