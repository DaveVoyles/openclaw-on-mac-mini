"""Tests for src/json_utils.py — try_parse_json, extract_json_block, repair_json, format_tool_result."""

from json_utils import (
    extract_json_block,
    format_tool_result,
    repair_json,
    try_parse_json,
)


class TestTryParseJson:
    def test_json_utils_valid_object(self):
        result = try_parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_utils_valid_array(self):
        result = try_parse_json("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_json_utils_invalid_json_returns_none(self):
        assert try_parse_json("{not valid}") is None

    def test_json_utils_empty_string_returns_none(self):
        assert try_parse_json("") is None

    def test_json_utils_whitespace_only_returns_none(self):
        assert try_parse_json("   ") is None

    def test_json_utils_none_input_returns_none(self):
        assert try_parse_json(None) is None

    def test_json_utils_nested_object(self):
        result = try_parse_json('{"a": {"b": [1, 2]}}')
        assert result == {"a": {"b": [1, 2]}}


class TestExtractJsonBlock:
    def test_json_utils_fenced_json_block(self):
        text = '```json\n{"key": "value"}\n```'
        result = extract_json_block(text)
        assert result == '{"key": "value"}'

    def test_generic_fenced_block(self):
        text = '```\n{"key": "value"}\n```'
        result = extract_json_block(text)
        assert result == '{"key": "value"}'

    def test_raw_json_object_in_prose(self):
        text = 'Here is the data: {"name": "Alice", "age": 30} end.'
        result = extract_json_block(text)
        assert result == '{"name": "Alice", "age": 30}'

    def test_raw_json_array_in_prose(self):
        text = "Result: [1, 2, 3] done."
        result = extract_json_block(text)
        assert result == "[1, 2, 3]"

    def test_json_utils_no_json_returns_none(self):
        assert extract_json_block("no json here") is None

    def test_nested_braces(self):
        text = '{"outer": {"inner": "value"}}'
        result = extract_json_block(text)
        assert result == '{"outer": {"inner": "value"}}'


class TestRepairJson:
    def test_valid_json_returned_directly(self):
        result = repair_json('{"valid": true}')
        assert result == {"valid": True}

    def test_trailing_comma_repaired(self):
        result = repair_json('{"a": 1,}')
        assert result == {"a": 1}

    def test_missing_closing_brace_repaired(self):
        result = repair_json('{"a": 1')
        assert result is not None
        assert result.get("a") == 1

    def test_comments_stripped(self):
        text = '{"a": 1 // comment\n}'
        result = repair_json(text)
        assert result == {"a": 1}

    def test_fenced_json_extracted_and_parsed(self):
        text = '```json\n{"key": "val"}\n```'
        result = repair_json(text)
        assert result == {"key": "val"}

    def test_json_utils_empty_string_returns_none_v2(self):
        assert repair_json("") is None

    def test_json_utils_none_input_returns_none_v2(self):
        assert repair_json(None) is None

    def test_block_comment_stripped(self):
        text = '{"a": /* comment */ 1}'
        result = repair_json(text)
        assert result == {"a": 1}

    def test_trailing_comma_in_array(self):
        result = repair_json("[1, 2, 3,]")
        assert result == [1, 2, 3]

    def test_missing_closing_bracket_repaired(self):
        result = repair_json("[1, 2, 3")
        assert result is not None
        assert isinstance(result, list)


class TestFormatToolResult:
    def test_dict_formatted_as_json(self):
        result = format_tool_result({"a": 1})
        assert '"a": 1' in result

    def test_list_formatted_as_json(self):
        result = format_tool_result([1, 2, 3])
        assert "1" in result

    def test_json_string_pretty_printed(self):
        result = format_tool_result('{"a": 1}')
        assert '"a": 1' in result

    def test_json_utils_plain_string_returned_as_is(self):
        result = format_tool_result("plain text")
        assert result == "plain text"

    def test_integer_converted_to_string(self):
        result = format_tool_result(42)
        assert "42" in result

    def test_none_converted_to_string(self):
        result = format_tool_result(None)
        assert result == "None"
