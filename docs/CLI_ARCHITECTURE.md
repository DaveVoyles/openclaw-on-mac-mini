# OpenClaw CLI Architecture
<!-- Updated: 2026-04-18 -->


Reference for agents building on or modifying the CLI. Covers the UX system,
update mechanism, standalone install, and key code locations.

## Source files

| File | Role |
| --- | --- |
| `src/openclaw_cli.py` | Primary CLI (~4,654 lines). Main REPL loop, `run_chat`, `main`; all `_cmd_*` handlers now live in extracted modules (see below) |
| `src/openclaw_cli_types.py` | Shared type definitions — `ChatCommandContext`, `SlashCommand`, `ChatCommandRegistry`, `AskResponse`. Leaf module; zero internal dependencies. (TD-28) |
| `src/openclaw_cli_cmd_settings.py` | 12 settings/appearance handlers: `/theme`, `/overlay`, `/colorscheme`, `/emojiheaders`, `/emoji`, `/layout`, `/links`, `/pasteguard`, `/accessibility`, `/keybind` and related. (TD-29) |
| `src/openclaw_cli_cmd_session.py` | 10 session lifecycle handlers: `/session`, `/events`, `/sessions`, `/export`, `/tag`, `/bookmark`, `/bookmarks`, `/resume`, `/replay`, `/handoff`. (TD-30) |
| `src/openclaw_cli_cmd_workflow.py` | 12 workflow/automation handlers: `/watch`, `/plan`, `/task`, `/risk`, `/incident`, `/workspace`, `/macro`, `/macrostatus`, `/workflow` and related. (TD-31) |
| `src/openclaw_cli_cmd_content.py` | 10 content/analytics handlers extracted from the primary CLI module. (TD-32) |
| `src/openclaw_cli_cmd_core.py` | 24 system/file/exec handlers: `/help`, `/clear`, `/context`, `/cwd`, `/files`, `/routing`, `/why`, `/trace`, `/autoroute`, `/snapshot`, `/rollback`, `/analyze`, `/research`, `/write`, `/exec`, `/edit`, `/update`, `/version`, `/tokeninfo`, `/draft`, `/template`, `/inject`, `/exporttemplates`, `/runbook`. (TD-33) |
| `src/openclaw_cli_cmd_misc.py` | UX/history/analytics handlers: `/recall`, `/histsearch`, `/celebrate`, `/rate`, `/streak`, `/heatmap`, `/followup`, `/shortcuts`, `/top`, `/freq`, `/tip`, `/keys`, `/bindlist`, `/diff`, `/changes`. (TD-33) |
| `src/openclaw_cli_cmd_system.py` | System/prompt handlers: `/system`, `/promptdebug`, `/autobold`, `/jsonformat`, `/separator`, `/palette`, `/prompt`, `/alias`, `/pathhints`, `/ratehint`, `/benchmark`. (TD-33) |
| `src/openclaw_cli_cli_parser.py` | Extracted `build_parser()` — pure argparse module, no side effects. (TD-34) |
| `src/openclaw_cli_help.py` | Extracted `print_chat_help()` — generates help table from command registry. (TD-34) |
| `src/openclaw_cli_actions.py` | Approval prompts (`request_cli_approval`) with colored risk levels plus review/trust/recovery cues; `_print_approval_recap()` recap display; `_print_usage()` consistent usage-error helper. (TD-28, W24) |
| `src/openclaw_cli_sessions.py` | Session persistence (load/save conversation history, watch state) |
| `src/openclaw_cli_ui_utils.py` | UI utility functions: spinner, banner, status bar, shell chrome bars. Contains `_print_shell_top_bar()` (session · model · autoroute, shown after each response) and `_print_shell_bottom_bar()` (mode + hints, shown before each prompt). Both bars degrade gracefully in plain/non-TTY/narrow modes. (W22) |
| `src/llm/context_limits.py` | `MODEL_CONTEXT_WINDOWS` dict (13 models) and `get_model_context_window()`. Powers `/tokeninfo` per-model context limit display and 80/90/95% overflow warnings. (W21) |
| `src/subprocess_utils.py` | Shell execution helpers used by `/exec` |
| `src/discord_web.py` | aiohttp server — health, dashboard, `/cli-update/*` endpoints |
| `scripts/install_openclaw_cli_remote.sh` | Push CLI files to a remote Mac via SSH+SCP |
| `scripts/uninstall_openclaw_cli_remote.sh` | Remove standalone install from a remote Mac via SSH |

---

## Model Selection

Key files: `src/model_routing_policy.py`, `src/model_router.py`, `src/llm/providers.py`.

### Routing profiles

The system-wide routing profile is set via the `ROUTING_PROFILE` env var (default: `copilot-first`).
Available values: `copilot-first | balanced | gemini-first | cost-saver`.

### Non-tool queries (copilot-first, Copilot proxy available)

| Condition | Model | Provider |
| --- | --- | --- |
| Short query (≤ 25 words) | `gpt-4o-mini` | Copilot proxy (mini fast-path) |
| Code query | `claude-sonnet-4.5` | Copilot proxy |
| Reasoning / math query (W29) | `o1-mini` | Copilot proxy |
| All other queries | `gpt-4o` | Copilot proxy |

### Tool-requiring queries (home automation, Docker, search, Sonarr, etc.)

| `COPILOT_TOOLS_ENABLED` | Model | Provider |
| --- | --- | --- |
| `true` (default) | `gpt-4o` | GitHub Models API — enterprise function calling |
| `false` (fallback) | Gemini | Direct — reliable native function calling |

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `ROUTING_PROFILE` | `copilot-first` | System-wide routing profile |
| `COPILOT_PROXY_URL` | `http://host.docker.internal:9191/v1` | Copilot proxy endpoint (optional; GitHub Models is the default path) |
| `OPENAI_MODEL` | `gpt-4o` | Default Copilot non-code model |
| `ANTHROPIC_MODEL` | `claude-sonnet-4.5` | Default Copilot code model |
| `COPILOT_REASONING_MODEL` | `o1-mini` | Model for reasoning/math queries (W29) |
| `COPILOT_TOOLS_ENABLED` | `true` | Route tool calls to GPT-4o via GitHub Models API (false = Gemini fallback) |
| `OPENAI_MINI_MODEL` | `gpt-4o-mini` | Fast-path model for short queries |

---

## UX patterns

