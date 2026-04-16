"""Session lifecycle command handlers.

Extracted from openclaw_cli.py.  Handlers delegate to helpers that live in the
session sub-modules; anything that still lives only in openclaw_cli is reached
via the lazy _get_cli_mod() accessor to avoid circular imports.
"""
from __future__ import annotations

import shlex
import shutil

from openclaw_cli_prefs import _PREFS
from openclaw_cli_session_cmds import (
    _build_event_label,
    _event_preview_lines,
    _event_recovery_actions,
    _build_handoff_check_lines,
)
from openclaw_cli_sessions import (
    SessionSummary,
    apply_handoff,
    create_handoff,
    create_session,
    create_session_bookmark,
    find_session_bookmark,
    list_handoffs,
    list_session_bookmarks,
    list_sessions,
    load_conversation_history,
    load_events,
    load_handoff,
    load_session,
    save_session,
)
from openclaw_cli_types import ChatCommandContext
from openclaw_cli_ui_core import (
    _B,
    _BCY,
    _BRE,
    _CY,
    _DM,
    _GR,
    _R,
    _YE,
    _get_is_tty,
)

# Sentinel strings — mirror openclaw_cli._CMD_CONTINUE / _CMD_QUIT.
_CMD_CONTINUE: str = "continue"
_CMD_QUIT: str = "quit"


def _get_cli_mod():
    """Lazy accessor for openclaw_cli to avoid circular imports."""
    import openclaw_cli as _m
    return _m


# ---------------------------------------------------------------------------
# _cmd_session
# ---------------------------------------------------------------------------

def _cmd_session(ctx: ChatCommandContext) -> str:
    """/session — show a compact summary of the current session."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    m._print_session_summary(session)
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_events
# ---------------------------------------------------------------------------

def _cmd_events(ctx: ChatCommandContext) -> str:
    """/events [n|decisions [n]] — show the last n events; 'decisions' filters to routing/decision kinds."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    _DECISION_KINDS = {"route", "plan", "approval", "checkpoint", "exec", "edit"}

    args = ctx.args.strip()
    decisions_only = False
    n = 5

    if args.startswith("decisions"):
        decisions_only = True
        remainder = args[len("decisions"):].strip()
        if remainder:
            try:
                n = int(remainder)
            except ValueError:
                m._print_error("Usage: /events decisions [n]")
                return _CMD_CONTINUE
    elif args:
        try:
            n = int(args)
        except ValueError:
            m._print_error("Usage: /events [n|decisions [n]]")
            return _CMD_CONTINUE

    load_limit = n * 10 if decisions_only else n
    events = load_events(ctx.session_id, limit=load_limit)

    if decisions_only:
        events = [ev for ev in events if str(ev.get("kind") or "").strip() in _DECISION_KINDS]
        events = events[:n]

    if not events:
        if m._RICH_AVAILABLE and m._IS_TTY:
            m._RICH_CONSOLE.print("[dim]No events recorded yet.[/]  [dim]Events appear after /analyze, /write, /exec, /edit, or chat turns.[/]")
        else:
            print("No events recorded yet. Events appear after /analyze, /write, /exec, /edit, or chat turns.")
        return _CMD_CONTINUE

    latest_event = events[0]
    latest_kind = str(latest_event.get("kind") or "event").strip() or "event"
    latest_ts = str(
        latest_event.get("timestamp") or latest_event.get("at") or latest_event.get("created_at") or "—"
    ).strip() or "—"
    summary_lines = [
        f"showing {len(events)} recent event{'s' if len(events) != 1 else ''}",
        f"latest kind: {latest_kind}",
        f"latest timestamp: {latest_ts}",
    ]
    if decisions_only:
        summary_lines.append("scope: decision-only routing / approval / checkpoint lane")
    detail_lines = _event_preview_lines(events)
    action_lines = _event_recovery_actions(events, decisions_only=decisions_only)
    if hasattr(m, "_print_dashboard_surface"):
        m._print_dashboard_surface(
            "Event Preview Strip",
            summary_lines=summary_lines,
            detail_lines=detail_lines,
            action_lines=action_lines,
            border_style="dim",
        )
    else:
        print("Event Preview Strip")
        print("Summary:")
        for line in summary_lines:
            print(f"  - {line}")
        if detail_lines:
            print("")
            print("Details:")
            for line in detail_lines:
                print(f"  - {line}")
        if action_lines:
            print("")
            print("Actions:")
            for line in action_lines:
                print(f"  - {line}")

    _KIND_COLORS = {
        "chat": "dim", "prompt": "white", "analyze": "cyan", "research": "blue",
        "write": "yellow", "exec": "bold yellow", "assistant": "green",
        "edit": "magenta", "error": "red", "watch": "cyan",
        "route": "bold cyan", "plan": "bold blue", "approval": "bold yellow",
        "checkpoint": "bold green",
    }
    if m._RICH_AVAILABLE and m._IS_TTY:
        if decisions_only:
            m._RICH_CONSOLE.print("[dim]Decision-only view — routing, approval, exec, edit events[/]")
        table = m._RichTable(border_style="dim", show_edge=True, pad_edge=True, header_style="bold cyan")
        table.add_column("Time", style="dim", no_wrap=True)
        table.add_column("Kind", no_wrap=True)
        table.add_column("Summary")
        for ev in events:
            ts = str(ev.get("timestamp") or ev.get("at") or ev.get("created_at") or "").strip()
            ts_short = ts[11:19] if len(ts) > 10 else ts
            kind = str(ev.get("kind") or "").strip()
            label = _build_event_label(ev)
            color = _KIND_COLORS.get(kind, "dim")
            table.add_row(ts_short, f"[{color}]{kind}[/]", label)
        m._RICH_CONSOLE.print(table)
    else:
        if decisions_only:
            print("Decision-only view — routing, approval, exec, edit events")
        for ev in events:
            ts = str(ev.get("timestamp") or ev.get("at") or ev.get("created_at") or "").strip()
            kind = str(ev.get("kind") or "").strip()
            label = _build_event_label(ev, excerpt_len=100)
            print(f"[{ts}] {kind}: {label}")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_sessions
