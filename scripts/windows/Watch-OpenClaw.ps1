<#
.SYNOPSIS
    OpenClaw Windows Folder Watcher.
    Watches Documents\OpenClaw\ and syncs new .docx/.xlsx files to macmini:/ai-files/
    via rsync (WSL) or uploads them to Slack directly as a fallback.

.DESCRIPTION
    This script is designed to be run as a Scheduled Task at login (no admin required).
    It uses FileSystemWatcher (.NET built-in) for near-instant detection.
    Primary sync: rsync via WSL if available.
    Fallback: HTTP upload to the /upload endpoint on macmini OR direct Slack file upload.

.PARAMETER WatchFolder
    The folder to monitor. Defaults to $env:USERPROFILE\Documents\OpenClaw

.PARAMETER RemoteHost
    SSH host alias for macmini. Defaults to "macmini".

.PARAMETER SlackToken
    Slack bot token for fallback upload. Set via $env:OPENCLAW_SLACK_TOKEN.

.PARAMETER SlackChannel
    Slack channel/DM ID to upload to. Set via $env:OPENCLAW_SLACK_CHANNEL.

.PARAMETER UploadEndpoint
    HTTP endpoint on Mac Mini for fallback upload. Defaults to http://macmini:8080/upload

.EXAMPLE
    .\Watch-OpenClaw.ps1
    .\Watch-OpenClaw.ps1 -WatchFolder "D:\MyDocs\OpenClaw" -RemoteHost "mymac"
#>

param(
    [string]$WatchFolder     = "$env:USERPROFILE\Documents\OpenClaw",
    [string]$RemoteHost      = "macmini",
    [string]$SlackToken      = $env:OPENCLAW_SLACK_TOKEN,
    [string]$SlackChannel    = $env:OPENCLAW_SLACK_CHANNEL,
    [string]$UploadEndpoint  = "http://macmini:8080/upload"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Constants ──────────────────────────────────────────────────────────────────
$SUPPORTED_EXTENSIONS = @(".docx", ".xlsx")
$MAX_SIZE_BYTES        = 50MB
$LOG_DIR               = "$env:APPDATA\OpenClaw"
$LOG_FILE              = "$LOG_DIR\openclaw-watcher.log"
$DEBOUNCE_SECONDS      = 5   # ignore duplicate events within this window

# ── Helpers ────────────────────────────────────────────────────────────────────
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts   = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    $line = "[$ts] [$Level] $Message"
    Add-Content -Path $LOG_FILE -Value $line -Encoding UTF8
    Write-Host $line
}

