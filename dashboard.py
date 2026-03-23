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
    --surface: rgba(0, 45, 114, 0.35);  /* heritage blue glass */
    --surface-solid: #001e4d;
    --border: rgba(104, 172, 229, 0.2);
    --text: #e8f0fe;
    --muted: rgba(104, 172, 229, 0.7);
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
      <div class="logo">&#129490;</div>
      <div>
        <h1>OpenClaw</h1>
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
    <a class="btn" href="/health" target="_blank"><span class="btn-icon">&#128154;</span> Health</a>
    <a class="btn" href="/metrics" target="_blank"><span class="btn-icon">&#128200;</span> Prometheus</a>
    <a class="btn btn-gold" href="/api/dashboard" target="_blank"><span class="btn-icon">&#128203;</span> API JSON</a>
    <button class="btn btn-refresh" onclick="refreshData(this)"><span class="btn-icon">&#8635;</span> Refresh</button>
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
    </div>

    <!-- System -->
    <div class="card">
      <h2>&#9881;&#65039; System Info</h2>
      <div class="stat-row"><span class="stat-label">Version</span><span class="stat-value" id="sys-version"></span></div>
      <div class="stat-row"><span class="stat-label">Phase</span><span class="stat-value" id="sys-phase"></span></div>
      <div class="stat-row"><span class="stat-label">Bot User</span><span class="stat-value" id="sys-bot" style="color:var(--spirit-blue)"></span></div>
      <div class="stat-row"><span class="stat-label">Model</span><span class="stat-value" id="sys-model" style="color:var(--gold)"></span></div>
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

  <!-- Skills -->
  <div class="card">
    <h2>&#129520; Skills (<span id="skill-count">0</span>)</h2>
    <input type="text" class="search-box" id="skill-search" placeholder="&#128269; Search skills..." oninput="filterSkills()">
    <table>
      <thead><tr><th style="width:2rem">#</th><th style="width:12rem">Skill</th><th>Description</th></tr></thead>
      <tbody id="skills-body"></tbody>
    </table>
  </div>

  <!-- Commands -->
  <div class="card">
    <h2>&#128172; Discord Commands</h2>
    <input type="text" class="search-box" id="cmd-search" placeholder="&#128269; Search commands..." oninput="filterCmds()">
    <table>
      <thead><tr><th style="width:14rem">Command</th><th>Description</th></tr></thead>
      <tbody id="commands-body"></tbody>
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
  document.getElementById('subtitle').textContent = `v${d.version} \u2022 ${d.skill_count} skills \u2022 ${cmdCount} commands`;
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
  document.getElementById('sys-rates').textContent = d.rate_info;
  document.getElementById('sys-python').textContent = d.python;
  document.getElementById('sys-discordpy').textContent = d.discord_py;
  document.getElementById('sys-latency').textContent = d.latency_ms + ' ms';

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
