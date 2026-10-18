param([switch]$Apply)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$taskName = 'MaaXuexi-Daily-0900'
$appRoot = Split-Path -Parent $PSScriptRoot

function New-MaaMorningTrigger([DateTimeOffset]$Now = [DateTimeOffset]::Now) {
    $china = [TimeZoneInfo]::FindSystemTimeZoneById('China Standard Time')
    $localNow = [TimeZoneInfo]::ConvertTime($Now, $china)
    $start = $localNow.Date.AddHours(7).AddMinutes(40)
    if ($start -le $localNow.DateTime) { $start = $start.AddDays(1) }
    $trigger = New-ScheduledTaskTrigger -Daily -At $start -RandomDelay (New-TimeSpan -Minutes 75)
    $trigger.StartBoundary = $start.ToString('yyyy-MM-ddTHH:mm:ss') + '+08:00'
    return $trigger
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if ($Apply) {
    # Keep the existing hidden launcher, user principal and duplicate-run settings.
    $actions = @($task.Actions)
    $expectedExe = Join-Path $appRoot '.venv\Scripts\pythonw.exe'
    $expectedArgs = '"' + (Join-Path $appRoot 'scripts\Launch-Hidden.py') + '" --daily'
    if ($actions.Count -ne 1 -or $actions[0].Execute -ne $expectedExe -or $actions[0].Arguments -ne $expectedArgs) {
        throw 'The scheduled task action does not belong to this MaaXuexi installation.'
    }
    $logRoot = Join-Path $appRoot 'logs'
    $null = New-Item -ItemType Directory -Path $logRoot -Force
    $backup = Join-Path $logRoot ('schedule-before-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.xml')
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath $backup -Encoding Unicode
    $task.Settings.StartWhenAvailable = $true
    $null = Set-ScheduledTask -TaskName $taskName -Trigger (New-MaaMorningTrigger) -Settings $task.Settings
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
}
$triggers = @($task.Triggers)
if ($triggers.Count -ne 1 -or $triggers[0].CimClass.CimClassName -ne 'MSFT_TaskDailyTrigger') {
    throw 'Expected one daily task trigger.'
}
$trigger = $triggers[0]
$delay = if ($trigger.RandomDelay) { [Xml.XmlConvert]::ToTimeSpan([string]$trigger.RandomDelay).TotalSeconds } else { 0 }
$info = Get-ScheduledTaskInfo -TaskName $taskName
[pscustomobject]@{
    available = $true
    enabled = ($task.State.ToString() -ne 'Disabled' -and $trigger.Enabled)
    task_name = $taskName
    start_boundary = [string]$trigger.StartBoundary
    random_delay_seconds = [int]$delay
    start_when_available = [bool]$task.Settings.StartWhenAvailable
    wake_from_sleep = [bool]$task.Settings.WakeToRun
    state = $task.State.ToString()
    next_base_run = $info.NextRunTime.ToString('o')
} | ConvertTo-Json -Compress
