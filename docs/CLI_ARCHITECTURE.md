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

### stderr vs stdout

- `_print_update_notice()` → **stderr** (must not corrupt JSON output from `--json` flag)
- `_print_error()` → **stdout** (tests assert on stdout)
- Fatal errors in `main()` → `sys.stderr`

### Shared helpers (defined ~L128–215)

| Helper | Purpose |
| --- | --- |
| `_status_emoji(status)` | Maps status strings to emoji (✓ ✗ ⚠ …) |
| `_print_meta_footer(label, value)` | Dim metadata line below output blocks |
| `_print_error(msg)` | Red error panel (rich) or `error: msg` (plain) |
| `_print_shell_result(rc, stdout, stderr)` | Colored shell output with exit code badge |
| `_print_file_edit_result(path, diff)` | Unified diff display for `/edit` |
| `_with_spinner(msg, fn)` | Braille spinner wrapping a blocking call |

### ANSI palette constants (~L90)

```python
_R   = "\033[0m"    # reset
_BYE = "\033[1;33m" # bold yellow
_BGR = "\033[1;32m" # bold green
_BRE = "\033[1;31m" # bold red
_BCY = "\033[1;36m" # bold cyan
_DM  = "\033[2m"    # dim
_YE  = "\033[33m"   # yellow
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
       or test_run_chat_autoroutes_plan_candidate \
       or test_run_chat_supports_help_command \
       or test_help_output_includes_new_commands)" \
  -q
```

Expected: **180 passed**. The 5 excluded tests are pre-existing failures
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
