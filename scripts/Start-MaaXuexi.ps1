param([switch]$NoBrowser, [switch]$Reconnect)
$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
$processRecordPath = Join-Path $appRoot 'logs\server-process.json'
. (Join-Path $PSScriptRoot 'Control-Browser.ps1')

function Open-ControlPage {
    if ($NoBrowser) { return }
    try { Open-ControlBrowser $appRoot }
    catch { Write-Host 'Could not open the browser. Progress page: http://127.0.0.1:8000/' }
}

function Test-ViewCompletedDay($Health) {
    if ($Reconnect -or $Health.task_running -or $Health.run_summary.status -ne 'completed') { return $false }
    try {
        $finished=[DateTimeOffset]::Parse([string]$Health.run_summary.finished_at)
        $beijing=[TimeSpan]::FromHours(8)
        return $finished.ToOffset($beijing).Date -eq [DateTimeOffset]::UtcNow.ToOffset($beijing).Date
    } catch { return $false }
}

function Get-InstalledBackendVersion {
    $source = [IO.File]::ReadAllText((Join-Path $appRoot 'backend\task_support.py'))
    $match = [regex]::Match($source, '(?m)^VERSION\s*=\s*"([^"\r\n]+)"\s*$')
    if (!$match.Success) { throw 'Cannot read the installed backend version. Check backend\task_support.py.' }
    return $match.Groups[1].Value
}

function Test-BackendPortOccupied {
    $probe = New-Object System.Net.Sockets.TcpClient
    try { $probe.Connect('127.0.0.1',8000); return $true }
    catch [System.Net.Sockets.SocketException] { return $false }
    finally { $probe.Dispose() }
}

function Wait-BackendPortFree([int]$TimeoutMilliseconds = 8000) {
    $deadline = (Get-Date).AddMilliseconds($TimeoutMilliseconds)
    while (Test-BackendPortOccupied) {
        if ((Get-Date) -ge $deadline) { throw 'Port 8000 is still occupied after stopping MaaXuexi. No unrelated process was stopped.' }
        Start-Sleep -Milliseconds 100
    }
}

function Get-RecordedProcesses {
    if (!(Test-Path -LiteralPath $processRecordPath)) { return }
    try {
        $records = Get-Content -LiteralPath $processRecordPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($record in $records) {
            $recordedProc = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
            $recordStart = if ($record.start_utc -is [DateTime]) { $record.start_utc.ToUniversalTime() } else { [DateTime]::Parse([string]$record.start_utc,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime() }
            if ($recordedProc -and $record.root -eq $appRoot -and $recordedProc.Path -eq $record.executable -and $recordedProc.StartTime.ToUniversalTime().Ticks -eq $recordStart.Ticks) { $recordedProc }
        }
    } catch { }
}

function Save-ProcessRecords($processes) {
    $records = @($processes | ForEach-Object { [pscustomobject]@{pid=$_.Id;start_utc=$_.StartTime.ToUniversalTime().ToString('O');executable=$_.Path;root=$appRoot} })
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($processRecordPath,(ConvertTo-Json -InputObject $records -Depth 5),$encoding)
}

function Read-RecordedBackendState([int]$TimeoutMilliseconds = 15000) {
    $deadline=(Get-Date).AddMilliseconds($TimeoutMilliseconds)
    while (@(Get-RecordedProcesses).Count -gt 0) {
        try {
            $state=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/status' -TimeoutSec 1
            if ($state.completion_action.state -notin @('closing','closed')) { return $state }
        } catch { }
        if ((Get-Date) -ge $deadline) { throw 'The recorded local backend is still starting or closing. No unrelated process was stopped.' }
        Start-Sleep -Milliseconds 150
    }
    return $null
}

function Initialize-LocalSettings {
    $settings = @{api_key='';model='deepseek-flash'}
    $payload = @{api_key=[string]$settings.api_key; model=[string]$settings.model} | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/settings' -Method Post -ContentType 'application/json' -Body ([System.Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 20
}

function Test-ActiveLocalTask([switch]$AllowTransientFailure) {
    try {
        $current = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/status' -TimeoutSec 5
    } catch [Net.WebException] {
        # During cold boot a read-only status timeout is an unknown state,
        # not proof of an idle backend. The boot loop owns the bounded wait.
        if ($AllowTransientFailure -and $_.Exception.Status -in @(
            [Net.WebExceptionStatus]::Timeout, [Net.WebExceptionStatus]::ConnectFailure,
            [Net.WebExceptionStatus]::ConnectionClosed, [Net.WebExceptionStatus]::ReceiveFailure,
            [Net.WebExceptionStatus]::SendFailure, [Net.WebExceptionStatus]::KeepAliveFailure)) {
            return $null
        }
        throw
    }
    if ($current.status -ne 'running' -or $current.backend -ne 'deepseek-local') {
        throw 'The local MaaXuexi backend is not ready.'
    }
    return [bool]($current.task_running -or $current.paused)
}

function Connect-DeviceWhenIdle($Device) {
    if (Test-ActiveLocalTask) {
        Write-Host 'A task is already running. Its device connection is preserved.'
        return
    }
    $payload = $Device | ConvertTo-Json -Depth 12 -Compress
    try {
        $connection = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/connect_device' -Method Post -ContentType 'application/json' -Body ([System.Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 45
    } catch {
        # A task can start in the browser after the idle check. Only treat the
        # resulting conflict as success after confirming an active local task.
        $response = $_.Exception.Response
        if ($response -and [int]$response.StatusCode -eq 409 -and (Test-ActiveLocalTask)) {
            Write-Host 'A task started during device preparation. Its connection is preserved.'
            return
        }
        throw
    }
    if ($connection.status -ne 'success') { throw 'MaaXuexi could not connect to MuMu.' }
    Write-Host 'MuMu connected. A recognized Android desktop opens Xuexi; existing app pages are preserved. Tasks start only on Execute or the schedule.'
}

function Invoke-AdbProbe([string]$AdbPath, [string]$Address, [ValidateSet('Connect','State','Boot')][string]$Mode) {
    if ($Address -notmatch '^(127\.0\.0\.1|localhost):[0-9]+$') { throw 'Invalid local ADB address.' }
    $arguments = switch ($Mode) {
        'Connect' { 'connect '+$Address }
        'State' { '-s '+$Address+' get-state' }
        'Boot' { '-s '+$Address+' shell getprop sys.boot_completed' }
    }
    # Windows PowerShell 5.1 turns native stderr into an exception when
    # ErrorActionPreference=Stop, including a normal temporary "device offline".
    # Drain both pipes directly and let the caller retry based on the exit code.
    $process = New-Object Diagnostics.Process
    $process.StartInfo = New-Object Diagnostics.ProcessStartInfo
    $process.StartInfo.FileName = $AdbPath
    $process.StartInfo.Arguments = $arguments
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (!$process.WaitForExit(5000)) {
            $process.Kill()
            $process.WaitForExit()
            return [pscustomobject]@{ExitCode=-1;StdOut=''}
        }
        $output = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        return [pscustomobject]@{ExitCode=$process.ExitCode;StdOut=$output.Trim()}
    } finally { $process.Dispose() }
}

function Show-EmulatorWindow([string]$CliPath, [string]$VmIndex, [int]$TimeoutMilliseconds = 3000) {
    if ($VmIndex -notmatch '^\d+$') { throw 'Invalid MuMu instance index.' }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = New-Object Diagnostics.ProcessStartInfo
    $process.StartInfo.FileName = $CliPath
    $process.StartInfo.Arguments = 'control -v '+$VmIndex+' show_window'
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (!$process.WaitForExit($TimeoutMilliseconds)) {
            $process.Kill()
            $process.WaitForExit()
            return $false
        }
        $null = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        return ($process.ExitCode -eq 0)
    } catch { return $false }
    finally { $process.Dispose() }
}

function Connect-ConfiguredEmulator {
    if (Test-ActiveLocalTask) {
        Write-Host 'A task is already running. Emulator preparation is handled by that task.'
        return
    }
    $emulatorConfigPath = Join-Path $appRoot 'config\emulator.json'
    if (!(Test-Path -LiteralPath $emulatorConfigPath)) { return }
    $emulatorConfig = Get-Content -LiteralPath $emulatorConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $cliPath = [string]$emulatorConfig.mumu_cli
    $vmIndex = [string]$emulatorConfig.vm_index
    if (!(Test-Path -LiteralPath $cliPath)) { throw 'MuMu is missing. Check config\emulator.json.' }

    function Read-EmulatorInfo {
        $infoText = (& $cliPath info -v $vmIndex) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'Could not query MuMu.' }
        $infoObject = $infoText | ConvertFrom-Json
        if ($infoObject.PSObject.Properties.Name -contains $vmIndex) { return $infoObject.$vmIndex }
        return $infoObject
    }

    $vmInfo = Read-EmulatorInfo
    if (!$vmInfo.is_process_started) {
        Write-Host 'Starting MuMu...'
        $null = & $cliPath control -v $vmIndex launch
        if ($LASTEXITCODE -ne 0) { throw 'MuMu could not start.' }
    }
    Write-Host 'Waiting for Android...'
    $adbPath = [string]$emulatorConfig.adb_path
    $bootReady = $false
    $bootStarted = Get-Date
    $bootDeadline = $bootStarted.AddSeconds(600)
    $lastProgress = $bootStarted.AddSeconds(-15)
    do {
        $activeTask = Test-ActiveLocalTask -AllowTransientFailure
        if ($null -eq $activeTask) {
            if (((Get-Date)-$lastProgress).TotalSeconds -ge 15) {
                Write-Host 'Local backend status temporarily unavailable during Android boot; waiting within the existing 600-second limit.'
                $lastProgress = Get-Date
            }
            Start-Sleep -Milliseconds 500
            continue
        }
        if ($activeTask) {
            Write-Host 'A task started while Android was loading. Its preparation is preserved.'
            return
        }
        try { $vmInfo = Read-EmulatorInfo } catch { $vmInfo = $null }
        if ($vmInfo -and $vmInfo.adb_port) {
            $adbHost = [string]$vmInfo.adb_host_ip
            if (!$adbHost) { $adbHost='127.0.0.1' }
            if ($adbHost -notin @('127.0.0.1','localhost')) { throw 'Invalid nonlocal MuMu ADB address.' }
            $address = $adbHost + ':' + [string]$vmInfo.adb_port
            $null = Invoke-AdbProbe $adbPath $address Connect
            $deviceState = Invoke-AdbProbe $adbPath $address State
            if ($deviceState.ExitCode -eq 0 -and $deviceState.StdOut -eq 'device') {
                $bootState = Invoke-AdbProbe $adbPath $address Boot
                if ($bootState.ExitCode -eq 0 -and $bootState.StdOut -eq '1') { $bootReady=$true; break }
            }
        }
        if (((Get-Date)-$lastProgress).TotalSeconds -ge 15) {
            Write-Host ('Waiting for Android/ADB boot: '+[int]((Get-Date)-$bootStarted).TotalSeconds+' seconds (limit 600).')
            $lastProgress = Get-Date
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $bootDeadline)
    if (!$bootReady) { throw 'Android/ADB boot was not ready within 600 seconds. Check the MuMu startup message.' }
    if (Test-ActiveLocalTask) { Write-Host 'The running task already handles the emulator.'; return }
    if (!(Show-EmulatorWindow $cliPath $vmIndex)) {
        if (Test-ActiveLocalTask) { Write-Host 'The running task already handles the emulator.'; return }
        $deviceState = Invoke-AdbProbe $adbPath $address State
        if ($deviceState.ExitCode -ne 0 -or $deviceState.StdOut -ne 'device') { throw 'The emulator disconnected while its window was being shown.' }
        Write-Host 'MuMu window display was not confirmed, but Android/ADB is online. Continuing with the existing device.'
    }

    $selectedDevice = $null
    for ($attempt=0; $attempt -lt 5; $attempt++) {
        if (Test-ActiveLocalTask) { Write-Host 'The running task already handles the device.'; return }
        $deviceList = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/get_device' -TimeoutSec 15
        $selectedDevice = @($deviceList.devices | Where-Object {$_.address -eq $address}) | Select-Object -First 1
        if ($selectedDevice) { break }
        Start-Sleep -Milliseconds 500
    }
    if (!$selectedDevice) { throw 'MuMu was not discovered. Select and connect the device in the web page.' }
    Connect-DeviceWhenIdle $selectedDevice
}

try {
    $expectedLocalVersion = Get-InstalledBackendVersion
    $matching = @(Get-RecordedProcesses)
    if ($matching.Count -gt 0) {
        $health = Read-RecordedBackendState
        if ($health) {
            if ($health.status -ne 'running') { throw 'The existing process is not ready. Stop it and try again.' }
            if ($health.backend -ne 'deepseek-local') { throw 'The original backend is running. Stop MaaXuexi, then start it again.' }
            if (!$health.task_running -and $health.local_version -ne $expectedLocalVersion) {
                Write-Host 'Loading the updated local backend. The current task has ended.'
                & (Join-Path $appRoot 'scripts\Stop-MaaXuexi.ps1')
                if ($LASTEXITCODE -ne 0) { throw 'Could not stop the previous local backend.' }
                Wait-BackendPortFree
            } else {
                Open-ControlPage
                if (!$health.task_running -and !(Test-ViewCompletedDay $health)) { Connect-ConfiguredEmulator }
                Write-Host 'MaaXuexi is already running: http://127.0.0.1:8000/'
                if ($health.task_running -and $health.local_version -ne $expectedLocalVersion) { Write-Host 'The active task is preserved; the update will load on the next idle startup.' }
                exit 0
            }
        } else { Wait-BackendPortFree }
    }
    if (Test-BackendPortOccupied) { throw 'Port 8000 is occupied by an unrecorded program. No unrelated process was stopped.' }

    $logRoot = Join-Path $appRoot 'logs'
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $stdoutPath = Join-Path $logRoot ($stamp+'.stdout.log')
    $stderrPath = Join-Path $logRoot ($stamp+'.stderr.log')
    $pythonPath = Join-Path $appRoot '.venv\Scripts\python.exe'
    $entryPath = Join-Path $appRoot 'backend\webui.py'
    if (!(Test-Path -LiteralPath $pythonPath) -or !(Test-Path -LiteralPath $entryPath)) { throw 'The DeepSeek backend is missing.' }
    $proc = Start-Process -FilePath $pythonPath -ArgumentList @('-u',('"'+$entryPath+'"')) -WorkingDirectory $appRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    Save-ProcessRecords @($proc)
    $ready = $false
    for ($step=0; $step -lt 40; $step++) {
        if ($proc.HasExited) { throw "MaaXuexi exited. See $stderrPath" }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/status' -TimeoutSec 1
            if ($health.status -eq 'running') { $ready=$true; break }
        } catch { }
        Start-Sleep -Milliseconds 400
    }
    if (!$ready) { throw "Startup timed out. See $stderrPath" }
    if ($health.pid -and [int]$health.pid -ne $proc.Id) {
        $serverProcess = Get-Process -Id ([int]$health.pid) -ErrorAction Stop
        Save-ProcessRecords @($serverProcess,$proc)
    }
    # The official backend permits an empty key. Article/video tasks do not use AI.
    Initialize-LocalSettings
    Write-Host 'MaaXuexi is ready: http://127.0.0.1:8000/'
    Open-ControlPage
    if (!(Test-ViewCompletedDay $health)) { Connect-ConfiguredEmulator }
    Write-Host 'Logs are saved in the logs folder. No learning task has been started.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
