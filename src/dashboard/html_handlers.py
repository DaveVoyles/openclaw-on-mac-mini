"""HTML page handlers for the dashboard."""

from aiohttp import web

from .helpers import DASHBOARD_HTML, GUIDE_HTML, TERMINAL_HTML


async def dashboard_handler(request: web.Request) -> web.Response:
    """Serve the dashboard HTML page."""
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def guide_handler(request: web.Request) -> web.Response:
    """Serve the guide / tutorial HTML page."""
    return web.Response(text=GUIDE_HTML, content_type="text/html")


async def terminal_handler(request: web.Request) -> web.Response:
    """Serve the terminal CLI cheat sheet page."""
    return web.Response(text=TERMINAL_HTML, content_type="text/html")
