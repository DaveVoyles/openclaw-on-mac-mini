"""Tests for ontology_skills.py — local graph memory skill."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import ontology_skills

# ---------------------------------------------------------------------------
# _safe_json_loads
# ---------------------------------------------------------------------------

def test_safe_json_loads_valid_object():
    parsed, err = ontology_skills._safe_json_loads('{"id": "abc", "type": "Person"}')
    assert parsed == {"id": "abc", "type": "Person"}
    assert err is None


def test_safe_json_loads_valid_list():
    parsed, err = ontology_skills._safe_json_loads('[{"id": "a"}]')
    assert isinstance(parsed, list)
    assert err is None


def test_safe_json_loads_invalid():
    parsed, err = ontology_skills._safe_json_loads("not json at all")
    assert parsed is None
    assert "❌" in err


def test_safe_json_loads_empty_string():
    parsed, err = ontology_skills._safe_json_loads("")
    assert parsed is None
    assert err is not None


# ---------------------------------------------------------------------------
# _normalize_json
# ---------------------------------------------------------------------------

def test_normalize_json_empty_string():
    result = ontology_skills._normalize_json("")
    assert result == "{}"


def test_normalize_json_whitespace_only():
    result = ontology_skills._normalize_json("   ")
    assert result == "{}"


def test_normalize_json_valid():
    result = ontology_skills._normalize_json('{"name": "Alice"}')
    assert json.loads(result) == {"name": "Alice"}


def test_normalize_json_none():
    result = ontology_skills._normalize_json(None)
    assert result == "{}"


def test_normalize_json_invalid_raises():
    with pytest.raises(json.JSONDecodeError):
        ontology_skills._normalize_json("bad-json")


def test_normalize_json_custom_default():
    result = ontology_skills._normalize_json("", default='{"k": 1}')
    assert json.loads(result) == {"k": 1}


# ---------------------------------------------------------------------------
# _entity_label
# ---------------------------------------------------------------------------

def test_entity_label_with_name():
    entity = {"id": "e1", "type": "Person", "properties": {"name": "Alice"}}
    assert ontology_skills._entity_label(entity) == "e1 [Person] Alice"


def test_entity_label_with_title():
    entity = {"id": "e2", "type": "Article", "properties": {"title": "My Post"}}
    result = ontology_skills._entity_label(entity)
    assert "My Post" in result


def test_entity_label_with_description():
    entity = {"id": "e3", "type": "Event", "properties": {"description": "Conference"}}
    result = ontology_skills._entity_label(entity)
    assert "Conference" in result


def test_entity_label_no_label():
    entity = {"id": "e4", "type": "Unknown", "properties": {}}
    result = ontology_skills._entity_label(entity)
    assert "(no label)" in result


def test_entity_label_missing_id_and_type():
    entity = {"properties": {"name": "Bob"}}
    result = ontology_skills._entity_label(entity)
    assert "?" in result
    assert "Bob" in result


# ---------------------------------------------------------------------------
# _format_entity
# ---------------------------------------------------------------------------

def test_format_entity_returns_json():
    entity = {"id": "e1", "type": "Person", "properties": {"name": "Alice"}}
    result = ontology_skills._format_entity(entity)
    assert '"id": "e1"' in result
    assert '"name": "Alice"' in result


# ---------------------------------------------------------------------------
# _format_entity_list
# ---------------------------------------------------------------------------

def test_format_entity_list_empty():
    result = ontology_skills._format_entity_list([])
    assert "No matching entities" in result


def test_format_entity_list_single_shows_full_json():
    entity = {"id": "e1", "type": "Person", "properties": {"name": "Bob"}}
    result = ontology_skills._format_entity_list([entity])
    assert "Found 1" in result
    assert '"id": "e1"' in result  # single entity shows full JSON


def test_format_entity_list_multiple():
    entities = [
        {"id": f"e{i}", "type": "Thing", "properties": {"name": f"Item {i}"}}
        for i in range(5)
    ]
    result = ontology_skills._format_entity_list(entities)
    assert "Found 5" in result
    assert "Item 0" in result


def test_format_entity_list_caps_at_10():
    entities = [
        {"id": f"e{i}", "type": "X", "properties": {"name": f"N{i}"}}
        for i in range(15)
    ]
    result = ontology_skills._format_entity_list(entities)
    assert "Found 15" in result
    # Only 10 rendered as bullets
    assert result.count("•") == 10


# ---------------------------------------------------------------------------
# _format_related
# ---------------------------------------------------------------------------

def test_format_related_empty():
    result = ontology_skills._format_related([])
    assert "No related entities" in result


def test_format_related_with_direction():
    items = [
        {
            "entity": {"id": "e2", "type": "Place", "properties": {"name": "Paris"}},
            "relation": "located_in",
            "direction": "outbound",
        }
    ]
    result = ontology_skills._format_related(items)
    assert "Found 1" in result
    assert "outbound" in result
    assert "located_in" in result
    assert "Paris" in result


def test_format_related_without_direction():
    items = [
        {"entity": {"id": "e3", "type": "Org", "properties": {"name": "Acme"}}, "relation": "works_for"}
    ]
    result = ontology_skills._format_related(items)
    assert "works_for" in result
    assert "Acme" in result


# ---------------------------------------------------------------------------
# _run_ontology — script missing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_ontology_script_missing():
    from pathlib import Path
    nonexistent = Path("/nonexistent/ontology.py")
    with patch.object(ontology_skills, "_ONTOLOGY_SCRIPT", nonexistent):
        rc, out, err = await ontology_skills._run_ontology(["create"])
    assert rc == 1
    assert "not installed" in err


# ---------------------------------------------------------------------------
# ontology_create_entity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_entity_invalid_json():
    result = await ontology_skills.ontology_create_entity("Person", "not-json")
    assert "Invalid properties_json" in result


@pytest.mark.asyncio
async def test_create_entity_subprocess_error():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(1, "", "write error"))):
        result = await ontology_skills.ontology_create_entity("Person", '{"name": "Alice"}')
    assert "❌" in result
    assert "write error" in result


@pytest.mark.asyncio
async def test_create_entity_bad_json_output():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, "not-json-output", ""))):
        result = await ontology_skills.ontology_create_entity("Person", '{"name": "Alice"}')
    assert "❌" in result


@pytest.mark.asyncio
async def test_create_entity_success():
    entity = {"id": "abc", "type": "Person", "properties": {"name": "Alice"}}
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(entity), ""))):
        result = await ontology_skills.ontology_create_entity("Person", '{"name": "Alice"}')
    assert "✅" in result
    assert "Alice" in result


@pytest.mark.asyncio
async def test_create_entity_with_explicit_id():
    entity = {"id": "custom-id", "type": "Person", "properties": {"name": "Bob"}}
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(entity), ""))) as mock_run:
        result = await ontology_skills.ontology_create_entity("Person", '{"name": "Bob"}', entity_id="custom-id")
    assert "✅" in result
    # id argument should be forwarded
    args = mock_run.call_args[0][0]
    assert "--id" in args
    assert "custom-id" in args


@pytest.mark.asyncio
async def test_create_entity_empty_id_omitted():
    entity = {"id": "gen-id", "type": "Person", "properties": {"name": "Carol"}}
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(entity), ""))) as mock_run:
        result = await ontology_skills.ontology_create_entity("Person", '{"name": "Carol"}', entity_id="  ")
    args = mock_run.call_args[0][0]
    assert "--id" not in args


@pytest.mark.asyncio
async def test_create_entity_vector_store_called():
    entity = {"id": "abc", "type": "Person", "properties": {"name": "Alice"}}
    mock_vs = MagicMock()
    mock_vs.add_memory = AsyncMock()
    with (
        patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(entity), ""))),
        patch.dict("sys.modules", {"vector_store": mock_vs}),
    ):
        result = await ontology_skills.ontology_create_entity("Person", '{"name": "Alice"}')
    assert "✅" in result
    mock_vs.add_memory.assert_called_once()


# ---------------------------------------------------------------------------
# ontology_get_entity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_entity_success():
    entity = {"id": "e1", "type": "Person", "properties": {"name": "Dave"}}
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(entity), ""))):
        result = await ontology_skills.ontology_get_entity("e1")
    assert '"id": "e1"' in result


@pytest.mark.asyncio
async def test_get_entity_not_found():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, "Entity not found: e99", ""))):
        result = await ontology_skills.ontology_get_entity("e99")
    assert "Entity not found" in result


@pytest.mark.asyncio
async def test_get_entity_error():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(1, "", "read error"))):
        result = await ontology_skills.ontology_get_entity("e1")
    assert "❌" in result


@pytest.mark.asyncio
async def test_get_entity_bad_json_output():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, "oops", ""))):
        result = await ontology_skills.ontology_get_entity("e1")
    assert "❌" in result


# ---------------------------------------------------------------------------
# ontology_query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_success():
    entities = [{"id": "e1", "type": "Person", "properties": {"name": "Alice"}}]
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(entities), ""))):
        result = await ontology_skills.ontology_query("Person", "{}")
    assert "Found 1" in result


@pytest.mark.asyncio
async def test_query_no_type():
    entities = []
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(entities), ""))):
        result = await ontology_skills.ontology_query()
    assert "No matching" in result


@pytest.mark.asyncio
async def test_query_invalid_where():
    result = await ontology_skills.ontology_query("Person", "bad-json")
    assert "Invalid where_json" in result


@pytest.mark.asyncio
async def test_query_subprocess_error():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(1, "", "query failed"))):
        result = await ontology_skills.ontology_query("Person")
    assert "❌" in result


# ---------------------------------------------------------------------------
# ontology_update_entity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_entity_success():
    entity = {"id": "e1", "type": "Person", "properties": {"name": "Updated"}}
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(entity), ""))):
        result = await ontology_skills.ontology_update_entity("e1", '{"name": "Updated"}')
    assert "✅" in result
    assert "Updated" in result


@pytest.mark.asyncio
async def test_update_entity_not_found():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, "Entity not found: e1", ""))):
        result = await ontology_skills.ontology_update_entity("e1", '{"name": "X"}')
    assert "Entity not found" in result


@pytest.mark.asyncio
async def test_update_entity_invalid_json():
    result = await ontology_skills.ontology_update_entity("e1", "bad-json")
    assert "Invalid properties_json" in result


@pytest.mark.asyncio
async def test_update_entity_error():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(1, "", "update error"))):
        result = await ontology_skills.ontology_update_entity("e1", '{"name": "X"}')
    assert "❌" in result


@pytest.mark.asyncio
async def test_update_entity_bad_json_output():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, "not-json", ""))):
        result = await ontology_skills.ontology_update_entity("e1", '{"name": "X"}')
    assert "❌" in result


# ---------------------------------------------------------------------------
# ontology_relate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_relate_success():
    rel = {"from": "e1", "rel": "knows", "to": "e2"}
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(rel), ""))):
        result = await ontology_skills.ontology_relate("e1", "knows", "e2")
    assert "✅" in result
    assert "knows" in result


@pytest.mark.asyncio
async def test_relate_with_properties():
    rel = {"from": "e1", "rel": "knows", "to": "e2"}
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(rel), ""))):
        result = await ontology_skills.ontology_relate("e1", "knows", "e2", '{"since": "2020"}')
    assert "✅" in result


@pytest.mark.asyncio
async def test_relate_invalid_props():
    result = await ontology_skills.ontology_relate("e1", "knows", "e2", "bad-json")
    assert "Invalid properties_json" in result


@pytest.mark.asyncio
async def test_relate_subprocess_error():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(1, "", "relate error"))):
        result = await ontology_skills.ontology_relate("e1", "knows", "e2")
    assert "❌" in result


@pytest.mark.asyncio
async def test_relate_bad_json_output():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, "not-json", ""))):
        result = await ontology_skills.ontology_relate("e1", "knows", "e2")
    assert "❌" in result


# ---------------------------------------------------------------------------
# ontology_get_related
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_related_success():
    items = [
        {
            "entity": {"id": "e2", "type": "Place", "properties": {"name": "Paris"}},
            "relation": "located_in",
            "direction": "outbound",
        }
    ]
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(items), ""))):
        result = await ontology_skills.ontology_get_related("e1")
    assert "Found 1" in result
    assert "Paris" in result


@pytest.mark.asyncio
async def test_get_related_empty():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, "[]", ""))):
        result = await ontology_skills.ontology_get_related("e1", relation="knows")
    assert "No related" in result


@pytest.mark.asyncio
async def test_get_related_with_relation_arg():
    items = []
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, json.dumps(items), ""))) as mock_run:
        await ontology_skills.ontology_get_related("e1", relation="works_for", direction="inbound")
    args = mock_run.call_args[0][0]
    assert "--rel" in args
    assert "works_for" in args
    assert "inbound" in args


@pytest.mark.asyncio
async def test_get_related_error():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(1, "", "related error"))):
        result = await ontology_skills.ontology_get_related("e1")
    assert "❌" in result


@pytest.mark.asyncio
async def test_get_related_bad_json_output():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, "broken", ""))):
        result = await ontology_skills.ontology_get_related("e1")
    assert "❌" in result


# ---------------------------------------------------------------------------
# ontology_validate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_validate_success():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, "All entities valid.", ""))):
        result = await ontology_skills.ontology_validate()
    assert "valid" in result.lower()


@pytest.mark.asyncio
async def test_validate_empty_output_uses_fallback():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(0, "", ""))):
        result = await ontology_skills.ontology_validate()
    assert result == "Graph is valid."


@pytest.mark.asyncio
async def test_validate_error():
    with patch("ontology_skills._run_ontology", AsyncMock(return_value=(1, "", "schema mismatch"))):
        result = await ontology_skills.ontology_validate()
    assert "❌" in result
