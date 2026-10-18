import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
"""Offline API contract and credential-handling checks; no paid requests."""
import sys
import json
import tempfile
from pathlib import Path
import task_support
from unittest.mock import patch
import httpx
import numpy as np
from fastapi.testclient import TestClient
import key_store
import utils
import webui

fixture = b'non-sensitive DPAPI test fixture'
encrypted = key_store._crypt(fixture, False)
assert fixture not in encrypted
assert key_store._crypt(encrypted, True) == fixture

calls = []
def respond(request):
    assert str(request.url) == 'https://api.deepseek.com/chat/completions'
    body = json.loads(request.content)
    assert body['model'] == 'deepseek-flash'
    assert body['thinking'] == {'type':'disabled'}
    assert 'enable_thinking' not in body
    assert isinstance(body['messages'][0]['content'], str)
    image_parts = [part for part in body['messages'][1]['content'] if part['type']=='image_url']
    assert image_parts[0]['image_url']['url'].startswith('data:image/png;base64,')
    assert body['response_format']=={'type':'json_object'}
    calls.append(body)
    prompt=body['messages'][0]['content']
    value={'choices':['B']} if '"choices"' in prompt else {'parts':['B']} if '"parts"' in prompt else {'answer':'B'}
    value['uncertain']=False
    return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]})

resolver = utils.AIResolver('offline-fixture-key', 'deepseek-flash')
resolver.session.close()
resolver.session = httpx.Client(transport=httpx.MockTransport(respond))
images = [np.zeros((80, 160, 3), dtype=np.uint8)]
assert resolver.resolve_choice(images) == ['B']
assert resolver.resolve_blank(images, True, 1) == 'B'
assert resolver.resolve_click_blank(images) == ['B']
assert len(calls) == 3
resolver.session.close()

class Worker:
    def __init__(self, *args, **kwargs):
        self.connected = True
        self.pause_flag = False
    def get_device(self): return []
    def connection_alive(self): return self.connected

with patch.object(webui, 'MaaWorker', Worker), patch.object(webui, 'load_key', return_value='private-test-value'), patch.object(webui, 'key_configured', return_value=False):
    with TestClient(webui.app) as client:
        response = client.get('/api/settings')
        assert response.status_code == 200
        assert response.json()['api_key'] == ''
        assert 'private-test-value' not in response.text
        assert client.get('/local/api-key').status_code == 200
        assert client.get('/api/status').json()['backend'] == 'deepseek-local'
        blocked = client.post('/api/start', json={'tasklist':['每日答题']}).json()
        assert blocked['status'] == 'failed'
        invalid = client.post('/api/local/provider', json={'api_key':{'secret':'private-test-value'}})
        assert invalid.status_code == 422
        assert 'private-test-value' not in invalid.text

from types import SimpleNamespace
from unittest.mock import Mock
from utils import MaaWorker

def fake_job(failed=False):
    job = Mock()
    job.wait.return_value = job
    job.get.return_value = SimpleNamespace(status=SimpleNamespace(failed=failed))
    return job

def guarded_worker(entry_failed=False):
    worker = MaaWorker.__new__(MaaWorker)
    worker.stop_flag = False
    worker.send_log = Mock()
    worker.prepare_device = Mock()
    worker._guard_verification = Mock()
    worker._content_page_ready = Mock(return_value=True)
    worker._app_in_foreground = Mock(return_value=True)
    worker._watch_video_segment = Mock(return_value=65)
    worker._recover_content_home = Mock()
    controller = Mock()
    controller.post_swipe.return_value = fake_job()
    controller.post_screencap.return_value = fake_job()
    worker.tasker = SimpleNamespace(controller=controller, post_task=Mock(return_value=fake_job(entry_failed)))
    worker.detect = Mock(return_value=([], []))
    return worker

with patch.object(MaaWorker, '_sleep'):
    worker = guarded_worker(entry_failed=True)
    try:
        worker.read_article()
        raise AssertionError('Wrong home page must stop the task')
    except RuntimeError:
        pass
    worker.detect.assert_not_called()
    worker.tasker.controller.post_swipe.assert_not_called()
    for method in ('read_article', 'watch_video'):
        worker = guarded_worker()
        # Fail the test explicitly if the original unbounded loop returns.
        worker.detect.side_effect = [([], [])] * 20 + [AssertionError('Unbounded content search')]
        try:
            getattr(worker, method)()
            raise AssertionError('Missing content must stop the task')
        except RuntimeError:
            pass
        assert worker.detect.call_count <= 20

    worker = guarded_worker(entry_failed=True)
    worker._reset_home = Mock(side_effect=RuntimeError('Home page not ready'))
    try:
        worker._enter_learning_score()
        raise AssertionError('Quiz navigation must confirm the home page')
    except RuntimeError:
        pass
    worker.tasker.controller.post_click.assert_not_called()

    worker = guarded_worker()
    worker.read_article = Mock(side_effect=RuntimeError('test navigation failure'))
    worker.read_learning_score = Mock(return_value={'选读文章':{'value':0,'maximum':12}})
    worker._reset_home = Mock()
    with tempfile.TemporaryDirectory() as directory, patch.object(task_support,'REPORT_PATH',Path(directory)/'run.json'), patch('utils.plyer.notification.notify'), patch('utils.traceback.print_exc'):
        worker.task(['选读文章'])
    assert worker.last_task_error == 'TaskIncomplete'
    assert worker.run_report.snapshot()['tasks']['选读文章']['state']=='failed'
    assert not any('积分已核实完成' in str(call) for call in worker.send_log.call_args_list)

print('PASS: DPAPI, image contracts, redaction, configuration guards, wrong-page stop, bounded search, quiz home guard, failure is not reported as success')
