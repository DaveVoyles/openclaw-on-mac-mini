#!/usr/bin/env python3
"""Scan tracked files for private/personal data that must not be public.

This repo is published on GitHub (see .github/docs/README.md). This scanner is
the automated enforcement of that policy: it fails if a commit introduces the
owner's personal contact info, a live Slack workspace invite, a real secret
token, or a tracked credential file.

It is intentionally narrow and low-noise: it flags high-confidence private data
only. Things that are acceptable for a homelab showcase (private LAN IPs like
192.168.x.x, personal home paths like /Users/<name>) are NOT flagged.

Usage:
    python3 scripts/scan_private_data.py            # scan tracked files, exit 1 on findings
    python3 scripts/scan_private_data.py --verbose  # also list what was scanned

Exit codes:
    0 = clean (no private data found)
    1 = findings (private data detected) OR a tracked credential file exists
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Real personal contact info that must never appear in the public repo.
# Add any additional owner-personal addresses here.
PERSONAL_EMAILS: tuple[str, ...] = ("you@example.com",)

# Files that legitimately contain example/forbidden patterns (policy docs,
# this scanner, and its tests). They are skipped wholesale.
ALLOWLISTED_FILES: frozenset[str] = frozenset(
    {
        "scripts/scan_private_data.py",
        "tests/test_scan_private_data.py",
        ".github/docs/README.md",
    }
)

# Credential files that must never be tracked by git (gitignored at rest).
TRACKED_CREDENTIAL_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.local",
    ".env.*.local",
    "*.key",
    "*.pem",
    "data/user_dropbox_tokens.json",
)

# A match is treated as a placeholder / test fixture (not a real leak) if its
# lowercased text contains any of these markers.
PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "your",
    "xxxx",
    "example",
    "placeholder",
    "fake",
    "dummy",
    "redacted",
    "changeme",
    "token-here",
    "test",
    "...",
    "<",
    "abcdef",
    "0123456789",
    "1234567890",
)

# (name, human description, compiled pattern). Patterns use word boundaries to
# avoid matching substrings like "ta[sk-]status".
PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "slack-invite",
        "Live Slack workspace invite link",
        re.compile(r"join\.slack\.com/t/[\w-]+/shared_invite/[\w/~+-]+"),
    ),
    (
        "slack-bot-token",
        "Slack bot/user token",
        re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{12,}"),
    ),
    (
        "github-pat",
        "GitHub personal access token",
        re.compile(r"\bghp_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"),
    ),
    (
        "openai-anthropic-key",
        "OpenAI/Anthropic API key",
        re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"),
    ),
    (
        "aws-access-key",
        "AWS access key id",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "google-api-key",
        "Google API key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    ),
    (
        "private-key",
        "Private key block",
        re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----"),
    ),
)


def repo_root() -> Path:
    """Return the git repository root."""
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(out.stdout.strip())


def tracked_files(root: Path) -> list[str]:
    """Return repo-relative paths of all git-tracked files."""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def tracked_credential_files(root: str | Path) -> list[str]:
    """Return tracked credential files that should never be committed."""
    out = subprocess.run(
        ["git", "ls-files", *TRACKED_CREDENTIAL_GLOBS],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    found = [line for line in out.stdout.splitlines() if line]
    # .env.example is the documented placeholder template and is allowed.
    return [f for f in found if f != ".env.example"]


def is_placeholder(text: str) -> bool:
    """True if the matched text is an obvious placeholder or test fixture."""
    lowered = text.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def scan_line(line: str) -> list[tuple[str, str, str]]:
    """Return (pattern_name, description, matched_text) findings for one line."""
    findings: list[tuple[str, str, str]] = []
    for email in PERSONAL_EMAILS:
        if email in line:
            findings.append(("personal-email", "Owner's personal email", email))
    for name, desc, pattern in PATTERNS:
        for match in pattern.finditer(line):
            text = match.group(0)
            if is_placeholder(text):
                continue
            findings.append((name, desc, text))
    return findings


def scan_file(root: Path, rel_path: str) -> list[tuple[int, str, str, str]]:
    """Scan a single tracked file. Returns (lineno, name, desc, text) findings."""
    path = root / rel_path
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []  # binary or unreadable — skip
    results: list[tuple[int, str, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, desc, matched in scan_line(line):
            results.append((lineno, name, desc, matched))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan tracked files for private/personal data before going public.")
    parser.add_argument("--verbose", action="store_true", help="Print how many files were scanned.")
    args = parser.parse_args(argv)

    root = repo_root()
    files = tracked_files(root)

    findings: list[tuple[str, int, str, str, str]] = []
    for rel_path in files:
        if rel_path in ALLOWLISTED_FILES:
            continue
        for lineno, name, desc, matched in scan_file(root, rel_path):
            findings.append((rel_path, lineno, name, desc, matched))

    cred_files = tracked_credential_files(root)

    if args.verbose:
        print(f"Scanned {len(files)} tracked files.")

    if not findings and not cred_files:
        print("✅ No private/personal data found in tracked files.")
        return 0

    print("❌ Private/personal data detected — must be removed before this repo is public.\n")
    if cred_files:
        print("Tracked credential files (must be gitignored, never committed):")
        for f in cred_files:
            print(f"  - {f}")
        print()
    if findings:
        print("Private-data matches:")
        for rel_path, lineno, name, desc, matched in findings:
            print(f"  {rel_path}:{lineno}: [{name}] {desc}: {matched}")
        print()
    print(
        "If a match is a deliberate placeholder/example, make it obviously fake "
        "(include 'example', 'YOUR', or 'xxxx'),\n"
        "or add the file to ALLOWLISTED_FILES in scripts/scan_private_data.py with a reason.\n"
        "See .github/docs/README.md for the public-repo policy."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
