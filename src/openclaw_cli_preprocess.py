"""Response text preprocessing and formatting helpers."""
from __future__ import annotations

import json
import re
import shutil
from typing import Any

from openclaw_cli_prefs import _PREFS, _A11Y_PLAIN_MODE, _A11Y_HIGH_CONTRAST
from openclaw_cli_ui_core import _CY, _R, _GR, _YE, _MA

try:
    from rich.console import Console as _RichConsole
    from rich.table import Table as _RichTable

    _RICH_CONSOLE = _RichConsole(highlight=False)
    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RICH_AVAILABLE = False


def _a11y_plain_mode() -> bool:
    return bool(_PREFS.get(_A11Y_PLAIN_MODE, False))


def _a11y_high_contrast() -> bool:
    return bool(_PREFS.get(_A11Y_HIGH_CONTRAST, False))


# ---------------------------------------------------------------------------
# Compiled regex constants
# ---------------------------------------------------------------------------

_RE_KV_BOLD = re.compile(r"\*\*[^*]+:\*\*")
_RE_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^\)]+)\)")
_RE_BARE_URL = re.compile(r"(https?://\S+)")
_MD_TABLE_BLOCK = re.compile(
    r"(?m)^(\|[^\n]+\n\|[-:| ]+\|(?:\n\|[^\n]+)*)",
)
_RE_SOURCES_BLOCK = re.compile(
    r"\n{1,2}(?:\*\*Sources\*\*|Sources):?\s*\n((?:(?:[-\*]|\d+\.)\s+.+\n?)+)",
    re.IGNORECASE,
)
_RE_SOURCES_BLOCK_LOOSE = re.compile(
    r"(?:^|\n)(?:\*\*Sources\*\*|Sources):?\s*\n((?:(?:[-\*]|\d+\.)\s+.+\n?)+)",
    re.IGNORECASE | re.MULTILINE,
)
_RE_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ---------------------------------------------------------------------------
# Bullet-to-table helpers
# ---------------------------------------------------------------------------


def _is_kv_bullet_group(lines: list[str]) -> bool:
    """Return True if all lines look like pipe-separated key:value bullet rows.

    Accepts both **Key:** value (bold) and plain Key: Value formats, including
    lines where the whole content is wrapped in italic markers (*...*).
    """
    for line in lines:
        content = re.sub(r"^[•\-\*]\s+", "", line.lstrip())
        # Strip wrapping italic markers (*content*) around the whole line body
        content = re.sub(r"^\*(.+)\*$", r"\1", content.strip())
        if _RE_KV_BOLD.search(content):
            continue
        # Accept plain "Key: value | Key: value" rows — require a colon in the
        # majority of pipe-segments so we don't misclassify normal prose bullets.
        segments = [s.strip() for s in content.split(" | ")]
        if len(segments) < 2:
            return False
        colon_count = sum(1 for s in segments if ":" in s)
        if colon_count < len(segments) // 2 + 1:
            return False
    return True


def _bullet_group_to_table(lines: list[str]) -> list[str]:
    """Convert pipe-in-bullet lines to a markdown table.

    Handles both **Key:** value (bold) and plain Key: Value formats.
    Also strips wrapping italic markers (*...*) that some models add.
    """
    headers: list[str] = []
    rows: list[list[str]] = []
    for line in lines:
        content = re.sub(r"^[•\-\*]\s+", "", line.lstrip())
        # Strip wrapping italic markers around the whole line body
        content = re.sub(r"^\*(.+)\*$", r"\1", content.strip())
        parts = [p.strip() for p in content.split(" | ")]
        row_headers: list[str] = []
        row_values: list[str] = []
        for part in parts:
            # Strip lone leading asterisks (partial italic markers from the first/last segment)
            part = re.sub(r"^\*+", "", part).strip()
            # Match **Key:** value  (bold-colon inside markers)
            m = re.match(r"\*\*([^*:]+):\*\*\s*(.*)", part)
            if m:
                row_headers.append(m.group(1).strip())
                row_values.append(m.group(2).strip())
            else:
                # Match plain "Key: value" — split on first colon
                colon_idx = part.find(":")
                if colon_idx > 0:
                    row_headers.append(part[:colon_idx].strip())
                    # Strip leading asterisks from values (closing italic marker from last segment)
                    val = re.sub(r"^\*+\s*", "", part[colon_idx + 1:].strip())
                    row_values.append(val)
                else:
                    row_headers.append(f"Col{len(row_headers) + 1}")
                    row_values.append(part)
        if not headers:
            headers = row_headers
        rows.append(row_values)
    table: list[str] = []
    table.append("| " + " | ".join(headers) + " |")
    table.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        while len(row) < len(headers):
            row.append("")
        table.append("| " + " | ".join(row[: len(headers)]) + " |")
    return table


