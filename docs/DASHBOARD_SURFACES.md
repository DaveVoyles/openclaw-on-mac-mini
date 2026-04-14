# Dashboard Surfaces & Docs Sync

> **Audience:** agents shipping CLI UX waves, especially Waves 21–30.
> **Goal:** keep terminal dashboard surfaces, browser dashboard surfaces, and CLI docs aligned as each wave lands.

## Canonical references

| Surface | Source of truth | Update expectation |
| --- | --- | --- |
| Terminal command inventory | `src/dashboard/helpers.py::_raw_command_groups()` | Regenerate `docs/COMMANDS.md`; do not hand-edit unless generation is retired |
| CLI architecture notes | `docs/CLI_ARCHITECTURE.md` | Update when rendering helpers, guardrails, persistence, or dashboard plumbing changes |
| End-user workflow guidance | `docs/CLI_QUICKSTART.md` | Update when a wave changes what users run, see, or copy/paste |
| UX roadmap / wave history | `docs/UX_IMPROVEMENTS.md` | Update roadmap status, shipped evidence, and future-wave sequencing |
| Dashboard surface inventory | `docs/DASHBOARD_SURFACES.md` | Update for every new or materially changed dashboard/status canvas |

## Surface inventory

### Terminal-first status canvases

| Surface | Entry point | Purpose | Plain/reduced-motion expectation |
| --- | --- | --- | --- |
| Session summary | `/session` | Single-session health, automation state, active context | Must stay readable as compact text without Rich-only cues |
| Session browser | `/sessions`, `openclaw session list`, `openclaw session show` | Browse, inspect, and resume session history | Non-TTY path must still expose core metadata and resume instructions |
| Watch control tower | `/watch status`, `/watch history` | Active phase, retries, checkpoint timeline, intervention clues | Motion and badges need text equivalents |
| Artifact browser | `/outputs`, `/outputs overlay` | Inspect saved outputs and previews | Overlay features must degrade to standard lists |
| Context inspector | `/context`, `/promptdebug` | Explain what the next action will inherit | Must preserve deterministic text for scripted/debug use |
| Event stream | `/events [n]` | Audit trail, timing cues, collaboration notes, retries | Dense badge grammar must still scan in plain text |
| Accessibility dashboard | `/accessibility status`, `/layout` | Show mode, contrast, and density state | Always available without Rich |
| Collaboration handoff | `/collab`, `openclaw session share` | Actor-oriented handoff snapshot | Must remain pasteable plain text |

### Browser/dashboard reference surfaces

| Surface | Likely home | Why it matters |
| --- | --- | --- |
| Terminal Agent Sessions | web dashboard session cards/detail view | Mirrors the most important session/watch metadata outside the REPL |
| Watch Insights | dashboard session detail | Reuses watch checkpoint + retry concepts from CLI `/watch` surfaces |
| Scheduled Tasks / Active Plans / Unified Task Status | dashboard control-plane cards | Must stay terminology-aligned with CLI commands and quickstart guidance |
| Future shared read-only monitoring | future Wave 27+ dashboard work | Should reuse the same labels, status grammar, and fallback terminology defined in CLI docs |

## Required docs/dashboard lane for every future wave

Every Wave 21–30 implementation should include a dedicated docs/dashboard lane with this checklist:

1. **Roadmap sync**
   - update `docs/UX_IMPROVEMENTS.md`
   - keep wave numbering/status truthful
   - capture shipped evidence and any deferred scope explicitly
2. **Architecture sync**
   - update `docs/CLI_ARCHITECTURE.md` for new helpers, guards, persistence, or rendering primitives
   - call out dashboard/data-flow changes when terminal and browser surfaces share state
3. **Quickstart sync**
   - update `docs/CLI_QUICKSTART.md` with the new user-visible commands, examples, and screenshots/snippets if helpful
   - describe how the UX feels in plain language, not just the command name
