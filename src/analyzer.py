"""
OpenClaw Analyzer — Phase 5: AI-Powered Log Analysis
Uses the existing Gemini LLM to analyze container logs and suggest fixes.
"""

import logging

from subprocess_utils import run as _run

log = logging.getLogger("openclaw.analyzer")

COMMAND_TIMEOUT = 15


async def analyze_logs(service: str, lines: int = 50) -> str:
    """
    Fetch container logs and use the LLM to analyze them.
    Returns a structured analysis including errors, warnings, and suggestions.
    """
    lines = min(max(lines, 10), 200)

    # Fetch logs (subprocess exec — no shell expansion, service name passed directly)
    rc, stdout, stderr = await _run(
        ["docker", "logs", service, "--tail", str(lines), "--timestamps"],
        timeout=20,
    )
    if rc != 0:
        return f"❌ Could not fetch logs for '{service}': {stderr.strip()}"

    log_text = stdout.strip()
    if not log_text:
        return f"✅ No log output from '{service}' (last {lines} lines are empty)."

    # Use LLM for analysis
    try:
        from llm import chat as llm_chat
        from llm import is_configured as llm_is_configured
        if not llm_is_configured():
            return _basic_analysis(service, log_text)

        prompt = (
            f"Analyze these Docker container logs for '{service}'. "
            f"Identify errors, warnings, and notable patterns. "
            f"Suggest fixes for any issues found. Be concise.\n\n"
            f"```\n{log_text[:3000]}\n```"
        )

        response_text, _, _ = await llm_chat(
            user_message=prompt,
            history=None,
            user_name="OpenClaw Analyzer",
        )
        return response_text

    except Exception as e:  # broad: intentional
        log.warning("LLM analysis failed, falling back to basic: %s", e)
        return _basic_analysis(service, log_text)


def _basic_analysis(service: str, log_text: str) -> str:
    """Fallback analysis without LLM — pattern matching for common issues."""
    lines = log_text.split("\n")
    errors = []
    warnings = []
    info_count = 0

    for line in lines:
        lower = line.lower()
        if any(kw in lower for kw in ("error", "fail", "fatal", "exception", "panic")):
            errors.append(line.strip()[:150])
        elif any(kw in lower for kw in ("warn", "warning", "deprecated")):
            warnings.append(line.strip()[:150])
        else:
            info_count += 1

    result = [f"**Log Analysis: {service}** ({len(lines)} lines)\n"]

    if errors:
        result.append(f"🔴 **Errors** ({len(errors)}):")
        for e in errors[:5]:
            result.append(f"  `{e}`")

    if warnings:
        result.append(f"\n🟡 **Warnings** ({len(warnings)}):")
        for w in warnings[:5]:
            result.append(f"  `{w}`")

    if not errors and not warnings:
        result.append("✅ No errors or warnings detected.")

    result.append(f"\nℹ️ {info_count} informational lines")
    return "\n".join(result)


async def suggest_fixes(service: str) -> str:
    """Analyze a service's logs and suggest actionable fixes."""
    return await analyze_logs(service, lines=100)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ANALYZER_SKILLS = {
    "analyze_logs": analyze_logs,
    "suggest_fixes": suggest_fixes,
}
