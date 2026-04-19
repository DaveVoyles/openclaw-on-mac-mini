"""
OpenClaw RBAC Permissions System — Phase 4

Role-Based Access Control with fine-grained permissions, decorators, and audit logging.
Extends the existing permissions.py with multi-user support.
"""

import functools
import logging
import sqlite3
import time
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import discord

from user_manager import User, UserRole, get_user_manager
from workspace_manager import WorkspaceRole, get_workspace_manager

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = Path("/memory/permissions.db")


class Permission(Enum):
    """Fine-grained permissions"""

    # Command execution
    EXECUTE_COMMANDS = "execute_commands"
    EXECUTE_ADMIN_COMMANDS = "execute_admin_commands"

    # Scheduling
    MANAGE_SCHEDULES = "manage_schedules"
    VIEW_SCHEDULES = "view_schedules"

    # Analytics & Monitoring
    VIEW_ANALYTICS = "view_analytics"
    EXPORT_DATA = "export_data"

    # User & Workspace Management
    INVITE_MEMBERS = "invite_members"
    REMOVE_MEMBERS = "remove_members"
    MANAGE_ROLES = "manage_roles"

    # Settings & Configuration
    MODIFY_SETTINGS = "modify_settings"
    MANAGE_INTEGRATIONS = "manage_integrations"

    # Resources
    CREATE_RESOURCES = "create_resources"
    DELETE_RESOURCES = "delete_resources"
    SHARE_RESOURCES = "share_resources"

    # Data Access
    ACCESS_SENSITIVE_DATA = "access_sensitive_data"
    DELETE_DATA = "delete_data"


# Default role permissions
DEFAULT_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.ADMIN: {  # Full access
        Permission.EXECUTE_COMMANDS,
        Permission.EXECUTE_ADMIN_COMMANDS,
        Permission.MANAGE_SCHEDULES,
        Permission.VIEW_SCHEDULES,
        Permission.VIEW_ANALYTICS,
        Permission.EXPORT_DATA,
        Permission.INVITE_MEMBERS,
        Permission.REMOVE_MEMBERS,
        Permission.MANAGE_ROLES,
        Permission.MODIFY_SETTINGS,
        Permission.MANAGE_INTEGRATIONS,
        Permission.CREATE_RESOURCES,
        Permission.DELETE_RESOURCES,
        Permission.SHARE_RESOURCES,
        Permission.ACCESS_SENSITIVE_DATA,
        Permission.DELETE_DATA,
    },
    UserRole.MEMBER: {  # Standard user access
        Permission.EXECUTE_COMMANDS,
        Permission.MANAGE_SCHEDULES,
        Permission.VIEW_SCHEDULES,
        Permission.VIEW_ANALYTICS,
        Permission.CREATE_RESOURCES,
        Permission.SHARE_RESOURCES,
    },
    UserRole.VIEWER: {  # Read-only access
        Permission.VIEW_SCHEDULES,
        Permission.VIEW_ANALYTICS,
    },
}


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------