### Rich / ANSI guard

Every formatted output block is wrapped with:

```python
if _RICH_AVAILABLE and _IS_TTY:
    # rich panels, tables, colored Text
else:
    # plain ANSI fallback (printable on any terminal)
```

`_RICH_AVAILABLE` is set at module load via `try: from rich import ...`.
`_IS_TTY = sys.stdout.isatty()`.

**Never** print rich markup unconditionally — the test suite captures stdout
and will see raw markup tags.

### Accessibility + adaptive layout

Wave 15 currently exposes three persisted accessibility preferences in
`_PREFS`:

| Preference | Key | Current behavior |
| --- | --- | --- |
| Reduced motion | `reduced_motion` | `_with_spinner()` skips animation, prints a static working line, and emits periodic text heartbeats for slower calls |
| Plain mode | `plain_mode` | Forces plain/non-Rich response rendering, simplifies the REPL prompt + startup banner, and maps to layout `plain` |
| High contrast | `high_contrast` | Switches to the high-contrast palette for separators, borders, and selected Rich/ANSI accents |

The interactive REPL exposes these through `/accessibility`:

```text
/accessibility status
/accessibility reduced-motion on|off
/accessibility plain on|off
/accessibility high-contrast on|off
/accessibility reset
```

Adaptive layout is split between two surfaces:

- `/layout compact|normal|verbose|plain` controls chrome density
- width-aware rendering helpers such as `_render_table_ansi()` and
  `/accessibility status` use terminal width at render time

Wave 16 layers feedback density on top of that baseline:

- `_with_spinner()` now ends with an explicit completion cue, and reduced-motion
  mode emits periodic plain-text heartbeats instead of going silent.
- `/clear`, `/layout`, and `/accessibility` use the shared compact feedback line
  helper for predictable confirmations.
- High/critical `/exec` and `/edit` actions print an extra warning + recovery
  hint before the existing approval prompt.
- The approval UX is still terminal-first: `request_cli_approval()` keeps the
  text review loop and cue block, and high-risk approvals can now open a compact
  review overlay without changing the non-TTY fallback contract.

### Theme engine + personalization

Wave 17 extends the existing theme layer without changing the JSON/non-TTY
surfaces:

| Preference | Key | Current behavior |
| --- | --- | --- |
| Theme | `theme` | Normalized through `_normalize_theme_name()` so invalid values safely fall back to `default` |
| Emoji pack | `emoji_pack` | Supports `classic`, `minimal`, and `ascii`; legacy `emoji` bool is still honored/migrated |
| Layout | `layout` | Clamped by `_normalize_personalization_prefs()` to `compact`, `normal`, `verbose`, or `plain` |

Command handlers:

- `/theme [name|list|preview|next|prev|reset]`
- `/emoji [on|off|status|pack <name>|preview]`

Implementation notes:

- `_status_emoji()` now routes through `_e()` so ASCII/minimal emoji packs affect
  status badges too, not just ad-hoc icon calls.
- `_load_prefs()` / `_save_prefs()` call `_normalize_personalization_prefs()` to
  keep stored customization values safe and additive.

### Interactive overlays

Wave 19 adds opt-in interactive list pickers without changing default scripted
behavior:

| Surface | Trigger | Current behavior |
| --- | --- | --- |
| REPL outputs picker | `/outputs overlay` or `/overlay on` + `/outputs` | Searchable output list; selecting an item prints the normal saved-output preview |
| REPL sessions picker | `/sessions overlay` or `/overlay on` + `/sessions` | Searchable recent-session list; selecting an item prints session summary + resume command |
| One-shot session picker | `openclaw session list --interactive` | Same searchable session picker outside the REPL |

Implementation notes:

- The persisted preference key is `_PREFS["interactive_overlays"]`.
- `_overlay_available()` guards overlay prompts behind TTY checks so non-TTY and
  scripted usage falls back to ordinary list output.
- `_run_interactive_overlay()` now has two safe modes: a richer TTY path with
  raw-key `↑/↓` movement, live filtering, and an inline preview block, plus the
  original plain `input()` path for plain-mode, scripted, and other fallback
  environments.
- That lightweight contract is deliberate: the richer TTY picker now supports
  arrow-key movement, live filtering, and inline previews without committing to
  a heavier curses/Textual full-screen shell, and the layout system still stops
  short of a true pane compositor even though it now reports explicit
  `/layout focus ...` transitions.

### REPL input stack

The interactive prompt path should stay explicitly tiered rather than assuming a
single backend:

- **Optional `prompt_toolkit` layer** — the deferred shell-input follow-up may
  use `prompt_toolkit` for richer interactive-TTY line editing, completion, and
  multiline compose ergonomics when the dependency is installed and the session
  is actually interactive.
- **`readline` baseline** — the shipped Wave 4 contract still relies on
  `readline` where available for slash-command completion, persisted history,
  reverse search, and the binding vocabulary surfaced by `/keys` and
  `/bindlist`.
- **Plain-input fallback** — startup and input must still succeed without
  optional dependencies, and plain-mode, non-TTY, or scripted environments
  should be able to fall back to the simpler `input()` path.

Important guardrails:

- Treat `prompt_toolkit` as a prompt-entry enhancement, not a commitment to a
  full-screen TUI shell.
- Keep the slash-command vocabulary and `/draft multiline` surface stable across
  all three layers.
- Stateful shell completion and broader hint derivation remain future work even
  if the richer prompt backend lands.

### Collaboration handoff UX

Wave 20 adds collaboration-oriented affordances on top of the existing local
session and handoff files:

- `/collab`, `/collab share`, and `openclaw session share <session-id>` render
  a pasteable handoff summary from persisted session metadata, outputs, events,
  and latest handoff data.
- `/collab note [@actor] TEXT` and `/collab decision [@actor] [#tag] TEXT`
  append additive `collab` events to the session log; decision tags are also
  mirrored into session tags as `collab:<tag>`.
- `export_session()` and `create_handoff()` now include a structured
  `collaboration` snapshot so downstream tooling can reuse the same actor,
  decision, and share-command metadata.

### Performance visibility

Wave 18B adds timing-oriented visibility without changing JSON/non-TTY
compatibility requirements:

- `/session` now folds watch timing hints into the automation row when persisted
  watch state exists (active phase, last completed run duration, accumulated
  retry backoff).
