#!/usr/bin/env python3
"""
OpenClaw E2E Test Runner

Reads tests/e2e/queries.yaml, fires each question through `openclaw ask`,
validates the response, and prints a color-coded pass/fail table.

Usage:
    python3 scripts/run_e2e_tests.py                    # all tests, local
    python3 scripts/run_e2e_tests.py --host macbook     # all tests via SSH
    python3 scripts/run_e2e_tests.py --id box-office-weekend
    python3 scripts/run_e2e_tests.py --verbose          # show full response on failure
    python3 scripts/run_e2e_tests.py --timeout 60       # per-test timeout (default 45s)

Exit code:
    0  — all tests passed
    1  — one or more tests failed
    2  — configuration / connection error
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# YAML parsing (stdlib only — no PyYAML dependency)
# ---------------------------------------------------------------------------

def _parse_yaml_queries(path: Path) -> list[dict[str, Any]]:
    """Minimal YAML parser for our flat query-list format.
    Supports: list items starting with '- id:', string scalars, lists, booleans, ints.
    Falls back to PyYAML if available (better multiline support).
    """
    try:
        import yaml  # type: ignore
        with path.open() as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, list) else []
    except ImportError:
        pass

    # Stdlib fallback — line-by-line parser for our specific format
    text = path.read_text()
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_expect = False
    in_keywords = False
    question_lines: list[str] = []
    collecting_question = False

    def _coerce(v: str) -> Any:
        v = v.strip()
        if v.lower() == "true":
            return True
        if v.lower() == "false":
            return False
        try:
            return int(v)
        except ValueError:
            return v.strip("'\"")

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        # Skip comments and blank lines (unless collecting multiline question)
        if not collecting_question and (not line.strip() or line.strip().startswith("#")):
            continue

        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # New top-level list item
        if stripped.startswith("- id:"):
            if current is not None:
                if question_lines:
                    current["question"] = " ".join(question_lines).strip()
                entries.append(current)
            current = {"id": stripped[5:].strip(), "expect": {}}
            in_expect = False
            in_keywords = False
            question_lines = []
            collecting_question = False
            continue

        if current is None:
            continue

        # question field (may be multiline with >)
        if stripped.startswith("question:"):
            val = stripped[9:].strip()
            if val == ">":
                collecting_question = True
                question_lines = []
            else:
                current["question"] = val.strip("'\"")
                collecting_question = False
            continue

        if collecting_question:
            if indent >= 4:
                question_lines.append(stripped)
                continue
            else:
                collecting_question = False
                current["question"] = " ".join(question_lines).strip()

        # expect block
        if stripped.startswith("expect:"):
            in_expect = True
            in_keywords = False
            continue

        if in_expect:
            if stripped.startswith("keywords:"):
                in_keywords = True
                current["expect"]["keywords"] = []
                continue
            if in_keywords and stripped.startswith("- "):
                current["expect"]["keywords"].append(stripped[2:].strip().strip("'\""))
                continue
            else:
                in_keywords = False

            if ":" in stripped:
                k, _, v = stripped.partition(":")
                current["expect"][k.strip()] = _coerce(v)

    if current is not None:
        if question_lines:
            current["question"] = " ".join(question_lines).strip()
        entries.append(current)

    return entries


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

ERROR_PHRASES = [
    "error:", "refused the connection", "401 unauthorized",
    "traceback", "cannot reach",
]

# CLI metadata footer prefixes — excluded from error-phrase checks
_FOOTER_PREFIXES = ("⏱", "✨", "[model:", "_via ", "sources:", "http", "⚠️")


def _strip_footer(text: str) -> str:
    """Return only the body lines (strip CLI timing/metadata footer lines)."""
    lines = text.splitlines()
    body_lines = []
    for line in lines:
        stripped = line.strip()
        if any(stripped.lower().startswith(p) for p in _FOOTER_PREFIXES):
            continue
        body_lines.append(line)
    return "\n".join(body_lines)


def _validate(response: str, expect: dict[str, Any]) -> list[str]:
    """Return a list of failure reasons (empty = pass)."""
    failures: list[str] = []
    body = response.strip()

    # Always: non-empty
    if not body:
        failures.append("response is empty")
        return failures  # no point checking further

    # Strip metadata footer before error-phrase checks
    body_only = _strip_footer(body)

    # Always: no error phrases (unless expect explicitly sets no_error: false)
    if expect.get("no_error", True):
        low = body_only.lower()
        for phrase in ERROR_PHRASES:
            if phrase in low:
                failures.append(f"response contains error phrase: '{phrase}'")
                break

    # min_words
    min_words = expect.get("min_words")
    if min_words:
        word_count = len(body.split())
        if word_count < min_words:
            failures.append(f"too short: {word_count} words (expected ≥{min_words})")

    # has_table
    if expect.get("has_table"):
        table_lines = [l for l in body.splitlines() if l.strip().startswith("|") and "|" in l[1:]]
        if len(table_lines) < 3:
            failures.append(f"no markdown table found (need ≥3 pipe lines, got {len(table_lines)})")

    # paragraphs (double-newline separated blocks)
    min_paragraphs = expect.get("paragraphs")
    if min_paragraphs:
        import re
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        if len(paras) < min_paragraphs:
            failures.append(f"too few paragraphs: {len(paras)} (expected ≥{min_paragraphs})")

    # keywords — at least ONE must match
    keywords = expect.get("keywords", [])
    if keywords:
        low = body.lower()
        matched = [kw for kw in keywords if kw.lower() in low]
        if not matched:
            failures.append(f"no keywords found (expected any of: {', '.join(keywords)})")

    # model
    expected_model = expect.get("model")
    if expected_model:
        meta_lines = [l for l in body.splitlines() if "metadata:" in l.lower() or "model:" in l.lower()]
        meta_text = " ".join(meta_lines).lower()
        if expected_model.lower() not in meta_text:
            failures.append(f"model mismatch: expected '{expected_model}' in metadata footer")

    return failures


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------

# ANSI colors
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_R = "\033[0m"


def _run_query(question: str, *, host: str | None, timeout: int, openclaw_cmd: list[str]) -> tuple[str, float]:
    """Run `openclaw ask <question>` and return (stdout, elapsed_seconds)."""
    if host:
        remote_bin = openclaw_cmd[-1] if len(openclaw_cmd) == 1 else "~/.local/bin/openclaw"
        cmd = ["ssh", host, f"{remote_bin} ask {_shell_quote(question)}"]
    else:
        cmd = openclaw_cmd + ["ask", question]

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.monotonic() - t0
        output = result.stdout + result.stderr
        return output.strip(), elapsed
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return f"error: timed out after {timeout}s", elapsed
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return f"error: {exc}", elapsed


def _shell_quote(s: str) -> str:
    """Simple single-quote shell escaping for SSH commands."""
    return "'" + s.replace("'", "'\\''") + "'"


def _find_openclaw() -> list[str]:
    """Find openclaw. Returns the base command list (e.g. ['openclaw'] or ['python3', 'src/...'])."""
    candidates = [
        Path.home() / ".local/bin/openclaw",
        Path("/usr/local/bin/openclaw"),
    ]
    for p in candidates:
        if p.exists():
            return [str(p)]
    found = shutil.which("openclaw")
    if found:
        return [found]
    # Fallback: run from repo source with python3
    repo = Path(__file__).resolve().parent.parent
    cli_py = repo / "src" / "openclaw_cli.py"
    if cli_py.exists():
        return ["python3", str(cli_py)]
    return ["openclaw"]  # best-effort; will fail with a clear message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenClaw E2E test runner")
    parser.add_argument("--host", help="SSH host to run tests on (e.g. macbook)")
    parser.add_argument("--id", help="Run only the test with this ID")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print full response on failure")
    parser.add_argument("--timeout", type=int, default=45, help="Per-test timeout in seconds (default: 45)")
    parser.add_argument(
        "--queries",
        default=str(Path(__file__).resolve().parent.parent / "tests/e2e/queries.yaml"),
        help="Path to queries.yaml (default: tests/e2e/queries.yaml)",
    )
    args = parser.parse_args(argv)

    queries_path = Path(args.queries)
    if not queries_path.exists():
        print(f"{_RED}✗ queries file not found: {queries_path}{_R}", file=sys.stderr)
        return 2

    tests = _parse_yaml_queries(queries_path)
    if not tests:
        print(f"{_RED}✗ no tests found in {queries_path}{_R}", file=sys.stderr)
        return 2

    if args.id:
        tests = [t for t in tests if t.get("id") == args.id]
        if not tests:
            print(f"{_RED}✗ no test with id '{args.id}' found{_R}", file=sys.stderr)
            return 2

    openclaw_cmd = _find_openclaw()
    target_label = f"ssh:{args.host}" if args.host else "local"

    print(f"\n{_BOLD}OpenClaw E2E Test Runner{_R}  {_DIM}({target_label} • {len(tests)} test{'s' if len(tests) != 1 else ''}){_R}\n")

    results: list[tuple[str, bool, float, list[str], str]] = []  # (id, passed, elapsed, failures, response)

    for test in tests:
        test_id = test.get("id", "unknown")
        question = test.get("question", "")
        expect = test.get("expect", {})

        print(f"  {_CYAN}▶ {test_id}{_R}  {_DIM}{question[:72]}{'…' if len(question) > 72 else ''}{_R}")
        print(f"    {_DIM}running…{_R}", end="\r", flush=True)

        response, elapsed = _run_query(question, host=args.host, timeout=args.timeout, openclaw_cmd=openclaw_cmd)
        failures = _validate(response, expect)
        passed = len(failures) == 0

        status = f"{_GREEN}✅ PASS{_R}" if passed else f"{_RED}✗  FAIL{_R}"
        timing = f"{_DIM}{elapsed:.1f}s{_R}"
        words = len(response.split())
        print(f"    {status}  {timing}  {_DIM}{words} words{_R}        ")

        if not passed:
            for reason in failures:
                print(f"       {_YELLOW}↳ {reason}{_R}")
            if args.verbose:
                print(f"\n{_DIM}--- response ---{_R}")
                print(response[:2000])
                print(f"{_DIM}--- end ---{_R}\n")

        results.append((test_id, passed, elapsed, failures, response))
        print()

    # Summary table
    passed_count = sum(1 for _, p, *_ in results if p)
    failed_count = len(results) - passed_count
    total_time = sum(e for _, _, e, *_ in results)

    print("─" * 60)
    print(f"  {_BOLD}Results:{_R}  {_GREEN}{passed_count} passed{_R}  "
          f"{(_RED + str(failed_count) + ' failed' + _R) if failed_count else _DIM + '0 failed' + _R}"
          f"  {_DIM}({total_time:.1f}s total){_R}")
    print("─" * 60)

    if failed_count:
        print(f"\n{_RED}Run with --verbose to see full responses for failed tests.{_R}\n")
        return 1

    print(f"\n{_GREEN}All tests passed.{_R}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
