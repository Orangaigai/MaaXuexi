"""Clock boundaries, persistence and API effects that can start real tasks."""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from pydantic import ValidationError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import schedule_support as schedules
from schedule_support import CHINA, ScheduleOptions, plan_next, schedule_display
import webui


def clock(value):
    return datetime.fromisoformat('2026-10-18T'+value+'+08:00')


class ClockPlanTests(unittest.TestCase):
    def test_defaults_match_the_requested_form(self):
        self.assertEqual(ScheduleOptions().model_dump(),dict(mode='random_window',time='09:00',window_start='08:00',window_end='09:00'))

    def test_invalid_modes_times_and_cross_midnight_ranges_are_rejected(self):
        for data in [dict(mode='other'),dict(time='9:00'),dict(time='24:00'),dict(time=True),
                     dict(window_start='09:00',window_end='08:00'),dict(window_end='08:00'),dict(extra='ignored')]:
            with self.subTest(data=data),self.assertRaises(ValidationError):
                ScheduleOptions(**data)

    def test_save_before_window_picks_today_and_never_runs_early(self):
        with patch('schedule_support.secrets.randbelow',return_value=30) as random:
            next_at,immediate=plan_next(ScheduleOptions(),clock('07:32:00'))
        self.assertEqual(next_at,clock('08:00:30'));self.assertFalse(immediate)
        random.assert_called_once_with(3600)

    def test_0732_saving_0700_0800_starts_now_and_next_plan_is_tomorrow(self):
        with patch('schedule_support.secrets.randbelow',return_value=3599):
            next_at,immediate=plan_next(ScheduleOptions(window_start='07:00',window_end='08:00'),clock('07:32:00'))
        self.assertTrue(immediate)
        self.assertEqual(next_at.isoformat(),'2026-10-19T07:59:59+08:00')

    def test_window_start_included_end_excluded(self):
        with patch('schedule_support.secrets.randbelow',return_value=0):
            self.assertTrue(plan_next(ScheduleOptions(),clock('08:00:00'))[1])
            self.assertFalse(plan_next(ScheduleOptions(),clock('09:00:00'))[1])
            self.assertFalse(plan_next(ScheduleOptions(),clock('09:00:01'))[1])

    def test_late_save_uses_tomorrow(self):
        with patch('schedule_support.secrets.randbelow',return_value=0):
            next_at,immediate=plan_next(ScheduleOptions(),clock('23:59:59'))
        self.assertEqual(next_at.isoformat(),'2026-10-19T08:00:00+08:00');self.assertFalse(immediate)

    def test_fixed_time_before_after_and_same_minute(self):
        options=ScheduleOptions(mode='fixed')
        self.assertEqual(plan_next(options,clock('08:59:59')),(clock('09:00:00'),False))
        self.assertEqual(plan_next(options,clock('09:00:30'))[0].day,19)
        self.assertTrue(plan_next(options,clock('09:00:30'))[1])
        self.assertFalse(plan_next(options,clock('09:01:00'))[1])

    def test_run_rearms_tomorrow_even_if_missed_run_catches_up_before_window(self):
        with patch('schedule_support.secrets.randbelow',return_value=10):
            next_at,immediate=plan_next(ScheduleOptions(),clock('06:00:00'),tomorrow=True)
        self.assertEqual(next_at.isoformat(),'2026-10-19T08:00:10+08:00');self.assertFalse(immediate)

    def test_all_random_picks_are_inside_the_requested_range(self):
        for offset in (0,1,1799,3599):
            with patch('schedule_support.secrets.randbelow',return_value=offset):
                next_at,_=plan_next(ScheduleOptions(),clock('07:00:00'))
            self.assertLessEqual(clock('08:00:00'),next_at);self.assertLess(next_at,clock('09:00:00'))

    def test_inputs_with_another_offset_are_evaluated_in_beijing_time(self):
        now=datetime.fromisoformat('2026-10-17T23:32:00+00:00')
        with patch('schedule_support.secrets.randbelow',return_value=0):
            next_at,immediate=plan_next(ScheduleOptions(),now)
        self.assertEqual(next_at,clock('08:00:00'));self.assertFalse(immediate)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'local-schedule.json'
        patcher=patch.object(schedules,'CONFIG_PATH',self.path);patcher.start();self.addCleanup(patcher.stop)

    def native(self, at='2026-10-18T08:20:00+08:00'):
        return dict(available=True,enabled=True,start_boundary=at,random_delay_seconds=0,
            next_base_run=at,start_when_available=True,state='Ready')

    def test_saved_random_plan_survives_reads_without_randomizing_again(self):
        with patch('schedule_support.now_china',return_value=clock('07:00:00')), \
             patch('schedule_support.secrets.randbelow',return_value=1200) as random, \
             patch('schedule_support._native',return_value=self.native()) as native:
            first=schedules.save_schedule(ScheduleOptions())
            stored=self.path.read_bytes()
            a=schedules.read_schedule();b=schedules.read_schedule()
        self.assertEqual(first['next_run_at'],a['next_run_at']);self.assertEqual(a,b)
        self.assertEqual(a['mode'],'random_window');self.assertEqual(stored,self.path.read_bytes())
        random.assert_called_once();self.assertIn('-Apply',native.call_args_list[0].args)
        self.assertEqual(native.call_args_list[1].args,())

    def test_native_failure_preserves_previous_configuration(self):
        old=json.dumps(dict(options=ScheduleOptions(mode='fixed').model_dump())).encode()
        self.path.write_bytes(old)
        with patch('schedule_support._native',side_effect=subprocess.CalledProcessError(1,'powershell')):
            with self.assertRaises(subprocess.CalledProcessError):
                schedules.save_schedule(ScheduleOptions())
        self.assertEqual(self.path.read_bytes(),old);self.assertFalse(self.path.with_suffix('.tmp').exists())

    def test_run_rearms_before_learning_and_can_change_the_next_random_time(self):
        self.path.write_text(json.dumps(dict(options=ScheduleOptions().model_dump())),'utf-8')
        at='2026-10-19T08:00:42+08:00'
        with patch('schedule_support.now_china',return_value=clock('08:20:00')), \
             patch('schedule_support.secrets.randbelow',return_value=42), \
             patch('schedule_support._native',return_value=self.native(at)):
            result=schedules.rearm_for_run()
        self.assertEqual(result['next_run_at'],at)
        self.assertEqual(json.loads(self.path.read_text())['planned_at'],at)

    def test_no_new_configuration_does_not_modify_the_existing_native_trigger(self):
        with patch('schedule_support._native') as native:
            self.assertIsNone(schedules.rearm_for_run())
        native.assert_not_called()

    def test_countdown_comes_from_actual_native_time_not_browser_clock_or_window_start(self):
        result=schedule_display(self.native(),ScheduleOptions(),clock('07:32:00'))
        self.assertEqual(result['seconds_until_next_run'],48*60)
        self.assertEqual(result['next_run_at'],clock('08:20:00').isoformat())
        self.assertEqual(result['window_start'],'08:00')

    def test_native_state_failure_does_not_report_a_fake_countdown(self):
        with patch('schedule_support._native',side_effect=OSError()):
            result=schedules.read_schedule()
        self.assertFalse(result['available']);self.assertNotIn('seconds_until_next_run',result)


