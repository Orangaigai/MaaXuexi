"""A manual fresh round must exercise Finish; already-full scores cannot pass it."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
import task_support
import webui
from test_optimization import worker


class DailyAcceptanceTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher=patch.object(task_support,'REPORT_PATH',Path(directory.name)/'run.json')
        patcher.start();self.addCleanup(patcher.stop)

    def daily_worker(self, valid=True):
        value=worker()
        value._summarize_run=Mock()
        value._finish_existing_quiz=Mock()
        value.read_learning_score=Mock(return_value={'每日答题':dict(value=5,maximum=5)})
        def answer():
            value.run_report.task('每日答题',questions_confirmed=5,
                submission_metrics=dict(complete_clicks=1 if valid else 0,left_completion_page=valid,
                                        result_observed=valid,result_back_clicks=1 if valid else 0))
        value.daily_answer=Mock(side_effect=answer)
        return value

    def test_normal_full_score_still_skips_without_running_a_round(self):
        value=self.daily_worker()
        value.task(['每日答题'])
        value.daily_answer.assert_not_called()
        self.assertEqual(value.run_report.snapshot()['tasks']['每日答题']['state'],'skipped_full')

    def test_manual_acceptance_exercises_exactly_one_full_score_round(self):
        value=self.daily_worker()
        value.task(['每日答题'],daily_acceptance=True)
        value.daily_answer.assert_called_once()
        report=value.run_report.snapshot()
        self.assertEqual(report['status'],'completed')
        self.assertFalse(report['new_points_claimed'])
        self.assertTrue(report['tasks']['每日答题']['acceptance_flow_verified'])

    def test_full_score_cannot_pass_acceptance_without_actual_finish_evidence(self):
        value=self.daily_worker(valid=False)
        value.task(['每日答题'],daily_acceptance=True)
        self.assertEqual(value.run_report.snapshot()['status'],'partial')
        self.assertNotIn('acceptance_flow_verified',value.run_report.snapshot()['tasks']['每日答题'])

    def test_full_score_and_left_page_cannot_replace_result_and_return_evidence(self):
        for missing in ('result_observed','result_back_clicks'):
            with self.subTest(missing=missing):
                value=self.daily_worker()
                def answer():
                    metrics=dict(complete_clicks=1,left_completion_page=True,
                                 result_observed=True,result_back_clicks=1)
                    metrics.pop(missing)
                    value.run_report.task('每日答题',questions_confirmed=5,submission_metrics=metrics)
                value.daily_answer=Mock(side_effect=answer)
                value.task(['每日答题'],daily_acceptance=True)
                report=value.run_report.snapshot()
                self.assertEqual(report['status'],'partial')
                self.assertNotIn('acceptance_flow_verified',report['tasks']['每日答题'])

    def test_acceptance_cannot_run_unattended_or_other_tasks(self):
        for tasks,unattended in [(['每日答题'],True),(['登录','每日答题'],False)]:
            with self.subTest(tasks=tasks,unattended=unattended):
                value=self.daily_worker()
                value.task(tasks,unattended=unattended,daily_acceptance=True)
                value.prepare_device.assert_not_called()
                self.assertEqual(value.run_report.snapshot()['status'],'failed')

    def test_explicit_endpoint_starts_only_manual_daily_acceptance(self):
        with patch.object(webui,'start_selected',return_value={'status':'success'}) as start:
            with TestClient(webui.app) as client:
                self.assertEqual(client.post('/api/local/verify-daily-flow',json={'tasklist':['每日答题']}).json()['status'],'success')
            args=start.call_args
            self.assertEqual(args.args[0].tasklist,['每日答题'])
            self.assertFalse(args.args[0].unattended)
            self.assertEqual(args.kwargs,{'daily_acceptance':True})

    def test_acceptance_uses_existing_key_and_active_task_guards(self):
        state=webui.AppState();state.worker=Mock()
        for active,key in [(True,True),(False,False)]:
            with self.subTest(active=active,key=key),patch.object(webui,'app_state',state), \
                 patch.object(webui,'task_running',return_value=active),patch.object(webui,'key_configured',return_value=key):
                result=webui.verify_daily_flow(webui.TaskModel(tasklist=['每日答题']))
                self.assertEqual(result['status'],'failed')
        self.assertIsNone(state.child_process)

    def test_live_acceptance_rejects_bodyless_forms_and_nonmanual_daily_requests(self):
        with patch.object(webui,'start_selected') as start:
            with TestClient(webui.app) as client:
                url='/api/local/verify-daily-flow'
                self.assertEqual(client.post(url).status_code,422)
                self.assertEqual(client.post(url,data={'tasklist':'每日答题'}).status_code,422)
                for body in [{'tasklist':['登录']},{'tasklist':['每日答题'],'unattended':True}]:
                    self.assertEqual(client.post(url,json=body).status_code,422)
            start.assert_not_called()

    def test_executor_keeps_normal_run_arguments_and_only_marks_explicit_acceptance(self):
        state=webui.AppState();state.worker=Mock()
        tasks=webui.TaskModel(tasklist=['每日答题'],unattended=True)
        with patch.object(webui,'app_state',state):
            webui.execute_tasks(tasks)
            state.worker.task.assert_called_once_with(['每日答题'],False,False,unattended=True)
            state.worker.task.reset_mock()
            webui.execute_tasks(webui.TaskModel(tasklist=['每日答题']),True)
            state.worker.task.assert_called_once_with(['每日答题'],False,False,unattended=False,daily_acceptance=True)

    def test_acceptance_uses_normal_page_observer_without_extra_continue(self):
        value=self.daily_worker()
        value.unattended=False
        value.run_report=None
        value._daily_acceptance_active=True
        ready=Mock(side_effect=[False,True])
        now=[0.0]
        with patch('utils.plyer.notification.notify'), \
             patch('utils.time.monotonic',side_effect=lambda:now[0]), \
             patch('utils.time.sleep',side_effect=lambda seconds:now.__setitem__(0,now[0]+seconds)):
            value.pause('真实访问验证',resume_when=ready)
        self.assertEqual(ready.call_count,2)
        self.assertFalse(value.pause_flag)


if __name__=='__main__':unittest.main()
