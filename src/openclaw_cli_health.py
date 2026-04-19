"""openclaw_cli_health.py — Health checking and source/operator display helpers.

Extracted from openclaw_cli.py. Contains print_health, _clean_sources_for_display,
and _operator_snapshot_lines.

_last_trace_snapshot was NOT extracted: tests monkeypatch get_last_decision_event
and _PREFS on openclaw_cli directly, so it must stay in main to preserve test coverage.

Allowed imports: openclaw_cli_session_display, openclaw_cli_ui_core,
                 openclaw_cli_preprocess, stdlib only.
Do NOT import from openclaw_cli — circular import.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openclaw_cli_preprocess import (
    _RE_ANSI_ESCAPE,
    _RE_BARE_URL,
    _RE_MD_LINK,
)
from openclaw_cli_session_display import (
    _progress_cell,
    _single_line_excerpt,
    _status_emoji,
)
from openclaw_cli_ui_core import (
    _B,
    _BGR,
    _BYE,
    _DM,
    _IS_TTY,
    _R,
)

# ---------------------------------------------------------------------------
# Rich — graceful fallback when not in a TTY or rich absent
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
    _RICH_CONSOLE = None  # type: ignore[assignment]
    _RichPanel = None  # type: ignore[assignment,misc]
    _RichTable = None  # type: ignore[assignment,misc]
    _RichText = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# HealthResponse dataclass
# ---------------------------------------------------------------------------


@dataclass
class HealthResponse:
    """Structured response from the OpenClaw health endpoint."""

    payload: Any
    raw_text: str
    status: str = ""
    healthy: bool | None = None


# ---------------------------------------------------------------------------
# Health display
# ---------------------------------------------------------------------------


def print_health(response: HealthResponse, *, output_json: bool) -> None:
    """Render a health response to stdout."""
    if output_json:
        if isinstance(response.payload, str):
            print(json.dumps({"health": response.payload}, indent=2, sort_keys=True))
            return
        print(json.dumps(response.payload, indent=2, sort_keys=True))
        return
    status = (response.status or "unknown").upper()
    emoji = _status_emoji(response.status or "")
    if _RICH_AVAILABLE and _IS_TTY:
        border = "green" if response.healthy is True else ("yellow" if response.healthy is False else "dim")
        status_style = (
            "bold green" if response.healthy is True else ("bold yellow" if response.healthy is False else "dim")
        )
        t = _RichText()
        t.append(f"{emoji}  OpenClaw  ", style="bold")
        t.append(status, style=status_style)
        if isinstance(response.payload, dict):
            grid = _RichTable.grid(padding=(0, 2))
            grid.add_column(style="dim", min_width=14)
            grid.add_column()
            labels = {
                "uptime_seconds": "uptime",
                "bot_user": "bot",
                "guilds": "guilds",
                "python": "python",
                "discord_py": "discord.py",
            }
            for key, label in labels.items():
                if key in response.payload:
                    val = str(response.payload[key])
                    if key == "uptime_seconds":
                        val = f"{val}s"
                    grid.add_row(label, val)
            checks = response.payload.get("checks")
            if isinstance(checks, dict) and checks:
                for name, value in sorted(checks.items()):
                    chk_emoji = "✅" if str(value).lower() in {"ok", "true", "healthy"} else "⚠️"
                    grid.add_row(f"  {name}", f"{chk_emoji} {value}")
            from rich.console import Group as _RichGroup

            _RICH_CONSOLE.print(_RichPanel(_RichGroup(t, grid), border_style=border, padding=(0, 1)))
        elif isinstance(response.payload, str) and response.payload.strip():
            from rich.console import Group as _RichGroup

            _RICH_CONSOLE.print(
                _RichPanel(
                    _RichGroup(t, _RichText(response.payload.strip(), style="dim")), border_style=border, padding=(0, 1)
                )
            )
        else:
            _RICH_CONSOLE.print(_RichPanel(t, border_style=border, padding=(0, 1)))
    else:
        if response.healthy is True:
            prefix = f"{_BGR}OK{_R}"
        elif response.healthy is False:
            prefix = f"{_BYE}WARN{_R}"
        else:
            prefix = f"{_DM}INFO{_R}"
        print(f"{emoji}  {prefix} OpenClaw health: {_B}{status}{_R}")
        if isinstance(response.payload, dict):
            for key in ("uptime_seconds", "bot_user", "guilds", "python", "discord_py"):
                if key in response.payload:
                    val = str(response.payload[key])
                    if key == "uptime_seconds":
                        val = f"{val}s"
                    print(f"  {_DM}{key}:{_R}  {val}")
            checks = response.payload.get("checks")
            if isinstance(checks, dict) and checks:
                for name, value in sorted(checks.items()):
                    print(f"  {_DM}{name}:{_R}  {value}")
            return
        if isinstance(response.payload, str) and response.payload.strip():
            print(response.payload.strip())


# ---------------------------------------------------------------------------
# Source URL display
# ---------------------------------------------------------------------------


def _clean_sources_for_display(sources: str) -> list[tuple[str, str]]:
    """Extract clean URLs from a sources block, stripping markdown link syntax.

    Handles:
      - Bare URLs: https://example.com
      - Markdown links: [text](https://example.com)  → https://example.com
      - Numbered/bulleted prefixes: 1. / - / * stripped
    Returns a list of (display_text, url) tuples, or (url, url) if no text.
    """
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in sources.splitlines():
        line = line.strip()
        # Strip bullet/number prefix
        line = re.sub(r"^(?:\d+\.|[-\*])\s+", "", line)
        line = line.strip()
        if not line:
            continue
        # Check for markdown link [text](url)
        md = _RE_MD_LINK.search(line)
        if md:
            text, url = md.group(1).strip(), md.group(2).strip()
            display = text if text and text != url else url
            display = _RE_ANSI_ESCAPE.sub("", display).strip()
            if not display or "http://" in display or "https://" in display:
                display = url
            if url not in seen:
                seen.add(url)
                results.append((display, url))
            continue
        # Check for bare URL
        bare = _RE_BARE_URL.search(line)
        if bare:
            url = bare.group(1).rstrip(")")
            if url not in seen:
                seen.add(url)
                results.append((url, url))
    return results


# ---------------------------------------------------------------------------
# Operator snapshot display
# ---------------------------------------------------------------------------


def _operator_snapshot_lines(snapshot: dict[str, Any]) -> list[str]:
    """Render human-readable lines for the operator snapshot."""
    lines = [
        _progress_cell("visibility", str(snapshot.get("access") or "read-only local snapshot"), status="info"),
    ]
    readiness_label = str(snapshot.get("readiness_label") or "").strip()
    readiness_detail = str(snapshot.get("readiness_detail") or "").strip()
    if readiness_label:
        readiness_value = readiness_label if not readiness_detail else f"{readiness_label} · {readiness_detail}"
        lines.append(
            _progress_cell(
                "readiness",
                readiness_value,
                status=str(snapshot.get("readiness_status") or "info"),
            )
        )
    watch_summary = str(snapshot.get("watch_summary") or "").strip()
    if watch_summary:
        lines.append(f"operator watch: {watch_summary}")
    queue_summary = str(snapshot.get("queue_summary") or "").strip()
    if queue_summary:
        lines.append(f"operator queue: {queue_summary}")
    latest_output = str(snapshot.get("latest_output") or "").strip()
    if latest_output:
        lines.append(f"latest output: {latest_output}")
    latest_decision = str(snapshot.get("latest_decision") or "").strip()
    if latest_decision:
        lines.append(f"latest decision: {_single_line_excerpt(latest_decision, max_chars=100)}")
    latest_note = str(snapshot.get("latest_note") or "").strip()
    if latest_note:
        lines.append(f"latest note: {_single_line_excerpt(latest_note, max_chars=100)}")
    latest_handoff = str(snapshot.get("latest_handoff") or "").strip()
    if latest_handoff:
        lines.append(f"latest handoff: {latest_handoff}")
    control = str(snapshot.get("control") or "").strip()
    if control:
        lines.append(f"control: {control}")
    return lines
