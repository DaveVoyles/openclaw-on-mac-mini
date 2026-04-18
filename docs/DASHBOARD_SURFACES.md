# Dashboard Surfaces & Docs Sync

> **Audience:** agents shipping CLI UX waves, especially Waves 21–35.
> **Goal:** keep terminal dashboard surfaces, browser dashboard surfaces, and CLI docs aligned as each wave lands.

## Canonical references

| Surface | Source of truth | Update expectation |
| --- | --- | --- |
| Terminal command inventory | `src/dashboard/helpers.py::_raw_command_groups()` | Regenerate `docs/COMMANDS.md`; do not hand-edit unless generation is retired |
| CLI architecture notes | `docs/CLI_ARCHITECTURE.md` | Update when rendering helpers, guardrails, persistence, or dashboard plumbing changes |
| End-user workflow guidance | `docs/CLI_QUICKSTART.md` | Update when a wave changes what users run, see, or copy/paste |
| UX roadmap / wave history | `docs/UX_IMPROVEMENTS.md` | Update roadmap status, shipped evidence, and future-wave sequencing |
| Dashboard surface inventory | `docs/DASHBOARD_SURFACES.md` | Update for every new or materially changed dashboard/status canvas |

## Interface inventory (all access points)

Added April 2026. Use this as the single source of truth for which interface exists, where it runs, and when to use it.

| Interface | URL / Entry | Port | Best For | Strengths |
| --- | --- | --- | --- | --- |
| 🎮 **Discord bot** | discord.com / `#openclaw` | — | Quick Q&A, group use, push notifications | Shared · Slash commands · Skill routing · Alerts |
| 💬 **Open WebUI** | `chat.davevoyles.synology.me` | 3000 | Long chats, rich formatting, any device | Markdown · Tables · Code blocks · Chat history · Regenerate |
| 💻 **CLI / Terminal** | `/terminal` (browser) or `openclaw` binary | 8765 | Power users, scripting, debugging | Low latency · Scriptable · Pipe-friendly |
| 📨 **Slack bot** | Slack DM or `@openclaw` mention | — | Family file processing, async queries, plain-language mode | Wave 4 commands · Block Kit buttons · File alerts · `/simple` toggle · Windows HTTP upload |
| 📊 **Dashboard v2** | `openclaw-dashboard.davevoyles.synology.me` | 7001 | Stats & monitoring at a glance | Always-on panel · Visual metrics |
| 🛠️ **OpenClaw Dashboard** | `openclaw.davevoyles.synology.me` | 8765 | Ops, status, admin tasks | Live status · Skill list · Route map · Cookie refresh |

### Technical wiring

- **Open WebUI** (`ghcr.io/open-webui/open-webui:main`) → connects to `http://openclaw:8765/v1` (OpenAI-compatible). Auth disabled (`WEBUI_AUTH=False`). Data persisted in Docker named volume `open-webui-data`.
- **Dashboard v2** (`openclaw-dashboard-v2` container) → port 7001. Lightweight stats view.
- **Slack bot** (`src/slack_bot.py`) → Socket Mode. Requires `SLACK_APP_TOKEN` (xapp-) and `SLACK_BOT_TOKEN` (xoxb-) in `.env`. Wave 5 commands (13 total): `/chat`, `/help`, `/simple`, `/files`, `/research`, `/batch`, `/health`, `/digest`, `/template`, `/metrics`, `/clear`, `/brief`, `/mystats`. File uploads via DM Block Kit buttons (Summarize, Proofread, Explain, Chart, Translate, Compare). `/upload` endpoint: `POST http://192.168.1.93:8080/upload` with `X-OpenClaw-Key` header; allowed extensions `.docx .xlsx .pdf .txt .csv`. To update manifest: `make slack-manifest` (copies JSON + opens browser); after saving, update `SLACK_BOT_TOKEN` in `.env` and run `make ship-server`.
- **Traefik routes**: `chat.*` → port 3000, `openclaw-dashboard.*` → port 7001. Dynamic config at `config/traefik/dynamic/mac-mini.yml`.



### Terminal-first status canvases

| Surface | Entry point | Purpose | Plain/reduced-motion expectation |
| --- | --- | --- | --- |
| Shell top bar | `_print_shell_top_bar()` (rendered after each response) | Always-on session · model · autoroute state; keeps operator aware of context without an explicit status command | Degrades to a single plain-text line in non-TTY, narrow, or plain-mode environments; omitted entirely below minimum width |
| Shell bottom bar | `_print_shell_bottom_bar()` (rendered before each prompt) | Always-on mode + hint line before the next user input | Same graceful degradation as top bar; no Rich markup in plain/narrow mode |
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

