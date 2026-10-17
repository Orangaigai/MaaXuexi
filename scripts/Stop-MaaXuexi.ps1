$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
$matching = @()
$recordPath = Join-Path $appRoot 'logs\server-process.json'
if (Test-Path -LiteralPath $recordPath) {
    try {
        $records = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($record in $records) {
            $recordedProc = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
            $recordStart = if ($record.start_utc -is [DateTime]) { $record.start_utc.ToUniversalTime() } else { [DateTime]::Parse([string]$record.start_utc,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime() }
            if ($recordedProc -and $record.root -eq $appRoot -and $recordedProc.Path -eq $record.executable -and $recordedProc.StartTime.ToUniversalTime().Ticks -eq $recordStart.Ticks) { $matching += $recordedProc }
        }
    } catch { }
}
if ($matching.Count -eq 0) { Write-Host 'MaaXuexi is not running.'; exit 0 }
try { $null = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/stop' -Method Post -TimeoutSec 3 } catch { }
$stopping = @($matching | Sort-Object Id -Unique)
foreach ($proc in $stopping) { Stop-Process -Id $proc.Id -ErrorAction SilentlyContinue }
foreach ($proc in $stopping) {
    if (!$proc.WaitForExit(8000)) { throw 'A recorded MaaXuexi process has not exited. Startup was stopped to avoid a port conflict.' }
}
Write-Host 'MaaXuexi has stopped.'
exit 0
