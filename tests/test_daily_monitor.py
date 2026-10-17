"""Run the real daily script against a simulated transient local-status failure."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == 'nt', 'Windows scheduled launcher')
class DailyMonitorTests(unittest.TestCase):
    def test_owner_pause_is_monitored_without_exiting_or_resubmitting(self):
        source = Path(__file__).resolve().parents[1] / 'scripts/Run-Daily.ps1'
        with tempfile.TemporaryDirectory(prefix='maa-owner-monitor-') as temporary:
            root = Path(temporary)
            (root/'scripts').mkdir(); (root/'config').mkdir()
            shutil.copyfile(source, root/'scripts/Run-Daily.ps1')
            (root/'scripts/Start-MaaXuexi.ps1').write_text('exit 0\n', encoding='utf-8-sig')
            launcher = str(root/'scripts/Run-Daily.ps1').replace("'", "''")
            wrapper = root/'test.ps1'
            wrapper.write_text("""
$ErrorActionPreference='Stop'
$global:statusCalls=0
$global:submissions=0
function Start-Sleep { param($Seconds) }
function Start-Process { param($FilePath) }
function Invoke-RestMethod {
    param($Uri,$Method,$ContentType,$Body,$TimeoutSec)
    if($Uri.EndsWith('/api/start')) { $global:submissions++; return @{status='success'} }
    if(!$Uri.EndsWith('/api/status')) { throw 'Unexpected request' }
    $global:statusCalls++
    return [pscustomobject]@{connected=$true;api_key_configured=$true;
        task_running=($global:statusCalls -ge 3 -and $global:statusCalls -le 5);
        paused=($global:statusCalls -eq 3 -or $global:statusCalls -eq 4);
        last_error=$null;run_summary=@{status='completed'}}
}
& '""" + launcher + """'
@{exit_code=$LASTEXITCODE;submissions=$global:submissions;status_calls=$global:statusCalls} | ConvertTo-Json -Compress
""", encoding='utf-8-sig')
            result = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper)],
                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            observation = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(observation, dict(exit_code=0,submissions=1,status_calls=6))
            log = next((root/'logs').glob('daily-*.log')).read_text('utf-8-sig')
            self.assertEqual(log.count('Waiting for owner verification'), 1)
            self.assertIn('monitoring the same task without another submission', log)
            self.assertIn('All selected task scores were verified', log)

    def test_transient_status_timeout_keeps_same_submission_and_recovers(self):
        source = Path(__file__).resolve().parents[1] / 'scripts/Run-Daily.ps1'
        with tempfile.TemporaryDirectory(prefix='maa-monitor-test-') as temporary:
            root = Path(temporary)
            (root/'scripts').mkdir()
            (root/'config').mkdir()
            shutil.copyfile(source, root/'scripts/Run-Daily.ps1')
            (root/'scripts/Start-MaaXuexi.ps1').write_text(
                'param([switch]$NoBrowser,[switch]$Reconnect)\n'
                '$global:startupNoBrowser=[bool]$NoBrowser\nexit 0\n', encoding='utf-8-sig')
            (root/'config/local-task-plan.json').write_text(json.dumps(dict(tasklist=['登录'])), encoding='utf-8')
            launcher = str(root/'scripts/Run-Daily.ps1').replace("'", "''")
            wrapper = root/'test.ps1'
            wrapper.write_text("""
$ErrorActionPreference='Stop'
$global:statusCalls=0
$global:submissions=0
function Start-Sleep { param($Seconds,$Milliseconds) }
function Invoke-RestMethod {
    param($Uri,$Method,$ContentType,$Body,$TimeoutSec)
    if($Uri.EndsWith('/api/start')) {
        $global:submissions++
        return [pscustomobject]@{status='success'}
    }
    $global:statusCalls++
    if($global:statusCalls -eq 3){throw 'Simulated temporary status timeout'}
    return [pscustomobject]@{connected=$true;api_key_configured=$true;task_running=($global:statusCalls -eq 4);paused=$false;last_error=$null;run_summary=@{status='completed'}}
}
& '""" + launcher + """'
@{exit_code=$LASTEXITCODE;submissions=$global:submissions;status_calls=$global:statusCalls;startup_no_browser=$global:startupNoBrowser} | ConvertTo-Json -Compress
""", encoding='utf-8-sig')
            result = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper)],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            observation = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(observation, dict(exit_code=0, submissions=1, status_calls=5, startup_no_browser=False))
            log = next((root/'logs').glob('daily-*.log')).read_text('utf-8-sig')
            self.assertIn('temporarily unavailable', log)
            self.assertIn('monitoring recovered', log)
            self.assertIn('All selected task scores were verified', log)

    def test_existing_task_opens_progress_without_resubmitting_even_if_browser_fails(self):
        source = Path(__file__).resolve().parents[1] / 'scripts/Run-Daily.ps1'
        for browser_fails in (False, True):
            with self.subTest(browser_fails=browser_fails), tempfile.TemporaryDirectory(prefix='maa-browser-test-') as temporary:
                root = Path(temporary)
                (root/'scripts').mkdir()
                shutil.copyfile(source, root/'scripts/Run-Daily.ps1')
                launcher = str(root/'scripts/Run-Daily.ps1').replace("'", "''")
                wrapper = root/'test.ps1'
                wrapper.write_text("""
$ErrorActionPreference='Stop'
$global:opens=0
function Start-Process {
    param($FilePath)
    if($FilePath -ne 'http://127.0.0.1:8000/'){throw 'Unexpected URL'}
    $global:opens++
    if(""" + ('$true' if browser_fails else '$false') + """){throw 'Simulated browser failure'}
}
function Invoke-RestMethod {
    param($Uri,$Method,$ContentType,$Body,$TimeoutSec)
    if(!$Uri.EndsWith('/api/status')){throw 'Unexpected task submission'}
    return [pscustomobject]@{task_running=$true}
}
& '""" + launcher + """'
@{exit_code=$LASTEXITCODE;browser_opens=$global:opens} | ConvertTo-Json -Compress
""", encoding='utf-8-sig')
                result = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper)],
                    capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode, 0, result.stderr)
                observation = json.loads(result.stdout.strip().splitlines()[-1])
                self.assertEqual(observation, dict(exit_code=0, browser_opens=1))


if __name__ == '__main__':
    unittest.main()
