param(
    [string]$TaskName = "EdgeBookmarkNavigatorSync",
    [string]$Time = "03:00",
    [string]$Python = "python",
    [string]$Config = (Join-Path $PSScriptRoot "sync_config.json")
)

$script = Join-Path $PSScriptRoot "edge_sync.py"
$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$script`" --config `"$Config`""
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Daily Edge bookmark sync" -Force | Out-Null
Write-Host "Installed scheduled task $TaskName. Daily run time: $Time."
