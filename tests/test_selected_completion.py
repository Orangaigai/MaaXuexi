"""Replay the actual stuck fifth question with selected, label-free options."""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

import daily_flow
from test_optimization import worker

BASE = Path(__file__).parent/'fixtures'
PAGE = json.loads((BASE/'daily-flow-20261017/completion-selected-no-labels.json').read_text('utf-8'))
RESULT = json.loads((BASE/'daily-flow-20261015/result.json').read_text('utf-8'))
PROFILE = [dict(text='学习积分', box=[100,460,140,40]), dict(text='我要答题', box=[300,460,140,40])]


class SelectedCompletionTests(unittest.TestCase):
    def test_actual_selected_question_is_completion_only_with_pending_submission(self):
        self.assertTrue(daily_flow.selected_question(PAGE))
        self.assertIsNone(daily_flow.completion_box(PAGE))
        self.assertEqual(daily_flow.completion_box(PAGE, pending=True), [585,60,63,33])
        self.assertEqual(daily_flow.daily_scene(PAGE, pending=True), 'completion')

    def test_owner_observer_leaves_pause_without_browser_continue(self):
        value=worker();value._screen_records=Mock(return_value=PAGE)
        self.assertFalse(value._daily_resume_ready())
        self.assertTrue(value._daily_resume_ready())
        value.tasker.controller.post_click.assert_not_called()

    def test_existing_five_answers_finish_and_return_without_new_questions(self):
        value=worker();value._sleep=Mock()
        value._screen_records=Mock(side_effect=[PAGE,PAGE,RESULT,PROFILE])
        value._finish_quiz(True)
        self.assertEqual(value._daily_finish_metrics['complete_clicks'],1)
        self.assertEqual(value._daily_finish_metrics['result_back_clicks'],1)
        self.assertTrue(value._daily_finish_metrics['result_observed'])
        self.assertTrue(value._daily_finish_metrics['left_completion_page'])
        self.assertEqual(value.tasker.controller.post_click.call_count,2)
        value.tasker.post_task.assert_not_called()

    def test_real_verification_still_overrides_this_underlying_question(self):
        captcha=[dict(text='访问验证',box=[200,100,180,40]),
                 dict(text='请拖动滑块完成拼图',box=[150,600,400,40])]
        self.assertIsNone(daily_flow.completion_box(PAGE+captcha,pending=True))
        self.assertEqual(daily_flow.daily_scene(PAGE+captcha,pending=True),'verification')

    def test_finish_button_and_source_footer_without_options_do_not_qualify(self):
        page=[row for row in PAGE if row['text']=='完成' or row['text'].startswith(('来源','出题'))]
        self.assertIsNone(daily_flow.completion_box(page,pending=True))


if __name__=='__main__':unittest.main()
