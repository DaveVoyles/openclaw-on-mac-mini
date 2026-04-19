"""
OpenClaw Share Manager — Phase 4

Public dashboard sharing with token-based authentication, expiration policies, and access analytics.
"""

import json
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = Path("/memory/shares.db")


class ShareDuration(Enum):
    """Share link expiration durations"""
    HOURS_24 = "24h"
    DAYS_7 = "7d"
    DAYS_30 = "30d"
    NEVER = "never"

    @property
    def seconds(self) -> Optional[float]:
        """Duration in seconds (None for never)"""
        durations = {
            "24h": 24 * 3600,
            "7d": 7 * 24 * 3600,
            "30d": 30 * 24 * 3600,
            "never": None,
        }
        return durations[self.value]


class ResourceType(Enum):
    """Types of shareable resources"""
    DASHBOARD = "dashboard"
    QUERY = "query"
    REPORT = "report"
    ANALYTICS = "analytics"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ShareLink:
    """Shareable link with token-based auth"""
    id: int                          # Internal share ID
    token: str                       # Unique share token (UUID)
    resource_type: ResourceType      # Type of shared resource
    resource_id: str                 # ID of shared resource
    created_by: int                  # User who created the share
    created_at: float                # Creation timestamp
    expires_at: Optional[float]      # Expiration timestamp (None = never)
    is_public: bool                  # Public (anyone with link) or private
    view_count: int                  # Number of times accessed
    last_accessed: Optional[float]   # Last access timestamp
    is_active: bool                  # Share active or revoked
    metadata: dict[str, Any]         # Additional metadata

    @property
    def is_expired(self) -> bool:
        """Check if share link is expired"""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if share link is valid (active and not expired)"""
        return self.is_active and not self.is_expired

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ShareLink":
        """Create ShareLink from database row"""
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        return cls(
            id=row["id"],
            token=row["token"],
            resource_type=ResourceType(row["resource_type"]),
            resource_id=row["resource_id"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            is_public=bool(row["is_public"]),
            view_count=row["view_count"],
            last_accessed=row["last_accessed"],
            is_active=bool(row["is_active"]),
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

def _init_db(conn: sqlite3.Connection) -> None:
    """Initialize database schema"""
    # Share links table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS share_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL,
            is_public INTEGER DEFAULT 1,
            view_count INTEGER DEFAULT 0,
            last_accessed REAL,
            is_active INTEGER DEFAULT 1,
            metadata_json TEXT
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_shares_token
        ON share_links(token)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_shares_resource
        ON share_links(resource_type, resource_id)
    """)

    # Access analytics table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS share_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            share_id INTEGER NOT NULL,
            accessed_at REAL NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            referrer TEXT,
            FOREIGN KEY (share_id) REFERENCES share_links(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_access_share_time
        ON share_access(share_id, accessed_at)
    """)

    # Embed configurations table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embed_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            share_id INTEGER NOT NULL,
            embed_code TEXT NOT NULL,
            allowed_domains TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY (share_id) REFERENCES share_links(id) ON DELETE CASCADE
        )
    """)

    conn.commit()


# ---------------------------------------------------------------------------
# Share Manager
# ---------------------------------------------------------------------------

