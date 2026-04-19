# Wave 10 — Cloud Account Integrations

**Created:** 2026-04-18  
**Status:** Planning  
**Risk:** Medium — new OAuth token handling, external API calls, new Python dependencies  
**Target:** 132+ tests (4 per lane × 3 lanes, plus existing 120)

---

## User Request

Design improvements around:
- **Dropbox** — parents already use it; eliminate manual file uploads
- **Google** — Gmail summaries, Calendar view (existing `google_oauth_setup.py` in place)
- **Other account integrations** — anything that saves time for non-technical family users

---

## Why This Matters (Benefits)

### Dropbox → OpenClaw Sync

> **Problem:** Parents must remember to upload files to Slack every session.  
> **Solution:** Drop a file in the "Family AI" Dropbox folder → it automatically appears in OpenClaw. Ask questions about it without ever uploading to Slack.

- Zero friction: file sync is passive, not manual
- Existing Dropbox habit — no behavior change required
- Slack notification when a new file arrives: "📦 New file synced from Dropbox: `budget_march.xlsx` — ready to analyze!"

### Gmail → Slack Digest

> **Problem:** Parents open email to triage, then switch to Slack. Two context switches.  
> **Solution:** `/inbox` in Slack shows last 5 unread emails with AI-written one-sentence summaries. Click "📖 Read full" to get the complete email body summarized.

- Saves 5–10 min/day of email triage for non-technical users
- "What needs my attention?" answered in one Slack command
- No email client needed for routine triage

### Google Calendar → Daily Briefing

> **Problem:** "What do I have today?" requires opening Calendar.  
> **Solution:** `/today` in Slack shows today's events in plain English. Bot can also answer "what's on my calendar this week?" in DM.

- Morning routine: one Slack message covers AI + calendar
- Integrates with future `/digest` personalization (add calendar to daily digest)
- Sets foundation for future reminders: "You have a dentist appointment in 2 hours"

---

## Setup Model (Admin Once, Family Forever)

All integrations use a single-account model:
- **Dave sets up tokens once** (runs existing `scripts/google_oauth_setup.py`, gets Dropbox app token)
- **Tokens go in `.env`** — no per-user OAuth flow needed
- **All family members** get access to shared family Dropbox folder + Dave's admin view of shared calendars/Gmail

This is intentional: the family server acts as a shared assistant, not a per-user account bridge.

---

## Wave 10 Plan

### Lane assignment

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|------------|--------|
| 1 | Han 😉🚀 | M | Dropbox sync — auto-poll, `/dropbox list`, file-ready notifications | — | Pending |
| 2 | Yoda 👽✨ | M | Google Calendar — `/today`, `/calendar`, NL trigger in DM | — | Pending |
| 3 | Leia 👑💁‍♀️ | M | Gmail read — `/inbox`, `/email <n>` summarize, Block Kit buttons | — | Pending |

All 3 lanes are fully independent — no cross-dependencies.

---

## Lane 1 — Han 😉🚀: Dropbox Sync

### Goal
Parents drop files into a Dropbox folder called **"Family AI"**. Within 30 minutes, OpenClaw syncs them to `/ai-files`, adds them to `_file_history`, and posts a Slack notification.

### New dependency
```
dropbox>=12.0.0   # add to requirements.txt
```

### New env vars (in `.env` + `config/docker.env.example`)
```
DROPBOX_APP_TOKEN=        # long-lived app token from dropbox.com/developers
DROPBOX_WATCH_FOLDER=/Family AI   # folder path to watch
DROPBOX_NOTIFY_CHANNEL=  # Slack channel ID to post sync notifications
```

### Implementation

**Module-level**
```python
_DROPBOX_TOKEN: str | None = os.getenv("DROPBOX_APP_TOKEN")
_DROPBOX_FOLDER: str = os.getenv("DROPBOX_WATCH_FOLDER", "/Family AI")
_DROPBOX_NOTIFY_CHANNEL: str | None = os.getenv("DROPBOX_NOTIFY_CHANNEL")
_dropbox_cursor: str | None = None   # longpoll cursor, persisted to data/dropbox_cursor.json
```

**`_dropbox_list_folder(path: str) -> list[dict]`**
- Calls Dropbox `files_list_folder(path)` → returns list of `{name, size, server_modified, id}`
- If `_DROPBOX_TOKEN` is None → returns `[]` (graceful no-op)

**`_dropbox_sync_new_files(client) -> int`** (async)
- Uses longpoll cursor approach: `files_list_folder_continue` to get only new files since last sync
- For each new `.docx`, `.pdf`, `.xlsx`, `.txt` file:
  1. Downloads to `data/dropbox_cache/<filename>`
  2. Adds entry to `_file_history[_DROPBOX_VIRTUAL_USER]` with `source="dropbox"`
  3. Posts Slack notification to `DROPBOX_NOTIFY_CHANNEL` if set
- Returns count of new files synced
- Persists cursor to `data/dropbox_cursor.json`

