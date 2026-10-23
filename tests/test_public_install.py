"""First-install safety: preserve settings, disabled jobs, ownership conflicts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name=='nt','Windows installer integration')
class PublicInstallTests(unittest.TestCase):
    def setup_copy(self):
        temp=tempfile.TemporaryDirectory(prefix='maa install 中文 ');self.addCleanup(temp.cleanup)
        root=Path(temp.name);(root/'scripts').mkdir();(root/'config').mkdir()
        for name in ('Install-MaaXuexi.ps1','Create-Shortcut.ps1','ShellLink.cs','Launch-Hidden.py'):
            shutil.copy2(ROOT/'scripts'/name,root/'scripts'/name)
        (root/'page').mkdir();shutil.copy2(ROOT/'page/favicon.ico',root/'page/favicon.ico')
        for name in ('local-task-plan','comment-policy'):
            shutil.copy2(ROOT/f'config/{name}.example.json',root/f'config/{name}.example.json')
        # Existing env stub prevents bootstrapping or package/network activity.
        (root/'.venv/Scripts').mkdir(parents=True);(root/'.venv/Scripts/python.exe').write_bytes(b'not executed')
        (root/'.venv/Scripts/pythonw.exe').write_bytes(b'not executed')
        mumu=root/'MuMu install/nx_main';mumu.mkdir(parents=True)
        for name in ('MuMuManager.exe','adb.exe'):(mumu/name).write_bytes(b'not executed')
        return root,mumu.parent

    def run_install(self,root,mumu,script='',skip_schedule=True):
        env=dict(os.environ,MAA_INSTALL_SCRIPT=str(root/'scripts/Install-MaaXuexi.ps1'),MAA_INSTALL_MUMU=str(mumu))
        tail='& $env:MAA_INSTALL_SCRIPT -MuMuRoot $env:MAA_INSTALL_MUMU -SkipDependencies'
        if skip_schedule:tail+=' -SkipSchedule'
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script+'\n'+tail],
            env=env,capture_output=True,text=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        return result

    def test_reinstall_preserves_existing_configuration_and_secret_bytes(self):
        root,mumu=self.setup_copy()
        keep={name:('keep-'+name).encode() for name in ['emulator.json','local-task-plan.json','local-schedule.json','comment-policy.json','deepseek-key.dpapi']}
        for name,value in keep.items():(root/'config'/name).write_bytes(value)
        result=self.run_install(root,mumu);self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertTrue(all((root/'config'/name).read_bytes()==value for name,value in keep.items()))

    def test_new_mumu_cli_layout_is_accepted_without_launching_it(self):
        root,mumu=self.setup_copy()
        (mumu/'nx_main/MuMuManager.exe').rename(mumu/'nx_main/mumu-cli.exe')
        result=self.run_install(root,mumu)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        config=json.loads((root/'config/emulator.json').read_text('utf-8-sig'))
        self.assertEqual(Path(config['mumu_cli']).name,'mumu-cli.exe')

    def test_first_install_creates_basics_and_a_disabled_owned_schedule(self):
        root,mumu=self.setup_copy()
        script=r'''
function Get-ScheduledTask { return $null }
function New-ScheduledTaskTrigger { param([switch]$Daily,$At); return [pscustomobject]@{StartBoundary=''} }
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory); return @{exe=$Execute;args=$Argument;cwd=$WorkingDirectory} }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel); return @{} }
function New-ScheduledTaskSettingsSet { param([switch]$StartWhenAvailable,$MultipleInstances,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries); return [pscustomobject]@{Enabled=$true} }
function Register-ScheduledTask {
 param($TaskName,$Action,$Trigger,$Principal,$Settings)
 if($TaskName -ne 'MaaXuexi-Daily-0900' -or $Settings.Enabled){throw 'Task must be disabled'}
 if(!$Action.args.EndsWith(' --daily') -or !$Trigger.StartBoundary.EndsWith('+08:00')){throw 'Wrong job'}
 Write-Host 'TEST_DISABLED_SCHEDULE'
}
'''
        result=self.run_install(root,mumu,script,False)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout);self.assertIn('TEST_DISABLED_SCHEDULE',result.stdout)
        plan=json.loads((root/'config/local-task-plan.json').read_text('utf-8-sig'))
        self.assertEqual(plan['tasklist'],['登录','选读文章','视听学习','本地频道'])
        self.assertFalse(json.loads((root/'config/comment-policy.json').read_text('utf-8-sig'))['enabled'])
        self.assertFalse((root/'config/deepseek-key.dpapi').exists())

    def test_another_installation_schedule_is_never_replaced(self):
        root,mumu=self.setup_copy()
        script=r'''
function Get-ScheduledTask { return @{Actions=@(@{Execute='Other installation/pythonw.exe';Arguments='other'})} }
function Register-ScheduledTask { throw 'MUST_NOT_REGISTER' }
function Disable-ScheduledTask { throw 'MUST_NOT_DISABLE' }
'''
        result=self.run_install(root,mumu,script,False)
        self.assertNotEqual(result.returncode,0);self.assertIn('Another installation owns',result.stdout)
        self.assertNotIn('MUST_NOT_',result.stdout+result.stderr)

    def shortcut_command(self,root,command):
        prefix="Add-Type -Path (Join-Path $env:MAA_SHORTCUT_ROOT 'scripts/ShellLink.cs')\n"
        return subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',prefix+command],
            env=dict(os.environ,MAA_SHORTCUT_ROOT=str(root)),capture_output=True,text=True,timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW)

    def test_installed_shortcut_uses_hidden_launcher_and_project_icon(self):
        root,mumu=self.setup_copy()
        result=self.run_install(root,mumu);self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=self.shortcut_command(root,r'''
$link=[MaaXuexiShellLink]::Read((Join-Path $env:MAA_SHORTCUT_ROOT 'MaaXuexi.lnk'))
if($link[0] -ne (Join-Path $env:MAA_SHORTCUT_ROOT '.venv\Scripts\pythonw.exe')){throw 'wrong target'}
if($link[1] -ne ('"'+(Join-Path $env:MAA_SHORTCUT_ROOT 'scripts\Launch-Hidden.py')+'"')){throw 'wrong args'}
if($link[3] -ne ((Join-Path $env:MAA_SHORTCUT_ROOT 'page\favicon.ico')+',0')){throw 'wrong icon'}
if($link[2] -ne $env:MAA_SHORTCUT_ROOT){throw 'wrong directory'}
''')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        # A same-target reinstall updates in place without starting the stub executable.
        result=self.run_install(root,mumu);self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_existing_unrelated_shortcut_is_not_overwritten(self):
        root,mumu=self.setup_copy()
        result=self.shortcut_command(root,r'''
[MaaXuexiShellLink]::Write((Join-Path $env:MAA_SHORTCUT_ROOT 'MaaXuexi.lnk'),(Join-Path $env:SystemRoot 'notepad.exe'),'',$env:MAA_SHORTCUT_ROOT,(Join-Path $env:MAA_SHORTCUT_ROOT 'page/favicon.ico'))
''')
        self.assertEqual(result.returncode,0,result.stderr)
        previous=(root/'MaaXuexi.lnk').read_bytes()
        result=self.run_install(root,mumu)
        self.assertNotEqual(result.returncode,0);self.assertIn('another target',result.stdout)
        self.assertEqual((root/'MaaXuexi.lnk').read_bytes(),previous)

    def test_missing_hidden_launcher_executable_does_not_create_a_broken_link(self):
        root,mumu=self.setup_copy();(root/'.venv/Scripts/pythonw.exe').unlink()
        result=self.run_install(root,mumu)
        self.assertNotEqual(result.returncode,0);self.assertIn('Install the project first',result.stdout)
        self.assertFalse((root/'MaaXuexi.lnk').exists())
