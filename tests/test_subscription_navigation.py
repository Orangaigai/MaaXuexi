"""Observed subscription navigation races; no device, model or account writes."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from subscription_tasks import SubscriptionTasks,subscription_scene
from task_support import TaskIncomplete
from test_optimization import job

FIXTURES=Path(__file__).parent/'fixtures/subscriptions'


class SubscriptionNavigationTests(unittest.TestCase):
    def setUp(self):
        self.mine=json.loads((FIXTURES/'mine-cancelled-20261024.json').read_text('utf-8'))
        self.profile=json.loads((FIXTURES/'navigation-profile-20261024.json').read_text('utf-8'))
        self.catalog=json.loads((FIXTURES/'catalog.json').read_text('utf-8'))
        self.score=[dict(text='学习积分',box=[280,55,180,35])]

    def worker(self,frames):
        value=SubscriptionTasks();value._check_active=Mock();value._sleep=Mock()
        value._sub_records=Mock(side_effect=frames);value._sub_click=Mock();value._sub_evidence=Mock()
        value.tasker=Mock();value.tasker.post_task.return_value=job()
        return value

    def test_cancelled_list_goes_back_with_its_header_and_waits_through_loading(self):
        value=self.worker([self.mine,[],self.mine,self.profile])
        value._sub_back()
        self.assertEqual(value._sub_records.call_count,4)
        self.assertEqual(value._sub_click.call_args.args[0]['box'],[14,36,64,68])
        value.tasker.post_task.assert_not_called()

    def test_catalog_back_waits_for_a_real_destination_not_a_blank_frame(self):
        value=self.worker([self.catalog,[],self.catalog,self.mine])
        value._sub_back()
        self.assertEqual(value._sub_records.call_count,4)
        value._sub_click.assert_called_once()
        value.tasker.post_task.assert_not_called()

    def test_score_keeps_system_back_but_still_waits_for_profile(self):
        value=self.worker([self.score,[],self.profile]);value._sub_back()
        value.tasker.post_task.assert_called_once_with('返回')
        self.assertEqual(value._sub_records.call_count,3)
        value._sub_click.assert_not_called()

    def test_unrecognized_home_loading_is_not_treated_as_return_completion(self):
        partial=[dict(text='推荐',box=[20,124,58,32])]
        value=self.worker([self.catalog,partial,self.profile]);value._sub_back()
        self.assertEqual(value._sub_records.call_count,3)

    def test_unchanged_list_times_out_without_repeated_back_inputs(self):
        value=self.worker([]);value._sub_records=Mock(return_value=self.mine)
        with patch('subscription_tasks.time.monotonic',side_effect=[0,0,13]):
            with self.assertRaisesRegex(TaskIncomplete,'订阅页面未确认'):value._sub_back()
        value._sub_click.assert_called_once()
        value.tasker.post_task.assert_not_called()

    def test_unknown_or_wrong_pages_are_not_sent_a_back_action(self):
        for rows in ([],self.profile,[dict(text='单选题',box=[30,190,100,35])]):
            with self.subTest(rows=rows):
                value=self.worker([rows])
                with self.assertRaisesRegex(TaskIncomplete,'未确认可导航页面'):value._sub_back()
                value._sub_click.assert_not_called();value.tasker.post_task.assert_not_called()

    def test_owned_menu_must_be_closed_before_navigation(self):
        menu=self.mine+[dict(text='取消订阅',box=[280,1100,170,35])]
        value=self.worker([menu])
        with self.assertRaisesRegex(TaskIncomplete,'未确认可导航页面'):value._sub_back()
        value._sub_click.assert_not_called();value.tasker.post_task.assert_not_called()

    def test_body_text_and_missing_add_button_cannot_authorize_header_navigation(self):
        body=[dict(text='我的订阅',box=[105,550,160,35]),dict(text='添加',box=[642,650,57,34])]
        misplaced=[dict(text='我的订阅',box=[307,53,103,34]),dict(text='添加',box=[120,53,57,34])]
        for rows in (body,misplaced,self.mine[:-1]):
            with self.subTest(rows=rows):self.assertIsNone(subscription_scene(rows))

    def test_verification_overrides_the_background_subscription_header(self):
        rows=self.mine+[dict(text='访问验证',box=[280,134,160,35])]
        self.assertIsNone(subscription_scene(rows))

    def test_catalog_to_mine_follows_actual_back_stack_without_a_second_input(self):
        value=self.worker([self.catalog,self.catalog,[],self.mine,self.mine])
        result=value._sub_mine()
        self.assertEqual(result,self.mine)
        value._sub_click.assert_called_once()
        value.tasker.post_task.assert_not_called()

    def test_optimistic_state_keeps_observing_until_delayed_owner_wait_returns(self):
        value=self.worker([]);clock=[0.];observed=[]
        value._paused_seconds=0
        def records():
            observed.append(clock[0])
            if len(observed)==3:
                # _sub_records handles the real owner wait, returning only
                # after the CAPTCHA has left; there is no automated input.
                clock[0]+=60;value._paused_seconds+=60
            return self.mine
        value._sub_records=Mock(side_effect=records)
        value._sleep.side_effect=lambda seconds:clock.__setitem__(0,clock[0]+1)
        with patch('subscription_tasks.time.monotonic',side_effect=lambda:clock[0]):
            self.assertEqual(value._sub_wait(lambda rows:True,settle_seconds=4),self.mine)
        self.assertEqual(clock[0],66)
        value._sub_click.assert_not_called();value.tasker.post_task.assert_not_called()

    def test_failed_optimistic_state_resets_the_stability_window(self):
        value=self.worker([]);clock=[0.];calls=[0]
        def records():
            calls[0]+=1
            return [] if calls[0]==3 else self.mine
        value._sub_records=Mock(side_effect=records)
        value._sleep.side_effect=lambda seconds:clock.__setitem__(0,clock[0]+1)
        with patch('subscription_tasks.time.monotonic',side_effect=lambda:clock[0]):
            value._sub_wait(bool,settle_seconds=4)
        self.assertEqual(clock[0],7)
        value._sub_click.assert_not_called()

    def test_unsettled_action_times_out_and_never_returns_transient_success(self):
        value=self.worker([]);clock=[0.];calls=[0]
        def records():
            calls[0]+=1
            return self.mine if calls[0]%2 else []
        value._sub_records=Mock(side_effect=records)
        value._sleep.side_effect=lambda seconds:clock.__setitem__(0,clock[0]+1)
        with patch('subscription_tasks.time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(TaskIncomplete):value._sub_wait(bool,timeout=6,settle_seconds=4)
        value._sub_click.assert_not_called();value.tasker.post_task.assert_not_called()
