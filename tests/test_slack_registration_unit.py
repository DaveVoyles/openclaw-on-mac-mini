"""
test_slack_registration_unit.py — Characterization tests for create_slack_app() handler registration.

These tests act as a safety net: if a refactor drops or misregisters handlers,
these tests will catch it before CI smoke tests do.

Counts at time of last update (src/slack_bot.py):
  @app.event:   5  (app_home_opened, app_mention, message, reaction_added, file_shared)
  @app.command: 57 (/arr, /batch, /brief, /calendar, /chat, /clawbox, /clawchan, /clear,
                     /contacts, /copilot ×5, /digest, /downloads, /drive, /email, /files,
                     /filesearch, /grafana, /h, /health, /help, /hermes, /host, /inbox,
                     /incident, /media, /metrics, /morning, /mypins, /mystats, /nas,
                     /news, /nickname, /notify, /plex, /q, /qbt, /request, /research,
                     /resume, /schedule, /sessions, /simple, /status, /tailscale,
                     /template, /today, /upcoming, /uptime, /wake, /watching)
  @app.action:  9  (file_compare_start, file_translate, translate_lang_selected,
                     retry_last_prompt, clarify_file, clarify_question, clarify_write,
                     gmail_summarize, incident_action_run)
  Total:        71
"""

import ast
import os
from pathlib import Path
from unittest.mock import patch

SLACK_BOT_SRC = Path(__file__).parent.parent / "src" / "slack_bot.py"

# Exact counts at authoring time — update intentionally when adding new handlers.
EXPECTED_EVENT_COUNT = 5
EXPECTED_COMMAND_COUNT = 57  # includes /adguard, /arr, /copilot ×5 subcommand handlers, /downloads, /grafana, /h, /hermes, /host, /incident, /media, /morning, /nas (status+containers+df+ls+free), /news, /notify, /plex, /q, /qbt, /request, /resume, /sessions, /status, /tailscale, /upcoming, /uptime, /wake, /watching, etc.
EXPECTED_ACTION_COUNT = 11  # includes incident_action_run, nas_restart_confirm, nas_restart_cancel
EXPECTED_TOTAL_COUNT = EXPECTED_EVENT_COUNT + EXPECTED_COMMAND_COUNT + EXPECTED_ACTION_COUNT  # 71


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _count_app_decorators(source: str) -> dict[str, int]:
    """Parse *source* with the AST and count @app.<kind>(...) decorators."""
    tree = ast.parse(source)
    counts: dict[str, int] = {"event": 0, "command": 0, "action": 0}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # Match @app.event(...) / @app.command(...) / @app.action(...)
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "app"
                and dec.func.attr in counts
            ):
                counts[dec.func.attr] += 1

    return counts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSlackHandlerRegistration:
    def test_create_slack_app_returns_none_when_unconfigured(self):
        """Without Slack env vars, create_slack_app() returns None."""
        env_without_slack = {
            k: v
            for k, v in os.environ.items()
            if k not in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_ENABLED")
        }
        env_without_slack["SLACK_ENABLED"] = "false"

        with patch.dict(os.environ, env_without_slack, clear=True):
            # Import here to pick up the patched environment
            import importlib

            import src.slack_bot as slack_bot_mod

            importlib.reload(slack_bot_mod)
            result = slack_bot_mod.create_slack_app()

        assert result is None, "create_slack_app() must return None when Slack is not configured."

    def test_slack_registration_ast_event_count(self):
        """@app.event registrations in slack_bot.py must equal the expected baseline.

        If this test fails, a handler was added or removed. Update EXPECTED_EVENT_COUNT
        intentionally when the change is deliberate.
        """
        source = SLACK_BOT_SRC.read_text(encoding="utf-8")
        counts = _count_app_decorators(source)
        assert counts["event"] == EXPECTED_EVENT_COUNT, (
            f"Expected {EXPECTED_EVENT_COUNT} @app.event registrations, "
            f"found {counts['event']}. Update EXPECTED_EVENT_COUNT if this is intentional."
        )

    def test_slack_registration_ast_command_count(self):
        """@app.command registrations in slack_bot.py must equal the expected baseline.

        If this test fails, a command was added or removed. Update EXPECTED_COMMAND_COUNT
        intentionally when the change is deliberate.
        """
        source = SLACK_BOT_SRC.read_text(encoding="utf-8")
        counts = _count_app_decorators(source)
        assert counts["command"] == EXPECTED_COMMAND_COUNT, (
            f"Expected {EXPECTED_COMMAND_COUNT} @app.command registrations, "
            f"found {counts['command']}. Update EXPECTED_COMMAND_COUNT if this is intentional."
        )

    def test_slack_registration_ast_action_count(self):
        """@app.action registrations in slack_bot.py must equal the expected baseline.

        If this test fails, an action handler was added or removed. Update EXPECTED_ACTION_COUNT
        intentionally when the change is deliberate.
        """
        source = SLACK_BOT_SRC.read_text(encoding="utf-8")
        counts = _count_app_decorators(source)
        assert counts["action"] == EXPECTED_ACTION_COUNT, (
            f"Expected {EXPECTED_ACTION_COUNT} @app.action registrations, "
            f"found {counts['action']}. Update EXPECTED_ACTION_COUNT if this is intentional."
        )

    def test_slack_registration_ast_total_count(self):
        """Total @app.* registrations must equal the expected baseline (69).

        This is the primary characterization guard: any accidental drop in handler
        count will fail CI immediately. Update EXPECTED_TOTAL_COUNT intentionally.
        """
        source = SLACK_BOT_SRC.read_text(encoding="utf-8")
        counts = _count_app_decorators(source)
        total = sum(counts.values())
        assert total == EXPECTED_TOTAL_COUNT, (
            f"Expected {EXPECTED_TOTAL_COUNT} total @app.* registrations "
            f"(event={EXPECTED_EVENT_COUNT}, command={EXPECTED_COMMAND_COUNT}, "
            f"action={EXPECTED_ACTION_COUNT}), "
            f"found {total} (event={counts['event']}, command={counts['command']}, "
            f"action={counts['action']}). "
            "Update the EXPECTED_* constants intentionally when adding/removing handlers."
        )
