"""Owner action resumes the same task; no browser execution or CAPTCHA input."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import task_support
import utils
from test_optimization import worker

CAPTCHA = [dict(text='访问验证', box=[200, 100, 180, 40]),
           dict(text='请拖动滑块完成拼图', box=[150, 600, 400, 40])]
PROFILE = [dict(text='学习积分', box=[100, 460, 140, 40]),
           dict(text='我要答题', box=[300, 460, 140, 40])]


class OwnerContinuationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        report = patch.object(task_support, 'REPORT_PATH', Path(directory.name) / 'run.json')
        report.start(); self.addCleanup(report.stop)
        self.clock = 0.0
        clock = patch('utils.time.monotonic', side_effect=lambda: self.clock)
        clock.start(); self.addCleanup(clock.stop)
        sleep = patch('utils.time.sleep', side_effect=self.advance)
        sleep.start(); self.addCleanup(sleep.stop)
        notify = patch('utils.plyer.notification.notify')
        self.notify = notify.start(); self.addCleanup(notify.stop)

    def advance(self, seconds):
        self.clock += seconds

    def guarded(self, frames):
        value = worker()
        del value._guard_verification
        value._screen_records = Mock(side_effect=frames)
        return value

    def test_generic_verification_resumes_from_stable_profile_without_any_input(self):
        for unattended in (False, True):
            with self.subTest(unattended=unattended):
                value = self.guarded([CAPTCHA, CAPTCHA, PROFILE, PROFILE, PROFILE])
                value.unattended = unattended
                value._guard_verification()
                self.assertFalse(value.pause_flag)
                self.assertGreater(value._paused_seconds, 0)
                value.tasker.controller.post_click.assert_not_called()
                value.tasker.controller.post_swipe.assert_not_called()
                value.tasker.post_task.assert_not_called()

    def test_empty_loading_and_verification_frames_do_not_resume(self):
        value = self.guarded([[], [dict(text='加载中...', box=[280, 500, 140, 40])],
                              CAPTCHA, PROFILE, CAPTCHA, PROFILE, PROFILE])
        for expected in (False, False, False, False, False, False, True):
            self.assertEqual(value._owner_resume_ready(), expected)

    def test_normal_page_outside_app_does_not_resume(self):
        value = self.guarded([PROFILE, PROFILE])
        value._app_in_foreground.return_value = False
        self.assertFalse(value._owner_resume_ready())
        self.assertFalse(value._owner_resume_ready())

    def test_scheduled_owner_wait_does_not_exhaust_active_deadline(self):
        value = worker(); value.unattended = True; value._deadline = .5
        ready = Mock(side_effect=lambda: self.clock >= 2)
        value.pause('真实访问验证', resume_when=ready)
        self.assertGreater(self.clock, .5)
        self.assertGreater(value._deadline, self.clock)
        self.assertFalse(value.pause_flag)

    def test_cancel_during_owner_wait_releases_worker_without_probe_or_input(self):
        value = worker(); value.unattended = True
        ready = Mock(return_value=False)
        with patch('utils.time.sleep', side_effect=lambda _: setattr(value, 'stop_flag', True)):
            with self.assertRaises(task_support.TaskCancelled):
                value.pause('真实访问验证', resume_when=ready)
        self.assertFalse(value.pause_flag)
        ready.assert_not_called()
        value.tasker.controller.post_click.assert_not_called()

    def test_notification_failure_does_not_abort_same_task_continuation(self):
        value = worker(); value.unattended = True
        self.notify.side_effect = RuntimeError('notification fixture failure')
        value.pause('真实访问验证', resume_when=lambda: True)
        self.assertFalse(value.pause_flag)

    def test_one_scheduled_run_continues_to_daily_after_owner_finishes_fun_verification(self):
        value = worker()
        value._finish_existing_quiz = Mock()
        value._summarize_run = Mock()
        before = {'趣味答题': dict(value=0, maximum=10), '每日答题': dict(value=0, maximum=5)}
        value.read_learning_score = Mock(side_effect=[before,
            {'趣味答题': dict(value=10, maximum=10)}, {'每日答题': dict(value=5, maximum=5)}])
        run_ids = []
        def fun():
            run_ids.append(value.run_report.snapshot()['run_id'])
            value.pause('趣味答题真实访问验证', resume_when=lambda: self.clock >= 2)
        def daily():
            run_ids.append(value.run_report.snapshot()['run_id'])
        value.funny_answer = Mock(side_effect=fun)
        value.daily_answer = Mock(side_effect=daily)
        value.task(['趣味答题', '每日答题'], unattended=True)
        self.assertEqual(value.run_report.snapshot()['status'], 'completed')
        self.assertEqual(len(set(run_ids)), 1)
        value.prepare_device.assert_called_once()
        value.funny_answer.assert_called_once()
        value.daily_answer.assert_called_once()
        self.assertEqual(value.read_learning_score.call_count, 3)


if __name__ == '__main__':
    unittest.main()
