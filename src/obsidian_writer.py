"""
OpenClaw Obsidian Writer

Saves structured Markdown notes to an Obsidian vault at /vault/.
All outputs (research reports, bookmarks, notes) are stored as .md files
with YAML frontmatter for compatibility with Obsidian and future indexing.
"""

import asyncio
import datetime
import json
import logging
import os
import re
from pathlib import Path

from utils import atomic_write

log = logging.getLogger("openclaw.obsidian")

VAULT_DIR = Path(os.getenv("VAULT_DIR", "/vault"))

# Subfolder layout within the vault
_SUBFOLDERS = {
    "research": "Research",
    "bookmark": "Bookmarks",
    "note": "Notes",
    "analytics": "Analytics",
    "journal": "Journal",
    "review": "Reviews",
}

_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Lazy-init the vault write lock inside the running event loop."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str, max_len: int = 50) -> str:
    """Convert text to a safe filename slug."""
    slug = re.sub(r"[^a-zA-Z0-9\s-]", "", text)
    slug = re.sub(r"\s+", "-", slug.strip()).lower()
    return slug[:max_len].strip("-") or "untitled"


def _build_frontmatter(
    title: str,
    source_url: str = "",
    tags: list[str] | None = None,
    model: str = "",
    content_type: str = "note",
) -> str:
    """Generate YAML frontmatter block."""
    date_str = datetime.date.today().isoformat()
    tag_list = list(tags or [])
    if content_type and content_type not in tag_list:
        tag_list = [content_type] + tag_list

    lines = [
        "---",
        f'title: "{title}"',
        f"date: {date_str}",
        f"type: {content_type}",
    ]
    if source_url:
        lines.append(f'source: "{source_url}"')
    if tag_list:
        lines.append("tags:")
        for t in tag_list:
            safe_tag = t.replace('"', "'").replace(" ", "-").lower()
            lines.append(f"  - {safe_tag}")
    if model:
        lines.append(f'model: "{model}"')
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core write function
# ---------------------------------------------------------------------------


async def save_to_vault(
    title: str,
    content: str,
    source_url: str = "",
    tags: list[str] | None = None,
    model: str = "",
    content_type: str = "note",
) -> str:
    """
    Save a Markdown note to the Obsidian vault.

    Args:
        title:        Note title (used in frontmatter and filename).
        content:      Markdown body text.
        source_url:   Original URL if this is a bookmark.
        tags:         List of Obsidian tags to apply.
        model:        LLM model that generated this content.
        content_type: One of 'research', 'bookmark', 'note', 'analytics'.

    Returns:
        Success/error message string.
    """
    async with _get_lock():
        try:
            subfolder = _SUBFOLDERS.get(content_type, "Notes")
            dest_dir = VAULT_DIR / subfolder
            dest_dir.mkdir(parents=True, exist_ok=True)

            date_str = datetime.date.today().isoformat()
            slug = _slugify(title)
            filename = f"{date_str}-{slug}.md"
            filepath = dest_dir / filename

            # Avoid overwriting: append a counter if file exists
            counter = 1
            while filepath.exists():
                filename = f"{date_str}-{slug}-{counter}.md"
                filepath = dest_dir / filename
                counter += 1

            frontmatter = _build_frontmatter(title, source_url, tags, model, content_type)
            body = f"# {title}\n\n{content}"
            full_doc = frontmatter + "\n\n" + body

            # Atomic write with fsync
            atomic_write(filepath, full_doc)

            log.info("Vault: saved %s (%d chars)", filepath.name, len(full_doc))
            return f"✅ Saved to vault: `{subfolder}/{filename}`"

        except Exception as e:
            log.error("Vault write failed: %s", e)
            return f"❌ Vault write failed: {e}"


# ---------------------------------------------------------------------------
# List vault notes
# ---------------------------------------------------------------------------


