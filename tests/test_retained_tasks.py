"""Retention and abandoned-task checks; no devices or paid calls."""
import sys
import json
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from unittest.mock import Mock, patch
from types import SimpleNamespace
from fastapi.testclient import TestClient
import task_support
import webui
from test_optimization import worker

class RetainedTaskTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        patcher=patch.object(task_support,"REPORT_PATH",Path(temporary.name)/"run.json")
        patcher.start();self.addCleanup(patcher.stop);self.addCleanup(temporary.cleanup)

    def test_retired_tasks_and_practice_flag_are_rejected(self):
        state=webui.AppState();state.worker=SimpleNamespace(connected=True)
        with patch.object(webui,"app_state",state):
            with TestClient(webui.app) as client:
                for body in ({"tasklist":["收藏"]},
                             {"tasklist":["强国运动"]},{"tasklist":["每日答题"],"fun_acceptance":True}):
                    self.assertEqual(client.post("/api/start",json=body).status_code,422)

    def test_saved_plan_contains_only_retained_tasks(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(webui,"task_running",return_value=False),patch.object(webui,"Path",side_effect=lambda relative:Path(directory)/relative):
            default=webui.get_task_plan()
            self.assertEqual(set(default["available"]),set(task_support.ALIASES))
            self.assertEqual(len(default["available"]),8)
            result=webui.save_task_plan(webui.TaskModel(tasklist=["登录","每日答题","每日答题"],debug=True))
            self.assertEqual(result["tasklist"],["登录","每日答题"])
            self.assertFalse(result["debug"])

    def test_cancelled_or_failed_retired_tasks_are_abandoned_before_core(self):
        for state,name in [("stopped","收藏"),("partial","强国运动"),("failed","强国运动")]:
            value=worker();value._reset_home=Mock();value._finish_existing_quiz=Mock()
            value.read_learning_score=Mock(return_value={"登录":dict(value=1,maximum=1)})
            with patch("utils.read_report",return_value={"status":state,"tasks":{name:{}}}):
                value.task(["登录"])
            value._reset_home.assert_called_once();value._finish_existing_quiz.assert_not_called()
            self.assertEqual(value.run_report.snapshot()["status"],"completed")
