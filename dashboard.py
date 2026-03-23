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


def _load_config() -> dict:
    cfg_file = CONFIG_DIR / "config.yaml"
    if cfg_file.exists():
        return yaml.safe_load(cfg_file.read_text()) or {}
    return {}


async def api_dashboard_handler(request: web.Request) -> web.Response:
    """JSON blob with all dashboard data."""
    bot = request.app.get("bot")
    uptime_s = time.monotonic() - bot.start_time if bot else 0

    from skills import SKILLS
    from llm import _TOOL_DECLARATIONS, get_rate_info, MODEL_NAME

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
        "rate_info": get_rate_info(),
        "github_repo": GITHUB_REPO,
        "config": {
            "llm": cfg.get("llm", {}),
            "security": cfg.get("security", {}),
            "phase": cfg.get("phase", "?"),
        },
        "spending": {
            "total_cost": round(sp.total_cost, 6),
            "budget_limit": sp._data["budget_limit"],
            "budget_remaining": round(sp.budget_remaining, 6),
            "budget_pct": round(sp.budget_pct_used, 2),
            "total_input_tokens": sp._data["total_input_tokens"],
            "total_output_tokens": sp._data["total_output_tokens"],
            "calls": sp._data["calls"],
            "daily": sp._data.get("daily", {}),
        },
        "skills": skills_list,
        "skill_count": len(skills_list),
        "commands": _command_list(),
    }
    return web.json_response(payload)


