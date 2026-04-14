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

import openclaw_cli_render as _render_mod
import openclaw_cli_path_utils as _path_utils

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
_CLI_BUILD = "wave35"  # updated with each UX wave batch

_OPENCLAW_TIPS = [
    "Press Tab after / to auto-complete slash commands.",
    "Use /recall 1 to instantly re-send your last prompt.",
    "Rate responses with /rate 5 to trigger a 🎉 celebration!",
    "Try /palette edit to find all editing-related commands.",
    "Use /histsearch <query> to find any past prompt instantly.",
    "Customize your prompt with /prompt {build} ❯",
    "Use /separator none to remove the separator between responses.",
    "Try /autobold off if you find the auto-bolding distracting.",
    "Use /top to see your most-used commands and prompts.",
    "Chain commands with /macro save to automate workflows.",
    "Use /pin <key> <value> to save quick-reference data.",
    "Type /shortcuts to see all keyboard shortcuts at a glance.",
    "Use /heatmap to discover your peak usage hours.",
    "Try /quality to see a colorful histogram of your ratings.",
    "Use /export to save your session to a file.",
    "Use /streak to track your consecutive high-rating streak.",
    "Use /tokeninfo to see estimated token usage this session.",
    "Try /emojiheaders off for a cleaner heading style.",
    "Use /links off if your terminal doesn't support clickable URLs.",
    "Use /pathhints off to disable file path quick-action hints.",
    "Try /celebrate Woohoo! for a surprise animation.",
    "Use /freq to analyze which slash commands you use most.",
    "Use /histsearch to find any prompt you've ever typed.",
    "The /stats command shows bar charts of your usage patterns.",
    "Use /plain for maximum compatibility on any terminal.",
]
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
# User preferences — theme, emoji, layout
# ---------------------------------------------------------------------------
_OPENCLAW_DIR = Path.home() / ".openclaw"
_PREFS_FILE = _OPENCLAW_DIR / "prefs.json"

_PREFS: dict[str, Any] = {
    "theme": "default",   # separator / accent colour
    "emoji": True,         # show emoji in UI (False → ASCII fallbacks)
    "emoji_pack": "classic",  # "classic" | "minimal" | "ascii"
    "layout": "normal",   # "compact" | "normal" | "verbose" | "plain"
    "layout_preset": "",  # "" | "focus" | "watch-monitor" | "handoff"
    "layout_focus": "primary",  # "primary" | "supporting"
    "interactive_overlays": False,  # opt-in interactive pickers for supported list commands
    "emoji_headers": True,  # prepend emoji to markdown headings in AI responses
}

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

# Accessibility mode keys in _PREFS
_A11Y_REDUCED_MOTION = "reduced_motion"   # bool: disable spinner/animations
_A11Y_PLAIN_MODE = "plain_mode"            # bool: simplify chrome to plain text
_A11Y_HIGH_CONTRAST = "high_contrast"     # bool: high-contrast colour palette

