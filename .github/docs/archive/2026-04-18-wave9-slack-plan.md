# Wave 9 — Slack Parent Experience

**Created:** 2026-04-18  
**Status:** Planning  
**Risk:** Medium — multi-file feature edits, new Slack event type (App Home)  
**Discord:** Ignore going forward — all new features Slack-only

---

## User Request

Focus next wave on Slack improvements for parents (Chuck = Dad, Lisa = Mom):

1. **User identity** — bot knows who each person is, greets them by name
2. **Interactive clarification** — bot prompts for more info when questions are vague
3. **Persistent wiki** — always-accessible reference guide inside Slack (App Home tab)
4. **Slack feature gaps** — anything else we're missing

---

## Current State

- User prefs stored in `data/slack_user_prefs.json` (keyed by Slack user_id)
- No name/persona mapping — bot has no idea "U_ABC123" is Chuck
- No clarification prompting — vague questions get vague answers
- No App Home tab (`app_home_opened` NOT in manifest bot_events)
- `_check_new_user_onboarding()` sends a welcome DM but uses no name
- 109 tests passing, ruff clean

---

## Wave 9 Plan

### Lane assignment

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|------------|--------|
| 1 | Han 😉🚀 | M | User personalization — `/nickname`, name greeting, persona store | — | Pending |
| 2 | Yoda 👽✨ | M | Clarification prompting — vague question detection + quick-reply buttons | — | Pending |
| 3 | Leia 👑💁‍♀️ | M | App Home wiki tab — persistent command reference + personalized hub | — | Pending |

All 3 lanes are independent — no cross-dependencies.

---

## Lane 1 — Han 😉🚀: User Personalization

### Goal
When Chuck sends a message, the bot says "Hi Chuck!" and uses his name naturally. No more generic "Hi there!" responses.

### Implementation

**Data: `data/slack_user_personas.json`**
```json
{
  "U_CHUCK_ID": {"name": "Chuck", "role": "Father"},
  "U_LISA_ID":  {"name": "Lisa",  "role": "Mother"}
}
```
- Load/save helpers: `_load_personas()` / `_save_personas()`
- `_get_user_name(user_id, client) -> str` async helper:
  1. Check `_personas` cache first → return stored name
  2. Else call `client.users_info(user=user_id)` → extract `real_name` or `display_name`
  3. Store in personas file for next time
  4. Fallback: return `"there"` (produces "Hi there!")

**`/nickname <name>` command**
- Sets preferred display name: `_personas[user_id]["name"] = name`
- Confirms: "✅ Got it! I'll call you **Chuck** from now on."
- Register in `scripts/update_slack_manifest.py`

**Greeting integration**
- In `handle_dm`: call `_get_user_name()` once; include name in first reply when thread is new
- In onboarding DM: personalize "Welcome, Chuck! Here's how to get started..."
- Keep greeting lightweight — don't force name into every message, just new threads

### Tests: `TestUserPersonalization` (4 tests)
1. `test_persona_fallback_returns_there` — no stored name, no API → returns "there"
2. `test_persona_stored_name_returned` — stored name in personas → returned directly
3. `test_nickname_command_stores_name` — `/nickname Chuck` writes to personas
4. `test_get_user_name_uses_cache` — stored name returned without API call

---

## Lane 2 — Yoda 👽✨: Clarification Prompting

### Goal
When a parent sends a vague question like "help" or "can you look at this?", the bot gently asks what they need instead of guessing or giving a generic answer.

### Implementation

**`_is_vague_question(text: str, has_files: bool) -> bool`**
- Returns True if ALL of:
  - `len(text.split()) < 6` words
  - `has_files` is False (no attachment that clarifies context)
  - text matches any vague pattern: `["help", "hi", "hello", "hey", "this", "it", "stuff", "can you", "?"]` alone or nearly alone

**Clarification Block Kit message** (ephemeral, in the DM channel):
```
Hi! I want to make sure I help you well. What would you like to do?

[📄 Ask about a file]  [💬 Ask me anything]  [📝 Help me write something]
```

**Action handlers** (`@app.action`):
- `clarify_file` → ephemeral: "Go ahead and upload your file, then type your question!"
- `clarify_question` → ephemeral: "Of course! What would you like to know?"
- `clarify_write` → ephemeral: "Happy to help! What are you working on — a letter, email, list, or something else?"

**Integration**: In `handle_dm`, before calling `_send_answer`:
```python
if _is_vague_question(text, has_files=bool(files)):
    await _post_clarification_prompt(client, channel, user_id)
    return  # wait for their reply
```

### Tests: `TestClarificationPrompts` (4 tests)
1. `test_vague_single_word_detected` — "help" → `_is_vague_question` returns True
2. `test_not_vague_with_context` — "summarize my budget spreadsheet" → returns False
3. `test_not_vague_when_files_present` — "this" + has_files=True → returns False
4. `test_not_vague_longer_question` — sentence > 6 words → returns False

---

