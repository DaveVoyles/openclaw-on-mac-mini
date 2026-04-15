"""
openclaw_cli_cmd_workflow.py — Workflow and automation command handlers.

Extracted from openclaw_cli.py.  Handlers that still need things that live
only in openclaw_cli are reached via the lazy _get_cli_mod() accessor to
avoid circular imports and to remain compatible with test monkeypatching.
"""
from __future__ import annotations

import re
from pathlib import Path

import openclaw_cli_session_cmds as _session_cmds_mod
from openclaw_cli_sessions import (
    append_event,
    apply_handoff,
    build_workspace_capsule,
    create_handoff,
    create_session,
    list_handoffs,
    load_handoff,
    load_watch_state,
    save_watch_state,
    update_session,
)
from openclaw_cli_types import ChatCommandContext
from openclaw_cli_ui_core import (
    _B,
    _CY,
    _DM,
    _GR,
    _R,
    _get_is_tty,
)
from openclaw_cli_watch import _print_watch_history, _print_watch_status

# Sentinel strings — mirror openclaw_cli._CMD_CONTINUE / _CMD_QUIT.
_CMD_CONTINUE: str = "continue"
_CMD_QUIT: str = "quit"


def _get_cli_mod():
    """Lazy import of main module for monkeypatch-safe back-references."""
    import openclaw_cli as _m
    return _m


# ---------------------------------------------------------------------------
# _cmd_watch
# ---------------------------------------------------------------------------

