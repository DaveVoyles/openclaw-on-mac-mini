"""Shared model/provider alias normalization and suggestion helpers."""

from __future__ import annotations

from difflib import get_close_matches

MODEL_INPUT_ALIASES: dict[str, str] = {
    "claude": "anthropic",
}

VALID_MODEL_PREFERENCES = {"auto", "local", "gemini", "openai", "anthropic"}


def normalize_model_input(model_or_provider: str) -> str:
    """Normalize user-entered model/provider aliases into canonical routing keys."""
    normalized = (model_or_provider or "").strip().lower()
    return MODEL_INPUT_ALIASES.get(normalized, normalized)


def model_input_suggestion(model_or_provider: str) -> str:
    """Return concise invalid-model guidance with optional did-you-mean hints."""
    attempted = (model_or_provider or "").strip().lower()
    if not attempted:
        return ""
    if attempted in VALID_MODEL_PREFERENCES:
        return ""
    if attempted in MODEL_INPUT_ALIASES:
        canonical = MODEL_INPUT_ALIASES[attempted]
        return f"Did you mean `{attempted}` (alias for `{canonical}`)?"
    candidates = sorted(VALID_MODEL_PREFERENCES | set(MODEL_INPUT_ALIASES))
    matches = get_close_matches(attempted, candidates, n=1, cutoff=0.6)
    if matches:
        match = matches[0]
        canonical = MODEL_INPUT_ALIASES.get(match, match)
        if canonical != match:
            return f"Did you mean `{match}` (alias for `{canonical}`)?"
        return f"Did you mean `{match}`?"
    return ""
