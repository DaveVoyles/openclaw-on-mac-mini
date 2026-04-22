# Oversized Function Refactor Sprint Plan

**Created:** 2026-04-19  
**Status:** Planning complete — awaiting execution approval

## Audit Summary

Three audit agents (Han, Yoda, Leia) investigated all 5 original candidates.

### ⚠️ Original Estimates Were Wrong

| Original Claim | Actual Size | Verdict |
|---|---|---|
| `_build_offline_quality_calibration_payload` (1267L) | **49L** | ✅ Skip |
| `_make_handler` slack_bot (1263L) | **6L** | ✅ Skip |
| `reset_circuit` (921L) | **6L** | ✅ Skip |
| `_channel_context_prefix` (870L) | **21L** | ✅ Skip |
| `create_slack_app` (780L) | **1,971L** | 🔴 Top priority |

### Real Targets

| Function | File | Lines | Risk | Effort |
|---|---|---|---|---|
| `create_slack_app()` | `src/slack_bot.py:2607–4578` | 1,971L | 🔴 HIGH | XL |
| `handle_ask()` | `src/ask_handler.py:86–813` | 727L | 🔴 HIGH | XL |
| `run_chat()` | `src/openclaw_cli.py:4792–5412` | 620L | 🔴 HIGH | XL |
| `chat_stream()` | `src/llm/chat.py:664–1179` | 515L | 🟡 MED-HIGH | L |

### Secondary Targets (200–386L, deferred)

| Function | File | Lines |
|---|---|---|
| `handle_watch_command` | `openclaw_cli_watch.py:1222` | 386L |
| `api_quality_metrics_handler` | `dashboard/api_handlers.py:2436` | 332L |
| `chat` | `src/llm/chat.py:1182` | 325L |
| `_register_trend_commands` | `discord_commands/trends.py:12` | 320L |
| `handle_message` | `discord_events.py:289` | 296L |

---

## Wave Plan

### Critical Path

```
Wave A (tests)  ──► Wave B (chat_stream) — MEDIUM-HIGH, L
                ├──► Wave C (handle_ask) — HIGH, XL
                ├──► Wave D (create_slack_app) — HIGH, XL
                └──► Wave E (run_chat) — HIGH, XL
```

Wave A must land before any of B–E begin.  
Waves B–E run in parallel after Wave A.

---

### Wave A — Safety Net Tests
**Risk:** LOW | **Effort:** M | **3 lanes**

| Lane | Agent | Scope |
|---|---|---|
| A1 | Han 😉🚀 | Slack handler registration count test: assert exact N handlers registered by `create_slack_app()` |
| A2 | Yoda 👽✨ | `handle_ask()` characterization tests: happy path, attachment path, streaming path |
| A3 | Leia 👑💁‍♀️ | `chat_stream()` routing path tests: web-search, coding, auto, forced routes |

**Done when:** All tests pass, pushed, smoke 108 ✅

---

### Wave B — Decompose `chat_stream()` (515L → ~150L)
**Risk:** MEDIUM-HIGH | **Effort:** L | **Blocked by:** Wave A | **2 lanes**

**File:** `src/llm/chat.py:664–1179`

**Internal phases today:**
1. History trimming + context extraction
2. Cross-channel context recall
3. Web-search fast-path (Perplexity)
4. Copilot fast-path (coding)
5. Multi-model auto-routing
6. Forced provider modes (openai/anthropic/copilot)
7. Forced local (Ollama)
8. Forced Gemini fallback

**Extract to:**

| Function | Est. Lines |
|---|---|
| `_prepare_history(history)` | ~25L |
| `_assemble_model_message(recalled, user_msg)` | ~30L |
| `_try_web_search_route(msg, history, ...)` | ~40L |
| `_try_coding_route(msg, history, ...)` | ~40L |
| `_route_by_preference(msg, history, model_pref, ...)` | ~120L |

**Target:** `chat_stream()` shrinks from 515L → ~150L.

| Lane | Agent | Scope |
|---|---|---|
| B1 | Han 😉🚀 | Extract routing helpers; refactor `chat_stream()` to call them |
| B2 | Yoda 👽✨ | Update Wave A tests to verify extracted paths; smoke ✅ |

---

### Wave C — Decompose `handle_ask()` (727L → pipeline)
**Risk:** HIGH | **Effort:** XL | **Blocked by:** Wave A | **2 lanes**

