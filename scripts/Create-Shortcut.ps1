param([string]$ShortcutPath)
$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
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
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut($ShortcutPath)
if ((Test-Path -LiteralPath $ShortcutPath) -and ($shortcut.TargetPath -ne $target -or $shortcut.Arguments -ne $arguments)) {
    throw 'This shortcut belongs to another target. It was not changed.'
}
$shortcut.TargetPath = $target
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $appRoot
$shortcut.IconLocation = $icon + ',0'
$shortcut.Description = 'MaaXuexi task console'
$shortcut.Save()
Write-Host ('Shortcut ready: ' + $ShortcutPath)
