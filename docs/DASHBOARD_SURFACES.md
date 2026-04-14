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

### Wave 22 dashboard alignment targets

When Wave 22 ships, keep these surfaces on the same status grammar:

| Surface group | Alignment requirement |
| --- | --- |
| `/session`, `/sessions` | Reuse the same badges/cells for active, waiting, blocked, complete, and next-step state |
| `/watch status`, `/watch history` | Express phase, retry/backoff, freshness, and intervention need through repeatable progress cells |
| `/events`, `/outputs`, `/context` | Use compact prefixes/cells that stay readable in dense history output and degrade cleanly to plain text |
| `/accessibility status`, `/layout` | Document how badge semantics survive plain mode, reduced motion, and high-contrast rendering |
| Browser/dashboard mirrors | Keep dashboard cards and shared monitoring terminology aligned with the CLI badge grammar |

Until a richer dashboard reference exists, keep `docs/DASHBOARD_SURFACES.md` as the canonical checklist and inventory for these waves.
