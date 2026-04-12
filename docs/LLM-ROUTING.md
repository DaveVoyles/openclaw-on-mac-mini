# OpenClaw — LLM Routing & Provider Architecture

This document describes the simplified model-routing and provider-calling layer introduced in April 2026. Read it before touching anything in `src/llm/`, `src/model_router.py`, or `src/model_routing_policy.py`.

---

## 🤖 Agent Fleet Instructions

The [Proposed Future Improvements](#proposed-future-improvements) section at the bottom of this doc is a **backlog for a fleet of agents**. An orchestrator agent should manage the fleet using the rules below.

### How the fleet works

1. **Orchestrator** reads the backlog table, checks the `Status` column, and dispatches available tasks to worker agents in parallel — respecting the dependency notes in each row.
2. **Before starting a task**, a worker agent updates its row's `Status` to `🔄 In Progress — Agent <name>` so no other agent picks up the same task.
3. **After completing a task**, the worker updates `Status` to `✅ Done`.
4. **Orchestrator** verifies done tasks (lint + pytest) then dispatches the next wave.

### Orchestrator prompt template

> "You are orchestrating the LLM routing improvements backlog defined in `docs/LLM-ROUTING.md` in the OpenClaw repo at `/Users/davevoyles/openclaw`. Read the Proposed Future Improvements table. Dispatch all tasks whose Status is `⬜ Open` and whose dependencies are `✅ Done` as parallel background agents. Each agent must update the Status column in this file when it claims a task and again when it finishes. Run `ruff check` + `pytest` after each wave and fix any regressions before proceeding."

### Worker agent rules

- **Claim your task before writing any code** — update the Status cell in this file first.
- Scope is strictly the files listed in the **Where** column. Do not touch others.
- Run `cd /Users/davevoyles/openclaw/src && /Users/davevoyles/openclaw/.venv/bin/python -m py_compile <file>` to verify syntax before marking done.
- If blocked, set Status to `🚫 Blocked — <reason>` and stop.

---

## Overview

The LLM subsystem routes every user message to the best available provider — Gemini, Copilot proxy (GPT-4o / Claude), Ollama, or Perplexity — and then calls that provider's API. These two concerns (routing and calling) were previously tangled across four files. They are now cleanly separated.

```
User message
     │
     ▼
src/llm/chat.py  ──  _resolve_non_gemini_reply()
     │                       │
     │              ┌────────┴────────────────────────┐
     │              │         routing decision         │
     │              │  src/model_router.py             │
     │              │   classify_query()               │
     │              │      └─ model_routing_policy.py  │
     │              │           select_auto_route()    │
     │              │           select_tool_route()    │
     │              └─────────────────────────────────┘
     │
     ├── perplexity-direct  →  skills/reporting_skills.py
     ├── copilot/openai/anthropic  →  src/llm/providers.py
     ├── ollama  →  src/llm/tool_execution.py (_try_local_model)
     └── gemini  →  src/llm/chat.py (_gemini_chat)
```

---

## File Map

| File | Responsibility | Lines |
|------|---------------|-------|
| `src/llm/providers.py` | **All non-Gemini HTTP calls.** `chat_openai`, `chat_anthropic`, `chat_openai_vision`, `call_provider()` unified dispatch | 259 |
| `src/model_router.py` | **Backwards-compat shim only.** Re-exports `ModelRoute`, `classify_query`, `copilot_model_for_message`, `is_ollama_alive` from `model_routing_policy` and `chat_openai`, `chat_anthropic`, `chat_openai_vision`, `COPILOT_PROXY_*` from `llm/providers` | ~18 |
| `src/model_routing_policy.py` | **Provider selection policy + query classification.** `select_auto_route()`, `select_tool_route()`, web-search/coding/sports fast-path selectors, `classify_query()`, `is_ollama_alive()`, `copilot_model_for_message()`, `ModelRoute` | ~698 |
| `src/llm/chat.py` | **Orchestration.** `chat_stream()`, `chat()`, `chat_deep()`. Calls `_resolve_non_gemini_reply()` first; falls through to `_gemini_chat()` | 1236 |
| `src/llm_client.py` | **Gemini SDK setup.** Model config, tool declarations, `quick_generate()` | 341 |

---

## Routing Flow

### 1. Fast-path selection (before Gemini)

`_resolve_non_gemini_reply()` in `chat.py` tries routes in priority order:

```
1. model_preference == "auto"
   └── select_web_search_route(model_message)
         prefer_search=True  →  generate_web_search_report()  [Perplexity]

2. model_preference == "auto" and not recalled_context
   └── COPILOT_PROXY_ENABLED and select_coding_route(query)
         matches=True  →  _try_copilot_proxy_reply()  [Copilot]

3. model_preference == "auto"
   └── classify_query()  →  ModelRoute.model_type
         "copilot"    →  _try_copilot_proxy_reply()
         "ollama"     →  _try_local_model()
         "openai"     →  providers.chat_openai()
         "anthropic"  →  providers.chat_anthropic()

4. model_preference in ("openai", "anthropic", "copilot")
   └── Direct forced-provider call

5. model_preference == "local"
   └── _try_local_model() (Ollama/Gemma)

6. None matched → returns None → caller falls through to Gemini
```

### 2. Routing policy profiles

`select_auto_route()` in `model_routing_policy.py` honours the `routing_profile` setting:

| Profile | Behaviour |
|---------|-----------|
| `copilot-first` *(default)* | Copilot proxy for all non-tool queries when available |
| `balanced` | Code→Copilot, creative→OpenAI, analysis→Copilot, chat→Ollama |
| `gemini-first` | Always Gemini, ignores other providers |
| `cost-saver` | Ollama first, Copilot fallback, no paid API calls |

Set via `ROUTING_PROFILE` env var or `cfg.routing_profile`.

### 3. Provider selection for tools

Tool-requiring queries always go to Gemini (native function calling). `select_tool_route()` picks the first available native-tool provider from: Gemini → Anthropic → OpenAI → Copilot → Ollama.

---

## The Provider Layer (`src/llm/providers.py`)

This is the **single place** where non-Gemini HTTP calls happen. Add new providers here.

### Key exports

```python
COPILOT_PROXY_URL: str        # env COPILOT_PROXY_URL
COPILOT_PROXY_ENABLED: bool   # True when proxy URL is set

async def chat_openai(message, history, system_prompt, *, model, temperature, max_tokens) -> str | None
async def chat_anthropic(message, history, system_prompt, *, model, temperature, max_tokens) -> str | None
async def chat_openai_vision(message, image_bytes, mime_type, *, model, temperature, max_tokens) -> str | None

async def call_provider(provider, message, history, system_prompt, **kw) -> str | None
# provider: "openai" | "anthropic" | "copilot"
```

### Copilot proxy routing

When `COPILOT_PROXY_ENABLED`:
- OpenAI calls route through the proxy URL with `COPILOT_PROXY_TOKEN`
- Anthropic calls also route through the proxy (OpenAI-compatible format)
- The proxy serves both GPT-4o and Claude models

`model_router.py` re-exports all three functions for backwards compatibility — existing callers don't need to change import paths.

---

## Dataclasses

### `RouteDecision` (unified)

```python
@dataclass(frozen=True, slots=True)
class RouteDecision:
    provider: str   # "gemini" | "copilot" | "openai" | "anthropic" | "ollama"
    reason: str     # human-readable explanation for logs
```

Returned by: `select_auto_route()`, `select_tool_route()`, `select_reflection_route()`, `select_summarization_route()`, `select_multimodal_route()`.

### Specialised route decisions (kept separate — different fields)

```python
WebSearchRouteDecision(prefer_search: bool, reason: str)
CodingRouteDecision(matches: bool, reason: str)
SportsRouteDecision(prefer_perplexity: bool, tool_name: str, reason: str)
```

---

## Adding a New Provider

1. Add the HTTP caller to `src/llm/providers.py`:
   ```python
   async def chat_newprovider(message, history, system_prompt, *, model="", ...) -> str | None:
       ...
   ```

2. Add a branch to `call_provider()` in the same file:
   ```python
   if provider == "newprovider":
       return await chat_newprovider(...)
   ```

3. Register availability in `select_auto_route()` in `model_routing_policy.py`:
   ```python
   newprovider_available = bool(os.getenv("NEWPROVIDER_API_KEY") or copilot_available)
   ```

4. Add routing logic in `classify_query()` in `model_router.py` if it needs its own `ModelRoute` type, or just let it flow through `call_provider()`.

5. Add a `ProviderCapabilities`-style guard in `select_tool_route()` if the provider supports native tools.

---

## History / What Changed (April 2026)

Before this refactoring the LLM layer had two problems:

**Problem 1 — Wrong module responsibility.**
`chat_openai`, `chat_anthropic`, and `chat_openai_vision` (actual HTTP callers) lived inside `model_router.py` (a routing module). They were imported via scattered local `from model_router import chat_openai` calls in 7 different files.

**Problem 2 — Duplicated dispatch logic.**
`chat_stream()` and `chat()` each contained five identical routing blocks (~165 lines each). Any change had to be made twice.

**What was done:**

| Change | Before | After |
|--------|--------|-------|
| Provider HTTP callers | In `model_router.py` | In `src/llm/providers.py` |
| `model_router.py` size | 384 lines | 222 lines |
| Routing blocks in `chat_stream` + `chat` | 10 blocks (5×2, duplicated) | 1 shared `_resolve_non_gemini_reply()` |
| `llm/chat.py` size | 1366 lines | 1236 lines |
| `*RouteDecision` dataclasses | 6 identical-field classes | 1 `RouteDecision` + 3 specialised |
| `model_routing_policy.py` size | 551 lines | 481 lines |

Additionally, `build_provider_capability_registry()` and `_prefer_specialized_non_tool_route()` (both only called from `select_auto_route()`) were inlined, removing two unnecessary function hops.

---

## Proposed Future Improvements

> **Fleet instructions are at the top of this document.** Orchestrator: dispatch open tasks in parallel waves. Workers: claim a task by updating its Status cell before writing any code.

| # | Task | Impact | Where | Depends On | Status |
|---|------|--------|-------|------------|--------|
| 1 | **Streaming support** — add `chat_openai_stream()` / `chat_anthropic_stream()` to unlock true token streaming for non-Gemini providers; wire into `_try_copilot_proxy_reply()` | High | `src/llm/providers.py`, `src/llm/chat.py` | — | ✅ Done |
| 2 | **Merge `model_router.py` → `model_routing_policy.py`** — router is now 3 functions + stubs; merging removes a file and one import hop; stubs become re-exports in `__init__.py` | Medium | `src/model_router.py`, `src/model_routing_policy.py`, `src/llm/__init__.py` | — | ✅ Done |
| 3 | **Route `quick_generate()` through `call_provider()`** — `llm_client.py` has its own inline Copilot fast-path duplicating `providers.py` logic | Medium | `src/llm_client.py` | — | ✅ Done |
| 4 | **LLM-based intent detection** — replace ~150 lines of hand-crafted regex in `classify_query()` + `select_coding_route()` + `select_web_search_route()` with `quick_generate("needs live web data? yes/no")` calls; ~200ms latency cost, better accuracy | High | `src/model_router.py`, `src/model_routing_policy.py` | 3 | ✅ Done |
| 5 | **Expose routing reason in Discord** — surface `RouteDecision.reason` as a footer in verbose mode (e.g. `_Routed via: Copilot — coding query_`) | Low | `src/ask_orchestrator.py` or `src/bot.py` | — | ✅ Done |
| 6 | **Circuit breaker for non-Gemini providers** — Gemini has `_gemini_circuit` via `tool_health.py`; add a `_provider_circuit` dict keyed by provider name inside `providers.py` to fast-fail after repeated failures | Medium | `src/llm/providers.py` | — | ✅ Done |
| 7 | **Retry with exponential backoff** — add `_call_with_retry(coro, *, retries=2, base_delay=1.0)` wrapper in `providers.py`; handles HTTP 429/502/503 transparently; reduces unnecessary Gemini fallbacks | Medium | `src/llm/providers.py` | 6 | 🔄 In Progress — r07-retry-backoff |
| 8 | **Standardise token recording** — `providers.py` only calls `spending_tracker.record_copilot()` for proxy calls; parse `usage` block from all API responses and record for direct OpenAI/Anthropic calls too | Low | `src/llm/providers.py` | — | ✅ Done |
| 9 | **Route `context.py` summarization through `call_provider()`** — `src/llm/context.py` line 353 still has stray `from model_router import chat_openai`; replace with `call_provider("copilot", ...)` | Low | `src/llm/context.py` | — | ✅ Done |
| 10 | **Dynamic Copilot proxy health-check** — `COPILOT_PROXY_ENABLED` is set once at import; add a startup ping (like `is_ollama_alive()`) that sets `_proxy_reachable` flag; check it in `call_provider()` to fast-fail when proxy URL is set but unreachable | Medium | `src/llm/providers.py` | 6 | ✅ Done |
| 11 | **Route `summarize_conversation()` through `call_provider()`** — `llm/chat.py` line 1188 imports `COPILOT_PROXY_ENABLED` + `chat_openai` from `model_router` inline; replace with `call_provider("copilot", ...)` | Low | `src/llm/chat.py` | — | ✅ Done |
| 12 | **Remove `_router_sessions` from `model_router.py`** — `SessionManager` was kept after provider functions moved out; `is_ollama_alive()` only needs a lightweight aiohttp session; removes the `http_session` import entirely | Low | `src/model_router.py` | 2 | ✅ Done |

| 13 | **`ProviderResponse` envelope** — wrap `call_provider()` return in a typed `ProviderResponse(text, provider, model, latency_ms, input_tokens, output_tokens)` dataclass instead of bare `str \| None`; removes implicit None checks, enables telemetry | High | `src/llm/providers.py` | — | ✅ Done |
| 14 | **Routing telemetry / audit log** — after each `call_provider()` call, append a JSON line to `data/routing_audit.jsonl` with timestamp, query_hash, provider, model, latency_ms, token counts; expose aggregated summary at `/metrics` | Medium | `src/llm/providers.py`, `src/discord_web.py` | 13 | ✅ Done |
| 15 | **Flash / mini-model fast-path** — in `select_auto_route()`, if query is ≤ 25 tokens, no tools needed, and no recalled_context, route to `gpt-4o-mini` (set via `OPENAI_MINI_MODEL` env var) to cut cost on trivial questions | Medium | `src/model_routing_policy.py`, `src/llm/providers.py` | — | ✅ Done |
| 16 | **Startup capability scan** — on bot boot, run parallel lightweight pings to Copilot proxy, Ollama, and check API key presence for OpenAI/Anthropic; log a one-line summary `Providers available: copilot=✓ ollama=✓ openai=✗` so misconfig is surfaced immediately | Medium | `src/llm/providers.py`, `src/bot.py` | 10 | ⬜ Open |
| 17 | **Consolidate `COPILOT_PROXY_ENABLED` into `providers.py`** — 15 files currently do `from model_router import COPILOT_PROXY_ENABLED`; move the constant (and its env-var logic) to `providers.py`; `model_router.py` re-exports it for compat | Medium | `src/llm/providers.py`, `src/model_router.py`, all 15 import sites | 2 | 🚫 Blocked — all 15 callers already import from `llm.providers` (local imports); `model_router.py` cannot re-export at module level because `from llm.providers import …` triggers `llm/__init__.py → llm_tools → skills` circular import chain |
| 18 | **Provider failover chain** — add a configurable `PROVIDER_FALLBACK_CHAIN` env var (default `copilot,ollama,gemini`); if the primary provider returns `None`, `call_provider()` walks the chain automatically before the caller falls through to Gemini | High | `src/llm/providers.py` | 6, 7 | ✅ Done |
| 19 | **Populate `ProviderResponse` token counts** — `chat_openai` and `chat_anthropic` extract `usage.prompt_tokens`/`usage.completion_tokens` internally but discard them; change their return types to a `(str \| None, int, int)` named tuple or a lightweight internal dataclass so `call_provider()` can propagate `input_tokens`/`output_tokens` into the `ProviderResponse` it returns; required for accurate telemetry in #14 | Medium | `src/llm/providers.py` | 13, 14 | ✅ Done |
| 22 | **Thread-safe `_last_usage` for async concurrency** — the current module-level `_last_usage` dict is a race condition under concurrent `call_provider()` calls; replace it with `contextvars.ContextVar` so each async task carries its own usage snapshot without clobbering neighbours | Medium | `src/llm/providers.py` | 19 | ✅ Done |
| 20 | **Fix in-flight syntax/import regressions in `research_agent.py` and `llm/__init__.py`** — the working tree has an orphaned `if COPILOT_PROXY_ENABLED:` guard (missing `from model_router import chat_openai` in scope at line 699) and a reference to undefined `select_research_synthesis_route`; these break `ruff` F821 on the full `src/` tree and will cause runtime failures when the research synthesis path is exercised | High | `src/research_agent.py`, `src/llm/__init__.py` | — | ✅ Done |
| 21 | **Add integration smoke-test for `ResearchAgent._synthesize` and `generate_follow_ups`** — now that the import scopes are correct, add a lightweight `pytest` test (with mocked providers) that exercises both code paths (`copilot` route and `gemini` fallback) to catch future scope regressions before they reach ruff | Medium | `tests/test_research_agent.py` | — | ⬜ Open |
| 23 | **Circuit breaker unit tests** — `reset_circuit()` is exported but no test exercises `_is_open`, `_record_failure`, `_record_success`, or the `call_provider()` top-level guard; add `tests/test_provider_circuit_breaker.py` with cases: circuit opens after N failures, skips call when open, auto-closes after timeout, `reset_circuit()` clears state; use `freezegun` or `monkeypatch` on `time.monotonic` | Medium | `tests/test_provider_circuit_breaker.py` | 6 | ✅ Done |
| 24 | **Audit `model_router.py` re-exports for staleness** — now that all import sites use `llm.providers` directly, `model_router.py`'s re-export of `COPILOT_PROXY_ENABLED` (and other symbols) may be dead code; grep all callers, identify any remaining `from model_router import X` for symbols that now live in `llm.providers`, and either remove the re-exports or document them as intentional backward-compat shims | Low | `src/model_router.py` | 17 | ✅ Done |
| 25 | **Per-provider breakdown in `token_usage_summary()`** — the current `_cumulative_tokens` dict aggregates all providers into a single `{"input": N, "output": N}`; extend it to `{"total": {"input": N, "output": N}, "by_provider": {"openai": {...}, "anthropic": {...}, "copilot": {...}}}` for granular cost attribution per provider | Low | `src/llm/providers.py` | 8 | 📋 Proposed |
| 25 | **Mini-model actual model forwarding** — `select_auto_route()` now returns `provider="copilot"` on the fast-path but doesn't signal which model to use (`_MINI_MODEL`); extend `AutoRouteDecision` with an optional `model: str = ""` field, set it to `_MINI_MODEL` on the fast-path, and propagate it through `classify_query()` → `ModelRoute` → `_try_copilot_proxy_reply()` so the actual mini model is used rather than the default proxy model | Medium | `src/model_routing_policy.py`, `src/llm/chat.py` | 15 | ⬜ Open |
| 26 | **Break `llm/__init__.py` → `llm_tools` circular import** — `llm/__init__.py` imports from `llm_tools` at module level, which imports `skills`, creating a circular chain that prevents any `llm.*` submodule from being imported at module level by other packages; refactor `llm/__init__.py` to use lazy/deferred imports (or move the `llm_tools` re-exports to a separate `llm/tools_compat.py` shim) so that `from llm.providers import X` works at module level without triggering the skills chain; this unblocks task #17 and any future module-level use of `llm` submodules | High | `src/llm/__init__.py`, `src/llm_tools.py` | — | ⬜ Open |
| 27 | **Concurrent `_last_usage` isolation test** — now that `_last_usage` uses `contextvars.ContextVar`, add `tests/test_provider_contextvar.py` with an `asyncio.gather` test that fires two concurrent fake `chat_openai` calls returning different token counts and asserts each `ProviderResponse` carries the correct per-task token values without cross-contamination; validates the fix from task #22 | Low | `tests/test_provider_contextvar.py` | 22 | ⬜ Open |
| 28 | **Telemetry JSONL reader / CLI summary tool** — add `scripts/telemetry_summary.py` (or a `python -m llm.telemetry` `__main__` entry point) that reads `data/routing_audit.jsonl`, computes per-provider success rate / avg latency / p95 latency / token totals, and prints a Markdown table; enables offline analysis without running the bot | Low | `src/llm/telemetry.py`, `scripts/telemetry_summary.py` | 14 | ✅ Done |
| 29 | **Telemetry rolling-window alert** — add `scripts/telemetry_alert.py` that reads `data/routing_audit.jsonl`, computes success rate over the last N records (default 100), and exits non-zero with a human-readable message if any provider's success rate drops below a configurable threshold (default 90%); enables simple cron-based or CI alerting without a full monitoring stack | Low | `scripts/telemetry_alert.py` | 28 | 📋 Proposed |
| 30 | **True Discord streaming for non-Gemini providers** — wire `call_provider_stream()` into the Discord `chat_stream()` yield loop so tokens are streamed to the user in real-time (edit-in-place message pattern); requires `PROVIDER_STREAM=1` env var; eliminates full-buffer wait for Copilot/OpenAI/Anthropic responses | High | `src/llm/chat.py`, `src/bot.py` | 1 | 📋 Proposed |
| 31 | **`call_provider_stream()` circuit breaker tests** — `call_provider_stream()` has its own `_is_open` guard and `_record_failure`/`_record_success` calls but is entirely untested; add `tests/test_provider_stream_circuit_breaker.py` covering: yields nothing when circuit is open, records failure on exception and opens circuit after N errors, records success on clean stream completion, half-open after timeout, per-provider isolation; use `monkeypatch` on `time.monotonic` and an `AsyncMock` async-generator for the underlying chat fn | Low | `tests/test_provider_stream_circuit_breaker.py` | 23 | 📋 Proposed |
| 32 | **`copilot_model_for_message()` A/B quality gate** — `copilot_model_for_message()` now gates the mini model on word count + a single regex, but has no quality feedback loop; add an optional `MINI_MODEL_EVAL=1` mode that logs side-by-side completions (full model vs mini) for short queries to `data/mini_model_eval.jsonl`, plus a `scripts/mini_model_eval_summary.py` that computes token savings and flags responses where the mini model is ≥20% shorter (a proxy for truncation); lets operators tune `MINI_MODEL_MAX_TOKENS` with real traffic data | Low | `src/model_routing_policy.py`, `scripts/mini_model_eval_summary.py` | 15 | 📋 Proposed |
| 33 | **Migrate remaining `from model_router import` call sites to `llm.providers`** — audit found 8 active call sites that still import `chat_openai`, `chat_openai_vision`, `chat_anthropic`, and `COPILOT_PROXY_ENABLED` directly from `model_router` (`llm/chat.py` ×5, `llm/context.py`, `llm_client.py`, `llm/response.py`, `research_agent.py`, `llm_patterns.py`); once task #26 resolves the `llm/__init__ → llm_tools` circular import, replace each `from model_router import X` with `from llm.providers import X` and then delete the `# compat` stubs from `model_router.py`, completing the consolidation started in task #17 | Medium | `src/llm/chat.py`, `src/llm/context.py`, `src/llm_client.py`, `src/llm/response.py`, `src/research_agent.py`, `src/llm_patterns.py`, `src/model_router.py` | 24, 26 | 📋 Proposed |

### Suggested dispatch waves

```
Wave 1 (no deps — all parallel):  #1, #2, #3, #5, #6, #8, #9, #11, #13, #15
Wave 2 (after wave 1):            #7 (needs #6), #10 (needs #6), #4 (needs #3),
                                  #14 (needs #13), #16 (needs #10)
Wave 3 (after wave 2):            #12 (needs #2), #17 (needs #2), #18 (needs #6, #7)
```
