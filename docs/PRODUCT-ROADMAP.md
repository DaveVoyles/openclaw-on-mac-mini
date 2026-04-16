# OpenClaw Product Roadmap

> **Canonical source for future improvements.**
> Start here for any new roadmap, wave, follow-up, or future-state planning work.

This document is the single cross-cutting roadmap for OpenClaw. Future agents and contributors
should add new improvement work here first, then link out to domain-specific deep dives only when
the extra detail is useful.

For documentation ownership, lifecycle rules, and artifact handling, see
[`docs/DOCS-GOVERNANCE.md`](DOCS-GOVERNANCE.md).

---

## How to use this roadmap

1. Add all new future work here first.
2. Treat this file as the source of truth for what is active, deferred, or complete across the repo.
3. Use domain-specific docs such as `docs/UX_IMPROVEMENTS.md` only for detailed implementation
   history or scoped wave requirements.
4. When work ships, update this file and the supporting scoped doc together.
5. Do not create a new standalone roadmap doc unless this file links to it and explains why it exists.

---

## Roadmap map

| Doc | Role | Status | How to use it now |
| --- | --- | --- | --- |
| `docs/PRODUCT-ROADMAP.md` | Canonical cross-cutting roadmap | Active | Start here for all future work |
| `docs/DOCS-GOVERNANCE.md` | Docs taxonomy and lifecycle rules | Active support doc | Use when deciding where documentation belongs |
| `docs/UX_IMPROVEMENTS.md` | Detailed CLI UX wave history and deferred UX follow-ups | Active, scoped | Use for CLI-specific wave detail after checking this roadmap |
| `docs/tech_debt.md` | Detailed CLI tech-debt audit history | Active, scoped | Use for tech-debt detail and shipped evidence |
| `docs/Discord_Improvements.md` | Discord improvement history | Historical/scoped | Reference only; all listed waves are shipped |
| `docs/DASHBOARD_SURFACES.md` | Docs/dashboard synchronization checklist | Active support doc | Use when CLI/dashboard surfaces change |
| `docs/archive/IMPLEMENTATION-PLAN.md` | Historical implementation plan | Archived | Context only; do not treat as a live roadmap |

---

## Current priorities

| Initiative | Status | Source detail | Next step |
| --- | --- | --- | --- |
| Docs governance and roadmap consolidation | Shipped foundation | This doc, `docs/index.md`, contributor/agent guidance | Keep future doc cleanup and stale-reference fixes flowing through this roadmap |
| Per-model context limits | Shipped (W21) | `src/llm/context_limits.py`; MODEL_CONTEXT_WINDOWS dict (13 models); `/tokeninfo` model limit + usage %; 80/90/95% overflow warnings | — shipped; no follow-up needed |
| Always-on shell chrome | Shipped (W22) | `_print_shell_top_bar()` (session · model · autoroute after each response); `_print_shell_bottom_bar()` (mode + hints before each prompt); graceful degradation in plain/non-TTY/narrow modes | — shipped; future shell-chrome expansion should open a new initiative here |
| Test coverage & exception hardening | Shipped (W18–W22) | 1,165+ unit tests added for previously untested modules; 280+ broad `except Exception` catches narrowed across all `src/` files | — shipped; continue flowing new coverage gaps through this roadmap as they surface |
| CLI UX follow-up wave: context-pressure shipped; restrained narrative follow-through next | Active follow-up | `docs/UX_IMPROVEMENTS.md` | Treat the context-pressure tranche as shipped: `/tokeninfo` now carries actor breakdown + bookmark-before-clear guidance, while `/context`, `/session`, and `/watch status` already surface lighter next-send or next-retry pressure cues. The next active docs/implementation wave is the restrained narrative follow-through: let `/session` and `/sessions` express momentum or milestones without obscuring core status, keep `/collab` and `session share/export` neutral and pasteable, and continue deferring richer recap/export/dashboard storytelling until it actually lands. |
| Dashboard/docs consistency for future CLI waves | Ongoing | `docs/DASHBOARD_SURFACES.md`, `docs/CLI_ARCHITECTURE.md`, `docs/CLI_QUICKSTART.md` | Keep docs/dashboard sync as a required lane for future CLI wave work |
| CLI tech-debt follow-up planning | Audit-driven | `docs/tech_debt.md` | Use the shipped April 2026 audit as current context; start a new TD wave here only when new debt is confirmed or a new audit is warranted |
| Discord follow-up work | Dormant | `docs/Discord_Improvements.md` | Add any future Discord improvements here first instead of reviving the old roadmap as the primary entrypoint |

