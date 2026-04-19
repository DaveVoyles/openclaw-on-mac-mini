"""
OpenClaw Table Renderer — Renders markdown tables as PNG images for Discord.

Discord doesn't support markdown tables natively. This module converts
markdown table text into a clean PNG image that can be attached to messages.
"""

import io
import logging
import re
from typing import Optional

log = logging.getLogger(__name__)


def _parse_markdown_table(text: str) -> Optional[tuple[list[str], list[list[str]]]]:
    """Parse a markdown table into headers and rows.

    Returns (headers, rows) or None if no valid table found.
    """
    lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
    if len(lines) < 2:
        return None

    table_lines = [ln for ln in lines if ln.startswith("|") and ln.endswith("|")]
    if len(table_lines) < 2:
        return None

    def parse_row(line: str) -> list[str]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        cleaned = []
        for c in cells:
            c = c.strip("* ")
            c = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', c)
            cleaned.append(c)
        return cleaned

    headers = None
    rows = []
    for line in table_lines:
        if all(c in "|-: " for c in line.replace("|", "")):
            continue  # separator
        if headers is None:
            headers = parse_row(line)
        else:
            rows.append(parse_row(line))

    if not headers or not rows:
        return None
    return headers, rows


def render_table_image(text: str) -> Optional[bytes]:
    """Render a markdown table as a PNG image.

    Returns PNG bytes or None if no table found or Pillow unavailable.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.debug("Pillow not installed — table image rendering unavailable")
        return None

    parsed = _parse_markdown_table(text)
    if not parsed:
        return None

    headers, rows = parsed
    num_cols = len(headers)

    # Normalize row lengths
    for row in rows:
        while len(row) < num_cols:
            row.append("")

    # Try to load a monospace font
    font_size = 16
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", font_size)
        bold_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", font_size)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSansMono.ttf", font_size)
            bold_font = font
        except (OSError, IOError):
            font = ImageFont.load_default()
            bold_font = font

    # Calculate column widths
    padding = 16
    char_width = font.getlength("M") if hasattr(font, "getlength") else 10
    col_widths = []
    for j in range(num_cols):
        max_w = len(headers[j]) if j < len(headers) else 0
        for row in rows:
            if j < len(row):
                max_w = max(max_w, len(row[j]))
        col_widths.append(int(max_w * char_width + padding * 2))

    row_height = font_size + padding * 2
    header_height = row_height
    total_width = sum(col_widths) + 2  # +2 for borders
    total_height = header_height + len(rows) * row_height + 2

    # Colors (dark theme matching Discord)
    bg_color = (47, 49, 54)       # Discord dark bg
    header_bg = (32, 34, 37)      # Slightly darker header
    text_color = (220, 221, 222)  # Light text
    header_text = (255, 255, 255) # White header
    border_color = (64, 68, 75)   # Subtle borders
    alt_row_bg = (54, 57, 63)     # Alternating row

    img = Image.new("RGB", (total_width, total_height), bg_color)
    draw = ImageDraw.Draw(img)

    y = 0

    # Draw header row
    draw.rectangle([0, y, total_width, y + header_height], fill=header_bg)
    x = 1
    for j, header in enumerate(headers):
        draw.text((x + padding, y + padding), header, fill=header_text, font=bold_font)
        x += col_widths[j]
        # Vertical line
        draw.line([(x, y), (x, y + header_height)], fill=border_color, width=1)
    # Horizontal line under header
    y += header_height
    draw.line([(0, y), (total_width, y)], fill=border_color, width=1)

    # Draw data rows
    for i, row in enumerate(rows):
        row_bg = alt_row_bg if i % 2 == 1 else bg_color
        draw.rectangle([0, y, total_width, y + row_height], fill=row_bg)
        x = 1
        for j in range(num_cols):
            cell = row[j] if j < len(row) else ""
            draw.text((x + padding, y + padding), cell, fill=text_color, font=font)
            x += col_widths[j]
            draw.line([(x, y), (x, y + row_height)], fill=border_color, width=1)
        y += row_height
        draw.line([(0, y), (total_width, y)], fill=border_color, width=1)

    # Save to bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.getvalue()


def should_render_table_image(
    table_text: str,
    *,
    min_rows_for_image: int = 8,
    min_cols_for_image: int = 5,
    min_cell_chars_for_image: int = 48,
    max_table_width_chars: int = 60,
) -> bool:
    """Return True when a table is large/complex enough to benefit from image fallback.

    Triggers on: 5+ columns, table wider than 60 chars, 8+ rows, or longest cell >= 48 chars.
    """
    parsed = _parse_markdown_table(table_text)
    if not parsed:
        return False
    headers, rows = parsed
    cols = len(headers)
    row_count = len(rows)
    longest_cell = max(
        [len(cell) for cell in headers] + [len(cell) for row in rows for cell in row],
        default=0,
    )
    # Estimate rendered table width: sum of max column widths + separators
    all_rows = [headers] + rows
    col_widths = [
        max((len(r[j]) if j < len(r) else 0) for r in all_rows)
        for j in range(cols)
    ]
    table_width = sum(col_widths) + (cols + 1) * 3
    return (
        row_count >= min_rows_for_image
        or cols >= min_cols_for_image
        or longest_cell >= min_cell_chars_for_image
        or table_width > max_table_width_chars
    )


def extract_table_text(text: str) -> Optional[str]:
    """Extract the first markdown table from text (for image rendering)."""
    lines = text.split("\n")
    table_lines = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        is_table = stripped.startswith("|") and stripped.endswith("|")
        if is_table:
            if not in_table:
                in_table = True
            table_lines.append(line)
        elif in_table:
            break  # End of table

    return "\n".join(table_lines) if table_lines else None
