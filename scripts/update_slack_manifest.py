#!/usr/bin/env python3
"""Update the Slack app manifest.

Usage:
  python3 scripts/update_slack_manifest.py --print    # print JSON to stdout
  python3 scripts/update_slack_manifest.py --browser  # copy JSON + open browser (recommended)
  python3 scripts/update_slack_manifest.py --push     # push via API (requires xoxe.xoxp- token)

--browser workflow (no special token needed):
  1. Copies manifest JSON to clipboard
  2. Opens https://app.slack.com/app-settings/T0ATWRAK4Q4/A0ATR6KFXNJ/app-manifest
  3. In the browser: Cmd+A → Cmd+V → Save Changes
  NOTE: After saving Slack may issue a new xoxb- bot token — update SLACK_BOT_TOKEN in .env
        and run `make ship-server` to apply it.

For --push, set SLACK_APP_ID and SLACK_CONFIG_TOKEN in .env:
  SLACK_APP_ID=A0123456789
  SLACK_CONFIG_TOKEN=xoxe.xoxp-...

A config token is obtained via the Slack CLI auth flow only:
  ~/.slack/bin/slack login  →  run /slackauthticket <ticket> in Slack  →  approve
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ── Single source of truth for the Slack manifest ──────────────────────────

MANIFEST: dict = {
    "display_information": {
        "name": "OpenClaw",
        "description": "Personal AI assistant - chat with OpenClaw via @OpenClaw, DM, or /chat",
        "background_color": "#1a1a2e",
    },
    "features": {
        "bot_user": {
            "display_name": "OpenClaw",
            "always_online": True,
        },
        "slash_commands": [
            {
                "command": "/chat",
                "description": "Ask OpenClaw anything. Use --model to pick gemini, openai, anthropic, or copilot.",
                "usage_hint": "[--model name] [--simple] your question",
                "should_escape": False,
            },
            {
                "command": "/help",
                "description": "Show examples and tips for using OpenClaw.",
                "usage_hint": "(no arguments needed)",
                "should_escape": False,
            },
            {
                "command": "/simple",
                "description": "Toggle plain-language mode. OpenClaw will always give easy-to-read answers.",
                "usage_hint": "on | off",
                "should_escape": False,
            },
            {
                "command": "/files",
                "description": "Browse and reference your synced documents",
                "usage_hint": "[filename]",
                "should_escape": False,
            },
            {
                "command": "/research",
                "description": "Research a topic and incorporate findings into a document.",
                "usage_hint": "[topic] for [filename]",
                "should_escape": False,
            },
            {
                "command": "/batch",
                "description": "Process multiple files at once.",
                "usage_hint": "summarize | proofread | explain",
                "should_escape": False,
            },
            {
                "command": "/health",
                "description": "Check if OpenClaw is running - shows Mac Mini health, file count, last sync time.",
                "usage_hint": "(no arguments needed)",
                "should_escape": False,
            },
            {
                "command": "/digest",
                "description": "Get a periodic summary of your synced files. Use: /digest on|off|status",
                "usage_hint": "[on|off|status]",
                "should_escape": False,
            },
            {
                "command": "/template",
                "description": "Download a starter document template. Use: /template list or /template budget|letter|meeting-notes",
                "usage_hint": "[list|budget|letter|meeting-notes]",
                "should_escape": False,
            },
            {
                "command": "/metrics",
                "description": "Show OpenClaw usage metrics for the last 7 days (admin).",
                "usage_hint": "(no arguments needed)",
                "should_escape": False,
            },
            {
                "command": "/clear",
                "description": "Clear your session: reset thread history and active file selections.",
                "usage_hint": "(no arguments needed)",
                "should_escape": False,
            },
            {
                "command": "/brief",
                "description": "Show your recently uploaded files",
                "usage_hint": "(no arguments needed)",
                "should_escape": False,
            },
            {
                "command": "/mystats",
                "description": "Show your personal usage statistics",
                "usage_hint": "(no arguments needed)",
                "should_escape": False,
            },
            {
                "command": "/saved",
                "description": "View your bookmarked bot responses",
                "usage_hint": "",
                "should_escape": False,
            },
            {
                "command": "/search",
                "description": "Search your uploaded file history by keyword",
                "usage_hint": "<keyword>",
                "should_escape": False,
            },
            {
                "command": "/schedule",
                "description": "Set your preferred digest delivery time",
                "usage_hint": "9am | 14:00 | off",
                "should_escape": False,
            },
        ],
    },
    "oauth_config": {
        "scopes": {
            "bot": [
                "app_mentions:read",
                "channels:history",
                "chat:write",
                "commands",
                "files:read",
                "files:write",
                "im:history",
                "im:read",
                "im:write",
                "reactions:read",
                "reactions:write",
            ]
        }
    },
    "settings": {
        "event_subscriptions": {
            "bot_events": [
                "app_mention",
                "file_shared",
                "message.im",
                "reaction_added",
            ]
        },
        "interactivity": {"is_enabled": True},
        "org_deploy_enabled": False,
        "socket_mode_enabled": True,
        "token_rotation_enabled": False,
    },
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _load_dotenv() -> None:
    """Load .env from repo root (if present) without requiring python-dotenv."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with env_path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _push_manifest(app_id: str, config_token: str) -> None:
    manifest_json = json.dumps(MANIFEST)
    payload = json.dumps({"app_id": app_id, "manifest": manifest_json}).encode()

    req = urllib.request.Request(
        "https://slack.com/api/apps.manifest.update",
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {config_token}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        print(f"❌ Network error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not body.get("ok"):
        error = body.get("error", "unknown")
        detail = body.get("detail", "")
        msg = f"❌ Slack API error: {error}"
        if detail:
            msg += f"\n   {detail}"
        print(msg, file=sys.stderr)
        sys.exit(1)

    print("✅ Manifest updated successfully.")


# ── Entry point ──────────────────────────────────────────────────────────────

SLACK_MANIFEST_URL = (
    "https://app.slack.com/app-settings/T0ATWRAK4Q4/A0ATR6KFXNJ/app-manifest"
)


def _browser_workflow() -> None:
    """Copy manifest to clipboard and open browser — the reliable no-token path."""
    import subprocess

    manifest_json = json.dumps(MANIFEST, indent=2)

    # Copy to clipboard (macOS)
    proc = subprocess.run(["pbcopy"], input=manifest_json.encode(), check=False)
    if proc.returncode != 0:
        print("⚠️  pbcopy failed — printing JSON instead, copy it manually:")
        print(manifest_json)
    else:
        print("✅ Manifest JSON copied to clipboard.")

    # Open browser
    subprocess.run(["open", SLACK_MANIFEST_URL], check=False)
    print(f"🌐 Opening: {SLACK_MANIFEST_URL}")
    print()
    print("In the browser editor:")
    print("  1. Click inside the JSON editor")
    print("  2. Cmd+A  →  Cmd+V  →  Save Changes")
    print()
    print("⚠️  After saving, Slack may issue a NEW xoxb- bot token.")
    print("   Copy it and update SLACK_BOT_TOKEN in .env, then run: make ship-server")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print or push the OpenClaw Slack app manifest."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--print", action="store_true", help="Print manifest JSON to stdout.")
    group.add_argument(
        "--browser",
        action="store_true",
        help="Copy JSON to clipboard and open browser (recommended — no token needed).",
    )
    group.add_argument("--push", action="store_true", help="Push manifest to Slack API.")
    args = parser.parse_args()

    if args.print:
        print(json.dumps(MANIFEST, indent=2))
        return

    if args.browser:
        _browser_workflow()
        return

    # --push
    _load_dotenv()
    app_id = os.environ.get("SLACK_APP_ID", "")
    config_token = os.environ.get("SLACK_CONFIG_TOKEN", "")

    if not app_id or app_id.startswith("A..."):
        print(
            "❌ SLACK_APP_ID not set. Add it to .env:\n"
            "   SLACK_APP_ID=A0123456789\n"
            "   (Find it at https://api.slack.com/apps → Your app → App ID)",
            file=sys.stderr,
        )
        sys.exit(1)

    if not config_token or config_token.startswith("xoxe.xoxp-..."):
        print(
            "❌ SLACK_CONFIG_TOKEN not set. Add it to .env:\n"
            "   SLACK_CONFIG_TOKEN=xoxe.xoxp-...\n"
            "   (Generate at https://api.slack.com/apps → App Config Tokens → needs app_configurations:write)",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"🚀 Pushing manifest to Slack app {app_id}...")
    _push_manifest(app_id, config_token)


if __name__ == "__main__":
    main()
