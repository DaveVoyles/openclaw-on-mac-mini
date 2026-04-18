"""
Tests for pure helper functions in src/slack_bot.py.

Mocks Slack/aiohttp dependencies at import time so no live connection is needed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Provide stub modules so slack_bot can be imported without real deps
# ---------------------------------------------------------------------------
for mod_name in ["slack_bolt", "slack_bolt.async_app", "aiohttp"]:
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        if mod_name == "slack_bolt.async_app":
            class AsyncApp:
                def __init__(self, *a, **kw):
                    pass

                def event(self, *a, **kw):
                    return lambda f: f

                def command(self, *a, **kw):
                    return lambda f: f

            stub.AsyncApp = AsyncApp
        sys.modules[mod_name] = stub

# Patch env vars so slack_bot imports without complaint
os.environ.setdefault("SLACK_ENABLED", "false")
os.environ.setdefault("SLACK_BOT_TOKEN", "")
os.environ.setdefault("SLACK_APP_TOKEN", "")

sys.path.insert(0, "src")

import slack_bot  # noqa: E402  (imported after stubs)
from slack_bot import (  # noqa: E402
    _HELP_TEXT,
    _WELCOME_MESSAGE,
    _get_user_simple,
    _parse_flags,
    _set_user_simple,
    _suggest_actions_for_file,
)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSlackBot(unittest.TestCase):

    # -- _suggest_actions_for_file -------------------------------------------

    def test_suggest_actions_word_docx(self):
        result = _suggest_actions_for_file(
            "report.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertIn("📄", result)
        self.assertIn("report.docx", result)

    def test_suggest_actions_word_doc(self):
        result = _suggest_actions_for_file("letter.doc", "application/msword")
        self.assertIn("📄", result)
        self.assertIn("letter.doc", result)

    def test_suggest_actions_excel_xlsx(self):
        result = _suggest_actions_for_file(
            "Budget2025.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("📊", result)
        self.assertIn("Budget2025.xlsx", result)

    def test_suggest_actions_csv(self):
        result = _suggest_actions_for_file("data.csv", "text/csv")
        self.assertIn("📊", result)
        self.assertIn("data.csv", result)

    def test_suggest_actions_pdf(self):
        result = _suggest_actions_for_file("contract.pdf", "application/pdf")
        self.assertIn("📑", result)
        self.assertIn("contract.pdf", result)

    def test_suggest_actions_image_jpeg(self):
        result = _suggest_actions_for_file("photo.jpg", "image/jpeg")
        self.assertIn("🖼️", result)
        self.assertIn("photo.jpg", result)

    def test_suggest_actions_image_png(self):
        result = _suggest_actions_for_file("screenshot.png", "image/png")
        self.assertIn("🖼️", result)

    def test_suggest_actions_unknown(self):
        result = _suggest_actions_for_file("archive.zip", "application/zip")
        self.assertIn("📎", result)
        self.assertIn("archive.zip", result)

    # -- _parse_flags --------------------------------------------------------

    def test_parse_flags_simple_only(self):
        text, model, simple = _parse_flags("--simple")
        self.assertEqual(text, "")
        self.assertEqual(model, "auto")
        self.assertTrue(simple)

    def test_parse_flags_model_and_simple(self):
        text, model, simple = _parse_flags("hello --model gemini --simple")
        self.assertEqual(text, "hello")
        self.assertEqual(model, "gemini")
        self.assertTrue(simple)

    def test_parse_flags_no_flags(self):
        text, model, simple = _parse_flags("what is the weather?")
        self.assertEqual(text, "what is the weather?")
        self.assertEqual(model, "auto")
        self.assertFalse(simple)

    # -- Constants -----------------------------------------------------------

    def test_welcome_message_not_empty(self):
        self.assertIsInstance(_WELCOME_MESSAGE, str)
        self.assertTrue(len(_WELCOME_MESSAGE) > 0)
        self.assertIn("OpenClaw", _WELCOME_MESSAGE)

    def test_help_text_not_empty(self):
        self.assertIsInstance(_HELP_TEXT, str)
        self.assertTrue(len(_HELP_TEXT) > 0)
        self.assertIn("/simple on", _HELP_TEXT)

    # -- User preferences ----------------------------------------------------

    def setUp(self):
        # Redirect prefs to a temp file for isolation
        self._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp.close()
        self._orig_path = slack_bot._PREFS_PATH
        slack_bot._PREFS_PATH = Path(self._tmp.name)
        # Reset in-memory prefs
        slack_bot._user_prefs = {}

    def tearDown(self):
        slack_bot._PREFS_PATH = self._orig_path
        slack_bot._user_prefs = {}
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_user_simple_default_false(self):
        self.assertFalse(_get_user_simple("U999"))

    def test_user_simple_set_on(self):
        _set_user_simple("U001", True)
        self.assertTrue(_get_user_simple("U001"))

    def test_user_simple_set_off(self):
        _set_user_simple("U001", True)
        _set_user_simple("U001", False)
        self.assertFalse(_get_user_simple("U001"))

    def test_user_simple_persisted_to_disk(self):
        _set_user_simple("U002", True)
        raw = json.loads(Path(self._tmp.name).read_text())
        self.assertTrue(raw["U002"]["simple"])

    def test_user_simple_loaded_from_disk(self):
        # Pre-populate the file
        Path(self._tmp.name).write_text(json.dumps({"U003": {"simple": True}}))
        slack_bot._load_prefs()
        self.assertTrue(_get_user_simple("U003"))

    def test_user_simple_multiple_users_independent(self):
        _set_user_simple("U010", True)
        _set_user_simple("U011", False)
        self.assertTrue(_get_user_simple("U010"))
        self.assertFalse(_get_user_simple("U011"))

    def test_user_simple_unknown_user_default(self):
        _set_user_simple("U010", True)
        # U099 was never set → should default to False
        self.assertFalse(_get_user_simple("U099"))

    # -- Block Kit block builder --------------------------------------------

    def test_build_file_blocks_document_has_buttons(self):
        from slack_bot import _build_file_blocks
        blocks = _build_file_blocks("report.docx", "A quarterly report", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "F123")
        # Should have a section + actions block
        types = [b["type"] for b in blocks]
        self.assertIn("section", types)
        self.assertIn("actions", types)

    def test_build_file_blocks_image_has_describe_button(self):
        from slack_bot import _build_file_blocks
        blocks = _build_file_blocks("photo.jpg", None, "image/jpeg", "F456")
        actions_block = next(b for b in blocks if b["type"] == "actions")
        action_ids = [e["action_id"] for e in actions_block["elements"]]
        self.assertIn("file_describe", action_ids)

    def test_build_file_blocks_document_has_proofread_button(self):
        from slack_bot import _build_file_blocks
        blocks = _build_file_blocks("letter.docx", None, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "F789")
        actions_block = next(b for b in blocks if b["type"] == "actions")
        action_ids = [e["action_id"] for e in actions_block["elements"]]
        self.assertIn("file_proofread", action_ids)

    def test_build_file_blocks_description_in_section(self):
        from slack_bot import _build_file_blocks
        blocks = _build_file_blocks("budget.xlsx", "A monthly budget with 3 sheets", "application/vnd.ms-excel", "F999")
        section_text = next(b for b in blocks if b["type"] == "section")["text"]["text"]
        self.assertIn("A monthly budget with 3 sheets", section_text)

    def test_register_file_stored_and_retrievable(self):
        from slack_bot import _file_registry, _register_file
        _register_file("FTEST1", {"name": "test.docx", "mimetype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"})
        self.assertIn("FTEST1", _file_registry)
        self.assertEqual(_file_registry["FTEST1"]["name"], "test.docx")


if __name__ == "__main__":
    unittest.main()
