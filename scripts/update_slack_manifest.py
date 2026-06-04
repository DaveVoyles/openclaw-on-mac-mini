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
import urllib.parse
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
        "app_home": {
            "home_tab_enabled": True,
            "messages_tab_enabled": True,
            "messages_tab_read_only_enabled": False,
        },
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
                "command": "/mypins",
                "description": "View your bookmarked bot responses",
                "usage_hint": "(no arguments needed)",
                "should_escape": False,
            },
            {
                "command": "/filesearch",
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
            {
                "command": "/nickname",
                "description": "Set your preferred display name (e.g. /nickname Chuck)",
                "usage_hint": "<your name>",
                "should_escape": False,
            },
            {
                "command": "/inbox",
                "description": "Show your unread Gmail emails",
                "usage_hint": "(no arguments needed)",
                "should_escape": False,
            },
            {
                "command": "/email",
                "description": "Summarize a specific email from your inbox",
                "usage_hint": "<number>",
                "should_escape": False,
            },
            {
                "command": "/today",
                "description": "Show today's Google Calendar events",
                "usage_hint": "(no arguments needed)",
                "should_escape": False,
            },
            {
                "command": "/calendar",
                "description": "Check your Google Calendar events",
                "usage_hint": "[today | week]",
                "should_escape": False,
            },
            {
                "command": "/clawbox",
                "description": "Browse recent files in your Dropbox watch folder",
                "usage_hint": "[list]",
                "should_escape": False,
            },
            {
                "command": "/clawchan",
                "description": "List or archive Slack channels (admin)",
                "usage_hint": "[list | archive <name>]",
                "should_escape": False,
            },
            {
                "command": "/incident",
                "description": "Incident Copilot: open, triage, and execute approved remediation actions",
                "usage_hint": "start <title> | status <id> | resolve <id> [postmortem] | list | timeline <id>",
                "should_escape": False,
            },
            {
                "command": "/copilot",
                "description": "Run host Copilot CLI (--allow-all-tools) over SSH; owner-only",
                "usage_hint": "<prompt> — e.g. diagnose why plex can't find files",
                "should_escape": False,
            },
            {
                "command": "/copilot-sessions",
                "description": "List your active and recent Copilot sessions",
                "usage_hint": "(no arguments)",
                "should_escape": False,
            },
            {
                "command": "/copilot-cancel",
                "description": "Send SIGINT (Ctrl-C) to the current turn of a Copilot session",
                "usage_hint": "<session_id>",
                "should_escape": False,
            },
            {
                "command": "/copilot-end",
                "description": "End a Copilot session and close the host process",
                "usage_hint": "<session_id>",
                "should_escape": False,
            },
            {
                "command": "/copilot-attach",
                "description": "Show details for a Copilot session (channel, transcript, idle)",
                "usage_hint": "<session_id>",
                "should_escape": False,
            },
            {
                "command": "/host",
                "description": "Quick-action shortcuts: status, logs, restart, disk, net, plex-fix, git",
                "usage_hint": "status | logs <svc> [n] | restart <svc> | disk | net | plex-fix | git <args>",
                "should_escape": False,
            },
            {
                "command": "/wake",
                "description": "Wake a configured MacBook Pro over Wake-on-LAN",
                "usage_hint": "mbp | mbp2",
                "should_escape": False,
            },
            {
                "command": "/nas",
                "description": "Inspect NAS disk usage, folders, and resource stats",
                "usage_hint": "df | ls <path> | free",
                "should_escape": False,
            },
            {
                "command": "/h",
                "description": "Short alias for /hermes to start a Hermes session",
                "usage_hint": "<prompt>",
                "should_escape": False,
            },
        ],
    },
    "oauth_config": {
        "pkce_enabled": False,
        "scopes": {
            "bot": [
                "app_mentions:read",
                "channels:history",
                "chat:write",
                "commands",
                "files:read",
                "files:write",
                "groups:history",
                "im:history",
                "im:read",
                "im:write",
                "reactions:read",
                "reactions:write",
            ],
            "user": [
                "channels:read",
                "groups:read",
                "groups:write",
            ],
        }
    },
    "settings": {
        "event_subscriptions": {
            "bot_events": [
                "app_home_opened",
                "app_mention",
                "file_shared",
                "message.channels",
                "message.groups",
                "message.im",
                "reaction_added",
            ]
        },
        "interactivity": {"is_enabled": True},
        "is_mcp_enabled": False,
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


def _rotate_config_token(refresh_token: str) -> tuple[str, str] | None:
    """Exchange a refresh token for a fresh (access, refresh) pair.

    Slack config tokens expire after 12 hours. ``tooling.tokens.rotate`` accepts
    a long-lived refresh token and returns a new short-lived access token plus a
    new refresh token. Returns None on failure.
    """
    req = urllib.request.Request(
        f"https://slack.com/api/tooling.tokens.rotate?refresh_token={urllib.parse.quote(refresh_token)}",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        print(f"❌ Token rotate network error: {exc}", file=sys.stderr)
        return None
    if not body.get("ok"):
        print(f"❌ Token rotate failed: {body.get('error','unknown')}", file=sys.stderr)
        return None
    return body.get("token", ""), body.get("refresh_token", "")


def _persist_tokens(access: str, refresh: str) -> None:
    """Write rotated tokens back to .env in-place (preserves other vars)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        env_path.touch()
    lines = env_path.read_text().splitlines()
    have_access = have_refresh = False
    out: list[str] = []
    for ln in lines:
        if ln.startswith("SLACK_CONFIG_TOKEN="):
            out.append(f"SLACK_CONFIG_TOKEN={access}")
            have_access = True
        elif ln.startswith("SLACK_CONFIG_REFRESH_TOKEN="):
            out.append(f"SLACK_CONFIG_REFRESH_TOKEN={refresh}")
            have_refresh = True
        else:
            out.append(ln)
    if not have_access:
        out.append(f"SLACK_CONFIG_TOKEN={access}")
    if not have_refresh:
        out.append(f"SLACK_CONFIG_REFRESH_TOKEN={refresh}")
    env_path.write_text("\n".join(out) + "\n")
    os.environ["SLACK_CONFIG_TOKEN"] = access
    os.environ["SLACK_CONFIG_REFRESH_TOKEN"] = refresh
    print("🔄 Rotated SLACK_CONFIG_TOKEN; .env updated.")


def _fetch_manifest(app_id: str, config_token: str) -> dict | None:
    """GET the currently-deployed manifest for drift detection."""
    req = urllib.request.Request(
        f"https://slack.com/api/apps.manifest.export?app_id={urllib.parse.quote(app_id)}",
        method="POST",
        headers={"Authorization": f"Bearer {config_token}"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        print(f"❌ Network error: {exc}", file=sys.stderr)
        return None
    if not body.get("ok"):
        print(f"❌ Slack API error fetching manifest: {body.get('error','unknown')}", file=sys.stderr)
        return None
    raw = body.get("manifest")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw if isinstance(raw, dict) else None


def _push_manifest(app_id: str, config_token: str) -> dict | None:
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
    return body


def _resolve_config_token() -> str | None:
    """Return a usable access token, rotating via refresh if needed."""
    token = os.environ.get("SLACK_CONFIG_TOKEN", "").strip()
    refresh = os.environ.get("SLACK_CONFIG_REFRESH_TOKEN", "").strip()
    if token and not token.startswith("xoxe.xoxp-..."):
        return token
    if refresh:
        print("🔄 No usable SLACK_CONFIG_TOKEN; rotating from refresh token…")
        pair = _rotate_config_token(refresh)
        if pair:
            new_access, new_refresh = pair
            _persist_tokens(new_access, new_refresh)
            return new_access
    return None


def _push_with_auto_rotate(app_id: str) -> None:
    """Push manifest; if the call fails with token_expired/invalid_auth, rotate and retry once."""
    token = _resolve_config_token()
    if not token:
        print(
            "❌ SLACK_CONFIG_TOKEN missing/invalid and no SLACK_CONFIG_REFRESH_TOKEN to rotate.\n"
            "   Add both to .env:\n"
            "     SLACK_CONFIG_TOKEN=xoxe.xoxp-...\n"
            "     SLACK_CONFIG_REFRESH_TOKEN=xoxe-...\n"
            "   Generate at https://api.slack.com/apps → 'App Configuration Tokens'",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"🚀 Pushing manifest to Slack app {app_id}…")
    manifest_json = json.dumps(MANIFEST)
    payload = json.dumps({"app_id": app_id, "manifest": manifest_json}).encode()

    def _do_push(tok: str) -> dict:
        req = urllib.request.Request(
            "https://slack.com/api/apps.manifest.update",
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {tok}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())

    try:
        body = _do_push(token)
    except urllib.error.URLError as exc:
        print(f"❌ Network error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not body.get("ok") and body.get("error") in ("token_expired", "invalid_auth"):
        refresh = os.environ.get("SLACK_CONFIG_REFRESH_TOKEN", "").strip()
        if not refresh:
            print(f"❌ Slack API error: {body.get('error')} and no refresh token available", file=sys.stderr)
            sys.exit(1)
        print("🔄 Access token expired; rotating and retrying…")
        pair = _rotate_config_token(refresh)
        if not pair:
            sys.exit(1)
        _persist_tokens(*pair)
        try:
            body = _do_push(pair[0])
        except urllib.error.URLError as exc:
            print(f"❌ Network error on retry: {exc}", file=sys.stderr)
            sys.exit(1)

    if not body.get("ok"):
        msg = f"❌ Slack API error: {body.get('error','unknown')}"
        if body.get("detail"):
            msg += f"\n   {body['detail']}"
        if body.get("errors"):
            msg += f"\n   {json.dumps(body['errors'], indent=2)}"
        print(msg, file=sys.stderr)
        sys.exit(1)

    print("✅ Manifest updated successfully.")


def _check_drift(app_id: str) -> int:
    """Compare in-repo manifest to deployed. Return 0 if in-sync, 2 if drift."""
    token = _resolve_config_token()
    if not token:
        print("❌ Need SLACK_CONFIG_TOKEN (or refresh) to compare deployed manifest.", file=sys.stderr)
        return 1
    deployed = _fetch_manifest(app_id, token)
    if deployed is None:
        return 1
    local = json.loads(json.dumps(MANIFEST, sort_keys=True))
    remote = json.loads(json.dumps(deployed, sort_keys=True))
    if local == remote:
        print("✅ In-repo manifest matches deployed Slack app.")
        return 0
    print("⚠️  Manifest drift detected. Diff (local → deployed):")
    import difflib
    a = json.dumps(local, indent=2, sort_keys=True).splitlines()
    b = json.dumps(remote, indent=2, sort_keys=True).splitlines()
    for line in difflib.unified_diff(b, a, fromfile="deployed", tofile="in-repo", lineterm=""):
        print(line)
    print("\nRun `make slack-manifest-push` to deploy in-repo manifest.")
    return 2


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
    group.add_argument(
        "--check",
        action="store_true",
        help="Compare in-repo manifest to deployed (CI drift check). Exit 2 on drift.",
    )
    args = parser.parse_args()

    if args.print:
        print(json.dumps(MANIFEST, indent=2))
        return

    if args.browser:
        _browser_workflow()
        return

    _load_dotenv()
    app_id = os.environ.get("SLACK_APP_ID", "")
    if not app_id or app_id.startswith("A..."):
        print(
            "❌ SLACK_APP_ID not set. Add it to .env:\n"
            "   SLACK_APP_ID=A0ATR6KFXNJ\n"
            "   (Find it at https://api.slack.com/apps → Your app → App ID)",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.check:
        sys.exit(_check_drift(app_id))

    # --push
    _push_with_auto_rotate(app_id)


if __name__ == "__main__":
    main()