- `/watch status` surfaces the active phase age, last checkpoint duration, and
  total retry backoff time using backward-compatible derivation from existing
  watch-state timestamps.
- `/watch history` annotates recent retries with their backoff delays.
- `/events` appends compact timing cues when event metadata includes
  `elapsed_seconds`, `approval_seconds`, or `retry_delay_seconds`.
- `/exec` and `/edit` now emit explicit `approval` decision events and include
  both approval wait time and execution/write time in their result metadata.

### Wave 22 status grammar baseline

Wave 22 is the current in-flight status-language slice. The implementation in
`src/openclaw_cli.py` is still incremental, but these shared primitives are the
current baseline that docs and tests should align around:

- `_status_emoji()` is the canonical status-family mapper for healthy/active,
  running, warning, failure, paused, and queued state labels, with emoji-pack
  fallbacks preserved through `_e()`.
- `_session_badges()` is the first compact badge row for dense list views; today
  it emits activity (`●`/`○`), freshness (`stale`), artifact presence
  (`outputs`), and tag cells.
- `summarize_session()` and `_print_watch_status()` already provide the timing
  portion of the lattice through consistent phase / last-run / backoff wording.
- `/accessibility status` remains the canonical plain/high-contrast/reduced-
  motion explanation surface, so any new badge grammar must stay readable there
  without relying on Rich-only color.

This means Wave 22 docs should describe the **shared vocabulary** honestly even
when individual surfaces are still adopting it incrementally.

### Dashboard/docs maintenance

When a wave changes any dashboard or status surface, update the docs as a set:

- `docs/DASHBOARD_SURFACES.md` — canonical inventory + per-wave checklist for
  Waves 21–35
- `docs/CLI_ARCHITECTURE.md` — rendering helpers, guardrails, persistence, and
  shared dashboard plumbing
- `docs/CLI_QUICKSTART.md` — user-facing command/examples for the changed
  surface
- `docs/UX_IMPROVEMENTS.md` — roadmap status, shipped evidence, and deferred
  scope

`docs/COMMANDS.md` is generated from
`src/dashboard/helpers.py::_raw_command_groups()`. Regenerate it when command
metadata changes; avoid hand-editing the file unless that generation flow is
retired.

### Wave 23 hierarchy slice (current truth)

Wave 23 is underway, but the shipped slice is narrower than the full roadmap
entry:

- `summarize_session()` now front-loads status/count progress cells before plan,
  task, file, and watch-detail lines.
- `inspect_session()` uses the same status/progress-cell grammar in its session,
  checkpoint, recent-progress, and recent-event sections so the first screenful
  reads like a lightweight dashboard in plain text too.
- `_print_watch_status()` and `_print_watch_history()` now use status-family
  cells (`ACTIVE`, `RETRY`, `INFO`, etc.) to elevate phase/backoff/intervention
  context above verbose chronology.

Treat this as a **composition slice**: the status lattice from Wave 22 is now
being used to shape hierarchy, but the broader “summary → details → actions”
dashboard family work is not fully closed yet.

### Wave 24 preview & focused inspection slice (current truth)

Wave 24 has started shipping as a narrow inspection layer on top of the Wave 19
overlay model and the Wave 23 hierarchy work:

- `/outputs 1`, `/outputs <filename>`, and `/outputs overlay` all resolve through
  `load_saved_output_preview(...)`, so the current preview contract is
  **filename + size + modified time + bounded excerpt + truncation notice**.
- `/sessions overlay` and `openclaw session list --interactive` still use the
  lightweight `_run_interactive_overlay()` picker, but the selected item now
  lands in `_print_session_summary()` plus a resume footer instead of forcing a
  separate manual browse step.
- `_print_watch_status()` and `_print_watch_history()` are the current
  “focused inspection” windows for automation state: phase, retry budget,
  checkpoint/retry history, and operator notes are surfaced before verbose raw
  details.
- `inspect_session()` is the richer non-overlay inspection path for this slice:
  it front-loads status cells, then groups watch state, checkpoints, recent
  progress, recent events, outputs, and collaboration in one deterministic view.

Deferred Wave 24 scope remains explicitly deferred: there is not yet a shared
preview-block helper across every surface, the session picker does not expose
inline share controls, and `/events` still relies on the existing dense list
instead of a dedicated preview strip.

### Wave 25 layout preset contract (current truth)

Wave 25 has started shipping as a **layout-preset state layer**, not yet as a
terminal TUI shell:

- `_layout_preset_name()`, `_layout_preset_config()`, and
  `_layout_preset_fallback()` define the current contract for persisted preset
  names, their documented primary/supporting surface pairings, and the
  downgrade path (`multi-pane`, `stacked`, or `single-pane`).
- `_normalize_personalization_prefs()` now treats `layout_preset` like other
  safe personalization state: aliases normalize to `focus`, `watch-monitor`, or
  `handoff`, and unknown values are discarded.
- `_cmd_layout()` is the live user-facing control plane for this slice: it sets
  presets, reports the current pairing/fallback, and resets back to the default
  single-pane mode without requiring a separate command family.
- `/accessibility status` mirrors the current preset + fallback state so the
  width/accessibility downgrade story stays visible in deterministic plain text.

The actual pane compositor remains deferred, but the current pane shells now
surface explicit `/layout focus …` transition cues so active-pane switching is
visible even in stacked and single-pane fallbacks.

### Wave 26 celebration slice (current truth)

Wave 26 is currently a **small emotional-feedback slice** centered on the shared
celebration helper, not a full session-mood layer:

- `_celebration_burst(message)` is the single runtime primitive. It shows a
  3-frame animated confetti burst only for interactive TTY sessions.
- The same helper downgrades to a plain one-line `🎉 {message}` confirmation when
  reduced motion or plain mode is enabled, which keeps the feature readable in
  accessibility-first output.
- `_cmd_celebrate()` is the explicit manual entry point for the feature.
- `_cmd_rate()` reuses `_celebration_burst("5-star rating — thanks! 🎉")` only
  for 5-star ratings, so celebratory feedback stays brief and tied to milestone
  success.
- Collaboration handoff rendering (`_build_session_share_text()`) remains
  deliberately neutral and pasteable; no mood metadata is injected into the
  exported/session-share contract yet.

### Wave 27 operator-visibility slice (current truth)

Wave 27 is currently a **read-only monitoring/documentation slice**, not a
hosted dashboard runtime:

