"""
openclaw_cli_render — Response rendering pipeline.

Depends on: openclaw_cli_ui_core (for ANSI constants).
Does NOT import from openclaw_cli.py at module level (avoids circular imports).
Helper utilities that still live in openclaw_cli.py are lazy-imported at call
time inside the functions that need them — by that point openclaw_cli.py is
fully loaded so there is no circularity.
"""
from __future__ import annotations

import re
import shutil
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Any

# ANSI constants from ui_core (leaf module — no circular risk)
try:
    from openclaw_cli_ui_core import (
        _R, _B, _DM, _CY, _GR, _YE, _RE, _MA,
        _BCY, _BGR, _BYE, _BRE, _BBL, _IT, _UL,
        _get_is_tty,
    )
except ImportError:
    # Fallback when running standalone before ui_core is available
    _R = _B = _DM = _CY = _GR = _YE = _RE = _MA = ""
    _BCY = _BGR = _BYE = _BRE = _BBL = _IT = _UL = ""

    def _get_is_tty() -> bool:
        return sys.stdout.isatty()


# ---------------------------------------------------------------------------
# Module-level regex constants (re-defined here to avoid any openclaw_cli import)
# ---------------------------------------------------------------------------

_RE_KV_BOLD = re.compile(r"\*\*[^*]+:\*\*")
_RE_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^\)]+)\)")
_RE_BARE_URL = re.compile(r"(https?://\S+)")
_RE_SOURCES_BLOCK = re.compile(
    r"\n{1,2}(?:\*\*Sources\*\*|Sources):?\s*\n((?:(?:[-\*]|\d+\.)\s+.+\n?)+)",
    re.IGNORECASE,
)
_RE_SOURCES_BLOCK_LOOSE = re.compile(
    r"(?:^|\n)(?:\*\*Sources\*\*|Sources):?\s*\n((?:(?:[-\*]|\d+\.)\s+.+\n?)+)",
    re.IGNORECASE | re.MULTILINE,
)
_RE_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_MD_TABLE_BLOCK = re.compile(
    r"(?m)^(\|[^\n]+\n\|[-:| ]+\|(?:\n\|[^\n]+)*)",
)
_URL_PATTERN = re.compile(r"(https?://[^\s\)\]\>\"\']+)", re.IGNORECASE)

# Heading emoji map (mirrors openclaw_cli._HEADING_EMOJIS)
_HEADING_EMOJIS: dict[int, str] = {
    1: "✨",
    2: "🔹",
    3: "▸",
    4: "·",
}


# ---------------------------------------------------------------------------
# RenderContext — snapshot of runtime state, built by print_response()
# ---------------------------------------------------------------------------

@dataclass
class RenderContext:
    """Snapshot of runtime rendering state passed to render helpers."""
    is_tty: bool
    is_rich: bool
    high_contrast: bool
    plain_mode: bool
    cols: int
    theme_ansi: str = ""          # pre-computed _theme_ansi() result
    prefs: dict = field(default_factory=dict)
    console: Any = None           # _RICH_CONSOLE instance
    Panel: Any = None             # _RichPanel class
    Text: Any = None              # _RichText class
    Rule: Any = None              # _RichRule class
    Table: Any = None             # _RichTable class
    Markdown: Any = None          # _RichMarkdown class


# ---------------------------------------------------------------------------
# Pure inline helpers (no globals, no ctx)
# ---------------------------------------------------------------------------

def _apply_inline_ansi(text: str) -> str:
    """Apply inline bold, italic, and code formatting via ANSI codes."""
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: f"{_B}{m.group(1)}{_R}", text)
    text = re.sub(r"__(.+?)__", lambda m: f"{_B}{m.group(1)}{_R}", text)
    text = re.sub(r"\*([^*\n]+?)\*", lambda m: f"{_IT}{m.group(1)}{_R}", text)
    text = re.sub(r"`([^`\n]+?)`", lambda m: f"{_CY}{m.group(1)}{_R}", text)
    return text


