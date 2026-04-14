"""openclaw_cli_content_cmds.py — Pure formatting/data-building helpers for content command handlers.

Extracted from content-oriented command handlers in openclaw_cli.py.
Handlers (_cmd_*) remain in openclaw_cli.py; only pure inner helpers live here.

Allowed imports: stdlib only (no openclaw_cli_* imports needed here).
Do NOT import from openclaw_cli — circular import.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any


# ---------------------------------------------------------------------------
# _cmd_export helpers
# ---------------------------------------------------------------------------

def _build_export_body(
    cmd_history: list,
    fmt: str,
    now_str: str,
    exported_at_iso: str,
) -> str:
    """Build the text body for /export content (md, json, or txt format).

    Args:
        cmd_history:     List of command history entries (str or dict).
        fmt:             Format string: "md", "json", or "txt".
        now_str:         Human-readable timestamp for the export header.
        exported_at_iso: ISO-format timestamp for JSON metadata field.

    Returns:
        The full content string ready to write to a file.
    """
    if fmt == "md":
        lines: list[str] = [
            "# OpenClaw Session Export\n",
            f"**Exported:** {now_str}\n\n---\n",
        ]
        for i, entry in enumerate(cmd_history, 1):
            if isinstance(entry, str):
                lines.append(f"### [{i}] Prompt\n\n{entry}\n\n")
            elif isinstance(entry, dict):
                prompt = entry.get("text", entry.get("prompt", entry.get("cmd", "")))
                ts_str = entry.get("timestamp", entry.get("ts", ""))
                ts_label = f" _{ts_str}_" if ts_str else ""
                lines.append(f"### [{i}]{ts_label}\n\n{prompt}\n\n")
        return "".join(lines)

    if fmt == "json":
        export_data: dict[str, Any] = {
            "exported_at": exported_at_iso,
            "entry_count": len(cmd_history),
            "history": cmd_history,
        }
        return json.dumps(export_data, indent=2, default=str)

    # txt
    lines = [f"OpenClaw Session Export — {now_str}\n", "=" * 60 + "\n\n"]
    for i, entry in enumerate(cmd_history, 1):
        if isinstance(entry, str):
            lines.append(f"[{i}] {entry}\n\n")
        elif isinstance(entry, dict):
            prompt = entry.get("text", entry.get("prompt", entry.get("cmd", "")))
            lines.append(f"[{i}] {prompt}\n\n")
    return "".join(lines)


# ---------------------------------------------------------------------------
# _cmd_stats (bar-chart variant) helpers
# ---------------------------------------------------------------------------

def _compute_cmd_freq(cmd_history: list) -> dict[str, int]:
    """Count the frequency of each command token in cmd_history.

    Args:
        cmd_history: List of history entries (str or dict).

    Returns:
        Mapping of command token → occurrence count.
    """
    counts: dict[str, int] = {}
    for entry in cmd_history:
        if isinstance(entry, dict):
            cmd = entry.get("cmd", entry.get("command", "unknown"))
        else:
            cmd = str(entry)
        cmd = cmd.split()[0] if cmd else "unknown"
        counts[cmd] = counts.get(cmd, 0) + 1
    return counts


def _compute_rating_freq(ratings: list) -> dict[str, int]:
    """Count the frequency of each rating label in the ratings list.

    Args:
        ratings: List of rating entries (str or dict with "score"/"rating" key).

    Returns:
        Mapping of display label (e.g. "⭐⭐⭐") → occurrence count.
    """
    counts: dict[str, int] = {}
    for r in ratings:
        if isinstance(r, dict):
            score = str(r.get("score", r.get("rating", "?")))
        else:
            score = str(r)
        label = "⭐" * int(score) if score.isdigit() else score
        counts[label] = counts.get(label, 0) + 1
    return counts


def _build_ascii_bar_rows(
    data: dict[str, int],
    max_bar: int = 30,
) -> list[tuple[str, str, int]]:
    """Compute sorted top-10 bar-chart rows from a frequency dict.

    Args:
        data:    Mapping of label → count.
        max_bar: Maximum bar width in characters.

    Returns:
        List of (label, bar_chars, count) tuples, sorted descending by count,
        capped at 10 entries.  Returns an empty list when *data* is empty.
    """
    if not data:
        return []
    max_val = max(data.values())
    rows: list[tuple[str, str, int]] = []
    for label, count in sorted(data.items(), key=lambda x: -x[1])[:10]:
        bar_len = int((count / max_val) * max_bar)
        rows.append((label, "█" * bar_len, count))
    return rows


# ---------------------------------------------------------------------------
# _cmd_stats (aggregate variant) helpers
# ---------------------------------------------------------------------------

def _build_session_stats_agg(sessions: list) -> dict[str, Any]:
    """Aggregate a list of SessionSummary objects into a stats summary dict.

    Args:
        sessions: List of SessionSummary objects (from list_sessions()).

    Returns:
        Dict with keys: total_sessions, total_commands, total_edits,
        total_checkpoints, active, newest, oldest, top_cwds.
    """
    total_sessions = len(sessions)
    total_commands = sum(getattr(s, "command_count", 0) for s in sessions)
    total_edits = sum(getattr(s, "file_edit_count", 0) for s in sessions)
    total_checkpoints = sum(getattr(s, "checkpoint_count", 0) for s in sessions)
    active = sum(1 for s in sessions if getattr(s, "status", "") == "active")

    first = sessions[0] if sessions else None
    last = sessions[-1] if sessions else None
    newest = (first.updated_at[:10] if first and getattr(first, "updated_at", None) else "—")
    oldest = (last.created_at[:10] if last and getattr(last, "created_at", None) else "—")

    cwd_counts: Counter[str] = Counter()
    for s in sessions:
        cwd = getattr(s, "cwd", None)
        if cwd:
            cwd_counts[cwd] += 1

    return {
        "total_sessions": total_sessions,
        "total_commands": total_commands,
        "total_edits": total_edits,
        "total_checkpoints": total_checkpoints,
        "active": active,
        "newest": newest,
        "oldest": oldest,
        "top_cwds": cwd_counts.most_common(3),
    }