**File:** `src/ask_handler.py:86–813`

**6 nested async functions today:**
- `_think()` — progress updates
- `_stream_chunk()` — yield chunks
- `_finalize()` — post-process/save
- `_create_context_controls()` — control dict
- `_route_and_query()` — LLM invoke
- `_on_text_update()` — message updates

**Target structure:**

```python
async def handle_ask(interaction, question, ...):
    ctx = _build_ask_context(interaction, question, ...)
    await _process_attachment(ctx)
    async for chunk in _route_and_stream(ctx):
        await _on_chunk(ctx, chunk)
    await _finalize_ask(ctx)
```

| Lane | Agent | Scope |
|---|---|---|
| C1 | Leia 👑💁‍♀️ | Move nested fns to module level; refactor to pipeline |
| C2 | Chewy 🐻💪 | Document (don't merge) shared logic duplication with Slack `_ask()` |

---

### Wave D — Decompose `create_slack_app()` (1,971L → handler registries)
**Risk:** HIGH | **Effort:** XL | **Blocked by:** Wave A | **3 lanes**

**File:** `src/slack_bot.py:2607–4578`

**41 inline handlers today covering 13 sections:**
- App Home, Mentions, DMs
- 20+ slash commands
- File events + file browser
- Dynamic action factory
- Compare/translate actions
- Gmail, Dropbox, Channel mgmt integrations

**Target structure:**

```python
def create_slack_app() -> AsyncApp | None:
    if not _slack_is_configured(): return None
    app = AsyncApp(token=SLACK_BOT_TOKEN)
    _register_core_handlers(app)         # Home, Mentions, DMs
    _register_slash_commands(app)        # All /commands
    _register_file_handlers(app)         # File events, browser, actions
    _register_integration_handlers(app)  # Gmail, Dropbox, Channels
    return app
```

**⚠️ Constraints:**
- Handlers capture globals (`_personas`, `_user_prefs`, `_file_registry`, etc.) — closure access must be preserved
- Decorator registration order matters
- Wave A handler count test catches silent misregistration

| Lane | Agent | Scope |
|---|---|---|
| D1 | Han 😉🚀 | Extract `_register_core_handlers()` + `_register_slash_commands()` |
| D2 | Yoda 👽✨ | Extract `_register_file_handlers()` + dynamic action loop |
| D3 | Leia 👑💁‍♀️ | Extract `_register_integration_handlers()` (Gmail + Dropbox + Channels) |

---

### Wave E — Decompose `run_chat()` (620L → CLISessionManager)
**Risk:** HIGH | **Effort:** XL | **Blocked by:** Wave A | **2 lanes**

**File:** `src/openclaw_cli.py:4792–5412`

**Complexity today:** 86 if-statements, 9 try/except, 9 for loops, globals mutation.

**Target structure:**

```python
class CLISessionManager:
    def __init__(self, config, session_id): ...
    async def get_next_input(self) -> str: ...   # readline/prompt_toolkit/piped
    async def route_command(self, cmd) -> bool: ... # /cmd dispatch
    async def stream_response(self, query): ...
    async def cleanup(self): ...

async def run_chat(config, ...) -> int:
    mgr = CLISessionManager(config, session_id)
    while True:
        raw = await mgr.get_next_input()
        if await mgr.route_command(raw): break
        async for chunk in mgr.stream_response(raw):
            print(chunk, end='', flush=True)
    await mgr.cleanup()
    return 0
```

| Lane | Agent | Scope |
|---|---|---|
| E1 | Han 😉🚀 | Extract `CLISessionManager` + `_InputHandler` strategies |
| E2 | Yoda 👽✨ | Extract `CommandRouter`; manual + CI smoke verification |

**⚠️ Note:** Manual smoke test of interactive REPL required (not just CI).

---

## Recommended Execution Order

After Wave A lands:

1. **Wave B** first — safest (MEDIUM-HIGH risk, L effort, fastest win)
2. **Waves C + D** in parallel — both HIGH risk XL
3. **Wave E** last — CLI REPL is most sensitive to regression

---

## Risk Management Rules

- Never merge a wave without smoke 108 ✅
- Wave A tests are the safety net — any regression must revert immediately
- `create_slack_app` is highest risk — do Wave D only after B and C land
- `run_chat` requires manual interactive CLI test, not just CI