# ---------------------------------------------------------------------------

def _cmd_sessions(ctx: ChatCommandContext) -> str:
    """/sessions [search QUERY | related] — browse recent sessions."""
    m = _get_cli_mod()
    is_tty = _get_is_tty()
    token = ctx.args.strip()
    token_lower = token.lower()
    overlay_query = ""
    wants_overlay = False
    if token_lower == "overlay":
        wants_overlay = True
    elif token_lower.startswith("overlay "):
        wants_overlay = True
        overlay_query = token[8:].strip()
    elif token_lower == "pick":
        wants_overlay = True
    elif token_lower.startswith("pick "):
        wants_overlay = True
        overlay_query = token[5:].strip()

    if token_lower.startswith("open "):
        target = token[5:].strip()
        print("\n  To resume that session, exit and run:")
        print(f"    {_BCY}openclaw session resume {target}{_R}\n")
        return _CMD_CONTINUE

    if token_lower == "related":
        if not ctx.session_id:
            print(f"  {_DM}No active session — start one first.{_R}")
            return _CMD_CONTINUE
        curr = load_session(ctx.session_id)
        if curr is None:
            print(f"  {_DM}Session not found.{_R}")
            return _CMD_CONTINUE
        curr_cwd = (curr.cwd or "").strip()
        curr_files = set(curr.files or [])
        all_sessions = list_sessions(limit=100)
        scored: list[tuple[int, SessionSummary]] = []
        for s in all_sessions:
            if s.session_id == ctx.session_id:
                continue
            score = 0
            if curr_cwd and s.cwd == curr_cwd:
                score += 3
            if curr_files and curr_files & set(s.files or []):
                score += 2
            if curr.plan_id and s.plan_id == curr.plan_id:
                score += 2
            if curr.task_id and s.task_id == curr.task_id:
                score += 1
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            print(f"  {_DM}No related sessions found (matching cwd, files, plan, or task).{_R}")
            return _CMD_CONTINUE
        print(f"\n  Related sessions (top {min(len(scored), 8)}):\n")
        for _score, s in scored[:8]:
            short_id = s.session_id[:8] + "…"
            title = (s.title[:40] + "…") if len(s.title) > 40 else s.title
            updated = s.updated_at[:10] if s.updated_at else "—"
            badges = m._session_badges(s)
            badge_str = f"  {_DM}{badges}{_R}" if badges else ""
            print(f"  {_CY}{short_id}{_R}  {title:<42} {_DM}{updated}{_R}{badge_str}")
        print("\n  Use /sessions open <id> to get resume instructions.\n")
        return _CMD_CONTINUE

    query = ""
    if token_lower.startswith("search "):
        query = token[7:].strip().lower()
    elif token and not token_lower.startswith("search") and not wants_overlay:
        query = token_lower

    sessions = list_sessions(limit=50)
    if query:
        sessions = [
            s for s in sessions
            if query in s.title.lower()
            or query in s.last_summary.lower()
            or query in s.session_id.lower()
            or query in " ".join(getattr(s, "tags", []))
        ]

    if not sessions:
        msg = f"No sessions matching '{query}'." if query else "No sessions found."
        hint = "" if query else f"  Start one with {_BCY}openclaw --session my-project{_R} or just type a question."
        print(f"  {_DM}{msg}{_R}")
        if hint:
            print(hint)
        return _CMD_CONTINUE

    if wants_overlay or (m._interactive_overlays_enabled() and not token):
        overlay_result = m._run_interactive_overlay(
            title="Session overlay",
            items=sessions,
            label_fn=lambda s: (
                f"{s.session_id[:8]}…  {s.title or '—'}  "
                f"{(s.updated_at or '—')[:19]}  {m._session_badges(s)}".strip()
            ),
            detail_fn=lambda s: m._session_preview_lines(s),
            on_select=lambda s: (
                m._print_session_summary(s),
                m._print_dashboard_surface(
                    "Focused Session Preview",
                    summary_lines=[
                        m._progress_cell("session", s.session_id[:8] + "…", status=s.status or "active"),
                        m._progress_cell("resume", "ready", status="info"),
                    ],
                    detail_lines=m._session_preview_lines(s),
                    action_lines=[
                        f"openclaw --session {s.session_id}",
                        f"openclaw session share {s.session_id}",
                    ],
                    border_style="cyan",
                ),
                m._print_meta_footer(("resume", f"openclaw --session {s.session_id}")),
            ),
            initial_query=overlay_query or query,
            empty_message="No sessions found.",
        )
        if overlay_result == "selected":
            m._set_command_result(ctx, ok=True, summary="selected session from overlay")
            return _CMD_CONTINUE
        if wants_overlay and overlay_result == "closed":
            m._set_command_result(ctx, ok=True, summary="session overlay closed")
            return _CMD_CONTINUE

    title_str = "Recent sessions" + (f" matching '{query}'" if query else "")
    fresh_count = sum(1 for s in sessions if not m._session_is_stale(s))
    active_count = sum(1 for s in sessions if m._status_family(s.status or "active") in {"active", "complete", "retry", "waiting"})
    operator_ready_count = 0
    for session in sessions:
        operator_snapshot = m._session_operator_snapshot(session)
        if str(operator_snapshot.get("readiness_label") or "").strip() == "handoff-ready":
            operator_ready_count += 1
    m._print_dashboard_surface(
        "Session Browser",
        summary_lines=[
            m._progress_cell("shown", str(len(sessions)), status="active"),
            m._progress_cell("fresh", str(fresh_count), status="info" if fresh_count else "idle"),
            m._progress_cell("active-ish", str(active_count), status="active" if active_count else "idle"),
            m._progress_cell("operator-ready", str(operator_ready_count), status="complete" if operator_ready_count else "idle"),
        ],
        detail_lines=[
            f"query: {query}" if query else "query: recent sessions",
            f"top session: {sessions[0].title or sessions[0].session_id}",
            *m._session_preview_lines(sessions[0]),
        ],
        action_lines=[
            "/sessions open <id> to get resume instructions",
            "/sessions overlay to inspect one session without leaving the browser",
            "/session after resuming to inspect the focused dashboard",
        ],
        border_style="dim",
    )
    if m._RICH_AVAILABLE and is_tty:
        tbl = m._RichTable(title=title_str, show_header=True, header_style="bold", box=None, pad_edge=False)
        tbl.add_column("ID", style="cyan", no_wrap=True, min_width=10)
        tbl.add_column("Title", no_wrap=False, min_width=20, max_width=38)
        tbl.add_column("Cmds", justify="right", style="dim", min_width=4)
        tbl.add_column("Updated", style="dim", no_wrap=True)
        tbl.add_column("Badges", style="dim", no_wrap=True)
        for s in sessions:
            short_id = s.session_id[:8] + "…"
            title = (s.title[:36] + "…") if len(s.title) > 36 else s.title
            updated = s.updated_at[:10] if s.updated_at else "—"
            badges = m._session_badges(s)
            tbl.add_row(short_id, title, str(s.command_count), updated, badges)
        m._RICH_CONSOLE.print()
        m._RICH_CONSOLE.print(tbl)
        m._RICH_CONSOLE.print("\n  [dim]Use /sessions open <id> to get resume instructions.[/]\n")
    else:
        print(f"\n  {title_str}:\n")
        print(f"  {'ID':<10}  {'Title':<36}  {'Cmds':>4}  {'Updated':<10}  Badges")
        print(f"  {'─'*10}  {'─'*36}  {'─'*4}  {'─'*10}  ──────")
        for s in sessions:
            short_id = (s.session_id[:8] + "…")[:10]
            title = (s.title[:34] + "…") if len(s.title) > 34 else s.title
            updated = s.updated_at[:10] if s.updated_at else "—"
            badges = m._session_badges(s) or "—"
            print(f"  {short_id:<10}  {title:<36}  {s.command_count:>4}  {updated:<10}  {badges}")
        print("\n  Use /sessions open <id> to get resume instructions.\n")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_export
