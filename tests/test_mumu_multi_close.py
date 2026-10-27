"""Shared MuMu main can exit only after all instance states are confirmed off."""
import json
import os
from pathlib import Path
import subprocess
import unittest


@unittest.skipUnless(os.name=='nt','Windows MuMu cleanup')
class MuMuMultiCloseTests(unittest.TestCase):
    def test_stopped_created_instances_active_instances_unknown_state_and_race(self):
        root=Path(__file__).resolve().parents[1]
        code=r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:MAA_FINISH_SCRIPT,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Finish script syntax error'}
foreach($name in @('Test-AllMuMuInstancesStopped','Close-IdleMuMuMain')){
 $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
 Invoke-Expression $fn.Extent.Text
}
$off='{"0":{"index":"0","error_code":0,"is_process_started":false,"is_android_started":false},"1":{"index":"1","error_code":0,"is_process_started":false,"is_android_started":false}}'
$active='{"0":{"index":"0","error_code":0,"is_process_started":false,"is_android_started":false},"1":{"index":"1","error_code":0,"is_process_started":true,"is_android_started":false}}'
function Invoke-MuMuCommand { param($CliPath,$Arguments)
 $global:calls+=@($Arguments)
 if($Arguments -eq 'info -v all'){$i=$global:reads;$global:reads++;return $global:answers[[Math]::Min($i,$global:answers.Count-1)]}
 if($Arguments -ne 'main close'){throw 'Unexpected mutation'}
}
function Probe($answers){
 $global:answers=$answers;$global:reads=0;$global:calls=@()
 $result=Close-IdleMuMuMain 'owned-mumu-cli.exe'
 return @{result=$result;closed=@($global:calls|Where-Object {$_ -eq 'main close'}).Count}
}
$stopped=Probe @($off,$off)
$running=Probe @($active)
$race=Probe @($off,$active)
$unknown=Probe @('{"0":{"index":"0","error_code":0,"is_android_started":false}}')
$bad=Probe @('not json')
if(!$stopped.result -or $stopped.closed -ne 1){throw 'Inactive created instances incorrectly block exit'}
foreach($r in @($running,$race,$unknown,$bad)){if($r.result -or $r.closed){throw 'Shared host was closed without confirming every instance off'}}
@{stopped=$stopped;running=$running;race=$race;unknown=$unknown;bad=$bad}|ConvertTo-Json -Depth 4 -Compress
'''
        r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],capture_output=True,
            text=True,timeout=15,env=dict(os.environ,MAA_FINISH_SCRIPT=str(root/'scripts/Finish-Session.ps1')),
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(r.returncode,0,r.stderr+r.stdout)
        self.assertEqual(json.loads(r.stdout.splitlines()[-1])['running']['closed'],0)


if __name__=='__main__':unittest.main()
