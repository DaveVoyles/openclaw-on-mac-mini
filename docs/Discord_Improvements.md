# Discord Bot Improvements Roadmap

> **Status:** ✅ All 14 waves shipped (April 2026)
> **Last Updated:** April 2026
> **Baseline:** Discord channel personas removed; current date injected into system prompt

This document captures all identified improvement opportunities for the OpenClaw Discord bot,
organized into implementation waves ordered by impact and dependency. Each wave is self-contained
and can be shipped independently.

**All waves have been implemented.** See commit history for details. Deploy with:
```bash
ssh 192.168.1.93 "DOCKER_CONFIG=/tmp/dockercfg /usr/local/bin/docker-compose build openclaw && /usr/local/bin/docker-compose up -d openclaw"
```

---

## Background

A fleet of four parallel agents audited the bot across four domains:

| Agent | Domain | Findings |
|-------|--------|----------|
| `discord-output-quality` | Response formatting, sources, tables, embeds | 6 findings |
| `discord-conversation-memory` | Threads, history, recall, sessions | 10 findings |
| `discord-ux-commands` | Slash commands, onboarding, errors, mobile | 16 findings |
| `discord-reliability-intelligence` | Routing, rate limits, tools, streaming | 18 findings |

**Total: 50 actionable findings** organized into 14 waves below.

---

## Wave 1 — Onboarding & First-Run Experience

**Goal:** Make the first interaction with the bot welcoming and useful instead of a blank slate.

**Problem:** `src/onboarding.py` contains a fully-built `OnboardingManager` with rich welcome embeds
and multi-step tutorial flows — but it is **never called anywhere**. New users land in a bot with
110 slash commands and no guidance.

### Tasks

- **W1-1** Hook `on_member_join()` in `src/bot.py` → call `OnboardingManager.send_welcome_message()`
  to DM new guild members with a welcome embed and quick-start tips.
- **W1-2** Track first-ever `/ask` per user (keyed by `user_id` in a lightweight JSON store or
  the existing memory store). On first use, append a one-time tip: *"💡 Run `/help` to explore
  all commands, or `/tutorial start` for a guided walkthrough."*
- **W1-3** Add `/tutorial [start|skip|next|restart]` commands in `src/discord_commands/utility.py`
  that drive users through `OnboardingManager` steps interactively.
- **W1-4** Add a "👋 First time?" option to the `/help` dropdown
  (`src/discord_commands/utility.py` lines 140–180) pointing to `/tutorial start`.

**Files:** `src/bot.py`, `src/onboarding.py`, `src/discord_commands/utility.py`
**Effort:** Medium — logic already written; needs hookup and slash commands.

---

## Wave 2 — Error Message Standardization

**Goal:** Every failure, at every command, gives the user a clear, actionable, consistently
formatted message — never a bare Python exception.

**Problem:** `/ask` has excellent category-specific error messages with trace IDs
(`src/ask_handler.py` lines 437–445), but most other commands catch bare exceptions and
either expose internal details or silently fail. Error messages are also inconsistently
ephemeral vs. channel-visible.

### Tasks

- **W2-1** Create `src/discord_error.py` — a shared error formatting module with:
  - `format_bot_error(e, context, trace_id)` → returns a standardized Discord embed
  - Error categories: `timeout`, `rate_limit`, `auth`, `tool_failure`, `provider`, `general`
  - Always uses `discord.utils.escape_markdown()` to sanitize exception text
- **W2-2** Replace bare `except` blocks in all cogs (`src/cogs/`) with calls to
  `format_bot_error()`. Grep target: `await interaction.followup.send(f"❌ {e}")`.
- **W2-3** Standardize `ephemeral=True` on all error responses. Errors must never pollute
  the channel — only the affected user should see them.
- **W2-4** For authorization failures (`src/discord_commands/_helpers.py` line 43), enhance
  message to: *"🔒 You don't have access to this command. Run `/whoami` to check your
  permission level, or contact your admin."*

**Files:** `src/discord_error.py` (new), `src/discord_commands/_helpers.py`,
`src/cogs/*.py`, `src/response_actions.py`
**Effort:** Medium — mechanical but touches many files; good for a fleet sub-agent.

---

## Wave 3 — Permission & Auth Visibility

**Goal:** Users understand exactly what they can do and why they're blocked, without needing
to ask an admin.

