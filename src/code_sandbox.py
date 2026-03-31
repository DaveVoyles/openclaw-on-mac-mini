"""Sandboxed code execution via throwaway Docker containers.

Runs user-provided code in an isolated ``python:3.12-slim`` container with:
- No network access (``--network none``)
- Read-only root filesystem
- 256 MB memory limit
- 30-second timeout
- Dropped capabilities

Returns (stdout, stderr, exit_code).
"""

import asyncio
import logging
import os
import secrets
import tempfile

log = logging.getLogger("openclaw.code_sandbox")

SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "python:3.12-slim")
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "30"))
SANDBOX_MEM_LIMIT = os.getenv("SANDBOX_MEM_LIMIT", "256m")
# Max output size to prevent DoS — 50 KB
MAX_OUTPUT = 50_000


async def run_code(
    code: str,
    language: str = "python",
    stdin_data: str = "",
) -> tuple[str, str, int]:
    """Execute *code* in a sandboxed container.

    Returns ``(stdout, stderr, exit_code)``.
    """
    if language != "python":
        return "", f"Unsupported language: {language}. Only 'python' is supported.", 1

    if not code.strip():
        return "", "No code provided.", 1

    # Write code to a temp file that we'll mount into the container
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="openclaw_sandbox_", delete=False
    ) as f:
        f.write(code)
        code_path = f.name
    os.chmod(code_path, 0o600)

    try:
        cmd = [
            "docker", "run",
            "--rm",
            "--network", "none",
            "--read-only",
            "--memory", SANDBOX_MEM_LIMIT,
            "--cpus", "1.0",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=50m",
            "--name", f"openclaw-sandbox-{secrets.token_hex(8)}",
            "-v", f"{code_path}:/sandbox/code.py:ro",
            SANDBOX_IMAGE,
            "python", "/sandbox/code.py",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if stdin_data else asyncio.subprocess.DEVNULL,
        )

        stdin_bytes = stdin_data.encode() if stdin_data else None
        stdout_raw, stderr_raw = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=SANDBOX_TIMEOUT + 10,  # extra grace for container startup
        )

        stdout = stdout_raw.decode(errors="replace")[:MAX_OUTPUT]
        stderr = stderr_raw.decode(errors="replace")[:MAX_OUTPUT]

        if len(stdout_raw) > MAX_OUTPUT:
            stdout += "\n… (output truncated)"
        if len(stderr_raw) > MAX_OUTPUT:
            stderr += "\n… (output truncated)"

        return stdout, stderr, proc.returncode or 0

    except asyncio.TimeoutError:
        # Kill the container if it's still running
        try:
            kill_proc = await asyncio.create_subprocess_exec(
                "docker", "kill", f"openclaw-sandbox-{os.getpid()}-{id(code) % 100000}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await kill_proc.wait()
        except Exception as exc:
            log.debug("Failed to kill sandbox container: %s", exc)
        return "", f"Execution timed out after {SANDBOX_TIMEOUT}s.", 1

    except Exception as e:
        log.error("Sandbox execution error: %s", e)
        return "", f"Sandbox error: {e}", 1

    finally:
        # Clean up the temp file
        try:
            os.unlink(code_path)
        except OSError:
            pass
