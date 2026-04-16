#!/usr/bin/env python3
"""Validate internal markdown links used by repo docs."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATTERNS = ("README.md", "docs/**/*.md", "scripts/README.md")
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")


def iter_docs(paths: list[str] | None = None) -> list[Path]:
    if paths:
        docs: set[Path] = set()
        for raw_path in paths:
            candidate = (REPO_ROOT / raw_path).resolve()
            try:
                candidate.relative_to(REPO_ROOT)
            except ValueError:
                continue
            if candidate.is_file() and candidate.suffix == ".md":
                docs.add(candidate)
        return sorted(docs)

    docs: set[Path] = set()
    for pattern in DOC_PATTERNS:
        docs.update(path for path in REPO_ROOT.glob(pattern) if path.is_file())
    return sorted(docs)


def normalize_target(raw: str) -> str | None:
    target = raw.strip()
    if not target or target.startswith(SKIP_PREFIXES):
        return None
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if "#" in target:
        target = target.split("#", 1)[0]
    return target or None


def is_valid_target(source: Path, target: str) -> bool:
    resolved = (source.parent / target).resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        return False
    return resolved.exists()


def main(argv: list[str] | None = None) -> int:
    docs = iter_docs(argv)
    if not docs:
        print("No markdown files to check")
        return 0

    failures: list[str] = []
    for doc in docs:
        rel_doc = doc.relative_to(REPO_ROOT)
        for line_no, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
            for match in LINK_RE.finditer(line):
                raw_target = match.group(1)
                target = normalize_target(raw_target)
                if target is None:
                    continue
                if not is_valid_target(doc, target):
                    failures.append(
                        f"{rel_doc}:{line_no}: missing link target '{raw_target}'"
                    )

    if failures:
        print("Broken relative markdown links found:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("Markdown links OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