**Background poll loop** (30-min interval, registered in `create_slack_app`)
```python
async def _dropbox_poll_loop():
    while True:
        await asyncio.sleep(1800)  # 30 min
        await _dropbox_sync_new_files(slack_client)
```

**`/dropbox` slash command**
```
/dropbox list        → last 10 files in the watched folder (names + dates)
/dropbox sync        → manual trigger (admin only, or any user)
/dropbox status      → shows whether DROPBOX_APP_TOKEN is configured
```

**Graceful degradation:** all handlers check `_DROPBOX_TOKEN is None` → ephemeral "Dropbox not configured. Ask Dave to set it up."

**Block Kit notification (posted on new file sync)**
```
📦 New file from Dropbox
────────────────────────
📄 budget_march.xlsx  •  synced just now
Use /chat or upload to analyze it.
```

### Tests: `TestDropboxSync` (4 tests)
1. `test_dropbox_list_no_token_returns_empty` — no token → returns `[]`
2. `test_dropbox_slash_status_no_token` — `/dropbox status` → "not configured" message
3. `test_dropbox_slash_list_no_token` — `/dropbox list` → ephemeral not-configured response
4. `test_dropbox_sync_increments_file_history` — mock Dropbox client, 1 new file → `_file_history` entry added

---

## Lane 2 — Yoda 👽✨: Google Calendar

### Goal
`/today` shows today's events in plain English. DM question "what do I have tomorrow?" or "what's on my calendar?" automatically triggers a calendar lookup.

### Dependencies
```
google-api-python-client>=2.100  # add to requirements.txt
```
`google-auth` already installed (used by genai).

### Env vars (already stubbed in docker.env.example)
```
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
GOOGLE_OAUTH_REFRESH_TOKEN=   # obtained via scripts/google_oauth_setup.py
```

### Implementation

**`_get_google_access_token() -> str | None`**
- Posts to `https://oauth2.googleapis.com/token` with client_id, client_secret, refresh_token
- Returns short-lived access token (1 hour TTL)
- Caches token + expiry in module-level `_google_token_cache: dict`
- Returns `None` if env vars missing

**`_get_calendar_events(days_ahead: int = 0) -> list[dict]`** (async)
- Calls Google Calendar API: `events().list(calendarId="primary", ...)`
- Time range: today 00:00 → today 23:59 (or +N days)
- Returns list of `{summary, start, end, location}` dicts
- Returns `[]` if no token

**`_format_calendar_events(events: list[dict]) -> str`**
- Plain-text formatting: "9:00 AM — Dentist (123 Main St)\n2:00 PM — School pickup"
- Returns "Nothing on the calendar today." if empty

**`/today` slash command**
- Fetches today's events → ephemeral Block Kit response
- Header: "📅 Today's schedule" with formatted events

**`/calendar [N]` slash command**  
- Optional: `/calendar 7` → next 7 days (default 7 if no arg)
- Groups by day, ephemeral

**NL trigger in `handle_dm`**
- Detect patterns: `re.search(r"\b(calendar|schedule|appointment|meeting|today|tomorrow)\b", text, re.I)`
- If match AND Google token configured → prepend calendar events to context passed to `_send_answer`
- Natural: "What do I have today?" → bot answer starts with "Here's what's on your calendar today: ..."

**Graceful degradation:** `_GOOGLE_TOKEN` not set → ephemeral "Google Calendar not connected. Ask Dave to set it up."

### Tests: `TestGoogleCalendar` (4 tests)
1. `test_get_calendar_no_token_returns_empty` — no env vars → `_get_calendar_events()` returns `[]`
2. `test_format_calendar_events_empty` — empty list → "Nothing on the calendar" message
3. `test_format_calendar_events_one_event` — 1 event dict → formatted string with time
4. `test_today_command_no_token_graceful` — `/today` with no token → "not configured" ephemeral

---

## Lane 3 — Leia 👑💁‍♀️: Gmail Read

### Goal
`/inbox` shows last 5 unread emails (subject + sender + time). Each email has a "📖 Summarize" button. `/email 1` summarizes email #1 from the list.

### Dependencies
Same `google-api-python-client>=2.100` (shared with Lane 2 — add once to requirements.txt).

### Env vars
Same `GOOGLE_OAUTH_*` vars from Lane 2. Gmail read scope already included in `google_oauth_setup.py`.

### Implementation

**`_get_gmail_unread(max_results: int = 5) -> list[dict]`** (async)
- Calls Gmail API: `users.messages.list(userId="me", labelIds=["UNREAD", "INBOX"], maxResults=max_results)`
- For each message ID, fetches headers only: `messages.get(format="metadata", metadataHeaders=["Subject","From","Date"])`
- Returns list of `{id, subject, from_name, from_email, date_str}`
- Returns `[]` if no token

**`_get_gmail_body(message_id: str) -> str`** (async)
- Fetches full message: `messages.get(format="full")`
- Extracts text/plain or text/html (stripped) body, up to 4000 chars
- Returns extracted text or "(empty email)"

