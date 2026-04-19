# OpenClaw CLI — Module Dependency Map
<!-- Updated: 2026-04-18 -->


Reference for understanding which modules depend on which, to prevent circular imports and guide where new code should live.

---

## Dependency Graph

```
openclaw_cli.py (orchestrator — imports everything)
  ├── openclaw_cli_ui_core.py     (leaf — no CLI module deps)
  ├── openclaw_cli_auth.py        (leaf — no CLI module deps)
  ├── openclaw_cli_sessions.py    (leaf — no CLI module deps)
  ├── openclaw_cli_actions.py     (leaf — no CLI module deps)
  ├── openclaw_cli_update.py  ──► openclaw_cli_ui_core
  ├── openclaw_cli_render.py  ──► openclaw_cli_ui_core
  ├── openclaw_cli_diff.py    ──► openclaw_cli_ui_core
  ├── openclaw_cli_path_utils.py ─► openclaw_cli_ui_core
  ├── openclaw_cli_router.py  ──► openclaw_cli_sessions, openclaw_cli_ui_core
  └── openclaw_cli_macros.py  ──► openclaw_cli_sessions, openclaw_cli_ui_core
```

**Rule:** Arrows point from importer → imported. `openclaw_cli.py` sits at the top; submodules never arrow back up to it.

---

## Per-Module Details

### `openclaw_cli_ui_core.py`
**Role:** ANSI color constants, TTY detection, palette helpers.

**Imports from CLI modules:** _none_ (leaf node)

**Imported by:**
- `openclaw_cli_update.py`
- `openclaw_cli_render.py`
- `openclaw_cli_diff.py`
- `openclaw_cli_path_utils.py`
- `openclaw_cli_router.py`
- `openclaw_cli_macros.py`
- `openclaw_cli.py`

**Globals shared at call time:** Exposes `_get_is_tty()` helper so callers can read TTY state without touching `openclaw_cli._IS_TTY` directly.

---

### `openclaw_cli_auth.py`
**Role:** Token storage, keychain integration, `OpenClawCliError` exception class.

**Imports from CLI modules:** _none_ (leaf node)

**Imported by:** `openclaw_cli.py`

**Globals shared at call time:** n/a — exposes functions and the error class.

---

### `openclaw_cli_sessions.py`
**Role:** Session persistence, conversation history load/save, event logging.

**Imports from CLI modules:** _none_ (leaf node)

**Imported by:**
- `openclaw_cli_router.py`
- `openclaw_cli_macros.py`
- `openclaw_cli.py`

**Globals shared at call time:** `_PREFS` dict is **not** imported; session functions receive configuration as parameters.

---

### `openclaw_cli_actions.py`
**Role:** Shell command approval prompts with colored risk levels, `request_cli_approval`.

**Imports from CLI modules:** _none_ (leaf node)

**Imported by:** `openclaw_cli.py`

---

### `openclaw_cli_update.py`
**Role:** Version check against server, self-update download logic.

**Imports from CLI modules:**
- `openclaw_cli_ui_core` — ANSI constants for progress output

**Imported by:** `openclaw_cli.py` (both `import openclaw_cli_update as _update_mod` and direct `from` imports)

---

### `openclaw_cli_render.py`
**Role:** Response rendering pipeline, `RenderContext` dataclass, Rich/ANSI fallback logic.

**Imports from CLI modules:**
- `openclaw_cli_ui_core` — ANSI palette and TTY detection

**Imported by:** `openclaw_cli.py` (`import openclaw_cli_render as _render_mod`)

---

### `openclaw_cli_diff.py`
**Role:** Unified diff colorization for `/diff` and edit previews.

**Imports from CLI modules:**
- `openclaw_cli_ui_core` — ANSI constants

**Imported by:** `openclaw_cli.py` (`from openclaw_cli_diff import _render_diff_ansi as _render_diff_ansi_impl`)

---

### `openclaw_cli_path_utils.py`
**Role:** File path detection, OSC-8 link formatting, follow-up suggestion generation, filename helpers.

**Imports from CLI modules:**
- `openclaw_cli_ui_core` — ANSI constants, `_get_is_tty()`

**Imported by:** `openclaw_cli.py` (`import openclaw_cli_path_utils as _path_utils`)

---

### `openclaw_cli_router.py`
**Role:** Intent classification, `ReplRouteDecision` dataclass, routing logic for the REPL.

**Imports from CLI modules:**
- `openclaw_cli_sessions` — session state for context-aware routing
- `openclaw_cli_ui_core` — constants (if needed for output)

**Imported by:** `openclaw_cli.py` (`from openclaw_cli_router import ...`)

---

### `openclaw_cli_macros.py`
**Role:** Macro recording, storage, playback, and multi-step workflow engine.

**Imports from CLI modules:**
- `openclaw_cli_sessions` — session load for macro context
- `openclaw_cli_ui_core` — ANSI colors for output

**Imported by:** `openclaw_cli.py` (`import openclaw_cli_macros as _macros_mod`)

---

### `openclaw_cli.py`
**Role:** Main REPL, command dispatch, CLI argument parsing (~13,300 lines). Imports all submodules above.

**Imports from CLI modules:** All of the above — see graph at the top.

**Imported by:** nothing (entry point)

---

## Import Rules

1. **Submodules never import `openclaw_cli`** — this prevents circular imports. If you need something from `openclaw_cli`, pass it as a parameter.

2. **Leaf modules** (`ui_core`, `auth`, `sessions`, `actions`) have zero CLI-module dependencies. Keep them that way.

3. **Mid-level modules** (`render`, `diff`, `path_utils`, `update`) may only import from leaf modules.

4. **Compound modules** (`router`, `macros`) may import from leaves and mid-level, but not from each other.

5. **Only `openclaw_cli.py`** may import from all levels.

---

## How `_PREFS` Is Shared

`_PREFS` is a `dict[str, Any]` defined in `openclaw_cli.py`. It holds user preferences (accessibility settings, display options) that persist across REPL commands.

**Sharing strategy: by-reference parameter passing**

Submodules that need preference values receive them as function arguments — they do **not** import `_PREFS` directly from `openclaw_cli`. This:
- prevents circular imports
- makes each function independently testable (`monkeypatch.setitem(mod._PREFS, key, val)` in tests works because the dict is shared by reference)
- keeps the module boundary clean

**In tests:**

```python
import openclaw_cli as mod

# Correct: setitem mutates the shared dict
monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)

# Wrong: setattr replaces the whole dict reference
monkeypatch.setattr(mod, "_PREFS", {"plain_mode": True})  # ❌ breaks sharing
```

---

## Circular Import Prevention

The architecture enforces a strict layered hierarchy:

```
Entry point → Compound → Mid-level → Leaf
```

If you find yourself needing to import `openclaw_cli` from a submodule, the right solution is to refactor the shared logic into a leaf module and import from there.
