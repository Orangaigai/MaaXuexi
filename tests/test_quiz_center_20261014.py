import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_optimization import worker
from four_player import quiz_center_today_mode
from task_support import TaskIncomplete, TaskSkipped
import device_support


RECORDS = json.loads((Path(__file__).parent/'fixtures/quiz-center-20261014/observed.json').read_text('utf-8'))['records']


class QuizCenterTests(unittest.TestCase):
    def test_actual_today_banner_identifies_double_player_not_other_visible_cards(self):
        self.assertEqual(quiz_center_today_mode(RECORDS), '双人对战')

    def test_four_player_banner_is_bound_to_its_own_lower_card(self):
        records = copy.deepcopy(RECORDS)
        marker = next(row for row in records if row['text'] == '今日趣味答题')
        marker['box'][0] = 430
        self.assertEqual(quiz_center_today_mode(records), '四人赛')

    def test_missing_duplicate_or_non_center_banner_is_not_guessed(self):
        marker = next(row for row in RECORDS if row['text'] == '今日趣味答题')
        for records in ([row for row in RECORDS if row is not marker], RECORDS+[marker],
                        [marker]+[row for row in RECORDS if row['text'] == '双人对战']):
            self.assertIsNone(quiz_center_today_mode(records))

    def test_today_competition_opens_today_card_without_matching_or_home_reset(self):
        value = worker()
        value._reset_home = Mock()
        value._sleep = Mock()
        room=[dict(text='双人对战',box=[290,58,140,40])]
        value._fun_records = Mock(side_effect=[RECORDS,RECORDS,room])
        value._fun_click=Mock(return_value=True)
        self.assertEqual(value._open_fun_challenge(),room)
        value._fun_click.assert_called_once_with(RECORDS,[r'今日趣味答题'])
        value.tasker.post_task.assert_not_called()
        value._reset_home.assert_not_called()

    def test_duplicate_today_markers_do_not_start_a_competitive_match(self):
        value = worker()
        value._sleep = Mock()
        marker=next(row for row in RECORDS if row['text']=='今日趣味答题')
        value._fun_records = Mock(return_value=RECORDS+[marker])
        with self.assertRaisesRegex(TaskIncomplete, '计分模式入口'):
            value._open_fun_challenge()
        value.tasker.controller.post_click.assert_not_called()

    def test_verification_after_today_entry_stops_instead_of_starting_a_match(self):
        value = worker()
        value._sleep = Mock()
        value._screen_records = Mock(side_effect=[RECORDS,RECORDS,[dict(text='访问验证',box=[200,300,200,40])]])
        value.pause = Mock(side_effect=TaskIncomplete('本人验证'))
        with self.assertRaisesRegex(TaskIncomplete, '本人验证'):
            value._open_fun_challenge()
        value.tasker.controller.post_click.assert_called_once()


class WindowDisplayTests(unittest.TestCase):
    def test_display_timeout_only_is_not_an_android_connection_failure(self):
        self.check_show_failure(True, True)

    def test_display_command_error_with_online_adb_still_connects(self):
        self.check_show_failure(False, True)

    def test_lost_adb_after_display_timeout_still_fails(self):
        self.check_show_failure(True, False)

    def check_show_failure(self, timeout, connected):
        config = json.dumps(dict(mumu_cli='mumu-cli.exe', adb_path='adb.exe', vm_index=0))
        def command(args, **kwargs):
            if 'info' in args:
                return SimpleNamespace(returncode=0,stdout=json.dumps(dict(is_process_started=True,adb_host_ip='127.0.0.1',adb_port=16384)))
            if 'getprop' in args:
                return SimpleNamespace(returncode=0,stdout='1')
            if 'show_window' in args:
                if timeout:
                    raise subprocess.TimeoutExpired(args, kwargs['timeout'])
                return SimpleNamespace(returncode=1,stdout='')
            return SimpleNamespace(returncode=0,stdout='')
        progress = Mock()
        with patch.object(Path,'read_text',return_value=config), patch.object(Path,'is_file',return_value=True), \
             patch.object(device_support,'command',side_effect=command), \
             patch.object(device_support,'online',side_effect=[True,connected]):
            if connected:
                self.assertEqual(device_support.prepare_emulator(progress,Mock()), ('adb.exe','127.0.0.1:16384'))
                self.assertIn('仍在线', progress.call_args.args[0])
            else:
                with self.assertRaisesRegex(TaskIncomplete, '连接已中断'):
                    device_support.prepare_emulator(progress,Mock())


if __name__ == '__main__':
    unittest.main()
