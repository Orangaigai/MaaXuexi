import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_optimization import worker
from schedule_support import read_schedule,schedule_display


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        config=patch('schedule_support.CONFIG_PATH',Path(temporary.name)/'schedule.json')
        config.start();self.addCleanup(config.stop)

    def data(self):
        return dict(available=True,enabled=True,start_boundary='2026-10-19T07:40:00+08:00',
            random_delay_seconds=4500,start_when_available=True,task_name='MaaXuexi-Daily-0900')

    def test_random_window_is_derived_from_real_trigger_and_retains_catchup(self):
        result=schedule_display(self.data())
        self.assertEqual((result['window_start'],result['window_end']),('07:40','08:55'))
        self.assertEqual(result['mode'],'random_window')
        self.assertTrue(result['start_when_available'])

    def test_fixed_or_utc_trigger_is_displayed_correctly(self):
        result=schedule_display(dict(self.data(),start_boundary='2026-10-18T23:40:00+00:00',random_delay_seconds=0))
        self.assertEqual(result['time'],'07:40')
        self.assertEqual(result['mode'],'fixed')

    def test_unavailable_windows_query_is_not_reported_as_disabled_or_old_nine(self):
        with patch('schedule_support.subprocess.run',side_effect=subprocess.TimeoutExpired('powershell',15)):
            result=read_schedule()
        self.assertFalse(result['available']);self.assertIsNone(result['enabled'])
        self.assertNotIn('time',result)

    def test_status_query_never_passes_apply(self):
        with patch('schedule_support.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps(self.data()))) as run:
            self.assertEqual(read_schedule()['window_end'],'08:55')
        self.assertNotIn('-Apply',run.call_args.args[0])

    @unittest.skipUnless(os.name=='nt','Windows trigger construction')
    def test_daily_trigger_uses_the_exact_chosen_time_without_another_random_delay(self):
        script=Path(__file__).resolve().parents[1]/'scripts/Schedule-MaaXuexi.ps1'
        code=r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:MAA_SCHEDULE_SCRIPT,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Schedule syntax error'}
$fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'New-MaaDailyTrigger'},$true)
Invoke-Expression $fn.Extent.Text
function New-ScheduledTaskTrigger { param([switch]$Daily,$At,$RandomDelay)
    [pscustomobject]@{StartBoundary='';Daily=[bool]$Daily;Delay=$RandomDelay} }
$early=New-MaaDailyTrigger '2026-10-19T08:12:34+08:00'
$late=New-MaaDailyTrigger '2026-10-19T09:00:00+08:00'
@{early=$early;late=$late}|ConvertTo-Json -Depth 4 -Compress
'''
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],
            capture_output=True,text=True,timeout=15,env=dict(os.environ,MAA_SCHEDULE_SCRIPT=str(script)),
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stderr)
        values=json.loads(result.stdout)
        self.assertEqual(values['early']['StartBoundary'],'2026-10-19T08:12:34+08:00')
        self.assertEqual(values['late']['StartBoundary'],'2026-10-19T09:00:00+08:00')
        self.assertTrue(values['early']['Daily']);self.assertIsNone(values['early']['Delay'])
