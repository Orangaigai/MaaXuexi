"""Cross-boundary replay and recovery; zero external model or Android actions."""
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock,patch
from fastapi.testclient import TestClient
from test_optimization import worker
import webui


class ConsoleSequences(unittest.TestCase):
    def test_websocket_reconnect_resumes_after_cursor_and_restart_uses_new_session(self):
        state=webui.AppState()
        for i in range(3):state.message_conn.put(str(i))
        with patch.object(webui,'app_state',state),TestClient(webui.app) as client:
            with client.websocket_connect('/api/ws?v=2') as stream:
                session=stream.receive_json()['session']
                self.assertEqual(stream.receive_json()['sequence'],0)
                cursor=stream.receive_json()['sequence']
            with client.websocket_connect(f'/api/ws?v=2&session={session}&after={cursor}') as stream:
                self.assertEqual(stream.receive_json()['session'],session)
                self.assertEqual(stream.receive_json()['sequence'],2)
            state.log_session='new-session'
            with client.websocket_connect(f'/api/ws?v=2&session={session}&after=999') as stream:
                self.assertEqual(stream.receive_json()['session'],'new-session')
                self.assertEqual(stream.receive_json()['sequence'],0)

    def test_near_simultaneous_start_requests_create_only_one_job(self):
        state=webui.AppState();state.worker=worker();entered=threading.Event();release=threading.Event()
        def execute(*args,**kwargs):entered.set();release.wait(2)
        with patch.object(webui,'app_state',state),patch.object(webui,'ensure_worker',return_value=state.worker), \
             patch.object(webui,'execute_tasks',side_effect=execute):
            try:
                self.assertEqual(webui.start_selected(webui.TaskModel(tasklist=['登录']))['status'],'success')
                self.assertTrue(entered.wait(1));thread=state.child_process
                self.assertEqual(webui.start_selected(webui.TaskModel(tasklist=['登录']))['status'],'failed')
                self.assertIs(state.child_process,thread)
            finally:release.set();state.child_process.join(2)

    def test_restarted_backend_continues_original_selection_without_new_defaults(self):
        report=dict(status='running',tasks={'每日答题':dict(state='running'),'订阅':dict(state='verified')})
        state=webui.AppState()
        with patch.object(webui,'app_state',state),patch.object(webui,'read_report',return_value=report), \
             patch.object(webui,'start_selected',return_value={'status':'success'}) as start:
            self.assertEqual(webui.resume()['status'],'success')
            self.assertEqual(start.call_args.args[0].tasklist,['每日答题','订阅'])
            self.assertFalse(start.call_args.args[0].unattended)

    def test_diagnostics_never_starts_a_worker_or_runs_while_task_is_active(self):
        with patch.object(webui,'task_running',return_value=True),patch.object(webui,'ensure_worker') as ensure:
            with self.assertRaises(webui.HTTPException):webui.local_diagnostics()
            ensure.assert_not_called()
