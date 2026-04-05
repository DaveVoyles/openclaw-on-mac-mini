"""
OpenClaw Export API — Phase 3: REST API for Data Exports
Provides REST endpoints for exporting data and generating reports.
"""

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any

from aiohttp import web

log = logging.getLogger("openclaw.api.export")

# Simple in-memory API key store (in production, use database)
API_KEYS = {
    os.getenv("EXPORT_API_KEY", "openclaw_export_key_demo"): {
        "name": "default",
        "rate_limit": 10,  # requests per hour
    }
}

# Rate limiting tracking
rate_limit_tracker: dict[str, list[float]] = {}


def verify_api_key(request: web.Request) -> str | None:
    """Verify API key from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header.startswith("Bearer "):
        return None
    
    api_key = auth_header[7:]  # Remove "Bearer " prefix
    
    if api_key in API_KEYS:
        return api_key
    
    return None


def check_rate_limit(api_key: str) -> bool:
    """Check if API key has exceeded rate limit."""
    now = time.time()
    hour_ago = now - 3600
    
    # Clean old entries
    if api_key in rate_limit_tracker:
        rate_limit_tracker[api_key] = [
            ts for ts in rate_limit_tracker[api_key] if ts > hour_ago
        ]
    else:
        rate_limit_tracker[api_key] = []
    
    # Check limit
    limit = API_KEYS[api_key]["rate_limit"]
    if len(rate_limit_tracker[api_key]) >= limit:
        return False
    
    # Record request
    rate_limit_tracker[api_key].append(now)
    return True


async def export_conversations_handler(request: web.Request) -> web.Response:
    """GET /api/export/conversations?format=csv&days=30"""
    # Verify API key
    api_key = verify_api_key(request)
    if not api_key:
        return web.json_response({"error": "Invalid API key"}, status=401)
    
    # Check rate limit
    if not check_rate_limit(api_key):
        return web.json_response({"error": "Rate limit exceeded"}, status=429)
    
    # Parse parameters
    format_type = request.query.get("format", "csv")
    days = int(request.query.get("days", "30"))
    channel_id = request.query.get("channel_id")
    
    filters = {}
    if channel_id:
        filters["channel_id"] = channel_id
    
    # Generate filename
    timestamp = int(time.time())
    filename = f"conversations_{timestamp}.{format_type}"
    output_path = Path("data/exports") / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Export data
    try:
        if format_type == "csv":
            from exporters import export_to_csv
            result = await export_to_csv("conversations", output_path, days=days, filters=filters)
        elif format_type == "json":
            from exporters import export_to_json
            result = await export_to_json("conversations", output_path, days=days, filters=filters)
        elif format_type == "parquet":
            from exporters import export_to_parquet
            result = await export_to_parquet("conversations", output_path, days=days, filters=filters)
        else:
            return web.json_response({"error": "Invalid format"}, status=400)
        
        if not result["success"]:
            return web.json_response({"error": result.get("error", "Export failed")}, status=500)
        
        # Schedule cleanup after 24 hours
        asyncio.create_task(_cleanup_file_after_delay(output_path, delay_hours=24))
        
        # Return file
        return web.FileResponse(
            output_path,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Export-Records": str(result.get("rows", result.get("records", 0))),
            },
        )
    
    except Exception as e:
        log.error(f"Export failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def export_trends_handler(request: web.Request) -> web.Response:
    """GET /api/export/trends?metric=stock_prices&format=parquet&days=90"""
    api_key = verify_api_key(request)
    if not api_key:
        return web.json_response({"error": "Invalid API key"}, status=401)
    
    if not check_rate_limit(api_key):
        return web.json_response({"error": "Rate limit exceeded"}, status=429)
    
    format_type = request.query.get("format", "csv")
    days = int(request.query.get("days", "30"))
    metric = request.query.get("metric")
    category = request.query.get("category")
    
    filters = {}
    if metric:
        filters["topic"] = metric
    if category:
        filters["category"] = category
    
    timestamp = int(time.time())
    filename = f"trends_{timestamp}.{format_type}"
    output_path = Path("data/exports") / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        if format_type == "csv":
            from exporters import export_to_csv
            result = await export_to_csv("trends", output_path, days=days, filters=filters)
        elif format_type == "json":
            from exporters import export_to_json
            result = await export_to_json("trends", output_path, days=days, filters=filters)
        elif format_type == "parquet":
            from exporters import export_to_parquet
            result = await export_to_parquet("trends", output_path, days=days, filters=filters)
        else:
            return web.json_response({"error": "Invalid format"}, status=400)
        
        if not result["success"]:
            return web.json_response({"error": result.get("error", "Export failed")}, status=500)
        
        asyncio.create_task(_cleanup_file_after_delay(output_path, delay_hours=24))
        
        return web.FileResponse(
            output_path,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    
    except Exception as e:
        log.error(f"Export failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def generate_report_handler(request: web.Request) -> web.Response:
    """POST /api/reports/generate"""
    api_key = verify_api_key(request)
    if not api_key:
        return web.json_response({"error": "Invalid API key"}, status=401)
    
    if not check_rate_limit(api_key):
        return web.json_response({"error": "Rate limit exceeded"}, status=429)
    
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    
    report_type = data.get("report_type", "weekly_summary")
    
    timestamp = int(time.time())
    filename = f"report_{report_type}_{timestamp}.pdf"
    output_path = Path("data/exports") / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        from report_generator import ReportGenerator
        
        generator = ReportGenerator()
        result = await generator.generate_report(
            report_type,
            output_path,
            data=data.get("data", {}),
        )
        
        if not result["success"]:
            return web.json_response({"error": result.get("error", "Report generation failed")}, status=500)
        
        asyncio.create_task(_cleanup_file_after_delay(output_path, delay_hours=24))
        
        return web.FileResponse(
            output_path,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    
    except Exception as e:
        log.error(f"Report generation failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def list_backups_handler(request: web.Request) -> web.Response:
    """GET /api/backups/list"""
    api_key = verify_api_key(request)
    if not api_key:
        return web.json_response({"error": "Invalid API key"}, status=401)
    
    try:
        from backup_manager import BackupManager
        
        manager = BackupManager()
        status = await manager.get_backup_status()
        
        # List all backups
        backups = []
        for backup in sorted(manager.backup_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            backups.append({
                "name": backup.name,
                "path": str(backup),
                "size_bytes": backup.stat().st_size if backup.is_file() else sum(
                    f.stat().st_size for f in backup.rglob("*") if f.is_file()
                ),
                "created_at": backup.stat().st_mtime,
            })
        
        return web.json_response({
            "backups": backups,
            "status": status,
        })
    
    except Exception as e:
        log.error(f"List backups failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def create_backup_handler(request: web.Request) -> web.Response:
    """POST /api/backups/create"""
    api_key = verify_api_key(request)
    if not api_key:
        return web.json_response({"error": "Invalid API key"}, status=401)
    
    if not check_rate_limit(api_key):
        return web.json_response({"error": "Rate limit exceeded"}, status=429)
    
    try:
        data = await request.json()
    except Exception:
        data = {}
    
    upload_to_nas = data.get("upload_to_nas", True)
    
    try:
        from backup_manager import backup_now
        
        result = await backup_now(upload_to_nas=upload_to_nas)
        
        if result["success"]:
            return web.json_response(result)
        else:
            return web.json_response({"error": result.get("error", "Backup failed")}, status=500)
    
    except Exception as e:
        log.error(f"Backup creation failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def _cleanup_file_after_delay(file_path: Path, delay_hours: int = 24):
    """Delete file after specified delay."""
    await asyncio.sleep(delay_hours * 3600)
    
    try:
        if file_path.exists():
            file_path.unlink()
            log.info(f"🗑️  Cleaned up export file: {file_path.name}")
    except Exception as e:
        log.warning(f"Failed to cleanup {file_path}: {e}")


def setup_export_routes(app: web.Application):
    """Register export API routes with aiohttp app."""
    app.router.add_get("/api/export/conversations", export_conversations_handler)
    app.router.add_get("/api/export/trends", export_trends_handler)
    app.router.add_post("/api/reports/generate", generate_report_handler)
    app.router.add_get("/api/backups/list", list_backups_handler)
    app.router.add_post("/api/backups/create", create_backup_handler)
    
    log.info("✅ Export API routes registered")
