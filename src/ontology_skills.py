"""
OpenClaw Ontology Skills
Structured graph memory backed by the installed ClawHub ontology script.
"""

import json
import logging
import sys
from pathlib import Path

from subprocess_utils import run as _run

log = logging.getLogger("openclaw.ontology")

_ROOT = Path(__file__).parent.parent
_ONTOLOGY_SCRIPT = _ROOT / "skills" / "ontology" / "scripts" / "ontology.py"
_GRAPH_PATH = "data/memory/ontology/graph.jsonl"
_SCHEMA_PATH = "data/memory/ontology/schema.yaml"
_ONTOLOGY_TIMEOUT = 20


def _missing_skill_message() -> str:
    return (
        "❌ Ontology skill is not installed. Run: "
        "npx clawhub@latest install ontology"
    )


def _safe_json_loads(text: str, context: str = "ontology"):
    """Parse JSON from subprocess output, returning (parsed, None) or (None, error_str)."""
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return None, f"❌ Unexpected output from {context}: {text[:200]}"


def _normalize_json(value: str | None, *, default: str = "{}") -> str:
    raw = value if value and value.strip() else default
    parsed = json.loads(raw)
    return json.dumps(parsed)


async def _run_ontology(args: list[str]) -> tuple[int, str, str]:
    if not _ONTOLOGY_SCRIPT.exists():
        return 1, "", _missing_skill_message()
    cmd = [sys.executable, str(_ONTOLOGY_SCRIPT), *args]
    return await _run(cmd, timeout=_ONTOLOGY_TIMEOUT)


def _entity_label(entity: dict) -> str:
    props = entity.get("properties", {})
    summary = (
        props.get("name")
        or props.get("title")
        or props.get("description")
        or props.get("content")
        or "(no label)"
    )
    return f"{entity.get('id', '?')} [{entity.get('type', '?')}] {summary}"


def _format_entity(entity: dict) -> str:
    return json.dumps(entity, indent=2)


def _format_entity_list(items: list[dict]) -> str:
    if not items:
        return "No matching entities found."
    lines = [f"Found {len(items)} matching entities:"]
    for entity in items[:10]:
        lines.append(f"• {_entity_label(entity)}")
    if len(items) == 1:
        lines.append("")
        lines.append(_format_entity(items[0]))
    return "\n".join(lines)


def _format_related(items: list[dict]) -> str:
    if not items:
        return "No related entities found."
    lines = [f"Found {len(items)} related entities:"]
    for item in items[:10]:
        entity = item.get("entity", {})
        rel = item.get("relation", "related_to")
        direction = item.get("direction")
        prefix = f"{direction} " if direction else ""
        lines.append(f"• {prefix}{rel}: {_entity_label(entity)}")
    return "\n".join(lines)


async def ontology_create_entity(entity_type: str, properties_json: str, entity_id: str = "") -> str:
    """Create an ontology entity in the local graph store."""
    try:
        props = _normalize_json(properties_json)
    except json.JSONDecodeError as exc:
        return f"❌ Invalid properties_json: {exc}"

    args = [
        "create",
        "--type", entity_type,
        "--props", props,
        "--graph", _GRAPH_PATH,
    ]
    if entity_id.strip():
        args.extend(["--id", entity_id.strip()])

    rc, out, err = await _run_ontology(args)
    if rc != 0:
        return f"❌ Failed to create ontology entity: {err.strip() or out.strip()}"

    entity, err_msg = _safe_json_loads(out)
    if err_msg:
        return err_msg

    # Embed entity into vector store for semantic recall
    try:
        import vector_store
        label = _entity_label(entity)
        entity_vid = entity.get("id", entity_type)
        await vector_store.add_memory(
            f"ontology_{entity_vid}",
            f"[Ontology: {entity_type}] {label}",
            tags=[entity_type, "ontology"],
        )
    except Exception:
        pass  # non-critical

    return f"✅ Created ontology entity:\n{_format_entity(entity)}"