4. **Dashboard surface inventory sync**
   - update this file with any new surface, renamed surface, or changed fallback behavior
   - note whether the surface is terminal-only, dashboard-only, or shared
5. **Command reference sync**
   - if command names/descriptions changed, regenerate `docs/COMMANDS.md`
   - if there was no command metadata change, explicitly note that `docs/COMMANDS.md` did not require regeneration

## Wave 21–30 planning guardrails

- Treat dashboard work as a first-class lane, not follow-up cleanup.
- Keep terminal-first behavior authoritative; browser/dashboard surfaces should mirror the same status language where possible.
- Every premium surface needs a plain-text equivalent.
- Every motion-heavy interaction needs a reduced-motion path.
- Prefer additive docs updates over duplicating generated command content.
- If a wave introduces a new dashboard canvas, add it here before closing the wave.

## Future-wave update template

Use this mini-template in the docs/dashboard lane output for each wave:

| Item | Questions to answer |
| --- | --- |
| Surface changes | Which terminal or browser dashboard surfaces changed? |
| Shared vocabulary | Did status labels, badges, or phase names change anywhere else? |
| Fallback parity | What is the non-Rich / reduced-motion / non-TTY story? |
| Command reference | Was `docs/COMMANDS.md` regenerated, or intentionally unchanged? |
| Evidence | Which tests, screenshots, or manual checks prove the docs match the shipped behavior? |

## Immediate Waves 21–30 focus

The approved next planning tranche should follow this order:

1. repair and normalize the late-wave roadmap in `docs/UX_IMPROVEMENTS.md`
2. implement Wave 21 and Wave 22 with a dedicated docs/dashboard lane
3. implement Wave 23 with explicit dashboard-elevation ownership

### Waves 27–30 dashboard alignment targets

| Wave | Surface focus | Docs/dashboard expectation |
| --- | --- | --- |
| Wave 27 — Live Dashboard Shares & Operator Visibility | `/session`, `/sessions`, `/watch*`, `/collab`, browser session cards | Define the read-only monitoring snapshot, keep approval/intervention labels aligned, and document that visibility does not imply remote control |
| Wave 28 — Gesture Language & Predictive Affordances | `/watch*`, `/session*`, `/outputs`, `/context`, error/approval flows | Reuse the same next-action labels across terminal and dashboard surfaces, with plain-text examples for hints and recovery menus |
| Wave 29 — Narrative Recaps & Session Storytelling | `openclaw session show/share/export`, `/collab`, browser session detail views | Standardize recap chapter names, actor labels, and next-step wording across terminal, saved artifacts, and dashboard summaries |
| Wave 30 — Premium Motion & Choreography Layer | startup banner, summary dashboards, approval/retry paths, accessibility surfaces | Document which choreography concepts are terminal-specific, which mirror to dashboards as static hierarchy, and how preference controls shape both |

#### Shared checklist for Waves 27–30

1. **Monitoring parity**
   - keep session, watch, approval, and collaboration summaries portable as plain text
   - document whether browser/dashboard mirrors are read-only, interactive, or intentionally deferred
2. **Vocabulary reuse**
   - reuse Wave 22 badge/status grammar and Wave 28 next-action wording rather than inventing dashboard-only terms
   - keep recap chapter titles and operator labels identical across CLI and dashboard surfaces
3. **Fallback behavior**
   - explain the non-TTY, reduced-motion, and plain-mode story for every new surface or hint pattern
   - note where browser surfaces can only mirror hierarchy/statics rather than terminal motion itself
4. **Command/doc sync**
   - update `docs/UX_IMPROVEMENTS.md` with shipped evidence before closing a wave
   - update `docs/CLI_ARCHITECTURE.md` and `docs/CLI_QUICKSTART.md` when implementation begins
   - regenerate `docs/COMMANDS.md` only if command metadata changed

### Wave 22 dashboard alignment targets

When Wave 22 ships, keep these surfaces on the same status grammar:

