#!/usr/bin/env python3
"""Validate .env against .env.example to catch missing required variables.

Usage:
    python3 scripts/validate_env.py           # check .env
    python3 scripts/validate_env.py --strict  # exit 1 if any required var missing
"""
import argparse
import sys
from pathlib import Path


def load_env_file(path: Path) -> dict[str, str | None]:
    """Parse a .env file into a dict of key -> value (None if no value)."""
    result: dict[str, str | None] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip() or None
        else:
            result[line] = None
    return result


def find_required_keys(example_path: Path) -> set[str]:
    """Find keys marked as REQUIRED in .env.example comments."""
    required: set[str] = set()
    lines = example_path.read_text().splitlines()
    for i, line in enumerate(lines):
        if "# REQUIRED" in line.upper():
            # The REQUIRED comment may be on the same line as the key
            if "=" in line:
                key = line.split("=")[0].strip().lstrip("#").strip()
                required.add(key)
            # Or it may precede the key on the next line
            elif i + 1 < len(lines) and "=" in lines[i + 1]:
                key = lines[i + 1].split("=")[0].strip()
                required.add(key)
    return required


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate .env against .env.example")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if required vars missing")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    parser.add_argument("--example", default=".env.example", help="Path to .env.example")
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    env_path = repo_root / args.env
    example_path = repo_root / args.example

    if not example_path.exists():
        print(f"❌ .env.example not found at {example_path}")
        return 1

    example_keys = set(load_env_file(example_path).keys())
    env_keys = set(load_env_file(env_path).keys())
    required_keys = find_required_keys(example_path)

    missing_all = example_keys - env_keys
    missing_required = required_keys - env_keys
    extra_keys = env_keys - example_keys

    print(f"📋 .env.example: {len(example_keys)} vars | .env: {len(env_keys)} vars")
    print(f"   Required: {len(required_keys)} | Missing required: {len(missing_required)}")

    if missing_required:
        print(f"\n❌ Missing REQUIRED variables ({len(missing_required)}):")
        for k in sorted(missing_required):
            print(f"   - {k}")

    if missing_all:
        print(f"\n⚠️  Missing optional variables ({len(missing_all)}) — set if feature needed:")
        for k in sorted(missing_all)[:10]:
            print(f"   - {k}")
        if len(missing_all) > 10:
            print(f"   ... and {len(missing_all) - 10} more")

    if extra_keys:
        print(f"\n🔍 Unknown vars in .env (not in .env.example) ({len(extra_keys)}):")
        for k in sorted(extra_keys)[:5]:
            print(f"   - {k}")

    if not missing_required and not missing_all:
        print("\n✅ All env vars present")

    return 1 if (args.strict and missing_required) else 0


if __name__ == "__main__":
    sys.exit(main())