# Maps theme name → Rich rule style + ANSI accent escape code
_THEMES: dict[str, tuple[str, str]] = {
    "default":  ("dim blue",    "\033[2;34m"),
    "green":    ("dim green",   "\033[2;32m"),
    "yellow":   ("dim yellow",  "\033[2;33m"),
    "magenta":  ("dim magenta", "\033[2;35m"),
    "cyan":     ("dim cyan",    "\033[2;36m"),
    "mono":     ("dim",         "\033[2m"),
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

_THEME_ORDER: tuple[str, ...] = tuple(_THEMES.keys())
_THEME_DESCRIPTIONS: dict[str, str] = {
    "default": "balanced blue accents",
    "green": "success-forward green accents",
    "yellow": "warm amber accents",
    "magenta": "vivid magenta accents",
    "cyan": "cool cyan accents",
    "mono": "neutral monochrome accents",
}
_THEME_ALIASES: dict[str, str] = {
    "blue": "default",
    "classic": "default",
    "amber": "yellow",
    "purple": "magenta",
    "teal": "cyan",
    "gray": "mono",
    "grey": "mono",
}
_EMOJI_PACKS: dict[str, dict[str, str]] = {
    "classic": {},
    "minimal": {
        "🦞": "[oc]",
        "💬": "[chat]",
        "📍": "[pin]",
        "💡": "[tip]",
        "📎": "[src]",
        "⌨": "[kbd]",
        "⏱": "[time]",
        "🗂": "[sess]",
        "👤": "[you]",
        "⚡": "[!]",
        "🟢": "[ok]",
        "🔵": "[run]",
        "🟡": "[warn]",
        "🔴": "[err]",
        "⏸": "[pause]",
        "⏳": "[wait]",
        "●": "[*]",
        "✅": "[ok]",
        "⚠️": "[warn]",
    },
    "ascii": {},
}


def _load_prefs() -> None:
    """Load user preferences from ~/.openclaw/prefs.json (silently ignores errors)."""
    try:
        prefs_file = _prefs_file_path()
        if prefs_file.exists():
            data = json.loads(prefs_file.read_text("utf-8"))
            if isinstance(data, dict):
                _PREFS.update(data)
                _normalize_personalization_prefs()
    except (OSError, json.JSONDecodeError):
        pass


def _save_prefs() -> None:
    """Persist user preferences to ~/.openclaw/prefs.json (silently ignores errors)."""
    try:
        _normalize_personalization_prefs()
        prefs_dir = _prefs_dir_path()
        prefs_file = _prefs_file_path()
        prefs_dir.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text(json.dumps(_PREFS, indent=2), "utf-8")
    except OSError:
        pass


def _prefs_set(key: str, value: object) -> None:
    """Set a single preference key and persist immediately."""
    _PREFS[key] = value
    _save_prefs()


def _prefs_dir_path() -> Path:
    """Return the preference directory, honoring test overrides when present."""
    override = os.environ.get("OPENCLAW_CLI_HOME")
    if override:
        return Path(override).expanduser() / ".openclaw"
    return _OPENCLAW_DIR


def _prefs_file_path() -> Path:
    """Return the preference file path."""
    return _prefs_dir_path() / "prefs.json"


def _normalize_theme_name(value: Any) -> str:
    """Normalize a theme preference or user-supplied theme token."""
    token = str(value or "default").strip().lower()
    token = _THEME_ALIASES.get(token, token)
    if token not in _THEMES:
        return "default"
    return token


def _emoji_pack_name() -> str:
    """Return the active emoji pack name with legacy bool migration."""
    pack = str(_PREFS.get("emoji_pack", "") or "").strip().lower()
    if pack in _EMOJI_PACKS:
        return pack
    if _PREFS.get("emoji", True):
        return "classic"
    return "ascii"


def _normalize_personalization_prefs() -> None:
    """Clamp personalization preferences to known-safe values."""
    _PREFS["theme"] = _normalize_theme_name(_PREFS.get("theme", "default"))
    layout = str(_PREFS.get("layout", "normal") or "normal").strip().lower()
    if layout not in {"compact", "normal", "verbose", "plain"}:
        layout = "normal"
    _PREFS["layout"] = layout
    preset = str(_PREFS.get("layout_preset", "") or "").strip().lower()
    preset = {
        "watch": "watch-monitor",
        "monitor": "watch-monitor",
        "collab": "handoff",
        "collaboration": "handoff",
    }.get(preset, preset)
    if preset not in {"", "focus", "watch-monitor", "handoff"}:
        preset = ""
    _PREFS["layout_preset"] = preset
    focus = str(_PREFS.get("layout_focus", "primary") or "primary").strip().lower()
    if focus not in {"primary", "supporting"}:
        focus = "primary"
    _PREFS["layout_focus"] = focus
    pack = _emoji_pack_name()
    _PREFS["emoji_pack"] = pack
    _PREFS["emoji"] = pack != "ascii"
    _PREFS["interactive_overlays"] = bool(_PREFS.get("interactive_overlays", False))
    for key in (_A11Y_REDUCED_MOTION, _A11Y_PLAIN_MODE, _A11Y_HIGH_CONTRAST):
        if key in _PREFS:
            _PREFS[key] = bool(_PREFS.get(key, False))


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
    except Exception:
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
    layout = str(_PREFS.get("layout", "normal") or "normal").strip().lower()
    if layout not in {"compact", "normal", "verbose", "plain"}:
        return "normal"
    return layout


def _layout_preset_name() -> str:
    """Return the normalized active layout preset name, if any."""
    preset = str(_PREFS.get("layout_preset", "") or "").strip().lower()
    return preset if preset in {"focus", "watch-monitor", "handoff"} else ""


def _layout_focus_name() -> str:
    """Return the active pane within the current layout preset."""
    focus = str(_PREFS.get("layout_focus", "primary") or "primary").strip().lower()
    return focus if focus in {"primary", "supporting"} else "primary"


def _layout_preset_config(name: str = "") -> dict[str, str]:
    """Return the documented surface pairing for a layout preset."""
    preset = name or _layout_preset_name()
    return {
        "focus": {
            "label": "focus",
            "primary": "/session",
            "supporting": "/context",
        },
        "watch-monitor": {
            "label": "watch-monitor",
            "primary": "/watch status",
            "supporting": "/watch history + /outputs",
        },
        "handoff": {
            "label": "handoff",
            "primary": "/collab",
            "supporting": "session summary + recent outputs",
        },
    }.get(preset, {})


def _layout_preset_fallback(*, width: int | None = None, is_tty: bool | None = None) -> str:
    """Return the current preset rendering fallback label."""
    if not _layout_preset_name():
        return "single-pane"
    tty = _get_is_tty() if is_tty is None else bool(is_tty)
    cols = _terminal_width() if width is None else int(width)
    if not tty or _a11y_plain_mode() or cols < 100:
        return "single-pane"
    if cols < 140:
        return "stacked"
    return "multi-pane"


def _layout_pane_line_limit() -> int:
    """Return the maximum number of lines shown per preset pane."""
    return {
        "compact": 6,
        "normal": 9,
        "verbose": 14,
        "plain": 9,
    }.get(_effective_layout_mode(), 9)


def _layout_pane_block(title: str, lines: list[str], *, active: bool = False) -> list[str]:
    """Return a bounded plain-text pane block for workspace presets."""
    clean = [str(line).strip() for line in lines if str(line or "").strip()]
    limit = _layout_pane_line_limit()
    clipped = clean[:limit]
    if len(clean) > limit:
        clipped.append(f"… {len(clean) - limit} more line(s); open the source surface for full detail")
    status = "ACTIVE" if active else "READY"
    return [f"{status} · {title}"] + [f"  {line}" for line in clipped]


def _layout_column_lines(left: list[str], right: list[str], *, width: int) -> list[str]:
    """Lay out two pane blocks side-by-side using safe plain text."""
    separator = " │ "
    column_width = max(28, (max(width, 72) - len(separator)) // 2)

    def _wrap(block: list[str]) -> list[str]:
        rows: list[str] = []
        for line in block:
            rows.extend(
                textwrap.wrap(
                    str(line),
                    width=column_width,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                or [""]
            )
        return rows

    left_rows = _wrap(left)
    right_rows = _wrap(right)
    total_rows = max(len(left_rows), len(right_rows))
    merged: list[str] = []
    for index in range(total_rows):
        left_line = left_rows[index] if index < len(left_rows) else ""
        right_line = right_rows[index] if index < len(right_rows) else ""
        merged.append(f"{left_line:<{column_width}}{separator}{right_line:<{column_width}}".rstrip())
    return merged


def _layout_outputs_lines(session_id: str) -> list[str]:
    """Return compact recent-output lines for layout presets."""
    outputs = list_saved_outputs(session_id, limit=3)
    if not outputs:
        return [
            _status_cell("idle", detail="no saved outputs"),
            "/outputs to inspect artifacts once something is saved",
        ]
    lines = [
        _progress_cell("artifacts", str(len(list_saved_outputs(session_id, limit=0))), status="complete"),
    ]
    preview = load_saved_output_preview(session_id, "1", max_chars=OUTPUT_DASHBOARD_EXCERPT_CHARS)
    if preview:
        lines.append(
            f"focused preview: {str(preview.get('name') or '').strip()} · "
            f"{_format_byte_count(int(preview.get('size_bytes') or 0))}"
        )
        lines.extend(_preview_block_lines("excerpt", str(preview.get("preview") or ""), max_chars=OUTPUT_DASHBOARD_EXCERPT_CHARS))
    for index, item in enumerate(outputs, start=1):
        lines.append(
            f"{index}. {str(item.get('name') or '').strip()} · "
            f"{_format_byte_count(int(item.get('size_bytes') or 0))}"
        )
    return lines


def _layout_collab_lines(session_id: str) -> list[str]:
    """Return collaboration snapshot lines for layout presets."""
    snapshot = build_collaboration_snapshot(session_id, limit=3)
    actors = list(snapshot.get("actors") or [])
    decisions = list(snapshot.get("recent_decisions") or [])
    notes = list(snapshot.get("recent_notes") or [])
    latest_handoff = snapshot.get("latest_handoff") or {}
    lines = [
        _progress_cell("actors", str(len(actors)), status="info" if actors else "idle"),
        _progress_cell("decisions", str(len(decisions)), status="complete" if decisions else "idle"),
    ]
    for actor in actors[:2]:
        lines.append(
            f"actor: {str(actor.get('name') or 'operator').strip()} · "
            f"{int(actor.get('event_count') or 0)} touchpoints"
        )
    if decisions:
        lines.append(f"decision: {_single_line_excerpt(_format_collaboration_entry(decisions[0]), max_chars=96)}")
    if notes:
        lines.append(f"note: {_single_line_excerpt(_format_collaboration_entry(notes[0]), max_chars=96)}")
    if latest_handoff:
        lines.append(
            f"handoff: {str(latest_handoff.get('id') or '').strip()} · "
            f"{str(latest_handoff.get('created_at') or '').strip()}"
        )
    lines.append("/collab share to print the full handoff bundle")
    return lines


def _layout_watch_lines(state: dict[str, Any] | None) -> list[str]:
    """Return watch-monitor lines for layout presets."""
    if not state:
        return [
            _status_cell("idle", detail="no active watch"),
            "Start one with: openclaw watch --goal …",
            "/watch status to inspect the live control tower when a watch exists",
        ]
    state = normalize_watch_state(state)
    timing = _watch_timing_summary(state)
    lines = [
        _progress_cell("status", str(state.get("status") or "active"), status=str(state.get("status") or "active")),
        _progress_cell("polls", f"{int(state.get('poll_count') or 0)}/{int(state.get('max_polls') or 0) or '∞'}", status=str(state.get("status") or "active")),
    ]
    goal = str(state.get("goal") or "").strip()
    if goal:
        lines.append(f"goal: {_single_line_excerpt(goal, max_chars=96)}")
    if timing["active_phase"]:
        phase_line = timing["active_phase"]
        if timing["active_phase_elapsed"] is not None:
            phase_line += f" · {_format_elapsed_compact(timing['active_phase_elapsed'])}"
        lines.append(_progress_cell("phase", phase_line, status="active"))
    lines.extend(_watch_focus_lines(state)[:4])
    progress_log = list(state.get("progress_log") or [])
    if progress_log:
        latest = progress_log[-1]
        note = str(latest.get("note") or latest.get("summary") or latest.get("content") or "").strip()
        if note:
            lines.append(f"latest checkpoint: {_single_line_excerpt(note, max_chars=96)}")
    lines.append("/watch intervene <msg> to leave an operator breadcrumb")
    return lines


def _layout_session_lines(session: SessionSummary) -> list[str]:
    """Return session health lines for layout presets."""
    lines = [
        session.title or session.session_id,
        _progress_cell("status", str(session.status or "active"), status=session.status or "active"),
        _progress_cell("updated", session.updated_at or "—", status="info"),
        _progress_cell("files", str(len(session.files or [])), status="active" if session.files else "idle"),
    ]
    if session.cwd:
        lines.append(f"cwd: {session.cwd}")
    if session.plan_id:
        lines.append(f"plan: {session.plan_id}")
    if session.task_id:
        lines.append(f"task: {session.task_id}")
    lines.extend(_session_preview_lines(session))
    return lines


def _print_layout_preset_workspace(ctx: "ChatCommandContext") -> None:
    """Render the active layout preset as a pane-like workspace view."""
    preset = _layout_preset_name()
    if not preset:
        print("Workspace preset is single-pane. Use /layout preset focus|watch-monitor|handoff to opt in.")
        return
    session_id = str(ctx.session_id or "").strip()
    if not session_id:
        print(f"Workspace preset {_layout_preset_config(preset).get('label', preset)} saved. Resume a session, then run /layout show.")
        return
    session = load_session(session_id)
    if session is None:
        print(f"Workspace preset {_layout_preset_config(preset).get('label', preset)} saved. Resume a session, then run /layout show.")
        return

    focus = _layout_focus_name()
    watch_state = load_watch_state(session.session_id)
    if preset == "focus":
        primary_title = "Session summary"
        primary_lines = _layout_session_lines(session)
        if watch_state:
            supporting_title = "Watch monitor"
            supporting_lines = _layout_watch_lines(watch_state)
        elif session.output_count:
            supporting_title = "Artifact preview"
            supporting_lines = _layout_outputs_lines(session.session_id)
        else:
            supporting_title = "Collaboration snapshot"
            supporting_lines = _layout_collab_lines(session.session_id)
    elif preset == "watch-monitor":
        primary_title = "Watch monitor"
        primary_lines = _layout_watch_lines(watch_state)
        supporting_title = "Recent artifacts"
        supporting_lines = _layout_outputs_lines(session.session_id)
    else:
        primary_title = "Collaboration snapshot"
        primary_lines = _layout_collab_lines(session.session_id)
        supporting_title = "Session health"
        supporting_lines = _layout_session_lines(session)

    render_mode = _layout_preset_fallback()
    width = _terminal_width(fallback=100)
    header = [
        f"Workspace preset: {_layout_preset_config(preset).get('label', preset)}",
        f"Render mode: {render_mode}",
        f"Active pane: {focus}",
        "",
    ]
    primary_block = _layout_pane_block(primary_title, primary_lines, active=focus == "primary")
    supporting_block = _layout_pane_block(supporting_title, supporting_lines, active=focus == "supporting")
    if render_mode == "multi-pane":
        body = _layout_column_lines(primary_block, supporting_block, width=width)
    elif render_mode == "stacked":
        body = [*primary_block, "", *supporting_block]
    else:
        active_block = primary_block if focus == "primary" else supporting_block
        collapsed = supporting_title if focus == "primary" else primary_title
        body = [
            *active_block,
            "",
            f"Supporting pane collapsed. Open {collapsed.lower()} via its source command or widen the terminal.",
        ]
    print("\n".join(header + body))


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
    if not _premium_motion_active():
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


def _response_footer_lines(*, elapsed: float = 0.0, tokens: int = 0, model: str = "") -> tuple[str, str]:
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
        headline = f"{_e('✨', '[done]')} Response complete in {elapsed:.1f}s"
    else:
        headline = f"{_e('✨', '[done]')} Response complete"
    return headline, detail


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
    """Run *fn* in a background thread while showing an animated braille spinner.

    Falls back to a direct call when output is not a TTY or when --json output
    is requested so that machine-readable output is never corrupted.

    When reduced-motion mode is active, skips the animation and prints a single
    static "thinking..." line instead, then runs *fn* directly.
    """
    is_tty = _get_is_tty()
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
    heartbeat_every = max(0.01, float(_SPINNER_HEARTBEAT_SECONDS))

    # Reduced-motion path: no animation, but still emit periodic liveness cues.
    if _a11y_reduced_motion():
        snapshot = _spinner_progress_snapshot(0.0)
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


TRANSIENT_WATCH_ERROR_MARKERS = (
    "timed out",
    "timeout",
    "unable to reach",
    "connection refused",
    "refused the connection",
    "temporarily unavailable",
    "temporary failure",
    "network is unreachable",
    "connection reset",
    "connection aborted",
    "remote end closed connection",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)


@dataclass
class AskResponse:
    """Structured response from the OpenClaw ask API."""

    response: str
    model: str
    tokens: int
    raw: dict[str, Any]


@dataclass
class HealthResponse:
    """Structured response from the OpenClaw health endpoint."""

    payload: Any
    raw_text: str
    status: str = ""
    healthy: bool | None = None


@dataclass(frozen=True)
class LocalLinkValidation:
    """Result of checking a plan/task identifier against local on-disk sources."""

    kind: str
    item_id: str
    available: bool
    exists: bool = False
    source: str = ""
    summary: str = ""


# ReplRouteStepContext, ReplRouteGrounding — imported from openclaw_cli_router above.


@dataclass
class CliConfig:
    """Resolved runtime configuration for a CLI invocation."""

    base_url: str
    token: str
    model: str
    timeout_seconds: int
    user_name: str
    client_name: str
    output_json: bool = False
    session_id: str = ""
    no_stream: bool = False


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
    try:
        watch_state = load_watch_state(session.session_id)
    except Exception:
        watch_state = None
    snapshot = build_collaboration_snapshot(session.session_id, limit=3)
    mood = _session_mood_snapshot(session, watch_state=watch_state, collaboration_snapshot=snapshot)
    operator_snapshot = _session_operator_snapshot(
        session,
        watch_state=watch_state,
        collaboration_snapshot=snapshot,
    )
    parts = [
        f"session: {session.session_id}",
        f"title: {session.title}",
        _progress_cell("status", str(session.status or "active"), status=session.status or "active"),
        f"cwd: {session.cwd}",
        f"updated: {session.updated_at}",
        f"freshness: {'stale' if _session_is_stale(session) else 'fresh'}",
        _progress_cell("commands", str(session.command_count), status="active" if session.command_count else "idle"),
        _progress_cell("outputs", str(session.output_count), status="complete" if session.output_count else "idle"),
    ]
    mood_cell = _session_mood_cell(mood)
    if mood_cell:
        parts.append(mood_cell)
    parts.extend(_operator_snapshot_lines(operator_snapshot)[:4])
    if session.plan_id:
        parts.append(f"plan: {session.plan_id}")
    if session.task_id:
        parts.append(f"task: {session.task_id}")
    if session.files:
        parts.append("files: " + ", ".join(session.files[:6]))
    if session.last_summary:
        parts.append(f"last: {session.last_summary}")
    if session.automation_mode:
        status = session.automation_status or "active"
        parts.append(_progress_cell("automation", f"{session.automation_mode} ({status})", status=status))
        if watch_state:
            timing = _watch_timing_summary(watch_state)
            timing_parts = []
            if timing["active_phase"]:
                detail = f"{timing['active_phase']}"
                if timing["active_phase_elapsed"] is not None:
                    detail += f" {_format_elapsed_compact(timing['active_phase_elapsed'])}"
                timing_parts.append(f"phase {detail}")
            if timing["latest_duration"] is not None:
                timing_parts.append(f"last run {_format_elapsed_compact(timing['latest_duration'])}")
            if timing["retry_delay_total"]:
                timing_parts.append(f"retry backoff {_format_elapsed_compact(timing['retry_delay_total'])}")
            if timing_parts:
                parts.append("timing: " + " · ".join(timing_parts))
    if session.checkpoint_count:
        parts.append(_progress_cell("checkpoints", str(session.checkpoint_count), status="complete"))
    if session.last_checkpoint_at:
        parts.append(f"last checkpoint: {session.last_checkpoint_at}")
    return "\n".join(parts)


def _print_session_summary(session: SessionSummary) -> None:
    """Print a compact session summary, with rich formatting when available."""
    watch_state = None
    try:
        watch_state = load_watch_state(session.session_id)
    except Exception:
        watch_state = None
    snapshot = build_collaboration_snapshot(session.session_id, limit=3)
    story = build_session_storyline(session.session_id, limit=4)
    mood = _session_mood_snapshot(session, watch_state=watch_state, collaboration_snapshot=snapshot)
    operator_snapshot = _session_operator_snapshot(
        session,
        watch_state=watch_state,
        collaboration_snapshot=snapshot,
    )

    summary_lines = [
        session.title,
        f"id {session.session_id}",
        _progress_cell("status", str(session.status or "active"), status=session.status or "active"),
        _status_cell("stale" if _session_is_stale(session) else "info", detail="freshness"),
        _progress_cell("updated", session.updated_at or "—", status="info"),
    ]
    mood_cell = _session_mood_cell(mood, rich=_RICH_AVAILABLE and _IS_TTY)
    if mood_cell:
        summary_lines.append(mood_cell)
    detail_lines = [
        f"story: {story.get('headline', '')}" if story.get("headline") else "",
        f"chapter: {story.get('chapter_title', '')} · {story.get('chapter_detail', '')}" if story.get("chapter_title") else "",
        _progress_cell("commands", str(session.command_count), status="active" if session.command_count else "idle"),
        _progress_cell("outputs", str(session.output_count), status="complete" if session.output_count else "idle"),
        _progress_cell("checkpoints", str(session.checkpoint_count), status="complete" if session.checkpoint_count else "idle"),
        f"cwd: {session.cwd}" if session.cwd else "",
        f"plan: {session.plan_id}" if session.plan_id else "",
        f"task: {session.task_id}" if session.task_id else "",
        (
            "files: "
            + ", ".join(session.files[:4])
            + ("…" if len(session.files) > 4 else "")
        )
        if session.files
        else "files: none tracked",
        f"last: {session.last_summary[:100]}" if session.last_summary else "",
    ]
    detail_lines.extend(_operator_snapshot_lines(operator_snapshot)[:5])
    for milestone in list(story.get("milestones") or [])[:2]:
        detail_lines.append(f"milestone: {milestone}")
    action_lines = []
    if session.automation_mode:
        a_status = session.automation_status or "active"
        detail_lines.append(_progress_cell("automation", f"{session.automation_mode} ({a_status})", status=a_status))
        if watch_state:
            timing = _watch_timing_summary(watch_state)
            polls = int(watch_state.get("poll_count") or 0)
            max_polls = int(watch_state.get("max_polls") or 0)
            failures = int(watch_state.get("failure_count") or 0)
            retry_limit = int(watch_state.get("retry_limit") or 3)
            detail_lines.append(_progress_cell("polls", f"{polls}/{max_polls or '∞'}", status=a_status))
            if failures:
                detail_lines.append(_progress_cell("failures", f"{failures}/{retry_limit}", status="retry"))
            if timing["active_phase"]:
                phase = timing["active_phase"]
                if timing["active_phase_elapsed"] is not None:
                    phase += f" {_format_elapsed_compact(timing['active_phase_elapsed'])}"
                detail_lines.append(_progress_cell("phase", phase, status="active"))
            if timing["latest_duration"] is not None:
                detail_lines.append(f"last run {_format_elapsed_compact(timing['latest_duration'])}")
            if timing["retry_delay_total"]:
                detail_lines.append(f"retry backoff {_format_elapsed_compact(timing['retry_delay_total'])}")
            last_error = str(watch_state.get("last_error") or "").strip()
            if last_error:
                detail_lines.append(f"last error: {last_error[:80]}")
        action_lines.append("/watch status to inspect the live control tower")
        action_lines.append("/watch history to review retries and checkpoints")
        if watch_state and (watch_state.get("last_error") or int(watch_state.get("failure_count") or 0) > 0):
            action_lines.append('/watch intervene "recovery note" to steer the next retry')
        if watch_state and list(watch_state.get("interventions") or []):
            action_lines.append("/collab share to copy the latest operator-visible snapshot")
    elif session.output_count:
        action_lines.append("/outputs 1 to inspect the newest saved output")
        if session.output_count > 1:
            action_lines.append("/outputs overlay to jump through saved artifacts")
    elif session.files:
        action_lines.append("/context to preview the next request grounding")
    else:
        action_lines.append("/files add <path> to attach workspace context")
    if session.plan_id or session.task_id:
        action_lines.append("/context to verify linked plan/task grounding")
    action_lines.append("/collab to copy the read-only operator snapshot")
    action_lines = _dedupe_preserve_order(action_lines)
    if session.last_checkpoint_at:
        detail_lines.append(f"last checkpoint: {session.last_checkpoint_at}")

    _print_dashboard_surface(
        "Session Dashboard",
        summary_lines=summary_lines,
        detail_lines=detail_lines,
        action_lines=action_lines,
        border_style="cyan",
    )


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


def _watch_focus_lines(state: dict[str, Any]) -> list[str]:
    state = normalize_watch_state(state)
    timing = _watch_timing_summary(state)
    lines: list[str] = []
    active_checkpoint = state.get("active_checkpoint") or {}
    if timing["active_phase"]:
        phase_line = timing["active_phase"]
        if timing["active_phase_elapsed"] is not None:
            phase_line += f" · {_format_elapsed_compact(timing['active_phase_elapsed'])}"
        lines.append(f"focus: {_progress_cell('phase', phase_line, status='active')}")
    latest_checkpoint = None
    checkpoints = list(state.get("checkpoints") or [])
    if checkpoints:
        latest_checkpoint = checkpoints[-1]
    if not latest_checkpoint and active_checkpoint:
        latest_checkpoint = active_checkpoint
    if latest_checkpoint:
        poll_value = latest_checkpoint.get("poll")
        note = str(
            latest_checkpoint.get("note")
            or latest_checkpoint.get("summary")
            or latest_checkpoint.get("status")
            or latest_checkpoint.get("phase")
            or ""
        ).strip()
        checkpoint_label = f"checkpoint {poll_value}" if poll_value else "checkpoint"
        if note:
            lines.append(f"{checkpoint_label}: {_single_line_excerpt(note, max_chars=WATCH_FOCUS_NOTE_CHARS)}")
    interventions = [item for item in list(state.get("interventions") or []) if isinstance(item, dict)]
    if interventions:
        latest = interventions[-1]
        action = str(latest.get("action") or "intervention").strip().replace("-", " ")
        reason = _single_line_excerpt(str(latest.get("reason") or "").strip(), max_chars=WATCH_FOCUS_NOTE_CHARS)
        status = str(latest.get("status") or "info").strip()
        detail = action if not reason else f"{action} · {reason}"
        lines.append(f"intervention: {_status_cell(status if status != 'pending' else 'info', detail=detail)}")
    last_error = str(state.get("last_error") or "").strip()
    if last_error:
        lines.append(f"focus error: {_single_line_excerpt(last_error, max_chars=WATCH_FOCUS_NOTE_CHARS)}")
    return lines


def _session_mood_snapshot(
    session: SessionSummary,
    *,
    watch_state: dict[str, Any] | None = None,
    collaboration_snapshot: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Derive a restrained mood/momentum cue from objective session state."""
    try:
        normalized_watch = normalize_watch_state(watch_state or {}) if watch_state else {}
    except Exception:
        normalized_watch = {}
    snapshot = collaboration_snapshot or {}
    actors = [item for item in list(snapshot.get("actors") or []) if isinstance(item, dict)]
    decisions = [item for item in list(snapshot.get("recent_decisions") or []) if isinstance(item, dict)]
    latest_handoff = snapshot.get("latest_handoff") or {}

    outputs = int(session.output_count or 0)
    checkpoints = int(session.checkpoint_count or 0)
    commands = int(session.command_count or 0)
    failures = int(normalized_watch.get("failure_count") or 0)
    watch_status = str(normalized_watch.get("status") or "").strip().lower()
    active_phase = str(_watch_timing_summary(normalized_watch).get("active_phase") or "").strip()
    actor_count = len(actors)

    if (
        session.status in {"complete", "completed"}
        or watch_status in {"complete", "completed"}
        or (outputs > 0 and checkpoints > 0 and commands > 0)
    ):
        detail = "outputs ready to review" if outputs else "checkpoint captured cleanly"
        if actor_count >= 2:
            detail += f" · {actor_count} collaborators in the loop"
        return {
            "status": "complete",
            "label": "milestone",
            "detail": detail,
            "headline": f"milestone: {detail}",
            "share_line": f"momentum   : milestone reached; {detail}",
        }

    if watch_status == "retrying" or failures > 0:
        detail = "recovering with checkpoints" if checkpoints else "retry loop staying engaged"
        if active_phase:
            detail += f" · phase {active_phase}"
        return {
            "status": "retry",
            "label": "resilient",
            "detail": detail,
            "headline": f"mood: resilient recovery · {detail}",
            "share_line": f"momentum   : resilient recovery; {detail}",
        }

    if actor_count >= 2 or decisions or latest_handoff:
        detail = f"{max(actor_count, 1)} collaborators aligned" if actor_count else "handoff context is ready"
        if decisions:
            detail += f" · {len(decisions)} recent decision{'s' if len(decisions) != 1 else ''}"
        return {
            "status": "info",
            "label": "shared",
            "detail": detail,
            "headline": f"mood: shared momentum · {detail}",
            "share_line": f"momentum   : shared momentum; {detail}",
        }

    if commands >= 3 or outputs > 0 or checkpoints > 0:
        detail = "signals are stacking up"
        if outputs > 0:
            detail = f"{outputs} output{'s' if outputs != 1 else ''} landed"
        elif checkpoints > 0:
            detail = f"{checkpoints} checkpoint{'s' if checkpoints != 1 else ''} recorded"
        elif commands >= 3:
            detail = f"{commands} command{'s' if commands != 1 else ''} into the flow"
        return {
            "status": "active",
            "label": "steady",
            "detail": detail,
            "headline": f"mood: building momentum · {detail}",
            "share_line": f"momentum   : building momentum; {detail}",
        }

    return {}


def _session_mood_cell(snapshot: dict[str, str], *, rich: bool = False) -> str:
    """Render a compact mood/momentum cell with text-first fallback."""
    label = str(snapshot.get("label") or "").strip()
    detail = str(snapshot.get("detail") or "").strip()
    if not label:
        return ""
    value = label if not detail else f"{label} · {detail}"
    return _progress_cell("mood", value, status=str(snapshot.get("status") or "info"), rich=rich)


def _session_preview_lines(session: SessionSummary) -> list[str]:
    lines: list[str] = []
    watch_state = None
    story = build_session_storyline(session.session_id, limit=3)
    if story.get("headline"):
        lines.append(f"story: {_single_line_excerpt(str(story.get('headline') or ''), max_chars=100)}")
    if session.last_summary:
        lines.append(f"latest activity: {_single_line_excerpt(session.last_summary, max_chars=100)}")
    if session.automation_mode:
        try:
            watch_state = load_watch_state(session.session_id)
        except Exception:
            watch_state = None
        if watch_state:
            lines.extend(_watch_focus_lines(watch_state)[:2])
    outputs = list_saved_outputs(session.session_id, limit=1)
    if outputs:
        output_item = outputs[0]
        preview = load_saved_output_preview(
            session.session_id,
            str(output_item.get("name") or "").strip(),
            max_chars=SESSION_PREVIEW_OUTPUT_CHARS,
        )
        output_line = f"latest output: {str(output_item.get('name') or '').strip()}"
        if preview:
            excerpt = _single_line_excerpt(str(preview.get("preview") or ""), max_chars=90)
            if excerpt:
                output_line += f" — {excerpt}"
        lines.append(output_line)
    snapshot = build_collaboration_snapshot(session.session_id, limit=3)
    actors = list(snapshot.get("actors") or [])
    decisions = list(snapshot.get("recent_decisions") or [])
    if actors:
        actor_names = ", ".join(str(actor.get("name") or "operator").strip() for actor in actors[:2] if str(actor.get("name") or "").strip())
        if actor_names:
            lines.append(f"collab: {actor_names}")
    if decisions:
        lines.append(f"decision: {_single_line_excerpt(_format_collaboration_entry(decisions[0]), max_chars=100)}")
    mood = _session_mood_snapshot(session, watch_state=watch_state, collaboration_snapshot=snapshot)
    mood_cell = _session_mood_cell(mood)
    if mood_cell:
        lines.append(mood_cell)
    timeline = list(story.get("timeline") or [])
    if timeline:
        lead = timeline[0]
        lines.append(
            f"recap: {str(lead.get('label') or 'update')}: "
            f"{_single_line_excerpt(str(lead.get('summary') or ''), max_chars=88)}"
        )
    return lines[:6]


def _session_operator_snapshot(
    session: SessionSummary,
    *,
    watch_state: dict[str, Any] | None = None,
    collaboration_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a read-only operator snapshot for monitoring and handoff surfaces."""
    try:
        normalized_watch = normalize_watch_state(watch_state or {}) if watch_state else {}
    except Exception:
        normalized_watch = {}
    snapshot = collaboration_snapshot or {}
    decisions = [item for item in list(snapshot.get("recent_decisions") or []) if isinstance(item, dict)]
    notes = [item for item in list(snapshot.get("recent_notes") or []) if isinstance(item, dict)]
    outputs = [item for item in list(snapshot.get("recent_outputs") or []) if isinstance(item, dict)]
    handoff = snapshot.get("latest_handoff") or {}
    interventions = [item for item in list(normalized_watch.get("interventions") or []) if isinstance(item, dict)]
    pending_interventions = [
        item for item in interventions if str(item.get("status") or "").strip().lower() == "pending"
    ]
    watch_status = str(normalized_watch.get("status") or "").strip().lower()
    failures = int(normalized_watch.get("failure_count") or 0)
    fresh = not _session_is_stale(session)
    stop_requested = bool(normalized_watch.get("stop_requested"))

    readiness_status = "info"
    readiness_label = "warming"
    readiness_detail = "local snapshot is still forming"
    if stop_requested:
        readiness_status = "warn"
        readiness_label = "attention"
        readiness_detail = "stop requested; verify the next clean handoff point"
    elif watch_status == "retrying" or failures > 0:
        readiness_status = "retry"
        readiness_label = "attention"
        readiness_detail = "automation is recovering; keep operator eyes on retries"
    elif watch_status in {"running", "active"}:
        readiness_status = "active"
        readiness_label = "live"
        readiness_detail = "watch loop is active; summary is safe to monitor"
    elif outputs or decisions or session.last_summary:
        readiness_status = "complete"
        readiness_label = "handoff-ready"
        readiness_detail = "local read-only snapshot is ready to share"
    elif fresh:
        readiness_status = "active"
        readiness_label = "warming"
        readiness_detail = "fresh session context is available for operators"

    watch_bits: list[str] = []
    if watch_status:
        watch_bits.append(watch_status)
    timing = _watch_timing_summary(normalized_watch) if normalized_watch else {}
    active_phase = str(timing.get("active_phase") or "").strip()
    if active_phase:
        watch_bits.append(active_phase)
    poll_count = int(normalized_watch.get("poll_count") or 0)
    max_polls = int(normalized_watch.get("max_polls") or 0)
    if poll_count or max_polls:
        watch_bits.append(f"{poll_count}/{max_polls or '∞'} polls")

    queue_bits: list[str] = []
    if pending_interventions:
        queue_bits.append(f"{len(pending_interventions)} pending")
    if stop_requested:
        queue_bits.append("stop requested")

    latest_output = str((outputs[0] or {}).get("name") or "").strip() if outputs else ""
    latest_decision = _format_collaboration_entry(decisions[0]) if decisions else ""
    latest_note = _format_collaboration_entry(notes[0]) if notes else ""
    latest_handoff = str(handoff.get("id") or "").strip()

    return {
        "access": "read-only local snapshot",
        "control": "visibility only; no remote control",
        "readiness_status": readiness_status,
        "readiness_label": readiness_label,
        "readiness_detail": readiness_detail,
        "watch_summary": " · ".join(watch_bits),
        "queue_summary": " · ".join(queue_bits),
        "latest_output": latest_output,
        "latest_decision": latest_decision,
        "latest_note": latest_note,
        "latest_handoff": latest_handoff,
    }


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


def _build_session_share_text(session_id: str) -> str:
    snapshot = build_collaboration_snapshot(session_id, limit=5)
    story = build_session_storyline(session_id, limit=5)
    bookmarks = list_session_bookmarks(session_id)
    session_data = snapshot.get("session") or {}
    actors = list(snapshot.get("actors") or [])
    recent_decisions = list(snapshot.get("recent_decisions") or [])
    recent_notes = list(snapshot.get("recent_notes") or [])
    recent_outputs = list(snapshot.get("recent_outputs") or [])
    latest_handoff = snapshot.get("latest_handoff") or {}
    share = snapshot.get("share") or {}
    mood = _session_mood_snapshot(
        require_session(session_id),
        watch_state=load_watch_state(session_id),
        collaboration_snapshot=snapshot,
    )
    operator_snapshot = _session_operator_snapshot(
        require_session(session_id),
        watch_state=load_watch_state(session_id),
        collaboration_snapshot=snapshot,
    )

    lines = [
        "SESSION HANDOFF",
        "-" * 60,
        f"title      : {session_data.get('title', '')}",
        f"session_id : {session_data.get('session_id', session_id)}",
        f"cwd        : {session_data.get('cwd', '')}",
    ]
    plan_id = str(session_data.get("plan_id") or "").strip()
    task_id = str(session_data.get("task_id") or "").strip()
    if plan_id:
        lines.append(f"plan       : {plan_id}")
    if task_id:
        lines.append(f"task       : {task_id}")
    last_summary = str(session_data.get("last_summary") or "").strip()
    if last_summary:
        lines.append(f"summary    : {last_summary}")
    if mood.get("share_line"):
        lines.append(str(mood.get("share_line")))
    if story.get("headline"):
        lines.append(f"story      : {story.get('headline', '')}")
    if story.get("chapter_title"):
        lines.append(f"chapter    : {story.get('chapter_title', '')} · {story.get('chapter_detail', '')}")
    session_tags = [str(tag or "").strip() for tag in list(session_data.get("tags") or []) if str(tag or "").strip()]
    if session_tags:
        lines.append(f"tags       : {', '.join(session_tags[:6])}")
    if actors:
        lines.append("")
        lines.append("ACTORS")
        for actor in actors[:5]:
            lines.append(
                f"  - {actor.get('name', 'operator')} "
                f"({int(actor.get('event_count') or 0)} touchpoints; last {actor.get('last_at', 'n/a')})"
            )
    if recent_decisions:
        lines.append("")
        lines.append("RECENT DECISIONS")
        for entry in recent_decisions[:3]:
            lines.append(f"  - {_format_collaboration_entry(entry)}")
    if recent_notes:
        lines.append("")
        lines.append("RECENT NOTES")
        for entry in recent_notes[:2]:
            lines.append(f"  - {_format_collaboration_entry(entry)}")
    if bookmarks:
        lines.append("")
        lines.append("BOOKMARKS")
        for bookmark in bookmarks[-3:]:
            lines.append(
                "  - "
                f"[{bookmark.get('id', '')}] "
                f"{bookmark.get('label', '')} "
                f"(turn {bookmark.get('turn_index', 0)})"
            )
    if latest_handoff:
        lines.append("")
        lines.append("LATEST HANDOFF")
        lines.append(f"  id   : {latest_handoff.get('id', '')}")
        lines.append(f"  when : {latest_handoff.get('created_at', '')}")
        note = str(latest_handoff.get("note") or "").strip()
        if note:
            lines.append(f"  note : {note}")
    lines.append("")
    lines.append("OPERATOR SNAPSHOT")
    lines.append(f"  access    : {operator_snapshot.get('access', 'read-only local snapshot')}")
    lines.append(f"  control   : {operator_snapshot.get('control', 'visibility only; no remote control')}")
    readiness_label = str(operator_snapshot.get("readiness_label") or "").strip()
    readiness_detail = str(operator_snapshot.get("readiness_detail") or "").strip()
    if readiness_label:
        readiness = readiness_label if not readiness_detail else f"{readiness_label} · {readiness_detail}"
        lines.append(f"  readiness : {readiness}")
    watch_summary = str(operator_snapshot.get("watch_summary") or "").strip()
    if watch_summary:
        lines.append(f"  watch     : {watch_summary}")
    queue_summary = str(operator_snapshot.get("queue_summary") or "").strip()
    if queue_summary:
        lines.append(f"  queue     : {queue_summary}")
    latest_output = str(operator_snapshot.get("latest_output") or "").strip()
    if latest_output:
        lines.append(f"  output    : {latest_output}")
    latest_decision = str(operator_snapshot.get("latest_decision") or "").strip()
    if latest_decision:
        lines.append(f"  decision  : {latest_decision}")
    latest_note = str(operator_snapshot.get("latest_note") or "").strip()
    if latest_note:
        lines.append(f"  note      : {latest_note}")
    if recent_outputs:
        lines.append("")
        lines.append("RECENT OUTPUTS")
        for item in recent_outputs[:3]:
            lines.append(f"  - {item.get('name', '')}")
    milestones = list(story.get("milestones") or [])
    actor_highlights = list(story.get("actor_highlights") or [])
    timeline = list(story.get("timeline") or [])
    if milestones:
        lines.append("")
        lines.append("MILESTONES")
        for item in milestones[:4]:
            lines.append(f"  - {item}")
    if actor_highlights:
        lines.append("")
        lines.append("CAST HIGHLIGHTS")
        for item in actor_highlights[:3]:
            lines.append(f"  - {item}")
    if timeline:
        lines.append("")
        lines.append("TIMELINE RECAP")
        for item in timeline[:4]:
            stamp = str(item.get("timestamp") or "").strip()
            prefix = f"{stamp} · " if stamp else ""
            lines.append(f"  - {prefix}{item.get('label', 'Update')}: {item.get('summary', '')}")
    lines.append("")
    lines.append("COMMANDS")
    lines.append(f"  resume : {share.get('resume_command', f'openclaw --session {session_id}')}")
    lines.append(f"  inspect: {share.get('inspect_command', f'openclaw session show {session_id}')}")
    lines.append(f"  share  : {share.get('share_command', f'openclaw session share {session_id}')}")
    return "\n".join(lines)


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
    resolved = _resolve_runbook_template(template_name)
    if resolved is None:
        valid = ", ".join(sorted(_RUNBOOK_TEMPLATES))
        raise OpenClawCliError(f"Unknown runbook template '{template_name}'. Available: {valid}")
    template_key, template = resolved
    snapshot = build_collaboration_snapshot(session_id, limit=5)
    story = build_session_storyline(session_id, limit=6)
    export = export_session(session_id)
    session_data = export.get("session") or {}
    recent_outputs = list(export.get("outputs") or [])
    recent_decisions = list(snapshot.get("recent_decisions") or [])
    commands = snapshot.get("share") or {}
    plan_id = str(session_data.get("plan_id") or "").strip()
    task_id = str(session_data.get("task_id") or "").strip()

    lines = [
        f"# {template.get('label', 'Runbook')}",
        "",
        f"- **Template:** {template_key}",
        f"- **Audience:** {template.get('audience', 'session review')}",
        f"- **Session:** {session_data.get('title', '') or session_id}",
        f"- **Session ID:** {session_data.get('session_id', session_id)}",
    ]
    cwd = str(session_data.get("cwd") or "").strip()
    if cwd:
        lines.append(f"- **Working directory:** `{cwd}`")
    if plan_id:
        lines.append(f"- **Plan:** `{plan_id}`")
    if task_id:
        lines.append(f"- **Task:** `{task_id}`")
    lines.append("")

    sections = tuple(template.get("sections") or ())
    if "summary" in sections:
        lines.extend(
            [
                "## Summary",
                "",
                f"- **Story:** {story.get('headline', 'Fresh session story is still forming')}",
                f"- **Chapter:** {story.get('chapter_title', 'Session recap')} · {story.get('chapter_detail', '')}",
            ]
        )
        narrative = str(story.get("narrative") or "").strip()
        if narrative:
            lines.append(f"- **Narrative:** {narrative}")
        lines.append("")

    milestones = list(story.get("milestones") or [])
    if "milestones" in sections and milestones:
        lines.append("## Milestones")
        lines.append("")
        lines.extend(f"- {item}" for item in milestones[:5])
        lines.append("")

    if "decisions" in sections and recent_decisions:
        lines.append("## Recent Decisions")
        lines.append("")
        lines.extend(f"- {_format_collaboration_entry(item)}" for item in recent_decisions[:4])
        lines.append("")

    timeline = list(story.get("timeline") or [])
    if "timeline" in sections and timeline:
        lines.append("## Timeline")
        lines.append("")
        for item in timeline[:5]:
            stamp = str(item.get("timestamp") or "").strip()
            prefix = f"{stamp} · " if stamp else ""
            lines.append(f"- {prefix}{item.get('label', 'Update')}: {item.get('summary', '')}")
        lines.append("")

    if "outputs" in sections and recent_outputs:
        lines.append("## Artifacts")
        lines.append("")
        for item in recent_outputs[:5]:
            lines.append(f"- {item.get('name', '')} · {item.get('modified_at', '')}")
        lines.append("")

    if "commands" in sections:
        lines.append("## Next Commands")
        lines.append("")
        lines.append(f"- Resume: `{commands.get('resume_command', f'openclaw --session {session_id}')}`")
        lines.append(f"- Inspect: `{commands.get('inspect_command', f'openclaw session show {session_id}')}`")
        lines.append(f"- Share: `{commands.get('share_command', f'openclaw session share {session_id}')}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _cmd_exporttemplates(ctx: ChatCommandContext) -> str:
    """/exporttemplates [list|show <name>] — inspect built-in runbook/export templates."""
    raw = (ctx.args or "").strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else "list"

    if sub in {"", "list"}:
        if _RICH_AVAILABLE and _IS_TTY:
            tbl = _RichTable(title="Export Templates", border_style="cyan", header_style="bold cyan")
            tbl.add_column("Name", style="bold")
            tbl.add_column("Audience", style="dim")
            tbl.add_column("Sections")
            for name, template in sorted(_RUNBOOK_TEMPLATES.items()):
                sections = ", ".join(str(s) for s in template.get("sections", ()))
                tbl.add_row(name, str(template.get("audience", "")), sections)
            _RICH_CONSOLE.print(tbl)
        else:
            print("Export templates:")
            for name, template in sorted(_RUNBOOK_TEMPLATES.items()):
                sections = ", ".join(str(s) for s in template.get("sections", ()))
                print(f"  {name}: {template.get('audience', '')} — {sections}")
        return _CMD_CONTINUE

    if sub == "show":
        name = parts[1].strip() if len(parts) > 1 else ""
        resolved = _resolve_runbook_template(name)
        if resolved is None:
            valid = ", ".join(sorted(_RUNBOOK_TEMPLATES))
            _print_error(f"Unknown export template '{name}'. Available: {valid}")
            return _CMD_CONTINUE
        template_key, template = resolved
        sections = ", ".join(str(s) for s in template.get("sections", ()))
        print(f"Template: {template_key}")
        print(f"Audience: {template.get('audience', '')}")
        print(f"Sections: {sections}")
        return _CMD_CONTINUE

    _print_error("Usage: /exporttemplates [list|show <name>]")
    return _CMD_CONTINUE


def _cmd_runbook(ctx: ChatCommandContext) -> str:
    """/runbook [template] [save <path>] — render a long-form session runbook."""
    session = _require_session_or_warn(ctx)
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
            _print_error("Usage: /runbook [template] [save <path>]")
            return _CMD_CONTINUE
        save_path = parts[1]

    try:
        content = _build_session_runbook_text(session.session_id, template_name=template_name)
    except OpenClawCliError as exc:
        _print_error(str(exc))
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


def inspect_session(session_id: str) -> str:
    """Render a human-readable inspection view of a persisted session."""
    from openclaw_cli_sessions import export_session

    export = export_session(session_id)
    session_data: dict[str, Any] = export.get("session") or {}
    events: list[dict[str, Any]] = export.get("events") or []
    outputs: list[dict[str, Any]] = export.get("outputs") or []
    watch: dict[str, Any] = export.get("watch_state") or {}
    routed_checkpoints: list[dict[str, Any]] = export.get("routed_action_checkpoints") or []
    collaboration: dict[str, Any] = export.get("collaboration") or {}
    bookmarks: list[dict[str, Any]] = list(session_data.get("bookmarks") or [])
    story = build_session_storyline(session_id, limit=5)
    mood = _session_mood_snapshot(require_session(session_id), watch_state=watch, collaboration_snapshot=collaboration)

    if _RICH_AVAILABLE and _IS_TTY:
        _inspect_session_rich(session_id, session_data, events, outputs, watch, routed_checkpoints)
        return ""

    sep = "-" * 60
    lines: list[str] = []

    # ── Metadata ─────────────────────────────────────────────────
    lines += [
        sep,
        "SESSION INSPECTION",
        sep,
        f"  id       : {session_data.get('session_id', session_id)}",
        f"  title    : {session_data.get('title', '')}",
        f"  status   : {_status_cell(str(session_data.get('status') or 'active'))}",
        f"  cwd      : {session_data.get('cwd', '')}",
        f"  created  : {session_data.get('created_at', '')}",
        f"  updated  : {session_data.get('updated_at', '')}",
        "  "
        + "  |  ".join(
            [
                _progress_cell("commands", str(session_data.get("command_count", 0)), status="active" if int(session_data.get("command_count", 0) or 0) else "idle"),
                _progress_cell("outputs", str(session_data.get("output_count", 0)), status="complete" if int(session_data.get("output_count", 0) or 0) else "idle"),
                _progress_cell("edits", str(session_data.get("file_edit_count", 0)), status="active" if int(session_data.get("file_edit_count", 0) or 0) else "idle"),
            ]
        ),
    ]
    if story.get("headline"):
        lines.append(f"  story    : {story.get('headline', '')}")
    if story.get("chapter_title"):
        lines.append(f"  chapter  : {story.get('chapter_title', '')} · {story.get('chapter_detail', '')}")
    mood_cell = _session_mood_cell(mood)
    if mood_cell:
        lines.append(f"  {mood_cell}")

    # ── Plan / task linkage ───────────────────────────────────────
    plan_id = str(session_data.get("plan_id") or "").strip()
    task_id = str(session_data.get("task_id") or "").strip()
    if plan_id or task_id:
        lines.append("")
        lines.append("PLAN / TASK LINKAGE")
        if plan_id:
            lines.append(f"  plan  : {plan_id}")
        if task_id:
            lines.append(f"  task  : {task_id}")

    # ── Tracked files ─────────────────────────────────────────────
    files: list[str] = list(session_data.get("files") or [])
    if files:
        lines.append("")
        lines.append("TRACKED FILES")
        for f in files[:10]:
            lines.append(f"  {f}")
        if len(files) > 10:
            lines.append(f"  … and {len(files) - 10} more")

    # ── Automation / watch status ─────────────────────────────────
    automation_mode = str(session_data.get("automation_mode") or "").strip()
    if automation_mode or watch:
        lines.append("")
        lines.append("AUTOMATION / WATCH")
        if automation_mode:
            a_status = str(session_data.get("automation_status") or "active").strip()
            interval = int(session_data.get("watch_interval_seconds") or 0)
            lines.append(f"  mode     : {_progress_cell('automation', f'{automation_mode} ({a_status})', status=a_status)}")
            if interval:
                lines.append(f"  interval : {_progress_cell('loop', f'{interval}s', status=a_status)}")
        if watch:
            w_status = str(watch.get("status") or "").strip()
            poll_count = int(watch.get("poll_count") or 0)
            max_polls = int(watch.get("max_polls") or 0)
            polls_value = f"{poll_count}/{max_polls or '∞'} polls"
            goal = str(watch.get("goal") or "").strip()
            if goal:
                lines.append(f"  goal     : {goal[:120]}")
            if w_status:
                lines.append(f"  w.status : {_progress_cell('watch', f'{w_status} · {polls_value}', status=w_status)}")
            last_error = str(watch.get("last_error") or "").strip()
            if last_error:
                lines.append(f"  last err : {_status_cell('error', detail=last_error[:180])}")

    # ── Checkpoints ───────────────────────────────────────────────
    checkpoint_count = int(session_data.get("checkpoint_count") or 0)
    last_checkpoint_at = str(session_data.get("last_checkpoint_at") or "").strip()
    watch_checkpoints: list[dict[str, Any]] = list(watch.get("checkpoints") or [])
    if checkpoint_count or watch_checkpoints or routed_checkpoints:
        lines.append("")
        lines.append("CHECKPOINTS")
        lines.append(
            f"  total : {_progress_cell('count', str(checkpoint_count), status='complete' if checkpoint_count else 'idle')}  last: {last_checkpoint_at or 'n/a'}"
        )
        for ckpt in routed_checkpoints[:3]:
            step_index = int(ckpt.get("step_index") or 0)
            step_total = int(ckpt.get("step_total") or 0)
            step_label = (
                f"step {step_index}/{step_total}"
                if step_index > 0 and step_total > 0
                else "routed action"
            )
            lines.append(
                f"  [{ckpt.get('created_at', '')}] {ckpt.get('action_kind', 'action')}"
                f" {step_label} ({ckpt.get('rollback_status', 'available')})"
            )
        for ckpt in watch_checkpoints[-3:]:
            ts = str(ckpt.get("timestamp") or ckpt.get("at") or "").strip()
            note = str(ckpt.get("note") or ckpt.get("summary") or "").strip()
            if ts or note:
                lines.append(f"  [{ts}] {note[:100]}")

    if bookmarks:
        lines.append("")
        lines.append("BOOKMARKS")
        for bookmark in bookmarks[-5:]:
            lines.append(
                f"  [{bookmark.get('id', '')}] "
                f"{bookmark.get('label', '')} "
                f"· turn {bookmark.get('turn_index', 0)}"
            )
            summary_text = str(bookmark.get("summary") or "").strip()
            if summary_text:
                lines.append(f"      {summary_text[:120]}")

    # ── Recent progress log (watch) ───────────────────────────────
    progress_log: list[dict[str, Any]] = list(watch.get("progress_log") or [])
    if progress_log:
        lines.append("")
        lines.append("RECENT PROGRESS (last 5 watch entries)")
        for entry in progress_log[-5:]:
            ts = str(entry.get("timestamp") or entry.get("at") or "").strip()
            phase = str(entry.get("phase") or "").strip()
            note = str(entry.get("note") or entry.get("summary") or entry.get("content") or "").strip()
            entry_status = "warn" if entry.get("warning") else "complete" if entry.get("ok") else "active"
            lines.append(f"  [{ts}] {_status_cell(entry_status, detail=phase or 'progress')} · {note[:120]}")

    # ── Recent events ─────────────────────────────────────────────
    if events:
        lines.append("")
        lines.append("RECENT EVENTS (last 5)")
        for event in events[-5:]:
            ts = str(event.get("timestamp") or event.get("at") or event.get("created_at") or "").strip()
            kind = str(event.get("kind") or "").strip()
            content = str(event.get("content") or "").strip()
            meta = event.get("metadata") or {}
            summary_note = str(meta.get("summary") if isinstance(meta, dict) else "").strip()
            label = summary_note or content[:80]
            event_status = "error" if kind == "error" else "complete" if kind in {"assistant", "checkpoint"} else "active" if kind in {"exec", "edit"} else "info"
            lines.append(f"  [{ts}] {_status_cell(event_status, detail=kind or 'event')} · {label}")

    # ── Saved outputs ─────────────────────────────────────────────
    if outputs:
        lines.append("")
        lines.append(f"SAVED OUTPUTS ({len(outputs)})")
        for out in outputs[-5:]:
            name = str(out.get("name") or "").strip()
            size = int(out.get("size_bytes") or 0)
            lines.append(f"  {name}  ({size} bytes)")

    actors: list[dict[str, Any]] = list(collaboration.get("actors") or [])
    recent_decisions: list[dict[str, Any]] = list(collaboration.get("recent_decisions") or [])
    latest_handoff = collaboration.get("latest_handoff") or {}
    if actors or recent_decisions or latest_handoff:
        lines.append("")
        lines.append("COLLABORATION")
        for actor in actors[:3]:
            lines.append(
                f"  actor : {actor.get('name', 'operator')} "
                f"({int(actor.get('event_count') or 0)} touchpoints)"
            )
        for entry in recent_decisions[:3]:
            lines.append(f"  decision : {_format_collaboration_entry(entry)}")
        if latest_handoff:
            lines.append(f"  handoff  : {latest_handoff.get('id', '')} @ {latest_handoff.get('created_at', '')}")
    if story.get("milestones") or story.get("timeline"):
        lines.append("")
        lines.append("STORY RECAP")
        for item in list(story.get("milestones") or [])[:4]:
            lines.append(f"  milestone: {item}")
        for item in list(story.get("timeline") or [])[:4]:
            lines.append(f"  timeline : {item.get('label', 'Update')} · {item.get('summary', '')}")

    # ── Last summary ──────────────────────────────────────────────
    last_summary = str(session_data.get("last_summary") or "").strip()
    if last_summary:
        lines.append("")
        lines.append("LAST SUMMARY")
        lines.append(f"  {last_summary}")

    lines.append(sep)
    lines.append(f"Resume: openclaw --session {session_data.get('session_id', session_id)}")
    return "\n".join(lines)


def _inspect_session_rich(
    session_id: str,
    session_data: dict[str, Any],
    events: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    watch: dict[str, Any],
    routed_checkpoints: list[dict[str, Any]],
) -> None:
    """Print a rich-formatted session inspection view."""
    sid = session_data.get("session_id", session_id)
    title = session_data.get("title") or "Session"
    status = str(session_data.get("status") or "active")
    collaboration = build_collaboration_snapshot(session_id, limit=5)
    story = build_session_storyline(session_id, limit=5)
    mood = _session_mood_snapshot(require_session(session_id), watch_state=watch, collaboration_snapshot=collaboration)
    bookmarks: list[dict[str, Any]] = list(session_data.get("bookmarks") or [])
    # Metadata panel
    meta = _RichTable.grid(padding=(0, 2))
    meta.add_column(style="dim", min_width=12)
    meta.add_column()
    meta.add_row("🆔 id", f"[dim]{sid}[/]")
    meta.add_row("status", _status_cell(status, rich=True))
    meta.add_row("📁 cwd", f"[dim]{session_data.get('cwd', '')}[/]")
    meta.add_row("🕐 created", f"[dim]{session_data.get('created_at', '')}[/]")
    meta.add_row("🕐 updated", f"[yellow]{session_data.get('updated_at', '')}[/]")
    meta.add_row(
        "📊 stats",
        "  •  ".join(
            [
                _progress_cell("commands", str(session_data.get("command_count", 0)), status="active" if int(session_data.get("command_count", 0) or 0) else "idle", rich=True),
                _progress_cell("outputs", str(session_data.get("output_count", 0)), status="complete" if int(session_data.get("output_count", 0) or 0) else "idle", rich=True),
                _progress_cell("edits", str(session_data.get("file_edit_count", 0)), status="active" if int(session_data.get("file_edit_count", 0) or 0) else "idle", rich=True),
            ]
        ),
    )
    mood_cell = _session_mood_cell(mood, rich=True)
    if mood_cell:
        meta.add_row("🙂 mood", mood_cell)
    if story.get("headline"):
        meta.add_row("🎬 story", f"[bold]{story.get('headline', '')}[/]")
    if story.get("chapter_title"):
        meta.add_row("📚 chapter", f"{story.get('chapter_title', '')} · {story.get('chapter_detail', '')}")
    plan_id = str(session_data.get("plan_id") or "").strip()
    task_id = str(session_data.get("task_id") or "").strip()
    if plan_id:
        meta.add_row("📋 plan", f"[magenta]{plan_id}[/]")
    if task_id:
        meta.add_row("✅ task", f"[magenta]{task_id}[/]")
    files: list[str] = list(session_data.get("files") or [])
    if files:
        file_str = ", ".join(files[:5]) + (f" … +{len(files)-5}" if len(files) > 5 else "")
        meta.add_row("📄 files", f"[dim]{file_str}[/]")
    _RICH_CONSOLE.print(_RichPanel(meta, title=f"[bold cyan]{title}[/]", border_style="cyan", padding=(0, 1)))

    # Events panel
    if events:
        kind_styles = {"prompt": "cyan", "assistant": "green", "exec": "yellow", "edit": "magenta", "error": "red"}
        ev_table = _RichTable(border_style="dim", show_edge=False, pad_edge=True, header_style="bold dim")
        ev_table.add_column("Time", style="dim", no_wrap=True)
        ev_table.add_column("Status", no_wrap=True)
        ev_table.add_column("Summary")
        for event in events[-8:]:
            ts = str(event.get("timestamp") or event.get("created_at") or "").strip()[-8:]
            kind = str(event.get("kind") or "").strip()
            meta_d = event.get("metadata") or {}
            summary = str(meta_d.get("summary") if isinstance(meta_d, dict) else "") or str(event.get("content") or "")
            style = kind_styles.get(kind, "dim")
            event_status = "error" if kind == "error" else "complete" if kind in {"assistant", "checkpoint"} else "active" if kind in {"exec", "edit"} else "info"
            ev_table.add_row(ts, f"[{style}]{_status_text(event_status)}[/]", f"{kind}: {summary[:80]}")
        _RICH_CONSOLE.print(_RichPanel(ev_table, title="[bold dim]Recent Events[/]", border_style="dim", padding=(0, 1)))

    # Outputs panel
    if outputs:
        out_table = _RichTable(border_style="dim", show_edge=False, pad_edge=True, header_style="bold dim")
        out_table.add_column("Name", style="cyan")
        out_table.add_column("Size", justify="right", style="dim")
        for out in outputs[-5:]:
            name = str(out.get("name") or "").strip()
            size = _format_byte_count(int(out.get("size_bytes") or 0))
            out_table.add_row(name, size)
        _RICH_CONSOLE.print(_RichPanel(out_table, title=f"[bold dim]Saved Outputs ({len(outputs)})[/]", border_style="dim", padding=(0, 1)))

    if bookmarks:
        bookmark_table = _RichTable(border_style="dim", show_edge=False, pad_edge=True, header_style="bold dim")
        bookmark_table.add_column("ID", style="cyan", no_wrap=True)
        bookmark_table.add_column("Turn", style="dim", no_wrap=True)
        bookmark_table.add_column("Label")
        for bookmark in bookmarks[-5:]:
            bookmark_table.add_row(
                str(bookmark.get("id") or ""),
                str(bookmark.get("turn_index") or ""),
                str(bookmark.get("label") or ""),
            )
        _RICH_CONSOLE.print(_RichPanel(bookmark_table, title="[bold dim]Bookmarks[/]", border_style="dim", padding=(0, 1)))

    milestones = list(story.get("milestones") or [])
    timeline = list(story.get("timeline") or [])
    cast = list(story.get("actor_highlights") or [])
    if milestones or timeline or cast:
        recap = _RichTable.grid(padding=(0, 1))
        recap.add_column(style="dim", min_width=11)
        recap.add_column()
        for item in milestones[:3]:
            recap.add_row("milestone", item)
        for item in cast[:2]:
            recap.add_row("cast", item)
        for item in timeline[:3]:
            recap.add_row(str(item.get("label") or "update"), str(item.get("summary") or ""))
        _RICH_CONSOLE.print(_RichPanel(recap, title="[bold dim]Story Recap[/]", border_style="magenta", padding=(0, 1)))

    _RICH_CONSOLE.print(f"  [dim]Resume:[/] [cyan]openclaw --session {sid}[/]")


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


def _watch_retry_delay_total(state: dict[str, Any]) -> int:
    total = 0
    for entry in list(state.get("retry_history") or []):
        try:
            delay = int(entry.get("delay_seconds") or watch_retry_delay_seconds(int(entry.get("attempt") or 1)))
        except (TypeError, ValueError):
            delay = 0
        total += max(0, delay)
    return total


def _watch_timing_summary(state: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_watch_state(state)
    active_checkpoint = normalized.get("active_checkpoint")
    checkpoints = [item for item in list(normalized.get("checkpoints") or []) if isinstance(item, dict)]
    latest_checkpoint = checkpoints[-1] if checkpoints else {}
    active_phase = ""
    active_phase_elapsed = None

    if isinstance(active_checkpoint, dict) and active_checkpoint:
        active_phase = str(active_checkpoint.get("phase") or "").strip()
        phase_started_at = ""
        for item in reversed(list(active_checkpoint.get("progress") or [])):
            if str(item.get("phase") or "").strip() == active_phase:
                phase_started_at = str(item.get("created_at") or "").strip()
                break
        if not phase_started_at:
            phase_started_at = str(active_checkpoint.get("updated_at") or active_checkpoint.get("started_at") or "").strip()
        active_phase_elapsed = _elapsed_seconds(phase_started_at)

    latest_duration = (
        latest_checkpoint.get("duration_seconds")
        or _elapsed_seconds(latest_checkpoint.get("started_at"), latest_checkpoint.get("completed_at"))
        or _elapsed_seconds(latest_checkpoint.get("created_at"), latest_checkpoint.get("completed_at"))
    )
    current_elapsed = _elapsed_seconds(normalized.get("last_run_at")) if normalized.get("status") in {"running", "retrying"} else None
    return {
        "active_phase": active_phase,
        "active_phase_elapsed": active_phase_elapsed,
        "latest_duration": latest_duration,
        "retry_delay_total": _watch_retry_delay_total(normalized),
        "current_elapsed": current_elapsed,
    }


def load_plan_goal(plan_id: str) -> str:
    """Resolve a plan goal when watch mode is attached to an existing plan."""
    normalized = str(plan_id or "").strip()
    if not normalized:
        return ""
    from agent_loop import load_plan as load_agent_plan

    plan = load_agent_plan(normalized)
    return str(plan.goal or "").strip() if plan else ""


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


def build_watch_state(
    *,
    session: SessionSummary,
    mode: str,
    goal: str,
    interval_seconds: int,
    max_polls: int,
    on_change: bool,
) -> dict[str, Any]:
    """Create the persisted watch-mode state payload."""
    now = utc_timestamp()
    return {
        "session_id": session.session_id,
        "mode": mode,
        "goal": goal,
        "cwd": session.cwd,
        "files": list(session.files or []),
        "plan_id": session.plan_id,
        "task_id": session.task_id,
        "interval_seconds": interval_seconds,
        "max_polls": max_polls,
        "poll_count": 0,
        "on_change": on_change,
        "status": "idle",
        "created_at": now,
        "updated_at": now,
        "last_run_at": "",
        "last_output_path": "",
        "last_summary": "",
        "last_error": "",
        "workspace_signature": "",
        "failure_count": 0,
        "consecutive_failures": 0,
        "retry_limit": WATCH_RETRY_LIMIT,
        "retry_history": [],
        "progress_log": [],
        "active_checkpoint": {},
        "checkpoints": [],
    }


def normalize_watch_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """Backfill watch-state fields introduced after the first CLI releases."""
    normalized = dict(state or {})
    normalized.setdefault("last_error", "")
    normalized.setdefault("failure_count", 0)
    normalized.setdefault("consecutive_failures", 0)
    normalized.setdefault("retry_limit", WATCH_RETRY_LIMIT)
    normalized["retry_limit"] = max(1, int(normalized.get("retry_limit") or WATCH_RETRY_LIMIT))
    normalized["retry_history"] = [
        item for item in list(normalized.get("retry_history") or []) if isinstance(item, dict)
    ][-WATCH_PROGRESS_LOG_LIMIT:]
    normalized["progress_log"] = [
        item for item in list(normalized.get("progress_log") or []) if isinstance(item, dict)
    ][-WATCH_PROGRESS_LOG_LIMIT:]
    normalized["interventions"] = [
        item for item in list(normalized.get("interventions") or []) if isinstance(item, dict)
    ][-WATCH_PROGRESS_LOG_LIMIT:]
    normalized["force_run_once"] = bool(normalized.get("force_run_once"))
    normalized["stop_requested"] = bool(normalized.get("stop_requested"))
    normalized["stop_requested_at"] = str(normalized.get("stop_requested_at", "") or "")
    normalized["last_intervention_at"] = str(normalized.get("last_intervention_at", "") or "")
    active_checkpoint = normalized.get("active_checkpoint")
    if not isinstance(active_checkpoint, dict):
        active_checkpoint = {}
    if active_checkpoint:
        active_checkpoint.setdefault("progress", [])
        active_checkpoint["progress"] = [
            item for item in list(active_checkpoint.get("progress") or []) if isinstance(item, dict)
        ][-WATCH_PROGRESS_LOG_LIMIT:]
        active_checkpoint.setdefault("attempts", [])
        active_checkpoint["attempts"] = [
            item for item in list(active_checkpoint.get("attempts") or []) if isinstance(item, dict)
        ][-WATCH_PROGRESS_LOG_LIMIT:]
        active_checkpoint.setdefault(
            "duration_seconds",
            _elapsed_seconds(active_checkpoint.get("started_at"), active_checkpoint.get("completed_at")),
        )
    normalized["active_checkpoint"] = active_checkpoint
    checkpoints = [item for item in list(normalized.get("checkpoints") or []) if isinstance(item, dict)]
    for checkpoint in checkpoints:
        checkpoint.setdefault(
            "duration_seconds",
            _elapsed_seconds(checkpoint.get("started_at") or checkpoint.get("created_at"), checkpoint.get("completed_at")),
        )
    normalized["checkpoints"] = checkpoints
    for entry in normalized["retry_history"]:
        entry.setdefault("delay_seconds", watch_retry_delay_seconds(int(entry.get("attempt") or 1)))
    return normalized


def watch_retry_delay_seconds(attempt: int) -> int:
    """Return a capped exponential backoff delay for transient watch retries."""
    return min(WATCH_RETRY_MAX_DELAY_SECONDS, max(1, 2 ** max(0, attempt - 1)))


def is_transient_watch_error(exc: Exception | str) -> bool:
    """Classify whether a watch failure is worth retrying automatically."""
    message = str(exc or "").strip().lower()
    if not message:
        return False
    return any(marker in message for marker in TRANSIENT_WATCH_ERROR_MARKERS)


def start_watch_checkpoint(*, iteration: int, mode: str) -> dict[str, Any]:
    """Create the mutable state object for an in-flight watch checkpoint."""
    now = utc_timestamp()
    return {
        "poll": iteration,
        "mode": mode,
        "status": "running",
        "started_at": now,
        "updated_at": now,
        "progress": [],
        "attempts": [],
    }


def record_watch_progress(
    *,
    session_id: str,
    state: dict[str, Any],
    iteration: int,
    mode: str,
    phase: str,
    message: str,
    output_json: bool,
) -> None:
    """Persist and optionally render watch progress updates."""
    entry = {
        "poll": iteration,
        "mode": mode,
        "phase": phase,
        "message": message,
        "created_at": utc_timestamp(),
    }
    progress_log = list(state.get("progress_log") or [])
    progress_log.append(entry)
    state["progress_log"] = progress_log[-WATCH_PROGRESS_LOG_LIMIT:]
    active_checkpoint = state.get("active_checkpoint")
    if isinstance(active_checkpoint, dict) and active_checkpoint:
        active_progress = list(active_checkpoint.get("progress") or [])
        active_progress.append(entry)
        active_checkpoint["progress"] = active_progress[-WATCH_PROGRESS_LOG_LIMIT:]
        active_checkpoint["phase"] = phase
        active_checkpoint["last_message"] = message
        active_checkpoint["updated_at"] = entry["created_at"]
    state["updated_at"] = entry["created_at"]
    save_watch_state(session_id, normalize_watch_state(state))
    if not output_json:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[dim][[/][cyan]{iteration}[/][dim]][/] [dim]{mode}/{phase}:[/] {message}")
        else:
            print(f"[watch {iteration}] {mode}/{phase}: {message}")


def print_watch_resume_snapshot(session_id: str, state: dict[str, Any], *, output_json: bool) -> None:
    """Print the most useful persisted state when resuming a watch session."""
    if output_json:
        return
    status = str(state.get("status") or "unknown").strip() or "unknown"
    poll_count = int(state.get("poll_count") or 0)
    last_summary = str(state.get("last_summary") or "").strip()
    last_error = str(state.get("last_error") or "").strip()
    active_checkpoint = state.get("active_checkpoint")
    recent_progress = list(state.get("progress_log") or [])[-3:]

    if _RICH_AVAILABLE and _IS_TTY:
        emoji = _status_emoji(status)
        border = "green" if status in ("active", "running") else ("yellow" if status in ("paused", "idle") else ("red" if status in ("failed", "error") else "dim"))
        body = _RichText()
        body.append(f"{emoji} status    ", style="dim")
        body.append(f"{status}", style=f"bold {border}")
        body.append(f"\n🔢 polls    ", style="dim")
        body.append(f"{poll_count}", style="cyan")
        if last_summary:
            body.append(f"\n📝 last     ", style="dim")
            body.append(last_summary, style="white")
        if last_error:
            body.append(f"\n⚠️  error    ", style="dim")
            body.append(last_error, style="red")
        if isinstance(active_checkpoint, dict) and active_checkpoint:
            partial = str(active_checkpoint.get("last_message") or "").strip()
            if partial:
                body.append(f"\n⏳ partial  ", style="dim")
                body.append(partial, style="yellow")
        if recent_progress:
            body.append(f"\n📋 recent   ", style="dim")
            for entry in recent_progress:
                body.append(f"\n   • {entry.get('message', '')}", style="dim")
        _RICH_CONSOLE.print(_RichPanel(body, title=f"[bold]resuming watch[/] [dim]{session_id}[/]", border_style=border, padding=(0, 1)))
    else:
        print(f"Resuming watch {session_id} (status={status}, completed polls={poll_count}).")
        if last_summary:
            print(f"Last checkpoint: {last_summary}")
        if last_error:
            print(f"Last error: {last_error}")
        if isinstance(active_checkpoint, dict) and active_checkpoint:
            partial = str(active_checkpoint.get("last_message") or "").strip()
            if partial:
                print(f"Partial progress: {partial}")
        if recent_progress:
            print("Recent progress:")
            for entry in recent_progress:
                print(f"  - {entry.get('message', '')}")


def refresh_watch_controls(session_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """Merge persisted intervention flags into the in-memory watch state."""
    latest = load_watch_state(session_id)
    if latest is None:
        return state
    latest = normalize_watch_state(latest)
    state["interventions"] = list(latest.get("interventions") or [])
    state["force_run_once"] = bool(latest.get("force_run_once"))
    state["stop_requested"] = bool(latest.get("stop_requested"))
    state["stop_requested_at"] = str(latest.get("stop_requested_at", "") or "")
    state["last_intervention_at"] = str(latest.get("last_intervention_at", "") or "")
    return state


def resolve_watch_intervention(
    state: dict[str, Any],
    *,
    action: str,
    status: str,
    note: str = "",
) -> bool:
    """Resolve the newest pending intervention of the requested action."""
    for item in reversed(list(state.get("interventions") or [])):
        if str(item.get("action") or "") != action or str(item.get("status") or "") != "pending":
            continue
        item["status"] = status
        item["applied_at"] = utc_timestamp()
        if note:
            item["note"] = note[:240]
        return True
    return False


def stop_watch_from_intervention(
    *,
    session: SessionSummary,
    state: dict[str, Any],
    mode: str,
    output_json: bool,
) -> int:
    """Persist a graceful watch stop requested through the dashboard."""
    interrupted_at = utc_timestamp()
    summary = "Watch stopped by dashboard intervention."
    active_checkpoint = state.get("active_checkpoint")
    if isinstance(active_checkpoint, dict) and active_checkpoint:
        partial = str(active_checkpoint.get("last_message") or "").strip()
        active_checkpoint.update(
            {
                "status": "interrupted",
                "completed_at": interrupted_at,
                "summary": partial[:160] if partial else "checkpoint interrupted by dashboard intervention",
            }
        )
        state.setdefault("checkpoints", []).append(dict(active_checkpoint))
        state["active_checkpoint"] = {}
    resolve_watch_intervention(
        state,
        action="graceful-stop",
        status="applied",
        note="Watch loop exited cleanly after dashboard stop request.",
    )
    state["status"] = "interrupted"
    state["updated_at"] = interrupted_at
    state["last_run_at"] = interrupted_at
    state["last_summary"] = summary
    state["stop_requested"] = False
    save_watch_state(session.session_id, state)
    append_event(
        session.session_id,
        kind="intervention",
        content=summary,
        metadata={
            "summary": summary,
            "mode": mode,
            "action": "graceful-stop",
            "plan_id": session.plan_id,
            "task_id": session.task_id,
        },
    )
    update_session(session.session_id, automation_mode=mode, automation_status="interrupted")
    if not output_json:
        _print_meta_footer(("resume", f"openclaw watch --resume {session.session_id}"))
    return 0


def render_watch_iteration(
    *,
    iteration: int,
    mode: str,
    summary: str,
    output_path: str,
    output_json: bool,
) -> None:
    """Print a compact watch checkpoint result."""
    payload = {
        "iteration": iteration,
        "mode": mode,
        "summary": summary,
        "saved": output_path,
    }
    if output_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if _RICH_AVAILABLE and _IS_TTY:
        _MODE_COLORS = {"analyze": "cyan", "research": "blue", "write": "yellow"}
        mode_color = _MODE_COLORS.get(str(mode).lower(), "white")
        _RICH_CONSOLE.print(f"\U0001f504 [bold]watch [{iteration}][/]  [{mode_color}]{mode}[/]  [dim]·[/]  {summary}")
        _print_meta_footer(("saved", output_path))
    else:
        print(f"[watch {iteration}] {mode}: {summary}")
        print(f"saved: {output_path}")


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
    use_high_contrast = _a11y_high_contrast() if high_contrast is None else high_contrast
    char = "=" if use_high_contrast or _a11y_plain_mode() else "─"
    return char * max(1, width)


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
    """Render a list of rows as an ANSI-aligned table, capped to terminal width."""
    if not rows:
        return []
    num_cols = max(len(r) for r in rows)
    w = _terminal_width()

    def _plain(cell: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", r"\1", re.sub(r"\*(.+?)\*", r"\1", cell))

    plain_rows = [[_plain(cell) for cell in row[:num_cols]] for row in rows]
    col_widths = [0] * num_cols
    for row in plain_rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))
    estimated_total = sum(col_widths) + num_cols * 3 + 1

    if w < 80 or estimated_total > max(20, w - 4):
        # Narrow terminal: list format — one "Header: value" line per cell per row
        headers = plain_rows[0] if plain_rows else []
        result: list[str] = []
        sep_core = _separator_fill(max(1, w - 4))
        sep_style = _theme_ansi() if _a11y_high_contrast() else _DM
        sep_reset = _R if sep_style else ""
        sep = f"  {sep_style}{sep_core}{sep_reset}"
        for row_i, row in enumerate(rows):
            if row_i == 0:
                # First row is the header — skip it as a data row
                continue
            result.append(sep)
            for j in range(num_cols):
                cell = row[j] if j < len(row) else ""
                header = headers[j] if j < len(headers) else f"Col {j + 1}"
                available = max(12, w - len(header) - 8)
                wrapped = textwrap.wrap(_plain(cell), width=available) or [""]
                rendered = _apply_inline_ansi(wrapped[0])
                result.append(f"  {_B}{header}:{_R} {rendered}")
                indent = " " * (len(header) + 4)
                for continuation in wrapped[1:]:
                    result.append(f"{indent}{_apply_inline_ansi(continuation)}")
            result.append("")
        if result:
            result.append(sep)
        return result

    # Wide terminal (>= 80): existing column formatting with proportional cap
    max_col_width = max(10, (w - 4) // num_cols)
    col_widths = [min(cw, max_col_width) for cw in col_widths]

    # Further scale down if total still exceeds terminal
    terminal_width = w - 4
    total = sum(col_widths) + num_cols * 3 + 1
    if total > terminal_width and sum(col_widths) > 0:
        available = max(num_cols * 6, terminal_width - num_cols * 3 - 1)
        scale = available / sum(col_widths)
        col_widths = [max(6, int(cw * scale)) for cw in col_widths]

    sep_len = min(sum(col_widths) + num_cols * 3 + 1, terminal_width)
    sep_style = _theme_ansi() if _a11y_high_contrast() else _DM
    sep_reset = _R if sep_style else ""
    sep = f"  {sep_style}{_separator_fill(sep_len)}{sep_reset}"

    result = [sep]
    for row_i, row in enumerate(rows):
        cells = []
        for j in range(num_cols):
            cell = row[j] if j < len(row) else ""
            plain = _plain(cell)
            max_w = col_widths[j]
            if len(plain) > max_w:
                plain = plain[: max_w - 1] + "…"
                cell = plain  # use truncated plain for formatting
            formatted = _apply_inline_ansi(cell)
            cells.append(formatted + " " * (max_w - len(plain)))
        result.append("  " + (" │ ".join(cells)).rstrip())
        if row_i == 0:
            result.append(sep)
    result.append(sep)
    return result


def _apply_inline_ansi(text: str) -> str:
    """Apply inline bold, italic, and code formatting via ANSI codes."""
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: f"{_B}{m.group(1)}{_R}", text)
    text = re.sub(r"__(.+?)__", lambda m: f"{_B}{m.group(1)}{_R}", text)
    text = re.sub(r"\*([^*\n]+?)\*", lambda m: f"{_IT}{m.group(1)}{_R}", text)
    text = re.sub(r"`([^`\n]+?)`", lambda m: f"{_CY}{m.group(1)}{_R}", text)
    return text


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
# Regex constants for rendering helpers (compiled once at module level for performance)
_RE_KV_BOLD = re.compile(r"\*\*[^*]+:\*\*")
_RE_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^\)]+)\)")
_RE_BARE_URL = re.compile(r"(https?://\S+)")


def _make_clickable_link(url: str, text: str = "") -> str:
    """Return an OSC 8 clickable hyperlink if supported, otherwise plain URL."""
    return _path_utils._make_clickable_link(url, text, prefs=_PREFS, is_tty=_get_is_tty())


def _linkify_response(text: str) -> str:
    """Replace bare URLs in response text with OSC 8 clickable links."""
    return _path_utils._linkify_response(text, prefs=_PREFS, is_tty=_get_is_tty())


def _render_markdown_ansi(text: str) -> str:
    """Convert markdown to ANSI-formatted terminal text (fallback when Rich is absent).

    Handles headings (H1–H4), bold/italic/code, blockquotes, tables, bullet
    lists (including nested), numbered lists, fenced code blocks, and rules.
    """
    term_cols = _terminal_width()
    rule_width = min(term_cols - 2, 72) if term_cols >= 80 else max(1, term_cols - 4)
    plain_mode = _a11y_plain_mode()
    narrow = term_cols < 72
    border_style = _theme_ansi() if _a11y_high_contrast() else _DM
    border_reset = _R if border_style else ""

    lines = text.split("\n")
    result: list[str] = []
    in_code = False
    code_lang = ""
    table_rows: list[list[str]] = []

    def flush_table() -> None:
        if table_rows:
            result.extend(_render_table_ansi(table_rows))
            table_rows.clear()

    for line in lines:
        # Fenced code blocks
        if line.startswith("```"):
            flush_table()
            if not in_code:
                in_code = True
                code_lang = line[3:].strip()
                lang_label = f" {code_lang} " if code_lang else " code "
                if plain_mode or narrow:
                    result.append(f"  {lang_label.strip()}:")
                else:
                    result.append(
                        f"  {border_style}╭─{lang_label}{_separator_fill(max(0, rule_width - len(lang_label) - 3), high_contrast=False)}╮{border_reset}"
                    )
            else:
                in_code = False
                if not (plain_mode or narrow):
                    result.append(f"  {border_style}╰{_separator_fill(rule_width - 1, high_contrast=False)}╯{border_reset}")
                code_lang = ""
            continue
        if in_code:
            prefix = "    " if (plain_mode or narrow) else f"  {border_style}│{border_reset} "
            result.append(f"{prefix}{_CY}{line}{_R}")
            continue

        # Markdown table rows
        if line.startswith("|"):
            stripped = line.strip().strip("|")
            if re.match(r"^[-| :]+$", stripped):
                continue  # skip separator row
            cells = [c.strip() for c in stripped.split("|")]
            table_rows.append(cells)
            continue
        else:
            flush_table()

        # Horizontal rule
        if re.match(r"^[-*_]{3,}\s*$", line):
            fill = _separator_fill(rule_width, high_contrast=_a11y_high_contrast())
            style = "" if plain_mode else border_style
            reset = border_reset if style else ""
            result.append(f"{style}{fill}{reset}")
            continue

        # Blockquotes
        bq = re.match(r"^>\s?(.*)", line)
        if bq:
            quote_marker = ">" if (plain_mode or narrow) else "▌"
            quote_style = "" if plain_mode else border_style
            reset = border_reset if quote_style else ""
            result.append(f"  {quote_style}{quote_marker}{reset}  {_apply_inline_ansi(bq.group(1))}")
            continue

        # ATX headings
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            raw = m.group(2)
            if not plain_mode and _PREFS.get("emoji_headers", True):
                emoji = _HEADING_EMOJIS.get(level, "")
                if emoji:
                    raw = f"{emoji} {raw}"
            content = _apply_inline_ansi(raw)
            if level == 1:
                result.append(f"\n{_B}{_UL}{content}{_R}")
                result.append("")
            elif level == 2:
                result.append(f"\n{_B}{content}{_R}")
            elif level == 3:
                result.append(f"{_B}{_DM}{content}{_R}")
            else:
                result.append(f"{_DM}{_IT}{content}{_R}")
            continue

        # Bullet list (supports nested via leading whitespace)
        bm = re.match(r"^(\s*)[-*•]\s+(.*)", line)
        if bm:
            indent = bm.group(1)
            depth = len(indent) // 2
            bullet = ("◦" if depth % 2 else "•")
            result.append(f"  {'  ' * depth}{bullet} {_apply_inline_ansi(bm.group(2))}")
            continue

        # Numbered list
        nm = re.match(r"^(\s*)(\d+)\.\s+(.*)", line)
        if nm:
            indent = nm.group(1)
            result.append(f"  {indent}{nm.group(2)}. {_apply_inline_ansi(nm.group(3))}")
            continue

        # Wrap long paragraph lines to terminal width to prevent mid-word splits
        if len(line) > term_cols - 2 and not plain_mode:
            plain_line = re.sub(r"\*{1,2}([^*]+)\*{1,2}|`([^`]+)`|_([^_]+)_", r"\1\2\3", line)
            wrapped_lines = textwrap.wrap(plain_line, width=term_cols - 2) or [line]
            for wl in wrapped_lines:
                result.append(_apply_inline_ansi(wl))
        else:
            result.append(_apply_inline_ansi(line))

    flush_table()
    return "\n".join(result)


def _is_kv_bullet_group(lines: list[str]) -> bool:
    """Return True if all lines look like pipe-separated key:value bullet rows.

    Accepts both **Key:** value (bold) and plain Key: Value formats, including
    lines where the whole content is wrapped in italic markers (*...*).
    """
    for line in lines:
        content = re.sub(r"^[•\-\*]\s+", "", line.lstrip())
        # Strip wrapping italic markers (*content*) around the whole line body
        content = re.sub(r"^\*(.+)\*$", r"\1", content.strip())
        if _RE_KV_BOLD.search(content):
            continue
        # Accept plain "Key: value | Key: value" rows — require a colon in the
        # majority of pipe-segments so we don't misclassify normal prose bullets.
        segments = [s.strip() for s in content.split(" | ")]
        if len(segments) < 2:
            return False
        colon_count = sum(1 for s in segments if ":" in s)
        if colon_count < len(segments) // 2 + 1:
            return False
    return True


def _bullet_group_to_table(lines: list[str]) -> list[str]:
    """Convert pipe-in-bullet lines to a markdown table.

    Handles both **Key:** value (bold) and plain Key: Value formats.
    Also strips wrapping italic markers (*...*) that some models add.
    """
    headers: list[str] = []
    rows: list[list[str]] = []
    for line in lines:
        content = re.sub(r"^[•\-\*]\s+", "", line.lstrip())
        # Strip wrapping italic markers around the whole line body
        content = re.sub(r"^\*(.+)\*$", r"\1", content.strip())
        parts = [p.strip() for p in content.split(" | ")]
        row_headers: list[str] = []
        row_values: list[str] = []
        for part in parts:
            # Strip lone leading asterisks (partial italic markers from the first/last segment)
            part = re.sub(r"^\*+", "", part).strip()
            # Match **Key:** value  (bold-colon inside markers)
            m = re.match(r"\*\*([^*:]+):\*\*\s*(.*)", part)
            if m:
                row_headers.append(m.group(1).strip())
                row_values.append(m.group(2).strip())
            else:
                # Match plain "Key: value" — split on first colon
                colon_idx = part.find(":")
                if colon_idx > 0:
                    row_headers.append(part[:colon_idx].strip())
                    # Strip leading asterisks from values (closing italic marker from last segment)
                    val = re.sub(r"^\*+\s*", "", part[colon_idx + 1:].strip())
                    row_values.append(val)
                else:
                    row_headers.append(f"Col{len(row_headers) + 1}")
                    row_values.append(part)
        if not headers:
            headers = row_headers
        rows.append(row_values)
    table: list[str] = []
    table.append("| " + " | ".join(headers) + " |")
    table.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        while len(row) < len(headers):
            row.append("")
        table.append("| " + " | ".join(row[: len(headers)]) + " |")
    return table


def _unwrap_code_block_tables(text: str) -> str:
    """Unwrap fenced code blocks that contain only pipe-in-bullet table rows.

    When the AI wraps a pipe-in-bullet table in triple-backtick fences, Rich
    renders it as a monospace code block instead of a table.  This step detects
    those blocks and removes the fences so _convert_bullet_tables can convert them.
    """
    def _replace(m: re.Match) -> str:
        content = m.group(1).strip()
        non_empty = [l for l in content.split("\n") if l.strip()]
        if len(non_empty) >= 2 and all(
            re.match(r"^[•\-\*]\s+.+$", l) and " | " in l
            for l in non_empty
        ):
            return content  # strip the fences
        return m.group(0)  # leave unchanged

    return re.sub(r"```[^\n]*\n(.*?)```", _replace, text, flags=re.DOTALL)


def _convert_bullet_tables(text: str) -> str:
    """Detect pipe-in-bullet table patterns and convert to proper markdown tables."""
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        bullet_match = re.match(r"^\s*[•\-\*]\s+.+$", line)
        if bullet_match and " | " in line:
            group = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if re.match(r"^\s*[•\-\*]\s+.+$", next_line) and " | " in next_line:
                    group.append(next_line)
                    j += 1
                else:
                    break
            if len(group) >= 2 and _is_kv_bullet_group(group):
                result.extend(_bullet_group_to_table(group))
                i = j
                continue
        result.append(line)
        i += 1
    return "\n".join(result)


def _colorize_json(text: str) -> str:
    """Apply ANSI color coding to a JSON string."""
    if _a11y_plain_mode():
        return text
    import re as _re_json
    # Keys (quoted strings before colon) → cyan
    text = _re_json.sub(r'"([^"]+)"(\s*:)', f'{_CY}"\\1"{_R}\\2', text)
    # String values → green
    text = _re_json.sub(r':\s*"([^"]*)"', f': {_GR}"\\1"{_R}', text)
    # Numbers → yellow
    text = _re_json.sub(r':\s*(-?\d+(?:\.\d+)?)', f': {_YE}\\1{_R}', text)
    # Booleans and null → magenta
    text = _re_json.sub(r'\b(true|false|null)\b', f'{_MA}\\1{_R}', text)
    return text


def _detect_and_format_json(text: str) -> str:
    """Detect bare JSON objects/arrays in response text and pretty-print them."""
    if not _PREFS.get("json_autoformat", True) or _a11y_plain_mode():
        return text

    lines = text.split("\n")
    result: list[str] = []
    i = 0
    in_code_block = False

    while i < len(lines):
        line = lines[i]

        # Track code blocks — don't touch content inside them
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            i += 1
            continue

        if in_code_block:
            result.append(line)
            i += 1
            continue

        stripped = line.strip()

        # Detect start of JSON: line starts with { or [
        if stripped.startswith("{") or stripped.startswith("["):
            # First try just this single line
            try:
                obj = json.loads(stripped)
                pretty = json.dumps(obj, indent=2)
                pretty_colored = _colorize_json(pretty)
                result.append("```json")
                result.extend(pretty_colored.split("\n"))
                result.append("```")
                i += 1
                continue
            except json.JSONDecodeError:
                pass
            # Then try accumulating more lines (multi-line JSON)
            json_lines = [line]
            j = i + 1
            matched = False
            while j < len(lines) and j < i + 50:
                json_lines.append(lines[j])
                candidate = "\n".join(json_lines)
                try:
                    obj = json.loads(candidate.strip())
                    pretty = json.dumps(obj, indent=2)
                    pretty_colored = _colorize_json(pretty)
                    result.append("```json")
                    result.extend(pretty_colored.split("\n"))
                    result.append("```")
                    i = j + 1
                    matched = True
                    break
                except json.JSONDecodeError:
                    j += 1
            if not matched:
                result.append(line)
                i += 1
            continue

        result.append(line)
        i += 1

    return "\n".join(result)


def _preprocess_response_text(text: str) -> tuple[str, str | None]:
    """Clean up raw LLM response text for better CLI rendering.

    Returns (cleaned_body, sources) where sources may be None.

    Steps:
      A. Strip recovery note blocks (before anything else so they don't interfere).
      B. Strip trailing ``_via model-name_`` trailer added by some proxied models.
      C. Extract the Sources section (if present) so it can be rendered separately.
      D. Strip inline [N] citation markers.
      E. Unwrap fenced code blocks that contain only pipe-in-bullet table rows.
      F. Convert pipe-in-bullet table patterns to proper markdown tables.
    """
    # A. Strip server-appended recovery note blocks — do this FIRST before any other
    # manipulation so the block is always present in text regardless of ordering.
    # Matches both \n\n and \n before the blockquote opener, and captures until
    # the blockquote section ends (no more > lines).
    text = re.sub(
        r"\n{1,2}> ℹ️ \*\*Recovery note:\*\*\n(?:> [^\n]*\n?)*",
        "",
        text,
    )
    # Also strip bare-text recovery note blocks (no blockquote markers) in case
    # the model emits the recovery note without > prefix after some processing.
    text = re.sub(
        r"\n{1,2}ℹ️ \*?\*?Recovery note\*?\*?:?[^\n]*\n(?:[^\n]*\n?){0,6}",
        "",
        text,
    )

    # B. Strip _via model_ trailer — search broadly near the end (last 3 lines)
    # rather than only at EOF so it's caught even when other trailers follow it.
    text = re.sub(r"\n_via [^\n]+_[ \t]*(?=\n|$)", "", text)
    text = text.rstrip()

    # C. Extract Sources / **Sources** block at the end.
    # Matches bullet lists (- / *) AND numbered lists (1. 2. 3.) after a Sources heading.
    # Finds ALL occurrences, keeps the longest (most complete), strips all from body.
    sources: str | None = None
    all_matches = list(_RE_SOURCES_BLOCK.finditer(text))
    if all_matches:
        # Use the match with the most content (longest group 1) as the canonical sources
        best = max(all_matches, key=lambda m: len(m.group(1)))
        sources = best.group(0).strip()
        # Strip ALL sources blocks from body (reverse order to preserve indices)
        for m in reversed(all_matches):
            text = text[: m.start()] + text[m.end():]
        text = text.rstrip()

    # D. Strip bare inline citation markers like [1], [2], [12]
    # Guard against stripping markdown link text like [text](url) — only remove
    # patterns where the bracket content is purely digits and not followed by (
    text = re.sub(r"\[(\d{1,2})\](?!\()", "", text)

    # E. Unwrap fenced code blocks that are really pipe-in-bullet tables
    text = _unwrap_code_block_tables(text)

    # F. Convert pipe-in-bullet table patterns to real markdown tables
    text = _convert_bullet_tables(text)

    return text, sources


def _auto_bold_response(text: str) -> str:
    """Apply auto-bolding to key terms in AI response text.

    Post-processes the response body to make dollar amounts, percentages,
    and filenames visually pop. Skips fenced code blocks, table rows, and
    blockquotes. Only active when auto_bold pref is True and not in plain mode.
    """
    if _a11y_plain_mode() or not _PREFS.get("auto_bold", True):
        return text

    lines = text.split("\n")
    result = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue
        if in_code_block or line.startswith("|") or line.startswith(">"):
            result.append(line)
            continue

        # 1. Dollar amounts — skip if already bolded
        line = re.sub(
            r'(?<!\*)\$(\d[\d,\.]*(?:\s*(?:million|billion|trillion|thousand|[KMBkmb]))?)\b(?!\*)',
            r'**$\1**',
            line,
        )
        # 2. Percentages — skip if already bolded
        line = re.sub(
            r'(?<!\*)(\d+(?:\.\d+)?%)(?!\*)',
            r'**\1**',
            line,
        )
        # 3. File extensions — wrap in backticks if not already
        line = re.sub(
            r'(?<![`\w])(\w[\w\-]*\.(?:py|md|json|yaml|yml|sh|txt|js|ts|go|rs|html|css))(?![`\w])',
            r'`\1`',
            line,
        )

        result.append(line)

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Smart markdown table renderer — handles wide tables gracefully
# ---------------------------------------------------------------------------

_MD_TABLE_BLOCK = re.compile(
    r"(?m)^(\|[^\n]+\n\|[-:| ]+\|(?:\n\|[^\n]+)*)",
)
_RE_SOURCES_BLOCK = re.compile(
    r"\n{1,2}(?:\*\*Sources\*\*|Sources):?\s*\n((?:(?:[-\*]|\d+\.)\s+.+\n?)+)",
    re.IGNORECASE,
)


def _strip_inline_md(text: str) -> str:
    """Strip common inline markdown markers (bold, italic, code) from a cell string."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    # Strip stray leading/trailing asterisks not caught above
    return text.strip().strip("*").strip()


def _parse_md_table(block: str) -> tuple[list[str], list[list[str]]] | None:
    """Parse a markdown table block into (headers, rows). Returns None on failure."""
    lines = [l for l in block.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    sep_line = lines[1]
    if not re.match(r"^\|[-:| ]+\|\s*$", sep_line):
        return None

    def _parse_row(line: str) -> list[str]:
        return [_strip_inline_md(p) for p in line.strip().strip("|").split("|")]

    headers = _parse_row(lines[0])
    rows = [_parse_row(l) for l in lines[2:] if l.strip() and "|" in l]
    if not headers:
        return None
    return headers, rows


def _render_md_table_rich(headers: list[str], rows: list[list[str]]) -> None:
    """Render a parsed markdown table using a Rich Table with sensible column widths.

    When too many columns exist to fit the terminal, the first column wraps
    (it's usually a label/name) and remaining columns share the available space.
    """
    term_cols = shutil.get_terminal_size((120, 24)).columns
    n = len(headers)
    if n == 0:
        return

    # Compute natural width of each column (max of header + values, capped)
    MAX_COL = 24
    MIN_COL = 5
    natural: list[int] = []
    for i, h in enumerate(headers):
        cell_max = max((len(r[i]) if i < len(r) else 0) for r in rows) if rows else 0
        natural.append(max(MIN_COL, min(max(len(h), cell_max), MAX_COL)))

    # Total needed: sum of column widths + 3 chars per column (border + padding)
    overhead = n * 3 + 1
    available = term_cols - overhead
    total_natural = sum(natural)

    if total_natural <= available:
        col_widths = natural
    else:
        # Scale down proportionally, respecting MIN_COL floor
        scale = max(0.3, available / total_natural)
        col_widths = [max(MIN_COL, int(w * scale)) for w in natural]

    table = _RichTable(
        border_style="bold white" if _a11y_high_contrast() else "dim",
        show_edge=True,
        pad_edge=True,
        header_style="bold bright_white" if _a11y_high_contrast() else "bold cyan",
    )
    for i, (h, w) in enumerate(zip(headers, col_widths)):
        # First column (labels/names) folds; numeric columns truncate cleanly
        overflow_mode = "fold" if i == 0 else "ellipsis"
        table.add_column(h, max_width=w, overflow=overflow_mode, no_wrap=(i > 0))

    for row in rows:
        cells = list(row) + [""] * max(0, n - len(row))
        table.add_row(*cells[:n])

    _RICH_CONSOLE.print(table)


def _clean_sources_for_display(sources: str) -> list[str]:
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
        status_style = "bold green" if response.healthy is True else ("bold yellow" if response.healthy is False else "dim")
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
            _RICH_CONSOLE.print(_RichPanel(_RichGroup(t, _RichText(response.payload.strip(), style="dim")), border_style=border, padding=(0, 1)))
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


@dataclass
class ChatCommandContext:
    """Mutable context passed to every slash-command handler."""

    history: list[dict[str, str]]
    session_id: str
    args: str = ""  # text after the command name, stripped
    config: Any = None  # CliConfig instance when running inside run_chat
    route_metadata: dict[str, Any] | None = None
    command_ok: bool = True
    command_summary: str = ""


@dataclass
class SlashCommand:
    """A single registered slash command with optional aliases."""

    name: str
    description: str
    handler: Callable[["ChatCommandContext"], str]
    aliases: tuple[str, ...] = ()


class ChatCommandRegistry:
    """Maps slash-command names (without the leading /) to handlers."""

    def __init__(self) -> None:
        self._commands: list[SlashCommand] = []
        self._lookup: dict[str, SlashCommand] = {}

    def register(self, cmd: SlashCommand) -> None:
        self._commands.append(cmd)
        self._lookup[cmd.name] = cmd
        for alias in cmd.aliases:
            self._lookup[alias] = cmd

    def dispatch(self, text: str, ctx: ChatCommandContext) -> str | None:
        """Route *text* to a handler if it starts with '/'.

        Returns a sentinel string (_CMD_CONTINUE or _CMD_QUIT) when handled,
        or None when the text is not a recognised slash command.

        Text after the command name is placed in ``ctx.args`` so handlers can
        accept optional arguments without needing separate registry entries.
        """
        if not text.startswith("/"):
            return None
        parts = text[1:].split(maxsplit=1)
        cmd_name = parts[0] if parts else ""
        if not cmd_name:
            return None
        cmd = self._lookup.get(cmd_name)
        if cmd is None:
            return None
        ctx.args = parts[1] if len(parts) > 1 else ""
        ctx.command_ok = True
        ctx.command_summary = ""
        return cmd.handler(ctx)

    def list_commands(self) -> list[SlashCommand]:
        """Return the primary commands in registration order."""
        return list(self._commands)


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
    except Exception as exc:
        _print_error(f"unable to capture safety checkpoint for {_routed_plan_step_label(metadata)}: {exc}")
        _set_command_result(ctx, ok=False, summary=f"checkpoint failed: {exc}")
        return False
    return True


def _cmd_quit(ctx: ChatCommandContext) -> str:
    return _CMD_QUIT


def _cmd_help(ctx: ChatCommandContext) -> str:
    token = ctx.args.strip().lower()
    if token.startswith("search "):
        print_chat_help(search=token[7:].strip())
    else:
        print_chat_help()
    return _CMD_CONTINUE


def _set_command_result(ctx: ChatCommandContext, *, ok: bool, summary: str = "") -> None:
    ctx.command_ok = ok
    ctx.command_summary = str(summary or "").strip()


def _cmd_clear(ctx: ChatCommandContext) -> str:
    n = len(ctx.history)
    ctx.history.clear()
    if ctx.session_id:
        append_event(
            ctx.session_id,
            kind="chat",
            content="/clear",
            metadata={"summary": "cleared chat history"},
        )
    _print_feedback("Conversation history cleared.", level="success", detail=f"{n} message(s) removed")
    return _CMD_CONTINUE


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
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    _print_session_summary(session)
    return _CMD_CONTINUE


def _cmd_context(ctx: ChatCommandContext) -> str:
    """/context — show the effective local grounding for the active session."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    summary_lines = [
        f"cwd: {session.cwd or '(none)'}",
        _progress_cell("files", str(len(session.files or [])), status="active" if session.files else "idle"),
        _progress_cell("plan", session.plan_id or "none", status="active" if session.plan_id else "idle"),
        _progress_cell("task", session.task_id or "none", status="active" if session.task_id else "idle"),
    ]
    detail_lines = []
    if session.files:
        detail_lines.extend(f"file: {path}" for path in session.files)
    else:
        detail_lines.append("files: (none tracked)")
    if session.plan_id:
        plan_validation = _validate_plan_id_local(session.plan_id, cwd=session.cwd)
        detail_lines.append(f"plan: {session.plan_id}{_link_validation_suffix(plan_validation)}")
    if session.task_id:
        task_validation = _validate_task_id_local(session.task_id, cwd=session.cwd)
        detail_lines.append(f"task: {session.task_id}{_link_validation_suffix(task_validation)}")
    grounding_preview = _render_effective_grounding_preview(session)
    if grounding_preview:
        detail_lines.append("effective grounding preview:")
        detail_lines.extend(str(grounding_preview).splitlines())
    sys_prompt = _PREFS.get("system_prompt", "").strip()
    if sys_prompt:
        preview = sys_prompt[:80] + ("…" if len(sys_prompt) > 80 else "")
        detail_lines.append(f"system: {preview}")
    _inj = globals().get("_next_inject", "")
    if _inj:
        detail_lines.append(f"inject: ({len(_inj)} chars pending)")
    action_lines = []
    if not session.files:
        action_lines.append("/files add <path> to add grounding files")
    else:
        action_lines.append("/files to review or remove tracked files")
    if session.plan_id or session.task_id:
        action_lines.append("/session to compare grounding against session health")
    else:
        action_lines.append("/plan <id> or /task <id> to strengthen work context")
    _print_dashboard_surface(
        "Context Dashboard",
        summary_lines=summary_lines,
        detail_lines=detail_lines,
        action_lines=action_lines,
    )
    return _CMD_CONTINUE


def _cmd_cwd(ctx: ChatCommandContext) -> str:
    """/cwd [path] — show or switch the session working directory."""
    session = _require_session_or_warn(ctx)
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
        _print_error(f"not a directory: {resolved}")
        return _CMD_CONTINUE
    update_session(ctx.session_id, cwd=resolved)
    append_event(
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


def _cmd_files(ctx: ChatCommandContext) -> str:
    """/files [add <path> | rm <path>] — list, add, or remove tracked files."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    raw = ctx.args.strip()
    if not raw:
        # List mode
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
            print("Usage: /files add <path>")
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
        append_event(
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
            print("Usage: /files rm <path>")
            return _CMD_CONTINUE
        resolved = str(Path(target).expanduser().resolve())
        current = list(session.files)
        # Match on resolved path or basename
        matched = [f for f in current if f == resolved or f == target or Path(f).name == target]
        if not matched:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"[yellow]not tracked:[/] {target}")
            else:
                print(f"Not tracked: {target}")
            return _CMD_CONTINUE
        for m in matched:
            current.remove(m)
        update_session(ctx.session_id, files=current)
        append_event(
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
        print("Usage: /files  |  /files add <path>  |  /files rm <path>")

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# Wave 12: Watch status helpers + /watch REPL command
# ---------------------------------------------------------------------------

def _print_watch_status(state: dict[str, Any]) -> None:
    """Render a compact watch-state status panel."""
    state = normalize_watch_state(state)
    goal = str(state.get("goal") or "").strip()
    mode = str(state.get("mode") or "").strip()
    w_status = str(state.get("status") or "").strip()
    poll_count = int(state.get("poll_count") or 0)
    max_polls = int(state.get("max_polls") or 0)
    failure_count = int(state.get("failure_count") or 0)
    retry_limit = int(state.get("retry_limit") or 3)
    last_run_at = str(state.get("last_run_at") or "").strip()
    interval_seconds = int(state.get("interval_seconds") or 0)
    last_error = str(state.get("last_error") or "").strip()
    last_summary = str(state.get("last_summary") or "").strip()
    timing = _watch_timing_summary(state)
    operator_snapshot = _session_operator_snapshot(
        SessionSummary(
            session_id=str(state.get("session_id") or "watch"),
            title=str(goal or "Watch session"),
            cwd=str(state.get("cwd") or ""),
            files=list(state.get("files") or []),
            plan_id=str(state.get("plan_id") or ""),
            task_id=str(state.get("task_id") or ""),
            status=str(w_status or "active"),
            last_summary=last_summary,
        ),
        watch_state=state,
    )
    polls_value = f"{poll_count}/{max_polls or '∞'}"

    phase_status = "retry" if w_status == "retrying" else "active"
    summary_lines = []
    if goal:
        summary_lines.append(goal[:80])
    summary_lines.extend(
        [
            _progress_cell("mode", mode or "watch", status=w_status or "active"),
            _progress_cell("status", w_status or "unknown", status=w_status or "unknown"),
            _progress_cell("polls", polls_value, status=w_status or "active"),
        ]
    )
    if w_status in {"completed", "complete"}:
        summary_lines.append(_progress_cell("mood", "milestone reached · latest watch loop finished cleanly", status="complete"))
    elif w_status == "retrying" or failure_count:
        summary_lines.append(_progress_cell("mood", "resilient recovery · retry budget still active", status="retry"))
    elif poll_count >= 2 or last_summary:
        summary_lines.append(_progress_cell("mood", "building momentum · signals are settling in", status="active"))
    detail_lines = []
    if failure_count:
        detail_lines.append(_progress_cell("failures", f"{failure_count}/{retry_limit}", status="retry"))
    else:
        detail_lines.append(_progress_cell("retry budget", str(retry_limit), status="idle"))
    if interval_seconds:
        detail_lines.append(_progress_cell("interval", f"{interval_seconds}s", status="waiting"))
    if timing["active_phase"]:
        phase_line = timing["active_phase"]
        if timing["active_phase_elapsed"] is not None:
            phase_line += f" · {_format_elapsed_compact(timing['active_phase_elapsed'])}"
        detail_lines.append(_progress_cell("phase", phase_line, status=phase_status))
    if timing["latest_duration"] is not None:
        detail_lines.append(_progress_cell("last duration", _format_elapsed_compact(timing["latest_duration"]), status="info"))
    if timing["retry_delay_total"]:
        detail_lines.append(_progress_cell("backoff", _format_elapsed_compact(timing["retry_delay_total"]), status="retry"))
    if last_run_at:
        detail_lines.append(f"last run: {last_run_at}")
    if last_summary:
        detail_lines.append(f"last output: {last_summary[:80]}")
    if last_error:
        detail_lines.append(f"last error: {last_error[:80]}")
    detail_lines.extend(_watch_focus_lines(state))
    detail_lines.extend(_operator_snapshot_lines(operator_snapshot)[:5])
    action_lines = [
        "/watch history to inspect checkpoint history",
        "/watch intervene <msg> to leave an operator breadcrumb",
    ]
    if w_status in {"completed", "complete"}:
        action_lines.insert(0, "/session to review the resulting session snapshot")
    else:
        action_lines.insert(0, "/watch retry-limit N to tune retry budget")
    if last_error or failure_count:
        action_lines.append('/watch intervene "recovery note" to guide the next loop')
    if list(state.get("interventions") or []):
        action_lines.append("/collab share to capture the operator-facing snapshot")
    action_lines = _dedupe_preserve_order(action_lines)
    _print_dashboard_surface(
        "Watch Control Tower",
        summary_lines=summary_lines,
        detail_lines=detail_lines,
        action_lines=action_lines,
        border_style="cyan",
    )


def _print_watch_history(state: dict[str, Any]) -> None:
    """Render recent watch progress log, retries, and operator notes."""
    state = normalize_watch_state(state)
    progress_log = list(state.get("progress_log") or [])
    retry_history = list(state.get("retry_history") or [])
    notes = [e for e in list(state.get("interventions") or []) if e.get("action") == "operator-note"]

    if not progress_log and not retry_history and not notes:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]No watch history yet.[/]")
        else:
            print("No watch history yet.")
        return

    summary_lines = [
        _progress_cell("recent checkpoints", str(len(progress_log[-10:])), status="active" if progress_log else "idle"),
        _progress_cell("retries", str(len(retry_history[-3:])), status="retry" if retry_history else "idle"),
        _progress_cell("operator notes", str(len(notes[-3:])), status="info" if notes else "idle"),
    ]
    detail_lines = []
    focus_lines = _watch_focus_lines(state)
    if focus_lines:
        detail_lines.append("Focused inspection:")
        detail_lines.extend(focus_lines)
    if progress_log:
        detail_lines.append("Recent progress:")
        for entry in progress_log[-10:]:
            ts = str(entry.get("timestamp") or entry.get("at") or "").strip()
            ts_short = ts[11:19] if len(ts) > 10 else ts
            phase = str(entry.get("phase") or "poll").strip()
            note = str(entry.get("note") or entry.get("summary") or entry.get("content") or "").strip()
            elapsed = _elapsed_seconds(entry.get("created_at"))
            suffix = f" ({_format_elapsed_compact(elapsed)} ago)" if elapsed is not None else ""
            entry_status = "complete" if entry.get("ok") else "warn" if entry.get("warning") else "active"
            detail_lines.append(f"{ts_short}  {_status_cell(entry_status, detail=phase)}  {note[:100]}{suffix}")
    if retry_history:
        detail_lines.append("Retry checkpoints:")
        for entry in retry_history[-3:]:
            ts = str(entry.get("at") or entry.get("timestamp") or "").strip()
            ts_short = ts[11:19] if len(ts) > 10 else ts
            reason = str(entry.get("reason") or entry.get("error") or "").strip()
            delay = entry.get("delay_seconds")
            delay_text = f" · backoff {_format_elapsed_compact(delay)}" if delay else ""
            detail_lines.append(f"{ts_short}  {_status_cell('retry')}  {reason[:100]}{delay_text}")
    if notes:
        detail_lines.append("Operator notes:")
        for note_entry in notes[-3:]:
            ts = str(note_entry.get("created_at") or "").strip()
            ts_short = ts[11:19] if len(ts) > 10 else ts
            reason = str(note_entry.get("reason") or "").strip()
            detail_lines.append(f"{ts_short}  {_status_cell('info', detail='operator-note')}  {reason[:100]}")
    _print_dashboard_surface(
        "Watch History",
        summary_lines=summary_lines,
        detail_lines=detail_lines,
        action_lines=_dedupe_preserve_order(
            [
                "/watch status to return to the live control tower",
                "/watch intervene <msg> to annotate the next checkpoint",
                "/watch retry-limit N to tune recovery budget after repeated retries" if retry_history else "",
                "/collab share to carry forward the latest operator note" if notes else "",
            ]
        ),
        border_style="dim",
    )


def _cmd_watch(ctx: ChatCommandContext) -> str:
    """/watch [status|history|retry-limit N|intervene TEXT] — inspect or control an active watch session."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    raw = ctx.args.strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else "status"
    rest = parts[1].strip() if len(parts) > 1 else ""

    state = load_watch_state(ctx.session_id)

    if sub in ("status", ""):
        if state is None:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No active watch session.[/]  Start one with [cyan]openclaw watch --goal …[/]")
            else:
                print("No active watch session. Start one with: openclaw watch --goal …")
            return _CMD_CONTINUE
        _print_watch_status(state)

    elif sub == "history":
        if state is None:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No watch history found.[/]")
            else:
                print("No watch history found.")
            return _CMD_CONTINUE
        _print_watch_history(state)

    elif sub == "retry-limit":
        if not rest:
            _print_error("Usage: /watch retry-limit N")
            return _CMD_CONTINUE
        try:
            n = max(1, int(rest.split()[0]))
        except ValueError:
            _print_error("Usage: /watch retry-limit N  (N must be a positive integer)")
            return _CMD_CONTINUE
        if state is None:
            _print_error("No active watch session to update.")
            return _CMD_CONTINUE
        state["retry_limit"] = n
        save_watch_state(ctx.session_id, state)
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]✓[/] retry limit set to [cyan]{n}[/]")
        else:
            print(f"retry limit set to {n}")

    elif sub == "intervene":
        note_text = rest.strip('"').strip("'").strip()
        if not note_text:
            _print_error('Usage: /watch intervene "note text"')
            return _CMD_CONTINUE
        if state is None:
            _print_error("No active watch session to add a note to.")
            return _CMD_CONTINUE
        import uuid as _uuid_mod
        from datetime import datetime as _dt, timezone as _tz
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
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]✓[/] operator note recorded  [dim]{note_text[:60]}[/]")
        else:
            print(f"operator note recorded: {note_text[:60]}")

    else:
        _print_error("Usage: /watch [status|history|retry-limit N|intervene TEXT]")

    return _CMD_CONTINUE


def _cmd_plan(ctx: ChatCommandContext) -> str:
    """/plan [<id> | status | focus | unlink] — show, link, focus, or unlink a plan for this session."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    arg = ctx.args.strip()
    if not arg:
        if session.plan_id:
            validation = _validate_plan_id_local(session.plan_id, cwd=session.cwd)
            suffix = _link_validation_suffix(validation)
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"📋 plan: [yellow]{session.plan_id}[/]{suffix}")
            else:
                print(f"plan: {session.plan_id}{suffix}")
        else:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No plan linked. Use:[/] /plan <id>")
            else:
                print("No plan linked. Use: /plan <id>")
        return _CMD_CONTINUE

    # /plan status — show linked plan details
    if arg.lower() == "status":
        if not session.plan_id:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No plan linked. Use:[/] /plan <id>")
            else:
                print("No plan linked. Use: /plan <id>")
            return _CMD_CONTINUE
        validation = _validate_plan_id_local(session.plan_id, cwd=session.cwd)
        if _RICH_AVAILABLE and _IS_TTY:
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
            _RICH_CONSOLE.print(_RichPanel(grid, title="[bold cyan]📋 Plan Status[/]", border_style="cyan", padding=(0, 1)))
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
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No plan linked.[/]")
            else:
                print("No plan linked.")
            return _CMD_CONTINUE
        validation = _validate_plan_id_local(session.plan_id, cwd=session.cwd)
        if not validation.exists or not validation.source:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"[yellow]⚠[/] Plan file not found locally for [yellow]{session.plan_id}[/].")
            else:
                print(f"Plan file not found locally for {session.plan_id}.")
            return _CMD_CONTINUE
        try:
            plan_text = Path(validation.source).read_text(encoding="utf-8")
        except OSError:
            _print_error(f"Could not read plan file: {validation.source}")
            return _CMD_CONTINUE
        # Find first unchecked task (- [ ]) and the next one after it
        lines = plan_text.splitlines()
        unchecked = [(i, l) for i, l in enumerate(lines) if re.match(r"^\s*-\s+\[ \]", l)]
        done_count = sum(1 for l in lines if re.match(r"^\s*-\s+\[x\]", l, re.IGNORECASE))
        if not unchecked:
            msg = "All tasks complete!" if done_count > 0 else "No task items found in plan."
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"[green]✅ {msg}[/]  [dim]{session.plan_id}[/]")
            else:
                print(f"{msg}  ({session.plan_id})")
            return _CMD_CONTINUE
        focus_lines: list[str] = []
        if validation.summary:
            focus_lines.append(f"Goal: {validation.summary}")
            focus_lines.append("")
        focus_lines.append(f"Done: {done_count}  Remaining: {len(unchecked)}")
        focus_lines.append("")
        # Current step
        cur_idx, cur_line = unchecked[0]
        focus_lines.append("▶ Current:")
        focus_lines.append(f"  {cur_line.strip()}")
        # Show a few context lines after the current step (sub-tasks or notes)
        for ctx_line in lines[cur_idx + 1: cur_idx + 4]:
            if ctx_line.strip() and not re.match(r"^\s*-\s+\[ \]", ctx_line):
                focus_lines.append(f"    {ctx_line.strip()}")
            else:
                break
        # Next pending step
        if len(unchecked) > 1:
            _, nxt_line = unchecked[1]
            focus_lines.append("")
            focus_lines.append("→ Next:")
            focus_lines.append(f"  {nxt_line.strip()}")
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(_RichPanel(
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
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No plan is currently linked.[/]")
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
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[dim]unlinked plan:[/] {old}")
        else:
            print(f"unlinked plan: {old}")
        return _CMD_CONTINUE

    validation = _validate_plan_id_local(arg, cwd=session.cwd)
    if not validation.available:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]local plan validation unavailable; linking anyway.[/]")
        else:
            print("local plan validation unavailable in this install; linking anyway.")
    elif validation.exists:
        detail = f": {validation.summary}" if validation.summary else ""
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]✓[/] confirmed plan [yellow]{arg}[/]{detail}")
        else:
            print(f"confirmed local plan '{arg}'{detail}")
    else:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[yellow]⚠[/] plan [dim]{arg}[/] not found locally; linking anyway.")
        else:
            print(f"warning: local plan '{arg}' was not found; linking anyway.")
    update_session(ctx.session_id, plan_id=arg)
    append_event(
        ctx.session_id,
        kind="chat",
        content=f"/plan {arg}",
        metadata={"summary": f"linked plan {arg}"},
    )
    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(f"📋 plan → [yellow]{arg}[/]")
    else:
        print(f"plan → {arg}")
    return _CMD_CONTINUE


def _cmd_task(ctx: ChatCommandContext) -> str:
    """/task [<id> | unlink] — show, link, or unlink a task for this session."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    arg = ctx.args.strip()
    if not arg:
        if session.task_id:
            validation = _validate_task_id_local(session.task_id, cwd=session.cwd)
            suffix = _link_validation_suffix(validation)
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"✅ task: [yellow]{session.task_id}[/]{suffix}")
            else:
                print(f"task: {session.task_id}{suffix}")
        else:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No task linked. Use:[/] /task <id>")
            else:
                print("No task linked. Use: /task <id>")
        return _CMD_CONTINUE

    if arg.lower() == "unlink":
        if not session.task_id:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No task is currently linked.[/]")
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
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[dim]unlinked task:[/] {old}")
        else:
            print(f"unlinked task: {old}")
        return _CMD_CONTINUE

    validation = _validate_task_id_local(arg, cwd=session.cwd)
    if not validation.available:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]local task validation unavailable; linking anyway.[/]")
        else:
            print("local task validation unavailable in this install; linking anyway.")
    elif validation.exists:
        detail = f": {validation.summary}" if validation.summary else ""
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]✓[/] confirmed task [yellow]{arg}[/]{detail}")
        else:
            print(f"confirmed local task '{arg}'{detail}")
    else:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[yellow]⚠[/] task [dim]{arg}[/] not found locally; linking anyway.")
        else:
            print(f"warning: local task '{arg}' was not found; linking anyway.")
    update_session(ctx.session_id, task_id=arg)
    append_event(
        ctx.session_id,
        kind="chat",
        content=f"/task {arg}",
        metadata={"summary": f"linked task {arg}"},
    )
    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(f"✅ task → [yellow]{arg}[/]")
    else:
        print(f"task → {arg}")
    return _CMD_CONTINUE