**Problem:** The `PermissionLevel` enum (`src/permissions.py` lines 86–92) defines `PUBLIC`,
`MEMBER`, `TRUSTED`, `ADMIN`, `OWNER` levels, but `/whoami` only shows "Authorized / Not
Authorized" — users can't see their level or which commands it unlocks.

### Tasks

- **W3-1** Update `/whoami` (`src/discord_commands/utility.py` lines 70–82) to display:
  - Current permission level (e.g., `MEMBER`)
  - Which command categories are available at that level
  - Which categories require higher access
- **W3-2** When a command is blocked by permission, include the *required* level in the error:
  *"🔒 This command requires `ADMIN` access. Your level: `MEMBER`."*
- **W3-3** Add `/permissions list` command that shows a table of all permission levels and
  the command categories they unlock — useful for admins setting up new users.

**Files:** `src/permissions.py`, `src/discord_commands/utility.py`
**Effort:** Small — UI change only; no logic changes.

---

## Wave 4 — Output Quality Fixes

**Goal:** Responses are clean, never duplicated, and sources are properly formatted.

**Problems (with root causes):**

- **Duplicate sources** — `src/openclaw_cli_preprocess.py` has `_preprocess_response_text()`
  that extracts and strips sources from the body before rendering — but this function is only
  used by the CLI. The Discord path (`src/ask_handler.py` lines 419–423) lets sources remain
  in the response body, then `_build_ask_recovery_block()` can append them again in the footer.

- **URL mangling** — Source URLs like `36mai-assistant-capabilities.htmlhttps://www.adobe.com/...`
  are created by a loose source-extraction regex in `src/openclaw_cli_preprocess.py` lines
  319–327. The display text (filename) is concatenated directly with the URL without validation.

- **Embed inconsistency** — `/ask` embeds only set `author` on the first chunk and `footer`
  on the last chunk (`src/ask_handler.py` lines 634–690). Plain-message responses
  (`src/discord_events.py` line 459) only set `description` and `color` — no author, no
  footer, no title. `src/builders/embed_builder.py` exists but is not used by the main flow.

- **Follow-up suggestion quality** — `_generate_follow_ups()` in `src/response_actions.py`
  lines 414–429 only sees 300 chars of the question and 500 chars of the response, uses
  fragile newline parsing instead of structured JSON, has no validation filter for generic
  answers ("Can you explain more?"), and silently produces empty results on parse failure.

### Tasks

- **W4-1** **Duplicate source fix:** Before splitting response text into Discord chunks,
  call (or inline) `_preprocess_response_text()` from `openclaw_cli_preprocess.py` to extract
  the sources block. Render sources as a dedicated embed field or send separately — never
  as body text. Add a guard: if `response_text` already contains a `Sources:` header,
  skip appending the recovery block's source section.

- **W4-2** **URL mangling fix:** In the source-extraction regex (`openclaw_cli_preprocess.py`
  line 332), add a post-processing step: for each extracted URL, apply
  `re.sub(r'^.*?(https?://)', r'\1', url)` to strip any filename prefix. Validate that the
  cleaned URL starts with `http`. Apply the same cleanup in `bot_formatting.py`'s
  source formatter.

- **W4-3** **Source relevance filter:** Before displaying sources, filter out any URL whose
  domain appears unrelated to the query terms (e.g., `adobe.com` when the query is about
  box office results). A simple domain-keyword overlap check against the top query nouns
  is sufficient.

- **W4-4** **Embed consistency via shared builder:** Create a `_build_response_embed()` helper
  (or use `src/builders/embed_builder.py`) that always sets: `author` (user avatar + name),
  `footer` (model + latency + chunk indicator `[1/3]`), `timestamp`, and semantic color
  (`purple` for AI, `orange` for warnings, `red` for errors). Apply to both `/ask` and
  plain-message flows.

- **W4-5** **Follow-up quality:** Increase context windows to 800/1,200 chars. Switch to
  structured JSON output (`{"follow_ups": ["q1", "q2"]}`). Add a validation filter to reject
  generic phrases. Cap at 3 follow-up buttons. Fall back gracefully if JSON parse fails.

**Files:** `src/ask_handler.py`, `src/openclaw_cli_preprocess.py`, `src/bot_formatting.py`,
`src/response_actions.py`, `src/discord_events.py`, `src/builders/embed_builder.py`
**Effort:** Small-to-medium.

---

## Wave 5 — Thread & Conversation Continuity

**Goal:** Users never lose a conversation to an archived thread. Session state persists
predictably across reconnects.

