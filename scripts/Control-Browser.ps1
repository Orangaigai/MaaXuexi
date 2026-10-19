# A dedicated browser profile lets this project close its own window only.
function Get-OwnedControlBrowser([string]$Root) {
    $recordPath = Join-Path $Root 'logs\control-browser.json'
    if (!(Test-Path -LiteralPath $recordPath)) { return }
    try {
        $record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $profile = Join-Path $Root 'config\control-browser'
        if ($record.root -ne $Root -or $record.profile -ne $profile) { return }
        $proc = Get-Process -Id ([int]$record.pid) -ErrorAction Stop
        $started = [DateTime]::Parse([string]$record.start_utc,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()
        if ($proc.Path -ne $record.executable -or $proc.StartTime.ToUniversalTime().Ticks -ne $started.Ticks) { return }
        $command = (Get-CimInstance Win32_Process -Filter ('ProcessId='+$proc.Id)).CommandLine
        $profilePattern = '(?:^|\s)--user-data-dir=(?:"'+[regex]::Escape($profile)+'"|'+[regex]::Escape($profile)+')(?=\s|$)'
        if ($proc.ProcessName -notin @('msedge','chrome') -or $command -notmatch $profilePattern) { return }
        return $proc
    } catch { return }
}

function Open-ControlBrowser([string]$Root) {
    $owned = Get-OwnedControlBrowser $Root
    if ($owned -and $owned.MainWindowHandle -ne 0) {
        if (!('MaaControlWindow' -as [type])) {
            Add-Type 'using System; using System.Runtime.InteropServices; public static class MaaControlWindow { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int n); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); }'
        }
        $null = [MaaControlWindow]::ShowWindow($owned.MainWindowHandle,9)
        $null = [MaaControlWindow]::SetForegroundWindow($owned.MainWindowHandle)
        return
    }
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    $browser = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (!$browser) { $null = Start-Process -FilePath 'http://127.0.0.1:8000/'; return }
    $profile = Join-Path $Root 'config\control-browser'
    $arguments = @('--app=http://127.0.0.1:8000/',('--user-data-dir="'+$profile+'"'),
        '--no-first-run','--no-default-browser-check','--disable-background-mode',
        '--disable-extensions','--window-size=1180,940')
    $null = Start-Process -FilePath $browser -ArgumentList $arguments -WindowStyle Normal
    $pattern = '(?:^|\s)--user-data-dir=(?:"'+[regex]::Escape($profile)+'"|'+[regex]::Escape($profile)+')(?=\s|$)'
    for ($step=0; $step -lt 40; $step++) {
        $items = Get-CimInstance Win32_Process -Filter "Name='msedge.exe' OR Name='chrome.exe'"
        foreach ($item in $items) {
            if ($item.CommandLine -notmatch $pattern -or $item.CommandLine -match '(?:^|\s)--type=') { continue }
            $proc = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
            if (!$proc -or $proc.MainWindowHandle -eq 0) { continue }
            $record = @{pid=$proc.Id;root=$Root;profile=$profile;executable=$proc.Path;start_utc=$proc.StartTime.ToUniversalTime().ToString('O')}
            $folder = Join-Path $Root 'logs'
            $null = New-Item -ItemType Directory -Path $folder -Force
            [IO.File]::WriteAllText((Join-Path $folder 'control-browser.json'),($record|ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
            return
        }
        Start-Sleep -Milliseconds 150
    }
    Write-Host 'Control page opened; browser ownership could not be confirmed, so it will not be force-closed.'
}

function Close-ControlBrowser([string]$Root) {
    $owned = Get-OwnedControlBrowser $Root
    if (!$owned) { return }
    $null = $owned.CloseMainWindow()
    if (!$owned.WaitForExit(5000)) {
        $confirmed = Get-OwnedControlBrowser $Root
        if (!$confirmed -or $confirmed.Id -ne $owned.Id) { throw 'Control browser ownership changed; no process stopped.' }
        Stop-Process -Id $confirmed.Id -ErrorAction Stop
        if (!$confirmed.WaitForExit(5000)) { throw 'The owned control browser did not exit.' }
    }
}