async def ontology_get_entity(entity_id: str) -> str:
    """Fetch a single ontology entity by ID."""
    rc, out, err = await _run_ontology([
        "get",
        "--id", entity_id,
        "--graph", _GRAPH_PATH,
    ])
    if rc != 0:
        return f"❌ Failed to get ontology entity: {err.strip() or out.strip()}"
    if out.strip().startswith("Entity not found"):
        return out.strip()
    entity, err_msg = _safe_json_loads(out)
    if err_msg:
        return err_msg
    return _format_entity(entity)


async def ontology_query(entity_type: str = "", where_json: str = "{}") -> str:
    """Query ontology entities by type and property filter."""
    try:
        where = _normalize_json(where_json)
    except json.JSONDecodeError as exc:
        return f"❌ Invalid where_json: {exc}"

    args = ["query"]
    if entity_type.strip():
        args.extend(["--type", entity_type.strip()])
    args.extend(["--where", where, "--graph", _GRAPH_PATH])

    rc, out, err = await _run_ontology(args)
    if rc != 0:
        return f"❌ Failed to query ontology: {err.strip() or out.strip()}"

    results, err_msg = _safe_json_loads(out)
    if err_msg:
        return err_msg
    return _format_entity_list(results)


async def ontology_update_entity(entity_id: str, properties_json: str) -> str:
    """Update properties on an ontology entity."""
    try:
        props = _normalize_json(properties_json)
    except json.JSONDecodeError as exc:
        return f"❌ Invalid properties_json: {exc}"

    rc, out, err = await _run_ontology([
        "update",
        "--id", entity_id,
        "--props", props,
        "--graph", _GRAPH_PATH,
    ])
    if rc != 0:
        return f"❌ Failed to update ontology entity: {err.strip() or out.strip()}"
    if out.strip().startswith("Entity not found"):
        return out.strip()
    entity, err_msg = _safe_json_loads(out)
    if err_msg:
        return err_msg
    return f"✅ Updated ontology entity:\n{_format_entity(entity)}"


async def ontology_relate(from_id: str, relation: str, to_id: str, properties_json: str = "{}") -> str:
    """Create a relation between two ontology entities."""
    try:
        props = _normalize_json(properties_json)
    except json.JSONDecodeError as exc:
        return f"❌ Invalid properties_json: {exc}"

    rc, out, err = await _run_ontology([
        "relate",
        "--from", from_id,
        "--rel", relation,
        "--to", to_id,
        "--props", props,
        "--graph", _GRAPH_PATH,
    ])
    if rc != 0:
        return f"❌ Failed to create ontology relation: {err.strip() or out.strip()}"
    data, err_msg = _safe_json_loads(out)
    if err_msg:
        return err_msg
    return (
        f"✅ Created relation: {data.get('from')} -[{data.get('rel')}]-> {data.get('to')}"
    )


async def ontology_get_related(entity_id: str, relation: str = "", direction: str = "both") -> str:
    """Get entities related to a given ontology entity."""
    args = [
        "related",
        "--id", entity_id,
        "--dir", direction,
        "--graph", _GRAPH_PATH,
    ]
    if relation.strip():
        args.extend(["--rel", relation.strip()])

    rc, out, err = await _run_ontology(args)
    if rc != 0:
        return f"❌ Failed to get related ontology entities: {err.strip() or out.strip()}"

    results, err_msg = _safe_json_loads(out)
    if err_msg:
        return err_msg
    return _format_related(results)


async def ontology_validate() -> str:
    """Validate the ontology graph against the local schema."""
    rc, out, err = await _run_ontology([
        "validate",
        "--graph", _GRAPH_PATH,
        "--schema", _SCHEMA_PATH,
    ])
    if rc != 0:
        return f"❌ Failed to validate ontology graph: {err.strip() or out.strip()}"
    return out.strip() or "Graph is valid."


ONTOLOGY_SKILLS = {
    "ontology_create_entity": ontology_create_entity,
    "ontology_get_entity": ontology_get_entity,
    "ontology_query": ontology_query,
    "ontology_update_entity": ontology_update_entity,
    "ontology_relate": ontology_relate,
    "ontology_get_related": ontology_get_related,
    "ontology_validate": ontology_validate,
}
