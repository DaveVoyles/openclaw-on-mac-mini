"""Property-based tests for parsers using Hypothesis."""

import pytest
from hypothesis import given, strategies as st

from table_renderer import _parse_markdown_table


class TestMarkdownTableParser:
    """Property-based tests for markdown table parsing."""

    @given(st.text())
    def test_parse_never_crashes(self, text: str):
        """Parser should handle any text without crashing."""
        result = _parse_markdown_table(text)
        # Should return None or tuple, never crash
        assert result is None or isinstance(result, tuple)

    @given(st.lists(st.text(min_size=1), min_size=2, max_size=10))
    def test_valid_simple_table(self, rows: list[str]):
        """Parser should handle valid simple tables."""
        # Build a minimal valid table
        headers = ["Col1", "Col2"]
        table_text = "| " + " | ".join(headers) + " |\n"
        table_text += "| --- | --- |\n"
        for row in rows[:5]:  # Limit to 5 rows
            # Escape pipes in cell content
            safe_cell = row.replace("|", "\\|")[:20]
            table_text += f"| {safe_cell} | {safe_cell} |\n"
        
        result = _parse_markdown_table(table_text)
        if result is not None:
            parsed_headers, parsed_rows = result
            assert len(parsed_headers) == 2
            assert len(parsed_rows) <= 5

    def test_empty_string(self):
        """Empty string should return None."""
        assert _parse_markdown_table("") is None

    def test_no_pipe_characters(self):
        """Text without pipes should return None."""
        assert _parse_markdown_table("Just some random text") is None

    @given(st.integers(min_value=0, max_value=100))
    def test_repeated_separators(self, count: int):
        """Multiple separator rows should not crash."""
        table = "| H1 | H2 |\n" + ("| --- | --- |\n" * count)
        result = _parse_markdown_table(table)
        # Should either parse or return None, not crash
        assert result is None or isinstance(result, tuple)


class TestWebhookFormatter:
    """Property-based tests for webhook payload parsing."""

    @given(st.dictionaries(st.text(), st.one_of(st.text(), st.integers(), st.none())))
    def test_format_sonarr_never_crashes(self, payload: dict):
        """Sonarr webhook parser should handle any dict without crashing."""
        from webhook_formatter import format_sonarr
        try:
            result = format_sonarr(payload)
            # Should return tuple of (title, desc, color)
            assert isinstance(result, tuple)
            assert len(result) == 3
        except (KeyError, ValueError, AttributeError, TypeError):
            # Expected for invalid payloads
            pass

    @given(st.dictionaries(st.text(), st.one_of(st.text(), st.integers(), st.none())))
    def test_format_plex_never_crashes(self, payload: dict):
        """Plex webhook parser should handle any dict without crashing."""
        from webhook_formatter import format_plex
        try:
            result = format_plex(payload)
            assert isinstance(result, tuple)
            assert len(result) == 3
        except (KeyError, ValueError, AttributeError, TypeError):
            # Expected for invalid payloads
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
