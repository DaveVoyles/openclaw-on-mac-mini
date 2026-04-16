"""
OpenClaw QMD (Memory) Skill — Phase 5
Implements 'Quick Memory Discovery' logic inspired by Tobi Lutke's QMD.
Allows the bot to store and retrieve long-term facts in a persistent vector-like store.
"""

import asyncio
import datetime
import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from utils import atomic_write

log = logging.getLogger("openclaw.qmd")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MEMORY_FILE = Path(os.getenv("QMD_MEMORY_FILE", "/memory/qmd.json"))
MAX_MEMORY_ENTRIES = 5000


class QMDMemory:
    """Simple JSON-based long-term memory store."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._memory = self._load()

    def _load(self) -> List[dict]:
        if MEMORY_FILE.exists():
            try:
                return json.loads(MEMORY_FILE.read_text())
            except (json.JSONDecodeError, OSError, ValueError) as e:
                log.error("Failed to load QMD memory: %s", e)
        return []

    def _save(self):
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write(MEMORY_FILE, json.dumps(self._memory, indent=2))
        except OSError as e:
            log.error("Failed to save QMD memory: %s", e)

    async def add(self, content: str, tags: List[str] = None):
        """Add a new fact to memory (deduplicates recent entries, caps at MAX_MEMORY_ENTRIES)."""
        async with self._lock:
            # Dedup: skip if identical content exists in last 100 entries
            if any(m["content"] == content for m in self._memory[-100:]):
                return
            self._memory.append({
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "content": content,
                "tags": tags or [],
            })
            # Evict oldest entries if over limit
            if len(self._memory) > MAX_MEMORY_ENTRIES:
                self._memory = self._memory[-MAX_MEMORY_ENTRIES:]
            self._save()

    async def search(self, query: str) -> str:
        """Search memory for keywords in content or tags."""
        async with self._lock:
            query_lower = query.lower()
            results = [
                m["content"] for m in self._memory
                if query_lower in m["content"].lower() or any(query_lower in t.lower() for t in m["tags"])
            ]
        if not results:
            return "No matching memories found."

        # Format results with bullets
        return "\n".join([f"• {r}" for r in results[:10]])

    async def list_all(self) -> str:
        """List all stored facts."""
        async with self._lock:
            if not self._memory:
                return "Memory is empty."
            return "\n".join([f"• [{m['ts'][:10]}] {m['content']}" for m in self._memory])


qmd_store = QMDMemory()

# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def remember_fact(content: str, tags: Optional[str] = "", source: str = "user-explicit") -> str:
    """Store a fact in long-term memory with intelligent routing (Phase 14D).

    Routes facts to the most appropriate store based on content:
      - Personal preferences → user_profile
      - Operational corrections → rules_engine
      - General facts → QMD + ChromaDB
      - Contacts → QMD with 'contact' tag + ChromaDB
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    # ── Knowledge routing: classify and route (Phase 14D) ──
    routed = False
    try:
        route = _classify_fact(content)
        if route == "preference":
            from user_profile import sync_profile_to_vectors, update_preference
            # Try to parse "key = value" or "key: value" patterns
            for sep in ("=", ":"):
                if sep in content:
                    k, v = content.split(sep, 1)
                    update_preference(k.strip(), v.strip())
                    try:
                        await sync_profile_to_vectors()
                    except Exception as exc:  # broad: intentional — vector sync can fail in many ways
                        log.debug("Profile vector sync failed: %s", exc)
                    routed = True
                    break
            if not routed:
                from user_profile import add_context_note
                add_context_note(content)
                routed = True
            log.info("Routed to user_profile: %s", content[:80])
        elif route == "rule":
            from rules_engine import add_rule
            await add_rule(content)
            routed = True
            log.info("Routed to rules_engine: %s", content[:80])
    except Exception as e:  # broad: intentional — knowledge routing spans imports + multiple backends
        log.debug("Knowledge routing failed (falling back to QMD): %s", e)

    # Always store in QMD (primary store) regardless of routing
    await qmd_store.add(content, tag_list)
    # Also embed into ChromaDB for semantic search
    try:
        import vector_store
        fact_id = str(int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000))
        await vector_store.add_memory(fact_id, content, tag_list, source=source)
    except Exception as e:  # broad: intentional — vector store can fail in many ways
        log.debug("Vector embed failed (non-critical): %s", e)

    suffix = ""
    if routed:
        suffix = " (also routed to specialized store)"
    return f"✅ Remembered: {content}{suffix}"


def _classify_fact(content: str) -> str:
    """Lightweight classification of a fact for routing.

    Returns: 'preference', 'rule', or 'general' (default).
    Uses keyword heuristics — no LLM call for speed.
    """
    lower = content.lower()
    # Preference indicators
    pref_signals = ["i prefer", "i like", "i want", "my timezone", "my favorite",
                    "i use", "i always", "i never", "default to", "set my"]
    if any(s in lower for s in pref_signals):
        return "preference"
    # Rule indicators
    rule_signals = ["don't", "do not", "always ", "never ", "you should",
                    "you must", "stop ", "remember to", "make sure"]
    if any(s in lower for s in rule_signals):
        return "rule"
    return "general"


async def recall_fact(query: str) -> str:
    """Search long-term memory using keyword match (QMD) + semantic search (ChromaDB).

    Results from both sources are merged and deduplicated.
    """
    # Keyword search (existing QMD)
    keyword_result = await qmd_store.search(query)
    keyword_hits = []
    if keyword_result and keyword_result != "No matching memories found.":
        keyword_hits = [line.lstrip("• ") for line in keyword_result.split("\n") if line.strip()]

    # Semantic search (ChromaDB)
    semantic_hits = []
    try:
        import vector_store
        results = await vector_store.search(
            vector_store.MEMORIES_COLLECTION, query, top_k=10
        )
        semantic_hits = [r["text"] for r in results]
    except Exception as e:  # broad: intentional — vector store can fail in many ways
        log.debug("Vector search failed (non-critical): %s", e)

    # Merge and deduplicate (keyword matches first, then semantic-only)
    seen = set()
    merged = []
    for hit in keyword_hits + semantic_hits:
        normalized = hit.strip()[:200]
        if normalized not in seen:
            seen.add(normalized)
            merged.append(hit)

    if not merged:
        return "No matching memories found."

    return "\n".join([f"• {r}" for r in merged[:10]])


async def list_memories() -> str:
    """List all entries in long-term memory (QMD)."""
    return await qmd_store.list_all()