Every Wave 21–35 implementation should include a dedicated docs/dashboard lane with this checklist:

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

## Wave 21–35 planning guardrails

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
| Wave 29 — Narrative Recaps & Session Storytelling | `openclaw session show/share`, `/collab`, `/session`, `/sessions`, future browser session detail views | Current shipped slice is the plain-text chapter scaffold: reuse ACTORS / RECENT DECISIONS / RECENT NOTES / LATEST HANDOFF / OPERATOR SNAPSHOT / COMMANDS plus the same actor labels and resume/inspect/share wording. The next restrained follow-through may let `/session` and `/sessions` surface momentum/milestone cues secondarily, while bullet/timeline exports, neutral handoff changes, and richer dashboard mirrors remain deferred |
| Wave 30 — Premium Motion & Choreography Layer | startup banner, completion/wait cues, approval emphasis, accessibility surfaces | Current shipped slice is the accessibility-first pacing layer: startup stays static in plain/narrow layouts, reduced-motion waits use heartbeat + completion text, risky approvals reuse the same compact warning voice, and decorative celebration still downgrades to one-line output; broader dashboard-wide reveal choreography remains deferred |

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

- `_print_shell_top_bar()` renders session · model · autoroute state after each response (always-on shell chrome).
- `_print_shell_bottom_bar()` renders mode + hints before each prompt (always-on shell chrome).
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
| `/outputs`, `/outputs overlay` | The shipped preview is a bounded inline excerpt with filename, size, modified time, and an explicit truncation note when the preview is clipped. Follow-up actions still happen as normal commands rather than inside a side panel or approval overlay. |
| `/sessions`, `openclaw session list --interactive` | The searchable picker is live; selecting a row opens the compact Session Dashboard plus the resume command. Share/handoff actions remain separate follow-up commands instead of picker-local buttons, and fuller-screen picker chrome is still deferred. |
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
| Browser/dashboard mirrors | Keep the preset names and “primary vs supporting pane” vocabulary aligned now, and mirror the shipped pane-focus transition wording, even though the actual browser-side split-pane implementation is still future work |

### Wave 26 mood & celebration targets

Wave 26 is currently shipping as a **celebration helper + neutral handoff**
slice. The full mood-model pass is still pending, so use this table as the
truth-source for what is live today:

| Surface group | Alignment requirement |
| --- | --- |
| `/session`, `/sessions` | The next slice may add lightweight momentum/milestone cues, but objective health, blocker, and next-step state still lead summaries |
| `/collab`, `openclaw session share`, exports | The live handoff summary remains neutral and pasteable; morale/momentum wording is still deferred here |
| Completion / recap surfaces | Milestone recognition is currently the short `_celebration_burst()` path used by `/celebrate` and `/rate 5` |
| `/watch status`, `/watch history`, `/events` | No new emotional overlay is shipped here yet; timing/risk details remain authoritative |
| Browser/dashboard mirrors | Future mirrors should reuse the same restrained celebration vocabulary and reduced-motion/plain-text fallback rules |

Until a richer dashboard reference exists, keep `docs/DASHBOARD_SURFACES.md` as the canonical checklist and inventory for these waves.

## Waves 31–35 shared design pattern

All new surfaces in Waves 31–35 should share three decisions:

1. **Split-bar shell chrome** — a standard three-zone layout of top context bar,
   primary output region, and bottom control bar that degrades cleanly to plain
   text
2. **Live phase/step feedback** — explicit messaging during operations instead of
   vague "thinking" language when the system knows the current phase or step
3. **Terminal-first with Rich-default** — assume iTerm2/macOS + Rich as the
   primary experience, with non-Rich, reduced-motion, plain-mode, and non-TTY
   parity built in from the start

See `docs/CLI_ARCHITECTURE.md` for the implementation-facing guidance on shell
chrome, phase/step helpers, and fallback guards.

### Waves 31–35 dashboard alignment targets

The Waves 31–35 tranche extends the dashboard model from monitoring and recap
into suggestions, replay, workflows, quality tracing, and exports.

#### Shared split-bar shell pattern (Wave 31 primary owner)

- **Top bar** — compact session/model/task context and state badges
- **Primary output region** — the main response, code, tables, or narrative block
- **Bottom bar** — current mode plus 1–2 inline hints or next actions

Fallback rules:

- **Rich/iTerm2 default** — color, emoji, dim accents, unicode separators
- **Plain mode** — explicit labels and text separators
- **Reduced motion** — same shell structure, no animated reveal assumptions
- **Non-TTY / narrow width** — collapse to essential status text and omit
  low-priority hints first

