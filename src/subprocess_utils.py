"""Shared async subprocess runner used by skills and analyzer modules."""

import asyncio
import os
from pathlib import Path

COMMAND_TIMEOUT = 15  # seconds


async def run(
    cmd: list[str],
    timeout: int = COMMAND_TIMEOUT,
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess asynchronously and return ``(returncode, stdout, stderr)``."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(Path(cwd).expanduser()) if cwd else None,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        return 1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return 1, "", f"Command not found: {cmd[0]}"
