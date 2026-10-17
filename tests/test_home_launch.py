"""Real splash fixtures and bounded, verification-aware launch state tests."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
import device_support
import home_navigation
import task_support
import utils
from test_optimization import worker


class HomeLaunchTests(unittest.TestCase):
    def setUp(self):
        folder = Path(__file__).parent / 'fixtures/launch'
        self.splash = json.loads((folder / '08.json').read_text('utf-8'))
        self.home = json.loads((folder / '12.json').read_text('utf-8'))
        self.value = worker()
        self.value.tasker.controller.cached_image = np.zeros((1280, 720, 3), np.uint8)
        self.value._screen_records = Mock(return_value=self.home)
        self.clock = 0
        self.value._sleep = Mock(side_effect=self.advance)
        self.value._save_launch_failure = Mock()
        patcher = patch('home_navigation.time.monotonic', side_effect=lambda: self.clock)
        patcher.start(); self.addCleanup(patcher.stop)
        # These failure fixtures exercise the outcome after the owner wait expires.
        wait = patch('utils.OWNER_WAIT_SECONDS', 0)
        wait.start(); self.addCleanup(wait.stop)
        notify = patch('utils.plyer.notification.notify')
        notify.start(); self.addCleanup(notify.stop)

    def advance(self, seconds):
        self.clock += seconds

    def test_real_splash_cannot_be_mistaken_for_home(self):
        self.assertFalse(home_navigation.home_ready(self.splash))
        self.assertTrue(home_navigation.home_ready(self.home))

    def test_home_must_be_confirmed_in_two_consecutive_frames(self):
        self.value._screen_records.side_effect = [self.home, self.splash, self.home, self.home]
        self.value._wait_home_ready()
        self.assertEqual(self.value._screen_records.call_count, 4)
        self.value.tasker.controller.post_click.assert_not_called()

    def test_splash_longer_than_old_thirteen_seconds_is_waited_out(self):
        self.value._screen_records.side_effect = [self.splash] * 25 + [self.home, self.home]
        self.value._wait_home_ready()
        self.assertGreater(self.value._home_launch_metrics['wait_seconds'], 13)
        self.value._save_launch_failure.assert_not_called()

    def test_unknown_popup_times_out_without_blind_clicking_or_restarts(self):
        self.value._screen_records.return_value = self.splash
        with self.assertRaises(task_support.TaskIncomplete):
            self.value._wait_home_ready(timeout=5)
        self.value._save_launch_failure.assert_called_once()
        self.value.tasker.controller.post_click.assert_not_called()
        self.value.tasker.controller.post_start_app.assert_not_called()

    def test_existing_home_does_not_restart_the_app(self):
        self.value._reset_home()
        self.value.tasker.controller.post_stop_app.assert_not_called()
        self.value.tasker.controller.post_start_app.assert_not_called()

    def test_another_normal_page_restarts_once_then_waits_for_stable_home(self):
        self.value._screen_records.side_effect = [[], self.splash, self.home, self.home]
        self.value._reset_home()
        self.value.tasker.controller.post_stop_app.assert_called_once()
        self.value.tasker.controller.post_start_app.assert_called_once()

    def test_verification_present_before_launch_prevents_restart(self):
        del self.value._guard_verification
        self.value.unattended = True
        self.value._captcha_present = Mock(return_value=True)
        with self.assertRaises(task_support.HumanRequired):
            self.value._reset_home()
        self.value.tasker.controller.post_stop_app.assert_not_called()
        self.value.tasker.controller.post_start_app.assert_not_called()

    def test_verification_during_launch_prevents_ad_skip_or_other_input(self):
        del self.value._guard_verification
        self.value.unattended = True
        self.value._screen_records.return_value = [dict(text='访问验证', box=[200, 400, 150, 40]),
                                                  dict(text='跳过', box=[600, 60, 60, 40])]
        with self.assertRaises(task_support.HumanRequired):
            self.value._wait_home_ready()
        self.value.tasker.controller.post_click.assert_not_called()

    def test_explicit_top_right_ad_skip_is_clicked_only_once(self):
        skip = [dict(text='跳过3秒', box=[600, 60, 60, 40])]
        self.value._screen_records.side_effect = [skip, skip, self.home, self.home]
        self.value.tasker.controller.post_click.return_value.wait.return_value.succeeded = True
        self.value._wait_home_ready()
        self.value.tasker.controller.post_click.assert_called_once_with(630, 80)

    def test_login_prompt_is_kept_for_owner(self):
        self.value.unattended = True
        self.value._screen_records.return_value = [dict(text='请输入手机号', box=[200, 400, 300, 40])]
        with self.assertRaises(task_support.HumanRequired):
            self.value._reset_home()
        self.value.tasker.controller.post_stop_app.assert_not_called()

    def test_wrong_resolution_stops_before_clicking(self):
        self.value.tasker.controller.cached_image = np.zeros((720, 1280, 3), np.uint8)
        with self.assertRaises(task_support.TaskIncomplete):
            self.value._wait_home_ready()
        self.value.tasker.controller.post_click.assert_not_called()

    def test_cancel_during_splash_stops_promptly(self):
        self.value._screen_records.return_value = self.splash
        self.value._sleep.side_effect = lambda seconds: setattr(self.value, 'stop_flag', True)
        with self.assertRaises(task_support.TaskCancelled):
            self.value._wait_home_ready()

    def notification(self):
        return [dict(text='要允许“学习强国”向您发送通知', box=[150,545,415,34]),
                dict(text='允许', box=[332,695,49,30]),
                dict(text='不允许', box=[323,784,71,31])]

    def permission_activity(self, system=True):
        package = 'com.android.permissioncontroller/.permission.ui.GrantPermissionsActivity' if system else 'cn.xuexi.android/.MainActivity'
        return SimpleNamespace(stdout='topResumedActivity=ActivityRecord{abc u0 ' + package + ' t1}')

    def test_real_notification_dialog_is_denied_then_background_detail_resets(self):
        self.value._adb_command = ('adb.exe', '-s', '127.0.0.1:16384')
        self.value._app_in_foreground.return_value = False
        self.value._screen_records.side_effect = [self.notification(), [], self.splash, self.home, self.home]
        self.value.tasker.controller.post_click.return_value.wait.return_value.succeeded = True
        with patch('home_navigation.subprocess.run', return_value=self.permission_activity()):
            self.value._reset_home()
        self.value.tasker.controller.post_click.assert_called_once_with(358, 799)
        self.value.tasker.controller.post_stop_app.assert_called_once()
        self.value.tasker.controller.post_start_app.assert_called_once()

    def test_notification_text_in_app_page_is_not_permission_authority(self):
        self.value._adb_command = ('adb.exe',)
        with patch('home_navigation.subprocess.run', return_value=self.permission_activity(system=False)):
            self.assertFalse(self.value._deny_launch_notification(self.notification()))
        self.value.tasker.controller.post_click.assert_not_called()

    def test_notification_during_launch_is_clicked_at_most_once(self):
        self.value._adb_command = ('adb.exe',)
        self.value._screen_records.side_effect = [self.notification(), self.notification(), self.home, self.home]
        self.value.tasker.controller.post_click.return_value.wait.return_value.succeeded = True
        with patch('home_navigation.subprocess.run', return_value=self.permission_activity()):
            self.value._wait_home_ready()
        self.value.tasker.controller.post_click.assert_called_once()

    def test_other_android_permissions_are_never_changed(self):
        request = self.notification()
        request[0]['text'] = '要允许“学习强国”访问您的位置信息吗？'
        self.assertIsNone(home_navigation.notification_deny(request))

    def test_ambiguous_permission_buttons_are_not_clicked(self):
        request = self.notification()
        request.append(dict(text='不允许', box=[323,900,71,31]))
        self.assertIsNone(home_navigation.notification_deny(request))

    def test_verification_has_priority_over_notification_dismissal(self):
        del self.value._guard_verification
        self.value.unattended = True
        self.value._captcha_present = Mock(return_value=True)
        self.value._screen_records.return_value = self.notification() + [dict(text='访问验证',box=[200,400,200,40])]
        with self.assertRaises(task_support.HumanRequired):
            self.value._reset_home()
        self.value.tasker.controller.post_click.assert_not_called()
        self.value.tasker.controller.post_stop_app.assert_not_called()

    def test_background_detail_is_reset_instead_of_resumed(self):
        self.value._app_in_foreground.return_value = False
        self.value._screen_records.side_effect = [[], self.home, self.home]
        self.value._reset_home()
        self.value.tasker.controller.post_stop_app.assert_called_once()

    def test_observed_music_album_card_is_excluded_from_articles(self):
        self.assertTrue(utils.is_audio_collection('凉山学习平台2026-10-09原创音乐专辑《行云流水》0中国音乐>今晚陪你听>行云流水原创音乐专辑13条音频'))

    def test_news_about_audio_is_still_readable(self):
        self.assertFalse(utils.is_audio_collection('音频技术助力新闻报道，听音乐有哪些好处'))

    def test_observed_recent_player_close_tracks_its_dragged_position(self):
        for y in (476, 1073):
            records = [dict(text='最近学习',box=[25,y,70,26]),
                       dict(text='00:00/04:49',box=[85,y+70,85,20])]
            self.assertEqual(home_navigation.recent_media_close(records), (408,y+65))

    def test_recent_study_text_without_player_timer_does_not_trigger_input(self):
        self.assertIsNone(home_navigation.recent_media_close([dict(text='最近学习',box=[25,476,70,26])]))

    def test_a_timer_elsewhere_does_not_establish_a_floating_player(self):
        self.assertIsNone(home_navigation.recent_media_close([
            dict(text='最近学习',box=[25,476,70,26]),dict(text='00:00/04:49',box=[500,800,100,20])]))

    def test_known_floating_player_is_closed_once_and_disappearance_confirmed(self):
        records = [dict(text='最近学习',box=[25,476,70,26]),dict(text='00:00/04:49',box=[85,546,85,20])]
        self.value._screen_records.side_effect = [records, self.home]
        self.value.tasker.controller.post_click.return_value.wait.return_value.succeeded = True
        self.value._dismiss_recent_media()
        self.value.tasker.controller.post_click.assert_called_once_with(408,541)

    def test_landscape_desktop_does_not_prevent_portrait_app_launch(self):
        self.value.tasker.controller.cached_image = np.zeros((720,1280,3), np.uint8)
        self.value._app_in_foreground.return_value = False
        def started():
            self.value.tasker.controller.cached_image = np.zeros((1280,720,3), np.uint8)
        self.value.tasker.controller.post_start_app.return_value.wait.side_effect = started
        self.value._reset_home()
        self.value.tasker.controller.post_start_app.assert_called_once()
        self.value.tasker.controller.post_click.assert_not_called()

    def test_orientation_transition_is_waited_out_before_home_confirmation(self):
        count = [0]
        def frame():
            count[0] += 1
            shape = (720,1280,3) if count[0] < 3 else (1280,720,3)
            self.value.tasker.controller.cached_image = np.zeros(shape, np.uint8)
            return self.home
        self.value._screen_records.side_effect = frame
        self.value._wait_home_ready()
        self.assertEqual(count[0], 4)
        self.value.tasker.controller.post_click.assert_not_called()

    def test_score_title_without_actual_data_is_not_ready(self):
        self.value._screen_records.return_value = [dict(text='积分规则',box=[50,560,150,40]),
            dict(text='今日已累积--积分',box=[440,560,230,40])]
        self.assertFalse(self.value._wait_score_data(timeout=3))
        self.value.tasker.controller.post_click.assert_not_called()

    def test_delayed_real_score_rows_are_waited_for(self):
        loaded = [dict(text='登录',box=[50,300,90,35]),dict(text='每日上限1分',box=[50,380,150,30]),
                  dict(text='1分/1分',box=[550,430,110,35])]
        self.value._screen_records.side_effect = [[], loaded]
        self.assertTrue(self.value._wait_score_data(timeout=3))
        self.assertEqual(self.value._screen_records.call_count, 2)

    def test_observed_network_error_is_never_treated_as_ready(self):
        self.value._screen_records.return_value = [dict(text='积分规则',box=[50,560,150,40]),
            dict(text='网络开小差，请稍后重试',box=[211,972,298,25])]
        self.assertFalse(self.value._wait_score_data())

    def test_score_network_failure_reopens_before_returning_fresh_scores(self):
        self.value._enter_learning_score = Mock(return_value=True)
        self.value._wait_score_data = Mock(side_effect=[False, True])
        self.value._dismiss_recent_media = Mock()
        scores = {'登录':dict(value=1,maximum=1)}
        self.value._read_score_pages = Mock(return_value=scores)
        self.assertEqual(self.value.read_learning_score(['登录']), scores)
        self.assertEqual(self.value._enter_learning_score.call_count, 2)

    def test_missing_requested_scores_cannot_silently_pass(self):
        self.value._enter_learning_score = Mock(return_value=True)
        self.value._wait_score_data = Mock(return_value=True)
        self.value._dismiss_recent_media = Mock()
        partial = {'登录':dict(value=1,maximum=1)}
        complete = dict(partial, **{'视听学习':dict(value=5,maximum=12)})
        self.value._read_score_pages = Mock(side_effect=[partial, complete])
        self.assertEqual(self.value.read_learning_score(['登录','视听学习']), complete)
        self.assertEqual(self.value._enter_learning_score.call_count, 2)

    def test_persistent_empty_score_page_stops_after_three_attempts(self):
        self.value._enter_learning_score = Mock(return_value=True)
        self.value._wait_score_data = Mock(return_value=False)
        self.value._read_score_pages = Mock()
        with self.assertRaises(task_support.TaskIncomplete):
            self.value.read_learning_score(['登录'])
        self.assertEqual(self.value._enter_learning_score.call_count, 3)
        self.value._read_score_pages.assert_not_called()


class EmulatorReadinessTests(unittest.TestCase):
    def config(self):
        return json.dumps(dict(mumu_cli='mumu-cli.exe', adb_path='adb.exe', vm_index=0))

    def test_adb_ready_is_accepted_even_when_mumu_android_flag_lags(self):
        def command(args, timeout=8):
            if 'info' in args:
                return SimpleNamespace(returncode=0, stdout=json.dumps(dict(is_process_started=True,
                    is_android_started=False, adb_host_ip='127.0.0.1', adb_port=16384)))
            return SimpleNamespace(returncode=0, stdout='1')
        with patch.object(Path, 'read_text', return_value=self.config()), patch.object(Path, 'is_file', return_value=True), \
             patch.object(device_support, 'command', side_effect=command), patch.object(device_support, 'online', return_value=True):
            self.assertEqual(device_support.prepare_emulator(Mock(), Mock())[1], '127.0.0.1:16384')

    def test_cold_boot_after_one_hundred_seconds_is_not_abandoned(self):
        clock = [0]
        def command(args, timeout=8):
            if 'info' in args:
                return SimpleNamespace(returncode=0, stdout=json.dumps(dict(is_process_started=True,
                    is_android_started=clock[0] >= 150, adb_host_ip='127.0.0.1',
                    adb_port=16384 if clock[0] >= 150 else None)))
            return SimpleNamespace(returncode=0, stdout='1')
        with patch.object(Path, 'read_text', return_value=self.config()), patch.object(Path, 'is_file', return_value=True), \
             patch.object(device_support, 'command', side_effect=command), patch.object(device_support, 'online', return_value=True), \
             patch.object(device_support.time, 'monotonic', side_effect=lambda:clock[0]), \
             patch.object(device_support.time, 'sleep', side_effect=lambda seconds:clock.__setitem__(0, clock[0]+seconds)):
            device_support.prepare_emulator(Mock(), Mock())
        self.assertGreaterEqual(clock[0], 150)

    def test_transient_cli_json_failure_is_retried_within_deadline(self):
        count = [0]
        def command(args, timeout=8):
            if 'info' in args:
                count[0] += 1
                text = 'startup in progress' if count[0] == 1 else json.dumps(dict(is_process_started=True,
                    is_android_started=True, adb_host_ip='127.0.0.1', adb_port=16384))
                return SimpleNamespace(returncode=0, stdout=text)
            return SimpleNamespace(returncode=0, stdout='1')
        with patch.object(Path, 'read_text', return_value=self.config()), patch.object(Path, 'is_file', return_value=True), \
             patch.object(device_support, 'command', side_effect=command), patch.object(device_support, 'online', return_value=True), \
             patch.object(device_support.time, 'sleep'):
            device_support.prepare_emulator(Mock(), Mock())
        self.assertEqual(count[0], 2)


if __name__ == '__main__':
    unittest.main()
