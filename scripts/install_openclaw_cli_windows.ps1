#Requires -Version 5.1
<#
.SYNOPSIS
    Installs the OpenClaw CLI on Windows.
.DESCRIPTION
    Downloads openclaw_cli.py and required support modules, creates a launcher
    wrapper in %USERPROFILE%\.openclaw\bin, and adds it to the user PATH.
.EXAMPLE
    irm https://openclaw.davevoyles.synology.me/install.ps1 | iex
.EXAMPLE
    $env:OPENCLAW_URL = "http://192.168.1.93:8765"; irm $env:OPENCLAW_URL/install.ps1 | iex
#>

$ErrorActionPreference = "Stop"

# ── Constants ────────────────────────────────────────────────────────────────

$DEFAULT_URL  = "https://openclaw.davevoyles.synology.me"
$BASE_URL     = if ($env:OPENCLAW_URL) { $env:OPENCLAW_URL.TrimEnd('/') } else { $DEFAULT_URL }
$INSTALL_DIR  = "$env:USERPROFILE\.openclaw"
$BIN_DIR      = "$INSTALL_DIR\bin"

$SUPPORT_FILES = @(
    "openclaw_cli_sessions.py",
    "openclaw_cli_actions.py",
    "openclaw_cli_cmd_core.py",
    "subprocess_utils.py"
)

# ── Helpers ──────────────────────────────────────────────────────────────────

function Write-Step {
    param([string]$Msg)
    Write-Host "  ✔  $Msg" -ForegroundColor Cyan
}

function Write-Banner {
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor DarkCyan
    Write-Host "  ║      OpenClaw CLI — Windows Setup     ║" -ForegroundColor DarkCyan
    Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor DarkCyan
    Write-Host ""
}

function Download-File {
    param([string]$Url, [string]$Dest)
    Write-Host "      → $Url" -ForegroundColor DarkGray
    Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
}

# ── 1. Check Python ───────────────────────────────────────────────────────────

Write-Banner

Write-Host "  Checking Python 3 …" -ForegroundColor Yellow
$pythonCmd = $null
foreach ($candidate in @("python", "python3")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python 3\.") {
            $pythonCmd = $candidate
            break
        }
    } catch { }
}

if (-not $pythonCmd) {
    Write-Host ""
    Write-Host "  ✘  Python 3 not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Install it with winget, then re-run this script:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "      winget install Python.Python.3" -ForegroundColor White
    Write-Host ""
    Write-Host "  Or download from https://www.python.org/downloads/" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}

$pythonVersion = & $pythonCmd --version 2>&1
Write-Step "Found $pythonVersion  ($pythonCmd)"

# ── 2. Create install directory ───────────────────────────────────────────────

if (-not (Test-Path $INSTALL_DIR)) {
    New-Item -ItemType Directory -Path $INSTALL_DIR | Out-Null
}
if (-not (Test-Path $BIN_DIR)) {
    New-Item -ItemType Directory -Path $BIN_DIR | Out-Null
}
Write-Step "Install directory: $INSTALL_DIR"

# ── 3. Download openclaw_cli.py ───────────────────────────────────────────────

Write-Host "  Downloading openclaw_cli.py …" -ForegroundColor Yellow
Download-File "$BASE_URL/downloads/openclaw_cli.py" "$INSTALL_DIR\openclaw_cli.py"
Write-Step "Downloaded openclaw_cli.py"

# ── 4. Download support modules ───────────────────────────────────────────────

Write-Host "  Downloading support modules …" -ForegroundColor Yellow
foreach ($file in $SUPPORT_FILES) {
    Download-File "$BASE_URL/downloads/openclaw-cli-support/$file" "$INSTALL_DIR\$file"
    Write-Step "Downloaded $file"
}

# ── 5. Create openclaw.cmd wrapper ────────────────────────────────────────────

$cmdContent = @"
@echo off
if "%OPENCLAW_URL%"=="" set OPENCLAW_URL=$DEFAULT_URL
$pythonCmd "%USERPROFILE%\.openclaw\openclaw_cli.py" %*
"@

$cmdPath = "$BIN_DIR\openclaw.cmd"
Set-Content -Path $cmdPath -Value $cmdContent -Encoding ASCII
Write-Step "Created launcher: $cmdPath"

# ── 6. Add bin dir to user PATH ───────────────────────────────────────────────

$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($currentPath -notlike "*$BIN_DIR*") {
    [Environment]::SetEnvironmentVariable(
        "PATH",
        "$currentPath;$BIN_DIR",
        "User"
    )
    Write-Step "Added $BIN_DIR to user PATH"
} else {
    Write-Step "$BIN_DIR already in user PATH"
}

# ── 7. Install prompt_toolkit (optional) ──────────────────────────────────────

Write-Host "  Installing prompt_toolkit (optional) …" -ForegroundColor Yellow
try {
    & $pythonCmd -m pip install --quiet prompt_toolkit
    Write-Step "prompt_toolkit installed"
} catch {
    Write-Host "  ⚠  prompt_toolkit install skipped (pip error — CLI will still work)" -ForegroundColor DarkYellow
}

# ── 8. Done ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║  ✅  OpenClaw CLI installed successfully!                 ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Open a NEW terminal window and run:" -ForegroundColor Yellow
Write-Host ""
Write-Host "      openclaw" -ForegroundColor White
Write-Host ""
Write-Host "  Server URL: $BASE_URL" -ForegroundColor DarkGray
Write-Host "  To use a different server, set:" -ForegroundColor DarkGray
Write-Host "      `$env:OPENCLAW_URL = `"http://your-server:8765`"" -ForegroundColor DarkGray
Write-Host ""