class ShareManager:
    """Manages public sharing of resources with token-based authentication"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        _init_db(self.conn)
        log.info("ShareManager initialized with database: %s", db_path)

    # -----------------------------------------------------------------------
    # Share creation & management
    # -----------------------------------------------------------------------

    def create_share(
        self,
        resource_type: ResourceType,
        resource_id: str,
        created_by: int,
        duration: ShareDuration = ShareDuration.DAYS_7,
        is_public: bool = True,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ShareLink:
        """Create a shareable link

        Args:
            resource_type: Type of resource to share
            resource_id: ID of resource to share
            created_by: User ID who is sharing
            duration: Share expiration duration
            is_public: Public (anyone with link) or private
            metadata: Optional metadata

        Returns:
            ShareLink object
        """
        token = secrets.token_urlsafe(32)
        now = time.time()

        # Calculate expiration
        expires_at = None
        if duration.seconds is not None:
            expires_at = now + duration.seconds

        metadata_json = json.dumps(metadata) if metadata else "{}"

        cursor = self.conn.execute(
            """
            INSERT INTO share_links
            (token, resource_type, resource_id, created_by, created_at, expires_at, is_public, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (token, resource_type.value, resource_id, created_by, now, expires_at, int(is_public), metadata_json),
        )
        self.conn.commit()

        share = self.get_share(cursor.lastrowid)
        if not share:
            raise RuntimeError("Failed to create share link")

        log.info(
            "Created share link for %s/%s (token=%s, expires=%s)",
            resource_type.value, resource_id, token[:8] + "...",
            duration.value,
        )
        return share

    def get_share(self, share_id: int) -> Optional[ShareLink]:
        """Get share link by ID"""
        row = self.conn.execute(
            "SELECT * FROM share_links WHERE id = ?",
            (share_id,),
        ).fetchone()
        return ShareLink.from_row(row) if row else None

    def get_share_by_token(self, token: str) -> Optional[ShareLink]:
        """Get share link by token"""
        row = self.conn.execute(
            "SELECT * FROM share_links WHERE token = ?",
            (token,),
        ).fetchone()
        return ShareLink.from_row(row) if row else None

    def get_share_by_resource(
        self,
        resource_type: ResourceType,
        resource_id: str,
    ) -> Optional[ShareLink]:
        """Get active share link for resource"""
        row = self.conn.execute(
            """
            SELECT * FROM share_links
            WHERE resource_type = ? AND resource_id = ? AND is_active = 1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (resource_type.value, resource_id),
        ).fetchone()
        return ShareLink.from_row(row) if row else None

    def list_shares(
        self,
        created_by: Optional[int] = None,
        resource_type: Optional[ResourceType] = None,
        active_only: bool = True,
    ) -> list[ShareLink]:
        """List share links with optional filtering"""
        query = "SELECT * FROM share_links WHERE 1=1"
        params = []

        if created_by is not None:
            query += " AND created_by = ?"
            params.append(created_by)

        if resource_type is not None:
            query += " AND resource_type = ?"
            params.append(resource_type.value)

        if active_only:
            query += " AND is_active = 1"

        query += " ORDER BY created_at DESC"

        rows = self.conn.execute(query, params).fetchall()
        return [ShareLink.from_row(row) for row in rows]

    def revoke_share(self, share_id: int) -> None:
        """Revoke a share link"""
        self.conn.execute(
            "UPDATE share_links SET is_active = 0 WHERE id = ?",
            (share_id,),
        )
        self.conn.commit()
        log.info("Revoked share link %d", share_id)

    def revoke_share_by_token(self, token: str) -> None:
        """Revoke a share link by token"""
        self.conn.execute(
            "UPDATE share_links SET is_active = 0 WHERE token = ?",
            (token,),
        )
        self.conn.commit()
        log.info("Revoked share link with token %s...", token[:8])

    def update_share(
        self,
        share_id: int,
        expires_at: Optional[float] = None,
        is_public: Optional[bool] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Update share link settings"""
        updates = []
        params = []

        if expires_at is not None:
            updates.append("expires_at = ?")
            params.append(expires_at)

        if is_public is not None:
            updates.append("is_public = ?")
            params.append(int(is_public))

        if metadata is not None:
            updates.append("metadata_json = ?")
            params.append(json.dumps(metadata))

        if not updates:
            return

        params.append(share_id)
        query = f"UPDATE share_links SET {', '.join(updates)} WHERE id = ?"

        self.conn.execute(query, params)
        self.conn.commit()
        log.info("Updated share link %d", share_id)

    # -----------------------------------------------------------------------
    # Access tracking
    # -----------------------------------------------------------------------

    def record_access(
        self,
        share_id: int,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        referrer: Optional[str] = None,
    ) -> None:
        """Record access to shared resource

        Args:
            share_id: Share link ID
            ip_address: Client IP address
            user_agent: Client user agent
            referrer: HTTP referrer
        """
        now = time.time()

        # Record access
        self.conn.execute(
            """
            INSERT INTO share_access (share_id, accessed_at, ip_address, user_agent, referrer)
            VALUES (?, ?, ?, ?, ?)
            """,
            (share_id, now, ip_address, user_agent, referrer),
        )

        # Update share link stats
        self.conn.execute(
            """
            UPDATE share_links
            SET view_count = view_count + 1, last_accessed = ?
            WHERE id = ?
            """,
            (now, share_id),
        )

        self.conn.commit()
        log.debug("Recorded access to share %d from %s", share_id, ip_address or "unknown")

    def get_access_analytics(
        self,
        share_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get access analytics for share link

        Returns list of access events with metadata
        """
        rows = self.conn.execute(
            """
            SELECT accessed_at, ip_address, user_agent, referrer
            FROM share_access
            WHERE share_id = ?
            ORDER BY accessed_at DESC
            LIMIT ?
            """,
            (share_id, limit),
        ).fetchall()

        return [
            {
                "timestamp": row["accessed_at"],
                "ip": row["ip_address"],
                "user_agent": row["user_agent"],
                "referrer": row["referrer"],
            }
            for row in rows
        ]

    def get_access_stats(self, share_id: int) -> dict[str, Any]:
        """Get aggregated access statistics"""
        # Total views
        total = self.conn.execute(
            "SELECT view_count, last_accessed FROM share_links WHERE id = ?",
            (share_id,),
        ).fetchone()

        if not total:
            return {}

        # Unique IPs
        unique_ips = self.conn.execute(
            "SELECT COUNT(DISTINCT ip_address) FROM share_access WHERE share_id = ?",
            (share_id,),
        ).fetchone()[0]

        # Recent accesses (last 24h)
        day_ago = time.time() - 86400
        recent = self.conn.execute(
            "SELECT COUNT(*) FROM share_access WHERE share_id = ? AND accessed_at > ?",
            (share_id, day_ago),
        ).fetchone()[0]

        return {
            "total_views": total["view_count"],
            "unique_visitors": unique_ips,
            "views_24h": recent,
            "last_accessed": total["last_accessed"],
        }

    # -----------------------------------------------------------------------
    # Embed code generation
    # -----------------------------------------------------------------------

    def create_embed_code(
        self,
        share_id: int,
        allowed_domains: Optional[list[str]] = None,
        width: str = "100%",
        height: str = "600px",
    ) -> str:
        """Generate embed code for shared resource

        Args:
            share_id: Share link ID
            allowed_domains: Optional list of allowed domains for embedding
            width: iframe width (CSS value)
            height: iframe height (CSS value)

        Returns:
            HTML embed code
        """
        share = self.get_share(share_id)
        if not share:
            raise ValueError(f"Share {share_id} not found")

        # Generate embed URL
        embed_url = f"/shared/{share.token}"

        # Create iframe code
        embed_code = f'''<iframe
    src="{embed_url}"
    width="{width}"
    height="{height}"
    frameborder="0"
    allowtransparency="true"
    style="border: 1px solid #ddd; border-radius: 4px;">
</iframe>'''

        # Save embed configuration
        domains_json = json.dumps(allowed_domains) if allowed_domains else None
        self.conn.execute(
            """
            INSERT INTO embed_configs (share_id, embed_code, allowed_domains, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (share_id, embed_code, domains_json, time.time()),
        )
        self.conn.commit()

        log.info("Generated embed code for share %d", share_id)
        return embed_code

    def get_embed_config(self, share_id: int) -> Optional[dict[str, Any]]:
        """Get embed configuration for share"""
        row = self.conn.execute(
            "SELECT * FROM embed_configs WHERE share_id = ? ORDER BY created_at DESC LIMIT 1",
            (share_id,),
        ).fetchone()

        if not row:
            return None

        allowed_domains = json.loads(row["allowed_domains"]) if row["allowed_domains"] else None

        return {
            "embed_code": row["embed_code"],
            "allowed_domains": allowed_domains,
            "created_at": row["created_at"],
        }

    # -----------------------------------------------------------------------
    # Utilities
    # -----------------------------------------------------------------------

    def cleanup_expired(self) -> int:
        """Remove expired share links

        Returns:
            Number of shares removed
        """
        now = time.time()
        cursor = self.conn.execute(
            "DELETE FROM share_links WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )
        self.conn.commit()

        count = cursor.rowcount
        if count > 0:
            log.info("Cleaned up %d expired share links", count)

        return count

    def get_stats(self) -> dict[str, Any]:
        """Get global sharing statistics"""
        total = self.conn.execute("SELECT COUNT(*) FROM share_links").fetchone()[0]
        active = self.conn.execute("SELECT COUNT(*) FROM share_links WHERE is_active = 1").fetchone()[0]

        total_views = self.conn.execute("SELECT SUM(view_count) FROM share_links").fetchone()[0] or 0
        total_accesses = self.conn.execute("SELECT COUNT(*) FROM share_access").fetchone()[0]

        return {
            "total_shares": total,
            "active_shares": active,
            "total_views": total_views,
            "total_accesses": total_accesses,
        }

    def close(self) -> None:
        """Close database connection"""
        self.conn.close()


# ---------------------------------------------------------------------------
# Global instance
# ---------------------------------------------------------------------------

_share_manager: Optional[ShareManager] = None


def get_share_manager() -> ShareManager:
    """Get global ShareManager instance"""
    global _share_manager
    if _share_manager is None:
        _share_manager = ShareManager()
    return _share_manager