def _cmd_watch(ctx: ChatCommandContext) -> str:
    """/watch [status|history|retry-limit N|intervene TEXT] — inspect or control an active watch session."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    raw = ctx.args.strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else "status"
    rest = parts[1].strip() if len(parts) > 1 else ""

    state = load_watch_state(ctx.session_id)

    if sub in ("status", ""):
        if state is None:
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print("[dim]No active watch session.[/]  Start one with [cyan]openclaw watch --goal …[/]")
            else:
                print("No active watch session. Start one with: openclaw watch --goal …")
            return _CMD_CONTINUE
        _print_watch_status(state)

    elif sub == "history":
        if state is None:
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print("[dim]No watch history found.[/]")
            else:
                print("No watch history found.")
            return _CMD_CONTINUE
        _print_watch_history(state)

    elif sub == "retry-limit":
        if not rest:
            m._print_error("Usage: /watch retry-limit N")
            return _CMD_CONTINUE
        try:
            n = max(1, int(rest.split()[0]))
        except ValueError:
            m._print_error("Usage: /watch retry-limit N  (N must be a positive integer)")
            return _CMD_CONTINUE
        if state is None:
            m._print_error("No active watch session to update.")
            return _CMD_CONTINUE
        state["retry_limit"] = n
        save_watch_state(ctx.session_id, state)
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print(f"[green]✓[/] retry limit set to [cyan]{n}[/]")
        else:
            print(f"retry limit set to {n}")

    elif sub == "intervene":
        note_text = rest.strip('"').strip("'").strip()
        if not note_text:
            m._print_error('Usage: /watch intervene "note text"')
            return _CMD_CONTINUE
        if state is None:
            m._print_error("No active watch session to add a note to.")
            return _CMD_CONTINUE
        import uuid as _uuid_mod
        from datetime import datetime as _dt
        from datetime import timezone as _tz
        interventions = list(state.get("interventions") or [])
        note_entry = {
            "request_id": _uuid_mod.uuid4().hex[:10],
            "action": "operator-note",
            "status": "recorded",
            "actor": "operator",
            "reason": note_text[:240],
            "created_at": _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        interventions.append(note_entry)
        state["interventions"] = interventions[-20:]
        save_watch_state(ctx.session_id, state)
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print(f"[green]✓[/] operator note recorded  [dim]{note_text[:60]}[/]")
        else:
            print(f"operator note recorded: {note_text[:60]}")

    else:
        m._print_error("Usage: /watch [status|history|retry-limit N|intervene TEXT]")

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_plan
# ---------------------------------------------------------------------------

def _cmd_plan(ctx: ChatCommandContext) -> str:
    """/plan [<id> | status | focus | unlink] — show, link, focus, or unlink a plan for this session."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    arg = ctx.args.strip()
    if not arg:
        if session.plan_id:
            validation = m._validate_plan_id_local(session.plan_id, cwd=session.cwd)
            suffix = m._link_validation_suffix(validation)
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print(f"📋 plan: [yellow]{session.plan_id}[/]{suffix}")
            else:
                print(f"plan: {session.plan_id}{suffix}")
        else:
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print("[dim]No plan linked. Use:[/] /plan <id>")
            else:
                print("No plan linked. Use: /plan <id>")
        return _CMD_CONTINUE

    # /plan status — show linked plan details
    if arg.lower() == "status":
        if not session.plan_id:
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print("[dim]No plan linked. Use:[/] /plan <id>")
            else:
                print("No plan linked. Use: /plan <id>")
            return _CMD_CONTINUE
        validation = m._validate_plan_id_local(session.plan_id, cwd=session.cwd)
        if m._RICH_AVAILABLE and m._IS_TTY:
            from rich.panel import Panel as _RichPanel
            from rich.table import Table as _RichTable
            grid = _RichTable.grid(padding=(0, 2))
            grid.add_column(style="dim", min_width=10)
            grid.add_column()
            grid.add_row("plan id", f"[yellow]{session.plan_id}[/]")
            if validation.summary:
                grid.add_row("goal", f"[bold]{validation.summary[:100]}[/]")
            status_str = "✅ found" if validation.exists else "⚠ not found locally" if validation.available else "unavailable"
            grid.add_row("status", status_str)
            if validation.source:
                grid.add_row("file", f"[dim]{validation.source}[/]")
            if session.task_id:
                grid.add_row("task", f"[magenta]{session.task_id}[/]")
            m._RICH_CONSOLE.print(_RichPanel(grid, title="[bold cyan]📋 Plan Status[/]", border_style="cyan", padding=(0, 1)))
        else:
            print(f"Plan: {session.plan_id}")
            if validation.summary:
                print(f"  goal:   {validation.summary[:100]}")
            if session.task_id:
                print(f"  task:   {session.task_id}")
            print(f"  status: {'found' if validation.exists else 'not found'}")
        return _CMD_CONTINUE

    # /plan focus — show only current + next pending step from plan file
    if arg.lower() == "focus":
        if not session.plan_id:
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print("[dim]No plan linked.[/]")
            else:
                print("No plan linked.")
            return _CMD_CONTINUE
        validation = m._validate_plan_id_local(session.plan_id, cwd=session.cwd)
        if not validation.exists or not validation.source:
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print(f"[yellow]⚠[/] Plan file not found locally for [yellow]{session.plan_id}[/].")
            else:
                print(f"Plan file not found locally for {session.plan_id}.")
            return _CMD_CONTINUE
        try:
            plan_text = Path(validation.source).read_text(encoding="utf-8")
        except OSError:
            m._print_error(f"Could not read plan file: {validation.source}")
            return _CMD_CONTINUE
        # Find first unchecked task (- [ ]) and the next one after it
        lines = plan_text.splitlines()
        unchecked = [(i, line) for i, line in enumerate(lines) if re.match(r"^\s*-\s+\[ \]", line)]
        done_count = sum(1 for line in lines if re.match(r"^\s*-\s+\[x\]", line, re.IGNORECASE))
        if not unchecked:
            msg = "All tasks complete!" if done_count > 0 else "No task items found in plan."
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print(f"[green]✅ {msg}[/]  [dim]{session.plan_id}[/]")
            else:
                print(f"{msg}  ({session.plan_id})")
            return _CMD_CONTINUE
        focus_lines = _session_cmds_mod._build_plan_focus_lines(
            lines=lines,
            plan_id=session.plan_id,
            done_count=done_count,
            unchecked=unchecked,
            summary=validation.summary,
        )
        if m._RICH_AVAILABLE and m._IS_TTY:
            from rich.markdown import Markdown as _RichMarkdown
            from rich.panel import Panel as _RichPanel
            m._RICH_CONSOLE.print(_RichPanel(
                _RichMarkdown("\n".join(focus_lines)),
                title=f"[bold cyan]📋 Plan Focus — {session.plan_id}[/]",
                border_style="cyan",
                padding=(0, 1),
            ))
        else:
            for fl in focus_lines:
                print(fl)
        return _CMD_CONTINUE

    if arg.lower() == "unlink":
        if not session.plan_id:
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print("[dim]No plan is currently linked.[/]")
            else:
                print("No plan is currently linked.")
            return _CMD_CONTINUE
        old = session.plan_id
        update_session(ctx.session_id, plan_id="")
        append_event(
            ctx.session_id,
            kind="chat",
            content="/plan unlink",
            metadata={"summary": f"unlinked plan {old}"},
        )
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print(f"[dim]unlinked plan:[/] {old}")
        else:
            print(f"unlinked plan: {old}")
        return _CMD_CONTINUE

    validation = m._validate_plan_id_local(arg, cwd=session.cwd)
    if not validation.available:
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print("[dim]local plan validation unavailable; linking anyway.[/]")
        else:
            print("local plan validation unavailable in this install; linking anyway.")
    elif validation.exists:
        detail = f": {validation.summary}" if validation.summary else ""
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print(f"[green]✓[/] confirmed plan [yellow]{arg}[/]{detail}")
        else:
            print(f"confirmed local plan '{arg}'{detail}")
    else:
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print(f"[yellow]⚠[/] plan [dim]{arg}[/] not found locally; linking anyway.")
        else:
            print(f"warning: local plan '{arg}' was not found; linking anyway.")
    update_session(ctx.session_id, plan_id=arg)
    append_event(
        ctx.session_id,
        kind="chat",
        content=f"/plan {arg}",
        metadata={"summary": f"linked plan {arg}"},
    )
    if m._RICH_AVAILABLE and m._IS_TTY:
        m._RICH_CONSOLE.print(f"📋 plan → [yellow]{arg}[/]")
    else:
        print(f"plan → {arg}")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_task
