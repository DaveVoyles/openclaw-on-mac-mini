"""
Parquet Exporter — Export large datasets to Parquet format.
Optimized for large-scale data analytics and archival.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger("openclaw.exporters.parquet")

ExportType = Literal["conversations", "trends", "tasks", "costs", "api_usage"]


async def export_to_parquet(
    export_type: ExportType,
    output_path: Path | str,
    *,
    days: int | None = None,
    filters: dict[str, Any] | None = None,
    compression: str = "snappy",
) -> dict[str, Any]:
    """
    Export data to Parquet format.

    Args:
        export_type: Type of data to export
        output_path: Path where Parquet file will be saved
        days: Number of days to include (optional)
        filters: Additional filters
        compression: Compression algorithm (snappy, gzip, brotli, none)

    Returns:
        dict with {"success": bool, "rows": int, "path": str, "error": str}
    """
    output_path = Path(output_path)
    filters = filters or {}

    try:
        # Reuse CSV export logic
        from .csv_exporter import (
            _export_conversations,
            _export_trends,
            _export_tasks,
            _export_costs,
            _export_api_usage,
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
            return {"success": False, "error": "No data to export", "rows": 0}

        # Write to Parquet with compression
        table = pa.Table.from_pandas(df)
        pq.write_table(table, output_path, compression=compression)

        log.info(f"✅ Exported {len(df)} rows to {output_path} (compression: {compression})")
        return {
            "success": True,
            "rows": len(df),
            "path": str(output_path),
            "size_bytes": output_path.stat().st_size,
            "compression": compression,
        }

    except Exception as e:
        log.error(f"Parquet export failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
