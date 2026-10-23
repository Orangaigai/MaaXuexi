param([string]$ShortcutPath)
$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
if (!('MaaXuexiShellLink' -as [type])) { Add-Type -Path (Join-Path $PSScriptRoot 'ShellLink.cs') }
$appRoot = [MaaXuexiShellLink]::CanonicalPath($appRoot)
if (!$ShortcutPath) { $ShortcutPath = Join-Path $appRoot 'MaaXuexi.lnk' }
$ShortcutPath = [IO.Path]::GetFullPath($ShortcutPath)
if ([IO.Path]::GetExtension($ShortcutPath) -ne '.lnk') { throw 'ShortcutPath must end in .lnk.' }
$target = Join-Path $appRoot '.venv\Scripts\pythonw.exe'
$launcher = Join-Path $appRoot 'scripts\Launch-Hidden.py'
$icon = Join-Path $appRoot 'page\favicon.ico'
foreach ($file in @($target, $launcher, $icon)) {
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw ('Install the project first. Missing: ' + $file) }
}
$arguments = '"' + $launcher + '"'
if (Test-Path -LiteralPath $ShortcutPath) {
    $existing = [MaaXuexiShellLink]::Read($ShortcutPath)
    $existingLauncher = if ($existing[1] -match '^"([^"]+)"$') { [MaaXuexiShellLink]::CanonicalPath($Matches[1]) } else { '' }
    if ([MaaXuexiShellLink]::CanonicalPath($existing[0]) -ne $target -or $existingLauncher -ne $launcher) {
        throw 'This shortcut belongs to another target. It was not changed.'
    }
}
[MaaXuexiShellLink]::Write($ShortcutPath, $target, $arguments, $appRoot, $icon)
Write-Host ('Shortcut ready: ' + $ShortcutPath)
