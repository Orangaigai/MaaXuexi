"""Windows session ownership and completed-run monitoring, with no real shutdown."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name=='nt','Windows PowerShell regression')
class SessionLauncherTests(unittest.TestCase):
    def powershell(self,script,env=None):
        values=dict(os.environ,MAA_SESSION_ROOT=str(ROOT));values.update(env or {})
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-Command',script],
          text=True,capture_output=True,timeout=25,env=values,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def helper(self,script,function):
        return f'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:MAA_SESSION_ROOT '{script}'),[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'PowerShell syntax error'}}
$node=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq '{function}'}},$true)
Invoke-Expression $node.Extent.Text
'''

    def test_console_open_preserves_device_until_explicit_idle_reconnect(self):
        data=self.powershell(r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:MAA_SESSION_ROOT 'scripts/Start-MaaXuexi.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'PowerShell syntax error'}
# Execute the actual entry branches with side effects replaced, including its early return.
$entry=@($ast.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})[-1].Extent.Text.Replace('exit 0','return')
$appRoot=$env:MAA_SESSION_ROOT
function Get-InstalledBackendVersion { 'test' }
function Get-RecordedProcesses { if($script:reuse){@{Id=1234}} }
function Read-RecordedBackendState { $script:health }
function Test-BackendPortOccupied { $false }
function New-Item { param($ItemType,$Path,[switch]$Force) }
function Test-Path { param($LiteralPath) $true }
function Start-Process { param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,$RedirectStandardOutput,$RedirectStandardError,[switch]$PassThru) @{HasExited=$false;Id=1234} }
function Save-ProcessRecords { param($Processes) }
function Invoke-RestMethod { param($Uri,$TimeoutSec) $script:health }
function Initialize-LocalSettings {}
function Open-ControlPage { $script:opens++ }
function Connect-ConfiguredEmulator { $script:connections++ }
$rows=@()
foreach($script:reuse in @($false,$true)){
    foreach($Reconnect in @($false,$true)){
        foreach($busy in @('idle','task','import')){
            $script:health=@{status='running';backend='deepseek-local';local_version='test';
                task_running=($busy -eq 'task');history_import=@{active=($busy -eq 'import')}}
            $script:connections=0;$script:opens=0
            & ([scriptblock]::Create($entry))
            $rows+=@{reuse=$script:reuse;reconnect=$Reconnect;busy=$busy;connections=$script:connections;opens=$script:opens}
        }
    }
}
ConvertTo-Json -InputObject $rows -Compress
''')
        for row in data:
            with self.subTest(**row):
                self.assertEqual(row['opens'],1)
                self.assertEqual(row['connections'],int(row['reconnect'] and row['busy']=='idle'))

    def test_disk_fallback_accepts_only_matching_current_success(self):
        with tempfile.TemporaryDirectory() as folder:
            data=self.powershell(self.helper('scripts/Run-Daily.ps1','Read-CompletedRunFromDisk')+r'''
$path=Join-Path $env:MAA_SESSION_TEMP 'last.json'
$submitted=[DateTimeOffset]::UtcNow.AddSeconds(-10)
$saved=@{run_id='run_current';started_at=[DateTimeOffset]::UtcNow.ToString('O');finished_at=[DateTimeOffset]::UtcNow.ToString('O');status='completed';tasks=@{login=@{state='skipped_full'}}}
function Save-Report { $saved|ConvertTo-Json -Depth 5|Set-Content -LiteralPath $path -Encoding UTF8 }
Save-Report;$valid=[bool](Read-CompletedRunFromDisk $path $submitted 'run_current' @('login'))
$wrong=[bool](Read-CompletedRunFromDisk $path $submitted 'other_run' @('login'))
$tasks=[bool](Read-CompletedRunFromDisk $path $submitted 'run_current' @('video'))
$saved.status='needs_human';Save-Report;$pending=[bool](Read-CompletedRunFromDisk $path $submitted 'run_current' @('login'))
$saved.status='completed';$saved.started_at=$submitted.AddDays(-1).ToString('O');Save-Report
$old=[bool](Read-CompletedRunFromDisk $path $submitted 'run_current' @('login'))
@{valid=$valid;wrong=$wrong;tasks=$tasks;pending=$pending;old=$old}|ConvertTo-Json -Compress
''',dict(MAA_SESSION_TEMP=folder))
        self.assertEqual(data,dict(valid=True,wrong=False,tasks=False,pending=False,old=False))

    def test_immediate_reopen_waits_for_owned_closing_backend_to_exit(self):
        data=self.powershell(self.helper('scripts/Start-MaaXuexi.ps1','Read-RecordedBackendState')+r'''
$script:reads=0
function Get-RecordedProcesses { $script:reads++;if($script:reads -lt 3){return @{Id=1234}} }
function Invoke-RestMethod { param($Uri,$TimeoutSec) return @{completion_action=@{state='closed'}} }
function Start-Sleep { param($Milliseconds) }
$result=Read-RecordedBackendState
@{exited=($null -eq $result);reads=$script:reads}|ConvertTo-Json -Compress
''')
        self.assertEqual(data,dict(exited=True,reads=3))

    def test_browser_record_must_match_profile_process_executable_and_start_time(self):
        with tempfile.TemporaryDirectory() as folder:
            data=self.powershell(r'''
$ErrorActionPreference='Stop'
. (Join-Path $env:MAA_SESSION_ROOT 'scripts/Control-Browser.ps1')
$root=$env:MAA_SESSION_TEMP;$null=New-Item -ItemType Directory -Path (Join-Path $root 'logs') -Force
$profile=Join-Path $root 'config\control-browser';$started=[DateTime]::UtcNow
$script:fake=@{Id=1234;ProcessName='msedge';Path='C:\browser\msedge.exe';StartTime=$started}
$script:command='msedge.exe --user-data-dir="'+$profile+'" --app=http://127.0.0.1:8000/'
function Get-Process { param($Id,$ErrorAction) return $script:fake }
function Get-CimInstance { param($ClassName,$Filter) return @{CommandLine=$script:command} }
$record=@{pid=1234;root=$root;profile=$profile;executable=$script:fake.Path;start_utc=$started.ToString('O')}
function Save-Owner { $record|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $root 'logs\control-browser.json') -Encoding UTF8 }
Save-Owner;$valid=[bool](Get-OwnedControlBrowser $root)
function ConvertFrom-Json {
 param([Parameter(ValueFromPipeline=$true)]$InputObject)
 process {
  $value=Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $InputObject
  $value.start_utc=[DateTime]::Parse($value.start_utc,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind)
  return $value
 }
}
$dateObject=[bool](Get-OwnedControlBrowser $root)
Remove-Item Function:\ConvertFrom-Json
$record.profile=Join-Path $root 'runtime\control-browser';$script:command='msedge.exe --user-data-dir="'+$record.profile+'"';Save-Owner
$newValid=[bool](Get-OwnedControlBrowser $root)
$record.profile=$profile;Save-Owner
$script:command='msedge.exe --user-data-dir="C:\other-profile"';$profileMismatch=[bool](Get-OwnedControlBrowser $root)
$script:command='msedge.exe --user-data-dir="'+$profile+'"';$record.start_utc=$started.AddSeconds(-1).ToString('O');Save-Owner
$recycled=[bool](Get-OwnedControlBrowser $root)
$record.start_utc=$started.ToString('O');$record.root='C:\other-install';Save-Owner;$otherRoot=[bool](Get-OwnedControlBrowser $root)
@{valid=$valid;dateObject=$dateObject;newValid=$newValid;profileMismatch=$profileMismatch;recycled=$recycled;otherRoot=$otherRoot}|ConvertTo-Json -Compress
''',dict(MAA_SESSION_TEMP=folder))
        self.assertEqual(data,dict(valid=True,dateObject=True,newValid=True,profileMismatch=False,recycled=False,otherRoot=False))

    def test_new_powershell_scripts_parse_without_running_emulator_commands(self):
        data=self.powershell(r'''
$failed=@()
foreach($name in @('Control-Browser.ps1','Finish-Session.ps1','Start-MaaXuexi.ps1','Run-Daily.ps1')){
    $tokens=$null;$errors=$null
    $null=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:MAA_SESSION_ROOT ('scripts\'+$name)),[ref]$tokens,[ref]$errors)
    if($errors.Count){$failed+=$name}
}
@{failed=@($failed)}|ConvertTo-Json -Compress
''')
        self.assertEqual(data['failed'],[])

    def test_browser_arguments_keep_the_quoted_profile_as_one_argument(self):
        with tempfile.TemporaryDirectory(prefix='maa browser args ') as folder:
            data=self.powershell(r'''
$ErrorActionPreference='Stop'
. (Join-Path $env:MAA_SESSION_ROOT 'scripts/Control-Browser.ps1')
function Get-OwnedControlBrowser { param($Root) return $null }
function Get-ControlBrowserProfile { param($Root) return (Join-Path $Root 'runtime\control-browser') }
function Test-Path { param($LiteralPath) return $true }
function Start-Process { param($FilePath,$ArgumentList,$WindowStyle) $global:captured=@($ArgumentList);$global:style=$WindowStyle }
function Get-CimInstance { param($ClassName,$Filter) return @{ProcessId=1234;CommandLine=('msedge.exe '+($global:captured -join ' '))} }
function Get-Process { param($Id,$ErrorAction) return @{Id=1234;MainWindowHandle=1;Path='C:\browser\msedge.exe';StartTime=[DateTime]::UtcNow} }
Open-ControlBrowser $env:MAA_SESSION_TEMP
@{arguments=@($global:captured);style=$global:style;profile=(Join-Path $env:MAA_SESSION_TEMP 'runtime\control-browser')}|ConvertTo-Json -Compress
''',dict(MAA_SESSION_TEMP=folder))
        self.assertEqual(len(data['arguments']),7)
        self.assertEqual(data['arguments'][1],'--user-data-dir="'+data['profile']+'"')
        self.assertEqual(data['style'],'Normal')

    def test_shutdown_accepts_both_mumu_cli_names_and_only_one_numeric_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('MuMuManager.exe','mumu-cli.exe','unrelated.exe'):(Path(folder)/name).write_bytes(b'not executed')
            data=self.powershell(self.helper('scripts/Finish-Session.ps1','Test-ConfiguredMuMuTarget')+r'''
$old=Test-ConfiguredMuMuTarget (Join-Path $env:MAA_SESSION_TEMP 'MuMuManager.exe') '0'
$new=Test-ConfiguredMuMuTarget (Join-Path $env:MAA_SESSION_TEMP 'mumu-cli.exe') '1'
$all=Test-ConfiguredMuMuTarget (Join-Path $env:MAA_SESSION_TEMP 'mumu-cli.exe') 'all'
$other=Test-ConfiguredMuMuTarget (Join-Path $env:MAA_SESSION_TEMP 'unrelated.exe') '0'
@{old=$old;new=$new;all=$all;other=$other}|ConvertTo-Json -Compress
''',dict(MAA_SESSION_TEMP=folder))
        self.assertEqual(data,dict(old=True,new=True,all=False,other=False))


if __name__=='__main__':unittest.main()
