"""Tests for json_utils, nlp_entities, skills_facade, todo_manager."""
import os

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/t_simple.db")

import json
import sys
from pathlib import Path

import pytest

# Ensure src/ is importable
_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


# ─────────────────────────────────────────────────────────────────────────────
# json_utils
# ─────────────────────────────────────────────────────────────────────────────
from json_utils import (
    extract_json_block,
    format_tool_result,
    repair_json,
    try_parse_json,
)


class TestTryParseJson:
    def test_simple_modules_valid_object(self):
        assert try_parse_json('{"key": "value"}') == {"key": "value"}

    def test_simple_modules_valid_array(self):
        assert try_parse_json("[1, 2, 3]") == [1, 2, 3]

    def test_valid_nested(self):
        result = try_parse_json('{"a": {"b": [1, 2]}}')
        assert result == {"a": {"b": [1, 2]}}

    def test_simple_modules_invalid_json_returns_none(self):
        assert try_parse_json("{bad json}") is None

    def test_simple_modules_empty_string_returns_none(self):
        assert try_parse_json("") is None

    def test_simple_modules_whitespace_only_returns_none(self):
        assert try_parse_json("   \n  ") is None

    def test_plain_text_returns_none(self):
        assert try_parse_json("hello world") is None

    def test_valid_number_string(self):
        # json.loads("42") returns 42, not a dict/list — function returns it
        result = try_parse_json("42")
        assert result == 42

    def test_trailing_comma_returns_none(self):
        assert try_parse_json('{"a": 1,}') is None

    def test_simple_modules_none_input_returns_none(self):
        # try_parse_json expects str; passing None hits TypeError branch
        assert try_parse_json(None) is None  # type: ignore[arg-type]

    def test_boolean_true(self):
        assert try_parse_json("true") is True

    def test_null(self):
        assert try_parse_json("null") is None  # json.loads("null") == None


class TestExtractJsonBlock:
    def test_simple_modules_fenced_json_block(self):
        text = '```json\n{"key": "val"}\n```'
        assert extract_json_block(text) == '{"key": "val"}'

    def test_fenced_generic_block(self):
        text = "```\n[1, 2, 3]\n```"
        assert extract_json_block(text) == "[1, 2, 3]"

    def test_prose_around_object(self):
        text = 'Here is the result: {"status": "ok"} enjoy!'
        result = extract_json_block(text)
        assert result == '{"status": "ok"}'

    def test_prose_around_array(self):
        text = "Items: [1, 2, 3] done."
        result = extract_json_block(text)
        assert result == "[1, 2, 3]"

    def test_raw_json_object_only(self):
        text = '{"a": 1}'
        assert extract_json_block(text) == '{"a": 1}'

    def test_simple_modules_nested_object(self):
        text = '{"outer": {"inner": true}}'
        result = extract_json_block(text)
        assert result == '{"outer": {"inner": true}}'

    def test_simple_modules_no_json_returns_none(self):
        assert extract_json_block("just plain text") is None

    def test_simple_modules_empty_string_returns_none_v2(self):
        assert extract_json_block("") is None

    def test_string_with_escaped_quotes(self):
        text = '{"msg": "say \\"hello\\""}'
        result = extract_json_block(text)
        assert result == '{"msg": "say \\"hello\\""}'

    def test_fenced_block_preferred_over_inline(self):
        text = 'prefix {"inline": 1} ```json\n{"fenced": 2}\n``` suffix'
        result = extract_json_block(text)
        assert result == '{"fenced": 2}'


