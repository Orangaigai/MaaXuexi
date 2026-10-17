"""Local task control and DeepSeek configuration for the installed runtime."""
import asyncio
import json
import os
import threading
import traceback
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from queue import SimpleQueue
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, SecretStr, ConfigDict, field_validator, StrictBool

from key_store import key_configured, load_key, save_key
from provider_support import MODEL, invalidate_validation, provider_metadata, test_connection
from utils import MaaWorker
from task_support import VERSION, read_report, ordered_tasks, ALIASES
from comment_tasks import comment_policy

class ConfigModel(BaseModel):
    api_key: SecretStr = SecretStr('')
    model: str = MODEL

class DeviceModel(BaseModel):
    name: str
    adb_path: str
    address: str
    screencap_methods: int
    input_methods: int
    config: dict

class TaskModel(BaseModel):
    tasklist: list[Literal['登录', '选读文章', '视听学习', '本地频道', '订阅', '发表观点', '趣味答题', '每日答题']]
    fast_answer: bool = False
    debug: bool = False
    unattended: StrictBool = False
    model_config = ConfigDict(extra="forbid")

    @field_validator('tasklist')
    @classmethod
    def normalize_tasks(cls, tasks):
        return ordered_tasks(tasks)

class AppState:
    def __init__(self):
        self.message_conn = SimpleQueue()
        self.child_process = None
        self.worker = None
        self.history_message = deque(maxlen=600)
        self.message_sequence = 0
        self.lock = threading.RLock()
        self.current_tasks = []

app_state = AppState()

def task_running():
    return app_state.child_process is not None and app_state.child_process.is_alive()

def ensure_worker():
    with app_state.lock:
        if app_state.worker is None:
            app_state.worker = MaaWorker(app_state.message_conn, api_key=load_key(), model=MODEL)
        return app_state.worker

def configure_key(key):
    with app_state.lock:
        if task_running():
            raise HTTPException(409, '请先中止任务，再配置密钥。')
        if key:
            try:
                save_key(key)
            except ValueError:
                raise HTTPException(400, '密钥不能为空或含有空格。')
            invalidate_validation()
        worker = ensure_worker()
        worker.api_key = load_key()
        old_session = worker.ai_resolver.session
        worker.update_ai_models(model=MODEL)
        old_session.close()

async def collect_messages():
    while True:
        for _ in range(500):
            if app_state.message_conn.empty():
                break
            message = app_state.message_conn.get_nowait()
            app_state.history_message.append((app_state.message_sequence, message))
            app_state.message_sequence += 1
        await asyncio.sleep(0.05)

@asynccontextmanager
async def lifespan(app):
    collector = asyncio.create_task(collect_messages())
    await asyncio.sleep(0.5)
    yield
    if app_state.worker:
        app_state.worker.stop_flag = True
        app_state.worker.pause_flag = False
    collector.cancel()
    try:
        await collector
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)
app.mount('/assets', StaticFiles(directory='page/assets'))

@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Do not echo submitted API keys in validation responses.
    details = [{k:v for k,v in item.items() if k in ('loc','msg','type')} for item in error.errors()]
    return JSONResponse(status_code=422, content={'detail':details})

@app.get('/')
def homepage():
    html = Path('page/index.html').read_text('utf-8')
    html = html.replace('</body>', '<script defer src="/assets/local-provider.js"></script></body>')
    html = html.replace('/assets/local-provider.js', '/assets/local-provider.js?v=' + VERSION)
    return HTMLResponse(html, headers={'Cache-Control':'no-store'})

@app.get('/local/api-key')
def api_key_page():
    return HTMLResponse(Path('backend/api-key.html').read_text('utf-8'), headers={'Cache-Control':'no-store'})

@app.get('/api/settings')
def get_settings():
    ensure_worker()
    return dict(api_key='', model=MODEL, provider='deepseek', api_key_configured=key_configured())

@app.post('/api/settings')
def post_settings(config: ConfigModel):
    configure_key(config.api_key.get_secret_value())
    return dict(status='success')

@app.get('/api/local/provider')
def get_provider():
    return provider_metadata()

@app.get('/api/local/task-plan')
def get_task_plan():
    path = Path('config/local-task-plan.json')
    default = [name for name in ALIASES if name != '发表观点' or comment_policy()['enabled']]
    try:
        plan = TaskModel(**json.loads(path.read_text('utf-8-sig')))
    except (OSError,ValueError,TypeError):
        plan = TaskModel(tasklist=default)
    return dict(**plan.model_dump(), available=list(ALIASES),
                experimental=[], experimental_live_verified=False)

@app.get('/api/local/comment-policy')
def get_comment_policy():
    return comment_policy()

@app.post('/api/local/task-plan')
def save_task_plan(plan: TaskModel):
    with app_state.lock:
        if task_running():
            raise HTTPException(409,'请等当前任务结束后再保存定时清单。')
        if not plan.tasklist:
            raise HTTPException(400,'至少选择一项任务。')
        if '发表观点' in plan.tasklist and not comment_policy()['enabled']:
            raise HTTPException(409,'请先确认按正文生成的规则和每天发表范围，再加入定时清单。')
        value = dict(tasklist=list(dict.fromkeys(plan.tasklist)),fast_answer=False,debug=False)
        path = Path('config/local-task-plan.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8-sig')
        temporary.replace(path)
        return dict(status='success',**value)