**Problem:** `_is_reusable_bot_thread()` (`src/discord_events.py` line 137) explicitly rejects
archived threads — if Discord auto-archives a thread mid-conversation, the next message creates
a brand new thread with no history. The in-memory conversation TTL (30 min) is shorter than
the disk-persist TTL, creating inconsistent behavior after reconnects.

### Tasks

- **W5-1** **Archived thread recovery:** When a new thread would be created but an archived
  matching thread exists, unarchive it via `thread.edit(archived=False)` before reusing it.
  Fall back to new thread only if unarchive fails.
- **W5-2** **Archive warning:** When a thread is nearing its `auto_archive_duration`, send a
  one-time message: *"⚠️ This thread will auto-archive in 10 minutes. Start a new `/ask` or
  keep chatting to extend it."*
- **W5-3** **TTL alignment:** Align `CONTEXT_TTL` (in-memory, `src/memory_helpers.py`) with
  `cfg.conversation_ttl_minutes` (disk persist, `src/memory_thread_persistence.py`) so users
  get consistent context windows regardless of whether the process restarted.
- **W5-4** **Resume hint:** When a user's first message in a new thread matches a recent saved
  conversation (via semantic search), proactively offer: *"💡 This looks related to your
  thread **{name}**. Use `/resume {name}` to continue it."*

**Files:** `src/discord_events.py`, `src/memory_helpers.py`, `src/memory_thread_persistence.py`
**Effort:** Medium.

---

## Wave 6 — Memory Recall Accuracy

**Goal:** Recalled context is always relevant. No false positives from over-broad domain guards.
No duplicate memories cluttering injection.

**Problem:** The vector recall guard (`src/vector_store_memory.py` lines 405–443) uses a flat
`0.7` cosine-similarity threshold, but sports/WWE domain suppression is triggered too broadly
(any token overlap causes suppression even for factual questions about those topics). Cross-session
deduplication only runs within-store, not across sessions.

### Tasks

- **W6-1** **Threshold tuning:** Make the similarity threshold query-type-aware: factual queries
  (who/what/when) use `0.75`; conversational queries use `0.65`. Configurable in `config.yaml`.
- **W6-2** **Domain suppression fix:** Replace the sports/WWE string-match guard with a proper
  topic classifier. Only suppress recall when the *current query* is off-topic for the recalled
  memory's domain — not when the memory mentions a keyword.
- **W6-3** **Cross-session deduplication:** When loading recalled memories for context injection,
  de-duplicate by semantic similarity (≥0.9) across the top-K results before injecting, so the
  LLM doesn't see the same fact repeated 3 times.
- **W6-4** **Recall transparency:** When memories are injected, add a collapsible embed field
  or ephemeral note showing which memories were used (title + age). Helps users understand why
  the bot remembered something.

**Files:** `src/vector_store_memory.py`, `src/vector_store_client.py`, `src/llm/context.py`
**Effort:** Medium — requires careful testing of recall quality.

---

## Wave 7 — Progress Indicators for All Commands

**Goal:** Every long-running operation shows live progress — not just `/ask`.

**Problem:** `/ask` has an excellent `_think()` progress-update pattern with real-time embed
edits every 3 seconds showing tool calls and timing. Most other commands — `/research`,
`/report`, `/incident start`, docker operations — just show Discord's built-in spinner with
no feedback for operations that take 15–60 seconds.

### Tasks

- **W7-1** Extract the `_think()` / `_update_progress()` pattern from `src/ask_handler.py`
  into a shared `src/discord_progress.py` module that any command can import.
- **W7-2** Add progress updates to `/research` cog: show each search sub-query as it runs,
  e.g., *"🔍 Searching: box office weekend results… | 🔍 Searching: film revenue April 2026…"*
- **W7-3** Add progress updates to Docker commands (`src/cogs/docker_cog.py`): show each
  container status check as it completes when running `/status` or `/health`.
- **W7-4** Add progress updates to `/report` and `/incident start` cogs, showing each data
  collection phase.

**Files:** `src/discord_progress.py` (new), `src/ask_handler.py`, `src/cogs/research_cog.py`,
`src/cogs/docker_cog.py`, `src/cogs/reports_cog.py`, `src/cogs/incident_cog.py`
**Effort:** Medium — new module is the hard part; wiring into cogs is mechanical.

---

## Wave 8 — Mobile Discord UX

**Goal:** Every response is readable on a phone screen without horizontal scrolling.

**Problem:** Markdown tables can exceed mobile viewport width (tables with 6+ columns are
horizontally scrollable and hard to read). Long code blocks with no line breaks scroll off-screen.
Feedback buttons (row 1) may be below the fold on narrow viewports.