def _unwrap_code_block_tables(text: str) -> str:
    """Unwrap fenced code blocks that contain only pipe-in-bullet table rows.

    When the AI wraps a pipe-in-bullet table in triple-backtick fences, Rich
    renders it as a monospace code block instead of a table.  This step detects
    those blocks and removes the fences so _convert_bullet_tables can convert them.
    """
    def _replace(m: re.Match) -> str:
        content = m.group(1).strip()
        non_empty = [l for l in content.split("\n") if l.strip()]
        if len(non_empty) >= 2 and all(
            re.match(r"^[•\-\*]\s+.+$", l) and " | " in l
            for l in non_empty
        ):
            return content  # strip the fences
        return m.group(0)  # leave unchanged

    return re.sub(r"```[^\n]*\n(.*?)```", _replace, text, flags=re.DOTALL)


def _convert_bullet_tables(text: str) -> str:
    """Detect pipe-in-bullet table patterns and convert to proper markdown tables."""
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        bullet_match = re.match(r"^\s*[•\-\*]\s+.+$", line)
        if bullet_match and " | " in line:
            group = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if re.match(r"^\s*[•\-\*]\s+.+$", next_line) and " | " in next_line:
                    group.append(next_line)
                    j += 1
                else:
                    break
            if len(group) >= 2 and _is_kv_bullet_group(group):
                result.extend(_bullet_group_to_table(group))
                i = j
                continue
        result.append(line)
        i += 1
    return "\n".join(result)


# ---------------------------------------------------------------------------
# JSON detection and formatting
# ---------------------------------------------------------------------------


def _colorize_json(text: str) -> str:
    """Apply ANSI color coding to a JSON string."""
    if _a11y_plain_mode():
        return text
    import re as _re_json
    # Keys (quoted strings before colon) → cyan
    text = _re_json.sub(r'"([^"]+)"(\s*:)', f'{_CY}"\\1"{_R}\\2', text)
    # String values → green
    text = _re_json.sub(r':\s*"([^"]*)"', f': {_GR}"\\1"{_R}', text)
    # Numbers → yellow
    text = _re_json.sub(r':\s*(-?\d+(?:\.\d+)?)', f': {_YE}\\1{_R}', text)
    # Booleans and null → magenta
    text = _re_json.sub(r'\b(true|false|null)\b', f'{_MA}\\1{_R}', text)
    return text


def _detect_and_format_json(text: str) -> str:
    """Detect bare JSON objects/arrays in response text and pretty-print them."""
    if not _PREFS.get("json_autoformat", True) or _a11y_plain_mode():
        return text

    lines = text.split("\n")
    result: list[str] = []
    i = 0
    in_code_block = False

    while i < len(lines):
        line = lines[i]

        # Track code blocks — don't touch content inside them
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            i += 1
            continue

        if in_code_block:
            result.append(line)
            i += 1
            continue

        stripped = line.strip()

        # Detect start of JSON: line starts with { or [
        if stripped.startswith("{") or stripped.startswith("["):
            # First try just this single line
            try:
                obj = json.loads(stripped)
                pretty = json.dumps(obj, indent=2)
                pretty_colored = _colorize_json(pretty)
                result.append("```json")
                result.extend(pretty_colored.split("\n"))
                result.append("```")
                i += 1
                continue
            except json.JSONDecodeError:
                pass
            # Then try accumulating more lines (multi-line JSON)
            json_lines = [line]
            j = i + 1
            matched = False
            while j < len(lines) and j < i + 50:
                json_lines.append(lines[j])
                candidate = "\n".join(json_lines)
                try:
                    obj = json.loads(candidate.strip())
                    pretty = json.dumps(obj, indent=2)
                    pretty_colored = _colorize_json(pretty)
                    result.append("```json")
                    result.extend(pretty_colored.split("\n"))
                    result.append("```")
                    i = j + 1
                    matched = True
                    break
                except json.JSONDecodeError:
                    j += 1
            if not matched:
                result.append(line)
                i += 1
            continue

        result.append(line)
        i += 1

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Master response text preprocessor
# ---------------------------------------------------------------------------