@app.post('/api/local/provider')
def post_provider(config: ConfigModel):
    key = config.api_key.get_secret_value().strip()
    if not key:
        raise HTTPException(400, '请输入 DeepSeek 官网 API Key。')
    configure_key(key)
    return dict(status='success', message='已在本机加密保存。', **provider_metadata())

@app.post('/api/local/provider/test')
def validate_provider():
    if task_running():
        raise HTTPException(409, '请先中止任务，再测试接口。')
    return test_connection()

@app.get('/api/get_device')
def get_devices():
    return dict(devices=ensure_worker().get_device())

@app.post('/api/connect_device')
def connect_device(device: DeviceModel):
    with app_state.lock:
        if task_running():
            raise HTTPException(409, '请先中止当前任务。')
        return dict(status='success' if ensure_worker().connect_device(device) else 'failed')

def execute_tasks(tasks, daily_acceptance=False, verification_first=False):
    try:
        worker = app_state.worker
        worker.last_task_error = None
        if daily_acceptance:
            worker.task(tasks.tasklist, tasks.fast_answer, tasks.debug, unattended=False, daily_acceptance=True)
        elif verification_first:
            worker.task(tasks.tasklist, tasks.fast_answer, tasks.debug, unattended=tasks.unattended,verification_first=True)
        else:
            worker.task(tasks.tasklist, tasks.fast_answer, tasks.debug, unattended=tasks.unattended)
    except Exception as error:
        app_state.worker.last_task_error = type(error).__name__
        app_state.worker.send_log('任务准备失败，请检查模拟器和登录状态')
        traceback.print_exc()
    finally:
        with app_state.lock:
            app_state.child_process = None
            app_state.current_tasks = []

@app.post('/api/start')
def start(tasks: TaskModel):
    return start_selected(tasks)

@app.post('/api/local/verify-daily-flow')
def verify_daily_flow(tasks: TaskModel):
    # Explicit one-round manual acceptance; never saved to the daily task plan.
    if tasks.tasklist != ['每日答题'] or tasks.unattended:
        raise HTTPException(422, '单组验收仅支持手动运行每日答题')
    return start_selected(TaskModel(tasklist=['每日答题']), daily_acceptance=True)

@app.post('/api/local/verify-full-flow')
def verify_full_flow(tasks: TaskModel):
    if set(tasks.tasklist)!=set(ALIASES):
        raise HTTPException(422, '完整流程验收需要保留全部八项任务')
    return start_selected(tasks,verification_first=True)

def start_selected(tasks: TaskModel, daily_acceptance=False, verification_first=False):
    with app_state.lock:
        worker = ensure_worker()
        if task_running():
            return dict(status='failed', message='任务已开始')
        if not tasks.tasklist:
            return dict(status='failed', message='请先选择任务')
        if '发表观点' in tasks.tasklist and not comment_policy()['enabled']:
            return dict(status='failed', message='发表观点尚待确认按正文生成的规则和每日使用范围，未启动发表。')
        if any(name in tasks.tasklist for name in ('每日答题','趣味答题','发表观点')) and not key_configured():
            return dict(status='failed', message='答题或观点草稿任务需要先配置 DeepSeek 密钥。')
        tasks.tasklist = list(dict.fromkeys(tasks.tasklist))
        worker.stop_flag = False
        worker.pause_flag = False
        worker.last_task_error = None
        app_state.current_tasks = tasks.tasklist
        args = (tasks, True) if daily_acceptance else (tasks,False,True) if verification_first else (tasks,)
        app_state.child_process = threading.Thread(target=execute_tasks, args=args, daemon=True)
        app_state.child_process.start()
        return dict(status='success')

@app.post('/api/stop')
def stop():
    with app_state.lock:
        if not task_running():
            return dict(status='failed', message='任务未开始')
        app_state.worker.request_stop()
        return dict(status='success')

@app.post('/api/continue')
def resume():
    with app_state.lock:
        if not task_running() or not app_state.worker.pause_flag:
            return dict(status='failed', message='当前没有等待继续的任务。')
        app_state.worker.pause_flag = False
        return dict(status='success')

@app.get('/api/status')
def status():
    report = getattr(app_state.worker, 'run_report', None)
    return dict(status='running', pid=os.getpid(), backend='deepseek-local',
                local_version=VERSION, run_summary=report.snapshot() if report else read_report(),
                task_running=task_running(), connected=bool(app_state.worker and app_state.worker.connection_alive()),
                current_tasks=app_state.current_tasks,
                last_error=getattr(app_state.worker, 'last_task_error', None),
                paused=bool(app_state.worker and app_state.worker.pause_flag),
                provider='deepseek', model=MODEL, api_key_configured=key_configured())

@app.websocket('/api/ws')
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    cursor = -1
    try:
        while True:
            for sequence, message in list(app_state.history_message):
                if sequence > cursor:
                    await websocket.send_text(message)
                    cursor = sequence
            await asyncio.sleep(0.1)
    except (WebSocketDisconnect, RuntimeError):
        return

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8000)