**`/inbox` slash command**
- Fetches unread → Block Kit ephemeral with each email as a section:
  ```
  📧 From: Mom <mom@gmail.com>  ·  2h ago
  "Family reunion planning — here's what I was thinking..."
  [📖 Summarize]
  ```
- Block Kit button action_id: `gmail_summarize_{message_id}`

**`@app.action("gmail_summarize_*")` handler** (wildcard pattern on action_id prefix)
- Extracts message_id from action_id
- Fetches body via `_get_gmail_body()`
- Passes to `_ask()` with prompt: "Summarize this email in 3 bullet points:\n\n{body}"
- Posts summary as ephemeral in the channel

**`/email <n>` slash command**
- Fetches inbox (same as `/inbox`), picks item N (1-indexed)
- Fetches body → sends to AI → posts summary

**Graceful degradation:** no token → "Gmail not connected."

**Privacy note:** Email body is passed to the configured AI model (Gemini/OpenAI). Bodies are NOT stored to disk; only used transiently for summarization.

### Tests: `TestGmailRead` (4 tests)
1. `test_get_gmail_unread_no_token_returns_empty` — no env vars → returns `[]`
2. `test_format_inbox_empty` — empty list → "No unread emails" message
3. `test_inbox_command_no_token_graceful` — `/inbox` with no token → "not configured" ephemeral
4. `test_get_gmail_body_truncates_long_body` — body > 4000 chars → truncated at 4000

---

## Wave 11 Preview (Future — Not Implementing Now)

| Feature | Value | Effort | Notes |
|---------|-------|--------|-------|
| Google Drive file browser | `/drive list`, `/drive open filename` | M | Uses same Google token |
| Dropbox write-back | "Save edited file back to Dropbox" button | M | Requires `files_upload` scope |
| Gmail draft/send | Draft reply from Slack | L | High risk — needs careful UX |
| iCloud Calendar (CalDAV) | For Mom specifically | M | No OAuth SDK; pure CalDAV |
| Google Contacts | "Who is Dr. Smith?" → contacts lookup | S | Read-only, same token |
| Twilio SMS alerts | "Text me when a new file arrives" | M | `twilio` already in requirements.txt |
| Apple Reminders (mac-mini) | Sync reminders via macOS API | L | Local Mac Mini only |

---

## New Dependencies (requirements.txt)

```
dropbox>=12.0.0                  # Dropbox API SDK
google-api-python-client>=2.100  # Google Calendar + Gmail API
```

## New Env Vars

| Variable | Used by | How to get |
|----------|---------|-----------|
| `DROPBOX_APP_TOKEN` | Lane 1 | dropbox.com/developers → App Console → long-lived token |
| `DROPBOX_WATCH_FOLDER` | Lane 1 | Folder path in Dropbox (default: `/Family AI`) |
| `DROPBOX_NOTIFY_CHANNEL` | Lane 1 | Slack channel ID for sync notifications |
| `GOOGLE_OAUTH_CLIENT_ID` | Lanes 2+3 | console.cloud.google.com |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Lanes 2+3 | Same project |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | Lanes 2+3 | Run `scripts/google_oauth_setup.py` |

All vars are optional — missing vars → graceful "not configured" UX.

---

## Validation

```bash
cd /Users/davevoyles/openclaw
pip3 install dropbox google-api-python-client
ruff check src/slack_bot.py                          # 0 errors
python3 -m pytest tests/test_slack_bot.py -q         # 132+ tests
python3 scripts/check_markdown_links.py              # clean
```

## Documentation Updates After Wave 10

- `docs/PARENTS-GUIDE.md`: 3 new sections (Dropbox, Calendar, Gmail)
- `templates/guide.html`: bump to v0.17.0, add cloud integrations section
- `config/docker.env.example`: uncomment + annotate new vars
- `scripts/update_slack_manifest.py`: add `/dropbox`, `/today`, `/calendar`, `/inbox`, `/email` commands
- `.github/docs/slack-app-manifest.md`: add 5 new commands to table

## Done When

- [ ] Lane 1: Dropbox poll loop, `/dropbox` command, file sync to `_file_history`, 4 tests pass
- [ ] Lane 2: `_get_google_access_token`, `_get_calendar_events`, `/today`, `/calendar`, NL trigger, 4 tests pass
- [ ] Lane 3: `_get_gmail_unread`, `_get_gmail_body`, `/inbox`, `/email`, Block Kit summarize button, 4 tests pass
- [ ] 132+ tests passing, ruff 0 errors
- [ ] New deps added to `requirements.txt`
- [ ] New env vars added to `config/docker.env.example`
- [ ] `scripts/update_slack_manifest.py` updated with 5 new commands
- [ ] PARENTS-GUIDE.md + guide.html v0.17.0 updated
- [ ] Pushed to main, CI green

---

## Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| 18:29 | — | Orchestrator | 📋 Wave 10 plan written, awaiting user approval |
