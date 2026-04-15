"""Print the interactive chat help table."""
from __future__ import annotations
try:
    from rich.console import Console as _RichConsole
    from rich.table import Table as _RichTable
    from rich.panel import Panel as _RichPanel
    _RICH_CONSOLE = _RichConsole(highlight=False)
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False
    _RichTable = None  # type: ignore[assignment,misc]
    _RichPanel = None  # type: ignore[assignment,misc]
    _RICH_CONSOLE = None  # type: ignore[assignment]
from openclaw_cli_ui_core import _IS_TTY, _R, _DM

def print_chat_help(*, search: str = "") -> None:
    """Print built-in interactive chat commands, optionally filtered by *search*."""
    commands = [
        ("/help [search QUERY]",           "Show this help, or filter commands by keyword"),
        ("/clear",                         "Reset the current conversation history"),
        ("/quit",                          "Exit the CLI"),
        ("/update",                        "Self-upgrade openclaw via pip"),
        ("/version",                       "Show running CLI version and build stamp"),
        ("/session",                       "Show current session summary"),
        ("/context",                       "Show effective session grounding preview"),
        ("/cwd [path]",                    "Show or switch the session working directory"),
        ("/files",                         "List tracked files"),
        ("/files add <path>",              "Add a file to tracked files"),
        ("/files rm <path>",               "Remove a file from tracked files"),
        ("/plan [<id>|unlink]",            "Show or link a plan"),
        ("/task [<id>|unlink]",            "Show or link a task"),
        ("/risk [list|add LEVEL TEXT|clear INDEX]", "Track blocking risks for the current session"),
        ("/incident [list|log TEXT|resolve INDEX]", "Track and resolve operator incidents for the current session"),
        ("/dashboard automation",              "Show a compact automation dashboard across active sessions"),
        ("/alerts [list|acknowledge INDEX]",   "List computed operator alerts and acknowledge one"),
        ("/fleet [status|health]",             "Show cross-session automation health in a compact view"),
        ("/outputs [promote <i> <name>]",  "List, preview, promote, or overlay-pick saved session outputs"),
        ("/overlay [on|off|status]",       "Toggle opt-in interactive pickers for supported list commands"),
        ("/rollback [last|list|<name>]",   "List git snapshots, preview/exec rollback, or restore checkpoint"),
        ("/snapshot [name]",               "Save current git HEAD as a named restore point"),
        ("/events [n|decisions]",              "Show last n session events, or decision-only view"),
        ("/why",                               "Explain the last routing/tool decision (confidence, rationale, grounding)"),
        ("/workspace [status|save|list|restore NAME]", "Manage workspace recovery capsules for the current session"),
        ("/collab [status|share|assign]",      "Show or extend the actor-oriented handoff summary for the current session"),
        ("/runbook [template] [save <path>]",  "Render a long-form runbook for the active session"),
        ("/exporttemplates [list|show <name>]", "Inspect built-in runbook/export templates"),
        ("/collab note [@actor] TEXT",         "Record a collaboration note in the local session audit trail"),
        ("/collab decision [@actor] [#tag] TEXT", "Record a tagged decision for later handoff/export"),
        ("/collab assign @actor TEXT",         "Assign an owner to the next shared task or handoff step"),
        ("/handoff check",                     "Audit readiness using linked plan/task, ownership, and open risks"),
        ("/search <query>",                    "Search this session's event history for matching turns"),
        ("/search --all <query>",              "Search across all session histories"),
        ("/autoroute [on|off]",            "Show or toggle high-confidence REPL auto-routing"),
        ("/analyze <goal>",                "Analyze the session workspace"),
        ("/research <query>",              "Run the research agent on a query"),
        ("/write <task>",                  "Generate a markdown document"),
        ("/exec [--] <command>",           "Run a shell command with approval + session tracking"),
        ("/edit <path> [--content TEXT]",  "Inspect or write a file (--append to append)"),
        ("/theme [name|list|preview|next|prev|reset]", "Manage UI themes and previews"),
        ("/emoji [on|off|pack|preview]", "Toggle emoji or switch emoji packs"),
        ("/layout [compact|normal|verbose|plain|preset|show]", "Switch density or preset workspace views"),
        ("/sessions [search|related]",     "Browse or search recent sessions; /sessions overlay opens a picker"),
        ("/export [md|json|txt] [file]",   "Export session history to file (md/json/txt)"),
        ("/stats [commands|ratings|sessions]", "Show ASCII bar charts of usage statistics"),
        ("/tag [add|rm|list] <tag>",       "Manage tags on the current session"),
        ("/bookmark [label]",              "Save a replay bookmark for the current session"),
        ("/bookmarks",                     "List saved replay bookmarks for the current session"),
        ("/resume [last|<id>]",            "Print resume instructions for a past session"),
        ("/replay [session-id] [--from <bookmark>]", "Re-print the current or a past session conversation"),
        ("/draft [save|load|clear|restore]",    "Save, load, clear, or restore a draft prompt"),
        ("/draft multiline [on|off]",           "Toggle multiline compose mode"),
        ("/template [list|use|save|delete]",    "Manage reusable prompt templates"),
        ("/pasteguard [on|off]",                "Toggle paste guard for large risky pastes"),
        ("/pin [name]",                         "Pin the last AI response (auto-named if no name given)"),
        ("/pin recall <name>",                  "Re-display a pinned response"),
        ("/pin rm <name>",                      "Remove a pin by name"),
        ("/pins",                               "List all pinned responses"),
        ("/accessibility [status|mode]",        "Show or set accessibility modes (a11y)"),
        ("/accessibility reduced-motion on|off","Toggle reduced-motion (no spinner animation)"),
        ("/accessibility plain on|off",         "Toggle plain/screen-reader mode"),
        ("/accessibility high-contrast on|off", "Toggle high-contrast colour palette"),
        ("/alias",                              "List all defined command aliases"),
        ("/alias <name> <expansion>",           "Define a command shorthand alias"),
        ("/alias rm <name>",                    "Remove a defined alias"),
        ("/history [page]",                     "Show command history, 15 per page (color-coded)"),
        ("/history clear",                      "Clear command history"),
        ("/recall",                             "List recent prompts (non-slash-command inputs)"),
        ("/recall <n>",                         "Re-inject the nth most recent prompt into chat"),
        ("/histsearch <query>",                 "Search prompt history for matching entries"),
        ("/macro list",                         "List all saved macros"),
        ("/macro save <name> [last N]",         "Save last N commands as a named macro"),
        ("/macro show <name>",                  "Show the commands stored in a macro"),
        ("/macro run <name>",                   "Execute a saved macro's commands in sequence"),
        ("/macro rm <name>",                    "Delete a named macro"),
        ("/workflow list",                      "List previewable workflows backed by the macro store"),
        ("/workflow save <name> [last N]",      "Save recent commands as a workflow"),
        ("/workflow preview <name>",            "Show the resolved workflow steps without executing them"),
        ("/workflow run <name>",                "Execute a saved workflow with session placeholders resolved"),
        ("/workflow rm <name>",                 "Delete a saved workflow"),
        ("/pattern list",                       "Browse saved reusable patterns with lightweight source metadata"),
        ("/pattern save <name> [last N|workflow NAME]", "Save recent commands or a workflow as a reusable pattern"),
        ("/pattern preview <name>",             "Preview a saved pattern before execution"),
        ("/pattern run <name>",                 "Execute a saved pattern with session placeholders resolved"),
        ("/pattern rm <name>",                  "Delete a saved pattern"),
        ("/rate [good|ok|bad|meh|1-5]",         "Rate the last AI response and store feedback"),
        ("/quality",  "Show response quality stats — avg score, distribution, recent ratings"),
        ("/quality predict", "Show the best-rated route based on your prior ratings"),
        ("/routing [suggest|analyze]",         "Inspect learned route suggestions without changing auto-routing"),
        ("/streak",   "Show your current high-rating streak and all-time best"),
        ("/heatmap",  "Show a color-coded 24-hour activity heatmap of openclaw usage"),
        ("/top [n]",  "Show the n most frequently used prompts and commands (default: 10)"),
        ("/freq",     "Show frequency analysis of slash commands used"),
        ("/ratehint [on|off]",                   "Show or toggle the post-response rating hint"),
        ("/inject <path>",                       "Inject file content as context prefix for next message"),
        ("/inject --url <url>",                  "Inject URL content as context prefix for next message"),
        ("/inject clear",                        "Clear the pending injection"),
        ("/inject status",                       "Show what content is queued for injection"),
        ("/promptdebug",                         "Preview the full prompt that would be sent to AI (system + inject + message)"),
        ("/system",                              "View the current system prompt"),
        ("/system set <text>",                   "Set a persistent system prompt prefix for all messages"),
        ("/system append <text>",               "Append to the existing system prompt"),
        ("/system clear",                        "Clear the system prompt"),
        ("/autobold [on|off]",                   "Toggle automatic bolding of numbers and filenames in responses"),
        ("/jsonformat [on|off]",                 "Toggle automatic JSON detection and pretty-printing in responses"),
        ("/separator [style]",                   "Set or preview response separator style (gradient|pulse|dots|wave|none)"),
        ("/links [on|off]",                      "Toggle clickable OSC 8 hyperlinks in responses (requires modern terminal)"),
        ("/palette [query]",                     "Search slash commands by keyword (fuzzy)"),
        ("/shortcuts",                           "Show keyboard shortcuts and quick-access reference card"),
        ("/keys",                                "Show active keyboard shortcuts and readline bindings"),
        ("/bindlist",                            "Show all keyboard bindings — built-in readline + custom"),
        ("/keybind [list|Ctrl+X /cmd|clear X]", "Manage custom readline key bindings"),
        ("/diff [file1 file2 | --git]",          "Show a colorized unified diff"),
        ("/changes",                             "Show session edit log and git status"),
        ("/timeline",                            "Show a visual activity timeline of recent openclaw usage"),
        ("/dashboard",                           "Show the power dashboard: sessions, stats, pins, and system status"),
        ("/benchmark [n]",                       "Measure AI server response latency (n pings, default 3, max 10)"),
        ("/followup",                            "Show contextual follow-up suggestions for your last prompt"),
        ("/followup on|off",                     "Enable or disable the auto-suggestion footer after responses"),
        ("/tokeninfo",                           "Show estimated context token usage"),
    ]

    q = search.strip().lower()
    if q:
        commands = [(cmd, desc) for cmd, desc in commands if q in cmd.lower() or q in desc.lower()]
        if not commands:
            print(f"  {_DM}No commands match '{q}'.{_R}")
            return

    notes = (
        "High-confidence freeform prompts can auto-route to /analyze, /research, /write, /exec, or /edit.\n"
        "Multi-step prompts can decompose into linked plans and auto-run step-by-step with [n/N] progress.\n"
        "Ambiguous prompts stay in normal chat. High/critical /exec and /edit steps still require approval.\n"
        "[autoroute:off] in the prompt means auto-routing is disabled — use /autoroute on to re-enable."
    )
    if _RICH_AVAILABLE and _IS_TTY:
        title = f"[bold cyan]OpenClaw Commands[/bold cyan]" + (f"  [dim]matching '{q}'[/]" if q else "")
        t = _RichTable.grid(padding=(0, 2))
        t.add_column(style="bold cyan", no_wrap=True)
        t.add_column(style="dim")
        for cmd, desc in commands:
            t.add_row(cmd, desc)
        _RICH_CONSOLE.print(_RichPanel(t, title=title, border_style="cyan", padding=(0, 1)))
        if not q:
            _RICH_CONSOLE.print(f"[dim]{notes}[/dim]")
            examples = [
                ("Ask a question",       "What does this repo do?"),
                ("Analyze a directory",  "openclaw analyze --cwd ./src"),
                ("Run a command",        "/exec -- git diff HEAD"),
                ("Research a topic",     "/research latest Python async patterns"),
                ("Link a plan",          "/plan my-feature-plan"),
            ]
            ex_grid = _RichTable.grid(padding=(0, 2))
            ex_grid.add_column(style="dim")
            ex_grid.add_column(style="bold cyan")
            for label, cmd in examples:
                ex_grid.add_row(label, cmd)
            _RICH_CONSOLE.print(_RichPanel(ex_grid, title="[bold]Examples[/]", border_style="dim", padding=(0, 1)))
    else:
        if q:
            print(f"  Commands matching '{q}':")
        else:
            print("Interactive commands:")
        for cmd, desc in commands:
            print(f"  {cmd:<42} {desc}")
        print()
        if not q:
            print(notes)
            print("\nExamples:")
            print('  Ask a question         What does this repo do?')
            print('  Analyze a directory    openclaw analyze --cwd ./src')
            print('  Run a command          /exec -- git diff HEAD')
            print('  Research a topic       /research latest Python async patterns')
            print('  Link a plan            /plan my-feature-plan')


