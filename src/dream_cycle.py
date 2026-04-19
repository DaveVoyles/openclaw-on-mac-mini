"""
OpenClaw Dream Cycle — Auto-Dream Memory Consolidation Engine

Three-phase dream cycle:
  1. Collect    — gather raw data from all memory backends
  2. Consolidate — deduplicate, index, and link memories
  3. Evaluate   — score importance, archive stale entries, compute health
"""

import asyncio
import datetime
import json
import logging
import math
import time
from pathlib import Path
from typing import Callable, Optional

import aiofiles

from utils import atomic_write

log = logging.getLogger(__name__)

MAX_DREAM_SECONDS = 600

CATEGORIES = [
    "identity",
    "user",
    "projects",
    "business",
    "people",
    "strategy",
    "decisions",
    "lessons",
    "environment",
    "threads",
]


# ---------------------------------------------------------------------------
# Dream Cycle Engine
# ---------------------------------------------------------------------------


class DreamCycle:
    """Core dream cycle engine for memory consolidation."""

    def __init__(self, data_dir: Path = Path("data/dream")):
        self.data_dir = data_dir
        self.index_path = data_dir / "index.json"
        self.memory_path = data_dir / "MEMORY.md"
        self.dream_log_path = data_dir / "dream-log.md"
        self.archive_path = data_dir / "archive.md"
        self.procedures_path = data_dir / "procedures.md"

    async def run(self, on_progress: Optional[Callable] = None) -> str:
        """Run a complete dream cycle. Returns the dream report."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with asyncio.timeout(MAX_DREAM_SECONDS):
                return await self._run_phases(on_progress)
        except asyncio.TimeoutError:
            log.warning("Dream cycle exceeded %ds timeout — aborting", MAX_DREAM_SECONDS)
            return f"⚠️ Dream cycle exceeded {MAX_DREAM_SECONDS}s hard timeout — aborted."

    async def _run_phases(self, on_progress: Optional[Callable] = None) -> str:
        """Execute the three dream phases (called within timeout context)."""
        start = time.monotonic()

        async def _progress(msg: str):
            log.info("Dream: %s", msg)
            if on_progress:
                try:
                    await on_progress(msg)
                except Exception:  # broad: intentional — callback can raise anything
                    pass

        # ── Phase 1: Collect ──────────────────────────────────────────
        await _progress("Phase 1/3: Collecting raw material…")
        raw = await self._collect()

        # ── Phase 2: Consolidate ──────────────────────────────────────
        await _progress(f"Phase 2/3: Consolidating {len(raw)} items…")
        index = _load_index(self.index_path)
        changes = await self._consolidate(index, raw)
        _save_index(self.index_path, index)

        # ── Phase 3: Evaluate ─────────────────────────────────────────
        await _progress("Phase 3/3: Evaluating health & generating insights…")
        report = await self._evaluate(index, changes)

        _save_index(self.index_path, index)
        await self._append_dream_log(report)

        log.info("Dream cycle complete in %.1fs", time.monotonic() - start)
        return report

    # ------------------------------------------------------------------
    # Phase 1: Collect
    # ------------------------------------------------------------------

    async def _collect(self) -> list[dict]:
        """Gather raw material from all memory backends."""
        collectors = [
            ("ChromaDB memories", self._collect_chromadb_memories()),
            ("ChromaDB conversations", self._collect_chromadb_conversations()),
            ("QMD facts", self._collect_qmd_facts()),
        ]
        # Sync collectors
        sync_collectors = [
            ("User profile", self._collect_user_profile),
            ("Audit logs", self._collect_audit_logs),
        ]

        items: list[dict] = []

        # Run async collectors concurrently
        results = await asyncio.gather(*(coro for _, coro in collectors), return_exceptions=True)
        for (label, _), result in zip(collectors, results):
            if isinstance(result, Exception):
                log.warning("Collect %s failed: %s", label, result)
            else:
                items.extend(result)

        # Run sync collectors
        for label, fn in sync_collectors:
            try:
                items.extend(fn())
            except Exception as e:  # broad: intentional — collector plugins can raise anything
                log.warning("Collect %s failed: %s", label, e)

        log.info("Collected %d raw items", len(items))
        return items

    async def _collect_chromadb_memories(self) -> list[dict]:
        """Pull all documents from the memories collection."""
        import vector_store

        col = vector_store._get_collection(vector_store.MEMORIES_COLLECTION)
        if col.count() == 0:
            return []

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, lambda: col.get(include=["metadatas", "documents"]))

        items = []
        for i, doc_id in enumerate(results.get("ids", [])):
            text = (results.get("documents") or [""])[i] if results.get("documents") else ""
            meta = (results.get("metadatas") or [{}])[i] if results.get("metadatas") else {}
            if not text.strip():
                continue
            items.append(
                {
                    "source": "chromadb:memories",
                    "source_id": doc_id,
                    "text": text,
                    "metadata": meta,
                    "category": _classify_category(text, meta),
                    "type": _classify_type(text, meta),
                }
            )
        return items

    async def _collect_chromadb_conversations(self) -> list[dict]:
        """Pull recent conversation summaries (last 7 days)."""
        import vector_store

        week_ago = time.time() - 7 * 86400
        results = await vector_store.search(
            vector_store.CONVERSATIONS_COLLECTION,
            "session summary conversation recap",
            top_k=50,
            threshold=0.1,
            track_access=False,
        )

        items = []
        for r in results:
            added = r.get("metadata", {}).get("added_at", 0)
            if added > week_ago or not added:
                items.append(
                    {
                        "source": "chromadb:conversations",
                        "source_id": r["id"],
                        "text": r["text"],
                        "metadata": r.get("metadata", {}),
                        "category": "threads",
                        "type": "fact",
                    }
                )
        return items

    async def _collect_qmd_facts(self) -> list[dict]:
        """Pull all facts from the QMD store."""
        from qmd import qmd_store

        items = []
        async with qmd_store._lock:
            for entry in qmd_store._memory:
                content = entry.get("content", "")
                items.append(
                    {
                        "source": "qmd",
                        "source_id": f"qmd_{hash(content) % 100000}",
                        "text": content,
                        "metadata": {"tags": entry.get("tags", []), "ts": entry.get("ts", "")},
                        "category": _classify_category(content, {}),
                        "type": "fact",
                    }
                )
        return items

    def _collect_user_profile(self) -> list[dict]:
        """Extract profile data as memory items."""
        from user_profile import load_profile

        profile = load_profile()
        items: list[dict] = []

        for k, v in (profile.get("preferences") or {}).items():
            items.append(
                {
                    "source": "user_profile",
                    "source_id": f"pref_{k}",
                    "text": f"User preference: {k} = {v}",
                    "metadata": {"type": "preference"},
                    "category": "user",
                    "type": "preference",
                }
            )

        if profile.get("interests"):
            items.append(
                {
                    "source": "user_profile",
                    "source_id": "interests",
                    "text": f"User interests: {', '.join(profile['interests'])}",
                    "metadata": {"type": "interest"},
                    "category": "user",
                    "type": "preference",
                }
            )

        for i, note in enumerate(profile.get("context_notes") or []):
            items.append(
                {
                    "source": "user_profile",
                    "source_id": f"note_{i}",
                    "text": note,
                    "metadata": {"type": "context_note"},
                    "category": "user",
                    "type": "fact",
                }
            )

        return items

    def _collect_audit_logs(self) -> list[dict]:
        """Parse recent JSONL audit logs (last 7 days)."""
        audit_dir = self.data_dir.parent / "audit"
        if not audit_dir.is_dir():
            return []

        cutoff = datetime.date.today() - datetime.timedelta(days=7)
        items: list[dict] = []

        for f in sorted(audit_dir.glob("*.jsonl")):
            try:
                file_date = datetime.date.fromisoformat(f.stem)
                if file_date < cutoff:
                    continue
            except ValueError:
                continue

            try:
                for line in f.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    action = record.get("action", "")
                    detail = record.get("detail", "")
                    if action and detail:
                        items.append(
                            {
                                "source": "audit",
                                "source_id": f"audit_{f.stem}_{action}_{hash(detail) % 10000}",
                                "text": f"[{action}] {detail}",
                                "metadata": record,
                                "category": _classify_category(f"[{action}] {detail}", {}),
                                "type": "fact",
                            }
                        )
            except OSError as exc:
                log.debug("Failed to read audit file %s: %s", f, exc)

        return items

    # ------------------------------------------------------------------
    # Phase 2: Consolidate
    # ------------------------------------------------------------------

    async def _consolidate(self, index: dict, raw: list[dict]) -> dict:
        """Deduplicate, index, and link raw items into the master index."""
        changes = {"added": 0, "updated": 0, "skipped": 0, "procedures": 0}
        today = datetime.date.today().isoformat()

        for item in raw:
            text = item.get("text", "")
            if len(text.strip()) < 5:
                changes["skipped"] += 1
                continue

            # Procedural items → procedures.md
            if _is_procedural(text):
                await self._append_procedure(text)
                changes["procedures"] += 1
                continue

            # Exact source-ID match → bump existing
            existing = _find_by_source_id(index, item["source_id"])
            if existing:
                existing["lastReferenced"] = today
                existing["referenceCount"] = existing.get("referenceCount", 1) + 1
                changes["updated"] += 1
                continue

            # Semantic duplicate → bump matched entry
            dup_id = await _check_semantic_duplicate(text)
            if dup_id:
                for entry in index["entries"]:
                    if entry.get("id") == dup_id:
                        entry["lastReferenced"] = today
                        entry["referenceCount"] = entry.get("referenceCount", 1) + 1
                        break
                changes["skipped"] += 1
                continue

            # New entry
            entry_id = _next_id(index)
            entry = {
                "id": entry_id,
                "sourceId": item["source_id"],
                "source": item["source"],
                "text": text[:500],
                "category": item.get("category", "general"),
                "type": item.get("type", "fact"),
                "tags": _extract_tags(text, item.get("metadata", {})),
                "created": today,
                "lastReferenced": today,
                "referenceCount": 1,
                "importance": 0.5,
                "relations": [],
            }
            index["entries"].append(entry)
            await self._append_memory_md(entry)
            changes["added"] += 1

        _build_relations(index)
        return changes

    async def _append_procedure(self, text: str) -> None:
        """Append a procedural item to procedures.md if not already present."""
        self.procedures_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.procedures_path.exists():
            self.procedures_path.write_text("# Procedures — How I Do Things\n\n---\n\n")
        existing = self.procedures_path.read_text()
        if text[:80] in existing:
            return
        async with aiofiles.open(self.procedures_path, "a") as f:
            await f.write(f"\n- {text[:300]}\n")

    async def _append_memory_md(self, entry: dict) -> None:
        """Append a new entry to MEMORY.md."""
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.memory_path.exists():
            self.memory_path.write_text("# MEMORY.md — Long-Term Memory\n\n---\n\n")

        emoji = {
            "identity": "🧠",
            "user": "👤",
            "projects": "🏗️",
            "business": "💰",
            "people": "👥",
            "strategy": "🎯",
            "decisions": "📌",
            "lessons": "💡",
            "environment": "🔧",
            "threads": "🌊",
        }.get(entry.get("category", ""), "📝")

        async with aiofiles.open(self.memory_path, "a") as f:
            await f.write(f"\n### {emoji} {entry['id']} — {entry['category']}\n")
            await f.write(f"- {entry['text'][:300]}\n")
            tags = ", ".join(entry.get("tags", [])) or "none"
            await f.write(f"- _Added: {entry['created']} | Tags: {tags}_\n")

    # ------------------------------------------------------------------
    # Phase 3: Evaluate
    # ------------------------------------------------------------------

    async def _evaluate(self, index: dict, changes: dict) -> str:
        """Score, archive, compute health, generate insights."""
        today = datetime.date.today()
        today_str = today.isoformat()
        archived_count = 0

        # Score every entry
        for entry in index["entries"]:
            entry["importance"] = _compute_importance(entry, today)

        # Archive stale entries (>90 days unreferenced AND importance < 0.3)
        keep: list[dict] = []
        for entry in index["entries"]:
            last_ref = entry.get("lastReferenced", entry.get("created", today_str))
            try:
                days_since = (today - datetime.date.fromisoformat(last_ref)).days
            except (ValueError, TypeError):
                days_since = 0

            if days_since > 90 and entry["importance"] < 0.3:
                self._archive_entry(entry)
                archived_count += 1
            else:
                keep.append(entry)
        index["entries"] = keep

        # Health
        health = _compute_health(index, self.memory_path)
        index["stats"]["healthScore"] = health["overall"]
        index["stats"]["healthMetrics"] = health["metrics"]
        index["stats"]["totalEntries"] = len(index["entries"])
        index["stats"]["avgImportance"] = round(
            (sum(e["importance"] for e in index["entries"]) / max(len(index["entries"]), 1)),
            3,
        )

        # Insights
        insights = await _generate_insights(index, changes)
        index["stats"]["insights"] = insights

        # Health history (rolling 30)
        history = index["stats"].setdefault("healthHistory", [])
        history.append({"date": today_str, "score": health["overall"], **health["metrics"]})
        index["stats"]["healthHistory"] = history[-30:]

        index["lastDream"] = today_str
        index["stats"]["lastPruned"] = today_str if archived_count else index["stats"].get("lastPruned")

        return _build_report(index, changes, health, insights, archived_count)

    async def _archive_entry(self, entry: dict) -> None:
        """Compress entry to one line and append to archive.md."""
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.archive_path.exists():
            self.archive_path.write_text(
                "# Memory Archive\n\n_Compressed entries that fell below importance threshold._\n\n---\n\n"
            )
        line = (
            f"- `{entry['id']}` ({entry.get('category', '?')}) "
            f"{entry['text'][:120].replace(chr(10), ' ')} "
            f"[imp={entry.get('importance', 0):.2f}, refs={entry.get('referenceCount', 0)}]\n"
        )
        async with aiofiles.open(self.archive_path, "a") as f:
            await f.write(line)

    async def _append_dream_log(self, report: str) -> None:
        """Append a dream report to dream-log.md."""
        self.dream_log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.dream_log_path.exists():
            self.dream_log_path.write_text("# Dream Log\n\n_Auto-Dream consolidation reports. Append-only._\n\n---\n\n")
        async with aiofiles.open(self.dream_log_path, "a") as f:
            await f.write(f"\n{report}\n")


# ---------------------------------------------------------------------------
# Index I/O
# ---------------------------------------------------------------------------

_DEFAULT_INDEX = {
    "version": "3.0",
    "lastDream": None,
    "config": {"notificationLevel": "summary", "instanceName": "openclaw"},
    "entries": [],
    "stats": {
        "totalEntries": 0,
        "avgImportance": 0,
        "lastPruned": None,
        "healthScore": 0,
        "healthMetrics": {
            "freshness": 0,
            "coverage": 0,
            "coherence": 0,
            "efficiency": 0,
            "reachability": 0,
        },
        "insights": [],
        "healthHistory": [],
    },
}


def _load_index(path: Path) -> dict:
    """Load index.json, creating from template if missing or corrupt."""
    if path.exists():
        try:
            data = json.loads(path.read_text())
            data.setdefault("entries", [])
            data.setdefault("stats", {})
            data["stats"].setdefault("healthMetrics", {})
            data["stats"].setdefault("insights", [])
            data["stats"].setdefault("healthHistory", [])
            return data
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            log.warning("Index corrupt (%s), backing up", exc)
            try:
                path.rename(path.with_suffix(".json.bak"))
            except OSError:
                pass

    return json.loads(json.dumps(_DEFAULT_INDEX))  # deep copy


def _save_index(path: Path, index: dict) -> None:
    """Atomically write index.json with backup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            path.with_suffix(".json.bak").write_text(path.read_text())
        except OSError:
            pass
    atomic_write(path, json.dumps(index, indent=2))


