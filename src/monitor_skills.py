"""
OpenClaw Monitor Skills — URL content change detection.

Lets the agent proactively watch web pages for changes and alert when
the content hash changes. Perfect for:
  - Tracking job postings, real-estate listings, regulatory updates
  - Monitoring competition pricing pages
  - Watching GitHub releases pages or status pages
  - Alerting when a news story is updated

Skills:
  snapshot_url(url, label)         — take a baseline snapshot of a URL
  check_url_for_changes(url)       — compare current content to stored snapshot
  list_monitored_urls()            — show all monitored URLs and their last-seen state
  remove_url_monitor(url)          — stop monitoring a URL

Usage pattern (with scheduler):
  1. snapshot_url("https://example.com/pricing", "Competitor Pricing")
  2. scheduler.create("check_url_for_changes",
                      args={"url": "https://example.com/pricing"},
                      interval_minutes=60,
                      notify_channel_id=ALERT_CHANNEL_ID,
                      alert_only=True)
  → Will alert whenever the page content changes.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

log = logging.getLogger("openclaw.monitor")

MEMORY_DIR = Path(os.getenv("MEMORY_DIR", "/memory"))
_SNAPSHOTS_FILE = MEMORY_DIR / "url_snapshots.json"

_TIMEOUT = aiohttp.ClientTimeout(total=20)

from http_session import SessionManager

_sessions = SessionManager(timeout=20, name="monitor")
_get_session = _sessions.get
close_session = _sessions.close


# ---------------------------------------------------------------------------
# Snapshot storage
# ---------------------------------------------------------------------------

def _load_snapshots() -> dict[str, dict]:
    """Load URL snapshot records from disk."""
    if _SNAPSHOTS_FILE.exists():
        try:
            return json.loads(_SNAPSHOTS_FILE.read_text())
        except Exception as exc:
            log.debug("Failed to load snapshots: %s", exc)
    return {}


def _save_snapshots(data: dict) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _SNAPSHOTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_SNAPSHOTS_FILE)


# ---------------------------------------------------------------------------
# Page fetching + normalization
# ---------------------------------------------------------------------------

_SSRF_PRIVATE = re.compile(
    r"^(https?://)?(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.).*",
    re.IGNORECASE,
)


async def _fetch_text(url: str) -> str:
    """Fetch URL content, strip HTML tags, normalize whitespace."""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise ValueError("Only http/https URLs are supported.")
    if _SSRF_PRIVATE.match(url):
        raise ValueError("Monitoring private/localhost URLs is not allowed.")

    session = await _get_session()
    async with session.get(
        url,
        headers={"User-Agent": "OpenClaw-Monitor/1.0"},
        allow_redirects=True,
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        raw = await resp.text(errors="replace")

    # Strip HTML tags, collapse whitespace — we hash the visible text
    text = re.sub(r"<[^>]*>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

async def snapshot_url(url: str, label: str = "") -> str:
    """
    Take a baseline snapshot of a URL for change monitoring.

    Call this once to establish the baseline. Then schedule
    `check_url_for_changes` to run periodically.

    Args:
        url:   Full https URL to monitor.
        label: Optional human-readable name (e.g. "Amazon Pricing").
    """
    try:
        text = await _fetch_text(url)
    except Exception as e:
        return f"❌ Could not fetch `{url}`: {e}"

    content_hash = _content_hash(text)
    snapshots = _load_snapshots()

    snapshots[url] = {
        "url": url,
        "label": label or url,
        "hash": content_hash,
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "last_changed": datetime.now(timezone.utc).isoformat(),
        "change_count": 0,
        "content_preview": text[:300],
    }
    _save_snapshots(snapshots)

    return (
        f"📸 Snapshot saved for `{label or url}`.\n"
        f"Hash: `{content_hash}` · {len(text):,} chars\n"
        f"Preview: *{text[:150]}…*"
    )


async def check_url_for_changes(url: str) -> str:
    """
    Compare current content of a URL against the stored snapshot.

    Returns a change alert if content has changed, or a clean status if not.
    This is designed to be called by the scheduler so alerts fire automatically.

    Args:
        url: The URL to check (must have been snapshotted first).
    """
    snapshots = _load_snapshots()
    if url not in snapshots:
        return f"⚠️ No baseline snapshot for `{url}`. Run `snapshot_url` first."

    try:
        text = await _fetch_text(url)
    except Exception as e:
        return f"❌ Could not fetch `{url}`: {e}"

    new_hash = _content_hash(text)
    record = snapshots[url]
    old_hash = record["hash"]
    label = record.get("label", url)

    now_iso = datetime.now(timezone.utc).isoformat()
    record["last_checked"] = now_iso

    if new_hash == old_hash:
        _save_snapshots(snapshots)
        return f"✅ No change detected — **{label}** (hash `{new_hash}`)"

    # Content changed — update record
    record["hash"] = new_hash
    record["last_changed"] = now_iso
    record["change_count"] = record.get("change_count", 0) + 1
    old_preview = record.get("content_preview", "")
    record["content_preview"] = text[:300]
    _save_snapshots(snapshots)

    # Diff first 300 chars of visible text
    old_snip = old_preview[:200]
    new_snip = text[:200]

    return (
        f"🔔 **Change detected** on **{label}**!\n"
        f"Hash: `{old_hash}` → `{new_hash}` "
        f"(change #{record['change_count']})\n\n"
        f"**Before** (first 200 chars):\n*{old_snip}*\n\n"
        f"**After** (first 200 chars):\n*{new_snip}*\n\n"
        f"🔗 {url}"
    )


async def list_monitored_urls() -> str:
    """List all URLs currently being monitored for changes."""
    snapshots = _load_snapshots()
    if not snapshots:
        return "No URLs are being monitored. Use `snapshot_url` to add one."

    lines = [f"**Monitored URLs** ({len(snapshots)} total)"]
    for rec in sorted(snapshots.values(), key=lambda r: r.get("last_changed", "")):
        label = rec.get("label", rec["url"])
        checked = rec.get("last_checked", "never")[:10]
        changed = rec.get("last_changed", "never")[:10]
        changes = rec.get("change_count", 0)
        lines.append(
            f"• **{label}** — last checked: `{checked}` · "
            f"last changed: `{changed}` · changes: {changes}\n"
            f"  `{rec['url']}`"
        )
    return "\n".join(lines)[:1900]


async def remove_url_monitor(url: str) -> str:
    """Stop monitoring a URL and remove its snapshot record."""
    snapshots = _load_snapshots()
    if url not in snapshots:
        return f"⚠️ `{url}` is not in the monitor list."
    label = snapshots[url].get("label", url)
    del snapshots[url]
    _save_snapshots(snapshots)
    return f"✅ Removed monitor for **{label}**."


MONITOR_SKILLS = {
    "snapshot_url": snapshot_url,
    "check_url_for_changes": check_url_for_changes,
    "list_monitored_urls": list_monitored_urls,
    "remove_url_monitor": remove_url_monitor,
}