## Lane 3 — Leia 👑💁‍♀️: App Home Wiki Tab

### Goal
Every parent can tap "OpenClaw" in Slack, go to the **Home** tab, and see their personal command reference — always up to date, always there.

### Implementation

**Manifest changes (in `scripts/update_slack_manifest.py`)**:
- Add `"app_home_opened"` to `bot_events`
- Add `"features": {"app_home": {"home_tab_enabled": true, ...}}`

**`_build_home_view(user_id: str, name: str) -> dict`**
Returns a Block Kit Home view dict with:
- **Header**: "👋 Hi Chuck! Welcome to your OpenClaw hub"
- **Divider**
- **Quick commands section** — markdown block with all 16 slash commands in 2-column table
- **Divider**
- **Recent files section** — last 3 from `_file_history[user_id]` (names + dates)
- **Footer** — "📖 Full guide: `/help` · Questions? Just message me!"

**Event handler**:
```python
@app.event("app_home_opened")
async def handle_app_home_opened(event, client):
    user_id = event["user"]
    name = await _get_user_name(user_id, client)
    view = _build_home_view(user_id, name)
    await client.views_publish(user_id=user_id, view=view)
```

**Scopes needed**: `app_home:write` (may require manifest update + re-auth)

**`docs/PARENTS-GUIDE.md`** — add section:
> "## 🏠 Your OpenClaw Home Tab
> Tap OpenClaw's name in Slack, then tap **Home**. You'll see your personal command guide and recent files — always there, always up to date."

### Tests: `TestAppHome` (4 tests)
1. `test_build_home_view_contains_header` — view has correct type "home"
2. `test_build_home_view_personalized` — name appears in header text
3. `test_build_home_view_has_commands` — key commands listed in blocks
4. `test_vague_is_vague_empty_string` — empty string → vague (edge case)

---

## Validation

```bash
cd /Users/davevoyles/openclaw
ruff check src/slack_bot.py          # 0 errors
python3 -m pytest tests/test_slack_bot.py -q  # 121+ tests passing
python3 scripts/check_markdown_links.py       # clean
```

## Documentation updates after Wave 9
- `docs/PARENTS-GUIDE.md`: App Home tab section + `/nickname` section
- `templates/guide.html`: update Slack section with App Home + nickname command
- `scripts/update_slack_manifest.py`: `/nickname` + `app_home_opened` event + `home_tab_enabled`

## Done when
- [x] Lane 1: `_get_user_name()`, `/nickname`, persona store, 4 tests pass
- [ ] Lane 2: `_is_vague_question()`, clarification Block Kit, action handlers, 4 tests pass
- [ ] Lane 3: `app_home_opened` handler, `_build_home_view()`, manifest updated, 4 tests pass
- [ ] 121+ tests passing, ruff 0 errors
- [ ] Manifest updated in `scripts/update_slack_manifest.py`
- [ ] PARENTS-GUIDE.md + guide.html updated
- [ ] Pushed to main, CI green

---

## Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| 18:08 | — | Orchestrator | 📋 Wave 9 plan written, awaiting user approval |
| 18:10 | — | Orchestrator | ✅ Approved. Launching 3-lane fleet. Baseline: 109 tests. |
| 18:10 | 1 | Han 😉🚀 | 📋 Launched: user personalization — _personas, _get_user_name, /nickname |
| 18:12 | 1 | Han 😉🚀 | ✅ DONE: _personas store, _get_user_name, /nickname handler, 4 tests added, 117 passing, ruff clean |
| 18:10 | 2 | Yoda 👽✨ | 📋 Launched: clarification prompting — _is_vague_question, Block Kit prompt, 3 action handlers |
| 18:12 | 2 | Yoda 👽✨ | ✅ DONE: 4 tests added, 113 passing, ruff clean |
| 18:10 | 3 | Leia 👑💁‍♀️ | 📋 Launched: App Home wiki tab — _build_home_view, app_home_opened event, manifest updates |
| 18:30 | 1 | Han 😉🚀 | ✅ Complete: _PERSONAS_PATH, _personas, _load_personas, _save_personas, _get_user_name, /nickname handler, personalized onboarding DM, /nickname in manifest, TestUserPersonalization (4 tests). 117/117 tests passing, ruff clean. |
| — | 2 | Yoda 👽✨ | ✅ Done: _is_vague_question + _VAGUE_PATTERNS + _post_clarification_prompt added; 3 clarify_* action handlers in create_slack_app(); vague check integrated in handle_dm (non-thread only); TestClarificationPrompts 4/4 passing; ruff 0 errors; 113 tests passing |
| — | 3 | Leia 👑💁‍♀️ | ✅ Done: _build_home_view() added before create_slack_app(); handle_app_home_opened event handler registered inside create_slack_app(); app_home_opened added to bot_events in manifest; app_home feature flags (home_tab_enabled, messages_tab_enabled) added to features in manifest; TestAppHome 4/4 passing; ruff 0 errors; 120 tests passing |

