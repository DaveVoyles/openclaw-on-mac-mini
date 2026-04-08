"""Per-user model preferences, persisted to disk."""

import json
import logging

from memory_helpers import MEMORY_DIR, _atomic_write
from model_aliases import model_input_suggestion, normalize_model_input

log = logging.getLogger("openclaw.memory")

__all__ = [
    "_PREFS_DIR",
    "_VALID_MODEL_PREFS",
    "_prefs_path",
    "_load_prefs",
    "_save_prefs",
    "get_model_preference",
    "set_model_preference",
]

_PREFS_DIR = MEMORY_DIR / "preferences"
_VALID_MODEL_PREFS = {"auto", "local", "gemini", "openai", "anthropic"}


def _prefs_path(user_id: int):
    _PREFS_DIR.mkdir(parents=True, exist_ok=True)
    return _PREFS_DIR / f"{user_id}.json"


def _load_prefs(user_id: int) -> dict:
    path = _prefs_path(user_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        log.debug("Failed to load preferences for user %d: %s", user_id, exc)
        return {}


def _save_prefs(user_id: int, prefs: dict) -> None:
    _atomic_write(_prefs_path(user_id), json.dumps(prefs, indent=2))


def get_model_preference(user_id: int) -> str:
    """Return the user's sticky model preference (default from config: 'auto')."""
    from config import cfg
    return _load_prefs(user_id).get("model_preference", cfg.default_model_preference)


def set_model_preference(user_id: int, pref: str) -> str:
    """Set the user's sticky model preference. Returns status message."""
    raw_pref = (pref or "").strip()
    pref = normalize_model_input(raw_pref)
    if pref not in _VALID_MODEL_PREFS:
        suggestion = model_input_suggestion(raw_pref)
        suggestion_suffix = f" {suggestion}" if suggestion else ""
        return (
            f"❌ Invalid preference `{raw_pref.lower()}`."
            f"{suggestion_suffix} Choose: `auto`, `local`, `gemini`, `openai`, or `anthropic`."
        )
    prefs = _load_prefs(user_id)
    prefs["model_preference"] = pref
    _save_prefs(user_id, prefs)
    labels = {
        "auto": "🔄 Auto (Copilot → Gemini)",
        "local": "🏠 Local (Gemma/Ollama)",
        "gemini": "☁️ Gemini (cloud)",
        "openai": "🟢 OpenAI (GPT-4o)",
        "anthropic": "🟣 Anthropic (Claude)",
    }
    return f"✅ Model preference set to **{labels.get(pref, pref)}**."