async def list_vault(content_type: str = "") -> str:
    """List recent notes in the vault, optionally filtered by type."""
    if not VAULT_DIR.exists():
        return "❌ Vault directory not found. Is VAULT_DIR mounted correctly?"

    subfolder = _SUBFOLDERS.get(content_type, "")
    search_dir = VAULT_DIR / subfolder if subfolder else VAULT_DIR

    try:
        files = sorted(
            [f for f in search_dir.rglob("*.md") if not f.name.startswith(".")],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return "Vault is empty."
        lines = [f"📒 **Vault** ({len(files)} notes):"]
        for f in files[:20]:
            rel = f.relative_to(VAULT_DIR)
            lines.append(f"  • `{rel}`")
        if len(files) > 20:
            lines.append(f"  … and {len(files) - 20} more")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Vault list error: {e}"


# ---------------------------------------------------------------------------
# Nightly vault indexer — imports vault notes into QMD for semantic search
# ---------------------------------------------------------------------------


async def index_vault_to_qmd() -> str:
    """
    Nightly vault indexer: scan all .md files and add new/updated ones to QMD
    memory so the agent can retrieve vault content via semantic search.

    Uses mtime-based change detection to avoid re-indexing unchanged notes.
    Runs at 3:50 AM daily (registered by bot.py).
    """
    from qmd import qmd_store

    if not VAULT_DIR.exists():
        return "❌ Vault directory not found."

    index_state_file = VAULT_DIR / ".index_state.json"

    # Load previous index state
    try:
        prev_state: dict[str, str] = (
            json.loads(index_state_file.read_text()) if index_state_file.exists() else {}
        )
    except Exception:
        prev_state = {}

    new_state: dict[str, str] = {}
    indexed, skipped = 0, 0

    md_files = [f for f in VAULT_DIR.rglob("*.md") if not f.name.startswith(".")]

    for md_file in md_files:
        try:
            mtime = str(md_file.stat().st_mtime)
            str_path = str(md_file)
            new_state[str_path] = mtime

            # Skip unchanged files
            if prev_state.get(str_path) == mtime:
                skipped += 1
                continue

            text = md_file.read_text(encoding="utf-8", errors="replace")

            # Extract title from frontmatter or first H1
            title_match = re.search(r"^title:\s*['\"]?(.+?)['\"]?\s*$", text, re.MULTILINE)
            if title_match:
                title = title_match.group(1)
            else:
                h1_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
                title = h1_match.group(1) if h1_match else md_file.stem.replace("-", " ")

            # Extract tags from YAML frontmatter
            tags_block_match = re.search(r"^tags:\s*\n((?:\s+-\s+\S+\s*\n)+)", text, re.MULTILINE)
            tags_list: list[str] = []
            if tags_block_match:
                tags_list = re.findall(r"^\s+-\s+(\S+)", tags_block_match.group(1), re.MULTILINE)

            # Strip frontmatter for body snippet
            body = re.sub(r"^---.*?---\n", "", text, count=1, flags=re.DOTALL).strip()
            snippet = body[:500].replace("\n", " ").strip()
            summary = f"[Vault/{md_file.parent.name}] {title}: {snippet}"

            await qmd_store.add(summary, tags=tags_list[:10])
            indexed += 1

        except Exception as e:
            log.warning("Vault index error for %s: %s", md_file, e)

    # Persist updated index state atomically
    try:
        tmp = index_state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(new_state, indent=2))
        tmp.replace(index_state_file)
    except Exception as e:
        log.warning("Could not save vault index state: %s", e)

    result = (
        f"✅ Vault indexed: {indexed} new/updated, {skipped} unchanged "
        f"({len(md_files)} total notes)"
    )
    log.info(result)
    return result


# ---------------------------------------------------------------------------
# Skill exports
# ---------------------------------------------------------------------------

OBSIDIAN_SKILLS = {
    "save_to_vault": save_to_vault,
    "list_vault": list_vault,
    "index_vault_to_qmd": index_vault_to_qmd,
}
