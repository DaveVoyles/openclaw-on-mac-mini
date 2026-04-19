"""Per-user model preferences, persisted to disk."""

import json
import logging

from memory_helpers import MEMORY_DIR, _atomic_write
from model_aliases import model_input_suggestion, normalize_model_input

log = logging.getLogger(__name__)

__all__ = [
    "_PREFS_DIR",
    "_VALID_MODEL_PREFS",
    "_prefs_path",
    "_load_prefs",
    "_save_prefs",
    "get_model_preference",
    "set_model_preference",
    "get_routing_profile",
    "set_routing_profile",
]

_PREFS_DIR = MEMORY_DIR / "preferences"
_VALID_MODEL_PREFS = {"auto", "local", "gemini", "openai", "anthropic", "copilot"}


def _prefs_path(user_id: int):
    _PREFS_DIR.mkdir(parents=True, exist_ok=True)
    return _PREFS_DIR / f"{user_id}.json"


def _load_prefs(user_id: int) -> dict:
    path = _prefs_path(user_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
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
            f"{suggestion_suffix} Choose: `auto`, `local`, `gemini`, `openai`, `anthropic`, or `copilot`."
        )
    prefs = _load_prefs(user_id)
    prefs["model_preference"] = pref
    _save_prefs(user_id, prefs)
    labels = {
        "auto": "🔄 Auto (routing profile)",
        "local": "🏠 Local (Gemma/Ollama)",
        "gemini": "☁️ Gemini (cloud)",
        "openai": "🟢 OpenAI (GPT-4o)",
        "anthropic": "🟣 Anthropic (Claude)",
        "copilot": "🟦 Copilot (enterprise proxy)",
    }
    return f"✅ Model preference set to **{labels.get(pref, pref)}**."


def get_routing_profile(user_id: int) -> str:
    """Return the user's routing profile override, or '' if none set (use system default)."""
    return _load_prefs(user_id).get("routing_profile", "")


def set_routing_profile(user_id: int, profile: str) -> str:
    """Set the user's routing profile override. Returns status message."""
    from model_routing_policy import VALID_ROUTING_PROFILES, normalize_routing_profile

    raw = (profile or "").strip()
    normalized = normalize_routing_profile(raw)
    # If normalize_routing_profile fell back to the config default, the input was invalid
    if raw.replace("_", "-").lower() not in VALID_ROUTING_PROFILES:
        choices = ", ".join(f"`{p}`" for p in sorted(VALID_ROUTING_PROFILES))
        return f"❌ Invalid profile `{raw}`. Choose: {choices}."
    prefs = _load_prefs(user_id)
    prefs["routing_profile"] = normalized
    _save_prefs(user_id, prefs)
    labels = {
        "copilot-first": "🟦 Copilot-first — Copilot for non-tool asks, Gemini for tools",
        "balanced": "⚖️ Balanced — best provider per query type",
        "gemini-first": "☁️ Gemini-first — Gemini preferred for everything",
        "cost-saver": "💰 Cost-saver — local Ollama first, Gemini only when needed",
    }
    return f"✅ Routing profile set to **{labels.get(normalized, normalized)}**."
