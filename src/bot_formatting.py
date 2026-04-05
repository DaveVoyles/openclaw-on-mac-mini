"""Discord message formatting utilities for OpenClaw bot.

Handles markdown conversion, table rendering, and message splitting for Discord embeds.
"""

import io
import re

import discord

from constants import EMBED_DESC_LIMIT

# Regex patterns for formatting
_IMAGE_LINK_RE = re.compile(r'!\[.*?\]\((https?://[^\s)]+)\)')
_BARE_IMAGE_RE = re.compile(r'\b(https?://[^\s]+\.(?:png|jpg|jpeg|gif|webp))\b', re.IGNORECASE)
_CODE_BLOCK_RE = re.compile(r"```(\w+)?\n([\s\S]+?)```")

# Discord embed limit (slightly lower than 4096 for safety)
_EMBED_LIMIT = 3900


def truncate_for_embed(text: str, limit: int = EMBED_DESC_LIMIT) -> str:
    """Truncate *text* to fit in a Discord embed description."""
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


def extract_image_url(text: str) -> str | None:
    """Return the first image URL found in the response text, or None."""
    m = _IMAGE_LINK_RE.search(text)
    if m:
        return m.group(1)
    m = _BARE_IMAGE_RE.search(text)
    if m:
        return m.group(1)
    return None


def format_markdown_for_discord(text: str) -> str:
    """Convert markdown elements that Discord embeds don't render natively."""
    lines = text.split("\n")
    result: list[str] = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue
        if in_code_block:
            result.append(line)
            continue

        header_match = re.match(r'^(#{1,3})\s+(.+)$', line)
        if header_match:
            level = len(header_match.group(1))
            heading_text = header_match.group(2).strip()
            if level == 1:
                result.append(f"__**{heading_text}**__")
            else:
                result.append(f"**{heading_text}**")
            continue

        result.append(line)

    return "\n".join(result)


def format_tables_for_discord(text: str) -> str:
    """Convert markdown tables to clean, padded ANSI code blocks for Discord."""
    lines = text.split("\n")
    result: list[str] = []
    table_lines: list[str] = []
    in_table = False

    def _flush_table(tlines: list[str]) -> None:
        rows: list[list[str]] = []
        separator_indices: list[int] = []
        for i, tl in enumerate(tlines):
            cells = [c.strip() for c in tl.strip().strip("|").split("|")]
            cleaned = []
            for c in cells:
                c = c.strip("*")
                c = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', c)
                cleaned.append(c)
            cells = cleaned
            stripped = tl.strip()
            is_sep = stripped.startswith("|") and all(c in "|-: " for c in stripped.replace("|", ""))
            if is_sep:
                separator_indices.append(i)
            else:
                rows.append(cells)

        if not rows:
            result.extend(tlines)
            return

        num_cols = max(len(r) for r in rows)
        col_widths = [0] * num_cols
        for row in rows:
            for j, cell in enumerate(row):
                if j < num_cols:
                    col_widths[j] = max(col_widths[j], len(cell))

        result.append("```ansi")

        row_idx = 0
        for i, tl in enumerate(tlines):
            if i in separator_indices:
                sep = "┼".join("─" * (w + 2) for w in col_widths)
                result.append(f"┼{sep}┼")
            else:
                if row_idx < len(rows):
                    cells = rows[row_idx]
                    padded = []
                    for j in range(num_cols):
                        cell = cells[j] if j < len(cells) else ""
                        padded.append(f" {cell:<{col_widths[j]}} ")
                    line_text = "│" + "│".join(padded) + "│"
                    if row_idx == 0:
                        line_text = f"\u001b[1;37m{line_text}\u001b[0m"
                    result.append(line_text)
                    row_idx += 1

        result.append("```")

    for line in lines:
        stripped = line.strip()
        is_table_row = stripped.startswith("|") and stripped.endswith("|")
        is_separator = is_table_row and all(c in "|-: " for c in stripped.replace("|", ""))

        if is_table_row or is_separator:
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(line)
        else:
            if in_table:
                _flush_table(table_lines)
                in_table = False
                table_lines = []
            result.append(line)

    if in_table and table_lines:
        _flush_table(table_lines)

    return "\n".join(result)


def split_response(text: str) -> list[str]:
    """Split a long response into chunks that fit within Discord's embed limit."""
    if len(text) <= _EMBED_LIMIT:
        return [text]

    chunks = []
    while text:
        if len(text) <= _EMBED_LIMIT:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, _EMBED_LIMIT)
        if split_at <= 0:
            split_at = _EMBED_LIMIT
            chunks.append(text[:split_at] + "…")
            text = "…" + text[split_at:].lstrip("\n")
        else:
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
    return chunks


def extract_file_attachment(text: str) -> tuple[discord.File, str] | None:
    """If the response contains a large code block (>500 chars), extract it as a discord.File."""
    matches = list(_CODE_BLOCK_RE.finditer(text))
    if not matches:
        return None

    best = max(matches, key=lambda m: len(m.group(2)))
    code = best.group(2).strip()
    lang = (best.group(1) or "txt").lower()

    if len(code) < 500:
        return None

    ext_map = {
        "python": "py", "py": "py", "javascript": "js", "js": "js",
        "typescript": "ts", "ts": "ts", "json": "json", "yaml": "yaml",
        "yml": "yaml", "html": "html", "css": "css", "sql": "sql",
        "bash": "sh", "sh": "sh", "csv": "csv", "markdown": "md", "md": "md",
    }
    ext = ext_map.get(lang, "txt")

    buffer = io.BytesIO(code.encode("utf-8"))
    return discord.File(buffer, filename=f"openclaw_output.{ext}"), lang
