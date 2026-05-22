# OpenClaw — LLM Routing & Provider Architecture
<!-- Updated: 2026-04-18 -->


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
| `src/llm/providers.py` | **All non-Gemini HTTP calls.** `chat_openai`, `chat_anthropic`, `chat_openai_vision`, `chat_ollama`, streaming variants, `call_provider()`, `call_provider_stream()`, `ProviderResponse`, circuit breaker, retry, failover chain, audit telemetry, startup scan | 1 035 |
| `src/llm/telemetry.py` | Audit log writer, `rotate_audit_log()`, env-var constants | 96 |
| `src/llm/__init__.py` | `__getattr__` lazy loader (breaks circular import chain); re-exports `call_provider`, `call_provider_stream`, `ProviderResponse`, streaming fns | 121 |
| `src/model_router.py` | **Backwards-compat shim only.** Re-exports routing symbols from `model_routing_policy` and provider symbols from `llm.providers` | 392 |
| `src/model_routing_policy.py` | **Provider selection policy + query classification.** `select_auto_route()` (with mini-model fast-path), `select_tool_route()`, `classify_query_llm()`, `AutoRouteDecision`, `ModelRoute` | 862 |
| `src/llm/chat.py` | **Orchestration.** `chat_stream()`, `chat()`, `chat_deep()`. Calls `_resolve_non_gemini_reply()` first; falls through to `_gemini_chat()` | 1 381 |
| `src/llm_client.py` | **Gemini SDK setup.** `quick_generate()` via `call_provider("copilot", ...)` | 344 |
| `src/discord_commands/providers.py` | `/providers` slash command — live availability, latency, circuit-breaker state | 54 |
| `src/discord_commands/routing.py` | `/routing` slash command — active profile, fallback chain, mini-model config | 81 |

---

## Routing Flow

### End-to-end orchestration

The runtime split is:

1. `src/llm/chat.py` normalizes the request, applies context controls, and decides whether to attempt non-Gemini fast paths.
2. `src/model_routing_policy.py` classifies the query and returns a provider decision for either plain-text or tool-heavy work.
3. `src/llm/providers.py` performs the non-Gemini HTTP call, applies retry/circuit-breaker/failover behavior, and returns a typed `ProviderResponse`.
4. `src/llm/telemetry.py` records routing telemetry when enabled.
5. If the selected non-Gemini path produces no usable answer, `chat.py` falls through to Gemini rather than surfacing a hard routing failure.

This separation keeps policy decisions, transport concerns, and final orchestration independent. It also means provider-specific failures degrade into fallback behavior instead of forcing every caller to implement its own retry chain.

### Request path summary

| Stage | Owner | Output |
| --- | --- | --- |
| Prompt preparation | `src/llm/chat.py` | Cleaned prompt, context metadata, routing notes |
| Query classification | `src/model_routing_policy.py` / `model_router.py` | `AutoRouteDecision` or tool-route decision |
| Provider execution | `src/llm/providers.py` | `ProviderResponse` or streaming chunks |
| Telemetry/audit | `src/llm/telemetry.py` | JSONL routing records when enabled |
| Final fallback | `src/llm/chat.py` | Gemini response when non-Gemini path is unavailable or unsuitable |

### 1. Fast-path selection (before Gemini)

`_resolve_non_gemini_reply()` in `chat.py` tries routes in priority order:

```
1. Mini-model fast-path (auto, <= MINI_TOKEN_THRESHOLD tokens, no tools, no recalled_context)
   └── call_provider("copilot", model_override=OPENAI_MINI_MODEL)

2. model_preference == "auto"
   └── select_web_search_route()  →  prefer_search=True  →  Perplexity

3. model_preference == "auto" and not recalled_context
   └── select_coding_route()  →  matches=True  →  _try_copilot_proxy_reply()

4. model_preference == "auto"
   └── classify_query() / classify_query_llm()  →  ModelRoute.model_type
         "copilot"    →  _try_copilot_proxy_reply()
         "ollama"     →  call_provider("ollama", ...)
         "openai"     →  call_provider("openai", ...)
         "anthropic"  →  call_provider("anthropic", ...)

5. model_preference in ("openai", "anthropic", "copilot")
   └── Direct forced-provider call via call_provider()

6. model_preference == "local"
   └── call_provider("ollama", ...)

7. None matched → returns None → caller falls through to Gemini
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

Tool-requiring queries go to GPT-4o via GitHub Models API when `COPILOT_TOOLS_ENABLED=true` (default), or Gemini when `false`. `select_tool_route()` picks the first available native-tool provider from: Copilot/GPT-4o → Gemini → Anthropic → OpenAI → Ollama.

---

## The Provider Layer (`src/llm/providers.py`)

This is the **single place** where non-Gemini HTTP calls happen. Add new providers here.

### Key exports

```python
COPILOT_PROXY_URL: str        # env COPILOT_PROXY_URL
COPILOT_PROXY_ENABLED: bool   # True when proxy URL is set
GITHUB_MODELS_ENABLED: bool   # True when GitHub Models API is available
COPILOT_AVAILABLE: bool       # True when COPILOT_PROXY_ENABLED OR GITHUB_MODELS_ENABLED

@dataclass
class ProviderResponse:
    text: str | None
    provider: str
    model: str
    latency_ms: float
    input_tokens: int
    output_tokens: int

async def call_provider(provider, message, history, system_prompt, **kw) -> ProviderResponse
async def call_provider_stream(provider, message, history, system_prompt, **kw) -> AsyncIterator[str]

async def chat_openai(...)   -> str | None
async def chat_anthropic(...)-> str | None
async def chat_openai_vision(...) -> str | None
async def chat_ollama(...)   -> str | None
async def chat_ollama_stream(...) -> AsyncIterator[str]

async def scan_providers()   -> dict[str, dict]   # {provider: {available, latency_ms}}
async def check_proxy_health() -> None            # sets _proxy_healthy flag

def token_usage_summary() -> dict                 # {total: {...}, by_provider: {...}}
def reset_token_usage(provider=None) -> None
```

### Reliability features

| Feature | How it works |
|---------|-------------|
| **Circuit breaker** | `_is_open(provider)` fast-fails after N consecutive failures; auto-resets after timeout |
| **Retry with backoff** | `_call_with_retry(coro, retries=2, base_delay=1.0)` handles 429/502/503 |
| **Failover chain** | `PROVIDER_FALLBACK_CHAIN` env var (default `copilot,ollama`); walks chain on `text=None` |
| **Audit log** | Every successful call appends a JSONL line to `data/routing_audit.jsonl` |
| **Async token isolation** | `_last_usage` is a `contextvars.ContextVar` — no cross-contamination under concurrency |

### Copilot / GitHub Models routing

When `COPILOT_AVAILABLE` (`COPILOT_PROXY_ENABLED` OR `GITHUB_MODELS_ENABLED`):
- OpenAI and Anthropic calls route through the GitHub Models API (`https://models.github.ai/inference`) with `COPILOT_PROXY_TOKEN`
- GitHub Models API is the current production path (`GITHUB_MODELS_ENABLED=true`); the local proxy (`COPILOT_PROXY_URL`) is optional/legacy
- The API serves both GPT-4o and Claude models via OpenAI-compatible format

`model_router.py` re-exports all symbols for backwards compatibility.

---

## Dataclasses

### `RouteDecision` (unified)

```python
@dataclass
class AutoRouteDecision:
    provider: str          # "gemini" | "copilot" | "openai" | "anthropic" | "ollama"
    reason: str            # human-readable for logs
    model_override: str    # non-empty for mini-model fast-path
```

### Specialised route decisions (kept separate — different fields)

```python
WebSearchRouteDecision(prefer_search: bool, reason: str)
CodingRouteDecision(matches: bool, reason: str)
```

---

## Adding a New Provider

