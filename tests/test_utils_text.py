"""Tests for text utility functions."""

from utils.text import (
    extract_code_blocks,
    remove_markdown,
    sanitize_filename,
    split_by_length,
    truncate,
)


class TestTruncate:
    def test_no_truncation_needed(self):
        assert truncate("Hello", 10) == "Hello"

    def test_exact_length(self):
        assert truncate("Hello", 5) == "Hello"

    def test_truncates_with_ellipsis(self):
        result = truncate("Hello world", 8)
        assert result == "Hello..."
        assert len(result) == 8

    def test_custom_suffix(self):
        result = truncate("Hello world", 8, suffix="…")
        assert result == "Hello w…"

    def test_max_length_shorter_than_suffix(self):
        result = truncate("Hello", 2, suffix="...")
        assert result == ".."


class TestSplitByLength:
    def test_empty_string(self):
        assert split_by_length("", 10) == []

    def test_no_split_needed(self):
        assert split_by_length("Hello", 10) == ["Hello"]

    def test_split_by_newline(self):
        text = "abc\ndef\nghi"
        result = split_by_length(text, 5)
        assert result == ["abc", "def", "ghi"]

    def test_split_by_words(self):
        text = "Hello world this is a test"
        result = split_by_length(text, 12)
        assert all(len(chunk) <= 12 for chunk in result)
        assert " ".join(result).replace("\n", " ") == text

    def test_preserves_newlines(self):
        text = "Line 1\nLine 2\nLine 3"
        result = split_by_length(text, 10)
        assert "Line 1" in result
        assert "Line 2" in result


class TestExtractCodeBlocks:
    def test_extract_single_code_block(self):
        text = "```python\nprint('hello')\n```"
        result = extract_code_blocks(text, "python")
        assert result == ["print('hello')"]

    def test_extract_multiple_blocks(self):
        text = "```python\ncode1\n```\ntext\n```python\ncode2\n```"
        result = extract_code_blocks(text, "python")
        assert len(result) == 2
        assert "code1" in result
        assert "code2" in result

    def test_extract_any_language(self):
        text = "```javascript\nconsole.log()\n```"
        result = extract_code_blocks(text)
        assert len(result) == 1

    def test_no_code_blocks(self):
        text = "Just plain text"
        result = extract_code_blocks(text)
        assert result == []


class TestRemoveMarkdown:
    def test_remove_bold(self):
        assert remove_markdown("**bold**") == "bold"

    def test_remove_italic(self):
        assert remove_markdown("*italic*") == "italic"

    def test_remove_headers(self):
        assert remove_markdown("# Header") == "Header"
        assert remove_markdown("## Header 2") == "Header 2"

    def test_remove_links(self):
        text = "[Link text](https://example.com)"
        assert remove_markdown(text) == "Link text"

    def test_remove_inline_code(self):
        text = "Use `code` here"
        result = remove_markdown(text)
        assert "`" not in result


class TestSanitizeFilename:
    def test_remove_invalid_chars(self):
        assert sanitize_filename("file/name?.txt") == "file_name_.txt"

    def test_replace_spaces(self):
        assert sanitize_filename("my file.txt") == "my_file.txt"

    def test_strip_dots_and_underscores(self):
        assert sanitize_filename("_file_.txt") == "file_.txt"
        assert sanitize_filename("..file..") == "file"

    def test_truncate_long_filename(self):
        long_name = "a" * 300 + ".txt"
        result = sanitize_filename(long_name, max_length=255)
        assert len(result) <= 255
        assert result.endswith(".txt")

    def test_empty_becomes_untitled(self):
        assert sanitize_filename("") == "untitled"
        assert sanitize_filename("....") == "untitled"
