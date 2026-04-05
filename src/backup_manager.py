"""
OpenClaw Backup Manager — Phase 3: Automated Backup System
Handles database backups, config files, and uploads to NAS.
Supports incremental/full backups with compression and retention policies.
"""

import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger("openclaw.backup_manager")

BackupType = Literal["full", "incremental"]
CompressionType = Literal["gzip", "none"]


class BackupManager:
    """Manages automated backups of databases, configs, and data."""

    def __init__(
        self,
        backup_dir: Path | str | None = None,
        nas_host: str | None = None,
        nas_path: str | None = None,
    ):
        """
        Initialize backup manager.

        Args:
            backup_dir: Local backup directory (default: data/backups)
            nas_host: NAS hostname or IP (default: from env or 192.168.1.8)
            nas_path: NAS backup path (default: /volume1/backups/openclaw)
        """
        if backup_dir is None:
            backup_dir = Path(os.getenv("BACKUP_DIR", "data/backups"))

        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self.nas_host = nas_host or os.getenv("NAS_HOST", "192.168.1.8")
        self.nas_path = nas_path or os.getenv("NAS_BACKUP_PATH", "/volume1/backups/openclaw")
        self.nas_user = os.getenv("NAS_USER", "dave")

        self.retention_days = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))

    async def create_backup(
        self,
        backup_type: BackupType = "full",
        compression: CompressionType = "gzip",
        upload_to_nas: bool = True,
    ) -> dict[str, Any]:
        """
        Create a backup of all critical data.

        Args:
            backup_type: "full" or "incremental"
            compression: Compression type
            upload_to_nas: Whether to upload to NAS after backup

        Returns:
            dict with {"success": bool, "path": str, "size_bytes": int, "uploaded": bool}
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"openclaw_backup_{backup_type}_{timestamp}"
            backup_path = self.backup_dir / backup_name
            backup_path.mkdir(parents=True, exist_ok=True)

            log.info(f"🔄 Creating {backup_type} backup: {backup_name}")

            # Backup databases
            db_backup_path = await self._backup_databases(backup_path)

            # Backup configuration files
            config_backup_path = await self._backup_configs(backup_path)

            # Backup conversation history
            conv_backup_path = await self._backup_conversations(backup_path)

            # Backup scheduled tasks
            tasks_backup_path = await self._backup_scheduled_tasks(backup_path)

            # Create backup manifest
            manifest = {
                "timestamp": timestamp,
                "type": backup_type,
                "compression": compression,
                "files": {
                    "databases": str(db_backup_path) if db_backup_path else None,
                    "configs": str(config_backup_path) if config_backup_path else None,
                    "conversations": str(conv_backup_path) if conv_backup_path else None,
                    "tasks": str(tasks_backup_path) if tasks_backup_path else None,
                },
                "created_at": datetime.now().isoformat(),
            }

            import json
            manifest_file = backup_path / "manifest.json"
            with open(manifest_file, "w") as f:
                json.dump(manifest, f, indent=2)

            # Compress if requested
            final_path = backup_path
            if compression == "gzip":
                final_path = await self._compress_backup(backup_path)

            # Calculate size
            total_size = sum(f.stat().st_size for f in final_path.rglob("*") if f.is_file())

            # Upload to NAS
            uploaded = False
            if upload_to_nas:
                uploaded = await self._upload_to_nas(final_path)

            log.info(f"✅ Backup created: {final_path} ({total_size:,} bytes)")

            # Cleanup old backups
            await self._cleanup_old_backups()

            return {
                "success": True,
                "path": str(final_path),
                "size_bytes": total_size,
                "uploaded": uploaded,
                "manifest": manifest,
            }

        except Exception as e:
            log.error(f"Backup failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _backup_databases(self, backup_path: Path) -> Path | None:
        """Backup SQLite databases."""
        import sqlite3

        db_dir = backup_path / "databases"
        db_dir.mkdir(exist_ok=True)

        # Find all database files
        db_files = [
            Path(os.getenv("THREAD_DB_PATH", "data/memory/openclaw.db")),
            Path("data/incidents.db"),
        ]

        for db_file in db_files:
            if not db_file.exists():
                continue

            try:
                # Create backup using SQLite backup API
                backup_file = db_dir / db_file.name

                source = sqlite3.connect(str(db_file))
                dest = sqlite3.connect(str(backup_file))

                source.backup(dest)

                source.close()
                dest.close()

                log.info(f"  ✓ Backed up {db_file.name}")
            except Exception as e:
                log.warning(f"  ⚠️ Failed to backup {db_file}: {e}")

        return db_dir if any(db_dir.iterdir()) else None

    async def _backup_configs(self, backup_path: Path) -> Path | None:
        """Backup configuration files."""
        config_dir = backup_path / "config"
        config_dir.mkdir(exist_ok=True)

        config_files = [
            ".env",
            "config/settings.yaml",
            "pyproject.toml",
        ]

        for config_file in config_files:
            source = Path(config_file)
            if not source.exists():
                continue

            try:
                dest = config_dir / source.name
                shutil.copy2(source, dest)
                log.info(f"  ✓ Backed up {source.name}")
            except Exception as e:
                log.warning(f"  ⚠️ Failed to backup {source}: {e}")

        return config_dir if any(config_dir.iterdir()) else None

    async def _backup_conversations(self, backup_path: Path) -> Path | None:
        """Export conversation history."""
        conv_dir = backup_path / "conversations"
        conv_dir.mkdir(exist_ok=True)

        # Export using CSV exporter
        try:
            from exporters import export_to_csv

            output_file = conv_dir / "conversations.csv"
            result = await export_to_csv(
                "conversations",
                output_file,
                days=None,  # All history
            )

            if result["success"]:
                log.info(f"  ✓ Backed up conversations ({result['rows']} rows)")
                return conv_dir
        except Exception as e:
            log.warning(f"  ⚠️ Failed to backup conversations: {e}")

        return None

    async def _backup_scheduled_tasks(self, backup_path: Path) -> Path | None:
        """Backup scheduled task definitions."""
        from scheduler import SCHEDULE_FILE

        if not SCHEDULE_FILE.exists():
            return None

        try:
            tasks_dir = backup_path / "tasks"
            tasks_dir.mkdir(exist_ok=True)

            dest = tasks_dir / "schedules.json"
            shutil.copy2(SCHEDULE_FILE, dest)

            log.info("  ✓ Backed up scheduled tasks")
            return tasks_dir
        except Exception as e:
            log.warning(f"  ⚠️ Failed to backup tasks: {e}")
            return None

    async def _compress_backup(self, backup_path: Path) -> Path:
        """Compress backup directory."""
        archive_path = backup_path.parent / f"{backup_path.name}.tar.gz"

        import tarfile
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(backup_path, arcname=backup_path.name)

        # Remove original directory
        shutil.rmtree(backup_path)

        log.info(f"  ✓ Compressed to {archive_path.name}")
        return archive_path

    async def _upload_to_nas(self, backup_path: Path) -> bool:
        """Upload backup to NAS using rsync."""
        try:
            # Ensure NAS directory exists
            mkdir_cmd = [
                "ssh",
                f"{self.nas_user}@{self.nas_host}",
                f"mkdir -p {self.nas_path}",
            ]
            subprocess.run(mkdir_cmd, check=True, capture_output=True)

            # Upload with rsync
            rsync_cmd = [
                "rsync",
                "-avz",
                "--progress",
                str(backup_path),
                f"{self.nas_user}@{self.nas_host}:{self.nas_path}/",
            ]

            subprocess.run(rsync_cmd, check=True, capture_output=True)
            log.info(f"  ✓ Uploaded to NAS: {self.nas_host}:{self.nas_path}")
            return True

        except subprocess.CalledProcessError as e:
            log.warning(f"  ⚠️ NAS upload failed: {e}")
            return False
        except Exception as e:
            log.warning(f"  ⚠️ NAS upload error: {e}")
            return False

    async def _cleanup_old_backups(self):
        """Remove backups older than retention period."""
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)

        deleted_count = 0
        for backup in self.backup_dir.iterdir():
            if backup.is_file() or backup.is_dir():
                # Parse timestamp from name
                try:
                    # Extract timestamp from name like "openclaw_backup_full_20260405_123456"
                    parts = backup.name.split("_")
                    if len(parts) >= 5:
                        date_str = parts[3]
                        time_str = parts[4].split(".")[0]
                        backup_date = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")

                        if backup_date < cutoff_date:
                            if backup.is_dir():
                                shutil.rmtree(backup)
                            else:
                                backup.unlink()
                            deleted_count += 1
                            log.info(f"  🗑️  Deleted old backup: {backup.name}")
                except Exception:
                    pass  # Skip if we can't parse the date

        if deleted_count > 0:
            log.info(f"  ✓ Cleaned up {deleted_count} old backups")

    async def get_backup_status(self) -> dict[str, Any]:
        """Get information about the last backup."""
        backups = sorted(self.backup_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)

        if not backups:
            return {
                "last_backup": None,
                "total_backups": 0,
                "total_size_bytes": 0,
            }

        last_backup = backups[0]
        total_size = sum(
            f.stat().st_size for backup in backups
            for f in (backup.rglob("*") if backup.is_dir() else [backup])
            if f.is_file()
        )

        return {
            "last_backup": {
                "path": str(last_backup),
                "timestamp": datetime.fromtimestamp(last_backup.stat().st_mtime).isoformat(),
                "size_bytes": last_backup.stat().st_size if last_backup.is_file() else sum(
                    f.stat().st_size for f in last_backup.rglob("*") if f.is_file()
                ),
            },
            "total_backups": len(backups),
            "total_size_bytes": total_size,
        }

    async def restore_backup(self, backup_path: Path | str, restore_to: Path | str | None = None) -> dict[str, Any]:
        """
        Restore from a backup.

        Args:
            backup_path: Path to backup file or directory
            restore_to: Destination directory (default: current project directory)

        Returns:
            dict with {"success": bool, "restored_files": list, "error": str}
        """
        backup_path = Path(backup_path)
        if restore_to is None:
            restore_to = Path.cwd()
        else:
            restore_to = Path(restore_to)

        try:
            log.info(f"🔄 Restoring backup from {backup_path}")

            # If compressed, extract first
            if backup_path.suffix == ".gz":
                import tarfile
                extract_dir = backup_path.parent / backup_path.stem.replace(".tar", "")
                with tarfile.open(backup_path, "r:gz") as tar:
                    tar.extractall(backup_path.parent)
                backup_path = extract_dir

            # Read manifest
            manifest_file = backup_path / "manifest.json"
            if manifest_file.exists():
                import json
                with open(manifest_file) as f:
                    manifest = json.load(f)
            else:
                manifest = {}

            restored_files = []

            # Restore databases
            db_dir = backup_path / "databases"
            if db_dir.exists():
                for db_file in db_dir.iterdir():
                    if db_file.suffix == ".db":
                        dest = Path("data/memory") / db_file.name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(db_file, dest)
                        restored_files.append(str(dest))
                        log.info(f"  ✓ Restored {db_file.name}")

            # Restore configs (with caution)
            config_dir = backup_path / "config"
            if config_dir.exists():
                for config_file in config_dir.iterdir():
                    dest = Path(config_file.name)
                    # Don't overwrite .env without confirmation
                    if dest.name == ".env" and dest.exists():
                        dest = Path(".env.restored")
                    shutil.copy2(config_file, dest)
                    restored_files.append(str(dest))
                    log.info(f"  ✓ Restored {config_file.name}")

            log.info(f"✅ Restored {len(restored_files)} files from backup")
            return {
                "success": True,
                "restored_files": restored_files,
                "manifest": manifest,
            }

        except Exception as e:
            log.error(f"Restore failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


# Convenience functions
async def backup_now(upload_to_nas: bool = True) -> dict[str, Any]:
    """Create a full backup now."""
    manager = BackupManager()
    return await manager.create_backup(backup_type="full", upload_to_nas=upload_to_nas)


async def get_backup_status() -> dict[str, Any]:
    """Get last backup information."""
    manager = BackupManager()
    return await manager.get_backup_status()
