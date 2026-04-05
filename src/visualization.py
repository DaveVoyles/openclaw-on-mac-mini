"""
Data visualization module for financial charts

Uses Plotly for generating interactive charts that can be exported to PNG/SVG
for Discord embeds and web dashboard integration.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots

log = logging.getLogger("openclaw.visualization")

# Cache directory for generated charts
CHART_CACHE_DIR = Path("data/charts")
CHART_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Chart cache (in-memory)
_chart_cache: dict[str, tuple[Path, datetime]] = {}
CACHE_TTL_MINUTES = 30


def _get_cache_key(data: dict[str, Any], chart_type: str) -> str:
    """Generate cache key from data and chart type."""
    data_str = str(sorted(data.items()))
    return hashlib.md5(f"{chart_type}:{data_str}".encode()).hexdigest()


def _get_cached_chart(cache_key: str) -> Path | None:
    """Get cached chart path if still valid."""
    if cache_key in _chart_cache:
        chart_path, timestamp = _chart_cache[cache_key]
        if chart_path.exists():
            # Check if cache is still valid
            age_minutes = (datetime.now() - timestamp).total_seconds() / 60
            if age_minutes < CACHE_TTL_MINUTES:
                return chart_path
        # Remove stale cache entry
        del _chart_cache[cache_key]
    return None


def _save_chart(fig: go.Figure, cache_key: str, format: str = "png") -> Path:
    """Save chart to disk and cache the path."""
    chart_path = CHART_CACHE_DIR / f"{cache_key}.{format}"

    if format == "png":
        fig.write_image(str(chart_path), width=1200, height=600)
    elif format == "svg":
        fig.write_image(str(chart_path), format="svg", width=1200, height=600)
    elif format == "html":
        fig.write_html(str(chart_path))

    _chart_cache[cache_key] = (chart_path, datetime.now())
    return chart_path


def create_stock_chart(
    data: dict[str, Any],
    chart_type: str = "candlestick",
    format: str = "png"
) -> dict[str, Any]:
    """
    Create a stock price chart (candlestick or line).

    Args:
        data: Stock data from get_stock_history
            {
                "ticker": "AAPL",
                "data": [
                    {"date": "2024-01-15", "open": 173.50, "high": 176.20,
                     "low": 172.80, "close": 175.43, "volume": 82345678},
                    ...
                ]
            }
        chart_type: "candlestick" or "line" (default: "candlestick")
        format: "png", "svg", or "html" (default: "png")

    Returns:
        {
            "status": "ok",
            "chart_path": "/tmp/openclaw_charts/abc123.png",
            "chart_type": "candlestick",
            "ticker": "AAPL",
            "cached": False
        }
    """
    try:
        # Check cache
        cache_key = _get_cache_key(data, f"{chart_type}_{format}")
        cached_path = _get_cached_chart(cache_key)
        if cached_path:
            return {
                "status": "ok",
                "chart_path": str(cached_path),
                "chart_type": chart_type,
                "ticker": data.get("ticker", "Unknown"),
                "cached": True,
            }

        ticker = data.get("ticker", "Unknown")
        history = data.get("data", [])

        if not history:
            return {
                "status": "error",
                "message": "No data provided for chart",
            }

        dates = [item["date"] for item in history]
        opens = [item["open"] for item in history]
        highs = [item["high"] for item in history]
        lows = [item["low"] for item in history]
        closes = [item["close"] for item in history]
        volumes = [item["volume"] for item in history]

        # Create figure with secondary y-axis for volume
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            row_heights=[0.7, 0.3],
            subplot_titles=(f"{ticker} Price", "Volume")
        )

        # Add price chart
        if chart_type == "candlestick":
            fig.add_trace(
                go.Candlestick(
                    x=dates,
                    open=opens,
                    high=highs,
                    low=lows,
                    close=closes,
                    name="Price"
                ),
                row=1, col=1
            )
        else:  # line chart
            fig.add_trace(
                go.Scatter(
                    x=dates,
                    y=closes,
                    mode="lines",
                    name="Close Price",
                    line=dict(color="#00D9FF", width=2)
                ),
                row=1, col=1
            )

        # Add volume bars
        colors = ['red' if closes[i] < opens[i] else 'green'
                  for i in range(len(closes))]
        fig.add_trace(
            go.Bar(
                x=dates,
                y=volumes,
                name="Volume",
                marker_color=colors,
                opacity=0.5
            ),
            row=2, col=1
        )

        # Update layout
        fig.update_layout(
            title=f"{ticker} Stock Chart",
            xaxis_rangeslider_visible=False,
            template="plotly_dark",
            showlegend=False,
            height=600,
            margin=dict(l=50, r=50, t=80, b=50)
        )

        fig.update_xaxes(title_text="Date", row=2, col=1)
        fig.update_yaxes(title_text="Price ($)", row=1, col=1)
        fig.update_yaxes(title_text="Volume", row=2, col=1)

        # Save chart
        chart_path = _save_chart(fig, cache_key, format)

        return {
            "status": "ok",
            "chart_path": str(chart_path),
            "chart_type": chart_type,
            "ticker": ticker,
            "cached": False,
        }

    except Exception as e:
        log.error(f"Error creating stock chart: {e}")
        return {
            "status": "error",
            "message": f"Failed to create chart: {str(e)}",
        }


def create_trend_chart(
    data: dict[str, Any],
    format: str = "png"
) -> dict[str, Any]:
    """
    Create a trend visualization chart.

    Args:
        data: Trend data
            {
                "ticker": "AAPL",
                "data": [
                    {"date": "2024-01-15", "value": 175.43},
                    ...
                ]
            }
        format: "png", "svg", or "html" (default: "png")

    Returns:
        {
            "status": "ok",
            "chart_path": "/tmp/openclaw_charts/abc123.png",
            "ticker": "AAPL"
        }
    """
    try:
        # Check cache
        cache_key = _get_cache_key(data, f"trend_{format}")
        cached_path = _get_cached_chart(cache_key)
        if cached_path:
            return {
                "status": "ok",
                "chart_path": str(cached_path),
                "ticker": data.get("ticker", "Unknown"),
                "cached": True,
            }

        ticker = data.get("ticker", "Unknown")
        history = data.get("data", [])

        if not history:
            return {
                "status": "error",
                "message": "No data provided for trend chart",
            }

        dates = [item["date"] for item in history]
        values = [item.get("value") or item.get("close", 0) for item in history]

        # Calculate trend line (simple linear regression)
        n = len(values)
        if n > 1:
            x_vals = list(range(n))
            x_mean = sum(x_vals) / n
            y_mean = sum(values) / n

            numerator = sum((x_vals[i] - x_mean) * (values[i] - y_mean) for i in range(n))
            denominator = sum((x_vals[i] - x_mean) ** 2 for i in range(n))

            if denominator != 0:
                slope = numerator / denominator
                intercept = y_mean - slope * x_mean
                trend_line = [slope * x + intercept for x in x_vals]
            else:
                trend_line = values
        else:
            trend_line = values

        # Create figure
        fig = go.Figure()

        # Add actual values
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=values,
                mode="lines+markers",
                name="Actual",
                line=dict(color="#00D9FF", width=2),
                marker=dict(size=6)
            )
        )

        # Add trend line
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=trend_line,
                mode="lines",
                name="Trend",
                line=dict(color="#FF6B6B", width=2, dash="dash")
            )
        )

        # Update layout
        fig.update_layout(
            title=f"{ticker} Trend Analysis",
            xaxis_title="Date",
            yaxis_title="Value",
            template="plotly_dark",
            height=600,
            margin=dict(l=50, r=50, t=80, b=50),
            hovermode="x unified"
        )

        # Save chart
        chart_path = _save_chart(fig, cache_key, format)

        return {
            "status": "ok",
            "chart_path": str(chart_path),
            "ticker": ticker,
            "cached": False,
        }

    except Exception as e:
        log.error(f"Error creating trend chart: {e}")
        return {
            "status": "error",
            "message": f"Failed to create trend chart: {str(e)}",
        }


def create_comparison_chart(
    data: dict[str, Any],
    format: str = "png"
) -> dict[str, Any]:
    """
    Create a multi-asset comparison chart.

    Args:
        data: Comparison data
            {
                "assets": [
                    {
                        "ticker": "AAPL",
                        "data": [
                            {"date": "2024-01-15", "value": 175.43},
                            ...
                        ]
                    },
                    {
                        "ticker": "MSFT",
                        "data": [...]
                    }
                ]
            }
        format: "png", "svg", or "html" (default: "png")

    Returns:
        {
            "status": "ok",
            "chart_path": "/tmp/openclaw_charts/abc123.png",
            "assets": ["AAPL", "MSFT"]
        }
    """
    try:
        # Check cache
        cache_key = _get_cache_key(data, f"comparison_{format}")
        cached_path = _get_cached_chart(cache_key)
        if cached_path:
            asset_list = [asset["ticker"] for asset in data.get("assets", [])]
            return {
                "status": "ok",
                "chart_path": str(cached_path),
                "assets": asset_list,
                "cached": True,
            }

        assets = data.get("assets", [])

        if not assets:
            return {
                "status": "error",
                "message": "No assets provided for comparison",
            }

        # Create figure
        fig = go.Figure()

        # Color palette
        colors = ["#00D9FF", "#FF6B6B", "#4ECDC4", "#FFE66D", "#A8DADC", "#E63946"]

        # Add each asset as a line
        for i, asset in enumerate(assets):
            ticker = asset["ticker"]
            history = asset["data"]

            if not history:
                continue

            dates = [item["date"] for item in history]
            values = [item.get("value") or item.get("close", 0) for item in history]

            # Normalize to percentage change from first value
            if values and values[0] != 0:
                first_value = values[0]
                normalized_values = [(v / first_value - 1) * 100 for v in values]
            else:
                normalized_values = values

            fig.add_trace(
                go.Scatter(
                    x=dates,
                    y=normalized_values,
                    mode="lines",
                    name=ticker,
                    line=dict(color=colors[i % len(colors)], width=2)
                )
            )

        # Update layout
        fig.update_layout(
            title="Asset Comparison (% Change)",
            xaxis_title="Date",
            yaxis_title="% Change from Start",
            template="plotly_dark",
            height=600,
            margin=dict(l=50, r=50, t=80, b=50),
            hovermode="x unified",
            legend=dict(
                yanchor="top",
                y=0.99,
                xanchor="left",
                x=0.01
            )
        )

        # Add horizontal line at 0%
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)

        # Save chart
        chart_path = _save_chart(fig, cache_key, format)

        asset_list = [asset["ticker"] for asset in assets]
        return {
            "status": "ok",
            "chart_path": str(chart_path),
            "assets": asset_list,
            "cached": False,
        }

    except Exception as e:
        log.error(f"Error creating comparison chart: {e}")
        return {
            "status": "error",
            "message": f"Failed to create comparison chart: {str(e)}",
        }


def clear_chart_cache() -> dict[str, Any]:
    """
    Clear all cached charts.

    Returns:
        {
            "status": "ok",
            "cleared": 5
        }
    """
    try:
        count = 0
        for chart_path, _ in _chart_cache.values():
            if chart_path.exists():
                chart_path.unlink()
                count += 1

        _chart_cache.clear()

        return {
            "status": "ok",
            "cleared": count,
        }
    except Exception as e:
        log.error(f"Error clearing chart cache: {e}")
        return {
            "status": "error",
            "message": str(e),
        }
