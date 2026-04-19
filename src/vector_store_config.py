"""Vector store configuration, constants, embedding function, and recall-guard state."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

CHROMA_DIR = Path(os.getenv("CHROMA_DIR", "/memory/chromadb"))
SIMILARITY_THRESHOLD = float(os.getenv("CHROMA_SIMILARITY_THRESHOLD", "0.7"))
DEFAULT_TOP_K = 5

# Collection names
MEMORIES_COLLECTION = "memories"
CONVERSATIONS_COLLECTION = "conversations"
RESEARCH_COLLECTION = "research"

# Embedding model configuration
# Default: ChromaDB's built-in all-MiniLM-L6-v2 (384 dims, free, CPU)
# Optional: Ollama-hosted models like embeddinggemma, nomic-embed-text, etc.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")  # empty = ChromaDB default
from config import cfg as _vs_cfg

OLLAMA_EMBED_URL = _vs_cfg.ollama_url


def _get_embedding_function():
    """Return the embedding function for ChromaDB collections.

    If EMBEDDING_MODEL is set, uses Ollama's embedding API.
    Otherwise returns None (ChromaDB uses its built-in default).

    WARNING: Changing embedding models requires re-indexing. Existing
    collections with MiniLM embeddings are incompatible with new model
    dimensions. Delete /memory/chromadb and let it rebuild.
    """
    if not EMBEDDING_MODEL:
        return None  # ChromaDB default (all-MiniLM-L6-v2)

    try:
        from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

        log.info("Using Ollama embedding model: %s at %s", EMBEDDING_MODEL, OLLAMA_EMBED_URL)
        log.warning(
            "Custom embedding model active (%s). If you switched models, "
            "existing collections must be re-indexed (delete %s and restart).",
            EMBEDDING_MODEL,
            CHROMA_DIR,
        )
        return OllamaEmbeddingFunction(
            url=f"{OLLAMA_EMBED_URL}/api/embeddings",
            model_name=EMBEDDING_MODEL,
        )
    except ImportError:
        log.warning("OllamaEmbeddingFunction not available in this ChromaDB version, using default")
        return None
    except Exception:  # broad: intentional
        return None


_embedding_fn = _get_embedding_function()

# ---------------------------------------------------------------------------
# Retention / compaction policy
# ---------------------------------------------------------------------------

_RETENTION_PROTECT_SECONDS = {
    "short": 0,
    "standard": 6 * 3600,
    "long": 24 * 3600,
}

# ---------------------------------------------------------------------------
# Recall-guard configuration
# ---------------------------------------------------------------------------

_RECALL_GUARD_MIN_SIMILARITY = float(os.getenv("RECALL_GUARD_MIN_SIMILARITY", "0.78"))
_RECALL_DOMAIN_DIRECTIVE_RE = re.compile(r"\buse\s*:?\s*(sports|wwe)\b", re.IGNORECASE)
_RECALL_DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
    "sports": (
        "sports",
        "game",
        "games",
        "matchup",
        "schedule",
        "scores",
        "team",
        "teams",
        "league",
        "espn",
        "ncaa",
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "mls",
        "lacrosse",
    ),
    "wwe": (
        "wwe",
        "wrestling",
        "raw",
        "smackdown",
        "nxt",
        "wrestlemania",
        "pay-per-view",
        "ppv",
        "premium live event",
    ),
}

# ---------------------------------------------------------------------------
# Recall-guard notes state
# ---------------------------------------------------------------------------

_last_recall_guard_notes: list[str] = []


def consume_recall_guard_notes() -> list[str]:
    """Return and clear recall-guard notes from the last recall_for_context call."""
    global _last_recall_guard_notes
    notes = list(_last_recall_guard_notes)
    _last_recall_guard_notes = []
    return notes


def _set_recall_guard_notes(notes: list[str]) -> None:
    global _last_recall_guard_notes
    _last_recall_guard_notes = list(notes)
