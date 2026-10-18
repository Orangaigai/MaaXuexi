"""Native activation must match the selected mode, even for a queued launcher."""
from datetime import datetime
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import schedule_support as schedules
from schedule_support import ScheduleOptions,CHINA
import webui


class ExecutionModeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'schedule.json'
        self.path.write_text(json.dumps(dict(options=ScheduleOptions(mode='fixed').model_dump())),'utf-8')
        self.original=self.path.read_bytes()
        patcher=patch.object(schedules,'CONFIG_PATH',self.path);patcher.start();self.addCleanup(patcher.stop)

    def native(self,enabled=True):
        return dict(available=True,enabled=enabled,start_boundary='2026-10-19T09:00:00+08:00',
            random_delay_seconds=0,next_base_run='2026-10-19T01:00:00Z',start_when_available=True,
            state='Ready' if enabled else 'Disabled')

    def test_manual_disables_windows_and_retains_time_options_without_a_fake_next_run(self):
        with patch.object(schedules,'_native',side_effect=[self.native(),self.native(False)]) as native:
            result=schedules.set_execution_mode('manual')
        self.assertEqual([call.args for call in native.call_args_list],[(),('-Disable',)])
        self.assertEqual(result['execution_mode'],'manual');self.assertIsNone(result['next_run_at'])
        self.assertIsNone(result['seconds_until_next_run']);self.assertEqual(result['time'],'09:00')
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_already_manual_is_idempotent(self):
        with patch.object(schedules,'_native',return_value=self.native(False)) as native:
            self.assertFalse(schedules.set_execution_mode('manual')['enabled'])
        native.assert_called_once_with()

    def test_queued_daily_launch_does_not_reenable_a_manual_schedule(self):
        with patch.object(schedules,'_native',return_value=self.native(False)) as native:
            result=schedules.rearm_for_run()
        native.assert_called_once_with();self.assertFalse(result['enabled'])
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_disabled_native_without_configuration_still_stops_a_queued_launch(self):
        self.path.unlink()
        with patch.object(schedules,'_native',return_value=self.native(False)):
            self.assertEqual(schedules.rearm_for_run()['execution_mode'],'manual')

    def test_automatic_reuses_saved_times_but_never_immediately_dispatches(self):
        now=datetime(2026,10,18,16,0,tzinfo=CHINA)
        with patch.object(schedules,'_native',side_effect=[self.native(False),self.native()]) as native, \
             patch.object(schedules,'now_china',return_value=now):
            result=schedules.set_execution_mode('automatic')
        self.assertEqual(native.call_args_list[1].args,('-Apply','-StartAt','2026-10-19T09:00:00+08:00'))
        self.assertTrue(result['enabled']);self.assertNotIn('trigger_immediately',result)
        self.assertEqual(json.loads(self.path.read_text())['options']['mode'],'fixed')

    def test_already_automatic_does_not_redraw_or_rewrite(self):
        with patch.object(schedules,'_native',return_value=self.native()) as native,patch.object(schedules,'plan_next') as pick:
            self.assertTrue(schedules.set_execution_mode('automatic')['enabled'])
        native.assert_called_once_with();pick.assert_not_called();self.assertEqual(self.path.read_bytes(),self.original)

    def test_windows_failure_does_not_overwrite_saved_options(self):
        with patch.object(schedules,'_native',side_effect=[self.native(),OSError()]):
            with self.assertRaises(OSError):schedules.set_execution_mode('manual')
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_mode_endpoint_does_not_start_or_stop_the_running_learning_task(self):
        with patch.object(webui,'set_execution_mode',return_value=dict(available=True,enabled=False)) as change, \
             patch.object(webui,'start_selected') as start,patch.object(webui,'stop') as stop:
            with TestClient(webui.app) as client:result=client.post('/api/local/execution-mode',json={'mode':'manual'})
        self.assertEqual(result.status_code,200);change.assert_called_once_with('manual')
        start.assert_not_called();stop.assert_not_called()

    def test_bad_mode_and_extra_fields_never_mutate_windows(self):
        with patch.object(webui,'set_execution_mode') as change:
            with TestClient(webui.app) as client:
                for body in [{'mode':'other'},{'mode':True},{'mode':'manual','enabled':True},{}]:
                    self.assertEqual(client.post('/api/local/execution-mode',json=body).status_code,422)
        change.assert_not_called()

    def test_native_failure_is_visible_to_the_page(self):
        with patch.object(webui,'set_execution_mode',side_effect=OSError()):
            with TestClient(webui.app) as client:result=client.post('/api/local/execution-mode',json={'mode':'manual'})
        self.assertEqual(result.status_code,503)

    def test_replanner_signals_manual_mode_for_a_clean_launcher_exit(self):
        script=Path(__file__).resolve().parents[1]/'scripts/Plan-NextRun.py'
        with patch.object(schedules,'rearm_for_run',return_value=dict(enabled=False)), \
             self.assertRaises(SystemExit) as raised:
            runpy.run_path(str(script),run_name='__main__')
        self.assertEqual(raised.exception.code,4)
