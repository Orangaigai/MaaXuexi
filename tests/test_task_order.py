"""Custom order survives saving, task dispatch, daily launches and continuation."""
from pathlib import Path
from types import SimpleNamespace
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import task_support
import webui
from task_support import ALIASES,BASIC_TASKS,OPTIONAL_TASKS,RunReport,ordered_tasks
from test_optimization import worker

CUSTOM=['趣味答题','发表观点','订阅','每日答题']


class TaskOrderTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        for module,name,value in [(webui,'Path',lambda relative:self.root/relative),
                                  (task_support,'REPORT_PATH',self.root/'run.json'),
                                  (webui,'task_running',lambda:False),
                                  (webui,'comment_policy',lambda:dict(enabled=True))]:
            patcher=patch.object(module,name,value);patcher.start();self.addCleanup(patcher.stop)

    def save(self,tasks=None,order=None):
        return webui.save_task_plan(webui.TaskPlanModel(tasklist=list(ALIASES) if tasks is None else tasks,
                                                       task_order=CUSTOM if order is None else order))

    def test_basic_tasks_stay_first_and_optional_request_order_is_preserved(self):
        requested=CUSTOM+list(reversed(BASIC_TASKS))+['趣味答题']
        expected=list(BASIC_TASKS)+CUSTOM
        self.assertEqual(ordered_tasks(requested),expected)
        self.assertEqual(ordered_tasks(requested,True),expected)
        self.assertEqual(webui.TaskModel(tasklist=requested).tasklist,expected)

    def test_old_saved_plan_loads_with_the_existing_default_optional_order(self):
        path=self.root/'config/local-task-plan.json';path.parent.mkdir()
        path.write_text(json.dumps(dict(tasklist=list(ALIASES),fast_answer=False,debug=False)),'utf-8')
        before=path.read_bytes();plan=webui.get_task_plan()
        self.assertEqual(plan['task_order'],list(OPTIONAL_TASKS))
        self.assertEqual(plan['tasklist'],list(ALIASES))
        self.assertEqual(path.read_bytes(),before)

    def test_complete_order_is_saved_even_for_unselected_tasks(self):
        result=self.save(tasks=['每日答题','登录','趣味答题'])
        self.assertEqual(result['tasklist'],['登录','趣味答题','每日答题'])
        self.assertEqual(webui.get_task_plan()['task_order'],CUSTOM)
        self.assertEqual(webui.get_task_plan()['tasklist'],result['tasklist'])
        result=self.save(tasks=list(ALIASES))
        self.assertEqual(result['tasklist'],list(BASIC_TASKS)+CUSTOM)

    def test_legacy_save_and_basic_save_do_not_reset_the_saved_order(self):
        self.save()
        webui.save_task_plan(webui.TaskModel(tasklist=list(BASIC_TASKS)))
        self.assertEqual(webui.get_task_plan()['task_order'],CUSTOM)
        self.assertEqual(webui.get_task_plan()['tasklist'],list(BASIC_TASKS))
        result=webui.save_task_plan(webui.TaskPlanModel(tasklist=list(ALIASES)))
        self.assertEqual(result['tasklist'],list(BASIC_TASKS)+CUSTOM)

    def test_invalid_order_is_rejected_without_changing_the_saved_plan(self):
        self.save();path=self.root/'config/local-task-plan.json';before=path.read_bytes()
        with TestClient(webui.app) as client:
            for order in [[],CUSTOM[:-1],['趣味答题']*4,['登录',*CUSTOM[1:]],'趣味答题',CUSTOM+['每日答题']]:
                with self.subTest(order=order):
                    response=client.post('/api/local/task-plan',json=dict(tasklist=['登录'],task_order=order))
                    self.assertEqual(response.status_code,422)
                    self.assertEqual(path.read_bytes(),before)

    def test_saving_is_blocked_during_an_active_run(self):
        self.save();path=self.root/'config/local-task-plan.json';before=path.read_bytes()
        with patch.object(webui,'task_running',return_value=True):
            with self.assertRaises(webui.HTTPException):self.save(order=list(OPTIONAL_TASKS))
        self.assertEqual(path.read_bytes(),before)

    def test_saved_run_now_uses_the_persisted_selected_order(self):
        self.save()
        with patch.object(webui,'start_selected',return_value={'status':'success'}) as start:
            webui.start_saved_plan(unattended=True,scheduled=True)
        self.assertEqual(start.call_args.args[0].tasklist,list(BASIC_TASKS)+CUSTOM)
        self.assertTrue(start.call_args.args[0].unattended)
        self.assertEqual(start.call_args.kwargs,dict(scheduled=True))

    def test_scheduled_payload_keeps_order_at_the_api_boundary(self):
        with patch.object(webui,'start_selected',return_value={'status':'success'}) as start:
            webui.start_scheduled(webui.TaskModel(tasklist=CUSTOM+list(reversed(BASIC_TASKS)),unattended=True))
        self.assertEqual(start.call_args.args[0].tasklist,list(BASIC_TASKS)+CUSTOM)

    def test_continuation_uses_original_run_order_even_after_plan_changes_and_json_sorting(self):
        selected=list(BASIC_TASKS)+CUSTOM
        report=RunReport(selected);report.finish('partial')
        restored=json.loads(json.dumps(report.snapshot(),sort_keys=True))
        self.save(order=list(OPTIONAL_TASKS))
        self.assertEqual(webui.continuation_tasks(restored),selected)
        with patch.object(webui,'current_report',return_value=restored),patch.object(webui,'start_selected') as start:
            webui.resume()
        self.assertEqual(start.call_args.args[0].tasklist,selected)

    def test_legacy_report_uses_its_existing_task_order(self):
        report=dict(status='partial',tasks={name:dict(state='deferred') for name in ['趣味答题','登录','订阅']})
        self.assertEqual(webui.continuation_tasks(report),['登录','趣味答题','订阅'])

    def test_real_worker_dispatch_respects_custom_order_without_device_or_model_calls(self):
        value=worker();calls=[]
        value._finish_existing_quiz=Mock();value._reset_home=Mock();value._local_clear_location_prompt=Mock()
        full={name:dict(value=1,maximum=1) for name in ALIASES}
        before={name:dict(value=0,maximum=1) for name in ALIASES}
        value.read_learning_score=Mock(side_effect=[before]+[full]*10)
        for method,name in [('read_article','选读文章'),('watch_video','视听学习'),('learn_local_channel','本地频道'),
                            ('daily_answer','每日答题'),('subscribe','订阅'),('publish_comment','发表观点'),('funny_answer','趣味答题')]:
            def perform(*args,_name=name,**kwargs):calls.append(_name);return full[_name]
            setattr(value,method,Mock(side_effect=perform))
        with patch('utils.SubscriptionStore') as store:
            store.return_value.pending.return_value=[]
            value.task(CUSTOM+list(reversed(BASIC_TASKS)))
        self.assertEqual(calls,list(BASIC_TASKS[1:])+CUSTOM)
        self.assertEqual(value.run_report.snapshot()['selected_tasks'],list(BASIC_TASKS)+CUSTOM)
        self.assertEqual(value.run_report.snapshot()['status'],'completed')

    def test_saved_order_cannot_authorize_public_comments(self):
        with patch.object(webui,'comment_policy',return_value=dict(enabled=False)):
            self.save(tasks=['登录'])
            with self.assertRaises(webui.HTTPException):self.save(tasks=['发表观点'])
        self.assertEqual(webui.get_task_plan()['tasklist'],['登录'])

    @unittest.skipUnless(os.name=='nt','Windows daily launcher')
    def test_native_launcher_uses_saved_order_even_when_tasklist_has_an_old_order(self):
        self.save()
        path=self.root/'config/local-task-plan.json'
        data=json.loads(path.read_text('utf-8-sig'));data['tasklist']=list(reversed(ALIASES))
        path.write_text(json.dumps(data),'utf-8')
        script=self.root/'scripts/Run-Daily.ps1';script.parent.mkdir(exist_ok=True)
        shutil.copy2(Path(__file__).resolve().parents[1]/'scripts/Run-Daily.ps1',script)
        for name in ('scripts/Start-MaaXuexi.ps1','backend/webui.py','config/emulator.json','config/deepseek-key.dpapi'):
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);target.touch()
        command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),'-CheckOnly']
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['tasks'],list(BASIC_TASKS)+CUSTOM)
        data['task_order']=['趣味答题']*4;path.write_text(json.dumps(data),'utf-8');before=path.read_bytes()
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Invalid local task order',result.stderr)
        self.assertEqual(path.read_bytes(),before)
