"""Reused score pages must begin above the requested task, never inside the store."""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock
from test_optimization import worker
from task_support import TaskIncomplete, HumanRequired

TOP=[dict(text='积分中心',box=[300,50,120,35]),dict(text='登录',box=[50,700,70,35])]
MID=[dict(text='积分中心',box=[300,50,120,35]),dict(text='趣味答题',box=[50,420,140,35])]


class ScoreStartTests(unittest.TestCase):
    def make_worker(self,frames):
        value=worker()
        value._sleep=Mock()
        value._screen_records=Mock(side_effect=frames)
        value._reset_home=Mock()
        return value

    def store(self):
        return json.loads((Path(__file__).parent/'fixtures/daily-flow-20261016/score-page-at-store.json').read_text('utf-8'))

    def test_real_store_page_returns_to_top_before_reusing_score(self):
        value=self.make_worker([self.store(),MID,TOP])
        self.assertTrue(value._enter_learning_score())
        self.assertEqual(value.tasker.controller.post_swipe.call_count,2)
        self.assertEqual(value.tasker.controller.post_swipe.call_args.args,(360,420,360,1150,450))
        value._reset_home.assert_not_called()

    def test_existing_login_card_does_not_scroll(self):
        value=self.make_worker([TOP])
        self.assertTrue(value._enter_learning_score())
        value.tasker.controller.post_swipe.assert_not_called()

    def test_loading_title_without_task_or_store_content_does_not_scroll(self):
        value=self.make_worker([[dict(text='积分中心',box=[300,50,120,35])]])
        self.assertTrue(value._enter_learning_score())
        value.tasker.controller.post_swipe.assert_not_called()

    def test_verification_appearing_after_first_move_stops_further_movement(self):
        value=self.make_worker([self.store(),[dict(text='访问验证',box=[300,130,120,35])]])
        value._guard_verification=Mock(side_effect=[None,None,HumanRequired('本人验证')])
        with self.assertRaises(HumanRequired):value._enter_learning_score()
        self.assertEqual(value.tasker.controller.post_swipe.call_count,1)

    def test_foreground_loss_or_changed_page_cannot_receive_a_swipe(self):
        for records,foreground in [(self.store(),False),([dict(text='其它页面',box=[300,50,120,35])],True)]:
            with self.subTest(foreground=foreground):
                value=self.make_worker([])
                value._app_in_foreground.return_value=foreground
                with self.assertRaises(TaskIncomplete):value._scroll_score_to_start(records)
                value.tasker.controller.post_swipe.assert_not_called()

    def test_persistent_store_page_stops_after_six_moves(self):
        value=self.make_worker([])
        value._screen_records=Mock(return_value=self.store())
        with self.assertRaisesRegex(TaskIncomplete,'6次'):value._scroll_score_to_start(self.store())
        self.assertEqual(value.tasker.controller.post_swipe.call_count,6)


if __name__=='__main__':unittest.main()
