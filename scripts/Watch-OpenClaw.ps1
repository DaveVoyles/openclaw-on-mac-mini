#Requires -Version 5.1
<#
.SYNOPSIS
    OpenClaw Windows Folder Watcher.
    Monitors Documents\OpenClaw\ and syncs .docx/.xlsx files to macmini:/ai-files/.

.DESCRIPTION
    Designed to run as a Scheduled Task at login (no admin required).
    Uses .NET FileSystemWatcher for near-instant file detection.

    Sync order:
      1. rsync via WSL (primary — fastest, uses existing SSH key)
      2. HTTP multipart upload to Mac Mini /upload endpoint (fallback)
      3. Log a friendly failure message if both methods fail

    Word files are retried once if locked (Word holds a lock while saving).

.PARAMETER WatchFolder
    Folder to monitor. Defaults to $env:USERPROFILE\Documents\OpenClaw.

.PARAMETER RemoteHost
    SSH / network alias for the Mac Mini. Defaults to env var OPENCLAW_REMOTE_HOST,
    then "macmini".

.PARAMETER UploadEndpoint
    HTTP endpoint for fallback upload. Defaults to http://192.168.1.93:8080/upload.

.EXAMPLE
    .\Watch-OpenClaw.ps1
    .\Watch-OpenClaw.ps1 -WatchFolder "D:\MyDocs\OpenClaw" -RemoteHost "mymac"
#>

param(
    [string]$WatchFolder    = "$env:USERPROFILE\Documents\OpenClaw",
    [string]$RemoteHost     = "macmini",
    [string]$UploadEndpoint = "http://192.168.1.93:8080/upload"
)

# Allow env var override when the parameter wasn't passed explicitly
if (-not $PSBoundParameters.ContainsKey('RemoteHost') -and $env:OPENCLAW_REMOTE_HOST) {
    $RemoteHost = $env:OPENCLAW_REMOTE_HOST
}

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# ── Constants ──────────────────────────────────────────────────────────────────
$SUPPORTED_EXT      = @(".docx", ".xlsx")
$MAX_SIZE_BYTES     = 50MB
$LOG_DIR            = "$env:APPDATA\OpenClaw"
$LOG_FILE           = "$LOG_DIR\watcher.log"
$DEBOUNCE_SECS      = 5
$FILE_LOCK_WAIT_SEC = 4   # seconds to wait before retrying a file locked by Word

# ── Logging ────────────────────────────────────────────────────────────────────
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    try { Add-Content -Path $LOG_FILE -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
    $color = switch ($Level) {
        "ERROR" { "Red"    }
        "WARN"  { "Yellow" }
        "DEBUG" { "Gray"   }
        default { "White"  }
    }
    Write-Host $line -ForegroundColor $color
}

# ── File lock detection ────────────────────────────────────────────────────────
function Test-FileLocked {
    param([string]$FilePath)
    try {
        $stream = [System.IO.File]::Open(
            $FilePath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::None)
        $stream.Close()
        $stream.Dispose()
        return $false
    } catch {
        return $true
    }
}

# Returns $true when the file is accessible; $false if still locked after one retry.
function Wait-FileUnlocked {
    param([string]$FilePath)
    if (-not (Test-FileLocked -FilePath $FilePath)) { return $true }

    $name = Split-Path $FilePath -Leaf
    Write-Log "File locked (Word may still be saving) — waiting ${FILE_LOCK_WAIT_SEC}s before retry: $name" "WARN"
    Start-Sleep -Seconds $FILE_LOCK_WAIT_SEC

    if (Test-FileLocked -FilePath $FilePath) {
        Write-Log "File still locked after retry — will sync on next save: $name" "WARN"
        return $false
    }
    return $true
}