# ---------------------------------------------------------------------------
# Importance scoring
# ---------------------------------------------------------------------------

_BASE_WEIGHTS = {
    "decision": 0.8,
    "lesson": 0.7,
    "preference": 0.6,
    "fact": 0.5,
    "thread": 0.4,
}


def _compute_importance(entry: dict, today: datetime.date) -> float:
    """importance = clamp(base_weight × recency × reference_boost, 0, 1)

    recency         = max(0.1, 1.0 − days / 180)
    reference_boost = max(1.0, log₂(referenceCount + 1))
    """
    base = _BASE_WEIGHTS.get(entry.get("type", "fact"), 0.5)

    last_ref = entry.get("lastReferenced", entry.get("created", today.isoformat()))
    try:
        days = (today - datetime.date.fromisoformat(last_ref)).days
    except (ValueError, TypeError):
        days = 0
    recency = max(0.1, 1.0 - days / 180)

    ref_count = entry.get("referenceCount", 1)
    reference_boost = max(1.0, math.log2(ref_count + 1))

    return round(max(0.0, min(1.0, base * recency * reference_boost)), 3)


# ---------------------------------------------------------------------------
# Health metrics
# ---------------------------------------------------------------------------


def _compute_health(index: dict, memory_path: Path | None = None) -> dict:
    """Compute 5-metric health score."""
    entries = index.get("entries", [])
    total = max(len(entries), 1)
    today = datetime.date.today()

    # 1. Freshness — entries referenced in last 30 days / total
    fresh = 0
    for e in entries:
        try:
            d = (today - datetime.date.fromisoformat(e.get("lastReferenced", e.get("created", "")))).days
            if d <= 30:
                fresh += 1
        except (ValueError, TypeError):
            pass
    freshness = fresh / total

    # 2. Coverage — categories updated in last 14 days / 10
    recent_cats: set[str] = set()
    for e in entries:
        try:
            d = (today - datetime.date.fromisoformat(e.get("lastReferenced", e.get("created", "")))).days
            if d <= 14:
                recent_cats.add(e.get("category", ""))
        except (ValueError, TypeError):
            pass
    coverage = min(1.0, len(recent_cats) / 10)

    # 3. Coherence — entries with relations / total
    coherence = sum(1 for e in entries if e.get("relations")) / total

    # 4. Efficiency — max(0, 1 − lines / 500)
    line_count = 0
    if memory_path and memory_path.exists():
        try:
            line_count = len(memory_path.read_text().splitlines())
        except OSError:
            pass
    efficiency = max(0.0, 1.0 - line_count / 500)

    # 5. Reachability — union-find connected components
    reachability = _compute_reachability(entries)

    metrics = {
        "freshness": round(freshness, 3),
        "coverage": round(coverage, 3),
        "coherence": round(coherence, 3),
        "efficiency": round(efficiency, 3),
        "reachability": round(reachability, 3),
    }
    return {"overall": round(sum(metrics.values()) / 5, 3), "metrics": metrics}


