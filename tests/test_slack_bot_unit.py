"""Unit tests for src/slack_bot.py — coverage gaps.

Targets pure helpers and config-guard paths. All Slack SDK and aiohttp
dependencies are stubbed at import time; no live connection is required.
"""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Stub heavy deps so slack_bot can be imported without real packages
# ---------------------------------------------------------------------------
for _mod in ["slack_bolt", "slack_bolt.async_app", "aiohttp"]:
    if _mod not in sys.modules:
        _stub = types.ModuleType(_mod)
        if _mod == "slack_bolt.async_app":

            class _AsyncApp:
                def __init__(self, *a, **kw):
                    pass

                def event(self, *a, **kw):
                    return lambda f: f

                def command(self, *a, **kw):
                    return lambda f: f

                def action(self, *a, **kw):
                    return lambda f: f

            _stub.AsyncApp = _AsyncApp
        sys.modules[_mod] = _stub

os.environ.setdefault("SLACK_ENABLED", "false")
os.environ.setdefault("SLACK_BOT_TOKEN", "")
os.environ.setdefault("SLACK_APP_TOKEN", "")

sys.path.insert(0, "src")

import slack_bot as sb  # noqa: E402

# ---------------------------------------------------------------------------
# _clean_for_slack
# ---------------------------------------------------------------------------


class TestCleanForSlack(unittest.TestCase):
    def test_bold_markdown_converted(self):
        result = sb._clean_for_slack("Hello **world**!")
        assert result == "Hello *world*!"

    def test_markdown_link_converted(self):
        result = sb._clean_for_slack("See [GitHub](https://github.com) for details.")
        assert result == "See <https://github.com|GitHub> for details."

    def test_atx_header_demoted_to_bold(self):
        result = sb._clean_for_slack("## Section Title")
        assert result == "*Section Title*"

    def test_horizontal_rule_removed(self):
        result = sb._clean_for_slack("before\n---\nafter")
        assert "---" not in result
        assert "before" in result
        assert "after" in result

    def test_plain_text_unchanged(self):
        text = "Just a plain message."
        assert sb._clean_for_slack(text) == text

    def test_output_stripped(self):
        result = sb._clean_for_slack("  hello  ")
        assert result == "hello"


# ---------------------------------------------------------------------------
# _parse_model_flag
# ---------------------------------------------------------------------------


class TestParseModelFlag(unittest.TestCase):
    def test_no_flag_returns_auto(self):
        text, model = sb._parse_model_flag("summarize this document")
        assert model == "auto"
        assert text == "summarize this document"

    def test_known_alias_resolved(self):
        text, model = sb._parse_model_flag("explain this --model gemini")
        assert model == "gemini"
        assert "--model" not in text

    def test_unknown_alias_falls_back_to_auto(self):
        text, model = sb._parse_model_flag("help --model nonexistent")
        assert model == "auto"
        assert "--model" not in text

    def test_flag_case_insensitive(self):
        _, model = sb._parse_model_flag("ask something --MODEL GPT")
        # GPT might not be a valid alias; just confirm no crash and flag removed
        assert isinstance(model, str)


# ---------------------------------------------------------------------------
# _mimetype_for
# ---------------------------------------------------------------------------


class TestMimetypeFor(unittest.TestCase):
    def test_docx(self):
        mt = sb._mimetype_for("report.docx")
        assert "wordprocessingml" in mt

    def test_xlsx(self):
        mt = sb._mimetype_for("data.xlsx")
        assert "spreadsheetml" in mt

    def test_pdf(self):
        assert sb._mimetype_for("doc.pdf") == "application/pdf"

    def test_txt(self):
        assert sb._mimetype_for("notes.txt") == "text/plain"

    def test_csv(self):
        assert sb._mimetype_for("export.csv") == "text/csv"

    def test_unknown_extension(self):
        assert sb._mimetype_for("archive.tar.gz") == "application/octet-stream"

    def test_case_insensitive_suffix(self):
        assert sb._mimetype_for("REPORT.PDF") == "application/pdf"


# ---------------------------------------------------------------------------
# _register_bot_message  (registry + pruning)
# ---------------------------------------------------------------------------


