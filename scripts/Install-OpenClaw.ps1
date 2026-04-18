#Requires -Version 5.1
<#
.SYNOPSIS
    OpenClaw Windows Installer.
    One-click setup: creates the watch folder, saves your config, and registers
    a Scheduled Task so the watcher starts automatically at login (no admin required).

.DESCRIPTION
    Run this once from a normal PowerShell window (no "Run as Administrator" needed).

    What it does:
      1. Creates Documents\OpenClaw\          — your drop folder
      2. Creates %APPDATA%\OpenClaw\          — log storage
      3. Saves Mac Mini host + optional Slack token to user environment variables
      4. Checks whether WSL / rsync is available and warns if not
      5. Registers the "OpenClaw Watcher" Scheduled Task (runs at every login)
      6. Starts the watcher immediately so you can test right away

.EXAMPLE
    .\Install-OpenClaw.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$SCRIPT_DIR   = $PSScriptRoot
$WATCHER      = Join-Path $SCRIPT_DIR "Watch-OpenClaw.ps1"
$WATCH_FOLDER = "$env:USERPROFILE\Documents\OpenClaw"
$LOG_DIR      = "$env:APPDATA\OpenClaw"
$TASK_NAME    = "OpenClaw Watcher"

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  OpenClaw Windows Setup" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# ── Verify watcher script is present ──────────────────────────────────────────
if (-not (Test-Path $WATCHER)) {
    Write-Host "❌ Cannot find Watch-OpenClaw.ps1 next to this installer." -ForegroundColor Red
    Write-Host "   Expected: $WATCHER" -ForegroundColor Red
    Write-Host "   Make sure both scripts are in the same folder." -ForegroundColor Red
    exit 1
}

# ── Step 1: Create folders ─────────────────────────────────────────────────────
Write-Host "Creating folders..." -ForegroundColor White
New-Item -ItemType Directory -Force -Path $WATCH_FOLDER | Out-Null
New-Item -ItemType Directory -Force -Path $LOG_DIR      | Out-Null
Write-Host "  ✅ Watch folder : $WATCH_FOLDER" -ForegroundColor Green
Write-Host "  ✅ Log folder   : $LOG_DIR" -ForegroundColor Green
Write-Host ""

# ── Step 2: Mac Mini hostname ──────────────────────────────────────────────────
Write-Host "Mac Mini setup" -ForegroundColor White
$remoteHost = Read-Host "  Mac Mini hostname or SSH alias [press Enter for: macmini]"
if ([string]::IsNullOrWhiteSpace($remoteHost)) { $remoteHost = "macmini" }
Write-Host ""

# ── Step 3: Optional Slack fallback ───────────────────────────────────────────
Write-Host "Slack fallback (optional — press Enter to skip)" -ForegroundColor White
$slackToken   = Read-Host "  Slack bot token (OPENCLAW_SLACK_TOKEN)"
$slackChannel = ""
if (-not [string]::IsNullOrWhiteSpace($slackToken)) {
    $slackChannel = Read-Host "  Slack channel or DM ID to upload files to"
}
Write-Host ""

# ── Step 4: Save config to user environment variables (no admin) ───────────────
Write-Host "Saving configuration..." -ForegroundColor White
[System.Environment]::SetEnvironmentVariable("OPENCLAW_REMOTE_HOST",   $remoteHost,   "User")
[System.Environment]::SetEnvironmentVariable("OPENCLAW_SLACK_TOKEN",   $slackToken,   "User")
[System.Environment]::SetEnvironmentVariable("OPENCLAW_SLACK_CHANNEL", $slackChannel, "User")
# Make them available in the current session too
$env:OPENCLAW_REMOTE_HOST   = $remoteHost
$env:OPENCLAW_SLACK_TOKEN   = $slackToken
$env:OPENCLAW_SLACK_CHANNEL = $slackChannel
Write-Host "  ✅ Config saved to user environment variables." -ForegroundColor Green
Write-Host ""

# ── Step 5: Check WSL availability ────────────────────────────────────────────
Write-Host "Checking sync method..." -ForegroundColor White
$wslAvailable = $false
try {
    $null = & wsl --status 2>&1
    $wslAvailable = ($LASTEXITCODE -eq 0)
} catch {}