# ── WSL / rsync ────────────────────────────────────────────────────────────────
function Test-WslAvailable {
    try {
        $null = & wsl --status 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Sync-ViaWsl {
    param([string]$FilePath)
    try {
        # Convert Windows watch-folder path to a WSL path
        $wslWatchPath = (& wsl wslpath -u $WatchFolder 2>&1)
        if ($LASTEXITCODE -ne 0) {
            Write-Log "wslpath conversion failed: $wslWatchPath" "WARN"
            return $false
        }

        # Sync the whole watch folder filtered to .docx/.xlsx (rsync handles change detection)
        $result = & wsl rsync -avz `
            --include="*.docx" `
            --include="*.xlsx" `
            --exclude="*" `
            "$wslWatchPath/" `
            "${RemoteHost}:/ai-files/" 2>&1

        if ($LASTEXITCODE -eq 0) { return $true }
        Write-Log "WSL rsync output: $result" "WARN"
        return $false
    } catch {
        Write-Log "WSL rsync exception: $_" "WARN"
        return $false
    }
}

# ── HTTP upload (PS 5.1-compatible multipart/form-data) ───────────────────────
function Sync-ViaHttpUpload {
    param([string]$FilePath)
    try {
        $fileName  = [System.IO.Path]::GetFileName($FilePath)
        $fileBytes = [System.IO.File]::ReadAllBytes($FilePath)
        $boundary  = [System.Guid]::NewGuid().ToString("N")
        $enc       = [System.Text.Encoding]::UTF8

        $headerStr = "--$boundary`r`n" +
                     "Content-Disposition: form-data; name=`"file`"; filename=`"$fileName`"`r`n" +
                     "Content-Type: application/octet-stream`r`n`r`n"
        $footerStr = "`r`n--$boundary--`r`n"

        $bodyBytes = $enc.GetBytes($headerStr) + $fileBytes + $enc.GetBytes($footerStr)

        $response = Invoke-WebRequest `
            -Uri         $UploadEndpoint `
            -Method      POST `
            -ContentType "multipart/form-data; boundary=$boundary" `
            -Body        $bodyBytes `
            -TimeoutSec  30 `
            -UseBasicParsing

        return ($response.StatusCode -eq 200)
    } catch {
        Write-Log "HTTP upload failed: $_" "WARN"
        return $false
    }
}

# ── Core sync logic ────────────────────────────────────────────────────────────
function Sync-File {
    param([string]$FilePath)

    # Skip Word temp/lock files (e.g. ~$report.docx)
    $fileName = [System.IO.Path]::GetFileName($FilePath)
    if ($fileName.StartsWith("~`$") -or $fileName.StartsWith(".~")) { return }

    $ext = [System.IO.Path]::GetExtension($FilePath).ToLower()
    if ($ext -notin $SUPPORTED_EXT) { return }

    if (-not (Test-Path -LiteralPath $FilePath)) {
        Write-Log "File not found (may have been moved): $fileName" "DEBUG"
        return
    }

    $size = (Get-Item -LiteralPath $FilePath).Length
    if ($size -gt $MAX_SIZE_BYTES) {
        Write-Host "⚠️  Skipped $fileName — file is over 50 MB." -ForegroundColor Yellow
        Write-Log "Skipped (> 50 MB): $fileName" "WARN"
        return
    }

    Write-Log "Change detected: $fileName"

    # Wait for Word to release the file lock before syncing
    if (-not (Wait-FileUnlocked -FilePath $FilePath)) { return }

    # ── Path 1: WSL rsync ────────────────────────────────────────────────────
    if (Test-WslAvailable) {
        Write-Log "Syncing via WSL/rsync..."
        if (Sync-ViaWsl -FilePath $FilePath) {
            Write-Host "✅ Synced $fileName to OpenClaw" -ForegroundColor Green
            Write-Log "Synced via WSL: $fileName"
            return
        }
        Write-Log "WSL rsync failed — trying HTTP upload." "WARN"
    }

    # ── Path 2: HTTP upload ──────────────────────────────────────────────────
    Write-Log "Uploading via HTTP to $UploadEndpoint..."
    if (Sync-ViaHttpUpload -FilePath $FilePath) {
        Write-Host "✅ Synced $fileName to OpenClaw (via HTTP upload)" -ForegroundColor Green
        Write-Log "Synced via HTTP: $fileName"
        return
    }

    # ── Path 3: log failure ──────────────────────────────────────────────────
    Write-Host "❌ Could not sync $fileName. Check your network connection." -ForegroundColor Red
    Write-Host "   See log for details: $LOG_FILE" -ForegroundColor Red
    Write-Log "All sync methods failed for: $fileName" "ERROR"
}

# ── Startup ────────────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $WatchFolder | Out-Null
New-Item -ItemType Directory -Force -Path $LOG_DIR     | Out-Null

Write-Log "OpenClaw watcher starting — watching: $WatchFolder"

if (-not (Test-WslAvailable)) {
    Write-Host "ℹ️  WSL not detected — will use HTTP upload to $UploadEndpoint." -ForegroundColor Yellow
    Write-Log "WSL not available; HTTP upload fallback active." "WARN"
}

# ── FileSystemWatcher setup ────────────────────────────────────────────────────
$watcher                       = [System.IO.FileSystemWatcher]::new($WatchFolder)
$watcher.Filter                = "*.*"
$watcher.IncludeSubdirectories = $false
$watcher.NotifyFilter          = [System.IO.NotifyFilters]::FileName -bor
                                 [System.IO.NotifyFilters]::LastWrite

Register-ObjectEvent -InputObject $watcher -EventName Created -SourceIdentifier "OCW_Created" | Out-Null
Register-ObjectEvent -InputObject $watcher -EventName Changed -SourceIdentifier "OCW_Changed" | Out-Null
Register-ObjectEvent -InputObject $watcher -EventName Renamed -SourceIdentifier "OCW_Renamed" | Out-Null
$watcher.EnableRaisingEvents = $true

Write-Host ""
Write-Host "👁  Watching: $WatchFolder" -ForegroundColor Cyan
Write-Host "   Drop .docx or .xlsx files there and they'll sync automatically." -ForegroundColor Cyan
Write-Host "   Press Ctrl+C to stop." -ForegroundColor Cyan
Write-Host ""

# Debounce: path -> last-processed UTC datetime
$lastSeen = @{}

try {
    while ($true) {
        # Poll the PowerShell event queue
        $e = $null
        foreach ($sid in @("OCW_Created", "OCW_Changed", "OCW_Renamed")) {
            $e = Get-Event -SourceIdentifier $sid -ErrorAction SilentlyContinue |
                 Select-Object -First 1
            if ($e) { break }
        }

        if ($e) {
            Remove-Event -EventIdentifier $e.EventIdentifier | Out-Null
            $path = $e.SourceEventArgs.FullPath

            # Debounce: ignore the same path within DEBOUNCE_SECS
            $now = [datetime]::UtcNow
            if ($lastSeen.ContainsKey($path)) {
                if (($now - $lastSeen[$path]).TotalSeconds -lt $DEBOUNCE_SECS) { continue }
            }
            $lastSeen[$path] = $now

            Sync-File -FilePath $path
        } else {
            Start-Sleep -Milliseconds 500
        }
    }
} finally {
    $watcher.EnableRaisingEvents = $false
    $watcher.Dispose()
    Unregister-Event -SourceIdentifier "OCW_Created" -ErrorAction SilentlyContinue
    Unregister-Event -SourceIdentifier "OCW_Changed" -ErrorAction SilentlyContinue
    Unregister-Event -SourceIdentifier "OCW_Renamed" -ErrorAction SilentlyContinue
    Write-Log "Watcher stopped."
}
