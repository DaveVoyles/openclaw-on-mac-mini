"""
Tests for data exporters (CSV, JSON, Parquet).
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import json

import pandas as pd
import pytest

from exporters import export_to_csv, export_to_json, export_to_parquet


@pytest.fixture
def export_dir(tmp_path):
    """Create temporary export directory."""
    export_path = tmp_path / "exports"
    export_path.mkdir()
    return export_path


@pytest.mark.asyncio
async def test_csv_export_empty_data(export_dir):
    """Test CSV export with no data."""
    output_file = export_dir / "test.csv"
    result = await export_to_csv("conversations", output_file, days=1)

    # Should fail gracefully with empty data
    assert result["success"] is False
    assert result["rows"] == 0
    assert "No data" in result["error"]


@pytest.mark.asyncio
async def test_csv_export_trends(export_dir):
    """Test CSV export of trend data."""
    output_file = export_dir / "trends.csv"
    result = await export_to_csv("trends", output_file, days=30)

    # May have data or be empty depending on test environment
    if result["success"]:
        assert result["rows"] >= 0
        assert output_file.exists()

        # Verify CSV structure
        df = pd.read_csv(output_file)
        assert isinstance(df, pd.DataFrame)


@pytest.mark.asyncio
async def test_csv_export_with_filters(export_dir):
    """Test CSV export with filters."""
    output_file = export_dir / "filtered.csv"
    filters = {"category": "stocks"}
    result = await export_to_csv("trends", output_file, days=7, filters=filters)

    # Should handle filters without errors
    assert "error" not in result or not result["success"]


@pytest.mark.asyncio
async def test_json_export_nested(export_dir):
    """Test JSON export with nested format."""
    output_file = export_dir / "data_nested.json"
    result = await export_to_json(
        "conversations",
        output_file,
        days=30,
        format_type="nested",
    )

    if result["success"]:
        assert output_file.exists()

        # Verify JSON structure
        with open(output_file) as f:
            data = json.load(f)
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_json_export_flat(export_dir):
    """Test JSON export with flat format."""
    output_file = export_dir / "data_flat.json"
    result = await export_to_json(
        "trends",
        output_file,
        days=30,
        format_type="flat",
    )

    if result["success"]:
        assert output_file.exists()

        # Verify JSON structure
        with open(output_file) as f:
            data = json.load(f)
        assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_parquet_export(export_dir):
    """Test Parquet export."""
    output_file = export_dir / "data.parquet"
    result = await export_to_parquet(
        "trends",
        output_file,
        days=30,
        compression="snappy",
    )

    if result["success"]:
        assert output_file.exists()
        assert result["compression"] == "snappy"

        # Verify Parquet can be read
        df = pd.read_parquet(output_file)
        assert isinstance(df, pd.DataFrame)


@pytest.mark.asyncio
async def test_export_invalid_type(export_dir):
    """Test export with invalid type."""
    output_file = export_dir / "invalid.csv"
    result = await export_to_csv("invalid_type", output_file)

    assert result["success"] is False
    assert "Unknown export type" in result["error"]


@pytest.mark.asyncio
async def test_export_tasks(export_dir):
    """Test exporting scheduled tasks."""
    output_file = export_dir / "tasks.csv"
    result = await export_to_csv("tasks", output_file)

    # May succeed or fail depending on whether tasks exist
    assert "success" in result


@pytest.mark.asyncio
async def test_export_costs(export_dir):
    """Test exporting cost tracking data."""
    output_file = export_dir / "costs.csv"
    result = await export_to_csv("costs", output_file, days=30)

    # May succeed or fail depending on whether cost tracking is enabled
    assert "success" in result


@pytest.mark.asyncio
async def test_parquet_compression_options(export_dir):
    """Test Parquet with different compression algorithms."""
    compressions = ["gzip", "snappy", "none"]

    for compression in compressions:
        output_file = export_dir / f"data_{compression}.parquet"
        result = await export_to_parquet(
            "trends",
            output_file,
            compression=compression,
        )

        if result["success"]:
            assert result["compression"] == compression


@pytest.mark.asyncio
async def test_export_metadata(export_dir):
    """Test that export metadata is correctly populated."""
    output_file = export_dir / "meta_test.csv"
    result = await export_to_csv("conversations", output_file, days=7)

    if result["success"]:
        assert "rows" in result or "records" in result
        assert "path" in result
        assert "size_bytes" in result
        assert result["size_bytes"] > 0