- `_build_session_share_text()` is the canonical operator-facing snapshot
  serializer today. It emits title/cwd/plan/task context, recent actors,
  decisions, notes, latest handoff, recent outputs, and the exact
  resume/inspect/share commands from local session data.
- `_session_preview_lines()`, `summarize_session()`, and `inspect_session()`
  already reuse the same watch/collaboration vocabulary for terminal-first
  previews, focused session inspection, and plain-text handoff review.
- `_watch_focus_lines()`, `_print_watch_status()`, and `_print_watch_history()`
  are the current operator-visibility surfaces for checkpoint freshness, retry
  pressure, and operator-note breadcrumbs.
- Approval visibility in this slice is still additive and local-first: existing
  approval events/timing remain visible through session/watch/event surfaces,
  but Wave 27 does **not** introduce any remote mutation path or shared control
  plane.

### Wave 28 predictive-affordance slice (current truth)

Wave 28 is currently a **deterministic hint layer**, not a centralized
next-action engine:

- `_print_dashboard_surface(...)` remains the shared rendering primitive, and the
  current predictive slice uses each surface's `action_lines` to advertise the
  next safest follow-up without changing the underlying raw command model.
- `_print_watch_status()` is the clearest live example: it always offers
  `/watch history` and `/watch intervene <msg>`, then switches the lead affordance
  between `/watch retry-limit N` and `/session` based on completion state.
- `_cmd_context()` uses the same action-line contract to steer users toward
  `/files`, `/plan`, `/task`, or `/session` depending on how much grounding is
  already attached to the active session.
- `_print_path_hints()` is intentionally narrower than the dashboard affordances:
  it only fires for interactive TTY output, only when mentioned files exist
  locally, and only suggests `/view` or `/edit`.
- `_print_risky_action_warning(...)` and the REPL's `OpenClawCliError` handler are
  the current recovery-first surfaces. They keep the guidance plain-text and
  deterministic (`Recovery: ...`, `/retry`, `/reset`) instead of opening
  interactive menus.

State-aware shell completion and a shared hint derivation helper remain future
Wave 28 follow-up work.

### Wave 29 storytelling slice (current truth)

Wave 29 is currently a **deterministic narrative scaffold**, and the next
follow-through slice should stay narrower than a generated prose/timeline recap
engine:

- `_build_session_share_text()` is the canonical plain-text storyteller today.
  It reuses persisted collaboration and watch metadata to emit stable recap
  chapters: `ACTORS`, `RECENT DECISIONS`, `RECENT NOTES`,
  `LATEST HANDOFF`, `OPERATOR SNAPSHOT`, `RECENT OUTPUTS`, and `COMMANDS`.
- `inspect_session()` reuses the same facts for the inspection path: session
  mood/milestone state, collaboration actors/decisions, outputs, and the resume
  command are all visible without scanning the raw event log first.
- `_session_preview_lines()` is the compact version of the same story contract:
  latest activity, watch focus, latest output, actor names, top decision, and a
  lightweight momentum/milestone cue.
- The next truthful extension point is still narrow: `/session` and `/sessions`
  may surface those momentum cues as secondary context, but status/count/watch
  signals remain the primary scan path.
- The current “next step” model is command-based rather than prose-based:
  resume / inspect / share are the shipped deterministic endings for these
  recap surfaces, and `_build_session_share_text()` should stay neutral/pasteable
  until a broader recap/export contract actually lands.

Bullet/timeline recap transforms, recap-specific export payloads, and richer
browser/dashboard storytelling remain future Wave 29 follow-up work.

### Wave 30 choreography slice (current truth)

Wave 30 is currently an **accessibility-first pacing layer**, not a new global
animation engine:

- `_print_feedback(...)` is the shared emphasis primitive for compact success,
  warning, and liveness cues. It keeps the same message shape across normal,
  high-contrast, and plain output.
- `_with_spinner(...)` is the main waiting-state choreography helper today. Rich
  TTY sessions get the existing spinner, while reduced-motion mode switches to a
  static working line, periodic `Still working on ...` heartbeats, and an
  explicit `response ready.` completion cue.
- `_print_risky_action_warning(...)` reuses that compact-emphasis language for
  high/critical `/exec` and `/edit` flows so approvals feel calm and consistent
  before the actual approval prompt appears.
- `_print_startup_banner(...)` remains intentionally static: plain mode and
  narrow terminals get a concise text-first reveal, while wider interactive
  terminals keep the richer panel without introducing extra animation.
- `_celebration_burst(...)` is still the only decorative motion path, and it
  already downgrades to a one-line `🎉 ...` confirmation in reduced-motion,
  plain-mode, or non-TTY contexts.

Shared reveal-order helpers for every dashboard surface, plus broader approval /
retry choreography adoption, remain future Wave 30 follow-up work.

### Shared split-bar shell pattern (Wave 31+)

Starting with Wave 31, interactive chat, agent, and review flows should reuse a
consistent three-zone shell:

1. **Top context bar** — compact session/model/task/status context
2. **Primary output region** — the existing response, table, or narrative output
3. **Bottom control bar** — current mode plus 1–2 contextual hints

Implementation guidance:

- treat Rich/iTerm2 as the default experience for this shell pattern
- keep the output region semantics unchanged; the shell adds chrome around it
- use plain-text labels and separators when Rich is unavailable or plain mode is
  active
- omit low-priority hints first on narrow terminals or non-TTY paths

**Current shipped Wave 31 slice:** the shell contract now includes the always-on
top context bar in addition to the earlier footer/status cues. Today the runtime uses:

- `_print_predictive_affordances(...)` for the post-response **Suggested
  follow-ups** block
- `_print_followup_suggestions(...)` for the compact **bottom bar** footer with
  `mode: ...` plus contextual actions
- `_print_status_bar(...)` as the lightweight post-response status cue
- `_top_context_bar_lines(...)` + `_print_top_context_bar(...)` for the
  always-on pre-prompt shell chrome that surfaces session/cwd/plan/task/hidden
  context/recovery cues in deterministic plain text too

### Wave 31+ phase/step feedback contract

Wave 31 is the primary owner of live agent transparency during longer-running
operations.

