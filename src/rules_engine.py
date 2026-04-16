"""
OpenClaw Rules Engine — Correction Learning System

Learns behavioral rules from user corrections and injects them into future
prompts.  Inspired by the Genesis ``rules.md`` pattern: every time the user
corrects the bot, a concise one-liner rule is extracted via Gemini and
persisted in two stores:

  1. ``/memory/rules.json``  — human-readable JSON list (backed up by 4AM cron)
  2. ChromaDB ``memories`` collection with ``type: "rule"`` metadata

At inference time, the most relevant rules are retrieved semantically and
prepended to the system prompt so the model never repeats the same mistake.
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("openclaw.rules_engine")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RULES_FILE = Path("/memory/rules.json")
MEMORIES_COLLECTION = "memories"
RULE_SIMILARITY_THRESHOLD = 0.6  # looser than default 0.7

# Correction indicators — checked case-insensitively
_CORRECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)^no[,.\s!]"),
    re.compile(r"(?i)\bthat'?s wrong\b"),
    re.compile(r"(?i)^actually[,.\s]"),
    re.compile(r"(?i)\bI told you\b"),
    re.compile(r"(?i)\bdon'?t do that\b"),
    re.compile(r"(?i)\bstop doing\b"),
    re.compile(r"(?i)\bremember that\b"),
    re.compile(r"(?i)\bI said\b"),
    re.compile(r"(?i)\byou should\b"),
    re.compile(r"(?i)\bI prefer\b"),
    re.compile(r"(?i)\bincorrect\b"),
    re.compile(r"(?i)\byou forgot\b"),
    re.compile(r"(?i)\bwrong\b"),
]


# ---------------------------------------------------------------------------
# File helpers (run blocking I/O in executor)
# ---------------------------------------------------------------------------


async def _load_rules() -> list[dict]:
    """Read the rules JSON file, returning an empty list on any error."""
    def _read():
        if not RULES_FILE.exists():
            return []
        return json.loads(RULES_FILE.read_text(encoding="utf-8"))

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _read)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log.warning("Failed to load %s: %s", RULES_FILE, exc)
        return []


async def _save_rules(rules: list[dict]) -> None:
    """Atomically write the rules list to disk."""
    def _write():
        RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        RULES_FILE.write_text(
            json.dumps(rules, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _write)
        log.debug("Saved %d rules to %s", len(rules), RULES_FILE)
    except OSError as exc:
        log.error("Failed to save rules: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_correction(user_message: str) -> bool:
    """Return True if *user_message* looks like a correction or preference."""
    if not user_message:
        return False
    for pattern in _CORRECTION_PATTERNS:
        if pattern.search(user_message):
            return True
    return False


async def extract_rule(user_message: str, bot_response: str) -> str:
    """Use Gemini to distil a one-liner operational rule from a correction.

    Returns the extracted rule text, or an empty string on failure.
    """
    from llm import chat  # local import to avoid circular dependency

    prompt = (
        "The user corrected the AI. Extract a concise operational rule "
        "(one sentence) that the AI should follow in the future.\n\n"
        f"User said: {user_message}\n"
        f"Bot had said: {bot_response}\n\n"
        "Rule:"
    )
    try:
        response, _, _ = await chat(prompt, model_preference="auto")
        rule = response.strip().strip('"').strip("'")
        log.info("Extracted rule: %s", rule)
        return rule
    except Exception as exc:  # broad: intentional — LLM call can fail in many ways
        log.error("Rule extraction failed: %s", exc)
        return ""


async def add_rule(rule_text: str, source_message: str = "") -> dict:
    """Persist a new learned rule to JSON and ChromaDB.

    Returns the stored rule dict with fields:
      id, rule, source, created_at, access_count
    """
    rule_id = f"rule_{int(time.time() * 1000)}"
    entry = {
        "id": rule_id,
        "rule": rule_text,
        "source": source_message[:500],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "access_count": 0,
    }

    # --- JSON persistence ---
    rules = await _load_rules()
    rules.append(entry)
    await _save_rules(rules)

    # --- ChromaDB embedding (fire-and-forget) ---
    try:
        from vector_store import add_document

        await add_document(
            MEMORIES_COLLECTION,
            doc_id=rule_id,
            text=rule_text,
            metadata={"type": "rule", "source": source_message[:200]},
        )
        log.debug("Embedded rule %s into ChromaDB", rule_id)
    except Exception as exc:  # broad: intentional — vector store can raise many error types
        log.warning("ChromaDB upsert failed for rule %s: %s", rule_id, exc)

    log.info("Added rule %s: %s", rule_id, rule_text)
    return entry


async def get_relevant_rules(
    query: str,
    top_k: int = 5,
) -> list[str]:
    """Retrieve the most relevant learned rules for a given query.

    Uses semantic search against ChromaDB with a looser similarity threshold
    (0.6) so rules surface even for tangentially related topics.
    Returns a list of rule text strings sorted by relevance.
    """
    try:
        from vector_store import search

        results = await search(
            MEMORIES_COLLECTION,
            query,
            top_k=top_k,
            where={"type": "rule"},
            threshold=RULE_SIMILARITY_THRESHOLD,
        )
        return [r["text"] for r in results]
    except Exception as exc:  # broad: intentional — vector store can raise many error types
        log.warning("ChromaDB rule search failed, falling back to JSON: %s", exc)

    # Fallback: return all rules (no ranking) when ChromaDB is unavailable
    rules = await _load_rules()
    return [r["rule"] for r in rules[:top_k]]


async def get_all_rules() -> list[dict]:
    """Return every learned rule from the JSON store."""
    return await _load_rules()


async def delete_rule(rule_id: str) -> bool:
    """Remove a rule from both JSON and ChromaDB. Returns True on success."""
    # --- JSON removal ---
    rules = await _load_rules()
    original_len = len(rules)
    rules = [r for r in rules if r.get("id") != rule_id]
    if len(rules) == original_len:
        log.warning("Rule %s not found in JSON", rule_id)
        return False
    await _save_rules(rules)

    # --- ChromaDB removal ---
    try:
        from vector_store import delete_document

        await delete_document(MEMORIES_COLLECTION, rule_id)
        log.debug("Deleted rule %s from ChromaDB", rule_id)
    except Exception as exc:  # broad: intentional — vector store can raise many error types
        log.warning("ChromaDB delete failed for rule %s: %s", rule_id, exc)

    log.info("Deleted rule %s", rule_id)
    return True
