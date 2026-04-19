"""Tests for src/table_renderer.py — parse, should_render, extract_table_text."""

import pytest

import table_renderer as tr
from table_renderer import (
    _parse_markdown_table,
    extract_table_text,
    should_render_table_image,
)

pytestmark = pytest.mark.xdist_group("table_renderer")


SIMPLE_TABLE = """\
| Name | Age | City |
|------|-----|------|
| Alice | 30 | Portland |
| Bob | 25 | Seattle |
"""

LARGE_TABLE = "\n".join(
    ["| " + " | ".join([f"col{j}" for j in range(7)]) + " |", "| " + " | ".join(["---"] * 7) + " |"]
    + ["| " + " | ".join([f"val{i}{j}" for j in range(7)]) + " |" for i in range(10)]
)


class TestParseMarkdownTable:
    def test_parse_valid_table(self):
        result = _parse_markdown_table(SIMPLE_TABLE)
        assert result is not None
        headers, rows = result
        assert headers == ["Name", "Age", "City"]
        assert len(rows) == 2

    def test_parse_strips_markdown_bold(self):
        table = "| **Name** | **Age** |\n|---|---|\n| Alice | 30 |\n"
        result = _parse_markdown_table(table)
        assert result is not None
        headers, rows = result
        assert headers[0] == "Name"

    def test_parse_strips_links(self):
        table = "| Name | Age |\n|------|-----|\n| [Alice](http://example.com) | 30 |\n| Bob | 25 |\n"
        result = _parse_markdown_table(table)
        assert result is not None
        _, rows = result
        assert rows[0][0] == "Alice"

    def test_returns_none_for_empty_text(self):
        assert _parse_markdown_table("") is None

    def test_returns_none_for_non_table(self):
        assert _parse_markdown_table("just some text\nno pipes here") is None

    def test_returns_none_for_single_line(self):
        assert _parse_markdown_table("| a | b |") is None

    def test_separator_only_skipped(self):
        table = "| A | B |\n|---|---|\n| x | y |\n"
        result = _parse_markdown_table(table)
        assert result is not None
        headers, rows = result
        assert headers == ["A", "B"]
        assert rows == [["x", "y"]]


class TestShouldRenderTableImage:
    def test_small_table_returns_false(self):
        assert should_render_table_image(SIMPLE_TABLE) is False

    def test_many_rows_triggers_image(self):
        big_table = "| A | B |\n|---|---|\n" + "\n".join("| x | y |" for _ in range(10))
        assert should_render_table_image(big_table, min_rows_for_image=8) is True

    def test_many_cols_triggers_image(self):
        assert should_render_table_image(LARGE_TABLE, min_cols_for_image=6) is True

    def test_long_cell_triggers_image(self):
        long_cell = "a" * 60
        table = f"| {long_cell} | B |\n|---|---|\n| x | y |\n"
        assert should_render_table_image(table, min_cell_chars_for_image=48) is True

    def test_non_table_returns_false(self):
        assert should_render_table_image("no table here") is False

    def test_custom_thresholds(self):
        # Table with 2 rows, 3 cols — won't trigger default but will with low thresholds
        table = "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 | 6 |\n"
        assert should_render_table_image(table, min_rows_for_image=2, min_cols_for_image=3) is True


class TestExtractTableText:
    def test_extracts_table_from_surrounding_text(self):
        text = "Here is the data:\n" + SIMPLE_TABLE + "\nEnd of table."
        result = extract_table_text(text)
        assert result is not None
        assert "|" in result

    def test_returns_none_when_no_table(self):
        assert extract_table_text("no table here") is None

    def test_extracts_first_table_only(self):
        text = SIMPLE_TABLE + "\n\nSome text\n\n" + SIMPLE_TABLE
        result = extract_table_text(text)
        assert result is not None
        # Should stop at first table
        assert result.count("Name") == 1

    def test_simple_table_extraction(self):
        result = extract_table_text(SIMPLE_TABLE)
        assert result is not None
        assert "Alice" in result


class TestRenderTableImage:
    def test_no_pillow_returns_none(self, monkeypatch):
        """If Pillow is unavailable, render_table_image should return None."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "PIL" or (args and "PIL" in str(args)):
                raise ImportError("No Pillow")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = tr.render_table_image(SIMPLE_TABLE)
        assert result is None

    def test_no_table_returns_none(self):
        result = tr.render_table_image("no table here")
        assert result is None