# ---------------------------------------------------------------------------

def _cmd_export(ctx: ChatCommandContext) -> str:
    """/export [md|json|txt] [filename] — export session history to a file."""
    import datetime as _dt
    m = _get_cli_mod()
    args = (ctx.args or "").strip().split()
    fmt = args[0].lower() if args else "md"
    if fmt not in ("md", "json", "txt", "markdown", "text"):
        fmt = "md"
    if fmt == "markdown":
        fmt = "md"
    if fmt == "text":
        fmt = "txt"

    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    custom_name = args[1] if len(args) > 1 else None
    ext = {"md": "md", "json": "json", "txt": "txt"}[fmt]
    filename = custom_name if custom_name else f"openclaw_export_{ts}.{ext}"
    if not filename.endswith(f".{ext}"):
        filename = f"{filename}.{ext}"

    cmd_history = _PREFS.get("cmd_history", [])
    is_tty = _get_is_tty()

    if not cmd_history:
        msg = "No history to export yet."
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    try:
        from pathlib import Path
        now_str = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        exported_at_iso = _dt.datetime.now().isoformat()
        content = m._content_cmds_mod._build_export_body(cmd_history, fmt, now_str, exported_at_iso)

        output_path = Path(filename).expanduser()
        output_path.write_text(content, encoding="utf-8")

        abs_path = str(output_path.resolve())
        count = len(cmd_history)
        size_kb = len(content.encode()) / 1024

        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print(f"\n[bold green]✅ Exported[/] [dim]{count} entries → [/][bold cyan]{abs_path}[/] [dim]({size_kb:.1f} KB, {fmt.upper()})[/]\n")
        else:
            print(f"\n✅ Exported {count} entries → {abs_path} ({size_kb:.1f} KB, {fmt.upper()})\n")

    except Exception as e:  # noqa: BLE001
        msg = f"Export failed: {e}"
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print(f"[red]{msg}[/]")
        else:
            print(msg)

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_tag
# ---------------------------------------------------------------------------

