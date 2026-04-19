"""Content retrieval, search, and analytics command handlers.

Extracted from openclaw_cli.py.
Handlers: _cmd_collab, _cmd_search, _cmd_outputs, _cmd_stats (×2),
          _cmd_pattern, _cmd_history, _cmd_pin, _cmd_pins,
          _cmd_quality, _cmd_timeline.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import openclaw_cli_macros as _macros_mod
from openclaw_cli_content_cmds import (
    _build_ascii_bar_rows,
    _build_session_stats_agg,
    _compute_cmd_freq,
    _compute_rating_freq,
)
from openclaw_cli_prefs import _PREFS, _save_prefs
from openclaw_cli_session_cmds import _highlight_ansi, _highlight_rich
from openclaw_cli_session_display import _build_session_share_text
from openclaw_cli_sessions import (
    append_event,
    list_saved_outputs,
    list_sessions,
    load_events,
    load_saved_output_preview,
    update_session,
)
from openclaw_cli_types import ChatCommandContext
from openclaw_cli_ui_core import (
    _B,
    _BBL,
    _BCY,
    _BRE,
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
from openclaw_cli_ui_utils import _e

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
# Sentinels and constants (mirror of openclaw_cli.py values)
# ---------------------------------------------------------------------------
_CMD_CONTINUE: str = "continue"
_CMD_QUIT: str = "quit"

OUTPUT_LIST_LIMIT = 10
OUTPUT_PREVIEW_MAX_CHARS = 4_000
OUTPUT_OVERLAY_EXCERPT_CHARS = 140
OUTPUT_DASHBOARD_EXCERPT_CHARS = 220


# ---------------------------------------------------------------------------
# Lazy import — avoids circular dependency on openclaw_cli
# ---------------------------------------------------------------------------
def _get_cli_mod():  # type: ignore[return]
    import openclaw_cli as _m

    return _m


# ---------------------------------------------------------------------------
# Local helpers (moved from openclaw_cli.py; used only by handlers here)
# ---------------------------------------------------------------------------


def _prefs_set(key: str, value: object) -> None:
    _PREFS[key] = value
    _save_prefs()


def _relative_time(ts_str: str) -> str:
    """Convert ISO timestamp to relative time string."""
    try:
        import datetime as _dt

        ts = _dt.datetime.fromisoformat(ts_str)
        now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        diff = now - ts
        secs = int(diff.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        elif secs < 3600:
            return f"{secs // 60}m ago"
        elif secs < 86400:
            return f"{secs // 3600}h ago"
        else:
            return f"{secs // 86400}d ago"
    except (ValueError, TypeError, OSError):  # noqa: BLE001
        return ""


def _parse_collab_entry(raw: str) -> tuple[str, list[str], str]:
    actor = ""
    tags: list[str] = []
    remainder: list[str] = []
    for token in str(raw or "").split():
        if not remainder and token.startswith("@") and len(token) > 1 and not actor:
            actor = token[1:]
            continue
        if not remainder and token.startswith("#") and len(token) > 1:
            tag = re.sub(r"[^a-z0-9_-]+", "-", token[1:].strip().lower()).strip("-")
            if tag and tag not in tags:
                tags.append(tag[:40])
            continue
        remainder.append(token)
    return actor, tags, " ".join(remainder).strip()


def _pattern_store() -> dict[str, dict[str, Any]]:
    patterns = _PREFS.setdefault("patterns", {})
    if not isinstance(patterns, dict):
        patterns = {}
        _PREFS["patterns"] = patterns
    return patterns


def _pattern_steps(entry: dict[str, Any]) -> list[str]:
    steps = entry.get("commands") or []
    return [str(step) for step in steps if str(step or "").strip()]


def _workflow_store() -> dict[str, list[str]]:
    return _macros_mod._workflow_store(_PREFS)


def _history_command_texts(limit: int) -> list[str]:
    return _macros_mod._history_command_texts(_PREFS, limit)


def _print_workflow_preview(name: str, steps: list[str], ctx: ChatCommandContext) -> None:
    _macros_mod._print_workflow_preview(name, steps, ctx)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _cmd_collab(ctx: ChatCommandContext) -> str:
    """/collab [status|share|note|decision|assign] — collaboration notes, decisions, assignments, and handoff summaries."""
    _cli = _get_cli_mod()
    session = _cli._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    raw = ctx.args.strip()
    if not raw or raw.lower() in {"status", "summary", "share"}:
        print(_build_session_share_text(session.session_id))
        return _CMD_CONTINUE

    parts = raw.split(None, 1)
    sub = parts[0].lower()
    remainder = parts[1].strip() if len(parts) > 1 else ""

    if sub not in {"note", "decision", "assign"}:
        _cli._print_error(
            "Usage: /collab [status|share|note [@actor] TEXT|decision [@actor] [#tag] TEXT|assign @actor TEXT]"
        )
        return _CMD_CONTINUE

    actor, tags, text = _parse_collab_entry(remainder)
    if not text:
        if sub == "assign":
            _cli._print_error("Usage: /collab assign @actor TEXT")
        else:
            _cli._print_error(f"Usage: /collab {sub} [@actor] {'[#tag] ' if sub == 'decision' else ''}TEXT")
        return _CMD_CONTINUE
    actor_label = actor or "operator"
    summary_text = " ".join(text.split())
    if len(summary_text) > 90:
        summary_text = summary_text[:89].rstrip() + "…"
    summary = f"{sub} by {actor_label}: {summary_text}"
    metadata: dict[str, Any] = {
        "summary": summary,
        "actor": actor_label,
        "tags": tags,
        "collab_kind": sub,
    }
    if sub == "assign":
        metadata["assignee"] = actor_label
        metadata["assignment_status"] = "active"
        metadata["collab_kind"] = "assignment"
        metadata["summary"] = f"assignment for {actor_label}: {summary_text}"
    append_event(
        session.session_id,
        kind="collab",
        content=text,
        metadata=metadata,
    )
    if tags:
        existing_tags = list(session.tags or [])
        for tag in tags:
            session_tag = f"collab:{tag}"
            if session_tag not in existing_tags:
                existing_tags.append(session_tag)
        update_session(session.session_id, tags=existing_tags)
    print(f"Recorded {sub} by {actor_label}.")
    print("Local session log only; workspace unchanged.")
    if tags:
        print(f"Tags: {', '.join('#' + tag for tag in tags)}")
    print(text)
    return _CMD_CONTINUE


def _cmd_search(ctx: ChatCommandContext) -> str:
    """/search [--all] <query> — full-text search across session event content."""
    is_tty = _get_is_tty()
    raw = ctx.args.strip()

    cross_session = False
    if raw.startswith("--all"):
        cross_session = True
        raw = raw[5:].strip()

    query = raw
    if not query:
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print("[dim]Usage: /search <query>  or  /search --all <query>[/]")
        else:
            print("Usage: /search <query>  or  /search --all <query>")
        return _CMD_CONTINUE

    ql = query.lower()
    MAX_RESULTS = 15 if cross_session else 10
    EXCERPT_LEN = 120

    def _highlight_ansi_local(text: str) -> str:
        return _highlight_ansi(text, query, ql, _BYE, _R)

    def _highlight_rich_local(text: str) -> str:
        return _highlight_rich(text, query)

    results: list[tuple[str, str, str, str]] = []  # (session_short, kind, excerpt, ts)

    if cross_session:
        all_sessions = list_sessions(limit=200)
        for sess in all_sessions:
            if len(results) >= MAX_RESULTS:
                break
            try:
                events = load_events(sess.session_id, limit=200)
            except (OSError, ValueError, AttributeError):
                import logging as _logging

                _logging.getLogger("openclaw_cli").debug("load_events failed for %s", sess.session_id, exc_info=True)
                continue
            for ev in events:
                if len(results) >= MAX_RESULTS:
                    break
                content = str(ev.get("content") or "").strip()
                if ql in content.lower():
                    kind = str(ev.get("kind") or "event").strip()
                    ts = str(ev.get("timestamp") or ev.get("at") or ev.get("created_at") or "").strip()
                    excerpt = content[:EXCERPT_LEN]
                    short_id = sess.session_id[:8] if sess.session_id else "????????"
                    results.append((short_id, kind, excerpt, ts))
    else:
        session = _get_cli_mod()._require_session_or_warn(ctx)
        if session is None:
            return _CMD_CONTINUE
        events = load_events(ctx.session_id, limit=500)
        for ev in events:
            if len(results) >= MAX_RESULTS:
                break
            content = str(ev.get("content") or "").strip()
            if ql in content.lower():
                kind = str(ev.get("kind") or "event").strip()
                ts = str(ev.get("timestamp") or ev.get("at") or ev.get("created_at") or "").strip()
                excerpt = content[:EXCERPT_LEN]
                results.append(("", kind, excerpt, ts))

    if not results:
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]No matches for '{query}'[/]")
        else:
            print(f"No matches for '{query}'")
        return _CMD_CONTINUE

    if _RICH_AVAILABLE and is_tty:
        grid = _RichTable.grid(padding=(0, 1))
        if cross_session:
            grid.add_column(style="dim", no_wrap=True)
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column()
        grid.add_column(style="dim", no_wrap=True)
        for short_id, kind, excerpt, ts in results:
            highlighted = _highlight_rich_local(excerpt)
            if cross_session:
                grid.add_row(short_id, kind, highlighted, ts)
            else:
                grid.add_row(kind, highlighted, ts)
        scope = "all sessions" if cross_session else "this session"
        _RICH_CONSOLE.print(
            _RichPanel(grid, title=f"[bold]🔍 search results[/] [dim]{scope}[/]", border_style="cyan", padding=(0, 1))
        )
    else:
        scope = "all sessions" if cross_session else "this session"
        print(f"[search results — {scope}]")
        for short_id, kind, excerpt, ts in results:
            highlighted = _highlight_ansi_local(excerpt)
            prefix = f"{short_id} " if cross_session and short_id else ""
            print(f"  {prefix}{_DM}{kind}{_R}  {highlighted}  {_DM}{ts}{_R}")

    return _CMD_CONTINUE


def _cmd_outputs(ctx: ChatCommandContext) -> str:
    """/outputs [<index>|<filename>|promote <index> <name>] — list or preview saved outputs."""
    _cli = _get_cli_mod()
    session = _cli._require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    outputs = list_saved_outputs(session.session_id, limit=OUTPUT_LIST_LIMIT)
    if not outputs:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(
                "[dim]No saved outputs yet.[/]  [dim]Use /write, /research, or /analyze to generate output.[/]"
            )
        else:
            print("No saved outputs yet. Use /write, /research, or /analyze to generate output.")
        return _CMD_CONTINUE

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

    # /outputs promote <index> <name>
    if token_lower.startswith("promote "):
        from pathlib import Path

        promote_args = token[8:].strip().split(maxsplit=1)
        if len(promote_args) < 2:
            print(f"{_BRE}error:{_R} Usage: /outputs promote <index> <stable-name>")
            return _CMD_CONTINUE
        idx_str, new_name = promote_args[0], promote_args[1].strip()
        all_outputs = list_saved_outputs(session.session_id, limit=0)
        if not idx_str.isdigit() or not (1 <= int(idx_str) <= len(all_outputs)):
            print(f"{_BRE}error:{_R} Index {idx_str!r} out of range (1–{len(all_outputs)})")
            return _CMD_CONTINUE
        src = Path(str(all_outputs[int(idx_str) - 1].get("path") or ""))
        if not src.exists():
            print(f"{_BRE}error:{_R} Source file not found: {src}")
            return _CMD_CONTINUE
        dst = src.parent / new_name
        try:
            import shutil as _shutil

            _shutil.copy2(src, dst)
        except OSError as exc:
            print(f"{_BRE}error:{_R} Could not promote: {exc}")
            return _CMD_CONTINUE
        print(f"  {_e('📄', '[promoted]')} {src.name} → {_BCY}{dst}{_R}")
        return _CMD_CONTINUE
    if wants_overlay or (_cli._interactive_overlays_enabled() and not token):
        output_previews: dict[str, dict[str, Any] | None] = {}
        for item in outputs[: min(len(outputs), OUTPUT_LIST_LIMIT)]:
            name = str(item.get("name") or "").strip()
            if name:
                output_previews[name] = load_saved_output_preview(
                    session.session_id,
                    name,
                    max_chars=OUTPUT_OVERLAY_EXCERPT_CHARS,
                )

        def _preview_output(item: dict[str, Any]) -> None:
            preview = load_saved_output_preview(
                session.session_id,
                str(item.get("name") or "").strip(),
                max_chars=OUTPUT_PREVIEW_MAX_CHARS,
            )
            if preview is None:
                print(f"Saved output not found: {str(item.get('name') or '').strip()}")
                return
            name = str(preview.get("name") or "").strip()
            size = _cli._format_byte_count(int(preview.get("size_bytes") or 0))
            modified_at = str(preview.get("modified_at") or "").strip()
            preview_label = f"saved output preview: {name} ({size}"
            if modified_at:
                preview_label += f"; {modified_at}"
            if preview.get("truncated"):
                preview_label += f"; preview limited to {OUTPUT_PREVIEW_MAX_CHARS} chars"
            preview_label += ")"
            print(preview_label)
            print(str(preview.get("preview") or ""))

        overlay_result = _cli._run_interactive_overlay(
            title="Saved outputs overlay",
            items=outputs,
            label_fn=lambda item: (
                f"{str(item.get('name') or '').strip()}  "
                f"{_cli._format_byte_count(int(item.get('size_bytes') or 0))}  "
                f"{str(item.get('modified_at') or '').strip()}  "
                f"{_cli._single_line_excerpt(str((output_previews.get(str(item.get('name') or '').strip()) or {}).get('preview') or ''), max_chars=70)}".strip()
            ),
            detail_fn=lambda item: [
                f"name: {str(item.get('name') or '').strip()}",
                f"size: {_cli._format_byte_count(int(item.get('size_bytes') or 0))}",
                f"modified: {str(item.get('modified_at') or '').strip() or '—'}",
                *[
                    line
                    for line in str(
                        (output_previews.get(str(item.get("name") or "").strip()) or {}).get("preview") or ""
                    ).splitlines()
                    if line.strip()
                ],
            ],
            on_select=_preview_output,
            initial_query=overlay_query,
            empty_message="No saved outputs yet.",
        )
        if overlay_result == "selected":
            _cli._set_command_result(ctx, ok=True, summary="selected saved output from overlay")
            return _CMD_CONTINUE
        if wants_overlay and overlay_result == "closed":
            _cli._set_command_result(ctx, ok=True, summary="outputs overlay closed")
            return _CMD_CONTINUE
        if wants_overlay and overlay_result in {"fallback", "empty"}:
            token = ""
    if not token:
        newest = outputs[0]
        newest_preview = load_saved_output_preview(
            session.session_id,
            str(newest.get("name") or "").strip(),
            max_chars=OUTPUT_DASHBOARD_EXCERPT_CHARS,
        )
        summary_lines = [
            _cli._progress_cell("shown", str(len(outputs)), status="active"),
            _cli._progress_cell("recent", str(newest.get("name") or "—"), status="complete"),
            _cli._progress_cell("freshness", "freshest first", status="info"),
        ]
        detail_lines = []
        if newest_preview:
            detail_lines.append(
                f"focused preview: {str(newest_preview.get('name') or '').strip()} · "
                f"{_cli._format_byte_count(int(newest_preview.get('size_bytes') or 0))}"
            )
            detail_lines.extend(
                _cli._preview_block_lines(
                    "excerpt",
                    str(newest_preview.get("preview") or ""),
                    max_chars=OUTPUT_DASHBOARD_EXCERPT_CHARS,
                )
            )
        for index, item in enumerate(outputs[:3], start=1):
            name = str(item.get("name") or "").strip()
            size = _cli._format_byte_count(int(item.get("size_bytes") or 0))
            modified_at = str(item.get("modified_at") or "").strip()
            suffix = f" · {modified_at}" if modified_at else ""
            detail_lines.append(f"{index}. {name} ({size}{suffix})")
        action_lines = [
            "/outputs 1 to preview the newest artifact",
            "/outputs promote <index> <name> to pin a stable filename",
        ]
        if len(outputs) > 1:
            action_lines.append("/outputs overlay or /outputs pick <query> to jump by name")
        if session.files or session.plan_id or session.task_id:
            action_lines.append("/context to compare saved artifacts against current grounding")
        _cli._print_dashboard_surface(
            "Outputs Dashboard",
            summary_lines=summary_lines,
            detail_lines=detail_lines,
            action_lines=_cli._dedupe_preserve_order(action_lines),
            border_style="dim",
        )
        if _RICH_AVAILABLE and _IS_TTY:
            table = _RichTable(
                border_style="dim",
                show_edge=True,
                pad_edge=True,
                header_style="bold cyan",
                caption=f"[dim]{len(outputs)} output(s)[/]",
            )
            table.add_column("#", style="dim", justify="right", no_wrap=True)
            table.add_column("Filename", style="bold")
            table.add_column("Size", style="cyan", justify="right", no_wrap=True)
            table.add_column("Modified", style="dim", no_wrap=True)
            for index, item in enumerate(outputs, start=1):
                name = str(item.get("name") or "").strip()
                size = _cli._format_byte_count(int(item.get("size_bytes") or 0))
                modified_at = str(item.get("modified_at") or "").strip()
                table.add_row(str(index), name, size, modified_at)
            _RICH_CONSOLE.print(table)
        else:
            print(f"saved outputs ({len(outputs)} shown):")
            for index, item in enumerate(outputs, start=1):
                name = str(item.get("name") or "").strip()
                size = _cli._format_byte_count(int(item.get("size_bytes") or 0))
                modified_at = str(item.get("modified_at") or "").strip()
                suffix = f"; {modified_at}" if modified_at else ""
                print(f"  {index}. {name} ({size}{suffix})")
        return _CMD_CONTINUE

    preview = load_saved_output_preview(session.session_id, token, max_chars=OUTPUT_PREVIEW_MAX_CHARS)
    if preview is None:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[yellow]not found:[/] {token}")
        else:
            print(f"Saved output not found: {token}")
        return _CMD_CONTINUE
    name = str(preview.get("name") or "").strip()
    size = _cli._format_byte_count(int(preview.get("size_bytes") or 0))
    modified_at = str(preview.get("modified_at") or "").strip()
    trunc_note = f"  [dim]preview limited to {OUTPUT_PREVIEW_MAX_CHARS} chars[/]" if preview.get("truncated") else ""
    if _RICH_AVAILABLE and _IS_TTY:
        subtitle = f"[dim]{size}"
        if modified_at:
            subtitle += f"  ·  {modified_at}"
        subtitle += f"[/]{trunc_note}"
        _RICH_CONSOLE.print(
            _RichPanel(
                str(preview.get("preview") or ""),
                title=f"[bold]{name}[/]  {subtitle}",
                border_style="dim",
                padding=(0, 1),
            )
        )
    else:
        preview_label = f"saved output preview: {name} ({size}"
        if modified_at:
            preview_label += f"; {modified_at}"
        if preview.get("truncated"):
            preview_label += f"; preview limited to {OUTPUT_PREVIEW_MAX_CHARS} chars"
        preview_label += ")"
        print(preview_label)
        print(str(preview.get("preview") or ""))
    _cli._print_predictive_affordances(
        _cli._dedupe_preserve_order(
            [
                "/outputs overlay to jump to another saved artifact" if len(outputs) > 1 else "",
                "/outputs promote <index> <name> to keep a stable copy",
                "/context to compare this artifact with current grounding"
                if session.files or session.plan_id or session.task_id
                else "",
            ]
        ),
        title="Artifact shortcuts",
        border_style="dim",
    )
    return _CMD_CONTINUE


def _cmd_stats(ctx: ChatCommandContext) -> str:
    """/stats — show aggregate usage statistics across all sessions."""
    is_tty = _get_is_tty()
    sessions = list_sessions(limit=500)

    if not sessions:
        print(f"  {_DM}No sessions found.{_R}")
        return _CMD_CONTINUE

    _agg = _build_session_stats_agg(sessions)
    total_sessions = _agg["total_sessions"]
    total_commands = _agg["total_commands"]
    total_edits = _agg["total_edits"]
    total_checkpoints = _agg["total_checkpoints"]
    active = _agg["active"]
    newest = _agg["newest"]
    oldest = _agg["oldest"]
    top_cwds = _agg["top_cwds"]

    if _RICH_AVAILABLE and is_tty:
        grid = _RichText()
        grid.append("  sessions    ", style="dim")
        grid.append(f"{total_sessions}", style="bold")
        grid.append(f"  ({active} active)\n", style="dim")
        grid.append("  commands    ", style="dim")
        grid.append(f"{total_commands}\n", style="bold")
        grid.append("  file edits  ", style="dim")
        grid.append(f"{total_edits}\n", style="bold")
        grid.append("  checkpoints ", style="dim")
        grid.append(f"{total_checkpoints}\n", style="bold")
        grid.append("  date range  ", style="dim")
        grid.append(f"{oldest}", style="bold")
        grid.append(" → ", style="dim")
        grid.append(f"{newest}\n", style="bold")
        if top_cwds:
            grid.append("\n  top dirs\n", style="dim")
            for cwd, count in top_cwds:
                short = cwd[-45:] if len(cwd) > 45 else cwd
                if len(cwd) > 45:
                    short = "…" + short
                grid.append(f"    {count:>3}×  ", style="dim")
                grid.append(f"{short}\n", style="cyan")
        _RICH_CONSOLE.print(
            _RichPanel(grid, title=f"[bold]{_e('📊', '[stats]')} OpenClaw Stats[/]", border_style="dim", padding=(0, 1))
        )
    else:
        print(f"\n  {_e('📊', '[stats]')} OpenClaw Stats\n")
        print(f"  sessions    : {total_sessions}  ({active} active)")
        print(f"  commands    : {total_commands}")
        print(f"  file edits  : {total_edits}")
        print(f"  checkpoints : {total_checkpoints}")
        print(f"  date range  : {oldest} → {newest}")
        if top_cwds:
            print("\n  top dirs:")
            for cwd, count in top_cwds:
                short = ("…" + cwd[-45:]) if len(cwd) > 45 else cwd
                print(f"    {count:>3}×  {short}")
        print()
    return _CMD_CONTINUE


def _cmd_pattern(ctx: "ChatCommandContext") -> str:
    """/pattern — manage reusable workflow patterns backed by history or workflows."""
    import re as _re

    args = (ctx.args or "").strip()
    patterns = _pattern_store()
    workflows = _workflow_store()
    is_tty = _get_is_tty()
    parts = args.split(None, 1)
    token = parts[0].lower() if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""

    if token in {"list", "ls"} or not args:
        if _RICH_AVAILABLE and is_tty:
            tbl = _RichTable("Pattern", "Source", "Steps", "Updated", border_style="dim", header_style="bold cyan")
            for name, entry in sorted(patterns.items()):
                source = str(entry.get("source") or "history")
                updated = str(entry.get("updated_at") or entry.get("created_at") or "")[:19]
                tbl.add_row(name, source, str(len(_pattern_steps(entry))), updated or "—")
            if not patterns:
                tbl.add_row("(no patterns saved)", "", "", "")
            _RICH_CONSOLE.print(tbl)
        else:
            print("Patterns:")
            if patterns:
                for name, entry in sorted(patterns.items()):
                    source = str(entry.get("source") or "history")
                    print(f"  {name}  ({len(_pattern_steps(entry))} steps · {source})")
            else:
                print("  (no patterns saved)")
        return _CMD_CONTINUE

    if token == "save":
        save_parts = rest.split()
        if not save_parts:
            _get_cli_mod()._print_error("Usage: /pattern save <name> [last N|workflow NAME]")
            return _CMD_CONTINUE
        pattern_name = save_parts[0]
        if not _re.match(r"^[A-Za-z0-9_-]{1,40}$", pattern_name):
            _get_cli_mod()._print_error("Pattern name must be 1-40 alphanumeric characters, hyphens, or underscores.")
            return _CMD_CONTINUE
        source = "history"
        source_name = ""
        commands: list[str] = []
        if len(save_parts) == 1:
            commands = _get_cli_mod()._history_command_texts(5)[-5:]
        elif len(save_parts) >= 3 and save_parts[1].lower() == "last":
            try:
                n = max(1, min(int(save_parts[2]), 20))
            except ValueError:
                _get_cli_mod()._print_error("Usage: /pattern save <name> [last N|workflow NAME]")
                return _CMD_CONTINUE
            commands = _get_cli_mod()._history_command_texts(n)[-n:]
        elif len(save_parts) >= 3 and save_parts[1].lower() == "workflow":
            source = "workflow"
            source_name = save_parts[2]
            if source_name not in workflows:
                _get_cli_mod()._print_error(f"Workflow '{source_name}' not found")
                return _CMD_CONTINUE
            commands = list(workflows[source_name])
        else:
            _get_cli_mod()._print_error("Usage: /pattern save <name> [last N|workflow NAME]")
            return _CMD_CONTINUE
        if not commands:
            _get_cli_mod()._print_error("No reusable commands found for this pattern")
            return _CMD_CONTINUE
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        patterns[pattern_name] = {
            "commands": commands[:20],
            "source": source,
            "source_name": source_name,
            "created_at": patterns.get(pattern_name, {}).get("created_at", timestamp),
            "updated_at": timestamp,
            "session_id": ctx.session_id,
        }
        _save_prefs()
        print(
            f"  {_GR}{_e('✅', '[OK]')} Pattern '{pattern_name}' saved ({len(commands[:20])} step{'s' if len(commands[:20]) != 1 else ''}).{_R}"
        )
        return _CMD_CONTINUE

    if token in {"show", "preview"}:
        if not rest:
            _get_cli_mod()._print_error(f"Usage: /pattern {token} <name>")
            return _CMD_CONTINUE
        name = rest.split()[0]
        entry = patterns.get(name)
        if not isinstance(entry, dict):
            _get_cli_mod()._print_error(f"Pattern '{name}' not found")
            return _CMD_CONTINUE
        steps = _pattern_steps(entry)
        source = str(entry.get("source") or "history")
        source_name = str(entry.get("source_name") or "").strip()
        if source_name:
            print(f"Pattern '{name}' · source {source}:{source_name}")
        else:
            print(f"Pattern '{name}' · source {source}")
        _print_workflow_preview(name, steps, ctx)
        return _CMD_CONTINUE

    if token == "run":
        if not rest:
            _get_cli_mod()._print_error("Usage: /pattern run <name>")
            return _CMD_CONTINUE
        name = rest.split()[0]
        entry = patterns.get(name)
        if not isinstance(entry, dict):
            _get_cli_mod()._print_error(f"Pattern '{name}' not found")
            return _CMD_CONTINUE
        return _get_cli_mod()._run_command_sequence(ctx, name, _pattern_steps(entry), kind="pattern")

    if token in {"rm", "remove"}:
        if not rest:
            _get_cli_mod()._print_error("Usage: /pattern rm <name>")
            return _CMD_CONTINUE
        name = rest.split()[0]
        if name not in patterns:
            _get_cli_mod()._print_error(f"Pattern '{name}' not found")
            return _CMD_CONTINUE
        del patterns[name]
        _save_prefs()
        print(f"  {_GR}{_e('✅', '[OK]')} Pattern '{name}' removed.{_R}")
        return _CMD_CONTINUE

    _get_cli_mod()._print_error("Unknown /pattern sub-command. Use: list, save, show, preview, run, rm")
    return _CMD_CONTINUE


def _cmd_history(ctx: "ChatCommandContext") -> str:
    """Show or clear recent command history with color-coding and pagination."""
    args = (ctx.args or "").strip()
    is_tty = _get_is_tty()
    hist: "list" = _PREFS.get("cmd_history", [])

    if args.lower() == "clear":
        _prefs_set("cmd_history", [])
        print(f"  {_GR}{_e('✅', '[OK]')} Command history cleared.{_R}")
        return _CMD_CONTINUE

    PAGE_SIZE = 15
    page = 1
    if args:
        try:
            page = max(1, int(args))
        except ValueError:
            _get_cli_mod()._print_error("Usage: /history [page] | /history clear")
            return _CMD_CONTINUE

    total = len(hist)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    entries = hist[start:end] if hist else []

    def _entry_text(e: object) -> str:
        if isinstance(e, dict):
            return e.get("text", e.get("prompt", e.get("cmd", "")))
        return str(e)

    def _entry_ts(e: object) -> str:
        if isinstance(e, dict):
            return e.get("ts", e.get("timestamp", ""))
        return ""

    if _RICH_AVAILABLE and is_tty:
        from rich.console import Group as _RichGroup
        from rich.text import Text as _RichText

        content_lines: list[_RichText] = []
        if not entries:
            content_lines.append(_RichText("(no history yet)", style="dim"))
        else:
            global_idx = start + 1
            for e in entries:
                text = _entry_text(e)
                ts_str = _entry_ts(e)
                rel = _relative_time(ts_str) if ts_str else ""
                line = _RichText()
                line.append(f"  {global_idx:>3}  ", style="dim")
                if text.startswith("/"):
                    line.append(text, style="bold cyan")
                else:
                    line.append(text, style="default")
                if rel:
                    line.append(f"  {rel}", style="dim")
                content_lines.append(line)
                global_idx += 1
        page_info = f"page {page}/{total_pages}" if total_pages > 1 else ""
        title_parts = [f"{_e('📜', '')} Command History"]
        if page_info:
            title_parts.append(f"[dim]({page_info})[/dim]")
        _RICH_CONSOLE.print(
            _RichPanel(
                _RichGroup(*content_lines),
                title=" ".join(title_parts),
                border_style="cyan",
                padding=(0, 1),
            )
        )
        if total_pages > 1:
            _RICH_CONSOLE.print(f"[dim]  /history {page + 1} for next page[/dim]" if page < total_pages else "")
    else:
        print(f"{_BBL}Command History:{_R}")
        if not entries:
            print(f"  {_DM}(no history yet){_R}")
        else:
            global_idx = start + 1
            for e in entries:
                text = _entry_text(e)
                ts_str = _entry_ts(e)
                rel = _relative_time(ts_str) if ts_str else ""
                color = _CY if text.startswith("/") else ""
                ts_suffix = f"  {_DM}{rel}{_R}" if rel else ""
                print(f"  {_DM}{global_idx:>3}{_R}  {color}{text}{_R}{ts_suffix}")
                global_idx += 1
        if total_pages > 1:
            print(f"  {_DM}Page {page}/{total_pages} — /history <page> for more{_R}")

    return _CMD_CONTINUE


def _cmd_pin(ctx: "ChatCommandContext") -> str:
    """Pin the last AI response for quick recall. Sub-commands: [name] | recall <name> | rm <name> | list."""
    import datetime as _dt

    args = (ctx.args or "").strip()
    parts = args.split(None, 1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    pins: list[dict] = _PREFS.setdefault("pins", [])
    is_tty = _get_is_tty()

    # ── list ──────────────────────────────────────────────────────────────────
    if sub in ("list", "ls") or (not sub):
        if sub in ("list", "ls") or args == "":
            if sub in ("list", "ls"):
                if not pins:
                    if _RICH_AVAILABLE and is_tty:
                        _RICH_CONSOLE.print(
                            _RichPanel("[dim](no pins)[/dim]", title="📌 Pins", border_style="cyan", padding=(0, 1))
                        )
                    else:
                        print(f"  {_B}📌 Pins{_R}")
                        print(f"  {_DM}(no pins){_R}")
                    return _CMD_CONTINUE
                if _RICH_AVAILABLE and is_tty:
                    t = _RichTable.grid(padding=(0, 2))
                    t.add_column(style="bold cyan", no_wrap=True)
                    t.add_column(style="dim")
                    for p in pins:
                        preview = p["text"][:80] + ("…" if len(p["text"]) > 80 else "")
                        t.add_row(p["name"], preview)
                    _RICH_CONSOLE.print(_RichPanel(t, title="📌 Pins", border_style="cyan", padding=(0, 1)))
                else:
                    print(f"  {_B}📌 Pins{_R}")
                    for p in pins:
                        preview = p["text"][:80] + ("…" if len(p["text"]) > 80 else "")
                        print(f"  {_CY}{p['name']}{_R}: {_DM}{preview}{_R}")
                return _CMD_CONTINUE

    # ── recall ────────────────────────────────────────────────────────────────
    if sub == "recall":
        if not rest:
            _get_cli_mod()._print_error("Usage: /pin recall <name>")
            return _CMD_CONTINUE
        name_lc = rest.lower()
        match = next((p for p in pins if p["name"].lower() == name_lc), None)
        if match is None:
            _get_cli_mod()._print_error(f"No pin named '{rest}'")
            return _CMD_CONTINUE
        from dataclasses import dataclass as _dc
        from dataclasses import field as _field

        @_dc
        class _PinResponse:
            response: str
            raw: dict = _field(default_factory=dict)
            metadata: dict = _field(default_factory=dict)
            error: str = ""

        _get_cli_mod().print_response(_PinResponse(response=match["text"]), output_json=False)
        return _CMD_CONTINUE

    # ── rm ────────────────────────────────────────────────────────────────────
    if sub == "rm":
        if not rest:
            _get_cli_mod()._print_error("Usage: /pin rm <name>")
            return _CMD_CONTINUE
        name_lc = rest.lower()
        before = len(pins)
        pins[:] = [p for p in pins if p["name"].lower() != name_lc]
        if len(pins) == before:
            _get_cli_mod()._print_error(f"No pin named '{rest}'")
            return _CMD_CONTINUE
        _prefs_set("pins", pins)
        print(f"  {_GR}{_e('✅', '[OK]')} Pin '{rest}' removed.{_R}")
        return _CMD_CONTINUE

    # ── pin (save) — bare /pin or /pin <name> ────────────────────────────────
    _last_response_text = _get_cli_mod()._last_response_text
    if not _last_response_text:
        _get_cli_mod()._print_error("Nothing to pin — no response yet")
        return _CMD_CONTINUE

    if len(pins) >= 20:
        _get_cli_mod()._print_error("Pin limit reached (20). Use /pin rm <name> to free a slot.")
        return _CMD_CONTINUE

    if not args:
        existing_nums = []
        for p in pins:
            if p["name"].startswith("pin-") and p["name"][4:].isdigit():
                existing_nums.append(int(p["name"][4:]))
        next_n = (max(existing_nums) + 1) if existing_nums else 1
        name = f"pin-{next_n}"
    else:
        name = args

    ts = _dt.datetime.now().isoformat(timespec="seconds")
    name_lc = name.lower()
    existing = next((i for i, p in enumerate(pins) if p["name"].lower() == name_lc), None)
    if existing is not None:
        pins[existing] = {"name": name, "text": _last_response_text, "ts": ts}
        action = "updated"
    else:
        pins.append({"name": name, "text": _last_response_text, "ts": ts})
        action = "pinned"

    _prefs_set("pins", pins)
    preview = _last_response_text[:60] + ("…" if len(_last_response_text) > 60 else "")
    print(f"  {_GR}{_e('✅', '[OK]')} {action.capitalize()} as '{_CY}{name}{_R}{_GR}': {_DM}{preview}{_R}")
    return _CMD_CONTINUE


def _cmd_pins(ctx: "ChatCommandContext") -> str:
    """List all pinned responses (alias for /pin list)."""
    ctx.args = "list"
    return _cmd_pin(ctx)


def _cmd_quality(ctx: "ChatCommandContext") -> str:
    """/quality — show a colored histogram of response quality ratings."""
    _cli = _get_cli_mod()
    is_tty = _get_is_tty()
    arg = (ctx.args or "").strip().lower()
    if arg == "predict":
        rows = _cli._route_quality_summary()
        if not rows:
            print("No route-quality history yet. Use /rate after routed responses to build predictions.")
            return _CMD_CONTINUE
        best = rows[0]
        print("Quality prediction")
        print("------------------")
        print(f"  Highest-confidence lane: /{best['route']}")
        print(f"  Predicted quality:       {best['avg']:.1f}/5 based on {best['count']} prior rating(s)")
        print(f"  High-rating share:       {best['high_rate']}%")
        if len(rows) > 1:
            next_best = rows[1]
            print(f"  Next best:               /{next_best['route']} ({next_best['avg']:.1f}/5)")
        print("  Use /routing analyze for the full learned summary.")
        return _CMD_CONTINUE
    ratings = _PREFS.get("ratings", [])
    snapshot = _cli._last_trace_snapshot(ctx.session_id) if getattr(ctx, "session_id", "") else None

    if not ratings:
        msg = "No ratings yet. Use /rate 1-5 after responses to track quality."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    counts: dict[int, int] = {i: 0 for i in range(1, 6)}
    for r in ratings:
        if isinstance(r, dict):
            score = r.get("score", r.get("rating", 0))
        else:
            try:
                score = int(r)
            except (ValueError, TypeError):
                score = 0
        if 1 <= score <= 5:
            counts[score] = counts.get(score, 0) + 1

    total = sum(counts.values())
    max_count = max(counts.values()) if any(counts.values()) else 1
    bar_height = 8

    score_colors = {
        1: _RE,
        2: _YE,
        3: _CY,
        4: _GR,
        5: _MA,
    }
    score_labels = {1: "1★", 2: "2★", 3: "3★", 4: "4★", 5: "5★"}

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]📊 Response Quality Distribution[/] [dim]({total} ratings)[/]\n")
    else:
        print(f"\n{_B}📊 Response Quality Distribution{_R} {_DM}({total} ratings){_R}\n")

    for row in range(bar_height, 0, -1):
        threshold = (row / bar_height) * max_count
        line = "  "
        for score in range(1, 6):
            count = counts[score]
            color = score_colors[score]
            if count >= threshold:
                line += f"{color}  ██  {_R}"
            else:
                line += f"{_DM}  ░░  {_R}"
        print(line)

    label_line = "  "
    count_line = "  "
    for score in range(1, 6):
        color = score_colors[score]
        label_line += f"{color} {score_labels[score]}  {_R}"
        count_line += f"{_DM}({counts[score]:>2})  {_R}"
    print(label_line)
    print(count_line)

    if total > 0:
        avg = sum(s * counts[s] for s in range(1, 6)) / total
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n  [dim]Average rating: [bold]{avg:.1f}[/]/5.0  ·  Total: {total}[/]\n")
            if snapshot:
                _RICH_CONSOLE.print(
                    f"  [dim]Latest route:[/] [bold]{snapshot.get('what_happened', '')}[/]  "
                    f"[dim]· confidence[/] [{snapshot.get('conf_color', 'dim')}]{snapshot.get('conf_label', '(unknown)')}[/]"
                )
                _RICH_CONSOLE.print("  [dim]Use /trace for the full decision snapshot.[/]\n")
        else:
            print(f"\n  {_DM}Average rating: {_B}{avg:.1f}{_R}{_DM}/5.0  ·  Total: {total}{_R}\n")
            if snapshot:
                print(
                    f"  Latest route: {snapshot.get('what_happened', '')} · confidence {snapshot.get('conf_label', '(unknown)')}"
                )
                print("  Use /trace for the full decision snapshot.\n")

    return _CMD_CONTINUE


# Note: this second _cmd_stats (usage bar charts) overwrites the first
# (session aggregate) — matching the behavior in openclaw_cli.py.
def _cmd_stats(ctx: "ChatCommandContext") -> str:  # type: ignore[no-redef]
    """/stats [category] — show ASCII bar charts of usage statistics (commands, ratings, sessions)."""
    category = ctx.args.strip().lower() or "all"
    is_tty = _get_is_tty()

    cmd_history = _PREFS.get("cmd_history", [])
    ratings = _PREFS.get("ratings", [])

    def _ascii_bar_chart(title: str, data: dict, max_bar: int = 30, color: str = _CY) -> None:
        if not data:
            print(f"  {_DM}No data for {title}{_R}")
            return
        rows = _build_ascii_bar_rows(data, max_bar)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n[bold cyan]{title}[/]")
            for label, bar, count in rows:
                _RICH_CONSOLE.print(f"  [dim]{label:<20}[/] [cyan]{bar:<30}[/] [bold]{count}[/]")
        else:
            print(f"\n{_B}{title}{_R}")
            for label, bar, count in rows:
                print(f"  {_DM}{label:<20}{_R} {color}{bar:<30}{_R} {_B}{count}{_R}")

    cmd_counts: dict = {}
    rating_counts: dict = {}

    if category in ("all", "commands"):
        cmd_counts = _compute_cmd_freq(cmd_history)
        _ascii_bar_chart("📊 Command Frequency", cmd_counts, color=_CY)

    if category in ("all", "ratings"):
        rating_counts = _compute_rating_freq(ratings)
        _ascii_bar_chart("⭐ Rating Distribution", rating_counts, color=_YE)

    if category in ("all", "sessions"):
        try:
            sessions = list_sessions()
            date_counts: dict = {}
            for s in sessions[-50:]:
                ts = s.get("created_at", s.get("timestamp", ""))
                date = ts[:10] if ts else "unknown"
                date_counts[date] = date_counts.get(date, 0) + 1
            _ascii_bar_chart("📅 Sessions by Date", date_counts, color=_GR)
        except (OSError, AttributeError, ValueError, TypeError):  # noqa: BLE001
            pass

    if not cmd_counts and not rating_counts:
        msg = "No usage data yet. Chat a bit first!"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n[dim]{msg}[/]\n")
        else:
            print(f"\n{_DM}{msg}{_R}\n")

    return _CMD_CONTINUE


def _cmd_timeline(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/timeline — show a visual activity timeline of recent openclaw usage."""
    import datetime

    is_tty = _get_is_tty()

    cmd_history = _get_cli_mod()._PREFS.get("cmd_history", [])

    if not cmd_history:
        msg = "No history yet — use openclaw for a while to see your timeline!"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    by_date: dict = {}
    for entry in cmd_history:
        if isinstance(entry, dict):
            ts_str = entry.get("timestamp", entry.get("ts", ""))
            text = entry.get("text", entry.get("prompt", entry.get("cmd", "")))
        else:
            continue
        if not ts_str or not text:
            continue
        try:
            ts = datetime.datetime.fromisoformat(ts_str)
            date_key = ts.strftime("%Y-%m-%d")
            if date_key not in by_date:
                by_date[date_key] = []
            by_date[date_key].append((ts.strftime("%H:%M"), text))
        except (ValueError, AttributeError):
            continue

    if not by_date:
        msg = "No timestamped history found."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    sorted_dates = sorted(by_date.keys(), reverse=True)[:7]

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]📅 Activity Timeline[/] [dim](last {len(sorted_dates)} days)[/]\n")

        for date_str in sorted_dates:
            entries = by_date[date_str]
            count = len(entries)

            bar_len = min(count, 20)
            bar = "█" * bar_len

            try:
                dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                day_label = dt.strftime("%a %b %d")
                today = datetime.date.today()
                diff = (today - dt.date()).days
                if diff == 0:
                    day_label = f"Today ({day_label})"
                elif diff == 1:
                    day_label = f"Yesterday ({day_label})"
            except (ValueError, TypeError):  # noqa: BLE001
                day_label = date_str

            _RICH_CONSOLE.print(f"  [bold]{day_label}[/]  [cyan]{bar}[/] [dim]{count} events[/]")

            for time_str, text in reversed(entries[-3:]):
                preview = text[:55] + "…" if len(text) > 55 else text
                style = "bold green" if text.startswith("/") else "dim"
                _RICH_CONSOLE.print(f"    [dim]{time_str}[/]  [{style}]{preview}[/]")

            _RICH_CONSOLE.print()
    else:
        print(f"\n📅 Activity Timeline (last {len(sorted_dates)} days)\n")
        for date_str in sorted_dates:
            entries = by_date[date_str]
            count = len(entries)
            bar = "█" * min(count, 20)
            print(f"  {date_str}  {_CY}{bar}{_R} {_DM}{count} events{_R}")
            for time_str, text in reversed(entries[-3:]):
                preview = text[:55]
                print(f"    {_DM}{time_str}  {preview}{_R}")
            print()

    return _CMD_CONTINUE
