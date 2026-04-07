from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_dashboard_helpers_module():
    repo_root = Path(__file__).resolve().parent.parent
    helpers_path = repo_root / "src" / "dashboard" / "helpers.py"
    spec = importlib.util.spec_from_file_location("dashboard_helpers_for_docs_test", helpers_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_command_docs_match_runtime_source_of_truth():
    helpers = _load_dashboard_helpers_module()
    docs_path = Path(__file__).resolve().parent.parent / "docs" / "COMMANDS.md"

    expected = helpers.render_command_reference_markdown()
    actual = docs_path.read_text()

    if not actual.endswith("\n"):
        actual = f"{actual}\n"
    if not expected.endswith("\n"):
        expected = f"{expected}\n"

    assert actual == expected, (
        "docs/COMMANDS.md is out of sync with dashboard command source. "
        "Regenerate it from src/dashboard/helpers.py::render_command_reference_markdown()."
    )
