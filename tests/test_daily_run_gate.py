"""Manual starts suppress today's automation, without blocking explicit retries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import schedule_support as schedules
import webui


class DailyRunGateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'schedule.json'
        self.now = datetime.fromisoformat('2026-10-22T07:32:00+08:00')
        for item in (patch.object(schedules, 'CONFIG_PATH', self.path),
                     patch.object(schedules, 'now_china', side_effect=lambda:self.now)):
            item.start(); self.addCleanup(item.stop)

    def test_manual_start_skips_automatic_but_not_manual_continuation(self):
        self.assertTrue(schedules.claim_daily_run())
        self.assertIsNone(schedules.claim_daily_run(scheduled=True))
        self.assertIsNotNone(schedules.claim_daily_run())
        self.assertTrue(schedules.daily_started_today())

    def test_auto_start_is_also_once_and_state_survives_another_read(self):
        self.assertTrue(schedules.claim_daily_run(scheduled=True))
        self.assertIsNone(schedules.claim_daily_run(scheduled=True))
        self.assertEqual(json.loads(schedules._daily_state_path().read_text())['day'], '2026-10-22')

    def test_next_beijing_day_resets_even_with_a_different_utc_offset(self):
        schedules.claim_daily_run()
        self.now = datetime.fromisoformat('2026-10-22T16:01:00+00:00')
        self.assertFalse(schedules.daily_started_today())
        self.assertTrue(schedules.claim_daily_run(scheduled=True))

    def test_simultaneous_automatic_requests_claim_exactly_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _:schedules.claim_daily_run(scheduled=True),range(4)))
        self.assertEqual(sum(value is not None for value in results), 1)

    def test_save_after_a_manual_start_cannot_trigger_again_inside_window(self):
        schedules.claim_daily_run()
        self.now = self.now.replace(hour=8, minute=15)
        self.path.write_text(json.dumps(dict(options=schedules.ScheduleOptions().model_dump())))
        def apply(options, next_at):
            return dict(today_started=True,next_run_at=next_at.isoformat())
        with patch.object(schedules, '_apply_plan', side_effect=apply):
            result = schedules.save_schedule(schedules.ScheduleOptions())
        self.assertFalse(result['trigger_immediately'])
        self.assertTrue(result['next_run_at'].startswith('2026-10-23T08:'))

    def test_saving_alone_does_not_claim_a_run(self):
        with patch.object(schedules, '_apply_plan', return_value={}):
            schedules.save_schedule(schedules.ScheduleOptions())
        self.assertFalse(schedules.daily_started_today())

    def start_with_mock_worker(self, tasks, *, fail=False, scheduled=False):
        with (patch.object(webui,'ensure_worker',return_value=Mock()),
              patch.object(webui,'task_running',return_value=False),
              patch.object(webui.app_state,'history_import',Mock(running=False)),
              patch.object(webui.app_state,'completion',Mock()),
              patch.object(webui.app_state,'child_process',None),
              patch.object(webui.threading,'Thread') as thread):
            if fail:
                thread.return_value.start.side_effect=RuntimeError('worker unavailable')
            return webui.start_selected(webui.TaskModel(tasklist=tasks), scheduled=scheduled)

    def test_rejected_empty_selection_does_not_cancel_todays_trigger(self):
        self.assertEqual(self.start_with_mock_worker([])['status'],'failed')
        self.assertFalse(schedules.daily_started_today())

    def test_thread_start_failure_rolls_back_only_its_new_claim(self):
        with self.assertRaisesRegex(RuntimeError,'worker unavailable'):
            self.start_with_mock_worker(['登录'],fail=True)
        self.assertFalse(schedules.daily_started_today())
        schedules.claim_daily_run()
        with self.assertRaises(RuntimeError):
            self.start_with_mock_worker(['登录'],fail=True)
        self.assertTrue(schedules.daily_started_today())

    def test_accepted_request_marks_today_and_late_scheduled_call_does_not_create_worker(self):
        self.assertEqual(self.start_with_mock_worker(['登录'])['status'],'success')
        with patch.object(webui,'ensure_worker') as worker:
            result=webui.start_scheduled(webui.TaskModel(tasklist=['登录'],unattended=True))
        self.assertEqual(result['status'],'skipped')
        worker.assert_not_called()

    def test_fresh_quiz_acceptance_does_not_consume_the_real_daily_run(self):
        with (patch.object(webui,'ensure_worker',return_value=Mock()),
              patch.object(webui,'task_running',return_value=False),
              patch.object(webui,'key_configured',return_value=True),
              patch.object(webui.app_state,'completion',Mock()),
              patch.object(webui.threading,'Thread')):
            webui.start_selected(webui.TaskModel(tasklist=['每日答题']),daily_acceptance=True)
        self.assertFalse(schedules.daily_started_today())

    def test_launcher_skips_today_but_keeps_explicit_full_flow_acceptance_available(self):
        import runpy
        schedules.claim_daily_run()
        script=Path(__file__).resolve().parents[1]/'scripts/Plan-NextRun.py'
        plan=dict(enabled=True,next_run_at='2026-10-23T08:00:00+08:00')
        with patch.object(schedules,'rearm_for_run',return_value=plan):
            with patch.object(sys,'argv',[str(script)]),self.assertRaises(SystemExit) as exit_result:
                runpy.run_path(str(script),run_name='__main__')
            self.assertEqual(exit_result.exception.code,5)
            with patch.object(sys,'argv',[str(script),'--acceptance']):
                runpy.run_path(str(script),run_name='__main__')


if __name__ == '__main__':
    unittest.main()