def _command_list() -> list[dict]:
    """Static command reference grouped by category."""
    return [
        {"category": "Foundation", "commands": [
            {"name": "/ping", "desc": "Check if bot is alive"},
            {"name": "/about", "desc": "Version and system info"},
            {"name": "/whoami", "desc": "Your Discord identity & permissions"},
            {"name": "/help", "desc": "List all commands"},
        ]},
        {"category": "Docker & System", "commands": [
            {"name": "/containers", "desc": "List running containers"},
            {"name": "/status <service>", "desc": "Container detail + resources"},
            {"name": "/logs <service> [lines]", "desc": "View container logs"},
            {"name": "/system", "desc": "CPU, memory, disk usage"},
            {"name": "/dockerstats", "desc": "Per-container resource usage"},
            {"name": "/restart <service>", "desc": "Restart a container (requires approval)"},
        ]},
        {"category": "AI & LLM", "commands": [
            {"name": "/ask <question>", "desc": "AI-powered query with function calling"},
            {"name": "/clear", "desc": "Clear conversation history"},
            {"name": "/analyze <service> [lines]", "desc": "AI log analysis"},
        ]},
        {"category": "Media & Downloads", "commands": [
            {"name": "/search <query> [type]", "desc": "Search Sonarr/Radarr catalogs"},
            {"name": "/queue", "desc": "Active downloads (SABnzbd + qBit)"},
            {"name": "/recent [count]", "desc": "Recently added Plex media"},
            {"name": "/health", "desc": "Check *arr + download client health"},
            {"name": "/ports", "desc": "Service port connectivity check"},
            {"name": "/report", "desc": "Comprehensive status report"},
        ]},
        {"category": "Memory & Automation", "commands": [
            {"name": "/remember <fact> [tags]", "desc": "Store a fact in long-term memory"},
            {"name": "/recall <query>", "desc": "Search long-term memory"},
            {"name": "/schedule", "desc": "Manage scheduled tasks"},
            {"name": "/skills", "desc": "List all LLM-callable skills"},
        ]},
        {"category": "Network & Monitoring", "commands": [
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
    ]


# ---------------------------------------------------------------------------
# HTML dashboard (self-contained, no external deps)
# ---------------------------------------------------------------------------


async def dashboard_handler(request: web.Request) -> web.Response:
    """Serve the dashboard HTML page."""
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw Dashboard</title>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
    --purple: #bc8cff;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
         background: var(--bg); color: var(--text); line-height: 1.5; padding: 1.5rem; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  h1 { font-size: 1.6rem; margin-bottom: 0.25rem; }
  h2 { font-size: 1.1rem; color: var(--accent); margin-bottom: 0.75rem; border-bottom: 1px solid var(--border); padding-bottom: 0.4rem; }
  .header { display: flex; align-items: center; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
  .header-right { margin-left: auto; display: flex; gap: 0.75rem; align-items: center; }
  .badge { display: inline-block; padding: 0.2rem 0.6rem; border-radius: 1rem; font-size: 0.75rem; font-weight: 600; }
  .badge-green { background: rgba(63,185,80,0.15); color: var(--green); }
  .badge-blue { background: rgba(88,166,255,0.15); color: var(--accent); }
  .badge-purple { background: rgba(188,140,255,0.15); color: var(--purple); }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 0.5rem; padding: 1rem; }
  .stat-row { display: flex; justify-content: space-between; padding: 0.3rem 0; border-bottom: 1px solid var(--border); }
  .stat-row:last-child { border-bottom: none; }
  .stat-label { color: var(--muted); }
  .stat-value { font-weight: 600; font-variant-numeric: tabular-nums; }
  .progress-bar { width: 100%; height: 1.25rem; background: var(--border); border-radius: 0.625rem; overflow: hidden; margin: 0.5rem 0; }
  .progress-fill { height: 100%; border-radius: 0.625rem; transition: width 0.5s; display: flex; align-items: center; justify-content: center; font-size: 0.7rem; font-weight: 700; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { text-align: left; padding: 0.5rem; border-bottom: 2px solid var(--border); color: var(--muted); font-weight: 600; }
  td { padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--border); }
  tr:hover td { background: rgba(88,166,255,0.04); }
  .cmd-cat { color: var(--accent); font-weight: 600; padding-top: 0.75rem; }
  code { background: rgba(88,166,255,0.1); padding: 0.15rem 0.4rem; border-radius: 0.25rem; font-size: 0.82rem; }
  .links { display: flex; gap: 1rem; flex-wrap: wrap; margin-top: 0.5rem; }
  .links a { padding: 0.4rem 0.8rem; border: 1px solid var(--border); border-radius: 0.375rem; font-size: 0.85rem; }
  .links a:hover { border-color: var(--accent); background: rgba(88,166,255,0.08); text-decoration: none; }
  #loading { text-align: center; padding: 3rem; color: var(--muted); }
  .chart-bar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.3rem; }
  .chart-bar-inner { height: 1rem; background: var(--accent); border-radius: 0.25rem; min-width: 2px; transition: width 0.3s; }
  .chart-label { font-size: 0.75rem; color: var(--muted); min-width: 5.5rem; }
  .chart-value { font-size: 0.75rem; font-variant-numeric: tabular-nums; }
  @media (max-width: 600px) { .grid { grid-template-columns: 1fr; } body { padding: 0.75rem; } }
</style>
</head>
<body>
<div id="loading">Loading dashboard data&hellip;</div>
<div id="app" style="display:none">

  <!-- Header -->
  <div class="header">
    <div>
      <h1>&#128033; OpenClaw Dashboard</h1>
      <span id="subtitle" class="badge badge-blue"></span>
    </div>
    <div class="header-right">
      <span id="status-badge" class="badge badge-green">Healthy</span>
      <span id="uptime-badge" class="badge badge-purple"></span>
    </div>
  </div>

  <!-- Quick Links -->
  <div class="links" style="margin-bottom:1.25rem">
    <a id="link-github" href="#" target="_blank">&#128279; GitHub Repo</a>
    <a id="link-health" href="/health" target="_blank">&#128154; Health</a>
    <a id="link-metrics" href="/metrics" target="_blank">&#128200; Prometheus</a>
    <a id="link-api" href="/api/dashboard" target="_blank">&#128203; API JSON</a>
  </div>

  <!-- Top cards -->
  <div class="grid">
    <!-- Spending -->
    <div class="card">
      <h2>&#128176; Gemini Spending</h2>
      <div class="stat-row"><span class="stat-label">Total Cost</span><span class="stat-value" id="sp-cost"></span></div>
      <div class="stat-row"><span class="stat-label">Budget</span><span class="stat-value" id="sp-budget"></span></div>
      <div class="progress-bar"><div class="progress-fill" id="sp-bar"></div></div>
      <div class="stat-row"><span class="stat-label">Input Tokens</span><span class="stat-value" id="sp-in"></span></div>
      <div class="stat-row"><span class="stat-label">Output Tokens</span><span class="stat-value" id="sp-out"></span></div>
      <div class="stat-row"><span class="stat-label">API Calls</span><span class="stat-value" id="sp-calls"></span></div>
      <div class="stat-row"><span class="stat-label">Avg / Call</span><span class="stat-value" id="sp-avg"></span></div>
      <div class="stat-row"><span class="stat-label">Est. Calls Left</span><span class="stat-value" id="sp-remaining"></span></div>
    </div>

    <!-- System -->
    <div class="card">
      <h2>&#9881;&#65039; System Info</h2>
      <div class="stat-row"><span class="stat-label">Version</span><span class="stat-value" id="sys-version"></span></div>
      <div class="stat-row"><span class="stat-label">Phase</span><span class="stat-value" id="sys-phase"></span></div>
      <div class="stat-row"><span class="stat-label">Bot User</span><span class="stat-value" id="sys-bot"></span></div>
      <div class="stat-row"><span class="stat-label">Model</span><span class="stat-value" id="sys-model"></span></div>
      <div class="stat-row"><span class="stat-label">Rate Limits</span><span class="stat-value" id="sys-rates"></span></div>
      <div class="stat-row"><span class="stat-label">Python</span><span class="stat-value" id="sys-python"></span></div>
      <div class="stat-row"><span class="stat-label">discord.py</span><span class="stat-value" id="sys-discordpy"></span></div>
      <div class="stat-row"><span class="stat-label">Latency</span><span class="stat-value" id="sys-latency"></span></div>
    </div>
  </div>

  <!-- Daily spending chart -->
  <div class="card" style="margin-bottom:1.25rem">
    <h2>&#128202; Daily Spending</h2>
    <div id="daily-chart"><span style="color:var(--muted)">No data yet — spending will appear after first /ask</span></div>
  </div>

  <!-- Skills -->
  <div class="card" style="margin-bottom:1.25rem">
    <h2>&#129520; Skills (<span id="skill-count">0</span>)</h2>
    <table>
      <thead><tr><th>#</th><th>Skill</th><th>Description</th></tr></thead>
      <tbody id="skills-body"></tbody>
    </table>
  </div>

  <!-- Commands -->
  <div class="card" style="margin-bottom:1.25rem">
    <h2>&#128172; Discord Commands</h2>
    <table>
      <thead><tr><th>Command</th><th>Description</th></tr></thead>
      <tbody id="commands-body"></tbody>
    </table>
  </div>

  <!-- Config -->
  <div class="card">
    <h2>&#128272; Security & Config</h2>
    <div id="config-rows"></div>
  </div>

</div>

<script>
(async () => {
  try {
    const r = await fetch('/api/dashboard');
    const d = await r.json();

    // Header
    document.getElementById('subtitle').textContent = `v${d.version} \u2022 ${d.skill_count} skills \u2022 ${d.commands.reduce((a,c) => a + c.commands.length, 0)} commands`;
    document.getElementById('uptime-badge').textContent = formatUptime(d.uptime_seconds);
    document.getElementById('link-github').href = d.github_repo;

    // Spending
    const sp = d.spending;
    document.getElementById('sp-cost').textContent = `$${sp.total_cost.toFixed(4)}`;
    document.getElementById('sp-budget').textContent = `$${sp.budget_remaining.toFixed(4)} / $${sp.budget_limit.toFixed(2)}`;
    const pct = sp.budget_pct;
    const bar = document.getElementById('sp-bar');
    bar.style.width = Math.max(pct, 1) + '%';
    bar.style.background = pct < 50 ? 'var(--green)' : pct < 80 ? 'var(--yellow)' : 'var(--red)';
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
      document.getElementById('sp-remaining').textContent = '\u2014';
    }

    // Daily chart
    const days = Object.entries(sp.daily).sort((a,b) => b[0].localeCompare(a[0])).slice(0, 14);
    if (days.length > 0) {
      const maxCost = Math.max(...days.map(([,v]) => v.cost_usd), 0.001);
      const chartEl = document.getElementById('daily-chart');
      chartEl.innerHTML = '';
      days.reverse().forEach(([day, v]) => {
        const pct = (v.cost_usd / maxCost) * 100;
        chartEl.innerHTML += `<div class="chart-bar"><span class="chart-label">${day.slice(5)}</span><div class="chart-bar-inner" style="width:${Math.max(pct,1)}%"></div><span class="chart-value">$${v.cost_usd.toFixed(4)} (${v.calls} calls)</span></div>`;
      });
    }

    // System
    document.getElementById('sys-version').textContent = d.version;
    document.getElementById('sys-phase').textContent = d.config.phase;
    document.getElementById('sys-bot').textContent = d.bot_user || 'N/A';
    document.getElementById('sys-model').textContent = d.model;
    document.getElementById('sys-rates').textContent = d.rate_info;
    document.getElementById('sys-python').textContent = d.python;
    document.getElementById('sys-discordpy').textContent = d.discord_py;
    document.getElementById('sys-latency').textContent = d.latency_ms + ' ms';

    // Skills table
    document.getElementById('skill-count').textContent = d.skill_count;
    const sb = document.getElementById('skills-body');
    d.skills.forEach((s, i) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${i+1}</td><td><code>${s.name}</code></td><td>${esc(s.description).slice(0, 120)}</td>`;
      sb.appendChild(tr);
    });

    // Commands table
    const cb = document.getElementById('commands-body');
    d.commands.forEach(cat => {
      const hdr = document.createElement('tr');
      hdr.innerHTML = `<td class="cmd-cat" colspan="2">${esc(cat.category)}</td>`;
      cb.appendChild(hdr);
      cat.commands.forEach(c => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td><code>${esc(c.name)}</code></td><td>${esc(c.desc)}</td>`;
        cb.appendChild(tr);
      });
    });

    // Config
    const cr = document.getElementById('config-rows');
    const sec = d.config.security || {};
    const llm = d.config.llm || {};
    const cfgItems = [
      ['Sandbox Mode', sec.sandbox_mode ? 'Enabled' : 'Disabled'],
      ['Require Approval', sec.require_approval ? 'Yes' : 'No'],
      ['Audit Logging', sec.audit_logging ? 'Enabled' : 'Disabled'],
      ['Max Tokens', llm.max_tokens || '?'],
      ['Temperature', llm.temperature || '?'],
      ['Max Tool Rounds', llm.max_tool_rounds || '?'],
      ['Conversation TTL', (llm.conversation?.ttl_minutes || '?') + ' min'],
      ['Max History', llm.conversation?.max_history || '?'],
    ];
    cfgItems.forEach(([k,v]) => {
      cr.innerHTML += `<div class="stat-row"><span class="stat-label">${k}</span><span class="stat-value">${v}</span></div>`;
    });

    document.getElementById('loading').style.display = 'none';
    document.getElementById('app').style.display = 'block';
  } catch(e) {
    document.getElementById('loading').textContent = 'Failed to load: ' + e.message;
  }
})();

function formatUptime(s) {
  const d = Math.floor(s/86400), h = Math.floor(s%86400/3600), m = Math.floor(s%3600/60);
  return d > 0 ? `${d}d ${h}h ${m}m` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
</script>
</body>
</html>
"""
