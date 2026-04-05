"""
OpenClaw Workspace Manager — Phase 4

Team workspaces with shared resources, member management, and workspace-level quotas.
"""

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from user_manager import User, UserRole, get_user_manager

log = logging.getLogger("openclaw.workspace_manager")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = Path("/memory/workspaces.db")


class WorkspaceRole(Enum):
    """Workspace-specific role"""
    OWNER = "owner"  # Workspace owner (all permissions)
    ADMIN = "admin"  # Admin within workspace
    MEMBER = "member"  # Standard member
    VIEWER = "viewer"  # Read-only access
    
    @property
    def level(self) -> int:
        """Numeric level for comparison"""
        levels = {"owner": 4, "admin": 3, "member": 2, "viewer": 1}
        return levels.get(self.value, 0)
    
    def __ge__(self, other: "WorkspaceRole") -> bool:
        return self.level >= other.level
    
    def __gt__(self, other: "WorkspaceRole") -> bool:
        return self.level > other.level


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Workspace:
    """Team workspace with shared resources"""
    id: int                          # Internal workspace ID
    name: str                        # Workspace name (unique)
    owner_id: int                    # User ID of workspace owner
    created_at: float                # Unix timestamp
    description: str = ""            # Workspace description
    settings: dict[str, Any] = None  # Workspace settings
    api_quota_daily: int = 500       # Shared daily API quota
    api_quota_used: int = 0          # Quota used today
    quota_reset_at: float = 0.0      # When quota resets
    is_active: bool = True           # Workspace active/archived
    
    def __post_init__(self):
        if self.settings is None:
            self.settings = {}
    
    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Workspace":
        """Create Workspace from database row"""
        settings = json.loads(row["settings_json"]) if row["settings_json"] else {}
        return cls(
            id=row["id"],
            name=row["name"],
            owner_id=row["owner_id"],
            created_at=row["created_at"],
            description=row["description"] or "",
            settings=settings,
            api_quota_daily=row["api_quota_daily"],
            api_quota_used=row["api_quota_used"],
            quota_reset_at=row["quota_reset_at"],
            is_active=bool(row["is_active"]),
        )


@dataclass
class WorkspaceMember:
    """Workspace membership with role"""
    workspace_id: int
    user_id: int
    role: WorkspaceRole
    joined_at: float
    
    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "WorkspaceMember":
        """Create WorkspaceMember from database row"""
        return cls(
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            role=WorkspaceRole(row["role"]),
            joined_at=row["joined_at"],
        )


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

