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


# ===========================================================================
# Phase 3 — Threaded interactive sessions
# ===========================================================================
#
# A session is a long-lived ``copilot`` process spawned on the host over an
# SSH-allocated PTY. Slack thread replies become subsequent user turns; output
# is streamed back, batched, and posted to the originating thread.
#
# Lifecycle:
#   start_session ─► spawn process, register row, return SessionHandle
#   send_turn     ─► write a line to stdin
#   cancel        ─► SIGINT to remote process group
#   end           ─► close stdin, await exit, remove from registry
#
# An idle sweeper marks sessions ``idle`` after OPENCLAW_HOST_BRIDGE_IDLE_TIMEOUT_S
# of no activity and tears them down.

import asyncio
import signal
from collections.abc import Awaitable, Callable

from host_bridge_persistence import Registry, SessionRecord

# Phase 3 configuration ------------------------------------------------------

MAX_SESSIONS_PER_USER = int(_env("OPENCLAW_HOST_BRIDGE_MAX_SESSIONS_PER_USER", "3") or "3")
IDLE_TIMEOUT_S = int(_env("OPENCLAW_HOST_BRIDGE_IDLE_TIMEOUT_S", "600") or "600")
OUTPUT_FLUSH_INTERVAL_S = float(_env("OPENCLAW_HOST_BRIDGE_FLUSH_INTERVAL_S", "1.5") or "1.5")
OUTPUT_CHUNK_BYTES = int(_env("OPENCLAW_HOST_BRIDGE_CHUNK_BYTES", "3500") or "3500")
SESSION_TURN_TIMEOUT_S = int(_env("OPENCLAW_HOST_BRIDGE_TURN_TIMEOUT_S", "1800") or "1800")  # 30 min hard cap on a single session


# Strip ANSI escape codes from streamed output before posting to Slack.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[=>]|\r")


def strip_ansi(text: str) -> str:
    if not text:
        return text
    return _ANSI_RE.sub("", text)


@dataclass
class _LiveSession:
    """In-process handle for an active SSH process. NOT persisted."""

    record: SessionRecord
    conn: Any                          # asyncssh.SSHClientConnection
    process: Any                       # asyncssh.SSHClientProcess
    output_buffer: list[str] = field(default_factory=list)
    last_flush: float = 0.0
    busy: bool = False                 # True between send_turn and idle-flush
    queue: list[str] = field(default_factory=list)
    reader_task: asyncio.Task[Any] | None = None
    flusher_task: asyncio.Task[Any] | None = None


# Type for the per-session output callback the Slack layer registers
ChunkPoster = Callable[[SessionRecord, str], Awaitable[None]]


