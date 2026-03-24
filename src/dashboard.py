"""
OpenClaw Dashboard — lightweight HTML dashboard served on the health endpoint.
Routes: GET /dashboard (HTML), GET /api/dashboard (JSON)
"""

import platform
import time

import discord
import yaml
from aiohttp import web
from pathlib import Path

from spending import tracker as spending_tracker

CONFIG_DIR = Path("/config")
GITHUB_REPO = "https://github.com/DaveVoyles/openclaw-on-mac-mini"
VERSION = "0.5.0"

_config_cache: dict | None = None
_config_mtime: float = 0.0


def _load_config() -> dict:
    global _config_cache, _config_mtime
    cfg_file = CONFIG_DIR / "config.yaml"
    try:
        current_mtime = cfg_file.stat().st_mtime if cfg_file.exists() else 0.0
    except OSError:
        current_mtime = 0.0
    if _config_cache is not None and current_mtime == _config_mtime:
        return _config_cache
    _config_cache = yaml.safe_load(cfg_file.read_text()) if cfg_file.exists() else {}
    _config_mtime = current_mtime
    return _config_cache or {}


async def api_dashboard_handler(request: web.Request) -> web.Response:
    """JSON blob with all dashboard data."""
    bot = request.app.get("bot")
    uptime_s = time.monotonic() - bot.start_time if bot else 0

    from skills import SKILLS, list_containers, get_docker_stats, get_system_stats
    from ontology_skills import ontology_query
    from llm import _TOOL_DECLARATIONS, get_rate_info, MODEL_NAME, OLLAMA_MODEL, LOCAL_LLM_ENABLED

    # Get container status list
    container_text = await list_containers()
    containers = []
    if not container_text.startswith("\u274c"):
        # The output uses multiple spaces instead of tabs sometimes. Split by 2+ spaces.
        import re
        lines = [line.strip() for line in container_text.split("\n") if line.strip() and not line.startswith("NAMES")]
        for line in lines:
            # Match Names (first col), Status (middle), ignore Ports (last)
            # Docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
            # But skills/__init__.py uses actual tab characters (\t) in the format string.
            # Let's check if it's splitting correctly.
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if not parts or len(parts) < 2:
                # Fallback to regex split if tabs aren't present
                parts = [p.strip() for p in re.split(r'\s{2,}', line) if p.strip()]

            if len(parts) >= 2:
                name = parts[0]
                status = parts[1]
                # Map status to health color/icon
                is_up = "Up" in status
                containers.append({
                    "name": name,
                    "status": status,
                    "is_up": is_up
                })

    # Get resource stats
    stats_text = await get_docker_stats()
    stats_list = []
    if not stats_text.startswith("\u274c"):
        # Parse table Format: NAME CPU% MEM NET
        stat_lines = [l.strip() for l in stats_text.split("\n") if l.strip() and not l.startswith("NAME")]
        for sl in stat_lines:
            parts = [p.strip() for p in sl.split("\t") if p.strip()]
            if not parts or len(parts) < 2:
                # Fallback to regex split if tabs aren't present
                import re
                parts = [p.strip() for p in re.split(r'\s{2,}', sl) if p.strip()]

            if len(parts) >= 2:
                stats_list.append({
                    "name": parts[0],
                    "cpu": parts[1] if len(parts) > 1 else "?",
                    "mem": parts[2] if len(parts) > 2 else "?",
                })

    # Get server system stats (CPU/MEM/Disk)
    sys_stats_text = await get_system_stats()
    # Format: **CPU**: 10.5% (8 cores)\n**Memory**: 4.2 / 16.0 GB (26.3%)\n**Disk** `/`: 200GB used / 500GB total (40%)
    sys_stats = {"cpu": "N/A", "mem": "N/A", "disk": "N/A"}
    for line in sys_stats_text.split("\n"):
        if "**CPU**" in line: sys_stats["cpu"] = line.split(":", 1)[1].strip()
        elif "Average" in line: sys_stats["cpu"] = line.split(":", 1)[1].strip() # Fallback for Load Avg
        elif "**Memory**" in line: sys_stats["mem"] = line.split(":", 1)[1].strip()
        elif "**Disk**" in line: sys_stats["disk"] = line.split(":", 1)[1].strip()

    # Get ontology facts (limit to recent 5)
    ontology_text = await ontology_query()
    ontology_facts = []
    if not ontology_text.startswith("❌") and "Found" in ontology_text:
        # Simple extraction of bullets
        fact_lines = [l.strip("• ").strip() for l in ontology_text.split("\n") if l.strip().startswith("•")]
        ontology_facts = fact_lines[:8]

    cfg = _load_config()
    sp = spending_tracker

    skills_list = []
    # Build skills from tool declarations (has descriptions)
    decl_map = {d["name"]: d.get("description", "") for d in _TOOL_DECLARATIONS}
    for name in sorted(SKILLS.keys()):
        skills_list.append({
            "name": name,
            "description": decl_map.get(name, getattr(SKILLS[name], "__doc__", "") or ""),
        })

    payload = {
        "version": VERSION,
        "uptime_seconds": round(uptime_s, 1),
        "bot_user": str(bot.user) if bot and bot.user else None,
        "guilds": len(bot.guilds) if bot else 0,
        "latency_ms": round(bot.latency * 1000, 1) if bot and bot.latency else 0,
        "python": platform.python_version(),
        "discord_py": discord.__version__,
        "model": MODEL_NAME,
        "local_model": OLLAMA_MODEL if LOCAL_LLM_ENABLED else None,
        "rate_info": get_rate_info(),
        "github_repo": GITHUB_REPO,
        "containers": containers,
        "stats": stats_list,
        "sys_stats": sys_stats,
        "ontology": ontology_facts,
        "config": {
            "llm": cfg.get("llm", {}),
            "security": cfg.get("security", {}),
            "phase": cfg.get("phase", "?"),
        },
        "spending": {
            "total_cost": round(sp.total_cost, 6),
            "budget_limit": sp.budget_limit,
            "budget_remaining": round(sp.budget_remaining, 6),
            "budget_pct": round(sp.budget_pct_used, 2),
            "total_input_tokens": sp.total_input_tokens,
            "total_output_tokens": sp.total_output_tokens,
            "calls": sp.calls,
            "daily": sp.daily,
        },
        "skills": skills_list,
        "skill_count": len(skills_list),
        "commands": _command_list(),
    }
    return web.json_response(payload)


def _command_list() -> list[dict]:
    """Static command reference grouped by category."""
    return [
        {"category": "🏛️ Foundation", "commands": [
            {"name": "/ping", "desc": "Check if bot is alive"},
            {"name": "/about", "desc": "Version and system info"},
            {"name": "/whoami", "desc": "Your Discord identity & permissions"},
            {"name": "/help", "desc": "List all commands"},
        ]},
        {"category": "🐳 Docker & System", "commands": [
            {"name": "/containers", "desc": "List running containers"},
            {"name": "/status <service>", "desc": "Container detail + resources"},
            {"name": "/logs <service> [lines]", "desc": "View container logs"},
            {"name": "/system", "desc": "CPU, memory, disk usage"},
            {"name": "/dockerstats", "desc": "Per-container resource usage"},
            {"name": "/restart <service>", "desc": "Restart a container (requires approval)"},
        ]},
        {"category": "🤖 AI & LLM", "commands": [
            {"name": "/ask <question>", "desc": "AI-powered query — Gemini (tools) or Ollama (chat). Weather/live-data queries auto-route to Gemini."},
            {"name": "/research <query>", "desc": "Deep multi-step research — Discord thread, planned sub-queries, 3-tier search (Tavily → DDG → Bing), source browsing, synthesized report"},
            {"name": "/weather [location]", "desc": "Current conditions + 3-day forecast for any location (default: WEATHER_DEFAULT_LOCATION env var)"},
            {"name": "/clear", "desc": "Clear active conversation history"},
            {"name": "/save <name>", "desc": "Save current conversation as a named thread (persisted to disk)"},
            {"name": "/resume <name>", "desc": "Resume a previously saved conversation thread"},
            {"name": "/threads", "desc": "List all your saved conversation threads"},
            {"name": "/forget <name>", "desc": "Delete a saved conversation thread"},
            {"name": "/analyze <service> [lines]", "desc": "AI log analysis"},
        ]},
        {"category": "🎬 Media & Downloads", "commands": [
            {"name": "/search <query> [type]", "desc": "Search Sonarr/Radarr catalogs"},
            {"name": "/queue", "desc": "Active downloads (SABnzbd + qBit)"},
            {"name": "/recent [count]", "desc": "Recently added Plex media"},
            {"name": "/health", "desc": "Check *arr + download client health"},
            {"name": "/ports", "desc": "Service port connectivity check"},
            {"name": "/report", "desc": "Comprehensive status report"},
        ]},
        {"category": "🧠 Memory & Automation", "commands": [
            {"name": "/remember <fact> [tags]", "desc": "Store a fact in long-term memory"},
            {"name": "/recall <query>", "desc": "Search long-term memory"},
            {"name": "/schedule", "desc": "Manage scheduled tasks"},
            {"name": "/skills", "desc": "List all LLM-callable skills"},
        ]},
        {"category": "🌐 Network & Monitoring", "commands": [
            {"name": "/network", "desc": "LAN, internet, DNS connectivity"},
            {"name": "/tailscale", "desc": "Tailscale VPN status"},
            {"name": "/speedtest", "desc": "Network speed test"},
            {"name": "/spending [breakdown]", "desc": "Gemini API cost tracking"},
        ]},
        {"category": "Security & Admin", "commands": [
            {"name": "/pending", "desc": "Pending approval requests"},
            {"name": "/auditlog [lines]", "desc": "View audit trail"},
            {"name": "/estop [stop|resume]", "desc": "Emergency stop all actions"},
            {"name": "/mail <to> <subject> <body>", "desc": "Send email via AgentMail"},
        ]},
        {"category": "Third-Party API Gateway (via /ask)", "commands": [
            {"name": "gateway_request", "desc": "Call any of 100+ APIs (Slack, GitHub, Notion, HubSpot, Stripe…) via Maton managed OAuth. Invoked by /ask."},
            {"name": "gateway_list_connections", "desc": "List active Maton OAuth connections (optionally filter by app). Invoked by /ask."},
            {"name": "gateway_create_connection", "desc": "Create a new Maton OAuth connection for an app and return the authorization URL. Invoked by /ask."},
        ]},
        {"category": "Knowledge Graph & Ontology (via /ask)", "commands": [
            {"name": "ontology_create_entity", "desc": "Create a new typed entity (Person, Project, Task, etc.) in graph memory."},
            {"name": "ontology_get_entity", "desc": "Retrieve all details and relations for a specific entity name."},
            {"name": "ontology_relate", "desc": "Create a typed link between two entities (e.g., 'blocks', 'manages')."},
            {"name": "ontology_query", "desc": "Search the knowledge graph for entities by name or type."},
        ]},
        {"category": "Self-Management & Autonomy (via /ask)", "commands": [
            {"name": "webfetch_md", "desc": "Smartly scrape any URL and convert main content to clean Markdown."},
            {"name": "git_status", "desc": "Check project repository status for code changes."},
            {"name": "git_log", "desc": "View recent code change history (commit log)."},
            {"name": "git_diff", "desc": "Compare code changes or view uncommitted changes."},
            {"name": "git_commit", "desc": "Commit all current changes with a brief summary message."},
            {"name": "init_planning_files", "desc": "Initialize task_plan.md, findings.md, progress.md for complex tasks."},
            {"name": "update_plan_status", "desc": "Log progress or update status of a phase in planning files."},
        ]},
    ]


