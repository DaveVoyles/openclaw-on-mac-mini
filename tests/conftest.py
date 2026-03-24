"""
Pytest configuration for OpenClaw tests.
Adds the project root to sys.path so all source modules are importable.
"""

import sys
from pathlib import Path

# Make sure the project root and src/ are on the path for all test modules
PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
