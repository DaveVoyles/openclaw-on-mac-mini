# CI Latent Bugs — Inventory & Remediation

**Discovered:** 2026-06-06 while investigating why the `test` CI job (`ruff check src/ tests/`) has been red on every `main` push since ~2026-05-21.
**Root cause:** these are **real latent bugs**, not just style — ruff's `F821 undefined-name` was flagging genuine runtime crashes.

## Real bugs (F821 — would crash at runtime)

| ID | File:line | Symptom | Fix |
| --- | --- | --- | --- |
| BUG-1 | `src/slack_bot.py:3707,3721,3739,3740` | `handle_dm` (the only `@app.event("message")` handler) calls `_is_hermes_session` / `_session_is_live` / `_run_hermes_turn`, which are nested inside `_register_integration_handlers`. A Slack thread reply to a Hermes/live session → `NameError`, message handler crashes. | Promote the 3 helpers + shared `_hermes_live_procs` state (and their module-level deps) so `handle_dm` can reach them. **HIGH risk — live message handler.** |
| BUG-2 | `src/dashboard/api_handlers.py:3971,4010` | OpenAI-compatible streaming path calls `_strip_via_footer`, which is **not defined anywhere** → `NameError` on that code path. | If nothing appends a "via" footer in the stream, make it a pass-through; else define the stripper. |
| BUG-3 | `src/dashboard/api_handlers.py:4436` | `api_hermes_memory_seed_handler` uses `os.uname()`, but the module imports `import os as _os` — bare `os` is undefined → `NameError`. | `os.` → `_os.` |

## Mechanical lint (safe, mostly auto-fixable) — 56 errors

| Rule | Count | Files | Action |
| --- | --- | --- | --- |
| I001 unsorted-imports | 26 | slack_bot.py(17), api_handlers.py(8), routes.py(1) | `ruff --fix` |
| E401 multiple-imports-on-one-line | 12 | slack_bot.py(7), api_handlers.py(5) | `ruff --fix` |
| F401 unused-import | 7 | tests/(5), bot_formatting.py(1), api_handlers.py(1) | `ruff --fix` |
| E741 ambiguous-name (`l`) | 6 | slack_bot.py(5), api_handlers.py(1) | manual rename |
| F541 f-string-no-placeholder | 3 | slack_bot.py(2), host_bridge.py(1) | `ruff --fix` |
| F841 unused-variable | 2 | slack_bot.py (`restart_out`, `active`) | manual remove |

## Formatting — 17 files need `ruff format`

Pre-existing drift across `ask_executor.py`, `nas.py`, `webhook_formatter.py`, `host_bridge*.py`, `html_handlers.py`, `conftest.py`, several `tests/*`, etc.
**Exception:** `src/dashboard/helpers.py` is excluded — it has uncommitted WIP (Plex work) whose edits overlap ruff's format region. Owner formats it on commit.

## Fleet lanes

| Lane | Agent | File scope | Fixes |
| --- | --- | --- | --- |
| 1 | Han 😉🚀 | `api_handlers.py` only | BUG-2, BUG-3, + api_handlers lint + format |
| 2 | Yoda 👽✨ | `slack_bot.py` only | BUG-1 (HIGH risk) + slack_bot lint + format |
| 3 | Leia 👑💁‍♀️ | all other drifted files (NOT helpers.py / tech-guide.html) | small-file lint + format sweep |

**Done when:** `ruff check src/ tests/` clean; `ruff format src/ tests/ --check` clean except `helpers.py`; full test suite passes; code-review clean.
