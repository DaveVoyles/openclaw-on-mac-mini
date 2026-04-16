"""
openclaw_cli_cmd_misc.py — Miscellaneous UX, history, and analytics command handlers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openclaw_cli_types import ChatCommandContext


def _get_cli_mod():
    """Lazy import of main module for monkeypatch-safe back-references."""
    import openclaw_cli as _m
    return _m


from openclaw_cli_ui_core import (
    _B,
    _BCY,
    _BGR,
    _BYE,
    _CY,
    _DM,
    _GR,
    _R,
    _RE,
    _YE,
    _get_is_tty,
)

_CMD_CONTINUE: str = "continue"  # matches openclaw_cli._CMD_CONTINUE


def _print_error(msg: str, **kw: object) -> None:
    """Route through main module so monkeypatching works in tests."""
    _get_cli_mod()._print_error(msg, **kw)

# _YL is used in _cmd_rate (score==3 branch) — alias to yellow
_YL = _YE

try:
    from rich.console import Console as _RichConsole
    from rich.panel import Panel as _RichPanel
    from rich.text import Text as _RichText
    _RICH_CONSOLE = _RichConsole()
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_CONSOLE = None
    _RichPanel = None
    _RichText = None
    _RICH_AVAILABLE = False

_IS_TTY = _get_is_tty()


# ---------------------------------------------------------------------------
# _cmd_recall
# ---------------------------------------------------------------------------

def _cmd_recall(ctx: ChatCommandContext) -> str:
    """/recall <n> — re-inject the nth most recent prompt into the chat (1=most recent)."""
    arg = (ctx.args or "").strip()
    is_tty = _get_is_tty()
    _cli = _get_cli_mod()

    cmd_history = _cli._PREFS.get("cmd_history", [])
    prompts: list[str] = []
    for entry in reversed(cmd_history):
        if isinstance(entry, dict):
            text = entry.get("text", entry.get("prompt", entry.get("cmd", "")))
        else:
            text = str(entry)
        if text and not text.startswith("/"):
            prompts.append(text)

    if not arg or not arg.isdigit():
        if not prompts:
            msg = "No prompt history yet."
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[dim]{msg}[/]")
            else:
                print(msg)
            return _CMD_CONTINUE

        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print("\n[bold cyan]📜 Recent Prompts[/]")
            for i, p in enumerate(prompts[:10], 1):
                preview = p[:70] + "…" if len(p) > 70 else p
                _RICH_CONSOLE.print(f"  [dim]{i:>2}.[/] [default]{preview}[/]")
            _RICH_CONSOLE.print("\n[dim]Use /recall <n> to re-send prompt #n[/]\n")
        else:
            print("\n📜 Recent Prompts")
            for i, p in enumerate(prompts[:10], 1):
                preview = p[:70] + "…" if len(p) > 70 else p
                print(f"  {i:>2}. {preview}")
            print("\n  Use /recall <n> to re-send prompt #n\n")
        return _CMD_CONTINUE

    n = int(arg)
    if n < 1 or n > len(prompts):
        msg = f"No prompt #{n} — history has {len(prompts)} entries."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    recalled = prompts[n - 1]
    _cli._next_inject = recalled

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"[dim]↩  Recalling:[/] [italic]{recalled[:80]}[/]")
    else:
        print(f"  ↩  Recalling: {recalled[:80]}")

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_histsearch
# ---------------------------------------------------------------------------

def _cmd_histsearch(ctx: ChatCommandContext) -> str:
    """/histsearch <query> — search prompt history for matching entries."""
    query = ctx.args.strip().lower()
    is_tty = _get_is_tty()

    if not query:
        msg = "Usage: /histsearch <query>"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    cmd_history = _get_cli_mod()._PREFS.get("cmd_history", [])

    matches = []
    for i, entry in enumerate(reversed(cmd_history)):
        if isinstance(entry, dict):
            text = entry.get("text", entry.get("prompt", entry.get("cmd", "")))
            ts = entry.get("timestamp", entry.get("ts", ""))
        else:
            text = str(entry)
            ts = ""

        if query in text.lower():
            matches.append((len(cmd_history) - i, text, ts))

    if not matches:
        msg = f"No history matches for '{query}'"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]🔍 History Search:[/] [italic]\"{query}\"[/] [dim]({len(matches)} match{'es' if len(matches)!=1 else ''})[/]\n")
        for idx, text, ts in matches[:20]:
            preview = text[:80] + "…" if len(text) > 80 else text
            highlighted = preview.replace(query, f"[bold yellow]{query}[/]")
            rel = ""
            if ts:
                try:
                    import datetime
                    dt = datetime.datetime.fromisoformat(ts)
                    diff = int((datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - dt).total_seconds())
                    rel = f"[dim] ({diff//3600}h ago)[/]" if diff >= 3600 else f"[dim] ({diff//60}m ago)[/]"
                except (ValueError, TypeError, AttributeError):  # noqa: BLE001
                    pass
            _RICH_CONSOLE.print(f"  [dim]#{idx:<4}[/] {highlighted}{rel}")
        _RICH_CONSOLE.print()
    else:
        print(f"\n🔍 History: \"{query}\" ({len(matches)} matches)\n")
        for idx, text, ts in matches[:20]:
            preview = text[:75] + "…" if len(text) > 75 else text
            highlighted = preview.replace(query, query.upper())
            print(f"  #{idx:<4} {highlighted}")
        print()

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_celebrate
# ---------------------------------------------------------------------------

def _cmd_celebrate(ctx: ChatCommandContext) -> str:
    """/celebrate — trigger a celebration animation (just for fun!)."""
    msg = ctx.args.strip() or "Woohoo! 🎉"
    _get_cli_mod()._celebration_burst(msg)
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _print_ascii_trophy (shared helper)
# ---------------------------------------------------------------------------

def _print_ascii_trophy(streak: int) -> None:
    """Print an ASCII trophy for streak achievements."""
    is_tty = _get_is_tty()
    if _get_cli_mod()._a11y_plain_mode():
        print(f"  🏆 {streak}-rating streak!")
        return

    trophy = [
        f"  {_YE}  ___  {_R}",
        f"  {_YE} /   \\ {_R}",
        f"  {_YE}|     |{_R}",
        f"  {_YE} \\   / {_R}",
        f"  {_YE}  | |  {_R}",
        f"  {_YE} _|_|_ {_R}",
        f"  {_YE}|_____|{_R}",
    ]

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold yellow]🏆 {streak}-Rating Streak! Amazing![/]\n")
        for line in trophy:
            _RICH_CONSOLE.print(line)
        _RICH_CONSOLE.print()
    else:
        print(f"\n  🏆 {streak}-Rating Streak! Amazing!\n")
        for line in trophy:
            print(line)
        print()


# ---------------------------------------------------------------------------
# _cmd_rate
# ---------------------------------------------------------------------------

def _cmd_rate(ctx: ChatCommandContext) -> str:
    """Rate the last AI response (/rate [good|ok|bad|meh|1-5])."""
    _cli = _get_cli_mod()
    raw = (ctx.args or "").strip().lower()
    if not raw:
        _print_error("Usage: /rate [good|ok|bad|meh|1-5]")
        return _CMD_CONTINUE

    _RATING_MAP = {
        "good": (5, "good"),
        "5":    (5, "good"),
        "4":    (4, "great"),
        "ok":   (3, "ok"),
        "meh":  (3, "ok"),
        "3":    (3, "ok"),
        "2":    (2, "poor"),
        "bad":  (1, "bad"),
        "1":    (1, "bad"),
    }
    if raw not in _RATING_MAP:
        _print_error("Unknown rating — use good, ok, bad, or 1-5")
        return _CMD_CONTINUE

    score, label = _RATING_MAP[raw]

    if not _cli._last_response_text:
        _print_error("Nothing to rate — no response yet")
        return _CMD_CONTINUE

    ts = datetime.now(timezone.utc).isoformat()
    ratings = _cli._PREFS.setdefault("ratings", [])
    rating_entry: dict[str, Any] = {"score": score, "label": label, "ts": ts}
    if ctx.session_id:
        snapshot = _cli._last_trace_snapshot(ctx.session_id)
        if snapshot:
            route = str(snapshot.get("slash_cmd") or "").strip().lstrip("/")
            if route:
                rating_entry["route"] = route
            conf_label = str(snapshot.get("conf_label") or "").strip()
            if conf_label:
                rating_entry["route_confidence"] = conf_label
    ratings.append(rating_entry)
    if len(ratings) > 500:
        _cli._PREFS["ratings"] = ratings[-500:]
    _cli._save_prefs()

    if ctx.session_id:
        try:
            _cli.append_event(
                session_id=ctx.session_id,
                kind="rating",
                content=f"rated: {label} ({score}/5)",
                metadata={"score": score, "label": label},
            )
        except (AttributeError, OSError, TypeError):
            _cli._LOG.debug("append_event for rating failed", exc_info=True)
            pass

    _STARS = {5: "⭐⭐⭐⭐⭐", 4: "⭐⭐⭐⭐", 3: "⭐⭐⭐", 2: "⭐⭐", 1: "⭐"}
    stars = _STARS[score]
    msg = f"{stars} Rated: {label}"
    if score >= 4:
        color = _GR
    elif score == 3:
        color = _YL
    else:
        color = _DM
    print(f"{color}{msg}{_R}")
    if score == 5:
        _cli._celebration_burst("5-star rating — thanks! 🎉")

    if score >= 4:
        ratings_list = _cli._PREFS.get("ratings", [])
        streak = 0
        for r in reversed(ratings_list):
            s = r.get("score", 0) if isinstance(r, dict) else 0
            if s >= 4:
                streak += 1
            else:
                break
        if streak in (5, 10, 20, 50):
            _print_ascii_trophy(streak)

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_streak
# ---------------------------------------------------------------------------

def _cmd_streak(ctx: ChatCommandContext) -> str:
    """/streak — show your current rating streak and all-time best."""
    is_tty = _get_is_tty()
    ratings = _get_cli_mod()._PREFS.get("ratings", [])

    if not ratings:
        msg = "No ratings yet. Use /rate 1-5 after responses!"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    current_streak = 0
    for r in reversed(ratings):
        if isinstance(r, dict):
            score = r.get("score", r.get("rating", 0))
        else:
            try:
                score = int(r)
            except (ValueError, TypeError):
                score = 0
        if score >= 4:
            current_streak += 1
        else:
            break

    best_streak = 0
    running = 0
    for r in ratings:
        if isinstance(r, dict):
            score = r.get("score", r.get("rating", 0))
        else:
            try:
                score = int(r)
            except (ValueError, TypeError):
                score = 0
        if score >= 4:
            running += 1
            best_streak = max(best_streak, running)
        else:
            running = 0

    total = len(ratings)
    high_pct = int(sum(1 for r in ratings if (r.get("score", 0) if isinstance(r, dict) else 0) >= 4) / max(1, total) * 100)

    if _RICH_AVAILABLE and is_tty:
        streak_color = "green" if current_streak >= 5 else "yellow" if current_streak >= 2 else "default"
        _RICH_CONSOLE.print("\n[bold cyan]🔥 Rating Streak[/]\n")
        _RICH_CONSOLE.print(f"  Current streak:  [{streak_color}]{current_streak} high ratings[/]  {'🔥' * min(current_streak, 10)}")
        _RICH_CONSOLE.print(f"  Best streak:     [bold]{best_streak}[/]")
        _RICH_CONSOLE.print(f"  High rate (4+):  [bold]{high_pct}%[/] of {total} ratings")
        _RICH_CONSOLE.print()
    else:
        fire = "🔥" * min(current_streak, 10)
        print("\n🔥 Rating Streak\n")
        print(f"  Current streak:  {current_streak} high ratings  {fire}")
        print(f"  Best streak:     {best_streak}")
        print(f"  High rate (4+):  {high_pct}% of {total} ratings\n")

    if current_streak >= 5:
        _print_ascii_trophy(current_streak)

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_heatmap
# ---------------------------------------------------------------------------

def _cmd_heatmap(ctx: ChatCommandContext) -> str:
    """/heatmap — show a color-coded hourly activity heatmap of openclaw usage."""
    import datetime
    is_tty = _get_is_tty()

    cmd_history = _get_cli_mod()._PREFS.get("cmd_history", [])

    hour_counts: dict[int, int] = {h: 0 for h in range(24)}
    day_counts: dict[int, int] = {d: 0 for d in range(7)}

    for entry in cmd_history:
        if isinstance(entry, dict):
            ts_str = entry.get("timestamp", entry.get("ts", ""))
        else:
            continue
        if not ts_str:
            continue
        try:
            ts = datetime.datetime.fromisoformat(ts_str)
            hour_counts[ts.hour] = hour_counts.get(ts.hour, 0) + 1
            day_counts[ts.weekday()] = day_counts.get(ts.weekday(), 0) + 1
        except (ValueError, AttributeError):
            continue

    total = sum(hour_counts.values())

    if total == 0:
        msg = "No timestamped history yet — use openclaw for a while to see your heatmap!"
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    max_hour = max(hour_counts.values()) or 1

    def _heat_color(count: int, max_count: int) -> str:
        if count == 0:
            return _DM
        ratio = count / max_count
        if ratio > 0.75:
            return _RE
        elif ratio > 0.5:
            return _YE
        elif ratio > 0.25:
            return _GR
        else:
            return _CY

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]🕐 Hourly Activity Heatmap[/] [dim]({total} events)[/]\n")
    else:
        print(f"\n{_B}🕐 Hourly Activity Heatmap{_R} {_DM}({total} events){_R}\n")

    hour_header = "  "
    for h in range(0, 24, 2):
        hour_header += f"{_DM}{h:02d}{_R}  "
    print(hour_header)

    heat_row = "  "
    for h in range(24):
        count = hour_counts[h]
        color = _heat_color(count, max_hour)
        block = "██" if count > 0 else "░░"
        heat_row += f"{color}{block}{_R} "
    print(heat_row)

    count_row = "  "
    for h in range(24):
        count = hour_counts[h]
        count_row += f"{_DM}{count:>2}{_R} "
    print(count_row)

    peak_hour = max(hour_counts, key=hour_counts.get)
    peak_count = hour_counts[peak_hour]

    print(f"\n  {_DM}Peak hour: {_B}{peak_hour:02d}:00{_R} {_DM}({peak_count} events)  ·  "
          f"Legend: {_RE}██{_R}=hot  {_YE}██{_R}=warm  {_GR}██{_R}=mild  {_CY}██{_R}=cool  {_DM}░░=none{_R}\n")

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_followup
# ---------------------------------------------------------------------------

def _cmd_followup(ctx: ChatCommandContext) -> str:
    """/followup [on|off] — show contextually relevant follow-up suggestions for your last prompt, or toggle the auto-suggestion footer."""
    arg = (ctx.args or "").strip().lower()
    _cli = _get_cli_mod()

    if arg in ("on", "off"):
        _cli._PREFS["show_suggestions"] = (arg == "on")
        state = "on" if _cli._PREFS["show_suggestions"] else "off"
        is_tty = _get_is_tty()
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[green]✓[/] follow-up suggestions [bold]{state}[/]")
        else:
            print(f"✓ follow-up suggestions {state}")
        return _CMD_CONTINUE

    last_prompt = str(_cli._PREFS.get("_last_prompt", "") or "")
    if not last_prompt:
        msg = "No recent prompt found. Type a question first, then use /followup."
        is_tty = _get_is_tty()
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    suggestions = _cli._suggest_followups(last_prompt, response_text=_cli._last_response_text, session_id=ctx.session_id)
    is_tty = _get_is_tty()

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(
            f"\n[bold cyan]💡 Follow-up suggestions[/] "
            f"[dim]based on: \"{last_prompt[:50]}{'…' if len(last_prompt) > 50 else ''}\"[/]\n"
        )
        for s in suggestions:
            cmd = s.split(" — ")[0]
            desc = s.split(" — ")[1] if " — " in s else ""
            _RICH_CONSOLE.print(f"  [bold cyan]{cmd}[/]  [dim]{desc}[/]")
        _RICH_CONSOLE.print()
    else:
        print(f"\n💡 Follow-up suggestions (based on: \"{last_prompt[:50]}…\")\n")
        for s in suggestions:
            print(
                f"  {_BCY}{s.split(' — ')[0]}{_R}  "
                f"{_DM}{s.split(' — ')[1] if ' — ' in s else ''}{_R}"
            )
        print()

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_shortcuts
# ---------------------------------------------------------------------------

def _cmd_shortcuts(ctx: ChatCommandContext) -> str:
    """/shortcuts — show keyboard shortcuts and quick-access reference card."""
    is_tty = _get_is_tty()

    sections = [
        ("⌨️  Navigation", [
            ("Tab",          "Auto-complete slash commands"),
            ("↑ / ↓",        "Scroll through command history"),
            ("Ctrl+A",       "Jump to start of line"),
            ("Ctrl+E",       "Jump to end of line"),
            ("Ctrl+W",       "Delete last word"),
            ("Ctrl+U",       "Clear current line"),
        ]),
        ("🔄  Session", [
            ("Ctrl+C",       "Interrupt current response"),
            ("Ctrl+D",       "Exit openclaw"),
            ("/quit",        "Exit gracefully"),
            ("/clear",       "Clear screen"),
        ]),
        ("📋  Quick Commands", [
            ("/last",        "Re-print last response"),
            ("/retry",       "Retry last prompt"),
            ("/draft",       "Edit current draft buffer"),
            ("/history",     "Browse recent prompts"),
            ("/palette",     "Search all commands (new!)"),
        ]),
        ("🎨  Appearance", [
            ("/separator [style]",  "Set response separator style"),
            ("/emojiheaders on|off", "Toggle emoji on headings"),
            ("/autobold on|off",     "Toggle auto-bold in responses"),
            ("/jsonformat on|off",   "Toggle JSON auto-detect & pretty-print"),
            ("/theme",               "Switch color theme"),
        ]),
        ("🔧  Power", [
            ("/macro [name]",   "Run saved macro"),
            ("/pin [key]",      "Pin a value for quick reference"),
            ("/export",         "Export session to file"),
            ("/help",           "Full command reference"),
        ]),
    ]

    if _RICH_AVAILABLE and is_tty:
        from rich.table import Table

        _RICH_CONSOLE.print()
        _RICH_CONSOLE.print(_RichPanel.fit("[bold cyan]⌨️  Keyboard Shortcuts & Quick Reference[/]", border_style="cyan"))
        _RICH_CONSOLE.print()

        for section_title, items in sections:
            tbl = Table(box=None, show_header=False, padding=(0, 2))
            tbl.add_column("Key", style="bold yellow", no_wrap=True, min_width=24)
            tbl.add_column("Action", style="default")
            for key, desc in items:
                tbl.add_row(key, desc)
            _RICH_CONSOLE.print(f"[bold]{section_title}[/]")
            _RICH_CONSOLE.print(tbl)
            _RICH_CONSOLE.print()
    else:
        print("\n⌨️  Keyboard Shortcuts & Quick Reference")
        print("=" * 50)
        for section_title, items in sections:
            print(f"\n{section_title}")
            print("─" * 40)
            for key, desc in items:
                print(f"  {key:<24} {desc}")
        print()

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_top
# ---------------------------------------------------------------------------

def _cmd_top(ctx: ChatCommandContext) -> str:
    """/top [n] — show the n most frequently used prompts and commands (default: 10)."""
    arg = ctx.args.strip()
    n = int(arg) if arg.isdigit() else 10
    n = min(max(n, 1), 50)
    is_tty = _get_is_tty()

    cmd_history = _get_cli_mod()._PREFS.get("cmd_history", [])

    freq: dict = {}
    for entry in cmd_history:
        if isinstance(entry, dict):
            text = entry.get("text", entry.get("prompt", entry.get("cmd", "")))
        else:
            text = str(entry)
        text = text.strip()
        if not text:
            continue
        key = text[:60]
        freq[key] = freq.get(key, 0) + 1

    if not freq:
        msg = "No history yet."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    top = sorted(freq.items(), key=lambda x: -x[1])[:n]
    max_count = top[0][1] if top else 1

    if _RICH_AVAILABLE and is_tty:
        from rich.box import SIMPLE
        from rich.table import Table
        _RICH_CONSOLE.print(f"\n[bold cyan]🔝 Top {len(top)} Most Used[/]\n")
        tbl = Table(box=SIMPLE, show_header=True, header_style="bold cyan")
        tbl.add_column("#", justify="right", style="dim", width=4)
        tbl.add_column("Count", justify="right", style="bold yellow", width=6)
        tbl.add_column("Bar", style="cyan", width=20)
        tbl.add_column("Text", style="default")
        for i, (text, count) in enumerate(top, 1):
            bar_len = int((count / max_count) * 18)
            bar = "█" * bar_len
            preview = text[:55] + "…" if len(text) > 55 else text
            style = "bold green" if text.startswith("/") else "default"
            tbl.add_row(str(i), str(count), bar, f"[{style}]{preview}[/]")
        _RICH_CONSOLE.print(tbl)
        _RICH_CONSOLE.print()
    else:
        print(f"\n🔝 Top {len(top)} Most Used\n")
        for i, (text, count) in enumerate(top, 1):
            bar_len = int((count / max_count) * 20)
            bar = "█" * bar_len
            preview = text[:50] + "…" if len(text) > 50 else text
            print(f"  {i:>3}. {_B}{count:>4}x{_R}  {_CY}{bar:<20}{_R}  {preview}")
        print()

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_freq
# ---------------------------------------------------------------------------

def _cmd_freq(ctx: ChatCommandContext) -> str:
    """/freq — show frequency analysis of slash commands used."""
    is_tty = _get_is_tty()
    cmd_history = _get_cli_mod()._PREFS.get("cmd_history", [])

    slash_freq: dict = {}
    for entry in cmd_history:
        if isinstance(entry, dict):
            text = entry.get("text", entry.get("cmd", ""))
        else:
            text = str(entry)
        text = text.strip()
        if text.startswith("/"):
            cmd_name = text.split()[0]
            slash_freq[cmd_name] = slash_freq.get(cmd_name, 0) + 1

    if not slash_freq:
        msg = "No slash command history yet."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    sorted_cmds = sorted(slash_freq.items(), key=lambda x: -x[1])[:20]
    max_count = sorted_cmds[0][1] if sorted_cmds else 1

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print("\n[bold cyan]📊 Slash Command Frequency[/]\n")
        for cmd, count in sorted_cmds:
            bar_len = int((count / max_count) * 25)
            bar = "█" * bar_len
            _RICH_CONSOLE.print(f"  [bold green]{cmd:<20}[/] [cyan]{bar:<25}[/] [bold yellow]{count}[/]")
        _RICH_CONSOLE.print()
    else:
        print("\n📊 Slash Command Frequency\n")
        for cmd, count in sorted_cmds:
            bar_len = int((count / max_count) * 25)
            bar = "█" * bar_len
            print(f"  {_BGR}{cmd:<20}{_R} {_CY}{bar:<25}{_R} {_BYE}{count}{_R}")
        print()

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_tip
# ---------------------------------------------------------------------------

def _cmd_tip(ctx: ChatCommandContext) -> str:
    """/tip — show a random openclaw usage tip."""
    import random
    is_tty = _get_is_tty()

    tip = random.choice(_get_cli_mod()._OPENCLAW_TIPS)

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print(f"\n[bold cyan]💡 Tip:[/] {tip}\n")
    else:
        print(f"\n{_BCY}💡 Tip:{_R} {tip}\n")

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _print_key_bindings / _cmd_keys
# ---------------------------------------------------------------------------

def _print_key_bindings() -> None:
    """Print currently active readline key bindings summary."""
    is_tty = _get_is_tty()
    bindings = [
        ("Ctrl+R",   "Reverse history search (type to filter)"),
        ("Ctrl+L",   "Clear screen"),
        ("Ctrl+W",   "Delete previous word"),
        ("Ctrl+U",   "Clear current line"),
        ("Ctrl+A",   "Jump to start of line"),
        ("Ctrl+E",   "Jump to end of line"),
        ("Ctrl+C",   "Interrupt / cancel"),
        ("Ctrl+D",   "Exit openclaw"),
        ("Tab",      "Auto-complete slash commands"),
        ("↑ / ↓",    "Browse command history"),
    ]

    if _RICH_AVAILABLE and is_tty:
        from rich.box import SIMPLE
        from rich.table import Table
        _RICH_CONSOLE.print("\n[bold cyan]⌨️  Active Key Bindings[/]\n")
        tbl = Table(box=SIMPLE, show_header=True, header_style="bold cyan")
        tbl.add_column("Key", style="bold yellow", no_wrap=True, width=16)
        tbl.add_column("Action")
        for key, desc in bindings:
            tbl.add_row(key, desc)
        _RICH_CONSOLE.print(tbl)
        _RICH_CONSOLE.print()
    else:
        print("\n⌨️  Active Key Bindings\n")
        for key, desc in bindings:
            print(f"  {_BYE}{key:<16}{_R} {desc}")
        print()


def _cmd_keys(ctx: ChatCommandContext) -> str:
    """/keys — show active keyboard shortcuts and readline bindings."""
    _print_key_bindings()
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_bindlist
# ---------------------------------------------------------------------------

def _cmd_bindlist(ctx: ChatCommandContext) -> str:
    """/bindlist — show all keyboard bindings (built-in readline + custom)."""
    is_tty = _get_is_tty()

    builtin_bindings = [
        ("Ctrl+R",   "Reverse history search"),
        ("Ctrl+L",   "Clear screen"),
        ("Ctrl+W",   "Delete previous word"),
        ("Ctrl+U",   "Clear current line"),
        ("Ctrl+A",   "Jump to line start"),
        ("Ctrl+E",   "Jump to line end"),
        ("Ctrl+C",   "Interrupt"),
        ("Ctrl+D",   "Exit"),
        ("Tab",      "Auto-complete /commands"),
        ("↑ / ↓",    "Browse history"),
    ]

    custom_bindings = list(_get_cli_mod()._PREFS.get("custom_keybinds", {}).items())

    if _RICH_AVAILABLE and is_tty:
        from rich.box import SIMPLE
        from rich.table import Table
        _RICH_CONSOLE.print("\n[bold cyan]⌨️  All Key Bindings[/]\n")

        tbl = Table(box=SIMPLE, show_header=True, header_style="bold cyan")
        tbl.add_column("Key", style="bold yellow", no_wrap=True, width=16)
        tbl.add_column("Action")
        tbl.add_column("Type", style="dim", width=8)

        for key, desc in builtin_bindings:
            tbl.add_row(key, desc, "built-in")

        for key, action in custom_bindings:
            tbl.add_row(key, action, "[green]custom[/]")

        _RICH_CONSOLE.print(tbl)
        if custom_bindings:
            _RICH_CONSOLE.print("\n[dim]Custom binds: use /keybind to add more, /keybind clear <key> to remove[/]\n")
        else:
            _RICH_CONSOLE.print("\n[dim]No custom binds yet — try: /keybind Ctrl+H /histsearch[/]\n")
    else:
        print("\n⌨️  All Key Bindings\n")
        print(f"  {'Key':<16} {'Action':<35} Type")
        print("─" * 60)
        for key, desc in builtin_bindings:
            print(f"  {_BYE}{key:<16}{_R} {desc:<35} {_DM}built-in{_R}")
        for key, action in custom_bindings:
            print(f"  {_BGR}{key:<16}{_R} {action:<35} {_GR}custom{_R}")
        print()

    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_diff
# ---------------------------------------------------------------------------

def _cmd_diff(ctx: ChatCommandContext) -> str:
    """/diff [file1 file2 | --git] — show a colorized unified diff."""
    import subprocess
    arg = ctx.args.strip()
    is_tty = _get_is_tty()

    if not arg or arg == "--git":
        try:
            result = subprocess.run(
                ["git", "diff", "--no-color"],
                capture_output=True, text=True, timeout=10
            )
            diff_text = result.stdout or result.stderr
        except (OSError, subprocess.SubprocessError) as e:  # noqa: BLE001
            diff_text = f"Error: {e}"
    else:
        parts = arg.split(None, 1)
        if len(parts) < 2:
            msg = "Usage: /diff file1 file2  or  /diff --git"
            if _RICH_AVAILABLE and is_tty:
                _RICH_CONSOLE.print(f"[yellow]{msg}[/]")
            else:
                print(msg)
            return _CMD_CONTINUE
        try:
            result = subprocess.run(
                ["diff", "-u", parts[0], parts[1]],
                capture_output=True, text=True, timeout=10
            )
            diff_text = result.stdout or "(no differences)"
        except (OSError, subprocess.SubprocessError) as e:  # noqa: BLE001
            diff_text = f"Error: {e}"

    if not diff_text or not diff_text.strip():
        msg = "No differences found."
        if _RICH_AVAILABLE and is_tty:
            _RICH_CONSOLE.print(f"[dim]{msg}[/]")
        else:
            print(msg)
        return _CMD_CONTINUE

    colored = _get_cli_mod()._render_diff_ansi(diff_text)
    print(colored)
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_changes
# ---------------------------------------------------------------------------

def _cmd_changes(ctx: ChatCommandContext) -> str:
    """/changes — show files mentioned/edited in this session."""
    import subprocess
    is_tty = _get_is_tty()
    _cli = _get_cli_mod()

    edits = _cli._PREFS.get("session_edits", [])

    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=5
        )
        git_changes = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):  # noqa: BLE001
        git_changes = ""

    if _RICH_AVAILABLE and is_tty:
        _RICH_CONSOLE.print("\n[bold cyan]📝 Session Changes[/]\n")
        if edits:
            for edit in edits[-20:]:
                _RICH_CONSOLE.print(f"  [dim]→[/] {edit}")
        else:
            _RICH_CONSOLE.print("  [dim]No session edits tracked yet[/]")

        if git_changes:
            _RICH_CONSOLE.print("\n[bold cyan]🔀 Git Status[/]\n")
            for line in git_changes.split("\n"):
                if line.startswith("M") or line.startswith(" M"):
                    _RICH_CONSOLE.print(f"  [yellow]{line}[/]")
                elif line.startswith("A") or line.startswith(" A"):
                    _RICH_CONSOLE.print(f"  [green]{line}[/]")
                elif line.startswith("D") or line.startswith(" D"):
                    _RICH_CONSOLE.print(f"  [red]{line}[/]")
                elif line.startswith("?"):
                    _RICH_CONSOLE.print(f"  [dim]{line}[/]")
                else:
                    _RICH_CONSOLE.print(f"  {line}")
        _RICH_CONSOLE.print()
    else:
        print("\n📝 Session Changes\n")
        if edits:
            for edit in edits[-20:]:
                print(f"  → {edit}")
        else:
            print("  No session edits tracked yet")
        if git_changes:
            print("\n🔀 Git Status\n")
            for line in git_changes.split("\n"):
                print(f"  {line}")
        print()

    return _CMD_CONTINUE