def _cmd_tag(ctx: ChatCommandContext) -> str:
    """/tag [add <tag>|rm <tag>|list] — manage tags on the current session."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    token = ctx.args.strip()
    token_lower = token.lower()

    if not token or token_lower == "list":
        tags = list(getattr(session, "tags", []))
        if tags:
            tag_str = "  ".join(f"{_CY}#{t}{_R}" for t in tags)
            print(f"  Tags: {tag_str}")
        else:
            print(f"  {_DM}No tags. Use /tag add <name> to add one.{_R}")
        return _CMD_CONTINUE

    parts = token.split(maxsplit=1)
    subcmd = parts[0].lower()
    tag_name = parts[1].strip().lower() if len(parts) > 1 else ""

    if subcmd == "add":
        if not tag_name:
            print(f"{_BRE}error:{_R} Usage: /tag add <name>")
            return _CMD_CONTINUE
        tags = list(getattr(session, "tags", []))
        if tag_name not in tags:
            tags.append(tag_name)
            session.tags = tags
            save_session(session)
            print(f"  Added tag {_CY}#{tag_name}{_R}")
        else:
            print(f"  {_DM}Tag #{tag_name} already present.{_R}")
    elif subcmd == "rm":
        if not tag_name:
            print(f"{_BRE}error:{_R} Usage: /tag rm <name>")
            return _CMD_CONTINUE
        tags = list(getattr(session, "tags", []))
        if tag_name in tags:
            tags.remove(tag_name)
            session.tags = tags
            save_session(session)
            print(f"  Removed tag {_DM}#{tag_name}{_R}")
        else:
            print(f"  {_DM}Tag #{tag_name} not found.{_R}")
    else:
        print(f"{_BRE}error:{_R} Unknown subcommand '{subcmd}'. Use: add, rm, list")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_bookmark
# ---------------------------------------------------------------------------

def _cmd_bookmark(ctx: ChatCommandContext) -> str:
    """/bookmark [label] — save a replay bookmark for the current session."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    label = " ".join(ctx.args.strip().split())
    bookmark = create_session_bookmark(session.session_id, label=label, history=ctx.history)
    detail = f"turn {bookmark.get('turn_index', 0)}"
    m._print_feedback(
        f"Saved bookmark [{bookmark.get('id', '')}] {bookmark.get('label', '')}",
        level="success",
        detail=detail,
    )
    m._set_command_result(ctx, ok=True, summary=f"bookmark {bookmark.get('id', '')} saved")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_bookmarks
