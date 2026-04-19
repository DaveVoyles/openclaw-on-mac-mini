#!/usr/bin/env python3
"""
validate_schema.py — Cross-check config/env_schema.yaml against .env.example.

Usage:
  python3 scripts/validate_schema.py           # Exit 1 on any gap
  python3 scripts/validate_schema.py --warn-only   # Print gaps but exit 0
  python3 scripts/validate_schema.py --fix-hints   # Suggest additions

Intended for CI and pre-commit validation.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml  # type: ignore[import]
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def load_schema_vars(schema_path: Path) -> set[str]:
    """Extract variable names from env_schema.yaml."""
    if not HAS_YAML:
        print("⚠️  PyYAML not installed — skipping schema load")
        return set()
    data = yaml.safe_load(schema_path.read_text()) or {}
    return set((data.get("variables") or {}).keys())


def load_example_vars(example_path: Path) -> set[str]:
    """Extract variable names from .env.example."""
    vars_: set[str] = set()
    for line in example_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Z][A-Z0-9_]+)\s*=", line)
        if match:
            vars_.add(match.group(1))
    return vars_


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Print warnings but always exit 0",
    )
    parser.add_argument(
        "--fix-hints",
        action="store_true",
        help="Print YAML snippets for undocumented vars",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    schema_path = repo_root / "config" / "env_schema.yaml"
    example_path = repo_root / ".env.example"

    if not schema_path.exists():
        print(f"❌ Schema not found: {schema_path}")
        return 0 if args.warn_only else 1

    if not example_path.exists():
        print(f"❌ .env.example not found: {example_path}")
        return 0 if args.warn_only else 1

    schema_vars = load_schema_vars(schema_path)
    example_vars = load_example_vars(example_path)

    undocumented = example_vars - schema_vars  # in .env.example but not in schema
    missing = schema_vars - example_vars        # in schema but not in .env.example

    print(f"📋 Schema vars:   {len(schema_vars)}")
    print(f"📋 Example vars:  {len(example_vars)}")

    if undocumented:
        print(f"\n⚠️  Undocumented in schema ({len(undocumented)} vars):")
        for v in sorted(undocumented):
            print(f"   {v}")
        if args.fix_hints:
            print("\n   YAML to add to config/env_schema.yaml:")
            for v in sorted(undocumented):
                print(
                    f"   {v}:\n"
                    f"     required: false\n"
                    f"     type: string\n"
                    f'     description: "TODO"\n'
                )

    if missing:
        print(f"\n⚠️  In schema but missing from .env.example ({len(missing)} vars):")
        for v in sorted(missing):
            print(f"   {v}")

    if not undocumented and not missing:
        print("\n✅ Schema and .env.example are in sync!")
        return 0

    gap_count = len(undocumented) + len(missing)
    prefix = "⚠️ " if args.warn_only else "❌"
    print(f"\n{prefix} {gap_count} gap(s) found.")
    return 0 if args.warn_only else 1


if __name__ == "__main__":
    sys.exit(main())