### Tasks

- **W8-1** **Table auto-downgrade:** In `src/bot_formatting.py`, if a rendered markdown table
  exceeds 60 characters wide, automatically trigger the PNG image renderer
  (`src/table_renderer.py`) rather than returning markdown. Lower the `should_render_table_image()`
  threshold from its current setting.
- **W8-2** **Code block line wrapping:** For code blocks wider than 60 chars, add soft wraps
  at word boundaries with `↵` continuation marker so mobile users don't need to scroll.
- **W8-3** **Button row priority:** Move 👍/👎 feedback buttons to **row 0** in
  `src/response_actions.py` (currently row 1). Feedback is the most important user action;
  it should always be visible above the fold.
- **W8-4** **Long response download option:** When a response exceeds 1,500 characters,
  append a "📄 Download as text" button that sends the full text as a `.txt` file attachment
  (uses the file-upload path already implemented in `src/ask_handler.py` lines 588–623).

**Files:** `src/bot_formatting.py`, `src/table_renderer.py`, `src/response_actions.py`,
`src/ask_handler.py`
**Effort:** Small-to-medium.

---

## Wave 9 — Model Routing Intelligence

**Goal:** The right model is chosen every time based on query type, tool requirements,
and real provider health — not just static rules.

**Problems:**
- `src/model_routing_policy.py` marks Anthropic and OpenAI as `supports_native_tools=False`
  (lines 142, 145) — incorrect for modern Claude and GPT-4o, forcing all tool calls to Gemini.
- The routing audit log tracks p95 latency per provider (`_get_provider_p95_latency`) but this
  data is **never consulted** when selecting a route.
- Ollama health is cached for 30s; the bot can route to a dead Ollama for up to 30 seconds.

### Tasks

- **W9-1** **Fix tool-calling capability flags:** Update `ProviderCapabilities` for
  Anthropic (`claude-3-*`) and OpenAI (`gpt-4o`, `gpt-4-turbo`) to `supports_native_tools=True`.
  Update `select_tool_route()` to include these providers before defaulting to Gemini.
- **W9-2** **Latency-aware routing:** Integrate `_get_provider_p95_latency()` into
  `select_auto_route()`. If a provider's p95 latency exceeds a configurable threshold
  (default: 10s), skip it and try the next available option.
- **W9-3** **Ollama pre-call check:** Before each Ollama LLM call (not just the cached check),
  do a lightweight `/api/tags` ping (100ms timeout). On failure, immediately fall back to
  Gemini without waiting for the full request to time out.
- **W9-4** **Route logging:** After every LLM call, log `{query_type, provider_selected,
  latency_ms, score}` to a structured log for future routing policy improvements.

**Files:** `src/model_routing_policy.py`, `src/model_router.py`, `src/llm/chat.py`
**Effort:** Medium — requires testing routing changes carefully.

---

## Wave 10 — Rate Limit & Background Task Isolation

**Goal:** User requests always get LLM quota priority. Background tasks can't starve
interactive conversations.

**Problems:**
- Rate limiter is checked once at the start of `chat()` (`src/llm/chat.py` line 132) but
  not before each provider fallback attempt. Multi-provider chains can exhaust quota mid-chain.
- Background tasks (error monitor, proactive scan) call LLM on fixed schedules with no
  awareness of current user load, consuming quota needed for active conversations.
- Auto-restart thrashing: failed tasks restart every 5 seconds with no backoff, consuming
  logs and CPU during incidents.

### Tasks

- **W10-1** **Per-attempt rate check:** Add rate limit check before each provider attempt in
  the fallback chain (`src/llm/chat.py`) — not just once at entry. If a fallback provider
  would exhaust quota, skip it and return the partial result.
- **W10-2** **Background quota reservation:** Background LLM callers (error monitor,
  proactive scan in `src/bg_monitoring.py`, `src/bg_healing.py`) must check a separate
  "background quota" (10% of total per-minute limit). User requests get priority for the
  remaining 90%.
- **W10-3** **Task restart backoff:** Replace the fixed 5s restart delay in `src/bg_tasks.py`
  with exponential backoff: 5s → 15s → 60s → 5min, resetting after 30 consecutive minutes
  of clean operation.
- **W10-4** **User-load awareness:** If ≥3 active Discord conversations are ongoing
  (tracked via a simple counter), skip optional background scans until load drops.