# ---------------------------------------------------------------------------

def _cmd_task(ctx: ChatCommandContext) -> str:
    """/task [<id> | unlink] — show, link, or unlink a task for this session."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    arg = ctx.args.strip()
    if not arg:
        if session.task_id:
            validation = m._validate_task_id_local(session.task_id, cwd=session.cwd)
            suffix = m._link_validation_suffix(validation)
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print(f"✅ task: [yellow]{session.task_id}[/]{suffix}")
            else:
                print(f"task: {session.task_id}{suffix}")
        else:
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print("[dim]No task linked. Use:[/] /task <id>")
            else:
                print("No task linked. Use: /task <id>")
        return _CMD_CONTINUE

    if arg.lower() == "unlink":
        if not session.task_id:
            if m._RICH_AVAILABLE and m._IS_TTY:
                m._RICH_CONSOLE.print("[dim]No task is currently linked.[/]")
            else:
                print("No task is currently linked.")
            return _CMD_CONTINUE
        old = session.task_id
        update_session(ctx.session_id, task_id="")
        append_event(
            ctx.session_id,
            kind="chat",
            content="/task unlink",
            metadata={"summary": f"unlinked task {old}"},
        )
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print(f"[dim]unlinked task:[/] {old}")
        else:
            print(f"unlinked task: {old}")
        return _CMD_CONTINUE

    validation = m._validate_task_id_local(arg, cwd=session.cwd)
    if not validation.available:
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print("[dim]local task validation unavailable; linking anyway.[/]")
        else:
            print("local task validation unavailable in this install; linking anyway.")
    elif validation.exists:
        detail = f": {validation.summary}" if validation.summary else ""
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print(f"[green]✓[/] confirmed task [yellow]{arg}[/]{detail}")
        else:
            print(f"confirmed local task '{arg}'{detail}")
    else:
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print(f"[yellow]⚠[/] task [dim]{arg}[/] not found locally; linking anyway.")
        else:
            print(f"warning: local task '{arg}' was not found; linking anyway.")
    update_session(ctx.session_id, task_id=arg)
    append_event(
        ctx.session_id,
        kind="chat",
        content=f"/task {arg}",
        metadata={"summary": f"linked task {arg}"},
    )
    if m._RICH_AVAILABLE and m._IS_TTY:
        m._RICH_CONSOLE.print(f"✅ task → [yellow]{arg}[/]")
    else:
        print(f"task → {arg}")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_risk
# ---------------------------------------------------------------------------

def _cmd_risk(ctx: ChatCommandContext) -> str:
    """/risk [list|add LEVEL TEXT|clear INDEX] — track blocking risks for handoffs."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    raw = (ctx.args or "").strip()
    parts = raw.split(None, 2)
    sub = parts[0].lower() if parts else "list"
    if sub in {"", "list", "status"}:
        risks = m._risk_entries(session.session_id)
        print("Open risks:")
        if not risks:
            print("  (none)")
            return _CMD_CONTINUE
        for index, entry in enumerate(risks, start=1):
            level = str(entry.get("risk_level") or "medium").upper()
            actor = str(entry.get("actor") or "operator")
            text = str(entry.get("content") or entry.get("summary") or "").strip()
            print(f"  {index}. {level} · {actor} · {text}")
        return _CMD_CONTINUE
    if sub == "add":
        if len(parts) < 3:
            m._print_error("Usage: /risk add <critical|high|medium|low> TEXT")
            return _CMD_CONTINUE
        level = parts[1].strip().lower()
        if level not in {"critical", "high", "medium", "low"}:
            m._print_error("Risk level must be one of: critical, high, medium, low")
            return _CMD_CONTINUE
        text = parts[2].strip()
        if not text:
            m._print_error("Usage: /risk add <critical|high|medium|low> TEXT")
            return _CMD_CONTINUE
        append_event(
            session.session_id,
            kind="collab",
            content=text,
            metadata={
                "summary": f"risk {level}: {' '.join(text.split())[:90]}",
                "actor": "operator",
                "tags": [level, "risk"],
                "collab_kind": "risk",
                "risk_level": level,
                "risk_status": "open",
            },
        )
        print(f"Recorded {level} risk.")
        print(text)
        return _CMD_CONTINUE
    if sub == "clear":
        if len(parts) < 2 or not parts[1].isdigit():
            m._print_error("Usage: /risk clear <index>")
            return _CMD_CONTINUE
        risks = m._risk_entries(session.session_id)
        index = int(parts[1])
        if index < 1 or index > len(risks):
            m._print_error(f"Risk index out of range: {index}")
            return _CMD_CONTINUE
        entry = risks[index - 1]
        text = str(entry.get("content") or entry.get("summary") or "").strip()
        level = str(entry.get("risk_level") or "medium").strip().lower()
        append_event(
            session.session_id,
            kind="collab",
            content=text,
            metadata={
                "summary": f"risk cleared: {' '.join(text.split())[:90]}",
                "actor": "operator",
                "tags": [level, "risk", "cleared"],
                "collab_kind": "risk",
                "risk_level": level,
                "risk_status": "cleared",
            },
        )
        print(f"Cleared risk {index}.")
        return _CMD_CONTINUE
    m._print_error("Usage: /risk [list|add LEVEL TEXT|clear INDEX]")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_incident
