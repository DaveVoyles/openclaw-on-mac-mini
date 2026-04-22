# GitHub Models API — Native Support Plan

**Date:** 2026-04-19  
**Request:** Default to GitHub Copilot models via GitHub Enterprise account; no proxy required.

---

## Problem

OpenClaw's `copilot-first` routing only activates when a local proxy (`copilot-openai-api`) is running at `COPILOT_PROXY_URL`. Without it, `COPILOT_PROXY_ENABLED = False` and all routing falls back to Gemini.

GitHub Enterprise (Copilot Business/Enterprise) provides **official** access to the same models via the [GitHub Models API](https://docs.github.com/en/github-models):
- Endpoint: `https://models.inference.ai.azure.com`
- Auth: GitHub PAT (with `models:read` scope) as a Bearer token
- OpenAI SDK compatible (same JSON interface)
- Models available: `gpt-4o`, `gpt-4o-mini`, `o1-mini`, `claude-3-5-sonnet`, and more

The `GITHUB_TOKEN` is already set in the environment (used for GitHub API calls). Adding `models:read` scope to that token — or using the same token if the PAT already has it — is all that's needed.

---

## Solution

Add a `GITHUB_MODELS_ENABLED` flag to `providers.py`. When True:
- The copilot provider uses `https://models.inference.ai.azure.com` + `GITHUB_TOKEN` as Bearer auth
- No local proxy required
- `COPILOT_AVAILABLE` (new umbrella flag) = `COPILOT_PROXY_ENABLED OR GITHUB_MODELS_ENABLED`
- All call sites in `chat.py` switch to `COPILOT_AVAILABLE` instead of `COPILOT_PROXY_ENABLED`

`ROUTING_PROFILE=copilot-first` is already the default, so once the flag is live and the token has the right scope, all routing immediately prefers GitHub models.

---

## File Changes

| File | Change | Size |
|------|--------|------|
| `src/llm/providers.py` | Add `GITHUB_MODELS_ENABLED`, `GITHUB_MODELS_BASE_URL`, `COPILOT_AVAILABLE`; update `chat_openai` call path to use GitHub Models endpoint when proxy is absent | M |
| `src/llm/chat.py` | Replace ~15 `COPILOT_PROXY_ENABLED` import/usage sites with `COPILOT_AVAILABLE` | M |
| `.env.example` | Document `GITHUB_TOKEN` usage for GitHub Models API; add `GITHUB_MODELS_ENABLED` override flag | S |

**No change needed in:**
- `src/config.py` — `github_token` already exists at line 260
- `src/model_routing_policy.py` — receives `copilot_available` as a param; no change needed if callers pass updated flag

---

## Wave Plan

### Wave 1 — Solo (tightly coupled; chat.py depends on providers.py exports)

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|------------|--------|
| 1 | Solo | M | providers.py + chat.py + .env.example | — | Pending |

**Solo rationale:** The 3 files are tightly coupled on the new constant name (`COPILOT_AVAILABLE`). Splitting to parallel agents would require pre-agreeing on the interface, and the overall change is small enough that solo is faster.

---

## Key Details for Implementation

### providers.py changes
```python
# Add near line 61
GITHUB_MODELS_BASE_URL = "https://models.inference.ai.azure.com/v1"
_GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
GITHUB_MODELS_ENABLED: bool = bool(_GITHUB_TOKEN)
COPILOT_AVAILABLE: bool = COPILOT_PROXY_ENABLED or GITHUB_MODELS_ENABLED
```

In `chat_openai()`, where `use_proxy = COPILOT_PROXY_ENABLED and _proxy_healthy`:
```python
use_github_models = GITHUB_MODELS_ENABLED and not (COPILOT_PROXY_ENABLED and _proxy_healthy)
use_proxy = COPILOT_PROXY_ENABLED and _proxy_healthy

if use_proxy:
    base_url = COPILOT_PROXY_URL.rstrip("/")
    # ... existing proxy auth
elif use_github_models:
    base_url = GITHUB_MODELS_BASE_URL
    headers = {"Authorization": f"Bearer {_GITHUB_TOKEN}", "Content-Type": "application/json"}
else:
    base_url = "https://api.openai.com/v1"
    # ... existing OpenAI auth
```

### Default model for GitHub Models
GitHub Models uses the same model name strings as OpenAI (`gpt-4o`, `gpt-4o-mini`). The existing `OPENAI_MINI_MODEL = "gpt-4o-mini"` and `OPENAI_MODEL = "gpt-4o"` work without change.

### .env.example addition
```bash
# GitHub Models API (GitHub Enterprise / Copilot Business)
# Set GITHUB_TOKEN with models:read scope to enable.
# When set, OpenClaw routes copilot-first queries through models.inference.ai.azure.com
# instead of requiring a local proxy. ROUTING_PROFILE=copilot-first is already the default.
# GITHUB_MODELS_ENABLED=true  # auto-detected when GITHUB_TOKEN is set; set false to disable
```

---

## Risk Assessment

**Risk: Medium**  
- Additive: if `GITHUB_TOKEN` is empty, nothing changes
- `GITHUB_TOKEN` is already used for GitHub API; same token works for Models API with `models:read` scope
- Rate limits: 15 RPM / 150K tokens/day on free tier; higher on Enterprise — sufficient for homelab use
- Proxy takes priority if both are configured (safe fallback)

---

## Validation

- `python3 -m py_compile src/llm/providers.py src/llm/chat.py`
- `grep -n "COPILOT_AVAILABLE" src/llm/providers.py src/llm/chat.py` — confirm all sites updated
- `python3 -m pytest tests/ -x -q` (run existing tests)
- `make ship-server && make verify-deploy`

---

## Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