function Test-WslAvailable {
    try {
        $null = wsl --status 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Sync-ViaWsl {
    param([string]$FilePath)
    $wslPath = wsl wslpath -u $FilePath
    $cmd     = "rsync -az --max-size=50m '$wslPath' '${RemoteHost}:/ai-files/'"
    $null    = wsl bash -c $cmd
    return ($LASTEXITCODE -eq 0)
}

function Sync-ViaHttpUpload {
    param([string]$FilePath)
    $fileName = [System.IO.Path]::GetFileName($FilePath)
    $bytes    = [System.IO.File]::ReadAllBytes($FilePath)
    $boundary = [System.Guid]::NewGuid().ToString()
    $body     = [System.Text.StringBuilder]::new()

    # multipart/form-data body
    $null = $body.AppendLine("--$boundary")
    $null = $body.AppendLine("Content-Disposition: form-data; name=`"file`"; filename=`"$fileName`"")
    $null = $body.AppendLine("Content-Type: application/octet-stream")
    $null = $body.AppendLine("")

    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body.ToString()) `
                 + $bytes `
                 + [System.Text.Encoding]::UTF8.GetBytes("`r`n--$boundary--`r`n")

    try {
        $response = Invoke-WebRequest -Uri $UploadEndpoint `
            -Method POST `
            -ContentType "multipart/form-data; boundary=$boundary" `
            -Body $bodyBytes `
            -TimeoutSec 30
        return ($response.StatusCode -eq 200)
    } catch {
        Write-Log "HTTP upload failed: $_" -Level "WARN"
        return $false
    }
}

function Sync-ViaSlack {
    param([string]$FilePath)
    if (-not $SlackToken -or -not $SlackChannel) {
        Write-Log "Slack fallback skipped: OPENCLAW_SLACK_TOKEN or OPENCLAW_SLACK_CHANNEL not set." -Level "WARN"
        return $false
    }
    $fileName = [System.IO.Path]::GetFileName($FilePath)
    $form     = @{
        token    = $SlackToken
        channels = $SlackChannel
        filename = $fileName
    }
    try {
        $response = Invoke-RestMethod -Uri "https://slack.com/api/files.upload" `
            -Method POST `
            -Form $form `
            -InFile $FilePath
        return ($response.ok -eq $true)
    } catch {
        Write-Log "Slack upload failed: $_" -Level "WARN"
        return $false
    }
}

function Sync-File {
    param([string]$FilePath)

    $info = [System.IO.FileInfo]::new($FilePath)
    if ($info.Extension.ToLower() -notin $SUPPORTED_EXTENSIONS) {
        Write-Log "Skipped (unsupported extension): $FilePath" -Level "DEBUG"
        return
    }
    if ($info.Length -gt $MAX_SIZE_BYTES) {
        Write-Log "Skipped (> 50 MB): $FilePath"
        return
    }

    Write-Log "Detected change: $FilePath"

    # Primary: rsync via WSL
    if (Test-WslAvailable) {
        Write-Log "Syncing via WSL/rsync..."
        if (Sync-ViaWsl -FilePath $FilePath) {
            Write-Log "✅ Synced via WSL: $($info.Name)"
            return
        }
        Write-Log "WSL rsync failed — trying HTTP upload fallback." -Level "WARN"
    }

    # Fallback 1: HTTP PUT to /upload endpoint on Mac Mini
    Write-Log "Trying HTTP upload to $UploadEndpoint..."
    if (Sync-ViaHttpUpload -FilePath $FilePath) {
        Write-Log "✅ Uploaded via HTTP: $($info.Name)"
        return
    }

    # Fallback 2: Slack direct upload
    Write-Log "Trying Slack direct upload..."
    if (Sync-ViaSlack -FilePath $FilePath) {
        Write-Log "✅ Uploaded to Slack: $($info.Name)"
        return
    }

    Write-Log "❌ All sync methods failed for: $($info.Name)" -Level "ERROR"
}

# ── Main ───────────────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $WatchFolder | Out-Null
New-Item -ItemType Directory -Force -Path $LOG_DIR     | Out-Null

Write-Log "OpenClaw Windows Watcher starting. Watching: $WatchFolder"

$watcher                       = [System.IO.FileSystemWatcher]::new($WatchFolder)
$watcher.Filter                = "*.*"
$watcher.IncludeSubdirectories = $false
$watcher.NotifyFilter          = [System.IO.NotifyFilters]::FileName `
                               -bor [System.IO.NotifyFilters]::LastWrite

$recentEvents = [System.Collections.Generic.Dictionary[string, datetime]]::new()

$onChange = {
    param($src, $e)
    $path = $e.FullPath
    $now  = [datetime]::UtcNow

    # Debounce: skip if same path fired within $DEBOUNCE_SECONDS
    if ($recentEvents.ContainsKey($path)) {
        $delta = ($now - $recentEvents[$path]).TotalSeconds
        if ($delta -lt $DEBOUNCE_SECONDS) { return }
    }
    $recentEvents[$path] = $now

    # Run sync in a background job to avoid blocking the watcher thread
    Start-Job -ScriptBlock {
        param($fp) & "$using:PSScriptRoot\Watch-OpenClaw.ps1" -SyncOnly $fp
    } -ArgumentList $path | Out-Null
}

Register-ObjectEvent -InputObject $watcher -EventName Created -Action $onChange | Out-Null
Register-ObjectEvent -InputObject $watcher -EventName Changed -Action $onChange | Out-Null
$watcher.EnableRaisingEvents = $true

Write-Log "Watcher active. Press Ctrl+C to stop."
try {
    while ($true) { Start-Sleep -Seconds 10 }
} finally {
    $watcher.EnableRaisingEvents = $false
    $watcher.Dispose()
    Write-Log "Watcher stopped."
}
