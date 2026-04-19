"""
OpenClaw Dropbox Sync — Wave 10
Polls a Dropbox folder for new files and notifies Slack users.

Requirements:
  pip install dropbox>=12.0.2

Config (.env):
  DROPBOX_ACCESS_TOKEN=sl.xxxxxxx        (required)
  DROPBOX_WATCH_PATH=/OpenClaw           (optional, default: /OpenClaw)
  DROPBOX_NOTIFY_USER_ID=U...            (optional; defaults to SLACK_NOTIFY_USER_ID)

Behaviour:
  - Background loop polls Dropbox every 30 s for new files in DROPBOX_WATCH_PATH.
  - On discovery, sends a Slack DM to the configured user with file name + summary offer.
  - /dropbox command calls list_recent_files() directly (no polling needed).
"""

import asyncio
import logging
import os

log = logging.getLogger(__name__)

DROPBOX_ACCESS_TOKEN: str = os.getenv("DROPBOX_ACCESS_TOKEN", "")
DROPBOX_WATCH_PATH: str = os.getenv("DROPBOX_WATCH_PATH", "/OpenClaw")
DROPBOX_POLL_INTERVAL: int = 30  # seconds
DROPBOX_CONFIGURED: bool = bool(DROPBOX_ACCESS_TOKEN)

# Track which file paths we've already alerted on to avoid duplicate DMs.
_seen_paths: set[str] = set()
_last_poll: float = 0.0


def _get_client():  # type: ignore[return]
    """Return an authenticated Dropbox client, or None if not configured."""
    if not DROPBOX_CONFIGURED:
        return None
    try:
        import dropbox  # type: ignore[import]

        return dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)
    except ImportError:
        log.warning("dropbox package not installed — run: pip install dropbox>=12.0.2")
        return None
    except Exception as exc:
        log.error("Dropbox client init failed: %s", exc)
        return None


async def list_recent_files(path: str = DROPBOX_WATCH_PATH, count: int = 20) -> list[dict]:
    """
    Return up to *count* recent files from the Dropbox folder *path*.

    Each entry is a dict with keys: name, path, size, modified.
    Returns an empty list when Dropbox is not configured or an error occurs.
    """
    if not DROPBOX_CONFIGURED:
        return []

    def _sync_list() -> list[dict]:
        dbx = _get_client()
        if dbx is None:
            return []
        try:
            result = dbx.files_list_folder(path)
        except Exception as exc:
            log.error("Dropbox list_folder error for %s: %s", path, exc)
            return []

        entries = []
        for entry in result.entries:
            try:
                import dropbox.files as dbxf  # type: ignore[import]

                if not isinstance(entry, dbxf.FileMetadata):
                    continue
            except ImportError:
                if not hasattr(entry, "size"):
                    continue

            entries.append(
                {
                    "name": entry.name,
                    "path": entry.path_lower,
                    "size": entry.size,
                    "modified": str(entry.client_modified)[:19],
                }
            )

        # Sort newest-first by modified timestamp (lexicographic ISO 8601 works)
        entries.sort(key=lambda e: e["modified"], reverse=True)
        return entries[:count]

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync_list), timeout=20)
    except asyncio.TimeoutError:
        log.warning("Dropbox list_recent_files timed out")
        return []


async def dropbox_watch_loop(slack_client: object, notify_user_id: str) -> None:
    """
    Background coroutine: poll Dropbox every DROPBOX_POLL_INTERVAL seconds.
    On new files, send a Slack DM to *notify_user_id* with a summary offer.
    Designed to be launched with asyncio.create_task().
    """
    global _seen_paths, _last_poll

    if not DROPBOX_CONFIGURED:
        log.info("Dropbox watch loop skipped — DROPBOX_ACCESS_TOKEN not set")
        return

    if not notify_user_id:
        log.info("Dropbox watch loop skipped — no notify_user_id configured")
        return

    log.info(
        "Dropbox watch loop started (path=%s, interval=%ds)",
        DROPBOX_WATCH_PATH,
        DROPBOX_POLL_INTERVAL,
    )

    # Seed seen paths on first run so we don't flood existing files.
    try:
        initial = await list_recent_files()
        _seen_paths = {f["path"] for f in initial}
        log.info("Dropbox: seeded %d existing files, watching for new ones", len(_seen_paths))
    except Exception as exc:
        log.warning("Dropbox: seed failed: %s", exc)

    while True:
        await asyncio.sleep(DROPBOX_POLL_INTERVAL)
        try:
            files = await list_recent_files()
            new_files = [f for f in files if f["path"] not in _seen_paths]
            for f in new_files:
                _seen_paths.add(f["path"])
                size_kb = f["size"] // 1024
                msg = (
                    f"📦 *New Dropbox file detected!*\n"
                    f"*{f['name']}* ({size_kb} KB)\n"
                    f"Would you like me to summarise it? Upload it here or let me know!"
                )
                try:
                    await slack_client.chat_postMessage(channel=notify_user_id, text=msg)
                    log.info("Dropbox alert sent for: %s", f["name"])
                except Exception as exc:
                    log.warning("Dropbox alert DM failed: %s", exc)
        except Exception as exc:
            log.warning("Dropbox poll iteration error: %s", exc)
