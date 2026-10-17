"""An existing unfinished round is recovered once before the full task list."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
import task_support
from test_optimization import worker


class RecoveryOnceTests(unittest.TestCase):
    def test_old_match_error_acknowledgement_does_not_leak_into_next_run(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(task_support,'REPORT_PATH',Path(directory)/'run.json'):
            value=worker();value._summarize_run=Mock()
            value._fun_metrics=dict(expired_match_acknowledged=True,matching_error_acknowledged=True)
            observed=[]
            value._finish_existing_quiz=Mock(side_effect=lambda:observed.append(dict(value._fun_metrics)))
            value.read_learning_score=Mock(return_value={'登录':dict(value=1,maximum=1)})
            value.task(['登录'])
            self.assertEqual(observed,[{}])
            self.assertEqual(value.run_report.snapshot()['status'],'completed')

    def test_full_task_list_recovers_existing_round_once_then_runs_all_handlers(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(task_support,'REPORT_PATH',Path(directory)/'run.json'):
            value=worker();events=[]
            value._finish_existing_quiz=Mock(side_effect=lambda:events.append('recover'))
            value._summarize_run=Mock();value._reset_home=Mock()
            tasks=['登录','选读文章','视听学习','趣味答题','每日答题']
            caps=dict(zip(tasks,[1,12,12,10,5]))
            before={name:dict(value=0,maximum=cap) for name,cap in caps.items()}
            def scores(targets):
                events.append('score')
                return before if len(targets)>1 else {targets[0]:dict(value=caps[targets[0]],maximum=caps[targets[0]])}
            value.read_learning_score=Mock(side_effect=scores)
            value.read_article=Mock();value.watch_video=Mock();value.funny_answer=Mock();value.daily_answer=Mock()
            value.task(tasks)
            self.assertEqual(value.run_report.snapshot()['status'],'completed')
            value._finish_existing_quiz.assert_called_once()
            self.assertEqual(events[:2],['recover','score'])
            for name in ('read_article','watch_video','funny_answer','daily_answer'):
                self.assertEqual(getattr(value,name).call_count,1,name)


if __name__=='__main__':unittest.main()