1. Add `async def chat_newprovider(...) -> str | None` to `src/llm/providers.py`
2. Add a branch to `call_provider()` in the same file
3. Add `_ping_newprovider()` to `scan_providers()` so startup scan detects it
4. Register availability in `select_auto_route()` in `model_routing_policy.py`
5. Add to `_FALLBACK_CHAIN` default if it should be a fallback target

---

## History / What Changed (April 2026)

### Before

`chat_openai`, `chat_anthropic`, `chat_openai_vision` (HTTP callers) lived in `model_router.py` (a routing module), imported via scattered local `from model_router import ...` calls in 7 files. `chat_stream()` and `chat()` each had 5 duplicate routing blocks (~330 lines duplicated).

### Wave 1 — Provider extraction

- Created `src/llm/providers.py` with `chat_openai`, `chat_anthropic`, `chat_openai_vision`, `call_provider()`
- `model_router.py` became a 27-line re-export shim
- Routing blocks unified into `_resolve_non_gemini_reply()`

### Waves 2–3 — Reliability + telemetry

- Circuit breaker (`_is_open`, `_record_failure`, `_record_success`) per provider
- `_call_with_retry` with exponential backoff (handles 429/502/503)
- `ProviderResponse` typed envelope replaces bare `str | None`
- Token counts via `_last_usage` ContextVar (async-safe, no cross-contamination)
- Audit log (`data/routing_audit.jsonl`) + `/routing-metrics` HTTP endpoint
- `classify_query_llm()` LLM-based intent detection added

### Waves 4–5 — Provider completeness

- Startup capability scan via `scan_providers()` / `check_proxy_health()`
- Ollama HTTP calls (`chat_ollama`) + streaming (`chat_ollama_stream`)
- Provider failover chain (`PROVIDER_FALLBACK_CHAIN` env var)
- Mini-model fast-path: queries <= `MINI_TOKEN_THRESHOLD` tokens route to `OPENAI_MINI_MODEL`
- `llm/__init__.py` `__getattr__` lazy loader breaks circular import chain
- `COPILOT_PROXY_ENABLED` consolidated to single source in `providers.py`

### Waves 6–7 — Slash commands + streaming

- `/providers` slash command: live availability, latency, circuit-breaker state
- `/routing` slash command: active profile, fallback chain, mini-model config
- `chat_openai_stream` + `chat_anthropic_stream` SSE streaming functions
- Ollama streaming token metrics (`prompt_eval_count`/`eval_count` -> `_last_usage`)
- `AUDIT_ROTATE_INTERVAL` env var; async `rotate_audit_log()` coroutine
- Per-provider token breakdown in `token_usage_summary()`
- Routing debug footer in Slack (`SHOW_ROUTING_DEBUG` env var)

### Wave 8 — Tests + cleanup

- `tests/test_provider_fallback.py` (5 failover chain tests)
- `tests/test_ollama_stream_metrics.py` (5 Ollama streaming token tests)
- `tests/test_audit_rotate_interval.py` (5 rotation interval env-var tests)
- `tests/test_ollama_stream_cumulative.py` (4 cumulative token tests)
- `tests/test_provider_streaming.py` (6 streaming function tests)
- Streaming functions exported via `llm/__init__.py` lazy loader
- Redundant local `os` imports removed from `providers.py`


---

## Proposed Future Improvements

> **Fleet instructions are at the top of this document.** Orchestrator: dispatch open tasks in parallel waves. Workers: claim a task by updating its Status cell before writing any code.

| # | Task | Impact | Where | Depends On | Status |
|---|------|--------|-------|------------|--------|
| 1 | **Streaming support** — add `chat_openai_stream()` / `chat_anthropic_stream()` to unlock true token streaming for non-Gemini providers; wire into `_try_copilot_proxy_reply()` | High | `src/llm/providers.py`, `src/llm/chat.py` | — | ✅ Done |
| 2 | **Merge `model_router.py` → `model_routing_policy.py`** — router is now 3 functions + stubs; merging removes a file and one import hop; stubs become re-exports in `__init__.py` | Medium | `src/model_router.py`, `src/model_routing_policy.py`, `src/llm/__init__.py` | — | ✅ Done |
| 3 | **Route `quick_generate()` through `call_provider()`** — `llm_client.py` has its own inline Copilot fast-path duplicating `providers.py` logic | Medium | `src/llm_client.py` | — | ✅ Done |
| 4 | **LLM-based intent detection** — replace ~150 lines of hand-crafted regex in `classify_query()` + `select_coding_route()` + `select_web_search_route()` with `quick_generate("needs live web data? yes/no")` calls; ~200ms latency cost, better accuracy | High | `src/model_router.py`, `src/model_routing_policy.py` | 3 | ✅ Done |
| 5 | **Expose routing reason in Slack** — surface `RouteDecision.reason` as a footer in verbose mode (e.g. `_Routed via: Copilot — coding query_`) | Low | `src/ask_orchestrator.py` or `src/slack_bot.py` | — | ✅ Done |
| 6 | **Circuit breaker for non-Gemini providers** — Gemini has `_gemini_circuit` via `tool_health.py`; add a `_provider_circuit` dict keyed by provider name inside `providers.py` to fast-fail after repeated failures | Medium | `src/llm/providers.py` | — | ✅ Done |
| 7 | **Retry with exponential backoff** — add `_call_with_retry(coro, *, retries=2, base_delay=1.0)` wrapper in `providers.py`; handles HTTP 429/502/503 transparently; reduces unnecessary Gemini fallbacks | Medium | `src/llm/providers.py` | 6 | ✅ Done |
| 8 | **Standardise token recording** — `providers.py` only calls `spending_tracker.record_copilot()` for proxy calls; parse `usage` block from all API responses and record for direct OpenAI/Anthropic calls too | Low | `src/llm/providers.py` | — | ✅ Done |
| 9 | **Route `context.py` summarization through `call_provider()`** — `src/llm/context.py` line 353 still has stray `from model_router import chat_openai`; replace with `call_provider("copilot", ...)` | Low | `src/llm/context.py` | — | ✅ Done |
| 10 | **Dynamic Copilot proxy health-check** — `COPILOT_PROXY_ENABLED` is set once at import; add a startup ping (like `is_ollama_alive()`) that sets `_proxy_reachable` flag; check it in `call_provider()` to fast-fail when proxy URL is set but unreachable | Medium | `src/llm/providers.py` | 6 | ✅ Done |
| 11 | **Route `summarize_conversation()` through `call_provider()`** — `llm/chat.py` line 1188 imports `COPILOT_PROXY_ENABLED` + `chat_openai` from `model_router` inline; replace with `call_provider("copilot", ...)` | Low | `src/llm/chat.py` | — | ✅ Done |
| 12 | **Remove `_router_sessions` from `model_router.py`** — `SessionManager` was kept after provider functions moved out; `is_ollama_alive()` only needs a lightweight aiohttp session; removes the `http_session` import entirely | Low | `src/model_router.py` | 2 | ✅ Done |