| Pattern | Purpose | Fallback expectation |
| --- | --- | --- |
| `Phase: <name>` | announce the current operation phase before or during long work | print as plain text when Rich is unavailable |
| `Step N/M: <description>` | make deterministic sub-step progress visible | keep numeric counts; never convert to fake percentages |
| `✓ step completed` | briefly acknowledge completed work before the next step begins | use `[done]`/plain-text equivalent in plain mode |
| trust cues | elapsed time, checkpoint counts, clear cancel/help affordances | remain readable without color or motion |
| bottom-bar hints | mode + 1–2 context-sensitive actions | collapse to text list or omit hints first in narrow/non-TTY paths |

Language rules:

- prefer specific action verbs like `retry`, `resume`, `share`, `inspect`, and
  `export`
- avoid vague status words like `busy` when a concrete phase or step is known
- avoid fake progress bars when only step counts or elapsed time are trustworthy

Wave 34 can later extend this contract with richer trace and quality metadata,
but the base phase/step/trust-cue vocabulary belongs to Wave 31.

**Current shipped Wave 31 slice:** `_spinner_progress_snapshot(...)` and
`_with_spinner(...)` are the live implementation today. They currently expose a
fixed three-step request lifecycle:

1. `warming up` → `step 1/3` → `preparing the request`
2. `working` → `step 2/3` → `waiting for the agent response`
3. `wrapping up` → `step 3/3` → `finalizing the answer`

Reduced-motion mode prints the same trust cues as static text plus periodic
`Still working on ...` heartbeats. Completion ends with the shared `response
ready.` feedback line rather than decorative-only motion.

### Wave 32 session bookmarks & replay contract

Wave 32 layers bookmark metadata on top of the existing local session/event
store instead of creating a separate replay subsystem.

Current shipped slice:

- `SessionSummary.bookmarks` stores normalized bookmark entries with `id`,
  `label`, `created_at`, `turn_index`, `history_index`, and compact previews.
- `/bookmark [label]` captures the latest replay anchor for the active session.
- `/bookmarks` lists those anchors in plain text so the same output works in
  Rich, plain mode, reduced motion, and non-TTY environments.
- `/replay [session-id] [--from <bookmark>]` resolves the bookmark and slices the
  rebuilt conversation history from `history_index` forward.
- `session show`, `session share`, and `session export` surface the same bookmark
  metadata so handoffs and JSON exports preserve replay anchors.

Deferred follow-up work:

- bookmark markers inside `/timeline` and `/watch history`
- richer bookmark management (rename/delete/group)
- bookmark promotion into later workflow/macros surfaces

### Wave 33 workflow preview & substitution contract

Wave 33 reuses the existing persisted macro store rather than introducing a
second workflow engine.

Current shipped slice:

- `_workflow_store()` keeps `/workflow` and `/macro` backed by the same local
  preference data so older macros continue to work.
- `/workflow preview <name>` prints a dry-run step list without executing any
  commands.
- `_render_workflow_step(...)` resolves a narrow set of session-aware
  placeholders: `{cwd}`, `{session}`, `{plan}`, and `{task}`.
- `_macro_run(..., kind=\"workflow\")` uses the same placeholder resolution when
  workflows execute, so preview and runtime stay aligned.

Deferred follow-up work:

- workflow metadata beyond the shared macro store
- workflow embedding in session exports and handoffs
- per-step approval gates and richer workflow policy controls

### Wave 34 traceability & quality contract

Wave 34 ships as a thin inspection layer over the existing routing and rating
primitives rather than a new experimentation backend.

Current shipped slice:

- `/trace` reads the latest stored decision event and prints the route,
  rationale, confidence, timestamp, and latest rating context.
- `/quality` keeps the existing histogram, then appends a compact latest-route
  summary so users can pivot into `/trace` without hunting for the right
  surface.
- The same data must remain readable in Rich, plain, reduced-motion, and
  non-TTY output paths.

Deferred follow-up work:

- local experiment controls and comparison modes
- richer latency envelopes and portable quality export schemas

### Wave 35 runbook & export contract

Wave 35 ships as a reporting veneer over the existing session export,
collaboration snapshot, and storyline primitives.

Current shipped slice:

- `_build_session_runbook_text(...)` composes a Markdown handoff from persisted
  session state rather than maintaining a second reporting datastore.
- `/runbook [template]` renders the current session for interactive review and
  can save the same content to disk.
- `openclaw session export --format runbook --template <name>` reuses the exact
  same rendering path so chat and non-interactive exports stay aligned.
- `/exporttemplates` exposes the built-in reporting templates that define the
  audience label and section ordering for the current slice.

Deferred follow-up work:

- user-authored export templates
- richer redaction controls and additional output formats

### Wave 36 workspace recovery contract

Wave 36 ships as a recovery veneer over the existing session, handoff, watch,
and export primitives.

Current shipped slice:

- `build_workspace_capsule(session_id)` derives a compact recovery snapshot from
  persisted session data, outputs, bookmarks, watch state, and a stable
  workspace signature.
- `/workspace status|save|list|restore` promotes the existing handoff manifest
  store into a first-class workspace recovery flow for the terminal.
- `create_handoff(...)` now embeds workspace capsule metadata, and
  `apply_handoff(...)` restores cwd, files, plan/task linkage, and saved watch
  state into the new session.
- `export_session(...)` exposes `workspace_capsule` so browser/dashboard and
  scriptable clients can inspect the same recovery payload.

Deferred follow-up work:

- remote or multi-machine capsule sync
- restore-in-place semantics for the current session
- richer watch pause/ask policy controls during restore

### Wave 37 pattern library contract

Wave 37 ships as a pattern-library veneer over the existing workflow and macro
runtime.

Current shipped slice:

- `_pattern_store()` persists reusable patterns in `_PREFS["patterns"]`
  alongside workflows/macros, using lightweight metadata instead of a second
  execution backend.
- `/pattern save <name> [last N|workflow NAME]` can capture a reusable flow from
  recent command history or clone an existing workflow into the pattern library.
- `/pattern list|show|preview` exposes those saved flows in-terminal while
  preserving Rich/plain compatibility.
- `/pattern run <name>` reuses the shared command-sequence runner, so
  placeholder resolution and slash-command dispatch stay aligned with `/workflow
  run`.

Deferred follow-up work:

- auto-mined patterns from successful sessions
- versioned pattern variants and richer metadata
- export/import and collaboration-aware sharing flows

### Wave 38 structured collaboration contract

Wave 38 ships as a planning and readiness veneer over the existing collaboration
event stream, session links, and handoff manifests.