| Wave | Surface focus | Docs/dashboard expectation |
| --- | --- | --- |
| Wave 31 — Intelligent Command Suggestions & Inline Assist | response wait-state helpers, top-context shell chrome, post-response suggestion chrome, `/followup`, `/ratehint`, `/pathhints`, `/api/agent/ask/stream` | Current shipped slice is the shell-polish lane: reuse the next-action verb set, document live phase/step/trust cues during waits, note that interactive TTY chat now streams answer chunks through the SSE ask endpoint, keep the top-context/status/bottom-bar shell truthful, and avoid implying a heavier pane compositor or dashboard-side remote control surface |
| Wave 32 — Instant Replay & Session Bookmarks | `/bookmark*`, `/replay --from`, session share/show/export | Current shipped slice is the plain-text bookmark lane: bookmark ids and labels stay visible in session inspection/handoff surfaces and replay can jump directly from a saved marker; timeline/watch markers remain deferred |
| Wave 33 — Command Chaining & Workflow Macros 2.0 | `/workflow*`, `/macro*`, workflow preview/run surfaces | Current shipped slice is the previewable workflow lane: `/workflow` reuses the macro store, preview shows step-by-step dry runs, and workflow execution resolves current-session placeholders before dispatch; export/share embedding remains deferred |
| Wave 34 — AI Quality & Experimentation Loops | response footer, `/trace`, `/quality`, `/experiment*` | Current shipped slice is the traceability lane: `/quality` keeps the histogram but adds the latest route/confidence summary, `/trace` expands the last decision into a human-readable trust snapshot, and experiment controls remain deferred |
| Wave 35 — Long-Form Reporting & Export Suites | `session export`, `/runbook`, `/export-templates`, session share | Current shipped slice is the runbook lane: reuse the persisted session storyline and collaboration snapshot for Markdown handoffs, expose the built-in template gallery in-terminal, and keep richer redaction/custom-template work deferred |
| Wave 36 — Workspace State & IDE-Like Recovery | `/workspace*`, handoff manifests, `session export` | Current shipped slice is the workspace capsule lane: reuse handoff manifests as recovery capsules, expose status/save/list/restore flows in-terminal, restore cwd/files/plan/task/watch state into a fresh session, and surface the same capsule in `session export` |
| Wave 37 — Pattern Library & Workflow Templates | `/pattern*`, `/workflow*`, command history reuse | Current shipped slice is the reusable-pattern lane: save flows from recent commands or existing workflows, browse and preview them with lightweight source metadata, run them through the shared workflow engine, and defer automatic mining/versioned variants |
| Wave 38 — Multi-Actor Planning & Risk-Aware Handoffs | `/collab assign`, `/risk`, `/handoff check`, collaboration snapshot | Current shipped slice is the structured-collaboration lane: record explicit owners, track open risks as local collaboration events, audit readiness before handoff, and surface the same assignments/risk state inside the operator-facing handoff snapshot |
| Wave 39 — Learned Routing & Personalized Quality Loops | `/quality predict`, `/routing*`, ratings + trace history | Current shipped slice is the advisory-learning lane: capture route metadata when ratings are recorded, aggregate local route-quality summaries, expose best-performing lanes in-terminal, and keep actual routing behavior unchanged and transparent |
| Wave 40 — Long-Running Automation Dashboard & Operator Intelligence | `/dashboard automation`, `/alerts*`, `/fleet*`, watch + session state | Current shipped slice is the operator-control lane: compute cross-session automation alerts from existing watch/session state, surface a compact automation dashboard in-terminal, let operators acknowledge known alerts, and reuse the same summary for fleet-style health views |
| Wave 41 — Incident Log & Operator Resolution | `/incident*`, `/collab status`, `/handoff check`, `/dashboard automation` | Current shipped slice is the incident-log lane: record unresolved operator issues as local collaboration events, resolve them with a lightweight terminal flow, keep the same incident state visible in handoff snapshots and readiness checks, and surface open-incident counts in the automation dashboard |
| Wave 42 — Source Rendering Reliability | `print_response()` render flow, sources extraction, ANSI sources panel | Current shipped slice is the render-reliability lane: source sections never render twice in the body, loose `Sources:` blocks still extract into the panel, ANSI escape codes are stripped from source labels, and the plain ANSI sources box tracks live terminal width |
| Wave 43 — Context & Token Intelligence | `/tokeninfo`, `/session`, response footer, startup tips | Current shipped slice is the context-transparency lane: `/tokeninfo` estimates session token usage with a progress bar, response completion surfaces token count more prominently, `/session` includes session age, and the startup tips pool points users toward the token-awareness workflow |
| Wave 44 — Startup & First-Run Polish | startup banner, session milestones, tips pool, `--no-banner` | Current shipped slice is the startup-polish lane: the banner greeting adapts to time of day, session milestones celebrate repeated use, the refreshed tips pool points at recent commands, and `--no-banner` keeps scripted runs deterministic |
| Wave 45 — Context Pressure Guardrails | `/tokeninfo`, `/context`, `/session`, `/watch status` | Current shipped slice is the context-pressure lane: `/tokeninfo` now breaks estimated usage down by actor, highlights the dominant contributor, and escalates recovery hints from light refresh guidance to bookmark-before-clear warnings as the shared 128k estimate fills; adjacent operator surfaces now echo lighter next-send or next-retry pressure cues plus recovery links back to `/tokeninfo`, `/bookmark`, or `/promptdebug` |