class SessionManager:
    """Async-safe registry of long-lived host Copilot processes."""

    def __init__(self, registry: Registry | None = None) -> None:
        self.registry = registry or Registry()
        self._live: dict[str, _LiveSession] = {}
        self._lock = asyncio.Lock()
        self._sweeper_task: asyncio.Task[Any] | None = None
        self._chunk_poster: ChunkPoster | None = None
        self._started = False

    # ------------------------------------------------------------------
    # Boot / shutdown
    # ------------------------------------------------------------------

    async def start(self, chunk_poster: ChunkPoster) -> None:
        if self._started:
            return
        await self.registry.load()
        self._chunk_poster = chunk_poster
        loop = asyncio.get_event_loop()
        self._sweeper_task = loop.create_task(self._idle_sweeper(), name="host_bridge_sweeper")
        self._started = True
        log.info("SessionManager started; %d historical row(s) loaded", len(self.registry.all()))

    async def shutdown(self) -> None:
        if self._sweeper_task and not self._sweeper_task.done():
            self._sweeper_task.cancel()
        async with self._lock:
            sids = list(self._live.keys())
        for sid in sids:
            try:
                await self.end(sid, reason="shutdown")
            except Exception as exc:  # noqa: BLE001
                log.warning("SessionManager shutdown: end(%s) failed: %s", sid, exc)
        await self.registry.save()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_session(
        self,
        *,
        slack_user: str,
        slack_channel: str,
        slack_thread_ts: str,
        initial_prompt: str,
        cwd: str | None = None,
    ) -> tuple[SessionRecord | None, str | None]:
        """Spawn a new interactive copilot process. Returns (record, error)."""
        if not _enabled():
            return None, "host bridge disabled (OPENCLAW_HOST_BRIDGE_ENABLED!=true)"

        active = [
            r for r in self.registry.list_for_user(slack_user)
            if r.status in ("active", "idle") and r.session_id in self._live
        ]
        if len(active) >= MAX_SESSIONS_PER_USER:
            return None, f"per-user session cap reached ({MAX_SESSIONS_PER_USER}). End an existing session first."

        try:
            import asyncssh
        except ImportError as exc:
            return None, f"asyncssh not installed: {exc}"

        key_file = Path(KEY_PATH)
        if not key_file.exists():
            return None, f"SSH key not found at {KEY_PATH}"

        session_id = uuid.uuid4().hex[:12]
        workdir = cwd or WORKDIR

        try:
            known_hosts = KNOWN_HOSTS if Path(KNOWN_HOSTS).exists() else None
            conn = await asyncssh.connect(  # type: ignore[attr-defined]
                HOST,
                username=USER,
                client_keys=[str(key_file)],
                known_hosts=known_hosts,
            )
        except Exception as exc:  # noqa: BLE001
            return None, f"SSH connect failed: {type(exc).__name__}: {exc}"

        # Build the remote invocation. We use bash -lc to pick up PATH/auth and
        # cd before launching copilot in interactive mode (no -p). Output goes
        # to a PTY so the CLI renders as it would for the user.
        q_workdir = shlex.quote(workdir)
        q_bin = shlex.quote(COPILOT_BIN)
        inner = f"cd {q_workdir} && {q_bin} --allow-all-tools"
        remote_cmd = f"bash -lc {shlex.quote(inner)}"

        try:
            process = await conn.create_process(
                remote_cmd,
                term_type="xterm-256color",
                term_size=(120, 32),
            )
        except Exception as exc:  # noqa: BLE001
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return None, f"spawn failed: {type(exc).__name__}: {exc}"

        now = time.time()
        transcript = _AUDIT_DIR / f"{session_id}.log"
        _ensure_audit_dirs()
        try:
            with transcript.open("w", encoding="utf-8") as fh:
                fh.write(f"# session {session_id} (interactive)\n")
                fh.write(f"# user:   {slack_user}\n")
                fh.write(f"# host:   {USER}@{HOST}\n")
                fh.write(f"# cwd:    {workdir}\n")
                fh.write(f"# opened: {now}\n")
                fh.write(f"# initial prompt: {initial_prompt!r}\n")
                fh.write("# =====\n")
        except OSError as exc:
            log.warning("host_bridge[%s]: transcript open failed: %s", session_id, exc)

        record = SessionRecord(
            session_id=session_id,
            slack_user=slack_user,
            slack_channel=slack_channel,
            slack_thread_ts=slack_thread_ts,
            started_at=now,
            last_activity=now,
            cwd=workdir,
            host_pid=None,
            status="active",
            transcript_path=str(transcript),
            turns=0,
        )
        await self.registry.add(record)

        live = _LiveSession(record=record, conn=conn, process=process, last_flush=time.monotonic())
        async with self._lock:
            self._live[session_id] = live

        # Spawn the pumps. The reader streams stdout; the flusher batches.
        loop = asyncio.get_event_loop()
        live.reader_task = loop.create_task(self._reader_loop(live), name=f"host_bridge_reader_{session_id}")
        live.flusher_task = loop.create_task(self._flusher_loop(live), name=f"host_bridge_flusher_{session_id}")

        # Send the initial prompt as the first user turn.
        await self.send_turn(session_id, initial_prompt, slack_user=slack_user)

        _write_audit_row(
            {
                "ts": now,
                "event": "session_start",
                "session_id": session_id,
                "slack_user_id": slack_user,
                "channel": slack_channel,
                "thread_ts": slack_thread_ts,
                "workdir": workdir,
                "initial_prompt": initial_prompt,
            }
        )
        return record, None

    async def send_turn(self, session_id: str, prompt: str, *, slack_user: str) -> str | None:
        """Append a user turn. Returns error string or None on success."""
        live = self._live.get(session_id)
        if live is None:
            return "session not found (may have ended or crashed)"
        if live.record.slack_user != slack_user:
            return "not your session"
        if live.process.stdin is None or live.process.stdin.is_closing():
            return "session stdin closed"

        line = (prompt.rstrip("\n") + "\n")
        try:
            live.process.stdin.write(line)
        except Exception as exc:  # noqa: BLE001
            return f"write failed: {type(exc).__name__}: {exc}"

        now = time.time()
        live.busy = True
        await self.registry.update(session_id, last_activity=now, turns=live.record.turns + 1)
        live.record.turns += 1
        live.record.last_activity = now
        _append_transcript(live.record, f"\n>>> USER TURN {live.record.turns} ({slack_user})\n{prompt}\n")
        _write_audit_row(
            {
                "ts": now,
                "event": "session_turn",
                "session_id": session_id,
                "slack_user_id": slack_user,
                "turn": live.record.turns,
                "prompt": prompt,
            }
        )
        return None

    async def cancel(self, session_id: str, *, slack_user: str) -> str | None:
        live = self._live.get(session_id)
        if live is None:
            return "session not found"
        if live.record.slack_user != slack_user:
            return "not your session"
        try:
            live.process.send_signal(signal.SIGINT)
        except Exception as exc:  # noqa: BLE001
            return f"cancel failed: {type(exc).__name__}: {exc}"
        _write_audit_row(
            {"ts": time.time(), "event": "session_cancel", "session_id": session_id, "slack_user_id": slack_user}
        )
        return None

    async def end(self, session_id: str, *, slack_user: str | None = None, reason: str = "ended") -> str | None:
        live = self._live.get(session_id)
        if live is None:
            await self.registry.update(session_id, status="ended")
            return None
        if slack_user is not None and live.record.slack_user != slack_user:
            return "not your session"

        # Best-effort: send /exit then close stdin then close conn.
        try:
            if live.process.stdin and not live.process.stdin.is_closing():
                try:
                    live.process.stdin.write("/exit\n")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    live.process.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

        try:
            await asyncio.wait_for(live.process.wait_closed(), timeout=10)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            try:
                live.process.terminate()
            except Exception:  # noqa: BLE001
                pass

        for task in (live.reader_task, live.flusher_task):
            if task and not task.done():
                task.cancel()

        try:
            live.conn.close()
        except Exception:  # noqa: BLE001
            pass

        await self._flush_now(live, final=True)
        async with self._lock:
            self._live.pop(session_id, None)
        await self.registry.update(session_id, status=reason)
        _write_audit_row(
            {"ts": time.time(), "event": "session_end", "session_id": session_id, "reason": reason}
        )
        return None

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def list_sessions(self, slack_user: str | None = None) -> list[SessionRecord]:
        if slack_user is None:
            return self.registry.all()
        return self.registry.list_for_user(slack_user)

    def find_by_thread(self, channel: str, thread_ts: str) -> SessionRecord | None:
        return self.registry.find_by_thread(channel, thread_ts)

    def get_record(self, session_id: str) -> SessionRecord | None:
        return self.registry.get(session_id)

    def is_live(self, session_id: str) -> bool:
        return session_id in self._live

    # ------------------------------------------------------------------
    # Internal pumps
    # ------------------------------------------------------------------

    async def _reader_loop(self, live: _LiveSession) -> None:
        sid = live.record.session_id
        try:
            while True:
                try:
                    data = await live.process.stdout.read(4096)
                except Exception as exc:  # noqa: BLE001
                    log.info("host_bridge[%s]: reader exception: %s", sid, exc)
                    break
                if not data:
                    break
                if isinstance(data, bytes):
                    chunk = data.decode("utf-8", "replace")
                else:
                    chunk = data
                chunk = strip_ansi(chunk)
                if not chunk:
                    continue
                live.output_buffer.append(chunk)
                live.record.last_activity = time.time()
                _append_transcript(live.record, chunk)
                # Flush eagerly when the buffer crosses the chunk size.
                if sum(len(c) for c in live.output_buffer) >= OUTPUT_CHUNK_BYTES:
                    await self._flush_now(live)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("host_bridge[%s]: reader loop crashed: %s", sid, exc)
        finally:
            # Process exited or stream closed — drain and mark ended.
            await self._flush_now(live, final=True)
            if sid in self._live:
                async with self._lock:
                    self._live.pop(sid, None)
                await self.registry.update(sid, status="ended")
                if self._chunk_poster is not None:
                    try:
                        await self._chunk_poster(live.record, "_(session ended)_")
                    except Exception:  # noqa: BLE001
                        pass

    async def _flusher_loop(self, live: _LiveSession) -> None:
        sid = live.record.session_id
        try:
            while True:
                await asyncio.sleep(OUTPUT_FLUSH_INTERVAL_S)
                if live.output_buffer:
                    await self._flush_now(live)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("host_bridge[%s]: flusher loop crashed: %s", sid, exc)

    async def _flush_now(self, live: _LiveSession, *, final: bool = False) -> None:
        if not live.output_buffer:
            if final and live.queue:
                # Drain queued user turns even if nothing pending to flush
                pass
            else:
                return
        text = "".join(live.output_buffer)
        live.output_buffer.clear()
        live.last_flush = time.monotonic()
        live.busy = False
        text = sanitize(text)
        # Slice into chunks that fit in a single Slack message
        if self._chunk_poster is None:
            return
        for i in range(0, len(text), OUTPUT_CHUNK_BYTES):
            piece = text[i:i + OUTPUT_CHUNK_BYTES]
            if not piece.strip():
                continue
            try:
                await self._chunk_poster(live.record, piece)
            except Exception as exc:  # noqa: BLE001
                log.warning("host_bridge[%s]: chunk post failed: %s", live.record.session_id, exc)

        # After we drain output, deliver any queued follow-up turns
        if not live.busy and live.queue:
            next_prompt = live.queue.pop(0)
            await self.send_turn(live.record.session_id, next_prompt, slack_user=live.record.slack_user)

    async def _idle_sweeper(self) -> None:
        try:
            while True:
                await asyncio.sleep(60)
                now = time.time()
                to_end: list[str] = []
                for sid, live in list(self._live.items()):
                    if (now - live.record.last_activity) > IDLE_TIMEOUT_S:
                        to_end.append(sid)
                for sid in to_end:
                    log.info("host_bridge[%s]: idle timeout; ending", sid)
                    try:
                        await self.end(sid, reason="idle_timeout")
                    except Exception as exc:  # noqa: BLE001
                        log.warning("idle end(%s) failed: %s", sid, exc)
        except asyncio.CancelledError:
            return


def _append_transcript(record: SessionRecord, chunk: str) -> None:
    if not record.transcript_path:
        return
    try:
        with open(record.transcript_path, "a", encoding="utf-8") as fh:
            fh.write(chunk)
    except OSError as exc:
        log.warning("transcript append failed for %s: %s", record.session_id, exc)


# Singleton manager — lazily created the first time the Slack layer needs it.
_SESSION_MANAGER: SessionManager | None = None


def get_session_manager() -> SessionManager:
    global _SESSION_MANAGER
    if _SESSION_MANAGER is None:
        _SESSION_MANAGER = SessionManager()
    return _SESSION_MANAGER


__all__ = [
    "BridgeResult",
    "ChunkPoster",
    "SessionManager",
    "SessionRecord",
    "get_session_manager",
    "run_copilot",
    "sanitize",
    "strip_ansi",
]
