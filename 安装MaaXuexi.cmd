@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Install-MaaXuexi.ps1"
if errorlevel 1 echo Installation failed. See docs/installation.md.
pause