# ---------------------------------------------------------------------------

def _cmd_bookmarks(ctx: ChatCommandContext) -> str:
    """/bookmarks — list replay bookmarks for the current session."""
    m = _get_cli_mod()
    session = m._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    bookmarks = list_session_bookmarks(session.session_id)
    if not bookmarks:
        print(f"  {_DM}No bookmarks yet. Use /bookmark <label> after a meaningful turn.{_R}")
        m._set_command_result(ctx, ok=True, summary="no bookmarks")
        return _CMD_CONTINUE

    print(f"\n  {_B}Session bookmarks{_R}\n")
    for bookmark in bookmarks:
        summary = str(bookmark.get("summary") or "").strip()
        print(
            f"  [{bookmark.get('id', '')}] {bookmark.get('label', '')}  "
            f"{_DM}(turn {bookmark.get('turn_index', 0)}){_R}"
        )
        if summary:
            print(f"      {summary[:120]}")
    print()
    m._set_command_result(ctx, ok=True, summary=f"{len(bookmarks)} bookmarks")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_resume
# ---------------------------------------------------------------------------

def _cmd_resume(ctx: ChatCommandContext) -> str:
    """/resume [last] — print resume instructions for the most recent other session."""
    m = _get_cli_mod()
    token = ctx.args.strip().lower()
    sessions = list_sessions(limit=20)
    candidates = [s for s in sessions if s.session_id != ctx.session_id]
    if token and token != "last":
        candidates = [s for s in candidates if s.session_id.startswith(token) or token in s.title.lower()]
    if not candidates:
        print(f"  {_DM}No other sessions to resume.{_R}")
        return _CMD_CONTINUE
    target = candidates[0]
    short_id = target.session_id[:8]
    title = (target.title[:50] + "…") if len(target.title) > 50 else target.title
    updated = target.updated_at[:10] if target.updated_at else "—"
    print(f"\n  {m._e('📍', '@')} Most recent session:")
    print(f"    {_B}{title}{_R}  {_DM}({short_id}…  updated {updated}){_R}")
    print("\n  To resume, exit and run:")
    print(f"    {_BCY}openclaw session resume {target.session_id}{_R}\n")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_replay