def _cmd_events(ctx: ChatCommandContext) -> str:
    """/events [n|decisions [n]] — show the last n events; 'decisions' filters to routing/decision kinds."""
    session = _require_session_or_warn(ctx)
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
                _print_error("Usage: /events decisions [n]")
                return _CMD_CONTINUE
    elif args:
        try:
            n = int(args)
        except ValueError:
            _print_error("Usage: /events [n|decisions [n]]")
            return _CMD_CONTINUE

    # Load more events when filtering so we have enough after the filter
    load_limit = n * 10 if decisions_only else n
    events = load_events(ctx.session_id, limit=load_limit)

    if decisions_only:
        events = [ev for ev in events if str(ev.get("kind") or "").strip() in _DECISION_KINDS]
        events = events[:n]

    if not events:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]No events recorded yet.[/]  [dim]Events appear after /analyze, /write, /exec, /edit, or chat turns.[/]")
        else:
            print("No events recorded yet. Events appear after /analyze, /write, /exec, /edit, or chat turns.")
        return _CMD_CONTINUE

    _KIND_COLORS = {
        "chat": "dim", "prompt": "white", "analyze": "cyan", "research": "blue",
        "write": "yellow", "exec": "bold yellow", "assistant": "green",
        "edit": "magenta", "error": "red", "watch": "cyan",
        "route": "bold cyan", "plan": "bold blue", "approval": "bold yellow",
        "checkpoint": "bold green", "exec": "bold yellow",
    }
    if _RICH_AVAILABLE and _IS_TTY:
        if decisions_only:
            _RICH_CONSOLE.print("[dim]Decision-only view — routing, approval, exec, edit events[/]")
        table = _RichTable(border_style="dim", show_edge=True, pad_edge=True, header_style="bold cyan")
        table.add_column("Time", style="dim", no_wrap=True)
        table.add_column("Kind", no_wrap=True)
        table.add_column("Summary")
        for ev in events:
            ts = str(ev.get("timestamp") or ev.get("at") or ev.get("created_at") or "").strip()
            ts_short = ts[11:19] if len(ts) > 10 else ts  # HH:MM:SS portion
            kind = str(ev.get("kind") or "").strip()
            meta = ev.get("metadata") or {}
            summary = str(meta.get("summary") if isinstance(meta, dict) else "").strip()
            content = str(ev.get("content") or "").strip()
            label = (summary or content[:80]).replace("\n", " ")
            if isinstance(meta, dict):
                timing_bits = []
                if meta.get("elapsed_seconds") is not None:
                    timing_bits.append(_format_elapsed_compact(meta.get("elapsed_seconds")))
                if meta.get("approval_seconds") is not None:
                    timing_bits.append(f"approval {_format_elapsed_compact(meta.get('approval_seconds'))}")
                if meta.get("retry_delay_seconds") is not None:
                    timing_bits.append(f"backoff {_format_elapsed_compact(meta.get('retry_delay_seconds'))}")
                if timing_bits:
                    label = f"{label}  ({', '.join(timing_bits)})"
            if kind == "checkpoint":
                label = f"{label} · milestone"
            elif kind == "collab":
                label = f"{label} · shared momentum"
            elif kind == "error":
                label = f"{label} · recovery needed"
            color = _KIND_COLORS.get(kind, "dim")
            table.add_row(ts_short, f"[{color}]{kind}[/]", label)
        _RICH_CONSOLE.print(table)
    else:
        if decisions_only:
            print("Decision-only view — routing, approval, exec, edit events")
        for ev in events:
            ts = str(ev.get("timestamp") or ev.get("at") or ev.get("created_at") or "").strip()
            kind = str(ev.get("kind") or "").strip()
            meta = ev.get("metadata") or {}
            summary = str(meta.get("summary") if isinstance(meta, dict) else "").strip()
            content = str(ev.get("content") or "").strip()
            label = summary or content[:100]
            if isinstance(meta, dict):
                timing_bits = []
                if meta.get("elapsed_seconds") is not None:
                    timing_bits.append(_format_elapsed_compact(meta.get("elapsed_seconds")))
                if meta.get("approval_seconds") is not None:
                    timing_bits.append(f"approval {_format_elapsed_compact(meta.get('approval_seconds'))}")
                if meta.get("retry_delay_seconds") is not None:
                    timing_bits.append(f"backoff {_format_elapsed_compact(meta.get('retry_delay_seconds'))}")
                if timing_bits:
                    label = f"{label} ({', '.join(timing_bits)})"
            if kind == "checkpoint":
                label = f"{label} · milestone"
            elif kind == "collab":
                label = f"{label} · shared momentum"
            elif kind == "error":
                label = f"{label} · recovery needed"
            print(f"[{ts}] {kind}: {label}")
    return _CMD_CONTINUE


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


