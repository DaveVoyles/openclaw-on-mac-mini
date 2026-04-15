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

11. **⛔ CI gate: always check before starting new work.** Run `gh run list --limit 3` before beginning any wave or feature. If CI is red, classify failures as pre-existing vs. new regressions. Fix new regressions before proceeding. See [DEVELOPMENT.md → CI Gate Policy](DEVELOPMENT.md#-ci-gate-policy--read-before-starting-any-wave-of-work) for the full checklist.

---

## Module Structure (post-TD-7 through TD-34)

The CLI monolith has been split into focused modules:

| Module | Responsibility |
|--------|---------------|
| `openclaw_cli.py` | Main REPL, command dispatch shims, ~4,654 lines |
| `openclaw_cli_cli_parser.py` | `build_parser()` — extracted CLI argument parser (TD-34) |
| `openclaw_cli_help.py` | `print_chat_help()` — extracted help renderer (TD-34) |
| `openclaw_cli_ui_core.py` | ANSI palette, TTY detection |
| `openclaw_cli_render.py` | Response rendering, RenderContext |
| `openclaw_cli_auth.py` | Token/keychain, OpenClawCliError |
| `openclaw_cli_update.py` | Version check, self-update |
| `openclaw_cli_sessions.py` | Session management, event logging |
| `openclaw_cli_actions.py` | Shell command execution |
| `openclaw_cli_router.py` | Routing logic, ReplRouteDecision |
| `openclaw_cli_diff.py` | Diff colorization |
| `openclaw_cli_path_utils.py` | Path detection, link formatting |
| `openclaw_cli_macros.py` | Macro/workflow engine |

**Import rules:** Submodules never import from `openclaw_cli.py`. Globals (`_PREFS`, `_IS_TTY`)
are passed as parameters at call time. This enables independent testing and import.

See `docs/DEPENDENCY_MAP.md` for the full dependency graph and circular import prevention strategy.

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

## Discord Cog Guidance (W1–W14)

### Error Handling in Cogs

Use `build_error_embed(e, context='/cmd-name')` from `discord_error.py` for **all** Discord cog error responses. Always pass `ephemeral=True` so errors are visible only to the invoking user.

```python
from src.discord_error import build_error_embed

@app_commands.command()
async def my_command(self, interaction: discord.Interaction):
    try:
        ...
    except Exception as e:
        embed = build_error_embed(e, context="/my-command")
        await interaction.followup.send(embed=embed, ephemeral=True)
```

`classify_error(exc)` maps the exception to an `ERROR_CATEGORIES` key so the embed colour and title are consistent across all cogs.

### Progress Indicators for Long-Running Commands

For cog commands that may take more than ~2 seconds, use `ProgressTracker` from `discord_progress.py` to show a live-updating embed instead of leaving Discord's "thinking…" spinner running indefinitely.

```python
from src.discord_progress import ProgressTracker

@app_commands.command()
async def slow_command(self, interaction: discord.Interaction):
    await interaction.response.defer()
    tracker = ProgressTracker()
    await tracker.start(interaction, "Running analysis…", steps=3)
    await tracker.update("Fetching data…", step=1)
    # … do work …
    await tracker.update("Processing results…", step=2)
    # … do work …
    await tracker.done("Analysis complete.")
```

### Alert Routing

Use `send_severity_alert()` from `alert_manager.py` for **all** monitoring alerts instead of posting directly to a channel. Severity routing:

| Severity | Destination |
| --- | --- |
| `DEBUG` / `INFO` | Log only (no Discord message) |
| `WARNING` | Configured alert channel |
| `CRITICAL` | Alert channel **+** DM to bot owner |

```python
from src.alert_manager import send_severity_alert

await send_severity_alert("WARNING", "Disk usage high", "Usage at 85%", service="nas")
await send_severity_alert("CRITICAL", "Container down", "openclaw container exited", service="openclaw")
```

### Memory Recall — Domain Guard

Set `RECALL_DOMAIN_GUARD_STRICT=true` in the environment to enable strict domain suppression for memory recall. When enabled, results whose stored domain does not match the active conversation domain are silently suppressed rather than returned with a low-confidence warning.

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