Current shipped slice:

- `build_collaboration_snapshot(...)` now separates structured `assignments` and
  `open_risks` from the existing notes and decisions.
- `/collab assign @actor TEXT` records ownership using the same local
  collaboration events used by `/collab note` and `/collab decision`.
- `/risk add|list|clear` manages a lightweight risk register by appending
  collaboration events with risk metadata instead of introducing a second
  planning store.
- `_handoff_check_snapshot(...)` derives readiness from linked plan/task
  context, assignments, existing handoffs, watch state, and unresolved risks,
  and `/handoff check` renders that audit in deterministic plain text.
- `_build_session_share_text(...)` now surfaces **ASSIGNMENTS** and **OPEN
  RISKS** so the operator-facing snapshot stays aligned with the new structured
  collaboration slice.

Deferred follow-up work:

- step-level `/plan structured` planning views
- dedicated gate commands and richer approval semantics
- dashboard/browser planning mirrors and remote enforcement

### Wave 39 learned routing contract

Wave 39 ships as a route-quality insight veneer over the existing routing trace
and rating history.

Current shipped slice:

- `_cmd_rate(...)` now stores route metadata when a current-session trace exists,
  so high/low ratings can be correlated with the last routed slash command.
- `_route_quality_summary()` aggregates those stored ratings by route and derives
  average score, sample count, and high-rating share using local prefs only.
- `/quality predict` and `/routing suggest|analyze` surface that learned summary
  as advisory terminal output; they do not rewrite or auto-apply routing.
- `/trace` remains the source of truth for explaining the last actual routing
  decision, keeping the learned layer transparent and reversible.

Deferred follow-up work:

- automatic route adaptation and experiment loops
- richer fairness or rollback controls for learned behavior
- dashboard/browser mirrors for route-quality summaries

### Wave 40 automation dashboard contract

Wave 40 ships as a computed operator dashboard veneer over the existing session,
watch, collaboration, and handoff state.

Current shipped slice:

- `_collect_operator_alerts()` derives retry, pending-intervention, stale-watch,
  and handoff-ready alerts from local session/watch state rather than a separate
  alert store.
- `_print_automation_dashboard()` aggregates active sessions, live watches,
  pending interventions, handoff-ready sessions, and top alerts into a compact
  terminal control-plane summary.
- `/dashboard automation`, `/alerts list|acknowledge`, and `/fleet
  status|health` all reuse those computed summaries, keeping the operator view
  local, deterministic, and plain-text friendly.
- acknowledged alert ids persist in `_PREFS["acknowledged_alerts"]`, which keeps
  the quieted-alert state lightweight and reversible.

Deferred follow-up work:

- predictive escalation or richer alert tuning
- browser/dashboard mirrors and remote fleet control

### Wave 41 incident log contract

Wave 41 ships as a lightweight incident annotation veneer over the existing
session, watch, and collaboration state.

Current shipped slice:

- `build_collaboration_snapshot(...)` now derives `open_incidents` from
  collaboration events tagged with `collab_kind="incident"`.
- `/incident list|log|resolve` reuses that same collaboration event stream, so
  operator issue tracking stays local and deterministic.
- `_build_session_share_text(...)` now surfaces **OPEN INCIDENTS** alongside
  assignments and risks in the session handoff snapshot.
- `_handoff_check_snapshot(...)` treats unresolved incidents like other
  readiness blockers and renders them in `/handoff check`.
- `_print_automation_dashboard()` now includes a session-wide open-incident
  count in the operator summary.

Deferred follow-up work:

- incident notes, stale-marking, and richer lifecycle controls
- cross-session incident aggregation or fleet-wide dashboards
- escalation automation and browser/dashboard mirrors

### Wave 42 source rendering reliability contract

Wave 42 ships as a reliability pass over the existing response preprocessing and
rendering pipeline.

Current shipped slice:

- `_preprocess_response_text(...)` now reuses a shared loose-sources regex so
  edge-case `Sources:` blocks still extract cleanly when the primary pass misses.
- `_render_response_body(...)` adds a final deduplication guard before
  delegating to `openclaw_cli_render`, which keeps sources headings out of the
  main response body.
- `_clean_sources_for_display(...)` strips ANSI escape codes from markdown-link
  labels and falls back to the raw URL when the display text looks corrupted.
- the ANSI sources panel now measures live terminal width instead of relying on
  a shorter cached render width, so the source box matches the current console.

Deferred follow-up work:

- per-source metadata, grouping, or extraction-confidence cues
- richer browser/dashboard source mirrors
- broader citation/reference UX beyond deduplication and fallback safety

### Wave 43 context & token intelligence contract

Wave 43 ships as a context-visibility pass over the existing session summary,
response footer, and startup discovery surfaces.

Current shipped slice:

- `/tokeninfo` remains the lightweight local estimator for session context usage,
  using the existing character-count heuristic and a 128k progress bar.
- `_response_footer_lines(...)` now lifts token count into the response headline
  when the API returns it, so token usage is easier to notice at completion time.
- `_session_age_label(...)` derives session age from `created_at`, and both
  `summarize_session(...)` and `_print_session_summary(...)` surface that age in
  plain-text session inspection output.
- the startup tips pool already includes `/tokeninfo`, which keeps the new
  context-awareness path discoverable without adding more chrome.

Deferred follow-up work:

- per-model context limits instead of the shared 128k heuristic
- token usage breakdowns by turn, actor, or command family
- proactive context-overflow warnings or auto-summary suggestions

### Wave 44 startup & first-run polish contract

Wave 44 ships as a startup-banner and discovery pass over the existing REPL
entrypoint, session history, and tips rotation.

Current shipped slice:

- `_time_greeting()` now drives contextual startup copy for morning, afternoon,
  and evening sessions.
- `_print_startup_banner(...)` checks persisted session count against milestone
  thresholds and renders a short celebration line when one is reached.
- the startup tips pool now includes the recent-command discovery set for
  `/tokeninfo`, `/trace`, `/handoff check`, `/fleet health`, `/alerts`,
  `/collab decision`, `/bookmark`, `/overlay`, `/pattern`, and `/draft multiline`.
- `run_chat(..., no_banner=True)` and the top-level `--no-banner` CLI flag keep
  scripted startup flows quiet by skipping the banner entirely.

Deferred follow-up work:

- per-user greeting personalization
- adaptive tip selection based on usage history
- richer startup animation or onboarding beyond the current lightweight banner

### Wave 45 context pressure guardrails contract

Wave 45 ships as a follow-up to the existing token-intelligence path rather than
as a new command family.

Current shipped slice:

- `_cmd_tokeninfo(...)` still uses the lightweight character-count heuristic, but
  now breaks estimated token usage down by actor so users can see whether user,
  assistant, or other history dominates the current context.
- `/tokeninfo` now highlights the largest actor share explicitly, keeping the
  terminal output scannable even when the session is long.
- pressure guidance is now staged: the existing mid-range stale-context tip
  remains, high pressure suggests bookmarking before clearing, and near-capacity
  pressure escalates to a stronger recovery hint.
- the surrounding operator surfaces now carry lighter follow-through: `/context`
  and the Session Dashboard surface next-send pressure, while `/watch status`
  surfaces next-retry pressure and recovery commands when automation inherits a
  heavy prompt or hidden context

Deferred follow-up work:

- per-model context limits instead of the shared 128k heuristic
- token usage breakdowns by turn or command family
- broader ambient overflow warnings beyond `/tokeninfo`, `/context`, `/session`,
  and `/watch status`

### Deferred interaction affordance follow-ups (current truth)

These items are still intentionally deferred even after the shipped Wave 31–45
slices:

- backend-backed SSE/token streaming is now wired into the interactive TTY chat
  runtime through `/api/agent/ask/stream`; non-TTY, JSON, and compact flows
  still fall back to the buffered request/response path
- `/edit` and `/exec` now pair their previews/prompts with compact approval-time
  review lines plus trust/recovery cues, so future follow-up work should target
  only larger review-shell expansions rather than reopening the shipped review
  baseline
- Wave 43–45 token heuristics are local and 128k-based; `/tokeninfo`,
  `/context`, `/session`, and `/watch status` now surface the shipped guardrail
  cues, while per-model limits and broader ambient overflow guidance remain
  deferred
- the full multi-region shell vision is still intentionally narrower than a true
  pane compositor, but the truthful current shell cues are now the top context
  bar, post-response status bar, and bottom footer together

### stderr vs stdout

- `_print_update_notice()` → **stderr** (must not corrupt JSON output from `--json` flag)
- `_print_error()` → **stdout** (tests assert on stdout)
- Fatal errors in `main()` → `sys.stderr`

### Shared helpers (defined ~L128–215)