def _cmd_why(ctx: ChatCommandContext) -> str:
    """/why — explain the last routing or tool decision from session history."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    snapshot = _last_trace_snapshot(ctx.session_id)
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
        print(f"  Confidence:    {str(snapshot.get('conf_label') or '(unknown)')}")
        if snapshot.get("target_text"):
            print(f"  Target:        {str(snapshot.get('target_text') or '')[:120]}")
        if snapshot.get("args_text"):
            print(f"  Args:          {str(snapshot.get('args_text') or '')[:120]}")
        print(f"  When:          {str(snapshot.get('ts') or '')}")
    return _CMD_CONTINUE


def _cmd_trace(ctx: ChatCommandContext) -> str:
    """/trace — show the latest routing trace plus the current quality context."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    snapshot = _last_trace_snapshot(ctx.session_id)
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
    """/collab [status|share|note|decision] — collaboration notes, decisions, and handoff summaries."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    raw = ctx.args.strip()
    if not raw or raw.lower() in {"status", "summary", "share"}:
        print(_build_session_share_text(session.session_id))
        return _CMD_CONTINUE

    parts = raw.split(None, 1)
    sub = parts[0].lower()
    remainder = parts[1].strip() if len(parts) > 1 else ""

    if sub not in {"note", "decision"}:
        _print_error("Usage: /collab [status|share|note [@actor] TEXT|decision [@actor] [#tag] TEXT]")
        return _CMD_CONTINUE

    actor, tags, text = _parse_collab_entry(remainder)
    if not text:
        _print_error(f"Usage: /collab {sub} [@actor] {'[#tag] ' if sub == 'decision' else ''}TEXT")
        return _CMD_CONTINUE
    actor_label = actor or "operator"
    summary_text = " ".join(text.split())
    if len(summary_text) > 90:
        summary_text = summary_text[:89].rstrip() + "…"
    summary = f"{sub} by {actor_label}: {summary_text}"
    append_event(
        session.session_id,
        kind="collab",
        content=text,
        metadata={
            "summary": summary,
            "actor": actor_label,
            "tags": tags,
            "collab_kind": sub,
        },
    )
    if tags:
        existing_tags = list(session.tags or [])
        for tag in tags:
            session_tag = f"collab:{tag}"
            if session_tag not in existing_tags:
                existing_tags.append(session_tag)
        update_session(session.session_id, tags=existing_tags)
    print(f"Recorded {sub} by {actor_label}.")
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

    def _highlight_ansi(text: str) -> str:
        idx = text.lower().find(ql)
        if idx == -1:
            return text
        return text[:idx] + _BYE + text[idx:idx + len(query)] + _R + text[idx + len(query):]

    def _highlight_rich(text: str) -> str:
        import re as _re
        return _re.sub(
            _re.escape(query),
            f"[bold yellow]{query}[/]",
            text,
            flags=_re.IGNORECASE,
        )

    results: list[tuple[str, str, str, str]] = []  # (session_short, kind, excerpt, ts)

    if cross_session:
        all_sessions = list_sessions(limit=200)
        for sess in all_sessions:
            if len(results) >= MAX_RESULTS:
                break
            try:
                events = load_events(sess.session_id, limit=200)
            except Exception:
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
        session = _require_session_or_warn(ctx)
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
            highlighted = _highlight_rich(excerpt)
            if cross_session:
                grid.add_row(short_id, kind, highlighted, ts)
            else:
                grid.add_row(kind, highlighted, ts)
        scope = "all sessions" if cross_session else "this session"
        _RICH_CONSOLE.print(_RichPanel(grid, title=f"[bold]🔍 search results[/] [dim]{scope}[/]", border_style="cyan", padding=(0, 1)))
    else:
        scope = "all sessions" if cross_session else "this session"
        print(f"[search results — {scope}]")
        for short_id, kind, excerpt, ts in results:
            highlighted = _highlight_ansi(excerpt)
            prefix = f"{short_id} " if cross_session and short_id else ""
            print(f"  {prefix}{_DM}{kind}{_R}  {highlighted}  {_DM}{ts}{_R}")

    return _CMD_CONTINUE


def _cmd_autoroute(ctx: ChatCommandContext) -> str:
    """/autoroute [on|off] — show or set session-level REPL auto-routing."""
    session = _require_session_or_warn(ctx)
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
        _print_error("Usage: /autoroute [on|off]")
        return _CMD_CONTINUE
    enabled = raw == "on"
    update_session(ctx.session_id, repl_auto_route=enabled)
    append_event(
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


def _cmd_outputs(ctx: ChatCommandContext) -> str:
    """/outputs [<index>|<filename>|promote <index> <name>] — list or preview saved outputs."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    outputs = list_saved_outputs(session.session_id, limit=OUTPUT_LIST_LIMIT)
    if not outputs:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]No saved outputs yet.[/]  [dim]Use /write, /research, or /analyze to generate output.[/]")
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
    if wants_overlay or (_interactive_overlays_enabled() and not token):
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
            size = _format_byte_count(int(preview.get("size_bytes") or 0))
            modified_at = str(preview.get("modified_at") or "").strip()
            preview_label = f"saved output preview: {name} ({size}"
            if modified_at:
                preview_label += f"; {modified_at}"
            if preview.get("truncated"):
                preview_label += f"; preview limited to {OUTPUT_PREVIEW_MAX_CHARS} chars"
            preview_label += ")"
            print(preview_label)
            print(str(preview.get("preview") or ""))

        overlay_result = _run_interactive_overlay(
            title="Saved outputs overlay",
            items=outputs,
            label_fn=lambda item: (
                f"{str(item.get('name') or '').strip()}  "
                f"{_format_byte_count(int(item.get('size_bytes') or 0))}  "
                f"{str(item.get('modified_at') or '').strip()}  "
                f"{_single_line_excerpt(str((output_previews.get(str(item.get('name') or '').strip()) or {}).get('preview') or ''), max_chars=70)}".strip()
            ),
            on_select=_preview_output,
            initial_query=overlay_query,
            empty_message="No saved outputs yet.",
        )
        if overlay_result == "selected":
            _set_command_result(ctx, ok=True, summary="selected saved output from overlay")
            return _CMD_CONTINUE
        if wants_overlay and overlay_result == "closed":
            _set_command_result(ctx, ok=True, summary="outputs overlay closed")
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
            _progress_cell("shown", str(len(outputs)), status="active"),
            _progress_cell("recent", str(newest.get("name") or "—"), status="complete"),
            _progress_cell("freshness", "freshest first", status="info"),
        ]
        detail_lines = []
        if newest_preview:
            detail_lines.append(
                f"focused preview: {str(newest_preview.get('name') or '').strip()} · "
                f"{_format_byte_count(int(newest_preview.get('size_bytes') or 0))}"
            )
            detail_lines.extend(
                _preview_block_lines(
                    "excerpt",
                    str(newest_preview.get("preview") or ""),
                    max_chars=OUTPUT_DASHBOARD_EXCERPT_CHARS,
                )
            )
        for index, item in enumerate(outputs[:3], start=1):
            name = str(item.get("name") or "").strip()
            size = _format_byte_count(int(item.get("size_bytes") or 0))
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
        _print_dashboard_surface(
            "Outputs Dashboard",
            summary_lines=summary_lines,
            detail_lines=detail_lines,
            action_lines=_dedupe_preserve_order(action_lines),
            border_style="dim",
        )
        if _RICH_AVAILABLE and _IS_TTY:
            table = _RichTable(border_style="dim", show_edge=True, pad_edge=True, header_style="bold cyan",
                               caption=f"[dim]{len(outputs)} output(s)[/]")
            table.add_column("#", style="dim", justify="right", no_wrap=True)
            table.add_column("Filename", style="bold")
            table.add_column("Size", style="cyan", justify="right", no_wrap=True)
            table.add_column("Modified", style="dim", no_wrap=True)
            for index, item in enumerate(outputs, start=1):
                name = str(item.get("name") or "").strip()
                size = _format_byte_count(int(item.get("size_bytes") or 0))
                modified_at = str(item.get("modified_at") or "").strip()
                table.add_row(str(index), name, size, modified_at)
            _RICH_CONSOLE.print(table)
        else:
            print(f"saved outputs ({len(outputs)} shown):")
            for index, item in enumerate(outputs, start=1):
                name = str(item.get("name") or "").strip()
                size = _format_byte_count(int(item.get("size_bytes") or 0))
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
    size = _format_byte_count(int(preview.get("size_bytes") or 0))
    modified_at = str(preview.get("modified_at") or "").strip()
    trunc_note = f"  [dim]preview limited to {OUTPUT_PREVIEW_MAX_CHARS} chars[/]" if preview.get("truncated") else ""
    if _RICH_AVAILABLE and _IS_TTY:
        subtitle = f"[dim]{size}"
        if modified_at:
            subtitle += f"  ·  {modified_at}"
        subtitle += f"[/]{trunc_note}"
        _RICH_CONSOLE.print(_RichPanel(str(preview.get("preview") or ""), title=f"[bold]{name}[/]  {subtitle}", border_style="dim", padding=(0, 1)))
    else:
        preview_label = f"saved output preview: {name} ({size}"
        if modified_at:
            preview_label += f"; {modified_at}"
        if preview.get("truncated"):
            preview_label += f"; preview limited to {OUTPUT_PREVIEW_MAX_CHARS} chars"
        preview_label += ")"
        print(preview_label)
        print(str(preview.get("preview") or ""))
    _print_predictive_affordances(
        _dedupe_preserve_order(
            [
                "/outputs overlay to jump to another saved artifact" if len(outputs) > 1 else "",
                "/outputs promote <index> <name> to keep a stable copy",
                "/context to compare this artifact with current grounding" if session.files or session.plan_id or session.task_id else "",
            ]
        ),
        title="Artifact shortcuts",
        border_style="dim",
    )
    return _CMD_CONTINUE


def _cmd_snapshot(ctx: ChatCommandContext) -> str:
    """/snapshot [name] — save current git HEAD as a named restore point."""
    import subprocess
    name = ctx.args.strip() or "auto"
    is_tty = _get_is_tty()

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        sha = result.stdout.strip()[:12]

        if not sha:
            msg = "Not in a git repo or no commits yet."
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
            else:
                print(msg)
            return _CMD_CONTINUE

        snapshots = _PREFS.get("snapshots", {})
        import datetime
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        snapshots[name] = {"sha": sha, "ts": ts}
        _prefs_set("snapshots", snapshots)

        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓[/] Snapshot [bold]{name}[/] saved at [dim]{sha}[/]")
        else:
            print(f"✓ Snapshot '{name}' saved at {sha}")
    except Exception as e:
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[red]Error:[/] {e}")
        else:
            print(f"Error: {e}")

    return _CMD_CONTINUE


def _cmd_rollback(ctx: ChatCommandContext) -> str:
    """/rollback [last|list|<name>] — restore latest checkpoint, list git snapshots, or preview/exec a git snapshot rollback."""
    arg = ctx.args.strip()
    arg_lower = arg.lower()

    # Git-snapshot: list saved snapshots (no arg or explicit "list" when no checkpoints match)
    if not arg or arg_lower == "list":
        import subprocess
        is_tty = _get_is_tty()
        snapshots = _PREFS.get("snapshots", {})
        if not snapshots:
            msg = "No snapshots saved. Use /snapshot [name] to save one."
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[dim]{msg}[/]")
            else:
                print(msg)
            return _CMD_CONTINUE
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n[bold cyan]📸 Saved Snapshots[/]\n")
            for snap_name, snap_data in snapshots.items():
                sha = snap_data.get("sha", "?")
                ts = snap_data.get("ts", "")[:10]
                _RICH_CONSOLE.print(f"  [bold green]{snap_name:<20}[/] [dim]{sha}[/]  {ts}")
            _RICH_CONSOLE.print()
        else:
            print(f"\n📸 Saved Snapshots\n")
            for snap_name, snap_data in snapshots.items():
                sha = snap_data.get("sha", "?")
                ts = snap_data.get("ts", "")[:10]
                print(f"  {snap_name:<20} {sha}  {ts}")
            print()
        return _CMD_CONTINUE

    # Existing checkpoint restore: /rollback last
    if arg_lower == "last":
        session = _require_session_or_warn(ctx)
        if session is None:
            return _CMD_CONTINUE
        outcome = restore_last_routed_action_checkpoint(session.session_id)
        if outcome is None:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]—  no routed action checkpoints available for this session[/]")
            else:
                print("No routed action checkpoints are available for this session.")
            _set_command_result(ctx, ok=False, summary="no routed checkpoints")
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
            _set_command_result(ctx, ok=True, summary=f"rolled back checkpoint {checkpoint_id}")
            return _CMD_CONTINUE
        if status == "already_rolled_back":
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"[dim]—  checkpoint {checkpoint_id} was already restored[/]")
            else:
                print(f"Checkpoint {checkpoint_id} for the last routed action was already restored.")
            _set_command_result(ctx, ok=True, summary=f"checkpoint {checkpoint_id} already restored")
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
            _set_command_result(ctx, ok=False, summary=f"rollback unavailable for {checkpoint_id}")
            return _CMD_CONTINUE
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[red]✗[/] rollback failed for [dim]{checkpoint_id}[/]: {reason or 'unable to restore the latest routed action'}")
        else:
            print(f"Rollback failed for checkpoint {checkpoint_id}: {reason or 'unable to restore the latest routed action.'}")
        _set_command_result(ctx, ok=False, summary=f"rollback failed for {checkpoint_id}")
        return _CMD_CONTINUE

    # Git-snapshot: named snapshot preview or exec
    import subprocess
    is_tty = _get_is_tty()
    parts = arg.split()
    exec_mode = "--exec" in parts
    name = parts[0] if parts else ""
    snapshots = _PREFS.get("snapshots", {})

    if name not in snapshots:
        msg = f"No snapshot named '{name}'. Use /rollback list to see saved snapshots."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    sha = snapshots[name].get("sha", "")

    if exec_mode:
        try:
            result = subprocess.run(
                ["git", "checkout", sha],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                if _RICH_AVAILABLE and is_tty:
                    _RICH_CONSOLE.print(f"[green]✓[/] Rolled back to snapshot [bold]{name}[/] ({sha})")
                else:
                    print(f"✓ Rolled back to {name} ({sha})")
            else:
                if _RICH_AVAILABLE and is_tty:
                    _RICH_CONSOLE.print(f"[red]Error:[/] {result.stderr}")
                else:
                    print(f"Error: {result.stderr}")
        except Exception as e:
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[red]Error:[/] {e}")
            else:
                print(f"Error: {e}")
    else:
        try:
            result = subprocess.run(
                ["git", "diff", "--stat", f"{sha}..HEAD"],
                capture_output=True, text=True, timeout=10
            )
            diff_stat = result.stdout.strip() or "(no differences)"
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"\n[bold cyan]📸 Rollback Preview:[/] [bold]{name}[/] → current HEAD\n")
                _RICH_CONSOLE.print(f"[dim]{diff_stat}[/]")
                _RICH_CONSOLE.print(f"\n[yellow]⚠️  Use /rollback {name} --exec to actually rollback (DESTRUCTIVE)[/]\n")
            else:
                print(f"\n📸 Rollback Preview: {name} → HEAD\n{diff_stat}")
                print(f"\n⚠️  Use /rollback {name} --exec to rollback (DESTRUCTIVE)\n")
        except Exception as e:
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[red]Error:[/] {e}")
            else:
                print(f"Error: {e}")

    return _CMD_CONTINUE




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
    config = _require_config_or_warn(ctx)
    if config is None:
        return _CMD_CONTINUE
    goal = ctx.args.strip()
    if not goal:
        _print_error("Usage: /analyze <goal>")
        _set_command_result(ctx, ok=False, summary="missing analysis goal")
        return _CMD_CONTINUE
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    _, context_text = collect_workspace_context(cwd=session.cwd or None, targets=list(session.files))
    scoped_config = bind_config_to_session(config, session.session_id)
    prompt = build_analysis_prompt(goal=goal, context_text=context_text, session=session)
    append_event(
        session.session_id,
        kind="analyze",
        content=goal,
        metadata={"summary": goal, "cwd": session.cwd, "files": list(session.files)},
    )
    try:
        response = _with_spinner(
            "🔍 Analyzing…",
            invoke_openclaw,
            prompt,
            config=scoped_config,
            history=list(ctx.history),
            output_json=False,
        )
    except OpenClawCliError as exc:
        _print_error(str(exc))
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    print_response(response, output_json=config.output_json)
    persist_response(session.session_id, goal, response.response)
    ctx.history[:] = load_conversation_history(session.session_id)
    _set_command_result(
        ctx,
        ok=True,
        summary=_summarize_terminal_result(response.response, fallback=f"analysis complete for {goal}"),
    )
    return _CMD_CONTINUE


