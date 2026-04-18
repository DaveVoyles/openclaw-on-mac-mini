#Requires -Version 5.1
<#
.SYNOPSIS
    OpenClaw Windows Uninstaller.
    Removes the Scheduled Task and optionally cleans up log files.

.DESCRIPTION
    Run this from a normal PowerShell window (no "Run as Administrator" needed).

    What it does:
      1. Stops and removes the "OpenClaw Watcher" Scheduled Task
      2. Clears the OPENCLAW_* user environment variables
      3. Optionally removes %APPDATA%\OpenClaw\ (asks before deleting)

.EXAMPLE
    .\Uninstall-OpenClaw.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$TASK_NAME = "OpenClaw Watcher"
$LOG_DIR   = "$env:APPDATA\OpenClaw"

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  OpenClaw Windows Uninstaller" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Remove Scheduled Task ─────────────────────────────────────────────
Write-Host "Removing Scheduled Task '$TASK_NAME'..." -ForegroundColor White
try {
    $existing = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
    if ($existing) {
        # Stop the task if it is currently running
        Stop-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false
        Write-Host "  ✅ Scheduled Task removed." -ForegroundColor Green
    } else {
        Write-Host "  ℹ️  No task named '$TASK_NAME' found — skipping." -ForegroundColor Yellow
    }
} catch {
    Write-Host "  ⚠️  Could not remove Scheduled Task: $_" -ForegroundColor Yellow
    Write-Host "     You can remove it manually in Task Scheduler." -ForegroundColor Yellow
}
Write-Host ""

# ── Step 2: Clear user environment variables ───────────────────────────────────
Write-Host "Removing OpenClaw environment variables..." -ForegroundColor White
foreach ($key in @("OPENCLAW_REMOTE_HOST", "OPENCLAW_SLACK_TOKEN", "OPENCLAW_SLACK_CHANNEL")) {
    try {
        [System.Environment]::SetEnvironmentVariable($key, $null, "User")
        Write-Host "  ✅ Removed: $key" -ForegroundColor Green
    } catch {
        Write-Host "  ⚠️  Could not remove $key : $_" -ForegroundColor Yellow
    }
}
Write-Host ""

# ── Step 3: Optionally remove log folder ──────────────────────────────────────
if (Test-Path $LOG_DIR) {
    Write-Host "Log folder found: $LOG_DIR" -ForegroundColor White
    $answer = Read-Host "  Remove it and all logs inside? [y/N]"
    if ($answer -match "^[Yy]") {
        try {
            Remove-Item -Path $LOG_DIR -Recurse -Force
            Write-Host "  ✅ Log folder removed." -ForegroundColor Green
        } catch {
            Write-Host "  ⚠️  Could not remove log folder: $_" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  Logs kept at: $LOG_DIR" -ForegroundColor White
    }
}
Write-Host ""

# ── Done ───────────────────────────────────────────────────────────────────────
Write-Host "OpenClaw watcher removed." -ForegroundColor Green
Write-Host ""
Write-Host "Your Documents\OpenClaw\ folder was left in place." -ForegroundColor White
Write-Host "Delete it manually if you no longer need it." -ForegroundColor White
Write-Host ""