def _init_db(conn: sqlite3.Connection) -> None:
    """Initialize database schema"""
    # Workspaces table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            owner_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            description TEXT,
            settings_json TEXT,
            api_quota_daily INTEGER DEFAULT 500,
            api_quota_used INTEGER DEFAULT 0,
            quota_reset_at REAL DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_workspaces_name 
        ON workspaces(name)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_workspaces_owner 
        ON workspaces(owner_id)
    """)
    
    # Workspace members table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workspace_members (
            workspace_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            joined_at REAL NOT NULL,
            PRIMARY KEY (workspace_id, user_id),
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_members_user 
        ON workspace_members(user_id)
    """)
    
    # Shared resources table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workspace_resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL,
            resource_type TEXT NOT NULL,
            resource_name TEXT NOT NULL,
            resource_data TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_resources_workspace 
        ON workspace_resources(workspace_id, resource_type)
    """)
    
    conn.commit()


# ---------------------------------------------------------------------------
# Workspace Manager
# ---------------------------------------------------------------------------

class WorkspaceManager:
    """Manages team workspaces and membership"""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        # Enable foreign key constraints
        self.conn.execute("PRAGMA foreign_keys = ON")
        _init_db(self.conn)
        log.info("WorkspaceManager initialized with database: %s", db_path)
    
    # -----------------------------------------------------------------------
    # Workspace creation & management
    # -----------------------------------------------------------------------
    
    def create_workspace(
        self,
        name: str,
        owner_id: int,
        description: str = "",
    ) -> Workspace:
        """Create a new workspace
        
        Args:
            name: Workspace name (must be unique)
            owner_id: User ID of workspace owner
            description: Optional workspace description
        
        Returns:
            Workspace object
        
        Raises:
            ValueError: If workspace name already exists
        """
        # Check if name is already taken
        existing = self.get_workspace_by_name(name)
        if existing:
            raise ValueError(f"Workspace '{name}' already exists")
        
        now = time.time()
        cursor = self.conn.execute(
            """
            INSERT INTO workspaces (name, owner_id, created_at, description, settings_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, owner_id, now, description, "{}"),
        )
        self.conn.commit()
        
        workspace_id = cursor.lastrowid
        
        # Add owner as member with OWNER role
        self.add_member(workspace_id, owner_id, WorkspaceRole.OWNER)
        
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            raise RuntimeError(f"Failed to create workspace {name}")
        
        log.info("Created workspace: %s (id=%d, owner=%d)", name, workspace_id, owner_id)
        return workspace
    
    def get_workspace(self, workspace_id: int) -> Optional[Workspace]:
        """Get workspace by ID"""
        row = self.conn.execute(
            "SELECT * FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        return Workspace.from_row(row) if row else None
    
    def get_workspace_by_name(self, name: str) -> Optional[Workspace]:
        """Get workspace by name"""
        row = self.conn.execute(
            "SELECT * FROM workspaces WHERE name = ?",
            (name,),
        ).fetchone()
        return Workspace.from_row(row) if row else None
    
    def list_workspaces(self, user_id: Optional[int] = None) -> list[Workspace]:
        """List all workspaces, optionally filtered by user membership"""
        if user_id:
            rows = self.conn.execute(
                """
                SELECT w.* FROM workspaces w
                JOIN workspace_members wm ON w.id = wm.workspace_id
                WHERE wm.user_id = ? AND w.is_active = 1
                ORDER BY w.created_at DESC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM workspaces WHERE is_active = 1 ORDER BY created_at DESC"
            ).fetchall()
        
        return [Workspace.from_row(row) for row in rows]
    
    def update_workspace(
        self,
        workspace_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        settings: Optional[dict[str, Any]] = None,
    ) -> None:
        """Update workspace details"""
        updates = []
        params = []
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        
        if settings is not None:
            updates.append("settings_json = ?")
            params.append(json.dumps(settings))
        
        if not updates:
            return
        
        params.append(workspace_id)
        query = f"UPDATE workspaces SET {', '.join(updates)} WHERE id = ?"
        
        self.conn.execute(query, params)
        self.conn.commit()
        log.info("Updated workspace %d", workspace_id)
    
    def archive_workspace(self, workspace_id: int) -> None:
        """Archive workspace (soft delete)"""
        self.conn.execute(
            "UPDATE workspaces SET is_active = 0 WHERE id = ?",
            (workspace_id,),
        )
        self.conn.commit()
        log.info("Archived workspace %d", workspace_id)
    
    # -----------------------------------------------------------------------
    # Member management
    # -----------------------------------------------------------------------
    
    def add_member(
        self,
        workspace_id: int,
        user_id: int,
        role: WorkspaceRole = WorkspaceRole.MEMBER,
    ) -> None:
        """Add user to workspace
        
        Args:
            workspace_id: Workspace ID
            user_id: User ID to add
            role: Workspace role (default: MEMBER)
        """
        now = time.time()
        
        try:
            self.conn.execute(
                """
                INSERT INTO workspace_members (workspace_id, user_id, role, joined_at)
                VALUES (?, ?, ?, ?)
                """,
                (workspace_id, user_id, role.value, now),
            )
            self.conn.commit()
            log.info("Added user %d to workspace %d with role %s", user_id, workspace_id, role.value)
        except sqlite3.IntegrityError:
            # User already a member - update role
            self.conn.execute(
                "UPDATE workspace_members SET role = ? WHERE workspace_id = ? AND user_id = ?",
                (role.value, workspace_id, user_id),
            )
            self.conn.commit()
            log.info("Updated role for user %d in workspace %d to %s", user_id, workspace_id, role.value)
    
    def remove_member(self, workspace_id: int, user_id: int) -> None:
        """Remove user from workspace"""
        # Don't allow removing the owner
        workspace = self.get_workspace(workspace_id)
        if workspace and workspace.owner_id == user_id:
            raise ValueError("Cannot remove workspace owner")
        
        self.conn.execute(
            "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        )
        self.conn.commit()
        log.info("Removed user %d from workspace %d", user_id, workspace_id)
    
    def get_member_role(self, workspace_id: int, user_id: int) -> Optional[WorkspaceRole]:
        """Get user's role in workspace"""
        row = self.conn.execute(
            "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        ).fetchone()
        return WorkspaceRole(row["role"]) if row else None
    
    def list_members(self, workspace_id: int) -> list[tuple[User, WorkspaceRole]]:
        """List all members of workspace with their roles"""
        rows = self.conn.execute(
            """
            SELECT u.*, wm.role, wm.joined_at
            FROM workspace_members wm
            JOIN users u ON wm.user_id = u.id
            WHERE wm.workspace_id = ?
            ORDER BY wm.joined_at
            """,
            (workspace_id,),
        ).fetchall()
        
        user_manager = get_user_manager()
        members = []
        for row in rows:
            user = user_manager.get_user(row["id"])
            if user:
                role = WorkspaceRole(row["role"])
                members.append((user, role))
        
        return members
    
    def is_member(self, workspace_id: int, user_id: int) -> bool:
        """Check if user is a member of workspace"""
        return self.get_member_role(workspace_id, user_id) is not None
    
    # -----------------------------------------------------------------------
    # API quota management
    # -----------------------------------------------------------------------
    
    def check_quota(self, workspace_id: int) -> bool:
        """Check if workspace has remaining API quota"""
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            return False
        
        # Reset quota if it's a new day
        now = time.time()
        if now >= workspace.quota_reset_at:
            self._reset_quota(workspace_id)
            return True
        
        return workspace.api_quota_used < workspace.api_quota_daily
    
    def consume_quota(self, workspace_id: int, amount: int = 1) -> bool:
        """Consume workspace API quota"""
        if not self.check_quota(workspace_id):
            return False
        
        self.conn.execute(
            "UPDATE workspaces SET api_quota_used = api_quota_used + ? WHERE id = ?",
            (amount, workspace_id),
        )
        self.conn.commit()
        return True
    
    def _reset_quota(self, workspace_id: int) -> None:
        """Reset daily quota for workspace"""
        import datetime
        now = datetime.datetime.now()
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
        reset_at = tomorrow.timestamp()
        
        self.conn.execute(
            "UPDATE workspaces SET api_quota_used = 0, quota_reset_at = ? WHERE id = ?",
            (reset_at, workspace_id),
        )
        self.conn.commit()
        log.debug("Reset quota for workspace %d", workspace_id)
    
    def set_quota(self, workspace_id: int, daily_limit: int) -> None:
        """Set custom daily quota for workspace"""
        self.conn.execute(
            "UPDATE workspaces SET api_quota_daily = ? WHERE id = ?",
            (daily_limit, workspace_id),
        )
        self.conn.commit()
        log.info("Set daily quota for workspace %d to %d", workspace_id, daily_limit)
    
    # -----------------------------------------------------------------------
    # Shared resources
    # -----------------------------------------------------------------------
    
    def save_resource(
        self,
        workspace_id: int,
        resource_type: str,
        resource_name: str,
        resource_data: dict[str, Any],
        created_by: int,
    ) -> int:
        """Save a shared resource to workspace
        
        Args:
            workspace_id: Workspace ID
            resource_type: Type of resource (query, dashboard, schedule, etc.)
            resource_name: Resource name
            resource_data: Resource data (will be JSON serialized)
            created_by: User ID who created the resource
        
        Returns:
            Resource ID
        """
        now = time.time()
        cursor = self.conn.execute(
            """
            INSERT INTO workspace_resources 
            (workspace_id, resource_type, resource_name, resource_data, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (workspace_id, resource_type, resource_name, json.dumps(resource_data), created_by, now, now),
        )
        self.conn.commit()
        
        log.info(
            "Saved resource '%s' (type=%s) to workspace %d",
            resource_name, resource_type, workspace_id,
        )
        return cursor.lastrowid
    
    def get_resource(self, resource_id: int) -> Optional[dict[str, Any]]:
        """Get shared resource by ID"""
        row = self.conn.execute(
            "SELECT * FROM workspace_resources WHERE id = ?",
            (resource_id,),
        ).fetchone()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "type": row["resource_type"],
            "name": row["resource_name"],
            "data": json.loads(row["resource_data"]),
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    
    def list_resources(
        self,
        workspace_id: int,
        resource_type: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List shared resources in workspace"""
        if resource_type:
            rows = self.conn.execute(
                """
                SELECT * FROM workspace_resources 
                WHERE workspace_id = ? AND resource_type = ?
                ORDER BY updated_at DESC
                """,
                (workspace_id, resource_type),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM workspace_resources 
                WHERE workspace_id = ?
                ORDER BY updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        
        resources = []
        for row in rows:
            resources.append({
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "type": row["resource_type"],
                "name": row["resource_name"],
                "data": json.loads(row["resource_data"]),
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        
        return resources
    
    def delete_resource(self, resource_id: int) -> None:
        """Delete shared resource"""
        self.conn.execute(
            "DELETE FROM workspace_resources WHERE id = ?",
            (resource_id,),
        )
        self.conn.commit()
        log.info("Deleted resource %d", resource_id)
    
    # -----------------------------------------------------------------------
    # Utilities
    # -----------------------------------------------------------------------
    
    def get_stats(self, workspace_id: int) -> dict[str, Any]:
        """Get workspace statistics"""
        member_count = self.conn.execute(
            "SELECT COUNT(*) FROM workspace_members WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()[0]
        
        resource_count = self.conn.execute(
            "SELECT COUNT(*) FROM workspace_resources WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()[0]
        
        workspace = self.get_workspace(workspace_id)
        
        return {
            "member_count": member_count,
            "resource_count": resource_count,
            "quota_used": workspace.api_quota_used if workspace else 0,
            "quota_daily": workspace.api_quota_daily if workspace else 0,
            "quota_remaining": (workspace.api_quota_daily - workspace.api_quota_used) if workspace else 0,
        }
    
    def close(self) -> None:
        """Close database connection"""
        self.conn.close()


# ---------------------------------------------------------------------------
# Global instance
# ---------------------------------------------------------------------------

_workspace_manager: Optional[WorkspaceManager] = None


def get_workspace_manager() -> WorkspaceManager:
    """Get global WorkspaceManager instance"""
    global _workspace_manager
    if _workspace_manager is None:
        _workspace_manager = WorkspaceManager()
    return _workspace_manager