if ($wslAvailable) {
    Write-Host "  ✅ WSL detected — primary sync will use rsync over SSH." -ForegroundColor Green

    # Test SSH reachability from within WSL
    try {
        $sshOut = & wsl bash -c "ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new $remoteHost 'echo ok' 2>&1"
        if ($sshOut -match "ok") {
            Write-Host "  ✅ SSH connection to '$remoteHost' verified." -ForegroundColor Green
        } else {
            Write-Host ""
            Write-Host "  ⚠️  WSL found, but could not reach '$remoteHost' via SSH." -ForegroundColor Yellow
            Write-Host "     To set up SSH keys, open a WSL terminal and run:" -ForegroundColor Yellow
            Write-Host "       ssh-keygen -t ed25519 -C 'openclaw-windows'" -ForegroundColor Yellow
            Write-Host "       ssh-copy-id $remoteHost" -ForegroundColor Yellow
            Write-Host "     Until then, HTTP upload will be used as a fallback." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  ⚠️  Could not test SSH from WSL: $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "  ⚠️  WSL not found — will use HTTP upload to http://${remoteHost}:8080/upload instead." -ForegroundColor Yellow
    Write-Host "     To install WSL later: open PowerShell and run  wsl --install" -ForegroundColor Yellow
}
Write-Host ""

# ── Step 6: Register Scheduled Task (no admin required) ───────────────────────
Write-Host "Registering Scheduled Task '$TASK_NAME'..." -ForegroundColor White

$taskArg = "-NoProfile -NonInteractive -WindowStyle Hidden " +
           "-File `"$WATCHER`" " +
           "-RemoteHost `"$remoteHost`""

$action    = New-ScheduledTaskAction `
    -Execute  "powershell.exe" `
    -Argument $taskArg

$trigger   = New-ScheduledTaskTrigger -AtLogOn

$settings  = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId   $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

try {
    Register-ScheduledTask `
        -TaskName  $TASK_NAME `
        -Action    $action `
        -Trigger   $trigger `
        -Settings  $settings `
        -Principal $principal `
        -Force | Out-Null

    Write-Host "  ✅ Task '$TASK_NAME' registered — starts automatically at every login." -ForegroundColor Green
} catch {
    Write-Host "  ❌ Could not register Scheduled Task: $_" -ForegroundColor Red
    Write-Host "     You can start the watcher manually by double-clicking Watch-OpenClaw.ps1." -ForegroundColor Yellow
}
Write-Host ""

# ── Step 7: Remind about execution policy ─────────────────────────────────────
$policy = Get-ExecutionPolicy -Scope CurrentUser
if ($policy -eq "Restricted" -or $policy -eq "Undefined") {
    Write-Host "⚠️  PowerShell script execution is currently restricted." -ForegroundColor Yellow
    Write-Host "   Run this command to allow scripts for your account only:" -ForegroundColor Yellow
    Write-Host "     Set-ExecutionPolicy RemoteSigned -Scope CurrentUser" -ForegroundColor Yellow
    Write-Host ""
}

# ── Step 8: Test the HTTP upload endpoint ────────────────────────────────────
Write-Host ""
Write-Host "Step 8: Testing HTTP upload endpoint..." -ForegroundColor White
$uploadUrl = "http://${remoteHost}:8080/upload"
try {
    $resp = Invoke-WebRequest -Uri "http://${remoteHost}:8080/health" -TimeoutSec 5 -ErrorAction Stop
    if ($resp.StatusCode -eq 200) {
        Write-Host "  ✅ Upload server reachable at $uploadUrl" -ForegroundColor Green
    } else {
        Write-Host "  ⚠️  Upload server responded with HTTP $($resp.StatusCode)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  ⚠️  Upload server not reachable at $uploadUrl" -ForegroundColor Yellow
    Write-Host "     This is expected if OpenClaw isn't running yet, or if WSL sync is used instead." -ForegroundColor Gray
}

# ── Step 9: Start watcher now for an immediate test ───────────────────────────
Write-Host "Starting the watcher now for a quick test..." -ForegroundColor White
try {
    Start-Process powershell.exe `
        -ArgumentList "-NoProfile -NonInteractive -WindowStyle Minimized -File `"$WATCHER`" -RemoteHost `"$remoteHost`"" `
        -WindowStyle Minimized
    Write-Host "  ✅ Watcher started in the background." -ForegroundColor Green
} catch {
    Write-Host "  ⚠️  Could not start watcher automatically: $_" -ForegroundColor Yellow
    Write-Host "     Run Watch-OpenClaw.ps1 manually to start it." -ForegroundColor Yellow
}

# ── Done ───────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "  🎉 OpenClaw is set up!" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Drop Word docs into:" -ForegroundColor White
Write-Host "    $WATCH_FOLDER" -ForegroundColor Cyan
Write-Host "  and they'll sync automatically to OpenClaw." -ForegroundColor White
Write-Host ""
Write-Host "  Logs: $LOG_DIR\watcher.log" -ForegroundColor White
Write-Host ""