| Surface group | Alignment requirement |
| --- | --- |
| `/session`, `/sessions` | Reuse the same badges/cells for active, waiting, blocked, complete, and next-step state |
| `/watch status`, `/watch history` | Express phase, retry/backoff, freshness, and intervention need through repeatable progress cells |
| `/events`, `/outputs`, `/context` | Use compact prefixes/cells that stay readable in dense history output and degrade cleanly to plain text |
| `/accessibility status`, `/layout` | Document how badge semantics survive plain mode, reduced motion, and high-contrast rendering |
| Browser/dashboard mirrors | Keep dashboard cards and shared monitoring terminology aligned with the CLI badge grammar |

### Wave 23 dashboard elevation targets

When Wave 23 ships, treat these surfaces as dashboard families instead of
isolated command outputs:

| Surface group | Alignment requirement |
| --- | --- |
| `/session`, `/sessions` | Use the same summary → details → actions hierarchy, with top-line health, freshness, and next-step blocks |
| `/watch status`, `/watch history` | Recast watch views as a control tower: active phase first, intervention cues second, historical checkpoints after that |
| `/outputs`, `/context`, `/events` | Promote recent/high-value items and keep verbose history visually subordinate |
| `/accessibility status`, `/layout` | Explain hierarchy changes in plain text too, not only through panel borders or color accents |
| Browser/dashboard mirrors | Reuse the same section names, card ordering, and summary labels in web/dashboard views |

### Wave 24 preview & focused inspection targets

When Wave 24 ships, preview-capable surfaces should share one inspection model:

| Surface group | Alignment requirement |
| --- | --- |
| `/outputs`, `/outputs overlay` | Preview blocks should show title, freshness, excerpt, and next action before full open/export flows |
| `/sessions`, `openclaw session list --interactive` | Session previews should expose health, latest activity, collaboration hints, and resume/share actions in a consistent order |
| `/watch status`, `/watch history` | Focused inspection windows should show active phase, latest checkpoint, and intervention context without losing chronology |
| `/context`, `/events` | Expanded rows or preview strips must preserve deterministic text fallback and bounded excerpt sizes |
| Browser/dashboard mirrors | Reuse the same preview fields, truncation rules, and labels for side-panel or detail-card inspection views |

### Wave 25 multi-pane preset targets

When Wave 25 ships, layout presets should be documented as first-class dashboard
surfaces:

| Preset / surface group | Alignment requirement |
| --- | --- |
| Focus preset | Pair session summary with the highest-value supporting surface and show clear active-pane affordances |
| Watch-monitor preset | Combine watch status/history with intervention actions and recent outputs using the same badge grammar |
| Collaboration / handoff preset | Pair collaboration snapshot with session health and recent artifacts without duplicating labels |
| `/layout`, `/accessibility`, preset commands | Always expose current preset, width fallback, and how to reset to the default single-pane mode |
| Browser/dashboard mirrors | Keep preset naming and “primary vs supporting pane” vocabulary aligned across CLI and web/dashboard docs |

### Wave 26 mood & celebration targets

When Wave 26 ships, emotional feedback should stay additive and accessible:

| Surface group | Alignment requirement |
| --- | --- |
| `/session`, `/sessions` | Mood or momentum cues appear in summaries only after objective health, blocker, and next-step state |
| `/collab`, `openclaw session share`, exports | Collaboration surfaces use neutral, pasteable language for morale/momentum cues |
| Completion / recap surfaces | Milestone recognition stays brief, skippable, and documented with plain-text equivalents |
| `/watch status`, `/watch history`, `/events` | Recovery or success sentiment can annotate state but must never replace timing/risk details |
| Browser/dashboard mirrors | Dashboard cards reuse the same mood vocabulary and reduced-emotion fallbacks as the CLI |

Until a richer dashboard reference exists, keep `docs/DASHBOARD_SURFACES.md` as the canonical checklist and inventory for these waves.
