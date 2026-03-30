"""
OpenClaw Worker Agent — Sub-agent spawning for autonomous task delegation.

The main Gemini agent can call spawn_worker() to delegate a focused subtask to
a lightweight sub-agent that runs its own tool loop and returns the result.

Usage pattern:
  - User asks a complex question with multiple independent sub-tasks
  - Main agent calls spawn_worker(goal="...", context="...")
  - Worker gets a fresh Gemini session, runs tools, returns a clean answer
  - Main agent synthesizes the worker's result with its own reasoning

This enables genuine parallel / delegated work within a single interaction.
"""

import asyncio
import logging

from google import genai

log = logging.getLogger("openclaw.worker")

_WORKER_SYSTEM_PROMPT = (
    "You are a focused worker sub-agent for OpenClaw. You have been assigned a specific task.\n"
    "Rules:\n"
    "- Complete the task fully using whatever tools are needed.\n"
    "- Be thorough but concise in your final answer — return facts, not reasoning.\n"
    "- If a tool fails, try an alternative approach once, then report what you found.\n"
    "- Do NOT mention being a sub-agent or worker in your output.\n"
    "- Return only the final result text — no preamble."
)


async def spawn_worker(
    goal: str,
    context: str = "",
    max_rounds: int = 8,
    conversation_history: list[dict] | None = None,
) -> str:
    """
    Spawn a focused sub-agent to accomplish a specific goal autonomously.

    The worker gets a fresh Gemini chat session with a task-focused system prompt,
    runs its own parallel tool loop, and returns a clean result string.

    Args:
        goal:       Clear description of what the worker should accomplish.
        context:    Optional background information or constraints.
        max_rounds: Maximum tool call rounds (default 8, capped at MAX_TOOL_ROUNDS).
        conversation_history: Optional recent conversation turns for context inheritance.

    Returns:
        The worker's synthesized result as a string.
    """
    # Import lazily to avoid circular imports at module load time
    from llm import (
        _get_model,
        _execute_function_call,
        _rate_limiter,
        _record_usage,
        MAX_TOOL_ROUNDS,
        _build_tools,
        _load_system_prompt,
        MODEL_NAME,
        GOOGLE_API_KEY,
        TEMPERATURE,
        MAX_TOKENS,
    )
    import os

    if not GOOGLE_API_KEY:
        return "❌ Worker: GOOGLE_API_KEY not set."

    if not _rate_limiter.check():
        return "❌ Worker: rate limit reached — try again in a moment."

    effective_rounds = min(max_rounds, MAX_TOOL_ROUNDS)

    # Build a dedicated worker chat session with its own system prompt
    client = genai.Client(api_key=GOOGLE_API_KEY)
    worker_config = genai.types.GenerateContentConfig(
        system_instruction=_WORKER_SYSTEM_PROMPT,
        tools=_build_tools(),
        max_output_tokens=MAX_TOKENS,
        temperature=max(0.1, TEMPERATURE - 0.2),  # slightly more deterministic
    )

    initial_message = goal
    if context:
        initial_message = f"Context: {context}\n\nTask: {goal}"

    # Inject recent conversation history for context inheritance
    if conversation_history:
        recent = conversation_history[-5:]  # last 5 turns
        history_lines = []
        for msg in recent:
            role = msg.get("role", "user")
            parts = msg.get("parts", [])
            text = " ".join(p for p in parts if isinstance(p, str))
            if text:
                history_lines.append(f"  {role}: {text[:200]}")
        if history_lines:
            history_summary = "\n".join(history_lines)
            initial_message = (
                f"Recent conversation context:\n{history_summary}\n\n{initial_message}"
            )

    log.info("Worker spawned for goal: %.80s…", goal)

    try:
        chat_session = client.chats.create(
            model=MODEL_NAME, config=worker_config, history=[],
        )
        loop = asyncio.get_running_loop()

        _rate_limiter.record()
        response = await loop.run_in_executor(
            None, lambda: chat_session.send_message(initial_message)
        )
        await _record_usage(response)

        rounds = 0
        while rounds < effective_rounds:
            try:
                all_parts = response.candidates[0].content.parts
            except (IndexError, AttributeError):
                break

            function_calls = [
                (part.function_call.name, dict(part.function_call.args) if part.function_call.args else {})
                for part in all_parts
                if hasattr(part, "function_call") and part.function_call.name
            ]

            if not function_calls:
                break

            log.info("Worker tool call(s) [round %d]: %s", rounds + 1,
                     ", ".join(f for f, _ in function_calls))

            # Execute all tool calls in parallel
            results = await asyncio.gather(*[
                _execute_function_call(fn_name, fn_args)
                for fn_name, fn_args in function_calls
            ])

            if not _rate_limiter.check():
                return "⚠️ Worker: rate limit hit mid-task. Partial results unavailable."

            response_parts = [
                genai.types.Part(
                    function_response=genai.types.FunctionResponse(
                        name=fn_name,
                        response={"result": result},
                    )
                )
                for (fn_name, _), result in zip(function_calls, results)
            ]

            _rate_limiter.record()
            response = await loop.run_in_executor(
                None,
                lambda parts=response_parts: chat_session.send_message(parts),
            )
            await _record_usage(response)
            rounds += 1

        # Extract final text
        try:
            result_text = response.text
        except (AttributeError, ValueError):
            result_text = ""
            try:
                parts = response.candidates[0].content.parts
                result_text = "".join(p.text for p in parts if hasattr(p, "text") and p.text)
            except Exception as exc:
                log.debug("Worker response text extraction fallback failed: %s", exc)

        if not result_text:
            result_text = "Worker completed but returned no output."

        log.info("Worker finished (rounds=%d): %.80s…", rounds, result_text)
        return result_text

    except Exception as e:
        log.error("Worker agent error: %s", e, exc_info=True)
        return f"❌ Worker failed: {e}"


WORKER_SKILLS = {
    "spawn_worker": spawn_worker,
}
