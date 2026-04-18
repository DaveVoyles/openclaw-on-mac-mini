$TASK_NAME = "OpenClaw-Watcher"
Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue
foreach ($key in @("OPENCLAW_REMOTE_HOST", "OPENCLAW_SLACK_TOKEN", "OPENCLAW_SLACK_CHANNEL")) {
    [System.Environment]::SetEnvironmentVariable($key, $null, "User")
}
Write-Host "✅ OpenClaw Watcher uninstalled."
