"""Run the real launcher probe under Windows PowerShell 5.1 error semantics."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import unittest


class LauncherTests(unittest.TestCase):
    def run_powershell(self, script, extra_env=None):
        env = dict(os.environ, MAA_TEST_LAUNCHER=str(Path(__file__).resolve().parents[1]/'scripts/Start-MaaXuexi.ps1'))
        env.update(extra_env or {})
        prefix = r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:MAA_TEST_LAUNCHER,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Launcher PowerShell syntax error.'}
foreach($name in @('Get-InstalledBackendVersion','Wait-BackendPortFree','Test-ActiveLocalTask','Connect-DeviceWhenIdle')) {
    $function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    if(!$function){throw ('Missing helper: '+$name)}
    Invoke-Expression $function.Extent.Text
}
'''
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',prefix+script],
            capture_output=True,text=True,timeout=20,env=env,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell regression')
    def test_connect_conflict_is_success_only_when_a_local_task_has_started(self):
        for code, active_after, expected_failure, expected_posts in (
                (409, True, False, 1), (409, False, True, 1),
                (503, True, True, 1), (200, False, False, 1)):
            with self.subTest(code=code, active_after=active_after):
                result = self.run_powershell(r'''
Add-Type 'using System.Net; public class MaaTestResponse : WebResponse { public HttpStatusCode StatusCode { get; set; } }'
$script:statusCalls=0
$script:posts=0
function Invoke-RestMethod {
    param($Uri,$Method,$ContentType,$Body,$TimeoutSec)
    if($Uri.EndsWith('/api/status')){
        $script:statusCalls++
        return @{status='running';backend='deepseek-local';task_running=($script:statusCalls -gt 1 -and $env:MAA_ACTIVE_AFTER -eq 'true');paused=$false}
    }
    if(!$Uri.EndsWith('/api/connect_device')){throw 'Unexpected endpoint'}
    $script:posts++
    if([int]$env:MAA_HTTP_CODE -eq 200){return @{status='success'}}
    $response=New-Object MaaTestResponse
    $response.StatusCode=[Net.HttpStatusCode][int]$env:MAA_HTTP_CODE
    throw (New-Object Net.WebException('Simulated connect error',$null,[Net.WebExceptionStatus]::ProtocolError,$response))
}
$failed=$false
try { Connect-DeviceWhenIdle @{address='127.0.0.1:16384'} } catch { $failed=$true }
@{failed=$failed;posts=$script:posts} | ConvertTo-Json -Compress
''', dict(MAA_HTTP_CODE=str(code), MAA_ACTIVE_AFTER=str(active_after).lower()))
                self.assertEqual(result, dict(failed=expected_failure, posts=expected_posts))

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell regression')
    def test_active_task_never_receives_a_reconnect_request(self):
        result = self.run_powershell(r'''
$script:posts=0
function Invoke-RestMethod {
    param($Uri,$Method,$ContentType,$Body,$TimeoutSec)
    if($Uri.EndsWith('/api/status')){return @{status='running';backend='deepseek-local';task_running=$true;paused=$false}}
    $script:posts++
    throw 'Should not reconnect an active task'
}
Connect-DeviceWhenIdle @{address='127.0.0.1:16384'}
@{posts=$script:posts} | ConvertTo-Json -Compress
''')
        self.assertEqual(result, dict(posts=0))

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell regression')
    def test_launcher_reads_installed_version_and_follows_future_updates(self):
        with tempfile.TemporaryDirectory(prefix='maa-version-test-') as temporary:
            root = Path(temporary)
            (root/'backend').mkdir()
            source = root/'backend/task_support.py'
            source.write_text('VERSION = "local-test.1"\nraise RuntimeError("Do not execute")\n',encoding='utf-8')
            result = self.run_powershell(r'''
$appRoot=$env:MAA_TEST_ROOT
$first=Get-InstalledBackendVersion
[IO.File]::WriteAllText((Join-Path $appRoot 'backend\task_support.py'),'VERSION = "local-test.2"')
$second=Get-InstalledBackendVersion
@{first=$first;second=$second} | ConvertTo-Json -Compress
''',dict(MAA_TEST_ROOT=str(root)))
            self.assertEqual(result,dict(first='local-test.1',second='local-test.2'))

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell regression')
    def test_restart_waits_for_old_listener_to_release_port(self):
        result = self.run_powershell(r'''
$script:probes=0
function Test-BackendPortOccupied { $script:probes++; return ($script:probes -le 2) }
Wait-BackendPortFree -TimeoutMilliseconds 2000
@{probes=$script:probes;released=$true} | ConvertTo-Json -Compress
''')
        self.assertEqual(result,dict(probes=3,released=True))

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell regression')
    def test_still_occupied_port_stops_restart_without_ending_unknown_process(self):
        result = self.run_powershell(r'''
function Test-BackendPortOccupied { return $true }
$failed=$false
try { Wait-BackendPortFree -TimeoutMilliseconds 0 } catch { $failed=($_.Exception.Message -like '*still occupied*') }
@{failed=$failed} | ConvertTo-Json -Compress
''')
        self.assertEqual(result,dict(failed=True))

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell regression')
    def test_stop_waits_for_owned_process_and_preserves_mismatched_record(self):
        with tempfile.TemporaryDirectory(prefix='maa-stop-test-') as temporary:
            root = Path(temporary)
            (root/'scripts').mkdir(); (root/'logs').mkdir()
            source = Path(__file__).resolve().parents[1]/'scripts/Stop-MaaXuexi.ps1'
            shutil.copyfile(source,root/'scripts/Stop-MaaXuexi.ps1')
            children = [subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) for _ in range(2)]
            try:
                result = self.run_powershell(r'''
$appRoot=$env:MAA_TEST_ROOT
$own=Get-Process -Id ([int]$env:MAA_TEST_OWNED)
$other=Get-Process -Id ([int]$env:MAA_TEST_OTHER)
$records=@(
    @{pid=$own.Id;root=$appRoot;executable=$own.Path;start_utc=$own.StartTime.ToUniversalTime().ToString('O')},
    @{pid=$other.Id;root=$appRoot;executable=$other.Path;start_utc=$other.StartTime.ToUniversalTime().AddSeconds(-1).ToString('O')}
)
$records | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $appRoot 'logs\server-process.json') -Encoding UTF8
# Prevent this isolated process test from issuing a request to the real backend.
function Invoke-RestMethod { return @{status='success'} }
& (Join-Path $appRoot 'scripts\Stop-MaaXuexi.ps1')
@{exit_code=$LASTEXITCODE;own_alive=[bool](Get-Process -Id $own.Id -ErrorAction SilentlyContinue);other_alive=[bool](Get-Process -Id $other.Id -ErrorAction SilentlyContinue)} | ConvertTo-Json -Compress
''',dict(MAA_TEST_ROOT=str(root),MAA_TEST_OWNED=str(children[0].pid),MAA_TEST_OTHER=str(children[1].pid)))
                self.assertEqual(result,dict(exit_code=0,own_alive=False,other_alive=True))
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=10)

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell regression')
    def test_native_stderr_is_retryable_instead_of_aborting_startup(self):
        script=Path(__file__).resolve().parents[1]/'scripts/Start-MaaXuexi.ps1'
        # Extract only the helper definition, without running the actual launcher.
        ps=r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:MAA_TEST_LAUNCHER,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Launcher PowerShell syntax error.'}
$function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-AdbProbe'},$true)
Invoke-Expression $function.Extent.Text
# Python receives ADB-shaped arguments, writes native stderr and returns nonzero.
# The helper must return that status under EAP=Stop without a NativeCommandError.
$result=Invoke-AdbProbe $env:MAA_TEST_PYTHON '127.0.0.1:16384' State
@{exit_code=$result.ExitCode;stdout=$result.StdOut;probe_returned=$true} | ConvertTo-Json -Compress
'''
        env=dict(os.environ,MAA_TEST_LAUNCHER=str(script),MAA_TEST_PYTHON=sys.executable)
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',ps],
                              capture_output=True,text=True,timeout=15,env=env,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout)
        self.assertTrue(data['probe_returned'])
        self.assertNotEqual(data['exit_code'],0)


if __name__=='__main__':
    unittest.main()
