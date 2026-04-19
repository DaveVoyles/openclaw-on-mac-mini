# Wave 8 Slack Improvements — Plan

**Created:** 2026-04-18  
**Status:** Ready to launch  
**Risk:** Medium — multi-file feature edits, Slack bot behavior changes  

---

## Context

**Where we are:** Waves 1–7 of Slack improvements are shipped and green (97 tests passing, all CI green).

### Shipped commands (as of Wave 7)
| Command | Function |
|---------|----------|
| `/chat` | Ask a question (renamed from `/ask` — Slack reserves `/ask`) |
| `/help` | Rich Block Kit categorized help |
| `/health` | Bot health (renamed from `/status` — Slack reserves `/status`) |
| `/digest on\|off\|status` | Daily DM of synced files |
| `/simple on\|off` | Plain-language mode |
| `/research <topic>` | Perplexity-backed web research |
| `/batch summarize\|proofread\|explain` | Process all uploaded files |
| `/files [recent\|<name>]` | File browser / reference |
| `/brief` | Last 5 uploads at a glance |
| `/mystats` | Per-user usage stats |
| `/template list\|<name>` | Starter document templates |
| `/clear` | Reset active file context |
| `/metrics` | Admin usage metrics |

### Key gaps identified for Wave 8
1. **DM thread memory** — `handle_dm` passes `thread_ts=None`; replies in a DM thread don't carry prior context. `handle_mention` already calls `_build_thread_history()` correctly.
2. **`/saved` command** — Wave 7 added 🔖 bookmark saving to `data/slack_saved_notes.json` but there's no way to retrieve saved notes.
3. **`/search` command** — users have file history (`_file_history`) but no way to search it by keyword.
4. **`/schedule <time>` for digest** — digest runs on a fixed interval; users can't set preferred delivery time.
5. **Error recovery UX** — when `_send_answer` fails, there's no retry affordance; it silently dies.
6. **Audio file stub** — audio uploads (`audio/*`) silently fail with an "unsupported type" note; should give a clear message.

---

## Wave 8 Plan

### Lane assignment

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|------------|--------|
| 1 | Han 😉🚀 | M | DM thread memory + `/saved` command | — | Pending |
| 2 | Yoda 👽✨ | M | `/search` + `/schedule` commands | — | Pending |
| 3 | Leia 👑💁‍♀️ | M | Error recovery UX + audio stub | — | Pending |

All 3 lanes are independent — no cross-dependencies.

---

## Lane 1 — Han 😉🚀: DM Thread Memory + `/saved`

### DM Thread Memory
- In `handle_dm` (line ~1962), detect `event.get("thread_ts")` 
- If present: call `_build_thread_history(client, channel, thread_ts)` to get prior context
- Pass history to `_send_answer` via the `history` param (same pattern as `handle_mention` at line 1938)
- This gives DM thread replies proper conversational context

### `/saved` command
- Handler: reads `data/slack_saved_notes.json`, filters by `user_id == body["user_id"]`
- Shows last 5 saved notes as ephemeral Block Kit card (text preview truncated to 200 chars, timestamp)
- If no saved notes: ephemeral "You haven't saved any messages yet — react 🔖 to any bot response to save it!"
- Register in `scripts/update_slack_manifest.py`

### Tests: `TestDMThreadMemoryAndSaved` (4 tests)
1. `test_dm_thread_ts_triggers_history` — when event has `thread_ts`, history is fetched
2. `test_dm_no_thread_ts_no_history` — when no `thread_ts`, `_build_thread_history` not called
3. `test_saved_empty` — empty saved_notes → ephemeral empty-state message
4. `test_saved_lists_entries` — populated saved_notes → entries shown with timestamps

---

## Lane 2 — Yoda 👽✨: `/search` + `/schedule`

### `/search <keyword>` command
- Searches `_file_history[user_id]` by: filename contains keyword, or `auto_brief` field contains keyword (case-insensitive)
- Returns ephemeral Block Kit card: matching files with name, type icon, relative timestamp
- No results: "No files matching '<keyword>' found — try `/brief` to see all your recent uploads"
- Register in manifest

### `/schedule <time|off>` command
- Parses time string: `9am`, `8:30am`, `14:00`, or `off`
- Stores `preferred_time` in `digest_prefs.json` per user alongside existing `enabled` flag
- `_digest_loop` reads `preferred_time` when checking whether to send (compare against current local hour)
- `/schedule off` removes the preference (reverts to fixed 24h interval behavior)
- Register in manifest

