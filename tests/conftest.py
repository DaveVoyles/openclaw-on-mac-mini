"""
Pytest configuration for OpenClaw tests.
Adds the project root to sys.path so all source modules are importable.
"""

import sys
from pathlib import Path

# Make sure the project root is on the path for all test modules
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
