"""
CSV Exporter — Export data to CSV format.
Supports conversation history, trend data, task logs, and cost tracking.
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd

log = logging.getLogger("openclaw.exporters.csv")

ExportType = Literal["conversations", "trends", "tasks", "costs", "api_usage"]


async def export_to_csv(
    export_type: ExportType,
    output_path: Path | str,
    *,
    days: int | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Export data to CSV format.

    Args:
        export_type: Type of data to export
        output_path: Path where CSV file will be saved
        days: Number of days to include (optional)
        filters: Additional filters (e.g., topic, category, metric)

    Returns:
        dict with {"success": bool, "rows": int, "path": str, "error": str}
    """
    output_path = Path(output_path)
    filters = filters or {}

    try:
        if export_type == "conversations":
            df = await _export_conversations(days, filters)
        elif export_type == "trends":
            df = await _export_trends(days, filters)
        elif export_type == "tasks":
            df = await _export_tasks(days, filters)
        elif export_type == "costs":
            df = await _export_costs(days, filters)
        elif export_type == "api_usage":
            df = await _export_api_usage(days, filters)
        else:
            return {"success": False, "error": f"Unknown export type: {export_type}"}

        if df.empty:
            return {"success": False, "error": "No data to export", "rows": 0}

        # Write to CSV
        df.to_csv(output_path, index=False)
        
        log.info(f"✅ Exported {len(df)} rows to {output_path}")
        return {
            "success": True,
            "rows": len(df),
            "path": str(output_path),
            "size_bytes": output_path.stat().st_size,
        }

    except Exception as e:
        log.error(f"CSV export failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def _export_conversations(days: int | None, filters: dict[str, Any]) -> pd.DataFrame:
    """Export conversation history from thread store."""
    import os
    
    db_path = Path(os.getenv("THREAD_DB_PATH", "data/memory/openclaw.db"))
    if not db_path.exists():
        return pd.DataFrame()

    cutoff = None
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    conn = sqlite3.connect(str(db_path))
    try:
        # Check if table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='threads'"
        )
        if not cursor.fetchone():
            return pd.DataFrame()

        query = "SELECT * FROM threads WHERE 1=1"
        params = []

        if cutoff:
            query += " AND created_at >= ?"
            params.append(cutoff)

        if "channel_id" in filters:
            query += " AND channel_id = ?"
            params.append(filters["channel_id"])

        query += " ORDER BY created_at DESC"
        
        df = pd.read_sql_query(query, conn, params=params)
        return df

    finally:
        conn.close()


async def _export_trends(days: int | None, filters: dict[str, Any]) -> pd.DataFrame:
    """Export trend data from trend_tracker."""
    import os
    
    db_path = Path(os.getenv("THREAD_DB_PATH", "data/memory/openclaw.db"))
    if not db_path.exists():
        return pd.DataFrame()

    cutoff_ts = None
    if days:
        cutoff_ts = (datetime.now() - timedelta(days=days)).timestamp()

    conn = sqlite3.connect(str(db_path))
    try:
        # Check if table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trend_data'"
        )
        if not cursor.fetchone():
            return pd.DataFrame()

        query = "SELECT * FROM trend_data WHERE 1=1"
        params = []

        if cutoff_ts:
            query += " AND timestamp >= ?"
            params.append(cutoff_ts)

        if "topic" in filters:
            query += " AND topic = ?"
            params.append(filters["topic"])

        if "category" in filters:
            query += " AND category = ?"
            params.append(filters["category"])

        query += " ORDER BY timestamp DESC"
        
        df = pd.read_sql_query(query, conn, params=params)
        
        # Convert timestamp to readable datetime
        if not df.empty and "timestamp" in df.columns:
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
        
        return df

    finally:
        conn.close()


async def _export_tasks(days: int | None, filters: dict[str, Any]) -> pd.DataFrame:
    """Export scheduled task execution logs."""
    import os
    from scheduler import SCHEDULE_FILE
    
    # For now, read from schedule file (future: add execution log table)
    if not SCHEDULE_FILE.exists():
        return pd.DataFrame()

    import json
    with open(SCHEDULE_FILE) as f:
        tasks_data = json.load(f)

    tasks = tasks_data.get("tasks", [])
    
    if not tasks:
        return pd.DataFrame()

    df = pd.DataFrame(tasks)
    
    # Filter by date if specified
    if days and "last_run" in df.columns:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        df = df[df["last_run"] >= cutoff]

    return df


async def _export_costs(days: int | None, filters: dict[str, Any]) -> pd.DataFrame:
    """Export API cost tracking data."""
    # Note: This will use llm_ratelimit or audit data when available
    import os
    
    db_path = Path(os.getenv("THREAD_DB_PATH", "data/memory/openclaw.db"))
    if not db_path.exists():
        return pd.DataFrame()

    conn = sqlite3.connect(str(db_path))
    try:
        # Check if cost tracking table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='api_costs'"
        )
        if not cursor.fetchone():
            return pd.DataFrame()

        query = "SELECT * FROM api_costs WHERE 1=1"
        params = []

        if days:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            query += " AND timestamp >= ?"
            params.append(cutoff)

        query += " ORDER BY timestamp DESC"
        
        df = pd.read_sql_query(query, conn, params=params)
        return df

    finally:
        conn.close()


async def _export_api_usage(days: int | None, filters: dict[str, Any]) -> pd.DataFrame:
    """Export API usage statistics from audit logs."""
    import os
    
    audit_dir = Path(os.getenv("AUDIT_DIR", "data/audit"))
    
    # Read audit logs and aggregate
    # For now, return placeholder (to be enhanced with actual audit data)
    return pd.DataFrame({
        "timestamp": [],
        "endpoint": [],
        "status": [],
        "response_time_ms": [],
    })