class TestRepairJson:
    def test_already_valid(self):
        assert repair_json('{"a": 1}') == {"a": 1}

    def test_trailing_comma_object(self):
        result = repair_json('{"a": 1,}')
        assert result == {"a": 1}

    def test_trailing_comma_array(self):
        result = repair_json('[1, 2, 3,]')
        assert result == [1, 2, 3]

    def test_single_quotes(self):
        result = repair_json("{'key': 'value'}")
        assert result == {"key": "value"}

    def test_missing_closing_brace(self):
        result = repair_json('{"a": 1')
        assert result == {"a": 1}

    def test_missing_closing_bracket(self):
        result = repair_json('[1, 2, 3')
        assert result == [1, 2, 3]

    def test_line_comment_removal(self):
        result = repair_json('{"a": 1 // comment\n}')
        assert result == {"a": 1}

    def test_block_comment_removal(self):
        result = repair_json('{"a": /* comment */ 1}')
        assert result == {"a": 1}

    def test_unquoted_keys(self):
        result = repair_json('{key: "value"}')
        assert result == {"key": "value"}

    def test_json_inside_markdown_fence(self):
        result = repair_json('```json\n{"x": 99}\n```')
        assert result == {"x": 99}

    def test_simple_modules_empty_string_returns_none_v3(self):
        assert repair_json("") is None

    def test_simple_modules_whitespace_only_returns_none_v2(self):
        assert repair_json("   ") is None

    def test_completely_broken_returns_none(self):
        assert repair_json("this is not json at all!!!") is None

    def test_prose_with_embedded_json(self):
        result = repair_json('Here is your answer: {"result": "done"} thanks!')
        assert result == {"result": "done"}

    def test_trailing_comma_nested(self):
        result = repair_json('{"a": [1, 2,], "b": {"c": 3,},}')
        assert result == {"a": [1, 2], "b": {"c": 3}}