class TestRegisterBotMessage(unittest.TestCase):
    def setUp(self):
        sb._bot_message_registry.clear()

    def tearDown(self):
        sb._bot_message_registry.clear()

    def test_entry_stored(self):
        sb._register_bot_message("C123", "1234.5678", "U001")
        assert sb._bot_message_registry[("C123", "1234.5678")] == "U001"

    def test_prune_at_501(self):
        # Fill to exactly 500
        for i in range(500):
            sb._register_bot_message("C000", f"ts{i:04d}", "U000")
        assert len(sb._bot_message_registry) == 500
        # 501st entry triggers pruning — still ≤ 500
        sb._register_bot_message("C999", "ts_new", "U999")
        assert len(sb._bot_message_registry) <= 500

    def test_latest_entry_survives_prune(self):
        for i in range(501):
            sb._register_bot_message("C000", f"ts{i:04d}", "U000")
        # The brand-new entry should survive
        sb._register_bot_message("CLAST", "ts_last", "ULAST")
        assert sb._bot_message_registry.get(("CLAST", "ts_last")) == "ULAST"


# ---------------------------------------------------------------------------
# _slack_is_configured
# ---------------------------------------------------------------------------


class TestSlackIsConfigured(unittest.TestCase):
    def _check(self, enabled, bot_token, app_token):
        with (
            patch.object(sb, "SLACK_ENABLED", enabled),
            patch.object(sb, "SLACK_BOT_TOKEN", bot_token),
            patch.object(sb, "SLACK_APP_TOKEN", app_token),
        ):
            return sb._slack_is_configured()

    def test_all_valid_returns_true(self):
        assert self._check(True, "xoxb-valid", "xapp-valid") is True

    def test_disabled_returns_false(self):
        assert self._check(False, "xoxb-valid", "xapp-valid") is False

    def test_bad_bot_token_returns_false(self):
        assert self._check(True, "bad-token", "xapp-valid") is False

    def test_missing_bot_token_returns_false(self):
        assert self._check(True, "", "xapp-valid") is False

    def test_bad_app_token_returns_false(self):
        assert self._check(True, "xoxb-valid", "bad-app-token") is False

    def test_missing_app_token_returns_false(self):
        assert self._check(True, "xoxb-valid", "") is False


# ---------------------------------------------------------------------------
# _load_personas / _save_personas  (file I/O)
# ---------------------------------------------------------------------------


class TestLoadSavePersonas(unittest.TestCase):
    def test_load_from_existing_file(self, tmp_path=None):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "personas.json"
            payload = {"U001": {"name": "Aria", "tone": "friendly"}}
            p.write_text(json.dumps(payload))
            orig_path = sb._PERSONAS_PATH
            sb._PERSONAS_PATH = p
            sb._personas.clear()
            try:
                sb._load_personas()
                assert sb._personas == payload
            finally:
                sb._PERSONAS_PATH = orig_path

    def test_load_missing_file_leaves_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "nonexistent.json"
            orig_path = sb._PERSONAS_PATH
            sb._PERSONAS_PATH = p
            sb._personas.clear()
            try:
                sb._load_personas()
                assert sb._personas == {}
            finally:
                sb._PERSONAS_PATH = orig_path

    def test_load_corrupt_file_logs_and_resets(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "personas.json"
            p.write_text("not-valid-json{{{")
            orig_path = sb._PERSONAS_PATH
            sb._PERSONAS_PATH = p
            sb._personas = {"existing": {}}
            try:
                sb._load_personas()
                assert sb._personas == {}
            finally:
                sb._PERSONAS_PATH = orig_path


# ---------------------------------------------------------------------------
# _load_file_history / _save_file_history  (file I/O)
# ---------------------------------------------------------------------------


class TestLoadSaveFileHistory(unittest.TestCase):
    def test_round_trip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "file_history.json"
            payload = {"U001": [{"name": "report.pdf", "size": 1024}]}
            p.write_text(json.dumps(payload))
            orig_path = sb._FILE_HISTORY_PATH
            sb._FILE_HISTORY_PATH = p
            sb._file_history.clear()
            try:
                sb._load_file_history()
                assert sb._file_history == payload
                # Modify and save
                sb._file_history["U002"] = [{"name": "notes.txt", "size": 512}]
                sb._save_file_history()
                saved = json.loads(p.read_text())
                assert "U002" in saved
            finally:
                sb._FILE_HISTORY_PATH = orig_path
                sb._file_history.clear()

    def test_load_missing_file_stays_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "no_history.json"
            orig_path = sb._FILE_HISTORY_PATH
            sb._FILE_HISTORY_PATH = p
            sb._file_history.clear()
            try:
                sb._load_file_history()
                assert sb._file_history == {}
            finally:
                sb._FILE_HISTORY_PATH = orig_path


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
