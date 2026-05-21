# OpenClaw — Documentation & Architecture Audit Report
<!-- Updated: 2026-05-21 -->

> **What this is:** A point-in-time audit of `docs/` against the actual code, produced 2026-05-21. Use it to refresh stale claims and to understand the gap between what's documented and what's running.
>
> **Method:** Three parallel research lanes verified every concrete claim (file paths, line counts, module structure, command counts, control flow) against the code on disk. See the "Verification" section for the exact facts used as ground truth.

---

## TL;DR

| Severity | Count | Examples |
|---|---|---|
| ❌ **Stale facts** — claim is wrong | 20+ | `src/llm.py` referenced (doesn't exist), 7 cogs (actually 40), 84 tools (actually 118) |
| ⚠️ **Drifted** — partly right, partly stale | 10+ | Module split tables based on old refactor snapshots; line counts off |
| ✅ **Verified accurate** | — | Most high-level architecture descriptions, recommended reading order, dependency map import rules |

The docs were last broadly updated **2026-04-18**. Significant code growth has occurred since:

- `src/bot.py` was further split — `src/discord_background.py` is now a re-export shim; the real loop logic lives in `src/bg_tasks.py`, `src/bg_briefing.py`, `src/bg_healing.py`, `src/bg_monitoring.py`.
- The LLM monolith `src/llm.py` is gone; LLM code is now a package at `src/llm/` (10 modules) plus `src/llm_client.py` for infra.
- `src/discord_commands` became a package (21 submodules) — previously a single file.
- `src/cogs/` grew from 7 → 40 cogs.
- `config/tools.yaml` grew from 84 → 118 tool declarations.
- The memory subsystem split into 9 `src/memory_*.py` modules; the vector store split into 6 `src/vector_store*.py` modules.
- A plugin system was added under `src/plugin_system/` (4 modules) with on-disk plugins in `plugins/examples/`.
- A second scheduler (`src/scheduler_advanced.py`) was added alongside `src/scheduler.py`.

---

## Verified ground truth (2026-05-21)

| Fact | Value |
|---|---|
| `src/*.py` files | **180** |
| `src/cogs/*.py` files | **40** |
| `src/discord_commands/*.py` files | **21** (package, not single file) |
| `src/llm/*.py` files | **10** |
| `skills/*.py` files | **22** |
| ClawHub skill bundles in `skills/<name>/` with `SKILL.md` | **12+** |
| `config/tools.yaml` `name:` entries | **118** |
| `src/bot.py` lines | **966** |
| `src/openclaw_cli.py` lines | **6,663** |
| `src/discord_background.py` | **re-export shim** — real code in `bg_*.py` |
| `src/llm.py` | **does not exist** — see `src/llm/chat.py` + `src/llm_client.py` |
| `src/memory_manager.py` | **does not exist** — see `src/memory_*.py` family |
| `src/autonomous_skills.py` | **does not exist** — see `skills/autonomous-loop/` bundle + `src/agent_loop.py` |

---

## Stale claims by file

### `docs/AGENT-GUIDE.md`

| Line | Claim | Reality | Fix |
|---|---|---|---|
| 14–21 | Flow ends in "`skills/*.py`" | True, but `src/llm/chat.py` (not `src/llm.py`) is the routing entrypoint | Update arrow diagram |
| 42 | "`worker_agent.py` bypasses the router" | Stale — `worker_agent.py` does use the LLM stack today | Remove or rewrite |
| 44–48 | `openclaw_cli.py` is "~4,654 lines" | **6,663 lines** | Update or drop count |
| 65–88 | Module split table is "post-TD-7 through TD-34" | Sizes drifted significantly | Either regenerate or add "snapshot date" caveat |
| 103 | "`config/tools.yaml` (84 tools)" | **118 tools** | Update count |
| 96–104 | "Key files" list | All files still exist; counts and surroundings stale | Refresh |

### `docs/MODULES.md`

This file has the most drift. The "src/\*.py — 149 files" header alone is wrong (180 actual). Specific issues:

| Line | Claim | Reality |
|---|---|---|
| 7 | "149 files" | **180** |
| 16 | `autonomous_skills.py` | does not exist |
| 17 | `bot.py` 1,146 lines | **966 lines** |
| 32 | `llm.py` exists | does not exist (now `src/llm/`) |
| 39 | `memory_manager.py` | does not exist |
| 45 | `openclaw_cli.py` ~4,654 lines | **6,663 lines** |
| 72 | `discord_commands.py` (single file, 1,130 lines) | now a **package** with 21 modules |
| 79 | "7 cogs, 36 commands" | **40 cog files** |
| 97 | "106 tool declarations" | **118** |
| 104–115 | "skills/ has 4 .py files" | **22 .py files**, plus 12+ bundle dirs |

### `docs/ARCHITECTURE.md`

Generally accurate at the *shape* level; specific counts and module sizes are stale.

| Line range | Issue |
|---|---|
| 120–128 | `bot.py` 1,146 lines / `discord_background.py` 702 lines — both stale |
| 171–189 | "17 cogs and 80+ commands" — now 40 cogs |
| Throughout | Mermaid diagrams reference real files but predate the LLM package split, the bg_* split, and the discord_commands package |

### `docs/DEPENDENCY_MAP.md`

| Line | Claim | Reality |
|---|---|---|
| 145–146 | `openclaw_cli.py` is "~13,300 lines" | **6,663 lines** (file *shrank* as code was extracted to submodules; doc never caught up) |

Import-rule guidance (sections "Import Rules", "How `_PREFS` Is Shared", "Circular Import Prevention") is still accurate and valuable.

### `docs/SKILL_DEVELOPMENT.md`, `docs/PLUGIN_DEVELOPMENT.md`, `docs/PLUGIN_API.md`, `docs/CONTRIBUTING.md`

- **SKILL_DEVELOPMENT.md** — implies skills are added under `src/<name>_skills.py`. Reality: most new skills live in `skills/*.py` and are wired through `skills/__init__.py` (`SKILLS` dict, 823 lines). Both patterns exist; the doc should call out which is canonical.
- **PLUGIN_API.md / PLUGIN_DEVELOPMENT.md** — overstate the plugin runtime. The loader works (manifest discovery, `on_load`, skill registration, persistent enable/disable state). But "register a Discord command from a plugin" is **logged only** today (see `src/plugin_system/plugin_api.py:147-150`). Docs should be honest about this.
- **CONTRIBUTING.md** — references file paths that have moved.

---

## What's missing

The current docs do not provide:

1. **A single map of where to add each extension type.** Spread across SKILL_DEVELOPMENT, PLUGIN_DEVELOPMENT, CONTRIBUTING, AGENT-GUIDE, MODULES. → Solved by **`docs/AGENT-EXTENSION-GUIDE.md`** (new).
2. **An up-to-date package overview.** `MODULES.md` is per-file but missing the `src/` subpackages (`src/llm/`, `src/api/`, `src/builders/`, `src/exporters/`, `src/plugin_system/`, `src/dashboard/`, `src/discord_commands/`). → Solved by section "Source layout" in refreshed ARCHITECTURE.md.
3. **A canonical list of background loops.** `discord_background.py` is now a shim; the actual loops are scattered across `bg_briefing.py`, `bg_healing.py`, `bg_monitoring.py`, `bg_tasks.py`, plus `agent_loop.py`, `dream_cycle.py`, `research_agent.py`, `worker_agent.py`, `incident_copilot.py`. → New section in `AGENT-EXTENSION-GUIDE.md`.
4. **Plugin honesty.** Docs imply Discord command registration from plugins is operational; code logs and returns. → Called out in extension guide.
5. **Scheduler differentiation.** `scheduler.py` (in-memory + JSON) vs `scheduler_advanced.py` (SQLite + retries) — no doc currently explains when to use which.

---

## Recommended actions (and what this audit produced)

| Action | Status |
|---|---|
| Produce this audit report | ✅ done — this file |
| Add `docs/AGENT-EXTENSION-GUIDE.md` covering 9 extension surfaces | ✅ done |
| Patch `docs/AGENT-GUIDE.md` (counts, dead refs, refreshed flow diagram) | ✅ done |
| Patch `docs/MODULES.md` (counts, dead refs, subpackage section) | ✅ done |
| Patch `docs/ARCHITECTURE.md` (counts, refreshed mermaid, bg_* shim note) | ✅ done |
| Mark `docs/SKILL_DEVELOPMENT.md` / `PLUGIN_*` for follow-up | ⚠️ flagged here for a future wave; not rewritten |
| Run markdown link checker | ✅ done after edits |

Follow-up that **this wave did not** complete (out of scope):

- Full rewrite of `docs/MODULES.md` per-file table — too noisy to keep accurate by hand; suggest generating from code.
- Full rewrite of `docs/SKILL_DEVELOPMENT.md` and `docs/PLUGIN_DEVELOPMENT.md`.
- Reconciliation of `docs/COMMANDS.md` (marked generated in DOCS-GOVERNANCE.md) with the 40 actual cogs.
- Inventory of every cog's commands (Yoda verified ~17 of 40; the rest are unverified).

---

## How this audit was produced

Three parallel `explore` agents:

- **LANE-001 (Han)** — line-by-line claim verification in the 7 highest-traffic docs.
- **LANE-002 (Yoda)** — fresh enumeration of `src/` packages, cogs, control flow, background loops, scheduler split, memory split, plugin system, and config/data layout.
- **LANE-003 (Leia)** — extension-surface analysis with concrete recipes per surface and gap analysis against existing extension docs.

The orchestrator reconciled their findings (Yoda's enumeration overrode one Leia claim about `src/discord_commands/`) and produced this report plus the related doc updates.

---

## Re-running this audit

Run these one-liners from `~/openclaw` to refresh ground truth:

```bash
# File counts
ls src/*.py | wc -l                       # → src/ python modules
ls src/cogs/*.py | wc -l                  # → cog count
ls src/discord_commands/*.py | wc -l      # → discord_commands submodules
ls src/llm/*.py | wc -l                   # → llm package modules
ls skills/*.py | wc -l                    # → skill modules
ls -d skills/*/ | wc -l                   # → skill bundle dirs

# Tool count
grep -c "^- name:" config/tools.yaml      # → tool declarations

# Hot file sizes
wc -l src/bot.py src/openclaw_cli.py src/llm/chat.py skills/__init__.py

# Sanity-check dead references
ls src/llm.py src/memory_manager.py src/autonomous_skills.py 2>&1 | grep "No such"
```
