"""Actual PowerShell 5.1 boot helper: timeout recovery, task races, deadline."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell regression')
class ColdStatusTests(unittest.TestCase):
    def run_boot(self, scenario):
        source = Path(__file__).resolve().parents[1]/'scripts/Start-MaaXuexi.ps1'
        with tempfile.TemporaryDirectory(prefix='maa-cold-status-') as directory:
            root = Path(directory)
            (root/'config').mkdir()
            cli = root/'fake-mumu.cmd'
            cli.write_text('@echo off\r\necho {"is_process_started":true,"adb_host_ip":"127.0.0.1","adb_port":16384}\r\n', encoding='ascii')
            (root/'config/emulator.json').write_text(json.dumps(dict(mumu_cli=str(cli),
                vm_index=0, adb_path='unused-adb.exe')), encoding='utf-8')
            wrapper = root/'test.ps1'
            wrapper.write_text(r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:MAA_TEST_LAUNCHER,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Invalid launcher syntax'}
foreach($name in @('Test-ActiveLocalTask','Connect-DeviceWhenIdle','Connect-ConfiguredEmulator')){
    $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
    if(!$fn){throw ('Missing '+$name)}
    Invoke-Expression $fn.Extent.Text
}
$appRoot=$env:MAA_TEST_ROOT
$script:clock=[DateTime]'2026-10-16T09:00:00'
$script:statuses=0;$script:posts=0;$script:adb=0;$script:shows=0;$script:discoveries=0
function Get-Date { return $script:clock }
function Start-Sleep {
    param($Milliseconds)
    $script:clock=$script:clock.AddSeconds($(if($env:MAA_TEST_SCENARIO -eq 'offline'){120}else{0.5}))
}
function Invoke-RestMethod {
    param($Uri,$Method,$ContentType,$Body,$TimeoutSec)
    if($Uri.EndsWith('/api/status')){
        $script:statuses++
        if($script:statuses -eq 2 -or ($env:MAA_TEST_SCENARIO -eq 'offline' -and $script:statuses -gt 1)){
            throw (New-Object Net.WebException('The operation has timed out.',$null,[Net.WebExceptionStatus]::Timeout,$null))
        }
        return @{status='running';backend='deepseek-local';task_running=($env:MAA_TEST_SCENARIO -eq 'active' -and $script:statuses -ge 3);paused=$false}
    }
    if($Uri.EndsWith('/api/get_device')){
        $script:discoveries++
        return @{devices=@(@{address='127.0.0.1:16384'})}
    }
    if($Uri.EndsWith('/api/connect_device')){
        $script:posts++
        return @{status='success'}
    }
    throw 'Unexpected endpoint'
}
function Invoke-AdbProbe {
    param($AdbPath,$Address,$Mode)
    $script:adb++
    return @{ExitCode=0;StdOut=$(if($Mode -eq 'State'){'device'}else{'1'})}
}
function Show-EmulatorWindow { $script:shows++; return $true }
$failure=$null
try { Connect-ConfiguredEmulator } catch { $failure=$_.Exception.Message }
@{failure=$failure;statuses=$script:statuses;posts=$script:posts;adb=$script:adb;
  shows=$script:shows;discoveries=$script:discoveries;elapsed=($script:clock-[DateTime]'2026-10-16T09:00:00').TotalSeconds} | ConvertTo-Json -Compress
''', encoding='utf-8-sig')
            result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(wrapper)],
                env=dict(os.environ, MAA_TEST_ROOT=str(root), MAA_TEST_LAUNCHER=str(source), MAA_TEST_SCENARIO=scenario),
                capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])

    def test_boot_timeout_recovers_and_connects_once(self):
        result = self.run_boot('idle')
        self.assertIsNone(result['failure'])
        self.assertEqual(result['posts'], 1)
        self.assertEqual(result['discoveries'], 1)
        self.assertEqual(result['shows'], 1)
        self.assertGreaterEqual(result['statuses'], 6)

    def test_active_task_after_unknown_status_is_preserved(self):
        result = self.run_boot('active')
        self.assertIsNone(result['failure'])
        self.assertEqual(result['statuses'], 3)
        self.assertEqual((result['posts'], result['adb'], result['shows'], result['discoveries']), (0,0,0,0))

    def test_persistent_timeout_stops_at_original_boot_deadline(self):
        result = self.run_boot('offline')
        self.assertIn('within 600 seconds', result['failure'])
        self.assertEqual(result['elapsed'], 600)
        self.assertEqual((result['posts'], result['adb'], result['shows'], result['discoveries']), (0,0,0,0))


if __name__ == '__main__':
    unittest.main()
