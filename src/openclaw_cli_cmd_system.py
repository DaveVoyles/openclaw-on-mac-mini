"""
openclaw_cli_cmd_system.py — System, prompt, and display configuration handlers.

Extracted from openclaw_cli.py (TD-34b).
Handlers: _cmd_system, _cmd_promptdebug, _cmd_autobold, _cmd_jsonformat,
          _cmd_separator, _cmd_palette, _cmd_prompt, _cmd_alias,
          _cmd_pathhints, _cmd_ratehint, _cmd_benchmark
"""
from __future__ import annotations

import os
import socket
import time
from typing import TYPE_CHECKING, Any

from openclaw_cli_ui_core import (
    _get_is_tty,
    _R, _DM, _CY, _GR, _YE, _RE,
)

if TYPE_CHECKING:
    from openclaw_cli_types import ChatCommandContext

_CMD_CONTINUE: str = "continue"  # matches openclaw_cli._CMD_CONTINUE

try:
    from rich.console import Console as _RichConsole
    from rich.panel import Panel as _RichPanel
    from rich.table import Table as _RichTable
    _RICH_CONSOLE = _RichConsole()
    _RICH_AVAILABLE = True
except ImportError:
    _RichConsole = None  # type: ignore[assignment, misc]
    _RichPanel = None  # type: ignore[assignment]
    _RichTable = None  # type: ignore[assignment]
    _RICH_CONSOLE = None
    _RICH_AVAILABLE = False


def _m() -> Any:
    """Lazy import of openclaw_cli — avoids circular import, respects test monkeypatching."""
    import openclaw_cli as _cli  # noqa: PLC0415
    return _cli


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _cmd_system(ctx: "ChatCommandContext") -> str:
    """View or set a persistent system prompt prefix for all AI messages."""
    is_tty = _get_is_tty()
    args = ctx.args.strip()
    parts = args.split(None, 1)
    sub = parts[0].lower() if parts else "view"
    rest = parts[1] if len(parts) > 1 else ""
    cli = _m()
    _PREFS = cli._PREFS
    _SYSTEM_PROMPT_MAX = cli._SYSTEM_PROMPT_MAX

    if sub in ("view", "") or not args:
        current = _PREFS.get("system_prompt", "").strip()
        if _RICH_AVAILABLE and is_tty:
            if current:
                _RICH_CONSOLE.print(_RichPanel(current, title="🔧 System Prompt", border_style="cyan", padding=(0, 1)))
            else:
                _RICH_CONSOLE.print(_RichPanel(f"[dim](not set)[/]", title="🔧 System Prompt", border_style="dim", padding=(0, 1)))
        else:
            if current:
                print(f"System prompt:\n  {current}")
            else:
                print(f"System prompt: (not set)")
        return _CMD_CONTINUE

    if sub == "clear":
        cli._prefs_set("system_prompt", "")
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print("[green]✓ System prompt cleared.[/]")
        else:
            print("✓ System prompt cleared.")
        return _CMD_CONTINUE

    if sub == "set":
        if not rest.strip():
            cli._print_error("Usage: /system set <text>")
            return _CMD_CONTINUE
        if len(rest) > _SYSTEM_PROMPT_MAX:
            cli._print_error("System prompt too long (max 2000 chars)")
            return _CMD_CONTINUE
        cli._prefs_set("system_prompt", rest)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓ System prompt set ({len(rest)} chars).[/]")
        else:
            print(f"✓ System prompt set ({len(rest)} chars).")
        return _CMD_CONTINUE

    if sub == "append":
        if not rest.strip():
            cli._print_error("Usage: /system append <text>")
            return _CMD_CONTINUE
        current = _PREFS.get("system_prompt", "")
        new_prompt = (current + "\n" + rest).strip() if current.strip() else rest
        if len(new_prompt) > _SYSTEM_PROMPT_MAX:
            cli._print_error("System prompt too long (max 2000 chars)")
            return _CMD_CONTINUE
        cli._prefs_set("system_prompt", new_prompt)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓ System prompt updated ({len(new_prompt)} chars).[/]")
        else:
            print(f"✓ System prompt updated ({len(new_prompt)} chars).")
        return _CMD_CONTINUE

    cli._print_error(f"Unknown sub-command '{sub}'. Use: view, set <text>, append <text>, clear")
    return _CMD_CONTINUE


