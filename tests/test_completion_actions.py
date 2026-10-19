"""Closing resources requires a completed saved run; failures preserve windows."""
from pathlib import Path
import os
import asyncio
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch,AsyncMock
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import completion_actions as actions
import webui
from fastapi.testclient import TestClient
from points_history import PointsHistory
from test_points_history import report


class CompletionActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'options.json'
        patcher=patch.object(actions,'OPTIONS_PATH',self.path);patcher.start();self.addCleanup(patcher.stop)
        self.action=actions.CompletionActions()

    def test_new_install_enabled_but_reading_old_success_does_not_arm(self):
        self.assertTrue(actions.read_options()['auto_close'])
        self.action.poll(report(),running=False,shutdown=Mock())
        self.assertEqual(self.action.snapshot()['state'],'idle')

    def test_incomplete_failed_and_acceptance_only_runs_never_arm(self):
        values=[report(status=state) for state in ('partial','failed','needs_human','stopped','running')]
        bad=report();bad['tasks']['登录']['state']='incomplete';values.append(bad)
        acceptance=report();acceptance['acceptance_test']='daily_fresh_round';values.append(acceptance)
        for value in values:
            self.action.arm(value);self.assertEqual(self.action.snapshot()['state'],'idle')

    def test_disabled_option_and_keep_open_are_distinct(self):
        actions.save_options(False);self.action.arm(report());self.assertEqual(self.action.snapshot()['state'],'idle')
        actions.save_options(True);self.action.arm(report());self.assertTrue(self.action.cancel())
        self.assertEqual(self.action.snapshot()['state'],'cancelled');self.assertTrue(actions.read_options()['auto_close'])

    def test_countdown_waits_and_new_task_or_changed_run_cancels_it(self):
        for changed,running in ((report('other'),False),(report(),True)):
            self.action.arm(report())
            with patch.object(actions.subprocess,'Popen') as spawn:
                self.action.poll(changed,running=running,shutdown=Mock());spawn.assert_not_called()
            self.assertEqual(self.action.snapshot()['state'],'cancelled')

    def start_closing(self,process):
        self.action.arm(report());self.action.deadline=0
        with (patch.object(actions.subprocess,'Popen',return_value=process) as spawn,
              patch.dict(os.environ,{'SystemRoot':self.tmp.name}),patch.object(actions.Path,'cwd',return_value=Path(self.tmp.name))):
            self.action.poll(report(),running=False,shutdown=Mock())
        return spawn

    def test_successful_helper_requests_graceful_backend_exit_once(self):
        process=Mock();process.poll.return_value=0
        spawn=self.start_closing(process);self.assertEqual(self.action.snapshot()['state'],'closing')
        self.assertIn('Finish-Session.ps1',' '.join(spawn.call_args.args[0]))
        self.assertFalse(self.action.cancel())
        shutdown=Mock();self.action.poll(report(),running=False,shutdown=shutdown)
        self.action.poll(report(),running=False,shutdown=shutdown);shutdown.assert_called_once()

    def test_failed_helper_preserves_backend(self):
        process=Mock();process.poll.return_value=1;self.start_closing(process)
        shutdown=Mock();self.action.poll(report(),running=False,shutdown=shutdown)
        shutdown.assert_not_called();self.assertEqual(self.action.snapshot()['state'],'failed')

    def test_only_the_created_helper_is_terminated_on_timeout(self):
        process=Mock();process.poll.return_value=None;self.start_closing(process);self.action.deadline=0
        shutdown=Mock();self.action.poll(report(),running=False,shutdown=shutdown)
        process.kill.assert_called_once();shutdown.assert_not_called()

    def test_options_api_rejects_non_boolean_and_history_read_does_not_close(self):
        store=PointsHistory(Path(self.tmp.name)/'points.sqlite3')
        with patch.object(webui.app_state,'points',store),patch.object(webui,'current_report',return_value=report()),TestClient(webui.app) as client:
            self.assertEqual(client.post('/api/local/completion-options',json={'auto_close':'true'}).status_code,422)
            self.assertEqual(client.get('/api/local/points-history?month=2026-10').json()['month_points'],1)
            self.assertEqual(webui.app_state.completion.snapshot()['state'],'idle')

    def test_history_save_failure_blocks_automatic_closure(self):
        worker=Mock();worker.last_task_error=None
        before=report('before');after=report('after');points=Mock();points.record_run.side_effect=OSError('read-only disk')
        action=actions.CompletionActions()
        with (patch.object(webui.app_state,'worker',worker),patch.object(webui.app_state,'points',points),
              patch.object(webui.app_state,'completion',action),patch.object(webui,'current_report',side_effect=[before,after])):
            webui.execute_tasks(webui.TaskModel(tasklist=['登录']))
        self.assertEqual(action.snapshot()['state'],'failed');self.assertIsNone(action.process)

    def test_starting_new_task_cancels_old_countdown_and_clears_old_banner(self):
        action=actions.CompletionActions();action.arm(report())
        worker=Mock()
        with (patch.object(webui.app_state,'completion',action),patch.object(webui.app_state,'child_process',None),
              patch.object(webui,'ensure_worker',return_value=worker),patch.object(webui.threading,'Thread')):
            result=webui.start_selected(webui.TaskModel(tasklist=['登录']))
        self.assertEqual(result['status'],'success')
        self.assertEqual(action.snapshot()['state'],'idle');self.assertIsNone(action.process)

    def test_idle_log_connection_exits_when_client_disconnects(self):
        socket=SimpleNamespace(query_params={},accept=AsyncMock(),receive=AsyncMock(return_value={'type':'websocket.disconnect'}))
        with patch.object(webui.app_state,'history_message',[]),patch.object(webui.app_state,'shutting_down',False):
            asyncio.run(asyncio.wait_for(webui.websocket_endpoint(socket),timeout=.5))
        socket.receive.assert_awaited_once()

    def test_shutdown_finishes_idle_log_connection_without_waiting_for_more_logs(self):
        async def idle():
            webui.app_state.shutting_down=True
            raise asyncio.TimeoutError()
        socket=SimpleNamespace(query_params={},accept=AsyncMock(),receive=AsyncMock(side_effect=idle))
        with patch.object(webui.app_state,'history_message',[]),patch.object(webui.app_state,'shutting_down',False):
            asyncio.run(asyncio.wait_for(webui.websocket_endpoint(socket),timeout=.5))
        socket.receive.assert_awaited_once()

    def test_graceful_shutdown_signals_log_connections_and_server_together(self):
        server=SimpleNamespace(should_exit=False)
        with patch.object(webui.app_state,'shutting_down',False):
            webui.request_server_shutdown(server)
            self.assertTrue(webui.app_state.shutting_down);self.assertTrue(server.should_exit)


if __name__=='__main__':unittest.main()
