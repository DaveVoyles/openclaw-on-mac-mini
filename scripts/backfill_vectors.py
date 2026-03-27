#!/usr/bin/env python3
"""
Backfill existing QMD memories and thread summaries into ChromaDB.

Run once after deploying Phase 13A to index existing data:
    docker exec openclaw python scripts/backfill_vectors.py

Safe to re-run — uses upsert (idempotent).
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Ensure src/ is on the path (for imports when running inside the container)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("backfill")


async def backfill_qmd():
    """Index all QMD facts into the memories collection."""
    from vector_store import add_memory

    qmd_path = Path(os.getenv("QMD_MEMORY_FILE", "/memory/qmd.json"))
    if not qmd_path.exists():
        log.info("No QMD file at %s — skipping", qmd_path)
        return 0

    data = json.loads(qmd_path.read_text())
    count = 0
    for i, entry in enumerate(data):
        content = entry.get("content", "")
        tags = entry.get("tags", [])
        if not content.strip():
            continue
        await add_memory(f"qmd_{i}", content, tags)
        count += 1
        if count % 50 == 0:
            log.info("  … indexed %d QMD facts", count)

    log.info("✅ Indexed %d QMD facts into memories collection", count)
    return count


async def backfill_summaries():
    """Index all session summaries into the conversations collection."""
    from vector_store import add_conversation_summary

    summaries_dir = Path("/memory/summaries")
    if not summaries_dir.exists():
        log.info("No summaries directory — skipping")
        return 0

    count = 0
    for f in summaries_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            summary = data.get("summary", "")
            user_id = data.get("user_id", 0)
            user_name = data.get("user_name", "unknown")
            if not summary.strip():
                continue
            await add_conversation_summary(user_id, f"session_{user_name}", summary)
            count += 1
        except Exception as e:
            log.warning("Failed to index %s: %s", f.name, e)

    log.info("✅ Indexed %d session summaries into conversations collection", count)
    return count


async def backfill_threads():
    """Index saved thread content into the conversations collection."""
    from vector_store import add_document, CONVERSATIONS_COLLECTION

    threads_dir = Path("/memory/threads")
    if not threads_dir.exists():
        log.info("No threads directory — skipping")
        return 0

    count = 0
    for f in threads_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            name = data.get("name", f.stem)
            history = data.get("history", [])
            if not history:
                continue

            # Combine all messages into a single text for embedding
            text_parts = []
            for msg in history[-20:]:  # Last 20 messages
                role = msg.get("role", "user")
                parts = msg.get("parts", [])
                content = " ".join(str(p) for p in parts)
                text_parts.append(f"{role}: {content}")

            combined = "\n".join(text_parts)
            if len(combined) < 20:
                continue

            await add_document(
                CONVERSATIONS_COLLECTION,
                doc_id=f"thread_{f.stem}",
                text=combined,
                metadata={
                    "type": "thread",
                    "thread_name": name,
                    "message_count": str(len(history)),
                },
            )
            count += 1
        except Exception as e:
            log.warning("Failed to index thread %s: %s", f.name, e)

    log.info("✅ Indexed %d saved threads into conversations collection", count)
    return count


async def main():
    log.info("🔄 Starting ChromaDB backfill…")

    qmd_count = await backfill_qmd()
    summary_count = await backfill_summaries()
    thread_count = await backfill_threads()

    total = qmd_count + summary_count + thread_count
    log.info("🎉 Backfill complete: %d total documents indexed", total)

    # Print stats
    from vector_store import get_stats
    stats = await get_stats()
    for name, info in stats.items():
        log.info("  📊 %s: %d documents", name, info["count"])


if __name__ == "__main__":
    asyncio.run(main())
