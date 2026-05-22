#!/usr/bin/env python3
"""
new_skill.py — Scaffold a new OpenClaw skill.

Creates a Python stub function and prints the registration snippets needed to
wire it into `skills/__init__.py` and `config/tools.yaml`.

Usage (interactive):
    python scripts/new_skill.py

Usage (non-interactive, agent-friendly):
    python scripts/new_skill.py --json '{"name":"check_plex_status","description":"Check Plex server health","params":[{"name":"verbose","type":"bool","desc":"Include extended diagnostics","required":false}],"module":"skills/monitor_skills.py"}'

JSON schema for --json:
    {
      "name":        str   (required) — snake_case function name
      "description": str   (required) — one-line LLM tool description
      "params":      list  (optional) — list of {name, type, desc, required}
      "module":      str   (optional) — relative path, e.g. "skills/my_module.py"
    }
"""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_INIT = REPO_ROOT / "skills" / "__init__.py"
TOOLS_YAML = REPO_ROOT / "config" / "tools.yaml"


def slugify(text: str) -> str:
    """Convert a human name to snake_case."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9_]", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def _build_snippets(name: str, description: str, params: list, module_hint: str) -> tuple[str, str, str, Path]:
    """Return (stub, skills_snippet, yaml_snippet, target_path)."""
    if module_hint:
        target_path = REPO_ROOT / module_hint
    else:
        target_path = SKILLS_INIT

    py_params = ", ".join(f'{p["name"]}: {p["type"]}' for p in params) if params else ""

    param_lines = ""
    for p in params:
        param_lines += f"\n    :param {p['name']}: {p['desc']}"

    stub = f'''
async def {name}({py_params}) -> str:
    """{description}.{param_lines}
    :return: Human-readable result string.
    """
    # TODO: implement {name}
    return f"❌ {name} not yet implemented."
'''

    skills_snippet = f'    "{name}": {name},'

    required_params = [p for p in params if p.get("required", True)]

    yaml_lines = [f'- name: {name}']
    yaml_lines.append(f'  description: |-')
    yaml_lines.append(f'    {description}')
    if params:
        yaml_lines.append('  parameters:')
        yaml_lines.append('    type: object')
        yaml_lines.append('    properties:')
        for p in params:
            ptype = p.get("type", "str")
            yaml_type = (
                "integer" if ptype in ("int",) else
                "number" if ptype in ("float",) else
                "boolean" if ptype in ("bool",) else
                "string"
            )
            yaml_lines.append(f'      {p["name"]}:')
            yaml_lines.append(f'        type: {yaml_type}')
            yaml_lines.append(f'        description: {p["desc"]}')
        if required_params:
            yaml_lines.append('    required:')
            for p in required_params:
                yaml_lines.append(f'      - {p["name"]}')

    yaml_snippet = "\n".join(yaml_lines)
    return stub, skills_snippet, yaml_snippet, target_path


def _print_output(stub: str, skills_snippet: str, yaml_snippet: str, target_path: Path) -> None:
    print("\n" + "=" * 60)
    print(f"📄  1. Add this function to: {target_path.relative_to(REPO_ROOT)}")
    print("=" * 60)
    print(stub)

    print("=" * 60)
    print(f"📋  2. Register in skills/__init__.py  (inside the SKILLS dict)")
    print("=" * 60)
    print(f"    # In the SKILLS = {{...}} dict near the bottom:")
    print(skills_snippet)

    print("\n" + "=" * 60)
    print("📋  3. Add to config/tools.yaml  (at the end of the file)")
    print("=" * 60)
    print(yaml_snippet)

    print("\n" + "=" * 60)
    print("✅  4. Update docs/SKILLS-CATALOG.md  with a one-liner in the")
    print("      appropriate section.")
    print("=" * 60)
    print("\nDone! Review the snippets above, paste them into the right files,")
    print("then restart OpenClaw with:")
    print("  cd ~/docker-stack/openclaw && docker compose restart openclaw")


def _run_json_mode(json_str: str) -> None:
    """Non-interactive mode: parse JSON spec and print scaffolding."""
    try:
        spec = json.loads(json_str)
    except json.JSONDecodeError as exc:
        print(f"❌ Invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    raw_name = spec.get("name", "").strip()
    if not raw_name:
        print("❌ JSON must include 'name'.", file=sys.stderr)
        sys.exit(1)

    name = slugify(raw_name)
    description = spec.get("description", f"Run {name}.").strip() or f"Run {name}."
    params = spec.get("params", [])
    module_hint = spec.get("module", "")

    stub, skills_snippet, yaml_snippet, target_path = _build_snippets(name, description, params, module_hint)
    _print_output(stub, skills_snippet, yaml_snippet, target_path)


def _run_interactive_mode() -> None:
    """Interactive mode: prompt user for each field."""
    print("🛠  OpenClaw New Skill Scaffold\n")

    raw_name = input("Skill function name (snake_case, e.g. check_plex_status): ").strip()
    if not raw_name:
        print("❌ Name required.")
        sys.exit(1)

    name = slugify(raw_name)
    if name != raw_name:
        print(f"   → normalized to: {name}")

    description = input("One-line description for the LLM (what does this skill do?): ").strip()
    if not description:
        description = f"Run {name}."

    has_param = input("Does it take any parameters? [y/N]: ").strip().lower() == "y"
    params: list[dict] = []
    if has_param:
        while True:
            pname = input("  Parameter name (blank to finish): ").strip()
            if not pname:
                break
            ptype = input(f"  Type of {pname} [str]: ").strip() or "str"
            pdesc = input(f"  Description of {pname}: ").strip()
            required = input(f"  Required? [Y/n]: ").strip().lower() != "n"
            params.append({"name": pname, "type": ptype, "desc": pdesc, "required": required})

    module_hint = input(
        "\nModule to add the function to (e.g. skills/my_module.py) — blank to add to skills/__init__.py: "
    ).strip()

    stub, skills_snippet, yaml_snippet, target_path = _build_snippets(name, description, params, module_hint)
    _print_output(stub, skills_snippet, yaml_snippet, target_path)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--json":
        _run_json_mode(sys.argv[2])
    elif len(sys.argv) == 1:
        _run_interactive_mode()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
