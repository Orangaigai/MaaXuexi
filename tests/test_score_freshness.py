"""The first score check of every run must not reuse yesterday's open page."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import task_support
from task_support import TaskIncomplete,HumanRequired
from test_optimization import worker,job

SCORE=[dict(text='积分中心',box=[305,57,103,31]),dict(text='登录',box=[52,500,70,35])]
PROFILE=[dict(text='我要答题',box=[300,460,140,40]),dict(text='学习积分',box=[100,460,140,40])]
VERIFY=[dict(text='访问验证',box=[303,134,113,31])]


class ScoreFreshnessTests(unittest.TestCase):
    def make_worker(self,frames):
        value=worker();value._sleep=Mock();value._reset_home=Mock()
        value._score_refresh_required=True
        value._screen_records=Mock(side_effect=frames)
        value._scroll_score_to_start=Mock()
        return value

    def test_first_score_check_leaves_and_reenters_the_existing_page(self):
        value=self.make_worker([SCORE,PROFILE])
        self.assertTrue(value._enter_learning_score())
        self.assertEqual([call.args[0] for call in value.tasker.post_task.call_args_list],
                         ['返回','本机个人页学习积分','积分规则'])
        value._scroll_score_to_start.assert_not_called()
        value._reset_home.assert_not_called()
        self.assertFalse(value._score_refresh_required)

    def test_later_checks_in_same_run_can_reuse_the_confirmed_page(self):
        value=self.make_worker([SCORE,PROFILE,SCORE])
        self.assertTrue(value._enter_learning_score())
        self.assertTrue(value._enter_learning_score())
        self.assertEqual(sum(call.args[0]=='返回' for call in value.tasker.post_task.call_args_list),1)
        value._scroll_score_to_start.assert_called_once_with(SCORE)

    def test_failed_departure_does_not_accept_the_old_score_page(self):
        value=self.make_worker([SCORE]);value.tasker.post_task.return_value=job(False)
        with self.assertRaisesRegex(TaskIncomplete,'未使用旧分数'):
            value._enter_learning_score()
        self.assertEqual(value.tasker.post_task.call_count,1)
        self.assertTrue(value._score_refresh_required)

    def test_verification_after_departure_prevents_score_entry(self):
        value=self.make_worker([SCORE,VERIFY])
        value._guard_verification=Mock(side_effect=[None,HumanRequired('本人验证')])
        with self.assertRaises(HumanRequired):value._enter_learning_score()
        self.assertEqual([call.args[0] for call in value.tasker.post_task.call_args_list],['返回'])
        self.assertTrue(value._score_refresh_required)

    def test_each_new_task_run_requests_a_fresh_initial_score(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(task_support,'REPORT_PATH',Path(directory)/'run.json'):
            value=worker();value._finish_existing_quiz=Mock();value._summarize_run=Mock()
            flags=[]
            value.read_learning_score=Mock(side_effect=lambda targets:flags.append(value._score_refresh_required) or {'登录':dict(value=1,maximum=1)})
            value._score_refresh_required=False
            value.task(['登录']);value._score_refresh_required=False;value.task(['登录'])
            self.assertEqual(flags,[True,True])


if __name__=='__main__':unittest.main()
