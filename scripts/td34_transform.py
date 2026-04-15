#!/usr/bin/env python3
"""TD-34 transformation script."""
import re, sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SRC = REPO_ROOT / "src"
CLI_PY = SRC / "openclaw_cli.py"

MODULE_MAP = {
    "_core_cmd_mod": "openclaw_cli_cmd_core",
    "_cmd_session_mod": "openclaw_cli_cmd_session",
    "_workflow_cmd_mod": "openclaw_cli_cmd_workflow",
    "_content_cmd_mod": "openclaw_cli_cmd_content",
    "_settings_cmd_mod": "openclaw_cli_cmd_settings",
    "_system_cmd_mod": "openclaw_cli_cmd_system",
    "_misc_cmd_mod": "openclaw_cli_cmd_misc",
}

WRAPPER_MAP = {
    "_cmd_exporttemplates": "_core_cmd_mod", "_cmd_runbook": "_core_cmd_mod",
    "_cmd_help": "_core_cmd_mod", "_cmd_clear": "_core_cmd_mod",
    "_cmd_context": "_core_cmd_mod", "_cmd_cwd": "_core_cmd_mod",
    "_cmd_files": "_core_cmd_mod", "_cmd_routing": "_core_cmd_mod",
    "_cmd_why": "_core_cmd_mod", "_cmd_trace": "_core_cmd_mod",
    "_cmd_autoroute": "_core_cmd_mod", "_cmd_snapshot": "_core_cmd_mod",
    "_cmd_rollback": "_core_cmd_mod", "_cmd_analyze": "_core_cmd_mod",
    "_cmd_research": "_core_cmd_mod", "_cmd_write": "_core_cmd_mod",
    "_cmd_exec": "_core_cmd_mod", "_cmd_edit": "_core_cmd_mod",
    "_cmd_update": "_core_cmd_mod", "_cmd_version": "_core_cmd_mod",
    "_cmd_tokeninfo": "_core_cmd_mod", "_cmd_draft": "_core_cmd_mod",
    "_cmd_template": "_core_cmd_mod", "_cmd_inject": "_core_cmd_mod",
    "_cmd_session": "_cmd_session_mod", "_cmd_events": "_cmd_session_mod",
    "_cmd_sessions": "_cmd_session_mod", "_cmd_export": "_cmd_session_mod",
    "_cmd_tag": "_cmd_session_mod", "_cmd_bookmark": "_cmd_session_mod",
    "_cmd_bookmarks": "_cmd_session_mod", "_cmd_resume": "_cmd_session_mod",
    "_cmd_replay": "_cmd_session_mod", "_cmd_handoff": "_cmd_session_mod",
    "_cmd_watch": "_workflow_cmd_mod", "_cmd_plan": "_workflow_cmd_mod",
    "_cmd_task": "_workflow_cmd_mod", "_cmd_risk": "_workflow_cmd_mod",
    "_cmd_incident": "_workflow_cmd_mod", "_cmd_workspace": "_workflow_cmd_mod",
    "_cmd_macro": "_workflow_cmd_mod", "_cmd_macrostatus": "_workflow_cmd_mod",
    "_cmd_workflow": "_workflow_cmd_mod", "_cmd_dashboard": "_workflow_cmd_mod",
    "_cmd_alerts": "_workflow_cmd_mod", "_cmd_fleet": "_workflow_cmd_mod",
    "_cmd_collab": "_content_cmd_mod", "_cmd_search": "_content_cmd_mod",
    "_cmd_outputs": "_content_cmd_mod", "_cmd_stats": "_content_cmd_mod",
    "_cmd_pattern": "_content_cmd_mod", "_cmd_history": "_content_cmd_mod",
    "_cmd_pin": "_content_cmd_mod", "_cmd_pins": "_content_cmd_mod",
    "_cmd_quality": "_content_cmd_mod", "_cmd_timeline": "_content_cmd_mod",
    "_cmd_theme": "_settings_cmd_mod", "_cmd_overlay": "_settings_cmd_mod",
    "_cmd_colorscheme": "_settings_cmd_mod", "_cmd_emojiheaders": "_settings_cmd_mod",
    "_cmd_emoji": "_settings_cmd_mod", "_cmd_layout": "_settings_cmd_mod",
    "_cmd_links": "_settings_cmd_mod", "_cmd_pasteguard": "_settings_cmd_mod",
    "_cmd_accessibility": "_settings_cmd_mod", "_cmd_keybind": "_settings_cmd_mod",
    "_cmd_system": "_system_cmd_mod", "_cmd_promptdebug": "_system_cmd_mod",
    "_cmd_autobold": "_system_cmd_mod", "_cmd_jsonformat": "_system_cmd_mod",
    "_cmd_separator": "_system_cmd_mod", "_cmd_palette": "_system_cmd_mod",
    "_cmd_prompt": "_system_cmd_mod", "_cmd_alias": "_system_cmd_mod",
    "_cmd_pathhints": "_system_cmd_mod", "_cmd_ratehint": "_system_cmd_mod",
    "_cmd_benchmark": "_system_cmd_mod",
    "_cmd_recall": "_misc_cmd_mod", "_cmd_histsearch": "_misc_cmd_mod",
    "_cmd_celebrate": "_misc_cmd_mod", "_cmd_rate": "_misc_cmd_mod",
    "_cmd_streak": "_misc_cmd_mod", "_cmd_heatmap": "_misc_cmd_mod",
    "_cmd_followup": "_misc_cmd_mod", "_cmd_shortcuts": "_misc_cmd_mod",
    "_cmd_top": "_misc_cmd_mod", "_cmd_freq": "_misc_cmd_mod",
    "_cmd_tip": "_misc_cmd_mod", "_cmd_keys": "_misc_cmd_mod",
    "_cmd_bindlist": "_misc_cmd_mod", "_cmd_diff": "_misc_cmd_mod",
    "_cmd_changes": "_misc_cmd_mod",
}
KEEP_FUNCTIONS = {"_cmd_quit"}


