"""
JSON Exporter — Export data to JSON format.
Supports both nested and flat JSON structures.
"""

import json
import logging
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

ExportType = Literal["conversations", "trends", "tasks", "costs", "api_usage"]
FormatType = Literal["nested", "flat"]


async def export_to_json(
    export_type: ExportType,
    output_path: Path | str,
    *,
    days: int | None = None,
    filters: dict[str, Any] | None = None,
    format_type: FormatType = "nested",
    indent: int = 2,
) -> dict[str, Any]:
    """
    Export data to JSON format.

    Args:
        export_type: Type of data to export
        output_path: Path where JSON file will be saved
        days: Number of days to include (optional)
        filters: Additional filters
        format_type: "nested" or "flat" structure
        indent: JSON indentation (None for compact)

    Returns:
        dict with {"success": bool, "records": int, "path": str, "error": str}
    """
    output_path = Path(output_path)
    filters = filters or {}

    try:
        # Reuse CSV export logic but convert to JSON
        from .csv_exporter import (
            _export_api_usage,
            _export_conversations,
            _export_costs,
            _export_tasks,
            _export_trends,
        )

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
            return {"success": False, "error": "No data to export", "records": 0}

        # Convert to JSON
        if format_type == "nested":
            data = df.to_dict(orient="records")
        else:  # flat
            data = df.to_dict(orient="list")

        # Write to file
        with open(output_path, "w") as f:
            json.dump(data, f, indent=indent, default=str)

        log.info(f"✅ Exported {len(df)} records to {output_path}")
        return {
            "success": True,
            "records": len(df),
            "path": str(output_path),
            "size_bytes": output_path.stat().st_size,
            "format": format_type,
        }

    except Exception as e:  # broad: needs-review
        return {"success": False, "error": str(e)}
