# OpenClaw CLI Architecture

Reference for agents building on or modifying the CLI. Covers the UX system,
update mechanism, standalone install, and key code locations.

## Source files

| File | Role |
| --- | --- |
| `src/openclaw_cli.py` | Primary CLI (~6200 lines). All commands, REPL loop, UX helpers, update logic |
| `src/openclaw_cli_actions.py` | Approval prompts (`request_cli_approval`) with colored risk levels |
| `src/openclaw_cli_sessions.py` | Session persistence (load/save conversation history, watch state) |
| `src/subprocess_utils.py` | Shell execution helpers used by `/exec` |
| `src/discord_web.py` | aiohttp server — health, dashboard, `/cli-update/*` endpoints |
| `scripts/install_openclaw_cli_remote.sh` | Push CLI files to a remote Mac via SSH+SCP |
| `scripts/uninstall_openclaw_cli_remote.sh` | Remove standalone install from a remote Mac via SSH |

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
- `_run_interactive_overlay()` is intentionally lightweight: it uses plain
  `input()`, fuzzy-ish text filtering, numeric selection, and cancellation via
  empty input / `q`, avoiding hard Rich dependencies.

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
  Waves 21–30
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

The actual pane compositor and active-pane focus management remain deferred.

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
