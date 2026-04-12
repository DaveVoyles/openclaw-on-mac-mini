# OpenClaw — Agent Quick Reference

**Read this first.** 30-second orientation before touching anything.

---

## Architecture in 30 Seconds

```
Discord user → src/bot.py → src/ask_orchestrator.py → src/llm/chat.py
                                                              ↓
                                           src/tool_router.py (shortlists tools)
                                                              ↓
                                           src/llm_tools.py (executes tools)
                                                              ↓
                                                 skills/*.py (skill functions)
```

**Where things run:**
- **Mac Mini M4** (192.168.1.93) — 18 Docker containers via OrbStack, all source code
- **NAS** (192.168.1.8) — Storage only (SMB + NFS mounts for media)
- **Source:** `~/openclaw/` — application code
- **Deploy config:** `~/docker-stack/openclaw/docker-compose.yml`
- **Dashboard:** `http://192.168.1.93:8765/dashboard`
- **Health:** `http://192.168.1.93:8765/health`

---

## 10 Critical Gotchas

1. **Source ≠ Deploy.** Application code lives in `~/openclaw/`. The docker-stack repo (`~/docker-stack/openclaw/`) contains only `docker-compose.yml` and env config. Edit source in openclaw, then rebuild from docker-stack.

2. **Rebuild command:**
   ```bash
   cd ~/docker-stack/openclaw && docker compose up -d --build
   ```

3. **`worker_agent.py` bypasses the router.** It uses the raw Gemini SDK directly (not `chat()`). Don't add routing logic expecting it to flow through `src/llm/chat.py`.

4. **Tests use xdist by default.** `pyproject.toml` forces `-n auto --dist loadfile`. Run single-process with:
   ```bash
   .venv/bin/python -m pytest tests/test_foo.py --override-ini="addopts=" -q
   ```

5. **Never use `model_preference="gemini"`.** All callers use `"auto"` or a specific provider name (`"copilot"`, `"perplexity"`). Zero hardcoded `"gemini"` preference strings remain — keep it that way.

6. **Fast-path guard.** Provider fast-paths in `chat()` only fire when `model_preference == "auto"` AND `recalled_context` is empty. This prevents hijacking mid-conversation follow-ups that need history.

7. **Direct-return marker.** Skills that return Perplexity results append `_via perplexity-direct_` via `_normalize_direct_provider_answer()`. `answer_policy.should_return_directly()` detects this and bypasses Gemini synthesis. Don't double-attribute or strip the marker.

8. **All new skills must be registered** in `skills/__init__.py` in the `SKILLS` dict. A function that isn't in that dict won't be callable by the tool router.

9. **Copilot proxy ≠ OpenAI.** Use `model_preference="copilot"` (not `"openai"`) to route to the GitHub Copilot proxy. `record_copilot()` in `spending.py` tracks usage (cost = $0).

10. **Two test virtualenvs.** `.venv` is the main dev venv; `.venv-test` is used by `run_tests.sh` in Docker. For local development, always use `.venv`.

---

## Key Files (Read Before Editing)

| File | Why It Matters |
|------|---------------|
| `src/llm/chat.py` | All LLM routing logic; fast-path blocks at top of `chat()` and `chat_stream()` |
| `src/model_routing_policy.py` | Route selector functions and regex patterns for each fast-path |
| `src/tool_router.py` | Keyword bundles that shortlist tools before Gemini sees them |
| `src/answer_policy.py` | `_DIRECT_RETURN_MARKERS` dict; controls when Perplexity results bypass synthesis |
| `skills/__init__.py` | `SKILLS` dict — the skill registry. New skills go here. |
| `skills/reporting_skills.py` | All Perplexity direct-return skills (news, sports, weather, finance, entertainment) |
| `src/spending.py` | Cost tracking for Gemini, Perplexity, Firecrawl, Copilot |
| `config/tools.yaml` | Gemini tool declarations (84 tools). Add new tools here + in `skills/__init__.py` |
| `src/config.py` | Centralized config — all env vars flow through here |

---

## Common Operations

```bash
# Run all tests
cd ~/openclaw && .venv/bin/python -m pytest tests/ --override-ini="addopts=" -q

# Run specific test file
.venv/bin/python -m pytest tests/test_reporting_skills.py --override-ini="addopts=" -q

# Lint
.venv/bin/ruff check src/ tests/

# Rebuild container
cd ~/docker-stack/openclaw && docker compose up -d --build

# Check logs
docker logs openclaw --tail 50 -f

# Health check
curl -s http://192.168.1.93:8765/health | python3 -m json.tool

# Check spending
curl -s http://192.168.1.93:8765/api/dashboard | python3 -c "
import sys, json
sp = json.load(sys.stdin)['spending']
print('Gemini:', sp['calls'], 'calls', f'\${sp[\"total_cost\"]:.4f}')
print('Perplexity:', sp['perplexity']['calls'], 'calls', f'\${sp[\"perplexity\"][\"total_cost_usd\"]:.4f}')
print('Firecrawl:', sp['firecrawl']['calls'], 'calls')
"
```

---

## How to Add a New Skill

1. Write the async function in the appropriate `skills/*.py` file
2. Register it in `skills/__init__.py` → `SKILLS` dict
3. Add a tool declaration in `config/tools.yaml`
4. If it should be a direct-return (Perplexity fast-path):
   - Add to `_DIRECT_RETURN_MARKERS` in `src/answer_policy.py`
   - Add keyword bundle in `src/tool_router.py`
   - Add route selector in `src/model_routing_policy.py`
   - Wire fast-path in `src/llm/chat.py` `chat()` and `chat_stream()`
5. Write tests in `tests/`
6. Rebuild: `cd ~/docker-stack/openclaw && docker compose up -d --build`

---

## Provider Routing Summary

| Provider | When Used | Cost |
|----------|-----------|------|
| `perplexity` | Real-time queries (news, sports, weather, finance, entertainment) | ~$0.005/call |
| `copilot` | Weekly recaps, cross-provider retry after low-quality Gemini | $0 |
| `gemini` (auto) | Tool-calling, multi-step reasoning, general questions | ~$0.001/call |
| `ollama` | Simple conversational turns, local-only queries | $0 |

---

## Reference Documents

| Document | Purpose |
|----------|---------|
| `docs/SERVICES.md` | Every external API — what it does, env var, why we use it |
| `docs/ARCHITECTURE.md` | Mermaid diagrams of request flow and component connections |
| `docs/MODULES.md` | Quick reference for every source file in `src/` |
| `docs/TROUBLESHOOTING.md` | Known issues and fixes |
| `docs/API_SETUP.md` | How to configure each API key |
