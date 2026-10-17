param()
$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
$previousLocation = Get-Location
try {
    Set-Location -LiteralPath $appRoot
    & (Join-Path $appRoot '.venv\Scripts\python.exe') -m unittest discover -s (Join-Path $appRoot 'tests') -p 'test_*.py' -q
    if ($LASTEXITCODE -ne 0) { throw 'Offline regression checks failed.' }
    & (Join-Path $PSScriptRoot 'Run-Daily.ps1') -CheckOnly
    if ($LASTEXITCODE -ne 0) { throw 'Daily launcher checks failed.' }
    $health = Invoke-RestMethod 'http://127.0.0.1:8000/api/status' -TimeoutSec 10
    if (!$health.connected) { throw 'The actual emulator connection is offline. Click Execute to reconnect, or restart the launcher.' }
    [pscustomobject]@{version=$health.local_version;connected=$health.connected;task_running=$health.task_running;key_configured=$health.api_key_configured} | ConvertTo-Json
    Write-Host 'Checks passed. No learning task or paid API request was started.'
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
finally { Set-Location $previousLocation }
