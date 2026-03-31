"""
OpenClaw Ollama Tool Calling — Phase 5 enhancement
Implements Ollama's native tool calling protocol for local Gemma models.

Ollama supports OpenAI-compatible tool definitions via /api/chat.
This module converts our tool declarations to Ollama format and handles
the tool-calling loop.
"""

import logging

import aiohttp

log = logging.getLogger("openclaw.ollama_tools")

# Subset of tools safe for local execution (read-only, no approvals needed)
OLLAMA_TOOL_ALLOWLIST = frozenset({
    # System monitoring (safe, read-only)
    "get_system_stats",
    "get_docker_stats",
    "get_uptime",
    "list_containers",
    "get_container_status",
    "get_container_logs",
    # Media monitoring (read-only)
    "check_arr_health",
    "check_download_clients",
    "check_plex_status",
    "get_plex_activity",
    "get_download_queue",
    "get_recent_additions",
    # Network (read-only)
    "get_network_status",
    "get_tailscale_status",
    # Memory (read-only recall)
    "recall_fact",
    "list_memories",
    # Weather
    "get_weather",
    # Code execution (sandboxed)
    "execute_python_code",
})

# Max tool rounds for Ollama (keep low — local model is less reliable)
OLLAMA_MAX_TOOL_ROUNDS = 3


def convert_tools_for_ollama(tool_declarations: list[dict]) -> list[dict]:
    """Convert our tool declarations (Gemini format) to Ollama/OpenAI format.

    Only includes tools in the allowlist.
    """
    tools = []
    for decl in tool_declarations:
        name = decl["name"]
        if name not in OLLAMA_TOOL_ALLOWLIST:
            continue

        # Convert to OpenAI-compatible function calling format
        properties = {}
        required = decl.get("parameters", {}).get("required", [])
        for prop_name, prop_def in decl.get("parameters", {}).get("properties", {}).items():
            properties[prop_name] = {
                "type": prop_def.get("type", "string"),
                "description": prop_def.get("description", ""),
            }

        tool = {
            "type": "function",
            "function": {
                "name": name,
                "description": decl.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
        tools.append(tool)

    return tools


async def chat_ollama_with_tools(
    user_message: str,
    history: list[dict],
    system_prompt: str,
    tool_declarations: list[dict],
    execute_fn,  # async callable: (name, args) -> str
    *,
    ollama_url: str,
    ollama_model: str,
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> tuple[str | None, list[tuple[str, dict, str]]]:
    """Send a message to Ollama with tool calling support.

    Returns (response_text, tool_calls_made) where tool_calls_made is a list
    of (tool_name, args, result) tuples.
    Returns (None, []) on failure.
    """
    tools = convert_tools_for_ollama(tool_declarations)
    if not tools:
        return None, []

    # Build messages
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for msg in history[-10:]:
        role = msg["role"]
        if role == "model":
            role = "assistant"
        content = " ".join(p for p in msg["parts"] if isinstance(p, str))
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    tool_calls_made: list[tuple[str, dict, str]] = []

    for round_num in range(OLLAMA_MAX_TOOL_ROUNDS):
        payload = {
            "model": ollama_model,
            "messages": messages,
            "stream": False,
            "tools": tools,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{ollama_url}/api/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        log.warning("Ollama tools returned HTTP %d", resp.status)
                        return None, tool_calls_made
                    data = await resp.json()
        except Exception as e:
            log.warning("Ollama tool call failed: %s", e)
            return None, tool_calls_made

        message = data.get("message", {})
        tool_calls = message.get("tool_calls", [])

        if not tool_calls:
            # No more tool calls — return the text response
            return message.get("content", ""), tool_calls_made

        # Execute tool calls
        messages.append(message)  # Add assistant's tool_call message

        for tc in tool_calls:
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            fn_args = fn.get("arguments", {})

            if fn_name not in OLLAMA_TOOL_ALLOWLIST:
                result = f"Tool '{fn_name}' is not available in local mode."
                log.warning("Ollama tried to call non-allowlisted tool: %s", fn_name)
            else:
                log.info("Ollama invoking tool: %s(%s) [round %d]", fn_name, fn_args, round_num + 1)
                try:
                    result = await execute_fn(fn_name, fn_args)
                except Exception as e:
                    result = f"Error: {e}"

            tool_calls_made.append((fn_name, fn_args, result))

            # Add tool result as a tool response message
            messages.append({
                "role": "tool",
                "content": result,
            })

    # Hit max rounds — try to get a final response without tools
    try:
        payload = {
            "model": ollama_model,
            "messages": messages + [{"role": "user", "content": "Please summarize your findings."}],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{ollama_url}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("message", {}).get("content", ""), tool_calls_made
    except Exception:
        pass

    return None, tool_calls_made