# ---------------------------------------------------------------------------

def _cmd_incident(ctx: ChatCommandContext) -> str:
    """/incident [list|log TEXT|resolve INDEX] — track operator incidents for the current session."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    raw = (ctx.args or "").strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""
    if sub in {"", "list", "status"}:
        incidents = m._incident_entries(session.session_id)
        print("Open incidents:")
        if not incidents:
            print("  (none)")
            return _CMD_CONTINUE
        for index, entry in enumerate(incidents, start=1):
            actor = str(entry.get("actor") or "operator")
            text = str(entry.get("content") or entry.get("summary") or "").strip()
            print(f"  {index}. {actor} · {text}")
        return _CMD_CONTINUE
    if sub == "log":
        text = rest.strip()
        if not text:
            m._print_error("Usage: /incident log TEXT")
            return _CMD_CONTINUE
        append_event(
            session.session_id,
            kind="collab",
            content=text,
            metadata={
                "summary": f"incident: {' '.join(text.split())[:90]}",
                "actor": "operator",
                "tags": ["incident"],
                "collab_kind": "incident",
                "incident_status": "open",
            },
        )
        print("Recorded incident.")
        print(text)
        return _CMD_CONTINUE
    if sub == "resolve":
        if not rest.isdigit():
            m._print_error("Usage: /incident resolve <index>")
            return _CMD_CONTINUE
        incidents = m._incident_entries(session.session_id)
        index = int(rest)
        if index < 1 or index > len(incidents):
            m._print_error(f"Incident index out of range: {index}")
            return _CMD_CONTINUE
        entry = incidents[index - 1]
        text = str(entry.get("content") or entry.get("summary") or "").strip()
        append_event(
            session.session_id,
            kind="collab",
            content=text,
            metadata={
                "summary": f"incident resolved: {' '.join(text.split())[:90]}",
                "actor": "operator",
                "tags": ["incident", "resolved"],
                "collab_kind": "incident",
                "incident_status": "resolved",
            },
        )
        print(f"Resolved incident {index}.")
        return _CMD_CONTINUE
    m._print_error("Usage: /incident [list|log TEXT|resolve INDEX]")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_workspace
# ---------------------------------------------------------------------------

def _cmd_workspace(ctx: ChatCommandContext) -> str:
    """/workspace [status|save|list|restore NAME] — manage workspace recovery capsules."""
    m = _get_cli_mod()
    raw = (ctx.args or "").strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else "status"
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub in {"status", ""}:
        session = m._require_session_or_warn(ctx)
        if session is None:
            return _CMD_CONTINUE
        m._print_workspace_capsule(build_workspace_capsule(session.session_id))
        return _CMD_CONTINUE

    if sub == "save":
        session = m._require_session_or_warn(ctx)
        if session is None:
            return _CMD_CONTINUE
        note = rest.strip('"').strip("'")
        handoff_id = create_handoff(session.session_id, note=note)
        manifest = load_handoff(handoff_id) or {}
        capsule = manifest.get("workspace_capsule") if isinstance(manifest, dict) else {}
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print(f"[bold green]Saved workspace capsule[/] [cyan]{handoff_id}[/]")
        else:
            print(f"Saved workspace capsule {handoff_id}")
        if isinstance(capsule, dict) and capsule:
            m._print_workspace_capsule(capsule, title="Saved Workspace Capsule")
        return _CMD_CONTINUE

    if sub == "list":
        handoffs = list_handoffs(limit=20)
        if not handoffs:
            print(f"  {_DM}No workspace capsules found. Use /workspace save to create one.{_R}")
            return _CMD_CONTINUE
        if m._RICH_AVAILABLE and m._IS_TTY:
            from rich.table import Table as _RichTable
            tbl = _RichTable("Capsule", "Session", "Files", "Outputs", "Watch", "Created", border_style="dim", header_style="bold cyan")
            for item in handoffs:
                capsule = item.get("workspace_capsule") if isinstance(item.get("workspace_capsule"), dict) else {}
                tbl.add_row(
                    str(item.get("id") or "")[:30],
                    str(item.get("source_session_id") or "")[:8],
                    str(capsule.get("tracked_file_count", len(item.get("tracked_files") or []))),
                    str(capsule.get("output_count", len(item.get("outputs_snapshot") or []))),
                    str(capsule.get("watch_status", "")) or "—",
                    str(item.get("created_at") or "")[:19],
                )
            m._RICH_CONSOLE.print(tbl)
        else:
            print("Workspace capsules:")
            for item in handoffs:
                capsule = item.get("workspace_capsule") if isinstance(item.get("workspace_capsule"), dict) else {}
                print(
                    f"  {str(item.get('id') or '')[:30]}  "
                    f"files:{capsule.get('tracked_file_count', len(item.get('tracked_files') or []))}  "
                    f"outputs:{capsule.get('output_count', len(item.get('outputs_snapshot') or []))}  "
                    f"watch:{str(capsule.get('watch_status') or '—')}"
                )
        return _CMD_CONTINUE

    if sub == "restore":
        if not rest:
            m._print_error("Usage: /workspace restore NAME")
            return _CMD_CONTINUE
        manifest = load_handoff(rest)
        if manifest is None:
            m._print_error(f"Workspace capsule not found: {rest}")
            return _CMD_CONTINUE
        new_session = create_session()
        new_session_id = new_session.session_id if hasattr(new_session, "session_id") else str(new_session)
        result = apply_handoff(manifest, new_session_id)
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print(f"[bold green]Workspace restored[/] [cyan]{new_session_id}[/]")
            m._RICH_CONSOLE.print(f"  [dim]Resume:[/] openclaw --session {new_session_id}")
        else:
            print(f"Workspace restored {new_session_id}")
            print(f"  Resume: openclaw --session {new_session_id}")
        restored = list(result.get("restored") or [])
        if restored:
            print(f"  Restored: {', '.join(str(item) for item in restored[:6])}")
        warnings = list(result.get("warnings") or [])
        if warnings:
            print(f"  Warnings: {', '.join(str(item) for item in warnings[:4])}")
        return _CMD_CONTINUE

    m._print_error("Usage: /workspace [status|save|list|restore NAME]")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_macro
# ---------------------------------------------------------------------------

def _cmd_macro(ctx: ChatCommandContext) -> str:
    """Manage named command macros. Sub-commands: list, save, show, rm, run."""
    import re as _re
    m = _get_cli_mod()

    args = (ctx.args or "").strip()
    macros = m._workflow_store()
    is_tty = _get_is_tty()

    parts = args.split(None, 1)
    token = parts[0].lower() if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""

    # ── list ──────────────────────────────────────────────────────────────────
    if token in ("list", "ls") or not args:
        if m._RICH_AVAILABLE and is_tty:
            from rich.panel import Panel as _RichPanel
            from rich.table import Table as _RichTable
            grid = _RichTable.grid(padding=(0, 2))
            grid.add_column(style="cyan", no_wrap=True)
            grid.add_column(style="dim")
            if macros:
                for name, cmds in sorted(macros.items()):
                    grid.add_row(name, f"{len(cmds)} command{'s' if len(cmds) != 1 else ''}")
            else:
                grid.add_row(f"{m._e('🔧', '')} (no macros defined)", "")
            m._RICH_CONSOLE.print(_RichPanel(
                grid,
                title=f"{m._e('🔧', '')} Macros",
                border_style="cyan",
                padding=(0, 1),
            ))
        else:
            print(f"{_B}Macros:{_R}")
            if macros:
                for name, cmds in sorted(macros.items()):
                    print(f"  {_CY}{name}{_R}  {_DM}({len(cmds)} command{'s' if len(cmds) != 1 else ''}){_R}")
            else:
                print(f"  {_DM}(no macros defined){_R}")
        return _CMD_CONTINUE

    # ── save ──────────────────────────────────────────────────────────────────
    if token == "save":
        save_parts = rest.split()
        if not save_parts:
            m._print_error("Usage: /macro save <name> [last N]")
            return _CMD_CONTINUE

        macro_name = save_parts[0]
        if not _re.match(r'^[A-Za-z0-9_-]{1,40}$', macro_name):
            m._print_error(
                "Macro name must be 1-40 alphanumeric characters, hyphens, or underscores."
            )
            return _CMD_CONTINUE

        n = 5
        if len(save_parts) >= 3 and save_parts[1].lower() == "last":
            try:
                n = max(1, min(int(save_parts[2]), 20))
            except ValueError:
                m._print_error("Usage: /macro save <name> [last N]")
                return _CMD_CONTINUE
        elif len(save_parts) == 2:
            m._print_error("Usage: /macro save <name> [last N]")
            return _CMD_CONTINUE

        hist = m._history_command_texts(20)
        if not hist:
            m._print_error("No command history to save — run some commands first")
            return _CMD_CONTINUE

        if len(macros) >= 30 and macro_name not in macros:
            m._print_error("Maximum of 30 macros reached. Remove one first with /macro rm <name>.")
            return _CMD_CONTINUE

        commands = hist[-n:]
        commands = commands[:20]
        updated = macro_name in macros
        macros[macro_name] = commands
        m._save_prefs()

        suffix = f"  {_GR}(updated){_R}" if updated else ""
        print(
            f"  {_GR}{m._e('✅', '[OK]')} Macro '{_CY}{macro_name}{_R}{_GR}' saved"
            f" ({len(commands)} command{'s' if len(commands) != 1 else ''}){_R}{suffix}"
        )
        return _CMD_CONTINUE

    # ── show ──────────────────────────────────────────────────────────────────
    if token == "show":
        if not rest:
            m._print_error("Usage: /macro show <name>")
            return _CMD_CONTINUE
        name = rest.split()[0]
        if name not in macros:
            m._print_error(f"Macro '{name}' not found")
            return _CMD_CONTINUE
        cmds = macros[name]
        if m._RICH_AVAILABLE and is_tty:
            from rich.console import Group as _RichGroup
            from rich.panel import Panel as _RichPanel
            from rich.text import Text as _RichText
            lines = []
            for i, cmd in enumerate(cmds, start=1):
                line = _RichText()
                line.append(f"  {i:>2}  ", style="dim")
                line.append(cmd, style="bold cyan")
                lines.append(line)
            m._RICH_CONSOLE.print(_RichPanel(
                _RichGroup(*lines),
                title=f"{m._e('🔧', '')} Macro: {name}",
                border_style="cyan",
                padding=(0, 1),
            ))
        else:
            print(f"{_B}Macro '{name}':{_R}")
            for i, cmd in enumerate(cmds, start=1):
                print(f"  {_DM}{i:>2}{_R}  {_CY}{cmd}{_R}")
        return _CMD_CONTINUE

    # ── rm ────────────────────────────────────────────────────────────────────
    if token == "rm":
        if not rest:
            m._print_error("Usage: /macro rm <name>")
            return _CMD_CONTINUE
        name = rest.split()[0]
        if name not in macros:
            m._print_error(f"Macro '{name}' not found")
            return _CMD_CONTINUE
        del macros[name]
        m._save_prefs()
        print(f"  {_GR}{m._e('✅', '[OK]')} Macro '{name}' removed{_R}")
        return _CMD_CONTINUE

    # ── run ───────────────────────────────────────────────────────────────────
    if token == "run":
        if not rest:
            m._print_error("Usage: /macro run <name>")
            return _CMD_CONTINUE
        return m._macro_run(ctx, rest.split()[0])

    m._print_error(f"Unknown /macro sub-command '{token}'. Use: list, save, show, rm, run")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_macrostatus
# ---------------------------------------------------------------------------

def _cmd_macrostatus(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/macrostatus — show saved macros with step counts."""
    m = _get_cli_mod()
    macros = m._PREFS.get("macros", {})
    is_tty = _get_is_tty()
    if not macros:
        msg = "No macros saved. Use /macro save <name> to create one."
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    if m._RICH_AVAILABLE and is_tty:
        from rich.box import SIMPLE as _RICH_BOX_SIMPLE
        from rich.table import Table as _RichTableLocal
        tbl = _RichTableLocal(box=_RICH_BOX_SIMPLE, show_header=True, header_style="bold cyan")
        tbl.add_column("Macro", style="bold green")
        tbl.add_column("Steps", justify="right")
        tbl.add_column("Preview", style="dim")
        for name, steps in macros.items():
            if isinstance(steps, list):
                count = str(len(steps))
                preview = str(steps[0])[:40] if steps else ""
            else:
                count = "1"
                preview = str(steps)[:40]
            tbl.add_row(name, count, preview)
        m._RICH_CONSOLE.print("\n[bold cyan]📋 Saved Macros[/]\n")
        m._RICH_CONSOLE.print(tbl)
    else:
        print("\n📋 Saved Macros")
        print(f"{'Name':<20} {'Steps':>6}  Preview")
        print("─" * 55)
        for name, steps in macros.items():
            if isinstance(steps, list):
                count = len(steps)
                preview = str(steps[0])[:30] if steps else ""
            else:
                count = 1
                preview = str(steps)[:30]
            print(f"  {name:<18} {count:>6}  {preview}")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_workflow
