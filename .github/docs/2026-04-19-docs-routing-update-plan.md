# Docs/Guide Update: GitHub Models + COPILOT_TOOLS_ENABLED

**Date:** 2026-04-19  
**Status:** Planning

---

## Context

Three recent commits changed the routing defaults:
- `c4532f4` — Fixed `COPILOT_PROXY_ENABLED` → `COPILOT_AVAILABLE` in reflection + quick_generate
- `60e3774` — Set `COPILOT_TOOLS_ENABLED=true` (tool-calling now → GPT-4o via GitHub Models)
- `99028e5` — GitHub Models API live (`https://models.github.ai/inference`)

---

## What's Stale

| File | Stale content | Fix needed |
|------|--------------|------------|
| `docs/CLI_ARCHITECTURE.md` | `COPILOT_TOOLS_ENABLED` table: false=default; routes to "Copilot proxy" | Update table: true=default; routes to "GitHub Models API" |
| `docs/API_REFERENCE.md` | Provider table says Copilot proxy (:9191); "Tool calling → gemini-2.5-flash" | Update backend column to "GitHub Models API"; note tool-calling default changed |
| `docs/ARCHITECTURE.md` | Mermaid diagram: "tool-calling queries → Gemini"; "CopilotProxy localhost:9191" | Update routing arrows + provider node label |
| `docs/LLM-ROUTING.md` | "Tool-requiring queries always go to Gemini" | Update to note COPILOT_TOOLS_ENABLED=true changes this |
| `templates/tech-guide.html` | "queries go to Gemini 2.5 Flash with function calling" routing flow; tool-call chain shows Gemini | Update to show GPT-4o default for tool queries |

## NOT Changing

- Gemini rate limits section (accurate, Gemini still a fallback)
- `/chat model:gemini` command docs (intentional override, still works)
- Archive docs
- parents-guide.html / onboarding.html (no model references)

---

## Wave Plan

| Lane | Fleet | Effort | Files | Status |
|------|-------|--------|-------|--------|
| 1 | Han 😉🚀 | M | CLI_ARCHITECTURE.md, API_REFERENCE.md, LLM-ROUTING.md, ARCHITECTURE.md | Pending |
| 2 | Yoda 👽✨ | M | templates/tech-guide.html | Pending |

Both lanes independent (different file sets, no overlap).

---

## Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| Wave 1 | 2 | Yoda 👽✨ | ✅ tech-guide.html: tool routing updated to GPT-4o default; pushed ff6f290 |
| Wave 1 | 1 | Han 😉🚀 | ✅ CLI_ARCHITECTURE.md, ARCHITECTURE.md, LLM-ROUTING.md updated; pushed 60265de |

**Status: COMPLETE**
