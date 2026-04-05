"""
OpenClaw User Management System — Phase 4

Multi-user support with registration, authentication, profiles, and roles.
Uses SQLite for persistence with Discord user ID mapping.
"""

import hashlib
import json
import logging
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from utils import atomic_write

log = logging.getLogger("openclaw.user_manager")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = Path("/memory/users.db")

class UserRole(Enum):
    """User role hierarchy: admin > member > viewer"""
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"
    
    @property
    def level(self) -> int:
        """Numeric level for comparison (higher = more privileges)"""
        return {"admin": 3, "member": 2, "viewer": 1}[self.value]
    
    def __ge__(self, other: "UserRole") -> bool:
        return self.level >= other.level
    
    def __gt__(self, other: "UserRole") -> bool:
        return self.level > other.level


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class User:
    """User account with profile and settings"""
    id: int                          # Internal user ID (autoincrement)
    discord_id: int                  # Discord user ID
    username: str                    # Discord username
    role: UserRole                   # User role (admin/member/viewer)
    created_at: float                # Unix timestamp
    last_active: float               # Last activity timestamp
    settings: dict[str, Any]         # User preferences/settings
    api_quota_daily: int = 100       # Daily API call quota
    api_quota_used: int = 0          # Quota used today
    quota_reset_at: float = 0.0      # When quota resets
    is_active: bool = True           # Account active/suspended
    session_token: Optional[str] = None  # Current session token
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        d = asdict(self)
        d["role"] = self.role.value
        return d
    
    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        """Create User from database row"""
        settings = json.loads(row["settings_json"]) if row["settings_json"] else {}
        return cls(
            id=row["id"],
            discord_id=row["discord_id"],
            username=row["username"],
            role=UserRole(row["role"]),
            created_at=row["created_at"],
            last_active=row["last_active"],
            settings=settings,
            api_quota_daily=row["api_quota_daily"],
            api_quota_used=row["api_quota_used"],
            quota_reset_at=row["quota_reset_at"],
            is_active=bool(row["is_active"]),
            session_token=row["session_token"],
        )


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

