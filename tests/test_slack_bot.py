"""
Tests for pure helper functions in src/slack_bot.py.

Mocks Slack/aiohttp dependencies at import time so no live connection is needed.
"""

from __future__ import annotations

import os
import sys
import types
import unittest

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

from slack_bot import (  # noqa: E402
    _HELP_TEXT,
    _WELCOME_MESSAGE,
    _parse_flags,
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
        self.assertIn("--simple", _HELP_TEXT)


if __name__ == "__main__":
    unittest.main()