def _cmd_promptdebug(ctx: "ChatCommandContext") -> str:
    """/promptdebug — preview what would be sent to the AI for the next message."""
    is_tty = _get_is_tty()
    cli = _m()
    sys_prompt = cli._PREFS.get("system_prompt", "").strip()
    inj = vars(cli).get("_next_inject", "").strip()

    parts = []
    if sys_prompt:
        parts.append(f"[System context]\n{sys_prompt}")
    if inj:
        parts.append(f"[Injected context]\n{inj}")
    parts.append("[User message]\n(your next message here)")

    preview = "\n\n".join(parts)

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(_RichPanel(preview, title="[bold]📤 Next message preview[/]", border_style="dim", padding=(0, 1)))
    else:
        print("\n📤 Next message preview:\n")
        print(preview)
    return _CMD_CONTINUE


def _cmd_autobold(ctx: "ChatCommandContext") -> str:
    """/autobold [on|off] — toggle automatic bolding of numbers and filenames in responses."""
    return _m()._handle_simple_toggle_pref(ctx, "auto_bold", "auto-bold")


def _cmd_jsonformat(ctx: "ChatCommandContext") -> str:
    """/jsonformat [on|off] — toggle automatic JSON detection and pretty-printing in responses."""
    return _m()._handle_simple_toggle_pref(ctx, "json_autoformat", "JSON auto-format")


def _cmd_separator(ctx: "ChatCommandContext") -> str:
    """/separator [style] — set or preview response separator style (gradient|pulse|dots|wave|none)."""
    arg = ctx.args.strip().lower()
    cli = _m()
    valid = list(cli._SEPARATOR_STYLES.keys())
    is_tty = _get_is_tty()

    if arg in valid:
        cli._prefs_set("separator_style", arg)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓[/] separator style: [bold]{arg}[/]")
        else:
            print(f"✓ separator style: {arg}")
        if arg != "none":
            cli._print_animated_separator()
    elif arg:
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]Unknown style '{arg}'[/] — valid: {', '.join(valid)}")
        else:
            print(f"Unknown style '{arg}' — valid: {', '.join(valid)}")
    else:
        current = cli._PREFS.get("separator_style", "gradient")
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]separator style: [bold]{current}[/] — /separator gradient|pulse|dots|wave|none[/]")
        else:
            print(f"separator style: {current} — /separator gradient|pulse|dots|wave|none")
    return _CMD_CONTINUE


def _cmd_palette(ctx: "ChatCommandContext") -> str:
    """/palette [query] — search slash commands by keyword (fuzzy)."""
    query = ctx.args.strip().lower()
    is_tty = _get_is_tty()
    cli = _m()

    commands = list(cli._get_cmd_registry().list_commands())

    if query:
        matches = [
            cmd for cmd in commands
            if query in cmd.name.lower() or
               (cmd.description and query in cmd.description.lower())
        ]
    else:
        matches = commands

    if not matches:
        msg = f"No commands matching '{query}'"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    matches.sort(key=lambda c: c.name)

    if _RICH_AVAILABLE and is_tty:
        from rich.table import Table
        from rich.box import SIMPLE
        tbl = Table(box=SIMPLE, show_header=True, header_style="bold cyan")
        tbl.add_column("Command", style="bold green", no_wrap=True)
        tbl.add_column("Description", style="default")
        for cmd in matches:
            tbl.add_row(f"/{cmd.name}", cmd.description or "")
        _RICH_CONSOLE.print(
            f"\n[bold cyan]🎯 Command Palette[/] "
            f"[dim]({len(matches)} match{'es' if len(matches) != 1 else ''})[/]\n"
        )
        _RICH_CONSOLE.print(tbl)
    else:
        print(f"\n🎯 Command Palette ({len(matches)} matches)")
        print(f"{'Command':<22} Description")
        print("─" * 60)
        for cmd in matches:
            print(f"  /{cmd.name:<20} {cmd.description or ''}")

    return _CMD_CONTINUE


