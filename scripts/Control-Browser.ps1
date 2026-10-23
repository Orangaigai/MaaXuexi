# Keep browser data out of config backups. Move the whole dedicated profile only
# while closed; never merge, delete, or stop an unverified browser to migrate it.
function Get-ControlProfilePaths([string]$Root) {
    $rootPath = [IO.Path]::GetFullPath($Root)
    $legacy = Join-Path $rootPath 'config\control-browser'
    $profile = Join-Path $rootPath 'runtime\control-browser'
    foreach ($path in @($legacy,$profile)) {
        if (![IO.Path]::GetFullPath($path).StartsWith($rootPath.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) {
            throw 'Control browser profile must remain inside this installation.'
        }
    }
    foreach ($path in @($rootPath,(Join-Path $rootPath 'config'),(Join-Path $rootPath 'runtime'),(Join-Path $rootPath 'logs'),(Join-Path $rootPath 'logs\control-profile.lock'),$legacy,$profile)) {
        if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Control browser migration does not follow junctions or symbolic links.'
        }
    }
    return @($legacy,$profile)
}

function Get-ControlBrowserProfile([string]$Root) {
    $rootPath = [IO.Path]::GetFullPath($Root)
    $paths = Get-ControlProfilePaths $rootPath
    $legacy = $paths[0];$profile = $paths[1]
    if (!(Test-Path -LiteralPath $legacy)) { return $profile }
    if (Test-Path -LiteralPath $profile) {
        Write-Host 'Both control profiles exist; keeping the old profile without merging data.'
        return $legacy
    }
    $guard = $null
    try {
        $logs = Join-Path $rootPath 'logs'
        $null = New-Item -ItemType Directory -Path $logs -Force -ErrorAction Stop
        $guard = [IO.File]::Open((Join-Path $logs 'control-profile.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
        if (!(Test-Path -LiteralPath $legacy)) { return $profile }
        if (Test-Path -LiteralPath $profile) { return $legacy }
        $pattern = '(?:^|\s)--user-data-dir=(?:"'+[regex]::Escape($legacy)+'"|'+[regex]::Escape($legacy)+')(?=\s|$)'
        $items = Get-CimInstance Win32_Process -Filter "Name='msedge.exe' OR Name='chrome.exe'" -ErrorAction Stop
        if ($items | Where-Object { [string]::IsNullOrWhiteSpace($_.CommandLine) }) { return $legacy }
        if ($items | Where-Object { $_.CommandLine -match $pattern }) { return $legacy }
        $null = New-Item -ItemType Directory -Path (Join-Path $rootPath 'runtime') -Force -ErrorAction Stop
        # Both paths were checked inside this installation. An atomic rename
        # refuses an existing destination instead of merging/nesting profiles.
        [IO.Directory]::Move($legacy,$profile)
        return $profile
    } catch {
        Write-Host 'Control profile migration deferred; existing browser data kept.'
        return $legacy
    } finally {
        if ($guard) { $guard.Dispose() }
    }
}

# A dedicated browser profile lets this project close its own window only.
function Get-OwnedControlBrowser([string]$Root) {
    $recordPath = Join-Path $Root 'logs\control-browser.json'
    if (!(Test-Path -LiteralPath $recordPath)) { return }
    try {
        $record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $allowed = Get-ControlProfilePaths $Root
        if ($record.root -ne $Root -or $record.profile -notin $allowed) { return }
        $profile = $record.profile
        $proc = Get-Process -Id ([int]$record.pid) -ErrorAction Stop
        $started = if ($record.start_utc -is [DateTime]) { $record.start_utc.ToUniversalTime() } else { [DateTime]::Parse([string]$record.start_utc,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime() }
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
    $profile = Get-ControlBrowserProfile $Root
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
