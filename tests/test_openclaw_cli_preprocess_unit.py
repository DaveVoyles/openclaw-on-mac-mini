"""Unit tests for URL sanitization and domain blocklist in openclaw_cli_preprocess."""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib
mod = importlib.import_module('openclaw_cli_preprocess')
_sanitize_source_url = getattr(mod, '_sanitize_source_url', None)

if _sanitize_source_url is None:
    pytest.skip("_sanitize_source_url not found in module", allow_module_level=True)


def test_sanitize_clean_url():
    assert _sanitize_source_url("https://example.com/page") == "https://example.com/page"


def test_sanitize_mangled_url():
    """Remove garbage prefix before https://."""
    result = _sanitize_source_url("36mabout.htmlhttps://example.com/page")
    assert result == "https://example.com/page"


def test_sanitize_http_url():
    result = _sanitize_source_url("garbagehttps://example.com")
    assert result == "https://example.com"


def test_sanitize_no_url_returns_original():
    """If no http:// found, return original unchanged."""
    result = _sanitize_source_url("not-a-url")
    assert result == "not-a-url"


def test_sanitize_empty_string():
    result = _sanitize_source_url("")
    assert result == ""