def get_function_body_end(lines, start):
    """Find the end of a function body (inclusive: def line + indented body + trailing blanks).
    
    Returns the first line index that is NOT part of this function:
    - The first non-blank, non-indented (column-0) line after the body.
    """
    n = len(lines)
    i = start + 1
    last_content_line = start  # track last non-blank line in body

    # Scan through indented body lines and blank lines
    while i < n:
        line = lines[i]
        stripped = line.rstrip('\n').rstrip()
        if stripped == '':
            # Blank line - might be trailing blank, continue scanning
            i += 1
        elif line[0] in (' ', '\t'):
            # Indented: part of the function body
            last_content_line = i
            i += 1
        else:
            # Non-blank, non-indented: this is module-level code, stop here
            break

    # Return the position just after the last content line + blank lines
    # We want to consume blank lines that immediately follow the body
    # but stop at the first module-level non-blank line
    end = last_content_line + 1
    while end < n and lines[end].strip() == '':
        end += 1
    return end


def find_wrapper_ranges(lines):
    """Find (name, start, end) for all simple wrapper functions."""
    pat = re.compile(r"^def (_cmd_\w+)\(")
    results = []
    i, n = 0, len(lines)
    while i < n:
        m = pat.match(lines[i])
        if m:
            name = m.group(1)
            if name in WRAPPER_MAP and name not in KEEP_FUNCTIONS:
                # Find the "conceptual" end of this function to check if it's a simple wrapper
                end = get_function_body_end(lines, i)
                body = "".join(lines[i:end])
                mod = WRAPPER_MAP[name]
                if re.search(rf"return\s+{re.escape(mod)}\.{re.escape(name)}\s*\(", body):
                    results.append((name, i, end))
                    i = end
                    continue
        i += 1
    return results


def find_named_function_range(lines, func_name):
    """Find (start, end) for a specifically named function."""
    pat = re.compile(r"^def (\w+)\(")
    n = len(lines)
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m and m.group(1) == func_name:
            end = get_function_body_end(lines, i)
            return (i, end)
    return None


