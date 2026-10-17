"""Recovery must recheck scores, stay bounded and report unfinished work."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import task_support
from test_optimization import worker


def score(value, maximum=12):
    return dict(value=value, maximum=maximum)


class RecoveryNotificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path_patch = patch.object(task_support, 'REPORT_PATH', Path(temporary.name) / 'run.json')
        path_patch.start(); self.addCleanup(path_patch.stop)
        notify_patch = patch('utils.plyer.notification.notify')
        self.notify = notify_patch.start(); self.addCleanup(notify_patch.stop)
        trace_patch = patch('utils.traceback.print_exc')
        trace_patch.start(); self.addCleanup(trace_patch.stop)
        self.value = worker()
        self.value._finish_existing_quiz = Mock()
        self.value._reset_home = Mock()

    def test_content_failure_rechecks_scores_without_replaying_content(self):
        value = self.value
        value.read_learning_score = Mock(side_effect=[{'选读文章': score(0)},
            {'选读文章': score(5)}, {'选读文章': score(12)}])
        value.read_article = Mock(side_effect=[task_support.TaskIncomplete('页面丢失'), None])
        value.task(['选读文章'], unattended=True)
        row = value.run_report.snapshot()['tasks']['选读文章']
        self.assertEqual(row['state'], 'failed')
        self.assertEqual(row['after'],score(5))
        self.assertNotIn('recovery_attempted',row)
        value.read_article.assert_called_once()
        value.prepare_device.assert_called_once()
        self.notify.assert_called_once()

    def test_full_score_after_failure_does_not_replay_content(self):
        value = self.value
        value.read_learning_score = Mock(side_effect=[{'选读文章': score(0)}, {'选读文章': score(12)}])
        value.read_article = Mock(side_effect=task_support.TaskIncomplete('返回页面失败'))
        value.task(['选读文章'], unattended=True)
        value.read_article.assert_called_once()
        self.assertEqual(value.run_report.snapshot()['status'], 'completed')

    def test_content_failure_is_not_retried_and_next_task_still_runs(self):
        value = self.value
        value.read_learning_score = Mock(side_effect=[{'选读文章': score(0), '视听学习': score(0)},
            {'选读文章': score(3)}, {'视听学习': score(12)}])
        value.read_article = Mock(side_effect=task_support.TaskIncomplete('内容页异常'))
        value.watch_video = Mock()
        value.task(['选读文章', '视听学习'], unattended=True)
        report = value.run_report.snapshot()
        self.assertEqual(value.read_article.call_count, 1)
        value.watch_video.assert_called_once()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['completed_tasks'], ['视听学习'])
        self.assertEqual(report['unfinished_tasks'], ['选读文章'])
        self.assertIn('待处理/未确认完成：选读文章', report['attention_message'])
        self.notify.assert_called_once()

    def test_article_shortfall_is_recorded_without_replaying_the_whole_task(self):
        value = self.value
        value.read_learning_score = Mock(side_effect=[{'选读文章': score(0)}, {'选读文章': score(11)},
            {'选读文章': score(11)}, {'选读文章': score(12)}])
        value.read_article = Mock()
        value.task(['选读文章'], unattended=True)
        value.read_article.assert_called_once()
        report=value.run_report.snapshot()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['tasks']['选读文章']['after'],score(11))

    def test_verification_during_score_recheck_stops_before_later_tasks(self):
        value = self.value
        value.read_learning_score = Mock(side_effect=[{'选读文章': score(0), '视听学习': score(0)},
            task_support.HumanRequired('平台访问验证')])
        value.read_article = Mock(side_effect=task_support.TaskIncomplete('页面异常'))
        value.watch_video = Mock()
        value.task(['选读文章', '视听学习'], unattended=True)
        report = value.run_report.snapshot()
        self.assertEqual(report['status'], 'needs_human')
        self.assertEqual(report['unfinished_tasks'], ['选读文章', '视听学习'])
        self.assertEqual(value.read_learning_score.call_count, 2)
        value.read_article.assert_called_once()
        value.watch_video.assert_not_called()
        value._reset_home.assert_called_once()  # Only the first, normal task start.
        value.tasker.controller.post_stop_app.assert_not_called()
        self.assertFalse(value.pause_flag)
        self.notify.assert_called_once()

    def test_unreadable_recovery_score_prevents_duplicate_content(self):
        value = self.value
        value.read_learning_score = Mock(side_effect=[{'选读文章': score(0)}, {}])
        value.read_article = Mock(side_effect=task_support.TaskIncomplete('列表异常'))
        value.task(['选读文章'], unattended=True)
        value.read_article.assert_called_once()
        report = value.run_report.snapshot()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['tasks']['选读文章']['reason'],'列表异常')

    def test_notification_failure_preserves_report_and_releases_task(self):
        value = self.value
        self.notify.side_effect = RuntimeError('offline notification fixture')
        value.read_learning_score = Mock(return_value={'趣味答题': score(0, 10)})
        value.funny_answer = Mock(side_effect=task_support.TaskUnavailable('今天是对战模式'))
        value.task(['趣味答题'], unattended=True)
        persisted = task_support.read_report()
        self.assertEqual(persisted['unfinished_tasks'], ['趣味答题'])
        self.assertEqual(persisted['notification_error'], 'RuntimeError')
        self.assertEqual(persisted['status'], 'partial')
        self.assertIsNone(value._deadline)

    def test_answer_tasks_are_not_automatically_replayed_after_failure(self):
        value = self.value
        value.read_learning_score = Mock(return_value={'趣味答题': score(0, 10)})
        value.funny_answer = Mock(side_effect=task_support.TaskIncomplete('模型超时'))
        value._recover_content_task = Mock()
        value.task(['趣味答题'], unattended=True)
        value._recover_content_task.assert_not_called()
        value.funny_answer.assert_called_once()

    def test_manual_content_failure_keeps_existing_retry_policy(self):
        value = self.value
        value.read_learning_score = Mock(return_value={'选读文章': score(0)})
        value.read_article = Mock(side_effect=task_support.TaskIncomplete('页面异常'))
        value._recover_content_task = Mock()
        value.task(['选读文章'])
        value._recover_content_task.assert_not_called()
        self.assertEqual(value.run_report.snapshot()['unfinished_tasks'], ['选读文章'])

    def test_already_full_scores_have_no_unfinished_notification(self):
        value = self.value
        value.read_learning_score = Mock(return_value={'选读文章': score(12)})
        value.task(['选读文章'], unattended=True)
        self.assertEqual(value.run_report.snapshot()['unfinished_tasks'], [])
        self.notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