Deferred interaction-affordance note:

- the browser/dashboard lane should not imply a heavier pane compositor or a
  remote-control dashboard shell beyond the shipped terminal-first top-context /
  status / bottom-bar pattern
- current truthful mirrors are still the terminal-first top-context/footer/status cues,
  pre-approval `/edit` diff preview + review/trust feedback, `/tokeninfo`-based context
  guidance, and the shared SSE ask plumbing
- the browser/dashboard lane should mirror the shipped terminal-first
  interaction work truthfully: compact approval review overlay, richer TTY
  pickers with inline previews, and explicit pane-focus transition wording
- true browser-side pane compositors, remote-control shells, or heavier
  full-screen picker environments should stay documented as deferred until a
  later wave actually ships them

---

## Badge & Status Grammar

> **Source of truth:** `src/openclaw_cli_session_display.py` — `_status_family()`, `_status_emoji()`, `_status_cell()`, `_progress_cell()`.

All terminal surfaces that show per-item status (session lists, watch control tower, event stream, context inspector) use a shared badge grammar. The grammar is defined once and reused everywhere to keep terminology consistent between the CLI and any future dashboard mirrors.

### Status families and emoji

| Status family | Trigger words | Emoji (full mode) | Plain-text label |
| --- | --- | --- | --- |
| `complete` | ok, healthy, done, completed, success, succeeded, complete | 🟢 | `COMPLETE` |
| `active` | active, running, in_progress, working, processing, streaming | 🔵 | `ACTIVE` |
| `waiting` | pending, queued, waiting, scheduled | ⏳ | `WAITING` |
| `idle` | idle | ⚪ | `IDLE` |
| `retry` | retry, retrying, backoff, recovering | 🔄 | `RETRY` |
| `warn` | warn, warning, degraded, attention | 🟡 | `WARN` |
| `error` | error, failed, failure, unhealthy | 🔴 | `ERROR` |
| `blocked` | blocked, stuck, needs_input | ⛔ | `BLOCKED` |
| `paused` | paused, stopped, cancelled, canceled | ⏸ | `PAUSED` |
| `info` | info, note, fresh, new | ℹ️ | `INFO` |
| `stale` | stale, old, expired | 🕰️ | `STALE` |
| `unknown` | (anything else) | ● | `STATUS` |

### Progress-cell shape conventions

`_progress_cell(label, value, *, status)` renders a compact label+value pair with an optional status badge prefix:

```
🟢 COMPLETE · phase: 3/3
🔄 RETRY · attempt: 2
🔴 ERROR · last err: connection refused
```

In dense list views (e.g. `/sessions`, `/watch history`) the cell fits on a single terminal line. The badge always leads — plain text label comes first, detail after ` · `.

The spinner used during long-running calls (`_with_spinner()`) uses braille-frame animation (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) while the background task runs, then replaces itself with the completion cue.

### Accessibility fallback (plain / non-TTY mode)

When `_a11y_plain_mode()` is `True` or `sys.stdout.isatty()` is `False`, every badge-bearing surface falls back to plain text:

- Emoji are suppressed; the ASCII fallback from `_e(emoji, fallback)` is used instead (e.g. `[ok]`, `[err]`, `[wait]`).
- `_status_cell()` returns the plain-text label only (e.g. `COMPLETE`, `ERROR`).
- Shell chrome bars (`_print_shell_top_bar`, `_print_shell_bottom_bar`) emit a simple `--- key: value | … ---` line in TTY plain-mode, and are silently suppressed in non-TTY environments below minimum width.
- Rich styles (color, bold) are not applied; output is safe for screen readers and log capture.
