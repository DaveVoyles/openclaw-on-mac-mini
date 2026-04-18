# Wave 10 — Dropbox + Gmail + Calendar Integrations

**Date:** 2026-04-18  
**Status:** Planning  
**Baseline:** 120 tests, `b7bbc3d` on `main`

---

## Context

User has two non-technical parents (Chuck = Father, Lisa = Mother) who use the Slack bot as their primary AI interface. They primarily use:
- **Dropbox** for file storage
- **Gmail** for email
- Likely Google Calendar for scheduling

The goal is to make it trivial to ask about files, emails, and appointments directly from Slack — removing the friction of uploading files manually or context-switching out of Slack.

**Key discovery:** `src/email_skills.py` and `src/calendar_skills.py` are fully built, async, production-ready — they just aren't wired to the Slack bot. Gmail/Calendar integration is primarily a bridging task.

---

## Why These Three Integrations

| Integration | Value | Effort | Notes |
|------------|-------|--------|-------|
| Gmail → Slack | 🔴 HIGH | 🟢 LOW | Skills exist; just needs Slack command wiring |
| Calendar → Slack | 🔴 HIGH | 🟢 LOW | Skills exist; enhances morning digest |
| Dropbox watcher | 🟠 MEDIUM-HIGH | 🟠 MEDIUM | New module; eliminates file upload friction |
| OneDrive | 🟡 LOW | 🔴 HIGH | Not needed if Dropbox exists |
| iCloud | 🟡 LOW | 🔴 HIGH | Apple API complexity not worth it |
| Google Drive | 🟡 LOW | 🟠 MEDIUM | They use Dropbox instead |

---

## Lane A — Gmail → Slack (Han 😉🚀)

### What Chuck/Lisa get
- `/email` — shows last 10 inbox messages (subject, sender, preview)
- `/email week` — emails from the last 7 days
- `/email <keyword>` — searches for emails about a topic (e.g., `/email doctor`)
- Natural language in DMs: "any emails from CVS today?" → routes to search_emails()

### Implementation

**New slash command `/email` in `src/slack_bot.py`:**

```python
@app.command("/email")
async def cmd_email(ack, respond, command):
    await ack()
    from email_skills import read_inbox, search_emails
    query = command.get("text", "").strip()
    user = command["user_id"]
    name = _get_user_name(user)
    if not query or query == "today":
        result = await read_inbox(count=10)
    elif query == "week":
        result = await read_inbox(count=25)  # then filter by date in email_skills
    else:
        result = await search_emails(query)
    await respond(f"📬 *Email summary for {name}*\n{result}")
```

**Graceful degradation:** If `GMAIL_USER` is not set, respond with a friendly setup message (not an error traceback).

**Manifest entry:**
```json
{"command": "/email", "description": "Check your Gmail inbox", "usage_hint": "[today | week | keyword]"}
```

### Tests (add to `tests/test_slack_bot.py`)

- `test_cmd_email_no_creds_returns_setup_message` — missing GMAIL_USER → friendly message
- `test_cmd_email_default_calls_read_inbox`
- `test_cmd_email_week_calls_read_inbox`
- `test_cmd_email_keyword_calls_search_emails`

### Done when
- [ ] `/email` command registered, acked, and responds
- [ ] Graceful degradation when GMAIL creds absent
- [ ] 4 tests added and passing
- [ ] Manifest script updated with `/email` entry

---

## Lane B — Calendar → Slack (Yoda 👽✨)

### What Chuck/Lisa get
- `/calendar` — shows today's events
- `/calendar week` — shows next 7 days
- `/calendar add <description>` — creates a new event (natural language)
- Morning digest (`/digest`) enhanced: if calendar configured, prepend "📅 Today: dentist at 10am"

### Implementation

**New slash command `/calendar` in `src/slack_bot.py`:**

```python
@app.command("/calendar")
async def cmd_calendar(ack, respond, command):
    await ack()
    from calendar_skills import get_todays_events, get_upcoming_events, create_calendar_event
    text = command.get("text", "").strip().lower()
    user = command["user_id"]
    name = _get_user_name(user)
    if not text or text == "today":
        result = await get_todays_events()
    elif text == "week":
        result = await get_upcoming_events(days=7)
    elif text.startswith("add "):
        result = await create_calendar_event(text[4:])
    else:
        result = await get_upcoming_events(days=7)
    await respond(f"📅 *Calendar for {name}*\n{result}")
```

**Digest enhancement** — in `_build_digest()` or `cmd_digest()`:
```python
try:
    from calendar_skills import get_todays_events
    cal = await get_todays_events()
    if cal and "No events" not in cal:
        digest_parts.insert(0, f"📅 *Today's schedule:*\n{cal}\n")
except Exception:
    pass  # calendar not configured — skip silently
```

**Graceful degradation:** If `GOOGLE_OAUTH_REFRESH_TOKEN` not set, show friendly setup hint.

**Manifest entry:**
```json
{"command": "/calendar", "description": "Check your Google Calendar", "usage_hint": "[today | week | add <event>]"}
```

### Tests
- `test_cmd_calendar_no_creds_returns_setup_message`
- `test_cmd_calendar_today_calls_get_todays_events`
- `test_cmd_calendar_week_calls_get_upcoming_events`
- `test_cmd_calendar_add_calls_create_event`
- `test_digest_includes_calendar_when_configured`