**Files:** `src/llm/chat.py`, `src/llm_ratelimit.py`, `src/bg_tasks.py`,
`src/bg_monitoring.py`, `src/bg_healing.py`
**Effort:** Medium.

---

## Wave 11 — Quality Auto-Repair Improvements

**Goal:** When the LLM produces a low-quality answer, the repair pass actually helps —
without making things worse.

**Problems:**
- The repair timeout (`src/quality_helpers.py`) is a flat 45s for all providers; fast Copilot
  can time out waiting for slow Gemini recovery.
- The broadening prompt used in repair ("use broader coverage, add freshness cues") can
  cause scope drift — the repaired answer may address a wider topic than the original question.
- Only one repair attempt is allowed (`_QUALITY_RETRY_MAX_ATTEMPTS = 1`); if repair fails,
  the user silently gets the low-quality original answer.

### Tasks

- **W11-1** **Provider-aware repair timeout:** Set timeout based on selected provider:
  Copilot → 20s, Gemini → 45s, Ollama → 60s.
- **W11-2** **Scope-constrained repair prompt:** Add explicit scope constraint to the broadening
  prompt: *"Do NOT change the scope, timeframe, or subject of the original query. Only expand
  source coverage and freshness."*
- **W11-3** **Two-attempt repair:** Allow up to 2 repair attempts. If Gemini repair fails or
  scores no improvement, try Copilot fast path as a second repair attempt before falling back
  to the original answer.
- **W11-4** **Repair transparency:** When budget is constrained and repair is skipped, append
  a subtle footer note: *"ℹ️ Quality review was skipped due to system load."* When a repair
  attempt improved the answer, note: *"✨ This response was improved by quality auto-repair."*

**Files:** `src/quality_helpers.py`, `src/llm/chat.py`
**Effort:** Small-to-medium.

---

## Wave 12 — Streaming & Real-Time Response UX

**Goal:** Users see the bot's response building in real time for all AI-heavy commands —
not a blank screen followed by a wall of text.

**Problems:**
- Streaming (`PROVIDER_STREAM=1`) only works for the Copilot provider. Gemini — the primary
  model — has no streaming implementation despite the SDK supporting it.
- Tool-using queries fall back to non-streaming even when `PROVIDER_STREAM=1` is set.
- Stream chunk interval (200 chars, `src/llm/chat.py:62`) is hard-coded with no tuning.

### Tasks

- **W12-1** **Gemini streaming:** Implement streaming for the Gemini provider in
  `src/llm/providers.py` using `google-generativeai`'s `generate_content_async` with
  `stream=True`. Yield partial chunks through the existing `chat_stream()` interface.
- **W12-2** **Tool call streaming:** While the LLM tool loop runs, yield intermediate
  notifications: *"🔧 Calling `search_web`…"*, *"✅ Got results from `search_web`"*. These
  are Discord message edits, not full chunks — append to the thinking embed, not the response.
- **W12-3** **Configurable chunk interval:** Replace hard-coded 200 with
  `PROVIDER_STREAM_INTERVAL_CHARS` env var (default 200, range 50–500). Lower values give
  more real-time feel; higher values reduce Discord API calls.
- **W12-4** **Stream error recovery:** If the stream closes early (network interruption),
  emit whatever was accumulated with a footer: *"⚠️ Stream interrupted — showing partial
  response."* Do not silently drop the partial content.

**Files:** `src/llm/providers.py`, `src/llm/chat.py`, `src/tool_orchestration.py`
**Effort:** High — Gemini streaming requires significant testing.

---

## Wave 13 — Proactive Notification Improvements

**Goal:** Alerts are actionable, deduplicated, routed intelligently, and don't create
notification fatigue.

**Problems:**
- All alerts go to a single `ALERT_CHANNEL_ID` regardless of severity — a critical container
  failure and a low-priority quality drift appear identically.
- Transient flakes (30-second network blip) trigger alerts that never resolve, with no
  "resolved" follow-up.
- Morning/evening briefings run on fixed UTC schedule; users in other timezones receive
  them at inconvenient times.
- There is no way to snooze or dismiss an alert without deleting the Discord message.

### Tasks

- **W13-1** **Severity-based routing:** Route alerts to different channels by severity:
  `DEBUG/INFO` → log channel only; `WARNING` → `#alerts`; `CRITICAL` → `#alerts` + DM to
  owner. Configure channel IDs in `config.yaml`.
