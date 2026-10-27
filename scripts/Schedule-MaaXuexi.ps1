param([switch]$Apply, [string]$StartAt, [switch]$Disable)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$taskName = 'MaaXuexi-Daily-0900'
$appRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'CatchUp-Task.ps1')

function New-MaaDailyTrigger([string]$StartAt) {
    $chosen = [DateTimeOffset]::ParseExact($StartAt, 'yyyy-MM-ddTHH:mm:sszzz', [Globalization.CultureInfo]::InvariantCulture)
    if ($chosen.Offset -ne [TimeSpan]::FromHours(8)) { throw 'Expected a Beijing time offset.' }
    $trigger = New-ScheduledTaskTrigger -Daily -At $chosen.LocalDateTime
    $trigger.StartBoundary = $chosen.ToString('yyyy-MM-ddTHH:mm:sszzz')
    return $trigger
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if ($Apply -and $Disable) { throw 'Choose one schedule operation.' }
if ($Apply -or $Disable) {
    $actions = @($task.Actions)
    $expectedExe = Join-Path $appRoot '.venv\Scripts\pythonw.exe'
    $expectedArgs = '"' + (Join-Path $appRoot 'scripts\Launch-Hidden.py') + '" --daily'
    if ($actions.Count -ne 1 -or $actions[0].Execute -ne $expectedExe -or $actions[0].Arguments -ne $expectedArgs) {
        throw 'The scheduled task action does not belong to this MaaXuexi installation.'
    }
    $logRoot = Join-Path $appRoot 'logs'
    $null = New-Item -ItemType Directory -Path $logRoot -Force
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $logRoot 'schedule-before-last-change.xml') -Encoding Unicode
    # Prepare/validate the owned helper before changing the main trigger.
    Sync-MaaCatchUpTask $appRoot ([bool]$Apply)
    if ($Disable) {
        $null = Disable-ScheduledTask -TaskName $taskName
    } else {
        $trigger = New-MaaDailyTrigger $StartAt
        $task.Settings.StartWhenAvailable = $true
        $null = Set-ScheduledTask -TaskName $taskName -Trigger $trigger -Settings $task.Settings
        $null = Enable-ScheduledTask -TaskName $taskName
    }
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
    next_base_run = $info.NextRunTime.ToUniversalTime().ToString('o')
} | ConvertTo-Json -Compress
