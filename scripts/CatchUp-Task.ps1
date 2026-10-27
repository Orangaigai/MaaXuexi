# Functions only: reading schedule status never registers or changes a task.
function Assert-MaaCatchUpOwnership([string]$Root) {
    $task = Get-ScheduledTask -TaskName 'MaaXuexi-CatchUp' -ErrorAction SilentlyContinue
    if (!$task) { return }
    $actions = @($task.Actions)
    $exe = Join-Path $Root '.venv\Scripts\pythonw.exe'
    $args = '"'+(Join-Path $Root 'scripts\Launch-Hidden.py')+'" --catch-up'
    if ($actions.Count -ne 1 -or $actions[0].Execute -ne $exe -or $actions[0].Arguments -ne $args -or $actions[0].WorkingDirectory -ne $Root) {
        throw 'Another installation owns MaaXuexi-CatchUp. It was not changed.'
    }
}

function Sync-MaaCatchUpTask([string]$Root, [bool]$Enabled) {
    Assert-MaaCatchUpOwnership $Root
    $main = Get-ScheduledTask -TaskName 'MaaXuexi-Daily-0900' -ErrorAction Stop
    $actions = @($main.Actions)
    $exe = Join-Path $Root '.venv\Scripts\pythonw.exe'
    $dailyArgs = '"'+(Join-Path $Root 'scripts\Launch-Hidden.py')+'" --daily'
    if ($actions.Count -ne 1 -or $actions[0].Execute -ne $exe -or $actions[0].Arguments -ne $dailyArgs) {
        throw 'The main schedule does not belong to this installation.'
    }
    $task = Get-ScheduledTask -TaskName 'MaaXuexi-CatchUp' -ErrorAction SilentlyContinue
    if (!$task -or $task.Description -ne 'MaaXuexi immediate catch-up v1') {
        $escape = { param($text) [Security.SecurityElement]::Escape([string]$text) }
        $user = & $escape $main.Principal.UserId
        $command = & $escape $exe
        $arguments = & $escape ('"'+(Join-Path $Root 'scripts\Launch-Hidden.py')+'" --catch-up')
        $directory = & $escape $Root
        $subscription = & $escape @'
<QueryList><Query Id="0" Path="Microsoft-Windows-NetworkProfile/Operational"><Select Path="Microsoft-Windows-NetworkProfile/Operational">*[System[Provider[@Name='Microsoft-Windows-NetworkProfile'] and (EventID=10000 or EventID=4004)]]</Select></Query><Query Id="1" Path="System"><Select Path="System">*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]</Select></Query></QueryList>
'@
        $enabledText = $Enabled.ToString().ToLowerInvariant()
        $xml = @"
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
 <RegistrationInfo><Description>MaaXuexi immediate catch-up v1</Description></RegistrationInfo>
 <Triggers>
  <LogonTrigger><Enabled>true</Enabled><UserId>$user</UserId></LogonTrigger>
  <EventTrigger><Enabled>true</Enabled><Subscription>$subscription</Subscription></EventTrigger>
 </Triggers>
 <Principals><Principal id="Author"><UserId>$user</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
 <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>false</StartWhenAvailable><RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable><Enabled>$enabledText</Enabled><Hidden>true</Hidden><RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun><ExecutionTimeLimit>PT1H</ExecutionTimeLimit></Settings>
 <Actions Context="Author"><Exec><Command>$command</Command><Arguments>$arguments</Arguments><WorkingDirectory>$directory</WorkingDirectory></Exec></Actions>
</Task>
"@
        $null = Register-ScheduledTask -TaskName 'MaaXuexi-CatchUp' -Xml $xml -Force -ErrorAction Stop
    } elseif ($Enabled -and $task.State.ToString() -eq 'Disabled') {
        $null = Enable-ScheduledTask -TaskName 'MaaXuexi-CatchUp' -ErrorAction Stop
    } elseif (!$Enabled -and $task.State.ToString() -ne 'Disabled') {
        $null = Disable-ScheduledTask -TaskName 'MaaXuexi-CatchUp' -ErrorAction Stop
    }
}