class TestFormatToolResult:
    def test_dict_input_pretty_prints(self):
        result = format_tool_result({"a": 1})
        assert json.loads(result) == {"a": 1}
        assert "\n" in result  # pretty-printed

    def test_list_input_pretty_prints(self):
        result = format_tool_result([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_json_string_pretty_prints(self):
        result = format_tool_result('{"x": 10}')
        assert json.loads(result) == {"x": 10}
        assert "\n" in result

    def test_simple_modules_plain_string_returned_as_is(self):
        assert format_tool_result("hello") == "hello"

    def test_number_returned_as_string(self):
        assert format_tool_result(42) == "42"

    def test_none_returned_as_string(self):
        assert format_tool_result(None) == "None"

    def test_tool_name_ignored_in_output(self):
        result = format_tool_result({"k": "v"}, tool_name="my_tool")
        assert json.loads(result) == {"k": "v"}

    def test_non_serialisable_dict_falls_back(self):
        class Weird:
            def __repr__(self):
                return "Weird()"

        # dict with non-serialisable value uses default=str
        result = format_tool_result({"obj": Weird()})
        data = json.loads(result)
        assert "obj" in data

    def test_empty_dict(self):
        assert format_tool_result({}) == "{}"

    def test_simple_modules_empty_list(self):
        assert format_tool_result([]) == "[]"


# ─────────────────────────────────────────────────────────────────────────────
# nlp_entities
# ─────────────────────────────────────────────────────────────────────────────
from nlp_entities import (
    _dedupe,
    _phrase_in_text,
    enrich_route_text_and_hints,
    extract_entities,
)


class TestPhraseInText:
    def test_simple_modules_exact_match(self):
        assert _phrase_in_text("plex is running", "plex") is True

    def test_simple_modules_no_match(self):
        assert _phrase_in_text("hello world", "plex") is False

    def test_word_boundary_left(self):
        # "complex" should not match "plex" since 'x' precedes it
        assert _phrase_in_text("complex system", "plex") is False

    def test_phrase_with_spaces(self):
        assert _phrase_in_text("restart sab nzbd now", "sab nzbd") is True


class TestDedupe:
    def test_removes_duplicates_preserves_order(self):
        assert _dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_simple_modules_empty_list_v2(self):
        assert _dedupe([]) == []

    def test_no_duplicates(self):
        assert _dedupe(["x", "y", "z"]) == ["x", "y", "z"]


class TestExtractEntities:
    def test_service_plex(self):
        result = extract_entities("restart plex for me")
        assert "services" in result
        assert "plex" in result["services"]

    def test_service_alias_sab(self):
        result = extract_entities("check sab queue")
        assert "services" in result
        assert "sabnzbd" in result["services"]

    def test_service_alias_qbit(self):
        result = extract_entities("open qbit")
        assert "services" in result
        assert "qbittorrent" in result["services"]

    def test_service_sonarr(self):
        result = extract_entities("sonarr is down")
        assert "services" in result
        assert "sonarr" in result["services"]

    def test_simple_modules_league_nba(self):
        result = extract_entities("who won the nba game")
        assert "leagues" in result
        assert "NBA" in result["leagues"]

    def test_league_d1_alias(self):
        result = extract_entities("d1 football scores")
        assert "leagues" in result
        assert "NCAA Division I" in result["leagues"]

    def test_platform_ps5(self):
        result = extract_entities("ps5 games this week")
        assert "platforms" in result
        assert "PlayStation" in result["platforms"]

    def test_simple_modules_platform_xbox(self):
        result = extract_entities("xbox series x news")
        assert "platforms" in result
        assert "Xbox" in result["platforms"]

    def test_wwe_raw(self):
        result = extract_entities("what happened on raw last night")
        assert "wwe" in result
        assert "WWE RAW" in result["wwe"]

    def test_simple_modules_wwe_smackdown(self):
        result = extract_entities("smackdown results")
        assert "wwe" in result
        assert "WWE SmackDown" in result["wwe"]

    def test_wwe_wrestlemania(self):
        result = extract_entities("wrestlemania card announced")
        assert "wwe" in result
        assert "WrestleMania" in result["wwe"]

    def test_no_entities_empty(self):
        result = extract_entities("hello how are you today")
        assert result == {}

    def test_simple_modules_multiple_services(self):
        result = extract_entities("sonarr and radarr both crashed")
        assert "sonarr" in result["services"]
        assert "radarr" in result["services"]

    def test_case_insensitive_assumed_lowercase(self):
        # The function expects pre-lowercased input
        result = extract_entities("plex media server")
        assert "plex" in result["services"]

    def test_deduplification_within_category(self):
        # "plex" canonical + "plex" alias won't duplicate
        result = extract_entities("plex plex plex")
        assert result["services"].count("plex") == 1


class TestEnrichRouteTextAndHints:
    def test_simple_modules_services_added_to_hints(self):
        text, hints = enrich_route_text_and_hints("restart plex", {})
        assert hints.get("services") == ["plex"]

    def test_simple_modules_league_added_to_hints(self):
        _, hints = enrich_route_text_and_hints("nba scores tonight", {})
        assert hints.get("league") == "NBA"

    def test_simple_modules_platform_added_to_hints(self):
        _, hints = enrich_route_text_and_hints("ps5 exclusives", {})
        assert "PlayStation" in hints.get("platforms", [])

    def test_wwe_added_to_hints(self):
        _, hints = enrich_route_text_and_hints("raw results", {})
        assert "WWE RAW" in hints.get("wwe_entities", [])

    def test_no_entities_hints_unchanged(self):
        _, hints = enrich_route_text_and_hints("hello world", {"foo": "bar"})
        assert hints.get("foo") == "bar"
        assert "entities" not in hints

    def test_channel_disambiguation(self):
        _, hints = enrich_route_text_and_hints("use this channel", {})
        assert hints.get("disambiguated_references", {}).get("channel") == "current"
        assert hints.get("disambiguation_confidence", 0) >= 0.9

    def test_service_disambiguation_single(self):
        _, hints = enrich_route_text_and_hints("restart plex use this service", {})
        disambig = hints.get("disambiguated_references", {})
        assert disambig.get("service") == "plex"

    def test_league_disambiguation_single(self):
        _, hints = enrich_route_text_and_hints("nba standings for this league", {})
        disambig = hints.get("disambiguated_references", {})
        assert disambig.get("league") == "NBA"

    def test_platform_disambiguation_single(self):
        _, hints = enrich_route_text_and_hints("ps5 games on this platform", {})
        disambig = hints.get("disambiguated_references", {})
        assert disambig.get("platform") == "PlayStation"

    def test_show_disambiguation_single_wwe(self):
        _, hints = enrich_route_text_and_hints("raw results from this show", {})
        disambig = hints.get("disambiguated_references", {})
        assert disambig.get("show") == "WWE RAW"

    def test_unresolved_service_without_match(self):
        # "this service" but no service entity → unresolved
        _, hints = enrich_route_text_and_hints("use this service", {})
        assert "service" in hints.get("unresolved_references", [])

    def test_existing_services_hint_not_overwritten(self):
        _, hints = enrich_route_text_and_hints("plex is up", {"services": ["custom"]})
        assert hints["services"] == ["custom"]

    def test_enriched_text_appends_canonical(self):
        # Alias "sab" → canonical "sabnzbd" — canonical appended if not present
        enriched, _ = enrich_route_text_and_hints("check sab queue", {})
        assert "sabnzbd" in enriched

    def test_bundle_names_service_disambiguation(self):
        # "this service" with plex-activity bundle and no explicit entity
        _, hints = enrich_route_text_and_hints(
            "this service is slow",
            {},
            matched_bundle_names={"plex-activity"},
        )
        disambig = hints.get("disambiguated_references", {})
        assert disambig.get("service") == "plex"


# ─────────────────────────────────────────────────────────────────────────────
# skills_facade
# ─────────────────────────────────────────────────────────────────────────────
from skills_facade import SKILLS, get_skill, list_skills, skill_exists


class TestSkillsFacade:
    def test_get_skill_existing(self):
        name = next(iter(SKILLS))
        result = get_skill(name)
        assert result is not None

    def test_get_skill_missing_returns_none(self):
        assert get_skill("__nonexistent_skill_xyz__") is None

    def test_list_skills_returns_list(self):
        skills = list_skills()
        assert isinstance(skills, list)

    def test_list_skills_is_sorted(self):
        skills = list_skills()
        assert skills == sorted(skills)

    def test_list_skills_contains_known_skills(self):
        skills = list_skills()
        for name in SKILLS:
            assert name in skills

    def test_list_skills_length_matches_skills_dict(self):
        assert len(list_skills()) == len(SKILLS)

    def test_skill_exists_true(self):
        name = next(iter(SKILLS))
        assert skill_exists(name) is True

    def test_skill_exists_false(self):
        assert skill_exists("__definitely_not_a_skill__") is False

    def test_all_skills_are_non_none(self):
        for name in SKILLS:
            assert get_skill(name) is not None


# ─────────────────────────────────────────────────────────────────────────────
# todo_manager
# ─────────────────────────────────────────────────────────────────────────────
import todo_manager as _todo_module
from todo_manager import TodoManager


@pytest.fixture()
def mgr(tmp_path, monkeypatch):
    """TodoManager backed by a temp file."""
    db = tmp_path / "todos.json"
    monkeypatch.setattr(_todo_module, "DATA_PATH", db)
    return TodoManager(path=db)


class TestTodoManagerAdd:
    def test_add_returns_todo_item(self, mgr):
        item = mgr.add("Buy milk", user_id=1)
        assert item.title == "Buy milk"
        assert item.user_id == 1
        assert item.completed is False
        assert item.priority == "medium"

    def test_add_with_priority(self, mgr):
        item = mgr.add("Urgent task", user_id=1, priority="high")
        assert item.priority == "high"

    def test_simple_modules_add_with_due_date(self, mgr):
        item = mgr.add("Doctor", user_id=1, due_date="2099-01-01")
        assert item.due_date == "2099-01-01"

    def test_add_generates_unique_ids(self, mgr):
        a = mgr.add("Task A", user_id=1)
        b = mgr.add("Task B", user_id=1)
        assert a.id != b.id

    def test_add_saves_to_disk(self, mgr, tmp_path):
        mgr.add("Persisted task", user_id=5)
        db = tmp_path / "todos.json"
        assert db.exists()
        data = json.loads(db.read_text())
        assert any(d["title"] == "Persisted task" for d in data)


class TestTodoManagerList:
    def test_list_for_user_returns_only_that_user(self, mgr):
        mgr.add("User1 task", user_id=1)
        mgr.add("User2 task", user_id=2)
        items = mgr.list_for_user(1)
        assert all(i.user_id == 1 for i in items)

    def test_list_excludes_completed_by_default(self, mgr):
        item = mgr.add("Done task", user_id=1)
        mgr.complete(item.id, user_id=1)
        items = mgr.list_for_user(1, filter_="all")
        assert all(not i.completed for i in items)

    def test_list_filter_done(self, mgr):
        item = mgr.add("To complete", user_id=1)
        mgr.complete(item.id, user_id=1)
        done = mgr.list_for_user(1, filter_="done")
        assert any(i.id == item.id for i in done)

    def test_list_filter_today(self, mgr):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        item = mgr.add("Today task", user_id=1, due_date=today)
        items = mgr.list_for_user(1, filter_="today")
        assert any(i.id == item.id for i in items)

    def test_list_filter_today_excludes_other_dates(self, mgr):
        mgr.add("Future task", user_id=1, due_date="2099-12-31")
        items = mgr.list_for_user(1, filter_="today")
        assert all(i.due_date != "2099-12-31" for i in items)

    def test_list_filter_overdue(self, mgr):
        item = mgr.add("Old task", user_id=1, due_date="2000-01-01")
        items = mgr.list_for_user(1, filter_="overdue")
        assert any(i.id == item.id for i in items)

    def test_list_filter_overdue_excludes_completed(self, mgr):
        item = mgr.add("Old done", user_id=1, due_date="2000-01-01")
        mgr.complete(item.id, user_id=1)
        items = mgr.list_for_user(1, filter_="overdue")
        assert not any(i.id == item.id for i in items)

    def test_simple_modules_list_empty_user(self, mgr):
        assert mgr.list_for_user(999) == []


class TestTodoManagerComplete:
    def test_simple_modules_complete_marks_done(self, mgr):
        item = mgr.add("Task", user_id=1)
        result = mgr.complete(item.id, user_id=1)
        assert result is not None
        assert result.completed is True

    def test_complete_wrong_user_returns_none(self, mgr):
        item = mgr.add("Task", user_id=1)
        result = mgr.complete(item.id, user_id=2)
        assert result is None

    def test_complete_nonexistent_returns_none(self, mgr):
        assert mgr.complete("deadbeef", user_id=1) is None

    def test_simple_modules_complete_persists(self, mgr, tmp_path):
        item = mgr.add("Task", user_id=1)
        mgr.complete(item.id, user_id=1)
        db = tmp_path / "todos.json"
        data = json.loads(db.read_text())
        assert data[0]["completed"] is True


class TestTodoManagerDelete:
    def test_simple_modules_delete_returns_true(self, mgr):
        item = mgr.add("To delete", user_id=1)
        assert mgr.delete(item.id, user_id=1) is True

    def test_delete_removes_item(self, mgr):
        item = mgr.add("To delete", user_id=1)
        mgr.delete(item.id, user_id=1)
        assert mgr.list_for_user(1, filter_="all") == []

    def test_simple_modules_delete_wrong_user_returns_false(self, mgr):
        item = mgr.add("Task", user_id=1)
        assert mgr.delete(item.id, user_id=2) is False

    def test_simple_modules_delete_nonexistent_returns_false(self, mgr):
        assert mgr.delete("deadbeef", user_id=1) is False

    def test_simple_modules_delete_persists(self, mgr, tmp_path):
        item = mgr.add("Task", user_id=1)
        mgr.delete(item.id, user_id=1)
        db = tmp_path / "todos.json"
        data = json.loads(db.read_text())
        assert data == []


class TestTodoManagerPersistence:
    def test_reload_from_disk(self, tmp_path, monkeypatch):
        db = tmp_path / "todos.json"
        monkeypatch.setattr(_todo_module, "DATA_PATH", db)
        mgr1 = TodoManager(path=db)
        mgr1.add("Persistent task", user_id=7)

        mgr2 = TodoManager(path=db)
        items = mgr2.list_for_user(7, filter_="all")
        assert any(i.title == "Persistent task" for i in items)

    def test_corrupted_file_loads_empty(self, tmp_path, monkeypatch):
        db = tmp_path / "todos.json"
        db.write_text("not valid json", encoding="utf-8")
        monkeypatch.setattr(_todo_module, "DATA_PATH", db)
        mgr = TodoManager(path=db)
        assert mgr.list_for_user(1) == []

    def test_missing_file_loads_empty(self, tmp_path, monkeypatch):
        db = tmp_path / "nonexistent.json"
        monkeypatch.setattr(_todo_module, "DATA_PATH", db)
        mgr = TodoManager(path=db)
        assert mgr.list_for_user(1) == []

    def test_simple_modules_save_creates_parent_dirs(self, tmp_path, monkeypatch):
        db = tmp_path / "deep" / "nested" / "todos.json"
        monkeypatch.setattr(_todo_module, "DATA_PATH", db)
        mgr = TodoManager(path=db)
        mgr.add("Deep task", user_id=1)
        assert db.exists()


class TestTodoManagerListOverdue:
    def test_list_overdue_global(self, mgr):
        mgr.add("Old u1", user_id=1, due_date="2000-01-01")
        mgr.add("Old u2", user_id=2, due_date="2000-01-01")
        overdue = mgr.list_overdue()
        assert len(overdue) == 2

    def test_list_overdue_excludes_future(self, mgr):
        mgr.add("Future", user_id=1, due_date="2099-12-31")
        assert mgr.list_overdue() == []

    def test_list_overdue_excludes_no_due_date(self, mgr):
        mgr.add("No date", user_id=1)
        assert mgr.list_overdue() == []

    def test_list_overdue_excludes_completed(self, mgr):
        item = mgr.add("Old done", user_id=1, due_date="2000-01-01")
        mgr.complete(item.id, user_id=1)
        assert mgr.list_overdue() == []
