"""Transport deadlines, ordered logging, retention and text-only evaluation."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import httpx
import numpy as np
from test_optimization import worker
from timed_http import AnswerSession,RequestCancelled
from answer_client import AIResolver
from runtime_logs import LogWriter
from diagnostics import RunDiagnostics,load_replay
from log_retention import prune_logs
from runtime_doctor import diagnose
from quiz_benchmark import load_cases,rotate_options
from task_support import RunReport
import task_support


class DeadlineTests(unittest.TestCase):
    def session(self,handler):
        session=AnswerSession(async_transport=httpx.MockTransport(handler),trust_env=False)
        self.addCleanup(session.close);return session

    def test_trickling_response_hits_total_deadline_and_releases_next_request(self):
        stopped=threading.Event();calls=[]
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    for _ in range(50):await asyncio.sleep(.025);yield b'x'
                finally:stopped.set()
        async def respond(request):
            calls.append(request)
            return httpx.Response(200,stream=Stream()) if len(calls)==1 else httpx.Response(200,json={'ok':True})
        session=self.session(respond);started=time.monotonic()
        with self.assertRaises(httpx.ReadTimeout):
            session.post_bounded('https://offline.invalid',json={},total_timeout=.15,stop_requested=lambda:False,
                                 timeout=httpx.Timeout(.1))
        self.assertLess(time.monotonic()-started,.7);self.assertTrue(stopped.wait(.5))
        response=session.post_bounded('https://offline.invalid',json={},total_timeout=1,stop_requested=lambda:False)
        self.assertTrue(response.json()['ok']);self.assertEqual(len(calls),2)

    def test_stop_cancels_running_request_without_waiting_for_read_timeout(self):
        cancelled=threading.Event();stop=threading.Event()
        async def respond(request):
            try:await asyncio.sleep(5)
            finally:cancelled.set()
        session=self.session(respond);timer=threading.Timer(.08,stop.set);timer.start();self.addCleanup(timer.join)
        started=time.monotonic()
        with self.assertRaises(RequestCancelled):
            session.post_bounded('https://offline.invalid',json={},total_timeout=4,stop_requested=stop.is_set)
        self.assertLess(time.monotonic()-started,.7);self.assertTrue(cancelled.wait(.5))

    def test_question_change_cancels_the_active_request(self):
        entered=threading.Event();done=[]
        async def respond(request):entered.set();await asyncio.sleep(5)
        session=self.session(respond)
        def run():
            try:session.post_bounded('https://offline.invalid',json={},total_timeout=4,stop_requested=lambda:False)
            except RequestCancelled:done.append(True)
        thread=threading.Thread(target=run);thread.start();self.assertTrue(entered.wait(1))
        session.cancel_pending();thread.join(1)
        self.assertFalse(thread.is_alive());self.assertEqual(done,[True])

    def test_close_during_loop_startup_does_not_leave_a_background_thread(self):
        entered=threading.Event();release=threading.Event();done=[]
        class StartingSession(AnswerSession):
            def _run(self):
                entered.set();release.wait(1);super()._run()
        session=StartingSession(trust_env=False);self.addCleanup(session.close)
        def request():
            try:session.post_bounded('https://offline.invalid',total_timeout=2,stop_requested=lambda:False)
            except RequestCancelled:done.append(True)
        requester=threading.Thread(target=request);requester.start();self.assertTrue(entered.wait(1))
        timer=threading.Timer(.05,release.set);timer.start()
        session.close();requester.join(1);timer.join()
        self.assertFalse(requester.is_alive());self.assertFalse(session._thread.is_alive());self.assertEqual(done,[True])

    def test_resolver_does_not_retry_an_expired_timed_request(self):
        calls=[]
        async def respond(request):calls.append(request);await asyncio.sleep(2)
        value=AIResolver('offline-test','deepseek-flash');value.session.close();value.session=self.session(respond)
        self.assertIsNone(value._post({'messages':[]},time_limit=.1))
        self.assertEqual((value.last_error,value.last_request_attempts,len(calls)),('read_timeout',1,1))


class MaintenanceTests(unittest.TestCase):
    def test_logs_are_ordered_and_drained_on_close_without_task_thread_sleep(self):
        with tempfile.TemporaryDirectory() as folder:
            writer=LogWriter(folder);started=time.monotonic()
            for i in range(40):writer.write(str(i))
            elapsed=time.monotonic()-started;writer.close()
            lines=next(Path(folder).glob('events-*.log')).read_text('utf-8').splitlines()
            self.assertEqual(lines,list(map(str,range(40))));self.assertLess(elapsed,.2)

    def test_late_failure_replaces_middle_trace_but_preserves_entry_and_budget(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(task_support,'REPORT_PATH',Path(folder)/'report.json'):
            report=RunReport(['每日答题']);trace=RunDiagnostics(report,Mock(),Path(folder)/'trace')
            for i in range(35):trace.capture('step-'+str(i),[],np.full((8,8,3),i,np.uint8))
            trace.capture('failure-last-observed',[],np.zeros((8,8,3),np.uint8))
            frames=load_replay(trace.folder/'index.json')
            self.assertEqual(len(frames),24);self.assertEqual(len(list(trace.folder.glob('*.png'))),24)
            self.assertEqual([r[0]['scene'] for r in frames[:2]],['step-0','step-1'])
            self.assertEqual(frames[-1][0]['scene'],'failure-last-observed')

    def test_retention_preserves_current_run_unknown_files_and_recent_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);now=time.time()
            old=root/'events-20200101.log';old.write_text('old')
            unknown=root/'notes.json';unknown.write_text('keep')
            recent=root/'events-20990101.log';recent.write_text('active')
            trace=root/'trace'/('a'*12);trace.mkdir(parents=True);image=trace/'frame.png';image.write_bytes(b'keep')
            for file in [old,unknown,image]:os.utime(file,(now-20*86400,now-20*86400))
            result=prune_logs(root,now=now,keep_trace='a'*12,max_bytes=0)
            self.assertEqual(result['deleted_files'],1);self.assertFalse(old.exists())
            self.assertTrue(all(file.exists() for file in [unknown,recent,image]))

    def test_doctor_collects_all_missing_prerequisites_without_network_or_model(self):
        with tempfile.TemporaryDirectory() as folder,patch('runtime_doctor.subprocess.run') as command:
            result=diagnose(folder)
            self.assertGreaterEqual(len(result['checks']),9);self.assertGreaterEqual(result['summary']['error'],3)
            self.assertEqual(result['paid_requests'],0);self.assertFalse(result['device_input']);command.assert_not_called()


class PermutationTests(unittest.TestCase):
    def cases(self):return load_cases(Path(__file__).parent/'fixtures/model-eval/cases.json')

    def test_answer_content_and_evidence_survive_all_rotations(self):
        cases=self.cases();rotated=rotate_options(cases)
        self.assertEqual(len(rotated),6)
        for source in cases:
            rows=[r for r in rotated if r['source_case_id']==source['id']]
            self.assertEqual(len(rows),len(source['options']))
            expected={source['options'][x] for x in source['expected']}
            for row in rows:
                self.assertEqual({row['options'][x] for x in row['expected']},expected)
                self.assertEqual(row['evidence'],source['evidence'])

    def test_relative_or_duplicate_option_content_is_not_rotated(self):
        for text in ['以上都对','A和B','选项C']:
            case=self.cases()[0];case['options']['A']=text
            with self.subTest(text=text),self.assertRaises(ValueError):rotate_options([case])
        case=self.cases()[0];case['options']['B']=case['options']['A']
        with self.assertRaises(ValueError):rotate_options([case])
