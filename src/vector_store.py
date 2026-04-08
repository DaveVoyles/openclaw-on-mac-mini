"""Vector store — thin re-export hub for backward compatibility.

All implementation lives in focused sub-modules:
  - vector_store_config     : constants, embedding fn, recall-guard state
  - vector_store_scope      : scope resolution and metadata helpers
  - vector_store_compaction : compaction, retention, decay, access tracking
  - vector_store_client     : ChromaDB client, lazy singleton, core CRUD
  - vector_store_memory     : high-level memory operations
"""

import time  # noqa: F401 — kept so patch("vector_store.time.time", ...) still works in tests

# ruff: noqa: F401 — re-exports for backward compatibility

from vector_store_config import (
    CHROMA_DIR,
    CONVERSATIONS_COLLECTION,
    DEFAULT_TOP_K,
    EMBEDDING_MODEL,
    MEMORIES_COLLECTION,
    OLLAMA_EMBED_URL,
    RESEARCH_COLLECTION,
    SIMILARITY_THRESHOLD,
    _RECALL_DOMAIN_DIRECTIVE_RE,
    _RECALL_DOMAIN_TERMS,
    _RECALL_GUARD_MIN_SIMILARITY,
    _RETENTION_PROTECT_SECONDS,
    _embedding_fn,
    _get_embedding_function,
    _last_recall_guard_notes,
    _set_recall_guard_notes,
    consume_recall_guard_notes,
)

from vector_store_scope import (
    _allow_fallback_result,
    _combine_scope_where,
    _extract_explicit_recall_domains,
    _infer_recall_domains,
    _inject_scope_metadata,
    _is_legacy_metadata,
    _normalize_scope_id,
    _resolve_scope,
)

from vector_store_compaction import (
    _compact_scope_if_needed,
    _compaction_priority,
    _retention_window_seconds,
    bump_access,
    get_decayed_documents,
    mark_decayed,
)

from vector_store_client import (
    _client,
    _collections,
    _get_client,
    _get_collection,
    _lock,
    add_document,
    delete_document,
    get_stats,
    search,
    search_all,
    search_safe,
)

from vector_store_memory import (
    add_conversation_summary,
    add_memory,
    add_memory_deduped,
    add_research_report,
    clear_scoped_memory,
    get_scoped_memory_summary,
    recall,
    recall_for_context,
)
