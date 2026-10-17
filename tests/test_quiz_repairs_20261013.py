"""Regressions from the real photo-fill and source-baseline failures."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from fun_challenge import fingerprint, question_text
from task_support import TaskIncomplete
from test_optimization import worker, job

FIXTURES=Path(__file__).parent/'fixtures/quiz-20261013'

class QuizRepairTests(unittest.TestCase):
    def fill_worker(self, filtered=True):
        actual=json.loads((FIXTURES/'fill-photo-match.json').read_text('utf-8'))
        value=worker()
        value._sleep=Mock()
        value.pause=Mock()
        value._get_red_texts=Mock(return_value=actual['hint_red_texts'])
        value.tasker.controller.post_screencap.return_value.get.return_value=np.zeros((1280,720,3),np.uint8)
        field=job()
        field.get.return_value.nodes[0].recognition=SimpleNamespace(
            all_results=actual['blank_boxes'],filtered_results=actual['filtered_boxes'] if filtered else [])
        value.tasker.post_task=Mock(side_effect=lambda name:field if name=='文本框' else job())
        return value

    def test_photo_false_matches_do_not_inflate_two_real_blank_cells_to_seventeen(self):
        value=self.fill_worker()
        imgs,options,red_texts,count=value._prepare('填空题')
        self.assertEqual(count,2)
        self.assertEqual(red_texts,['兵民'])
        self.assertEqual(len(imgs),2)

    def test_normal_fill_mode_uses_exact_length_hint_without_model_call(self):
        value=self.fill_worker()
        value.fast_answer=False
        value.ai_resolver=SimpleNamespace(resolve_blank=Mock())
        imgs,options,red_texts,count=value._prepare('填空题')
        answer,from_hint=value._determine_answer('填空题',options,red_texts,imgs,count)
        self.assertEqual((answer,from_hint),('兵民',True))
        value.ai_resolver.resolve_blank.assert_not_called()
        value.pause.assert_not_called()

    def test_no_passed_template_matches_does_not_use_rejected_photo_cells(self):
        value=self.fill_worker(filtered=False)
        self.assertEqual(value._prepare('填空题')[3],0)

    def test_uncertain_daily_model_answer_ends_task_without_indefinite_pause_or_click(self):
        value=worker()
        value.pause=Mock()
        value.ai_resolver=SimpleNamespace(resolve_choice=Mock(return_value=None),last_error='invalid_choice_answer')
        with self.assertRaisesRegex(TaskIncomplete,'invalid_choice_answer'):
            value._determine_answer('单选题',{},[],[np.zeros((80,160,3),np.uint8)])
        value.pause.assert_not_called()
        value.tasker.controller.post_click.assert_not_called()
        value.tasker.post_task.assert_not_called()

    def test_uncertain_video_fill_answer_does_not_wait_for_human_or_submit(self):
        value=worker()
        value.pause=Mock()
        value.tasker.post_task.return_value=job(False)
        value.ai_resolver=SimpleNamespace(resolve_blank=Mock(return_value=None),last_error='invalid_blank_answer')
        value._submit_answer=Mock()
        with self.assertRaisesRegex(TaskIncomplete,'invalid_blank_answer'):
            value._handle_fill_blank()
        value.pause.assert_not_called()
        value._submit_answer.assert_not_called()

    def test_real_publisher_fragment_and_slide_do_not_change_question_identity(self):
        actual=json.loads((FIXTURES/'fun-source-animation.json').read_text('utf-8'))
        before=question_text(actual['before'],actual['before_options'])
        after=question_text(actual['after'],actual['after_options'])
        self.assertTrue(before)
        self.assertEqual(before,after)
        self.assertNotIn('武汉大学',after)
        self.assertEqual(fingerprint(actual['before'],actual['before_options']),
                         fingerprint(actual['after'],actual['after_options']))

    def test_changed_question_still_invalidates_the_answer(self):
        actual=json.loads((FIXTURES/'fun-source-animation.json').read_text('utf-8'))
        after=actual['after']
        after[0]['text']='这是另一个不同问题。'
        self.assertNotEqual(fingerprint(actual['before'],actual['before_options']),
                            fingerprint(after,actual['after_options']))

    def completion_worker(self, blocked='每日答题'):
        value=worker()
        value._adb_command=['adb','-s','local-device']
        value._captcha_present=Mock(return_value=False)
        value._completion_button=Mock(return_value=True)
        value._finish_quiz=Mock()
        value.tasker.post_task.return_value=job(False)
        value._previous_run_report=dict(status='needs_human',blocked_task=blocked)
        return value

    def test_resumed_daily_complete_button_is_submitted_without_reanswering(self):
        value=self.completion_worker()
        with patch('utils.subprocess.run',return_value=SimpleNamespace(
                stdout='topResumedActivity=ActivityRecord{cn.xuexi.android/QuizActivity}')):
            value._finish_existing_quiz()
        value._finish_quiz.assert_called_once_with(True)

    def test_unrelated_pending_task_does_not_authorize_clicking_complete(self):
        value=self.completion_worker(blocked='趣味答题')
        with patch('utils.subprocess.run',return_value=SimpleNamespace(
                stdout='topResumedActivity=ActivityRecord{cn.xuexi.android/QuizActivity}')):
            value._finish_existing_quiz()
        value._finish_quiz.assert_not_called()

    def test_current_verification_is_checked_before_pending_complete_button(self):
        from task_support import HumanRequired
        value=self.completion_worker()
        value._captcha_present.return_value=True
        value.pause=Mock(side_effect=HumanRequired('本人验证'))
        with patch('utils.subprocess.run',return_value=SimpleNamespace(
                stdout='topResumedActivity=ActivityRecord{cn.xuexi.android/QuizActivity}')):
            with self.assertRaises(HumanRequired):
                value._finish_existing_quiz()
        value._completion_button.assert_not_called()
        value._finish_quiz.assert_not_called()

    def test_ten_correct_waits_for_normal_settlement_without_another_answer(self):
        value=worker()
        value._fun_metrics={}
        value._sleep=Mock()
        value._fun_records=Mock(side_effect=[
            [dict(text='连续答对x10',box=[400,200,180,30])],
            [dict(text='练习结束',box=[250,250,200,40])]])
        value._wait_fun_settlement()
        self.assertTrue(value._fun_metrics['settlement_observed'])
        value.tasker.controller.post_click.assert_not_called()
        value.tasker.post_task.assert_not_called()

    def test_unsaved_history_dialog_is_cancelled_instead_of_abandoned(self):
        value=worker()
        value._sleep=Mock()
        value._fun_click=Mock(return_value=True)
        value._fun_records=Mock(return_value=[
            dict(text='退出后作答历史将不会保存',box=[160,617,396,34]),
            dict(text='退出',box=[186,724,74,42]),
            dict(text='继续',box=[456,725,73,39])])
        with self.assertRaisesRegex(TaskIncomplete,'已取消退出'):
            value._exit_fun_challenge()
        self.assertEqual(value._fun_click.call_args.args[1],[r'继续',r'继续答题'])

    def test_unknown_settlement_times_out_without_discarding_history(self):
        value=worker()
        value._race_time=Mock(side_effect=[0,76])
        value._exit_fun_challenge=Mock()
        with self.assertRaisesRegex(TaskIncomplete,'尚未确认本局结算'):
            value._wait_fun_settlement()
        value._exit_fun_challenge.assert_not_called()

    def test_recovering_completion_persists_progress_before_a_new_verification(self):
        from task_support import HumanRequired
        value=self.completion_worker()
        value.run_report=SimpleNamespace(snapshot=Mock(return_value=dict(tasks={'每日答题':{}})),task=Mock(),update=Mock())
        value._finish_quiz=Mock(side_effect=HumanRequired('新的本人验证'))
        with patch('utils.subprocess.run',return_value=SimpleNamespace(
                stdout='topResumedActivity=ActivityRecord{cn.xuexi.android/QuizActivity}')):
            with self.assertRaises(HumanRequired):
                value._finish_existing_quiz()
        value.run_report.task.assert_called_once_with('每日答题',state='running',questions_confirmed=5,completion_pending=True)
        self.assertEqual(value.run_report.update.call_args.kwargs['blocked_task'],'每日答题')

    def test_legacy_daily_only_completion_with_missing_block_name_can_resume(self):
        value=self.completion_worker(blocked=None)
        value._previous_run_report['tasks']={'每日答题':dict(state='deferred')}
        with patch('utils.subprocess.run',return_value=SimpleNamespace(
                stdout='topResumedActivity=ActivityRecord{cn.xuexi.android/QuizActivity}')):
            value._finish_existing_quiz()
        value._finish_quiz.assert_called_once_with(True)

    def end_dialog(self):
        return json.loads((FIXTURES/'fun-ended-dialog.json').read_text('utf-8'))

    def test_real_end_round_button_is_clicked_instead_of_system_back(self):
        value=worker()
        value._sleep=Mock()
        value._fun_metrics={}
        value._fun_click=Mock(return_value=True)
        value._fun_records=Mock(side_effect=[self.end_dialog(),[dict(text='强国总题库',box=[200,180,300,60])]])
        value._exit_fun_challenge()
        value._fun_click.assert_called_once()
        self.assertEqual(value._fun_click.call_args.args[1],[r'结束本局'])
        value.tasker.post_task.assert_not_called()
        self.assertTrue(value._fun_metrics['end_round_clicked'])
        self.assertTrue(value._fun_metrics['settlement_committed'])

    def test_end_round_button_is_not_repeated_while_waiting_for_result(self):
        value=worker()
        value._sleep=Mock()
        value._fun_metrics={}
        value._fun_click=Mock(return_value=True)
        value._fun_records=Mock(return_value=self.end_dialog())
        with self.assertRaisesRegex(TaskIncomplete,'未重复点击'):
            value._exit_fun_challenge()
        value._fun_click.assert_called_once()
        value.tasker.post_task.assert_not_called()

    def pending_fun_worker(self, count=10):
        value=worker()
        value.run_report=SimpleNamespace(snapshot=Mock(return_value=dict(tasks={'趣味答题':{}})),task=Mock(),update=Mock())
        value._fun_records=Mock(return_value=self.end_dialog())
        value._exit_fun_challenge=Mock()
        value.funny_answer=Mock()
        previous=dict(tasks={'趣味答题':dict(metrics=dict(mode='挑战答题',
            settlement_observed=True,confirmed_correct=count,exited=False))})
        return value,previous

    def test_existing_ten_correct_round_resumes_submission_without_replaying(self):
        value,previous=self.pending_fun_worker()
        self.assertTrue(value._resume_fun_submission(previous))
        value._exit_fun_challenge.assert_called_once_with(self.end_dialog())
        value.funny_answer.assert_not_called()
        self.assertTrue(value._fun_metrics['resumed_submission'])

    def test_no_complete_round_or_wrong_current_page_does_not_resume_submission(self):
        for count in (9,10):
            value,previous=self.pending_fun_worker(count)
            value._fun_records.return_value=[dict(text='普通页面',box=[200,180,300,60])]
            self.assertFalse(value._resume_fun_submission(previous))
            value._exit_fun_challenge.assert_not_called()
            value.funny_answer.assert_not_called()

    def test_real_fun_network_dialog_retries_and_keeps_existing_round(self):
        value=worker()
        value._sleep=Mock()
        value.pause=Mock()
        value._fun_metrics=dict(confirmed_correct=10)
        value._screen_records=Mock(side_effect=[json.loads((FIXTURES/'fun-network-error.json').read_text('utf-8')),self.end_dialog()])
        self.assertEqual(value._fun_records(),self.end_dialog())
        value.pause.assert_not_called()
        value.tasker.controller.post_click.assert_called_once_with(494,728)
        self.assertEqual(value._fun_metrics['confirmed_correct'],10)
        self.assertEqual(value._fun_metrics['network_error'],'网络开小差，请稍后再试')
        self.assertEqual(value._fun_metrics['network_retry_clicks_current_run'],1)

    def test_retry_then_verification_stops_after_one_retry_without_new_round(self):
        from task_support import HumanRequired
        value=worker()
        value._sleep=Mock()
        value.pause=Mock(side_effect=HumanRequired('本人验收'))
        value._fun_metrics=dict(confirmed_correct=3)
        value._screen_records=Mock(side_effect=[json.loads((FIXTURES/'fun-network-error.json').read_text('utf-8')),
            [dict(text='访问验证',box=[303,134,113,31])]])
        with self.assertRaises(HumanRequired):
            value._fun_records()
        value.tasker.controller.post_click.assert_called_once_with(494,728)
        self.assertEqual(value._fun_metrics['confirmed_correct'],3)

    def test_verification_has_priority_over_background_network_retry(self):
        from task_support import HumanRequired
        value=worker()
        value.pause=Mock(side_effect=HumanRequired('本人验收'))
        records=json.loads((FIXTURES/'fun-network-error.json').read_text('utf-8'))
        records.append(dict(text='访问验证',box=[303,134,113,31]))
        value._screen_records=Mock(return_value=records)
        with self.assertRaises(HumanRequired):
            value._fun_records()
        value.tasker.controller.post_click.assert_not_called()

    def test_persistent_network_error_has_only_one_retry(self):
        import itertools
        from task_support import HumanRequired
        value=worker()
        value._sleep=Mock()
        value._race_time=Mock(side_effect=itertools.count(0,5))
        value._screen_records=Mock(return_value=json.loads((FIXTURES/'fun-network-error.json').read_text('utf-8')))
        with self.assertRaisesRegex(HumanRequired,'重试1次'):
            value._fun_records()
        self.assertEqual(value.tasker.controller.post_click.call_count,1)

    def test_duplicate_retry_buttons_do_not_cause_an_arbitrary_click(self):
        value=worker()
        records=json.loads((FIXTURES/'fun-network-error.json').read_text('utf-8'))
        records.append(dict(text='重试',box=[457,850,74,38]))
        value._screen_records=Mock(return_value=records)
        with self.assertRaisesRegex(TaskIncomplete,'唯一的重试按钮'):
            value._fun_records()
        value.tasker.controller.post_click.assert_not_called()

    def test_fun_submission_progress_is_saved_before_checking_current_verification(self):
        from task_support import HumanRequired
        value,previous=self.pending_fun_worker()
        value._adb_command=['adb','-s','local-device']
        value._previous_run_report=previous
        value._fun_records=Mock(side_effect=HumanRequired('平台验证'))
        with patch('utils.subprocess.run',return_value=SimpleNamespace(
                stdout='topResumedActivity=ActivityRecord{cn.xuexi.android/QuizActivity}')):
            with self.assertRaises(HumanRequired):
                value._finish_existing_quiz()
        kwargs=value.run_report.task.call_args.kwargs
        self.assertTrue(kwargs['completion_pending'])
        self.assertEqual(kwargs['metrics']['confirmed_correct'],10)
        self.assertEqual(value.run_report.update.call_args.kwargs['blocked_task'],'趣味答题')
        value._exit_fun_challenge.assert_not_called()

    def test_current_fun_task_is_not_reset_by_previous_round_recovery(self):
        value,previous=self.pending_fun_worker()
        value.run_report.snapshot.return_value=dict(tasks={'趣味答题':dict(state='running')})
        self.assertFalse(value._resume_fun_submission(previous))
        value._fun_records.assert_not_called()
        value.run_report.task.assert_not_called()
        value._exit_fun_challenge.assert_not_called()

    def test_brief_fun_loading_waits_without_touching_underlying_end_button(self):
        value=worker()
        value._sleep=Mock()
        loading=json.loads((FIXTURES/'fun-post-verification-loading.json').read_text('utf-8'))
        value._screen_records=Mock(side_effect=[loading,self.end_dialog()])
        self.assertEqual(value._fun_records(),self.end_dialog())
        value._sleep.assert_called_once_with(.5)
        value.tasker.controller.post_click.assert_not_called()

    def test_stuck_real_loading_stops_without_retries_or_verification_prompt(self):
        value=worker()
        value._sleep=Mock()
        value.pause=Mock()
        value._fun_metrics=dict(confirmed_correct=10)
        value._screen_records=Mock(return_value=json.loads((FIXTURES/'fun-post-verification-loading.json').read_text('utf-8')))
        with self.assertRaisesRegex(TaskIncomplete,'加载长时间未结束'):
            value._fun_records()
        self.assertLessEqual(value._screen_records.call_count,25)
        self.assertTrue(value._fun_metrics['loading_timeout'])
        value.pause.assert_not_called()
        value.tasker.controller.post_click.assert_not_called()

    def test_reset_zero_end_page_cannot_resume_saved_ten_or_start_new_round(self):
        value,previous=self.pending_fun_worker()
        page=[row for row in json.loads((FIXTURES/'fun-post-verification-loading.json').read_text('utf-8'))
              if not row['text'].startswith('加载中')]
        value._fun_records.return_value=page
        with self.assertRaisesRegex(TaskIncomplete,'显示答对0题.*保存的10题不一致'):
            value._resume_fun_submission(previous)
        value._exit_fun_challenge.assert_not_called()
        value.funny_answer.assert_not_called()
        self.assertEqual(value._fun_metrics['confirmed_correct'],10)
        self.assertEqual(value._fun_metrics['observed_settlement_correct'],0)
        self.assertTrue(value.run_report.task.call_args.kwargs['completion_pending'])

    def test_end_count_mismatch_does_not_click_submit(self):
        value=worker()
        value._fun_metrics=dict(confirmed_correct=10)
        page=[dict(row,text='本次答对0题') if row['text'].startswith('本次答对') else row
              for row in self.end_dialog()]
        value._fun_records=Mock(return_value=page)
        value._fun_click=Mock()
        with self.assertRaisesRegex(TaskIncomplete,'不一致'):
            value._exit_fun_challenge()
        value._fun_click.assert_not_called()

    def test_replay_result_returns_once_without_restarting_or_reclicking_end(self):
        value=worker()
        value._sleep=Mock()
        value._fun_metrics=dict(confirmed_correct=3,end_round_clicked=True)
        value._fun_click=Mock()
        value._fun_records=Mock(side_effect=[
            [dict(text='再来一局',box=[220,1000,300,60]),dict(text='本次答对3题',box=[200,400,280,40])],
            [dict(text='强国总题库',box=[200,180,300,60])]])
        value._exit_fun_challenge()
        value._fun_click.assert_not_called()
        value.tasker.post_task.assert_called_once_with('返回')
        self.assertTrue(value._fun_metrics['result_page_observed'])

    def test_three_answer_pending_round_resumes_using_its_saved_target(self):
        value,previous=self.pending_fun_worker(3)
        previous['tasks']['趣味答题']['metrics']['target_correct']=3
        page=[dict(row,text='本次答对3题') if row['text'].startswith('本次答对') else row
              for row in self.end_dialog()]
        value._fun_records.return_value=page
        self.assertTrue(value._resume_fun_submission(previous))
        value._exit_fun_challenge.assert_called_once_with(page)

if __name__=='__main__':
    unittest.main()