---

## Consolidated backlog

### 1. Cross-repo documentation governance

**Goal:** keep one discoverable roadmap that future agents can continue to build on.

**Open work:**
- keep contributor docs, landing pages, and agent instructions pointing to this roadmap
- keep old roadmap docs clearly labeled as scoped or historical
- remove stale references to superseded planning files when they surface

### 2. CLI UX deferred work

These are the highest-signal deferred items repeatedly called out in `docs/UX_IMPROVEMENTS.md`:

- broader proactive context-pressure surfacing beyond the already-shipped `/tokeninfo`, `/context`, `/session`, and `/watch status` cues
- prompt-toolkit-backed shell input follow-up only when the richer interactive-TTY editing/completion experience is worth the added dependency cost, while keeping `readline` and plain `input()` fallbacks for plain-mode, non-TTY, scripted, and missing-dependency paths
- any future shell-chrome expansion beyond the now-shipped always-on top/bottom bar pair (`_print_shell_top_bar` / `_print_shell_bottom_bar`), approval review overlay, richer TTY pickers, pane-focus cues, and review/trust/recovery approval cues
- restrained narrative/morale/dashboard storytelling follow-through that lets `/session` and `/sessions` acknowledge momentum or milestones without turning neutral handoff/export surfaces into prose-heavy recaps

When one of these becomes active, create a new entry here with owner, status, and links to the
relevant section of `docs/UX_IMPROVEMENTS.md`.

### Initiative: CLI UX follow-up wave — interactive shell surfaces landed

- **Status:** in progress
- **Owner area:** cli
- **Supporting doc:** `docs/UX_IMPROVEMENTS.md`
- **Why now:** The terminal-first CLI interaction tranche is now materially shipped: interactive REPL sessions render an always-on top context bar, high-risk approvals can open a compact review overlay, TTY overlays support arrow-key filtering plus preview panes, and layout presets report explicit pane-focus transitions. Keeping that recorded here prevents future waves from reopening already shipped interaction work.
- **Next step:** Focus the next CLI UX wave on the restrained narrative follow-through: preserve objective status-first summaries, let `/session` and `/sessions` surface momentum/milestone cues only as secondary context, keep `/collab` plus `session share/export` neutral and pasteable, and leave recap-mode exports, richer browser/dashboard storytelling, and broader mood-language experiments deferred until they actually ship.

### Initiative: Per-model context limits — shipped W21

- **Status:** shipped
- **Owner area:** cli
- **Supporting doc:** `src/llm/context_limits.py`
- **Why now:** Different models have different context windows; surfacing per-model limits closes the gap between raw token counts and actionable context-pressure guidance.
- **Next step:** Shipped. `MODEL_CONTEXT_WINDOWS` covers 13 models; `/tokeninfo` shows model limit and usage %; proactive overflow warnings fire at 80/90/95% thresholds. Any follow-up (new models, threshold tuning) should open a new entry here.

### Initiative: Always-on shell chrome — shipped W22

- **Status:** shipped
- **Owner area:** cli
- **Supporting doc:** `docs/DASHBOARD_SURFACES.md`, `docs/UX_IMPROVEMENTS.md`
- **Why now:** Operators needed persistent session context without running explicit status commands after every exchange.
- **Next step:** Shipped. `_print_shell_top_bar()` renders session · model · autoroute state after each response; `_print_shell_bottom_bar()` renders mode + hints before each prompt. Both degrade gracefully in plain/non-TTY/narrow modes. Future shell-chrome work should open a new initiative entry here.

### Initiative: Test coverage & exception hardening — shipped W18–W22

- **Status:** shipped
- **Owner area:** cross-cutting
- **Supporting doc:** `docs/tech_debt.md`
- **Why now:** Large portions of `src/` had no direct unit-test coverage and many broad `except Exception` catches silently swallowed failures, making regressions hard to detect.
- **Next step:** Shipped. W18–W22 added 1,165+ unit tests for previously untested modules and narrowed 280+ overly broad exception catches across all `src/` files. New coverage gaps and remaining broad catches should be tracked through the tech-debt audit log and surfaced here when a new wave is warranted.

