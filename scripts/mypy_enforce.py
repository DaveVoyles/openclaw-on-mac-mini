#!/usr/bin/env python3
"""Enforce mypy strict mode on approved files."""

import subprocess
import sys

# Phase 1 strict files (should have 0 errors)
STRICT_FILES = [
    "src/config.py",
    "src/gateway.py",
]

# Phase 2+ candidates (monitor, not yet enforced)
MONITOR_FILES = [
    "src/backup_manager.py",
    "src/calendar_skills.py",
    "src/ask_handler.py",
]


def run_mypy(files: list[str], strict: bool = False) -> tuple[int, str, str]:
    """Run mypy on files, return (returncode, stdout, stderr)."""
    cmd = [
        "python3",
        "-m",
        "mypy",
        "--show-error-codes",
        "--ignore-missing-imports",
        "--follow-imports=silent",
    ]
    if strict:
        cmd.append("--disallow-untyped-defs")
    cmd.extend(files)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


# Check strict files
print("🔒 STRICT MODE FILES (enforcement):")
strict_rc, strict_out, strict_err = run_mypy(STRICT_FILES, strict=True)
print(strict_out)
if strict_err:
    print(strict_err, file=sys.stderr)

if strict_rc != 0:
    print(f"  ❌ {len(STRICT_FILES)} strict files have errors")
    sys.exit(1)

print(f"  ✅ {len(STRICT_FILES)} strict files pass")

# Check monitor files (report only)
print("\n📊 MONITOR FILES (report only, not enforced):")
monitor_rc, monitor_out, monitor_err = run_mypy(MONITOR_FILES, strict=False)

error_count = len([line for line in monitor_out.split("\n") if ": error:" in line])
warning_count = len([line for line in monitor_out.split("\n") if ": warning:" in line])
print(f"  Errors: {error_count}, Warnings: {warning_count} (not enforced)")

print("\n✅ Mypy enforcement passed")
