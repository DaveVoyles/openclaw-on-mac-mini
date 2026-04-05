#!/usr/bin/env python3
"""Check Python syntax for all files in src/."""
import ast
import pathlib
import sys

errors = []
for f in pathlib.Path("src").glob("**/*.py"):
    try:
        ast.parse(f.read_text())
    except SyntaxError as e:
        errors.append(f"{f}: {e}")

if errors:
    for e in errors:
        print(e)
    sys.exit(1)

print(f"All {len(list(pathlib.Path('src').glob('**/*.py')))} files OK")