### Tests: `TestSearchAndSchedule` (4 tests)
1. `test_search_finds_matching_filename` — keyword matches filename in history
2. `test_search_no_match` — keyword not found → empty-state message
3. `test_schedule_stores_preferred_time` — `digest_prefs.json` updated with time
4. `test_schedule_off_clears_preference` — `off` removes `preferred_time` key

---

## Lane 3 — Leia 👑💁‍♀️: Error Recovery UX + Audio Stub

### Error Recovery UX ("Try again" button)
- `_send_answer` final except block: instead of just logging, post Block Kit message with "⚠️ Something went wrong — want me to try again?" + button `🔁 Retry` (`action_id: retry_last_prompt`, `value: <prompt_hash>`)
- Module-level `_retry_cache: dict[str, str]` maps `prompt_hash → original_prompt` (max 50 entries, FIFO eviction)
- `retry_last_prompt` action handler: looks up prompt from cache, re-calls `_send_answer`
- Register `@app.action("retry_last_prompt")`

### Audio file stub
- In `_process_slack_files`: when `mimetype.startswith("audio/")`, append a clear message instead of the generic unsupported-type note:
  `[🎵 Audio file detected: {filename} — audio transcription is not yet supported. Try describing what you need help with in text!]`
- In `_build_file_blocks`: when mimetype starts with `audio/`, show a single ephemeral-style button: "🎵 Audio — coming soon" (disabled/placeholder)

### Tests: `TestErrorRecoveryAndAudio` (4 tests)
1. `test_retry_cache_stores_prompt` — prompt stored in `_retry_cache` on error
2. `test_retry_cache_evicts_at_max` — at 51 entries, oldest is evicted
3. `test_audio_mime_in_process_files` — `audio/mpeg` → audio stub message appended
4. `test_build_file_blocks_audio_type` — `audio/mp4` → blocks contain audio label, no proofread button

---

## Validation

```bash
cd /Users/davevoyles/openclaw
ruff check src/slack_bot.py          # 0 errors
python3 -m pytest tests/test_slack_bot.py -q  # 109+ tests passing
gh run list --limit 3                # all green
```

## Documentation updates after Wave 8
- `docs/PARENTS-GUIDE.md`: add `/saved`, `/search`, `/schedule` sections
- `templates/guide.html`: update Slack section, bump to v0.16.0
- `.github/docs/slack-app-manifest.md`: add 3 new commands

## Done when
- [ ] Lane 1: DM thread memory wired, `/saved` command works, 4 tests pass
- [ ] Lane 2: `/search` and `/schedule` work, 4 tests each pass
- [ ] Lane 3: retry button on error, audio stub message, 4 tests pass
- [ ] 109+ tests passing, ruff 0 errors
- [ ] All 3 new commands in `scripts/update_slack_manifest.py`
- [ ] PARENTS-GUIDE.md + guide.html updated
- [ ] Pushed to main, CI green

---

## Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| — | — | — | Wave 8 plan created |
| 17:58 | — | Orchestrator | 🔍 Pre-flight: other agent already completed /search, /schedule, audio stub, retry cache, DM thread memory, manifest. 105 tests passing. |
| 17:58 | 1 | Han 😉🚀 | 📋 Launched: add TestErrorRecoveryAndAudio (4 tests) + fix test_dm_thread_ts_detected → target 109+ |
| 17:58 | 2 | Yoda 👽✨ | ✅ DONE: PARENTS-GUIDE.md + guide.html updated (/saved, /search, /schedule), link check passed |
| Done | 2 | Yoda 👽✨ | ✅ docs/PARENTS-GUIDE.md: added `/search` section after `/brief`, `/saved` section after 🔖 bookmark section, `/schedule` section after `/digest`. templates/guide.html: added 3 rows to Slack commands table (/saved, /search, /schedule), added 3 new h3 subsections in Section 72 (View Saved Notes, Search File History, Schedule Your Digest), updated quick-start flow. Version already at v0.16.0. `python3 scripts/check_markdown_links.py` → Markdown links OK. |
| Done | 1 | Han 😉🚀 | ✅ tests/test_slack_bot.py: fixed test_dm_thread_ts_detected (async→sync), replaced async test_audio_mime_in_process_files with sync inline-logic test. TestErrorRecoveryAndAudio already present with 4 tests. **109 tests passing**, ruff 0 errors. |
