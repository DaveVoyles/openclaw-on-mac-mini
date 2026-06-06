"""Phase 3 host-bridge session tests.

Covers:
- Atomic registry add / update / load / crash-recovery
- SessionManager rejects when bridge disabled
- SessionManager per-user concurrent cap
- find_by_thread routing
- end() is idempotent
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Ensure src/ on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _setup_env(tmp_audit: Path) -> None:
    os.environ["AUDIT_DIR"] = str(tmp_audit)
    os.environ["OPENCLAW_HOST_BRIDGE_ENABLED"] = "true"
    os.environ["OPENCLAW_HOST_BRIDGE_KEY"] = str(tmp_audit / "fake_key")
    os.environ["OPENCLAW_HOST_BRIDGE_KNOWN_HOSTS"] = str(tmp_audit / "fake_known_hosts")
    (tmp_audit / "fake_key").write_text("dummy")
    os.environ.pop("OPENCLAW_HOST_BRIDGE_REGISTRY", None)


class RegistryPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_save_load_roundtrip(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _setup_env(Path(td))
            from host_bridge_persistence import Registry, SessionRecord

            reg = Registry(path=Path(td) / "host_bridge" / "sessions.json")
            await reg.load()

            rec = SessionRecord(
                session_id="abc123",
                slack_user="U1",
                slack_channel="C1",
                slack_thread_ts="1.000",
                started_at=time.time(),
                last_activity=time.time(),
                cwd="/tmp",
                status="active",
                turns=2,
            )
            await reg.add(rec)
            self.assertTrue(reg.path.exists())

            data = json.loads(reg.path.read_text())
            self.assertIn("abc123", data)
            self.assertEqual(data["abc123"]["status"], "active")

    async def test_load_marks_active_as_crashed(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _setup_env(Path(td))
            from host_bridge_persistence import Registry, SessionRecord

            path = Path(td) / "host_bridge" / "sessions.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            rec = SessionRecord(
                session_id="zzz",
                slack_user="U1",
                slack_channel="C1",
                slack_thread_ts="1.0",
                started_at=time.time(),
                last_activity=time.time(),
                cwd="/",
                status="active",
            )
            path.write_text(json.dumps({"zzz": rec.to_dict()}))

            reg = Registry(path=path)
            await reg.load()
            self.assertEqual(reg.get("zzz").status, "crashed")

    async def test_find_by_thread(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _setup_env(Path(td))
            from host_bridge_persistence import Registry, SessionRecord

            reg = Registry(path=Path(td) / "sessions.json")
            await reg.load()
            r1 = SessionRecord(
                session_id="s1",
                slack_user="U1",
                slack_channel="C1",
                slack_thread_ts="1.0",
                started_at=0,
                last_activity=0,
                cwd="/",
            )
            await reg.add(r1)
            self.assertEqual(reg.find_by_thread("C1", "1.0").session_id, "s1")
            self.assertIsNone(reg.find_by_thread("C2", "1.0"))


class SessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_session_rejects_when_disabled(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _setup_env(Path(td))
            os.environ["OPENCLAW_HOST_BRIDGE_ENABLED"] = "false"
            # reimport to pick up env
            for mod in ("host_bridge", "host_bridge_persistence"):
                sys.modules.pop(mod, None)
            from host_bridge import SessionManager

            mgr = SessionManager()
            await mgr.registry.load()
            rec, err = await mgr.start_session(
                slack_user="U1",
                slack_channel="C1",
                slack_thread_ts="1.0",
                initial_prompt="hi",
            )
            self.assertIsNone(rec)
            self.assertIn("disabled", err or "")

    async def test_per_user_cap_enforced(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _setup_env(Path(td))
            os.environ["OPENCLAW_HOST_BRIDGE_MAX_SESSIONS_PER_USER"] = "1"
            for mod in ("host_bridge", "host_bridge_persistence"):
                sys.modules.pop(mod, None)
            from host_bridge import SessionManager
            from host_bridge_persistence import SessionRecord

            mgr = SessionManager()
            await mgr.registry.load()
            # Inject a fake live session to occupy the cap
            rec = SessionRecord(
                session_id="live1",
                slack_user="U1",
                slack_channel="C1",
                slack_thread_ts="1.0",
                started_at=time.time(),
                last_activity=time.time(),
                cwd="/",
                status="active",
            )
            await mgr.registry.add(rec)
            mgr._live["live1"] = MagicMock()
            mgr._live["live1"].record = rec

            rec2, err = await mgr.start_session(
                slack_user="U1",
                slack_channel="C2",
                slack_thread_ts="2.0",
                initial_prompt="hi",
            )
            self.assertIsNone(rec2)
            self.assertIn("cap reached", (err or "").lower())

    async def test_end_idempotent_on_unknown_session(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _setup_env(Path(td))
            for mod in ("host_bridge", "host_bridge_persistence"):
                sys.modules.pop(mod, None)
            from host_bridge import SessionManager

            mgr = SessionManager()
            await mgr.registry.load()
            # Should silently no-op
            err = await mgr.end("does-not-exist")
            self.assertIsNone(err)

    async def test_send_turn_rejects_wrong_owner(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            _setup_env(Path(td))
            for mod in ("host_bridge", "host_bridge_persistence"):
                sys.modules.pop(mod, None)
            from host_bridge import SessionManager
            from host_bridge_persistence import SessionRecord

            mgr = SessionManager()
            await mgr.registry.load()
            rec = SessionRecord(
                session_id="s1",
                slack_user="U1",
                slack_channel="C1",
                slack_thread_ts="1.0",
                started_at=time.time(),
                last_activity=time.time(),
                cwd="/",
            )
            live = MagicMock()
            live.record = rec
            live.process = MagicMock()
            live.process.stdin = MagicMock()
            live.process.stdin.is_closing.return_value = False
            mgr._live["s1"] = live
            await mgr.registry.add(rec)

            err = await mgr.send_turn("s1", "hello", slack_user="U2")
            self.assertEqual(err, "not your session")


if __name__ == "__main__":
    unittest.main()
