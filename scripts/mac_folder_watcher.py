"""
OpenClaw Mac Folder Watcher.

Watches ~/Documents/OpenClaw/ and rsyncs new .docx/.xlsx/.pdf files
(≤ 50 MB) to macmini:/ai-files/ via SSH.

Usage (normally invoked by launchd):
    python3 /path/to/mac_folder_watcher.py [--once]

    --once  Sync once and exit (for cron or manual testing).
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WATCH_DIR = Path.home() / "Documents" / "OpenClaw"
REMOTE = "macmini:/ai-files/"
SUPPORTED_SUFFIXES = {".docx", ".xlsx", ".pdf"}
MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
POLL_INTERVAL_SECONDS = 60

# Optional: DM parent in Slack when a file syncs
# Set SLACK_BOT_TOKEN + SLACK_NOTIFY_USER_ID in env to enable
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_NOTIFY_USER_ID = os.environ.get("SLACK_NOTIFY_USER_ID", "")


def rsync_new_files() -> list[str]:
    """Sync new supported files ≤ 50 MB. Returns list of synced filenames."""
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rsync",
        "-az",
        "--ignore-existing",  # skip files already on remote
        "--max-size=50m",  # skip files > 50 MB
        "--include=*.docx",
        "--include=*.xlsx",
        "--include=*.pdf",
        "--exclude=*",  # skip everything else
        f"{WATCH_DIR}/",
        REMOTE,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[watcher] rsync error: {result.stderr.strip()}", file=sys.stderr)
        return []
    # Parse rsync output to find transferred filenames
    synced = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith(("sending", "sent", "total"))
    ]
    return synced


def notify_slack(filenames: list[str]) -> None:
    """Post a Slack DM to the parent when files are synced (optional)."""
    if not SLACK_BOT_TOKEN or not SLACK_NOTIFY_USER_ID or not filenames:
        return
    text = "✅ Synced to OpenClaw: " + ", ".join(f"`{f}`" for f in filenames)
    payload = json.dumps({"channel": SLACK_NOTIFY_USER_ID, "text": text}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[watcher] Slack notify failed: {e}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Sync once and exit.")
    args = parser.parse_args()

    if args.once:
        synced = rsync_new_files()
        if synced:
            notify_slack(synced)
        return

    print(f"[watcher] Watching {WATCH_DIR} → {REMOTE} (poll every {POLL_INTERVAL_SECONDS}s)")
    while True:
        synced = rsync_new_files()
        if synced:
            print(f"[watcher] Synced: {synced}")
            notify_slack(synced)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
