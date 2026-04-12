#!/usr/bin/env python3
"""Wrapper entrypoint for the OpenClaw terminal client."""

import sys
from importlib import import_module
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

main = import_module("openclaw_cli").main


if __name__ == "__main__":
    raise SystemExit(main())