def _cmd_research(ctx: ChatCommandContext) -> str:
    """/research <query> — run the research agent using the current session context."""
    query = ctx.args.strip()
    if not query:
        _print_error("Usage: /research <query>")
        _set_command_result(ctx, ok=False, summary="missing research query")
        return _CMD_CONTINUE
    try:
        from research_agent import ResearchAgent  # type: ignore[import]
    except ImportError:
        _print_error(missing_feature_hint("openclaw research"))
        _set_command_result(ctx, ok=False, summary="research agent unavailable")
        return _CMD_CONTINUE
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    _, context_text = collect_workspace_context(cwd=session.cwd or None, targets=list(session.files))
    effective_query = query
    plan_ctx = _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd)
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

    append_event(session.session_id, kind="research", content=query, metadata={"summary": query})
    try:
        report = run_async(ResearchAgent().run(effective_query, on_progress=_progress))
    except Exception as exc:
        _print_error(str(exc))
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    if _IS_TTY:
        sys.stdout.write("\r" + " " * 62 + "\r")
        sys.stdout.flush()
    output_target = save_output(
        session.session_id,
        output_name_from_title(query, default_stem="research-report", suffix=".md"),
        report,
    )
    append_event(
        session.session_id,
        kind="assistant",
        content=report,
        metadata={"summary": f"saved research to {output_target}"},
    )
    print(report)
    _print_meta_footer(("saved", output_target))
    _set_command_result(ctx, ok=True, summary=f"saved research to {output_target}")
    return _CMD_CONTINUE


def _cmd_write(ctx: ChatCommandContext) -> str:
    """/write <task> — generate a markdown document using the current session context."""
    config = _require_config_or_warn(ctx)
    if config is None:
        return _CMD_CONTINUE
    task_text = ctx.args.strip()
    if not task_text:
        _print_error("Usage: /write <task>")
        _set_command_result(ctx, ok=False, summary="missing writing task")
        return _CMD_CONTINUE
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    _, context_text = collect_workspace_context(cwd=session.cwd or None, targets=list(session.files))
    title = task_text[:80]
    scoped_config = bind_config_to_session(config, session.session_id)
    prompt = build_write_prompt(task=task_text, context_text=context_text, session=session, title=title)
    append_event(session.session_id, kind="write", content=task_text, metadata={"summary": task_text})
    try:
        response = _with_spinner(
            "✍️  Writing…",
            invoke_openclaw,
            prompt,
            config=scoped_config,
            history=list(ctx.history),
            output_json=False,
        )
    except OpenClawCliError as exc:
        _print_error(str(exc))
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    persist_response(session.session_id, task_text, response.response)
    output_target = save_output(
        session.session_id,
        output_name_from_title(title, default_stem="draft", suffix=".md"),
        response.response,
    )
    print(response.response)
    _print_meta_footer(("saved", output_target))
    ctx.history[:] = load_conversation_history(session.session_id)
    _set_command_result(ctx, ok=True, summary=f"saved draft to {output_target}")
    return _CMD_CONTINUE


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


def _exec_progress_animate(proc: Any, label: str = "") -> tuple:
    """Animate an indeterminate progress bar while proc runs. Returns (stdout, stderr, returncode)."""
    is_tty = _get_is_tty()
    if not is_tty or _a11y_reduced_motion() or _a11y_plain_mode():
        stdout, stderr = proc.communicate()
        return stdout, stderr, proc.returncode

    width = 30
    frames = []
    for pos in list(range(0, width - 8)) + list(range(width - 8, 0, -1)):
        bar = "░" * pos + "█████████" + "░" * (width - pos - 9)
        bar = bar[:width]
        frames.append(bar)

    frame_idx = 0
    import threading
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


def _analyze_exec_error(cmd: str, stderr: str, returncode: int) -> "list[str]":
    """Analyze a failed command and return smart recovery hints."""
    if returncode == 0:
        return []
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
        import re as _re
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


def _print_exec_error_hints(cmd: str, stderr: str, returncode: int) -> None:
    """Print smart recovery hints after a failed exec command."""
    if _a11y_plain_mode():
        return
    hints = _analyze_exec_error(cmd, stderr, returncode)
    if not hints:
        return
    is_tty = _get_is_tty()

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print("\n[bold yellow]💡 Recovery hints:[/]")
        for hint in hints:
            _RICH_CONSOLE.print(f"  [dim]→[/] {hint}")
        _RICH_CONSOLE.print()
    else:
        print(f"\n{_BYE}💡 Recovery hints:{_R}")
        for hint in hints:
            print(f"  {_DM}→{_R} {hint}")
        print()


