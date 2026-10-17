"""Regression for the real fifth-submit -> owner verification -> result path."""
import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import daily_flow
import task_support
from test_optimization import worker

FIXTURES=Path(__file__).parent/'fixtures/daily-flow-20261016'

def actual_submission():
    event=json.loads((FIXTURES/'fifth-submit-9fa4837a7a5f.json').read_text('utf-8-sig'))
    data=event['action_details']
    point=data['detail']['point']
    action=NS(name=data['name'],action=data['action'],success=data['success'],
              action_id=data['action_id'],result=NS(point=NS(x=point[0],y=point[1])))
    return NS(status=NS(succeeded=True),nodes=[NS(name=event['name'],
              completed=event['node_details']['completed'],action=action)])

def evidence():
    return dict(questions_confirmed=5,final_answer_submission=daily_flow.final_submission_evidence(actual_submission()),
                submission_metrics=dict(complete_clicks=0,result_observed=True,result_back_clicks=1,left_completion_page=True))


class DailyDirectResultTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        patcher=patch.object(task_support,'REPORT_PATH',Path(directory.name)/'run.json')
        patcher.start();self.addCleanup(patcher.stop)

    def test_real_native_fifth_submission_is_recorded(self):
        final=daily_flow.final_submission_evidence(actual_submission())
        self.assertEqual(final,dict(question_number=5,node='确定',action='Click',success=True,
                                    action_id=500000112,point=[586,91]))

    def test_native_click_result_keeps_json_point_as_list(self):
        from maa.define import ClickActionResult
        job=actual_submission()
        job.nodes[0].action.result=ClickActionResult(point=[586,91],contact=0,pressure=0)
        self.assertEqual(daily_flow.final_submission_evidence(job)['point'],[586,91])

    def test_bad_native_coordinate_data_does_not_interrupt_result_handling(self):
        for point in ([586],['586',91],None,[586,91,1]):
            with self.subTest(point=point):
                job=actual_submission();job.nodes[0].action.result.point=point
                self.assertIsNone(daily_flow.final_submission_evidence(job))

    def test_direct_result_with_real_submission_passes_without_an_extra_finish_click(self):
        self.assertTrue(daily_flow.acceptance_ready(evidence()))

    def test_failed_incomplete_or_unrelated_actions_are_not_submission_evidence(self):
        cases=[lambda job:setattr(job.status,'succeeded',False),
               lambda job:setattr(job.nodes[0],'completed',False),
               lambda job:setattr(job.nodes[0].action,'success',False),
               lambda job:setattr(job.nodes[0].action,'name','重试'),
               lambda job:setattr(job.nodes[0].action.result.point,'y',690)]
        for change in cases:
            with self.subTest(change=change):
                job=actual_submission();change(job)
                self.assertIsNone(daily_flow.final_submission_evidence(job))

    def test_real_submission_still_needs_five_questions_result_and_automatic_return(self):
        for name in ('questions_confirmed','result_observed','result_back_clicks','left_completion_page'):
            with self.subTest(missing=name):
                row=copy.deepcopy(evidence())
                (row if name=='questions_confirmed' else row['submission_metrics']).pop(name)
                self.assertFalse(daily_flow.acceptance_ready(row))

    def test_verification_direct_result_returns_without_sending_finish_again(self):
        old=Path(__file__).parent/'fixtures/daily-flow-20261015'
        verification=json.loads((old/'verification.json').read_text('utf-8'))
        result=json.loads((FIXTURES/'direct-result-9fa4837a7a5f.json').read_text('utf-8'))
        center=json.loads((FIXTURES/'returned-center-9fa4837a7a5f.json').read_text('utf-8'))
        value=worker();value.pause=Mock();value._sleep=Mock()
        value.tasker.controller.cached_image=None
        value._screen_records=Mock(side_effect=[verification,result,center])
        daily_flow.finish_daily_quiz(value)
        value.tasker.controller.post_click.assert_called_once_with(210,820)
        value.tasker.controller.post_swipe.assert_not_called()
        row=evidence();row['submission_metrics']=value._daily_finish_metrics
        self.assertTrue(daily_flow.acceptance_ready(row))

    def test_whole_manual_acceptance_does_not_report_this_real_path_as_failed(self):
        value=worker();value._summarize_run=Mock();value._finish_existing_quiz=Mock()
        value.read_learning_score=Mock(return_value={'每日答题':dict(value=5,maximum=5)})
        value.daily_answer=Mock(side_effect=lambda:value.run_report.task('每日答题',**evidence()))
        value.task(['每日答题'],daily_acceptance=True)
        report=value.run_report.snapshot()
        self.assertEqual(report['status'],'completed')
        self.assertTrue(report['tasks']['每日答题']['acceptance_flow_verified'])
        self.assertFalse(report['new_points_claimed'])


if __name__=='__main__':unittest.main()