# ---------------------------------------------------------------------------

def _cmd_replay(ctx: ChatCommandContext) -> str:
    """/replay [session-id] [--from bookmark] — re-print the current or a past session's conversation."""
    m = _get_cli_mod()
    is_tty = _get_is_tty()
    raw_args = ctx.args.strip()
    parts = shlex.split(raw_args) if raw_args else []
    token = ""
    bookmark_token = ""

    if parts:
        if parts[0] == "--from":
            if len(parts) != 2:
                print(f"{_BRE}error:{_R} Usage: /replay [session-id] [--from <bookmark>]")
                return _CMD_CONTINUE
            bookmark_token = parts[1]
        else:
            token = parts[0]
            if len(parts) > 1:
                if len(parts) == 3 and parts[1] == "--from":
                    bookmark_token = parts[2]
                else:
                    print(f"{_BRE}error:{_R} Usage: /replay [session-id] [--from <bookmark>]")
                    return _CMD_CONTINUE

    target_session_id = ""
    header = "Replay: current session"

    if token:
        all_sessions = list_sessions(limit=100)
        match = next(
            (s for s in all_sessions
             if s.session_id.startswith(token) or token.lower() in s.title.lower()),
            None,
        )
        if match is None:
            print(f"{_BRE}error:{_R} No session found matching '{token}'")
            return _CMD_CONTINUE
        target_session_id = match.session_id
        history = load_conversation_history(match.session_id, limit_turns=50)
        header = f"Replay: {match.title[:50]} ({match.session_id[:8]}…)"
    elif bookmark_token:
        session = m._require_session_or_warn(ctx)
        if session is None:
            return _CMD_CONTINUE
        target_session_id = session.session_id
        history = load_conversation_history(target_session_id, limit_turns=0)
    else:
        history = ctx.history

    if bookmark_token:
        bookmark_session_id = target_session_id or ctx.session_id
        if not bookmark_session_id:
            print(f"{_BRE}error:{_R} No session found for bookmark replay")
            return _CMD_CONTINUE
        bookmark = find_session_bookmark(bookmark_session_id, bookmark_token)
        if bookmark is None:
            print(f"{_BRE}error:{_R} No bookmark found matching '{bookmark_token}'")
            return _CMD_CONTINUE
        history = history[int(bookmark.get("history_index") or 0):]
        header = (
            f"Replay from [{bookmark.get('id', '')}] {bookmark.get('label', '')}"
            f" (turn {bookmark.get('turn_index', 0)})"
        )

    if not history:
        print(f"  {_DM}No conversation history to replay.{_R}")
        return _CMD_CONTINUE

    turns = [(t.get("role", ""), (t.get("content") or "").strip()) for t in history]

    if m._RICH_AVAILABLE and is_tty:
        m._RICH_CONSOLE.print(f"\n[bold]{header}[/]\n")
        for role, msg in turns:
            if role == "user":
                m._RICH_CONSOLE.print(f"[bold cyan]{m._e('👤', 'You')}[/]\n{msg}\n")
            else:
                m._print_response_separator()
                m._RICH_CONSOLE.print(msg + "\n")
    else:
        print(f"\n  {header}\n")
        for role, msg in turns:
            if role == "user":
                print(f"\n{_BCY}{m._e('👤', 'You')}{_R}\n{msg}\n")
            else:
                print()
                m._print_response_separator()
                print(f"{msg}\n")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_handoff
