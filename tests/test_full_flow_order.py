"""A one-run verification-first order does not change the normal daily plan."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from types import SimpleNamespace
from unittest.mock import Mock,patch
from fastapi.testclient import TestClient
import webui
from task_support import ALIASES,ordered_tasks
from test_optimization import worker


class FullFlowOrderTests(unittest.TestCase):
    def test_explicit_order_keeps_every_task_and_default_order_remains(self):
        selected=list(ALIASES)
        self.assertEqual(ordered_tasks(selected,True),['登录','趣味答题','每日答题','订阅','发表观点','选读文章','视听学习','本地频道'])
        self.assertEqual(ordered_tasks(selected),selected)

    def test_endpoint_does_not_save_or_change_plan(self):
        with patch.object(webui,'start_selected',return_value={'status':'success'}) as start,patch.object(webui,'save_task_plan') as save:
            self.assertEqual(webui.verify_full_flow(webui.TaskModel(tasklist=list(ALIASES),unattended=True)),{'status':'success'})
            self.assertEqual(start.call_args.kwargs,{'verification_first':True});save.assert_not_called()

    def test_partial_plan_is_not_claimed_as_a_full_flow(self):
        with patch.object(webui,'start_selected') as start:
            with self.assertRaises(Exception):webui.verify_full_flow(webui.TaskModel(tasklist=['每日答题']))
            start.assert_not_called()

    def test_executor_keeps_one_worker_for_all_eight_tasks(self):
        tasks=webui.TaskModel(tasklist=list(ALIASES),unattended=True)
        state=SimpleNamespace(worker=SimpleNamespace(task=Mock()),lock=__import__('threading').Lock(),child_process=Mock(),current_tasks=[])
        with patch.object(webui,'app_state',state):webui.execute_tasks(tasks,verification_first=True)
        state.worker.task.assert_called_once_with(list(ALIASES),False,False,unattended=True,verification_first=True)


if __name__=='__main__':unittest.main()
