"""A wrong daily answer must not restart a round or be reported as full credit."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
from test_optimization import worker,job
from task_support import RunReport,TaskUnavailable,TaskIncomplete
import task_support


class SinglePassTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher=patch.object(task_support,'REPORT_PATH',Path(directory.name)/'run.json')
        patcher.start();self.addCleanup(patcher.stop)

    def quiz(self,transitions):
        value=worker()
        value._sleep=Mock()
        value._navigate_to_daily_answer=Mock(return_value=True)
        value._handle_choice=Mock(return_value=True)
        value._check_quiz_transition=Mock(side_effect=transitions)
        value._finish_quiz=Mock()
        value.run_report=RunReport(['每日答题'])
        return value

    def test_wrong_answer_advances_within_one_round_and_finishes_once(self):
        value=self.quiz([False,True,True,True,True,True])
        value.daily_answer()
        value._navigate_to_daily_answer.assert_called_once()
        self.assertEqual(value._handle_choice.call_count,5)
        value._finish_quiz.assert_called_once_with(True)
        wrong_clicks=[call for call in value.tasker.post_task.call_args_list
                      if call.args[0]=='下一题检测']
        self.assertEqual(len(wrong_clicks),1)
        self.assertEqual(wrong_clicks[0].args[1]['下一题检测']['action'],'Click')
        self.assertFalse(any(call.args[0] in ('返回','放弃答题退出') for call in value.tasker.post_task.call_args_list))
        row=value.run_report.snapshot()['tasks']['每日答题']
        self.assertEqual(row['incorrect_questions'],[1])
        self.assertEqual(row['questions_confirmed'],5)

    def test_fifth_wrong_answer_advances_to_completion_without_new_round(self):
        value=self.quiz([True,True,True,True,False,True])
        value.daily_answer()
        value._navigate_to_daily_answer.assert_called_once()
        value._finish_quiz.assert_called_once_with(True)
        self.assertEqual(value.run_report.snapshot()['tasks']['每日答题']['incorrect_questions'],[5])

    def test_missing_wrong_answer_next_button_stops_without_restarting(self):
        value=self.quiz([False])
        value.tasker.post_task.side_effect=lambda name,*a,**k:job(name!='下一题检测')
        with self.assertRaisesRegex(TaskIncomplete,'下一题按钮'):value.daily_answer()
        value._navigate_to_daily_answer.assert_called_once()
        value._finish_quiz.assert_not_called()

    def test_missing_daily_entry_is_not_reopened(self):
        value=worker()
        value._click_daily_answer_button=Mock(side_effect=TaskUnavailable('入口未确认'))
        with self.assertRaises(TaskUnavailable):value._navigate_to_daily_answer()
        value._click_daily_answer_button.assert_called_once()

    def test_five_correct_answers_complete_only_once(self):
        value=self.quiz([True]*5)
        value.daily_answer()
        value._navigate_to_daily_answer.assert_called_once()
        value._finish_quiz.assert_called_once_with(True)
        self.assertEqual(value.run_report.snapshot()['tasks']['每日答题']['incorrect_questions'],[])

    def test_previous_submission_network_page_resumes_existing_finish_handler(self):
        value=worker()
        value._adb_command=['adb.exe','-s','127.0.0.1:16384']
        value._previous_run_report=dict(status='needs_human',blocked_task='每日答题',
            tasks={'每日答题':dict(completion_pending=True)})
        value._resume_fun_submission=Mock(return_value=False)
        value._captcha_present=Mock(return_value=False)
        value._completion_button=Mock(return_value=False)
        value._finish_quiz=Mock()
        value._screen_records=Mock(return_value=[dict(text='网络开小差',box=[240,490,240,40]),
            dict(text='重试',box=[310,650,100,40])])
        value.tasker.post_task.return_value=job(False)
        with patch('utils.subprocess.run',return_value=SimpleNamespace(stdout='topResumedActivity=cn.xuexi.android/Main')):
            value._finish_existing_quiz()
        value._finish_quiz.assert_called_once_with(True)
        value.tasker.controller.post_stop_app.assert_not_called()

if __name__=='__main__':unittest.main()