# ---------------------------------------------------------------------------
# HTML dashboard (self-contained, no external deps)
# ---------------------------------------------------------------------------


async def dashboard_handler(request: web.Request) -> web.Response:
    """Serve the dashboard HTML page."""
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def guide_handler(request: web.Request) -> web.Response:
    """Serve the guide / tutorial HTML page."""
    return web.Response(text=GUIDE_HTML, content_type="text/html")


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw Dashboard</title>
<style>
  :root {
    /* JHU Primary */
    --heritage-blue: #002D72;
    --spirit-blue: #68ACE5;
    /* JHU Cool Accents */
    --medium-blue: #0077D8;
    --harbor-blue: #4E97E0;
    --mint-green: #86C8BC;
    --homewood-green: #008767;
    --forest-green: #275E3D;
    --lavender: #9E8FB0;
    --plum: #51284F;
    /* JHU Warm Accents */
    --gold: #F1C400;
    --orange: #FF9E1B;
    --red: #CF4520;
    --dark-red: #A6192E;
    /* JHU Grayscale */
    --sable: #31261D;
    --white: #FFFFFF;
    --black: #000000;

    /* Semantic mapping */
    --bg: #001233;           /* deep navy (darker than heritage for bg) */
    --surface: rgba(255, 255, 255, 0.08); /* neutral light frost for better clarity */
    --surface-solid: #001e4d;
    --border: rgba(104, 172, 229, 0.35);
    --text: #ffffff;         /* pure white for maximum legibility */
    --muted: #b0d4ff;        /* very light sky blue for muted text */
    --accent: var(--spirit-blue);
    --accent2: var(--gold);
    --green: var(--homewood-green);
    --yellow: var(--gold);
    --warn: var(--orange);
    --danger: var(--red);
    --glow: rgba(104, 172, 229, 0.15);
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.6;
    min-height: 100vh; overflow-x: hidden;
  }

  /* WebGL canvas background */
  #gl-canvas {
    position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    z-index: 0; pointer-events: none;
  }

  .container { position: relative; z-index: 1; max-width: 1200px; margin: 0 auto; padding: 1.5rem; }

  a { color: var(--spirit-blue); text-decoration: none; transition: color 0.2s; }
  a:hover { color: var(--gold); }

  h1 { font-size: 1.8rem; font-weight: 700; letter-spacing: -0.02em; }
  h2 {
    font-size: 1rem; font-weight: 600; color: var(--gold);
    margin-bottom: 0.75rem; padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--border);
    text-transform: uppercase; letter-spacing: 0.05em;
  }

  /* Header */
  .header {
    display: flex; align-items: center; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap;
    padding-bottom: 1rem; border-bottom: 1px solid var(--border);
  }
  .header-title { display: flex; align-items: center; gap: 0.75rem; }
  .header-title .logo {
    width: 44px; height: 44px; border-radius: 12px;
    background: linear-gradient(135deg, var(--heritage-blue), var(--medium-blue));
    display: flex; align-items: center; justify-content: center; font-size: 1.5rem;
    box-shadow: 0 0 20px rgba(0,119,216,0.3);
  }
  .header-right { margin-left: auto; display: flex; gap: 0.5rem; align-items: center; }

  /* Badges */
  .badge {
    display: inline-flex; align-items: center; gap: 0.3rem;
    padding: 0.25rem 0.7rem; border-radius: 2rem; font-size: 0.72rem;
    font-weight: 600; letter-spacing: 0.02em;
  }
  .badge-green { background: rgba(0,135,103,0.2); color: var(--mint-green); border: 1px solid rgba(0,135,103,0.3); }
  .badge-blue { background: rgba(104,172,229,0.15); color: var(--spirit-blue); border: 1px solid rgba(104,172,229,0.2); }
  .badge-gold { background: rgba(241,196,0,0.12); color: var(--gold); border: 1px solid rgba(241,196,0,0.2); }
  .badge-dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
  .badge-green .badge-dot { background: var(--mint-green); box-shadow: 0 0 6px var(--mint-green); }

  /* Buttons */
  .btn-row { display: flex; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
  .btn {
    display: inline-flex; align-items: center; gap: 0.4rem;
    padding: 0.5rem 1rem; border-radius: 0.5rem; font-size: 0.82rem;
    font-weight: 600; cursor: pointer; transition: all 0.2s;
    border: 1px solid var(--border); color: var(--text);
    background: var(--surface);
    backdrop-filter: blur(8px); text-decoration: none;
  }
  .btn:hover {
    background: rgba(0,119,216,0.25); border-color: var(--spirit-blue);
    transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,119,216,0.2);
    color: var(--white); text-decoration: none;
  }
  .btn-primary {
    background: linear-gradient(135deg, var(--heritage-blue), var(--medium-blue));
    border-color: var(--medium-blue);
  }
  .btn-primary:hover {
    background: linear-gradient(135deg, var(--medium-blue), var(--harbor-blue));
    box-shadow: 0 4px 16px rgba(0,119,216,0.35);
  }
  .btn-gold { border-color: rgba(241,196,0,0.3); color: var(--gold); }
  .btn-gold:hover { background: rgba(241,196,0,0.15); border-color: var(--gold); }
  .btn-refresh { border-color: rgba(134,200,188,0.3); color: var(--mint-green); }
  .btn-refresh:hover { background: rgba(0,135,103,0.15); border-color: var(--mint-green); }
  .btn-refresh.spinning .btn-icon { animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Grid */
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 1rem; margin-bottom: 1.25rem; }

  /* Cards */
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 0.75rem; padding: 1.25rem;
    backdrop-filter: blur(12px);
    transition: border-color 0.2s, box-shadow 0.2s;
    margin-bottom: 1.25rem;
  }
  .card:hover { border-color: rgba(104,172,229,0.35); box-shadow: 0 0 20px var(--glow); }

  /* Stat rows */
  .stat-row { display: flex; justify-content: space-between; padding: 0.4rem 0; border-bottom: 1px solid rgba(104,172,229,0.08); }
  .stat-row:last-child { border-bottom: none; }
  .stat-label { color: var(--muted); font-size: 0.85rem; }
  .stat-value { font-weight: 600; font-variant-numeric: tabular-nums; font-size: 0.85rem; }

  /* Progress bar */
  .progress-bar {
    width: 100%; height: 1.4rem; border-radius: 0.7rem; overflow: hidden;
    margin: 0.6rem 0; background: rgba(0,45,114,0.5);
    box-shadow: inset 0 1px 3px rgba(0,0,0,0.3);
  }
  .progress-fill {
    height: 100%; border-radius: 0.7rem; transition: width 0.8s ease;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.68rem; font-weight: 700; color: var(--white);
    text-shadow: 0 1px 2px rgba(0,0,0,0.3);
  }
  .pf-ok { background: linear-gradient(90deg, var(--homewood-green), var(--mint-green)); }
  .pf-warn { background: linear-gradient(90deg, var(--gold), var(--orange)); }
  .pf-danger { background: linear-gradient(90deg, var(--red), var(--dark-red)); }

  /* Health Matrix */
  .health-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 0.75rem;
    margin-top: 0.5rem;
  }
  .health-item {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.6rem 0.75rem;
    background: rgba(104, 172, 229, 0.03);
    border: 1px solid rgba(104, 172, 229, 0.1);
    border-radius: 0.5rem;
    font-size: 0.8rem;
    transition: all 0.2s;
  }
  .health-item:hover {
    background: rgba(104, 172, 229, 0.08);
    border-color: rgba(104, 172, 229, 0.25);
    transform: translateY(-1px);
  }
  .health-status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .health-status-up { background: var(--mint-green); box-shadow: 0 0 8px var(--mint-green); }
  .health-status-down { background: var(--red); box-shadow: 0 0 8px var(--red); }
  .health-name {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text);
    font-weight: 500;
  }

  /* Tables */
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th {
    text-align: left; padding: 0.5rem 0.6rem;
    border-bottom: 2px solid rgba(104,172,229,0.2);
    color: var(--gold); font-weight: 600; font-size: 0.75rem;
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  td { padding: 0.45rem 0.6rem; border-bottom: 1px solid rgba(104,172,229,0.06); }
  tr:hover td { background: rgba(0,119,216,0.06); }
  .cmd-cat {
    color: var(--spirit-blue); font-weight: 700; padding-top: 0.85rem;
    font-size: 0.78rem; letter-spacing: 0.03em;
  }
  code {
    background: rgba(0,119,216,0.12); color: var(--spirit-blue);
    padding: 0.15rem 0.45rem; border-radius: 0.3rem; font-size: 0.8rem;
    font-family: 'SF Mono', 'Fira Code', monospace;
  }

  /* Daily chart bars */
  .chart-bar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem; }
  .chart-bar-inner {
    height: 1.1rem; border-radius: 0.3rem; min-width: 3px;
    background: linear-gradient(90deg, var(--medium-blue), var(--spirit-blue));
    transition: width 0.5s ease; box-shadow: 0 0 8px rgba(104,172,229,0.2);
  }
  .chart-label { font-size: 0.75rem; color: var(--muted); min-width: 4.5rem; font-variant-numeric: tabular-nums; }
  .chart-value { font-size: 0.73rem; font-variant-numeric: tabular-nums; color: var(--spirit-blue); }

  /* Skill search */
  .search-box {
    width: 100%; padding: 0.5rem 0.75rem; margin-bottom: 0.75rem;
    background: rgba(0,45,114,0.4); border: 1px solid var(--border);
    border-radius: 0.4rem; color: var(--text); font-size: 0.85rem;
    outline: none; transition: border-color 0.2s;
  }
  .search-box:focus { border-color: var(--spirit-blue); box-shadow: 0 0 0 3px rgba(104,172,229,0.15); }
  .search-box::placeholder { color: var(--muted); }

  /* Loading */
  #loading {
    text-align: center; padding: 4rem; color: var(--muted);
    font-size: 1.1rem;
  }
  .loader {
    width: 36px; height: 36px; border: 3px solid var(--border);
    border-top-color: var(--spirit-blue); border-radius: 50%;
    animation: spin 0.8s linear infinite; margin: 0 auto 1rem;
  }

  /* Footer */
  .footer { text-align: center; color: var(--muted); font-size: 0.75rem; padding: 1.5rem 0 0.5rem; border-top: 1px solid var(--border); margin-top: 1rem; }

  @media (max-width: 600px) { .grid { grid-template-columns: 1fr; } .container { padding: 0.75rem; } }
</style>
</head>
<body>
<canvas id="gl-canvas"></canvas>
<div class="container">

