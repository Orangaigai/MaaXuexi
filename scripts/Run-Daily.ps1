param([switch]$CheckOnly, [switch]$NoBrowser, [switch]$VerificationFirst)
$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
$baseUrl = 'http://127.0.0.1:8000'
$taskNames = @('\u767b\u5f55', '\u9009\u8bfb\u6587\u7ae0', '\u89c6\u542c\u5b66\u4e60', '\u672c\u5730\u9891\u9053', '\u8da3\u5473\u7b54\u9898', '\u8ba2\u9605', '\u6bcf\u65e5\u7b54\u9898') | ForEach-Object { ('"'+$_+'"') | ConvertFrom-Json }
$planPath = Join-Path $appRoot 'config\local-task-plan.json'
if (Test-Path -LiteralPath $planPath) {
    $plan = Get-Content -LiteralPath $planPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $allowedTasks = @($taskNames) + ('"\u53d1\u8868\u89c2\u70b9"' | ConvertFrom-Json)
    $configured = @($plan.tasklist | Select-Object -Unique)
    if (!$configured.Count -or @($configured | Where-Object {$_ -notin $allowedTasks}).Count) { throw 'Invalid local task plan.' }
    $taskNames = $configured
}
if ($CheckOnly) {
    foreach ($relative in @('scripts\Start-MaaXuexi.ps1','backend\webui.py','config\emulator.json','config\deepseek-key.dpapi')) {
        if (!(Test-Path -LiteralPath (Join-Path $appRoot $relative))) { throw "Missing required file: $relative" }
    }
    [pscustomobject]@{mode='check_only';tasks=$taskNames;local_url=$baseUrl;fast_answer=$false;debug=$false;unattended=$true} | ConvertTo-Json
    exit 0
}
$logRoot = Join-Path $appRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$logPath = Join-Path $logRoot ('daily-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
function Write-RunLog([string]$message) { Add-Content -LiteralPath $logPath -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss')+' '+$message) -Encoding UTF8 }
$runLock = $null
$powerHeld = $false
try {
    try { $runLock = [IO.File]::Open((Join-Path $logRoot 'daily-run.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None) }
    catch [IO.IOException] { Write-RunLog 'Another daily launcher is running; skipped.'; exit 0 }
    $health = $null
    try { $health = Invoke-RestMethod ($baseUrl+'/api/status') -TimeoutSec 5 } catch { }
    if ($health -and $health.task_running) {
        if (!$NoBrowser) {
            try { Start-Process -FilePath ($baseUrl+'/') }
            catch { Write-RunLog ('Could not open the browser. Progress page: '+$baseUrl+'/') }
        }
        Write-RunLog 'A learning task is already running; skipped.'
        exit 0
    }
    Add-Type 'using System; using System.Runtime.InteropServices; public static class MaaDailyPowerState { [DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags); }'
    $powerHeld = [MaaDailyPowerState]::SetThreadExecutionState([uint32]2147483649) -ne 0
    Write-RunLog 'Starting local backend and emulator.'
    & (Join-Path $appRoot 'scripts\Start-MaaXuexi.ps1') -NoBrowser:$NoBrowser -Reconnect *>&1 | ForEach-Object { Write-RunLog ('Startup: '+[string]$_) }
    if ($LASTEXITCODE -ne 0) { throw 'Startup helper failed.' }
    $health = Invoke-RestMethod ($baseUrl+'/api/status') -TimeoutSec 10
    if (!$health.connected) { throw 'The emulator is not connected.' }
    $answerTasks = @('\u6bcf\u65e5\u7b54\u9898','\u8da3\u5473\u7b54\u9898','\u53d1\u8868\u89c2\u70b9') | ForEach-Object { ('"'+$_+'"') | ConvertFrom-Json }
    if (@($taskNames | Where-Object { $_ -in $answerTasks }).Count -and !$health.api_key_configured) { throw 'DeepSeek API key is not configured.' }
    $payload = @{tasklist=$taskNames;fast_answer=$false;debug=$false;unattended=$true} | ConvertTo-Json -Compress
    $startPath = if ($VerificationFirst) { '/api/local/verify-full-flow' } else { '/api/start' }
    if ($VerificationFirst) { Write-RunLog 'Temporary verification-first acceptance; saved daily plan is unchanged.' }
    $result = Invoke-RestMethod ($baseUrl+$startPath) -Method Post -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 10
    if ($result.status -ne 'success') { Write-RunLog 'Task start was declined; check the control page.'; exit 1 }
    Write-RunLog ('Submitted '+$taskNames.Count+' selected tasks. API key is not logged.')
    $deadline = (Get-Date).AddMinutes(45)
    $monitorFailures = 0
    $lastCheck = Get-Date
    $waitingForOwner = $false
    do {
        Start-Sleep -Seconds 5
        $checkedAt = Get-Date
        if ($health -and $health.paused) { $deadline = $deadline.Add($checkedAt - $lastCheck) }
        $lastCheck = $checkedAt
        if ((Get-Date) -gt $deadline) {
            Write-RunLog 'Time limit reached; requesting task stop.'
            try { $null = Invoke-RestMethod ($baseUrl+'/api/stop') -Method Post -TimeoutSec 10 } catch { Write-RunLog 'Stop request could not be confirmed; check the control page.' }
            exit 3
        }
        try {
            $health = Invoke-RestMethod ($baseUrl+'/api/status') -TimeoutSec 15
            if ($monitorFailures) { Write-RunLog 'Backend status monitoring recovered; continuing the existing task.' }
            $monitorFailures = 0
        } catch {
            $monitorFailures += 1
            Write-RunLog ('Backend status temporarily unavailable; waiting for the existing task, without resubmitting ('+$monitorFailures+').')
            $health = [pscustomobject]@{task_running=$true;paused=$false}
            continue
        }
        if ($health.paused) {
            if (!$waitingForOwner) { Write-RunLog 'Waiting for owner verification in MuMu; the existing task will resume automatically. Do not resubmit.' }
            $waitingForOwner = $true
        } elseif ($waitingForOwner) {
            Write-RunLog 'Owner wait ended; monitoring the same task without another submission.'
            $waitingForOwner = $false
        }
    } while ($health.task_running)
    if ($health.run_summary.status -eq 'needs_human') { Write-RunLog 'User action required. Unattended run ended; completed scores retained. No further app operation was attempted.'; exit 2 }
    if ($health.last_error) { Write-RunLog 'Task reported an error. Check the control page and backend stderr logs.'; exit 1 }
    if ($health.run_summary.status -ne 'completed') { Write-RunLog 'Task stopped without confirmed full scores. Check the control page.'; exit 1 }
    $credited = @($health.run_summary.tasks.PSObject.Properties | Where-Object { $_.Value.state -eq 'credited' })
    $skippedModes = @($health.run_summary.tasks.PSObject.Properties | Where-Object { $_.Value.state -eq 'skipped_mode' })
    if ($skippedModes.Count) { Write-RunLog 'Competitive quiz skipped by owner policy. Other selected task scores verified; no points claimed for the skipped mode.' }
    elseif ($credited.Count) { Write-RunLog 'All selected tasks verified; the fun quiz first-round credit is recorded at its actual score, without rematching.' }
    else { Write-RunLog 'All selected task scores were verified complete or already full.' }
} catch {
    Write-RunLog ('Daily launcher failed: '+$_.Exception.Message)
    exit 1
} finally {
    if ($powerHeld) { $null = [MaaDailyPowerState]::SetThreadExecutionState([uint32]2147483648) }
    if ($runLock) { $runLock.Dispose() }
}