def _init_db(conn: sqlite3.Connection) -> None:
    """Initialize permissions database"""
    # User-specific permission overrides
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_permissions (
            user_id INTEGER NOT NULL,
            permission TEXT NOT NULL,
            granted INTEGER NOT NULL,
            granted_by INTEGER,
            granted_at REAL NOT NULL,
            PRIMARY KEY (user_id, permission)
        )
    """)

    # Workspace-specific permission overrides
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workspace_permissions (
            workspace_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            permission TEXT NOT NULL,
            granted INTEGER NOT NULL,
            granted_by INTEGER,
            granted_at REAL NOT NULL,
            PRIMARY KEY (workspace_id, user_id, permission)
        )
    """)

    # Permission audit log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS permission_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            permission TEXT,
            target_user_id INTEGER,
            details TEXT,
            timestamp REAL NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_user_timestamp
        ON permission_audit(user_id, timestamp)
    """)

    conn.commit()


# ---------------------------------------------------------------------------
# Permission Manager
# ---------------------------------------------------------------------------


class PermissionManager:
    """Manages fine-grained permissions for users and workspaces"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        _init_db(self.conn)
        log.info("PermissionManager initialized with database: %s", db_path)

    # -----------------------------------------------------------------------
    # Permission checks
    # -----------------------------------------------------------------------

    def has_permission(
        self,
        user: User,
        permission: Permission,
        workspace_id: Optional[int] = None,
    ) -> bool:
        """Check if user has a specific permission

        Args:
            user: User object
            permission: Permission to check
            workspace_id: Optional workspace context

        Returns:
            True if user has permission, False otherwise
        """
        # Check workspace-specific permissions first
        if workspace_id is not None:
            override = self._get_workspace_permission(workspace_id, user.id, permission)
            if override is not None:
                return override

            # Check workspace role
            workspace_manager = get_workspace_manager()
            ws_role = workspace_manager.get_member_role(workspace_id, user.id)
            if ws_role and ws_role == WorkspaceRole.OWNER:
                # Workspace owners have all permissions in their workspace
                return True

        # Check user-specific permission overrides
        override = self._get_user_permission(user.id, permission)
        if override is not None:
            return override

        # Fall back to default role permissions
        return permission in DEFAULT_PERMISSIONS.get(user.role, set())

    def _get_user_permission(self, user_id: int, permission: Permission) -> Optional[bool]:
        """Get user-specific permission override"""
        row = self.conn.execute(
            "SELECT granted FROM user_permissions WHERE user_id = ? AND permission = ?",
            (user_id, permission.value),
        ).fetchone()
        return bool(row["granted"]) if row else None

    def _get_workspace_permission(
        self,
        workspace_id: int,
        user_id: int,
        permission: Permission,
    ) -> Optional[bool]:
        """Get workspace-specific permission override"""
        row = self.conn.execute(
            """
            SELECT granted FROM workspace_permissions
            WHERE workspace_id = ? AND user_id = ? AND permission = ?
            """,
            (workspace_id, user_id, permission.value),
        ).fetchone()
        return bool(row["granted"]) if row else None

    # -----------------------------------------------------------------------
    # Permission management
    # -----------------------------------------------------------------------

    def grant_permission(
        self,
        user_id: int,
        permission: Permission,
        granted_by: int,
        workspace_id: Optional[int] = None,
    ) -> None:
        """Grant a permission to user

        Args:
            user_id: User to grant permission to
            permission: Permission to grant
            granted_by: User ID who granted the permission
            workspace_id: Optional workspace context
        """
        now = time.time()

        if workspace_id is not None:
            # Workspace-specific permission
            self.conn.execute(
                """
                INSERT OR REPLACE INTO workspace_permissions
                (workspace_id, user_id, permission, granted, granted_by, granted_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (workspace_id, user_id, permission.value, granted_by, now),
            )
        else:
            # User-level permission
            self.conn.execute(
                """
                INSERT OR REPLACE INTO user_permissions
                (user_id, permission, granted, granted_by, granted_at)
                VALUES (?, ?, 1, ?, ?)
                """,
                (user_id, permission.value, granted_by, now),
            )

        self.conn.commit()

        self._log_audit(
            granted_by,
            "grant_permission",
            permission.value,
            user_id,
            f"Granted {permission.value} to user {user_id}" + (f" in workspace {workspace_id}" if workspace_id else ""),
        )

        log.info(
            "Granted permission %s to user %d by user %d%s",
            permission.value,
            user_id,
            granted_by,
            f" in workspace {workspace_id}" if workspace_id else "",
        )

    def revoke_permission(
        self,
        user_id: int,
        permission: Permission,
        revoked_by: int,
        workspace_id: Optional[int] = None,
    ) -> None:
        """Revoke a permission from user

        Args:
            user_id: User to revoke permission from
            permission: Permission to revoke
            revoked_by: User ID who revoked the permission
            workspace_id: Optional workspace context
        """
        now = time.time()

        if workspace_id is not None:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO workspace_permissions
                (workspace_id, user_id, permission, granted, granted_by, granted_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (workspace_id, user_id, permission.value, revoked_by, now),
            )
        else:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO user_permissions
                (user_id, permission, granted, granted_by, granted_at)
                VALUES (?, ?, 0, ?, ?)
                """,
                (user_id, permission.value, revoked_by, now),
            )

        self.conn.commit()

        self._log_audit(
            revoked_by,
            "revoke_permission",
            permission.value,
            user_id,
            f"Revoked {permission.value} from user {user_id}"
            + (f" in workspace {workspace_id}" if workspace_id else ""),
        )

        log.info(
            "Revoked permission %s from user %d by user %d%s",
            permission.value,
            user_id,
            revoked_by,
            f" in workspace {workspace_id}" if workspace_id else "",
        )

    def list_permissions(
        self,
        user_id: int,
        workspace_id: Optional[int] = None,
    ) -> dict[str, bool]:
        """List all permissions for user

        Returns a dict mapping permission name to granted status
        """
        user_manager = get_user_manager()
        user = user_manager.get_user(user_id)
        if not user:
            return {}

        permissions = {}

        # Start with default role permissions
        for perm in Permission:
            permissions[perm.value] = self.has_permission(user, perm, workspace_id)

        return permissions

    # -----------------------------------------------------------------------
    # Audit logging
    # -----------------------------------------------------------------------

    def _log_audit(
        self,
        user_id: int,
        action: str,
        permission: Optional[str] = None,
        target_user_id: Optional[int] = None,
        details: str = "",
    ) -> None:
        """Log permission change to audit log"""
        self.conn.execute(
            """
            INSERT INTO permission_audit
            (user_id, action, permission, target_user_id, details, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, action, permission, target_user_id, details, time.time()),
        )
        self.conn.commit()

    def get_audit_log(
        self,
        user_id: Optional[int] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get permission audit log

        Args:
            user_id: Optional filter by user who performed action
            limit: Maximum number of entries

        Returns:
            List of audit log entries
        """
        if user_id is not None:
            rows = self.conn.execute(
                """
                SELECT * FROM permission_audit
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM permission_audit
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            {
                "user_id": row["user_id"],
                "action": row["action"],
                "permission": row["permission"],
                "target_user_id": row["target_user_id"],
                "details": row["details"],
                "timestamp": row["timestamp"],
            }
            for row in rows
        ]

    def close(self) -> None:
        """Close database connection"""
        self.conn.close()


# ---------------------------------------------------------------------------
# Permission decorators
# ---------------------------------------------------------------------------


def require_permission(permission: Permission, workspace_aware: bool = False):
    """Decorator to enforce permission checks on Discord commands

    Args:
        permission: Required permission
        workspace_aware: If True, check workspace-specific permissions

    Usage:
        @require_permission(Permission.MANAGE_SCHEDULES)
        async def schedule_command(interaction: discord.Interaction):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(interaction: discord.Interaction, *args, **kwargs):
            # Get user from Discord ID
            user_manager = get_user_manager()
            user = user_manager.get_user_by_discord_id(interaction.user.id)

            if not user:
                await interaction.response.send_message(
                    "🔒 You must register first. Use `/user register`",
                    ephemeral=True,
                )
                return

            # Check if user is active
            if not user.is_active:
                await interaction.response.send_message(
                    "🔒 Your account is suspended. Contact an administrator.",
                    ephemeral=True,
                )
                return

            # Determine workspace context if needed
            workspace_id = None
            if workspace_aware and hasattr(interaction, "workspace_id"):
                workspace_id = interaction.workspace_id

            # Check permission
            perm_manager = get_permission_manager()
            if not perm_manager.has_permission(user, permission, workspace_id):
                await interaction.response.send_message(
                    f"🔒 You don't have permission: {permission.value}",
                    ephemeral=True,
                )
                return

            # Update user activity
            user_manager.update_activity(user.id)

            # Execute command
            return await func(interaction, *args, **kwargs)

        return wrapper

    return decorator


def require_role(min_role: UserRole):
    """Decorator to enforce minimum role requirement

    Args:
        min_role: Minimum required role

    Usage:
        @require_role(UserRole.ADMIN)
        async def admin_command(interaction: discord.Interaction):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(interaction: discord.Interaction, *args, **kwargs):
            user_manager = get_user_manager()
            user = user_manager.get_user_by_discord_id(interaction.user.id)

            if not user:
                await interaction.response.send_message(
                    "🔒 You must register first. Use `/user register`",
                    ephemeral=True,
                )
                return

            if not user.role >= min_role:
                await interaction.response.send_message(
                    f"🔒 Requires role: {min_role.value} (you are: {user.role.value})",
                    ephemeral=True,
                )
                return

            user_manager.update_activity(user.id)
            return await func(interaction, *args, **kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Global instance
# ---------------------------------------------------------------------------

_permission_manager: Optional[PermissionManager] = None


def get_permission_manager() -> PermissionManager:
    """Get global PermissionManager instance"""
    global _permission_manager
    if _permission_manager is None:
        _permission_manager = PermissionManager()
    return _permission_manager
