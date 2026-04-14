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
| Future shared read-only monitoring | future Wave 27+ dashboard work | Current Wave 27 slice is docs/vocabulary only: reuse CLI labels and fallback terminology, but do not imply remote control |

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

The current docs/dashboard tranche should keep this order:

1. keep the late-wave roadmap truthful in `docs/UX_IMPROVEMENTS.md`
2. align the Wave 27 operator-visibility slice across architecture, quickstart, and tests
3. preserve shared terminology for later browser/dashboard mirrors without implying remote control

### Waves 27–30 dashboard alignment targets

| Wave | Surface focus | Docs/dashboard expectation |
| --- | --- | --- |
| Wave 27 — Live Dashboard Shares & Operator Visibility | `/session`, `/sessions`, `/watch*`, `/collab`, browser session cards | Current shipped slice is the terminal/read-only snapshot: keep intervention, resume, and handoff labels aligned, and document that visibility does not imply remote control |
| Wave 28 — Gesture Language & Predictive Affordances | `/watch*`, `/session*`, `/outputs`, `/context`, error/approval flows | Current shipped slice is the lightweight hint layer: reuse `/watch history`, `/watch intervene`, `/watch retry-limit`, `/session`, `/files`, `/plan`, `/retry`, and `/reset` labels consistently, with plain-text examples for hints and recovery menus |
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

The current Wave 22 slice is still incremental, so treat this section as both a
checklist and a truth-source for what is already aligned today:

- `_status_emoji()` owns the canonical status-family mapping.
- `_session_badges()` is the live compact-cell baseline for dense session lists.
- `summarize_session()`, `_print_watch_status()`, and `/accessibility status`
  provide the current fallback wording that later surfaces should reuse.
- `docs/COMMANDS.md` remains intentionally unchanged until command metadata, not
  just surrounding docs/tests, actually changes.

When Wave 22 ships, keep these surfaces on the same status grammar:

| Surface group | Alignment requirement |
| --- | --- |
| `/session`, `/sessions` | Reuse the same badges/cells for active, waiting, blocked, complete, and next-step state |
| `/watch status`, `/watch history` | Express phase, retry/backoff, freshness, and intervention need through repeatable progress cells |
| `/events`, `/outputs`, `/context` | Use compact prefixes/cells that stay readable in dense history output and degrade cleanly to plain text |
| `/accessibility status`, `/layout` | Document how badge semantics survive plain mode, reduced motion, and high-contrast rendering |
| Browser/dashboard mirrors | Keep dashboard cards and shared monitoring terminology aligned with the CLI badge grammar |

### Wave 23 dashboard elevation targets

The currently shipped Wave 23 slice is partial. Treat these surfaces as the
first dashboard-family pass, not the completed end state:

| Surface group | Alignment requirement |
| --- | --- |
| `/session`, `/sessions` | Top-line status, freshness, counts, and compact badges should land before deeper detail; explicit action regions are still follow-up work |
| `/watch status`, `/watch history` | Current control-tower slice leads with status/phase/retry signals and keeps chronology below that |
| `/outputs`, `/context`, `/events` | Promote recent/high-value items and keep verbose history visually subordinate |
| `/accessibility status`, `/layout` | Explain hierarchy changes in plain text too, not only through panel borders or color accents |
| Browser/dashboard mirrors | Reuse the same section names, card ordering, and summary labels in web/dashboard views |

Wave 23 docs/tests should therefore describe the **shipped hierarchy slice**
truthfully:

- session summaries and inspection surfaces now lead with status-family cells,
  counts, and watch context
- watch status/history views lead with control-state signals before raw history
- plain-text ordering is part of the feature, not a fallback afterthought

### Wave 24 preview & focused inspection targets

The current Wave 24 slice is incremental. Treat these surfaces as the shipped
preview/focused-inspection baseline today:

| Surface group | Alignment requirement |
| --- | --- |
| `/outputs`, `/outputs overlay` | The shipped preview is a bounded inline excerpt with filename, size, modified time, and an explicit truncation note when the preview is clipped. Follow-up actions still happen as normal commands rather than inside a side panel. |
| `/sessions`, `openclaw session list --interactive` | The searchable picker is live; selecting a row opens the compact Session Dashboard plus the resume command. Share/handoff actions remain separate follow-up commands instead of picker-local buttons. |
| `/watch status`, `/watch history` | These are the current focused inspection windows: status leads with mode/status/polls/phase, while history keeps recent progress, retries, and operator notes grouped above raw chronology. |
| `/context`, `/events`, `openclaw session show` | Focused inspection is currently delivered through bounded grounding previews in `/context` and through the richer `session show` inspection view; dedicated preview strips for `/events` are still deferred. |
| Browser/dashboard mirrors | Keep future mirrors aligned to the current CLI field order and truncation rules, but treat browser-side preview panes as future work until a shared implementation exists. |

### Wave 25 multi-pane preset targets

Wave 25 is currently shipping as a **preset contract + fallback reporting**
slice rather than a full split-pane renderer. Treat the table below as the
truth-source for what is live today:

| Preset / surface group | Alignment requirement |
| --- | --- |
| Focus preset | Persisted through `/layout focus`; today it documents `/session` as the primary pane and `/context` as the supporting pane |
| Watch-monitor preset | Persisted through `/layout watch-monitor`; today it documents `/watch status` as primary with `/watch history + /outputs` as the supporting lane |
| Collaboration / handoff preset | Persisted through `/layout handoff`; today it documents `/collab` as primary with session summary + recent outputs as supporting context |
| `/layout`, `/accessibility`, preset commands | `/layout` reports the current preset plus `multi-pane`/`stacked`/`single-pane` fallback, `/layout reset` returns to default mode, and `/accessibility status` mirrors the same fallback state |
| Browser/dashboard mirrors | Keep the preset names and “primary vs supporting pane” vocabulary aligned now, even though the actual browser-side split-pane implementation is still future work |

### Wave 26 mood & celebration targets

Wave 26 is currently shipping as a **celebration helper + neutral handoff**
slice. The full mood-model pass is still pending, so use this table as the
truth-source for what is live today:

| Surface group | Alignment requirement |
| --- | --- |
| `/session`, `/sessions` | No dedicated mood row is shipped yet; objective health, blocker, and next-step state still lead summaries |
| `/collab`, `openclaw session share`, exports | The live handoff summary remains neutral and pasteable; morale/momentum wording is deferred |
| Completion / recap surfaces | Milestone recognition is currently the short `_celebration_burst()` path used by `/celebrate` and `/rate 5` |
| `/watch status`, `/watch history`, `/events` | No new emotional overlay is shipped here yet; timing/risk details remain authoritative |
| Browser/dashboard mirrors | Future mirrors should reuse the same restrained celebration vocabulary and reduced-motion/plain-text fallback rules |

Until a richer dashboard reference exists, keep `docs/DASHBOARD_SURFACES.md` as the canonical checklist and inventory for these waves.
