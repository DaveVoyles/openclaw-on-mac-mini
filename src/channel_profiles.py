"""Channel-aware retrieval profile resolution."""

from __future__ import annotations


def resolve_retrieval_profile_settings(query: str, channel_name: str = "") -> dict:
    """Return default retrieval settings. Channel focus is handled via name prefix in model_message."""
    return {"min_results": 3, "expand_query": False, "topic_class": "general"}


def channel_context_prefix(channel_name: str) -> str:
    """Return a single-line context hint to prepend to model_message."""
    if not channel_name or channel_name in ("general", ""):
        return ""
    return f"Channel: #{channel_name}\n"