### Initiative: Wave 24 — Recovery UX + Exception Hardening Finale

- **Status:** shipped
- **Owner area:** cli, cross-cutting
- **Supporting doc:** `docs/UX_IMPROVEMENTS.md`, `docs/tech_debt.md`
- **Why now:** Remaining deferred UX items — approval recap, auto-retry notes, consistent usage error messages — and the final ~102 untagged broad exception catches are actionable and low-risk.
- **Next step:** Shipped. `_print_approval_recap()` and `_print_usage()` landed in `openclaw_cli_actions.py`; module extraction (TD-28 through TD-34) complete; exception hardening across all `src/` files done.

### Initiative: Wave 25 — Finish-Line Sprint + Docs Refresh + Coverage

- **Status:** shipped
- **Owner area:** cli, cross-cutting
- **Why now:** Five partial UX items remain incomplete, docs are stale relative to W21–W24 shipped features and the TD-28–TD-34 module refactor, and test coverage has gaps in router/watch/session_display.
- **What shipped:** 5 partial UX features wired (`_print_approval_recap`, `_make_prompt` draft badge, `↺ Watch auto-retried` text, `_a11y_plain_mode`, `_session_mood_snapshot`) — Han; 218 new tests bringing suite to 600 passing — Rey; docs refresh (CLI_ARCHITECTURE.md, CLI_QUICKSTART.md, DASHBOARD_SURFACES.md) — Leia; exception format normalized across src/ — solo.
- **Next step:** Complete. W26 stale-audit lane (Leia) flipped 30 more stale checkboxes in UX_IMPROVEMENTS.md.

### Initiative: Wave 26 — Stale Audit + Checkbox Reconciliation

- **Status:** shipped
- **Owner area:** docs, cross-cutting
- **Why now:** 51 open checkboxes in UX_IMPROVEMENTS.md were identified at W26 start; W21–W25 shipped code but boxes were never flipped, causing false signal about open work.
- **What shipped:** automation state cell (`_watch_status_cell`) in default status bar; watch completion recap; 54 integration tests; 30 stale checkboxes cleared in `docs/UX_IMPROVEMENTS.md`.

### Initiative: Wave 27 — Grounding Block Inspection + Remaining Stale Audit

- **Status:** in progress
- **Owner area:** docs, cli
- **Supporting doc:** `docs/UX_IMPROVEMENTS.md`
- **Why now:** Grounding block inspection (`/context last`) remains the highest-signal open UX item, and a residual stale-checkbox audit (4 confirmed this wave) keeps the doc accurate.
- **Next step:** Wire `/context last` to surface the exact grounding block used by the last analyze/research/write action; continue stale-checkbox reconciliation.

### 3. CLI technical-debt follow-up

`docs/tech_debt.md` shows the major TD waves through TD-34a and TD-34b as shipped. Treat that
document as the audit log and shipped evidence, not the place to start new cross-cutting planning.
Any new TD work should begin here and link back to the current April 2026 audit, or to a newer
audit section if one is created later.

### 4. Discord future work

`docs/Discord_Improvements.md` is useful historical evidence, but all listed waves are shipped.
Future Discord improvements should be proposed here first and only get a dedicated scoped roadmap if
the work grows large enough to justify it.

---

## Agent rules

When an agent is working on future improvements, roadmap cleanup, or wave planning:

1. Read this file first.
2. Check the scoped support doc only for the area being changed.
3. Update this roadmap whenever a new initiative starts, ships, or is deferred.
4. Prefer updating an existing scoped roadmap over creating a new planning file.
5. If a scoped roadmap becomes fully shipped or purely historical, mark it clearly and keep this
   roadmap as the active entrypoint.
6. If a new roadmap file is unavoidable, add it to the roadmap map above in the same change.

---

## Entry template for new work

Use this shape when adding a new initiative:

```md
### Initiative: <name>

- **Status:** proposed | in progress | deferred | shipped
- **Owner area:** cli | discord | docs | infra | cross-cutting
- **Supporting doc:** `docs/...`
- **Why now:** <one or two lines>
- **Next step:** <concrete next action>
```

---

## Historical references

These remain useful for context, but they are not the active roadmap:

- `docs/archive/IMPLEMENTATION-PLAN.md`
- `docs/Discord_Improvements.md`
- shipped sections inside `docs/UX_IMPROVEMENTS.md`
- shipped sections inside `docs/tech_debt.md`