# ---------------------------------------------------------------------------

def _cmd_handoff(ctx: ChatCommandContext) -> str:
    """/handoff [create|list|open NAME|note TEXT|check] — save/restore a resumable workspace handoff."""
    m = _get_cli_mod()
    is_tty = _get_is_tty()
    raw = ctx.args.strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "check":
        session = m._require_session_or_warn(ctx)
        if session is None:
            return _CMD_CONTINUE
        check = m._handoff_check_snapshot(session.session_id)
        for line in _build_handoff_check_lines(check):
            print(line)
        return _CMD_CONTINUE

    # ── create ──────────────────────────────────────────────────────────────
    if sub == "create":
        session_id = m._require_session_or_warn(ctx)
        if session_id is None:
            return _CMD_CONTINUE
        if isinstance(session_id, object) and hasattr(session_id, "session_id"):
            session_id = session_id.session_id  # type: ignore[union-attr]
        note = ""
        if rest.lower().startswith("note "):
            note = rest[5:].strip().strip('"').strip("'")
        elif rest:
            note = rest.strip('"').strip("'")
        try:
            handoff_id = create_handoff(session_id, note=note)
        except ValueError as exc:
            m._print_error(str(exc))
            return _CMD_CONTINUE
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print(
                f"\n[bold green]{m._e('✅', '[OK]')} Handoff created:[/] [cyan]{handoff_id}[/]"
            )
            m._RICH_CONSOLE.print(
                f"  Resume with: [dim]openclaw --session {session_id}[/]  "
                f"or  [dim]/handoff open {handoff_id}[/]\n"
            )
        else:
            print(f"\n{_GR}{m._e('✅', '[OK]')} Handoff created:{_R} {handoff_id}")
            print(f"  Resume with: openclaw --session {session_id}")
            print(f"  Or use:      /handoff open {handoff_id}\n")
        return _CMD_CONTINUE

    # ── list ────────────────────────────────────────────────────────────────
    if sub == "list" or (not sub):
        handoffs = list_handoffs(limit=20)
        if not handoffs:
            print(f"  {_DM}No handoffs found. Create one with /handoff create{_R}")
            return _CMD_CONTINUE
        if m._RICH_AVAILABLE and is_tty:
            tbl = m._RichTable(
                "ID", "Session", "Title", "CWD", "Note", "Created",
                border_style="dim",
                header_style="bold cyan",
                show_lines=False,
            )
            for h in handoffs:
                hid = (h.get("id") or "")[:30]
                sess = (h.get("source_session_id") or "")[:8]
                title = (h.get("session_title") or "")[:24]
                cwd = (h.get("cwd") or "")[-30:]
                note_cell = (h.get("note") or "")[:30]
                created = (h.get("created_at") or "")[:19]
                tbl.add_row(hid, sess, title, cwd, note_cell, created)
            m._RICH_CONSOLE.print(tbl)
        else:
            cols = shutil.get_terminal_size((120, 24)).columns
            header = f"  {'ID':<30}  {'Session':<8}  {'Title':<24}  {'Note':<20}  {'Created'}"
            print(f"\n{_BCY}{header}{_R}")
            print(f"  {'─' * min(cols - 4, 100)}")
            for h in handoffs:
                hid = (h.get("id") or "")[:30]
                sess = (h.get("source_session_id") or "")[:8]
                title = (h.get("session_title") or "")[:24]
                note_cell = (h.get("note") or "")[:20]
                created = (h.get("created_at") or "")[:19]
                print(f"  {hid:<30}  {sess:<8}  {title:<24}  {note_cell:<20}  {created}")
            print()
        return _CMD_CONTINUE

    # ── open ────────────────────────────────────────────────────────────────
    if sub == "open":
        if not rest:
            m._print_error("Usage: /handoff open NAME")
            return _CMD_CONTINUE
        manifest = load_handoff(rest)
        if manifest is None:
            m._print_error(f"Handoff not found: {rest}")
            return _CMD_CONTINUE
        new_session = create_session()
        new_session_id = new_session.session_id if hasattr(new_session, "session_id") else str(new_session)
        result = apply_handoff(manifest, new_session_id)
        restored = result.get("restored", [])
        missing = result.get("missing", [])
        warnings = result.get("warnings", [])
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print(f"\n[bold green]{m._e('✅', '[OK]')} Handoff applied to new session:[/] [cyan]{new_session_id}[/]")
            if restored:
                m._RICH_CONSOLE.print(f"  [green]Restored:[/] {', '.join(str(r) for r in restored)}")
            if missing:
                m._RICH_CONSOLE.print(f"  [yellow]Missing:[/] {', '.join(str(m_) for m_ in missing)}")
            for w in warnings:
                m._RICH_CONSOLE.print(f"  [yellow]{m._e('⚠️', 'Warning:')}[/] {w}")
            m._RICH_CONSOLE.print(f"  Resume with: [dim]openclaw --session {new_session_id}[/]\n")
        else:
            print(f"\n{_GR}{m._e('✅', '[OK]')} Handoff applied to new session:{_R} {new_session_id}")
            if restored:
                print(f"  Restored: {', '.join(str(r) for r in restored)}")
            if missing:
                print(f"  {_YE}Missing:{_R} {', '.join(str(m_) for m_ in missing)}")
            for w in warnings:
                print(f"  {_YE}{m._e('⚠️', 'Warning:')} {w}{_R}")
            print(f"  Resume with: openclaw --session {new_session_id}\n")
        return _CMD_CONTINUE

    # ── note ────────────────────────────────────────────────────────────────
    if sub == "note":
        session_id = m._require_session_or_warn(ctx)
        if session_id is None:
            return _CMD_CONTINUE
        if isinstance(session_id, object) and hasattr(session_id, "session_id"):
            session_id = session_id.session_id  # type: ignore[union-attr]
        note_text = rest.strip('"').strip("'")
        if not note_text:
            m._print_error("Usage: /handoff note TEXT")
            return _CMD_CONTINUE
        try:
            handoff_id = create_handoff(session_id, note=note_text)
        except ValueError as exc:
            m._print_error(str(exc))
            return _CMD_CONTINUE
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print(
                f"\n[bold green]{m._e('✅', '[OK]')} Handoff with note saved:[/] [cyan]{handoff_id}[/]\n"
            )
        else:
            print(f"\n{_GR}{m._e('✅', '[OK]')} Handoff with note saved:{_R} {handoff_id}\n")
        return _CMD_CONTINUE

    # ── unknown / usage ─────────────────────────────────────────────────────
    print(f"  {_CY}Usage:{_R} /handoff [create|list|open NAME|note TEXT|check]")
    return _CMD_CONTINUE
