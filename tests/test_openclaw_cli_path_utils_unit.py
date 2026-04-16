"""Unit tests for openclaw_cli_path_utils.py — pure helper functions."""
from __future__ import annotations

import openclaw_cli_path_utils as mod

# ---------------------------------------------------------------------------
# _dedupe_preserve_order
# ---------------------------------------------------------------------------

def test_dedupe_empty():
    assert mod._dedupe_preserve_order([]) == []


def test_dedupe_removes_duplicates_keeps_first():
    result = mod._dedupe_preserve_order(["a", "b", "a", "c"])
    assert result == ["a", "b", "c"]


def test_dedupe_strips_whitespace_before_dedup():
    result = mod._dedupe_preserve_order(["  a  ", "b", "a"])
    assert result == ["a", "b"]


def test_dedupe_removes_empty_strings():
    result = mod._dedupe_preserve_order(["a", "", "   ", "b"])
    assert result == ["a", "b"]


def test_dedupe_preserves_order_of_first_seen():
    result = mod._dedupe_preserve_order(["z", "a", "m", "z", "a"])
    assert result == ["z", "a", "m"]


# ---------------------------------------------------------------------------
# _detect_file_paths
# ---------------------------------------------------------------------------

def test_detect_file_paths_finds_src_paths():
    text = "See src/main.py for details."
    paths = mod._detect_file_paths(text)
    assert any("src/main.py" in p for p in paths)


def test_detect_file_paths_finds_absolute_paths():
    text = "Check /home/user/project/file.txt for the config."
    paths = mod._detect_file_paths(text)
    assert any("file.txt" in p for p in paths)


def test_detect_file_paths_excludes_urls():
    text = "Visit https://example.com/path/to/resource.html"
    paths = mod._detect_file_paths(text)
    assert not any("example.com" in p for p in paths)


def test_detect_file_paths_limits_to_five():
    text = " ".join([f"src/file{i}.py" for i in range(10)])
    paths = mod._detect_file_paths(text)
    assert len(paths) <= 5


def test_detect_file_paths_no_paths_returns_empty():
    text = "No file paths here, just plain text."
    paths = mod._detect_file_paths(text)
    assert paths == []


def test_detect_file_paths_deduplicates():
    text = "src/foo.py and src/foo.py again"
    paths = mod._detect_file_paths(text)
    assert paths.count("src/foo.py") == 1


# ---------------------------------------------------------------------------
# output_name_from_title
# ---------------------------------------------------------------------------

def test_output_name_from_title_basic():
    result = mod.output_name_from_title("My Report", default_stem="report", suffix=".md")
    assert result.endswith(".md")
    assert "my-report" in result


def test_output_name_from_title_empty_uses_default():
    result = mod.output_name_from_title("", default_stem="fallback", suffix=".txt")
    assert result == "fallback.txt"


def test_output_name_from_title_strips_special_chars():
    result = mod.output_name_from_title("Hello!! World??", default_stem="x", suffix=".md")
    assert "!" not in result
    assert "?" not in result


def test_output_name_from_title_truncates_at_40():
    long_title = "a" * 100
    result = mod.output_name_from_title(long_title, default_stem="d", suffix=".md")
    stem = result[: -len(".md")]
    assert len(stem) <= 40


# ---------------------------------------------------------------------------
# missing_feature_hint
# ---------------------------------------------------------------------------

def test_missing_feature_hint_contains_feature():
    hint = mod.missing_feature_hint("rich-tables")
    assert "rich-tables" in hint


def test_missing_feature_hint_mentions_runtime():
    hint = mod.missing_feature_hint("foo")
    assert "runtime" in hint.lower() or "openclaw" in hint.lower()


def test_missing_feature_hint_mentions_standalone():
    hint = mod.missing_feature_hint("bar")
    assert "standalone" in hint.lower()


# ---------------------------------------------------------------------------
# _a11y_plain / _a11y_reduced
# ---------------------------------------------------------------------------

def test_a11y_plain_false_by_default():
    assert mod._a11y_plain({}) is False
    assert mod._a11y_plain(None) is False


def test_a11y_plain_true_when_set():
    assert mod._a11y_plain({"plain_mode": True}) is True


def test_a11y_reduced_false_by_default():
    assert mod._a11y_reduced({}) is False
    assert mod._a11y_reduced(None) is False


def test_a11y_reduced_true_when_set():
    assert mod._a11y_reduced({"reduced_motion": True}) is True


# ---------------------------------------------------------------------------
# _make_clickable_link
# ---------------------------------------------------------------------------

def test_make_clickable_link_plain_mode_returns_url():
    result = mod._make_clickable_link("https://example.com", prefs={"plain_mode": True}, is_tty=True)
    assert result == "https://example.com"


def test_make_clickable_link_no_tty_returns_url():
    result = mod._make_clickable_link("https://example.com", prefs={}, is_tty=False)
    assert result == "https://example.com"


def test_make_clickable_link_clickable_links_off_returns_url():
    result = mod._make_clickable_link(
        "https://example.com", "label", prefs={"clickable_links": False}, is_tty=True
    )
    assert result == "label"


def test_make_clickable_link_tty_enabled_returns_osc8():
    result = mod._make_clickable_link("https://example.com", "label", prefs={}, is_tty=True)
    assert "\033]8;;" in result


# ---------------------------------------------------------------------------
# _suggest_followups
# ---------------------------------------------------------------------------

def test_suggest_followups_returns_at_most_three():
    suggestions = mod._suggest_followups("tell me about error handling")
    assert len(suggestions) <= 3


def test_suggest_followups_no_empty_items():
    suggestions = mod._suggest_followups("find the file src/main.py")
    assert all(s.strip() for s in suggestions)


def test_suggest_followups_with_session_id():
    suggestions = mod._suggest_followups("do something", session_id="sess-123")
    assert any("/context" in s for s in suggestions)


def test_suggest_followups_error_keyword_includes_exec():
    suggestions = mod._suggest_followups("fix the broken error in main")
    assert any("/exec" in s for s in suggestions)


def test_suggest_followups_default_when_no_match():
    # A completely neutral prompt gets default suggestions
    suggestions = mod._suggest_followups("do the thing")
    assert len(suggestions) == 3


def test_suggest_followups_deduplicates():
    # Providing the same hint sources shouldn't produce duplicate suggestions
    suggestions = mod._suggest_followups("compare diff before after change")
    assert len(suggestions) == len(set(suggestions))


def test_suggest_followups_path_in_response_adds_view():
    suggestions = mod._suggest_followups(
        "what is this", response_text="See src/main.py for details."
    )
    assert any("/view" in s for s in suggestions)


# ---------------------------------------------------------------------------
# _linkify_response
# ---------------------------------------------------------------------------

def test_linkify_response_plain_mode_unchanged():
    text = "Visit https://example.com now"
    result = mod._linkify_response(text, prefs={"plain_mode": True}, is_tty=True)
    assert result == text


def test_linkify_response_no_tty_unchanged():
    text = "Visit https://example.com"
    result = mod._linkify_response(text, prefs={}, is_tty=False)
    assert result == text


def test_linkify_response_skips_code_blocks():
    text = "```\nhttps://example.com\n```"
    result = mod._linkify_response(text, prefs={}, is_tty=True)
    # URL inside code block should not be linkified (no OSC 8)
    assert "\033]8;;" not in result.split("```")[1]


def test_linkify_response_tty_wraps_urls():
    text = "See https://example.com for more"
    result = mod._linkify_response(text, prefs={}, is_tty=True)
    assert "\033]8;;" in result
