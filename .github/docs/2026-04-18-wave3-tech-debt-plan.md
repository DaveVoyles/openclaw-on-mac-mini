# Wave 3 — Tech Debt: CI/DX/Security Quick Wins

**Date:** 2026-04-18  
**Status:** 🚀 Launching  
**Risk:** Low–Medium

## Context

P2 wave complete (7 todos done, committed). Wave 3 implements the highest-ROI items
from Luke (CI/DX) and Darth (architecture/security) planning agents, plus the 4
leftover P2 todos still pending.

## Target Outcome

- Faster CI feedback (caching + format enforcement)
- Better local dev workflow (Makefile targets, .editorconfig, validate_env)
- Security/reliability quick wins (DB timeout, health check, error format)
- Docs stamped with last-updated timestamps
- guide.html has full command coverage

---

## Wave 3 Lane Plan

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|------------|--------|
| 1 | Han 😉🚀 | M | Docs timestamps + guide.html + config/skills cleanup + script dedup | — | 🚀 Active |
| 2 | Yoda 👽✨ | M | CI: cache artifacts + ruff format check + pip-audit + expensive marker | — | 🚀 Active |
| 3 | Leia 👑💁‍♀️ | S | DX: Makefile targets + .editorconfig + validate_env.py | — | 🚀 Active |
| 4 | Chewy 🐻💪 | S | Security: DB timeout + health check + error response format | — | 🚀 Active |

All lanes independent. No blockers.

---

## Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| Launch | All | — | 4 lanes launched in parallel |

---

## Wave 3 Todos

**Han (Lane 1):**
- w3-docs-timestamps — timestamps on 42 docs
- w3-guide-html — guide.html command coverage
- w3-config-skills — remove empty dir
- w3-script-dedup — consolidate install scripts

**Yoda (Lane 2):**
- w3-ci-cache — cache ruff/mypy/pytest
- w3-ci-format — enforce ruff format in CI
- w3-ci-pip-audit — pip-audit in security.yml
- w3-ci-test-excludes — @pytest.mark.expensive marker

**Leia (Lane 3):**
- w3-dx-makefile — Makefile DX targets
- w3-dx-editorconfig — .editorconfig
- w3-dx-validate-env — scripts/validate_env.py

**Chewy (Lane 4):**
- w3-sec-db-timeout — SQLite timeout standardization
- w3-sec-health-check — enhance /health endpoint
- w3-sec-error-format — APIErrorResponse model

**Deferred (XL — future sprint):**
- td-p3-oversized-fns — refactor 800-1300L functions
- td-wave3-007 — bot.py God object refactor
- td-wave3-008 — CLI circular dep resolution

---

## Validation

Each lane runs smoke tests (108) before committing.  
CI must pass after all pushes.

---

## Wave 3 Retrospective

### Actual vs. Estimated
- Lane 1 (Han): M → 119s ✅ fast
- Lane 2 (Yoda): M → 197s ✅ on target
- Lane 3 (Leia): S → 117s ✅ fast
- Lane 4 (Chewy): S → 276s ⚠️ ran long (18-file sqlite scan)

### What went well
- Zero merge conflicts across 4 parallel lanes
- All 14 todos delivered; smoke 108/108 throughout
- Han finished early; no rebalancing needed (scope was well-matched)

### What to improve for Wave 4
- Chewy's sqlite scan underestimated at S; should be M for multi-file src/ work
- Pre-flight: size any task touching >10 src/ files as M minimum

---

## Wave 4 Plan

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|------------|--------|
| 1 | Han 😉🚀 | S | Dependabot setup + CI reorder for fast feedback | — | 🚀 Active |
| 2 | Yoda 👽✨ | M | Structured logging: trace_id injection + handler decorator | — | 🚀 Active |
| 3 | Leia 👑💁‍♀️ | S | Mark slow tests + docs/TESTING.md | — | 🚀 Active |
| 4 | Chewy 🐻💪 | M | Exception chaining + vector store LRU cache + CI failure guide | — | 🚀 Active |

### Wave 4 Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| 21:53 | All | — | Wave 4 launched (4 lanes in parallel) |