- **W13-2** **Deduplication window:** Suppress duplicate alerts for the same issue within
  a 30-minute window. If the issue persists past 30 minutes, re-alert with escalated severity.
  Add "✅ Resolved" follow-up message when the issue clears.
- **W13-3** **Alert snooze via reaction:** Add ⏰ reaction to alert embeds. When owner reacts
  with ⏰, suppress that alert class for 1 hour. When owner reacts with ✅, mark as resolved
  and stop repeating.
- **W13-4** **Timezone-aware briefings:** Read `user_timezone` from user preferences
  (or infer from conversation patterns). Schedule morning/evening briefings relative to the
  user's local timezone rather than UTC.
- **W13-5** **Remediation hints in alerts:** Quality drift alerts should include a one-line
  suggested action based on the drift category (provider degradation → switch primary model;
  tool failure spike → check API key; recall drop → vector store maintenance).

**Files:** `src/bg_monitoring.py`, `src/bg_tasks.py`, `src/bg_briefing.py`,
`src/alert_manager.py`, `src/notification_prefs.py`
**Effort:** Medium.

---

## Wave 14 — Command Discoverability & Dynamic Help

**Goal:** Any user can find the right command in under 10 seconds, even with 110+ commands
registered.

**Problem:** The `/help` command (`src/discord_commands/utility.py`) is a static dropdown
with manually maintained category lists. As new commands are added, help drifts out of date.
There is no way to search commands by keyword or discover what a command does without running it.

### Tasks

- **W14-1** **Dynamic command enumeration:** Replace static category lists in `/help` with
  auto-generated lists derived from `bot.tree.get_commands()`. Each command's description
  (from `@app_commands.command(description=...)`) is shown automatically.
- **W14-2** **Keyword search:** Add `/help search <keyword>` that fuzzy-matches across command
  names and descriptions. Returns the top 5 matching commands with their usage examples.
- **W14-3** **Disambiguation hints:** For command pairs that overlap in purpose (e.g.,
  `/logs` vs. `/analyze`, `/status` vs. `/health`), add a one-line comparison note in the
  help entry: *"Use `/logs` for raw output; `/analyze` for AI-powered insights."*
- **W14-4** **`/commands` shortcut:** Add `/commands [category]` as an alias for `/help`
  with a category filter — shorter to type on mobile.

**Files:** `src/discord_commands/utility.py`
**Effort:** Small-to-medium.

---

## Suggested Implementation Order

| Wave | Name | Impact | Effort | Priority |
|------|------|--------|--------|----------|
| **W1** | Onboarding & First-Run | High | Medium | 🔴 Ship first |
| **W2** | Error Message Standardization | High | Medium | 🔴 Ship first |
| **W3** | Permission & Auth Visibility | Medium | Small | 🟡 Quick win |
| **W4** | Output Quality Fixes | High | Small | 🔴 Ship first |
| **W5** | Thread & Conversation Continuity | High | Medium | 🟡 Next |
| **W6** | Memory Recall Accuracy | Medium | Medium | 🟡 Next |
| **W7** | Progress Indicators for All Commands | Medium | Medium | 🟡 Next |
| **W8** | Mobile Discord UX | Medium | Small | 🟡 Quick win |
| **W9** | Model Routing Intelligence | High | Medium | 🟠 After stabilization |
| **W10** | Rate Limit & Background Task Isolation | Medium | Medium | 🟠 After stabilization |
| **W11** | Quality Auto-Repair Improvements | Medium | Small | 🟠 After stabilization |
| **W12** | Streaming & Real-Time UX | Medium | High | 🔵 Later |
| **W13** | Proactive Notification Improvements | Low | Medium | 🔵 Later |
| **W14** | Dynamic Help & Command Discovery | Medium | Small | 🔵 Later |

---

## Quick Wins (Do These Immediately)

These can each be completed in a single PR with no architectural risk:

1. **W4-2** — URL mangling fix: `re.sub(r'^.*?(https?://)', r'\1', url)` in source formatter (1 hr, 1 function)
2. **W4-1** — Duplicate sources: guard against `Sources:` appearing in both body and recovery block (1 hr, 2 files)
3. **W3-1** — Update `/whoami` to show permission level and unlocked categories (30 min, 1 file)
4. **W8-3** — Move 👍/👎 feedback buttons to row 0 in `src/response_actions.py` (30 min, 1 file)
5. **W2-3** — Standardize `ephemeral=True` on all error messages across cogs (2 hrs, grep + replace)
6. **W11-2** — Add scope constraint to quality-repair broadening prompt (15 min, 1 function)