def _cmd_prompt(ctx: "ChatCommandContext") -> str:
    """/prompt [format] — customize the REPL prompt. Use {route}, {session}, {model}, {build}, {time}.

    Examples:
      /prompt {route} openclaw>
      /prompt openclaw [{time}]>
      /prompt {build} ❯
      /prompt reset          (restore default)
    """
    arg = ctx.args.strip()
    is_tty = _get_is_tty()
    cli = _m()

    if not arg:
        current = cli._PREFS.get("prompt_format", cli._DEFAULT_PROMPT_FORMAT)
        preview = cli._render_prompt_format(current)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"\n[bold cyan]Current prompt format:[/]")
            _RICH_CONSOLE.print(f"  Format:  [dim]{current}[/]")
            _RICH_CONSOLE.print(f"  Preview: [bold]{preview}[/]")
            _RICH_CONSOLE.print(f"\n[dim]Tokens: {{route}} {{session}} {{model}} {{build}} {{time}}[/]")
            _RICH_CONSOLE.print(f"[dim]Use /prompt reset to restore default[/]\n")
        else:
            print(f"\nCurrent: {current}")
            print(f"Preview: {preview}")
            print(f"Tokens: {{route}} {{session}} {{model}} {{build}} {{time}}")
        return _CMD_CONTINUE

    if arg == "reset":
        cli._prefs_set("prompt_format", cli._DEFAULT_PROMPT_FORMAT)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓[/] prompt format reset to default")
        else:
            print("✓ prompt format reset")
        return _CMD_CONTINUE

    if len(arg) < 2:
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]Prompt format too short[/]")
        else:
            print("Prompt format too short")
        return _CMD_CONTINUE

    cli._prefs_set("prompt_format", arg)
    preview = cli._render_prompt_format(arg)
    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"[green]✓[/] prompt format updated")
        _RICH_CONSOLE.print(f"  Preview: [bold]{preview}[/]")
    else:
        print(f"✓ prompt format: {preview}")
    return _CMD_CONTINUE


def _cmd_alias(ctx: "ChatCommandContext") -> str:
    """Define, list, or remove command aliases."""
    args = (ctx.args or "").strip()
    cli = _m()
    aliases: "dict[str, str]" = cli._PREFS.setdefault("aliases", {})
    is_tty = _get_is_tty()

    if not args:
        # List all aliases
        if _RICH_AVAILABLE and is_tty:
            grid = _RichTable.grid(padding=(0, 2))
            grid.add_column(style="cyan", no_wrap=True)
            grid.add_column(style="dim")
            if aliases:
                for name, expansion in sorted(aliases.items()):
                    grid.add_row(name, expansion)
            else:
                grid.add_row("(no aliases defined)", "")
            _RICH_CONSOLE.print(_RichPanel(grid, title="Aliases", border_style="cyan", padding=(0, 1)))
        else:
            print("Aliases:")
            if aliases:
                for name, expansion in sorted(aliases.items()):
                    print(f"  {_CY}{name}{_R} → {_DM}{expansion}{_R}")
            else:
                print(f"  {_DM}(no aliases defined){_R}")
        return _CMD_CONTINUE

    parts = args.split(None, 1)
    sub = parts[0].lower()

    if sub == "rm":
        # Remove alias
        target = parts[1].strip().lstrip("/").lower() if len(parts) > 1 else ""
        if not target:
            cli._print_error("Usage: /alias rm <name>")
            return _CMD_CONTINUE
        if target not in aliases:
            cli._print_error(f"Alias '{target}' not found.")
            return _CMD_CONTINUE
        del aliases[target]
        cli._save_prefs()
        print(f"  {_GR}{cli._e('✅', '[OK]')} Alias '{target}' removed.{_R}")
        return _CMD_CONTINUE

    # Define alias: /alias <name> <expansion>
    name = sub.lstrip("/")
    expansion = parts[1].strip() if len(parts) > 1 else ""

    if not expansion:
        cli._print_error("Usage: /alias <name> <expansion>")
        return _CMD_CONTINUE
    if name in ("alias", "rm"):
        cli._print_error(f"'{name}' is reserved and cannot be used as an alias name.")
        return _CMD_CONTINUE
    if name in cli._BUILTIN_COMMAND_NAMES:
        cli._print_error(f"'{name}' is a built-in command name and cannot be used as an alias.")
        return _CMD_CONTINUE
    if len(aliases) >= cli._MAX_ALIASES and name not in aliases:
        cli._print_error(f"Maximum of {cli._MAX_ALIASES} aliases reached. Remove one first with /alias rm <name>.")
        return _CMD_CONTINUE

    aliases[name] = expansion
    cli._save_prefs()
    print(f"  {_GR}{cli._e('✅', '[OK]')} Alias '{_CY}{name}{_R}{_GR}' → {_DM}{expansion}{_R}{_GR} defined.{_R}")
    return _CMD_CONTINUE