def _cmd_exec(ctx: ChatCommandContext) -> str:
    """/exec [--] <command> — run a shell command with session tracking and approval."""
    raw = ctx.args.strip()
    if raw.startswith("-- "):
        raw = raw[3:]
    if not raw:
        _print_error("Usage: /exec [--] <command>")
        _set_command_result(ctx, ok=False, summary="missing shell command")
        return _CMD_CONTINUE
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    try:
        command_parts = shlex.split(raw)
    except ValueError as exc:
        _print_error(f"invalid shell command: {exc}")
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    if not command_parts:
        _print_error("Usage: /exec [--] <command>")
        _set_command_result(ctx, ok=False, summary="missing shell command")
        return _CMD_CONTINUE
    risk_level = infer_command_risk(command_parts)
    _print_risky_action_warning(
        action="/exec",
        target=raw,
        risk_level=risk_level,
        recovery_hint="check the cwd and use your shell history or VCS tools before re-running.",
    )
    approval_started = time.monotonic()
    approved = request_cli_approval(
        action="shell.exec",
        target=raw,
        risk_level=risk_level,
        detail=f"cwd={session.cwd}",
        auto_approve=False,
        session_id=session.session_id,
        plan_id=session.plan_id,
        task_id=session.task_id,
    )
    approval_seconds = max(0.0, time.monotonic() - approval_started)
    append_event(
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
        _print_error("shell command not approved")
        _print_feedback("Approval denied.", level="warn", detail=f"after {_format_elapsed_compact(approval_seconds)}")
        _set_command_result(ctx, ok=False, summary="shell command not approved")
        return _CMD_CONTINUE
    if not _capture_routed_action_checkpoint(
        ctx,
        session=session,
        action_kind="exec",
        target=raw,
        detail=f"cwd={session.cwd}",
    ):
        return _CMD_CONTINUE
    exec_started = time.monotonic()
    _exec_cwd = session.cwd or None
    _use_animation = _get_is_tty() and not _a11y_reduced_motion() and not _a11y_plain_mode()
    try:
        if _use_animation:
            import subprocess as _sp
            _proc = _sp.Popen(
                command_parts,
                cwd=_exec_cwd,
                stdout=_sp.PIPE,
                stderr=_sp.PIPE,
            )
            _raw_stdout, _raw_stderr, _rc = _exec_progress_animate(_proc, label=raw[:50])
            from openclaw_cli_actions import ShellCommandResult, normalize_cwd
            result = ShellCommandResult(
                command=shlex.join(command_parts),
                cwd=str(normalize_cwd(_exec_cwd)),
                returncode=_rc,
                stdout=_raw_stdout.decode(errors="replace"),
                stderr=_raw_stderr.decode(errors="replace"),
                timed_out=False,
            )
        else:
            result = run_async(run_shell_command(command_parts, cwd=_exec_cwd, timeout=60))
    except Exception as exc:
        _print_error(str(exc))
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    exec_seconds = max(0.0, time.monotonic() - exec_started)
    append_event(
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
    _print_shell_result(result)
    if result.returncode != 0:
        _print_exec_error_hints(raw, result.stderr, result.returncode)
    _print_feedback(
        "Command complete.",
        level="success" if result.returncode == 0 else "warn",
        detail=(
            f"exit {result.returncode} · {_format_elapsed_compact(exec_seconds)} run"
            f" · approval {_format_elapsed_compact(approval_seconds)} · cwd {result.cwd}"
        ),
    )
    _set_command_result(
        ctx,
        ok=result.returncode == 0,
        summary=f"exit {result.returncode}: {raw}",
    )
    return _CMD_CONTINUE


def _cmd_edit(ctx: ChatCommandContext) -> str:
    """/edit <path> [--content <text> | --append <text> | --replace OLD NEW] — inspect or write a file."""
    raw = ctx.args.strip()
    if not raw:
        _print_error("Usage: /edit <path> [--content <text>] [--append <text>] [--replace OLD NEW]")
        _set_command_result(ctx, ok=False, summary="missing edit target")
        return _CMD_CONTINUE
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        _print_error(f"invalid edit arguments: {exc}")
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    if not parts:
        _print_error("Usage: /edit <path> [--content <text>] [--append <text>] [--replace OLD NEW]")
        _set_command_result(ctx, ok=False, summary="missing edit target")
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
            _print_error("Usage: /edit <path> [--content <text>] [--append <text>] [--replace OLD NEW]")
            _set_command_result(ctx, ok=False, summary="missing replace arguments")
            return _CMD_CONTINUE
        replace_values = rest[1:3]
    elif rest and not rest[0].startswith("--"):
        content = " ".join(rest)

    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    if not content and not replace_values:
        # Info mode: show file stats and a preview
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
                _set_command_result(ctx, ok=True, summary=f"previewed {resolved}")
            else:
                _print_error(f"file not found: {resolved}")
                _set_command_result(ctx, ok=False, summary=f"file not found: {resolved}")
        except Exception as exc:
            _print_error(f"error reading {path}: {exc}")
            _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    risk_level = infer_file_edit_risk(path)
    _print_risky_action_warning(
        action="/edit",
        target=path,
        risk_level=risk_level,
        recovery_hint="routed edits can use /rollback last; otherwise recover with your editor or VCS.",
    )
    approval_started = time.monotonic()
    approved = request_cli_approval(
        action="file.edit",
        target=path,
        risk_level=risk_level,
        detail=f"append={append_mode};replace={bool(replace_values)}",
        auto_approve=False,
        session_id=session.session_id,
        plan_id=session.plan_id,
        task_id=session.task_id,
    )
    approval_seconds = max(0.0, time.monotonic() - approval_started)
    append_event(
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
        _print_error("file edit not approved")
        _print_feedback("Approval denied.", level="warn", detail=f"after {_format_elapsed_compact(approval_seconds)}")
        _set_command_result(ctx, ok=False, summary="file edit not approved")
        return _CMD_CONTINUE
    resolved_path = str(Path(path).expanduser().resolve())
    if not _capture_routed_action_checkpoint(
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
    except Exception as exc:
        _print_error(str(exc))
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    edit_seconds = max(0.0, time.monotonic() - edit_started)
    append_event(
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
    _print_file_edit_result(result)
    _print_feedback(
        "Edit complete.",
        level="success" if result.changed else "info",
        detail=f"{result.summary} · {_format_elapsed_compact(edit_seconds)} write · approval {_format_elapsed_compact(approval_seconds)}",
    )
    _set_command_result(ctx, ok=True, summary=result.summary)
    return _CMD_CONTINUE


def _cmd_update(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/update — self-upgrade openclaw via pip without leaving the REPL."""
    import argparse as _argparse
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


def _cmd_version(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/version — show the running CLI version and build stamp."""
    ver = cli_version()
    server = ctx.config.base_url if ctx.config else "unknown"
    if _RICH_AVAILABLE and _IS_TTY:
        t = _RichText()
        t.append(f"{_e('🦞', '[openclaw]')} OpenClaw  ", style="bold cyan")
        t.append(ver, style="bold")
        t.append(f"\n  server  ", style="dim")
        t.append(server, style="cyan")
        _RICH_CONSOLE.print(_RichPanel(t, border_style="dim", padding=(0, 1)))
    else:
        print(f"\n  openclaw {ver}  ·  server: {server}\n")
    return _CMD_CONTINUE


def _print_theme_preview(theme_name: str, *, persisted: bool) -> None:
    """Print a compact theme preview without requiring Rich."""
    is_tty = _get_is_tty()
    normalized = _normalize_theme_name(theme_name)
    _, ansi_code = _THEMES[normalized]
    swatch = f"{ansi_code}{'━' * 8}{_R}" if is_tty else "--------"
    state = "saved" if persisted else "preview"
    print(
        f"  Theme {state}: {_B}{normalized}{_R} — "
        f"{_THEME_DESCRIPTIONS.get(normalized, 'accent theme')} {swatch}"
    )
    print(f"  {_theme_ansi()}{'─' * 14}{_R} {_status_emoji('healthy')} accent sample")
    print(f"  {_e('💡', '[tip]')} Try /theme next, /theme prev, or /emoji preview for quick comparisons.")


def _cycle_theme(direction: str) -> None:
    """Advance the stored theme forward or backward through the palette."""
    current = _normalize_theme_name(_PREFS.get("theme", "default"))
    index = _THEME_ORDER.index(current)
    if direction == "prev":
        next_theme = _THEME_ORDER[(index - 1) % len(_THEME_ORDER)]
    else:
        next_theme = _THEME_ORDER[(index + 1) % len(_THEME_ORDER)]
    _prefs_set("theme", next_theme)
    _print_theme_preview(next_theme, persisted=True)


def _cmd_theme(ctx: ChatCommandContext) -> str:
    """Handler for /theme — display or set the UI colour theme."""
    is_tty = _get_is_tty()
    token = ctx.args.strip().lower()

    if not token or token == "list":
        current = _normalize_theme_name(_PREFS.get("theme", "default"))
        print(f"\n  Available themes (current: {_B}{current}{_R}):\n")
        for name, (_rich_style, ansi_code) in _THEMES.items():
            marker = " ← current" if name == current else ""
            if is_tty:
                swatch = f"{ansi_code}{'━' * 6}{_R}"
            else:
                swatch = "------"
            desc = _THEME_DESCRIPTIONS.get(name, "")
            print(f"    {_B}{name:<10}{_R} {swatch} {desc}{_DM}{marker}{_R}")
        print("\n  Usage: /theme <name> | list | preview [name] | next | prev | reset\n")
        return _CMD_CONTINUE

    if token == "next":
        _cycle_theme("next")
        return _CMD_CONTINUE
    if token in {"prev", "previous"}:
        _cycle_theme("prev")
        return _CMD_CONTINUE
    if token == "reset":
        _prefs_set("theme", "default")
        _print_theme_preview("default", persisted=True)
        return _CMD_CONTINUE
    if token.startswith("preview"):
        parts = token.split()
        requested = parts[1] if len(parts) > 1 else _normalize_theme_name(_PREFS.get("theme", "default"))
        normalized = _normalize_theme_name(requested)
        if requested not in _THEMES and requested not in _THEME_ALIASES and normalized == "default":
            names = "  ".join(_THEME_ORDER)
            print(f"{_BRE}error:{_R} Unknown theme '{requested}'. Choose from: {names}")
            return _CMD_CONTINUE
        original_theme = _PREFS.get("theme", "default")
        _PREFS["theme"] = normalized
        _print_theme_preview(normalized, persisted=False)
        _PREFS["theme"] = original_theme
        return _CMD_CONTINUE

    normalized = _normalize_theme_name(token)
    if token not in _THEMES and token not in _THEME_ALIASES and normalized == "default":
        names = "  ".join(_THEME_ORDER)
        print(f"{_BRE}error:{_R} Unknown theme '{token}'. Choose from: {names}")
        return _CMD_CONTINUE

    _prefs_set("theme", normalized)
    _print_theme_preview(normalized, persisted=True)
    return _CMD_CONTINUE


def _cmd_overlay(ctx: ChatCommandContext) -> str:
    """/overlay [on|off|status] — manage opt-in interactive overlays."""
    token = (ctx.args or "").strip().lower()
    if not token or token == "status":
        state = "ON" if _interactive_overlays_enabled() else "OFF"
        availability = "available" if _overlay_available() else "unavailable"
        print(f"Interactive overlays: {state} ({availability} in this terminal)")
        print("Supported surfaces: /outputs, /sessions, and openclaw session list --interactive")
        return _CMD_CONTINUE
    if token not in {"on", "off"}:
        _print_error("Usage: /overlay [on|off|status]")
        return _CMD_CONTINUE
    enabled = token == "on"
    _prefs_set("interactive_overlays", enabled)
    if enabled:
        print("Interactive overlays enabled for supported list commands.")
    else:
        print("Interactive overlays disabled; list commands will stay non-interactive.")
    return _CMD_CONTINUE


def _cmd_colorscheme(ctx: ChatCommandContext) -> str:
    """/colorscheme [name|list|reset] — view or set the extended color scheme."""
    arg = (ctx.args or "").strip().lower()
    is_tty = _get_is_tty()

    if not arg or arg == "list":
        current = _PREFS.get("color_scheme", "default")
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n[bold cyan]🎨 Color Schemes[/]\n")
            for name, scheme in _EXTENDED_SCHEMES.items():
                active = " ← active" if name == current else ""
                primary = scheme.get("primary", "")
                reset = "\033[0m"
                label = scheme.get("label", name)
                _RICH_CONSOLE.print(f"  {primary}■{reset}  [bold]{name}[/]  [dim]{label}{active}[/]")
            _RICH_CONSOLE.print(f"\n  [dim]Use /colorscheme <name> to activate[/]\n")
        else:
            current_marker = lambda n: " ← active" if n == current else ""
            print(f"\n🎨 Color Schemes\n")
            for name, scheme in _EXTENDED_SCHEMES.items():
                p = scheme.get("primary", "")
                print(f"  {p}■\033[0m  {name}  {scheme.get('label', '')}{current_marker(name)}")
            print(f"\n  Use /colorscheme <name> to activate\n")
        return _CMD_CONTINUE

    if arg == "reset":
        arg = "default"

    if arg not in _EXTENDED_SCHEMES:
        names = ", ".join(_EXTENDED_SCHEMES.keys())
        msg = f"Unknown scheme '{arg}'. Available: {names}"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    _prefs_set("color_scheme", arg)
    scheme = _EXTENDED_SCHEMES[arg]
    label = scheme.get("label", arg)

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold green]✅ Color scheme set to[/] [bold]{arg}[/] [dim]{label}[/]\n")
    else:
        print(f"\n✅ Color scheme → {arg} {label}\n")

    return _CMD_CONTINUE


def _cmd_emojiheaders(ctx: ChatCommandContext) -> str:
    """/emojiheaders [on|off] — toggle emoji prefixes on AI response headings."""
    arg = ctx.args.strip().lower()
    if arg in ("on", "off"):
        _prefs_set("emoji_headers", (arg == "on"))
        state = "on" if _PREFS["emoji_headers"] else "off"
        is_tty = _get_is_tty()
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓[/] emoji headers [bold]{state}[/]")
        else:
            print(f"✓ emoji headers {state}")
    else:
        state = "on" if _PREFS.get("emoji_headers", True) else "off"
        is_tty = _get_is_tty()
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]emoji headers is [bold]{state}[/] — /emojiheaders on|off[/]")
        else:
            print(f"emoji headers is {state}")
    return _CMD_CONTINUE


def _cmd_emoji(ctx: ChatCommandContext) -> str:
    """Handler for /emoji — toggle emoji display on or off."""
    token = ctx.args.strip().lower()
    pack = _emoji_pack_name()
    if not token or token == "status":
        state = "on" if pack != "ascii" else "off"
        print(
            f"  Emoji is currently {_B}{state}{_R} "
            f"(pack: {_B}{pack}{_R}). Usage: /emoji on | off | pack <classic|minimal|ascii> | preview"
        )
        return _CMD_CONTINUE
    if token == "preview":
        print("  Emoji packs:")
        original_pack = _PREFS.get("emoji_pack", "classic")
        original_flag = _PREFS.get("emoji", True)
        for pack_name in ("classic", "minimal", "ascii"):
            _PREFS["emoji_pack"] = pack_name
            _PREFS["emoji"] = pack_name != "ascii"
            sample = " ".join(
                [
                    _e("💬", "[chat]"),
                    _status_emoji("healthy"),
                    _e("💡", "[tip]"),
                    _e("📍", "[pin]"),
                ]
            )
            marker = " ← current" if pack_name == pack else ""
            print(f"    {_B}{pack_name:<8}{_R} {sample}{marker}")
        _PREFS["emoji_pack"] = original_pack
        _PREFS["emoji"] = original_flag
        return _CMD_CONTINUE
    if token.startswith("pack "):
        requested = token.split(None, 1)[1].strip().lower()
        if requested not in _EMOJI_PACKS:
            print(f"{_BRE}error:{_R} Unknown emoji pack '{requested}'. Choose from: classic, minimal, ascii")
            return _CMD_CONTINUE
        _PREFS["emoji_pack"] = requested
        _prefs_set("emoji", requested != "ascii")
        print(f"  Emoji pack set to {_B}{requested}{_R}. Run /emoji preview to compare packs.")
        return _CMD_CONTINUE
    if token == "on":
        _PREFS["emoji"] = True
        if _emoji_pack_name() == "ascii":
            _PREFS["emoji_pack"] = "classic"
        _save_prefs()
        print(f"  Emoji enabled ✓ (pack: {_B}{_emoji_pack_name()}{_R})")
    elif token == "off":
        _PREFS["emoji"] = False
        _prefs_set("emoji_pack", "ascii")
        print("  Emoji disabled — ASCII fallbacks active.")
    else:
        print(f"{_BRE}error:{_R} Expected 'on', 'off', 'pack <name>', or 'preview', got '{token}'")
    return _CMD_CONTINUE


def _cmd_layout(ctx: ChatCommandContext) -> str:
    """Handler for /layout — switch density or render preset workspaces."""
    token = ctx.args.strip().lower()
    valid_layouts = ("compact", "normal", "verbose", "plain")
    preset_aliases = {
        "focus": "focus",
        "watch": "watch-monitor",
        "watch-monitor": "watch-monitor",
        "monitor": "watch-monitor",
        "handoff": "handoff",
        "collab": "handoff",
        "collaboration": "handoff",
    }
    if not token:
        current = _effective_layout_mode()
        preset = _layout_preset_name()
        print(f"  Layout is currently {_B}{current}{_R}.")
        if preset:
            config = _layout_preset_config(preset)
            fallback = _layout_preset_fallback()
            print(f"  Preset:           {_B}{config['label']}{_R} ({fallback})")
            print(f"  Active pane:      {_layout_focus_name()}")
            print(f"  Primary pane:     {config['primary']}")
            print(f"  Supporting pane:  {config['supporting']}")
            print("  Preview now with /layout show. Reset to single-pane with /layout reset.")
        else:
            print("  Preset:           single-pane default")
            print("  Usage: /layout compact | normal | verbose | plain")
            print("         /layout preset focus|watch-monitor|handoff")
            print("         /layout show | /layout focus primary|supporting | /layout reset")
        return _CMD_CONTINUE
    if token == "show":
        _print_layout_preset_workspace(ctx)
        return _CMD_CONTINUE
    if token.startswith("focus "):
        requested_focus = token.split(None, 1)[1].strip()
        if requested_focus not in {"primary", "supporting"}:
            _print_error("Usage: /layout focus primary|supporting")
            return _CMD_CONTINUE
        if not _layout_preset_name():
            _print_error("Choose a preset first: /layout preset focus|watch-monitor|handoff")
            return _CMD_CONTINUE
        _prefs_set("layout_focus", requested_focus)
        _print_feedback(f"Active pane set to {requested_focus}.", level="success")
        _print_layout_preset_workspace(ctx)
        return _CMD_CONTINUE
    preset_token = token.split(None, 1)[1].strip() if token.startswith("preset ") else token
    if preset_token in preset_aliases:
        preset = preset_aliases[preset_token]
        _PREFS["layout_preset"] = preset
        _prefs_set("layout_focus", "primary")
        config = _layout_preset_config(preset)
        fallback = _layout_preset_fallback()
        _print_feedback(
            f"Layout preset set to {config['label']}.",
            level="success",
            detail=f"primary {config['primary']} · supporting {config['supporting']} · fallback {fallback}",
        )
        _print_layout_preset_workspace(ctx)
        return _CMD_CONTINUE
    if token in {"reset", "off", "default", "single", "single-pane"}:
        _PREFS["layout_preset"] = ""
        _prefs_set("layout_focus", "primary")
        _print_feedback("Layout preset reset to single-pane default.", level="success")
        return _CMD_CONTINUE
    if token not in valid_layouts:
        print(
            f"{_BRE}error:{_R} Expected one of "
            "compact, normal, verbose, plain, preset <focus|watch-monitor|handoff>, "
            "show, focus <primary|supporting>, or reset, "
            f"got '{token}'"
        )
        return _CMD_CONTINUE
    _PREFS["layout"] = token
    _prefs_set(_A11Y_PLAIN_MODE, token == "plain")
    desc = {
        "compact": "reduced chrome; separator + status bar hidden",
        "normal": "default density",
        "verbose": "full density with extra context where available",
        "plain": "screen-reader/plain-text friendly mode",
    }[token]
    _print_feedback(f"Layout set to {token}.", level="success", detail=desc)
    return _CMD_CONTINUE


def _cmd_draft(ctx: ChatCommandContext) -> str:
    """Handler for /draft — save, load, clear, or restore a draft prompt."""
    global _draft_buffer, _last_interrupted_prompt, _multiline_mode

    parts = ctx.args.strip().split(None, 1)
    sub = parts[0].lower() if parts else ""

    if sub == "save":
        text = parts[1].strip() if len(parts) > 1 else ""
        if not text:
            print(f"  {_DM}Usage: /draft save <text to draft>{_R}")
            return _CMD_CONTINUE
        _draft_buffer = text
        print(f"  {_GR}Draft saved.{_R}")
        return _CMD_CONTINUE

    if sub == "load":
        if _draft_buffer:
            print(f"  {_CY}Current draft:{_R}\n  {_draft_buffer}")
        else:
            print(f"  {_DM}No draft saved. Use /draft save <text> to save one.{_R}")
        return _CMD_CONTINUE

    if sub == "clear":
        _draft_buffer = ""
        print(f"  {_GR}Draft cleared.{_R}")
        return _CMD_CONTINUE

    if sub == "restore":
        if _last_interrupted_prompt:
            print(f"  {_DM}Last interrupted prompt:{_R}  {_last_interrupted_prompt}")
            _draft_buffer = _last_interrupted_prompt
        else:
            print(f"  {_DM}No interrupted prompt to restore.{_R}")
        return _CMD_CONTINUE

    if sub == "multiline":
        rest = (parts[1].strip().lower() if len(parts) > 1 else "")
        if rest == "on":
            _multiline_mode = True
            print(f"  {_GR}Multiline mode: ON{_R} — type \\end on its own line to submit")
        elif rest == "off":
            _multiline_mode = False
            print(f"  Multiline mode: OFF")
        else:
            state = "ON" if _multiline_mode else "OFF"
            print(f"  Multiline mode is currently {_B}{state}{_R}. Usage: /draft multiline on | off")
        return _CMD_CONTINUE

    # No subcommand — show current draft or usage
    if _draft_buffer:
        print(f"  {_CY}Current draft:{_R}\n  {_draft_buffer}")
    else:
        print(f"  {_DM}No draft saved.{_R} Usage: /draft save <text> | load | clear | restore | multiline on|off")
    return _CMD_CONTINUE


def _cmd_template(ctx: ChatCommandContext) -> str:
    """Handler for /template — manage reusable prompt templates."""
    global _draft_buffer

    import re as _re

    parts = ctx.args.strip().split(None, 1)
    sub = parts[0].lower() if parts else ""

    templates: dict = _PREFS.setdefault("templates", {})

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
            _print_error(f"Template name '{name}' is invalid — use letters, digits, and hyphens only.")
            return _CMD_CONTINUE
        templates[name] = text
        _save_prefs()
        print(f"  {_GR}Template '{name}' saved.{_R}")
        return _CMD_CONTINUE

    if sub == "use":
        name = (parts[1].strip() if len(parts) > 1 else "")
        if not name:
            print(f"  {_DM}Usage: /template use <name>{_R}")
            return _CMD_CONTINUE
        text = templates.get(name)
        if text is None:
            _print_error(f"Template '{name}' not found. Use /template list to see available templates.")
            return _CMD_CONTINUE
        _draft_buffer = text
        print(f"  {_GR}Template '{name}' loaded into draft.{_R} Use /draft load to review or submit directly.")
        return _CMD_CONTINUE

    if sub == "delete":
        name = (parts[1].strip() if len(parts) > 1 else "")
        if not name:
            print(f"  {_DM}Usage: /template delete <name>{_R}")
            return _CMD_CONTINUE
        if name not in templates:
            _print_error(f"Template '{name}' not found. Use /template list to see available templates.")
            return _CMD_CONTINUE
        del templates[name]
        _save_prefs()
        print(f"  {_GR}Template '{name}' deleted.{_R}")
        return _CMD_CONTINUE

    _print_error(f"Unknown /template subcommand '{sub}'. Usage: list | use <name> | save <name> <text> | delete <name>")
    return _CMD_CONTINUE


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
    except Exception:
        return False


def _cmd_sessions(ctx: ChatCommandContext) -> str:
    """/sessions [search QUERY | related] — browse recent sessions."""
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
        print(f"\n  To resume that session, exit and run:")
        print(f"    {_BCY}openclaw session resume {target}{_R}\n")
        return _CMD_CONTINUE

    if token_lower == "related":
        # Find sessions with cwd or file overlap against the current session
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
        scored: list[tuple[int, "SessionSummary"]] = []
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
            badges = _session_badges(s)
            badge_str = f"  {_DM}{badges}{_R}" if badges else ""
            print(f"  {_CY}{short_id}{_R}  {title:<42} {_DM}{updated}{_R}{badge_str}")
        print(f"\n  Use /sessions open <id> to get resume instructions.\n")
        return _CMD_CONTINUE

    query = ""
    if token_lower.startswith("search "):
        query = token[7:].strip().lower()
    elif token and not token_lower.startswith("search") and not wants_overlay:
        # treat bare word as a search query shorthand
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

    if wants_overlay or (_interactive_overlays_enabled() and not token):
        overlay_result = _run_interactive_overlay(
            title="Session overlay",
            items=sessions,
            label_fn=lambda s: (
                f"{s.session_id[:8]}…  {s.title or '—'}  "
                f"{(s.updated_at or '—')[:19]}  {_session_badges(s)}".strip()
            ),
            on_select=lambda s: (
                _print_session_summary(s),
                _print_dashboard_surface(
                    "Focused Session Preview",
                    summary_lines=[
                        _progress_cell("session", s.session_id[:8] + "…", status=s.status or "active"),
                        _progress_cell("resume", "ready", status="info"),
                    ],
                    detail_lines=_session_preview_lines(s),
                    action_lines=[
                        f"openclaw --session {s.session_id}",
                        f"openclaw session share {s.session_id}",
                    ],
                    border_style="cyan",
                ),
                _print_meta_footer(("resume", f"openclaw --session {s.session_id}")),
            ),
            initial_query=overlay_query or query,
            empty_message="No sessions found.",
        )
        if overlay_result == "selected":
            _set_command_result(ctx, ok=True, summary="selected session from overlay")
            return _CMD_CONTINUE
        if wants_overlay and overlay_result == "closed":
            _set_command_result(ctx, ok=True, summary="session overlay closed")
            return _CMD_CONTINUE

    title_str = "Recent sessions" + (f" matching '{query}'" if query else "")
    fresh_count = sum(1 for s in sessions if not _session_is_stale(s))
    active_count = sum(1 for s in sessions if _status_family(s.status or "active") in {"active", "complete", "retry", "waiting"})
    operator_ready_count = 0
    for session in sessions:
        operator_snapshot = _session_operator_snapshot(session)
        if str(operator_snapshot.get("readiness_label") or "").strip() == "handoff-ready":
            operator_ready_count += 1
    _print_dashboard_surface(
        "Session Browser",
        summary_lines=[
            _progress_cell("shown", str(len(sessions)), status="active"),
            _progress_cell("fresh", str(fresh_count), status="info" if fresh_count else "idle"),
            _progress_cell("active-ish", str(active_count), status="active" if active_count else "idle"),
            _progress_cell("operator-ready", str(operator_ready_count), status="complete" if operator_ready_count else "idle"),
        ],
        detail_lines=[
            f"query: {query}" if query else "query: recent sessions",
            f"top session: {sessions[0].title or sessions[0].session_id}",
            *_session_preview_lines(sessions[0]),
        ],
        action_lines=[
            "/sessions open <id> to get resume instructions",
            "/sessions overlay to inspect one session without leaving the browser",
            "/session after resuming to inspect the focused dashboard",
        ],
        border_style="dim",
    )
    if _RICH_AVAILABLE and is_tty:
        tbl = _RichTable(title=title_str, show_header=True, header_style="bold", box=None, pad_edge=False)
        tbl.add_column("ID", style="cyan", no_wrap=True, min_width=10)
        tbl.add_column("Title", no_wrap=False, min_width=20, max_width=38)
        tbl.add_column("Cmds", justify="right", style="dim", min_width=4)
        tbl.add_column("Updated", style="dim", no_wrap=True)
        tbl.add_column("Badges", style="dim", no_wrap=True)
        for s in sessions:
            short_id = s.session_id[:8] + "…"
            title = (s.title[:36] + "…") if len(s.title) > 36 else s.title
            updated = s.updated_at[:10] if s.updated_at else "—"
            badges = _session_badges(s)
            tbl.add_row(short_id, title, str(s.command_count), updated, badges)
        _RICH_CONSOLE.print()
        _RICH_CONSOLE.print(tbl)
        _RICH_CONSOLE.print(f"\n  [dim]Use /sessions open <id> to get resume instructions.[/]\n")
    else:
        print(f"\n  {title_str}:\n")
        print(f"  {'ID':<10}  {'Title':<36}  {'Cmds':>4}  {'Updated':<10}  Badges")
        print(f"  {'─'*10}  {'─'*36}  {'─'*4}  {'─'*10}  ──────")
        for s in sessions:
            short_id = (s.session_id[:8] + "…")[:10]
            title = (s.title[:34] + "…") if len(s.title) > 34 else s.title
            updated = s.updated_at[:10] if s.updated_at else "—"
            badges = _session_badges(s) or "—"
            print(f"  {short_id:<10}  {title:<36}  {s.command_count:>4}  {updated:<10}  {badges}")
        print(f"\n  Use /sessions open <id> to get resume instructions.\n")
    return _CMD_CONTINUE


def _cmd_export(ctx: ChatCommandContext) -> str:
    """/export [md|json|txt] [filename] — export session history to a file."""
    import datetime as _dt
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
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    try:
        now_str = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if fmt == "md":
            lines = [f"# OpenClaw Session Export\n", f"**Exported:** {now_str}\n\n---\n"]
            for i, entry in enumerate(cmd_history, 1):
                if isinstance(entry, str):
                    lines.append(f"### [{i}] Prompt\n\n{entry}\n\n")
                elif isinstance(entry, dict):
                    prompt = entry.get("text", entry.get("prompt", entry.get("cmd", "")))
                    ts_str = entry.get("timestamp", entry.get("ts", ""))
                    ts_label = f" _{ts_str}_" if ts_str else ""
                    lines.append(f"### [{i}]{ts_label}\n\n{prompt}\n\n")
            content = "".join(lines)

        elif fmt == "json":
            import json as _json
            export_data = {
                "exported_at": _dt.datetime.now().isoformat(),
                "entry_count": len(cmd_history),
                "history": cmd_history,
            }
            content = _json.dumps(export_data, indent=2, default=str)

        else:  # txt
            lines = [f"OpenClaw Session Export — {now_str}\n", "=" * 60 + "\n\n"]
            for i, entry in enumerate(cmd_history, 1):
                if isinstance(entry, str):
                    lines.append(f"[{i}] {entry}\n\n")
                elif isinstance(entry, dict):
                    prompt = entry.get("text", entry.get("prompt", entry.get("cmd", "")))
                    lines.append(f"[{i}] {prompt}\n\n")
            content = "".join(lines)

        output_path = Path(filename).expanduser()
        output_path.write_text(content, encoding="utf-8")

        abs_path = str(output_path.resolve())
        count = len(cmd_history)
        size_kb = len(content.encode()) / 1024

        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n[bold green]✅ Exported[/] [dim]{count} entries → [/][bold cyan]{abs_path}[/] [dim]({size_kb:.1f} KB, {fmt.upper()})[/]\n")
        else:
            print(f"\n✅ Exported {count} entries → {abs_path} ({size_kb:.1f} KB, {fmt.upper()})\n")

    except Exception as e:
        msg = f"Export failed: {e}"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[red]{msg}[/]")
        else:
            print(msg)

    return _CMD_CONTINUE


def _cmd_stats(ctx: ChatCommandContext) -> str:
    """/stats — show aggregate usage statistics across all sessions."""
    is_tty = _get_is_tty()
    sessions = list_sessions(limit=500)

    if not sessions:
        print(f"  {_DM}No sessions found.{_R}")
        return _CMD_CONTINUE

    total_sessions = len(sessions)
    total_commands = sum(s.command_count for s in sessions)
    total_edits = sum(s.file_edit_count for s in sessions)
    total_checkpoints = sum(s.checkpoint_count for s in sessions)
    active = sum(1 for s in sessions if s.status == "active")
    newest = sessions[0].updated_at[:10] if sessions else "—"
    oldest = sessions[-1].created_at[:10] if sessions else "—"

    # Most-used cwd roots
    from collections import Counter
    cwd_counts: Counter[str] = Counter()
    for s in sessions:
        if s.cwd:
            cwd_counts[s.cwd] += 1
    top_cwds = cwd_counts.most_common(3)

    if _RICH_AVAILABLE and is_tty:
        from rich.table import Table as _RichTableLocal
        grid = _RichText()
        grid.append(f"  sessions    ", style="dim")
        grid.append(f"{total_sessions}", style="bold")
        grid.append(f"  ({active} active)\n", style="dim")
        grid.append(f"  commands    ", style="dim")
        grid.append(f"{total_commands}\n", style="bold")
        grid.append(f"  file edits  ", style="dim")
        grid.append(f"{total_edits}\n", style="bold")
        grid.append(f"  checkpoints ", style="dim")
        grid.append(f"{total_checkpoints}\n", style="bold")
        grid.append(f"  date range  ", style="dim")
        grid.append(f"{oldest}", style="bold")
        grid.append(f" → ", style="dim")
        grid.append(f"{newest}\n", style="bold")
        if top_cwds:
            grid.append(f"\n  top dirs\n", style="dim")
            for cwd, count in top_cwds:
                short = cwd[-45:] if len(cwd) > 45 else cwd
                if len(cwd) > 45:
                    short = "…" + short
                grid.append(f"    {count:>3}×  ", style="dim")
                grid.append(f"{short}\n", style="cyan")
        _RICH_CONSOLE.print(_RichPanel(grid, title=f"[bold]{_e('📊', '[stats]')} OpenClaw Stats[/]", border_style="dim", padding=(0, 1)))
    else:
        print(f"\n  {_e('📊', '[stats]')} OpenClaw Stats\n")
        print(f"  sessions    : {total_sessions}  ({active} active)")
        print(f"  commands    : {total_commands}")
        print(f"  file edits  : {total_edits}")
        print(f"  checkpoints : {total_checkpoints}")
        print(f"  date range  : {oldest} → {newest}")
        if top_cwds:
            print(f"\n  top dirs:")
            for cwd, count in top_cwds:
                short = ("…" + cwd[-45:]) if len(cwd) > 45 else cwd
                print(f"    {count:>3}×  {short}")
        print()
    return _CMD_CONTINUE


def _cmd_tag(ctx: ChatCommandContext) -> str:
    """/tag [add <tag>|rm <tag>|list] — manage tags on the current session."""
    session = _require_session_or_warn(ctx)
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


def _cmd_bookmark(ctx: ChatCommandContext) -> str:
    """/bookmark [label] — save a replay bookmark for the current session."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    label = " ".join(ctx.args.strip().split())
    bookmark = create_session_bookmark(session.session_id, label=label, history=ctx.history)
    detail = f"turn {bookmark.get('turn_index', 0)}"
    _print_feedback(
        f"Saved bookmark [{bookmark.get('id', '')}] {bookmark.get('label', '')}",
        level="success",
        detail=detail,
    )
    _set_command_result(ctx, ok=True, summary=f"bookmark {bookmark.get('id', '')} saved")
    return _CMD_CONTINUE


def _cmd_bookmarks(ctx: ChatCommandContext) -> str:
    """/bookmarks — list replay bookmarks for the current session."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    bookmarks = list_session_bookmarks(session.session_id)
    if not bookmarks:
        print(f"  {_DM}No bookmarks yet. Use /bookmark <label> after a meaningful turn.{_R}")
        _set_command_result(ctx, ok=True, summary="no bookmarks")
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
    _set_command_result(ctx, ok=True, summary=f"{len(bookmarks)} bookmarks")
    return _CMD_CONTINUE


def _cmd_resume(ctx: ChatCommandContext) -> str:
    """/resume [last] — print resume instructions for the most recent other session."""
    token = ctx.args.strip().lower()
    sessions = list_sessions(limit=20)
    # Exclude current session if active
    candidates = [s for s in sessions if s.session_id != ctx.session_id]
    if token and token != "last":
        # try to match by prefix
        candidates = [s for s in candidates if s.session_id.startswith(token) or token in s.title.lower()]
    if not candidates:
        print(f"  {_DM}No other sessions to resume.{_R}")
        return _CMD_CONTINUE
    target = candidates[0]
    short_id = target.session_id[:8]
    title = (target.title[:50] + "…") if len(target.title) > 50 else target.title
    updated = target.updated_at[:10] if target.updated_at else "—"
    print(f"\n  {_e('📍', '@')} Most recent session:")
    print(f"    {_B}{title}{_R}  {_DM}({short_id}…  updated {updated}){_R}")
    print(f"\n  To resume, exit and run:")
    print(f"    {_BCY}openclaw session resume {target.session_id}{_R}\n")
    return _CMD_CONTINUE


def _cmd_replay(ctx: ChatCommandContext) -> str:
    """/replay [session-id] [--from bookmark] — re-print the current or a past session's conversation."""
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
        # Try to find a session matching the token as id prefix or title substring
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
        session = _require_session_or_warn(ctx)
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

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold]{header}[/]\n")
        for role, msg in turns:
            if role == "user":
                _RICH_CONSOLE.print(f"[bold cyan]{_e('👤', 'You')}[/]\n{msg}\n")
            else:
                _print_response_separator()
                _RICH_CONSOLE.print(msg + "\n")
    else:
        print(f"\n  {header}\n")
        for role, msg in turns:
            if role == "user":
                print(f"\n{_BCY}{_e('👤', 'You')}{_R}\n{msg}\n")
            else:
                print()
                _print_response_separator()
                print(f"{msg}\n")
    return _CMD_CONTINUE


def _cmd_handoff(ctx: ChatCommandContext) -> str:
    """/handoff [create|list|open NAME|note TEXT] — save/restore a resumable workspace handoff."""
    is_tty = _get_is_tty()
    raw = ctx.args.strip()
    parts = raw.split(None, 1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    # ── create ──────────────────────────────────────────────────────────────
    if sub == "create":
        session_id = _require_session_or_warn(ctx)
        if session_id is None:
            return _CMD_CONTINUE
        if isinstance(session_id, object) and hasattr(session_id, "session_id"):
            session_id = session_id.session_id  # type: ignore[union-attr]
        # parse optional note: `/handoff create note "text"` or `/handoff create "text"`
        note = ""
        if rest.lower().startswith("note "):
            note = rest[5:].strip().strip('"').strip("'")
        elif rest:
            note = rest.strip('"').strip("'")
        try:
            handoff_id = create_handoff(session_id, note=note)
        except ValueError as exc:
            _print_error(str(exc))
            return _CMD_CONTINUE
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(
                f"\n[bold green]{_e('✅', '[OK]')} Handoff created:[/] [cyan]{handoff_id}[/]"
            )
            _RICH_CONSOLE.print(
                f"  Resume with: [dim]openclaw --session {session_id}[/]  "
                f"or  [dim]/handoff open {handoff_id}[/]\n"
            )
        else:
            print(f"\n{_GR}{_e('✅', '[OK]')} Handoff created:{_R} {handoff_id}")
            print(f"  Resume with: openclaw --session {session_id}")
            print(f"  Or use:      /handoff open {handoff_id}\n")
        return _CMD_CONTINUE

    # ── list ────────────────────────────────────────────────────────────────
    if sub == "list" or (not sub):
        handoffs = list_handoffs(limit=20)
        if not handoffs:
            print(f"  {_DM}No handoffs found. Create one with /handoff create{_R}")
            return _CMD_CONTINUE
        if _RICH_AVAILABLE and is_tty:
            tbl = _RichTable(
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
            _RICH_CONSOLE.print(tbl)
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
            _print_error("Usage: /handoff open NAME")
            return _CMD_CONTINUE
        manifest = load_handoff(rest)
        if manifest is None:
            _print_error(f"Handoff not found: {rest}")
            return _CMD_CONTINUE
        new_session = create_session()
        new_session_id = new_session.session_id if hasattr(new_session, "session_id") else str(new_session)
        result = apply_handoff(manifest, new_session_id)
        restored = result.get("restored", [])
        missing = result.get("missing", [])
        warnings = result.get("warnings", [])
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n[bold green]{_e('✅', '[OK]')} Handoff applied to new session:[/] [cyan]{new_session_id}[/]")
            if restored:
                _RICH_CONSOLE.print(f"  [green]Restored:[/] {', '.join(str(r) for r in restored)}")
            if missing:
                _RICH_CONSOLE.print(f"  [yellow]Missing:[/] {', '.join(str(m) for m in missing)}")
            for w in warnings:
                _RICH_CONSOLE.print(f"  [yellow]{_e('⚠️', 'Warning:')}[/] {w}")
            _RICH_CONSOLE.print(f"  Resume with: [dim]openclaw --session {new_session_id}[/]\n")
        else:
            print(f"\n{_GR}{_e('✅', '[OK]')} Handoff applied to new session:{_R} {new_session_id}")
            if restored:
                print(f"  Restored: {', '.join(str(r) for r in restored)}")
            if missing:
                print(f"  {_YE}Missing:{_R} {', '.join(str(m) for m in missing)}")
            for w in warnings:
                print(f"  {_YE}{_e('⚠️', 'Warning:')} {w}{_R}")
            print(f"  Resume with: openclaw --session {new_session_id}\n")
        return _CMD_CONTINUE

    # ── note ────────────────────────────────────────────────────────────────
    if sub == "note":
        session_id = _require_session_or_warn(ctx)
        if session_id is None:
            return _CMD_CONTINUE
        if isinstance(session_id, object) and hasattr(session_id, "session_id"):
            session_id = session_id.session_id  # type: ignore[union-attr]
        note_text = rest.strip('"').strip("'")
        if not note_text:
            _print_error("Usage: /handoff note TEXT")
            return _CMD_CONTINUE
        try:
            handoff_id = create_handoff(session_id, note=note_text)
        except ValueError as exc:
            _print_error(str(exc))
            return _CMD_CONTINUE
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(
                f"\n[bold green]{_e('✅', '[OK]')} Handoff with note saved:[/] [cyan]{handoff_id}[/]\n"
            )
        else:
            print(f"\n{_GR}{_e('✅', '[OK]')} Handoff with note saved:{_R} {handoff_id}\n")
        return _CMD_CONTINUE

    # ── unknown / usage ─────────────────────────────────────────────────────
    print(f"  {_CY}Usage:{_R} /handoff [create|list|open NAME|note TEXT]")
    return _CMD_CONTINUE


def _print_macro_progress(steps: list, current_idx: int, done_indices: set) -> None:
    """Print a live macro step progress tracker."""
    if _a11y_plain_mode():
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


def _workflow_store() -> dict[str, list[str]]:
    raw = _PREFS.setdefault("macros", {})
    if not isinstance(raw, dict):
        raw = {}
        _PREFS["macros"] = raw
    return raw


def _history_command_texts(limit: int) -> list[str]:
    items = list(_PREFS.get("cmd_history", []))
    commands: list[str] = []
    for entry in items:
        if isinstance(entry, dict):
            text = str(entry.get("text", entry.get("prompt", entry.get("cmd", ""))) or "").strip()
        else:
            text = str(entry or "").strip()
        if text:
            commands.append(text)
    return commands[-max(1, limit):]


def _render_workflow_step(command: str, ctx: ChatCommandContext) -> str:
    session = load_session(ctx.session_id) if ctx.session_id else None
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


def _print_workflow_preview(name: str, steps: list[str], ctx: ChatCommandContext) -> None:
    print(f"\n  {_B}Workflow preview '{name}'{_R}\n")
    for index, step in enumerate(steps, start=1):
        rendered = _render_workflow_step(step, ctx)
        print(f"  {_DM}{index:>2}{_R}  {_CY}{step}{_R}")
        if rendered != step:
            print(f"      {_DM}→ {rendered}{_R}")
    print(f"\n  {_DM}dry run only — use /workflow run {name} to execute.{_R}\n")


def _macro_run(ctx: ChatCommandContext, name: str, *, kind: str = "macro") -> str:
    """Execute a named macro/workflow's commands in sequence."""
    macros = _workflow_store()
    if name not in macros:
        _print_error(f"{kind.title()} '{name}' not found")
        return _CMD_CONTINUE
    commands = macros[name]
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


def _cmd_inject(ctx: "ChatCommandContext") -> str:
    """/inject — inject file or URL content as context prefix for the next message."""
    global _next_inject
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
        _next_inject = ""
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print("[green]✓[/] Injection cleared")
        else:
            print("✓ Injection cleared")
        return _CMD_CONTINUE

    if arg == "status":
        if _next_inject:
            preview = _next_inject[:100]
            suffix = "…" if len(_next_inject) > 100 else ""
            char_count = len(_next_inject)
            if _RICH_AVAILABLE and is_tty:
                from rich.panel import Panel as _RichPanel  # noqa: PLC0415
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
            _print_error("requests library not available — install with pip install requests")
            return _CMD_CONTINUE
        try:
            content = _requests.get(url, timeout=10).text
        except Exception as exc:  # noqa: BLE001
            _print_error(f"Failed to fetch URL: {exc}")
            return _CMD_CONTINUE
        _MAX = 8000
        truncated = False
        if len(content) > _MAX:
            content = content[:_MAX]
            truncated = True
        _next_inject = content
        preview = content[:60].replace("\n", " ")
        suffix = "…" if len(content) > 60 else ""
        trunc_note = f" [truncated at {_MAX} chars]" if truncated else ""
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(
                f"[green]✓[/] Loaded [bold]{len(content)}[/] chars from URL{trunc_note}\n"
                f"[dim]Preview: {preview}{suffix}[/]"
            )
        else:
            print(f"✓ Loaded {len(content)} chars from URL{trunc_note}\nPreview: {preview}{suffix}")
        return _CMD_CONTINUE

    # File path
    path = Path(arg).expanduser().resolve()
    if not path.exists():
        _print_error(f"File not found: {path}")
        return _CMD_CONTINUE
    if not path.is_file():
        _print_error(f"Not a file: {path}")
        return _CMD_CONTINUE
    try:
        raw = path.read_bytes()
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        _print_error("file appears to be binary")
        return _CMD_CONTINUE
    except OSError as exc:
        _print_error(f"Could not read file: {exc}")
        return _CMD_CONTINUE
    _MAX = 8000
    truncated = False
    if len(content) > _MAX:
        content = content[:_MAX]
        truncated = True
    _next_inject = content
    preview = content[:60].replace("\n", " ")
    suffix = "…" if len(content) > 60 else ""
    trunc_note = f" [truncated at {_MAX} chars]" if truncated else ""
    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(
            f"[green]✓[/] Loaded [bold]{len(content)}[/] chars from [cyan]{path.name}[/]{trunc_note}\n"
            f"[dim]Preview: {preview}{suffix}[/]"
        )
    else:
        print(f"✓ Loaded {len(content)} chars from {path.name}{trunc_note}\nPreview: {preview}{suffix}")
    return _CMD_CONTINUE


_SYSTEM_PROMPT_MAX = 2000


def _cmd_system(ctx: ChatCommandContext) -> str:
    """View or set a persistent system prompt prefix for all AI messages."""
    is_tty = _get_is_tty()
    args = ctx.args.strip()
    parts = args.split(None, 1)
    sub = parts[0].lower() if parts else "view"
    rest = parts[1] if len(parts) > 1 else ""

    if sub in ("view", "") or not args:
        current = _PREFS.get("system_prompt", "").strip()
        if _RICH_AVAILABLE and is_tty:
            if current:
                _RICH_CONSOLE.print(_RichPanel(current, title="🔧 System Prompt", border_style="cyan", padding=(0, 1)))
            else:
                _RICH_CONSOLE.print(_RichPanel(f"[dim](not set)[/]", title="🔧 System Prompt", border_style="dim", padding=(0, 1)))
        else:
            if current:
                print(f"System prompt:\n  {current}")
            else:
                print(f"System prompt: (not set)")
        return _CMD_CONTINUE

    if sub == "clear":
        _prefs_set("system_prompt", "")
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print("[green]✓ System prompt cleared.[/]")
        else:
            print("✓ System prompt cleared.")
        return _CMD_CONTINUE

    if sub == "set":
        if not rest.strip():
            _print_error("Usage: /system set <text>")
            return _CMD_CONTINUE
        if len(rest) > _SYSTEM_PROMPT_MAX:
            _print_error("System prompt too long (max 2000 chars)")
            return _CMD_CONTINUE
        _prefs_set("system_prompt", rest)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓ System prompt set ({len(rest)} chars).[/]")
        else:
            print(f"✓ System prompt set ({len(rest)} chars).")
        return _CMD_CONTINUE

    if sub == "append":
        if not rest.strip():
            _print_error("Usage: /system append <text>")
            return _CMD_CONTINUE
        current = _PREFS.get("system_prompt", "")
        new_prompt = (current + "\n" + rest).strip() if current.strip() else rest
        if len(new_prompt) > _SYSTEM_PROMPT_MAX:
            _print_error("System prompt too long (max 2000 chars)")
            return _CMD_CONTINUE
        _prefs_set("system_prompt", new_prompt)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓ System prompt updated ({len(new_prompt)} chars).[/]")
        else:
            print(f"✓ System prompt updated ({len(new_prompt)} chars).")
        return _CMD_CONTINUE

    _print_error(f"Unknown sub-command '{sub}'. Use: view, set <text>, append <text>, clear")
    return _CMD_CONTINUE


def _cmd_promptdebug(ctx: ChatCommandContext) -> str:
    """/promptdebug — preview what would be sent to the AI for the next message."""
    is_tty = _get_is_tty()
    sys_prompt = _PREFS.get("system_prompt", "").strip()
    inj = globals().get("_next_inject", "").strip()

    parts = []
    if sys_prompt:
        parts.append(f"[System context]\n{sys_prompt}")
    if inj:
        parts.append(f"[Injected context]\n{inj}")
    parts.append("[User message]\n(your next message here)")

    preview = "\n\n".join(parts)

    if _RICH_AVAILABLE and is_tty:
        from rich.syntax import Syntax as _RichSyntax
        _RICH_CONSOLE.print(_RichPanel(preview, title="[bold]📤 Next message preview[/]", border_style="dim", padding=(0, 1)))
    else:
        print("\n📤 Next message preview:\n")
        print(preview)
    return _CMD_CONTINUE


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
    return _handle_simple_toggle_pref(ctx, "auto_bold", "auto-bold")


def _cmd_jsonformat(ctx: ChatCommandContext) -> str:
    """/jsonformat [on|off] — toggle automatic JSON detection and pretty-printing in responses."""
    return _handle_simple_toggle_pref(ctx, "json_autoformat", "JSON auto-format")


def _cmd_separator(ctx: ChatCommandContext) -> str:
    """/separator [style] — set or preview response separator style (gradient|pulse|dots|wave|none)."""
    arg = ctx.args.strip().lower()
    valid = list(_SEPARATOR_STYLES.keys())
    is_tty = _get_is_tty()

    if arg in valid:
        _prefs_set("separator_style", arg)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓[/] separator style: [bold]{arg}[/]")
        else:
            print(f"✓ separator style: {arg}")
        if arg != "none":
            _print_animated_separator()
    elif arg:
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]Unknown style '{arg}'[/] — valid: {', '.join(valid)}")
        else:
            print(f"Unknown style '{arg}' — valid: {', '.join(valid)}")
    else:
        current = _PREFS.get("separator_style", "gradient")
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]separator style: [bold]{current}[/] — /separator gradient|pulse|dots|wave|none[/]")
        else:
            print(f"separator style: {current} — /separator gradient|pulse|dots|wave|none")
    return _CMD_CONTINUE


def _cmd_links(ctx: "ChatCommandContext") -> str:
    """/links [on|off] — toggle clickable OSC 8 hyperlinks in responses (requires modern terminal)."""
    return _handle_simple_toggle_pref(ctx, "clickable_links", "clickable links")


_CMD_REGISTRY_CACHE: "dict | None" = None


def _get_cmd_registry() -> "ChatCommandRegistry":
    """Return the cached command registry, building it once on first call."""
    global _CMD_REGISTRY_CACHE
    if _CMD_REGISTRY_CACHE is None:
        _CMD_REGISTRY_CACHE = build_chat_command_registry()
    return _CMD_REGISTRY_CACHE


def _cmd_palette(ctx: "ChatCommandContext") -> str:
    """/palette [query] — search slash commands by keyword (fuzzy)."""
    query = ctx.args.strip().lower()
    is_tty = _get_is_tty()

    commands = list(_get_cmd_registry().list_commands())

    if query:
        matches = [
            cmd for cmd in commands
            if query in cmd.name.lower() or
               (cmd.description and query in cmd.description.lower())
        ]
    else:
        matches = commands

    if not matches:
        msg = f"No commands matching '{query}'"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    matches.sort(key=lambda c: c.name)

    if _RICH_AVAILABLE and is_tty:
        from rich.table import Table
        from rich.box import SIMPLE
        tbl = Table(box=SIMPLE, show_header=True, header_style="bold cyan")
        tbl.add_column("Command", style="bold green", no_wrap=True)
        tbl.add_column("Description", style="default")
        for cmd in matches:
            tbl.add_row(f"/{cmd.name}", cmd.description or "")
        _RICH_CONSOLE.print(
            f"\n[bold cyan]🎯 Command Palette[/] "
            f"[dim]({len(matches)} match{'es' if len(matches) != 1 else ''})[/]\n"
        )
        _RICH_CONSOLE.print(tbl)
    else:
        print(f"\n🎯 Command Palette ({len(matches)} matches)")
        print(f"{'Command':<22} Description")
        print("─" * 60)
        for cmd in matches:
            print(f"  /{cmd.name:<20} {cmd.description or ''}")

    return _CMD_CONTINUE


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
        ("/outputs [promote <i> <name>]",  "List, preview, promote, or overlay-pick saved session outputs"),
        ("/overlay [on|off|status]",       "Toggle opt-in interactive pickers for supported list commands"),
        ("/rollback [last|list|<name>]",   "List git snapshots, preview/exec rollback, or restore checkpoint"),
        ("/snapshot [name]",               "Save current git HEAD as a named restore point"),
        ("/events [n|decisions]",              "Show last n session events, or decision-only view"),
        ("/why",                               "Explain the last routing/tool decision (confidence, rationale, grounding)"),
        ("/collab [status|share]",             "Show an actor-oriented handoff summary for the current session"),
        ("/runbook [template] [save <path>]",  "Render a long-form runbook for the active session"),
        ("/exporttemplates [list|show <name>]", "Inspect built-in runbook/export templates"),
        ("/collab note [@actor] TEXT",         "Record a collaboration note in the local session audit trail"),
        ("/collab decision [@actor] [#tag] TEXT", "Record a tagged decision for later handoff/export"),
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
        ("/rate [good|ok|bad|meh|1-5]",         "Rate the last AI response and store feedback"),
        ("/quality",  "Show response quality stats — avg score, distribution, recent ratings"),
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
    """Print a compact dim status line below the response.

    Shows session, context size, and autoroute state so the user always has
    situational awareness without cluttering the response output itself.
    """
    is_tty = _get_is_tty()
    if not is_tty:
        return
    cols = _terminal_width()
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
        if _RICH_AVAILABLE and is_tty:
            style = "bold white" if _a11y_high_contrast() else "dim"
            _RICH_CONSOLE.print(f"[{style}]  {line}[/]")
        else:
            style = _theme_ansi() if _a11y_high_contrast() else _DM
            reset = _R if style else ""
            print(f"  {style}{line}{reset}")


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
    arg = ctx.args.strip()
    is_tty = _get_is_tty()

    if not arg:
        current = _PREFS.get("prompt_format", _DEFAULT_PROMPT_FORMAT)
        preview = _render_prompt_format(current)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n[bold cyan]Current prompt format:[/]")
            _RICH_CONSOLE.print(f"  Format:  [dim]{current}[/]")
            _RICH_CONSOLE.print(f"  Preview: [bold]{preview}[/]")
            _RICH_CONSOLE.print(f"\n[dim]Tokens: {{route}} {{session}} {{model}} {{build}} {{time}}[/]")
            _RICH_CONSOLE.print(f"[dim]Use /prompt reset to restore default[/]\n")
        else:
            print(f"\nCurrent: {current}")
            print(f"Preview: {preview}")
            print(f"Tokens: {{route}} {{session}} {{model}} {{build}} {{time}}")
        return _CMD_CONTINUE

    if arg == "reset":
        _prefs_set("prompt_format", _DEFAULT_PROMPT_FORMAT)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓[/] prompt format reset to default")
        else:
            print("✓ prompt format reset")
        return _CMD_CONTINUE

    if len(arg) < 2:
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]Prompt format too short[/]")
        else:
            print("Prompt format too short")
        return _CMD_CONTINUE

    _prefs_set("prompt_format", arg)
    preview = _render_prompt_format(arg)
    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"[green]✓[/] prompt format updated")
        _RICH_CONSOLE.print(f"  Preview: [bold]{preview}[/]")
    else:
        print(f"✓ prompt format: {preview}")
    return _CMD_CONTINUE


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


def _print_startup_banner(config: CliConfig, session_id: str) -> None:
    """Print a colored startup banner for the interactive REPL."""
    autoroute_on = _session_auto_route_enabled(session_id)
    ver = cli_version()
    cols = _terminal_width()

    # Plain-mode path: no ANSI, no emoji, no decorative borders.
    if _a11y_plain_mode() or cols < 40:
        autoroute_str = "on" if autoroute_on else "off"
        print(f"🦞 OpenClaw {ver}")
        print(f"Server: {config.base_url}")
        print(f"User: {config.user_name}")
        if session_id:
            print(f"Session: {session_id[:8]}…")
        print("Type /help for commands. /quit to exit.")
        print(f"Auto-routing: {autoroute_str}")
        return

    if _RICH_AVAILABLE and _IS_TTY:
        t = _RichText()
        t.append(f"{_e('🦞', '[openclaw]')} OpenClaw", style="bold cyan")
        t.append(f"  {ver}", style="cyan dim")
        t.append("  connected to ", style="dim")
        t.append(config.base_url, style="cyan")
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
            f"\n  {_DM}connected to{_R}  {_CY}{config.base_url}{_R}"
            f"\n  {_DM}{_e('👤', '[user]')} user:{_R}      {_BGR}{config.user_name}{_R}"
            f"{session_line}"
            f"\n"
            f"\n  Type anything to chat · {_BCY}/help{_R} for commands · {_BCY}/quit{_R} to exit · {_B}Tab{_R}{_DM} completes /commands{_R}"
            f"{autoroute_line}\n"
        )


def _cmd_pasteguard(ctx: "ChatCommandContext") -> str:
    """Toggle or inspect the paste guard setting."""
    token = (ctx.args or "").strip().lower()
    if token == "on":
        _prefs_set("paste_guard", True)
        print(f"  {_GR}{_e('✅', '[OK]')} Paste guard enabled.{_R}")
    elif token == "off":
        _prefs_set("paste_guard", False)
        print(f"  {_YE}{_e('⚠️', '[warn]')} Paste guard disabled.{_R}")
    else:
        state = "on" if _PREFS.get("paste_guard", True) else "off"
        print(f"  Paste guard is currently {_B}{state}{_R}. Use /pasteguard on|off to change.")
    return _CMD_CONTINUE


_BUILTIN_COMMAND_NAMES: "frozenset[str]" = frozenset({
    # Core
    "help", "clear", "quit", "exit", "update", "version", "v",
    # Session & context
    "session", "context", "cwd", "files", "plan", "watch", "task",
    "sessions", "tag", "resume", "replay", "handoff", "collab",
    # Outputs & edits
    "outputs", "rollback", "events", "why", "trace", "runbook", "exporttemplates", "edit", "exec", "write",
    "changes", "diff", "snapshot",
    # Routing & analysis
    "autoroute", "analyze", "research",
    # Display & UI
    "theme", "emoji", "layout", "colorscheme", "separator", "links",
    "autobold", "jsonformat", "emojiheaders", "pathhints", "ratehint",
    "promptdebug", "quality", "tip", "shortcuts",
    "palette", "overlay", "bindlist", "keybind", "keys",
    # Dashboard & benchmarks
    "dashboard", "benchmark", "timeline",
    # History & search
    "history", "recall", "histsearch", "freq", "heatmap", "top", "streak",
    # Persistence
    "export", "stats",
    # Pinning & notes
    "pin", "pins", "search",
    # Aliases, macros, templates
    "alias", "macro", "macrostatus", "workflow", "template", "draft",
    # Accessibility
    "accessibility", "a11y",
    # Misc / fun
    "rate", "ratehint", "celebrate", "inject", "system", "prompt",
    "pasteguard", "followup",
})

_MAX_ALIASES = 50


def _cmd_alias(ctx: "ChatCommandContext") -> str:
    """Define, list, or remove command aliases."""
    args = (ctx.args or "").strip()
    aliases: "dict[str, str]" = _PREFS.setdefault("aliases", {})
    is_tty = _get_is_tty()

    if not args:
        # List all aliases
        if _RICH_AVAILABLE and is_tty:
            grid = _RichTable.grid(padding=(0, 2))
            grid.add_column(style="cyan", no_wrap=True)
            grid.add_column(style="dim")
            if aliases:
                for name, expansion in sorted(aliases.items()):
                    grid.add_row(name, expansion)
            else:
                grid.add_row("(no aliases defined)", "")
            _RICH_CONSOLE.print(_RichPanel(grid, title="Aliases", border_style="cyan", padding=(0, 1)))
        else:
            print("Aliases:")
            if aliases:
                for name, expansion in sorted(aliases.items()):
                    print(f"  {_CY}{name}{_R} → {_DM}{expansion}{_R}")
            else:
                print(f"  {_DM}(no aliases defined){_R}")
        return _CMD_CONTINUE

    parts = args.split(None, 1)
    sub = parts[0].lower()

    if sub == "rm":
        # Remove alias
        target = parts[1].strip().lstrip("/").lower() if len(parts) > 1 else ""
        if not target:
            _print_error("Usage: /alias rm <name>")
            return _CMD_CONTINUE
        if target not in aliases:
            _print_error(f"Alias '{target}' not found.")
            return _CMD_CONTINUE
        del aliases[target]
        _save_prefs()
        print(f"  {_GR}{_e('✅', '[OK]')} Alias '{target}' removed.{_R}")
        return _CMD_CONTINUE

    # Define alias: /alias <name> <expansion>
    name = sub.lstrip("/")
    expansion = parts[1].strip() if len(parts) > 1 else ""

    if not expansion:
        _print_error("Usage: /alias <name> <expansion>")
        return _CMD_CONTINUE
    if name in ("alias", "rm"):
        _print_error(f"'{name}' is reserved and cannot be used as an alias name.")
        return _CMD_CONTINUE
    if name in _BUILTIN_COMMAND_NAMES:
        _print_error(f"'{name}' is a built-in command name and cannot be used as an alias.")
        return _CMD_CONTINUE
    if len(aliases) >= _MAX_ALIASES and name not in aliases:
        _print_error(f"Maximum of {_MAX_ALIASES} aliases reached. Remove one first with /alias rm <name>.")
        return _CMD_CONTINUE

    aliases[name] = expansion
    _save_prefs()
    print(f"  {_GR}{_e('✅', '[OK]')} Alias '{_CY}{name}{_R}{_GR}' → {_DM}{expansion}{_R}{_GR} defined.{_R}")
    return _CMD_CONTINUE