<div id="loading"><div class="loader"></div>Connecting to OpenClaw&hellip;</div>
<div id="app" style="display:none">

  <!-- Header -->
  <div class="header">
    <div class="header-title">
      <div class="logo"></div>
      <div>
        <h1>Dave's OpenClaw on a Mac Mini</h1>
        <span id="subtitle" class="badge badge-blue" style="margin-top:2px"></span>
      </div>
    </div>
    <div class="header-right">
      <span id="status-badge" class="badge badge-green"><span class="badge-dot"></span> Online</span>
      <span id="uptime-badge" class="badge badge-gold"></span>
    </div>
  </div>

  <!-- Buttons -->
  <div class="btn-row">
    <a id="link-github" class="btn btn-primary" href="#" target="_blank"><span class="btn-icon">&#128279;</span> GitHub</a>
    <a class="btn btn-gold" href="https://davevoyles.github.io/openclaw-dashboard/" target="_blank" rel="noopener"><span class="btn-icon">&#127918;</span> Mission Control</a>
    <a class="btn btn-gold" href="https://homepage.davevoyles.synology.me/" target="_blank" rel="noopener"><span class="btn-icon">&#127968;</span> Synology NAS</a>
    <a class="btn" href="/guide" target="_blank"><span class="btn-icon">&#128218;</span> Guide</a>
    <button class="btn btn-refresh" onclick="refreshData(this)"><span class="btn-icon">&#8635;</span> Refresh</button>
  </div>

  <!-- Container Health Matrix -->
  <div class="card">
    <h2>&#128373; Container Health Status</h2>
    <div id="container-grid" class="health-grid">
      <div style="color:var(--muted);font-style:italic">Loading container status...</div>
    </div>
  </div>

  <!-- Mac Mini Resource Usage -->
  <div class="card" id="mac-mini-usage" style="display:none">
    <h2>&#128187; Mac Mini Resource Usage</h2>
    <div class="grid" style="grid-template-columns: repeat(3, 1fr); margin-top: 0.5rem; gap: 1rem;">
      <div class="stat-item" style="text-align:center; background: rgba(104, 172, 229, 0.05); padding: 1rem; border-radius: 0.5rem; border: 1px solid var(--border);">
        <div style="font-size: 0.7rem; color: var(--gold); text-transform: uppercase; margin-bottom: 0.25rem;">CPU Usage</div>
        <div id="server-cpu" style="font-size: 1.1rem; font-weight: 700;">--</div>
      </div>
      <div class="stat-item" style="text-align:center; background: rgba(104, 172, 229, 0.05); padding: 1rem; border-radius: 0.5rem; border: 1px solid var(--border);">
        <div style="font-size: 0.7rem; color: var(--spirit-blue); text-transform: uppercase; margin-bottom: 0.25rem;">Memory</div>
        <div id="server-mem" style="font-size: 1.1rem; font-weight: 700;">--</div>
      </div>
      <div class="stat-item" style="text-align:center; background: rgba(104, 172, 229, 0.05); padding: 1rem; border-radius: 0.5rem; border: 1px solid var(--border);">
        <div style="font-size: 0.7rem; color: var(--mint-green); text-transform: uppercase; margin-bottom: 0.25rem;">Storage (Root)</div>
        <div id="server-disk" style="font-size: 1.1rem; font-weight: 700;">--</div>
      </div>
    </div>
  </div>

  <!-- Ontology Browser / Facts -->
  <div class="card">
    <h2>&#129504; Long-Term Memory (Ontology)</h2>
    <div id="ontology-list">
      <div style="color:var(--muted);font-style:italic">Loading remembered facts...</div>
    </div>
    <p style="margin-top:1rem;font-size:0.75rem;color:var(--muted)">
      These are the durable facts OpenClaw has "remembered" about your projects, devices, and people.
      Use <code>/ask "Remember that..."</code> to add new facts.
    </p>
  </div>

  <!-- Top cards -->
  <div class="grid">
    <!-- Spending -->
    <div class="card">
      <h2>&#128176; Gemini Spending</h2>
      <div class="stat-row"><span class="stat-label">Total Cost</span><span class="stat-value" id="sp-cost" style="color:var(--gold)"></span></div>
      <div class="stat-row"><span class="stat-label">Remaining</span><span class="stat-value" id="sp-budget"></span></div>
      <div class="progress-bar"><div class="progress-fill" id="sp-bar"></div></div>
      <div class="stat-row"><span class="stat-label">Input Tokens</span><span class="stat-value" id="sp-in"></span></div>
      <div class="stat-row"><span class="stat-label">Output Tokens</span><span class="stat-value" id="sp-out"></span></div>
      <div class="stat-row"><span class="stat-label">API Calls</span><span class="stat-value" id="sp-calls"></span></div>
      <div class="stat-row"><span class="stat-label">Avg / Call</span><span class="stat-value" id="sp-avg"></span></div>
      <div class="stat-row"><span class="stat-label">Est. Calls Left</span><span class="stat-value" id="sp-remaining" style="color:var(--mint-green)"></span></div>
      <div class="stat-row" style="margin-top:.75rem">
        <a href="https://aistudio.google.com/spend?project=gen-lang-client-0162710322" target="_blank" rel="noopener"
           style="color:var(--gold);font-size:.85rem;text-decoration:none;">&#128279; View exact spend in AI Studio &#8599;</a>
      </div>
    </div>

    <!-- System -->
    <div class="card">
      <h2>&#9881;&#65039; System Info</h2>
      <div class="stat-row"><span class="stat-label">Version</span><span class="stat-value" id="sys-version"></span></div>
      <div class="stat-row"><span class="stat-label">Phase</span><span class="stat-value" id="sys-phase"></span></div>
      <div class="stat-row"><span class="stat-label">Bot User</span><span class="stat-value" id="sys-bot" style="color:var(--spirit-blue)"></span></div>
      <div class="stat-row"><span class="stat-label">Main Model</span><span class="stat-value" id="sys-model" style="color:var(--gold)"></span></div>
      <div class="stat-row"><span class="stat-label">Local Model</span><span class="stat-value" id="sys-local-model" style="color:pink"></span></div>
      <div class="stat-row"><span class="stat-label">Rate Limits</span><span class="stat-value" id="sys-rates"></span></div>
      <div class="stat-row"><span class="stat-label">Python</span><span class="stat-value" id="sys-python"></span></div>
      <div class="stat-row"><span class="stat-label">discord.py</span><span class="stat-value" id="sys-discordpy"></span></div>
      <div class="stat-row"><span class="stat-label">Latency</span><span class="stat-value" id="sys-latency"></span></div>
    </div>
  </div>

  <!-- Daily spending chart -->
  <div class="card">
    <h2>&#128202; Daily Spending</h2>
    <div id="daily-chart"><span style="color:var(--muted);font-style:italic">No data yet &#8212; spending will appear after your first /ask</span></div>
  </div>

  <!-- Commands -->
  <div class="card">
    <h2>&#128172; Discord Commands</h2>
    <p style="color:var(--muted);font-size:0.85rem;margin-bottom:1rem;font-style:italic">
      Manual slash commands you type directly in Discord to control the bot.
    </p>
    <input type="text" class="search-box" id="cmd-search" placeholder="&#128269; Search commands..." oninput="filterCmds()">
    <table>
      <thead><tr><th style="width:14rem">Command</th><th>Description</th></tr></thead>
      <tbody id="commands-body"></tbody>
    </table>
  </div>

  <!-- Skills -->
  <div class="card">
    <h2>&#129520; Skills (<span id="skill-count">0</span>)</h2>
    <p style="color:var(--muted);font-size:0.85rem;margin-bottom:1rem;font-style:italic">
      Autonomous capabilities the AI uses behind the scenes when answering your <code>/ask</code> queries.
    </p>
    <input type="text" class="search-box" id="skill-search" placeholder="&#128269; Search skills..." oninput="filterSkills()">
    <table>
      <thead><tr><th style="width:2rem">#</th><th style="width:12rem">Skill</th><th>Description</th></tr></thead>
      <tbody id="skills-body"></tbody>
    </table>
  </div>

  <!-- Config -->
  <div class="card">
    <h2>&#128272; Security &amp; Config</h2>
    <div id="config-rows"></div>
  </div>

  <div class="footer">
    OpenClaw &mdash; built with Heritage Blue &#128153; &mdash; <span id="refresh-ts"></span>
  </div>

</div><!-- /app -->
</div><!-- /container -->

