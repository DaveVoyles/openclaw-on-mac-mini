# OpenClaw Documentation Governance
<!-- Updated: 2026-04-18 -->


> **Use this guide to decide where documentation belongs, how it should be labeled, and how it
> should be maintained.**

OpenClaw now uses a single canonical roadmap plus scoped, reference, operational, and historical
docs. This guide explains the taxonomy so contributors and agents know where to put new
documentation and how to keep existing docs from drifting.

---

## Quick start

When you add or update a doc:

1. If the work is roadmap, wave, or future-improvement planning, start with
   [`PRODUCT-ROADMAP.md`](PRODUCT-ROADMAP.md).
2. If the work changes contributor workflow, also check
   [`CONTRIBUTING.md`](CONTRIBUTING.md), [`DEVELOPMENT.md`](DEVELOPMENT.md), and
   [`AGENT-GUIDE.md`](AGENT-GUIDE.md).
3. If the work changes generated command metadata, regenerate `docs/COMMANDS.md` instead of
   hand-editing it.
4. If the work is a local export or scratch note, keep it out of canonical docs unless the content
   is migrated into a durable file under `docs/`.

---

## Documentation classes

| Class | Meaning | Examples | Rule |
| --- | --- | --- | --- |
| **Canonical** | Primary entrypoint for a cross-cutting topic | `docs/PRODUCT-ROADMAP.md`, `docs/index.md` | Start here first; supporting docs should point back to it |
| **Scoped** | Detailed doc for one subsystem, feature family, or planning area | `docs/UX_IMPROVEMENTS.md`, `docs/tech_debt.md`, `docs/LLM-ROUTING.md`, `docs/DASHBOARD_SURFACES.md` | Keep scope narrow; do not let scoped docs compete with canonical docs |
| **Reference / technical** | Architecture, API, module, or implementation detail docs | `docs/ARCHITECTURE.md`, `docs/API_REFERENCE.md`, `docs/CLI_ARCHITECTURE.md` | Update when behavior or structure changes |
| **Operational** | Troubleshooting, maintenance, or runbook-style docs | `docs/MAINTENANCE.md`, `docs/TROUBLESHOOTING.md`, `docs/PATREON_MONITORING.md`, `scripts/README.md` | Keep task-focused; point to code or scripts for implementation detail |
| **Generated** | Derived from code or runtime metadata | `docs/COMMANDS.md` | Regenerate from source of truth; avoid manual edits |
| **Historical / archived** | Shipped or superseded plans kept for context | `docs/archive/Discord_Improvements.md`, `docs/archive/IMPLEMENTATION-PLAN.md` | Label clearly as historical and keep the canonical entrypoint elsewhere |
| **Non-canonical artifacts** | Local exports, notes, and scratch files | `openclaw_export_*.md`, `notes.txt` | Do not treat as repository docs; migrate useful content elsewhere |

---

## Roadmap lifecycle rules

Use these rules for planning docs:

1. **Create new future work in `docs/PRODUCT-ROADMAP.md` first.**
2. **Use a scoped planning doc only when one area needs detailed wave-level history or execution
   detail.**
3. **Add a canonical-roadmap note** near the top of each scoped planning doc.
4. **Add a lifecycle label** near the top of each scoped planning doc, such as:
   - `Active, scoped planning doc`
   - `Active, scoped support doc`
   - `Historical, scoped planning doc`
5. **When a scoped roadmap becomes fully shipped or purely historical, mark it clearly** and keep
   `docs/PRODUCT-ROADMAP.md` as the active entrypoint.
6. **Do not create a new roadmap file** unless `docs/PRODUCT-ROADMAP.md` links to it and explains
   why it exists.

---

## Current classification map

### Canonical docs

- `docs/PRODUCT-ROADMAP.md`
- `docs/index.md`

### Scoped planning and support docs

- `docs/UX_IMPROVEMENTS.md`
- `docs/tech_debt.md`
- `docs/LLM-ROUTING.md`
- `docs/DASHBOARD_SURFACES.md`

### Historical planning docs

- `docs/archive/Discord_Improvements.md`
- `docs/archive/IMPLEMENTATION-PLAN.md`

### Generated docs

- `docs/COMMANDS.md`

### Reference / technical docs

- `docs/ARCHITECTURE.md`
- `docs/API_REFERENCE.md`
- `docs/MODULES.md`
- `docs/SERVICES.md`
- `docs/MEMORY-SYSTEM.md`
- `docs/CLI_ARCHITECTURE.md`
- `docs/PLUGIN_API.md`

### Operational docs

- `docs/MAINTENANCE.md`
- `docs/TROUBLESHOOTING.md`
- `docs/PATREON_MONITORING.md`
- `docs/API_SETUP.md`
- `docs/TESTING.md`
- `docs/RESEARCH-GUIDE.md`
- `docs/SKILL_DEVELOPMENT.md`
- `docs/PLUGIN_DEVELOPMENT.md`
- `scripts/README.md`

Not every reference or operational doc needs a lifecycle banner. Reserve top-of-file lifecycle
labels and canonical-roadmap notes for planning docs, historical docs, or any file that could be
mistaken for the primary roadmap.

---

## Root-level artifact policy

The repo root may accumulate local exports and notes during interactive work.

- `openclaw_export_*.md` files are **session export artifacts**, not canonical documentation.
- `notes.txt` is a **local note artifact**, not canonical documentation.
- Root-level SQLite files such as `threads.db`, `t.db`, `test.db`, and `test_t.db` should be
  treated as **local or legacy data artifacts**, not canonical runtime data or docs.
- Root-level helper scripts such as `verify_apis.py`, `verify_weather.py`,
  `test_apis_direct.py`, and `test_weekly_recap.py` are **manual verification helpers**. Keep
  them out of docs navigation and do not treat them as the primary automated test entrypoint.
- Local-only root artifacts should be ignored in `.gitignore` where practical; keep them out of
  docs navigation and planning guidance.
- If an artifact contains information worth preserving, migrate the useful content into a real doc
  under `docs/` and keep the artifact itself non-canonical.
- Prefer durable homes for new artifacts:
  - automated tests in `tests/`
  - runtime or persistent data under `data/`
  - contributor guidance under `docs/`

---

## Documentation update checklist

Before finishing a docs change:

1. Update the canonical entrypoint if the change affects cross-cutting behavior.
2. Update the scoped or reference doc if the change affects one subsystem in detail.
3. Re-label any doc whose lifecycle state changed.
4. Run the markdown link checker:

   ```bash
   python3 scripts/check_markdown_links.py
   ```

5. Run the relevant existing checks if available:

   ```bash
   pre-commit run --files <changed-docs>
   ```

---

## When not to add a new doc

Avoid creating a new doc when:

- an existing scoped doc can absorb the change cleanly
- the content belongs in `docs/PRODUCT-ROADMAP.md`
- the content is generated and should come from code instead
- the content is a local artifact or scratch note rather than durable documentation