def _cmd_macro(ctx: "ChatCommandContext") -> str:
    """Manage named command macros. Sub-commands: list, save, show, rm, run."""
    import re as _re

    args = (ctx.args or "").strip()
    macros = _workflow_store()
    is_tty = _get_is_tty()

    parts = args.split(None, 1)
    token = parts[0].lower() if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""

    # ── list ──────────────────────────────────────────────────────────────────
    if token in ("list", "ls") or not args:
        if _RICH_AVAILABLE and is_tty:
            grid = _RichTable.grid(padding=(0, 2))
            grid.add_column(style="cyan", no_wrap=True)
            grid.add_column(style="dim")
            if macros:
                for name, cmds in sorted(macros.items()):
                    grid.add_row(name, f"{len(cmds)} command{'s' if len(cmds) != 1 else ''}")
            else:
                grid.add_row(f"{_e('🔧', '')} (no macros defined)", "")
            _RICH_CONSOLE.print(_RichPanel(
                grid,
                title=f"{_e('🔧', '')} Macros",
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
        # Parse: save <name> [last <N>]
        save_parts = rest.split()
        if not save_parts:
            _print_error("Usage: /macro save <name> [last N]")
            return _CMD_CONTINUE

        macro_name = save_parts[0]
        # Validate name: alphanumeric + hyphens + underscores, max 40 chars
        if not _re.match(r'^[A-Za-z0-9_-]{1,40}$', macro_name):
            _print_error(
                "Macro name must be 1-40 alphanumeric characters, hyphens, or underscores."
            )
            return _CMD_CONTINUE

        # Parse optional "last N"
        n = 5
        if len(save_parts) >= 3 and save_parts[1].lower() == "last":
            try:
                n = max(1, min(int(save_parts[2]), 20))
            except ValueError:
                _print_error("Usage: /macro save <name> [last N]")
                return _CMD_CONTINUE
        elif len(save_parts) == 2:
            _print_error("Usage: /macro save <name> [last N]")
            return _CMD_CONTINUE

        hist = _history_command_texts(20)
        if not hist:
            _print_error("No command history to save — run some commands first")
            return _CMD_CONTINUE

        if len(macros) >= 30 and macro_name not in macros:
            _print_error("Maximum of 30 macros reached. Remove one first with /macro rm <name>.")
            return _CMD_CONTINUE

        commands = hist[-n:]
        commands = commands[:20]  # cap at 20 commands per macro
        updated = macro_name in macros
        macros[macro_name] = commands
        _save_prefs()

        suffix = f"  {_GR}(updated){_R}" if updated else ""
        print(
            f"  {_GR}{_e('✅', '[OK]')} Macro '{_CY}{macro_name}{_R}{_GR}' saved"
            f" ({len(commands)} command{'s' if len(commands) != 1 else ''}){_R}{suffix}"
        )
        return _CMD_CONTINUE

    # ── show ──────────────────────────────────────────────────────────────────
    if token == "show":
        if not rest:
            _print_error("Usage: /macro show <name>")
            return _CMD_CONTINUE
        name = rest.split()[0]
        if name not in macros:
            _print_error(f"Macro '{name}' not found")
            return _CMD_CONTINUE
        cmds = macros[name]
        if _RICH_AVAILABLE and is_tty:
            from rich.text import Text as _RichText
            from rich.console import Group as _RichGroup
            lines = []
            for i, cmd in enumerate(cmds, start=1):
                line = _RichText()
                line.append(f"  {i:>2}  ", style="dim")
                line.append(cmd, style="bold cyan")
                lines.append(line)
            _RICH_CONSOLE.print(_RichPanel(
                _RichGroup(*lines),
                title=f"{_e('🔧', '')} Macro: {name}",
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
            _print_error("Usage: /macro rm <name>")
            return _CMD_CONTINUE
        name = rest.split()[0]
        if name not in macros:
            _print_error(f"Macro '{name}' not found")
            return _CMD_CONTINUE
        del macros[name]
        _save_prefs()
        print(f"  {_GR}{_e('✅', '[OK]')} Macro '{name}' removed{_R}")
        return _CMD_CONTINUE

    # ── run ───────────────────────────────────────────────────────────────────
    if token == "run":
        if not rest:
            _print_error("Usage: /macro run <name>")
            return _CMD_CONTINUE
        return _macro_run(ctx, rest.split()[0])

    _print_error(f"Unknown /macro sub-command '{token}'. Use: list, save, show, rm, run")
    return _CMD_CONTINUE


def _cmd_macrostatus(ctx: "ChatCommandContext") -> str:  # noqa: ARG001
    """/macrostatus — show saved macros with step counts."""
    macros = _PREFS.get("macros", {})
    is_tty = _get_is_tty()
    if not macros:
        msg = "No macros saved. Use /macro save <name> to create one."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    if _RICH_AVAILABLE and is_tty:
        from rich.table import Table as _RichTableLocal
        from rich.box import SIMPLE as _RICH_BOX_SIMPLE
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
        _RICH_CONSOLE.print("\n[bold cyan]📋 Saved Macros[/]\n")
        _RICH_CONSOLE.print(tbl)
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


def _cmd_workflow(ctx: "ChatCommandContext") -> str:
    """/workflow — manage previewable workflows backed by the macro store."""
    args = (ctx.args or "").strip()
    workflows = _workflow_store()
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
            _print_error("Usage: /workflow preview <name>")
            return _CMD_CONTINUE
        name = rest.split()[0]
        if name not in workflows:
            _print_error(f"Workflow '{name}' not found")
            return _CMD_CONTINUE
        _print_workflow_preview(name, list(workflows[name]), ctx)
        return _CMD_CONTINUE

    if token == "run":
        if not rest:
            _print_error("Usage: /workflow run <name>")
            return _CMD_CONTINUE
        return _macro_run(ctx, rest.split()[0], kind="workflow")

    _print_error("Unknown /workflow sub-command. Use: list, save, show, preview, run, rm")
    return _CMD_CONTINUE


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
    except Exception:
        return ""


def _cmd_history(ctx: "ChatCommandContext") -> str:
    """Show or clear recent command history with color-coding and pagination."""
    args = (ctx.args or "").strip()
    is_tty = _get_is_tty()
    hist: "list" = _PREFS.get("cmd_history", [])

    if args.lower() == "clear":
        _prefs_set("cmd_history", [])
        print(f"  {_GR}{_e('✅', '[OK]')} Command history cleared.{_R}")
        return _CMD_CONTINUE

    # Parse optional page argument
    PAGE_SIZE = 15
    page = 1
    if args:
        try:
            page = max(1, int(args))
        except ValueError:
            _print_error(f"Usage: /history [page] | /history clear")
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
        from rich.text import Text as _RichText
        from rich.console import Group as _RichGroup
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
        _RICH_CONSOLE.print(_RichPanel(
            _RichGroup(*content_lines),
            title=" ".join(title_parts),
            border_style="cyan",
            padding=(0, 1),
        ))
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


def _cmd_recall(ctx: "ChatCommandContext") -> str:
    """/recall <n> — re-inject the nth most recent prompt into the chat (1=most recent)."""
    arg = (ctx.args or "").strip()
    is_tty = _get_is_tty()

    cmd_history = _PREFS.get("cmd_history", [])
    prompts: list[str] = []
    for entry in reversed(cmd_history):
        if isinstance(entry, dict):
            text = entry.get("text", entry.get("prompt", entry.get("cmd", "")))
        else:
            text = str(entry)
        if text and not text.startswith("/"):
            prompts.append(text)

    if not arg or not arg.isdigit():
        if not prompts:
            msg = "No prompt history yet."
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[dim]{msg}[/]")
            else:
                print(msg)
            return _CMD_CONTINUE

        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print("\n[bold cyan]📜 Recent Prompts[/]")
            for i, p in enumerate(prompts[:10], 1):
                preview = p[:70] + "…" if len(p) > 70 else p
                _RICH_CONSOLE.print(f"  [dim]{i:>2}.[/] [default]{preview}[/]")
            _RICH_CONSOLE.print(f"\n[dim]Use /recall <n> to re-send prompt #n[/]\n")
        else:
            print("\n📜 Recent Prompts")
            for i, p in enumerate(prompts[:10], 1):
                preview = p[:70] + "…" if len(p) > 70 else p
                print(f"  {i:>2}. {preview}")
            print("\n  Use /recall <n> to re-send prompt #n\n")
        return _CMD_CONTINUE

    n = int(arg)
    if n < 1 or n > len(prompts):
        msg = f"No prompt #{n} — history has {len(prompts)} entries."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    recalled = prompts[n - 1]
    global _next_inject
    _next_inject = recalled

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"[dim]↩  Recalling:[/] [italic]{recalled[:80]}[/]")
    else:
        print(f"  ↩  Recalling: {recalled[:80]}")

    return _CMD_CONTINUE


def _cmd_histsearch(ctx: "ChatCommandContext") -> str:
    """/histsearch <query> — search prompt history for matching entries."""
    query = ctx.args.strip().lower()
    is_tty = _get_is_tty()

    if not query:
        msg = "Usage: /histsearch <query>"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    cmd_history = _PREFS.get("cmd_history", [])

    matches = []
    for i, entry in enumerate(reversed(cmd_history)):
        if isinstance(entry, dict):
            text = entry.get("text", entry.get("prompt", entry.get("cmd", "")))
            ts = entry.get("timestamp", entry.get("ts", ""))
        else:
            text = str(entry)
            ts = ""

        if query in text.lower():
            matches.append((len(cmd_history) - i, text, ts))

    if not matches:
        msg = f"No history matches for '{query}'"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]🔍 History Search:[/] [italic]\"{query}\"[/] [dim]({len(matches)} match{'es' if len(matches)!=1 else ''})[/]\n")
        for idx, text, ts in matches[:20]:
            preview = text[:80] + "…" if len(text) > 80 else text
            highlighted = preview.replace(query, f"[bold yellow]{query}[/]")
            rel = ""
            if ts:
                try:
                    import datetime
                    dt = datetime.datetime.fromisoformat(ts)
                    diff = int((datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - dt).total_seconds())
                    rel = f"[dim] ({diff//3600}h ago)[/]" if diff >= 3600 else f"[dim] ({diff//60}m ago)[/]"
                except Exception:
                    pass
            _RICH_CONSOLE.print(f"  [dim]#{idx:<4}[/] {highlighted}{rel}")
        _RICH_CONSOLE.print()
    else:
        print(f"\n🔍 History: \"{query}\" ({len(matches)} matches)\n")
        for idx, text, ts in matches[:20]:
            preview = text[:75] + "…" if len(text) > 75 else text
            highlighted = preview.replace(query, query.upper())
            print(f"  #{idx:<4} {highlighted}")
        print()

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
            # "list" explicitly, OR bare "/pin" with no _last_response_text check needed
            if sub in ("list", "ls"):
                if not pins:
                    if _RICH_AVAILABLE and is_tty:
                        _RICH_CONSOLE.print(_RichPanel(f"[dim](no pins)[/dim]", title="📌 Pins", border_style="cyan", padding=(0, 1)))
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
            _print_error("Usage: /pin recall <name>")
            return _CMD_CONTINUE
        name_lc = rest.lower()
        match = next((p for p in pins if p["name"].lower() == name_lc), None)
        if match is None:
            _print_error(f"No pin named '{rest}'")
            return _CMD_CONTINUE
        # Re-render using a minimal AskResponse-like object
        from dataclasses import dataclass as _dc, field as _field

        @_dc
        class _PinResponse:
            response: str
            raw: dict = _field(default_factory=dict)
            metadata: dict = _field(default_factory=dict)
            error: str = ""

        print_response(_PinResponse(response=match["text"]), output_json=False)
        return _CMD_CONTINUE

    # ── rm ────────────────────────────────────────────────────────────────────
    if sub == "rm":
        if not rest:
            _print_error("Usage: /pin rm <name>")
            return _CMD_CONTINUE
        name_lc = rest.lower()
        before = len(pins)
        pins[:] = [p for p in pins if p["name"].lower() != name_lc]
        if len(pins) == before:
            _print_error(f"No pin named '{rest}'")
            return _CMD_CONTINUE
        _prefs_set("pins", pins)
        print(f"  {_GR}{_e('✅', '[OK]')} Pin '{rest}' removed.{_R}")
        return _CMD_CONTINUE

    # ── pin (save) — bare /pin or /pin <name> ────────────────────────────────
    # At this point sub is either "" (no args) or a custom name
    global _last_response_text
    if not _last_response_text:
        _print_error("Nothing to pin — no response yet")
        return _CMD_CONTINUE

    if len(pins) >= 20:
        _print_error("Pin limit reached (20). Use /pin rm <name> to free a slot.")
        return _CMD_CONTINUE

    # Determine name
    if not args:
        # Auto-generate: find highest pin-N
        existing_nums = []
        for p in pins:
            if p["name"].startswith("pin-") and p["name"][4:].isdigit():
                existing_nums.append(int(p["name"][4:]))
        next_n = (max(existing_nums) + 1) if existing_nums else 1
        name = f"pin-{next_n}"
    else:
        name = args  # may include sub (whole args string is the name)

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


def _celebration_burst(message: str = "") -> None:
    """Print a short animated celebration burst (confetti + message)."""
    import random
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


def _cmd_celebrate(ctx: "ChatCommandContext") -> str:
    """/celebrate — trigger a celebration animation (just for fun!)."""
    msg = ctx.args.strip() or "Woohoo! 🎉"
    _celebration_burst(msg)
    return _CMD_CONTINUE


def _cmd_rate(ctx: "ChatCommandContext") -> str:
    """Rate the last AI response (/rate [good|ok|bad|meh|1-5])."""
    global _last_response_text
    raw = (ctx.args or "").strip().lower()
    if not raw:
        print(f"Usage: /rate [good|ok|bad|meh|1-5]")
        return _CMD_CONTINUE

    _RATING_MAP = {
        "good": (5, "good"),
        "5":    (5, "good"),
        "4":    (4, "great"),
        "ok":   (3, "ok"),
        "meh":  (3, "ok"),
        "3":    (3, "ok"),
        "2":    (2, "poor"),
        "bad":  (1, "bad"),
        "1":    (1, "bad"),
    }
    if raw not in _RATING_MAP:
        _print_error("Unknown rating — use good, ok, bad, or 1-5")
        return _CMD_CONTINUE

    score, label = _RATING_MAP[raw]

    if not _last_response_text:
        _print_error("Nothing to rate — no response yet")
        return _CMD_CONTINUE

    ts = datetime.now(timezone.utc).isoformat()
    ratings = _PREFS.setdefault("ratings", [])
    ratings.append({"score": score, "label": label, "ts": ts})
    if len(ratings) > 500:
        _PREFS["ratings"] = ratings[-500:]
    _save_prefs()

    if ctx.session_id:
        try:
            append_event(
                session_id=ctx.session_id,
                kind="rating",
                content=f"rated: {label} ({score}/5)",
                metadata={"score": score, "label": label},
            )
        except Exception:
            pass

    _STARS = {5: "⭐⭐⭐⭐⭐", 4: "⭐⭐⭐⭐", 3: "⭐⭐⭐", 2: "⭐⭐", 1: "⭐"}
    stars = _STARS[score]
    msg = f"{stars} Rated: {label}"
    if score >= 4:
        color = _GR
    elif score == 3:
        color = _YL
    else:
        color = _DM
    print(f"{color}{msg}{_R}")
    if score == 5:
        _celebration_burst("5-star rating — thanks! 🎉")

    if score >= 4:
        ratings_list = _PREFS.get("ratings", [])
        streak = 0
        for r in reversed(ratings_list):
            s = r.get("score", 0) if isinstance(r, dict) else 0
            if s >= 4:
                streak += 1
            else:
                break
        if streak in (5, 10, 20, 50):
            _print_ascii_trophy(streak)

    return _CMD_CONTINUE


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
    is_tty = _get_is_tty()
    ratings = _PREFS.get("ratings", [])

    if not ratings:
        msg = "No ratings yet. Use /rate 1-5 after responses!"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    # Calculate current streak (consecutive 4+ from most recent)
    current_streak = 0
    for r in reversed(ratings):
        if isinstance(r, dict):
            score = r.get("score", r.get("rating", 0))
        else:
            try:
                score = int(r)
            except (ValueError, TypeError):
                score = 0
        if score >= 4:
            current_streak += 1
        else:
            break

    # Calculate best streak ever
    best_streak = 0
    running = 0
    for r in ratings:
        if isinstance(r, dict):
            score = r.get("score", r.get("rating", 0))
        else:
            try:
                score = int(r)
            except (ValueError, TypeError):
                score = 0
        if score >= 4:
            running += 1
            best_streak = max(best_streak, running)
        else:
            running = 0

    total = len(ratings)
    high_pct = int(sum(1 for r in ratings if (r.get("score", 0) if isinstance(r, dict) else 0) >= 4) / max(1, total) * 100)

    if _RICH_AVAILABLE and is_tty:
        streak_color = "green" if current_streak >= 5 else "yellow" if current_streak >= 2 else "default"
        _RICH_CONSOLE.print(f"\n[bold cyan]🔥 Rating Streak[/]\n")
        _RICH_CONSOLE.print(f"  Current streak:  [{streak_color}]{current_streak} high ratings[/]  {'🔥' * min(current_streak, 10)}")
        _RICH_CONSOLE.print(f"  Best streak:     [bold]{best_streak}[/]")
        _RICH_CONSOLE.print(f"  High rate (4+):  [bold]{high_pct}%[/] of {total} ratings")
        _RICH_CONSOLE.print()
    else:
        fire = "🔥" * min(current_streak, 10)
        print(f"\n🔥 Rating Streak\n")
        print(f"  Current streak:  {current_streak} high ratings  {fire}")
        print(f"  Best streak:     {best_streak}")
        print(f"  High rate (4+):  {high_pct}% of {total} ratings\n")

    if current_streak >= 5:
        _print_ascii_trophy(current_streak)

    return _CMD_CONTINUE


def _cmd_accessibility(ctx: "ChatCommandContext") -> str:
    """Show or configure accessibility modes (reduced-motion, plain, high-contrast)."""
    args = (ctx.args or "").strip()
    parts = args.split() if args else []
    sub = parts[0].lower() if parts else "status"
    val = parts[1].lower() if len(parts) > 1 else ""

    def _on_off(val: str, key: str, label: str) -> str:
        if val == "on":
            _PREFS[key] = True
            if key == _A11Y_PLAIN_MODE:
                _PREFS["layout"] = "plain"
            _save_prefs()
            return f"{label} enabled."
        elif val == "off":
            _PREFS[key] = False
            if key == _A11Y_PLAIN_MODE and _effective_layout_mode() == "plain":
                _PREFS["layout"] = "normal"
            _save_prefs()
            return f"{label} disabled."
        else:
            state = "ON" if _PREFS.get(key, False) else "off"
            return f"  {label}: {_B}{state}{_R}. Use on|off to change."

    if sub in ("status", ""):
        try:
            import shutil as _shutil
            cols = _shutil.get_terminal_size(fallback=(80, 24)).columns
        except Exception:
            try:
                cols = os.get_terminal_size(fallback=(80, 24)).columns
            except Exception:
                cols = 80

        rm   = "ON" if _a11y_reduced_motion() else "off"
        pm   = "ON" if _a11y_plain_mode()     else "off"
        hc   = "ON" if _a11y_high_contrast()  else "off"
        layout = _effective_layout_mode()
        preset = _layout_preset_name() or "single-pane"
        preset_fallback = _layout_preset_fallback(width=cols, is_tty=_IS_TTY)
        rich = "yes" if _RICH_AVAILABLE else "no"
        tty  = "yes" if _IS_TTY else "no"

        if _RICH_AVAILABLE and _IS_TTY:
            from rich.text import Text as _Text  # noqa: PLC0415
            lines = _RichText()
            lines.append(f"  Reduced motion:   {rm}\n",   style="bold" if rm == "ON" else "dim")
            lines.append(f"  Plain mode:       {pm}\n",   style="bold" if pm == "ON" else "dim")
            lines.append(f"  High contrast:    {hc}\n",   style="bold" if hc == "ON" else "dim")
            lines.append(f"  Layout mode:      {layout}\n", style="dim")
            lines.append(f"  Layout preset:    {preset}\n", style="dim")
            lines.append(f"  Preset fallback:  {preset_fallback}\n", style="dim")
            lines.append(f"  Rich available:   {rich}\n", style="dim")
            lines.append(f"  TTY detected:     {tty}\n",  style="dim")
            lines.append(f"  Terminal width:   {cols} columns", style="dim")
            _RICH_CONSOLE.print(_RichPanel(lines, title=f"{_e('♿', '[a11y]')} Accessibility Status", border_style="cyan"))
        else:
            print(f"{_e('♿', '[a11y]')} Accessibility Status")
            print(f"  Reduced motion:   {rm}")
            print(f"  Plain mode:       {pm}")
            print(f"  High contrast:    {hc}")
            print(f"  Layout mode:      {layout}")
            print(f"  Layout preset:    {preset}")
            print(f"  Preset fallback:  {preset_fallback}")
            print(f"  Rich available:   {rich}")
            print(f"  TTY detected:     {tty}")
            print(f"  Terminal width:   {cols} columns")
        return _CMD_CONTINUE

    if sub == "reduced-motion":
        message = _on_off(val, _A11Y_REDUCED_MOTION, "Reduced motion")
        _print_feedback(message, level="success" if val == "on" else ("warn" if val == "off" else "info"))
        return _CMD_CONTINUE

    if sub == "plain":
        message = _on_off(val, _A11Y_PLAIN_MODE, "Plain mode")
        _print_feedback(message, level="success" if val == "on" else ("warn" if val == "off" else "info"))
        return _CMD_CONTINUE

    if sub == "high-contrast":
        message = _on_off(val, _A11Y_HIGH_CONTRAST, "High contrast")
        _print_feedback(message, level="success" if val == "on" else ("warn" if val == "off" else "info"))
        return _CMD_CONTINUE

    if sub == "reset":
        for key in (_A11Y_REDUCED_MOTION, _A11Y_PLAIN_MODE, _A11Y_HIGH_CONTRAST):
            _PREFS.pop(key, None)
        if _effective_layout_mode() == "plain":
            _PREFS["layout"] = "normal"
        _save_prefs()
        _print_feedback("Accessibility modes reset to defaults.", level="success")
        return _CMD_CONTINUE

    print("  Usage: /accessibility [status|reduced-motion|plain|high-contrast|reset] [on|off]")
    return _CMD_CONTINUE


def _cmd_heatmap(ctx: ChatCommandContext) -> str:
    """/heatmap — show a color-coded hourly activity heatmap of openclaw usage."""
    import datetime
    is_tty = _get_is_tty()

    cmd_history = _PREFS.get("cmd_history", [])

    hour_counts: dict[int, int] = {h: 0 for h in range(24)}
    day_counts: dict[int, int] = {d: 0 for d in range(7)}  # 0=Mon, 6=Sun

    for entry in cmd_history:
        if isinstance(entry, dict):
            ts_str = entry.get("timestamp", entry.get("ts", ""))
        else:
            continue
        if not ts_str:
            continue
        try:
            ts = datetime.datetime.fromisoformat(ts_str)
            hour_counts[ts.hour] = hour_counts.get(ts.hour, 0) + 1
            day_counts[ts.weekday()] = day_counts.get(ts.weekday(), 0) + 1
        except (ValueError, AttributeError):
            continue

    total = sum(hour_counts.values())

    if total == 0:
        msg = "No timestamped history yet — use openclaw for a while to see your heatmap!"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    max_hour = max(hour_counts.values()) or 1

    def _heat_color(count: int, max_count: int) -> str:
        if count == 0:
            return _DM
        ratio = count / max_count
        if ratio > 0.75:
            return _RE
        elif ratio > 0.5:
            return _YE
        elif ratio > 0.25:
            return _GR
        else:
            return _CY

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]🕐 Hourly Activity Heatmap[/] [dim]({total} events)[/]\n")
    else:
        print(f"\n{_B}🕐 Hourly Activity Heatmap{_R} {_DM}({total} events){_R}\n")

    hour_header = "  "
    for h in range(0, 24, 2):
        hour_header += f"{_DM}{h:02d}{_R}  "
    print(hour_header)

    heat_row = "  "
    for h in range(24):
        count = hour_counts[h]
        color = _heat_color(count, max_hour)
        block = "██" if count > 0 else "░░"
        heat_row += f"{color}{block}{_R} "
    print(heat_row)

    count_row = "  "
    for h in range(24):
        count = hour_counts[h]
        count_row += f"{_DM}{count:>2}{_R} "
    print(count_row)

    peak_hour = max(hour_counts, key=hour_counts.get)
    peak_count = hour_counts[peak_hour]

    print(f"\n  {_DM}Peak hour: {_B}{peak_hour:02d}:00{_R} {_DM}({peak_count} events)  ·  "
          f"Legend: {_RE}██{_R}=hot  {_YE}██{_R}=warm  {_GR}██{_R}=mild  {_CY}██{_R}=cool  {_DM}░░=none{_R}\n")

    return _CMD_CONTINUE


def _cmd_quality(ctx: "ChatCommandContext") -> str:
    """/quality — show a colored histogram of response quality ratings."""
    is_tty = _get_is_tty()
    ratings = _PREFS.get("ratings", [])
    snapshot = _last_trace_snapshot(ctx.session_id) if getattr(ctx, "session_id", "") else None

    if not ratings:
        msg = "No ratings yet. Use /rate 1-5 after responses to track quality."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    # Count scores 1-5 — handle both dict entries and raw integers
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
    bar_height = 8  # rows tall

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

    # Vertical histogram: print bar_height rows from top to bottom
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

    # X-axis: star labels and counts
    label_line = "  "
    count_line = "  "
    for score in range(1, 6):
        color = score_colors[score]
        label_line += f"{color} {score_labels[score]}  {_R}"
        count_line += f"{_DM}({counts[score]:>2})  {_R}"
    print(label_line)
    print(count_line)

    # Summary stats
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
                print(f"  Latest route: {snapshot.get('what_happened', '')} · confidence {snapshot.get('conf_label', '(unknown)')}")
                print("  Use /trace for the full decision snapshot.\n")

    return _CMD_CONTINUE


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
    return _handle_simple_toggle_pref(ctx, "path_hints", "path hints")


def _cmd_ratehint(ctx: "ChatCommandContext") -> str:
    """/ratehint [on|off] — toggle the post-response rating hint."""
    return _handle_simple_toggle_pref(ctx, "show_rate_hint", "rating hint", note="/ratehint on|off")


def _cmd_followup(ctx: "ChatCommandContext") -> str:
    """/followup [on|off] — show contextually relevant follow-up suggestions for your last prompt, or toggle the auto-suggestion footer."""
    arg = (ctx.args or "").strip().lower()

    if arg in ("on", "off"):
        _PREFS["show_suggestions"] = (arg == "on")
        state = "on" if _PREFS["show_suggestions"] else "off"
        is_tty = _get_is_tty()
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓[/] follow-up suggestions [bold]{state}[/]")
        else:
            print(f"✓ follow-up suggestions {state}")
        return _CMD_CONTINUE

    last_prompt = str(_PREFS.get("_last_prompt", "") or "")
    if not last_prompt:
        msg = "No recent prompt found. Type a question first, then use /followup."
        is_tty = _get_is_tty()
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    suggestions = _suggest_followups(last_prompt, response_text=_last_response_text, session_id=ctx.session_id)
    is_tty = _get_is_tty()

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(
            f"\n[bold cyan]💡 Follow-up suggestions[/] "
            f"[dim]based on: \"{last_prompt[:50]}{'…' if len(last_prompt) > 50 else ''}\"[/]\n"
        )
        for s in suggestions:
            cmd = s.split(" — ")[0]
            desc = s.split(" — ")[1] if " — " in s else ""
            _RICH_CONSOLE.print(f"  [bold cyan]{cmd}[/]  [dim]{desc}[/]")
        _RICH_CONSOLE.print()
    else:
        print(f"\n💡 Follow-up suggestions (based on: \"{last_prompt[:50]}…\")\n")
        for s in suggestions:
            print(
                f"  {_BCY}{s.split(' — ')[0]}{_R}  "
                f"{_DM}{s.split(' — ')[1] if ' — ' in s else ''}{_R}"
            )
        print()

    return _CMD_CONTINUE


def _cmd_shortcuts(ctx: "ChatCommandContext") -> str:
    """/shortcuts — show keyboard shortcuts and quick-access reference card."""
    is_tty = _get_is_tty()

    sections = [
        ("⌨️  Navigation", [
            ("Tab",          "Auto-complete slash commands"),
            ("↑ / ↓",        "Scroll through command history"),
            ("Ctrl+A",       "Jump to start of line"),
            ("Ctrl+E",       "Jump to end of line"),
            ("Ctrl+W",       "Delete last word"),
            ("Ctrl+U",       "Clear current line"),
        ]),
        ("🔄  Session", [
            ("Ctrl+C",       "Interrupt current response"),
            ("Ctrl+D",       "Exit openclaw"),
            ("/quit",        "Exit gracefully"),
            ("/clear",       "Clear screen"),
        ]),
        ("📋  Quick Commands", [
            ("/last",        "Re-print last response"),
            ("/retry",       "Retry last prompt"),
            ("/draft",       "Edit current draft buffer"),
            ("/history",     "Browse recent prompts"),
            ("/palette",     "Search all commands (new!)"),
        ]),
        ("🎨  Appearance", [
            ("/separator [style]",  "Set response separator style"),
            ("/emojiheaders on|off", "Toggle emoji on headings"),
            ("/autobold on|off",     "Toggle auto-bold in responses"),
            ("/jsonformat on|off",   "Toggle JSON auto-detect & pretty-print"),
            ("/theme",               "Switch color theme"),
        ]),
        ("🔧  Power", [
            ("/macro [name]",   "Run saved macro"),
            ("/pin [key]",      "Pin a value for quick reference"),
            ("/export",         "Export session to file"),
            ("/help",           "Full command reference"),
        ]),
    ]

    if _RICH_AVAILABLE and is_tty:
        from rich.table import Table
        from rich.box import ROUNDED

        _RICH_CONSOLE.print()
        _RICH_CONSOLE.print(_RichPanel.fit("[bold cyan]⌨️  Keyboard Shortcuts & Quick Reference[/]", border_style="cyan"))
        _RICH_CONSOLE.print()

        for section_title, items in sections:
            tbl = Table(box=None, show_header=False, padding=(0, 2))
            tbl.add_column("Key", style="bold yellow", no_wrap=True, min_width=24)
            tbl.add_column("Action", style="default")
            for key, desc in items:
                tbl.add_row(key, desc)
            _RICH_CONSOLE.print(f"[bold]{section_title}[/]")
            _RICH_CONSOLE.print(tbl)
            _RICH_CONSOLE.print()
    else:
        print("\n⌨️  Keyboard Shortcuts & Quick Reference")
        print("=" * 50)
        for section_title, items in sections:
            print(f"\n{section_title}")
            print("─" * 40)
            for key, desc in items:
                print(f"  {key:<24} {desc}")
        print()

    return _CMD_CONTINUE


def _cmd_stats(ctx: "ChatCommandContext") -> str:
    """/stats [category] — show ASCII bar charts of usage statistics (commands, ratings, sessions)."""
    category = ctx.args.strip().lower() or "all"
    is_tty = _get_is_tty()

    cmd_history = _PREFS.get("cmd_history", [])
    ratings = _PREFS.get("ratings", [])

    def _ascii_bar_chart(title: str, data: dict, max_bar: int = 30, color: str = _CY) -> None:
        if not data:
            print(f"  {_DM}No data for {title}{_R}")
            return
        max_val = max(data.values()) if data else 1
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n[bold cyan]{title}[/]")
            for label, count in sorted(data.items(), key=lambda x: -x[1])[:10]:
                bar_len = int((count / max_val) * max_bar)
                bar = "█" * bar_len
                _RICH_CONSOLE.print(f"  [dim]{label:<20}[/] [cyan]{bar:<30}[/] [bold]{count}[/]")
        else:
            print(f"\n{_B}{title}{_R}")
            for label, count in sorted(data.items(), key=lambda x: -x[1])[:10]:
                bar_len = int((count / max_val) * max_bar)
                bar = "█" * bar_len
                print(f"  {_DM}{label:<20}{_R} {color}{bar:<30}{_R} {_B}{count}{_R}")

    cmd_counts: dict = {}
    rating_counts: dict = {}

    if category in ("all", "commands"):
        for entry in cmd_history:
            if isinstance(entry, dict):
                cmd = entry.get("cmd", entry.get("command", "unknown"))
            else:
                cmd = str(entry)
            cmd = cmd.split()[0] if cmd else "unknown"
            cmd_counts[cmd] = cmd_counts.get(cmd, 0) + 1
        _ascii_bar_chart("📊 Command Frequency", cmd_counts, color=_CY)

    if category in ("all", "ratings"):
        for r in ratings:
            if isinstance(r, dict):
                score = str(r.get("score", r.get("rating", "?")))
            else:
                score = str(r)
            label = f"{'⭐' * int(score) if score.isdigit() else score}"
            rating_counts[label] = rating_counts.get(label, 0) + 1
        _ascii_bar_chart("⭐ Rating Distribution", rating_counts, color=_YE)

    if category in ("all", "sessions"):
        try:
            from openclaw_cli_sessions import list_sessions  # type: ignore[import]
            sessions = list_sessions()
            date_counts: dict = {}
            for s in sessions[-50:]:
                ts = s.get("created_at", s.get("timestamp", ""))
                date = ts[:10] if ts else "unknown"
                date_counts[date] = date_counts.get(date, 0) + 1
            _ascii_bar_chart("📅 Sessions by Date", date_counts, color=_GR)
        except Exception:
            pass

    if not cmd_counts and not rating_counts:
        msg = "No usage data yet. Chat a bit first!"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n[dim]{msg}[/]\n")
        else:
            print(f"\n{_DM}{msg}{_R}\n")

    return _CMD_CONTINUE


def _cmd_top(ctx: "ChatCommandContext") -> str:
    """/top [n] — show the n most frequently used prompts and commands (default: 10)."""
    arg = ctx.args.strip()
    n = int(arg) if arg.isdigit() else 10
    n = min(max(n, 1), 50)
    is_tty = _get_is_tty()

    cmd_history = _PREFS.get("cmd_history", [])

    freq: dict = {}
    for entry in cmd_history:
        if isinstance(entry, dict):
            text = entry.get("text", entry.get("prompt", entry.get("cmd", "")))
        else:
            text = str(entry)
        text = text.strip()
        if not text:
            continue
        key = text[:60]
        freq[key] = freq.get(key, 0) + 1

    if not freq:
        msg = "No history yet."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    top = sorted(freq.items(), key=lambda x: -x[1])[:n]
    max_count = top[0][1] if top else 1

    if _RICH_AVAILABLE and is_tty:
        from rich.table import Table
        from rich.box import SIMPLE
        _RICH_CONSOLE.print(f"\n[bold cyan]🔝 Top {len(top)} Most Used[/]\n")
        tbl = Table(box=SIMPLE, show_header=True, header_style="bold cyan")
        tbl.add_column("#", justify="right", style="dim", width=4)
        tbl.add_column("Count", justify="right", style="bold yellow", width=6)
        tbl.add_column("Bar", style="cyan", width=20)
        tbl.add_column("Text", style="default")
        for i, (text, count) in enumerate(top, 1):
            bar_len = int((count / max_count) * 18)
            bar = "█" * bar_len
            preview = text[:55] + "…" if len(text) > 55 else text
            style = "bold green" if text.startswith("/") else "default"
            tbl.add_row(str(i), str(count), bar, f"[{style}]{preview}[/]")
        _RICH_CONSOLE.print(tbl)
        _RICH_CONSOLE.print()
    else:
        print(f"\n🔝 Top {len(top)} Most Used\n")
        for i, (text, count) in enumerate(top, 1):
            bar_len = int((count / max_count) * 20)
            bar = "█" * bar_len
            preview = text[:50] + "…" if len(text) > 50 else text
            print(f"  {i:>3}. {_B}{count:>4}x{_R}  {_CY}{bar:<20}{_R}  {preview}")
        print()

    return _CMD_CONTINUE


def _cmd_freq(ctx: "ChatCommandContext") -> str:
    """/freq — show frequency analysis of slash commands used."""
    is_tty = _get_is_tty()
    cmd_history = _PREFS.get("cmd_history", [])

    slash_freq: dict = {}
    for entry in cmd_history:
        if isinstance(entry, dict):
            text = entry.get("text", entry.get("cmd", ""))
        else:
            text = str(entry)
        text = text.strip()
        if text.startswith("/"):
            cmd_name = text.split()[0]
            slash_freq[cmd_name] = slash_freq.get(cmd_name, 0) + 1

    if not slash_freq:
        msg = "No slash command history yet."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    sorted_cmds = sorted(slash_freq.items(), key=lambda x: -x[1])[:20]
    max_count = sorted_cmds[0][1] if sorted_cmds else 1

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]📊 Slash Command Frequency[/]\n")
        for cmd, count in sorted_cmds:
            bar_len = int((count / max_count) * 25)
            bar = "█" * bar_len
            _RICH_CONSOLE.print(f"  [bold green]{cmd:<20}[/] [cyan]{bar:<25}[/] [bold yellow]{count}[/]")
        _RICH_CONSOLE.print()
    else:
        print(f"\n📊 Slash Command Frequency\n")
        for cmd, count in sorted_cmds:
            bar_len = int((count / max_count) * 25)
            bar = "█" * bar_len
            print(f"  {_BGR}{cmd:<20}{_R} {_CY}{bar:<25}{_R} {_BYE}{count}{_R}")
        print()

    return _CMD_CONTINUE


