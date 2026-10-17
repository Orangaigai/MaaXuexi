"""Unattended runs must release the worker without interacting with a challenge."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import task_support
import utils
import webui
from test_optimization import worker, job


class UnattendedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        report_patch = patch.object(task_support, 'REPORT_PATH', Path(temporary.name) / 'run.json')
        report_patch.start()
        self.addCleanup(report_patch.stop)
        wait = patch('utils.OWNER_WAIT_SECONDS', 0)
        wait.start(); self.addCleanup(wait.stop)

    def guarded_worker(self):
        value = worker()
        del value._guard_verification
        value.unattended = True
        value._captcha_present = Mock(return_value=True)
        return value

    def test_api_normalizes_order_even_if_daily_is_requested_first(self):
        selected = ['每日答题', '趣味答题', '发表观点', '订阅', '本地频道', '视听学习', '选读文章', '登录', '每日答题']
        self.assertEqual(webui.TaskModel(tasklist=selected).tasklist,
                         ['登录','选读文章','视听学习','本地频道','趣味答题','订阅','发表观点','每日答题'])

    def test_expired_unattended_wait_raises_without_android_input(self):
        value = self.guarded_worker()
        with patch('utils.plyer.notification.notify'), patch('utils.time.sleep') as sleep:
            with self.assertRaises(task_support.HumanRequired):
                value._guard_verification()
        self.assertFalse(value.pause_flag)
        sleep.assert_called_once_with(.1)
        value.tasker.controller.post_click.assert_not_called()
        value.tasker.controller.post_swipe.assert_not_called()

    def test_completed_work_is_retained_and_remaining_work_is_deferred(self):
        value = worker()
        value._finish_existing_quiz = Mock()
        value._reset_home = Mock()
        scores = {name: dict(value=0, maximum=cap) for name, cap in
                  [('选读文章', 12), ('趣味答题', 10), ('每日答题', 5)]}
        value.read_learning_score = Mock(side_effect=[scores, {'选读文章': dict(value=12, maximum=12)}])
        value.read_article = Mock()
        value.funny_answer = Mock(side_effect=lambda: value.pause('平台访问验证'))
        value.daily_answer = Mock()
        with patch('utils.plyer.notification.notify'):
            value.task(['每日答题', '趣味答题', '选读文章'], unattended=True)
        report = value.run_report.snapshot()
        self.assertEqual(list(report['tasks']), ['选读文章', '趣味答题', '每日答题'])
        self.assertEqual(report['status'], 'needs_human')
        self.assertTrue(report['requires_human'])
        self.assertTrue(report['unattended'])
        self.assertEqual(report['blocked_task'], '趣味答题')
        self.assertEqual(report['tasks']['选读文章']['state'], 'verified')
        self.assertEqual(report['tasks']['选读文章']['after']['value'], 12)
        self.assertEqual(report['tasks']['趣味答题']['state'], 'needs_human')
        self.assertEqual(report['tasks']['每日答题']['state'], 'deferred')
        self.assertFalse(value.pause_flag)
        value.daily_answer.assert_not_called()
        self.assertEqual(value.read_learning_score.call_count, 2)

    def test_verification_at_start_does_not_start_any_learning_task(self):
        value = worker()
        value._finish_existing_quiz = Mock(side_effect=lambda: value.pause('平台访问验证'))
        value.read_learning_score = Mock()
        with patch('utils.plyer.notification.notify'):
            value.task(['选读文章', '每日答题'], unattended=True)
        report = value.run_report.snapshot()
        self.assertEqual(report['status'], 'needs_human')
        self.assertIsNone(report['blocked_task'])
        self.assertTrue(all(row['state'] == 'deferred' for row in report['tasks'].values()))
        value.read_learning_score.assert_not_called()

    def test_restart_cannot_clear_an_active_challenge(self):
        value = self.guarded_worker()
        with patch('utils.plyer.notification.notify'):
            with self.assertRaises(task_support.HumanRequired):
                value._reset_home()
        value.tasker.controller.post_stop_app.assert_not_called()
        value.tasker.controller.post_start_app.assert_not_called()

    def test_score_verification_does_not_swipe_a_challenge(self):
        import numpy as np
        value = self.guarded_worker()
        value._enter_learning_score = Mock(return_value=True)
        detail = job().get.return_value
        detail.nodes[0].recognition.all_results = [SimpleNamespace(text='访问验证', box=[200, 300, 150, 30])]
        value.tasker.post_task.return_value.get.return_value = detail
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=np.zeros((1280,720,3),np.uint8)
        value.tasker.post_recognition=Mock()
        value.tasker.post_recognition.return_value.wait.return_value.get.return_value=detail
        with patch('utils.plyer.notification.notify'):
            with self.assertRaises(task_support.HumanRequired):
                value.read_learning_score(['选读文章'])
        value.tasker.controller.post_swipe.assert_not_called()

    def test_content_check_catches_challenge_before_recognizing_underlying_page(self):
        value = self.guarded_worker()
        with patch('utils.plyer.notification.notify'):
            with self.assertRaises(task_support.HumanRequired):
                utils.MaaWorker._content_page_ready(value, 'article')
        value.tasker.post_task.assert_not_called()

    def test_manual_pause_can_resume_and_keeps_previous_behavior(self):
        value = worker()
        value.unattended = False
        with patch('utils.plyer.notification.notify'), \
             patch('utils.time.sleep', side_effect=lambda seconds: setattr(value, 'pause_flag', False)) as sleep:
            value.pause('平台访问验证')
        sleep.assert_called_once()
        self.assertFalse(value.pause_flag)

    def test_expired_verification_wait_reports_restart_without_another_probe(self):
        value = self.guarded_worker()
        ready = Mock(return_value=True)
        with patch('utils.plyer.notification.notify'), patch('utils.time.sleep') as sleep:
            with self.assertRaisesRegex(task_support.HumanRequired, '本轮结束.*重新执行') as error:
                value.pause('趣味答题真实访问验证，请本人完成', resume_when=ready)
        self.assertNotIn('自动继续', str(error.exception))
        ready.assert_not_called()
        sleep.assert_called_once_with(.1)
        self.assertFalse(value.pause_flag)
        value.tasker.controller.post_click.assert_not_called()

    def test_manual_verification_promises_resume_only_while_worker_remains_alive(self):
        value = worker()
        value.unattended = False
        value.run_report = Mock()
        ready = Mock(return_value=True)
        with patch('utils.plyer.notification.notify'), patch('utils.time.sleep'):
            value.pause('趣味答题真实访问验证，请本人完成', resume_when=ready)
        paused = value.run_report.update.call_args_list[0].kwargs
        self.assertEqual(paused['status'], 'paused')
        self.assertIn('正常页面恢复后自动继续本轮', paused['pause_reason'])
        self.assertNotIn('重新执行', paused['pause_reason'])
        ready.assert_called_once()
        self.assertFalse(value.pause_flag)
        self.assertEqual(value.run_report.update.call_args.kwargs['status'], 'running')

    def test_web_worker_releases_its_slot_after_unattended_verification(self):
        value = worker()
        value._finish_existing_quiz = Mock(side_effect=lambda: value.pause('平台访问验证'))
        state = webui.AppState()
        state.worker = value
        state.child_process = Mock()
        state.current_tasks = ['每日答题']
        with patch.object(webui, 'app_state', state), patch('utils.plyer.notification.notify'):
            webui.execute_tasks(webui.TaskModel(tasklist=['每日答题'], unattended=True))
        self.assertIsNone(state.child_process)
        self.assertEqual(state.current_tasks, [])
        self.assertFalse(value.pause_flag)
        self.assertEqual(value.last_task_error, 'HumanRequired')
        self.assertEqual(value.run_report.snapshot()['status'], 'needs_human')


if __name__ == '__main__':
    unittest.main()