def _cmd_pathhints(ctx: "ChatCommandContext") -> str:
    """/pathhints [on|off] — toggle file path quick-action hints after responses."""
    return _m()._handle_simple_toggle_pref(ctx, "path_hints", "path hints")


def _cmd_ratehint(ctx: "ChatCommandContext") -> str:
    """/ratehint [on|off] — toggle the post-response rating hint."""
    return _m()._handle_simple_toggle_pref(ctx, "show_rate_hint", "rating hint", note="/ratehint on|off")


def _cmd_benchmark(ctx: "ChatCommandContext") -> str:
    """/benchmark [n] — run n quick AI pings to measure response latency (default: 3)."""
    arg = ctx.args.strip()
    n = int(arg) if arg.isdigit() else 3
    n = min(max(n, 1), 10)
    is_tty = _get_is_tty()

    # Resolve server URL from config or env fallback.
    if ctx.config and getattr(ctx.config, "base_url", None):
        server_url = ctx.config.base_url.rstrip("/")
    else:
        server_url = os.getenv("OPENCLAW_URL", "http://192.168.1.93:8765").rstrip("/")

    host_part = server_url.replace("https://", "").replace("http://", "")
    host = host_part.split(":")[0]
    try:
        port = int(host_part.split(":")[1]) if ":" in host_part else 8765
    except (IndexError, ValueError):
        port = 8765

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]⏱️  Benchmark[/] [dim]({n} TCP pings → {host}:{port})[/]\n")
    else:
        print(f"\n⏱️  Benchmark ({n} pings → {host}:{port})\n")

    times: list[float] = []
    for i in range(n):
        start = time.time()
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
            elapsed = time.time() - start
            times.append(elapsed)

            bar_len = min(int(elapsed * 20), 40)
            bar_color = _RE if elapsed > 3 else _YE if elapsed > 1.5 else _GR
            bar = f"{bar_color}{'█' * bar_len}{_R}"

            if _RICH_AVAILABLE and is_tty:
                color = "red" if elapsed > 3 else "yellow" if elapsed > 1.5 else "green"
                _RICH_CONSOLE.print(f"  [{i + 1}/{n}] [{color}]{elapsed:.3f}s[/]  {bar}")
            else:
                print(f"  [{i + 1}/{n}] {elapsed:.3f}s  {bar}")
        except Exception as exc:  # noqa: BLE001
            elapsed = time.time() - start
            times.append(elapsed)
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"  [{i + 1}/{n}] [red]Error: {exc}[/]")
            else:
                print(f"  [{i + 1}/{n}] Error: {exc}")

    if times:
        avg = sum(times) / len(times)
        mn = min(times)
        mx = max(times)
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(
                f"\n  [dim]Min:[/] [bold]{mn:.3f}s[/]  "
                f"[dim]Avg:[/] [bold]{avg:.3f}s[/]  "
                f"[dim]Max:[/] [bold]{mx:.3f}s[/]"
            )
            quality = "🟢 Fast" if avg < 1.5 else "🟡 Moderate" if avg < 3 else "🔴 Slow"
            _RICH_CONSOLE.print(f"  [dim]Quality:[/] {quality}\n")
        else:
            print(f"\n  Min: {mn:.3f}s  Avg: {avg:.3f}s  Max: {mx:.3f}s")
            quality = "Fast" if avg < 1.5 else "Moderate" if avg < 3 else "Slow"
            print(f"  Quality: {quality}\n")

    return _CMD_CONTINUE
