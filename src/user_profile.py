"""Structured user profile — tracks preferences, interests, and working style.

Persists to /memory/user_profile.json and embeds into ChromaDB for
semantic recall.  Inspired by the Genesis "User Context" memory pattern.
"""

import json
import logging
import time
from pathlib import Path

from utils import atomic_write

log = logging.getLogger("openclaw.user_profile")

# ---------------------------------------------------------------------------
# Schema & paths
# ---------------------------------------------------------------------------

PROFILE_PATH = Path("/memory/user_profile.json")

DEFAULT_PROFILE: dict = {
    "preferences": {},
    "working_style": "",
    "interests": [],
    "tools": [],
    "communication_style": "",
    "context_notes": [],
    "learned_at": {},
}

# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def load_profile() -> dict:
    """Return the on-disk profile, or *DEFAULT_PROFILE* if missing/corrupt."""
    try:
        return json.loads(PROFILE_PATH.read_text())
    except FileNotFoundError:
        log.info("No profile on disk — returning defaults")
        return {**DEFAULT_PROFILE}
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        log.warning("Profile unreadable, returning defaults: %s", exc)
        return {**DEFAULT_PROFILE}


def save_profile(profile: dict) -> None:
    """Atomically write *profile* to disk."""
    atomic_write(PROFILE_PATH, json.dumps(profile, indent=2))
    log.debug("Profile saved to %s", PROFILE_PATH)


# ---------------------------------------------------------------------------
# Update helpers
# ---------------------------------------------------------------------------

def _stamp(profile: dict, field: str) -> None:
    profile.setdefault("learned_at", {})[field] = time.time()


def update_preference(key: str, value: str) -> None:
    """Set a single preference key."""
    profile = load_profile()
    profile["preferences"][key] = value
    _stamp(profile, f"preferences.{key}")
    save_profile(profile)
    log.info("Preference updated: %s = %s", key, value)


def add_interest(interest: str) -> None:
    """Append *interest* if not already present."""
    profile = load_profile()
    if interest not in profile["interests"]:
        profile["interests"].append(interest)
        _stamp(profile, "interests")
        save_profile(profile)
        log.info("Interest added: %s", interest)


def add_context_note(note: str) -> None:
    """Append a free-text context note."""
    profile = load_profile()
    profile["context_notes"].append(note)
    _stamp(profile, "context_notes")
    save_profile(profile)
    log.info("Context note added")


def update_field(field: str, value) -> None:
    """Generic updater for any top-level profile field."""
    profile = load_profile()
    if field not in DEFAULT_PROFILE:
        log.warning("Unknown profile field '%s' — ignoring", field)
        return
    profile[field] = value
    _stamp(profile, field)
    save_profile(profile)
    log.info("Field '%s' updated", field)


# ---------------------------------------------------------------------------
# Auto-learn from conversation
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = (
    "Analyze this message for personal profile information. "
    "Extract any: timezone, preferences, interests, tools used, "
    "communication style preferences, or personal context. "
    "Return JSON with fields to update, or empty {{}} if nothing to extract.\n"
    "User: {user_message}"
)


async def learn_from_message(
    user_message: str,
    bot_response: str,
) -> list[str]:
    """Use the LLM to detect personal info and update the profile.

    Returns a list of human-readable strings describing what was learned.
    Never raises — all failures are silently logged.
    """
    learned: list[str] = []
    try:
        from llm import chat

        prompt = _EXTRACT_PROMPT.format(user_message=user_message)
        response, _, _ = await chat(prompt, model_preference="auto")

        # Strip markdown fences if present
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]

        updates: dict = json.loads(text)
        if not updates:
            return learned

        profile = load_profile()

        if "preferences" in updates and isinstance(updates["preferences"], dict):
            profile["preferences"].update(updates["preferences"])
            for k, v in updates["preferences"].items():
                _stamp(profile, f"preferences.{k}")
                learned.append(f"preference {k}={v}")

        if "interests" in updates and isinstance(updates["interests"], list):
            for i in updates["interests"]:
                if i not in profile["interests"]:
                    profile["interests"].append(i)
                    learned.append(f"interest: {i}")
            if updates["interests"]:
                _stamp(profile, "interests")

        if "tools" in updates and isinstance(updates["tools"], list):
            for t in updates["tools"]:
                if t not in profile["tools"]:
                    profile["tools"].append(t)
                    learned.append(f"tool: {t}")
            if updates["tools"]:
                _stamp(profile, "tools")

        for field in ("working_style", "communication_style"):
            if field in updates and updates[field]:
                profile[field] = updates[field]
                _stamp(profile, field)
                learned.append(f"{field}: {updates[field]}")

        if "context_notes" in updates and isinstance(updates["context_notes"], list):
            for note in updates["context_notes"]:
                profile["context_notes"].append(note)
                learned.append(f"note: {note}")
            if updates["context_notes"]:
                _stamp(profile, "context_notes")

        if learned:
            save_profile(profile)
            try:
                await sync_profile_to_vectors()
            except (OSError, ValueError, AttributeError) as exc:
                log.debug("Vector sync failed (non-critical): %s", exc)

    except Exception as exc:  # broad: intentional
        log.debug("learn_from_message failed (non-critical): %s", exc)

    return learned


# ---------------------------------------------------------------------------
# Profile → system-prompt context
# ---------------------------------------------------------------------------


def get_profile_prompt() -> str:
    """Return a formatted block suitable for system-prompt injection.

    Returns ``""`` when the profile is empty/default.
    """
    profile = load_profile()
    lines: list[str] = []

    if profile.get("preferences"):
        pairs = ", ".join(f"{k}={v}" for k, v in profile["preferences"].items())
        lines.append(f"Preferences: {pairs}")

    if profile.get("interests"):
        lines.append(f"Interests: {', '.join(profile['interests'])}")

    if profile.get("tools"):
        lines.append(f"Tools: {', '.join(profile['tools'])}")

    if profile.get("working_style"):
        lines.append(f"Working style: {profile['working_style']}")

    if profile.get("communication_style"):
        lines.append(f"Communication style: {profile['communication_style']}")

    if profile.get("context_notes"):
        lines.append(f"Context notes: {'; '.join(profile['context_notes'])}")

    if not lines:
        return ""
    return "[User Profile]\n" + "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# ChromaDB sync
# ---------------------------------------------------------------------------


async def sync_profile_to_vectors() -> None:
    """Upsert the full profile text into the memories collection."""
    text = get_profile_prompt()
    if not text:
        return

    try:
        import vector_store

        await vector_store.add_document(
            collection_name="memories",
            doc_id="user_profile",
            text=text,
            metadata={"type": "user_profile"},
        )
        log.debug("Profile synced to ChromaDB")
    except (OSError, ValueError, AttributeError) as exc:
        log.warning("Profile vector sync failed: %s", exc)
