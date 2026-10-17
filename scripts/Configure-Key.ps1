$ErrorActionPreference='Stop'
& (Join-Path $PSScriptRoot 'Start-MaaXuexi.ps1') -NoBrowser
if($LASTEXITCODE -ne 0) {exit 1}
Start-Process 'http://127.0.0.1:8000/local/api-key'