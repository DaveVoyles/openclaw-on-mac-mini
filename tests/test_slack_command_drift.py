"""Tests for the Slack command drift checker (scripts/check_slack_command_drift.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_slack_command_drift.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_slack_command_drift", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_no_drift_in_current_repo():
    """The committed handlers and manifest must agree (no drift)."""
    module = _load_module()
    assert module.main() == 0


def test_every_handler_is_registered_or_excluded():
    module = _load_module()
    handlers = module.handler_commands()
    registered = set(module.manifest_commands())
    excluded = set(module.ALLOWED_UNREGISTERED)
    missing = handlers - registered - excluded
    assert not missing, f"handlers missing from manifest and not excluded: {sorted(missing)}"


def test_manifest_within_slack_cap():
    module = _load_module()
    assert len(module.manifest_commands()) <= module.SLACK_COMMAND_CAP


def test_detects_unregistered_handler(monkeypatch):
    """A new handler that isn't in the manifest must be flagged as drift."""
    module = _load_module()
    original = module.handler_commands
    monkeypatch.setattr(module, "handler_commands", lambda: original() | {"/brandnew"})
    assert module.main() == 1


def test_allowed_unregistered_are_real_handlers():
    """Every intentional exclusion must still be a real handler (no stale entries)."""
    module = _load_module()
    handlers = module.handler_commands()
    stale = set(module.ALLOWED_UNREGISTERED) - handlers
    assert not stale, f"ALLOWED_UNREGISTERED has stale entries: {sorted(stale)}"