### Done when
- [ ] `/calendar` command registered and responds
- [ ] Graceful degradation when OAuth not configured
- [ ] Digest enhanced with today's events (opt-in, silent fail)
- [ ] 5 tests added and passing
- [ ] Manifest script updated with `/calendar` entry

---

## Lane C — Dropbox Watcher (Leia 👑💁‍♀️)

### What Chuck/Lisa get
- Drop a file in `~/Dropbox/OpenClaw/` → 30 seconds later, Slack DM: "New file: Medical Insurance 2026.pdf — want me to summarize it?"
- `/dropbox` — lists recent files from the watched folder
- `/dropbox sync` — manually trigger a sync check
- Files pulled from Dropbox are ingested to `_file_history` just like Slack uploads

### Implementation

**New file `src/dropbox_sync.py`:**
```python
"""
Dropbox integration for OpenClaw.
Polls a Dropbox folder for new files and notifies Slack.
Requires: pip install dropbox
Config: DROPBOX_ACCESS_TOKEN, DROPBOX_WATCH_PATH (default: /OpenClaw)
"""
import asyncio, logging, time
from pathlib import Path

DROPBOX_POLL_INTERVAL = 30  # seconds

async def list_recent_files(dbx, path: str = "/OpenClaw", count: int = 10) -> list[dict]:
    ...

async def dropbox_watch_loop(slack_client, notify_channel: str):
    """Background loop: poll Dropbox every 30s, DM on new files."""
    ...
```

**Integration into `slack_bot.py`:**
- Start `dropbox_watch_loop` in `create_slack_app()` alongside `_digest_loop`
- Add `/dropbox` slash command handler
- Pass `_file_history` reference so Dropbox files appear in `/files` too

**Manifest entry:**
```json
{"command": "/dropbox", "description": "Browse your Dropbox files", "usage_hint": "[list | sync]"}
```

**Requirements:** Add `dropbox>=12.0.2` to `requirements.txt`

### Tests
- `test_dropbox_sync_disabled_when_no_token`
- `test_dropbox_list_recent_files_returns_list`
- `test_dropbox_cmd_list_formats_response`
- `test_dropbox_cmd_sync_triggers_check`
- `test_dropbox_new_file_sends_dm`

### Done when
- [ ] `src/dropbox_sync.py` created with poller and file lister
- [ ] `/dropbox` command registered and responds
- [ ] Graceful no-op when `DROPBOX_ACCESS_TOKEN` not set
- [ ] Background watcher starts alongside digest loop
- [ ] 5 tests added and passing
- [ ] `dropbox` added to `requirements.txt`
- [ ] Manifest script updated with `/dropbox` entry

---

## Lane D — Docs Update (Chewy 🐻💪)

### Files to update
1. **`docs/PARENTS-GUIDE.md`** — new section "Connecting Your Accounts" with:
   - Gmail setup (App Password, 3-step)
   - Calendar setup (link to `scripts/google_oauth_setup.py`)
   - Dropbox setup (token, watch folder)
   - `/email`, `/calendar`, `/dropbox` command reference
2. **`templates/guide.html`** — add 3 command rows to the command table (Section 72)
3. **`CHANGELOG.md`** — Wave 10 section
4. **`docs/PRODUCT-ROADMAP.md`** — mark Wave 10 as planned/in-progress
5. **`.env.example`** — add `DROPBOX_ACCESS_TOKEN=` and `DROPBOX_WATCH_PATH=` entries with comments

### Done when
- [ ] PARENTS-GUIDE.md has "Connecting Your Accounts" section with setup steps
- [ ] guide.html has 3 new command rows
- [ ] CHANGELOG.md updated
- [ ] .env.example has Dropbox keys with comments
- [ ] `python3 scripts/check_markdown_links.py` passes

---

## Credentials Reference (for user)

### Gmail (simplest — no OAuth)
1. Enable 2-Step Verification on Google account
2. myaccount.google.com → Security → App Passwords → Create (label: "OpenClaw")
3. Add to `/Users/davevoyles/openclaw/.env`:
   ```
   GMAIL_USER=chuck@gmail.com
   GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
   ```
4. `make ship-server` — no manifest change needed

### Google Calendar (OAuth flow required)
1. Run: `python3 scripts/google_oauth_setup.py`
2. Follow browser prompts → adds `GOOGLE_OAUTH_*` vars to `.env`
3. `make ship-server`

### Dropbox (API token)
1. Go to [dropbox.com/developers](https://www.dropbox.com/developers/apps)
2. Create app → "Scoped access" → "Full Dropbox" → name it "OpenClaw"
3. Generate access token → copy it
4. Add to `.env`:
   ```
   DROPBOX_ACCESS_TOKEN=sl.xxxxxxxxxx
   DROPBOX_WATCH_PATH=/OpenClaw
   ```
5. Create folder `~/Dropbox/OpenClaw/` on your computer
6. `make ship-server`

---

## Execution Order

Lanes A, B, C are independent and can run in parallel (different commands, only C creates a new file).  
Lane D runs after A+B+C complete.

Orchestrator merges, runs tests, commits.

## Done-When (overall)
- [ ] 20 new slash commands total (add `/email`, `/calendar`, `/dropbox`)
- [ ] 140+ tests passing (currently 120)
- [ ] All commands degrade gracefully with missing credentials
- [ ] Docs updated and markdown links clean
- [ ] `make slack-manifest` run (browser paste) to register new commands
- [ ] `make ship-server` deployed