<script>
// =========================================================================
// WebGL animated particle background
// =========================================================================
(function initGL() {
  const canvas = document.getElementById('gl-canvas');
  const gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: false });
  if (!gl) return; // graceful fallback — no WebGL

  function resize() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; gl.viewport(0,0,canvas.width,canvas.height); }
  resize(); window.addEventListener('resize', resize);

  const vsrc = `
    attribute vec2 aPos;
    attribute float aSize;
    attribute float aAlpha;
    uniform vec2 uRes;
    varying float vAlpha;
    void main() {
      vec2 clip = (aPos / uRes) * 2.0 - 1.0;
      clip.y = -clip.y;
      gl_Position = vec4(clip, 0.0, 1.0);
      gl_PointSize = aSize;
      vAlpha = aAlpha;
    }`;
  const fsrc = `
    precision mediump float;
    varying float vAlpha;
    void main() {
      float d = length(gl_PointCoord - 0.5) * 2.0;
      if (d > 1.0) discard;
      float a = smoothstep(1.0, 0.3, d) * vAlpha;
      gl_FragColor = vec4(0.408, 0.675, 0.898, a); /* spirit blue */
    }`;

  function mkShader(src, type) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s;
  }
  const prog = gl.createProgram();
  gl.attachShader(prog, mkShader(vsrc, gl.VERTEX_SHADER));
  gl.attachShader(prog, mkShader(fsrc, gl.FRAGMENT_SHADER));
  gl.linkProgram(prog); gl.useProgram(prog);

  const N = 120;
  const px = new Float32Array(N), py = new Float32Array(N);
  const vx = new Float32Array(N), vy = new Float32Array(N);
  const sz = new Float32Array(N), al = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    px[i] = Math.random() * canvas.width;
    py[i] = Math.random() * canvas.height;
    vx[i] = (Math.random() - 0.5) * 0.4;
    vy[i] = (Math.random() - 0.5) * 0.3;
    sz[i] = 1.5 + Math.random() * 3;
    al[i] = 0.08 + Math.random() * 0.18;
  }

  const posBuf = gl.createBuffer(), sizeBuf = gl.createBuffer(), alphaBuf = gl.createBuffer();
  const aPos = gl.getAttribLocation(prog, 'aPos');
  const aSize = gl.getAttribLocation(prog, 'aSize');
  const aAlpha = gl.getAttribLocation(prog, 'aAlpha');
  const uRes = gl.getUniformLocation(prog, 'uRes');

  gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE);

  const posData = new Float32Array(N * 2);

  function frame() {
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(uRes, canvas.width, canvas.height);

    for (let i = 0; i < N; i++) {
      px[i] += vx[i]; py[i] += vy[i];
      if (px[i] < 0 || px[i] > canvas.width) vx[i] *= -1;
      if (py[i] < 0 || py[i] > canvas.height) vy[i] *= -1;
      posData[i*2] = px[i]; posData[i*2+1] = py[i];
    }

    gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
    gl.bufferData(gl.ARRAY_BUFFER, posData, gl.DYNAMIC_DRAW);
    gl.enableVertexAttribArray(aPos); gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, sizeBuf);
    gl.bufferData(gl.ARRAY_BUFFER, sz, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(aSize); gl.vertexAttribPointer(aSize, 1, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, alphaBuf);
    gl.bufferData(gl.ARRAY_BUFFER, al, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(aAlpha); gl.vertexAttribPointer(aAlpha, 1, gl.FLOAT, false, 0, 0);

    gl.drawArrays(gl.POINTS, 0, N);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();

// =========================================================================
// Data fetch & render
// =========================================================================
let _allSkills = [], _allCmds = [];

async function loadData() {
  const r = await fetch('/api/dashboard');
  return await r.json();
}

function render(d) {
  // Header
  const cmdCount = d.commands.reduce((a,c) => a + c.commands.length, 0);
  document.getElementById('subtitle').innerHTML = `v${d.version} \u2022 ${d.skill_count} skills \u2022 ${cmdCount} commands &middot; <a href="https://openclaw.davevoyles.synology.me/dashboard" target="_blank" style="color:var(--muted);text-decoration:none;font-weight:bold">openclaw.davevoyles.synology.me</a>`;
  document.getElementById('uptime-badge').textContent = '\u23f1 ' + formatUptime(d.uptime_seconds);
  document.getElementById('link-github').href = d.github_repo;

  // Spending
  const sp = d.spending;
  document.getElementById('sp-cost').textContent = `$${sp.total_cost.toFixed(4)}`;
  document.getElementById('sp-budget').textContent = `$${sp.budget_remaining.toFixed(4)} / $${sp.budget_limit.toFixed(2)}`;
  const pct = sp.budget_pct;
  const bar = document.getElementById('sp-bar');
  bar.style.width = Math.max(pct, 2) + '%';
  bar.className = 'progress-fill ' + (pct < 50 ? 'pf-ok' : pct < 80 ? 'pf-warn' : 'pf-danger');
  bar.textContent = pct.toFixed(1) + '%';
  document.getElementById('sp-in').textContent = sp.total_input_tokens.toLocaleString();
  document.getElementById('sp-out').textContent = sp.total_output_tokens.toLocaleString();
  document.getElementById('sp-calls').textContent = sp.calls.toLocaleString();
  if (sp.calls > 0) {
    const avg = sp.total_cost / sp.calls;
    document.getElementById('sp-avg').textContent = `$${avg.toFixed(6)}`;
    document.getElementById('sp-remaining').textContent = avg > 0 ? `~${Math.floor(sp.budget_remaining / avg).toLocaleString()}` : '\u2014';
  } else {
    document.getElementById('sp-avg').textContent = '\u2014';
    document.getElementById('sp-remaining').textContent = '\u221e';
  }

  // Daily chart
  const days = Object.entries(sp.daily).sort((a,b) => b[0].localeCompare(a[0])).slice(0, 14);
  const chartEl = document.getElementById('daily-chart');
  if (days.length > 0) {
    const maxCost = Math.max(...days.map(([,v]) => v.cost_usd), 0.0001);
    chartEl.innerHTML = '';
    days.reverse().forEach(([day, v]) => {
      const p = (v.cost_usd / maxCost) * 100;
      chartEl.innerHTML += `<div class="chart-bar"><span class="chart-label">${day.slice(5)}</span><div class="chart-bar-inner" style="width:${Math.max(p,2)}%"></div><span class="chart-value">$${v.cost_usd.toFixed(4)} &middot; ${v.calls} calls</span></div>`;
    });
  }

  // System
  document.getElementById('sys-version').textContent = d.version;
  document.getElementById('sys-phase').textContent = 'Phase ' + d.config.phase;
  document.getElementById('sys-bot').textContent = d.bot_user || 'N/A';
  document.getElementById('sys-model').textContent = d.model;
  document.getElementById('sys-local-model').textContent = d.local_model || 'Disabled';
  document.getElementById('sys-rates').textContent = d.rate_info;
  document.getElementById('sys-python').textContent = d.python;
  document.getElementById('sys-discordpy').textContent = d.discord_py;
  document.getElementById('sys-latency').textContent = d.latency_ms + ' ms';

  // Containers
  renderContainers(d.containers);

  // Resource usage stats
  if (d.stats) renderStats(d.stats);

  // Server Resource stats
  if (d.sys_stats) {
    document.getElementById('mac-mini-usage').style.display = 'block';
    document.getElementById('server-cpu').textContent = d.sys_stats.cpu || '--';
    document.getElementById('server-mem').textContent = d.sys_stats.mem || '--';
    document.getElementById('server-disk').textContent = d.sys_stats.disk || '--';
  }

  // Ontology
  renderOntology(d.ontology);

  // Skills
  _allSkills = d.skills;
  document.getElementById('skill-count').textContent = d.skill_count;
  renderSkills(d.skills);

  // Commands
  _allCmds = d.commands;
  renderCmds(d.commands);

  // Config
  const cr = document.getElementById('config-rows');
  cr.innerHTML = '';
  const sec = d.config.security || {};
  const llm = d.config.llm || {};
  [
    ['Sandbox Mode', sec.sandbox_mode, true],
    ['Require Approval', sec.require_approval, true],
    ['Audit Logging', sec.audit_logging, true],
    ['Max Tokens', llm.max_tokens || '?', false],
    ['Temperature', llm.temperature || '?', false],
    ['Max Tool Rounds', llm.max_tool_rounds || '?', false],
    ['Conversation TTL', (llm.conversation?.ttl_minutes || '?') + ' min', false],
    ['Max History', llm.conversation?.max_history || '?', false],
  ].forEach(([k,v,isBool]) => {
    const val = isBool ? (v ? '<span style="color:var(--mint-green)">&#10003; Enabled</span>' : '<span style="color:var(--danger)">&#10007; Disabled</span>') : v;
    cr.innerHTML += `<div class="stat-row"><span class="stat-label">${k}</span><span class="stat-value">${val}</span></div>`;
  });

  document.getElementById('refresh-ts').textContent = 'Updated ' + new Date().toLocaleTimeString();
}

function renderSkills(skills) {
  const sb = document.getElementById('skills-body');
  sb.innerHTML = '';
  skills.forEach((s, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td style="color:var(--muted)">${i+1}</td><td><code>${esc(s.name)}</code></td><td style="color:var(--muted)">${esc(s.description).slice(0,130)}</td>`;
    sb.appendChild(tr);
  });
}

function renderContainers(containers) {
  const cg = document.getElementById('container-grid');
  if (!containers || containers.length === 0) {
    cg.innerHTML = '<div style="color:var(--muted);font-style:italic">No containers found.</div>';
    return;
  }
  cg.innerHTML = '';
  containers.forEach(c => {
    const div = document.createElement('div');
    div.className = 'health-item';
    const statusClass = c.is_up ? 'health-status-up' : 'health-status-down';
    div.innerHTML = `<div class="health-status-dot ${statusClass}"></div><span class="health-name" title="${esc(c.status)}">${esc(c.name)}</span>`;
    cg.appendChild(div);
  });
}

function renderStats(stats) {
  const cg = document.getElementById('container-grid');
  // Enhance existing grid items with stats if they match
  const items = cg.querySelectorAll('.health-item');
  items.forEach(item => {
    const name = item.querySelector('.health-name').textContent;
    const stat = stats.find(s => s.name === name);
    if (stat) {
      const statsUI = document.createElement('div');
      statsUI.style.fontSize = '0.7rem';
      statsUI.style.color = 'var(--muted)';
      statsUI.style.marginLeft = 'auto';
      statsUI.innerHTML = `${stat.cpu} <span style="opacity:0.5;margin:0 2px">|</span> ${stat.mem.split('/')[0].trim()}`;
      item.appendChild(statsUI);
    }
  });
}

function renderOntology(facts) {
  const list = document.getElementById('ontology-list');
  if (!facts || facts.length === 0) {
    list.innerHTML = '<div style="color:var(--muted);font-style:italic">No facts remembered yet. Try telling the agent to "Remember that..."</div>';
    return;
  }
  list.innerHTML = '';
  facts.forEach(fact => {
    const div = document.createElement('div');
    div.className = 'stat-row'; // Reuse stat-row for clean horizontal layout
    div.innerHTML = `<span class="stat-label">&#8226;</span><span class="stat-value" style="flex:1;margin-left:0.5rem;text-align:left;color:var(--text)">${esc(fact)}</span>`;
    list.appendChild(div);
  });
}

function renderCmds(cmds) {
  const cb = document.getElementById('commands-body');
  cb.innerHTML = '';
  cmds.forEach(cat => {
    const hdr = document.createElement('tr');
    hdr.innerHTML = `<td class="cmd-cat" colspan="2">${esc(cat.category)}</td>`;
    hdr.dataset.cat = '1';
    cb.appendChild(hdr);
    cat.commands.forEach(c => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td><code>${esc(c.name)}</code></td><td style="color:var(--muted)">${esc(c.desc)}</td>`;
      tr.dataset.name = c.name.toLowerCase();
      cb.appendChild(tr);
    });
  });
}

function filterSkills() {
  const q = document.getElementById('skill-search').value.toLowerCase();
  const filtered = q ? _allSkills.filter(s => s.name.includes(q) || s.description.toLowerCase().includes(q)) : _allSkills;
  renderSkills(filtered);
}

function filterCmds() {
  const q = document.getElementById('cmd-search').value.toLowerCase();
  const rows = document.querySelectorAll('#commands-body tr');
  rows.forEach(r => {
    if (r.dataset.cat) { r.style.display = q ? 'none' : ''; return; }
    r.style.display = (r.dataset.name || '').includes(q) || r.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}

async function refreshData(btn) {
  if (btn) { btn.classList.add('spinning'); btn.disabled = true; }
  try {
    const d = await loadData();
    render(d);
  } catch(e) { console.error(e); }
  if (btn) { setTimeout(() => { btn.classList.remove('spinning'); btn.disabled = false; }, 600); }
}

// Init
(async () => {
  try {
    const d = await loadData();
    render(d);
    document.getElementById('loading').style.display = 'none';
    document.getElementById('app').style.display = 'block';
  } catch(e) {
    document.getElementById('loading').innerHTML = `<span style="color:var(--danger)">Failed to connect: ${esc(e.message)}</span>`;
  }
})();

// Auto-refresh every 60s
setInterval(() => refreshData(null), 60000);

function formatUptime(s) {
  const d = Math.floor(s/86400), h = Math.floor(s%86400/3600), m = Math.floor(s%3600/60);
  return d > 0 ? `${d}d ${h}h ${m}m` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}
function esc(s) { const el = document.createElement('div'); el.textContent = s; return el.innerHTML; }
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Guide / Tutorial page
# ---------------------------------------------------------------------------

GUIDE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw Guide &amp; Tutorial</title>
<style>
  :root {
    --heritage-blue: #002D72;
    --spirit-blue: #68ACE5;
    --medium-blue: #0077D8;
    --harbor-blue: #4E97E0;
    --mint-green: #86C8BC;
    --homewood-green: #008767;
    --gold: #F1C400;
    --orange: #FF9E1B;
    --red: #CF4520;
    --sable: #31261D;
    --bg: #001233;
    --surface: rgba(0, 45, 114, 0.35);
    --border: rgba(104, 172, 229, 0.2);
    --text: #e8f0fe;
    --muted: rgba(104, 172, 229, 0.7);
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.7; padding: 2rem;
  }
  .container { max-width: 900px; margin: 0 auto; }
  a { color: var(--spirit-blue); text-decoration: none; }
  a:hover { color: var(--gold); }
  h1 { font-size: 2rem; margin-bottom: 0.5rem; color: var(--spirit-blue); }
  h2 {
    font-size: 1.3rem; color: var(--gold); margin: 2rem 0 0.75rem;
    padding-bottom: 0.4rem; border-bottom: 1px solid var(--border);
  }
  h3 { font-size: 1.05rem; color: var(--spirit-blue); margin: 1.25rem 0 0.5rem; }
  p, li { color: var(--text); font-size: 0.92rem; }
  ul, ol { padding-left: 1.5rem; margin: 0.5rem 0; }
  li { margin-bottom: 0.3rem; }
  code {
    background: rgba(0,119,216,0.12); color: var(--spirit-blue);
    padding: 0.15rem 0.45rem; border-radius: 0.3rem; font-size: 0.85rem;
    font-family: 'SF Mono', 'Fira Code', monospace;
  }
  pre {
    background: rgba(0,45,114,0.5); border: 1px solid var(--border);
    border-radius: 0.5rem; padding: 1rem; overflow-x: auto;
    margin: 0.75rem 0; font-size: 0.84rem; line-height: 1.5;
  }
  pre code { background: none; padding: 0; color: var(--text); }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 0.75rem; padding: 1.25rem; margin: 1rem 0;
    backdrop-filter: blur(12px);
  }
  .badge {
    display: inline-block; padding: 0.15rem 0.5rem; border-radius: 1rem;
    font-size: 0.72rem; font-weight: 700; margin-right: 0.3rem;
  }
  .risk-low { background: rgba(0,135,103,0.2); color: var(--mint-green); }
  .risk-high { background: rgba(207,69,32,0.2); color: var(--orange); }
  .risk-crit { background: rgba(207,69,32,0.3); color: var(--red); }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin: 0.75rem 0; }
  th {
    text-align: left; padding: 0.5rem 0.6rem; border-bottom: 2px solid var(--border);
    color: var(--gold); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em;
  }
  td { padding: 0.45rem 0.6rem; border-bottom: 1px solid rgba(104,172,229,0.06); }
  .toc { list-style: none; padding-left: 0; columns: 2; }
  .toc li { margin-bottom: 0.4rem; }
  .toc a { font-size: 0.88rem; }
  .subtitle { color: var(--muted); font-size: 0.95rem; margin-bottom: 1.5rem; }
  .btn {
    display: inline-flex; align-items: center; gap: 0.4rem;
    padding: 0.5rem 1rem; border-radius: 0.5rem; font-size: 0.82rem;
    font-weight: 600; border: 1px solid var(--border); color: var(--text);
    background: var(--surface); text-decoration: none; margin-right: 0.5rem;
    transition: all 0.2s;
  }
  .btn:hover { background: rgba(0,119,216,0.25); border-color: var(--spirit-blue); color: #fff; }
  .tip {
    border-left: 3px solid var(--gold); padding: 0.75rem 1rem;
    margin: 1rem 0; background: rgba(241,196,0,0.06); border-radius: 0 0.5rem 0.5rem 0;
  }
  .tip strong { color: var(--gold); }
  @media (max-width: 600px) { .toc { columns: 1; } .container { padding: 0; } body { padding: 0.75rem; } }
</style>
</head>
<body>
<div class="container">

<a class="btn" href="/dashboard">&larr; Back to Dashboard</a>

<h1>&#128218; OpenClaw Guide &amp; Tutorial</h1>
<p class="subtitle">Version 0.6.0 &mdash; Everything you need to know to get the most out of your AI-powered Mac Mini agent.</p>

<!-- TOC -->
<div class="card">
<h3 style="margin-top:0">Table of Contents</h3>
<ul class="toc">
  <li><a href="#overview">1. Overview &amp; Architecture</a></li>
  <li><a href="#getting-started">2. Getting Started</a></li>
  <li><a href="#ask">3. The /ask Command (AI Chat)</a></li>
  <li><a href="#threads">4. Conversation Threads (Save &amp; Resume)</a></li>
  <li><a href="#docker">5. Docker &amp; System Commands</a></li>
  <li><a href="#media">6. Media &amp; Downloads</a></li>
  <li><a href="#monitoring">7. Network &amp; Monitoring</a></li>
  <li><a href="#memory">8. Long-Term Memory (QMD)</a></li>
  <li><a href="#scheduler">9. Scheduled Tasks</a></li>
  <li><a href="#spending">10. Spending &amp; Budget Tracking</a></li>
  <li><a href="#mail">11. AgentMail (Email)</a></li>
  <li><a href="#scraping">12. Web Search &amp; Git</a></li>
  <li><a href="#weather">13. Weather (/weather)</a></li>
  <li><a href="#research">14. Deep Research (/research)</a></li>
  <li><a href="#session-memory">15. Session Memory &amp; Recall</a></li>
  <li><a href="#autonomy">16. Autonomous Operations</a></li>
  <li><a href="#security">17. Security &amp; Approvals</a></li>
  <li><a href="#dashboard-guide">18. Dashboard &amp; Endpoints</a></li>
  <li><a href="#tips">19. Power User Tips</a></li>
  <li><a href="#troubleshooting">20. Troubleshooting</a></li>
  <li><a href="#gateway">21. API Gateway (Maton)</a></li>
</ul>
</div>

<!-- 1. Overview -->
<h2 id="overview">1. Overview &amp; Architecture</h2>
<p>OpenClaw is a Discord bot that acts as an AI-powered operations agent for your Mac Mini Docker stack. It connects to:</p>
<ul>
  <li><strong>Google Gemini 2.5 Flash</strong> &mdash; AI reasoning with function calling (66 skills) — used for tool-requiring, weather, and research queries</li>
  <li><strong>Ollama gemma3:12b</strong> &mdash; local LLM running on the Mac Mini for conversational queries (free, no rate limits, ~15&ndash;20 tok/s on M4 Neural Engine)</li>
  <li><strong>Docker Engine</strong> &mdash; manage 26+ containers running on the Mac Mini</li>
  <li><strong>*arr Stack</strong> &mdash; Sonarr, Radarr, Lidarr, Prowlarr, Bazarr via their APIs</li>
  <li><strong>Download Clients</strong> &mdash; SABnzbd (Usenet) and qBittorrent (torrents)</li>
  <li><strong>Plex / Tautulli</strong> &mdash; media server monitoring</li>
  <li><strong>Synology NAS</strong> &mdash; storage backend</li>
</ul>

<div class="card">
<h3>How It Works &mdash; Hybrid Routing</h3>
<pre><code>You type /ask "hello"
    &darr;
OpenClaw checks: does this need tool use? &rarr; No
    &darr;
Routed to Ollama (local, free, &lt;200ms)
    &darr;
You get a fast conversational reply

You type /ask "What's downloading right now?"
    &darr;
OpenClaw checks: does this need tool use? &rarr; Yes ("download" keyword)
    &darr;
Sent to Gemini 2.5 Flash with 27 tool declarations
    &darr;
Gemini calls get_download_queue
    &darr;
OpenClaw queries SABnzbd + qBittorrent APIs
    &darr;
Gemini formats the results into a human-readable answer
    &darr;
You get a Discord embed with your active downloads</code></pre>
<p>Gemini can chain up to <strong>5 tool calls</strong> per question. Simple chat goes to Ollama at zero cost. The response footer shows which model answered.</p>
</div>

<!-- 2. Getting Started -->
<h2 id="getting-started">2. Getting Started</h2>
<p>OpenClaw lives in your Discord server. All commands are slash commands &mdash; type <code>/</code> in any channel to see the full list.</p>

<h3>First Commands to Try</h3>
<div class="card">
<table>
  <tr><td><code>/ping</code></td><td>Verify the bot is alive. Shows latency and uptime.</td></tr>
  <tr><td><code>/about</code></td><td>See version, Python version, discord.py version, OS info.</td></tr>
  <tr><td><code>/whoami</code></td><td>Check your Discord ID and whether you're in the allowed-users list.</td></tr>
  <tr><td><code>/help</code></td><td>Full list of all 29 commands grouped by category.</td></tr>
  <tr><td><code>/skills</code></td><td>See all 27 LLM-callable skills that <code>/ask</code> can use.</td></tr>
</table>
</div>

<!-- 3. /ask -->
<h2 id="ask">3. The <code>/ask</code> Command (AI Chat)</h2>
<p>The most powerful command. Uses <strong>hybrid routing</strong>: conversational queries go to local Ollama (free, fast), tool-requiring queries go to Gemini 2.5 Flash with function calling.</p>

<h3>Example Queries</h3>
<div class="card">
<pre><code>/ask What containers are running?
/ask Is Sonarr healthy? Any errors in the last 50 lines?
/ask Show me active downloads
/ask What was recently added to Plex?
/ask Run a speed test
/ask What's my Gemini spending so far?
/ask Check all services and give me a status report
/ask Search for "The Bear" on Sonarr
/ask How much disk space is left?
/ask Remember that the NAS IP is 192.168.1.8
/ask What do you remember about the NAS?</code></pre>
</div>

<div class="tip">
  <strong>&#128161; Context is per-channel:</strong> <code>/ask</code> automatically remembers the last 20 messages in a channel for 30 minutes. No need to reply-thread &mdash; just keep using <code>/ask</code>. Use <code>/clear</code> to reset, or <code>/save</code> to keep it permanently.
</div>

<div class="tip">
  <strong>&#128161; Long responses:</strong> Responses longer than 3,800 characters are automatically split across multiple Discord embeds &mdash; no truncation.
</div>

<h3>How Hybrid Routing Works</h3>
<table>
  <tr><th>Query type</th><th>Routed to</th><th>Cost</th><th>Footer shows</th></tr>
  <tr><td>Conversational ("hello", "what time is it", "who wrote Hamlet")</td><td>Ollama gemma3:12b (local)</td><td>Free</td><td><code>local &middot; unlimited</code></td></tr>
  <tr><td>Tool-requiring ("sonarr", "status", "docker", "download", etc.)</td><td>Gemini 2.5 Flash</td><td>~$0.0005/query</td><td>Rate counters</td></tr>
  <tr><td>Weather / forecast ("weather", "rain", "forecast", "temperature")</td><td>Gemini 2.5 Flash + <code>get_weather</code> tool</td><td>~$0.0005/query</td><td>Rate counters</td></tr>
  <tr><td>Hallucination detected in Ollama response</td><td>Gemini 2.5 Flash (re-routed automatically)</td><td>~$0.0005/query</td><td>Rate counters</td></tr>
</table>

<h3>Rate Limits (Gemini only)</h3>
<table>
  <tr><th>Limit</th><th>Value</th><th>What Happens</th></tr>
  <tr><td>Per minute</td><td>60 requests</td><td>Queued, slight delay</td></tr>
  <tr><td>Per hour</td><td>500 requests</td><td>Graceful rejection with retry message</td></tr>
  <tr><td>Budget</td><td>$30.00</td><td>Bot warns at 80%, stops at 100%</td></tr>
</table>

<!-- NEW: Conversation Threads -->
<h2 id="threads">4. Conversation Threads (Save &amp; Resume)</h2>
<p>By default, <code>/ask</code> keeps 30 minutes of context in memory. If you're researching something and want to pick it up later, save the conversation as a named thread. Threads are written to disk and survive container restarts.</p>

<h3>Workflow</h3>
<div class="card">
<pre><code>1. Chat with /ask as normal
   /ask Tell me about the Sonarr upgrade options
   /ask What version is Sonarr on?
   /ask What would break if I upgraded?

2. Save when you want to stop:
   /save sonarr-upgrade
   &rarr; ✅ Saved thread sonarr-upgrade (6 messages)

3. Come back later (any time, even after restart):
   /resume sonarr-upgrade
   &rarr; ✅ Resumed thread sonarr-upgrade &mdash; 6 messages (saved 2026-03-23 14:30)

4. Continue the conversation:
   /ask OK where were we?</code></pre>
</div>

<h3>Commands</h3>
<table>
  <tr><th>Command</th><th>What it does</th></tr>
  <tr><td><code>/save &lt;name&gt;</code></td><td>Snapshot current conversation to disk. Name: letters, digits, <code>-</code> or <code>_</code>, up to 32 chars.</td></tr>
  <tr><td><code>/resume &lt;name&gt;</code></td><td>Load a saved thread and make it the active conversation in this channel.</td></tr>
  <tr><td><code>/threads</code></td><td>List all your saved threads with message count and save date.</td></tr>
  <tr><td><code>/forget &lt;name&gt;</code></td><td>Permanently delete a saved thread from disk.</td></tr>
</table>

<div class="tip">
  <strong>&#128161; Thread names are per-user</strong> and stored in <code>/memory/threads/</code>. They are private to you and not visible to other bot users.
</div>

<div class="tip">
  <strong>&#128161; Overwrites work:</strong> Running <code>/save same-name</code> again replaces the previous save &mdash; useful to update a thread you're actively working on.
</div>

<!-- 4. Docker & System (renumbered) -->
<h2 id="docker">5. Docker &amp; System Commands</h2>

<h3><code>/containers</code></h3>
<p>Lists all running Docker containers with their name, status, and port mappings. Quick way to see what's up.</p>

<h3><code>/status &lt;service&gt;</code></h3>
<p>Deep-dive on one container: CPU usage, memory, network I/O, ports, restart count, image version.</p>
<pre><code>/status sonarr
/status qbittorrent
/status openclaw</code></pre>

<h3><code>/logs &lt;service&gt; [lines]</code></h3>
<p>View the last N lines of a container's logs (default: 30, max: 100). Great for debugging.</p>
<pre><code>/logs sonarr 50
/logs sabnzbd</code></pre>

<h3><code>/system</code></h3>
<p>Mac Mini resource usage: CPU %, memory (used/total), disk space. Pulls from Glances if available, falls back to system commands.</p>

<h3><code>/dockerstats</code></h3>
<p>Per-container resource table: CPU %, memory usage, network RX/TX. Similar to <code>docker stats</code> but formatted nicely.</p>

<h3><code>/restart &lt;service&gt;</code> <span class="badge risk-high">HIGH RISK</span></h3>
<p>Restarts a container. This requires <strong>approval</strong> &mdash; you'll get a button prompt:</p>
<pre><code>/restart sonarr
  &rarr; "Restart sonarr? ✅ Approve | ❌ Deny"
  &rarr; Click ✅ to confirm (expires in 5 minutes)</code></pre>
<p><strong>Protected services</strong> that can never be restarted via the bot: <code>traefik</code>, <code>socket-proxy</code>, <code>homepage</code>, <code>watchtower</code>.</p>

<!-- 5. Media -->
<h2 id="media">6. Media &amp; Downloads</h2>

<h3><code>/search &lt;query&gt; [type]</code></h3>
<p>Search your Sonarr and Radarr libraries. The <code>type</code> parameter is optional: <code>tv</code>, <code>movie</code>, or <code>all</code> (default).</p>
<pre><code>/search The Bear
/search Oppenheimer movie
/search breaking bad tv</code></pre>

<h3><code>/queue</code></h3>
<p>Shows active downloads from both SABnzbd (Usenet) and qBittorrent (torrents). Includes filename, progress %, speed, and ETA.</p>

<h3><code>/recent [count]</code></h3>
<p>Recently added media from Plex (via Tautulli). Default 10, max 25.</p>
<pre><code>/recent
/recent 5</code></pre>

<h3><code>/health</code></h3>
<p>Checks connectivity to all media services:</p>
<ul>
  <li><strong>*arr services:</strong> Sonarr, Radarr, Lidarr, Prowlarr &mdash; checks their <code>/ping</code> or <code>/api/v3/system/status</code> endpoints</li>
  <li><strong>Download clients:</strong> SABnzbd API + qBittorrent API</li>
  <li><strong>Plex:</strong> via Tautulli status endpoint</li>
</ul>

<h3><code>/ports</code></h3>
<p>TCP connectivity check on 10 key services. Verifies each service is listening on its expected port. Useful after restarts or network changes.</p>

<h3><code>/report</code></h3>
<p>The big one &mdash; generates a comprehensive status report combining:</p>
<ul>
  <li>Service health checks</li>
  <li>Active downloads</li>
  <li>Recent Plex additions</li>
  <li>System resource usage</li>
  <li>Docker container stats</li>
</ul>

<!-- 6. Network -->
<h2 id="monitoring">7. Network &amp; Monitoring</h2>

<h3><code>/network</code></h3>
<p>Full connectivity check:</p>
<ul>
  <li>&#9989; LAN (ping NAS at 192.168.1.8)</li>
  <li>&#9989; Internet (ping 1.1.1.1)</li>
  <li>&#9989; DNS resolution (resolve google.com)</li>
  <li>&#9989; OpenClaw self health check</li>
</ul>

<h3><code>/speedtest</code></h3>
<p>Downloads a 10MB file from Cloudflare to measure throughput, plus tests DNS resolution latency. Good for diagnosing slow downloads.</p>

<h3><code>/spending [breakdown]</code></h3>
<p>See how much the Gemini API has cost so far. Without the breakdown flag, shows a summary. With breakdown, shows daily spending for the last 7 days.</p>
<pre><code>/spending
/spending breakdown:True</code></pre>

<!-- 7. Memory -->
<h2 id="memory">8. Long-Term Memory (QMD)</h2>
<p>OpenClaw has a persistent memory system that survives restarts. Store facts, preferences, IPs, credentials notes, or anything else you want the bot to remember.</p>

<h3><code>/remember &lt;content&gt; [tags]</code></h3>
<p>Store a fact. Tags are optional comma-separated labels for easier recall.</p>
<pre><code>/remember The NAS IP is 192.168.1.8 tags:network,infrastructure
/remember Sonarr API key rotated on 2026-03-15 tags:sonarr,security
/remember Dave prefers dark mode dashboards tags:preferences
/remember qBittorrent login: admin / check-secrets-env tags:credentials</code></pre>

<h3><code>/recall &lt;query&gt;</code></h3>
<p>Search your stored memories by keyword or tag.</p>
<pre><code>/recall NAS
/recall sonarr
/recall credentials</code></pre>

<div class="tip">
  <strong>&#128161; Tip:</strong> The AI can also use memory! When you <code>/ask</code> a question, Gemini can call <code>recall_fact</code> to check if it already knows the answer from stored memories. Try: <code>/ask What do you remember about the NAS?</code>
</div>

<h3>How It Works Under the Hood</h3>
<p>Memories are stored as JSON in <code>/memory/qmd.json</code>. Each entry has a timestamp, content, and tags. Search is case-insensitive keyword matching against both content and tags.</p>

<!-- 8. Scheduler -->
<h2 id="scheduler">9. Scheduled Tasks</h2>
<p>Automate recurring operations. The scheduler runs any registered skill on a daily or interval basis.</p>

<h3><code>/schedule list</code></h3>
<p>View all scheduled tasks with their status, last run time, and run count.</p>

<h3><code>/schedule add</code></h3>
<p>Create a new scheduled task. Two modes:</p>

<div class="card">
<h3 style="margin-top:0">Daily Schedule (specific time)</h3>
<pre><code>/schedule action:add skill:check_arr_health hour:6 minute:0
  &rarr; Runs check_arr_health every day at 6:00 AM

/schedule action:add skill:create_status_report hour:8 minute:30
  &rarr; Daily status report at 8:30 AM</code></pre>

<h3>Interval Schedule (every N minutes)</h3>
<pre><code>/schedule action:add skill:check_download_clients interval:30
  &rarr; Checks download clients every 30 minutes

/schedule action:add skill:get_docker_stats interval:60
  &rarr; Docker stats snapshot every hour</code></pre>
</div>

<h3><code>/schedule remove</code></h3>
<pre><code>/schedule action:remove task_id:sched-1</code></pre>

<h3><code>/schedule toggle</code></h3>
<p>Enable or disable a task without deleting it.</p>
<pre><code>/schedule action:toggle task_id:sched-1</code></pre>

<div class="tip">
  <strong>&#128161; Tip:</strong> Good scheduled tasks to set up:
  <ul>
    <li><code>check_arr_health</code> every 30 min &mdash; catch service issues early</li>
    <li><code>create_status_report</code> daily at 8 AM &mdash; morning briefing</li>
    <li><code>get_docker_stats</code> every 60 min &mdash; resource usage baseline</li>
  </ul>
</div>

<!-- 9. Spending -->
<h2 id="spending">10. Spending &amp; Budget Tracking</h2>
<p>Every Gemini API call is tracked. The bot records input tokens, output tokens, cost, and timestamps.</p>

<div class="card">
<h3 style="margin-top:0">Pricing (Gemini 2.5 Flash &mdash; Paid Tier)</h3>
<table>
  <tr><th>Type</th><th>Rate</th><th>Typical /ask Cost</th></tr>
  <tr><td>Input tokens</td><td>$0.10 / million</td><td>~$0.0001 per question</td></tr>
  <tr><td>Output tokens</td><td>$0.40 / million</td><td>~$0.0004 per answer</td></tr>
  <tr><td><strong>Typical /ask</strong></td><td colspan="2"><strong>~$0.0005 per round-trip</strong> (with function calling)</td></tr>
</table>
<p>At $0.0005 per query, your $30 budget allows roughly <strong>~60,000 queries</strong>.</p>
</div>

<h3>Budget Safeguards</h3>
<ul>
  <li><strong>50% used:</strong> Normal operation</li>
  <li><strong>80% used:</strong> Warning in <code>/spending</code> output</li>
  <li><strong>100% used:</strong> <code>/ask</code> is disabled to prevent runaway costs</li>
</ul>

<h3>Checking Spending</h3>
<pre><code>/spending             &rarr; Summary: total cost, remaining, token counts
/spending breakdown:True  &rarr; Daily breakdown for last 7 days</code></pre>
<p>Also visible on the <a href="/dashboard">Dashboard</a> with a progress bar and daily chart.</p>

<!-- 10. AgentMail -->
<h2 id="mail">11. AgentMail (Email)</h2>
<p>Send emails directly from Discord using the AgentMail.to API. Useful for alerts, notifications, or sending yourself reminders.</p>

<h3><code>/mail &lt;to&gt; &lt;subject&gt; &lt;body&gt;</code></h3>
<pre><code>/mail you@example.com "Server Alert" "Sonarr restarted at 3:15 AM"
/mail user@example.com "Download Complete" "The Bear S03 finished downloading"</code></pre>

<div class="card">
<h3 style="margin-top:0">Setup Required</h3>
<p><strong>Status: &#9888;&#65039; Not yet configured.</strong></p>
<p>To enable AgentMail:</p>
<ol>
  <li>Sign up at <a href="https://agentmail.to" target="_blank">agentmail.to</a> for an API key</li>
  <li>Add to your <code>.env</code> file: <code>AGENTMAIL_API_KEY=your_key_here</code></li>
  <li>Rebuild the container: <code>cd ~/docker-stack/openclaw &amp;&amp; docker compose up -d --build</code></li>
</ol>
<p>Once configured, the AI can also send emails via <code>/ask</code>: <em>"Email me a status report at you@example.com"</em></p>
</div>

<!-- 11. Web Scraping & Git -->
<h2 id="scraping">12. Web Search, Real Estate &amp; Git</h2>
<p>OpenClaw can extract content from the web, perform deep market research, and interact with Git repositories for code-related tasks.</p>

<h3>3-Tier Search Cascade</h3>
<p>Every <code>search_web</code> call tries three sources in order until one succeeds:</p>
<div class="card"><table>
  <tr><th>Tier</th><th>Method</th><th>Requires</th></tr>
  <tr><td><strong>1 &mdash; Tavily</strong></td><td>AI-optimized semantic search; returns direct answers, not just links</td><td><code>TAVILY_API_KEY</code></td></tr>
  <tr><td><strong>2 &mdash; DuckDuckGo</strong></td><td>Free DDG Lite via <code>free-web-search</code> ClawHub skill</td><td>None</td></tr>
  <tr><td><strong>3 &mdash; Bing HTML</strong></td><td>Scraped with BeautifulSoup4 as last resort</td><td>None</td></tr>
</table></div>
<p>For deep multi-source topic research, use <a href="#research"><code>/research</code></a>.</p>

<h3>Inline Search (via <code>/ask</code>)</h3>
<p>The AI can search the web and fetch content to answer questions:</p>
<ul>
  <li><code>search_web</code>: 3-tier cascade search (Tavily &rarr; DuckDuckGo &rarr; Bing).</li>
  <li><code>browse_url</code>: Extract clean text and markdown from a specific URL.</li>
</ul>

<div class="tip">
  <strong>🏠 Real Estate Investigator:</strong> Gemini is specifically configured to handle property research. Try asking: <br>
  <code>/ask "Find homes in Narberth under $450k with low taxes"</code> <br>
  The agent will search current listings, analyze property taxes, and identify key features like fenced yards.
</div>

<h3>Git Operations</h3>
<ul>
  <li><code>git_search</code>: Search for code patterns in a local repository</li>
  <li><code>git_diff</code>: Compare changes between branches or commits</li>
  <li><code>git_commit_info</code>: Retrieve details about specific commits</li>
</ul>

<!-- 13. Weather -->
<h2 id="weather">13. Weather</h2>
<p>Get current conditions and a 3-day forecast via <a href="https://wttr.in" target="_blank">wttr.in</a> &mdash; no API key required.</p>

<h3><code>/weather [location]</code></h3>
<pre><code>/weather
/weather Philadelphia
/weather London, UK
/weather 19072</code></pre>
<p>With no argument, uses the <code>WEATHER_DEFAULT_LOCATION</code> env var (defaults to your configured city).</p>

<div class="tip">
  <strong>&#128161; Also via /ask:</strong> Any weather-related query containing "weather", "rain", "forecast", "temperature", etc. is automatically routed to Gemini, which calls the <code>get_weather</code> tool. Try:<br>
  <code>/ask What&rsquo;s the weather in Narberth this weekend?</code>
</div>

<div class="card">
<h3 style="margin-top:0">Sample Output</h3>
<pre><code>&#127748; Weather for Philadelphia, PA
Current: &#9925; Overcast, 58&deg;F | Wind: NW 12 mph | Humidity: 72%
&#128197; 3-Day Forecast:
  Today   &#9925; High 62&deg;F / Low 44&deg;F &mdash; Overcast
  Tue     &#127783; High 55&deg;F / Low 40&deg;F &mdash; Light rain
  Wed     &#9728; High 68&deg;F / Low 48&deg;F &mdash; Sunny</code></pre>
</div>

<!-- 14. Deep Research -->
<h2 id="research">14. Deep Research (<code>/research</code>)</h2>
<p>Multi-step autonomous research. Given a question or topic, the bot:</p>
<ol>
  <li><strong>Plans</strong> a set of focused sub-queries using Gemini</li>
  <li><strong>Searches</strong> each sub-query in parallel (3-tier cascade: Tavily &rarr; DuckDuckGo &rarr; Bing)</li>
  <li><strong>Browses</strong> the top source URLs to extract full article content</li>
  <li><strong>Synthesizes</strong> a structured report using low-temperature Gemini in a new Discord thread</li>
</ol>

<h3><code>/research &lt;query&gt;</code></h3>
<pre><code>/research Best neighborhoods in Philadelphia under $400k with good schools
/research Docker vs Podman tradeoffs for home lab usage
/research How to set up Tailscale exit nodes on Synology NAS
/research Current Sonarr v4 migration breaking changes</code></pre>

<div class="tip">
  <strong>&#128161; Live progress:</strong> A Discord thread is created immediately. The bot posts progress messages as each search and URL is processed. The final report appears with action buttons:<br>
  &nbsp;&nbsp;<strong>&#128204; Save to Memory</strong> &mdash; stores the report summary in QMD long-term memory<br>
  &nbsp;&nbsp;<strong>&#128260; Re-run in 24h</strong> &mdash; schedules the research query to repeat tomorrow
</div>

<!-- 15. Session Memory -->
<h2 id="session-memory">15. Session Memory &amp; Cross-Session Recall</h2>
<p>OpenClaw automatically summarizes conversations and injects context into new sessions.</p>

<div class="card">
<h3 style="margin-top:0">How It Works</h3>
<ol>
  <li>You have a conversation with <code>/ask</code> (4+ turns)</li>
  <li>After 30 minutes of inactivity the session expires</li>
  <li>Gemini auto-summarizes the session (~300 tokens)</li>
  <li>Summary saved to disk and QMD memory (<code>/memory/summaries/{user_id}_last_session.json</code>)</li>
  <li>Next conversation: the bot injects &ldquo;Last time we talked about&hellip;&rdquo; into context automatically</li>
</ol>
</div>

<div class="tip">
  <strong>&#128161; No action needed:</strong> This is fully automatic. To query it explicitly:<br>
  <code>/ask What did we talk about last time?</code>
</div>

<p>For facts you always want the bot to retain (not just session summaries), use <code>/remember</code> and <code>/recall</code> &mdash; see <a href="#memory">Section 8</a>.</p>

<!-- 17. Security -->
<h2 id="security">17. Security &amp; Approvals</h2>

<h3>Authorization</h3>
<p>Only Discord users listed in <code>ALLOWED_USER_IDS</code> (in <code>.env</code>) can use the bot. All other users are silently rejected.</p>

<h3>Risk Levels</h3>
<table>
  <tr><th>Level</th><th>Behavior</th><th>Examples</th></tr>
  <tr><td><span class="badge risk-low">LOW</span></td><td>Auto-execute, no approval</td><td><code>/containers</code>, <code>/logs</code>, <code>/health</code>, <code>/search</code></td></tr>
  <tr><td><span class="badge risk-high">HIGH</span></td><td>Requires button approval (5 min timeout)</td><td><code>/restart</code></td></tr>
</table>

<h3><code>/pending</code></h3>
<p>See any pending approval requests (e.g., a <code>/restart</code> waiting for confirmation).</p>

<h3><code>/auditlog [lines]</code></h3>
<p>View the audit trail. Every action is logged to <code>/audit/{date}.jsonl</code> with timestamp, user, action, and result.</p>
<pre><code>/auditlog
/auditlog 25</code></pre>

<h3><code>/estop [stop|resume]</code> <span class="badge risk-crit">EMERGENCY</span></h3>
<p>Emergency stop. Immediately disables all write actions (<code>/restart</code>, <code>/ask</code>, scheduled tasks).</p>
<pre><code>/estop          &rarr; Freezes everything
/estop resume   &rarr; Resumes normal operation</code></pre>
<p>Use this if the bot is behaving unexpectedly or you need to pause all automation.</p>

<!-- 12. Autonomous Operations -->
<h2 id="autonomy">16. Autonomous Agent Operations</h2>
<p>OpenClaw includes dedicated tools for long-running, multi-step tasks that require planning, research, and persistent working memory. These tools enable the AI to work autonomously on complex goals (Manus-style) without constant user prompting.</p>

<div class="card">
<h3 style="margin-top:0">The Planning Workflow</h3>
<p>When you give a complex goal via <code>/ask</code> (e.g., <em>"Research and document a full upgrade path for our Docker stack"</em>), the AI uses three persistent files to track its progress:</p>
<ol>
  <li><strong><code>task_plan.md</code></strong>: The master checklist of phases and steps.</li>
  <li><strong><code>findings.md</code></strong>: Raw data, command outputs, and research notes.</li>
  <li><strong><code>progress.md</code></strong>: A high-level executive summary of current status.</li>
</ol>
<p>This "working memory on disk" allows the AI to maintain context over long periods and across multiple <code>/ask</code> sessions.</p>
</div>

<h3>Core Autonomy Tools (via <code>/ask</code>)</h3>
<ul>
    <li><strong><code>init_planning_files</code></strong>: Creates the base plan and finding files for a new goal.</li>
    <li><strong><code>update_plan_status</code></strong>: Marks phases as complete and updates the current progress note.</li>
    <li><strong><code>autonomous_loop</code></strong>: Starts a continuous execution cycle where the AI checks its plan, performs the next action, evaluates the result, and loops until completion.</li>
</ul>

<h3>Controlling Autonomous Loops</h3>
<p>Safety is built into the autonomous execution system:</p>
<ul>
    <li><strong>Emergency Stop:</strong> If a file named <code>STOP_AUTONOMOUS</code> is created in the <code>data/</code> directory, all loops immediately terminate.</li>
    <li><strong>Iteration Limit:</strong> Loops have a hard-coded maximum number of steps to prevent runaway resource usage.</li>
    <li><strong>Manual Resume:</strong> You can always check <code>progress.md</code> to see where the agent left off and resume the task later.</li>
</ul>

<!-- 13. Dashboard -->
<h2 id="dashboard-guide">18. Dashboard &amp; Endpoints</h2>

<h3>Web Dashboard</h3>
<p>The visual dashboard at <a href="/dashboard">/dashboard</a> shows real-time bot status, spending, skills, and commands. It auto-refreshes every 60 seconds.</p>

<h3>All HTTP Endpoints</h3>
<table>
  <tr><th>Endpoint</th><th>Format</th><th>Purpose</th></tr>
  <tr><td><code>/health</code></td><td>JSON</td><td>Bot status, uptime, guild count. Used by Uptime Kuma.</td></tr>
  <tr><td><code>/metrics</code></td><td>Prometheus</td><td>Scraped by Prometheus/Grafana for graphing.</td></tr>
  <tr><td><code>/dashboard</code></td><td>HTML</td><td>Visual dashboard with JHU brand styling.</td></tr>
  <tr><td><code>/api/dashboard</code></td><td>JSON</td><td>Raw dashboard data (skills, commands, spending, config).</td></tr>
  <tr><td><code>/guide</code></td><td>HTML</td><td>This guide page.</td></tr>
</table>

<h3>External Access</h3>
<p>All endpoints are accessible externally via the Synology reverse proxy:</p>
<pre><code>https://openclaw.davevoyles.synology.me/dashboard
https://openclaw.davevoyles.synology.me/health
https://openclaw.davevoyles.synology.me/guide</code></pre>

<!-- 13. Tips -->
<h2 id="tips">19. Power User Tips</h2>

<div class="card">
<h3 style="margin-top:0">&#127919; Best Practices</h3>
<ol>
  <li><strong>Use <code>/ask</code> for complex queries.</strong> Instead of running 5 separate commands, ask: <em>"Check all services, show downloads, and tell me if anything looks wrong."</em></li>
  <li><strong>Save research threads.</strong> Working on an upgrade or investigating an issue? <code>/save my-topic</code> before you stop. <code>/resume my-topic</code> to pick up where you left off.</li>
  <li><strong>Store important info in memory.</strong> <code>/remember</code> API keys, server IPs, rotation dates. The AI can recall them later.</li>
  <li><strong>Set up scheduled health checks.</strong> <code>/schedule add skill:check_arr_health interval:30</code> catches issues before you notice them.</li>
  <li><strong>Use <code>/analyze</code> for debugging.</strong> When a service acts up, <code>/analyze sonarr 100</code> feeds 100 lines of logs to AI for analysis.</li>
  <li><strong>Check <code>/spending</code> weekly.</strong> At ~$0.0005 per query, it'll last a long time, but it's good to monitor.</li>
  <li><strong>Conversation threads for research.</strong> Save a thread, come back days later, resume it and the AI has full context from where you left off.</li>
  <li><strong>Conversation context is your friend.</strong> Ask a question, then follow up: <em>"What caused that error?"</em> or <em>"Show me the logs for that service."</em></li>
</ol>
</div>

<div class="card">
<h3 style="margin-top:0">&#128296; Useful Daily Commands</h3>
<pre><code>/report                    &rarr; Morning status check
/health                    &rarr; Quick service health
/queue                     &rarr; What's downloading?
/recent 5                  &rarr; Latest Plex additions
/spending                  &rarr; Budget check
/ask Any errors in the last hour?  &rarr; AI-powered log scan</code></pre>
</div>

<div class="card">
<h3 style="margin-top:0">&#128171; Advanced /ask Queries</h3>
<pre><code>/ask Compare CPU usage of sonarr vs radarr
/ask What downloaded this week? Summarize by media type
/ask Is anything using more than 500MB of memory?
/ask Check if ports 8989, 7878, and 8686 are open
/ask Remember that I rotated the Sonarr API key today
/ask Send me an email summary at you@example.com (requires AgentMail setup)
/ask What's the NAS IP? (recalls from memory if stored)</code></pre>
</div>

<div class="card">
<h3 style="margin-top:0">&#129504; Ontology & Graph Memory Queries</h3>
<pre><code>/ask Create a Person entity for 'Dave' with email 'you@example.com'
/ask Link the 'OpenClaw' project to the 'Slack Integration' task with a 'has_task' relation
/ask What are all the tasks that 'blocks' the 'Dashboard' project?
/ask Tell me everything you know about 'Dave'
/ask Search graph for entities of type 'Project'
/ask Validate the knowledge graph</code></pre>
</div>

<div class="card">
<h3 style="margin-top:0">&#127760; API Gateway Examples</h3>
<pre><code>/ask List my active Maton OAuth connections
/ask Connect me to Slack via the API gateway
/ask Use the gateway to post "Hello from OpenClaw!" to Slack channel C0123456
/ask Get my GitHub repos via the gateway
/ask Read cells A1:B10 from my Google Sheet with ID SHEET_ID
/ask Create a HubSpot contact for john@example.com via the gateway</code></pre>
</div>

<!-- 14. Troubleshooting -->
<h2 id="troubleshooting">20. Troubleshooting</h2>

<div class="card">
<h3 style="margin-top:0">Common Issues</h3>
<table>
  <tr><th>Problem</th><th>Solution</th></tr>
  <tr><td>Bot doesn't respond to commands</td><td>Check <code>/health</code> endpoint. If down, run <code>cd ~/docker-stack/openclaw && docker compose up -d</code></td></tr>
  <tr><td><code>/ask</code> returns "over budget"</td><td>Check <code>/spending</code>. Increase budget in <code>.env</code>: <code>GEMINI_BUDGET_LIMIT=50</code>, then rebuild.</td></tr>
  <tr><td><code>/ask</code> returns "rate limited"</td><td>Wait 1 minute. Default: 60 requests/min. Reduce usage or increase limits in <code>config.yaml</code>.</td></tr>
  <tr><td><code>/restart</code> denied</td><td>Check if <code>/estop</code> is active. Also check that the service isn't in the denied list (traefik, socket-proxy, etc.).</td></tr>
  <tr><td><code>/mail</code> fails</td><td><code>AGENTMAIL_API_KEY</code> not set. See <a href="#mail">AgentMail section</a>.</td></tr>
  <tr><td>Skills show "error fetching"</td><td>Service may be down or API key invalid. Check <code>/health</code> first, then verify API keys in <code>.env</code>.</td></tr>
  <tr><td>Dashboard blank</td><td>Bot container may be starting up. Wait 40 seconds after restart (health check start period).</td></tr>
  <tr><td>Conversation lost context</td><td>Conversations expire after 30 minutes of inactivity. Use <code>/save</code> before leaving to keep it permanently, or <code>/resume</code> to restore a saved thread.</td></tr>
  <tr><td><code>gateway_request</code> returns &ldquo;MATON_API_KEY is not set&rdquo;</td><td>Add <code>MATON_API_KEY=your_key</code> to <code>.env</code> and rebuild the container.</td></tr>
  <tr><td>Gateway returns HTTP 400</td><td>No active OAuth connection for that app. Run <em>/ask Connect me to slack via the gateway</em> and open the returned URL to complete OAuth.</td></tr>
  <tr><td>Gateway returns HTTP 401</td><td>Maton API key is invalid. Rotate it at <a href="https://maton.ai/settings" target="_blank">maton.ai/settings</a> and update <code>.env</code>.</td></tr>
  <tr><td>Gateway returns HTTP 429</td><td>Maton rate limit: max 10 requests/second per account. Slow down or spread requests over time.</td></tr>
</table>
</div>

<div class="card">
<h3 style="margin-top:0">Useful Terminal Commands</h3>
<pre><code># Check bot container status
docker logs openclaw --tail 50

# Rebuild after config changes
cd ~/docker-stack/openclaw && docker compose up -d --build

# Check if bot is healthy
curl http://192.168.1.93:8765/health

# View spending data directly
docker exec openclaw cat /memory/spending.json | python3 -m json.tool

# View stored memories
docker exec openclaw cat /memory/qmd.json | python3 -m json.tool

# View scheduled tasks
docker exec openclaw cat /memory/schedules.json | python3 -m json.tool</code></pre>
</div>

<!-- 16. API Gateway -->
<h2 id="gateway">21. API Gateway (Maton)</h2>
<p>The API Gateway gives OpenClaw access to <strong>100+ third-party APIs</strong> &mdash; Slack, GitHub, Google Workspace, Notion, HubSpot, Stripe, Airtable, and more &mdash; through a single <a href="https://maton.ai" target="_blank">Maton</a> API key with fully managed OAuth. No need to handle OAuth flows yourself; Maton stores and refreshes the tokens.</p>

<div class="card">
<h3 style="margin-top:0">How It Works</h3>
<pre><code>You: /ask Post &ldquo;Deployment done&rdquo; to Slack channel #deployments
    &darr;
Gemini calls gateway_request(app="slack", path="api/chat.postMessage", ...)
    &darr;
OpenClaw sends: POST https://gateway.maton.ai/slack/api/chat.postMessage
                Authorization: Bearer $MATON_API_KEY
    &darr;
Maton injects the stored Slack OAuth token and proxies the request
    &darr;
Slack receives the message &mdash; reply appears in Discord</code></pre>
<p>The gateway base URL is <code>https://gateway.maton.ai/{app}/{native-api-path}</code>. You use native API paths exactly as documented by each service &mdash; Maton just handles auth.</p>
</div>

<h3>Setup</h3>
<div class="card">
<ol>
  <li>Sign in at <a href="https://maton.ai" target="_blank">maton.ai</a> &rarr; <a href="https://maton.ai/settings" target="_blank">Settings</a> &rarr; copy your API key.</li>
  <li>It&rsquo;s already in your <code>.env</code> as <code>MATON_API_KEY</code>. &check;</li>
  <li>Connect each app you want to use (only needs to be done once per app):<br>
    <code>/ask Create a Maton OAuth connection for slack</code><br>
    &rarr; OpenClaw returns a URL &rarr; open it in your browser &rarr; complete OAuth &rarr; done.</li>
</ol>
</div>

<h3>The Three Gateway Skills</h3>
<table>
  <tr><th>Skill</th><th>What it does</th></tr>
  <tr><td><code>gateway_request</code></td><td>Call any native API endpoint through the proxy. Supports GET, POST, PUT, PATCH, DELETE with JSON bodies. Returns the raw API response.</td></tr>
  <tr><td><code>gateway_list_connections</code></td><td>List all active OAuth connections (optionally filtered by app name).</td></tr>
  <tr><td><code>gateway_create_connection</code></td><td>Create a new OAuth connection for an app. Returns the OAuth authorization URL to open in a browser.</td></tr>
</table>

<h3>Example /ask Queries</h3>
<div class="card">
<pre><code>/ask List all my active Maton gateway connections
/ask Connect me to GitHub via the API gateway
/ask Use the gateway to post &ldquo;Hello!&rdquo; to Slack channel C0123456
/ask Get my GitHub repositories using the gateway
/ask Read cells A1:D20 from Google Sheet 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
/ask Create a HubSpot contact: email john@example.com, name John Doe
/ask Query Notion database abc123 via the gateway
/ask List my Stripe customers
/ask Search Airtable base appXXXXXX for records in table &ldquo;Projects&rdquo;</code></pre>
</div>

<h3>Supported Services (highlights)</h3>
<table>
  <tr><th>Category</th><th>Services</th></tr>
  <tr><td><strong>Google Workspace</strong></td><td>Gmail, Google Calendar, Google Drive, Google Docs, Google Sheets, Google Slides, Google Tasks, Google Contacts, Google Meet</td></tr>
  <tr><td><strong>Microsoft 365</strong></td><td>Outlook, OneDrive, SharePoint, Microsoft Teams, Microsoft Excel, OneNote, Microsoft To Do</td></tr>
  <tr><td><strong>Project Mgmt</strong></td><td>Notion, Asana, ClickUp, Linear, Trello, Jira, Monday.com, Wrike, Basecamp</td></tr>
  <tr><td><strong>CRM / Sales</strong></td><td>HubSpot, Salesforce, Pipedrive, Attio, Twenty CRM, Zoho CRM</td></tr>
  <tr><td><strong>Communication</strong></td><td>Slack, Microsoft Teams, Telegram, Twilio, WhatsApp Business</td></tr>
  <tr><td><strong>Dev Tools</strong></td><td>GitHub, GitLab (via native API), Sentry, PostHog, Netlify</td></tr>
  <tr><td><strong>Finance</strong></td><td>Stripe, QuickBooks, Xero, Chargebee</td></tr>
  <tr><td><strong>Databases / Docs</strong></td><td>Airtable, Coda, Notion, Baserow, Confluence</td></tr>
  <tr><td><strong>AI / Media</strong></td><td>ElevenLabs, fal.ai, Vimeo, YouTube, Firecrawl, Tavily</td></tr>
</table>
<p>Full list of 100+ services: <a href="https://clawhub.ai/byungkyu/api-gateway" target="_blank">clawhub.ai/byungkyu/api-gateway</a></p>

<h3>Rate Limits &amp; Notes</h3>
<ul>
  <li>Maton allows <strong>10 requests/second</strong> per account.</li>
  <li>Target API rate limits (e.g., Slack&rsquo;s own limits) apply on top of this.</li>
  <li>Custom headers and query parameters are forwarded to the target API.</li>
  <li>All HTTP methods are supported: GET, POST, PUT, PATCH, DELETE.</li>
  <li>If you have multiple OAuth connections for the same app, specify a <code>connection_id</code> in your request.</li>
</ul>

<div class="tip">
  <strong>&#128161; Tip:</strong> You don&rsquo;t need to know the native API URLs by heart &mdash; just describe what you want and Gemini will figure out the right endpoint. E.g. <em>&ldquo;/ask get the last 10 emails from Gmail via the gateway&rdquo;</em>.
</div>

<div style="text-align:center; margin-top:2.5rem; color:var(--muted); font-size:0.8rem; border-top:1px solid var(--border); padding-top:1rem;">
  OpenClaw v0.5.0 &mdash; <a href="/dashboard">Dashboard</a> &middot; <a href="https://github.com/DaveVoyles/openclaw-on-mac-mini" target="_blank">GitHub</a>
</div>

</div><!-- /container -->
</body>
</html>
"""
