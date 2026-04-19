"""llm/context_limits.py — Model-aware context window limits.

Maps known model names/prefixes to their published context window sizes.
Prefix matching is used so that model variants (e.g. ``gemini-2.0-flash-exp``)
resolve to their base entry (``gemini-2.0-flash``).
"""
from __future__ import annotations

# Published context window sizes (in tokens).
# Values are conservative/well-known figures; longer suffix variants inherit
# the base prefix value via get_model_context_window().
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gemini-2.0-flash": 1_048_576,
    "gemini-1.5-pro": 2_097_152,
    "gemini-1.5-flash": 1_048_576,
    "gemini-1.0-pro": 32_768,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o1-mini": 200_000,
    "o3-mini": 200_000,
    "claude-sonnet-4.5": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-haiku": 200_000,
    "ollama": 8_192,
    # Copilot proxy model names (host.docker.internal:9191/v1)
    "copilot/gpt-4o": 128_000,
    "copilot/gpt-4o-mini": 128_000,
    "copilot/gpt-4.1": 1_047_576,
    "copilot/gpt-4.1-mini": 1_047_576,
    "copilot/o1": 200_000,
    "copilot/o1-mini": 200_000,
    "copilot/o3-mini": 200_000,
    "copilot/claude-sonnet-4.5": 200_000,
    "copilot/claude-sonnet-4": 200_000,
    "copilot/claude-opus-4": 200_000,
    "copilot/claude-3-5-sonnet": 200_000,
}

# Sorted longest-first so the most specific prefix always wins.
_SORTED_PREFIXES: list[tuple[str, int]] = sorted(
    MODEL_CONTEXT_WINDOWS.items(), key=lambda kv: -len(kv[0])
)


def get_model_context_window(model_name: str | None) -> int | None:
    """Return the context window (tokens) for *model_name*, or ``None`` if unknown.

    Matching is case-insensitive and uses the longest matching prefix, so
    ``gemini-2.0-flash-exp`` resolves to the ``gemini-2.0-flash`` entry.
    """
    if not model_name:
        return None
    normalised = model_name.strip().lower()
    for prefix, limit in _SORTED_PREFIXES:
        if normalised == prefix or normalised.startswith(prefix + "-") or normalised.startswith(prefix + ":"):
            return limit
    return None