class ScheduleApiTests(unittest.TestCase):
    def test_immediate_button_uses_saved_daily_tasks_and_does_not_edit_schedule(self):
        with patch.object(webui,'get_task_plan',return_value={'tasklist':['登录','每日答题']}), \
             patch.object(webui,'task_running',return_value=False), \
             patch.object(webui,'start_selected',return_value={'status':'success'}) as start, \
             patch.object(webui,'save_schedule') as save:
            with TestClient(webui.app) as client:
                self.assertEqual(client.post('/api/local/schedule/run-now',json={}).status_code,200)
        self.assertEqual(start.call_args.args[0].tasklist,['登录','每日答题'])
        self.assertFalse(start.call_args.args[0].unattended);save.assert_not_called()

    def test_in_window_save_dispatches_exactly_once_with_a_bounded_owner_wait(self):
        with patch.object(webui,'save_schedule',return_value={'trigger_immediately':True}), \
             patch.object(webui,'start_saved_plan',return_value={'status':'success'}) as start:
            with TestClient(webui.app) as client:
                response=client.post('/api/local/schedule',json=ScheduleOptions().model_dump())
        self.assertEqual(response.status_code,200)
        start.assert_called_once_with(unattended=True)

    def test_outside_window_save_never_starts_learning(self):
        with patch.object(webui,'save_schedule',return_value={'trigger_immediately':False}), \
             patch.object(webui,'start_saved_plan') as start:
            with TestClient(webui.app) as client:
                self.assertEqual(client.post('/api/local/schedule',json={'mode':'fixed'}).status_code,200)
        start.assert_not_called()

    def test_an_active_task_is_not_started_again_by_in_window_save(self):
        with patch.object(webui,'task_running',return_value=True), \
             patch.object(webui,'start_selected') as start, \
             patch.object(webui,'save_schedule',return_value={'trigger_immediately':True}):
            with TestClient(webui.app) as client:
                result=client.post('/api/local/schedule',json=ScheduleOptions().model_dump()).json()
        self.assertEqual(result['status'],'success');self.assertEqual(result['immediate_status'],'failed')
        start.assert_not_called()

    def test_invalid_requests_cannot_reprogram_windows_or_start_learning(self):
        with patch.object(webui,'save_schedule') as save,patch.object(webui,'start_selected') as start:
            with TestClient(webui.app) as client:
                for body in [dict(mode='unknown'),dict(window_end='07:00'),dict(time='25:00'),dict(time=True)]:
                    self.assertEqual(client.post('/api/local/schedule',json=body).status_code,422)
        save.assert_not_called();start.assert_not_called()

    def test_native_failure_is_visible_and_never_dispatches(self):
        with patch.object(webui,'save_schedule',side_effect=OSError()),patch.object(webui,'start_saved_plan') as start:
            with TestClient(webui.app) as client:
                self.assertEqual(client.post('/api/local/schedule',json={}).status_code,503)
        start.assert_not_called()
