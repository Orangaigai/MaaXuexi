param([Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9_-]{1,80}$')][string]$RunId,
      [Parameter(Mandatory=$true)][int]$BackendPid)
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

try {
    $health=Invoke-RestMethod 'http://127.0.0.1:8000/api/status' -TimeoutSec 10
    if ($health.pid -ne $BackendPid -or $health.task_running -or $health.history_import.active -or $health.run_summary.run_id -ne $RunId -or
        $health.run_summary.status -ne 'completed' -or $health.completion_action.state -ne 'closing') {
        throw 'The completed session changed. No window or emulator was closed.'
    }
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
    Close-ControlBrowser $appRoot
    Write-Host 'Daily points saved. Configured MuMu instance and owned control window closed.'
    exit 0
} catch {
    Write-Host $_.Exception.Message
    exit 1
}
