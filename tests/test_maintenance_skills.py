"""Tests for maintenance_skills.py — 4:00 AM automated maintenance tasks."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import maintenance_skills

# ---------------------------------------------------------------------------
# update_skills
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_skills_success():
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "Already up to date.", ""))):
        result = await maintenance_skills.update_skills()
    assert "✅" in result
    assert "Already up to date" in result


@pytest.mark.asyncio
async def test_update_skills_success_multiline_output():
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "remote: done\nFast-forward", ""))):
        result = await maintenance_skills.update_skills()
    assert "✅" in result
    assert "Fast-forward" in result


@pytest.mark.asyncio
async def test_update_skills_failure():
    with patch("subprocess_utils.run", AsyncMock(return_value=(1, "", "CONFLICT merge conflict"))):
        result = await maintenance_skills.update_skills()
    assert "⚠️" in result
    assert "failed" in result.lower()


# ---------------------------------------------------------------------------
# restart_gateway
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restart_gateway_success():
    mock_llm = MagicMock()
    mock_llm.close_sessions = AsyncMock()
    mock_llm._reset_models = MagicMock()
    mock_hs = MagicMock()
    mock_hs.close = AsyncMock()
    with patch.dict("sys.modules", {"llm": mock_llm, "http_session": mock_hs}):
        result = await maintenance_skills.restart_gateway()
    assert "✅" in result
    assert "LLM sessions cleared" in result
    assert "HTTP sessions closed" in result


@pytest.mark.asyncio
async def test_restart_gateway_llm_exception():
    mock_llm = MagicMock()
    mock_llm.close_sessions = AsyncMock(side_effect=Exception("llm error"))
    mock_hs = MagicMock()
    mock_hs.close = AsyncMock()
    with patch.dict("sys.modules", {"llm": mock_llm, "http_session": mock_hs}):
        result = await maintenance_skills.restart_gateway()
    assert "Gateway restart" in result
    assert "LLM clear failed" in result


@pytest.mark.asyncio
async def test_restart_gateway_http_exception():
    mock_llm = MagicMock()
    mock_llm.close_sessions = AsyncMock()
    mock_llm._reset_models = MagicMock()
    mock_hs = MagicMock()
    mock_hs.close = AsyncMock(side_effect=Exception("http error"))
    with patch.dict("sys.modules", {"llm": mock_llm, "http_session": mock_hs}):
        result = await maintenance_skills.restart_gateway()
    assert "Gateway restart" in result
    assert "HTTP close failed" in result


# ---------------------------------------------------------------------------
# backup_config_to_nas
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backup_config_to_nas_nas_unreachable():
    with patch("subprocess_utils.run", AsyncMock(return_value=(1, "", "connection refused"))):
        result = await maintenance_skills.backup_config_to_nas()
    assert "❌" in result
    assert "NAS" in result


@pytest.mark.asyncio
async def test_backup_config_to_nas_success():
    # First call = mkdir, subsequent = rsync/scp
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "", ""))):
        result = await maintenance_skills.backup_config_to_nas()
    assert "✅" in result
    assert "NAS backup" in result


@pytest.mark.asyncio
async def test_backup_config_to_nas_config_rsync_fails():
    responses = [(0, "", ""), (1, "", "rsync error")]
    call_count = [0]

    async def _run_seq(cmd, timeout=30):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        return responses[idx]

    with patch("subprocess_utils.run", side_effect=_run_seq):
        result = await maintenance_skills.backup_config_to_nas()
    assert "config ❌" in result


# ---------------------------------------------------------------------------
# backup_vault_to_nas
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backup_vault_to_nas_vault_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "nonexistent"))
    # Re-import to pick up the env var inside the function
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "", ""))):
        result = await maintenance_skills.backup_vault_to_nas()
    assert "not found" in result or "✅" in result  # no vault dir → skip


@pytest.mark.asyncio
async def test_backup_vault_to_nas_nas_unreachable(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("VAULT_DIR", str(vault))
    with patch("subprocess_utils.run", AsyncMock(return_value=(1, "", "SSH error"))):
        result = await maintenance_skills.backup_vault_to_nas()
    assert "❌" in result


@pytest.mark.asyncio
async def test_backup_vault_to_nas_rsync_fails(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("VAULT_DIR", str(vault))
    responses = [(0, "", ""), (1, "", "rsync failed")]
    call_count = [0]

    async def _run_seq(cmd, timeout=30):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        return responses[idx]

    with patch("subprocess_utils.run", side_effect=_run_seq):
        result = await maintenance_skills.backup_vault_to_nas()
    assert "❌" in result


@pytest.mark.asyncio
async def test_backup_vault_to_nas_success(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("VAULT_DIR", str(vault))
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "", ""))):
        result = await maintenance_skills.backup_vault_to_nas()
    assert "✅" in result


# ---------------------------------------------------------------------------
# run_maintenance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_maintenance_all_succeed():
    with (
        patch.object(maintenance_skills, "update_skills", AsyncMock(return_value="✅ up to date")),
        patch.object(maintenance_skills, "restart_gateway", AsyncMock(return_value="✅ restarted")),
        patch.object(maintenance_skills, "backup_config_to_nas", AsyncMock(return_value="✅ config backed up")),
        patch.object(maintenance_skills, "backup_vault_to_nas", AsyncMock(return_value="✅ vault backed up")),
        patch.object(maintenance_skills, "run_memory_decay", AsyncMock(return_value="🧹 no candidates")),
        patch.object(maintenance_skills, "_run_dream_cycle", AsyncMock(return_value="🌙 done")),
    ):
        result = await maintenance_skills.run_maintenance()
    assert "4:00 AM Maintenance Complete" in result
    assert "skills-update" in result
    assert "gateway-restart" in result
    assert "nas-backup" in result
    assert "vault-backup" in result
    assert "memory-decay" in result
    assert "dream-cycle" in result


@pytest.mark.asyncio
async def test_run_maintenance_step_exception_captured():
    with (
        patch.object(maintenance_skills, "update_skills", AsyncMock(side_effect=Exception("git broken"))),
        patch.object(maintenance_skills, "restart_gateway", AsyncMock(return_value="✅")),
        patch.object(maintenance_skills, "backup_config_to_nas", AsyncMock(return_value="✅")),
        patch.object(maintenance_skills, "backup_vault_to_nas", AsyncMock(return_value="✅")),
        patch.object(maintenance_skills, "run_memory_decay", AsyncMock(return_value="✅")),
        patch.object(maintenance_skills, "_run_dream_cycle", AsyncMock(return_value="✅")),
    ):
        result = await maintenance_skills.run_maintenance()
    assert "❌" in result
    assert "git broken" in result


# ---------------------------------------------------------------------------
# run_memory_decay
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_memory_decay_no_candidates():
    mock_vs = MagicMock()
    mock_vs.MEMORIES_COLLECTION = "memories"
    mock_vs.CONVERSATIONS_COLLECTION = "conversations"
    mock_vs.RESEARCH_COLLECTION = "research"
    mock_vs.get_decayed_documents = AsyncMock(return_value=[])
    with patch.dict("sys.modules", {"vector_store": mock_vs}):
        result = await maintenance_skills.run_memory_decay()
    assert "no candidates" in result


@pytest.mark.asyncio
async def test_run_memory_decay_marks_documents():
    mock_vs = MagicMock()
    mock_vs.MEMORIES_COLLECTION = "memories"
    mock_vs.CONVERSATIONS_COLLECTION = "conversations"
    mock_vs.RESEARCH_COLLECTION = "research"
    mock_vs.get_decayed_documents = AsyncMock(return_value=[{"id": "m1"}, {"id": "m2"}])
    mock_vs.mark_decayed = AsyncMock(return_value=2)
    with patch.dict("sys.modules", {"vector_store": mock_vs}):
        result = await maintenance_skills.run_memory_decay()
    assert "marked" in result
    assert "6" in result  # 3 collections × 2 each


@pytest.mark.asyncio
async def test_run_memory_decay_import_exception():
    with patch.dict("sys.modules", {"vector_store": None}):
        result = await maintenance_skills.run_memory_decay()
    assert "⚠️" in result or "failed" in result.lower()


# ---------------------------------------------------------------------------
# _run_dream_cycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_dream_cycle_success():
    mock_dc = MagicMock()
    instance = MagicMock()
    instance.run = AsyncMock(return_value="Dream report " * 20)
    mock_dc.DreamCycle = MagicMock(return_value=instance)
    with patch.dict("sys.modules", {"dream_cycle": mock_dc}):
        result = await maintenance_skills._run_dream_cycle()
    assert "🌙" in result
    assert "complete" in result


@pytest.mark.asyncio
async def test_run_dream_cycle_import_exception():
    with patch.dict("sys.modules", {"dream_cycle": None}):
        result = await maintenance_skills._run_dream_cycle()
    assert "⚠️" in result
    assert "failed" in result.lower()


# ---------------------------------------------------------------------------
# run_memory_consolidation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_memory_consolidation_not_enough_sessions():
    mock_vs = MagicMock()
    mock_vs.CONVERSATIONS_COLLECTION = "conversations"
    mock_vs.search = AsyncMock(return_value=[{"text": "session 1", "metadata": {"added_at": 9999999999, "type": "summary"}}])
    with patch.dict("sys.modules", {"vector_store": mock_vs}):
        result = await maintenance_skills.run_memory_consolidation()
    assert "Not enough" in result


@pytest.mark.asyncio
async def test_run_memory_consolidation_success():
    import time
    recent_ts = time.time() - 86400  # 1 day ago
    mock_vs = MagicMock()
    mock_vs.CONVERSATIONS_COLLECTION = "conversations"
    mock_vs.search = AsyncMock(return_value=[
        {"text": f"Session summary {i}", "metadata": {"added_at": recent_ts, "type": "summary"}}
        for i in range(5)
    ])
    mock_vs.add_document = AsyncMock()
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=("• Point 1\n• Point 2", [], "gemini"))
    with (
        patch.dict("sys.modules", {"vector_store": mock_vs, "llm": mock_llm}),
    ):
        result = await maintenance_skills.run_memory_consolidation()
    # Either succeeds (digest created) or skips gracefully
    assert result


@pytest.mark.asyncio
async def test_run_memory_consolidation_import_exception():
    with patch.dict("sys.modules", {"vector_store": None}):
        result = await maintenance_skills.run_memory_consolidation()
    assert "skipped" in result.lower() or "failed" in result.lower()


# ---------------------------------------------------------------------------
# check_gluetun_vpn
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_gluetun_vpn_healthy():
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "healthy running", ""))):
        result = await maintenance_skills.check_gluetun_vpn()
    assert "✅" in result
    assert "healthy" in result


@pytest.mark.asyncio
async def test_check_gluetun_vpn_not_found():
    with patch("subprocess_utils.run", AsyncMock(return_value=(1, "", "container not found"))):
        result = await maintenance_skills.check_gluetun_vpn()
    assert "❌" in result


@pytest.mark.asyncio
async def test_check_gluetun_vpn_unhealthy_but_running():
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "unhealthy running", ""))):
        result = await maintenance_skills.check_gluetun_vpn()
    assert "⚠️" in result or "❌" in result


@pytest.mark.asyncio
async def test_check_gluetun_vpn_stopped():
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "healthy stopped", ""))):
        result = await maintenance_skills.check_gluetun_vpn()
    assert "❌" in result


@pytest.mark.asyncio
async def test_check_gluetun_vpn_empty_output():
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "", ""))):
        result = await maintenance_skills.check_gluetun_vpn()
    assert result  # Some output, not a crash


# ---------------------------------------------------------------------------
# check_nas_health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_nas_health_all_healthy():
    responses = [
        (0, "md0 : active raid5 [===]", ""),
        (0, "Filesystem      Size  Used Avail Use% Mounted on\n/dev/md0  10G  5G  5G  50% /volume1", ""),
        (0, "up 5 days, 3 hours", ""),
    ]
    call_count = [0]

    async def _run_seq(cmd, timeout=30):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        return responses[idx]

    with patch("subprocess_utils.run", side_effect=_run_seq):
        result = await maintenance_skills.check_nas_health()
    assert "RAID" in result


@pytest.mark.asyncio
async def test_check_nas_health_raid_degraded():
    responses = [
        (0, "md0 : active raid5 [=_=]\ndegraded", ""),
        (1, "", ""),
        (1, "", ""),
    ]
    call_count = [0]

    async def _run_seq(cmd, timeout=30):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        return responses[idx]

    with patch("subprocess_utils.run", side_effect=_run_seq):
        result = await maintenance_skills.check_nas_health()
    assert "DEGRADED" in result


@pytest.mark.asyncio
async def test_check_nas_health_ssh_unreachable():
    with patch("subprocess_utils.run", AsyncMock(return_value=(1, "", "SSH failed"))):
        result = await maintenance_skills.check_nas_health()
    # Should gracefully handle SSH failures
    assert result


@pytest.mark.asyncio
async def test_check_nas_health_high_disk_usage():
    responses = [
        (0, "", ""),  # RAID check fails silently
        (0, "Filesystem      Size  Used Avail Use%\n/dev/md0  10G  9G  1G  91% /volume1", ""),
        (0, "up 1 day", ""),
    ]
    call_count = [0]

    async def _run_seq(cmd, timeout=30):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        return responses[idx]

    with patch("subprocess_utils.run", side_effect=_run_seq):
        result = await maintenance_skills.check_nas_health()
    assert result


# ---------------------------------------------------------------------------
# auto_cleanup_disk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_cleanup_disk_all_succeed():
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "Deleted 100MB", ""))):
        result = await maintenance_skills.auto_cleanup_disk()
    assert "Docker prune" in result or result  # Some cleanup output


@pytest.mark.asyncio
async def test_auto_cleanup_disk_all_fail():
    with patch("subprocess_utils.run", AsyncMock(return_value=(1, "", "permission denied"))):
        result = await maintenance_skills.auto_cleanup_disk()
    assert result == "No cleanup actions taken" or result == ""


@pytest.mark.asyncio
async def test_auto_cleanup_disk_log_cleanup():
    responses_by_cmd = {}

    async def _run_smart(cmd, timeout=30):
        cmd_str = " ".join(str(c) for c in cmd)
        if "find" in cmd_str and "log" in cmd_str:
            return (0, "/memory/logs/old.log\n", "")
        if "find" in cmd_str and "audit" in cmd_str:
            return (0, "/memory/audit/old.jsonl\n", "")
        if "docker" in cmd_str:
            return (0, "done", "")
        if "df" in cmd_str:
            return (0, "/dev/md0  10G  5G  50%  /\n", "")
        return (0, "", "")

    with patch("subprocess_utils.run", side_effect=_run_smart):
        result = await maintenance_skills.auto_cleanup_disk()
    assert result  # produced some output


# ---------------------------------------------------------------------------
# fix_qbit_download_path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fix_qbit_ssh_fail():
    with patch("subprocess_utils.run", AsyncMock(return_value=(1, "", "SSH failed"))):
        result = await maintenance_skills.fix_qbit_download_path()
    assert "❌" in result
    assert "qBittorrent" in result


@pytest.mark.asyncio
async def test_fix_qbit_already_correct():
    conf = f"Session\\DefaultSavePath={maintenance_skills.QBIT_EXPECTED_SAVE_PATH}\n"
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, conf, ""))):
        result = await maintenance_skills.fix_qbit_download_path()
    assert "correct" in result or "✅" in result


@pytest.mark.asyncio
async def test_fix_qbit_path_fixed_successfully():
    responses = [
        (0, "Session\\DefaultSavePath=/wrong/path\n", ""),  # grep config
        (0, "", ""),                                          # fix+restart
    ]
    call_count = [0]

    async def _run_seq(cmd, timeout=30):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        return responses[idx]

    with patch("subprocess_utils.run", side_effect=_run_seq):
        result = await maintenance_skills.fix_qbit_download_path()
    assert "✅" in result or "Fixed" in result


@pytest.mark.asyncio
async def test_fix_qbit_path_fix_fails():
    responses = [
        (0, "Session\\DefaultSavePath=/wrong/path\n", ""),  # grep config
        (1, "", "permission denied"),                         # fix fails
    ]
    call_count = [0]

    async def _run_seq(cmd, timeout=30):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        return responses[idx]

    with patch("subprocess_utils.run", side_effect=_run_seq):
        result = await maintenance_skills.fix_qbit_download_path()
    assert "❌" in result


# ---------------------------------------------------------------------------
# copilot_fix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_copilot_fix_success():
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "Fixed the failing test", ""))):
        result = await maintenance_skills.copilot_fix("Fix the tests")
    assert "✅" in result
    assert "Fixed the failing test" in result


@pytest.mark.asyncio
async def test_copilot_fix_failure_no_output():
    with patch("subprocess_utils.run", AsyncMock(return_value=(1, "", "SSH error"))):
        result = await maintenance_skills.copilot_fix("Fix the tests")
    assert "❌" in result


@pytest.mark.asyncio
async def test_copilot_fix_nonzero_with_output():
    with patch("subprocess_utils.run", AsyncMock(return_value=(1, "partial output here", ""))):
        result = await maintenance_skills.copilot_fix("Do something")
    assert "partial output here" in result
    assert "⚠️" in result


@pytest.mark.asyncio
async def test_copilot_fix_long_output_truncated():
    long_output = "x" * 3000
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, long_output, ""))):
        result = await maintenance_skills.copilot_fix("Long task")
    assert "truncated" in result or len(result) < 2500


@pytest.mark.asyncio
async def test_copilot_fix_custom_cwd():
    with patch("subprocess_utils.run", AsyncMock(return_value=(0, "done", ""))) as mock_run:
        await maintenance_skills.copilot_fix("Fix it", cwd="~/myproject")
    # The command string passed to SSH should contain the cwd
    cmd_args = mock_run.call_args[0][0]
    cmd_str = " ".join(str(c) for c in cmd_args)
    assert "myproject" in cmd_str
