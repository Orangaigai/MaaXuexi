"""Display/notification settings must never alter quiz state or credentials."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_optimization import worker
from fastapi.testclient import TestClient
import local_preferences as preferences
import webui
from window_policy import WindowPolicy


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.path=Path(folder.name)/'settings.json'
        value=patch.object(preferences,'PREFERENCES_PATH',self.path)
        value.start();self.addCleanup(value.stop)

    def test_first_use_is_background_with_one_attention_and_notifications(self):
        value=preferences.read_preferences()
        self.assertEqual(value['window_mode'],'background')
        self.assertTrue(value['show_on_attention']);self.assertTrue(value['notifications'])
        self.assertFalse(value['open_console_on_schedule'])
        self.assertFalse(self.path.exists())

    def test_round_trip_and_independent_switches(self):
        expected=dict(window_mode='bottom',show_on_attention=True,notifications=False,open_console_on_schedule=True)
        self.assertEqual(preferences.save_preferences(expected),expected)
        self.assertEqual(preferences.read_preferences(),expected)
        self.assertFalse(self.path.with_suffix('.tmp').exists())

    def test_invalid_saved_file_uses_safe_defaults_without_overwriting(self):
        for data in ('not json','[]','{"notifications":"false"}','{"window_mode":"unknown"}'):
            self.path.write_text(data,encoding='utf-8')
            self.assertEqual(preferences.read_preferences()['window_mode'],'background')
            self.assertEqual(self.path.read_text('utf-8'),data)

    def test_disabled_notifications_do_not_call_os(self):
        preferences.save_preferences(dict(notifications=False))
        notify=Mock()
        self.assertFalse(preferences.desktop_notification(notify,title='fixture'))
        notify.assert_not_called()
        preferences.save_preferences(dict(notifications=True))
        self.assertTrue(preferences.desktop_notification(notify,title='fixture'))
        notify.assert_called_once_with(title='fixture')

    def test_disabled_notification_still_keeps_unfinished_report(self):
        preferences.save_preferences(dict(notifications=False))
        value=worker();value.run_report=Mock()
        value.run_report.snapshot.return_value=dict(status='partial',tasks={'订阅':dict(state='failed')})
        with patch('utils.plyer.notification.notify') as notify:
            value._summarize_run()
        notify.assert_not_called()
        value.run_report.update.assert_any_call(completed_tasks=[],skipped_tasks=[],unfinished_tasks=['订阅'])
        value.run_report.update.assert_any_call(notification_attempted=False,notification_suppressed=True)

    def test_native_window_is_applied_once_during_boot_without_force_show_command(self):
        import device_support
        calls=[];boots=iter(['0','1']);policy=Mock()
        info=dict(is_process_started=True,adb_port=16384,main_wnd='00030840',pid=123)
        def command(args,**kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0,stdout=json.dumps(info) if 'info' in args else next(boots) if 'getprop' in args else '')
        config=json.dumps(dict(mumu_cli='mumu-cli.exe',adb_path='adb.exe',vm_index=0))
        with patch.object(Path,'read_text',return_value=config),patch.object(Path,'is_file',return_value=True), \
             patch.object(device_support,'command',side_effect=command),patch.object(device_support,'online',return_value=True), \
             patch.object(device_support.time,'sleep'):
            self.assertEqual(device_support.prepare_emulator(Mock(),Mock(),window_policy=policy)[1],'127.0.0.1:16384')
        policy.apply.assert_called_once_with(info)
        self.assertFalse(any('show_window' in args for args in calls))

    def test_preferences_endpoint_validates_and_does_not_touch_tasks(self):
        native=Mock();native.apply.return_value=True
        state=SimpleNamespace(lock=threading.RLock(),worker=SimpleNamespace(window_policy=native),
                                          history_import=SimpleNamespace(running=False))
        with patch.object(webui,'app_state',state):
            client=TestClient(webui.app)
            result=client.get('/api/local/preferences').json()
            self.assertNotIn('api_key',result)
            for invalid in ({'window_mode':'bad'},{'notifications':'false'},{'api_key':'must-not-accept'}):
                self.assertEqual(client.post('/api/local/preferences',json=invalid).status_code,422)
            self.assertFalse(self.path.exists())
            result=client.post('/api/local/preferences',json=dict(window_mode='bottom',notifications=False)).json()
            self.assertEqual(result['window_mode'],'bottom');self.assertFalse(result['notifications'])
            native.apply.assert_called_once()
            self.assertEqual(client.get('/local/settings').status_code,200)


class WindowPolicyTests(unittest.TestCase):
    def setUp(self):
        self.native=Mock();self.native.valid.return_value=True
        self.info=dict(main_wnd='00030840',pid=123,is_process_started=True)
        self.options=dict(window_mode='background',show_on_attention=True)
        self.policy=WindowPolicy(self.native)
        patches=[patch('window_policy.emulator_info',return_value=self.info),
                 patch('window_policy.read_preferences',side_effect=lambda:self.options.copy())]
        for value in patches:value.start();self.addCleanup(value.stop)

    def test_background_minimizes_without_show_or_desktop_input(self):
        self.assertTrue(self.policy.apply())
        self.native.show.assert_called_once_with(int('30840',16),7)
        self.native.order.assert_not_called()

    def test_bottom_and_topmost_are_explicit_modes(self):
        for mode,order in [('bottom',1),('topmost',-1)]:
            self.native.reset_mock();self.options['window_mode']=mode
            self.assertTrue(self.policy.apply())
            self.native.show.assert_called_once_with(int('30840',16),4)
            self.native.order.assert_called_once_with(int('30840',16),order)

    def test_normal_never_restores_minimized_window_or_raises_it(self):
        self.options['window_mode']='normal'
        self.policy.apply()
        self.native.release.assert_called_once();self.native.show.assert_not_called()

    def test_attention_is_once_releases_topmost_and_settings_cannot_cover_challenge(self):
        hwnd=int('30840',16)
        self.assertTrue(self.policy.attention());self.assertFalse(self.policy.attention())
        self.assertEqual(self.native.order.call_args_list,[unittest.mock.call(hwnd,-1),unittest.mock.call(hwnd,-2)])
        self.native.reset_mock();self.policy.apply();self.native.show.assert_not_called()
        self.policy.resume();self.native.show.assert_called_once_with(hwnd,7)
        self.native.reset_mock();self.policy.resume();self.native.show.assert_not_called()

    def test_attention_window_switch_is_independent_of_toast_switch(self):
        self.options['show_on_attention']=False
        self.assertFalse(self.policy.attention());self.native.show.assert_not_called()
        self.policy.resume();self.assertFalse(self.policy.waiting)

    def test_reused_or_foreign_handle_is_never_manipulated(self):
        self.native.valid.return_value=False
        self.assertFalse(self.policy.apply());self.assertIn('归属',self.policy.error)
        self.native.show.assert_not_called();self.native.order.assert_not_called()

    def test_shutdown_or_missing_window_never_restarts_emulator(self):
        for info in ({},dict(is_process_started=False),dict(is_process_started=True,main_wnd='0',pid=123)):
            self.assertFalse(self.policy.apply(info));self.native.show.assert_not_called()

    def test_window_error_does_not_escape_into_quiz_logic(self):
        self.native.show.side_effect=OSError('window unavailable')
        self.assertFalse(self.policy.apply());self.assertEqual(self.policy.error,'window unavailable')

    def test_worker_manual_pause_never_raises_window(self):
        value=worker();value.window_policy=Mock();value.stop_flag=True
        from task_support import TaskCancelled
        with self.assertRaises(TaskCancelled):value.pause('manual',manual=True)
        value.window_policy.attention.assert_not_called()

    def test_worker_owner_resume_restores_mode_without_any_browser_click(self):
        value=worker();value.window_policy=Mock();ready=Mock(return_value=True)
        with patch('utils.plyer.notification.notify'):
            value.pause('owner fixture',resume_when=ready)
        value.window_policy.attention.assert_called_once()
        value.window_policy.resume.assert_called_once()
        self.assertFalse(value.pause_flag)
        value.tasker.controller.post_click.assert_not_called()

    def test_no_desktop_focus_or_input_api_in_controller(self):
        source=(Path(__file__).resolve().parents[1]/'backend/window_policy.py').read_text('utf-8')
        for name in ('SetForegroundWindow','AttachThreadInput','SendInput','mouse_event','keybd_event'):
            self.assertNotIn(name,source)


if __name__=='__main__':unittest.main()