def _compute_reachability(entries: list[dict]) -> float:
    """Union-find to measure graph connectivity.

    Returns 1.0 for a fully connected graph, 1/N for N isolated components.
    """
    if not entries:
        return 1.0

    ids = {e["id"] for e in entries}
    parent: dict[str, str] = {eid: eid for eid in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for e in entries:
        for rel in e.get("relations", []):
            if rel in ids:
                union(e["id"], rel)

    components = len({find(eid) for eid in ids})
    return round(1.0 / max(components, 1), 3)


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


async def _generate_insights(index: dict, changes: dict) -> list[str]:
    """Use LLM to generate 1-3 pattern/gap/trend insights."""
    entries = index.get("entries", [])
    if not entries:
        return ["Memory is empty — first dream cycle will populate entries."]

    # Build compact state summary for the prompt
    cats: dict[str, int] = {}
    for e in entries:
        cat = e.get("category", "other")
        cats[cat] = cats.get(cat, 0) + 1
    cat_summary = ", ".join(f"{k}: {v}" for k, v in sorted(cats.items()))

    recent = sorted(entries, key=lambda e: e.get("lastReferenced", ""), reverse=True)[:10]
    recent_text = "\n".join(f"- [{e['id']}] ({e['category']}) {e['text'][:100]}" for e in recent)

    prompt = (
        "You are a memory consolidation system. Analyze this memory state and produce "
        "1-3 brief insights (pattern connections, gaps, trends). One line each.\n\n"
        f"Total entries: {len(entries)}\n"
        f"Categories: {cat_summary}\n"
        f"Changes this cycle: +{changes['added']} new, ↻{changes['updated']} updated, "
        f"⊘{changes['skipped']} deduped\n"
        f"Health: {index['stats'].get('healthScore', 0):.0%}\n\n"
        f"Recent entries:\n{recent_text}\n\n"
        "Return only the insights as a bullet list (- insight)."
    )

    try:
        from llm import chat

        response, _, _ = await chat(prompt, model_preference="auto")
        insights = []
        for line in response.strip().splitlines():
            line = line.strip().lstrip("-•*").strip()
            if line and len(line) > 10:
                insights.append(line)
        if insights:
            return insights[:3]
    except Exception as exc:  # broad: intentional — LLM insight generation can fail in many ways
        log.warning("LLM insight generation failed: %s", exc)

    return _fallback_insights(index, changes)


def _fallback_insights(index: dict, changes: dict) -> list[str]:
    """Deterministic insights when LLM is unavailable."""
    insights: list[str] = []
    entries = index.get("entries", [])

    if changes["added"] > 5:
        insights.append(f"High activity: {changes['added']} new memories consolidated.")
    if changes["skipped"] > changes["added"]:
        insights.append("Memory is stabilizing — more duplicates than new entries.")

    cats = {e.get("category") for e in entries}
    missing = [c for c in CATEGORIES if c not in cats]
    if missing:
        insights.append(f"Gap: no entries in {', '.join(missing[:3])} categories.")

    return insights[:3] or ["Dream cycle completed normally."]


# ---------------------------------------------------------------------------
# Consolidation helpers
# ---------------------------------------------------------------------------


async def _check_semantic_duplicate(text: str) -> Optional[str]:
    """Return matching memory ID if text is a semantic duplicate (similarity > 0.9)."""
    try:
        import vector_store

        results = await vector_store.search(
            vector_store.MEMORIES_COLLECTION,
            text,
            top_k=1,
            threshold=0.9,
            track_access=False,
        )
        if results:
            return results[0]["id"]
    except Exception as exc:  # broad: intentional — vector store can fail in many ways
        log.debug("Semantic dedup check failed: %s", exc)
    return None


def _find_by_source_id(index: dict, source_id: str) -> Optional[dict]:
    for entry in index["entries"]:
        if entry.get("sourceId") == source_id:
            return entry
    return None


def _next_id(index: dict) -> str:
    """Generate next mem_NNN ID."""
    max_n = 0
    for entry in index["entries"]:
        eid = entry.get("id", "")
        if eid.startswith("mem_"):
            try:
                max_n = max(max_n, int(eid.split("_")[1]))
            except (IndexError, ValueError):
                pass
    return f"mem_{max_n + 1:03d}"


def _build_relations(index: dict) -> None:
    """Link entries that share tags or come from the same source."""
    tag_map: dict[str, list[str]] = {}
    source_map: dict[str, list[str]] = {}

    for e in index["entries"]:
        eid = e["id"]
        for tag in e.get("tags", []):
            tag_map.setdefault(tag, []).append(eid)
        src = e.get("source", "")
        if src:
            source_map.setdefault(src, []).append(eid)

    for group in (tag_map, source_map):
        for _, ids in group.items():
            if len(ids) < 2:
                continue
            for eid in ids:
                entry = next((e for e in index["entries"] if e["id"] == eid), None)
                if entry is None:
                    continue
                related = [rid for rid in ids if rid != eid]
                existing = set(entry.get("relations", []))
                entry["relations"] = list(existing | set(related))


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _classify_category(text: str, meta: dict) -> str:
    lower = text.lower()
    rules = [
        (["openclaw", "bot", "discord", "skill"], "identity"),
        (["dave", "user", "prefer", "timezone"], "user"),
        (["project", "repo", "github", "monstervis"], "projects"),
        (["decided", "decision", "changed", "migrat", "switched"], "decisions"),
        (["lesson", "learned", "mistake", "gotcha", "caveat"], "lessons"),
        (["docker", "container", "nas", "server", "traefik", "ssh"], "environment"),
        (["strategy", "goal", "plan", "roadmap"], "strategy"),
        (["todo", "thread", "open", "pending"], "threads"),
        (["team", "contact", "person", "member"], "people"),
        (["revenue", "cost", "price", "business"], "business"),
    ]
    for keywords, category in rules:
        if any(k in lower for k in keywords):
            return category
    return "general"


def _classify_type(text: str, meta: dict) -> str:
    if meta.get("type") in ("preference", "user_profile"):
        return "preference"
    lower = text.lower()
    if any(w in lower for w in ["decided", "decision", "chose", "switched"]):
        return "decision"
    if any(w in lower for w in ["lesson", "learned", "never", "always"]):
        return "lesson"
    if any(w in lower for w in ["todo", "thread", "open", "pending"]):
        return "thread"
    return "fact"


def _extract_tags(text: str, meta: dict) -> list[str]:
    tags: set[str] = set()
    raw = meta.get("tags", "")
    if isinstance(raw, str):
        tags.update(t.strip() for t in raw.split(",") if t.strip())
    elif isinstance(raw, list):
        tags.update(t for t in raw if t)

    lower = text.lower()
    kw_tags = {
        "docker": "docker",
        "nas": "nas",
        "discord": "discord",
        "gemini": "gemini",
        "chromadb": "chromadb",
        "openclaw": "openclaw",
        "research": "research",
        "plex": "plex",
        "traefik": "traefik",
    }
    for keyword, tag in kw_tags.items():
        if keyword in lower:
            tags.add(tag)
    return list(tags)


def _is_procedural(text: str) -> bool:
    lower = text.lower()
    signals = [
        "how to",
        "steps:",
        "workflow:",
        "procedure:",
        "always do",
        "when you",
        "make sure to",
        "first, then",
        "run the command",
    ]
    return any(s in lower for s in signals)


def _build_report(
    index: dict,
    changes: dict,
    health: dict,
    insights: list[str],
    archived: int,
) -> str:
    today = datetime.date.today().isoformat()
    lines = [
        f"## 🌙 Dream Report — {today}",
        "",
        f"**Entries:** {len(index['entries'])} active | {archived} archived",
        f"**Changes:** +{changes['added']} new, ↻{changes['updated']} updated, "
        f"⊘{changes['skipped']} deduped, 📋{changes['procedures']} procedures",
        f"**Avg importance:** {index['stats'].get('avgImportance', 0):.2f}",
        "",
        "### Health Score",
        f"**Overall: {health['overall']:.0%}**",
    ]
    for metric, value in health["metrics"].items():
        bar = "█" * int(value * 10) + "░" * (10 - int(value * 10))
        lines.append(f"  {metric:14s} {bar} {value:.0%}")

    if insights:
        lines.append("")
        lines.append("### 💡 Insights")
        for insight in insights:
            lines.append(f"- {insight}")

    lines.extend(["", "---"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill entry points
# ---------------------------------------------------------------------------


async def dream_now(on_progress: Optional[Callable] = None) -> str:
    """Run a dream cycle now. Skill entry point."""
    return await DreamCycle().run(on_progress=on_progress)


async def get_memory_health() -> str:
    """Return current memory health score and metrics."""
    cycle = DreamCycle()
    index = _load_index(cycle.index_path)

    if not index.get("entries"):
        return "📊 **Memory Health:** No entries indexed yet. Run a dream cycle first."

    health = _compute_health(index, cycle.memory_path)
    lines = [
        f"📊 **Memory Health: {health['overall']:.0%}**",
        f"  Entries: {len(index['entries'])}",
        f"  Avg importance: {index['stats'].get('avgImportance', 0):.2f}",
        f"  Last dream: {index.get('lastDream', 'never')}",
        "",
    ]
    for metric, value in health["metrics"].items():
        bar = "█" * int(value * 10) + "░" * (10 - int(value * 10))
        lines.append(f"  {metric:14s} {bar} {value:.0%}")

    for i in index["stats"].get("insights", []):
        lines.append(f"  💡 {i}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill exports
# ---------------------------------------------------------------------------

DREAM_SKILLS = {
    "dream_now": dream_now,
    "get_memory_health": get_memory_health,
}
