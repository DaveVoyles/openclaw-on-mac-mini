#!/usr/bin/env python3
"""Detect drift between the Slack slash-command handlers and the app manifest.

Every ``@app.command("/x")`` handler registered in ``src/slack_bot.py`` must be
declared in the manifest (``scripts/update_slack_manifest.py``) for Slack to
recognise it — otherwise Slack rejects it with "not a valid command".

Slack also caps an app at 50 slash commands, so a small set of low-value or
reserved commands are intentionally left unregistered. Those are listed in
``ALLOWED_UNREGISTERED`` below so they don't trip this check.

Exit codes:
    0  no drift (or only intentional exclusions)
    1  drift found — handlers missing from the manifest, manifest commands with
       no handler, or the manifest exceeds Slack's 50-command cap

Run locally:  python3 scripts/check_slack_command_drift.py
Used by CI to stop new commands from silently going unregistered.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SLACK_BOT = REPO_ROOT / "src" / "slack_bot.py"

# Slack's hard limit on slash commands per app (manifest API rejects more).
SLACK_COMMAND_CAP = 50

# Handlers that intentionally have NO manifest entry. Keep this in sync with the
# NOTE comment above ``slash_commands`` in scripts/update_slack_manifest.py.
ALLOWED_UNREGISTERED: dict[str, str] = {
    "/simple": "redundant with `/chat --simple`",
    "/brief": "redundant with /files",
    "/template": "niche starter-doc downloads",
    "/mypins": "niche bookmarked-responses viewer",
    "/media": "redundant with /watching + `/plex recent`",
    "/downloads": "redundant with /qbt + /arr",
    "/upcoming": "redundant with /arr",
    "/status": "reserved by Slack as a built-in (sets your Slack status)",
}

_COMMAND_RE = re.compile(r'@app\.command\(\s*["\'](/[a-z0-9_-]+)["\']')


def handler_commands() -> set[str]:
    """Return every slash command registered via ``@app.command`` in slack_bot.py."""
    text = SLACK_BOT.read_text(encoding="utf-8")
    return set(_COMMAND_RE.findall(text))


def manifest_commands() -> list[str]:
    """Return the slash commands declared in the app manifest (in order)."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from update_slack_manifest import MANIFEST  # noqa: PLC0415

    return [c["command"] for c in MANIFEST["features"]["slash_commands"]]


def main() -> int:
    handlers = handler_commands()
    manifest_list = manifest_commands()
    manifest = set(manifest_list)

    problems: list[str] = []

    # 1. Handlers that Slack will reject because they are not in the manifest.
    unregistered = handlers - manifest - set(ALLOWED_UNREGISTERED)
    if unregistered:
        problems.append(
            "Handlers missing from the manifest (Slack will reject them):\n"
            + "\n".join(
                f"    {cmd}  — add it to scripts/update_slack_manifest.py" for cmd in sorted(unregistered)
            )
        )

    # 2. Manifest commands that have no handler (dead command or typo).
    orphaned = manifest - handlers
    if orphaned:
        problems.append(
            "Manifest commands with no @app.command handler:\n"
            + "\n".join(f"    {cmd}" for cmd in sorted(orphaned))
        )

    # 3. Manifest must stay within Slack's hard cap.
    if len(manifest_list) > SLACK_COMMAND_CAP:
        problems.append(
            f"Manifest declares {len(manifest_list)} commands but Slack allows at "
            f"most {SLACK_COMMAND_CAP}. Remove {len(manifest_list) - SLACK_COMMAND_CAP} command(s)."
        )

    # 4. Intentional exclusions that are no longer real handlers (stale list).
    stale = set(ALLOWED_UNREGISTERED) - handlers
    if stale:
        problems.append(
            "ALLOWED_UNREGISTERED lists commands that are no longer handlers "
            "(remove them from this script):\n" + "\n".join(f"    {cmd}" for cmd in sorted(stale))
        )

    if problems:
        print("❌ Slack command drift detected:\n")
        print("\n\n".join(problems))
        print(
            f"\nHandlers: {len(handlers)} · Manifest: {len(manifest_list)} · "
            f"Intentionally unregistered: {len(ALLOWED_UNREGISTERED)}"
        )
        return 1

    print(
        f"✅ No Slack command drift. {len(handlers)} handlers, {len(manifest_list)} "
        f"registered, {len(ALLOWED_UNREGISTERED)} intentionally unregistered."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
