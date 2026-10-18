import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
"""Offline regression checks for known failure modes; no key loading or paid calls."""
import asyncio
import base64
import json
import tempfile
import threading
import time
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import numpy as np
from PIL import Image
from fastapi.testclient import TestClient

import answer_client
import task_support
import utils
import webui


def job(succeeded=True, text="单选题"):
    value = SimpleNamespace(status=SimpleNamespace(succeeded=succeeded, failed=not succeeded),
                            nodes=[SimpleNamespace(recognition=SimpleNamespace(best_result=SimpleNamespace(text=text)))])
    result = Mock()
    result.wait.return_value = result
    result.get.return_value = value
    return result


def worker():
    value = utils.MaaWorker.__new__(utils.MaaWorker)
    value.stop_flag = False
    value.pause_flag = False
    value.fast_answer = False
    value._deadline = None
    value.send_log = Mock()
    value.prepare_device = Mock()
    value._guard_verification = Mock()
    value._content_page_ready = Mock(return_value=True)
    value._app_in_foreground = Mock(return_value=True)
    value._watch_video_segment = Mock(return_value=65)
    value.tasker = SimpleNamespace(controller=Mock(), post_task=Mock(return_value=job()))
    return value


class OptimizationTests(unittest.TestCase):
    def setUp(self):
        self.images = [np.zeros((80, 160, 3), np.uint8)]
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(task_support, "REPORT_PATH", Path(self.temp.name)/"last-run.json")
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def resolver(self, result=None, finish="stop", transport=None):
        value = answer_client.AIResolver("offline-fixture-key", "deepseek-flash")
        value.session.close()
        if transport is None:
            raw = result if isinstance(result, str) else json.dumps(result)
            transport = lambda request: httpx.Response(200, json={"choices":[{
                "finish_reason":finish, "message":{"content":raw}}]})
        value.session = httpx.Client(transport=httpx.MockTransport(transport))
        self.addCleanup(value.session.close)
        return value

    def test_colors_and_native_resolution(self):
        bgr = np.zeros((80, 160, 3), np.uint8)
        bgr[:] = [0, 0, 255]
        encoded = answer_client.AIResolver.image_encode(bgr)
        decoded = Image.open(BytesIO(base64.b64decode(encoded)))
        self.assertEqual(decoded.format, "PNG")
        self.assertEqual(decoded.size, (160, 80))
        self.assertEqual(decoded.getpixel((10, 10)), (255, 0, 0))

    def test_strict_choice_contract(self):
        for result in ("Answer is B", {"choices":["B","B"],"uncertain":False},
                       {"choices":["B","C"],"uncertain":False},
                       {"choices":["B"],"uncertain":True},
                       {"choices":["G"],"uncertain":False}):
            with self.subTest(result=result):
                self.assertIsNone(self.resolver(result).resolve_choice(self.images))
        self.assertEqual(self.resolver({"choices":["C","B"],"uncertain":False})
                         .resolve_choice(self.images, "多选题"), ["B","C"])
        self.assertIsNone(self.resolver({"choices":["B"],"uncertain":False}, finish="length")
                          .resolve_choice(self.images))
        self.assertIsNone(self.resolver({"answer":"长度不符","uncertain":False})
                          .resolve_blank(self.images, True, 2))

    def test_bounded_network_retry(self):
        calls = []
        def transport(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After":"0"})
            return httpx.Response(200, json={"choices":[{"message":{
                "content":'{"choices":["B"],"uncertain":false}'}}]})
        self.assertEqual(self.resolver(transport=transport).resolve_choice(self.images), ["B"])
        self.assertEqual(len(calls), 2)
        for kind in ("unauthorized", "read_timeout"):
            calls.clear()
            def failed(request):
                calls.append(request)
                if kind == "read_timeout":
                    raise httpx.ReadTimeout("offline read timeout", request=request)
                return httpx.Response(401)
            self.assertIsNone(self.resolver(transport=failed).resolve_choice(self.images))
            self.assertEqual(len(calls), 1)

    def test_actual_score_ocr_fixtures(self):
        fixture_path = Path(__file__).resolve().parents[1]/"score-ocr-fixtures.json"
        if not fixture_path.exists():
            fixture_path = Path(__file__).resolve().parent/"fixtures/score-ocr-fixtures.json"
        fixtures = json.loads(fixture_path.read_text("utf-8"))
        before = task_support.parse_score_rows(fixtures["today-score-ready.png"])
        after = task_support.parse_score_rows(fixtures["today-score-mid.png"])
        tasks = task_support.parse_score_rows(fixtures["today-score-mid-tasks.png"])
        self.assertEqual(before["选读文章"]["value"], 0)
        self.assertEqual(before["选读文章"]["maximum"], 12)
        self.assertTrue(task_support.is_complete(after["选读文章"]))
        self.assertEqual(tasks["每日答题"]["value"], 0)
        self.assertEqual(tasks["每日答题"]["maximum"], 5)
        # The unanchored 12/12 near the top must not be assigned to daily quiz.
        self.assertFalse(task_support.is_complete(tasks["每日答题"]))
        self.assertNotIn("视听学习", tasks)

    def test_ambiguous_rows_are_not_full(self):
        records = [dict(text="每日答题",box=[54,450,130,35]),
                   dict(text="每日上限5分",box=[54,550,140,30]),
                   dict(text="0分/5分",box=[560,600,100,30]),
                   dict(text="趣味答题",box=[54,680,130,35]),
                   dict(text="已完成",box=[560,750,100,30]),
                   dict(text="10分/10分",box=[540,900,120,30])]
        self.assertFalse(task_support.is_complete(task_support.parse_score_rows(records)["每日答题"]))
        records[2]["text"] = "9分/5分"
        self.assertNotIn("每日答题", task_support.parse_score_rows(records))

    def test_full_scores_skip_all_actions(self):
        value = worker()
        scores = {name:dict(value=cap, maximum=cap) for name,cap in
                  (("选读文章",12),("视听学习",12),("每日答题",5))}
        value.read_learning_score = Mock(return_value=scores)
        value._reset_home = Mock()
        value.read_article = Mock()
        value.watch_video = Mock()
        value.daily_answer = Mock()
        value.task(list(scores))
        self.assertEqual(value.run_report.snapshot()["status"], "completed")
        self.assertTrue(all(x["state"] == "skipped_full" for x in value.run_report.snapshot()["tasks"].values()))
        value._reset_home.assert_not_called()
        value.read_article.assert_not_called()
        value.watch_video.assert_not_called()
        value.daily_answer.assert_not_called()

    def test_unknown_final_score_is_not_success(self):
        value = worker()
        value.read_learning_score = Mock(side_effect=[{"选读文章":dict(value=0,maximum=12)}, {}])
        value._reset_home = Mock()
        value.read_article = Mock()
        with patch("utils.traceback.print_exc"), patch("utils.plyer.notification.notify"):
            value.task(["选读文章"])
        self.assertEqual(value.last_task_error, "TaskIncomplete")
        self.assertEqual(value.run_report.snapshot()["tasks"]["选读文章"]["state"], "unverified")
        self.assertEqual(value.read_article.call_count, 1)
        self.assertFalse(any("积分已核实完成" in str(x) for x in value.send_log.call_args_list))

    def test_failed_answer_does_not_submit(self):
        value = worker()
        value._sleep = Mock()
        value._navigate_to_daily_answer = Mock(return_value=True)
        value._handle_choice = Mock(return_value=False)
        with self.assertRaises(task_support.TaskIncomplete):
            value.daily_answer()
        self.assertFalse(any(x.args[0] == "确定" for x in value.tasker.post_task.call_args_list))

    def test_missing_transition_is_not_correct(self):
        value = worker()
        value._sleep = Mock()
        value.tasker.post_task.return_value = job(False)
        value._captcha_present = Mock(return_value=False)
        for index in (0, 4):
            with self.subTest(index=index):
                with self.assertRaises(task_support.TaskIncomplete):
                    value._check_quiz_transition(index)

    def test_stop_is_not_reset(self):
        value = worker()
        value.stop_flag = True
        value.read_learning_score = Mock()
        value.task(["选读文章"])
        value.read_learning_score.assert_not_called()
        self.assertEqual(value.run_report.snapshot()["status"], "stopped")

    def test_long_wait_can_be_stopped(self):
        value = worker()
        timer = threading.Timer(0.02, lambda:setattr(value,"stop_flag",True))
        started = time.monotonic()
        timer.start()
        try:
            with self.assertRaises(task_support.TaskCancelled):
                value._sleep(60)
        finally:
            timer.cancel()
        self.assertLess(time.monotonic()-started, 0.5)

    def test_video_coordinates_not_invalidated(self):
        value = worker()
        value._sleep = Mock()
        value.detect = Mock(return_value=([[10,300,400,300]],["video"]))
        value._has_unread_text = Mock(return_value=True)
        value.tasker.controller.post_screencap.return_value = job()
        value.tasker.controller.post_screencap.return_value.get.return_value = np.zeros((1280,720,3),np.uint8)
        value.watch_video()
        actions = [x[0] for x in value.tasker.controller.method_calls]
        self.assertLess(actions.index("post_click"), actions.index("post_swipe"))

    def test_existing_finished_quiz_is_resumed_by_program(self):
        value = worker()
        value._adb_command = ("adb.exe", "-s", "127.0.0.1:16384")
        value._captcha_present = Mock(return_value=False)
        value._completion_button = Mock(return_value=True)
        value._finish_quiz = Mock()
        with patch.object(utils.subprocess, "run", return_value=SimpleNamespace(stdout="topResumedActivity=ActivityRecord{1 u0 cn.xuexi.android/com.alibaba.lightapp.runtime.activity.CommonWebViewActivity}")) as query:
            value._finish_existing_quiz()
        self.assertEqual(query.call_args.args[0], ["adb.exe", "-s", "127.0.0.1:16384", "shell", "dumpsys", "activity", "activities"])
        value._finish_quiz.assert_called_once_with(True)
        value.tasker.controller.post_stop_app.assert_not_called()

    def test_existing_other_app_is_not_touched(self):
        value = worker()
        value._adb_command = ("adb.exe", "-s", "127.0.0.1:16384")
        value._finish_quiz = Mock()
        with patch.object(utils.subprocess, "run", return_value=SimpleNamespace(stdout="topResumedActivity=ActivityRecord{1 u0 com.android.launcher3/.Launcher}")):
            value._finish_existing_quiz()
        value.tasker.post_task.assert_not_called()
        value._finish_quiz.assert_not_called()

    def test_finish_button_submits_and_waits(self):
        value = worker()
        value._sleep = Mock()
        value._captcha_present = Mock(return_value=False)
        value._completion_button = Mock(return_value=True)
        value._screen_records=Mock(side_effect=[
            [dict(text='5/5',box=[580,190,80,40]),dict(text='完成',box=[590,60,80,40])],
            [dict(text='本次答题',box=[240,320,200,40])],
            [dict(text='我要答题',box=[300,460,140,40]),dict(text='学习积分',box=[100,460,140,40])]])
        value._finish_quiz(True)
        self.assertEqual(value._completion_button.call_args.args, ("Click",))
        self.assertTrue(value._daily_finish_metrics['left_completion_page'])
        value.tasker.post_task.assert_called_once_with('返回')

    def test_log_replay_remains_bounded(self):
        state = webui.AppState()
        for index in range(1000):
            state.message_conn.put(str(index))
        with patch.object(webui,"app_state",state):
            with TestClient(webui.app) as client:
                self.assertEqual(len(state.history_message),600)
                with client.websocket_connect("/api/ws") as ws:
                    self.assertEqual(ws.receive_text(),"400")
                    for _ in range(599):
                        last = ws.receive_text()
                    self.assertEqual(last,"999")


if __name__ == "__main__":
    unittest.main(verbosity=2)