def _preprocess_response_text(text: str) -> tuple[str, str | None]:
    """Clean up raw LLM response text for better CLI rendering.

    Returns (cleaned_body, sources) where sources may be None.

    Steps:
      A. Strip recovery note blocks (before anything else so they don't interfere).
      B. Strip trailing ``_via model-name_`` trailer added by some proxied models.
      C. Extract the Sources section (if present) so it can be rendered separately.
      D. Strip inline [N] citation markers.
      E. Unwrap fenced code blocks that contain only pipe-in-bullet table rows.
      F. Convert pipe-in-bullet table patterns to proper markdown tables.
    """
    # A. Strip server-appended recovery note blocks — do this FIRST before any other
    # manipulation so the block is always present in text regardless of ordering.
    # Matches both \n\n and \n before the blockquote opener, and captures until
    # the blockquote section ends (no more > lines).
    text = re.sub(
        r"\n{1,2}> ℹ️ \*\*Recovery note:\*\*\n(?:> [^\n]*\n?)*",
        "",
        text,
    )
    # Also strip bare-text recovery note blocks (no blockquote markers) in case
    # the model emits the recovery note without > prefix after some processing.
    text = re.sub(
        r"\n{1,2}ℹ️ \*?\*?Recovery note\*?\*?:?[^\n]*\n(?:[^\n]*\n?){0,6}",
        "",
        text,
    )

    # B. Strip _via model_ trailer — search broadly near the end (last 3 lines)
    # rather than only at EOF so it's caught even when other trailers follow it.
    text = re.sub(r"\n_via [^\n]+_[ \t]*(?=\n|$)", "", text)
    text = text.rstrip()

    # C. Extract Sources / **Sources** block at the end.
    # Matches bullet lists (- / *) AND numbered lists (1. 2. 3.) after a Sources heading.
    # Finds ALL occurrences, keeps the longest (most complete), strips all from body.
    sources: str | None = None
    all_matches = list(_RE_SOURCES_BLOCK.finditer(text))
    if all_matches:
        # Use the match with the most content (longest group 1) as the canonical sources
        best = max(all_matches, key=lambda m: len(m.group(1)))
        sources = best.group(0).strip()
        # Strip ALL sources blocks from body (reverse order to preserve indices)
        for m in reversed(all_matches):
            text = text[: m.start()] + text[m.end():]
        text = text.rstrip()

    # Fallback: catch Sources blocks with no preceding blank line
    if sources is None:
        all_loose = list(_RE_SOURCES_BLOCK_LOOSE.finditer(text))
        if all_loose:
            best = max(all_loose, key=lambda m: len(m.group(1)))
            sources = best.group(0).strip()
            for m in reversed(all_loose):
                text = text[: m.start()] + text[m.end():]
            text = text.rstrip()

    # D. Strip bare inline citation markers like [1], [2], [12]
    # Guard against stripping markdown link text like [text](url) — only remove
    # patterns where the bracket content is purely digits and not followed by (
    text = re.sub(r"\[(\d{1,2})\](?!\()", "", text)

    # E. Unwrap fenced code blocks that are really pipe-in-bullet tables
    text = _unwrap_code_block_tables(text)

    # F. Convert pipe-in-bullet table patterns to real markdown tables
    text = _convert_bullet_tables(text)

    return text, sources


# ---------------------------------------------------------------------------
# Auto-bolding
# ---------------------------------------------------------------------------


