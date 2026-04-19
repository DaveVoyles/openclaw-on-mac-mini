"""
OpenClaw Fact Extractor — Automatic memory from conversations.
Extracts memorable facts from user messages and stores them in QMD + ChromaDB.
"""

import hashlib
import logging
import re
import time

log = logging.getLogger(__name__)

# Rate limiting: only extract facts every N messages per user
_extraction_counter: dict[int, int] = {}  # user_id → message count
EXTRACT_EVERY_N = 3  # Extract facts every 3rd message
MIN_MESSAGE_LENGTH = 30  # Skip very short messages

# Patterns that indicate trivial/command messages (skip extraction)
_SKIP_PATTERNS = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no|yep|nope|bye|gm|gn|lol|haha)\b"
    r"|^/"  # slash commands
    r"|^\?"  # just a question mark
    r"|^(what|how|why|when|where|who|can you|could you|please|tell me)\b.{0,30}$",  # pure questions with no facts
    re.IGNORECASE,
)


def should_extract(user_id: int, message: str) -> bool:
    """Decide whether to run fact extraction on this message."""
    if len(message.strip()) < MIN_MESSAGE_LENGTH:
        return False
    if _SKIP_PATTERNS.match(message.strip()):
        return False

    # Rate limit per user
    count = _extraction_counter.get(user_id, 0) + 1
    _extraction_counter[user_id] = count

    if count % EXTRACT_EVERY_N != 0:
        return False

    return True


async def extract_and_store_facts(
    user_message: str,
    bot_response: str,
    user_id: int,
) -> list[str]:
    """Extract memorable facts from a conversation turn and store them.

    Returns list of extracted facts (for logging/debugging).
    """
    from llm_client import quick_generate

    extraction_prompt = (
        "You are a memory extraction system. From the following conversation turn, "
        "extract any NEW facts, preferences, personal details, or important information "
        "worth remembering long-term about the user.\n\n"
        "Rules:\n"
        "- Only extract CONCRETE facts (names, dates, locations, preferences, decisions)\n"
        "- Do NOT extract opinions, greetings, or transient information\n"
        "- Do NOT extract facts about the AI assistant itself\n"
        "- Return one fact per line, each as a brief statement\n"
        "- If there are NO memorable facts, reply with exactly: NONE\n\n"
        f"USER: {user_message}\n"
        f"ASSISTANT: {bot_response[:500]}\n\n"
        "Extracted facts (one per line):"
    )

    try:
        from llm_client import quick_generate

        text = await quick_generate(extraction_prompt, max_tokens=300, temperature=0.1)
        if not text or text.upper() == "NONE":
            return []

        facts = [
            line.strip().lstrip("- •·") for line in text.split("\n") if line.strip() and line.strip().upper() != "NONE"
        ]
        facts = [f for f in facts if len(f) > 10]  # Filter out noise

        if not facts:
            return []

        # Store each fact (max 5 per turn)
        stored = []
        for fact in facts[:5]:
            await _store_fact(fact, user_id)
            stored.append(fact)

        if stored:
            log.info(
                "Auto-extracted %d facts from user %s: %s",
                len(stored),
                user_id,
                "; ".join(f[:50] for f in stored),
            )

        return stored

    except Exception as e:  # broad: intentional
        log.debug("Fact extraction failed (non-fatal): %s", e)
        return []


async def _store_fact(fact: str, user_id: int) -> None:
    """Store a fact in QMD and ChromaDB with dedup check."""
    import vector_store

    # Dedup: check if a very similar fact already exists
    try:
        existing = await vector_store.search(
            vector_store.MEMORIES_COLLECTION,
            fact,
            top_k=1,
            threshold=0.9,  # Very high threshold = near-duplicate
        )
        if existing:
            log.debug(
                "Fact already exists (%.0f%% similar): %s",
                existing[0]["similarity"] * 100,
                fact[:60],
            )
            await vector_store.bump_access(
                vector_store.MEMORIES_COLLECTION,
                [existing[0]["id"]],
            )
            return
    except (OSError, ValueError, AttributeError) as exc:
        log.debug("Dedup check failed, storing anyway: %s", exc)

    # Generate unique ID
    fact_id = hashlib.md5(f"{fact}_{user_id}_{int(time.time())}".encode()).hexdigest()[:12]

    # Store in ChromaDB
    await vector_store.add_document(
        vector_store.MEMORIES_COLLECTION,
        doc_id=f"auto_{fact_id}",
        text=fact,
        metadata={
            "type": "fact",
            "source": "auto-extracted",
            "confidence": 0.7,
            "user_id": str(user_id),
            "tags": "auto-extracted",
        },
    )

    # Also store in QMD (best-effort)
    try:
        from qmd import remember_fact

        await remember_fact(fact, tags="auto-extracted", source="auto-extracted")
    except (ImportError, OSError, ValueError, AttributeError) as exc:
        log.debug("QMD store for auto-extracted fact failed: %s", exc)