def _cmd_tip(ctx: "ChatCommandContext") -> str:
    """/tip — show a random openclaw usage tip."""
    import random
    is_tty = _get_is_tty()

    tip = random.choice(_OPENCLAW_TIPS)

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]💡 Tip:[/] {tip}\n")
    else:
        print(f"\n{_BCY}💡 Tip:{_R} {tip}\n")

    return _CMD_CONTINUE


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
    _print_key_bindings()
    return _CMD_CONTINUE


def _cmd_bindlist(ctx: "ChatCommandContext") -> str:
    """/bindlist — show all keyboard bindings (built-in readline + custom)."""
    is_tty = _get_is_tty()

    builtin_bindings = [
        ("Ctrl+R",   "Reverse history search"),
        ("Ctrl+L",   "Clear screen"),
        ("Ctrl+W",   "Delete previous word"),
        ("Ctrl+U",   "Clear current line"),
        ("Ctrl+A",   "Jump to line start"),
        ("Ctrl+E",   "Jump to line end"),
        ("Ctrl+C",   "Interrupt"),
        ("Ctrl+D",   "Exit"),
        ("Tab",      "Auto-complete /commands"),
        ("↑ / ↓",    "Browse history"),
    ]

    custom_bindings = list(_PREFS.get("custom_keybinds", {}).items())

    if _RICH_AVAILABLE and is_tty:
        from rich.table import Table
        from rich.box import SIMPLE
        _RICH_CONSOLE.print(f"\n[bold cyan]⌨️  All Key Bindings[/]\n")

        tbl = Table(box=SIMPLE, show_header=True, header_style="bold cyan")
        tbl.add_column("Key", style="bold yellow", no_wrap=True, width=16)
        tbl.add_column("Action")
        tbl.add_column("Type", style="dim", width=8)

        for key, desc in builtin_bindings:
            tbl.add_row(key, desc, "built-in")

        for key, action in custom_bindings:
            tbl.add_row(key, action, "[green]custom[/]")

        _RICH_CONSOLE.print(tbl)
        if custom_bindings:
            _RICH_CONSOLE.print(f"\n[dim]Custom binds: use /keybind to add more, /keybind clear <key> to remove[/]\n")
        else:
            _RICH_CONSOLE.print(f"\n[dim]No custom binds yet — try: /keybind Ctrl+H /histsearch[/]\n")
    else:
        print(f"\n⌨️  All Key Bindings\n")
        print(f"  {'Key':<16} {'Action':<35} Type")
        print("─" * 60)
        for key, desc in builtin_bindings:
            print(f"  {_BYE}{key:<16}{_R} {desc:<35} {_DM}built-in{_R}")
        for key, action in custom_bindings:
            print(f"  {_BGR}{key:<16}{_R} {action:<35} {_GR}custom{_R}")
        print()

    return _CMD_CONTINUE


def _cmd_keybind(ctx: "ChatCommandContext") -> str:
    """/keybind [key action | list | clear <key>] — manage custom readline key bindings.

    Examples:
      /keybind list                    — show all custom bindings
      /keybind Ctrl+H /histsearch      — bind Ctrl+H to /histsearch
      /keybind Ctrl+T /top             — bind Ctrl+T to /top
      /keybind clear Ctrl+H            — remove a binding
    """
    arg = ctx.args.strip()
    is_tty = _get_is_tty()

    if not arg or arg == "list":
        custom = _PREFS.get("custom_keybinds", {})
        if not custom:
            msg = "No custom keybinds. Try: /keybind Ctrl+H /histsearch"
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[dim]{msg}[/]")
            else:
                print(msg)
            return _CMD_CONTINUE

        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n[bold cyan]⌨️  Custom Keybinds[/]\n")
            for key, action in custom.items():
                _RICH_CONSOLE.print(f"  [bold yellow]{key:<16}[/] → [bold green]{action}[/]")
            _RICH_CONSOLE.print()
        else:
            print(f"\n⌨️  Custom Keybinds\n")
            for key, action in custom.items():
                print(f"  {_BYE}{key:<16}{_R} → {_BGR}{action}{_R}")
            print()
        return _CMD_CONTINUE

    parts = arg.split(None, 1)
    if parts[0] == "clear" and len(parts) > 1:
        key_name = parts[1].strip()
        custom = _PREFS.get("custom_keybinds", {})
        if key_name in custom:
            del custom[key_name]
            _prefs_set("custom_keybinds", custom)
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[green]✓[/] Removed keybind for [bold]{key_name}[/]")
            else:
                print(f"✓ Removed keybind for {key_name}")
        else:
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[yellow]No keybind for '{key_name}'[/]")
            else:
                print(f"No keybind for '{key_name}'")
        return _CMD_CONTINUE

    if len(parts) < 2:
        msg = "Usage: /keybind <Key> <action>  e.g. /keybind Ctrl+H /histsearch"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    key_name = parts[0]
    action = parts[1].strip()

    if not (key_name.startswith("Ctrl+") or key_name.startswith("Alt+")):
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]Key must start with Ctrl+ or Alt+ (e.g. Ctrl+H)[/]")
        else:
            print("Key must start with Ctrl+ or Alt+ (e.g. Ctrl+H)")
        return _CMD_CONTINUE

    if not action.startswith("/"):
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]Action must be a slash command (e.g. /histsearch)[/]")
        else:
            print("Action must be a slash command")
        return _CMD_CONTINUE

    custom = _PREFS.get("custom_keybinds", {})
    custom[key_name] = action
    _prefs_set("custom_keybinds", custom)

    _apply_custom_keybind(key_name, action)

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"[green]✓[/] Bound [bold yellow]{key_name}[/] → [bold green]{action}[/]")
    else:
        print(f"✓ Bound {key_name} → {action}")

    return _CMD_CONTINUE


def _render_diff_ansi(diff_text: str) -> str:
    """Apply ANSI colors to unified diff output (+ green, - red, @@ cyan)."""
    return _render_diff_ansi_impl(diff_text, plain_mode=_a11y_plain_mode())


def _cmd_diff(ctx: ChatCommandContext) -> str:
    """/diff [file1 file2 | --git] — show a colorized unified diff."""
    import subprocess
    arg = ctx.args.strip()
    is_tty = _get_is_tty()

    if not arg or arg == "--git":
        try:
            result = subprocess.run(
                ["git", "diff", "--no-color"],
                capture_output=True, text=True, timeout=10
            )
            diff_text = result.stdout or result.stderr
        except Exception as e:
            diff_text = f"Error: {e}"
    else:
        parts = arg.split(None, 1)
        if len(parts) < 2:
            msg = "Usage: /diff file1 file2  or  /diff --git"
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
            else:
                print(msg)
            return _CMD_CONTINUE
        try:
            result = subprocess.run(
                ["diff", "-u", parts[0], parts[1]],
                capture_output=True, text=True, timeout=10
            )
            diff_text = result.stdout or "(no differences)"
        except Exception as e:
            diff_text = f"Error: {e}"

    if not diff_text or not diff_text.strip():
        msg = "No differences found."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    colored = _render_diff_ansi(diff_text)
    print(colored)
    return _CMD_CONTINUE


def _cmd_changes(ctx: ChatCommandContext) -> str:
    """/changes — show files mentioned/edited in this session."""
    import subprocess
    is_tty = _get_is_tty()

    edits = _PREFS.get("session_edits", [])

    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=5
        )
        git_changes = result.stdout.strip()
    except Exception:
        git_changes = ""

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]📝 Session Changes[/]\n")
        if edits:
            for edit in edits[-20:]:
                _RICH_CONSOLE.print(f"  [dim]→[/] {edit}")
        else:
            _RICH_CONSOLE.print(f"  [dim]No session edits tracked yet[/]")

        if git_changes:
            _RICH_CONSOLE.print(f"\n[bold cyan]🔀 Git Status[/]\n")
            for line in git_changes.split("\n"):
                if line.startswith("M") or line.startswith(" M"):
                    _RICH_CONSOLE.print(f"  [yellow]{line}[/]")
                elif line.startswith("A") or line.startswith(" A"):
                    _RICH_CONSOLE.print(f"  [green]{line}[/]")
                elif line.startswith("D") or line.startswith(" D"):
                    _RICH_CONSOLE.print(f"  [red]{line}[/]")
                elif line.startswith("?"):
                    _RICH_CONSOLE.print(f"  [dim]{line}[/]")
                else:
                    _RICH_CONSOLE.print(f"  {line}")
        _RICH_CONSOLE.print()
    else:
        print(f"\n📝 Session Changes\n")
        if edits:
            for edit in edits[-20:]:
                print(f"  → {edit}")
        else:
            print(f"  No session edits tracked yet")
        if git_changes:
            print(f"\n🔀 Git Status\n")
            for line in git_changes.split("\n"):
                print(f"  {line}")
        print()

    return _CMD_CONTINUE


def _cmd_timeline(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/timeline — show a visual activity timeline of recent openclaw usage."""
    import datetime
    is_tty = _get_is_tty()

    cmd_history = _PREFS.get("cmd_history", [])

    if not cmd_history:
        msg = "No history yet — use openclaw for a while to see your timeline!"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    # Group entries by date
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

    # Sort dates descending (most recent first), show last 7 days
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
            except Exception:
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


def _cmd_dashboard(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/dashboard — show the power dashboard: sessions, stats, pins, and system status."""
    is_tty = _get_is_tty()

    # Gather data
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

    if _RICH_AVAILABLE and is_tty:
        from rich.table import Table
        from rich.panel import Panel
        from rich.columns import Columns
        from rich.box import SIMPLE

        _RICH_CONSOLE.print()

        # Header
        _RICH_CONSOLE.rule("[bold cyan]🦞 OpenClaw Dashboard[/]", style="cyan")
        _RICH_CONSOLE.print()

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

        _RICH_CONSOLE.print(Columns([stats_panel, pins_panel], equal=True, expand=True))

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
        _RICH_CONSOLE.print(activity_panel)

        # Row 3: Quick reference
        _RICH_CONSOLE.print()
        _RICH_CONSOLE.print(
            f"[dim]Build:[/] [bold]{_CLI_BUILD}[/]  "
            f"[dim]Prefs:[/] [bold]{len(_PREFS)} keys[/]  "
            f"[dim]Commands:[/] [bold]{len(_BUILTIN_COMMAND_NAMES)}[/]  "
            f"[dim]Type[/] [bold cyan]/help[/] [dim]for full reference[/]"
        )
        _RICH_CONSOLE.print()
        _RICH_CONSOLE.rule(style="dim")
        _RICH_CONSOLE.print()

    else:
        # Plain-text dashboard
        print(f"\n{'='*60}")
        print(f"  🦞 OpenClaw Dashboard  [{_CLI_BUILD}]")
        print(f"{'='*60}")
        print(f"  Prompts:      {total_prompts}")
        print(f"  Commands:     {total_commands}")
        print(f"  Ratings:      {total_ratings}  (avg: {avg_rating:.1f})")
        print(f"  Est tokens:   ~{est_tokens:,}")
        print(f"  Macros:       {len(macros)}")
        print(f"  Pins:         {len(pins)}")
        print(f"  Snapshots:    {len(snapshots)}")
        print(f"  Commands reg: {len(_BUILTIN_COMMAND_NAMES)}")
        if pins:
            print(f"\n  📌 Pins:")
            for k, v in list(pins.items())[:5]:
                print(f"     {k}: {str(v)[:40]}")
        print(f"\n  Type /help for full reference.")
        print(f"{'='*60}\n")

    return _CMD_CONTINUE


def _cmd_benchmark(ctx: ChatCommandContext) -> str:
    """/benchmark [n] — run n quick AI pings to measure response latency (default: 3)."""
    import time
    import socket

    arg = ctx.args.strip()
    n = int(arg) if arg.isdigit() else 3
    n = min(max(n, 1), 10)
    is_tty = _get_is_tty()

    # Resolve server URL from config or env fallback.
    if ctx.config and getattr(ctx.config, "base_url", None):
        server_url = ctx.config.base_url.rstrip("/")
    else:
        server_url = os.getenv("OPENCLAW_URL", "http://192.168.1.93:8765").rstrip("/")

    host_part = server_url.replace("https://", "").replace("http://", "")
    host = host_part.split(":")[0]
    try:
        port = int(host_part.split(":")[1]) if ":" in host_part else 8765
    except (IndexError, ValueError):
        port = 8765

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]⏱️  Benchmark[/] [dim]({n} TCP pings → {host}:{port})[/]\n")
    else:
        print(f"\n⏱️  Benchmark ({n} pings → {host}:{port})\n")

    times: list[float] = []
    for i in range(n):
        start = time.time()
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
            elapsed = time.time() - start
            times.append(elapsed)

            bar_len = min(int(elapsed * 20), 40)
            bar_color = _RE if elapsed > 3 else _YE if elapsed > 1.5 else _GR
            bar = f"{bar_color}{'█' * bar_len}{_R}"

            if _RICH_AVAILABLE and is_tty:
                color = "red" if elapsed > 3 else "yellow" if elapsed > 1.5 else "green"
                _RICH_CONSOLE.print(f"  [{i + 1}/{n}] [{color}]{elapsed:.3f}s[/]  {bar}")
            else:
                print(f"  [{i + 1}/{n}] {elapsed:.3f}s  {bar}")
        except Exception as exc:  # noqa: BLE001
            elapsed = time.time() - start
            times.append(elapsed)
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"  [{i + 1}/{n}] [red]Error: {exc}[/]")
            else:
                print(f"  [{i + 1}/{n}] Error: {exc}")

    if times:
        avg = sum(times) / len(times)
        mn = min(times)
        mx = max(times)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(
                f"\n  [dim]Min:[/] [bold]{mn:.3f}s[/]  "
                f"[dim]Avg:[/] [bold]{avg:.3f}s[/]  "
                f"[dim]Max:[/] [bold]{mx:.3f}s[/]"
            )
            quality = "🟢 Fast" if avg < 1.5 else "🟡 Moderate" if avg < 3 else "🔴 Slow"
            _RICH_CONSOLE.print(f"  [dim]Quality:[/] {quality}\n")
        else:
            print(f"\n  Min: {mn:.3f}s  Avg: {avg:.3f}s  Max: {mx:.3f}s")
            quality = "Fast" if avg < 1.5 else "Moderate" if avg < 3 else "Slow"
            print(f"  Quality: {quality}\n")

    return _CMD_CONTINUE


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
    ("outputs",      "List or preview saved outputs (/outputs [<index>|<filename>])",                                           _cmd_outputs,      ()),
    ("overlay",      "Toggle opt-in interactive overlays (/overlay [on|off|status])",                                          _cmd_overlay,      ()),
    ("colorscheme",  "View or set the extended color scheme (/colorscheme [name|list|reset])",                                  _cmd_colorscheme,  ()),
    ("rollback",     "List/preview git snapshots or restore latest checkpoint (/rollback [last|list|<name>])",                  _cmd_rollback,     ()),
    ("snapshot",     "Save current git HEAD as a named restore point (/snapshot [name])",                                      _cmd_snapshot,     ()),
    ("events",       "Show recent session events (/events [n|decisions])",                                                     _cmd_events,       ()),
    ("why",          "Explain the last routing or tool decision",                                                               _cmd_why,          ()),
    ("trace",        "Show the latest routing trace with quality context",                                                      _cmd_trace,        ()),
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
    ("handoff",      "Save/restore a resumable workspace handoff  [create|list|open NAME|note TEXT]",                          _cmd_handoff,      ()),
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
    ("rate",         "Rate the last AI response (/rate [good|ok|bad|meh|1-5])",                                               _cmd_rate,         ("feedback",)),
    ("celebrate",    "Trigger a celebration animation (/celebrate [message])",                                                 _cmd_celebrate,    ()),
    ("quality",      "Show response quality stats and rating history",                                                         _cmd_quality,      ()),
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
    ("dashboard",    "Show the power dashboard: sessions, stats, pins, and system status",                                     _cmd_dashboard,    ()),
    ("benchmark",    "Measure AI server response latency (/benchmark [n], default 3 pings, max 10)",                           _cmd_benchmark,    ()),
    ("followup",     "Show contextual follow-up suggestions for your last prompt (/followup [on|off])",                        _cmd_followup,     ()),
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
) -> int:
    """Run an interactive chat session against OpenClaw."""
    _load_prefs()
    history: list[dict[str, str]] = load_conversation_history(session_id) if session_id else []
    registry = build_chat_command_registry()
    load_shell_history()
    _setup_readline()
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


def execute_watch_iteration(
    *,
    session: SessionSummary,
    state: dict[str, Any],
    config: CliConfig,
    output_override: str = "",
    deep_research: bool = False,
    title: str = "",
    on_progress: Callable[[str, str], None] | None = None,
) -> tuple[str, str]:
    """Run a single watch-mode checkpoint and persist its output."""
    goal = str(state.get("goal") or "").strip()
    mode = str(state.get("mode") or "analyze").strip().lower()
    cwd = str(state.get("cwd") or session.cwd or "").strip() or None
    targets = list(state.get("files") or session.files or [])
    if on_progress:
        on_progress("context", "Collecting workspace context")
    normalized_targets, context_text = collect_workspace_context(cwd=cwd, targets=targets)
    if normalized_targets != session.files or (cwd and cwd != session.cwd):
        session = update_session(session.session_id, cwd=cwd or session.cwd, files=normalized_targets)
        state["cwd"] = session.cwd
        state["files"] = list(session.files or [])

    output_path = str(output_override or "").strip()
    if mode == "analyze":
        if on_progress:
            on_progress("request", "Submitting analysis checkpoint")
        prompt = build_analysis_prompt(goal=goal, context_text=context_text, session=session)
        append_event(
            session.session_id,
            kind="analyze",
            content=goal,
            metadata={
                "summary": goal,
                "cwd": session.cwd,
                "files": normalized_targets,
                "plan_id": session.plan_id,
                "task_id": session.task_id,
                "automation_mode": "watch",
            },
        )
        response = invoke_openclaw(
            prompt,
            config=bind_config_to_session(config, session.session_id),
            history=load_conversation_history(session.session_id),
        )
        persist_response(session.session_id, goal, response.response)
        if on_progress:
            on_progress("persist", "Saving analysis checkpoint")
        if output_path:
            write_text_file(output_path, content=response.response)
            saved_path = output_path
        else:
            saved_path = str(
                save_output(
                    session.session_id,
                    output_name_from_title(f"watch-{mode}-{state.get('poll_count', 0)}", default_stem="watch-analysis", suffix=".md"),
                    response.response,
                )
            )
        return response.response, saved_path

    if mode == "research":
        try:
            from research_agent import ResearchAgent
        except ImportError as exc:
            raise OpenClawCliError(missing_feature_hint("openclaw watch --mode research")) from exc

        effective_query = goal
        plan_ctx = _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd)
        if plan_ctx:
            effective_query = f"{plan_ctx}\n\n{effective_query}"
        if context_text and normalized_targets:
            effective_query = f"{effective_query}\n\nLocal workspace context:\n{context_text[:4000]}"

        if on_progress:
            on_progress("request", "Starting research checkpoint")

        async def _progress(message: str) -> None:
            if on_progress:
                on_progress("research", message)

        append_event(
            session.session_id,
            kind="research",
            content=goal,
            metadata={"summary": goal, "files": normalized_targets, "automation_mode": "watch"},
        )
        report = run_async(ResearchAgent().run(effective_query, on_progress=_progress, deep=deep_research))
        if on_progress:
            on_progress("persist", "Saving research checkpoint")
        if output_path:
            write_text_file(output_path, content=report)
            saved = output_path
        else:
            saved = str(
                save_output(
                    session.session_id,
                    output_name_from_title(f"watch-{mode}-{state.get('poll_count', 0)}", default_stem="watch-research", suffix=".md"),
                    report,
                )
            )
        append_event(session.session_id, kind="assistant", content=report, metadata={"summary": f"saved research to {saved}"})
        return report, saved

    if mode == "write":
        document_title = title or goal[:80] or "OpenClaw Watch Draft"
        if on_progress:
            on_progress("request", "Submitting writing checkpoint")
        prompt = build_write_prompt(task=goal, context_text=context_text, session=session, title=document_title)
        append_event(
            session.session_id,
            kind="write",
            content=goal,
            metadata={"summary": goal, "files": normalized_targets, "automation_mode": "watch"},
        )
        response = invoke_openclaw(
            prompt,
            config=bind_config_to_session(config, session.session_id),
            history=load_conversation_history(session.session_id),
        )
        persist_response(session.session_id, goal, response.response)
        if on_progress:
            on_progress("persist", "Saving writing checkpoint")
        if output_path:
            write_text_file(output_path, content=response.response)
            saved = output_path
        else:
            saved = str(
                save_output(
                    session.session_id,
                    output_name_from_title(f"watch-{document_title}-{state.get('poll_count', 0)}", default_stem="watch-draft", suffix=".md"),
                    response.response,
                )
            )
        return response.response, saved

    raise OpenClawCliError(f"Unsupported watch mode: {mode}")


def handle_watch_command(args: argparse.Namespace, *, config: CliConfig) -> int:
    """Run a resumable watch loop over a session workspace."""
    resume_id = str(getattr(args, "resume", "") or "").strip()
    requested_session = str(getattr(args, "session", "") or config.session_id or "").strip()
    if resume_id and requested_session and resume_id != requested_session:
        raise OpenClawCliError("Use either --resume or --session for watch mode, not both.")

    existing_state = load_watch_state(resume_id or requested_session) if (resume_id or requested_session) else None
    session_seed = require_session(resume_id or requested_session) if (resume_id or requested_session) else None
    goal_parts, prompt_targets = extract_prompt_targets(
        list(getattr(args, "goal", []) or []),
        cwd=getattr(args, "cwd", None) or (session_seed.cwd if session_seed else None),
    )
    prompt_goal = parse_prompt(goal_parts) if goal_parts else ""
    plan_id = str(getattr(args, "plan_id", "") or (existing_state or {}).get("plan_id") or (session_seed.plan_id if session_seed else "")).strip()
    task_id = str(getattr(args, "task_id", "") or (existing_state or {}).get("task_id") or (session_seed.task_id if session_seed else "")).strip()
    goal = prompt_goal or str((existing_state or {}).get("goal") or "").strip() or load_plan_goal(plan_id)
    if task_id and not goal:
        goal = f"Continue task {task_id}"
    if not goal:
        raise OpenClawCliError("Watch mode needs a goal, plan, or task to follow.")

    mode = str(getattr(args, "mode", "") or (existing_state or {}).get("mode") or "analyze").strip().lower()
    interval_seconds = max(1, int(getattr(args, "interval", 0) or (existing_state or {}).get("interval_seconds") or 60))
    max_polls = max(0, int(getattr(args, "iterations", 0) or (existing_state or {}).get("max_polls") or 0))
    on_change = bool(getattr(args, "on_change", False) or (existing_state or {}).get("on_change"))
    cwd = str(getattr(args, "cwd", "") or (existing_state or {}).get("cwd") or (session_seed.cwd if session_seed else "")).strip() or None
    explicit_targets = [*list(getattr(args, "files", []) or []), *prompt_targets]
    if not explicit_targets:
        explicit_targets = list((existing_state or {}).get("files") or (session_seed.files if session_seed else []) or [])
    normalized_targets, _ = collect_workspace_context(cwd=cwd, targets=explicit_targets)

    session = ensure_cli_session(
        resume_id or requested_session,
        title=f"Watch: {goal[:60]}",
        cwd=cwd,
        files=normalized_targets,
        plan_id=plan_id,
        task_id=task_id,
    )
    session = update_session(
        session.session_id,
        cwd=cwd or session.cwd,
        files=normalized_targets,
        plan_id=plan_id,
        task_id=task_id,
        automation_mode=mode,
        automation_status="watching",
        watch_interval_seconds=interval_seconds,
    )

    resume_snapshot = normalize_watch_state(existing_state) if existing_state else None
    state = existing_state or build_watch_state(
        session=session,
        mode=mode,
        goal=goal,
        interval_seconds=interval_seconds,
        max_polls=max_polls,
        on_change=on_change,
    )
    state = normalize_watch_state(state)
    state.update(
        {
            "mode": mode,
            "goal": goal,
            "cwd": session.cwd,
            "files": list(normalized_targets),
            "plan_id": plan_id,
            "task_id": task_id,
            "interval_seconds": interval_seconds,
            "max_polls": max_polls,
            "on_change": on_change,
            "status": "running",
            "updated_at": utc_timestamp(),
        }
    )
    save_watch_state(session.session_id, state)

    if not config.output_json:
        if resume_snapshot:
            print_watch_resume_snapshot(session.session_id, resume_snapshot, output_json=config.output_json)
        if _RICH_AVAILABLE and _IS_TTY:
            _body = _RichText()
            _body.append(f"  session  ", style="dim")
            _body.append(f"{session.session_id}\n")
            _body.append(f"  mode     ", style="dim")
            _body.append(f"{mode}\n")
            _body.append(f"  goal     ", style="dim")
            _body.append(f"{goal[:60]}\n")
            _body.append(f"  interval ", style="dim")
            _body.append(f"{interval_seconds}s")
            _body.append("  ·  max ", style="dim")
            _body.append(f"{'infinite' if max_polls == 0 else max_polls}\n")
            _body.append("  Ctrl-C to pause & resume", style="dim")
            _RICH_CONSOLE.print(_RichPanel(_body, border_style="cyan", title="[bold cyan]👁  watch[/]"))
        else:
            print(
                f"Watching session {session.session_id} in {mode} mode "
                f"(interval={interval_seconds}s, max polls={'infinite' if max_polls == 0 else max_polls})."
            )
            print("Press Ctrl-C to stop and resume later with `openclaw watch --resume <session_id>`.")

    try:
        while max_polls == 0 or int(state.get("poll_count", 0) or 0) < max_polls:
            state = refresh_watch_controls(session.session_id, state)
            if state.get("stop_requested"):
                return stop_watch_from_intervention(
                    session=session,
                    state=state,
                    mode=mode,
                    output_json=config.output_json,
                )
            state["poll_count"] = int(state.get("poll_count", 0) or 0) + 1
            workspace_signature = build_workspace_signature(cwd=state.get("cwd"), targets=list(state.get("files") or []))
            force_run_once = bool(state.get("force_run_once"))
            if on_change and state.get("workspace_signature") and workspace_signature == state.get("workspace_signature") and not force_run_once:
                state["updated_at"] = utc_timestamp()
                state["status"] = "waiting"
                save_watch_state(session.session_id, state)
                update_session(session.session_id, automation_status="waiting", automation_mode=mode)
                if not config.output_json:
                    print(f"[watch {state['poll_count']}] unchanged; waiting for workspace updates.")
            else:
                if force_run_once:
                    state["force_run_once"] = False
                    resolve_watch_intervention(
                        state,
                        action="force-checkpoint",
                        status="applied",
                        note="Forced one checkpoint despite unchanged workspace.",
                    )
                    record_watch_progress(
                        session_id=session.session_id,
                        state=state,
                        iteration=state["poll_count"],
                        mode=mode,
                        phase="control",
                        message="Dashboard requested a forced checkpoint; running anyway.",
                        output_json=config.output_json,
                    )
                state["active_checkpoint"] = start_watch_checkpoint(iteration=state["poll_count"], mode=mode)
                save_watch_state(session.session_id, state)
                retry_limit = max(1, int(state.get("retry_limit") or WATCH_RETRY_LIMIT))
                attempt = 0
                while True:
                    attempt += 1
                    active_checkpoint = state.setdefault("active_checkpoint", start_watch_checkpoint(iteration=state["poll_count"], mode=mode))
                    attempts = list(active_checkpoint.get("attempts") or [])
                    attempts.append({"attempt": attempt, "started_at": utc_timestamp(), "status": "running"})
                    active_checkpoint["attempts"] = attempts[-WATCH_PROGRESS_LOG_LIMIT:]
                    active_checkpoint["updated_at"] = utc_timestamp()
                    save_watch_state(session.session_id, state)

                    try:
                        result_text, output_path = execute_watch_iteration(
                            session=require_session(session.session_id),
                            state=state,
                            config=config,
                            output_override=str(getattr(args, "output", "") or "").strip(),
                            deep_research=bool(getattr(args, "deep", False)),
                            title=str(getattr(args, "title", "") or "").strip(),
                            on_progress=lambda phase, message: record_watch_progress(
                                session_id=session.session_id,
                                state=state,
                                iteration=state["poll_count"],
                                mode=mode,
                                phase=phase,
                                message=message,
                                output_json=config.output_json,
                            ),
                        )
                        finished_at = utc_timestamp()
                        active_checkpoint["attempts"][-1].update(
                            {
                                "finished_at": finished_at,
                                "status": "completed",
                                "duration_seconds": _elapsed_seconds(
                                    active_checkpoint["attempts"][-1].get("started_at"),
                                    finished_at,
                                ),
                            }
                        )
                        break
                    except Exception as exc:
                        error_message = str(exc).strip() or exc.__class__.__name__
                        transient = is_transient_watch_error(error_message)
                        finished_at = utc_timestamp()
                        active_checkpoint["attempts"][-1].update(
                            {
                                "finished_at": finished_at,
                                "status": "failed",
                                "error": error_message,
                                "transient": transient,
                                "duration_seconds": _elapsed_seconds(
                                    active_checkpoint["attempts"][-1].get("started_at"),
                                    finished_at,
                                ),
                            }
                        )
                        state["failure_count"] = int(state.get("failure_count") or 0) + 1
                        state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
                        state["last_error"] = error_message
                        retry_entry = {
                            "poll": state["poll_count"],
                            "attempt": attempt,
                            "error": error_message,
                            "transient": transient,
                            "created_at": utc_timestamp(),
                            "delay_seconds": watch_retry_delay_seconds(attempt) if transient and attempt < retry_limit else 0,
                        }
                        retry_history = list(state.get("retry_history") or [])
                        retry_history.append(retry_entry)
                        state["retry_history"] = retry_history[-WATCH_PROGRESS_LOG_LIMIT:]
                        state["status"] = "retrying" if transient and attempt < retry_limit else "failed"
                        state["updated_at"] = utc_timestamp()
                        save_watch_state(session.session_id, state)
                        update_session(
                            session.session_id,
                            automation_mode=mode,
                            automation_status="retrying" if transient and attempt < retry_limit else "failed",
                            watch_interval_seconds=interval_seconds,
                        )
                        if transient and attempt < retry_limit:
                            delay_seconds = int(retry_entry.get("delay_seconds") or watch_retry_delay_seconds(attempt))
                            record_watch_progress(
                                session_id=session.session_id,
                                state=state,
                                iteration=state["poll_count"],
                                mode=mode,
                                phase="retry",
                                message=(
                                    f"Transient failure on attempt {attempt}/{retry_limit}: "
                                    f"{error_message}. Retrying in {delay_seconds}s."
                                ),
                                output_json=config.output_json,
                            )
                            time.sleep(delay_seconds)
                            continue
                        failure_summary = f"{mode} failed: {error_message[:160]}"
                        checkpoint_completed_at = utc_timestamp()
                        active_checkpoint.update(
                            {
                                "status": "failed",
                                "completed_at": checkpoint_completed_at,
                                "summary": failure_summary,
                                "error": error_message,
                                "transient": transient,
                                "duration_seconds": _elapsed_seconds(
                                    active_checkpoint.get("started_at"),
                                    checkpoint_completed_at,
                                ),
                            }
                        )
                        state.setdefault("checkpoints", []).append(dict(active_checkpoint))
                        state["last_run_at"] = active_checkpoint["completed_at"]
                        state["last_summary"] = failure_summary
                        state["active_checkpoint"] = {}
                        save_watch_state(session.session_id, state)
                        append_event(
                            session.session_id,
                            kind="checkpoint",
                            content=failure_summary,
                            metadata={
                                "summary": failure_summary,
                                "mode": mode,
                                "poll": state["poll_count"],
                                "plan_id": plan_id,
                                "task_id": task_id,
                                "status": "failed",
                                "error": error_message,
                                "retry_count": attempt,
                                "retry_delay_seconds": _watch_retry_delay_total(state),
                                "elapsed_seconds": active_checkpoint.get("duration_seconds"),
                            },
                        )
                        raise OpenClawCliError(
                            f"Watch poll {state['poll_count']} failed after {attempt} attempt(s): {error_message}"
                        ) from exc
                checkpoint_summary = str(result_text or "").strip().splitlines()[0][:160] if str(result_text or "").strip() else f"{mode} checkpoint"
                checkpoint = {
                    "poll": state["poll_count"],
                    "created_at": utc_timestamp(),
                    "completed_at": utc_timestamp(),
                    "summary": checkpoint_summary,
                    "output_path": output_path,
                    "workspace_signature": workspace_signature,
                    "status": "completed",
                    "attempt_count": attempt,
                    "progress": list(state.get("active_checkpoint", {}).get("progress") or []),
                    "attempts": list(state.get("active_checkpoint", {}).get("attempts") or []),
                    "started_at": str(state.get("active_checkpoint", {}).get("started_at") or ""),
                }
                checkpoint["duration_seconds"] = _elapsed_seconds(checkpoint.get("started_at") or checkpoint.get("created_at"), checkpoint.get("completed_at"))
                state.setdefault("checkpoints", []).append(checkpoint)
                state["workspace_signature"] = workspace_signature
                state["last_run_at"] = checkpoint["completed_at"]
                state["last_output_path"] = output_path
                state["last_summary"] = checkpoint_summary
                state["last_error"] = ""
                state["consecutive_failures"] = 0
                state["status"] = "running"
                state["updated_at"] = checkpoint["completed_at"]
                state["active_checkpoint"] = {}
                save_watch_state(session.session_id, state)
                append_event(
                    session.session_id,
                    kind="checkpoint",
                    content=checkpoint_summary,
                    metadata={
                        "summary": checkpoint_summary,
                        "mode": mode,
                        "poll": state["poll_count"],
                        "output_path": output_path,
                        "plan_id": plan_id,
                        "task_id": task_id,
                        "elapsed_seconds": checkpoint.get("duration_seconds"),
                        "retry_delay_seconds": _watch_retry_delay_total(state),
                    },
                )
                update_session(
                    session.session_id,
                    automation_mode=mode,
                    automation_status="running",
                    watch_interval_seconds=interval_seconds,
                )
                render_watch_iteration(
                    iteration=state["poll_count"],
                    mode=mode,
                    summary=checkpoint_summary,
                    output_path=output_path,
                    output_json=config.output_json,
                )

            if max_polls and int(state.get("poll_count", 0) or 0) >= max_polls:
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        active_checkpoint = state.get("active_checkpoint")
        if isinstance(active_checkpoint, dict) and active_checkpoint:
            interrupted_at = utc_timestamp()
            interruption_summary = str(active_checkpoint.get("last_message") or f"{mode} interrupted").strip()[:160]
            active_checkpoint.update(
                {
                    "status": "interrupted",
                    "completed_at": interrupted_at,
                    "summary": interruption_summary,
                }
            )
            state.setdefault("checkpoints", []).append(dict(active_checkpoint))
            state["last_run_at"] = interrupted_at
            state["last_summary"] = interruption_summary
            state["active_checkpoint"] = {}
        state["status"] = "interrupted"
        state["updated_at"] = utc_timestamp()
        save_watch_state(session.session_id, state)
        update_session(session.session_id, automation_mode=mode, automation_status="interrupted")
        if not config.output_json:
            _print_meta_footer(("resume", f"openclaw watch --resume {session.session_id}"))
        return 130

    state["status"] = "completed" if max_polls else "idle"
    state["updated_at"] = utc_timestamp()
    save_watch_state(session.session_id, state)
    update_session(
        session.session_id,
        automation_mode=mode,
        automation_status="completed" if max_polls else "idle",
        watch_interval_seconds=interval_seconds,
    )
    if not config.output_json:
        _print_meta_footer(("session", session.session_id))
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
                except Exception:
                    pass
            else:
                # Standard install: check PyPI
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
                return run_chat(scoped_config, session_id=session_id)
            return run_chat(scoped_config)
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
        except Exception:
            pass
        _print_connection_error_panel(str(exc), base_url=_base)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
