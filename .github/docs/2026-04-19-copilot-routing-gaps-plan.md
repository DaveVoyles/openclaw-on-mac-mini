# Copilot-First Routing Gaps — Audit & Migration Plan

**Date:** 2026-04-19  
**Status:** Planning

---

## User Request

> "Which queries would NOT go through copilot-first? Are there any we can migrate to copilot first?"

---

## Current State

`ROUTING_PROFILE=copilot-first` is the default. `COPILOT_AVAILABLE = COPILOT_PROXY_ENABLED or GITHUB_MODELS_ENABLED`.  
With the new GitHub token (`ghp_...` + `copilot` scope) and `GITHUB_MODELS_ENABLED=true`,  
`COPILOT_AVAILABLE` is now `True` in production.

---

## Routing Gaps — What Does NOT Go Through Copilot

| # | Query type | Where routed | Reason | Migratable? |
|---|------------|-------------|--------|-------------|
| 1 | **Tool-calling queries** | Gemini (default) | `COPILOT_TOOLS_ENABLED=false` default; Gemini preferred for function calling | ✅ Yes — set `COPILOT_TOOLS_ENABLED=true` |
| 2 | **Reflection calls** (`llm_patterns.py`) | Gemini | Bug: passes `COPILOT_PROXY_ENABLED` instead of `COPILOT_AVAILABLE` | ✅ Easy fix |
| 3 | **`quick_generate` helper** (`llm_client.py`) | Gemini | Bug: checks `COPILOT_PROXY_ENABLED` not `COPILOT_AVAILABLE` | ✅ Easy fix |
| 4 | **Web search queries** | Perplexity-direct | Short-circuits before LLM routing; live web data needed | ❌ Intentional — keep |
| 5 | **Sports/schedule queries** | Perplexity-direct | Same — live schedule data | ❌ Intentional — keep |
| 6 | **Forced `model_preference="gemini"`** | Gemini | Explicit user/system override | ❌ Intentional |
| 7 | **Image generation** | Stable Diffusion | Not LLM routing | ❌ N/A |

---

## Migrations Planned

### Lane 1 (Han 😉🚀) — Bug fixes: stale `COPILOT_PROXY_ENABLED` refs
**Files:** `src/llm_patterns.py`, `src/llm_client.py`  
**Change:** Replace `COPILOT_PROXY_ENABLED` → `COPILOT_AVAILABLE` in reflection route + `quick_generate`  
**Effort:** S | **Risk:** Low

### Lane 2 (Yoda 👽✨) — Enable `COPILOT_TOOLS_ENABLED=true`
**Files:** `.env` (Mac Mini), `.env.example`, `DEPLOYMENT.md`  
**Change:** Set `COPILOT_TOOLS_ENABLED=true` in `.env`; add to `.env.example`; verify GPT-4o tool calling works  
**Effort:** M | **Risk:** Medium (tool-calling behavior changes)

---

## NOT Migrating (Intentional)

- **Web search / sports → Perplexity**: correct — live data, not LLM-answerable
- **Forced `model_preference="gemini"`**: explicit bypasses by design
- **Image generation**: Stable Diffusion, not an LLM

---

## Wave Plan

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|------------|--------|
| 1 | Han 😉🚀 | S | Fix stale `COPILOT_PROXY_ENABLED` in llm_patterns.py + llm_client.py | — | Pending |
| 2 | Yoda 👽✨ | M | Enable COPILOT_TOOLS_ENABLED + verify + doc | — | Pending |

Both lanes are independent — can launch in parallel.

---

## Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| Wave 1 | 1 | Han 😉🚀 | ✅ Fixed COPILOT_PROXY_ENABLED → COPILOT_AVAILABLE in llm_patterns.py + llm_client.py; 40/40 tests pass; pushed c4532f4 |
| Wave 1 | 2 | Yoda 👽✨ | ✅ Function calling verified (finish_reason: tool_calls); COPILOT_TOOLS_ENABLED=true set; deployed + healthy; pushed 60e3774 |

**Status: COMPLETE — both migrations deployed to production.**
