"""Launch from the observed desktop without touching ads or existing app pages."""
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_optimization import worker
import task_support
import webui


class LauncherAdTests(unittest.TestCase):
    def setUp(self):
        self.value = worker()
        self.value._adb_command = ('adb.exe', '-s', '127.0.0.1:16384')
        self.value.tasker.controller.post_start_app.return_value.wait.return_value.succeeded = True

    def activity(self, package):
        return SimpleNamespace(stdout=f'topResumedActivity=ActivityRecord{{ab u0 {package}/.Main t2}}')

    def test_known_desktop_opens_app_once_without_ad_click_or_force_stop(self):
        for package in ('app.lawnchair', 'com.android.launcher3'):
            with self.subTest(package=package), patch('home_navigation.subprocess.run', return_value=self.activity(package)):
                self.value.tasker.controller.reset_mock()
                self.assertTrue(self.value._open_from_android_launcher())
                self.value.tasker.controller.post_start_app.assert_called_once_with('cn.xuexi.android')
                self.value.tasker.controller.post_stop_app.assert_not_called()
                self.value.tasker.controller.post_click.assert_not_called()

    def test_existing_target_permissions_browser_or_unknown_app_is_preserved(self):
        for package in ('cn.xuexi.android', 'com.android.permissioncontroller', 'com.android.chrome', 'unknown.app'):
            with self.subTest(package=package), patch('home_navigation.subprocess.run', return_value=self.activity(package)):
                self.assertFalse(self.value._open_from_android_launcher())
        self.value.tasker.controller.post_start_app.assert_not_called()
        self.value.tasker.controller.post_stop_app.assert_not_called()
        self.value.tasker.controller.post_click.assert_not_called()

    def test_historical_desktop_record_does_not_override_current_target_app(self):
        result = self.activity('cn.xuexi.android')
        result.stdout += '\n Hist #0: ActivityRecord{cd u0 app.lawnchair/.LawnchairLauncher t1}'
        with patch('home_navigation.subprocess.run', return_value=result):
            self.assertFalse(self.value._open_from_android_launcher())
        self.value.tasker.controller.post_start_app.assert_not_called()

    def test_unknown_foreground_from_adb_timeout_preserves_screen(self):
        with patch('home_navigation.subprocess.run', side_effect=subprocess.TimeoutExpired('adb', 8)):
            self.assertFalse(self.value._open_from_android_launcher())
        self.value.tasker.controller.post_start_app.assert_not_called()
        self.value.tasker.controller.post_click.assert_not_called()

    def test_desktop_launch_failure_is_reported_without_repeated_start_or_ad_click(self):
        self.value.tasker.controller.post_start_app.return_value.wait.return_value.succeeded = False
        with patch('home_navigation.subprocess.run', return_value=self.activity('app.lawnchair')):
            with self.assertRaisesRegex(task_support.TaskIncomplete, '启动学习强国失败'):
                self.value._open_from_android_launcher()
        self.value.tasker.controller.post_start_app.assert_called_once()
        self.value.tasker.controller.post_click.assert_not_called()

    def test_stopped_idle_worker_can_prepare_the_app_without_starting_tasks(self):
        self.value.stop_flag = True
        with patch('home_navigation.subprocess.run', return_value=self.activity('app.lawnchair')):
            self.assertTrue(self.value._open_from_android_launcher())
        self.assertTrue(self.value.stop_flag)
        self.value.tasker.post_task.assert_not_called()

    def test_home_reset_reports_stop_failure_without_claiming_app_opened(self):
        self.value._launch_records = Mock(return_value=[])
        self.value._wait_home_ready = Mock()
        self.value.tasker.controller.post_stop_app.return_value.wait.return_value.succeeded = False
        with self.assertRaisesRegex(task_support.TaskIncomplete, '停止命令未成功'):
            self.value._go_home()
        self.value.tasker.controller.post_start_app.assert_not_called()
        self.value._wait_home_ready.assert_not_called()

    def test_home_reset_reports_start_failure_without_waiting_two_minutes(self):
        self.value._launch_records = Mock(return_value=[])
        self.value._wait_home_ready = Mock()
        self.value.tasker.controller.post_stop_app.return_value.wait.return_value.succeeded = True
        self.value.tasker.controller.post_start_app.return_value.wait.return_value.succeeded = False
        with self.assertRaisesRegex(task_support.TaskIncomplete, '启动命令未成功'):
            self.value._go_home()
        self.value.tasker.controller.post_start_app.assert_called_once()
        self.value._wait_home_ready.assert_not_called()

    def test_connect_endpoint_returns_actionable_launch_failure(self):
        state = webui.AppState()
        state.worker = self.value
        self.value.connect_device = Mock(side_effect=task_support.TaskIncomplete('桌面启动失败'))
        with patch.object(webui, 'app_state', state):
            with self.assertRaises(webui.HTTPException) as caught:
                webui.connect_device(SimpleNamespace())
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, '桌面启动失败')
