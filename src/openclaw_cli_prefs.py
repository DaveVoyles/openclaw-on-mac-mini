"""User preferences and configuration subsystem for OpenClaw CLI."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tips
# ---------------------------------------------------------------------------
_OPENCLAW_TIPS: list[str] = [
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
    "Use /tokeninfo to check how full your context window is.",
    "Use /trace to see the full routing decision with quality context.",
    "Use /handoff check to audit session readiness before handing off.",
    "Use /fleet health to get a cross-session automation health summary.",
    "Use /alerts list to see computed operator alerts from active sessions.",
    "Use /collab decision to record a tagged decision for later export.",
    "Use /bookmark to save a replay point in the current session.",
    "Use /overlay on to enable interactive list pickers for session commands.",
    "Use /pattern list to browse saved prompt patterns.",
    "Use /draft multiline on to enter multi-line compose mode.",
]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_OPENCLAW_DIR: Path = Path.home() / ".openclaw"
_PREFS_FILE: Path = _OPENCLAW_DIR / "prefs.json"

# ---------------------------------------------------------------------------
# Preference defaults
# ---------------------------------------------------------------------------
_PREFS: dict[str, Any] = {
    "theme": "default",  # separator / accent colour
    "emoji": True,  # show emoji in UI (False → ASCII fallbacks)
    "emoji_pack": "classic",  # "classic" | "minimal" | "ascii"
    "layout": "normal",  # "compact" | "normal" | "verbose" | "plain"
    "layout_preset": "",  # "" | "focus" | "watch-monitor" | "handoff"
    "layout_focus": "primary",  # "primary" | "supporting"
    "interactive_overlays": False,  # opt-in interactive pickers for supported list commands
    "emoji_headers": True,  # prepend emoji to markdown headings in AI responses
}

# ---------------------------------------------------------------------------
# Accessibility mode keys in _PREFS
# ---------------------------------------------------------------------------
_A11Y_REDUCED_MOTION = "reduced_motion"  # bool: disable spinner/animations
_A11Y_PLAIN_MODE = "plain_mode"  # bool: simplify chrome to plain text
_A11Y_HIGH_CONTRAST = "high_contrast"  # bool: high-contrast colour palette

# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------
_THEMES: dict[str, tuple[str, str]] = {
    "default": ("dim blue", "\033[2;34m"),
    "green": ("dim green", "\033[2;32m"),
    "yellow": ("dim yellow", "\033[2;33m"),
    "magenta": ("dim magenta", "\033[2;35m"),
    "cyan": ("dim cyan", "\033[2;36m"),
    "mono": ("dim", "\033[2m"),
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

# ---------------------------------------------------------------------------
# Emoji packs
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


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
