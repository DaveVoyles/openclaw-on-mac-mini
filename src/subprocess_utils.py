"""Shared async subprocess runner used by skills and analyzer modules."""

import asyncio

COMMAND_TIMEOUT = 15  # seconds


async def run(cmd: list[str], timeout: int = COMMAND_TIMEOUT) -> tuple[int, str, str]:
    """Run a subprocess asynchronously and return (returncode, stdout, stderr)."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
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
