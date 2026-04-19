"""
OpenClaw JSON Utilities — Phase 2: Structured Output
Provides JSON validation, repair, and extraction helpers.

Used to clean up tool results and parse structured LLM outputs.
"""

import json
import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)


def try_parse_json(text: str) -> Optional[dict | list]:
    """Try to parse text as JSON, returning None on failure."""
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def extract_json_block(text: str) -> Optional[str]:
    """Extract a JSON block from text that may contain markdown fences or surrounding prose.

    Handles:
    - ```json ... ``` blocks
    - ``` ... ``` blocks
    - Raw JSON objects/arrays
    """
    # Try markdown-fenced JSON
    match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', text)
    if match:
        return match.group(1).strip()

    # Try to find raw JSON object or array
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            c = text[i]
            if escape_next:
                escape_next = False
                continue
            if c == '\\' and in_string:
                escape_next = True
                continue
            if c == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == start_char:
                depth += 1
            elif c == end_char:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

    return None


def repair_json(text: str) -> Optional[dict | list]:
    """Attempt to repair and parse malformed JSON.

    Handles common issues:
    - Trailing commas
    - Single quotes instead of double quotes
    - Unquoted keys
    - Missing closing brackets
    - Comments (// and /* */)
    """
    if not text or not text.strip():
        return None

    # First try direct parse
    result = try_parse_json(text)
    if result is not None:
        return result

    # Try extracting JSON block from surrounding text
    extracted = extract_json_block(text)
    if extracted:
        result = try_parse_json(extracted)
        if result is not None:
            return result
        text = extracted

    # Apply repairs
    repaired = text

    # Remove comments
    repaired = re.sub(r'//[^\n]*', '', repaired)
    repaired = re.sub(r'/\*[\s\S]*?\*/', '', repaired)

    # Replace single quotes with double quotes (only when no double quotes exist)
    if '"' not in repaired and "'" in repaired:
        repaired = repaired.replace("'", '"')

    # Remove trailing commas before } or ]
    repaired = re.sub(r',\s*([}\]])', r'\1', repaired)

    # Try to add missing closing brackets
    open_braces = repaired.count('{') - repaired.count('}')
    open_brackets = repaired.count('[') - repaired.count(']')
    if open_braces > 0:
        repaired += '}' * open_braces
    if open_brackets > 0:
        repaired += ']' * open_brackets

    result = try_parse_json(repaired)
    if result is not None:
        log.debug("Repaired JSON successfully")
        return result

    # Try quoting unquoted keys
    try:
        repaired = re.sub(r'(?<=[{,\n])\s*([a-zA-Z_]\w*)\s*:', r' "\1":', repaired)
        result = try_parse_json(repaired)
        if result is not None:
            log.debug("Repaired JSON (quoted keys)")
            return result
    except (re.error, json.JSONDecodeError, ValueError):
        pass

    log.debug("JSON repair failed for: %.100s…", text)
    return None


def format_tool_result(result: Any, tool_name: str = "") -> str:
    """Format a tool result for clean presentation.

    If the result is JSON-parseable, pretty-prints it.
    Otherwise returns the string as-is.
    """
    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, indent=2, default=str)
        except (TypeError, ValueError):
            return str(result)

    if isinstance(result, str):
        parsed = try_parse_json(result)
        if parsed is not None:
            return json.dumps(parsed, indent=2, default=str)

    return str(result)