def _strip_inline_md(text: str) -> str:
    """Strip common inline markdown markers (bold, italic, code) from a cell string."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.strip().strip("*").strip()


def _separator_fill(width: int, *, high_contrast: bool = False, plain_mode: bool = False) -> str:
    """Return a separator line of *width* chars suited to the current mode."""
    char = "=" if high_contrast or plain_mode else "─"
    return char * max(1, width)


def _response_footer_lines(
    *, elapsed: float = 0.0, tokens: int = 0, model: str = ""
) -> tuple[str, str]:
    """Return (headline, detail) strings for the response footer."""
    parts: list[str] = []
    if elapsed > 0:
        parts.append(f"⏱ {elapsed:.1f}s")
    if tokens:
        parts.append(f"{tokens} tokens")
    if model:
        parts.append(model)
    detail = "  •  ".join(parts)
    if elapsed > 0:
        headline = f"✨ Response complete in {elapsed:.1f}s"
    else:
        headline = "✨ Response complete"
    return headline, detail


def _motion_pause(stage: str) -> None:
    """Motion-choreography pause — no-op stub; real pacing lives in openclaw_cli."""


# ---------------------------------------------------------------------------
# Table parsing helpers (pure)
# ---------------------------------------------------------------------------

def _is_kv_bullet_group(lines: list[str]) -> bool:
    """Return True if all lines look like pipe-separated key:value bullet rows."""
    for line in lines:
        content = re.sub(r"^[•\-\*]\s+", "", line.lstrip())
        content = re.sub(r"^\*(.+)\*$", r"\1", content.strip())
        if _RE_KV_BOLD.search(content):
            continue
        segments = [s.strip() for s in content.split(" | ")]
        if len(segments) < 2:
            return False
        colon_count = sum(1 for s in segments if ":" in s)
        if colon_count < len(segments) // 2 + 1:
            return False
    return True


def _bullet_group_to_table(lines: list[str]) -> list[str]:
    """Convert pipe-in-bullet lines to a markdown table."""
    headers: list[str] = []
    rows: list[list[str]] = []
    for line in lines:
        content = re.sub(r"^[•\-\*]\s+", "", line.lstrip())
        content = re.sub(r"^\*(.+)\*$", r"\1", content.strip())
        parts = [p.strip() for p in content.split(" | ")]
        row_headers: list[str] = []
        row_values: list[str] = []
        for part in parts:
            part = re.sub(r"^\*+", "", part).strip()
            m = re.match(r"\*\*([^*:]+):\*\*\s*(.*)", part)
            if m:
                row_headers.append(m.group(1).strip())
                row_values.append(m.group(2).strip())
            else:
                colon_idx = part.find(":")
                if colon_idx > 0:
                    row_headers.append(part[:colon_idx].strip())
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
    """Unwrap fenced code blocks that contain only pipe-in-bullet table rows."""
    def _replace(m: re.Match) -> str:
        content = m.group(1).strip()
        non_empty = [l for l in content.split("\n") if l.strip()]
        if len(non_empty) >= 2 and all(
            re.match(r"^[•\-\*]\s+.+$", l) and " | " in l
            for l in non_empty
        ):
            return content
        return m.group(0)

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


# ---------------------------------------------------------------------------
# ANSI table renderer (needs ctx for cols/high_contrast/plain_mode)
# ---------------------------------------------------------------------------

def _render_table_ansi(rows: list[list[str]], ctx: RenderContext) -> list[str]:
    """Render a list of rows as an ANSI-aligned table, capped to terminal width."""
    if not rows:
        return []
    num_cols = max(len(r) for r in rows)
    w = ctx.cols

    def _plain(cell: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", r"\1", re.sub(r"\*(.+?)\*", r"\1", cell))

    plain_rows = [[_plain(cell) for cell in row[:num_cols]] for row in rows]
    col_widths = [0] * num_cols
    for row in plain_rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))
    estimated_total = sum(col_widths) + num_cols * 3 + 1

    hc = ctx.high_contrast
    pm = ctx.plain_mode
    theme = ctx.theme_ansi

    if w < 80 or estimated_total > max(20, w - 4):
        headers = plain_rows[0] if plain_rows else []
        result: list[str] = []
        sep_core = _separator_fill(max(1, w - 4), high_contrast=hc, plain_mode=pm)
        sep_style = theme if hc else _DM
        sep_reset = _R if sep_style else ""
        sep = f"  {sep_style}{sep_core}{sep_reset}"
        for row_i, row in enumerate(rows):
            if row_i == 0:
                continue
            result.append(sep)
            for j in range(num_cols):
                cell = row[j] if j < len(row) else ""
                header = headers[j] if j < len(headers) else f"Col {j + 1}"
                available = max(12, w - len(header) - 8)
                wrapped = textwrap.wrap(_plain(cell), width=available) or [""]
                rendered = _apply_inline_ansi(wrapped[0])
                result.append(f"  {_B}{header}:{_R} {rendered}")
                indent = " " * (len(header) + 4)
                for continuation in wrapped[1:]:
                    result.append(f"{indent}{_apply_inline_ansi(continuation)}")
            result.append("")
        if result:
            result.append(sep)
        return result

    max_col_width = max(10, (w - 4) // num_cols)
    col_widths = [min(cw, max_col_width) for cw in col_widths]

    terminal_width = w - 4
    total = sum(col_widths) + num_cols * 3 + 1
    if total > terminal_width and sum(col_widths) > 0:
        available = max(num_cols * 6, terminal_width - num_cols * 3 - 1)
        scale = available / sum(col_widths)
        col_widths = [max(6, int(cw * scale)) for cw in col_widths]

    sep_len = min(sum(col_widths) + num_cols * 3 + 1, terminal_width)
    sep_style = theme if hc else _DM
    sep_reset = _R if sep_style else ""
    sep = f"  {sep_style}{_separator_fill(sep_len, high_contrast=hc, plain_mode=pm)}{sep_reset}"

    result = [sep]
    for row_i, row in enumerate(rows):
        cells = []
        for j in range(num_cols):
            cell = row[j] if j < len(row) else ""
            plain = _plain(cell)
            max_w = col_widths[j]
            if len(plain) > max_w:
                plain = plain[: max_w - 1] + "…"
                cell = plain
            formatted = _apply_inline_ansi(cell)
            cells.append(formatted + " " * (max_w - len(plain)))
        result.append("  " + (" │ ".join(cells)).rstrip())
        if row_i == 0:
            result.append(sep)
    result.append(sep)
    return result


# ---------------------------------------------------------------------------
# Link helpers (ctx-aware)
# ---------------------------------------------------------------------------

def _make_clickable_link(url: str, text: str = "", *, ctx: RenderContext) -> str:
    """Return an OSC 8 clickable hyperlink if supported, otherwise plain URL."""
    if not ctx.prefs.get("clickable_links", True) or ctx.plain_mode:
        return text or url
    if not ctx.is_tty:
        return text or url
    display = text or url
    return f"\033]8;;{url}\033\\{_UL}{_CY}{display}{_R}\033]8;;\033\\"


def _linkify_response(text: str, ctx: RenderContext) -> str:
    """Replace bare URLs in response text with OSC 8 clickable links."""
    if not ctx.prefs.get("clickable_links", True) or ctx.plain_mode:
        return text
    if not ctx.is_tty:
        return text

    lines = text.split("\n")
    result = []
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code and not line.startswith("|"):
            line = _URL_PATTERN.sub(
                lambda m: _make_clickable_link(m.group(1), ctx=ctx), line
            )
        result.append(line)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Rich table renderer (ctx-aware)
# ---------------------------------------------------------------------------

def _render_md_table_rich(
    headers: list[str], rows: list[list[str]], ctx: RenderContext
) -> None:
    """Render a parsed markdown table using Rich with sensible column widths."""
    term_cols = ctx.cols
    n = len(headers)
    if n == 0:
        return

    MAX_COL = 24
    MIN_COL = 5
    natural: list[int] = []
    for i, h in enumerate(headers):
        cell_max = max((len(r[i]) if i < len(r) else 0) for r in rows) if rows else 0
        natural.append(max(MIN_COL, min(max(len(h), cell_max), MAX_COL)))

    overhead = n * 3 + 1
    available = term_cols - overhead
    total_natural = sum(natural)

    if total_natural <= available:
        col_widths = natural
    else:
        scale = max(0.3, available / total_natural)
        col_widths = [max(MIN_COL, int(w * scale)) for w in natural]

    table = ctx.Table(
        border_style="bold white" if ctx.high_contrast else "dim",
        show_edge=True,
        pad_edge=True,
        header_style="bold bright_white" if ctx.high_contrast else "bold cyan",
    )
    for i, (h, w) in enumerate(zip(headers, col_widths)):
        overflow_mode = "fold" if i == 0 else "ellipsis"
        table.add_column(h, max_width=w, overflow=overflow_mode, no_wrap=(i > 0))

    for row in rows:
        cells = list(row) + [""] * max(0, n - len(row))
        table.add_row(*cells[:n])

    ctx.console.print(table)


# ---------------------------------------------------------------------------
# Core render functions
# ---------------------------------------------------------------------------

def _inject_heading_emojis(text: str, ctx: RenderContext) -> str:
    """Prepend emoji to markdown headings based on level."""
    if not ctx.prefs.get("emoji_headers", True) or ctx.plain_mode:
        return text
    lines = text.split("\n")
    result = []
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("#"):
            m = re.match(r"^(#{1,4}) (.+)$", line)
            if m:
                level = len(m.group(1))
                emoji = _HEADING_EMOJIS.get(level, "")
                if emoji:
                    line = f"{m.group(1)} {emoji} {m.group(2)}"
        result.append(line)
    return "\n".join(result)


def _render_markdown_ansi(text: str, ctx: RenderContext) -> str:
    """Convert markdown to ANSI-formatted terminal text (fallback when Rich is absent).

    Handles headings (H1–H4), bold/italic/code, blockquotes, tables, bullet
    lists (including nested), numbered lists, fenced code blocks, and rules.
    """
    term_cols = ctx.cols
    rule_width = min(term_cols - 2, 72) if term_cols >= 80 else max(1, term_cols - 4)
    plain_mode = ctx.plain_mode
    narrow = term_cols < 72
    border_style = ctx.theme_ansi if ctx.high_contrast else _DM
    border_reset = _R if border_style else ""

    lines = text.split("\n")
    result: list[str] = []
    in_code = False
    code_lang = ""
    table_rows: list[list[str]] = []

    def flush_table() -> None:
        if table_rows:
            result.extend(_render_table_ansi(table_rows, ctx))
            table_rows.clear()

    for line in lines:
        # Fenced code blocks
        if line.startswith("```"):
            flush_table()
            if not in_code:
                in_code = True
                code_lang = line[3:].strip()
                lang_label = f" {code_lang} " if code_lang else " code "
                if plain_mode or narrow:
                    result.append(f"  {lang_label.strip()}:")
                else:
                    result.append(
                        f"  {border_style}╭─{lang_label}"
                        f"{_separator_fill(max(0, rule_width - len(lang_label) - 3), high_contrast=False)}╮{border_reset}"
                    )
            else:
                in_code = False
                if not (plain_mode or narrow):
                    result.append(
                        f"  {border_style}╰"
                        f"{_separator_fill(rule_width - 1, high_contrast=False)}╯{border_reset}"
                    )
                code_lang = ""
            continue
        if in_code:
            prefix = "    " if (plain_mode or narrow) else f"  {border_style}│{border_reset} "
            result.append(f"{prefix}{_CY}{line}{_R}")
            continue

        # Markdown table rows
        if line.startswith("|"):
            stripped = line.strip().strip("|")
            if re.match(r"^[-| :]+$", stripped):
                continue
            cells = [c.strip() for c in stripped.split("|")]
            table_rows.append(cells)
            continue
        else:
            flush_table()

        # Horizontal rule
        if re.match(r"^[-*_]{3,}\s*$", line):
            fill = _separator_fill(rule_width, high_contrast=ctx.high_contrast, plain_mode=plain_mode)
            style = "" if plain_mode else border_style
            reset = border_reset if style else ""
            result.append(f"{style}{fill}{reset}")
            continue

        # Blockquotes
        bq = re.match(r"^>\s?(.*)", line)
        if bq:
            quote_marker = ">" if (plain_mode or narrow) else "▌"
            quote_style = "" if plain_mode else border_style
            reset = border_reset if quote_style else ""
            result.append(f"  {quote_style}{quote_marker}{reset}  {_apply_inline_ansi(bq.group(1))}")
            continue

        # ATX headings
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            raw = m.group(2)
            if not plain_mode and ctx.prefs.get("emoji_headers", True):
                emoji = _HEADING_EMOJIS.get(level, "")
                if emoji:
                    raw = f"{emoji} {raw}"
            content = _apply_inline_ansi(raw)
            if level == 1:
                result.append(f"\n{_B}{_UL}{content}{_R}")
                result.append("")
            elif level == 2:
                result.append(f"\n{_B}{content}{_R}")
            elif level == 3:
                result.append(f"{_B}{_DM}{content}{_R}")
            else:
                result.append(f"{_DM}{_IT}{content}{_R}")
            continue

        # Bullet list (supports nested via leading whitespace)
        bm = re.match(r"^(\s*)[-*•]\s+(.*)", line)
        if bm:
            indent = bm.group(1)
            depth = len(indent) // 2
            bullet = "◦" if depth % 2 else "•"
            result.append(f"  {'  ' * depth}{bullet} {_apply_inline_ansi(bm.group(2))}")
            continue

        # Numbered list
        nm = re.match(r"^(\s*)(\d+)\.\s+(.*)", line)
        if nm:
            indent = nm.group(1)
            result.append(f"  {indent}{nm.group(2)}. {_apply_inline_ansi(nm.group(3))}")
            continue

        # Wrap long paragraph lines
        if len(line) > term_cols - 2 and not plain_mode:
            plain_line = re.sub(r"\*{1,2}([^*]+)\*{1,2}|`([^`]+)`|_([^_]+)_", r"\1\2\3", line)
            wrapped_lines = textwrap.wrap(plain_line, width=term_cols - 2) or [line]
            for wl in wrapped_lines:
                result.append(_apply_inline_ansi(wl))
        else:
            result.append(_apply_inline_ansi(line))

    flush_table()
    return "\n".join(result)


def _auto_bold_response(text: str, ctx: RenderContext) -> str:
    """Apply auto-bolding to key terms in AI response text.

    Post-processes the response body to make dollar amounts, percentages,
    and filenames visually pop. Skips fenced code blocks, table rows, and
    blockquotes. Only active when auto_bold pref is True and not in plain mode.
    """
    if ctx.plain_mode or not ctx.prefs.get("auto_bold", True):
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

        # 1. Dollar amounts
        line = re.sub(
            r"(?<!\*)\$(\d[\d,\.]*(?:\s*(?:million|billion|trillion|thousand|[KMBkmb]))?)\b(?!\*)",
            r"**$\1**",
            line,
        )
        # 2. Percentages
        line = re.sub(
            r"(?<!\*)(\d+(?:\.\d+)?%)(?!\*)",
            r"**\1**",
            line,
        )
        # 3. File extensions
        line = re.sub(
            r"(?<![`\w])(\w[\w\-]*\.(?:py|md|json|yaml|yml|sh|txt|js|ts|go|rs|html|css))(?![`\w])",
            r"`\1`",
            line,
        )

        result.append(line)

    return "\n".join(result)


def _preprocess_response_text(text: str) -> tuple[str, str | None]:
    """Clean up raw LLM response text for better CLI rendering.

    Returns (cleaned_body, sources) where sources may be None.

    Steps:
      A. Strip recovery note blocks.
      B. Strip trailing ``_via model-name_`` trailer.
      C. Extract the Sources section.
      D. Strip inline [N] citation markers.
      E. Unwrap fenced code blocks that contain only pipe-in-bullet table rows.
      F. Convert pipe-in-bullet table patterns to proper markdown tables.
    """
    # A. Strip server-appended recovery note blocks
    text = re.sub(
        r"\n{1,2}> ℹ️ \*\*Recovery note:\*\*\n(?:> [^\n]*\n?)*",
        "",
        text,
    )
    text = re.sub(
        r"\n{1,2}ℹ️ \*?\*?Recovery note\*?\*?:?[^\n]*\n(?:[^\n]*\n?){0,6}",
        "",
        text,
    )

    # B. Strip _via model_ trailer
    text = re.sub(r"\n_via [^\n]+_[ \t]*(?=\n|$)", "", text)
    text = text.rstrip()

    # C. Extract Sources block
    sources: str | None = None
    all_matches = list(_RE_SOURCES_BLOCK.finditer(text))
    if all_matches:
        best = max(all_matches, key=lambda m: len(m.group(1)))
        sources = best.group(0).strip()
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

    # D. Strip bare inline citation markers like [1], [2]
    text = re.sub(r"\[(\d{1,2})\](?!\()", "", text)

    # E. Unwrap fenced code blocks that are really pipe-in-bullet tables
    text = _unwrap_code_block_tables(text)

    # F. Convert pipe-in-bullet table patterns to real markdown tables
    text = _convert_bullet_tables(text)

    return text, sources


def _clean_sources_for_display(sources: str) -> list[tuple[str, str]]:
    """Extract clean (display, url) tuples from a sources block.

    Handles bare URLs, markdown links, and numbered/bulleted prefixes.
    """
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in sources.splitlines():
        line = line.strip()
        line = re.sub(r"^(?:\d+\.|[-\*])\s+", "", line)
        line = line.strip()
        if not line:
            continue
        md = _RE_MD_LINK.search(line)
        if md:
            text, url = md.group(1).strip(), md.group(2).strip()
            display = text if text and text != url else url
            display = _RE_ANSI_ESCAPE.sub("", display).strip()
            if not display or "http://" in display or "https://" in display:
                display = url
            if url not in seen:
                seen.add(url)
                results.append((display, url))
            continue
        bare = _RE_BARE_URL.search(line)
        if bare:
            url = bare.group(1).rstrip(")")
            if url not in seen:
                seen.add(url)
                results.append((url, url))
    return results


def _render_body_with_tables(body: str, ctx: RenderContext) -> None:
    """Render response body, using a smart Rich Table for any markdown table blocks."""
    last_end = 0
    for m in _MD_TABLE_BLOCK.finditer(body):
        pre = body[last_end : m.start()].strip()
        if pre:
            ctx.console.print(ctx.Markdown(pre))
        parsed = _parse_md_table(m.group(0))
        if parsed:
            _render_md_table_rich(*parsed, ctx)
        else:
            ctx.console.print(ctx.Markdown(m.group(0)))
        last_end = m.end()
    remaining = body[last_end:].strip()
    if remaining:
        ctx.console.print(ctx.Markdown(remaining))


def _render_response_body(
    text: str,
    sources: str | None,
    ctx: RenderContext,
) -> None:
    """Render the main response body (Rich tables or ANSI markdown) plus inline sources panel."""
    if not text.strip():
        text = "_No response text returned._"
    # Safety: strip any Sources section still in body (regex miss fallback)
    text = re.sub(
        r"\n{0,2}(?:\*\*Sources\*\*|Sources):?\s*\n(?:(?:[-\*]|\d+\.)\s+.+\n?)+",
        "",
        text,
        flags=re.MULTILINE,
    ).rstrip()
    if not text.strip():
        text = "_No response text returned._"
    if ctx.is_rich and ctx.is_tty:
        _render_body_with_tables(text, ctx)
        if sources:
            src_items = _clean_sources_for_display(sources)
            src_text = ctx.Text()
            for i, (display, url) in enumerate(src_items):
                if i > 0:
                    src_text.append("\n")
                src_text.append(f"{i + 1}. ", style="dim")
                if display != url:
                    src_text.append(display, style="bold")
                    src_text.append("  ", style="")
                src_text.append(url, style="cyan link " + url)
            if not src_items:
                src_text = ctx.Text(sources, style="dim")
            ctx.console.print(
                ctx.Panel(
                    src_text,
                    title="[dim]📎 Sources[/]",
                    border_style="bold white" if ctx.high_contrast else "dim blue",
                    padding=(0, 1),
                )
            )
    elif ctx.is_tty:
        # Rich not available but interactive TTY — use ANSI markdown renderer
        print(_render_markdown_ansi(_linkify_response(text, ctx), ctx))
        if sources:
            src_items = _clean_sources_for_display(sources)
            w = max(shutil.get_terminal_size((ctx.cols or 80, 24)).columns - 2, 40)
            border_style = ctx.theme_ansi if ctx.high_contrast else _DM
            border_reset = _R if border_style else ""
            print(
                f"\n  {border_style}╭─ 📎 Sources "
                f"{_separator_fill(max(0, w - 14), high_contrast=False)}╮{border_reset}"
            )
            for i, (display, url) in enumerate(src_items or [(sources, sources)]):
                label = f"{i + 1}. " if src_items else ""
                display_clean = re.sub(r"[\x00-\x1f\x7f]", "", display)
                if display_clean == url or "http" in display_clean or not display_clean.strip():
                    name_part = ""
                else:
                    name_part = f"{_B}{display_clean}{_R}  "
                link = (
                    _make_clickable_link(url, ctx=ctx)
                    if ctx.prefs.get("clickable_links", True) and ctx.prefs.get("rich", True)
                    else f"{_CY}{url}{_R}"
                )
                print(f"  {border_style}│{border_reset}  {_DM}{label}{_R}{name_part}{link}")
            print(
                f"  {border_style}╰"
                f"{_separator_fill(w - 1, high_contrast=False)}╯{border_reset}"
            )
    else:
        print(text)
        if sources:
            print(f"\nSources:\n{sources}")


def _render_response_footer(
    model: str | None,
    tokens: int | None,
    elapsed: float,
    ctx: RenderContext,
) -> None:
    """Render the timing/model footer rule below the response body."""
    if not (model or tokens or elapsed > 0):
        return
    headline, footer = _response_footer_lines(
        elapsed=elapsed,
        tokens=tokens or 0,
        model=model or "",
    )
    if ctx.is_rich and ctx.is_tty:
        _motion_pause("footer")
        ctx.console.print(ctx.Rule(style="bold white" if ctx.high_contrast else "dim"))
        headline_style = "bold white" if ctx.high_contrast else "bold cyan"
        footer_style = "bold white" if ctx.high_contrast else "dim"
        ctx.console.print(f"[{headline_style}]{headline}[/]")
        if footer:
            ctx.console.print(f"[{footer_style}]{footer}[/]")
    elif ctx.is_tty:
        print()
        headline_style = ctx.theme_ansi if ctx.high_contrast else _BCY
        footer_style = ctx.theme_ansi if ctx.high_contrast else _DM
        headline_reset = _R if headline_style else ""
        footer_reset = _R if footer_style else ""
        print(f"{headline_style}{headline}{headline_reset}")
        if footer:
            print(f"{footer_style}{footer}{footer_reset}")
    else:
        print()
        print(headline)
        if footer:
            print(f"Metadata: {footer}")
