param([Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9_-]{1,80}$')][string]$RunId,
      [Parameter(Mandatory=$true)][int]$BackendPid,
      [switch]$KeepEmulator, [switch]$KeepConsole)
$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'Control-Browser.ps1')

function Test-ConfiguredMuMuTarget([string]$CliPath,[string]$VmIndex) {
    return ($VmIndex -match '^\d+$' -and (Test-Path -LiteralPath $CliPath -PathType Leaf) -and
        (Split-Path -Leaf $CliPath) -in @('MuMuManager.exe','mumu-cli.exe'))
}

function Invoke-MuMuCommand([string]$CliPath,[string]$Arguments) {
    $proc = New-Object Diagnostics.Process
    $proc.StartInfo = New-Object Diagnostics.ProcessStartInfo
    $proc.StartInfo.FileName=$CliPath; $proc.StartInfo.Arguments=$Arguments
    $proc.StartInfo.UseShellExecute=$false; $proc.StartInfo.CreateNoWindow=$true
    $proc.StartInfo.RedirectStandardOutput=$true; $proc.StartInfo.RedirectStandardError=$true
    try {
        $null=$proc.Start(); $stdout=$proc.StandardOutput.ReadToEndAsync(); $stderr=$proc.StandardError.ReadToEndAsync()
        if (!$proc.WaitForExit(15000)) { $proc.Kill(); throw 'MuMu command timed out.' }
        $output=$stdout.GetAwaiter().GetResult(); $null=$stderr.GetAwaiter().GetResult()
        if ($proc.ExitCode -ne 0) { throw 'MuMu command did not succeed.' }
        return $output
    } finally { $proc.Dispose() }
}

function Test-AllMuMuInstancesStopped($Info) {
    if (!$Info) { return $false }
    $properties=@($Info.PSObject.Properties)
    if ($properties.Name -contains 'index') { $rows=@($Info) }
    else {
        if (!$properties.Count -or @($properties | Where-Object {$_.Name -notmatch '^\d+$'}).Count) { return $false }
        $rows=@($properties | ForEach-Object {$_.Value})
    }
    foreach ($row in $rows) {
        if ([string]$row.index -notmatch '^\d+$' -or $row.error_code -ne 0 -or
            $row.is_process_started -isnot [bool] -or $row.is_android_started -isnot [bool] -or
            $row.is_process_started -or $row.is_android_started) { return $false }
    }
    return $rows.Count -gt 0
}

function Close-IdleMuMuMain([string]$CliPath) {
    try {
        $all=Invoke-MuMuCommand $CliPath 'info -v all' | ConvertFrom-Json
        if (!(Test-AllMuMuInstancesStopped $all)) {
            Write-Host 'MuMu main application kept: another instance is running, or its state is not confirmed.'
            return $false
        }
        # Recheck immediately before graceful close; never kill the shared host.
        $all=Invoke-MuMuCommand $CliPath 'info -v all' | ConvertFrom-Json
        if (!(Test-AllMuMuInstancesStopped $all)) {
            Write-Host 'MuMu main application kept: instance state changed.'
            return $false
        }
        $null=Invoke-MuMuCommand $CliPath 'main close'
        Write-Host 'All MuMu instances stopped; requested graceful exit of the main application.'
        return $true
    } catch {
        Write-Host 'Target instance stopped; shared MuMu main application kept because its state or graceful exit was not confirmed.'
        return $false
    }
}

try {
    $health=Invoke-RestMethod 'http://127.0.0.1:8000/api/status' -TimeoutSec 10
    if ($health.pid -ne $BackendPid -or $health.task_running -or $health.history_import.active -or $health.run_summary.run_id -ne $RunId -or
        $health.run_summary.status -ne 'completed' -or $health.completion_action.state -ne 'closing') {
        throw 'The completed session changed. No window or emulator was closed.'
    }
    if (!$KeepEmulator) {
    $config=Get-Content -LiteralPath (Join-Path $appRoot 'config\emulator.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $cli=[string]$config.mumu_cli; $index=[string]$config.vm_index
    if (!(Test-ConfiguredMuMuTarget $cli $index)) {
        throw 'Could not verify the configured MuMu instance.'
    }
    $info=Invoke-MuMuCommand $cli ('info -v '+$index) | ConvertFrom-Json
    if ($info.PSObject.Properties.Name -contains $index) { $info=$info.$index }
    if ($info.is_process_started) {
        $null=Invoke-MuMuCommand $cli ('control -v '+$index+' shutdown')
        $stopped=$false
        for ($step=0; $step -lt 30; $step++) {
            $info=Invoke-MuMuCommand $cli ('info -v '+$index) | ConvertFrom-Json
            if ($info.PSObject.Properties.Name -contains $index) { $info=$info.$index }
            if ($info.is_process_started -eq $false) { $stopped=$true; break }
            Start-Sleep -Milliseconds 500
        }
        if (!$stopped) { throw 'MuMu shutdown was not confirmed. Control page preserved.' }
    }
    $null=Close-IdleMuMuMain $cli
    }
    if (!$KeepConsole) { Close-ControlBrowser $appRoot }
    Write-Host ('Daily points saved. Emulator closed: '+(!$KeepEmulator)+'; console closed: '+(!$KeepConsole)+'.')
    exit 0
} catch {
    Write-Host $_.Exception.Message
    exit 1
}
