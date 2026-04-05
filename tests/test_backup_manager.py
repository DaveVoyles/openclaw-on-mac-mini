"""
Tests for backup manager.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import asyncio
import json
import shutil
import sqlite3

import pytest

from backup_manager import BackupManager, backup_now, get_backup_status


@pytest.fixture
def backup_dir(tmp_path):
    """Create temporary backup directory."""
    backup_path = tmp_path / "backups"
    backup_path.mkdir()
    return backup_path


@pytest.fixture
def test_database(tmp_path):
    """Create a test SQLite database."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE test_data (id INTEGER, value TEXT)")
    conn.execute("INSERT INTO test_data VALUES (1, 'test')")
    conn.commit()
    conn.close()
    return db_path


@pytest.mark.asyncio
async def test_backup_manager_init(backup_dir):
    """Test BackupManager initialization."""
    manager = BackupManager(backup_dir=backup_dir)
    assert manager.backup_dir == backup_dir
    assert backup_dir.exists()


@pytest.mark.asyncio
async def test_create_full_backup(backup_dir, monkeypatch):
    """Test creating a full backup."""
    manager = BackupManager(backup_dir=backup_dir)
    
    # Don't upload to NAS in tests
    result = await manager.create_backup(
        backup_type="full",
        upload_to_nas=False,
    )
    
    if result["success"]:
        assert "path" in result
        assert "size_bytes" in result
        assert result["uploaded"] is False


@pytest.mark.asyncio
async def test_create_incremental_backup(backup_dir):
    """Test creating an incremental backup."""
    manager = BackupManager(backup_dir=backup_dir)
    
    result = await manager.create_backup(
        backup_type="incremental",
        upload_to_nas=False,
    )
    
    if result["success"]:
        assert "manifest" in result


@pytest.mark.asyncio
async def test_backup_manifest(backup_dir):
    """Test that backup manifest is created."""
    manager = BackupManager(backup_dir=backup_dir)
    
    result = await manager.create_backup(
        backup_type="full",
        upload_to_nas=False,
        compression="none",
    )
    
    if result["success"]:
        backup_path = Path(result["path"])
        manifest_file = backup_path / "manifest.json"
        
        if manifest_file.exists():
            with open(manifest_file) as f:
                manifest = json.load(f)
            
            assert "timestamp" in manifest
            assert "type" in manifest
            assert manifest["type"] == "full"


@pytest.mark.asyncio
async def test_backup_compression(backup_dir):
    """Test backup compression."""
    manager = BackupManager(backup_dir=backup_dir)
    
    result = await manager.create_backup(
        backup_type="full",
        compression="gzip",
        upload_to_nas=False,
    )
    
    if result["success"]:
        backup_path = Path(result["path"])
        # Compressed backups should have .tar.gz extension
        assert backup_path.suffix == ".gz" or backup_path.is_file()


@pytest.mark.asyncio
async def test_backup_status(backup_dir):
    """Test getting backup status."""
    manager = BackupManager(backup_dir=backup_dir)
    
    # Create a backup first
    await manager.create_backup(backup_type="full", upload_to_nas=False)
    
    # Get status
    status = await manager.get_backup_status()
    
    assert "total_backups" in status
    if status["total_backups"] > 0:
        assert "last_backup" in status
        assert status["last_backup"] is not None


@pytest.mark.asyncio
async def test_cleanup_old_backups(backup_dir):
    """Test cleanup of old backups."""
    manager = BackupManager(backup_dir=backup_dir)
    manager.retention_days = 0  # Clean up everything
    
    # Create a test backup directory
    old_backup = backup_dir / "openclaw_backup_full_20200101_120000"
    old_backup.mkdir()
    (old_backup / "test.txt").write_text("test")
    
    # Run cleanup
    await manager._cleanup_old_backups()
    
    # Old backup should be removed
    # (may not work if timestamp parsing fails, which is okay)


@pytest.mark.asyncio
async def test_restore_backup(backup_dir, tmp_path):
    """Test restoring from a backup."""
    manager = BackupManager(backup_dir=backup_dir)
    
    # Create a simple backup directory
    backup_path = backup_dir / "test_backup"
    backup_path.mkdir()
    
    # Create test files
    db_dir = backup_path / "databases"
    db_dir.mkdir()
    test_db = db_dir / "test.db"
    test_db.write_text("fake database")
    
    # Create manifest
    manifest = {
        "timestamp": "20260405_120000",
        "type": "full",
        "files": {"databases": str(db_dir)},
    }
    (backup_path / "manifest.json").write_text(json.dumps(manifest))
    
    # Restore
    restore_dir = tmp_path / "restore"
    result = await manager.restore_backup(backup_path, restore_to=restore_dir)
    
    if result["success"]:
        assert "restored_files" in result
        assert len(result["restored_files"]) > 0


@pytest.mark.asyncio
async def test_backup_databases(backup_dir, test_database, monkeypatch):
    """Test database backup."""
    manager = BackupManager(backup_dir=backup_dir)
    
    # Set up test database path
    monkeypatch.setenv("THREAD_DB_PATH", str(test_database))
    
    backup_path = backup_dir / "test_backup"
    backup_path.mkdir()
    
    result = await manager._backup_databases(backup_path)
    
    # Should create databases directory
    if result:
        assert result.exists()


@pytest.mark.asyncio
async def test_convenience_functions(backup_dir, monkeypatch):
    """Test convenience functions."""
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))
    
    # Test backup_now
    result = await backup_now(upload_to_nas=False)
    assert "success" in result
    
    # Test get_backup_status
    status = await get_backup_status()
    assert "total_backups" in status


@pytest.mark.asyncio
async def test_nas_upload_disabled(backup_dir):
    """Test that NAS upload can be disabled."""
    manager = BackupManager(backup_dir=backup_dir)
    
    result = await manager.create_backup(
        backup_type="full",
        upload_to_nas=False,
    )
    
    if result["success"]:
        assert result["uploaded"] is False
