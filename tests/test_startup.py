"""Regressions for a lost ADB connection, cold boot and start error reporting."""
import sys
import subprocess
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import device_support
import task_support
import webui
from test_optimization import worker, job


class StartupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(task_support, "REPORT_PATH", Path(temp.name) / "run.json")
        patcher.start(); self.addCleanup(patcher.stop)

    def test_start_accepts_disconnected_device_and_runs_preparation(self):
        state = webui.AppState(); value = worker(); value.connected = False
        value.task = Mock(); value.request_stop = Mock(); state.worker = value
        with patch.object(webui, "app_state", state):
            result = webui.start(webui.TaskModel(tasklist=["选读文章"]))
            thread = state.child_process
            if thread:
                thread.join(2)
            self.assertEqual(result["status"], "success")
            value.task.assert_called_once_with(["选读文章"], False, False, unattended=False)

    def test_stale_connected_flag_is_not_reported_as_online(self):
        value = worker(); value.connected = True
        state = webui.AppState(); state.worker = value
        with patch.object(webui, "app_state", state), patch("utils.online", return_value=False):
            self.assertFalse(webui.status()["connected"])

    def test_preparation_keeps_mumu_discovery_metadata(self):
        import utils
        value=worker();value.run_report=None
        device=SimpleNamespace(address="127.0.0.1:16384",config={"extras":{"mumu":{"enable":True}}})
        value.connect_device=Mock(return_value=True)
        value.connection_alive=Mock(return_value=True)
        with patch("utils.prepare_emulator",return_value=("adb.exe",device.address)), \
             patch("utils.Toolkit.find_adb_devices",return_value=[device]) as find:
            utils.MaaWorker.prepare_device(value)
        find.assert_called_once_with()
        self.assertIs(value.connect_device.call_args.args[0],device)

    def test_connection_preserves_emulator_screenshot_method_and_extras(self):
        value=worker();value.tasker.bind=Mock(return_value=True)
        device=SimpleNamespace(adb_path="adb.exe",address="127.0.0.1:16384",screencap_methods=64,
                               input_methods=7,config={"extras":{"mumu":{"enable":True,"index":0}}})
        controller=Mock();controller.post_connection.return_value.wait.return_value.succeeded=True
        with patch("utils.AdbController",return_value=controller) as create:
            self.assertTrue(value.connect_device(device))
        self.assertEqual(create.call_args.kwargs["screencap_methods"],64)
        self.assertIs(create.call_args.kwargs["config"],device.config)

    def test_preparation_failure_does_not_leave_all_tasks_pending(self):
        value = worker()
        value.prepare_device.side_effect = task_support.TaskIncomplete("模拟器连接超时")
        value.read_learning_score = Mock()
        with patch("utils.traceback.print_exc"), patch("utils.plyer.notification.notify"):
            value.task(["选读文章", "视听学习"])
        report = value.run_report.snapshot()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "模拟器连接超时")
        self.assertTrue(all(row["state"] == "failed" for row in report["tasks"].values()))
        value.read_learning_score.assert_not_called()

    def config(self):
        return json.dumps(dict(mumu_cli="mumu-cli.exe", adb_path="adb.exe", vm_index=0))

    def test_cold_boot_waits_for_boot_and_rejects_false_connect_success(self):
        seen = []
        def run(args, timeout=8):
            seen.append(args)
            if "info" in args:
                info = dict(is_process_started=len(seen)>1, is_android_started=len(seen)>1,
                            adb_host_ip="127.0.0.1", adb_port=16384)
                return SimpleNamespace(returncode=0, stdout=json.dumps(info))
            if "getprop" in args:
                return SimpleNamespace(returncode=0, stdout="1")
            return SimpleNamespace(returncode=0, stdout="unable to connect")
        with patch.object(Path, "read_text", return_value=self.config()), patch.object(Path,"is_file",return_value=True), \
             patch.object(device_support,"command",side_effect=run), patch.object(device_support,"online",side_effect=[False,True]), \
             patch.object(device_support.time,"sleep"):
            adb,address = device_support.prepare_emulator(Mock(),Mock())
        self.assertEqual(address,"127.0.0.1:16384")
        self.assertEqual(sum("connect" in args for args in seen),2)
        self.assertTrue(any("launch" in args for args in seen))
        self.assertTrue(any("getprop" in args for args in seen))

    def test_cold_boot_tolerates_one_connect_or_boot_probe_timeout(self):
        for timed_out in ('connect', 'getprop'):
            with self.subTest(command=timed_out):
                failed = False
                def run(args, timeout=8):
                    nonlocal failed
                    if 'info' in args:
                        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(
                            is_process_started=True, adb_port=16384)))
                    if timed_out in args and not failed:
                        failed = True
                        raise subprocess.TimeoutExpired(args, timeout)
                    return SimpleNamespace(returncode=0, stdout='1')
                with patch.object(Path,'read_text',return_value=self.config()), \
                     patch.object(Path,'is_file',return_value=True), \
                     patch.object(device_support,'command',side_effect=run), \
                     patch.object(device_support,'online',return_value=True), \
                     patch.object(device_support.time,'sleep'):
                    self.assertEqual(device_support.prepare_emulator(Mock(),Mock())[1],
                                     '127.0.0.1:16384')
                self.assertTrue(failed)

    def test_persistent_adb_timeout_still_stops_at_existing_boot_deadline(self):
        def run(args, timeout=8):
            if 'info' in args:
                return SimpleNamespace(returncode=0, stdout=json.dumps(dict(
                    is_process_started=True, adb_port=16384)))
            raise subprocess.TimeoutExpired(args, timeout)
        with patch.object(Path,'read_text',return_value=self.config()), \
             patch.object(Path,'is_file',return_value=True), \
             patch.object(device_support,'command',side_effect=run), \
             patch.object(device_support.time,'monotonic',side_effect=[0,0,0,0,2]), \
             patch.object(device_support.time,'sleep'):
            with self.assertRaisesRegex(task_support.TaskIncomplete,'等待模拟器连接超时'):
                device_support.prepare_emulator(Mock(),Mock(),timeout=1)

    def test_preparation_can_be_stopped_before_any_command(self):
        with patch.object(Path,"read_text",return_value=self.config()), patch.object(Path,"is_file",return_value=True), \
             patch.object(device_support,"command") as run:
            with self.assertRaises(task_support.TaskCancelled):
                device_support.prepare_emulator(Mock(),Mock(side_effect=task_support.TaskCancelled))
            run.assert_not_called()

    def test_lost_article_feed_is_recovered_without_swiping_wrong_page(self):
        value = worker(); value._sleep = Mock()
        value._content_page_ready.return_value = False
        value._recover_content_home = Mock(); value.detect = Mock()
        with self.assertRaises(task_support.TaskIncomplete):
            value.read_article()
        self.assertEqual(value._recover_content_home.call_count, 3)
        value.detect.assert_not_called()
        value.tasker.controller.post_swipe.assert_not_called()

    def test_unconfirmed_video_player_is_not_timed_as_playback(self):
        import numpy as np
        value = worker(); value._sleep = Mock()
        value._content_page_ready.return_value = False
        value.detect = Mock(return_value=([[10,300,400,300]],["video"]))
        value._has_unread_text = Mock(return_value=True)
        value.tasker.controller.post_screencap.return_value = job()
        value.tasker.controller.post_screencap.return_value.get.return_value = np.zeros((1280,720,3),np.uint8)
        with self.assertRaises(task_support.TaskIncomplete):
            value.watch_video()
        self.assertFalse(any(call.args[0] >= 50 for call in value._sleep.call_args_list))
        value.tasker.controller.post_swipe.assert_not_called()

    def test_card_that_did_not_open_is_not_clicked_repeatedly_or_backed_out(self):
        import numpy as np
        value = worker(); value._sleep = Mock()
        value._content_page_ready.side_effect = lambda kind: kind == "feed"
        value.detect = Mock(return_value=([[10,300,400,300]],["article"]))
        value._has_unread_text = Mock(return_value=True)
        value._article_card_text = Mock(return_value="未能打开的同一张卡片")
        value.tasker.controller.post_screencap.return_value = job()
        value.tasker.controller.post_screencap.return_value.get.return_value = np.zeros((1280,720,3),np.uint8)
        with self.assertRaises(task_support.TaskIncomplete):
            value.read_article()
        value.tasker.controller.post_click.assert_called_once()
        self.assertFalse(any(call.args[0]=="返回" for call in value.tasker.post_task.call_args_list))

    def test_video_shortfall_is_supplemented_and_verified_again(self):
        value=worker(); value._reset_home=Mock(); value.watch_video=Mock()
        value.read_learning_score=Mock(side_effect=[{"视听学习":dict(value=0,maximum=12)},
                                                   {"视听学习":dict(value=11,maximum=12)},
                                                   {"视听学习":dict(value=12,maximum=12)}])
        value.task(["视听学习"])
        self.assertEqual(value.watch_video.call_count,2)
        self.assertEqual(value.watch_video.call_args_list[1].kwargs["duration"],80)
        self.assertEqual(value.run_report.snapshot()["tasks"]["视听学习"]["after"]["value"],12)
        self.assertEqual(value.run_report.snapshot()["status"],"completed")

    def score_records(self, with_daily_action=True):
        records=[dict(text="每日答题",box=[50,450,150,35]),
                 dict(text="趣味答题",box=[50,680,150,35]),
                 dict(text="去答题",box=[560,900,90,30])]
        if with_daily_action:
            records.append(dict(text="去答题",box=[560,600,90,30]))
        return records

    def test_daily_navigation_uses_native_entry_and_confirms_question(self):
        value=worker();value._reset_home=Mock();value._sleep=Mock()
        value._captcha_present=Mock(return_value=False)
        value._screen_records=Mock(return_value=[dict(text="单选题",box=[50,200,90,30])])
        value.tasker.post_task.return_value=job()
        value._open_daily_quiz()
        self.assertEqual([c.args[0] for c in value.tasker.post_task.call_args_list],
                         ["本机个人页入口","本机答题中心入口","本机每日答题卡片"])
        value.tasker.controller.post_swipe.assert_not_called()

    def test_daily_navigation_does_not_treat_entry_click_as_question_success(self):
        value=worker();value._reset_home=Mock();value._sleep=Mock()
        value._captcha_present=Mock(return_value=False)
        value._screen_records=Mock(return_value=self.score_records(False))
        value.tasker.post_task.return_value=job()
        with self.assertRaises(task_support.TaskUnavailable):
            value._open_daily_quiz()
        value.tasker.controller.post_click.assert_not_called()
        self.assertEqual(value._screen_records.call_count,13)

    def test_video_progress_parser_rejects_invalid_and_reads_split_time(self):
        self.assertEqual(task_support.parse_video_progress("00：09 / 00：14"),dict(position=9,total=14))
        for value in ("正在加载", "00:19/00:14", "00:70/02:00", "00:00/00:00"):
            self.assertIsNone(task_support.parse_video_progress(value))

    def test_short_video_counts_only_actual_progress_and_exits_at_end(self):
        import utils
        value=worker();value._sleep=Mock()
        value._watch_video_segment=utils.MaaWorker._watch_video_segment.__get__(value)
        value._video_progress=Mock(side_effect=[dict(position=0,total=14),dict(position=8,total=14),dict(position=14,total=14)])
        self.assertEqual(value._watch_video_segment(65),14)
        self.assertEqual([call.args[0] for call in value._sleep.call_args_list],[8,6])

    def test_stalled_video_is_not_counted_as_playing(self):
        import utils
        value=worker();value._sleep=Mock()
        value._watch_video_segment=utils.MaaWorker._watch_video_segment.__get__(value)
        value._video_progress=Mock(return_value=dict(position=0,total=60))
        self.assertEqual(value._watch_video_segment(65),0)
        self.assertEqual(value._sleep.call_count,3)

    def test_click_option_detection_separates_real_two_four_and_six_button_pages(self):
        import numpy as np
        from PIL import Image
        from utils import detect_click_option_boxes
        fixtures=Path(__file__).parent/'fixtures'
        for name,centers in (
            ('click-two.png',[(110,540),(220,540)]),
            ('click-four.png',[(100,495),(180,495),(265,495),(348,495)]),
            ('click-six.png',[(100,595),(180,595),(265,595),(348,595),(430,595),(515,595)]),
        ):
            with self.subTest(name=name):
                boxes=detect_click_option_boxes(np.asarray(Image.open(fixtures/name).convert('RGB')))
                self.assertEqual(len(boxes),len(centers))
                for cx,cy in centers:
                    self.assertEqual(sum(x<cx<x+w and y<cy<y+h for x,y,w,h in boxes),1)

    def test_click_answer_missing_is_failed_without_false_success_or_takeover(self):
        value=worker();value._scan_click_options=Mock(return_value={"就业":[60,500,94,70]})
        value.pause=Mock()
        with self.assertRaises(task_support.TaskIncomplete):
            value._click_text_answers(["零就业"])
        value.tasker.controller.post_click.assert_not_called()
        value.pause.assert_not_called()

    def test_click_answer_no_visual_change_is_failed(self):
        import numpy as np
        value=worker();value._scan_click_options=Mock(return_value={"家庭":[60,500,94,70]})
        value._sleep=Mock();value.pause=Mock()
        capture=Mock();capture.wait.return_value=capture
        capture.get.return_value=np.zeros((1280,720,3),np.uint8)
        value.tasker.controller.post_screencap.return_value=capture
        value.tasker.controller.post_click.return_value.wait.return_value.succeeded=True
        with self.assertRaises(task_support.TaskIncomplete):
            value._click_text_answers(["家庭"])
        value.pause.assert_not_called()


if __name__ == "__main__":
    unittest.main()
