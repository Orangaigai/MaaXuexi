"""Foreground query recovery, measured capture timings and lossless profile moves."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from test_optimization import worker
import test_session_launcher
from task_support import TaskIncomplete
from screen_reading import read_screen
from runtime_doctor import diagnose


FOREGROUND = "topResumedActivity=ActivityRecord{u0 cn.xuexi.android/.MainActivity}"
def response(stdout="", stderr="", code=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


class ForegroundRecoveryTests(unittest.TestCase):
    def value(self):
        value=worker();del value._app_in_foreground
        value._adb_command=('not-executed.exe','-s','127.0.0.1:16384')
        value.connected=True;value._sleep=Mock()
        return value

    def test_comment_navigation_continues_after_slow_query_and_clicks_only_once(self):
        value=self.value()
        with patch('utils.subprocess.run',side_effect=[subprocess.TimeoutExpired('query',8),response('device'),response(FOREGROUND)]) as run:
            value._comment_click(dict(box=[100,200,60,40]))
        self.assertTrue(value.connected)
        self.assertEqual(run.call_count,3)
        value.tasker.controller.post_click.assert_called_once_with(130,220)
        self.assertEqual(run.call_args_list[1].args[0][-1],'get-state')

    def test_failed_query_then_other_app_never_clicks(self):
        value=self.value()
        with patch('utils.subprocess.run',side_effect=[subprocess.CalledProcessError(1,'query'),response('device'),response('topResumedActivity=ActivityRecord{u0 com.other/.Main}')]):
            with self.assertRaisesRegex(TaskIncomplete,'不在前台'):
                value._comment_click(dict(box=[100,200,60,40]))
        self.assertTrue(value.connected);value.tasker.controller.post_click.assert_not_called()

    def test_repeated_query_failure_online_is_not_reported_as_disconnect(self):
        value=self.value()
        with patch('utils.subprocess.run',side_effect=[subprocess.TimeoutExpired('q',8),response('device')]*2) as run:
            with self.assertRaisesRegex(TaskIncomplete,'ADB仍在线.*两次'):
                value._app_in_foreground()
        self.assertTrue(value.connected);self.assertEqual(run.call_count,4)
        value.tasker.controller.post_click.assert_not_called()

    def test_actual_offline_clears_connection_without_another_query_or_click(self):
        value=self.value()
        with patch('utils.subprocess.run',side_effect=[subprocess.TimeoutExpired('q',8),response(stderr='error: device offline',code=1)]) as run:
            with self.assertRaisesRegex(TaskIncomplete,'断开或未授权'):
                value._app_in_foreground()
        self.assertFalse(value.connected);self.assertEqual(run.call_count,2)

    def test_ambiguous_probe_failure_retains_connection_and_original_error(self):
        for state in [response(stderr='server temporary error',code=1),subprocess.TimeoutExpired('probe',3)]:
            with self.subTest(state=state):
                value=self.value()
                with patch('utils.subprocess.run',side_effect=[subprocess.TimeoutExpired('q',8),state]):
                    with self.assertRaises(TaskIncomplete) as error:value._app_in_foreground()
                self.assertTrue(value.connected)
                self.assertIsInstance(error.exception.__cause__,subprocess.TimeoutExpired)

    def test_successful_foreground_query_does_not_add_a_probe(self):
        value=self.value()
        with patch('utils.subprocess.run',return_value=response(FOREGROUND)) as run:
            self.assertTrue(value._app_in_foreground())
        run.assert_called_once()


class CaptureTimingTests(unittest.TestCase):
    def test_identical_frame_still_measures_each_real_capture_without_new_ocr(self):
        value=worker();image=np.zeros((32,32,3),np.uint8)
        value._screen_ocr_cache=(image,[]);value.tasker.post_recognition=Mock()
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=image
        with patch('screen_reading.time.monotonic',side_effect=[10.,10.123,11.,11.456]):
            self.assertEqual(read_screen(value),[]);self.assertEqual(read_screen(value),[])
        self.assertEqual(value._screen_capture_metrics['samples'],2)
        self.assertAlmostEqual(value._screen_capture_metrics['seconds'],.456)
        value.tasker.post_recognition.assert_not_called()

    def test_invalid_capture_never_replaces_last_valid_sample(self):
        value=worker();value._screen_capture_metrics=dict(seconds=.1,samples=1)
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=None
        with self.assertRaises(TaskIncomplete):read_screen(value)
        self.assertEqual(value._screen_capture_metrics,dict(seconds=.1,samples=1))

    def test_diagnostics_reports_measured_time_without_rating_hardware_or_extra_capture(self):
        with tempfile.TemporaryDirectory() as folder,patch('runtime_doctor.subprocess.run') as run:
            result=diagnose(folder,screen_metrics=dict(seconds=.123,sampled_at='2026-10-22T14:00:00+08:00'))
        row=next(row for row in result['checks'] if row['name']=='截图耗时')
        self.assertEqual(row['state'],'ok');self.assertIn('123 ms',row['message'])
        self.assertIn('不含OCR',row['message']);run.assert_not_called()

    def test_missing_or_invalid_timing_is_explicitly_unsampled(self):
        for seconds in [None,float('nan'),-1,True,'0.1']:
            with tempfile.TemporaryDirectory() as folder:
                result=diagnose(folder,screen_metrics=dict(seconds=seconds))
            row=next(row for row in result['checks'] if row['name']=='截图耗时')
            self.assertEqual(row['state'],'warn');self.assertIn('暂无截图样本',row['message'])


class RuntimePublicationTests(unittest.TestCase):
    def test_browser_runtime_data_cannot_enter_the_public_manifest(self):
        validator=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/Check-Repository.py'))['public_path']
        self.assertFalse(validator('runtime/control-browser/Default/Cookies'))
        self.assertFalse(validator('runtime/control-browser/Local State'))
        self.assertTrue(validator('scripts/Control-Browser.ps1'))


@unittest.skipUnless(os.name=='nt','Windows native file-move checks')
class ProfileMigrationTests(unittest.TestCase):
    powershell=test_session_launcher.SessionLauncherTests.powershell
    def run_profile(self,scenario):
        with tempfile.TemporaryDirectory(prefix='maa profile ') as folder:
            script = r'''$ErrorActionPreference='Stop'
. (Join-Path $env:MAA_SESSION_ROOT 'scripts/Control-Browser.ps1')
$root=$env:MAA_SESSION_TEMP
$old=Join-Path $root 'config\control-browser';$new=Join-Path $root 'runtime\control-browser'
$null=New-Item -ItemType Directory -Path (Join-Path $old 'Default') -Force
[IO.File]::WriteAllText((Join-Path $old 'Default\Preferences'),'original preferences')
function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) return @() }
''' + scenario + r'''
$path=Get-ControlBrowserProfile $root
@{path=$path;old=(Test-Path -LiteralPath $old);new=(Test-Path -LiteralPath $new);nested=(Test-Path -LiteralPath (Join-Path $new 'control-browser'));
 oldData=$(if(Test-Path -LiteralPath (Join-Path $old 'Default\Preferences')){[IO.File]::ReadAllText((Join-Path $old 'Default\Preferences'))});
 newData=$(if(Test-Path -LiteralPath (Join-Path $new 'Default\Preferences')){[IO.File]::ReadAllText((Join-Path $new 'Default\Preferences'))})}|ConvertTo-Json -Compress
'''
            return self.powershell(script,dict(MAA_SESSION_TEMP=folder))

    def test_closed_profile_moves_all_data_and_repeated_resolution_is_stable(self):
        data=self.run_profile('$first=Get-ControlBrowserProfile $root')
        self.assertFalse(data['old']);self.assertTrue(data['new'])
        self.assertEqual(data['newData'],'original preferences')
        self.assertTrue(data['path'].endswith(r'runtime\control-browser'))

    def test_running_legacy_profile_is_kept_without_a_move(self):
        data=self.run_profile(r'''function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) return @{CommandLine=('msedge.exe --user-data-dir="'+$old+'"')} }''')
        self.assertTrue(data['old']);self.assertFalse(data['new'])
        self.assertTrue(data['path'].endswith(r'config\control-browser'))

    def test_unknown_process_state_defers_migration_without_data_loss(self):
        data=self.run_profile("function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) throw 'query unavailable' }")
        self.assertTrue(data['old']);self.assertFalse(data['new']);self.assertEqual(data['oldData'],'original preferences')

    def test_two_profiles_are_never_merged_or_overwritten(self):
        data=self.run_profile(r'''$null=New-Item -ItemType Directory -Path (Join-Path $new 'Default') -Force
[IO.File]::WriteAllText((Join-Path $new 'Default\Preferences'),'second profile')''')
        self.assertTrue(data['old']);self.assertTrue(data['new'])
        self.assertEqual(data['oldData'],'original preferences');self.assertEqual(data['newData'],'second profile')

    def test_move_failure_keeps_the_old_profile_usable(self):
        data=self.run_profile("$hold=[IO.File]::Open((Join-Path $old 'Default\\Preferences'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)")
        self.assertTrue(data['old']);self.assertFalse(data['new']);self.assertEqual(data['oldData'],'original preferences')

    def test_another_migration_holding_file_lock_defers_without_a_move(self):
        data=self.run_profile(r'''$null=New-Item -ItemType Directory -Path (Join-Path $root 'logs') -Force
$hold=[IO.File]::Open((Join-Path $root 'logs\control-profile.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)''')
        self.assertTrue(data['old']);self.assertFalse(data['new'])

    def test_destination_appearing_during_query_never_nests_or_merges_profiles(self):
        data=self.run_profile(r'''function Get-CimInstance {
 param($ClassName,$Filter,$ErrorAction)
 $null=New-Item -ItemType Directory -Path (Join-Path $new 'Default') -Force
 [IO.File]::WriteAllText((Join-Path $new 'Default\Preferences'),'second profile')
 return @()
}''')
        self.assertTrue(data['old']);self.assertTrue(data['new']);self.assertFalse(data['nested'])
        self.assertEqual(data['oldData'],'original preferences');self.assertEqual(data['newData'],'second profile')

    def test_unreadable_browser_command_line_defers_migration(self):
        data=self.run_profile("function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) return @{CommandLine=$null} }")
        self.assertTrue(data['old']);self.assertFalse(data['new'])

    def test_redirected_runtime_is_rejected_without_moving_data_or_claiming_browser(self):
        with tempfile.TemporaryDirectory(prefix='maa redirect ') as folder:
            data=self.powershell(r'''
$ErrorActionPreference='Stop'
. (Join-Path $env:MAA_SESSION_ROOT 'scripts/Control-Browser.ps1')
$root=$env:MAA_SESSION_TEMP
$null=New-Item -ItemType Directory -Path (Join-Path $root 'config/control-browser/Default') -Force
$null=New-Item -ItemType Directory -Path (Join-Path $root 'outside') -Force
$null=New-Item -ItemType Junction -Path (Join-Path $root 'runtime') -Target (Join-Path $root 'outside')
[IO.File]::WriteAllText((Join-Path $root 'config/control-browser/Default/Preferences'),'original preferences')
$rejected=$false
try{$null=Get-ControlBrowserProfile $root}catch{$rejected=$true}
$null=New-Item -ItemType Directory -Path (Join-Path $root 'logs') -Force
@{root=$root;profile=(Join-Path $root 'runtime/control-browser');pid=1234}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $root 'logs/control-browser.json') -Encoding UTF8
@{rejected=$rejected;owned=[bool](Get-OwnedControlBrowser $root);oldData=[IO.File]::ReadAllText((Join-Path $root 'config/control-browser/Default/Preferences'))}|ConvertTo-Json -Compress
''',dict(MAA_SESSION_TEMP=folder))
        self.assertEqual(data,dict(rejected=True,owned=False,oldData='original preferences'))

if __name__=='__main__':unittest.main()
