<#
.SYNOPSIS
    OpenClaw Windows Installer.
    Creates the watched folder, configures environment, and registers a Scheduled Task
    that launches the watcher at user login (no admin rights required).
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$SCRIPT_DIR    = $PSScriptRoot
$WATCHER       = "$SCRIPT_DIR\Watch-OpenClaw.ps1"
$WATCH_FOLDER  = "$env:USERPROFILE\Documents\OpenClaw"
$TASK_NAME     = "OpenClaw-Watcher"
$LOG_DIR       = "$env:APPDATA\OpenClaw"

Write-Host "=== OpenClaw Windows Setup ===" -ForegroundColor Cyan

# 1. Create folders
New-Item -ItemType Directory -Force -Path $WATCH_FOLDER | Out-Null
New-Item -ItemType Directory -Force -Path $LOG_DIR      | Out-Null
Write-Host "✅ Watch folder: $WATCH_FOLDER"

# 2. Ask for Mac Mini hostname (or SSH alias)
$remoteHost = Read-Host "Mac Mini hostname or SSH alias [default: macmini]"
if (-not $remoteHost) { $remoteHost = "macmini" }

# 3. Optional: Slack token for fallback upload
$slackToken   = Read-Host "Slack bot token for fallback (leave blank to skip)"
$slackChannel = ""
if ($slackToken) {
    $slackChannel = Read-Host "Slack channel/DM ID for file upload"
}

# 4. Persist config to user environment variables (no admin required)
[System.Environment]::SetEnvironmentVariable("OPENCLAW_REMOTE_HOST",   $remoteHost,   "User")
[System.Environment]::SetEnvironmentVariable("OPENCLAW_SLACK_TOKEN",   $slackToken,   "User")
[System.Environment]::SetEnvironmentVariable("OPENCLAW_SLACK_CHANNEL", $slackChannel, "User")
Write-Host "✅ Configuration saved to user environment variables."

# 5. Verify WSL availability
$wslAvailable = $false
try {
    wsl --status 2>&1 | Out-Null
    $wslAvailable = ($LASTEXITCODE -eq 0)
} catch {}

if ($wslAvailable) {
    Write-Host "✅ WSL detected — primary sync will use rsync."
    # Test SSH connectivity
    $sshTest = wsl bash -c "ssh -o ConnectTimeout=5 -o BatchMode=yes $remoteHost 'echo ok' 2>&1"
    if ($sshTest -ne "ok") {
        Write-Warning "⚠️  Cannot reach $remoteHost via SSH from WSL. See README-Windows.md for SSH key setup."
    } else {
        Write-Host "✅ SSH connection to $remoteHost verified."
    }
} else {
    Write-Host "⚠️  WSL not found — will use HTTP upload or Slack fallback." -ForegroundColor Yellow
}

# 6. Register Scheduled Task (no admin required: /SC ONLOGON /RU CURRENTUSER)
$action    = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$WATCHER`" -RemoteHost `"$remoteHost`""
$trigger   = New-ScheduledTaskTrigger -AtLogOn
$settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName  $TASK_NAME `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "✅ Scheduled Task '$TASK_NAME' registered (runs at login, no admin required)."

# 7. Start watcher now for immediate test
Write-Host "🚀 Starting watcher now for a quick test..."
Start-Process powershell.exe `
    -ArgumentList "-NoProfile -NonInteractive -WindowStyle Minimized -File `"$WATCHER`" -RemoteHost `"$remoteHost`"" `
    -WindowStyle Minimized

Write-Host ""
Write-Host "Done! Drop a .docx or .xlsx into:" -ForegroundColor Green
Write-Host "  $WATCH_FOLDER" -ForegroundColor Green
Write-Host "It will sync to OpenClaw within seconds."
Write-Host "Logs: $LOG_DIR\openclaw-watcher.log"
