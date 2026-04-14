"""Terminal client for OpenClaw's authenticated ask API."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import getpass
import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

from openclaw_cli_auth import (
    AUTH_FILE_NAME,
    KEYCHAIN_SERVICE,
    TOKEN_ENV_VARS,
    OpenClawCliError,
    TokenResolution,
    auth_storage_path,
    delete_keychain_token,
    read_keychain_token,
    write_keychain_token,
)
from openclaw_cli_actions import (
    format_shell_result,
    infer_command_risk,
    infer_file_edit_risk,
    preview_file_result,
    replace_text_in_file,
    request_cli_approval,
    risk_level_from_name,
    run_shell_command,
    write_text_file,
)
from openclaw_cli_sessions import (
    SessionSummary,
    append_event,
    apply_handoff,
    build_collaboration_snapshot,
    build_session_storyline,
    build_workspace_capsule,
    build_workspace_signature,
    collect_workspace_context,
    create_handoff,
    create_routed_action_checkpoint,
    create_session_bookmark,
    create_session,
    export_session,
    extract_prompt_targets,
    find_session_bookmark,
    get_last_decision_event,
    list_handoffs,
    list_saved_outputs,
    list_session_bookmarks,
    list_sessions,
    load_conversation_history,
    load_events,
    load_handoff,
    load_saved_output_preview,
    load_session,
    load_watch_state,
    recent_output_context,
    require_session,
    restore_last_routed_action_checkpoint,
    save_output,
    save_session,
    save_watch_state,
    update_session,
)

try:
    import readline
except ImportError:  # pragma: no cover - platform-dependent
    readline = None

import openclaw_cli_update as _update_mod
from openclaw_cli_update import (
    cli_version,
    _version_tuple,
    _fetch_latest_pypi_version,
    _find_pip,
    _print_update_notice,
    _standalone_install_dir,
    _update_standalone_install,
    handle_update_command,
    check_for_update,
)
from openclaw_cli_diff import _render_diff_ansi as _render_diff_ansi_impl

# ---------------------------------------------------------------------------
# Router — REPL routing and intent classification
# ---------------------------------------------------------------------------
from openclaw_cli_router import (
    REPL_ROUTE_AUTO_THRESHOLD,
    REPL_ROUTE_ANNOUNCEMENT_COMMAND_LIMIT,
    REPL_ROUTE_ANNOUNCEMENT_REASON_LIMIT,
    ReplRouteStepContext,
    ReplRouteGrounding,
    ReplRouteKind,
    ReplPlanStep,
    ReplRouteDecision,
    _ROUTE_DOC_HINTS,
    _ROUTE_ANALYZE_HINTS,
    _ROUTE_SHELL_HINTS,
    _ROUTE_ACTION_HINTS,
    _PLAN_ROUTE_SPLIT_RE,
    _PLAN_ROUTE_LEAD_RE,
    _EDIT_ROUTE_RE,
    _PLAN_CREATE_RESULT_RE,
    _ROUTE_STEP_REF_RE,
    _ROUTE_CURRENT_STEP_RE,
    _ROUTE_CURRENT_TASK_RE,
    _ROUTE_PROGRESS_PREFIXES,
    _ROUTE_STEP_WORDS,
    _candidate_workspace_roots as _router_candidate_workspace_roots,
    _resolve_local_source as _router_resolve_local_source,
    _find_local_tasks_file,
    _load_task_record,
    _load_route_plan,
    _normalize_route_step_context,
    _active_plan_step,
    _find_plan_step_context,
    _load_repl_route_grounding,
    _normalize_prompt_text,
    _clean_route_token,
    _unwrap_route_text,
    _normalize_route_field,
    _extract_fenced_route_block,
    _iter_route_quoted_segments,
    _shell_split_route_tokens,
    _first_shell_token,
    _shell_quote_route_arg,
    _looks_like_path,
    _extract_first_path,
    _strip_request_lead,
    _extract_after_prefix,
    _strip_route_prefixes,
    _clean_route_fragment,
    _extract_route_quoted_content,
    _find_route_path_span,
    _extract_append_content,
    _extract_replace_values,
    _extract_structured_edit_route,
    _extract_write_payload,
    _parse_route_step_number,
    _remove_route_span,
    _build_chat_route,
    _build_route_decision,
    _grounded_subject_route,
    _grounding_intent,
    _grounded_prompt_route,
    _apply_grounding_to_route,
    _maybe_route_with_grounding,
    _clean_plan_clause,
    _classify_repl_clause,
    _maybe_build_plan_route,
    _deterministic_repl_route,
    _looks_action_like,
    _extract_exec_args,
    _extract_route_payload,
    lightweight_classify_repl_prompt,
    route_repl_prompt as _router_route_repl_prompt,
    _truncate_repl_route_text,
    _session_auto_route_enabled,
    _confidence_badge,
    _format_route_announcement,
    _append_repl_route_event,
    _plan_step_slash_command,
    _extract_created_plan_id,
)

# ---------------------------------------------------------------------------
# Terminal detection and ANSI palette — defined in openclaw_cli_ui_core
# Re-exported here for backward compatibility with existing code and tests.
# ---------------------------------------------------------------------------
from openclaw_cli_ui_core import (
    _IS_TTY,
    _c,
    _get_is_tty,
    _R, _B, _DM, _CY, _GR, _YE, _RE, _MA,
    _BCY, _BGR, _BYE, _BRE, _BBL, _IT, _UL,
)
from openclaw_cli_exec import (
    _separator_fill as _exec_separator_fill,
    _motion_pause as _exec_motion_pause,
    _spinner_phase_label as _exec_spinner_phase_label,
    _spinner_progress_snapshot as _exec_spinner_progress_snapshot,
    _response_footer_lines as _exec_response_footer_lines,
    _progress_bar as _exec_progress_bar,
    _exec_progress_animate as _exec_animate_fn,
    _analyze_exec_error as _exec_analyze_exec_error,
    _print_exec_error_hints as _exec_print_exec_error_hints,
)


def _get_is_tty() -> bool:
    """Live TTY check — reads openclaw_cli._IS_TTY so monkeypatch works in tests."""
    return _IS_TTY or sys.stdout.isatty()

# ---------------------------------------------------------------------------
# Color / rich support — graceful fallback when not in a TTY or rich absent
# ---------------------------------------------------------------------------
try:
    from rich.console import Console as _RichConsole
    from rich.markdown import Markdown as _RichMarkdown
    from rich.panel import Panel as _RichPanel
    from rich.table import Table as _RichTable
    from rich.text import Text as _RichText

    _RICH_CONSOLE = _RichConsole(highlight=False)
    _RICH_ERR = _RichConsole(stderr=True, highlight=False)
    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RICH_AVAILABLE = False

import openclaw_cli_health as _health_mod
from openclaw_cli_health import HealthResponse
import openclaw_cli_types as _types_mod
from openclaw_cli_types import (
    AskResponse,
    ChatCommandContext,
    ChatCommandRegistry,
    CliConfig,
    LocalLinkValidation,
    SlashCommand,
)
import openclaw_cli_render as _render_mod
from openclaw_cli_render import _render_markdown_ansi  # re-exported; implementation lives in render module
import openclaw_cli_preprocess as _preprocess_mod
from openclaw_cli_preprocess import (
    _MD_TABLE_BLOCK,
    _RE_ANSI_ESCAPE,
    _RE_MD_LINK,
    _RE_BARE_URL,
    _RE_SOURCES_BLOCK,
    _RE_SOURCES_BLOCK_LOOSE,
    _parse_md_table,
)
import openclaw_cli_path_utils as _path_utils
import openclaw_cli_macros as _macros_mod
import openclaw_cli_layout as _layout_mod
import openclaw_cli_session_cmds as _session_cmds_mod
import openclaw_cli_cmd_core as _core_cmd_mod
import openclaw_cli_cmd_session as _cmd_session_mod
import openclaw_cli_cmd_workflow as _workflow_cmd_mod
import openclaw_cli_ui_utils as _ui_utils_mod
import openclaw_cli_content_cmds as _content_cmds_mod
import openclaw_cli_cmd_content as _content_cmd_mod
import openclaw_cli_cmd_settings as _settings_cmd_mod
import openclaw_cli_cmd_system as _system_cmd_mod
import openclaw_cli_cmd_misc as _misc_cmd_mod
import openclaw_cli_session_display as _session_display_mod
import openclaw_cli_session_utils as _session_utils_mod
import openclaw_cli_watch as _watch_mod
from openclaw_cli_watch import (
    normalize_watch_state,
    _watch_timing_summary,
    _watch_focus_lines,
    _print_watch_status,
    _print_watch_history,
    handle_watch_command,
    execute_watch_iteration,
    build_watch_state,
    watch_retry_delay_seconds,
    is_transient_watch_error,
    start_watch_checkpoint,
    record_watch_progress,
    print_watch_resume_snapshot,
    refresh_watch_controls,
    resolve_watch_intervention,
    stop_watch_from_intervention,
    render_watch_iteration,
    load_plan_goal,
    _watch_retry_delay_total,
)
import logging as _logging

_LOG = _logging.getLogger("openclaw_cli")

# Draft buffer — ephemeral unsent prompt (cleared on submission or /draft clear)
_draft_buffer: str = ""
# Last interrupted prompt for restore-last (set on KeyboardInterrupt/Ctrl-C)
_last_interrupted_prompt: str = ""
# Multiline compose mode — toggled by /draft multiline on/off
_multiline_mode: bool = False
# Last AI response text — used by /pin
_last_response_text: str = ""
# Content to prepend to the next outgoing message — set by /inject
_next_inject: str = ""


DEFAULT_BASE_URL = "http://localhost:8765"
DEFAULT_MODEL = "auto"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_VERSION = "0.6.0"
_CLI_BUILD = "wave45"  # updated with each UX wave batch

_DEFAULT_PROMPT_FORMAT = "{route} openclaw{session}> "
HISTORY_FILE = Path.home() / ".openclaw_history"
HISTORY_LIMIT = 500
WATCH_PROGRESS_LOG_LIMIT = 25
WATCH_RETRY_LIMIT = 3
WATCH_RETRY_MAX_DELAY_SECONDS = 8
CONTEXT_PREVIEW_MAX_CHARS = 5_000
OUTPUT_LIST_LIMIT = 10
OUTPUT_PREVIEW_MAX_CHARS = 4_000
OUTPUT_OVERLAY_EXCERPT_CHARS = 140
OUTPUT_DASHBOARD_EXCERPT_CHARS = 220
SESSION_PREVIEW_OUTPUT_CHARS = 160
WATCH_FOCUS_NOTE_CHARS = 120
# REPL_ROUTE_* constants imported from openclaw_cli_router above.

# ---------------------------------------------------------------------------
# User preferences — imported from openclaw_cli_prefs
# ---------------------------------------------------------------------------
import openclaw_cli_prefs as _prefs_mod
from openclaw_cli_prefs import (
    _OPENCLAW_DIR,
    _PREFS_FILE,
    _PREFS,
    _THEMES,
    _THEME_ORDER,
    _THEME_DESCRIPTIONS,
    _THEME_ALIASES,
    _OPENCLAW_TIPS,
    _A11Y_REDUCED_MOTION,
    _A11Y_PLAIN_MODE,
    _A11Y_HIGH_CONTRAST,
    _EMOJI_PACKS,
    _load_prefs,
    _save_prefs,
    _prefs_dir_path,
    _prefs_file_path,
    _normalize_theme_name,
    _emoji_pack_name,
    _normalize_personalization_prefs,
)


def _prefs_set(key: str, value: object) -> None:
    """Update _PREFS in-place and persist.

    Defined as a shim in this module (not re-exported from openclaw_cli_prefs)
    so that tests can monkeypatch both `mod._PREFS` and `mod._save_prefs`
    independently and have _prefs_set see both replacements via module globals.
    """
    _PREFS[key] = value
    _save_prefs()

_HEADING_EMOJIS: dict[int, str] = {
    1: "✨",  # H1 — rare, important
    2: "🔹",  # H2 — section header
    3: "▸",   # H3 — subsection
    4: "·",   # H4 — minor sub
}

_CMD_HISTORY_MAX = 50  # max entries in command history
_SPINNER_HEARTBEAT_SECONDS = 4.0
_MOTION_PACING_SECONDS: dict[str, float] = {
    "banner": 0.04,
    "separator": 0.03,
    "footer": 0.02,
}

_HIGH_CONTRAST_THEMES: dict[str, tuple[str, str]] = {
    "default":  ("bold bright_white",   "\033[1;97m"),
    "green":    ("bold bright_green",   "\033[1;92m"),
    "yellow":   ("bold bright_yellow",  "\033[1;93m"),
    "magenta":  ("bold bright_magenta", "\033[1;95m"),
    "cyan":     ("bold bright_cyan",    "\033[1;96m"),
    "mono":     ("bold white",          "\033[1;37m"),
}

# ASCII fallbacks for each emoji used in the UI
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

_EXTENDED_SCHEMES: dict[str, dict[str, str]] = {
    "cyberpunk": {
        "primary": "\033[95m",    # bright magenta
        "accent":  "\033[96m",    # bright cyan
        "dim":     "\033[35m",    # magenta dim
        "ok":      "\033[92m",    # bright green
        "warn":    "\033[93m",    # bright yellow
        "error":   "\033[91m",    # bright red
        "label":   "cyberpunk 🌆",
    },
    "ocean": {
        "primary": "\033[96m",    # bright cyan
        "accent":  "\033[34m",    # blue
        "dim":     "\033[36m",    # cyan
        "ok":      "\033[32m",    # green
        "warn":    "\033[33m",    # yellow
        "error":   "\033[31m",    # red
        "label":   "ocean 🌊",
    },
    "sunset": {
        "primary": "\033[33m",    # yellow
        "accent":  "\033[31m",    # red
        "dim":     "\033[35m",    # magenta
        "ok":      "\033[32m",    # green
        "warn":    "\033[91m",    # bright red
        "error":   "\033[31m",    # red
        "label":   "sunset 🌅",
    },
    "matrix": {
        "primary": "\033[92m",    # bright green
        "accent":  "\033[32m",    # green
        "dim":     "\033[2;32m",  # dim green
        "ok":      "\033[92m",    # bright green
        "warn":    "\033[33m",    # yellow
        "error":   "\033[91m",    # bright red
        "label":   "matrix 🟩",
    },
    "default": {
        "primary": "\033[36m",    # cyan
        "accent":  "\033[1;36m",  # bold cyan
        "dim":     "\033[2m",     # dim
        "ok":      "\033[32m",    # green
        "warn":    "\033[33m",    # yellow
        "error":   "\033[31m",    # red
        "label":   "default 🦞",
    },
}


def _a11y_reduced_motion() -> bool:
    """Return True when reduced-motion mode is active."""
    return bool(_PREFS.get(_A11Y_REDUCED_MOTION, False))


def _a11y_plain_mode() -> bool:
    """Return True when plain/screen-reader mode is active."""
    return bool(_PREFS.get(_A11Y_PLAIN_MODE, False))


def _a11y_high_contrast() -> bool:
    """Return True when high-contrast mode is active."""
    return bool(_PREFS.get(_A11Y_HIGH_CONTRAST, False))


def _interactive_overlays_enabled() -> bool:
    """Return True when opt-in interactive overlays are enabled."""
    return bool(_PREFS.get("interactive_overlays", False))


def _overlay_available() -> bool:
    """Return True when an interactive overlay can safely prompt for input."""
    stdin_tty = True
    try:
        stdin_tty = bool(sys.stdin.isatty())
    except Exception:  # noqa: BLE001  # TTY detection may fail; degrade gracefully
        stdin_tty = False
    return bool(_get_is_tty() and stdin_tty)


def _overlay_query_score(text: str, query: str) -> int:
    """Return a simple fuzzy score; 0 means no match."""
    haystack = " ".join(str(text or "").lower().split())
    needle = " ".join(str(query or "").lower().split())
    if not needle:
        return 1
    if needle in haystack:
        return 1000 - max(0, haystack.find(needle))
    tokens = [token for token in needle.split(" ") if token]
    if tokens and all(token in haystack for token in tokens):
        return 700 - sum(max(0, haystack.find(token)) for token in tokens)
    pos = -1
    score = 0
    for ch in needle:
        next_pos = haystack.find(ch, pos + 1)
        if next_pos == -1:
            return 0
        score += max(1, 20 - min(19, next_pos - pos))
        pos = next_pos
    return score


def _overlay_filter_items(
    items: list[Any],
    *,
    query: str,
    label_fn: Callable[[Any], str],
    limit: int = 9,
) -> list[Any]:
    """Return the top overlay matches for a query."""
    scored: list[tuple[int, int, Any]] = []
    for index, item in enumerate(items):
        score = _overlay_query_score(label_fn(item), query)
        if score > 0:
            scored.append((score, index, item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [item for _, _, item in scored[:limit]]


def _run_interactive_overlay(
    *,
    title: str,
    items: list[Any],
    label_fn: Callable[[Any], str],
    on_select: Callable[[Any], None],
    initial_query: str = "",
    empty_message: str = "No matches.",
) -> str:
    """Run a lightweight interactive picker for supported REPL overlays."""
    if not items:
        print(empty_message)
        return "empty"
    if not _overlay_available():
        print(f"{_DM}Interactive overlay unavailable here; falling back to the normal listing.{_R}")
        return "fallback"

    query = initial_query.strip()
    while True:
        matches = _overlay_filter_items(items, query=query, label_fn=label_fn)
        print(f"\n{_B}{title}{_R}")
        if query:
            print(f"  {_DM}filter:{_R} {query}")
        if matches:
            for index, item in enumerate(matches, start=1):
                print(f"  {_CY}{index}.{_R} {label_fn(item)}")
        else:
            print(f"  {_DM}No matches for '{query}'.{_R}")
        print(f"  {_DM}Type a search term, a number to select, Enter to cancel, or q to close.{_R}")
        choice = input("overlay> ").strip()
        if not choice or choice.lower() in {"q", "quit", "exit"}:
            print(f"  {_DM}Overlay closed.{_R}")
            return "closed"
        if choice.isdigit():
            selected_index = int(choice) - 1
            if 0 <= selected_index < len(matches):
                on_select(matches[selected_index])
                return "selected"
            print(f"  {_DM}Selection out of range.{_R}")
            continue
        query = choice


def _effective_layout_mode() -> str:
    """Return the normalized active layout mode."""
    return _layout_mod._effective_layout_mode(_PREFS)


def _layout_preset_name() -> str:
    """Return the normalized active layout preset name, if any."""
    return _layout_mod._layout_preset_name(_PREFS)


def _layout_focus_name() -> str:
    """Return the active pane within the current layout preset."""
    return _layout_mod._layout_focus_name(_PREFS)


def _layout_preset_config(name: str = "") -> dict[str, str]:
    """Return the documented surface pairing for a layout preset."""
    return _layout_mod._layout_preset_config(_PREFS, name)


def _layout_preset_fallback(*, width: int | None = None, is_tty: bool | None = None) -> str:
    """Return the current preset rendering fallback label."""
    return _layout_mod._layout_preset_fallback(
        _PREFS,
        width=_terminal_width() if width is None else width,
        is_tty=_get_is_tty() if is_tty is None else is_tty,
    )


def _layout_pane_line_limit() -> int:
    """Return the maximum number of lines shown per preset pane."""
    return _layout_mod._layout_pane_line_limit(_PREFS)


def _layout_pane_block(title: str, lines: list[str], *, active: bool = False) -> list[str]:
    """Return a bounded plain-text pane block for workspace presets."""
    return _layout_mod._layout_pane_block(_PREFS, title, lines, active=active)


def _layout_column_lines(left: list[str], right: list[str], *, width: int) -> list[str]:
    """Lay out two pane blocks side-by-side using safe plain text."""
    return _layout_mod._layout_column_lines(left, right, width=width)


def _layout_outputs_lines(session_id: str) -> list[str]:
    """Return compact recent-output lines for layout presets."""
    return _layout_mod._layout_outputs_lines(_PREFS, session_id)


def _layout_collab_lines(session_id: str) -> list[str]:
    """Return collaboration snapshot lines for layout presets."""
    return _layout_mod._layout_collab_lines(_PREFS, session_id)


def _layout_watch_lines(state: dict[str, Any] | None) -> list[str]:
    """Return watch-monitor lines for layout presets."""
    return _layout_mod._layout_watch_lines(
        _PREFS,
        state,
        normalize_watch_state_fn=normalize_watch_state,
        watch_timing_summary_fn=_watch_timing_summary,
        watch_focus_lines_fn=_watch_focus_lines,
    )


def _layout_session_lines(session: SessionSummary) -> list[str]:
    """Return session health lines for layout presets."""
    return _layout_mod._layout_session_lines(
        _PREFS,
        session,
        session_preview_lines_fn=_session_preview_lines,
    )


def _print_layout_preset_workspace(ctx: "ChatCommandContext") -> None:
    """Render the active layout preset as a pane-like workspace view."""
    _layout_mod._print_layout_preset_workspace(
        _PREFS,
        str(ctx.session_id or ""),
        width=_terminal_width(fallback=100),
        is_tty=_get_is_tty(),
    )


def _e(emoji: str, fallback: str = "") -> str:
    """Return *emoji* or its ASCII fallback depending on the emoji pref."""
    pack = _emoji_pack_name()
    if pack == "classic":
        return emoji
    if pack == "minimal":
        return _EMOJI_PACKS["minimal"].get(emoji, fallback or _EMOJI_FALLBACKS.get(emoji, ""))
    return fallback or _EMOJI_FALLBACKS.get(emoji, "")


def _theme_style() -> str:
    """Return the Rich rule style string for the current theme."""
    theme = _normalize_theme_name(_PREFS.get("theme", "default"))
    palette = _HIGH_CONTRAST_THEMES if _a11y_high_contrast() else _THEMES
    rich_style, _ = palette.get(theme, palette["default"])
    return rich_style


def _theme_ansi() -> str:
    """Return the ANSI escape code for the current theme accent (plain-text path)."""
    is_tty = _get_is_tty()
    if not is_tty:
        return ""
    theme = _normalize_theme_name(_PREFS.get("theme", "default"))
    palette = _HIGH_CONTRAST_THEMES if _a11y_high_contrast() else _THEMES
    _, ansi = palette.get(theme, palette["default"])
    return ansi


# ---------------------------------------------------------------------------
# Shared display helpers
# ---------------------------------------------------------------------------

def _status_family(status: str) -> str:
    """Normalize related status words into a shared rendering family."""
    s = str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
    if s in {"ok", "healthy", "done", "completed", "success", "succeeded", "complete"}:
        return "complete"
    if s in {"active", "running", "in_progress", "working", "processing", "streaming"}:
        return "active"
    if s in {"pending", "queued", "waiting", "idle", "scheduled"}:
        return "waiting" if s != "idle" else "idle"
    if s in {"retry", "retrying", "backoff", "recovering"}:
        return "retry"
    if s in {"warn", "warning", "degraded", "attention"}:
        return "warn"
    if s in {"error", "failed", "failure", "unhealthy"}:
        return "error"
    if s in {"blocked", "stuck", "needs_input", "needs-input"}:
        return "blocked"
    if s in {"paused", "stopped", "cancelled", "canceled"}:
        return "paused"
    if s in {"info", "note", "fresh", "new"}:
        return "info"
    if s in {"stale", "old", "expired"}:
        return "stale"
    return "unknown"


def _status_text(status: str) -> str:
    """Return the canonical plain-text status label."""
    family = _status_family(status)
    return {
        "complete": "COMPLETE",
        "active": "ACTIVE",
        "waiting": "WAITING",
        "idle": "IDLE",
        "retry": "RETRY",
        "warn": "WARN",
        "error": "ERROR",
        "blocked": "BLOCKED",
        "paused": "PAUSED",
        "info": "INFO",
        "stale": "STALE",
        "unknown": "STATUS",
    }.get(family, "STATUS")


def _status_style(status: str) -> str:
    """Return the Rich/ANSI style token for a status family."""
    family = _status_family(status)
    return {
        "complete": "green",
        "active": "cyan",
        "waiting": "yellow",
        "idle": "dim",
        "retry": "magenta",
        "warn": "bold yellow",
        "error": "bold red",
        "blocked": "red",
        "paused": "yellow",
        "info": "blue",
        "stale": "dim",
        "unknown": "dim",
    }.get(family, "dim")


def _status_emoji(status: str) -> str:
    """Map a status string to a representative emoji."""
    family = _status_family(status)
    if family == "complete":
        return _e("🟢", "[ok]")
    if family == "active":
        return _e("🔵", "[run]")
    if family == "waiting":
        return _e("⏳", "[wait]")
    if family == "idle":
        return _e("⚪", "[idle]")
    if family == "retry":
        return _e("🔄", "[retry]")
    if family == "warn":
        return _e("🟡", "[warn]")
    if family == "error":
        return _e("🔴", "[err]")
    if family == "blocked":
        return _e("⛔", "[block]")
    if family == "paused":
        return _e("⏸", "[pause]")
    if family == "info":
        return _e("ℹ️", "[info]")
    if family == "stale":
        return _e("🕰️", "[stale]")
    return _e("●", "[*]")


def _status_cell(status: str, *, detail: str = "", rich: bool = False) -> str:
    """Return a compact badge-like status cell with plain-text parity."""
    label = _status_text(status)
    suffix = f" · {detail}" if detail else ""
    if rich and _RICH_AVAILABLE and _IS_TTY and not _a11y_plain_mode():
        emoji = _status_emoji(status)
        style = _status_style(status)
        return f"[{style}]{emoji} {label}[/]{suffix}"
    return f"{label}{suffix}"


def _progress_cell(label: str, value: str, *, status: str = "", rich: bool = False) -> str:
    """Return a dense progress/status cell that degrades to readable plain text."""
    cell = f"{label}: {value}".strip()
    if not status:
        return cell
    badge = _status_cell(status, rich=rich)
    return f"{badge} · {cell}"


def _premium_motion_active(*, output_json: bool = False) -> bool:
    """Return True when tasteful motion is allowed for this surface."""
    is_tty = _get_is_tty()
    return bool(is_tty and not output_json and not _a11y_plain_mode() and not _a11y_reduced_motion())


def _motion_pause(stage: str) -> None:
    """Sleep briefly to stagger premium UI choreography when motion is enabled."""
    _exec_motion_pause(
        stage,
        is_tty=_get_is_tty(),
        plain_mode=_a11y_plain_mode(),
        reduced_motion=_a11y_reduced_motion(),
    )


def _spinner_phase_label(elapsed: float) -> str:
    """Return a lightweight motion-language label for spinner pacing."""
    return _exec_spinner_phase_label(elapsed)


def _spinner_progress_snapshot(elapsed: float) -> dict[str, Any]:
    """Return live phase/step copy for the request spinner."""
    return _exec_spinner_progress_snapshot(elapsed)


def _response_footer_lines(*, elapsed: float = 0.0, tokens: int = 0, model: str = "") -> tuple[str, str]:
    """Return the footer headline and metadata line for a response."""
    return _exec_response_footer_lines(
        elapsed=elapsed,
        tokens=tokens,
        model=model,
        done_symbol=_e("✨", "[done]"),
    )


def _dashboard_section_lines(title: str, lines: list[str]) -> list[str]:
    """Return normalized lines for a plain-text dashboard section."""
    clean = [str(line).strip() for line in lines if str(line or "").strip()]
    if not clean:
        return []
    return [f"{title}:"] + [f"  - {line}" for line in clean]


def _append_dashboard_rich_section(
    body: "_RichText",
    title: str,
    lines: list[str],
    *,
    title_style: str = "bold cyan",
    line_style: str = "",
) -> None:
    """Append a dashboard section to a Rich text buffer."""
    clean = [str(line).strip() for line in lines if str(line or "").strip()]
    if not clean:
        return
    if body.plain:
        body.append("\n")
    body.append(f"{title}\n", style=title_style)
    for line in clean:
        body.append("  • ", style="dim")
        body.append(f"{line}\n", style=line_style)


def _print_dashboard_surface(
    title: str,
    *,
    summary_lines: list[str],
    detail_lines: list[str] | None = None,
    action_lines: list[str] | None = None,
    border_style: str = "dim",
) -> None:
    """Render a summary → details → actions dashboard surface with safe fallbacks."""
    detail_lines = detail_lines or []
    action_lines = action_lines or []
    is_tty = _get_is_tty()
    if _RICH_AVAILABLE and is_tty and not _a11y_plain_mode():
        body = _RichText()
        _append_dashboard_rich_section(body, "Summary", summary_lines)
        _append_dashboard_rich_section(body, "Details", detail_lines, title_style="bold white")
        _append_dashboard_rich_section(body, "Actions", action_lines, title_style="bold yellow")
        _RICH_CONSOLE.print(
            _RichPanel(body, title=f"[bold]{title}[/]", border_style=border_style, padding=(0, 1))
        )
        return
    lines = [title, *(_dashboard_section_lines("Summary", summary_lines))]
    detail_block = _dashboard_section_lines("Details", detail_lines)
    if detail_block:
        lines.extend(["", *detail_block])
    action_block = _dashboard_section_lines("Actions", action_lines)
    if action_block:
        lines.extend(["", *action_block])
    print("\n".join(lines))


def _dedupe_preserve_order(lines: "list[str]") -> list[str]:
    """Return non-empty lines without duplicates, preserving first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        text = str(line or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _print_predictive_affordances(
    hints: "list[str]",
    *,
    title: str = "Next steps",
    border_style: str = "dim",
) -> None:
    """Render a compact, fallback-safe next-step menu."""
    clean = _dedupe_preserve_order(hints)[:4]
    if not clean:
        return
    is_tty = _get_is_tty()
    if _RICH_AVAILABLE and is_tty and not _a11y_plain_mode():
        body = _RichText()
        for hint in clean:
            body.append("  • ", style="dim")
            body.append(f"{hint}\n")
        _RICH_CONSOLE.print(
            _RichPanel(body, title=f"[bold]{title}[/]", border_style=border_style, padding=(0, 1))
        )
        return
    print(f"{title}:")
    for hint in clean:
        print(f"  - {hint}")


def _build_error_recovery_hints(msg: str, *, session_id: str = "") -> list[str]:
    """Return recovery-oriented affordances for common CLI failures."""
    lower = str(msg or "").lower()
    hints: list[str] = []
    if any(token in lower for token in ("refused", "unreachable", "timed out", "resolve", "unable to reach")):
        hints.extend(
            [
                "openclaw health to verify the local server",
                "openclaw status to confirm URL, token source, and recent session state",
            ]
        )
    if any(token in lower for token in ("401", "unauthorized", "forbidden", "token")):
        hints.extend(
            [
                "openclaw auth login to refresh the saved token",
                "openclaw status to confirm which token source is active",
            ]
        )
    if "usage:" in lower:
        hints.append("/help or /palette <term> to find the expected command shape")
    if session_id:
        hints.extend(
            [
                "/retry to resend the last request",
                "/context to inspect the grounding for the next request",
            ]
        )
    elif not hints:
        hints.append("/help to browse the available command surface")
    return _dedupe_preserve_order(hints)


def _print_meta_footer(*pairs: tuple[str, str]) -> None:
    """Print dim label + value metadata lines after a command (e.g. session id, saved path)."""
    if not pairs:
        return
    print()
    if _RICH_AVAILABLE and _IS_TTY:
        for label, value in pairs:
            _RICH_CONSOLE.print(f"  [dim]{label}:[/]  [dim]{value}[/]")
    else:
        for label, value in pairs:
            print(f"  {_DM}{label}:{_R}  {value}")


def _print_error(msg: str, *, file: object = None) -> None:
    """Print a standardized error message with color when available."""
    import sys as _sys
    dest = file if file is not None else _sys.stdout
    if _RICH_AVAILABLE and _IS_TTY and dest is _sys.stderr:
        _RICH_CONSOLE.print(f"[bold red]error:[/] {msg}", stderr=True)
    elif _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(f"[bold red]error:[/] {msg}")
    else:
        print(f"{_BRE}error:{_R} {msg}", file=dest)


def _risk_label(risk_level: Any) -> str:
    """Return a normalized approval risk label."""
    return str(getattr(risk_level, "value", risk_level) or "").strip().upper() or "UNKNOWN"


def _print_feedback(message: str, *, level: str = "info", detail: str = "") -> None:
    """Print a compact feedback line with accessible emphasis."""
    level_key = str(level or "info").strip().lower()
    plain = _a11y_plain_mode()
    is_tty = _get_is_tty()
    icon_map = {
        "success": ("✓", "[done]"),
        "warn": ("⚠", "[warn]"),
        "info": ("ℹ", "[info]"),
    }
    rich_style = {"success": "green", "warn": "bold yellow", "info": "cyan"}.get(level_key, "cyan")
    ansi_style = {
        "success": _theme_ansi() or _BGR,
        "warn": _BRE if _a11y_high_contrast() else _BYE,
        "info": _theme_ansi() or _BCY,
    }.get(level_key, _theme_ansi() or _BCY)
    icon, fallback = icon_map.get(level_key, icon_map["info"])
    label = fallback if plain else icon
    if _RICH_AVAILABLE and is_tty and not plain:
        suffix = f" [dim]{detail}[/]" if detail else ""
        _RICH_CONSOLE.print(f"[{rich_style}]{label}[/] {message}{suffix}")
    elif is_tty and not plain:
        suffix = f" {_DM}{detail}{_R}" if detail else ""
        print(f"{ansi_style}{label}{_R} {message}{suffix}")
    else:
        suffix = f" ({detail})" if detail else ""
        print(f"{label} {message}{suffix}")


def _print_risky_action_warning(*, action: str, target: str, risk_level: Any, recovery_hint: str = "") -> None:
    """Print an accessible pre-approval emphasis block for risky actions."""
    risk = _risk_label(risk_level)
    if risk not in {"HIGH", "CRITICAL"}:
        return
    impact = (
        "destructive side effects are possible — verify the exact target before approving."
        if risk == "CRITICAL"
        else "this action can change project or system state — review the target before approving."
    )
    if recovery_hint:
        impact = f"{impact} Recovery: {recovery_hint}"
    _print_feedback(f"Review carefully: {target}", level="warn", detail=f"{action} · {risk.lower()} risk")
    width = max(60, min(shutil.get_terminal_size((88, 24)).columns - 4, 100))
    print(
        textwrap.fill(
            impact,
            width=width,
            initial_indent="    ",
            subsequent_indent="    ",
        )
    )


def _print_shell_result(result: Any) -> None:
    """Print a shell execution result with colored exit code and dim output blocks."""
    if _RICH_AVAILABLE and _IS_TTY:
        exit_ok = result.returncode == 0
        exit_style = "green" if exit_ok else "bold red"
        exit_icon = "\u2713" if exit_ok else "\u2717"
        _RICH_CONSOLE.print(f"[dim]$[/] {result.command}  [dim]\u00b7  cwd:[/] {result.cwd}  [{exit_style}]{exit_icon} exit {result.returncode}[/]")
        if result.stdout.strip():
            _RICH_CONSOLE.print(f"[dim]\u2500\u2500\u2500 stdout \u2500\u2500\u2500[/]\n[dim]{result.stdout.rstrip()}[/]")
        if result.stderr.strip():
            _RICH_CONSOLE.print(f"[dim]\u2500\u2500\u2500 stderr \u2500\u2500\u2500[/]\n[dim red]{result.stderr.rstrip()}[/]")
    else:
        from openclaw_cli_actions import format_shell_result
        print(format_shell_result(result))


def _print_file_edit_result(result: Any) -> None:
    """Print a file edit result with colored summary and diff when available."""
    if _RICH_AVAILABLE and _IS_TTY:
        icon = "✓" if result.changed else "—"
        style = "green" if result.changed else "dim"
        _RICH_CONSOLE.print(f"[{style}]{icon}[/] {result.summary}  [dim]{result.path}[/]")
        if result.diff:
            _RICH_CONSOLE.print("[dim]─── diff ───[/]")
            for line in result.diff.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    _RICH_CONSOLE.print(f"[green]{line}[/]")
                elif line.startswith("-") and not line.startswith("---"):
                    _RICH_CONSOLE.print(f"[red]{line}[/]")
                else:
                    _RICH_CONSOLE.print(f"[dim]{line}[/]")
    else:
        from openclaw_cli_actions import preview_file_result
        print(preview_file_result(result))


def _with_spinner(label: str, fn: Any, *args: Any, output_json: bool = False, **kwargs: Any) -> Any:
    return _ui_utils_mod._with_spinner(label, fn, *args, output_json=output_json, _override_is_tty=_IS_TTY, _override_heartbeat_secs=_SPINNER_HEARTBEAT_SECONDS, **kwargs)


# AskResponse, LocalLinkValidation, CliConfig — moved to openclaw_cli_types; imported at top of file.

# HealthResponse — moved to openclaw_cli_health; imported at top of file.

# ReplRouteStepContext, ReplRouteGrounding — imported from openclaw_cli_router above.


def normalize_base_url(raw_url: str | None) -> str:
    """Normalize a user-provided base URL and remove trailing slashes."""
    value = str(raw_url or "").strip() or DEFAULT_BASE_URL
    return value.rstrip("/")


def default_client_name() -> str:
    """Return a human-readable client name for telemetry and headers."""
    return (
        os.getenv("OPENCLAW_CLIENT_NAME")
        or socket.gethostname()
        or platform.node()
        or "openclaw-cli"
    ).strip()


def default_user_name() -> str:
    """Return the logical user label sent to the ask API."""
    configured = (os.getenv("OPENCLAW_USER_NAME") or "").strip()
    if configured:
        return configured
    user = getpass.getuser().strip() or "cli"
    client_name = default_client_name()
    return f"{user}@{client_name}"


def read_saved_token(*, path: Path | None = None) -> str:
    """Read a token from the fallback credential file when present."""
    target = path or auth_storage_path()
    if not target.exists():
        return ""
    try:
        return target.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OpenClawCliError(f"Unable to read stored OpenClaw token from {target}: {exc}") from exc


def write_saved_token(token: str, *, path: Path | None = None) -> Path:
    """Persist a token to the fallback credential file."""
    value = str(token).strip()
    if not value:
        raise OpenClawCliError("OpenClaw token cannot be empty.")
    target = path or auth_storage_path()
    tmp_target = target.with_name(f".{target.name}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target.write_text(f"{value}\n", encoding="utf-8")
        if os.name != "nt":
            tmp_target.chmod(0o600)
        os.replace(tmp_target, target)
        if os.name != "nt":
            target.chmod(0o600)
    except OSError as exc:
        raise OpenClawCliError(f"Unable to store OpenClaw token in {target}: {exc}") from exc
    return target


def delete_saved_token(*, path: Path | None = None) -> bool:
    """Delete the fallback credential file when present."""
    target = path or auth_storage_path()
    if not target.exists():
        return False
    try:
        target.unlink()
    except OSError as exc:
        raise OpenClawCliError(f"Unable to remove stored OpenClaw token from {target}: {exc}") from exc
    return True


def resolve_token_details(explicit_token: str | None = None) -> TokenResolution:
    """Resolve a token and describe where it came from."""
    value = str(explicit_token or "").strip()
    if value:
        return TokenResolution(token=value, source="command line flag --token")

    for env_name in ("OPENCLAW_TOKEN", "DASHBOARD_API_TOKEN"):
        env_value = str(os.getenv(env_name) or "").strip()
        if env_value:
            return TokenResolution(token=env_value, source=f"environment variable {env_name}")

    keychain_token = read_keychain_token()
    if keychain_token:
        return TokenResolution(token=keychain_token, source=f"macOS Keychain '{KEYCHAIN_SERVICE}'")

    saved_token = read_saved_token()
    if saved_token:
        return TokenResolution(token=saved_token, source=f"credential file {auth_storage_path()}")

    return TokenResolution(token="", source="")


def resolve_token(explicit_token: str | None = None) -> str:
    """Resolve a token from CLI arg, env vars, keychain, or fallback file store."""
    return resolve_token_details(explicit_token).token


def auth_setup_hint(*, platform_name: str | None = None) -> str:
    """Return platform-aware guidance for configuring CLI authentication."""
    if (platform_name or sys.platform) == "darwin":
        return (
            f"Set {TOKEN_ENV_VARS}, store a token in macOS Keychain under "
            f"'{KEYCHAIN_SERVICE}', run `openclaw auth login`, or pass --token."
        )
    return f"Set {TOKEN_ENV_VARS}, run `openclaw auth login`, or pass --token."


def build_headers(*, token: str, client_name: str) -> dict[str, str]:
    """Build HTTP headers for OpenClaw API requests."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"OpenClawCLI/1.0 ({client_name})",
        "X-OpenClaw-Client": client_name,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def attach_session_header(headers: dict[str, str], *, session_id: str) -> dict[str, str]:
    """Attach the CLI session header when a session is active."""
    updated = dict(headers)
    if session_id:
        updated["X-OpenClaw-Session-ID"] = session_id
    return updated


def parse_prompt(prompt_parts: list[str]) -> str:
    """Resolve a prompt from args or stdin for pipeline-friendly use."""
    joined = " ".join(prompt_parts).strip()
    if joined:
        return joined
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def ensure_cli_session(
    session_id: str,
    *,
    title: str,
    cwd: str | None = None,
    files: list[str] | None = None,
    plan_id: str = "",
    task_id: str = "",
) -> SessionSummary:
    """Load an existing session or create a new one when needed."""
    existing_id = str(session_id or "").strip()
    if existing_id:
        session = load_session(existing_id)
        if session is None:
            raise OpenClawCliError(f"Session '{existing_id}' was not found.")
        return session
    return create_session(title=title, cwd=cwd, files=files or [], plan_id=plan_id, task_id=task_id)


def bind_config_to_session(config: CliConfig, session_id: str) -> CliConfig:
    """Return a copy of the CLI config scoped to the given session."""
    return CliConfig(
        base_url=config.base_url,
        token=config.token,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        user_name=config.user_name,
        client_name=config.client_name,
        output_json=config.output_json,
        session_id=session_id,
    )


def summarize_session(session: SessionSummary) -> str:
    """Render a compact single-session summary for terminal output."""
    return _session_utils_mod.summarize_session(session, _age_label_fn=_session_age_label)


def _print_session_summary(session: SessionSummary) -> None:
    """Print a compact session summary, with rich formatting when available."""
    return _session_display_mod._print_session_summary(session)


def _print_session_list(items: list[SessionSummary]) -> None:
    """Print a session list table, with rich formatting when available."""
    if not items:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]No OpenClaw CLI sessions have been recorded yet.[/]")
        else:
            print("No OpenClaw CLI sessions have been recorded yet.")
        return
    if _RICH_AVAILABLE and _IS_TTY:
        table = _RichTable(border_style="dim", show_edge=True, pad_edge=True, header_style="bold cyan")
        table.add_column("Session ID", style="dim", no_wrap=True)
        table.add_column("Title", style="bold")
        table.add_column("Updated", style="yellow", no_wrap=True)
        table.add_column("Cmds", justify="right", style="cyan")
        table.add_column("Outputs", justify="right", style="cyan")
        table.add_column("Mood", style="dim")
        table.add_column("Mode", style="dim")
        for s in items:
            mood = _session_mood_snapshot(s)
            table.add_row(
                s.session_id,
                s.title or "—",
                s.updated_at or "—",
                str(s.command_count),
                str(s.output_count),
                str(mood.get("label") or "—"),
                s.automation_mode or "—",
            )
        _RICH_CONSOLE.print(table)
    else:
        print(format_session_list(items))


def _format_collaboration_entry(entry: dict[str, Any]) -> str:
    actor = str(entry.get("actor") or "operator").strip()
    summary = str(entry.get("summary") or entry.get("content") or "").strip()
    tags = [str(tag or "").strip() for tag in list(entry.get("tags") or []) if str(tag or "").strip()]
    suffix = f" [{' '.join('#' + tag for tag in tags)}]" if tags else ""
    return f"{actor}: {summary}{suffix}".strip()




def _session_mood_snapshot(
    session: SessionSummary,
    *,
    watch_state: dict[str, Any] | None = None,
    collaboration_snapshot: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Derive a restrained mood/momentum cue from objective session state."""
    return _session_display_mod._session_mood_snapshot(
        session,
        watch_state=watch_state,
        collaboration_snapshot=collaboration_snapshot,
    )


def _session_mood_cell(snapshot: dict[str, str], *, rich: bool = False) -> str:
    """Render a compact mood/momentum cell with text-first fallback."""
    label = str(snapshot.get("label") or "").strip()
    detail = str(snapshot.get("detail") or "").strip()
    if not label:
        return ""
    value = label if not detail else f"{label} · {detail}"
    return _progress_cell("mood", value, status=str(snapshot.get("status") or "info"), rich=rich)


def _session_preview_lines(session: SessionSummary) -> list[str]:
    return _session_utils_mod._session_preview_lines(session)


def _session_operator_snapshot(
    session: SessionSummary,
    *,
    watch_state: dict[str, Any] | None = None,
    collaboration_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a read-only operator snapshot for monitoring and handoff surfaces."""
    return _session_display_mod._session_operator_snapshot(
        session,
        watch_state=watch_state,
        collaboration_snapshot=collaboration_snapshot,
    )


def _operator_snapshot_lines(snapshot: dict[str, Any]) -> list[str]:
    return _health_mod._operator_snapshot_lines(snapshot)


def _acknowledged_alert_ids() -> set[str]:
    raw = _PREFS.setdefault("acknowledged_alerts", [])
    if not isinstance(raw, list):
        raw = []
        _PREFS["acknowledged_alerts"] = raw
    return {str(item).strip() for item in raw if str(item).strip()}


def _set_acknowledged_alert_ids(values: set[str]) -> None:
    _PREFS["acknowledged_alerts"] = sorted(values)
    _save_prefs()


def _collect_operator_alerts() -> list[dict[str, Any]]:
    return _session_utils_mod._collect_operator_alerts()


def _print_automation_dashboard() -> None:
    sessions = list_sessions(limit=50)
    active_sessions = 0
    live_watches = 0
    pending_interventions = 0
    ready_handoffs = 0
    open_incidents = 0
    for session in sessions:
        watch_state = load_watch_state(session.session_id) or {}
        snapshot = build_collaboration_snapshot(session.session_id, limit=5)
        operator = _session_operator_snapshot(session, watch_state=watch_state, collaboration_snapshot=snapshot)
        if not _session_is_stale(session):
            active_sessions += 1
        if str((watch_state or {}).get("status") or "").strip().lower() in {"running", "active", "retrying"}:
            live_watches += 1
        pending_interventions += len([item for item in list((watch_state or {}).get("interventions") or []) if isinstance(item, dict) and str(item.get("status") or "").strip().lower() == "pending"])
        if str(operator.get("readiness_label") or "").strip() == "handoff-ready":
            ready_handoffs += 1
        open_incidents += len([item for item in list(snapshot.get("open_incidents") or []) if isinstance(item, dict)])
    alerts = _collect_operator_alerts()
    print("Automation dashboard")
    print("--------------------")
    print(f"  Active sessions:       {active_sessions}")
    print(f"  Live watches:          {live_watches}")
    print(f"  Pending interventions: {pending_interventions}")
    print(f"  Handoff-ready:         {ready_handoffs}")
    print(f"  Open incidents:        {open_incidents}")
    print(f"  Alerts:                {len(alerts)}")
    if alerts:
        print("  Top alerts:")
        for alert in alerts[:5]:
            print(f"    - [{str(alert.get('severity') or 'info').upper()}] {str(alert.get('title') or '')} · {str(alert.get('message') or '')}")


def _build_session_share_text(session_id: str) -> str:
    return _session_display_mod._build_session_share_text(session_id)


_RUNBOOK_TEMPLATES: dict[str, dict[str, Any]] = {
    "operator": {
        "label": "Operator Runbook",
        "audience": "CLI operator handoff",
        "sections": ("summary", "milestones", "decisions", "timeline", "outputs", "commands"),
    },
    "stakeholder": {
        "label": "Stakeholder Update",
        "audience": "status recap for non-operators",
        "sections": ("summary", "milestones", "outputs", "commands"),
    },
    "postmortem": {
        "label": "Postmortem Draft",
        "audience": "incident recap and follow-up review",
        "sections": ("summary", "decisions", "timeline", "outputs", "commands"),
    },
}


def _resolve_runbook_template(name: str) -> tuple[str, dict[str, Any]] | None:
    token = str(name or "operator").strip().lower()
    if not token:
        token = "operator"
    template = _RUNBOOK_TEMPLATES.get(token)
    if template is None:
        return None
    return token, template


def _build_session_runbook_text(session_id: str, *, template_name: str = "operator") -> str:
    return _session_display_mod._build_session_runbook_text(session_id, template_name=template_name)


def _cmd_exporttemplates(ctx: ChatCommandContext) -> str:
    """/exporttemplates [list|show <name>] — inspect built-in runbook/export templates."""
    return _core_cmd_mod._cmd_exporttemplates(ctx)


def _cmd_runbook(ctx: ChatCommandContext) -> str:
    """/runbook [template] [save <path>] — render a long-form session runbook."""
    return _core_cmd_mod._cmd_runbook(ctx)


def inspect_session(session_id: str) -> str:
    """Render a human-readable inspection view of a persisted session."""
    return _session_display_mod.inspect_session(session_id)


def _inspect_session_rich(
    session_id: str,
    session_data: dict[str, Any],
    events: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    watch: dict[str, Any],
    routed_checkpoints: list[dict[str, Any]],
) -> None:
    """Print a rich-formatted session inspection view."""
    return _session_display_mod._inspect_session_rich(
        session_id, session_data, events, outputs, watch, routed_checkpoints
    )


def format_session_list(items: list[SessionSummary]) -> str:
    """Render a recent-session table as plain text."""
    if not items:
        return "No OpenClaw CLI sessions have been recorded yet."
    rows = ["SESSION ID | UPDATED | MODE | COMMANDS | OUTPUTS | MOOD | TITLE", "-" * 132]
    for session in items:
        mood = _session_mood_snapshot(session)
        rows.append(
            f"{session.session_id} | {session.updated_at} | {session.automation_mode or '-'} | {session.command_count} | "
            f"{session.output_count} | {str(mood.get('label') or '-')} | {session.title}"
        )
    return "\n".join(rows)


def utc_timestamp() -> str:
    """Return a UTC timestamp for watch-mode state updates."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc_timestamp(raw_value: Any) -> datetime | None:
    """Parse an ISO8601 timestamp used by persisted CLI/session state."""
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_seconds(started_at: Any, finished_at: Any | None = None) -> float | None:
    start_dt = _parse_utc_timestamp(started_at)
    if start_dt is None:
        return None
    end_dt = _parse_utc_timestamp(finished_at) if finished_at else datetime.now(timezone.utc)
    if end_dt is None:
        end_dt = datetime.now(timezone.utc)
    return max(0.0, (end_dt - start_dt).total_seconds())


def _format_elapsed_compact(seconds: Any) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "0s"
    if value < 1:
        return f"{value:.1f}s"
    if value < 60:
        return f"{value:.1f}s" if value < 10 else f"{value:.0f}s"
    minutes, rem = divmod(int(round(value)), 60)
    if minutes < 60:
        return f"{minutes}m {rem}s" if rem else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _session_age_label(session: SessionSummary) -> str:
    """Return a compact age label for a persisted session."""
    age_seconds = _elapsed_seconds(session.created_at)
    if age_seconds is None:
        return "unknown"
    return _format_elapsed_compact(age_seconds)








def _truncate_preview(text: str, *, max_chars: int) -> str:
    clipped = str(text or "").strip()
    if len(clipped) <= max_chars:
        return clipped
    return clipped[: max_chars - 15].rstrip() + "\n...[truncated]..."


def _single_line_excerpt(text: str, *, max_chars: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def _preview_block_lines(title: str, text: str, *, max_chars: int, max_lines: int = 3) -> list[str]:
    preview = _truncate_preview(text, max_chars=max_chars)
    if not preview:
        return []
    lines = preview.splitlines()[:max_lines]
    block = [f"{title}:"]
    block.extend(f"  {line}" for line in lines if line.strip())
    return block


def _format_byte_count(size_bytes: int) -> str:
    size = float(max(0, int(size_bytes or 0)))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{int(size)} B"


def _candidate_workspace_roots(cwd: str | None = None) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    seeds = [str(cwd or "").strip(), str(Path.cwd())]
    for seed in seeds:
        if not seed:
            continue
        resolved = Path(seed).expanduser().resolve()
        for candidate in (resolved, *resolved.parents):
            marker = str(candidate)
            if marker in seen:
                continue
            seen.add(marker)
            roots.append(candidate)
    return roots


def _resolve_local_source(path_text: str, *, cwd: str | None = None) -> Path:
    candidate = Path(path_text).expanduser()
    if candidate.is_absolute():
        return candidate
    base = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()
    return (base / candidate).resolve()


def _find_local_plan_dir(*, cwd: str | None = None) -> Path | None:
    candidates: list[Path] = []
    env_path = str(os.getenv("PLANS_DIR") or "").strip()
    if env_path:
        candidates.append(_resolve_local_source(env_path, cwd=cwd))
    candidates.extend(root / "data" / "plans" for root in _candidate_workspace_roots(cwd))
    seen: set[str] = set()
    for candidate in candidates:
        marker = str(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        if candidate.is_dir():
            return candidate
    return None


def _find_local_tasks_file(*, cwd: str | None = None) -> Path | None:
    candidates: list[Path] = []
    env_path = str(os.getenv("MC_TASKS_FILE") or "").strip()
    if env_path:
        candidates.append(_resolve_local_source(env_path, cwd=cwd))
    candidates.extend(root / "data" / "tasks.json" for root in _candidate_workspace_roots(cwd))
    seen: set[str] = set()
    for candidate in candidates:
        marker = str(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        if candidate.is_file():
            return candidate
    return None


def _read_plan_goal_from_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# Plan:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _validate_plan_id_local(plan_id: str, *, cwd: str | None = None) -> LocalLinkValidation:
    normalized = str(plan_id or "").strip()
    if not normalized:
        return LocalLinkValidation(kind="plan", item_id="", available=False)
    plan_dir = _find_local_plan_dir(cwd=cwd)
    if plan_dir is None:
        return LocalLinkValidation(kind="plan", item_id=normalized, available=False)
    plan_path = plan_dir / f"{normalized}.md"
    if not plan_path.is_file():
        return LocalLinkValidation(
            kind="plan",
            item_id=normalized,
            available=True,
            exists=False,
            source=str(plan_dir),
        )
    return LocalLinkValidation(
        kind="plan",
        item_id=normalized,
        available=True,
        exists=True,
        source=str(plan_path),
        summary=_read_plan_goal_from_file(plan_path),
    )


def _validate_task_id_local(task_id: str, *, cwd: str | None = None) -> LocalLinkValidation:
    normalized = str(task_id or "").strip()
    if not normalized:
        return LocalLinkValidation(kind="task", item_id="", available=False)
    tasks_file = _find_local_tasks_file(cwd=cwd)
    if tasks_file is None:
        return LocalLinkValidation(kind="task", item_id=normalized, available=False)
    try:
        payload = json.loads(tasks_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LocalLinkValidation(kind="task", item_id=normalized, available=False, source=str(tasks_file))
    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    if not isinstance(tasks, list):
        return LocalLinkValidation(kind="task", item_id=normalized, available=False, source=str(tasks_file))
    task = next((item for item in tasks if isinstance(item, dict) and str(item.get("id") or "").strip() == normalized), None)
    if task is None:
        return LocalLinkValidation(
            kind="task",
            item_id=normalized,
            available=True,
            exists=False,
            source=str(tasks_file),
        )
    title = str(task.get("title") or "").strip()
    status = str(task.get("status") or "").strip()
    summary_parts = [part for part in (title, f"status={status}" if status else "") if part]
    return LocalLinkValidation(
        kind="task",
        item_id=normalized,
        available=True,
        exists=True,
        source=str(tasks_file),
        summary="; ".join(summary_parts),
    )


def _load_task_record(task_id: str, *, cwd: str | None = None) -> dict[str, Any] | None:
    normalized = str(task_id or "").strip()
    if not normalized:
        return None
    tasks_file = _find_local_tasks_file(cwd=cwd)
    if tasks_file is None:
        return None
    try:
        payload = json.loads(tasks_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    if not isinstance(tasks, list):
        return None
    return next(
        (
            item
            for item in tasks
            if isinstance(item, dict) and str(item.get("id") or "").strip() == normalized
        ),
        None,
    )


# _load_route_plan, _normalize_route_step_context, _active_plan_step,
# _find_plan_step_context, _load_repl_route_grounding — moved to openclaw_cli_router.


def _link_validation_suffix(result: LocalLinkValidation) -> str:
    if not result.item_id:
        return ""
    if not result.available:
        return " (validation unavailable)"
    if result.exists:
        return " (confirmed locally)"
    return " (warning: not found locally)"


def _render_effective_grounding_preview(
    session: SessionSummary,
    *,
    max_chars: int = CONTEXT_PREVIEW_MAX_CHARS,
) -> str:
    _, workspace_context = collect_workspace_context(
        cwd=session.cwd or None,
        targets=list(session.files),
        max_chars=max_chars,
    )
    sections: list[str] = []
    plan_context = _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd)
    if plan_context:
        sections.extend(["Plan/task framing:", plan_context])
    if workspace_context:
        sections.extend(["Workspace context:", workspace_context])
    prior_outputs = recent_output_context(session.session_id, max_chars=max(750, min(1_500, max_chars // 3)))
    if prior_outputs:
        sections.extend(["Recent session outputs:", prior_outputs])
    return _truncate_preview("\n\n".join(section for section in sections if section), max_chars=max_chars)
























def _plan_task_context_snippet(plan_id: str, task_id: str, *, cwd: str | None = None) -> str:
    """Return a concise plan/task framing block for LLM prompts, or '' if neither is set."""
    plan_id = str(plan_id or "").strip()
    task_id = str(task_id or "").strip()
    if not plan_id and not task_id:
        return ""
    lines = ["Active work context:"]
    if plan_id:
        lines.append(f"  Plan: {plan_id}")
        plan_validation = _validate_plan_id_local(plan_id, cwd=cwd)
        if plan_validation.exists and plan_validation.summary:
            lines.append(f"  Plan goal: {plan_validation.summary}")
    if task_id:
        lines.append(f"  Task: {task_id}")
        task_validation = _validate_task_id_local(task_id, cwd=cwd)
        if task_validation.exists and task_validation.summary:
            lines.append(f"  Task detail: {task_validation.summary}")
    lines.append("Keep your response aligned with this plan/task framing.")
    return "\n".join(lines)


def build_analysis_prompt(
    *,
    goal: str,
    context_text: str,
    session: SessionSummary,
) -> str:
    """Build an ask payload for file/directory analysis."""
    prompt_sections = [
        "You are OpenClaw operating in terminal analysis mode.",
        f"Session ID: {session.session_id}",
        f"Goal: {goal}",
        _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd),
        f"Working directory: {session.cwd}",
        "Use the workspace context below to ground your answer. "
        "Focus on findings, risks, and concrete next steps.",
        "Workspace context:",
        context_text,
    ]
    prior_outputs = recent_output_context(session.session_id)
    if prior_outputs:
        prompt_sections.extend(["Recent session outputs:", prior_outputs])
    return "\n\n".join(section for section in prompt_sections if section)


def build_write_prompt(
    *,
    task: str,
    context_text: str,
    session: SessionSummary,
    title: str,
) -> str:
    """Build a writing-oriented ask payload."""
    prompt_sections = [
        "You are OpenClaw operating in document-writing mode.",
        f"Session ID: {session.session_id}",
        f"Document title: {title}",
        f"Writing task: {task}",
        _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd),
        "Return clean markdown suitable for saving to a file. "
        "Prefer clear headings and direct prose.",
        "Workspace context:",
        context_text,
    ]
    prior_outputs = recent_output_context(session.session_id)
    if prior_outputs:
        prompt_sections.extend(["Recent session outputs:", prior_outputs])
    return "\n\n".join(section for section in prompt_sections if section)


def persist_response(session_id: str, prompt: str, response: str) -> None:
    """Persist a prompt/response turn into the local CLI session store."""
    append_event(session_id, kind="user", content=prompt, metadata={"summary": prompt})
    append_event(session_id, kind="assistant", content=response, metadata={"summary": response})


def invoke_openclaw(
    prompt: str,
    *,
    config: CliConfig,
    history: list[dict[str, str]] | None = None,
    opener: Any = request.urlopen,
) -> AskResponse:
    """Submit a prompt to the OpenClaw ask API."""
    payload = {
        "prompt": prompt,
        "model": config.model,
        "history": history or [],
        "user_name": config.user_name,
    }
    req = request.Request(
        f"{config.base_url}/api/agent/ask",
        data=json.dumps(payload).encode("utf-8"),
        headers=attach_session_header(
            build_headers(token=config.token, client_name=config.client_name),
            session_id=config.session_id,
        ),
        method="POST",
    )
    try:
        with opener(req, timeout=config.timeout_seconds) as resp:
            body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenClawCliError(format_http_error(config.base_url, exc.code, detail)) from exc
    except error.URLError as exc:
        raise OpenClawCliError(format_url_error(config.base_url, exc)) from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OpenClawCliError("OpenClaw returned invalid JSON.") from exc

    if not isinstance(data, dict):
        raise OpenClawCliError("OpenClaw returned an unexpected response payload.")

    return AskResponse(
        response=str(data.get("response") or "").strip(),
        model=str(data.get("model") or config.model),
        tokens=int(data.get("tokens") or 0),
        raw=data,
    )


def fetch_health(
    *,
    config: CliConfig,
    opener: Any = request.urlopen,
) -> HealthResponse:
    """Fetch the OpenClaw health endpoint."""
    req = request.Request(
        f"{config.base_url}/health",
        headers=attach_session_header(
            build_headers(token=config.token, client_name=config.client_name),
            session_id=config.session_id,
        ),
        method="GET",
    )
    try:
        with opener(req, timeout=config.timeout_seconds) as resp:
            body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenClawCliError(format_http_error(config.base_url, exc.code, detail)) from exc
    except error.URLError as exc:
        raise OpenClawCliError(format_url_error(config.base_url, exc)) from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = body.strip()
    status, healthy = analyze_health_payload(payload)
    return HealthResponse(payload=payload, raw_text=body, status=status, healthy=healthy)


def format_http_error(base_url: str, status_code: int, detail: str) -> str:
    """Format actionable HTTP failures from the OpenClaw API."""
    message = detail.strip()
    if message:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("error"):
            message = str(payload["error"]).strip()
    if status_code == 401:
        return (
            f"OpenClaw rejected the request with 401 Unauthorized. "
            f"{auth_setup_hint()} for {base_url}."
        )
    if not message:
        message = f"HTTP {status_code}"
    return f"OpenClaw request failed ({status_code}): {message}"


def format_url_error(base_url: str, exc: error.URLError) -> str:
    """Format actionable network failures for OpenClaw endpoints."""
    reason = str(getattr(exc, "reason", exc) or "").lower()
    if "timed out" in reason:
        return (
            f"Timed out while contacting OpenClaw at {base_url}. "
            "Check that the service is running and reachable from this machine."
        )
    if "connection refused" in reason or "actively refused" in reason:
        return (
            f"OpenClaw at {base_url} refused the connection. "
            "Check that the service is running and that the URL/port are correct."
        )
    if (
        "nodename nor servname provided" in reason
        or "name or service not known" in reason
        or "getaddrinfo failed" in reason
    ):
        return f"Unable to resolve the OpenClaw host in {base_url}. Check OPENCLAW_URL or pass --url."
    return f"Unable to reach OpenClaw at {base_url}. Set OPENCLAW_URL or pass --url."


def analyze_health_payload(payload: Any) -> tuple[str, bool | None]:
    """Best-effort classification of the /health payload."""
    if isinstance(payload, dict):
        status = str(payload.get("status") or "").strip().lower()
        if status in {"healthy", "ok", "pass"}:
            return status, True
        if status in {"degraded", "down", "fail", "failing", "unhealthy", "error"}:
            return status, False
        checks = payload.get("checks")
        if isinstance(checks, dict):
            normalized = " ".join(str(value).lower() for value in checks.values())
            if any(bad in normalized for bad in ("down", "fail", "unhealthy", "missing")):
                return status or "degraded", False
        return status, None
    if isinstance(payload, str):
        text = payload.strip().lower()
        if not text:
            return "", None
        if text in {"healthy", "ok", "pass"} or "healthy" in text:
            return text, True
        if any(bad in text for bad in ("down", "fail", "unhealthy", "degraded", "error")):
            return text, False
        return text, None
    return "", None


def _terminal_width(*, fallback: int = 80) -> int:
    """Return current terminal width, with a sensible fallback."""
    try:
        return os.get_terminal_size().columns
    except OSError:
        return fallback


_SEPARATOR_STYLES: dict[str, list[str]] = {
    "gradient": ["▓▒░  ▓▒░  ▓▒░", "░▓▒  ░▓▒  ░▓▒", "▒░▓  ▒░▓  ▒░▓"],
    "pulse":    ["─────────────", "━━━━━━━━━━━━━", "═══════════════"],
    "dots":     ["· · · · · · ·", "• • • • • • •", "○ ○ ○ ○ ○ ○ ○"],
    "wave":     ["~-~-~-~-~-~-~", "-~-~-~-~-~-~-", "~-~-~-~-~-~-~"],
    "none":     [],
}


def _separator_fill(width: int, *, high_contrast: bool | None = None) -> str:
    """Return a separator line sized for the current terminal/mode."""
    return _exec_separator_fill(
        width,
        high_contrast=_a11y_high_contrast() if high_contrast is None else high_contrast,
        plain_mode=_a11y_plain_mode(),
    )


def _print_response_separator(*, label: str = "", detail: str = "", status: str = "info") -> None:
    """Print an adaptive response separator."""
    if _a11y_plain_mode():
        if label:
            suffix = f" ({detail})" if detail else ""
            print(f"\n{label}:{suffix}")
        else:
            print()
        return
    cols = _terminal_width()
    sep_width = min(max(8, cols - 2), 60)
    status_label = _status_emoji(status)
    display_label = " ".join(part for part in [status_label, label] if part).strip() or label
    if detail and cols >= 96:
        display_label = f"{display_label} · {detail}".strip()
    if _RICH_AVAILABLE and _get_is_tty():
        from rich.rule import Rule as _RichRule

        _RICH_CONSOLE.print(_RichRule(display_label if cols >= 72 else "", style=_theme_style()))
        _motion_pause("separator")
    else:
        line = _separator_fill(sep_width)
        if label and cols >= 72:
            line = f"{line} {display_label} {line}"
            line = line[:sep_width]
        print(f"{_theme_ansi()}{line}{_R}")


def _print_animated_separator() -> None:
    """Print a short animated separator after an AI response."""
    if _a11y_plain_mode() or _a11y_reduced_motion():
        return
    style = _PREFS.get("separator_style", "gradient")
    frames = _SEPARATOR_STYLES.get(style, [])
    if not frames:
        return
    is_tty = _get_is_tty()
    if not is_tty:
        return

    width = 40
    color = _DM
    for frame in frames:
        line = (frame * (width // len(frame) + 1))[:width]
        sys.stdout.write(f"\r{color}{line}{_R}")
        sys.stdout.flush()
        time.sleep(0.08)
    sys.stdout.write(f"\r{' ' * width}\r")  # clear
    sys.stdout.flush()
    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"[dim]{'─' * 42}[/]")
    else:
        print(f"{_DM}{'─' * 42}{_R}")


def _render_table_ansi(rows: list[list[str]]) -> list[str]:
    """Shim — delegates to openclaw_cli_render; honours monkeypatched _terminal_width."""
    from dataclasses import replace as _dc_replace
    return _render_mod._render_table_ansi(rows, _dc_replace(_make_render_ctx(), cols=_terminal_width()))


def _inject_heading_emojis(text: str) -> str:
    """Prepend emoji to markdown headings based on level."""
    if not _PREFS.get("emoji_headers", True) or _a11y_plain_mode():
        return text
    lines = text.split("\n")
    result = []
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("#"):
            m = re.match(r'^(#{1,4}) (.+)$', line)
            if m:
                level = len(m.group(1))
                emoji = _HEADING_EMOJIS.get(level, "")
                if emoji:
                    line = f"{m.group(1)} {emoji} {m.group(2)}"
        result.append(line)
    return "\n".join(result)


_URL_PATTERN = re.compile(r'(https?://[^\s\)\]\>\"\']+)', re.IGNORECASE)


def _make_clickable_link(url: str, text: str = "") -> str:
    """Return an OSC 8 clickable hyperlink if supported, otherwise plain URL."""
    return _path_utils._make_clickable_link(url, text, prefs=_PREFS, is_tty=_get_is_tty())


def _linkify_response(text: str) -> str:
    """Replace bare URLs in response text with OSC 8 clickable links."""
    return _path_utils._linkify_response(text, prefs=_PREFS, is_tty=_get_is_tty())


def _is_kv_bullet_group(lines: list[str]) -> bool:
    return _preprocess_mod._is_kv_bullet_group(lines)


def _bullet_group_to_table(lines: list[str]) -> list[str]:
    return _preprocess_mod._bullet_group_to_table(lines)


def _unwrap_code_block_tables(text: str) -> str:
    return _preprocess_mod._unwrap_code_block_tables(text)


def _convert_bullet_tables(text: str) -> str:
    return _preprocess_mod._convert_bullet_tables(text)


def _colorize_json(text: str) -> str:
    return _preprocess_mod._colorize_json(text)


def _detect_and_format_json(text: str) -> str:
    return _preprocess_mod._detect_and_format_json(text)


def _preprocess_response_text(text: str) -> tuple[str, str | None]:
    return _preprocess_mod._preprocess_response_text(text)


def _auto_bold_response(text: str) -> str:
    return _preprocess_mod._auto_bold_response(text)


# ---------------------------------------------------------------------------
# Smart markdown table renderer — handles wide tables gracefully
# (implementation in openclaw_cli_preprocess; constants re-imported above)
# ---------------------------------------------------------------------------


def _strip_inline_md(text: str) -> str:
    return _preprocess_mod._strip_inline_md(text)


def _render_md_table_rich(headers: list[str], rows: list[list[str]]) -> None:
    return _preprocess_mod._render_md_table_rich(headers, rows)


def _clean_sources_for_display(sources: str) -> list[tuple[str, str]]:
    return _health_mod._clean_sources_for_display(sources)


def _render_body_with_tables(body: str) -> None:
    """Render response body, using a smart Rich Table for any markdown table blocks."""
    last_end = 0
    for m in _MD_TABLE_BLOCK.finditer(body):
        pre = body[last_end : m.start()].strip()
        if pre:
            _RICH_CONSOLE.print(_RichMarkdown(pre))
        parsed = _parse_md_table(m.group(0))
        if parsed:
            _render_md_table_rich(*parsed)
        else:
            _RICH_CONSOLE.print(_RichMarkdown(m.group(0)))
        last_end = m.end()
    remaining = body[last_end:].strip()
    if remaining:
        _RICH_CONSOLE.print(_RichMarkdown(remaining))


def _make_render_ctx(is_tty: bool | None = None, high_contrast: bool | None = None) -> "_render_mod.RenderContext":
    """Build a RenderContext from current module globals — called at render time."""
    try:
        from rich.rule import Rule as _Rule
    except ImportError:
        _Rule = None
    _is_tty = _get_is_tty() if is_tty is None else is_tty
    _hc = _a11y_high_contrast() if high_contrast is None else high_contrast
    return _render_mod.RenderContext(
        is_tty=_is_tty,
        is_rich=_RICH_AVAILABLE,
        high_contrast=_hc,
        plain_mode=_a11y_plain_mode(),
        cols=shutil.get_terminal_size((80, 24)).columns,
        theme_ansi=_theme_ansi(),
        prefs=_PREFS,  # pass by reference — monkeypatches on _PREFS work transparently
        console=globals().get("_RICH_CONSOLE"),
        Panel=globals().get("_RichPanel"),
        Text=globals().get("_RichText"),
        Rule=_Rule,
        Table=globals().get("_RichTable"),
        Markdown=globals().get("_RichMarkdown"),
    )


def _render_response_body(
    text: str,
    sources: str | None,
    is_tty: bool,
    high_contrast: bool,
) -> None:
    """Render the main response body — delegated to openclaw_cli_render."""
    text = re.sub(
        r"\n{0,2}(?:\*\*Sources\*\*|Sources):?\s*\n(?:(?:[-\*]|\d+\.)\s+.+\n?)+",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    ).rstrip()
    _render_mod._render_response_body(text, sources, _make_render_ctx(is_tty, high_contrast))


def _render_response_footer(
    model: "str | None",
    tokens: "int | None",
    elapsed: float,
    is_tty: bool,
    high_contrast: bool,
) -> None:
    """Render the timing/model footer — delegated to openclaw_cli_render."""
    _render_mod._render_response_footer(model, tokens, elapsed, _make_render_ctx(is_tty, high_contrast))


def print_response(response: AskResponse, *, output_json: bool, elapsed: float = 0.0) -> None:
    """Render a response to stdout."""
    if output_json:
        print(json.dumps(response.raw, indent=2, sort_keys=True))
        return

    # Re-check TTY at call time — module-level _IS_TTY can be False in some
    # terminal emulators (tmux, iTerm, etc.) even during an interactive session.
    is_tty = _get_is_tty()
    if _a11y_plain_mode():
        is_tty = False  # force plain-text path; skip Rich rendering
    high_contrast = _a11y_high_contrast()

    if response.response:
        body, sources = _preprocess_response_text(response.response)
        body = _auto_bold_response(body)
        body = _detect_and_format_json(body)
        body = _inject_heading_emojis(body)
        _render_response_body(body, sources, is_tty, high_contrast)

    _render_response_footer(response.model, response.tokens, elapsed, is_tty, high_contrast)


def print_health(response: HealthResponse, *, output_json: bool) -> None:
    return _health_mod.print_health(response, output_json=output_json)


def maybe_warn_missing_token(config: CliConfig) -> None:
    """Warn before interactive or one-shot usage when auth is not configured."""
    if config.token:
        return
    print(
        "warning: no OpenClaw API token is configured; requests will likely fail with 401 Unauthorized. "
        + auth_setup_hint(),
        file=sys.stderr,
    )


# ReplRouteKind, ReplPlanStep, ReplRouteDecision, routing constants and all routing
# functions (_normalize_prompt_text through _extract_created_plan_id) — moved to
# openclaw_cli_router and imported at top of file.


def route_repl_prompt(
    prompt: str,
    *,
    classifier_func: Callable[[str], "ReplRouteDecision | None"] = lightweight_classify_repl_prompt,
    min_confidence: float = REPL_ROUTE_AUTO_THRESHOLD,
    session_id: str = "",
    session: "SessionSummary | None" = None,
) -> "ReplRouteDecision":
    """Decide how a freeform REPL prompt should be handled."""
    return _router_route_repl_prompt(
        prompt,
        classifier_func=classifier_func,
        min_confidence=min_confidence,
        session_id=session_id,
        session=session,
        validate_plan_fn=_validate_plan_id_local,
    )


def _summarize_terminal_result(text: str, *, fallback: str) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return fallback
    if len(compact) <= 180:
        return compact
    return compact[:179].rstrip() + "…"


def _load_plan_storage_helpers() -> tuple[Any, Any]:
    try:
        from agent_loop import load_plan, save_plan
    except ImportError:
        return None, None
    return load_plan, save_plan


def _create_persisted_plan(
    *,
    goal: str,
    steps: tuple[ReplPlanStep, ...] = (),
    steps_text: str = "",
    session_id: str = "",
) -> tuple[str, str]:
    try:
        from agent_loop import create_plan
    except ImportError as exc:
        raise OpenClawCliError(missing_feature_hint("openclaw plan")) from exc

    step_commands = [
        line.strip()
        for line in (
            steps_text.strip().splitlines()
            if steps_text.strip()
            else [_plan_step_slash_command(step) for step in steps]
        )
        if line.strip()
    ]
    create_result = str(run_async(create_plan(goal, steps_text="\n".join(step_commands))))
    plan_id = _extract_created_plan_id(create_result)
    if not plan_id:
        raise OpenClawCliError(create_result or "Unable to determine the created plan id.")

    if session_id:
        session = update_session(session_id, plan_id=plan_id)
        load_plan, save_plan = _load_plan_storage_helpers()
        if load_plan is not None and save_plan is not None:
            plan = load_plan(plan_id)
            if plan is not None:
                plan.context["session_id"] = session.session_id
                plan.context["cwd"] = session.cwd
                if session.files:
                    plan.context["files"] = "\n".join(session.files[:20])
                save_plan(plan)
        append_event(
            session_id,
            kind="plan",
            content=goal,
            metadata={
                "plan_id": plan_id,
                "summary": f"created plan {plan_id}",
                "steps": step_commands,
                "source": "repl.autoroute" if steps else "cli.plan",
            },
        )
    return plan_id, create_result


def _update_persisted_plan_step(
    plan_id: str,
    step_num: int,
    *,
    status: str,
    output: str = "",
    session_id: str = "",
) -> None:
    load_plan, save_plan = _load_plan_storage_helpers()
    if load_plan is None or save_plan is None or not plan_id:
        return
    plan = load_plan(plan_id)
    if plan is None:
        return
    step = next((item for item in plan.steps if getattr(item, "num", 0) == step_num), None)
    if step is None:
        return
    step.status = status
    if output:
        step.output = str(output)[:2_000]
        if status == "done":
            plan.context[f"step_{step_num}_output"] = str(output)[:2_000]
    if session_id:
        plan.context["session_id"] = session_id
    if status == "failed":
        plan.status = "interrupted"
    elif all(getattr(item, "is_complete", False) for item in plan.steps):
        plan.status = "completed"
    else:
        plan.status = "in-progress"
    save_plan(plan)


def _execute_routed_plan(
    *,
    prompt: str,
    decision: ReplRouteDecision,
    registry: "ChatCommandRegistry",
    ctx: "ChatCommandContext",
) -> str:
    session = _require_session_or_warn(ctx)
    if session is None:
        return ""

    plan_id, create_result = _create_persisted_plan(goal=prompt, steps=decision.steps, session_id=session.session_id)
    total = len(decision.steps)
    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(_RichPanel(f"📋 Plan [yellow]{plan_id}[/] · [dim]{total} steps[/]", border_style="dim"))
    else:
        print(f"plan {plan_id}: {total} steps")
    ctx.history[:] = load_conversation_history(session.session_id)
    for step in decision.steps:
        slash_command = _plan_step_slash_command(step)
        if not slash_command:
            summary = f"step {step.index} has no executable slash command"
            _update_persisted_plan_step(plan_id, step.index, status="failed", output=summary, session_id=session.session_id)
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"  [dim][{step.index}/{total}][/] [red]✗ failed:[/] {summary}")
            else:
                print(f"[{step.index}/{total}] failed: {summary}")
            return _CMD_CONTINUE
        _update_persisted_plan_step(plan_id, step.index, status="in-progress", session_id=session.session_id)
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"  [dim][{step.index}/{total}][/] [cyan]{slash_command}[/]")
        else:
            print(f"[{step.index}/{total}] {slash_command}")
        routed = registry.dispatch(slash_command, ctx)
        ctx.history[:] = load_conversation_history(session.session_id)
        summary = ctx.command_summary or _summarize_terminal_result(
            slash_command,
            fallback=f"step {step.index} complete",
        )
        if routed == _CMD_QUIT:
            _update_persisted_plan_step(plan_id, step.index, status="failed", output="execution aborted", session_id=session.session_id)
            return _CMD_QUIT
        if routed is None or not ctx.command_ok:
            _update_persisted_plan_step(plan_id, step.index, status="failed", output=summary, session_id=session.session_id)
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"  [dim][{step.index}/{total}][/] [red]✗ failed:[/] {summary}")
            else:
                print(f"[{step.index}/{total}] failed: {summary}")
            return _CMD_CONTINUE
        _update_persisted_plan_step(plan_id, step.index, status="done", output=summary, session_id=session.session_id)
    update_session(session.session_id, plan_id=plan_id)
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# Slash-command dispatcher
# ---------------------------------------------------------------------------

_CMD_CONTINUE: str = "continue"  # sentinel: command handled, keep the REPL loop
_CMD_QUIT: str = "quit"           # sentinel: command handled, exit the REPL
_SYSTEM_PROMPT_MAX = 2000


# ChatCommandContext, SlashCommand, ChatCommandRegistry — moved to openclaw_cli_types; imported at top of file.


def _routed_plan_metadata(ctx: ChatCommandContext) -> dict[str, Any]:
    metadata = ctx.route_metadata if isinstance(ctx.route_metadata, dict) else {}
    if str(metadata.get("source") or "").strip().lower() != "repl.plan":
        return {}
    return metadata


def _routed_plan_step_label(metadata: dict[str, Any]) -> str:
    step_index = int(metadata.get("step_index") or 0)
    step_total = int(metadata.get("step_total") or 0)
    if step_index > 0 and step_total > 0:
        return f"routed plan step {step_index}/{step_total}"
    return "routed plan step"


def _capture_routed_action_checkpoint(
    ctx: ChatCommandContext,
    *,
    session: SessionSummary,
    action_kind: str,
    target: str,
    detail: str,
    file_paths: list[str] | None = None,
) -> bool:
    metadata = _routed_plan_metadata(ctx)
    if not metadata:
        return True
    workspace_targets = list(file_paths or session.files)
    try:
        checkpoint = create_routed_action_checkpoint(
            session.session_id,
            action_kind=action_kind,
            target=target,
            detail=detail,
            cwd=session.cwd,
            route_metadata=metadata,
            file_paths=file_paths,
            workspace_signature=build_workspace_signature(
                cwd=session.cwd or None,
                targets=workspace_targets or None,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.error("safety checkpoint capture failed", exc_info=True)
        _print_error(f"unable to capture safety checkpoint for {_routed_plan_step_label(metadata)}: {exc}")
        _set_command_result(ctx, ok=False, summary=f"checkpoint failed: {exc}")
        return False
    return True


def _cmd_quit(ctx: ChatCommandContext) -> str:
    return _CMD_QUIT


def _cmd_help(ctx: ChatCommandContext) -> str:
    return _core_cmd_mod._cmd_help(ctx)


def _set_command_result(ctx: ChatCommandContext, *, ok: bool, summary: str = "") -> None:
    ctx.command_ok = ok
    ctx.command_summary = str(summary or "").strip()


def _cmd_clear(ctx: ChatCommandContext) -> str:
    return _core_cmd_mod._cmd_clear(ctx)


# ---------------------------------------------------------------------------
# Session / context inspection and mutation commands
# ---------------------------------------------------------------------------

def _require_session_or_warn(ctx: ChatCommandContext) -> "SessionSummary | None":
    """Load the active session, printing a warning when none is set."""
    if not ctx.session_id:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[yellow]⚠[/]  no active session  [dim]·  openclaw --session <id>  or  openclaw session create[/]")
        else:
            print("No active session. Start with: openclaw --session <id> or openclaw session create")
        _set_command_result(ctx, ok=False, summary="no active session")
        return None
    session = load_session(ctx.session_id)
    if session is None:
        _print_error(f"session '{ctx.session_id}' not found")
        _set_command_result(ctx, ok=False, summary=f"session '{ctx.session_id}' not found")
        return None
    return session


def _cmd_session(ctx: ChatCommandContext) -> str:
    """/session — show a compact summary of the current session."""
    return _cmd_session_mod._cmd_session(ctx)


def _cmd_context(ctx: ChatCommandContext) -> str:
    """/context — show the effective local grounding for the active session."""
    return _core_cmd_mod._cmd_context(ctx)


def _cmd_cwd(ctx: ChatCommandContext) -> str:
    """/cwd [path] — show or switch the session working directory."""
    return _core_cmd_mod._cmd_cwd(ctx)


def _cmd_files(ctx: ChatCommandContext) -> str:
    """/files [add <path> | rm <path>] — list, add, or remove tracked files."""
    return _core_cmd_mod._cmd_files(ctx)


# ---------------------------------------------------------------------------
# Wave 12: Watch status helpers + /watch REPL command
# ---------------------------------------------------------------------------





def _cmd_watch(ctx: ChatCommandContext) -> str:
    """/watch [status|history|retry-limit N|intervene TEXT] — inspect or control an active watch session."""
    return _workflow_cmd_mod._cmd_watch(ctx)


def _cmd_plan(ctx: ChatCommandContext) -> str:
    """/plan [<id> | status | focus | unlink] — show, link, focus, or unlink a plan for this session."""
    return _workflow_cmd_mod._cmd_plan(ctx)


def _cmd_task(ctx: ChatCommandContext) -> str:
    """/task [<id> | unlink] — show, link, or unlink a task for this session."""
    return _workflow_cmd_mod._cmd_task(ctx)


def _cmd_events(ctx: ChatCommandContext) -> str:
    """/events [n|decisions [n]] — show the last n events; 'decisions' filters to routing/decision kinds."""
    return _cmd_session_mod._cmd_events(ctx)


def _last_trace_snapshot(session_id: str) -> dict[str, Any] | None:
    last_ev = get_last_decision_event(session_id)
    if last_ev is None:
        return None
    kind = str(last_ev.get("kind") or "").strip()
    meta = last_ev.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    content = str(last_ev.get("content") or "").strip()
    ts = str(last_ev.get("timestamp") or last_ev.get("at") or last_ev.get("created_at") or "").strip()

    slash_cmd = meta.get("slash_command") or ""
    rationale = meta.get("rationale") or content[:200] or "(no rationale recorded)"
    target_text = meta.get("target_text") or ""
    args_text = meta.get("args_text") or ""

    raw_conf = meta.get("confidence")
    try:
        confidence = float(raw_conf) if raw_conf is not None else None
    except (ValueError, TypeError):
        confidence = None

    if confidence is not None and confidence >= 0.80:
        conf_label = f"{confidence:.2f} (HIGH)"
        conf_color = "green"
        border_style = "green"
    elif confidence is not None and confidence >= 0.50:
        conf_label = f"{confidence:.2f} (MEDIUM)"
        conf_color = "yellow"
        border_style = "yellow"
    elif confidence is not None:
        conf_label = f"{confidence:.2f} (LOW)"
        conf_color = "red"
        border_style = "red"
    else:
        conf_label = "(unknown)"
        conf_color = "dim"
        border_style = "dim"

    ratings = _PREFS.get("ratings", [])
    latest_rating = ratings[-1] if ratings else None
    latest_rating_label = ""
    if isinstance(latest_rating, dict):
        latest_rating_label = (
            f"{latest_rating.get('score', latest_rating.get('rating', '?'))}/5"
            f" ({latest_rating.get('label', 'rated')})"
        )

    return {
        "kind": kind,
        "meta": meta,
        "content": content,
        "ts": ts,
        "slash_cmd": slash_cmd,
        "rationale": rationale,
        "target_text": target_text,
        "args_text": args_text,
        "conf_label": conf_label,
        "conf_color": conf_color,
        "border_style": border_style,
        "what_happened": f"{kind}" + (f" → /{slash_cmd}" if slash_cmd else (f" — {content[:60]}" if content else "")),
        "latest_rating": latest_rating_label,
        "rating_count": len(ratings),
    }


def _route_quality_summary() -> list[dict[str, Any]]:
    ratings = list(_PREFS.get("ratings") or [])
    buckets: dict[str, dict[str, Any]] = {}
    for item in ratings:
        if not isinstance(item, dict):
            continue
        route = str(item.get("route") or item.get("slash_command") or "").strip().lstrip("/")
        if not route:
            continue
        try:
            score = int(item.get("score", item.get("rating", 0)))
        except (TypeError, ValueError):
            continue
        entry = buckets.setdefault(route, {"route": route, "scores": [], "count": 0})
        entry["scores"].append(score)
        entry["count"] = int(entry.get("count") or 0) + 1
    rows: list[dict[str, Any]] = []
    for route, entry in buckets.items():
        scores = list(entry.get("scores") or [])
        if not scores:
            continue
        avg = sum(scores) / len(scores)
        rows.append(
            {
                "route": route,
                "count": len(scores),
                "avg": avg,
                "high_rate": int(sum(1 for score in scores if score >= 4) / max(1, len(scores)) * 100),
            }
        )
    rows.sort(key=lambda item: (float(item.get("avg") or 0.0), int(item.get("count") or 0), str(item.get("route") or "")), reverse=True)
    return rows


def _cmd_routing(ctx: ChatCommandContext) -> str:
    """/routing [suggest|analyze] — inspect learned routing hints from past ratings."""
    return _core_cmd_mod._cmd_routing(ctx)


def _cmd_why(ctx: ChatCommandContext) -> str:
    """/why — explain the last routing or tool decision from session history."""
    return _core_cmd_mod._cmd_why(ctx)


def _cmd_trace(ctx: ChatCommandContext) -> str:
    """/trace — show the latest routing trace plus the current quality context."""
    return _core_cmd_mod._cmd_trace(ctx)


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


def _cmd_collab(ctx: ChatCommandContext) -> str:
    """/collab [status|share|note|decision|assign] — collaboration notes, decisions, assignments, and handoff summaries."""
    return _content_cmd_mod._cmd_collab(ctx)


def _risk_entries(session_id: str) -> list[dict[str, Any]]:
    snapshot = build_collaboration_snapshot(session_id, limit=25)
    return [item for item in list(snapshot.get("open_risks") or []) if isinstance(item, dict)]


def _handoff_check_snapshot(session_id: str) -> dict[str, Any]:
    session = require_session(session_id)
    snapshot = build_collaboration_snapshot(session_id, limit=10)
    open_risks = [item for item in list(snapshot.get("open_risks") or []) if isinstance(item, dict)]
    open_incidents = [item for item in list(snapshot.get("open_incidents") or []) if isinstance(item, dict)]
    assignments = [item for item in list(snapshot.get("assignments") or []) if isinstance(item, dict)]
    latest_handoff = snapshot.get("latest_handoff") or {}
    watch_state = load_watch_state(session_id) or {}
    checks: list[tuple[str, bool, str]] = []
    checks.append(("plan", bool(session.plan_id), str(session.plan_id or "link a plan with /plan <id>")))
    checks.append(("task", bool(session.task_id), str(session.task_id or "link a task with /task <id>")))
    checks.append(("owner", bool(assignments), str(assignments[0].get("assignee") or assignments[0].get("actor") if assignments else "record ownership with /collab assign @actor TEXT")))
    checks.append(("handoff", bool(latest_handoff), str(latest_handoff.get("id") or "create one with /handoff create")))
    watch_status = str(watch_state.get("status") or "").strip().lower()
    checks.append(("watch", watch_status not in {"running", "active"}, watch_status or "idle"))
    checks.append(("incidents", not open_incidents, "resolve incidents with /incident resolve <index>" if open_incidents else "none"))
    readiness = "ready"
    if open_risks or open_incidents:
        readiness = "blocked"
    elif not all(ok for _, ok, _ in checks[:3]):
        readiness = "needs-attention"
    return {
        "readiness": readiness,
        "checks": checks,
        "open_risks": open_risks,
        "open_incidents": open_incidents,
        "assignments": assignments,
    }


def _cmd_risk(ctx: ChatCommandContext) -> str:
    """/risk [list|add LEVEL TEXT|clear INDEX] — track blocking risks for handoffs."""
    return _workflow_cmd_mod._cmd_risk(ctx)


def _incident_entries(session_id: str) -> list[dict[str, Any]]:
    snapshot = build_collaboration_snapshot(session_id, limit=25)
    return [item for item in list(snapshot.get("open_incidents") or []) if isinstance(item, dict)]


def _cmd_incident(ctx: ChatCommandContext) -> str:
    """/incident [list|log TEXT|resolve INDEX] — track operator incidents for the current session."""
    return _workflow_cmd_mod._cmd_incident(ctx)


def _cmd_search(ctx: ChatCommandContext) -> str:
    """/search [--all] <query> — full-text search across session event content."""
    return _content_cmd_mod._cmd_search(ctx)


def _cmd_autoroute(ctx: ChatCommandContext) -> str:
    """/autoroute [on|off] — show or set session-level REPL auto-routing."""
    return _core_cmd_mod._cmd_autoroute(ctx)


def _cmd_outputs(ctx: ChatCommandContext) -> str:
    """/outputs [<index>|<filename>|promote <index> <name>] — list or preview saved outputs."""
    return _content_cmd_mod._cmd_outputs(ctx)


def _cmd_snapshot(ctx: ChatCommandContext) -> str:
    """/snapshot [name] — save current git HEAD as a named restore point."""
    return _core_cmd_mod._cmd_snapshot(ctx)


def _cmd_rollback(ctx: ChatCommandContext) -> str:
    """/rollback [last|list|<name>] — restore latest checkpoint, list git snapshots, or preview/exec a git snapshot rollback."""
    return _core_cmd_mod._cmd_rollback(ctx)




# ---------------------------------------------------------------------------
# Action delegation slash commands
# ---------------------------------------------------------------------------

def _require_config_or_warn(ctx: ChatCommandContext) -> "CliConfig | None":
    """Return ctx.config, printing a warning when it is absent."""
    if ctx.config is None:
        _print_error("this command requires an active config. Start with: openclaw --session <id>")
        _set_command_result(ctx, ok=False, summary="missing active config")
        return None
    return ctx.config


def _cmd_analyze(ctx: ChatCommandContext) -> str:
    """/analyze <goal> — run an analysis using the current session context."""
    return _core_cmd_mod._cmd_analyze(ctx)


def _cmd_research(ctx: ChatCommandContext) -> str:
    """/research <query> — run the research agent using the current session context."""
    return _core_cmd_mod._cmd_research(ctx)


def _cmd_write(ctx: ChatCommandContext) -> str:
    """/write <task> — generate a markdown document using the current session context."""
    return _core_cmd_mod._cmd_write(ctx)


def _progress_bar(current: int, total: int, width: int = 30, label: str = "") -> str:
    """Return a colored ANSI progress bar string."""
    return _exec_progress_bar(current, total, width, label)


def _exec_progress_animate(proc: Any, label: str = "") -> tuple:
    """Animate an indeterminate progress bar while proc runs. Returns (stdout, stderr, returncode)."""
    return _exec_animate_fn(
        proc,
        label,
        is_tty=_get_is_tty(),
        plain_mode=_a11y_plain_mode(),
        reduced_motion=_a11y_reduced_motion(),
    )


def _analyze_exec_error(cmd: str, stderr: str, returncode: int) -> "list[str]":
    """Analyze a failed command and return smart recovery hints."""
    return _exec_analyze_exec_error(cmd, stderr, returncode)


def _print_exec_error_hints(cmd: str, stderr: str, returncode: int) -> None:
    """Print smart recovery hints after a failed exec command."""
    _exec_print_exec_error_hints(
        cmd,
        stderr,
        returncode,
        plain_mode=_a11y_plain_mode(),
        is_tty=_get_is_tty(),
    )


def _cmd_exec(ctx: ChatCommandContext) -> str:
    """/exec [--] <command> — run a shell command with session tracking and approval."""
    return _core_cmd_mod._cmd_exec(ctx)


def _cmd_edit(ctx: ChatCommandContext) -> str:
    """/edit <path> [--content <text> | --append <text> | --replace OLD NEW] — inspect or write a file."""
    return _core_cmd_mod._cmd_edit(ctx)


def _cmd_update(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/update — self-upgrade openclaw via pip without leaving the REPL."""
    return _core_cmd_mod._cmd_update(ctx)


def _cmd_version(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/version — show the running CLI version and build stamp."""
    return _core_cmd_mod._cmd_version(ctx)


def _estimate_token_count(value: object) -> int:
    """Estimate token count using the shared rough character heuristic."""
    return max(0, len(str(value or "")) // 4)


def _history_token_breakdown(history: list[dict[str, object]]) -> dict[str, object]:
    """Summarize estimated token usage for the current session history."""
    roles: dict[str, dict[str, int]] = {}
    total_chars = 0
    total_tokens = 0
    total_messages = len(history)
    for message in history:
        role = str(message.get("role") or "unknown").strip().lower() or "unknown"
        content = str(message.get("content") or "")
        chars = len(content)
        tokens = _estimate_token_count(content)
        total_chars += chars
        total_tokens += tokens
        bucket = roles.setdefault(role, {"messages": 0, "chars": 0, "tokens": 0})
        bucket["messages"] += 1
        bucket["chars"] += chars
        bucket["tokens"] += tokens
    ordered_roles = sorted(roles.items(), key=lambda item: (-item[1]["tokens"], item[0]))
    return {
        "total_chars": total_chars,
        "total_tokens": total_tokens,
        "total_messages": total_messages,
        "roles": ordered_roles,
    }


def _cmd_tokeninfo(ctx: "ChatCommandContext") -> str:
    """/tokeninfo — show estimated token usage for this session."""
    return _core_cmd_mod._cmd_tokeninfo(ctx)


def _print_theme_preview(theme_name: str, *, persisted: bool) -> None:
    """Print a compact theme preview without requiring Rich."""
    _settings_cmd_mod._print_theme_preview(theme_name, persisted=persisted)


def _cycle_theme(direction: str) -> None:
    """Advance the stored theme forward or backward through the palette."""
    _settings_cmd_mod._cycle_theme(direction)


def _cmd_theme(ctx: ChatCommandContext) -> str:
    """Handler for /theme — display or set the UI colour theme."""
    return _settings_cmd_mod._cmd_theme(ctx)


def _cmd_overlay(ctx: ChatCommandContext) -> str:
    """/overlay [on|off|status] — manage opt-in interactive overlays."""
    return _settings_cmd_mod._cmd_overlay(ctx)


def _cmd_colorscheme(ctx: ChatCommandContext) -> str:
    """/colorscheme [name|list|reset] — view or set the extended color scheme."""
    return _settings_cmd_mod._cmd_colorscheme(ctx)


def _cmd_emojiheaders(ctx: ChatCommandContext) -> str:
    """/emojiheaders [on|off] — toggle emoji prefixes on AI response headings."""
    return _settings_cmd_mod._cmd_emojiheaders(ctx)


def _cmd_emoji(ctx: ChatCommandContext) -> str:
    """Handler for /emoji — toggle emoji display on or off."""
    return _settings_cmd_mod._cmd_emoji(ctx)


def _cmd_layout(ctx: ChatCommandContext) -> str:
    """Handler for /layout — switch density or render preset workspaces."""
    return _settings_cmd_mod._cmd_layout(ctx)


def _cmd_draft(ctx: ChatCommandContext) -> str:
    """Handler for /draft — save, load, clear, or restore a draft prompt."""
    return _core_cmd_mod._cmd_draft(ctx)


def _cmd_template(ctx: ChatCommandContext) -> str:
    """Handler for /template — manage reusable prompt templates."""
    return _core_cmd_mod._cmd_template(ctx)


def _session_badges(s: "SessionSummary") -> str:
    """Build a compact badge string for a session summary row."""
    parts: list[str] = []
    parts.append(_status_cell(s.status or "active", rich=_RICH_AVAILABLE and _IS_TTY))
    if s.automation_mode:
        parts.append(_progress_cell("auto", s.automation_mode, status=s.automation_status or "active", rich=_RICH_AVAILABLE and _IS_TTY))
    if _session_is_stale(s):
        parts.append(_status_cell("stale", rich=_RICH_AVAILABLE and _IS_TTY))
    elif s.updated_at:
        parts.append(_status_cell("info", detail="fresh", rich=_RICH_AVAILABLE and _IS_TTY))
    if (s.output_count or 0) > 0:
        parts.append(_progress_cell("outputs", str(s.output_count), status="complete", rich=_RICH_AVAILABLE and _IS_TTY))
    if (s.checkpoint_count or 0) > 0:
        parts.append(_progress_cell("ckpt", str(s.checkpoint_count), status="complete", rich=_RICH_AVAILABLE and _IS_TTY))
    mood_cell = _session_mood_cell(_session_mood_snapshot(s), rich=_RICH_AVAILABLE and _IS_TTY)
    if mood_cell:
        parts.append(mood_cell)
    if getattr(s, "tags", []):
        parts.append(" ".join(f"#{t}" for t in s.tags[:3]))
    return "  ".join(parts)


def _session_is_stale(s: "SessionSummary", days: int = 7) -> bool:
    try:
        updated = datetime.fromisoformat(s.updated_at.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - updated
        return age.days >= days
    except Exception:  # noqa: BLE001  # optional staleness check; safe to return False
        return False


def _cmd_sessions(ctx: ChatCommandContext) -> str:
    """/sessions [search QUERY | related] — browse recent sessions."""
    return _cmd_session_mod._cmd_sessions(ctx)


def _cmd_export(ctx: ChatCommandContext) -> str:
    """/export [md|json|txt] [filename] — export session history to a file."""
    return _cmd_session_mod._cmd_export(ctx)


def _cmd_stats(ctx: ChatCommandContext) -> str:
    """/stats — show aggregate usage statistics across all sessions."""
    return _content_cmd_mod._cmd_stats(ctx)


def _cmd_tag(ctx: ChatCommandContext) -> str:
    """/tag [add <tag>|rm <tag>|list] — manage tags on the current session."""
    return _cmd_session_mod._cmd_tag(ctx)


def _cmd_bookmark(ctx: ChatCommandContext) -> str:
    """/bookmark [label] — save a replay bookmark for the current session."""
    return _cmd_session_mod._cmd_bookmark(ctx)


def _cmd_bookmarks(ctx: ChatCommandContext) -> str:
    """/bookmarks — list replay bookmarks for the current session."""
    return _cmd_session_mod._cmd_bookmarks(ctx)


def _cmd_resume(ctx: ChatCommandContext) -> str:
    """/resume [last] — print resume instructions for the most recent other session."""
    return _cmd_session_mod._cmd_resume(ctx)


def _cmd_replay(ctx: ChatCommandContext) -> str:
    """/replay [session-id] [--from bookmark] — re-print the current or a past session's conversation."""
    return _cmd_session_mod._cmd_replay(ctx)


def _cmd_handoff(ctx: ChatCommandContext) -> str:
    """/handoff [create|list|open NAME|note TEXT|check] — save/restore a resumable workspace handoff."""
    return _cmd_session_mod._cmd_handoff(ctx)


def _print_workspace_capsule(capsule: dict[str, Any], *, title: str = "Workspace Capsule") -> None:
    _ui_utils_mod._print_workspace_capsule(capsule, title=title)
def _cmd_workspace(ctx: ChatCommandContext) -> str:
    """/workspace [status|save|list|restore NAME] — manage workspace recovery capsules."""
    return _workflow_cmd_mod._cmd_workspace(ctx)


def _print_macro_progress(steps: list, current_idx: int, done_indices: set) -> None:
    """Print a live macro step progress tracker."""
    _macros_mod._print_macro_progress(steps, current_idx, done_indices, a11y_plain=_a11y_plain_mode())


def _workflow_store() -> dict[str, list[str]]:
    return _macros_mod._workflow_store(_PREFS)


def _pattern_store() -> dict[str, dict[str, Any]]:
    patterns = _PREFS.setdefault("patterns", {})
    if not isinstance(patterns, dict):
        patterns = {}
        _PREFS["patterns"] = patterns
    return patterns


def _history_command_texts(limit: int) -> list[str]:
    return _macros_mod._history_command_texts(_PREFS, limit)


def _render_workflow_step(command: str, ctx: ChatCommandContext) -> str:
    return _macros_mod._render_workflow_step(command, ctx)


def _print_workflow_preview(name: str, steps: list[str], ctx: ChatCommandContext) -> None:
    _macros_mod._print_workflow_preview(name, steps, ctx)


def _run_command_sequence(ctx: ChatCommandContext, name: str, commands: list[str], *, kind: str = "macro") -> str:
    """Execute a named macro/workflow/pattern command sequence."""
    if not commands:
        _print_error(f"{kind.title()} '{name}' is empty")
        return _CMD_CONTINUE

    is_tty = _get_is_tty()
    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"[dim]▶ Running {kind} '[bold cyan]{name}[/]' ({len(commands)} commands)[/]")
    else:
        print(f"▶ Running {kind} '{name}' ({len(commands)} commands)")

    registry = build_chat_command_registry()
    done_set: set = set()
    for i, cmd in enumerate(commands):
        _print_macro_progress(commands, i, done_set)
        rendered = _render_workflow_step(str(cmd), ctx)

        if rendered.startswith("/"):
            parts = rendered[1:].split(None, 1)
            cmd_name = parts[0].lower()
            cmd_args = parts[1] if len(parts) > 1 else ""
            slash_cmd = registry._lookup.get(cmd_name)
            if slash_cmd is not None:
                sub_ctx = ChatCommandContext(
                    history=list(ctx.history),
                    session_id=ctx.session_id,
                    args=cmd_args,
                )
                slash_cmd.handler(sub_ctx)
            else:
                _print_error(f"Unknown command in {kind}: {rendered}")
        else:
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[dim yellow]  ⚠ Skipped (natural language — run manually): {rendered}[/]")
            else:
                print(f"  ⚠ Skipped (natural language): {rendered}")

        done_set.add(i)

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"[green]✓ {kind.title()} '{name}' complete[/]")
    else:
        print(f"✓ {kind.title()} '{name}' complete")
    return _CMD_CONTINUE


def _macro_run(ctx: ChatCommandContext, name: str, *, kind: str = "macro") -> str:
    """Execute a named macro/workflow's commands in sequence."""
    macros = _workflow_store()
    if name not in macros:
        _print_error(f"{kind.title()} '{name}' not found")
        return _CMD_CONTINUE
    return _run_command_sequence(ctx, name, list(macros[name]), kind=kind)


def _pattern_steps(entry: dict[str, Any]) -> list[str]:
    steps = entry.get("commands") or []
    return [str(step) for step in steps if str(step or "").strip()]


def _cmd_pattern(ctx: "ChatCommandContext") -> str:
    """/pattern — manage reusable workflow patterns backed by history or workflows."""
    return _content_cmd_mod._cmd_pattern(ctx)


def _cmd_inject(ctx: "ChatCommandContext") -> str:
    """/inject — inject file or URL content as context prefix for the next message."""
    return _core_cmd_mod._cmd_inject(ctx)


def _cmd_system(ctx: ChatCommandContext) -> str:
    """View or set a persistent system prompt prefix for all AI messages."""
    return _system_cmd_mod._cmd_system(ctx)


def _cmd_promptdebug(ctx: ChatCommandContext) -> str:
    """/promptdebug — preview what would be sent to the AI for the next message."""
    return _system_cmd_mod._cmd_promptdebug(ctx)


def _handle_simple_toggle_pref(
    ctx: "ChatCommandContext",
    key: str,
    label: str,
    default: bool = True,
    note: str = "",
) -> str:
    """Shared on/off toggle handler for simple boolean preference commands."""
    val = (ctx.args or "").strip().lower()
    is_tty = _get_is_tty()
    if val in ("on", "off"):
        _prefs_set(key, val == "on")
        state = "on" if _PREFS[key] else "off"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓[/] {label} [bold]{state}[/]")
        else:
            print(f"✓ {label} {state}")
    else:
        state = "on" if _PREFS.get(key, default) else "off"
        suffix = f" — {note}" if note else ""
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{label} is [bold]{state}[/]{suffix}[/]")
        else:
            print(f"{label} is {state}{suffix}")
    return _CMD_CONTINUE


def _cmd_autobold(ctx: ChatCommandContext) -> str:
    """/autobold [on|off] — toggle automatic bolding of numbers and filenames in responses."""
    return _system_cmd_mod._cmd_autobold(ctx)


def _cmd_jsonformat(ctx: ChatCommandContext) -> str:
    """/jsonformat [on|off] — toggle automatic JSON detection and pretty-printing in responses."""
    return _system_cmd_mod._cmd_jsonformat(ctx)


def _cmd_separator(ctx: ChatCommandContext) -> str:
    """/separator [style] — set or preview response separator style (gradient|pulse|dots|wave|none)."""
    return _system_cmd_mod._cmd_separator(ctx)


def _cmd_links(ctx: "ChatCommandContext") -> str:
    """/links [on|off] — toggle clickable OSC 8 hyperlinks in responses (requires modern terminal)."""
    return _settings_cmd_mod._cmd_links(ctx)


_CMD_REGISTRY_CACHE: "dict | None" = None


def _get_cmd_registry() -> "ChatCommandRegistry":
    """Return the cached command registry, building it once on first call."""
    global _CMD_REGISTRY_CACHE
    if _CMD_REGISTRY_CACHE is None:
        _CMD_REGISTRY_CACHE = build_chat_command_registry()
    return _CMD_REGISTRY_CACHE


def _cmd_palette(ctx: "ChatCommandContext") -> str:
    """/palette [query] — search slash commands by keyword (fuzzy)."""
    return _system_cmd_mod._cmd_palette(ctx)


def build_chat_command_registry() -> ChatCommandRegistry:
    """Build and return the default interactive-chat command registry."""
    registry = ChatCommandRegistry()
    for name, description, handler, aliases in _COMMAND_SPECS:
        registry.register(SlashCommand(
            name=name,
            description=description,
            handler=handler,
            aliases=aliases,
        ))
    return registry


def print_chat_help(*, search: str = "") -> None:
    """Print built-in interactive chat commands, optionally filtered by *search*."""
    commands = [
        ("/help [search QUERY]",           "Show this help, or filter commands by keyword"),
        ("/clear",                         "Reset the current conversation history"),
        ("/quit",                          "Exit the CLI"),
        ("/update",                        "Self-upgrade openclaw via pip"),
        ("/version",                       "Show running CLI version and build stamp"),
        ("/session",                       "Show current session summary"),
        ("/context",                       "Show effective session grounding preview"),
        ("/cwd [path]",                    "Show or switch the session working directory"),
        ("/files",                         "List tracked files"),
        ("/files add <path>",              "Add a file to tracked files"),
        ("/files rm <path>",               "Remove a file from tracked files"),
        ("/plan [<id>|unlink]",            "Show or link a plan"),
        ("/task [<id>|unlink]",            "Show or link a task"),
        ("/risk [list|add LEVEL TEXT|clear INDEX]", "Track blocking risks for the current session"),
        ("/incident [list|log TEXT|resolve INDEX]", "Track and resolve operator incidents for the current session"),
        ("/dashboard automation",              "Show a compact automation dashboard across active sessions"),
        ("/alerts [list|acknowledge INDEX]",   "List computed operator alerts and acknowledge one"),
        ("/fleet [status|health]",             "Show cross-session automation health in a compact view"),
        ("/outputs [promote <i> <name>]",  "List, preview, promote, or overlay-pick saved session outputs"),
        ("/overlay [on|off|status]",       "Toggle opt-in interactive pickers for supported list commands"),
        ("/rollback [last|list|<name>]",   "List git snapshots, preview/exec rollback, or restore checkpoint"),
        ("/snapshot [name]",               "Save current git HEAD as a named restore point"),
        ("/events [n|decisions]",              "Show last n session events, or decision-only view"),
        ("/why",                               "Explain the last routing/tool decision (confidence, rationale, grounding)"),
        ("/workspace [status|save|list|restore NAME]", "Manage workspace recovery capsules for the current session"),
        ("/collab [status|share|assign]",      "Show or extend the actor-oriented handoff summary for the current session"),
        ("/runbook [template] [save <path>]",  "Render a long-form runbook for the active session"),
        ("/exporttemplates [list|show <name>]", "Inspect built-in runbook/export templates"),
        ("/collab note [@actor] TEXT",         "Record a collaboration note in the local session audit trail"),
        ("/collab decision [@actor] [#tag] TEXT", "Record a tagged decision for later handoff/export"),
        ("/collab assign @actor TEXT",         "Assign an owner to the next shared task or handoff step"),
        ("/handoff check",                     "Audit readiness using linked plan/task, ownership, and open risks"),
        ("/search <query>",                    "Search this session's event history for matching turns"),
        ("/search --all <query>",              "Search across all session histories"),
        ("/autoroute [on|off]",            "Show or toggle high-confidence REPL auto-routing"),
        ("/analyze <goal>",                "Analyze the session workspace"),
        ("/research <query>",              "Run the research agent on a query"),
        ("/write <task>",                  "Generate a markdown document"),
        ("/exec [--] <command>",           "Run a shell command with approval + session tracking"),
        ("/edit <path> [--content TEXT]",  "Inspect or write a file (--append to append)"),
        ("/theme [name|list|preview|next|prev|reset]", "Manage UI themes and previews"),
        ("/emoji [on|off|pack|preview]", "Toggle emoji or switch emoji packs"),
        ("/layout [compact|normal|verbose|plain|preset|show]", "Switch density or preset workspace views"),
        ("/sessions [search|related]",     "Browse or search recent sessions; /sessions overlay opens a picker"),
        ("/export [md|json|txt] [file]",   "Export session history to file (md/json/txt)"),
        ("/stats [commands|ratings|sessions]", "Show ASCII bar charts of usage statistics"),
        ("/tag [add|rm|list] <tag>",       "Manage tags on the current session"),
        ("/bookmark [label]",              "Save a replay bookmark for the current session"),
        ("/bookmarks",                     "List saved replay bookmarks for the current session"),
        ("/resume [last|<id>]",            "Print resume instructions for a past session"),
        ("/replay [session-id] [--from <bookmark>]", "Re-print the current or a past session conversation"),
        ("/draft [save|load|clear|restore]",    "Save, load, clear, or restore a draft prompt"),
        ("/draft multiline [on|off]",           "Toggle multiline compose mode"),
        ("/template [list|use|save|delete]",    "Manage reusable prompt templates"),
        ("/pasteguard [on|off]",                "Toggle paste guard for large risky pastes"),
        ("/pin [name]",                         "Pin the last AI response (auto-named if no name given)"),
        ("/pin recall <name>",                  "Re-display a pinned response"),
        ("/pin rm <name>",                      "Remove a pin by name"),
        ("/pins",                               "List all pinned responses"),
        ("/accessibility [status|mode]",        "Show or set accessibility modes (a11y)"),
        ("/accessibility reduced-motion on|off","Toggle reduced-motion (no spinner animation)"),
        ("/accessibility plain on|off",         "Toggle plain/screen-reader mode"),
        ("/accessibility high-contrast on|off", "Toggle high-contrast colour palette"),
        ("/alias",                              "List all defined command aliases"),
        ("/alias <name> <expansion>",           "Define a command shorthand alias"),
        ("/alias rm <name>",                    "Remove a defined alias"),
        ("/history [page]",                     "Show command history, 15 per page (color-coded)"),
        ("/history clear",                      "Clear command history"),
        ("/recall",                             "List recent prompts (non-slash-command inputs)"),
        ("/recall <n>",                         "Re-inject the nth most recent prompt into chat"),
        ("/histsearch <query>",                 "Search prompt history for matching entries"),
        ("/macro list",                         "List all saved macros"),
        ("/macro save <name> [last N]",         "Save last N commands as a named macro"),
        ("/macro show <name>",                  "Show the commands stored in a macro"),
        ("/macro run <name>",                   "Execute a saved macro's commands in sequence"),
        ("/macro rm <name>",                    "Delete a named macro"),
        ("/workflow list",                      "List previewable workflows backed by the macro store"),
        ("/workflow save <name> [last N]",      "Save recent commands as a workflow"),
        ("/workflow preview <name>",            "Show the resolved workflow steps without executing them"),
        ("/workflow run <name>",                "Execute a saved workflow with session placeholders resolved"),
        ("/workflow rm <name>",                 "Delete a saved workflow"),
        ("/pattern list",                       "Browse saved reusable patterns with lightweight source metadata"),
        ("/pattern save <name> [last N|workflow NAME]", "Save recent commands or a workflow as a reusable pattern"),
        ("/pattern preview <name>",             "Preview a saved pattern before execution"),
        ("/pattern run <name>",                 "Execute a saved pattern with session placeholders resolved"),
        ("/pattern rm <name>",                  "Delete a saved pattern"),
        ("/rate [good|ok|bad|meh|1-5]",         "Rate the last AI response and store feedback"),
        ("/quality",  "Show response quality stats — avg score, distribution, recent ratings"),
        ("/quality predict", "Show the best-rated route based on your prior ratings"),
        ("/routing [suggest|analyze]",         "Inspect learned route suggestions without changing auto-routing"),
        ("/streak",   "Show your current high-rating streak and all-time best"),
        ("/heatmap",  "Show a color-coded 24-hour activity heatmap of openclaw usage"),
        ("/top [n]",  "Show the n most frequently used prompts and commands (default: 10)"),
        ("/freq",     "Show frequency analysis of slash commands used"),
        ("/ratehint [on|off]",                   "Show or toggle the post-response rating hint"),
        ("/inject <path>",                       "Inject file content as context prefix for next message"),
        ("/inject --url <url>",                  "Inject URL content as context prefix for next message"),
        ("/inject clear",                        "Clear the pending injection"),
        ("/inject status",                       "Show what content is queued for injection"),
        ("/promptdebug",                         "Preview the full prompt that would be sent to AI (system + inject + message)"),
        ("/system",                              "View the current system prompt"),
        ("/system set <text>",                   "Set a persistent system prompt prefix for all messages"),
        ("/system append <text>",               "Append to the existing system prompt"),
        ("/system clear",                        "Clear the system prompt"),
        ("/autobold [on|off]",                   "Toggle automatic bolding of numbers and filenames in responses"),
        ("/jsonformat [on|off]",                 "Toggle automatic JSON detection and pretty-printing in responses"),
        ("/separator [style]",                   "Set or preview response separator style (gradient|pulse|dots|wave|none)"),
        ("/links [on|off]",                      "Toggle clickable OSC 8 hyperlinks in responses (requires modern terminal)"),
        ("/palette [query]",                     "Search slash commands by keyword (fuzzy)"),
        ("/shortcuts",                           "Show keyboard shortcuts and quick-access reference card"),
        ("/keys",                                "Show active keyboard shortcuts and readline bindings"),
        ("/bindlist",                            "Show all keyboard bindings — built-in readline + custom"),
        ("/keybind [list|Ctrl+X /cmd|clear X]", "Manage custom readline key bindings"),
        ("/diff [file1 file2 | --git]",          "Show a colorized unified diff"),
        ("/changes",                             "Show session edit log and git status"),
        ("/timeline",                            "Show a visual activity timeline of recent openclaw usage"),
        ("/dashboard",                           "Show the power dashboard: sessions, stats, pins, and system status"),
        ("/benchmark [n]",                       "Measure AI server response latency (n pings, default 3, max 10)"),
        ("/followup",                            "Show contextual follow-up suggestions for your last prompt"),
        ("/followup on|off",                     "Enable or disable the auto-suggestion footer after responses"),
        ("/tokeninfo",                           "Show estimated context token usage"),
    ]

    q = search.strip().lower()
    if q:
        commands = [(cmd, desc) for cmd, desc in commands if q in cmd.lower() or q in desc.lower()]
        if not commands:
            print(f"  {_DM}No commands match '{q}'.{_R}")
            return

    notes = (
        "High-confidence freeform prompts can auto-route to /analyze, /research, /write, /exec, or /edit.\n"
        "Multi-step prompts can decompose into linked plans and auto-run step-by-step with [n/N] progress.\n"
        "Ambiguous prompts stay in normal chat. High/critical /exec and /edit steps still require approval.\n"
        "[autoroute:off] in the prompt means auto-routing is disabled — use /autoroute on to re-enable."
    )
    if _RICH_AVAILABLE and _IS_TTY:
        title = f"[bold cyan]OpenClaw Commands[/bold cyan]" + (f"  [dim]matching '{q}'[/]" if q else "")
        t = _RichTable.grid(padding=(0, 2))
        t.add_column(style="bold cyan", no_wrap=True)
        t.add_column(style="dim")
        for cmd, desc in commands:
            t.add_row(cmd, desc)
        _RICH_CONSOLE.print(_RichPanel(t, title=title, border_style="cyan", padding=(0, 1)))
        if not q:
            _RICH_CONSOLE.print(f"[dim]{notes}[/dim]")
            examples = [
                ("Ask a question",       "What does this repo do?"),
                ("Analyze a directory",  "openclaw analyze --cwd ./src"),
                ("Run a command",        "/exec -- git diff HEAD"),
                ("Research a topic",     "/research latest Python async patterns"),
                ("Link a plan",          "/plan my-feature-plan"),
            ]
            ex_grid = _RichTable.grid(padding=(0, 2))
            ex_grid.add_column(style="dim")
            ex_grid.add_column(style="bold cyan")
            for label, cmd in examples:
                ex_grid.add_row(label, cmd)
            _RICH_CONSOLE.print(_RichPanel(ex_grid, title="[bold]Examples[/]", border_style="dim", padding=(0, 1)))
    else:
        if q:
            print(f"  Commands matching '{q}':")
        else:
            print("Interactive commands:")
        for cmd, desc in commands:
            print(f"  {cmd:<42} {desc}")
        print()
        if not q:
            print(notes)
            print("\nExamples:")
            print('  Ask a question         What does this repo do?')
            print('  Analyze a directory    openclaw analyze --cwd ./src')
            print('  Run a command          /exec -- git diff HEAD')
            print('  Research a topic       /research latest Python async patterns')
            print('  Link a plan            /plan my-feature-plan')


def handle_auth_command(args: argparse.Namespace) -> int:
    """Handle CLI token login/status/logout flows."""
    def _auth_print(msg: str, style: str = "") -> None:
        if _RICH_AVAILABLE and _IS_TTY and style:
            _RICH_CONSOLE.print(f"[{style}]{msg}[/]")
        else:
            print(msg)

    if args.auth_command == "login":
        token = str(getattr(args, "token", "") or "").strip()
        if not token:
            token = getpass.getpass("OpenClaw API token: ").strip()
        if not token:
            raise OpenClawCliError("OpenClaw token cannot be empty.")
        if sys.platform == "darwin":
            write_keychain_token(token)
            delete_saved_token()
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"🔑 [green]✓[/] token stored in macOS Keychain [dim]({KEYCHAIN_SERVICE})[/]")
            else:
                print(f"Stored OpenClaw token in macOS Keychain under '{KEYCHAIN_SERVICE}'.")
            return 0
        saved_path = write_saved_token(token)
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"🔑 [green]✓[/] token stored  [dim]{saved_path}[/]")
        else:
            print(f"Stored OpenClaw token in {saved_path}.")
        return 0

    if args.auth_command == "status":
        resolution = resolve_token_details(getattr(args, "token", None))
        if _RICH_AVAILABLE and _IS_TTY:
            if resolution.token:
                _RICH_CONSOLE.print(f"🔑 [green]✓[/] token configured  [dim]via {resolution.source}[/]")
            else:
                _RICH_CONSOLE.print("🔑 [dim]✗  no token configured[/]")
            if sys.platform == "darwin":
                _RICH_CONSOLE.print(f"   [dim]keychain service:[/]  {KEYCHAIN_SERVICE}")
            _RICH_CONSOLE.print(f"   [dim]credential file:[/]   {auth_storage_path()}")
        else:
            if resolution.token:
                print(f"OpenClaw token available via {resolution.source}.")
            else:
                print("No OpenClaw token is currently configured.")
            if sys.platform == "darwin":
                print(f"Keychain service: {KEYCHAIN_SERVICE}")
            print(f"Credential file: {auth_storage_path()}")
        return 0

    if args.auth_command == "logout":
        removed_locations: list[str] = []
        if delete_saved_token():
            removed_locations.append(str(auth_storage_path()))
        if delete_keychain_token():
            removed_locations.append(f"macOS Keychain '{KEYCHAIN_SERVICE}'")
        if removed_locations:
            joined = ", ".join(removed_locations)
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"🔑 [green]✓[/] token removed  [dim]{joined}[/]")
            else:
                print("Removed OpenClaw token from " + joined + ".")
        else:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("🔑 [dim]—  no persisted token was found[/]")
            else:
                print("No persisted OpenClaw token was found.")
        return 0

    raise OpenClawCliError(f"Unknown auth command: {args.auth_command}")


def _print_connection_error_panel(msg: str, base_url: str = "") -> None:
    """Print a rich error panel for connection/HTTP failures, with hints."""
    if not (_RICH_AVAILABLE and _IS_TTY):
        _print_error(msg, file=sys.stderr)
        _print_predictive_affordances(
            _build_error_recovery_hints(msg),
            title="Recovery menu",
            border_style="red",
        )
        return
    is_connection = any(k in msg.lower() for k in ("refused", "unreachable", "timed out", "resolve", "unable to reach"))
    body = _RichText()
    body.append(msg + "\n", style="red")
    if base_url:
        body.append("\n  url tried: ", style="dim")
        body.append(base_url + "\n", style="cyan")
    if is_connection:
        body.append("\n  hints:\n", style="dim")
        body.append("  • Is the OpenClaw server running?\n", style="dim")
        body.append("  • Check OPENCLAW_URL or pass --url\n", style="dim")
        body.append("  • Try: openclaw health\n", style="dim")
    _RICH_CONSOLE.print(_RichPanel(body, title="[bold red]❌ Connection failed[/]", border_style="red", padding=(0, 1)), file=sys.stderr)
    _print_predictive_affordances(
        _build_error_recovery_hints(msg),
        title="Recovery menu",
        border_style="red",
    )


def handle_status_command(args: argparse.Namespace, *, config: "CliConfig") -> int:
    """Show an at-a-glance status dashboard."""
    output_json = config.output_json

    version = cli_version()
    latest = _update_mod._latest_version

    try:
        health = fetch_health(config=config)
        health_status = health.status or "unknown"
        health_ok = health.healthy
    except OpenClawCliError as exc:
        health_status = "unreachable"
        health_ok = None
        health_err = str(exc)
    else:
        health_err = ""

    resolution = resolve_token_details(config.token)

    from openclaw_cli_sessions import list_sessions as _list_sessions
    recent = _list_sessions(limit=1)
    recent_session = recent[0] if recent else None

    if output_json:
        import json as _json
        print(_json.dumps({
            "version": version,
            "latest": latest,
            "health": health_status,
            "token_source": resolution.source if resolution.token else None,
            "recent_session": recent_session.session_id if recent_session else None,
        }, indent=2))
        return 0

    if _RICH_AVAILABLE and _IS_TTY:
        grid = _RichText()
        update_badge = ""
        if latest and latest != version:
            update_badge = f"  [yellow]⬆ {latest} available[/]  [dim]/update[/]"
        grid.append("  version    ", style="dim")
        grid.append(version, style="bold")
        grid.append(update_badge + "\n")
        health_emoji = _status_emoji(health_status)
        health_color = "green" if health_ok is True else ("yellow" if health_ok is False else "red")
        grid.append("  server     ", style="dim")
        grid.append(config.base_url, style="cyan")
        grid.append(f"  [{health_color}]{health_emoji} {health_status.upper()}[/]\n")
        if health_err:
            grid.append(f"              [dim red]{health_err[:80]}[/]\n")
        grid.append("  token      ", style="dim")
        if resolution.token:
            grid.append(f"🔑 [green]configured[/]  [dim]via {resolution.source}[/]\n")
        else:
            grid.append("🔑 [dim]not configured[/]\n")
        if recent_session:
            grid.append("  session    ", style="dim")
            grid.append(f"[yellow]{recent_session.session_id[:8]}…[/]")
            if recent_session.title:
                grid.append(f"  [dim]{recent_session.title[:50]}[/]")
            grid.append("\n")
        _RICH_CONSOLE.print(_RichPanel(grid, title="[bold cyan]🦞 OpenClaw Status[/]", border_style="cyan", padding=(0, 1)))
    else:
        print(f"version : {version}" + (f" (update: {latest})" if latest and latest != version else ""))
        print(f"server  : {config.base_url}  [{health_status}]")
        print(f"token   : {'configured via ' + resolution.source if resolution.token else 'not configured'}")
        if recent_session:
            print(f"session : {recent_session.session_id}")
    return 0


def load_shell_history() -> None:
    """Load persisted readline history when available."""
    if readline is None:
        return
    try:
        if HISTORY_FILE.exists():
            readline.read_history_file(str(HISTORY_FILE))
    except OSError:
        return
    readline.set_history_length(HISTORY_LIMIT)


def save_shell_history() -> None:
    """Persist readline history between interactive sessions when available."""
    if readline is None:
        return
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        readline.set_history_length(HISTORY_LIMIT)
        readline.write_history_file(str(HISTORY_FILE))
    except OSError:
        return


def _make_completer(registry: "ChatCommandRegistry") -> "Any":
    """Return a readline tab-completer for registered slash commands.

    Completes ``/`` prefixes against command names and their aliases.
    Falls back gracefully — never raises, since readline calls completers
    during interactive input where exceptions would be swallowed silently.
    """
    slash_names = []
    for cmd in registry.list_commands():
        slash_names.append("/" + cmd.name)
        for alias in cmd.aliases:
            slash_names.append("/" + alias)

    def _completer(text: str, state: int) -> "str | None":
        try:
            if text.startswith("/"):
                matches = [n for n in slash_names if n.startswith(text)]
            else:
                matches = []
            return matches[state] if state < len(matches) else None
        except Exception:  # noqa: BLE001
            return None

    return _completer


class _SlashCompleter:
    """readline completer for slash commands.

    Uses ``_BUILTIN_COMMAND_NAMES`` and user-defined aliases from ``_PREFS``
    so it works correctly even before the full command registry is built.
    """

    def __init__(self) -> None:
        self._matches: "list[str]" = []

    def complete(self, text: str, state: int) -> "str | None":
        if state == 0:
            self._matches = self._compute_matches(text)
        try:
            return self._matches[state]
        except IndexError:
            return None

    def _compute_matches(self, text: str) -> "list[str]":
        if not text.startswith("/"):
            return []
        prefix = text[1:].lower()
        names = sorted(_BUILTIN_COMMAND_NAMES)
        aliases = list(_PREFS.get("aliases", {}).keys())
        all_names = names + aliases
        matches = [f"/{n}" for n in all_names if n.lower().startswith(prefix)]
        return matches


def build_config(args: argparse.Namespace) -> CliConfig:
    """Build resolved CLI config from parsed args and environment."""
    timeout_seconds = max(1, int(args.timeout))
    return CliConfig(
        base_url=normalize_base_url(args.url or os.getenv("OPENCLAW_URL")),
        token=resolve_token(args.token),
        model=str(args.model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        timeout_seconds=timeout_seconds,
        user_name=(args.user_name or default_user_name()).strip(),
        client_name=(args.client_name or default_client_name()).strip(),
        output_json=bool(args.json),
        session_id=str(getattr(args, "session", "") or "").strip(),
        no_stream=bool(getattr(args, "no_stream", False)),
    )


def _print_status_bar(
    *,
    session_id: str = "",
    autoroute_on: bool = True,
    history_len: int = 0,
) -> None:
    _ui_utils_mod._print_status_bar(session_id=session_id, autoroute_on=autoroute_on, history_len=history_len, _override_is_tty=_IS_TTY, _override_rich_available=_RICH_AVAILABLE, _override_cols=_terminal_width())
def _make_prompt(session_id: str = "", autoroute_on: bool = True, multiline: bool = False) -> str:
    """Build the REPL prompt string, optionally with session hint or autoroute badge."""
    if _a11y_plain_mode():
        return "openclaw> "
    # If a custom prompt format is configured, use it instead of the default
    custom_fmt = _PREFS.get("prompt_format", "")
    if custom_fmt and custom_fmt != _DEFAULT_PROMPT_FORMAT:
        return _render_prompt_format(custom_fmt)
    is_tty = _get_is_tty()
    narrow = _terminal_width() < 56
    if is_tty:
        name = "\033[1;34moc\033[0m" if narrow else "\033[1;34mopenclaw\033[0m"
        ml_badge = f" \033[2;33m[multiline]\033[0m" if multiline else ""
        if not autoroute_on:
            return f"{name} \033[33m[autoroute:off]\033[0m{ml_badge} ❯ "
        if session_id:
            short = session_id[:4] if narrow else session_id[:8]
            return f"{name} \033[36m[{short}…]\033[0m{ml_badge} ❯ "
        return f"{name}{ml_badge} ❯ "
    ml_suffix = " [multiline]" if multiline else ""
    return f"openclaw{ml_suffix} ❯ "


def _render_prompt_format(fmt: str) -> str:
    """Render a prompt format string with current state substitutions."""
    import datetime
    now = datetime.datetime.now().strftime("%H:%M")

    route_mode = _PREFS.get("route_mode", "")
    route = f"[{route_mode}]" if route_mode else "[no-route]"

    session_name = _PREFS.get("current_session", "")
    session = f" ({session_name})" if session_name else ""

    model = _PREFS.get("last_model", "")

    result = fmt
    result = result.replace("{route}", route)
    result = result.replace("{session}", session)
    result = result.replace("{model}", model)
    result = result.replace("{build}", _CLI_BUILD)
    result = result.replace("{time}", now)
    return result


def _cmd_prompt(ctx: "ChatCommandContext") -> str:
    """/prompt [format] — customize the REPL prompt. Use {route}, {session}, {model}, {build}, {time}.

    Examples:
      /prompt {route} openclaw>
      /prompt openclaw [{time}]>
      /prompt {build} ❯
      /prompt reset          (restore default)
    """
    return _system_cmd_mod._cmd_prompt(ctx)


def _print_first_run_tips() -> None:
    """Print a compact new-session tip panel (shown once, only in TTY mode)."""
    is_tty = _get_is_tty()
    tips = [
        (f"{_e('📁', '[cwd]')} /cwd <path>",       "Set working directory for file context"),
        (f"{_e('📄', '[files]')} /files add <path>", "Track specific files the AI can reference"),
        (f"{_e('📋', '[plan]')} /plan <id>",         "Link a plan so routes read it automatically"),
        (f"{_e('🔍', '[ctx]')} /context",             "See what context the AI currently has"),
        (f"{_e('💡', '[help]')} /help search <kw>",  "Search commands by keyword"),
    ]
    if _RICH_AVAILABLE and is_tty:
        t = _RichTable.grid(padding=(0, 2))
        t.add_column(style="bold cyan", no_wrap=True)
        t.add_column(style="dim")
        for cmd, desc in tips:
            t.add_row(cmd, desc)
        _RICH_CONSOLE.print(_RichPanel(
            t,
            title=f"[dim]{_e('🚀', '[new]')} New session — quick tips[/]",
            border_style="dim",
            padding=(0, 1),
        ))
    else:
        print(f"\n  {_e('🚀', '[new]')} New session — quick tips:")
        for cmd, desc in tips:
            print(f"    {cmd:<32}  {desc}")
        print()


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


def _print_startup_banner(config: CliConfig, session_id: str) -> None:
    _ui_utils_mod._print_startup_banner(config, session_id)
def _cmd_pasteguard(ctx: "ChatCommandContext") -> str:
    """Toggle or inspect the paste guard setting."""
    return _settings_cmd_mod._cmd_pasteguard(ctx)


_BUILTIN_COMMAND_NAMES: "frozenset[str]" = frozenset({
    # Core
    "help", "clear", "quit", "exit", "update", "version", "v",
    # Session & context
    "session", "context", "cwd", "files", "plan", "watch", "task", "risk", "incident",
    "sessions", "tag", "resume", "replay", "handoff", "workspace", "collab",
    # Outputs & edits
    "outputs", "rollback", "events", "why", "trace", "runbook", "exporttemplates", "edit", "exec", "write",
    "changes", "diff", "snapshot",
    # Routing & analysis
    "autoroute", "analyze", "research",
    # Display & UI
    "theme", "emoji", "layout", "colorscheme", "separator", "links",
    "autobold", "jsonformat", "emojiheaders", "pathhints", "ratehint",
    "promptdebug", "quality", "routing", "tip", "shortcuts",
    "palette", "overlay", "bindlist", "keybind", "keys",
    # Dashboard & benchmarks
    "dashboard", "alerts", "fleet", "benchmark", "timeline",
    # History & search
    "history", "recall", "histsearch", "freq", "heatmap", "top", "streak",
    # Persistence
    "export", "stats",
    # Pinning & notes
    "pin", "pins", "search",
    # Aliases, macros, templates
    "alias", "macro", "macrostatus", "workflow", "pattern", "patterns", "template", "draft",
    # Accessibility
    "accessibility", "a11y",
    # Misc / fun
    "rate", "ratehint", "celebrate", "inject", "system", "prompt",
    "pasteguard", "followup", "tokeninfo",
})

_MAX_ALIASES = 50


def _cmd_alias(ctx: "ChatCommandContext") -> str:
    """Define, list, or remove command aliases."""
    return _system_cmd_mod._cmd_alias(ctx)


def _cmd_macro(ctx: "ChatCommandContext") -> str:
    """Manage named command macros. Sub-commands: list, save, show, rm, run."""
    return _workflow_cmd_mod._cmd_macro(ctx)


def _cmd_macrostatus(ctx: "ChatCommandContext") -> str:  # noqa: ARG001
    """/macrostatus — show saved macros with step counts."""
    return _workflow_cmd_mod._cmd_macrostatus(ctx)


def _cmd_workflow(ctx: "ChatCommandContext") -> str:
    """/workflow — manage previewable workflows backed by the macro store."""
    return _workflow_cmd_mod._cmd_workflow(ctx)


def _relative_time(ts_str: str) -> str:
    """Convert ISO timestamp to relative time string."""
    try:
        import datetime
        ts = datetime.datetime.fromisoformat(ts_str)
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
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
    except Exception:  # noqa: BLE001  # optional relative-time formatting
        return ""


def _cmd_history(ctx: "ChatCommandContext") -> str:
    """Show or clear recent command history with color-coding and pagination."""
    return _content_cmd_mod._cmd_history(ctx)


def _cmd_recall(ctx: "ChatCommandContext") -> str:
    """/recall <n> — re-inject the nth most recent prompt into the chat (1=most recent)."""
    return _misc_cmd_mod._cmd_recall(ctx)


def _cmd_histsearch(ctx: "ChatCommandContext") -> str:
    """/histsearch <query> — search prompt history for matching entries."""
    return _misc_cmd_mod._cmd_histsearch(ctx)


def _cmd_pin(ctx: "ChatCommandContext") -> str:
    """Pin the last AI response for quick recall. Sub-commands: [name] | recall <name> | rm <name> | list."""
    return _content_cmd_mod._cmd_pin(ctx)


def _cmd_pins(ctx: "ChatCommandContext") -> str:
    """List all pinned responses (alias for /pin list)."""
    return _content_cmd_mod._cmd_pins(ctx)


def _celebration_burst(message: str = "") -> None:
    _ui_utils_mod._celebration_burst(message)
def _cmd_celebrate(ctx: "ChatCommandContext") -> str:
    """/celebrate — trigger a celebration animation (just for fun!)."""
    return _misc_cmd_mod._cmd_celebrate(ctx)


def _cmd_rate(ctx: "ChatCommandContext") -> str:
    """Rate the last AI response (/rate [good|ok|bad|meh|1-5])."""
    return _misc_cmd_mod._cmd_rate(ctx)


def _print_ascii_trophy(streak: int) -> None:
    """Print an ASCII trophy for streak achievements."""
    is_tty = _get_is_tty()
    if _a11y_plain_mode():
        print(f"  🏆 {streak}-rating streak!")
        return

    trophy = [
        f"  {_YE}  ___  {_R}",
        f"  {_YE} /   \\ {_R}",
        f"  {_YE}|     |{_R}",
        f"  {_YE} \\   / {_R}",
        f"  {_YE}  | |  {_R}",
        f"  {_YE} _|_|_ {_R}",
        f"  {_YE}|_____|{_R}",
    ]

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold yellow]🏆 {streak}-Rating Streak! Amazing![/]\n")
        for line in trophy:
            _RICH_CONSOLE.print(line)
        _RICH_CONSOLE.print()
    else:
        print(f"\n  🏆 {streak}-Rating Streak! Amazing!\n")
        for line in trophy:
            print(line)
        print()


def _cmd_streak(ctx: "ChatCommandContext") -> str:
    """/streak — show your current rating streak and all-time best."""
    return _misc_cmd_mod._cmd_streak(ctx)


def _cmd_accessibility(ctx: "ChatCommandContext") -> str:
    """Show or configure accessibility modes (reduced-motion, plain, high-contrast)."""
    return _settings_cmd_mod._cmd_accessibility(ctx)


def _cmd_heatmap(ctx: ChatCommandContext) -> str:
    """/heatmap — show a color-coded hourly activity heatmap of openclaw usage."""
    return _misc_cmd_mod._cmd_heatmap(ctx)


def _cmd_quality(ctx: "ChatCommandContext") -> str:
    """/quality — show a colored histogram of response quality ratings."""
    return _content_cmd_mod._cmd_quality(ctx)


import re as _re

_FILE_PATH_PATTERN = _path_utils._FILE_PATH_PATTERN


def _detect_file_paths(text: str) -> "list[str]":
    return _path_utils._detect_file_paths(text)


def _print_path_hints(paths: "list[str]") -> None:
    return _path_utils._print_path_hints(paths, prefs=_PREFS, is_tty=_get_is_tty(), rich_available=_RICH_AVAILABLE)


def _suggest_followups(last_prompt: str, *, response_text: str = "", session_id: str = "") -> list[str]:
    return _path_utils._suggest_followups(last_prompt, response_text=response_text, session_id=session_id)


def _print_followup_suggestions(suggestions: list[str], *, mode: str = "chat") -> None:
    return _path_utils._print_followup_suggestions(suggestions, mode=mode, prefs=_PREFS, is_tty=_get_is_tty(), rich_available=_RICH_AVAILABLE)


def _cmd_pathhints(ctx: "ChatCommandContext") -> str:
    """/pathhints [on|off] — toggle file path quick-action hints after responses."""
    return _system_cmd_mod._cmd_pathhints(ctx)


def _cmd_ratehint(ctx: "ChatCommandContext") -> str:
    """/ratehint [on|off] — toggle the post-response rating hint."""
    return _system_cmd_mod._cmd_ratehint(ctx)


def _cmd_followup(ctx: "ChatCommandContext") -> str:
    """/followup [on|off] — show contextually relevant follow-up suggestions for your last prompt, or toggle the auto-suggestion footer."""
    return _misc_cmd_mod._cmd_followup(ctx)


def _cmd_shortcuts(ctx: "ChatCommandContext") -> str:
    """/shortcuts — show keyboard shortcuts and quick-access reference card."""
    return _misc_cmd_mod._cmd_shortcuts(ctx)


def _cmd_stats(ctx: "ChatCommandContext") -> str:
    """/stats [category] — show ASCII bar charts of usage statistics (commands, ratings, sessions)."""
    return _content_cmd_mod._cmd_stats(ctx)


def _cmd_top(ctx: "ChatCommandContext") -> str:
    """/top [n] — show the n most frequently used prompts and commands (default: 10)."""
    return _misc_cmd_mod._cmd_top(ctx)


def _cmd_freq(ctx: "ChatCommandContext") -> str:
    """/freq — show frequency analysis of slash commands used."""
    return _misc_cmd_mod._cmd_freq(ctx)


def _cmd_tip(ctx: "ChatCommandContext") -> str:
    """/tip — show a random openclaw usage tip."""
    return _misc_cmd_mod._cmd_tip(ctx)


def _paste_guard(
    prompt: str,
    *,
    input_func: Any,
    autoroute_on: bool,
) -> "str | None":
    """Warn and confirm before executing a large paste that routes to a risky command.

    Returns the prompt unchanged (proceed), or None (cancel this turn).
    Fails open — any unexpected error returns prompt unchanged.
    """
    try:
        if not (
            len(prompt) > 400
            and prompt.count("\n") >= 3
            and autoroute_on
            and _PREFS.get("paste_guard", True)
        ):
            return prompt

        # Peek at the route without executing — routing is deterministic for the same prompt.
        decision = route_repl_prompt(prompt, min_confidence=0.70)
        risky_kinds = {ReplRouteKind.EXEC, ReplRouteKind.EDIT, ReplRouteKind.PLAN}
        if decision.kind not in risky_kinds:
            return prompt

        route_label = f"/{decision.kind.value}" if decision.kind != ReplRouteKind.PLAN else "plan"
        preview = prompt[:200].replace("\n", "↵")
        print(
            f"\n  {_BYE}{_e('⚠️', '[!]')} Large paste detected — would route to {_BCY}{route_label}{_R}"
            f"\n  {_DM}Preview (first 200 chars):{_R} {preview}…"
            f"\n  {_B}[y]{_R} proceed  {_B}[n]{_R} cancel  {_B}[e]{_R} edit before sending\n"
        )
        choice = input_func("  Your choice [y/n/e]: ").strip().lower()
        if choice in ("y", ""):
            return prompt
        if choice == "e":
            print(f"  {_DM}Edit your prompt and re-submit.{_R}")
            return None
        # "n" or anything else
        print(f"  {_DM}Paste cancelled.{_R}")
        return None
    except Exception:  # noqa: BLE001
        return prompt


def _print_key_bindings() -> None:
    """Print currently active readline key bindings summary."""
    is_tty = _get_is_tty()
    bindings = [
        ("Ctrl+R",   "Reverse history search (type to filter)"),
        ("Ctrl+L",   "Clear screen"),
        ("Ctrl+W",   "Delete previous word"),
        ("Ctrl+U",   "Clear current line"),
        ("Ctrl+A",   "Jump to start of line"),
        ("Ctrl+E",   "Jump to end of line"),
        ("Ctrl+C",   "Interrupt / cancel"),
        ("Ctrl+D",   "Exit openclaw"),
        ("Tab",      "Auto-complete slash commands"),
        ("↑ / ↓",    "Browse command history"),
    ]

    if _RICH_AVAILABLE and is_tty:
        from rich.table import Table
        from rich.box import SIMPLE
        _RICH_CONSOLE.print(f"\n[bold cyan]⌨️  Active Key Bindings[/]\n")
        tbl = Table(box=SIMPLE, show_header=True, header_style="bold cyan")
        tbl.add_column("Key", style="bold yellow", no_wrap=True, width=16)
        tbl.add_column("Action")
        for key, desc in bindings:
            tbl.add_row(key, desc)
        _RICH_CONSOLE.print(tbl)
        _RICH_CONSOLE.print()
    else:
        print(f"\n⌨️  Active Key Bindings\n")
        for key, desc in bindings:
            print(f"  {_BYE}{key:<16}{_R} {desc}")
        print()


def _cmd_keys(ctx: "ChatCommandContext") -> str:
    """/keys — show active keyboard shortcuts and readline bindings."""
    return _misc_cmd_mod._cmd_keys(ctx)


def _cmd_bindlist(ctx: "ChatCommandContext") -> str:
    """/bindlist — show all keyboard bindings (built-in readline + custom)."""
    return _misc_cmd_mod._cmd_bindlist(ctx)


def _cmd_keybind(ctx: "ChatCommandContext") -> str:
    """/keybind [key action | list | clear <key>] — manage custom readline key bindings."""
    return _settings_cmd_mod._cmd_keybind(ctx)


def _render_diff_ansi(diff_text: str) -> str:
    """Apply ANSI colors to unified diff output (+ green, - red, @@ cyan)."""
    return _render_diff_ansi_impl(diff_text, plain_mode=_a11y_plain_mode())


def _cmd_diff(ctx: ChatCommandContext) -> str:
    """/diff [file1 file2 | --git] — show a colorized unified diff."""
    return _misc_cmd_mod._cmd_diff(ctx)


def _cmd_changes(ctx: ChatCommandContext) -> str:
    """/changes — show files mentioned/edited in this session."""
    return _misc_cmd_mod._cmd_changes(ctx)


def _cmd_timeline(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/timeline — show a visual activity timeline of recent openclaw usage."""
    return _content_cmd_mod._cmd_timeline(ctx)


def _cmd_dashboard(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/dashboard — show the power dashboard: sessions, stats, pins, and system status."""
    return _workflow_cmd_mod._cmd_dashboard(ctx)


def _cmd_alerts(ctx: ChatCommandContext) -> str:
    """/alerts [list|acknowledge INDEX] — inspect computed operator alerts."""
    return _workflow_cmd_mod._cmd_alerts(ctx)


def _cmd_fleet(ctx: ChatCommandContext) -> str:
    """/fleet [status|health] — show cross-session automation health summaries."""
    return _workflow_cmd_mod._cmd_fleet(ctx)


def _cmd_benchmark(ctx: ChatCommandContext) -> str:
    """/benchmark [n] — run n quick AI pings to measure response latency (default: 3)."""
    return _system_cmd_mod._cmd_benchmark(ctx)


# fmt: off
# Each entry: (name, description, handler, aliases)
_COMMAND_SPECS: "list[tuple]" = [
    ("help",         "Show this help",                                                                                          _cmd_help,         ()),
    ("clear",        "Reset the current conversation history",                                                                  _cmd_clear,        ()),
    ("quit",         "Exit the CLI",                                                                                            _cmd_quit,         ("exit",)),
    ("update",       "Self-upgrade openclaw via pip",                                                                           _cmd_update,       ()),
    ("version",      "Show the running CLI version",                                                                            _cmd_version,      ("v",)),
    ("session",      "Show current session summary",                                                                            _cmd_session,      ()),
    ("context",      "Show the effective session grounding preview",                                                            _cmd_context,      ()),
    ("cwd",          "Show or switch the session working directory (/cwd [path])",                                              _cmd_cwd,          ()),
    ("files",        "List, add, or remove tracked files (/files [add|rm] [path])",                                            _cmd_files,        ()),
    ("plan",         "Show, link, focus, or unlink a plan (/plan [<id>|status|focus|unlink])",                                  _cmd_plan,         ()),
    ("watch",        "Inspect or control active watch sessions (/watch [status|history|retry-limit N|intervene TEXT])",         _cmd_watch,        ()),
    ("task",         "Show, link, or unlink a task (/task [<id>|unlink])",                                                     _cmd_task,         ()),
    ("risk",         "Track blocking risks for the session (/risk [list|add LEVEL TEXT|clear INDEX])",                        _cmd_risk,         ()),
    ("incident",     "Track and resolve operator incidents (/incident [list|log TEXT|resolve INDEX])",                        _cmd_incident,     ()),
    ("dashboard",    "Show dashboard stats or automation summary (/dashboard [automation])",                                   _cmd_dashboard,    ()),
    ("alerts",       "List computed operator alerts (/alerts [list|acknowledge INDEX])",                                       _cmd_alerts,       ()),
    ("fleet",        "Show cross-session automation health (/fleet [status|health])",                                           _cmd_fleet,        ()),
    ("outputs",      "List or preview saved outputs (/outputs [<index>|<filename>])",                                           _cmd_outputs,      ()),
    ("overlay",      "Toggle opt-in interactive overlays (/overlay [on|off|status])",                                          _cmd_overlay,      ()),
    ("colorscheme",  "View or set the extended color scheme (/colorscheme [name|list|reset])",                                  _cmd_colorscheme,  ()),
    ("rollback",     "List/preview git snapshots or restore latest checkpoint (/rollback [last|list|<name>])",                  _cmd_rollback,     ()),
    ("snapshot",     "Save current git HEAD as a named restore point (/snapshot [name])",                                      _cmd_snapshot,     ()),
    ("events",       "Show recent session events (/events [n|decisions])",                                                     _cmd_events,       ()),
    ("why",          "Explain the last routing or tool decision",                                                               _cmd_why,          ()),
    ("trace",        "Show the latest routing trace with quality context",                                                      _cmd_trace,        ()),
    ("workspace",    "Manage workspace recovery capsules (/workspace [status|save|list|restore NAME])",                        _cmd_workspace,    ()),
    ("runbook",      "Render a long-form runbook for the active session",                                                       _cmd_runbook,      ()),
    ("exporttemplates", "Inspect built-in runbook/export templates",                                                            _cmd_exporttemplates, ("export-templates",)),
    ("collab",       "Capture collaboration notes/decisions and print a handoff summary",                                      _cmd_collab,       ()),
    ("search",       "Search session event history (/search <query> or /search --all <query>)",                                _cmd_search,       ()),
    ("autoroute",    "Show or toggle session auto-routing (/autoroute [on|off])",                                              _cmd_autoroute,    ()),
    ("analyze",      "Run an analysis on the current session context (/analyze <goal>)",                                       _cmd_analyze,      ()),
    ("research",     "Run the research agent on a query (/research <query>)",                                                  _cmd_research,     ()),
    ("write",        "Generate a markdown document from a writing task (/write <task>)",                                       _cmd_write,        ()),
    ("exec",         "Run a shell command with session tracking (/exec [--] <command>)",                                       _cmd_exec,         ()),
    ("edit",         "Inspect or write a file (/edit <path> [--content <text>] [--append <text>])",                            _cmd_edit,         ()),
    ("theme",        "Manage UI themes (/theme [name|list|preview|next|prev|reset])",                                          _cmd_theme,        ()),
    ("emojiheaders", "Toggle emoji prefixes on AI response headings (/emojiheaders [on|off])",                                 _cmd_emojiheaders, ()),
    ("emoji",        "Manage emoji packs (/emoji [on|off|pack|preview])",                                                      _cmd_emoji,        ()),
    ("layout",       "Switch density or preset workspaces (/layout [compact|normal|verbose|plain|preset|show])",               _cmd_layout,       ()),
    ("sessions",     "Browse recent sessions (/sessions [search QUERY])",                                                      _cmd_sessions,     ()),
    ("export",       "Export session history to file (md/json/txt)",                                                           _cmd_export,       ()),
    ("stats",        "Show aggregate usage statistics",                                                                        _cmd_stats,        ()),
    ("tag",          "Manage session tags (/tag [add <tag>|rm <tag>|list])",                                                   _cmd_tag,          ()),
    ("bookmark",     "Save a replay bookmark for the current session (/bookmark [label])",                                     _cmd_bookmark,     ()),
    ("bookmarks",    "List saved replay bookmarks for the current session",                                                    _cmd_bookmarks,    ()),
    ("resume",       "Print resume instructions for the most-recent other session (/resume [last|id])",                        _cmd_resume,       ()),
    ("replay",       "Re-print the current or a past session conversation (/replay [session-id] [--from bookmark])",           _cmd_replay,       ()),
    ("handoff",      "Save/restore a resumable workspace handoff  [create|list|open NAME|note TEXT|check]",                    _cmd_handoff,      ()),
    ("draft",        "Save, load, or clear a draft prompt",                                                                    _cmd_draft,        ()),
    ("template",     "Manage reusable prompt templates",                                                                       _cmd_template,     ()),
    ("pasteguard",   "Toggle paste guard for large risky pastes",                                                              _cmd_pasteguard,   ()),
    ("pin",          "Pin the last response for quick recall (/pin [name] | /pin recall <name> | /pin rm <name>)",             _cmd_pin,          ()),
    ("pins",         "List all pinned responses",                                                                              _cmd_pins,         ()),
    ("accessibility","Show or set accessibility modes (reduced-motion, plain, high-contrast)",                                 _cmd_accessibility,("a11y",)),
    ("alias",        "Define, list, or remove command aliases (/alias [name expansion | rm name])",                            _cmd_alias,        ()),
    ("history",      "Show recent command history (/history [page] | /history clear)",                                        _cmd_history,      ()),
    ("recall",       "Re-inject the nth most recent prompt (/recall [n])",                                                     _cmd_recall,       ()),
    ("histsearch",   "Search prompt history for matching entries (/histsearch <query>)",                                       _cmd_histsearch,   ()),
    ("macro",        "Manage and run command macros (/macro [save|list|show|run|rm] [name])",                                  _cmd_macro,        ()),
    ("macrostatus",  "Show saved macros with step counts (/macrostatus)",                                                      _cmd_macrostatus,  ()),
    ("workflow",     "Manage previewable workflows (/workflow [save|list|show|preview|run|rm] [name])",                       _cmd_workflow,     ()),
    ("pattern",      "Manage reusable pattern-library flows (/pattern [save|list|show|preview|run|rm] [name])",               _cmd_pattern,      ("patterns",)),
    ("rate",         "Rate the last AI response (/rate [good|ok|bad|meh|1-5])",                                               _cmd_rate,         ("feedback",)),
    ("celebrate",    "Trigger a celebration animation (/celebrate [message])",                                                 _cmd_celebrate,    ()),
    ("quality",      "Show response quality stats and predictions (/quality [predict])",                                       _cmd_quality,      ()),
    ("routing",      "Inspect learned route suggestions (/routing [suggest|analyze])",                                        _cmd_routing,      ()),
    ("heatmap",      "Show a color-coded hourly activity heatmap of openclaw usage",                                           _cmd_heatmap,      ()),
    ("top",          "Show the n most frequently used prompts and commands (default: 10)",                                     _cmd_top,          ()),
    ("freq",         "Show frequency analysis of slash commands used",                                                         _cmd_freq,         ()),
    ("ratehint",     "Toggle the post-response rating hint (/ratehint [on|off])",                                              _cmd_ratehint,     ()),
    ("streak",       "Show your current high-rating streak and all-time best",                                                 _cmd_streak,       ()),
    ("tip",          "Show a random openclaw usage tip",                                                                       _cmd_tip,          ()),
    ("inject",       "Inject file/URL content as context prefix for next message (/inject <path> | --url <url> | clear | status)", _cmd_inject,  ()),
    ("promptdebug",  "Preview what would be sent to the AI for the next message",                                              _cmd_promptdebug,  ("pd",)),
    ("system",       "View or set a persistent system prompt prefix (/system [view|set <text>|append <text>|clear])",          _cmd_system,       ()),
    ("autobold",     "Toggle automatic bolding of numbers and filenames in responses (/autobold [on|off])",                    _cmd_autobold,     ()),
    ("jsonformat",   "Toggle automatic JSON detection and pretty-printing in responses (/jsonformat [on|off])",                _cmd_jsonformat,   ()),
    ("separator",    "Set or preview response separator style (/separator gradient|pulse|dots|wave|none)",                     _cmd_separator,    ()),
    ("links",        "Toggle clickable OSC 8 hyperlinks in responses (/links [on|off])",                                       _cmd_links,        ()),
    ("palette",      "Search slash commands by keyword (/palette [query])",                                                    _cmd_palette,      ()),
    ("shortcuts",    "Show keyboard shortcuts and quick-access reference card",                                                _cmd_shortcuts,    ()),
    ("stats",        "Show ASCII bar charts of usage stats (/stats [commands|ratings|sessions])",                              _cmd_stats,        ()),
    ("pathhints",    "Toggle file path quick-action hints after responses (/pathhints [on|off])",                              _cmd_pathhints,    ()),
    ("prompt",       "Customize the REPL prompt string (/prompt [format|reset]). Tokens: {route} {session} {model} {build} {time}", _cmd_prompt, ()),
    ("keys",         "Show active keyboard shortcuts and readline bindings",                                                   _cmd_keys,         ()),
    ("bindlist",     "Show all keyboard bindings — built-in readline + custom",                                                _cmd_bindlist,     ()),
    ("keybind",      "Manage custom readline key bindings (/keybind [list | Ctrl+X /cmd | clear Ctrl+X])",                    _cmd_keybind,      ()),
    ("diff",         "Show a colorized unified diff (/diff file1 file2  or  /diff --git)",                                    _cmd_diff,         ()),
    ("changes",      "Show session edit log and git status",                                                                   _cmd_changes,      ()),
    ("timeline",     "Show a visual activity timeline of recent openclaw usage",                                               _cmd_timeline,     ()),
    ("benchmark",    "Measure AI server response latency (/benchmark [n], default 3 pings, max 10)",                           _cmd_benchmark,    ()),
    ("followup",     "Show contextual follow-up suggestions for your last prompt (/followup [on|off])",                        _cmd_followup,     ()),
    ("tokeninfo",    "Show estimated context token usage for this session",                                                    _cmd_tokeninfo,    ()),
]
# fmt: on


def _apply_custom_keybind(key_name: str, action: str) -> None:
    """Apply a custom keybind via readline (best-effort)."""
    try:
        import readline as _rl
        if key_name.startswith("Ctrl+"):
            char = key_name[5:].upper()
            if len(char) == 1:
                ctrl_seq = f"\\C-{char.lower()}"
                _rl.parse_and_bind(f'"{ctrl_seq}": "{action}\\n"')
    except (ImportError, AttributeError):
        pass


def _apply_all_custom_keybinds() -> None:
    """Apply all saved custom keybinds on startup."""
    custom = _PREFS.get("custom_keybinds", {})
    for key_name, action in custom.items():
        _apply_custom_keybind(key_name, action)


def _setup_readline() -> None:
    """Configure readline tab completion and keyboard shortcuts for the REPL."""
    if readline is None:
        return
    _slash_completer = _SlashCompleter()
    readline.set_completer(_slash_completer.complete)
    readline.set_completer_delims(" \t\n")
    readline.parse_and_bind("tab: complete")
    try:
        import readline as _rl
        # Ensure emacs mode is active so Ctrl-R reverse search works natively.
        _rl.parse_and_bind("set editing-mode emacs")
        # Ctrl-L: clear screen
        _rl.parse_and_bind(r'"\C-l": clear-screen')
        # Ctrl-W: delete word backward
        _rl.parse_and_bind(r'"\C-w": backward-kill-word')
        # Ctrl-U: kill line (explicit for clarity)
        _rl.parse_and_bind(r'"\C-u": unix-line-discard')
    except (ImportError, AttributeError):
        pass
    _apply_all_custom_keybinds()


def _maybe_show_startup_tip(config: "CliConfig", session_id: str, history: list) -> None:
    """Optionally display a random startup tip and first-run checklist."""
    import random as _random
    _is_tty_startup = _get_is_tty()
    if _random.random() < 0.3 and not _a11y_plain_mode() and _is_tty_startup and not config.output_json:
        _startup_tip = _random.choice(_OPENCLAW_TIPS)
        if _RICH_AVAILABLE:
            _RICH_CONSOLE.print(f"[dim]💡 {_startup_tip}[/]\n")
        else:
            print(f"{_DM}💡 {_startup_tip}{_R}\n")
    # First-run checklist: show tips when starting a brand-new empty session
    if session_id and not history and _is_tty_startup and not config.output_json:
        _print_first_run_tips()


def _read_multiline_input(input_func: Any, prompt_str: str) -> str:
    """Collect multiline input lines until the user types \\end."""
    print(f"  {_DM}[multiline — type \\end to submit]{_R}")
    lines: list[str] = []
    while True:
        line = str(input_func(prompt_str)).rstrip("\n")
        if line.strip().lower() == r"\end":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def run_chat(
    config: CliConfig,
    *,
    input_func: Any = input,
    ask_func: Any = invoke_openclaw,
    session_id: str = "",
    no_banner: bool = False,
) -> int:
    """Run an interactive chat session against OpenClaw."""
    _load_prefs()
    history: list[dict[str, str]] = load_conversation_history(session_id) if session_id else []
    registry = build_chat_command_registry()
    load_shell_history()
    _setup_readline()
    if not no_banner:
        _print_startup_banner(config, session_id)
    _maybe_show_startup_tip(config, session_id, history)
    while True:
        try:
            autoroute_on = _session_auto_route_enabled(session_id)
            prompt_str = _make_prompt(session_id=session_id, autoroute_on=autoroute_on, multiline=_multiline_mode)
            if _multiline_mode:
                prompt = _read_multiline_input(input_func, prompt_str)
            else:
                prompt = str(input_func(prompt_str)).strip()
        except EOFError:
            print()
            # Auto-summarize: promote the last user prompt to session title if still generic
            if session_id and history:
                _last_prompt = next(
                    (t["content"] for t in reversed(history) if t.get("role") == "user"), ""
                )
                if _last_prompt:
                    _sess = load_session(session_id)
                    if _sess and (not _sess.title or _sess.title.startswith("Session ")):
                        _sess.title = _last_prompt[:60].strip()
                        save_session(_sess)
            save_shell_history()
            return 0
        except KeyboardInterrupt:
            print()
            global _last_interrupted_prompt
            if readline is not None:
                _partial = readline.get_line_buffer().strip()
                if _partial:
                    _last_interrupted_prompt = _partial
                    print(f"  {_DM}↳ prompt interrupted — type /draft restore to recover it{_R}")
            save_shell_history()
            return 130

        if not prompt:
            continue

        # Record command history (skip empty lines)
        if prompt.strip():
            _hist = _PREFS.setdefault("cmd_history", [])
            _hist.append(prompt.strip())
            if len(_hist) > _CMD_HISTORY_MAX:
                _PREFS["cmd_history"] = _hist[-_CMD_HISTORY_MAX:]
            _save_prefs()

        # Paste guard — warn on large pastes that would trigger risky routing
        prompt = _paste_guard(prompt, input_func=input_func, autoroute_on=autoroute_on)
        if prompt is None:
            continue  # user declined — skip this turn

        # Alias expansion — one level only (no recursion) to avoid cycles
        if prompt.startswith("/"):
            _tok = prompt[1:].split(None, 1)
            _alias_name = _tok[0].lower() if _tok else ""
            _user_aliases = _PREFS.get("aliases", {})
            if _alias_name in _user_aliases:
                prompt = _user_aliases[_alias_name]
                if len(_tok) > 1:
                    prompt = prompt + " " + _tok[1]

        # Inline help: /cmd ? prints description without dispatching
        _help_match = re.match(r"^/(\S+)\s+\?$", prompt)
        if _help_match:
            _help_name = _help_match.group(1)
            _help_cmd = registry._lookup.get(_help_name)
            if _help_cmd:
                print(f"  {_BCY}/{_help_cmd.name}{_R}  —  {_help_cmd.description}")
                if _help_cmd.aliases:
                    aliases_str = ", ".join(f"{_DM}/{a}{_R}" for a in _help_cmd.aliases)
                    print(f"  {_DM}aliases:{_R} {aliases_str}")
            else:
                print(f"  {_DM}Unknown command /{_help_name} — type /help for a list.{_R}")
            continue

        ctx = ChatCommandContext(history=history, session_id=session_id, config=config)
        result = registry.dispatch(prompt, ctx)
        if result == _CMD_QUIT:
            save_shell_history()
            return 0
        if result == _CMD_CONTINUE:
            continue

        # Unknown slash command — don't send to the AI; suggest closest match.
        if prompt.startswith("/"):
            cmd_name = prompt.split()[0][1:]  # strip leading /
            _print_error(f"Unknown command {_BCY}/{cmd_name}{_R}. Type {_BCY}/help{_R} for a list.")
            _known = list(registry._lookup.keys())
            _suggestions = difflib.get_close_matches(cmd_name, _known, n=1, cutoff=0.6)
            if _suggestions:
                print(f"  {_DM}Did you mean {_R}{_BCY}/{_suggestions[0]}{_R}{_DM}?{_R}")
            _print_predictive_affordances(
                [
                    "/palette <term> to search commands by name or purpose",
                    "/shortcuts to review quick keyboard and command gestures",
                ],
                title="Command recovery",
                border_style="yellow",
            )
            continue

        if autoroute_on:
            route_decision = route_repl_prompt(prompt, session_id=session_id)
            if route_decision.should_auto_execute_plan():
                print(_format_route_announcement(route_decision))
                _append_repl_route_event(session_id, prompt, route_decision)
                try:
                    routed = _execute_routed_plan(
                        prompt=prompt,
                        decision=route_decision,
                        registry=registry,
                        ctx=ctx,
                    )
                except OpenClawCliError as exc:
                    print(f"{_BRE}error:{_R} {exc}", file=sys.stderr)
                else:
                    if routed == _CMD_QUIT:
                        save_shell_history()
                        return 0
                    if routed == _CMD_CONTINUE:
                        continue
            if route_decision.should_auto_route():
                print(_format_route_announcement(route_decision))
                _append_repl_route_event(session_id, prompt, route_decision)
                routed = registry.dispatch(route_decision.to_slash_command(), ctx)
                if routed == _CMD_QUIT:
                    save_shell_history()
                    return 0
                if routed == _CMD_CONTINUE:
                    continue
            # Prompt was classified but confidence was too low — show a brief hint.
            if route_decision.kind == ReplRouteKind.CHAT and session_id:
                _hint_rationale = (route_decision.rationale or "")[:80]
                print(f"  {_DM}↳ stayed in chat — confidence below threshold · {_hint_rationale}{_R}")

        try:
            _t0 = time.monotonic()
            global _next_inject
            # Store prompt for /followup and auto-suggestion footer (in-memory only, not saved to disk)
            _PREFS["_last_prompt"] = prompt.strip()
            if _next_inject:
                effective_input = f"[Injected context]\n{_next_inject}\n\n[User message]\n{prompt}"
                _next_inject = ""
            else:
                effective_input = prompt
            _sys_prompt = _PREFS.get("system_prompt", "").strip()
            if _sys_prompt:
                effective_input = f"[System context]\n{_sys_prompt}\n\n{effective_input}"
            response = _with_spinner(
                f"{_e('💬', '>>')} Thinking…",
                ask_func,
                effective_input,
                config=config,
                history=list(history),
                output_json=config.output_json,
            )
            _elapsed = time.monotonic() - _t0
        except KeyboardInterrupt:
            print(f"\n{_DM}{_e('⌨', '[ctrl-c]')} [interrupted]{_R}")
            continue
        except OpenClawCliError as exc:
            print(f"{_BRE}error:{_R} {exc}", file=sys.stderr)
            _print_predictive_affordances(
                _build_error_recovery_hints(str(exc), session_id=session_id) + ["/reset to clear chat history if the context feels stuck"],
                title="Recovery menu",
                border_style="red",
            )
            continue

        # Visual separator + status bar (skipped in compact layout)
        _is_tty = _get_is_tty()
        _compact = _PREFS.get("layout") == "compact"
        if _is_tty and not config.output_json and not _compact:
            _print_response_separator(label="Response", detail="answer reveal", status="active")

        print_response(response, output_json=config.output_json, elapsed=_elapsed)
        if body := (response.response or ""):
            _paths = _detect_file_paths(body)
            if _paths:
                _print_path_hints(_paths)
            _print_predictive_affordances(
                _dedupe_preserve_order(
                    [
                        f"/exec — run a shell command to investigate {_paths[0]}" if _paths and not _paths[0].startswith("/") else "",
                        "/context to verify what the next request will inherit" if session_id else "",
                        "/pin <name> to save this answer for reuse" if body.strip() else "",
                        "/outputs to review saved artifacts for this session" if session_id else "",
                    ]
                ),
                title="Suggested follow-ups",
                border_style="cyan",
            )
        _print_animated_separator()
        if not config.output_json:
            _footer_hints: list[str] = []
            if _PREFS.get("show_rate_hint", True):
                _footer_hints.append("/rate good — mark this answer helpful")
            if _PREFS.get("show_suggestions", True):
                _footer_hints.extend(
                    _suggest_followups(
                        prompt,
                        response_text=response.response or "",
                        session_id=session_id,
                    )
                )
            _print_followup_suggestions(_footer_hints, mode="chat")
        global _last_response_text
        _last_response_text = response.response or ""
        if not config.output_json and not _compact:
            _print_status_bar(
                session_id=session_id,
                autoroute_on=autoroute_on,
                history_len=len(history),
            )
        history.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response.response},
            ]
        )
        if session_id:
            append_event(session_id, kind="chat", content=prompt, metadata={"summary": prompt})
            persist_response(session_id, prompt, response.response)
            # Auto-title: after the first real turn, promote prompt to session title
            if len(history) == 2:
                _sess = load_session(session_id)
                if _sess and (not _sess.title or _sess.title.startswith("Session ")):
                    _sess.title = prompt[:60].strip()
                    save_session(_sess)


def run_async(coro: Any) -> Any:
    """Run an async coroutine from the synchronous CLI entrypoint."""
    return asyncio.run(coro)


def output_name_from_title(title: str, *, default_stem: str, suffix: str) -> str:
    """Build a safe output filename from free-form user input."""
    return _path_utils.output_name_from_title(title, default_stem=default_stem, suffix=suffix)


def missing_feature_hint(feature: str) -> str:
    """Explain when a standalone CLI install is missing optional dependencies."""
    return _path_utils.missing_feature_hint(feature)


def handle_session_command(args: argparse.Namespace) -> int:
    """Handle local CLI session management."""
    subcommand = str(getattr(args, "session_command", "") or "").strip()
    if subcommand == "create":
        session = create_session(
            title=str(getattr(args, "title", "") or "").strip() or "OpenClaw CLI session",
            cwd=getattr(args, "cwd", None),
            files=list(getattr(args, "files", []) or []),
            plan_id=str(getattr(args, "plan_id", "") or "").strip(),
            task_id=str(getattr(args, "task_id", "") or "").strip(),
        )
        _print_session_summary(session)
        return 0
    if subcommand == "list":
        sessions = list_sessions(limit=int(getattr(args, "limit", 20) or 20))
        filter_query = str(getattr(args, "filter", "") or "").strip().lower()
        if filter_query:
            sessions = [
                s for s in sessions
                if filter_query in s.session_id.lower()
                or filter_query in (s.title or "").lower()
                or filter_query in (s.last_summary or "").lower()
                or filter_query in " ".join(getattr(s, "tags", [])).lower()
            ]
        if bool(getattr(args, "interactive", False)):
            overlay_result = _run_interactive_overlay(
                title="Session list overlay",
                items=sessions,
                label_fn=lambda s: (
                    f"{s.session_id[:8]}…  {s.title or '—'}  "
                    f"{(s.updated_at or '—')[:19]}  {_session_badges(s)}".strip()
                ),
                on_select=lambda s: (
                    _print_session_summary(s),
                    _print_meta_footer(("resume", f"openclaw --session {s.session_id}")),
                ),
                initial_query=filter_query,
                empty_message="No sessions found.",
            )
            if overlay_result == "selected":
                return 0
        _print_session_list(sessions)
        return 0
    if subcommand == "show":
        out = inspect_session(args.session_id)
        if out:
            print(out)
        return 0
    if subcommand == "resume":
        session = require_session(args.session_id)
        _print_session_summary(session)
        _print_meta_footer(("resume", f"openclaw --session {session.session_id}"))
        return 0
    if subcommand == "export":
        if getattr(args, "format", "json") == "runbook":
            print(_build_session_runbook_text(args.session_id, template_name=getattr(args, "template", "operator")))
        else:
            print(json.dumps(export_session(args.session_id), indent=2, sort_keys=True))
        return 0
    if subcommand == "share":
        print(_build_session_share_text(args.session_id))
        return 0
    raise OpenClawCliError(f"Unknown session command: {subcommand}")


def handle_plan_command(args: argparse.Namespace, *, session_id: str = "") -> int:
    """Handle agent-plan operations using the existing agent loop."""
    try:
        from agent_loop import cancel_plan, read_plan, resume_plan
        from agent_loop import list_plans as list_plan_objects
    except ImportError as exc:
        raise OpenClawCliError(missing_feature_hint("openclaw plan")) from exc

    subcommand = str(getattr(args, "plan_command", "") or "").strip()
    if subcommand == "create":
        goal = parse_prompt(getattr(args, "goal", []) or [])
        if not goal:
            raise OpenClawCliError("A plan goal is required.")
        steps_text = str(getattr(args, "steps_text", "") or "")
        _plan_id, create_result = _create_persisted_plan(
            goal=goal,
            steps_text=steps_text,
            session_id=session_id,
        )
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]✓[/] [bold]plan created:[/] [yellow]{_plan_id}[/]")
        else:
            print(create_result)
        return 0
    if subcommand == "list":
        plans = list_plan_objects(str(getattr(args, "status", "all") or "all"))
        if not plans:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No plans found.[/]")
            else:
                print("No plans found.")
            return 0
        if _RICH_AVAILABLE and _IS_TTY:
            _STATUS_COLORS = {
                "running": "cyan", "done": "green", "completed": "green",
                "failed": "red", "cancelled": "dim", "pending": "yellow",
                "in-progress": "cyan",
            }
            table = _RichTable(border_style="dim", show_edge=True, pad_edge=True, header_style="bold cyan")
            table.add_column("Plan ID", style="dim", no_wrap=True)
            table.add_column("Status", no_wrap=True)
            table.add_column("Progress", no_wrap=True)
            table.add_column("Goal")
            for plan in plans:
                s = (plan.status or "unknown").lower()
                color = _STATUS_COLORS.get(s, "dim")
                emoji = _status_emoji(s)
                goal_text = (plan.goal or "")[:72] + ("…" if len(plan.goal or "") > 72 else "")
                table.add_row(
                    plan.plan_id,
                    f"[{color}]{emoji} {plan.status}[/]",
                    plan.progress_str() if hasattr(plan, "progress_str") else "—",
                    goal_text,
                )
            _RICH_CONSOLE.print(table)
        else:
            for plan in plans:
                print(f"{plan.plan_id} | {plan.status} | {plan.progress_str()} | {plan.goal}")
        return 0
    if subcommand == "show":
        print(run_async(read_plan(args.plan_id)))
        return 0
    if subcommand == "resume":
        result = run_async(resume_plan(args.plan_id))
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]▶[/] [bold]plan resumed:[/] [yellow]{args.plan_id}[/]  [dim]{str(result)[:120]}[/]")
        else:
            print(result)
        return 0
    if subcommand == "cancel":
        result = run_async(cancel_plan(args.plan_id))
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[red]✗[/] [bold]plan cancelled:[/] [dim]{args.plan_id}[/]  [dim]{str(result)[:120]}[/]")
        else:
            print(result)
        return 0
    raise OpenClawCliError(f"Unknown plan command: {subcommand}")


def handle_analyze_command(args: argparse.Namespace, *, config: CliConfig) -> int:
    """Analyze a working directory or file set through the ask API."""
    prompt_parts, prompt_targets = extract_prompt_targets(list(getattr(args, "prompt", []) or []), cwd=getattr(args, "cwd", None))
    goal = parse_prompt(prompt_parts)
    if not goal:
        raise OpenClawCliError("Analysis goal is required.")

    explicit_targets = [*list(getattr(args, "files", []) or []), *prompt_targets]
    normalized_targets, context_text = collect_workspace_context(cwd=getattr(args, "cwd", None), targets=explicit_targets)
    session = ensure_cli_session(
        getattr(args, "session", ""),
        title=f"Analyze: {goal[:60]}",
        cwd=getattr(args, "cwd", None),
        files=normalized_targets,
        plan_id=str(getattr(args, "plan_id", "") or "").strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
    )
    scoped_config = bind_config_to_session(config, session.session_id)
    prompt = build_analysis_prompt(goal=goal, context_text=context_text, session=session)
    append_event(
        session.session_id,
        kind="analyze",
        content=goal,
        metadata={
            "summary": goal,
            "cwd": session.cwd,
            "files": normalized_targets,
            "plan_id": getattr(args, "plan_id", ""),
            "task_id": getattr(args, "task_id", ""),
        },
    )
    response = _with_spinner(
        "🔍 Analyzing…",
        invoke_openclaw,
        prompt,
        config=scoped_config,
        history=load_conversation_history(session.session_id),
        output_json=config.output_json,
    )
    print_response(response, output_json=config.output_json)
    persist_response(session.session_id, goal, response.response)
    _print_meta_footer(("session", session.session_id))
    return 0


def handle_research_command(args: argparse.Namespace) -> int:
    """Run the built-in research agent from the CLI."""
    from openclaw_cli_actions import write_text_file
    try:
        from research_agent import ResearchAgent
    except ImportError as exc:
        raise OpenClawCliError(missing_feature_hint("openclaw research")) from exc

    prompt_parts, prompt_targets = extract_prompt_targets(list(getattr(args, "query", []) or []), cwd=getattr(args, "cwd", None))
    query = parse_prompt(prompt_parts)
    if not query:
        raise OpenClawCliError("Research query is required.")

    explicit_targets = [*list(getattr(args, "files", []) or []), *prompt_targets]
    normalized_targets, context_text = collect_workspace_context(cwd=getattr(args, "cwd", None), targets=explicit_targets)
    session = ensure_cli_session(
        getattr(args, "session", ""),
        title=f"Research: {query[:60]}",
        cwd=getattr(args, "cwd", None),
        files=normalized_targets,
        plan_id=str(getattr(args, "plan_id", "") or "").strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
    )
    effective_query = query
    plan_ctx = _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd)
    if plan_ctx:
        effective_query = f"{plan_ctx}\n\n{effective_query}"
    if context_text and normalized_targets:
        effective_query = f"{effective_query}\n\nLocal workspace context:\n{context_text[:4000]}"

    async def _progress(message: str) -> None:
        if _IS_TTY:
            sys.stdout.write(f"\r🔍 {message:<60}")
            sys.stdout.flush()
        else:
            print(message)

    append_event(session.session_id, kind="research", content=query, metadata={"summary": query, "files": normalized_targets})
    report = run_async(ResearchAgent().run(effective_query, on_progress=_progress, deep=bool(getattr(args, "deep", False))))
    if _IS_TTY:
        sys.stdout.write("\r" + " " * 62 + "\r")
        sys.stdout.flush()

    output_path = str(getattr(args, "output", "") or "").strip()
    if output_path:
        write_text_file(output_path, content=report)
        output_display = output_path
    else:
        output_target = save_output(
            session.session_id,
            output_name_from_title(query, default_stem="research-report", suffix=".md"),
            report,
        )
        output_display = str(output_target)
    append_event(session.session_id, kind="assistant", content=report, metadata={"summary": f"saved research to {output_display}"})
    print(report)
    _print_meta_footer(("saved", output_display), ("session", session.session_id))
    return 0


def handle_write_command(args: argparse.Namespace, *, config: CliConfig) -> int:
    """Generate a markdown document from a writing task."""
    task_parts, prompt_targets = extract_prompt_targets(list(getattr(args, "task", []) or []), cwd=getattr(args, "cwd", None))
    task_text = parse_prompt(task_parts)
    if not task_text:
        raise OpenClawCliError("Writing task is required.")

    explicit_targets = [*list(getattr(args, "files", []) or []), *prompt_targets]
    normalized_targets, context_text = collect_workspace_context(cwd=getattr(args, "cwd", None), targets=explicit_targets)
    title = str(getattr(args, "title", "") or "").strip() or task_text[:80]
    session = ensure_cli_session(
        getattr(args, "session", ""),
        title=f"Write: {title[:60]}",
        cwd=getattr(args, "cwd", None),
        files=normalized_targets,
        plan_id=str(getattr(args, "plan_id", "") or "").strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
    )
    scoped_config = bind_config_to_session(config, session.session_id)
    prompt = build_write_prompt(task=task_text, context_text=context_text, session=session, title=title)
    append_event(session.session_id, kind="write", content=task_text, metadata={"summary": task_text, "files": normalized_targets})
    response = _with_spinner(
        "✍️  Writing…",
        invoke_openclaw,
        prompt,
        config=scoped_config,
        history=load_conversation_history(session.session_id),
        output_json=config.output_json,
    )
    persist_response(session.session_id, task_text, response.response)

    output_path = str(getattr(args, "output", "") or "").strip()
    if output_path:
        write_text_file(output_path, content=response.response)
        output_display = output_path
    else:
        output_target = save_output(
            session.session_id,
            output_name_from_title(title, default_stem="draft", suffix=".md"),
            response.response,
        )
        output_display = str(output_target)
    print(response.response)
    _print_meta_footer(("saved", output_display), ("session", session.session_id))
    return 0






def handle_exec_command(args: argparse.Namespace) -> int:
    """Run a shell command with session tracking and CLI approvals."""
    command_parts = list(getattr(args, "shell_command", []) or [])
    if command_parts and command_parts[0] == "--":
        command_parts = command_parts[1:]
    if not command_parts:
        raise OpenClawCliError("A command is required after `openclaw exec --`.")
    risk_level = risk_level_from_name(getattr(args, "risk", None), default=infer_command_risk(command_parts))
    session = ensure_cli_session(
        getattr(args, "session", ""),
        title=f"Exec: {' '.join(command_parts)[:60]}",
        cwd=getattr(args, "cwd", None),
        files=[],
        plan_id=str(getattr(args, "plan_id", "") or "").strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
    )
    _print_risky_action_warning(
        action="exec",
        target=" ".join(command_parts),
        risk_level=risk_level,
        recovery_hint="check the cwd and use your shell history or VCS tools before re-running.",
    )
    if not request_cli_approval(
        action="shell.exec",
        target=" ".join(command_parts),
        risk_level=risk_level,
        detail=f"cwd={getattr(args, 'cwd', '') or os.getcwd()}",
        auto_approve=bool(getattr(args, "yes", False)),
        session_id=session.session_id,
        plan_id=session.plan_id,
        task_id=session.task_id,
    ):
        raise OpenClawCliError("Shell command was not approved.")
    result = run_async(
        run_shell_command(
            command_parts,
            cwd=getattr(args, "cwd", None),
            timeout=int(getattr(args, "command_timeout", 60) or 60),
        )
    )
    append_event(
        session.session_id,
        kind="exec",
        content=" ".join(command_parts),
        metadata={
            "summary": f"exit {result.returncode}: {' '.join(command_parts)}",
            "cwd": result.cwd,
            "risk_level": risk_level.value,
            "returncode": result.returncode,
        },
    )
    _print_shell_result(result)
    _print_feedback(
        "Command complete.",
        level="success" if result.returncode == 0 else "warn",
        detail=f"exit {result.returncode} · cwd {result.cwd}",
    )
    _print_meta_footer(("session", session.session_id))
    return 0 if result.returncode == 0 else 1


def handle_edit_command(args: argparse.Namespace) -> int:
    """Edit a text file with diff previews and approval tracking."""
    path = str(getattr(args, "path", "") or "").strip()
    if not path:
        raise OpenClawCliError("A file path is required.")
    content = str(getattr(args, "content", "") or "")
    replace_values = list(getattr(args, "replace", []) or [])
    if not replace_values and not content and sys.stdin.isatty():
        raise OpenClawCliError("Provide --replace OLD NEW, --content TEXT, or pipe content on stdin.")
    if not replace_values and not content and not sys.stdin.isatty():
        content = sys.stdin.read()

    risk_level = risk_level_from_name(getattr(args, "risk", None), default=infer_file_edit_risk(path))
    session = ensure_cli_session(
        getattr(args, "session", ""),
        title=f"Edit: {Path(path).name}",
        cwd=str(Path(path).expanduser().resolve().parent),
        files=[path],
        plan_id=str(getattr(args, "plan_id", "") or "").strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
    )
    _print_risky_action_warning(
        action="edit",
        target=path,
        risk_level=risk_level,
        recovery_hint="recover with your editor or VCS; routed REPL edits also support /rollback last.",
    )
    if not request_cli_approval(
        action="file.edit",
        target=path,
        risk_level=risk_level,
        detail=f"append={bool(getattr(args, 'append', False))} dry_run={bool(getattr(args, 'dry_run', False))}",
        auto_approve=bool(getattr(args, "yes", False)),
        session_id=session.session_id,
        plan_id=session.plan_id,
        task_id=session.task_id,
    ):
        raise OpenClawCliError("File edit was not approved.")
    if replace_values:
        result = replace_text_in_file(
            path,
            old=replace_values[0],
            new=replace_values[1],
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    else:
        result = write_text_file(
            path,
            content=content,
            append=bool(getattr(args, "append", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    append_event(
        session.session_id,
        kind="edit",
        content=path,
        metadata={
            "summary": result.summary,
            "files": [result.path],
            "changed": result.changed and not bool(getattr(args, "dry_run", False)),
            "risk_level": risk_level.value,
        },
    )
    _print_file_edit_result(result)
    _print_feedback(
        "Edit complete.",
        level="success" if result.changed else "info",
        detail=result.summary,
    )
    _print_meta_footer(("session", session.session_id))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Launch OpenClaw from the terminal.",
        epilog=(
            "Running with no prompt starts interactive chat.\n"
            "Passing a bare prompt auto-wraps to `ask`.\n"
            "`OpenClaw` is a shell shim for `openclaw` in the installer/setup scripts.\n\n"
            "Examples:\n"
            "  OpenClaw\n"
            "  openclaw \"what changed overnight?\"\n"
            "  openclaw analyze --cwd . @README.md \"summarize the repo\"\n"
            "  openclaw watch --cwd . --on-change --iterations 5 \"keep an eye on test regressions\"\n"
            "  openclaw research \"best async Python patterns\"\n"
            "  openclaw write --title \"Weekly recap\" \"Draft the report\"\n"
            "  openclaw exec -- git status\n"
            "  openclaw ask \"summarize the latest alerts\"\n"
            "  openclaw --health\n"
            "  openclaw auth login\n"
            "  openclaw auth status"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {cli_version()}")
    parser.add_argument("--health", action="store_true", help="Check the OpenClaw /health endpoint and exit")
    parser.add_argument("--url", help="OpenClaw base URL (default: OPENCLAW_URL or http://localhost:8765)")
    parser.add_argument("--token", help=f"API token (default: {TOKEN_ENV_VARS}, plus macOS Keychain on macOS)")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model preference: auto, gemini, openai, anthropic, or local",
    )
    parser.add_argument(
        "--timeout",
        default=DEFAULT_TIMEOUT_SECONDS,
        type=int,
        help="HTTP timeout in seconds",
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON responses")
    parser.add_argument("--no-stream", dest="no_stream", action="store_true", help="Disable streaming output (batch mode)")
    parser.add_argument("--no-banner", dest="no_banner", action="store_true", help="Suppress startup banner (for scripting)")
    parser.add_argument("--user-name", help="Logical user label sent to OpenClaw")
    parser.add_argument("--client-name", help="Client/machine label for headers and telemetry")
    parser.add_argument("--session", help="Resume or tag a local CLI session")

    subparsers = parser.add_subparsers(dest="command")

    ask_parser = subparsers.add_parser("ask", help="Send a single prompt")
    ask_parser.add_argument("prompt", nargs="*", help="Prompt text (or pipe via stdin)")

    subparsers.add_parser("chat", help="Start an interactive chat session")
    subparsers.add_parser("health", help="Check the OpenClaw /health endpoint")
    auth_parser = subparsers.add_parser("auth", help="Manage stored CLI authentication")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)
    login_parser = auth_subparsers.add_parser("login", help="Persist a token for future CLI use")
    login_parser.add_argument("--token", help="Token to store; if omitted, prompt securely")
    auth_subparsers.add_parser("status", help="Show where the CLI token is currently resolved from")
    auth_subparsers.add_parser("logout", help="Remove persisted CLI token(s)")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze a project, directory, or file set")
    analyze_parser.add_argument("--cwd", help="Working directory to inspect")
    analyze_parser.add_argument("--file", dest="files", action="append", default=[], help="Explicit file or directory target")
    analyze_parser.add_argument("--plan-id", help="Optional related plan identifier")
    analyze_parser.add_argument("--task-id", help="Optional related task identifier")
    analyze_parser.add_argument("prompt", nargs="*", help="Analysis goal; @path references are treated as targets")

    session_parser = subparsers.add_parser("session", help="Manage local CLI sessions")
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True)
    session_create = session_subparsers.add_parser("create", help="Create a new local CLI session")
    session_create.add_argument("--title", help="Human-readable session title")
    session_create.add_argument("--cwd", help="Working directory associated with the session")
    session_create.add_argument("--file", dest="files", action="append", default=[], help="Initial tracked file or directory")
    session_create.add_argument("--plan-id", help="Optional related plan identifier")
    session_create.add_argument("--task-id", help="Optional related task identifier")
    session_list = session_subparsers.add_parser("list", help="List recent local sessions")
    session_list.add_argument("--limit", type=int, default=20, help="Maximum number of sessions to print")
    session_list.add_argument("--filter", help="Optional text filter for titles, IDs, summaries, or tags")
    session_list.add_argument("--interactive", action="store_true", help="Open an opt-in interactive session picker when running in a TTY")
    session_show = session_subparsers.add_parser("show", help="Show a local session summary")
    session_show.add_argument("session_id", help="Session identifier")
    session_resume = session_subparsers.add_parser("resume", help="Show a session and print its resume command")
    session_resume.add_argument("session_id", help="Session identifier")
    session_export = session_subparsers.add_parser("export", help="Export a local session as JSON or runbook text")
    session_export.add_argument("session_id", help="Session identifier")
    session_export.add_argument("--format", choices=("json", "runbook"), default="json", help="Export format")
    session_export.add_argument("--template", default="operator", help="Runbook template when --format runbook")
    session_share = session_subparsers.add_parser("share", help="Print a shareable collaboration handoff summary")
    session_share.add_argument("session_id", help="Session identifier")

    plan_parser = subparsers.add_parser("plan", help="Manage agent loop plans")
    plan_subparsers = plan_parser.add_subparsers(dest="plan_command", required=True)
    plan_create = plan_subparsers.add_parser("create", help="Create a new plan")
    plan_create.add_argument("goal", nargs="*", help="Plan goal")
    plan_create.add_argument("--steps-text", default="", help="Optional newline-delimited steps")
    plan_list = plan_subparsers.add_parser("list", help="List plans")
    plan_list.add_argument("--status", default="all", help="Plan status filter")
    plan_show = plan_subparsers.add_parser("show", help="Show a plan")
    plan_show.add_argument("plan_id", help="Plan identifier")
    plan_resume = plan_subparsers.add_parser("resume", help="Resume an interrupted plan")
    plan_resume.add_argument("plan_id", help="Plan identifier")
    plan_cancel = plan_subparsers.add_parser("cancel", help="Cancel a plan")
    plan_cancel.add_argument("plan_id", help="Plan identifier")

    research_parser = subparsers.add_parser("research", help="Run deep research with saved session outputs")
    research_parser.add_argument("--cwd", help="Working directory to include as context")
    research_parser.add_argument("--file", dest="files", action="append", default=[], help="Explicit file or directory target")
    research_parser.add_argument("--plan-id", help="Optional related plan identifier")
    research_parser.add_argument("--task-id", help="Optional related task identifier")
    research_parser.add_argument("--deep", action="store_true", help="Use iterative gap-filling research mode")
    research_parser.add_argument("--output", help="Optional output file path")
    research_parser.add_argument("query", nargs="*", help="Research query; @path references are treated as targets")

    write_parser = subparsers.add_parser("write", help="Draft a document and save it to the current session")
    write_parser.add_argument("--cwd", help="Working directory to include as context")
    write_parser.add_argument("--file", dest="files", action="append", default=[], help="Explicit file or directory target")
    write_parser.add_argument("--plan-id", help="Optional related plan identifier")
    write_parser.add_argument("--task-id", help="Optional related task identifier")
    write_parser.add_argument("--title", help="Document title")
    write_parser.add_argument("--output", help="Optional output file path")
    write_parser.add_argument("task", nargs="*", help="Writing task; @path references are treated as targets")

    watch_parser = subparsers.add_parser("watch", help="Run a bounded, resumable automation watch loop")
    watch_parser.add_argument("--cwd", help="Working directory to inspect")
    watch_parser.add_argument("--file", dest="files", action="append", default=[], help="Explicit file or directory target")
    watch_parser.add_argument("--plan-id", help="Optional related plan identifier")
    watch_parser.add_argument("--task-id", help="Optional related task identifier")
    watch_parser.add_argument("--mode", choices=["analyze", "research", "write"], default="analyze", help="Watch action to run each poll")
    watch_parser.add_argument("--interval", type=int, default=30, help="Seconds between polls")
    watch_parser.add_argument("--iterations", type=int, default=5, help="Maximum polls before exiting (0 means keep running)")
    watch_parser.add_argument("--on-change", action="store_true", help="Skip iterations until tracked workspace content changes")
    watch_parser.add_argument("--resume", help="Resume a prior watch session by session id")
    watch_parser.add_argument("--deep", action="store_true", help="Use deep research when mode=research")
    watch_parser.add_argument("--title", help="Document title override when mode=write")
    watch_parser.add_argument("--output", help="Optional output file to overwrite each poll")
    watch_parser.add_argument("goal", nargs="*", help="Automation goal; @path references are treated as targets")

    exec_parser = subparsers.add_parser("exec", help="Run a shell command with session and approval tracking")
    exec_parser.add_argument("--cwd", help="Working directory for the command")
    exec_parser.add_argument("--command-timeout", type=int, default=60, help="Shell command timeout in seconds")
    exec_parser.add_argument("--risk", choices=["low", "medium", "high", "critical"], help="Override the inferred command risk")
    exec_parser.add_argument("--yes", action="store_true", help="Auto-approve high-risk commands")
    exec_parser.add_argument("--plan-id", help="Optional related plan identifier")
    exec_parser.add_argument("--task-id", help="Optional related task identifier")
    exec_parser.add_argument("shell_command", nargs=argparse.REMAINDER, help="Command to execute; prefix with -- to stop option parsing")

    edit_parser = subparsers.add_parser("edit", help="Apply a text edit with diff preview support")
    edit_parser.add_argument("path", help="File path to edit")
    edit_parser.add_argument("--replace", nargs=2, metavar=("OLD", "NEW"), help="Replace text in the file")
    edit_parser.add_argument("--content", help="Replace the full file content (or append with --append)")
    edit_parser.add_argument("--append", action="store_true", help="Append content instead of replacing the file")
    edit_parser.add_argument("--dry-run", action="store_true", help="Preview the diff without writing the file")
    edit_parser.add_argument("--risk", choices=["low", "medium", "high", "critical"], help="Override the inferred edit risk")
    edit_parser.add_argument("--yes", action="store_true", help="Auto-approve high-risk edits")
    edit_parser.add_argument("--plan-id", help="Optional related plan identifier")
    edit_parser.add_argument("--task-id", help="Optional related task identifier")
    subparsers.add_parser("status", help="Show version, server health, and token status")
    subparsers.add_parser("update", help="Upgrade openclaw to the latest version from PyPI")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    raw_argv = list(argv if argv is not None else sys.argv[1:])
    known_commands = {
        "ask",
        "chat",
        "health",
        "auth",
        "analyze",
        "session",
        "plan",
        "research",
        "write",
        "watch",
        "exec",
        "edit",
        "update",
        "status",
    }

    # Skip background update check when the user is explicitly running `openclaw update`.
    _skip_update_check = bool(raw_argv and raw_argv[0] == "update")
    if not _skip_update_check:
        # Run update check synchronously in a thread so we can join it before
        # drawing the readline prompt. The thread only sets _latest_version;
        # we print the notice in the main thread after joining so it never
        # appears interleaved with the REPL prompt.
        def _update_check_worker() -> None:
            install_dir = _standalone_install_dir()
            if install_dir:
                # Standalone: compare file hashes against the server
                try:
                    import hashlib
                    base_url = os.getenv("OPENCLAW_URL", "http://192.168.1.93:8765").rstrip("/")
                    url = f"{base_url}/cli-update/meta"
                    import urllib.request as _ur
                    with _ur.urlopen(_ur.Request(url), timeout=3.0) as resp:
                        server_hashes: dict[str, str] = json.loads(resp.read())
                    for fname, server_hash in server_hashes.items():
                        local_path = Path(install_dir) / fname
                        if local_path.exists():
                            local_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
                            if local_hash != server_hash:
                                _update_mod._standalone_needs_update = True
                                break
                        else:
                            _update_mod._standalone_needs_update = True
                            break
                except Exception:  # noqa: BLE001  # background update check; non-critical
                    pass
                latest = _fetch_latest_pypi_version(timeout=3.0)
                if latest:
                    _update_mod._latest_version = latest

        _update_thread: threading.Thread | None = threading.Thread(
            target=_update_check_worker, daemon=True
        )
        _update_thread.start()
    else:
        _update_thread = None

    if raw_argv and raw_argv[0] not in known_commands and not raw_argv[0].startswith("-"):
        raw_argv = ["ask", *raw_argv]
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    command = "health" if getattr(args, "health", False) else args.command or "chat"

    # Wait for the update check then print the notice from the main thread,
    # guaranteeing it appears before any output (including the REPL prompt).
    if _update_thread is not None:
        _update_thread.join(timeout=3.5)
    current_ver = cli_version()
    if _update_mod._standalone_needs_update:
        _print_update_notice(current_ver, None)  # standalone: no version string
    elif _update_mod._latest_version and _version_tuple(_update_mod._latest_version) > _version_tuple(current_ver):
        _print_update_notice(current_ver, _update_mod._latest_version)

    try:
        if command == "auth":
            return handle_auth_command(args)
        if command == "session":
            return handle_session_command(args)
        if command == "update":
            return handle_update_command(args)
        config = build_config(args)
        if command == "status":
            return handle_status_command(args, config=config)
        if command in {"ask", "chat"} and config.session_id:
            require_session(config.session_id)

        if command in {"ask", "chat", "analyze", "write", "watch"}:
            maybe_warn_missing_token(config)
        if command == "chat":
            session = None
            if config.session_id:
                session = require_session(config.session_id)
            elif getattr(args, "session", ""):
                session = require_session(getattr(args, "session", ""))
            session_id = session.session_id if session else ""
            scoped_config = bind_config_to_session(config, session_id) if session_id else config
            if session_id:
                _chat_kwargs: dict[str, Any] = {"session_id": session_id}
                if getattr(args, "no_banner", False):
                    _chat_kwargs["no_banner"] = True
                return run_chat(scoped_config, **_chat_kwargs)
            _chat_kwargs = {}
            if getattr(args, "no_banner", False):
                _chat_kwargs["no_banner"] = True
            return run_chat(scoped_config, **_chat_kwargs)
        if command == "health":
            health = fetch_health(config=config)
            print_health(health, output_json=config.output_json)
            return 0
        if command == "plan":
            return handle_plan_command(args, session_id=config.session_id)
        if command == "analyze":
            return handle_analyze_command(args, config=config)
        if command == "research":
            return handle_research_command(args)
        if command == "write":
            return handle_write_command(args, config=config)
        if command == "watch":
            return handle_watch_command(args, config=config)
        if command == "exec":
            return handle_exec_command(args)
        if command == "edit":
            return handle_edit_command(args)

        prompt = parse_prompt(args.prompt)
        if not prompt:
            parser.error("prompt is required unless you pipe text on stdin")
        history = load_conversation_history(config.session_id) if config.session_id else None
        if history is None:
            response = _with_spinner("💬 Thinking…", invoke_openclaw, prompt, config=config, output_json=config.output_json)
        else:
            response = _with_spinner("💬 Thinking…", invoke_openclaw, prompt, config=config, history=history, output_json=config.output_json)
        print_response(response, output_json=config.output_json)
        if config.session_id:
            append_event(config.session_id, kind="prompt", content=prompt, metadata={"summary": prompt})
            persist_response(config.session_id, prompt, response.response)
        return 0
    except OpenClawCliError as exc:
        _base = ""
        try:
            _base = config.base_url
        except Exception:  # noqa: BLE001  # best-effort base_url access for error display
            pass
        _print_connection_error_panel(str(exc), base_url=_base)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