def _auto_bold_response(text: str) -> str:
    """Apply auto-bolding to key terms in AI response text.

    Post-processes the response body to make dollar amounts, percentages,
    and filenames visually pop. Skips fenced code blocks, table rows, and
    blockquotes. Only active when auto_bold pref is True and not in plain mode.
    """
    if _a11y_plain_mode() or not _PREFS.get("auto_bold", True):
        return text

    lines = text.split("\n")
    result = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue
        if in_code_block or line.startswith("|") or line.startswith(">"):
            result.append(line)
            continue

        # 1. Dollar amounts — skip if already bolded
        line = re.sub(
            r'(?<!\*)\$(\d[\d,\.]*(?:\s*(?:million|billion|trillion|thousand|[KMBkmb]))?)\b(?!\*)',
            r'**$\1**',
            line,
        )
        # 2. Percentages — skip if already bolded
        line = re.sub(
            r'(?<!\*)(\d+(?:\.\d+)?%)(?!\*)',
            r'**\1**',
            line,
        )
        # 3. File extensions — wrap in backticks if not already
        line = re.sub(
            r'(?<![`\w])(\w[\w\-]*\.(?:py|md|json|yaml|yml|sh|txt|js|ts|go|rs|html|css))(?![`\w])',
            r'`\1`',
            line,
        )

        result.append(line)

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Markdown table parsing and Rich rendering
# ---------------------------------------------------------------------------


def _strip_inline_md(text: str) -> str:
    """Strip common inline markdown markers (bold, italic, code) from a cell string."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    # Strip stray leading/trailing asterisks not caught above
    return text.strip().strip("*").strip()


def _parse_md_table(block: str) -> tuple[list[str], list[list[str]]] | None:
    """Parse a markdown table block into (headers, rows). Returns None on failure."""
    lines = [l for l in block.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    sep_line = lines[1]
    if not re.match(r"^\|[-:| ]+\|\s*$", sep_line):
        return None

    def _parse_row(line: str) -> list[str]:
        return [_strip_inline_md(p) for p in line.strip().strip("|").split("|")]

    headers = _parse_row(lines[0])
    rows = [_parse_row(l) for l in lines[2:] if l.strip() and "|" in l]
    if not headers:
        return None
    return headers, rows


def _render_md_table_rich(headers: list[str], rows: list[list[str]]) -> None:
    """Render a parsed markdown table using a Rich Table with sensible column widths.

    When too many columns exist to fit the terminal, the first column wraps
    (it's usually a label/name) and remaining columns share the available space.
    """
    term_cols = shutil.get_terminal_size((120, 24)).columns
    n = len(headers)
    if n == 0:
        return

    # Compute natural width of each column (max of header + values, capped)
    MAX_COL = 24
    MIN_COL = 5
    natural: list[int] = []
    for i, h in enumerate(headers):
        cell_max = max((len(r[i]) if i < len(r) else 0) for r in rows) if rows else 0
        natural.append(max(MIN_COL, min(max(len(h), cell_max), MAX_COL)))

    # Total needed: sum of column widths + 3 chars per column (border + padding)
    overhead = n * 3 + 1
    available = term_cols - overhead
    total_natural = sum(natural)

    if total_natural <= available:
        col_widths = natural
    else:
        # Scale down proportionally, respecting MIN_COL floor
        scale = max(0.3, available / total_natural)
        col_widths = [max(MIN_COL, int(w * scale)) for w in natural]

    table = _RichTable(
        border_style="bold white" if _a11y_high_contrast() else "dim",
        show_edge=True,
        pad_edge=True,
        header_style="bold bright_white" if _a11y_high_contrast() else "bold cyan",
    )
    for i, (h, w) in enumerate(zip(headers, col_widths)):
        # First column (labels/names) folds; numeric columns truncate cleanly
        overflow_mode = "fold" if i == 0 else "ellipsis"
        table.add_column(h, max_width=w, overflow=overflow_mode, no_wrap=(i > 0))

    for row in rows:
        cells = list(row) + [""] * max(0, n - len(row))
        table.add_row(*cells[:n])

    _RICH_CONSOLE.print(table)
