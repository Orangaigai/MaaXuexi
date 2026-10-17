import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import daily_flow
import device_support
import utils
from task_support import HumanRequired,TaskIncomplete,TaskCancelled
from test_optimization import worker

FIXTURES=Path(__file__).parent/'fixtures/daily-flow-20261015'
def fixture(name): return json.loads((FIXTURES/(name+'.json')).read_text('utf-8'))
PROFILE=[dict(text='我要答题',box=[300,460,140,40]),dict(text='学习积分',box=[100,460,140,40])]
CENTER=[dict(text='答题练习',box=[35,150,180,40]),dict(text='答题竞赛',box=[35,600,180,40]),dict(text='每日答题',box=[80,420,140,40])]
NETWORK=[dict(text='网络开小差',box=[240,490,240,40]),dict(text='重试',box=[310,650,100,40])]


class DailyFlowTests(unittest.TestCase):
    def setUp(self):
        # Old failure fixtures represent the end of the scheduled owner wait.
        wait = patch('utils.OWNER_WAIT_SECONDS', 0)
        wait.start(); self.addCleanup(wait.stop)
        notify = patch('utils.plyer.notification.notify')
        notify.start(); self.addCleanup(notify.stop)

    def make_worker(self,frames):
        value=worker()
        value._sleep=Mock(side_effect=lambda seconds:setattr(self,'clock',self.clock+seconds))
        value._screen_records=Mock(side_effect=frames)
        value.tasker.controller.cached_image=None
        self.clock=0
        patcher=patch.object(daily_flow.time,'monotonic',side_effect=lambda:self.clock)
        patcher.start();self.addCleanup(patcher.stop)
        return value

    def test_real_scrolled_fifth_needs_pending_record_but_not_hidden_header(self):
        records=fixture('completion')
        self.assertIsNone(daily_flow.completion_box(records,pending=False))
        self.assertEqual(daily_flow.completion_box(records,pending=True),[586,61,62,32])
        self.assertEqual(daily_flow.daily_scene(records,pending=True),'completion')
        self.assertFalse(daily_flow.verification_visible(records))

    def test_real_verification_overrides_a_visible_finish_button(self):
        records=fixture('verification')+fixture('completion')
        self.assertTrue(daily_flow.verification_visible(records))
        self.assertEqual(daily_flow.daily_scene(records,pending=True),'verification')
        self.assertIsNone(daily_flow.completion_box(records,pending=True))

    def test_fifth_transition_retains_position_when_the_header_is_scrolled_away(self):
        value=self.make_worker([fixture('completion'),fixture('completion')])
        value.tasker.post_task.return_value.wait.return_value.get.return_value=SimpleNamespace(status=SimpleNamespace(succeeded=False,failed=True))
        self.assertTrue(value._check_quiz_transition(4))
        self.assertTrue(value._daily_completion_expected)
        value.tasker.controller.post_click.assert_not_called()

    def test_words_about_security_in_a_question_are_not_a_verification_page(self):
        records=fixture('completion')+[dict(text='请勿向陌生人提供安全验证信息',box=[70,300,540,40])]
        self.assertFalse(daily_flow.verification_visible(records))

    def test_unknown_finish_button_is_not_clicked_with_only_saved_progress(self):
        self.assertIsNone(daily_flow.completion_box([dict(text='完成',box=[590,60,80,40])],pending=True))

    def test_finish_then_result_return_uses_actual_cta_without_new_quiz(self):
        value=self.make_worker([fixture('completion'),fixture('completion'),fixture('result'),PROFILE])
        value._finish_quiz(True)
        self.assertEqual([call.args for call in value.tasker.controller.post_click.call_args_list],[(617,77),(210,820)])
        value.tasker.post_task.assert_not_called()
        self.assertEqual(value._daily_finish_metrics['complete_clicks'],1)
        self.assertEqual(value._daily_finish_metrics['result_back_clicks'],1)

    def test_completed_result_is_returned_without_reanswering_or_reclicking_finish(self):
        value=self.make_worker([fixture('result'),PROFILE])
        value._finish_quiz(True)
        value.tasker.controller.post_click.assert_called_once_with(210,820)
        self.assertEqual(value._daily_finish_metrics['complete_clicks'],0)

    def test_unattended_real_verification_stops_with_no_click(self):
        value=self.make_worker([fixture('verification')])
        value.unattended=True
        with self.assertRaises(HumanRequired): value._finish_quiz(True)
        value.tasker.controller.post_click.assert_not_called()
        value.tasker.post_task.assert_not_called()

    def test_verification_then_owner_resume_can_click_finish_and_return(self):
        value=self.make_worker([fixture('verification'),fixture('completion'),fixture('completion'),fixture('result'),PROFILE])
        value.pause=Mock()
        value._finish_quiz(True)
        self.assertIn('resume_when',value.pause.call_args.kwargs)
        self.assertEqual(value._daily_finish_metrics['verification_pauses'],1)
        self.assertEqual(value._daily_finish_metrics['complete_clicks'],1)

    def test_owner_resume_requires_two_positive_frames_and_never_clicks_verification(self):
        value=self.make_worker([fixture('verification'),[],fixture('result'),fixture('result')])
        self.assertFalse(value._daily_resume_ready())
        self.assertFalse(value._daily_resume_ready())
        self.assertFalse(value._daily_resume_ready())
        self.assertTrue(value._daily_resume_ready())
        value.tasker.controller.post_click.assert_not_called()

    def test_acceptance_verification_to_fifth_resumes_and_finishes_without_continue(self):
        verification=fixture('verification')
        completion=fixture('completion')
        # Real pause polling, then the same production Finish and return handler.
        value=self.make_worker([verification,verification,completion,completion,
                                completion,completion,fixture('result'),PROFILE])
        value.unattended=False
        value._daily_acceptance_active=True
        value.run_report=None
        def sleep(seconds):
            self.clock+=seconds
            if value._screen_records.call_count<=4:
                value.tasker.controller.post_click.assert_not_called()
        with patch('utils.time.sleep',side_effect=sleep),patch('utils.plyer.notification.notify'):
            value._finish_quiz(True)
        self.assertFalse(value.pause_flag)
        self.assertEqual(value._daily_finish_metrics['verification_pauses'],1)
        self.assertEqual(value._daily_finish_metrics['complete_clicks'],1)
        self.assertEqual(value._daily_finish_metrics['result_back_clicks'],1)
        self.assertTrue(value._daily_finish_metrics['left_completion_page'])
        self.assertEqual([call.args for call in value.tasker.controller.post_click.call_args_list],
                         [(617,77),(210,820)])

    def test_completion_stops_after_at_most_two_delayed_finish_taps(self):
        value=self.make_worker([])
        value._screen_records=Mock(return_value=fixture('completion'))
        times=[]
        value.tasker.controller.post_click.side_effect=lambda *args:times.append(self.clock) or Mock()
        value.tasker.controller.post_swipe.side_effect=lambda *args:times.append(self.clock) or Mock()
        with self.assertRaisesRegex(TaskIncomplete,'完成2次'): value._finish_quiz(True)
        self.assertEqual(value.tasker.controller.post_click.call_count,1)
        self.assertEqual(value.tasker.controller.post_swipe.call_count,1)
        self.assertGreaterEqual(times[1]-times[0],6)

    def test_real_post_verification_fifth_is_completion_despite_ocr_515(self):
        records=json.loads((Path(__file__).parent/'fixtures/daily-flow-20261016/completion-after-verification.json').read_text('utf-8'))
        self.assertEqual(daily_flow.daily_scene(records,pending=True),'completion')
        self.assertEqual(daily_flow.completion_box(records,pending=True),[585,60,63,33])
        self.assertFalse(daily_flow.verification_visible(records))

    def test_missed_first_finish_tap_can_complete_same_round_on_second_tap(self):
        value=self.make_worker([])
        completion=json.loads((Path(__file__).parent/'fixtures/daily-flow-20261016/completion-after-verification.json').read_text('utf-8'))
        def records():
            clicks=value.tasker.controller.post_click.call_count+value.tasker.controller.post_swipe.call_count
            return completion if clicks<2 else (fixture('result') if clicks==2 else PROFILE)
        value._screen_records=Mock(side_effect=records)
        times=[]
        value.tasker.controller.post_click.side_effect=lambda *args:times.append(self.clock) or Mock()
        value.tasker.controller.post_swipe.side_effect=lambda *args:times.append(self.clock) or Mock()
        value._finish_quiz(True)
        self.assertEqual(value._daily_finish_metrics['complete_clicks'],2)
        self.assertGreaterEqual(times[1]-times[0],6)
        self.assertEqual(value._daily_finish_metrics['result_back_clicks'],1)
        value.tasker.post_task.assert_not_called()

    def test_verification_appearing_before_second_finish_tap_prevents_it(self):
        value=self.make_worker([])
        def records():
            clicks=value.tasker.controller.post_click.call_count
            return fixture('verification') if clicks==1 and self.clock>=6 else fixture('completion')
        value._screen_records=Mock(side_effect=records)
        value.unattended=True
        with self.assertRaises(HumanRequired):value._finish_quiz(True)
        self.assertEqual(value.tasker.controller.post_click.call_count,1)
        value.tasker.controller.post_swipe.assert_not_called()

    def test_late_verification_before_stationary_press_never_sends_input(self):
        value=self.make_worker([fixture('verification')])
        value._daily_completion_expected=True
        self.assertFalse(value._completion_button('Press',records=fixture('completion')))
        value.tasker.controller.post_click.assert_not_called()
        value.tasker.controller.post_swipe.assert_not_called()

    def test_failed_first_finish_input_stops_without_repeating_commands(self):
        value=self.make_worker([fixture('completion'),fixture('completion')])
        value.tasker.controller.post_click.return_value.wait.return_value.succeeded=False
        with self.assertRaisesRegex(TaskIncomplete,'触控命令失败'):
            value._finish_quiz(True)
        value.tasker.controller.post_click.assert_called_once()
        value.tasker.controller.post_swipe.assert_not_called()
        self.assertEqual(value._daily_finish_metrics['complete_clicks'],0)

    def test_failed_recovery_press_stops_without_repeating_commands(self):
        value=self.make_worker([])
        value._screen_records=Mock(return_value=fixture('completion'))
        value.tasker.controller.post_swipe.return_value.wait.return_value.succeeded=False
        with self.assertRaisesRegex(TaskIncomplete,'触控命令失败'):
            value._finish_quiz(True)
        value.tasker.controller.post_click.assert_called_once()
        value.tasker.controller.post_swipe.assert_called_once_with(617,77,617,77,120)
        self.assertEqual(value._daily_finish_metrics['complete_clicks'],1)

    def test_finish_then_verification_then_same_fifth_can_recover_once(self):
        value=self.make_worker([])
        value.pause=Mock()
        def records():
            clicks=value.tasker.controller.post_click.call_count+value.tasker.controller.post_swipe.call_count
            if clicks==0:return fixture('completion')
            if clicks==1:return fixture('completion') if value.pause.called else fixture('verification')
            return fixture('result') if clicks==2 else PROFILE
        value._screen_records=Mock(side_effect=records)
        times=[]
        value.tasker.controller.post_click.side_effect=lambda *args:times.append(self.clock) or Mock()
        value.tasker.controller.post_swipe.side_effect=lambda *args:times.append(self.clock) or Mock()
        value._finish_quiz(True)
        self.assertEqual(value._daily_finish_metrics['verification_pauses'],1)
        self.assertEqual(value._daily_finish_metrics['complete_clicks'],2)
        self.assertEqual(value._daily_finish_metrics['result_back_clicks'],1)
        self.assertGreaterEqual(times[1]-times[0],6)

    def test_daily_network_error_retries_once_then_preserves_the_round(self):
        value=self.make_worker([])
        value._screen_records=Mock(return_value=NETWORK)
        with self.assertRaisesRegex(HumanRequired,'重试1次'): value._finish_quiz(True)
        value.tasker.controller.post_click.assert_called_once_with(360,670)

    def test_verification_replaced_by_network_error_resumes_only_for_bounded_retry(self):
        value=self.make_worker([fixture('verification'),NETWORK,NETWORK])
        self.assertFalse(value._daily_resume_ready())
        self.assertFalse(value._daily_resume_ready())
        self.assertTrue(value._daily_resume_ready())
        value.tasker.controller.post_click.assert_not_called()

    def test_network_error_after_verification_retries_same_submission_then_returns(self):
        value=self.make_worker([fixture('verification'),NETWORK,fixture('result'),PROFILE])
        value.pause=Mock()
        value._finish_quiz(True)
        self.assertEqual(value._daily_finish_metrics['network_retries'],1)
        self.assertEqual(value._daily_finish_metrics['complete_clicks'],0)
        self.assertEqual([call.args for call in value.tasker.controller.post_click.call_args_list],[(360,670),(210,820)])

    def test_late_verification_before_finish_click_prevents_that_click(self):
        value=self.make_worker([fixture('completion'),fixture('verification'),fixture('verification')])
        value.unattended=True
        with self.assertRaises(HumanRequired): value._finish_quiz(True)
        value.tasker.controller.post_click.assert_not_called()

    def test_unknown_scene_stops_without_back_or_restarting_the_app(self):
        value=self.make_worker([])
        value._screen_records=Mock(return_value=[dict(text='未知页面',box=[50,400,500,30])])
        with self.assertRaisesRegex(TaskIncomplete,'长时间未确认'): value._finish_quiz(True)
        value.tasker.controller.post_click.assert_not_called()
        value.tasker.post_task.assert_not_called()

    def test_foreground_loss_does_not_send_result_back_to_another_app(self):
        value=self.make_worker([fixture('result')])
        value._app_in_foreground.return_value=False
        with self.assertRaisesRegex(TaskIncomplete,'不在前台'): value._finish_quiz(True)
        value.tasker.controller.post_click.assert_not_called()

    def test_daily_entry_reuses_center_without_restarting_or_opening_profile(self):
        value=self.make_worker([CENTER,[dict(text='单选题',box=[70,200,140,40])]])
        value._reset_home=Mock()
        value._captcha_present=Mock(return_value=False)
        value._open_daily_quiz()
        value._reset_home.assert_not_called()
        self.assertEqual([call.args[0] for call in value.tasker.post_task.call_args_list],['本机每日答题卡片'])

    def test_daily_entry_reuses_profile_without_reopening_app(self):
        value=self.make_worker([PROFILE,[dict(text='单选题',box=[70,200,140,40])]])
        value._reset_home=Mock()
        value._captcha_present=Mock(return_value=False)
        value._open_daily_quiz()
        value._reset_home.assert_not_called()
        self.assertEqual([call.args[0] for call in value.tasker.post_task.call_args_list],['本机答题中心入口','本机每日答题卡片'])

    def test_pause_can_observe_owner_finished_page_without_a_continue_click(self):
        value=self.make_worker([])
        value.unattended=False
        value.run_report=None
        ready=Mock(side_effect=[False,True])
        with patch('utils.time.sleep',side_effect=lambda seconds:setattr(self,'clock',self.clock+seconds)),patch('utils.plyer.notification.notify'):
            value.pause('本人验证',resume_when=ready)
        self.assertEqual(ready.call_count,2)
        self.assertFalse(value.pause_flag)

    def test_async_mumu_launch_timeout_waits_for_actual_boot_without_second_launch(self):
        calls=[]
        config=json.dumps(dict(mumu_cli='mumu-cli.exe',adb_path='adb.exe',vm_index=0))
        def command(args,**kwargs):
            calls.append(args)
            if 'launch' in args: raise subprocess.TimeoutExpired(args,8)
            if 'info' in args:
                return SimpleNamespace(returncode=0,stdout=json.dumps(dict(is_process_started=False,adb_host_ip='127.0.0.1',adb_port=16384)))
            if 'getprop' in args:return SimpleNamespace(returncode=0,stdout='1')
            return SimpleNamespace(returncode=0,stdout='')
        with patch.object(Path,'read_text',return_value=config),patch.object(Path,'is_file',return_value=True), \
             patch.object(device_support,'command',side_effect=command),patch.object(device_support,'online',return_value=True):
            self.assertEqual(device_support.prepare_emulator(Mock(),Mock())[1],'127.0.0.1:16384')
        self.assertEqual(sum('launch' in args for args in calls),1)


if __name__=='__main__':unittest.main()