def _init_db(conn: sqlite3.Connection) -> None:
    """Initialize database schema"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER UNIQUE NOT NULL,
            username TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            created_at REAL NOT NULL,
            last_active REAL NOT NULL,
            settings_json TEXT,
            api_quota_daily INTEGER DEFAULT 100,
            api_quota_used INTEGER DEFAULT 0,
            quota_reset_at REAL DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            session_token TEXT
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_discord_id 
        ON users(discord_id)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_session_token 
        ON users(session_token)
    """)
    
    # User activity log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            activity_type TEXT NOT NULL,
            details TEXT,
            timestamp REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_activity_user_timestamp 
        ON user_activity(user_id, timestamp)
    """)
    
    conn.commit()


# ---------------------------------------------------------------------------
# User Manager
# ---------------------------------------------------------------------------

class UserManager:
    """Manages user accounts, authentication, and profiles"""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        _init_db(self.conn)
        log.info("UserManager initialized with database: %s", db_path)
    
    # -----------------------------------------------------------------------
    # User registration & retrieval
    # -----------------------------------------------------------------------
    
    def register_user(
        self,
        discord_id: int,
        username: str,
        role: UserRole = UserRole.MEMBER,
    ) -> User:
        """Register a new user or update existing username
        
        Args:
            discord_id: Discord user ID
            username: Discord username
            role: Initial role (default: MEMBER)
        
        Returns:
            User object
        """
        now = time.time()
        
        # Check if user already exists
        existing = self.get_user_by_discord_id(discord_id)
        if existing:
            # Update username if changed
            if existing.username != username:
                self.conn.execute(
                    "UPDATE users SET username = ?, last_active = ? WHERE discord_id = ?",
                    (username, now, discord_id),
                )
                self.conn.commit()
                existing.username = username
                existing.last_active = now
                log.info("Updated username for user %d: %s", discord_id, username)
            return existing
        
        # Create new user
        cursor = self.conn.execute(
            """
            INSERT INTO users (discord_id, username, role, created_at, last_active, settings_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (discord_id, username, role.value, now, now, "{}"),
        )
        self.conn.commit()
        
        user = self.get_user(cursor.lastrowid)
        if not user:
            raise RuntimeError(f"Failed to create user {discord_id}")
        
        self._log_activity(user.id, "registration", f"User {username} registered")
        log.info("Registered new user: %s (discord_id=%d, role=%s)", username, discord_id, role.value)
        return user
    
    def get_user(self, user_id: int) -> Optional[User]:
        """Get user by internal ID"""
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return User.from_row(row) if row else None
    
    def get_user_by_discord_id(self, discord_id: int) -> Optional[User]:
        """Get user by Discord ID"""
        row = self.conn.execute(
            "SELECT * FROM users WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
        return User.from_row(row) if row else None
    
    def get_user_by_session_token(self, token: str) -> Optional[User]:
        """Get user by session token"""
        row = self.conn.execute(
            "SELECT * FROM users WHERE session_token = ? AND is_active = 1",
            (token,),
        ).fetchone()
        return User.from_row(row) if row else None
    
    def list_users(self, role: Optional[UserRole] = None) -> list[User]:
        """List all users, optionally filtered by role"""
        if role:
            rows = self.conn.execute(
                "SELECT * FROM users WHERE role = ? ORDER BY created_at DESC",
                (role.value,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC"
            ).fetchall()
        
        return [User.from_row(row) for row in rows]
    
    # -----------------------------------------------------------------------
    # Profile & settings
    # -----------------------------------------------------------------------
    
    def update_settings(self, user_id: int, settings: dict[str, Any]) -> None:
        """Update user settings (merge with existing)"""
        user = self.get_user(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")
        
        # Merge settings
        user.settings.update(settings)
        
        self.conn.execute(
            "UPDATE users SET settings_json = ? WHERE id = ?",
            (json.dumps(user.settings), user_id),
        )
        self.conn.commit()
        log.info("Updated settings for user %d", user_id)
    
    def get_settings(self, user_id: int) -> dict[str, Any]:
        """Get user settings"""
        user = self.get_user(user_id)
        return user.settings if user else {}
    
    def update_role(self, user_id: int, new_role: UserRole) -> None:
        """Update user role (admin operation)"""
        self.conn.execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (new_role.value, user_id),
        )
        self.conn.commit()
        self._log_activity(user_id, "role_change", f"Role changed to {new_role.value}")
        log.info("Updated role for user %d to %s", user_id, new_role.value)
    
    # -----------------------------------------------------------------------
    # Session management
    # -----------------------------------------------------------------------
    
    def create_session(self, user_id: int) -> str:
        """Create a new session token for user"""
        token = secrets.token_urlsafe(32)
        
        self.conn.execute(
            "UPDATE users SET session_token = ?, last_active = ? WHERE id = ?",
            (token, time.time(), user_id),
        )
        self.conn.commit()
        
        self._log_activity(user_id, "login", "Session created")
        log.info("Created session for user %d", user_id)
        return token
    
    def invalidate_session(self, user_id: int) -> None:
        """Invalidate user session (logout)"""
        self.conn.execute(
            "UPDATE users SET session_token = NULL WHERE id = ?",
            (user_id,),
        )
        self.conn.commit()
        self._log_activity(user_id, "logout", "Session invalidated")
        log.info("Invalidated session for user %d", user_id)
    
    def update_activity(self, user_id: int) -> None:
        """Update last active timestamp"""
        self.conn.execute(
            "UPDATE users SET last_active = ? WHERE id = ?",
            (time.time(), user_id),
        )
        self.conn.commit()
    
    # -----------------------------------------------------------------------
    # API quota management
    # -----------------------------------------------------------------------
    
    def check_quota(self, user_id: int) -> bool:
        """Check if user has remaining API quota
        
        Returns:
            True if quota available, False if exceeded
        """
        user = self.get_user(user_id)
        if not user:
            return False
        
        # Reset quota if it's a new day
        now = time.time()
        if now >= user.quota_reset_at:
            self._reset_quota(user_id)
            return True
        
        return user.api_quota_used < user.api_quota_daily
    
    def consume_quota(self, user_id: int, amount: int = 1) -> bool:
        """Consume API quota
        
        Args:
            user_id: User ID
            amount: Quota units to consume
        
        Returns:
            True if quota consumed successfully, False if exceeded
        """
        if not self.check_quota(user_id):
            return False
        
        self.conn.execute(
            "UPDATE users SET api_quota_used = api_quota_used + ? WHERE id = ?",
            (amount, user_id),
        )
        self.conn.commit()
        return True
    
    def _reset_quota(self, user_id: int) -> None:
        """Reset daily quota for user"""
        # Reset at midnight tomorrow
        import datetime
        now = datetime.datetime.now()
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
        reset_at = tomorrow.timestamp()
        
        self.conn.execute(
            "UPDATE users SET api_quota_used = 0, quota_reset_at = ? WHERE id = ?",
            (reset_at, user_id),
        )
        self.conn.commit()
        log.debug("Reset quota for user %d", user_id)
    
    def set_quota(self, user_id: int, daily_limit: int) -> None:
        """Set custom daily quota for user"""
        self.conn.execute(
            "UPDATE users SET api_quota_daily = ? WHERE id = ?",
            (daily_limit, user_id),
        )
        self.conn.commit()
        log.info("Set daily quota for user %d to %d", user_id, daily_limit)
    
    # -----------------------------------------------------------------------
    # User status
    # -----------------------------------------------------------------------
    
    def suspend_user(self, user_id: int) -> None:
        """Suspend user account"""
        self.conn.execute(
            "UPDATE users SET is_active = 0, session_token = NULL WHERE id = ?",
            (user_id,),
        )
        self.conn.commit()
        self._log_activity(user_id, "suspension", "Account suspended")
        log.warning("Suspended user %d", user_id)
    
    def activate_user(self, user_id: int) -> None:
        """Activate user account"""
        self.conn.execute(
            "UPDATE users SET is_active = 1 WHERE id = ?",
            (user_id,),
        )
        self.conn.commit()
        self._log_activity(user_id, "activation", "Account activated")
        log.info("Activated user %d", user_id)
    
    # -----------------------------------------------------------------------
    # Activity logging
    # -----------------------------------------------------------------------
    
    def _log_activity(self, user_id: int, activity_type: str, details: str = "") -> None:
        """Log user activity"""
        self.conn.execute(
            "INSERT INTO user_activity (user_id, activity_type, details, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, activity_type, details, time.time()),
        )
        self.conn.commit()
    
    def get_user_activity(
        self,
        user_id: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get recent user activity"""
        rows = self.conn.execute(
            """
            SELECT activity_type, details, timestamp
            FROM user_activity
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        
        return [
            {
                "type": row["activity_type"],
                "details": row["details"],
                "timestamp": row["timestamp"],
            }
            for row in rows
        ]
    
    # -----------------------------------------------------------------------
    # Admin utilities
    # -----------------------------------------------------------------------
    
    def get_stats(self) -> dict[str, Any]:
        """Get user statistics"""
        total = self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active = self.conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0]
        
        roles = {}
        for role in UserRole:
            count = self.conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = ?",
                (role.value,),
            ).fetchone()[0]
            roles[role.value] = count
        
        return {
            "total_users": total,
            "active_users": active,
            "suspended_users": total - active,
            "by_role": roles,
        }
    
    def close(self) -> None:
        """Close database connection"""
        self.conn.close()


# ---------------------------------------------------------------------------
# Global instance
# ---------------------------------------------------------------------------

_user_manager: Optional[UserManager] = None


def get_user_manager() -> UserManager:
    """Get global UserManager instance"""
    global _user_manager
    if _user_manager is None:
        _user_manager = UserManager()
    return _user_manager