def build_import_block(names):
    by_pkg = {}
    for n in sorted(names):
        pkg = MODULE_MAP[WRAPPER_MAP[n]]
        by_pkg.setdefault(pkg, []).append(n)
    out = ["# Re-exported command functions (wrappers extracted; TD-34)"]
    for pkg in sorted(by_pkg):
        for n in sorted(by_pkg[pkg]):
            out.append(f"from {pkg} import {n} as {n}  # noqa: F401")
    out.append("")
    return "\n".join(out)


def main():
    text = CLI_PY.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    print(f"Original: {len(lines)} lines")

    # Find all wrapper function ranges
    wrapper_ranges = find_wrapper_ranges(lines)
    wrappers = {name for name, _, _ in wrapper_ranges}
    print(f"Wrappers to remove: {len(wrappers)}")

    # Find print_chat_help and build_parser
    pch_range = find_named_function_range(lines, "print_chat_help")
    bp_range = find_named_function_range(lines, "build_parser")
    if not pch_range or not bp_range:
        print("ERROR: Missing print_chat_help or build_parser"); sys.exit(1)

    pch_lines = lines[pch_range[0]:pch_range[1]]
    bp_lines = lines[bp_range[0]:bp_range[1]]

    # Write openclaw_cli_help.py
    help_py = SRC / "openclaw_cli_help.py"
    help_content = '''"""Print the interactive chat help table."""
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

''' + "".join(pch_lines)
    help_py.write_text(help_content, encoding="utf-8")
    print(f"Wrote {help_py} ({len(help_content.splitlines())} lines)")

    # Write openclaw_cli_cli_parser.py
    dm = re.search(r'^DEFAULT_MODEL\s*=\s*(.+)$', text, re.M)
    dt = re.search(r'^DEFAULT_TIMEOUT_SECONDS\s*=\s*(.+)$', text, re.M)
    dm_val = dm.group(1).strip() if dm else '"auto"'
    dt_val = dt.group(1).strip() if dt else '120'

    parser_py = SRC / "openclaw_cli_cli_parser.py"
    parser_content = f'''"""CLI argument parser for OpenClaw."""
from __future__ import annotations
import argparse
from openclaw_cli_auth import TOKEN_ENV_VARS
from openclaw_cli_update import cli_version
DEFAULT_MODEL = {dm_val}
DEFAULT_TIMEOUT_SECONDS = {dt_val}

''' + "".join(bp_lines)
    parser_py.write_text(parser_content, encoding="utf-8")
    print(f"Wrote {parser_py} ({len(parser_content.splitlines())} lines)")

    # Remove ranges from openclaw_cli.py (descending order to preserve indices)
    all_ranges = list(wrapper_ranges)
    all_ranges.append(("print_chat_help", pch_range[0], pch_range[1]))
    all_ranges.append(("build_parser", bp_range[0], bp_range[1]))
    all_ranges.sort(key=lambda x: x[1], reverse=True)

    new_lines = list(lines)
    for name, s, e in all_ranges:
        del new_lines[s:e]
    print(f"Removed {len(all_ranges)} function blocks")

    # Insert re-export imports after _misc_cmd_mod import
    anchor = None
    for i, line in enumerate(new_lines):
        if "import openclaw_cli_cmd_misc as _misc_cmd_mod" in line:
            anchor = i
            break
    if anchor is None:
        print("ERROR: anchor line not found"); sys.exit(1)

    insert = ("\n" + build_import_block(wrappers) +
              "from openclaw_cli_help import print_chat_help  # noqa: F401\n"
              "from openclaw_cli_cli_parser import build_parser  # noqa: F401\n\n")
    new_lines.insert(anchor + 1, insert)

    new_text = "".join(new_lines)
    CLI_PY.write_text(new_text, encoding="utf-8")
    count = len(new_text.splitlines())
    print(f"Final: {count} lines")
    if count < 5000:
        print(f"SUCCESS: {count} < 5000")
    else:
        print(f"STILL TOO LARGE: {count}")

    # Verify _COMMAND_SPECS is still present
    if "_COMMAND_SPECS" in new_text:
        print("OK: _COMMAND_SPECS still present")
    else:
        print("ERROR: _COMMAND_SPECS was deleted!")


if __name__ == "__main__":
    main()
