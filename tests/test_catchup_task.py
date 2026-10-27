"""Run real PowerShell/XML construction with all task mutations mocked."""
import json
import os
from pathlib import Path
import subprocess
import unittest


@unittest.skipUnless(os.name=='nt','Windows task XML')
class CatchUpTaskTests(unittest.TestCase):
    def test_owned_event_task_modes_and_foreign_task_protection(self):
        root=Path(__file__).resolve().parents[1]
        code=r'''
$ErrorActionPreference='Stop'
. $env:MAA_CATCHUP_SCRIPT
$global:root=Join-Path $env:TEMP 'maa catch-up 中文 & probe'
$global:exe=Join-Path $global:root '.venv\Scripts\pythonw.exe'
$global:dailyArgs='"'+(Join-Path $global:root 'scripts\Launch-Hidden.py')+'" --daily'
$global:main=[pscustomobject]@{Actions=@([pscustomobject]@{Execute=$global:exe;Arguments=$global:dailyArgs});Principal=[pscustomobject]@{UserId='S-1-5-21-111-222-333-1001'}}
$global:helper=$null;$global:registered=0;$global:enabled=0;$global:disabled=0
function Get-ScheduledTask { [CmdletBinding()]param($TaskName)
 if($TaskName -eq 'MaaXuexi-Daily-0900'){return $global:main};return $global:helper }
function Register-ScheduledTask { [CmdletBinding()]param($TaskName,$Xml,[switch]$Force)
 $global:registered++;$global:created=[xml]$Xml
 $global:helper=[pscustomobject]@{Description='MaaXuexi immediate catch-up v1';State='Ready';Actions=@([pscustomobject]@{Execute=$global:created.Task.Actions.Exec.Command;Arguments=$global:created.Task.Actions.Exec.Arguments;WorkingDirectory=$global:created.Task.Actions.Exec.WorkingDirectory})} }
function Enable-ScheduledTask { [CmdletBinding()]param($TaskName) $global:enabled++;$global:helper.State='Ready' }
function Disable-ScheduledTask { [CmdletBinding()]param($TaskName) $global:disabled++;$global:helper.State='Disabled' }
Sync-MaaCatchUpTask $global:root $true
$xml=$global:created
if($xml.Task.Actions.Exec.WorkingDirectory -ne $global:root){throw 'Working directory escaping changed'}
if($xml.Task.Principals.Principal.UserId -ne $global:main.Principal.UserId){throw 'Wrong Windows identity'}
if($xml.Task.Principals.Principal.RunLevel -ne 'LeastPrivilege'){throw 'Elevated unexpectedly'}
if($xml.Task.Triggers.LogonTrigger.UserId -ne $global:main.Principal.UserId){throw 'Wrong logon user'}
if($xml.Task.Triggers.EventTrigger.Subscription -notmatch 'EventID=10000 or EventID=4004'){throw 'Network trigger missing'}
if($xml.Task.Triggers.EventTrigger.Subscription -notmatch 'Power-Troubleshooter'){throw 'Resume trigger missing'}
if($xml.Task.Settings.StartWhenAvailable -ne 'false'){throw 'New catch-up still queues delayed starts'}
if($xml.Task.Settings.MultipleInstancesPolicy -ne 'IgnoreNew'){throw 'Duplicate instances allowed'}
Sync-MaaCatchUpTask $global:root $false
Sync-MaaCatchUpTask $global:root $true
if($global:registered -ne 1 -or $global:disabled -ne 1 -or $global:enabled -ne 1){throw 'Existing task was needlessly recreated or modes not synced'}
$global:helper.Actions[0].Execute='C:\unrelated\pythonw.exe'
$refused=$false
try { Sync-MaaCatchUpTask $global:root $true } catch { $refused=$true }
if(!$refused -or $global:registered -ne 1){throw 'Foreign task was overwritten'}
@{registered=$global:registered;enabled=$global:enabled;disabled=$global:disabled;foreign_refused=$refused}|ConvertTo-Json -Compress
'''
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],capture_output=True,
            encoding='utf-8-sig',errors='replace',timeout=15,env=dict(os.environ,MAA_CATCHUP_SCRIPT=str(root/'scripts/CatchUp-Task.ps1')),
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(json.loads(result.stdout)['foreign_refused'])


if __name__=='__main__':unittest.main()
