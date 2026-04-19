"""
Enhanced Logging and Audit Trail System for OpenClaw.

Features:
- Structured logging (JSON format)
- Log rotation (daily, max 30 days)
- Audit trails for user actions
- Security event logging
"""

import json
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from audit import audit_log as legacy_audit_log


# Configure structured JSON logging
class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        if hasattr(record, "trace_id"):
            log_data["trace_id"] = record.trace_id
        if hasattr(record, "user_id"):
            log_data["user_id"] = record.user_id
        if hasattr(record, "correlation_id"):
            log_data["correlation_id"] = record.correlation_id
        if hasattr(record, "command"):
            log_data["command"] = record.command
        if hasattr(record, "metadata"):
            log_data["metadata"] = record.metadata

        return json.dumps(log_data)


def setup_logging(
    log_dir: Path = Path("logs"),
    log_level: int = logging.INFO,
    enable_json: bool = False,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 30,  # Keep 30 days
):
    """Configure logging with rotation and structured output."""
    log_dir.mkdir(exist_ok=True)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler (human-readable)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "openclaw.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    file_handler.setLevel(log_level)

    if enable_json:
        file_formatter = JSONFormatter()
    else:
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Separate error log
    error_handler = logging.handlers.RotatingFileHandler(
        log_dir / "errors.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)
    root_logger.addHandler(error_handler)

    # Audit log (always JSON)
    audit_handler = logging.handlers.RotatingFileHandler(
        log_dir / "audit.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    audit_handler.setLevel(logging.INFO)
    audit_handler.setFormatter(JSONFormatter())

    audit_logger = logging.getLogger("audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.addHandler(audit_handler)
    audit_logger.propagate = False  # Don't send to root logger

    logging.info("Logging configured successfully")


class AuditLogger:
    """Enhanced audit logging with categories."""

    def __init__(self):
        self.logger = logging.getLogger("audit")

    def log_user_action(
        self,
        user_id: str,
        action: str,
        detail: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        result: str = "success",
    ):
        """Log a user action."""
        log_data = {
            "category": "user_action",
            "user_id": user_id,
            "action": action,
            "detail": detail,
            "result": result,
            "metadata": metadata or {},
        }

        self.logger.info(
            f"User action: {action}",
            extra={
                "user_id": user_id,
                "metadata": log_data,
            },
        )

        # Also log to legacy audit system
        legacy_audit_log(
            user=None,  # We don't have the user object here
            action=action,
            detail=detail,
            result=result,
        )

    def log_command_execution(
        self,
        user_id: str,
        command: str,
        parameters: Optional[Dict[str, Any]] = None,
        result: str = "success",
        error: Optional[str] = None,
    ):
        """Log a command execution."""
        log_data = {
            "category": "command_execution",
            "user_id": user_id,
            "command": command,
            "parameters": parameters or {},
            "result": result,
            "error": error,
        }

        level = logging.ERROR if result == "error" else logging.INFO
        self.logger.log(
            level,
            f"Command: {command}",
            extra={
                "user_id": user_id,
                "command": command,
                "metadata": log_data,
            },
        )

    def log_permission_change(
        self,
        admin_user_id: str,
        target_user_id: str,
        action: str,
        detail: str = "",
    ):
        """Log a permission change."""
        log_data = {
            "category": "permission_change",
            "admin_user_id": admin_user_id,
            "target_user_id": target_user_id,
            "action": action,
            "detail": detail,
        }

        self.logger.warning(
            f"Permission change: {action}",
            extra={
                "user_id": admin_user_id,
                "metadata": log_data,
            },
        )

    def log_config_change(
        self,
        user_id: str,
        config_key: str,
        old_value: Any,
        new_value: Any,
    ):
        """Log a configuration change."""
        log_data = {
            "category": "config_change",
            "user_id": user_id,
            "config_key": config_key,
            "old_value": str(old_value),
            "new_value": str(new_value),
        }

        self.logger.warning(
            f"Config changed: {config_key}",
            extra={
                "user_id": user_id,
                "metadata": log_data,
            },
        )

    def log_security_event(
        self,
        event_type: str,
        user_id: Optional[str] = None,
        detail: str = "",
        severity: str = "warning",
    ):
        """Log a security event."""
        log_data = {
            "category": "security_event",
            "event_type": event_type,
            "user_id": user_id or "unknown",
            "detail": detail,
            "severity": severity,
        }

        level_map = {
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }
        level = level_map.get(severity, logging.WARNING)

        self.logger.log(
            level,
            f"Security event: {event_type}",
            extra={
                "user_id": user_id,
                "metadata": log_data,
            },
        )

    def log_failed_auth(self, user_id: str, reason: str):
        """Log a failed authentication attempt."""
        self.log_security_event(
            event_type="failed_auth",
            user_id=user_id,
            detail=reason,
            severity="warning",
        )

    def log_suspicious_activity(self, user_id: str, detail: str):
        """Log suspicious activity."""
        self.log_security_event(
            event_type="suspicious_activity",
            user_id=user_id,
            detail=detail,
            severity="error",
        )

    def get_audit_logs(
        self,
        user_id: Optional[str] = None,
        category: Optional[str] = None,
        days: int = 7,
    ) -> list:
        """Get audit logs (read from file)."""
        logs = []
        audit_file = Path("logs/audit.log")

        if not audit_file.exists():
            return logs

        try:
            with open(audit_file, "r") as f:
                for line in f:
                    try:
                        log_entry = json.loads(line)

                        # Filter by user_id if provided
                        if user_id and log_entry.get("user_id") != user_id:
                            continue

                        # Filter by category if provided
                        if category:
                            metadata = log_entry.get("metadata", {})
                            if metadata.get("category") != category:
                                continue

                        # Filter by days
                        timestamp = datetime.fromisoformat(
                            log_entry["timestamp"].rstrip("Z")
                        )
                        age_days = (datetime.utcnow() - timestamp).days
                        if age_days > days:
                            continue

                        logs.append(log_entry)
                    except json.JSONDecodeError:
                        continue
        except (OSError, ValueError, KeyError, AttributeError) as e:
            logging.error(f"Error reading audit logs: {e}")

        return logs


# Global audit logger
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get or create the global audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger
