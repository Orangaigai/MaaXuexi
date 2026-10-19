param(
    [string]$MuMuRoot,
    [ValidateRange(0, 999)][int]$VmIndex = 0,
    [string]$PythonPath,
    [switch]$SkipDependencies,
    [switch]$SkipSchedule
)
$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
$previousLocation = Get-Location

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program" }
}

function Write-JsonIfMissing([string]$Path, $Value) {
    if (Test-Path -LiteralPath $Path) { return }
    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 8), $encoding)
}

try {
    if (!$MuMuRoot) { $MuMuRoot = Read-Host 'MuMu install folder (contains nx_main)' }
    $MuMuRoot = (Resolve-Path -LiteralPath $MuMuRoot).Path
    $cliPath = Join-Path $MuMuRoot 'nx_main\MuMuManager.exe'
    if (!(Test-Path -LiteralPath $cliPath -PathType Leaf)) { $cliPath = Join-Path $MuMuRoot 'nx_main\mumu-cli.exe' }
    $adbPath = Join-Path $MuMuRoot 'nx_main\adb.exe'
    if (!(Test-Path -LiteralPath $cliPath -PathType Leaf) -or !(Test-Path -LiteralPath $adbPath -PathType Leaf)) {
        throw 'MuMuManager.exe/mumu-cli.exe or adb.exe is missing. Select the MuMu install folder, not nx_main. See docs/installation.md.'
    }

    Set-Location -LiteralPath $appRoot
    $python = Join-Path $appRoot '.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $python)) {
        $bootstrapArgs = @()
        if ($PythonPath) { $bootstrap = (Get-Command $PythonPath -ErrorAction Stop).Source }
        else { $bootstrap = (Get-Command py -ErrorAction Stop).Source; $bootstrapArgs = @('-3.12') }
        $info = (& $bootstrap @bootstrapArgs -c 'import sys,json;print(json.dumps([list(sys.version_info[:2]),64 if sys.maxsize>2**32 else 32]))') | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or $info[0][0] -ne 3 -or $info[0][1] -ne 12 -or $info[1] -ne 64) {
            throw 'Install 64-bit Python 3.12 from python.org, including its py launcher.'
        }
        Invoke-Checked $bootstrap ($bootstrapArgs + @('-m','venv',(Join-Path $appRoot '.venv')))
    }
    if (!$SkipDependencies) {
        Invoke-Checked $python @('-m','pip','install','--disable-pip-version-check','-r',(Join-Path $appRoot 'backend\requirements-runtime.lock.txt'))
    }

    $configRoot = Join-Path $appRoot 'config'
    $null = New-Item -ItemType Directory -Path $configRoot -Force
    Write-JsonIfMissing (Join-Path $configRoot 'emulator.json') @{mumu_cli=$cliPath;adb_path=$adbPath;vm_index=$VmIndex}
    foreach ($name in @('local-task-plan','comment-policy')) {
        $destination = Join-Path $configRoot ($name+'.json')
        if (!(Test-Path -LiteralPath $destination)) { Copy-Item -LiteralPath (Join-Path $configRoot ($name+'.example.json')) -Destination $destination }
    }
    Write-JsonIfMissing (Join-Path $configRoot 'local-schedule.json') @{
        options=@{mode='random_window';time='09:00';window_start='08:00';window_end='09:00'}
    }

    if (!$SkipSchedule) {
        $taskName = 'MaaXuexi-Daily-0900'
        $expectedExe = Join-Path $appRoot '.venv\Scripts\pythonw.exe'
        $expectedArgs = '"'+(Join-Path $appRoot 'scripts\Launch-Hidden.py')+'" --daily'
        $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($existing) {
            $actions = @($existing.Actions)
            if ($actions.Count -ne 1 -or $actions[0].Execute -ne $expectedExe -or $actions[0].Arguments -ne $expectedArgs) {
                throw 'Another installation owns MaaXuexi-Daily-0900. It was not changed. See docs/installation.md.'
            }
            Write-Host 'Existing schedule preserved.'
        } else {
            $chosen = [DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(8)).Date.AddDays(1).AddHours(9)
            $boundary = New-Object DateTimeOffset($chosen, ([TimeSpan]::FromHours(8)))
            $trigger = New-ScheduledTaskTrigger -Daily -At $boundary.LocalDateTime
            $trigger.StartBoundary = $boundary.ToString('yyyy-MM-ddTHH:mm:sszzz')
            $action = New-ScheduledTaskAction -Execute $expectedExe -Argument $expectedArgs -WorkingDirectory $appRoot
            $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
            $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
            $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            $settings.Enabled = $false
            $null = Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings
            Write-Host 'Schedule registered disabled. Enable it in the console when ready.'
        }
    }
    Write-Host 'Installation complete. Existing settings and keys were preserved.'
    Write-Host 'Open the Start command file, then use http://127.0.0.1:8000/ .'
    Write-Host 'No learning task or paid API request was started.'
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
finally { Set-Location $previousLocation }