| 13 | **`ProviderResponse` envelope** — wrap `call_provider()` return in a typed `ProviderResponse(text, provider, model, latency_ms, input_tokens, output_tokens)` dataclass instead of bare `str \| None`; removes implicit None checks, enables telemetry | High | `src/llm/providers.py` | — | ✅ Done |
| 14 | **Routing telemetry / audit log** — after each `call_provider()` call, append a JSON line to `data/routing_audit.jsonl` with timestamp, query_hash, provider, model, latency_ms, token counts; expose aggregated summary at `/metrics` | Medium | `src/llm/providers.py`, `src/discord_web.py` | 13 | ✅ Done |
| 15 | **Flash / mini-model fast-path** — in `select_auto_route()`, if query is ≤ 25 tokens, no tools needed, and no recalled_context, route to `gpt-4o-mini` (set via `OPENAI_MINI_MODEL` env var) to cut cost on trivial questions | Medium | `src/model_routing_policy.py`, `src/llm/providers.py` | — | ✅ Done |
| 16 | **Startup capability scan** — on bot boot, run parallel lightweight pings to Copilot proxy, Ollama, and check API key presence for OpenAI/Anthropic; log a one-line summary `Providers available: copilot=✓ ollama=✓ openai=✗` so misconfig is surfaced immediately | Medium | `src/llm/startup.py`, `src/bot.py` | 10 | ✅ Done |
| 17 | **Consolidate `COPILOT_PROXY_ENABLED` into `providers.py`** — 15 files currently do `from model_router import COPILOT_PROXY_ENABLED`; move the constant (and its env-var logic) to `providers.py`; `model_router.py` re-exports it for compat | Medium | `src/llm/providers.py`, `src/model_router.py`, all 15 import sites | 2 | ✅ Done |
| 18 | **Provider failover chain** — add a configurable `PROVIDER_FALLBACK_CHAIN` env var (default `copilot,ollama,gemini`); if the primary provider returns `None`, `call_provider()` walks the chain automatically before the caller falls through to Gemini | High | `src/llm/providers.py` | 6, 7 | ✅ Done |
| 19 | **Populate `ProviderResponse` token counts** — `chat_openai` and `chat_anthropic` extract `usage.prompt_tokens`/`usage.completion_tokens` internally but discard them; change their return types to a `(str \| None, int, int)` named tuple or a lightweight internal dataclass so `call_provider()` can propagate `input_tokens`/`output_tokens` into the `ProviderResponse` it returns; required for accurate telemetry in #14 | Medium | `src/llm/providers.py` | 13, 14 | ✅ Done |
| 22 | **Thread-safe `_last_usage` for async concurrency** — the current module-level `_last_usage` dict is a race condition under concurrent `call_provider()` calls; replace it with `contextvars.ContextVar` so each async task carries its own usage snapshot without clobbering neighbours | Medium | `src/llm/providers.py` | 19 | ✅ Done |
| 20 | **Fix in-flight syntax/import regressions in `research_agent.py` and `llm/__init__.py`** — the working tree has an orphaned `if COPILOT_PROXY_ENABLED:` guard (missing `from model_router import chat_openai` in scope at line 699) and a reference to undefined `select_research_synthesis_route`; these break `ruff` F821 on the full `src/` tree and will cause runtime failures when the research synthesis path is exercised | High | `src/research_agent.py`, `src/llm/__init__.py` | — | ✅ Done |
| 21 | **Add integration smoke-test for `ResearchAgent._synthesize` and `generate_follow_ups`** — now that the import scopes are correct, add a lightweight `pytest` test (with mocked providers) that exercises both code paths (`copilot` route and `gemini` fallback) to catch future scope regressions before they reach ruff | Medium | `tests/test_research_agent.py` | — | ⬜ Open |
| 23 | **Circuit breaker unit tests** — `reset_circuit()` is exported but no test exercises `_is_open`, `_record_failure`, `_record_success`, or the `call_provider()` top-level guard; add `tests/test_provider_circuit_breaker.py` with cases: circuit opens after N failures, skips call when open, auto-closes after timeout, `reset_circuit()` clears state; use `freezegun` or `monkeypatch` on `time.monotonic` | Medium | `tests/test_provider_circuit_breaker.py` | 6 | ✅ Done |
| 24 | **Audit `model_router.py` re-exports for staleness** — now that all import sites use `llm.providers` directly, `model_router.py`'s re-export of `COPILOT_PROXY_ENABLED` (and other symbols) may be dead code; grep all callers, identify any remaining `from model_router import X` for symbols that now live in `llm.providers`, and either remove the re-exports or document them as intentional backward-compat shims | Low | `src/model_router.py` | 17 | ✅ Done |
| 25 | **Per-provider breakdown in `token_usage_summary()`** — the current `_cumulative_tokens` dict aggregates all providers into a single `{"input": N, "output": N}`; extend it to `{"total": {"input": N, "output": N}, "by_provider": {"openai": {...}, "anthropic": {...}, "copilot": {...}}}` for granular cost attribution per provider | Low | `src/llm/providers.py` | 8 | ✅ Done |
| 25 | **Mini-model actual model forwarding** — `select_auto_route()` now returns `provider="copilot"` on the fast-path but doesn't signal which model to use (`_MINI_MODEL`); extend `AutoRouteDecision` with an optional `model: str = ""` field, set it to `_MINI_MODEL` on the fast-path, and propagate it through `classify_query()` → `ModelRoute` → `_try_copilot_proxy_reply()` so the actual mini model is used rather than the default proxy model | Medium | `src/model_routing_policy.py`, `src/llm/chat.py` | 15 | ✅ Done |
| 26 | **Break `llm/__init__.py` → `llm_tools` circular import** — `llm/__init__.py` imports from `llm_tools` at module level, which imports `skills`, creating a circular chain that prevents any `llm.*` submodule from being imported at module level by other packages; refactor `llm/__init__.py` to use lazy/deferred imports (or move the `llm_tools` re-exports to a separate `llm/tools_compat.py` shim) so that `from llm.providers import X` works at module level without triggering the skills chain; this unblocks task #17 and any future module-level use of `llm` submodules | High | `src/llm/__init__.py`, `src/llm_tools.py` | — | ✅ Done |
| 27 | **`check_proxy_health()` periodic re-ping** — `check_proxy_health()` is called once at startup; if the proxy recovers after a transient outage the bot stays degraded until restart; add a background `asyncio.Task` (started in `bot.py` or a new `health_monitor.py`) that re-runs `check_proxy_health()` every N seconds (default `PROXY_HEALTH_INTERVAL=60`), so `_proxy_healthy` self-heals without a restart | Low | `src/llm/providers.py`, `src/bot.py` | 10 | ✅ Done |
| 27 | **Real Ollama HTTP calls in `call_provider()`** — added `chat_ollama()` that POSTs to `{OLLAMA_BASE_URL}/api/chat` with `stream: false`; response parsed from `message.content` / `prompt_eval_count` / `eval_count`; wrapped with `_call_with_retry` + circuit breaker (`_is_open` / `_record_success` / `_record_failure`); added `_OLLAMA_BASE_URL` and `_OLLAMA_DEFAULT_MODEL` env-var constants; `call_provider("ollama", …)` now routes to the live endpoint instead of returning `None` | Medium | `src/llm/providers.py` | — | ✅ Done |
| 27b | **Concurrent `_last_usage` isolation test** — now that `_last_usage` uses `contextvars.ContextVar`, add `tests/test_provider_contextvar.py` with an `asyncio.gather` test that fires two concurrent fake `chat_openai` calls returning different token counts and asserts each `ProviderResponse` carries the correct per-task token values without cross-contamination; validates the fix from task #22 | Low | `tests/test_provider_contextvar.py` | 22 | ⬜ Open |
| 28 | **Telemetry JSONL reader / CLI summary tool** — add `scripts/telemetry_summary.py` (or a `python -m llm.telemetry` `__main__` entry point) that reads `data/routing_audit.jsonl`, computes per-provider success rate / avg latency / p95 latency / token totals, and prints a Markdown table; enables offline analysis without running the bot | Low | `src/llm/telemetry.py`, `scripts/telemetry_summary.py` | 14 | ✅ Done |
| 29 | **Telemetry rolling-window alert** — add `scripts/telemetry_alert.py` that reads `data/routing_audit.jsonl`, computes success rate over the last N records (default 100), and exits non-zero with a human-readable message if any provider's success rate drops below a configurable threshold (default 90%); enables simple cron-based or CI alerting without a full monitoring stack | Low | `scripts/telemetry_alert.py` | 28 | ✅ Done |
| 30 | **True Slack streaming for non-Gemini providers** — wire `call_provider_stream()` into the Slack `chat_stream()` yield loop so tokens are streamed to the user in real-time (edit-in-place message pattern via `client.chat_update`); requires `PROVIDER_STREAM=1` env var; eliminates full-buffer wait for Copilot/OpenAI/Anthropic responses | High | `src/llm/chat.py`, `src/slack_bot.py` | 1 | 📋 Proposed |
| 31 | **`call_provider_stream()` circuit breaker tests** — `call_provider_stream()` has its own `_is_open` guard and `_record_failure`/`_record_success` calls but is entirely untested; add `tests/test_provider_stream_circuit_breaker.py` covering: yields nothing when circuit is open, records failure on exception and opens circuit after N errors, records success on clean stream completion, half-open after timeout, per-provider isolation; use `monkeypatch` on `time.monotonic` and an `AsyncMock` async-generator for the underlying chat fn | Low | `tests/test_provider_stream_circuit_breaker.py` | 23 | ✅ Done |
| 37 | **Circuit breaker state SSE endpoint** — expose a `/api/circuit-breaker` Server-Sent Events endpoint (or a polling REST endpoint) that pushes live circuit state (`provider`, `is_open`, `failures`, `open_until`) for all tracked providers; enables real-time dashboard widgets and alerting without polling `scan_providers()`; implement as a new `src/cogs/circuit_breaker_api.py` aiohttp route mounted on the existing bot HTTP server | Low | `src/cogs/circuit_breaker_api.py`, `src/llm/providers.py` | 34 | 📋 Proposed |
| 38 | **Unit tests for `startup.scan_providers()`** — add `tests/test_llm_startup.py` with pytest tests covering: happy path (all 4 providers available), copilot disabled via `COPILOT_PROXY_ENABLED=False`, Ollama exception swallowed, partial availability (only OpenAI key set), and log format verification that `_log_availability_summary` emits ✅/❌ per provider; use `AsyncMock` for async fns, `monkeypatch` for env vars | Low | `tests/test_llm_startup.py` | 16 | ✅ Done |
| 34 | **Retry metrics in telemetry audit log** — `_call_with_retry` logs retry attempts at WARNING but never surfaces them in `data/routing_audit.jsonl`; add a `retry_count: int` field to `ProviderResponse` (default 0) and have `_call_with_retry` return `(result, attempt_count)` so `call_provider()` can populate it; record `retry_count` in the JSONL line emitted by task #14; enables alerting and dashboarding on provider reliability trends | Medium | `src/llm/providers.py` | 7, 14 | ✅ Done |
| 32 | **`copilot_model_for_message()` A/B quality gate** — `copilot_model_for_message()` now gates the mini model on word count + a single regex, but has no quality feedback loop; add an optional `MINI_MODEL_EVAL=1` mode that logs side-by-side completions (full model vs mini) for short queries to `data/mini_model_eval.jsonl`, plus a `scripts/mini_model_eval_summary.py` that computes token savings and flags responses where the mini model is ≥20% shorter (a proxy for truncation); lets operators tune `MINI_MODEL_MAX_TOKENS` with real traffic data | Low | `src/model_routing_policy.py`, `scripts/mini_model_eval_summary.py` | 15 | ✅ Done |
| 33 | **Migrate remaining `from model_router import` call sites to `llm.providers`** — audit found 8 active call sites that still import `chat_openai`, `chat_openai_vision`, `chat_anthropic`, and `COPILOT_PROXY_ENABLED` directly from `model_router` (`llm/chat.py` ×5, `llm/context.py`, `llm_client.py`, `llm/response.py`, `research_agent.py`, `llm_patterns.py`); once task #26 resolves the `llm/__init__ → llm_tools` circular import, replace each `from model_router import X` with `from llm.providers import X` and then delete the `# compat` stubs from `model_router.py`, completing the consolidation started in task #17 | Medium | `src/llm/chat.py`, `src/llm/context.py`, `src/llm_client.py`, `src/llm/response.py`, `src/research_agent.py`, `src/llm_patterns.py`, `src/model_router.py` | 24, 26 | ✅ Done |
| 34 | **Integration tests for provider failover chain** — extract `_call_one(provider, ...)` private helper from `call_provider()` and add automatic fallback walking over `_FALLBACK_CHAIN`; write `tests/test_provider_fallback.py` with 5 tests: success-first-try, falls-back-on-None, all-fail-returns-null, fallback-logs-warning, contextvar-isolation (concurrent gather); update circuit breaker tests to patch `_FALLBACK_CHAIN=[]` where they assumed no fallback | Medium | `src/llm/providers.py`, `tests/test_provider_fallback.py`, `tests/test_provider_circuit_breaker.py` | 18, 23 | ✅ Done |
| 35 | **`_call_one` exception handling and circuit-breaker recording** — `_call_one` currently delegates exception handling to the underlying `chat_openai`/`chat_anthropic` functions, but if an unexpected exception leaks through (e.g., from `aiohttp` session teardown), `call_provider` would propagate it instead of returning a null `ProviderResponse`; wrap `_call_one`'s body in a `try/except Exception` that logs a warning, calls `_record_failure(provider)`, and returns `None`; add a test in `test_provider_fallback.py` that patches `chat_openai` to raise and asserts `call_provider` still returns a null `ProviderResponse` without raising | Low | `src/llm/providers.py`, `tests/test_provider_fallback.py` | 34 | ⬜ Open |
| 36 | **Ollama streaming support in `call_provider_stream()`** — `chat_ollama` uses `stream: false`; add an async-generator variant `stream_ollama()` that POSTs with `stream: true` and yields newline-delimited JSON chunks (each parsed for `message.content`); wire it into `call_provider_stream()` so Ollama responses are streamed to Slack in real-time; consistent with the existing `_stream_openai` / `_stream_anthropic` pattern | Medium | `src/llm/providers.py` | 27, 30 | ✅ Done |
| 39 | **Ollama streaming token metrics** — `chat_ollama_stream()` yields tokens but does not update `_last_usage`; parse the final `done=true` chunk's `prompt_eval_count` and `eval_count` fields and write them into `_last_usage` so telemetry and circuit-breaker stats are accurate for streamed Ollama calls; mirrors how `chat_ollama()` captures these counts after the blocking response | Low | `src/llm/providers.py` | 36 | ✅ Done |
| 34 | **`scan_providers()` result caching + re-scan command** — cache the result of `scan_providers()` in a module-level `_provider_status: dict[str, bool]` var in `providers.py`; expose a `get_provider_status()` getter; add a `/providers` slash command in a new `providers_cog.py` that re-runs the scan on demand and responds with a formatted embed showing each provider's live status, latency, and circuit-breaker state | Low | `src/llm/providers.py`, `src/cogs/providers_cog.py` | 16 | ✅ Done |
| 35 | **Background audit log rotation** — replace the synchronous read-entire-file rotation in `src/llm/telemetry.py:record()` with a lightweight async background task (`asyncio.create_task`) that checks and trims `routing_audit.jsonl` at a configurable interval (default every 5 min via `AUDIT_ROTATE_INTERVAL` env var); eliminates per-write I/O overhead for high-volume deployments; the `record()` call path becomes write-only with no read-back | Low | `src/llm/telemetry.py` | 14 | ✅ Done |
| 40 | **Mini-model fast-path metrics** — `_try_copilot_proxy_reply()` now uses `model_override` when set (mini-model path), but this bypasses `_copilot_model_candidates()` fallback logic; add a `routing_notes` append when `model_override` is used (e.g. `"mini-model override: gpt-4o-mini"`) and record it in the telemetry audit log so mini-model usage is visible in routing dashboards; also ensure `model_label` in the returned tuple reflects the actual `model_override` value | Low | `src/llm/chat.py`, `src/llm/telemetry.py` | 25 | ✅ Done |
| 41 | **Move `from skills import SKILLS` in `llm/chat.py` to a lazy import** — `llm/chat.py` still imports `SKILLS` at module level (line 39), which forces the entire `skills` package to load whenever `llm.chat` is imported; move it to a function-level import inside `_run_function_call()` (or whichever function uses it), so `llm/chat.py` is safe to import at module level in test/dev environments; this completes the cleanup started in task #26 and removes the last module-level `skills` import from the `llm` package | Low | `src/llm/chat.py` | 26 | ⬜ Open |
| 42 | **`call_provider_with_fallback()` telemetry integration** — `call_provider_with_fallback()` delegates to `call_provider()` which already emits routing audit entries; but the top-level failover attempt list and final outcome (how many providers were tried, which succeeded) are not recorded; add a structured audit entry after the loop (both on success and exhaustion) so operators can track failover frequency and identify which secondary providers are being relied on in production | Low | `src/llm/providers.py`, `src/llm/telemetry.py` | 18, 14 | 📋 Proposed |
| 43 | **`scan_providers()` latency instrumentation** — `scan_providers()` returns a `dict[str, bool]` but discards timing data; record each provider's ping latency (ms) alongside the bool result in a `dict[str, dict]` (e.g. `{"copilot": {"ok": True, "latency_ms": 42}}`); expose a `get_provider_latency()` helper and log latencies in `_log_availability_summary`; enables surfacing slow providers in the `/providers` slash command and dashboards; depends on #38 (the new tests cover the updated return shape) | Low | `src/llm/startup.py`, `tests/test_llm_startup.py` | 38 | ✅ Done |
| 43 | **Telemetry alert latency threshold** — extend `scripts/telemetry_alert.py` with `--max-latency MS` (default: 2000) and `--latency-percentile` (default: p95) flags; compute per-provider latency at the chosen percentile over the rolling window and exit 1 with an alert message if any provider exceeds the threshold; allows the same cron/CI job that checks success rates to also catch providers that are responding too slowly, without requiring a separate monitoring stack | Low | `scripts/telemetry_alert.py` | 29 | ✅ Done |
| 44 | **`rotate_audit_log()` configurable interval via env var** — `_audit_log_rotation_loop()` in `bot.py` sleeps a hardcoded 3600 s; read `AUDIT_ROTATE_INTERVAL` (seconds, default 3600) from env at startup and pass it to the sleep call; also expose a `rotate_audit_log()` call via the `/admin` slash command so operators can trigger an on-demand rotation without restarting the bot | Low | `src/bot.py`, `src/llm/telemetry.py` | 35 | ✅ Done |
| 44 | **Remove unused `import os` from `model_router.py`** — after consolidating `COPILOT_PROXY_URL`/`COPILOT_PROXY_ENABLED` to `llm.providers`, `model_router.py` no longer reads any env vars directly; audit remaining `os.*` usage in the file and, if none exist, remove the `import os` line to keep imports clean | Low | `src/model_router.py` | 17 | ⬜ Open |
| 45 | **Expose mini-model routing note in `/routing` slash command** — now that `routing_notes` records `mini-model: <model>` entries (Task #40), surface them in the `/routing debug` command output so operators can see at-a-glance when a request was handled by the mini-model fast-path rather than the full model selection flow; depends on #40 | Low | `src/commands/routing.py` | 40 | ⬜ Open |
| 45 | **Unit tests for `call_provider_stream()` Ollama token capture** — add `tests/test_ollama_stream_metrics.py` with an async test that mocks `chat_ollama_stream()` to yield tokens and set `_last_usage` on the `done` chunk; assert that after `call_provider_stream("ollama", …)` exhausts, `_cumulative_tokens` and `_tokens_by_provider["ollama"]` reflect the captured `prompt_eval_count`/`eval_count` values; validates the fix from task #39 | Low | `tests/test_ollama_stream_metrics.py` | 39 | ⬜ Open |
| 46 | **Unit tests for `scan_providers()` latency shape** — now that `scan_providers()` returns `dict[str, dict]` with `available` and `latency_ms` keys (Task #38/43), add `tests/test_scan_providers_latency.py` with pytest cases: latency is a positive float when provider is reachable, `latency_ms` is `None` when provider is unavailable, all four provider keys are always present, concurrent timing ensures pings run in parallel (total wall time < sum of individual pings), and `bot.py` startup loop correctly reads `info["available"]` from the dict; use `AsyncMock` with a simulated `asyncio.sleep` delay to drive latency values | Low | `tests/test_scan_providers_latency.py`, `src/llm/providers.py` | 43 | 🔄 In Progress — Agent t46-stream-tests |
| 47 | **Expose `token_usage_summary()` via HTTP health endpoint** — `token_usage_summary()` now returns `{"total": {...}, "by_provider": {...}}`; surface this in the existing web health handler (e.g. `/health/llm`) so operators can inspect live token-burn rates per provider without needing direct Python access; the handler already calls provider introspection functions, so this is a small additive change; include a `reset_token_usage()` call on startup so metrics reflect the current process lifetime only | Low | `src/discord_web.py`, `src/llm/providers.py` | 25 | ✅ Done |
| 48 | **Update `test_model_selection.py` patches from `model_router.*` to `llm.providers.*`** — the test suite currently patches `model_router.chat_openai`, `model_router.chat_anthropic`, and `model_router.COPILOT_PROXY_ENABLED` to mock provider calls in `llm/chat.py`; now that the call sites import directly from `llm.providers`, these patches have no effect and the tests rely on the compat shim being in sync; replace all `patch("model_router.chat_openai", ...)`, `patch("model_router.chat_anthropic", ...)`, and `patch("model_router.COPILOT_PROXY_ENABLED", ...)` with `patch("llm.providers.chat_openai", ...)` etc. in `tests/test_model_selection.py` (and any other test file using `model_router.*` patches) to ensure the mocks actually intercept the production code paths | Low | `tests/test_model_selection.py` | 33 | ✅ Done |
| 49 | **Unit tests for `start_proxy_health_loop()` / `stop_proxy_health_loop()`** — add `tests/test_proxy_health_loop.py` with async tests: (1) `start_proxy_health_loop()` returns an `asyncio.Task` and calling it a second time returns the same task (idempotent); (2) `stop_proxy_health_loop()` cancels the task and sets `_health_task = None`; (3) after stop, calling start again creates a fresh task; (4) the loop calls `check_proxy_health()` after each `PROXY_HEALTH_INTERVAL` sleep and swallows exceptions without crashing; use `AsyncMock` + `monkeypatch` on `asyncio.sleep` to drive the loop without real delays | Low | `tests/test_proxy_health_loop.py` | 27 | ✅ Done |
| 49 | **Unit tests for `_AUDIT_ROTATE_INTERVAL` env-var wiring** — add `tests/test_audit_rotate_interval.py` with cases: (1) default value is 3600 when env var is unset, (2) custom value (e.g. 60) is picked up when `AUDIT_ROTATE_INTERVAL=60` is set before import, (3) `bot.py`'s `_audit_log_rotation_loop` sleeps the configured interval by mocking `asyncio.sleep` and asserting it is called with `_AUDIT_ROTATE_INTERVAL` not the literal 3600; ensures the env-var wiring introduced in task #44 is regression-tested | Low | `tests/test_audit_rotate_interval.py`, `src/llm/telemetry.py` | 44 | ✅ Done |
| 51 | **Export streaming functions via `llm/__init__.py`** — add `chat_ollama_stream`, `call_provider_stream`, `call_provider`, and `ProviderResponse` to the `_LAZY_EXPORTS` dict in `src/llm/__init__.py` so callers can do `from llm import call_provider_stream` without importing from `llm.providers` directly; note `chat_openai_stream`/`chat_anthropic_stream` do not exist as public functions (only private `_stream_openai`/`_stream_anthropic`) so only real public symbols are exported | Low | `src/llm/__init__.py` | — | ✅ Done |
| 52 | **Add `chat_openai_stream` and `chat_anthropic_stream` public wrappers in `llm/providers.py`** — `_stream_openai` and `_stream_anthropic` are private async generators used internally by `call_provider_stream`; expose them as public `chat_openai_stream(messages, ...)` and `chat_anthropic_stream(messages, ...)` functions with the same signature convention as `chat_ollama_stream` so external callers can stream directly from a single provider without going through `call_provider_stream`; once added, export them via `llm/__init__.py` lazy loader (complement to task #51) | Low | `src/llm/providers.py`, `src/llm/__init__.py` | 51 | ⬜ Open |
| 52 | **Remove redundant local `os` imports in `providers.py`** — `_ping_openai()` and `_ping_anthropic()` each contained a local `import os as _os` statement that shadowed the module-level `import os`; removed both local imports and replaced `_os.getenv(...)` calls with `os.getenv(...)` to use the already-available module-level name; reduces noise and makes import graph cleaner | Low | `src/llm/providers.py` | — | ✅ Done |
| 53 | **Tests for `call_provider_stream()` cumulative token accumulation** — add `tests/test_ollama_stream_cumulative.py` with 4 async tests covering: (1) `_cumulative_tokens` increases by prompt+eval counts after a completed stream; (2) `_tokens_by_provider["ollama"]` is populated with per-provider counts; (3) `_cumulative_tokens` is unchanged when the stream raises an error; (4) two sequential streams accumulate correctly in `_cumulative_tokens`; validates the token-accounting logic in `call_provider_stream()` introduced in task #39/#36 | Low | `tests/test_ollama_stream_cumulative.py` | 39, 36 | ✅ Done |
| 54 | **Tests for `reset_token_usage()` per-provider and full-reset behaviour** — add `tests/test_token_usage_reset.py` covering: (1) `reset_token_usage()` with no args zeros both `_cumulative_tokens` and `_tokens_by_provider`; (2) `reset_token_usage("ollama")` clears only `_tokens_by_provider["ollama"]` and reduces `_cumulative_tokens` by the removed counts; (3) calling `reset_token_usage("unknown")` on a missing provider is a no-op; (4) `token_usage_summary()` reflects zeroed state immediately after reset; ensures the reset path introduced alongside `_tokens_by_provider` in task #25 is regression-tested | Low | `tests/test_token_usage_reset.py` | 25, 53 | ⬜ Open |

| 54 | **Standardise module-level `import os` alias to `_os` in `providers.py`** — the file currently uses the unaliased `import os` while all other stdlib imports (`json as _json`, `time as _time`) use the underscore-prefix convention; rename the module-level import to `import os as _os` and update all ~25 call sites (`os.getenv(...)` → `_os.getenv(...)`) so the import style is consistent throughout the module; no behaviour change, pure style hygiene; depends on #52 being merged first | Low | `src/llm/providers.py` | 52 | ⬜ Open |
| 53 | **Add `POST /health/llm/reset` endpoint to reset token counters at runtime** — now that `/health/llm` exposes live token usage, operators need a way to reset counters without restarting the bot; add a `POST /health/llm/reset` route in `src/discord_web.py` that calls `reset_token_usage()` (optionally scoped to a single provider via `?provider=` query param) and returns the zeroed-out summary; protect the endpoint with the existing `_require_api_action_auth` guard so only authenticated callers can reset metrics | Low | `src/discord_web.py` | 47 | 📋 Proposed |
| 55 | **Unit tests for `telemetry.record()` disabled / enabled paths** — add `tests/test_telemetry_record.py` covering: (1) `record()` is a no-op when `_ENABLED=False` (no file written); (2) when `_ENABLED=True`, calling `record()` appends a valid JSONL line to `_LOG_PATH` with all expected fields; (3) `record()` swallows write exceptions without raising; (4) `tail()` returns the last N records as parsed dicts; (5) `summarise()` returns the no-records message when given an empty list; patches `_LOG_PATH` and `_ENABLED` via `monkeypatch.setattr` so real files are never touched | Low | `tests/test_telemetry_record.py` | 49 | ⬜ Open |
| 56 | **Unit tests for `telemetry_alert.py` latency and success-rate logic** — add `tests/test_telemetry_alert.py` with pytest cases covering: (1) `percentile()` returns correct value for p50/p95/p99 on a known sorted list; (2) `compute_latencies()` groups values by provider correctly; (3) `--max-latency` flag triggers exit 1 and prints the correct provider line when p95 exceeds the limit; (4) combined success-rate failure and latency failure both appear in output and exit is still 1; (5) all-OK path exits 0 and prints the combined summary line; (6) `--latency-percentile 50` uses median instead of p95; drives all cases via `subprocess.run` or by importing `main()` with `monkeypatch` on `sys.argv`; depends on task #43 | Low | `tests/test_telemetry_alert.py` | 43 | 📋 Proposed |

| 50 | **Unit tests for `chat_openai_stream` and `chat_anthropic_stream`** — add `tests/test_provider_streaming.py` with 6 tests: (1) openai SSE tokens yielded in order, (2) `[DONE]` sentinel causes clean stop, (3) malformed JSON lines skipped with valid tokens still yielded, (4) proxy URL used when `COPILOT_PROXY_ENABLED=True`, (5) anthropic `content_block_delta` events yield tokens, (6) `message_stop` event causes clean termination | Low | `tests/test_provider_streaming.py` | 1, 27 | 🔄 In Progress — Agent t50-stream-tests |
| 56 | **Fix remaining `test_model_selection.py` failures caused by missing `SKILLS` attribute and stale Gemini routing assumptions** — after task #48 corrected the provider patches, 6 tests still fail: two raise `AttributeError: module 'llm.chat' has no attribute 'SKILLS'` (the tests patch `llm.chat.SKILLS` but the attribute no longer lives there after a refactor), and four stream tests get a real Gemini response instead of the mocked Copilot reply because `COPILOT_PROXY_ENABLED` is checked outside the inline-import scope; fix by (1) updating `SKILLS` patch targets to the correct module (likely `skills.reporting_skills` or wherever the constant now lives), and (2) auditing `chat_stream` routing logic to ensure the `COPILOT_PROXY_ENABLED` guard reads from the patched `llm.providers` namespace at call time, not an earlier-bound local | Low | `tests/test_model_selection.py` | 48 | ✅ Done — 60dbd28 |
| 57 | **`scripts/mini_model_eval_summary.py` — eval log analyser** — `log_mini_model_eval()` (task #32) writes `data/mini_model_eval.jsonl` but there is no tool to read it; add `scripts/mini_model_eval_summary.py` that reads the JSONL log and prints: (1) total mini-model selections vs near-miss "full" selections, (2) word-count histogram (bins: ≤10, 11-25, 26-50, 51-75, >75), (3) estimated token savings assuming mini-model costs 0.15× full-model; the script should accept `--path` (override log path), `--tail N` (last N records only), and `--json` (output raw summary as JSON for dashboarding); this completes the original task #32 scope which specified the summary script | Low | `scripts/mini_model_eval_summary.py` | 32 | 📋 Proposed |
| 58 | **Integration tests for `check_proxy_health()` against a local HTTP stub** — complement the unit tests in `tests/test_proxy_health_loop.py` (task #49) with `tests/test_proxy_health_integration.py` that drives the real `check_proxy_health()` function end-to-end using `pytest-aiohttp`'s `aiohttp_server` fixture: (1) server returns HTTP 200 → `proxy_is_healthy()` becomes `True`; (2) server returns HTTP 503 → `proxy_is_healthy()` becomes `False`; (3) server closes the connection (no response) → `_proxy_healthy` becomes `False` and no exception propagates; (4) `check_proxy_health(timeout=0.01)` with a server that sleeps 0.5 s → returns `False` without hanging; patches `COPILOT_PROXY_URL` via `monkeypatch.setenv` and reloads the module so the real URL points at the local stub; validates the actual HTTP logic that the unit tests skip | Low | `tests/test_proxy_health_integration.py` | 49 | 📋 Proposed |
| r37 | **Unit tests for `POST /health/llm/reset` and `GET /health/llm/circuit`** — add `tests/test_discord_web_circuit.py` covering: (1) `GET /health/llm/circuit` returns a dict with all known providers and their `{"open": bool}` state without token data; (2) `POST /health/llm/reset` with no `?provider` resets all providers and returns `{"reset": [...], "circuit_state": {...}}`; (3) `POST /health/llm/reset?provider=copilot` only resets copilot and returns `{"reset": ["copilot"], ...}`; (4) both action endpoints return 401 when auth is required and the token is missing; (5) the reset endpoint is idempotent (calling twice produces the same circuit_state); use `pytest-aiohttp` with `aiohttp_client` fixture and patch `llm.providers._circuit`, `llm.providers._is_open`, and `llm.providers.reset_circuit` via `monkeypatch` | Low | `tests/test_discord_web_circuit.py` | r37 | 📋 Proposed |
| 59 | **Unit tests for `retry_count` telemetry propagation** — task #34 added `retry_count` to `ProviderResponse` and `_call_with_retry`, but has no dedicated tests; add `tests/test_retry_telemetry.py` covering: (1) `_call_with_retry` returns `(result, 0)` on first-try success; (2) returns `(result, 1)` after one transient failure (HTTP 429); (3) raises after all retries exhausted; (4) `_call_one("openai", …)` sets `resp.retry_count=0` on success and `resp.retry_count=1` after one retry; (5) `telemetry.record()` writes `"retry_count": N` to the JSONL entry; use `aioresponses` or `AsyncMock` to inject HTTP 429 → 200 sequences | Low | `tests/test_retry_telemetry.py` | 34 | 📋 Proposed |
| r42 | **Unit tests for `call_provider_with_fallback()` telemetry integration** — task r42 wired `telemetry.record()` into every attempt inside `call_provider_with_fallback()`; add `tests/test_fallback_telemetry.py` covering: (1) success on first provider calls `telemetry.record` once with `success=True`, `retry_count=0`, and the correct `provider`/`model`/`latency_ms` from the `ProviderResponse`; (2) first provider fails, second succeeds — `record` is called twice: once with `success=False` + `retry_count=0`, once with `success=True` + `retry_count=1`; (3) all providers fail — `record` is called once per non-circuit-open attempt, all with `success=False`; (4) when `ROUTING_TELEMETRY` is unset/false, `telemetry.record` is never imported or called; (5) circuit-open providers are skipped and do NOT produce a telemetry entry; patch `ROUTING_TELEMETRY` via `monkeypatch.setenv`, `call_provider` via `AsyncMock`, and `llm.telemetry.record` via `monkeypatch.setattr` | Low | `tests/test_fallback_telemetry.py` | r42 | 📋 Proposed |
| 60 | **Persist `scan_providers()` latency to `telemetry.record()`** — `scan_providers()` now measures per-provider round-trip latency (task r44) but does not write it to the JSONL audit log; call `telemetry.record(event="provider_scan", data=status)` in `startup.py` after `_log_availability_summary()` so each bot startup emits one audit entry containing `available` and `latency_ms` for all four providers; add `tests/test_startup_telemetry.py` covering: (1) `telemetry.record` is called exactly once per `scan_providers()` invocation, (2) the `data` dict includes all four provider keys with the correct shape, (3) when telemetry is disabled (`_ENABLED=False`) the call is a no-op and does not raise; the entry enables long-term latency trend analysis via the existing `telemetry_alert.py` script | Low | `src/llm/startup.py`, `tests/test_startup_telemetry.py` | r44 | 📋 Proposed |

### Suggested dispatch waves

```
Wave 1 (no deps — all parallel):  #1, #2, #3, #5, #6, #8, #9, #11, #13, #15
Wave 2 (after wave 1):            #7 (needs #6), #10 (needs #6), #4 (needs #3),
                                  #14 (needs #13), #16 (needs #10)
Wave 3 (after wave 2):            #12 (needs #2), #17 (needs #2), #18 (needs #6, #7)
```

| r30 | **Extend PROVIDER_STREAM partial-chunk yields to Gemini and Ollama paths** — `_stream_copilot_chunks()` (task r30) wired progressive Slack placeholder-edits for the Copilot proxy path only; the Gemini streaming SDK (`generate_content_stream`) and Ollama (`/api/generate` chunked JSON) both natively support incremental tokens but `chat_stream` still awaits a complete response before yielding; refactor the Gemini and Ollama paths in `chat_stream` to yield `(partial_text, False, {})` as tokens arrive so the `on_partial_chunk` → Slack edit chain works for all providers; measure edit-rate to respect Slack's rate limits and tune `_STREAM_SLACK_EDIT_INTERVAL` accordingly | Medium | `src/llm/chat.py` | r30 | 📋 Proposed |

---

## Proposed Waves 9–11

> **Orchestrator note:** Use a fleet of parallel agents for each wave. One orchestrator manages the fleet. Workers claim a task by updating its Status cell before writing any code. Waves within a wave are all parallel; later waves depend on earlier ones as noted.

### Architectural Context

Before dispatching Wave 9, agents should understand the design intent:

- **Do not build a singleton manager.** `providers.py` already has effective module-level singletons (circuit breakers, `_last_usage` ContextVar, `_cumulative_tokens`). Adding another manager class creates a God-object anti-pattern.
- **Do build a `RequestTrace` dataclass** (task W9-A below). One instance per request, threaded by reference through `chat()` → skill dispatch → `call_provider()`. Each layer annotates it. This is the key enabler for response transparency and observability.
- **Ensemble/race routing** (fire 2 providers in parallel, return the fastest) is intentionally deferred — it doubles spend and complicates audit semantics. Improve routing *prediction* first (Wave 10), then revisit.

---

### Wave 9 — Response Transparency

> **Goal:** users know which model answered them and which skills were used. All 4 tasks are parallel.

| # | Task | Impact | Where | Depends On | Status |
|---|------|--------|-------|------------|--------|
| W9-A | **`RequestTrace` dataclass** — create `src/llm/trace.py` with `@dataclass class RequestTrace(model_used, provider, skills_invoked: list[str], routing_reason, latency_ms, mini_model_used: bool)`; thread it through `chat()` and `_resolve_non_gemini_reply()` as an optional kwarg (defaults to a fresh instance); populate `provider` and `model_used` from the returned `ProviderResponse` | High | `src/llm/trace.py` (new), `src/llm/chat.py` | — | ✅ Done |
| W9-B | **Skill invocation tracking** — in `chat.py`, wherever a skill is invoked inline (e.g., `generate_sports_watch_report`, `generate_web_search_report`, `search_web`), append the canonical skill name to `trace.skills_invoked`; no new infrastructure — instrument the existing `routing_notes` pattern; also set `trace.mini_model_used = True` when the mini-model fast-path fires | Medium | `src/llm/chat.py` | W9-A | ✅ Done |
| W9-C | **Rich Slack response footer** — after `chat()` resolves, construct a footer from `RequestTrace`: `_via gpt-4o (copilot) · skills: search_web, weather_`; extend `SHOW_ROUTING_DEBUG` env var: `=1` shows model+provider only, `=2` shows model+provider+skills+latency; suppress footer when trace is empty (e.g. Gemini fallback with no skills) | Medium | `src/llm/chat.py`, `src/slack_bot.py` | W9-A, W9-B | ✅ Done |
| W9-D | **`RequestTrace` unit tests** — `tests/test_request_trace.py`: (1) trace is populated after `call_provider()` returns; (2) skill name appended when skill is invoked; (3) footer includes model name when `SHOW_ROUTING_DEBUG=1`; (4) footer includes skills when `SHOW_ROUTING_DEBUG=2`; (5) footer suppressed when debug disabled; use `AsyncMock` for providers | Low | `tests/test_request_trace.py` | W9-A, W9-B, W9-C | ✅ Done |

---

### Wave 10 — Speed & Latency

> **Goal:** the correct answer arrives faster. W10-A and W10-B are parallel; W10-C is independent.

| # | Task | Impact | Where | Depends On | Status |
|---|------|--------|-------|------------|--------|
| W10-A | **Query classification LRU cache** — `classify_query_llm()` costs ~200 ms per call (it fires an LLM); cache results by `sha256(stripped_query[:200])` in a module-level `_classify_cache: dict[str, tuple[str, float]]` (value is `(result, timestamp)`); evict entries older than `CLASSIFY_CACHE_TTL` (default 300 s, env-var overridable); log cache hits in `RequestTrace.routing_reason` as `"classify: cache-hit"`; add a `clear_classify_cache()` helper for tests | High | `src/model_routing_policy.py` | — | ✅ Done |
| W10-B | **Perplexity fast-path regex bypass** — `select_web_search_route()` fires both a regex and an LLM classification before routing to Perplexity; add a `_HIGH_CONFIDENCE_SEARCH_RE` pattern (covers "today", "current score", "live", "right now", "latest", "yesterday's", etc.) that, when matched, skips `classify_query_llm()` entirely and returns `prefer_search=True` immediately; log bypass as `"classify: regex-bypass"` in `RequestTrace.routing_reason`; saves 200 ms on high-volume sports/news queries | High | `src/model_routing_policy.py` | — | ✅ Done |
| W10-C | **Progressive Slack "thinking" placeholder** — immediately after receiving a user message (before any provider is called), post a `_⏳ Thinking…_` placeholder message; edit it in-place with real content as tokens arrive; eliminates the perceived "silent wait" for the user without reducing actual latency; wire `on_partial_chunk` in `slack_bot.py` to edit the placeholder with each `chat_stream()` yield | Medium | `src/slack_bot.py` | — | ✅ Done |
| W10-D | **Provider latency-aware routing** — extend `select_auto_route()` in `model_routing_policy.py` to read cached `scan_providers()` latency data (`get_provider_status()`); if Copilot p95 latency (from last N audit-log entries) exceeds `LATENCY_SWITCH_THRESHOLD_MS` (default 2000 ms), temporarily deprioritise it in favor of Ollama or OpenAI; log reason in `RequestTrace`; auto-reverts when latency recovers; prevents routing to a slow provider when a faster one is available | Medium | `src/model_routing_policy.py`, `src/llm/providers.py` | W9-A | ✅ Done |
| W10-E | **Tests for classification cache and regex bypass** — `tests/test_classify_cache.py`: (1) cache returns stored result on second call with same query; (2) cache expires after TTL; (3) `clear_classify_cache()` empties cache; (4) regex bypass fires for known high-confidence patterns; (5) regex bypass does NOT fire for ambiguous queries (LLM still called) | Low | `tests/test_classify_cache.py` | W10-A, W10-B | ✅ Done |

---

### Wave 11 — Quality & Governance

> **Goal:** understand what's working, tune the system over time. All tasks are parallel.

| # | Task | Impact | Where | Depends On | Status |
|---|------|--------|-------|------------|--------|
| W11-A | **Per-skill cost attribution** — extend `token_usage_summary()` to include `"by_skill": {"search_web": {"input": N, "output": N}}` by reading `skills_invoked` from `RequestTrace` at telemetry write time; allows operators to see which skills drive token spend | Low | `src/llm/telemetry.py`, `src/llm/providers.py` | W9-A, W9-B | ✅ Done |
| W11-B | **Feedback reaction logging** — after the bot posts a response, add 👍/👎 reactions; if a user reacts within 60 s, write `{timestamp, query_hash, model, provider, skills_invoked, rating: 1/-1}` to `data/feedback.jsonl`; provides ground-truth for future routing tuning without any survey friction | Medium | `src/bot.py` | W9-A | ✅ Done |
| W11-C | **`scripts/routing_recommender.py`** — reads last 1000 `data/routing_audit.jsonl` entries, computes per-provider p95 latency + success rate + avg token cost, and prints a Markdown table plus a recommendation sentence (e.g. "Consider switching to `cost-saver` — Copilot p95 latency is 3.2 s vs Ollama 0.8 s"); runs standalone, no bot required | Low | `scripts/routing_recommender.py` | — | ✅ Done |
| W11-D | **Routing profile A/B testing mode** — add `ROUTING_AB_SPLIT` env var (e.g. `copilot-first:60,balanced:40`); `select_auto_route()` probabilistically picks a profile weighted by these percentages; logs which profile was chosen in `RequestTrace.routing_reason` and the telemetry audit log; enables controlled comparison of routing profiles on live traffic without a config change | Medium | `src/model_routing_policy.py` | W9-A | ✅ Done |
| W11-E | **Model answer quality self-check** — optional `QUALITY_CHECK=1` mode: after a provider returns a response, fire a second `quick_generate()` call asking "Is this a complete, helpful answer? yes/no"; if "no", retry with the next provider in the fallback chain before returning to the user; log check outcome in `RequestTrace`; trade-off: doubles latency when triggered, but prevents obviously-bad answers from reaching the user | High | `src/llm/providers.py`, `src/model_routing_policy.py` | W9-A | ✅ Done |
| W11-F | **`/feedback summary` Slack command** — reads `data/feedback.jsonl`, computes thumbs-up rate per provider + per skill, and posts a formatted response; allows server owners to see which configurations users rate highest without leaving Slack | Low | `src/slack_bot.py` | W11-B | ✅ Done |

---

### Wave 12 — Developer Experience

> **Goal:** easier to add new providers and skills; better local dev loop. All tasks are parallel.

| # | Task | Impact | Where | Depends On | Status |
|---|------|--------|-------|------------|--------|
| W12-A | **Provider plugin interface** — define an abstract `ProviderPlugin(ABC)` protocol in `src/llm/provider_plugin.py` with `async def call(...)`, `async def ping(...)`, `async def stream(...)` methods; refactor `providers.py` to load all providers from a registry (`_PROVIDERS: dict[str, ProviderPlugin]`) populated at import time; makes adding a new provider a single-file change (`src/llm/provider_plugins/myprovider.py`) without touching `providers.py` | High | `src/llm/provider_plugin.py` (new), `src/llm/providers.py` | — | ✅ Done |
| W12-B | **Local dev mode** — `DEV_MODE=1` env var: route all `call_provider()` calls to a local echo stub (returns the system prompt + "STUB" + first 50 chars of the message) so developers can run the bot without any API keys or Ollama; log clearly in routing footer `_[DEV MODE — stub response]_` | Medium | `src/llm/providers.py` | — | ✅ Done |
| W12-C | **`/admin reload-routing`** Slack command — reloads `model_routing_policy.py` via `importlib.reload()` without restarting the bot; useful for tuning routing profiles or regex patterns in production without a deploy cycle; restrict to bot owner | Low | `src/slack_bot.py` | — | ✅ Done |
| W12-D | **Provider integration test harness** — `scripts/provider_smoke_test.py` sends a fixed test message to each configured provider and prints the response + latency; validates API keys, proxy URL, and Ollama availability end-to-end; usable as a pre-deploy check in CI | Low | `scripts/provider_smoke_test.py` | — | ✅ Done |

---

### Suggested Wave Dispatch Order

```
Wave 9 (all parallel, no deps):    W9-A, W9-B, W9-C, W9-D
Wave 10 (all parallel):            W10-A, W10-B, W10-C, W10-D, W10-E
Wave 11 (after Wave 9):            W11-A, W11-B, W11-C, W11-D, W11-E, W11-F
Wave 12 (independent, any time):   W12-A, W12-B, W12-C, W12-D
```