| Helper | Purpose |
| --- | --- |
| `_status_emoji(status)` | Maps status strings to emoji (✓ ✗ ⚠ …) |
| `_print_feedback(message, level, detail)` | Shared compact confirmations, liveness notes, and completion cues |
| `_print_risky_action_warning(action, target, risk_level, recovery_hint)` | Accessible pre-approval emphasis for high/critical `/exec` and `/edit` actions |
| `_print_meta_footer(label, value)` | Dim metadata line below output blocks |
| `_print_error(msg)` | Red error panel (rich) or `error: msg` (plain) |
| `_print_shell_result(rc, stdout, stderr)` | Colored shell output with exit code badge |
| `_print_file_edit_result(path, diff)` | Unified diff display for `/edit` |
| `_with_spinner(msg, fn)` | Braille spinner wrapping a blocking call; reduced-motion mode uses text heartbeats + explicit completion feedback |
| `_preprocess_response_text(text)` | Clean raw LLM response text before rendering: strips `_via model_` trailers, extracts Sources section, removes inline `[N]` citation markers, converts pipe-in-bullet table patterns to real markdown tables. Returns `(body, sources)` |
| `_apply_inline_ansi(text)` | Apply bold (`**`), italic (`*`), and inline code (`` ` ``) as ANSI spans. Used by the ANSI markdown fallback renderer. |
| `_render_markdown_ansi(text)` | Full ANSI markdown renderer (fallback when Rich is absent or TTY not detected at module load). Handles H1–H4 headings, blockquotes (`▌`), fenced code blocks with language border, nested bullets, numbered lists, horizontal rules scaled to terminal width. |
| `_render_table_ansi(rows)` | Render a list of `[str]` rows as a columnar ANSI table, scaled to terminal width with `…` truncation. |
| `print_response(response, *, output_json, elapsed=0.0)` | Render a full AI response to stdout. Accepts optional `elapsed` (seconds) to show in the footer as `⏱ Xs  •  N tokens  •  model`. |

### ANSI palette constants (~L96)

```python
_R   = "\033[0m"    # reset
_B   = "\033[1m"    # bold
_DM  = "\033[2m"    # dim
_IT  = "\033[3m"    # italic
_UL  = "\033[4m"    # underline
_CY  = "\033[36m"   # cyan
_GR  = "\033[32m"   # green
_YE  = "\033[33m"   # yellow
_RE  = "\033[31m"   # red
_BCY = "\033[1;36m" # bold cyan
_BGR = "\033[1;32m" # bold green
_BYE = "\033[1;33m" # bold yellow
_BRE = "\033[1;31m" # bold red
_BBL = "\033[1;34m" # bold blue
_IT  = "\033[3m"    # italic
_UL  = "\033[4m"    # underline
```

---

## REPL loop

`run_chat()` (~L5025) is the main loop:

1. Draws prompt via `_make_prompt()` (shows session/autoroute hints)
2. Calls `registry.dispatch(prompt, ctx)` — handles slash commands first
3. If `dispatch` returns `None` **and** the prompt starts with `/` → prints
   "Unknown command" error and loops (does **not** fall through to AI)
4. If autoroute is on, passes natural-language prompts through `route_repl_prompt()`
5. Falls through to `invoke_openclaw()` for plain chat

The REPL prompt badge `[autoroute:off]` is shown (dim, in the prompt) whenever
auto-routing is disabled for the session. Use `/autoroute on` to re-enable.
Users who see this badge can type `/help` for a full explanation.

### Slash command registry

`build_chat_command_registry()` (~L4470) registers all `/commands`.

```python
registry.register(SlashCommand(
    name="example",
    description="...",
    handler=_cmd_example,
    aliases=("ex",),
))
```

`dispatch()` strips the leading `/`, splits on whitespace, looks up the name
or alias in `_lookup`, and calls the handler. Returns `_CMD_CONTINUE`,
`_CMD_QUIT`, or `None` (unrecognised — **not** an error before the guard).

**To add a new slash command**: write a `_cmd_foo(ctx: ChatCommandContext) -> str`
handler, register it in `build_chat_command_registry()`, and add a row to the
`print_chat_help()` table (~L4600).

---

## Update system

### Overview

```
startup
  └─ _update_check_worker() [background thread]
       ├─ standalone install?  →  fetch OPENCLAW_URL/cli-update/meta (SHA256 hashes)
       │                           compare to local file hashes
       │                           set _standalone_needs_update = True if any differ
       └─ venv/pip install?   →  fetch PyPI version → set _latest_version

main() [after thread.join(3.5s)]
  └─ if _standalone_needs_update or _latest_version > current
       → _print_update_notice()   [to stderr, before readline loop]

/update  →  _cmd_update()
  └─ standalone?  →  _update_standalone_install(install_dir, base_url)
                       download 4 files from OPENCLAW_URL/cli-update/{name}
                       atomic replace (.tmp → dest)
                       reset _standalone_needs_update = False
  └─ venv?        →  pip install --upgrade openclaw
```

### Server endpoints (`discord_web.py`)

| Endpoint | Purpose |
| --- | --- |
| `GET /cli-update/{filename}` | Serve raw text of a whitelisted CLI file |
| `GET /cli-update/meta` | Return `{"filename": "<sha256>", …}` for all 4 CLI files |

Whitelist (`_CLI_UPDATE_WHITELIST`):
- `openclaw_cli.py`
- `openclaw_cli_actions.py`
- `openclaw_cli_sessions.py`
- `subprocess_utils.py`

No authentication — these endpoints are local-network-only (same as `/health`).
The `meta` route is registered **before** the `{filename}` wildcard to prevent
aiohttp from matching "meta" as a filename.

### Version detection

- **Standalone**: SHA256 hash comparison (local vs server via `/cli-update/meta`)
- **Venv/pip**: PyPI version string comparison (PyPI `openclaw` package)

> ⚠️ The PyPI `openclaw` package (e.g. `2026.3.20`) is the **cmdop SDK wrapper**,
> not the CLI. Never compare CLI version to PyPI version for standalone installs.

---

## Standalone install

A "standalone install" is when `openclaw_cli.py` runs from
`~/.local/share/openclaw-cli/` (not from `site-packages`).

Detection: `_standalone_install_dir()` (~L650) checks:
```python
"site-packages" not in str(__file__)
and (Path(__file__).parent / "openclaw_cli_sessions.py").exists()
```

### Structure on the remote Mac

```
~/.local/bin/openclaw          # bash shim: exec python3 ~/.local/share/…/openclaw_cli.py
~/.local/bin/openclaw-cli      # symlink → openclaw
~/.local/share/openclaw-cli/
    openclaw_cli.py
    openclaw_cli_actions.py
    openclaw_cli_sessions.py
    subprocess_utils.py
    openclaw_aliases.sh        # shell functions: openclaw(), OpenClaw(), oc-*
~/.zshrc additions:
    export OPENCLAW_URL="http://192.168.1.93:8765"
    source "~/.local/share/openclaw-cli/openclaw_aliases.sh"
```

### Install / uninstall scripts (run from Mac Mini)

```bash
# Fresh install to laptop
bash scripts/install_openclaw_cli_remote.sh macbook

# Remove everything
bash scripts/uninstall_openclaw_cli_remote.sh macbook

# Quick sync after code changes (no .zshrc edits)
scp src/openclaw_cli.py src/openclaw_cli_actions.py \
    macbook:/Users/davevoyles/.local/share/openclaw-cli/
```

---

## Python 3.9 compatibility (laptop)

The laptop uses Apple CommandLineTools Python 3.9. Any file synced there must:

- Use `from __future__ import annotations` at the top (enables `X | Y` union
  syntax and `list[str]` generic syntax in type hints at runtime)
- Avoid `match`/`case` statements
- Avoid `str.removeprefix()` / `str.removesuffix()` without a compat shim

Currently affected files: all four in `~/.local/share/openclaw-cli/`.

---

## Testing

```bash
python3 -m pytest tests/test_openclaw_cli.py \
  -k "not (test_run_chat_uses_router_before_generic_chat_fallback \
       or test_run_chat_routed_edit_still_requests_approval \
       or test_run_chat_autoroutes_plan_candidate_into_persisted_execution \
       or test_run_chat_supports_help_command \
        or test_help_output_includes_new_commands)" \
  -q
```

Expected: **203+ passed**. The 5 excluded tests are pre-existing failures
unrelated to CLI UX work.

After any change to `src/openclaw_cli.py` or `src/openclaw_cli_actions.py`:
1. Run tests
2. `scp src/openclaw_cli.py src/openclaw_cli_actions.py macbook:/Users/davevoyles/.local/share/openclaw-cli/`
3. Redeploy server if `discord_web.py` changed: `docker compose up -d --build`

---

## Commit history (UX improvements, 2026-04)

| Commit | What changed |
| --- | --- |
| `feat: show update-available notice on startup` | Background thread + PyPI version check |
| `feat: add 'openclaw update' self-upgrade command` | `openclaw update` subcommand with pip + spinner |
| `feat: rich colorful update notice and startup banner` | Rich panels for update and banner |
| `feat: visual polish pass — rich panels, tables, color, emojis` | Round 1 — health, session, plan, watch commands |
| `feat: visual polish round 2 — auth, approval, exec, watch` | Round 2 — approval risk colors, shell/edit results |
| `feat: round 3+4 — spinners, status command, REPL polish` | Spinners on ask/analyze/write, `openclaw status` dashboard |
| `feat: round 5 — plan exec progress, watch banner` | Plan step badges, watch Rich start panel, `/cwd` styling |
| `feat: round 6 — usage errors, approval declines, save footers` | Unified `_print_error` / `_print_meta_footer` usage |
| `feat: add /update REPL slash command` | `/update` in REPL without leaving chat |
| `feat: standalone /update fetches files from openclaw server` | `/cli-update/*` server endpoint + urllib download |
| `fix: prevent update notice appearing inside readline prompt` | Thread sets global; main() prints after join |
| `fix: block unknown slash commands from reaching AI` | `/unknown` → error message, not AI fallback |
| `fix: standalone update check uses file hashes, not PyPI version` | SHA256 hash comparison via `/cli-update/meta` |