# ---------------------------------------------------------------------------

def _cmd_workflow(ctx: ChatCommandContext) -> str:
    """/workflow — manage previewable workflows backed by the macro store."""
    m = _get_cli_mod()
    args = (ctx.args or "").strip()
    workflows = m._workflow_store()
    parts = args.split(None, 1)
    token = parts[0].lower() if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""

    if token in {"list", "ls"} or not args:
        print(f"{_B}Workflows:{_R}")
        if workflows:
            for name, steps in sorted(workflows.items()):
                print(f"  {_CY}{name}{_R}  {_DM}({len(steps)} step{'s' if len(steps) != 1 else ''}){_R}")
        else:
            print(f"  {_DM}(no workflows saved — use /workflow save <name> [last N]){_R}")
        return _CMD_CONTINUE

    if token == "save":
        return _cmd_macro(ChatCommandContext(history=ctx.history, session_id=ctx.session_id, args=f"save {rest}"))

    if token == "show":
        return _cmd_macro(ChatCommandContext(history=ctx.history, session_id=ctx.session_id, args=f"show {rest}"))

    if token in {"rm", "remove"}:
        return _cmd_macro(ChatCommandContext(history=ctx.history, session_id=ctx.session_id, args=f"rm {rest}"))

    if token == "preview":
        if not rest:
            m._print_error("Usage: /workflow preview <name>")
            return _CMD_CONTINUE
        name = rest.split()[0]
        if name not in workflows:
            m._print_error(f"Workflow '{name}' not found")
            return _CMD_CONTINUE
        m._print_workflow_preview(name, list(workflows[name]), ctx)
        return _CMD_CONTINUE

    if token == "run":
        if not rest:
            m._print_error("Usage: /workflow run <name>")
            return _CMD_CONTINUE
        return m._macro_run(ctx, rest.split()[0], kind="workflow")

    m._print_error("Unknown /workflow sub-command. Use: list, save, show, preview, run, rm")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_dashboard
