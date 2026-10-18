"""Pausing is cooperative; continuing never approves a verification challenge."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
from fastapi.testclient import TestClient
from test_optimization import worker
import task_support
import utils
import webui


class PauseControlTests(unittest.TestCase):
    def wait(self,predicate):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.005)
        self.fail('Pause observer did not reach the expected state')

    def launch(self,value,**options):
        failures=[]
        def run():
            try:value.pause('fixture pause',**options)
            except Exception as error:failures.append(error)
        thread=threading.Thread(target=run,daemon=True);thread.start()
        self.addCleanup(lambda:(setattr(value,'pause_flag',False),setattr(value,'stop_flag',True),thread.join(2)))
        self.wait(lambda:value.pause_flag)
        return thread,failures

    def test_pause_request_does_not_stop_or_click_the_device(self):
        value=worker();value.request_pause()
        self.assertTrue(value._manual_pause_requested)
        value.tasker.controller.post_click.assert_not_called()

    def test_request_is_consumed_at_the_next_safe_checkpoint(self):
        value=worker();value.pause=Mock();value.request_pause();value._check_active()
        value.pause.assert_called_once_with('已由你暂停，点击控制台“继续执行”恢复',manual=True)
        self.assertFalse(value._manual_pause_requested)

    def test_manual_pause_holds_even_for_unattended_run_and_resumes_only_on_request(self):
        value=worker();value.unattended=True;value._deadline=time.monotonic()+10
        original=value._deadline
        with patch('utils.OWNER_WAIT_SECONDS',.03):
            thread,errors=self.launch(value,manual=True)
            time.sleep(.15)
            self.assertTrue(thread.is_alive());self.assertTrue(value.pause_flag)
            self.assertFalse(value.request_continue());thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertGreater(value._deadline,original)

    def test_continue_during_verification_keeps_the_owner_observer_in_charge(self):
        value=worker();value.unattended=False;ready=Mock(return_value=False)
        with patch('utils.plyer.notification.notify'):
            thread,errors=self.launch(value,resume_when=ready)
            self.assertTrue(value.request_continue());time.sleep(.12)
            self.assertTrue(value.pause_flag);self.assertTrue(thread.is_alive())
            ready.return_value=True;thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        value.tasker.controller.post_click.assert_not_called()

    def test_stop_releases_manual_hold_without_resuming_actions(self):
        value=worker();value.tasker.post_stop=Mock()
        thread,errors=self.launch(value,manual=True)
        value.request_stop();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertFalse(value.pause_flag)
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],task_support.TaskCancelled)
        value.tasker.post_stop.assert_called_once()

    def test_continue_can_cancel_a_pause_request_before_a_checkpoint(self):
        value=worker();value.request_pause();value.request_continue();value.pause=Mock();value._check_active()
        value.pause.assert_not_called();self.assertFalse(value._manual_pause_requested)


class ContinuationApiTests(unittest.TestCase):
    def state(self,status='partial'):
        value=worker();value.run_report=Mock()
        value.run_report.snapshot.return_value={'status':status,'tasks':{'登录':{'state':'verified'},'选读文章':{'state':'incomplete'}}}
        state=webui.AppState();state.worker=value
        return state

    def test_active_manual_resume_uses_the_same_worker_not_another_task(self):
        state=self.state();state.worker.pause_flag=True
        with patch.object(webui,'app_state',state),patch.object(webui,'task_running',return_value=True), \
             patch.object(webui,'start_selected') as start:
            self.assertEqual(webui.resume()['status'],'success')
        self.assertFalse(state.worker.pause_flag);start.assert_not_called()

    def test_active_verification_resume_does_not_clear_verification_pause(self):
        state=self.state();state.worker.pause_flag=True;state.worker._verification_wait_active=True
        with patch.object(webui,'app_state',state),patch.object(webui,'task_running',return_value=True):
            result=webui.resume()
        self.assertTrue(result['waiting_for_owner']);self.assertTrue(state.worker.pause_flag)

    def test_ended_run_resumes_the_original_selection_for_fresh_score_checks(self):
        for status in ['partial','failed','stopped','needs_human','paused','running']:
            state=self.state(status)
            with self.subTest(status=status),patch.object(webui,'app_state',state), \
                 patch.object(webui,'task_running',return_value=False), \
                 patch.object(webui,'start_selected',return_value={'status':'success'}) as start:
                self.assertEqual(webui.resume()['status'],'success')
                self.assertEqual(start.call_args.args[0].tasklist,['登录','选读文章'])
                self.assertFalse(start.call_args.args[0].unattended)

    def test_completed_run_is_not_implicitly_repeated(self):
        state=self.state('completed')
        with patch.object(webui,'app_state',state),patch.object(webui,'task_running',return_value=False), \
             patch.object(webui,'start_selected') as start:
            self.assertEqual(webui.resume()['status'],'failed')
        start.assert_not_called()

    def test_stale_or_unknown_tasks_are_not_reintroduced(self):
        self.assertEqual(webui.continuation_tasks({'status':'partial','tasks':{'运动':{}}}),[])
        self.assertEqual(webui.continuation_tasks({'status':'partial','tasks':None}),[])

    def test_pause_endpoint_requires_an_active_job(self):
        for active in [False,True]:
            state=self.state();state.worker.request_pause=Mock()
            with self.subTest(active=active),patch.object(webui,'app_state',state), \
                 patch.object(webui,'task_running',return_value=active):
                with TestClient(webui.app) as client:result=client.post('/api/pause',json={}).json()
            self.assertEqual(result['status'],'success' if active else 'failed')
            self.assertEqual(state.worker.request_pause.call_count,1 if active else 0)

    def test_continued_task_skips_completed_scores_and_only_runs_remaining_work(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(task_support,'REPORT_PATH',Path(folder)/'report.json'):
            value=worker();value._finish_existing_quiz=Mock();value._summarize_run=Mock();value._reset_home=Mock()
            value.read_learning_score=Mock(return_value={'登录':{'value':1,'maximum':1},'选读文章':{'value':0,'maximum':12}})
            value.read_article=Mock(return_value={'value':12,'maximum':12})
            value.task(['登录','选读文章'])
            result=value.run_report.snapshot()
        self.assertEqual(result['tasks']['登录']['state'],'skipped_full')
        self.assertEqual(result['tasks']['选读文章']['state'],'verified')
        value.read_article.assert_called_once()
        self.assertEqual(result['status'],'completed')
