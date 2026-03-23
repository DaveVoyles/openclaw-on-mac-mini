"""
OpenClaw QMD (Memory) Skill — Phase 5
Implements 'Quick Memory Discovery' logic inspired by Tobi Lutke's QMD.
Allows the bot to store and retrieve long-term facts in a persistent vector-like store.
"""

import datetime
import json
import logging
import os
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("openclaw.qmd")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MEMORY_FILE = Path(os.getenv("QMD_MEMORY_FILE", "/memory/qmd.json"))


class QMDMemory:
    """Simple JSON-based long-term memory store."""

    def __init__(self):
        self._memory = self._load()

    def _load(self) -> List[dict]:
        if MEMORY_FILE.exists():
            try:
                return json.loads(MEMORY_FILE.read_text())
            except Exception as e:
                log.error("Failed to load QMD memory: %s", e)
        return []

    def _save(self):
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            MEMORY_FILE.write_text(json.dumps(self._memory, indent=2))
        except Exception as e:
            log.error("Failed to save QMD memory: %s", e)

    def add(self, content: str, tags: List[str] = None):
        """Add a new fact to memory."""
        self._memory.append({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "content": content,
            "tags": tags or [],
        })
        self._save()

    def search(self, query: str) -> str:
        """Search memory for keywords in content or tags."""
        query = query.lower()
        results = [
            m["content"] for m in self._memory
            if query in m["content"].lower() or any(query in t.lower() for t in m["tags"])
        ]
        if not results:
            return "No matching memories found."

        # Format results with bullets
        return "\n".join([f"• {r}" for r in results[:10]])

    def list_all(self) -> str:
        """List all stored facts."""
        if not self._memory:
            return "Memory is empty."
        return "\n".join([f"• [{m['ts'][:10]}] {m['content']}" for m in self._memory])


qmd_store = QMDMemory()

# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def remember_fact(content: str, tags: Optional[str] = "") -> str:
    """Store a fact in long-term memory (QMD)."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    qmd_store.add(content, tag_list)
    return f"✅ Remembered: {content}"


async def recall_fact(query: str) -> str:
    """Search long-term memory (QMD) for a specific fact or topic."""
    return qmd_store.search(query)


async def list_memories() -> str:
    """List all entries in long-term memory (QMD)."""
    return qmd_store.list_all()
