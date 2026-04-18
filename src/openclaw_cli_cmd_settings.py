"""Settings and appearance command handlers.

Extracted from openclaw_cli.py (TD-29).
Handlers: _cmd_theme, _cmd_overlay, _cmd_colorscheme, _cmd_emojiheaders,
          _cmd_emoji, _cmd_layout, _cmd_links, _cmd_pasteguard, _cmd_paste,
          _cmd_accessibility, _cmd_keybind

Helpers also moved (only used by _cmd_theme):
  _print_theme_preview, _cycle_theme

Allowed direct imports: openclaw_cli_prefs (constants/pure helpers),
                        openclaw_cli_ui_core (ANSI codes, _CMD_CONTINUE-compat).
All openclaw_cli.py globals accessed via _m() to respect test monkeypatching.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openclaw_cli_prefs import (
    _A11Y_HIGH_CONTRAST,
    _A11Y_PLAIN_MODE,
    _A11Y_REDUCED_MOTION,
    _EMOJI_PACKS,
    _THEME_ALIASES,
    _THEME_DESCRIPTIONS,
    _THEME_ORDER,
    _THEMES,
    _emoji_pack_name,
    _normalize_theme_name,
    _save_prefs,
)
from openclaw_cli_ui_core import (
    _B,
    _BGR,
    _BRE,
    _BYE,
    _DM,
    _GR,
    _R,
    _YE,
)

if TYPE_CHECKING:
    from openclaw_cli_types import ChatCommandContext

_CMD_CONTINUE: str = "continue"  # matches openclaw_cli._CMD_CONTINUE


def _m() -> Any:
    """Lazy import of openclaw_cli — avoids circular import, respects test monkeypatching."""
    import openclaw_cli as _cli  # noqa: PLC0415
    return _cli


# ---------------------------------------------------------------------------
# Helpers moved wholesale from main (only called by _cmd_theme)
# ---------------------------------------------------------------------------

def _print_theme_preview(theme_name: str, *, persisted: bool) -> None:
    """Print a compact theme preview without requiring Rich."""
    m = _m()
    is_tty = m._get_is_tty()
    normalized = _normalize_theme_name(theme_name)
    _, ansi_code = _THEMES[normalized]
    swatch = f"{ansi_code}{'━' * 8}{_R}" if is_tty else "--------"
    state = "saved" if persisted else "preview"
    print(
        f"  Theme {state}: {_B}{normalized}{_R} — "
        f"{_THEME_DESCRIPTIONS.get(normalized, 'accent theme')} {swatch}"
    )
    print(f"  {m._theme_ansi()}{'─' * 14}{_R} {m._status_emoji('healthy')} accent sample")
    print(f"  {m._e('💡', '[tip]')} Try /theme next, /theme prev, or /emoji preview for quick comparisons.")


def _cycle_theme(direction: str) -> None:
    """Advance the stored theme forward or backward through the palette."""
    m = _m()
    current = _normalize_theme_name(m._PREFS.get("theme", "default"))
    index = _THEME_ORDER.index(current)
    if direction == "prev":
        next_theme = _THEME_ORDER[(index - 1) % len(_THEME_ORDER)]
    else:
        next_theme = _THEME_ORDER[(index + 1) % len(_THEME_ORDER)]
    m._prefs_set("theme", next_theme)
    _print_theme_preview(next_theme, persisted=True)


# ---------------------------------------------------------------------------
# Settings command handlers
# ---------------------------------------------------------------------------

def _cmd_theme(ctx: "ChatCommandContext") -> str:
    """Handler for /theme — display or set the UI colour theme."""
    m = _m()
    is_tty = m._get_is_tty()
    token = ctx.args.strip().lower()

    if not token or token == "list":
        current = _normalize_theme_name(m._PREFS.get("theme", "default"))
        print(f"\n  Available themes (current: {_B}{current}{_R}):\n")
        for name, (_rich_style, ansi_code) in _THEMES.items():
            marker = " ← current" if name == current else ""
            if is_tty:
                swatch = f"{ansi_code}{'━' * 6}{_R}"
            else:
                swatch = "------"
            desc = _THEME_DESCRIPTIONS.get(name, "")
            print(f"    {_B}{name:<10}{_R} {swatch} {desc}{_DM}{marker}{_R}")
        print("\n  Usage: /theme <name> | list | preview [name] | next | prev | reset\n")
        return _CMD_CONTINUE

    if token == "next":
        _cycle_theme("next")
        return _CMD_CONTINUE
    if token in {"prev", "previous"}:
        _cycle_theme("prev")
        return _CMD_CONTINUE
    if token == "reset":
        m._prefs_set("theme", "default")
        _print_theme_preview("default", persisted=True)
        return _CMD_CONTINUE
    if token.startswith("preview"):
        parts = token.split()
        requested = parts[1] if len(parts) > 1 else _normalize_theme_name(m._PREFS.get("theme", "default"))
        normalized = _normalize_theme_name(requested)
        if requested not in _THEMES and requested not in _THEME_ALIASES and normalized == "default":
            names = "  ".join(_THEME_ORDER)
            print(f"{_BRE}error:{_R} Unknown theme '{requested}'. Choose from: {names}")
            return _CMD_CONTINUE
        original_theme = m._PREFS.get("theme", "default")
        m._PREFS["theme"] = normalized
        _print_theme_preview(normalized, persisted=False)
        m._PREFS["theme"] = original_theme
        return _CMD_CONTINUE

    normalized = _normalize_theme_name(token)
    if token not in _THEMES and token not in _THEME_ALIASES and normalized == "default":
        names = "  ".join(_THEME_ORDER)
        print(f"{_BRE}error:{_R} Unknown theme '{token}'. Choose from: {names}")
        return _CMD_CONTINUE

    m._prefs_set("theme", normalized)
    _print_theme_preview(normalized, persisted=True)
    return _CMD_CONTINUE


def _cmd_overlay(ctx: "ChatCommandContext") -> str:
    """/overlay [on|off|status] — manage opt-in interactive overlays."""
    m = _m()
    token = (ctx.args or "").strip().lower()
    if not token or token == "status":
        state = "ON" if m._interactive_overlays_enabled() else "OFF"
        availability = "available" if m._overlay_available() else "unavailable"
        print(f"Interactive overlays: {state} ({availability} in this terminal)")
        print("Supported surfaces: /outputs, /sessions, and openclaw session list --interactive")
        return _CMD_CONTINUE
    if token not in {"on", "off"}:
        m._print_error("Usage: /overlay [on|off|status]")
        return _CMD_CONTINUE
    enabled = token == "on"
    m._prefs_set("interactive_overlays", enabled)
    if enabled:
        print("Interactive overlays enabled for supported list commands.")
    else:
        print("Interactive overlays disabled; list commands will stay non-interactive.")
    return _CMD_CONTINUE


def _cmd_colorscheme(ctx: "ChatCommandContext") -> str:
    """/colorscheme [name|list|reset] — view or set the extended color scheme."""
    m = _m()
    arg = (ctx.args or "").strip().lower()
    is_tty = m._get_is_tty()

    if not arg or arg == "list":
        current = m._PREFS.get("color_scheme", "default")
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print("\n[bold cyan]🎨 Color Schemes[/]\n")
            for name, scheme in m._EXTENDED_SCHEMES.items():
                active = " ← active" if name == current else ""
                primary = scheme.get("primary", "")
                reset = "\033[0m"
                label = scheme.get("label", name)
                m._RICH_CONSOLE.print(f"  {primary}■{reset}  [bold]{name}[/]  [dim]{label}{active}[/]")
            m._RICH_CONSOLE.print("\n  [dim]Use /colorscheme <name> to activate[/]\n")
        else:
            def current_marker(n: str) -> str:
                return " ← active" if n == current else ""
            print("\n🎨 Color Schemes\n")
            for name, scheme in m._EXTENDED_SCHEMES.items():
                p = scheme.get("primary", "")
                print(f"  {p}■\033[0m  {name}  {scheme.get('label', '')}{current_marker(name)}")
            print("\n  Use /colorscheme <name> to activate\n")
        return _CMD_CONTINUE

    if arg == "reset":
        arg = "default"

    if arg not in m._EXTENDED_SCHEMES:
        names = ", ".join(m._EXTENDED_SCHEMES.keys())
        msg = f"Unknown scheme '{arg}'. Available: {names}"
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    m._prefs_set("color_scheme", arg)
    scheme = m._EXTENDED_SCHEMES[arg]
    label = scheme.get("label", arg)

    if m._RICH_AVAILABLE and is_tty:
        m._RICH_CONSOLE.print(f"\n[bold green]✅ Color scheme set to[/] [bold]{arg}[/] [dim]{label}[/]\n")
    else:
        print(f"\n✅ Color scheme → {arg} {label}\n")

    return _CMD_CONTINUE


def _cmd_emojiheaders(ctx: "ChatCommandContext") -> str:
    """/emojiheaders [on|off] — toggle emoji prefixes on AI response headings."""
    m = _m()
    arg = ctx.args.strip().lower()
    if arg in ("on", "off"):
        m._prefs_set("emoji_headers", (arg == "on"))
        state = "on" if m._PREFS["emoji_headers"] else "off"
        is_tty = m._get_is_tty()
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print(f"[green]✓[/] emoji headers [bold]{state}[/]")
        else:
            print(f"✓ emoji headers {state}")
    else:
        state = "on" if m._PREFS.get("emoji_headers", True) else "off"
        is_tty = m._get_is_tty()
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print(f"[dim]emoji headers is [bold]{state}[/] — /emojiheaders on|off[/]")
        else:
            print(f"emoji headers is {state}")
    return _CMD_CONTINUE


def _cmd_emoji(ctx: "ChatCommandContext") -> str:
    """Handler for /emoji — toggle emoji display on or off."""
    m = _m()
    token = ctx.args.strip().lower()
    pack = _emoji_pack_name()
    if not token or token == "status":
        state = "on" if pack != "ascii" else "off"
        print(
            f"  Emoji is currently {_B}{state}{_R} "
            f"(pack: {_B}{pack}{_R}). Usage: /emoji on | off | pack <classic|minimal|ascii> | preview"
        )
        return _CMD_CONTINUE
    if token == "preview":
        print("  Emoji packs:")
        original_pack = m._PREFS.get("emoji_pack", "classic")
        original_flag = m._PREFS.get("emoji", True)
        for pack_name in ("classic", "minimal", "ascii"):
            m._PREFS["emoji_pack"] = pack_name
            m._PREFS["emoji"] = pack_name != "ascii"
            sample = " ".join(
                [
                    m._e("💬", "[chat]"),
                    m._status_emoji("healthy"),
                    m._e("💡", "[tip]"),
                    m._e("📍", "[pin]"),
                ]
            )
            marker = " ← current" if pack_name == pack else ""
            print(f"    {_B}{pack_name:<8}{_R} {sample}{marker}")
        m._PREFS["emoji_pack"] = original_pack
        m._PREFS["emoji"] = original_flag
        return _CMD_CONTINUE
    if token.startswith("pack "):
        requested = token.split(None, 1)[1].strip().lower()
        if requested not in _EMOJI_PACKS:
            print(f"{_BRE}error:{_R} Unknown emoji pack '{requested}'. Choose from: classic, minimal, ascii")
            return _CMD_CONTINUE
        m._PREFS["emoji_pack"] = requested
        m._prefs_set("emoji", requested != "ascii")
        print(f"  Emoji pack set to {_B}{requested}{_R}. Run /emoji preview to compare packs.")
        return _CMD_CONTINUE
    if token == "on":
        m._PREFS["emoji"] = True
        if _emoji_pack_name() == "ascii":
            m._PREFS["emoji_pack"] = "classic"
        _save_prefs()
        print(f"  Emoji enabled ✓ (pack: {_B}{_emoji_pack_name()}{_R})")
    elif token == "off":
        m._PREFS["emoji"] = False
        m._prefs_set("emoji_pack", "ascii")
        print("  Emoji disabled — ASCII fallbacks active.")
    else:
        print(f"{_BRE}error:{_R} Expected 'on', 'off', 'pack <name>', or 'preview', got '{token}'")
    return _CMD_CONTINUE


def _cmd_layout(ctx: "ChatCommandContext") -> str:
    """Handler for /layout — switch density or render preset workspaces."""
    m = _m()
    token = ctx.args.strip().lower()
    valid_layouts = ("compact", "normal", "verbose", "plain")
    preset_aliases = {
        "focus": "focus",
        "watch": "watch-monitor",
        "watch-monitor": "watch-monitor",
        "monitor": "watch-monitor",
        "handoff": "handoff",
        "collab": "handoff",
        "collaboration": "handoff",
    }
    if not token:
        current = m._effective_layout_mode()
        preset = m._layout_preset_name()
        print(f"  Layout is currently {_B}{current}{_R}.")
        if preset:
            config = m._layout_preset_config(preset)
            fallback = m._layout_preset_fallback()
            print(f"  Preset:           {_B}{config['label']}{_R} ({fallback})")
            print(f"  Active pane:      {m._layout_focus_name()}")
            print(f"  Primary pane:     {config['primary']}")
            print(f"  Supporting pane:  {config['supporting']}")
            print("  Preview now with /layout show. Reset to single-pane with /layout reset.")
        else:
            print("  Preset:           single-pane default")
            print("  Usage: /layout compact | normal | verbose | plain")
            print("         /layout preset focus|watch-monitor|handoff")
            print("         /layout show | /layout focus primary|supporting | /layout reset")
        return _CMD_CONTINUE
    if token == "show":
        m._print_layout_preset_workspace(ctx)
        return _CMD_CONTINUE
    if token.startswith("focus "):
        requested_focus = token.split(None, 1)[1].strip()
        if requested_focus not in {"primary", "supporting"}:
            m._print_error("Usage: /layout focus primary|supporting")
            return _CMD_CONTINUE
        if not m._layout_preset_name():
            m._print_error("Choose a preset first: /layout preset focus|watch-monitor|handoff")
            return _CMD_CONTINUE
        previous_focus = m._layout_focus_name()
        m._prefs_set("layout_focus", requested_focus)
        if requested_focus == previous_focus:
            detail = f"No transition needed; {requested_focus} is already active."
        else:
            detail = f"Focus transition: {previous_focus} -> {requested_focus}"
        m._print_feedback(f"Active pane set to {requested_focus}.", level="success", detail=detail)
        m._print_layout_preset_workspace(ctx)
        return _CMD_CONTINUE
    preset_token = token.split(None, 1)[1].strip() if token.startswith("preset ") else token
    if preset_token in preset_aliases:
        preset = preset_aliases[preset_token]
        m._PREFS["layout_preset"] = preset
        m._prefs_set("layout_focus", "primary")
        config = m._layout_preset_config(preset)
        fallback = m._layout_preset_fallback()
        m._print_feedback(
            f"Layout preset set to {config['label']}.",
            level="success",
            detail=f"primary {config['primary']} · supporting {config['supporting']} · fallback {fallback}",
        )
        m._print_layout_preset_workspace(ctx)
        return _CMD_CONTINUE
    if token in {"reset", "off", "default", "single", "single-pane"}:
        m._PREFS["layout_preset"] = ""
        m._prefs_set("layout_focus", "primary")
        m._print_feedback("Layout preset reset to single-pane default.", level="success")
        return _CMD_CONTINUE
    if token not in valid_layouts:
        print(
            f"{_BRE}error:{_R} Expected one of "
            "compact, normal, verbose, plain, preset <focus|watch-monitor|handoff>, "
            "show, focus <primary|supporting>, or reset, "
            f"got '{token}'"
        )
        return _CMD_CONTINUE
    m._PREFS["layout"] = token
    m._prefs_set(_A11Y_PLAIN_MODE, token == "plain")
    desc = {
        "compact": "reduced chrome; separator + status bar hidden",
        "normal": "default density",
        "verbose": "full density with extra context where available",
        "plain": "screen-reader/plain-text friendly mode",
    }[token]
    m._print_feedback(f"Layout set to {token}.", level="success", detail=desc)
    return _CMD_CONTINUE


def _cmd_links(ctx: "ChatCommandContext") -> str:
    """/links [on|off] — toggle clickable OSC 8 hyperlinks in responses (requires modern terminal)."""
    return _m()._handle_simple_toggle_pref(ctx, "clickable_links", "clickable links")


def _cmd_pasteguard(ctx: "ChatCommandContext") -> str:
    """Toggle or inspect the paste guard setting."""
    m = _m()
    token = (ctx.args or "").strip().lower()
    if token == "on":
        m._prefs_set("paste_guard", True)
        print(f"  {_GR}{m._e('✅', '[OK]')} Paste guard enabled.{_R}")
    elif token == "off":
        m._prefs_set("paste_guard", False)
        print(f"  {_YE}{m._e('⚠️', '[warn]')} Paste guard disabled.{_R}")
    else:
        state = "on" if m._PREFS.get("paste_guard", True) else "off"
        print(f"  Paste guard is currently {_B}{state}{_R}. Use /pasteguard on|off to change.")
    return _CMD_CONTINUE


def _cmd_paste(ctx: "ChatCommandContext") -> str:  # noqa: ARG001
    """Show multi-line paste options for the current session."""
    m = _m()
    print(f"\n  {_B}Pasting multi-line text into OpenClaw{_R}\n")
    print(f"  {_DM}When you paste text with newlines, each newline is normally treated as Enter.{_R}")
    print(f"  {_DM}Use one of these options to paste a multi-line query as a single message:{_R}\n")
    print(f"  {m._e('①', '1.')} {_B}/multiline{_R}  — toggle multiline mode, then paste, then type {_B}\\end{_R} to submit")
    print(f"  {m._e('②', '2.')} {_B}Bracketed paste{_R} — automatic on supported terminals (iTerm2, Terminal.app)")
    print("              OpenClaw detects paste markers and buffers lines automatically.")
    print(f"  {m._e('③', '3.')} {_B}iTerm2 shortcut{_R} — Edit → Paste Special → Paste Escaping Special Characters")
    print("              (joins lines; may alter formatting)\n")
    return _CMD_CONTINUE


def _cmd_accessibility(ctx: "ChatCommandContext") -> str:
    """Show or configure accessibility modes (reduced-motion, plain, high-contrast)."""
    m = _m()
    args = (ctx.args or "").strip()
    parts = args.split() if args else []
    sub = parts[0].lower() if parts else "status"
    val = parts[1].lower() if len(parts) > 1 else ""

    def _on_off(val: str, key: str, label: str) -> str:
        if val == "on":
            m._PREFS[key] = True
            if key == _A11Y_PLAIN_MODE:
                m._PREFS["layout"] = "plain"
            _save_prefs()
            return f"{label} enabled."
        elif val == "off":
            m._PREFS[key] = False
            if key == _A11Y_PLAIN_MODE and m._effective_layout_mode() == "plain":
                m._PREFS["layout"] = "normal"
            _save_prefs()
            return f"{label} disabled."
        else:
            state = "ON" if m._PREFS.get(key, False) else "off"
            return f"  {label}: {_B}{state}{_R}. Use on|off to change."

    if sub in ("status", ""):
        try:
            import shutil as _shutil
            cols = _shutil.get_terminal_size(fallback=(80, 24)).columns
        except (OSError, AttributeError, ValueError):  # noqa: BLE001
            cols = 80

        rm   = "ON" if m._a11y_reduced_motion() else "off"
        pm   = "ON" if m._a11y_plain_mode()     else "off"
        hc   = "ON" if m._a11y_high_contrast()  else "off"
        layout = m._effective_layout_mode()
        preset = m._layout_preset_name() or "single-pane"
        preset_fallback = m._layout_preset_fallback(width=cols, is_tty=m._IS_TTY)
        rich = "yes" if m._RICH_AVAILABLE else "no"
        tty  = "yes" if m._IS_TTY else "no"

        if m._RICH_AVAILABLE and m._IS_TTY:
            lines = m._RichText()
            lines.append(f"  Reduced motion:   {rm}\n",   style="bold" if rm == "ON" else "dim")
            lines.append(f"  Plain mode:       {pm}\n",   style="bold" if pm == "ON" else "dim")
            lines.append(f"  High contrast:    {hc}\n",   style="bold" if hc == "ON" else "dim")
            lines.append(f"  Layout mode:      {layout}\n", style="dim")
            lines.append(f"  Layout preset:    {preset}\n", style="dim")
            lines.append(f"  Preset fallback:  {preset_fallback}\n", style="dim")
            lines.append(f"  Rich available:   {rich}\n", style="dim")
            lines.append(f"  TTY detected:     {tty}\n",  style="dim")
            lines.append(f"  Terminal width:   {cols} columns", style="dim")
            m._RICH_CONSOLE.print(m._RichPanel(lines, title=f"{m._e('♿', '[a11y]')} Accessibility Status", border_style="cyan"))
        else:
            print(f"{m._e('♿', '[a11y]')} Accessibility Status")
            print(f"  Reduced motion:   {rm}")
            print(f"  Plain mode:       {pm}")
            print(f"  High contrast:    {hc}")
            print(f"  Layout mode:      {layout}")
            print(f"  Layout preset:    {preset}")
            print(f"  Preset fallback:  {preset_fallback}")
            print(f"  Rich available:   {rich}")
            print(f"  TTY detected:     {tty}")
            print(f"  Terminal width:   {cols} columns")
        return _CMD_CONTINUE

    if sub == "reduced-motion":
        message = _on_off(val, _A11Y_REDUCED_MOTION, "Reduced motion")
        m._print_feedback(message, level="success" if val == "on" else ("warn" if val == "off" else "info"))
        return _CMD_CONTINUE

    if sub == "plain":
        message = _on_off(val, _A11Y_PLAIN_MODE, "Plain mode")
        m._print_feedback(message, level="success" if val == "on" else ("warn" if val == "off" else "info"))
        return _CMD_CONTINUE

    if sub == "high-contrast":
        message = _on_off(val, _A11Y_HIGH_CONTRAST, "High contrast")
        m._print_feedback(message, level="success" if val == "on" else ("warn" if val == "off" else "info"))
        return _CMD_CONTINUE

    if sub == "reset":
        for key in (_A11Y_REDUCED_MOTION, _A11Y_PLAIN_MODE, _A11Y_HIGH_CONTRAST):
            m._PREFS.pop(key, None)
        if m._effective_layout_mode() == "plain":
            m._PREFS["layout"] = "normal"
        _save_prefs()
        m._print_feedback("Accessibility modes reset to defaults.", level="success")
        return _CMD_CONTINUE

    print("  Usage: /accessibility [status|reduced-motion|plain|high-contrast|reset] [on|off]")
    return _CMD_CONTINUE


def _cmd_keybind(ctx: "ChatCommandContext") -> str:
    """/keybind [key action | list | clear <key>] — manage custom readline key bindings.

    Examples:
      /keybind list                    — show all custom bindings
      /keybind Ctrl+H /histsearch      — bind Ctrl+H to /histsearch
      /keybind Ctrl+T /top             — bind Ctrl+T to /top
      /keybind clear Ctrl+H            — remove a binding
    """
    m = _m()
    arg = ctx.args.strip()
    is_tty = m._get_is_tty()

    if not arg or arg == "list":
        custom = m._PREFS.get("custom_keybinds", {})
        if not custom:
            msg = "No custom keybinds. Try: /keybind Ctrl+H /histsearch"
            if m._RICH_AVAILABLE and is_tty:
                m._RICH_CONSOLE.print(f"[dim]{msg}[/]")
            else:
                print(msg)
            return _CMD_CONTINUE

        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print("\n[bold cyan]⌨️  Custom Keybinds[/]\n")
            for key, action in custom.items():
                m._RICH_CONSOLE.print(f"  [bold yellow]{key:<16}[/] → [bold green]{action}[/]")
            m._RICH_CONSOLE.print()
        else:
            print("\n⌨️  Custom Keybinds\n")
            for key, action in custom.items():
                print(f"  {_BYE}{key:<16}{_R} → {_BGR}{action}{_R}")
            print()
        return _CMD_CONTINUE

    parts = arg.split(None, 1)
    if parts[0] == "clear" and len(parts) > 1:
        key_name = parts[1].strip()
        custom = m._PREFS.get("custom_keybinds", {})
        if key_name in custom:
            del custom[key_name]
            m._prefs_set("custom_keybinds", custom)
            if m._RICH_AVAILABLE and is_tty:
                m._RICH_CONSOLE.print(f"[green]✓[/] Removed keybind for [bold]{key_name}[/]")
            else:
                print(f"✓ Removed keybind for {key_name}")
        else:
            if m._RICH_AVAILABLE and is_tty:
                m._RICH_CONSOLE.print(f"[yellow]No keybind for '{key_name}'[/]")
            else:
                print(f"No keybind for '{key_name}'")
        return _CMD_CONTINUE

    if len(parts) < 2:
        msg = "Usage: /keybind <Key> <action>  e.g. /keybind Ctrl+H /histsearch"
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    key_name = parts[0]
    action = parts[1].strip()

    if not (key_name.startswith("Ctrl+") or key_name.startswith("Alt+")):
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print("[yellow]Key must start with Ctrl+ or Alt+ (e.g. Ctrl+H)[/]")
        else:
            print("Key must start with Ctrl+ or Alt+ (e.g. Ctrl+H)")
        return _CMD_CONTINUE

    if not action.startswith("/"):
        if m._RICH_AVAILABLE and is_tty:
            m._RICH_CONSOLE.print("[yellow]Action must be a slash command (e.g. /histsearch)[/]")
        else:
            print("Action must be a slash command")
        return _CMD_CONTINUE

    custom = m._PREFS.get("custom_keybinds", {})
    custom[key_name] = action
    m._prefs_set("custom_keybinds", custom)

    m._apply_custom_keybind(key_name, action)

    if m._RICH_AVAILABLE and is_tty:
        m._RICH_CONSOLE.print(f"[green]✓[/] Bound [bold yellow]{key_name}[/] → [bold green]{action}[/]")
    else:
        print(f"✓ Bound {key_name} → {action}")

    return _CMD_CONTINUE
