"""Discord message formatting utilities for OpenClaw bot.

Handles markdown conversion, table rendering, and message splitting for Discord embeds.
"""

import io
import re
from typing import Literal

import discord

from constants import EMBED_DESC_LIMIT, EMBED_SPLIT_LIMIT

# Regex patterns for formatting
_IMAGE_LINK_RE = re.compile(r'!\[.*?\]\((https?://[^\s)]+)\)')
_BARE_IMAGE_RE = re.compile(r'\b(https?://[^\s]+\.(?:png|jpg|jpeg|gif|webp))\b', re.IGNORECASE)
_CODE_BLOCK_RE = re.compile(r"```(\w+)?\n([\s\S]+?)```")
_FENCED_BLOCK_RE = re.compile(r"```[^\n]*\n[\s\S]*?```")
TableFormatMode = Literal["discord", "copy-safe"]

# Discord embed split limit shared with bot.py so helper behavior stays consistent.
_EMBED_LIMIT = EMBED_SPLIT_LIMIT


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


def _is_markdown_table_separator(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and all(c in "|-: " for c in stripped.replace("|", ""))


def _parse_markdown_table_rows(table_lines: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in table_lines:
        stripped = line.strip()
        if _is_markdown_table_separator(stripped):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        cleaned = []
        for cell in cells:
            cell = cell.strip("*")
            cell = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cell)
            cleaned.append(cell)
        rows.append(cleaned)
    return rows


def _render_discord_table(rows: list[list[str]]) -> list[str]:
    num_cols = max(len(r) for r in rows)
    col_widths = [0] * num_cols
    for row in rows:
        for j, cell in enumerate(row):
            if j < num_cols:
                col_widths[j] = max(col_widths[j], len(cell))

    border = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    rendered = ["```text", border]
    for idx, cells in enumerate(rows):
        padded = []
        for j in range(num_cols):
            cell = cells[j] if j < len(cells) else ""
            padded.append(f" {cell:<{col_widths[j]}} ")
        rendered.append("|" + "|".join(padded) + "|")
        if idx == 0:
            rendered.append(border)
    rendered.extend([border, "```"])
    return rendered


def _render_copy_safe_table(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    header = rows[0]
    body = rows[1:] if len(rows) > 1 else []
    rendered = ["📋 Table"]
    if not body:
        rendered.append("• " + " | ".join(cell or "—" for cell in header))
        return rendered

    for idx, row in enumerate(body, start=1):
        rendered.append(f"• Row {idx}")
        max_cols = max(len(header), len(row))
        for col in range(max_cols):
            label = header[col] if col < len(header) and header[col] else f"Column {col + 1}"
            value = row[col] if col < len(row) and row[col] else "—"
            rendered.append(f"  - {label}: {value}")
    return rendered


def format_tables(text: str, mode: TableFormatMode = "discord") -> str:
    """Convert markdown tables to formatted blocks optimized for the requested mode."""
    lines = text.split("\n")
    result: list[str] = []
    table_lines: list[str] = []
    in_table = False
    in_code_block = False

    def _flush_table(tlines: list[str]) -> None:
        rows = _parse_markdown_table_rows(tlines)
        if not rows:
            result.extend(tlines)
            return

        if mode == "copy-safe":
            result.extend(_render_copy_safe_table(rows))
        else:
            result.extend(_render_discord_table(rows))

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_table:
                _flush_table(table_lines)
                in_table = False
                table_lines = []
            in_code_block = not in_code_block
            result.append(line)
            continue
        if in_code_block:
            result.append(line)
            continue

        is_table_row = stripped.startswith("|") and stripped.endswith("|")
        is_separator = is_table_row and _is_markdown_table_separator(stripped)

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


def format_tables_for_discord(text: str) -> str:
    """Convert markdown tables to clean, padded text code blocks for Discord."""
    return format_tables(text, mode="discord")


def format_tables_for_copy(text: str) -> str:
    """Convert markdown tables into copy-safe text blocks for thread/detail responses."""
    return format_tables(text, mode="copy-safe")


def _split_plain_segment(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    lines = text.splitlines(keepends=True)
    for line in lines:
        if len(line) > limit:
            if current:
                chunks.append(current.rstrip("\n"))
                current = ""
            start = 0
            while start < len(line):
                piece = line[start:start + limit]
                start += limit
                if start < len(line):
                    piece = piece[:-1] + "…"
                chunks.append(piece.rstrip("\n"))
            continue
        if len(current) + len(line) > limit:
            chunks.append(current.rstrip("\n"))
            current = line
            continue
        current += line
    if current or not chunks:
        chunks.append(current.rstrip("\n"))
    return chunks


def _split_code_block_segment(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]

    first_newline = block.find("\n")
    if first_newline == -1:
        return _split_plain_segment(block, limit)

    opener = block[:first_newline + 1]
    inner_and_close = block[first_newline + 1:]
    if not inner_and_close.endswith("```"):
        return _split_plain_segment(block, limit)

    inner = inner_and_close[:-3]
    payload_limit = limit - len(opener) - 4
    if payload_limit < 40:
        return _split_plain_segment(block, limit)

    payload_chunks = _split_plain_segment(inner, payload_limit)
    wrapped: list[str] = []
    for chunk in payload_chunks:
        payload = chunk.rstrip("\n")
        wrapped.append(f"{opener}{payload}\n```")
    return wrapped


def split_response(text: str, limit: int = _EMBED_LIMIT) -> list[str]:
    """Split a long response into chunks that fit within Discord's embed limit."""
    if len(text) <= limit:
        return [text]

    pieces: list[str] = []
    cursor = 0
    for match in _FENCED_BLOCK_RE.finditer(text):
        if match.start() > cursor:
            pieces.extend(_split_plain_segment(text[cursor:match.start()], limit))
        pieces.extend(_split_code_block_segment(match.group(0), limit))
        cursor = match.end()
    if cursor < len(text):
        pieces.extend(_split_plain_segment(text[cursor:], limit))

    chunks: list[str] = []
    for piece in pieces:
        if not piece:
            continue
        if not chunks:
            chunks.append(piece)
            continue
        candidate = chunks[-1] + piece
        if len(candidate) <= limit:
            chunks[-1] = candidate
        else:
            chunks.append(piece)
    if not chunks:
        return [""]
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
