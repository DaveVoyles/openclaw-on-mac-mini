"""SSH bridge: run host `copilot` CLI from Slack with identity-equivalent access.

⚠️  HIGH-RISK MODULE — this opens an SSH session as `davevoyles@<host>` and
runs `copilot --allow-all-tools`, granting the Slack caller full identity-
equivalent access to the Mac Mini host (file system, network, Docker, etc.).

Defense in depth:
  1. Slack caller is gated by an explicit user-ID allowlist in slack_bot.py
  2. A *dedicated* SSH keypair is used (revocable independent of other keys)
  3. Every invocation produces a session UUID, a JSONL audit row, and a
     full transcript file under ``data/audit/host_bridge/``
  4. Wall-clock timeout (default 10 minutes) caps any single command
  5. Output is sanitised against well-known secret patterns before being
     posted back to Slack

Environment configuration (read once at module import):
  OPENCLAW_HOST_BRIDGE_ENABLED         "true" to permit any execution
  OPENCLAW_HOST_BRIDGE_HOST            target SSH host (default host.docker.internal)
  OPENCLAW_HOST_BRIDGE_USER            target user (default davevoyles)
  OPENCLAW_HOST_BRIDGE_KEY             private key path inside container
  OPENCLAW_HOST_BRIDGE_KNOWN_HOSTS     known_hosts file path inside container
  OPENCLAW_HOST_BRIDGE_WORKDIR         remote cwd (default /Users/davevoyles/docker-stack)
  OPENCLAW_HOST_BRIDGE_COPILOT_BIN     absolute path to copilot binary on host
  OPENCLAW_HOST_BRIDGE_TIMEOUT_S       per-command timeout in seconds (default 600)
  OPENCLAW_HOST_BRIDGE_MAX_OUTPUT      max bytes of stdout to capture (default 200_000)

The Slack handler is responsible for enforcing the user allowlist; this module
will refuse to run when ``OPENCLAW_HOST_BRIDGE_ENABLED`` is not true but does
not itself know which Slack user invoked it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (resolved at import; can be overridden via env in tests)
# ---------------------------------------------------------------------------


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _enabled() -> bool:
    return _env("OPENCLAW_HOST_BRIDGE_ENABLED", "false").lower() == "true"


HOST = _env("OPENCLAW_HOST_BRIDGE_HOST", "host.docker.internal")
USER = _env("OPENCLAW_HOST_BRIDGE_USER", "davevoyles")
KEY_PATH = _env("OPENCLAW_HOST_BRIDGE_KEY", "/home/openclaw/.ssh/host_bridge_ed25519")
KNOWN_HOSTS = _env("OPENCLAW_HOST_BRIDGE_KNOWN_HOSTS", "/home/openclaw/.ssh/known_hosts")
WORKDIR = _env("OPENCLAW_HOST_BRIDGE_WORKDIR", "/Users/davevoyles/docker-stack")
COPILOT_BIN = _env("OPENCLAW_HOST_BRIDGE_COPILOT_BIN", "/opt/homebrew/bin/copilot")
TIMEOUT_S = int(_env("OPENCLAW_HOST_BRIDGE_TIMEOUT_S", "600") or "600")
MAX_OUTPUT = int(_env("OPENCLAW_HOST_BRIDGE_MAX_OUTPUT", "200000") or "200000")

# Audit log location: respect AUDIT_DIR env (writable mount in production)
# and fall back to a repo-local path for local dev / tests.
_AUDIT_ROOT = Path(os.getenv("AUDIT_DIR") or str(Path(__file__).resolve().parent.parent / "data" / "audit"))
_AUDIT_DIR = _AUDIT_ROOT / "host_bridge"
_AUDIT_LOG = _AUDIT_ROOT / "host_bridge.jsonl"


def _ensure_audit_dirs() -> None:
    try:
        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("host_bridge: audit dir create failed: %s", exc)


# ---------------------------------------------------------------------------
# Secret sanitisation
# ---------------------------------------------------------------------------

# Conservative patterns — match before posting any captured output to Slack.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),               # Slack tokens
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),                       # GitHub PAT
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),               # fine-grained PAT
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),                    # Google API keys
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                        # OpenAI-style
    re.compile(r"AKIA[0-9A-Z]{16}"),                           # AWS access key id
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),          # Bearer tokens
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
]


def sanitize(text: str) -> str:
    """Redact well-known secret shapes from ``text`` before sending to Slack."""
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("«REDACTED»", out)
    return out


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class BridgeResult:
    session_id: str
    success: bool
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    truncated: bool = False
    error: str | None = None
    transcript_path: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def _write_audit_row(row: dict[str, Any]) -> None:
    _ensure_audit_dirs()
    try:
        with _AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("host_bridge: audit append failed: %s", exc)


def _write_transcript(session_id: str, payload: dict[str, Any]) -> str | None:
    _ensure_audit_dirs()
    path = _AUDIT_DIR / f"{session_id}.log"
    try:
        with path.open("w", encoding="utf-8") as fh:
            fh.write(f"# session {session_id}\n")
            fh.write(f"# prompt: {payload.get('prompt','')!r}\n")
            fh.write(f"# user:   {payload.get('slack_user_id','?')}\n")
            fh.write(f"# host:   {USER}@{HOST}\n")
            fh.write(f"# cwd:    {payload.get('workdir', WORKDIR)}\n")
            fh.write(f"# exit:   {payload.get('exit_code')}\n")
            fh.write(f"# took:   {payload.get('duration_s',0):.2f}s\n")
            fh.write("# ===== STDOUT =====\n")
            fh.write(payload.get("stdout", "") or "")
            fh.write("\n# ===== STDERR =====\n")
            fh.write(payload.get("stderr", "") or "")
            fh.write("\n")
        return str(path)
    except OSError as exc:
        log.warning("host_bridge: transcript write failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _build_remote_cmd(prompt: str, workdir: str) -> str:
    """Build the exact shell string executed inside the SSH login shell.

    The prompt is shell-quoted; ``bash -lc`` is used so the host's PATH and
    Copilot CLI auth profile (~/.config/copilot) are picked up the same way
    as an interactive davevoyles shell.
    """
    q_prompt = shlex.quote(prompt)
    q_workdir = shlex.quote(workdir)
    q_bin = shlex.quote(COPILOT_BIN)
    inner = f"cd {q_workdir} && {q_bin} --allow-all-tools -p {q_prompt}"
    return f"bash -lc {shlex.quote(inner)}"


async def run_copilot(
    *,
    prompt: str,
    slack_user_id: str,
    workdir: str | None = None,
    timeout_s: int | None = None,
) -> BridgeResult:
    """Run ``copilot -p <prompt>`` on the host over SSH and return its output.

    Returns a :class:`BridgeResult` whether or not execution succeeded. Errors
    (auth, timeout, network) populate ``error`` and set ``success`` to False.
    """
    session_id = uuid.uuid4().hex[:12]
    workdir = workdir or WORKDIR
    timeout = timeout_s if (timeout_s and timeout_s > 0) else TIMEOUT_S
    start_ts = time.monotonic()
    started_at = time.time()

    if not _enabled():
        return BridgeResult(
            session_id=session_id,
            success=False,
            exit_code=None,
            stdout="",
            stderr="",
            duration_s=0.0,
            error="host bridge disabled — set OPENCLAW_HOST_BRIDGE_ENABLED=true",
        )

    if not prompt or not prompt.strip():
        return BridgeResult(
            session_id=session_id,
            success=False,
            exit_code=None,
            stdout="",
            stderr="",
            duration_s=0.0,
            error="empty prompt",
        )

    key_file = Path(KEY_PATH)
    if not key_file.exists():
        return BridgeResult(
            session_id=session_id,
            success=False,
            exit_code=None,
            stdout="",
            stderr="",
            duration_s=0.0,
            error=f"SSH key not found at {KEY_PATH}",
        )

    try:
        import asyncssh  # lazy import — module is optional at test/install time
    except ImportError as exc:
        return BridgeResult(
            session_id=session_id,
            success=False,
            exit_code=None,
            stdout="",
            stderr="",
            duration_s=0.0,
            error=f"asyncssh not installed: {exc}",
        )

    remote_cmd = _build_remote_cmd(prompt, workdir)
    log.info(
        "host_bridge[%s]: user=%s host=%s timeout=%ss prompt=%r",
        session_id, slack_user_id, HOST, timeout, prompt[:120],
    )

    stdout_buf = ""
    stderr_buf = ""
    exit_code: int | None = None
    error: str | None = None
    truncated = False

    try:
        known_hosts = KNOWN_HOSTS if Path(KNOWN_HOSTS).exists() else None
        async with asyncssh.connect(  # type: ignore[attr-defined]
            HOST,
            username=USER,
            client_keys=[str(key_file)],
            known_hosts=known_hosts,
        ) as conn:
            result = await conn.run(remote_cmd, check=False, timeout=timeout)
            stdout_buf = (result.stdout or "") if isinstance(result.stdout, str) else (result.stdout.decode("utf-8", "replace") if result.stdout else "")
            stderr_buf = (result.stderr or "") if isinstance(result.stderr, str) else (result.stderr.decode("utf-8", "replace") if result.stderr else "")
            exit_code = result.exit_status if result.exit_status is not None else None
    except Exception as exc:  # broad: surface as error to Slack instead of crashing the bot
        error = f"{type(exc).__name__}: {exc}"
        log.warning("host_bridge[%s]: SSH/exec failed: %s", session_id, error)

    if len(stdout_buf) > MAX_OUTPUT:
        truncated = True
        stdout_buf = stdout_buf[:MAX_OUTPUT] + f"\n…[truncated {len(stdout_buf)-MAX_OUTPUT} bytes]"
    if len(stderr_buf) > MAX_OUTPUT:
        truncated = True
        stderr_buf = stderr_buf[:MAX_OUTPUT] + f"\n…[truncated]"

    stdout_buf = sanitize(stdout_buf)
    stderr_buf = sanitize(stderr_buf)

    duration = time.monotonic() - start_ts
    success = (error is None) and (exit_code == 0)

    transcript_path = _write_transcript(
        session_id,
        {
            "prompt": prompt,
            "slack_user_id": slack_user_id,
            "workdir": workdir,
            "exit_code": exit_code,
            "duration_s": duration,
            "stdout": stdout_buf,
            "stderr": stderr_buf,
        },
    )

    _write_audit_row(
        {
            "ts": started_at,
            "session_id": session_id,
            "slack_user_id": slack_user_id,
            "host": HOST,
            "user": USER,
            "workdir": workdir,
            "prompt": prompt,
            "exit_code": exit_code,
            "duration_s": round(duration, 3),
            "truncated": truncated,
            "error": error,
            "transcript": transcript_path,
        }
    )

    return BridgeResult(
        session_id=session_id,
        success=success,
        exit_code=exit_code,
        stdout=stdout_buf,
        stderr=stderr_buf,
        duration_s=duration,
        truncated=truncated,
        error=error,
        transcript_path=transcript_path,
    )


__all__ = ["BridgeResult", "run_copilot", "sanitize"]