# ---------------------------------------------------------------------------

def _cmd_dashboard(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/dashboard — show the power dashboard: sessions, stats, pins, and system status."""
    m = _get_cli_mod()
    raw = (ctx.args or "").strip().lower()
    if raw == "automation":
        m._print_automation_dashboard()
        return _CMD_CONTINUE
    is_tty = _get_is_tty()

    # Gather data
    _PREFS = m._PREFS
    cmd_history = _PREFS.get("cmd_history", [])
    ratings = _PREFS.get("ratings", [])
    pins = _PREFS.get("pins", {})
    macros = _PREFS.get("macros", {})
    aliases = _PREFS.get("aliases", {})
    snapshots = _PREFS.get("snapshots", {})
    custom_keybinds = _PREFS.get("custom_keybinds", {})

    total_prompts = sum(1 for e in cmd_history if isinstance(e, dict) and not e.get("text", "").startswith("/"))
    total_commands = sum(1 for e in cmd_history if isinstance(e, dict) and e.get("text", "").startswith("/"))
    total_ratings = len(ratings)
    avg_rating = 0.0
    if ratings:
        scores = []
        for r in ratings:
            if isinstance(r, dict):
                s = r.get("score", r.get("rating", 0))
            else:
                try:
                    s = int(r)
                except (ValueError, TypeError):
                    s = 0
            scores.append(s)
        avg_rating = sum(scores) / len(scores) if scores else 0

    # Token estimates
    total_chars = sum(len(e.get("text", "")) for e in cmd_history if isinstance(e, dict))
    est_tokens = total_chars // 4

    if m._RICH_AVAILABLE and is_tty:
        from rich.box import SIMPLE
        from rich.columns import Columns
        from rich.panel import Panel
        from rich.table import Table

        m._RICH_CONSOLE.print()

        # Header
        m._RICH_CONSOLE.rule("[bold cyan]🦞 OpenClaw Dashboard[/]", style="cyan")
        m._RICH_CONSOLE.print()

        # Row 1: Stats + Pins side by side
        stats_tbl = Table(box=SIMPLE, show_header=False, padding=(0, 1))
        stats_tbl.add_column("Metric", style="dim", width=22)
        stats_tbl.add_column("Value", style="bold")
        stats_tbl.add_row("Total prompts", str(total_prompts))
        stats_tbl.add_row("Slash commands used", str(total_commands))
        stats_tbl.add_row("Responses rated", str(total_ratings))
        stats_tbl.add_row("Avg rating", f"{avg_rating:.1f} ⭐" if avg_rating else "—")
        stats_tbl.add_row("Est. tokens used", f"~{est_tokens:,}")
        stats_tbl.add_row("Macros saved", str(len(macros)))
        stats_tbl.add_row("Aliases", str(len(aliases)))
        stats_tbl.add_row("Snapshots", str(len(snapshots)))
        stats_tbl.add_row("Custom keybinds", str(len(custom_keybinds)))
        stats_panel = Panel(stats_tbl, title="[bold cyan]📊 Stats[/]", border_style="cyan", padding=(0, 1))

        pins_tbl = Table(box=SIMPLE, show_header=False, padding=(0, 1))
        pins_tbl.add_column("Key", style="bold yellow", width=14)
        pins_tbl.add_column("Value", style="default")
        if pins:
            for k, v in list(pins.items())[:8]:
                val_str = str(v)[:35] + ("…" if len(str(v)) > 35 else "")
                pins_tbl.add_row(k, val_str)
        else:
            pins_tbl.add_row("[dim]no pins[/]", "[dim]use /pin key value[/]")
        pins_panel = Panel(pins_tbl, title="[bold yellow]📌 Pins[/]", border_style="yellow", padding=(0, 1))

        m._RICH_CONSOLE.print(Columns([stats_panel, pins_panel], equal=True, expand=True))

        # Row 2: Recent activity
        recent = []
        for e in reversed(cmd_history[-10:]):
            if isinstance(e, dict):
                text = e.get("text", e.get("prompt", e.get("cmd", "")))
                ts = e.get("timestamp", e.get("ts", ""))
                if text:
                    recent.append((text[:55], ts[:10] if ts else ""))

        activity_tbl = Table(box=SIMPLE, show_header=True, header_style="bold cyan")
        activity_tbl.add_column("Recent", style="default")
        activity_tbl.add_column("Date", style="dim", width=12)
        for text, ts in reversed(recent[:5]):
            style = "bold green" if text.startswith("/") else "default"
            activity_tbl.add_row(f"[{style}]{text}[/]", ts)
        if not recent:
            activity_tbl.add_row("[dim]No history yet[/]", "")

        activity_panel = Panel(activity_tbl, title="[bold cyan]🕐 Recent Activity[/]", border_style="dim", padding=(0, 1))
        m._RICH_CONSOLE.print(activity_panel)

        # Row 3: Quick reference
        m._RICH_CONSOLE.print()
        m._RICH_CONSOLE.print(
            f"[dim]Build:[/] [bold]{m._CLI_BUILD}[/]  "
            f"[dim]Prefs:[/] [bold]{len(_PREFS)} keys[/]  "
            f"[dim]Commands:[/] [bold]{len(m._BUILTIN_COMMAND_NAMES)}[/]  "
            f"[dim]Type[/] [bold cyan]/help[/] [dim]for full reference[/]"
        )
        m._RICH_CONSOLE.print()
        m._RICH_CONSOLE.rule(style="dim")
        m._RICH_CONSOLE.print()

    else:
        # Plain-text dashboard
        print(f"\n{'='*60}")
        print(f"  🦞 OpenClaw Dashboard  [{m._CLI_BUILD}]")
        print(f"{'='*60}")
        print(f"  Prompts:      {total_prompts}")
        print(f"  Commands:     {total_commands}")
        print(f"  Ratings:      {total_ratings}  (avg: {avg_rating:.1f})")
        print(f"  Est tokens:   ~{est_tokens:,}")
        print(f"  Macros:       {len(macros)}")
        print(f"  Pins:         {len(pins)}")
        print(f"  Snapshots:    {len(snapshots)}")
        print(f"  Commands reg: {len(m._BUILTIN_COMMAND_NAMES)}")
        if pins:
            print("\n  📌 Pins:")
            for k, v in list(pins.items())[:5]:
                print(f"     {k}: {str(v)[:40]}")
        print("\n  Type /help for full reference.")
        print(f"{'='*60}\n")

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_alerts
# ---------------------------------------------------------------------------

def _cmd_alerts(ctx: ChatCommandContext) -> str:
    """/alerts [list|acknowledge INDEX] — inspect computed operator alerts."""
    m = _get_cli_mod()
    raw = (ctx.args or "").strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""
    alerts = m._collect_operator_alerts()
    acked = m._acknowledged_alert_ids()
    visible = [item for item in alerts if str(item.get("id") or "") not in acked]
    if sub in {"", "list"}:
        print("Operator alerts")
        print("---------------")
        if not visible:
            print("  (none)")
            return _CMD_CONTINUE
        for index, alert in enumerate(visible, start=1):
            print(
                f"  {index}. [{str(alert.get('severity') or 'info').upper()}] "
                f"{str(alert.get('title') or '')} · {str(alert.get('message') or '')}"
            )
        return _CMD_CONTINUE
    if sub in {"ack", "acknowledge"}:
        if not rest.isdigit():
            m._print_error("Usage: /alerts acknowledge <index>")
            return _CMD_CONTINUE
        index = int(rest)
        if index < 1 or index > len(visible):
            m._print_error(f"Alert index out of range: {index}")
            return _CMD_CONTINUE
        acked.add(str(visible[index - 1].get("id") or ""))
        m._set_acknowledged_alert_ids(acked)
        print(f"Acknowledged alert {index}.")
        return _CMD_CONTINUE
    m._print_error("Usage: /alerts [list|acknowledge INDEX]")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_fleet
# ---------------------------------------------------------------------------

def _cmd_fleet(ctx: ChatCommandContext) -> str:
    """/fleet [status|health] — show cross-session automation health summaries."""
    m = _get_cli_mod()
    raw = (ctx.args or "").strip().lower()
    sub = raw or "status"
    if sub not in {"status", "health"}:
        m._print_error("Usage: /fleet [status|health]")
        return _CMD_CONTINUE
    m._print_automation_dashboard()
    return _CMD_CONTINUE
